"""tools-rwt-store.py - offline workspace, SQLite store, ingestion, registries, audit, snapshots.

Shared foundation for the offline-research-workbench Skill Tool. Contains the only
database schema in the package, the shared text utilities, and the hash-chained
audit log used by every approval gate.

Loaded by tools-offline-research.py before every other tools-* module.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import unicodedata
import zipfile
from datetime import datetime, timezone

NAME = "store"
ROLE = "workspace + database + shared utilities + audit log"
GROUPS = [
    ("corpus", "Corpus ingestion, installation, integrity, dedupe, licensing, snapshots"),
    ("registry", "Model, corpus, prompt and tool registries"),
    ("jobs", "Bounded local job scheduler"),
    ("state", "Durable research state and checkpoints"),
    ("backup", "Backup, restore verification and offline dependency mirror"),
]
deps: dict = {}

SCHEMA_VERSION = "1"
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl",
                 ".html", ".htm", ".xml", ".srt", ".vtt", ".tex", ".bib", ".ris"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".cache"}


# ---------------------------------------------------------------- errors

class ToolError(Exception):
    """Structured, user-facing failure. `code` becomes the process exit code."""

    def __init__(self, code: int, message: str, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def payload(self) -> dict:
        return {"status": "error", "message": self.message, "exit_code": self.code, **self.detail}


# ---------------------------------------------------------------- configuration

def base_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def default_config_path() -> str:
    return os.path.join(base_dir(), "asset-default-config.json")


def deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | None = None) -> dict:
    cfg = {}
    dflt = default_config_path()
    if os.path.exists(dflt):
        with open(dflt, encoding="utf-8") as fh:
            cfg = json.load(fh)
    if path:
        if not os.path.exists(path):
            raise ToolError(2, f"config file not found: {path}")
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = deep_merge(cfg, json.load(fh))
        except json.JSONDecodeError as exc:
            raise ToolError(2, f"config file is not valid JSON: {path}: {exc}")
    return cfg


def cfg_get(cfg: dict, dotted: str, default=None):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


# ---------------------------------------------------------------- small utilities

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slug(text: str, maxlen: int = 60) -> str:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:maxlen].strip("-") or "item"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes((text or "").encode("utf-8"))


def sha256_file(path: str, limit_mb: int = 256) -> str:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > limit_mb * 1024 * 1024:
                raise ToolError(2, f"file exceeds {limit_mb} MB hashing limit: {path}")
            h.update(chunk)
    return h.hexdigest()


MOJIBAKE_MARKERS = ("Ã©", "Ã¨", "Ã ", "â€™", "â€œ", "â€", "Â ", "Ã±", "Ã¼", "Ã¶", "Ã¡", "Ã­")


def fix_mojibake(text: str) -> str:
    """Repair classic UTF-8 decoded as Latin-1 mojibake; conservative and reversible-checked."""
    if "\u00c3" not in text and "\u00e2\u0080" not in text:
        return text
    for enc in ("latin-1", "cp1252"):
        try:
            candidate = text.encode(enc, errors="strict").decode("utf-8", errors="strict")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if candidate.count("Ã") + candidate.count("â€") < text.count("Ã") + text.count("â€"):
            return candidate
    return text


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", fix_mojibake(text or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_TAG_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
_ANY_TAG = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    """Deterministic HTML -> text with navigation/boilerplate reduction (no third-party parser)."""
    html = _TAG_RE.sub(" ", html)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>", "\n", html, flags=re.I)
    text = _ANY_TAG.sub(" ", html)
    entities = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
                "&#39;": "'", "&rsquo;": "\u2019", "&mdash;": "\u2014", "&ndash;": "\u2013"}
    for k, v in entities.items():
        text = text.replace(k, v)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    # drop short repeated navigation lines and pure link furniture
    kept, seen = [], {}
    for ln in lines:
        if not ln:
            kept.append("")
            continue
        seen[ln] = seen.get(ln, 0) + 1
        if len(ln) < 40 and seen[ln] > 2:
            continue
        kept.append(ln)
    return normalize_text("\n".join(kept))


_ABBREV = {"e.g", "i.e", "cf", "et al", "vs", "no", "fig", "figs", "eq", "sec", "art", "ch",
           "p", "pp", "vol", "dr", "mr", "ms", "mrs", "prof", "inc", "ltd", "co", "st", "jr", "sr"}


def sentences(text: str) -> list[str]:
    """Sentence splitter that respects markdown structure (headings, bullets, blank lines)."""
    text = (text or "").strip()
    if not text:
        return []
    blocks, buffer = [], []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if buffer:
                blocks.append(" ".join(buffer)); buffer = []
            continue
        if re.match(r"^(#{1,6}\s|[-*+]\s|\d+[.)]\s|\|)", stripped):
            if buffer:
                blocks.append(" ".join(buffer)); buffer = []
            blocks.append(stripped)
            continue
        buffer.append(stripped)
    if buffer:
        blocks.append(" ".join(buffer))
    out = []
    for block in blocks:
        if not block:
            continue
        parts = re.split(r'(?<=[.!?\u3002\uff01\uff1f])(["\u201d\u2019)\]]*)\s+', block)
        buf = ""
        for i in range(0, len(parts), 2):
            seg = parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
            if not seg:
                continue
            buf = (buf + " " + seg).strip() if buf else seg
            tail = re.findall(r"([A-Za-z.]+)$", buf.rstrip(".!?\u201d\u2019)]"))
            last_word = (tail[0].lower() if tail else "")
            if last_word in _ABBREV and not buf.endswith((".", "!", "?")):
                continue
            out.append(buf.strip())
            buf = ""
        if buf:
            out.append(buf.strip())
    return [s for s in out if s]


_TOKEN_RE = re.compile(r"[^\W_]+(?:['\u2019-][^\W_]+)*", re.UNICODE)


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def words(text: str) -> int:
    return len(_TOKEN_RE.findall(text or ""))


STOPWORDS = {
    "en": set("""a an the and or but if then than that this these those of in on at to for from by with
        as is are was were be been being it its it's their there here we you they he she i not no nor
        do does did have has had will would can could should may might must about into over under more
        most such other some any all each both few many own same so only very also which who whom whose
        while during between within without because however therefore thus""".split()),
    "fil": set("""ang ng mga sa na at ay si ni kay para kung hindi may mayroon ito iyon dito diyan
        sila kami tayo ako ikaw siya ngunit subalit dahil upang rin din lamang lang ba po mga""".split()),
}


def shingles(text: str, n: int = 5) -> set:
    toks = tokens(text)
    if len(toks) < n:
        return {" ".join(toks)} if toks else set()
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / float(len(a | b)) if inter else 0.0


def read_text(path: str, max_mb: int = 64) -> str:
    """Read a text-bearing local file. PDF/Office/audio/image need an adapter (see tools-rwt-adapters.py)."""
    if not os.path.exists(path):
        raise ToolError(2, f"input not found: {path}")
    if os.path.isdir(path):
        raise ToolError(2, f"input is a directory, expected a file: {path}")
    size = os.path.getsize(path)
    if size > max_mb * 1024 * 1024:
        raise ToolError(2, f"input exceeds {max_mb} MB limit: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext not in TEXT_SUFFIXES:
        raise ToolError(4, f"no offline text extractor for '{ext}'; configure an adapter "
                           f"(models run --kind extract) or supply a text export",
                        {"supported_text_suffixes": sorted(TEXT_SUFFIXES)})
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        text = raw.decode("utf-8", errors="replace")
    text = normalize_text(text)
    if ext in (".html", ".htm", ".xml"):
        text = html_to_text(text)
    return text


def chunk_text(text: str, max_words: int = 220) -> list[str]:
    """Paragraph-preserving chunker used for segments / embeddings."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", normalize_text(text)) if p.strip()]
    chunks, buf = [], []
    for p in paras:
        n = words(p)
        if n > max_words:
            if buf:
                chunks.append(" ".join(buf)); buf = []
            sents, cur = sentences(p), []
            for s in sents:
                if words(" ".join(cur + [s])) > max_words and cur:
                    chunks.append(" ".join(cur)); cur = [s]
                else:
                    cur.append(s)
            if cur:
                chunks.append(" ".join(cur))
            continue
        if words(" ".join(buf + [p])) > max_words and buf:
            chunks.append(" ".join(buf)); buf = [p]
        else:
            buf.append(p)
    if buf:
        chunks.append(" ".join(buf))
    return chunks or ([text.strip()] if text.strip() else [])


