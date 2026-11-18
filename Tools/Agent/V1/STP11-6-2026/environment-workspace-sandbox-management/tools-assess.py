"""Evidence-backed plans for consequential or provider-specific workspace decisions."""
from __future__ import annotations

import os
import re
import shutil
import stat
import time
from collections import Counter
from pathlib import Path
from workspace_core import Context, ToolError, string, strings
from workspace_inspect import _classify, _cleanup, _dependency_refs, _layout, _secrets
from workspace_system import KNOWN_TOOLS

GROUPS={
    'organization': {'layout','simplify','naming','output','collaboration','review','lifecycle','formats'},
    'environment': {'isolation','environment','offline','fallback','tool-route','retirement','limitations','package-rollback','incremental-build','build','cross-build','conversion'},
    'access': {'access','least-privilege','readonly','service-account','sensitive-boundaries'},
    'secrets': {'credential-incident','credential-lifecycle','credential-scope','containment'},
    'network': {'certificates','proxy','local-network','network-failure','container-network','container-volumes'},
    'capacity': {'large-data','incremental','dormant'},
    'governance': {'corruption','environment-rollback','logging','signing','shell','shim','system-package','untrusted','compat-report'},
}
COVERED=set().union(*GROUPS.values())


def _overview(ctx: Context) -> tuple[list[dict],dict]:
    rows,stats=ctx.entries(ctx.request_paths())
    return rows, {'files':sum(x['kind']=='file' for x in rows),
                  'file_bytes':sum(x['size'] for x in rows if x['kind']=='file'),
                  'directories':sum(x['kind']=='directory' for x in rows),
                  'symlinks':sum(x['kind']=='symlink' for x in rows),'scan':stats}


def _organization(ctx: Context, v: str, rows: list[dict]) -> dict:
    known=_layout(ctx)['folders']
    top={x['path'].split('/')[0] for x in rows}
    missing=[x for x in known if x not in top]
    deep=[x['path'] for x in rows if x['path'].count('/')>=7]
    names=[x['path'] for x in rows if re.search('[^a-z0-9./-]',x['path'])]
    cases={}
    for x in rows:cases.setdefault(x['path'].casefold(),[]).append(x['path'])
    collisions=[x for x in cases.values() if len(x)>1]
    sensitive=[x['path'] for x in rows if ctx.protected(x['path'])]
    dupdirs=[x['path'] for x in rows if x['kind']=='directory' and any(t in x['path'].lower() for t in ('copy of','old-old','backup-backup'))]
    evidence={'top_level':sorted(top),'missing_layout_folders':missing,'deep_paths':deep[:100],
              'nonportable_names':names[:100],'case_collisions':collisions[:100],
              'protected_paths':sensitive[:100],'ambiguous_directories':dupdirs[:100]}
    if v=='naming':
        recommendations=['Review nonportable and case-colliding names; supply explicit rename pairs to batch-move-and-copy-operator.',
                         'Preserve old-to-new mapping in the transfer journal; never rename an original upload.']
        findings=[{'issue':'naming-candidates','count':len(names)+len(collisions)}]
    elif v in ('output','review'):
        finals=[r['path'] for r in rows if r['path'].split('/')[0] in ('deliverables','final')]
        evidence['final_paths']=finals[:200]
        findings=[{'issue':'temporary-under-final','paths':[x for x in finals if x.endswith(('.tmp','.log','.cache','.part'))]}]
        recommendations=['Provide exact expected deliverable paths to deliverable-completeness-auditor and secure-workspace-release-auditor.',
                         'Keep candidates, reviewed, approved and final versions separate; promote only verified copies.']
    elif v=='formats':
        evidence['formats']=dict(Counter(Path(x['path']).suffix.lower() or '[none]' for x in rows if x['kind']=='file'))
        findings=[];recommendations=['Preserve source and editable format; archive widely supported text/ZIP/PDF formats alongside originals.',
                                      'Test archive integrity and recipient reopen path before retiring original software.']
    elif v=='collaboration':
        findings=[{'issue':'shared-root-permissions','paths':[r['path'] for r in rows if int(r['mode'],8)&0o002][:50]}]
        recommendations=['Assign each contributor a distinct worktree or working directory and exclusive output names.',
                         'Use file-lock-and-concurrency-manager for cooperative locks; require integration checks before promoting output.']
    elif v=='lifecycle':
        findings=[];recommendations=['Capture a state snapshot, archive approved sources and outputs, verify restore, then review retention candidates.',
                                      'Do not purge originals or backups without policy approval and a verified restore point.']
    else:
        findings=[{'issue':'missing-layout-folders','paths':missing},{'issue':'excessive-nesting','count':len(deep)},
                  {'issue':'ambiguous-directories','paths':dupdirs}]
        recommendations=['Use workspace-layout-architect to create only reviewed missing directories.',
                         'Use reversible copy/move transactions to consolidate; protected originals remain unchanged.']
    return {'evidence':evidence,'findings':findings,'recommendations':recommendations}


