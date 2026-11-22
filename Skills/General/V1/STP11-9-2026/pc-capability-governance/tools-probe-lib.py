"""Shared probe, validation, evidence and error utilities.

Part of the ``pc-capability-governance`` Skill Tool. Imported by
``tools-provider-commands.py`` (probe command table) and by the primary entry
point ``tools-capability_governance.py``.

Standard library only. No network calls except an optional, bounded TCP
reachability check that can be disabled with ``--offline``.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0.0"
TOOL_ID = "pc-capability-governance"

# ---------------------------------------------------------------- statuses --
STATUS_FOUND = "FOUND"
STATUS_MISSING = "MISSING"
STATUS_WRONG_VERSION = "WRONG VERSION"
STATUS_BROKEN = "BROKEN"
STATUS_LICENSE_REQUIRED = "LICENSE REQUIRED"
STATUS_WINDOWS_HOST_REQUIRED = "WINDOWS HOST REQUIRED"
STATUS_GPU_REQUIRED = "GPU REQUIRED"
STATUS_VIRTUALIZATION_REQUIRED = "VIRTUALIZATION REQUIRED"
STATUS_NETWORK_REQUIRED = "NETWORK REQUIRED"
STATUS_NO_PROVIDER = "NO PROVIDER"

CONTRACT_STATUSES = (
    STATUS_FOUND,
    STATUS_MISSING,
    STATUS_WRONG_VERSION,
    STATUS_BROKEN,
    STATUS_LICENSE_REQUIRED,
    STATUS_WINDOWS_HOST_REQUIRED,
    STATUS_GPU_REQUIRED,
    STATUS_VIRTUALIZATION_REQUIRED,
    STATUS_NETWORK_REQUIRED,
)

CONTRACT_FIELDS = (
    "capability_id",
    "status",
    "detected_command",
    "detected_version",
    "host_type",
    "architecture",
    "requirements",
    "test_command",
    "exit_code",
    "evidence_path",
    "recommended_action",
)

# Exit codes used by every tools-* entry point.
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_APPROVAL_REQUIRED = 3
EXIT_BLOCKED = 4
EXIT_INTERNAL = 5


class SkillError(Exception):
    """Structured, user-facing error with a stable machine status."""

    def __init__(self, status, message, details=None, exit_code=EXIT_USAGE):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or {}
        self.exit_code = exit_code

    def to_dict(self):
        return {
            "error": True,
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "exit_code": self.exit_code,
        }


# ------------------------------------------------------------------- time ---
def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_run_id() -> str:
    return "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


# ------------------------------------------------------------------- json ---
def read_json(path, what="JSON file"):
    """Read a JSON document with precise validation errors."""
    if not isinstance(path, str) or not path:
        raise SkillError("INVALID_INPUT", f"{what} path is required")
    if not os.path.isfile(path):
        raise SkillError("INVALID_INPUT", f"{what} not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise SkillError(
            "INVALID_INPUT",
            f"{what} is not valid JSON: {path}",
            {"line": exc.lineno, "column": exc.colno, "reason": exc.msg},
        )
    except OSError as exc:
        raise SkillError("INVALID_INPUT", f"{what} cannot be read: {exc}")


def write_json(path, payload, indent=2):
    """Write JSON atomically (temp file in the same directory, then rename)."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=indent, ensure_ascii=False, sort_keys=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def emit(payload, stream=None):
    stream = stream or sys.stdout
    json.dump(payload, stream, indent=2, ensure_ascii=False)
    stream.write("\n")
    stream.flush()


