"""Task contracts and output validation.

Agent-assisted capabilities cannot generate prose inside a deterministic Python
process, so they emit a *task contract*: a fully specified, parameterised
brief (role, objective, constraints, acceptance checks, safety rules, output
format) that the calling assistant executes. ``validate_output`` then checks
the produced text against the same contract, so the round trip is
machine-verifiable rather than a promise.

Both functions are used by the CLI (``contract`` and ``validate`` commands) and
by the selftest runner.
"""

from __future__ import annotations

import json
import re

EXPORTS = ["build_contract", "validate_output", "contract_markdown"]

TEMPLATE_ASSET = "asset-prompt-templates.json"
MAX_EMBEDDED_INPUT = 8000
PLACEHOLDER_PATTERNS = [
    r"\blorem ipsum\b", r"\bTODO\b", r"\bTBD\b", r"\[insert[^\]]*\]",
    r"\{\{[^}]+\}\}", r"<placeholder", r"\bXXX\b", r"\bFIXME\b",
    r"\[your [^\]]+\]", r"\byour name here\b",
]


def _fmt_value(value):
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "none"
    if value in ("", None):
        return "not specified"
    return str(value)


def _templates(assets):
    return assets.json(TEMPLATE_ASSET)


def _render(text, mapping):
    out = text
    for key, value in mapping.items():
        out = out.replace("{%s}" % key, str(value))
    return out


def _directives(ctx, tpl):
    directives, generic = [], []
    for name, value in ctx.params.items():
        template = tpl.get("param_directives", {}).get(name)
        if template:
            directives.append(_render(template, {"value": _fmt_value(value), "name": name}))
        else:
            generic.append("Respect `%s` = %s." % (name, _fmt_value(value)))
    return directives, generic


def build_contract(ctx):
    """Compose the task contract for one capability + parameter set."""
    cap = ctx.capability
    tpl = _templates(ctx.assets)
    cat = tpl["categories"][cap["prompt_template"]]
    directives, generic = _directives(ctx, tpl)
    text = ctx.text or ""
    embedded = text
    truncated = False
    if len(embedded) > MAX_EMBEDDED_INPUT:
        embedded = embedded[:MAX_EMBEDDED_INPUT]
        truncated = True

    objective = _render(cat["objective"], {
        "name": cap["name"],
        "description": cap["description"],
        "category": cap["category"],
    })
    checks = [{"id": c["id"], "kind": c["kind"], "description": c["description"]}
              for c in cap["acceptance_checks"]]
    validate_cmd = ("python3 tools-ultimate-text.py validate %s --text-file draft.txt"
                    % cap["id"])
    run_cmd = ("python3 tools-ultimate-text.py run %s --text-file input.txt %s"
               % (cap["id"],
                  " ".join("--set %s=%s" % (k, _fmt_value(v).replace(" ", "_"))
                           for k, v in ctx.params.items() if k in ("mode", "style", "format"))))

    contract = {
        "schema": "ultimate-text/task-contract@1",
        "capability": {
            "id": cap["id"],
            "name": cap["name"],
            "category": cap["category"],
            "engine": cap["engine"],
            "description": cap["description"],
        },
        "role": cat["role"],
        "objective": objective,
        "inputs": {
            "primary_text_chars": len(text),
            "primary_text_sha256": ctx.core.sha256_text(text) if text else None,
            "truncated_in_prompt": truncated,
            "second_text_supplied": bool(ctx.text2.strip()) if hasattr(ctx, "text2") else False,
        },
        "constraints": directives + generic + list(cat.get("constraints", [])),
        "quality_bar": list(cat.get("quality_bar", [])),
        "acceptance_checks": checks,
        "output_format": cat.get("output_format", "plain text, no preamble"),
        "safety": list(tpl.get("global_safety", [])) + list(cat.get("safety", [])),
        "validation": {
            "command": validate_cmd,
            "run_local_reference": run_cmd if cap["engine"] == "local" else None,
        },
        "params": dict(ctx.params),
    }
    contract["prompt_text"] = _prompt_text(contract, embedded, tpl)
    contract["prompt_markdown"] = contract_markdown(contract, embedded)
    return contract


def _prompt_text(contract, embedded, tpl):
    lines = []
    lines.append("ROLE: %s" % contract["role"])
    lines.append("")
    lines.append("TASK: %s" % contract["objective"])
    if contract["inputs"]["primary_text_chars"]:
        lines.append("")
        lines.append("INPUT TEXT%s:" % (" (truncated)" if contract["inputs"]["truncated_in_prompt"] else ""))
        lines.append(embedded)
    lines.append("")
    lines.append("CONSTRAINTS:")
    for item in contract["constraints"]:
        lines.append("- %s" % item)
    if contract["quality_bar"]:
        lines.append("")
        lines.append("QUALITY BAR:")
        for item in contract["quality_bar"]:
            lines.append("- %s" % item)
    lines.append("")
    lines.append("ACCEPTANCE CHECKS:")
    for check in contract["acceptance_checks"]:
        lines.append("- [%s] %s" % (check["kind"], check["description"]))
    lines.append("")
    lines.append("OUTPUT FORMAT: %s" % contract["output_format"])
    if contract["safety"]:
        lines.append("")
        lines.append("SAFETY:")
        for item in contract["safety"]:
            lines.append("- %s" % item)
    lines.append("")
    lines.append("AFTER PRODUCING THE TEXT, VERIFY WITH:")
    lines.append(contract["validation"]["command"])
    return "\n".join(lines)


