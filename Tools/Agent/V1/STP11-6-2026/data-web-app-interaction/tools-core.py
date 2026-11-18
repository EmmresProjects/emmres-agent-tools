#!/usr/bin/env python3
"""Shared runtime kernel for the data-web-app-interaction Skill Tool.

Provides relative resource resolution, hyphenated sibling module loading,
configuration merging, pre-execution input validation, secret/PII redaction,
approval gatekeeping, human-handoff detection, bounded retries/timeouts,
rate limiting, hash-chained ledgers, resumable checkpoints, and shared
stdlib HTML/DOM and structured-data parsers.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import csv
import datetime as dt
import hashlib
import html.parser
import importlib.util
import io
import ipaddress
import json
import os
import re
import sqlite3
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SKILL_TOOL_NAME = "data-web-app-interaction"
DEFAULT_CONFIG_REL = "data-web-app-interaction/asset-default-config.json"
DEFAULT_POLICY_REL = "data-web-app-interaction/asset-safety-policy.json"
DEFAULT_CATALOG_REL = "data-web-app-interaction/asset-skill-catalog.json"


class SkillToolError(Exception):
    """Base structured error for Skill Tool failures."""

    def __init__(self, message: str, status: str = "failed", details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details or {}


class InputValidationError(SkillToolError):
    """Raised when user inputs fail pre-execution validation."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, status="validation_error", details=details)


class ApprovalRequiredError(SkillToolError):
    """Raised when a consequential or mutating action lacks explicit approval."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, status="approval_required", details=details)


class HumanHandoffError(SkillToolError):
    """Raised when CAPTCHA, MFA, legal acceptance, or payment requires human action."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, status="human_handoff_required", details=details)


