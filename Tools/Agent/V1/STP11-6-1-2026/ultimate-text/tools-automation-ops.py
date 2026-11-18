"""Automation and quality-gate capabilities (12 handlers).

Anything that touches the filesystem is planned first and only written when
``write=true`` follows an explicit approval gate. Internal capability calls use
the same Context machinery as the CLI, with a depth limit so a pipeline cannot
recursively call itself.
"""

from __future__ import annotations

import json
import os
import re
import time

EXPORTS = ["run_capability", "list_files", "pipeline_spec"]
LEX_ASSET = "asset-lexicons-language.json"

BLOCKED_IN_PIPELINE = {"automation.text-pipeline-architect", "meta.end-to-end-text-production-director"}


def _handlers():
    module = __import__(__name__)
    return getattr(module, "ALL_HANDLERS", {}) or {}


def run_capability(ctx, capability_id, text, extra_params=None, depth=0):
    """Run another local capability in-process and return its result dict."""
    if depth > int(ctx.cfg.get("max_pipeline_depth", 4)):
        raise core.InvalidInput("pipeline depth limit reached",
                                hint="Reduce nesting or raise max_pipeline_depth in the config.")
    handlers = _handlers()
    if capability_id not in handlers:
        raise core.InvalidParam("no local handler for %r" % capability_id,
                                hint="Only local (deterministic) capabilities can run inside a pipeline.")
    if capability_id in BLOCKED_IN_PIPELINE:
        raise core.InvalidParam("%r cannot be nested inside another pipeline" % capability_id)
    cap = ctx.catalog.get(capability_id)
    params = ctx.catalog.default_params(cap)
    for key, value in (extra_params or {}).items():
        if key not in cap["params"]:
            raise core.InvalidParam("capability %r has no parameter %r" % (capability_id, key))
        params[key] = H.coerce_param(key, cap["params"][key], value)
    sub = ctx.core.Context(
        ctx.core, ctx.assets, ctx.catalog, cap, params, ctx.cfg, [text, ctx.text2, ctx.text3],
        ctx.cwd, approve=ctx.approve, dry_run=True, verbose=False,
        timeout=int(cap.get("timeout_s") or ctx.cfg.get("default_timeout_s", 20)),
        encoding=ctx.encoding,
    )
    result = handlers[capability_id](sub)
    if not isinstance(result, dict):
        result = {"output": str(result)}
    return result


def parse_pipeline(spec):
    """Parse 'cap.id:param=value|cap.id' into [(capability_id, params), ...]."""
    steps = []
    for raw in [part.strip() for part in spec.split("|") if part.strip()]:
        capability_id, _, param_text = raw.partition(":")
        params = {}
        for chunk in [c.strip() for c in param_text.split(";") if c.strip()]:
            key, _, value = chunk.partition("=")
            params[key.strip()] = value.strip()
        steps.append((capability_id.strip(), params))
    return steps


pipeline_spec = parse_pipeline


def list_files(root, glob_pattern="*", recursive=False, max_files=200):
    import fnmatch
    matches = []
    if not os.path.isdir(root):
        raise core.InvalidInput("not a directory: %s" % root)
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in sorted(filenames):
                if fnmatch.fnmatch(name, glob_pattern):
                    matches.append(os.path.relpath(os.path.join(dirpath, name), root))
                    if len(matches) >= max_files:
                        return matches
    else:
        for name in sorted(os.listdir(root)):
            full = os.path.join(root, name)
            if os.path.isfile(full) and fnmatch.fnmatch(name, glob_pattern):
                matches.append(name)
                if len(matches) >= max_files:
                    break
    return matches


def _directory(ctx, explicit=""):
    candidate = explicit.strip() or ctx.text.strip()
    if not candidate:
        paths = ctx.cfg.get("paths")
        if isinstance(paths, dict) and paths.get("input_dir"):
            candidate = str(paths["input_dir"])
    if not candidate:
        raise core.InvalidInput("name a directory in the text or set paths.input_dir in the config")
    return ctx.resolve(candidate, must_exist=True, kind="dir")