def contract_markdown(contract, embedded=""):
    cap = contract["capability"]
    out = []
    out.append("## Task contract: %s" % cap["name"])
    out.append("")
    out.append("`%s` | category: %s | engine: %s" % (cap["id"], cap["category"], cap["engine"]))
    out.append("")
    out.append("**Role.** %s" % contract["role"])
    out.append("")
    out.append("**Objective.** %s" % contract["objective"])
    out.append("")
    out.append("**Constraints**")
    for item in contract["constraints"]:
        out.append("- %s" % item)
    if contract["quality_bar"]:
        out.append("")
        out.append("**Quality bar**")
        for item in contract["quality_bar"]:
            out.append("- %s" % item)
    out.append("")
    out.append("**Acceptance checks**")
    for check in contract["acceptance_checks"]:
        out.append("- `%s` - %s" % (check["kind"], check["description"]))
    out.append("")
    out.append("**Output format.** %s" % contract["output_format"])
    if contract["safety"]:
        out.append("")
        out.append("**Safety**")
        for item in contract["safety"]:
            out.append("- %s" % item)
    out.append("")
    out.append("**Verify the result with** `%s`." % contract["validation"]["command"])
    if embedded:
        fence = "```"
        out.append("")
        out.append("**Input text**")
        out.append("")
        out.append(fence + "text")
        out.append(embedded)
        out.append(fence)
    return "\n".join(out)


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def _count_words(text):
    return len(re.findall(r"[^\s]+", text))


def _placeholder_hits(text):
    hits = []
    for pattern in PLACEHOLDER_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            hits.append({"pattern": pattern, "match": match.group(0)})
    return hits


def validate_output(core, assets, catalog, cap, params, text, cwd=".", approve=False):
    """Check produced text against the capability's acceptance contract."""
    checks = []
    text = text or ""
    words = _count_words(text)
    visible = [c for c in text if not c.isspace()]

    def add(check_id, kind, ok, detail, severity="error"):
        checks.append({"id": check_id, "kind": kind, "status": "pass" if ok else "fail",
                       "severity": severity, "detail": detail})
        return ok

    add("non_empty", "non_empty", bool(visible),
        "%d non-space characters" % len(visible))
    hits = _placeholder_hits(text)
    add("no_placeholders", "no_placeholders", not hits,
        "no placeholder markers" if not hits else
        "found %d placeholder marker(s): %s" % (len(hits), ", ".join(sorted({h["match"] for h in hits}))))

    max_words = int(params.get("max_words") or 0)
    if max_words:
        add("max_words", "max_words", words <= max_words, "%d words (limit %d)" % (words, max_words))
    max_chars = int(params.get("max_chars") or 0)
    if max_chars:
        add("max_chars", "max_chars", len(text) <= max_chars,
            "%d characters (limit %d)" % (len(text), max_chars))
    min_words = int(params.get("min_words") or 0)
    if min_words:
        add("min_words", "min_words", words >= min_words, "%d words (minimum %d)" % (words, min_words))
    exact_chars = int(params.get("exact_chars") or 0)
    if exact_chars:
        add("exact_chars", "exact_chars", len(text) == exact_chars,
            "%d characters (required exactly %d)" % (len(text), exact_chars))
    max_syllables = int(params.get("max_syllables") or 0)
    if max_syllables:
        syllables = _syllables(text)
        add("max_syllables", "max_syllables", syllables <= max_syllables,
            "%d syllables (limit %d)" % (syllables, max_syllables))
    forbidden = (params.get("forbidden_letters") or "").strip()
    if forbidden:
        found = sorted({ch for ch in text.lower() if ch in forbidden.lower()})
        add("forbidden_letters", "absent_chars", not found,
            "no forbidden letters" if not found else "contains forbidden letters: %s" % "".join(found))

    # advisory checks: report but never fail the capability
    if params.get("case") not in (None, "keep"):
        mode = params["case"]
        ok = {"lower": text == text.lower(), "upper": text == text.upper(),
              "title": text == text.title()}.get(mode, True)
        add("declared_case", "declared_value", ok, "requested case: %s" % mode, severity="warn")

    failed = [c for c in checks if c["status"] == "fail" and c["severity"] == "error"]
    warned = [c for c in checks if c["status"] == "fail" and c["severity"] == "warn"]
    status = "validation_failed" if failed else "ok"
    summary_lines = ["%s: %d check(s), %d failed, %d advisory failure(s)"
                     % (cap["id"], len(checks), len(failed), len(warned))]
    for check in checks:
        mark = "PASS" if check["status"] == "pass" else ("FAIL" if check["severity"] == "error" else "WARN")
        summary_lines.append("  [%s] %-16s %s" % (mark, check["kind"], check["detail"]))
    stats = core.text_stats(text)
    return {
        "status": status,
        "capability": cap["id"],
        "stats": stats,
        "checks": checks,
        "failed": [c["id"] for c in failed],
        "advisory": [c["id"] for c in warned],
        "summary": "\n".join(summary_lines),
        "warnings": [] if not warned else ["advisory check(s) failed: %s" % ", ".join(c["id"] for c in warned)],
    }


def _syllables(text):
    count = 0
    for word in re.findall(r"[A-Za-z]+", text.lower()):
        word = re.sub(r"(?:[^aeiou]e|[^aeiou]es)$", "", word)
        groups = re.findall(r"[aeiouy]+", word)
        count += max(1, len(groups))
    return count

