#!/usr/bin/env python3
"""spriteforge plan: production planning, routing, governance and handoff records.

This module owns the planning and bookkeeping primitives of the catalog. All
operations are deterministic JSON/text producers -- no image rendering happens
here, so they are fast and safe to run on every pipeline step.

  catalog          parse plain text / Markdown / HTML into capability records
  sheet-plan       choose cell size, rows, columns, sheet size and naming
  action-matrix    build and audit the action coverage matrix
  estimate         estimate frames, sheets, review rounds, storage, stages
  handoff          write the production handoff manifest
  version          record version, approval state, locks and file identity
  rights           track source, creator, licence, credit and restrictions
  style-bible      build the project style bible
  state-machine    map clips into states, transitions and priorities
  dependency       track which outputs depend on which sources
  router           route a request to the smallest set of capabilities
  change-request   turn reviewer notes into exact tasks
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sfcore_plan", HERE / "tools-spriteforge-core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["sfcore_plan"] = core
_spec.loader.exec_module(core)  # type: ignore[union-attr]

SFError = core.SFError
CAPABILITIES = HERE / "asset-sf-capability-catalog.json"

# --------------------------------------------------------------------------- #
# Text / HTML parsing shared by catalog + router
# --------------------------------------------------------------------------- #

TAG_RE = re.compile(r"<[^>]+>")
BLOCK_TAG_RE = re.compile(r"</?(?:p|div|li|ul|ol|tr|td|th|table|section|article|header|footer|br|h[1-6])[^>]*>", re.I)
ENTITY = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
          "&mdash;": "-", "&ndash;": "-", "&hellip;": "..."}
NUMBERING_RE = re.compile(r"^\s*(?:[-*+>]\s+|\d{1,3}[.)]\s+)?(?:\[[ xX]\]\s+)?")
CATEGORY_HEADER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\d{1,2}[.)]\s*)?(?P<name>[A-Z][A-Za-z0-9 &,'/\-]{2,60}?)\s*$")
SKILL_RE = re.compile(r"skill\s*name\s*[:\-]\s*(?P<name>.+)$", re.I)
DESC_RE = re.compile(r"^\s*(?:description|desc|summary)\s*[:\-]\s*(?P<desc>.+)$", re.I)
PAGE_NOISE_RE = re.compile(
    r"^(?:SPRITEFORGE\b.*|Sprite,\s*Sprite Sheet.*|.*\|\s*Page \d+\s*|Page \d+(?:\s+of\s+\d+)?|\d+)$",
    re.I)


def normalise_source(raw: str) -> str:
    """Strip HTML/CSS/attributes, entity-decode and reunite wrapped lines."""
    text = BLOCK_TAG_RE.sub("\n", raw)
    text = TAG_RE.sub("", text)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.I | re.S)
    for k, v in ENTITY.items():
        text = text.replace(k, v)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    # a hyphen at end of line is a broken word; other trailing spaces are soft wraps
    text = re.sub(r"(?<=[A-Za-z])-[ \t]*\n(?=\S)", "-", text)
    text = re.sub(r"[ \t]+\n(?=\S)", " ", text)
    # wrapped description continuations that start lowercase
    text = re.sub(r"(?<=[a-z,;:])\n(?=[]a-z(\"'])", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _clean_header(line: str) -> str:
    return NUMBERING_RE.sub("", line).strip(" :*#-|")


def parse_capability_source(raw: str) -> tuple[list[dict], list[str]]:
    """Extract category + skill records from catalog-style text, Markdown or HTML."""
    text = normalise_source(raw)
    lines = [ln.strip() for ln in text.split("\n")]
    warnings: list[str] = []
    entries: list[dict] = []
    table_header = None
    for i, line in enumerate(lines):
        if not line or PAGE_NOISE_RE.match(line):
            continue
        # Markdown/HTML tables: | Skill Name | Description |
        if line.startswith("|") and line.count("|") >= 2:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if table_header is None and any(c.lower() in {"skill", "skill name", "name", "capability"} for c in cells):
                table_header = [c.lower() for c in cells]
                continue
            if table_header and not set("".join(cells)) <= set("-: "):
                name_idx = next((j for j, c in enumerate(table_header) if c in {"skill", "skill name", "name", "capability"}), 0)
                desc_idx = next((j for j, c in enumerate(table_header) if c in {"description", "desc", "summary", "purpose"}), 1)
                name = cells[name_idx] if name_idx < len(cells) else ""
                desc = cells[desc_idx] if desc_idx < len(cells) else ""
                if name:
                    entries.append({"line": i, "name": name, "description": desc, "header": False})
                continue
        m = SKILL_RE.search(line)
        if m and "description" not in line.lower():
            name = re.sub(r"\s*[|\u2013\u2014]\s*.*$", "", m.group("name")).strip(" .:*-")
            if name:
                entries.append({"line": i, "name": name, "description": "", "header": False})
                continue
        m = DESC_RE.match(line)
        if m and entries and not entries[-1]["header"]:
            if not entries[-1]["description"]:
                entries[-1]["description"] = m.group("desc").strip()
            continue
        # candidate category header: short, capitalised, no sentence period,
        # and a skill entry must follow within the next three content lines
        header = _clean_header(line)
        if header and len(header) <= 62 and not header.endswith(".") and CATEGORY_HEADER_RE.match(header):
            lookahead = [ln.strip() for ln in lines[i + 1:i + 5] if ln.strip() and not PAGE_NOISE_RE.match(ln)]
            if any(SKILL_RE.search(la) and "description" not in la.lower() for la in lookahead):
                entries.append({"line": i, "name": header, "description": "", "header": True})
                continue
        # bare continuation that belongs to the previous description
        if entries and not entries[-1]["header"] and entries[-1]["description"] and len(line) > 20:
            entries[-1]["description"] = (entries[-1]["description"] + " " + line).strip()
    categories: list[dict] = []
    current = None
    for e in entries:
        if e["header"]:
            current = {"name": e["name"], "skills": []}
            categories.append(current)
            continue
        if current is None:
            current = {"name": "uncategorised", "skills": []}
            categories.append(current)
        if not e["description"]:
            warnings.append(f"skill without description: {e['name']}")
        current["skills"].append({"name": e["name"], "description": e["description"]})
    categories = [c for c in categories if c["skills"]]
    if not categories:
        raise SFError("NOT_A_CATALOG",
                      "no 'Skill Name:' entries found; this parser expects catalog text with names and descriptions",
                      hint="pass --text or --input with lines like '1. Skill Name: Foo' / 'Description: ...'")
    seen: set[str] = set()
    for c in categories:
        for sk in c["skills"]:
            key = sk["name"].lower()
            if key in seen:
                warnings.append(f"duplicate skill name: {sk['name']}")
            seen.add(key)
    return categories, warnings


def load_capability_asset() -> list[dict]:
    if not CAPABILITIES.exists():
        raise SFError("CAPABILITY_ASSET_MISSING", f"missing capability catalog asset: {CAPABILITIES.name}",
                      hint="the asset ships with the Skill Tool; restore asset-sf-capability-catalog.json")
    data = core.load_json(CAPABILITIES)
    if not isinstance(data, list):
        raise SFError("CAPABILITY_ASSET_INVALID", "capability catalog must be a JSON list")
    return data


def all_skills(caps: list[dict]) -> list[dict]:
    out = []
    for c in caps:
        for s in c.get("skills", []):
            s = dict(s)
            s["category"] = c["name"]
            s["category_id"] = c.get("id")
            out.append(s)
    return out


STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "for", "with", "from", "in", "on", "into", "by", "as",
    "is", "are", "be", "that", "this", "it", "its", "their", "make", "makes", "creates", "create",
    "build", "builds", "using", "use", "used", "all", "any", "each", "other", "such", "so", "then",
    "sprite", "sprites", "skill", "skills", "tool", "tools", "help", "want", "need", "please",
    "new", "set", "sets", "based",
}


def _stem(word: str) -> str:
    """Very small suffix stripper so 'directional', 'directions', 'direction' collapse."""
    w = word.lower()
    for suffix in ("ational", "ation", "ments", "ment"):
        if len(w) > len(suffix) + 4 and w.endswith(suffix):
            return w[: -len(suffix)]
    for suffix in ("ings", "ing", "ies", "ers", "est"):
        if len(w) > len(suffix) + 4 and w.endswith(suffix):
            w = w[: -len(suffix)]
            break
    if len(w) > 4 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) > 5 and w.endswith("al"):
        w = w[:-2]
    if len(w) > 5 and w.endswith("e"):
        w = w[:-1]
    return w


def tokenise(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {_stem(w) for w in words if len(w) > 2 and w not in STOPWORDS}


def build_index(skills: list[dict]) -> dict[str, float]:
    """Inverse document frequency over the capability corpus."""
    n = max(1, len(skills))
    df: dict[str, int] = {}
    for s in skills:
        for t in tokenise(f"{s['name']} {s.get('description','')} {s.get('category','')}"):
            df[t] = df.get(t, 0) + 1
    return {t: math.log(1 + n / c) for t, c in df.items()}


def score_skill(request_tokens: set[str], skill: dict, idf: dict[str, float] | None = None) -> float:
    idf = idf or {}
    target = tokenise(f"{skill['name']} {skill.get('description','')} {skill.get('category','')}")
    name_tokens = tokenise(skill["name"])
    category_tokens = tokenise(skill.get("category", ""))
    if not request_tokens or not target:
        return 0.0
    hit = request_tokens & target
    if not hit:
        return 0.0
    weight = sum(idf.get(t, 1.0) for t in hit) / math.sqrt(len(target))
    name_hit = sum(idf.get(t, 1.0) for t in request_tokens & name_tokens)
    cat_hit = sum(idf.get(t, 1.0) for t in request_tokens & category_tokens)
    return round(weight + name_hit * 1.6 + cat_hit * 0.8, 4)


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #

def op_catalog(args, root: Path, rep: core.Reporter):
    """Search, lint or extract capabilities from text, Markdown or HTML."""
    if args.self:
        caps = load_capability_asset()
        payload = {"source": "asset-sf-capability-catalog.json", "categories": caps,
                   "category_count": len(caps), "skill_count": sum(len(c["skills"]) for c in caps)}
        out = core.guard_output(root / args.output, root, args.allow_outside_root)
        if not args.dry_run:
            core.save_json(payload, out)
            rep.add_output(out, "catalog")
        return {"summary": {"categories": payload["category_count"], "skills": payload["skill_count"],
                            "output": str(out)}}
    if args.text:
        raw = args.text
    elif args.input:
        p = Path(args.input) if Path(args.input).is_absolute() else root / args.input
        if not p.exists():
            raise SFError("INPUT_NOT_FOUND", f"catalog source not found: {p}")
        if p.is_dir():
            raise SFError("INPUT_IS_DIR",
                          f"--input must be a catalog document (text, Markdown or HTML), "
                          f"got a folder: {p}",
                          hint="point --input at a .txt/.md/.html catalog, or use "
                               "`analyze catalog` to index a folder of sprites")
        try:
            raw = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise SFError("SPEC_NOT_JSON",
                          f"{p.name} is not a UTF-8 text file",
                          hint="catalog input must be text, Markdown or HTML")
    else:
        raise SFError("MISSING_INPUT", "pass --text, --input or --self")
    categories, warnings = parse_capability_source(raw)
    skills = [dict(s, category=c["name"], category_id=c.get("id")) for c in categories for s in c["skills"]]
    if args.query:
        q = tokenise(args.query)
        idf = build_index(skills)
        for s in skills:
            s["score"] = score_skill(q, s, idf)
        skills = sorted([s for s in skills if s["score"] > 0], key=lambda s: -s["score"])
    if args.category:
        wanted = {core.slugify(c) for c in args.category}
        skills = [s for s in skills if core.slugify(s["category"]) in wanted]
    payload = {
        "source": "inline" if args.text else args.input,
        "category_count": len(categories),
        "skill_count": len(skills),
        "warnings": warnings,
        "skills": skills,
    }
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "catalog")
    for w in warnings:
        rep.warn("CATALOG_WARNING", w)
    rep.stat("skills", len(skills))
    return {"summary": {"categories": len(categories), "skills": len(skills), "warnings": len(warnings),
                        "output": str(out)}}


def op_sheet_plan(args, root: Path, rep: core.Reporter):
    """Plan cell size, rows, columns, sheet size, padding and naming."""
    cell = core.parse_size(args.cell)
    frames = args.frames
    if frames < 1:
        raise SFError("BAD_ARGUMENT", "--frames must be >= 1")
    cols = args.cols or math.ceil(math.sqrt(frames))
    cols = max(1, min(cols, frames))
    rows = math.ceil(frames / cols)
    pad = args.padding
    W = cols * (cell[0] + pad) + pad + args.margin * 2
    H = rows * (cell[1] + pad) + pad + args.margin * 2
    if args.pow2:
        def next_pow2(v: int) -> int:
            p = 1
            while p < v:
                p *= 2
            return p
        W, H = next_pow2(W), next_pow2(H)
    if W * H > args.max_pixels:
        raise SFError("SHEET_TOO_LARGE", f"planned sheet {W}x{H} exceeds --max-pixels {args.max_pixels}",
                      hint="split into a multi-sheet set (see --sheets) or reduce --cols")
    names = re.sub(r"\{(\w+)\}", lambda m: {
        "index": "0001", "n": "1", "action": args.action, "direction": args.direction,
    }.get(m.group(1), m.group(0)), args.pattern)
    plan = {
        "cell": list(cell), "frames": frames, "cols": cols, "rows": rows,
        "padding": pad, "margin": args.margin, "sheet": [W, H],
        "power_of_two": bool(args.pow2),
        "used_ratio": round((frames * cell[0] * cell[1]) / (W * H), 3),
        "naming": {"pattern": args.pattern, "example": names},
        "anchors": {"pivot": args.pivot, "origin": "top-left", "y_down": True},
        "frame_order": "row-major left-to-right, top-to-bottom",
        "warnings": [],
    }
    if args.multi_sheet and frames > args.multi_sheet:
        plan["sheets"] = math.ceil(frames / args.multi_sheet)
        plan["frames_per_sheet"] = args.multi_sheet
        plan["warnings"].append(f"split into {plan['sheets']} sheets for memory budget")
    if plan["used_ratio"] < 0.5:
        plan["warnings"].append("sheet utilisation below 50%; consider fewer columns or tighter padding")
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(plan, out)
        rep.add_output(out, "sheet-plan")
    rep.stat("sheet", [W, H])
    return {"summary": {"sheet": [W, H], "rows": rows, "cols": cols, "used_ratio": plan["used_ratio"],
                        "output": str(out)}, "plan": plan}


def _load_action_matrix(args, root: Path) -> dict:
    if args.input:
        p = Path(args.input) if Path(args.input).is_absolute() else root / args.input
        data = core.load_json(p)
        if not isinstance(data, dict):
            raise SFError("BAD_MATRIX", "matrix input must be a JSON object")
        return data
    axes = core.parse_kv_list(args.axis, name="--axis")
    if not axes:
        raise SFError("MISSING_INPUT", "pass --input matrix JSON or at least one --axis name=v1,v2")
    return {"axes": {k: [v.strip() for v in val.split(",") if v.strip()] for k, val in axes.items()}}


def op_action_matrix(args, root: Path, rep: core.Reporter):
    """Build and audit required/optional/blocked action coverage."""
    matrix = _load_action_matrix(args, root)
    axes = matrix.get("axes") or {}
    if not axes:
        raise SFError("BAD_MATRIX", "matrix needs a non-empty 'axes' object")
    status = matrix.get("status") or {}
    rows = []
    missing = blocked = approved = optional = 0
    for axis, actions in axes.items():
        for action in actions:
            st = str(status.get(f"{axis}:{action}", status.get(action, "required"))).lower()
            if st not in {"required", "optional", "blocked", "approved", "missing", "done"}:
                raise SFError("BAD_STATUS", f"status '{st}' for {axis}:{action} is not recognised")
            rows.append({"unit": axis, "action": action, "status": st, "clip": f"{axis}_{action}"})
            missing += st in {"required", "missing"}
            blocked += st == "blocked"
            approved += st in {"approved", "done"}
            optional += st == "optional"
    payload = {
        "axes": axes, "rows": rows, "coverage": {
            "total": len(rows), "approved": approved, "missing": missing,
            "blocked": blocked, "optional": optional,
            "completion_ratio": round(approved / len(rows), 3) if rows else 0.0,
        },
    }
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "action-matrix")
    rep.stat("rows", len(rows))
    return {"summary": payload["coverage"] | {"output": str(out)}}


def op_estimate(args, root: Path, rep: core.Reporter):
    """Estimate frames, sheets, review rounds, storage and stages."""
    units = args.units
    actions = [a.strip() for a in args.actions.split(",") if a.strip()]
    if not actions:
        raise SFError("BAD_ARGUMENT", "--actions needs at least one action name")
    directions = args.directions
    frames_per_action = core.parse_int_list(args.frames_per_action, name="frame counts")
    if units < 1 or directions < 1:
        raise SFError("BAD_ARGUMENT", "units and directions must be >= 1")
    per_unit_actions = len(actions)
    raw_frames = per_unit_actions * directions * (sum(frames_per_action) / len(frames_per_action))
    total_frames = int(math.ceil(raw_frames * units))
    cell_px = args.cell_wh * args.cell_ht
    pixels = total_frames * cell_px
    png_bytes = pixels * (1.4 if args.opaque else 2.2)  # empirical RGBA PNG estimate
    sheet_frames = max(1, args.sheet_frames)
    sheets = math.ceil(total_frames / sheet_frames)
    variants = max(1, args.variants)
    review_rounds = max(1, math.ceil(total_frames / args.frames_per_review))
    person_days = total_frames / args.frames_per_day * variants
    estimate = {
        "inputs": {"units": units, "actions_per_unit": per_unit_actions, "directions": directions,
                   "avg_frames_per_action": sum(frames_per_action) / len(frames_per_action),
                   "variants": variants},
        "outputs": {
            "total_frames": total_frames,
            "sheets": sheets * variants,
            "metadata_files": sheets * variants,
            "estimated_png_bytes": int(png_bytes),
            "estimated_png_mb": round(png_bytes / 1_048_576, 2),
            "source_mb": round(png_bytes * args.source_multiplier / 1_048_576, 2),
        },
        "work": {
            "review_rounds": review_rounds,
            "person_days": round(person_days, 1),
            "stages": ["brief", "concept", "approved-design", "actions", "sheets", "metadata",
                       "validation", "export", "handoff"],
        },
        "assumptions": [
            f"PNG estimate assumes ~{1.4 if args.opaque else 2.2} bytes/pixel",
            "source master size = final size x source_multiplier",
            f"1 review per {args.frames_per_review} frames, 1 round minimum",
            f"{args.frames_per_day} finished frames per person-day",
        ],
    }
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(estimate, out)
        rep.add_output(out, "estimate")
    rep.stat("total_frames", total_frames)
    return {"summary": {"total_frames": total_frames, "sheets": estimate["outputs"]["sheets"],
                        "png_mb": estimate["outputs"]["estimated_png_mb"],
                        "person_days": estimate["work"]["person_days"], "output": str(out)}}


def op_handoff(args, root: Path, rep: core.Reporter):
    """Write a production handoff manifest for a finished or in-progress set."""
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if not src.exists():
        raise SFError("INPUT_NOT_FOUND", f"handoff source folder not found: {src}")
    files = []
    for p in sorted(src.rglob("*")):
        if p.is_file():
            files.append({
                "path": str(p.relative_to(src)).replace("\\", "/"),
                "bytes": p.stat().st_size,
                "sha256": core.file_sha256(p) if p.stat().st_size < args.hash_max_bytes else None,
                "ext": p.suffix.lower(),
            })
    if not files:
        raise SFError("EMPTY_INPUT", f"no files under {src}")
    by_kind: dict[str, list[str]] = {}
    for f in files:
        kind = "source" if f["ext"] in {".psd", ".ase", ".aseprite", ".kra", ".svg", ".blend", ".json"} else \
               "preview" if "preview" in f["path"].lower() or f["ext"] in {".gif", ".webp"} else "export"
        by_kind.setdefault(kind, []).append(f["path"])
    manifest = {
        "asset_set": args.name or src.name,
        "root": str(src),
        "created_utc": core.time.strftime("%Y-%m-%dT%H:%M:%SZ", core.time.gmtime()),
        "file_count": len(files),
        "total_bytes": sum(f["bytes"] for f in files),
        "kinds": {k: len(v) for k, v in by_kind.items()},
        "files": files,
        "checks": {
            "has_sources": bool(by_kind.get("source")),
            "has_exports": bool(by_kind.get("export")),
            "has_previews": bool(by_kind.get("preview")),
            "has_readme": any(f["path"].lower().endswith(("readme.md", "readme.txt")) for f in files),
            "has_license_notes": any("licen" in f["path"].lower() for f in files),
        },
        "tool": f"{core.TOOL_NAME} {core.TOOL_VERSION}",
    }
    for key, ok in manifest["checks"].items():
        if not ok:
            rep.warn("HANDOFF_INCOMPLETE", f"handoff is missing {key}", check=key)
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(manifest, out)
        rep.add_output(out, "handoff-manifest")
    rep.stat("files", len(files))
    return {"summary": {"files": len(files), "bytes": manifest["total_bytes"],
                        "output": str(out)}, "checks": manifest["checks"]}


def _load_records(args, root: Path, key: str) -> dict:
    if args.input:
        p = Path(args.input) if Path(args.input).is_absolute() else root / args.input
        data = core.load_json(p)
        if not isinstance(data, dict):
            raise SFError("BAD_INPUT", "record input must be a JSON object")
        return data
    return {key: []}


def op_version(args, root: Path, rep: core.Reporter):
    """Record version, approval state, lock state and final-file identity."""
    data = _load_records(args, root, "records")
    records = data.setdefault("records", [])
    if not args.asset:
        raise SFError("MISSING_ARGUMENT", "--asset is required to record a version")
    if args.final and not (args.approve and "version-final" in args.approve):
        raise SFError("APPROVAL_REQUIRED", "recording a final (published) version needs approval",
                      hint="re-run with --approve version-final", exit_code=4)
    if args.status not in {"draft", "review", "approved", "final", "locked", "rejected"}:
        raise SFError("BAD_STATUS", "status must be draft/review/approved/final/locked/rejected")
    existing = [r for r in records if r.get("asset") == args.asset]
    version = args.version or f"v{len(existing) + 1}"
    if any(r.get("version") == version for r in existing):
        raise SFError("VERSION_EXISTS", f"version {version} already recorded for {args.asset}")
    identity = None
    if args.file:
        fp = Path(args.file) if Path(args.file).is_absolute() else root / args.file
        if not fp.exists():
            raise SFError("FILE_NOT_FOUND", f"cannot record identity, file missing: {fp}")
        identity = {"file": str(fp), "sha256": core.file_sha256(fp), "bytes": fp.stat().st_size}
    record = {
        "asset": args.asset, "version": version, "status": args.status,
        "reviewer": args.reviewer, "note": args.note, "identity": identity,
        "locked": args.status == "locked", "created_utc": core.time.strftime("%Y-%m-%dT%H:%M:%SZ", core.time.gmtime()),
    }
    if args.status == "approved" and not args.reviewer:
        rep.warn("MISSING_REVIEWER", "approved without --reviewer is weak governance")
    records.append(record)
    if args.status == "approved":
        for r in existing:
            if r.get("status") == "approved":
                r["status"] = "superseded"
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(data, out)
        rep.add_output(out, "version-record")
    rep.stat("versions", len(records))
    return {"summary": {"asset": args.asset, "version": version, "status": args.status,
                        "output": str(out)}, "record": record}


def op_rights(args, root: Path, rep: core.Reporter):
    """Track source, creator, licence, allowed use, credit text and restrictions."""
    data = _load_records(args, root, "rights")
    entries = data.setdefault("rights", [])
    if args.asset:
        missing = [f for f in ("source", "creator", "license") if not getattr(args, f)]
        if missing:
            raise SFError("MISSING_FIELDS", f"rights entry needs {', '.join(missing)}",
                          hint="supply --source, --creator and --license")
        entry = {
            "asset": args.asset, "source": args.source, "creator": args.creator,
            "license": args.license, "allowed_use": args.allowed_use,
            "credit_text": args.credit or f"{args.asset} by {args.creator} ({args.license})",
            "restrictions": [r.strip() for r in (args.restrict or "").split(",") if r.strip()],
            "url": args.url, "recorded_utc": core.time.strftime("%Y-%m-%dT%H:%M:%SZ", core.time.gmtime()),
        }
        if any(e["asset"] == args.asset for e in entries):
            raise SFError("ASSET_EXISTS", f"rights already recorded for {args.asset}",
                          hint="use a new --asset key or edit the JSON record manually")
        risky = [lic for lic in ("unknown", "none", "proprietary", "all-rights-reserved")
                 if lic in args.license.lower()]
        if risky:
            rep.warn("LICENSE_REVIEW", f"licence '{args.license}' needs legal review before shipping")
        entries.append(entry)
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    credits = []
    for e in entries:
        credits.append(f"- {e['asset']}: {e['credit_text']} [{e['license']}]")
    data["credits_markdown"] = "# Credits\n\n" + ("\n".join(credits) if credits else "_none recorded_") + "\n"
    if not args.dry_run:
        core.save_json(data, out)
        rep.add_output(out, "rights-record")
        if args.credits:
            cp = core.guard_output(root / args.credits, root, args.allow_outside_root)
            cp.write_text(data["credits_markdown"], encoding="utf-8")
            rep.add_output(cp, "credits-file")
    rep.stat("entries", len(entries))
    return {"summary": {"entries": len(entries), "output": str(out)}}


def op_style_bible(args, root: Path, rep: core.Reporter):
    """Build the project style bible from tokens, targets and rules."""
    data = _load_records(args, root, "style")
    if args.tokens:
        p = Path(args.tokens) if Path(args.tokens).is_absolute() else root / args.tokens
        tokens = core.load_json(p)
        data["tokens"] = tokens.get("tokens", tokens)
    data["project"] = args.project
    data["canvas"] = args.canvas
    data["rules"] = {
        "outline": args.outline,
        "palette_limit": args.palette_limit,
        "pixel_grid": args.pixel_grid,
        "shading_steps": args.shading_steps,
        "allowed_scale_factors": core.parse_int_list(args.scales, name="scales"),
        "line_weight_px": args.line_weight,
    }
    data["lighting"] = {"key": args.light_key, "ambient": args.light_ambient}
    data["camera"] = {"projection": args.projection, "default_direction": args.direction}
    data["export"] = {"formats": [f.strip() for f in args.formats.split(",") if f.strip()],
                      "naming": args.naming,
                      "targets": [t.strip() for t in args.targets.split(",") if t.strip()]}
    data["do_not"] = [d.strip() for d in args.do_not.split(",") if d.strip()]
    md = _style_bible_markdown(data)
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(data, out)
        rep.add_output(out, "style-bible")
        if args.markdown:
            mp = core.guard_output(root / args.markdown, root, args.allow_outside_root)
            mp.write_text(md, encoding="utf-8")
            rep.add_output(mp, "style-bible-markdown")
    return {"summary": {"project": args.project, "rules": len(data["rules"]), "output": str(out)}}


def _style_bible_markdown(data: dict) -> str:
    r = data["rules"]
    tokens = data.get("tokens", {}).get("roles", {}) if isinstance(data.get("tokens"), dict) else {}
    lines = [f"# Style Bible - {data['project']}", "", f"Canvas: {data['canvas']}", "",
             "## Rules", "",
             f"- Outline: {r['outline']}", f"- Palette limit: {r['palette_limit']} colours",
             f"- Pixel grid: {r['pixel_grid']}", f"- Shading steps: {r['shading_steps']}",
             f"- Allowed scale factors: {r['allowed_scale_factors']}", f"- Line weight: {r['line_weight_px']} px",
             "", "## Lighting", "", f"- Key: {data['lighting']['key']}", f"- Ambient: {data['lighting']['ambient']}",
             "", "## Camera", "", f"- Projection: {data['camera']['projection']}",
             f"- Default direction: {data['camera']['default_direction']}", "",
             "## Export", "", f"- Formats: {', '.join(data['export']['formats'])}",
             f"- Naming: {data['export']['naming']}", f"- Targets: {', '.join(data['export']['targets'])}"]
    if tokens:
        lines += ["", "## Palette roles", "", "| Role | Colour |", "| --- | --- |"]
        lines += [f"| {k} | `{v}` |" for k, v in tokens.items()]
    if data["do_not"]:
        lines += ["", "## Do not", ""] + [f"- {d}" for d in data["do_not"]]
    return "\n".join(lines) + "\n"


def op_state_machine(args, root: Path, rep: core.Reporter):
    """Map sprite clips into states, transitions, conditions and priorities."""
    data = _load_records(args, root, "states")
    if args.input:
        p = Path(args.input) if Path(args.input).is_absolute() else root / args.input
        data = core.load_json(p)
    states = data.get("states")
    if not states and args.states:
        states = []
        for item in args.states.split(","):
            name, _, clip = item.partition("=")
            states.append({"name": name.strip(), "clip": (clip or name).strip(), "loop": True})
    if not states:
        raise SFError("MISSING_INPUT", "pass --states idle=hero_idle,run=hero_run or --input state JSON")
    names = {s.get("name") for s in states}
    if None in names:
        raise SFError("BAD_STATE", "every state needs a name")
    transitions = data.get("transitions") or []
    for t in transitions:
        for key in ("from", "to"):
            if t.get(key) not in names and t.get(key) != "*":
                raise SFError("BAD_TRANSITION", f"transition references unknown state '{t.get(key)}'")
    reachable = {s["name"] for s in states if s.get("initial") or s.get("name") == (data.get("initial") or states[0]["name"])}
    changed = True
    while changed:
        changed = False
        for t in transitions:
            if (t.get("from") in reachable or t.get("from") == "*") and t.get("to") not in reachable:
                reachable.add(t["to"])
                changed = True
    unreachable = sorted(names - reachable)
    if unreachable:
        rep.warn("UNREACHABLE_STATES", f"states with no incoming transition: {', '.join(unreachable)}",
                 states=unreachable)
    payload = {
        "initial": data.get("initial") or states[0]["name"],
        "states": states, "transitions": transitions,
        "unreachable": unreachable,
        "engine_hints": {
            "unity": "Animator: one state per clip, transitions on parameters with 'has exit time' off for action states",
            "godot": "AnimationTree StateMachine node; transitions via expression or advance",
            "generic": "table-driven: evaluate transitions in priority order, first match wins",
        },
    }
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "state-machine")
    return {"summary": {"states": len(states), "transitions": len(transitions),
                        "unreachable": len(unreachable), "output": str(out)}}


def op_dependency(args, root: Path, rep: core.Reporter):
    """Track which outputs depend on each source asset."""
    if not args.edge:
        raise SFError("MISSING_INPUT", "provide at least one --edge source=output (repeatable)")
    edges = []
    nodes: dict[str, dict] = {}
    for raw in args.edge:
        if "=" not in raw:
            raise SFError("BAD_EDGE", f"--edge expects SOURCE=OUTPUT, got '{raw}'")
        src, dst = (x.strip() for x in raw.split("=", 1))
        if not src or not dst:
            raise SFError("BAD_EDGE", f"--edge needs both sides, got '{raw}'")
        edges.append({"from": src, "to": dst})
        nodes.setdefault(src, {"id": src, "kind": "source", "dependents": []})
        nodes.setdefault(dst, {"id": dst, "kind": "output", "dependents": []})
        nodes[src]["dependents"].append(dst)
    dependents_of: dict[str, list[str]] = {}
    for e in edges:
        dependents_of.setdefault(e["from"], []).append(e["to"])

    def closure(node: str, seen: set[str] | None = None) -> set[str]:
        seen = seen or set()
        for nxt in dependents_of.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                closure(nxt, seen)
        return seen

    for node in nodes:
        nodes[node]["transitive_dependents"] = sorted(closure(node) - {node})
    payload = {"nodes": sorted(nodes.values(), key=lambda n: n["id"]), "edges": edges,
               "roots": sorted(n["id"] for n in nodes.values() if not any(n["id"] == e["to"] for e in edges))}
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "dependency-graph")
    heaviest = max((n["id"] for n in nodes.values()), key=lambda i: len(nodes[i]["transitive_dependents"]), default=None)
    return {"summary": {"nodes": len(nodes), "edges": len(edges), "heaviest_source": heaviest,
                        "output": str(out)}}


def op_router(args, root: Path, rep: core.Reporter):
    """Route a request to the smallest set of capabilities and their handoff order."""
    caps = load_capability_asset()
    skills = all_skills(caps)
    query = args.query or (args.text or "")
    if not query and args.input:
        p = Path(args.input) if Path(args.input).is_absolute() else root / args.input
        query = p.read_text(encoding="utf-8", errors="replace")
    if not query.strip():
        raise SFError("MISSING_INPUT", "pass --query, --text or --input")
    tokens = tokenise(query)
    idf = build_index(skills)
    scored = []
    for s in skills:
        sc = score_skill(tokens, s, idf)
        if sc > 0:
            scored.append((sc, s))
    scored.sort(key=lambda pair: -pair[0])
    picked = []
    seen_ops = set()
    for sc, s in scored:
        if len(picked) >= args.limit:
            break
        # prefer coverage breadth: skip near-duplicate primitive once chosen
        if s["primitive"] in seen_ops:
            continue
        seen_ops.add(s["primitive"])
        picked.append({"catalog_id": s["id"], "skill": s["name"], "category": s["category"],
                       "score": round(sc, 3), "primitive": s["primitive"],
                       "module": s["module"], "operation": s["operation"],
                       "command": f'python3 {s["module"]} {s["operation"]} --help',
                       "approval_required": s.get("approval_required", False)})
    if not picked:
        raise SFError("NO_MATCH", "no capability matched the request",
                      hint="use broader words (walk, atlas, validate) or raise --limit")
    order = ["plan", "author", "edit", "sheet", "anim", "validate", "convert", "engine", "govern", "pipeline"]
    picked.sort(key=lambda p: (order.index(p["module"].split("-")[-1].replace(".py", ""))
                               if p["module"].split("-")[-1].replace(".py", "") in order else 99, -p["score"]))
    payload = {
        "request": query[:2000],
        "capabilities": picked,
        "handoff_order": [p["primitive"] for p in picked],
        "guarded": [p["skill"] for p in picked if p["approval_required"]],
        "coverage_note": "one capability per primitive is selected; run --limit higher for alternates",
    }
    if args.output:
        out = core.guard_output(root / args.output, root, args.allow_outside_root)
        if not args.dry_run:
            core.save_json(payload, out)
            rep.add_output(out, "routing-plan")
    rep.stat("matched", len(scored))
    return {"summary": {"selected": len(picked), "matched": len(scored),
                        "order": payload["handoff_order"]}, "capabilities": picked}


CHANGE_PATTERNS = [
    (r"\b(frame|f)\s*#?\s*(\d{1,4})\b", "frame"),
    (r"\b(layer|part)\s*[:\-]?\s*([A-Za-z0-9_\- ]{2,24})", "layer"),
    (r"#(?:[0-9a-fA-F]{6})\b", "colour"),
    (r"\b(\d{1,3})\s*ms\b", "timing_ms"),
    (r"\b(faster|slower|quicker|delay|earlier|later)\b", "timing_note"),
    (r"\b(pivot|anchor|origin|socket)\b", "anchor"),
    (r"\b(export|sheet|atlas|png|webp|gif)\b", "export"),
    (r"\b(scale|size|resize|larger|smaller|px)\b", "size"),
    (r"\b(colour|color|palette|hue|tint|shade)\b", "colour"),
    (r"\b(outline|edge|halo)\b", "outline"),
]


def op_change_request(args, root: Path, rep: core.Reporter):
    """Turn reviewer comments into exact frame/layer/colour/timing/export tasks."""
    if args.text:
        raw = args.text
    elif args.input:
        p = Path(args.input) if Path(args.input).is_absolute() else root / args.input
        if not p.exists():
            raise SFError("INPUT_NOT_FOUND", f"notes file not found: {p}")
        raw = p.read_text(encoding="utf-8", errors="replace")
    else:
        raise SFError("MISSING_INPUT", "pass --text or --input with reviewer notes")
    chunks: list[str] = []
    for ln in normalise_source(raw).split("\n"):
        for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z])", ln.strip(" -*\t")):
            if piece.strip():
                chunks.append(piece.strip(" -*\t"))
    notes = chunks
    if not notes:
        raise SFError("EMPTY_INPUT", "no reviewer notes found")
    tasks = []
    for idx, note in enumerate(notes, start=1):
        kinds, evidence = [], {}
        for pattern, kind in CHANGE_PATTERNS:
            for m in re.finditer(pattern, note, re.I):
                if kind not in kinds:
                    kinds.append(kind)
                if kind == "frame":
                    evidence.setdefault("frames", set()).add(int(m.group(2)))
                elif kind == "layer":
                    evidence.setdefault("layers", set()).add(m.group(2).strip())
                elif kind == "colour":
                    evidence.setdefault("colours", set()).add(m.group(0) if m.group(0).startswith("#") else note.strip())
                elif kind == "timing_ms":
                    evidence.setdefault("timings", set()).add(int(m.group(1)))
        if not kinds:
            kinds = ["clarify"]
        task = {
            "id": f"CR-{idx:03d}", "note": note, "kinds": kinds,
            "frames": sorted(evidence.get("frames", [])),
            "layers": sorted(evidence.get("layers", [])),
            "colours": sorted(evidence.get("colours", [])),
            "timings_ms": sorted(evidence.get("timings", [])),
            "priority": "high" if re.search(r"\b(block|broken|wrong|missing|crash)\b", note, re.I) else "normal",
            "action": _change_action(kinds),
        }
        if kinds == ["clarify"]:
            rep.warn("NEEDS_CLARIFICATION", f"note {idx} has no actionable target", note=note)
        tasks.append(task)
    payload = {"task_count": len(tasks), "tasks": tasks,
               "clarifications": [t["id"] for t in tasks if t["kinds"] == ["clarify"]]}
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "change-request")
    rep.stat("tasks", len(tasks))
    return {"summary": {"tasks": len(tasks), "clarifications": len(payload["clarifications"]),
                        "output": str(out)}, "tasks": tasks}


def _change_action(kinds: list[str]) -> str:
    mapping = {
        "frame": "target the listed frames only; keep neighbouring frames unchanged",
        "layer": "edit the named layer/part; preserve layer order and anchors",
        "colour": "recolour within the approved palette ramp; keep value contrast",
        "timing_ms": "retime the frame list; keep total clip length in sync",
        "timing_note": "adjust spacing/anticipation in the named segment",
        "anchor": "fix pivot/socket data and re-validate across frames",
        "export": "re-export the affected sheets and metadata only",
        "size": "resize or rescale and re-run sheet validation",
        "outline": "apply the style bible outline rule to the affected frames",
        "clarify": "ask the reviewer for a concrete frame/layer/colour target",
    }
    return "; ".join(mapping.get(k, k) for k in kinds)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _p_catalog(p):
    p.add_argument("--input", "-i", help="catalog text/Markdown/HTML file")
    p.add_argument("--text", help="inline catalog text")
    p.add_argument("--self", action="store_true", help="dump the bundled 154-capability catalog")
    p.add_argument("--query", help="score skills against this query")
    p.add_argument("--category", action="append", default=[], help="keep only these categories (repeatable)")
    p.add_argument("--output", "-o", default="catalog.json")


def _p_sheet_plan(p):
    p.add_argument("--cell", default="64x64", help="frame cell size WxH")
    p.add_argument("--frames", type=int, required=True)
    p.add_argument("--cols", type=int, default=0, help="columns (0 = auto square-ish)")
    p.add_argument("--padding", type=int, default=0)
    p.add_argument("--margin", type=int, default=0)
    p.add_argument("--pow2", action="store_true", help="round sheet size up to a power of two")
    p.add_argument("--multi-sheet", type=int, default=0, help="split into sheets of at most N frames")
    p.add_argument("--pattern", default="{action}_{direction}_{index:04d}", help="frame naming pattern")
    p.add_argument("--action", default="idle")
    p.add_argument("--direction", default="s")
    p.add_argument("--pivot", default="bottom-center")
    p.add_argument("--max-pixels", type=int, default=64_000_000)
    p.add_argument("--output", "-o", default="sheet-plan.json")


def _p_action_matrix(p):
    p.add_argument("--input", "-i", help="matrix JSON with axes")
    p.add_argument("--axis", action="append", default=[], metavar="UNIT=a1,a2")
    p.add_argument("--output", "-o", default="action-matrix.json")


def _p_estimate(p):
    p.add_argument("--units", type=int, default=1)
    p.add_argument("--actions", default="idle,walk,attack,hit,death", help="action names comma separated")
    p.add_argument("--directions", type=int, default=8)
    p.add_argument("--frames-per-action", default="6")
    p.add_argument("--cell-wh", type=int, default=64)
    p.add_argument("--cell-ht", type=int, default=64)
    p.add_argument("--opaque", action="store_true", help="estimate for opaque (no alpha) art")
    p.add_argument("--variants", type=int, default=1)
    p.add_argument("--sheet-frames", type=int, default=64)
    p.add_argument("--frames-per-review", type=int, default=40)
    p.add_argument("--frames-per-day", type=float, default=25.0)
    p.add_argument("--source-multiplier", type=float, default=4.0)
    p.add_argument("--output", "-o", default="estimate.json")


def _p_handoff(p):
    p.add_argument("--input", "-i", required=True, help="folder to package")
    p.add_argument("--name", help="asset set name")
    p.add_argument("--hash-max-bytes", type=int, default=64 * 1024 * 1024)
    p.add_argument("--output", "-o", default="handoff.json")


def _p_version(p):
    p.add_argument("--input", "-i", help="existing version record JSON")
    p.add_argument("--asset", required=True)
    p.add_argument("--version", help="version label (default: next increment)")
    p.add_argument("--status", default="draft",
                   choices=["draft", "review", "approved", "final", "locked", "rejected"])
    p.add_argument("--reviewer")
    p.add_argument("--note")
    p.add_argument("--file", help="final file to fingerprint")
    p.add_argument("--final", action="store_true", help="shortcut: mark as final (needs --approve version-final)")
    p.add_argument("--output", "-o", default="versions.json")


def _p_rights(p):
    p.add_argument("--input", "-i", help="existing rights JSON")
    p.add_argument("--asset", help="asset key to add")
    p.add_argument("--source", help="where it came from")
    p.add_argument("--creator")
    p.add_argument("--license")
    p.add_argument("--allowed-use", default="project use")
    p.add_argument("--credit", help="credit text (default generated)")
    p.add_argument("--restrict", help="comma separated restrictions")
    p.add_argument("--url")
    p.add_argument("--credits", help="also write a credits Markdown file here")
    p.add_argument("--output", "-o", default="rights.json")


def _p_style(p):
    p.add_argument("--input", "-i", help="existing style bible JSON")
    p.add_argument("--tokens", help="palette/tokens JSON to embed")
    p.add_argument("--project", default="untitled")
    p.add_argument("--canvas", default="64x64")
    p.add_argument("--outline", default="1px outer, dark")
    p.add_argument("--palette-limit", type=int, default=24)
    p.add_argument("--pixel-grid", default="1x1 (integer scale only)")
    p.add_argument("--shading-steps", type=int, default=3)
    p.add_argument("--scales", default="1,2,4")
    p.add_argument("--line-weight", type=int, default=1)
    p.add_argument("--light-key", default="top-left 45deg")
    p.add_argument("--light-ambient", default="neutral 20%")
    p.add_argument("--projection", default="orthographic")
    p.add_argument("--direction", default="south")
    p.add_argument("--formats", default="png,json")
    p.add_argument("--naming", default="{unit}_{action}_{direction}_{index:04d}")
    p.add_argument("--targets", default="generic")
    p.add_argument("--do-not", default="no photobashing, no baked outlines on FX, no off-palette highlights")
    p.add_argument("--markdown", help="also write the style bible as Markdown here")
    p.add_argument("--output", "-o", default="style-bible.json")


def _p_state(p):
    p.add_argument("--input", "-i", help="state machine JSON")
    p.add_argument("--states", help="comma list name=clip")
    p.add_argument("--output", "-o", default="states.json")


def _p_dep(p):
    p.add_argument("--edge", action="append", default=[], metavar="SRC=DST")
    p.add_argument("--output", "-o", default="dependencies.json")


def _p_router(p):
    p.add_argument("--query")
    p.add_argument("--text")
    p.add_argument("--input", "-i")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--output", "-o")


def _p_change(p):
    p.add_argument("--input", "-i", help="reviewer notes file")
    p.add_argument("--text", help="inline reviewer notes")
    p.add_argument("--output", "-o", default="change-request.json")


OPS = {
    "catalog": (_p_catalog, op_catalog),
    "sheet-plan": (_p_sheet_plan, op_sheet_plan),
    "action-matrix": (_p_action_matrix, op_action_matrix),
    "estimate": (_p_estimate, op_estimate),
    "handoff": (_p_handoff, op_handoff),
    "version": (_p_version, op_version),
    "rights": (_p_rights, op_rights),
    "style-bible": (_p_style, op_style_bible),
    "state-machine": (_p_state, op_state_machine),
    "dependency": (_p_dep, op_dependency),
    "router": (_p_router, op_router),
    "change-request": (_p_change, op_change_request),
}

if __name__ == "__main__":
    raise SystemExit(core.run_ops(
        "plan",
        "spriteforge plan: production planning, routing, governance records and handoff",
        OPS,
    ))
