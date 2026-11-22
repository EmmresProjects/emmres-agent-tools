"""Shared bounded, root-confined primitives. Loaded by tools-workspace.py."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

STATE = '.workspace-control'
HARD_LIMITS = {
    'max_entries': 20000, 'max_depth': 32, 'max_input_bytes': 1048576,
    'max_text_bytes': 4194304, 'max_file_bytes': 1073741824,
    'max_transfer_bytes': 2147483648, 'max_archive_bytes': 2147483648,
    'max_archive_members': 10000, 'max_command_seconds': 300,
    'max_command_output_bytes': 262144, 'max_retries': 4,
    'max_workflow_steps': 64,
}
_SECRET_RE = re.compile(r'(?i)(?:(?:api[_-]?key|token|secret|password|authorization|client[_-]?secret|cookie)\s*[:=]\s*)(["\']?)([^\s"\'&,;}{]{6,})')
_PRIVATE_RE = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')
_DRIVE_RE = re.compile(r'^[a-zA-Z]:')


class ToolError(Exception):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


def utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False)


def digest(data: bytes | str) -> str:
    return hashlib.sha256(data.encode('utf-8') if isinstance(data, str) else data).hexdigest()


def as_object(value: Any, label: str = 'request') -> dict:
    if not isinstance(value, dict):
        raise ToolError('invalid_input', f'{label} must be a JSON object')
    return value


def string(value: Any, label: str, *, nonempty: bool = True, maxlen: int = 512) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()) or len(value) > maxlen or '\x00' in value:
        raise ToolError('invalid_input', f'{label} must be a string of at most {maxlen} characters')
    return value


def strings(value: Any, label: str, *, maximum: int = 2000) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ToolError('invalid_input', f'{label} must be a list of at most {maximum} strings')
    return [string(x, f'{label}[{n}]') for n, x in enumerate(value)]


def integer(value: Any, label: str, low: int = 0, high: int = 100000000) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ToolError('invalid_input', f'{label} must be an integer between {low} and {high}')
    return value


def load_json(path: Path, max_bytes: int = 262144) -> Any:
    try:
        if path.stat().st_size > max_bytes:
            raise ToolError('limit_exceeded', f'JSON exceeds {max_bytes} bytes', path=str(path))
        def unique(pairs):
            obj = {}
            for k, v in pairs:
                if k in obj:
                    raise ToolError('invalid_input', 'Duplicate JSON key', key=k)
                obj[k] = v
            return obj
        def no_constant(value):
            raise ToolError('invalid_input', f'Non-finite JSON number: {value}')
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique, parse_constant=no_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ToolError('invalid_input', f'Cannot read valid UTF-8 JSON: {exc}', path=str(path)) from exc


def redact(text: str) -> str:
    text = re.sub(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?'
                  r'-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
                  '[REDACTED PRIVATE KEY]', text, flags=re.S)
    return _PRIVATE_RE.sub('[REDACTED PRIVATE KEY HEADER]',
                           _SECRET_RE.sub(lambda m: m.group(0).replace(m.group(2), '[REDACTED]'), text))


def public_data(value: Any, key: str = '') -> Any:
    """Scrub structured diagnostics without serializing and reparsing redacted JSON."""
    if key not in ('approval_token',) and re.search(r'(?i)(password|secret|credential|private[_-]?key|cookie|token)', key):
        return '[REDACTED]'
    if isinstance(value, dict):
        return {str(k): public_data(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [public_data(v) for v in value]
    if isinstance(value, str):
        return redact(value)
    return value


def file_sha(path: Path, max_bytes: int | None = None) -> str:
    try:
        size = path.stat().st_size
        if max_bytes is not None and size > max_bytes:
            raise ToolError('limit_exceeded', 'File too large to hash', path=str(path), size=size, limit=max_bytes)
        h = hashlib.sha256()
        with path.open('rb') as fh:
            while data := fh.read(1024 * 1024):
                h.update(data)
        return h.hexdigest()
    except OSError as exc:
        raise ToolError('io_error', f'Cannot hash file: {exc}', path=str(path)) from exc


class Context:
    def __init__(self, root: str, policy: dict, capability: dict, request: dict, *, apply: bool = False, approve: str | None = None):
        p = Path(root).absolute()
        if not p.is_dir() or p.is_symlink():
            raise ToolError('invalid_root', 'Root must be an existing, non-symlink directory')
        self.root = p.resolve(strict=True)
        self.policy = self._validate_policy(policy)
        self.capability = capability
        self.request = as_object(request)
        self.apply, self.approve = apply, approve
        self.limits = self.policy['limits']
        self.state = self.root / STATE

    @staticmethod
    def _validate_policy(policy: dict) -> dict:
        p = as_object(policy, 'policy')
        limits = as_object(p.get('limits'), 'policy.limits')
        for k, hard in HARD_LIMITS.items():
            integer(limits.get(k), 'policy.limits.' + k, 1, hard)
        for k in ('protected_prefixes', 'cleanup_prefixes', 'excluded_names', 'download_allow_hosts'):
            strings(p.get(k), 'policy.' + k, maximum=100)
        integer(p.get('retention_days'), 'policy.retention_days', 0, 3650)
        if not isinstance(p.get('offline'), bool) or not isinstance(p.get('enable_container_adapter'), bool) or not isinstance(p.get('enable_vm_adapter'), bool):
            raise ToolError('invalid_input', 'Policy booleans are invalid')
        profiles = as_object(p.get('trusted_profiles'), 'policy.trusted_profiles')
        for name, profile in profiles.items():
            string(name, 'profile name', maxlen=80)
            profile = as_object(profile, f'profile {name}')
            argv = strings(profile.get('argv'), f'{name}.argv', maximum=40)
            if not argv or any('\n' in a or '\r' in a for a in argv):
                raise ToolError('invalid_input', f'Invalid argv in profile {name}')
            if '/' in argv[0] or '\\' in argv[0] or argv[0].startswith('-'):
                raise ToolError('invalid_input', f'Profile {name} executable must be a bare command name')
            integer(profile.get('timeout_seconds'), f'{name}.timeout_seconds', 1, limits['max_command_seconds'])
        return p

    def path(self, name: str, *, must_exist: bool = False, allow_root: bool = False, internal: bool = False) -> Path:
        name = string(name, 'path', maxlen=1024)
        if name == '.' and allow_root:
            return self.root
        if name.startswith('/') or '\\' in name or _DRIVE_RE.match(name) or name.endswith('/'):
            raise ToolError('unsafe_path', 'Use a root-relative POSIX path without backslashes or drive letters', path=name)
        parts = name.split('/')
        if not parts or any(part in ('.', '..', '') or len(part) > 255 for part in parts):
            raise ToolError('unsafe_path', 'Empty, dot, parent, or overlong path component', path=name)
        if parts[0] == STATE and not internal:
            raise ToolError('unsafe_path', 'Internal state is not a user file path', path=name)
        result = self.root
        for part in parts:
            result = result / part
            try:
                mode = result.lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(mode):
                raise ToolError('unsafe_path', 'Symlink component is not permitted', path=name)
        if must_exist and not result.exists():
            raise ToolError('missing_input', 'Path does not exist', path=name)
        return result

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def protected(self, name: str) -> bool:
        part = name.split('/')[0]
        return part in self.policy['protected_prefixes'] or part == STATE

    def require_mutable(self, name: str, *, source: bool = False, allow_final_new: bool = False) -> None:
        prefix = name.split('/')[0]
        if allow_final_new and not source and prefix in ('deliverables', 'final') and not self.path(name).exists():
            return  # Reviewed promotion of a NEW artifact is permitted; replacement is not.
        if self.protected(name):
            raise ToolError('protected_path', 'Source-of-truth, existing final, and internal state cannot be modified by this operation', path=name, source=source)

    def request_paths(self, key: str = 'paths', default: list[str] | None = None, *, files: bool = False) -> list[str]:
        names = strings(self.request.get(key, default if default is not None else ['.']), key, maximum=self.limits['max_entries'])
        if not names:
            raise ToolError('invalid_input', f'{key} must not be empty')
        for n in names:
            p = self.path(n, must_exist=True, allow_root=True)
            if files and not p.is_file():
                raise ToolError('invalid_input', 'Expected a regular file', path=n)
        return names

    def entries(self, paths: list[str] | None = None, *, include_excluded: bool = False, hashes: bool = False) -> tuple[list[dict], dict]:
        paths = paths or ['.']
        result: list[dict] = []
        ignored = 0
        stack: list[tuple[Path, int]] = []
        for name in paths:
            path = self.path(name, must_exist=True, allow_root=True)
            if path != self.root:
                stack.append((path, 0))
            else:
                try:
                    stack.extend((Path(e.path), 1) for e in os.scandir(path))
                except OSError as exc:
                    raise ToolError('io_error', f'Cannot enumerate root: {exc}') from exc
        while stack:
            path, depth = stack.pop()
            rel = self.relative(path)
            if depth > self.limits['max_depth']:
                raise ToolError('limit_exceeded', 'Directory depth limit exceeded', path=rel)
            if len(result) >= self.limits['max_entries']:
                raise ToolError('limit_exceeded', 'Directory entry limit exceeded', limit=self.limits['max_entries'])
            if not include_excluded and (path.name in self.policy['excluded_names'] or rel.split('/')[0] == STATE):
                ignored += 1
                continue
            try:
                st = path.lstat()
            except OSError as exc:
                raise ToolError('io_error', f'Cannot stat path: {exc}', path=rel) from exc
            mode = st.st_mode
            kind = 'symlink' if stat.S_ISLNK(mode) else 'directory' if stat.S_ISDIR(mode) else 'file' if stat.S_ISREG(mode) else 'special'
            row = {'path': rel, 'kind': kind, 'size': st.st_size if kind == 'file' else 0,
                   'modified_ns': st.st_mtime_ns, 'mode': f'{stat.S_IMODE(mode):04o}',
                   'owner': getattr(st, 'st_uid', None)}
            if kind == 'symlink':
                try:
                    row['target'] = os.readlink(path)
                    row['broken'] = not path.exists()
                except OSError:
                    row['broken'] = True
            if hashes and kind == 'file':
                if st.st_size <= self.limits['max_file_bytes']:
                    row['sha256'] = file_sha(path, self.limits['max_file_bytes'])
                else:
                    row['sha256'] = None
                    row['hash_note'] = 'over max_file_bytes'
            result.append(row)
            if kind == 'directory':
                try:
                    with os.scandir(path) as entries:
                        stack.extend((Path(e.path), depth + 1) for e in entries)
                except OSError as exc:
                    raise ToolError('io_error', f'Cannot read directory: {exc}', path=rel) from exc
        result.sort(key=lambda x: x['path'])
        return result, {'entries': len(result), 'excluded': ignored, 'limit': self.limits['max_entries']}

    def approval_token(self, plan: dict) -> str:
        data = {'root': str(self.root), 'capability': self.capability['id'], 'request': self.request,
                'plan': plan, 'policy': digest(canonical(self.policy))}
        return digest(canonical(data))[:24]

    def authorize(self, plan: dict, risk: str = 'workspace mutation') -> dict | None:
        token = self.approval_token(plan)
        if not self.apply:
            return {'status': 'approval_required', 'risk': risk, 'plan': plan, 'approval_token': token,
                    'next': 'Review plan, then rerun with --apply --approve ' + token}
        if self.approve != token:
            raise ToolError('approval_required', 'Approval does not match the current plan; rerun without --apply and review the new token', risk=risk, approval_token=token)
        return None

    def ensure_state(self) -> Path:
        if self.state.is_symlink():
            raise ToolError('unsafe_path', 'Internal state must not be a symlink')
        self.state.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.state, 0o700)
        return self.state

    def state_file(self, name: str) -> Path:
        if '/' in name or name.startswith('.') or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,120}', name):
            raise ToolError('invalid_input', 'Invalid internal state filename')
        return self.ensure_state() / name

    def atomic_bytes(self, path: Path, content: bytes, *, overwrite: bool = False, mode: int = 0o600) -> None:
        if not path.parent.is_dir() or path.parent.is_symlink():
            raise ToolError('missing_input', 'Output parent must exist and not be a symlink', path=self.relative(path))
        if path.is_symlink() or (path.exists() and not overwrite):
            raise ToolError('collision', 'Output exists or is a symlink', path=self.relative(path))
        fd, name = tempfile.mkstemp(prefix='.workspace-write-', dir=path.parent)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, 'wb') as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            if path.is_symlink() or (path.exists() and not overwrite):
                raise ToolError('collision', 'Output changed during write', path=self.relative(path))
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def json_output(self, name: str, data: dict) -> str:
        path = self.path(name)
        self.require_mutable(name)
        content = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        if path.exists():
            if path.is_file() and path.read_bytes() == content:
                return name
            raise ToolError('collision', 'Report output already exists with different content', path=name)
        self.atomic_bytes(path, content)
        return name

    def read_text(self, name: str, *, max_bytes: int | None = None) -> str:
        path = self.path(name, must_exist=True)
        if not path.is_file():
            raise ToolError('invalid_input', 'Expected a regular text file', path=name)
        limit = max_bytes or self.limits['max_text_bytes']
        if path.stat().st_size > limit:
            raise ToolError('limit_exceeded', 'Text file exceeds size limit', path=name, limit=limit)
        try:
            return path.read_text(encoding='utf-8')
        except UnicodeError as exc:
            raise ToolError('invalid_input', 'File is not UTF-8', path=name) from exc

    def safe_external(self, argv: list[str], timeout: int, *, cwd: Path | None = None, env_names: list[str] | None = None) -> dict:
        if not argv or not all(isinstance(a, str) and '\x00' not in a and len(a) <= 4096 for a in argv):
            raise ToolError('invalid_input', 'Invalid command arguments')
        if '/' in argv[0] or '\\' in argv[0] or not shutil.which(argv[0]):
            raise ToolError('integration_unavailable', 'Approved executable is not available', executable=argv[0])
        timeout = integer(timeout, 'timeout', 1, self.limits['max_command_seconds'])
        allowed = {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'SYSTEMROOT', 'TEMP', 'TMP'}
        allowed.update(env_names or [])
        env = {k: v for k, v in os.environ.items() if k in allowed}
        start = time.monotonic()
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                proc = subprocess.Popen(argv, cwd=cwd or self.root, env=env, stdin=subprocess.DEVNULL,
                                        stdout=stdout, stderr=stderr, start_new_session=(os.name == 'posix'))
                try:
                    exit_code = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    if os.name == 'posix':
                        import signal
                        os.killpg(proc.pid, signal.SIGTERM)
                    else:
                        proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        if os.name == 'posix':
                            os.killpg(proc.pid, signal.SIGKILL)
                        else:
                            proc.kill()
                        proc.wait(timeout=3)
                    raise ToolError('timeout', 'Approved command timed out and was terminated', seconds=timeout)
                except KeyboardInterrupt as exc:
                    if os.name == 'posix':
                        import signal
                        os.killpg(proc.pid, signal.SIGTERM)
                    else:
                        proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        if os.name == 'posix': os.killpg(proc.pid, signal.SIGKILL)
                        else: proc.kill()
                        proc.wait(timeout=3)
                    raise ToolError('cancelled', 'Approved child process stopped after user cancellation') from exc
            except OSError as exc:
                raise ToolError('io_error', f'Cannot start approved executable: {exc}') from exc
            limit = self.limits['max_command_output_bytes']
            stdout.seek(0); stderr.seek(0)
            out = stdout.read(limit + 1); err = stderr.read(limit + 1)
            return {'exit_code': exit_code, 'duration_seconds': round(time.monotonic()-start, 3),
                    'stdout': redact(out[:limit].decode('utf-8', 'replace')),
                    'stderr': redact(err[:limit].decode('utf-8', 'replace')),
                    'stdout_truncated': len(out) > limit, 'stderr_truncated': len(err) > limit}