class PolicyBlockedError(SkillToolError):
    """Raised when target boundaries or safety rules forbid an action."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, status="blocked_by_policy", details=details)


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def resolve_package_resource(rel_path: str) -> Path:
    """Resolve a forward-slash path relative to the future skill root."""
    if not rel_path or "\x00" in rel_path:
        raise InputValidationError("Resource path must be a non-empty string.")
    normalized = rel_path.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p and p != "."]
    if ".." in parts:
        raise PolicyBlockedError(
            f"Parent directory traversal is forbidden in resource reference: {rel_path}",
            {"path": rel_path},
        )
    direct = Path("/".join(parts))
    if direct.exists():
        return direct
    # If invoked from inside the Skill Tool directory itself, strip leading folder prefix cleanly
    if parts and parts[0] == SKILL_TOOL_NAME and len(parts) > 1:
        local_candidate = Path("/".join(parts[1:]))
        if local_candidate.exists():
            return local_candidate
    # Fallback relative to script location without parent traversal strings
    module_dir = Path(__file__).parent
    if parts and parts[0] == SKILL_TOOL_NAME and len(parts) > 1:
        sibling = module_dir / "/".join(parts[1:])
        if sibling.exists():
            return sibling
    sibling_direct = module_dir / "/".join(parts)
    if sibling_direct.exists():
        return sibling_direct
    return direct


def load_sibling_module(filename: str) -> Any:
    """Load a hyphenated tools-*.py module from data-web-app-interaction/ without bytecode."""
    sys.dont_write_bytecode = True
    clean_name = filename if filename.endswith(".py") else f"{filename}.py"
    mod_key = "dwai_" + re.sub(r"[^a-z0-9]+", "_", clean_name[:-3].lower())
    if mod_key in sys.modules:
        return sys.modules[mod_key]
    target = resolve_package_resource(f"{SKILL_TOOL_NAME}/{clean_name}")
    if not target.exists():
        raise SkillToolError(f"Required tool module not found: {SKILL_TOOL_NAME}/{clean_name}")
    spec = importlib.util.spec_from_file_location(mod_key, str(target))
    if spec is None or spec.loader is None:
        raise SkillToolError(f"Unable to create module spec for {SKILL_TOOL_NAME}/{clean_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_key] = module
    spec.loader.exec_module(module)
    return module


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_runtime_config(config_path: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], str]:
    default_file = resolve_package_resource(DEFAULT_CONFIG_REL)
    if not default_file.exists():
        raise InputValidationError(f"Default configuration missing: {DEFAULT_CONFIG_REL}")
    cfg = json.loads(default_file.read_text(encoding="utf-8"))
    used_path = DEFAULT_CONFIG_REL
    if config_path:
        user_file = validate_safe_path(config_path, must_exist=True, allow_dir=False)
        try:
            user_cfg = json.loads(user_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InputValidationError(f"Invalid JSON in config file '{config_path}': {exc}") from exc
        if not isinstance(user_cfg, dict):
            raise InputValidationError("Configuration file root must be a JSON object.")
        cfg = deep_merge(cfg, user_cfg)
        used_path = config_path
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg, used_path


def load_safety_policy(cfg: Dict[str, Any]) -> Dict[str, Any]:
    policy_rel = cfg.get("paths", {}).get("policy_path", DEFAULT_POLICY_REL)
    policy_file = resolve_package_resource(policy_rel)
    if not policy_file.exists():
        raise InputValidationError(f"Safety policy file not found: {policy_rel}")
    return json.loads(policy_file.read_text(encoding="utf-8"))


def validate_safe_path(
    path_str: str,
    must_exist: bool = False,
    allow_dir: bool = True,
    max_bytes: Optional[int] = None,
    forbid_traversal: bool = True,
) -> Path:
    if not isinstance(path_str, str) or not path_str.strip():
        raise InputValidationError("Path must be a non-empty string.", {"path": path_str})
    if "\x00" in path_str:
        raise InputValidationError("Path contains forbidden NUL byte.", {"path": path_str})
    norm = path_str.replace("\\", "/")
    segments = [seg for seg in norm.split("/") if seg]
    if forbid_traversal and ".." in segments:
        raise PolicyBlockedError(
            f"Parent directory traversal ('..') is forbidden: {path_str}",
            {"path": path_str},
        )
    p = Path(path_str)
    if not p.exists() and norm.startswith(f"{SKILL_TOOL_NAME}/"):
        p = resolve_package_resource(norm)
    if must_exist and not p.exists():
        raise InputValidationError(f"Input path does not exist: {path_str}", {"path": path_str})
    if p.exists():
        if p.is_dir() and not allow_dir:
            raise InputValidationError(f"Expected a file but received a directory: {path_str}", {"path": path_str})
        if p.is_file() and max_bytes is not None:
            size = p.stat().st_size
            if size > max_bytes:
                raise InputValidationError(
                    f"File exceeds configured size limit ({size} > {max_bytes} bytes): {path_str}",
                    {"path": path_str, "size_bytes": size, "max_bytes": max_bytes},
                )
    return p


def luhn_valid(digits: str) -> bool:
    clean = [int(ch) for ch in digits if ch.isdigit()]
    if len(clean) < 13 or len(clean) > 19:
        return False
    checksum = 0
    parity = len(clean) % 2
    for idx, d in enumerate(clean):
        if idx % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


class SecretRedactor:
    """Redacts secrets, tokens, keys, cookies, and optional PII from text and JSON structures."""

    def __init__(self, policy: Dict[str, Any], redact_pii: bool = False) -> None:
        self.rules: List[Tuple[str, re.Pattern[str], str]] = []
        for rule in policy.get("secret_redaction_rules", []):
            self.rules.append((rule["name"], re.compile(rule["pattern"]), rule["replacement"]))
        self.pii_patterns: Dict[str, re.Pattern[str]] = {
            k: re.compile(v) for k, v in policy.get("pii_patterns", {}).items()
        }
        self.redact_pii = redact_pii
        self.redaction_count = 0

    def scrub_text(self, text: str) -> str:
        if not text:
            return text
        out = text
        for _, regex, repl in self.rules:
            out, count = regex.subn(repl, out)
            self.redaction_count += count
        # Redact Luhn-valid payment cards unconditionally in logs/outputs
        if "payment_card_candidate" in self.pii_patterns:
            def _card_repl(match: re.Match[str]) -> str:
                raw = match.group(0)
                if luhn_valid(raw):
                    self.redaction_count += 1
                    return "[REDACTED-PAYMENT-CARD]"
                return raw
            out = self.pii_patterns["payment_card_candidate"].sub(_card_repl, out)
        if self.redact_pii:
            for label, regex in self.pii_patterns.items():
                if label == "payment_card_candidate":
                    continue
                out, count = regex.subn(f"[REDACTED-{label.upper()}]", out)
                self.redaction_count += count
        return out

    def scrub_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.scrub_text(obj)
        if isinstance(obj, list):
            return [self.scrub_obj(item) for item in obj]
        if isinstance(obj, dict):
            cleaned: Dict[str, Any] = {}
            sensitive_key_re = re.compile(
                r"(?i)^(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|cookie|set-cookie|authorization|recovery[_-]?code)$"
            )
            for k, v in obj.items():
                key_str = str(k)
                if sensitive_key_re.match(key_str) and v not in (None, "", "[REDACTED-SECRET]"):
                    self.redaction_count += 1
                    cleaned[key_str] = "[REDACTED-SECRET]"
                else:
                    cleaned[key_str] = self.scrub_obj(v)
            return cleaned
        return obj


def enforce_url_boundary(url: str, cfg: Dict[str, Any], policy: Dict[str, Any], extra_allowed_domains: Optional[List[str]] = None) -> Dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    allowed_schemes = cfg.get("network", {}).get("allowed_schemes", ["https", "http"])
    if parsed.scheme.lower() not in allowed_schemes:
        raise PolicyBlockedError(
            f"URL scheme '{parsed.scheme}' is not permitted; allowed: {allowed_schemes}",
            {"url": url, "allowed_schemes": allowed_schemes},
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise InputValidationError(f"URL is missing a valid hostname: {url}", {"url": url})
    boundaries = policy.get("target_boundaries", {})
    blocked_domains = [d.lower() for d in boundaries.get("blocked_domains", [])]
    if host in blocked_domains:
        raise PolicyBlockedError(f"Domain '{host}' is blocked by safety policy.", {"url": url, "host": host})
    allow_localhost = bool(boundaries.get("allow_localhost_in_sandbox_test", False))
    if cfg.get("network", {}).get("block_private_ip_ranges", True) and not allow_localhost:
        if host == "localhost":
            raise PolicyBlockedError("Localhost target blocked unless allow_localhost_in_sandbox_test is enabled.", {"url": url})
        try:
            ip_obj = ipaddress.ip_address(host)
            for cidr in boundaries.get("blocked_ip_cidrs", []):
                if ip_obj in ipaddress.ip_network(cidr, strict=False):
                    raise PolicyBlockedError(f"IP address {host} falls in restricted range {cidr}.", {"url": url, "cidr": cidr})
        except ValueError:
            pass
    allowed_domains = [d.lower() for d in (extra_allowed_domains if extra_allowed_domains is not None else boundaries.get("allowed_domains", []))]
    if allowed_domains:
        matched = any(host == d or host.endswith("." + d) for d in allowed_domains)
        if not matched:
            raise PolicyBlockedError(
                f"Host '{host}' is outside declared allowed_domains: {allowed_domains}",
                {"url": url, "host": host, "allowed_domains": allowed_domains},
            )
    return {"scheme": parsed.scheme.lower(), "host": host, "path": parsed.path or "/", "query": parsed.query}


def check_human_handoff_triggers(params: Dict[str, Any], content_text: str, policy: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return handoff descriptor when explicit CAPTCHA, MFA, legal acceptance, or payment challenge is active."""
    explicit_challenge = str(params.get("challenge_type", "")).lower().strip()
    triggers = [t.lower() for t in policy.get("human_handoff_triggers", [])]
    if explicit_challenge and any(t in explicit_challenge for t in triggers):
        return {
            "handoff_reason": explicit_challenge,
            "required_actor": "authorized_human_operator",
            "safe_posture": "paused_before_challenge",
        }
    if params.get("detect_page_challenges", False) and content_text:
        lowered = content_text.lower()
        for trig in ("g-recaptcha", "hcaptcha", "cf-turnstile", "enter 6-digit verification code", "accept legal terms"):
            if trig in lowered:
                return {
                    "handoff_reason": f"detected_{trig}",
                    "required_actor": "authorized_human_operator",
                    "safe_posture": "paused_before_challenge",
                }
    return None


