"""Evidence-based, read-only filesystem inspections; no symlinks are followed."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import shutil
import stat
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from workspace_core import Context, ToolError, _SECRET_RE, _PRIVATE_RE, file_sha, load_json, string, strings, utc

_TEXT_EXT = {'.txt', '.md', '.json', '.jsonl', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.py', '.js', '.ts', '.html', '.css', '.xml', '.csv', '.sh', '.sql', '.rst'}
_SECRET_KEY = re.compile(r'(?i)(?:token|secret|passw|credential|api[_-]?key|private[_-]?key)')
_REF_PATTERNS = [
    ('html', re.compile(r'''(?:src|href)\s*=\s*["']([^"']+)["']''', re.I)),
    ('markdown', re.compile(r'!?(?:\[[^\]]*\])\(([^)]+)\)')),
    ('css', re.compile(r'url\(["\']?([^\)"\']+)')),
    ('python', re.compile(r'^\s*(?:from|import)\s+([a-zA-Z_][\w.]*)', re.M)),
]


def _rows(ctx: Context, *, hashes: bool = False, excluded: bool = False):
    return ctx.entries(ctx.request_paths(), hashes=hashes, include_excluded=excluded)


def _layout(ctx: Context) -> dict:
    return load_json(Path(__file__).with_name('asset-layout.json'))


def _counts(rows: list[dict]) -> dict:
    return {'kinds': dict(Counter(x['kind'] for x in rows)),
            'file_bytes': sum(x['size'] for x in rows if x['kind'] == 'file'),
            'extensions': dict(sorted(Counter(Path(x['path']).suffix.lower() or '[none]' for x in rows if x['kind'] == 'file').items()))}


def _secrets(ctx: Context, rows: list[dict]) -> dict:
    findings, skipped = [], []
    for row in rows:
        if row['kind'] != 'file' or Path(row['path']).suffix.lower() not in _TEXT_EXT:
            continue
        if row['size'] > ctx.limits['max_text_bytes']:
            skipped.append(row['path']); continue
        try:
            text = ctx.read_text(row['path'])
        except ToolError:
            skipped.append(row['path']); continue
        for n, line in enumerate(text.splitlines(), 1):
            matches = []
            if _PRIVATE_RE.search(line):
                matches.append('private-key-marker')
            if _SECRET_RE.search(line):
                matches.append('credential-assignment')
            if matches:
                findings.append({'path': row['path'], 'line': n, 'types': matches})
                if len(findings) >= 500:
                    return {'findings': findings, 'truncated': True, 'skipped': skipped}
    return {'findings': findings, 'truncated': False, 'skipped': skipped,
            'note': 'Heuristic scan; no secret values returned. Other formats and encodings may not be covered.'}


def _dependency_refs(ctx: Context, rows: list[dict]) -> dict:
    references, skipped = [], []
    for row in rows:
        if row['kind'] != 'file' or row['size'] > ctx.limits['max_text_bytes']:
            continue
        ext = Path(row['path']).suffix.lower()
        if ext not in {'.html', '.htm', '.css', '.md', '.py'}:
            continue
        try:
            text = ctx.read_text(row['path'])
        except ToolError:
            skipped.append(row['path']); continue
        patterns = _REF_PATTERNS[:1] if ext in ('.html', '.htm') else [_REF_PATTERNS[2]] if ext == '.css' else [_REF_PATTERNS[1]] if ext == '.md' else [_REF_PATTERNS[3]]
        for kind, pattern in patterns:
            for m in pattern.finditer(text):
                raw = m.group(1).split('#', 1)[0].split('?', 1)[0].strip()
                if not raw or raw.startswith(('http:', 'https:', 'data:', 'mailto:', '//', '#')):
                    continue
                if len(references) >= 2000:
                    return {'references': references, 'truncated': True,
                            'missing': sum(x.get('status') == 'missing' for x in references), 'skipped': skipped}
                if kind == 'python':
                    references.append({'from': row['path'], 'module': raw, 'kind': kind, 'status': 'import-unverified'})
                    continue
                try:
                    candidate = (Path(row['path']).parent / raw).as_posix()
                    # Normalization is deliberate only for inspecting references; the core path guard is authoritative.
                    candidate = os.path.normpath(candidate).replace('\\', '/')
                    target = ctx.path(candidate)
                    status = 'exists' if target.exists() else 'missing'
                except ToolError:
                    candidate, status = raw, 'outside-or-unsafe'
                references.append({'from': row['path'], 'to': candidate, 'kind': kind, 'status': status})
    return {'references': references, 'missing': sum(x.get('status') == 'missing' for x in references), 'skipped': skipped,
            'note': 'Static references only; runtime imports, generated links and external services are not resolved.'}


def _classify(ctx: Context, rows: list[dict]) -> dict:
    rules = _layout(ctx)['classification']
    groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row['kind'] != 'file':
            continue
        ext = Path(row['path']).suffix.lower()
        label = next((g for g, suffixes in rules.items() if ext in suffixes), 'other')
        groups[label].append(row['path'])
    return {'groups': dict(groups), 'rules': rules, 'note': 'Extension-based classification; file signatures should be validated before execution.'}


def _cleanup(ctx: Context, rows: list[dict], *, variant: str = 'cleanup') -> dict:
    candidates, protected, uncertain = [], [], []
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - ctx.policy['retention_days'] * 86400
    for row in rows:
        name = row['path']; prefix = name.split('/')[0]
        if row['kind'] != 'file':
            continue
        if ctx.protected(name) or prefix in ('reports', 'backups'):
            protected.append(name); continue
        if prefix not in ctx.policy['cleanup_prefixes']:
            continue
        if row['modified_ns'] / 1e9 > cutoff:
            uncertain.append({'path': name, 'reason': 'inside retention window'}); continue
        if variant == 'orphans' and not Path(name).suffix.lower() in ('.tmp', '.log', '.cache', '.part', '.pyc'):
            uncertain.append({'path': name, 'reason': 'ownership and references unproven'}); continue
        candidates.append({'path': name, 'bytes': row['size'], 'age_days': round((dt.datetime.now(dt.timezone.utc).timestamp()-row['modified_ns']/1e9)/86400, 1),
                           'risk': 'review references and active jobs before quarantine'})
    return {'candidates': candidates, 'potential_bytes': sum(x['bytes'] for x in candidates), 'protected': protected,
            'uncertain': uncertain, 'retention_days': ctx.policy['retention_days'],
            'note': 'Candidate discovery only. Use safe-file quarantine with explicit paths and approval, not automatic deletion.'}


def _snapshot(ctx: Context) -> dict:
    rows, stats = _rows(ctx, hashes=True)
    return {'schema': 'workspace-snapshot-v1', 'captured_at': utc(), 'root_label': ctx.root.name,
            'platform': {'python': sys.version.split()[0], 'os': os.name, 'platform': sys.platform},
            'disk': dict(zip(('total', 'used', 'free'), shutil.disk_usage(ctx.root))),
            'entries': rows, 'scan': stats, 'counts': _counts(rows)}


def _diff(ctx: Context) -> dict:
    before = string(ctx.request.get('before'), 'before')
    after = string(ctx.request.get('after'), 'after')
    left = load_json(ctx.path(before, must_exist=True), ctx.limits['max_archive_bytes'])
    right = load_json(ctx.path(after, must_exist=True), ctx.limits['max_archive_bytes'])
    # The CLI's --output persists a complete structured response; accept that envelope as well.
    left = left.get('result', left) if isinstance(left, dict) else left
    right = right.get('result', right) if isinstance(right, dict) else right
    for value in (left, right):
        if not isinstance(value, dict) or value.get('schema') != 'workspace-snapshot-v1' or not isinstance(value.get('entries'), list):
            raise ToolError('invalid_input', 'Both inputs must be workspace-snapshot-v1 JSON')
    a = {r['path']: r for r in left['entries']}; b = {r['path']: r for r in right['entries']}
    added = sorted(b.keys()-a.keys()); removed = sorted(a.keys()-b.keys())
    changed = []
    for p in sorted(a.keys() & b.keys()):
        fields = [k for k in ('kind', 'size', 'mode', 'sha256', 'modified_ns') if a[p].get(k) != b[p].get(k)]
        if fields:
            changed.append({'path': p, 'fields': fields})
    return {'added': added, 'removed': removed, 'changed': changed, 'counts': {'added': len(added), 'removed': len(removed), 'changed': len(changed)},
            'disk_free_change': right.get('disk', {}).get('free', 0)-left.get('disk', {}).get('free', 0)}


def run(ctx: Context, variant: str) -> dict:
    if variant == 'diff':
        return _diff(ctx)
    if variant == 'snapshot':
        return _snapshot(ctx)
    hashes = variant in {'duplicates', 'index', 'secret-final'} or bool(ctx.request.get('include_hashes', False))
    if type(ctx.request.get('include_hashes', False)) is not bool:
        raise ToolError('invalid_input', 'include_hashes must be boolean')
    if type(ctx.request.get('include_excluded', False)) is not bool:
        raise ToolError('invalid_input', 'include_excluded must be boolean')
    rows, stats = _rows(ctx, hashes=hashes, excluded=ctx.request.get('include_excluded', False))
    if variant == 'inventory':
        return {'entries': rows, 'scan': stats, 'counts': _counts(rows),
                'environment_variable_names': sorted(os.environ.keys())[:500],
                'note': 'Environment variable values are deliberately not included.'}
    if variant == 'hidden':
        found = [x for x in rows if any(seg.startswith('.') for seg in x['path'].split('/')) or x['path'].endswith(('.lock', '.tmp', '.part'))]
        return {'artifacts': found, 'count': len(found), 'scan': stats}
    if variant == 'recent':
        limit = ctx.request.get('limit', 50); limit = min(200, int(limit)) if type(limit) is int and limit > 0 else 50
        return {'recent': sorted(rows, key=lambda x: (-x['modified_ns'], x['path']))[:limit],
                'note': 'Timestamps and file types are evidence; producing commands cannot be inferred reliably.'}
    if variant == 'dependencies':
        return _dependency_refs(ctx, rows)
    if variant == 'health':
        disk = shutil.disk_usage(ctx.root)
        issues = []
        for r in rows:
            if r['kind'] == 'symlink' and r.get('broken'):
                issues.append({'path': r['path'], 'type': 'broken-link'})
            if r['kind'] == 'special':
                issues.append({'path': r['path'], 'type': 'special-file-not-readable-as-regular-data'})
            if r['kind'] == 'file' and r['path'].endswith(('.lock', '.part')):
                issues.append({'path': r['path'], 'type': 'possible-interrupted-or-active-output'})
            if r['kind'] == 'file' and r['size'] == 0:
                issues.append({'path': r['path'], 'type': 'empty-file-review'})
        if disk.free < 100 * 1024 * 1024:
            issues.append({'type': 'low-disk-space', 'free_bytes': disk.free})
        return {'issues': issues, 'disk': {'total': disk.total, 'used': disk.used, 'free': disk.free},
                'root_writable': os.access(ctx.root, os.W_OK), 'entries_checked': len(rows)}
    if variant == 'index':
        query = ctx.request.get('query')
        if query is not None:
            query = string(query, 'query', maxlen=200)
        hits, indexed, skipped = [], [], []
        for r in rows:
            if r['kind'] != 'file':
                continue
            record = {'path': r['path'], 'size': r['size'], 'sha256': r.get('sha256')}
            indexed.append(record)
            if query and query.lower() in r['path'].lower():
                hits.append({'path': r['path'], 'match': 'filename'})
            if query and Path(r['path']).suffix.lower() in _TEXT_EXT and r['size'] <= ctx.limits['max_text_bytes']:
                try:
                    for i, line in enumerate(ctx.read_text(r['path']).splitlines(), 1):
                        if query.lower() in line.lower():
                            hits.append({'path': r['path'], 'line': i, 'match': 'text'})
                            if len(hits) >= 1000:
                                break
                except ToolError:
                    skipped.append(r['path'])
            if len(hits) >= 1000:
                break
        return {'index': indexed, 'hits': hits, 'hit_limit': 1000, 'query': query, 'skipped_text': skipped}
    if variant == 'classify':
        return _classify(ctx, rows)
    if variant == 'duplicates':
        sizes: dict[int, list[dict]] = defaultdict(list)
        for r in rows:
            if r['kind'] == 'file' and r.get('sha256'):
                sizes[r['size']].append(r)
        hashes: dict[str, list[str]] = defaultdict(list)
        for group in sizes.values():
            if len(group) > 1:
                for row in group:
                    hashes[row['sha256']].append(row['path'])
        dups = [{'sha256': h, 'paths': p} for h,p in hashes.items() if len(p) > 1]
        return {'duplicate_groups': dups, 'reclaimable_bytes_estimate': sum((len(g['paths'])-1)*next(r['size'] for r in rows if r['path']==g['paths'][0]) for g in dups),
                'note': 'No files removed; matches without a hash are not considered duplicates.'}
    if variant in ('structure', 'boundaries'):
        expected = strings(ctx.request.get('required', _layout(ctx)['folders'] if variant == 'structure' else []), 'required', maximum=200)
        missing = [p for p in expected if not ctx.path(p).exists()]
        empty = [r['path'] for r in rows if r['kind'] == 'directory' and not any(x['path'].startswith(r['path'] + '/') for x in rows)]
        exposed = [r['path'] for r in rows if r['kind'] in ('file','directory') and int(r['mode'], 8) & 0o002]
        return {'valid': not missing and not exposed, 'missing_required': missing,
                'empty_directories': empty, 'world_writable': exposed,
                'observed_top_level': sorted({r['path'].split('/')[0] for r in rows}),
                'note': 'Boundary inspection checks layout and simple permissions, not ACLs or mount isolation.'}
    if variant == 'symlinks':
        return {'symlinks': [x for x in rows if x['kind'] == 'symlink'],
                'note': 'Links are reported but not followed; an absolute target requires manual review.'}
    if variant in ('metadata', 'permissions'):
        return {'entries': [{'path': x['path'], 'kind': x['kind'], 'mode': x['mode'], 'owner': x['owner'], 'modified_ns': x['modified_ns'],
                             'effective_read': os.access(ctx.root / x['path'], os.R_OK), 'effective_write': os.access(ctx.root / x['path'], os.W_OK)} for x in rows],
                'note': 'POSIX mode and effective access only; ACLs, inheritance, and ownership changes are not applied.'}
    if variant in ('caches', 'scratch'):
        prefixes = ctx.policy['cleanup_prefixes'] if variant == 'caches' else ['scratch']
        matches = [x for x in rows if x['path'].split('/')[0] in prefixes]
        return {'artifacts': matches, 'bytes': sum(x['size'] for x in matches if x['kind']=='file'), 'scan': stats}
    if variant == 'stale':
        edges = ctx.request.get('dependencies', [])
        if not isinstance(edges, list) or len(edges) > 200:
            raise ToolError('invalid_input', 'dependencies must be a list of up to 200 {input,output} pairs')
        stale = []
        for edge in edges:
            if not isinstance(edge, dict):
                raise ToolError('invalid_input', 'Invalid dependency pair')
            source = ctx.path(string(edge.get('input'), 'input'), must_exist=True)
            target = ctx.path(string(edge.get('output'), 'output'))
            if not target.exists() or target.stat().st_mtime_ns < source.stat().st_mtime_ns:
                stale.append({'input': edge['input'], 'output': edge['output'], 'reason': 'missing' if not target.exists() else 'older-than-input'})
        partial = [x['path'] for x in rows if x['kind']=='file' and x['path'].endswith(('.part','.partial','.tmp'))]
        return {'stale': stale, 'partial_files': partial, 'note': 'Mtime is only a hint; provide explicit input/output pairs for meaningful staleness checks.'}
    if variant in ('cleanup', 'orphans'):
        return _cleanup(ctx, rows, variant=variant)
    if variant == 'exclusions':
        return {'protected_prefixes': ctx.policy['protected_prefixes'], 'cleanup_prefixes': ctx.policy['cleanup_prefixes'],
                'excluded_names': ctx.policy['excluded_names'], 'protected_observed': [r['path'] for r in rows if ctx.protected(r['path'])]}
    if variant in ('secrets','secret-final'):
        if variant == 'secret-final' and ctx.request.get('paths', ['.']) == ['.']:
            raise ToolError('invalid_input', 'Final artifact audit requires explicit paths')
        return _secrets(ctx, rows)
    if variant == 'media':
        suffixes = set(_layout(ctx)['classification']['media'])
        media = [r for r in rows if r['kind'] == 'file' and Path(r['path']).suffix.lower() in suffixes]
        groups = defaultdict(list)
        for r in media:
            match = re.match(r'^(.*?)(\d{3,})(\.(?:png|jpg|jpeg))$', r['path'], re.I)
            if match:
                groups[(match.group(1), len(match.group(2)), match.group(3).lower())].append(int(match.group(2)))
        sequences = []
        for (prefix, width, ext), numbers in sorted(groups.items()):
            numbers = sorted(set(numbers))
            missing = sorted(set(range(numbers[0], min(numbers[-1], numbers[0] + 10000) + 1)) - set(numbers))[:100]
            sequences.append({'prefix': prefix, 'width': width, 'extension': ext, 'first': numbers[0],
                              'last': numbers[-1], 'frames': len(numbers), 'missing_numbers_sample': missing})
        return {'media': media, 'total_bytes': sum(r['size'] for r in media), 'frame_sequences': sequences,
                'note': 'Groups numbered frame sequences without changing them; format integrity requires domain.media.'}
    raise ToolError('internal_error', 'Unimplemented inspection route', route=variant)
