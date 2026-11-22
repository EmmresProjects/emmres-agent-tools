#!/usr/bin/env python3
"""Builds the consolidated editable production handoff and local recovery state."""
from __future__ import annotations

import copy
import json
from pathlib import Path, PurePosixPath

import ugm_capability_packets as capability_packets
import ugm_common as common
import ugm_docs as docs
import ugm_prototype as prototype
import ugm_registry as registry

TEMPLATE_PLACEHOLDERS = (
    '{{TITLE}}', '{{UMBRELLA}}', '{{GOAL}}', '{{GENRE}}', '{{ENGINE}}', '{{LANGUAGE}}',
    '{{PLATFORM}}', '{{SEED}}', '{{SELECTION_MODE}}', '{{HANDLER_COUNT}}', '{{SKILL_COUNT}}',
    '{{VERSION_STATUS}}', '{{MISSING_EVIDENCE}}', '{{PROVIDER}}', '{{MODEL_PROFILE}}',
    '{{PROMPT_SET}}', '{{ACCEPTANCE_CRITERIA}}', '{{CONSTRAINTS}}', '{{OUTPUT_FILES}}',
    '{{HANDLER_TABLE}}', '{{CAPABILITY_SECTIONS}}', '{{COMPLETE_CAPABILITY_LIST}}',
    '{{RECOVERY_STEPS}}', '{{APPROVAL_GATES}}', '{{LIMITATIONS}}',
)


def apply_brief_overrides(brief: dict, options: dict) -> dict:
    effective = copy.deepcopy(brief)
    mapping = {
        'engine_override': 'engine',
        'language_override': 'language',
        'platform_override': 'target_platform',
        'version_override': 'engine_version',
    }
    for option_key, brief_key in mapping.items():
        value = options.get(option_key)
        if value:
            effective[brief_key] = str(value)
    if options.get('seed_override') is not None:
        effective['seed'] = int(options['seed_override'])
    return effective


def _brief_signature(brief: dict) -> dict:
    return {
        'goal': brief.get('goal'),
        'genre': brief.get('genre'),
        'engine': brief.get('engine'),
        'engine_version': brief.get('engine_version'),
        'language': brief.get('language'),
        'target_platform': brief.get('target_platform'),
        'seed': brief.get('seed', 0),
        'acceptance_criteria_count': len(brief.get('acceptance_criteria', [])),
    }


def _md_list(items, empty_text='- None supplied.') -> str:
    values = [str(item) for item in items if str(item).strip()]
    return '\n'.join(f'- {value}' for value in values) if values else empty_text


def _md_table(rows: list[list[str]], headers: list[str]) -> str:
    lines = ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |']
    lines.extend('| ' + ' | '.join(str(cell).replace('|', '\\|') for cell in row) + ' |' for row in rows)
    return '\n'.join(lines)


def _handler_table(selection: dict) -> str:
    rows = []
    for handler in selection['handlers']:
        rows.append([
            f'`{handler["id"]}`',
            handler['category'],
            f'`{handler["operation"]}`',
            str(len(handler['skills'])),
            ', '.join(handler['controls'][:3]),
        ])
    return _md_table(rows, ['Handler', 'Category', 'Operation', 'Specialists', 'Primary controls'])


def _capability_sections(selection: dict) -> str:
    lines = []
    for handler in selection['handlers']:
        lines.extend([
            f'### `{handler["id"]}` — {handler["category"]}',
            '',
            f'Operation `{handler["operation"]}`: {handler["outcome"]}',
            '',
            f'- Editable planning artifacts: {", ".join(handler["artifacts"])}.',
            f'- Controls: {", ".join(handler["controls"])}.',
            f'- Checks: {", ".join(handler["checks"])}.',
            f'- Recovery: {handler["recovery"]}',
            f'- Interoperability: {", ".join(handler["interoperability"])}.',
            f'- Selected specialists: {", ".join(skill["name"] for skill in handler["skills"])}.',
            '',
        ])
    return '\n'.join(lines)


def _complete_capability_list(catalog: dict) -> str:
    lines = []
    for handler in catalog['handlers']:
        names = ', '.join(skill['name'] for skill in handler['skills'])
        lines.append(f'- **{handler["category"]}** (`{handler["id"]}`): {names}.')
    return '\n'.join(lines)


def _output_list(expected_files: list[str]) -> str:
    return '\n'.join(f'- `{name}`' for name in expected_files)