# ------------------------------------------------------------------ hashes --
def sha256_file(path, chunk=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_tree(root):
    """Deterministic hash of a directory tree (paths + file hashes)."""
    if not os.path.isdir(root):
        raise SkillError("INVALID_INPUT", f"not a directory: {root}")
    entries = []
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                entries.append(f"{rel}\0{sha256_file(full)}")
            except OSError as exc:
                entries.append(f"{rel}\0unreadable:{exc.errno}")
    return sha256_bytes("\n".join(entries).encode("utf-8")), len(entries)


# ---------------------------------------------------------------- versions --
def parse_version(text, regex=None):
    """Extract the first version-looking token (or the first regex group)."""
    if not text:
        return None
    if regex:
        match = re.search(regex, text, re.MULTILINE)
        if not match:
            return None
        for group in match.groups():
            if group:
                return group.strip()
        return match.group(0).strip()
    match = re.search(r"(\d+\.\d+(?:\.\d+)*(?:[-+.~][0-9A-Za-z][0-9A-Za-z.\-+~]*)?)", text)
    return match.group(1) if match else None


def version_tuple(version):
    """Numeric release component of a version string, e.g. '17.0.9+11' -> (17,0,9)."""
    if not version:
        return ()
    head = re.split(r"[-+.~ ]", str(version).strip())[0]
    parts = []
    for piece in head.split("."):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            digits = re.match(r"(\d+)", piece)
            parts.append(int(digits.group(1)) if digits else 0)
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def compare_versions(left, right):
    """Return -1, 0 or 1 comparing two version strings on their release component."""
    a, b = version_tuple(left), version_tuple(right)
    width = max(len(a), len(b))
    a = a + (0,) * (width - len(a))
    b = b + (0,) * (width - len(b))
    return (a > b) - (a < b)


def version_in_range(version, minimum=None, maximum=None):
    """Check a detected version against an inclusive/exclusive policy window."""
    if not version:
        return False, "no version detected"
    if minimum and compare_versions(version, minimum) < 0:
        return False, f"below minimum supported version {minimum}"
    if maximum and compare_versions(version, maximum) > 0:
        return False, f"above maximum supported version {maximum}"
    return True, "within supported window"


# ------------------------------------------------------------------ shell ---
def run_capture(argv, timeout=10.0, env_extra=None, cwd=None):
    """Run a bounded, non-interactive subprocess and capture its output."""
    if not isinstance(argv, (list, tuple)) or not argv:
        raise SkillError("INVALID_INPUT", "command must be a non-empty list")
    env = dict(os.environ)
    env.setdefault("LC_ALL", "C")
    env.setdefault("LANG", "C")
    env["GIT_TERMINAL_PROMPT"] = "0"
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})
    started = time.time()
    try:
        proc = subprocess.run(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
        timed_out = False
        exit_code = proc.returncode
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        err = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
    except FileNotFoundError:
        return {
            "command": list(argv),
            "found": False,
            "exit_code": 127,
            "timed_out": False,
            "stdout": "",
            "stderr": "executable not found on PATH",
            "duration_ms": int((time.time() - started) * 1000),
        }
    except OSError as exc:
        return {
            "command": list(argv),
            "found": False,
            "exit_code": 126,
            "timed_out": False,
            "stdout": "",
            "stderr": f"cannot execute: {exc}",
            "duration_ms": int((time.time() - started) * 1000),
        }
    return {
        "command": list(argv),
        "found": True,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": out.strip(),
        "stderr": err.strip(),
        "duration_ms": int((time.time() - started) * 1000),
    }


# ------------------------------------------------------------------- host ---
def detect_host(offline=False, network_probe=None, network_timeout=0.6):
    """Describe the current host's capabilities without modifying anything."""
    system = platform.system().lower()
    host_type = {"linux": "linux", "windows": "windows", "darwin": "macos"}.get(system, system or "unknown")
    arch = platform.machine() or "unknown"
    kvm_dev = os.path.exists("/dev/kvm")
    kvm_readable = False
    if kvm_dev:
        try:
            kvm_readable = os.access("/dev/kvm", os.R_OK | os.W_OK)
        except OSError:
            kvm_readable = False
    gpu_markers = []
    if os.path.isdir("/dev/dri"):
        gpu_markers.append("/dev/dri")
    for candidate in ("nvidia-smi", "rocm-smi"):
        if shutil.which(candidate):
            gpu_markers.append(candidate)
    if host_type == "windows":
        gpu_markers.append("windows-display-adapter")
    network_available = None
    if offline:
        network_available = False
    else:
        endpoint = network_probe or {"host": "1.1.1.1", "port": 443}
        try:
            with socket.create_connection((endpoint["host"], int(endpoint["port"])), timeout=network_timeout):
                network_available = True
        except OSError:
            network_available = False
    return {
        "host_type": host_type,
        "architecture": arch,
        "kernel": platform.release(),
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "virtualization_present": bool(kvm_dev and kvm_readable),
        "virtualization_marker": "/dev/kvm" if kvm_dev else "absent",
        "gpu_present": bool(gpu_markers),
        "gpu_markers": gpu_markers,
        "network_available": network_available,
        "offline_mode": bool(offline),
    }


def host_matches(host_requirements, host):
    """Evaluate hard host requirements; returns (met, status, reason)."""
    req = host_requirements or {}
    wanted_os = [o.lower() for o in req.get("os", ["any"])]
    if "any" not in wanted_os and host["host_type"] not in wanted_os:
        needed = "windows" if "windows" in wanted_os else wanted_os[0]
        return (
            False,
            STATUS_WINDOWS_HOST_REQUIRED if needed == "windows" else f"{needed.upper()} HOST REQUIRED",
            f"host is {host['host_type']}, provider requires {'/'.join(wanted_os)}",
        )
    wanted_arch = [a.lower() for a in req.get("arch", ["any"])]
    if "any" not in wanted_arch and host["architecture"].lower() not in [a.lower() for a in wanted_arch]:
        return False, STATUS_MISSING, f"architecture {host['architecture']} not in {wanted_arch}"
    if req.get("gpu") and not host["gpu_present"]:
        return False, STATUS_GPU_REQUIRED, "no GPU device or GPU inspection tool detected"
    if req.get("virtualization") and not host["virtualization_present"]:
        return False, STATUS_VIRTUALIZATION_REQUIRED, (
            f"virtualization unavailable ({host['virtualization_marker']}); KVM/HAXM/WHPX required"
        )
    if req.get("network") and host["network_available"] is False:
        return False, STATUS_NETWORK_REQUIRED, "provider requires network access but none is usable"
    return True, STATUS_FOUND, "host requirements met"


# --------------------------------------------------------------- evidence ---
def evidence_dir(root, run_id):
    target = os.path.join(root, run_id)
    os.makedirs(target, exist_ok=True)
    return target


class EvidenceWriter:
    """Persist run evidence and expose a stable, relative evidence path."""

    def __init__(self, base_dir, run_id, base_root=None, keep_payloads=True):
        self.base_dir = os.path.abspath(base_dir)
        self.base_root = os.path.abspath(base_root or os.getcwd())
        self.run_id = run_id
        self.dir = evidence_dir(self.base_dir, run_id)
        self.keep_payloads = keep_payloads
        self.written = []

    def relative(self, path):
        """Relative-to-root path when inside the root, otherwise an absolute POSIX path.

        Never emits parent-directory traversal, so recorded evidence paths stay
        safe to embed in reports and handoff packages.
        """
        absolute = os.path.abspath(path)
        try:
            rel = os.path.relpath(absolute, self.base_root).replace(os.sep, "/")
        except ValueError:  # different drives on Windows
            return absolute.replace(os.sep, "/")
        if rel.startswith("../") or rel == "..":
            return absolute.replace(os.sep, "/")
        return rel

    def write(self, name, payload):
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "payload"
        path = os.path.join(self.dir, safe if safe.endswith(".json") else safe + ".json")
        write_json(path, payload)
        record = {"name": os.path.basename(path), "path": self.relative(path), "sha256": sha256_file(path)}
        self.written.append(record)
        return record

    def manifest(self):
        return {
            "run_id": self.run_id,
            "evidence_dir": self.relative(self.dir),
            "files": self.written,
        }


# ----------------------------------------------------------- input parsing --
TAG_RE = re.compile(r"<[^>]+>")
CSS_BLOCK_RE = re.compile(r"<style[^>]*>.*?</style>|<script[^>]*>.*?</script>", re.I | re.S)
ENTITY_MAP = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&nbsp;": " ",
    "&mdash;": "-",
    "&ndash;": "-",
    "&rsquo;": "'",
    "&lsquo;": "'",
    "&hellip;": "...",
}
CATEGORY_HEAD_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:category\s+)?(\d{1,2})?[.)\]]?\s*"
    r"(?P<name>[A-Z][A-Za-z0-9 ,/&'’.\-+()]{3,90}?)\s*"
    r"(?:\(\s*(?P<count>\d{1,3})\s*(?:skills|tools|capabilities)\s*\))?\s*$",
    re.I,
)
SKILL_LINE_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\d{1,3}|[a-z])[.)\]:-]\s*"
    r"(?:skill\s+name\s*:\s*)?(?P<name>.+?)\s*$",
    re.I,
)
SKILL_NAME_ONLY_RE = re.compile(r"^\s*(?:skill\s+name\s*:\s*)(?P<name>.+?)\s*$", re.I)
DESCRIPTION_RE = re.compile(r"^\s*(?:description|desc|purpose)\s*:\s*(?P<desc>.+?)\s*$", re.I)
ANNEX_SECTIONS = (
    "implementation priority",
    "startup self-test contract",
    "required self-test output fields",
    "p0 - must add first",
    "p1 - high value",
    "p2 - advanced",
    "category index",
    "catalog summary",
    "current baseline tools",
    "operating boundaries",
    "purpose",
)
NOISE_WORDS = {
    "purpose",
    "operating boundaries",
    "current baseline tools",
    "catalog summary",
    "category index",
    "implementation priority",
    "startup self-test contract",
    "required self-test output fields",
    "pc agent skill catalog",
    "comprehensive capability catalog for lawful pc work",
}


