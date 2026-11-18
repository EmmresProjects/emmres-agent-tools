#!/usr/bin/env python3
"""Category-specific capability packet generators for all 33 handlers and 330 specialists."""
from __future__ import annotations

import json
from typing import Iterable

import ugm_common as common

SPECIAL = {
'01-vision-planning': ('Generate an editable design-and-production section with outcomes, scope, milestones, risks, and acceptance checks.', 'No project management service is called; all planning state stays in generated Markdown and JSON.', 'Missing acceptance criteria must fail brief validation; a changed goal must change the plan.'),
'02-version-intelligence': ('Generate version notes, a compatibility matrix, and explicit missing-evidence records from the brief and local documentation.', 'Only user-supplied local official docs are inspected; no network retrieval or version guessing is performed.', 'A supplied version token must appear in evidence; an absent exact version must be reported as incomplete.'),
'03-unity': ('Generate a Unity architecture and C# interface checklist tied to the declared engine version.', 'No Unity editor, package manager, compiler, or player build is invoked.', 'The plan must state that Unity compilation is external and cite supplied version evidence.'),
'04-unreal': ('Generate Unreal module, Blueprint, cook, and packaging checklists with explicit ownership boundaries.', 'No UnrealBuildTool, editor, or cook process is invoked.', 'The packet must separate planning artifacts from external cook and packaging steps.'),
'05-godot': ('Generate Godot scene-tree, signal, resource, and export-preset guidance.', 'No Godot editor, GDScript runtime, C# build, or export template is invoked.', 'The selected script language and export template requirement must appear in version notes.'),
'06-html5': ('Generate a consolidated browser architecture plus a real self-contained HTML5 Canvas prototype.', 'The only runnable source generated is a single HTML file with inline CSS and JavaScript.', 'The HTML must parse, contain no external resources, and expose deterministic pure game logic.'),
'07-python-games': ('Generate Python module, dependency-lock, save-compatibility, and packaging instructions.', 'No Pygame, RenPy, Arcade, Panda3D, Ursina, Kivy, or executable bundler is bundled.', 'The plan must require exact runtime and package pins before any external build.'),
'08-native-frameworks': ('Generate a native build graph, memory-ownership contract, and portability checklist.', 'No C, C++, C#, Java, SDL, SFML, OpenGL, or Vulkan compiler or SDK is invoked.', 'Compiler, language standard, and graphics backend must be declared or marked missing.'),
'09-creator-platforms': ('Generate platform-specific project-model, extension, and deployment-rule guidance.', 'No third-party creator editor is installed, launched, or queried.', 'Each platform packet must state that version-specific editor behavior requires supplied official evidence.'),
'10-custom-tooling': ('Generate an engine subsystem map, bounded DSL grammar notes, bytecode limits, and toolchain lock.', 'No generated grammar, compiler, interpreter, debugger, or LSP server is executed.', 'Runtime limits and diagnostic recovery rules must be explicit and bounded.'),
'11-player-mechanics': ('Generate deterministic mechanic state tables, input-buffer rules, feedback notes, and save-schema guidance.', 'Mechanics are specified as editable data and checks, not executed as arbitrary game code.', 'Each state transition must have a test that can fail and a replayable input sequence.'),
'12-game-ai': ('Generate agent state contracts, navigation-data notes, fallback states, and debug-overlay requirements.', 'No unreviewed agent code is executed; agent behavior is represented as constraints and scenarios.', 'The same seed must produce the same documented scenario and chosen-action trace.'),
'13-procedural-generation': ('Generate seed ledgers, generation constraints, progression rules, and measurable quality gates.', 'Generators are described by deterministic contracts; no unbounded world generation is run.', 'A repeated seed must be marked reproducible and invalid worlds must be rejected.'),
'14-2d-systems': ('Generate pixel-grid, tilemap, sprite-atlas, collision, sorting, and device-performance rules.', 'No image editor or raster asset pipeline is invoked; described assets use reusable schemas.', 'Whole-pixel snapping and atlas-border validation must appear as checks.'),
'15-3d-rendering': ('Generate scene-streaming maps, material-parameter tables, shader-variant limits, and fallback tiers.', 'No GPU, renderer, shader compiler, or 3D editor is invoked.', 'Coordinate system, units, and platform fallbacks must be explicit.'),
'16-physics-simulation': ('Generate fixed-step contracts, collision-layer tables, replay traces, and contact-debug notes.', 'No physics engine is executed; simulation is represented as deterministic test inputs.', 'Units, substeps, and replay comparison must be recorded as checks.'),
'17-animation-motion': ('Generate animation state maps, event tracks, retargeting tables, and runtime bone budgets.', 'No rig, mocap file, or animation runtime is loaded.', 'Every transition must have an exit condition and every event must be listed.'),
'18-ui-input': ('Generate screen flows, input-binding tables, safe-area rules, rebinding profiles, and accessibility checks.', 'No device input service is captured; bindings are editable data.', 'Focus, back paths, conflicts, and localization must be checked.'),
'19-audio-systems': ('Generate audio event tables, mixing snapshots, codec rules, and bounded voice budgets.', 'No audio middleware, decoder, or platform audio service is invoked.', 'Events must have voice limits and asset validation must cover sample rates and channels.'),
'20-networking': ('Generate authority boundaries, replication tables, session recovery notes, and rate-limit rules.', 'No socket, server, matchmaking, or online service is opened.', 'The server-authority statement and disconnect recovery plan must be explicit.'),
'21-live-services': ('Generate durable schemas, migration plans, idempotency rules, and incident runbooks.', 'No database, cloud account, identity provider, or live service is contacted.', 'Schema migrations, backups, and rate limits must be present as tests or gates.'),
'22-platform-integration': ('Generate platform capability matrices, lifecycle notes, and certification checklists.', 'No platform SDK, console certification service, or device is used.', 'Permissions, suspend behavior, and packaging differences must be documented.'),
'23-xr-spatial': ('Generate XR action maps, comfort presets, safety-boundary rules, and device-fallback tiers.', 'No XR runtime, headset, eye tracker, or spatial anchor service is accessed.', 'Consent gates, frame-latency budgets, and seated fallbacks must be listed.'),
'24-narrative-localization': ('Generate story-variable tables, dialogue-graph requirements, stable string IDs, and locale metadata.', 'No authoring tool or localization service is invoked.', 'Branch reachability, save compatibility, and right-to-left layout checks must be present.'),
'25-levels-content': ('Generate level metrics, puzzle-solution tables, checkpoint rules, and playtest analysis fields.', 'No level editor or automated playtest agent is launched.', 'Reachability, soft locks, and solution checks must be testable.'),
'26-economy-live-ops': ('Generate economy models, reward schedules, auditable ledgers, and rollback plans.', 'No storefront, payment, battle-pass, or live configuration service is contacted.', 'Age-aware safeguards and approval gates must appear before any monetization change.'),
'27-testing-qa': ('Generate a layered test strategy, compatibility matrix, deterministic replay fixtures, and triage rules.', 'Only the local self-test harness is executed when requested; external test runners are not launched.', 'Tests must be able to fail and clean-state startup must be included.'),
'28-performance': ('Generate device budgets, benchmark procedures, profiling notes, and regression gates.', 'No profiler, GPU capture, or external benchmark service is invoked.', 'Frame time, memory, bandwidth, and worst-case scene budgets must be explicit.'),
'29-builds-releases': ('Generate build graphs, artifact contracts, clean-machine checklists, and rollback points.', 'No signing, publishing, deployment, installer, store, or CI service is executed.', 'Release and store-submission steps must remain human approval gates.'),
'30-trust-safety': ('Generate threat models, accessibility matrices, data-minimization notes, and compliance evidence fields.', 'No security scanner, moderation service, identity provider, or policy API is contacted.', 'Untrusted inputs, secrets absence, consent, and accessibility options must be checked.'),
'31-modding-extensions': ('Generate versioned mod APIs, bounded sandbox permissions, content schemas, and load-order rules.', 'No user script, plugin, workshop, or arbitrary extension is executed.', 'Host APIs must be bounded and load order must be deterministic.'),
'32-legacy-preservation': ('Generate provenance records, format notes, compatibility-layer plans, and reversible migration fixtures.', 'No binary parser, emulator, decompiler, or recovered source is executed.', 'Original artifacts must be preserved and all format assumptions documented.'),
'33-production-orchestration': ('Generate the unified production graph, source normalization manifest, approval ledger, and project-health summary.', 'All orchestration is local and deterministic; human approval is required before external actions.', 'All selected capabilities must be reachable and no external action may be executed.'),
}


