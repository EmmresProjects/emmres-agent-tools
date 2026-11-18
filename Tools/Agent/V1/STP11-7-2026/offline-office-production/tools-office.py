#!/usr/bin/env python3
"""Primary offline office CLI. No network or automatic installation."""
import argparse
import json
from pathlib import Path
import signal
import sys
import time

sys.dont_write_bytecode=True
import importlib.util
spec=importlib.util.spec_from_file_location('office_common',Path(__file__).with_name('tools-common.py'))
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
native=c.load('native');pdf=c.load('pdf');work=c.load('workflows')

CAPABILITIES={
 'create':'Author editable DOCX, PPTX, XLSX or final Latin-text PDF from structured JSON.',
 'extract':'Extract body text or cell/formula values without executing document content.',
 'inspect':'Bounded OOXML/PDF structural, review, active-content and basic accessibility heuristics.',
 'pdf':'Merge/select/reorder/rotate pages, render PNG, create/fill text forms, AES-256 encrypt/decrypt, clear metadata, redact searchable text.',
 'compare':'Unified extracted-text diff across supported formats.',
 'mail-merge':'DOCX {{field}} template merge over supplied records.',
 'parse-catalog':'Normalize plain text, Markdown, copied HTML, PDF or DOCX capability descriptions.',
 'assess':'Assess readiness and gaps for any of the 480 source catalog entries; never claims full implementation.',
 'baseline':'Record local runtime/package versions and engine availability.',
 'adapter':'Bounded local LibreOffice-to-PDF or Tesseract image-to-text adapter; requires local engine and approval.',
 'records':'Backup, safe restore, CRC validation and SHA-256 provenance.',
 'gate':'Fail-closed release gate driven by explicit external evidence and structural findings.',
 'batch':'Sequential bounded manifest-driven operations; stop on first failure with recovery cursor.'
}

def catalog():return json.loads(Path(__file__).with_name('asset-capability-catalog.json').read_text(encoding='utf-8'))

def assess(d):
    rows=catalog();ids=d.get('ids',[])
    if not isinstance(ids,list) or any(not isinstance(i,str) for i in ids):c.fail('invalid_input','ids must be an array of catalog IDs.')
    if ids:
        if set(ids)-{r['id'] for r in rows}:c.fail('invalid_input','Unknown catalog ID.')
        rows=[r for r in rows if r['id'] in ids]
    env=work.baseline({},c)
    return {'entries':[dict(r,status='partial-foundation-only',release='blocked',limitation='The named catalog skill is not implemented in full. Use the tested operations only for their documented scopes; specialist execution/validation still required.') for r in rows], 'operations':CAPABILITIES,'environment':env,'requires_external_validation':['Microsoft Office and LibreOffice reopening/round-trip tests','Assistive technology and PDF/UA testing','PDF/A and PDF/X specialist preflight','Digital signing, trust-chain and revocation validation','Formula recalculation, pivot tables and data models','Printer/device tests and licensed OCR/handwriting models','OS-enforced sandbox and deployment/rollback approval']}

def gate(d):
    policy=json.loads(Path(__file__).with_name('asset-release-policy.json').read_text())
    evidence=c.require(d,'evidence',dict)
    checks=policy['required_checks'];missing=[name for name in checks if evidence.get(name) is not True]
    result=native.inspect({'source':c.require(d,'source')},c)
    if result['findings']:missing.append('structural_findings_resolved')
    return {'released':not missing,'failed_checks':missing,'inspection':result,'warning':'External evidence is operator-attested, not independently verified; no standards certification is issued.'}

def execute(op,d,depth=0):
    c.check()
    if not isinstance(d,dict):c.fail('invalid_input','Request must be a JSON object.')
    if op=='create':return native.create(d,c)
    if op=='extract':return native.extract(d,c)
    if op=='inspect':return native.inspect(d,c)
    if op=='pdf':return pdf.run(d,c)
    if op=='compare':return work.compare(d,c,native)
    if op=='mail-merge':return work.mail_merge(d,c)
    if op=='parse-catalog':return work.catalog_parse(d,c)
    if op=='baseline':return work.baseline(d,c)
    if op=='assess':return assess(d)
    if op=='adapter':return work.adapter(d,c)
    if op=='records':return work.records(d,c)
    if op=='gate':return gate(d)
    if op=='batch':
        if depth:c.fail('invalid_input','Nested batches prohibited.')
        jobs=c.bounded(c.require(d,'jobs',list));start=d.get('resume_from',0)
        if len(jobs)>100 or not isinstance(start,int) or not 0<=start<=len(jobs):c.fail('limit','Batch limit is 100 jobs; resume cursor must be valid.')
        # Validate operation names/requests before performing any job.
        for job in jobs:
            if not isinstance(job,dict) or job.get('operation') not in CAPABILITIES or job.get('operation')=='batch' or not isinstance(job.get('request'),dict):c.fail('invalid_input','Each batch job requires operation and request; no nested batch.')
        results=[]
        for i in range(start,len(jobs)):
            try:result=execute(jobs[i]['operation'],jobs[i]['request'],depth+1)
            except Exception as e:
                return {'completed':False,'results':results,'resume_from':i,'error':{'code':getattr(e,'code','job_failed'),'message':str(e)},'recovery':'Correct the failing request, set resume_from to this cursor, then rerun; completed jobs are not rolled back.'}
            results.append({'index':i,'result':result})
        return {'completed':True,'results':results,'resume_from':len(jobs)}
    c.fail('unsupported','Unknown operation.')