def bulk_replace_plan(ctx):
    find = ctx.params["find"]
    replacement = ctx.params["replacement"]
    rules = ctx.params["rules"].strip()
    if not find and not rules:
        raise core.InvalidInput("give --set find=... or --set rules='from=>to;from2=>to2'")
    rule_pairs = []
    if rules:
        for chunk in re_split(rules):
            left, _, right = chunk.partition("=>")
            if not left:
                raise core.InvalidParam("rule %r is missing '=>'" % chunk)
            rule_pairs.append((left, right))
    elif find:
        rule_pairs.append((find, replacement))
    root = _directory(ctx)
    glob_pattern = ctx.params["glob"]
    recursive = bool(ctx.params["recursive"])
    use_regex = bool(ctx.params["regex"])
    case_sensitive = bool(ctx.params["case_sensitive"])
    max_files = int(ctx.params["max_files"])
    write = bool(ctx.params["write"])
    files = list_files(root, glob_pattern, recursive, max_files)
    plan = []
    for name in files:
        path = os.path.join(root, name)
        try:
            text = core.read_text_file(path, encoding=ctx.encoding, errors="replace",
                                       max_bytes=int(ctx.cfg.get("max_input_bytes", 4 * 1024 * 1024)))
        except core.SkillError as exc:
            plan.append({"file": name, "matches": 0, "error": str(exc)})
            continue
        updated = text
        counts = {}
        for left, right in rule_pairs:
            flags = 0 if case_sensitive else re.IGNORECASE
            pattern = left if use_regex else __import__("re").escape(left)
            found = len(__import__("re").findall(pattern, updated, flags))
            if found:
                counts[left] = found
                updated = __import__("re").sub(pattern, right, updated, flags=flags)
        plan.append({"file": name, "matches": sum(counts.values()), "rules": counts,
                     "changed": updated != text,
                     "preview": _preview(text, updated)})
    total = sum(item.get("matches", 0) for item in plan)
    written = []
    approval = None
    if write:
        approval = ctx.gate("write replacements into %d file(s)" % len(plan), {"files": len(plan)})
        for item in plan:
            if not item.get("changed"):
                continue
            source = os.path.join(root, item["file"])
            text = core.read_text_file(source, encoding=ctx.encoding, errors="replace")
            updated = text
            for left, right in rule_pairs:
                flags = 0 if case_sensitive else __import__("re").IGNORECASE
                pattern = left if use_regex else __import__("re").escape(left)
                updated = __import__("re").sub(pattern, right, updated, flags=flags)
            core.write_text_atomic(source, updated, allow_overwrite=True, encoding=ctx.encoding)
            written.append(item["file"])
    rows = [[item["file"], item.get("matches", 0), item.get("changed", False),
             (item.get("preview") or "")[:50]] for item in plan]
    output = ["# Replacement plan (%d file(s) scanned, %d match(es))" % (len(plan), total), ""]
    if rows:
        output.append(core.table(rows, ["file", "matches", "changed", "first change"]))
    output.append("")
    output.append("mode: %s" % ("WRITTEN" if write else "dry run (pass --set write=true with --approve to apply)"))
    data = {"root": root, "files": len(plan), "matches": total, "write": write,
            "written": written, "rules": [list(pair) for pair in rule_pairs],
            "approval": approval, "plan": plan[:50]}
    warnings = []
    if total and not write:
        warnings.append("nothing was modified: this was a plan only")
    return {"output": "\n".join(output), "data": data, "written": written, "warnings": warnings}


def re_split(rules):
    return [chunk.strip() for chunk in rules.replace("\n", ";").split(";") if chunk.strip()]


def _preview(before, after):
    before_lines = before.split("\n")
    after_lines = after.split("\n")
    for idx, (old, new) in enumerate(zip(before_lines, after_lines)):
        if old != new:
            return "line %d: %s -> %s" % (idx + 1, old.strip()[:40], new.strip()[:40])
    return ""


