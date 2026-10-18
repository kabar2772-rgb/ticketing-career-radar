#!/usr/bin/env python3
"""Ticketing Career Radar — public Greenhouse, Lever and Ashby boards."""
import csv
import datetime as dt
import hashlib
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'sources.json').read_text(encoding='utf-8'))
HEADERS = {'User-Agent': 'TicketingCareerRadar/0.4 (personal career research)'}
TIMEOUT = 20


def clean(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', str(value or '')))).strip()


def fetch(url):
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()



class UEFAJobsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href', '')
            self.href = href if 'jobylon.com' in href else None
            self.parts = []

    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href is not None:
            label = clean(' '.join(self.parts))
            if label:
                self.links.append((self.href, label))
            self.href = None
            self.parts = []



def collect_public_jobs(source):
    """Read publicly visible job cards and JobPosting structured data; no login or bypass."""
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin, urlparse
    url = source['slug']
    response = requests.get(url, headers={**HEADERS, 'User-Agent': 'Mozilla/5.0 (compatible; CareerRadar/1.0)'}, timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    origin = urlparse(response.url).netloc.lower()
    seen = set()
    def emit(title, link, location='', employer=''):
        link = urljoin(response.url, link or '')
        parsed = urlparse(link)
        title = clean(title)
        location = clean(location)
        if parsed.scheme not in ('http', 'https') or parsed.netloc.lower() != origin:
            return None
        if not title or len(title) > 180 or len(title) < 6 or not location or link in seen:
            return None
        if re.search(r'^(search|view all|apply now|read more|jobs|careers|sign in|register)', title, re.I):
            return None
        seen.add(link)
        return dict(title=title, url=link, location=location, description=title + ' ' + clean(employer), published='')

    for script in soup.find_all('script', type='application/ld+json'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        stack = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            stack.extend(v for k, v in node.items() if k in ('@graph', 'itemListElement'))
            if node.get('@type') not in ('JobPosting', ['JobPosting']):
                continue
            places = node.get('jobLocation') or []
            if isinstance(places, dict):
                places = [places]
            locations = []
            for place in places:
                address = (place.get('address') or {}) if isinstance(place, dict) else {}
                if isinstance(address, dict):
                    locations.append(', '.join(str(address.get(k) or '') for k in ('addressLocality', 'addressRegion', 'addressCountry')).strip(', '))
            location = '; '.join(p for p in locations if p)
            if not location and node.get('jobLocationType') == 'TELECOMMUTE':
                location = 'Remote'
            employer = node.get('hiringOrganization') or {}
            result = emit(node.get('title'), node.get('url'), location, employer.get('name', '') if isinstance(employer, dict) else '')
            if result:
                yield result

    # GSJ's new platform renders the first page of job cards server-side.
    # Do not infer locations from unrelated surrounding cards.
    if source['company'] == 'Global Sports Jobs':
        for a in soup.select('a[href]'):
            href = a.get('href', '')
            if not re.search(r'/jobs/[0-9a-f]{8}-[0-9a-f-]{27,}', href, re.I):
                continue
            card = a.find_parent(['article', 'li'])
            if card is None:
                card = a.parent
            if card is None:
                continue
            title = a.get_text(' ', strip=True)
            if len(title) < 6 or title.lower() in ('view job', 'view details', 'apply', 'save job'):
                heading = card.find(['h2', 'h3', 'h4'])
                title = heading.get_text(' ', strip=True) if heading else ''
            text = card.get_text(' ', strip=True)
            country = re.search(r'\b(United Kingdom|Switzerland|Germany|France|Spain|Italy|Netherlands|Belgium|Portugal|Austria|Ireland|Poland|Sweden|Denmark|Norway|Finland|UAE|United Arab Emirates|Qatar|Saudi Arabia|Brazil|United States|Canada|Australia|Japan|Singapore|India)\b', text, re.I)
            if not country:
                continue
            result = emit(title, href, country.group(0))
            if result:
                yield result

def collect(source):
    adapter, slug = source['adapter'], source['slug']
    if adapter == 'external':
        yield from collect_public_jobs(source)
    elif adapter == 'uefa':
        response = requests.get('https://www.uefa.com/about/jobs/', headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        parser = UEFAJobsParser()
        parser.feed(response.text)
        if not parser.links:
            raise ValueError('UEFA jobs links not found in official page')
        seen = set()
        for url, label in parser.links:
            if url in seen:
                continue
            seen.add(url)
            title = re.split(r'\s+(?:Division:|Application deadline:)', label, maxsplit=1)[0].strip()
            if title:
                yield dict(title=title, url=url, location='Nyon, Switzerland', description=label, published='')

    elif adapter == 'teamtailor':
        response = requests.get('https://' + slug + '/jobs', headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        class JobLinks(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links = []
                self.href = None
                self.words = []
            def handle_starttag(self, tag, attrs):
                if tag == 'a':
                    href = dict(attrs).get('href', '')
                    if re.search(r'/jobs/\\d+[-/]', href):
                        self.href = href
                        self.words = []
            def handle_data(self, data):
                if self.href is not None:
                    self.words.append(data)
            def handle_endtag(self, tag):
                if tag == 'a' and self.href:
                    title = clean(' '.join(self.words))
                    if title:
                        self.links.append((self.href, title))
                    self.href = None
        parser = JobLinks()
        parser.feed(response.text)
        if not parser.links:
            raise ValueError('No Teamtailor job links on public page')
        seen = set()
        for href, title in parser.links:
            url = requests.compat.urljoin(response.url, href)
            if url in seen:
                continue
            seen.add(url)
            yield dict(title=title, url=url, location=source.get('location', ''),
                       description=title + ' ' + source.get('category', ''), published='')
    elif adapter == 'greenhouse':
        data = fetch(f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true')['jobs']
        for item in data:
            yield dict(title=item.get('title', ''), url=item.get('absolute_url', ''),
                       location=(item.get('location') or {}).get('name', ''),
                       description=clean(item.get('content', '')), published=item.get('updated_at', ''))
    elif adapter == 'lever':
        for item in fetch(f'https://api.lever.co/v0/postings/{slug}?mode=json'):
            categories = item.get('categories') or {}
            yield dict(title=item.get('text', ''), url=item.get('hostedUrl', ''),
                       location=categories.get('location', ''),
                       description=clean(item.get('descriptionPlain') or item.get('description', '')),
                       published='')
    elif adapter == 'ashby':
        for item in fetch(f'https://api.ashbyhq.com/posting-api/job-board/{slug}')['jobs']:
            yield dict(title=item.get('title', ''), url=item.get('jobUrl', ''),
                       location=item.get('location', ''),
                       description=clean(item.get('descriptionPlain') or item.get('descriptionHtml', '')),
                       published=item.get('publishedAt', ''))
    else:
        raise ValueError(f'Unknown adapter: {adapter}')


TICKETING = re.compile(r'\b(ticketing|tickets?|box office|access control|admissions|ticket sales|ticket operations|ticket distribution|ticketing systems?)\b', re.I)
OPERATIONS = re.compile(r'\b(venue|stadium|arena|event|events|tournament|matchday|match day|fan experience|guest experience|spectator services|visitor services|hospitality|operations|operational readiness)\b', re.I)
LEADERSHIP = re.compile(r'\b(head|director|manager|management|lead|senior|chief|vp|vice president|general manager|operations manager|supervisor)\b', re.I)
JUNIOR = re.compile(r'\b(intern|internship|assistant|junior|trainee|graduate|entry.level|coordinator|associate|representative|agent|cashier)\b', re.I)
EXCLUDE = re.compile(r'\b(software engineer|software developer|developer|devops|cybersecurity|it support|help desk|technical support|airline|airfare|flight reservations?|travel agent|air ticketing|jira|servicenow)\b', re.I)
SPORT = re.compile(r'\b(sport|sports|football|soccer|fifa|uefa|formula\s*1|f1|motogp|racing|motorsport|olympic|stadium|arena|tournament|grand prix|cricket|tennis|basketball|rugby|club|league|matchday)\b', re.I)
CULTURE = re.compile(r'\b(museum|museums|theatre|theater|gallery|galleries|exhibition|theme park|amusement park|entertainment|concert|performing arts|attraction|cultural|heritage|visitor attraction)\b', re.I)
GCC = re.compile(r'\b(uae|united arab emirates|dubai|abu dhabi|sharjah|ajman|ras al khaimah|fujairah|qatar|doha|lusail|saudi arabia|saudi|ksa|riyadh|jeddah|jidda|dammam|khobar|alula|al ula|neom|tabuk|makkah|mecca|medina|madinah)\b', re.I)
NO_SPONSOR = re.compile(r'(?:does not|do not|will not|cannot|can not)\s+(?:offer|provide)\s+(?:work\s+)?(?:visa\s+)?sponsorship|(?:no|not|without)\s+(?:visa\s+)?sponsorship|(?:must|should)\s+(?:already\s+)?(?:have|hold|possess)\s+(?:a\s+)?(?:valid\s+)?(?:right|authorization|permission)\s+to\s+work|not\s+(?:able|willing)\s+to\s+sponsor', re.I)
YES_SPONSOR = re.compile(r'visa sponsorship (?:is )?(?:available|provided|offered)|(?:provide|offer)\s+(?:visa\s+)?sponsorship|work permit (?:support|assistance|provided)|relocation and visa (?:support|assistance)', re.I)



US_LOCATION = re.compile(
    r'\\b(?:USA|U\\.?S\\.?A\\.?|United States(?: of America)?|'
    r'New York|Los Angeles|San Francisco|San Diego|San Jose|Chicago|Boston|'
    r'Washington(?:,? D\\.?C\\.?)?|Seattle|Miami|Orlando|Tampa|Atlanta|'
    r'Dallas|Houston|Austin|Denver|Phoenix|Philadelphia|Detroit|'
    r'Las Vegas|Nashville|Charlotte|Minneapolis|Baltimore|Indianapolis|'
    r'California|Texas|Florida|Colorado|New Jersey|Pennsylvania|'
    r'Massachusetts|Illinois|Ohio|Virginia|Maryland|Arizona|Nevada|'
    r'Tennessee|North Carolina|Georgia|Connecticut|Oregon|Michigan|'
    r'Wisconsin|Minnesota|Missouri|Utah|Kentucky|Louisiana)\\b',
    re.I
)
US_STATE_CODE = re.compile(
    r'(?:,\\s*|\\b)(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)(?=\\b|$)'
)
MMA_EXCEPTION = re.compile(r'\\b(?:UFC|Ultimate Fighting Championship|Bellator(?: MMA)?)\\b', re.I)

def excluded_us_job(job, source):
    location = job.get('location') or ''
    if not (US_LOCATION.search(location) or US_STATE_CODE.search(location)):
        return False
    # Exception is for actual UFC/Bellator jobs, not any posting that mentions MMA.
    identity = ' '.join((source.get('company', ''), job.get('title', '')))
    return not MMA_EXCEPTION.search(identity)


def classify(job, source):
    title = job['title'] or ''
    description = job['description'] or ''
    location = job['location'] or ''
    combined = title + ' ' + description
    if excluded_us_job(job, source):
        return None
    if EXCLUDE.search(title) or (JUNIOR.search(title) and not (source['company'] == 'UEFA' and TICKETING.search(title))):
        return None
    ticketing = bool(TICKETING.search(title))
    operations = bool(OPERATIONS.search(title))
    leadership = bool(LEADERSHIP.search(title))
    if not (ticketing or (operations and leadership) or (source.get('category') == 'sport' and leadership and re.search(r'\b(partnership|commercial|accreditation|overlay|ceremonies|guest services|premium experience)\b', title, re.I))):
        return None
    if not leadership and not ticketing:
        return None

    # Optional source.category: sport, culture, or mixed (default).
    category = source.get('category', 'mixed').lower()
    sport = bool(SPORT.search(combined[:2500]))
    culture = bool(CULTURE.search(combined[:2500]))
    if category == 'venue':
        if not GCC.search(location):
            return None
    elif category == 'culture' or (category == 'mixed' and culture and not sport):
        if not GCC.search(location):
            return None
        category = 'culture'
    elif category == 'sport' or sport:
        category = 'sport'
    else:
        # Unclassified employers: allow ticketing leadership globally;
        # broader operations only when explicitly sports-related.
        if not ticketing and category != 'venue':
            return None

    if NO_SPONSOR.search(description):
        visa = 'Work authorization required / no sponsorship stated'
    elif YES_SPONSOR.search(description):
        visa = 'Sponsorship mentioned — verify eligibility'
    else:
        visa = 'Not specified — verify'
    score = (76 if ticketing else 48) + (16 if leadership else 0)
    if category == 'sport':
        score += 5
    if source['company'] == 'UEFA':
        score += 15
    if re.search(r'\b(USA|United States|Canada|California|Denver|New York|Los Angeles|Colorado|Washington|Texas|Florida|Chicago|Boston)\b', location, re.I):
        score -= 30
    if re.search(r'\b(Switzerland|Europe|UK|United Kingdom|Germany|France|Spain|Italy|Netherlands|Portugal|Belgium|Austria|UAE|Qatar|Saudi Arabia|Dubai|Doha|Riyadh)\b', location, re.I):
        score += 12
    if visa.startswith('Work authorization'):
        score -= 25
    return visa, max(0, min(100, score))


def main():
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    output = ROOT / 'data'
    output.mkdir(exist_ok=True)
    old_file = output / 'jobs.json'
    try:
        old = json.loads(old_file.read_text(encoding='utf-8')).get('jobs', []) if old_file.exists() else []
    except (ValueError, OSError):
        old = []
    indexed = {j['id']: j for j in old if j.get('id')}
    stats = []
    for source in CONFIG.get('sources', []):
        try:
            rows = list(collect(source))
            relevant = 0
            current_ids = set()
            for raw in rows:
                if not raw.get('url') or not raw.get('title'):
                    continue
                ident = hashlib.sha256(raw['url'].split('?')[0].encode()).hexdigest()[:20]
                current_ids.add(ident)
                decision = classify(raw, source)
                if decision is None:
                    continue
                visa, score = decision
                previous = indexed.get(ident, {})
                indexed[ident] = {
                    'id': ident, 'company': source['company'],
                    'title': raw['title'], 'url': raw['url'],
                    'location': raw.get('location', ''),
                    'description': clean(raw.get('description', ''))[:8000],
                    'published': raw.get('published', ''),
                    'visa': visa, 'score': score,
                    'salary': 'Not disclosed / not parsed',
                    'first_seen': previous.get('first_seen', now),
                    'last_seen': now, 'status': 'active',
                    'source': source['adapter']
                }
                relevant += 1
            for ident, job in indexed.items():
                if (job.get('company') == source['company']
                        and job.get('source') == source['adapter']
                        and ident not in current_ids):
                    job['status'] = 'not found in latest source'
            stats.append({'source': source['company'], 'state': 'ok',
                          'retrieved': len(rows), 'relevant': relevant})
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as exc:
            stats.append({'source': source['company'], 'state': 'error',
                          'detail': str(exc)[:200]})
    indexed = {ident: job for ident, job in indexed.items()
               if not excluded_us_job(job, {'company': job.get('company', '')})}
    jobs = sorted(indexed.values(),
                  key=lambda j: (j.get('status') != 'active', -j.get('score', 0), j.get('title', '')))
    (output / 'jobs.json').write_text(
        json.dumps({'updated': now, 'sources': stats, 'jobs': jobs},
                   ensure_ascii=False, indent=2), encoding='utf-8')
    columns = ['company', 'title', 'location', 'score', 'visa', 'salary',
               'status', 'first_seen', 'last_seen', 'url']
    with (output / 'jobs.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({key: job.get(key, '') for key in columns} for job in jobs)
    print(json.dumps({'updated': now, 'sources': stats, 'total': len(jobs)},
                     ensure_ascii=False))
    return 0 if any(item['state'] == 'ok' for item in stats) else 2


if __name__ == '__main__':
    sys.exit(main())