def _environment(ctx: Context, v: str, rows: list[dict]) -> dict:
    tools={x:bool(shutil.which(x)) for x in KNOWN_TOOLS}
    manifest=[x['path'] for x in rows if Path(x['path']).name in ('requirements.txt','pyproject.toml','package.json','package-lock.json','Dockerfile','Cargo.toml','go.mod')]
    lock=[x for x in manifest if 'lock' in x or 'requirements' in x]
    evidence={'tools_available':{k:w for k,w in tools.items() if w},'required_manifests':manifest,
              'lock_or_pinned_files':lock,'offline_policy':ctx.policy['offline'],
              'docker_opt_in':ctx.policy['enable_container_adapter'],'root_writable':os.access(ctx.root,os.W_OK)}
    findings=[]; recommendations=[]
    if v in ('tool-route','fallback'):
        wanted=ctx.request.get('preferred')
        if wanted is not None:
            wanted=string(wanted,'preferred',maxlen=50)
            if wanted not in tools:raise ToolError('invalid_input','preferred must be a recognized tool basename')
        extension=ctx.request.get('format')
        if extension is not None:extension=string(extension,'format',maxlen=20).lower()
        builtin=extension in ('.json','.csv','.zip','.sqlite','.wav','.png','.docx','.pptx','.xlsx','.pdf')
        evidence.update(preferred=wanted,preferred_available=tools.get(wanted),format=extension,
                        stdlib_inspection_available=builtin)
        recommendations.append('Choose read-only stdlib inspection first for recognized formats; use approved external adapter only when actual rendering, transcoding or execution is needed.')
        if wanted and not tools[wanted]:findings.append({'issue':'preferred-tool-missing','tool':wanted})
    elif v=='offline':
        recommendations=['Collect pinned requirements and local wheelhouse/package cache; use the offline pip/npm adapters with hashes and scripts disabled.',
                         'If a trusted host is approved, set an explicit HTTPS allowlist and checksum before download.']
    elif v in ('build','incremental-build','cross-build','conversion'):
        findings.append({'issue':'no-declared-build-profile' if not ctx.policy['trusted_profiles'] else 'build-profile-required',
                         'profiles':list(ctx.policy['trusted_profiles'])})
        recommendations=['Capture compiler/runtime versions, locale, inputs and build graph; execute only a configured trusted profile with a bounded timeout.',
                         'Compare output hashes from a clean-state rebuild; cross-platform success is not inferred from local tests.']
    elif v=='retirement':
        findings.append({'issue':'unversioned-tools','tools':[k for k,w in tools.items() if w][:30]})
        recommendations=['Pin runtime versions and retain tested original outputs; migrate using a clean rebuild and compare artifact structure and behavior.']
    elif v=='package-rollback':
        recommendations=['Restore the previous isolated environment snapshot or recreate a new venv from the last verified lockfile; never edit global packages here.']
    elif v=='isolation':
        recommendations=['Prefer a fresh Python venv or copy-only staging; use a non-root container only when Docker and host policy permit.',
                         'This CLI cannot enforce kernel isolation or contain an untrusted executable by itself.']
    else:
        recommendations=['Record manifests and version pins; start in a disposable workspace with no network or shared global mutation.',
                         'Choose a tested adapter only if an authorized runtime is present and explicit approval is supplied.']
    return {'evidence':evidence,'findings':findings,'recommendations':recommendations}