def batch_rename(ctx):
    root = _directory(ctx)
    pattern = ctx.params["pattern"].strip()
    if not pattern:
        raise core.InvalidInput("give --set pattern='old_' (literal or regex with regex=true implied)")
    replacement = ctx.params["replacement"]
    files = list_files(root, ctx.params["glob"], bool(ctx.params["recursive"]), int(ctx.params["max_files"]))
    start = int(ctx.params["start"])
    digits = int(ctx.params["digits"])
    import re as _re
    rows = []
    for index, name in enumerate(files, start):
        try:
            base = _re.sub(pattern, replacement, name)
        except _re.error as exc:
            raise core.InvalidParam("invalid pattern: %s" % exc)
        if "%(n)" in base or "{n}" in base:
            base = base.replace("%(n)", str(index).zfill(digits)).replace("{n}", str(index).zfill(digits))
        if base == name:
            continue
        rows.append([name, base, os.path.exists(os.path.join(root, base))])
    collisions = [r for r in rows if r[2]]
    output = ["# Rename plan (%d of %d files change)" % (len(rows), len(files)), ""]
    if rows:
        output.append(core.table(rows[:60], ["current name", "new name", "target exists"]))
    output.append("")
    output.append("nothing was renamed: this tool only generates the mapping")
    data = {"root": root, "files": len(files), "renames": len(rows),
            "collisions": len(collisions), "plan": rows[:200]}
    warnings = []
    if collisions:
        warnings.append("%d target name(s) already exist and would be overwritten" % len(collisions))
    return {"output": "\n".join(output), "data": data, "warnings": warnings}


def pipeline_architect(ctx):
    spec = ctx.params["pipeline"].strip()
    if not spec:
        raise core.InvalidInput("give --set pipeline='editing.grammar-and-mechanics-editor|typography.typography-cleaner'")
    steps = parse_pipeline(spec)
    stop_on_error = bool(ctx.params["stop_on_error"])
    text = ctx.text
    records = []
    for capability_id, params in steps:
        started = time.time()
        try:
            result = run_capability(ctx, capability_id, text, params)
        except core.SkillError as exc:
            records.append({"step": capability_id, "status": "error", "error": exc.to_dict()})
            if stop_on_error:
                break
            continue
        text = result.get("output") or ""
        records.append({"step": capability_id, "status": "ok",
                        "ms": round((time.time() - started) * 1000, 1),
                        "chars": len(text),
                        "warnings": result.get("warnings") or []})
    failed = [r for r in records if r["status"] == "error"]
    if ctx.params["format"] == "json":
        output = json.dumps({"steps": records, "output": text}, indent=2, ensure_ascii=False)
    else:
        lines = ["# Pipeline run (%d step(s))" % len(steps), ""]
        lines.append(core.table([[r["step"], r["status"], r.get("ms", "-"), r.get("chars", "-")]
                                 for r in records], ["step", "status", "ms", "chars"]))
        lines.append("")
        lines.append("## Final text")
        lines.append(text)
        output = "\n".join(lines)
    return {"output": output, "data": {"steps": records, "failed": len(failed),
                                       "final_chars": len(text)},
            "warnings": ["%d step(s) failed" % len(failed)] if failed else []}


LINT_RULES = {
    "double-space": (r"[^\n ] {2,}[^\n ]", "two or more spaces inside a line"),
    "space-before-punctuation": (r"\s+[,.;:!?]", "space before punctuation"),
    "missing-space-after-punctuation": (r"[,.;:!?][A-Za-z]", "no space after punctuation"),
    "straight-quotes": (r'["\']', "straight quote instead of a typographic quote"),
    "repeated-word": (r"\b(\w+)\s+\1\b", "word repeated twice"),
    "trailing-space": (r"[ \t]+$", "trailing whitespace"),
    "tab-character": (r"\t", "tab character in prose"),
    "mixed-line-endings": (r"\r(?!\n)", "carriage return without newline"),
    "todo-marker": (r"\b(TODO|FIXME|XXX)\b", "unresolved marker left in text"),
    "very-long-sentence": (r"[^.!?\n]{220,}", "sentence longer than 220 characters"),
}