class CapabilityPacketFactory:
    """One factory with 33 clearly identified category handlers."""

    def __init__(self, config: dict, deadline: common.Deadline):
        self.config = config
        self.deadline = deadline
        if len(SPECIAL) != 33:
            raise common.ToolError('INVALID_HANDLER_TABLE', 'Exactly 33 category handler adapters are required.', [len(SPECIAL)])

    def render(self, handler: dict, skill: dict, brief: dict, route: dict, ordinal: int) -> dict:
        self.deadline.check(f'capability packet {ordinal}')
        if handler['id'] not in SPECIAL:
            raise common.ToolError('UNKNOWN_HANDLER', 'No category adapter exists for this handler.', [handler['id']])
        implementation, adapter_note, test_note = SPECIAL[handler['id']]
        request = {
            'goal': brief.get('goal'),
            'genre': brief.get('genre'),
            'engine': route['requested_engine'],
            'language': brief.get('language'),
            'target_platform': brief.get('target_platform'),
            'seed': brief.get('seed', 0),
            'quality': brief.get('quality', 'prototype'),
        }
        version_status = route['version_status']
        missing_evidence = route['missing_version_evidence']
        approval_gates = list(dict.fromkeys(list(brief.get('approval_gates', [])) + ['publish', 'deploy', 'store-submission']))
        return {
            'schema_id': 'ugm-capability-packet-1.0',
            'ordinal': ordinal,
            'umbrella': 'ultimate-game-maker',
            'handler': {
                'id': handler['id'],
                'category': handler['category'],
                'operation': handler['operation'],
                'outcome': handler['outcome'],
            },
            'skill': {
                'name': skill['name'],
                'slug': skill['slug'],
                'description': skill['description'],
                'focus': skill['focus'],
                'deliverables': skill['deliverables'],
            },
            'request': request,
            'routing': {
                'profile_id': route['profile']['id'],
                'source_generation': route['profile']['source_generation'],
                'version_status': version_status,
                'missing_version_evidence': missing_evidence,
                'prototype_available': route['prototype_available'],
            },
            'generation': {
                'provider': self.config['providers']['blueprint'],
                'model_profile': self.config['models']['blueprint'],
                'prompt_set': self.config['prompts']['production_template'],
                'policy': self.config['policies']['approval_config'],
                'implementation': implementation,
                'adapter_note': adapter_note,
                'planned_artifacts': handler['artifacts'],
            },
            'contract': {
                'inputs': ['goal', 'genre', 'engine', 'exact version', 'language', 'target platform', 'assets', 'references', 'constraints', 'acceptance criteria'],
                'outputs': skill['deliverables'],
                'controls': handler['controls'],
                'checks': list(dict.fromkeys(skill['checks'] + handler['checks'] + [test_note])),
                'recovery': handler['recovery'],
                'interoperability': handler['interoperability'],
            },
            'approval': {
                'required_before_external_actions': approval_gates,
                'status': 'not-executed',
                'external_execution': False,
            },
            'limits': [
                'No external runtime, compiler, store, cloud, credential, or payment service is called.',
                'No arbitrary code, shell, or network execution is performed from brief content.',
                f'Output is bounded by {self.config["limits"]["max_generated_file_bytes"]} bytes per file and a {self.config["runtime"]["deadline_seconds"]} second cooperative deadline.',
            ],
        }


