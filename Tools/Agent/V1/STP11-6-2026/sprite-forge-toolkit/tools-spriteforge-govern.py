#!/usr/bin/env python3
"""spriteforge govern: style gates, licence rollups and batch write approval.

Operations
----------
  style-gate   block exports that break size/palette/outline/naming/anchor rules
  license      roll up licences across a folder and write a credits file
  batch-gate   require explicit approval before bulk overwrite/delete/publish

All three are guarded: they need ``--approve <token>`` because they decide what
may ship. ``style-gate`` is read-only; ``batch-gate`` never deletes anything by
itself, it only authorises the destructive step you are about to run and records
the decision.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sfcore_govern", HERE / "tools-spriteforge-core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["sfcore_govern"] = core
_spec.loader.exec_module(core)  # type: ignore[union-attr]

SFError = core.SFError
Image = core.Image

LICENSE_TAGS = {
    "cc0": ("public-domain", "no attribution required"),
    "cc-by": ("attribution", "credit the creator"),
    "cc-by-sa": ("attribution-sharealike", "credit + share derivative under same licence"),
    "cc-by-nc": ("non-commercial", "no commercial use; needs review for paid products"),
    "cc-by-nd": ("no-derivatives", "cannot ship modified versions"),
    "ofl": ("font", "keep the font licence file with the font"),
    "mit": ("permissive-code", "keep the copyright notice"),
    "apache": ("permissive-code", "keep NOTICE file"),
    "gpl": ("copyleft", "derivatives must be GPL; usually unsuitable for game art"),
    "proprietary": ("restricted", "written permission required"),
    "unknown": ("unknown", "treat as unusable until clarified"),
}


def _norm(text: str) -> str:
    """Normalise licence wording: lowercase, unify separators and spacing."""
    low = text.lower()
    low = re.sub(r"[_/]+", "-", low)
    low = re.sub(r"\bcc[- ]?by\b", "cc-by", low)
    low = re.sub(r"\bcc[- ]?0\b", "cc0", low)
    low = re.sub(r"\s*-\s*", "-", low)
    low = re.sub(r"\s+", " ", low)
    return low


# Most specific first: "cc-by-sa" must win over "cc-by".
LICENSE_PATTERNS: list[tuple[str, str]] = [
    (r"\bcc-by-sa\b", "cc-by-sa"),
    (r"\bcc-by-nc-nd\b|\bcc-by-nd-nc\b", "cc-by-nc"),
    (r"\bcc-by-nc\b", "cc-by-nc"),
    (r"\bcc-by-nd\b", "cc-by-nd"),
    (r"\bcc-by\b", "cc-by"),
    (r"\bcc0\b|\bcreative commons zero\b|\bpublic domain\b|\bcc-public-domain\b", "cc0"),
    (r"\bopen font licen[cs]e\b|\bofl\b", "ofl"),
    (r"\bapache\b(?: licen[cs]e)?\s*(?:2|version 2)?", "apache"),
    (r"\bmit licen[cs]e\b|\bmit\b", "mit"),
    (r"\bgnu (?:general public|gpl)\b|\bgpl-?[23]?\b|\bagpl\b", "gpl"),
    (r"\ball rights reserved\b|\bproprietary\b|\bcommercial licen[cs]e\b", "proprietary"),
    (r"\b(?:no|unknown) licen[cs]e\b|\bunlicen[cs]ed\b", "unknown"),
]


def classify_license(text: str) -> dict:
    """Classify licence text into one of LICENSE_TAGS, most specific match first."""
    norm = _norm(text)
    for pattern, tag in LICENSE_PATTERNS:
        if re.search(pattern, norm):
            kind, note = LICENSE_TAGS[tag]
            return {"tag": tag, "kind": kind, "note": note}
    return {"tag": "unknown", "kind": "unknown", "note": LICENSE_TAGS["unknown"][1]}


# --------------------------------------------------------------------------- #
# style gate
# --------------------------------------------------------------------------- #

def op_style_gate(args, root: Path, rep: core.Reporter):
    """Block exports that break approved size, palette, outline, naming or anchor rules."""
    core.require_approval(args, "govern-stylegate",
                          "a style gate decides what may ship, so a human owns the rule set")
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    files = core.list_images(src, recursive=True) if src.is_dir() else [src]
    if not files:
        raise SFError("NO_FRAMES", f"no images under {src}")
    violations: list[dict] = []
    checked = 0
    allowed = None
    if args.palette:
        data = core.load_json(Path(args.palette) if Path(args.palette).is_absolute() else root / args.palette)
        block = data.get("roles", data) if isinstance(data, dict) else data
        values = block.values() if isinstance(block, dict) else block
        allowed = {tuple(int(str(v).lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) for v in values}
    expected = core.parse_size(args.size) if args.size else None
    name_re = re.compile(args.name_pattern) if args.name_pattern else None
    for p in files:
        rep.deadline.check()
        checked += 1
        im = core.load_image(p, max_pixels=args.max_pixels)
        if expected and im.size != expected:
            violations.append({"file": p.name, "rule": "size", "expected": list(expected),
                               "actual": list(im.size)})
        if name_re and not name_re.match(p.stem):
            violations.append({"file": p.name, "rule": "naming", "pattern": args.name_pattern})
        if allowed is not None:
            counts = core.color_counts(im)
            off = {c[:3] for c, n in counts.items() if c[3] > 0 and c[:3] not in allowed}
            if len(off) > args.max_off_palette:
                violations.append({"file": p.name, "rule": "palette", "off_palette_colours": len(off)})
        if args.require_alpha and not core.has_transparency(im):
            violations.append({"file": p.name, "rule": "alpha", "message": "no transparency"})
        if args.require_outline:
            alpha = im.split()[-1].point(lambda v: 255 if v > 0 else 0)
            edge = alpha.filter(core.ImageFilter.FIND_EDGES)
            dark = sum(1 for px in core.iter_pixels(im) if px[3] > 200 and max(px[:3]) < 90)
            if dark / max(1, core.opaque_pixels(edge)) < args.outline_ratio:
                violations.append({"file": p.name, "rule": "outline",
                                   "message": "no dark outline pixels detected"})
        if args.min_content_px:
            bbox = core.alpha_bbox(im)
            if not bbox or (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) < args.min_content_px:
                violations.append({"file": p.name, "rule": "size",
                                   "message": f"content below {args.min_content_px}px"})
    blocked = bool(violations)
    payload = {"check": "style-gate", "input": str(src), "files_checked": checked,
               "rules": {"size": args.size, "palette": bool(allowed), "naming": args.name_pattern,
                         "require_alpha": bool(args.require_alpha),
                         "require_outline": bool(args.require_outline)},
               "violations": violations, "blocked": blocked, "verdict": "block" if blocked else "pass"}
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "gate-report")
    for v in violations:
        rep.warn("GATE_VIOLATION", f"{v['file']}: {v['rule']}")
    rep.stat("violations", len(violations))
    return {"summary": {"files_checked": checked, "violations": len(violations),
                        "blocked": blocked, "output": str(out)}}


# --------------------------------------------------------------------------- #
# licence rollup
# --------------------------------------------------------------------------- #

def op_license(args, root: Path, rep: core.Reporter):
    """Roll up licences across a folder and prepare a credits file."""
    core.require_approval(args, "govern-license",
                          "licence records are a legal deliverable; confirm they are accurate")
    src = Path(args.input) if Path(args.input).is_absolute() else root / args.input
    if src.is_dir():
        candidates = [p for p in src.rglob("*") if p.is_file() and
                      (p.suffix.lower() in {".txt", ".md", ".json", ".csv"} or
                       "licen" in p.name.lower() or "credit" in p.name.lower())]
        records = []
        if args.records:
            rp = Path(args.records) if Path(args.records).is_absolute() else root / args.records
            data = core.load_json(rp)
            records = data.get("rights", data) if isinstance(data, dict) else data
        entries = []
        for r in records:
            lic = classify_license(str(r.get("license", "")))
            entries.append({"asset": r.get("asset"), "license": r.get("license"),
                            "creator": r.get("creator"), "source": r.get("source"),
                            "credit_text": r.get("credit_text") or f"{r.get('asset')} by {r.get('creator')}",
                            **lic})
        scanned = [{"file": p.name, "hint": p.read_text(encoding="utf-8", errors="replace")[:400].strip()}
                   for p in candidates[: args.max_files]]
    else:
        text = src.read_text(encoding="utf-8", errors="replace")
        entries = [{"asset": src.stem, "license": text[:200], "creator": args.creator,
                    "source": args.source, "credit_text": args.credit or src.stem,
                    **classify_license(text)}]
        scanned = [{"file": src.name, "hint": text[:400]}]
    risky = [e for e in entries if e["kind"] in {"non-commercial", "no-derivatives", "copyleft", "restricted", "unknown"}]
    for e in risky:
        rep.warn("LICENSE_RISK", f"{e['asset']}: {e['kind']} ({e['note']})")
    credits = ["# Credits", ""] + [f"- {e['asset']}: {e['credit_text']} ({e['license']})" for e in entries]
    payload = {"entries": entries, "risky": [e["asset"] for e in risky],
               "scanned_files": scanned,
               "summary_by_kind": _kind_counts(entries),
               "credits_markdown": "\n".join(credits) + "\n"}
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        core.save_json(payload, out)
        rep.add_output(out, "license-rollup")
        if args.credits:
            cp = core.guard_output(root / args.credits, root, args.allow_outside_root)
            cp.write_text(payload["credits_markdown"], encoding="utf-8")
            rep.add_output(cp, "credits-file")
    rep.stat("entries", len(entries))
    return {"summary": {"entries": len(entries), "risky": len(risky),
                        "kinds": payload["summary_by_kind"], "output": str(out)}}


def _kind_counts(entries: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    return dict(sorted(counts.items()))


# --------------------------------------------------------------------------- #
# batch gate
# --------------------------------------------------------------------------- #

def op_batch_gate(args, root: Path, rep: core.Reporter):
    """Authorise (and record) a bulk overwrite, delete or publish step."""
    core.require_approval(args, "govern-batch-gate",
                          "bulk destructive actions need an explicit human approval")
    if args.action not in {"overwrite", "delete", "publish"}:
        raise SFError("BAD_ACTION", "action must be overwrite, delete or publish")
    target = Path(args.target) if Path(args.target).is_absolute() else root / args.target
    if not target.exists():
        raise SFError("TARGET_NOT_FOUND", f"target does not exist: {target}")
    if target.is_dir():
        files = [p for p in target.rglob("*") if p.is_file()]
    else:
        files = [target]
    if len(files) > args.max_files:
        raise SFError("TOO_MANY_FILES", f"{len(files)} files exceeds --max-files {args.max_files}")
    total = sum(p.stat().st_size for p in files)
    record = {
        "action": args.action, "target": str(target), "files": len(files), "bytes": total,
        "operator": args.operator, "reason": args.reason,
        "performed_utc": core.time.strftime("%Y-%m-%dT%H:%M:%SZ", core.time.gmtime()),
        "tool": f"{core.TOOL_NAME} {core.TOOL_VERSION}",
        "note": "this record authorises the action; the destructive step itself is run separately",
        "approved": True,
    }
    if not args.reason:
        rep.warn("NO_REASON", "recording a batch write without --reason weakens the audit trail")
    out = core.guard_output(root / args.output, root, args.allow_outside_root)
    if not args.dry_run:
        existing = core.load_json(out) if out.exists() else {"records": []}
        if not isinstance(existing, dict):
            existing = {"records": []}
        existing.setdefault("records", []).append(record)
        core.save_json(existing, out)
        rep.add_output(out, "batch-approval")
    return {"summary": {"action": args.action, "files": len(files), "bytes": total,
                        "recorded": str(out)}}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _p_gate(p):
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", default="style-gate.json")
    p.add_argument("--size", help="required WxH for every file")
    p.add_argument("--palette", help="palette JSON every colour must belong to")
    p.add_argument("--max-off-palette", type=int, default=0)
    p.add_argument("--name-pattern", default=r"^[a-z0-9]+_[a-z]+(?:_[a-z]{1,2})?(?:_\d{4})?$")
    p.add_argument("--require-alpha", action="store_true", default=True)
    p.add_argument("--require-outline", action="store_true")
    p.add_argument("--outline-ratio", type=float, default=0.05)
    p.add_argument("--min-content-px", type=int, default=0)
    p.add_argument("--max-pixels", type=int, default=64_000_000)


def _p_license(p):
    p.add_argument("--input", "-i", required=True, help="licence text file or project folder")
    p.add_argument("--records", help="rights JSON from plan.py rights")
    p.add_argument("--creator")
    p.add_argument("--source")
    p.add_argument("--credit")
    p.add_argument("--credits", help="write a credits Markdown file here")
    p.add_argument("--max-files", type=int, default=200)
    p.add_argument("--output", "-o", default="licenses.json")


def _p_batch_gate(p):
    p.add_argument("--action", required=True, choices=["overwrite", "delete", "publish"])
    p.add_argument("--target", required=True, help="file or folder affected")
    p.add_argument("--operator", default="unspecified")
    p.add_argument("--reason", required=True, help="why this bulk action is safe")
    p.add_argument("--max-files", type=int, default=10000)
    p.add_argument("--output", "-o", default="batch-approvals.json")


OPS = {
    "style-gate": (_p_gate, op_style_gate),
    "license": (_p_license, op_license),
    "batch-gate": (_p_batch_gate, op_batch_gate),
}

if __name__ == "__main__":
    raise SystemExit(core.run_ops(
        "govern",
        "spriteforge govern: style gates, licence rollups and batch write approval",
        OPS,
    ))