def linter_designer(ctx):
    action = ctx.params["action"]
    rules_raw = ctx.params["rules"].strip()
    enabled = [r.strip() for r in rules_raw.split(",") if r.strip()] if rules_raw else sorted(LINT_RULES)
    unknown = [r for r in enabled if r not in LINT_RULES]
    if unknown:
        raise core.InvalidParam("unknown lint rule(s): %s" % ", ".join(unknown),
                                hint="Available: %s" % ", ".join(sorted(LINT_RULES)))
    if action == "design":
        rows = [[name, LINT_RULES[name][1], LINT_RULES[name][0]] for name in enabled]
        return {"output": core.table(rows, ["rule", "what it catches", "pattern"]),
                "data": {"rules": enabled, "count": len(enabled)}}
    text = ctx.require_text(minimum=1)
    import re as _re
    findings = []
    for name in enabled:
        pattern, description = LINT_RULES[name]
        for match in _re.finditer(pattern, text, _re.MULTILINE):
            line = text[:match.start()].count("\n") + 1
            findings.append({"rule": name, "line": line, "column": match.start() - text.rfind("\n", 0, match.start()),
                             "excerpt": match.group(0)[:60], "description": description})
    rules_checked = len(enabled)
    if ctx.params["format"] == "json":
        output = json.dumps({"findings": findings, "rules": enabled}, indent=2, ensure_ascii=False)
    else:
        output = (core.table([[f["line"], f["column"], f["rule"], f["excerpt"]] for f in findings[:80]],
                             ["line", "column", "rule", "excerpt"]) if findings
                  else "no lint findings across %d rule(s)" % rules_checked)
    return {"output": output, "data": {"findings": findings, "count": len(findings), "rules": enabled},
            "warnings": ["%d lint finding(s)" % len(findings)] if findings else []}


def golden_samples(ctx):
    action = ctx.params["action"]
    sample = ctx.params["sample"].strip()
    expected_file = ctx.params["expected_file"].strip()
    text = ctx.text
    if action in ("create", "add"):
        if not text.strip():
            raise core.InvalidInput("supply the source text (--text or --text-file) to record as a sample")
        record = {"sample": sample or "sample-1", "input": text,
                  "input_sha256": H.fingerprint(text) if hasattr(H, "fingerprint") else core.sha256_text(text),
                  "capability": ctx.params.get("capability") or "",
                  "recorded": core.now_iso(),
                  "expected": ""}
        body = json.dumps(record, indent=2, ensure_ascii=False)
        lines = ["# Golden sample recorded: %s" % record["sample"], "",
                 "- input checksum: `%s`" % record["input_sha256"],
                 "- record the expected output before using this as a regression test", "",
                 "```json", body, "```"]
        return {"output": "\n".join(lines), "data": record,
                "warnings": ["the 'expected' field is empty: fill it in before treating this as a baseline"]}
    if not expected_file:
        raise core.InvalidInput("give --set expected_file=path/to/expected.txt to compare against")
    path = ctx.resolve(expected_file, must_exist=True)
    expected = core.read_text_file(path, encoding=ctx.encoding)
    actual = text
    if expected == actual:
        return {"output": "PASS: output matches the golden file exactly", "data": {"status": "pass",
                "expected_sha256": core.sha256_text(expected), "actual_sha256": core.sha256_text(actual)}}
    import difflib
    diff = list(difflib.unified_diff(expected.split("\n"), actual.split("\n"),
                                     fromfile="expected", tofile="actual", lineterm=""))
    data = {"status": "fail", "diff_lines": len(diff),
            "expected_sha256": core.sha256_text(expected), "actual_sha256": core.sha256_text(actual),
            "diff": diff[:200]}
    return {"output": "FAIL: output differs from the golden file\n\n" + "\n".join(diff[:80]), "data": data,
            "warnings": ["regression against the recorded baseline"]}


EDGE_CASES = {
    "empty": "",
    "whitespace": "   \n\t\n  ",
    "bidi": "abc\u202e123\u202c def",
    "combining": "e\u0301a\u0308n\u0303",
    "emoji": "launch 🚀 done ✅ family 👩‍👩‍👧‍👦",
    "very-long": "word " * 400,
    "control": "bell\u0007 nul-ish\u0001 text",
    "mixed-script": "paypal раураl ΑΘΗΝΑ",
    "rtl": "مرحبا بالعالم שלום",
    "surrogates": "broken \ufffd\ufffd text",
    "punctuation": "!!!???,,,;;;",
    "html": "<b>bold</b> &amp; <script>x</script>",
    "markdown": "# H\n\n- a\n- b\n\n| a | b |\n| - | - |\n| 1 | 2 |",
    "json-like": '{"a": [1, 2, {"b": null}]}',
    "numbers": "0 -1 3.14 1e-9 999999999999999999999 0x1F",
}


