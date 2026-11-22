#!/usr/bin/env python3
"""Offline Audio Suite: bounded, local-first audio production and delivery CLI."""
import argparse, hashlib, importlib.util, json, math, os, platform, shutil, sys, tempfile, time, zipfile
from pathlib import Path
sys.dont_write_bytecode = True
_core_spec=importlib.util.spec_from_file_location('offline_audio_core', Path(__file__).with_name('tools-audio-core.py'))
core=importlib.util.module_from_spec(_core_spec)
_core_spec.loader.exec_module(core)

CAPABILITIES = [
 {'name':'project-scaffold','command':'scaffold','scope':'Portable project folders and a starter production contract.'},
 {'name':'wav-inspection-qc','command':'analyze / qc','scope':'PCM/float WAV integrity, duration, sample peak, RMS, clipping, silence and DC measurements.'},
 {'name':'safe-wav-processing','command':'process','scope':'Peak normalization, gain, trim, PCM bit-depth render, and mono/stereo plus sample-rate conversion.'},
 {'name':'batch-processing','command':'batch','scope':'Manifest-driven execution, per-item results, stop/resume-friendly reporting.'},
 {'name':'test-tone','command':'tone','scope':'Deterministic sine-wave test fixture generation for local signal-path checks.'},
 {'name':'rights-provenance-gate','command':'rights-check','scope':'Validate supplied rights/consent evidence records; not legal advice.'},
 {'name':'project-packaging','command':'package / verify','scope':'Local ZIP with file hashes, safe path handling, integrity verification.'},
 {'name':'environment-and-budget','command':'environment / budget','scope':'Runtime and storage reporting; no hardware/device control.'},
]

def emit(obj, output=None):
    text=json.dumps(obj,indent=2,ensure_ascii=False,allow_nan=False)+'\n'
    if output:
        p=Path(output); p.parent.mkdir(parents=True,exist_ok=True)
        tmp=p.with_name(p.name+'.tmp'); tmp.write_text(text,encoding='utf-8'); os.replace(tmp,p)
    sys.stdout.write(text)

def need_file(path):
    p=Path(path)
    if not p.is_file(): raise ValueError(f'Input file does not exist or is not a regular file: {path}')
    return p

def read_json(path):
    try: return json.loads(need_file(path).read_text(encoding='utf-8'))
    except json.JSONDecodeError as e: raise ValueError(f'Invalid JSON in {path}: {e}')

def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def validate_rights(obj):
    if not isinstance(obj,dict) or not isinstance(obj.get('assets'),list) or not obj['assets']:
        raise ValueError('Rights ledger must be an object with a non-empty assets array')
    issues=[]
    for i,a in enumerate(obj['assets']):
        if not isinstance(a,dict): issues.append(f'assets[{i}] must be an object'); continue
        for k in ('asset','rights_status','evidence'):
            if not isinstance(a.get(k),str) or not a[k].strip(): issues.append(f'assets[{i}].{k} is required')
        if a.get('rights_status') not in ('authorized','owned','public_domain','licensed'):
            issues.append(f'assets[{i}].rights_status must be authorized, owned, public_domain, or licensed')
        if a.get('consent_required') is True and not (isinstance(a.get('consent_record'),str) and a['consent_record'].strip()):
            issues.append(f'assets[{i}] requires consent_record')
        if a.get('release_approved') is not True:
            issues.append(f'assets[{i}] lacks explicit release_approved=true')
    return issues

