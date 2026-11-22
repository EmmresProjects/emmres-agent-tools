#!/usr/bin/env python3
"""Primary CLI for the unified, combined Ultimate Game Maker Skill Tool.

Run from the future skill root:
    python3 ultimate-game-maker/tools-game-maker.py --help
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location('ugm_common', HERE / 'tools-common.py')
if _spec is None or _spec.loader is None:
    raise RuntimeError('Unable to load tools-common.py')
common = importlib.util.module_from_spec(_spec)
sys.modules['ugm_common'] = common
_spec.loader.exec_module(common)
modules = common.bootstrap_modules(HERE)
validation = modules.validation
registry = modules.registry
planner = modules.planner
capability_packets = modules.capability_packets
source_parser = modules.source_parser
docs = modules.docs
prototype = modules.prototype
production = modules.production_engine

COMMANDS = [
    {'name': 'help', 'purpose': 'Show built-in command help.'},
    {'name': 'version', 'purpose': 'Report the Skill Tool version, runtime contract, and supported formats.'},
    {'name': 'capabilities', 'purpose': 'List all 33 category handlers and 330 unified specialists, with filters and markdown/CSV views.'},
    {'name': 'catalog', 'purpose': 'Inspect exact category, handler, skill, and description data.'},
    {'name': 'parse-source', 'purpose': 'Normalize a capability, numbered list, one category, or several categories from text, Markdown, or copied HTML.'},
    {'name': 'example', 'purpose': 'Emit or write the working example game brief.'},
    {'name': 'validate', 'purpose': 'Validate a game brief against the reusable schema.'},
    {'name': 'plan', 'purpose': 'Generate the complete unified editable production handoff and optional HTML5 prototype.'},
    {'name': 'capability', 'purpose': 'Generate one distinct capability packet for any of the 330 named specialists.'},
    {'name': 'docs', 'purpose': 'Extract bounded version evidence from user-supplied local official documentation.'},
    {'name': 'prototype', 'purpose': 'Generate and audit one self-contained HTML5 Canvas game file.'},
    {'name': 'gate', 'purpose': 'Record an explicit human-approval receipt without performing the external action.'},
    {'name': 'gate-list', 'purpose': 'List destructive, privileged, publishing, financial, credential, and security-sensitive approval gates.'},
    {'name': 'recover', 'purpose': 'Regenerate missing outputs from a recovery state while preserving passing files.'},
    {'name': 'describe', 'purpose': 'Render one complete production contract with artifacts, checks, risks, parameters, and prerequisites.'},
    {'name': 'route', 'purpose': 'Score all 12 engine routes against a validated production request.'},
    {'name': 'production-plan', 'purpose': 'Build a deterministic 33/330 contract-driven plan with milestones, risks, and next commands.'},
    {'name': 'validate-request', 'purpose': 'Validate a production request against the local JSON Schema subset, including errors and warnings.'},
    {'name': 'export', 'purpose': 'Export an editable plan, checklist, handoff, evidence template, and per-capability specs.'},
    {'name': 'evidence-gate', 'purpose': 'Create an evidence template or evaluate draft, milestone, and release gates.'},
    {'name': 'doctor', 'purpose': 'Check environment, package hygiene, references, production assets, and Python 3.9 syntax.'},
    {'name': 'example-request', 'purpose': 'Emit or write the working production request template.'},
    {'name': 'verify', 'purpose': 'Run doctor plus the full real-interface selftest in one bounded command.'},
    {'name': 'selftest', 'purpose': 'Run real-interface, output-parsing, production-contract, evidence-gate, Node-logic, Python 3.9 syntax, and recovery validation.'},
]


class StructuredArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        payload = common.error_payload(common.ToolError('INVALID_ARGUMENTS', message, [message], ['Run --help for the exact command syntax.']))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        raise SystemExit(2)


def build_parser() -> StructuredArgumentParser:
    parser = StructuredArgumentParser(
        prog='python3 ultimate-game-maker/tools-game-maker.py',
        description='Unified Ultimate Game Maker Skill Tool: 33 category handlers, 330 named specialists, production contracts, routing, evidence gates, and editable bundle export in one reusable component.',
        allow_abbrev=False,
    )
    common_parent = argparse.ArgumentParser(add_help=False)
    common_parent.add_argument('--config', help='Optional JSON config override.')
    common_parent.add_argument('--set', action='append', default=[], metavar='KEY=VALUE', help='Override one documented dotted config key; repeatable.')
    common_parent.add_argument('--timeout-seconds', type=float, help='Override runtime.deadline_seconds for this run.')
    common_parent.add_argument('--format', choices=['json', 'text', 'markdown', 'csv'], help='Structured JSON, readable text, Markdown, or CSV output; defaults to config.')
    common_parent.add_argument('--confirm', help='Explicit approval token for approved actions.')
    common_parent.add_argument('--approve-overwrite', action='store_true', help='Allow replacement of existing output files; requires --confirm.')
    common_parent.add_argument('--dry-run', action='store_true', help='Validate and render without writing files.')

    resource_parent = argparse.ArgumentParser(add_help=False)
    resource_parent.add_argument('--catalog', help='Override ultimate-game-maker/asset-catalog.json.')
    resource_parent.add_argument('--engine-profiles', help='Override ultimate-game-maker/asset-engine-profiles.json.')
    resource_parent.add_argument('--brief-schema', help='Override ultimate-game-maker/asset-brief-schema.json.')
    resource_parent.add_argument('--template', help='Override ultimate-game-maker/asset-production-template.md.')
    resource_parent.add_argument('--html5-template', help='Override ultimate-game-maker/asset-html5-prototype.html.')

    brief_parent = argparse.ArgumentParser(add_help=False)
    brief_parent.add_argument('--brief', help='Path to a game brief JSON file.')
    brief_parent.add_argument('--engine', help='Override brief.engine for this run.')
    brief_parent.add_argument('--language', help='Override brief.language for this run.')
    brief_parent.add_argument('--platform', help='Override brief.target_platform for this run.')
    brief_parent.add_argument('--engine-version', help='Override brief.engine_version for this run.')
    brief_parent.add_argument('--seed', type=int, help='Override brief.seed for this run.')

    sub = parser.add_subparsers(dest='command', required=True, metavar='COMMAND')
    sub.add_parser('help', parents=[common_parent], help='Show built-in command help.')
    sub.add_parser('version', parents=[common_parent], help='Report version and runtime contract.')

    p = sub.add_parser('capabilities', parents=[common_parent, resource_parent], help='List the capability set and commands.')
    p.add_argument('--detailed', action='store_true', help='Include all 330 specialist names and focus lines.')
    p.add_argument('--skill', action='append', default=[], help='Repeatable exact capability name, slug, or id.')
    p.add_argument('--category', action='append', default=[], help='Repeatable category number, name, slug, or unique substring.')
    p.add_argument('--phase', action='append', default=[], help='Repeatable phase selector, for example 1 or phase-1.')
    p.add_argument('--tag', action='append', default=[], help='Repeatable contract tag selector.')
    p.add_argument('--exclude', action='append', default=[], help='Repeatable capability selector to subtract.')
    p.add_argument('--search', help='Free-text search across capability names, categories, and descriptions.')
    p.add_argument('--limit', type=int, help='Maximum number of selected capabilities to return.')

    p = sub.add_parser('catalog', parents=[common_parent, resource_parent], help='Inspect exact catalog data.')
    p.add_argument('--category', help='Filter by exact category name, slug, or handler ID.')
    p.add_argument('--skill', help='Return one exact specialist by name or slug.')
    p.add_argument('--summary', action='store_true', help='Omit full skill arrays for a compact view.')

    p = sub.add_parser('parse-source', parents=[common_parent], help='Parse a capability list from text, Markdown, HTML, or stdin.')
    p.add_argument('--source', required=True, help='Source file path, or - for stdin.')
    p.add_argument('--umbrella', help='Override the derived lowercase hyphenated umbrella slug.')
    p.add_argument('--output', help='Write the normalized JSON manifest to this file.')

    p = sub.add_parser('example', parents=[common_parent, resource_parent], help='Emit the working example brief.')
    p.add_argument('--output', help='Write the example brief to this JSON file.')

    p = sub.add_parser('validate', parents=[common_parent, resource_parent, brief_parent], help='Validate a game brief.')
    p.set_defaults(brief_required=True)

    p = sub.add_parser('plan', parents=[common_parent, resource_parent, brief_parent], help='Generate the unified production handoff.')
    p.add_argument('--output', help='Output directory; defaults to storage.output_root.')
    p.add_argument('--categories', help='Comma-separated category names, slugs, or handler IDs; unioned with brief.categories.')
    p.add_argument('--skills', help='Comma-separated specialist names or slugs; unioned with brief.skills.')
    p.add_argument('--docs', nargs='*', default=[], help='Local UTF-8 official documentation files to ingest.')
    p.add_argument('--no-prototype', action='store_true', help='Skip the self-contained HTML5 prototype.')
    p.set_defaults(brief_required=True)

    p = sub.add_parser('capability', parents=[common_parent, resource_parent, brief_parent], help='Render one specialist capability packet.')
    p.add_argument('--skill', required=True, help='Exact specialist name or slug.')
    p.add_argument('--output', help='Write the packet JSON to this file.')

    p = sub.add_parser('docs', parents=[common_parent, resource_parent, brief_parent], help='Inspect local official documentation evidence.')
    p.add_argument('--docs', nargs='+', required=True, help='One or more local UTF-8 documentation files.')
    p.add_argument('--output', help='Write the evidence JSON to this file.')
    p.set_defaults(brief_required=True)

    p = sub.add_parser('prototype', parents=[common_parent, resource_parent, brief_parent], help='Generate one self-contained HTML5 prototype file.')
    p.add_argument('--output', required=True, help='Write the generated HTML file to this path.')
    p.set_defaults(brief_required=True)

    p = sub.add_parser('gate', parents=[common_parent], help='Record a human approval receipt.')
    p.add_argument('--action', required=True, help='Approval-gated action name; see gate-list.')
    p.add_argument('--reason', required=True, help='Human review reason of at least 5 characters.')
    p.add_argument('--output', help='Write the approval receipt JSON to this file.')

    p = sub.add_parser('gate-list', parents=[common_parent, resource_parent, brief_parent], help='List approval gates.')

    p = sub.add_parser('recover', parents=[common_parent, resource_parent, brief_parent], help='Regenerate missing outputs from recovery state.')
    p.add_argument('--state', required=True, help='Path to recovery-state.json.')
    p.add_argument('--output', help='Override the recovered output directory.')
    p.add_argument('--replace-existing', action='store_true', help='Replace existing files; requires --confirm.')
    p.set_defaults(brief_required=True)

    p = sub.add_parser('selftest', parents=[common_parent], help='Run the built-in validation suite.')
    p.add_argument('--verbose', action='store_true', help='Include every check name in text output.')
    p.add_argument('--skip-node', action='store_true', help='Skip the optional Node logic harness.')

    request_parent = argparse.ArgumentParser(add_help=False)
    request_parent.add_argument('--project-name', help='Override project.name in the production request.')
    request_parent.add_argument('--genre', help='Override project.genre.')
    request_parent.add_argument('--goal', help='Override project.goal.')
    request_parent.add_argument('--engine', help='Override project.engine.')
    request_parent.add_argument('--engine-version', help='Override project.engine_version.')
    request_parent.add_argument('--language', help='Override project.language.')
    request_parent.add_argument('--dimension', help='Override project.dimension.')
    request_parent.add_argument('--scope', help='Override project.scope.')
    request_parent.add_argument('--team-size', type=int, help='Override project.team_size.')
    request_parent.add_argument('--weeks', type=int, help='Override project.weeks.')
    request_parent.add_argument('--target-fps', type=int, help='Override project.target_fps.')
    request_parent.add_argument('--platform', action='append', default=[], help='Repeatable requested platform.')
    request_parent.add_argument('--constraint', action='append', default=[], help='Repeatable project constraint.')
    request_parent.add_argument('--acceptance', action='append', default=[], help='Repeatable acceptance criterion.')
    request_parent.add_argument('--skills', help='Comma-separated capability names, slugs, or ids.')
    request_parent.add_argument('--categories', help='Comma-separated category numbers, names, or slugs.')
    request_parent.add_argument('--phases', help='Comma-separated phase selectors.')
    request_parent.add_argument('--tags', help='Comma-separated contract tags.')
    request_parent.add_argument('--exclude', action='append', default=[], help='Repeatable production-request exclusion.')
    request_parent.add_argument('--limit', type=int, help='Override capabilities.limit.')
    request_parent.add_argument('--out-dir', help='Override output.dir in the production request.')

    p = sub.add_parser('describe', parents=[common_parent], help='Render one complete production contract.')
    p.add_argument('--skill', required=True, help='Exact capability name, slug, or id.')
    p.add_argument('--output', help='Write the contract JSON, Markdown, CSV, or text to this file.')

    p = sub.add_parser('route', parents=[common_parent, request_parent], help='Score all 12 engine routes.')
    p.add_argument('--request', help='Production request JSON file, or - for stdin; defaults to the packaged template.')
    p.add_argument('--output', help='Write the routing result to this file.')

    p = sub.add_parser('production-plan', parents=[common_parent, request_parent], help='Build a deterministic contract-driven production plan.')
    p.add_argument('--request', help='Production request JSON file, or - for stdin; defaults to the packaged template.')
    p.add_argument('--output', help='Write the plan JSON, Markdown, CSV, or text to this file.')

    p = sub.add_parser('validate-request', parents=[common_parent, request_parent], help='Validate a production request.')
    p.add_argument('--request', required=True, help='Production request JSON file, or - for stdin.')
    p.add_argument('--output', help='Write the validation report to this file.')

    p = sub.add_parser('export', parents=[common_parent, request_parent], help='Export an editable production bundle.')
    p.add_argument('--request', help='Production request JSON file, or - for stdin; used when --plan is absent.')
    p.add_argument('--plan', help='Existing production-plan JSON to export.')
    p.add_argument('--out', required=True, help='Output directory for the bundle.')
    p.add_argument('--profile', choices=['draft', 'milestone', 'release'], default='milestone', help='Evidence gate profile.')
    p.add_argument('--no-specs', action='store_true', help='Omit per-capability spec files.')
    p.add_argument('--approve', action='store_true', help='Allow replacement of a populated output directory; requires --confirm.')

    p = sub.add_parser('evidence-gate', parents=[common_parent], help='Create or evaluate a local evidence gate.')
    p.add_argument('--plan', required=True, help='Production-plan JSON to gate.')
    gate_mode = p.add_mutually_exclusive_group(required=True)
    gate_mode.add_argument('--template', action='store_true', help='Emit a missing-evidence template.')
    gate_mode.add_argument('--evidence', help='Filled evidence JSON to evaluate.')
    p.add_argument('--profile', choices=['draft', 'milestone', 'release'], default='milestone', help='Evidence gate profile.')
    p.add_argument('--output', help='Write the template or report to this file.')

    sub.add_parser('doctor', parents=[common_parent], help='Check environment, package hygiene, references, and assets.')

    p = sub.add_parser('example-request', parents=[common_parent], help='Emit the working production request template.')
    p.add_argument('--output', help='Write the request template to this JSON file.')

    p = sub.add_parser('verify', parents=[common_parent], help='Run doctor plus the full selftest.')
    p.add_argument('--verbose', action='store_true', help='Retained for compatibility with the verification launcher.')
    p.add_argument('--skip-node', action='store_true', help='Skip the optional bounded Node logic harness.')

    return parser


PARSER = build_parser()


def prepare(args):
    skill_root = common.find_skill_root()
    config = validation.load_config(HERE, getattr(args, 'config', None), getattr(args, 'set', []), skill_root)
    if getattr(args, 'timeout_seconds', None) is not None:
        config = common.apply_overrides(config, [f'runtime.deadline_seconds={args.timeout_seconds}'])
        validation.validate_config(config)
    if getattr(args, 'skip_node', False):
        config = common.apply_overrides(config, ['selftest.skip_node=true'])
        validation.validate_config(config)
    deadline = common.Deadline(config['runtime']['deadline_seconds'])
    output_format = getattr(args, 'format', None) or config['runtime']['default_format']
    return config, deadline, output_format, skill_root


def options_from_args(args, config: dict, skill_root: Path | None = None) -> dict:
    return {
        'categories': common.parse_csv(getattr(args, 'categories', None)),
        'skills': common.parse_csv(getattr(args, 'skills', None)),
        'engine_override': getattr(args, 'engine', None),
        'language_override': getattr(args, 'language', None),
        'platform_override': getattr(args, 'platform', None),
        'version_override': getattr(args, 'engine_version', None),
        'seed_override': getattr(args, 'seed', None),
        'docs_paths': [str(item) for item in (getattr(args, 'docs', []) or [])],
        'include_prototype': not getattr(args, 'no_prototype', False),
        'template': getattr(args, 'template', None),
        'html5_template': getattr(args, 'html5_template', None),
        'catalog': getattr(args, 'catalog', None),
        'engine_profiles': getattr(args, 'engine_profiles', None),
        'brief_schema': getattr(args, 'brief_schema', None),
        'output_root': getattr(args, 'output', None) or config['storage']['output_root'],
        'skill_root': skill_root,
    }


def load_resources(args, config: dict, deadline: common.Deadline, skill_root: Path,
                   need_catalog=True, need_profiles=True, need_schema=True) -> dict:
    resources = {'skill_root': skill_root}
    if need_catalog:
        resources['catalog'] = registry.load_catalog(config, deadline, getattr(args, 'catalog', None), skill_root)
    if need_profiles:
        resources['profiles'] = registry.load_engine_profiles(config, deadline, getattr(args, 'engine_profiles', None), skill_root)
    if need_schema:
        resources['schema'] = validation.load_schema(config, getattr(args, 'brief_schema', None), skill_root)
    return resources


def load_brief_or_template(args, config: dict, schema: dict, skill_root: Path) -> tuple[dict, str]:
    brief = getattr(args, 'brief', None)
    if not brief:
        if getattr(args, 'brief_required', False):
            raise common.ToolError('MISSING_BRIEF', 'This command requires --brief.', [], ['Provide a game brief JSON file or run example --output brief.json.'])
        ref = config['paths']['brief_template']
        path = common.resolve_reference(ref, skill_root)
        value = common.load_json_file(path, int(config['limits']['max_brief_bytes']), 'asset-brief-template.json', config)
        validation.validate_brief(value, schema, config)
        return value, ref
    value = validation.load_brief(str(brief), config, schema, skill_root)
    return value, str(brief)


def _warning_lines(items) -> list[str]:
    return ['%s: %s' % (item.get('path', '$'), item.get('message', '')) if isinstance(item, dict) else str(item) for item in items]


def _engine(config: dict, deadline: common.Deadline, skill_root: Path):
    return production.build_engine(config, deadline, skill_root, HERE)


def _write_result(args, config: dict, deadline: common.Deadline, result: dict,
                  output_format: str, payload) -> None:
    result['output'] = None
    if not getattr(args, 'output', None):
        return
    validation.validate_overwrite_flag(args, config)
    if getattr(args, 'dry_run', False):
        result['output'] = {'written': False, 'dry_run': True}
        return
    if output_format == 'markdown':
        content = result.get('markdown') or json.dumps(payload, indent=2, ensure_ascii=False)
    elif output_format == 'csv':
        content = result.get('csv') or json.dumps(payload, indent=2, ensure_ascii=False)
    elif output_format == 'text':
        content = result.get('text') or json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        content = json.dumps(payload, indent=2, ensure_ascii=False)
    result['output'] = common.write_single_output(args.output, content.rstrip('\n') + '\n', config, deadline, args.approve_overwrite)


def effective_brief_and_route(brief: dict, options: dict, profiles: dict, config: dict, schema: dict) -> tuple[dict, dict]:
    effective = planner.apply_brief_overrides(brief, options)
    validation.validate_brief(effective, schema, config)
    route = registry.route_engine(effective, profiles, options.get('engine_override'))
    return effective, route


def render_success_text(command: str, result: dict, output_format: str = 'text') -> str:
    if output_format == 'markdown' and isinstance(result.get('markdown'), str):
        return result['markdown']
    if output_format == 'csv' and isinstance(result.get('csv'), str):
        return result['csv']
    if output_format == 'text' and isinstance(result.get('text'), str):
        return result['text']
    if command == 'capabilities':
        lines = [f"Umbrella: {result['umbrella']['slug']}", f"Handlers: {result['handler_count']}", f"Specialists: {result['skill_count']}", '', 'Commands:']
        lines.extend(f"  {item['name']}: {item['purpose']}" for item in result.get('commands', []))
        lines.extend(['', 'Handlers:'])
        lines.extend(f"  {item['id']} | {item['operation']} | {item['category']} | {item['skill_count']} specialists" for item in result['handlers'])
        if result.get('handlers') and result['handlers'][0].get('skills'):
            lines.extend(['', 'Detailed specialists:'])
            for handler in result['handlers']:
                for skill in handler.get('skills', []):
                    lines.append(f"  {handler['id']} | {skill['slug']} | {skill['focus']}")
        return '\n'.join(lines)
    if command == 'capability':
        return result.get('markdown', json.dumps(result, indent=2, ensure_ascii=False))
    if command == 'catalog':
        if 'skill' in result:
            return f"{result['skill']['name']} ({result['skill']['slug']})\n{result['skill']['description']}\nFocus: {result['skill']['focus']}"
        if 'handler' in result:
            return json.dumps(result['handler'], indent=2, ensure_ascii=False)
        return json.dumps(result, indent=2, ensure_ascii=False)
    if command == 'plan':
        summary = result['summary']
        output = result['output']
        lines = [
            f"Plan: {summary['umbrella']} | {summary['handler_count']} handlers | {summary['skill_count']} specialists | {summary['packet_count']} packets",
            f"Engine: {summary['engine']} | profile {summary['profile_id']} | version {summary['version_status']}",
            f"Files: {summary['file_count']} | bytes: {summary['total_bytes']} | prototype: {summary['prototype_included']} | local docs: {summary['local_docs_included']}",
            f"Output root: {result['output_root']} | written: {output['written']} | dry run: {output['dry_run']}",
        ]
        lines.extend(f"  {name}: {size} bytes" for name, size in output['files'].items())
        return '\n'.join(lines)
    if command == 'parse-source':
        parsed = result['parsed_source']
        return '\n'.join([
            f"Umbrella: {parsed['umbrella']['slug']} ({parsed['umbrella']['name']})",
            f"Source format: {parsed['source_format']}",
            f"Categories: {parsed['category_count']} | capabilities: {parsed['capability_count']}",
            f"Normalization: {json.dumps(parsed['normalization'], sort_keys=True)}",
        ])
    if command == 'selftest':
        return '\n'.join([
            f"Selftest passed: {result['passed']} | passed checks: {result['passed_count']} | failed: {result['failed_count']} | skipped: {result['skipped_count']}",
            *[f"  {check}" for check in result.get('checks', [])],
        ])
    if command in ('docs', 'prototype', 'gate', 'recover', 'validate', 'example', 'version', 'gate-list'):
        lines = []
        for key, value in result.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                lines.append(f'{key}: {value}')
            elif isinstance(value, list) and len(value) <= 12 and all(not isinstance(item, (dict, list)) for item in value):
                lines.append(f'{key}: {", ".join(str(item) for item in value)}')
            elif isinstance(value, dict):
                lines.append(f'{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)[:800]}')
            else:
                lines.append(f'{key}: <{type(value).__name__}, {len(value)} items>')
        return '\n'.join(lines)
    return json.dumps(result, indent=2, ensure_ascii=False)


def render_error_text(payload: dict) -> str:
    error = payload['error']
    lines = [f"ERROR {error['code']}: {error['message']}"]
    if error['details']:
        lines.append('Details:')
        lines.extend(f'  - {item}' for item in error['details'][:20])
    if error['recovery']:
        lines.append('Recovery:')
        lines.extend(f'  - {item}' for item in error['recovery'])
    return '\n'.join(lines)


def emit(payload: dict, output_format: str) -> None:
    if output_format == 'json':
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif payload.get('ok'):
        print(render_success_text(payload.get('command') or '', payload.get('result', {}), output_format))
    else:
        print(render_error_text(payload))


def command_help(args) -> tuple[dict, list[str]]:
    PARSER.print_help()
    raise SystemExit(0)


def command_version(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    return {
        'version': common.VERSION,
        'umbrella': 'ultimate-game-maker',
        'primary_entry_point': 'ultimate-game-maker/tools-game-maker.py',
        'python': sys.version.split()[0],
        'minimum_python': config['runtime']['minimum_python'],
        'standard_library_only': True,
        'supported_formats': ['json', 'text', 'markdown', 'csv'],
        'production_engine': 'unified-contract-routing-evidence-v2',
        'command_count': len(COMMANDS),
        'commands': [item['name'] for item in COMMANDS],
    }, []


def command_capabilities(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    resources = load_resources(args, config, deadline, skill_root, need_profiles=False, need_schema=False)
    catalog = resources['catalog']
    result = registry.capability_list(catalog, detailed=args.detailed)
    result['commands'] = COMMANDS
    result['command_count'] = len(COMMANDS)
    engine = _engine(config, deadline, skill_root)
    production_view = engine.capabilities(args, output_format)
    result['production'] = {
        'selected_count': production_view['selected_count'],
        'filters': production_view['filters'],
        'has_filters': production_view['has_filters'],
        'stats': production_view['stats'],
    }
    if production_view['has_filters'] or output_format in ('markdown', 'csv'):
        result['capability_rows'] = production_view['capabilities']
        result['markdown'] = production_view['markdown']
        result['csv'] = production_view['csv']
        result['text'] = production_view['text']
    return result, []


def command_catalog(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    resources = load_resources(args, config, deadline, skill_root, need_profiles=False, need_schema=False)
    result = registry.catalog_view(resources['catalog'], args.category, args.skill, detailed=not args.summary)
    return result, []


def command_parse_source(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    validation.validate_overwrite_flag(args, config)
    if args.source == '-':
        raw = sys.stdin.read()
        max_bytes = int(config['limits']['max_source_bytes'])
        if len(raw.encode('utf-8')) > max_bytes:
            raise common.ToolError('INPUT_TOO_LARGE', 'stdin source exceeds the configured byte limit.', [len(raw.encode('utf-8'))])
        parsed = source_parser.parse_source_text(raw, args.umbrella)
    else:
        parsed = source_parser.parse_source_file(Path(args.source), config, deadline, args.umbrella)
    payload = json.dumps(parsed, indent=2, ensure_ascii=False) + '\n'
    result = {'parsed_source': parsed, 'output': None}
    if args.output:
        result['output'] = common.write_single_output(args.output, payload, config, deadline, args.approve_overwrite)
    return result, list(parsed.get('warnings', []))


def command_example(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    validation.validate_overwrite_flag(args, config)
    resources = load_resources(args, config, deadline, skill_root, need_catalog=False, need_profiles=False, need_schema=True)
    brief, source = load_brief_or_template(args, config, resources['schema'], skill_root)
    payload = json.dumps(brief, indent=2, ensure_ascii=False) + '\n'
    result = {'brief_source': source, 'brief': brief, 'output': None}
    if args.output:
        result['output'] = common.write_single_output(args.output, payload, config, deadline, args.approve_overwrite)
    return result, []


def command_validate(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    resources = load_resources(args, config, deadline, skill_root, need_catalog=False, need_profiles=False, need_schema=True)
    brief, source = load_brief_or_template(args, config, resources['schema'], skill_root)
    return {
        'valid': True,
        'brief_source': source,
        'required_fields': resources['schema']['required'],
        'acceptance_criteria_count': len(brief.get('acceptance_criteria', [])),
        'engine': brief.get('engine'),
        'language': brief.get('language'),
        'target_platform': brief.get('target_platform'),
        'seed': brief.get('seed', 0),
    }, []


def command_plan(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    validation.validate_overwrite_flag(args, config)
    resources = load_resources(args, config, deadline, skill_root)
    brief, brief_source = load_brief_or_template(args, config, resources['schema'], skill_root)
    options = options_from_args(args, config, skill_root)
    options['output_root'] = args.output or config['storage']['output_root']
    if options['docs_paths']:
        validation.validate_doc_paths(options['docs_paths'], config, deadline)
    rendered = planner.render_outputs(brief, config, resources['catalog'], resources['profiles'], deadline, options)
    output = planner.write_outputs(rendered['files'], options['output_root'], config, deadline,
                                   approve_overwrite=args.approve_overwrite, dry_run=args.dry_run)
    warnings = []
    if rendered['route']['version_status'] != 'complete':
        warnings.append('Exact version evidence is incomplete; supply local official documentation before an external build.')
    if rendered['route']['external_docs_required']:
        warnings.append('The routed engine profile requires supplied official documentation; no external compiler is bundled.')
    if not rendered['summary']['prototype_included']:
        warnings.append('No runnable prototype was generated for this engine profile; planning artifacts and external build checklists were produced.')
    return {
        'brief_source': brief_source,
        'summary': rendered['summary'],
        'output_root': options['output_root'],
        'output': output,
        'recovery_state': str(Path(options['output_root']) / 'recovery-state.json'),
        'version_status': rendered['route']['version_status'],
        'missing_version_evidence': rendered['route']['missing_version_evidence'],
        'selected_operations': [handler['operation'] for handler in rendered['selection']['handlers']],
        'prototype_report': rendered['prototype_report'],
    }, warnings


def command_capability(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    validation.validate_overwrite_flag(args, config)
    resources = load_resources(args, config, deadline, skill_root)
    brief, brief_source = load_brief_or_template(args, config, resources['schema'], skill_root)
    options = options_from_args(args, config, skill_root)
    effective, route = effective_brief_and_route(brief, options, resources['profiles'], config, resources['schema'])
    handler, skill = registry.resolve_skill(resources['catalog'], args.skill)
    factory = capability_packets.CapabilityPacketFactory(config, deadline)
    packet = factory.render(handler, skill, effective, route, 1)
    payload = json.dumps(packet, indent=2, ensure_ascii=False) + '\n'
    result = {
        'brief_source': brief_source,
        'handler': packet['handler'],
        'skill': {'name': skill['name'], 'slug': skill['slug'], 'focus': skill['focus']},
        'packet': packet,
        'markdown': capability_packets.render_packet_text(packet),
        'output': None,
    }
    if args.output:
        result['output'] = common.write_single_output(args.output, payload, config, deadline, args.approve_overwrite)
    return result, []


def command_docs(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    validation.validate_overwrite_flag(args, config)
    resources = load_resources(args, config, deadline, skill_root, need_catalog=False, need_profiles=True, need_schema=True)
    brief, brief_source = load_brief_or_template(args, config, resources['schema'], skill_root)
    options = options_from_args(args, config, skill_root)
    effective, route = effective_brief_and_route(brief, options, resources['profiles'], config, resources['schema'])
    paths = validation.validate_doc_paths(args.docs, config, deadline)
    evidence = docs.ingest(paths, effective, route, config, deadline)
    payload = docs.render_docs_json(evidence)
    result = {'brief_source': brief_source, 'evidence': evidence, 'output': None}
    if args.output:
        result['output'] = common.write_single_output(args.output, payload, config, deadline, args.approve_overwrite)
    warnings = []
    if evidence['status'] != 'complete-local-evidence':
        warnings.append('Local evidence did not contain every exact version string from the brief.')
    warnings.append('The tool cannot verify that user-supplied files are official; review provenance manually.')
    return result, warnings


def command_prototype(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    validation.validate_overwrite_flag(args, config)
    resources = load_resources(args, config, deadline, skill_root, need_catalog=False, need_profiles=True, need_schema=True)
    brief, brief_source = load_brief_or_template(args, config, resources['schema'], skill_root)
    options = options_from_args(args, config, skill_root)
    effective, route = effective_brief_and_route(brief, options, resources['profiles'], config, resources['schema'])
    html, report = prototype.render_prototype(effective, config, route, deadline, args.html5_template, skill_root)
    output = common.write_single_output(args.output, html, config, deadline, args.approve_overwrite)
    return {
        'brief_source': brief_source,
        'engine': route['requested_engine'],
        'profile_id': route['profile']['id'],
        'report': report,
        'output': output,
    }, ['Interactive browser rendering is user-side; generation parsed and audited the HTML and exposed pure logic.']


def command_gate(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    validation.validate_approval(args, config, args.action, args.reason)
    validation.validate_overwrite_flag(args, config)
    receipt = {
        'schema_id': 'ugm-approval-receipt-1.0',
        'action': args.action,
        'reason': args.reason.strip(),
        'reviewed_at_utc': common.utc_now(),
        'approved_by': 'explicit-cli-confirmation',
        'execution': 'not-performed',
        'external_execution': False,
        'note': 'This receipt records human review only; it does not publish, deploy, delete, purchase, or change credentials.',
    }
    payload = json.dumps(receipt, indent=2, ensure_ascii=False) + '\n'
    result = {'receipt': receipt, 'output': None}
    if args.output:
        result['output'] = common.write_single_output(args.output, payload, config, deadline, args.approve_overwrite)
    return result, ['The external action was not executed; perform it separately only after platform-side review.']


def command_gate_list(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    brief_gates = []
    if getattr(args, 'brief', None):
        resources = load_resources(args, config, deadline, skill_root, need_catalog=False, need_profiles=False, need_schema=True)
        brief, _ = load_brief_or_template(args, config, resources['schema'], skill_root)
        brief_gates = list(brief.get('approval_gates', []))
    gates = list(dict.fromkeys(brief_gates + config['approval']['required_actions']))
    return {
        'gate_count': len(gates),
        'gates': gates,
        'confirmation_token': config['approval']['confirmation_token'],
        'external_execution': False,
        'receipt_command': 'python3 ultimate-game-maker/tools-game-maker.py gate --action <action> --reason "<human review>" --confirm <token>',
    }, []


def command_recover(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    if args.replace_existing:
        validation.validate_approval(args, config, 'overwrite')
    resources = load_resources(args, config, deadline, skill_root)
    brief, brief_source = load_brief_or_template(args, config, resources['schema'], skill_root)
    state_path = Path(args.state)
    state = common.load_json_file(state_path, int(config['limits']['max_config_bytes']), 'recovery-state.json', config)
    for option_key, value in (('catalog', getattr(args, 'catalog', None)), ('engine_profiles', getattr(args, 'engine_profiles', None)), ('brief_schema', getattr(args, 'brief_schema', None)), ('template', getattr(args, 'template', None)), ('html5_template', getattr(args, 'html5_template', None))):
        if value:
            state.setdefault('options', {})[option_key] = value
    result = planner.recover_from_state(state, brief, config, resources['catalog'], resources['profiles'], deadline,
                                        output_root=args.output, replace_existing=args.replace_existing,
                                        confirm=args.confirm, skill_root=skill_root)
    result['brief_source'] = brief_source
    result['state_source'] = str(state_path)
    return result, []


def command_selftest(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    report = modules.selftest.run(config, deadline, args, skill_root, HERE)
    if not report.get('passed'):
        failures = [item for item in report.get('checks', []) if isinstance(item, dict) and item.get('status') == 'failed']
        raise common.ToolError('SELFTEST_FAILED', 'One or more validation checks failed.', [json.dumps(item, ensure_ascii=False) for item in failures[:10]], ['Inspect the failure details, fix the packaged files, and rerun selftest.'], exit_code=1)
    return report, list(report.get('warnings', []))


def command_describe(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    engine = _engine(config, deadline, skill_root)
    result = engine.describe(args, output_format)
    _write_result(args, config, deadline, result, output_format, result['contract'])
    return result, []


def command_route(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    engine = _engine(config, deadline, skill_root)
    result = engine.route(args, output_format)
    payload = {
        'valid': result['valid'], 'request': result['request'], 'routing': result['routing'],
        'assumed_defaults': result['assumed_defaults'], 'validation_warnings': result['validation_warnings'],
    }
    _write_result(args, config, deadline, result, output_format, payload)
    return result, _warning_lines(result['validation_warnings'])


def command_production_plan(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    engine = _engine(config, deadline, skill_root)
    result = engine.production_plan(args, output_format)
    _write_result(args, config, deadline, result, output_format, result['plan'])
    return result, _warning_lines(result['validation_warnings'])


def command_validate_request(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    engine = _engine(config, deadline, skill_root)
    result = engine.validate_request(args, output_format)
    payload = {
        'valid': result['valid'], 'request_source': result['request_source'], 'errors': result['errors'],
        'warnings': result['warnings'], 'schema_keywords_unchecked': result['schema_keywords_unchecked'],
        'normalized_request': result['normalized_request'], 'assumed_defaults': result['assumed_defaults'],
    }
    _write_result(args, config, deadline, result, output_format, payload)
    return result, _warning_lines(result['warnings'])


def command_export(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    if args.approve:
        validation.validate_approval(args, config, 'overwrite')
    engine = _engine(config, deadline, skill_root)
    result = engine.export(args, output_format)
    return result, ['No external engine SDK, store, cloud, credential, or publishing action was executed.']


def command_evidence_gate(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    engine = _engine(config, deadline, skill_root)
    result = engine.evidence_gate(args, output_format)
    payload = result['template'] if result['mode'] == 'template' else result['report']
    _write_result(args, config, deadline, result, output_format, payload)
    return result, []


def command_doctor(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    engine = _engine(config, deadline, skill_root)
    result = engine.doctor(output_format)
    return result, ['Python 3.9 compatibility was checked by syntax parsing; no separate 3.9 interpreter was executed.']


def command_example_request(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    engine = _engine(config, deadline, skill_root)
    result = engine.example_request(args, output_format)
    _write_result(args, config, deadline, result, output_format, result['template'])
    return result, []


def command_verify(args, config, deadline, output_format, skill_root) -> tuple[dict, list[str]]:
    engine = _engine(config, deadline, skill_root)
    doctor = engine.doctor('json')
    report = modules.selftest.run(config, deadline, args, skill_root, HERE)
    if not report.get('passed'):
        failures = [item for item in report.get('checks', []) if isinstance(item, dict) and item.get('status') == 'failed']
        raise common.ToolError('VERIFY_FAILED', 'Doctor passed but one or more selftest checks failed.', [json.dumps(item, ensure_ascii=False) for item in failures[:10]], ['Inspect the failure details and rerun selftest.'], exit_code=1)
    text = '\n'.join([
        'Verify: OK',
        'Doctor: %d files, %d references, %d capabilities' % (doctor['package']['file_count'], doctor['references_resolved'], doctor['production']['capabilities']),
        'Selftest: %d passed, %d failed, %d skipped' % (report['passed_count'], report['failed_count'], report['skipped_count']),
    ])
    return {
        'passed': True,
        'doctor': doctor,
        'selftest': report,
        'text': text,
        'markdown': '# Verification report\n\n```text\n' + text + '\n```\n',
    }, list(report.get('warnings', [])) + ['Python 3.9 compatibility was checked by syntax parsing; no separate 3.9 interpreter was executed.']


HANDLERS = {
    'help': command_help,
    'version': command_version,
    'capabilities': command_capabilities,
    'catalog': command_catalog,
    'parse-source': command_parse_source,
    'example': command_example,
    'validate': command_validate,
    'plan': command_plan,
    'capability': command_capability,
    'docs': command_docs,
    'prototype': command_prototype,
    'gate': command_gate,
    'gate-list': command_gate_list,
    'recover': command_recover,
    'describe': command_describe,
    'route': command_route,
    'production-plan': command_production_plan,
    'validate-request': command_validate_request,
    'export': command_export,
    'evidence-gate': command_evidence_gate,
    'doctor': command_doctor,
    'example-request': command_example_request,
    'verify': command_verify,
    'selftest': command_selftest,
}



def main(argv=None) -> int:
    common.assert_runtime()
    args = PARSER.parse_args(argv)
    command = args.command
    config = None
    output_format = 'json'
    try:
        if command == 'help':
            return command_help(args)
        config, deadline, output_format, skill_root = prepare(args)
        handler = HANDLERS[command]
        result, warnings = handler(args, config, deadline, output_format, skill_root)
        emit(common.success_payload(command, result, warnings), output_format)
        return 0
    except KeyboardInterrupt:
        error = common.ToolError('CANCELLED', 'The operation was cancelled by the user.', [], ['Rerun with a clean output path; partial files are cleaned up when the writer was active.'], exit_code=130)
        emit(common.error_payload(error, command), output_format)
        return 130
    except common.ToolError as error:
        emit(common.error_payload(error, command), output_format)
        return error.exit_code
    except SystemExit:
        raise
    except Exception as exc:
        error = common.ToolError('UNEXPECTED_ERROR', 'An unhandled runtime error occurred.', [type(exc).__name__, str(exc)[:400]], ['Rerun selftest, restore any missing prefixed files, and use a clean output path.'], exit_code=1)
        emit(common.error_payload(error, command), output_format)
        return 1


if __name__ == '__main__':
    sys.exit(main())
