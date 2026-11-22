"""Portable system, runtime, resource, dependency and compatibility probes."""
from __future__ import annotations

import csv
import importlib.metadata
import json
import locale
import os
import platform
import re
import shutil
import socket
import struct
import sys
import time
import tomllib
from collections import Counter
from pathlib import Path
from workspace_core import Context, ToolError, file_sha, redact, string, strings

KNOWN_TOOLS = ('python3','python','node','npm','git','docker','podman','virsh','VBoxManage','ffmpeg','ffprobe',
               'libreoffice','soffice','gcc','clang','make','cmake','java','go','cargo','rustc','pytest','pip')


def _tools(ctx: Context) -> dict:
    requested = strings(ctx.request.get('tools', list(KNOWN_TOOLS)), 'tools', maximum=100)
    if any(not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.+-]{0,49}', name) for name in requested):
        raise ToolError('invalid_input', 'Tool names must be simple executable basenames')
    return {'tools': [{'name':n,'available':bool(shutil.which(n)), 'path':shutil.which(n)} for n in requested],
            'python': {'executable':sys.executable,'version':platform.python_version()},
            'note': 'Availability is PATH detection, not proof of a trusted binary, license or working integration.'}


def _resources(ctx: Context) -> dict:
    d = shutil.disk_usage(ctx.root)
    mem = {}
    if Path('/proc/meminfo').is_file():
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith(('MemTotal:', 'MemAvailable:', 'SwapFree:')):
                key, value = line.split(':',1)
                mem[key] = int(value.strip().split()[0]) * 1024
    cgroup = Path('/sys/fs/cgroup/memory.max')
    if cgroup.is_file():
        try:
            value = cgroup.read_text().strip()
            if value != 'max': mem['cgroup_memory_max'] = int(value)
            current = Path('/sys/fs/cgroup/memory.current')
            if current.is_file(): mem['cgroup_memory_current'] = int(current.read_text().strip())
        except (OSError,ValueError): pass
    memory_candidates=[n for n in (mem.get('MemAvailable'),
                         max(0, mem['cgroup_memory_max']-mem.get('cgroup_memory_current',0)) if 'cgroup_memory_max' in mem else None)
                       if n is not None]
    limits = {}
    try:
        import resource
        for name in ('RLIMIT_NOFILE','RLIMIT_NPROC','RLIMIT_AS','RLIMIT_CPU'):
            if hasattr(resource,name):
                soft,hard = resource.getrlimit(getattr(resource,name))
                limits[name] = {'soft':soft,'hard':hard}
    except ImportError: pass
    return {'disk': {'total':d.total,'used':d.used,'free':d.free},'memory_bytes':mem,
            'cpu_count':os.cpu_count(), 'limits':limits,
            'available_memory_estimate':min(memory_candidates) if memory_candidates else None,
            'note':'Memory and CPU measurements are host observations or cgroup hints, not cloud/API quotas.'}


def _gpu() -> dict:
    nodes = Path('/proc/driver/nvidia/gpus')
    detected = [p.name for p in nodes.iterdir()] if nodes.is_dir() else []
    return {'nvidia_devices_from_proc':detected, 'nvidia_smi_on_path':bool(shutil.which('nvidia-smi')),
            'note':'No GPU allocation or driver compatibility is inferred from PATH alone; CPU fallback recommended when no device is detected.'}


def _processes(ctx: Context) -> dict:
    items = []
    proc = Path('/proc')
    if proc.is_dir() and hasattr(os,'getuid'):
        for pid in list(proc.iterdir())[:20000]:
            if not pid.name.isdigit(): continue
            try:
                status = (pid/'status').read_text(encoding='utf-8',errors='replace')
                uid = re.search(r'^Uid:\s*(\d+)',status,re.M)
                if not uid or int(uid.group(1)) != os.getuid(): continue
                name = re.search(r'^Name:\s*(.*)$',status,re.M)
                ppid = re.search(r'^PPid:\s*(\d+)',status,re.M)
                rss = re.search(r'^VmRSS:\s*(\d+)',status,re.M)
                items.append({'pid':int(pid.name),'name':name.group(1) if name else '[unknown]',
                              'parent_pid':int(ppid.group(1)) if ppid else None,
                              'rss_bytes':int(rss.group(1))*1024 if rss else None})
                if len(items) >= ctx.limits['max_entries']: break
            except (OSError,ValueError): continue
    else:
        items = [{'pid':os.getpid(),'name':'workspace-tool','parent_pid':os.getppid(),'rss_bytes':None}]
    return {'same_user_processes':sorted(items,key=lambda x:x['pid']),
            'note':'Command arguments are withheld because they may contain secrets. Only task-owned jobs can be stopped.'}


