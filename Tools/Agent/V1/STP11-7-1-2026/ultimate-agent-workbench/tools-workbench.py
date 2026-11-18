#!/usr/bin/env python3
"""Primary CLI. Python 3.13 (validated on 3.13.16); standard library only."""
import argparse
import importlib.util
import json
import math
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('local',HERE/'tools-local.py')
local=importlib.util.module_from_spec(spec);spec.loader.exec_module(local)
CATALOG=json.loads((HERE/'asset-capabilities.json').read_text())
CAPS={x['id']:x for x in CATALOG}


def dump(value): return json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n'

def atomic(root,name,data,approve=False):
    p=local.path(root,name,False)
    if p.exists() and not approve: raise ValueError('Overwrite requires --approve: '+name)
    if not p.parent.is_dir(): raise ValueError('Output parent directory must already exist')
    tmp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('xb') as f: f.write(data);f.flush();os.fsync(f.fileno())
        # Refuse racing creation without using overwrite semantics.
        if not approve: os.link(tmp,p);tmp.unlink()
        else: os.replace(tmp,p)
    finally:
        if tmp.exists():tmp.unlink()


def load(root,name): return json.loads(local.read(root,name))

def connect(root,name):
    p=local.path(root,name,False)
    if not p.parent.is_dir():raise ValueError('State parent must exist')
    db=sqlite3.connect(p,timeout=5,isolation_level=None)
    db.execute('PRAGMA busy_timeout=5000')
    db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
    return db


def state_get(db):
    row=db.execute('SELECT body FROM state WHERE id=1').fetchone()
    if not row:raise ValueError('No project: use plan first')
    return json.loads(row[0])


def save(db,s):
    s['revision']=s.get('revision',0)+1
    db.execute('INSERT OR REPLACE INTO state VALUES (1,?)',(dump(s),))


def topo(tasks):
    ids=[t['id'] for t in tasks]
    if len(set(ids))!=len(ids):raise ValueError('Duplicate task ids')
    known=set(ids);done=set();order=[];layers=[]
    for t in tasks:
        if not isinstance(t.get('depends',[]),list) or not set(t.get('depends',[])) <= known:raise ValueError('Unknown dependency')
    while len(done)<len(tasks):
        ready=[t for t in tasks if t['id'] not in done and set(t.get('depends',[])) <= done]
        if not ready:raise ValueError('Dependency cycle')
        layers.append([t['id'] for t in ready]);order+=ready;done.update(t['id'] for t in ready)
    return order,layers