def write_json(path: str, data) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def write_text(path: str, text: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- context

class Ctx:
    """Execution context shared by every handler."""

    def __init__(self, workspace: str | None = None, config: str | None = None, actor: str = "operator"):
        self.cfg = load_config(config)
        root = workspace or os.environ.get("RWT_WORKSPACE") or cfg_get(self.cfg, "workspace.default", "rwt-workspace")
        self.root = os.path.abspath(root)
        self.actor = actor or "operator"
        self._conn = None

    # paths --------------------------------------------------------
    def path(self, *parts) -> str:
        return os.path.join(self.root, *parts)

    def out_path(self, name: str) -> str:
        """Resolve an output path: absolute kept, workspace-relative kept, bare names go to outputs/."""
        if os.path.isabs(name):
            p = name
        elif os.sep in name or "/" in name:
            p = self.path(*name.replace("\\", "/").split("/"))
        else:
            p = self.path("outputs", name)
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        return p

    def resolve_input(self, candidate: str) -> str:
        """Resolve an input path against the working directory, then the workspace."""
        if not candidate:
            return candidate
        if os.path.isabs(candidate) or os.path.exists(candidate):
            return candidate
        workspace_candidate = self.path(*candidate.replace("\\", "/").split("/"))
        return workspace_candidate if os.path.exists(workspace_candidate) else candidate

    # database -----------------------------------------------------
    def ensure(self):
        for sub in ("db", "corpus", "outputs", "snapshots", "exports", "state"):
            os.makedirs(self.path(sub), exist_ok=True)
        return self

    @property
    def db_path(self) -> str:
        return self.path("db", "workbench.sqlite3")

    def conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.ensure()
        conn = sqlite3.connect(self.db_path, timeout=cfg_get(self.cfg, "limits.timeout_seconds", 30))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        init_schema(conn)
        self._conn = conn
        return conn

    def close(self):
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    def commit(self):
        if self._conn is not None:
            self._conn.commit()

    # convenience --------------------------------------------------
    def audit(self, action: str, target: str, detail=None):
        audit_append(self.conn(), self.actor, action, target, detail or {})
        self.commit()


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sources(
  id TEXT PRIMARY KEY, title TEXT, doctype TEXT, language TEXT, publisher TEXT,
  issued TEXT, retrieved TEXT, license TEXT, jurisdiction TEXT, uri TEXT,
  path TEXT, media_type TEXT, checksum TEXT, word_count INTEGER DEFAULT 0,
  peer_status TEXT, status TEXT DEFAULT 'active', meta TEXT DEFAULT '{}', created TEXT);
CREATE TABLE IF NOT EXISTS source_versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, version_label TEXT,
  path TEXT, checksum TEXT, issued TEXT, supersedes TEXT, status TEXT DEFAULT 'current',
  note TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS segments(
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, version_label TEXT, ordinal INTEGER,
  kind TEXT DEFAULT 'passage', locator TEXT, page INTEGER, section TEXT,
  start_ms INTEGER, end_ms INTEGER, lang TEXT, text TEXT, checksum TEXT);
CREATE TABLE IF NOT EXISTS claims(
  id TEXT PRIMARY KEY, source_id TEXT, segment_id TEXT, text TEXT, ctype TEXT,
  confidence REAL, status TEXT DEFAULT 'extracted', created TEXT);
CREATE TABLE IF NOT EXISTS claim_evidence(
  id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT, source_id TEXT, segment_id TEXT,
  relation TEXT, score REAL, created TEXT);
CREATE TABLE IF NOT EXISTS entities(
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, segment_id TEXT, mention TEXT,
  etype TEXT, start_off INTEGER, end_off INTEGER, stable_id TEXT, confidence REAL);
CREATE TABLE IF NOT EXISTS relations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, segment_id TEXT, subject TEXT,
  rtype TEXT, object TEXT, confidence REAL);
CREATE TABLE IF NOT EXISTS citations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, segment_id TEXT, raw TEXT,
  ckey TEXT, kind TEXT, locator TEXT, context TEXT, role TEXT);
