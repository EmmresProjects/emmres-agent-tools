"""Declarative orchestration, checkpoints, evidence, policy, and approvals."""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from workspace_core import Context, ToolError, canonical, digest, file_sha, integer, load_json, redact, string, strings, utc

_DISPATCH=None
_REGISTRY={}


def configure(dispatch,registry):
    global _DISPATCH,_REGISTRY
    _DISPATCH,_REGISTRY=dispatch,registry


def _load_workflow(ctx: Context) -> tuple[dict,str]:
    name=ctx.request.get('workflow')
    p=ctx.path(string(name,'workflow'),must_exist=True) if name is not None else Path(__file__).with_name('asset-workflow.json')
    wf=load_json(p,ctx.limits['max_input_bytes'])
    if not isinstance(wf,dict) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',str(wf.get('workflow_id',''))):
        raise ToolError('invalid_input','Workflow needs lowercase hyphenated workflow_id')
    steps=wf.get('steps')
    if not isinstance(steps,list) or not steps or len(steps)>ctx.limits['max_workflow_steps']:
        raise ToolError('invalid_input','Workflow must contain 1..max_workflow_steps steps')
    seen=set()
    for step in steps:
        if not isinstance(step,dict) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',str(step.get('id',''))):
            raise ToolError('invalid_input','Every step needs a lowercase hyphenated id')
        if step['id'] in seen:raise ToolError('invalid_input','Duplicate workflow step id',step=step['id'])
        seen.add(step['id'])
        if step.get('capability') not in _REGISTRY:
            raise ToolError('invalid_input','Unknown workflow capability',step=step['id'])
        if not isinstance(step.get('request',{}),dict):
            raise ToolError('invalid_input','Step request must be a JSON object',step=step['id'])
        if type(step.get('parallel',False)) is not bool:
            raise ToolError('invalid_input','parallel flag must be boolean',step=step['id'])
        integer(step.get('priority',0),'step.priority',0,10)
        if type(step.get('allow_failed_check',False)) is not bool:
            raise ToolError('invalid_input','allow_failed_check must be boolean',step=step['id'])
        if step.get('parallel') and _REGISTRY[step['capability']]['mode']=='action':
            raise ToolError('invalid_input','Consequential/action steps cannot run in parallel',step=step['id'])
        strings(step.get('depends_on',[]),'depends_on',maximum=len(steps))
        route=_REGISTRY[step['capability']]['route']
        if route.startswith('workflow.') and route.split('.')[1] in ('run','resume'):
            raise ToolError('invalid_input','Nested workflow execution is not allowed',step=step['id'])
    for step in steps:
        if any(x not in seen or x==step['id'] for x in step.get('depends_on',[])):
            raise ToolError('invalid_input','Invalid dependency reference',step=step['id'])
    pending={step['id']:set(step.get('depends_on',[])) for step in steps}
    layers=[]
    while pending:
        ready=sorted(k for k,v in pending.items() if not v)
        if not ready:raise ToolError('invalid_input','Workflow has a dependency cycle')
        layers.append(ready)
        for k in ready:del pending[k]
        for deps in pending.values():deps.difference_update(ready)
    return wf,digest(canonical(wf))


def _subcontext(ctx: Context, capid: str, request: dict, *, apply=False, approve=None, root=None) -> Context:
    return Context(str(root or ctx.root),ctx.policy,_REGISTRY[capid],request,apply=apply,approve=approve)


def _checkpoint_path(ctx: Context,wf: dict) -> Path:
    return ctx.state/('workflow-'+wf['workflow_id']+'.json')


def _load_checkpoint(ctx: Context,wf: dict, hash_value: str) -> dict | None:
    path=_checkpoint_path(ctx,wf)
    if not path.is_file():return None
    checkpoint=load_json(path,ctx.limits['max_input_bytes'])
    if checkpoint.get('workflow_sha256')!=hash_value:
        raise ToolError('checkpoint_mismatch','Workflow changed since checkpoint; choose a new workflow_id or inspect prior checkpoint')
    return checkpoint


def _fingerprints(ctx: Context, result: dict) -> list[dict]:
    candidates=[]
    for key in ('path','destination'):
        name=result.get(key)
        if isinstance(name,str):
            try:
                p=ctx.path(name)
                if p.is_file() and p.stat().st_size<=ctx.limits['max_transfer_bytes']:
                    candidates.append({'path':name,'sha256':file_sha(p)})
            except ToolError:pass
    for item in result.get('entries',[]) if isinstance(result.get('entries'),list) else []:
        if isinstance(item,dict) and item.get('state')=='done' and 'destination' in item:
            name=item['destination'];p=ctx.path(name)
            if p.is_file():candidates.append({'path':name,'sha256':file_sha(p)})
    return candidates


