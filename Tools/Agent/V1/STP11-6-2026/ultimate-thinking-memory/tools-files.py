"""File structure and checkpoint handlers.

Families: S (structural checks: JSON, Markdown links and headings, Python syntax, ZIP integrity,
skill-folder naming, comparisons, sections, environment, cross-file joins, path existence) and
K (checkpoints: pack, verify, latest, handoff, restore). Restore into a non-empty folder needs
explicit approval and backs up every overwritten file first.
Loaded by tools-engine.py; do not run directly.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
HANDLERS = {}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FILE_RE = re.compile(r"^(tools|knowledge|asset)-[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z0-9]+$")


def _load_core():
    name = "tools_core_shared"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TOOL_DIR / "tools-core.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()


def handler(key):
    def register(fn):
        HANDLERS[key] = fn
        return fn
    return register


def _safe_rel(rel):
    """Return a normalized forward-slash relative path or None if it is unsafe."""
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel):
        return None
    parts = Path(rel.replace("\\", "/")).parts
    if any(p in ("..", "") for p in parts):
        return None
    return "/".join(parts)


def _strip_code(text):
    return re.sub(r"```.*?```", "", text, flags=re.S)


def md_issues(text, base_dir):
    issues = []
    body = _strip_code(text)
    for m in re.finditer(r"\[([^\]]*)\]\(([^)\s]+)\)", body):
        label, target = m.group(1), m.group(2)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path_part = target.split("#")[0]
        if not path_part:
            continue
        if _safe_rel(path_part) is None:
            issues.append(("unsafe-link", target))
        elif not (base_dir / path_part).exists():
            issues.append(("broken-link", target))
        if not label.strip():
            issues.append(("empty-link-text", target))
    if re.search(r"!\[\s*\]\(", body):
        issues.append(("image-without-alt", "one or more images lack alt text"))
    levels = [len(h) for h in re.findall(r"^(#{1,6})\s", body, re.M)]
    if not levels:
        issues.append(("no-heading", "document has no headings"))
    else:
        if levels[0] != 1:
            issues.append(("no-top-level-heading", "first heading is not level 1"))
        for a, b in zip(levels, levels[1:]):
            if b > a + 1:
                issues.append(("heading-jump", f"h{a} to h{b}"))
    return issues


def _walk(root, ctx):
    skip = {ctx.state_dir.name, ctx.output_dir.name}
    return core.walk_files(root, ctx.policy, skip_dirs=skip)


# ====================================================================== S
@handler("S:check")
def s_check(ctx):
    target = ctx.require_input()
    base = target if target.is_dir() else target.parent
    files = [target] if target.is_file() else _walk(target, ctx)
    skill_mode = ctx.opt_bool("skill_tool", False)
    items = []

    def add(path, check, status, detail=""):
        items.append({"file": path, "check": check, "status": status, "detail": detail})

    for f in files:
        ctx.tick()
        rel = str(f.relative_to(base)).replace("\\", "/")
        if f.stat().st_size > ctx.policy["limits"]["max_scan_file_bytes"]:
            add(rel, "size", "warn", "file is larger than the scan limit; not inspected")
            continue
        suffix = f.suffix.lower()
        if suffix == ".json":
            try:
                json.loads(f.read_text(encoding="utf-8"))
                add(rel, "json-parse", "pass")
            except (ValueError, UnicodeDecodeError) as exc:
                add(rel, "json-parse", "fail", str(exc)[:160])
        elif suffix == ".md":
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                add(rel, "utf8", "fail", str(exc)[:160])
                continue
            problems = md_issues(text, f.parent)
            if not problems:
                add(rel, "markdown", "pass")
            for kind, detail in problems:
                add(rel, kind, "warn" if kind in ("heading-jump", "image-without-alt", "empty-link-text") else "fail", detail)
        elif suffix == ".py":
            try:
                compile(f.read_text(encoding="utf-8"), str(f), "exec")
                add(rel, "python-syntax", "pass")
            except (SyntaxError, UnicodeDecodeError) as exc:
                add(rel, "python-syntax", "fail", str(exc)[:160])
        elif suffix == ".zip":
            try:
                with zipfile.ZipFile(f) as zf:
                    bad = zf.testzip()
                    nested = [n for n in zf.namelist() if n.lower().endswith((".zip", ".tar", ".gz", ".7z", ".rar"))]
                add(rel, "zip-integrity", "fail" if bad else "pass", f"corrupt member {bad}" if bad else "")
                if nested:
                    add(rel, "no-nested-archive", "fail", ", ".join(nested[:5]))
            except zipfile.BadZipFile as exc:
                add(rel, "zip-integrity", "fail", str(exc))
        if skill_mode:
            if FILE_RE.match(f.name) is None:
                add(rel, "skill-file-name", "fail", "must start with tools-, knowledge-, or asset- and use lowercase a-z, 0-9, dots, hyphens")
            if f.name == "SKILL.md":
                add(rel, "no-skill-md", "fail", "a Skill Tool folder must not contain SKILL.md")
    if skill_mode:
        if target.is_dir():
            if NAME_RE.match(target.name) is None:
                add(target.name, "skill-folder-name", "fail", "must match ^[a-z0-9]+(?:-[a-z0-9]+)*$")
            subdirs = [p for p in target.iterdir() if p.is_dir()]
            if subdirs:
                add(target.name, "no-subfolders", "fail", ", ".join(p.name for p in subdirs))
            knowledge = [p.name for p in target.iterdir() if p.is_file() and p.name.startswith("knowledge-")]
            add(target.name, "exactly-one-knowledge-file", "pass" if len(knowledge) == 1 else "fail", ", ".join(knowledge))
            primary = [p.name for p in target.iterdir() if p.is_file() and p.name.startswith("tools-")]
            add(target.name, "tools-entry-point-present", "pass" if primary else "fail", str(len(primary)) + " tools- files")
    counts = defaultdict(int)
    for it in items:
        counts[it["status"]] += 1
    overall = "pass" if counts.get("fail", 0) == 0 else "fail"
    return {"summary": {"files": len({i["file"] for i in items}), "checks": len(items), "pass": counts.get("pass", 0),
                        "warn": counts.get("warn", 0), "fail": counts.get("fail", 0), "verdict": overall},
            "items": items}


@handler("S:compare")
def s_compare(ctx):
    a = ctx.require_input()
    other = ctx.opt("other")
    if not other:
        raise core.InputError("option other=<second file> is required")
    left = core.read_text_file(a, ctx.policy).splitlines()
    right = core.read_text_file(Path(other), ctx.policy).splitlines()
    for i in range(max(len(left), len(right))):
        ctx.tick()
        l_line = left[i] if i < len(left) else "<end of file>"
        r_line = right[i] if i < len(right) else "<end of file>"
        if l_line != r_line:
            return {"summary": {"identical": False, "first_mismatch_line": i + 1, "left_lines": len(left),
                                "right_lines": len(right)},
                    "items": [{"line": i + 1, "left": core.excerpt(l_line, 200), "right": core.excerpt(r_line, 200)}]}
    return {"summary": {"identical": True, "left_lines": len(left), "right_lines": len(right)}, "items": []}


@handler("S:sections")
def s_sections(ctx):
    path = ctx.require_input()
    required = ctx.opt("required")
    if not required:
        raise core.InputError("option required=<comma-separated heading words> is required")
    text = core.read_text_file(path, ctx.policy)
    headings = [h.strip().lower() for h in re.findall(r"^#{1,6}\s+(.*)$", _strip_code(text), re.M)]
    items = []
    for want in [w.strip() for w in required.split(",") if w.strip()]:
        found = any(want.lower() in h for h in headings)
        items.append({"section": want, "status": "present" if found else "missing"})
    missing = sum(1 for i in items if i["status"] == "missing")
    return {"summary": {"required": len(items), "missing": missing, "verdict": "pass" if not missing else "fail",
                        "headings_found": len(headings)}, "items": items}


@handler("S:env")
def s_env(ctx):
    p = ctx.policy
    items = []

    def add(check, ok, detail=""):
        items.append({"check": check, "status": "pass" if ok else "fail", "detail": detail})

    minimum = tuple(int(x) for x in p["defaults"]["python_min"].split("."))
    add("python-version", sys.version_info[:2] >= minimum, f"{sys.version.split()[0]} (need >= {p['defaults']['python_min']})")
    modules = [m.strip() for m in (ctx.opt("modules") or "").split(",") if m.strip()]
    for mod in modules:
        ok = importlib.util.find_spec(mod) is not None
        add(f"module {mod}", ok, "importable" if ok else "not importable")
    for label, folder in (("state-dir-writable", ctx.state_dir), ("output-dir-writable", ctx.output_dir)):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(folder), prefix=".probe-")
            os.close(fd)
            os.remove(tmp)
            add(label, True, str(folder))
        except OSError as exc:
            add(label, False, str(exc))
    free_mb = shutil.disk_usage(str(ctx.state_dir if ctx.state_dir.exists() else Path.cwd())).free / 1048576
    add("free-disk", free_mb >= p["defaults"]["min_free_disk_mb"], f"{free_mb:.0f} MB free")
    if ctx.input_path is not None and ctx.input_path.suffix.lower() == ".json":
        data = ctx.read_json()
        paths = data.get("paths", []) if isinstance(data, dict) else []
        for rel in paths:
            safe = _safe_rel(rel)
            if safe is None:
                add(f"path {rel}", False, "unsafe path rejected")
            else:
                ok = (Path.cwd() / safe).exists()
                add(f"path {rel}", ok, "exists" if ok else "missing")
    failed = sum(1 for i in items if i["status"] == "fail")
    return {"summary": {"checks": len(items), "failed": failed, "verdict": "ready" if not failed else "not ready"},
            "items": items}


@handler("S:join")
def s_join(ctx):
    root = ctx.input_path if ctx.input_path.is_dir() else ctx.input_path.parent
    files = [f for f in _walk(root, ctx) if f.suffix.lower() == ".md"]
    heading_owner = defaultdict(set)
    items = []
    for f in files:
        ctx.tick()
        rel = str(f.relative_to(root)).replace("\\", "/")
        text = f.read_text(encoding="utf-8", errors="replace")
        for h in re.findall(r"^#{1,6}\s+(.*)$", _strip_code(text), re.M):
            heading_owner[h.strip().lower()].add(rel)
        for kind, detail in md_issues(text, f.parent):
            if kind in ("broken-link", "unsafe-link"):
                items.append({"file": rel, "check": kind, "detail": detail})
    for heading, owners in heading_owner.items():
        if len(owners) > 1:
            items.append({"file": ", ".join(sorted(owners)), "check": "duplicate-heading", "detail": heading})
    return {"summary": {"markdown_files": len(files), "join_issues": len(items)}, "items": items}


@handler("S:exists")
def s_exists(ctx):
    data = ctx.read_json()
    paths = data.get("paths") if isinstance(data, dict) else data
    if not isinstance(paths, list) or not paths:
        raise core.InputError("exists input must be a non-empty list of paths or an object with 'paths'")
    base = ctx.base_dir()
    items = []
    for raw in paths:
        ctx.tick()
        safe = _safe_rel(raw)
        if safe is None:
            items.append({"path": str(raw), "status": "rejected", "detail": "unsafe path (absolute or contains ..)"})
            continue
        target = base / safe
        if target.is_file():
            items.append({"path": safe, "status": "present", "detail": f"{target.stat().st_size} bytes"})
        elif target.is_dir():
            items.append({"path": safe, "status": "present (folder)", "detail": ""})
        else:
            items.append({"path": safe, "status": "missing", "detail": ""})
    counts = defaultdict(int)
    for it in items:
        counts[it["status"].split(" ")[0]] += 1
    verdict = "complete" if counts.get("missing", 0) == 0 and counts.get("rejected", 0) == 0 else "incomplete"
    return {"summary": {"paths": len(items), "present": counts.get("present", 0), "missing": counts.get("missing", 0),
                        "rejected": counts.get("rejected", 0), "verdict": verdict, "base": str(base)},
            "items": items}


# ====================================================================== K
def _checkpoint_index(ctx):
    return ctx.state_path("checkpoints", "index.jsonl")


def _collect(ctx, base):
    """Return (relative_paths, scope) for a checkpoint from a folder input or a JSON file list."""
    if ctx.input_path is not None and ctx.input_path.is_dir():
        files = [f for f in _walk(ctx.input_path, ctx) if not f.name.endswith((".zip", ".manifest.json"))]
        rels = [str(f.relative_to(base)).replace("\\", "/") for f in files if f.resolve().is_relative_to(base)]
        return sorted(rels), "directory"
    spec = ctx.read_json() if ctx.input_path is not None else {}
    files_list = spec.get("files") if isinstance(spec, dict) else None
    if files_list is None and not isinstance(spec, dict):
        raise core.InputError("input must be a folder or a JSON object with a 'files' list")
    if not isinstance(files_list, list) or not files_list:
        raise core.InputError("checkpoint needs a folder input or a JSON 'files' list")
    if len(files_list) > ctx.policy["limits"]["max_checkpoint_files"]:
        raise core.InputError("too many files for one checkpoint")
    rels = []
    for raw in files_list:
        safe = _safe_rel(raw)
        if safe is None:
            raise core.InputError(f"unsafe path in checkpoint list: {raw!r}")
        if not (base / safe).is_file():
            raise core.InputError(f"file to checkpoint not found: {safe}")
        rels.append(safe)
    return sorted(set(rels)), "list"


@handler("K:pack")
def k_pack(ctx):
    base = ctx.base_dir()
    spec = ctx.read_json() if ctx.input_path is not None and ctx.input_path.is_file() and ctx.input_path.suffix == ".json" else {}
    name = ctx.opt("name") or (spec.get("name") if isinstance(spec, dict) else None) or ctx.slug
    if not NAME_RE.match(str(name)):
        raise core.InputError("checkpoint name must be lowercase letters, digits, and hyphens")
    rels, scope = _collect(ctx, base)
    limits = ctx.policy["limits"]
    buf = io.BytesIO()
    entries, total = [], 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in rels:
            ctx.tick()
            data = (base / rel).read_bytes()
            total += len(data)
            if total > limits["max_checkpoint_bytes"]:
                raise core.InputError("checkpoint exceeds max_checkpoint_bytes")
            zf.writestr(rel, data)
            entries.append({"path": rel, "sha256": core.sha256_bytes(data), "size": len(data)})
    archive = buf.getvalue()
    archive_path = core.atomic_write_bytes(ctx.output_dir / f"{name}.zip", archive, ctx.policy)
    manifest = {"name": name, "created": core.now_iso(), "base_dir": str(base), "scope": scope,
                "archive": f"{name}.zip", "archive_sha256": core.sha256_bytes(archive),
                "file_count": len(entries), "total_bytes": total, "files": entries}
    manifest_path = core.atomic_write_json(ctx.output_dir / f"{name}.manifest.json", manifest, ctx.policy)
    core.ledger_append(_checkpoint_index(ctx), ctx.slug,
                       {"event": "pack", "name": name, "manifest": str(manifest_path), "archive": str(archive_path),
                        "files": len(entries)}, ctx.policy)
    ctx.outputs.extend([str(archive_path), str(manifest_path)])
    return {"summary": {"name": name, "files": len(entries), "bytes": total, "scope": scope,
                        "archive": str(archive_path), "manifest": str(manifest_path)},
            "items": [{"path": e["path"], "size": e["size"], "sha256": e["sha256"][:16] + "..."} for e in entries[:200]]}


def _load_manifest(ctx):
    path = ctx.opt("manifest")
    if not path and ctx.input_path is not None and ctx.input_path.suffix.lower() == ".json":
        path = str(ctx.input_path)
    if not path:
        raise core.InputError("a manifest is needed: use --input <name>.manifest.json or option manifest=<path>")
    manifest_path = Path(path).resolve()
    manifest = core.read_json_file(manifest_path, ctx.policy)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise core.InputError("not a checkpoint manifest (missing 'files')")
    manifest["_path"] = str(manifest_path)
    return manifest


def _verify(manifest, base, ctx, check_archive):
    items = []
    listed = set()
    for entry in manifest["files"]:
        ctx.tick()
        safe = _safe_rel(entry.get("path"))
        if safe is None:
            items.append({"path": str(entry.get("path")), "status": "rejected", "detail": "unsafe path in manifest"})
            continue
        listed.add(safe)
        target = base / safe
        if not target.is_file():
            items.append({"path": safe, "status": "missing", "detail": ""})
        elif core.sha256_file(target) != entry.get("sha256"):
            items.append({"path": safe, "status": "changed", "detail": "content differs from checkpoint"})
        else:
            items.append({"path": safe, "status": "ok", "detail": ""})
    unexpected = []
    if manifest.get("scope") == "directory" and base.is_dir():
        for f in _walk(base, ctx):
            if f.name.endswith((".zip", ".manifest.json")):
                continue
            rel = str(f.relative_to(base)).replace("\\", "/")
            if rel not in listed:
                unexpected.append(rel)
                items.append({"path": rel, "status": "unexpected", "detail": "not in checkpoint (possible side effect)"})
    archive_status = None
    if check_archive:
        archive = (Path(manifest["_path"]).parent / manifest.get("archive", "")).resolve()
        if not archive.is_file():
            archive_status = "archive missing"
        elif core.sha256_file(archive) != manifest.get("archive_sha256"):
            archive_status = "archive changed"
        else:
            archive_status = "archive ok"
    bad = [i for i in items if i["status"] in ("missing", "changed", "rejected")]
    ok = not bad and (archive_status in (None, "archive ok"))
    counts = defaultdict(int)
    for it in items:
        counts[it["status"]] += 1
    return {"ok": ok, "items": items, "archive": archive_status, "unexpected": len(unexpected),
            "counts": dict(counts)}


@handler("K:verify")
def k_verify(ctx):
    manifest = _load_manifest(ctx)
    base_opt = ctx.opt("base")
    base = Path(base_opt).resolve() if base_opt else Path(manifest.get("base_dir", ctx.base_dir()))
    result = _verify(manifest, base, ctx, ctx.opt_bool("archive", False))
    verdict = "verified" if result["ok"] else "stale or changed"
    if result["unexpected"]:
        verdict += "; unexpected files present"
    return {"summary": {"checkpoint": manifest.get("name"), "base": str(base), "verdict": verdict,
                        **result["counts"], "archive": result["archive"] or "not checked"},
            "items": result["items"]}


@handler("K:latest")
def k_latest(ctx):
    records, problems = core.ledger_read(_checkpoint_index(ctx), ctx.policy)
    checked = []
    for rec in reversed(records):
        data = rec.get("data", {})
        mpath = Path(data.get("manifest", ""))
        if not mpath.is_file():
            mpath = ctx.output_dir / f"{data.get('name', '')}.manifest.json"
        if not mpath.is_file():
            checked.append({"name": data.get("name"), "status": "manifest not found"})
            continue
        try:
            manifest = core.read_json_file(mpath, ctx.policy)
            manifest["_path"] = str(mpath.resolve())
            result = _verify(manifest, Path(manifest.get("base_dir", ".")), ctx, True)
        except core.InputError as exc:
            checked.append({"name": data.get("name"), "status": f"unreadable: {exc}"})
            continue
        checked.append({"name": manifest.get("name"), "status": "verified" if result["ok"] else "failed verification",
                        "created": manifest.get("created")})
        if result["ok"]:
            return {"summary": {"latest_verified": manifest.get("name"), "created": manifest.get("created"),
                                "manifest": str(mpath), "index_records": len(records),
                                "damaged_index_lines": len(problems)},
                    "items": checked[:50],
                    "resume_hint": "Restore or continue from this checkpoint; run K:verify first against the working folder."}
    raise core.InputError("no verified checkpoint found; create one with K:pack or check the index for damage")


@handler("K:handoff")
def k_handoff(ctx):
    notes = {}
    if ctx.input_path is not None and ctx.input_path.is_file() and ctx.input_path.suffix.lower() == ".json":
        notes = ctx.read_json()
        if not isinstance(notes, dict):
            raise core.InputError("handoff input must be a JSON object")
    records, _ = core.ledger_read(_checkpoint_index(ctx), ctx.policy)
    latest_name = records[-1]["data"].get("name") if records else "none"
    ledger_name = ctx.opt("ledger")
    tail_lines = []
    if ledger_name:
        ledger_path = ctx.state_dir / "ledgers" / f"{ledger_name}.jsonl"
        ledger_records, _ = core.ledger_read(ledger_path, ctx.policy)
        for rec in ledger_records[-10:]:
            tail_lines.append(f"- seq {rec['seq']} ({rec['ts']}): {core.canonical(rec['data'])[:200]}")
    sections = [
        ("Decisions made", notes.get("decisions", [])),
        ("Open issues", notes.get("open_issues", [])),
        ("Blockers", notes.get("blockers", [])),
        ("Next actions (in order)", notes.get("next_actions", [])),
        ("Exact details to preserve", notes.get("pinned", [])),
    ]
    body = [f"# Handoff: {ctx.opt('title') or ctx.capability['name']}", "",
            f"- Generated (UTC): {core.now_iso()}",
            f"- Latest checkpoint recorded: {latest_name}",
            "- Resume rule: verify the checkpoint with K:verify before continuing; do not repeat completed consequential actions.", ""]
    items = []
    for title, values in sections:
        body.append(f"## {title}")
        if not values:
            body.append("_None recorded._")
        for v in values if isinstance(values, list) else [values]:
            body.append(f"- {v}")
            items.append({"section": title, "entry": core.excerpt(str(v), 200)})
        body.append("")
    if tail_lines:
        body += ["## Recent ledger entries", *tail_lines, ""]
    path = ctx.write_artifact(f"{ctx.slug}.handoff.md", "\n".join(body) + "\n")
    return {"summary": {"entries": len(items), "latest_checkpoint": latest_name, "artifact": path},
            "items": items}


@handler("K:restore")
def k_restore(ctx):
    manifest = _load_manifest(ctx)
    target_raw = ctx.opt("target")
    if not target_raw:
        raise core.InputError("option target=<folder to restore into> is required")
    target = Path(target_raw).resolve()
    archive = (Path(manifest["_path"]).parent / manifest.get("archive", "")).resolve()
    if not archive.is_file():
        raise core.InputError(f"archive not found next to the manifest: {archive.name}")
    if core.sha256_file(archive) != manifest.get("archive_sha256"):
        raise core.InputError("archive does not match the manifest hash; refusing to restore")
    non_empty = target.is_dir() and any(target.iterdir())
    if non_empty and not ctx.approve:
        ctx.require_approval("restoring into a non-empty folder can overwrite files; a person must approve",
                             {"target": str(target), "checkpoint": manifest.get("name")})
    expected = {e["path"]: e["sha256"] for e in manifest["files"]}
    staging_root = ctx.state_dir / "tmp"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="restore-", dir=str(staging_root)))
    backup_dir = ctx.state_dir / "backups" / core.now_iso().replace(":", "").replace("-", "")
    moved, backed_up, skipped = [], [], []
    try:
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                ctx.tick()
                safe = _safe_rel(name)
                if safe is None or safe not in expected:
                    raise core.InputError(f"archive member not in manifest or unsafe: {name}")
                data = zf.read(name)
                if core.sha256_bytes(data) != expected[safe]:
                    raise core.InputError(f"archive member hash mismatch: {safe}")
                core.atomic_write_bytes(staging / safe, data, ctx.policy)
        for rel in sorted(expected):
            dest = target / rel
            src = staging / rel
            if dest.is_file() and core.sha256_file(dest) == expected[rel]:
                skipped.append(rel)
                continue
            if dest.exists():
                bak = backup_dir / rel
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, bak)
                backed_up.append(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)
            moved.append(rel)
    except (core.ToolError, OSError, zipfile.BadZipFile) as exc:
        for rel in moved:
            if rel not in backed_up:
                try:
                    os.remove(target / rel)
                except OSError:
                    pass
        for rel in backed_up:
            try:
                shutil.copy2(backup_dir / rel, target / rel)
            except OSError:
                pass
        raise core.ToolError(f"restore failed and was rolled back: {exc}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    core.ledger_append(_checkpoint_index(ctx), ctx.slug,
                       {"event": "restore", "checkpoint": manifest.get("name"), "target": str(target),
                        "written": len(moved), "backups": str(backup_dir) if backed_up else ""}, ctx.policy)
    return {"summary": {"checkpoint": manifest.get("name"), "target": str(target), "written": len(moved),
                        "unchanged": len(skipped), "backed_up": len(backed_up),
                        "backup_folder": str(backup_dir) if backed_up else "none"},
            "items": [{"path": r, "action": "written" + (" (previous copy backed up)" if r in backed_up else "")} for r in moved]}
