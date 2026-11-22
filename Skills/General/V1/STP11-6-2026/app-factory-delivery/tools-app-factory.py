#!/usr/bin/env python3
"""App Factory Delivery Orchestrator - primary entry point.

A catalog-driven app-factory Skill Tool: it routes a delivery request to the right
capability, produces the planning and verification artifacts each factory stage
needs, gates them, and reports release readiness from checks that actually ran.

Usage:
    python3 tools-app-factory.py --help
    python3 tools-app-factory.py capabilities
    python3 tools-app-factory.py route --request "offline-first Flutter app with sync" --platform mobile
    python3 tools-app-factory.py init --project-dir ./shop --name "Shop" --preset python-cli
    python3 tools-app-factory.py stage requirements --project-dir ./shop --input reqs.md
    ...
    python3 tools-app-factory.py stage readiness --project-dir ./shop

Only the Python standard library is required. See knowledge-readme.md.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

# Keep the Skill Tool folder free of generated files: no __pycache__ next to the tools.
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILENAME = ".factory-state.json"
DEFAULT_ARTIFACT_ROOT = "factory"
TOOL_VERSION = "1.0.0"

ASSET_FILES = {
    "catalog": "asset-skill-catalog.json",
    "workflow": "asset-factory-workflow.json",
    "presets": "asset-stack-presets.json",
    "controls": "asset-security-controls.json",
}

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GATE = 3
EXIT_PREREQ = 4
EXIT_APPROVAL = 5
EXIT_BLOCKED = 6


class ToolError(Exception):
    """Structured, user-facing error with an actionable hint."""

    def __init__(self, message: str, hint: str = "", code: int = EXIT_USAGE):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code


def _load_module(module_name: str, filename: str):
    path = os.path.join(HERE, filename)
    if not os.path.isfile(path):
        raise ToolError(f"required module is missing: {filename}", "Reinstall the Skill Tool folder with every tools- file present.")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


ENGINE = _load_module("factory_engine", "tools-factory-engine.py")
NORMALIZER = _load_module("catalog_normalizer", "tools-catalog-normalizer.py")
RUNNER = _load_module("verification_runner", "tools-verification-runner.py")


# --------------------------------------------------------------------------- #
# assets, state, io
# --------------------------------------------------------------------------- #
def load_assets() -> dict:
    assets = {}
    for key, filename in ASSET_FILES.items():
        path = os.path.join(HERE, filename)
        if not os.path.isfile(path):
            raise ToolError(
                f"asset file is missing: {filename}",
                "Every asset- file in ASSET_FILES must sit beside the tools- files; restore it from the Skill Tool folder.",
            )
        try:
            assets[key] = ENGINE.load_json(path)
        except json.JSONDecodeError as error:
            raise ToolError(f"asset file is not valid JSON: {filename} ({error})", "Restore the shipped asset file.") from error

    workflow = assets["workflow"]
    presets = assets["presets"]
    if not workflow.get("stages"):
        raise ToolError("asset-factory-workflow.json defines no stages", "Restore the shipped workflow asset.")
    if not presets.get("presets"):
        raise ToolError("asset-stack-presets.json defines no presets", "Restore the shipped presets asset.")
    criteria = [criterion["id"] for criterion in presets.get("criteria", [])]
    for key, preset in presets["presets"].items():
        for candidate in preset.get("candidates", []):
            missing = set(criteria) - set(candidate.get("scores", {}))
            if missing:
                raise ToolError(
                    f"preset '{key}' candidate '{candidate['key']}' lacks scores for: {', '.join(sorted(missing))}",
                    "Every candidate must score every criterion in asset-stack-presets.json.",
                )
    if not workflow.get("limits", {}).get("verify", {}).get("allowed_executables"):
        raise ToolError("workflow limits define no verification allowlist", "Restore asset-factory-workflow.json.")

    category_slugs = {category["slug"] for category in assets["catalog"].get("categories", [])}
    skill_slugs = {skill["slug"] for category in assets["catalog"].get("categories", []) for skill in category.get("skills", [])}
    for key, preset in presets["presets"].items():
        dead = sorted(set(preset.get("catalog_categories", [])) - category_slugs)
        if dead:
            raise ToolError(
                f"preset '{key}' references unknown catalog categories: {', '.join(dead)}",
                "Fix catalog_categories in asset-stack-presets.json so every reference resolves in asset-skill-catalog.json.",
            )
    for stage in workflow["stages"]:
        dead = sorted(set(stage.get("catalog_skills", [])) - skill_slugs)
        if dead:
            raise ToolError(
                f"stage '{stage['id']}' references unknown catalog skills: {', '.join(dead)}",
                "Fix catalog_skills in asset-factory-workflow.json so every reference resolves in asset-skill-catalog.json.",
            )
    return assets


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _write_text(path: str, text: str) -> dict:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text if text.endswith("\n") else text + "\n")
    digest, size = ENGINE.sha256_file(path)
    return {"sha256": digest, "bytes": size}


def _write_json(path: str, payload: dict) -> dict:
    return _write_text(path, json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n")


def _read_text(path: str, max_bytes: int) -> str:
    if not os.path.isfile(path):
        raise ToolError(f"input file not found: {path}", "Pass an existing file path, or use the inline option for a short value.")
    size = os.path.getsize(path)
    if size > max_bytes:
        raise ToolError(f"input file is {size} bytes, above max_input_bytes ({max_bytes})", "Split the file or raise the limit in asset-factory-workflow.json.")
    if size == 0:
        raise ToolError(f"input file is empty: {path}", "Provide at least one requirement line or command list entry.")
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def state_path(project_dir: str) -> str:
    return os.path.join(project_dir, STATE_FILENAME)


def load_state(project_dir: str) -> dict:
    path = state_path(project_dir)
    if not os.path.isfile(path):
        raise ToolError(
            f"no factory state found at {_rel(path, os.getcwd()) if os.path.abspath(path).startswith(os.getcwd()) else path}",
            "Run 'init' for this project directory first, or pass the correct --project-dir.",
            EXIT_PREREQ,
        )
    try:
        return ENGINE.load_json(path)
    except json.JSONDecodeError as error:
        raise ToolError(f"factory state is not valid JSON: {error}", "Recreate the state with 'init --force --approve overwrite'.", EXIT_PREREQ) from error


def save_state(project_dir: str, state: dict) -> None:
    state["updated_utc"] = utc_now()
    payload = json.dumps(state, indent=2, ensure_ascii=False)
    directory = os.path.dirname(os.path.abspath(state_path(project_dir))) or "."
    os.makedirs(directory, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(prefix=".factory-state-", dir=directory, text=True)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload + "\n")
        os.replace(temp_path, state_path(project_dir))
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def record_event(state: dict, event: str, detail: str = "") -> None:
    state.setdefault("history", []).append({"utc": utc_now(), "event": event, "detail": detail})
    state["history"] = state["history"][-60:]


def resolve_paths(args) -> tuple[str, str]:
    project_dir = os.path.abspath(args.project_dir)
    artifact_root = os.path.abspath(args.out) if getattr(args, "out", None) else os.path.join(project_dir, DEFAULT_ARTIFACT_ROOT)
    try:
        if os.path.commonpath([project_dir, artifact_root]) != project_dir:
            raise ToolError(
                "--out must stay inside the project directory so artifacts remain portable",
                "Use a path under the project directory, for example <project>/factory.",
            )
    except ValueError as error:
        raise ToolError(f"--out is not comparable with the project directory: {error}", "Use a path under the project directory.") from error
    return project_dir, artifact_root


def parse_weights(raw: str | None, presets: dict) -> dict:
    if not raw:
        return {}
    weights = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ToolError(f"invalid weight '{chunk}'", "Use the form criterion=value, for example performance=0.4,security=0.3")
        key, value = chunk.split("=", 1)
        key, value = key.strip(), value.strip()
        try:
            weights[key] = float(value)
        except ValueError as error:
            raise ToolError(f"weight for '{key}' is not a number: {value}", "Weights must be numeric, for example performance=0.4") from error
        if weights[key] < 0:
            raise ToolError(f"weight for '{key}' is negative", "Weights must be zero or positive.")
    known = {criterion["id"] for criterion in presets.get("criteria", [])}
    unknown = sorted(set(weights) - known)
    if unknown:
        raise ToolError(
            "unknown weighting criteria: " + ", ".join(unknown),
            "Available criteria: " + ", ".join(sorted(known)),
        )
    if sum(weights.values()) <= 0:
        raise ToolError("weights sum to zero", "Give at least one positive weight.")
    return weights


def excluded_paths(project_dir: str, artifact_root: str) -> list[str]:
    return [state_path(project_dir), os.path.join(project_dir, "catalog-import.json"), artifact_root]


# --------------------------------------------------------------------------- #
# output helpers
# --------------------------------------------------------------------------- #
def emit(args, json_payload: dict, text_lines: list[str]) -> None:
    if getattr(args, "format", "text") == "json":
        print(json.dumps({"ok": True, **json_payload}, indent=2, ensure_ascii=False))
    else:
        for line in text_lines:
            print(line)


def stage_summary(stage_id: str, gate: dict, artifacts: list[dict], extra: list[str] | None = None) -> list[str]:
    lines = [
        f"stage: {stage_id}",
        f"gate: {'PASS' if gate['pass'] else 'FAIL'} (score {gate['score']}/100)",
    ]
    if gate["failed"]:
        lines.append("failed criteria: " + ", ".join(gate["failed"]))
    else:
        lines.append("failed criteria: none")
    for artifact in artifacts:
        lines.append(f"artifact: {artifact['path']} ({artifact['bytes']} bytes, sha256 {artifact['sha256'][:16]}...)")
    for line in extra or []:
        lines.append(line)
    return lines


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
CAPABILITIES = [
    {
        "id": "capabilities",
        "command": "capabilities",
        "summary": "List this Skill Tool's operational capabilities and catalog coverage.",
        "catalog_skills": ["app-factory-orchestrator", "quality-gate-controller"],
    },
    {
        "id": "catalog",
        "command": "catalog list | catalog search | catalog show | catalog import",
        "summary": "Query the bundled 30-category / 244-skill catalog and normalize pasted plain-text, Markdown or HTML capability lists into catalog JSON.",
        "catalog_skills": ["language-and-framework-router", "app-factory-orchestrator"],
    },
    {
        "id": "route",
        "command": "route --request TEXT",
        "summary": "Route a delivery request to catalog skills, delivery presets and the factory stage composition using term-weighted scoring.",
        "catalog_skills": ["language-and-framework-router", "technology-stack-selector"],
    },
    {
        "id": "init",
        "command": "init --project-dir DIR --name NAME",
        "summary": "Create factory state for a project with preset, stack, version and weighting decisions.",
        "catalog_skills": ["cross-skill-state-manager"],
    },
    {
        "id": "stage-requirements",
        "command": "stage requirements --input FILE",
        "summary": "Generate personas, epics, user stories, acceptance criteria, NFR coverage, assumptions and open questions.",
        "catalog_skills": ["requirements-to-backlog-engineer", "multi-platform-product-planner"],
    },
    {
        "id": "stage-architecture",
        "command": "stage architecture",
        "summary": "Score candidate stacks under configurable weights, then emit components, data, deployment, version policy, risks, milestones and an ADR.",
        "catalog_skills": ["universal-app-solution-architect", "technology-stack-selector", "version-and-documentation-resolver"],
    },
    {
        "id": "stage-tests",
        "command": "stage tests",
        "summary": "Build a risk-based test strategy that maps every acceptance criterion to test levels and adds cross-cutting security, accessibility, performance and recovery suites.",
        "catalog_skills": ["test-strategy-architect", "unit-test-engineer", "integration-test-engineer", "end-to-end-test-engineer"],
    },
    {
        "id": "stage-security",
        "command": "stage security --evidence FILE",
        "summary": "Evaluate 26 security, privacy and supply-chain controls against supplied evidence with severity rollup and no unproven passing states.",
        "catalog_skills": ["security-threat-model-engineer", "privacy-and-data-protection-engineer", "secrets-and-credential-hygiene-engineer"],
    },
    {
        "id": "stage-packaging",
        "command": "stage packaging --commits FILE",
        "summary": "Hash the release set, detect credential-like files, apply semver bump rules from conventional commits, and emit release notes, changelog, checksums and an inventory-lite SBOM.",
        "catalog_skills": ["release-packaging-engineer", "semantic-versioning-and-changelog-manager", "artifact-handoff-and-packaging-manager"],
    },
    {
        "id": "stage-readiness",
        "command": "stage readiness",
        "summary": "Aggregate every stage gate, detect artifact drift, require a current verification run and emit a weighted readiness score with blockers and recommendations.",
        "catalog_skills": ["quality-gate-controller", "app-factory-orchestrator"],
    },
    {
        "id": "verify",
        "command": "verify --dry-run | verify --approve TOKEN",
        "summary": "Plan and, after exact token approval, execute the preset's bounded verification commands with timeouts, retry caps, output truncation and blocked-toolchain reporting.",
        "catalog_skills": ["build-run-test-coordinator"],
    },
    {
        "id": "boundary",
        "command": "boundary [--preset KEY]",
        "summary": "Detect missing toolchains, executable failures and missing project artifacts, then state the exact escalation instead of claiming success.",
        "catalog_skills": ["capability-boundary-and-escalation-manager"],
    },
    {
        "id": "status",
        "command": "status",
        "summary": "Show recorded state: stage gates, artifacts with checksum validity, verification status and the next recommended action.",
        "catalog_skills": ["cross-skill-state-manager", "app-factory-orchestrator"],
    },
]


def cmd_capabilities(args, assets) -> int:
    payload = {
        "tool": "app-factory-delivery",
        "version": TOOL_VERSION,
        "python": sys.version.split()[0],
        "capabilities": CAPABILITIES,
        "catalog": {
            "categories": assets["catalog"].get("counts", {}).get("categories", 0),
            "skills": assets["catalog"].get("counts", {}).get("skills", 0),
            "source": ASSET_FILES["catalog"],
        },
        "stages": [{"id": stage["id"], "title": stage["title"], "produces": stage.get("produces", [])} for stage in assets["workflow"]["stages"]],
        "presets": sorted(assets["presets"]["presets"]),
        "dependencies": "Python 3.9+ standard library only",
    }
    lines = [
        "app-factory-delivery 1.0.0",
        f"python: {payload['python']}",
        f"catalog: {payload['catalog']['categories']} categories, {payload['catalog']['skills']} skills ({payload['catalog']['source']})",
        "",
        "capabilities:",
    ]
    for item in CAPABILITIES:
        lines.append(f"  {item['id']:<20} {item['command']}")
        lines.append(f"  {'':<20} {item['summary']}")
    lines += ["", "factory stages:"]
    for stage in payload["stages"]:
        lines.append(f"  {stage['id']:<14} {stage['title']} -> {', '.join(stage['produces'])}")
    lines += ["", "delivery presets: " + ", ".join(payload["presets"]), "", "dependencies: Python 3.9+ standard library only"]
    emit(args, payload, lines)
    return EXIT_OK


def cmd_catalog(args, assets) -> int:
    if args.catalog_command == "import":
        limits = assets["workflow"].get("limits", {})
        try:
            catalog = NORMALIZER.parse_file(
                args.input,
                source_kind=args.source_format,
                default_category=args.default_category,
                limits={"max_input_bytes": int(limits.get("max_input_bytes", 2000000))},
            )
            if args.merge:
                if not os.path.isfile(args.merge):
                    raise ToolError(
                        f"merge base not found: {args.merge}",
                        "Pass a normalized catalog JSON produced by 'catalog import --out' or a shipped asset-*.json catalog.",
                    )
                catalog = NORMALIZER.merge_catalogs(NORMALIZER.load_catalog(args.merge), catalog)
        except FileNotFoundError as error:
            raise ToolError(str(error), "Pass --input with an existing .txt, .md or .html file.") from error
        except ValueError as error:
            raise ToolError(
                f"could not normalize the input: {error}",
                "Include category headings and 'Skill Name:'/'Description:' pairs, numbered items, or bullet items shaped like '- Capability — description'.",
            ) from error
        written = None
        if args.out:
            out_path = os.path.abspath(args.out)
            written = _write_json(out_path, catalog)
            written["path"] = out_path
        payload = {"catalog": catalog, "written": written}
        lines = [
            f"categories: {catalog['counts']['categories']}  skills: {catalog['counts']['skills']}  ignored: {catalog['counts']['ignored']}  missing descriptions: {catalog['counts']['description_missing']}",
        ]
        for category in catalog["categories"]:
            lines.append(f"  [{category['index']}] {category['name']} ({len(category['skills'])} skills)")
        if args.out:
            lines.append(f"written: {os.path.abspath(args.out)}")
        else:
            lines.append("no --out given: full catalog JSON follows")
            lines.append(json.dumps(catalog, indent=2, ensure_ascii=False))
        emit(args, payload, lines)
        return EXIT_OK

    catalog = assets["catalog"]
    if args.catalog_command == "list":
        categories = catalog["categories"]
        if args.category:
            category = ENGINE.find_category(catalog, args.category)
            if category is None:
                raise ToolError(
                    f"unknown category '{args.category}'",
                    "Use the category index, slug or exact name; run 'catalog list' to see them all.",
                )
            categories = [category]
        payload = {
            "categories": [
                {
                    "index": category["index"],
                    "name": category["name"],
                    "slug": category["slug"],
                    "skill_count": len(category["skills"]),
                    "skills": [] if args.brief else [{"name": skill["name"], "slug": skill["slug"]} for skill in category["skills"]],
                }
                for category in categories
            ],
            "counts": catalog.get("counts", {}),
        }
        lines = [f"{catalog['counts']['skills']} skills in {catalog['counts']['categories']} categories", ""]
        for category in payload["categories"]:
            lines.append(f"[{category['index']}] {category['name']} ({category['skill_count']} skills)")
            if not args.brief:
                for skill in category["skills"]:
                    lines.append(f"    - {skill['name']}  ({skill['slug']})")
        emit(args, payload, lines)
        return EXIT_OK

    if args.catalog_command == "search":
        if not args.query:
            raise ToolError("catalog search needs --query", "For example: --query \"offline sync\"")
        result = ENGINE.search_catalog(catalog, args.query, category_selector=args.category, limit=args.limit)
        lines = [f"query: {args.query}  matches: {result['count']}  tokens: {', '.join(result['tokens']) or '-'}", ""]
        for item in result["results"]:
            lines.append(f"  {item['relevance']:.2f}  {item['name']}  [{item['category']}]")
            lines.append(f"          {item['description'][:150]}")
        if not result["results"]:
            lines.append("  no match; try broader terms or 'catalog list'")
        emit(args, result, lines)
        return EXIT_OK

    if args.catalog_command == "show":
        index = ENGINE.catalog_index(catalog)
        slug = ENGINE.slugify(args.skill or "")
        entry = index.get(slug)
        if entry is None:
            matches = [item for key, item in index.items() if slug and (slug in key or slug in ENGINE.slugify(item["name"]))]
            if len(matches) != 1:
                raise ToolError(
                    f"skill '{args.skill}' not found (or ambiguous)",
                    "Use the exact slug from 'catalog list --brief', or a longer distinctive fragment.",
                )
            entry = matches[0]
        payload = {"skill": entry}
        emit(args, payload, [f"{entry['name']}  ({entry['slug']})", f"category: {entry['category']}", "", entry["description"]])
        return EXIT_OK

    raise ToolError("unknown catalog subcommand", "Use: catalog list | search | show | import")


def cmd_route(args, assets) -> int:
    request = args.request
    if args.request_file:
        request = _read_text(os.path.abspath(args.request_file), int(assets["workflow"]["limits"].get("max_input_bytes", 2000000)))
    if not request:
        raise ToolError("route needs --request or --request-file", "For example filter it: --request \"go service with gRPC and tracing\"")
    result = ENGINE.route(
        assets["catalog"],
        assets["presets"],
        assets["workflow"],
        request,
        platform=args.platform,
        language=args.language,
        category=args.category,
        limit=args.limit,
    )
    lines = [f"request: {result['request'][:160]}", ""]
    if result["presets"]:
        lines.append("preset recommendations:")
        for item in result["presets"]:
            lines.append(f"  {item['score']:>5}  {item['key']:<20} {item['label']}  ({'; '.join(item['reasons'])})")
        lines.append("")
    lines.append("matched catalog skills:")
    for item in result["skills"]:
        lines.append(f"  {item['relevance']:.2f}  {item['name']}  [{item['category']}]")
        if item["matched_terms"]:
            lines.append(f"          matched: {', '.join(item['matched_terms'])}")
    lines.append("")
    lines.append("stage composition:")
    for stage in result["stage_composition"]:
        required = ", ".join(skill["name"] for skill in stage["required_skills"])
        lines.append(f"  {stage['stage']:<13} {required}")
    for note in result["notes"]:
        lines.append(f"note: {note}")
    if result["presets"]:
        lines.append("")
        lines.append(f"next: init --project-dir <dir> --name <name> --preset {result['presets'][0]['key']}")
    emit(args, result, lines)
    return EXIT_OK


def cmd_init(args, assets) -> int:
    project_dir = os.path.abspath(args.project_dir)
    presets = assets["presets"]
    preset_key = args.preset
    if preset_key and preset_key not in presets["presets"]:
        raise ToolError(
            f"unknown preset '{preset_key}'",
            "Available presets: " + ", ".join(sorted(presets["presets"])),
        )
    if not preset_key and not args.reason:
        raise ToolError(
            "init needs --preset (or --reason to record why no preset is chosen yet)",
            "Available presets: " + ", ".join(sorted(presets["presets"])),
        )
    weights = parse_weights(args.weights, presets)
    stack = args.stack
    if stack and preset_key:
        ENGINE.select_stack(presets, preset_key, weights=weights, candidate=stack)
    elif stack and not preset_key:
        raise ToolError("--stack requires --preset", "Choose a preset first: " + ", ".join(sorted(presets["presets"])))
    if args.version:
        ENGINE.parse_semver(args.version)

    existing_state = os.path.isfile(state_path(project_dir))
    if existing_state:
        if not args.force:
            raise ToolError(
                "factory state already exists for this project directory",
                "Run the stages directly, or re-run init with --force --approve overwrite to replace the recorded state.",
            )
        if (args.approve or "").strip().lower() != "overwrite":
            raise ToolError(
                "refusing to replace existing factory state without explicit approval",
                "Re-run with --force --approve overwrite once you have confirmed you want to discard the recorded stage history.",
                EXIT_APPROVAL,
            )
    elif os.path.isdir(project_dir) and os.listdir(project_dir):
        existing_state = False  # an existing project tree is a supported starting point
    os.makedirs(project_dir, exist_ok=True)

    state = {
        "schema_version": "1.0",
        "tool_version": TOOL_VERSION,
        "name": args.name or os.path.basename(project_dir.rstrip(os.sep)) or "project",
        "preset": preset_key,
        "stack": stack or "",
        "weights": weights,
        "version": args.version or "1.0.0",
        "target_toolchain": args.target_toolchain or "",
        "request": args.reason or "",
        "project_dir_abs": project_dir,
        "artifact_root": _rel(os.path.join(project_dir, DEFAULT_ARTIFACT_ROOT), project_dir),
        "created_utc": utc_now(),
        "updated_utc": utc_now(),
        "stages": {},
        "verification": {},
        "history": [],
    }
    record_event(state, "init", f"preset={preset_key or 'none'} stack={stack or 'auto'}")
    save_state(project_dir, state)

    payload = {"state_file": _rel(state_path(project_dir), project_dir), "state": state}
    lines = [
        "initialized",
        f"project: {state['name']}",
        f"directory: {project_dir}",
        f"preset: {preset_key or 'none recorded'}",
        f"stack: {stack or 'auto (decided during stage architecture)'}",
        f"version: {state['version']}",
        f"state file: {_rel(state_path(project_dir), project_dir)}",
        "",
        "next: stage requirements --project-dir "
        + (args.project_dir)
        + " --input <requirements.md>",
    ]
    emit(args, payload, lines)
    return EXIT_OK


def _stage_record(state: dict, stage_id: str, artifacts: list[dict], gate: dict, facts: dict, details: dict) -> None:
    state.setdefault("stages", {})[stage_id] = {
        "status": "complete" if gate["pass"] else "failed_gate",
        "completed_utc": utc_now(),
        "artifacts": artifacts,
        "gate": gate,
        "facts": facts,
        "details": details,
    }
    record_event(state, f"stage:{stage_id}", f"gate={'pass' if gate['pass'] else 'fail'} score={gate['score']}")


def _finish_stage(args, project_dir: str, state: dict, stage_id: str, artifacts: list[dict], gate: dict, extra: list[str] | None = None) -> int:
    save_state(project_dir, state)
    lines = stage_summary(stage_id, gate, artifacts, extra)
    lines.extend(getattr(args, "prereq_warnings", []))
    if not gate["pass"]:
        lines.append("")
        lines.append("recovery: fix the failed criteria listed above and re-run the same stage command.")
    emit(args, {"stage": stage_id, "gate": gate, "artifacts": artifacts}, lines)
    if getattr(args, "strict", False) and not gate["pass"]:
        return EXIT_GATE
    return EXIT_OK


def stage_requirements(args, assets, project_dir: str, artifact_root: str, state: dict) -> int:
    limits = assets["workflow"].get("limits", {})
    max_bytes = int(limits.get("max_input_bytes", 2000000))
    if args.input:
        text = _read_text(os.path.abspath(args.input), max_bytes)
        source_path = _rel(os.path.abspath(args.input), project_dir) if os.path.abspath(args.input).startswith(project_dir) else os.path.abspath(args.input)
    elif args.requirement:
        text = "\n".join(args.requirement)
        source_path = "inline"
    elif state.get("request"):
        text = state["request"]
        source_path = "state:request"
    else:
        raise ToolError(
            "requirements stage needs input",
            "Pass --input <file.md>, one or more --requirement \"...\" values, or record the request during init with --reason.",
            EXIT_PREREQ,
        )

    try:
        payload = ENGINE.build_requirements(
            [text],
            assets["workflow"],
            project_name=state["name"],
            preset_key=state.get("preset"),
            source_path=source_path,
            default_persona=args.persona,
        )
    except ValueError as error:
        raise ToolError(f"could not build the backlog: {error}", "One requirement per line, as bullets, numbered items or 'As a ... I want ...' statements.") from error

    md_path = os.path.join(artifact_root, "requirements-backlog.md")
    json_path = os.path.join(artifact_root, "requirements-backlog.json")
    meta_md = _write_text(md_path, ENGINE.render_requirements_md(payload))
    meta_json = _write_json(json_path, payload)

    stories = payload["stories"]
    facts = {
        "stories_present": len(stories) > 0,
        "acceptance_criteria_complete": all(len(story["acceptance_criteria"]) >= 2 for story in stories),
        "nfr_coverage": sum(1 for entry in payload["nfr_coverage"].values() if entry["addressed"]) >= 3,
        "priorities_assigned": all(story.get("priority") for story in stories),
        "assumptions_tracked": bool(payload["assumptions"] or payload["open_questions"]),
    }
    addressed = [name for name, entry in payload["nfr_coverage"].items() if entry["addressed"]]
    details = {
        "nfr_coverage": f"{len(addressed)} categories addressed: {', '.join(addressed) or 'none'}",
        "stories_present": f"{len(stories)} stories across {len(payload['epics'])} epics",
        "acceptance_criteria_complete": f"{sum(len(story['acceptance_criteria']) for story in stories)} criteria generated",
    }
    gate = ENGINE.evaluate_stage_gate("requirements", {"facts": facts, "details": details}, assets["workflow"])
    artifacts = [
        {"path": _rel(md_path, project_dir), **meta_md},
        {"path": _rel(json_path, project_dir), **meta_json},
    ]
    _stage_record(state, "requirements", artifacts, gate, facts, details)
    extra = [
        f"stories: {len(stories)}  criteria: {sum(len(s['acceptance_criteria']) for s in stories)}  NFR categories addressed: {len(addressed)}",
        f"open questions: {len(payload['open_questions'])}  assumptions: {len(payload['assumptions'])}",
    ]
    return _finish_stage(args, project_dir, state, "requirements", artifacts, gate, extra)


def stage_architecture(args, assets, project_dir: str, artifact_root: str, state: dict) -> int:
    preset_key = args.preset or state.get("preset")
    if not preset_key:
        raise ToolError(
            "no preset selected",
            "Pass --preset <key> or re-init the project with a preset. Available: " + ", ".join(sorted(assets["presets"]["presets"])),
            EXIT_PREREQ,
        )
    if preset_key not in assets["presets"]["presets"]:
        raise ToolError(f"unknown preset '{preset_key}'", "Available: " + ", ".join(sorted(assets["presets"]["presets"])))
    weights = parse_weights(args.weights, assets["presets"]) or state.get("weights", {})
    target = args.target_toolchain or state.get("target_toolchain")
    requirements = None
    requirements_path = os.path.join(artifact_root, "requirements-backlog.json")
    if os.path.isfile(requirements_path):
        requirements = ENGINE.load_json(requirements_path)
    notes = args.notes or state.get("request", "")

    try:
        payload = ENGINE.build_architecture(
            assets["presets"],
            preset_key,
            project_name=state["name"],
            requirements=requirements,
            weights=weights,
            candidate=args.stack or state.get("stack") or None,
            target_version=target,
            notes=notes,
        )
    except ValueError as error:
        raise ToolError(f"could not build the architecture: {error}", "Check the candidate key and weighting criteria.") from error

    md_path = os.path.join(artifact_root, "architecture-decision.md")
    json_path = os.path.join(artifact_root, "architecture-decision.json")
    meta_md = _write_text(md_path, ENGINE.render_architecture_md(payload, assets["workflow"]))
    meta_json = _write_json(json_path, payload)

    facts = {
        "stack_decision_ranked": len(payload["stack_decision"]["candidates"]) >= 2,
        "components_defined": len(payload["components"]) >= 3,
        "data_and_storage": bool(payload["data_model"]),
        "deployment_target": bool(payload["deployment"]),
        "risks_with_mitigation": sum(1 for risk in payload["risks"] if risk.get("mitigation")) >= 3,
        "adr_recorded": bool(payload["adr"].get("id")),
        "version_policy": bool(payload["version_policy"]["target_version"]) and payload["version_policy"]["target_version"] != "UNRESOLVED",
    }
    details = {
        "version_policy": "target toolchain version recorded" if facts["version_policy"] else "target toolchain version unresolved: pass --target-toolchain (for example python3.12, node22, api35)",
        "stack_decision_ranked": f"chosen {payload['chosen']['key']} at {payload['chosen']['weighted_score']}/5 across {len(payload['stack_decision']['candidates'])} candidates",
        "risks_with_mitigation": f"{len(payload['risks'])} risks recorded",
    }
    gate = ENGINE.evaluate_stage_gate("architecture", {"facts": facts, "details": details}, assets["workflow"])
    artifacts = [
        {"path": _rel(md_path, project_dir), **meta_md},
        {"path": _rel(json_path, project_dir), **meta_json},
    ]
    state["preset"] = preset_key
    state["stack"] = payload["chosen"]["key"]
    state["weights"] = weights
    if target:
        state["target_toolchain"] = target
    _stage_record(state, "architecture", artifacts, gate, facts, details)
    extra = [
        f"chosen stack: {payload['chosen']['label']} ({payload['chosen']['weighted_score']}/5)",
        f"target toolchain: {target or 'UNRESOLVED'}",
    ]
    return _finish_stage(args, project_dir, state, "architecture", artifacts, gate, extra)


def stage_tests(args, assets, project_dir: str, artifact_root: str, state: dict) -> int:
    requirements_path = os.path.join(artifact_root, "requirements-backlog.json")
    if not os.path.isfile(requirements_path):
        raise ToolError(
            "tests stage needs the requirements backlog",
            "Run 'stage requirements' first; the test strategy is mapped onto the acceptance criteria of the backlog.",
            EXIT_PREREQ,
        )
    requirements = ENGINE.load_json(requirements_path)
    payload = ENGINE.build_test_plan(requirements, assets["workflow"])

    md_path = os.path.join(artifact_root, "test-strategy.md")
    json_path = os.path.join(artifact_root, "test-strategy.json")
    meta_md = _write_text(md_path, ENGINE.render_test_plan_md(payload))
    meta_json = _write_json(json_path, payload)

    coverage = payload["coverage"]
    cross_levels = {item["level"] for item in payload["cross_cutting"]}
    facts = {
        "criteria_mapped": coverage["criteria_total"] > 0 and not coverage["unmapped"],
        "levels_covered": len(payload["levels_used"]) >= 3,
        "cross_cutting_suite": {"security", "accessibility", "performance", "recovery"} <= cross_levels,
        "data_and_isolation": bool(payload["data_and_isolation"]["isolation"]),
        "regression_policy": bool(payload["regression_policy"]),
    }
    details = {
        "criteria_mapped": f"{coverage['criteria_mapped']}/{coverage['criteria_total']} criteria mapped",
        "levels_covered": "levels: " + ", ".join(payload["levels_used"]),
        "cross_cutting_suite": "cross-cutting levels: " + ", ".join(sorted(cross_levels)),
    }
    gate = ENGINE.evaluate_stage_gate("tests", {"facts": facts, "details": details}, assets["workflow"])
    artifacts = [
        {"path": _rel(md_path, project_dir), **meta_md},
        {"path": _rel(json_path, project_dir), **meta_json},
    ]
    _stage_record(state, "tests", artifacts, gate, facts, details)
    extra = [f"test cases: {len(payload['cases'])}  criteria mapped: {coverage['criteria_mapped']}/{coverage['criteria_total']}"]
    return _finish_stage(args, project_dir, state, "tests", artifacts, gate, extra)


def stage_security(args, assets, project_dir: str, artifact_root: str, state: dict) -> int:
    limits = assets["workflow"].get("limits", {})
    if not args.evidence:
        raise ToolError(
            "security stage needs an evidence file",
            "Provide a JSON file mapping control ids to status: {\"reviewer\": \"you\", \"controls\": {\"SEC-01\": {\"status\": \"pass\", \"note\": \"...\"}}}. "
            "Unevidenced controls are reported as unknown and block the gate.",
            EXIT_PREREQ,
        )
    evidence_path = os.path.abspath(args.evidence)
    raw = _read_text(evidence_path, int(limits.get("max_input_bytes", 2000000)))
    try:
        evidence = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ToolError(f"evidence file is not valid JSON: {error}", "Use {\"reviewer\": \"...\", \"controls\": {\"SEC-01\": {\"status\": \"pass\"}}}") from error
    if not isinstance(evidence, dict):
        raise ToolError("evidence file must contain a JSON object", "Top level must be an object with 'controls'.")

    preset_key = state.get("preset")
    try:
        payload = ENGINE.evaluate_security(assets["controls"], preset_key, evidence, project_name=state["name"])
    except ValueError as error:
        raise ToolError(f"evidence rejected: {error}", "Use control ids and statuses listed in asset-security-controls.json.") from error

    md_path = os.path.join(artifact_root, "security-review.md")
    json_path = os.path.join(artifact_root, "security-review.json")
    meta_md = _write_text(md_path, ENGINE.render_security_md(payload))
    meta_json = _write_json(json_path, payload)

    facts = {
        "no_open_critical_high": not payload["open_critical_high"],
        "evidence_complete": not payload["unevidenced"],
        "medium_tracked": not payload["medium_untracked"],
        "privacy_disclosure": not payload["privacy_gaps"],
        "secrets_hygiene": not payload["secrets_gaps"],
    }
    details = {
        "no_open_critical_high": "open critical/high: " + (", ".join(payload["open_critical_high"]) or "none"),
        "evidence_complete": f"{len(payload['unevidenced'])} controls still unknown",
        "privacy_disclosure": "privacy gaps: " + (", ".join(payload["privacy_gaps"]) or "none"),
        "secrets_hygiene": "secret-handling gaps: " + (", ".join(payload["secrets_gaps"]) or "none"),
    }
    gate = ENGINE.evaluate_stage_gate("security", {"facts": facts, "details": details}, assets["workflow"])
    artifacts = [
        {"path": _rel(md_path, project_dir), **meta_md},
        {"path": _rel(json_path, project_dir), **meta_json},
    ]
    _stage_record(state, "security", artifacts, gate, facts, details)
    extra = [
        f"controls in scope: {payload['controls_total']}  open critical/high: {len(payload['open_critical_high'])}  unevidenced: {len(payload['unevidenced'])}",
    ]
    if payload["unevidenced"]:
        extra.append("recovery: add evidence for " + ", ".join(payload["unevidenced"][:8]) + " and re-run 'stage security'.")
    return _finish_stage(args, project_dir, state, "security", artifacts, gate, extra)


def stage_packaging(args, assets, project_dir: str, artifact_root: str, state: dict) -> int:
    limits = assets["workflow"].get("limits", {})
    version_current = args.current_version or state.get("version", "1.0.0")
    try:
        ENGINE.parse_semver(version_current)
    except ValueError as error:
        raise ToolError(str(error), "Pass --current-version MAJOR.MINOR.PATCH.") from error

    commits = []
    if args.commits:
        commits = ENGINE.parse_commits(_read_text(os.path.abspath(args.commits), int(limits.get("max_input_bytes", 2000000))))
    version_info = ENGINE.next_version(version_current, commits, bump=args.bump)
    if args.version:
        ENGINE.parse_semver(args.version)
        version_info = {
            "previous": version_current,
            "version": args.version,
            "bump": args.bump,
            "signals": version_info["signals"],
        }

    exclude = excluded_paths(project_dir, artifact_root)
    try:
        manifest = ENGINE.compute_manifest(project_dir, limits, version_info["version"], exclude_paths=exclude)
    except ValueError as error:
        raise ToolError(f"could not build the release manifest: {error}", "Narrow --project-dir or raise the limits in asset-factory-workflow.json.") from error
    release = ENGINE.build_release(manifest, version_info, commits, state["name"], state.get("preset"), notes=args.notes or "")

    manifest_path = os.path.join(artifact_root, "release-manifest.json")
    notes_path = os.path.join(artifact_root, "release-notes.md")
    checksums_path = os.path.join(artifact_root, "checksums-sha256.txt")
    meta_manifest = _write_json(manifest_path, manifest)
    meta_notes = _write_text(notes_path, ENGINE.render_release_md(release, version_info, manifest))
    checksum_lines = "\n".join(f"{entry['sha256']}  {entry['path']}" for entry in manifest["files"])
    meta_checksums = _write_text(checksums_path, checksum_lines or "(no files in release set)")

    blocked_skips = [item for item in manifest["skipped"] if item["reason"] != "excluded by request"]
    facts = {
        "manifest_complete": manifest["file_count"] > 0 and not blocked_skips,
        "version_valid": bool(commits) or version_info["bump"] in {"major", "minor", "patch"} or bool(args.version),
        "changelog_generated": bool(release["changelog"]),
        "sbom_written": bool(release["sbom"]["components"]) or bool(release["sbom"]["dependency_manifests"]),
        "sensitive_files": not manifest["sensitive_files"],
        "rollback_notes": bool(args.rollback_note),
    }
    details = {
        "manifest_complete": f"{manifest['file_count']} files hashed, {len(blocked_skips)} skipped",
        "version_valid": f"{version_info['previous']} -> {version_info['version']} ({version_info['bump']})"
        + ("" if facts["version_valid"] else "; no change set supplied, so 'auto' could not justify a bump"),
        "sensitive_files": "credential-like files: " + (", ".join(item["path"] for item in manifest["sensitive_files"]) or "none"),
        "changelog_generated": f"{sum(len(items) for items in release['changelog'].values())} change entries",
        "rollback_notes": "rollback note supplied" if facts["rollback_notes"] else "no --rollback-note supplied; release notes carry the generic recovery section only",
    }
    gate = ENGINE.evaluate_stage_gate("packaging", {"facts": facts, "details": details}, assets["workflow"])
    artifacts = [
        {"path": _rel(manifest_path, project_dir), **meta_manifest},
        {"path": _rel(notes_path, project_dir), **meta_notes},
        {"path": _rel(checksums_path, project_dir), **meta_checksums},
    ]
    state["version"] = version_info["version"]
    _stage_record(state, "packaging", artifacts, gate, facts, details)
    extra = [
        f"version: {version_info['previous']} -> {version_info['version']} ({version_info['bump']})",
        f"files hashed: {manifest['file_count']}  sensitive: {len(manifest['sensitive_files'])}  skipped: {len(blocked_skips)}",
    ]
    if manifest["sensitive_files"]:
        extra.append("recovery: remove credential-like files from the tree (" + ", ".join(item["path"] for item in manifest["sensitive_files"][:5]) + ") and rotate any exposed secret.")
    return _finish_stage(args, project_dir, state, "packaging", artifacts, gate, extra)


def stage_readiness(args, assets, project_dir: str, artifact_root: str, state: dict) -> int:
    limits = assets["workflow"].get("limits", {})
    payload = ENGINE.evaluate_readiness(
        state,
        assets["workflow"],
        project_dir,
        artifact_root,
        limits,
        require_verification=None if args.require_verification is None else args.require_verification,
        exclude_paths=excluded_paths(project_dir, artifact_root),
    )
    # Recompute the readiness-stage gate from the aggregate facts.
    facts = {
        "stages_complete": all(stage["status"] != "missing" and stage["pass"] for stage in payload["stages"]),
        "artifacts_stable": not payload["drift"] and not payload["missing_artifacts"],
        "verification_current": payload["verification_present"] and not payload["verification_stale"] and payload["verification_score"] > 0,
        "no_blockers": not payload["blockers"],
        "advisories_reviewed": bool(args.accept_advisories) or not payload["advisories"],
    }
    details = {
        "stages_complete": ", ".join(f"{stage['stage']}={'pass' if stage['pass'] else stage['status']}" for stage in payload["stages"]),
        "artifacts_stable": f"{len(payload['drift'])} modified, {len(payload['missing_artifacts'])} missing artifacts",
        "verification_current": (
            "no verification run recorded"
            if not payload["verification_present"]
            else f"verification score {payload['verification_score']}, stale={payload['verification_stale']}"
        ),
        "no_blockers": f"{len(payload['blockers'])} blocker(s)",
    }
    gate = ENGINE.evaluate_stage_gate("readiness", {"facts": facts, "details": details}, assets["workflow"])

    md_path = os.path.join(artifact_root, "readiness-report.md")
    json_path = os.path.join(artifact_root, "readiness-report.json")
    meta_md = _write_text(md_path, ENGINE.render_readiness_md(payload))
    meta_json = _write_json(json_path, payload)
    artifacts = [
        {"path": _rel(md_path, project_dir), **meta_md},
        {"path": _rel(json_path, project_dir), **meta_json},
    ]
    _stage_record(state, "readiness", artifacts, gate, facts, details)
    extra = [
        f"score: {payload['score']}/100 (pass >= {payload['pass_threshold']})",
        f"release ready: {'YES' if payload['release_ready'] else 'NO'}",
    ]
    for blocker in payload["blockers"][:8]:
        extra.append(f"blocker: {blocker['source']}/{blocker['id']}: {blocker['detail']}")
    return _finish_stage(args, project_dir, state, "readiness", artifacts, gate, extra)


STAGE_HANDLERS = {
    "requirements": stage_requirements,
    "architecture": stage_architecture,
    "tests": stage_tests,
    "security": stage_security,
    "packaging": stage_packaging,
    "readiness": stage_readiness,
}


def cmd_stage(args, assets) -> int:
    project_dir, artifact_root = resolve_paths(args)
    state = load_state(project_dir)
    stage_def = next(stage for stage in assets["workflow"]["stages"] if stage["id"] == args.stage)
    missing = [stage_id for stage_id in stage_def.get("requires", []) if stage_id not in state.get("stages", {})]
    args.prereq_warnings = [
        f"warning: prerequisite stage '{stage_id}' has not been run; the readiness gate will report it as missing"
        for stage_id in missing
    ]
    handler = STAGE_HANDLERS[args.stage]
    return handler(args, assets, project_dir, artifact_root, state)


def cmd_verify(args, assets) -> int:
    project_dir, artifact_root = resolve_paths(args)
    state = load_state(project_dir)
    preset_key = args.preset or state.get("preset")
    if not preset_key:
        raise ToolError(
            "no preset selected, so there are no verification commands to plan",
            "Pass --preset <key> or re-init with a preset. Available: " + ", ".join(sorted(assets["presets"]["presets"])),
            EXIT_PREREQ,
        )
    preset = assets["presets"]["presets"].get(preset_key)
    if preset is None:
        raise ToolError(f"unknown preset '{preset_key}'", "Available: " + ", ".join(sorted(assets["presets"]["presets"])))

    try:
        plan = RUNNER.build_plan(
            preset_key,
            preset,
            assets["workflow"].get("limits", {}),
            project_dir,
            extra_commands=args.extra_command,
            project_name=state.get("name", ""),
        )
        report = RUNNER.execute_plan(plan, project_dir, args.approve, dry_run=args.dry_run)
    except RUNNER.ApprovalRequired as error:
        raise ToolError(
            str(error),
            f"Review the plan with 'verify --dry-run', then re-run with --approve {error.token}. Nothing was executed.",
            EXIT_APPROVAL,
        ) from error
    except ValueError as error:
        raise ToolError(f"verification plan rejected: {error}", "Fix the command or the preset; the allowlist lives in asset-factory-workflow.json limits.verify.") from error

    if args.dry_run:
        md_path = os.path.join(artifact_root, "verification-plan.md")
        meta = _write_text(md_path, RUNNER.render_report_md(report))
        save_state(project_dir, state)
        emit(
            args,
            {"report": report, "plan_artifact": {"path": _rel(md_path, project_dir), **meta}},
            [
                f"verification plan for preset '{preset_key}': {len(plan['commands'])} commands (nothing executed)",
                f"approval token: {report['token']}",
                "",
            ]
            + [f"  {'required' if command['required'] else 'optional'}  {command['timeout_seconds']}s  {command['command']}" for command in plan["commands"]]
            + ["", f"next: verify --project-dir {args.project_dir} --approve {report['token']}"],
        )
        return EXIT_OK

    digest_info = ENGINE.tree_digest(project_dir, assets["workflow"].get("limits", {}), excluded_paths(project_dir, artifact_root))
    digest, file_count = digest_info["digest"], digest_info["file_count"]
    state["verification"] = {
        "token": report["token"],
        "pass": report["pass"],
        "score": report["score"],
        "detail": report["detail"],
        "generated_utc": report["generated_utc"],
        "duration_seconds": report.get("duration_seconds", 0),
        "tree_digest": digest,
        "files_digested": file_count,
        "require_verification": True,
    }
    md_path = os.path.join(artifact_root, "verification-report.md")
    json_path = os.path.join(artifact_root, "verification-report.json")
    meta_md = _write_text(md_path, RUNNER.render_report_md(report))
    meta_json = _write_json(json_path, report)
    record_event(state, "verify", f"pass={report['pass']} score={report['score']}")
    save_state(project_dir, state)

    lines = [
        f"verification: {'PASS' if report['pass'] else 'FAIL'} ({report['score']}/100) — {report['detail']}",
        f"files digested: {file_count}  tree digest: {digest[:16]}...",
        "",
    ]
    for result in report["results"]:
        lines.append(f"  {result['status']:<8} {'required' if result['required'] else 'optional'}  {result['id']}  ({result['duration_seconds']}s)")
        if result["status"] != "passed" and result.get("reason"):
            lines.append(f"           reason: {result['reason'][:160]}")
    lines += ["", f"report: {_rel(md_path, project_dir)}"]
    if not report["pass"]:
        lines.append("recovery: fix the failing commands, or mark genuinely optional commands as required=false in asset-stack-presets.json; then re-run verify.")
    emit(args, {"report": report, "artifacts": [{"path": _rel(md_path, project_dir), **meta_md}, {"path": _rel(json_path, project_dir), **meta_json}]}, lines)
    return EXIT_OK if report["pass"] else EXIT_GATE


def cmd_boundary(args, assets) -> int:
    project_dir = os.path.abspath(args.project_dir) if args.project_dir else None
    preset_key = args.preset
    if preset_key is None and project_dir and os.path.isfile(state_path(project_dir)):
        preset_key = ENGINE.load_json(state_path(project_dir)).get("preset")
    payload = ENGINE.boundary_check(assets["presets"], preset_key, assets["workflow"], project_dir)
    lines = [
        f"preset: {payload.get('preset') or 'all presets'}",
        f"required toolchain complete here: {'YES' if payload['can_verify_here'] else 'NO'}",
        "",
    ]
    for item in payload["toolchain"]:
        if item["required"] or args.verbose:
            status = "available" if item["available"] else "MISSING"
            lines.append(f"  {item['preset']:<18} {item['executable']:<12} {'required' if item['required'] else 'optional'}  {status}")
    for item in payload["missing_artifact_checks"]:
        lines.append(f"  missing artifact ({item['severity']}): {item['label']} — expected {item['glob']}")
    if payload["escalations"]:
        lines += ["", "escalations:"] + [f"  - {item}" for item in payload["escalations"]]
    emit(args, payload, lines)
    return EXIT_OK if payload["can_verify_here"] else EXIT_BLOCKED


def cmd_status(args, assets) -> int:
    project_dir, _ = resolve_paths(args)
    state = load_state(project_dir)
    artifact_root = os.path.join(project_dir, state.get("artifact_root", DEFAULT_ARTIFACT_ROOT))
    stages = []
    for stage in assets["workflow"]["stages"]:
        record = state.get("stages", {}).get(stage["id"])
        if not record:
            stages.append({"stage": stage["id"], "status": "not_run", "score": None, "artifacts_ok": None})
            continue
        artifacts_ok = True
        for artifact in record.get("artifacts", []):
            path = os.path.join(project_dir, artifact["path"])
            if not os.path.isfile(path):
                artifacts_ok = False
                break
            digest, _ = ENGINE.sha256_file(path)
            if digest != artifact.get("sha256"):
                artifacts_ok = False
                break
        stages.append(
            {
                "stage": stage["id"],
                "status": record.get("status"),
                "score": record.get("gate", {}).get("score"),
                "pass": record.get("gate", {}).get("pass"),
                "failed": record.get("gate", {}).get("failed", []),
                "completed_utc": record.get("completed_utc"),
                "artifacts_ok": artifacts_ok,
                "artifacts": [artifact["path"] for artifact in record.get("artifacts", [])],
            }
        )
    next_stage = next((item["stage"] for item in stages if item["status"] == "not_run"), None)
    failing_stage = next((item["stage"] for item in stages if item.get("pass") is False), None)
    payload = {
        "project": state.get("name"),
        "preset": state.get("preset"),
        "stack": state.get("stack"),
        "version": state.get("version"),
        "target_toolchain": state.get("target_toolchain"),
        "stages": stages,
        "verification": state.get("verification", {}),
        "next_stage": next_stage,
        "failing_stage": failing_stage,
        "history": state.get("history", [])[-10:],
    }
    lines = [
        f"project: {payload['project']}  preset: {payload['preset'] or 'none'}  stack: {payload['stack'] or 'auto'}  version: {payload['version']}",
        "",
        "stage                status        score   artifacts",
    ]
    for item in stages:
        score = "-" if item["score"] is None else f"{item['score']}"
        artifacts = "n/a" if item["artifacts_ok"] is None else ("ok" if item["artifacts_ok"] else "MISSING/CHANGED")
        lines.append(f"  {item['stage']:<18} {item['status']:<12} {score:<7} {artifacts}")
        if item.get("failed"):
            lines.append(f"      failed: {', '.join(item['failed'])}")
    verification = payload["verification"]
    if verification:
        lines += ["", f"verification: {'PASS' if verification.get('pass') else 'FAIL'} ({verification.get('score')}/100) at {verification.get('generated_utc')}"]
    else:
        lines += ["", "verification: not run"]
    if next_stage:
        lines += ["", f"next: stage {next_stage} --project-dir {args.project_dir}"]
    elif failing_stage:
        lines += ["", f"next: clear the failed criteria in stage {failing_stage}, then re-run it and 'stage readiness'"]
    else:
        lines += ["", "next: every stage gate passes; re-run verify after any change and archive the readiness report with the release"]
    emit(args, payload, lines)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools-app-factory.py",
        description=(
            "App Factory Delivery Orchestrator: route a delivery request, generate the artifacts each factory stage needs, "
            "gate them, run bounded verification, and report release readiness from checks that actually ran."
        ),
        epilog=(
            "examples:\n"
            "  python3 tools-app-factory.py capabilities\n"
            "  python3 tools-app-factory.py route --request \"go service with grpc\" --platform backend\n"
            "  python3 tools-app-factory.py init --project-dir ./demo --name Demo --preset python-cli --target-toolchain python3.13\n"
            "  python3 tools-app-factory.py stage requirements --project-dir ./demo --input reqs.md\n"
            "  python3 tools-app-factory.py stage architecture --project-dir ./demo\n"
            "  python3 tools-app-factory.py stage tests --project-dir ./demo\n"
            "  python3 tools-app-factory.py stage security --project-dir ./demo --evidence evidence.json\n"
            "  python3 tools-app-factory.py stage packaging --project-dir ./demo --commits commits.txt --rollback-note \"redeploy previous wheel\"\n"
            "  python3 tools-app-factory.py verify --project-dir ./demo --dry-run\n"
            "  python3 tools-app-factory.py verify --project-dir ./demo --approve APP-XXXXXXXXXX\n"
            "  python3 tools-app-factory.py stage readiness --project-dir ./demo\n"
            "  python3 tools-app-factory.py status --project-dir ./demo\n"
            "\nSee knowledge-readme.md for schemas, limits, approval rules and exit codes.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--format", choices=["text", "json"], default="text", help="output format (default: text)")
    parser.add_argument("--version", action="version", version=f"app-factory-delivery {TOOL_VERSION}")
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    capabilities = subparsers.add_parser("capabilities", help="list operational capabilities and catalog coverage")
    capabilities.set_defaults(func=cmd_capabilities)

    catalog = subparsers.add_parser("catalog", help="query or import capability catalogs")
    catalog_sub = catalog.add_subparsers(dest="catalog_command", metavar="subcommand")
    catalog_list = catalog_sub.add_parser("list", help="list categories and skills")
    catalog_list.add_argument("--category", help="category index, slug or exact name")
    catalog_list.add_argument("--brief", action="store_true", help="categories only")
    catalog_search = catalog_sub.add_parser("search", help="term-weighted search")
    catalog_search.add_argument("--query", required=True, help="search terms")
    catalog_search.add_argument("--category", help="restrict to one category")
    catalog_search.add_argument("--limit", type=int, default=20, help="max results (default 20)")
    catalog_show = catalog_sub.add_parser("show", help="show one skill")
    catalog_show.add_argument("--skill", required=True, help="skill slug or distinctive fragment")
    catalog_import = catalog_sub.add_parser("import", help="normalize a plain-text, Markdown or HTML capability list")
    catalog_import.add_argument("--input", required=True, help=".txt, .md or .html file")
    catalog_import.add_argument("--out", help="write normalized catalog JSON here (default: print to stdout)")
    catalog_import.add_argument("--source-format", dest="source_format", default="auto", choices=["auto", "text", "markdown", "html"], help="input format hint (default: auto)")
    catalog_import.add_argument("--default-category", default="General Capabilities", help="category for entries before any category marker")
    catalog_import.add_argument("--merge", help="merge into an existing normalized catalog")
    catalog.set_defaults(func=cmd_catalog)

    route = subparsers.add_parser("route", help="route a request to skills, presets and stages")
    route.add_argument("--request", help="free-text delivery request")
    route.add_argument("--request-file", help="read the request from a file instead")
    route.add_argument("--platform", help="web, backend, mobile or desktop")
    route.add_argument("--language", help="requested language, for example python or kotlin")
    route.add_argument("--category", help="restrict to one catalog category")
    route.add_argument("--limit", type=int, default=12, help="max skills (default 12)")
    route.set_defaults(func=cmd_route)

    init = subparsers.add_parser("init", help="create factory state for a project")
    init.add_argument("--project-dir", required=True, help="project directory")
    init.add_argument("--name", help="project name (default: directory name)")
    init.add_argument("--preset", help="delivery preset key")
    init.add_argument("--stack", help="candidate stack key defined by the preset")
    init.add_argument("--weights", help="criterion weights, for example performance=0.4,security=0.3")
    init.add_argument("--version", dest="version", help="current semantic version (default 1.0.0)")
    init.add_argument("--target-toolchain", help="target language/framework/SDK version, for example python3.13 or node22")
    init.add_argument("--reason", help="recorded delivery request (used when no requirements file is supplied)")
    init.add_argument("--force", action="store_true", help="allow initializing a non-empty directory")
    init.add_argument("--approve", help="approval keyword required with --force ('overwrite')")
    init.set_defaults(func=cmd_init)

    stage = subparsers.add_parser("stage", help="run one factory stage")
    stage_sub = stage.add_subparsers(dest="stage", metavar="stage", required=True)
    common = dict()

    requirements = stage_sub.add_parser("requirements", help="backlog, acceptance criteria and NFR coverage")
    requirements.add_argument("--project-dir", required=True)
    requirements.add_argument("--out", help=f"artifact directory inside the project (default {DEFAULT_ARTIFACT_ROOT})")
    requirements.add_argument("--input", help="requirements file (.md or .txt)")
    requirements.add_argument("--requirement", action="append", help="inline requirement line (repeatable)")
    requirements.add_argument("--persona", default="end user", help="default persona (default: end user)")
    requirements.add_argument("--strict", action="store_true", help="exit 3 when the stage gate fails")
    requirements.set_defaults(func=cmd_stage)

    architecture = stage_sub.add_parser("architecture", help="stack decision, components, risks and ADR")
    architecture.add_argument("--project-dir", required=True)
    architecture.add_argument("--out")
    architecture.add_argument("--preset", help="override the recorded preset")
    architecture.add_argument("--stack", help="force a candidate stack key")
    architecture.add_argument("--weights", help="criterion weights, for example cost=0.3,performance=0.3")
    architecture.add_argument("--target-toolchain", help="target language/framework/SDK version")
    architecture.add_argument("--notes", help="decision context recorded in the ADR")
    architecture.add_argument("--strict", action="store_true")
    architecture.set_defaults(func=cmd_stage)

    tests = stage_sub.add_parser("tests", help="risk-based test strategy mapped to acceptance criteria")
    tests.add_argument("--project-dir", required=True)
    tests.add_argument("--out")
    tests.add_argument("--strict", action="store_true")
    tests.set_defaults(func=cmd_stage)

    security = stage_sub.add_parser("security", help="security and privacy control review")
    security.add_argument("--project-dir", required=True)
    security.add_argument("--out")
    security.add_argument("--evidence", help="JSON evidence file mapping control ids to status (required to run the stage)")
    security.add_argument("--strict", action="store_true")
    security.set_defaults(func=cmd_stage)

    packaging = stage_sub.add_parser("packaging", help="manifest, checksums, semver bump, changelog and SBOM")
    packaging.add_argument("--project-dir", required=True)
    packaging.add_argument("--out")
    packaging.add_argument("--commits", help="file of conventional commit lines")
    packaging.add_argument("--bump", default="auto", choices=["auto", "major", "minor", "patch", "none"])
    packaging.add_argument("--current-version", help="override the recorded version")
    packaging.add_argument("--version", dest="version", help="force the resulting version")
    packaging.add_argument("--rollback-note", help="rollback or recovery plan for this release")
    packaging.add_argument("--notes", help="free-text release notes")
    packaging.add_argument("--strict", action="store_true")
    packaging.set_defaults(func=cmd_stage)

    readiness = stage_sub.add_parser("readiness", help="aggregate gates into a release-readiness report")
    readiness.add_argument("--project-dir", required=True)
    readiness.add_argument("--out")
    readiness.add_argument("--accept-advisories", action="store_true", help="mark non-blocking advisories as reviewed")
    readiness.add_argument("--require-verification", dest="require_verification", action="store_true", default=None)
    readiness.add_argument("--no-require-verification", dest="require_verification", action="store_false")
    readiness.add_argument("--strict", action="store_true")
    readiness.set_defaults(func=cmd_stage)

    verify = subparsers.add_parser("verify", help="plan and run bounded verification commands")
    verify.add_argument("--project-dir", required=True)
    verify.add_argument("--out")
    verify.add_argument("--preset", help="override the recorded preset")
    verify.add_argument("--dry-run", action="store_true", help="show the plan and approval token without executing")
    verify.add_argument("--approve", help="exact approval token printed by --dry-run")
    verify.add_argument("--extra-command", dest="extra_command", action="append", help="extra command with an allowlisted executable (repeatable)")
    verify.set_defaults(func=cmd_verify)

    boundary = subparsers.add_parser("boundary", help="report missing toolchains and artifacts")
    boundary.add_argument("--preset", help="preset to check (default: the recorded preset)")
    boundary.add_argument("--project-dir", help="project directory for artifact checks")
    boundary.add_argument("--verbose", action="store_true", help="also list optional executables")
    boundary.set_defaults(func=cmd_boundary)

    status = subparsers.add_parser("status", help="show recorded state and the next action")
    status.add_argument("--project-dir", required=True)
    status.add_argument("--out")
    status.set_defaults(func=cmd_status)

    _add_subcommand_format(parser)
    return parser


def _add_subcommand_format(parser: argparse.ArgumentParser) -> None:
    """Allow --format after any subcommand without clobbering the global value."""
    containers = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)]
    for container in containers:
        for subparser in container.choices.values():
            _add_subcommand_format(subparser)
            existing = {option for action in subparser._actions for option in action.option_strings}
            if "--format" not in existing:
                subparser.add_argument(
                    "--format",
                    choices=["text", "json"],
                    default=argparse.SUPPRESS,
                    help="output format (default: text)",
                )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    if args.command == "catalog" and not getattr(args, "catalog_command", None):
        _print_error("catalog needs a subcommand", "Use: catalog list | catalog search | catalog show | catalog import", EXIT_USAGE, getattr(args, "format", "text"))
        return EXIT_USAGE
    try:
        assets = load_assets()
        return args.func(args, assets)
    except ToolError as error:
        _print_error(error.message, error.hint, error.code, getattr(args, "format", "text"))
        return error.code
    except RUNNER.ApprovalRequired as error:
        _print_error(str(error), f"Approve with --approve {error.token} after reviewing --dry-run.", EXIT_APPROVAL, getattr(args, "format", "text"))
        return EXIT_APPROVAL
    except (ValueError, KeyError) as error:
        _print_error(f"invalid input: {error}", "Run --help for the accepted arguments.", EXIT_USAGE, getattr(args, "format", "text"))
        return EXIT_USAGE
    except OSError as error:
        _print_error(f"filesystem error: {error}", "Check paths, permissions and free space.", EXIT_USAGE, getattr(args, "format", "text"))
        return EXIT_USAGE
    except KeyboardInterrupt:
        _print_error("interrupted before completion", "No further work was performed; re-run the command when ready.", 130, getattr(args, "format", "text"))
        return 130


def _print_error(message: str, hint: str, code: int, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps({"ok": False, "error": message, "hint": hint, "exit_code": code}, indent=2, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"error: {message}", file=sys.stderr)
        if hint:
            print(f"hint: {hint}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