def _completed_valid(ctx: Context,checkpoint: dict,step: dict) -> bool:
    prior=checkpoint.get('completed',{}).get(step['id'])
    if not prior or prior.get('request_sha256')!=digest(canonical(step.get('request',{}))):return False
    if _REGISTRY[step['capability']]['mode']!='action':return False
    fingerprints=prior.get('outputs',[])
    if not fingerprints:return False
    return all(ctx.path(x['path']).is_file() and file_sha(ctx.path(x['path']))==x['sha256'] for x in fingerprints)


def _preview(ctx: Context,wf: dict,checkpoint: dict | None=None, *, resume: bool=False) -> tuple[dict,dict]:
    previews=[]; tokens={}
    for step in wf['steps']:
        cap=_REGISTRY[step['capability']]
        if resume and checkpoint and _completed_valid(ctx,checkpoint,step):
            previews.append({'id':step['id'],'capability':cap['id'],'status':'skipped-verified-checkpoint'})
            continue
        sub=_subcontext(ctx,cap['id'],step.get('request',{}))
        if cap['mode']=='action':
            value=_DISPATCH(sub,cap['route'])
            if value.get('approval_token'):tokens[step['id']]=value['approval_token']
            previews.append({'id':step['id'],'capability':cap['id'],'status':value.get('status','inspected'),
                             'plan':value.get('plan'), 'requires_approval':bool(value.get('approval_token'))})
        else:
            # Read-only validation catches bad inputs and failed checks before any mutation.
            value=_DISPATCH(sub,cap['route'])
            passed=value.get('valid',value.get('ready',value.get('covered',None)))
            if passed is False and not step.get('allow_failed_check',False):
                raise ToolError('verification_failed','Read-only preflight check failed; no workflow mutation started',step=step['id'])
            previews.append({'id':step['id'],'capability':cap['id'],'status':'inspected',
                             'check_passed':passed})
    return {'workflow_id':wf['workflow_id'],'steps':previews},tokens


def _call_readonly(ctx: Context,step: dict):
    cap=_REGISTRY[step['capability']]
    retries=ctx.limits['max_retries']
    for n in range(retries+1):
        try:return _DISPATCH(_subcontext(ctx,cap['id'],step.get('request',{})),cap['route'])
        except ToolError as exc:
            if exc.code not in ('io_error','timeout','network_failed') or n==retries:
                raise ToolError(exc.code,exc.message,**{**exc.details,'step':step['id']}) from exc
            time.sleep(min(2**n,4))


