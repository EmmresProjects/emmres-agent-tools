#!/usr/bin/env python3
"""tools-rwt-core.py - Shared core for the Offline Research, Writing & Translation
Workbench (RWT).

Provides: workspace/store management, hash-chained tamper-evident audit log,
JSON/text output envelope, error types, exit codes, text utilities
(tokenization, sentence splitting, n-grams, Jaccard, readability), JSON file
helpers, and the sibling-module loader used by the other tools-* modules.

This module is a library; it is not intended to be executed directly, though it
prints a short capability summary when run without arguments.
"""
import difflib
import hashlib
import json
import math
import os
import re
import sys
import time
import unicodedata
import uuid
from datetime import datetime, timezone

TOOL_VERSION = "1.0.0"
WORKSPACE_DIR = ".rwt"
STORES_DIR = os.path.join(WORKSPACE_DIR, "stores")
FILES_DIR = os.path.join(WORKSPACE_DIR, "files")
SNAPSHOTS_DIR = os.path.join(WORKSPACE_DIR, "snapshots")
EXPORTS_DIR = os.path.join(WORKSPACE_DIR, "exports")
CONFIG_PATH = os.path.join(WORKSPACE_DIR, "workspace.json")
AUDIT_PATH = os.path.join(WORKSPACE_DIR, "audit.jsonl")

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_VALIDATION = 2
EXIT_UNAVAILABLE = 3
EXIT_BLOCKED = 4

ALLOWED_COLLECTIONS = frozenset([
    "documents", "references", "claims", "evidence", "reviews", "decisions",
    "terms", "segments", "registry", "disclosures", "feedback", "corrections",
    "gaps", "benchmarks", "lineage", "queue", "changes", "snapshots",
])


