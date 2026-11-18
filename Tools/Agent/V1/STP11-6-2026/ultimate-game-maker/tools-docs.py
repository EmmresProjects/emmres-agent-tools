#!/usr/bin/env python3
"""Bounded local official-document evidence extraction and version notes."""
from __future__ import annotations

import json
import re
from pathlib import Path

import ugm_common as common
import ugm_source_parser as source_parser


def _version_strings(brief: dict, route: dict) -> list[str]:
    values = [
        route['requested_engine'],
        brief.get('engine_version'),
        brief.get('language'),
        brief.get('language_version'),
        brief.get('target_platform'),
    ]
    environment = brief.get('build_environment', {})
    if isinstance(environment, dict):
        values.extend(str(value) for value in environment.values() if isinstance(value, (str, int, float)))
    result = []
    for value in values:
        text = str(value or '').strip()
        if text and text not in result:
            result.append(text)
    return result


def ingest(paths: list[Path], brief: dict, route: dict, config: dict,
           deadline: common.Deadline) -> dict:
    tokens = [str(token) for token in config['version_intelligence']['required_version_tokens']]
    exact_versions = _version_strings(brief, route)
    records = []
    for index, path in enumerate(paths, start=1):
        deadline.check(f'ingest documentation file {index}')
        raw = common.read_text_limited(path, int(config['limits']['max_doc_bytes']), f'documentation file {index}', config)
        text, markup_stats = source_parser.strip_markup(raw)
        lines = [' '.join(line.split()) for line in text.splitlines() if line.strip()]
        joined = '\n'.join(lines)
        lowered = joined.lower()
        found_tokens = sorted({token for token in tokens if token.lower() in lowered})
        exact_matches = sorted({value for value in exact_versions if value.lower() in lowered})
        migration_signals = []
        for pattern in (r'\bdeprecat\w*\b', r'\bremoved?\b', r'\bmigrat\w*\b', r'\bbreaking change\b', r'\brenamed?\b'):
            if re.search(pattern, joined, flags=re.I):
                migration_signals.append(pattern.replace(r'\b', '').replace(r'\w*', ''))
        snippets = []
        for value in (exact_matches + found_tokens)[:5]:
            needle = value.lower()
            for line_number, line in enumerate(lines):
                if needle in line.lower():
                    snippets.append({'line': line_number + 1, 'match': value, 'excerpt': line[:240]})
                    break
        warnings = []
        if not found_tokens:
            warnings.append('No configured version or migration vocabulary was found in this file.')
        if not exact_matches:
            warnings.append('No exact version string from the brief was found in this file.')
        records.append({
            'path': str(path),
            'extension': path.suffix.lower(),
            'bytes': path.stat().st_size,
            'line_count': len(lines),
            'markup_normalization': markup_stats,
            'version_tokens_found': found_tokens,
            'exact_version_matches': exact_matches,
            'migration_signals': sorted(set(migration_signals)),
            'excerpts': snippets[:5],
            'authenticity': 'unverified-user-supplied-local-file',
            'warnings': warnings,
        })
    all_matches = sorted({value for record in records for value in record['exact_version_matches']})
    all_tokens = sorted({value for record in records for value in record['version_tokens_found']})
    missing_exact = [value for value in exact_versions if value not in all_matches]
    return {
        'schema_id': 'ugm-docs-evidence-1.0',
        'engine_request': route['requested_engine'],
        'profile_id': route['profile']['id'],
        'version_evidence': route['version_evidence'],
        'missing_version_evidence': route['missing_version_evidence'],
        'exact_version_strings_checked': exact_versions,
        'exact_version_strings_found': all_matches,
        'exact_version_strings_not_found': missing_exact,
        'version_tokens_found': all_tokens,
        'required_version_tokens': tokens,
        'files': records,
        'external_fetch': {
            'enabled': False,
            'status': 'not-attempted',
            'reason': config['version_intelligence']['external_fetch']['reason'],
        },
        'authenticity_note': config['version_intelligence']['unverified_authenticity_note'],
        'status': 'complete-local-evidence' if records and all_matches else 'incomplete-local-evidence',
    }


def render_docs_json(evidence: dict) -> str:
    return json.dumps(evidence, indent=2, ensure_ascii=False) + '\n'


def render_version_notes(brief: dict, route: dict, evidence: dict | None = None) -> str:
    profile = route['profile']
    rows = [
        ('Requested engine', route['requested_engine']),
        ('Routed profile', profile['id']),
        ('Language', brief.get('language')),
        ('Language version', brief.get('language_version')),
        ('Engine version', brief.get('engine_version')),
        ('Target platform', brief.get('target_platform')),
        ('Source generation', profile['source_generation']),
        ('Bundled runtime', 'none'),
        ('Version status', route['version_status']),
    ]
    lines = [
        '# Version and Compatibility Notes',
        '',
        'These notes are generated from the brief, the configured engine profile, and optional user-supplied local official documentation. The tool does not guess behavior across releases and does not fetch documentation.',
        '',
        '| Field | Value |',
        '| --- | --- |',
    ]
    lines.extend(f'| {key} | `{value}` |' for key, value in rows)
    lines.extend([
        '',
        '## Required interoperability data',
        '',
    ])
    lines.extend(f'- {item}' for item in profile.get('version_fields', []))
    lines.extend([
        '',
        '## Missing evidence',
        '',
    ])
    if route['missing_version_evidence']:
        lines.extend(f'- `{item}`: supply an exact value or local official documentation.' for item in route['missing_version_evidence'])
    else:
        lines.append('- None from the routed profile; still validate the actual external environment before building.')
    if evidence:
        lines.extend([
            '',
            '## Local documentation evidence',
            '',
            f'- Status: `{evidence["status"]}`.',
            f'- Files inspected: **{len(evidence["files"])}**.',
            f'- Exact version strings found: {", ".join("`" + value + "`" for value in evidence["exact_version_strings_found"]) or "none"}.',
            f'- Exact version strings not found: {", ".join("`" + value + "`" for value in evidence["exact_version_strings_not_found"]) or "none"}.',
            '- Authenticity: user-supplied local files are not verified as official by this tool.',
        ])
    else:
        lines.extend([
            '',
            '## Local documentation evidence',
            '',
            '- No local documentation files were supplied with this run.',
            '- Use the `docs` command or `plan --docs` with local UTF-8 text, Markdown, HTML, or JSON files.',
        ])
    lines.extend([
        '',
        '## Version-intelligence checklist',
        '',
        '- Lock engine, editor, SDK, compiler, language standard, packages, and build tooling to exact values.',
        '- Compare API and syntax differences against supplied local official documentation.',
        '- Record deprecations, removals, renamed APIs, and migration steps.',
        '- Verify plugin and package compatibility from supplied manifests.',
        '- Preserve passing work and rerun linked checks from a clean state after the smallest repair.',
        '',
        '## External integrations',
        '',
        '- No network adapter, engine installer, SDK, compiler, store, or certification service is bundled.',
        '- Human approval is required before publishing, deployment, credential, financial, or security-sensitive actions.',
        '',
    ])
    return '\n'.join(lines)
