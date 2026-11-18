#!/usr/bin/env python3
"""Catalog, capability selection, and engine-routing registry."""
from __future__ import annotations

import re
from pathlib import Path

import ugm_common as common


def _override_or_reference(override: str | None, default_ref: str, label: str,
                           skill_root: Path | None = None) -> Path:
    if override:
        path = Path(override)
        if not path.is_file():
            raise common.ToolError('MISSING_RESOURCE', f'{label} override does not exist.', [override], ['Provide an existing file path or remove the override.'])
        return path
    return common.resolve_reference(default_ref, skill_root)


def load_catalog(config: dict, deadline: common.Deadline, override: str | None = None,
                 skill_root: Path | None = None) -> dict:
    deadline.check('load catalog')
    path = _override_or_reference(override, config['paths']['catalog'], 'catalog', skill_root)
    catalog = common.load_json_file(path, int(config['limits']['max_catalog_bytes']), 'asset-catalog.json', config)
    validate_catalog(catalog)
    return catalog


def validate_catalog(catalog: dict) -> None:
    if not isinstance(catalog, dict) or catalog.get('schema_id') != 'ugm-catalog-1.0':
        raise common.ToolError('INVALID_CATALOG', 'The catalog schema is not ugm-catalog-1.0.', [catalog.get('schema_id') if isinstance(catalog, dict) else type(catalog).__name__])
    handlers = catalog.get('handlers')
    if not isinstance(handlers, list) or len(handlers) != 33:
        raise common.ToolError('INVALID_CATALOG', 'The unified catalog must contain exactly 33 category handlers.', [len(handlers) if isinstance(handlers, list) else None])
    skill_slugs: list[str] = []
    operations: list[str] = []
    for handler in handlers:
        for key in ('id', 'category', 'category_slug', 'operation', 'outcome', 'description', 'example_command', 'artifacts', 'controls', 'checks', 'recovery', 'interoperability', 'skills'):
            if key not in handler:
                raise common.ToolError('INVALID_CATALOG', 'A category handler is missing a required field.', [handler.get('id'), key])
        if len(handler['skills']) != 10:
            raise common.ToolError('INVALID_CATALOG', 'Each category handler must contain 10 named specialists.', [handler['id'], len(handler['skills'])])
        operations.append(handler['operation'])
        for skill in handler['skills']:
            for key in ('name', 'slug', 'description', 'focus', 'deliverables', 'checks'):
                if key not in skill or not skill[key]:
                    raise common.ToolError('INVALID_CATALOG', 'A specialist entry is missing a required field.', [handler['id'], skill.get('name'), key])
            skill_slugs.append(skill['slug'])
    if len(skill_slugs) != 330 or len(set(skill_slugs)) != 330:
        raise common.ToolError('INVALID_CATALOG', 'The unified catalog must contain 330 unique specialist slugs.', [len(skill_slugs), len(set(skill_slugs))])
    if len(set(operations)) != 33:
        raise common.ToolError('INVALID_CATALOG', 'Every category handler needs a unique operation.', [len(set(operations))])


def load_engine_profiles(config: dict, deadline: common.Deadline, override: str | None = None,
                         skill_root: Path | None = None) -> dict:
    deadline.check('load engine profiles')
    path = _override_or_reference(override, config['paths']['engine_profiles'], 'engine profiles', skill_root)
    profiles = common.load_json_file(path, int(config['limits']['max_engine_profiles_bytes']), 'asset-engine-profiles.json', config)
    if not isinstance(profiles, dict) or profiles.get('schema_id') != 'ugm-engine-profiles-1.0':
        raise common.ToolError('INVALID_ENGINE_PROFILES', 'The engine-profile schema is not ugm-engine-profiles-1.0.')
    if not isinstance(profiles.get('profiles'), list) or not profiles['profiles']:
        raise common.ToolError('INVALID_ENGINE_PROFILES', 'At least one engine profile is required.')
    ids = [profile.get('id') for profile in profiles['profiles']]
    if len(ids) != len(set(ids)) or profiles.get('default') not in ids:
        raise common.ToolError('INVALID_ENGINE_PROFILES', 'Engine profile IDs must be unique and the default must exist.', [ids])
    return profiles


