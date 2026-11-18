#!/usr/bin/env python3
"""Unified bounded research workbench. Standard library, Python 3.10+."""
import argparse
import datetime as dt
import difflib
import hashlib
import importlib.util
import json
import pathlib
import re
import sys
import tempfile
import urllib.parse

ROOT=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('research_web', ROOT/'tools-web.py')
web=importlib.util.module_from_spec(spec);spec.loader.exec_module(web)
CATALOG=json.loads((ROOT/'asset-capabilities.json').read_text(encoding='utf-8'))
TODAY=lambda:dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
STOP={'the','and','for','with','that','from','this','what','when','where','which','into','about','does','are','was','were','has','have','how','why','its','but','not','you','your','their','than'}

def tokens(s):return set(re.findall(r'[a-z0-9]{3,}',s.lower()))-STOP

def error(code,msg):return {'ok':False,'error':{'code':code,'message':str(msg)}}

def load_config(path):
    p=pathlib.Path(path).resolve()
    if not p.is_file():raise ValueError('Input configuration not found: '+str(path))
    c=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(c,dict):raise ValueError('Configuration must be a JSON object')
    if not isinstance(c.get('question'),str) or not c['question'].strip() or len(c['question'])>2000:raise ValueError('question must be 1-2000 characters')
    if not isinstance(c.get('sources',[]),list) or len(c.get('sources',[]))>100:raise ValueError('sources must be a list of at most 100')
    if not isinstance(c.get('claims',[]),list) or len(c.get('claims',[]))>100 or any(not isinstance(x,str) or not x.strip() for x in c.get('claims',[])):raise ValueError('claims must be a list of nonempty strings (max 100)')
    for k in ('regions','languages','source_classes','must_cover','exclude_terms','synonyms'):
        if not isinstance(c.get(k,[]),list) or any(not isinstance(x,str) or not x.strip() for x in c.get(k,[])):raise ValueError(k+' must be a list of nonempty strings')
    for x in c.get('sources',[]):
        if not isinstance(x,dict) or (('path' in x)==('url' in x)):raise ValueError('Each source needs exactly one of path or url')
        if 'path' in x:
            if not isinstance(x['path'],str) or not x['path']:raise ValueError('Invalid source path')
            path2=(p.parent/x['path']).resolve()
            if not path2.is_file() or path2.stat().st_size>2000000:raise ValueError('Missing or oversized source file: '+x['path'])
            if path2.suffix.lower() not in ('.txt','.md','.html','.htm','.json','.csv','.xml','.rss','.atom'):raise ValueError('Unsupported source extension: '+x['path'])
        else:
            if not isinstance(x['url'],str):raise ValueError('Invalid URL')
            parsed=urllib.parse.urlsplit(x['url'])
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('Only public HTTPS URLs without credentials are permitted')
        if x.get('type','secondary') not in ('primary','official','academic','secondary','community','other'):raise ValueError('Invalid source type')
    for k in ('max_pages','max_bytes','timeout'):
        v=c.get(k,{'max_pages':12,'max_bytes':1000000,'timeout':10}[k])
        bounds={'max_pages':(1,50),'max_bytes':(1024,2000000),'timeout':(1,30)}[k]
        if type(v)!=int or not bounds[0]<=v<=bounds[1]:raise ValueError(f'{k} must be integer within {bounds}')
    c['_base']=str(p.parent)
    return c

def match_catalog(args):
    items=CATALOG
    if args.category:
        items=[x for x in items if args.category.casefold() in x['category'].casefold() or args.category==x['id'].split('.')[0]]
        if not items:raise ValueError('Unknown category: '+args.category)
    if args.skill:
        items=[x for x in items if args.skill.casefold() in (x['name'].casefold(),x['id'].casefold())]
        if not items:raise ValueError('Unknown skill: '+args.skill)
    return items

def plan(c,items):
    q=c['question'].strip(); words=sorted(tokens(q),key=lambda s:(-len(s),s))[:8]
    queries=[q, ' '.join(words)+' official documentation', ' '.join(words)+' study data', ' '.join(words)+' critique limitations']
    for synonym in c.get('synonyms',[])[:12]:queries.append(q+' '+synonym)
    if c.get('exclude_terms'):queries=[x+' '+ ' '.join('-'+t.replace(' ','-') for t in c['exclude_terms'][:8]) for x in queries]
    for region in c.get('regions',[])[:4]:queries.append(q+' '+region)
    for lang in c.get('languages',[])[:4]:queries.append(q+' '+lang)
    return {'question':q,'generated_at':TODAY(),'queries':list(dict.fromkeys(queries)),'scope':{'regions':c.get('regions',[]),'languages':c.get('languages',[]),'source_classes':c.get('source_classes',[]),'freshness_after':c.get('freshness_after'),'must_cover':c.get('must_cover',[])},'stop_conditions':{'page_limit':c.get('max_pages',12),'evidence_saturation':'review when new documents add no new matched claims or facets','unresolved_gaps':'report, never silently assert completion'},'catalog_selection':[{'id':x['id'],'name':x['name'],'category':x['category']} for x in items]}

