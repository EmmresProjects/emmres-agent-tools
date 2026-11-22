#!/usr/bin/env python3
"""AI Video Production: vendor-neutral planning, validation, captions, animatics, and rendering."""
from __future__ import annotations

import argparse
import html.parser
import json
import sys
from pathlib import Path
from typing import Any
from importlib.machinery import SourceFileLoader

sys.dont_write_bytecode = True  # Keep the reusable component folder free of generated cache subfolders.
HERE = Path(__file__).resolve().parent
core = SourceFileLoader("video_core", str(HERE / "tools-video-core.py")).load_module()
media = SourceFileLoader("video_media", str(HERE / "tools-video-media.py")).load_module()
media._core = core
media.ToolError = core.ToolError
ToolError = core.ToolError
VERSION = "1.0.0"


class TextExtractor(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)


def plain_request(value: str) -> str:
    parser = TextExtractor()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())


def request_selection(request: str, brief: dict[str, Any], catalog: dict[str, Any]) -> None:
    text = plain_request(request).lower()
    if not text:
        raise ToolError("EMPTY_REQUEST", "Selection request is empty")
    categories, skills = core.catalog_indexes(catalog)
    matched_categories, matched_skills = [], []
    for category in catalog["categories"]:
        if category["name"].lower() in text or category["id"] in text or f"category {category['number']}" in text:
            matched_categories.append(category["id"])
    seen = set()
    for category in catalog["categories"]:
        for skill in category["skills"]:
            if skill["id"] not in seen and (skill["name"].lower() in text or skill["id"] in text):
                matched_skills.append(skill["id"])
                seen.add(skill["id"])
    if not matched_categories and not matched_skills:
        raise ToolError("NO_CAPABILITY_MATCH", "No exact catalog category or capability name was found in the request", {"recovery": "Use capabilities --query to find names, or provide categories/capabilities in the brief."})
    brief["categories"] = list(dict.fromkeys(brief.get("categories", []) + matched_categories))
    brief["capabilities"] = list(dict.fromkeys(brief.get("capabilities", []) + matched_skills))


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--catalog", help="Catalog JSON path (default: bundled asset-skill-catalog.json)")
    parser.add_argument("--config", help="Configuration JSON path (default: bundled asset-default-config.json)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tools-ai-video.py", description="Plan and previsualize AI video workflows across 47 categories and 564 catalog capabilities. Neural/provider work is disclosed as planned, never simulated.", formatter_class=argparse.RawDescriptionHelpFormatter, epilog="Examples:\n  bash -lc 'python3 ai-video-production/tools-ai-video.py capabilities --query caption'\n  bash -lc 'python3 ai-video-production/tools-ai-video.py run --input ai-video-production/asset-example-brief.json --output-dir output/video'\n  bash -lc 'python3 ai-video-production/tools-ai-video.py render --input output/video/plan.json --output output/video/animatic.mp4 --approve'")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("capabilities", help="List/search all categories and capabilities")
    add_common(p)
    p.add_argument("--category", help="Category id, exact name, or number")
    p.add_argument("--query", help="Case-insensitive text in ids, names, descriptions, or purposes")
    p.add_argument("--format", choices=("json", "text"), default="text")
    p.add_argument("--output", help="Optional JSON output path")
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("validate", help="Validate and normalize a production brief")
    add_common(p); p.add_argument("--input", required=True); p.add_argument("--output"); p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("plan", help="Create an editable production plan and provider handoffs")
    add_common(p); p.add_argument("--input", required=True); p.add_argument("--output", required=True); p.add_argument("--request", help="Plain text, Markdown, or copied HTML containing category/capability names"); p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("preview", help="Generate a self-contained interactive HTML animatic")
    add_common(p); p.add_argument("--input", required=True, help="Production plan JSON"); p.add_argument("--output", required=True); p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("captions", help="Write SRT or WebVTT from a production plan")
    add_common(p); p.add_argument("--input", required=True); p.add_argument("--output", required=True); p.add_argument("--format", choices=("srt", "vtt")); p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("evaluate", help="Run deterministic timeline, caption, approval, and disclosure checks")
    add_common(p); p.add_argument("--input", required=True); p.add_argument("--output"); p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("probe", help="Check optional local media runtimes")
    add_common(p)
    p = sub.add_parser("run", help="Run plan, preview, captions, and evaluation in one command")
    add_common(p); p.add_argument("--input", required=True); p.add_argument("--output-dir", required=True); p.add_argument("--request"); p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("render", help="Render a color-panel MP4 animatic with optional FFmpeg (approval required)")
    add_common(p); p.add_argument("--input", required=True); p.add_argument("--output", required=True); p.add_argument("--approve", action="store_true"); p.add_argument("--overwrite", action="store_true"); p.add_argument("--cancel-file", help="Cancellation is requested when this file exists")
    return parser


