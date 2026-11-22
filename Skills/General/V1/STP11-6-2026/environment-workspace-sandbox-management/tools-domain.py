"""Bounded semantic and structural checks for data, media, office, web, archives, releases and logs."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sqlite3
import struct
import wave
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path
from workspace_core import Context, ToolError, _PRIVATE_RE, _SECRET_RE, digest, file_sha, load_json, redact, string, strings
from workspace_files import _check_zip
from workspace_inspect import _dependency_refs, _secrets


def _check_format(ctx: Context, name: str) -> dict:
    p=ctx.path(name,must_exist=True)
    if not p.is_file():
        return {'path':name,'valid':False,'format':'not-regular-file','findings':['Expected regular file']}
    limit=ctx.limits['max_transfer_bytes'];size=p.stat().st_size
    if size>limit:return {'path':name,'valid':False,'format':'oversized','findings':['Exceeds max_transfer_bytes']}
    ext=p.suffix.lower();findings=[];meta={}
    try:
        with p.open('rb') as f:head=f.read(32)
        if ext in ('.json','.jsonl'):
            text=ctx.read_text(name)
            if ext=='.json':
                data=json.loads(text);meta['top_level_type']=type(data).__name__
            else:
                count=0
                for line in text.splitlines():
                    if line.strip():json.loads(line);count+=1
                meta['records']=count
        elif ext in ('.csv','.tsv'):
            text=ctx.read_text(name)
            sample=list(csv.reader(io.StringIO(text),delimiter='\t' if ext=='.tsv' else ','))
            if not sample:findings.append('Empty delimited file')
            else:
                meta['header']=sample[0];meta['rows']=len(sample)-1
                if len(set(sample[0]))!=len(sample[0]):findings.append('Duplicate column names')
                if any(len(r)!=len(sample[0]) for r in sample[1:]):findings.append('Inconsistent row width')
        elif ext in ('.xml','.svg'):
            root=ET.fromstring(ctx.read_text(name));meta['root_tag']=root.tag
        elif ext in ('.zip','.docx','.pptx','.xlsx','.odt','.ods','.odp'):
            arc,members,manifest=_check_zip(ctx,name)
            meta['members']=len(members);meta['manifest']=bool(manifest)
            required={'.docx':'word/document.xml','.pptx':'ppt/presentation.xml','.xlsx':'xl/workbook.xml'}
            if ext in required and required[ext] not in {m['name'] for m in members}:
                findings.append('OOXML main part missing: '+required[ext])
        elif ext=='.pdf':
            if not head.startswith(b'%PDF-'):findings.append('PDF header missing')
            with p.open('rb') as f:
                f.seek(max(0,size-2048));tail=f.read()
            if b'%%EOF' not in tail:findings.append('PDF end marker missing')
            meta['rendered']=False
        elif ext=='.png':
            if not head.startswith(b'\x89PNG\r\n\x1a\n') or len(head)<24:
                findings.append('PNG signature/IHDR missing')
            else:
                meta['width'],meta['height']=struct.unpack('>II',head[16:24])
                if not 0<meta['width']<=100000 or not 0<meta['height']<=100000:findings.append('Implausible PNG dimensions')
        elif ext in ('.jpg','.jpeg'):
            if not head.startswith(b'\xff\xd8\xff'):findings.append('JPEG header missing')
        elif ext=='.wav':
            with wave.open(str(p),'rb') as f:
                meta.update(channels=f.getnchannels(),sample_rate=f.getframerate(),frames=f.getnframes())
        elif ext in ('.sqlite','.db'):
            if not head.startswith(b'SQLite format 3\x00'):findings.append('SQLite header missing')
            else:
                with sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True,timeout=3) as conn:
                    if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':findings.append('SQLite integrity check failed')
        elif ext in ('.txt','.md','.html','.css','.py','.js','.ts','.toml','.yaml','.yml','.sh'):
            meta['utf8_characters']=len(ctx.read_text(name))
        else:
            meta['inspection']='signature-only';meta['magic_hex']=head[:8].hex()
    except (ToolError,OSError,ValueError,UnicodeError,RuntimeError,ET.ParseError,zipfile.BadZipFile,sqlite3.Error) as exc:
        findings.append(redact(str(exc))[:240])
    return {'path':name,'valid':not findings,'format':ext or 'unknown','bytes':size,'sha256':file_sha(p,limit),
            'findings':findings,'metadata':meta,'note':'Structural parse/signature only; rendering and full semantic correctness need separate checks.'}


def _validate(ctx: Context) -> dict:
    names=strings(ctx.request.get('paths',[]),'paths',maximum=1000)
    required=strings(ctx.request.get('required',[]),'required',maximum=1000)
    if not names and not required:
        raise ToolError('invalid_input','Provide paths and/or required paths for validation')
    results=[];missing=[]
    for name in required:
        p=ctx.path(name)
        if not p.exists():missing.append(name)
    for name in names:
        results.append(_check_format(ctx,name))
    return {'valid':not missing and all(x['valid'] for x in results),'missing_required':missing,
            'files':results,'checked_count':len(results)}


def _data(ctx: Context) -> dict:
    name=string(ctx.request.get('path'),'path')
    p=ctx.path(name,must_exist=True)
    if p.suffix.lower() not in ('.csv','.tsv','.json'):
        raise ToolError('invalid_input','Data schema check supports CSV, TSV and JSON records')
    text=ctx.read_text(name)
    if p.suffix.lower()=='.json':
        data=json.loads(text)
        if not isinstance(data,list) or len(data)>100000 or not all(isinstance(x,dict) for x in data):
            raise ToolError('invalid_input','JSON data must be a bounded list of objects')
        records=data
    else:
        records=list(csv.DictReader(io.StringIO(text),delimiter='\t' if p.suffix.lower()=='.tsv' else ','))
        if len(records)>100000:raise ToolError('limit_exceeded','Too many data records')
    required=strings(ctx.request.get('required_columns',[]),'required_columns',maximum=100)
    actual=sorted({str(k) for row in records for k in row})
    missing=sorted(set(required)-set(actual));nulls={k:sum(r.get(k) in ('',None) for r in records) for k in actual}
    return {'path':name,'rows':len(records),'columns':actual,'missing_required_columns':missing,'null_counts':nulls,
            'valid':not missing,'sha256':file_sha(p),
            'note':'Checks shape, required columns and nulls only; value ranges and regulatory obligations need declared rules.'}


def _data_lineage(ctx: Context) -> dict:
    paths = strings(ctx.request.get('paths', []), 'paths', maximum=100)
    edges = []; skipped = []
    if ctx.state.is_dir():
        for record in sorted(ctx.state.glob('intake-*.json'))[:1000]:
            if record.is_symlink():
                skipped.append(record.name); continue
            data = load_json(record, ctx.limits['max_input_bytes'])
            for item in data.get('items', []):
                if not isinstance(item, dict) or 'source' not in item or 'destination' not in item:
                    skipped.append(record.name); continue
                if paths and not any(item[key] in paths for key in ('source', 'destination')):
                    continue
                source = ctx.path(item['source']); destination = ctx.path(item['destination'])
                source_ok = source.is_file() and file_sha(source) == item['sha256']
                dest_ok = destination.is_file() and file_sha(destination) == item['sha256']
                edges.append({'source': item['source'], 'derived_copy': item['destination'],
                              'sha256': item['sha256'], 'original_matches': source_ok,
                              'copy_matches': dest_ok, 'record': ctx.relative(record)})
                if len(edges) >= ctx.limits['max_entries']:
                    raise ToolError('limit_exceeded', 'Intake lineage exceeds entry limit')
    return {'edges': edges, 'unverified_records': skipped, 'all_copies_match': all(x['copy_matches'] for x in edges),
            'note': 'Only this tool\'s recorded intake copies are proven; downstream transformations require declared lineage.'}


def _media(ctx: Context) -> dict:
    names=strings(ctx.request.get('paths',[]),'paths',maximum=100)
    if not names:raise ToolError('invalid_input','Provide explicit media paths')
    formats=[_check_format(ctx,n) for n in names]
    return {'valid':all(x['valid'] for x in formats),'files':formats,
            'rendered_or_listened':False,'note':'No perceptual QA is claimed; optional ffmpeg adapter can transcode if installed and approved.'}


def _document(ctx: Context) -> dict:
    names=strings(ctx.request.get('paths',[]),'paths',maximum=100)
    if not names:raise ToolError('invalid_input','Provide explicit document paths')
    items=[]
    for name in names:
        check=_check_format(ctx,name)
        links=[];parts=[]
        p=ctx.path(name,must_exist=True)
        if p.suffix.lower() in ('.docx','.pptx','.xlsx') and check['valid']:
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if n.endswith('.rels') and z.getinfo(n).file_size<=ctx.limits['max_text_bytes']:
                        try:
                            root=ET.fromstring(z.read(n))
                            for rel in root:
                                if rel.attrib.get('TargetMode')=='External':
                                    links.append({'part':n,'target_scheme':rel.attrib.get('Target','').split(':',1)[0]})
                        except ET.ParseError:
                            check['valid']=False;check['findings'].append('Malformed relationships XML')
                parts=[n for n in z.namelist() if n.endswith('.xml')][:50]
        items.append({'file':check,'external_links':links,'xml_parts_sample':parts})
    return {'valid':all(x['file']['valid'] for x in items),'documents':items,
            'note':'Editable package structure and external relationships checked; visual layout, formulas, fonts and slides are not rendered here.'}


def _web(ctx: Context) -> dict:
    rows,_=ctx.entries(ctx.request_paths())
    refs=_dependency_refs(ctx,rows)
    html=[r for r in rows if r['kind']=='file' and Path(r['path']).suffix.lower() in ('.html','.htm')]
    packages=[r['path'] for r in rows if r['path'].endswith(('package.json','package-lock.json'))]
    return {'html_files':[x['path'] for x in html],'package_manifests':packages,
            'references':refs,'valid_static_references':refs['missing']==0,
            'note':'No server, browser, API or deployment was started; use approved runtime profile for an executable smoke test.'}


def _logs(ctx: Context) -> dict:
    names=strings(ctx.request.get('paths', [ctx.request['path']] if 'path' in ctx.request else []), 'paths', maximum=20)
    if not names:raise ToolError('invalid_input','Provide path or paths (up to 20 UTF-8 logs)')
    matches=[];counts=Counter();correlations={};total_bytes=0
    for name in names:
        path=ctx.path(name,must_exist=True)
        total_bytes+=path.stat().st_size
        if total_bytes>8*ctx.limits['max_text_bytes']:
            raise ToolError('limit_exceeded','Combined log inputs exceed analysis budget')
        text=ctx.read_text(name)
        for n,line in enumerate(text.splitlines(),1):
            sev=next((x for x in ('FATAL','ERROR','WARN','INFO','DEBUG') if re.search(r'\b'+x+r'\b',line,re.I)),'OTHER')
            counts[sev]+=1
            timestamp=re.search(r'\b20\d\d-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d+)?',line)
            ident=re.search(r'(?i)\b(?:correlation|trace|request)[_-]?id\s*[:=]\s*([a-z0-9._-]{4,64})',line)
            event={'path':name,'line':n,'severity':sev,'timestamp':timestamp.group(0) if timestamp else None}
            if ident:
                event['correlation_hash']=digest(ident.group(1))[:16]
                correlations.setdefault(event['correlation_hash'],set()).add(name)
            if sev in ('FATAL','ERROR','WARN') and len(matches)<200:
                matches.append({**event,'sample':redact(line)[:180]})
    chronological=sorted((x for x in matches if x['timestamp']),key=lambda x:(x['timestamp'],x['path'],x['line']))
    return {'paths':names,'lines':sum(counts.values()),'severity_counts':dict(counts),'signals':matches,
            'earliest_timestamped_signal':chronological[0] if chronological else None,
            'cross_file_correlation_hashes':{k:sorted(v) for k,v in correlations.items() if len(v)>1},
            'note':'Order is based on reported timestamps; clock skew and incomplete logs may hide the actual first failure.'}


def _archive_verify(ctx: Context) -> dict:
    name=string(ctx.request.get('archive'),'archive')
    p,members,manifest=_check_zip(ctx,name)
    checks=[]
    if manifest:
        expected={r['path']:r['sha256'] for r in manifest['members']}
        with zipfile.ZipFile(p) as z:
            for member,sha in expected.items():
                if member not in z.namelist():
                    checks.append({'member':member,'valid':False,'reason':'missing'});continue
                import hashlib
                h=hashlib.sha256()
                with z.open(member) as stream:
                    while data:=stream.read(1024*1024):h.update(data)
                checks.append({'member':member,'valid':h.hexdigest()==sha})
    return {'archive':name,'zip_crc_valid':True,'manifest_present':bool(manifest),
            'manifest_hashes_valid':all(x['valid'] for x in checks) if manifest else None,
            'manifest_checks':checks,'members':len(members),'sha256':file_sha(p),
            'note':'Archive is opened and CRC-checked; restoration still needs files.extract and recipient workflow validation.'}


def _cleanup(ctx: Context) -> dict:
    rows,_=ctx.entries(ctx.request_paths())
    leftovers=[r['path'] for r in rows if r['kind']=='file' and r['path'].split('/')[0] in ctx.policy['cleanup_prefixes']]
    finals=[r['path'] for r in rows if r['kind']=='file' and r['path'].split('/')[0] in ('deliverables','final')]
    findings=_secrets(ctx,[r for r in rows if r['path'] in finals])
    return {'final_artifacts':finals,'temporary_files_remaining':leftovers,'final_secret_findings':findings['findings'],
            'clean':not leftovers and not findings['findings'],
            'note':'Active jobs are inspected separately; temporary files may be intentionally retained for recovery.'}


def _delivery(ctx: Context) -> dict:
    names=strings(ctx.request.get('paths',[]),'paths',maximum=500)
    if not names:raise ToolError('invalid_input','Provide explicit deliverable paths')
    checked=[_check_format(ctx,n) for n in names]
    secrets=_secrets(ctx,[{'path':c['path'],'kind':'file','size':c['bytes']} for c in checked if c.get('bytes') is not None])
    return {'valid':all(c['valid'] for c in checked) and not secrets['findings'],
            'files':checked,'secret_findings':secrets['findings'],
            'note':'Checks requested paths and supported signatures, not visual design or recipient access permissions.'}


def _readiness(ctx: Context) -> dict:
    required=strings(ctx.request.get('required',[]),'required',maximum=100)
    missing=[n for n in required if not ctx.path(n).exists()]
    disk=shutil.disk_usage(ctx.root)
    return {'ready':not missing and disk.free>=100*1024*1024,
            'missing_required':missing,'disk_free_bytes':disk.free,'root_writable':os.access(ctx.root,os.W_OK),
            'note':'External services and credentials cannot be declared ready without independent checks.'}


def _requirements(ctx: Context) -> dict:
    required=strings(ctx.request.get('required',[]),'required',maximum=200)
    tools=strings(ctx.request.get('tools',[]),'tools',maximum=100)
    if not required and not tools:raise ToolError('invalid_input','Specify required file paths and/or executable names')
    files=[{'path':name,'present':ctx.path(name).exists()} for name in required]
    binaries=[{'name':name,'present':bool(shutil.which(name))} for name in tools]
    return {'covered':all(x['present'] for x in files+binaries),'files':files,'tools':binaries,
            'note':'Coverage is limited to the declared requirements; content and semantic checks are separate.'}


def _security(ctx: Context) -> dict:
    rows,_=ctx.entries(ctx.request_paths())
    secrets=_secrets(ctx,rows)
    suspicious=[]
    for r in rows:
        if r['kind']=='symlink':suspicious.append({'path':r['path'],'issue':'symlink: review target'})
        if r['kind']=='file' and int(r['mode'],8)&0o002:suspicious.append({'path':r['path'],'issue':'world-writable file'})
        if r['kind']=='file' and Path(r['path']).suffix.lower() in ('.sh','.py','.js') and r['size']<=ctx.limits['max_text_bytes']:
            try:
                t=ctx.read_text(r['path'])
                if re.search(r'\b(curl|wget)\b.+\|\s*(bash|sh)\b|\brm\s+-rf\b|\beval\s*\(',t,re.I):
                    suspicious.append({'path':r['path'],'issue':'review potentially consequential script command'})
            except ToolError:pass
    return {'secret_findings':secrets['findings'],'suspicious':suspicious,
            'secure_by_static_checks':not secrets['findings'] and not suspicious,
            'note':'Heuristic only; not a vulnerability scan, antivirus, sandbox or signature verification.'}


def _sbom(ctx: Context) -> dict:
    import importlib.metadata
    from workspace_system import _requirements
    packages=[]
    for d in sorted(importlib.metadata.distributions(),key=lambda x:(x.metadata.get('Name') or '').lower())[:500]:
        packages.append({'name':d.metadata.get('Name'),'version':d.version,'license':(d.metadata.get('License') or '')[:100]})
    manifests=_requirements(ctx)
    return {'current_python_environment':packages,'total_reported':len(packages),
            'workspace_manifests':manifests['manifests'],'manifest_findings':manifests['findings'],
            'note':'Current Python runtime plus declared local manifests; a project venv, npm tree, container image and provenance need separate inventory.'}


def _release(ctx: Context) -> dict:
    names=strings(ctx.request.get('paths',[]),'paths',maximum=500)
    if not names:raise ToolError('invalid_input','Provide exact release artifact paths')
    delivery=_delivery(ctx)
    rows,_=ctx.entries(names)
    secret=_secrets(ctx,rows)
    insecure=[r['path'] for r in rows if r['kind']=='symlink' or (r['kind']=='file' and int(r['mode'],8)&0o002)]
    return {'ready':delivery['valid'] and not insecure and not secret['findings'],
            'artifacts':delivery['files'],'secret_findings':secret['findings'],'unsafe_paths_or_permissions':insecure,
            'note':'Only local structural and heuristic checks; release may still need license, signature, visual, accessibility and integration review.'}


def _clean_rebuild(ctx: Context) -> dict:
    # An isolated replay of read-only declarative steps; no source writes or arbitrary code.
    workflow_name=string(ctx.request.get('workflow'),'workflow')
    declared=strings(ctx.request.get('inputs',[]),'inputs',maximum=200)
    workflow=load_json(ctx.path(workflow_name,must_exist=True),ctx.limits['max_input_bytes'])
    steps=workflow.get('steps') if isinstance(workflow,dict) else None
    if not isinstance(steps,list) or not steps or len(steps)>ctx.limits['max_workflow_steps']:
        raise ToolError('invalid_input','Clean-state workflow needs bounded steps')
    from workspace_workflow import replay_readonly
    return replay_readonly(ctx,workflow,declared)


def run(ctx: Context, variant: str) -> dict:
    handlers={'validate':_validate,'data-schema':_data,'data-lineage':_data_lineage,
              'media':_media,'document':_document,'web':_web,
              'logs':_logs,'archive-verify':_archive_verify,'cleanup':_cleanup,'delivery':_delivery,
              'readiness':_readiness,'requirements':_requirements,'security':_security,'sbom':_sbom,
              'release':_release,'clean-rebuild':_clean_rebuild}
    if variant not in handlers:raise ToolError('internal_error','Unknown domain route',variant=variant)
    return handlers[variant](ctx)
