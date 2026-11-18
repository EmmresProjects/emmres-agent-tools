#!/usr/bin/env python3
"""Core foundation for offline-agents-automation: config, schemas, SQLite WAL store, crypto, RBAC/approvals, embeddings, and audit chain."""
import sys
sys.dont_write_bytecode = True

import base64
import datetime
import hashlib
import hmac
import html
import importlib.util
import json
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

TOOL_FOLDER_NAME = "offline-agents-automation"
SKILL_TOOL_DIR = Path(__file__).resolve().parent
_LOADED_MODULES: Dict[str, Any] = {}


def load_tool_module(module_filename: str) -> Any:
    """Load a sibling tools-*.py file without generating bytecode caches."""
    sys.dont_write_bytecode = True
    if module_filename in _LOADED_MODULES:
        return _LOADED_MODULES[module_filename]
    target = SKILL_TOOL_DIR / module_filename
    if not target.is_file():
        raise FileNotFoundError(f"Required tool module missing: {TOOL_FOLDER_NAME}/{module_filename}")
    mod_name = "oaa_" + re.sub(r"[^a-z0-9]+", "_", module_filename.lower())
    spec = importlib.util.spec_from_file_location(mod_name, str(target))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to create module spec for {TOOL_FOLDER_NAME}/{module_filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _LOADED_MODULES[module_filename] = module
    return module


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_utc(ts: str) -> datetime.datetime:
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def sha256_hex(data: Any) -> str:
    if isinstance(data, bytes):
        raw = data
    elif isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        raw = canonical_json(data).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def resolve_resource_path(rel_path: str) -> Path:
    """Resolve a future-skill-root relative path without parent traversal."""
    norm = rel_path.replace("\\", "/").strip()
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or norm.split("/").count("..") > 0:
        raise ValueError(f"Unsafe or non-relative resource path rejected: {rel_path}")
    candidate_cwd = Path(norm)
    if candidate_cwd.exists():
        return candidate_cwd
    prefix = f"{TOOL_FOLDER_NAME}/"
    if norm.startswith(prefix):
        inner = norm[len(prefix):]
        candidate_pkg = SKILL_TOOL_DIR / inner
        if candidate_pkg.exists():
            return candidate_pkg
    candidate_direct = SKILL_TOOL_DIR / norm
    if candidate_direct.exists():
        return candidate_direct
    return candidate_cwd


def validate_safe_user_path(path_str: str, allow_outside_cwd: bool = True) -> Path:
    """Validate configurable input/output path, rejecting null bytes and parent traversal tokens."""
    if not path_str or "\x00" in path_str:
        raise ValueError("Invalid empty or null-byte file path.")
    parts = Path(path_str).parts
    if ".." in parts:
        raise ValueError(f"Parent-directory traversal ('..') is forbidden in path: {path_str}")
    p = Path(path_str)
    if not allow_outside_cwd and p.is_absolute():
        raise ValueError(f"Absolute path disallowed by current sandbox policy: {path_str}")
    return p


def load_json_resource(rel_path: str) -> Dict[str, Any]:
    path = resolve_resource_path(rel_path)
    if not path.is_file():
        raise FileNotFoundError(f"Resource file not found: {rel_path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in resource: {rel_path}")
    return data


def load_runtime_bundle(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load default config plus security policy, schemas, prompts, workflows, and reference corpus."""
    cfg_rel = config_path or f"{TOOL_FOLDER_NAME}/asset-default-config.json"
    if config_path:
        cfg_file = validate_safe_user_path(config_path)
        if not cfg_file.is_file():
            cfg_file = resolve_resource_path(config_path)
        with cfg_file.open("r", encoding="utf-8") as fh:
            config = json.load(fh)
    else:
        config = load_json_resource(cfg_rel)

    res = config.get("resources", {})
    security_policy = load_json_resource(res.get("security_policy_path", f"{TOOL_FOLDER_NAME}/asset-security-policy.json"))
    schemas = load_json_resource(res.get("schemas_path", f"{TOOL_FOLDER_NAME}/asset-schemas.json"))
    prompts = load_json_resource(res.get("prompt_templates_path", f"{TOOL_FOLDER_NAME}/asset-prompt-templates.json"))
    workflows = load_json_resource(res.get("workflow_presets_path", f"{TOOL_FOLDER_NAME}/asset-workflow-presets.json"))
    corpus = load_json_resource(res.get("reference_corpus_path", f"{TOOL_FOLDER_NAME}/asset-reference-corpus-and-benchmarks.json"))
    return {
        "config": config,
        "security_policy": security_policy,
        "schemas": schemas,
        "prompts": prompts,
        "workflows": workflows,
        "corpus": corpus,
    }


def validate_json_schema(instance: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Deterministic recursive JSON Schema validator supporting core draft keywords."""
    errors: List[str] = []
    expected_type = schema.get("type")
    if expected_type:
        type_ok = True
        if expected_type == "object":
            type_ok = isinstance(instance, dict)
        elif expected_type == "array":
            type_ok = isinstance(instance, list)
        elif expected_type == "string":
            type_ok = isinstance(instance, str)
        elif expected_type == "boolean":
            type_ok = isinstance(instance, bool)
        elif expected_type == "integer":
            type_ok = isinstance(instance, int) and not isinstance(instance, bool)
        elif expected_type == "number":
            type_ok = isinstance(instance, (int, float)) and not isinstance(instance, bool)
        if not type_ok:
            errors.append(f"{path}: expected type '{expected_type}', got '{type(instance).__name__}'")
            return errors

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} not in allowed enum {schema['enum']}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            errors.append(f"{path}: string shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > int(schema["maxLength"]):
            errors.append(f"{path}: string exceeds maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: string does not match pattern {schema['pattern']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: value {instance} below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: value {instance} above maximum {schema['maximum']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < int(schema["minItems"]):
            errors.append(f"{path}: array has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > int(schema["maxItems"]):
            errors.append(f"{path}: array exceeds {schema['maxItems']} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, elem in enumerate(instance):
                errors.extend(validate_json_schema(elem, item_schema, f"{path}[{idx}]"))

    if isinstance(instance, dict):
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: missing required property '{req}'")
        props = schema.get("properties", {})
        for k, v in instance.items():
            if k in props and isinstance(props[k], dict):
                errors.extend(validate_json_schema(v, props[k], f"{path}.{k}"))

    return errors


def slugify_name(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "unnamed-skill-tool"


def parse_capability_catalog_text(raw_text: str) -> Dict[str, Any]:
    """
    Parse plain text, Markdown, or copied HTML containing a single capability, numbered list,
    or multi-category catalog. Strips HTML tags, CSS/script blocks, data attributes, numbering, and spacing.
    """
    cleaned = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", raw_text)
    cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|section|article)>", "\n", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)

    categories: List[Dict[str, Any]] = []
    current_cat: Optional[Dict[str, Any]] = None
    current_skill: Optional[Dict[str, Any]] = None

    for raw_line in cleaned.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        line_no_md = re.sub(r"^[#>*•\-]+\s*", "", line).strip()
        if not line_no_md:
            continue

        m_skill_explicit = re.match(
            r"^(?:\(?\d+[\.\)]\s*)?(?:Skill(?:\s+Tool)?\s+Name|Capability|Tool\s+Name)\s*:\s*(.+)$",
            line_no_md,
            flags=re.IGNORECASE,
        )
        if m_skill_explicit:
            if current_skill is not None:
                if current_cat is None:
                    current_cat = {"category_name": "General Capabilities", "category_slug": "general-capabilities", "skills": []}
                    categories.append(current_cat)
                current_cat["skills"].append(current_skill)
            sname = re.sub(r"\*+", "", m_skill_explicit.group(1)).strip()
            current_skill = {"name": sname, "slug": slugify_name(sname), "description": ""}
            continue

        m_desc = re.match(r"^Description\s*:\s*(.+)$", line_no_md, flags=re.IGNORECASE)
        if m_desc and current_skill is not None:
            current_skill["description"] = m_desc.group(1).strip()
            continue

        m_inline = re.match(r"^(?:\(?\d+[\.\)]\s*)?\*\*([^*]+)\*\*\s*[:\-–]\s*(.+)$", line.strip())
        if not m_inline:
            m_inline = re.match(r"^(?:\(?\d+[\.\)]\s*)([A-Z][A-Za-z0-9 /&,\-]{2,65})\s*:\s*(.+)$", line_no_md)
        if m_inline and not line_no_md.lower().startswith("category:"):
            if current_skill is not None:
                if current_cat is None:
                    current_cat = {"category_name": "General Capabilities", "category_slug": "general-capabilities", "skills": []}
                    categories.append(current_cat)
                current_cat["skills"].append(current_skill)
                current_skill = None
            sname = m_inline.group(1).strip()
            sdesc = m_inline.group(2).strip()
            if current_cat is None:
                current_cat = {"category_name": "General Capabilities", "category_slug": "general-capabilities", "skills": []}
                categories.append(current_cat)
            current_cat["skills"].append({"name": sname, "slug": slugify_name(sname), "description": sdesc})
            continue

        m_cat = re.match(r"^(?:Category\s*:\s*|\d+\.\s+)([A-Z][A-Za-z0-9 ,/&\-]{3,90})$", line_no_md)
        if m_cat or line.strip().startswith("#"):
            cat_title = m_cat.group(1).strip() if m_cat else re.sub(r"^#+\s*(?:\d+\.\s*)?", "", line.strip()).strip()
            if cat_title:
                if current_skill is not None:
                    if current_cat is None:
                        current_cat = {"category_name": "General Capabilities", "category_slug": "general-capabilities", "skills": []}
                        categories.append(current_cat)
                    current_cat["skills"].append(current_skill)
                    current_skill = None
                current_cat = {"category_name": cat_title, "category_slug": slugify_name(cat_title), "skills": []}
                categories.append(current_cat)
                continue

        if current_skill is not None:
            current_skill["description"] = (current_skill["description"] + " " + line_no_md).strip()
        else:
            # Single-capability plain text fallback
            current_skill = {
                "name": line_no_md[:60].strip(" .:-"),
                "slug": slugify_name(line_no_md[:60]),
                "description": line_no_md,
            }

    if current_skill is not None:
        if current_cat is None:
            current_cat = {"category_name": "General Capabilities", "category_slug": "general-capabilities", "skills": []}
            categories.append(current_cat)
        current_cat["skills"].append(current_skill)

    categories = [c for c in categories if c["skills"]]
    total_skills = sum(len(c["skills"]) for c in categories)
    if len(categories) == 1 and categories[0]["category_slug"] != "general-capabilities":
        umbrella = categories[0]["category_slug"]
    elif len(categories) > 1:
        umbrella = slugify_name(categories[0]["category_name"].split(" and ")[0] + "-suite")
    elif total_skills == 1:
        umbrella = categories[0]["skills"][0]["slug"]
    else:
        umbrella = "unified-capability-tool"

    return {
        "umbrella_name": umbrella,
        "category_count": len(categories),
        "capability_count": total_skills,
        "categories": categories,
    }


class OfflineEmbedder:
    """Deterministic offline dense feature hashing embedder with word + character n-grams."""

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = max(16, int(dimensions))

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dimensions
        norm_text = re.sub(r"\s+", " ", text.lower()).strip()
        if not norm_text:
            return vec
        words = re.findall(r"[a-z0-9]+", norm_text)
        features: List[Tuple[str, float]] = []
        for w in words:
            features.append((f"w:{w}", 2.0))
            if len(w) > 4:
                features.append((f"stem:{w[:4]}", 1.0))
        for i in range(len(words) - 1):
            features.append((f"bg:{words[i]}_{words[i+1]}", 1.5))
        compact = "".join(words)
        for i in range(max(0, len(compact) - 3)):
            features.append((f"c4:{compact[i:i+4]}", 0.35))

        for feat, weight in features:
            digest = hashlib.sha256(feat.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            vec[idx] += sign * weight

        mag = math.sqrt(sum(v * v for v in vec))
        if mag > 0:
            vec = [round(v / mag, 6) for v in vec]
        return vec


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    na = math.sqrt(sum(a * a for a in vec_a))
    nb = math.sqrt(sum(b * b for b in vec_b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return round(dot / (na * nb), 6)


def tokenize_terms(text: str) -> List[str]:
    stop = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "into", "what", "how", "when"}
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1 and t not in stop]


def bm25_scores(query: str, docs: List[str], k1: float = 1.5, b: float = 0.75) -> List[float]:
    if not docs:
        return []
    q_terms = tokenize_terms(query)
    if not q_terms:
        return [0.0] * len(docs)
    tokenized_docs = [tokenize_terms(d) for d in docs]
    avgdl = max(1.0, sum(len(td) for td in tokenized_docs) / len(tokenized_docs))
    n_docs = len(docs)
    df: Dict[str, int] = {}
    for term in set(q_terms):
        df[term] = sum(1 for td in tokenized_docs if term in td)

    scores: List[float] = []
    for td in tokenized_docs:
        dl = len(td)
        tf_map: Dict[str, int] = {}
        for tok in td:
            tf_map[tok] = tf_map.get(tok, 0) + 1
        s = 0.0
        for term in q_terms:
            n_q = df.get(term, 0)
            if n_q == 0:
                continue
            idf = math.log(1.0 + (n_docs - n_q + 0.5) / (n_q + 0.5))
            tf = tf_map.get(term, 0)
            if tf > 0:
                denom = tf + k1 * (1.0 - b + b * (dl / avgdl))
                s += idf * ((tf * (k1 + 1.0)) / denom)
        scores.append(round(s, 6))
    return scores


def reciprocal_rank_fusion(rank_lists: List[List[str]], k: int = 60) -> List[Tuple[str, float]]:
    fused: Dict[str, float] = {}
    for rlist in rank_lists:
        for rank_idx, doc_id in enumerate(rlist, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank_idx)
    ordered = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(doc_id, round(score, 6)) for doc_id, score in ordered]


def _keystream_bytes(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(enc_key + nonce + counter.to_bytes(8, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt_secret_blob(plaintext: str, passphrase: str) -> Dict[str, str]:
    """Authenticated offline encryption using PBKDF2-HMAC-SHA256 + SHA256-CTR + HMAC-SHA256 (Encrypt-then-MAC)."""
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    master = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 120_000, dklen=64)
    enc_key, mac_key = master[:32], master[32:]
    pt_bytes = plaintext.encode("utf-8")
    ks = _keystream_bytes(enc_key, nonce, len(pt_bytes))
    ciphertext = bytes(a ^ b for a, b in zip(pt_bytes, ks))
    mac = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).hexdigest()
    return {
        "ciphertext_b64": base64.b64encode(nonce + ciphertext).decode("ascii"),
        "salt_hex": salt.hex(),
        "mac_hex": mac,
    }


def decrypt_secret_blob(ciphertext_b64: str, salt_hex: str, mac_hex: str, passphrase: str) -> str:
    salt = bytes.fromhex(salt_hex)
    raw = base64.b64decode(ciphertext_b64.encode("ascii"))
    nonce, ciphertext = raw[:16], raw[16:]
    master = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 120_000, dklen=64)
    enc_key, mac_key = master[:32], master[32:]
    expected_mac = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_mac, mac_hex):
        raise PermissionError("Secret vault MAC verification failed: wrong passphrase or tampered ciphertext.")
    ks = _keystream_bytes(enc_key, nonce, len(ciphertext))
    pt_bytes = bytes(a ^ b for a, b in zip(ciphertext, ks))
    return pt_bytes.decode("utf-8")


def redact_sensitive_text(text: str, security_policy: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Redact secrets and PII patterns before persistence or prompt assembly."""
    redacted = text
    triggered: List[str] = []
    for rule in security_policy.get("secret_redaction_patterns", []):
        pat = rule.get("pattern", "")
        name = rule.get("name", "secret")
        if pat and re.search(pat, redacted):
            triggered.append(name)
            redacted = re.sub(pat, f"[REDACTED:{name}]", redacted)
    return redacted, triggered


def issue_capability_token(
    signing_key: str,
    principal: str,
    capability: str,
    scope: str,
    ttl_seconds: int = 300,
) -> Dict[str, Any]:
    now_dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    exp_dt = now_dt + datetime.timedelta(seconds=max(1, int(ttl_seconds)))
    claims = {
        "jti": uuid.uuid4().hex[:16],
        "sub": principal,
        "cap": capability,
        "scope": scope,
        "iat": now_dt.isoformat().replace("+00:00", "Z"),
        "exp": exp_dt.isoformat().replace("+00:00", "Z"),
    }
    payload_b64 = base64.urlsafe_b64encode(canonical_json(claims).encode("utf-8")).decode("ascii")
    sig = hmac.new(signing_key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return {"token": f"{payload_b64}.{sig}", "claims": claims}


def verify_capability_token(token: str, signing_key: str, expected_capability: Optional[str] = None) -> Dict[str, Any]:
    if "." not in token:
        raise PermissionError("Malformed capability token.")
    payload_b64, sig = token.split(".", 1)
    expected_sig = hmac.new(signing_key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        raise PermissionError("Capability token signature invalid.")
    claims = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8"))
    if parse_iso_utc(claims["exp"]) < datetime.datetime.now(datetime.timezone.utc):
        raise PermissionError("Capability token has expired.")
    if expected_capability and claims.get("cap") not in (expected_capability, "*"):
        raise PermissionError(f"Token capability '{claims.get('cap')}' does not match '{expected_capability}'.")
    return claims


class OfflineStateStore:
    """Durable SQLite WAL database managing registries, vectors, memory, workflows, queues, approvals, and tamper-evident audit logs."""

    def __init__(self, state_dir: Path, config: Dict[str, Any], security_policy: Dict[str, Any]) -> None:
        self.state_dir = state_dir
        self.config = config
        self.security_policy = security_policy
        rt = config.get("runtime", {})
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir = self.state_dir / rt.get("quarantine_dirname", "quarantine")
        self.artifacts_dir = self.state_dir / rt.get("artifacts_dirname", "artifacts")
        self.backups_dir = self.state_dir / rt.get("backups_dirname", "backups")
        self.workspaces_dir = self.state_dir / rt.get("workspaces_dirname", "ephemeral-workspaces")
        for sub in (self.quarantine_dir, self.artifacts_dir, self.backups_dir, self.workspaces_dir):
            sub.mkdir(parents=True, exist_ok=True)

        self.db_path = self.state_dir / rt.get("sqlite_filename", "offline-agent-store.sqlite3")
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kv_registries (
                    namespace TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    version TEXT NOT NULL DEFAULT '1.0.0',
                    status TEXT NOT NULL DEFAULT 'active',
                    owner TEXT NOT NULL DEFAULT 'system',
                    payload_json TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    updated_utc TEXT NOT NULL,
                    PRIMARY KEY (namespace, item_key, version)
                );

                CREATE TABLE IF NOT EXISTS documents_and_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    version TEXT NOT NULL,
                    published_date TEXT NOT NULL,
                    authority REAL NOT NULL DEFAULT 0.8,
                    classification TEXT NOT NULL DEFAULT 'internal',
                    project TEXT NOT NULL DEFAULT 'default',
                    approved INTEGER NOT NULL DEFAULT 1,
                    quarantined INTEGER NOT NULL DEFAULT 0,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge_graph_edges (
                    edge_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    object TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 1,
                    confidence REAL NOT NULL DEFAULT 0.9,
                    created_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS durable_memory (
                    memory_id TEXT PRIMARY KEY,
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    memory_class TEXT NOT NULL,
                    key_topic TEXT NOT NULL,
                    content TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_id TEXT NOT NULL,
                    author TEXT NOT NULL,
                    classification TEXT NOT NULL DEFAULT 'internal',
                    ttl_expiry_utc TEXT,
                    legal_hold INTEGER NOT NULL DEFAULT 0,
                    retracted INTEGER NOT NULL DEFAULT 0,
                    retraction_reason TEXT,
                    vector_json TEXT NOT NULL,
                    created_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_tombstones (
                    content_sha256 TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    retracted_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_runs_and_checkpoints (
                    run_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    current_state TEXT NOT NULL,
                    step_index INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    principal TEXT NOT NULL DEFAULT 'operator',
                    plan_json TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL,
                    side_effects_json TEXT NOT NULL,
                    tokens_used INTEGER NOT NULL DEFAULT 0,
                    tool_calls_used INTEGER NOT NULL DEFAULT 0,
                    cpu_seconds_used REAL NOT NULL DEFAULT 0.0,
                    updated_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS idempotency_ledger (
                    idempotency_key TEXT PRIMARY KEY,
                    capability TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs_and_queues (
                    job_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 50,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    depends_on_json TEXT NOT NULL DEFAULT '[]',
                    worker_id TEXT,
                    lease_expires_utc TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    dead_letter_reason TEXT,
                    scheduled_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    capability TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    requester TEXT NOT NULL,
                    status TEXT NOT NULL,
                    required_approvals INTEGER NOT NULL,
                    decisions_json TEXT NOT NULL,
                    conditions_json TEXT NOT NULL,
                    expires_utc TEXT NOT NULL,
                    created_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS secret_vault (
                    secret_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    ciphertext_b64 TEXT NOT NULL,
                    salt_hex TEXT NOT NULL,
                    mac_hex TEXT NOT NULL,
                    rotated_from INTEGER,
                    active INTEGER NOT NULL DEFAULT 1,
                    updated_utc TEXT NOT NULL,
                    PRIMARY KEY (secret_id, version)
                );

                CREATE TABLE IF NOT EXISTS circuit_breakers (
                    target_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL DEFAULT 'closed',
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    opened_utc TEXT,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    seq_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    timestamp_utc TEXT NOT NULL,
                    monotonic_ns INTEGER NOT NULL,
                    principal TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    category_id INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    summary_redacted TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                """
            )

    def put_registry_item(
        self,
        namespace: str,
        item_key: str,
        payload: Dict[str, Any],
        version: str = "1.0.0",
        status: str = "active",
        owner: str = "operator",
    ) -> Dict[str, Any]:
        p_json = canonical_json(payload)
        digest = sha256_hex(p_json)
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO kv_registries (namespace, item_key, version, status, owner, payload_json, sha256, updated_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, item_key, version) DO UPDATE SET
                    status=excluded.status,
                    owner=excluded.owner,
                    payload_json=excluded.payload_json,
                    sha256=excluded.sha256,
                    updated_utc=excluded.updated_utc
                """,
                (namespace, item_key, version, status, owner, p_json, digest, now),
            )
        return {"namespace": namespace, "item_key": item_key, "version": version, "status": status, "sha256": digest, "updated_utc": now}

    def list_registry_items(self, namespace: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM kv_registries WHERE namespace=? AND status=? ORDER BY item_key, version",
                    (namespace, status),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM kv_registries WHERE namespace=? ORDER BY item_key, version",
                    (namespace,),
                ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "namespace": r["namespace"],
                    "item_key": r["item_key"],
                    "version": r["version"],
                    "status": r["status"],
                    "owner": r["owner"],
                    "payload": json.loads(r["payload_json"]),
                    "sha256": r["sha256"],
                    "updated_utc": r["updated_utc"],
                }
            )
        return out

    def append_audit_event(
        self,
        trace_id: str,
        principal: str,
        capability: str,
        category_id: int,
        decision: str,
        summary: str,
        details: Dict[str, Any],
        parent_span_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        span_id = uuid.uuid4().hex[:12]
        now = utc_now_iso()
        mono = time.monotonic_ns()
        redacted_summary, _ = redact_sensitive_text(summary, self.security_policy)
        redacted_details_str, redactions = redact_sensitive_text(canonical_json(details), self.security_policy)

        with self.connect() as conn:
            last = conn.execute("SELECT event_hash FROM audit_log ORDER BY seq_id DESC LIMIT 1").fetchone()
            prev_hash = last["event_hash"] if last else ("0" * 64)
            chain_material = f"{prev_hash}|{trace_id}|{span_id}|{now}|{principal}|{capability}|{category_id}|{decision}|{redacted_summary}|{redacted_details_str}"
            event_hash = sha256_hex(chain_material)
            cur = conn.execute(
                """
                INSERT INTO audit_log
                (trace_id, span_id, parent_span_id, timestamp_utc, monotonic_ns, principal, capability, category_id, decision, prev_hash, event_hash, summary_redacted, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    span_id,
                    parent_span_id,
                    now,
                    mono,
                    principal,
                    capability,
                    category_id,
                    decision,
                    prev_hash,
                    event_hash,
                    redacted_summary,
                    redacted_details_str,
                ),
            )
            seq_id = cur.lastrowid
        return {
            "seq_id": seq_id,
            "trace_id": trace_id,
            "span_id": span_id,
            "prev_hash": prev_hash,
            "event_hash": event_hash,
            "redactions_applied": redactions,
            "timestamp_utc": now,
        }

    def verify_audit_chain(self) -> Dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY seq_id ASC").fetchall()
        prev_hash = "0" * 64
        broken_at: Optional[int] = None
        for r in rows:
            if r["prev_hash"] != prev_hash:
                broken_at = int(r["seq_id"])
                break
            expected = sha256_hex(
                f"{prev_hash}|{r['trace_id']}|{r['span_id']}|{r['timestamp_utc']}|{r['principal']}|{r['capability']}|{r['category_id']}|{r['decision']}|{r['summary_redacted']}|{r['details_json']}"
            )
            if expected != r["event_hash"]:
                broken_at = int(r["seq_id"])
                break
            prev_hash = r["event_hash"]
        return {
            "chain_valid": broken_at is None,
            "checked_events": len(rows),
            "head_hash": prev_hash,
            "first_corrupted_seq_id": broken_at,
        }

    def create_approval_request(
        self,
        capability: str,
        payload_sha256: str,
        risk_class: str,
        requester: str,
        conditions: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        req_count = 2 if risk_class == "dual-control" else 1
        ttl = ttl_seconds or int(self.config.get("limits_and_recovery", {}).get("approval_ttl_seconds", 3600))
        now_dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        exp_dt = now_dt + datetime.timedelta(seconds=max(60, ttl))
        approval_id = "apr-" + uuid.uuid4().hex[:12]
        rec = {
            "approval_id": approval_id,
            "capability": capability,
            "payload_sha256": payload_sha256,
            "risk_class": risk_class,
            "requester": requester,
            "status": "pending",
            "required_approvals": req_count,
            "decisions": [],
            "conditions": conditions or {},
            "expires_utc": exp_dt.isoformat().replace("+00:00", "Z"),
            "created_utc": now_dt.isoformat().replace("+00:00", "Z"),
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO approvals
                (approval_id, capability, payload_sha256, risk_class, requester, status, required_approvals, decisions_json, conditions_json, expires_utc, created_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    capability,
                    payload_sha256,
                    risk_class,
                    requester,
                    "pending",
                    req_count,
                    "[]",
                    canonical_json(rec["conditions"]),
                    rec["expires_utc"],
                    rec["created_utc"],
                ),
            )
        return rec

    def record_approval_decision(
        self,
        approval_id: str,
        reviewer: str,
        reviewer_role: str,
        decision: str,
        rationale: str = "",
    ) -> Dict[str, Any]:
        roles_cfg = self.security_policy.get("roles", {})
        role_info = roles_cfg.get(reviewer_role, {})
        if not role_info.get("can_approve", False):
            raise PermissionError(f"Role '{reviewer_role}' is not authorized to approve consequential operations.")
        if decision not in ("approved", "denied", "revoked"):
            raise ValueError("Decision must be one of: approved, denied, revoked.")

        with self.connect() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if not row:
                raise KeyError(f"Approval request not found: {approval_id}")
            if parse_iso_utc(row["expires_utc"]) < datetime.datetime.now(datetime.timezone.utc):
                conn.execute("UPDATE approvals SET status='expired' WHERE approval_id=?", (approval_id,))
                raise PermissionError(f"Approval request '{approval_id}' has expired.")

            if self.security_policy.get("approval_rules", {}).get("separation_of_duties", True):
                if reviewer == row["requester"] and decision == "approved":
                    raise PermissionError("Separation-of-duties violation: requester cannot self-approve.")

            decisions: List[Dict[str, Any]] = json.loads(row["decisions_json"])
            if any(d["reviewer"] == reviewer and d["decision"] == "approved" for d in decisions) and decision == "approved":
                raise PermissionError(f"Reviewer '{reviewer}' has already recorded an approval on '{approval_id}'.")

            decisions.append(
                {
                    "reviewer": reviewer,
                    "role": reviewer_role,
                    "decision": decision,
                    "rationale": rationale,
                    "timestamp_utc": utc_now_iso(),
                }
            )
            if decision in ("denied", "revoked"):
                new_status = decision
            else:
                approved_count = sum(1 for d in decisions if d["decision"] == "approved")
                new_status = "approved" if approved_count >= int(row["required_approvals"]) else "pending"

            conn.execute(
                "UPDATE approvals SET status=?, decisions_json=? WHERE approval_id=?",
                (new_status, canonical_json(decisions), approval_id),
            )
        return {
            "approval_id": approval_id,
            "status": new_status,
            "required_approvals": int(row["required_approvals"]),
            "decisions": decisions,
        }

    def verify_approval_for_execution(
        self,
        approval_id: str,
        capability: str,
        payload_sha256: str,
    ) -> Dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if not row:
            raise PermissionError(f"Approval ID '{approval_id}' does not exist.")
        if row["status"] != "approved":
            raise PermissionError(f"Approval ID '{approval_id}' is in status '{row['status']}', not 'approved'.")
        if parse_iso_utc(row["expires_utc"]) < datetime.datetime.now(datetime.timezone.utc):
            raise PermissionError(f"Approval ID '{approval_id}' has expired at {row['expires_utc']}.")
        if row["capability"] != capability:
            raise PermissionError(f"Approval capability mismatch: granted for '{row['capability']}', called '{capability}'.")
        if self.security_policy.get("approval_rules", {}).get("bind_approval_to_payload_sha256", True):
            if row["payload_sha256"] != payload_sha256:
                raise PermissionError("Approval invalidated because input payload changed after approval.")
        return {
            "approval_id": approval_id,
            "risk_class": row["risk_class"],
            "decisions": json.loads(row["decisions_json"]),
        }

    def check_and_record_circuit_breaker(self, target_id: str, success: Optional[bool] = None, error_msg: str = "") -> Dict[str, Any]:
        limits = self.config.get("limits_and_recovery", {})
        threshold = int(limits.get("circuit_breaker_failure_threshold", 3))
        cooldown = int(limits.get("circuit_breaker_cooldown_seconds", 60))

        with self.connect() as conn:
            row = conn.execute("SELECT * FROM circuit_breakers WHERE target_id=?", (target_id,)).fetchone()
            state = row["state"] if row else "closed"
            failures = int(row["failure_count"]) if row else 0
            opened_utc = row["opened_utc"] if row else None

            if state == "open" and opened_utc:
                elapsed = (datetime.datetime.now(datetime.timezone.utc) - parse_iso_utc(opened_utc)).total_seconds()
                if elapsed >= cooldown:
                    state = "half-open"

            if success is None:
                return {"target_id": target_id, "state": state, "failure_count": failures, "opened_utc": opened_utc}

            if success:
                state = "closed"
                failures = 0
                opened_utc = None
                error_msg = ""
            else:
                failures += 1
                if failures >= threshold:
                    state = "open"
                    opened_utc = utc_now_iso()

            conn.execute(
                """
                INSERT INTO circuit_breakers (target_id, state, failure_count, opened_utc, last_error)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(target_id) DO UPDATE SET
                    state=excluded.state,
                    failure_count=excluded.failure_count,
                    opened_utc=excluded.opened_utc,
                    last_error=excluded.last_error
                """,
                (target_id, state, failures, opened_utc, error_msg),
            )
        return {"target_id": target_id, "state": state, "failure_count": failures, "opened_utc": opened_utc}


def check_rbac_and_approval(
    store: OfflineStateStore,
    capability_meta: Dict[str, Any],
    params: Dict[str, Any],
    principal: str,
    principal_role: str,
    approval_id: Optional[str],
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Enforce RBAC role policy, data classification policy, and single/dual-control human approval gates."""
    roles = store.security_policy.get("roles", {})
    role_cfg = roles.get(principal_role)
    if not role_cfg:
        raise PermissionError(f"Unknown principal role '{principal_role}'. Allowed roles: {sorted(roles.keys())}")

    risk_class = capability_meta["risk_class"]
    if risk_class not in role_cfg.get("allowed_risk_classes", []):
        raise PermissionError(
            f"Role '{principal_role}' cannot invoke '{capability_meta['slug']}' (risk_class='{risk_class}')."
        )

    data_class = str(params.get("classification", "internal")).lower()
    if data_class not in role_cfg.get("allowed_classifications", []):
        raise PermissionError(
            f"Role '{principal_role}' lacks clearance for data classification '{data_class}'."
        )

    payload_hash = sha256_hex({"capability": capability_meta["slug"], "params": params})
    requires_gate = risk_class in ("approval-required", "dual-control") and not dry_run
    # Read-only sub-operations (e.g., status/list/inspect) on a gated manager do not mutate state
    op_mode = str(params.get("operation", "execute")).lower()
    if op_mode in ("list", "status", "inspect", "verify", "plan", "dry-run"):
        requires_gate = False

    if requires_gate:
        if not approval_id:
            pending = store.create_approval_request(
                capability=capability_meta["slug"],
                payload_sha256=payload_hash,
                risk_class=risk_class,
                requester=principal,
                conditions={"role": principal_role},
            )
            return {
                "authorized": False,
                "approval_required": True,
                "payload_sha256": payload_hash,
                "pending_approval": pending,
            }
        verified = store.verify_approval_for_execution(
            approval_id=approval_id,
            capability=capability_meta["slug"],
            payload_sha256=payload_hash,
        )
        return {
            "authorized": True,
            "approval_required": True,
            "payload_sha256": payload_hash,
            "verified_approval": verified,
        }

    return {
        "authorized": True,
        "approval_required": False,
        "payload_sha256": payload_hash,
    }


def execute_with_retries_and_limits(
    store: OfflineStateStore,
    target_id: str,
    fn: Callable[[], Dict[str, Any]],
    max_retries: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Run an operation with circuit-breaker check, bounded exponential backoff, and wall-time deadline inspection."""
    cb = store.check_and_record_circuit_breaker(target_id)
    if cb["state"] == "open":
        raise RuntimeError(f"Circuit breaker OPEN for '{target_id}' after {cb['failure_count']} failures.")

    limits = store.config.get("limits_and_recovery", {})
    retries = int(max_retries if max_retries is not None else limits.get("max_retries", 3))
    base_ms = float(limits.get("retry_base_delay_ms", 150))
    max_ms = float(limits.get("retry_max_delay_ms", 2000))
    deadline_s = float(timeout_seconds if timeout_seconds is not None else limits.get("hard_timeout_seconds", 30.0))

    start = time.monotonic()
    attempt = 0
    last_exc: Optional[Exception] = None
    while attempt <= retries:
        attempt += 1
        try:
            result = fn()
            elapsed = time.monotonic() - start
            if elapsed > deadline_s:
                raise TimeoutError(f"Operation '{target_id}' exceeded deadline ({elapsed:.3f}s > {deadline_s:.3f}s).")
            store.check_and_record_circuit_breaker(target_id, success=True)
            return {"output": result, "attempts": attempt, "elapsed_seconds": round(elapsed, 6)}
        except (PermissionError, ValueError, KeyError, FileNotFoundError):
            # Non-retryable deterministic input/policy errors
            raise
        except Exception as exc:
            last_exc = exc
            store.check_and_record_circuit_breaker(target_id, success=False, error_msg=str(exc))
            if attempt > retries or (time.monotonic() - start) >= deadline_s:
                break
            delay_s = min(max_ms, base_ms * (2 ** (attempt - 1))) / 1000.0
            time.sleep(min(delay_s, 0.25))
    raise RuntimeError(f"Operation '{target_id}' failed after {attempt} attempt(s): {last_exc}")
