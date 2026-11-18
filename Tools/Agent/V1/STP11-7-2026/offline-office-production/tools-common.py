"""Shared bounded I/O, error contracts, and safe package loading."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import zipfile

MAX_BYTES = 64 * 1024 * 1024
MAX_ITEMS = 1000
DEADLINE = float('inf')
APPROVED = False

class ToolError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)

def fail(code, message):
    raise ToolError(code, message)

def check():
    if time.monotonic() > DEADLINE:
        fail('timeout', 'Operation exceeded its time budget; completed outputs may remain.')

def path(value, exists=False):
    if not isinstance(value, str) or not value or '\\' in value:
        fail('path', 'Use a nonempty relative path with forward slashes.')
    p = Path(value)
    if p.is_absolute() or '..' in p.parts:
        fail('path', 'Absolute paths and parent traversal are prohibited.')
    p = p.resolve()
    if not p.is_relative_to(Path.cwd().resolve()):
        fail('path', 'Symlink escape outside the current working root.')
    if exists and not p.is_file():
        fail('missing_input', 'Input file does not exist: ' + value)
    if exists and p.stat().st_size > MAX_BYTES:
        fail('limit', 'Input exceeds 64 MiB: ' + value)
    return p

def need_approval(reason):
    if not APPROVED:
        fail('approval_required', reason + ' Re-run with --approve only after operator review.')

def require(d, key, kind=str):
    if key not in d or not isinstance(d[key], kind):
        fail('invalid_input', f'{key} is required and must be {kind.__name__}.')
    return d[key]

def read_json(value):
    try:
        return json.loads(path(value, True).read_text(encoding='utf-8'))
    except (UnicodeError, json.JSONDecodeError) as e:
        fail('invalid_json', str(e))

def bounded(items):
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        fail('limit', 'Expected an array with at most 1000 items.')
    return items

def write(value, writer):
    """Atomic artifact commit; never silently overwrite."""
    check()
    dest = path(value)
    if dest.exists():
        need_approval('Replacing an existing file is destructive.')
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.office-', suffix=dest.suffix, dir=dest.parent)
    os.close(fd)
    try:
        writer(Path(tmp))
        check()
        if Path(tmp).stat().st_size > MAX_BYTES:
            fail('limit', 'Output exceeds 64 MiB.')
        if dest.exists() and not APPROVED:
            fail('conflict', 'Output appeared during processing; no replacement performed.')
        os.replace(tmp, dest)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return value

def json_write(value, obj):
    return write(value, lambda p: p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8'))

def zip_check(p):
    with zipfile.ZipFile(p) as z:
        items = z.infolist()
        if len(items) > 5000 or sum(x.file_size for x in items) > 128 * 1024 * 1024:
            fail('unsafe_archive', 'Expanded archive exceeds safety limits.')
        for item in items:
            n = item.filename
            if n.startswith('/') or '\\' in n or '..' in Path(n).parts or item.flag_bits & 1:
                fail('unsafe_archive', 'Unsafe or encrypted archive member.')
            if item.file_size > MAX_BYTES or item.file_size > max(item.compress_size, 1) * 500:
                fail('unsafe_archive', 'Archive member exceeds size or compression ratio limit.')
        if z.testzip():
            fail('corrupt_archive', 'Archive CRC validation failed.')

def load(name):
    # Only static, bundled module names are passed by the application.
    p = Path(__file__).with_name('tools-' + name + '.py')
    spec = importlib.util.spec_from_file_location('office_' + name, p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