def _execute_workflow(ctx: Context, *, resume: bool=False) -> dict:
    wf,hash_value=_load_workflow(ctx)
    checkpoint=_load_checkpoint(ctx,wf,hash_value) if resume else None
    plan,tokens=_preview(ctx,wf,checkpoint,resume=resume)
    approval_plan={'workflow_id':wf['workflow_id'],'workflow_sha256':hash_value,'resume':resume,'steps':plan['steps']}
    pending=ctx.authorize(approval_plan,'run bounded declared workflow; all mutating stages must match reviewed preflight')
    if pending:return {**pending,'workflow':plan}
    path=_checkpoint_path(ctx,wf)
    if path.exists() and not resume:
        raise ToolError('collision','Workflow checkpoint already exists; inspect it or use resume',workflow_id=wf['workflow_id'])
    record=checkpoint or {'workflow_id':wf['workflow_id'],'workflow_sha256':hash_value,'started':utc(),'completed':{},'failures':[]}
    steps={s['id']:s for s in wf['steps']}
    pending_ids=set(steps);done=set();results={}
    def save():
        ctx.ensure_state()
        ctx.atomic_bytes(path,(json.dumps(record,indent=2,ensure_ascii=False)+'\n').encode(),overwrite=path.exists())
    for step in wf['steps']:
        if resume and checkpoint and _completed_valid(ctx,checkpoint,step):
            done.add(step['id']);pending_ids.remove(step['id']);results[step['id']]={'status':'skipped-verified-checkpoint'}
    while pending_ids:
        if (ctx.state/'cancel').exists():
            record['status']='cancelled';save()
            return {'status':'cancelled','workflow_id':wf['workflow_id'],'completed_steps':sorted(done),'results':results}
        ready=[steps[s['id']] for s in wf['steps'] if s['id'] in pending_ids and set(s.get('depends_on',[]))<=done]
        ready.sort(key=lambda s:-s.get('priority',0))
        if not ready:raise ToolError('internal_error','No ready steps despite validated DAG')
        parallel=[s for s in ready if s.get('parallel',False) and _REGISTRY[s['capability']]['mode']!='action']
        batch=parallel if parallel else ready[:1]
        try:
            if len(batch)>1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(4,len(batch))) as pool:
                    futures={pool.submit(_call_readonly,ctx,s):s for s in batch}
                    returned={s['id']:future.result() for future,s in futures.items()}
            else:
                step=batch[0];cap=_REGISTRY[step['capability']]
                if cap['mode']=='action':
                    sub=_subcontext(ctx,cap['id'],step.get('request',{}),apply=True,approve=tokens.get(step['id']))
                    returned={step['id']:_DISPATCH(sub,cap['route'])}
                else:returned={step['id']:_call_readonly(ctx,step)}
            for s in batch:
                result=returned[s['id']]
                passed=result.get('valid',result.get('ready',result.get('covered',None)))
                if result.get('status') in ('failed','cancelled','approval_required') or (passed is False and not s.get('allow_failed_check',False)):
                    raise ToolError('workflow_stage_failed','Stage did not complete or a required check failed',step=s['id'],
                                    status=result.get('status'),check_passed=passed)
                record['completed'][s['id']]={'finished':utc(),'capability':s['capability'],
                     'request_sha256':digest(canonical(s.get('request',{}))),
                     'result_sha256':digest(canonical(result)), 'outputs':_fingerprints(ctx,result)}
                done.add(s['id']);pending_ids.remove(s['id']);results[s['id']]=result
                record['status']='running';save()
        except ToolError as exc:
            failed_id=exc.details.get('step',batch[0]['id'])
            record['status']='failed';record['failures'].append({'step':failed_id,'code':exc.code,'time':utc()});save()
            return {'status':'failed','workflow_id':wf['workflow_id'],'completed_steps':sorted(done),
                    'failed_step':failed_id,'error':{'code':exc.code,'message':exc.message},
                    'recovery':'Inspect checkpoint; repair smallest failed stage and rerun workspace-task-orchestrator with resume.'}
    record['status']='completed';record['finished']=utc();save()
    return {'status':'completed','workflow_id':wf['workflow_id'],'completed_steps':sorted(done),'results':results,
            'checkpoint':ctx.relative(path)}


def _scrub_event(value, key=''):
    if re.search(r'(?i)(password|secret|token|credential|private[_-]?key|authorization|cookie)', key):
        return '[REDACTED]'
    if isinstance(value, dict):
        return {k:_scrub_event(v, str(k)) for k,v in value.items()}
    if isinstance(value, list):
        return [_scrub_event(v) for v in value]
    if isinstance(value, str):
        if '-----BEGIN' in value and 'PRIVATE KEY-----' in value:
            return '[REDACTED PRIVATE KEY]'
        return redact(value)
    return value


def _event(ctx: Context) -> dict:
    event=ctx.request.get('event')
    if not isinstance(event,dict) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',str(event.get('type',''))):
        raise ToolError('invalid_input','event must be an object with lowercase hyphenated type')
    if len(canonical(event).encode())>ctx.limits['max_input_bytes']:
        raise ToolError('limit_exceeded','Event too large')
    # Values are redacted BEFORE storage, not just at presentation time.
    safe=_scrub_event(event)
    plan={'type':safe['type'],'event_sha256':digest(canonical(safe))}
    pending=ctx.authorize(plan,'append redacted hash-chained local audit event')
    if pending:return pending
    ctx.ensure_state();path=ctx.state_file('activity.jsonl')
    previous='0'*64
    if path.is_file():
        with path.open('rb') as f:
            for line in f:
                if line.strip():previous=json.loads(line)['hash']
    row={'time':utc(),'event':safe,'previous':previous}
    row['hash']=digest(canonical(row))
    fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
    with os.fdopen(fd,'ab') as out:
        out.write((canonical(row)+'\n').encode());out.flush();os.fsync(out.fileno())
    return {'status':'completed','event_type':safe['type'],'hash':row['hash'],'path':ctx.relative(path)}


