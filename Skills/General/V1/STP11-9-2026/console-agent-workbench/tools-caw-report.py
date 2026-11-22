#!/usr/bin/env python3
"""caw_report - preservation report rendering with artifact minimization.

Consumes the JSON envelopes produced by the other workbench commands and
renders one report as Markdown, self-contained HTML (inline CSS, no external
resources), or structured JSON.

Rendering pipeline
------------------
1. classify each supplied JSON payload by its ``command``/``record_type``
2. build a section list from the data actually supplied (nothing is invented)
3. substitute those sections into ``asset-report-template.md``
4. drop sections that ended up empty, so the report has no placeholder text
5. apply the policy redaction rules to every string
"""

from __future__ import annotations

import html as _html
import os
import re

import caw_runtime as rt

TEMPLATE_FILENAME = "asset-report-template.md"

MAX_TABLE_ROWS = 200


# ---------------------------------------------------------------------------
# Input classification
# ---------------------------------------------------------------------------
def _kind_of(payload):
    if not isinstance(payload, dict):
        return "unknown"
    cmd = payload.get("command")
    data = payload.get("data", payload)
    if isinstance(data, dict):
        rtype = data.get("record_type")
        if rtype == "provenance":
            return "provenance"
        if rtype == "authorization":
            return "authorization"
        if rtype == "approval":
            return "approval"
        if "verdict" in data and "findings" in data:
            return "scan"
        if "results" in data and "files_checked" in data:
            return "verify"
        if "capabilities" in data and "host" in data:
            return "inventory"
        if "checks" in data:
            return "selftest"
        if "stages" in data and "goal" in data:
            return "plan"
        if "selected_profile" in data:
            return "route"
        if "detected_format" in data:
            return "inspect"
        if "file_count" in data and "manifest_sha256" in data:
            return "hash"
    if cmd in ("scan", "verify", "inventory", "selftest", "plan", "route", "inspect",
               "hash", "provenance"):
        return cmd
    return "unknown"


def collect(input_paths):
    sources = []
    for raw in input_paths:
        data = rt.load_json(raw, "report input")
        sources.append({"source": os.path.basename(raw), "kind": _kind_of(data), "payload": data})
    return sources