def decode_entities(text):
    out = text
    for entity, replacement in ENTITY_MAP.items():
        out = out.replace(entity, replacement)
    out = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), out)
    return out


def normalize_markup(text):
    """Strip HTML/CSS/scripts and normalise whitespace while preserving structure.

    Each visible block becomes its own line so category headings and numbered
    skill lines stay separable. Numbering marks, CSS classes, data attributes
    and inline styling are discarded.
    """
    if not isinstance(text, str):
        raise SkillError("INVALID_INPUT", "source text must be a string")
    out = CSS_BLOCK_RE.sub("\n", text)
    out = re.sub(r"<br\s*/?>", "\n", out, flags=re.I)
    out = re.sub(r"</(p|div|li|h[1-6]|tr|td|section|article|ul|ol|table|header|footer)>", "\n", out, flags=re.I)
    out = re.sub(r"<(li|p|div|tr|h[1-6])\b[^>]*>", "\n", out, flags=re.I)
    out = TAG_RE.sub("", out)
    out = decode_entities(out)
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for raw in out.split("\n"):
        line = re.sub(r"[ \t\u00a0]+", " ", raw).strip()
        if re.match(r"^=+\s*PAGE\s+\d+\s*=+$", line, re.I) or re.match(r"^-{3,}\s*page\s+\d+.*-{3,}$", line, re.I):
            continue  # page markers from PDF text extraction are not content
        line = re.sub(r"^[*\u2022\u25cf\-]{1,2}\s+", "", line) if line.startswith(("* ", "- ", "\u2022 ")) else line
        if line:
            lines.append(line)
    return "\n".join(lines)