def write_text(path: str, text: str, overwrite: bool) -> None:
    target = Path(path)
    if target.exists() and not overwrite:
        raise ToolError("OUTPUT_EXISTS", f"Refusing to overwrite existing output: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ToolError("OUTPUT_WRITE_FAILED", f"Cannot write {target}: {exc}") from exc


def load_plan(path: str, config: dict[str, Any]) -> dict[str, Any]:
    plan = core.read_json(path, config["limits"]["max_input_bytes"])
    if plan.get("tool") != "ai-video-production" or not isinstance(plan.get("shots"), list) or not isinstance(plan.get("brief"), dict):
        raise ToolError("INVALID_PLAN", "Input is not an AI Video Production plan")
    return plan


def execute(args: argparse.Namespace) -> dict[str, Any]:
    config = core.load_config(args.config)
    catalog = core.load_catalog(args.catalog)
    if args.command == "probe":
        return {"status": "ok", "python": sys.version.split()[0], "runtimes": media.probe(config), "network": "disabled by policy"}
    if args.command == "capabilities":
        categories, _ = core.catalog_indexes(catalog)
        selected_categories = catalog["categories"]
        if args.category:
            key = args.category.lower()
            if key not in categories:
                raise ToolError("UNKNOWN_CATEGORY", f"Unknown category: {args.category}")
            selected_categories = [categories[key]]
        query = args.query.lower() if args.query else None
        result_categories = []
        for category in selected_categories:
            skills = [s for s in category["skills"] if not query or query in json.dumps(s, ensure_ascii=False).lower() or query in category["purpose"].lower()]
            if skills:
                result_categories.append({**{k: v for k, v in category.items() if k != "skills"}, "skills": skills})
        count = sum(len(c["skills"]) for c in result_categories)
        result = {"status": "ok", "capability_count": count, "category_count": len(result_categories), "categories": result_categories}
        if args.output:
            core.write_json(args.output, result, args.overwrite)
        if args.format == "text" and not args.output:
            lines = []
            for category in result_categories:
                lines.append(f"{category['number']}. {category['name']} [{category['id']}]\n  {category['purpose']}")
                lines.extend(f"  - {s['id']}: {s['name']}" for s in category["skills"])
            print("\n".join(lines))
        return result
    if args.command == "validate":
        brief = core.normalize_brief(core.read_json(args.input, config["limits"]["max_input_bytes"]), config)
        result = {"status": "valid", "brief": brief, "selected_capability_count": len(core.select_capabilities(brief, catalog))}
        if args.output:
            core.write_json(args.output, result, args.overwrite)
        return result
    if args.command in ("plan", "run"):
        brief = core.normalize_brief(core.read_json(args.input, config["limits"]["max_input_bytes"]), config)
        if args.request:
            request_selection(args.request, brief, catalog)
        plan = core.create_plan(brief, catalog)
        if args.command == "plan":
            core.write_json(args.output, plan, args.overwrite)
            return {"status": plan["status"], "output": args.output, "capability_count": plan["capability_count"], "shot_count": len(plan["shots"]), "warnings": plan["warnings"]}
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        plan_path = out / "plan.json"
        core.write_json(plan_path, plan, args.overwrite)
        write_text(str(out / "preview.html"), core.render_html(plan), args.overwrite)
        captions = core.captions_from_plan(plan)
        write_text(str(out / "captions.srt"), core.format_srt(captions), args.overwrite)
        write_text(str(out / "captions.vtt"), core.format_vtt(captions), args.overwrite)
        evaluation = core.evaluate_plan(plan)
        core.write_json(out / "evaluation.json", evaluation, args.overwrite)
        return {"status": "completed-local-artifacts", "output_directory": str(out), "artifacts": ["plan.json", "preview.html", "captions.srt", "captions.vtt", "evaluation.json"], "capability_count": plan["capability_count"], "evaluation_status": evaluation["status"]}
    plan = load_plan(args.input, config)
    if args.command == "preview":
        write_text(args.output, core.render_html(plan), args.overwrite)
        return {"status": "created", "output": args.output, "type": "self-contained HTML animatic"}
    if args.command == "captions":
        fmt = args.format or Path(args.output).suffix.lower().lstrip(".")
        if fmt not in ("srt", "vtt"):
            raise ToolError("INVALID_CAPTION_FORMAT", "Use --format srt or --format vtt")
        captions = core.captions_from_plan(plan)
        text = core.format_srt(captions) if fmt == "srt" else core.format_vtt(captions)
        write_text(args.output, text, args.overwrite)
        return {"status": "created", "output": args.output, "format": fmt, "caption_count": len(captions)}
    if args.command == "evaluate":
        result = core.evaluate_plan(plan)
        if args.output:
            core.write_json(args.output, result, args.overwrite)
        return result
    if args.command == "render":
        cancel_path = Path(args.cancel_file) if args.cancel_file else None
        return media.render_animatic(plan, args.output, config, args.approve, args.overwrite, lambda: bool(cancel_path and cancel_path.exists()))
    raise ToolError("UNKNOWN_COMMAND", f"Unsupported command: {args.command}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = execute(args)
        # Text capability output is human-oriented; all other operations emit structured JSON.
        if args.command != "capabilities" or args.output or args.format == "json":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("status") not in ("review-required",) else 2
    except ToolError as exc:
        print(json.dumps({"status": "error", "error": {"code": exc.code, "message": exc.message, "details": exc.details}}, indent=2, ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "error", "error": {"code": "CANCELLED", "message": "Interrupted by user"}}), file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
