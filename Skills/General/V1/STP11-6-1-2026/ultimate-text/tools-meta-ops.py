"""Meta capabilities (12 handlers): routing, contracts, verification, maintenance.

These handlers inspect the Skill Tool itself - the catalog, the handler map and
the other capabilities - which is why they live in a separate module from the
text transformations they orchestrate.
"""

from __future__ import annotations

import json
import os
import re

EXPORTS = ["score_capability", "catalog_report"]
SEC_ASSET = "asset-patterns-security.json"
LEX_ASSET = "asset-lexicons-language.json"

STOPWORDS = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "my", "our",
             "this", "that", "text", "make", "need", "want", "please", "help", "using", "use"}


def _handlers():
    module = __import__(__name__)
    return getattr(module, "ALL_HANDLERS", {}) or {}


def _tokens(text):
    return [w for w in re.findall(r"[a-z0-9+#-]+", text.lower()) if w not in STOPWORDS and len(w) > 2]


def score_capability(capability, tokens):
    haystack = " ".join([capability["id"], capability["name"], capability.get("description", ""),
                         capability.get("category", ""), " ".join(capability.get("keywords") or [])]).lower()
    score = 0.0
    for token in tokens:
        if token in capability["id"].lower():
            score += 3.0
        elif token in capability["name"].lower():
            score += 2.0
        elif token in haystack:
            score += 1.0
    if capability.get("engine") == "local":
        score += 0.4  # prefer deterministic capabilities when the match is close
    return score


def catalog_report(catalog):
    skills = [capability for category in catalog.categories for capability in category["skills"]]
    by_engine = {"local": 0, "agent": 0}
    by_category = {}
    for capability in skills:
        by_engine[capability["engine"]] = by_engine.get(capability["engine"], 0) + 1
        by_category[capability["category_slug"]] = by_category.get(capability["category_slug"], 0) + 1
    return {"total": len(skills), "by_engine": by_engine, "by_category": by_category}


def task_router(ctx):
    mission = ctx.params["mission"].strip() or ctx.text.strip()
    if not mission:
        raise core.InvalidInput("give --set mission='what you need to do' or pass text")
    top_n = int(ctx.params["top_n"])
    tokens = _tokens(mission)
    if not tokens:
        raise core.InvalidInput("the mission text contains no searchable words")
    scored = []
    for category in ctx.catalog.categories:
        for capability in category["skills"]:
            score = score_capability(capability, tokens)
            if score > 0:
                scored.append((score, capability))
    scored.sort(key=lambda pair: -pair[0])
    chosen = scored[:top_n]
    if not chosen:
        raise core.InvalidInput("no capability matched %r" % mission,
                                hint="Try 'search <word>' to browse the 240 capabilities.")
    rows = [[c["id"], c["engine"], round(s, 2), c["category"]] for s, c in chosen]
    data = {"mission": mission, "tokens": tokens,
            "matches": [{"id": c["id"], "score": round(s, 2), "engine": c["engine"],
                         "description": c.get("description", "")} for s, c in chosen]}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    lines = ["# Routing for: %s" % mission, "", core.table(rows, ["capability", "engine", "score", "category"])]
    for score, capability in chosen[:3]:
        lines.append("")
        lines.append("## %s" % capability["id"])
        lines.append(capability.get("description", "")[:240])
        lines.append("")
        lines.append("Try: `python3 tools-ultimate-text.py run %s --help`" % capability["id"])
    return {"output": "\n".join(lines), "data": data}


