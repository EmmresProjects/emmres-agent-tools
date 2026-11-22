#!/usr/bin/env python3
"""Input, configuration, approval, and bounded local-document validation."""
from __future__ import annotations

import json
import re
from pathlib import Path

import ugm_common as common


def load_config(tool_dir: Path, override_path: str | None = None,
                overrides: list[str] | None = None, skill_root: Path | None = None) -> dict:
    config = common.load_default_config(tool_dir)
    if override_path:
        path = Path(override_path)
        if not path.is_file():
            raise common.ToolError('MISSING_CONFIG', 'The config override file does not exist.', [override_path], ['Provide an existing relative config path.'])
        patch = common.load_json_file(path, int(config['limits']['max_config_bytes']), 'config override', config)
        if not isinstance(patch, dict):
            raise common.ToolError('INVALID_CONFIG', 'The config override must be a JSON object.', [type(patch).__name__])
        config = common.deep_merge(config, patch)
    if overrides:
        config = common.apply_overrides(config, overrides)
    validate_config(config)
    return config


def _expect(condition: bool, code: str, message: str, details=None, recovery=None):
    if not condition:
        raise common.ToolError(code, message, details or [], recovery or [])


def _validate_reference_values(value, location: str):
    if isinstance(value, dict):
        for key, child in value.items():
            _validate_reference_values(child, f'{location}.{key}')
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_reference_values(child, f'{location}[{index}]')
    elif isinstance(value, str):
        if re.search(r'https?://', value):
            raise common.ToolError('UNSUPPORTED_INTEGRATION', 'External URLs are not supported in configuration.', [location], ['Remove the URL and use a local relative path.'])
        if '\\' in value or value.startswith('/') or value.startswith('~') or re.match(r'^[a-zA-Z]:', value) or '..' in Path(value).parts:
            if location.startswith(('paths.', 'prompts.', 'policies.', 'storage.output_root')):
                raise common.ToolError('INVALID_REFERENCE', 'Internal resource references must be relative POSIX paths.', [location, value], ['Use a path such as ultimate-game-maker/asset-catalog.json.'])