def handle(a):
    cmd=a.command
    if cmd=='capabilities': return emit({'ok':True,'capabilities':CAPABILITIES},a.output)
    if cmd=='scaffold':
        root=Path(a.output)
        if root.exists() and any(root.iterdir()): raise ValueError('Refusing to scaffold into a non-empty directory')
        root.mkdir(parents=True,exist_ok=True)
        for name in ('recordings','takes','edits','sessions','midi','samples','stems','mixes','masters','transcripts','rights','archive','reports'):
            (root/name).mkdir(exist_ok=True)
        cfg=read_json(Path(__file__).with_name('asset-default-config.json'))
        cfg['project_name']=a.name
        (root/'project-config.json').write_text(json.dumps(cfg,indent=2)+'\n',encoding='utf-8')
        return emit({'ok':True,'project':str(root),'created_directories':13,'config':'project-config.json'},None)
    if cmd=='environment':
        p=Path(a.path).resolve()
        usage=shutil.disk_usage(p if p.exists() else p.parent)
        return emit({'ok':True,'python':platform.python_version(),'platform':platform.platform(),'stdlib_only':True,'network_used':False,'path':str(p),'free_bytes':usage.free,'supports_audio_devices':False,'supports_models_or_daw_plugins':False},a.output)
    if cmd=='budget':
        duration=float(a.duration); rate=int(a.rate); channels=int(a.channels); bits=int(a.bits); copies=int(a.copies); margin=float(a.margin)
        if not all(math.isfinite(v) for v in (duration,margin)) or duration<=0 or not 8000<=rate<=384000 or not 1<=channels<=32 or bits not in (16,24,32) or copies<1 or not 0<=margin<=5:
            raise ValueError('Require duration>0, rate 8000..384000, channels 1..32, bits 16/24/32, copies>=1, margin 0..5')
        raw=math.ceil(duration*rate*channels*bits/8); total=math.ceil(raw*copies*(1+margin))
        return emit({'ok':True,'one_uncompressed_pcm_file_bytes':raw,'copies':copies,'safety_margin_fraction':margin,'estimated_total_bytes':total},a.output)
    if cmd=='tone':
        if not all(math.isfinite(v) for v in (a.duration,a.frequency,a.level_dbfs)) or not 0.01<=a.duration<=600 or not 20<=a.frequency<=20000 or not 8000<=a.rate<=192000 or not -60<=a.level_dbfs<=-0.1 or a.channels not in (1,2):
            raise ValueError('Tone limits: duration 0.01..600s, frequency 20..20000Hz, sample rate 8000..192000Hz, level -60..-0.1dBFS, channels 1 or 2')
        amp=10**(a.level_dbfs/20); n=round(a.duration*a.rate)
        if n*a.channels>core.MAX_SAMPLE_VALUES: raise ValueError('Tone exceeds decoded sample-value safety limit')
        samples=[]
        for i in range(n):
            v=amp*math.sin(2*math.pi*a.frequency*i/a.rate)
            samples.extend([v]*a.channels)
        core.write_wav(a.output,samples,a.channels,a.rate,a.bits)
        return emit({'ok':True,'output':a.output,'frames':n,'frequency_hz':a.frequency,'duration_seconds':n/a.rate},None)
    if cmd in ('analyze','qc'):
        if cmd=='qc':
            vals=[a.max_peak_dbfs,a.min_duration] + ([] if a.max_duration is None else [a.max_duration])
            if not all(math.isfinite(v) for v in vals) or not -100<=a.max_peak_dbfs<=0 or a.min_duration<0 or (a.max_duration is not None and (a.max_duration<0 or a.max_duration<a.min_duration)):
                raise ValueError('QC limits must be finite; peak -100..0 dBFS; duration limits nonnegative and maximum >= minimum')
        data=core.read_wav(need_file(a.input)); report=core.analyze(data)
        if cmd=='qc':
            checks=[]
            checks.append({'name':'nonempty','pass':data['frames']>0})
            checks.append({'name':'sample_peak_ceiling','pass':report['sample_peak_dbfs'] is None or report['sample_peak_dbfs']<=a.max_peak_dbfs,'limit_dbfs':a.max_peak_dbfs})
            checks.append({'name':'minimum_duration','pass':report['duration_seconds']>=a.min_duration,'limit_seconds':a.min_duration})
            if a.max_duration is not None: checks.append({'name':'maximum_duration','pass':report['duration_seconds']<=a.max_duration,'limit_seconds':a.max_duration})
            report['checks']=checks; report['passed']=all(c['pass'] for c in checks)
        report['ok']=True
        emit(report,a.output)
        if cmd=='qc' and not report['passed']: return 2
        return 0
    if cmd=='process':
        src=need_file(a.input); dst=Path(a.output)
        if src.resolve()==dst.resolve(): raise ValueError('Input and output must differ; source files are never overwritten')
        if dst.exists() and not a.overwrite: raise ValueError('Output exists; specify --overwrite to replace this output file')
        data=core.read_wav(src); samples,ch,rate,bits,detail=core.process(data,a.operation,vars(a))
        core.write_wav(dst,samples,ch,rate,bits)
        return emit({'ok':True,'operation':a.operation,'input':str(src),'output':str(dst),'before':core.analyze(data),'after':core.analyze(core.read_wav(dst)),'details':detail},None)
    if cmd=='batch':
        manifest=read_json(a.input)
        if not isinstance(manifest,dict) or not isinstance(manifest.get('jobs'),list) or len(manifest['jobs'])>10000:
            raise ValueError('Manifest must contain a jobs array with at most 10000 entries')
        base=Path(a.input).resolve().parent; results=[]
        for i,job in enumerate(manifest['jobs']):
            try:
                if not isinstance(job,dict): raise ValueError('job must be an object')
                op=job.get('operation'); src=(base/str(job.get('input',''))).resolve(); dst=(base/str(job.get('output',''))).resolve()
                if op not in ('normalize','gain','trim','convert'): raise ValueError('Unsupported operation')
                if not src.is_relative_to(base) or not dst.is_relative_to(base): raise ValueError('Manifest paths must stay inside manifest directory')
                if not src.is_file(): raise ValueError(f'Missing input: {job.get("input")}')
                if src==dst: raise ValueError('Input and output must differ')
                if dst.exists() and not a.overwrite: raise ValueError('Output exists; rerun with --overwrite only after reviewing it')
                data=core.read_wav(src); samples,ch,rate,bits,detail=core.process(data,op,job.get('params',{})); core.write_wav(dst,samples,ch,rate,bits)
                results.append({'index':i,'ok':True,'operation':op,'output':str(dst.relative_to(base)),'details':detail})
            except Exception as e: results.append({'index':i,'ok':False,'error':str(e)})
        result={'ok':all(x['ok'] for x in results),'job_count':len(results),'passed':sum(x['ok'] for x in results),'failed':sum(not x['ok'] for x in results),'results':results,'resume_guidance':'Successful outputs are preserved; fix failed items and rerun the manifest. Existing outputs are skipped unless --overwrite is supplied.'}
        emit(result,a.output)
        if not result['ok']: return 2
        return 0
    if cmd=='rights-check':
        obj=read_json(a.input); issues=validate_rights(obj)
        result={'ok':not issues,'asset_count':len(obj.get('assets',[])) if isinstance(obj,dict) and isinstance(obj.get('assets'),list) else 0,'issues':issues,'notice':'This checks completeness and explicit declarations only; it cannot establish legal ownership, license validity, identity, or consent authenticity.'}
        emit(result,a.output)
        if issues: return 2
        return 0
    if cmd=='package':
        root=need_file(a.input) if Path(a.input).is_file() else Path(a.input)
        if not root.is_dir(): raise ValueError('Package input must be an existing directory')
        if a.release:
            if not a.approve_release: raise ValueError('--release requires explicit --approve-release acknowledgement')
            if not a.rights_ledger: raise ValueError('--release requires --rights-ledger JSON')
            issues=validate_rights(read_json(a.rights_ledger))
            if issues: raise ValueError('Rights gate failed: '+'; '.join(issues))
        dest=Path(a.output).resolve(); root=root.resolve()
        if dest==root or root in dest.parents: raise ValueError('Archive output must be outside the source project folder')
        if dest.exists() and not a.overwrite: raise ValueError('Archive output exists; use --overwrite after review')
        files=[]
        for p in sorted(root.rglob('*')):
            if p.is_symlink(): continue
            if p.is_file():
                rel=p.relative_to(root).as_posix()
                if rel in ('PACKAGE-MANIFEST.json',): continue
                files.append((p,rel))
        if a.rights_ledger:
            ledger=need_file(a.rights_ledger).resolve()
            if all(p.resolve()!=ledger for p,_ in files):
                if any(rel=='rights-ledger.json' for _,rel in files): raise ValueError('Project already contains rights-ledger.json; choose a non-conflicting layout or store the ledger in the project')
                files.append((ledger,'rights-ledger.json'))
        if len(files)>50000: raise ValueError('Project exceeds 50000 file safety limit')
        manifest={'schema':'offline-audio-package-v1','created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'files':[{'path':rel,'size_bytes':p.stat().st_size,'sha256':sha256(p)} for p,rel in files]}
        dest.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='audio-package-',suffix='.zip',dir=dest.parent); os.close(fd)
        try:
            with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
                for p,rel in files: z.write(p,rel)
                z.writestr('PACKAGE-MANIFEST.json',json.dumps(manifest,indent=2)+'\n')
            os.replace(tmp,dest)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return emit({'ok':True,'archive':str(dest),'file_count':len(files),'release_gate_applied':bool(a.release)},None)
    if cmd=='verify':
        p=need_file(a.input)
        try:
            with zipfile.ZipFile(p) as z:
                names=z.namelist()
                if len(names)>50001: raise ValueError('Archive exceeds file-count safety limit')
                if len(names)!=len(set(names)): raise ValueError('Duplicate paths in ZIP are not allowed')
                if any('\\' in n or ':' in n or Path(n).is_absolute() or '..' in Path(n).parts for n in names): raise ValueError('Unsafe path in ZIP')
                if 'PACKAGE-MANIFEST.json' not in names: raise ValueError('Package manifest missing')
                manifest=json.loads(z.read('PACKAGE-MANIFEST.json'))
                issues=[]; expanded=0
                for f in manifest.get('files',[]):
                    if not isinstance(f,dict): issues.append('Malformed manifest file record'); continue
                    name=f.get('path','')
                    if name not in names: issues.append(f'Missing {name}'); continue
                    info=z.getinfo(name); expanded+=info.file_size
                    if info.file_size>256_000_000 or expanded>2_000_000_000: raise ValueError('Archive exceeds verification expansion limits')
                    h=hashlib.sha256(); count=0
                    with z.open(info,'r') as stream:
                        for block in iter(lambda:stream.read(1024*1024),b''):
                            count+=len(block); h.update(block)
                    if count!=f.get('size_bytes'): issues.append(f'Size mismatch {name}')
                    if h.hexdigest()!=f.get('sha256'): issues.append(f'Hash mismatch {name}')
                return emit({'ok':not issues,'archive':str(p),'listed_files':len(manifest.get('files',[])),'issues':issues},a.output)
        except zipfile.BadZipFile: raise ValueError('Invalid or corrupt ZIP archive')
    raise ValueError('Unknown command')

