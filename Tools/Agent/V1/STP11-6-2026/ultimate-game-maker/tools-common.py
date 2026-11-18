#!/usr/bin/env python3
"""Shared runtime utilities for the unified Ultimate Game Maker Skill Tool.

Loaded by tools-game-maker.py and tools-selftest.py through importlib. It contains no
user-facing command; its role is reusable validation, resource resolution, bounded I/O,
output safety, cancellation, cleanup, and module bootstrapping.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

sys.dont_write_bytecode = True

TOOL_FOLDER = 'ultimate-game-maker'
VERSION = '2.0.0'
MIN_PYTHON = (3, 9)
MODULE_FILES = [
    ('ugm_source_parser', 'tools-source-parser.py'),
    ('ugm_registry', 'tools-registry.py'),
    ('ugm_validation', 'tools-validation.py'),
    ('ugm_capability_packets', 'tools-capability-packets.py'),
    ('ugm_docs', 'tools-docs.py'),
    ('ugm_prototype', 'tools-prototype.py'),
    ('ugm_planner', 'tools-planner.py'),
    ('ugm_production_registry', 'tools-production-registry.py'),
    ('ugm_production_planner', 'tools-production-planner.py'),
    ('ugm_evidence_gates', 'tools-evidence-gates.py'),
    ('ugm_production_engine', 'tools-production-engine.py'),
    ('ugm_selftest', 'tools-selftest.py'),
]


def assert_runtime() -> None:
    """Fail with a structured error on Python versions below the declared floor."""
    if sys.version_info < MIN_PYTHON:
        raise ToolError('UNSUPPORTED_PYTHON', 'Python %d.%d or newer is required.' % MIN_PYTHON,
                        ['.'.join(str(part) for part in sys.version_info[:3])],
                        ['Use a supported Python interpreter; see knowledge-readme.md.'])



class ToolError(Exception):
    """Structured, recoverable tool failure."""

    def __init__(self, code: str, message: str, details=None, recovery=None, exit_code: int = 2):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []
        self.recovery = recovery or []
        self.exit_code = exit_code

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'message': self.message,
            'details': list(self.details),
            'recovery': list(self.recovery),
        }


def error_payload(error: ToolError, command: str | None = None) -> dict:
    return {
        'ok': False,
        'schema_id': 'ugm-result-1.0',
        'command': command,
        'error': error.to_dict(),
    }


def success_payload(command: str, result: dict, warnings=None) -> dict:
    return {
        'ok': True,
        'schema_id': 'ugm-result-1.0',
        'command': command,
        'result': result,
        'warnings': list(warnings or []),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    text = value.lower()
    text = text.replace('c++', 'cpp').replace('c#', 'csharp')
    text = text.replace('&', ' and ').replace('+', ' plus ').replace('#', ' sharp ')
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return re.sub(r'-{2,}', '-', text).strip('-')


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(',') if part.strip()]


def deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def parse_override(raw: str) -> tuple[str, object]:
    if '=' not in raw:
        raise ToolError('INVALID_OVERRIDE', 'Each --set value must use key=value.', [raw], ['Use --set runtime.deadline_seconds=30.'])
    key, value = raw.split('=', 1)
    key = key.strip()
    if not key or not re.fullmatch(r'[a-z0-9_]+(?:\.[a-z0-9_]+)*', key):
        raise ToolError('INVALID_OVERRIDE', 'Override keys must be lowercase dotted paths.', [key], ['Use a key such as runtime.deadline_seconds.'])
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value.strip()
    return key, parsed


def apply_overrides(config: dict, overrides: list[str]) -> dict:
    result = dict(config)
    for raw in overrides:
        key, value = parse_override(raw)
        parts = key.split('.')
        cursor = result
        for part in parts[:-1]:
            if part not in cursor or not isinstance(cursor[part], dict):
                raise ToolError('INVALID_OVERRIDE', f'Override path {key} does not resolve to a configured object.', [part], ['Check tools-default-config.json for the exact key path.'])
            cursor = cursor[part]
        final = parts[-1]
        if final not in cursor:
            raise ToolError('INVALID_OVERRIDE', f'Unknown override key {key}.', [final], ['Use only documented configuration keys.'])
        cursor[final] = value
    return result


class Deadline:
    """Cooperative deadline checked at every major generation stage."""

    def __init__(self, seconds: float):
        try:
            self.seconds = float(seconds)
        except (TypeError, ValueError):
            raise ToolError('INVALID_CONFIG', 'runtime.deadline_seconds must be a number.', [seconds])
        if self.seconds <= 0 or self.seconds > 300:
            raise ToolError('INVALID_CONFIG', 'runtime.deadline_seconds must be between 0 and 300.', [self.seconds])
        self.started = time.monotonic()

    def check(self, stage: str = 'work') -> None:
        if time.monotonic() - self.started > self.seconds:
            raise ToolError(
                'TIMEOUT',
                f'The operation exceeded its {self.seconds:g} second deadline at {stage}.',
                [stage],
                ['Reduce selected categories or skills, or increase runtime.deadline_seconds with --set.'],
            )

    def remaining(self) -> float:
        return max(0.0, self.seconds - (time.monotonic() - self.started))


def retry_io(operation, config: dict, stage: str):
    retry = config.get('retry', {})
    attempts = int(retry.get('attempts', 3))
    backoff = float(retry.get('backoff_seconds', 0.02))
    attempts = max(1, min(attempts, 5))
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except OSError as exc:
            last = exc
            if attempt < attempts and backoff > 0:
                time.sleep(min(backoff * attempt, 0.2))
    raise ToolError('IO_RETRY_EXHAUSTED', f'Filesystem operation failed after {attempts} bounded attempts at {stage}.', [str(last)], ['Check permissions and free disk space, then rerun from a clean output path.'])


def read_bytes_limited(path: Path, max_bytes: int, label: str, config: dict | None = None) -> bytes:
    cfg = config or {'retry': {'attempts': 1, 'backoff_seconds': 0}, 'runtime': {'deadline_seconds': 30}}
    deadline = Deadline(cfg.get('runtime', {}).get('deadline_seconds', 30))

    def operation():
        if not path.exists():
            raise ToolError('MISSING_INPUT', f'{label} does not exist.', [str(path)], ['Provide an existing relative file path.'])
        if not path.is_file():
            raise ToolError('INVALID_INPUT', f'{label} is not a regular file.', [str(path)], ['Provide a regular file path.'])
        size = path.stat().st_size
        if size > max_bytes:
            raise ToolError('INPUT_TOO_LARGE', f'{label} exceeds the {max_bytes} byte limit.', [size], ['Reduce the file or raise the documented limit in a custom config.'])
        deadline.check(f'read {label}')
        return path.read_bytes()

    try:
        return retry_io(operation, cfg, f'read {label}')
    except ToolError:
        raise


def read_text_limited(path: Path, max_bytes: int, label: str, config: dict) -> str:
    data = read_bytes_limited(path, max_bytes, label, config)
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ToolError('INVALID_ENCODING', f'{label} must be UTF-8 text.', [str(exc)], ['Convert the source to UTF-8 text before rerunning.'])


def load_json_file(path: Path, max_bytes: int, label: str, config: dict) -> dict | list:
    text = read_text_limited(path, max_bytes, label, config)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolError('INVALID_JSON', f'{label} is not valid JSON.', [f'line {exc.lineno}, column {exc.colno}: {exc.msg}'], ['Fix the JSON syntax and rerun.'])


def find_skill_root() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [Path.cwd(), here.parent]
    for candidate in candidates:
        if (candidate / TOOL_FOLDER / 'tools-game-maker.py').is_file():
            return candidate
    return Path.cwd()


def validate_reference(ref: str) -> str:
    if not isinstance(ref, str) or not ref:
        raise ToolError('INVALID_REFERENCE', 'Resource reference must be a non-empty string.', [repr(ref)])
    if '\\' in ref or ref.startswith('/') or ref.startswith('~') or re.match(r'^[a-zA-Z]:', ref) or '://' in ref:
        raise ToolError('INVALID_REFERENCE', 'Resource references must be relative POSIX paths from the future skill root.', [ref], ['Use a reference such as ultimate-game-maker/asset-catalog.json.'])
    pure = PurePosixPath(ref)
    if pure.is_absolute() or '..' in pure.parts:
        raise ToolError('INVALID_REFERENCE', 'Resource references cannot use parent traversal.', [ref])
    return ref


def resolve_reference(ref: str, skill_root: Path | None = None) -> Path:
    validate_reference(ref)
    root = skill_root or find_skill_root()
    candidate = root / ref
    if candidate.is_file():
        return candidate
    # Fallback for direct execution of a supporting tool from the Skill Tool folder.
    sibling = Path(__file__).resolve().parent / PurePosixPath(ref).name
    if sibling.is_file():
        return sibling
    raise ToolError('MISSING_RESOURCE', 'A referenced resource does not exist at the exact case-sensitive path.', [ref, str(candidate)], ['Restore the missing prefixed file from the delivery ZIP.'])


def load_default_config(tool_dir: Path) -> dict:
    path = tool_dir / 'tools-default-config.json'
    cfg = {'retry': {'attempts': 1, 'backoff_seconds': 0}, 'runtime': {'deadline_seconds': 30}}
    return load_json_file(path, 1024 * 1024, 'tools-default-config.json', cfg)


def bootstrap_modules(base_dir: Path | None = None, skip: set[str] | None = None) -> SimpleNamespace:
    """Load fixed sibling modules in dependency order without arbitrary imports."""
    here = Path(__file__).resolve().parent if base_dir is None else Path(base_dir).resolve()
    skip = skip or set()
    loaded: dict[str, object] = {}
    for name, filename in MODULE_FILES:
        if name in skip:
            continue
        if name in sys.modules:
            loaded[name] = sys.modules[name]
            continue
        path = here / filename
        if not path.is_file():
            raise ToolError('MISSING_TOOL_MODULE', 'A required supporting tool file is missing.', [filename], ['Restore the complete Skill Tool folder.'])
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ToolError('MODULE_LOAD_FAILED', 'A supporting tool file could not be loaded.', [filename])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    short_names = {
        'source_parser': 'ugm_source_parser',
        'registry': 'ugm_registry',
        'validation': 'ugm_validation',
        'capability_packets': 'ugm_capability_packets',
        'docs': 'ugm_docs',
        'prototype': 'ugm_prototype',
        'planner': 'ugm_planner',
        'production_registry': 'ugm_production_registry',
        'production_planner': 'ugm_production_planner',
        'evidence_gates': 'ugm_evidence_gates',
        'production_engine': 'ugm_production_engine',
        'selftest': 'ugm_selftest',
    }
    return SimpleNamespace(**{short: loaded[long] for short, long in short_names.items() if long in loaded})


class OutputWriter:
    """Atomic output with bounded cleanup and preservation of pre-existing files."""

    def __init__(self, root: Path, config: dict, approve_overwrite: bool = False,
                 allow_existing: bool = False, cleanup_on_failure: bool | None = None,
                 deadline: Deadline | None = None):
        self.root = Path(root)
        self.config = config
        self.approve_overwrite = bool(approve_overwrite)
        self.allow_existing = bool(allow_existing)
        self.cleanup_on_failure = config.get('cleanup', {}).get('remove_partial_on_failure', True) if cleanup_on_failure is None else cleanup_on_failure
        self.deadline = deadline
        self.created_files: list[Path] = []
        self.created_dirs: list[Path] = []
        self.rollback: dict[Path, bytes] = {}
        self.committed = False

    def _check_deadline(self, stage: str) -> None:
        if self.deadline:
            self.deadline.check(stage)

    def prepare(self) -> None:
        self._check_deadline('prepare output')
        if self.root.exists():
            if not self.root.is_dir():
                raise ToolError('INVALID_OUTPUT', 'Output path exists but is not a directory.', [str(self.root)])
            if any(self.root.iterdir()) and not self.allow_existing and not self.approve_overwrite:
                raise ToolError('APPROVAL_REQUIRED', 'Output directory is not empty and overwrite was not approved.', [str(self.root)], ['Use a new --output path, or pass --approve-overwrite --confirm APPROVE.'])
        else:
            parents = []
            parent = self.root.parent
            while parent != Path('') and parent != Path('.') and not parent.exists():
                parents.append(parent)
                parent = parent.parent
            for path in reversed(parents):
                path.mkdir()
                self.created_dirs.append(path)
            self.root.mkdir()
            self.created_dirs.append(self.root)

    @staticmethod
    def _relative(relative: str) -> PurePosixPath:
        if not isinstance(relative, str) or not relative:
            raise ToolError('INVALID_OUTPUT_PATH', 'Generated file paths must be non-empty strings.', [repr(relative)])
        pure = PurePosixPath(relative)
        if '\\' in relative or pure.is_absolute() or '..' in pure.parts or relative.startswith('~'):
            raise ToolError('INVALID_OUTPUT_PATH', 'Generated file paths must stay inside the output root.', [relative])
        return pure

    def write(self, relative: str, content: str | bytes) -> Path:
        pure = self._relative(relative)
        self._check_deadline(f'write {relative}')
        target = self.root.joinpath(*pure.parts)
        if target.exists() and target.is_dir():
            raise ToolError('INVALID_OUTPUT_PATH', 'A generated file path collides with a directory.', [relative])
        parent = target.parent
        if not parent.exists():
            missing = []
            cursor = parent
            while not cursor.exists():
                missing.append(cursor)
                cursor = cursor.parent
            for path in reversed(missing):
                path.mkdir()
                self.created_dirs.append(path)
        data = content.encode('utf-8') if isinstance(content, str) else content
        max_bytes = int(self.config.get('limits', {}).get('max_generated_file_bytes', 5 * 1024 * 1024))
        if len(data) > max_bytes:
            raise ToolError('OUTPUT_TOO_LARGE', f'Generated file {relative} exceeds the {max_bytes} byte limit.', [len(data)], ['Select fewer categories or skills, or raise the documented limit in a custom config.'])
        existed = target.exists()
        if existed:
            if not self.approve_overwrite:
                raise ToolError('APPROVAL_REQUIRED', f'Output file {relative} already exists and overwrite was not approved.', [relative], ['Preserve the existing file, use a new output path, or pass --approve-overwrite --confirm APPROVE.'])
            if target not in self.rollback:
                self.rollback[target] = retry_io(lambda: target.read_bytes(), self.config, f'backup {relative}')
        temp_name = None

        def operation():
            nonlocal temp_name
            with tempfile.NamedTemporaryFile(mode='wb', dir=str(parent), prefix='.ugm-', suffix='.tmp', delete=False) as handle:
                temp_name = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
            temp_name = None
            return target

        try:
            retry_io(operation, self.config, f'write {relative}')
        finally:
            if temp_name is not None and temp_name.exists():
                temp_name.unlink()
        if not existed:
            self.created_files.append(target)
        return target

    def finish(self) -> None:
        self.committed = True
        self.rollback.clear()
        self.created_files.clear()
        self.created_dirs.clear()

    def cleanup(self) -> None:
        if self.committed or not self.cleanup_on_failure:
            return
        for target, original in self.rollback.items():
            try:
                with tempfile.NamedTemporaryFile(mode='wb', dir=str(target.parent), prefix='.ugm-rollback-', suffix='.tmp', delete=False) as handle:
                    temp = Path(handle.name)
                    handle.write(original)
                os.replace(temp, target)
            except OSError:
                pass
        for target in reversed(self.created_files):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        for directory in reversed(self.created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        self.rollback.clear()
        self.created_files.clear()
        self.created_dirs.clear()

    def __enter__(self):
        self.prepare()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.finish()
        else:
            self.cleanup()
        return False


def write_single_output(path_str: str, content: str | bytes, config: dict, deadline: Deadline,
                        approve_overwrite: bool = False) -> dict:
    """Write one configured output file with the same atomic and rollback rules as plan."""
    path = Path(path_str)
    parent = path.parent if path.parent != Path('') else Path('.')
    name = path.name
    if not name or name in ('.', '..'):
        raise ToolError('INVALID_OUTPUT', 'A single-file output path needs a filename.', [path_str])
    with OutputWriter(parent, config, approve_overwrite=approve_overwrite,
                      allow_existing=True, deadline=deadline) as writer:
        writer.write(name, content)
    return {
        'path': str(path),
        'bytes': len(content.encode('utf-8') if isinstance(content, str) else content),
        'written': True,
    }