def _recovery_steps(catalog: dict, route: dict) -> str:
    steps = [
        'Validate the brief and config before regenerating any output.',
        'Capture the exact mismatch and identify the earliest failing layer.',
        'Preserve passing generated files; do not overwrite them without explicit approval.',
        'Regenerate the smallest missing layer with the `recover` command.',
        'Rerun `selftest`, then rerun the linked capability and prototype checks.',
        f'For the routed `{route["profile"]["id"]}` profile, use exact version evidence before any external build.',
    ]
    steps.extend(f'Handler recovery rule for `{handler["id"]}`: {handler["recovery"]}' for handler in catalog['handlers'][:5])
    return '\n'.join(f'{index}. {step}' for index, step in enumerate(steps, start=1))


def _limitations(config: dict, route: dict) -> str:
    profile = route['profile']
    return '\n'.join([
        f'- Routed source generation: `{profile["source_generation"]}`.',
        '- No engine editor, compiler, SDK, console certification service, cloud account, payment provider, or store is bundled.',
        '- No network documentation retrieval is implemented; local official documentation files must be supplied.',
        '- Publishing, deployment, credential, financial, and security-sensitive actions stay behind human approval and are never executed.',
        '- The generated HTML5 prototype is self-contained; a browser is required for interactive play.',
        f'- Cooperative deadline: {config["runtime"]["deadline_seconds"]} seconds; retry attempts: {config["retry"]["attempts"]}.',
        f'- Maximum generated file size: {config["limits"]["max_generated_file_bytes"]} bytes; maximum output files: {config["limits"]["max_output_files"]}.',
    ])


def _load_template(config: dict, override: str | None, skill_root: Path | None) -> str:
    if override:
        path = Path(override)
        if not path.is_file():
            raise common.ToolError('MISSING_RESOURCE', 'The production template override does not exist.', [override])
    else:
        path = common.resolve_reference(config['prompts']['production_template'], skill_root)
    template = common.read_text_limited(path, int(config['limits']['max_template_bytes']), 'asset-production-template.md', config)
    missing = [placeholder for placeholder in TEMPLATE_PLACEHOLDERS if placeholder not in template]
    if missing:
        raise common.ToolError('INVALID_TEMPLATE', 'The production template is missing required placeholders.', missing, ['Use ultimate-game-maker/asset-production-template.md or a template with the same placeholders.'])
    return template


def _render_template(template: str, values: dict) -> str:
    output = template
    for placeholder, value in values.items():
        output = output.replace(placeholder, str(value))
    unresolved = [placeholder for placeholder in TEMPLATE_PLACEHOLDERS if placeholder in output]
    if unresolved:
        raise common.ToolError('UNRESOLVED_TEMPLATE', 'Production-plan placeholders remain unresolved.', unresolved)
    if '{{' in output or '}}' in output:
        raise common.ToolError('UNRESOLVED_TEMPLATE', 'User-supplied content contains template braces.', [], ['Remove literal {{ and }} characters from brief fields, or use a different prompt template.'])
    return output


def _render_game_design(brief: dict, route: dict, selection: dict) -> str:
    assets = brief.get('assets', [])
    asset_lines = []
    for asset in assets:
        if isinstance(asset, dict):
            asset_lines.append(f'- **{asset.get("name", "unnamed")}**: {asset.get("kind", "unspecified")}; source `{asset.get("source", "unspecified")}`; license `{asset.get("license", "unspecified")}`.')
    controls = brief.get('controls', {})
    control_lines = [f'- **{key}**: {value}' for key, value in controls.items()] if isinstance(controls, dict) else []
    return '\n'.join([
        '# Game Design Document',
        '',
        f'## Vision: {brief.get("goal")}',
        '',
        f'Genre: `{brief.get("genre")}`. Quality target: `{brief.get("quality", "prototype")}`. Seed: `{brief.get("seed", 0)}`.',
        '',
        '## Core loop',
        '',
        'Read the goal and controls, move through the playable space, collect or complete the objective, receive immediate feedback, handle failure or recovery, and reach a measurable win state. The HTML5 profile implements this loop as an arcade delivery prototype; other profiles receive the same loop as an editable specification plus external build checks.',
        '',
        '## Mechanics and controls',
        '',
        _md_list(control_lines, '- No structured controls supplied; use the prototype defaults where available.'),
        '',
        '## Content and assets',
        '',
        _md_list(asset_lines, '- No external assets supplied; the HTML5 prototype uses procedural canvas shapes.'),
        '',
        '## Progression, difficulty, and save behavior',
        '',
        f'- Difficulty inputs: {", ".join(str(item) for item in brief.get("constraints", [])) or "portable defaults"}.',
        '- The prototype uses three lives, a bounded timer, deterministic parcels, and drifting hazards.',
        '- Local browser storage is limited to a best-score number and can be cleared from the UI.',
        '- No account, cloud save, or personal data integration is included.',
        '',
        '## Accessibility and safety',
        '',
        _md_list(brief.get('accessibility', []), '- Keyboard controls, high-contrast HUD, visible focus, reduced-motion respect, and mute control.'),
        '',
        '## Acceptance',
        '',
        _md_list(brief.get('acceptance_criteria', [])),
        '',
        f'Unified handlers in this plan: **{selection["handler_count"]}**. Named specialists: **{selection["skill_count"]}**.',
        f'Engine profile: `{route["profile"]["id"]}` with `{route["profile"]["source_generation"]}`.',
        '',
    ])