def _log_verify(ctx: Context) -> dict:
    if not ctx.state.is_dir():return {'valid':True,'events':0,'files':[]}
    files=sorted(ctx.state.glob('activity-*.jsonl'))+([ctx.state/'activity.jsonl'] if (ctx.state/'activity.jsonl').is_file() else [])
    prev='0'*64;events=0;issues=[]
    for p in files:
        if p.is_symlink():raise ToolError('unsafe_path','Audit log is symlinked')
        with p.open('rb') as f:
            for n,line in enumerate(f,1):
                try:
                    row=json.loads(line)
                    supplied=row.pop('hash')
                    if row['previous']!=prev or digest(canonical(row))!=supplied:
                        issues.append({'file':p.name,'line':n,'issue':'hash-chain-break'})
                    prev=supplied;events+=1
                except (ValueError,KeyError,TypeError):
                    issues.append({'file':p.name,'line':n,'issue':'malformed-event'})
    return {'valid':not issues,'events':events,'files':[ctx.relative(p) for p in files],
            'issues':issues,'note':'An unkeyed hash chain detects accidental edits, not a malicious full rewrite.'}


def _log_rotate(ctx: Context) -> dict:
    path=ctx.state/'activity.jsonl'
    if not path.is_file():return {'status':'unchanged','reason':'no log'}
    threshold=integer(ctx.request.get('max_bytes',1048576),'max_bytes',128,ctx.limits['max_archive_bytes'])
    size=path.stat().st_size
    if size<threshold:return {'status':'unchanged','bytes':size,'max_bytes':threshold}
    if not _log_verify(ctx)['valid']:raise ToolError('verification_failed','Cannot rotate a broken audit chain')
    archives=[int(m.group(1)) for p in ctx.state.glob('activity-*.jsonl')
              if (m:=re.fullmatch(r'activity-(\d{6})\.jsonl',p.name))]
    destination='activity-'+f'{max(archives,default=0)+1:06d}'+'.jsonl'
    plan={'source':'activity.jsonl','source_sha256':file_sha(path),'archive':destination,'bytes':size}
    pending=ctx.authorize(plan,'rotate audit log without deleting old entries; preserve hash-chain continuity')
    if pending:return pending
    target=ctx.state_file(destination)
    if target.exists() or file_sha(path)!=plan['source_sha256']:
        raise ToolError('changed_input','Audit log or rotation target changed after approval')
    os.rename(path,target)
    return {'status':'completed','archive':ctx.relative(target),'events':_log_verify(ctx)['events']}


def _policy(ctx: Context) -> dict:
    rows,_=ctx.entries(ctx.request_paths())
    world=[r['path'] for r in rows if r['kind'] in ('file','directory') and int(r['mode'],8)&0o002]
    links=[r['path'] for r in rows if r['kind']=='symlink']
    return {'compliant_by_local_checks':not world and not links,
            'world_writable':world,'symlinks':links,'protected_prefixes':ctx.policy['protected_prefixes'],
            'offline':ctx.policy['offline'],'limits':ctx.limits,
            'note':'Policy enforces this tool only; it cannot enforce ACLs, host network, secrets provider or other applications.'}