def _requirements(ctx: Context) -> dict:
    names = ctx.request.get('paths', ['requirements.txt','pyproject.toml','package.json','package-lock.json','Cargo.toml','go.mod'])
    names = strings(names,'paths',maximum=50)
    files, findings = [], []
    for name in names:
        p = ctx.path(name)
        if not p.exists(): continue
        if not p.is_file() or p.stat().st_size > ctx.limits['max_text_bytes']:
            findings.append({'path':name,'issue':'not a bounded regular text file'}); continue
        text = ctx.read_text(name)
        entry = {'path':name,'sha256':file_sha(p),'format':p.suffix.lower(),'dependencies':[]}
        try:
            if p.name.startswith('requirements') and p.suffix == '.txt':
                deps = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
                for d in deps:
                    if d.startswith(('-e','-r','--','git+','http:','https:')) or '://' in d or '@' in d:
                        findings.append({'path':name,'issue':'nonlocal or indirect dependency requires manual trust review'})
                        entry['dependencies'].append('[nonlocal/indirect reference withheld]')
                    else:
                        entry['dependencies'].append(redact(d[:200]))
                entry['hash_pinned'] = bool(deps) and all('==' in x and '--hash=sha256:' in x for x in deps)
            elif p.name in ('pyproject.toml','Cargo.toml'):
                data = tomllib.loads(text)
                raw = data.get('project',{}).get('dependencies',[]) if p.name=='pyproject.toml' else list(data.get('dependencies',{}))
                entry['dependencies'] = [redact(x[:200]) if isinstance(x,str) and '://' not in x and '@' not in x else '[nonlocal reference withheld]' for x in raw]
            elif p.name in ('package.json','package-lock.json'):
                data = json.loads(text)
                raw = data.get('dependencies',{})
                entry['dependencies'] = {k:(redact(str(v)[:100]) if '://' not in str(v) and '@' not in str(v) else '[nonlocal reference withheld]')
                                         for k,v in raw.items()} if isinstance(raw,dict) else []
                if p.name=='package.json' and data.get('scripts'):
                    findings.append({'path':name,'issue':'package scripts exist; npm adapter disables lifecycle scripts'})
            elif p.name == 'go.mod':
                entry['dependencies'] = [line.strip() for line in text.splitlines() if line.lstrip().startswith('require ')]
        except (ValueError,TypeError,KeyError) as exc:
            findings.append({'path':name,'issue':'parse failed: '+redact(str(exc))[:160]})
        files.append(entry)
    return {'manifests':files,'findings':findings,
            'note':'Manifest inspection is static. Registry signatures, license trust, vulnerabilities and build scripts require external verification.'}


def _compat(ctx: Context) -> dict:
    rows, _ = ctx.entries(ctx.request_paths())
    collisions = {}
    for r in rows:
        key = r['path'].casefold()
        collisions.setdefault(key,[]).append(r['path'])
    hazards = []
    for r in rows:
        parts = Path(r['path']).parts
        if len(r['path']) > 240: hazards.append({'path':r['path'],'issue':'long path'})
        if any(x.upper().split('.')[0] in {'CON','PRN','AUX','NUL','COM1','LPT1'} for x in parts):
            hazards.append({'path':r['path'],'issue':'Windows reserved basename'})
        if r['kind']=='symlink': hazards.append({'path':r['path'],'issue':'symlink portability'})
    hazards += [{'paths':v,'issue':'case-insensitive path collision'} for v in collisions.values() if len(v)>1]
    return {'os':platform.system(),'release':platform.release(),'architecture':platform.machine(),
            'python':platform.python_version(), 'filesystem_encoding':sys.getfilesystemencoding(),
            'path_hazards':hazards, 'note':'Other operating systems and ABIs are not executed or tested here.'}