QUESTIONS = [
    ("audience", "Who reads the final text, and how expert are they in the subject?"),
    ("purpose", "What must the reader do after reading it (decide, act, learn, comply)?"),
    ("length", "Is there a length limit, in words or characters?"),
    ("format", "Which output format is required (plain text, markdown, JSON, SRT, table)?"),
    ("tone", "What tone and register are expected, and is humour or jargon acceptable?"),
    ("constraints", "Which words, claims or topics must be avoided?"),
    ("sources", "Which sources must be cited, and must claims be traceable to them?"),
    ("deadline", "When is it due, and who approves the final version?"),
    ("locale", "Which language, region and date/number conventions apply?"),
    ("accessibility", "Does the text need to meet an accessibility standard or reading level?"),
    ("data", "Does the text contain personal data, secrets or regulated information?"),
    ("success", "How will you judge that the result is good enough?"),
]


def requirement_elicitor(ctx):
    mission = ctx.text.strip() or ctx.params.get("mission", "").strip()
    limit = int(ctx.params["max_questions"])
    questions = []
    lowered = mission.lower()
    covered = set()
    for key, question in QUESTIONS:
        cues = {"audience": ["reader", "audience", "customer", "executive"],
                "length": ["word", "page", "short", "brief", "long"],
                "format": ["format", "markdown", "json", "srt", "table"],
                "tone": ["tone", "formal", "friendly", "voice"],
                "sources": ["source", "cite", "reference", "evidence"],
                "locale": ["language", "locale", "region", "translate"],
                "data": ["personal", "pii", "secret", "confidential"]}.get(key, [])
        if any(cue in lowered for cue in cues):
            covered.add(key)
            continue
        questions.append({"id": key, "question": question})
        if len(questions) >= limit:
            break
    if not questions:
        questions = [{"id": "confirm", "question": "The brief already covers audience, length, format, tone and sources - confirm nothing else is fixed."}]
    data = {"mission": mission, "questions": questions, "assumed": sorted(covered)}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    lines = ["# Requirements to confirm", ""]
    if mission:
        lines.append("Brief: %s" % mission)
        lines.append("")
    lines.extend("- **%s**: %s" % (q["id"], q["question"]) for q in questions)
    if covered:
        lines.append("")
        lines.append("Already stated in the brief: %s" % ", ".join(sorted(covered)))
    return {"output": "\n".join(lines), "data": data}


def output_contract_builder(ctx):
    text = ctx.text.strip()
    if not text:
        raise core.InvalidInput("give a capability id (for example editing.grammar-and-mechanics-editor)")
    capability = ctx.catalog.resolve(text)
    include_checks = bool(ctx.params["include_checks"])
    contract = H.build_contract(_ctx_for(ctx, capability))
    if ctx.params["format"] == "json":
        payload = dict(contract)
        if not include_checks:
            payload.pop("acceptance_checks", None)
        return {"output": json.dumps(payload, indent=2, ensure_ascii=False),
                "data": {"capability": capability["id"], "contract": payload}}
    markdown = H.contract_markdown(contract)
    return {"output": markdown, "data": {"capability": capability["id"],
                                         "fields": sorted(contract), "format": "markdown"}}


def _ctx_for(ctx, capability):
    params = ctx.catalog.default_params(capability)
    return ctx.core.Context(ctx.core, ctx.assets, ctx.catalog, capability, params, ctx.cfg,
                            [ctx.text, ctx.text2, ctx.text3], ctx.cwd, approve=ctx.approve,
                            dry_run=True, timeout=ctx.timeout, encoding=ctx.encoding)


def selection_advisor(ctx):
    task = ctx.text.strip() or ctx.params.get("mission", "").strip()
    if not task:
        raise core.InvalidInput("describe the task in text so a tool can be recommended")
    top_n = int(ctx.params["top_n"])
    explain = bool(ctx.params["explain"])
    tokens = _tokens(task)
    scored = []
    for category in ctx.catalog.categories:
        for capability in category["skills"]:
            score = score_capability(capability, tokens)
            if score > 0:
                scored.append((score, capability))
    scored.sort(key=lambda pair: -pair[0])
    if not scored:
        raise core.InvalidInput("nothing in the catalog matches %r" % task)
    rows = []
    for score, capability in scored[:top_n]:
        reason = "deterministic local handler" if capability["engine"] == "local" else "needs the calling assistant"
        rows.append([capability["id"], capability["engine"], round(score, 2),
                     reason if explain else ""])
    data = {"task": task, "recommendations": [{"id": c["id"], "score": round(s, 2)} for s, c in scored[:top_n]]}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    return {"output": core.table(rows, ["capability", "engine", "score", "why"]), "data": data}


