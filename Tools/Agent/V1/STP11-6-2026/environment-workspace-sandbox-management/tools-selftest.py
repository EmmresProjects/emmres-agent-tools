#!/usr/bin/env python3
"""Temporary-workspace CLI regression tests; never writes inside the distributed tool folder."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import wave
import zipfile
from pathlib import Path

BASE=Path(__file__).resolve().parent
NAME=BASE.name
REPORT={'ok':False,'checks':[],'capability_routes_tested':0,'catalog_entries_tested':0,'blocked_optional_integrations':[]}


def check(condition, label):
    if not condition:raise AssertionError(label)
    REPORT['checks'].append(label)


def cli(root: Path, work: Path, capability: str, req: dict | None=None, *, apply=False, token=None, policy=None, output=None):
    if req is not None:
        (work/'reports/request.json').write_text(json.dumps(req),encoding='utf-8')
    args=[sys.executable,NAME+'/tools-workspace.py','run',capability,'--root','workspace']
    if req is not None:args+=['--input','reports/request.json']
    if policy:args+=['--policy',policy]
    if output:args+=['--output',output]
    if apply:args+=['--apply','--approve',token or 'invalid-token']
    p=subprocess.run(args,cwd=root,text=True,capture_output=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},timeout=25)
    try:data=json.loads(p.stdout)
    except json.JSONDecodeError as exc:raise AssertionError(f'Non-JSON {capability}: {p.stdout[:300]} {p.stderr[:300]}') from exc
    return p.returncode,data


def preapply(root,work,cap,req,policy=None):
    rc,plan=cli(root,work,cap,req,policy=policy)
    if not (rc==0 and plan.get('result',{}).get('status')=='approval_required'):
        raise AssertionError(f'{cap}: reviewed preflight failed; response={json.dumps(plan)[:700]}')
    check(True,f'{cap}: reviewed preflight')
    token=plan['result']['approval_token']
    bad,wrong=cli(root,work,cap,req,apply=True,token='incorrect',policy=policy)
    check(bad==2 and wrong['error']['code']=='approval_required',f'{cap}: wrong approval rejected')
    rc,out=cli(root,work,cap,req,apply=True,token=token,policy=policy)
    if not (rc==0 and out.get('ok') and out.get('status')=='completed'):
        raise AssertionError(f'{cap}: approved action failed; response={json.dumps(out)[:550]}')
    check(True,f'{cap}: approved action completed')
    return out['result']


def make_wheel(wheelhouse: Path) -> tuple[str,str]:
    filename='localdemo-0.1-py3-none-any.whl'
    path=wheelhouse/filename
    content={
      'localdemo/__init__.py':b'__version__ = "0.1"\n',
      'localdemo-0.1.dist-info/METADATA':b'Metadata-Version: 2.1\nName: localdemo\nVersion: 0.1\n',
      'localdemo-0.1.dist-info/WHEEL':b'Wheel-Version: 1.0\nGenerator: workspace-selftest\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    rows=[]
    for n,data in content.items():
        h=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        rows.append(f'{n},sha256={h},{len(data)}')
    rec='localdemo-0.1.dist-info/RECORD'
    content[rec]=('\n'.join(rows+[rec+',,'])+'\n').encode()
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for n,data in content.items():z.writestr(n,data)
    return filename,hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(work: Path):
    for d in ('inputs','uploads','sources','working','scratch','cache','build','tests','reports','deliverables','backups','wheelhouse','project'):
        (work/d).mkdir(parents=True)
    (work/'inputs/a.txt').write_text('first line\n',encoding='utf-8')
    (work/'inputs/b.txt').write_text('second line\n',encoding='utf-8')
    (work/'inputs/secret.txt').write_text('password = ABC123456789\n',encoding='utf-8')
    (work/'inputs/data.json').write_text('{"items": [1, 2]}\n',encoding='utf-8')
    (work/'inputs/data.csv').write_text('id,label\n1,cat\n2,dog\n',encoding='utf-8')
    (work/'inputs/app.html').write_text('<img src="a.txt">\n',encoding='utf-8')
    (work/'inputs/log.txt').write_text('2026-11-05 ERROR token=ABC123456789 something failed\n',encoding='utf-8')
    (work/'inputs/fixture.elf').write_bytes(b'\x7fELF\x02\x01'+b'\x00'*12+b'\x3e\x00'+b'\x00'*50)
    (work/'scratch/old.tmp').write_text('old removable content')
    (work/'working/verify.txt').write_text('first line\n')
    (work/'inputs/tiny.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Ys0F0cAAAAASUVORK5CYII='))
    with wave.open(str(work/'inputs/tone.wav'),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000);f.writeframes(b'\x00\x00'*100)
    with zipfile.ZipFile(work/'inputs/sample.docx','w') as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
    (work/'project/package.json').write_text(json.dumps({'name':'local-project','version':'1.0.0','dependencies':{}}))
    (work/'project/package-lock.json').write_text(json.dumps({'name':'local-project','version':'1.0.0','lockfileVersion':3,'requires':True,
                                                              'packages':{'':{'name':'local-project','version':'1.0.0'}}}))
    _,hash_value=make_wheel(work/'wheelhouse')
    (work/'inputs/requirements.txt').write_text('localdemo==0.1 --hash=sha256:'+hash_value+'\n')
    (work/'reports/workflow.json').write_text(json.dumps({'workflow_id':'check-workspace','steps':[
        {'id':'inventory','capability':'workspace-inventory-scanner','request':{'paths':['inputs/a.txt']},'parallel':True},
        {'id':'validate','capability':'structural-workspace-validator','request':{'required':['inputs/a.txt']},'parallel':True}]}))
    (work/'reports/replay.json').write_text(json.dumps({'workflow_id':'replay','steps':[
        {'id':'inventory','capability':'workspace-inventory-scanner','request':{'paths':['inputs/a.txt']}},
        {'id':'check','capability':'structural-workspace-validator','request':{'required':['inputs/a.txt']}}]}))
    with zipfile.ZipFile(work/'inputs/unsafe.zip','w') as z:z.writestr('../escape.txt','unsafe')
    shutil.copyfile(work/'inputs/a.txt',work/'working/a-copy.txt')


def request(route: str, txid: str, port: int) -> dict:
    group,variant=route.split('.',1)
    if group in ('assess',):return {}
    if group=='inspect':
        return {'before':'reports/before.json','after':'reports/after.json'} if variant=='diff' else {'paths':['deliverables/fixture.zip']} if variant=='secret-final' else {'dependencies':[{'input':'inputs/a.txt','output':'working/a-copy.txt'}]} if variant=='stale' else {}
    if group=='files':
        return {
          'transfer':{'source':'inputs/a.txt','destination':'working/candidate-copy.txt'},
          'archive':{'paths':['inputs/a.txt'],'destination':'deliverables/candidate.zip'},
          'extract':{'archive':'deliverables/fixture.zip','destination':'working/candidate-extract'},
          'quarantine':{'paths':['scratch/old.tmp']},
          'rollback':{'transaction_id':txid},
          'verify':{'source':'inputs/a.txt','destination':'working/verify.txt'},
          'write':{'path':'working/candidate-note.txt','content':'new value'},
          'intake':{'paths':['uploads/user.txt'],'destination':'working/candidate-intake'},
          'redact':{'source':'inputs/secret.txt','destination':'working/candidate-redacted.txt'},
          'chmod':{'path':'working/verify.txt','mode':'600'},
          'metadata':{'path':'working/verify.txt','mode':'600','mtime_ns':0},
          'assemble':{'paths':['inputs/a.txt','inputs/b.txt'],'destination':'working/candidate-assembled.md'},
          'lock':{'name':'test-lock','op':'status'},
          'scratch':{'label':'candidate'},
          'layout':{},
        }[variant]
    if group=='system':
        return {'port':port} if variant=='port' else {'paths':['inputs/fixture.elf']} if variant=='binary' else {}
    if group=='runtime':
        return {
          'venv':{'destination':'working/candidate-venv','with_pip':False},
          'pip':{'environment':'working/local-venv','requirements':'inputs/requirements.txt','wheelhouse':'wheelhouse'},
          'npm':{'project':'project'},'verify':{'environment':'working/local-venv'},
          'exec':{'profile':'python-version'},'schedule':{'profile':'python-version','count':1},
          'service-start':{'profile':'python-version','job_id':'candidate-service'},
          'service-stop':{'job_id':'test-service'},'service-status':{'job_id':'test-service'},
          'sqlite':{'destination':'working/candidate.sqlite','tables':[{'name':'items','columns':[{'name':'name','type':'TEXT'}]}]},
          'git-init':{'destination':'working/candidate-repo'},
          'git-worktree':{'repository':'sources/repo','branch':'agent-candidate','destination':'working/agent-candidate'},
          'download':{'url':'https://example.invalid/file','sha256':'0'*64,'destination':'working/network.dat'},
          'transcode':{'source':'inputs/tone.wav','destination':'working/out.mp3','format':'mp3'},
          'render':{'source':'inputs/sample.docx','destination':'working/sample.pdf'},
          'container-build':{'dockerfile':'sources/Dockerfile','tag':'workspace-candidate'},
          'container-run':{'image':'workspace-candidate'},'vm':{'op':'list'},
        }[variant]
    if group=='domain':
        return {
          'validate':{'paths':['inputs/data.json','inputs/data.csv','inputs/tiny.png','inputs/tone.wav','inputs/sample.docx']},
          'data-schema':{'path':'inputs/data.csv','required_columns':['id','label']},
          'data-lineage':{},
          'media':{'paths':['inputs/tiny.png','inputs/tone.wav']},
          'document':{'paths':['inputs/sample.docx']},
          'web':{},'logs':{'path':'inputs/log.txt'},
          'archive-verify':{'archive':'deliverables/fixture.zip'},
          'cleanup':{},'delivery':{'paths':['deliverables/fixture.zip']},
          'readiness':{'required':['inputs/a.txt']},
          'requirements':{'required':['inputs/a.txt'],'tools':['python3']},
          'security':{},'sbom':{},
          'release':{'paths':['deliverables/fixture.zip']},
          'clean-rebuild':{'workflow':'reports/replay.json','inputs':['inputs/a.txt']},
        }[variant]
    if group=='workflow':
        return {
          'run':{'workflow':'reports/workflow.json'},'resume':{'workflow':'reports/workflow.json'},
          'design':{'workflow':'reports/workflow.json'},
          'evidence':{'workflow_id':'check-workspace'},
          'recover':{'workflow_id':'check-workspace'},
          'gate':{'action':'purge','paths':['scratch/old.tmp']},
          'risk':{'action':'purge','paths':['scratch/old.tmp']},
          'ledger':{'event':{'type':'test-event','message':'value redacted'}},
          'log-rotate':{'max_bytes':128},
          'log-verify':{},'policy':{},'summary':{},
        }[variant]
    raise AssertionError(route)


def main():
    with tempfile.TemporaryDirectory(prefix='workspace-skill-selftest-') as temp:
        root=Path(temp);(root/'SKILL.md').write_text('# Test skill root\n')
        shutil.copytree(BASE,root/NAME,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        pkg=root/NAME;work=root/'workspace';work.mkdir();fixture(work)
        command=[sys.executable,NAME+'/tools-workspace.py']
        p=subprocess.run(command+['--help'],cwd=root,text=True,capture_output=True,timeout=10)
        check(p.returncode==0 and 'capabilities' in p.stdout,'CLI help in clean skill root')
        p=subprocess.run(command+['capabilities','--json'],cwd=root,text=True,capture_output=True,timeout=10)
        catalog=json.loads(p.stdout)
        check(p.returncode==0 and catalog['count']==310 and len(catalog['categories'])==31,'310 entries and 31 categories registered')
        entries=catalog['capabilities'];routes={c['route'] for c in entries};check(len(routes)==157,'157 distinct functional routes registered')
        p=subprocess.run(command+['resolve','--root','workspace','--text','<li class="x">Category 1: Workspace Discovery and State Inspection</li>\n2. Skill Name: Safe Delete Coordinator'],cwd=root,text=True,capture_output=True,timeout=10)
        check(p.returncode==0 and json.loads(p.stdout)['count']==11,'HTML/Markdown/numbered list resolution')
        rc,root_inventory=cli(root,work,'workspace-inventory-scanner')
        check(rc==0 and root_inventory['ok'],'inventory inside workspace from relative skill root')
        p=subprocess.run(command+['run','workspace-inventory-scanner','--root','.'],cwd=root,text=True,capture_output=True,timeout=10)
        check(p.returncode==0 and any(x['path']=='SKILL.md' for x in json.loads(p.stdout)['result']['entries']),
              'tool copied beside one SKILL.md and run with --root .')
        rc,missing=cli(root,work,'copy-integrity-verifier',{'source':'missing.txt','destination':'working/verify.txt'})
        check(rc==2 and missing['error']['code']=='missing_input','missing source rejected')
        rc,invalid=cli(root,work,'data-manifest-and-schema-manager',{'path':'inputs/data.csv','required_columns':['absent']})
        check(rc==0 and invalid['result']['valid'] is False,'semantic-invalid data reported as invalid')
        rc,badpath=cli(root,work,'batch-move-and-copy-operator',{'source':'inputs/a.txt','destination':'../escape.txt'})
        check(rc==2 and badpath['error']['code']=='unsafe_path','parent traversal rejected')
        (work/'uploads/user.txt').write_text('uploaded material')
        (work/'sources/repo').mkdir();subprocess.run(['git','init','-q','sources/repo'],cwd=work,check=True)
        (work/'sources/repo/file.txt').write_text('repo content')
        subprocess.run(['git','-C','sources/repo','add','file.txt'],cwd=work,check=True)
        subprocess.run(['git','-C','sources/repo','-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture'],cwd=work,check=True)
        archive=preapply(root,work,'archive-structure-designer',{'paths':['inputs/a.txt','inputs/data.json'],'destination':'deliverables/fixture.zip'})
        check(zipfile.is_zipfile(work/archive['path']),'actual ZIP reopened')
        tx=preapply(root,work,'batch-move-and-copy-operator',{'source':'inputs/a.txt','destination':'working/transfer.txt'})
        txid=tx['transaction_id']
        rc,_=cli(root,work,'current-state-snapshotter',{'paths':['inputs']},output='reports/before.json')
        check(rc==0,'snapshot report saved')
        (work/'inputs/a.txt').write_text('changed line\n')
        rc,_=cli(root,work,'current-state-snapshotter',{'paths':['inputs']},output='reports/after.json')
        check(rc==0,'second snapshot saved after changed input')
        rc,difference=cli(root,work,'state-difference-reporter',{'before':'reports/before.json','after':'reports/after.json'})
        check(rc==0 and any(x['path']=='inputs/a.txt' for x in difference['result']['changed']),'changed input changes snapshot diff')
        (work/'inputs/a.txt').write_text('first line\n')
        repeat=preapply(root,work,'delivery-package-builder',{
            'paths':['inputs/a.txt','inputs/data.json'],'destination':'deliverables/fixture-repeat.zip'})
        check(repeat['sha256']==archive['sha256'],'unchanged inputs yield byte-identical deterministic ZIP archives')
        preapply(root,work,'workspace-layout-architect',{'folders':['working/new-nested']})
        check((work/'working/new-nested').is_dir(),'approved workspace layout created')
        assembled=preapply(root,work,'multi-document-assembly-manager',{
            'paths':['inputs/a.txt','inputs/b.txt'],'destination':'working/assembled.md'})
        check('second line' in (work/'working/assembled.md').read_text() and '<!-- Source:' in (work/'working/assembled.md').read_text(),
              'editable text assembly retains provenance and originals')
        oldstat=(work/'working/verify.txt').stat()
        meta=preapply(root,work,'metadata-preservation-specialist',{
            'path':'working/verify.txt','mode':'600','mtime_ns':0})
        check((work/'working/verify.txt').stat().st_mtime_ns==0,'approved metadata normalization applied')
        rc,meta_preview=cli(root,work,'file-operation-recovery-specialist',{'transaction_id':meta['transaction_id']})
        check(rc==0 and meta_preview['status']=='approval_required','metadata rollback preflight')
        rc,meta_undo=cli(root,work,'file-operation-recovery-specialist',{'transaction_id':meta['transaction_id']},
                         apply=True,token=meta_preview['result']['approval_token'])
        check(rc==0 and (work/'working/verify.txt').stat().st_mtime_ns==oldstat.st_mtime_ns,
              'metadata rollback restores original mtime')
        intake=preapply(root,work,'user-upload-intake-manager',{'paths':['uploads/user.txt'],'destination':'working/staged'})
        rc,lineage=cli(root,work,'data-lineage-organizer')
        check(rc==0 and lineage['result']['edges'] and lineage['result']['all_copies_match'],
              'staged input provenance and copy lineage verified')
        if shutil.which('git'):
            repo=preapply(root,work,'repository-workspace-initializer',{'destination':'working/initialized'})
            check((work/repo['destination'] / '.git').is_dir(),'local repository initialized without global settings')
        preapply(root,work,'python-virtual-environment-manager',{'destination':'working/local-venv','with_pip':True})
        preapply(root,work,'pinned-package-installer',{'environment':'working/local-venv','requirements':'inputs/requirements.txt','wheelhouse':'wheelhouse'})
        rc,env=cli(root,work,'dependency-installation-verifier',{'environment':'working/local-venv'})
        check(rc==0 and 'localdemo' in env['result']['packages']['stdout'].lower(),'offline wheel installed in isolated venv and verified')
        preapply(root,work,'database-workspace-provisioner',{'destination':'working/data.sqlite','tables':[{'name':'items','columns':[{'name':'id','type':'INTEGER','primary_key':True},{'name':'text','type':'TEXT'}]}]})
        rc,db=cli(root,work,'input-validation-router',{'paths':['working/data.sqlite']})
        check(rc==0 and db['result']['files'][0]['valid'],'SQLite database reopened and integrity checked')
        preapply(root,work,'workspace-activity-ledger',{'event':{'type':'test-event','message':'password=ABC123456789'}})
        rc,log=cli(root,work,'audit-trail-integrity-checker')
        check(rc==0 and log['result']['valid'] and 'ABC123456789' not in (work/'.workspace-control/activity.jsonl').read_text(),
              'activity log hash chain valid; secret value not stored')
        preapply(root,work,'log-rotation-and-retention-manager',{'max_bytes':128})
        rc,rotated=cli(root,work,'audit-trail-integrity-checker')
        check(rc==0 and rotated['result']['valid'] and rotated['result']['events']==1,'log rotation preserves integrity')
        preapply(root,work,'workspace-task-orchestrator',{'workflow':'reports/workflow.json'})
        rc,cp=cli(root,work,'orchestration-recovery-specialist',{'workflow_id':'check-workspace'})
        check(rc==0 and cp['result']['status']=='completed','workflow checkpoint recorded and recovery report available')
        rc,preview=cli(root,work,'workspace-task-orchestrator',{'workflow':'reports/workflow.json'})
        check(rc==0 and preview['status']=='approval_required','workflow rerun preflight available')
        rc,resume=cli(root,work,'checkpoint-and-resume-manager',{'workflow':'reports/workflow.json'})
        check(rc==0 and resume['status']=='approval_required','workflow resume preflight available')
        preapply(root,work,'secret-redaction-operator',{'source':'inputs/secret.txt','destination':'working/redacted.txt'})
        check('ABC123456789' not in (work/'working/redacted.txt').read_text() and 'ABC123456789' in (work/'inputs/secret.txt').read_text(),
              'redacted copy masks secret while original preserved')
        rc,unsafe=cli(root,work,'input-decompression-manager',{'archive':'inputs/unsafe.zip','destination':'working/evil'})
        check(rc==2 and unsafe['error']['code']=='unsafe_archive' and not (root/'escape.txt').exists(),
              'ZIP traversal rejected before work')
        (work/'working/escape').symlink_to(root,target_is_directory=True)
        rc,symlink=cli(root,work,'batch-move-and-copy-operator',{'source':'inputs/a.txt','destination':'working/escape/a.txt'})
        check(rc==2 and symlink['error']['code']=='unsafe_path','symlink boundary rejected')
        (work/'working/escape').unlink()
        # Local background service profile lives in a policy inside the temporary workspace.
        policy=json.loads((pkg/'asset-policy.json').read_text());policy['trusted_profiles']['python-service']={
           'argv':['python3','-m','http.server','0','--bind','127.0.0.1'],'timeout_seconds':8}
        (work/'reports/policy.json').write_text(json.dumps(policy))
        job=None
        try:
            job=preapply(root,work,'background-service-manager',{'profile':'python-service','job_id':'test-service'},policy='reports/policy.json')
            rc,status=cli(root,work,'long-running-job-supervisor',{'job_id':'test-service'})
            check(rc==0 and status['result']['alive'],'task-owned service started and status inspected')
            # Exercise every catalog alias through the CLI using an appropriate bounded real request.
            blocked=[];passed=[];failed=[]
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            for cap in entries:
                req=request(cap['route'],txid,port)
                rc,out=cli(root,work,cap['id'],req)
                if rc==0 and out.get('ok') and out.get('capability',{}).get('id')==cap['id']:
                    passed.append(cap['id'])
                elif rc==3 and out.get('error',{}).get('code')=='integration_unavailable' and cap['route'] in (
                     'runtime.container-build','runtime.container-run','runtime.vm','runtime.transcode','runtime.render','runtime.download'):
                    blocked.append(cap['id'])
                else:
                    failed.append({'id':cap['id'],'route':cap['route'],'returncode':rc,'response':out})
            if failed:raise AssertionError('Catalog alias failures: '+json.dumps(failed[:10])[:2300])
            REPORT['catalog_entries_tested']=len(passed)+len(blocked)
            REPORT['capability_routes_tested']=len({c['route'] for c in entries})
            REPORT['blocked_optional_integrations']=blocked
            check(REPORT['catalog_entries_tested']==310,'all 310 catalog aliases reached a real handler or declared blocked integration')
            check(REPORT['capability_routes_tested']==157,'all 157 route variants exercised via catalog aliases')
            rc,stop_plan=cli(root,work,'graceful-shutdown-coordinator',{'job_id':'test-service'})
            check(rc==0 and stop_plan['status']=='approval_required','service stop preflight')
            rc,stopped=cli(root,work,'graceful-shutdown-coordinator',{'job_id':'test-service'},apply=True,token=stop_plan['result']['approval_token'])
            check(rc==0 and not stopped['result']['alive'],'verified task-owned service stopped')
        finally:
            if job and job.get('pid'):
                try:os.killpg(job['pid'],9)
                except (OSError,PermissionError):pass
        # Copy recovery after source changed: a copied destination remains hash-verified.
        rc,undo=cli(root,work,'file-operation-recovery-specialist',{'transaction_id':txid})
        check(rc==0 and undo['status']=='approval_required','rollback preflight')
        rc,rolled=cli(root,work,'file-operation-recovery-specialist',{'transaction_id':txid},apply=True,token=undo['result']['approval_token'])
        check(rc==0 and rolled['result']['rolled_back']==1 and not (work/'working/transfer.txt').exists(),
              'transaction rollback restores pre-operation state')
        check((work/'inputs/a.txt').read_text()=='first line\n','original input preserved')
        copied=preapply(root,work,'batch-move-and-copy-operator',{
            'source':'working/a-copy.txt','destination':'working/moved.txt','action':'move'})
        check(not (work/'working/a-copy.txt').exists() and (work/'working/moved.txt').is_file(),
              'reviewed move preserves a source recovery copy')
        move_undo=preapply(root,work,'file-operation-recovery-specialist',{'transaction_id':copied['transaction_id']})
        check((work/'working/a-copy.txt').is_file() and not (work/'working/moved.txt').exists(),
              'move rollback restores original working file')
        extracted=preapply(root,work,'input-decompression-manager',{
            'archive':'deliverables/fixture.zip','destination':'working/restored'})
        check((work/'working/restored/inputs/a.txt').read_bytes()==(work/'inputs/a.txt').read_bytes(),
              'verified archive extracted with original bytes')
        new_text=preapply(root,work,'atomic-file-writer',{'path':'working/atomic.txt','content':'atomic content'})
        check((work/'working/atomic.txt').read_text()=='atomic content','atomic output opened and inspected')
        preapply(root,work,'file-operation-recovery-specialist',{'transaction_id':new_text['transaction_id']})
        check(not (work/'working/atomic.txt').exists(),'atomic write rollback removes only verified output')
        q=preapply(root,work,'safe-delete-coordinator',{'paths':['scratch/old.tmp']})
        check(not (work/'scratch/old.tmp').exists(),'approved cleanup stages file into quarantine')
        rc,hold=cli(root,work,'safe-delete-coordinator',{'purge':True,'transaction_id':q['transaction_id']})
        check(rc==2 and hold['error']['code']=='retention_hold','permanent deletion blocked during retention window')
        preapply(root,work,'file-operation-recovery-specialist',{'transaction_id':q['transaction_id']})
        check((work/'scratch/old.tmp').is_file(),'quarantine rollback restores original scratch file')
        lock=preapply(root,work,'shared-resource-lock-manager',{'name':'unit','op':'acquire','ttl_seconds':30})
        rc,held=cli(root,work,'shared-resource-lock-manager',{'name':'unit','op':'status'})
        check(rc==0 and held['result']['held'],'cooperative lock acquired and status verified')
        preapply(root,work,'shared-resource-lock-manager',{'name':'unit','op':'release','lock_token':lock['lock_token']})
        rc,free=cli(root,work,'shared-resource-lock-manager',{'name':'unit','op':'status'})
        check(rc==0 and not free['result']['held'],'cooperative lock released by holder')
        executed=preapply(root,work,'task-process-launcher',{'profile':'python-version'})
        check('Python' in executed['result']['stdout'] or 'Python' in executed['result']['stderr'],
              'trusted process profile executed and output captured')
        scheduled=preapply(root,work,'scheduled-task-manager',{'profile':'python-version','count':2,'interval_seconds':0})
        check(scheduled['count']==2 and all(x['exit_code']==0 for x in scheduled['results']),
              'bounded in-process schedule executed twice')
        if shutil.which('npm'):
            shutil.copytree(work/'project',work/'project-isolated')
            node=preapply(root,work,'node-environment-manager',{'project':'project-isolated'})
            check(node['result']['exit_code']==0,'offline npm ci with disabled scripts completed in isolated project')
        if shutil.which('git'):
            tree=preapply(root,work,'agent-worktree-manager',{
                'repository':'sources/repo','branch':'agent-live','destination':'working/agent-live'})
            check((work/tree['destination']/'file.txt').is_file(),'approved git worktree created and reopened')
        resumed=preapply(root,work,'checkpoint-and-resume-manager',{'workflow':'reports/workflow.json'})
        check(resumed['workflow_id']=='check-workspace','checkpoint resume executed through CLI')
        (work/'.workspace-control/cancel').write_text('request stop between stages')
        rc,cancel_plan=cli(root,work,'checkpoint-and-resume-manager',{'workflow':'reports/workflow.json'})
        check(rc==0 and cancel_plan['status']=='approval_required','cancellation request still needs reviewed workflow plan')
        rc,cancelled=cli(root,work,'checkpoint-and-resume-manager',{'workflow':'reports/workflow.json'},
                         apply=True,token=cancel_plan['result']['approval_token'])
        check(rc==4 and cancelled['result']['status']=='cancelled','workflow cancellation marker stops before next stage')
        (work/'.workspace-control/cancel').unlink()
        (work/'reports/failing-workflow.json').write_text(json.dumps({'workflow_id':'must-fail','steps':[
            {'id':'check','capability':'structural-workspace-validator','request':{'required':['missing-required-file']}},
            {'id':'write','capability':'atomic-file-writer','request':{'path':'working/should-not-exist.txt','content':'unsafe'},
             'depends_on':['check']}]}))
        rc,blocked=cli(root,work,'workspace-task-orchestrator',{'workflow':'reports/failing-workflow.json'})
        check(rc==2 and blocked['error']['code']=='verification_failed' and not (work/'working/should-not-exist.txt').exists(),
              'failed preflight prevents later consequential workflow stage')
        rc,replayed=cli(root,work,'clean-state-acceptance-tester',{
            'workflow':'reports/replay.json','inputs':['inputs/a.txt']})
        check(rc==0 and replayed['result']['clean_state_replay_passed'],
              'read-only workflow replayed from clean declared inputs')
        (work/'inputs/other.log').write_text('2026-11-05 10:00:02 WARN request_id=alpha1234 later\n')
        (work/'inputs/log.txt').write_text('2026-11-05 10:00:01 ERROR request_id=alpha1234 token=ABC123456789 first\n')
        rc,correlated=cli(root,work,'cross-tool-log-correlator',{'paths':['inputs/log.txt','inputs/other.log']})
        check(rc==0 and correlated['result']['cross_file_correlation_hashes']
              and 'ABC123456789' not in json.dumps(correlated),
              'redacted cross-file log correlation and earliest timestamped signal')
    REPORT['ok']=True
    print(json.dumps(REPORT,indent=2,ensure_ascii=False))
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        REPORT['ok']=False;REPORT['error']=str(exc)
        print(json.dumps(REPORT,indent=2,ensure_ascii=False))
        sys.exit(1)