def _render_architecture(brief: dict, route: dict, config: dict, selection: dict) -> str:
    profile = route['profile']
    return '\n'.join([
        '# Architecture',
        '',
        '## Local tool layers',
        '',
        '1. `tools-game-maker.py` accepts commands and structured arguments.',
        '2. `tools-source-parser.py` normalizes text, Markdown, and copied HTML capability sources.',
        '3. `tools-registry.py` loads the 33-handler catalog, selects capabilities, and routes engine profiles.',
        '4. `tools-validation.py` validates briefs, config, approvals, and bounded local documentation inputs.',
        '5. `tools-capability-packets.py` renders one distinct packet per named specialist.',
        '6. `tools-docs.py` extracts version evidence from supplied local documents.',
        '7. `tools-prototype.py` renders and audits the self-contained HTML5 prototype.',
        '8. `tools-planner.py` composes the editable handoff and recovery state.',
        '9. `tools-selftest.py` validates the real CLI and generated outputs.',
        '',
        '## Routed engine boundary',
        '',
        f'- Requested engine: `{route["requested_engine"]}`.',
        f'- Profile: `{profile["id"]}`; source generation: `{profile["source_generation"]}`.',
        f'- Language: `{brief.get("language")}`; platform: `{brief.get("target_platform")}`.',
        f'- External runtime required: `{profile.get("external_runtime_required")}`.',
        f'- Runtime check: {profile.get("runtime_check")}.',
        f'- Official documentation required: `{profile.get("official_docs_required")}`.',
        '',
        '## Data flow',
        '',
        'brief -> schema validation -> engine route -> capability selection -> packets -> consolidated Markdown/JSON -> optional HTML5 prototype -> recovery state -> structured CLI result.',
        '',
        '## Storage and policy',
        '',
        f'- Storage: `{config["storage"]["type"]}` with configured root `{config["storage"]["output_root"]}`.',
        f'- Blueprint provider: `{config["providers"]["blueprint"]}`; model profile: `{config["models"]["blueprint"]}`; prompt set: `{config["prompts"]["production_template"]}`.',
        f'- Approval policy: `{config["policies"]["approval_config"]}`.',
        '- No secrets, credentials, arbitrary code, shell commands, network requests, or external tool execution are used.',
        '',
        f'This plan includes {selection["handler_count"]} category handlers and {selection["skill_count"]} named specialists without splitting them into separate Skill Tool folders.',
        '',
    ])