def _risk(ctx: Context) -> dict:
    action=string(ctx.request.get('action'),'action',maxlen=100)
    paths=strings(ctx.request.get('paths',[]),'paths',maximum=100)
    destructive=action.lower() in ('delete','purge','replace','chmod','publish','install','execute','stop','expose')
    sensitive=any(ctx.protected(n) for n in paths)
    irrevers=action.lower() in ('purge','publish','credential-rotate')
    score=int(destructive)*3+int(sensitive)*3+int(irrevers)*4+min(3,len(paths)//10)
    return {'action':action,'paths':paths,'score':score,'level':'high' if score>=6 else 'medium' if score>=3 else 'low',
            'requires_approval':destructive or sensitive or irrevers,
            'factors':{'consequential':destructive,'sensitive_or_protected':sensitive,'irreversible':irrevers},
            'note':'A decision aid, not automatic authorization.'}


def _summary(ctx: Context) -> dict:
    rows,_=ctx.entries(ctx.request_paths())
    return {'workspace':ctx.root.name,'files':sum(x['kind']=='file' for x in rows),
            'directories':sum(x['kind']=='directory' for x in rows),
            'bytes':sum(x['size'] for x in rows if x['kind']=='file'),
            'runbook':['Inspect inventory and snapshot; validate required inputs and security findings.',
                       'Select least-privilege environment, review dry-run plan and approval token.',
                       'Run approved operations, verify formats and semantic checks, then archive/reopen deliverables.',
                       'Stop task-owned jobs, quarantine only reviewed temporary data, retain rollback journals.'],
            'note':'Runbook is derived from observed root counts; user-specific acceptance criteria must be supplied separately.'}


def _evidence(ctx: Context) -> dict:
    workflow=ctx.request.get('workflow_id')
    if workflow:
        workflow=string(workflow,'workflow_id',maxlen=64)
        p=ctx.state/('workflow-'+workflow+'.json')
        if not p.is_file():raise ToolError('missing_input','Workflow checkpoint not found')
        cp=load_json(p,ctx.limits['max_input_bytes'])
        return {'workflow_id':workflow,'status':cp.get('status'),'completed':cp.get('completed',{}),
                'failures':cp.get('failures',[]),'audit_chain':_log_verify(ctx)}
    return {'audit_chain':_log_verify(ctx),
            'checkpoints':[p.name for p in ctx.state.glob('workflow-*.json')] if ctx.state.is_dir() else []}


def _recover(ctx: Context) -> dict:
    ident=string(ctx.request.get('workflow_id'),'workflow_id',maxlen=64)
    p=ctx.state/('workflow-'+ident+'.json')
    if not p.is_file():raise ToolError('missing_input','Workflow checkpoint not found')
    cp=load_json(p,ctx.limits['max_input_bytes'])
    failures=cp.get('failures',[])
    return {'status':cp.get('status'),'earliest_failed_step':failures[0] if failures else None,
            'passing_steps':sorted(cp.get('completed',{})),
            'recovery':'Correct the first failing input or environment, validate its output, then rerun workflow.resume with fresh approval.',
            'note':'No speculative rollback or automatic retry of consequential stages is performed.'}


def replay_readonly(ctx: Context,wf: dict,declared: list[str]) -> dict:
    for s in wf['steps']:
        cap=_REGISTRY.get(s.get('capability'))
        if not cap or cap['mode']=='action' or cap['route'] in ('domain.clean-rebuild','workflow.run','workflow.resume'):
            raise ToolError('not_allowed','Clean-state replay accepts read-only nonrecursive steps only',step=s.get('id'))
    files=[]
    for name in declared:
        p=ctx.path(name,must_exist=True)
        if not p.is_file() or p.stat().st_size>ctx.limits['max_transfer_bytes']:
            raise ToolError('invalid_input','Declared clean-state input must be a bounded regular file',path=name)
        files.append({'path':name,'sha256':file_sha(p)})
    if not files:raise ToolError('invalid_input','Declare at least one input file for clean-state replay')
    source_results=[];clean_results=[]
    with tempfile.TemporaryDirectory(prefix='workspace-clean-replay-') as root:
        for item in files:
            target=Path(root)/item['path']
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ctx.path(item['path']),target)
            if file_sha(target)!=item['sha256']:raise ToolError('verification_failed','Clean copy checksum differs')
        for step in wf['steps']:
            cap=_REGISTRY[step['capability']]
            original=_DISPATCH(_subcontext(ctx,cap['id'],step.get('request',{})),cap['route'])
            replay=_DISPATCH(_subcontext(ctx,cap['id'],step.get('request',{}),root=root),cap['route'])
            keys=('valid','ready','covered','missing_required','counts')
            comparable={k:original[k] for k in keys if k in original and k in replay}
            agrees=all(original[k]==replay[k] for k in comparable)
            source_results.append({'step':step['id'],'status':'inspected','comparable_keys':list(comparable)})
            clean_results.append({'step':step['id'],'matches_declared_checks':agrees,
                                  'clean_status':{k:replay[k] for k in comparable}})
    return {'clean_state_replay_passed':all(x['matches_declared_checks'] for x in clean_results),
            'inputs':files,'original':source_results,'clean':clean_results,
            'note':'Replays read-only checks in a temporary root with only declared files; external builds, renderers and services are not claimed.'}


def run(ctx: Context,variant: str) -> dict:
    if variant in ('run','resume'):
        return _execute_workflow(ctx,resume=variant=='resume')
    if variant=='design':
        wf,h=_load_workflow(ctx)
        return {'workflow_id':wf['workflow_id'],'sha256':h,
                'steps':[{'id':s['id'],'capability':s['capability'],'depends_on':s.get('depends_on',[]),
                          'parallel':s.get('parallel',False),'mode':_REGISTRY[s['capability']]['mode']} for s in wf['steps']],
                'valid':True,'note':'Validation only; run preflight before applying.'}
    if variant=='ledger':return _event(ctx)
    if variant=='log-rotate':return _log_rotate(ctx)
    if variant=='log-verify':return _log_verify(ctx)
    if variant=='policy':return _policy(ctx)
    if variant in ('risk','gate'):return _risk(ctx)
    if variant=='summary':return _summary(ctx)
    if variant=='evidence':return _evidence(ctx)
    if variant=='recover':return _recover(ctx)
    raise ToolError('internal_error','Unknown workflow route',variant=variant)
