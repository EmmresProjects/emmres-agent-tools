#!/usr/bin/env python3
"""tools-story-narrative.py - primary entry point for the story-narrative-studio Skill Tool.

Runnable, standard-library-only CLI that turns narrative planning, production
and revision work into validated, versioned artifacts.

    python3 tools-story-narrative.py help
    python3 tools-story-narrative.py capabilities
    python3 tools-story-narrative.py outline --title "..." --format novel --genre fantasy

See knowledge-readme.md for the full command reference. All paths in commands
are relative to the future skill root, e.g.
``python3 story-narrative-studio/tools-story-narrative.py ...``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True  # keep the packaged folder free of __pycache__

import importlib.util as _ilu

_CORE_SPEC = _ilu.spec_from_file_location("sns_cli_core", os.path.join(HERE, "tools-narrative-core.py"))
core = _ilu.module_from_spec(_CORE_SPEC)
_CORE_SPEC.loader.exec_module(core)
sys.modules["sns_core"] = core

_generators = None
_audit = None
_revision = None


def generators():
    global _generators
    if _generators is None:
        _generators = core.load_sibling_module("tools-narrative-generators.py")
    return _generators


def audit_mod():
    global _audit
    if _audit is None:
        _audit = core.load_sibling_module("tools-manuscript-audit.py")
    return _audit


def revision_mod():
    global _revision
    if _revision is None:
        _revision = core.load_sibling_module("tools-revision-planner.py")
    return _revision


VERSION = core.TOOL_VERSION
DEFAULT_OUT = "story-narrative-out"

OPERATIONS = [
    ("capabilities", "List every operation, its options and the catalog categories it serves."),
    ("catalog", "Search the 37 categories and 444 named capabilities from the supplied catalog."),
    ("brief", "Build a production brief: promise, audience envelope, boundaries, budgets, acceptance criteria."),
    ("premise", "Generate distinct premise directions with loglines, pressure, lie believed and climax cost."),
    ("outline", "Generate a beat sheet for a structure preset with act budgets, tension targets and scene counts."),
    ("characters", "Generate a cast sheet with wants, needs, wounds, voice, relationships and theme answers."),
    ("scenes", "Turn an outline into scene cards with goal, obstacle, tactic ladder, turn and word budget."),
    ("comic", "Break a comic, manga or manhwa chapter into pages, panels, splashes and lettering budgets."),
    ("game", "Build and validate a branch node graph with choices, flags, routes and reachable endings."),
    ("export", "Export a synopsis, treatment, style sheet, pitch or bible summary from a project bundle."),
    ("audit", "Score a manuscript against the 14-dimension rubric with measurable evidence and a verdict."),
    ("continuity", "Validate a continuity bible and cross-check it against the manuscript."),
    ("verse", "Analyse poem or lyric prosody: syllables, rhyme scheme, stanzas, refrains, meter estimate."),
    ("revise", "Convert audit findings into an ordered, approval-gated revision plan."),
    ("self-test", "Run the bundled smoke tests end to end in a temporary directory."),
]


# --------------------------------------------------------------------------
# Argument construction
# --------------------------------------------------------------------------
def add_common(parser):
    group = parser.add_argument_group("shared options")
    group.add_argument("--out", default=DEFAULT_OUT,
                       help="output directory (default: %s). Created if missing." % DEFAULT_OUT)
    group.add_argument("--name", default=None,
                       help="output filename inside --out; defaults to a generated name.")
    group.add_argument("--project", default=None,
                       help="project bundle JSON path; stores this operation's result and reuses earlier sections.")
    group.add_argument("--json", action="store_true", dest="as_json",
                       help="print the structured result envelope to stdout.")
    group.add_argument("--stdout", action="store_true",
                       help="print the deliverable to stdout instead of writing a file.")
    group.add_argument("--approve-overwrite", action="store_true",
                       help="allow replacing an existing output file (required for any overwrite).")
    group.add_argument("--timeout", type=float, default=60.0,
                       help="soft time budget in seconds for the operation (default 60, max 600).")
    group.add_argument("--quiet", action="store_true", help="suppress the human summary line.")


def add_story_args(parser, required=True, structural=True):
    group = parser.add_argument_group("story specification")
    group.add_argument("--title", default=None, help="working title.")
    group.add_argument("--format", default=None,
                       help="novel, novella, short-story, flash, screenplay, tv, comic, manga, "
                            "visual-novel, interactive-fiction, ttrpg, audio-drama, stage, poetry, "
                            "lyrics, memoir, game.")
    group.add_argument("--genre", default=None,
                       help="fantasy, science-fiction, mystery, romance, horror, thriller, literary, "
                            "historical, comedy, slice-of-life, young-adult, cyberpunk, fabulation.")
    group.add_argument("--audience", default="general",
                       help="children, middle-grade, ya, adult, general (default: general).")
    group.add_argument("--words", type=int, default=None, help="target word count.")
    if structural:
        group.add_argument("--structure", default=None,
                           help="beat preset: three-act, hero-journey, save-the-cat, kishotenketsu, "
                                "seven-point, short-story, flash, tv-pilot, comic-22page, vn-route, ttrpg-session.")
        group.add_argument("--beats", type=int, default=None, help="override the number of beats.")
        group.add_argument("--scenes", type=int, default=None, help="override the number of scenes.")
        group.add_argument("--cast", type=int, default=None, help="override the cast size.")
        group.add_argument("--pages", type=int, default=None, help="page count for comic output.")
        group.add_argument("--nodes", type=int, default=None, help="node count for game output.")
        group.add_argument("--routes", type=int, default=None, help="number of routes for game output.")
    group.add_argument("--themes", default=None, help="comma separated themes.")
    group.add_argument("--non-negotiables", default=None, help="comma separated must-keep constraints.")
    group.add_argument("--boundaries", default=None, help="comma separated must-not-become constraints.")
    group.add_argument("--style", default="plain", help="voice preset: plain, lyrical, noir, warm, clinical, epic, vernacular.")
    group.add_argument("--platform", default=None, help="platform or medium constraint, free text.")
    group.add_argument("--deadline", default=None, help="milestone or deadline, free text.")
    group.add_argument("--seed", type=int, default=20260101, help="deterministic seed (default 20260101).")
    group.add_argument("--allow-custom", action="store_true",
                       help="accept a format, genre, audience or structure outside the presets "
                            "and continue with generic envelopes plus a warning.")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tools-story-narrative.py",
        description="story-narrative-studio: narrative planning, production and revision Skill Tool.",
        epilog="Run `help` for the workflow, or `capabilities` for the operation list.",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true", dest="want_help",
                        help="show this help and exit.")
    sub = parser.add_subparsers(dest="op")

    p = sub.add_parser("help", add_help=False, help="show help, workflow and examples.")
    p.add_argument("topic", nargs="?", default=None, help="operation name for detailed help.")
    add_common(p)

    p = sub.add_parser("capabilities", add_help=False,
                       help="list operations, options and the catalog categories each serves.")
    p.add_argument("--op", default=None, dest="op_filter", help="detail one operation.")
    p.add_argument("--op-json", action="store_true", help="emit the capability table as JSON.")
    p.add_argument("--categories", action="store_true", help="include the full 37-category catalog listing.")
    add_common(p)

    p = sub.add_parser("catalog", add_help=False, help="search or list catalog categories and named capabilities.")
    p.add_argument("--search", default=None, help="keyword search across names and descriptions.")
    p.add_argument("--category", default=None, help="category id or slug.")
    p.add_argument("--op", default=None, dest="op_filter",
                   help="show catalog categories that back a runnable operation.")
    p.add_argument("--limit", type=int, default=40, help="maximum rows to print (default 40).")
    add_common(p)

    p = sub.add_parser("brief", add_help=False, help="build a production brief.")
    add_story_args(p)
    add_common(p)

    p = sub.add_parser("premise", add_help=False, help="generate premise directions.")
    add_story_args(p)
    p.add_argument("--directions", type=int, default=3, help="number of directions (2-7, default 3).")
    add_common(p)

    p = sub.add_parser("outline", add_help=False, help="generate a beat sheet.")
    add_story_args(p)
    p.add_argument("--premise", default=None, help="premise JSON from a previous run, to reuse its protagonist.")
    add_common(p)

    p = sub.add_parser("characters", add_help=False, help="generate a cast sheet.")
    add_story_args(p)
    p.add_argument("--protagonist", default=None, help="force the protagonist name.")
    add_common(p)

    p = sub.add_parser("scenes", add_help=False, help="generate scene cards from an outline.")
    add_story_args(p)
    p.add_argument("--outline", default=None, help="outline JSON; falls back to --project.")
    p.add_argument("--cast-file", default=None, help="cast JSON; falls back to --project.")
    add_common(p)

    p = sub.add_parser("comic", add_help=False, help="break a comic or manga chapter into pages and panels.")
    add_story_args(p)
    add_common(p)

    p = sub.add_parser("game", add_help=False, help="build and validate a branch node graph.")
    add_story_args(p)
    add_common(p)

    p = sub.add_parser("export", add_help=False, help="export a synopsis, treatment, style sheet, pitch or bible summary.")
    p.add_argument("--kind", default="synopsis",
                   choices=["synopsis", "treatment", "style-sheet", "pitch", "bible-summary"])
    p.add_argument("--word-budget", type=int, default=500, help="target words for synopsis or treatment.")
    add_story_args(p, required=False, structural=False)
    add_common(p)

    for name, help_text in (
        ("audit", "score a manuscript against the rubric."),
        ("revise", "build a revision plan from audit findings."),
    ):
        p = sub.add_parser(name, add_help=False, help=help_text)
        p.add_argument("--input", default=None, help="manuscript file (UTF-8 .md or .txt).")
        p.add_argument("--text", default=None, help="manuscript text inline (short pieces).")
        p.add_argument("--outline", default=None, help="outline JSON for beat-coverage checks.")
        p.add_argument("--fail-on-block", action="store_true",
                       help="exit with code 5 when the audit verdict is 'blocked'.")
        add_story_args(p, required=False, structural=False)
        if name == "revise":
            p.add_argument("--pass", default=None, dest="passes",
                           help="comma separated revision passes to include (default: all).")
            p.add_argument("--max-tasks", type=int, default=40, help="cap the number of tasks (default 40).")
        add_common(p)

    p = sub.add_parser("continuity", add_help=False, help="validate a continuity bible against the manuscript.")
    p.add_argument("--bible", default=None, help="continuity bible JSON; falls back to --project.")
    p.add_argument("--input", default=None, help="manuscript file for cross-checking.")
    p.add_argument("--text", default=None, help="manuscript text inline.")
    p.add_argument("--fail-on-critical", action="store_true",
                   help="exit with code 5 when any critical finding is present.")
    add_story_args(p, required=False, structural=False)
    add_common(p)

    p = sub.add_parser("verse", add_help=False, help="analyse poem or lyric prosody.")
    p.add_argument("--input", default=None, help="poem or lyric file (UTF-8).")
    p.add_argument("--text", default=None, help="verse text inline.")
    add_story_args(p, required=False, structural=False)
    add_common(p)

    p = sub.add_parser("self-test", add_help=False, help="run bundled smoke tests.")
    p.add_argument("--keep", action="store_true", help="keep the temporary test directory.")
    add_common(p)

    return parser


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------
def emit(args, envelope, summary=None):
    if args.as_json:
        sys.stdout.write(core.dumps_json(envelope) + "\n")
    elif summary and not args.quiet:
        sys.stdout.write(summary + "\n")
    return envelope


def resolve_out(args, default_name):
    directory = os.path.abspath(args.out)
    name = args.name or default_name
    path = core.resolve_out_path(directory, name, field="output")
    return directory, path


def _display_path(path):
    """Relative when the file sits under the working directory, absolute otherwise."""
    rel = os.path.relpath(path, os.getcwd())
    return path if rel.startswith("..") else rel


def save_artifact(args, path, text, approve, kind="deliverable"):
    core.write_text_guarded(path, text, approve=approve, kind=kind)
    return {"path": _display_path(path), "bytes": len(text.encode("utf-8")), "kind": kind}


def save_json_artifact(args, path, data, approve, kind="structured result"):
    core.write_json_guarded(path, data, approve=approve, kind=kind)
    return {"path": _display_path(path),
            "bytes": len(json.dumps(data).encode("utf-8")), "kind": kind}


def load_project_if_any(args):
    if not args.project:
        return None
    if not os.path.exists(args.project):
        return core.project_new()
    return core.project_load(args.project)


def store_and_maybe_save(args, project, section, value):
    if not args.project:
        return []
    if project is None:
        raise core.StudioError("Project handling was requested without a project.", code=core.EXIT_INTERNAL)
    core.project_set(project, section, value)
    path = os.path.abspath(args.project)
    core.save_project(path, project)
    return [{"path": _display_path(path),
             "bytes": os.path.getsize(path), "kind": "project bundle (%s updated)" % section}]


def spec_or_none(args, warnings):
    """Story spec is optional for analysis commands: use it when format is given."""
    if getattr(args, "format", None):
        spec, _ = generators().spec_from_args(args, warnings)
        return spec
    return None


def artifact_default_name(spec_like, suffix):
    return core.slugify(spec_like, "output") + suffix


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_help(args):
    topic = args.topic
    if topic:
        return emit(args, core.ok_envelope("help", help_for_op(topic)), help_for_op(topic))
    text = HELP_TEXT
    return emit(args, core.ok_envelope("help", {"text": text}), text)


def help_for_op(name):
    rows = [item for item in OPERATIONS if item[0] == name]
    if not rows:
        raise core.StudioError(
            "Unknown operation %r." % name, field="topic",
            hint="Run `capabilities` for the operation list.",
        )
    examples = {
        "brief": 'python3 tools-story-narrative.py brief --title "The Salt Road" --format novel --genre fantasy --audience adult --words 90000 --themes "duty, inheritance"',
        "premise": 'python3 tools-story-narrative.py premise --title "The Salt Road" --format novella --genre fantulation --directions 4 --seed 7',
        "outline": 'python3 tools-story-narrative.py outline --title "The Salt Road" --format novel --genre fantasy --structure save-the-cat --project project.json',
        "characters": 'python3 tools-story-narrative.py characters --title "The Salt Road" --format novel --genre fantasy --cast 8',
        "scenes": 'python3 tools-story-narrative.py scenes --format novel --genre fantasy --project project.json',
        "comic": 'python3 tools-story-narrative.py comic --title "Low Tide" --format manga --genre mystery --pages 24',
        "game": 'python3 tools-story-narrative.py game --title "Signal Debt" --format visual-novel --genre cyberpunk --nodes 18 --routes 3',
        "export": 'python3 tools-story-narrative.py export --kind synopsis --word-budget 600 --project project.json',
        "audit": 'python3 tools-story-narrative.py audit --input asset-sample-manuscript.md --format short-story --genre literary --audience adult --words 4500',
        "continuity": 'python3 tools-story-narrative.py continuity --bible asset-sample-bible.json --input asset-sample-manuscript.md',
        "verse": 'python3 tools-story-narrative.py verse --input my-poem.txt',
        "revise": 'python3 tools-story-narrative.py revise --input asset-sample-manuscript.md --format short-story --genre literary --pass structural,prose',
        "catalog": "python3 tools-story-narrative.py catalog --search reveal --limit 15",
        "capabilities": "python3 tools-story-narrative.py capabilities --op outline",
        "self-test": "python3 tools-story-narrative.py self-test",
    }
    lines = ["%s - %s" % (name, rows[0][1]), "", "Example:", "  %s" % examples.get(name, "n/a")]
    return "\n".join(lines)


HELP_TEXT = """story-narrative-studio {version} - narrative planning, production and revision