def edge_case_corpus(ctx):
    kinds = [k.lower() for k in ctx.params["kinds"]]
    count = int(ctx.params["count"])
    rng = core.seeded_rng(ctx.params["seed"])
    unknown = [k for k in kinds if k not in EDGE_CASES]
    if unknown:
        raise core.InvalidParam("unknown kind(s): %s" % ", ".join(unknown),
                                hint="Available: %s" % ", ".join(sorted(EDGE_CASES)))
    records = []
    for kind in kinds:
        base = EDGE_CASES[kind]
        for index in range(1, count + 1):
            value = base
            if kind == "very-long":
                value = " ".join(["word"] * (100 + rng.next_int(50) * 10))
            elif kind == "numbers":
                value = " ".join(rng.choice(base.split()) for _ in range(1, 4 + rng.next_int(4)))
            records.append({"id": "%s-%02d" % (kind, index), "kind": kind,
                            "text": value, "chars": len(value),
                            "codepoints": [("U+%04X" % ord(c)) for c in value[:12]]})
    fmt = str(ctx.params.get("format") or "jsonl")
    if fmt == "jsonl":
        output = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    else:
        output = core.table([[r["id"], r["chars"], r["text"][:40].replace("\n", " ")] for r in records],
                            ["id", "chars", "text"])
    return {"output": output, "data": {"records": len(records), "kinds": kinds,
                                       "seed": ctx.params["seed"]}}


CONVERSIONS = [("json", "structured-formats.json-to-text-and-table-generator"),
               ("csv", "structured-formats.delimited-data-text-converter"),
               ("yaml", "structured-formats.yaml-to-text-and-table-converter"),
               ("markdown", "structured-formats.markdown-text-structurer")]


def conversion_qa(ctx):
    formats = [f.lower() for f in ctx.params["formats"]]
    samples = int(ctx.params["samples"])
    seed = ctx.params["seed"]
    text = ctx.text
    results = []
    for fmt in formats:
        capability = dict(CONVERSIONS).get(fmt)
        if not capability:
            results.append({"format": fmt, "status": "skipped", "note": "no converter mapped"})
            continue
        payload = text or sample_payload(fmt, seed)
        try:
            result = run_capability(ctx, capability, payload)
        except core.SkillError as exc:
            results.append({"format": fmt, "status": "error", "capability": capability,
                            "error": exc.to_dict()})
            continue
        output = result.get("output") or ""
        results.append({"format": fmt, "status": "ok", "capability": capability,
                        "chars": len(output), "empty": not output.strip(),
                        "has_replacement_char": "\ufffd" in output,
                        "sample": output[:160]})
    failures = [r for r in results if r["status"] != "ok" or r.get("empty") or r.get("has_replacement_char")]
    if str(ctx.params.get("format") or "") == "json":
        output = json.dumps(results, indent=2, ensure_ascii=False)
    else:
        output = core.table([[r["format"], r["status"], r.get("chars", "-"), r.get("note", "")[:40]]
                             for r in results], ["format", "status", "chars", "note"])
    return {"output": output, "data": {"results": results, "failures": len(failures),
                                       "samples": samples, "seed": seed},
            "warnings": ["%d format(s) failed the round-trip check" % len(failures)] if failures else []}


def sample_payload(fmt, seed):
    payloads = {
        "json": '{"team": "docs", "items": [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}], "active": true}',
        "csv": "id,name,score\n1,alpha,10\n2,beta,20\n3,gamma,30",
        "yaml": "team: docs\nitems:\n  - id: 1\n    name: alpha\nactive: true",
        "markdown": "# Title\n\n## Section\n\n- first point\n- second point\n\n| a | b |\n| - | - |\n| 1 | 2 |",
        "srt": "1\n00:00:00,000 --> 00:00:02,000\nFirst line\n\n2\n00:00:02,000 --> 00:00:04,000\nSecond line",
    }
    return payloads.get(fmt, "sample text for %s" % fmt)