CREATE TABLE IF NOT EXISTS refs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, ckey TEXT, raw TEXT, title TEXT,
  authors TEXT, year TEXT, venue TEXT, identifier TEXT, resolved INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tm_units(
  id TEXT PRIMARY KEY, src_lang TEXT, tgt_lang TEXT, source TEXT, target TEXT,
  domain TEXT, project TEXT, provenance TEXT, status TEXT DEFAULT 'draft',
  checksum TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS terms(
  id TEXT PRIMARY KEY, term TEXT, lang TEXT, definition TEXT, forbidden TEXT,
  equivalents TEXT DEFAULT '{}', domain TEXT, owner TEXT, status TEXT DEFAULT 'approved', created TEXT);
CREATE TABLE IF NOT EXISTS reviews(
  id TEXT PRIMARY KEY, kind TEXT, ref TEXT, assignee TEXT, status TEXT DEFAULT 'open',
  priority TEXT, decision TEXT, note TEXT, created TEXT, decided TEXT);
CREATE TABLE IF NOT EXISTS approvals(
  id TEXT PRIMARY KEY, action TEXT, target TEXT, actor TEXT, role TEXT, note TEXT,
  created TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS decisions(
  id TEXT PRIMARY KEY, kind TEXT, summary TEXT, rationale TEXT, actor TEXT, ref TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS audit(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, target TEXT,
  detail TEXT, prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, name TEXT, kind TEXT, due TEXT, status TEXT DEFAULT 'pending',
  payload TEXT DEFAULT '{}', attempts INTEGER DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, task TEXT, config_hash TEXT, inputs_hash TEXT, outputs TEXT,
  corpus_snapshot TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS registry(
  kind TEXT, key TEXT, value TEXT, version TEXT, created TEXT, PRIMARY KEY(kind, key, version));
CREATE TABLE IF NOT EXISTS snapshots(
  id TEXT PRIMARY KEY, path TEXT, checksum TEXT, note TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS cache(kind TEXT, key TEXT, value TEXT, created TEXT, PRIMARY KEY(kind, key));
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS benchmarks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, suite TEXT, item TEXT, metric TEXT,
  expected TEXT, actual TEXT, passed INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS incidents(
  id TEXT PRIMARY KEY, kind TEXT, summary TEXT, severity TEXT, status TEXT,
  containment TEXT, actor TEXT, created TEXT);
CREATE INDEX IF NOT EXISTS idx_segments_source ON segments(source_id);
CREATE INDEX IF NOT EXISTS idx_claims_source ON claims(source_id);
CREATE INDEX IF NOT EXISTS idx_audit_seq ON audit(seq);
"""


def init_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    try:
        conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
            text, segment_id UNINDEXED, source_id UNINDEXED, lang UNINDEXED,
            tokenize='unicode61 remove_diacritics 2')""")
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('fts5','1')")
    except sqlite3.OperationalError:
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('fts5','0')")
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (SCHEMA_VERSION,))
    conn.commit()


def meta(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# ---------------------------------------------------------------- audit chain

GENESIS = "0" * 64


def audit_append(conn, actor: str, action: str, target: str, detail: dict) -> dict:
    prev = conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = prev["hash"] if prev else GENESIS
    body = {"ts": now_iso(), "actor": actor, "action": action, "target": target,
            "detail": detail, "prev_hash": prev_hash}
    digest = sha256_text(json.dumps(body, sort_keys=True, ensure_ascii=False))
    conn.execute("""INSERT INTO audit(ts,actor,action,target,detail,prev_hash,hash)
                    VALUES(?,?,?,?,?,?,?)""",
                 (body["ts"], actor, action, target,
                  json.dumps(detail, ensure_ascii=False, sort_keys=True), prev_hash, digest))
    return {**body, "hash": digest}


def audit_verify(conn) -> dict:
    prev_hash, checked, broken = GENESIS, 0, []
    for row in conn.execute("SELECT * FROM audit ORDER BY seq ASC"):
        body = {"ts": row["ts"], "actor": row["actor"], "action": row["action"],
                "target": row["target"], "detail": json.loads(row["detail"] or "{}"),
                "prev_hash": row["prev_hash"]}
        expect = sha256_text(json.dumps(body, sort_keys=True, ensure_ascii=False))
        checked += 1
        if expect != row["hash"] or row["prev_hash"] != prev_hash:
            broken.append({"seq": row["seq"], "action": row["action"]})
        prev_hash = row["hash"]
    return {"status": "ok" if not broken else "tampered", "entries_checked": checked,
            "broken_entries": broken}


# ---------------------------------------------------------------- approval gates

def gated_actions(cfg) -> list:
    return list(cfg_get(cfg, "approval.gated_actions", []) or [])


def action_is_gated(cfg, action: str) -> bool:
    gates = gated_actions(cfg)
    return any(action == g or (g.endswith(".*") and action.startswith(g[:-1])) for g in gates)


def record_approval(conn, action: str, target: str, actor: str, role: str, note: str = "") -> dict:
    aid = f"apr-{int(time.time() * 1000)}-{slug(target, 12)}"
    body = {"id": aid, "action": action, "target": target, "actor": actor,
            "role": role, "note": note, "created": now_iso()}
    digest = sha256_text(json.dumps(body, sort_keys=True))
    conn.execute("""INSERT INTO approvals(id,action,target,actor,role,note,created,hash)
                    VALUES(?,?,?,?,?,?,?,?)""",
                 (aid, action, target, actor, role, note, body["created"], digest))
    audit_append(conn, actor, "approval.recorded", f"{action}:{target}",
                 {"approval_id": aid, "role": role, "note": note})
    conn.commit()
    return {**body, "hash": digest}


def require_approval(ctx: "Ctx", action: str, target: str, approved: bool,
                     approver: str | None, note: str = "") -> dict:
    """Enforce an approval gate. Raises ToolError(3) when the gate is closed."""
    if not action_is_gated(ctx.cfg, action):
        return {"gated": False, "approved": True}
    if not approved:
        role = cfg_get(ctx.cfg, "approval.required_role", "approver")
        raise ToolError(3, f"approval required for '{action}'; re-run with --approve --approver {role}:<name> "
                           f"after a reviewer records the decision", {"action": action, "target": target})
    if not approver or ":" not in approver:
        raise ToolError(3, "--approver must be given as <role>:<name>, e.g. approver:a.reviewer",
                        {"action": action})
    role, name = approver.split(":", 1)
    rec = record_approval(ctx.conn(), action, target, name, role, note)
    return {"gated": True, "approved": True, "approval": rec}


# ---------------------------------------------------------------- source helpers

def upsert_source(conn, data: dict) -> dict:
    sid = data.get("id") or f"src-{slug(data.get('title', 'untitled'), 40)}-{data['checksum'][:8]}"
    conn.execute("""INSERT INTO sources(id,title,doctype,language,publisher,issued,retrieved,license,
        jurisdiction,uri,path,media_type,checksum,word_count,peer_status,status,meta,created)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET title=excluded.title, checksum=excluded.checksum,
          word_count=excluded.word_count, path=excluded.path, meta=excluded.meta""",
                 (sid, data.get("title"), data.get("doctype"), data.get("language"),
                  data.get("publisher"), data.get("issued"), data.get("retrieved") or now_iso(),
                  data.get("license"), data.get("jurisdiction"), data.get("uri"), data.get("path"),
                  data.get("media_type"), data.get("checksum"), int(data.get("word_count", 0)),
                  data.get("peer_status"), data.get("status", "active"),
                  json.dumps(data.get("meta", {}), ensure_ascii=False), now_iso()))
    return {"id": sid}


def add_segments(conn, source_id: str, text: str, lang: str, max_words: int = 220,
                 kind: str = "passage", version_label: str = "v1") -> list:
    ids = []
    for i, chunk in enumerate(chunk_text(text, max_words)):
        seg_id = f"{source_id}#p{i + 1}"
        conn.execute("""INSERT OR REPLACE INTO segments(id,source_id,version_label,ordinal,kind,
            locator,page,section,start_ms,end_ms,lang,text,checksum)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (seg_id, source_id, version_label, i + 1, kind, f"para {i + 1}", None, None,
                      None, None, lang, chunk, sha256_text(chunk)))
        try:
            conn.execute("DELETE FROM segments_fts WHERE segment_id=?", (seg_id,))
            conn.execute("INSERT INTO segments_fts(text,segment_id,source_id,lang) VALUES(?,?,?,?)",
                         (chunk, seg_id, source_id, lang))
        except sqlite3.OperationalError:
            pass
        ids.append(seg_id)
    return ids


TIMECODE_RE = re.compile(r"^(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(?P<end>\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})")


def parse_transcript_cues(text: str) -> list:
    """Parse SRT/WebVTT text into cues with millisecond offsets and optional speaker labels."""
    cues, pending = [], []
    lines = text.replace("\r\n", "\n").split("\n")
    for raw in lines:
        line = raw.strip()
        if line.upper().startswith("WEBVTT"):
            continue
        m = TIMECODE_RE.match(line)
        if m:
            if pending:
                cues.append(pending)
            pending = [ms_from_timecode(m.group("start")), ms_from_timecode(m.group("end")), []]
            continue
        if pending is None:
            continue
        if not line:
            if pending and pending[2]:
                cues.append(pending)
                pending = None
            continue
        if pending and not re.fullmatch(r"\d+", line):
            pending[2].append(line)
    if pending and pending[2]:
        cues.append(pending)
    out = []
    for start, end, body in cues:
        if not body:
            continue
        joined = " ".join(body)
        speaker = None
        smatch = re.match(r"^([A-Z][A-Z0-9 .'\-]{1,30}):\s*(.*)$", joined)
        if smatch:
            speaker, joined = smatch.group(1).strip(), smatch.group(2)
        out.append({"start_ms": start, "end_ms": end, "text": joined.strip(), "speaker": speaker})
    return out


def ms_from_timecode(value: str) -> int:
    value = value.replace(",", ".")
    hms, _, frac = value.partition(".")
    parts = [int(x) for x in hms.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    hours, minutes, seconds = parts[-3:]
    return ((hours * 3600) + (minutes * 60) + seconds) * 1000 + int((frac or "0").ljust(3, "0")[:3])


def add_timecoded_segments(conn, source_id: str, cues: list, lang: str, kind: str = "transcript") -> list:
    ids = []
    for i, cue in enumerate(cues):
        seg_id = f"{source_id}#t{i + 1}"
        text = cue["text"]
        conn.execute("""INSERT OR REPLACE INTO segments(id,source_id,version_label,ordinal,kind,
            locator,page,section,start_ms,end_ms,lang,text,checksum)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (seg_id, source_id, "v1", i + 1, kind,
                      f"{fmt_ms(cue['start_ms'])}-{fmt_ms(cue['end_ms'])}",
                      None, cue.get("speaker"), cue["start_ms"], cue["end_ms"], lang, text,
                      sha256_text(text)))
        try:
            conn.execute("DELETE FROM segments_fts WHERE segment_id=?", (seg_id,))
            conn.execute("INSERT INTO segments_fts(text,segment_id,source_id,lang) VALUES(?,?,?,?)",
                         (text, seg_id, source_id, lang))
        except sqlite3.OperationalError:
            pass
        ids.append(seg_id)
    return ids


def fmt_ms(value: int) -> str:
    value = int(value or 0)
    hours, rem = divmod(value, 3600000)
    minutes, rem = divmod(rem, 60000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def reindex_fts(conn) -> int:
    try:
        conn.execute("DELETE FROM segments_fts")
        conn.execute("INSERT INTO segments_fts(text,segment_id,source_id,lang) "
                     "SELECT text,id,source_id,lang FROM segments")
        return conn.execute("SELECT count(*) c FROM segments").fetchone()["c"]
    except sqlite3.OperationalError:
        return 0


def get_segment(conn, seg_id: str):
    row = conn.execute("SELECT * FROM segments WHERE id=?", (seg_id,)).fetchone()
    return dict(row) if row else None


def get_source(conn, source_id: str):
    row = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["meta"] = json.loads(d.get("meta") or "{}")
    return d


def snapshot_corpus(ctx: "Ctx", note: str = "") -> dict:
    ctx.ensure()
    sid = f"snap-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    target = ctx.path("snapshots", f"{sid}.zip")
    manifest = {"snapshot_id": sid, "created": now_iso(), "note": note, "files": []}
    ctx.commit()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for sub in ("db", "corpus", "state"):
            base = ctx.path(sub)
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for fn in sorted(filenames):
                    if fn.endswith(("-wal", "-shm")):
                        continue
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, ctx.root).replace(os.sep, "/")
                    zf.write(full, rel)
                    manifest["files"].append({"path": rel, "checksum": sha256_file(full),
                                              "bytes": os.path.getsize(full)})
        zf.writestr("snapshot-manifest.json", json.dumps(manifest, indent=2))
    digest = sha256_file(target)
    ctx.conn().execute("INSERT OR REPLACE INTO snapshots(id,path,checksum,note,created) VALUES(?,?,?,?,?)",
                       (sid, os.path.relpath(target, ctx.root), digest, note, now_iso()))
    ctx.commit()
    return {"snapshot_id": sid, "path": os.path.relpath(target, ctx.root), "checksum": digest,
            "file_count": len(manifest["files"])}


# ---------------------------------------------------------------- ops: corpus

def op_corpus_ingest(ctx: Ctx, input_path: str, title=None, doctype="document", language="en",
                     license=None, publisher=None, issued=None, jurisdiction=None, uri=None,
                     copy=True, peer_status="unknown", max_words=None) -> dict:
    max_mb = cfg_get(ctx.cfg, "limits.max_file_mb", 64)
    max_words = max_words or cfg_get(ctx.cfg, "limits.max_words_per_segment", 220)
    if not input_path or not os.path.exists(input_path):
        raise ToolError(2, f"input not found: {input_path}")
    input_path = os.path.abspath(input_path)
    files = []
    if os.path.isdir(input_path):
        depth_limit = cfg_get(ctx.cfg, "limits.max_scan_depth", 6)
        limit = cfg_get(ctx.cfg, "limits.max_files_per_ingest", 500)
        for dirpath, dirnames, filenames in os.walk(input_path):
            rel_depth = os.path.relpath(dirpath, input_path).count(os.sep)
            if rel_depth >= depth_limit:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in sorted(filenames):
                files.append(os.path.join(dirpath, fn))
        if len(files) > limit:
            raise ToolError(2, f"ingest would process {len(files)} files, limit is {limit}; "
                               f"narrow the input or raise limits.max_files_per_ingest")
    else:
        files = [input_path]
    results, skipped = [], []
    conn = ctx.conn()
    for path in files:
        ext = os.path.splitext(path)[1].lower()
        if ext not in TEXT_SUFFIXES:
            skipped.append({"path": os.path.relpath(path, ctx.root) if path.startswith(ctx.root) else path,
                            "reason": f"unsupported text format {ext}"})
            continue
        text = read_text(path, max_mb)
        if not text:
            skipped.append({"path": path, "reason": "empty after extraction"})
            continue
        digest = sha256_text(text)
        doc_title = title if (title and len(files) == 1) else os.path.splitext(os.path.basename(path))[0]
        stored_path = os.path.relpath(path, ctx.root) if path.startswith(ctx.root) else path
        if copy and not path.startswith(ctx.root):
            # store the normalized text so the file checksum equals the recorded content checksum
            dest = ctx.path("corpus", f"{slug(doc_title, 50)}-{digest[:8]}{ext}")
            write_text(dest, text)
            stored_path = os.path.relpath(dest, ctx.root)
        info = upsert_source(conn, {
            "title": doc_title, "doctype": doctype, "language": language, "publisher": publisher,
            "issued": issued, "license": license, "jurisdiction": jurisdiction, "uri": uri,
            "path": stored_path, "media_type": ext.lstrip("."), "checksum": digest,
            "word_count": words(text), "peer_status": peer_status,
            "meta": {"acquisition_route": "local-file", "operator": ctx.actor,
                     "ingested": now_iso(), "original_name": os.path.basename(path)}})
        if ext in (".srt", ".vtt"):
            cues = parse_transcript_cues(text)
            seg_ids = add_timecoded_segments(conn, info["id"], cues, language) if cues else []
        else:
            seg_ids = add_segments(conn, info["id"], text, language, max_words)
        results.append({"source_id": info["id"], "title": doc_title, "segments": len(seg_ids),
                        "words": words(text), "checksum": digest, "path": stored_path})
    ctx.commit()
    audit_append(conn, ctx.actor, "corpus.ingest", input_path,
                 {"sources": len(results), "skipped": len(skipped)})
    ctx.commit()
    return {"status": "ok", "ingested": results, "skipped": skipped,
            "source_count": conn.execute("SELECT count(*) c FROM sources").fetchone()["c"],
            "segment_count": conn.execute("SELECT count(*) c FROM segments").fetchone()["c"]}


def op_corpus_list(ctx: Ctx, doctype=None, language=None, limit=50) -> dict:
    conn = ctx.conn()
    sql, args = "SELECT id,title,doctype,language,issued,license,word_count,status FROM sources WHERE 1=1", []
    if doctype:
        sql += " AND doctype=?"; args.append(doctype)
    if language:
        sql += " AND language=?"; args.append(language)
    sql += " ORDER BY created DESC LIMIT ?"; args.append(int(limit))
    rows = [dict(r) for r in conn.execute(sql, args)]
    return {"status": "ok", "count": len(rows), "sources": rows}


def op_corpus_show(ctx: Ctx, source_id: str) -> dict:
    conn = ctx.conn()
    src = get_source(conn, source_id)
    if not src:
        raise ToolError(2, f"unknown source: {source_id}")
    segs = [dict(r) for r in conn.execute(
        "SELECT id,ordinal,locator,page,section,start_ms,end_ms,lang,text FROM segments "
        "WHERE source_id=? ORDER BY ordinal", (source_id,))]
    versions = [dict(r) for r in conn.execute(
        "SELECT version_label,checksum,issued,supersedes,status FROM source_versions WHERE source_id=?",
        (source_id,))]
    return {"status": "ok", "source": src, "versions": versions, "segment_count": len(segs),
            "segments": segs[:10], "truncated": len(segs) > 10}


def op_corpus_install(ctx: Ctx, bundle: str, confirm: bool = False) -> dict:
    """Install a checksummed corpus bundle (zip with corpus-manifest.json)."""
    if not confirm:
        raise ToolError(3, "corpus bundle installation requires --confirm")
    if not os.path.exists(bundle):
        raise ToolError(2, f"bundle not found: {bundle}")
    if not zipfile.is_zipfile(bundle):
        raise ToolError(2, f"bundle is not a zip archive: {bundle}")
    ctx.ensure()
    with zipfile.ZipFile(bundle) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        for n in names:
            if os.path.isabs(n) or ".." in n.split("/"):
                raise ToolError(2, f"bundle contains unsafe path: {n}")
        if "corpus-manifest.json" not in names:
            raise ToolError(2, "bundle has no corpus-manifest.json")
        manifest = json.loads(zf.read("corpus-manifest.json"))
        problems = []
        for entry in manifest.get("files", []):
            if entry["path"] not in names:
                problems.append(f"missing file: {entry['path']}")
                continue
            if sha256_bytes(zf.read(entry["path"])) != entry.get("checksum"):
                problems.append(f"checksum mismatch: {entry['path']}")
        if problems:
            raise ToolError(2, "bundle verification failed", {"problems": problems})
        dest = ctx.path("corpus", f"installed-{slug(manifest.get('name', 'bundle'), 32)}")
        os.makedirs(dest, exist_ok=True)
        for n in names:
            if n == "corpus-manifest.json":
                continue
            target = os.path.join(dest, n)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(n) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
        write_json(os.path.join(dest, "corpus-manifest.json"), manifest)
    conn = ctx.conn()
    conn.execute("INSERT OR REPLACE INTO registry(kind,key,value,version,created) VALUES(?,?,?,?,?)",
                 ("corpora", manifest.get("name", os.path.basename(bundle)),
                  json.dumps({"path": os.path.relpath(dest, ctx.root), "files": len(names),
                              "license": manifest.get("license"), "signed": manifest.get("signature")},
                             ensure_ascii=False),
                  manifest.get("version", "1"), now_iso()))
    ctx.commit()
    audit_append(conn, ctx.actor, "corpus.install", bundle, {"installed_to": os.path.relpath(dest, ctx.root)})
    ctx.commit()
    return {"status": "ok", "installed_to": os.path.relpath(dest, ctx.root),
            "files": len([n for n in names if n != "corpus-manifest.json"]),
            "bundle_name": manifest.get("name"), "license": manifest.get("license")}


def op_corpus_package(ctx: Ctx, output: str, name=None, note=None) -> dict:
    """Package the current workspace corpus into a checksummed, redistributable bundle."""
    ctx.commit()
    name = name or "corpus-bundle"
    out = ctx.out_path(output)
    manifest = {"name": name, "version": "1", "created": now_iso(), "note": note or "",
                "license": None, "files": []}
    base = ctx.path("corpus")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, base).replace(os.sep, "/")
                zf.write(full, rel)
                manifest["files"].append({"path": rel, "checksum": sha256_file(full),
                                          "bytes": os.path.getsize(full)})
        if not manifest["files"]:
            raise ToolError(2, "workspace corpus/ is empty; ingest or install content first")
        licenses = {r["license"] for r in ctx.conn().execute("SELECT license FROM sources")}
        manifest["license"] = ",".join(sorted(x for x in licenses if x)) or None
        zf.writestr("corpus-manifest.json", json.dumps(manifest, indent=2))
    return {"status": "ok", "bundle": out, "files": len(manifest["files"]),
            "checksum": sha256_file(out), "bytes": os.path.getsize(out)}


def op_corpus_dedupe(ctx: Ctx, threshold: float = 0.85) -> dict:
    conn = ctx.conn()
    rows = [dict(r) for r in conn.execute("SELECT id,title,checksum,path FROM sources")]
    exact, near, shingle_cache = {}, [], {}
    for r in rows:
        exact.setdefault(r["checksum"], []).append(r["id"])
    exact_dupes = [{"checksum": k, "sources": v} for k, v in exact.items() if len(v) > 1]
    for r in rows:
        seg = conn.execute("SELECT text FROM segments WHERE source_id=? ORDER BY ordinal LIMIT 40",
                           (r["id"],)).fetchone()
        if seg:
            shingle_cache[r["id"]] = shingles(seg["text"], 5)
    ids = sorted(shingle_cache)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            score = jaccard(shingle_cache[ids[i]], shingle_cache[ids[j]])
            if score >= threshold:
                near.append({"a": ids[i], "b": ids[j], "similarity": round(score, 4)})
    near.sort(key=lambda x: -x["similarity"])
    return {"status": "ok", "sources": len(rows), "exact_duplicate_groups": exact_dupes,
            "near_duplicate_pairs": near[:100],
            "threshold": threshold, "near_pair_count": len(near)}


def op_corpus_integrity(ctx: Ctx) -> dict:
    conn = ctx.conn()
    max_mb = cfg_get(ctx.cfg, "limits.max_file_mb", 64)
    problems = []
    for r in conn.execute("SELECT id,path,checksum FROM sources"):
        if not r["path"]:
            problems.append({"kind": "missing_path", "source_id": r["id"]}); continue
        p = r["path"] if os.path.isabs(r["path"]) else ctx.path(r["path"])
        if not os.path.exists(p):
            problems.append({"kind": "missing_file", "source_id": r["id"], "path": r["path"]}); continue
        try:
            file_hash = sha256_file(p)
            text_hash = sha256_text(read_text(p, max_mb)) if os.path.splitext(p)[1].lower() in TEXT_SUFFIXES else None
            if r["checksum"] not in (file_hash, text_hash):
                problems.append({"kind": "hash_mismatch", "source_id": r["id"], "path": r["path"],
                                 "recorded": r["checksum"], "file": file_hash})
        except ToolError as exc:
            problems.append({"kind": "hash_error", "source_id": r["id"], "message": exc.message})
    orphans = [dict(r) for r in conn.execute(
        "SELECT id FROM segments WHERE source_id NOT IN (SELECT id FROM sources) LIMIT 50")]
    empty = [dict(r) for r in conn.execute("SELECT id FROM segments WHERE length(trim(text))=0 LIMIT 50")]
    missing_seg = [dict(r) for r in conn.execute(
        "SELECT id FROM sources WHERE id NOT IN (SELECT DISTINCT source_id FROM segments) LIMIT 50")]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    ok = not problems and not orphans and not empty and not missing_seg and integrity == "ok"
    return {"status": "ok" if ok else "issues", "database_integrity": integrity,
            "file_problems": problems, "orphan_segments": orphans,
            "empty_segments": empty, "sources_without_segments": missing_seg,
            "checked_sources": conn.execute("SELECT count(*) c FROM sources").fetchone()["c"]}


def op_corpus_repair_encoding(ctx: Ctx, input_path: str, output: str | None = None) -> dict:
    if not os.path.exists(input_path):
        raise ToolError(2, f"input not found: {input_path}")
    with open(input_path, "rb") as fh:
        data = fh.read()
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            decoded = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        decoded = data.decode("utf-8", errors="replace")
    fixed = normalize_text(decoded)
    marker_before = sum(decoded.count(m) for m in MOJIBAKE_MARKERS)
    marker_after = sum(fixed.count(m) for m in MOJIBAKE_MARKERS)
    out = ctx.out_path(output or f"repaired-{slug(os.path.basename(input_path), 40)}.txt")
    write_text(out, fixed)
    return {"status": "ok", "input": input_path, "output": out,
            "changed": fixed != decoded, "markers_before": marker_before,
            "remaining_mojibake_markers": marker_after}


def op_corpus_strip_boilerplate(ctx: Ctx, input_path: str, output: str | None = None) -> dict:
    raw = read_text(input_path)
    ext = os.path.splitext(input_path)[1].lower()
    cleaned = html_to_text(raw) if ext in (".html", ".htm", ".xml") else normalize_text(raw)
    out = ctx.out_path(output or f"clean-{slug(os.path.basename(input_path), 40)}.txt")
    write_text(out, cleaned)
    return {"status": "ok", "input": input_path, "output": out,
            "words_before": words(raw), "words_after": words(cleaned)}


def op_corpus_coverage(ctx: Ctx) -> dict:
    conn = ctx.conn()

    def group(col):
        return [dict(r) for r in conn.execute(
            f"SELECT COALESCE(NULLIF({col},''),'unspecified') k, count(*) n FROM sources GROUP BY k ORDER BY n DESC")]
    total = conn.execute("SELECT count(*) c FROM sources").fetchone()["c"]
    years = [(r["issued"] or "")[:4] for r in conn.execute("SELECT issued FROM sources")]
    spans = sorted(y for y in years if y.isdigit())
    gaps = []
    if total == 0:
        gaps.append("corpus is empty")
    langs = {r["k"] for r in group("language")}
    for want in cfg_get(ctx.cfg, "languages.required_for_coverage", ["en"]):
        if want not in langs:
            gaps.append(f"no sources in required language '{want}'")
    return {"status": "ok", "total_sources": total, "by_doctype": group("doctype"),
            "by_language": group("language"), "by_license": group("license"),
            "by_peer_status": group("peer_status"),
            "year_span": [spans[0], spans[-1]] if spans else None, "gaps": gaps}


def op_corpus_version_link(ctx: Ctx, source_id: str, version_label: str, supersedes: str | None = None,
                           note: str = "") -> dict:
    conn = ctx.conn()
    src = get_source(conn, source_id)
    if not src:
        raise ToolError(2, f"unknown source: {source_id}")
    if supersedes and not get_source(conn, supersedes):
        raise ToolError(2, f"superseded source not found: {supersedes}")
    checksum = src["checksum"]
    if supersedes:
        conn.execute("UPDATE source_versions SET status='superseded' WHERE source_id=?", (supersedes,))
        conn.execute("UPDATE sources SET status='superseded' WHERE id=?", (supersedes,))
        conn.execute("UPDATE source_versions SET supersedes=? WHERE source_id=? AND status='current'",
                     (supersedes, source_id))
    conn.execute("""INSERT INTO source_versions(source_id,version_label,path,checksum,issued,supersedes,status,note,created)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                 (source_id, version_label, src["path"], checksum, src["issued"], supersedes,
                  "current", note, now_iso()))
    conn.execute("UPDATE sources SET status='active' WHERE id=?", (source_id,))
    ctx.commit()
    audit_append(conn, ctx.actor, "corpus.version_link", source_id,
                 {"version": version_label, "supersedes": supersedes})
    ctx.commit()
    return {"status": "ok", "source_id": source_id, "version": version_label,
            "supersedes": supersedes,
            "chain": [dict(r) for r in conn.execute(
                "SELECT source_id,version_label,supersedes,status FROM source_versions WHERE source_id IN (?,?) "
                "ORDER BY id", (source_id, supersedes or source_id))]}


def op_corpus_update(ctx: Ctx, manifest_path: str, approved=False, approver=None, note="") -> dict:
    """Incremental corpus update from a JSON manifest: add / replace / delete operations."""
    if not os.path.exists(manifest_path):
        raise ToolError(2, f"update manifest not found: {manifest_path}")
    with open(manifest_path, encoding="utf-8") as fh:
        try:
            plan = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ToolError(2, f"update manifest is not valid JSON: {exc}")
    ops = plan.get("operations", [])
    if not isinstance(ops, list) or not ops:
        raise ToolError(2, "manifest must contain a non-empty 'operations' list")
    if any(o.get("action") == "delete" for o in ops):
        require_approval(ctx, "corpus.delete", os.path.basename(manifest_path), approved, approver, note)
    conn = ctx.conn()
    applied, failed = [], []
    for op in ops:
        action = op.get("action")
        try:
            if action in ("add", "replace"):
                res = op_corpus_ingest(ctx, op["path"], title=op.get("title"), doctype=op.get("doctype", "document"),
                                       language=op.get("language", "en"), license=op.get("license"),
                                       publisher=op.get("publisher"), issued=op.get("issued"),
                                       jurisdiction=op.get("jurisdiction"), copy=True)
                applied.append({"action": action, "result": res["ingested"][0]["source_id"]})
                if action == "replace":
                    for old in op.get("replaces", []):
                        conn.execute("UPDATE sources SET status='replaced' WHERE id=?", (old,))
            elif action == "delete":
                sid = op["source_id"]
                if not get_source(conn, sid):
                    raise ToolError(2, f"delete target not found: {sid}")
                conn.execute("DELETE FROM segments_fts WHERE source_id=?", (sid,))
                conn.execute("DELETE FROM segments WHERE source_id=?", (sid,))
                conn.execute("UPDATE sources SET status='deleted' WHERE id=?", (sid,))
                applied.append({"action": "delete", "source_id": sid})
            else:
                raise ToolError(2, f"unsupported action: {action}")
        except (ToolError, KeyError) as exc:
            failed.append({"action": action, "error": getattr(exc, "message", str(exc))})
    ctx.commit()
    audit_append(conn, ctx.actor, "corpus.update", manifest_path,
                 {"applied": len(applied), "failed": len(failed)})
    ctx.commit()
    return {"status": "ok" if not failed else "partial", "applied": applied, "failed": failed}


def op_corpus_snapshot(ctx: Ctx, note: str = "") -> dict:
    snap = snapshot_corpus(ctx, note)
    return {"status": "ok", **snap}


def op_corpus_rollback(ctx: Ctx, snapshot_id: str, approved=False, approver=None, note="") -> dict:
    require_approval(ctx, "corpus.rollback", snapshot_id, approved, approver, note)
    conn = ctx.conn()
    row = conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
    if not row:
        raise ToolError(2, f"unknown snapshot: {snapshot_id}")
    snap_path = row["path"] if os.path.isabs(row["path"]) else ctx.path(row["path"])
    if not os.path.exists(snap_path):
        raise ToolError(2, f"snapshot archive missing: {snap_path}")
    if sha256_file(snap_path) != row["checksum"]:
        raise ToolError(2, "snapshot archive checksum mismatch; refusing to restore")
    ctx.close()
    with zipfile.ZipFile(snap_path) as zf:
        names = zf.namelist()
        for n in names:
            if os.path.isabs(n) or ".." in n.split("/"):
                raise ToolError(2, f"snapshot contains unsafe path: {n}")
        zf.extractall(ctx.root)
    conn = ctx.conn()
    reindexed = reindex_fts(conn)
    conn.commit()
    audit_append(conn, ctx.actor, "corpus.rollback", snapshot_id, {"reindexed_segments": reindexed})
    ctx.commit()
    return {"status": "ok", "restored_snapshot": snapshot_id, "files": len(names),
            "reindexed_segments": reindexed}


def op_corpus_delete(ctx: Ctx, source_id: str, approved=False, approver=None, note="",
                     keep_file: bool = False) -> dict:
    """Remove a source, its segments, claims, citations and stored file (approval gated)."""
    conn = ctx.conn()
    src = get_source(conn, source_id)
    if not src:
        raise ToolError(2, f"unknown source: {source_id}")
    require_approval(ctx, "corpus.delete", source_id, approved, approver, note)
    seg_ids = [r["id"] for r in conn.execute("SELECT id FROM segments WHERE source_id=?", (source_id,))]
    claims = [r["id"] for r in conn.execute("SELECT id FROM claims WHERE source_id=?", (source_id,))]
    citations = [r["id"] for r in conn.execute("SELECT id FROM citations WHERE source_id=?", (source_id,))] \
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='citations'").fetchone() else []
    for table, ids in (("segments", seg_ids), ("claims", claims)):
        for item in ids:
            conn.execute(f"DELETE FROM {table} WHERE id=?", (item,))
    if citations:
        for item in citations:
            conn.execute("DELETE FROM citations WHERE id=?", (item,))
    try:
        conn.execute("DELETE FROM segments_fts WHERE source_id=?", (source_id,))
    except sqlite3.OperationalError:
        pass
    conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
    removed_file = None
    if not keep_file and src.get("path"):
        target = src["path"] if os.path.isabs(src["path"]) else ctx.path(src["path"])
        if os.path.exists(target):
            os.remove(target)
            removed_file = src["path"]
    conn.commit()
    audit_append(conn, ctx.actor, "corpus.delete", source_id,
                 {"segments": len(seg_ids), "claims": len(claims), "file": removed_file})
    ctx.commit()
    return {"status": "ok", "deleted_source": source_id, "segments_removed": len(seg_ids),
            "claims_removed": len(claims), "citations_removed": len(citations),
            "file_removed": removed_file, "approved_by": approver}


def op_corpus_structure(ctx: Ctx, source_id: str) -> dict:
    conn = ctx.conn()
    src = get_source(conn, source_id)
    if not src:
        raise ToolError(2, f"unknown source: {source_id}")
    path = src["path"] if os.path.isabs(src["path"] or "") else ctx.path(src["path"] or "")
    if not path or not os.path.exists(path):
        raise ToolError(2, f"source file not available for structure extraction: {src['path']}")
    text = read_text(path)
    headings = [{"level": len(m.group(1)), "text": m.group(2).strip()}
                for m in re.finditer(r"^(#{1,6})\s+(.+)$", text, re.M)]
    footnotes = [m.strip() for m in re.findall(r"^\s*(?:\[\d+\]|\d+\.)\s+(.{15,})$", text, re.M)][:50]
    captions = re.findall(r"^\s*(?:Table|Figure|Fig\.|Chart)\s+\d+[:.].*$", text, re.M | re.I)[:50]
    pages = len(re.findall(r"\f", text)) + 1
    return {"status": "ok", "source_id": source_id, "headings": headings[:50],
            "heading_count": len(headings), "footnotes": footnotes,
            "captions": [c.strip() for c in captions], "page_break_count": pages - 1,
            "word_count": words(text)}


def op_corpus_license(ctx: Ctx, source_id: str, license: str | None = None,
                      max_quote_words: int | None = None, redistribute: bool | None = None) -> dict:
    conn = ctx.conn()
    src = get_source(conn, source_id)
    if not src:
        raise ToolError(2, f"unknown source: {source_id}")
    meta = src["meta"]
    rights = meta.setdefault("rights", {})
    if license:
        conn.execute("UPDATE sources SET license=? WHERE id=?", (license, source_id))
    if max_quote_words is not None:
        rights["max_quote_words"] = int(max_quote_words)
    if redistribute is not None:
        rights["redistribute"] = bool(redistribute)
    conn.execute("UPDATE sources SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), source_id))
    ctx.commit()
    audit_append(conn, ctx.actor, "corpus.license", source_id,
                 {"license": license, "rights": rights})
    ctx.commit()
    return {"status": "ok", "source_id": source_id, "license": license or src["license"],
            "rights": rights}


def op_corpus_usage(ctx: Ctx) -> dict:
    """Licensed-content usage meter: words stored, quotes recorded, deletion status."""
    conn = ctx.conn()
    limits = cfg_get(ctx.cfg, "quoting", {}) or {}
    rows = []
    for r in conn.execute("SELECT id,title,license,word_count,meta,status FROM sources ORDER BY id"):
        meta = json.loads(r["meta"] or "{}")
        rights = meta.get("rights", {})
        rows.append({"source_id": r["id"], "license": r["license"], "status": r["status"],
                     "words_stored": r["word_count"],
                     "max_quote_words": rights.get("max_quote_words", limits.get("default_max_words")),
                     "redistribute": rights.get("redistribute", False)})
    total_words = sum(r["words_stored"] or 0 for r in rows)
    budget = cfg_get(ctx.cfg, "licensing.max_total_words", None)
    return {"status": "ok", "sources": len(rows), "total_words_stored": total_words,
            "storage_budget_words": budget,
            "budget_exceeded": bool(budget and total_words > budget), "entries": rows}


# ---------------------------------------------------------------- ops: registry / jobs / state

def op_registry_add(ctx: Ctx, kind: str, key: str, value: str, version: str = "1") -> dict:
    if kind not in ("models", "corpora", "prompts", "tools", "language-assets"):
        raise ToolError(2, f"unknown registry kind: {kind} (models|corpora|prompts|tools|language-assets)")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = {"note": value}
    conn = ctx.conn()
    conn.execute("INSERT OR REPLACE INTO registry(kind,key,value,version,created) VALUES(?,?,?,?,?)",
                 (kind, key, json.dumps(parsed, ensure_ascii=False), version, now_iso()))
    ctx.commit()
    return {"status": "ok", "kind": kind, "key": key, "version": version}


def op_registry_list(ctx: Ctx, kind: str) -> dict:
    conn = ctx.conn()
    rows = [{"key": r["key"], "version": r["version"], "value": json.loads(r["value"]),
             "created": r["created"]}
            for r in conn.execute("SELECT * FROM registry WHERE kind=? ORDER BY key,version", (kind,))]
    return {"status": "ok", "kind": kind, "count": len(rows), "entries": rows}


def op_jobs_add(ctx: Ctx, name: str, kind: str, due: str | None = None, payload: str = "{}") -> dict:
    allowed = {"integrity", "freshness-report", "backup", "reindex", "coverage"}
    if kind not in allowed:
        raise ToolError(2, f"unsupported job kind: {kind} ({', '.join(sorted(allowed))})")
    job_id = f"job-{slug(name, 30)}-{int(time.time())}"
    conn = ctx.conn()
    conn.execute("INSERT INTO jobs(id,name,kind,due,status,payload,attempts,created) VALUES(?,?,?,?,?,?,?,?)",
                 (job_id, name, kind, due or now_iso(), "pending", payload, 0, now_iso()))
    ctx.commit()
    return {"status": "ok", "job_id": job_id, "kind": kind, "due": due or now_iso()}


def op_jobs_list(ctx: Ctx, status: str | None = None) -> dict:
    conn = ctx.conn()
    sql, args = "SELECT * FROM jobs", []
    if status:
        sql += " WHERE status=?"; args.append(status)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY due LIMIT 200", args)]
    return {"status": "ok", "count": len(rows), "jobs": rows}


def op_jobs_run_due(ctx: Ctx, max_jobs: int = 10) -> dict:
    retries = cfg_get(ctx.cfg, "limits.retries", 1)
    conn = ctx.conn()
    due = [dict(r) for r in conn.execute(
        "SELECT * FROM jobs WHERE status='pending' AND due<=? ORDER BY due LIMIT ?",
        (now_iso(), max(1, min(int(max_jobs), 50))))]
    done = []
    for job in due:
        attempts = 0
        while attempts <= retries:
            attempts += 1
            try:
                if job["kind"] == "integrity":
                    result = op_corpus_integrity(ctx)
                elif job["kind"] == "coverage":
                    result = op_corpus_coverage(ctx)
                elif job["kind"] == "reindex":
                    result = {"reindexed": reindex_fts(conn)}
                elif job["kind"] == "backup":
                    result = snapshot_corpus(ctx, "scheduled backup")
                else:  # freshness-report
                    result = {"note": "freshness report requires tools-rwt-verify.py freshness; "
                                      "run verify freshness directly for scoring"}
                conn.execute("UPDATE jobs SET status='done',attempts=? WHERE id=?", (attempts, job["id"]))
                done.append({"job_id": job["id"], "kind": job["kind"], "status": "done",
                             "attempts": attempts, "result": result})
                break
            except Exception as exc:  # noqa: BLE001 - job failures must not abort the batch
                if attempts > retries:
                    conn.execute("UPDATE jobs SET status='failed',attempts=? WHERE id=?", (attempts, job["id"]))
                    done.append({"job_id": job["id"], "kind": job["kind"], "status": "failed",
                                 "attempts": attempts, "error": str(exc)})
        conn.commit()
    ctx.commit()
    return {"status": "ok", "ran": len(done), "jobs": done}


def op_state_save(ctx: Ctx, key: str, value: str) -> dict:
    conn = ctx.conn()
    conn.execute("INSERT OR REPLACE INTO state(key,value,updated) VALUES(?,?,?)", (key, value, now_iso()))
    ctx.commit()
    return {"status": "ok", "key": key, "bytes": len(value)}


def op_state_load(ctx: Ctx, key: str) -> dict:
    row = ctx.conn().execute("SELECT value,updated FROM state WHERE key=?", (key,)).fetchone()
    if not row:
        raise ToolError(2, f"no checkpoint stored under key: {key}")
    return {"status": "ok", "key": key, "updated": row["updated"], "value": row["value"]}


def op_state_checkpoint(ctx: Ctx, label: str) -> dict:
    snap = snapshot_corpus(ctx, f"checkpoint: {label}")
    op_state_save(ctx, f"checkpoint:{label}", json.dumps(snap))
    return {"status": "ok", "checkpoint": label, **snap}


def op_backup_restore_verify(ctx: Ctx, snapshot_id: str | None = None) -> dict:
    conn = ctx.conn()
    if snapshot_id:
        row = conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if not row:
            raise ToolError(2, f"unknown snapshot: {snapshot_id}")
        snap = {"snapshot_id": row["id"], "path": row["path"], "checksum": row["checksum"]}
    else:
        snap = snapshot_corpus(ctx, "backup restore verification")
    snap_path = snap["path"] if os.path.isabs(snap["path"]) else ctx.path(snap["path"])
    problems = []
    with tempfile.TemporaryDirectory(prefix="rwt-restore-") as tmp:
        with zipfile.ZipFile(snap_path) as zf:
            zf.extractall(tmp)
        manifest_path = os.path.join(tmp, "snapshot-manifest.json")
        if not os.path.exists(manifest_path):
            problems.append("snapshot-manifest.json missing")
        else:
            with open(manifest_path, encoding="utf-8") as fh:
                manifest = json.load(fh)
            for entry in manifest.get("files", []):
                target = os.path.join(tmp, entry["path"])
                if not os.path.exists(target):
                    problems.append(f"missing after restore: {entry['path']}")
                elif sha256_file(target) != entry["checksum"]:
                    problems.append(f"checksum mismatch after restore: {entry['path']}")
    return {"status": "ok" if not problems else "failed", "snapshot": snap["snapshot_id"],
            "problems": problems, "verified": not problems}


def op_mirror_list(ctx: Ctx, mirror_dir: str | None = None) -> dict:
    """Inventory a local offline dependency mirror (wheels, models, fonts, dictionaries)."""
    target = mirror_dir or cfg_get(ctx.cfg, "mirror.path", "mirror")
    if not os.path.isdir(target):
        return {"status": "ok", "mirror": target, "exists": False, "entries": [],
                "note": "create the mirror directory and place verified packages/models inside it"}
    entries = []
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            entries.append({"path": os.path.relpath(full, target).replace(os.sep, "/"),
                            "bytes": os.path.getsize(full), "checksum": sha256_file(full)})
    return {"status": "ok", "mirror": target, "exists": True, "count": len(entries),
            "entries": entries[:500]}


# ---------------------------------------------------------------- command registration

def register(group_parsers, common):
    def add(group_name, help_text):
        p = group_parsers[group_name]
        p.__doc__ = help_text
        return p

    g = add("corpus", "Corpus ingestion, installation, integrity, dedupe, licensing, snapshots")

    def sub(cmd, help_text, func, /, **opts):
        p = g.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)
        return p

    sub("ingest", "Ingest a text-bearing file or directory into the workspace corpus",
        op_corpus_ingest,
        input_path=(("--input", "-i"), dict(required=True)),
        title=(("--title",), dict(default=None)),
        doctype=(("--doctype",), dict(default="document")),
        language=(("--language", "--lang"), dict(default="en")),
        license=(("--license",), dict(default=None)),
        publisher=(("--publisher",), dict(default=None)),
        issued=(("--issued",), dict(default=None)),
        jurisdiction=(("--jurisdiction",), dict(default=None)),
        uri=(("--uri",), dict(default=None)),
        copy=(("--copy",), dict(action="store_true")),
        peer_status=(("--peer-status",), dict(default="unknown")),
        max_words=(("--max-words",), dict(type=int, default=None)))
    sub("list", "List corpus sources", op_corpus_list,
        doctype=(("--doctype",), dict(default=None)),
        language=(("--language", "--lang"), dict(default=None)),
        limit=(("--limit",), dict(type=int, default=50)))
    sub("show", "Show one source, its versions and first segments", op_corpus_show,
        source_id=(("--source-id", "--source"), dict(required=True)))
    sub("install", "Install a checksummed corpus bundle (zip + corpus-manifest.json)",
        op_corpus_install,
        bundle=(("--bundle",), dict(required=True)),
        confirm=(("--confirm",), dict(action="store_true")))
    sub("package", "Package the workspace corpus into a checksummed bundle", op_corpus_package,
        output=(("--output", "-o"), dict(required=True)),
        name=(("--name",), dict(default=None)),
        note=(("--note",), dict(default=None)))
    sub("dedupe", "Find exact and near-duplicate sources", op_corpus_dedupe,
        threshold=(("--threshold",), dict(type=float, default=0.85)))
    sub("integrity", "Audit files, hashes, database integrity and orphans", op_corpus_integrity)
    sub("repair-encoding", "Repair mojibake and normalize Unicode in a text file", op_corpus_repair_encoding,
        input_path=(("--input", "-i"), dict(required=True)),
        output=(("--output", "-o"), dict(default=None)))
    sub("strip-boilerplate", "Remove navigation/boilerplate and normalize to plain text",
        op_corpus_strip_boilerplate,
        input_path=(("--input", "-i"), dict(required=True)),
        output=(("--output", "-o"), dict(default=None)))
    sub("coverage", "Coverage audit by type, language, license and year", op_corpus_coverage)
    sub("version-link", "Link a newer version to the version it supersedes", op_corpus_version_link,
        source_id=(("--source-id", "--source"), dict(required=True)),
        version_label=(("--version-label", "--version"), dict(required=True)),
        supersedes=(("--supersedes",), dict(default=None)),
        note=(("--note",), dict(default="")))
    sub("update", "Apply an incremental add/replace/delete corpus manifest", op_corpus_update,
        manifest_path=(("--manifest", "-m"), dict(required=True)),
        approved=(("--approve",), dict(action="store_true")),
        approver=(("--approver",), dict(default=None)),
        note=(("--note",), dict(default="")))
    sub("delete", "Delete a source and its derived records (approval gated)", op_corpus_delete,
        source_id=(("--source-id", "--source"), dict(required=True)),
        approved=(("--approve",), dict(action="store_true")),
        approver=(("--approver",), dict(default=None)),
        note=(("--note",), dict(default="")),
        keep_file=(("--keep-file",), dict(action="store_true")))
    sub("snapshot", "Create a verifiable corpus snapshot", op_corpus_snapshot,
        note=(("--note",), dict(default="")))
    sub("rollback", "Restore a previous snapshot (approval gated)", op_corpus_rollback,
        snapshot_id=(("--snapshot-id", "--snapshot"), dict(required=True)),
        approved=(("--approve",), dict(action="store_true")),
        approver=(("--approver",), dict(default=None)),
        note=(("--note",), dict(default="")))
    sub("structure", "Extract headings, footnotes, captions and page breaks", op_corpus_structure,
        source_id=(("--source-id", "--source"), dict(required=True)))
    sub("license", "Set license and quotation rights for a source", op_corpus_license,
        source_id=(("--source-id", "--source"), dict(required=True)),
        license=(("--license",), dict(default=None)),
        max_quote_words=(("--max-quote-words",), dict(type=int, default=None)),
        redistribute=(("--redistribute",), dict(action="store_true", default=None)))
    sub("usage", "Licensed-content usage meter", op_corpus_usage)

    r = add("registry", "Model, corpus, prompt and tool registries")
    def rsub(cmd, help_text, func, /, **opts):
        p = r.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)
    rsub("add", "Register or update a registry entry", op_registry_add,
         kind=(("--kind",), dict(required=True)),
         key=(("--key",), dict(required=True)),
         value=(("--value",), dict(required=True)),
         version=(("--version",), dict(default="1")))
    rsub("list", "List registry entries", op_registry_list,
         kind=(("--kind",), dict(required=True)))

    j = add("jobs", "Bounded local job scheduler")
    def jsub(cmd, help_text, func, /, **opts):
        p = j.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)
    jsub("add", "Queue a scheduled job (integrity|coverage|reindex|backup|freshness-report)",
         op_jobs_add,
         name=(("--name",), dict(required=True)), kind=(("--kind",), dict(required=True)),
         due=(("--due",), dict(default=None)), payload=(("--payload",), dict(default="{}")))
    jsub("list", "List queued/running jobs", op_jobs_list, status=(("--status",), dict(default=None)))
    jsub("run-due", "Run due jobs with retry and per-job failure isolation", op_jobs_run_due,
         max_jobs=(("--max-jobs",), dict(type=int, default=10)))

    s = add("state", "Durable research state and checkpoints")
    def ssub(cmd, help_text, func, /, **opts):
        p = s.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)
    ssub("save", "Save a named state blob", op_state_save,
         key=(("--key",), dict(required=True)), value=(("--value",), dict(required=True)))
    ssub("load", "Load a named state blob", op_state_load, key=(("--key",), dict(required=True)))
    ssub("checkpoint", "Snapshot the workspace and record a checkpoint", op_state_checkpoint,
         label=(("--label",), dict(required=True)))

    b = add("backup", "Backup and restore verification")
    def bsub(cmd, help_text, func, /, **opts):
        p = b.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)
    bsub("restore-verify", "Extract a snapshot to a temp dir and verify every checksum",
         op_backup_restore_verify, snapshot_id=(("--snapshot-id",), dict(default=None)))
    bsub("mirror", "Inventory a local offline dependency mirror", op_mirror_list,
         mirror_dir=(("--mirror-dir",), dict(default=None)))