def validate_config(config: dict) -> None:
    _expect(isinstance(config, dict) and config.get('schema_id') == 'ugm-config-1.0', 'INVALID_CONFIG', 'The config schema is not ugm-config-1.0.')
    required_sections = ['runtime', 'limits', 'retry', 'cleanup', 'recovery', 'approval', 'providers', 'models', 'prompts', 'policies', 'paths', 'storage', 'version_intelligence', 'prototype', 'selftest', 'security']
    missing = [section for section in required_sections if section not in config]
    _expect(not missing, 'INVALID_CONFIG', 'The config is missing required sections.', missing)
    runtime = config['runtime']
    _expect(0 < float(runtime.get('deadline_seconds', 0)) <= 300, 'INVALID_CONFIG', 'runtime.deadline_seconds must be between 0 and 300.', [runtime.get('deadline_seconds')])
    _expect(runtime.get('default_format') in ('json', 'text', 'markdown', 'csv'), 'INVALID_CONFIG', 'runtime.default_format must be json or text.')
    limits = config['limits']
    for key, value in limits.items():
        _expect(isinstance(value, int) and value > 0, 'INVALID_CONFIG', f'limits.{key} must be a positive integer.', [key, value])
    _expect(limits['max_categories_per_request'] <= 33, 'INVALID_CONFIG', 'The category limit cannot exceed the 33 unified handlers.', [limits['max_categories_per_request']])
    _expect(limits['max_skills_per_request'] <= 330, 'INVALID_CONFIG', 'The specialist limit cannot exceed the 330 unified specialists.', [limits['max_skills_per_request']])
    retry = config['retry']
    _expect(1 <= int(retry.get('attempts', 0)) <= 5, 'INVALID_CONFIG', 'retry.attempts must be between 1 and 5.', [retry.get('attempts')])
    _expect(0 <= float(retry.get('backoff_seconds', -1)) <= 1, 'INVALID_CONFIG', 'retry.backoff_seconds must be between 0 and 1.', [retry.get('backoff_seconds')])
    approval = config['approval']
    _expect(isinstance(approval.get('confirmation_token'), str) and approval['confirmation_token'], 'INVALID_CONFIG', 'A non-empty approval.confirmation_token is required.')
    required_gates = {'overwrite', 'delete', 'publish', 'deploy', 'financial', 'credentials', 'security-sensitive', 'network', 'external-execution', 'privileged', 'destructive', 'irreversible'}
    _expect(required_gates.issubset(set(approval.get('required_actions', []))), 'INVALID_CONFIG', 'The approval policy is missing mandatory approval actions.', sorted(required_gates - set(approval.get('required_actions', []))))
    _expect(approval.get('external_execution') is False, 'UNSUPPORTED_INTEGRATION', 'External execution is not implemented.', [approval.get('external_execution')])
    _expect(config['providers'].get('external') == 'disabled', 'UNSUPPORTED_INTEGRATION', 'External providers are not implemented.', [config['providers'].get('external')])
    _expect(config['storage'].get('type') == 'local-filesystem', 'UNSUPPORTED_INTEGRATION', 'Only local-filesystem storage is implemented.', [config['storage'].get('type')])
    _expect(config['version_intelligence']['external_fetch'].get('enabled') is False, 'UNSUPPORTED_INTEGRATION', 'External documentation fetching is not implemented.', [config['version_intelligence']['external_fetch'].get('enabled')])
    _expect(config['policies'].get('no_external_actions') is True, 'UNSUPPORTED_INTEGRATION', 'External actions must remain disabled.')
    for key, value in config['security'].items():
        _expect(value is False, 'UNSUPPORTED_INTEGRATION', f'security.{key} is not implemented and must remain false.', [key, value])
    for section in ('paths', 'prompts', 'policies'):
        for key, value in config[section].items():
            if isinstance(value, str) and (key.endswith('_template') or section == 'paths' or key in ('approval_config', 'production_template', 'html5_template', 'evidence_policy', 'request_template')):
                common.validate_reference(value)
    _validate_reference_values({section: config[section] for section in ('paths', 'prompts', 'policies', 'storage')}, 'config')
    _expect(isinstance(config['version_intelligence'].get('allowed_extensions'), list) and config['version_intelligence']['allowed_extensions'], 'INVALID_CONFIG', 'At least one local documentation extension is required.')
    _expect(0 < int(config['selftest'].get('node_timeout_seconds', 0)) <= 180, 'INVALID_CONFIG', 'selftest.node_timeout_seconds must be between 1 and 180.')
    _expect(0 < int(config['selftest'].get('cli_timeout_seconds', 0)) <= 180, 'INVALID_CONFIG', 'selftest.cli_timeout_seconds must be between 1 and 180.')
    text = json.dumps(config, ensure_ascii=False)
    for forbidden in ('api_key', 'apikey', 'password', 'private_key', 'client_secret', 'access_token'):
        _expect(forbidden not in text.lower(), 'EMBEDDED_SECRET', 'Configuration contains a credential-like key.', [forbidden], ['Remove secrets; this tool has no credential integration.'])


def load_schema(config: dict, override: str | None = None, skill_root: Path | None = None) -> dict:
    if override:
        path = Path(override)
        if not path.is_file():
            raise common.ToolError('MISSING_RESOURCE', 'The brief schema override does not exist.', [override])
    else:
        path = common.resolve_reference(config['paths']['brief_schema'], skill_root)
    schema = common.load_json_file(path, int(config['limits']['max_schema_bytes']), 'asset-brief-schema.json', config)
    if not isinstance(schema, dict) or schema.get('schema_id') != 'ugm-brief-schema-1.0':
        raise common.ToolError('INVALID_SCHEMA', 'The brief schema is not ugm-brief-schema-1.0.')
    return schema


def load_brief(path_str: str, config: dict, schema: dict | None = None,
               skill_root: Path | None = None) -> dict:
    path = Path(path_str)
    brief = common.load_json_file(path, int(config['limits']['max_brief_bytes']), 'game brief', config)
    if schema is None:
        schema = load_schema(config, skill_root=skill_root)
    validate_brief(brief, schema, config)
    return brief