def deterministic_controller(ctx):
    text = ctx.require_text(minimum=0)
    seed = ctx.params["seed"]
    normalize = [n.lower() for n in ctx.params["normalize"]]
    repeat = int(ctx.params["repeat"])
    sort_lines = bool(ctx.params["sort_lines"])
    def apply(blob):
        out = blob
        if "newlines" in normalize:
            out = core.canonical_newlines(out)
        if "trailing-space" in normalize:
            out = "\n".join(line.rstrip() for line in out.split("\n"))
        if "unicode" in normalize:
            import unicodedata
            out = unicodedata.normalize("NFC", out)
        if sort_lines:
            out = "\n".join(sorted(out.split("\n")))
        return out.rstrip("\n")
    digests = []
    for _ in range(max(2, repeat)):
        digests.append(core.sha256_text(apply(text) + "|seed=%s" % seed))
    stable = len(set(digests)) == 1
    out = apply(text)
    data = {"seed": seed, "runs": len(digests), "stable": stable,
            "digest": digests[0], "normalisations": normalize, "sort_lines": sort_lines,
            "chars": len(out)}
    output = "\n".join(["# Determinism report", "",
                        "- runs: %d" % len(digests),
                        "- identical output across runs: %s" % stable,
                        "- digest: `%s`" % digests[0],
                        "",
                        "## Normalised text", out])
    return {"output": output, "data": data,
            "warnings": [] if stable else ["output varied between runs: a source of randomness remains"]}


def review_queue(ctx):
    text = ctx.require_text(minimum=5)
    threshold = float(ctx.params["threshold"])
    tables = ctx.assets.json("asset-patterns-security.json")
    items = []
    sensitive = tables["sensitive_topics"] if "sensitive_topics" in tables else {}
    for kind, spec in tables["pii"].items():
        import re as _re
        for match in _re.finditer(spec["pattern"], text):
            confidence = spec.get("confidence", 0.6)
            if confidence < threshold:
                items.append({"kind": "pii", "type": kind, "confidence": confidence,
                              "excerpt": match.group(0)[:40], "offset": match.start(),
                              "reason": "below confidence threshold"})
    lex = ctx.assets.json(LEX_ASSET)
    for topic, cues in lex["sensitive_topics"].items():
        for cue in cues:
            if cue.lower() in text.lower():
                items.append({"kind": "sensitive-topic", "type": topic, "confidence": 0.5,
                              "excerpt": cue, "offset": text.lower().index(cue.lower()),
                              "reason": "topic cue needs a human judgement call"})
    items.sort(key=lambda i: i["confidence"])
    if ctx.params["format"] == "jsonl":
        output = "\n".join(json.dumps(i, ensure_ascii=False) for i in items) or "no items need review"
    else:
        output = core.table([[i["kind"], i["type"], i["confidence"], i["excerpt"]] for i in items],
                            ["kind", "type", "confidence", "excerpt"]) if items else "no items need review"
    return {"output": output, "data": {"items": items, "count": len(items), "threshold": threshold},
            "warnings": ["%d item(s) queued for human review" % len(items)] if items else []}