def acquire(c,network=False):
    docs=[];issues=[];seen=set();max_pages=c.get('max_pages',12)
    for source in c.get('sources',[])[:max_pages]:
        ident=source.get('url') or source.get('path')
        if ident in seen:continue
        seen.add(ident)
        try:
            if 'path' in source:
                p=(pathlib.Path(c['_base'])/source['path']).resolve();data=p.read_bytes()
                kind={'.html':'html','.htm':'html','.json':'json','.csv':'csv','.xml':'xml','.rss':'xml','.atom':'xml'}.get(p.suffix.lower(),'text')
                base=''; provenance=source['path']
            else:
                if not network:
                    issues.append({'source':ident,'reason':'Network disabled; pass --allow-network to fetch'});continue
                data,kind,base=web.fetch(ident,c.get('max_bytes',1000000),c.get('timeout',10))
                provenance=base
            e=web.extract(data,kind,base)
            if not e['text'] and not e['links']:issues.append({'source':ident,'reason':'No readable text or links'});continue
            docs.append({'id':'S'+str(len(docs)+1),'source':provenance,'type':source.get('type','secondary'),'title':source.get('title') or e['title'] or ident,'retrieved_at':TODAY(),'sha256':hashlib.sha256(data).hexdigest(),'text':e['text'][:200000],'links':list(dict.fromkeys(e['links']))[:100],'tables':e['tables'][:100]})
        except Exception as exc:issues.append({'source':ident,'reason':str(exc)})
    hashes=set();unique=[]
    for d in docs:
        if d['sha256'] in hashes:issues.append({'source':d['source'],'reason':'Duplicate content'});continue
        hashes.add(d['sha256']);d['id']='S'+str(len(unique)+1);unique.append(d)
    return unique,issues

def passages(text,query,limit=4):
    terms=tokens(query)
    segments=re.split(r'(?<=[.!?])\s+|\s*\n+\s*',text)
    ranked=[]
    for index,s in enumerate(segments):
        if len(s)<16:continue
        overlap=terms & tokens(s)
        if overlap:ranked.append((len(overlap),index,s[:500]))
    ranked.sort(key=lambda x:(-x[0],x[1]))
    return [{'text':s,'matched_terms':n} for n,_,s in ranked[:limit]]

def analyze(c,docs,issues):
    targets=c.get('claims',[]) or [c['question']]
    findings=[]
    for claim in targets:
        evidence=[]
        for d in docs:
            for p in passages(d['text'],claim,2):
                evidence.append({'source_id':d['id'],'source':d['source'],'type':d['type'],'passage':p['text'],'matched_terms':p['matched_terms']})
        evidence.sort(key=lambda x:(-x['matched_terms'],x['source_id']))
        findings.append({'claim':claim,'status':'candidate passages; manual verification required' if evidence else 'unresolved','evidence':evidence[:10],'independent_sources':len(set(x['source_id'] for x in evidence)),'warning':'Term overlap is not entailment; quoted passages may contradict the claim.' if evidence else 'No matching passages found.'})
    facets=[]
    for facet in c.get('must_cover',[]):
        hits=[d['id'] for d in docs if tokens(facet)&tokens(d['text'])]
        facets.append({'facet':facet,'status':'candidate coverage' if hits else 'gap','sources':hits})
    types={d['type'] for d in docs}
    gaps=[{'type':'source_class','value':x} for x in c.get('source_classes',[]) if x not in types]
    gaps += [{'type':'facet','value':x['facet']} for x in facets if x['status']=='gap']
    if not docs:gaps.append({'type':'evidence','value':'No readable sources acquired'})
    return {'findings':findings,'coverage':facets,'gaps':gaps,'source_types':sorted(types),'issues':issues,'completion':'review required' if not gaps else 'incomplete'}

def public_docs(docs,include_text=False):
    return [{k:v for k,v in d.items() if include_text or k!='text'} for d in docs]

def monitor(c,docs,state_path,approve=False):
    p=pathlib.Path(state_path)
    old={}
    if p.exists():
        old=json.loads(p.read_text(encoding='utf-8'))
        if not isinstance(old,dict) or old.get('version')!=1:raise ValueError('Invalid state version')
    indexed={d['source']:d for d in docs};prior=old.get('sources',{})
    events=[]
    for name,d in indexed.items():
        prev=prior.get(name)
        if prev is None:events.append({'source':name,'change':'new'})
        elif prev.get('sha256')!=d['sha256']:
            before=prev.get('text','');after=d['text']
            diff=list(difflib.unified_diff(before.splitlines(),after.splitlines(),n=1))[:80]
            events.append({'source':name,'change':'updated','diff':diff})
    for name in prior.keys()-indexed.keys():events.append({'source':name,'change':'not_observed','note':'Not treated as deletion; fetch may have failed'})
    if p.exists() and not approve:raise ValueError('State exists; use --approve-overwrite to update after reviewing')
    payload={'version':1,'updated_at':TODAY(),'sources':{**prior,**{name:{'sha256':d['sha256'],'text':d['text'][:200000]} for name,d in indexed.items()}}}
    p.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=p.parent,prefix='.research-',delete=False) as f:
        json.dump(payload,f,ensure_ascii=False,indent=2);tmp=pathlib.Path(f.name)
    tmp.replace(p)
    return {'events':events,'state':str(p),'observed':len(indexed)}

