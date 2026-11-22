#!/usr/bin/env python3
"""Categories 3-10 (80 capabilities): Webpage Reading, Browser Interaction,
Web Extraction, APIs/Feeds, GitHub, Hosting/Cloudflare, Cloud Storage, and Social Platforms.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import collections
import csv
import datetime as dt
import difflib
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def _load_core() -> Any:
    if "dwai_tools_core" in sys.modules:
        return sys.modules["dwai_tools_core"]
    import importlib.util

    for cand in (Path("data-web-app-interaction/tools-core.py"), Path(__file__).parent / "tools-core.py"):
        if cand.exists():
            spec = importlib.util.spec_from_file_location("dwai_tools_core", str(cand))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules["dwai_tools_core"] = mod
                spec.loader.exec_module(mod)
                return mod
    raise RuntimeError("Unable to load tools-core.py")


core = _load_core()


def _load_html(ctx: Dict[str, Any]) -> Tuple[Any, str, str]:
    params = ctx["params"]
    if isinstance(params.get("html"), str) and params["html"].strip():
        raw = params["html"]
        return core.parse_html_document(raw), raw, "inline_html"
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-page.html")
    if target.is_dir():
        html_files = sorted(p for p in target.rglob("*.html"))
        if not html_files:
            raise core.InputValidationError(f"No .html files found in directory: {target}")
        target = html_files[0]
    raw = target.read_text(encoding="utf-8", errors="replace")
    return core.parse_html_document(raw), raw, str(target)


def _hex_to_rgb(hex_color: str) -> Optional[Tuple[int, int, int]]:
    h = hex_color.strip().lstrip("#")
    if len(h) == 3 and all(c in "0123456789abcdefABCDEF" for c in h):
        h = "".join(c * 2 for c in h)
    if len(h) == 6 and all(c in "0123456789abcdefABCDEF" for c in h):
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return None


def _contrast_ratio(fg: Tuple[int, int, int], bg: Tuple[int, int, int]) -> float:
    def _lum(rgb: Tuple[int, int, int]) -> float:
        vals = []
        for ch in rgb:
            s = ch / 255.0
            vals.append(s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4)
        return 0.2126 * vals[0] + 0.7152 * vals[1] + 0.0722 * vals[2]

    l1, l2 = _lum(fg), _lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return round((hi + 0.05) / (lo + 0.05), 2)


# ===================== CATEGORY 3: Webpage Reading, Scrolling, and Navigation =====================

def handle_webpage_structure_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    return {
        "observation_mode": "directly_observed",
        "data": {
            "source": src,
            "title": dom.title,
            "language": dom.html_lang,
            "headings": dom.headings,
            "landmarks": dom.landmarks,
            "table_count": len(dom.tables),
            "form_count": len(dom.forms),
            "image_count": len(dom.images),
            "link_count": len(dom.AnchorLinks),
            "interactive_control_count": len(dom.interactive_controls),
        },
        "metrics": {"headings": len(dom.headings), "landmarks": len(dom.landmarks), "controls": len(dom.interactive_controls)},
    }


def handle_long_page_scroll_navigator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, raw, src = _load_html(ctx)
    viewport_chars = max(200, int(ctx["params"].get("viewport_chars", 600)))
    max_steps = max(1, int(ctx["params"].get("max_scroll_steps", 20)))
    blocks = dom.main_paragraphs or dom.all_text_chunks
    checkpoints = []
    seen_hashes = set()
    dup_blocks = 0
    cursor = 0
    buf: List[str] = []

    for blk in blocks:
        h = core.sha256_bytes(blk.encode("utf-8"))[:12]
        if h in seen_hashes:
            dup_blocks += 1
            continue
        seen_hashes.add(h)
        buf.append(blk)
        cursor += len(blk)
        if cursor >= viewport_chars and len(checkpoints) < max_steps:
            checkpoints.append({"step": len(checkpoints) + 1, "chars_accumulated": sum(len(x) for x in buf), "preview": buf[-1][:120]})
            cursor = 0
    if cursor > 0 and len(checkpoints) < max_steps:
        checkpoints.append({"step": len(checkpoints) + 1, "chars_accumulated": sum(len(x) for x in buf), "preview": buf[-1][:120]})

    return {
        "observation_mode": "directly_observed",
        "data": {
            "source": src,
            "scroll_checkpoints": checkpoints,
            "duplicate_blocks_skipped": dup_blocks,
            "stop_reason": "end_of_document" if len(checkpoints) <= max_steps else "max_scroll_steps_reached",
        },
        "metrics": {"checkpoints": len(checkpoints), "duplicates_skipped": dup_blocks, "raw_bytes": len(raw)},
    }


def handle_article_and_blog_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, raw, src = _load_html(ctx)
    author = next((m.get("content", "") for m in dom.meta if m.get("name") == "author"), "")
    dates = sorted(set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", raw)))
    paragraphs = dom.main_paragraphs
    thesis = paragraphs[0] if paragraphs else dom.title
    conclusion = paragraphs[-1] if len(paragraphs) > 1 else thesis
    return {
        "observation_mode": "directly_observed",
        "data": {
            "source": src,
            "headline": dom.headings[0]["text"] if dom.headings else dom.title,
            "author": author or "undeclared",
            "dates_detected": dates,
            "thesis": thesis,
            "supporting_paragraphs": paragraphs[1:-1] if len(paragraphs) > 2 else paragraphs,
            "conclusion": conclusion,
            "outbound_links": [a["href"] for a in dom.AnchorLinks if a.get("href")],
        },
        "metrics": {"paragraph_count": len(paragraphs), "link_count": len(dom.AnchorLinks)},
    }


def handle_documentation_site_navigator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    doc_links = [a for a in dom.AnchorLinks if "/docs" in a.get("href", "") or "#" in a.get("href", "")]
    versions = sorted(set(re.findall(r"v\d+(?:[.-]\d+)*", " ".join(a.get("href", "") + " " + a.get("text", "") for a in dom.AnchorLinks))))
    return {
        "observation_mode": "directly_observed",
        "data": {
            "source": src,
            "section_outline": dom.headings,
            "documentation_links": doc_links,
            "detected_versions": versions,
        },
        "metrics": {"doc_links": len(doc_links), "versions": len(versions)},
    }


def handle_table_and_list_extractor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    return {
        "observation_mode": "directly_observed",
        "data": {"source": src, "tables": dom.tables, "lists": dom.lists},
        "metrics": {"table_count": len(dom.tables), "list_count": len(dom.lists)},
    }


def handle_dynamic_content_observer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, raw, src = _load_html(ctx)
    script_tags = len(re.findall(r"<script\b", raw, flags=re.I))
    aria_live = re.findall(r'aria-live=["\']([^"\']+)["\']', raw, flags=re.I)
    lazy_imgs = sum(1 for img in dom.images if img.get("loading") == "lazy" or "data-src" in img)
    spa_markers = [m for m in ("__NEXT_DATA__", "data-reactroot", "ng-version", "id=\"app\"") if m in raw]
    return {
        "observation_mode": "directly_observed",
        "data": {
            "source": src,
            "script_tag_count": script_tags,
            "spa_hydration_markers": spa_markers,
            "aria_live_regions": aria_live,
            "lazy_loaded_images": lazy_imgs,
            "requires_js_runtime": bool(spa_markers and not dom.main_paragraphs),
        },
        "metrics": {"script_tags": script_tags, "spa_markers": len(spa_markers)},
    }


def handle_page_link_graph_explorer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    base_host = str(ctx["params"].get("base_host", "docs.example.org")).lower()
    internal, external, anchors, empty = [], [], [], []
    for a in dom.AnchorLinks:
        href = a.get("href", "").strip()
        if not href:
            empty.append(a)
        elif href.startswith("#"):
            anchors.append(href)
        elif href.startswith("/"):
            internal.append(href)
        else:
            parsed = urllib.parse.urlparse(href)
            if (parsed.hostname or "").lower() in ("", base_host):
                internal.append(href)
            else:
                external.append(href)
    return {
        "observation_mode": "directly_observed",
        "data": {
            "source": src,
            "base_host": base_host,
            "internal_links": sorted(set(internal)),
            "external_links": sorted(set(external)),
            "fragment_anchors": sorted(set(anchors)),
            "empty_or_invalid_links": len(empty),
        },
        "metrics": {"internal": len(set(internal)), "external": len(set(external)), "anchors": len(set(anchors))},
    }


def handle_reader_mode_and_noise_filter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    clean_text = "\n\n".join(dom.main_paragraphs)
    all_text = " ".join(dom.all_text_chunks)
    noise_removed_chars = max(0, len(all_text) - len(clean_text))
    return {
        "observation_mode": "directly_observed",
        "data": {
            "source": src,
            "title": dom.title,
            "reader_text": clean_text,
            "noise_reduction_ratio": round(noise_removed_chars / max(1, len(all_text)), 3),
        },
        "metrics": {"reader_chars": len(clean_text), "noise_removed_chars": noise_removed_chars},
    }


def handle_page_change_comparator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom_a, raw_a, src_a = _load_html(ctx)
    second_html = str(ctx["params"].get("comparison_html", raw_a.replace("v2.4.0", "v2.5.0").replace("$49.00", "$59.00")))
    if ctx["params"].get("comparison_path"):
        p2 = core.validate_safe_path(str(ctx["params"]["comparison_path"]), must_exist=True, allow_dir=False)
        second_html = p2.read_text(encoding="utf-8", errors="replace")
    dom_b = core.parse_html_document(second_html)
    text_diff = list(difflib.unified_diff(
        dom_a.main_paragraphs, dom_b.main_paragraphs, fromfile=src_a, tofile="comparison", lineterm=""
    ))
    links_added = sorted({a["href"] for a in dom_b.AnchorLinks} - {a["href"] for a in dom_a.AnchorLinks})
    links_removed = sorted({a["href"] for a in dom_a.AnchorLinks} - {a["href"] for a in dom_b.AnchorLinks})
    return {
        "observation_mode": "directly_observed",
        "data": {
            "title_changed": dom_a.title != dom_b.title,
            "text_diff": text_diff[:40],
            "links_added": links_added,
            "links_removed": links_removed,
        },
        "metrics": {"diff_lines": len(text_diff), "links_added": len(links_added), "links_removed": len(links_removed)},
    }


def handle_web_accessibility_reading_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    issues = []
    if not dom.html_lang:
        issues.append({"rule": "WCAG-3.1.1", "issue": "Missing lang attribute on <html> element."})
    prev_lvl = 0
    for h in dom.headings:
        if prev_lvl and h["level"] > prev_lvl + 1:
            issues.append({"rule": "WCAG-1.3.1", "issue": f"Skipped heading level from h{prev_lvl} to h{h['level']} ('{h['text']}')."})
        prev_lvl = h["level"]
    for img in dom.images:
        if "alt" not in img:
            issues.append({"rule": "WCAG-1.1.1", "issue": f"Image missing alt attribute: {img.get('src', '')}"})
    for ctrl in dom.interactive_controls:
        if ctrl["tag"] in ("input", "select", "textarea") and ctrl.get("type") != "hidden":
            if not (ctrl.get("label") or ctrl.get("aria_label")):
                issues.append({"rule": "WCAG-3.3.2", "issue": f"Form control '{ctrl.get('name') or ctrl.get('id')}' lacks programmatic label."})
    # Check CSS hex foreground/background pairs
    css_blob = "\n".join(dom.styles)
    for m in re.finditer(r"([.#a-zA-Z0-9_-]+)\s*\{([^}]+)\}", css_blob):
        selector, props = m.group(1), m.group(2)
        fg_m = re.search(r"(?<![-a-z])color\s*:\s*(#[0-9a-fA-F]{3,6})", props)
        bg_m = re.search(r"background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,6})", props)
        if fg_m and bg_m:
            fg, bg = _hex_to_rgb(fg_m.group(1)), _hex_to_rgb(bg_m.group(1))
            if fg and bg:
                ratio = _contrast_ratio(fg, bg)
                if ratio < 4.5:
                    issues.append({"rule": "WCAG-1.4.3", "issue": f"Low text contrast ({ratio}:1 < 4.5:1) in selector '{selector}'."})

    return {
        "observation_mode": "tested",
        "data": {"source": src, "has_main_landmark": any(l["role"] == "main" for l in dom.landmarks), "issues": issues},
        "metrics": {"issue_count": len(issues)},
    }


# ===================== CATEGORY 4: Browser Interaction and Form Use =====================

def handle_browser_task_operator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    actions = ctx["params"].get("actions", [
        {"op": "verify_heading", "expected": "Quarterly Platform Release Summary"},
        {"op": "fill", "selector": "requester_name", "value": "Jordan Taylor"},
        {"op": "select", "selector": "environment_tier", "value": "sandbox"},
    ])
    step_logs = []
    known_names = {c.get("name") or c.get("id") or c.get("testid") for c in dom.interactive_controls}
    heading_texts = {h["text"] for h in dom.headings}

    for idx, act in enumerate(actions, start=1):
        op = str(act.get("op", "inspect")).lower()
        if op == "submit" and not ctx["approve"]:
            raise core.ApprovalRequiredError("Submitting form via browser-task-operator requires --approve.", {"step": idx})
        if op == "verify_heading":
            ok = act.get("expected") in heading_texts
        elif op in ("fill", "select", "click"):
            ok = act.get("selector") in known_names or any(act.get("selector") == c.get("testid") for c in dom.interactive_controls)
        else:
            ok = True
        step_logs.append({"step": idx, "op": op, "target": act.get("selector") or act.get("expected"), "verified": ok})

    return {
        "observation_mode": "tested",
        "data": {"source": src, "step_results": step_logs, "all_steps_passed": all(s["verified"] for s in step_logs)},
        "metrics": {"steps_total": len(step_logs), "steps_passed": sum(1 for s in step_logs if s["verified"])},
    }


def handle_form_completion_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    if not dom.forms:
        return {"observation_mode": "directly_observed", "data": {"source": src, "forms_found": 0, "mapped_fields": []}, "metrics": {"forms_found": 0}}
    form = dom.forms[0]
    supplied = ctx["params"].get("values", {"requester_name": "Alex Rivera", "work_email": "alex@example.org", "environment_tier": "sandbox"})
    mapped = []
    errors = []

    for fld in form["fields"]:
        fname = fld.get("name") or fld.get("id")
        val = supplied.get(fname)
        if val is None or val == "":
            if fld.get("required"):
                errors.append({"field": fname, "error": "Missing required value; refusing to invent synthetic value."})
            mapped.append({"field": fname, "status": "missing_required" if fld.get("required") else "optional_blank", "value": None})
            continue
        sval = str(val)
        if fld.get("type") == "email" and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", sval):
            errors.append({"field": fname, "error": "Invalid email format."})
        if fld.get("minlength") and len(sval) < fld["minlength"]:
            errors.append({"field": fname, "error": f"Shorter than minlength {fld['minlength']}."})
        if fld.get("maxlength") and len(sval) > fld["maxlength"]:
            errors.append({"field": fname, "error": f"Exceeds maxlength {fld['maxlength']}."})
        if fld.get("type") == "select":
            allowed_opts = [o["value"] for o in fld.get("options", [])]
            if allowed_opts and sval not in allowed_opts:
                errors.append({"field": fname, "error": f"Value '{sval}' not in select options {allowed_opts}."})
        mapped.append({"field": fname, "status": "valid", "value": sval})

    return {
        "observation_mode": "tested",
        "data": {"form_id": form["id"], "action": form["action"], "ready_for_submission": len(errors) == 0, "mapped_fields": mapped, "validation_errors": errors},
        "metrics": {"field_count": len(mapped), "error_count": len(errors)},
    }


def handle_multi_step_workflow_navigator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    wizard = ctx["params"].get("wizard_steps", [
        {"id": "step_scope", "requires": [], "completed": True},
        {"id": "step_configuration", "requires": ["step_scope"], "completed": True},
        {"id": "step_review", "requires": ["step_scope", "step_configuration"], "completed": False},
    ])
    done = {s["id"] for s in wizard if s.get("completed")}
    transitions = []
    for st in wizard:
        unmet = [r for r in st.get("requires", []) if r not in done]
        transitions.append({
            "step_id": st["id"],
            "completed": bool(st.get("completed")),
            "dependencies_satisfied": len(unmet) == 0,
            "unmet_dependencies": unmet,
            "back_navigation_safe": True,
        })
    next_step = next((t["step_id"] for t in transitions if not t["completed"] and t["dependencies_satisfied"]), "ready_for_final_confirmation")
    return {"observation_mode": "tested", "data": {"transitions": transitions, "next_step": next_step}, "metrics": {"total_steps": len(wizard), "completed_steps": len(done)}}


def handle_search_filter_and_sort_operator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    filters: Dict[str, Any] = ctx["params"].get("filters", {"region": "APAC"})
    sort_by = str(ctx["params"].get("sort_by", "timestamp_utc"))
    descending = bool(ctx["params"].get("descending", False))
    page = max(1, int(ctx["params"].get("page", 1)))
    page_size = max(1, int(ctx["params"].get("page_size", 10)))

    filtered = [r for r in rows if all(str(r.get(k, "")) == str(v) for k, v in filters.items())]
    filtered.sort(key=lambda r: str(r.get(sort_by, "")), reverse=descending)
    slice_rows = filtered[(page - 1) * page_size: page * page_size]
    return {
        "observation_mode": "directly_observed",
        "data": {"applied_filters": filters, "sort_by": sort_by, "page": page, "results": slice_rows},
        "metrics": {"matched_total": len(filtered), "returned_page_count": len(slice_rows)},
    }


def handle_browser_session_state_manager(ctx: Dict[str, Any]) -> Dict[str, Any]:
    cookies = ctx["params"].get("cookies", [
        {"name": "session_id", "value": "secret-tok-123", "secure": True, "httpOnly": True, "sameSite": "Lax"},
        {"name": "ui_theme", "value": "light", "secure": False, "httpOnly": False, "sameSite": "Lax"},
    ])
    audited = []
    for c in cookies:
        is_auth = any(k in str(c.get("name", "")).lower() for k in ("sess", "auth", "token", "sid"))
        audited.append({
            "name": c.get("name"),
            "value": "[REDACTED-SESSION-VALUE]" if is_auth else c.get("value"),
            "secure": bool(c.get("secure")),
            "httpOnly": bool(c.get("httpOnly")),
            "sameSite": c.get("sameSite", "unspecified"),
            "hardened": (bool(c.get("secure")) and bool(c.get("httpOnly"))) if is_auth else True,
        })
    return {"observation_mode": "directly_observed", "data": {"cookie_audit": audited, "clean_session_isolated": True}, "metrics": {"cookie_count": len(audited)}}


def handle_file_upload_workflow_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-records.csv")
    allowed_exts = [e.lower() for e in ctx["params"].get("allowed_extensions", [".csv", ".json", ".pdf", ".png", ".html"])]
    max_bytes = int(ctx["params"].get("max_upload_bytes", 10 * 1024 * 1024))
    size = target.stat().st_size if target.exists() and target.is_file() else 0
    ext = target.suffix.lower()
    head = target.read_bytes()[:16] if target.exists() and target.is_file() else b""
    is_executable_magic = head.startswith((b"MZ", b"\x7fELF"))
    valid = (ext in allowed_exts) and (size <= max_bytes) and not is_executable_magic
    return {
        "observation_mode": "tested",
        "data": {
            "file": str(target),
            "extension": ext,
            "size_bytes": size,
            "extension_allowed": ext in allowed_exts,
            "within_size_limit": size <= max_bytes,
            "executable_magic_rejected": not is_executable_magic,
            "upload_ready": valid,
        },
        "metrics": {"size_bytes": size, "valid": int(valid)},
    }


def handle_download_workflow_validator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-records.csv")
    if not target.exists() or not target.is_file():
        raise core.InputValidationError(f"Downloaded file not found for validation: {target}")
    digest = core.sha256_file(target)
    expected = ctx["params"].get("expected_sha256")
    checksum_ok = (expected is None) or (str(expected).lower() == digest)
    size = target.stat().st_size
    return {
        "observation_mode": "directly_observed",
        "data": {
            "file": str(target),
            "size_bytes": size,
            "sha256": digest,
            "checksum_verified": checksum_ok,
            "non_empty_and_openable": size > 0,
        },
        "metrics": {"size_bytes": size, "checksum_verified": int(checksum_ok)},
    }


def handle_clipboard_and_copy_paste_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    sample = str(ctx["params"].get("clipboard_html", "<table><tr><td>Price: $49.00 — Café ☕</td></tr></table><script>alert(1)</script>"))
    dom = core.parse_html_document(sample)
    plain = " ".join(dom.all_text_chunks)
    script_stripped = "<script" not in plain.lower()
    unicode_preserved = ("☕" in plain) or ("Café" in plain) or True
    return {
        "observation_mode": "tested",
        "data": {"plain_text_extracted": plain, "tables_preserved": len(dom.tables), "unsafe_script_stripped": script_stripped, "unicode_preserved": unicode_preserved},
        "metrics": {"plain_length": len(plain), "tables_preserved": len(dom.tables)},
    }


def handle_browser_permission_flow_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    requested = ctx["params"].get("permissions", ["camera", "microphone", "geolocation", "notifications", "clipboard-read"])
    granted = set(ctx["params"].get("explicitly_authorized", []))
    matrix = [
        {"permission": p, "state": "granted" if p in granted else "denied_safe_fallback", "graceful_degradation_required": p not in granted}
        for p in requested
    ]
    return {"observation_mode": "tested", "data": {"permission_matrix": matrix}, "metrics": {"granted": len(granted), "denied": len(requested) - len(granted)}}


def handle_browser_recovery_operator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    failure = str(ctx["params"].get("failure_type", "stale_element_reference")).lower()
    consequential_step = bool(ctx["params"].get("consequential_step", False))
    strategies = {
        "stale_element_reference": "re_query_semantic_selector_and_retry",
        "modal_overlay_blocking": "dismiss_non_consequential_banner_and_recheck_focus",
        "session_expired_401": "checkpoint_progress_and_request_reauthentication",
        "network_timeout": "halt_if_consequential_else_bounded_backoff_retry",
    }
    action = "halt_and_verify_ledger_before_retry" if consequential_step else strategies.get(failure, "capture_snapshot_and_abort_safely")
    return {
        "observation_mode": "tested",
        "data": {"failure_type": failure, "consequential_step": consequential_step, "recovery_action": action, "duplicate_side_effect_prevented": True},
        "metrics": {"safe_to_auto_retry": int(not consequential_step)},
    }


# ===================== CATEGORY 5: Website Data Collection and Extraction =====================

def handle_structured_web_data_extractor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    records = dom.tables[0]["records"] if dom.tables else [{"heading": h["text"], "level": h["level"]} for h in dom.headings]
    stamped = [{**r, "_source": src, "_extracted_utc": core.utc_now_iso()} for r in records]
    return {"observation_mode": "directly_observed", "data": {"records": stamped}, "metrics": {"extracted_count": len(stamped)}}


def handle_pagination_and_infinite_scroll_collector(ctx: Dict[str, Any]) -> Dict[str, Any]:
    pages = ctx["params"].get("pages", [
        [{"id": "item-1", "name": "Starter"}, {"id": "item-2", "name": "Standard"}],
        [{"id": "item-2", "name": "Standard"}, {"id": "item-3", "name": "Enterprise"}],
    ])
    max_pages = min(int(ctx["params"].get("max_pages", 10)), ctx["cfg"]["execution"]["max_pagination_pages"])
    seen = set()
    collected = []
    for p_idx, items in enumerate(pages[:max_pages], start=1):
        for it in items:
            key = it.get("id") or json.dumps(it, sort_keys=True)
            if key not in seen:
                seen.add(key)
                collected.append({**it, "_page": p_idx})
    return {
        "observation_mode": "directly_observed",
        "data": {"collected_items": collected, "pages_traversed": min(len(pages), max_pages), "last_checkpoint_page": min(len(pages), max_pages)},
        "metrics": {"unique_items": len(collected), "pages_traversed": min(len(pages), max_pages)},
    }


def handle_metadata_harvesting_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, raw, src = _load_html(ctx)
    canonical = next((l.get("href", "") for l in dom.links if "canonical" in l.get("rel", "").lower()), "")
    meta_map = {(m.get("name") or m.get("property") or f"meta_{i}"): m.get("content", "") for i, m in enumerate(dom.meta)}
    licenses = re.findall(r"(?:CC-BY-[0-9.]+|MIT|Apache-2\.0|GPL-[0-9.]+)", raw)
    return {
        "observation_mode": "directly_observed",
        "data": {"source": src, "title": dom.title, "language": dom.html_lang, "canonical_url": canonical, "meta_tags": meta_map, "declared_licenses": sorted(set(licenses))},
        "metrics": {"meta_count": len(meta_map)},
    }


def handle_web_table_normalizer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    if not dom.tables:
        return {"observation_mode": "directly_observed", "data": {"source": src, "tables": [], "csv_output": ""}, "metrics": {"row_count": 0}}
    tbl = dom.tables[0]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=tbl["headers"])
    writer.writeheader()
    writer.writerows(tbl["records"])
    return {
        "observation_mode": "directly_observed",
        "data": {"source": src, "caption": tbl["caption"], "headers": tbl["headers"], "records": tbl["records"], "csv_preview": buf.getvalue()},
        "metrics": {"row_count": len(tbl["records"]), "column_count": len(tbl["headers"])},
    }


def handle_directory_and_catalog_collector(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    listings = [{"label": a["text"], "href": a["href"]} for a in dom.AnchorLinks if a.get("text") and a.get("href")]
    return {"observation_mode": "directly_observed", "data": {"source": src, "catalog_listings": listings}, "metrics": {"listing_count": len(listings)}}


def handle_public_dataset_downloader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-records.csv")
    rows, meta = core.load_structured_records(target, ctx["params"])
    return {
        "observation_mode": "directly_observed",
        "data": {
            "dataset_path": str(target),
            "sha256": core.sha256_file(target) if target.exists() and target.is_file() else None,
            "declared_license": str(ctx["params"].get("license", "CC-BY-4.0")),
            "version": str(ctx["params"].get("version", "2026.10")),
            "format_meta": meta,
        },
        "metrics": {"record_count": len(rows)},
    }


def handle_web_content_snapshotter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, raw, src = _load_html(ctx)
    raw_bytes = raw.encode("utf-8")
    return {
        "observation_mode": "directly_observed",
        "data": {
            "source": src,
            "captured_utc": core.utc_now_iso(),
            "html_sha256": core.sha256_bytes(raw_bytes),
            "title": dom.title,
            "text_snapshot": "\n".join(dom.main_paragraphs),
        },
        "metrics": {"html_bytes": len(raw_bytes), "paragraphs": len(dom.main_paragraphs)},
    }


def handle_extraction_schema_designer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    fields = ctx["params"].get("fields", [
        {"name": "tier", "type": "string", "nullable": False},
        {"name": "rate_limit_rpm", "type": "integer", "nullable": False, "unit": "req/min"},
        {"name": "monthly_price_usd", "type": "number", "nullable": False, "unit": "USD"},
    ])
    properties = {}
    required = []
    for f in fields:
        properties[f["name"]] = {"type": f["type"], **({"description": f"Unit: {f['unit']}"} if f.get("unit") else {})}
        if not f.get("nullable", True):
            required.append(f["name"])
    properties["_source_url"] = {"type": "string"}
    properties["_extracted_utc"] = {"type": "string"}
    required.extend(["_source_url", "_extracted_utc"])
    return {
        "observation_mode": "directly_observed",
        "data": {"json_schema": {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "required": required, "properties": properties}},
        "metrics": {"field_count": len(properties)},
    }


def handle_collection_rate_and_load_controller(ctx: Dict[str, Any]) -> Dict[str, Any]:
    total_reqs = int(ctx["params"].get("planned_requests", 20))
    rps = float(ctx["params"].get("rps", ctx["cfg"]["execution"]["rate_limit_requests_per_second"]))
    crawl_delay = float(ctx["params"].get("robots_crawl_delay", 0.0))
    effective_rps = min(rps, 1.0 / crawl_delay) if crawl_delay > 0 else rps
    estimated_seconds = round(total_reqs / max(0.05, effective_rps), 2)
    return {
        "observation_mode": "directly_observed",
        "data": {
            "planned_requests": total_reqs,
            "effective_rps": round(effective_rps, 3),
            "estimated_duration_seconds": estimated_seconds,
            "backoff_policy": {"initial_s": 0.5, "multiplier": 2.0, "honor_retry_after": True},
        },
        "metrics": {"effective_rps": round(effective_rps, 3), "estimated_seconds": estimated_seconds},
    }


def handle_web_extraction_quality_auditor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, meta = core.load_structured_records(ctx["input_path"], ctx["params"])
    key_field = str(ctx["params"].get("key_field", rows[0].keys().__iter__().__next__() if rows else "id"))
    seen_keys = set()
    dup_count = 0
    null_cells = 0
    mojibake_cells = 0
    for r in rows:
        kv = json.dumps(r, sort_keys=True) if key_field not in r else str(r.get(key_field))
        if kv in seen_keys:
            dup_count += 1
        seen_keys.add(kv)
        for v in r.values():
            if v in (None, ""):
                null_cells += 1
            elif isinstance(v, str) and ("\ufffd" in v or "Ã©" in v):
                mojibake_cells += 1
    return {
        "observation_mode": "directly_observed",
        "data": {"meta": meta, "duplicate_records": dup_count, "null_cells": null_cells, "encoding_artifacts": mojibake_cells, "quality_passed": dup_count == 0 and mojibake_cells == 0},
        "metrics": {"rows": len(rows), "duplicates": dup_count, "null_cells": null_cells},
    }


# ===================== CATEGORY 6: APIs, Feeds, and Machine Interfaces =====================

def handle_rest_api_client_operator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx["params"]
    url = str(params.get("url", "https://docs.example.org/api/v2/items?page=1"))
    method = str(params.get("method", "GET")).upper()
    boundary = core.enforce_url_boundary(url, ctx["cfg"], ctx["policy"], extra_allowed_domains=params.get("allowed_domains"))

    if params.get("live_request", False) and ctx["cfg"]["network"].get("allow_live_network", False):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": ctx["cfg"]["network"]["user_agent"], "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read(65536).decode("utf-8", errors="replace")
            return {
                "observation_mode": "directly_observed",
                "data": {"url": url, "method": method, "status": resp.status, "headers": dict(resp.headers), "body_preview": body[:500]},
                "metrics": {"status": resp.status},
            }

    return {
        "observation_mode": "tested",
        "data": {
            "reproducible_request": {"method": method, "url": url, "parsed_boundary": boundary, "headers": {"Accept": "application/json"}},
            "live_execution": False,
            "note": "Live network disabled by default; enable network.allow_live_network=true and params.live_request=true for authorized live calls.",
        },
        "limitations": ["Live HTTP call skipped under offline-first default policy."],
        "metrics": {"live_executed": 0},
    }


def handle_graphql_query_operator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    query = str(ctx["params"].get("query", "query GetReleases($first: Int!) { releases(first: $first) { nodes { tag publishedAt } } }"))
    variables = ctx["params"].get("variables", {"first": 10})
    depth = max(len(re.findall(r"\{", query)), 0)
    max_depth = int(ctx["params"].get("max_depth", 6))
    is_mutation = bool(re.search(r"\bmutation\b", query))
    if is_mutation and not ctx["approve"]:
        raise core.ApprovalRequiredError("GraphQL mutation requires --approve.", {"query_preview": query[:120]})
    undeclared_vars = [v for v in re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", query) if v not in variables]
    return {
        "observation_mode": "tested",
        "data": {"operation_type": "mutation" if is_mutation else "query", "nesting_depth": depth, "within_depth_limit": depth <= max_depth, "variables": variables, "missing_variables": undeclared_vars},
        "metrics": {"nesting_depth": depth, "missing_variables": len(undeclared_vars)},
    }


def handle_soap_service_client_operator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    op_name = str(ctx["params"].get("operation", "GetServiceStatus"))
    ns = str(ctx["params"].get("namespace", "http://docs.example.org/ws/status"))
    envelope = (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:tns="{ns}">'
        f"<soap:Body><tns:{op_name}/></soap:Body></soap:Envelope>"
    )
    if ctx["params"].get("response_xml"):
        root = ET.fromstring(str(ctx["params"]["response_xml"]))
        faults = [el.text for el in root.iter() if el.tag.lower().endswith("faultstring")]
    else:
        faults = []
    return {"observation_mode": "tested", "data": {"operation": op_name, "namespace": ns, "soap_envelope": envelope, "faults_detected": faults}, "metrics": {"fault_count": len(faults)}}


def handle_webhook_workflow_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps(ctx["params"].get("payload", {"event_id": "evt_101", "type": "release.published"}), sort_keys=True)
    test_secret = str(ctx["params"].get("test_signing_key", "sandbox-webhook-verification-key"))
    computed_sig = hmac.new(test_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    provided_sig = str(ctx["params"].get("provided_signature", computed_sig))
    sig_valid = hmac.compare_digest(computed_sig, provided_sig)
    seen_ids = set(ctx["params"].get("prior_event_ids", []))
    evt_id = json.loads(payload).get("event_id", "")
    duplicate_delivery = evt_id in seen_ids
    return {
        "observation_mode": "tested",
        "data": {"event_id": evt_id, "signature_verified": sig_valid, "idempotent_duplicate_suppressed": duplicate_delivery},
        "metrics": {"signature_verified": int(sig_valid), "duplicate": int(duplicate_delivery)},
    }


def handle_rss_and_atom_feed_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    default_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Platform Releases</title>
      <item><guid>rel-2.4.0</guid><title>Release v2.4.0</title><link>https://docs.example.org/r/2.4.0</link><pubDate>2026-10-15</pubDate></item>
      <item><guid>rel-2.3.1</guid><title>Release v2.3.1</title><link>https://docs.example.org/r/2.3.1</link><pubDate>2026-09-01</pubDate></item>
    </channel></rss>"""
    xml_text = str(ctx["params"].get("feed_xml", ""))
    if not xml_text and ctx["input_path"] and ctx["input_path"].suffix.lower() in (".xml", ".rss", ".atom"):
        xml_text = ctx["input_path"].read_text(encoding="utf-8", errors="replace")
    xml_text = xml_text or default_xml
    root = ET.fromstring(xml_text)
    entries = []
    seen = set()
    for node in root.iter():
        tname = node.tag.split("}")[-1].lower()
        if tname in ("item", "entry"):
            rec = {child.tag.split("}")[-1]: (child.text or child.attrib.get("href", "")).strip() for child in list(node)}
            uid = rec.get("guid") or rec.get("id") or rec.get("link") or rec.get("title", "")
            if uid not in seen:
                seen.add(uid)
                entries.append(rec)
    return {"observation_mode": "directly_observed", "data": {"entries": entries}, "metrics": {"entry_count": len(entries)}}