USAGE
  python3 tools-story-narrative.py <operation> [options]

OPERATIONS
{ops}

TYPICAL PIPELINE
  1. brief       -> define promise, audience envelope, boundaries, budgets
  2. premise     -> choose one direction
  3. outline     -> beat sheet with act and tension budgets
  4. characters  -> cast sheet
  5. scenes | comic | game -> production-ready units
  6. audit       -> measurable verdict
  7. revise      -> ordered, approval-gated revision plan
  8. continuity  -> bible validation and manuscript cross-check
  9. export      -> synopsis, treatment, style sheet, pitch

PROJECT BUNDLE
  Pass --project project.json to any generating operation. Each run stores its
  section (brief, premise, outline, characters, scenes, comic, game, bible) and
  later operations reuse it: `scenes` reads the outline, `export` reads everything.

SAFETY
  Existing files are never overwritten without --approve-overwrite.
  Audit and continuity are read-only. Nothing is executed, downloaded or published.

EXIT CODES
  0 ok | 2 invalid input | 3 approval required | 4 missing file or asset
  5 limit, timeout or blocked verdict | 6 internal error

DETAIL
  python3 tools-story-narrative.py help <operation>
  python3 tools-story-narrative.py capabilities --categories
""".format(version=VERSION, ops="\n".join("  %-14s %s" % (n, d) for n, d in OPERATIONS))


def capability_table(categories=True):
    catalog = core.load_asset("catalog")
    ops = []
    for name, description in OPERATIONS:
        entries = catalog["operations"].get(name, [])
        ops.append({
            "operation": name,
            "description": description,
            "catalog_categories": [e["category"] for e in entries],
            "catalog_skill_count": sum(len(e["skill_ids"]) for e in entries),
        })
    data = {
        "tool": core.TOOL_NAME,
        "version": VERSION,
        "primary_entry": core.PRIMARY_ENTRY,
        "operations": ops,
        "operation_count": len(ops),
        "catalog": {
            "categories": catalog["source"]["category_count"],
            "named_capabilities": catalog["source"]["skill_count"],
        },
        "limits": core.LIMITS,
    }
    if categories:
        data["category_list"] = [{"id": c["id"], "name": c["name"], "skills": len(c["skills"])}
                                 for c in catalog["categories"]]
    return data


def cmd_capabilities(args):
    data = capability_table(categories=args.categories)
    if args.op_filter:
        match = [o for o in data["operations"] if o["operation"] == args.op_filter]
        if not match:
            raise core.StudioError("Unknown operation %r." % args.op_filter, field="op",
                                   hint="Run `capabilities` with no --op to list operations.")
        data = {"tool": data["tool"], "version": data["version"], "operation": match[0]}
        payload = data["operation"]
        if args.op_json:
            return emit(args, core.ok_envelope("capabilities", data), core.dumps_json(data))
        text = "\n".join([
            "%s - %s" % (payload["operation"], payload["description"]),
            "Catalog categories served: %s" % ", ".join(payload["catalog_categories"]),
            "Named catalog capabilities behind it: %s" % payload["catalog_skill_count"],
            "",
            "Run `help %s` for an example command." % payload["operation"],
        ])
        return emit(args, core.ok_envelope("capabilities", data), text)
    if args.op_json:
        return emit(args, core.ok_envelope("capabilities", data), core.dumps_json(data))
    lines = ["%s %s - %s operations" % (data["tool"], data["version"], data["operation_count"]), ""]
    for op in data["operations"]:
        lines.append("%-14s %-3s %s" % (op["operation"], op["catalog_skill_count"], op["description"]))
        lines.append("%-14s     categories: %s" % ("", "; ".join(op["catalog_categories"][:3]) +
                                                   (" ..." if len(op["catalog_categories"]) > 3 else "")))
    lines.append("")
    lines.append("Catalog: %s categories, %s named capabilities." % (
        data["catalog"]["categories"], data["catalog"]["named_capabilities"]))
    if args.categories:
        lines.append("")
        for cat in data["category_list"]:
            lines.append("  %2d. %-52s %s skills" % (cat["id"], cat["name"], cat["skills"]))
    lines.append("")
    lines.append("Limits: %s" % json.dumps(core.LIMITS))
    text = "\n".join(lines)
    return emit(args, core.ok_envelope("capabilities", data), text)


def cmd_catalog(args):
    catalog = core.load_asset("catalog")
    categories = catalog["categories"]
    if args.op_filter:
        entries = catalog["operations"].get(args.op_filter)
        if entries is None:
            raise core.StudioError("Unknown operation %r." % args.op_filter, field="op",
                                   hint="Valid operations: %s" % ", ".join(sorted(catalog["operations"])))
        rows = [{"id": e["category_id"], "name": e["category"], "skills": len(e["skill_ids"])}
                for e in entries]
        payload = {"op": args.op_filter, "categories": rows}
        text = "\n".join(["Operation %s is backed by %s categories:" % (args.op_filter, len(rows))] +
                         ["  %2d. %-52s %s skills" % (r["id"], r["name"], r["skills"]) for r in rows])
        return emit(args, core.ok_envelope("catalog", payload), text)

    matches = []
    if args.search:
        needle = args.search.lower().strip()
        for cat in categories:
            for skill in cat["skills"]:
                haystack = " ".join([skill["name"], skill["description"]] + skill["keywords"]).lower()
                if needle in haystack:
                    matches.append({"category_id": cat["id"], "category": cat["name"],
                                    "skill": skill["name"], "description": skill["description"],
                                    "id": skill["id"]})
        if not matches:
            raise core.StudioError(
                "No catalog capability matched %r." % args.search, code=core.EXIT_NOTFOUND, field="search",
                hint="Try a shorter keyword, e.g. --search reveal, or omit --search to list categories.",
            )
        matches = matches[: max(1, args.limit)]
        payload = {"search": args.search, "matches": len(matches), "results": matches}
        text = "\n".join(["%s result(s) for %r:" % (len(matches), args.search)] +
                         ["  [%s] %-46s %s" % (m["id"], m["skill"], m["description"][:90]) for m in matches])
        return emit(args, core.ok_envelope("catalog", payload), text)

    if args.category:
        cat = None
        for candidate in categories:
            if str(candidate["id"]) == str(args.category) or candidate["slug"] == args.category:
                cat = candidate
        if cat is None:
            raise core.StudioError(
                "No category matched %r." % args.category, code=core.EXIT_NOTFOUND, field="category",
                hint="Pass a number 1-37 or a slug such as plot-structure-and-story-architecture.",
            )
        payload = {"category": cat["name"], "id": cat["id"], "skills": cat["skills"]}
        text = "\n".join(["Category %s: %s" % (cat["id"], cat["name"])] +
                         ["  %2d. %-46s %s" % (s["index"], s["name"], s["description"]) for s in cat["skills"]])
        return emit(args, core.ok_envelope("catalog", payload), text)

    payload = {
        "categories": [{"id": c["id"], "name": c["name"], "skills": len(c["skills"])} for c in categories],
        "category_count": len(categories),
        "named_capabilities": catalog["source"]["skill_count"],
    }
    text = "\n".join(["%s categories, %s named capabilities:" % (len(categories), payload["named_capabilities"])] +
                     ["  %2d. %-52s %s" % (c["id"], c["name"], len(c["skills"])) for c in categories])
    return emit(args, core.ok_envelope("catalog", payload), text)


def _finish_generation(args, op, build, section, warnings, deadline, extra_artifacts=None):
    data, markdown, build_warnings = build()
    warnings.extend(build_warnings or [])
    deadline.check("render")
    artifacts = []
    if args.stdout:
        sys.stdout.write(markdown if not markdown.endswith("\n") else markdown)
        artifacts.append({"path": "(stdout)", "bytes": len(markdown.encode("utf-8")), "kind": "deliverable"})
    else:
        directory, path = resolve_out(args, artifact_default_name(data.get("title") or args.title or op, "-%s.md" % op))
        artifacts.append(save_artifact(args, path, markdown, args.approve_overwrite, kind="%s deliverable (markdown)" % op))
        json_path = os.path.splitext(path)[0] + ".json"
        artifacts.append(save_json_artifact(args, json_path, data, args.approve_overwrite, kind="%s data (JSON)" % op))
    project = load_project_if_any(args)
    if project is not None:
        if project.get("title") in (None, "", core.project_new()["title"]):
            project["title"] = data.get("title") or args.title or "Untitled narrative project"
        artifacts.extend(store_and_maybe_save(args, project, section, data))
    envelope = core.ok_envelope(op, data if args.as_json else {"summary": summarize(op, data)},
                               warnings=warnings, artifacts=artifacts)
    summary = "OK %s -> %s%s" % (
        op,
        ", ".join(a["path"] for a in artifacts) or "(no files)",
        ("  warnings: %s" % len(warnings)) if warnings else "",
    )
    return emit(args, envelope, summary)


def summarize(op, data):
    if op == "premise":
        return {"directions": len(data["directions"]), "thematic_question": data["thematic_question"]}
    if op == "outline":
        return {"beats": len(data["beats"]), "scenes": data["scene_count"], "words": data["target_words"],
                "structure": data["structure"]}
    if op == "characters":
        return {"cast": data["cast_count"], "pov_ceiling": data["pov_ceiling"]}
    if op == "scenes":
        return {"scenes": data["scene_count"], "budgeted_words": data["total_target_words"]}
    if op == "comic":
        return {"pages": data["page_count"], "panels": data["total_panels"],
                "dialogue_words": data["total_dialogue_words"]}
    if op == "game":
        return {"nodes": data["node_count"], "endings": len(data["endings"]),
                "graph_warnings": len(data["graph_warnings"])}
    if op == "brief":
        return {"title": data["title"], "format": data["format"], "budget": data["production_budget"]}
    return {"title": data.get("title")}


def cmd_generate(op, args):
    deadline = core.Deadline(args.timeout)
    warnings = []
    inherited = load_project_if_any(args) if args.project else None
    if inherited and not args.title:
        args.title = inherited.get("title")
        warnings.append("Title inherited from the project bundle.")
    spec, spec_warnings = generators().spec_from_args(args)
    warnings.extend(w for w in spec_warnings if w not in warnings)

    if op == "brief":
        return _finish_generation(args, op, lambda: generators().build_brief(spec), "brief", warnings, deadline)
    if op == "premise":
        count = core.require_int(args.directions, "directions", 2, len(generators().ANGLES))
        return _finish_generation(args, op, lambda: generators().build_premise(spec, count),
                                  "premise", warnings, deadline)
    if op == "outline":
        if args.premise:
            premise = core.read_json_file(args.premise, field="premise")
            warnings.append("Premise loaded from %s; its protagonist and thematic question were kept." % args.premise)
            spec["premise_seed_protagonist"] = None
            premise_protagonist = None
            try:
                premise_protagonist = premise["directions"][0]["protagonist"]
            except (KeyError, IndexError, TypeError):
                warnings.append("Premise file had no directions; protagonist was regenerated.")
            if premise_protagonist:
                spec["forced_protagonist"] = premise_protagonist
        return _finish_generation(args, op, lambda: generators().build_outline(spec),
                                  "outline", warnings, deadline)
    if op == "characters":
        if args.project and os.path.exists(args.project):
            earlier = core.project_load(args.project).get("outline")
            if earlier:
                spec["forced_protagonist"] = earlier.get("protagonist")
                spec["forced_antagonist"] = earlier.get("antagonist")
                warnings.append("Protagonist and opposition names inherited from the stored outline.")
        if args.protagonist:
            spec["forced_protagonist"] = core.require_text(args.protagonist, "protagonist", max_len=80)
        return _finish_generation(args, op, lambda: generators().build_characters(spec),
                                  "characters", warnings, deadline)
    if op == "scenes":
        outline = None
        if args.outline:
            outline = core.read_json_file(args.outline, field="outline")
        elif args.project and os.path.exists(args.project):
            outline = core.project_load(args.project).get("outline")
        if not outline:
            raise core.StudioError(
                "No outline available for scene generation.", code=core.EXIT_NOTFOUND, field="outline",
                hint="Run `outline` first with --project project.json, or pass --outline outline.json.",
            )
        cast = core.read_json_file(args.cast_file, field="cast-file") if args.cast_file else None
        if cast is None and args.project and os.path.exists(args.project):
            cast = core.project_load(args.project).get("characters")
        return _finish_generation(args, op, lambda: generators().build_scenes(spec, outline, cast),
                                  "scenes", warnings, deadline)
    if op == "comic":
        return _finish_generation(args, op, lambda: generators().build_comic(spec, args.pages),
                                  "comic", warnings, deadline)
    if op == "game":
        return _finish_generation(args, op, lambda: generators().build_game(spec, args.nodes, args.routes),
                                  "game", warnings, deadline)
    raise core.StudioError("Unknown generating operation %r." % op, code=core.EXIT_INTERNAL)


def cmd_export(args):
    deadline = core.Deadline(args.timeout)
    warnings = []
    if not args.project:
        raise core.StudioError(
            "export needs a project bundle.", field="project",
            hint="Generate sections first with --project project.json, then export.",
        )
    project = core.project_load(args.project)
    warnings.append("Exports assemble stored sections; they do not invent missing ones.")
    data, markdown, build_warnings = generators().build_export(project, args.kind, args.word_budget)
    warnings.extend(build_warnings)
    deadline.check("render")
    artifacts = []
    if args.stdout:
        sys.stdout.write(markdown)
        artifacts.append({"path": "(stdout)", "bytes": len(markdown.encode("utf-8")), "kind": "export"})
    else:
        directory, path = resolve_out(args, artifact_default_name(project.get("title"), "-%s.md" % args.kind))
        artifacts.append(save_artifact(args, path, markdown, args.approve_overwrite, kind="%s export" % args.kind))
    return emit(args, core.ok_envelope("export", data, warnings=warnings, artifacts=artifacts),
                "OK export -> %s" % ", ".join(a["path"] for a in artifacts))


def cmd_audit(args):
    deadline = core.Deadline(args.timeout)
    warnings = []
    text = read_analysis_input(args)
    outline = None
    if args.outline:
        outline = core.read_json_file(args.outline, field="outline")
    elif args.project and os.path.exists(args.project):
        outline = core.project_load(args.project).get("outline")
    spec = spec_or_none(args, warnings)
    if spec is None:
        warnings.append("No --format supplied: length, audience and format readiness are not scored.")
    elif not args.title:
        spec["title"] = audit_mod().guess_title(text)
    report, markdown = audit_mod().audit_manuscript(text, spec, outline=outline, timeout=args.timeout)
    report["source"] = args.input or "(inline text)"
    deadline.check("report")
    artifacts = []
    if args.stdout:
        sys.stdout.write(markdown)
        artifacts.append({"path": "(stdout)", "bytes": len(markdown.encode("utf-8")), "kind": "audit report"})
    else:
        directory, path = resolve_out(args, artifact_default_name(report["title"], "-audit.md"))
        artifacts.append(save_artifact(args, path, markdown, args.approve_overwrite, kind="audit report"))
        artifacts.append(save_json_artifact(args, os.path.splitext(path)[0] + ".json", report,
                                           args.approve_overwrite, kind="audit data"))
    project = load_project_if_any(args)
    if project is not None:
        core.project_add_audit(project, {"kind": "audit", "verdict": report["verdict"], "at": report["generated_at"]})
        artifacts.extend(store_and_maybe_save(args, project, "audits", project["audits"]))
    envelope = core.ok_envelope("audit", report, warnings=warnings, artifacts=artifacts)
    emit(args, envelope, "OK audit -> %s | verdict %s (weighted %s)" % (
        ", ".join(a["path"] for a in artifacts), report["verdict"]["verdict"],
        report["verdict"]["weighted_score"]))
    if args.fail_on_block and report["verdict"]["verdict"] == "blocked":
        raise core.StudioError(
            "Audit verdict is blocked.", code=core.EXIT_LIMIT, field="verdict",
            hint="Address critical issues and rerun; use --fail-on-block only in CI.",
        )
    return envelope


def read_analysis_input(args):
    if args.input and args.text:
        raise core.StudioError("Pass either --input or --text, not both.", field="input")
    if args.text:
        text = args.text
        if len(text.encode("utf-8")) > core.LIMITS["max_input_bytes"]:
            raise core.StudioError("--text exceeds the input size limit.", code=core.EXIT_LIMIT, field="text")
        return text
    if not args.input:
        raise core.StudioError("An input file is required.", field="input",
                               hint="Use --input asset-sample-manuscript.md or --text \"short passage\".")
    return core.read_text_file(args.input, field="input")


def cmd_continuity(args):
    warnings = []
    bible = None
    if args.bible:
        bible = core.read_json_file(args.bible, field="bible")
    elif args.project and os.path.exists(args.project):
        bible = core.project_load(args.project).get("bible")
        warnings.append("Bible loaded from the project bundle.")
    if not bible:
        raise core.StudioError(
            "No continuity bible supplied.", field="bible",
            hint="Pass --bible asset-sample-bible.json, or store one in a project bundle under 'bible'.",
        )
    text = None
    if args.input or args.text:
        text = read_analysis_input(args)
    report, markdown = audit_mod().continuity_check(bible, text, timeout=args.timeout)
    artifacts = []
    if args.project:
        project = load_project_if_any(args) or core.project_new(bible.get("title") if isinstance(bible, dict) else None)
        if isinstance(bible, dict):
            project["title"] = project.get("title") or bible.get("title")
        core.project_set(project, "bible", bible)
        core.project_add_audit(project, {"kind": "continuity", "status": report["status"],
                                        "score": report["score"], "at": core.now_iso()})
        core.save_project(os.path.abspath(args.project), project)
        artifacts.append({"path": _display_path(os.path.abspath(args.project)),
                          "bytes": os.path.getsize(os.path.abspath(args.project)),
                          "kind": "project bundle (bible stored)"})
    if args.stdout:
        sys.stdout.write(markdown)
        artifacts.append({"path": "(stdout)", "bytes": len(markdown.encode("utf-8")), "kind": "continuity report"})
    else:
        directory, path = resolve_out(args, artifact_default_name(report["title"], "-continuity.md"))
        artifacts.append(save_artifact(args, path, markdown, args.approve_overwrite, kind="continuity report"))
        artifacts.append(save_json_artifact(args, os.path.splitext(path)[0] + ".json", report,
                                           args.approve_overwrite, kind="continuity data"))
    envelope = core.ok_envelope("continuity", report, warnings=warnings, artifacts=artifacts)
    emit(args, envelope, "OK continuity -> %s | status %s | score %s" % (
        ", ".join(a["path"] for a in artifacts), report["status"], report["score"]))
    if args.fail_on_critical and report["counts"]["critical"]:
        raise core.StudioError(
            "Continuity bible has critical findings.", code=core.EXIT_LIMIT, field="verdict",
            hint="Fix schema errors before integrating the bible.",
        )
    return envelope


def cmd_verse(args):
    warnings = ["Prosody values are heuristic estimates from spelling; confirm metre against a dictionary."]
    text = read_analysis_input(args)
    report, markdown = audit_mod().verse_analyse(text, timeout=args.timeout)
    artifacts = []
    if args.stdout:
        sys.stdout.write(markdown)
        artifacts.append({"path": "(stdout)", "bytes": len(markdown.encode("utf-8")), "kind": "prosody report"})
    else:
        directory, path = resolve_out(args, artifact_default_name("verse", "-prosody.md"))
        artifacts.append(save_artifact(args, path, markdown, args.approve_overwrite, kind="prosody report"))
        artifacts.append(save_json_artifact(args, os.path.splitext(path)[0] + ".json", report,
                                           args.approve_overwrite, kind="prosody data"))
    return emit(args, core.ok_envelope("verse", report, warnings=warnings, artifacts=artifacts),
                "OK verse -> %s | %s lines, scheme %s" % (
                    ", ".join(a["path"] for a in artifacts), report["lines"], report["rhyme_scheme"]))


def cmd_revise(args):
    warnings = []
    text = read_analysis_input(args)
    spec = spec_or_none(args, warnings)
    if spec is None:
        warnings.append("No --format supplied: format readiness is not scored; all other passes still run.")
    if spec is not None and not args.title:
        spec["title"] = audit_mod().guess_title(text)
    passes = core.parse_list(args.passes, "pass") or None
    max_tasks = core.require_int(args.max_tasks, "max-tasks", 1, 500)
    plan, markdown, report = revision_mod().build_plan(
        text, spec, passes=passes, max_tasks=max_tasks, timeout=args.timeout)
    artifacts = []
    if args.stdout:
        sys.stdout.write(markdown)
        artifacts.append({"path": "(stdout)", "bytes": len(markdown.encode("utf-8")), "kind": "revision plan"})
    else:
        directory, path = resolve_out(args, artifact_default_name(plan["title"], "-revision-plan.md"))
        artifacts.append(save_artifact(args, path, markdown, args.approve_overwrite, kind="revision plan"))
        artifacts.append(save_json_artifact(args, os.path.splitext(path)[0] + ".json", plan,
                                           args.approve_overwrite, kind="revision plan data"))
    envelope = core.ok_envelope("revise", plan, warnings=warnings, artifacts=artifacts)
    emit(args, envelope, "OK revise -> %s | %s tasks (%s require approval)" % (
        ", ".join(a["path"] for a in artifacts), len(plan["tasks"]), len(plan["approval_required"])))
    if args.fail_on_block and report["verdict"]["verdict"] == "blocked":
        raise core.StudioError("Audit verdict is blocked.", code=core.EXIT_LIMIT, field="verdict",
                               hint="Resolve critical tasks first.")
    return envelope


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------
def cmd_self_test(args):
    import tempfile

    results = []

    def check(name, func):
        started = time.monotonic()
        try:
            detail = func()
            results.append({"check": name, "status": "pass", "detail": detail,
                            "ms": round((time.monotonic() - started) * 1000)})
        except Exception as exc:  # noqa: BLE001 - self-test reports, never hides
            results.append({"check": name, "status": "fail",
                            "detail": "%s: %s" % (type(exc).__name__, exc),
                            "ms": round((time.monotonic() - started) * 1000)})

    workdir = tempfile.mkdtemp(prefix="sns-selftest-")
    sample_text = open(os.path.join(HERE, "asset-sample-manuscript.md"), encoding="utf-8").read()
    sample_bible_path = os.path.join(HERE, "asset-sample-bible.json")

    base = argparse.Namespace(
        title="Signal Debt", format="visual-novel", genre="cyberpunk", audience="adult",
        words=40000, structure=None, beats=None, scenes=None, cast=6, pages=22, nodes=12, routes=3,
        themes="debt, autonomy", non_negotiables=None, boundaries=None, style="plain",
        platform="desktop", deadline=None, seed=11,
    )

    def gen(op, section, **overrides):
        spec_args = argparse.Namespace(**{**vars(base), **overrides})
        spec, _ = generators().spec_from_args(spec_args, [])
        if op == "brief":
            data, md, _ = generators().build_brief(spec)
        elif op == "premise":
            data, md, _ = generators().build_premise(spec, 3)
        elif op == "outline":
            data, md, _ = generators().build_outline(spec)
        elif op == "characters":
            data, md, _ = generators().build_characters(spec)
        elif op == "comic":
            data, md, _ = generators().build_comic(spec, 12)
        elif op == "game":
            data, md, _ = generators().build_game(spec, 10, 3)
        else:
            raise ValueError(op)
        assert data and md and len(md) > 200, "%s produced a thin deliverable" % op
        json.dumps(data)
        return "%s: %s bytes markdown" % (op, len(md.encode("utf-8")))

    for op in ("brief", "premise", "outline", "characters", "comic", "game"):
        check("generate-%s" % op, lambda op=op: gen(op, op))

    def scenes_check():
        spec, _ = generators().spec_from_args(base, [])
        outline, _, _ = generators().build_outline(spec)
        cast, _, _ = generators().build_characters(spec)
        data, md, _ = generators().build_scenes(spec, outline, cast)
        assert data["scene_count"] >= len(outline["beats"])
        json.dumps(data)
        return "scenes: %s cards" % data["scene_count"]

    check("generate-scenes", scenes_check)

    def audit_check():
        spec, _ = generators().spec_from_args(
            argparse.Namespace(**{**vars(base), "format": "short-story", "genre": "literary",
                                  "audience": "adult", "words": 900}), [])
        report, md = audit_mod().audit_manuscript(sample_text, spec)
        assert report["metrics"]["words"] > 100
        assert report["verdict"]["verdict"] in ("blocked", "revise", "ready")
        assert "Verdict" in md
        return "audit: verdict %s, score %s" % (report["verdict"]["verdict"], report["verdict"]["weighted_score"])

    check("audit-manuscript", audit_check)

    def continuity_check():
        bible = core.read_json_file(sample_bible_path, field="bible")
        report, md = audit_mod().continuity_check(bible, sample_text)
        assert report["coverage"]["characters"] >= 2
        return "continuity: %s findings, status %s" % (len(report["findings"]), report["status"])

    check("continuity-bible", continuity_check)

    def verse_check():
        report, md = audit_mod().verse_analyse(
            "The tide came in at four\nand took the salt road down\nwe counted what was left\nand named it ours\n")
        assert report["lines"] == 4
        assert report["stanzas"] == 1
        return "verse: scheme %s, mean syllables %s" % (report["rhyme_scheme"], report["mean_syllables"])

    check("verse-prosody", verse_check)

    def revise_check():
        spec, _ = generators().spec_from_args(
            argparse.Namespace(**{**vars(base), "format": "short-story", "genre": "literary",
                                  "audience": "adult", "words": 900}), [])
        plan, md, report = revision_mod().build_plan(sample_text, spec, max_tasks=25)
        assert plan["tasks"], "no tasks produced"
        assert "Revision Plan" in md
        return "revise: %s tasks, %s gated" % (len(plan["tasks"]), len(plan["approval_required"]))

    check("revision-plan", revise_check)

    def export_check():
        project = core.project_new("Signal Debt")
        spec, _ = generators().spec_from_args(base, [])
        for op, section in (("brief", "brief"), ("premise", "premise"), ("outline", "outline"),
                            ("characters", "characters")):
            data = {
                "brief": lambda: generators().build_brief(spec)[0],
                "premise": lambda: generators().build_premise(spec, 2)[0],
                "outline": lambda: generators().build_outline(spec)[0],
                "characters": lambda: generators().build_characters(spec)[0],
            }[op]()
            core.project_set(project, section, data)
        data, md, _ = generators().build_export(project, "synopsis", 300)
        assert "Synopsis" in md and len(md) > 200
        return "export: %s bytes" % len(md.encode("utf-8"))

    check("export-synopsis", export_check)

    def determinism_check():
        a = gen("outline", "outline")
        b = gen("outline", "outline")
        assert a == b, "same seed produced different output"
        c = gen("outline", "outline", seed=999)
        assert c != a, "changing the seed did not change the output"
        return "deterministic for a fixed seed; changes when the seed changes"

    check("determinism", determinism_check)

    def validation_check():
        try:
            generators().spec_from_args(argparse.Namespace(**{**vars(base), "format": None}), [])
        except core.StudioError as exc:
            assert exc.code == core.EXIT_INPUT
        else:
            raise AssertionError("missing format was accepted")
        try:
            core.resolve_out_path(workdir, "../escape.md")
        except core.StudioError:
            pass
        else:
            raise AssertionError("path traversal was accepted")
        try:
            core.write_text_guarded(os.path.join(workdir, "x.md"), "one", approve=False)
            core.write_text_guarded(os.path.join(workdir, "x.md"), "two", approve=False)
        except core.StudioError as exc:
            assert exc.code == core.EXIT_APPROVAL
        else:
            raise AssertionError("overwrite was allowed without approval")
        return "invalid input, traversal and unapproved overwrite are all rejected"

    check("validation-and-approval", validation_check)

    def project_roundtrip():
        path = os.path.join(workdir, "project.json")
        project = core.project_new("Roundtrip")
        core.project_set(project, "outline", {"beats": []})
        core.save_project(path, project)
        loaded = core.project_load(path)
        assert loaded["title"] == "Roundtrip"
        return "project bundle written and reloaded"

    check("project-roundtrip", project_roundtrip)

    passed = sum(1 for r in results if r["status"] == "pass")
    payload = {
        "checks": results,
        "passed": passed,
        "failed": len(results) - passed,
        "workdir": workdir if args.keep else "(removed)",
    }
    if not args.keep:
        core.cleanup(workdir)
    text = "\n".join(["%s %-24s %-5s %s" % ("PASS" if r["status"] == "pass" else "FAIL", r["check"],
                                            r["status"], r["detail"]) for r in results])
    text += "\n\n%s passed, %s failed" % (passed, len(results) - passed)
    emit(args, core.ok_envelope("self-test", payload), text)
    if payload["failed"]:
        raise core.StudioError("Self-test failed: %s check(s) did not pass." % payload["failed"],
                               code=core.EXIT_INTERNAL, field="self-test")
    return payload


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def dispatch(args):
    if getattr(args, "want_help", False) and not args.op:
        return cmd_help(argparse.Namespace(topic=None, as_json=False))
    if not args.op:
        return cmd_help(argparse.Namespace(topic=None, as_json=False))
    op = args.op
    if op == "help":
        return cmd_help(args)
    if op == "capabilities":
        return cmd_capabilities(args)
    if op == "catalog":
        return cmd_catalog(args)
    if op in ("brief", "premise", "outline", "characters", "scenes", "comic", "game"):
        return cmd_generate(op, args)
    if op == "export":
        return cmd_export(args)
    if op == "audit":
        return cmd_audit(args)
    if op == "continuity":
        return cmd_continuity(args)
    if op == "verse":
        return cmd_verse(args)
    if op == "revise":
        return cmd_revise(args)
    if op == "self-test":
        return cmd_self_test(args)
    raise core.StudioError("Unknown operation %r." % op, field="op",
                           hint="Run `capabilities` for the operation list.")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "want_help", False) and getattr(args, "op", None):
        args.topic = args.op
        args.op = "help"
        for attr, default in (("as_json", False), ("quiet", False), ("out", DEFAULT_OUT)):
            if not hasattr(args, attr):
                setattr(args, attr, default)
        return dispatch(args)
    try:
        dispatch(args)
        return 0
    except core.StudioError as exc:
        sys.stderr.write(core.dumps_json(exc.envelope(getattr(args, "op", None))) + "\n")
        return exc.code
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        sys.stderr.write(core.dumps_json(core.StudioError(
            "Cancelled by user.", code=core.EXIT_INTERNAL).envelope(getattr(args, "op", None))) + "\n")
        return core.EXIT_INTERNAL
    except Exception as exc:  # noqa: BLE001 - never leak a traceback to a writer
        sys.stderr.write(core.dumps_json({
            "status": "error", "tool": core.TOOL_NAME, "version": VERSION,
            "op": getattr(args, "op", None), "code": core.EXIT_INTERNAL,
            "error": {"message": "%s: %s" % (type(exc).__name__, exc),
                      "hint": "Rerun with --json for detail, or run `self-test` to check the installation."},
        }) + "\n")
        return core.EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