# ---------------------------------------------------------------------------
# Section builders (Markdown fragments)
# ---------------------------------------------------------------------------
def _md_escape(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table(columns, rows):
    if not rows:
        return "_None recorded._"
    width = len(columns)
    out = ["| " + " | ".join(_md_escape(c) for c in columns) + " |",
           "|" + "|".join(["---"] * width) + "|"]
    for row in rows[:MAX_TABLE_ROWS]:
        cells = list(row) + [""] * (width - len(row))
        out.append("| " + " | ".join(_md_escape(c) for c in cells[:width]) + " |")
    if len(rows) > MAX_TABLE_ROWS:
        out.append(f"| _truncated_ | {len(rows) - MAX_TABLE_ROWS} more rows |")
    return "\n".join(out)


def _bullets(items):
    items = [i for i in items if i not in (None, "")]
    if not items:
        return "_None recorded._"
    return "\n".join(f"- {i}" for i in items)


def _data(source):
    payload = source["payload"]
    return payload.get("data", payload) if isinstance(payload, dict) else {}


def _section_summary(sources, meta):
    counts = {}
    for s in sources:
        counts[s["kind"]] = counts.get(s["kind"], 0) + 1
    rows = [[k, v] for k, v in sorted(counts.items())]
    if not rows:
        lines = ["No evidence inputs were supplied; this report therefore contains only the "
                 "front matter and the operator's own statements."]
    else:
        lines = [
            "Evidence inputs consumed by this report:",
            "",
            _table(["Input kind", "Count"], rows),
        ]
    verdicts = []
    for s in sources:
        data = _data(s)
        if s["kind"] == "scan":
            verdicts.append(f"guardrail verdict for `{s['source']}`: **{data.get('verdict')}**")
        if s["kind"] == "verify":
            verdicts.append(f"DAT verification: {data.get('verified')}/{data.get('files_checked')} "
                            f"files verified")
        if s["kind"] == "inventory":
            summary = data.get("summary") or {}
            verdicts.append("capability inventory: " + ", ".join(
                f"{k}={v}" for k, v in sorted(summary.items()) if k != "total"))
    if verdicts:
        lines.extend(["", "Headline results:", "", _bullets(verdicts)])
    return "\n".join(lines)


def _section_capabilities(sources):
    rows = []
    for s in sources:
        if s["kind"] not in ("inventory", "selftest"):
            continue
        data = _data(s)
        for cap in data.get("capabilities", []) or []:
            rows.append([cap.get("capability_id"), cap.get("status"),
                         cap.get("detected_version") or cap.get("test_result", ""),
                         cap.get("priority", "")])
        for check in data.get("checks", []) or []:
            rows.append([check.get("check_id"), check.get("status"), check.get("detail", ""), "local"])
    if not rows:
        return ""
    return _table(["Capability / check", "Status", "Evidence", "Priority"], rows)


def _section_artifacts(sources):
    rows = []
    for s in sources:
        data = _data(s)
        if s["kind"] == "provenance":
            integ = data.get("integrity", {})
            material = data.get("material", {})
            rows.append([
                material.get("reference"), material.get("platform") or material.get("media_type"),
                integ.get("size"), integ.get("sha256"),
                (data.get("dump") or {}).get("method"),
                (data.get("material") or {}).get("region"),
                (data.get("material") or {}).get("revision"),
            ])
        elif s["kind"] == "inspect":
            artifacts = data.get("artifacts") or [data]
            for art in artifacts:
                rows.append([art.get("file_name"), art.get("platform"),
                             art.get("size"), (art.get("hashes") or {}).get("sha256"),
                             art.get("detected_format"), "", ""])
        elif s["kind"] == "hash":
            for entry in (data.get("entries") or [])[:MAX_TABLE_ROWS]:
                rows.append([entry.get("path"), "", entry.get("size"),
                             (entry.get("hashes") or {}).get("sha256"), "manifest", "", ""])
    if not rows:
        return ""
    deduped = []
    seen = set()
    for row in rows:
        key = (row[0], row[3])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return _table(["Artifact", "Platform / media", "Size (bytes)", "SHA-256", "Method",
                   "Region", "Revision"], deduped)


def _section_verification(sources):
    rows = []
    notes = []
    for s in sources:
        data = _data(s)
        if s["kind"] != "verify":
            continue
        notes.append(f"`{s['source']}` used DAT **{data.get('dat_name')}** "
                     f"version {data.get('dat_version')} "
                     f"({data.get('dat_entries')} entries).")
        for rec in data.get("results", []) or []:
            match = rec.get("match") or {}
            rows.append([rec.get("path"), rec.get("status"), match.get("name") or "",
                         rec.get("match_method") or "", rec.get("note") or ""])
    if not rows and not notes:
        return ""
    return "\n\n".join([_bullets(notes), _table(
        ["File", "Status", "DAT entry", "Match method", "Note"], rows)])


def _section_guardrails(sources):
    rows = []
    verdicts = []
    for s in sources:
        data = _data(s)
        if s["kind"] != "scan":
            continue
        verdicts.append(f"`{s['source']}`: verdict **{data.get('verdict')}** "
                        f"(mode {data.get('mode')}, {data.get('files_scanned')} files scanned, "
                        f"{data.get('finding_count')} findings)")
        for f in data.get("findings", []) or []:
            rows.append([f.get("severity"), f.get("rule_id"), f.get("path"), f.get("reason")])
    if not verdicts:
        return ""
    return "\n\n".join([_bullets(verdicts),
                        _table(["Severity", "Rule", "Path", "Reason"], rows)])


def _section_plan(sources):
    out = []
    for s in sources:
        data = _data(s)
        if s["kind"] != "plan":
            continue
        lines = [f"### {data.get('goal')}",
                 "",
                 f"Risk: **{data.get('risk')}** · stages: {', '.join(st['stage'] for st in data.get('stages', []))}",
                 ""]
        for st in data.get("stages", []):
            lines.append(f"**{st['stage'].title()}** — {st['objective']}")
            lines.append("")
            lines.extend(f"- {item}" for item in st.get("checklist", []))
            if st.get("approval_required"):
                lines.append("- _approval required before this stage_")
            lines.append("")
        lines.extend(["**Baseline:**", ""] + [f"- {b}" for b in data.get("baseline", [])])
        lines.extend(["", "**Stop conditions:**", ""] +
                     [f"- {b}" for b in data.get("stop_conditions", [])])
        lines.extend(["", "**Rollback:**", ""] + [f"- {b}" for b in data.get("rollback", [])])
        out.append("\n".join(lines))
    return "\n\n".join(out)


def _section_routing(sources):
    rows = []
    for s in sources:
        data = _data(s)
        if s["kind"] != "route":
            continue
        sel = data.get("selected_profile") or {}
        rows.append([sel.get("profile_id"), sel.get("worker_class"),
                     ", ".join(sel.get("provides", [])), sel.get("official_program") or "-"])
    if not rows:
        return ""
    return _table(["Profile", "Worker class", "Provides", "Official program"], rows)


def _section_rights(sources, meta, policy):
    rows = []
    for s in sources:
        data = _data(s)
        if s["kind"] == "authorization":
            rows.append(["authorization", data.get("operator"),
                         ", ".join(f"{i.get('material')}:{i.get('reference')}"
                                   for i in data.get("items", [])),
                         data.get("expires_at")])
        elif s["kind"] == "approval":
            rows.append(["approval", data.get("operator"), data.get("action"),
                         data.get("expires_at")])
        elif s["kind"] == "provenance":
            rights = data.get("rights") or {}
            rows.append(["material rights", data.get("operator"),
                         f"redistribution={rights.get('redistribution')}",
                         rights.get("attribution") or ""])
    lines = []
    if rows:
        lines.append(_table(["Record", "Operator", "Scope", "Expires / attribution"], rows))
    lines.append("")
    lines.append(f"Redistribution rights asserted by the operator: "
                 f"**{meta.get('rights', 'not asserted')}**.")
    lines.append("")
    lines.append("Artifact minimization: this report carries metadata only — no media content, "
                 "key material, firmware, or ROM data is embedded." if meta.get("minimal", True)
                 else "This report includes content references because redistribution rights were "
                      "explicitly asserted by the operator.")
    return "\n".join(lines)


def _section_limitations(sources, meta):
    items = list(meta.get("limitations") or [])
    for s in sources:
        payload = s["payload"]
        for w in (payload.get("warnings") or [])[:20]:
            items.append(str(w))
        data = _data(s)
        for w in (data.get("warnings") or [])[:20]:
            items.append(str(w))
        for note in (data.get("skipped") or [])[:20]:
            items.append(f"skipped {note.get('path')}: {note.get('reason')}")
    if not items:
        return ""
    return _bullets(items)


def _section_next_steps(sources, meta):
    items = list(meta.get("next_steps") or [])
    for s in sources:
        data = _data(s)
        for cap in data.get("capabilities", []) or []:
            if cap.get("status") not in ("FOUND",):
                action = cap.get("recommended_action")
                if action:
                    items.append(f"{cap.get('capability_id')} ({cap.get('status')}): {action}")
        for step in data.get("next_steps", []) or []:
            items.append(f"{data.get('file_name') or s['source']}: {step}")
        for art in data.get("artifacts", []) or []:
            for step in art.get("next_steps", []) or []:
                items.append(f"{art.get('file_name')}: {step}")
        for rec in data.get("recommendations", []) or []:
            items.append(str(rec))
        if data.get("verdict") in ("block", "review"):
            items.append(f"resolve guardrail verdict for `{s['source']}` before packaging")
    if not items:
        return ""
    seen, unique = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            unique.append(i)
    return _bullets(unique)


# ---------------------------------------------------------------------------
# Markdown template substitution
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(r"^(#{1,6})\s")


def _tidy(markdown):
    """Drop sections whose body is empty and collapse excess blank lines."""
    lines = markdown.split("\n")
    blocks = []          # list of [heading_line_or_None, [body lines]]
    current = [None, []]
    for line in lines:
        if _HEADING_RE.match(line):
            blocks.append(current)
            current = [line, []]
        else:
            current[1].append(line)
    blocks.append(current)
    kept = []
    for head, body in blocks:
        if head is not None and not "".join(body).strip():
            continue
        kept.append((head, body))
    out = []
    for head, body in kept:
        if head is not None:
            out.append(head)
        out.extend(body)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def render_markdown(report, template_text):
    values = dict(report["sections"])
    text = template_text
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value if value else "")
    for key, value in report["front"].items():
        text = text.replace("{{" + key + "}}", str(value))
    remaining = re.findall(r"\{\{([a-z_]+)\}\}", text)
    if remaining:
        raise rt.ValidationError(
            "report template contains unknown placeholders: " + ", ".join(sorted(set(remaining))),
            ["edit asset-report-template.md so every placeholder is supported"])
    return _tidy(text)


