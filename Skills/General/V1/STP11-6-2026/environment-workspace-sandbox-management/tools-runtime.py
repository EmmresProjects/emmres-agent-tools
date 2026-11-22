"""Explicitly approved, bounded local runtimes and optional external adapters."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import venv
from datetime import datetime
from pathlib import Path
from workspace_core import Context, ToolError, canonical, digest, file_sha, integer, load_json, redact, string, strings, utc


def _new_dest(ctx: Context, name: str, *, parent: bool = True) -> Path:
    p = ctx.path(name); ctx.require_mutable(name)
    if p.exists():
        raise ToolError('collision', 'Output must be a new path', path=name)
    if parent and not p.parent.is_dir():
        raise ToolError('missing_input', 'Output parent directory must already exist', parent=ctx.relative(p.parent))
    return p


def _venv(ctx: Context) -> dict:
    name = string(ctx.request.get('destination', 'working/venv'), 'destination')
    dest = _new_dest(ctx, name)
    with_pip = ctx.request.get('with_pip', True)
    if type(with_pip) is not bool:
        raise ToolError('invalid_input', 'with_pip must be boolean')
    plan = {'destination':name,'interpreter':sys.version.split()[0], 'with_pip':with_pip,
            'system_site_packages':False, 'network':'none by this operation'}
    pending = ctx.authorize(plan, 'create isolated Python environment; no global packages altered')
    if pending: return pending
    if dest.exists(): raise ToolError('collision','Destination changed during approval', path=name)
    try:
        venv.EnvBuilder(with_pip=with_pip, system_site_packages=False, clear=False, symlinks=False).create(dest)
        # Some Python distributions ship world-writable activation templates. Never propagate them.
        if os.name=='posix':
            for parent, directories, files in os.walk(dest, followlinks=False):
                for child in directories+files:
                    p=Path(parent)/child
                    if p.is_symlink(): continue  # venv may create an internal lib64 -> lib link.
                    mode=stat.S_IMODE(p.stat().st_mode)
                    safe_mode=mode & ~0o027  # remove group write and every permission for others.
                    if safe_mode!=mode:os.chmod(p,safe_mode)
        exe = dest / ('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        if not exe.is_file():
            raise ToolError('verification_failed','Virtual environment interpreter missing')
        version = subprocess.run([str(exe),'--version'],capture_output=True,text=True,timeout=10,check=True)
        return {'status':'completed','destination':name,'interpreter':ctx.relative(exe),'version':redact(version.stdout.strip() or version.stderr.strip())}
    except Exception:
        if dest.exists(): shutil.rmtree(dest)
        raise


def _venv_exe(ctx: Context) -> Path:
    name = string(ctx.request.get('environment'), 'environment')
    p = ctx.path(name, must_exist=True)
    if not p.is_dir() or not (p/'pyvenv.cfg').is_file():
        raise ToolError('invalid_input','environment must be an existing local venv directory')
    exe=p / ('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not exe.is_file() or exe.is_symlink():
        raise ToolError('missing_input','venv interpreter missing or symlinked')
    return exe


def _local_process(ctx: Context, argv: list[str], timeout: int, cwd: Path | None = None) -> dict:
    # Fixed executable path is derived only from a newly validated venv. No shell is used.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(argv,cwd=cwd or ctx.root,env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','LC_ALL','TZ','SYSTEMROOT','TEMP','TMP')},
                                    stdin=subprocess.DEVNULL,stdout=out,stderr=err,start_new_session=os.name=='posix')
            try: code=proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name=='posix': os.killpg(proc.pid,signal.SIGKILL)
                else: proc.kill()
                proc.wait(); raise ToolError('timeout','Approved isolated process timed out',seconds=timeout)
            except KeyboardInterrupt as exc:
                if os.name=='posix': os.killpg(proc.pid,signal.SIGKILL)
                else: proc.kill()
                proc.wait(); raise ToolError('cancelled','Isolated child process stopped after cancellation') from exc
        except OSError as exc:
            raise ToolError('integration_unavailable', f'Cannot run local venv executable: {exc}') from exc
        limit=ctx.limits['max_command_output_bytes']; out.seek(0);err.seek(0)
        return {'exit_code':code,'stdout':redact(out.read(limit).decode('utf-8','replace')),
                'stderr':redact(err.read(limit).decode('utf-8','replace'))}


def _pip(ctx: Context) -> dict:
    exe = _venv_exe(ctx)
    requirements=string(ctx.request.get('requirements'),'requirements')
    wheels=string(ctx.request.get('wheelhouse'),'wheelhouse')
    req=ctx.path(requirements,must_exist=True); wheelhouse=ctx.path(wheels,must_exist=True)
    if not req.is_file() or not wheelhouse.is_dir():
        raise ToolError('invalid_input','requirements must be a file and wheelhouse a directory')
    lines=[x.strip() for x in ctx.read_text(requirements).splitlines() if x.strip() and not x.strip().startswith('#')]
    if not lines or len(lines)>100:
        raise ToolError('invalid_input','Provide 1-100 pinned requirements with hashes')
    for line in lines:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+(?:\s+--hash=sha256:[a-fA-F0-9]{64})+',line):
            raise ToolError('invalid_input','Every requirement must be exact name==version with SHA256 hash; no URLs, flags or nested requirements', line=line[:80])
    wheel_files=[]
    for p in sorted(wheelhouse.iterdir()):
        if p.suffix != '.whl': continue
        checked=ctx.path(ctx.relative(p),must_exist=True)
        if not checked.is_file():
            raise ToolError('invalid_input','Wheelhouse entry is not a regular file',name=p.name)
        wheel_files.append({'name':p.name,'sha256':file_sha(checked,ctx.limits['max_transfer_bytes'])})
    plan={'environment':ctx.request['environment'],'requirements_sha256':file_sha(req), 'wheelhouse':wheels,
          'wheels':wheel_files,'offline':True,'no_deps':True,'require_hashes':True}
    if not plan['wheels']:
        raise ToolError('missing_input','Wheelhouse contains no .whl files')
    pending=ctx.authorize(plan,'install locally supplied, pinned wheels in one venv; package code may run during installation')
    if pending:return pending
    if file_sha(req)!=plan['requirements_sha256'] or any(
        file_sha(ctx.path(wheels+'/'+item['name'],must_exist=True))!=item['sha256'] for item in plan['wheels']):
        raise ToolError('changed_input','Requirements or local wheel changed after approval')
    argv=[str(exe),'-I','-m','pip','--isolated','install','--no-cache-dir','--disable-pip-version-check','--no-index','--no-deps','--require-hashes',
          '--find-links',str(wheelhouse),'-r',str(req)]
    result=_local_process(ctx,argv,ctx.limits['max_command_seconds'])
    if result['exit_code']:
        raise ToolError('command_failed','Offline pip install failed; environment may need recreation', result=result)
    return {'status':'completed','installation':result,'verification':_local_process(ctx,[str(exe),'-I','-m','pip','list','--format=json'],10)}


def _npm(ctx: Context) -> dict:
    project_name=string(ctx.request.get('project'),'project')
    project=ctx.path(project_name,must_exist=True)
    if not project.is_dir():
        raise ToolError('missing_input','Node project must be a directory')
    package_file=ctx.path(project_name+'/package.json',must_exist=True)
    lock_file=ctx.path(project_name+'/package-lock.json',must_exist=True)
    if not package_file.is_file() or not lock_file.is_file():
        raise ToolError('missing_input','Node project requires regular package.json and package-lock.json')
    if not shutil.which('npm'):
        raise ToolError('integration_unavailable','npm is not installed')
    package=load_json(package_file,ctx.limits['max_input_bytes'])
    lock=load_json(lock_file,ctx.limits['max_archive_bytes'])
    if not isinstance(package,dict) or not isinstance(lock,dict) or package.get('name')!=lock.get('name'):
        raise ToolError('invalid_input','package.json and package-lock.json must agree on project name')
    plan={'project':project_name,'package_sha256':file_sha(package_file),
          'lock_sha256':file_sha(lock_file),'offline':True,'ignore_scripts':True,
          'node_modules_exists':(project/'node_modules').exists()}
    if plan['node_modules_exists']:
        raise ToolError('collision','npm ci would remove existing node_modules; use a new isolated project copy')
    pending=ctx.authorize(plan,'offline npm ci (scripts disabled); project dependencies may still be untrusted')
    if pending:return pending
    if (file_sha(package_file)!=plan['package_sha256'] or file_sha(lock_file)!=plan['lock_sha256']
        or (project/'node_modules').exists()):
        raise ToolError('changed_input','Node manifests or isolated project state changed after approval')
    cache=ctx.ensure_state()/'npm-cache'
    cache.mkdir(mode=0o700,exist_ok=True)
    result=ctx.safe_external(['npm','ci','--offline','--ignore-scripts','--no-audit','--no-fund',
                              '--cache',str(cache)],ctx.limits['max_command_seconds'],cwd=project)
    if result['exit_code']:
        raise ToolError('command_failed','npm ci failed; inspect new isolated project copy before retry',result=result)
    return {'status':'completed','result':result,'installed_directory':project_name+'/node_modules'}


def _profile(ctx: Context):
    name=string(ctx.request.get('profile'),'profile',maxlen=80)
    profile=ctx.policy['trusted_profiles'].get(name)
    if not profile:
        raise ToolError('not_allowed','Profile is not explicitly configured',profile=name)
    work=string(ctx.request.get('cwd','.'),'cwd')
    cwd=ctx.path(work,must_exist=True,allow_root=True)
    if not cwd.is_dir(): raise ToolError('invalid_input','cwd must be a directory')
    if not shutil.which(profile['argv'][0]):
        raise ToolError('integration_unavailable','Trusted profile executable not available',executable=profile['argv'][0])
    timeout=profile['timeout_seconds']
    env_names=strings(ctx.request.get('env_names',[]),'env_names',maximum=20)
    for n in env_names:
        if not re.fullmatch('[A-Za-z_][A-Za-z_0-9]{0,63}',n) or n not in os.environ:
            raise ToolError('missing_input','Declared environment variable is missing or invalid',name=n)
    return name,profile,cwd,timeout,env_names


def _exec(ctx: Context) -> dict:
    name,profile,cwd,timeout,env_names=_profile(ctx)
    plan={'profile':name,'executable':profile['argv'][0], 'argv_sha256':digest(canonical(profile['argv'])),
          'cwd':ctx.relative(cwd),'timeout_seconds':timeout,'passed_environment_names':env_names}
    pending=ctx.authorize(plan,'execute explicitly trusted profile without shell; external processes are NOT a security sandbox')
    if pending:return pending
    result=ctx.safe_external(profile['argv'],timeout,cwd=cwd,env_names=env_names)
    if result['exit_code']:
        raise ToolError('command_failed','Trusted profile failed',profile=name,result=result)
    return {'status':'completed','profile':name,'result':result}


def _job_file(ctx: Context, name: str) -> Path:
    if not re.fullmatch(r'[a-z0-9-]{1,64}',name):
        raise ToolError('invalid_input','job_id must be lowercase alphanumeric/hyphen')
    return ctx.state / ('job-'+name+'.json')


def _start_tick(pid: int):
    try:
        s=Path('/proc')/str(pid)/'stat'
        fields=s.read_text().rsplit(')',1)[1].split()
        return None if fields[0]=='Z' else fields[19]
    except (OSError,IndexError): return None


def _job_status(ctx: Context, name: str) -> dict:
    p=_job_file(ctx,name)
    if not p.is_file() or p.is_symlink(): raise ToolError('missing_input','Task-owned job record not found',job_id=name)
    job=load_json(p)
    tick=_start_tick(job['pid']) if job.get('pid') else None
    alive=bool(tick and tick==job['start_tick'])
    log=ctx.path(job['log'],internal=True)
    log_stat=log.stat() if log.is_file() else None
    started=datetime.fromisoformat(job['started']).timestamp()
    elapsed=max(0,round(time.time()-started,1))
    return {'job_id':name,'alive':alive,'pid':job['pid'],'profile':job['profile'],'started':job['started'],
            'log':job['log'],'log_bytes':log_stat.st_size if log_stat else None,
            'seconds_since_log_update':round(time.time()-log_stat.st_mtime,1) if log_stat else None,
            'elapsed_seconds':elapsed,'overdue':elapsed>job.get('max_runtime_seconds',86400),
            'stale':not alive}


def _service(ctx: Context, variant: str) -> dict:
    if os.name!='posix' or not Path('/proc').is_dir():
        raise ToolError('integration_unavailable','Background ownership verification requires POSIX /proc')
    if variant=='service-status':
        name=ctx.request.get('job_id')
        if name:return _job_status(ctx,string(name,'job_id'))
        if not ctx.state.is_dir():return {'jobs':[]}
        jobs=[]
        for p in sorted(ctx.state.glob('job-*.json'))[:100]:
            try:jobs.append(_job_status(ctx,p.name[4:-5]))
            except ToolError:continue
        return {'jobs':jobs}
    if variant=='service-start':
        name,profile,cwd,timeout,env_names=_profile(ctx)
        job_id=string(ctx.request.get('job_id'),'job_id',maxlen=64)
        path=_job_file(ctx,job_id)
        if path.exists():raise ToolError('collision','job_id already has a record; review and stop/clean before reuse',job_id=job_id)
        max_runtime=integer(ctx.request.get('max_runtime_seconds',3600),'max_runtime_seconds',1,86400)
        plan={'job_id':job_id,'profile':name,'argv_sha256':digest(canonical(profile['argv'])),
              'cwd':ctx.relative(cwd),'startup_wait_seconds':min(2,timeout),'env_names':env_names,
              'max_runtime_seconds':max_runtime}
        pending=ctx.authorize(plan,'start task-owned background service; follow with status and explicit stop')
        if pending:return pending
        ctx.ensure_state()
        log=ctx.state_file('job-'+job_id+'.log')
        with log.open('xb') as fh:
            os.chmod(log,0o600)
            env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','TZ','SYSTEMROOT','TEMP','TMP',*env_names}}
            proc=subprocess.Popen(profile['argv'],cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=fh,stderr=subprocess.STDOUT,start_new_session=True)
        tick=_start_tick(proc.pid)
        if tick is None:
            raise ToolError('command_failed','Service exited before ownership record could be established; inspect redacted task log',log=ctx.relative(log))
        record={'pid':proc.pid,'start_tick':tick,'profile':name,'started':utc(),'log':ctx.relative(log),
                'argv_sha256':plan['argv_sha256'],'max_runtime_seconds':max_runtime}
        ctx.atomic_bytes(path,(json.dumps(record,indent=2)+'\n').encode())
        time.sleep(min(0.2,timeout))
        status=_job_status(ctx,job_id)
        if not status['alive']:
            raise ToolError('command_failed','Service exited during startup; inspect redacted task log',log=ctx.relative(log))
        return {'status':'completed',**status}
    name=string(ctx.request.get('job_id'),'job_id')
    status=_job_status(ctx,name)
    if not status['alive']:
        return {'status':'unchanged',**status,'note':'Process is already stopped; record kept for audit.'}
    plan={'job_id':name,'pid':status['pid'],'profile':status['profile'],'started':status['started']}
    pending=ctx.authorize(plan,'terminate only this verified, task-owned process group')
    if pending:return pending
    if not _job_status(ctx,name)['alive']:
        return {'status':'unchanged',**_job_status(ctx,name)}
    if os.getpgid(status['pid']) != status['pid']:
        raise ToolError('not_allowed','Task process no longer leads its recorded process group; refuse broad termination')
    os.killpg(status['pid'],signal.SIGTERM)
    until=time.monotonic()+3
    while _job_status(ctx,name)['alive'] and time.monotonic()<until:
        time.sleep(0.1)
    if _job_status(ctx,name)['alive']:
        os.killpg(status['pid'],signal.SIGKILL)
        time.sleep(0.1)
    return {'status':'completed',**_job_status(ctx,name)}


def _schedule(ctx: Context) -> dict:
    name,profile,cwd,timeout,env_names=_profile(ctx)
    count=integer(ctx.request.get('count',1),'count',1,5)
    interval=integer(ctx.request.get('interval_seconds',0),'interval_seconds',0,60)
    if count*timeout+(count-1)*interval>300:
        raise ToolError('limit_exceeded','Scheduled run exceeds 300-second budget')
    plan={'profile':name,'argv_sha256':digest(canonical(profile['argv'])),'count':count,'interval_seconds':interval,'cwd':ctx.relative(cwd)}
    pending=ctx.authorize(plan,'bounded in-process recurrence; no persistent cron or global scheduler changes')
    if pending:return pending
    results=[]
    for n in range(count):
        if (ctx.state/'cancel').is_file():
            return {'status':'cancelled','completed':len(results),'results':results}
        results.append(ctx.safe_external(profile['argv'],timeout,cwd=cwd,env_names=env_names))
        if results[-1]['exit_code']:
            return {'status':'failed','completed':len(results),'results':results}
        if n<count-1:time.sleep(interval)
    return {'status':'completed','count':count,'results':results}


def _sqlite(ctx: Context) -> dict:
    name=string(ctx.request.get('destination'),'destination')
    if not name.endswith(('.sqlite','.db')):
        raise ToolError('invalid_input','SQLite destination needs .sqlite or .db extension')
    dst=_new_dest(ctx,name)
    tables=ctx.request.get('tables')
    if not isinstance(tables,list) or not tables or len(tables)>20:
        raise ToolError('invalid_input','tables must contain 1..20 table definitions')
    schema=[]
    for table in tables:
        if not isinstance(table,dict) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',str(table.get('name',''))):
            raise ToolError('invalid_input','Invalid table name')
        cols=table.get('columns')
        if not isinstance(cols,list) or not cols or len(cols)>100:
            raise ToolError('invalid_input','Each table needs 1..100 columns')
        built=[]
        for col in cols:
            if not isinstance(col,dict) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',str(col.get('name',''))):
                raise ToolError('invalid_input','Invalid column name')
            typ=col.get('type')
            if typ not in ('INTEGER','TEXT','REAL','BLOB') or type(col.get('primary_key',False)) is not bool:
                raise ToolError('invalid_input','Column type must be INTEGER/TEXT/REAL/BLOB and primary_key boolean')
            built.append('"'+col['name']+'" '+typ+(' PRIMARY KEY' if col.get('primary_key') else ''))
        if len({c['name'].lower() for c in cols})!=len(cols):
            raise ToolError('invalid_input','Duplicate column name')
        schema.append({'name':table['name'],'sql':'CREATE TABLE "'+table['name']+'" ('+', '.join(built)+')'})
    if len({x['name'].lower() for x in schema})!=len(schema):
        raise ToolError('invalid_input','Duplicate table name')
    plan={'destination':name,'tables':[{'name':s['name'],'sql_sha256':digest(s['sql'])} for s in schema]}
    pending=ctx.authorize(plan,'create new bounded SQLite database; no user-supplied SQL executed')
    if pending:return pending
    fd,temp=tempfile.mkstemp(prefix='.workspace-sqlite-',dir=dst.parent);os.close(fd)
    try:
        conn=sqlite3.connect(temp,timeout=5)
        try:
            for s in schema:conn.execute(s['sql'])
            conn.commit()
            if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ToolError('verification_failed','SQLite integrity check failed')
        finally:conn.close()
        if dst.exists():raise ToolError('collision','Database destination appeared')
        os.replace(temp,dst)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    return {'status':'completed','destination':name,'tables':[s['name'] for s in schema],'sha256':file_sha(dst)}


def _external(ctx: Context, variant: str) -> dict:
    if variant.startswith('container'):
        if not ctx.policy['enable_container_adapter'] or not shutil.which('docker'):
            raise ToolError('integration_unavailable','Docker adapter disabled by policy or Docker CLI unavailable; no container operation performed',
                            required=['enable_container_adapter=true','Docker CLI and daemon','trusted pinned image or Dockerfile'])
        if variant=='container-build':
            dockerfile=string(ctx.request.get('dockerfile'),'dockerfile')
            text=ctx.read_text(dockerfile,max_bytes=65536)
            bases=re.findall(r'^\s*FROM\s+(\S+)',text,re.I|re.M)
            if not bases or any('@sha256:' not in base for base in bases) or re.search(r'^\s*(ADD|VOLUME|USER\s+root|EXPOSE)\b',text,re.I|re.M):
                raise ToolError('not_allowed','Dockerfile must use pinned digest FROM and no ADD/VOLUME/USER root/EXPOSE')
            tag=string(ctx.request.get('tag'),'tag',maxlen=60)
            if not re.fullmatch('workspace-[a-z0-9-]{1,49}',tag):
                raise ToolError('invalid_input','Image tag must start with workspace-')
            plan={'tag':tag,'dockerfile':dockerfile,'sha256':digest(text),'network':'none','pull':False}
            pending=ctx.authorize(plan,'Docker daemon may be privileged; building Dockerfile runs code in container')
            if pending:return pending
            result=ctx.safe_external(['docker','build','--network=none','--pull=false','-t',tag,'-f',str(ctx.path(dockerfile)),'.'],
                                     ctx.limits['max_command_seconds'],cwd=ctx.root)
        else:
            image=string(ctx.request.get('image'),'image',maxlen=120)
            if not re.fullmatch(r'workspace-[a-z0-9-]{1,49}|[a-z0-9./-]+@sha256:[a-f0-9]{64}',image):
                raise ToolError('invalid_input','Image must be workspace-* or digest-pinned')
            plan={'image':image,'network':'none','user':'65534:65534','read_only':True,'no_host_mounts':True}
            pending=ctx.authorize(plan,'run approved container; Docker socket itself grants host-level powers')
            if pending:return pending
            result=ctx.safe_external(['docker','run','--rm','--network=none','--read-only','--user','65534:65534',
                                      '--pids-limit','64','--memory','512m','--cpus','1','--cap-drop=ALL',
                                      '--security-opt','no-new-privileges','--tmpfs','/tmp:rw,nosuid,noexec,size=64m',image],
                                     ctx.limits['max_command_seconds'])
        if result['exit_code']:
            raise ToolError('command_failed','Docker operation failed',result=result)
        return {'status':'completed','result':result}
    if variant=='vm':
        if not ctx.policy['enable_vm_adapter'] or not shutil.which('virsh'):
            raise ToolError('integration_unavailable','VM adapter disabled or virsh unavailable; VM lifecycle needs a provisioned libvirt session and OS image')
        op=ctx.request.get('op','list')
        if op!='list':
            raise ToolError('integration_unavailable','VM creation/snapshot changes are not supported without an approved, configured OS image and hypervisor policy; only session inventory is implemented')
        return {'status':'inspected','result':ctx.safe_external(['virsh','--connect','qemu:///session','list','--all'],10)}
    if variant=='transcode':
        if not shutil.which('ffmpeg'):
            raise ToolError('integration_unavailable','ffmpeg is not installed; no transcoding performed')
        source=string(ctx.request.get('source'),'source');dest=string(ctx.request.get('destination'),'destination')
        inp=ctx.path(source,must_exist=True);out=_new_dest(ctx,dest)
        if not inp.is_file() or inp.stat().st_size>ctx.limits['max_transfer_bytes']:
            raise ToolError('invalid_input','Source must be bounded regular media file')
        format=ctx.request.get('format')
        choices={'wav':('pcm_s16le','.wav'),'mp3':('libmp3lame','.mp3'),'mp4':('libx264','.mp4')}
        if format not in choices or not dest.endswith(choices[format][1]):
            raise ToolError('invalid_input','Format must be wav, mp3, or mp4 with matching destination extension')
        plan={'source':source,'source_sha256':file_sha(inp),'destination':dest,'format':format,'codec':choices[format][0]}
        pending=ctx.authorize(plan,'ffmpeg processes media with an external codec; original file preserved')
        if pending:return pending
        if file_sha(inp)!=plan['source_sha256']:
            raise ToolError('changed_input','Transcode source changed after approval')
        with tempfile.TemporaryDirectory(prefix='.workspace-transcode-',dir=out.parent) as folder:
            temporary=Path(folder)/('result'+choices[format][1])
            args=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-n','-i',str(inp)]
            args += ['-vn','-c:a',choices[format][0]] if format!='mp4' else ['-c:v','libx264','-c:a','aac']
            result=ctx.safe_external(args+[str(temporary)],ctx.limits['max_command_seconds'])
            if result['exit_code'] or not temporary.is_file() or not 0<temporary.stat().st_size<=ctx.limits['max_transfer_bytes']:
                raise ToolError('command_failed','Transcode failed or output invalid',result=result)
            if out.exists():raise ToolError('collision','Transcode destination appeared during work')
            os.replace(temporary,out)
        return {'status':'completed','destination':dest,'sha256':file_sha(out),'result':result}
    if variant=='render':
        binary=shutil.which('libreoffice') or shutil.which('soffice')
        if not binary:
            raise ToolError('integration_unavailable','LibreOffice is not installed; structural Office checks still work')
        source=string(ctx.request.get('source'),'source');dest=string(ctx.request.get('destination'),'destination')
        inp=ctx.path(source,must_exist=True);out=_new_dest(ctx,dest)
        if inp.suffix.lower() not in ('.docx','.pptx','.xlsx','.odt','.odp','.ods') or not dest.endswith('.pdf'):
            raise ToolError('invalid_input','Office source and new .pdf destination required')
        if out.stem!=inp.stem:
            raise ToolError('invalid_input','PDF basename must match source basename for LibreOffice conversion')
        plan={'source':source,'source_sha256':file_sha(inp),'destination':dest,'program':Path(binary).name}
        pending=ctx.authorize(plan,'external Office renderer; isolated per-run profile and preserved source')
        if pending:return pending
        if file_sha(inp)!=plan['source_sha256']:
            raise ToolError('changed_input','Office source changed after approval')
        with tempfile.TemporaryDirectory(prefix='.workspace-office-',dir=out.parent) as folder:
            work=Path(folder);profile=work/'profile';profile.mkdir();rendered=work/(inp.stem+'.pdf')
            result=ctx.safe_external([Path(binary).name,'-env:UserInstallation='+profile.as_uri(),
                                      '--headless','--convert-to','pdf','--outdir',str(work),str(inp)],
                                     ctx.limits['max_command_seconds'])
            if result['exit_code'] or not rendered.is_file() or not 0<rendered.stat().st_size<=ctx.limits['max_transfer_bytes']:
                raise ToolError('command_failed','Office rendering failed or produced no bounded PDF',result=result)
            with rendered.open('rb') as handle:
                if handle.read(5)!=b'%PDF-':
                    raise ToolError('verification_failed','Office output is not a PDF')
            if out.exists():raise ToolError('collision','PDF destination appeared during rendering')
            os.replace(rendered,out)
        return {'status':'completed','destination':dest,'sha256':file_sha(out),'result':result}
    raise ToolError('internal_error','Unknown external adapter',variant=variant)


def _download(ctx: Context) -> dict:
    if ctx.policy['offline']:
        raise ToolError('integration_unavailable','Network operations disabled by offline policy; configure an explicit allowlist and rerun with approval')
    url=string(ctx.request.get('url'),'url',maxlen=2048)
    try:
        parsed=urllib.parse.urlsplit(url)
        port=parsed.port
    except ValueError as exc:
        raise ToolError('invalid_input','Malformed HTTPS URL or port') from exc
    hosts=ctx.policy['download_allow_hosts']
    if parsed.scheme!='https' or parsed.username or parsed.password or port not in (None,443) or not parsed.hostname or parsed.hostname.lower() not in hosts:
        raise ToolError('not_allowed','Only allowlisted HTTPS hosts on port 443 without URL credentials are permitted')
    sha=string(ctx.request.get('sha256'),'sha256',maxlen=64).lower()
    if not re.fullmatch('[a-f0-9]{64}',sha):raise ToolError('invalid_input','sha256 must be 64 hex digits')
    dest=string(ctx.request.get('destination'),'destination');out=_new_dest(ctx,dest)
    bandwidth=integer(ctx.request.get('bandwidth_bytes_per_second',10485760),'bandwidth_bytes_per_second',1024,104857600)
    plan={'host':parsed.hostname,'path_sha256':digest(parsed.path + '?' + parsed.query),'destination':dest,
          'expected_sha256':sha,'max_bytes':ctx.limits['max_transfer_bytes'],
          'bandwidth_bytes_per_second':bandwidth,'deadline_seconds':ctx.limits['max_command_seconds']}
    pending=ctx.authorize(plan,'download allowlisted HTTPS resource with bounded size and checksum; no automatic execution')
    if pending:return pending
    class Guarded(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            p=urllib.parse.urlsplit(newurl)
            if p.scheme!='https' or p.hostname not in hosts or p.port not in (None,443) or p.username or p.password:
                raise ToolError('not_allowed','Redirect left HTTPS host allowlist')
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    opener=urllib.request.build_opener(Guarded)
    attempts=[];started=time.monotonic()
    for n in range(ctx.limits['max_retries']+1):
        if time.monotonic()-started > ctx.limits['max_command_seconds']:
            raise ToolError('timeout','Download exceeded overall deadline')
        fd,temp=tempfile.mkstemp(prefix='.workspace-download-',dir=out.parent)
        try:
            with os.fdopen(fd,'wb') as f,opener.open(urllib.request.Request(url,headers={'User-Agent':'workspace-control/1'}),timeout=10) as response:
                h=hashlib.sha256();size=0
                while chunk:=response.read(1024*1024):
                    size+=len(chunk)
                    if size>ctx.limits['max_transfer_bytes']:
                        raise ToolError('limit_exceeded','Download exceeded byte limit')
                    f.write(chunk);h.update(chunk)
                    elapsed=time.monotonic()-started
                    if elapsed > ctx.limits['max_command_seconds']:
                        raise ToolError('timeout','Download exceeded overall deadline')
                    wait=size/bandwidth-elapsed
                    if wait>0:time.sleep(min(wait,2))
            if h.hexdigest()!=sha:raise ToolError('verification_failed','Downloaded content does not match required SHA256')
            if out.exists():raise ToolError('collision','Download destination appeared')
            os.replace(temp,out)
            return {'status':'completed','destination':dest,'sha256':sha,'bytes':size,'attempts':n+1,'host':parsed.hostname}
        except (urllib.error.URLError,TimeoutError) as exc:
            if isinstance(exc,urllib.error.HTTPError) and exc.code not in (429,500,502,503,504):
                raise ToolError('network_failed','Non-retryable HTTPS response',host=parsed.hostname,status=exc.code) from exc
            attempts.append(type(exc).__name__)
            if n>=ctx.limits['max_retries']:
                raise ToolError('network_failed','Download failed after bounded retries',host=parsed.hostname,attempts=attempts) from exc
            time.sleep(min(2**n,4))
        finally:
            if os.path.exists(temp):os.unlink(temp)


def _git_init(ctx: Context) -> dict:
    if not shutil.which('git'):
        raise ToolError('integration_unavailable', 'git is not installed')
    name = string(ctx.request.get('destination', 'working/repository'), 'destination')
    dest = _new_dest(ctx, name)
    plan = {'destination': name, 'initial_branch': 'main', 'templates': 'disabled', 'network': 'none'}
    pending = ctx.authorize(plan, 'initialize a NEW local repository; no source files or global git settings altered')
    if pending: return pending
    result = ctx.safe_external(['git', 'init', '-q', '--initial-branch=main', '--template=', str(dest)],
                               ctx.limits['max_command_seconds'])
    if result['exit_code'] or not (dest / '.git').is_dir():
        if dest.is_dir() and not any(dest.iterdir()): dest.rmdir()
        raise ToolError('command_failed', 'Local git repository initialization failed', result=result)
    return {'status': 'completed', 'destination': name, 'git_directory': name + '/.git', 'result': result}


def _git_worktree(ctx: Context) -> dict:
    if not shutil.which('git'):raise ToolError('integration_unavailable','git is not installed')
    repo=string(ctx.request.get('repository'),'repository');branch=string(ctx.request.get('branch'),'branch',maxlen=80)
    dest=string(ctx.request.get('destination'),'destination')
    if not re.fullmatch('agent-[a-z0-9-]{1,70}',branch):raise ToolError('invalid_input','Branch must start with agent-')
    rp=ctx.path(repo,must_exist=True);dp=_new_dest(ctx,dest)
    if not rp.is_dir() or not (rp/'.git').exists():raise ToolError('invalid_input','repository needs an existing local .git')
    check=ctx.safe_external(['git','-C',str(rp),'rev-parse','--show-toplevel'],10)
    if check['exit_code'] or check['stdout'].strip()!=str(rp.resolve()):
        raise ToolError('invalid_input','Repository must be root of local git worktree')
    plan={'repository':repo,'branch':branch,'destination':dest,'head':ctx.safe_external(['git','-C',str(rp),'rev-parse','HEAD'],10)['stdout'].strip()}
    pending=ctx.authorize(plan,'create task-owned git branch and worktree; no merge or deletion')
    if pending:return pending
    result=ctx.safe_external(['git','-C',str(rp),'worktree','add','-b',branch,str(dp),'HEAD'],ctx.limits['max_command_seconds'])
    if result['exit_code'] or not dp.is_dir():
        raise ToolError('command_failed','Git worktree creation failed',result=result)
    return {'status':'completed','destination':dest,'branch':branch,'result':result}


def run(ctx: Context, variant: str) -> dict:
    handlers={'venv':_venv,'pip':_pip,'npm':_npm,'exec':_exec,'schedule':_schedule,'sqlite':_sqlite,
              'download':_download,'git-init':_git_init,'git-worktree':_git_worktree}
    if variant in handlers:return handlers[variant](ctx)
    if variant.startswith('service-'):return _service(ctx,variant)
    if variant in ('container-build','container-run','vm','transcode','render'):return _external(ctx,variant)
    if variant=='verify':
        exe=_venv_exe(ctx)
        return {'environment':ctx.request['environment'],'python':_local_process(ctx,[str(exe),'--version'],10),
                'packages':_local_process(ctx,[str(exe),'-I','-m','pip','list','--format=json'],10)}
    raise ToolError('internal_error','Unknown runtime operation',variant=variant)