def _clean_name(name):
    """Trim separators without destroying names such as '.NET' or '7-Zip'."""
    name = re.sub(r"\s+", " ", name or "").strip()
    name = re.sub(r"^[\s:;,\-\u2013\u2014]+", "", name)
    name = re.sub(r"[\s:;,\-\u2013\u2014]+$", "", name)
    name = re.sub(r"\.$", "", name)
    name = re.sub(r"^(skill|tool)\s*:\s*", "", name, flags=re.I)
    return name.strip()


def _slug(name):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "item"


def parse_catalog(text):
    """Extract categories and Skill Tool entries from plain text, Markdown or HTML.

    Returns a dict with ``categories`` (name, slug, declared_count, skills) and
    flat ``skills``. Descriptions come from an explicit ``Description:`` line or
    from the following non-heading line.
    """
    normalized = normalize_markup(text)
    lines = normalized.split("\n")
    categories = []
    index_entries = []
    index_phase = False
    current = None
    pending_skill = None
    orphan_skills = []

    def close_pending():
        nonlocal pending_skill
        pending_skill = None

    annex = False
    headers = _running_headers(lines)
    for index, line in enumerate(lines):
        stripped = _join_wrapped(lines, index, headers)
        lowered = stripped.lower().strip(":").strip("*# ").strip()
        if not stripped or (line.strip() in headers and not _match_category(stripped, _next_content(lines, index))):
            continue  # blank or repeated page header/footer
        if any(lowered.startswith(marker) for marker in ANNEX_SECTIONS):
            close_pending()
            current = None
            annex = True
            if lowered.startswith("category index"):
                index_phase = True
            continue
        if index_phase and current is None:
            heading = _match_category(stripped, _next_content(lines, index))
            if heading and heading[1] is not None:
                index_entries.append({"name": heading[0], "slug": _slug(heading[0]), "declared_count": heading[1]})
                continue
            index_phase = False
        desc = DESCRIPTION_RE.match(stripped)
        if desc and pending_skill is not None:
            pending_skill["description"] = _clean_desc(desc.group("desc"))
            close_pending()
            continue
        if lowered in NOISE_WORDS:
            close_pending()
            continue
        name_only = SKILL_NAME_ONLY_RE.match(stripped)
        if name_only:
            close_pending()
            skill = {"name": _clean_name(name_only.group("name")), "description": "", "number": None}
            (current["skills"] if current else orphan_skills).append(skill)
            pending_skill = skill
            continue
        if annex:
            heading = _match_category(stripped, _next_content(lines, index))
            known = {_slug(category["name"]) for category in categories} | {
                entry["slug"] for entry in index_entries
            }
            # Annex blocks (index, priorities, contract) end only where the real
            # category list resumes, i.e. a heading naming a known category.
            if heading and heading[1] is None and _slug(heading[0]) in known:
                annex = False
                close_pending()
                name, declared = heading
                current = {"name": name, "slug": _slug(name), "declared_count": declared, "skills": []}
                categories.append(current)
            continue
        heading = _match_category(stripped, _next_content(lines, index))
        if heading:
            close_pending()
            name, declared = heading
            current = {
                "name": name,
                "slug": _slug(name),
                "declared_count": declared,
                "declared_number": number_of(stripped),
                "skills": [],
            }
            categories.append(current)
            continue
        numbered = SKILL_LINE_RE.match(stripped)
        if numbered:
            candidate = _clean_name(numbered.group("name"))
            if not candidate or len(candidate) < 2:
                continue
            close_pending()
            number_match = re.match(r"^\s*(\d{1,3})", stripped)
            skill = {
                "name": candidate,
                "description": "",
                "number": int(number_match.group(1)) if number_match else None,
            }
            (current["skills"] if current else orphan_skills).append(skill)
            pending_skill = skill
            continue
        if pending_skill is not None and not pending_skill["description"]:
            pending_skill["description"] = _clean_desc(stripped)
            close_pending()
        elif current is not None and stripped:
            # Continuation prose under a heading is not a skill entry.
            pass

    if index_entries:
        declared_by_slug = {entry["slug"]: entry["declared_count"] for entry in index_entries}
        for category in categories:
            if category["slug"] in declared_by_slug and category.get("declared_count") is None:
                category["declared_count"] = declared_by_slug[category["slug"]]

    if orphan_skills:
        categories.insert(
            0,
            {"name": "uncategorized", "slug": "uncategorized", "declared_count": None, "skills": orphan_skills},
        )
    categories = _consolidate(categories)
    for category in categories:
        seen = set()
        unique = []
        for skill in category["skills"]:
            skill["slug"] = _slug(skill["name"])
            skill["category"] = category["name"]
            skill["category_slug"] = category["slug"]
            if skill["slug"] in seen:
                continue
            seen.add(skill["slug"])
            unique.append(skill)
        category["skills"] = unique
    flat = [skill for category in categories for skill in category["skills"]]
    return {
        "categories": categories,
        "index_entries": index_entries,
        "skills": flat,
        "counts": {
            "categories": len(categories),
            "skills": len(flat),
            "with_description": sum(1 for s in flat if s["description"]),
        },
    }