def schedule(s,count):
    completed=s.get('completed-stages',0)
    if not isinstance(count,int) or not completed<count<=100:raise ValueError('Stage count must exceed completed stages and be <=100')
    remaining=[t for t in s['tasks'] if t['status']!='done']
    if count-completed>max(1,len(remaining)):raise ValueError('More stages than remaining coherent work packages')
    order,_=topo(s['tasks']); todo=[t for t in order if t['status']!='done']
    for i,t in enumerate(todo):t['stage']=completed+1+min(count-completed-1,i*(count-completed)//max(1,len(todo)))
    s['stages']=count


def plan(req,approved,stages,root):
    if not isinstance(req,dict) or not isinstance(req.get('goal'),str) or not req['goal'].strip():raise ValueError('goal must be nonempty text')
    tasks=req.get('tasks')
    if not isinstance(tasks,list) or not 1<=len(tasks)<=400:raise ValueError('tasks must contain 1..400 objects')
    for t in tasks:
        if not isinstance(t,dict) or not isinstance(t.get('id'),str) or not t['id'].isascii() or not t['id'].replace('-','').isalnum():raise ValueError('Task ids must be ASCII alphanumeric/hyphen')
        if t.get('capability') not in CAPS:raise ValueError('Unknown capability: '+str(t.get('capability')))
        if t.get('operation','external') not in local.OPS+['external']:raise ValueError('Unknown operation')
        t.setdefault('operation','external');t.setdefault('params',{});t.setdefault('depends',[]);t.setdefault('requirements',[]);t.setdefault('files',[]);t.setdefault('duration',1)
        if not isinstance(t['params'],dict) or not isinstance(t['files'],list) or not isinstance(t['requirements'],list):raise ValueError('Invalid task params/files/requirements')
        if not isinstance(t['duration'],(int,float)) or not math.isfinite(t['duration']) or t['duration']<=0:raise ValueError('duration must be finite and positive')
        if not isinstance(t.get('acceptance'),str) or not t['acceptance'].strip():raise ValueError('Every task needs acceptance criteria')
        for f in t['files']:local.fingerprint(root,f)
        if t.get('output'):local.path(root,t['output'],False)
        t['status']='pending';t['result']=None;t['attempts']=0
    requirements=req.get('requirements',[])
    if not isinstance(requirements,list) or not all(isinstance(x,str) and x for x in requirements) or len(set(requirements))!=len(requirements):raise ValueError('requirements must be unique nonempty strings')
    if any(not set(t['requirements']) <= set(requirements) for t in tasks):raise ValueError('Task references unknown requirement')
    uncovered=set(requirements)-{r for t in tasks for r in t['requirements']}
    if uncovered:raise ValueError('Unassigned requirements: '+', '.join(sorted(uncovered)))
    order,layers=topo(tasks);ends={};critical={}
    for t in order:
        deps=t['depends'];prior=max(deps,key=lambda x:ends[x]) if deps else None
        ends[t['id']]=(ends[prior] if prior else 0)+t['duration'];critical[t['id']]=(critical[prior] if prior else [])+[t['id']]
    final=max(ends,key=ends.get)
    rec=min(len(tasks),max(1,math.ceil((len(tasks)+len(requirements)/4)/4)))
    s={'version':1,'goal':req['goal'],'requirements':requirements,'tasks':tasks,'recommended-stages':rec,'approved':approved,'completed-stages':0,'cancelled':False,'parallel-layers':layers,'critical-path':critical[final],'critical-duration':ends[final],'events':[], 'baselines':{f:local.fingerprint(root,f) for t in tasks for f in t['files']}}
    schedule(s,stages or rec);return s


def check_files(s,root):
    for f,base in s['baselines'].items():
        if local.fingerprint(root,f)!=base:raise ValueError('Required file changed: '+f+'; validate replacement then recover --refresh-files --approve')


def run_stage(s,root,signal):
    if not s['approved']:raise ValueError('Plan approval required: replan --stages N --approve')
    if s['cancelled']:raise ValueError('Cancelled; recover --approve first')
    if s['completed-stages']>=s['stages']:return {'status':'complete','state':s}
    if s['completed-stages'] and signal.casefold().strip() not in ('continue','proceed','go on','next'):raise ValueError('Explicit Continue signal required')
    check_files(s,root);stage=s['completed-stages']+1
    for t in topo(s['tasks'])[0]:
        if t['stage']!=stage or t['status']=='done':continue
        if t['status']=='failed':return {'status':'repair','task':t['id'],'error':t.get('error')}
        if any(next(x for x in s['tasks'] if x['id']==d)['status']!='done' for d in t['depends']):return {'status':'blocked','task':t['id'],'reason':'Dependency evidence incomplete'}
        if t['operation']=='external':return {'status':'blocked','task':t['id'],'capability':CAPS[t['capability']], 'acceptance':t['acceptance'],'reason':'Domain execution unavailable locally. Supply reviewed result and evidence with submit. No domain work has been claimed complete.'}
        t['attempts']+=1
        try:
            result=local.execute(t['operation'],t['params'],root)
            if isinstance(result,tuple):
                result,data=result
                if not t.get('output'):raise ValueError('This operation requires task output path')
                atomic(root,t['output'],data)
                result['artifact']=local.fingerprint(root,t['output'])
            t['result']=result
            # Failed validation is never marked done.
            if isinstance(result,dict) and result.get('valid') is False:raise ValueError('Validation returned valid=false')
            t['status']='done'
        except (ValueError,KeyError,TypeError,OSError,SyntaxError,UnicodeError,zip_errors()) as e:
            t['status']='failed';t['error']=str(e);return {'status':'repair','task':t['id'],'error':str(e)}
    s['completed-stages']=stage
    return {'status':'checkpoint' if stage<s['stages'] else 'complete','label':f'Stage {stage} of {s["stages"]}','next':'Continue' if stage<s['stages'] else None,'coverage':coverage(s)}


def zip_errors():
    import zipfile
    return zipfile.BadZipFile


def coverage(s):return {r:[t['id'] for t in s['tasks'] if r in t['requirements'] and t['status']=='done'] for r in s['requirements']}


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',default='.',help='Workspace root; all data paths are relative to it')
    p.add_argument('--state',default='workbench-state.sqlite',help='Relative SQLite state path')
    sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('capabilities',help='List 242 workflow specifications and implemented local operations');c.add_argument('--category');c.add_argument('--input',help='Optional catalog override relative to root')
    c=sub.add_parser('process',help='Execute one bounded local operation');c.add_argument('operation',choices=local.OPS);c.add_argument('--input',required=True,help='JSON params file');c.add_argument('--output',help='Artifact or JSON output');c.add_argument('--approve',action='store_true')
    c=sub.add_parser('plan',help='Validate, estimate and persist a DAG plan');c.add_argument('--input',required=True);c.add_argument('--stages',type=int);c.add_argument('--approve',action='store_true')
    c=sub.add_parser('replan',help='Expand/compress remaining stages without losing tasks');c.add_argument('--stages',type=int,required=True);c.add_argument('--approve',action='store_true')
    c=sub.add_parser('run',help='Execute current stage; pause on external work or failure');c.add_argument('--signal',default='')
    c=sub.add_parser('submit',help='Accept human-reviewed external evidence');c.add_argument('--task',required=True);c.add_argument('--input',required=True);c.add_argument('--approve',action='store_true')
    c=sub.add_parser('recover',help='Retry failed work or accept replacement files');c.add_argument('--task');c.add_argument('--refresh-files',action='store_true');c.add_argument('--approve',action='store_true')
    sub.add_parser('status',help='Inspect durable project state and requirement coverage')
    sub.add_parser('cancel',help='Pause future work; preserves all completed outputs')
    c=sub.add_parser('export',help='Export complete state and coverage as JSON');c.add_argument('--output',required=True);c.add_argument('--approve',action='store_true')
    return p


def main():
    args=parser().parse_args();root=Path(args.root).resolve()
    if not root.is_dir():raise ValueError('Workspace root does not exist')
    if args.command=='capabilities':
        catalog=load(root,args.input) if args.input else CATALOG
        return {'local-operations':local.OPS,'capabilities':[c for c in catalog if not args.category or args.category.casefold() in c['category'].casefold()],'notice':'All catalog entries are evidence workflows. Only listed local operations run autonomously.'}
    if args.command=='process':
        result=local.execute(args.operation,load(root,args.input),root)
        if isinstance(result,tuple):
            if not args.output:raise ValueError('Binary/HTML operation requires --output')
            result,data=result;atomic(root,args.output,data,args.approve);result['artifact']=local.fingerprint(root,args.output)
        elif args.output:atomic(root,args.output,dump(result).encode(),args.approve)
        return result
    db=connect(root,args.state)
    try:
        db.execute('BEGIN IMMEDIATE')
        if args.command=='plan':
            if db.execute('SELECT 1 FROM state').fetchone():raise ValueError('State already contains a project; choose a different --state')
            s=plan(load(root,args.input),args.approve,args.stages,root);result={'status':'approved' if args.approve else 'awaiting-approval','state':s}
        else:
            s=state_get(db)
            if args.command=='replan':
                if not args.approve:raise ValueError('Replan requires --approve')
                schedule(s,args.stages);s['approved']=True;result=s
            elif args.command=='run':result=run_stage(s,root,args.signal)
            elif args.command=='submit':
                if not args.approve:raise ValueError('Human review approval requires --approve')
                if not s['approved'] or s['cancelled']:raise ValueError('Project not approved or cancelled')
                check_files(s,root)
                t=next((t for t in s['tasks'] if t['id']==args.task),None)
                if not t or t['operation']!='external' or t['status']=='done':raise ValueError('Task must be an unfinished external task')
                if t['stage']!=s['completed-stages']+1 or any(next(x for x in s['tasks'] if x['id']==d)['status']!='done' for d in t['depends']):raise ValueError('Task entry gate not satisfied')
                receipt=load(root,args.input)
                if not isinstance(receipt,dict) or receipt.get('passed') is not True or not isinstance(receipt.get('summary'),str) or not receipt['summary'].strip() or not isinstance(receipt.get('reviewer'),str) or not receipt['reviewer'].strip() or not isinstance(receipt.get('evidence'),list) or not receipt['evidence']:raise ValueError('Receipt requires passed=true, summary, reviewer, nonempty evidence path list')
                receipt['fingerprints']=[local.fingerprint(root,f) for f in receipt['evidence']]
                t['result']=receipt;t['status']='done';result={'accepted':t['id'],'trust':'Human-attested; not independently domain-verified'}
            elif args.command=='recover':
                if not args.approve:raise ValueError('Recovery requires --approve')
                if args.refresh_files:s['baselines']={f:local.fingerprint(root,f) for f in s['baselines']}
                matches=[t for t in s['tasks'] if t['status']=='failed' and (not args.task or t['id']==args.task)]
                if args.task and not matches:raise ValueError('No matching failed task')
                for t in matches:t['status']='pending';t.pop('error',None)
                s['cancelled']=False;result={'retry-tasks':[t['id'] for t in matches],'warning':'Existing artifacts are never overwritten by run. Move an already-written output aside before retrying.'}
            elif args.command=='cancel':s['cancelled']=True;result={'status':'cancelled','preserved':True}
            elif args.command in ('status','export'):
                result={'state':s,'coverage':coverage(s),'complete':s['completed-stages']==s['stages'] and all(t['status']=='done' for t in s['tasks'])}
                if args.command=='export':atomic(root,args.output,dump(result).encode(),args.approve)
            else:raise ValueError('Unknown command')
        if args.command not in ('status','export'):
            s['events'].append({'command':args.command,'at':int(time.time())});save(db,s)
        db.execute('COMMIT');return result
    except BaseException:
        if db.in_transaction:db.execute('ROLLBACK')
        raise
    finally:db.close()


if __name__=='__main__':
    try: print(dump({'ok':True,'result':main()}))
    except KeyboardInterrupt:print(dump({'ok':False,'error':{'code':'cancelled','message':'Interrupted; transaction rolled back. Inspect artifacts before retrying.'}}));sys.exit(130)
    except Exception as e:print(dump({'ok':False,'error':{'code':type(e).__name__,'message':str(e),'recovery':'Correct input or inspect status; use approved recover for failed tasks.'}}));sys.exit(2)