def verification_orchestrator(ctx):
    text = ctx.require_text(minimum=1)
    checks = [c.lower() for c in ctx.params["checks"]]
    tables = ctx.assets.json(SEC_ASSET)
    lex = ctx.assets.json(LEX_ASSET)
    import unicodedata
    results = []

    def add(name, status, detail):
        results.append({"check": name, "status": status, "detail": detail})

    if "unicode" in checks:
        invisible = sum(1 for c in text if unicodedata.category(c) in ("Cf", "Cc") and c not in "\n\r\t")
        add("unicode", "warn" if invisible or not unicodedata.is_normalized("NFC", text) else "pass",
            "%d invisible/control, NFC=%s" % (invisible, unicodedata.is_normalized("NFC", text)))
    if "pii" in checks:
        hits = [(kind, len(re.findall(spec["pattern"], text))) for kind, spec in tables["pii"].items()]
        total = sum(count for _, count in hits)
        add("pii", "fail" if total else "pass", "%d match(es)" % total)
    if "secrets" in checks:
        found = [kind for kind, spec in tables["secrets"].items() if re.search(spec["pattern"], text)]
        add("secrets", "fail" if found else "pass", ", ".join(found) or "none")
    if "readability" in checks:
        grade = H.reading_metrics(text).get("fk_grade") or 0
        add("readability", "warn" if grade > 12 else "pass", "Flesch-Kincaid grade %.1f" % grade)
    if "lint" in checks:
        issues = 0
        for name, (pattern, _) in (("double-space", (r"[^\n ] {2,}[^\n ]", "")),
                                   ("repeated-word", (r"\b(\w+)\s+\1\b", "")),
                                   ("trailing-space", (r"[ \t]+$", ""))):
            issues += len(re.findall(pattern, text, re.M))
        add("lint", "warn" if issues else "pass", "%d mechanical issue(s)" % issues)
    if "length" in checks:
        words = len(text.split())
        add("length", "pass" if 10 <= words <= 5000 else "warn", "%d words" % words)
    if "encoding" in checks:
        add("encoding", "pass", "utf-8 encodable, %d bytes" % len(text.encode("utf-8")))
    if "structure" in checks:
        add("structure", "pass", "%d paragraph(s), %d line(s)" % (len([p for p in text.split("\n\n") if p.strip()]),
                                                                  text.count("\n") + 1))
    failed = [r for r in results if r["status"] == "fail"]
    warned = [r for r in results if r["status"] == "warn"]
    verdict = "fail" if failed else ("review" if warned else "pass")
    if ctx.params["format"] == "json":
        output = json.dumps({"verdict": verdict, "checks": results}, indent=2, ensure_ascii=False)
    else:
        output = "\n".join(["# Verification: %s" % verdict.upper(), "",
                            core.table([[r["check"], r["status"], r["detail"]] for r in results],
                                       ["check", "status", "detail"])])
    return {"output": output, "data": {"verdict": verdict, "checks": results,
                                       "failed": len(failed), "warned": len(warned)},
            "warnings": ["%d failing check(s)" % len(failed)] if failed else []}


