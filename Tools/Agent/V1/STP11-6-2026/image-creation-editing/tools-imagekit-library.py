"""imagekit library: asset indexing, versions, dedupe, naming, rights, proofing
galleries, handoff bundles, usage guides and preservation planning.

File-changing actions (rename, quarantine, move) are dry-run by default and
require --approve. Nothing is deleted: duplicates are quarantined, never removed.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import shutil
from pathlib import Path

import imagekit_core as core


def _library_dir(ctx, a):
    raw = getattr(a, "library", None) or ctx.config.paths.get("library_dir") or "library"
    p = Path(raw).expanduser().resolve()
    if not p.exists():
        raise core.input_error(f"Library folder not found: {p}",
                               "Pass --library DIR pointing at a folder of images.")
    if not p.is_dir():
        raise core.input_error(f"--library is not a directory: {p}")
    return p


def _scan(root, limit):
    files = [p for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower() in core.IMAGE_EXTS]
    if len(files) > limit:
        raise core.input_error(
            f"{len(files)} images in the library exceed limits.max_batch_items ({limit})",
            "Index a subfolder or raise the limit deliberately.")
    return files


def build_index(ctx, root, files, tag=None):
    records = []
    for p in files:
        try:
            info = core.image_info(p)
        except Exception as exc:
            records.append({"path": core.rel_posix(p, root), "error": str(exc)})
            continue
        rel = core.rel_posix(p, root)
        parts = rel.split("/")
        m = re.search(r"-(draft|review|approved|final|superseded)\b", p.stem)
        records.append({
            "path": rel,
            "folder": parts[0] if len(parts) > 1 else "",
            "name": p.name,
            "ext": p.suffix.lstrip(".").lower(),
            "width": info["width"], "height": info["height"],
            "aspect": core.aspect_label(info["width"], info["height"]),
            "mode": info["mode"], "format": info["format"],
            "bytes": info["bytes"], "dpi": info["dpi"], "alpha": info["has_alpha"],
            "version": core.parse_version_token(p.name),
            "status": m.group(1) if m else "unlabelled",
            "sha256": core.sha256_file(p),
            "dhash": core.dhash(p),
            "has_provenance": p.with_suffix(p.suffix + ".provenance.json").exists(),
            "tags": [t.strip() for t in (tag or "").split(",") if t.strip()],
            "modified": p.stat().st_mtime,
        })
    records.sort(key=lambda r: r["path"])
    return {
        "index_version": 1,
        "generated_at": core.now_iso(),
        "root": root.name,
        "asset_count": len(records),
        "by_format": _count(records, "format"),
        "by_status": _count(records, "status"),
        "by_extension": _count(records, "ext"),
        "total_bytes": sum(r.get("bytes", 0) for r in records),
        "duplicate_groups": len(_duplicate_groups(records)),
        "assets": records,
    }


def _count(records, key):
    out = {}
    for r in records:
        value = str(r.get(key) or "unknown")
        out[value] = out.get(value, 0) + 1
    return out


def _duplicate_groups(records, hamming_threshold=6):
    groups = []
    by_hash = {}
    for r in records:
        if "sha256" not in r:
            continue
        by_hash.setdefault(r["sha256"], []).append(r)
    exact = [g for g in by_hash.values() if len(g) > 1]
    for group in exact:
        groups.append({"kind": "exact", "members": [g["path"] for g in group],
                       "bytes_saved": group[0].get("bytes", 0) * (len(group) - 1)})
    seen = {r["path"] for g in exact for r in g}
    remaining = [r for r in records if r.get("dhash") and r["path"] not in seen]
    used = set()
    for i, r in enumerate(remaining):
        if r["path"] in used:
            continue
        cluster = [r]
        for other in remaining[i + 1:]:
            if other["path"] in used:
                continue
            if core.hamming_hex(r["dhash"], other["dhash"]) <= hamming_threshold:
                cluster.append(other)
                used.add(other["path"])
        if len(cluster) > 1:
            used.add(r["path"])
            groups.append({"kind": "near", "members": [c["path"] for c in cluster],
                           "max_hamming": max(core.hamming_hex(cluster[0]["dhash"], c["dhash"]) for c in cluster[1:]),
                           "bytes_saved": sum(c.get("bytes", 0) for c in cluster[1:])})
    return groups


def op_library(ctx, a):
    action = (getattr(a, "action", None) or "index").lower()
    handlers = {
        "index": library_index,
        "dedupe": library_dedupe,
        "name": library_name,
        "versions": library_versions,
        "rights": library_rights,
        "gallery": library_gallery,
        "handoff": library_handoff,
        "usage": library_usage,
        "preserve": library_preserve,
        "master": library_master,
        "verify-handoff": library_verify_handoff,
    }
    if action not in handlers:
        raise core.usage_error(f"Unknown --action '{action}'",
                               "Choose from: " + ", ".join(sorted(handlers)))
    return handlers[action](ctx, a)


def library_index(ctx, a):
    root = _library_dir(ctx, a)
    files = _scan(root, ctx.config.limit("max_batch_items", 500))
    if not files:
        raise core.input_error("The library folder contains no images.")
    index = build_index(ctx, root, files, getattr(a, "tags", None))
    name = core.slugify(f"{getattr(a, 'project', None) or root.name}-library-index")
    out_json = core.emit_json(ctx, name, index)
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(["path", "width", "height", "aspect", "format", "mode", "bytes", "dpi",
                     "version", "status", "has_provenance", "sha256"])
    for r in index["assets"]:
        writer.writerow([r.get("path"), r.get("width"), r.get("height"), r.get("aspect"),
                         r.get("format"), r.get("mode"), r.get("bytes"), r.get("dpi"),
                         r.get("version"), r.get("status"), r.get("has_provenance"), r.get("sha256", "")[:16]])
    out_csv = core.emit_text(ctx, core.slugify(f"{getattr(a, 'project', None) or root.name}-library-index"),
                             "csv", csv_buf.getvalue())
    ctx.audit("library.indexed", assets=index["asset_count"])
    return {"data": {k: index[k] for k in ("asset_count", "by_format", "by_status", "total_bytes",
                                            "duplicate_groups")},
            "outputs": [out_json, out_csv]}


def library_dedupe(ctx, a):
    root = _library_dir(ctx, a)
    files = _scan(root, ctx.config.limit("max_batch_items", 500))
    index = build_index(ctx, root, files)
    groups = _duplicate_groups(index["assets"], int(getattr(a, "threshold", 6) or 6))
    apply = bool(getattr(a, "apply", False))
    quarantine = root / "_quarantine"
    plan = []
    for group in groups:
        members = sorted(group["members"])
        keep = members[0]
        drop = members[1:]
        for d in drop:
            plan.append({"group_kind": group["kind"], "keep": keep, "quarantine": d})
    if apply:
        ctx.require_approval("library.dedupe.apply",
                             "Moving duplicates into the quarantine folder changes the delivered library.",
                             {"moves": len(plan)})
        moved = []
        quarantine.mkdir(exist_ok=True)
        for item in plan:
            src = root / item["quarantine"]
            if not src.exists():
                continue
            target = quarantine / src.name
            counter = 1
            while target.exists():
                target = quarantine / f"{src.stem}-dup{counter}{src.suffix}"
                counter += 1
            shutil.move(str(src), str(target))
            moved.append({"from": item["quarantine"], "to": core.rel_posix(target, root)})
        plan = moved
    payload = {"operation": "library", "action": "dedupe", "apply": apply,
               "groups": groups, "planned_moves": plan,
               "bytes_recoverable": sum(g.get("bytes_saved", 0) for g in groups),
               "quarantine_folder": core.rel_posix(quarantine, root) if apply else "_quarantine (would be created)",
               "note": "nothing is deleted; duplicates move to the quarantine folder for human review"}
    out_json = core.emit_json(ctx, core.slugify(f"{getattr(a, 'project', None) or root.name}-dedupe-plan"),
                             payload)
    ctx.audit("library.deduped", apply=apply, groups=len(groups))
    return {"data": payload, "outputs": [out_json]}


def library_name(ctx, a):
    root = _library_dir(ctx, a)
    files = _scan(root, ctx.config.limit("max_batch_items", 500))
    project = getattr(a, "project", None) or root.name
    apply = bool(getattr(a, "apply", False))
    naming = ctx.config.naming
    plan, skipped = [], []
    for p in files:
        version = core.parse_version_token(p.name) or 1
        status_match = re.search(r"-(draft|review|approved|final|superseded)\b", p.stem)
        content = re.sub(r"[-_]{2,}", "-", p.stem)
        content = re.sub(r"-?v\d{1,3}\b", "", content)
        content = re.sub(r"-(draft|review|approved|final|superseded)\b", "", content)
        content = re.sub(r"-\d{2,5}x\d{2,5}\b", "", content).strip("-_ ")
        try:
            info = core.image_info(p)
        except Exception:
            skipped.append(p.name)
            continue
        new_name = core.build_filename({
            "project": project, "content": content or p.stem, "dimensions": (info["width"], info["height"]),
            "version": version, "status": status_match.group(1) if status_match else "review"},
            p.suffix, naming)
        if new_name != p.name:
            plan.append({"from": core.rel_posix(p, root), "to": new_name})
    if apply:
        ctx.require_approval("library.rename.apply",
                             "Renaming files breaks any external links that point at the old names.",
                             {"renames": len(plan)})
        done = []
        for item in plan:
            src = root / item["from"]
            target = root / item["to"]
            if target.exists():
                skipped.append(f"{item['to']} (exists)")
                continue
            src.rename(target)
            done.append(item)
        plan = done
    payload = {"operation": "library", "action": "name", "apply": apply,
               "pattern": naming.get("pattern"), "renames": plan, "skipped": skipped,
               "note": "dry-run by default; run again with --apply --approve to rename"}
    out_json = core.emit_json(ctx, core.slugify(f"{project}-naming-plan"), payload)
    ctx.audit("library.renamed", apply=apply, count=len(plan))
    return {"data": payload, "outputs": [out_json]}


def library_versions(ctx, a):
    root = _library_dir(ctx, a)
    files = _scan(root, ctx.config.limit("max_batch_items", 500))
    groups = {}
    for p in files:
        key = re.sub(r"[-_]?v\d{1,3}\b", "", p.stem).strip("-_ ")
        key = re.sub(r"-(draft|review|approved|final|superseded)\b", "", key)
        groups.setdefault(key, []).append(p)
    report = []
    for key, members in sorted(groups.items()):
        entries = []
        for p in sorted(members, key=lambda x: (core.parse_version_token(x.name) or 0, x.name)):
            try:
                info = core.image_info(p)
            except Exception:
                continue
            entries.append({"file": core.rel_posix(p, root), "version": core.parse_version_token(p.name),
                            "status": (re.search(r"-(draft|review|approved|final|superseded)\b", p.stem).group(1)
                                       if re.search(r"-(draft|review|approved|final|superseded)\b", p.stem) else "unlabelled"),
                            "size": [info["width"], info["height"]], "bytes": info["bytes"],
                            "sha256": core.sha256_file(p)[:16]})
        if len(entries) < 2:
            continue
        masters = [e for e in entries if e["status"] == "final"] or entries[-1:]
        report.append({"key": key, "count": len(entries), "latest": entries[-1]["file"],
                       "masters": [m["file"] for m in masters],
                       "superseded": [e["file"] for e in entries if e not in masters],
                       "entries": entries})
    payload = {"operation": "library", "action": "versions", "groups": report,
               "group_count": len(report),
               "note": "masters are the 'final' entries when present, otherwise the highest version"}
    out_json = core.emit_json(ctx, core.slugify(f"{getattr(a, 'project', None) or root.name}-versions"), payload)
    return {"data": payload, "outputs": [out_json]}


def library_rights(ctx, a):
    root = _library_dir(ctx, a)
    registry = None
    for candidate in ("rights.json", "_rights/rights.json", "rights/rights.json"):
        p = root / candidate
        if p.exists():
            registry = core.read_json(p, required=True)
            break
    if registry is None:
        assets = _scan(root, ctx.config.limit("max_batch_items", 500))
        template = {
            "schema": "rights-registry/1",
            "updated": core.now_iso(),
            "note": ("Fill licence, territory, channels, expires, source and consent for each asset. "
                     "Re-run 'library --action rights' to evaluate status. Assets are listed so nothing "
                     "is silently left unmanaged."),
            "assets": [{"asset": core.rel_posix(rec, root), "licence": "", "territory": "",
                        "channels": [], "expires": None, "source": "", "consent": "", "notes": ""}
                       for rec in assets[:400]],
        }
        template_path = ctx.out_path(core.slugify(f"{getattr(a, 'project', None) or root.name}-rights-template"), ".json")
        if not ctx.dry_run:
            core.write_json(template_path, template)
        payload = {"operation": "library", "action": "rights", "registry_found": False,
                   "template": core.rel_posix(template_path, ctx.out_root),
                   "assets_to_manage": len(template["assets"]),
                   "instructions": [f"Copy or edit {core.rel_posix(template_path, ctx.out_root)} into the library as rights.json",
                                    "Fill licence, territory, channels, expires, source and consent",
                                    "Re-run: library --action rights --library DIR"],
                   "schema": {"asset": "relative path", "licence": "name or SPDX id",
                              "territory": "world | country list", "channels": ["web", "print", "social"],
                              "expires": "YYYY-MM-DD or null", "source": "origin of the file",
                              "consent": "release or model reference", "notes": "free text"}}
        ctx.audit("library.rights_template", assets=len(template["assets"]))
        return {"data": payload, "outputs": [template_path]}
    records = registry.get("assets") or registry if isinstance(registry, list) else registry.get("assets", [])
    if isinstance(records, dict):
        records = list(records.values())
    today = core.now_iso()[:10]
    report = []
    for rec in records:
        expires = rec.get("expires")
        days = None
        if expires:
            try:
                from datetime import date
                y, m, d = (int(x) for x in str(expires).split("-")[:3])
                days = (date(y, m, d) - date.today()).days
            except Exception:
                days = None
        status = ("expired" if days is not None and days < 0 else
                  "expiring" if days is not None and days <= int(getattr(a, "window", 0) or 30) else
                  "active" if days is not None else "unmanaged")
        report.append({"asset": rec.get("asset"), "licence": rec.get("licence"),
                       "territory": rec.get("territory"), "channels": rec.get("channels"),
                       "expires": expires, "days_remaining": days, "status": status})
    counts = {}
    for r in report:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    payload = {"operation": "library", "action": "rights", "checked_at": today,
               "counts": counts, "records": report,
               "action_list": [r["asset"] for r in report if r["status"] in ("expired", "expiring")],
               "note": "expiry is calculated locally; verify terms with the licence holder"}
    out_json = core.emit_json(ctx, core.slugify(f"{getattr(a, 'project', None) or root.name}-rights"), payload)
    ctx.audit("library.rights_checked", counts=counts)
    return {"data": payload, "outputs": [out_json]}


def library_gallery(ctx, a):
    root = _library_dir(ctx, a)
    files = _scan(root, ctx.config.limit("max_batch_items", 500))
    index = build_index(ctx, root, files, getattr(a, "tags", None))
    title = getattr(a, "title", None) or f"{getattr(a, 'project', None) or root.name} review gallery"
    cards = []
    for i, record in enumerate(index["assets"], start=1):
        if "error" in record:
            continue
        cards.append(f"""<figure class="card">
  <img src="{record['path']}" alt="{record['name']}" loading="lazy">
  <figcaption><b>#{i:03d}</b> {record['name']}<br>
  <span class="meta">{record['width']}x{record['height']} · {record['format']} · {record['status']} ·
  {round(record['bytes']/1024)} KB{' · alpha' if record['alpha'] else ''}</span>
  <label><input type="checkbox"> approved</label></figcaption></figure>""")
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>
 body{{margin:0;background:#0f1115;color:#e8eef4;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}}
 header{{padding:24px 28px;border-bottom:1px solid #202834}}
 h1{{margin:0;font-size:20px}} .sub{{color:#8ea0b4;margin-top:4px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px;padding:24px 28px 60px}}
 .card{{margin:0;background:#161b22;border:1px solid #232b36;border-radius:10px;overflow:hidden}}
 .card img{{width:100%;display:block;aspect-ratio:4/3;object-fit:cover;background:#0b0e13}}
 figcaption{{padding:10px 12px;font-size:12.5px}} .meta{{color:#8ea0b4}}
 label{{display:block;margin-top:8px;color:#7ee0c1}}
</style></head><body>
<header><h1>{title}</h1><div class="sub">{index['asset_count']} assets · generated {index['generated_at']} ·
approval checkboxes are local to this page and are not saved</div></header>
<div class="grid">{''.join(cards)}</div></body></html>
"""
    out_html = core.emit_text(ctx, core.slugify(f"{getattr(a, 'project', None) or root.name}-gallery"),
                              "html", html)
    ctx.audit("library.gallery_built", assets=index["asset_count"])
    return {"data": {"operation": "library", "action": "gallery", "assets": index["asset_count"],
                     "gallery": core.rel_posix(out_html, ctx.out_root),
                     "note": "relative image paths assume the gallery sits beside the library folder"},
            "outputs": [out_html]}