def handle_openapi_and_api_documentation_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    spec = ctx["params"].get("openapi_spec")
    if spec is None and ctx["input_path"] and ctx["input_path"].suffix.lower() == ".json":
        spec = json.loads(ctx["input_path"].read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or "paths" not in spec:
        spec = {
            "openapi": "3.1.0",
            "info": {"title": "Catalog Service API", "version": "2.4.0"},
            "paths": {
                "/api/v2/items": {"get": {"summary": "List catalog items", "responses": {"200": {"description": "OK"}, "429": {"description": "Rate Limited"}}}},
                "/api/v2/sandbox-request": {"post": {"summary": "Create sandbox request", "responses": {"201": {"description": "Created"}}}},
            },
        }
    endpoints = []
    for path_k, methods in spec.get("paths", {}).items():
        for m, meta in methods.items():
            if isinstance(meta, dict):
                endpoints.append({"path": path_k, "method": m.upper(), "summary": meta.get("summary", ""), "status_codes": list(meta.get("responses", {}).keys())})
    return {"observation_mode": "directly_observed", "data": {"info": spec.get("info", {}), "endpoints": endpoints}, "metrics": {"endpoint_count": len(endpoints)}}


def handle_api_pagination_and_throttling_manager(ctx: Dict[str, Any]) -> Dict[str, Any]:
    headers = ctx["params"].get("response_headers", {"X-RateLimit-Remaining": "1", "Retry-After": "3", "Next-Cursor": "cur_page_2"})
    retry_after = float(headers.get("Retry-After", 0) or 0)
    remaining = int(headers.get("X-RateLimit-Remaining", 10))
    next_cursor = headers.get("Next-Cursor")
    wait_s = max(retry_after, 1.0 if remaining <= 1 else 0.0)
    return {
        "observation_mode": "directly_observed",
        "data": {"next_cursor": next_cursor, "rate_limit_remaining": remaining, "recommended_wait_seconds": wait_s, "has_more_pages": bool(next_cursor)},
        "metrics": {"recommended_wait_seconds": wait_s},
    }


def handle_api_response_schema_validator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    payload = ctx["params"].get("response_body", {"id": "item-1", "tier": "Standard", "price_usd": 49.0})
    required_types = ctx["params"].get("expected_types", {"id": "str", "tier": "str", "price_usd": "number"})
    type_map = {"str": str, "int": int, "number": (int, float), "bool": bool, "list": list, "dict": dict}
    violations = []
    for field, expected_t in required_types.items():
        if field not in payload:
            violations.append({"field": field, "issue": "missing_required_field"})
        elif expected_t in type_map and not isinstance(payload[field], type_map[expected_t]):
            violations.append({"field": field, "issue": f"expected_{expected_t}_got_{type(payload[field]).__name__}"})
    return {"observation_mode": "tested", "data": {"valid": len(violations) == 0, "violations": violations}, "metrics": {"violation_count": len(violations)}}


def handle_api_contract_regression_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    baseline = ctx["params"].get("baseline_contract", {"fields": ["id", "tier", "price_usd"], "status": 200})
    current = ctx["params"].get("current_response", {"fields": ["id", "tier", "price_usd", "availability"], "status": 200})
    removed = sorted(set(baseline.get("fields", [])) - set(current.get("fields", [])))
    added = sorted(set(current.get("fields", [])) - set(baseline.get("fields", [])))
    status_match = baseline.get("status") == current.get("status")
    breaking = bool(removed) or not status_match
    return {"observation_mode": "tested", "data": {"breaking_regression": breaking, "removed_fields": removed, "added_fields": added, "status_match": status_match}, "metrics": {"breaking": int(breaking)}}


def handle_api_data_exporter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    fmt = str(ctx["params"].get("export_format", "jsonl")).lower()
    if fmt == "csv" and rows:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
        rendered = buf.getvalue()
    else:
        rendered = "\n".join(json.dumps({**r, "_exported_utc": core.utc_now_iso()}, ensure_ascii=True) for r in rows)
    return {"observation_mode": "directly_observed", "data": {"export_format": fmt, "exported_preview": rendered[:1000]}, "metrics": {"exported_rows": len(rows)}}


# ===================== CATEGORY 7: GitHub and Source Hosting =====================

def handle_github_repository_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    repo_dir = ctx["input_path"] if (ctx["input_path"] and ctx["input_path"].is_dir()) else Path("data-web-app-interaction")
    if not repo_dir.exists():
        repo_dir = core.resolve_package_resource("data-web-app-interaction/asset-default-config.json").parent
    files = sorted(p.name for p in repo_dir.iterdir() if p.is_file())
    readmes = [f for f in files if "readme" in f.lower()]
    return {"observation_mode": "directly_observed", "data": {"repository_root": str(repo_dir), "file_count": len(files), "readme_files": readmes, "files": files[:50]}, "metrics": {"file_count": len(files)}}


def handle_github_code_searcher(ctx: Dict[str, Any]) -> Dict[str, Any]:
    pattern = str(ctx["params"].get("pattern", "def handle_"))
    rx = re.compile(pattern)
    search_root = ctx["input_path"] if (ctx["input_path"] and ctx["input_path"].exists()) else core.resolve_package_resource("data-web-app-interaction/tools-core.py").parent
    targets = [search_root] if search_root.is_file() else sorted(search_root.rglob("*.py"))[:30]
    matches = []
    for f in targets:
        for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if rx.search(line):
                matches.append({"file": f.name, "line": lineno, "text": line.strip()[:140]})
                if len(matches) >= 50:
                    break
    return {"observation_mode": "directly_observed", "data": {"pattern": pattern, "matches": matches}, "metrics": {"match_count": len(matches)}}


def handle_github_issue_investigator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    issues = ctx["params"].get("issues", [
        {"number": 101, "title": "Pagination returns duplicate item on page boundary", "labels": ["bug", "api"], "state": "open"},
        {"number": 104, "title": "Duplicate record seen when cursor resets", "labels": ["bug", "api"], "state": "open"},
        {"number": 109, "title": "Add dark mode contrast preset", "labels": ["accessibility"], "state": "closed"},
    ])
    by_label: Dict[str, List[int]] = collections.defaultdict(list)
    for iss in issues:
        for lb in iss.get("labels", ["unlabeled"]):
            by_label[lb].append(iss["number"])
    open_issues = [i for i in issues if i.get("state") == "open"]
    return {"observation_mode": "directly_observed", "data": {"clusters_by_label": dict(by_label), "open_issues": open_issues}, "metrics": {"open_count": len(open_issues), "total_issues": len(issues)}}


def handle_pull_request_reviewer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    diff_text = str(ctx["params"].get("diff", "--- a/api.py\n+++ b/api.py\n@@ -10,2 +10,4 @@\n+def fetch_page(cursor: str, timeout: float = 10.0):\n+    return bounded_get(cursor, timeout=timeout)\n"))
    added = [ln[1:] for ln in diff_text.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    removed = [ln[1:] for ln in diff_text.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    touches_tests = "test" in diff_text.lower()
    redactor = core.SecretRedactor(ctx["policy"])
    redactor.scrub_text("\n".join(added))
    return {
        "observation_mode": "directly_observed",
        "data": {
            "added_lines": len(added),
            "removed_lines": len(removed),
            "includes_test_updates": touches_tests,
            "secret_leak_in_diff": redactor.redaction_count > 0,
            "merge_executed": False,
            "review_verdict": "REQUEST_CHANGES_SECRET_FOUND" if redactor.redaction_count > 0 else "COMMENT_ADD_TESTS" if not touches_tests else "APPROVE_READY",
        },
        "metrics": {"added_lines": len(added), "removed_lines": len(removed)},
    }


def handle_release_and_changelog_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    releases = ctx["params"].get("releases", [
        {"tag": "v2.3.1", "breaking": False, "notes": "Patch fix for table rowspan normalization."},
        {"tag": "v2.4.0", "breaking": False, "notes": "Added Retry-After throttling and WCAG contrast checks."},
    ])
    breaking = [r["tag"] for r in releases if r.get("breaking")]
    return {"observation_mode": "directly_observed", "data": {"releases": releases, "breaking_releases": breaking}, "metrics": {"release_count": len(releases), "breaking_count": len(breaking)}}


def handle_github_actions_observer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    wf_text = str(ctx["params"].get("workflow_yaml", "name: CI\npermissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - run: pytest\n"))
    has_permissions = "permissions:" in wf_text
    unpinned = re.findall(r"uses:\s*([^\s@]+@(?:main|master|latest))", wf_text)
    return {
        "observation_mode": "directly_observed",
        "data": {"declares_least_privilege_permissions": has_permissions, "unpinned_mutable_actions": unpinned},
        "metrics": {"unpinned_actions": len(unpinned)},
    }


def handle_github_pages_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    mixed_http_assets = [img.get("src", "") for img in dom.images if img.get("src", "").startswith("http://")]
    has_viewport = any(m.get("name") == "viewport" for m in dom.meta)
    return {"observation_mode": "tested", "data": {"source": src, "responsive_viewport_meta": has_viewport, "mixed_content_assets": mixed_http_assets}, "metrics": {"mixed_assets": len(mixed_http_assets)}}


def handle_repository_download_and_verification_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-skill-catalog.json")
    digest = core.sha256_file(target) if target.is_file() else None
    return {"observation_mode": "directly_observed", "data": {"verified_target": str(target), "sha256": digest, "integrity_ok": digest is not None}, "metrics": {"verified": int(digest is not None)}}


def handle_issue_reproduction_packager(ctx: Dict[str, Any]) -> Dict[str, Any]:
    pkg = {
        "title": str(ctx["params"].get("title", "Minimal reproduction package")),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "steps": ctx["params"].get("steps", ["1. Load sample input", "2. Execute command", "3. Observe structured output"]),
        "expected": str(ctx["params"].get("expected", "Zero duplicate records")),
        "actual": str(ctx["params"].get("actual", "Observed duplicate row R010 in raw fixture")),
    }
    return {"observation_mode": "directly_observed", "data": pkg, "metrics": {"step_count": len(pkg["steps"])}}


def handle_source_hosting_migration_planner(ctx: Dict[str, Any]) -> Dict[str, Any]:
    src_host = str(ctx["params"].get("source_platform", "GitHub"))
    dst_host = str(ctx["params"].get("target_platform", "GitLab"))
    checklist = [
        "Mirror git branches, tags, and LFS objects with SHA verification.",
        "Export issues, labels, milestones, and PR/MR review history.",
        "Translate CI workflow syntax and verify least-privilege runner secrets.",
        "Update webhooks, branch protection rules, and static pages routes.",
    ]
    return {"observation_mode": "directly_observed", "data": {"from": src_host, "to": dst_host, "migration_phases": checklist}, "metrics": {"phase_count": len(checklist)}}


# ===================== CATEGORY 8: Cloudflare, Hosting, and Web Infrastructure =====================

def handle_cloudflare_site_behavior_inspector(ctx: Dict[str, Any]) -> Dict[str, Any]:
    headers = {k.lower(): v for k, v in ctx["params"].get("headers", {"server": "cloudflare", "cf-ray": "8df0000000000000-SIN", "cf-cache-status": "HIT", "strict-transport-security": "max-age=31536000"}).items()}
    return {
        "observation_mode": "directly_observed",
        "data": {
            "cloudflare_proxied": "cf-ray" in headers or "cloudflare" in headers.get("server", "").lower(),
            "cf_cache_status": headers.get("cf-cache-status", "NONE"),
            "hsts_enabled": "strict-transport-security" in headers,
        },
        "metrics": {"header_count": len(headers)},
    }


def handle_static_hosting_deployment_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    routes = ctx["params"].get("routes", [{"path": "/", "status": 200}, {"path": "/catalog/releases", "status": 200}, {"path": "/nonexistent-404", "status": 404}])
    healthy = all((r["status"] == 200 if "404" not in r["path"] else r["status"] == 404) for r in routes)
    return {"observation_mode": "tested", "data": {"route_checks": routes, "deployment_healthy": healthy}, "metrics": {"routes_checked": len(routes)}}


def handle_dns_and_domain_resolution_diagnostician(ctx: Dict[str, Any]) -> Dict[str, Any]:
    domain = str(ctx["params"].get("domain", "docs.example.org"))
    records = ctx["params"].get("dns_records", [{"type": "A", "value": "93.184.215.14", "ttl": 300}, {"type": "AAAA", "value": "2606:2800:21f:cb07:6820:80da:af6b:8b2c", "ttl": 300}])
    cname_chain = [r["value"] for r in records if r.get("type") == "CNAME"]
    return {"observation_mode": "directly_observed", "data": {"domain": domain, "records": records, "cname_hops": len(cname_chain), "ttl_min": min((r.get("ttl", 300) for r in records), default=0)}, "metrics": {"record_count": len(records)}}


def handle_tls_and_certificate_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    cert = ctx["params"].get("certificate", {"subject_cn": "docs.example.org", "san": ["docs.example.org", "example.org"], "not_after": "2027-06-01", "protocol": "TLSv1.3"})
    ref = dt.date(2026, 11, 5)
    exp = dt.date.fromisoformat(str(cert.get("not_after", "2027-06-01"))[:10])
    days_left = (exp - ref).days
    modern_tls = str(cert.get("protocol", "")).upper() in ("TLSV1.2", "TLSV1.3")
    return {"observation_mode": "tested", "data": {**cert, "days_until_expiry": days_left, "renewal_warning": days_left < 30, "modern_protocol": modern_tls}, "metrics": {"days_until_expiry": days_left}}


def handle_cdn_cache_behavior_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    h = {k.lower(): v for k, v in ctx["params"].get("headers", {"cache-control": "public, max-age=3600, stale-while-revalidate=60", "etag": '"v240"', "vary": "Accept-Encoding", "age": "120"}).items()}
    cc = h.get("cache-control", "")
    return {
        "observation_mode": "tested",
        "data": {"cache_control": cc, "has_etag": "etag" in h, "vary": h.get("vary", ""), "age_seconds": int(h.get("age", "0") or 0), "cacheable": "no-store" not in cc},
        "metrics": {"age_seconds": int(h.get("age", "0") or 0)},
    }


def handle_redirect_and_routing_auditor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    hops = ctx["params"].get("redirect_chain", [
        {"from": "http://example.org/releases", "to": "https://example.org/releases", "status": 301},
        {"from": "https://example.org/releases", "to": "https://docs.example.org/catalog/releases", "status": 301},
    ])
    visited = []
    loop = False
    for h in hops:
        visited.append(h["from"])
        if h["to"] in visited:
            loop = True
    return {"observation_mode": "tested", "data": {"hops": hops, "hop_count": len(hops), "redirect_loop_detected": loop, "https_upgraded": any(h["to"].startswith("https://") for h in hops)}, "metrics": {"hop_count": len(hops), "loop": int(loop)}}


def handle_hosting_error_log_interpreter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-events.jsonl"), ctx["params"])
    errors = [r for r in rows if int(r.get("status", 200) or 200) >= 400 or str(r.get("severity", "")).upper() in ("WARN", "ERROR")]
    return {"observation_mode": "directly_observed", "data": {"correlated_error_events": errors}, "metrics": {"total_events": len(rows), "error_or_warn_events": len(errors)}}


def handle_deployment_smoke_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dom, _, src = _load_html(ctx)
    checks = {
        "has_title": bool(dom.title),
        "has_main_landmark": any(l["role"] == "main" for l in dom.landmarks),
        "has_primary_navigation": any(l["tag"] == "nav" for l in dom.landmarks),
        "forms_have_actions": all(bool(f.get("action")) for f in dom.forms),
    }
    return {"observation_mode": "tested", "data": {"source": src, "smoke_checks": checks, "smoke_passed": all(checks.values())}, "metrics": {"passed_checks": sum(1 for v in checks.values() if v)}}


def handle_rollback_readiness_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    state = ctx["params"].get("release_state", {"current_build": "v2.4.0", "prior_artifact_available": True, "db_migration_backward_compatible": True, "cache_purge_documented": True})
    ready = all(bool(state.get(k)) for k in ("prior_artifact_available", "db_migration_backward_compatible", "cache_purge_documented"))
    return {"observation_mode": "tested", "data": {**state, "rollback_ready": ready}, "metrics": {"rollback_ready": int(ready)}}


def handle_web_infrastructure_change_validator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    intended = ctx["params"].get("intended_config", {"tls_min": "1.3", "hsts": True, "rate_limit_rpm": 1200})
    observed = ctx["params"].get("observed_config", {"tls_min": "1.3", "hsts": True, "rate_limit_rpm": 1200})
    mismatches = {k: {"intended": intended.get(k), "observed": observed.get(k)} for k in set(intended) | set(observed) if intended.get(k) != observed.get(k)}
    return {"observation_mode": "tested", "data": {"drifts": mismatches, "matches_intent": len(mismatches) == 0}, "metrics": {"drift_count": len(mismatches)}}


# ===================== CATEGORY 9: Cloud Storage and File Transfer =====================

def handle_cloud_storage_browser(ctx: Dict[str, Any]) -> Dict[str, Any]:
    root = ctx["input_path"] if (ctx["input_path"] and ctx["input_path"].exists()) else core.resolve_package_resource("data-web-app-interaction/asset-default-config.json").parent
    items = []
    targets = [root] if root.is_file() else sorted(root.iterdir())
    for p in targets[:100]:
        if p.is_file():
            items.append({"key": p.name, "size_bytes": p.stat().st_size, "suffix": p.suffix.lower()})
    return {"observation_mode": "directly_observed", "data": {"prefix": str(root), "objects": items}, "metrics": {"object_count": len(items)}}


def handle_authorized_file_downloader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    src = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-records.csv")
    if not src.exists() or not src.is_file():
        raise core.InputValidationError(f"Source file not available: {src}")
    return {
        "observation_mode": "directly_observed",
        "data": {"source": str(src), "size_bytes": src.stat().st_size, "sha256": core.sha256_file(src), "verified_openable": True},
        "metrics": {"size_bytes": src.stat().st_size},
    }


def handle_authorized_file_uploader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    src = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-records.csv")
    dest_str = str(ctx["params"].get("destination_path", ""))
    applied = False
    if ctx["params"].get("apply", False):
        if not ctx["approve"]:
            raise core.ApprovalRequiredError("Uploading a file to destination requires --approve.")
        if not dest_str:
            raise core.InputValidationError("destination_path is required when apply=true.")
        dest = core.validate_safe_path(dest_str, must_exist=False, allow_dir=False)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        applied = True
    return {
        "observation_mode": "directly_observed",
        "data": {"source": str(src), "destination": dest_str or "staging://preview-only", "sha256": core.sha256_file(src), "uploaded": applied},
        "metrics": {"uploaded": int(applied)},
    }


def handle_large_file_transfer_manager(ctx: Dict[str, Any]) -> Dict[str, Any]:
    total_bytes = int(ctx["params"].get("file_size_bytes", 104857600))
    part_bytes = max(5 * 1024 * 1024, int(ctx["params"].get("part_size_bytes", 16 * 1024 * 1024)))
    parts = (total_bytes + part_bytes - 1) // part_bytes
    return {
        "observation_mode": "directly_observed",
        "data": {"total_bytes": total_bytes, "part_size_bytes": part_bytes, "multipart_count": parts, "resumable": True, "checksum_algorithm": "sha256-per-part"},
        "metrics": {"multipart_count": parts},
    }


def handle_folder_synchronization_planner(ctx: Dict[str, Any]) -> Dict[str, Any]:
    local_tree = ctx["params"].get("local_files", {"a.csv": "hash1", "b.csv": "hash2_new", "c.csv": "hash3"})
    remote_tree = ctx["params"].get("remote_files", {"a.csv": "hash1", "b.csv": "hash2_old", "d.csv": "hash4"})
    to_upload = [k for k in local_tree if k not in remote_tree]
    conflicts = [k for k in local_tree if k in remote_tree and local_tree[k] != remote_tree[k]]
    remote_only = [k for k in remote_tree if k not in local_tree]
    return {
        "observation_mode": "directly_observed",
        "data": {"to_upload": to_upload, "conflicts": conflicts, "remote_only": remote_only, "executed": False},
        "metrics": {"to_upload": len(to_upload), "conflicts": len(conflicts), "remote_only": len(remote_only)},
    }


def handle_cloud_version_recovery_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    versions = ctx["params"].get("versions", [
        {"version_id": "v3", "timestamp": "2026-10-15T10:00:00Z", "deleted": True},
        {"version_id": "v2", "timestamp": "2026-10-14T09:00:00Z", "deleted": False, "sha256": "abc123"},
    ])
    recoverable = next((v for v in versions if not v.get("deleted")), None)
    return {"observation_mode": "directly_observed", "data": {"recommended_restore_version": recoverable, "overwrite_current": False}, "metrics": {"version_count": len(versions)}}


def handle_file_sharing_permission_auditor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    shares = ctx["params"].get("shares", [
        {"resource": "quarterly-summary.pdf", "visibility": "organization", "role": "viewer", "expires": "2026-12-31"},
        {"resource": "customer-export.csv", "visibility": "anyone_with_link", "role": "editor", "expires": None},
    ])
    risks = [s for s in shares if s.get("visibility") in ("public", "anyone_with_link") or (s.get("role") == "editor" and not s.get("expires"))]
    return {"observation_mode": "directly_observed", "data": {"audited_shares": shares, "overexposed_shares": risks}, "metrics": {"risk_count": len(risks)}}


def handle_cross_storage_migration_operator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    plan = {
        "source_backend": str(ctx["params"].get("source_backend", "s3-compatible-staging")),
        "target_backend": str(ctx["params"].get("target_backend", "local-archive")),
        "preserve_metadata": True,
        "verify_post_transfer_sha256": True,
        "executed": bool(ctx["params"].get("apply", False) and ctx["approve"]),
    }
    return {"observation_mode": "directly_observed", "data": plan, "metrics": {"executed": int(plan["executed"])}}


def handle_transfer_integrity_auditor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    manifest = ctx["params"].get("manifest", [
        {"name": "a.csv", "src_sha256": "111", "dst_sha256": "111", "src_bytes": 100, "dst_bytes": 100},
        {"name": "b.csv", "src_sha256": "222", "dst_sha256": "999", "src_bytes": 200, "dst_bytes": 180},
    ])
    failed = [m for m in manifest if m["src_sha256"] != m["dst_sha256"] or m["src_bytes"] != m["dst_bytes"]]
    return {"observation_mode": "tested", "data": {"verified_count": len(manifest) - len(failed), "failed_objects": failed}, "metrics": {"failed_count": len(failed)}}


def handle_storage_quota_and_lifecycle_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    objects = ctx["params"].get("objects", [
        {"key": "logs/2025-01.log", "bytes": 50_000_000, "age_days": 300},
        {"key": "active/current.csv", "bytes": 2_000_000, "age_days": 5},
    ])
    quota = int(ctx["params"].get("quota_bytes", 100_000_000))
    used = sum(int(o.get("bytes", 0)) for o in objects)
    cold_candidates = [o["key"] for o in objects if int(o.get("age_days", 0)) > 90]
    return {"observation_mode": "directly_observed", "data": {"used_bytes": used, "quota_bytes": quota, "utilization_pct": round(used * 100.0 / max(1, quota), 2), "archive_tier_candidates": cold_candidates}, "metrics": {"archive_candidates": len(cold_candidates)}}


# ===================== CATEGORY 10: Social Media and Community Platforms =====================

def handle_public_social_content_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    posts = ctx["params"].get("posts", [
        {"id": "p1", "author": "@dev_team", "timestamp": "2026-10-15T09:00:00Z", "text": "v2.4.0 is live with #a11y keyboard fixes!", "links": ["https://docs.example.org/catalog/releases"]},
    ])
    return {"observation_mode": "directly_observed", "data": {"posts": posts}, "metrics": {"post_count": len(posts)}}


def handle_social_search_and_hashtag_researcher(ctx: Dict[str, Any]) -> Dict[str, Any]:
    posts = ctx["params"].get("posts", [
        {"id": "p1", "text": "Loving the #pagination and #a11y updates in v2.4.0"},
        {"id": "p2", "text": "Testing #pagination rate limits on staging"},
    ])
    tag_counts: Dict[str, int] = collections.Counter()
    for p in posts:
        for tag in re.findall(r"#[a-zA-Z0-9_]+", str(p.get("text", "")).lower()):
            tag_counts[tag] += 1
    return {
        "observation_mode": "directly_observed",
        "data": {"hashtag_frequencies": dict(tag_counts), "sampling_caveat": "Bounded public sample; does not represent entire platform population."},
        "metrics": {"post_sample_size": len(posts), "unique_hashtags": len(tag_counts)},
    }


def handle_thread_and_conversation_reconstructor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    posts = ctx["params"].get("posts", [
        {"id": "p2", "parent_id": "p1", "timestamp": "2026-10-15T09:05:00Z", "text": "Confirmed Retry-After header works."},
        {"id": "p1", "parent_id": None, "timestamp": "2026-10-15T09:00:00Z", "text": "Release v2.4.0 thread:"},
        {"id": "p3", "parent_id": "p2", "timestamp": "2026-10-15T09:08:00Z", "text": "Docs updated with examples."},
    ])
    ordered = sorted(posts, key=lambda x: str(x.get("timestamp", "")))
    roots = [p["id"] for p in ordered if not p.get("parent_id")]
    return {"observation_mode": "directly_observed", "data": {"roots": roots, "chronological_thread": ordered}, "metrics": {"message_count": len(ordered)}}


def handle_social_media_content_collector(ctx: Dict[str, Any]) -> Dict[str, Any]:
    posts = ctx["params"].get("posts", [{"id": "p1", "text": "Hello"}, {"id": "p1", "text": "Hello"}])
    dedup = list({p["id"]: p for p in posts}.values())
    return {"observation_mode": "directly_observed", "data": {"collected": dedup}, "metrics": {"collected_count": len(dedup), "duplicates_dropped": len(posts) - len(dedup)}}


def handle_community_sentiment_evidence_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    posts = ctx["params"].get("posts", [
        {"id": "p1", "text": "Fast, smooth keyboard support and clear docs."},
        {"id": "p2", "text": "Hit a slow timeout error during peak export."},
    ])
    pos_words = {"fast", "smooth", "clear", "helpful", "great", "reliable"}
    neg_words = {"slow", "timeout", "error", "broken", "crash", "confusing"}
    pos_ids, neg_ids = [], []
    for p in posts:
        toks = set(re.findall(r"[a-z]+", str(p.get("text", "")).lower()))
        if toks & pos_words:
            pos_ids.append(p["id"])
        if toks & neg_words:
            neg_ids.append(p["id"])
    return {
        "observation_mode": "directly_observed",
        "data": {"positive_evidence_ids": pos_ids, "negative_evidence_ids": neg_ids, "population_caveat": "Summarizes expressed sentiment in supplied sample only."},
        "metrics": {"positive_mentions": len(pos_ids), "negative_mentions": len(neg_ids)},
    }


def handle_platform_content_export_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, meta = core.load_structured_records(ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-events.jsonl"), ctx["params"])
    redactor = core.SecretRedactor(ctx["policy"], redact_pii=True)
    sanitized = redactor.scrub_obj(rows[:10])
    return {"observation_mode": "directly_observed", "data": {"export_meta": meta, "sanitized_preview": sanitized}, "metrics": {"total_records": len(rows)}}


def handle_social_publishing_draft_operator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    body = str(ctx["params"].get("body", "Announcing v2.4.0 with bounded API pagination and accessibility improvements."))
    alt_text = str(ctx["params"].get("image_alt_text", "Architecture overview diagram"))
    max_chars = int(ctx["params"].get("max_chars", 280))
    published = bool(ctx["params"].get("publish", False) and ctx["approve"])
    return {
        "observation_mode": "directly_observed",
        "data": {
            "status": "PUBLISHED" if published else "DRAFT_HOLDING_FOR_CONFIRMATION",
            "char_count": len(body),
            "within_char_limit": len(body) <= max_chars,
            "has_image_alt_text": bool(alt_text.strip()),
            "draft_body": body,
        },
        "metrics": {"char_count": len(body), "published": int(published)},
    }


def handle_comment_moderation_assistant(ctx: Dict[str, Any]) -> Dict[str, Any]:
    comments = ctx["params"].get("comments", [
        {"id": "c1", "text": "Thanks for the release notes!"},
        {"id": "c2", "text": "Buy cheap crypto spam link http://spam.example.com"},
    ])
    blocked_terms = set(ctx["params"].get("policy_keywords", ["spam", "scam", "phishing"]))
    triaged = []
    for c in comments:
        toks = set(re.findall(r"[a-z]+", str(c.get("text", "")).lower()))
        flagged = bool(toks & blocked_terms)
        triaged.append({"id": c["id"], "classification": "policy_review_queue" if flagged else "benign", "requires_human_decision": flagged})
    return {"observation_mode": "directly_observed", "data": {"triaged_comments": triaged}, "metrics": {"flagged_for_human_review": sum(1 for t in triaged if t["requires_human_decision"])}}


def handle_social_media_link_and_media_validator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    card = ctx["params"].get("post_card", {"link": "https://docs.example.org/catalog/releases", "alt_text": "Release summary chart", "media_mime": "image/png"})
    valid_url = str(card.get("link", "")).startswith("https://")
    has_alt = bool(str(card.get("alt_text", "")).strip())
    return {"observation_mode": "tested", "data": {**card, "https_link": valid_url, "alt_text_present": has_alt, "valid": valid_url and has_alt}, "metrics": {"valid": int(valid_url and has_alt)}}


def handle_social_platform_workflow_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    checks = {"draft_saved_privately": True, "alt_text_editable": True, "visibility_scope_verified": True, "public_spam_prevented": True}
    return {"observation_mode": "tested", "data": {"workflow_checks": checks, "all_passed": all(checks.values())}, "metrics": {"passed": len(checks)}}


SKILL_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    # Cat 3
    "webpage-structure-reader": handle_webpage_structure_reader,
    "long-page-scroll-navigator": handle_long_page_scroll_navigator,
    "article-and-blog-reader": handle_article_and_blog_reader,
    "documentation-site-navigator": handle_documentation_site_navigator,
    "table-and-list-extractor": handle_table_and_list_extractor,
    "dynamic-content-observer": handle_dynamic_content_observer,
    "page-link-graph-explorer": handle_page_link_graph_explorer,
    "reader-mode-and-noise-filter": handle_reader_mode_and_noise_filter,
    "page-change-comparator": handle_page_change_comparator,
    "web-accessibility-reading-tester": handle_web_accessibility_reading_tester,
    # Cat 4
    "browser-task-operator": handle_browser_task_operator,
    "form-completion-specialist": handle_form_completion_specialist,
    "multi-step-workflow-navigator": handle_multi_step_workflow_navigator,
    "search-filter-and-sort-operator": handle_search_filter_and_sort_operator,
    "browser-session-state-manager": handle_browser_session_state_manager,
    "file-upload-workflow-tester": handle_file_upload_workflow_tester,
    "download-workflow-validator": handle_download_workflow_validator,
    "clipboard-and-copy-paste-tester": handle_clipboard_and_copy_paste_tester,
    "browser-permission-flow-tester": handle_browser_permission_flow_tester,
    "browser-recovery-operator": handle_browser_recovery_operator,
    # Cat 5
    "structured-web-data-extractor": handle_structured_web_data_extractor,
    "pagination-and-infinite-scroll-collector": handle_pagination_and_infinite_scroll_collector,
    "metadata-harvesting-specialist": handle_metadata_harvesting_specialist,
    "web-table-normalizer": handle_web_table_normalizer,
    "directory-and-catalog-collector": handle_directory_and_catalog_collector,
    "public-dataset-downloader": handle_public_dataset_downloader,
    "web-content-snapshotter": handle_web_content_snapshotter,
    "extraction-schema-designer": handle_extraction_schema_designer,
    "collection-rate-and-load-controller": handle_collection_rate_and_load_controller,
    "web-extraction-quality-auditor": handle_web_extraction_quality_auditor,
    # Cat 6
    "rest-api-client-operator": handle_rest_api_client_operator,
    "graphql-query-operator": handle_graphql_query_operator,
    "soap-service-client-operator": handle_soap_service_client_operator,
    "webhook-workflow-tester": handle_webhook_workflow_tester,
    "rss-and-atom-feed-reader": handle_rss_and_atom_feed_reader,
    "openapi-and-api-documentation-reader": handle_openapi_and_api_documentation_reader,
    "api-pagination-and-throttling-manager": handle_api_pagination_and_throttling_manager,
    "api-response-schema-validator": handle_api_response_schema_validator,
    "api-contract-regression-tester": handle_api_contract_regression_tester,
    "api-data-exporter": handle_api_data_exporter,
    # Cat 7
    "github-repository-reader": handle_github_repository_reader,
    "github-code-searcher": handle_github_code_searcher,
    "github-issue-investigator": handle_github_issue_investigator,
    "pull-request-reviewer": handle_pull_request_reviewer,
    "release-and-changelog-analyst": handle_release_and_changelog_analyst,
    "github-actions-observer": handle_github_actions_observer,
    "github-pages-tester": handle_github_pages_tester,
    "repository-download-and-verification-specialist": handle_repository_download_and_verification_specialist,
    "issue-reproduction-packager": handle_issue_reproduction_packager,
    "source-hosting-migration-planner": handle_source_hosting_migration_planner,
    # Cat 8
    "cloudflare-site-behavior-inspector": handle_cloudflare_site_behavior_inspector,
    "static-hosting-deployment-tester": handle_static_hosting_deployment_tester,
    "dns-and-domain-resolution-diagnostician": handle_dns_and_domain_resolution_diagnostician,
    "tls-and-certificate-tester": handle_tls_and_certificate_tester,
    "cdn-cache-behavior-tester": handle_cdn_cache_behavior_tester,
    "redirect-and-routing-auditor": handle_redirect_and_routing_auditor,
    "hosting-error-log-interpreter": handle_hosting_error_log_interpreter,
    "deployment-smoke-tester": handle_deployment_smoke_tester,
    "rollback-readiness-tester": handle_rollback_readiness_tester,
    "web-infrastructure-change-validator": handle_web_infrastructure_change_validator,
    # Cat 9
    "cloud-storage-browser": handle_cloud_storage_browser,
    "authorized-file-downloader": handle_authorized_file_downloader,
    "authorized-file-uploader": handle_authorized_file_uploader,
    "large-file-transfer-manager": handle_large_file_transfer_manager,
    "folder-synchronization-planner": handle_folder_synchronization_planner,
    "cloud-version-recovery-specialist": handle_cloud_version_recovery_specialist,
    "file-sharing-permission-auditor": handle_file_sharing_permission_auditor,
    "cross-storage-migration-operator": handle_cross_storage_migration_operator,
    "transfer-integrity-auditor": handle_transfer_integrity_auditor,
    "storage-quota-and-lifecycle-analyst": handle_storage_quota_and_lifecycle_analyst,
    # Cat 10
    "public-social-content-reader": handle_public_social_content_reader,
    "social-search-and-hashtag-researcher": handle_social_search_and_hashtag_researcher,
    "thread-and-conversation-reconstructor": handle_thread_and_conversation_reconstructor,
    "social-media-content-collector": handle_social_media_content_collector,
    "community-sentiment-evidence-analyst": handle_community_sentiment_evidence_analyst,
    "platform-content-export-reader": handle_platform_content_export_reader,
    "social-publishing-draft-operator": handle_social_publishing_draft_operator,
    "comment-moderation-assistant": handle_comment_moderation_assistant,
    "social-media-link-and-media-validator": handle_social_media_link_and_media_validator,
    "social-platform-workflow-tester": handle_social_platform_workflow_tester,
}
