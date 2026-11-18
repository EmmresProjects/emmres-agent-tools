#!/usr/bin/env python3
"""Reference library subsystem.

Implements the saved-reference-library workflow the catalog describes: one
human-editable Markdown file per category, a fixed entry schema, URL
canonicalisation, deduplication that never deletes history, staleness auditing,
bounded optional online link checks, and topic packaging for progressive loading.

Loaded by ``agent-skill-catalog/tools-skillcatalog.py`` via ``importlib``.
Standard library only. Network access happens only behind ``--online``.
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:  # loaded through the primary entry point
    from tools_catalog_parser import normalize_ws, slugify, tokenize
except ImportError:  # direct execution / standalone loading
    _here = os.path.dirname(os.path.abspath(__file__))
    _spec = importlib.util.spec_from_file_location(
        "tools_catalog_parser", os.path.join(_here, "tools-catalog-parser.py"))
    _parser = importlib.util.module_from_spec(_spec)
    sys.modules["tools_catalog_parser"] = _parser
    _spec.loader.exec_module(_parser)
    from tools_catalog_parser import normalize_ws, slugify, tokenize

__all__ = [
    "AUTHORITY_RANK",
    "TRACKING_PARAMS",
    "audit_library",
    "canonical_url",
    "dedupe_entries",
    "entry_block",
    "library_to_markdown_report",
    "load_library",
    "package_library",
    "parse_library_text",
    "validate_entries",
    "write_library",
]

AUTHORITY_RANK = {"official": 4, "primary": 4, "peer-reviewed": 3, "institutional": 3,
                  "reference": 2, "community": 1, "unverified": 0}
STATUS_VALUES = {"active", "stale", "moved", "dead", "merged", "superseded", "archived"}

TRACKING_PARAMS = {
    "gclid", "fbclid", "msclkid", "dclid", "yclid", "igshid", "mc_cid", "mc_eid",
    "ref", "ref_src", "ref_url", "referrer", "source", "src", "si", "spm",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "fb_action_ids", "wt_mc",
}

REQUIRED_FIELDS = ("title", "url", "category", "source-type", "authority", "date-added", "status")
DEFAULT_FIELDS = ("id", "title", "url", "category", "tags", "source-type", "authority",
                  "added-by", "date-added", "last-checked", "status", "archive-url",
                  "license", "annotation", "related")

_HEADER_RE = re.compile(r"^###\s+(?P<title>.+?)\s*$")
_KV_RE = re.compile(r"^[-*]\s+(?P<key>[a-z][a-z0-9-]*)\s*:\s*(?P<value>.*)$")


# --------------------------------------------------------------------------- #
# Entry handling
# --------------------------------------------------------------------------- #
def canonical_url(url: str) -> str:
    """Canonicalise a URL: drop tracking parameters, normalise host and slashes."""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not host:
        return url
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = "%s:%d" % (host, port)
    query_pairs = []
    for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in TRACKING_PARAMS:
            continue
        query_pairs.append((key, value))
    query_pairs.sort()
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((scheme, netloc, path, urllib.parse.urlencode(query_pairs), ""))


def entry_block(entry: dict, fields: tuple = DEFAULT_FIELDS) -> str:
    """Render one entry as a human-editable Markdown record block."""
    lines = ["### %s" % (entry.get("title") or "Untitled source")]
    for key in fields:
        value = entry.get(key, "")
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        value = normalize_ws(str(value)) if value not in (None, "") else "-"
        lines.append("- %s: %s" % (key, value))
    return "\n".join(lines)


def parse_library_text(text: str) -> list[dict]:
    """Parse category Markdown files back into entry dicts."""
    entries: list[dict] = []
    current: dict | None = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        header = _HEADER_RE.match(line)
        if header:
            if current and current.get("url"):
                entries.append(current)
            current = {"title": normalize_ws(header.group("title"))}
            continue
        if current is None:
            continue
        match = _KV_RE.match(line.strip())
        if not match:
            continue
        key = match.group("key")
        value = normalize_ws(match.group("value"))
        if value in ("-", "", "—"):
            value = ""
        if key in ("tags", "related"):
            current[key] = [item.strip() for item in value.split(",") if item.strip()] if value else []
        else:
            current[key] = value
    if current and current.get("url"):
        entries.append(current)
    for entry in entries:
        entry.setdefault("category", "general")
        entry["canonical-url"] = canonical_url(entry.get("url", ""))
    return entries


def load_library(directory: str) -> list[dict]:
    """Load every category Markdown file in a library directory."""
    if not os.path.isdir(directory):
        raise FileNotFoundError("library directory not found: %s" % directory)
    entries: list[dict] = []
    files = sorted(name for name in os.listdir(directory) if name.endswith(".md"))
    if not files:
        raise FileNotFoundError("no .md library files found in %s" % directory)
    for name in files:
        if name in ("archive-index.md", "source-schema.md"):
            continue
        with open(os.path.join(directory, name), "r", encoding="utf-8") as handle:
            parsed = parse_library_text(handle.read())
        for entry in parsed:
            entry.setdefault("source-file", name)
            entry.setdefault("category", name[:-3])
        entries.extend(parsed)
    return entries


def validate_entries(entries: list[dict], today: str | None = None) -> dict:
    """Validate entries against the shipped schema rules."""
    today = today or _dt.date.today().isoformat()
    errors: list[dict] = []
    warnings: list[dict] = []

    def add(bucket, code, message, where=""):
        bucket.append({"code": code, "message": message, "entry": where})

    for position, entry in enumerate(entries, start=1):
        where = entry.get("id") or entry.get("title") or "entry#%d" % position
        for field in REQUIRED_FIELDS:
            if not str(entry.get(field, "")).strip():
                add(errors, "FIELD_MISSING", "required field %r is empty" % field, where)
        url = str(entry.get("url", "")).strip()
        if url:
            parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
            if parts.scheme not in ("http", "https"):
                add(errors, "URL_SCHEME", "url must use http or https: %r" % url, where)
            if "." not in (parts.hostname or ""):
                add(errors, "URL_HOST", "url host looks invalid: %r" % url, where)
            if entry.get("url") != canonical_url(url) and "?" in url:
                add(warnings, "URL_NOT_CANONICAL", "url carries tracking or ordering noise: %r" % url, where)
        authority = str(entry.get("authority", "")).strip().lower()
        if authority and authority not in AUTHORITY_RANK:
            add(warnings, "AUTHORITY_UNKNOWN",
                "authority %r not in %s" % (authority, sorted(AUTHORITY_RANK)), where)
        status = str(entry.get("status", "")).strip().lower()
        if status and status not in STATUS_VALUES:
            add(warnings, "STATUS_UNKNOWN", "status %r not in %s" % (status, sorted(STATUS_VALUES)), where)
        added = str(entry.get("date-added", "")).strip()
        if added and not _valid_date(added):
            add(errors, "DATE_INVALID", "date-added is not YYYY-MM-DD: %r" % added, where)
        elif added and added > today:
            add(errors, "DATE_FUTURE", "date-added %r is after today (%s)" % (added, today), where)
        checked = str(entry.get("last-checked", "")).strip()
        if checked and not _valid_date(checked):
            add(warnings, "DATE_INVALID", "last-checked is not YYYY-MM-DD: %r" % checked, where)
        annotation = str(entry.get("annotation", "")).strip()
        if not annotation or len(annotation) < 12:
            add(warnings, "ANNOTATION_THIN", "annotation is missing or very short", where)
        if not entry.get("archive-url") and str(entry.get("authority", "")).lower() in ("official", "primary"):
            add(warnings, "ARCHIVE_MISSING", "high-authority entry has no archive-url", where)

    seen: dict[str, str] = {}
    for entry in entries:
        canon = entry.get("canonical-url") or canonical_url(str(entry.get("url", "")))
        if not canon:
            continue
        where = entry.get("id") or entry.get("title") or "entry"
        if canon in seen:
            add(warnings, "URL_DUPLICATE", "duplicate canonical url of %r" % seen[canon], where)
        else:
            seen[canon] = where

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "counts": {"entries": len(entries), "errors": len(errors), "warnings": len(warnings)}}


def _valid_date(value: str) -> bool:
    try:
        _dt.date.fromisoformat(value)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
def _atomic_write(path: str, text: str) -> None:
    tmp = path + ".tmp-partial"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def write_library(directory: str, entries: list[dict], schema: dict | None = None,
                  force: bool = False, approve: bool = False) -> dict:
    """Write one Markdown file per category plus an archive index."""
    os.makedirs(directory, exist_ok=True)
    existing = [name for name in os.listdir(directory) if name.endswith(".md")]
    if existing and not (force or approve):
        raise PermissionError(
            "library directory %s already contains %d Markdown file(s); pass --approve (and --force) to overwrite"
            % (directory, len(existing)))

    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        slug = slugify(str(entry.get("category") or "general"))
        grouped.setdefault(slug, []).append(entry)

    written: list[str] = []
    for slug in sorted(grouped):
        entries_in_group = sorted(
            grouped[slug],
            key=lambda item: (-AUTHORITY_RANK.get(str(item.get("authority", "")).lower(), 1),
                              str(item.get("title", "")).lower()),
        )
        lines = ["# %s sources" % slug.replace("-", " ").title(), ""]
        lines.append("%d entries · schema: source-schema.md · index: archive-index.md" % len(entries_in_group))
        lines.append("")
        lines.append("Edit entries in place. Never delete a record: set `status` to merged, superseded, moved or dead and record the successor in `related`.")
        lines.append("")
        for entry in entries_in_group:
            entry.setdefault("category", slug)
            lines.append(entry_block(entry))
            lines.append("")
        path = os.path.join(directory, slug + ".md")
        _atomic_write(path, "\n".join(lines).rstrip() + "\n")
        written.append(os.path.basename(path))

    index = ["# Archive index", "", "Priority order: official/primary, then peer-reviewed/institutional, then reference, then community.", ""]
    index.append("| Category | Entries | File | Active | Stale or dead |")
    index.append("| --- | --- | --- | --- | --- |")
    for slug in sorted(grouped):
        rows = grouped[slug]
        stale = sum(1 for item in rows if str(item.get("status", "")).lower() in ("stale", "dead", "moved", "superseded", "merged"))
        index.append("| %s | %d | %s.md | %d | %d |" % (
            slug, len(rows), slug, len(rows) - stale, stale))
    index.append("")
    index.append("Load order for a task: read this index, then the one or two category files that match, then individual entries.")
    _atomic_write(os.path.join(directory, "archive-index.md"), "\n".join(index) + "\n")
    written.append("archive-index.md")

    if schema:
        _atomic_write(os.path.join(directory, "source-schema.md"), _schema_markdown(schema))
        written.append("source-schema.md")
    return {"ok": True, "directory": directory, "files": sorted(written), "categories": len(grouped),
            "entries": len(entries)}


def _schema_markdown(schema: dict) -> str:
    out = ["# Source schema", "", schema.get("description", ""), ""]
    out.append("| Field | Required | Type | Allowed values | Notes |")
    out.append("| --- | --- | --- | --- | --- |")
    for field in schema.get("fields", []):
        allowed = field.get("enum") or field.get("values") or ""
        if isinstance(allowed, list):
            allowed = ", ".join(str(item) for item in allowed)
        out.append("| %s | %s | %s | %s | %s |" % (
            field.get("name", ""), "yes" if field.get("required") else "no",
            field.get("type", "string"), allowed, field.get("notes", "")))
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
def audit_library(entries: list[dict], today: str | None = None, max_age_days: int = 365,
                  online: bool = False, timeout: float = 5.0, retries: int = 1,
                  max_urls: int = 25, sleep_between: float = 0.2) -> dict:
    """Audit staleness, structure and (optionally) live link health."""
    today = today or _dt.date.today().isoformat()
    today_date = _dt.date.fromisoformat(today)
    findings: list[dict] = []
    stale_ids: list[str] = []

    def flag(level, code, where, message):
        findings.append({"level": level, "code": code, "entry": where, "message": message})

    for entry in entries:
        where = entry.get("id") or entry.get("title") or "entry"
        status = str(entry.get("status", "")).lower()
        if status in ("dead", "superseded", "merged", "moved"):
            flag("info", "STATUS_INACTIVE", where, "status is %s; keep for history, do not cite as current" % status)
        checked = str(entry.get("last-checked") or entry.get("date-added") or "").strip()
        if _valid_date(checked):
            age = (today_date - _dt.date.fromisoformat(checked)).days
            if age > max_age_days:
                flag("warning", "STALE_ENTRY", where, "last verified %d days ago (limit %d)" % (age, max_age_days))
                stale_ids.append(where)
            elif age < 0:
                flag("warning", "CHECK_DATE_FUTURE", where, "last checked date is in the future: %s" % checked)
        else:
            flag("warning", "NO_CHECK_DATE", where, "no usable last-checked or date-added value")
        if not entry.get("archive-url") and status == "active":
            flag("info", "NO_ARCHIVE", where, "no archive snapshot recorded")
        annotation = str(entry.get("annotation", ""))
        if len(annotation.split()) > 60:
            flag("info", "ANNOTATION_LONG", where, "annotation exceeds 60 words; move detail to a topic note")

    online_results = []
    if online:
        candidates = [entry for entry in entries if str(entry.get("url", "")).startswith(("http://", "https://"))]
        for entry in candidates[: max(0, max_urls)]:
            online_results.append(_check_url(entry, timeout=timeout, retries=retries))
            time.sleep(max(0.0, sleep_between))
        for result in online_results:
            where = result.get("entry")
            if result["state"] == "error":
                flag("error", "LINK_UNREACHABLE", where, result.get("detail", ""))
            elif result["state"] == "restricted":
                flag("warning", "LINK_RESTRICTED", where, "HTTP %s — access restricted or requires auth" % result["status"])
            elif result["state"] == "redirected":
                flag("warning", "LINK_REDIRECTED", where, "redirects to %s" % result["final_url"])
            elif result["state"] == "dead":
                flag("error", "LINK_DEAD", where, "HTTP %s" % result["status"])
        skipped = max(0, len(candidates) - max_urls)
        if skipped:
            flag("info", "ONLINE_CAP", "-", "%d URL(s) skipped by --max-urls %d" % (skipped, max_urls))

    by_category: dict[str, int] = {}
    for entry in entries:
        key = slugify(str(entry.get("category") or "general"))
        by_category[key] = by_category.get(key, 0) + 1

    severity = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        severity[finding["level"]] = severity.get(finding["level"], 0) + 1

    return {
        "ok": severity["error"] == 0,
        "today": today,
        "max_age_days": max_age_days,
        "entries": len(entries),
        "by_category": by_category,
        "stale_entries": stale_ids,
        "severity": severity,
        "findings": findings,
        "online": {"enabled": online, "checked": len(online_results), "results": online_results},
    }


def _check_url(entry: dict, timeout: float, retries: int) -> dict:
    """Bounded single-URL health check. Never raises; returns a state record."""
    url = entry.get("url", "")
    record = {"entry": entry.get("id") or entry.get("title"), "url": url,
              "state": "error", "status": None, "final_url": url, "detail": ""}
    attempts = max(1, retries + 1)
    for attempt in range(attempts):
        request = urllib.request.Request(url, method="GET", headers={
            "User-Agent": "agent-skill-catalog-link-audit/1.0 (+offline-first)",
            "Range": "bytes=0-2048",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                record.update({"state": "ok", "status": response.status,
                               "final_url": response.geturl()})
                if canonical_url(response.geturl()) != canonical_url(url):
                    record["state"] = "redirected"
                return record
        except urllib.error.HTTPError as exc:
            record.update({"status": exc.code, "detail": "HTTP %s" % exc.code})
            if exc.code in (401, 403, 429):
                record["state"] = "restricted"
                return record
            if exc.code in (404, 410):
                record["state"] = "dead"
                return record
            if attempt == attempts - 1:
                return record
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            record.update({"state": "error", "detail": "%s: %s" % (type(exc).__name__, exc)})
            if attempt == attempts - 1:
                return record
            time.sleep(0.3 * (attempt + 1))
    return record


def library_to_markdown_report(audit: dict) -> str:
    """Render the audit result as a reviewable Markdown report."""
    out = ["# Reference library audit", ""]
    out.append("- Run date: %s" % audit["today"])
    out.append("- Entries: %d · stale: %d" % (audit["entries"], len(audit["stale_entries"])))
    out.append("- Severity: %d error(s), %d warning(s), %d info" % (
        audit["severity"].get("error", 0), audit["severity"].get("warning", 0), audit["severity"].get("info", 0)))
    out.append("- Staleness limit: %d days" % audit["max_age_days"])
    out.append("- Online checks: %s" % ("%d URL(s)" % audit["online"]["checked"] if audit["online"]["enabled"] else "disabled (offline)"))
    out.append("")
    out.append("## Findings")
    out.append("")
    if not audit["findings"]:
        out.append("No findings.")
    else:
        out.append("| Level | Code | Entry | Detail |")
        out.append("| --- | --- | --- | --- |")
        order = {"error": 0, "warning": 1, "info": 2}
        for finding in sorted(audit["findings"], key=lambda item: order.get(item["level"], 3)):
            out.append("| %s | %s | %s | %s |" % (
                finding["level"], finding["code"], finding["entry"],
                str(finding["message"]).replace("|", "\\|")))
    out.append("")
    out.append("## Repair queue")
    out.append("")
    out.append("Repair batches are proposals; nothing is edited automatically. For each flagged entry record a decision,")
    out.append("then update `status`, `last-checked`, `archive-url` and `annotation`. Dead links stay in the file marked `dead`.")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Deduplication
# --------------------------------------------------------------------------- #
def dedupe_entries(entries: list[dict], apply: bool = False, approve: bool = False) -> dict:
    """Group exact canonical duplicates and merge annotations without deleting history."""
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        canon = entry.get("canonical-url") or canonical_url(str(entry.get("url", "")))
        if not canon:
            continue
        groups.setdefault(canon, []).append(entry)

    duplicates = {canon: items for canon, items in groups.items() if len(items) > 1}
    near: list[dict] = []
    by_title: dict[tuple, list[dict]] = {}
    for entry in entries:
        host = urllib.parse.urlsplit(str(entry.get("url", "")) if "://" in str(entry.get("url", "")) else "https://" + str(entry.get("url", ""))).hostname or ""
        key = (host.lower(), normalize_ws(str(entry.get("title", ""))).lower())
        if key[1]:
            by_title.setdefault(key, []).append(entry)
    for (host, title), items in by_title.items():
        if len(items) > 1:
            canons = {item.get("canonical-url") or canonical_url(str(item.get("url", ""))) for item in items}
            if len(canons) > 1:
                near.append({"reason": "same host and title, different urls", "host": host, "title": title,
                             "entries": [item.get("url") for item in items]})

    merges = []
    for canon, items in sorted(duplicates.items()):
        ranked = sorted(items, key=lambda item: (
            -len([value for value in item.values() if value not in ("", None, [], "-")]),
            -AUTHORITY_RANK.get(str(item.get("authority", "")).lower(), 1),
            str(item.get("date-added", "")),
        ))
        primary, others = ranked[0], ranked[1:]
        merged_fields = {}
        for other in others:
            if len(str(other.get("annotation", ""))) > len(str(merged_fields.get("annotation", primary.get("annotation", "")))):
                merged_fields["annotation"] = other.get("annotation")
            tags = sorted({*(primary.get("tags") or []), *(other.get("tags") or [])})
            if tags:
                merged_fields["tags"] = tags
            if not primary.get("archive-url") and other.get("archive-url"):
                merged_fields["archive-url"] = other.get("archive-url")
        merges.append({
            "canonical_url": canon,
            "keep": primary.get("id") or primary.get("title"),
            "keep_url": primary.get("url"),
            "merge": [item.get("id") or item.get("title") for item in others],
            "tombstone": [item.get("url") for item in others],
            "field_updates": merged_fields,
            "action": "set merged entries to status=merged and record superseded-by=%s" % primary.get("url"),
        })

    applied = False
    if apply:
        if not approve:
            raise PermissionError("--apply rewrites library records; rerun with --approve to confirm")
        for merge in merges:
            for entry in entries:
                if (entry.get("canonical-url") or canonical_url(str(entry.get("url", "")))) != merge["canonical_url"]:
                    continue
                if (entry.get("id") or entry.get("title")) == merge["keep"]:
                    for key, value in merge["field_updates"].items():
                        entry[key] = value
                else:
                    entry["status"] = "merged"
                    entry["annotation"] = normalize_ws(
                        "%s (merged into %s)" % (entry.get("annotation", ""), merge["keep_url"]))
        applied = True

    return {
        "ok": True,
        "applied": applied,
        "entries": len(entries),
        "duplicate_groups": len(merges),
        "near_duplicates": near,
        "merges": merges,
    }


# --------------------------------------------------------------------------- #
# Topic packaging
# --------------------------------------------------------------------------- #
def package_library(entries: list[dict], name: str, categories: list[str] | None = None,
                    limit: int = 40, out_dir: str = ".") -> dict:
    """Build a topic reference pack: compact index plus the selected category files."""
    slug = slugify(name)
    wanted = {slugify(category) for category in (categories or []) if str(category).strip()}
    selected = [entry for entry in entries
                if not wanted or slugify(str(entry.get("category", ""))) in wanted]
    selected = [entry for entry in selected if str(entry.get("status", "")).lower() not in ("dead", "merged")]
    selected.sort(key=lambda item: (-AUTHORITY_RANK.get(str(item.get("authority", "")).lower(), 1),
                                    str(item.get("last-checked") or item.get("date-added") or ""),
                                    str(item.get("title", "")).lower()))
    selected = selected[: max(1, limit)]
    if not selected:
        raise ValueError("no entries matched the requested categories: %s" % ", ".join(sorted(wanted)) or "(none given)")
    os.makedirs(out_dir, exist_ok=True)

    index = ["# %s reference pack" % name, "",
             "%d entries · generated for progressive loading" % len(selected), "",
             "## How to load", "",
             "1. Read this index only. It names the files and the priority order.",
             "2. Open the single category file that matches the task.",
             "3. Open individual entries only when their annotation looks decisive.",
             "4. Do not load every file for a narrow question.", "",
             "## Files", "", "| Priority | Category | Entries | File |", "| --- | --- | --- | --- |"]
    grouped: dict[str, list[dict]] = {}
    for entry in selected:
        grouped.setdefault(slugify(str(entry.get("category", "general"))), []).append(entry)
    ordered = sorted(grouped.items(), key=lambda item: -len(item[1]))
    for priority, (category, items) in enumerate(ordered, start=1):
        index.append("| %d | %s | %d | %s.md |" % (priority, category, len(items), category))
    index.append("")
    index.append("## Priority entries")
    index.append("")
    for entry in selected[:10]:
        index.append("- **%s** (%s) — %s" % (entry.get("title", ""), entry.get("authority", "unrated"),
                                             entry.get("annotation", "")))
    index.append("")
    _atomic_write(os.path.join(out_dir, "%s-index.md" % slug), "\n".join(index))

    for category, items in ordered:
        lines = ["# %s (topic pack: %s)" % (category, name), ""]
        for entry in items:
            lines.append(entry_block(entry))
            lines.append("")
        _atomic_write(os.path.join(out_dir, "%s.md" % category), "\n".join(lines).rstrip() + "\n")

    payload = {"name": name, "entries": selected, "categories": sorted(grouped)}
    _atomic_write(os.path.join(out_dir, "%s-entries.json" % slug), json.dumps(payload, indent=2) + "\n")
    return {"ok": True, "pack": slug, "out_dir": out_dir, "selected": len(selected),
            "categories": sorted(grouped),
            "files": ["%s-index.md" % slug, "%s-entries.json" % slug] + ["%s.md" % c for c, _ in ordered]}


def load_entries_file(path: str) -> list[dict]:
    """Load entries from a JSON array or an object with an ``entries`` list."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        data = data.get("entries", [])
    if not isinstance(data, list):
        raise ValueError("entries file must contain a JSON array or an object with an 'entries' array")
    for entry in data:
        entry["canonical-url"] = canonical_url(str(entry.get("url", "")))
    return data