def enforce_approval_gate(
    skill_meta: Dict[str, Any],
    params: Dict[str, Any],
    approve: bool,
    approval_note: str,
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    """Verify consequential, destructive, publishing, financial, or credential mutations have explicit approval."""
    requested_op = str(params.get("operation", params.get("action", "inspect"))).lower().strip()
    http_method = str(params.get("method", "GET")).upper().strip()
    consequential_ops = set(policy.get("consequential_operations", []))
    mutating_methods = set(policy.get("mutating_http_methods", ["POST", "PUT", "PATCH", "DELETE"]))

    mutating_flag = bool(
        params.get("apply", False)
        or params.get("execute_mutation", False)
        or params.get("publish", False)
        or params.get("send", False)
        or params.get("confirm_purchase", False)
        or params.get("confirm_booking", False)
        or params.get("merge_pr", False)
        or requested_op in consequential_ops
        or (http_method in mutating_methods and params.get("live_request", False))
    )

    # Financial Consequence Gatekeeper always requires explicit approval when evaluating an active charge/commit
    if skill_meta["slug"] == "financial-consequence-gatekeeper" and params.get("commit_transaction", False):
        mutating_flag = True

    if mutating_flag and not approve:
        raise ApprovalRequiredError(
            f"Capability '{skill_meta['name']}' requested consequential operation '{requested_op}' without --approve.",
            {
                "skill_id": skill_meta["skill_id"],
                "slug": skill_meta["slug"],
                "requested_operation": requested_op,
                "risk_level": skill_meta.get("risk_level", "consequential"),
                "how_to_approve": "Re-run with --approve --approval-note '<authorized scope and reason>' after reviewing preview.",
            },
        )
    return {
        "mutating_operation": mutating_flag,
        "approved": bool(approve) if mutating_flag else True,
        "approval_note": approval_note if mutating_flag else "",
        "requested_operation": requested_op,
    }


class RateLimiter:
    """Bounded token-interval limiter to protect local and remote services."""

    def __init__(self, requests_per_second: float) -> None:
        self.rps = max(0.1, float(requests_per_second))
        self.min_interval = 1.0 / self.rps
        self._last_ts = 0.0

    def acquire(self) -> float:
        now = time.monotonic()
        elapsed = now - self._last_ts
        waited = 0.0
        if self._last_ts > 0 and elapsed < self.min_interval:
            waited = self.min_interval - elapsed
            time.sleep(waited)
        self._last_ts = time.monotonic()
        return waited


def run_bounded(
    func: Callable[[], Any],
    timeout_seconds: float,
    max_retries: int = 0,
    backoff_seconds: float = 0.25,
    backoff_multiplier: float = 2.0,
) -> Tuple[Any, Dict[str, Any]]:
    """Run callable within timeout and bounded retry policy."""
    attempts = 0
    delay = max(0.0, float(backoff_seconds))
    last_exc: Optional[Exception] = None
    t0 = time.perf_counter()

    while attempts <= max_retries:
        attempts += 1
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(func)
        try:
            result = future.result(timeout=max(0.5, float(timeout_seconds)))
            executor.shutdown(wait=False)
            return result, {
                "attempts": attempts,
                "duration_ms": round((time.perf_counter() - t0) * 1000.0, 3),
                "timed_out": False,
            }
        except FuturesTimeoutError as exc:
            executor.shutdown(wait=False, cancel_futures=True)
            last_exc = SkillToolError(
                f"Operation timed out after {timeout_seconds}s (attempt {attempts}/{max_retries + 1}).",
                status="failed",
                details={"timeout_seconds": timeout_seconds, "attempts": attempts},
            )
            if attempts > max_retries:
                raise last_exc from exc
        except (InputValidationError, ApprovalRequiredError, HumanHandoffError, PolicyBlockedError):
            executor.shutdown(wait=False)
            raise
        except Exception as exc:
            executor.shutdown(wait=False)
            last_exc = exc
            if attempts > max_retries:
                break
        if delay > 0:
            time.sleep(min(delay, 5.0))
            delay *= backoff_multiplier

    if isinstance(last_exc, SkillToolError):
        raise last_exc
    raise SkillToolError(f"Operation failed after {attempts} attempt(s): {last_exc}")


class ActionLedger:
    """Append-only SHA-256 hash-chained ledger of observations, actions, and outcomes."""

    def __init__(self, ledger_path: Optional[Path] = None) -> None:
        self.ledger_path = ledger_path
        self.entries: List[Dict[str, Any]] = []
        if ledger_path and ledger_path.exists():
            for line in ledger_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.entries.append(json.loads(line))

    def append(
        self,
        step_id: str,
        skill_id: str,
        observed_state: str,
        action_taken: str,
        resulting_state: str,
        uncertainty: str = "low",
        next_safe_step: str = "verify_output",
    ) -> Dict[str, Any]:
        prev_hash = self.entries[-1]["entry_hash"] if self.entries else "0" * 64
        seq = len(self.entries) + 1
        payload = {
            "sequence": seq,
            "timestamp_utc": utc_now_iso(),
            "step_id": step_id,
            "skill_id": skill_id,
            "observed_state": observed_state,
            "action_taken": action_taken,
            "resulting_state": resulting_state,
            "uncertainty": uncertainty,
            "next_safe_step": next_safe_step,
            "previous_hash": prev_hash,
        }
        digest = sha256_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))
        payload["entry_hash"] = digest
        self.entries.append(payload)
        if self.ledger_path:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=True) + "\n")
        return payload