def _access(ctx: Context, v: str, rows: list[dict]) -> dict:
    broad=[{'path':r['path'],'mode':r['mode']} for r in rows if r['kind'] in ('file','directory') and int(r['mode'],8)&0o007]
    writable=[r['path'] for r in rows if r['kind']=='file' and int(r['mode'],8)&0o020]
    protected=[r['path'] for r in rows if ctx.protected(r['path'])]
    evidence={'broad_access':broad[:100],'group_writable_files':writable[:100],
              'protected_source_paths':protected[:100], 'root_mode':oct(ctx.root.stat().st_mode&0o777)}
    recommendations=['Use least privilege; apply only scoped 600/700 or narrower modes with permission-repair-specialist and review the rollback journal.',
                     'Never elevate privileges, chown, edit host mount table or modify another account via this tool.']
    if v=='readonly':recommendations.append('Read-only mounts require host/container enforcement; this tool preserves originals with copy-only staging.')
    if v=='service-account':recommendations.append('Service account identities and ACLs must be provisioned by the host administrator; keep task-owned logs and outputs isolated.')
    if v=='sensitive-boundaries':recommendations.append('Separate public and confidential paths; pass explicit allowed paths for data and secret audits.')
    return {'evidence':evidence,'findings':[{'issue':'broad-permissions','count':len(broad)+len(writable)}],
            'recommendations':recommendations}


def _secrets_group(ctx: Context,v:str,rows:list[dict]) -> dict:
    scan=_secrets(ctx,rows)
    names=[x for x in os.environ if re.search('(?i)(token|secret|key|password|credential)',x)]
    steps=['Stop exporting exposed artifacts, identify affected paths without copying values, and preserve a restricted incident record.',
           'Revoke/rotate credentials with the actual provider; then audit outputs, logs and archives again.']
    if v=='credential-scope':steps=['Confirm scopes, audience and expiry through the provider, not from variable names; keep credentials out of argv and logs.']
    if v=='credential-lifecycle':steps=['Use provider-managed short-lived credentials and pass only named environment variables to an approved runtime profile.',
                                         'Expire/revoke in provider after task; this tool cannot issue or rotate external credentials.']
    if v=='containment':steps=['Preserve checksums of suspect files, quarantine only approved local copies, block further delivery, and run the release audit.']
    return {'evidence':{'heuristic_secret_findings':scan['findings'],'sensitive_variable_names':sorted(names),
                        'values_returned':False},'findings':[{'issue':'potential-secret-leak','count':len(scan['findings'])}],
            'recommendations':steps,'unavailable':'Provider-side revocation, rotation and scope verification require their own authorized integration.'}


def _network(ctx: Context,v:str,rows:list[dict]) -> dict:
    certs=[r['path'] for r in rows if Path(r['path']).suffix.lower() in ('.crt','.pem','.p12','.pfx')]
    evidence={'offline_policy':ctx.policy['offline'],'download_allow_hosts':ctx.policy['download_allow_hosts'],
              'proxy_variable_names':sorted(k for k in os.environ if k.lower().endswith('_proxy')),
              'certificate_files':certs[:100], 'docker_available':bool(shutil.which('docker'))}
    limitations={'certificates':'Certificate trust-store and local hosts changes require an external, approved administrator tool.',
                 'proxy':'Proxy values may include secrets; no system or application proxy settings changed.',
                 'container-volumes':'Docker volume lifecycle requires a configured runtime and explicit volume ownership policy.',
                 'container-network':'Docker network changes require a configured runtime and host authorization.',
                 'network-failure':'Latency/loss simulation requires an isolated test network; host routing and firewall are not changed.',
                 'local-network':'No host route, bridge, firewall or public listener is opened.'}
    return {'evidence':evidence,'findings':[],
            'recommendations':['Keep tests on loopback or no-network by default; inventory required ports, mounts and cleanup steps.',
                               'Use an approved adapter only for a predeclared, reversible scope.'],
            'unavailable':limitations[v]}