def number_of(line):
    """Leading list number of a heading or entry, e.g. '12. Archives, ...' -> 12."""
    match = re.match(r"^\s*#{0,6}\s*(\d{1,2})[.)]\s+", line or "")
    return int(match.group(1)) if match else None


def _consolidate(categories):
    """Merge same-slug categories and drop index-only headings with no skills."""
    merged = {}
    order = []
    for category in categories:
        slug = category["slug"]
        if slug not in merged:
            merged[slug] = dict(category)
            merged[slug]["skills"] = list(category["skills"])
            order.append(slug)
            continue
        target = merged[slug]
        target["skills"].extend(category["skills"])
        if target.get("declared_count") is None and category.get("declared_count") is not None:
            target["declared_count"] = category["declared_count"]
        if not target.get("name") and category.get("name"):
            target["name"] = category["name"]
    out = [merged[slug] for slug in order]
    _mark_index(categories, merged)
    kept = [c for c in out if c["skills"]]
    dropped = [c for c in out if not c["skills"]]
    for category in kept:
        category["index_only_headings_dropped"] = len(dropped) if category is kept[0] else None
    return kept


def _mark_index(original, merged):
    """Flag headings that came from an index block rather than the real list.

    An index lists every category once, numbered from 1, in order. When the same
    document later repeats a category with entries, the index copy is redundant:
    keeping it would double-count the category and its declared totals.
    """
    numbered = [c.get("declared_number") for c in original if c.get("declared_number")]
    index_run = len(numbered) >= 3 and numbered[:3] == [1, 2, 3]
    if not index_run:
        return
    for category in original:
        category["from_index"] = bool(category.get("declared_count") is not None and index_run)
    for category in merged.values():
        category["from_index"] = bool(category.get("from_index") and category.get("declared_count") is not None)
        if category["from_index"] and category["skills"]:
            # Mixed index+list: keep the skills but stop trusting the index count.
            category["declared_count"] = None
        category["index_only_headings_dropped"] = None