class CheckpointStore:
    """Atomic checkpoint and idempotency store preventing duplicate consequential actions on resume."""

    def __init__(self, checkpoint_path: Optional[Path] = None) -> None:
        self.checkpoint_path = checkpoint_path
        self.state: Dict[str, Any] = {
            "updated_utc": utc_now_iso(),
            "completed_steps": {},
            "idempotency_keys": {},
        }
        if checkpoint_path and checkpoint_path.exists():
            loaded = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.state.update(loaded)

    def is_completed(self, step_id: str) -> bool:
        return step_id in self.state.get("completed_steps", {})

    def check_idempotency(self, idem_key: str) -> Optional[Dict[str, Any]]:
        return self.state.get("idempotency_keys", {}).get(idem_key)

    def record(self, step_id: str, summary: Dict[str, Any], idem_key: Optional[str] = None) -> None:
        self.state.setdefault("completed_steps", {})[step_id] = {
            "timestamp_utc": utc_now_iso(),
            "summary": summary,
        }
        if idem_key:
            self.state.setdefault("idempotency_keys", {})[idem_key] = {
                "step_id": step_id,
                "timestamp_utc": utc_now_iso(),
                "summary": summary,
            }
        self.state["updated_utc"] = utc_now_iso()
        if self.checkpoint_path:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp_name = tempfile.mkstemp(dir=str(self.checkpoint_path.parent), prefix=".chk-", suffix=".tmp")
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                    json.dump(self.state, fh, indent=2, ensure_ascii=True)
                os.replace(tmp_name, self.checkpoint_path)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)


