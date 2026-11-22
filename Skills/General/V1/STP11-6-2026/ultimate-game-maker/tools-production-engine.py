#!/usr/bin/env python3
"""Unified production-contract, routing, evidence-gate, export, and health engine.

This module adapts the second Skill Tool's real capabilities onto the single
primary entry point, tools-game-maker.py. It reads the existing unified catalog
plus the category-profile, specialization-lexicon, request-schema, gate-policy,
and request-template assets. It has no third-party dependencies and never
executes a shell, network request, engine SDK, or arbitrary code.
"""
from __future__ import annotations

import ast
import copy
import csv
import functools
import io
import json
import shutil
import sys
import time
from pathlib import Path

import ugm_common as common
import ugm_validation as validation
import ugm_production_registry as production_registry
import ugm_production_planner as production_planner
import ugm_evidence_gates as evidence_gates


def _to_tool_error(exc, default_code: str = 'PRODUCTION_ERROR') -> common.ToolError:
    if isinstance(exc, common.ToolError):
        return exc
    code = str(getattr(exc, 'code', default_code)).upper()
    message = str(getattr(exc, 'message', str(exc)))
    hint = getattr(exc, 'hint', None)
    raw_details = getattr(exc, 'details', None)
    if isinstance(raw_details, dict):
        details = [json.dumps(raw_details, ensure_ascii=False, sort_keys=True)[:1000]]
    elif isinstance(raw_details, list):
        details = [str(item)[:1000] for item in raw_details[:20]]
    elif raw_details is None:
        details = []
    else:
        details = [str(raw_details)[:1000]]
    recovery = [str(hint)] if hint else ['Inspect the structured details, correct the input, and rerun.']
    return common.ToolError(code, message, details, recovery)


