"""Approved file transactions, quarantine/rollback, archives and safe intake."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path
from workspace_core import Context, ToolError, canonical, digest, file_sha, integer, load_json, redact, string, strings, utc


def _parent(ctx: Context, path: Path, *, create: bool = False) -> None:
    cur = ctx.root
    for part in path.parent.relative_to(ctx.root).parts:
        cur = cur / part
        if cur.is_symlink():
            raise ToolError('unsafe_path', 'Output parent is a symlink', path=ctx.relative(cur))
        if cur.exists() and not cur.is_dir():
            raise ToolError('collision', 'A parent component is not a directory', path=ctx.relative(cur))
        if create and not cur.exists():
            cur.mkdir(mode=0o700)
    if not create and not path.parent.is_dir():
        raise ToolError('missing_input', 'Output parent must exist', path=ctx.relative(path.parent))


def _journal(ctx: Context, kind: str, actions: list[dict]) -> tuple[str, Path]:
    ctx.ensure_state()
    ident = secrets.token_hex(8)
    path = ctx.state_file('transaction-' + ident + '.json')
    ctx.atomic_bytes(path, (json.dumps({'id': ident, 'kind': kind, 'created': utc(), 'status': 'prepared', 'actions': actions}, indent=2) + '\n').encode())
    return ident, path


def _set_journal(ctx: Context, path: Path, status: str, actions: list[dict], **extra) -> None:
    old = load_json(path)
    old.update({'status': status, 'actions': actions, 'updated': utc(), **extra})
    ctx.atomic_bytes(path, (json.dumps(old, indent=2) + '\n').encode(), overwrite=True)


def _verified_copy(ctx: Context, src: Path, dst: Path, expected: str, *, preserve: bool = True) -> None:
    _parent(ctx, dst, create=True)
    if dst.exists() or dst.is_symlink():
        raise ToolError('collision', 'Copy destination already exists', path=ctx.relative(dst))
    fd, temp = tempfile.mkstemp(prefix='.workspace-copy-', dir=dst.parent)
    try:
        with os.fdopen(fd, 'wb') as out, src.open('rb') as inp:
            shutil.copyfileobj(inp, out, 1024 * 1024)
            out.flush(); os.fsync(out.fileno())
        if file_sha(Path(temp), ctx.limits['max_transfer_bytes']) != expected:
            raise ToolError('changed_input', 'Source changed while copying', path=ctx.relative(src))
        if preserve:
            shutil.copystat(src, temp, follow_symlinks=False)
        if dst.exists() or dst.is_symlink():
            raise ToolError('collision', 'Output appeared during copy', path=ctx.relative(dst))
        os.replace(temp, dst)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _transfer_request(ctx: Context) -> list[dict]:
    entries = ctx.request.get('entries')
    if entries is None and all(k in ctx.request for k in ('source', 'destination')):
        entries = [{k: ctx.request[k] for k in ('source', 'destination')} | {'action': ctx.request.get('action', 'copy')}]
    if not isinstance(entries, list) or not entries or len(entries) > 200:
        raise ToolError('invalid_input', 'Provide 1 to 200 entries with source, destination and copy/move action')
    result, destinations, sources = [], set(), set()
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ToolError('invalid_input', 'Entry must be an object', index=idx)
        name = string(entry.get('source'), 'source'); target = string(entry.get('destination'), 'destination')
        action = entry.get('action', 'copy')
        if action not in ('copy', 'move'):
            raise ToolError('invalid_input', 'Only copy and move are supported', action=action)
        src = ctx.path(name, must_exist=True); dest = ctx.path(target)
        ctx.require_mutable(target, allow_final_new=True)
        if action == 'move':
            ctx.require_mutable(name, source=True)
        if not src.is_file() or src == dest:
            raise ToolError('invalid_input', 'Transfer source must be a distinct regular file', source=name)
        if src.stat().st_size > ctx.limits['max_transfer_bytes']:
            raise ToolError('limit_exceeded', 'Transfer file exceeds limit', source=name)
        if target in destinations or name in sources or target in sources or name in destinations:
            raise ToolError('collision', 'Overlapping source or destination in transaction', path=target)
        sources.add(name); destinations.add(target)
        _parent(ctx, dest)
        conflict = ctx.request.get('on_conflict', 'error')
        if conflict not in ('error', 'skip', 'replace'):
            raise ToolError('invalid_input', 'on_conflict must be error, skip or replace')
        source_hash = file_sha(src, ctx.limits['max_transfer_bytes'])
        current = file_sha(dest, ctx.limits['max_transfer_bytes']) if dest.is_file() else None
        if dest.exists() and not dest.is_file():
            raise ToolError('collision', 'Destination is not a regular file', destination=target)
        if dest.exists() and conflict == 'error':
            raise ToolError('collision', 'Destination exists; select skip or replace explicitly', destination=target)
        result.append({'source': name, 'destination': target, 'action': action, 'sha256': source_hash,
                       'bytes': src.stat().st_size, 'existing_sha256': current,
                       'decision': 'skip' if current is not None and conflict == 'skip' else 'replace' if current is not None else 'create'})
    return result


def _transfer(ctx: Context) -> dict:
    plan = {'entries': _transfer_request(ctx), 'on_conflict': ctx.request.get('on_conflict', 'error')}
    pending = ctx.authorize(plan, 'copy/move/replace files; original inputs can only be copied')
    if pending:
        return pending
    ident, journal = _journal(ctx, 'transfer', [])
    backup_root = ctx.ensure_state() / 'backups' / ident
    backup_root.mkdir(parents=True, mode=0o700, exist_ok=False)
    completed = []
    try:
        for n, item in enumerate(plan['entries']):
            if item['decision'] == 'skip':
                completed.append({**item, 'state': 'skipped'}); _set_journal(ctx, journal, 'running', completed); continue
            src = ctx.path(item['source'], must_exist=True); dst = ctx.path(item['destination'])
            if file_sha(src, ctx.limits['max_transfer_bytes']) != item['sha256']:
                raise ToolError('changed_input', 'Source changed after approval', source=item['source'])
            if item['existing_sha256']:
                if not dst.is_file() or file_sha(dst, ctx.limits['max_transfer_bytes']) != item['existing_sha256']:
                    raise ToolError('changed_input', 'Destination changed after approval', destination=item['destination'])
            elif dst.exists():
                raise ToolError('collision', 'Destination appeared after approval', destination=item['destination'])
            before = backup_root / (str(n) + '-destination') if item['existing_sha256'] else None
            original = backup_root / (str(n) + '-source') if item['action'] == 'move' else None
            if before:
                _verified_copy(ctx, dst, before, item['existing_sha256'])
            if original:
                _verified_copy(ctx, src, original, item['sha256'])
            if dst.exists():
                # Only after a verified backup, allow atomic replacement.
                temp = dst.parent / ('.workspace-old-' + ident + '-' + str(n))
                os.replace(dst, temp)
                try:
                    _verified_copy(ctx, src, dst, item['sha256'])
                except Exception:
                    os.replace(temp, dst)
                    raise
                temp.unlink()
            else:
                _verified_copy(ctx, src, dst, item['sha256'])
            if item['action'] == 'move':
                src.unlink()
            done = {**item, 'state': 'done', 'backup_destination': ctx.relative(before) if before else None,
                    'backup_source': ctx.relative(original) if original else None}
            completed.append(done); _set_journal(ctx, journal, 'running', completed)
        _set_journal(ctx, journal, 'committed', completed)
    except Exception as exc:
        _set_journal(ctx, journal, 'interrupted', completed, failure=redact(str(exc)))
        raise ToolError('transaction_interrupted', 'Transaction interrupted; verified completed actions are journaled. Run file-operation-recovery-specialist with transaction_id.',
                        transaction_id=ident) from exc
    return {'status': 'completed', 'transaction_id': ident, 'entries': completed,
            'recovery': 'Run file-operation-recovery-specialist with {"transaction_id":"' + ident + '"} for a reviewed rollback.'}


def _layout(ctx: Context) -> dict:
    default = load_json(Path(__file__).with_name('asset-layout.json'))['folders']
    names = strings(ctx.request.get('folders', default), 'folders', maximum=100)
    if not names:
        raise ToolError('invalid_input', 'folders cannot be empty')
    missing = []
    for name in names:
        p = ctx.path(name)
        if p.exists() and not p.is_dir():
            raise ToolError('collision', 'Layout path exists as a file', path=name)
        if not p.exists():
            missing.append(name)
    plan = {'create': sorted(set(missing)), 'already_present': sorted(set(names)-set(missing))}
    if not missing:
        return {'status': 'unchanged', **plan}
    pending = ctx.authorize(plan, 'create workspace directories')
    if pending:
        return pending
    for name in plan['create']:
        _parent(ctx, ctx.path(name), create=True)
        ctx.path(name).mkdir(mode=0o700, exist_ok=True)
    return {'status': 'completed', **plan}


def _scratch(ctx: Context) -> dict:
    label = string(ctx.request.get('label'), 'label', maxlen=64)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', label):
        raise ToolError('invalid_input', 'Scratch label must be lowercase alphanumeric with hyphens')
    quota = integer(ctx.request.get('quota_bytes', ctx.limits['max_transfer_bytes']), 'quota_bytes', 1, ctx.limits['max_transfer_bytes'])
    name = 'scratch/' + label
    p = ctx.path(name)
    if p.exists():
        marker = p / '.workspace-owner'
        if marker.is_file():
            return {'status': 'unchanged', 'path': name, 'marker': load_json(marker)}
        raise ToolError('collision', 'Scratch directory already exists but is not tool-owned', path=name)
    plan = {'create': name, 'quota_bytes': quota}
    pending = ctx.authorize(plan, 'create isolated scratch directory')
    if pending:
        return pending
    _parent(ctx, p, create=True)
    p.mkdir(mode=0o700)
    ctx.atomic_bytes(p / '.workspace-owner', (json.dumps({'created': utc(), 'quota_bytes': quota, 'purpose': ctx.request.get('purpose', label)}, indent=2)+'\n').encode())
    return {'status': 'completed', 'path': name, 'quota_bytes': quota}


def _write(ctx: Context) -> dict:
    name = string(ctx.request.get('path'), 'path')
    content = string(ctx.request.get('content'), 'content', nonempty=False, maxlen=ctx.limits['max_text_bytes'])
    dest = ctx.path(name); ctx.require_mutable(name); _parent(ctx, dest)
    if dest.exists() and not dest.is_file():
        raise ToolError('collision', 'Output is not a regular file', path=name)
    old_hash = file_sha(dest, ctx.limits['max_transfer_bytes']) if dest.exists() else None
    new_bytes = content.encode('utf-8')
    if len(new_bytes) > ctx.limits['max_text_bytes']:
        raise ToolError('limit_exceeded', 'UTF-8 content exceeds max_text_bytes')
    new_hash = digest(new_bytes)
    if old_hash == new_hash:
        return {'status': 'unchanged', 'path': name, 'sha256': new_hash}
    if old_hash and ctx.request.get('on_conflict') != 'replace':
        raise ToolError('collision', 'Set on_conflict to replace and review approval before replacing', path=name)
    plan = {'write': name, 'bytes': len(new_bytes), 'sha256': new_hash, 'previous_sha256': old_hash}
    pending = ctx.authorize(plan, 'atomic write; replacement retains a verified backup')
    if pending:
        return pending
    ident, journal = _journal(ctx, 'write', [])
    backup = None
    if old_hash:
        backup = ctx.ensure_state() / 'backups' / ident / 'previous'
        backup.parent.mkdir(parents=True)
        _verified_copy(ctx, dest, backup, old_hash)
    ctx.atomic_bytes(dest, new_bytes, overwrite=bool(old_hash))
    action = {'destination': name, 'sha256': new_hash, 'previous_sha256': old_hash, 'backup_destination': ctx.relative(backup) if backup else None,
              'state': 'done'}
    _set_journal(ctx, journal, 'committed', [action])
    return {'status': 'completed', 'path': name, 'sha256': new_hash, 'transaction_id': ident}


def _quarantine(ctx: Context) -> dict:
    if ctx.request.get('purge'):
        ident = string(ctx.request.get('transaction_id'), 'transaction_id', maxlen=32)
        if not re.fullmatch('[0-9a-f]{16}', ident):
            raise ToolError('invalid_input', 'Invalid quarantine transaction ID')
        journal = ctx.state / ('transaction-' + ident + '.json')
        if not journal.is_file():
            raise ToolError('missing_input', 'Unknown quarantine transaction')
        data = load_json(journal)
        if data.get('kind') != 'quarantine' or data.get('status') != 'committed':
            raise ToolError('invalid_input', 'Only committed quarantine transactions can be purged')
        created = dt.datetime.fromisoformat(data['created'])
        if (dt.datetime.now(dt.timezone.utc) - created).days < ctx.policy['retention_days']:
            raise ToolError('retention_hold', 'Quarantine retention period has not elapsed', retention_days=ctx.policy['retention_days'])
        files = [ctx.path(a['quarantine'], must_exist=True, internal=True) for a in data['actions']]
        plan = {'purge_transaction': ident, 'files': [{'path': ctx.relative(f), 'sha256': file_sha(f)} for f in files]}
        pending = ctx.authorize(plan, 'PERMANENT deletion of previously quarantined files')
        if pending:
            return pending
        for f in files:
            f.unlink()
        _set_journal(ctx, journal, 'purged', data['actions'])
        return {'status': 'completed', 'purged': len(files), 'transaction_id': ident}
    names = strings(ctx.request.get('paths'), 'paths', maximum=200)
    if not names:
        raise ToolError('invalid_input', 'Provide explicit paths for quarantine')
    items = []
    for name in names:
        source = ctx.path(name, must_exist=True)
        if not source.is_file():
            raise ToolError('invalid_input', 'Only regular files may be quarantined', path=name)
        size = source.stat().st_size
        if size > ctx.limits['max_transfer_bytes']:
            raise ToolError('limit_exceeded', 'File too large to quarantine', path=name)
        items.append({'path': name, 'bytes': size, 'sha256': file_sha(source),
                      'mode': 'copy-preserving-original' if ctx.protected(name) else 'move-with-rollback'})
    if len(set(names)) != len(names):
        raise ToolError('invalid_input', 'Duplicate path in quarantine list')
    plan = {'items': items, 'retention_days': ctx.policy['retention_days']}
    pending = ctx.authorize(plan, 'quarantine files; original source-of-truth files are copied, never deleted')
    if pending:
        return pending
    ident, journal = _journal(ctx, 'quarantine', [])
    done = []
    try:
        for item in items:
            source = ctx.path(item['path'], must_exist=True)
            if file_sha(source) != item['sha256']:
                raise ToolError('changed_input', 'Quarantine input changed', path=item['path'])
            target = ctx.ensure_state() / 'quarantine' / ident / item['path']
            _verified_copy(ctx, source, target, item['sha256'])
            if item['mode'] == 'move-with-rollback':
                source.unlink()
            done.append({**item, 'quarantine': ctx.relative(target), 'state': 'done'})
            _set_journal(ctx, journal, 'running', done)
        _set_journal(ctx, journal, 'committed', done)
    except Exception as exc:
        _set_journal(ctx, journal, 'interrupted', done, failure=redact(str(exc)))
        raise ToolError('transaction_interrupted', 'Quarantine interrupted; use transaction_id to inspect and roll back', transaction_id=ident) from exc
    return {'status': 'completed', 'transaction_id': ident, 'items': done}


def _rollback(ctx: Context) -> dict:
    ident = string(ctx.request.get('transaction_id'), 'transaction_id', maxlen=32)
    if not re.fullmatch(r'[0-9a-f]{16}', ident):
        raise ToolError('invalid_input', 'Invalid transaction ID')
    journal = ctx.state / ('transaction-' + ident + '.json')
    if not journal.is_file() or journal.is_symlink():
        raise ToolError('missing_input', 'Transaction journal not found')
    data = load_json(journal)
    if data.get('kind') not in ('transfer', 'quarantine', 'write', 'chmod', 'metadata', 'assemble') or data.get('status') not in ('committed','interrupted','running'):
        raise ToolError('invalid_input', 'Transaction is not eligible for rollback')
    actions = [x for x in data['actions'] if x.get('state') == 'done']
    checks = []
    for action in reversed(actions):
        if data['kind'] == 'quarantine':
            f = ctx.path(action['quarantine'], must_exist=True, internal=True)
            if file_sha(f) != action['sha256']:
                raise ToolError('changed_input', 'Quarantine copy changed; manual recovery required', path=action['quarantine'])
            if action['mode'] == 'move-with-rollback' and ctx.path(action['path']).exists():
                raise ToolError('collision', 'Original path is occupied; manual recovery required', path=action['path'])
            checks.append({'restore': action['path'], 'from': action['quarantine']})
        elif data['kind'] in ('chmod', 'metadata'):
            f = ctx.path(action['path'], must_exist=True)
            if f.stat().st_mode & 0o777 != int(action['new_mode'], 8):
                raise ToolError('changed_input', 'Permissions changed after transaction', path=action['path'])
            if data['kind'] == 'metadata' and (f.stat().st_mtime_ns != action['new_mtime_ns'] or file_sha(f) != action['sha256']):
                raise ToolError('changed_input', 'File changed since metadata normalization', path=action['path'])
            checks.append({'restore_metadata': action['path'], 'mode': action['old_mode'],
                           'mtime_ns': action.get('old_mtime_ns')})
        else:
            f = ctx.path(action['destination'], must_exist=True)
            if file_sha(f) != action['sha256']:
                raise ToolError('changed_input', 'Output changed; manual recovery required', path=action['destination'])
            if action.get('backup_source') and ctx.path(action['source']).exists():
                raise ToolError('collision', 'Original move source is occupied', path=action['source'])
            checks.append({'remove_or_restore': action['destination'], 'restore_source': action.get('source') if action.get('backup_source') else None})
    plan = {'transaction_id': ident, 'kind': data['kind'], 'steps': checks}
    pending = ctx.authorize(plan, 'rollback: remove/restore only verified outputs of this transaction')
    if pending:
        return pending
    for action in reversed(actions):
        if data['kind'] == 'quarantine':
            f = ctx.path(action['quarantine'], must_exist=True, internal=True)
            if action['mode'] == 'move-with-rollback':
                _verified_copy(ctx, f, ctx.path(action['path']), action['sha256'])
            f.unlink()
        elif data['kind'] in ('chmod', 'metadata'):
            path = ctx.path(action['path'])
            os.chmod(path, int(action['old_mode'], 8))
            if data['kind'] == 'metadata':
                os.utime(path, ns=(action['old_atime_ns'], action['old_mtime_ns']))
        else:
            dest = ctx.path(action['destination'], must_exist=True)
            dest.unlink()
            if action.get('backup_destination'):
                old = ctx.path(action['backup_destination'], must_exist=True, internal=True)
                _verified_copy(ctx, old, dest, action['previous_sha256'] if data['kind']=='write' else action['existing_sha256'])
            if action.get('backup_source'):
                source_backup = ctx.path(action['backup_source'], must_exist=True, internal=True)
                _verified_copy(ctx, source_backup, ctx.path(action['source']), action['sha256'])
    _set_journal(ctx, journal, 'rolled_back', data['actions'])
    return {'status': 'completed', 'rolled_back': len(actions), 'transaction_id': ident}


def _verify(ctx: Context) -> dict:
    pairs = ctx.request.get('entries')
    if pairs is None and all(k in ctx.request for k in ('source','destination')):
        pairs = [{'source': ctx.request['source'], 'destination': ctx.request['destination']}]
    if not isinstance(pairs, list) or not pairs or len(pairs) > 200:
        raise ToolError('invalid_input', 'Provide entries with source and destination to verify')
    checks = []
    for pair in pairs:
        pair = pair if isinstance(pair, dict) else {}
        a = string(pair.get('source'), 'source'); b = string(pair.get('destination'), 'destination')
        p = ctx.path(a, must_exist=True); q = ctx.path(b, must_exist=True)
        if not p.is_file() or not q.is_file():
            raise ToolError('invalid_input', 'Verify requires regular files')
        if max(p.stat().st_size, q.stat().st_size) > ctx.limits['max_transfer_bytes']:
            raise ToolError('limit_exceeded', 'Verification input too large')
        ha, hb = file_sha(p), file_sha(q)
        checks.append({'source': a, 'destination': b, 'source_sha256': ha, 'destination_sha256': hb, 'size_equal': p.stat().st_size==q.stat().st_size,
                       'hash_equal': ha==hb, 'mode_equal': stat.S_IMODE(p.stat().st_mode)==stat.S_IMODE(q.stat().st_mode)})
    return {'valid': all(c['hash_equal'] for c in checks), 'checks': checks}


def _archive_inputs(ctx: Context) -> list[dict]:
    names = ctx.request_paths(default=[])
    rows, _ = ctx.entries(names, hashes=True)
    files = [r for r in rows if r['kind'] == 'file']
    if any(r['kind'] not in ('file','directory') for r in rows):
        raise ToolError('unsafe_path', 'Archive cannot include symlinks or special files')
    if not files or len(files) > ctx.limits['max_archive_members']:
        raise ToolError('invalid_input', 'Archive needs 1..max_archive_members regular files')
    if len({r['path'].casefold() for r in files}) != len(files):
        raise ToolError('collision', 'Overlapping inputs or case-colliding archive member paths')
    if any(r['sha256'] is None for r in files):
        raise ToolError('limit_exceeded', 'Archive member exceeds max_file_bytes')
    if sum(r['size'] for r in files) > ctx.limits['max_archive_bytes']:
        raise ToolError('limit_exceeded', 'Archive input total exceeds limit')
    if any(r['path'] == 'workspace-manifest.json' for r in files):
        raise ToolError('collision', 'Reserved workspace-manifest.json member would collide')
    return sorted(files, key=lambda r:r['path'])


def _archive(ctx: Context) -> dict:
    destination = string(ctx.request.get('destination'), 'destination')
    if not destination.endswith('.zip'):
        raise ToolError('invalid_input', 'Archive destination must end in .zip')
    dst = ctx.path(destination); ctx.require_mutable(destination, allow_final_new=True)
    _parent(ctx, dst)
    if dst.exists():
        raise ToolError('collision', 'Archive destination already exists', path=destination)
    files = _archive_inputs(ctx)
    if destination in {r['path'] for r in files}:
        raise ToolError('collision', 'Cannot archive an output into itself')
    plan = {'destination': destination, 'members': [{'path':r['path'],'sha256':r['sha256'],'size':r['size']} for r in files]}
    pending = ctx.authorize(plan, 'create verified archive (new file only)')
    if pending:
        return pending
    fd, temp = tempfile.mkstemp(prefix='.workspace-archive-', dir=dst.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            def fixed_info(name):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                return info
            for row in files:
                p = ctx.path(row['path'], must_exist=True)
                h = hashlib.sha256(); count = 0
                with p.open('rb') as source, archive.open(fixed_info(row['path']), 'w', force_zip64=True) as target:
                    while chunk := source.read(1024 * 1024):
                        count += len(chunk)
                        if count > row['size'] or count > ctx.limits['max_file_bytes']:
                            raise ToolError('changed_input', 'Archive input grew during packaging', path=row['path'])
                        h.update(chunk); target.write(chunk)
                if count != row['size'] or h.hexdigest() != row['sha256']:
                    raise ToolError('changed_input', 'Archive input changed after approval', path=row['path'])
            archive.writestr(fixed_info('workspace-manifest.json'),
                             json.dumps({'schema':'workspace-archive-v1','members':plan['members']}, sort_keys=True, separators=(',', ':')))
        if os.stat(temp).st_size > ctx.limits['max_archive_bytes']:
            raise ToolError('limit_exceeded', 'Created archive exceeds byte limit')
        with zipfile.ZipFile(temp) as verify:
            if verify.testzip() is not None:
                raise ToolError('verification_failed', 'Archive failed ZIP integrity check')
        if dst.exists():
            raise ToolError('collision', 'Archive destination appeared during creation')
        os.replace(temp, dst)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {'status': 'completed', 'path': destination, 'members': len(files), 'sha256': file_sha(dst)}


def _check_zip(ctx: Context, name: str) -> tuple[Path, list[dict], dict | None]:
    p = ctx.path(name, must_exist=True)
    if not p.is_file() or p.stat().st_size > ctx.limits['max_archive_bytes'] or not zipfile.is_zipfile(p):
        raise ToolError('invalid_input', 'Expected a bounded ZIP file', path=name)
    rows, seen, total = [], set(), 0
    try:
        with zipfile.ZipFile(p) as z:
            if len(z.infolist()) > ctx.limits['max_archive_members']:
                raise ToolError('limit_exceeded', 'Archive has too many entries')
            for info in z.infolist():
                n = info.filename
                parts = n.rstrip('/').split('/')
                if (not n or n.startswith('/') or '\\' in n or re.match(r'^[A-Za-z]:', n)
                    or any(x in ('', '.', '..') or ':' in x for x in parts) or '\x00' in n):
                    raise ToolError('unsafe_archive', 'Unsafe archive member path', member=n)
                key = n.rstrip('/').casefold()
                if key in seen:
                    raise ToolError('unsafe_archive', 'Duplicate or case-colliding archive member', member=n)
                seen.add(key)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ToolError('unsafe_archive', 'Archive symlink not permitted', member=n)
                total += info.file_size
                if total > ctx.limits['max_archive_bytes'] or info.file_size > ctx.limits['max_transfer_bytes']:
                    raise ToolError('limit_exceeded', 'Archive expansion exceeds limit')
                if info.file_size > max(1024, info.compress_size) * 200:
                    raise ToolError('unsafe_archive', 'Suspicious compression expansion ratio', member=n)
                rows.append({'name': n, 'bytes': info.file_size, 'directory': info.is_dir()})
            if z.testzip() is not None:
                raise ToolError('verification_failed', 'Archive CRC test failed')
            manifest = None
            if 'workspace-manifest.json' in seen:
                if z.getinfo('workspace-manifest.json').file_size > ctx.limits['max_input_bytes']:
                    raise ToolError('limit_exceeded', 'Archive manifest exceeds JSON limit')
                manifest = json.loads(z.read('workspace-manifest.json'))
                if not isinstance(manifest, dict) or manifest.get('schema') != 'workspace-archive-v1' or not isinstance(manifest.get('members'), list):
                    raise ToolError('invalid_input', 'Archive manifest schema invalid')
                declared = manifest['members']
                actual = {m['name'] for m in rows if not m['directory'] and m['name'] != 'workspace-manifest.json'}
                if (len(declared) != len(actual) or {m.get('path') for m in declared if isinstance(m, dict)} != actual
                    or any(not isinstance(m, dict) or not isinstance(m.get('sha256'), str)
                           or not re.fullmatch(r'[a-f0-9]{64}', m['sha256']) for m in declared)):
                    raise ToolError('invalid_input', 'Archive manifest paths or checksums do not match actual members')
    except (OSError, zipfile.BadZipFile, RuntimeError, json.JSONDecodeError) as exc:
        raise ToolError('invalid_input', f'Cannot safely inspect ZIP: {exc}') from exc
    return p, rows, manifest


def _extract(ctx: Context) -> dict:
    archive_name = string(ctx.request.get('archive'), 'archive')
    target_name = string(ctx.request.get('destination'), 'destination')
    dst = ctx.path(target_name); ctx.require_mutable(target_name)
    if dst.exists():
        raise ToolError('collision', 'Extract into a new directory, not an existing one', path=target_name)
    _parent(ctx, dst)
    arc, members, manifest = _check_zip(ctx, archive_name)
    expected = {m['path']: m['sha256'] for m in manifest['members']} if manifest else {}
    plan = {'archive': archive_name, 'archive_sha256': file_sha(arc), 'destination': target_name,
            'members': members, 'manifest_verified_on_extraction': bool(manifest)}
    pending = ctx.authorize(plan, 'extract bounded ZIP into a new directory; verify members and optional checksums')
    if pending:
        return pending
    if file_sha(arc) != plan['archive_sha256']:
        raise ToolError('changed_input', 'Archive changed after approval')
    dst.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(arc) as z:
            for m in members:
                parts = m['name'].rstrip('/').split('/')
                out = dst.joinpath(*parts)
                if m['directory']:
                    out.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                h = hashlib.sha256(); count = 0
                with z.open(m['name']) as source, out.open('xb') as target:
                    while chunk := source.read(1024*1024):
                        count += len(chunk)
                        if count > m['bytes'] or count > ctx.limits['max_transfer_bytes']:
                            raise ToolError('limit_exceeded', 'Archive member changed or exceeded bound', member=m['name'])
                        target.write(chunk); h.update(chunk)
                if count != m['bytes'] or (m['name'] in expected and h.hexdigest() != expected[m['name']]):
                    raise ToolError('verification_failed', 'Archive member size or checksum mismatch', member=m['name'])
    except Exception:
        shutil.rmtree(dst)
        raise
    return {'status': 'completed', 'destination': target_name, 'members': len(members), 'verified_manifest': bool(manifest)}


def _intake(ctx: Context) -> dict:
    names = ctx.request_paths(files=True)
    target_name = string(ctx.request.get('destination', 'working/intake'), 'destination')
    target = ctx.path(target_name); ctx.require_mutable(target_name)
    if target.exists() and not target.is_dir():
        raise ToolError('collision', 'Intake destination is not a directory')
    items = []
    for n in names:
        p = ctx.path(n, must_exist=True)
        if p.stat().st_size > ctx.limits['max_transfer_bytes']:
            raise ToolError('limit_exceeded', 'Intake input exceeds transfer limit', path=n)
        with p.open('rb') as f:
            magic = f.read(8)
        kind = 'zip' if magic.startswith(b'PK\x03\x04') else 'pdf' if magic.startswith(b'%PDF-') else 'png' if magic.startswith(b'\x89PNG') else 'executable' if magic.startswith((b'\x7fELF', b'MZ', b'#!')) or p.suffix.lower() in ('.exe','.com','.dll','.msi','.bat','.ps1','.sh') else 'unknown'
        if kind == 'executable':
            raise ToolError('quarantine_required', 'Executable input is not staged into working; use input-quarantine-controller on this path', path=n)
        safe = re.sub('[^a-z0-9.-]+','-',p.name.lower()).strip('.-') or 'input'
        out = target / safe
        if out.exists() or any(x['destination'] == ctx.relative(out) for x in items):
            raise ToolError('collision', 'Normalized intake name collides', destination=ctx.relative(out))
        items.append({'source': n, 'destination': ctx.relative(out), 'kind': kind, 'sha256': file_sha(p), 'bytes':p.stat().st_size,
                      'quarantine_recommended': kind == 'executable'})
    plan = {'items': items, 'originals_preserved': True}
    pending = ctx.authorize(plan, 'stage verified copies only; executable inputs must not be executed')
    if pending:
        return pending
    _parent(ctx, target, create=True)
    target.mkdir(mode=0o700, exist_ok=True)
    for item in items:
        _verified_copy(ctx, ctx.path(item['source'], must_exist=True), ctx.path(item['destination']), item['sha256'])
    provenance = ctx.state_file('intake-' + secrets.token_hex(8) + '.json')
    ctx.atomic_bytes(provenance, (json.dumps({'created':utc(),'items':items},indent=2)+'\n').encode())
    return {'status': 'completed', 'items': items, 'provenance_record': ctx.relative(provenance)}


def _redact(ctx: Context) -> dict:
    source = string(ctx.request.get('source'), 'source'); destination = string(ctx.request.get('destination'), 'destination')
    text = ctx.read_text(source)
    output = re.sub(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
                    '[REDACTED PRIVATE KEY]', text, flags=re.S)
    output = redact(output)
    dest = ctx.path(destination); ctx.require_mutable(destination)
    if dest.exists():
        raise ToolError('collision', 'Redaction must create a new output, never overwrite', path=destination)
    _parent(ctx, dest)
    plan = {'source': source, 'source_sha256': file_sha(ctx.path(source)), 'destination': destination,
            'replacement_count_estimate': max(0, text.count('PRIVATE KEY')-output.count('PRIVATE KEY')),
            'output_sha256': digest(output)}
    pending = ctx.authorize(plan, 'create a redacted UTF-8 copy; manual privacy review still required')
    if pending:
        return pending
    if file_sha(ctx.path(source)) != plan['source_sha256']:
        raise ToolError('changed_input', 'Source changed after approval')
    ctx.atomic_bytes(dest, output.encode('utf-8'))
    return {'status': 'completed', 'destination': destination, 'sha256': digest(output)}


def _chmod(ctx: Context) -> dict:
    name = string(ctx.request.get('path'), 'path'); mode_s = string(ctx.request.get('mode'), 'mode', maxlen=4)
    if not re.fullmatch(r'0?[0-7]{3}', mode_s):
        raise ToolError('invalid_input', 'mode must be a three-digit octal permission (e.g. 600 or 700)')
    new = int(mode_s, 8)
    if new & 0o007 or new & 0o020:
        raise ToolError('protected_path', 'World access and group write are not allowed by this tool')
    p = ctx.path(name, must_exist=True); ctx.require_mutable(name)
    old = stat.S_IMODE(p.stat().st_mode)
    if old == new:
        return {'status':'unchanged','path':name,'mode':f'{new:04o}'}
    plan = {'path': name, 'old_mode': f'{old:04o}', 'new_mode': f'{new:04o}'}
    pending = ctx.authorize(plan, 'permission change; ownership and ACLs are not modified')
    if pending:
        return pending
    ident, journal = _journal(ctx, 'chmod', [])
    os.chmod(p, new)
    _set_journal(ctx, journal, 'committed', [{**plan,'state':'done'}])
    return {'status':'completed','path':name,'mode':f'{new:04o}','transaction_id':ident}


def _metadata(ctx: Context) -> dict:
    name = string(ctx.request.get('path'), 'path')
    p = ctx.path(name, must_exist=True); ctx.require_mutable(name)
    if not p.is_file():
        raise ToolError('invalid_input', 'Metadata normalization accepts a regular file only')
    if p.stat().st_size > ctx.limits['max_transfer_bytes']:
        raise ToolError('limit_exceeded', 'Metadata input exceeds transfer limit')
    content_hash = file_sha(p)
    old = p.stat()
    mode_s = ctx.request.get('mode', f'{stat.S_IMODE(old.st_mode):03o}')
    if not isinstance(mode_s, str) or not re.fullmatch(r'0?[0-7]{3}', mode_s):
        raise ToolError('invalid_input', 'mode must be a three-digit octal string')
    new_mode = int(mode_s, 8)
    if new_mode & 0o007 or new_mode & 0o020:
        raise ToolError('protected_path', 'World access or group write is not permitted')
    new_time = ctx.request.get('mtime_ns', old.st_mtime_ns)
    if type(new_time) is not int or not 0 <= new_time <= (time.time() + 365 * 86400) * 1_000_000_000:
        raise ToolError('invalid_input', 'mtime_ns must be a bounded nonnegative UTC nanosecond timestamp')
    plan = {'path': name, 'sha256': content_hash, 'old_mode': f'{stat.S_IMODE(old.st_mode):04o}',
            'new_mode': f'{new_mode:04o}', 'old_mtime_ns': old.st_mtime_ns, 'new_mtime_ns': new_time}
    if plan['old_mode'] == plan['new_mode'] and plan['old_mtime_ns'] == new_time:
        return {'status': 'unchanged', **plan}
    pending = ctx.authorize(plan, 'change only mode/mtime of one working file; journal previous metadata')
    if pending: return pending
    current = p.stat()
    if file_sha(p) != content_hash or current.st_mtime_ns != old.st_mtime_ns or stat.S_IMODE(current.st_mode) != stat.S_IMODE(old.st_mode):
        raise ToolError('changed_input', 'File content or metadata changed after approval')
    ident, journal = _journal(ctx, 'metadata', [])
    action = {**plan, 'old_atime_ns': current.st_atime_ns, 'state': 'done'}
    os.chmod(p, new_mode)
    os.utime(p, ns=(current.st_atime_ns, new_time))
    _set_journal(ctx, journal, 'committed', [action])
    return {'status': 'completed', 'path': name, 'mode': plan['new_mode'], 'mtime_ns': new_time, 'transaction_id': ident}


def _assemble(ctx: Context) -> dict:
    names = strings(ctx.request.get('paths'), 'paths', maximum=100)
    destination = string(ctx.request.get('destination'), 'destination')
    if not names or not destination.endswith(('.md', '.txt')):
        raise ToolError('invalid_input', 'Assemble 1..100 UTF-8 text/Markdown files into a new .md or .txt file')
    dest = ctx.path(destination); ctx.require_mutable(destination)
    _parent(ctx, dest)
    if dest.exists(): raise ToolError('collision', 'Assembly destination already exists')
    sections = []; members = []; size = 0
    for name in names:
        p = ctx.path(name, must_exist=True)
        if p.suffix.lower() not in ('.md', '.txt'):
            raise ToolError('invalid_input', 'Only editable UTF-8 .md/.txt sources can be assembled', path=name)
        text = ctx.read_text(name)
        sha = file_sha(p)
        section = f'<!-- Source: {name} (sha256: {sha}) -->\n' + text.rstrip() + '\n'
        size += len(section.encode('utf-8')) + 2
        if size > ctx.limits['max_text_bytes']:
            raise ToolError('limit_exceeded', 'Assembly exceeds text byte limit')
        sections.append(section); members.append({'path': name, 'sha256': sha})
    content = ('\n'.join(sections)).encode('utf-8')
    plan = {'destination': destination, 'sources': members, 'output_sha256': digest(content), 'bytes': len(content)}
    pending = ctx.authorize(plan, 'assemble editable text copies with source provenance; originals preserved')
    if pending: return pending
    if any(file_sha(ctx.path(m['path'])) != m['sha256'] for m in members):
        raise ToolError('changed_input', 'Assembly source changed after approval')
    ident, journal = _journal(ctx, 'assemble', [])
    ctx.atomic_bytes(dest, content)
    _set_journal(ctx, journal, 'committed', [{'destination': destination, 'sha256': plan['output_sha256'], 'state': 'done'}])
    return {'status': 'completed', 'destination': destination, 'sources': members, 'sha256': plan['output_sha256'], 'transaction_id': ident}


def _lock(ctx: Context) -> dict:
    name = string(ctx.request.get('name'), 'name', maxlen=64)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', name):
        raise ToolError('invalid_input', 'Lock name must be lowercase alphanumeric/hyphen')
    op = ctx.request.get('op', 'status')
    if op not in ('status','acquire','release'):
        raise ToolError('invalid_input', 'op must be status, acquire or release')
    path = ctx.state / ('lock-' + name + '.json')
    current = load_json(path) if path.is_file() and not path.is_symlink() else None
    if op == 'status':
        return {'name': name, 'held': bool(current), 'owner': current.get('owner') if current else None,
                'created': current.get('created') if current else None,
                'expired': bool(current and current['expires'] < time.time())}
    if op == 'acquire':
        ttl = integer(ctx.request.get('ttl_seconds', 3600), 'ttl_seconds', 1, 86400)
        if current:
            raise ToolError('lock_held', 'Lock already exists; expired locks require owner review and release', name=name, expired=current['expires'] < time.time())
        plan = {'name':name,'op':op,'ttl_seconds':ttl,'owner':ctx.request.get('owner','operator')}
        pending = ctx.authorize(plan, 'acquire cooperative workspace lock')
        if pending: return pending
        ctx.ensure_state()
        token = secrets.token_hex(16)
        record = {'name':name,'owner':string(plan['owner'],'owner',maxlen=80),'created':utc(),'expires':time.time()+ttl,
                  'token_sha256':digest(token)}
        try:
            with path.open('x', encoding='utf-8') as f:
                json.dump(record, f)
        except FileExistsError as exc:
            raise ToolError('lock_held','Lock was acquired concurrently',name=name) from exc
        return {'status':'completed','name':name,'lock_token':token,'expires':record['expires']}
    token = string(ctx.request.get('lock_token'), 'lock_token', maxlen=80)
    if not current or digest(token) != current['token_sha256']:
        raise ToolError('approval_required', 'Only the holder can release this lock; stale locks require manual recovery')
    plan = {'name':name,'op':'release','owner':current['owner'],'created':current['created']}
    pending = ctx.authorize(plan, 'release cooperative workspace lock')
    if pending: return pending
    path.unlink()
    return {'status':'completed','released':name}


def run(ctx: Context, variant: str) -> dict:
    handlers = {'transfer':_transfer,'layout':_layout,'scratch':_scratch,'write':_write,'quarantine':_quarantine,
                'rollback':_rollback,'verify':_verify,'archive':_archive,'extract':_extract,'intake':_intake,
                'redact':_redact,'chmod':_chmod,'metadata':_metadata,'assemble':_assemble,'lock':_lock}
    if variant not in handlers:
        raise ToolError('internal_error', 'Unknown file operation', variant=variant)
    return handlers[variant](ctx)