class RwtError(Exception):
    """Base error; carries an exit code and an error type label."""

    exit_code = EXIT_USAGE

    def __init__(self, message, *, details=None, exit_code=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if exit_code is not None:
            self.exit_code = exit_code


class ValidationError(RwtError):
    exit_code = EXIT_VALIDATION


class UnavailableError(RwtError):
    exit_code = EXIT_UNAVAILABLE


class BlockedError(RwtError):
    exit_code = EXIT_BLOCKED


def load_sibling(module_name, filename):
    """Load a sibling tools-*.py module by explicit filename (hyphens cannot be imported)."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, filename)
    import importlib.util
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RwtError(f"Cannot load sibling module {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix=""):
    return (prefix + uuid.uuid4().hex[:12])


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, limit_bytes=None):
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
            if limit_bytes and read >= limit_bytes:
                break
    return h.hexdigest()


def json_dump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def atomic_write(path, content):
    tmp = path + ".tmp" + uuid.uuid4().hex[:8]
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp, path)


def read_json_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def asset_path(filename):
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, filename)
    if not os.path.isfile(path):
        raise RwtError(f"Required asset missing: {filename} (expected beside the tools)")
    return path


def load_asset(filename):
    return read_json_file(asset_path(filename))


# ---------------------------------------------------------------- workspace
class Workspace:
    """A local research workspace: config + JSONL stores + files + audit log."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.config = None

    @property
    def exists(self):
        return os.path.isfile(os.path.join(self.root, CONFIG_PATH))

    def create(self, name, preset=None, force=False):
        if self.exists and not force:
            raise ValidationError(
                f"Workspace already initialized at {self.root} (use --force only with care)")
        for sub in (STORES_DIR, FILES_DIR, SNAPSHOTS_DIR, EXPORTS_DIR):
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)
        cfg = {
            "version": TOOL_VERSION,
            "name": name or os.path.basename(self.root),
            "created": now_iso(),
            "preset": preset or "minimal",
            "languages": ["en"],
            "quotation_limits": {"default_words": 90, "licensed_words": 250},
            "export_gate": {"require_approval": True, "embargo_until": None},
            "retention_days": 365,
            "models": {},
            "redaction_profile": "standard",
            "roles": ["author", "editor", "fact-checker", "translator", "domain-expert",
                      "legal", "approver"],
        }
        if preset:
            try:
                presets = load_asset("asset-config-presets.json")
                chosen = next((p for p in presets.get("presets", [])
                               if p.get("name") == preset), None)
            except RwtError:
                chosen = None
            if chosen:
                for key in ("languages", "quotation_limits", "export_gate",
                            "retention_days", "redaction_profile", "roles"):
                    if key in chosen:
                        cfg[key] = chosen[key]
        atomic_write(os.path.join(self.root, CONFIG_PATH),
                     json.dumps(cfg, ensure_ascii=False, indent=2))
        audit = os.path.join(self.root, AUDIT_PATH)
        with open(audit, "a", encoding="utf-8") as fh:
            fh.write(json_dump({"ts": now_iso(), "actor": "system", "action": "workspace.init",
                                "detail": {"name": cfg["name"], "preset": cfg["preset"]},
                                "prev": None}) + "\n")
        self._rechain()
        self.config = cfg
        return cfg

    def load(self):
        if not self.exists:
            raise ValidationError(
                f"No RWT workspace at {self.root}. Run: rwt init {self.root}")
        with open(os.path.join(self.root, CONFIG_PATH), "r", encoding="utf-8") as fh:
            self.config = json.load(fh)
        return self.config

    def save_config(self, cfg):
        atomic_write(os.path.join(self.root, CONFIG_PATH),
                     json.dumps(cfg, ensure_ascii=False, indent=2))
        self.config = cfg

    # ---------------- collections
    def store_path(self, collection):
        if collection not in ALLOWED_COLLECTIONS:
            raise ValidationError(
                f"Unknown collection '{collection}'. Allowed: {sorted(ALLOWED_COLLECTIONS)}")
        path = os.path.join(self.root, STORES_DIR, collection + ".jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def list_records(self, collection, limit=None):
        path = self.store_path(collection)
        records = []
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        records.append({"_corrupt_line": line[:200]})
        if limit:
            records = records[-limit:]
        return records

    def add_record(self, collection, data, actor="local"):
        rec = dict(data)
        rec.setdefault("id", new_id(collection[:3] + "_"))
        rec.setdefault("collection", collection)
        rec.setdefault("created", now_iso())
        rec["updated"] = rec["created"]
        path = self.store_path(collection)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json_dump(rec) + "\n")
        self.audit(actor, f"{collection}.add", {"id": rec["id"]})
        return rec

    def update_record(self, collection, record_id, data, actor="local"):
        records = self.list_records(collection)
        found, out = False, []
        for rec in records:
            if rec.get("id") == record_id:
                rec = dict(rec)
                rec.update(data)
                rec["updated"] = now_iso()
                found = True
            out.append(rec)
        if not found:
            raise ValidationError(f"No record {record_id} in {collection}")
        self._rewrite_collection(collection, out)
        self.audit(actor, f"{collection}.update", {"id": record_id})
        return next(r for r in out if r.get("id") == record_id)

    def _rewrite_collection(self, collection, records):
        path = self.store_path(collection)
        atomic_write(path, "".join(json_dump(r) + "\n" for r in records))

    # ---------------- files
    def store_file(self, src_path):
        if not os.path.isfile(src_path):
            raise ValidationError(f"File not found: {src_path}")
        with open(src_path, "rb") as fh:
            data = fh.read()
        digest = sha256_bytes(data)
        dest_dir = os.path.join(self.root, FILES_DIR)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, digest[:16] + "-" + os.path.basename(src_path))
        if not os.path.isfile(dest):
            with open(dest, "wb") as fh:
                fh.write(data)
        return {"sha256": digest, "stored_as": os.path.relpath(dest, self.root),
                "bytes": len(data), "original_name": os.path.basename(src_path)}

    # ---------------- snapshots
    def snapshot(self, label="manual"):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        snap_dir = os.path.join(self.root, SNAPSHOTS_DIR, f"{stamp}-{label}")
        os.makedirs(snap_dir, exist_ok=True)
        manifest = []
        for rel_root, _dirs, files in os.walk(os.path.join(self.root, WORKSPACE_DIR)):
            for name in files:
                src = os.path.join(rel_root, name)
                rel = os.path.relpath(src, self.root)
                if rel.startswith(os.path.join(WORKSPACE_DIR, "snapshots")):
                    continue  # never nest snapshots
                digest = sha256_file(src)
                rel_snap = os.path.join(snap_dir, os.path.basename(rel) + "." + digest[:8])
                with open(src, "rb") as fh:
                    content = fh.read()
                with open(rel_snap, "wb") as fh:
                    fh.write(content)
                manifest.append({"original": rel.replace(os.sep, "/"),
                                 "snapshot_file": os.path.basename(rel_snap),
                                 "sha256": digest, "bytes": len(content)})
        atomic_write(os.path.join(snap_dir, "manifest.json"),
                     json.dumps({"label": label, "created": now_iso(),
                                 "files": manifest}, ensure_ascii=False, indent=2))
        self.audit("local", "workspace.snapshot", {"label": label, "files": len(manifest)})
        return {"snapshot": os.path.relpath(snap_dir, self.root).replace(os.sep, "/"),
                "files": len(manifest), "created": now_iso()}

    def list_snapshots(self):
        base = os.path.join(self.root, SNAPSHOTS_DIR)
        out = []
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                mp = os.path.join(base, name, "manifest.json")
                if os.path.isfile(mp):
                    with open(mp, "r", encoding="utf-8") as fh:
                        m = json.load(fh)
                    out.append({"snapshot": f"{WORKSPACE_DIR}/snapshots/{name}",
                                "label": m.get("label"), "created": m.get("created"),
                                "files": len(m.get("files", []))})
        return out

    def restore_snapshot(self, snapshot_name, actor="local", confirm=False):
        base = os.path.join(self.root, SNAPSHOTS_DIR, os.path.basename(snapshot_name.rstrip("/")))
        mp = os.path.join(base, "manifest.json")
        if not os.path.isfile(mp):
            raise ValidationError(f"Snapshot not found: {snapshot_name}")
        if not confirm:
            raise BlockedError(
                "Restore overwrites workspace stores. Re-run with --yes to confirm "
                "(a pre-restore snapshot is taken automatically).")
        pre = self.snapshot("pre-restore")
        with open(mp, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        restored = 0
        for entry in manifest.get("files", []):
            src = os.path.join(base, entry["snapshot_file"])
            if sha256_file(src) != entry["sha256"]:
                raise ValidationError(f"Snapshot file failed checksum: {entry['snapshot_file']}")
            dest = os.path.join(self.root, entry["original"].replace("/", os.sep))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(src, "rb") as fh:
                content = fh.read()
            with open(dest, "wb") as fh:
                fh.write(content)
            restored += 1
        self.audit(actor, "workspace.restore",
                   {"snapshot": snapshot_name, "restored": restored, "pre_restore": pre["snapshot"]})
        return {"restored_files": restored, "pre_restore_snapshot": pre["snapshot"]}

    # ---------------- audit (hash chain)
    def _audit_entries(self):
        path = os.path.join(self.root, AUDIT_PATH)
        entries = []
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            entries.append({"_corrupt": True, "raw": line[:200]})
        return entries

    def _rechain(self):
        entries = self._audit_entries()
        prev = None
        out = []
        for e in entries:
            e = dict(e)
            e.pop("hash", None)
            e["prev"] = prev
            body = json_dump(e)
            e["hash"] = sha256_bytes(body.encode("utf-8"))
            prev = e["hash"]
            out.append(e)
        if out:
            atomic_write(os.path.join(self.root, AUDIT_PATH),
                         "".join(json_dump(e) + "\n" for e in out))

    def audit(self, actor, action, detail):
        path = os.path.join(self.root, AUDIT_PATH)
        entries = self._audit_entries()
        prev = entries[-1].get("hash") if entries else None
        entry = {"ts": now_iso(), "actor": actor, "action": action,
                 "detail": detail, "prev": prev}
        entry["hash"] = sha256_bytes(json_dump(entry).encode("utf-8"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json_dump(entry) + "\n")
        return entry["hash"]

    def verify_audit(self):
        entries = self._audit_entries()
        prev = None
        problems = []
        for i, e in enumerate(entries):
            if e.get("_corrupt"):
                problems.append({"index": i, "issue": "unparseable audit line"})
                continue
            if e.get("prev") != prev:
                problems.append({"index": i, "issue": "broken chain link",
                                 "expected_prev": prev, "found_prev": e.get("prev")})
            found = e.get("hash")
            body = {k: v for k, v in e.items() if k != "hash"}
            if sha256_bytes(json_dump(body).encode("utf-8")) != found:
                problems.append({"index": i, "issue": "hash mismatch (tampered entry)"})
            prev = found
        return {"entries": len(entries), "ok": not problems, "problems": problems}


# ---------------------------------------------------------------- text utils
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-'\u00C0-\u024F\u0400-\u04FF\u4E00-\u9FFF\uAC00-\uD7AF]*")
SENT_RE = re.compile(r"(?<=[.!?…])\s+(?=[\"'(\[]?[A-Z0-9])")
STOPWORDS = set("""a an and are as at be been but by for from had has have he her his i in into is it its of on or that the their this to was were will with you your we they them its our not no do does did
                    el la los las de del y o en un una que es son al por para con sin sobre entre es está están como más menos muy
                    ang ng mga sa na at ay ito iyon siya sila kami kayo ko mo niya kanya namin natin""".split())


def normalize_ws(text):
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", text or "").strip())


def tokenize(text):
    return WORD_RE.findall(unicodedata.normalize("NFC", (text or "").lower()))


def content_tokens(text):
    return [t for t in tokenize(text) if t not in STOPWORDS]


def sentences(text):
    text = normalize_ws(text)
    if not text:
        return []
    parts = SENT_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def ngrams(tokens, n):
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def shingle_jaccard(text_a, text_b, n=5, k=200):
    def shingles(t):
        toks = tokenize(t)
        return set(ngrams(toks, n)) if len(toks) >= n else set(toks)
    return jaccard(shingles(text_a), shingles(text_b))


def syllables(word):
    word = re.sub(r"[^a-z]", "", (word or "").lower())
    if not word:
        return 1
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups)
    if word.endswith("e") and not word.endswith(("le", "ee", "ye")) and count > 1:
        count -= 1
    return max(1, count)


def flesch_kincaid(text):
    sents = sentences(text)
    words = tokenize(text)
    if not sents or not words:
        return {"reading_ease": None, "grade_level": None,
                "words": len(words), "sentences": len(sents)}
    syl = sum(syllables(w) for w in words)
    wps = len(words) / len(sents)
    spw = syl / len(words)
    ease = 206.835 - 1.015 * wps - 84.6 * spw
    grade = 0.39 * wps + 11.8 * spw - 15.59
    return {"reading_ease": round(ease, 1), "grade_level": round(grade, 1),
            "words": len(words), "sentences": len(sents),
            "words_per_sentence": round(wps, 2), "syllables_per_word": round(spw, 3)}


def sim_ratio(a, b):
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()


def parse_date(value):
    """Best-effort parse of common date strings to ISO date. Returns (iso, uncertain)."""
    value = normalize_ws(str(value))
    if not value:
        return None, True
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value)
    if m:
        return value, False
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", value)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12:  # day first unambiguous
            return f"{y:04d}-{b:02d}-{a:02d}", False
        if b > 12:  # month first unambiguous
            return f"{y:04d}-{a:02d}-{b:02d}", False
        return f"{y:04d}-{a:02d}-{b:02d}", True  # ambiguous: assumed MM/DD/YYYY, locale review needed
    m = re.match(r"^(\d{4})$", value)
    if m:
        return f"{value}-01-01", True
    for fmt in ("%B %d, %Y", "%d %B %Y", "%b %d, %Y", "%Y/%m/%d"):
        try:
            d = datetime.strptime(value, fmt)
            return d.strftime("%Y-%m-%d"), False
        except ValueError:
            continue
    return None, True