def protected(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except common.ToolError:
            raise
        except Exception as exc:
            raise _to_tool_error(exc)
    return wrapper


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except common.ToolError:
        raise
    except Exception as exc:
        raise _to_tool_error(exc)


def _md_cell(value) -> str:
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')


def _warning_strings(warnings: list[dict]) -> list[str]:
    return ['%s: %s' % (item.get('path', '$'), item.get('message', '')) for item in warnings]


def _error_strings(errors: list[dict]) -> list[str]:
    return ['%s [%s] %s' % (item.get('path', '$'), item.get('keyword', ''), item.get('message', '')) for item in errors]


class ProductionEngine:
    """One bounded engine shared by all enhanced primary-CLI commands."""

    def __init__(self, config: dict, deadline: common.Deadline, skill_root: Path, tool_dir: Path):
        self.config = config
        self.deadline = deadline
        self.skill_root = skill_root
        self.tool_dir = tool_dir
        self.deadline.check('production engine startup')
        registry = production_registry.Registry(
            tool_dir, deadline=time.monotonic() + self.deadline.remaining())
        self.registry = _call(registry.load)
        self.schema = self._load_reference(config['paths']['request_schema'], 'max_request_schema_bytes', 'asset-request-schema.json')
        self.policy = self._load_reference(config['paths']['gate_policy'], 'max_gate_policy_bytes', 'asset-gate-policy.json')
        self.request_template = self._load_reference(config['paths']['request_template'], 'max_request_bytes', 'asset-production-request-template.json')
        self.unsupported_schema_keywords = _call(production_planner.unsupported_keywords, self.schema)
        if self.unsupported_schema_keywords:
            raise common.ToolError('INVALID_REQUEST_SCHEMA', 'The request schema uses unsupported keywords.', self.unsupported_schema_keywords, ['Use only the documented JSON Schema subset.'])
        self.policy_problems = _call(evidence_gates.validate_policy, self.policy)
        if self.policy_problems:
            raise common.ToolError('INVALID_GATE_POLICY', 'The evidence policy is invalid.', self.policy_problems, ['Restore asset-gate-policy.json or fix the listed keys.'])
        self.stats = _call(self.registry.stats)
        if self.stats.get('categories') != 33 or self.stats.get('capabilities') != 330:
            raise common.ToolError('INVALID_PRODUCTION_CATALOG', 'The production registry does not contain 33 categories and 330 capabilities.', [json.dumps(self.stats, sort_keys=True)], ['Restore asset-catalog.json, asset-category-profiles.json, and asset-skill-lexicon.json.'])

    def _load_reference(self, ref: str, limit_key: str, label: str) -> dict:
        path = common.resolve_reference(ref, self.skill_root)
        return common.load_json_file(path, int(self.config['limits'][limit_key]), label, self.config)

    def _load_user_json(self, path_str: str, limit_key: str, label: str, allow_stdin: bool = False) -> dict:
        if allow_stdin and path_str == '-':
            raw = sys.stdin.read()
            if len(raw.encode('utf-8')) > int(self.config['limits'][limit_key]):
                raise common.ToolError('INPUT_TOO_LARGE', 'stdin JSON exceeds the configured byte limit.', [len(raw.encode('utf-8'))])
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise common.ToolError('INVALID_JSON', 'stdin is not valid JSON.', ['line %d column %d: %s' % (exc.lineno, exc.colno, exc.msg)])
            if not isinstance(value, dict):
                raise common.ToolError('INVALID_JSON', 'The JSON root must be an object.', [type(value).__name__])
            return value
        path = Path(path_str)
        if not path.is_file():
            raise common.ToolError('MISSING_INPUT', 'The requested JSON input file does not exist.', [path_str], ['Provide an existing file, or use - for stdin where documented.'])
        value = common.load_json_file(path, int(self.config['limits'][limit_key]), label, self.config)
        if not isinstance(value, dict):
            raise common.ToolError('INVALID_JSON', 'The JSON root must be an object.', [path_str, type(value).__name__])
        return value

    def _request_raw(self, args) -> dict:
        path_str = getattr(args, 'request', None)
        if path_str:
            return self._load_user_json(path_str, 'max_request_bytes', 'production-request.json', allow_stdin=True)
        return copy.deepcopy(self.request_template)

    def _apply_overrides(self, raw: dict, args) -> None:
        project = raw.setdefault('project', {})
        capabilities = raw.setdefault('capabilities', {})
        output = raw.setdefault('output', {})
        overrides = raw.setdefault('overrides', {})
        scalar_project = [
            ('project_name', 'name'), ('genre', 'genre'), ('goal', 'goal'), ('engine', 'engine'),
            ('engine_version', 'engine_version'), ('language', 'language'), ('dimension', 'dimension'),
            ('scope', 'scope'), ('team_size', 'team_size'), ('weeks', 'weeks'), ('target_fps', 'target_fps'),
        ]
        for attr, key in scalar_project:
            value = getattr(args, attr, None)
            if value is not None:
                project[key] = value
        for attr, key in (('platform', 'platforms'), ('constraint', 'constraints'), ('acceptance', 'acceptance_criteria')):
            values = list(getattr(args, attr, []) or [])
            if values:
                project[key] = values
        for attr, key in (('skills', 'skills'), ('categories', 'categories'), ('phases', 'phases'), ('tags', 'tags'), ('exclude', 'exclude')):
            values = common.parse_csv(getattr(args, attr, None))
            if values:
                capabilities[key] = values
        if getattr(args, 'limit', None) is not None:
            capabilities['limit'] = int(args.limit)
        if getattr(args, 'out_dir', None):
            output['dir'] = args.out_dir
        if getattr(args, 'performance_budget_ms', None) is not None:
            overrides['performance_budget_ms'] = float(args.performance_budget_ms)
        if getattr(args, 'notes', None):
            overrides['notes'] = args.notes

    def _validate_raw(self, raw: dict):
        return _call(production_planner.validate_request, raw, self.schema)

    def _build_request(self, args):
        self.deadline.check('production request validation')
        raw = self._request_raw(args)
        self._apply_overrides(raw, args)
        ok, errors, warnings = self._validate_raw(raw)
        if not ok:
            raise common.ToolError(
                'INVALID_REQUEST',
                'The production request failed schema validation with %d error(s).' % len(errors),
                _error_strings(errors),
                ['Fix the listed JSON paths and rerun, or use validate-request to inspect the full report.'],
            )
        request = _call(production_planner.normalize_request, raw)
        return raw, request, warnings

    def _validate_plan(self, plan: dict) -> dict:
        if not isinstance(plan, dict):
            raise common.ToolError('INVALID_PLAN', 'The production plan must be a JSON object.', [type(plan).__name__])
        missing = [key for key in ('plan_id', 'request', 'routing', 'summary', 'work_items', 'milestones', 'approvals') if key not in plan]
        if missing or not isinstance(plan.get('work_items'), list) or not plan.get('work_items'):
            raise common.ToolError('INVALID_PLAN', 'The production plan is missing required fields or work items.', missing, ['Generate the plan with production-plan and pass that file to export or evidence-gate.'])
        return plan

    def _load_plan(self, args) -> dict:
        payload = self._load_user_json(args.plan, 'max_plan_bytes', 'production-plan.json')
        return self._validate_plan(payload.get('plan', payload))

    @protected
    def capabilities(self, args, output_format: str) -> dict:
        self.deadline.check('production capability selection')
        skill_queries = list(getattr(args, 'skill', []) or [])
        category_queries = list(getattr(args, 'category', []) or [])
        phase_queries = list(getattr(args, 'phase', []) or [])
        tag_queries = list(getattr(args, 'tag', []) or [])
        exclude_queries = list(getattr(args, 'exclude', []) or [])
        search = getattr(args, 'search', None)
        limit = getattr(args, 'limit', None)
        has_filters = bool(skill_queries or category_queries or phase_queries or tag_queries or exclude_queries or search or limit)
        chosen = {}
        all_skills = self.registry.skills()
        for query in skill_queries:
            skill = _call(self.registry.resolve_skill, query)
            chosen[skill['id']] = skill
        for query in category_queries:
            category = _call(self.registry.resolve_category, query)
            for skill in all_skills:
                if skill['category_slug'] == category['slug']:
                    chosen[skill['id']] = skill
        normalized_phases = {_call(self.registry.normalize_phase, value) for value in phase_queries}
        if normalized_phases:
            for skill in all_skills:
                if self.registry.profile(skill)['phase'] in normalized_phases:
                    chosen[skill['id']] = skill
        lowered_tags = [value.lower() for value in tag_queries]
        if lowered_tags:
            for skill in all_skills:
                tags = [tag.lower() for tag in self.registry.contract(skill)['tags']]
                if any(tag in tags for tag in lowered_tags):
                    chosen[skill['id']] = skill
        if any((skill_queries, category_queries, phase_queries, tag_queries)):
            selected = sorted(chosen.values(), key=lambda item: (item['category_number'], item['order']))
        else:
            selected = list(all_skills)
        if search:
            hits = {skill['id'] for skill in _call(self.registry.search, search)}
            selected = [skill for skill in selected if skill['id'] in hits]
        for query in exclude_queries:
            try:
                drop = _call(self.registry.resolve_skill, query)
                selected = [skill for skill in selected if skill['id'] != drop['id']]
            except common.ToolError:
                continue
        if limit is not None:
            if int(limit) < 1:
                raise common.ToolError('INVALID_LIMIT', '--limit must be at least 1.', [limit])
            selected = selected[:int(limit)]
        rows = []
        for skill in selected:
            contract = self.registry.contract(skill)
            rows.append({
                'id': contract['id'],
                'name': contract['name'],
                'category': contract['category'],
                'category_number': contract['category_number'],
                'phase': contract['phase'],
                'specializations': contract['specializations'],
                'tags': contract['tags'],
                'checks': len(contract['checks']),
                'effort_points': contract['effort_points'],
            })
        headers = ['id', 'name', 'category', 'phase', 'checks', 'effort_points', 'tags']
        markdown_lines = ['# Ultimate Game Maker capability contracts', '', '| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
        for row in rows:
            markdown_lines.append('| ' + ' | '.join(_md_cell(row.get(key, '')) for key in headers) + ' |')
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator='\n')
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row.get(key, '') for key in headers])
        text_lines = ['Selected capabilities: %d of %d' % (len(rows), len(all_skills)), 'Format: %s' % output_format]
        for row in rows:
            text_lines.append('%s | %s | %s | %s | %d checks' % (row['id'].split('/')[-1], row['name'], row['category'], row['phase'], row['checks']))
        return {
            'selected_count': len(rows),
            'filters': {
                'skills': skill_queries, 'categories': category_queries, 'phases': phase_queries,
                'tags': tag_queries, 'exclude': exclude_queries, 'search': search, 'limit': limit,
            },
            'has_filters': has_filters,
            'capabilities': rows,
            'stats': self.stats,
            'markdown': '\n'.join(markdown_lines) + '\n',
            'csv': buffer.getvalue(),
            'text': '\n'.join(text_lines),
        }

    @protected
    def describe(self, args, output_format: str) -> dict:
        self.deadline.check('production contract describe')
        skill = _call(self.registry.resolve_skill, args.skill)
        contract = _call(self.registry.contract, skill)
        lines = [
            '# %s' % contract['name'],
            '',
            '- ID: `%s`' % contract['id'],
            '- Category: %s (%s)' % (contract['category'], contract['category_number']),
            '- Phase: %s' % contract['phase'],
            '- Engine family: %s' % ', '.join(contract['engine_family']),
            '- Languages: %s' % ', '.join(contract['languages']),
            '- Specializations: %s' % ', '.join(contract['specializations']) if contract['specializations'] else '- Specializations: none',
            '- Tags: %s' % ', '.join(contract['tags']),
            '- Effort points: %s' % contract['effort_points'],
            '',
            '## Description',
            '',
            contract['description'],
            '',
            '## Artifacts',
            '',
        ]
        for artifact in contract['artifacts']:
            lines.append('- **%s** (%s): %s' % (artifact['title'], artifact['format'], artifact['purpose']))
        lines.extend(['', '## Acceptance checks', ''])
        for check in contract['checks']:
            lines.append('- `%s` [%s/%s]: %s Evidence: %s' % (check['id'], check['class'], 'required' if check['required'] else 'optional', check['statement'], check['evidence']))
        lines.extend(['', '## Risks', ''])
        for risk in contract['risks']:
            lines.append('- %s (%s): %s' % (risk['risk'], risk['severity'], risk['mitigation']))
        lines.extend(['', '## Verification and approvals', ''])
        for item in contract['verification']:
            lines.append('- %s: %s' % (item.get('intent', 'check'), item.get('method', '')))
        lines.append('- Approval actions: %s' % (', '.join(contract['approval_actions']) if contract['approval_actions'] else 'none'))
        lines.append('- Depends on: %s' % (', '.join(contract['depends_on']) if contract['depends_on'] else 'none'))
        text = '\n'.join([
            '%s (%s)' % (contract['name'], contract['id']),
            'Category: %s | Phase: %s' % (contract['category'], contract['phase']),
            'Description: %s' % contract['description'],
            'Artifacts: %d | checks: %d | risks: %d | effort: %s' % (len(contract['artifacts']), len(contract['checks']), len(contract['risks']), contract['effort_points']),
        ])
        return {'contract': contract, 'markdown': '\n'.join(lines) + '\n', 'text': text}

    @protected
    def validate_request(self, args, output_format: str) -> dict:
        self.deadline.check('production request validation report')
        raw = self._request_raw(args)
        self._apply_overrides(raw, args)
        ok, errors, warnings = self._validate_raw(raw)
        unsupported = _call(production_planner.unsupported_keywords, self.schema)
        normalized = _call(production_planner.normalize_request, raw) if ok else None
        text_lines = [
            'validation: %s' % ('PASS' if ok else 'FAIL'),
            'errors: %d | warnings: %d | schema keywords unchecked: %d' % (len(errors), len(warnings), len(unsupported)),
        ]
        text_lines.extend('  - %s' % item for item in _error_strings(errors))
        text_lines.extend('  ! %s' % item for item in _warning_strings(warnings))
        markdown = '\n'.join(['# Production request validation', '', '- Result: %s' % ('PASS' if ok else 'FAIL'), '- Errors: %d' % len(errors), '- Warnings: %d' % len(warnings), '', '## Errors', ''] + ['- %s' % item for item in _error_strings(errors)] + ['', '## Warnings', ''] + ['- %s' % item for item in _warning_strings(warnings)]) + '\n'
        return {
            'valid': ok,
            'request_source': getattr(args, 'request', None) or 'ultimate-game-maker/asset-production-request-template.json',
            'errors': errors,
            'warnings': warnings,
            'schema_keywords_unchecked': unsupported,
            'normalized_request': normalized,
            'assumed_defaults': normalized.get('assumed_defaults', []) if normalized else [],
            'text': '\n'.join(text_lines),
            'markdown': markdown,
        }

    @protected
    def route(self, args, output_format: str) -> dict:
        raw, request, warnings = self._build_request(args)
        routing = _call(production_planner.route, request)
        selected = routing.get('selected', {})
        text = '\n'.join([
            'Selected engine: %s' % selected.get('label', selected.get('engine', 'unknown')),
            'Decision: %s' % routing.get('decision', ''),
            'Project: %s | dimension: %s | scope: %s | team: %s' % (
                request['project'].get('name', ''), request['project'].get('dimension', ''),
                request['project'].get('scope', ''), request['project'].get('team_size', '')),
            'Capabilities: %s' % json.dumps(request['capabilities'], ensure_ascii=False, sort_keys=True),
        ])
        markdown = '\n'.join(['# Engine routing', '', '- Selected: %s' % selected.get('label', selected.get('engine', 'unknown')), '- Decision: %s' % routing.get('decision', ''), '', '## Routing detail', '', '```json', json.dumps(routing, indent=2, ensure_ascii=False), '```']) + '\n'
        return {'valid': True, 'request': request, 'routing': routing, 'assumed_defaults': request.get('assumed_defaults', []), 'validation_warnings': warnings, 'text': text, 'markdown': markdown}

    @protected
    def production_plan(self, args, output_format: str) -> dict:
        raw, request, warnings = self._build_request(args)
        source = {'kind': 'request-file' if getattr(args, 'request', None) else 'default-template-or-selector-flags', 'path': getattr(args, 'request', None)}
        plan = _call(production_planner.build_plan, self.registry, request, source)
        markdown = _call(production_planner.render_plan_md, plan)
        summary = plan['summary']
        text_lines = [
            'Plan: %s' % plan['plan_id'],
            'Engine: %s (%s)' % (plan['routing'].get('selected', {}).get('label', ''), plan['routing'].get('decision', '')),
            'Capabilities: %d across %d categories' % (summary['capabilities'], summary['categories']),
            'Checks: %d required of %d | risks: %d | effort: %s' % (summary['required_checks'], summary['total_checks'], summary['risks'], summary['effort_points']),
            'Feasibility: %s' % plan['feasibility'].get('verdict', ''),
            'Approvals: %s' % (', '.join(summary['approval_actions']) or 'none'),
        ]
        for item in plan['work_items'][:12]:
            text_lines.append('  %s %s [%s] %d checks' % (item['id'], item['capability'], item['phase'], len(item['checks'])))
        if len(plan['work_items']) > 12:
            text_lines.append('  ... %d more work items' % (len(plan['work_items']) - 12))
        return {'plan': plan, 'plan_id': plan['plan_id'], 'summary': summary, 'feasibility': plan['feasibility'], 'validation_warnings': warnings, 'text': '\n'.join(text_lines), 'markdown': markdown}

    @protected
    def export(self, args, output_format: str) -> dict:
        self.deadline.check('production bundle export')
        if getattr(args, 'plan', None):
            plan = self._load_plan(args)
        else:
            plan = self.production_plan(args, 'json')['plan']
        include_specs = not bool(getattr(args, 'no_specs', False))
        files = _call(evidence_gates.plan_export_files, plan, production_planner, self.policy, include_specs, args.profile)
        max_files = int(self.config['limits']['max_export_files'])
        if len(files) > max_files:
            raise common.ToolError('EXPORT_TOO_LARGE', 'The bundle exceeds the configured file limit.', [len(files), max_files], ['Reduce capabilities.limit, use --no-specs, or raise limits.max_export_files deliberately.'])
        report = _call(
            evidence_gates.export_bundle,
            plan,
            production_planner,
            self.policy,
            args.out,
            approve=bool(args.approve),
            dry_run=bool(args.dry_run),
            include_specs=include_specs,
            overwrite=bool(args.approve),
            deadline=time.monotonic() + self.deadline.remaining(),
            profile=args.profile,
        )
        text_lines = [
            'Export: %s' % ('dry run' if report.get('dry_run') else 'written'),
            'Plan: %s | profile: %s' % (report.get('plan_id', ''), args.profile),
            'Files: %s | bytes: %s' % (report.get('file_count', len(report.get('files', []))), report.get('total_bytes', 0)),
            'Output: %s' % report.get('output_dir', ''),
        ]
        markdown = '\n'.join(['# Editable production bundle export', '', '- Dry run: %s' % bool(report.get('dry_run')), '- Plan: `%s`' % report.get('plan_id', ''), '- Profile: %s' % args.profile, '- Output: `%s`' % report.get('output_dir', ''), '', '## Files', ''] + ['- `%s`' % (item['path'] if isinstance(item, dict) else item) for item in report.get('files', [])[:40]]) + '\n'
        return {'plan_id': plan['plan_id'], 'profile': args.profile, 'include_specs': include_specs, 'file_count': report.get('file_count', len(report.get('files', []))), 'report': report, 'text': '\n'.join(text_lines), 'markdown': markdown}

    @protected
    def evidence_gate(self, args, output_format: str) -> dict:
        self.deadline.check('evidence gate')
        plan = self._load_plan(args)
        if args.template:
            template = _call(evidence_gates.evidence_template, plan, self.policy, args.profile)
            text = 'Evidence template: plan %s | profile %s | checks %d | approvals %d' % (plan['plan_id'], args.profile, len(template['checks']), len(template['approvals_required']))
            return {'mode': 'template', 'plan_id': plan['plan_id'], 'profile': args.profile, 'template': template, 'text': text, 'markdown': '# Evidence template\n\n' + text + '\n'}
        evidence = self._load_user_json(args.evidence, 'max_evidence_bytes', 'evidence.json')
        report = _call(evidence_gates.evaluate, plan, evidence, self.policy, args.profile)
        rendered = _call(evidence_gates.render_report_text, report)
        return {'mode': 'evaluate', 'plan_id': plan['plan_id'], 'profile': args.profile, 'report': report, 'text': rendered, 'markdown': '# Evidence gate report\n\n```text\n' + rendered + '\n```\n'}

    @protected
    def example_request(self, args, output_format: str) -> dict:
        template = copy.deepcopy(self.request_template)
        text = 'Production request template: %s | categories %d | limit %s' % (
            template['project']['name'], len(template['capabilities']['categories']), template['capabilities'].get('limit'))
        return {'template': template, 'schema_id': self.schema.get('schema_id'), 'text': text, 'markdown': '# Production request template\n\n```json\n' + json.dumps(template, indent=2, ensure_ascii=False) + '\n```\n'}

    def _python39_syntax(self) -> dict:
        files = []
        for path in sorted(self.tool_dir.glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=path.name, feature_version=(3, 9))
            future = any(isinstance(node, ast.ImportFrom) and node.module == '__future__' and any(alias.name == 'annotations' for alias in node.names) for node in tree.body)
            files.append({'file': path.name, 'future_annotations': future})
        if not all(item['future_annotations'] for item in files):
            raise common.ToolError('PYTHON_39_SYNTAX', 'One or more Python tools lack future annotations.', [item['file'] for item in files if not item['future_annotations']])
        return {'compatible': True, 'feature_version': '3.9', 'file_count': len(files), 'all_future_annotations': True}

    def _config_resource_refs(self) -> int:
        count = 0
        for section in ('paths', 'prompts', 'policies'):
            for key, value in self.config[section].items():
                if isinstance(value, str) and value.startswith('ultimate-game-maker/'):
                    common.resolve_reference(value, self.skill_root)
                    count += 1
        return count

    @protected
    def doctor(self, output_format: str) -> dict:
        import ugm_selftest as selftest
        self.deadline.check('doctor')
        validation.validate_config(self.config)
        package = _call(selftest._check_tool_folder, self.tool_dir)
        references = _call(selftest._check_references, self.tool_dir, self.skill_root)
        config_refs = self._config_resource_refs()
        syntax = self._python39_syntax()
        node = shutil.which('node')
        result = {
            'ok': True,
            'tool_version': common.VERSION,
            'minimum_python': self.config['runtime']['minimum_python'],
            'runtime_python': sys.version.split()[0],
            'python_39_syntax': syntax,
            'python_39_interpreter_note': 'No separate Python 3.9 interpreter was executed; syntax was checked with ast.parse(feature_version=(3, 9)).',
            'package': package,
            'references_resolved': references,
            'config_resources_resolved': config_refs,
            'production': self.stats,
            'phases': list(self.registry.profiles_doc.get('phases', {})),
            'engine_routes': len(production_planner.ROUTES),
            'gate_profiles': list(evidence_gates.GATE_PROFILES),
            'default_gate_profile': self.policy.get('default_profile'),
            'request_schema': {'schema_id': self.schema.get('schema_id'), 'unsupported_keywords': self.unsupported_schema_keywords},
            'gate_policy': {'valid': not self.policy_problems, 'problems': self.policy_problems},
            'optional_node_detected': bool(node),
            'external_integrations': {'network': False, 'shell': False, 'arbitrary_code': False, 'engine_sdk': False, 'credentials': False},
        }
        text = '\n'.join([
            'Doctor: OK',
            'Version: %s | Python: %s | minimum: %s' % (common.VERSION, result['runtime_python'], result['minimum_python']),
            'Package: %d files (%d tools, %d assets, %d knowledge)' % (package['file_count'], package['tools_files'], package['asset_files'], package['knowledge_files']),
            'References: %d | config resources: %d' % (references, config_refs),
            'Production: %d categories, %d capabilities, %d distinct domain signatures, %d checks' % (self.stats['categories'], self.stats['capabilities'], self.stats['distinct_domain_signatures'], self.stats['total_checks']),
            'Routes: %d | gate profiles: %s | default: %s' % (len(production_planner.ROUTES), ', '.join(evidence_gates.GATE_PROFILES), self.policy.get('default_profile')),
            'Python 3.9 syntax: %s across %d files; no 3.9 interpreter run' % (syntax['compatible'], syntax['file_count']),
            'Optional Node detected: %s' % bool(node),
        ])
        result['text'] = text
        result['markdown'] = '# Doctor report\n\n```text\n' + text + '\n```\n'
        return result


def build_engine(config: dict, deadline: common.Deadline, skill_root: Path, tool_dir: Path) -> ProductionEngine:
    return ProductionEngine(config, deadline, skill_root, tool_dir)