def repair_manager(ctx):
    text = ctx.require_text(minimum=0)
    strategy = ctx.params["strategy"]
    max_steps = int(ctx.params["max_steps"])
    import unicodedata
    current = text
    steps = []
    candidates = []
    if strategy in ("auto", "encoding") and ("\ufffd" in current or _looks_mojibake(current)):
        candidates.append("mojibake")
    if strategy in ("auto", "unicode") and any(unicodedata.category(c) == "Cf" for c in current):
        candidates.append("invisible")
    if strategy in ("auto", "newlines") and "\r" in current:
        candidates.append("newlines")
    if strategy in ("auto", "unicode") and not unicodedata.is_normalized("NFC", current):
        candidates.append("nfc")
    if strategy in ("auto", "whitespace") and re.search(r"[ \t]+$", current, re.M):
        candidates.append("trailing-space")
    if not candidates:
        return {"output": "no repairable defect detected for strategy %r" % strategy,
                "data": {"steps": [], "changed": False, "strategy": strategy},
                "warnings": ["if the problem is semantic, repair cannot help: regenerate the text instead"]}
    for step in candidates[:max_steps]:
        before = current
        if step == "mojibake":
            for _ in range(2):
                try:
                    current = current.encode("latin-1").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    break
        elif step == "invisible":
            current = "".join(c for c in current if unicodedata.category(c) != "Cf")
        elif step == "newlines":
            current = core.canonical_newlines(current)
        elif step == "nfc":
            current = unicodedata.normalize("NFC", current)
        elif step == "trailing-space":
            current = "\n".join(line.rstrip() for line in current.split("\n"))
        steps.append({"step": step, "changed": current != before,
                      "chars_before": len(before), "chars_after": len(current)})
    data = {"strategy": strategy, "steps": steps, "changed": current != text,
            "checksum_before": core.sha256_text(text)[:16],
            "checksum_after": core.sha256_text(current)[:16], "success": current != text}
    output = "\n".join(["# Repair report", "",
                        core.table([[s["step"], s["changed"], s["chars_before"], s["chars_after"]]
                                    for s in steps], ["step", "changed", "chars before", "chars after"]),
                        "", "## Repaired text", current])
    return {"output": output, "data": data,
            "warnings": [] if data["changed"] else ["no step changed the text: the defect is elsewhere"]}


def _looks_mojibake(text):
    return bool(re.search(r"(Ã.|â€|Â.)", text))


def capability_composer(ctx):
    goal = ctx.params["goal"].strip() or ctx.text.strip()
    if not goal:
        raise core.InvalidInput("give --set goal='clean up and publish a support reply'")
    limit = int(ctx.params["max_steps"])
    tokens = _tokens(goal)
    scored = []
    for category in ctx.catalog.categories:
        for capability in category["skills"]:
            if capability["engine"] != "local":
                continue
            score = score_capability(capability, tokens)
            if score > 0:
                scored.append((score, capability))
    scored.sort(key=lambda pair: -pair[0])
    if not scored:
        raise core.InvalidInput("no local capability matched the goal")
    chosen, seen = [], set()
    for score, capability in scored:
        if capability["category_slug"] in seen:
            continue
        seen.add(capability["category_slug"])
        chosen.append(capability)
        if len(chosen) >= limit:
            break
    spec = "|".join(c["id"] for c in chosen)
    lines = ["# Composed pipeline", ""]
    lines.append(core.table([[str(i + 1), c["id"], round(s, 2) if isinstance(s, float) else ""]
                             for i, (s, c) in enumerate([(score_capability(c, tokens), c) for c in chosen])],
                            ["#", "capability", "score"]))
    lines.append("")
    lines.append("Run it with:")
    lines.append("")
    lines.append("```")
    lines.append("python3 tools-ultimate-text.py run automation.text-pipeline-architect --text-file input.txt "
                 "--set \"pipeline=%s\"" % spec)
    lines.append("```")
    lines.append("")
    lines.append("Each step receives the previous step's output, so order matters. Drop steps whose "
                 "category overlaps to avoid double-processing.")
    data = {"goal": goal, "pipeline": spec, "steps": [c["id"] for c in chosen]}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    return {"output": "\n".join(lines), "data": data,
            "warnings": ["review the order before running: the composed chain is a heuristic suggestion"]}


