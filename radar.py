#!/usr/bin/env python3
"""Ticketing Career Radar: public ATS ingestion; no authentication or bypass."""
import csv, datetime as dt, hashlib, html, json, os, re, sys
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parent
CONFIG=json.loads((ROOT/'sources.json').read_text())
HEADERS={'User-Agent':'TicketingCareerRadar/0.3 (+personal career research)'}
TIMEOUT=18

def fetch(url):
    r=requests.get(url,headers=HEADERS,timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def clean(value):
    return re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]*>',' ',value or ''))).strip()

def collect(src):
    kind,slug=src['adapter'],src['slug']
    if kind=='greenhouse':
        data=fetch(f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true')['jobs']
        for j in data:
            yield dict(title=j.get('title',''),url=j.get('absolute_url',''),location=(j.get('location') or {}).get('name',''),description=clean(j.get('content','')),published=j.get('updated_at',''))
    elif kind=='lever':
        for j in fetch(f'https://api.lever.co/v0/postings/{slug}?mode=json'):
            yield dict(title=j.get('text',''),url=j.get('hostedUrl',''),location=(j.get('categories') or {}).get('location',''),description=clean(j.get('descriptionPlain') or j.get('description','')),published='')
    elif kind=='ashby':
        for j in fetch(f'https://api.ashbyhq.com/posting-api/job-board/{slug}')['jobs']:
            yield dict(title=j.get('title',''),url=j.get('jobUrl',''),location=j.get('location',''),description=clean(j.get('descriptionPlain') or j.get('descriptionHtml','')),published=j.get('publishedAt',''))
    else: raise ValueError('Unknown adapter: '+kind)

TICKET=re.compile(r'\b(ticketing|ticket sales|box office|access control|ticket operations|ticket distribution|admissions)\b',re.I)
LEAD=re.compile(r'\b(head|director|manager|lead|senior|chief|venue ticketing)\b',re.I)
JUNIOR=re.compile(r'\b(intern|assistant|junior|trainee)\b',re.I)
OTHER=re.compile(r'\b(venue operations|event operations|tournament operations|hospitality operations)\b',re.I)
NO_SPONSOR=re.compile(r'(?:no|not|without)\s+(?:visa\s+)?sponsorship|(?:must|should)\s+(?:already\s+)?(?:have|hold|possess)\s+(?:a\s+)?(?:valid\s+)?(?:right|authorization|permission)\s+to\s+work|only\s+(?:local|domestic)\s+(?:candidates|applicants)|not\s+(?:able|willing)\s+to\s+sponsor',re.I)
YES_SPONSOR=re.compile(r'visa sponsorship (?:is )?(?:available|provided|offered)|(?:provide|offer)\s+(?:visa\s+)?sponsorship|work permit (?:support|assistance|provided)|relocation and visa (?:support|assistance)',re.I)

def assess(j):
    title=j['title']; desc=j['description']; combined=title+' '+desc
    if NO_SPONSOR.search(combined): visa='Work authorization required / no sponsorship stated'
    elif YES_SPONSOR.search(combined): visa='Sponsorship mentioned — verify eligibility'
    else: visa='Not specified — verify'
    match=TICKET.search(title)
    if match: score=72+(17 if LEAD.search(title) else 0)-(22 if JUNIOR.search(title) else 0)
    elif OTHER.search(title): score=45+(10 if LEAD.search(title) else 0)
    else: score=20
    if visa.startswith('Work authorization'): score-=20
    return visa,max(0,min(100,score))

def main():
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    oldpath=ROOT/'data/jobs.json'
    old=json.loads(oldpath.read_text())['jobs'] if oldpath.exists() else []
    indexed={j['id']:j for j in old}
    stats=[]
    for src in CONFIG['sources']:
        try:
            rows=list(collect(src)); accepted=0
            for raw in rows:
                if not raw['url'] or not raw['title']:continue
                if not (TICKET.search(raw['title']) or OTHER.search(raw['title'])):continue
                ident=hashlib.sha256(raw['url'].split('?')[0].encode()).hexdigest()[:20]
                visa,score=assess(raw)
                previous=indexed.get(ident,{})
                indexed[ident]=dict(id=ident,company=src['company'],title=raw['title'],url=raw['url'],location=raw['location'],description=raw['description'][:8000],published=raw['published'],visa=visa,score=score,salary='Not disclosed / not parsed',first_seen=previous.get('first_seen',now),last_seen=now,status='active',source=src['adapter'])
                accepted+=1
            # Mark disappeared jobs only if this source was successfully retrieved.
            current_ids={hashlib.sha256(r['url'].split('?')[0].encode()).hexdigest()[:20] for r in rows if r.get('url')}
            for ident,job in indexed.items():
                if job['company']==src['company'] and job['source']==src['adapter'] and ident not in current_ids:job['status']='not found in latest source'
            stats.append(dict(source=src['company'],state='ok',retrieved=len(rows),relevant=accepted))
        except (requests.RequestException,ValueError,KeyError,TypeError) as e:
            stats.append(dict(source=src['company'],state='error',detail=str(e)[:180]))
    jobs=sorted(indexed.values(),key=lambda j:(j['status']!='active',-j['score'],j['title']))
    (ROOT/'data').mkdir(exist_ok=True)
    (ROOT/'data/jobs.json').write_text(json.dumps(dict(updated=now,sources=stats,jobs=jobs),ensure_ascii=False,indent=2))
    cols=['company','title','location','score','visa','salary','status','first_seen','last_seen','url']
    with (ROOT/'data/jobs.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows({k:j.get(k,'') for k in cols} for j in jobs)
    print(json.dumps(dict(updated=now,sources=stats,total=len(jobs)),ensure_ascii=False))
    return 0 if any(s['state']=='ok' for s in stats) else 2
if __name__=='__main__':sys.exit(main())