class SimpleDOMParser(html.parser.HTMLParser):
    """Pure-stdlib structured HTML5/DOM analyzer capturing landmarks, headings, links, tables, forms, meta, and styles."""

    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    NOISE_TAGS = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_lang: Optional[str] = None
        self.title: str = ""
        self.meta: List[Dict[str, str]] = []
        self.links: List[Dict[str, str]] = []
        self.AnchorLinks: List[Dict[str, str]] = []
        self.headings: List[Dict[str, Any]] = []
        self.landmarks: List[Dict[str, str]] = []
        self.images: List[Dict[str, str]] = []
        self.forms: List[Dict[str, Any]] = []
        self.tables: List[Dict[str, Any]] = []
        self.lists: List[List[str]] = []
        self.styles: List[str] = []
        self.labels: Dict[str, str] = {}
        self.interactive_controls: List[Dict[str, Any]] = []
        self.main_paragraphs: List[str] = []
        self.all_text_chunks: List[str] = []

        self._tag_stack: List[Tuple[str, Dict[str, str]]] = []
        self._in_title = False
        self._in_style = False
        self._current_heading: Optional[Tuple[int, Dict[str, str], List[str]]] = None
        self._current_a: Optional[Tuple[Dict[str, str], List[str]]] = None
        self._current_label: Optional[Tuple[str, List[str]]] = None
        self._current_button: Optional[Tuple[Dict[str, str], List[str]]] = None
        self._current_form: Optional[Dict[str, Any]] = None
        self._current_select: Optional[Dict[str, Any]] = None
        self._current_option: Optional[Tuple[Dict[str, str], List[str]]] = None
        self._current_table: Optional[Dict[str, Any]] = None
        self._current_row: Optional[List[Dict[str, Any]]] = None
        self._current_cell: Optional[Tuple[str, Dict[str, str], List[str]]] = None
        self._current_list: Optional[List[str]] = None
        self._current_li: Optional[List[str]] = None
        self._current_p: Optional[List[str]] = None

    def _in_noise_region(self) -> bool:
        for t, attrs in self._tag_stack:
            if t in self.NOISE_TAGS:
                return True
            cls = (attrs.get("class", "") + " " + attrs.get("id", "")).lower()
            if any(tok in cls for tok in ("cookie", "banner-ad", "advert", "popup-ad")):
                return True
            if t in ("nav", "footer", "aside"):
                return True
        return False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        ad = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if t not in self.VOID_TAGS:
            self._tag_stack.append((t, ad))

        if t == "html" and "lang" in ad:
            self.html_lang = ad["lang"]
        elif t == "title":
            self._in_title = True
        elif t == "style":
            self._in_style = True
        elif t == "meta":
            self.meta.append(ad)
        elif t == "link":
            self.links.append(ad)
        elif t in ("header", "nav", "main", "aside", "footer", "section", "dialog", "form") or "role" in ad:
            self.landmarks.append({
                "tag": t,
                "role": ad.get("role", t if t in ("header", "nav", "main", "aside", "footer", "dialog", "form") else ""),
                "id": ad.get("id", ""),
                "aria_label": ad.get("aria-label", ""),
            })

        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._current_heading = (int(t[1]), ad, [])
        elif t == "a":
            self._current_a = (ad, [])
        elif t == "img":
            self.images.append(ad)
        elif t == "label":
            self._current_label = (ad.get("for", ""), [])
        elif t == "form":
            self._current_form = {
                "id": ad.get("id", ""),
                "action": ad.get("action", ""),
                "method": ad.get("method", "get").upper(),
                "fields": [],
                "buttons": [],
            }
        elif t in ("input", "textarea"):
            ctrl = {
                "tag": t,
                "id": ad.get("id", ""),
                "name": ad.get("name", ""),
                "type": ad.get("type", "text" if t == "input" else "textarea").lower(),
                "required": "required" in ad,
                "minlength": int(ad["minlength"]) if ad.get("minlength", "").isdigit() else None,
                "maxlength": int(ad["maxlength"]) if ad.get("maxlength", "").isdigit() else None,
                "pattern": ad.get("pattern", ""),
                "aria_label": ad.get("aria-label", ""),
                "testid": ad.get("data-testid", ""),
                "tabindex": ad.get("tabindex", ""),
            }
            self.interactive_controls.append(ctrl)
            if self._current_form is not None:
                self._current_form["fields"].append(ctrl)
        elif t == "select":
            sel = {
                "tag": "select",
                "id": ad.get("id", ""),
                "name": ad.get("name", ""),
                "type": "select",
                "required": "required" in ad,
                "options": [],
                "aria_label": ad.get("aria-label", ""),
                "testid": ad.get("data-testid", ""),
            }
            self._current_select = sel
            self.interactive_controls.append(sel)
            if self._current_form is not None:
                self._current_form["fields"].append(sel)
        elif t == "option" and self._current_select is not None:
            self._current_option = (ad, [])
        elif t == "button":
            self._current_button = (ad, [])
        elif t == "table":
            self._current_table = {"id": ad.get("id", ""), "caption": "", "rows": []}
        elif t == "caption" and self._current_table is not None:
            self._current_cell = ("caption", ad, [])
        elif t == "tr" and self._current_table is not None:
            self._current_row = []
        elif t in ("th", "td") and self._current_row is not None:
            self._current_cell = (t, ad, [])
        elif t in ("ul", "ol") and self._current_list is None:
            self._current_list = []
        elif t == "li" and self._current_list is not None:
            self._current_li = []
        elif t == "p":
            self._current_p = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._in_style:
            self.styles.append(data)
        if self._current_heading is not None:
            self._current_heading[2].append(data)
        if self._current_a is not None:
            self._current_a[1].append(data)
        if self._current_label is not None:
            self._current_label[1].append(data)
        if self._current_button is not None:
            self._current_button[1].append(data)
        if self._current_option is not None:
            self._current_option[1].append(data)
        if self._current_cell is not None:
            self._current_cell[2].append(data)
        if self._current_li is not None:
            self._current_li[0:] = self._current_li + [data]
        if self._current_p is not None and not self._in_noise_region():
            self._current_p.append(data)
        if not any(t in self.NOISE_TAGS for t, _ in self._tag_stack):
            stripped = data.strip()
            if stripped:
                self.all_text_chunks.append(stripped)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        for idx in range(len(self._tag_stack) - 1, -1, -1):
            if self._tag_stack[idx][0] == t:
                self._tag_stack.pop(idx)
                break

        if t == "title":
            self._in_title = False
            self.title = re.sub(r"\s+", " ", self.title).strip()
        elif t == "style":
            self._in_style = False
        elif t in ("h1", "h2", "h3", "h4", "h5", "h6") and self._current_heading is not None:
            lvl, ad, buf = self._current_heading
            self.headings.append({"level": lvl, "id": ad.get("id", ""), "text": re.sub(r"\s+", " ", "".join(buf)).strip()})
            self._current_heading = None
        elif t == "a" and self._current_a is not None:
            ad, buf = self._current_a
            self.AnchorLinks.append({
                "href": ad.get("href", ""),
                "text": re.sub(r"\s+", " ", "".join(buf)).strip(),
                "rel": ad.get("rel", ""),
                "aria_label": ad.get("aria-label", ""),
            })
            self._current_a = None
        elif t == "label" and self._current_label is not None:
            for_id, buf = self._current_label
            txt = re.sub(r"\s+", " ", "".join(buf)).strip()
            if for_id:
                self.labels[for_id] = txt
            self._current_label = None
        elif t == "option" and self._current_option is not None and self._current_select is not None:
            ad, buf = self._current_option
            txt = re.sub(r"\s+", " ", "".join(buf)).strip()
            self._current_select["options"].append({"value": ad.get("value", txt), "label": txt})
            self._current_option = None
        elif t == "select":
            self._current_select = None
        elif t == "button" and self._current_button is not None:
            ad, buf = self._current_button
            txt = re.sub(r"\s+", " ", "".join(buf)).strip()
            btn = {
                "tag": "button",
                "id": ad.get("id", ""),
                "type": ad.get("type", "submit").lower(),
                "text": txt,
                "aria_label": ad.get("aria-label", ""),
                "testid": ad.get("data-testid", ""),
            }
            self.interactive_controls.append(btn)
            if self._current_form is not None:
                self._current_form["buttons"].append(btn)
            self._current_button = None
        elif t == "form" and self._current_form is not None:
            for fld in self._current_form["fields"]:
                if fld.get("id") and fld["id"] in self.labels:
                    fld["label"] = self.labels[fld["id"]]
            self.forms.append(self._current_form)
            self._current_form = None
        elif t == "caption" and self._current_cell is not None and self._current_table is not None:
            self._current_table["caption"] = re.sub(r"\s+", " ", "".join(self._current_cell[2])).strip()
            self._current_cell = None
        elif t in ("th", "td") and self._current_cell is not None and self._current_row is not None:
            ctag, ad, buf = self._current_cell
            self._current_row.append({
                "tag": ctag,
                "text": re.sub(r"\s+", " ", "".join(buf)).strip(),
                "colspan": max(1, int(ad["colspan"])) if ad.get("colspan", "").isdigit() else 1,
                "rowspan": max(1, int(ad["rowspan"])) if ad.get("rowspan", "").isdigit() else 1,
            })
            self._current_cell = None
        elif t == "tr" and self._current_row is not None and self._current_table is not None:
            if self._current_row:
                self._current_table["rows"].append(self._current_row)
            self._current_row = None
        elif t == "table" and self._current_table is not None:
            self.tables.append(normalize_html_table(self._current_table))
            self._current_table = None
        elif t == "li" and self._current_li is not None and self._current_list is not None:
            txt = re.sub(r"\s+", " ", "".join(self._current_li)).strip()
            if txt:
                self._current_list.append(txt)
            self._current_li = None
        elif t in ("ul", "ol") and self._current_list is not None:
            if self._current_list:
                self.lists.append(self._current_list)
            self._current_list = None
        elif t == "p" and self._current_p is not None:
            txt = re.sub(r"\s+", " ", "".join(self._current_p)).strip()
            if txt:
                self.main_paragraphs.append(txt)
            self._current_p = None