def _norm(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')


def resolve_handler(catalog: dict, value: str) -> dict:
    target = _norm(value)
    handlers = catalog['handlers']
    for handler in handlers:
        if target in {_norm(handler['id']), _norm(handler['category']), _norm(handler['category_slug'])}:
            return handler
    # Unique prefix match is allowed for short IDs such as "unity" or "03-unreal".
    matches = [handler for handler in handlers if _norm(handler['id']).startswith(target) or _norm(handler['category_slug']).startswith(target)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise common.ToolError('UNKNOWN_CATEGORY', 'The requested category does not exist in the unified catalog.', [value], ['Run capabilities --format text to see all 33 category IDs.'])
    raise common.ToolError('AMBIGUOUS_CATEGORY', 'The requested category matches more than one handler.', [value, [m['id'] for m in matches]], ['Use an exact category ID from capabilities --format text.'])


def resolve_skill(catalog: dict, value: str, handler_id: str | None = None) -> tuple[dict, dict]:
    target = _norm(value)
    candidates: list[tuple[dict, dict]] = []
    for handler in catalog['handlers']:
        if handler_id and handler['id'] != handler_id:
            continue
        for skill in handler['skills']:
            if target in {_norm(skill['name']), _norm(skill['slug'])}:
                candidates.append((handler, skill))
    if not candidates:
        raise common.ToolError('UNKNOWN_SKILL', 'The requested specialist does not exist in the unified catalog.', [value], ['Run catalog --format text to see all 330 specialist names.'])
    if len(candidates) > 1:
        raise common.ToolError('AMBIGUOUS_SKILL', 'The requested specialist matches more than one entry.', [value, [c[1]['name'] for c in candidates]], ['Use the exact skill slug shown by catalog --format json.'])
    return candidates[0]


def select(catalog: dict, config: dict, categories: list[str] | None = None,
           skills: list[str] | None = None, brief: dict | None = None) -> dict:
    brief = brief or {}
    requested_categories = list(categories or []) + [str(value) for value in brief.get('categories', [])]
    requested_skills = list(skills or []) + [str(value) for value in brief.get('skills', [])]
    if len(requested_categories) > int(config['limits']['max_categories_per_request']):
        raise common.ToolError('TOO_MANY_CATEGORIES', 'Too many categories were requested.', [len(requested_categories)], ['Use at most 33 category selectors.'])
    if len(requested_skills) > int(config['limits']['max_skills_per_request']):
        raise common.ToolError('TOO_MANY_SKILLS', 'Too many specialists were requested.', [len(requested_skills)], ['Use at most 330 specialist selectors.'])
    selected_handlers: dict[str, dict] = {}
    for value in requested_categories:
        handler = resolve_handler(catalog, value)
        entry = selected_handlers.setdefault(handler['id'], {**handler, 'skills': []})
        entry['skills'] = list(handler['skills'])
    for value in requested_skills:
        handler, skill = resolve_skill(catalog, value)
        entry = selected_handlers.setdefault(handler['id'], {**handler, 'skills': []})
        if all(existing['slug'] != skill['slug'] for existing in entry['skills']):
            entry['skills'].append(skill)
    if not selected_handlers:
        for handler in catalog['handlers']:
            selected_handlers[handler['id']] = {**handler, 'skills': list(handler['skills'])}
    handlers = [selected_handlers[handler['id']] for handler in catalog['handlers'] if handler['id'] in selected_handlers]
    skill_count = sum(len(handler['skills']) for handler in handlers)
    return {
        'mode': 'filtered-union' if requested_categories or requested_skills else 'all-capabilities',
        'requested_categories': requested_categories,
        'requested_skills': requested_skills,
        'handler_count': len(handlers),
        'skill_count': skill_count,
        'handlers': handlers,
    }


def route_engine(brief: dict, profiles: dict, override: str | None = None) -> dict:
    requested = str(override or brief.get('engine') or profiles['default']).strip()
    key = _norm(requested)
    aliases = profiles.get('aliases', {})
    profile_id = aliases.get(key, key)
    profile = next((item for item in profiles['profiles'] if item['id'] == profile_id), None)
    if profile is None:
        profile = {
            'id': 'unrecognized',
            'display_name': requested,
            'language': str(brief.get('language') or 'configurable'),
            'platform': str(brief.get('target_platform') or 'configurable'),
            'version_fields': ['engine_version', 'language_version', 'compiler', 'sdk'],
            'source_generation': 'production-packet-only',
            'build': 'documented from supplied official version evidence',
            'runtime_check': 'no external runtime or compiler is bundled',
            'external_runtime_required': True,
            'official_docs_required': True,
            'version_evidence_required': ['engine_version', 'language_version', 'build_environment'],
        }
    evidence = {
        'engine': requested,
        'engine_version': brief.get('engine_version'),
        'language': brief.get('language'),
        'language_version': brief.get('language_version'),
        'target_platform': brief.get('target_platform'),
        'build_environment': brief.get('build_environment', {}),
    }
    missing = []
    for field in profile.get('version_evidence_required', []):
        label = field.replace(' ', '_')
        if label not in evidence or evidence[label] in (None, '', {}):
            if label == 'language standard from brief' or 'language standard' in field:
                if evidence.get('language_version'):
                    continue
            missing.append(field)
    return {
        'requested_engine': requested,
        'profile': profile,
        'version_evidence': evidence,
        'missing_version_evidence': missing,
        'version_status': 'complete' if not missing else 'incomplete',
        'runtime_bundled': False,
        'prototype_available': profile.get('source_generation') == 'runnable-standalone-prototype',
        'external_docs_required': bool(profile.get('official_docs_required')),
    }


def capability_list(catalog: dict, detailed: bool = False) -> dict:
    handlers = []
    for handler in catalog['handlers']:
        item = {
            'id': handler['id'],
            'category': handler['category'],
            'operation': handler['operation'],
            'outcome': handler['outcome'],
            'skill_count': len(handler['skills']),
        }
        if detailed:
            item['skills'] = [{'name': skill['name'], 'slug': skill['slug'], 'focus': skill['focus']} for skill in handler['skills']]
        handlers.append(item)
    return {
        'umbrella': catalog['umbrella'],
        'handler_count': len(handlers),
        'skill_count': sum(item['skill_count'] for item in handlers),
        'handlers': handlers,
    }


def catalog_view(catalog: dict, category: str | None = None, skill: str | None = None,
                 detailed: bool = True) -> dict:
    if skill:
        handler, found = resolve_skill(catalog, skill)
        return {'handler': {key: handler[key] for key in ('id', 'category', 'operation', 'outcome')}, 'skill': found}
    if category:
        handler = resolve_handler(catalog, category)
        view = {key: handler[key] for key in ('id', 'category', 'operation', 'outcome', 'description', 'artifacts', 'controls', 'checks', 'recovery', 'interoperability')}
        if detailed:
            view['skills'] = handler['skills']
        return {'handler': view}
    return capability_list(catalog, detailed=detailed)