def years_since(iso_date):
    try:
        d = datetime.strptime(iso_date[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    now = datetime.now(timezone.utc)
    return round((now - d.replace(tzinfo=timezone.utc)).days / 365.25, 2)


# ---------------------------------------------------------------- output
def emit(result, fmt="json"):
    if fmt == "text":
        print(render_text(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def render_text(obj, indent=0):
    pad = "  " * indent
    if isinstance(obj, dict):
        lines = []
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(render_text(value, indent + 1))
            else:
                lines.append(f"{pad}{key}: {value}")
        return "\n".join(lines)
    if isinstance(obj, list):
        if all(not isinstance(v, (dict, list)) for v in obj):
            return "\n".join(f"{pad}- {v}" for v in obj)
        lines = []
        for i, value in enumerate(obj):
            lines.append(f"{pad}[{i}]")
            lines.append(render_text(value, indent + 1))
        return "\n".join(lines)
    return f"{pad}{obj}"


def make_result(command, result, warnings=None, extra=None):
    out = {"ok": True, "command": command, "result": result,
           "warnings": warnings or [], "duration_ms": None}
    if extra:
        out.update(extra)
    return out


def fail(command, exc, fmt="json"):
    if isinstance(exc, RwtError):
        err = {"type": exc.__class__.__name__, "message": exc.message, "details": exc.details}
        code = exc.exit_code
    else:
        err = {"type": exc.__class__.__name__, "message": str(exc), "details": {}}
        code = EXIT_USAGE
    payload = {"ok": False, "command": command, "error": err}
    if fmt == "text":
        print(f"ERROR [{err['type']}] {err['message']}", file=sys.stderr)
        if err["details"]:
            print(json.dumps(err["details"], ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h", "help"):
        print("tools-rwt-core.py — shared library: Workspace layout, stores, audit chain, text analytics.")
        print("Not a user-facing command; engines import it. Use tools-rwt.py as entry point.")
        print("Workspace structure: .rwt/{workspace.json,stores/*.jsonl,files/,snapshots/,exports/,audit.jsonl}")
        return 0
    print(json.dumps({"tool": "tools-rwt-core.py", "version": TOOL_VERSION,
                      "role": "shared library (not a user command)",
                      "entry_point": "tools-rwt.py"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