def preference_profiler(ctx):
    text = ctx.text
    name = ctx.params["name"]
    pairs = dict(re.findall(r"([a-z_]+)\s*=\s*([^;\n]+)", text))
    for key in ("tone", "register", "length", "locale", "date_style", "quote_style"):
        match = re.search(r"\b%s\s*(?:is|:)\s*([^.;\n]+)" % key, text, re.I)
        if match and key not in pairs:
            pairs[key] = match.group(1).strip()
    profile = {"name": name or "profile", "preferences": pairs,
               "defaults_from_system": {"date_style": "iso", "quote_style": "typographic",
                                        "length": "unspecified"},
               "created": core.now_iso(),
               "conflicts": []}
    if pairs.get("quote_style") in ("straight", "ascii") and pairs.get("smart_quotes") == "true":
        profile["conflicts"].append("straight quotes requested while smart quotes are enabled")
    data = profile
    output = (json.dumps(profile, indent=2, ensure_ascii=False) if ctx.params["format"] == "json"
              else "\n".join(["# Preference profile: %s" % profile["name"], "",
                              core.table([[k, v] for k, v in sorted(pairs.items())], ["setting", "value"])
                              if pairs else "no explicit preferences found in the input",
                              "", "Add preferences as `key=value` pairs or 'tone: friendly' phrases."]))
    return {"output": output, "data": data,
            "warnings": ["%d conflicting preference(s)" % len(profile["conflicts"])]
            if profile["conflicts"] else []}


def knowledge_curator(ctx):
    action = ctx.params["action"]
    directory = ctx.params["dir_path"].strip() or "."
    glob_pattern = ctx.params["glob"]
    query = ctx.params["query"].strip()
    top_k = int(ctx.params["top_k"])
    max_files = int(ctx.params["max_files"])
    root = ctx.resolve(directory, must_exist=True, kind="dir")
    files = H.list_files(root, glob_pattern, True, max_files)
    if not files:
        raise core.InvalidInput("no files matched %r under %s" % (glob_pattern, directory))
    documents = []
    for relative in files:
        try:
            body = core.read_text_file(os.path.join(root, relative), encoding=ctx.encoding,
                                       errors="replace", max_bytes=int(ctx.cfg.get("max_input_bytes", 1048576)))
        except core.SkillError:
            continue
        documents.append({"path": relative, "text": body, "words": len(body.split())})
    if action == "index":
        rows = [[d["path"], d["words"]] for d in documents]
        return {"output": core.table(rows, ["document", "words"]),
                "data": {"documents": len(documents), "root": root,
                         "total_words": sum(d["words"] for d in documents)}}
    if action == "list":
        return {"output": "\n".join("%s (%d words)" % (d["path"], d["words"]) for d in documents),
                "data": {"documents": [d["path"] for d in documents], "count": len(documents)}}
    if not query:
        raise core.InvalidInput("give --set query=... for a search")
    import math
    tokens = _tokens(query)
    docs_tokens = [re.findall(r"[a-z0-9']+", d["text"].lower()) for d in documents]
    df = {}
    for tokens_doc in docs_tokens:
        for token in set(tokens_doc):
            df[token] = df.get(token, 0) + 1
    scored = []
    for document, tokens_doc in zip(documents, docs_tokens):
        if not tokens_doc:
            continue
        score = 0.0
        for token in tokens:
            tf = tokens_doc.count(token) / len(tokens_doc)
            idf = math.log((1 + len(documents)) / (1 + df.get(token, 0))) + 1
            score += tf * idf
        if score > 0:
            snippet = ""
            for token in tokens:
                position = document["text"].lower().find(token)
                if position >= 0:
                    snippet = document["text"][max(0, position - 40):position + 80].replace("\n", " ")
                    break
            scored.append({"path": document["path"], "score": round(score, 4), "snippet": snippet})
    scored.sort(key=lambda item: -item["score"])
    data = {"query": query, "results": scored[:top_k], "documents": len(documents),
            "searched_terms": tokens}
    rows = [[r["path"], r["score"], r["snippet"][:70]] for r in scored[:top_k]]
    return {"output": core.table(rows, ["document", "score", "snippet"]) if rows else "no document matched",
            "data": data}