def _running_headers(lines):
    """Lines repeated three or more times are page headers/footers, not content."""
    counts = {}
    for raw in lines:
        text = raw.strip()
        if text and len(text) <= 90:
            counts[text] = counts.get(text, 0) + 1
    return {text for text, count in counts.items() if count >= 3}


def _join_wrapped(lines, index, headers=frozenset()):
    """Join a numbered heading wrapped across lines ("... and Thermal" + "Engineering")."""
    line = lines[index].strip()
    if not re.match(r"^\d{1,2}[.)]\s+", line):
        return line
    parts = [line]
    for offset in (1, 2):
        if index + offset >= len(lines):
            return " ".join(parts)
        candidate = lines[index + offset].strip()
        if not candidate or candidate in headers:
            return " ".join(parts)
        if re.match(r"^(\d{1,2}[.)]\s|skill\s*name\s*:|description\s*:|p\d\s*-)", candidate, re.I):
            return " ".join(parts)
        words = candidate.split()
        # A short, unpunctuated, capitalised tail continues the title.
        if len(words) <= 4 and candidate[:1].isupper() and not candidate.endswith((".", ":", ";")):
            parts.append(candidate)
            continue
        return " ".join(parts)
    return " ".join(parts)


def _next_content(lines, index, limit=3):
    """Return the next non-empty line after ``index`` (bounded lookahead)."""
    for offset in range(1, limit + 1):
        if index + offset >= len(lines):
            return None
        candidate = lines[index + offset].strip()
        if candidate:
            return candidate
    return None