def validate_brief(brief: dict, schema: dict, config: dict) -> None:
    errors: list[str] = []

    def check(value, rules: dict, location: str):
        expected = rules.get('type')
        if expected == 'object':
            if not isinstance(value, dict):
                errors.append(f'{location}: expected object')
                return
            for key in rules.get('required', []):
                if key not in value:
                    errors.append(f'{location}.{key}: required field is missing')
            properties = rules.get('properties', {})
            if rules.get('additionalProperties') is False:
                for key in value:
                    if key not in properties:
                        errors.append(f'{location}.{key}: additional property is not allowed')
            for key, child in value.items():
                if key in properties:
                    check(child, properties[key], f'{location}.{key}')
        elif expected == 'array':
            if not isinstance(value, list):
                errors.append(f'{location}: expected array')
                return
            if 'minItems' in rules and len(value) < rules['minItems']:
                errors.append(f'{location}: needs at least {rules["minItems"]} items')
            if 'maxItems' in rules and len(value) > rules['maxItems']:
                errors.append(f'{location}: exceeds {rules["maxItems"]} items')
            if 'items' in rules:
                for index, child in enumerate(value):
                    check(child, rules['items'], f'{location}[{index}]')
        elif expected == 'string':
            if not isinstance(value, str):
                errors.append(f'{location}: expected string')
                return
            if 'minLength' in rules and len(value) < rules['minLength']:
                errors.append(f'{location}: shorter than {rules["minLength"]} characters')
            if 'maxLength' in rules and len(value) > rules['maxLength']:
                errors.append(f'{location}: longer than {rules["maxLength"]} characters')
            if 'enum' in rules and value not in rules['enum']:
                errors.append(f'{location}: allowed values are {", ".join(rules["enum"])}')
        elif expected == 'integer':
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f'{location}: expected integer')
                return
            if 'minimum' in rules and value < rules['minimum']:
                errors.append(f'{location}: below minimum {rules["minimum"]}')
            if 'maximum' in rules and value > rules['maximum']:
                errors.append(f'{location}: above maximum {rules["maximum"]}')
        elif expected is None and isinstance(rules, dict):
            return

    check(brief, schema, 'brief')
    for key in ('password', 'api_key', 'apikey', 'secret', 'private_key', 'client_secret', 'access_token', 'credential'):
        if key in {str(k).lower() for k in brief.keys()}:
            errors.append(f'brief.{key}: credential-like fields are not accepted')
    if errors:
        raise common.ToolError('INVALID_BRIEF', 'The game brief failed schema validation.', errors[:30], ['Fix the listed fields using ultimate-game-maker/asset-brief-template.json as a working example.'])
    acceptance = brief.get('acceptance_criteria', [])
    if not acceptance:
        raise common.ToolError('INVALID_BRIEF', 'At least one acceptance criterion is required.', [], ['Add acceptance_criteria to the brief.'])


def validate_approval(args, config: dict, action: str, reason: str | None = None) -> None:
    required = set(config['approval']['required_actions'])
    if action not in required:
        raise common.ToolError('UNKNOWN_APPROVAL_ACTION', 'The requested action is not governed by the approval policy.', [action], ['Use gate-list to see all governed actions.'])
    token = config['approval']['confirmation_token']
    if getattr(args, 'confirm', None) != token:
        raise common.ToolError('APPROVAL_REQUIRED', f'Explicit approval requires --confirm {token}.', [action, getattr(args, 'confirm', None)], [f'Rerun with --confirm {token} only after human review.'])
    if reason is not None and len(reason.strip()) < 5:
        raise common.ToolError('INVALID_APPROVAL_REASON', 'An approval reason of at least 5 characters is required.', [reason], ['Describe who reviewed the action and why.'])


def validate_overwrite_flag(args, config: dict) -> None:
    if getattr(args, 'approve_overwrite', False) and getattr(args, 'confirm', None) != config['approval']['confirmation_token']:
        validate_approval(args, config, 'overwrite')


def validate_doc_paths(path_strings: list[str], config: dict, deadline: common.Deadline) -> list[Path]:
    if not path_strings:
        raise common.ToolError('MISSING_DOCS', 'At least one local documentation file is required.', [], ['Pass --docs with one or more local UTF-8 text, Markdown, HTML, or JSON files.'])
    max_files = int(config['limits']['max_docs_files'])
    if len(path_strings) > max_files:
        raise common.ToolError('TOO_MANY_DOCS', 'Too many documentation files were requested.', [len(path_strings)], [f'Supply at most {max_files} files.'])
    allowed = set(config['version_intelligence']['allowed_extensions'])
    paths: list[Path] = []
    for value in path_strings:
        deadline.check('validate documentation input')
        path = Path(value)
        if not path.is_file():
            raise common.ToolError('MISSING_DOCS', 'A documentation file does not exist.', [value], ['Provide an existing local file path.'])
        if path.suffix.lower() not in allowed:
            raise common.ToolError('UNSUPPORTED_DOC_FORMAT', 'The documentation file extension is not supported.', [value, path.suffix], ['Convert PDF or binary documentation to UTF-8 text, Markdown, HTML, or JSON.'])
        if path.stat().st_size > int(config['limits']['max_doc_bytes']):
            raise common.ToolError('DOC_TOO_LARGE', 'A documentation file exceeds the configured size limit.', [value, path.stat().st_size])
        paths.append(path)
    return paths