class Parser(argparse.ArgumentParser):
    def error(self,message):c.fail('arguments',message)

def main():
    p=Parser(description=__doc__,epilog='Run from the future skill root. All user paths are relative to the current working directory. Use run OP --input request.json; outputs are structured JSON.')
    sub=p.add_subparsers(dest='command',required=True,parser_class=Parser)
    sub.add_parser('capabilities',help='List tested operation interfaces, not a claim of all catalog skills.')
    cat=sub.add_parser('catalog',help='Show complete source catalog.');cat.add_argument('--id');cat.add_argument('--category')
    run=sub.add_parser('run',help='Execute a configured operation.');run.add_argument('operation',choices=CAPABILITIES)
    run.add_argument('--input',help='Relative JSON request path (required except baseline/assess).')
    run.add_argument('--output',help='Optional structured report path; stdout always receives JSON.')
    run.add_argument('--approve',action='store_true',help='Operator approval for the actions described in the request, including overwrite.')
    run.add_argument('--timeout',type=int,default=120,help='Overall time budget, 1–300 seconds (POSIX alarm + cooperative checks).')
    args=p.parse_args()
    if args.command=='capabilities':return {'operations':CAPABILITIES,'catalog_count':len(catalog()),'catalog_implementation':'partial; assess reports unmet requirements'}
    if args.command=='catalog':
        rows=catalog()
        if args.id:rows=[r for r in rows if r['id']==args.id]
        if args.category:rows=[r for r in rows if args.category.casefold() in r['category'].casefold()]
        if not rows:c.fail('not_found','No matching capability.')
        return {'entries':rows,'count':len(rows)}
    if not 1<=args.timeout<=300:c.fail('limit','Overall timeout must be 1–300 seconds.')
    c.APPROVED=args.approve;c.DEADLINE=time.monotonic()+args.timeout
    if hasattr(signal,'SIGALRM'):
        def alarm(*_):c.fail('timeout','Overall time budget exceeded. Check committed artifacts before resuming.')
        signal.signal(signal.SIGALRM,alarm);signal.alarm(args.timeout)
    try:
        if not args.input and args.operation not in ('baseline','assess'):c.fail('missing_input','--input is required for this operation.')
        d=c.read_json(args.input) if args.input else {}
        if args.output:
            report=c.path(args.output)
            if report.exists():c.need_approval('Report output already exists.')
            if args.input and report==c.path(args.input):c.fail('path','Report output must not replace request input.')
        result=execute(args.operation,d)
        payload={'ok':not (args.operation=='batch' and not result['completed']),'operation':args.operation,'result':result}
        if args.output:c.json_write(args.output,payload)
        return payload
    finally:
        if hasattr(signal,'SIGALRM'):signal.alarm(0)

if __name__=='__main__':
    try:
        output=main();print(json.dumps(output,ensure_ascii=False,indent=2,default=str));sys.exit(0 if output.get('ok',True) else 1)
    except KeyboardInterrupt:
        print(json.dumps({'ok':False,'error':{'code':'cancelled','message':'Cancelled; committed batch outputs remain. Temporary artifact files are removed.'}}));sys.exit(130)
    except Exception as e:
        code=getattr(e,'code','operation_failed')
        message=getattr(e,'message',str(e))
        if isinstance(e,ModuleNotFoundError):code='dependency_missing';message='Install the documented offline dependencies: '+str(e.name)
        if 'password' in message.lower() and not hasattr(e,'code'):message='PDF password or encryption operation failed.'
        print(json.dumps({'ok':False,'error':{'code':code,'message':message}},ensure_ascii=False));sys.exit(2)