def normalize_html_table(raw_table: Dict[str, Any]) -> Dict[str, Any]:
    """Expand colspan and rowspan cells into clean rectangular headers and records."""
    grid: Dict[Tuple[int, int], str] = {}
    header_rows = 0
    max_col = 0
    raw_rows = raw_table.get("rows", [])
    for r_idx, row in enumerate(raw_rows):
        c_idx = 0
        if all(cell["tag"] == "th" for cell in row):
            header_rows += 1
        for cell in row:
            while (r_idx, c_idx) in grid:
                c_idx += 1
            for dr in range(cell["rowspan"]):
                for dc in range(cell["colspan"]):
                    grid[(r_idx + dr, c_idx + dc)] = cell["text"]
                    max_col = max(max_col, c_idx + dc + 1)
            c_idx += cell["colspan"]

    total_rows = len(raw_rows)
    matrix = [[grid.get((r, c), "") for c in range(max_col)] for r in range(total_rows)]
    if not matrix:
        return {"id": raw_table.get("id", ""), "caption": raw_table.get("caption", ""), "headers": [], "records": []}
    h_count = max(1, header_rows) if len(matrix) > 1 else 0
    if h_count >= 1:
        headers = []
        for c in range(max_col):
            parts = []
            for r in range(h_count):
                val = matrix[r][c]
                if val and val not in parts:
                    parts.append(val)
            headers.append(" / ".join(parts) if parts else f"col_{c + 1}")
        body = matrix[h_count:]
    else:
        headers = [f"col_{c + 1}" for c in range(max_col)]
        body = matrix
    records = [dict(zip(headers, row)) for row in body]
    return {
        "id": raw_table.get("id", ""),
        "caption": raw_table.get("caption", ""),
        "headers": headers,
        "row_count": len(records),
        "records": records,
    }