def operate(args):
    if args.command=='capabilities':
        items=match_catalog(args)
        return {'ok':True,'count':len(items),'capabilities':items,'note':'Catalog entries identify research workflows; platform-specific integrations are not implied.'}
    c=load_config(args.input)
    items=match_catalog(args)
    if args.command=='plan':return {'ok':True,'operation':'plan','result':plan(c,items)}
    docs,issues=acquire(c,args.allow_network)
    if args.command=='discover':
        q=tokens(c['question']);links=[]
        for d in docs:
            for link in d['links']:
                parsed=urllib.parse.urlsplit(link)
                if parsed.scheme!='https':continue
                score=len(q&tokens(link))
                links.append({'source_id':d['id'],'url':link,'query_term_matches':score})
        links.sort(key=lambda x:-x['query_term_matches'])
        return {'ok':True,'operation':'discover','sources':public_docs(docs),'leads':links[:100],'issues':issues,'note':'Leads are not evidence and are not automatically followed.'}
    if args.command=='extract':return {'ok':True,'operation':'extract','sources':public_docs(docs,args.include_text),'issues':issues}
    if args.command=='monitor':
        if not args.state:raise ValueError('monitor requires --state')
        result=monitor(c,docs,args.state,args.approve_overwrite)
        return {'ok':True,'operation':'monitor','result':result,'issues':issues}
    audit=analyze(c,docs,issues)
    if args.command=='verify':return {'ok':True,'operation':'verify','sources':public_docs(docs),'result':{'findings':audit['findings'],'warning':'No automatic truth determination; inspect citations and contrary evidence.'},'issues':issues}
    if args.command=='audit':return {'ok':True,'operation':'audit','sources':public_docs(docs),'result':audit}
    # full workbench pipeline, including synthesis as citation-backed candidate passages
    return {'ok':True,'operation':args.command,'plan':plan(c,items),'sources':public_docs(docs,args.include_text),'result':audit,'synthesis':[{'claim':f['claim'],'status':f['status'],'citations':[{'source_id':e['source_id'],'passage':e['passage']} for e in f['evidence'][:3]]} for f in audit['findings']],'limitations':['Candidate passages are not independently verified facts','Dynamic browser content, private databases, licensed content and media transcription require separate integrations']}

def main():
    p=argparse.ArgumentParser(description='Bounded research discovery, extraction, evidence audit and monitoring; JSON output.')
    sub=p.add_subparsers(dest='command',required=True)
    for command in ('capabilities','plan','discover','extract','verify','audit','synthesize','monitor','run'):
        s=sub.add_parser(command,help={'run':'Execute unified workflow','capabilities':'List PDF catalog skills'}.get(command,command.capitalize()+' research operation'))
        s.add_argument('--category',help='Category name substring or category number')
        s.add_argument('--skill',help='Exact catalog skill name or ID')
        if command!='capabilities':
            s.add_argument('--input',required=True,help='JSON research configuration')
            s.add_argument('--allow-network',action='store_true',help='Fetch public HTTPS sources after robots check')
            s.add_argument('--include-text',action='store_true',help='Include extracted full text in output')
            s.add_argument('--output',help='Write JSON to this file; existing file needs --approve-overwrite')
            s.add_argument('--approve-overwrite',action='store_true',help='Explicit approval to replace output/state file')
            s.add_argument('--state',help='Monitor state JSON file (monitor only)')
    args=p.parse_args()
    try:
        if args.command!='capabilities' and args.output:
            out=pathlib.Path(args.output)
            if out.exists() and not args.approve_overwrite:raise ValueError('Output exists; pass --approve-overwrite to replace')
            if args.command=='monitor' and args.state and out.resolve()==pathlib.Path(args.state).resolve():raise ValueError('Output and state must be different files')
            if out.resolve()==pathlib.Path(args.input).resolve():raise ValueError('Output cannot replace input configuration')
        result=operate(args)
        payload=json.dumps(result,ensure_ascii=False,indent=2)
        if args.command!='capabilities' and args.output:
            out.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=out.parent,prefix='.research-',delete=False) as f:f.write(payload+'\n');tmp=pathlib.Path(f.name)
            tmp.replace(out)
        print(payload)
    except (ValueError,OSError,UnicodeError,json.JSONDecodeError) as exc:
        print(json.dumps(error('invalid_or_unavailable',exc),ensure_ascii=False),file=sys.stderr)
        return 2
    return 0
if __name__=='__main__':sys.exit(main())