def library_handoff(ctx, a):
    root = _library_dir(ctx, a)
    files = _scan(root, ctx.config.limit("max_batch_items", 500))
    publish = bool(getattr(a, "publish", False))
    if publish:
        ctx.require_approval("handoff.publish",
                             "Publishing a handoff bundle transmits assets outside this workspace.",
                             {"assets": len(files)})
    if getattr(a, "apply", False) and getattr(a, "target", None):
        ctx.require_approval(
            "handoff.apply",
            "Copying a handoff bundle writes and can overwrite files in the target folder.",
            {"target": str(getattr(a, "target"))})
    target = Path(getattr(a, "target", None) or (root.parent / f"{root.name}-handoff")).expanduser().resolve()
    if target == root:
        raise core.usage_error("Handoff target cannot be the library folder itself")
    index = build_index(ctx, root, files)
    masters, derivatives, previews = [], [], []
    for record in index["assets"]:
        if "error" in record:
            continue
        ext = record["ext"]
        largest = record["width"] * record["height"] >= 1600 * 1600 or ext in ("tif", "tiff", "png") and record["bytes"] > 2_000_000
        (masters if largest else derivatives).append(record)
    plan = {"masters": [m["path"] for m in masters],
            "derivatives": [d["path"] for d in derivatives],
            "notes": [], "target": str(target)}
    if ctx.dry_run:
        payload = {"operation": "library", "action": "handoff", "dry_run": True, "plan": plan,
                   "counts": {"masters": len(masters), "derivatives": len(derivatives)}}
        out_json = core.emit_json(ctx, core.slugify(f"{getattr(a, 'project', None) or root.name}-handoff-plan"),
                                 payload)
        return {"data": payload, "outputs": [out_json]}
    for sub in ("masters", "derivatives", "previews", "records"):
        (target / sub).mkdir(parents=True, exist_ok=True)
    copied, unchanged = 0, 0
    for record, sub in [(m, "masters") for m in masters] + [(d, "derivatives") for d in derivatives]:
        src = root / record["path"]
        dst = target / sub / src.name
        if dst.exists() and dst.stat().st_size == src.stat().st_size and dst.resolve() != src.resolve():
            unchanged += 1
            continue
        if dst.exists() and dst.resolve() != src.resolve():
            shutil.copy2(src, dst)
            copied += 1
            continue
        if dst.resolve() == src.resolve():
            unchanged += 1
            continue
        shutil.copy2(src, dst)
        copied += 1
        if record["width"] > 800:
            with __import__("PIL.Image", fromlist=["Image"]).open(src) as im:
                prev = im.convert("RGB")
                prev.thumbnail((800, 800))
                prev.save(target / "previews" / (src.stem + "-preview.jpg"), quality=80)
    (target / "records").mkdir(exist_ok=True)
    if not ctx.dry_run:
        core.write_json(target / "records" / "asset-index.json", index)
    usage_md = usage_guide_text(index, getattr(a, "project", None) or root.name)
    (target / "records" / "usage-guide.md").write_text(usage_md, encoding="utf-8")
    readme = [f"# Handoff bundle: {getattr(a, 'project', None) or root.name}", "",
              f"Assets: {len(masters)} masters, {len(derivatives)} derivatives.",
              "", "## Folders", "- `masters/` highest-fidelity files, keep untouched",
              "- `derivatives/` delivery-ready exports", "- `previews/` quick-look jpgs",
              "- `records/` index, usage guide", "",
              "## Before publishing", "- confirm rights for every asset",
              "- confirm disclosures are attached to synthetic imagery",
              "- run `library --action verify-handoff --target DIR` to confirm the bundle opens"]
    (target / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    payload = {"operation": "library", "action": "handoff", "target": target.name,
               "counts": {"masters": len(masters), "derivatives": len(derivatives),
                          "copied": copied, "already_present": unchanged},
               "files": len(masters) + len(derivatives),
               "records": ["asset-index.json", "usage-guide.md"],
               "note": "the bundle is a folder, not an archive; no nested archives are produced"}
    out_json = core.emit_json(ctx, core.slugify(f"{getattr(a, 'project', None) or root.name}-handoff-report"),
                              payload)
    ctx.audit("library.handoff_built", masters=len(masters), derivatives=len(derivatives))
    return {"data": payload, "outputs": [out_json]}


def usage_guide_text(index, project):
    lines = [f"# Asset usage guide — {project}", "",
             f"Generated {index['generated_at']} from {index['asset_count']} indexed assets.", "",
             "## Approved use", "- use the master files for anything that will be re-edited",
             "- use derivatives for delivery; they are already sized for their channel",
             "- keep clear space around any mark equal to one third of its height", "",
             "## Prohibited modifications",
             "- do not recolour, stretch or crop into the subject's silhouette",
             "- do not remove or obscure provenance or disclosure text",
             "- do not upscale derivatives beyond 150 percent", "",
             "## Attribution and disclosure"]
    synthetic = [a["path"] for a in index["assets"] if a.get("has_provenance")]
    if synthetic:
        lines.append("- provenance records exist for: " + ", ".join(synthetic[:8]))
    else:
        lines.append("- no provenance records found; confirm the source of each asset before external use")
    lines += ["", "## Minimum sizes", "- screen: 800px on the long edge",
              "- print: 300 dpi at final trim size", ""]
    return "\n".join(lines) + "\n"


def library_usage(ctx, a):
    root = _library_dir(ctx, a)
    files = _scan(root, ctx.config.limit("max_batch_items", 500))
    index = build_index(ctx, root, files)
    text = usage_guide_text(index, getattr(a, "project", None) or root.name)
    out_md = core.emit_text(ctx, core.slugify(f"{getattr(a, 'project', None) or root.name}-usage-guide"),
                            "md", text)
    out_json = core.emit_json(ctx, core.slugify(f"{getattr(a, 'project', None) or root.name}-usage-guide"),
                             {"operation": "library", "action": "usage", "assets": index["asset_count"],
                              "guide": core.rel_posix(out_md, ctx.out_root)})
    return {"data": {"guide": core.rel_posix(out_md, ctx.out_root)}, "outputs": [out_md, out_json]}


def library_preserve(ctx, a):
    root = _library_dir(ctx, a)
    files = _scan(root, ctx.config.limit("max_batch_items", 500))
    index = build_index(ctx, root, files)
    durable = {"png", "tif", "tiff"}
    at_risk = [r for r in index["assets"]
               if r.get("ext") not in durable and r.get("bytes", 0) < 2_000_000]
    small = [r for r in index["assets"] if r.get("bytes", 0) < 200_000]
    payload = {
        "operation": "library", "action": "preserve",
        "recommendations": [
            "keep one lossless master per asset (PNG or TIFF) plus its provenance record",
            "store checksums; use 'library --action index' output as the checksum baseline",
            "re-verify checksums every 12 months and after any storage migration",
            "keep two copies on different media, one offline",
            "record format migrations in the provenance lineage field",
        ],
        "at_risk_count": len(at_risk),
        "at_risk": [r["path"] for r in at_risk][:40],
        "small_files": [r["path"] for r in small][:40],
        "format_mix": index["by_extension"],
        "note": "lossy or unusually small files are the ones most likely to be unusable after a migration",
    }
    out_json = core.emit_json(ctx, core.slugify(f"{getattr(a, 'project', None) or root.name}-preservation-plan"),
                              payload)
    md = ["# Preservation plan", "", f"Assets: {index['asset_count']}", "", "## Recommendations"]
    md += [f"- {r}" for r in payload["recommendations"]]
    md += ["", f"Assets not stored losslessly: {len(at_risk)}"]
    md += [f"- {p}" for p in payload["at_risk"][:20]]
    out_md = core.emit_text(ctx, core.slugify(f"{getattr(a, 'project', None) or root.name}-preservation-plan"),
                             "md", "\n".join(md) + "\n")
    return {"data": payload, "outputs": [out_json, out_md]}


def library_master(ctx, a):
    root = _library_dir(ctx, a)
    files = _scan(root, ctx.config.limit("max_batch_items", 500))
    index = build_index(ctx, root, files)
    masters, derivatives = [], []
    for r in index["assets"]:
        if "error" in r:
            continue
        lossless = r["ext"] in ("png", "tif", "tiff")
        big = r["width"] * r["height"] >= 2_000_000
        (masters if lossless and big else derivatives).append(r)
    payload = {"operation": "library", "action": "master",
               "masters": [m["path"] for m in masters],
               "derivatives": [d["path"] for d in derivatives],
               "counts": {"masters": len(masters), "derivatives": len(derivatives)},
               "rule": "master = lossless format (png/tif) at 2 MP or larger; everything else is a derivative",
               "note": "review borderline files manually before treating this split as authoritative"}
    out_json = core.emit_json(ctx, core.slugify(f"{getattr(a, 'project', None) or root.name}-master-split"),
                              payload)
    return {"data": payload, "outputs": [out_json]}


def library_verify_handoff(ctx, a):
    target = Path(getattr(a, "target", None) or "").expanduser()
    if not target or not target.is_dir():
        raise core.usage_error("--action verify-handoff needs --target DIR (the handoff bundle folder).")
    problems, checked = [], 0
    for p in sorted(target.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() in core.IMAGE_EXTS:
            checked += 1
            try:
                info = core.image_info(p)
                if info["width"] < 8 or info["height"] < 8:
                    problems.append(f"{core.rel_posix(p, target)}: degenerate size")
            except Exception as exc:
                problems.append(f"{core.rel_posix(p, target)}: {exc}")
    index_path = target / "records" / "asset-index.json"
    index_ok = index_path.exists()
    if index_ok:
        index = core.read_json(index_path, required=True)
        expected = {a["path"].split("/")[-1] for a in index.get("assets", []) if "path" in a}
        present = {p.name for p in target.rglob("*") if p.is_file() and p.suffix.lower() in core.IMAGE_EXTS}
        missing = sorted(expected - present)
        if missing:
            problems.append(f"missing from bundle: {', '.join(missing[:10])}")
    payload = {"operation": "library", "action": "verify-handoff", "target": target.name,
               "images_checked": checked, "index_present": index_ok,
               "problems": problems, "ok": not problems and index_ok,
               "note": "this checks that files open and that the bundle matches its index; "
                       "it cannot test the recipient's design application"}
    out_json = core.emit_json(ctx, core.slugify(f"{target.name}-handoff-verify"), payload)
    ctx.audit("library.handoff_verified", ok=payload["ok"])
    return {"data": payload, "outputs": [out_json]}