def performance_optimizer(ctx):
    capability = ctx.params["capability"].strip()
    if not capability:
        raise core.InvalidInput("give --set capability=<capability-id>")
    repeat = max(1, int(ctx.params["repeat"]))
    size = int(ctx.params["input_bytes"])
    text = ctx.text or ("Benchmark sentence for timing. " * (size // 28 + 1))[:size]
    timings = []
    outputs = set()
    for _ in range(repeat):
        started = time.time()
        result = run_capability(ctx, capability, text)
        timings.append((time.time() - started) * 1000)
        outputs.add(len(result.get("output") or ""))
    average = sum(timings) / len(timings)
    data = {"capability": capability, "runs": repeat, "input_bytes": len(text),
            "ms": [round(t, 1) for t in timings], "average_ms": round(average, 1),
            "min_ms": round(min(timings), 1), "max_ms": round(max(timings), 1),
            "chars_per_second": round(len(text) / (average / 1000.0), 0) if average else None,
            "output_lengths": sorted(outputs)}
    output = "\n".join(["# Performance report", "",
                        "- capability: `%s`" % capability,
                        "- input: %d characters" % len(text),
                        "- runs: %d, average %.1f ms (min %.1f, max %.1f)" % (repeat, average, min(timings), max(timings)),
                        "- throughput: %.0f characters/second on this machine" % data["chars_per_second"] if data["chars_per_second"] else "",
                        "", "Caching and batching only pay off when the same input is reused; "
                            "measure again on your own hardware before optimising."])
    warnings = []
    if average > int(ctx.cfg.get("slow_capability_ms", 2000)):
        warnings.append("average runtime exceeds the %d ms soft budget" % int(ctx.cfg.get("slow_capability_ms", 2000)))
    return {"output": "\n".join(output), "data": data, "warnings": warnings}


def interoperability_adapter(ctx):
    text = ctx.require_text(minimum=0)
    target = ctx.params["to"]
    source = ctx.params["from_format"]
    import re as _re
    if source == "auto":
        stripped = text.lstrip()
        if stripped.startswith("{"):
            source = "json"
        elif "\t" in text or (text.count(",") > 2 and text.count("\n") > 1):
            source = "csv"
        elif re.search(r"^\s*[-*] ", text, _re.M):
            source = "markdown"
        elif "-->" in text:
            source = "srt"
        else:
            source = "lines"
    records = []
    if source == "json":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise core.InvalidInput("input is not valid JSON: %s" % exc.msg)
        if isinstance(parsed, list):
            records = [item if isinstance(item, dict) else {"value": item} for item in parsed]
        elif isinstance(parsed, dict):
            records = [parsed]
        else:
            records = [{"value": parsed}]
    elif source == "csv":
        import csv
        import io
        rows = list(csv.reader(io.StringIO(text)))
        header = rows[0] if rows else []
        records = [dict(zip(header, row)) for row in rows[1:]]
    elif source == "srt":
        for block in _re.split(r"\n\s*\n", text.strip()):
            lines = [ln for ln in block.split("\n") if ln.strip()]
            if len(lines) >= 3:
                records.append({"index": lines[0], "timing": lines[1], "text": " ".join(lines[2:])})
    elif source == "markdown":
        for line in text.split("\n"):
            match = _re.match(r"^\s*[-*]\s+(.*)$", line)
            if match:
                records.append({"item": match.group(1)})
    else:
        records = [{"line": index, "text": line} for index, line in enumerate(text.split("\n"), 1)]
    if not records:
        raise core.InvalidInput("no records could be read from the input")
    headers = []
    for record in records:
        for key in record:
            if key not in headers:
                headers.append(key)
    if target == "json":
        output = json.dumps(records, indent=2, ensure_ascii=False)
    elif target == "jsonl":
        output = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    elif target == "csv":
        lines = [",".join(headers)]
        lines += [",".join('"%s"' % str(r.get(h, "")).replace('"', '""') for h in headers) for r in records]
        output = "\n".join(lines)
    elif target == "markdown":
        lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        lines += ["| " + " | ".join(str(r.get(h, "")) for h in headers) + " |" for r in records]
        output = "\n".join(lines)
    elif target == "lines":
        output = "\n".join(" ".join(str(v) for v in r.values()) for r in records)
    else:
        raise core.InvalidParam("unsupported target format %r" % target,
                                hint="Use json, jsonl, csv, markdown or lines.")
    return {"output": output, "data": {"from": source, "to": target, "records": len(records),
                                       "fields": headers}}


def readiness_gate(ctx):
    text = ctx.require_text(minimum=1)
    checks = [c.lower() for c in ctx.params["checks"]]
    strict = bool(ctx.params["strict"])
    results = []
    if "lint" in checks:
        lint = linter_designer(_with_params(ctx, {"action": "run", "rules": "", "format": "json"}))
        count = len((lint.get("data") or {}).get("findings") or [])
        results.append(("lint", "fail" if count else "pass", "%d finding(s)" % count))
    if "readability" in checks:
        metrics = H.reading_metrics(text)
        grade = metrics.get("fk_grade") or 0
        results.append(("readability", "warn" if grade > 12 else "pass", "grade %.1f" % grade))
    if "pii" in checks:
        tables = ctx.assets.json("asset-patterns-security.json")
        import re as _re
        hits = sum(len(_re.findall(spec["pattern"], text)) for spec in tables["pii"].values())
        results.append(("pii", "fail" if hits else "pass", "%d match(es)" % hits))
    if "secrets" in checks:
        tables = ctx.assets.json("asset-patterns-security.json")
        import re as _re
        hits = sum(len(_re.findall(spec["pattern"], text)) for spec in tables["secrets"].values())
        results.append(("secrets", "fail" if hits else "pass", "%d match(es)" % hits))
    if "unicode" in checks:
        import unicodedata
        invisible = sum(1 for c in text if unicodedata.category(c) == "Cf")
        results.append(("unicode", "warn" if invisible else "pass",
                        "NFC=%s, %d invisible" % (unicodedata.is_normalized("NFC", text), invisible)))
    if "links" in checks:
        import re as _re
        links = _re.findall(r"https?://[^\s)]+", text)
        broken = [l for l in links if l.endswith((".", ",")) or " " in l]
        results.append(("links", "warn" if broken else "pass",
                        "%d link(s), %d suspicious" % (len(links), len(broken))))
    if "structure" in checks:
        headings = len([ln for ln in text.split("\n") if ln.startswith("#")])
        paragraphs = len([p for p in text.split("\n\n") if p.strip()])
        verdict = "pass" if headings or paragraphs > 1 else "warn"
        results.append(("structure", verdict, "%d heading(s), %d paragraph(s)" % (headings, paragraphs)))
    if "length" in checks:
        words = len(text.split())
        verdict = "warn" if words < 10 else "pass"
        results.append(("length", verdict, "%d words" % words))
    failed = [r for r in results if r[1] == "fail"]
    warned = [r for r in results if r[1] == "warn"]
    verdict = "BLOCKED" if failed or (strict and warned) else "READY" if not warned else "READY WITH WARNINGS"
    if ctx.params["format"] == "json":
        output = json.dumps({"verdict": verdict, "checks": results}, indent=2, ensure_ascii=False)
    else:
        lines = ["# Publication readiness: **%s**" % verdict, ""]
        lines.append(core.table([[r[0], r[1], r[2]] for r in results], ["check", "result", "detail"]))
        lines.append("")
        lines.append("Gate policy: %s" % ("strict (warnings block)" if strict else "lenient (only failures block)"))
        output = "\n".join(lines)
    return {"output": output, "data": {"verdict": verdict, "checks": results,
                                       "failed": len(failed), "warned": len(warned),
                                       "strict": strict},
            "warnings": ["%s: %d failing check(s)" % (verdict, len(failed))] if failed else []}


def _with_params(ctx, overrides):
    clone = ctx.core.Context(ctx.core, ctx.assets, ctx.catalog, ctx.capability, dict(overrides),
                             ctx.cfg, [ctx.text, ctx.text2, ctx.text3], ctx.cwd,
                             approve=ctx.approve, dry_run=True, timeout=ctx.timeout,
                             encoding=ctx.encoding)
    return clone


HANDLERS = {
    "automation.bulk-find-and-replace-planner": bulk_replace_plan,
    "automation.batch-rename-text-generator": batch_rename,
    "automation.text-pipeline-architect": pipeline_architect,
    "automation.text-linter-rule-designer": linter_designer,
    "automation.golden-sample-and-regression-builder": golden_samples,
    "automation.edge-case-text-corpus-generator": edge_case_corpus,
    "automation.text-conversion-qa-auditor": conversion_qa,
    "automation.deterministic-output-controller": deterministic_controller,
    "automation.human-review-queue-builder": review_queue,
    "automation.text-workflow-performance-optimizer": performance_optimizer,
    "automation.text-tool-interoperability-adapter": interoperability_adapter,
    "automation.publication-readiness-gate": readiness_gate,
}