def evaluation_designer(ctx):
    cases = int(ctx.params["cases"])
    text = ctx.text.strip()
    capability = ctx.catalog.resolve(text) if text else None
    if capability is None:
        raise core.InvalidInput("give a capability id to design an evaluation for")
    rng = core.seeded_rng(capability["id"])
    kinds = ["typical", "edge", "invalid", "regression", "adversarial", "empty", "large", "mixed-script"]
    rows = []
    for index in range(cases):
        kind = kinds[index % len(kinds)]
        expectation = {
            "typical": "produces the documented output shape without warnings",
            "edge": "handles the boundary without crashing (empty line, single word, maximum length)",
            "invalid": "raises a structured error with a hint and a non-zero exit code",
            "regression": "byte-identical output to the recorded golden sample",
            "adversarial": "does not follow instructions embedded in the input text",
            "empty": "returns an empty-but-valid result instead of an exception",
            "large": "completes inside the configured timeout at the documented input limit",
            "mixed-script": "keeps non-ASCII text intact and reports the scripts it saw",
        }[kind]
        rows.append([str(index + 1), kind, expectation])
    output = "\n".join(["# Evaluation plan: %s" % capability["id"], "",
                        "Acceptance checks from the catalog: %s" %
                        ("; ".join(capability.get("acceptance_checks") or []) or "none recorded"),
                        "", core.table(rows, ["case", "kind", "expectation"]),
                        "", "Run each case through `run %s` and record the envelope status plus the output checksum."
                        % capability["id"]])
    data = {"capability": capability["id"], "cases": cases,
            "plan": [{"case": int(r[0]), "kind": r[1], "expectation": r[2]} for r in rows]}
    return {"output": output, "data": data,
            "warnings": ["cases are generated from templates, not from real defects"]}


def catalog_maintainer(ctx):
    action = ctx.params["action"]
    handlers = _handlers()
    report = catalog_report(ctx.catalog)
    problems = []
    local_ids = set()
    for category in ctx.catalog.categories:
        for capability in category["skills"]:
            cid = capability["id"]
            if capability["engine"] == "local":
                local_ids.add(cid)
                if cid not in handlers:
                    problems.append({"id": cid, "issue": "no handler bound"})
            else:
                if cid in handlers:
                    problems.append({"id": cid, "issue": "agent capability has a local handler"})
            for name, spec in (capability.get("params") or {}).items():
                if spec.get("type") == "enum" and spec.get("default") not in spec.get("values", []):
                    problems.append({"id": cid, "issue": "default %r not in enum values for %s"
                                     % (spec.get("default"), name)})
                if spec.get("type") == "integer":
                    low, high = spec.get("min"), spec.get("max")
                    default = spec.get("default")
                    if low is not None and default is not None and default < low:
                        problems.append({"id": cid, "issue": "default below min for %s" % name})
                    if high is not None and default is not None and default > high:
                        problems.append({"id": cid, "issue": "default above max for %s" % name})
            if not capability.get("description"):
                problems.append({"id": cid, "issue": "missing description"})
    extra = sorted(set(handlers) - local_ids)
    for cid in extra:
        problems.append({"id": cid, "issue": "handler bound to an id that is not in the catalog"})
    status = "ok" if not problems else "problems"
    if ctx.params["format"] == "json":
        payload = {"status": status, "report": report, "problems": problems}
        return {"output": json.dumps(payload, indent=2, ensure_ascii=False), "data": payload}
    lines = ["# Catalog maintenance: **%s**" % status, ""]
    lines.append("- capabilities: %d (local %d, agent %d)"
                 % (report["total"], report["by_engine"].get("local", 0), report["by_engine"].get("agent", 0)))
    lines.append("- local handlers bound: %d" % len(local_ids & set(handlers)))
    lines.append("")
    lines.append(core.table([[p["id"], p["issue"]] for p in problems[:60]], ["capability", "issue"])
                 if problems else "no structural problems found")
    return {"output": "\n".join(lines), "data": {"status": status, "report": report, "problems": problems},
            "warnings": ["%d structural problem(s)" % len(problems)] if problems else []}