def build_packets(selection: dict, brief: dict, route: dict, config: dict,
                  deadline: common.Deadline) -> list[dict]:
    factory = CapabilityPacketFactory(config, deadline)
    packets: list[dict] = []
    ordinal = 0
    for handler in selection['handlers']:
        for skill in handler['skills']:
            ordinal += 1
            packets.append(factory.render(handler, skill, brief, route, ordinal))
    if ordinal != selection['skill_count']:
        raise common.ToolError('PACKET_COUNT_MISMATCH', 'Rendered packet count does not match the selection.', [ordinal, selection['skill_count']])
    return packets


def packets_json(packets: list[dict], selection: dict, brief: dict, route: dict, config: dict) -> str:
    payload = {
        'schema_id': 'ugm-capability-packets-1.0',
        'umbrella': 'ultimate-game-maker',
        'selection': {key: selection[key] for key in ('mode', 'requested_categories', 'requested_skills', 'handler_count', 'skill_count')},
        'brief_summary': {
            'goal': brief.get('goal'),
            'engine': route['requested_engine'],
            'language': brief.get('language'),
            'target_platform': brief.get('target_platform'),
            'seed': brief.get('seed', 0),
        },
        'routing': route,
        'generation': {
            'provider': config['providers']['blueprint'],
            'model_profile': config['models']['blueprint'],
            'prompt_set': config['prompts']['production_template'],
            'deterministic': config['runtime']['deterministic_output'],
        },
        'packet_count': len(packets),
        'packets': packets,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + '\n'


def packet_markdown(packet: dict) -> str:
    skill = packet['skill']
    handler = packet['handler']
    contract = packet['contract']
    lines = [
        f'### {packet["ordinal"]:03d}. {skill["name"]}',
        '',
        f'- **Handler:** `{handler["id"]}` / `{handler["operation"]}`',
        f'- **Responsibility:** {skill["focus"]}.',
        f'- **Source description:** {skill["description"]}',
        f'- **Generated implementation:** {packet["generation"]["implementation"]}',
        f'- **Adapter boundary:** {packet["generation"]["adapter_note"]}',
        f'- **Version status:** `{packet["routing"]["version_status"]}`; missing evidence: {", ".join(packet["routing"]["missing_version_evidence"]) or "none"}.',
        f'- **Editable outputs:** {", ".join(skill["deliverables"])}.',
        '- **Checks:**',
    ]
    lines.extend(f'  - {check}' for check in contract['checks'])
    lines.extend([
        f'- **Recovery:** {contract["recovery"]}',
        f'- **Interoperability:** {", ".join(contract["interoperability"])}.',
        f'- **Approval gates:** {", ".join(packet["approval"]["required_before_external_actions"])} remain not executed.',
        '',
    ])
    return '\n'.join(lines)


def packets_markdown(packets: Iterable[dict], selection: dict, brief: dict, route: dict) -> str:
    packets = list(packets)
    grouped: dict[str, list[dict]] = {}
    for packet in packets:
        grouped.setdefault(packet['handler']['id'], []).append(packet)
    lines = [
        '# Unified Capability Packets',
        '',
        f'Umbrella: `ultimate-game-maker`  ',
        f'Mode: `{selection["mode"]}`  ',
        f'Handlers: **{selection["handler_count"]}**  ',
        f'Specialists: **{selection["skill_count"]}**  ',
        f'Goal: {brief.get("goal")}  ',
        f'Engine: `{route["requested_engine"]}`  ',
        f'Language: `{brief.get("language")}`  ',
        f'Platform: `{brief.get("target_platform")}`  ',
        f'Seed: `{brief.get("seed", 0)}`',
        '',
        'Every listed capability is included in this unified tool. Packets are editable Markdown and mirrored in `capability-packets.json`.',
        '',
    ]
    for handler_id, group in grouped.items():
        first = group[0]
        lines.extend([
            f'## {handler_id} — {first["handler"]["category"]}',
            '',
            f'- **Operation:** `{first["handler"]["operation"]}`',
            f'- **Outcome:** {first["handler"]["outcome"]}',
            f'- **Specialists in this selection:** {len(group)}',
            '',
        ])
        lines.extend(packet_markdown(packet) for packet in group)
    return '\n'.join(lines)


def acceptance_checks(packets: list[dict], brief: dict, route: dict) -> str:
    records = []
    for packet in packets:
        for index, check in enumerate(packet['contract']['checks'], start=1):
            records.append({
                'id': f'{packet["ordinal"]:03d}-{index:02d}',
                'handler': packet['handler']['id'],
                'operation': packet['handler']['operation'],
                'skill': packet['skill']['slug'],
                'check': check,
                'can_fail': True,
                'status': 'defined',
                'evidence_required': 'generated packet, local validation, or external runtime evidence',
            })
    payload = {
        'schema_id': 'ugm-acceptance-checks-1.0',
        'brief_acceptance_criteria': brief.get('acceptance_criteria', []),
        'version_status': route['version_status'],
        'missing_version_evidence': route['missing_version_evidence'],
        'record_count': len(records),
        'records': records,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + '\n'


def render_packet_text(packet: dict) -> str:
    return packet_markdown(packet)