def parse_html_document(html_text: str) -> SimpleDOMParser:
    parser = SimpleDOMParser()
    parser.feed(html_text)
    parser.close()
    for ctrl in parser.interactive_controls:
        cid = ctrl.get("id", "")
        if cid and cid in parser.labels and "label" not in ctrl:
            ctrl["label"] = parser.labels[cid]
    return parser


def load_structured_records(input_path: Optional[Path], params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load tabular/structured records from CSV, TSV, JSON, JSONL, XML, SQLite, or inline params['records']."""
    if isinstance(params.get("records"), list):
        recs = [dict(r) if isinstance(r, dict) else {"value": r} for r in params["records"]]
        return recs, {"format": "inline_records", "record_count": len(recs)}

    if input_path is None:
        default_csv = resolve_package_resource("data-web-app-interaction/asset-sample-records.csv")
        input_path = default_csv

    if input_path.is_dir():
        raise InputValidationError(f"Expected structured data file, got directory: {input_path}")

    suffix = input_path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        delim = "\t" if suffix == ".tsv" else params.get("delimiter", ",")
        raw = input_path.read_text(encoding="utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(raw), delimiter=delim)
        rows = [dict(r) for r in reader]
        return rows, {"format": suffix.lstrip("."), "fields": list(reader.fieldnames or []), "record_count": len(rows)}

    if suffix in (".jsonl", ".ndjson"):
        rows = []
        for idx, line in enumerate(input_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if line.strip():
                obj = json.loads(line)
                rows.append(obj if isinstance(obj, dict) else {"line": idx, "value": obj})
        fields = sorted({k for r in rows for k in r.keys()})
        return rows, {"format": "jsonl", "fields": fields, "record_count": len(rows)}

    if suffix in (".json", ".geojson"):
        payload = json.loads(input_path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(payload, list):
            rows = [r if isinstance(r, dict) else {"value": r} for r in payload]
        elif isinstance(payload, dict):
            for candidate in ("records", "items", "data", "rows", "features", "entries"):
                if isinstance(payload.get(candidate), list):
                    rows = [r if isinstance(r, dict) else {"value": r} for r in payload[candidate]]
                    break
            else:
                rows = [payload]
        else:
            rows = [{"value": payload}]
        fields = sorted({k for r in rows for k in r.keys()})
        return rows, {"format": suffix.lstrip("."), "fields": fields, "record_count": len(rows)}

    if suffix in (".sqlite", ".db", ".sqlite3"):
        conn = sqlite3.connect(f"file:{input_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
            if not tables:
                return [], {"format": "sqlite", "tables": [], "record_count": 0}
            target_table = params.get("table", tables[0])
            if not re.match(r"^[A-Za-z0-9_]+$", str(target_table)) or target_table not in tables:
                raise InputValidationError(f"Requested table '{target_table}' not in SQLite database tables {tables}.")
            cur = conn.execute(f'SELECT * FROM "{target_table}" LIMIT 10000')
            rows = [dict(r) for r in cur.fetchall()]
            fields = list(rows[0].keys()) if rows else []
            return rows, {"format": "sqlite", "table": target_table, "tables": tables, "fields": fields, "record_count": len(rows)}
        finally:
            conn.close()

    if suffix == ".xml":
        root = ET.fromstring(input_path.read_text(encoding="utf-8", errors="replace"))
        rows = []
        for child in list(root):
            rec: Dict[str, Any] = dict(child.attrib)
            for sub in list(child):
                tag = sub.tag.split("}")[-1]
                rec[tag] = (sub.text or "").strip()
            if not list(child) and child.text and child.text.strip():
                rec[child.tag.split("}")[-1]] = child.text.strip()
            rows.append(rec)
        fields = sorted({k for r in rows for k in r.keys()})
        return rows, {"format": "xml", "root_tag": root.tag.split("}")[-1], "fields": fields, "record_count": len(rows)}

    # Fallback line-oriented text records
    lines = [ln for ln in input_path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    rows = [{"line_number": i + 1, "text": ln} for i, ln in enumerate(lines)]
    return rows, {"format": "text_lines", "fields": ["line_number", "text"], "record_count": len(rows)}
