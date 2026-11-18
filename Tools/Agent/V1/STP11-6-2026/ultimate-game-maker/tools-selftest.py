#!/usr/bin/env python3
"""Real-interface validation suite loaded by tools-game-maker.py selftest.

Uses fixed local subprocess arguments, no shell, bounded timeouts, and temporary files
that are removed after the run. The optional Node harness executes only generated inline
JavaScript in a VM with DOM stubs.
"""
from __future__ import annotations

import ast
import copy
import csv
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import ugm_capability_packets as capability_packets
import ugm_common as common
import ugm_docs as docs
import ugm_planner as planner
import ugm_production_engine as production_engine
import ugm_prototype as prototype
import ugm_registry as registry
import ugm_source_parser as source_parser
import ugm_validation as validation

REFERENCE_RE = re.compile(r'ultimate-game-maker/(?:tools|asset|knowledge)-[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z0-9]+')


def _run_process(argv: list[str], cwd: Path, timeout: float, max_bytes: int,
                 input_text: str | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        argv,
        cwd=str(cwd),
        input=input_text,
        text=True,
        encoding='utf-8',
        errors='replace',
        capture_output=True,
        timeout=timeout,
        check=False,
        shell=False,
    )
    output_bytes = len((result.stdout or '').encode('utf-8')) + len((result.stderr or '').encode('utf-8'))
    if output_bytes > max_bytes:
        raise RuntimeError(f'process output exceeded {max_bytes} bytes')
    return result


def _run_cli(args: list[str], config: dict, skill_root: Path, tool_dir: Path,
             expect_json: bool = True, cwd: Path | None = None,
             tool_path: str | None = None, input_text: str | None = None):
    timeout = float(config['selftest']['cli_timeout_seconds'])
    max_bytes = int(config['selftest']['max_process_output_bytes'])
    executable = str(tool_dir / 'tools-game-maker.py') if tool_path is None else tool_path
    argv = [sys.executable, executable] + list(args)
    if expect_json and not any(item == '--format' or item.startswith('--format=') for item in args):
        argv += ['--format', 'json']
    result = _run_process(argv, cwd or skill_root, timeout, max_bytes, input_text)
    if expect_json:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f'CLI did not return valid JSON: {exc}; stderr={result.stderr[:200]}')
        return payload, result
    return result.stdout, result


def _assert_ok(payload: dict, label: str):
    if not payload.get('ok'):
        error = payload.get('error', {})
        raise AssertionError(f'{label}: {error.get("code")} {error.get("message")}')
    return payload['result']


def _assert_error(payload: dict, code: str, label: str):
    if payload.get('ok') or payload.get('error', {}).get('code') != code:
        raise AssertionError(f'{label}: expected structured error {code}, got {payload.get("error", {}).get("code")}')
    return payload['error']


def _check_references(tool_dir: Path, root: Path) -> int:
    refs: set[str] = set()
    for path in tool_dir.iterdir():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        refs.update(REFERENCE_RE.findall(text))
    for ref in refs:
        common.resolve_reference(ref, root)
    return len(refs)


def _check_tool_folder(tool_dir: Path) -> dict:
    entries = list(tool_dir.iterdir())
    folders = [entry.name for entry in entries if entry.is_dir()]
    files = [entry.name for entry in entries if entry.is_file()]
    bad = [name for name in files if not name.startswith(('tools-', 'asset-', 'knowledge-'))]
    knowledge = [name for name in files if name.startswith('knowledge-')]
    archives = [name for name in files if Path(name).suffix.lower() in ('.zip', '.gz', '.tar', '.7z', '.rar', '.pyc')]
    if folders:
        raise AssertionError(f'internal subfolders: {folders}')
    if bad:
        raise AssertionError(f'unclassified files: {bad}')
    if knowledge != ['knowledge-readme.md']:
        raise AssertionError(f'expected one knowledge-readme.md, got {knowledge}')
    if 'SKILL.md' in files:
        raise AssertionError('Skill Tool folder must not contain SKILL.md')
    if archives:
        raise AssertionError(f'archives or caches found: {archives}')
    if not any(name.startswith('tools-') and name.endswith('.py') for name in files):
        raise AssertionError('no runnable tools- Python file')
    return {'file_count': len(files), 'tools_files': sum(name.startswith('tools-') for name in files), 'asset_files': sum(name.startswith('asset-') for name in files), 'knowledge_files': len(knowledge)}