def production_director(ctx):
    goal = ctx.params["goal"].strip() or ctx.text.strip()
    gate = bool(ctx.params["gate"])
    if not goal:
        raise core.InvalidInput("give --set goal='what the finished text must achieve'")
    tokens = _tokens(goal)
    stages = [("interpret", ["analysis.document-structure-analyzer", "analysis.key-information-extractor"]),
              ("clean", ["typography.typography-cleaner", "editing.grammar-and-mechanics-editor"]),
              ("shape", ["editing.plain-language-converter", "editing.text-quality-finisher"]),
              ("verify", ["automation.publication-readiness-gate", "integrity.encoding-forensics-analyst"]),
              ("label", ["integrity.text-provenance-labeler"])]
    plan = []
    for stage, candidates in stages:
        best = None
        best_score = -1
        for cid in candidates:
            try:
                capability = ctx.catalog.get(cid)
            except core.SkillError:
                continue
            score = score_capability(capability, tokens) + 0.1
            if score > best_score:
                best, best_score = cid, score
        if best:
            plan.append({"stage": stage, "capability": best})
    text = ctx.require_text(minimum=0)
    executed = []
    current = text
    if text.strip():
        if gate:
            ctx.gate("run the multi-stage production pipeline", {"stages": len(plan)})
        for step in plan:
            try:
                result = H.run_capability(ctx, step["capability"], current)
            except core.SkillError as exc:
                executed.append({"stage": step["stage"], "capability": step["capability"],
                                 "status": "error", "error": exc.to_dict()})
                break
            current = result.get("output") or current
            executed.append({"stage": step["stage"], "capability": step["capability"],
                             "status": "ok", "chars": len(current)})
    else:
        executed.append({"stage": "plan-only", "capability": "-", "status": "no input text supplied"})
    lines = ["# Production plan", ""]
    lines.append(core.table([[s["stage"], s["capability"], next((e["status"] for e in executed
                                                                if e["capability"] == s["capability"]), "planned")]
                             for s in plan], ["stage", "capability", "status"]))
    if text.strip() and current.strip():
        lines.append("")
        lines.append("## Result")
        lines.append(current)
    else:
        lines.append("")
        lines.append("Supply text (or --text-file) to execute the plan; this run only planned it.")
    data = {"goal": goal, "gate": gate, "plan": plan, "executed": executed,
            "final_chars": len(current), "executed_pipeline": current != text}
    failed = [e for e in executed if e.get("status") == "error"]
    return {"output": "\n".join(lines), "data": data,
            "warnings": ["%d stage(s) failed" % len(failed)] if failed else []}


HANDLERS = {
    "meta.text-task-router": task_router,
    "meta.text-requirement-elicitor": requirement_elicitor,
    "meta.text-output-contract-builder": output_contract_builder,
    "meta.text-tool-selection-advisor": selection_advisor,
    "meta.text-verification-orchestrator": verification_orchestrator,
    "meta.text-repair-and-recovery-manager": repair_manager,
    "meta.text-capability-composer": capability_composer,
    "meta.text-preference-profiler": preference_profiler,
    "meta.text-knowledge-base-curator": knowledge_curator,
    "meta.text-skill-evaluation-designer": evaluation_designer,
    "meta.text-skill-catalog-maintainer": catalog_maintainer,
    "meta.end-to-end-text-production-director": production_director,
}