def build_parser():
    p=argparse.ArgumentParser(prog='tools-audio.py',description='Local-first offline audio utilities. No network, DAW, device, model, or plugin execution.')
    sp=p.add_subparsers(dest='command',required=True)
    q=sp.add_parser('capabilities',help='List implemented capabilities'); q.add_argument('--output')
    q=sp.add_parser('scaffold',help='Create a portable audio project directory'); q.add_argument('--output',required=True); q.add_argument('--name',default='New Audio Project')
    q=sp.add_parser('environment',help='Report runtime and free disk space'); q.add_argument('--path',default='.'); q.add_argument('--output')
    q=sp.add_parser('budget',help='Estimate uncompressed PCM storage'); q.add_argument('--duration',type=float,required=True); q.add_argument('--rate',type=int,default=48000); q.add_argument('--channels',type=int,default=2); q.add_argument('--bits',type=int,default=24); q.add_argument('--copies',type=int,default=1); q.add_argument('--margin',type=float,default=.2); q.add_argument('--output')
    q=sp.add_parser('tone',help='Generate a WAV sine test tone'); q.add_argument('--output',required=True); q.add_argument('--duration',type=float,default=3); q.add_argument('--frequency',type=float,default=1000); q.add_argument('--rate',type=int,default=48000); q.add_argument('--channels',type=int,choices=(1,2),default=1); q.add_argument('--level-dbfs',type=float,default=-18); q.add_argument('--bits',type=int,choices=(16,24,32),default=24)
    for name in ('analyze','qc'):
        q=sp.add_parser(name,help='Inspect WAV measurements' if name=='analyze' else 'Check peak and duration constraints')
        q.add_argument('--input',required=True); q.add_argument('--output')
        if name=='qc': q.add_argument('--max-peak-dbfs',type=float,default=-1.0); q.add_argument('--min-duration',type=float,default=0); q.add_argument('--max-duration',type=float)
    q=sp.add_parser('process',help='Process one WAV into a new WAV'); q.add_argument('--input',required=True); q.add_argument('--output',required=True); q.add_argument('--operation',choices=('normalize','gain','trim','convert'),required=True); q.add_argument('--target-dbfs',type=float,default=-1); q.add_argument('--gain-db',type=float,default=0); q.add_argument('--start-seconds',type=float,default=0); q.add_argument('--end-seconds',type=float); q.add_argument('--sample-rate-hz',type=int); q.add_argument('--channels',type=int); q.add_argument('--bit-depth',type=int,choices=(16,24,32),default=24); q.add_argument('--overwrite',action='store_true')
    q=sp.add_parser('batch',help='Run bounded manifest-driven WAV jobs'); q.add_argument('--input',required=True); q.add_argument('--output'); q.add_argument('--overwrite',action='store_true')
    q=sp.add_parser('rights-check',help='Validate declared rights and consent ledger'); q.add_argument('--input',required=True); q.add_argument('--output')
    q=sp.add_parser('package',help='Create a local ZIP with checksums'); q.add_argument('--input',required=True); q.add_argument('--output',required=True); q.add_argument('--rights-ledger'); q.add_argument('--release',action='store_true'); q.add_argument('--approve-release',action='store_true'); q.add_argument('--overwrite',action='store_true')
    q=sp.add_parser('verify',help='Verify package paths, sizes and SHA-256 hashes'); q.add_argument('--input',required=True); q.add_argument('--output')
    return p

def main(argv=None):
    parser=build_parser()
    try:
        args=parser.parse_args(argv)
        code=handle(args)
        return code if isinstance(code,int) else 0
    except SystemExit as e:
        return int(e.code)
    except (ValueError,OSError,core.AudioError,zipfile.BadZipFile) as e:
        sys.stderr.write(json.dumps({'ok':False,'error':str(e)},ensure_ascii=False)+'\n')
        return 2

if __name__=='__main__': raise SystemExit(main())
