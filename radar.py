#!/usr/bin/env python3
"""Ticketing Career Radar — public Greenhouse, Lever and Ashby boards."""
import csv
import datetime as dt
import hashlib
import html
import json
import re
import sys
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


def collect(source):
    adapter, slug = source['adapter'], source['slug']
    if adapter == 'greenhouse':
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
NO_SPONSOR = re.compile(r'(?:does not|do not|will not|cannot|can not)\\s+(?:offer|provide)\\s+(?:work\\s+)?(?:visa\\s+)?sponsorship|(?:no|not|without)\s+(?:visa\s+)?sponsorship|(?:must|should)\s+(?:already\s+)?(?:have|hold|possess)\s+(?:a\s+)?(?:valid\s+)?(?:right|authorization|permission)\s+to\s+work|not\s+(?:able|willing)\s+to\s+sponsor', re.I)
YES_SPONSOR = re.compile(r'visa sponsorship (?:is )?(?:available|provided|offered)|(?:provide|offer)\s+(?:visa\s+)?sponsorship|work permit (?:support|assistance|provided)|relocation and visa (?:support|assistance)', re.I)


def classify(job, source):
    title = job['title'] or ''
    description = job['description'] or ''
    location = job['location'] or ''
    combined = title + ' ' + description
    if EXCLUDE.search(title) or JUNIOR.search(title):
        return None
    ticketing = bool(TICKETING.search(title))
    operations = bool(OPERATIONS.search(title))
    leadership = bool(LEADERSHIP.search(title))
    if not (ticketing or (operations and leadership)):
        return None
    if not leadership and not ticketing:
        return None

    # Optional source.category: sport, culture, or mixed (default).
    category = source.get('category', 'mixed').lower()
    sport = bool(SPORT.search(combined[:2500]))
    culture = bool(CULTURE.search(combined[:2500]))
    if category == 'culture' or (category == 'mixed' and culture and not sport):
        if not GCC.search(location):
            return None
        category = 'culture'
    elif category == 'sport' or sport:
        category = 'sport'
    else:
        # Unclassified employers: allow ticketing leadership globally;
        # broader operations only when explicitly sports-related.
        if not ticketing:
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
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            stats.append({'source': source['company'], 'state': 'error',
                          'detail': str(exc)[:200]})
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