def _binary(ctx: Context) -> dict:
    names = ctx.request_paths(files=True)
    results=[]
    for name in names:
        with ctx.path(name, must_exist=True).open('rb') as f: head=f.read(64)
        if head[:4]==b'\x7fELF' and len(head)>=20:
            fmt='ELF'; machine=int.from_bytes(head[18:20],'little' if head[5]==1 else 'big')
            abi={'class':32 if head[4]==1 else 64,'machine_id':machine}
        elif head[:2]==b'MZ': fmt='PE/MZ'; abi={'machine_id':'requires PE header inspection'}
        elif head[:4] in (b'\xfe\xed\xfa\xce',b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe'):
            fmt='Mach-O'; abi={'architecture':'unverified'}
        else: fmt='unknown';abi={}
        results.append({'path':name,'format':fmt,'abi':abi,'sha256':file_sha(ctx.path(name),ctx.limits['max_transfer_bytes'])})
    return {'binaries':results,'note':'Magic and ELF machine ID only; signatures, dynamic libraries and binary trust are not verified.'}


def run(ctx: Context, variant: str) -> dict:
    if variant == 'tools': return _tools(ctx)
    if variant in ('disk','memory','cpu','fd','quota','contention','adaptive','resources','network'):
        r = _resources(ctx)
        if variant == 'disk':
            estimate = ctx.request.get('estimated_new_bytes',0)
            estimate = estimate if type(estimate) is int and estimate >= 0 else 0
            return {'disk':r['disk'],'estimated_new_bytes':estimate,
                    'fits_with_20_percent_headroom':r['disk']['free']-estimate >= max(100*1024*1024,r['disk']['total']//20)}
        if variant == 'memory': return {'memory_bytes':r['memory_bytes'],'recommended_batch_bytes':max(1048576,min(ctx.limits['max_file_bytes'], (r['available_memory_estimate'] or 67108864)//8)),
                                         'note':'Actual process RSS must be monitored during work.'}
        if variant == 'cpu': return {'cpu_count':r['cpu_count'],'recommended_workers':max(1,min(8,(r['cpu_count'] or 1)//2)), 'note':'Bound by workload and declared policy.'}
        if variant == 'fd':
            opened = len(list(Path('/proc/self/fd').iterdir())) if Path('/proc/self/fd').is_dir() else None
            return {'self_open_file_descriptors':opened,'limit':r['limits'].get('RLIMIT_NOFILE'),'note':'One-time snapshot; not a leak detector.'}
        if variant == 'quota': return {'process_limits':r['limits'],'disk':r['disk'],'unknown':['cloud','API','account','tenant quotas']}
        if variant == 'contention':
            processes = _processes(ctx)['same_user_processes']
            return {'same_user_processes':len(processes),'largest_rss':sorted(processes,key=lambda x:x.get('rss_bytes') or 0,reverse=True)[:10],
                    'note':'CPU/I/O contention cannot be proven from this snapshot.'}
        if variant == 'adaptive':
            return {'recommended_workers':max(1,min(8,(r['cpu_count'] or 1)//2)),
                    'batch_bytes':max(1048576,min(ctx.limits['max_file_bytes'],(r['available_memory_estimate'] or 67108864)//8)),
                    'retry_limit':ctx.limits['max_retries'], 'disk_free':r['disk']['free']}
        if variant == 'network':
            proxies = [k for k in os.environ if k.lower().endswith('_proxy')]
            return {'offline_policy':ctx.policy['offline'],'proxy_variable_names':sorted(proxies),
                    'local_hostname':socket.gethostname(),'download_allow_hosts':ctx.policy['download_allow_hosts'],
                    'note':'No public DNS, internet connectivity or credentials probed without authorization.'}
        return r
    if variant == 'gpu': return _gpu()
    if variant == 'processes': return _processes(ctx)
    if variant in ('requirements','dependencies','extras','provenance','lockfile'):
        data=_requirements(ctx)
        if variant == 'lockfile':
            locks=[x for x in data['manifests'] if 'lock' in x['path'].lower() or 'hash_pinned' in x]
            return {'lockfiles_or_pinned_manifests':locks,'findings':data['findings']}
        if variant == 'extras':
            return {'observed_manifests':data['manifests'],'recommendation':'Install only dependencies needed for the approved workflow; no package changes made.'}
        return data
    if variant in ('matrix','runtimes'):
        data = _compat(ctx); tools = _tools(ctx)
        return {**data,'tools':tools['tools'] if variant=='matrix' else [x for x in tools['tools'] if x['name'] in ('python3','python','node','npm','java','go','cargo')],
                'built_in_formats':['JSON','CSV','ZIP','SQLite','WAV','PNG signature','OOXML structure','XML structure'],
                'external_formats':'transcode/render and VM/container operations require installed adapters and explicit approval'}
    if variant == 'locale':
        return {'locale':locale.setlocale(locale.LC_CTYPE,None),'preferred_encoding':locale.getpreferredencoding(False),
                'timezone':time.tzname,'filesystem_encoding':sys.getfilesystemencoding(),
                'recommendation':{'TZ':'UTC','PYTHONHASHSEED':'0','encoding':'UTF-8'}}
    if variant == 'seed':
        return {'PYTHONHASHSEED_present':'PYTHONHASHSEED' in os.environ,
                'recommended_seed':ctx.request.get('seed',0), 'uncontrolled':['external services','hardware','parallel scheduling','wall clock']}
    if variant == 'isolation':
        return {'uid':os.getuid() if hasattr(os,'getuid') else None,'root_mode':oct(ctx.root.stat().st_mode & 0o777),
                'root_writable':os.access(ctx.root,os.W_OK),'docker_socket_present':Path('/var/run/docker.sock').exists(),
                'inside_container_hint':Path('/.dockerenv').exists(),'offline_policy':ctx.policy['offline'],
                'note':'This tool is not a security sandbox. Host mounts, ACLs and network enforcement require the host/container platform.'}
    if variant == 'env-secrets':
        names = [k for k in os.environ if re.search(r'(?i)(token|secret|password|key|credential)',k)]
        return {'sensitive_variable_names':sorted(names),'values_returned':False,
                'note':'Credential scope, rotation, expiry and incident response depend on the provider and are not inferred.'}
    if variant == 'port':
        port=ctx.request.get('port')
        if type(port) is not int or not 1<=port<=65535:
            raise ToolError('invalid_input','port must be an integer from 1 to 65535')
        s=socket.socket();s.settimeout(2)
        try:
            s.bind(('127.0.0.1',port))
            available=True
        except OSError:
            available=False
        finally:s.close()
        return {'port':port,'loopback_available_at_probe_time':available,
                'note':'Probe does not reserve port; a future service may collide. No external interface opened.'}
    if variant == 'compat': return _compat(ctx)
    if variant == 'binary': return _binary(ctx)
    raise ToolError('internal_error','Unknown system inspection variant', variant=variant)