def _capacity(ctx: Context,v:str,rows:list[dict]) -> dict:
    disk=shutil.disk_usage(ctx.root)
    files=sorted((r for r in rows if r['kind']=='file'),key=lambda x:x['size'],reverse=True)
    total=sum(r['size'] for r in files)
    older=[r['path'] for r in files if time.time()-r['modified_ns']/1e9>ctx.policy['retention_days']*86400]
    evidence={'disk_free_bytes':disk.free,'workspace_scanned_bytes':total,'largest_files':files[:20],
              'outside_retention_age_count':len(older)}
    recommendations=['Stream large files in chunks with bounded buffers, preserve originals, and reserve at least 20% disk headroom before copy/build/archive.',
                     'Mark scratch ownership and verify archive/restore before considering old caches for quarantine.']
    if v=='dormant':recommendations.append('Aged files are only review candidates; last access, service use and license status cannot be inferred from mtimes.')
    return {'evidence':evidence,'findings':[{'issue':'insufficient-headroom','active':disk.free<max(100*1024*1024,disk.total//20)}],
            'recommendations':recommendations}


def _governance(ctx: Context,v:str,rows:list[dict]) -> dict:
    available={x:bool(shutil.which(x)) for x in ('apt','dnf','brew','winget','gpg','openssl','qemu-system-x86_64','git')}
    backups=[r['path'] for r in rows if r['path'].split('/')[0]=='backups']
    evidence={'available_external_tools':available,'backups':backups[:100],
              'symlinks':[r['path'] for r in rows if r['kind']=='symlink'][:100],
              'platform':os.name,'config_profiles':list(ctx.policy['trusted_profiles'])}
    specific={
       'system-package':'System package installation changes global/privileged state; this tool deliberately never invokes apt/dnf/brew/winget.',
       'signing':'No signing key or certificate is generated, read or used; a trusted signing service with explicit approval is required.',
       'untrusted':'Static inspection is not safe execution; use a real host-provided sandbox for untrusted binaries.',
       'corruption':'Corrupt workspace recovery is limited to verified copies, archives and transaction journals; no hidden filesystem repair.',
       'environment-rollback':'Recreate isolated environment from a pinned manifest or restore verified backup; host/global runtime rollback unavailable.',
       'shell':'Commands use explicit python3 invocation and no shell interpolation; other shells are not executed here.',
       'shim':'Compatibility shims must be reviewed and tested in their target runtime; none are automatically generated.',
       'logging':'Use structured, redacted events and a hash chain; integrity hash chains do not provide authenticated nonrepudiation.',
       'compat-report':'Local platform findings do not certify another OS/ABI.',
    }
    return {'evidence':evidence,'findings':[],
            'recommendations':['Capture a before snapshot, verify inputs and constraints, run only explicitly approved bounded operations, then verify results and recovery path.'],
            'unavailable':specific[v]}


def run(ctx: Context,variant: str) -> dict:
    if variant not in COVERED:
        raise ToolError('internal_error','Unknown assessment route',variant=variant)
    rows,summary=_overview(ctx)
    group=next(k for k,vs in GROUPS.items() if variant in vs)
    evaluator={'organization':_organization,'environment':_environment,'access':_access,
               'secrets':_secrets_group,'network':_network,'capacity':_capacity,'governance':_governance}[group]
    result=evaluator(ctx,variant,rows)
    return {'status':'assessment_only','focus':variant,'scope':summary,'evidence':result['evidence'],
            'findings':result['findings'],'recommendations':result['recommendations'],
            **({'unavailable_integration':result['unavailable']} if 'unavailable' in result else {}),
            'note':'Evidence-backed advisory result. No external or privileged change was performed.'}