# ---------------------------------------------------------------------------
# Minimal Markdown -> HTML (self-contained, no external resources)
# ---------------------------------------------------------------------------
_INLINE_CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       line-height: 1.55; max-width: 62rem; margin: 0 auto; padding: 2rem 1.25rem 4rem;
       background: #ffffff; color: #1b1f24; }
h1 { font-size: 1.9rem; border-bottom: 3px solid #2f6f4f; padding-bottom: .4rem; }
h2 { font-size: 1.3rem; margin-top: 2rem; color: #2f6f4f; }
h3 { font-size: 1.05rem; margin-top: 1.4rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: .88rem; }
th, td { border: 1px solid #d6dbe0; padding: .4rem .55rem; text-align: left; vertical-align: top; }
th { background: #eef4f0; }
code { background: #f2f4f6; padding: .1rem .3rem; border-radius: 3px; font-size: .86em; }
pre { background: #f6f8fa; padding: .8rem; overflow-x: auto; border-radius: 4px; }
blockquote { border-left: 4px solid #cbd5d0; margin: 1rem 0; padding: .2rem 1rem; color: #45505a; }
ul { padding-left: 1.3rem; }
footer { margin-top: 3rem; border-top: 1px solid #d6dbe0; padding-top: .8rem; color: #5b6670;
         font-size: .82rem; }
@media (prefers-color-scheme: dark) {
  body { background: #12161a; color: #e6eaee; }
  h2 { color: #7fc9a3; } th { background: #1c2329; } th, td { border-color: #2b333b; }
  code, pre { background: #1c2329; } footer { border-color: #2b333b; color: #9aa5ae; }
}
"""


def _inline(text):
    text = _html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"_([^_\n]+)_", r"<em>\1</em>", text)
    return text


def md_to_html(markdown, title="Report"):
    lines = markdown.split("\n")
    out = []
    i = 0
    in_list = False
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(_html.escape(lines[i]))
                i += 1
            out.append("<pre><code>" + "\n".join(block) + "</code></pre>")
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:\-|]+\|$", lines[i + 1].strip()):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>" +
                       "".join(f"<th>{_inline(c)}</th>" for c in header) +
                       "</tr></thead><tbody>" +
                       "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
                               for r in rows) +
                       "</tbody></table>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line[2:])}</li>")
            i += 1
            continue
        if in_list and not line.strip():
            out.append("</ul>")
            in_list = False
            i += 1
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith(">"):
            out.append(f"<blockquote>{_inline(line.lstrip('> '))}</blockquote>")
            i += 1
            continue
        if line.strip() in ("---", "***"):
            out.append("<hr>")
            i += 1
            continue
        if not line.strip():
            i += 1
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|\||- |```|>)", lines[i]):
            para.append(lines[i])
            i += 1
        out.append("<p>" + _inline(" ".join(p.strip() for p in para)) + "</p>")
    if in_list:
        out.append("</ul>")
    return ("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{_html.escape(title)}</title>\n<style>{_INLINE_CSS}</style>\n</head>\n"
            "<body>\n" + "\n".join(out) + "\n</body>\n</html>\n")


# ---------------------------------------------------------------------------
# Top-level build
# ---------------------------------------------------------------------------
def build(input_paths, policy, meta=None):
    meta = dict(meta or {})
    sources = collect(input_paths) if input_paths else []
    sections = {
        "summary": _section_summary(sources, meta),
        "capabilities": _section_capabilities(sources),
        "artifacts": _section_artifacts(sources),
        "verification": _section_verification(sources),
        "guardrails": _section_guardrails(sources),
        "plan": _section_plan(sources),
        "routing": _section_routing(sources),
        "rights": _section_rights(sources, meta, policy),
        "limitations": _section_limitations(sources, meta),
        "next_steps": _section_next_steps(sources, meta),
    }
    report_id = rt.new_id("report")
    front = {
        "title": meta.get("title") or "Console preservation report",
        "report_id": report_id,
        "generated_at": rt.iso(),
        "operator": meta.get("operator") or "not stated",
        "goal": meta.get("goal") or "not stated",
        "policy_id": policy["policy_id"],
        "skill_version": rt.VERSION,
    }
    return {
        "report_id": report_id,
        "front": front,
        "meta": meta,
        "sources": [{"source": s["source"], "kind": s["kind"]} for s in sources],
        "sections": sections,
    }


def load_template(template_path=None):
    tpath = template_path or rt.resource_default(TEMPLATE_FILENAME)
    with open(rt.require_existing(tpath, "report template", kind="file"), "r",
              encoding="utf-8") as fh:
        return fh.read()


def render(report, policy, fmt="md", template_path=None):
    template_text = load_template(template_path)
    markdown = render_markdown(report, template_text)
    clean_markdown, hits = shield_redact(markdown, policy)
    result = {
        "report_id": report["report_id"],
        "format": fmt,
        "redactions": hits,
        "markdown": clean_markdown,
        "html": None,
    }
    if fmt == "html":
        result["html"] = md_to_html(clean_markdown, report["front"]["title"])
    elif fmt == "json":
        redacted, json_hits = shield_redact_obj(report, policy)
        for k, v in json_hits.items():
            result["redactions"][k] = result["redactions"].get(k, 0) + v
        result["report"] = redacted
    return result


def shield_redact(text, policy):
    import caw_guard
    return caw_guard.redact_text(text, policy)


def shield_redact_obj(obj, policy):
    import caw_guard
    return caw_guard.redact_obj(obj, policy)