def _write_fixtures(root: Path, config: dict, skill_root: Path, tool_dir: Path) -> dict:
    ref = config['paths']['brief_template']
    brief = common.load_json_file(common.resolve_reference(ref, skill_root), int(config['limits']['max_brief_bytes']), 'asset-brief-template.json', config)
    changed = copy.deepcopy(brief)
    changed['goal'] = 'A different arcade route with new parcels and a new deterministic seed.'
    changed['seed'] = int(brief.get('seed', 0)) + 1
    invalid = copy.deepcopy(brief)
    invalid.pop('acceptance_criteria', None)
    paths = {
        'brief': root / 'brief.json',
        'changed': root / 'brief-changed.json',
        'invalid': root / 'brief-invalid.json',
        'missing': root / 'missing-brief.json',
        'plain': root / 'source-plain.txt',
        'markdown': root / 'source.md',
        'html': root / 'source.html',
        'multi': root / 'source-multi.md',
        'doc-md': root / 'official-notes.md',
        'doc-html': root / 'official-notes.html',
        'custom-template': root / 'custom-production-template.md',
        'example': root / 'example-brief.json',
        'parsed': root / 'parsed-source.json',
        'docs-output': root / 'docs-evidence.json',
        'prototype': root / 'standalone-prototype.html',
        'plan1': root / 'plan-output',
        'plan2': root / 'plan-changed-output',
        'non-html5-plan': root / 'non-html5-plan-output',
        'non-html5-prototype': root / 'non-html5-prototype.html',
        'filtered-plan': root / 'filtered-plan-output',
        'receipt': root / 'approval-receipt.json',
        'clean-root': root / 'clean-skill-root',
        'production-request': root / 'production-request.json',
        'production-invalid-request': root / 'production-invalid-request.json',
        'production-missing-request': root / 'production-missing-request.json',
        'production-validation-report': root / 'production-validation-report.json',
        'production-describe': root / 'production-describe.json',
        'production-plan': root / 'production-plan.json',
        'production-plan-changed': root / 'production-plan-changed.json',
        'production-bundle': root / 'production-bundle',
        'production-evidence': root / 'production-evidence.json',
        'production-filled-evidence': root / 'production-filled-evidence.json',
    }
    paths['brief'].write_text(json.dumps(brief, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    paths['changed'].write_text(json.dumps(changed, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    paths['invalid'].write_text(json.dumps(invalid, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    paths['plain'].write_text(
        'Category 1: Game Vision, Design, and Production Planning\n'
        '1. Skill Name: Game Creative Director\n'
        'Description: Defines observable player outcomes and editable handoffs.\n'
        '2. Skill Name: Core Loop Designer\n'
        'Description: Defines repeatable loops and testable player outcomes.\n',
        encoding='utf-8',
    )
    paths['markdown'].write_text(
        '# Category 1: Game Vision\n\n'
        '1. **Skill Name:** Game Creative Director\n'
        '   **Description:** Defines player outcomes and [planning notes](local-note.md).\n'
        '2. **Skill Name:** Core Loop Designer\n'
        '   **Description:** Defines repeatable loops and acceptance checks.\n',
        encoding='utf-8',
    )
    paths['html'].write_text(
        '<style>.injected{color:red}</style>'
        '<div class="catalog" data-row="7"><h2>Category 1: Game Vision</h2><ol>'
        '<li><b>1. Skill Name:</b> Game Creative Director</li>'
        '<li>Description: Defines observable player outcomes and handoffs.</li>'
        '<li>2. Skill Name: Core Loop Designer</li>'
        '<li>Description: Defines repeatable loops and testable outcomes.</li>'
        '</ol></div>',
        encoding='utf-8',
    )
    paths['multi'].write_text(
        '# Category 1: Game Vision, Design, and Production Planning\n\n'
        '1. Skill Name: Game Creative Director\n'
        '   Description: Defines observable player outcomes and editable handoffs.\n'
        '2. Skill Name: Core Loop Designer\n'
        '   Description: Defines repeatable loops and testable player outcomes.\n\n'
        '# Category 2: Engine Discovery, Documentation, and Version Intelligence\n\n'
        '1. Skill Name: Game Engine Capability Router\n'
        '   Description: Routes requests to version-correct documentation evidence.\n'
        '2. Skill Name: Engine Version Compatibility Analyst\n'
        '   Description: Compares exact engine and editor versions against a brief.\n',
        encoding='utf-8',
    )
    paths['doc-md'].write_text(
        '# Official local note for html5\n\n'
        'Engine version ES2020 browser runtime, language javascript, language version ECMAScript 2020, platform browser.\n'
        'A deprecated API was removed in a migration. Package build export SDK and compiler notes are user supplied.\n',
        encoding='utf-8',
    )
    paths['doc-html'].write_text(
        '<style>.hidden{display:none}</style><section data-version="ES2020">'
        '<p>API changed. Version deprecation and migration guidance applies to the package build and export SDK.</p>'
        '</section>',
        encoding='utf-8',
    )
    template_text = common.read_text_limited(
        common.resolve_reference(config['prompts']['production_template'], skill_root),
        int(config['limits']['max_template_bytes']), 'asset-production-template.md', config,
    )
    paths['custom-template'].write_text(template_text + '\nCUSTOM PROMPT MARKER\n', encoding='utf-8')
    request_template = common.load_json_file(
        common.resolve_reference(config['paths']['request_template'], skill_root),
        int(config['limits']['max_request_bytes']), 'asset-production-request-template.json', config)
    paths['production-request'].write_text(json.dumps(request_template, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    paths['production-invalid-request'].write_text(json.dumps({'project': {'name': ''}, 'capabilities': {'skills': []}}, indent=2) + '\n', encoding='utf-8')
    return {'brief': brief, 'changed': changed, 'paths': paths}


def run(config: dict, deadline: common.Deadline, args, skill_root: Path, tool_dir: Path) -> dict:
    checks: list[dict] = []
    warnings: list[str] = []
    report = {
        'schema_id': 'ugm-selftest-report-1.0',
        'passed': False,
        'checks': checks,
        'warnings': warnings,
        'environment': {
            'python': sys.version.split()[0],
            'node_detected': bool(shutil.which('node')),
            'node_skipped_by_config': bool(config['selftest'].get('skip_node')),
        },
    }

    def add(name: str, operation):
        try:
            deadline.check(f'selftest: {name}')
            outcome = operation()
            if isinstance(outcome, tuple) and outcome and outcome[0] == 'skipped':
                checks.append({'name': name, 'status': 'skipped', 'detail': str(outcome[1])})
            else:
                checks.append({'name': name, 'status': 'passed', 'detail': str(outcome) if outcome not in (None, '') else 'ok'})
        except Exception as exc:
            checks.append({'name': name, 'status': 'failed', 'detail': f'{type(exc).__name__}: {str(exc)[:500]}'})

    with tempfile.TemporaryDirectory(prefix='ugm-selftest-') as temp_name:
        root = Path(temp_name)
        fixtures = _write_fixtures(root, config, skill_root, tool_dir)
        paths = fixtures['paths']
        brief = fixtures['brief']

        def cli(command_args, expect_json=True, cwd=None, tool_path=None, input_text=None):
            return _run_cli(command_args, config, skill_root, tool_dir, expect_json, cwd, tool_path, input_text)

        add('tool folder structure and prefixed filenames', lambda: _check_tool_folder(tool_dir))
        add('exact case-sensitive resource references from skill root', lambda: f'{_check_references(tool_dir, skill_root)} references resolved')

        def ref_resources():
            catalog = registry.load_catalog(config, deadline, None, skill_root)
            profiles = registry.load_engine_profiles(config, deadline, None, skill_root)
            schema = validation.load_schema(config, None, skill_root)
            registry.validate_catalog(catalog)
            assert len(profiles['profiles']) >= 1 and schema['schema_id'] == 'ugm-brief-schema-1.0'
            return f'{len(catalog["handlers"])} handlers, {sum(len(h["skills"]) for h in catalog["handlers"])} specialists'
        add('catalog, engine-profile, and brief-schema resources load', ref_resources)

        def help_check():
            payload, process = cli(['--help'], expect_json=False)
            assert process.returncode == 0 and 'usage:' in payload.lower() and 'tools-game-maker.py' in payload
            return 'built-in argparse help rendered'
        add('primary --help command', help_check)

        def capabilities_check():
            payload, _ = cli(['capabilities', '--detailed', '--format', 'json'])
            result = _assert_ok(payload, 'capabilities')
            assert result['handler_count'] == 33 and result['skill_count'] == 330
            assert sum(len(handler.get('skills', [])) for handler in result['handlers']) == 330
            assert result['command_count'] >= 15
            text, process = cli(['capabilities', '--format', 'text'], expect_json=False)
            assert process.returncode == 0 and 'Handlers:' in text
            return '33 handlers, 330 specialists, command list, and text rendering'
        add('capabilities command and text capability list', capabilities_check)

        def version_and_config_check():
            payload, _ = cli(['version', '--format', 'json'])
            result = _assert_ok(payload, 'version')
            assert result['version'] == common.VERSION
            payload, _ = cli(['capabilities', '--set', 'runtime.deadline_seconds=12', '--format', 'json'])
            _assert_ok(payload, 'config override')
            return 'version and --set config override'
        add('version command and documented config override', version_and_config_check)

        def catalog_check():
            payload, _ = cli(['catalog', '--skill', 'Game Creative Director', '--format', 'json'])
            result = _assert_ok(payload, 'catalog skill')
            assert result['skill']['slug'] == 'game-creative-director'
            payload, _ = cli(['catalog', '--category', '03-unity', '--summary', '--format', 'json'])
            result = _assert_ok(payload, 'catalog category')
            assert result['handler']['operation'] == 'engine.unity'
            return 'exact specialist and category lookups'
        add('catalog command lookup', catalog_check)

        def parse_source_checks():
            plain, _ = cli(['parse-source', '--source', str(paths['plain']), '--output', str(paths['parsed']), '--format', 'json'])
            plain_result = _assert_ok(plain, 'parse-source plain')
            assert plain_result['parsed_source']['category_count'] == 1
            assert plain_result['parsed_source']['capability_count'] == 2
            assert json.loads(paths['parsed'].read_text(encoding='utf-8'))['schema_id'] == 'ugm-parsed-source-1.0'
            markdown, _ = cli(['parse-source', '--source', str(paths['markdown']), '--format', 'json'])
            markdown_result = _assert_ok(markdown, 'parse-source markdown')
            assert markdown_result['parsed_source']['source_format'] == 'markdown'
            html, _ = cli(['parse-source', '--source', str(paths['html']), '--umbrella', 'vision-tools', '--format', 'json'])
            html_result = _assert_ok(html, 'parse-source html')
            parsed = html_result['parsed_source']
            assert parsed['source_format'] == 'html' and parsed['umbrella']['slug'] == 'vision-tools'
            assert parsed['normalization']['css_or_script_blocks_removed'] >= 1
            assert 'color:red' not in json.dumps(parsed)
            stdin_payload, _ = cli(['parse-source', '--source', '-', '--format', 'json'], input_text=paths['plain'].read_text(encoding='utf-8'))
            stdin_result = _assert_ok(stdin_payload, 'parse-source stdin')
            assert stdin_result['parsed_source']['capability_count'] == 2
            return 'plain text, Markdown, copied HTML, umbrella override, file output, and stdin'
        add('parse-source normalizes all accepted input forms', parse_source_checks)

        def multi_category_source_check():
            payload, _ = cli(['parse-source', '--source', str(paths['multi']), '--format', 'json'])
            result = _assert_ok(payload, 'parse-source multi-category')
            parsed = result['parsed_source']
            assert parsed['category_count'] == 2 and parsed['capability_count'] == 4
            assert parsed['umbrella']['slug'] == 'game-production-orchestration'
            assert parsed['categories'][0]['name'].startswith('Game Vision')
            assert parsed['categories'][1]['name'].startswith('Engine Discovery')
            return 'two categories unified into one derived umbrella'
        add('parse-source combines several categories into one umbrella', multi_category_source_check)

        def example_check():
            payload, _ = cli(['example', '--output', str(paths['example']), '--format', 'json'])
            result = _assert_ok(payload, 'example')
            written = json.loads(paths['example'].read_text(encoding='utf-8'))
            assert written['goal'] == result['brief']['goal']
            return 'working example brief written and parsed'
        add('example command', example_check)

        def validate_checks():
            valid, _ = cli(['validate', '--brief', str(paths['brief']), '--format', 'json'])
            _assert_ok(valid, 'validate valid')
            invalid, _ = cli(['validate', '--brief', str(paths['invalid']), '--format', 'json'])
            _assert_error(invalid, 'INVALID_BRIEF', 'validate invalid')
            missing, _ = cli(['validate', '--brief', str(paths['missing']), '--format', 'json'])
            _assert_error(missing, 'MISSING_INPUT', 'validate missing')
            return 'valid, invalid, and missing brief inputs'
        add('validate command with valid, invalid, and missing input', validate_checks)

        def direct_registry_and_packets():
            catalog = registry.load_catalog(config, deadline, None, skill_root)
            profiles = registry.load_engine_profiles(config, deadline, None, skill_root)
            schema = validation.load_schema(config, None, skill_root)
            validation.validate_brief(brief, schema, config)
            selection = registry.select(catalog, config)
            route = registry.route_engine(brief, profiles, None)
            packets = capability_packets.build_packets(selection, brief, route, config, deadline)
            assert len(packets) == 330
            assert len({packet['skill']['slug'] for packet in packets}) == 330
            assert all(packet['handler']['id'] in capability_packets.SPECIAL for packet in packets)
            assert all(packet['skill']['focus'] and packet['contract']['checks'] for packet in packets)
            for handler in catalog['handlers']:
                assert registry.resolve_handler(catalog, handler['id'])['id'] == handler['id']
                for skill in handler['skills']:
                    found_handler, found_skill = registry.resolve_skill(catalog, skill['slug'])
                    assert found_handler['id'] == handler['id'] and found_skill['slug'] == skill['slug']
            changed = copy.deepcopy(brief)
            changed['goal'] = 'Different goal for direct packet comparison.'
            changed_packets = capability_packets.build_packets(selection, changed, route, config, deadline)
            assert packets[0]['request']['goal'] != changed_packets[0]['request']['goal']
            custom_config = copy.deepcopy(config)
            custom_config['models']['blueprint'] = 'custom-model-label'
            custom_config['providers']['blueprint'] = 'local-custom-provider'
            custom_config['storage']['output_root'] = 'custom-storage-root'
            options = {
                'template': str(paths['custom-template']),
                'include_prototype': False,
                'output_root': 'custom-storage-root',
                'skill_root': skill_root,
            }
            custom_render = planner.render_outputs(brief, custom_config, catalog, profiles, deadline, options)
            assert 'CUSTOM PROMPT MARKER' in custom_render['files']['production-plan.md']
            assert 'custom-model-label' in custom_render['files']['production-plan.md']
            assert custom_render['state']['output_root'] == 'custom-storage-root'
            return 'all 330 packets, 33 handlers, input sensitivity, and configurable prompts/models/storage'
        add('direct capability registry, packet generation, and configuration', direct_registry_and_packets)

        def capability_cli_checks():
            catalog = registry.load_catalog(config, deadline, None, skill_root)
            for handler in catalog['handlers']:
                slug = handler['skills'][0]['slug']
                payload, _ = cli(['capability', '--skill', slug, '--brief', str(paths['brief']), '--format', 'json'])
                result = _assert_ok(payload, f'capability {slug}')
                assert result['skill']['slug'] == slug
                assert result['markdown'].startswith('### ')
            text, process = cli(['capability', '--skill', 'game-creative-director', '--brief', str(paths['brief']), '--format', 'text'], expect_json=False)
            assert process.returncode == 0 and 'Responsibility' in text
            return '33 representative specialists through the real CLI plus text rendering'
        add('capability command reaches one specialist per category', capability_cli_checks)

        def docs_check():
            payload, _ = cli(['docs', '--brief', str(paths['brief']), '--docs', str(paths['doc-md']), str(paths['doc-html']), '--output', str(paths['docs-output']), '--format', 'json'])
            result = _assert_ok(payload, 'docs')
            evidence = result['evidence']
            assert evidence['status'] == 'complete-local-evidence'
            assert evidence['external_fetch']['status'] == 'not-attempted'
            assert any(record['markup_normalization']['css_or_script_blocks_removed'] >= 1 for record in evidence['files'])
            assert json.loads(paths['docs-output'].read_text(encoding='utf-8'))['schema_id'] == 'ugm-docs-evidence-1.0'
            unknown, _ = cli(['docs', '--brief', str(paths['brief']), '--docs', str(paths['brief'].with_suffix('.bin')), '--format', 'json'])
            _assert_error(unknown, 'MISSING_DOCS', 'docs missing')
            return 'local Markdown and HTML evidence, no network, and missing-file error'
        add('docs command with local official documentation evidence', docs_check)

        def prototype_check():
            payload, _ = cli(['prototype', '--brief', str(paths['brief']), '--output', str(paths['prototype']), '--format', 'json'])
            result = _assert_ok(payload, 'prototype')
            html = paths['prototype'].read_text(encoding='utf-8')
            audit = prototype.verify_html(html, result['report']['game_config'])
            assert audit['script_count'] == 1 and html.count('UltimateGameMakerPrototype') >= 2
            return 'generated HTML parsed, audited, and exported pure game logic'
        add('prototype command and HTML audit', prototype_check)

        def non_html5_checks():
            payload, _ = cli(['plan', '--brief', str(paths['brief']), '--engine', 'unity', '--language', 'csharp', '--engine-version', '2022.3.0', '--output', str(paths['non-html5-plan']), '--no-prototype', '--format', 'json'])
            result = _assert_ok(payload, 'non-html5 plan')
            assert result['summary']['profile_id'] == 'unity'
            assert result['summary']['prototype_included'] is False
            assert result['version_status'] == 'incomplete'
            assert not (paths['non-html5-plan'] / 'prototype' / 'index.html').exists()
            refused, _ = cli(['prototype', '--brief', str(paths['brief']), '--engine', 'unity', '--language', 'csharp', '--engine-version', '2022.3.0', '--output', str(paths['non-html5-prototype']), '--format', 'json'])
            _assert_error(refused, 'PROTOTYPE_NOT_AVAILABLE', 'non-html5 prototype')
            assert not paths['non-html5-prototype'].exists()
            return 'Unity plan-only routing, missing version evidence, and prototype refusal'
        add('non-HTML5 engine routing and prototype refusal', non_html5_checks)

        plan1_result = None

        def plan_check():
            nonlocal plan1_result
            payload, _ = cli(['plan', '--brief', str(paths['brief']), '--output', str(paths['plan1']), '--docs', str(paths['doc-md']), str(paths['doc-html']), '--format', 'json'])
            result = _assert_ok(payload, 'plan')
            assert result['summary']['handler_count'] == 33 and result['summary']['skill_count'] == 330
            assert result['summary']['prototype_included'] is True and result['summary']['local_docs_included'] is True
            assert result['output']['written'] is True
            state = json.loads((paths['plan1'] / 'recovery-state.json').read_text(encoding='utf-8'))
            actual = sorted(str(path.relative_to(paths['plan1']).as_posix()) for path in paths['plan1'].rglob('*') if path.is_file())
            assert actual == sorted(state['expected_files'])
            for relative in actual:
                text = (paths['plan1'] / relative).read_text(encoding='utf-8')
                if relative.endswith('.json'):
                    json.loads(text)
                elif relative.endswith('.md'):
                    assert text.strip() and '{{' not in text
                elif relative.endswith('.html'):
                    prototype.verify_html(text)
            packets = json.loads((paths['plan1'] / 'capability-packets.json').read_text(encoding='utf-8'))
            checks_payload = json.loads((paths['plan1'] / 'acceptance-checks.json').read_text(encoding='utf-8'))
            graph = json.loads((paths['plan1'] / 'production-graph.json').read_text(encoding='utf-8'))
            assert packets['packet_count'] == 330 and checks_payload['record_count'] >= 330 and graph['node_count'] == 33
            assert 'SKILL.md' not in actual and not any(name.lower().endswith(('.zip', '.gz', '.tar')) for name in actual)
            plan1_result = result
            return f'{len(actual)} outputs parsed; 330 packets and 33 graph nodes'
        add('plan command generates, writes, and parses every important output', plan_check)

        def dry_run_check():
            payload, _ = cli(['plan', '--brief', str(paths['brief']), '--output', str(root / 'dry-run-output'), '--dry-run', '--no-prototype', '--format', 'json'])
            result = _assert_ok(payload, 'dry-run plan')
            assert result['output']['dry_run'] is True and result['output']['written'] is False
            assert not (root / 'dry-run-output').exists()
            return 'no files written during dry run'
        add('plan dry-run and write gating', dry_run_check)

        def filtered_selection_check():
            payload, _ = cli(['plan', '--brief', str(paths['brief']), '--categories', '03-unity', '--skills', 'Game Creative Director', '--output', str(paths['filtered-plan']), '--no-prototype', '--format', 'json'])
            result = _assert_ok(payload, 'filtered plan')
            assert result['summary']['selection_mode'] == 'filtered-union'
            assert result['summary']['handler_count'] == 2
            assert result['summary']['skill_count'] == 11
            packets = json.loads((paths['filtered-plan'] / 'capability-packets.json').read_text(encoding='utf-8'))
            assert packets['packet_count'] == 11
            assert {packet['handler']['id'] for packet in packets['packets']} == {'01-vision-planning', '03-unity'}
            return 'category and specialist selectors unioned into one plan'
        add('plan category and specialist filters use a union', filtered_selection_check)

        def changed_input_check():
            payload, _ = cli(['plan', '--brief', str(paths['changed']), '--output', str(paths['plan2']), '--no-prototype', '--format', 'json'])
            result = _assert_ok(payload, 'changed plan')
            first = (paths['plan1'] / 'capability-packets.json').read_text(encoding='utf-8')
            second = (paths['plan2'] / 'capability-packets.json').read_text(encoding='utf-8')
            assert first != second
            assert fixtures['changed']['goal'] in second and fixtures['changed']['goal'] not in first
            return 'goal and seed change regenerated different packets'
        add('changed input produces a different result', changed_input_check)

        def gate_checks():
            denied, _ = cli(['gate', '--action', 'publish', '--reason', 'human review pending', '--format', 'json'])
            _assert_error(denied, 'APPROVAL_REQUIRED', 'gate without approval')
            approved, _ = cli(['gate', '--action', 'publish', '--reason', 'reviewed by release owner', '--confirm', 'APPROVE', '--output', str(paths['receipt']), '--format', 'json'])
            result = _assert_ok(approved, 'gate')
            assert result['receipt']['execution'] == 'not-performed'
            assert json.loads(paths['receipt'].read_text(encoding='utf-8'))['external_execution'] is False
            gates, _ = cli(['gate-list', '--brief', str(paths['brief']), '--format', 'json'])
            gate_result = _assert_ok(gates, 'gate-list')
            assert 'deploy' in gate_result['gates'] and 'credentials' in gate_result['gates']
            return 'approval refusal, receipt, no external execution, and gate list'
        add('gate and gate-list approval rules', gate_checks)

        def recovery_check():
            removed = paths['plan1'] / 'game-design.md'
            removed.unlink()
            payload, _ = cli(['recover', '--state', str(paths['plan1'] / 'recovery-state.json'), '--brief', str(paths['brief']), '--format', 'json'])
            result = _assert_ok(payload, 'recover')
            assert removed.exists() and 'game-design.md' in result['regenerated']
            assert len(result['preserved']) >= 10
            mismatch, _ = cli(['recover', '--state', str(paths['plan1'] / 'recovery-state.json'), '--brief', str(paths['changed']), '--format', 'json'])
            _assert_error(mismatch, 'RECOVERY_INPUT_MISMATCH', 'recover changed brief')
            return 'missing file restored, passing files preserved, changed brief refused'
        add('recover command and recovery path', recovery_check)

        def overwrite_approval_check():
            payload, _ = cli(['plan', '--brief', str(paths['brief']), '--output', str(paths['plan1']), '--no-prototype', '--format', 'json'])
            _assert_error(payload, 'APPROVAL_REQUIRED', 'overwrite without approval')
            return 'non-empty output directory refused without approval'
        add('overwrite approval gate', overwrite_approval_check)

        def production_assets_check():
            engine = production_engine.build_engine(config, deadline, skill_root, tool_dir)
            stats = engine.stats
            assert stats['categories'] == 33 and stats['capabilities'] == 330
            assert stats['distinct_domain_signatures'] == 330 and stats['total_checks'] >= 3300
            assert engine.unsupported_schema_keywords == [] and engine.policy_problems == []
            assert len(engine.registry.category_list()) == 33 and len(engine.registry.skills()) == 330
            return '33 profiles, 330 distinct contracts, 3300 checks, 12 routes, and valid policy/schema'
        add('combined production assets load with 330 distinct contracts', production_assets_check)

        def python_39_syntax_check():
            checked = 0
            for source in sorted(tool_dir.glob('*.py')):
                tree = ast.parse(source.read_text(encoding='utf-8'), filename=source.name, feature_version=(3, 9))
                assert any(isinstance(node, ast.ImportFrom) and node.module == '__future__' and any(alias.name == 'annotations' for alias in node.names) for node in tree.body), source.name
                checked += 1
            assert checked >= 10
            return '%d Python files parsed with 3.9 syntax and future annotations' % checked
        add('Python 3.9 syntax compatibility across packaged Python files', python_39_syntax_check)

        def capabilities_views_check():
            payload, _ = cli(['capabilities', '--category', '19', '--tag', 'xr', '--limit', '5', '--format', 'json'])
            result = _assert_ok(payload, 'filtered capabilities')
            assert len(result['capability_rows']) == 5 and result['production']['stats']['capabilities'] == 330
            markdown, process = cli(['capabilities', '--category', '19', '--limit', '3', '--format', 'markdown'], expect_json=False)
            assert process.returncode == 0 and markdown.startswith('# Ultimate Game Maker capability contracts')
            csv_text, process = cli(['capabilities', '--category', '19', '--limit', '3', '--format', 'csv'], expect_json=False)
            rows = list(csv.reader(io.StringIO(csv_text)))
            assert process.returncode == 0 and rows[0][0] == 'id' and len(rows) >= 2
            return 'filtered JSON, Markdown, and CSV capability views parsed'
        add('capabilities filters and markdown/CSV rendering', capabilities_views_check)

        def describe_check():
            payload, _ = cli(['describe', '--skill', 'Save and Checkpoint Engineer', '--output', str(paths['production-describe']), '--format', 'json'])
            result = _assert_ok(payload, 'describe')
            contract = result['contract']
            assert contract['name'] == 'Save and Checkpoint Engineer' and contract['artifacts'] and contract['checks']
            saved = json.loads(paths['production-describe'].read_text(encoding='utf-8'))
            assert saved['id'] == contract['id']
            unknown, _ = cli(['describe', '--skill', 'Nonexistent Wizard', '--format', 'json'])
            _assert_error(unknown, 'UNKNOWN_CAPABILITY', 'unknown describe skill')
            return 'full contract written, parsed, and unknown skill rejected'
        add('describe production contract and unknown-capability error', describe_check)

        def request_validation_check():
            emitted, _ = cli(['example-request', '--format', 'json'])
            emitted_result = _assert_ok(emitted, 'example-request')
            assert emitted_result['template']['project']['name']
            valid, _ = cli(['validate-request', '--request', str(paths['production-request']), '--output', str(paths['production-validation-report']), '--format', 'json'])
            valid_result = _assert_ok(valid, 'validate-request')
            assert valid_result['valid'] is True and not valid_result['errors']
            saved = json.loads(paths['production-validation-report'].read_text(encoding='utf-8'))
            assert saved['valid'] is True
            invalid, _ = cli(['validate-request', '--request', str(paths['production-invalid-request']), '--format', 'json'])
            invalid_result = _assert_ok(invalid, 'invalid validate-request')
            assert invalid_result['valid'] is False and invalid_result['errors']
            missing, _ = cli(['validate-request', '--request', str(paths['production-missing-request']), '--format', 'json'])
            _assert_error(missing, 'MISSING_INPUT', 'missing request')
            return 'example template, valid/invalid/missing request paths, and written report'
        add('example-request and validate-request valid, invalid, and missing inputs', request_validation_check)

        def route_changed_input_check():
            first, _ = cli(['route', '--dimension', '2d', '--platform', 'web', '--team-size', '2', '--scope', 'prototype', '--format', 'json'])
            second, _ = cli(['route', '--dimension', '3d', '--platform', 'xbox', '--team-size', '40', '--scope', 'full-game', '--format', 'json'])
            a = _assert_ok(first, 'route web')
            b = _assert_ok(second, 'route console')
            assert a['routing']['decision'] and b['routing']['decision']
            assert json.dumps(a['routing'], sort_keys=True) != json.dumps(b['routing'], sort_keys=True)
            return 'changed dimension, platform, team, and scope changed the routing result'
        add('route command and changed-input routing result', route_changed_input_check)

        def production_plan_check():
            payload, _ = cli(['production-plan', '--request', str(paths['production-request']), '--output', str(paths['production-plan']), '--format', 'json'])
            result = _assert_ok(payload, 'production-plan')
            plan = json.loads(paths['production-plan'].read_text(encoding='utf-8'))
            assert plan['plan_id'] == result['plan_id'] and len(plan['work_items']) == result['summary']['capabilities']
            assert plan['milestones'] and plan['request'] and plan['routing']
            assert 'ultimate-game-maker/tools-game-maker.py' in json.dumps(plan['next_commands'])
            changed, _ = cli(['production-plan', '--request', str(paths['production-request']), '--project-name', 'Changed Runner', '--output', str(paths['production-plan-changed']), '--format', 'json'])
            changed_result = _assert_ok(changed, 'changed production-plan')
            changed_plan = json.loads(paths['production-plan-changed'].read_text(encoding='utf-8'))
            assert changed_plan['plan_id'] != plan['plan_id'] and changed_result['plan_id'] == changed_plan['plan_id']
            return 'plan JSON parsed, next commands resolved, and changed project name changed plan id'
        add('production-plan writes, parses, and changes with input', production_plan_check)

        def export_check():
            dry, _ = cli(['export', '--plan', str(paths['production-plan']), '--out', str(paths['production-bundle']), '--dry-run', '--format', 'json'])
            dry_result = _assert_ok(dry, 'export dry-run')
            assert dry_result['report']['dry_run'] is True and not paths['production-bundle'].exists()
            written, _ = cli(['export', '--plan', str(paths['production-plan']), '--out', str(paths['production-bundle']), '--format', 'json'])
            written_result = _assert_ok(written, 'export')
            files = sorted(path.relative_to(paths['production-bundle']).as_posix() for path in paths['production-bundle'].rglob('*') if path.is_file())
            assert len(files) == written_result['file_count'] and 'plan.json' in files and any(name.startswith('specs/') for name in files)
            for relative in files:
                content = (paths['production-bundle'] / relative).read_text(encoding='utf-8')
                if relative.endswith('.json'):
                    json.loads(content)
                elif relative.endswith('.md'):
                    assert content.strip() and '{{' not in content
            refused, _ = cli(['export', '--plan', str(paths['production-plan']), '--out', str(paths['production-bundle']), '--dry-run', '--format', 'json'])
            _assert_error(refused, 'APPROVAL_REQUIRED', 'populated export without approval')
            approved, _ = cli(['export', '--plan', str(paths['production-plan']), '--out', str(paths['production-bundle']), '--approve', '--confirm', 'APPROVE', '--format', 'json'])
            approved_result = _assert_ok(approved, 'approved export')
            assert approved_result['report']['dry_run'] is False and approved_result['report']['file_count'] == len(files)
            return 'dry run, %d parsed bundle files, approval refusal, and approved replacement' % len(files)
        add('export dry-run, bundle parsing, and approval-gated replacement', export_check)

        def evidence_gate_check():
            template_payload, _ = cli(['evidence-gate', '--plan', str(paths['production-plan']), '--template', '--output', str(paths['production-evidence']), '--format', 'json'])
            template_result = _assert_ok(template_payload, 'evidence template')
            template = json.loads(paths['production-evidence'].read_text(encoding='utf-8'))
            assert template['plan_id'] == template_result['plan_id'] and template['checks']
            missing, _ = cli(['evidence-gate', '--plan', str(paths['production-plan']), '--evidence', str(paths['production-evidence']), '--profile', 'milestone', '--format', 'json'])
            missing_result = _assert_ok(missing, 'missing evidence gate')
            assert missing_result['report']['ok'] is False and missing_result['report']['counts']['missing'] > 0
            for check_id, entry in template['checks'].items():
                entry['status'] = 'pass'
                entry['evidence'] = 'captured proof for %s: log, build id, and timestamp recorded' % check_id
            for action in template.get('approvals_required', []):
                template.setdefault('approvals', []).append({'action': action, 'approver': 'release owner', 'timestamp': '2026-11-05T00:00:00+00:00', 'note': 'reviewed locally'})
            paths['production-filled-evidence'].write_text(json.dumps(template, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            passing, _ = cli(['evidence-gate', '--plan', str(paths['production-plan']), '--evidence', str(paths['production-filled-evidence']), '--profile', 'release', '--format', 'json'])
            passing_result = _assert_ok(passing, 'release evidence gate')
            assert passing_result['report']['ok'] is True and passing_result['report']['counts']['pass'] > 0
            return 'template, blocked missing evidence, filled evidence, and passing release gate'
        add('evidence-gate template, missing evidence block, and passing release gate', evidence_gate_check)

        def doctor_check():
            payload, _ = cli(['doctor', '--format', 'json'])
            result = _assert_ok(payload, 'doctor')
            assert result['ok'] is True and result['production']['capabilities'] == 330
            assert result['python_39_syntax']['compatible'] is True and result['python_39_syntax']['all_future_annotations'] is True
            assert result['package']['file_count'] >= 20 and result['references_resolved'] >= 20
            return 'doctor reports package, %d references, 330 capabilities, and 3.9 syntax' % result['references_resolved']
        add('doctor environment, references, assets, and Python syntax health', doctor_check)

        def clean_root_check():
            clean_root = paths['clean-root']
            clean_root.mkdir()
            shutil.copytree(tool_dir, clean_root / common.TOOL_FOLDER)
            (clean_root / 'SKILL.md').write_text('# Test skill root\n', encoding='utf-8')
            all_files = [path for path in clean_root.rglob('*') if path.is_file()]
            assert len(all_files) <= 500
            assert all(len(path.relative_to(clean_root).parts) <= 3 for path in all_files)
            skill_files = [path for path in clean_root.rglob('SKILL.md')]
            assert len(skill_files) == 1 and skill_files[0].parent == clean_root
            assert _check_references(clean_root / common.TOOL_FOLDER, clean_root) >= 10
            payload, _ = _run_cli(['capabilities', '--format', 'json'], config, clean_root, tool_dir, True, clean_root, 'ultimate-game-maker/tools-game-maker.py')
            result = _assert_ok(payload, 'clean-root capabilities')
            assert result['handler_count'] == 33
            doctor, _ = _run_cli(['doctor', '--format', 'json'], config, clean_root, tool_dir, True, clean_root, 'ultimate-game-maker/tools-game-maker.py')
            doctor_result = _assert_ok(doctor, 'clean-root doctor')
            assert doctor_result['production']['capabilities'] == 330
            described, _ = _run_cli(['describe', '--skill', 'Game Creative Director', '--format', 'json'], config, clean_root, tool_dir, True, clean_root, 'ultimate-game-maker/tools-game-maker.py')
            assert _assert_ok(described, 'clean-root describe')['contract']['name'] == 'Game Creative Director'
            launcher = _run_process(['sh', 'ultimate-game-maker/tools-verify.sh', 'doctor'], clean_root,
                                    float(config['selftest']['cli_timeout_seconds']),
                                    int(config['selftest']['max_process_output_bytes']))
            assert launcher.returncode == 0 and 'verify: all checks passed' in launcher.stdout
            assert not list((clean_root / common.TOOL_FOLDER).glob('__pycache__'))
            return 'one root SKILL.md, <=500 files, <=3 path levels, relative references, doctor, describe, and POSIX launcher'
        add('clean temporary skill root compatibility', clean_root_check)

        def node_harness_check():
            if config['selftest'].get('skip_node'):
                warnings.append('Node logic harness skipped by config.')
                return ('skipped', 'selftest.skip_node=true')
            node = shutil.which('node')
            if not node:
                warnings.append('Node was not available; the browser game logic was not executed.')
                return ('skipped', 'node executable not found')
            html = (paths['plan1'] / 'prototype' / 'index.html').read_text(encoding='utf-8')
            harness = prototype.build_node_harness(html)
            harness_path = root / 'node-logic-harness.js'
            harness_path.write_text(harness, encoding='utf-8')
            timeout = float(config['selftest']['node_timeout_seconds'])
            max_bytes = int(config['selftest']['max_process_output_bytes'])
            syntax = _run_process([node, '--check', str(harness_path)], root, timeout, max_bytes)
            if syntax.returncode != 0:
                raise AssertionError(f'node --check failed: {syntax.stderr[:300]}')
            executed = _run_process([node, str(harness_path)], root, timeout, max_bytes)
            if executed.returncode != 0:
                raise AssertionError(f'node harness failed: {executed.stderr[:300]} or {executed.stdout[:300]}')
            outcome = json.loads(executed.stdout)
            assert outcome['passed'] is True and len(outcome['checks']) >= 5
            return 'node --check plus bounded VM execution of deterministic game logic'
        add('optional Node execution of generated browser game logic', node_harness_check)

    passed_count = sum(1 for item in checks if item['status'] == 'passed')
    failed_count = sum(1 for item in checks if item['status'] == 'failed')
    skipped_count = sum(1 for item in checks if item['status'] == 'skipped')
    report.update({
        'passed': failed_count == 0,
        'passed_count': passed_count,
        'failed_count': failed_count,
        'skipped_count': skipped_count,
        'check_count': len(checks),
    })
    return report