def _render_dependency_lock(brief: dict, route: dict, config: dict) -> str:
    payload = {
        'schema_id': 'ugm-dependency-lock-1.0',
        'deterministic_local_generation': config['runtime']['deterministic_output'],
        'local_runtime': {
            'python': config['runtime']['minimum_python'],
            'standard_library_only': True,
            'third_party_packages': [],
        },
        'optional_test_runtime': {
            'node': '18+',
            'used_for': 'bounded inline JavaScript logic harness in selftest',
            'required_for_main_commands': False,
        },
        'engine': {
            'requested': route['requested_engine'],
            'profile_id': route['profile']['id'],
            'engine_version': brief.get('engine_version', 'unspecified'),
            'language': brief.get('language'),
            'language_version': brief.get('language_version', 'unspecified'),
            'target_platform': brief.get('target_platform'),
            'build_environment': brief.get('build_environment', {}),
        },
        'external_integrations': {
            'status': 'not-bundled',
            'network': False,
            'engine_editors': False,
            'compilers': False,
            'sdks': False,
            'stores': False,
            'credentials': False,
        },
        'locked_resources': {
            'catalog': config['paths']['catalog'],
            'engine_profiles': config['paths']['engine_profiles'],
            'brief_schema': config['paths']['brief_schema'],
            'production_template': config['prompts']['production_template'],
            'html5_template': config['prompts']['html5_template'],
        },
        'notes': ['Exact external versions must come from the brief or user-supplied local official documentation.'],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + '\n'


def _render_build_instructions(brief: dict, route: dict, prototype_enabled: bool) -> str:
    lines = [
        '# Build and Verification Instructions',
        '',
        'All commands are run from the future skill root with the `python3` interpreter specified explicitly.',
        '',
        '## Local tool commands',
        '',
        '`python3 ultimate-game-maker/tools-game-maker.py --help`',
        '',
        '`python3 ultimate-game-maker/tools-game-maker.py capabilities --format text`',
        '',
        '`python3 ultimate-game-maker/tools-game-maker.py validate --brief <brief.json> --format text`',
        '',
        '`python3 ultimate-game-maker/tools-game-maker.py plan --brief <brief.json> --output <output-directory>`',
        '',
        '`python3 ultimate-game-maker/tools-game-maker.py selftest --format json`',
        '',
        '## Runnable prototype',
        '',
    ]
    if prototype_enabled:
        lines.extend([
            'A self-contained HTML5 file was generated at `prototype/index.html`.',
            'Open it directly in a modern browser, or serve the output directory with any local static file server. No build step, package install, CDN, or network access is required.',
            'Use WASD, arrow keys, or pointer drag; P pauses, R restarts, and M mutes.',
            '',
        ])
    else:
        lines.extend([
            f'No runnable prototype was generated for `{route["requested_engine"]}` because the routed profile is `{route["profile"]["source_generation"]}`.',
            'Use the generated architecture, version notes, and build checklist with your own licensed engine, SDK, and compiler. The tool does not bundle or launch them.',
            '',
        ])
    lines.extend([
        '## External engine or platform build',
        '',
        '1. Supply exact engine, editor, SDK, compiler, language standard, package, and build-tool versions.',
        '2. Supply local official documentation files with `plan --docs` or the `docs` command.',
        '3. Review `version-notes.md`, `interoperability.json`, and `dependency-lock.json`.',
        '4. Run the external build in your own environment using the documented commands from those official materials.',
        '5. Record clean-machine evidence, failing tests, and recovery steps before requesting release approval.',
        '6. Do not publish, deploy, submit to a store, change credentials, or make financial changes without human approval.',
        '',
        '## Human approval',
        '',
        '`python3 ultimate-game-maker/tools-game-maker.py gate --action deploy --reason "<human review>" --confirm APPROVE`',
        '',
        'The gate records a local receipt only; it never performs the external action.',
        '',
    ])
    return '\n'.join(lines)


def _render_test_plan(brief: dict, selection: dict, route: dict, checks_count: int) -> str:
    return '\n'.join([
        '# Test Plan',
        '',
        '## Checks that can fail locally',
        '',
        '- Brief schema validation rejects missing, invalid, and credential-like fields.',
        '- Catalog validation requires 33 handlers and 330 unique specialists.',
        '- Source parsing rejects empty input and reports normalization statistics.',
        '- Capability packet rendering requires every selected specialist to have a distinct responsibility.',
        '- HTML generation rejects external resources, unresolved placeholders, and missing pure logic exports.',
        '- The optional Node harness executes the inline JavaScript with DOM stubs and asserts deterministic state, collection, hazards, pause, reset, and seed changes.',
        '- Recovery requires a matching brief signature and regenerates only missing files unless approved replacement is requested.',
        '',
        '## Brief acceptance criteria',
        '',
        _md_list(brief.get('acceptance_criteria', [])),
        '',
        f'## Generated acceptance records: {checks_count}',
        '',
        f'Selected handlers: **{selection["handler_count"]}**. Selected specialists: **{selection["skill_count"]}**.',
        f'Routed profile: `{route["profile"]["id"]}`; version status: `{route["version_status"]}`.',
        '',
        '## Planned but external checks',
        '',
        '- Engine compilation, clean startup on target devices, store certification, GPU captures, network soak tests, and live-service migrations are documented but not executed by this tool.',
        '- Each external check needs a real runtime, SDK, device, or service supplied by the user.',
        '',
    ])


def _render_recovery_plan(output_root: str, file_count: int, catalog: dict, route: dict, token: str) -> str:
    root = output_root
    return '\n'.join([
        '# Recovery Plan',
        '',
        'This file explains how to recover generated outputs without modifying passing work.',
        '',
        '## Recovery state',
        '',
        f'- State file: `{root}/recovery-state.json`.',
        f'- Expected output files: **{file_count}**.',
        '- The state stores the brief signature, effective config, selection filters, resource paths, and output file names. It contains no hashes, credentials, or raw logs.',
        '',
        '## Missing-file recovery',
        '',
        f'`python3 ultimate-game-maker/tools-game-maker.py recover --state {root}/recovery-state.json --brief <brief.json>`',
        '',
        'The command regenerates only missing files and preserves existing files. It errors if the brief signature changed; use `plan` with a new output directory for a changed brief.',
        '',
        '## Approved replacement',
        '',
        f'`python3 ultimate-game-maker/tools-game-maker.py recover --state {root}/recovery-state.json --brief <brief.json> --replace-existing --confirm {token}`',
        '',
        'Replacement is destructive and requires the configured approval token. A rollback copy of each replaced file is retained in memory until the command succeeds.',
        '',
        '## Failure isolation',
        '',
        _recovery_steps(catalog, route),
        '',
    ])


def _render_approval_gates(brief: dict, config: dict) -> str:
    gates = list(dict.fromkeys(list(brief.get('approval_gates', [])) + config['approval']['required_actions']))
    records = [{
        'action': gate,
        'status': 'pending',
        'human_review_required': True,
        'external_execution': False,
        'approval_command': f'python3 ultimate-game-maker/tools-game-maker.py gate --action {gate} --reason "<human review>" --confirm {config["approval"]["confirmation_token"]}',
    } for gate in gates]
    return json.dumps({
        'schema_id': 'ugm-approval-gates-1.0',
        'gate_count': len(records),
        'receipt_does_not_execute_action': True,
        'gates': records,
    }, indent=2, ensure_ascii=False) + '\n'


def _render_production_graph(selection: dict, brief: dict, route: dict) -> str:
    nodes = []
    for handler in selection['handlers']:
        nodes.append({
            'id': handler['id'],
            'type': 'category-handler',
            'operation': handler['operation'],
            'category': handler['category'],
            'specialist_count': len(handler['skills']),
            'external_execution': False,
        })
    edges = [{'from': nodes[index]['id'], 'to': nodes[index + 1]['id'], 'kind': 'handoff'} for index in range(len(nodes) - 1)]
    return json.dumps({
        'schema_id': 'ugm-production-graph-1.0',
        'umbrella': 'ultimate-game-maker',
        'goal': brief.get('goal'),
        'engine': route['requested_engine'],
        'seed': brief.get('seed', 0),
        'node_count': len(nodes),
        'edge_count': len(edges),
        'nodes': nodes,
        'edges': edges,
        'approval_boundary': 'external actions are represented as gates and never executed',
    }, indent=2, ensure_ascii=False) + '\n'


def _render_interoperability(catalog: dict, brief: dict, route: dict) -> str:
    return json.dumps({
        'schema_id': 'ugm-interoperability-1.0',
        'catalog_contract': catalog['contract']['interoperability'],
        'brief_values': {
            'engine': route['requested_engine'],
            'engine_version': brief.get('engine_version', 'unspecified'),
            'language': brief.get('language'),
            'language_version': brief.get('language_version', 'unspecified'),
            'target_platform': brief.get('target_platform'),
            'build_environment': brief.get('build_environment', {}),
            'coordinate_system': brief.get('controls', {}).get('coordinate_system', 'unspecified'),
            'units': brief.get('controls', {}).get('units', 'unspecified'),
        },
        'routed_profile_fields': route['profile'].get('version_fields', []),
        'missing_version_evidence': route['missing_version_evidence'],
        'version_status': route['version_status'],
    }, indent=2, ensure_ascii=False) + '\n'


def _resource_paths(config: dict, options: dict) -> dict:
    return {
        'catalog': str(options.get('catalog') or config['paths']['catalog']),
        'engine_profiles': str(options.get('engine_profiles') or config['paths']['engine_profiles']),
        'brief_schema': str(options.get('brief_schema') or config['paths']['brief_schema']),
        'production_template': str(options.get('template') or config['prompts']['production_template']),
        'html5_template': str(options.get('html5_template') or config['prompts']['html5_template']),
    }


def _validate_files(files: dict, config: dict) -> None:
    limits = config['limits']
    if len(files) > int(limits['max_output_files']):
        raise common.ToolError('TOO_MANY_OUTPUT_FILES', 'The generated output exceeds the configured file limit.', [len(files)])
    total = 0
    for relative, content in files.items():
        pure = PurePosixPath(relative)
        if pure.is_absolute() or '..' in pure.parts or '\\' in relative:
            raise common.ToolError('INVALID_OUTPUT_PATH', 'A generated output path escapes the output root.', [relative])
        size = len(content.encode('utf-8') if isinstance(content, str) else content)
        if size > int(limits['max_generated_file_bytes']):
            raise common.ToolError('OUTPUT_TOO_LARGE', 'A generated file exceeds the configured size limit.', [relative, size])
        total += size
    if total > int(limits['max_total_output_bytes']):
        raise common.ToolError('OUTPUT_TOO_LARGE', 'The total generated output exceeds the configured byte limit.', [total])


def render_outputs(brief: dict, config: dict, catalog: dict, profiles: dict, deadline: common.Deadline,
                   options: dict) -> dict:
    """Render every consolidated output deterministically. No filesystem writes here."""
    deadline.check('render production outputs')
    effective_brief = apply_brief_overrides(brief, options)
    route = registry.route_engine(effective_brief, profiles, options.get('engine_override'))
    selection = registry.select(catalog, config, options.get('categories'), options.get('skills'), effective_brief)
    packets = capability_packets.build_packets(selection, effective_brief, route, config, deadline)
    deadline.check('render capability documents')
    docs_paths = [Path(value) for value in (options.get('docs_paths') or [])]
    evidence = docs.ingest(docs_paths, effective_brief, route, config, deadline) if docs_paths else None
    prototype_enabled = bool(options.get('include_prototype', True) and route['prototype_available'])
    prototype_report = None
    prototype_html = None
    if prototype_enabled:
        prototype_html, prototype_report = prototype.render_prototype(
            effective_brief, config, route, deadline,
            template_override=options.get('html5_template'),
            skill_root=options.get('skill_root'),
        )
    deadline.check('compose consolidated outputs')
    template = _load_template(config, options.get('template'), options.get('skill_root'))
    checks = capability_packets.acceptance_checks(packets, effective_brief, route)
    checks_payload = json.loads(checks)
    approval_gates = list(dict.fromkeys(list(effective_brief.get('approval_gates', [])) + config['approval']['required_actions']))
    files: dict[str, str] = {}
    files['capability-packets.json'] = capability_packets.packets_json(packets, selection, effective_brief, route, config)
    files['capability-packets.md'] = capability_packets.packets_markdown(packets, selection, effective_brief, route)
    files['game-design.md'] = _render_game_design(effective_brief, route, selection)
    files['architecture.md'] = _render_architecture(effective_brief, route, config, selection)
    files['version-notes.md'] = docs.render_version_notes(effective_brief, route, evidence)
    files['dependency-lock.json'] = _render_dependency_lock(effective_brief, route, config)
    files['build-instructions.md'] = _render_build_instructions(effective_brief, route, prototype_enabled)
    files['test-plan.md'] = _render_test_plan(effective_brief, selection, route, checks_payload['record_count'])
    files['acceptance-checks.json'] = checks
    files['approval-gates.json'] = _render_approval_gates(effective_brief, config)
    files['production-graph.json'] = _render_production_graph(selection, effective_brief, route)
    files['interoperability.json'] = _render_interoperability(catalog, effective_brief, route)
    if evidence:
        files['official-docs-evidence.json'] = docs.render_docs_json(evidence)
    if prototype_enabled and prototype_html and prototype_report:
        files['prototype/index.html'] = prototype_html
        files['prototype-report.json'] = json.dumps(prototype_report, indent=2, ensure_ascii=False) + '\n'
    output_root = str(options.get('output_root') or config['storage']['output_root'])
    expected_files = sorted(set(files.keys()) | {'production-plan.md', 'recovery-plan.md', 'recovery-state.json'})
    files['recovery-plan.md'] = _render_recovery_plan(output_root, len(expected_files), catalog, route, config['approval']['confirmation_token'])
    state_options = {
        key: [str(item) for item in value] if isinstance(value, list) else value
        for key, value in options.items() if key in (
            'categories', 'skills', 'engine_override', 'language_override', 'platform_override',
            'version_override', 'seed_override', 'docs_paths', 'include_prototype', 'template',
            'html5_template', 'catalog', 'engine_profiles', 'brief_schema', 'output_root',
        )
    }
    state = {
        'schema_id': 'ugm-recovery-state-1.0',
        'umbrella': 'ultimate-game-maker',
        'output_root': output_root,
        'brief_signature': _brief_signature(effective_brief),
        'selection': {key: selection[key] for key in ('mode', 'requested_categories', 'requested_skills', 'handler_count', 'skill_count')},
        'routing': {
            'requested_engine': route['requested_engine'],
            'profile_id': route['profile']['id'],
            'version_status': route['version_status'],
            'missing_version_evidence': route['missing_version_evidence'],
            'prototype_available': route['prototype_available'],
        },
        'options': state_options,
        'config': copy.deepcopy(config),
        'resource_paths': _resource_paths(config, options),
        'expected_files': expected_files,
        'file_count': len(expected_files),
        'prototype_included': prototype_enabled,
        'local_docs_included': bool(evidence),
        'recovery_rules': [
            'Validate inputs before regeneration.',
            'Preserve existing passing files.',
            'Regenerate missing files only unless approved replacement is requested.',
            'Rerun selftest after recovery.',
        ],
    }
    template_values = {
        '{{TITLE}}': f'Production Plan: {effective_brief.get("genre", "Game")} / {route["requested_engine"]}',
        '{{UMBRELLA}}': 'ultimate-game-maker',
        '{{GOAL}}': str(effective_brief.get('goal')),
        '{{GENRE}}': str(effective_brief.get('genre')),
        '{{ENGINE}}': route['requested_engine'],
        '{{LANGUAGE}}': str(effective_brief.get('language')),
        '{{PLATFORM}}': str(effective_brief.get('target_platform')),
        '{{SEED}}': str(effective_brief.get('seed', 0)),
        '{{SELECTION_MODE}}': selection['mode'],
        '{{HANDLER_COUNT}}': str(selection['handler_count']),
        '{{SKILL_COUNT}}': str(selection['skill_count']),
        '{{VERSION_STATUS}}': route['version_status'],
        '{{MISSING_EVIDENCE}}': ', '.join(f'`{item}`' for item in route['missing_version_evidence']) or 'none',
        '{{PROVIDER}}': config['providers']['blueprint'],
        '{{MODEL_PROFILE}}': config['models']['blueprint'],
        '{{PROMPT_SET}}': config['prompts']['production_template'],
        '{{ACCEPTANCE_CRITERIA}}': _md_list(effective_brief.get('acceptance_criteria', [])),
        '{{CONSTRAINTS}}': _md_list(list(effective_brief.get('constraints', [])) + list(effective_brief.get('references', []))),
        '{{OUTPUT_FILES}}': _output_list(expected_files),
        '{{HANDLER_TABLE}}': _handler_table(selection),
        '{{CAPABILITY_SECTIONS}}': _capability_sections(selection),
        '{{COMPLETE_CAPABILITY_LIST}}': _complete_capability_list(catalog),
        '{{RECOVERY_STEPS}}': _recovery_steps(catalog, route),
        '{{APPROVAL_GATES}}': _md_list([f'`{gate}` remains pending' for gate in approval_gates]),
        '{{LIMITATIONS}}': _limitations(config, route),
    }
    files['production-plan.md'] = _render_template(template, template_values)
    files['recovery-state.json'] = json.dumps(state, indent=2, ensure_ascii=False) + '\n'
    if sorted(files.keys()) != expected_files:
        raise common.ToolError('RECOVERY_STATE_MISMATCH', 'Recovery state does not match the rendered output list.', [len(files), len(expected_files)])
    _validate_files(files, config)
    deadline.check('finish production outputs')
    return {
        'files': files,
        'state': state,
        'summary': {
            'umbrella': 'ultimate-game-maker',
            'selection_mode': selection['mode'],
            'handler_count': selection['handler_count'],
            'skill_count': selection['skill_count'],
            'packet_count': len(packets),
            'file_count': len(files),
            'total_bytes': sum(len(value.encode('utf-8')) for value in files.values()),
            'engine': route['requested_engine'],
            'profile_id': route['profile']['id'],
            'version_status': route['version_status'],
            'prototype_included': prototype_enabled,
            'local_docs_included': bool(evidence),
            'approval_gate_count': len(approval_gates),
        },
        'route': route,
        'selection': selection,
        'prototype_report': prototype_report,
        'docs_evidence': evidence,
    }


def write_outputs(files: dict, output_root: str, config: dict, deadline: common.Deadline,
                  approve_overwrite: bool = False, dry_run: bool = False) -> dict:
    sizes = {name: len(value.encode('utf-8')) for name, value in files.items()}
    if dry_run:
        return {
            'written': False,
            'dry_run': True,
            'output_root': output_root,
            'file_count': len(files),
            'total_bytes': sum(sizes.values()),
            'files': sizes,
        }
    root = Path(output_root)
    order = ['recovery-state.json'] + sorted(name for name in files if name != 'recovery-state.json')
    with common.OutputWriter(root, config, approve_overwrite=approve_overwrite, deadline=deadline) as writer:
        for relative in order:
            writer.write(relative, files[relative])
    return {
        'written': True,
        'dry_run': False,
        'output_root': output_root,
        'file_count': len(files),
        'total_bytes': sum(sizes.values()),
        'files': sizes,
    }


def recover_from_state(state: dict, brief: dict, config: dict | None, catalog: dict, profiles: dict,
                       deadline: common.Deadline, output_root: str | None = None,
                       replace_existing: bool = False, confirm: str | None = None,
                       skill_root: Path | None = None) -> dict:
    if not isinstance(state, dict) or state.get('schema_id') != 'ugm-recovery-state-1.0':
        raise common.ToolError('INVALID_RECOVERY_STATE', 'The recovery state schema is not ugm-recovery-state-1.0.')
    effective_config = copy.deepcopy(state.get('config') or config or {})
    if not isinstance(effective_config, dict):
        raise common.ToolError('INVALID_RECOVERY_STATE', 'The recovery state has no usable config.')
    if replace_existing and confirm != effective_config['approval']['confirmation_token']:
        raise common.ToolError('APPROVAL_REQUIRED', f'Replacing existing files requires --confirm {effective_config["approval"]["confirmation_token"]}.', [confirm])
    options = copy.deepcopy(state.get('options', {}))
    options['output_root'] = str(output_root or state.get('output_root') or effective_config['storage']['output_root'])
    options['skill_root'] = skill_root
    resource_paths = state.get('resource_paths', {})
    for option_key, resource_key in (('catalog', 'catalog'), ('engine_profiles', 'engine_profiles'), ('brief_schema', 'brief_schema'), ('template', 'production_template'), ('html5_template', 'html5_template')):
        if not options.get(option_key) and resource_paths.get(resource_key):
            options[option_key] = resource_paths[resource_key]
    rendered = render_outputs(brief, effective_config, catalog, profiles, deadline, options)
    if rendered['state']['brief_signature'] != state.get('brief_signature'):
        raise common.ToolError('RECOVERY_INPUT_MISMATCH', 'The supplied brief no longer matches the recovery state.', [rendered['state']['brief_signature'], state.get('brief_signature')], ['Use plan with a new output directory for a changed brief, or restore the original brief.'])
    if sorted(rendered['files'].keys()) != sorted(state.get('expected_files', [])):
        raise common.ToolError('RECOVERY_OUTPUT_MISMATCH', 'The regenerated output list does not match the recovery state.', [len(rendered['files']), len(state.get('expected_files', []))])
    root = Path(options['output_root'])
    regenerated: list[str] = []
    preserved: list[str] = []
    with common.OutputWriter(root, effective_config, approve_overwrite=replace_existing,
                             allow_existing=True, deadline=deadline) as writer:
        for relative in ['recovery-state.json'] + sorted(name for name in rendered['files'] if name != 'recovery-state.json'):
            target = root.joinpath(*PurePosixPath(relative).parts)
            if target.exists() and not replace_existing:
                preserved.append(relative)
                continue
            writer.write(relative, rendered['files'][relative])
            regenerated.append(relative)
    return {
        'output_root': str(root),
        'regenerated': regenerated,
        'preserved': preserved,
        'replaced_existing': bool(replace_existing),
        'file_count': len(rendered['files']),
        'brief_signature': rendered['state']['brief_signature'],
    }