def _clean_desc(text):
    desc = _clean_name(text)
    return re.sub(r"\s+", " ", desc)


def _match_category(line, next_line=None):
    """Return (name, declared_count) when a line looks like a category heading.

    ``next_line`` (the following non-empty line) disambiguates a numbered entry
    whose description follows: that is a Skill Tool entry, never a heading.
    """
    text = line.strip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"^\*\*(.+)\*\*$", r"\1", text)
    declared = None
    count_match = re.search(r"\(\s*(\d{1,3})\s*(?:skills|tools|capabilities)\s*\)", text, re.I)
    if count_match:
        declared = int(count_match.group(1))
        text = text[: count_match.start()].rstrip(" -|\u2013\u2014")
    had_number = False
    stripped_any = True
    while stripped_any:
        stripped_any = False
        for pattern in (r"^\s*(\d{1,2})[.)]\s+", r"^\s*(?:category|section)\s+\d{1,2}[.)]?\s+"):
            match = re.match(pattern, text, re.I)
            if match:
                text = text[match.end():]
                had_number = True
                stripped_any = True
    name = _clean_name(text)
    if not name or len(name) > 90 or len(name) < 4:
        return None
    if name.lower() in NOISE_WORDS:
        return None
    if re.match(r"^skill\s*(name)?\s*:", name, re.I):
        return None  # explicit skill line, never a category
    if declared is not None:
        return name, declared  # explicit "(N skills)" count wins over word heuristics
    if next_line and DESCRIPTION_RE.match(next_line):
        return None  # a description follows: this is an entry, not a heading
    if "," not in name and re.match(r"^(detect|build|route|check|create|record|repeat|classify|pin|break|choose|prevent|run|maintain|index|confirm|compare|evaluate|manage|report|produce|extract|provide|generate|validate|convert|repair|install|verify)(s|es|ed|ing)?\b", name, re.I):
        return None  # imperative sentence: a description, not a heading
    if name.endswith(('.', ':')):
        return None
    if not had_number:
        return None
    return name, None


# ------------------------------------------------- boundary term matching ---
_SPELLING = (
    ("licence", "license"),
    ("licencing", "licensing"),
    ("authorisation", "authorization"),
    ("behaviour", "behavior"),
    ("colour", "color"),
    ("serialise", "serialize"),
    ("minimise", "minimize"),
)


def normalize_term_text(text):
    """Lowercase, unify common spelling variants and collapse whitespace."""
    out = (text or "").lower()
    for british, american in _SPELLING:
        out = out.replace(british, american)
    out = re.sub(r"[\u2018\u2019]", "'", out)
    out = re.sub(r"\s+", " ", out)
    return out


def term_tokens(text):
    return re.findall(r"[a-z0-9][a-z0-9'_.-]*", text)


def _prefix(token, width=6):
    return token[:width]


def match_term(normalized_text, text_tokens, term):
    """Return 'phrase', 'prefix' or 'proximity' when ``term`` occurs in the text.

    Handles three real-world cases with one mechanism:
      * exact phrase present            -> 'phrase'
      * morphology (redistribute ~ redistribution) -> 'prefix'
      * reordered or padded phrasing (license bypass ~ bypass the license check)
        -> 'proximity', matched inside a small sliding token window
    """
    needle = normalize_term_text(term)
    if not needle:
        return None
    if needle in normalized_text:
        return "phrase"
    wanted = [tok for tok in term_tokens(needle) if len(tok) > 1]
    if not wanted:
        return None
    if len(wanted) == 1:
        target = _prefix(wanted[0])
        return "prefix" if any(tok.startswith(target) for tok in text_tokens) else None
    window = len(wanted) + 4
    prefixes = [_prefix(tok) for tok in wanted]
    for start in range(0, max(1, len(text_tokens) - window + 1)):
        chunk = text_tokens[start : start + window]
        if all(any(tok.startswith(pref) for tok in chunk) for pref in prefixes):
            return "proximity"
    return None
