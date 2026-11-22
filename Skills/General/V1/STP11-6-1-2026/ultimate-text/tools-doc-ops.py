"""Professional/workplace documents (12) and creative writing (12).

Six professional capabilities and four creative capabilities are agent-assisted
(they emit task contracts). Everything in this module is a deterministic
transformation, formatter, extractor or seeded generator.
"""

from __future__ import annotations

import json
import re

EXPORTS = ["render_json_template", "extract_sections"]
LEX_ASSET = "asset-lexicons-language.json"

WEAK_VERBS = {"responsible for", "worked on", "helped with", "involved in", "assisted with",
              "participated in", "tasked with", "duties included", "handled"}
STRONG_VERBS = ["led", "built", "shipped", "reduced", "increased", "designed", "migrated",
                "automated", "negotiated", "launched", "cut", "grew", "delivered", "trained"]

SUPPORT_TEMPLATES = {
    "refund": [
        "Thanks for flagging this. I have started the refund for order {order}.",
        "Refunds normally settle in 5-10 business days; you will get an email when it is issued.",
        "If the money has not arrived by then, reply here and I will escalate it with the payments team.",
    ],
    "shipping": [
        "Thanks for your patience. I checked the tracking for order {order}.",
        "The latest scan shows the parcel is in transit; delivery estimates update every 24 hours.",
        "If there is no movement in two business days, I will open a carrier investigation for you.",
    ],
    "technical": [
        "Thanks for the detail - that helps. Here is what I can confirm so far.",
        "Please send the exact error text, the time it happened, and the steps that triggered it.",
        "Once I have those, I will reproduce it and come back with either a fix or a workaround.",
    ],
    "cancel": [
        "I can help with that. I have queued the cancellation for order {order}.",
        "You will keep access until the end of the current billing period, and no further charges will be made.",
        "If you change your mind, the account can be restored within 30 days without losing your data.",
    ],
    "apology": [
        "I am sorry - this is not the experience we want you to have.",
        "Here is what happened and what we are doing about it: {detail}.",
        "I will follow up personally once the fix is confirmed.",
    ],
    "general": [
        "Thanks for reaching out - I can help with this.",
        "Here is the short answer: {detail}.",
        "Tell me if you would like me to take it further and I will pick it up from here.",
    ],
}


def _lex(ctx):
    return ctx.assets.json(LEX_ASSET)


def render_json_template(template, fields):
    out = template
    for key, value in fields.items():
        out = out.replace("{%s}" % key, str(value))
    return out


def extract_sections(text):
    """Split a document into heading -> body sections (markdown or ALL CAPS headings)."""
    sections, current = [], {"heading": "", "lines": []}
    for line in core.canonical_newlines(text).split("\n"):
        markdown = re.match(r"^(#{1,6})\s+(.*)$", line)
        caps = re.match(r"^([A-Z][A-Z &/'-]{3,60}):?\s*$", line)
        if markdown or caps:
            if current["heading"] or current["lines"]:
                sections.append(current)
            current = {"heading": (markdown.group(2) if markdown else caps.group(1)).strip(),
                       "lines": []}
        else:
            current["lines"].append(line)
    if current["heading"] or current["lines"]:
        sections.append(current)
    for section in sections:
        section["body"] = "\n".join(section["lines"]).strip()
    return sections


# --------------------------------------------------------------------------
# professional
# --------------------------------------------------------------------------


def meeting_assistant(ctx):
    text = ctx.require_text(minimum=20)
    action = ctx.params["action"]
    fmt = ctx.params["format"]
    lines = [ln.strip() for ln in core.canonical_newlines(text).split("\n") if ln.strip()]
    speakers = {}
    decisions, actions, topics = [], [], []
    for line in lines:
        speaker = re.match(r"^([A-Z][A-Za-z .'-]{1,25}):\s*(.+)$", line)
        if speaker:
            name = speaker.group(1).strip()
            speakers[name] = speakers.get(name, 0) + 1
            body = speaker.group(2)
        else:
            body = line
        if re.search(r"\b(decided|agreed|approved|signed off|conclusion)\b", body, re.I):
            decisions.append(body)
        if re.search(r"\b(will|to do|action|follow up|next step|owner:|due)\b", body, re.I):
            owner = re.search(r"(?:owner|assigned to)\s*[:\-]?\s*([A-Z][A-Za-z]+)", body)
            due = re.search(r"\b(?:by|due)\s+([A-Za-z0-9 ,/.-]{3,20})", body)
            actions.append({"action": body, "owner": owner.group(1) if owner else "",
                            "due": due.group(1).strip() if due else ""})
        if line.endswith(":") or re.match(r"^(topic|agenda)\b", line, re.I):
            topics.append(line.rstrip(":"))
    summary_points = H.summarize_extractive(text, max_sentences=4)
    data = {"speakers": speakers, "decisions": decisions, "actions": actions,
            "topics": topics, "summary": summary_points}
    if fmt == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    blocks = []
    if action in ("summary", "all"):
        blocks.append("## Summary\n" + " ".join(summary_points))
    if action in ("decisions", "all"):
        blocks.append("## Decisions\n" + ("\n".join("- %s" % d for d in decisions)
                                          if decisions else "- none recorded"))
    if action in ("actions", "all"):
        rows = [[a["action"][:80], a["owner"] or "-", a["due"] or "-"] for a in actions]
        blocks.append("## Actions\n" + (core.table(rows, ["action", "owner", "due"])
                                        if rows else "no actions detected"))
    if action == "minutes":
        header = "## Meeting minutes\nSpeakers: %s\n" % (", ".join("%s (%d)" % kv for kv in speakers.items()) or "unknown")
        blocks.insert(0, header)
    return {"output": "\n\n".join(blocks), "data": data}


def business_templates(ctx):
    fields_raw = ctx.params["fields"].strip() or "{}"
    try:
        fields = json.loads(fields_raw)
    except json.JSONDecodeError as exc:
        raise core.InvalidInput("--set fields must be JSON: %s" % exc.msg)
    template = ctx.params["template"]
    if not fields:
        fields = {"project": "<project>", "owner": "<owner>", "date": "<date>",
                  "status": "<status>", "risk": "<risk>", "next_step": "<next step>"}
    blocks = {
        "status-update": """# Status update - {project}
Date: {date}
Owner: {owner}
Status: {status}

## Done this period
- {next_step}

## Next period
- {next_step}

## Risks and blockers
- {risk}

## Asks
- {risk}
""",
        "meeting-agenda": """# Agenda - {project}
Date: {date}
Facilitator: {owner}

1. Context and goal (5 min) - {project}
2. Status review (10 min) - {status}
3. Risks and decisions needed (10 min) - {risk}
4. Next steps and owners (5 min) - {next_step}

Pre-read: status update shared before {date}.
""",
        "proposal-outline": """# Proposal - {project}
Prepared by {owner} on {date}

1. Problem and evidence
2. Proposed approach
3. Scope and out of scope
4. Timeline and milestones ({date})
5. Resourcing and cost
6. Risks and mitigations ({risk})
7. Decision requested ({next_step})
""",
        "project-brief": """# Project brief - {project}
Owner: {owner}
Date: {date}
Status: {status}

## Objective
## Success measures
## Scope
## Milestones
## Risks
## Approvals
""",
        "risk-log": """# Risk log - {project}

| id | risk | likelihood | impact | owner | mitigation | status |
| --- | --- | --- | --- | --- | --- | --- |
| R1 | {risk} | medium | high | {owner} | {next_step} | open |
""",
        "decision-record": """# Decision record - {project}
Date: {date}
Decision owner: {owner}
Status: {status}

## Context
## Decision
## Alternatives considered
## Consequences
## Follow-up
""",
        "handover": """# Handover - {project}
Prepared by {owner} on {date}

## What this is
## Current status ({status})
## Key contacts and systems
## Known risks ({risk})
## First three actions for the new owner
1. {next_step}
""",
    }
    template_text = blocks.get(template)
    if template_text is None:
        raise core.InvalidParam("unknown template %r" % template,
                                hint="Available: %s" % ", ".join(sorted(blocks)))
    output = render_json_template(template_text, fields)
    missing = sorted({m for m in re.findall(r"\{(\w+)\}", output)})
    return {"output": output, "data": {"template": template, "fields": sorted(fields),
                                       "unresolved": missing},
            "warnings": ["unresolved fields: %s" % ", ".join(missing)] if missing else []}


def learning_materials(ctx):
    text = ctx.require_text(minimum=80)
    kind = ctx.params["kind"]
    count = int(ctx.params["count"])
    level = ctx.params["level"]
    freq = H.word_freq(text)
    terms = [word for word, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:count]]
    sentences = [s for s in core.sentence_split(text) if len(s.split()) > 4]
    headings = [line.lstrip("# ").strip() for line in core.canonical_newlines(text).split("\n")
                if re.match(r"^#{1,6}\s", line)]
    blocks = []
    if kind in ("glossary", "all"):
        rows = [[term, _define(text, term)] for term in terms]
        blocks.append("## Glossary\n" + (core.table(rows, ["term", "definition from context"]) if rows
                                          else "no terms extracted"))
    if kind in ("objectives", "all"):
        objectives = ["Explain what %s means" % terms[0]] if terms else []
        objectives += ["Apply %s to a realistic example" % t for t in terms[1:4]]
        objectives += ["Identify risks when %s is used" % terms[0]] if terms else []
        blocks.append("## Learning objectives (%s)\n" % level + "\n".join("- %s" % o for o in objectives[:6]))
    if kind in ("outline", "all"):
        outline = ["## Outline"]
        if headings:
            outline.extend("- %s" % h for h in headings[:12])
        else:
            outline.extend("- Part %d: %s" % (i + 1, " ".join(s.split()[:8]))
                           for i, s in enumerate(sentences[:8]))
        blocks.append("\n".join(outline))
    if kind in ("flashcards", "all"):
        cards = []
        for term in terms[: max(1, count // 2)]:
            cards.append("Front: What is %s?\nBack: %s" % (term, _define(text, term)))
        blocks.append("## Flashcards\n" + "\n\n".join(cards))
    if kind in ("quiz", "all"):
        questions = []
        for idx, sentence in enumerate(sentences[:count]):
            if terms:
                term = terms[idx % len(terms)]
                if term in sentence.lower():
                    cloze = re.sub(r"\b%s\b" % re.escape(term), "_____", sentence, count=1, flags=re.I)
                    questions.append("%d. Fill in the blank: %s" % (idx + 1, cloze))
        if questions:
            questions.append("\nAnswers: " + ", ".join(terms[: len(questions)]))
        blocks.append("## Quiz\n" + ("\n".join(questions) if questions else "no cloze questions could be built"))
    data = {"kind": kind, "terms": terms, "sentences": len(sentences), "level": level}
    return {"output": "\n\n".join(blocks), "data": data}


def _define(text, term):
    for sentence in core.sentence_split(text):
        if term.lower() in sentence.lower():
            return core.truncate(sentence.strip(), 180)
    return "no definition sentence found"


def resume_editor(ctx):
    text = ctx.require_text(minimum=60)
    target = ctx.params["target"]
    style = ctx.params["style"]
    max_words = int(ctx.params["max_bullet_words"])
    sections = extract_sections(text)
    bullets, issues = [], []
    for section in sections:
        for line in section["body"].split("\n"):
            stripped = line.strip(" -*\u2022\t")
            if len(stripped.split()) < 3:
                continue
            bullets.append({"section": section["heading"] or "summary", "text": stripped})
    for bullet in bullets:
        text_b = bullet["text"]
        lowered = text_b.lower()
        if any(weak in lowered for weak in WEAK_VERBS):
            issues.append({"kind": "weak-phrasing", "text": text_b[:80],
                           "advice": "start with a strong verb: %s" % ", ".join(STRONG_VERBS[:5])})
        if not re.search(r"\d", text_b):
            issues.append({"kind": "no-metric", "text": text_b[:80],
                           "advice": "add a number: volume, time saved, percentage, or scale"})
        if len(text_b.split()) > max_words:
            issues.append({"kind": "too-long", "text": text_b[:80],
                           "advice": "cut to %d words or fewer" % max_words})
        if text_b.endswith("."):
            issues.append({"kind": "trailing-period", "text": text_b[:60],
                           "advice": "résumé bullets conventionally omit the final period"})
    out_bullets = []
    for bullet in bullets:
        body = bullet["text"].rstrip(".")
        if style == "bullets":
            out_bullets.append("- %s" % body)
        else:
            out_bullets.append(body)
    header = "## %s" % target.replace("-", " ").title()
    data = {"bullets": len(bullets), "issues": issues, "issue_count": len(issues),
            "sections": [s["heading"] for s in sections if s["heading"]]}
    output = header + "\n" + "\n".join(out_bullets) if style == "bullets" else "\n".join(out_bullets)
    if issues:
        rows = [[i["kind"], i["text"][:60], i["advice"]] for i in issues[:30]]
        output += "\n\n### Review\n" + core.table(rows, ["issue", "text", "advice"])
    return {"output": output, "data": data}


def support_response(ctx):
    text = ctx.require_text(minimum=5)
    intent = ctx.params["intent"]
    tone = ctx.params["tone"]
    max_words = int(ctx.params["max_words"])
    if intent == "auto":
        lowered = text.lower()
        if re.search(r"\brefund|money back|charged twice|overcharged\b", lowered):
            intent = "refund"
        elif re.search(r"\bshipping|delivery|tracking|parcel|arrived\b", lowered):
            intent = "shipping"
        elif re.search(r"\b(cancel|subscription|unsubscribe)\b", lowered):
            intent = "cancel"
        elif re.search(r"\b(error|bug|crash|not working|broken|fails)\b", lowered):
            intent = "technical"
        elif re.search(r"\b(sorry|apolog|disappoint)\b", lowered):
            intent = "apology"
        else:
            intent = "general"
    order = re.search(r"\b(?:order|ticket|case)\s*(?:#|number|id)?\s*[:#]?\s*([A-Z0-9-]{4,20})", text, re.I)
    detail = H.summarize_extractive(text, max_sentences=1)
    lines = SUPPORT_TEMPLATES[intent]
    body = render_json_template("\n\n".join(lines),
                                {"order": order.group(1) if order else "<order number>",
                                 "detail": detail[0] if detail else "<brief explanation>"})
    if tone == "formal":
        body = body.replace("Thanks for", "Thank you for").replace("I will", "I shall")
    elif tone == "neutral":
        body = body.replace("Thanks for", "Thank you for")
    words = body.split()
    if len(words) > max_words:
        body = " ".join(words[:max_words]).rstrip(".,") + "."
    escalation = []
    if re.search(r"\b(legal|lawyer|attorney|regulator|chargeback|ombudsman)\b", text, re.I):
        escalation.append("mentions legal or regulatory escalation: route to a supervisor before sending")
    if re.search(r"\b(suicide|self-harm|threat|abuse)\b", text, re.I):
        escalation.append("contains a safety or abuse signal: follow the trust-and-safety path, do not answer automatically")
    data = {"intent": intent, "tone": tone, "words": len(body.split()),
            "escalations": escalation, "order": order.group(1) if order else None}
    return {"output": body, "data": data,
            "warnings": escalation}


def legal_plain_language(ctx):
    text = ctx.require_text(minimum=40)
    lex = _lex(ctx)
    strength = ctx.params["strength"]
    out = text
    changes = []
    replacements = dict(lex["fluff_phrases"])
    replacements.update({k: v for k, v in lex["jargon"].items() if len(k) > 4})
    replacements.update({
        "hereinafter": "from now on", "hereunder": "under this agreement",
        "hereby": "now", "therein": "in that document", "thereto": "to that",
        "witnesseth": "records", "indemnify": "protect against loss",
        "hold harmless": "protect against claims", "force majeure": "unforeseeable events",
        "severability": "if one clause fails, the rest still apply",
        "notwithstanding": "even if", "shall": "must",
    })
    for phrase in sorted(replacements, key=len, reverse=True):
        pattern = re.compile(r"\b%s\b" % re.escape(phrase), re.I)
        if pattern.search(out):
            count = len(pattern.findall(out))
            if strength == "light" and len(phrase.split()) < 2 and phrase != "shall":
                continue
            out = H.replace_preserving_case(phrase, replacements[phrase], out)
            changes.append({"from": phrase, "to": replacements[phrase] or "(removed)", "count": count})
    obligations = []
    for sentence in core.sentence_split(out):
        if re.search(r"\b(must|shall|may not|is required|is entitled)\b", sentence, re.I):
            obligations.append(sentence.strip()[:200])
    defined_terms = re.findall(r"[\"“]([A-Z][A-Za-z ]{1,40})[\"”]", text)
    if ctx.params["flag_obligations"]:
        for sentence in core.sentence_split(text):
            if re.search(r"\b(shall|must)\b", sentence):
                changes.append({"from": "modal obligation", "to": "flagged",
                                "count": 1, "detail": sentence[:90]})
    data = {"changes": changes, "obligations": obligations,
            "defined_terms": sorted(set(defined_terms)), "strength": strength}
    lines = ["# Plain-language pass (legal)", ""]
    if changes:
        lines.append(core.table([[c.get("from", ""), c.get("to", ""), c.get("count", "")] for c in changes[:40]],
                                ["original", "replacement", "count"]))
    if obligations:
        lines.append("")
        lines.append("## Obligations to review with a lawyer")
        lines.extend("- %s" % o for o in obligations[:12])
    if defined_terms:
        lines.append("")
        lines.append("Defined terms kept: %s" % ", ".join(sorted(set(defined_terms))[:15]))
    output = out if ctx.params["keep_defined_terms"] else out
    return {"output": output + ("\n\n" + "\n".join(lines) if strength == "aggressive" else ""),
            "data": data,
            "warnings": ["plain-language conversion of legal text never replaces legal review"]}


# --------------------------------------------------------------------------
# creative
# --------------------------------------------------------------------------

SYLLABLE_WORDS = {
    "a": 1, "the": 1, "and": 1, "of": 1, "in": 1, "to": 1, "is": 1, "it": 1, "on": 1,
    "light": 1, "night": 1, "rain": 1, "sky": 1, "sea": 1, "stone": 1, "leaf": 1,
    "quiet": 2, "river": 2, "silver": 2, "morning": 2, "shadow": 2, "window": 2,
    "distant": 2, "lantern": 2, "whisper": 2, "orange": 2, "paper": 2,
    "tomorrow": 3, "remember": 3, "beautiful": 3, "andromeda": 4,
}


def _syllables(word):
    lowered = word.lower().strip(".,;:!?\"'")
    if lowered in SYLLABLE_WORDS:
        return SYLLABLE_WORDS[lowered]
    return H.count_syllables(lowered) if lowered else 0


def poetry_maker(ctx):
    text = ctx.require_text(minimum=1)
    form = ctx.params["form"]
    count = int(ctx.params["count"])
    rng = core.seeded_rng(ctx.params["seed"] or form)
    words = [w for w in re.split(r"[\s,/]+", text) if w.strip()]
    targets = {
        "haiku": [5, 7, 5], "tanka": [5, 7, 5, 7, 7],
        "limerick": [8, 8, 5, 5, 8], "chant": [4, 4, 6, 4], "free": None,
    }[form]
    if ctx.params["syllables"]:
        try:
            targets = [int(x) for x in re.split(r"[-, ]+", ctx.params["syllables"]) if x]
        except ValueError:
            raise core.InvalidParam("--set syllables expects numbers like 5-7-5")
    blocks = []
    for _ in range(count):
        pool = rng.shuffle(words) if words else ["silence"]
        if targets is None:
            lines = [" ".join(pool[i:i + 5]) for i in range(0, min(len(pool), 20), 5)]
            blocks.append("\n".join(lines) or "silence")
            continue
        lines, pool_idx = [], 0
        for budget in targets:
            line_words, used = [], 0
            while pool_idx < len(pool) and used < budget:
                word = pool[pool_idx]
                syll = _syllables(word)
                if used + syll > budget:
                    pool_idx += 1
                    continue
                line_words.append(word)
                used += syll
                pool_idx += 1
            fillers = ["still", "here", "now", "light", "slow", "gone", "far", "warm"]
            last = line_words[-1] if line_words else ""
            while used < budget:
                candidates = [f for f in fillers if f != last]
                filler = rng.choice(candidates)
                line_words.append(filler)
                last = filler
                used += _syllables(filler)
            lines.append(" ".join(line_words))
        blocks.append("\n".join(lines))
    data = {"form": form, "target_syllables": targets, "lines": len(blocks[0].split("\n")) if blocks else 0,
            "note": "form-fitting mode: line breaks follow syllable budgets, not semantics"}
    return {"output": "\n\n".join(blocks), "data": data,
            "warnings": ["automatic verse fitting produces metrical scaffolding, not finished poetry"]}


def dialogue_script(ctx):
    text = ctx.require_text(minimum=5)
    fmt = ctx.params["format"]
    action = ctx.params["action"]
    lines = [ln.rstrip() for ln in core.canonical_newlines(text).split("\n")]
    parsed, problems = [], []
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            parsed.append({"kind": "blank"})
            continue
        if re.match(r"^(INT|EXT|INT\./EXT|EST)[\. ]", stripped, re.I):
            parsed.append({"kind": "scene", "content": stripped})
            continue
        match = re.match(r"^([A-Z][A-Z0-9 .'\-]{1,28}):\s*(.*)$", stripped) or \
                re.match(r"^([A-Z][A-Z0-9 .'\-]{1,28})$", stripped)
        if match:
            name = match.group(1).strip()
            body = match.group(2) if match.lastindex and match.lastindex > 1 and match.group(2) else ""
            parsed.append({"kind": "character", "name": name, "content": body})
            continue
        if stripped.startswith("(") and stripped.endswith(")"):
            parsed.append({"kind": "parenthetical", "content": stripped})
            continue
        parsed.append({"kind": "action", "content": stripped})
    for idx, item in enumerate(parsed):
        if item["kind"] == "character" and not item.get("content"):
            if idx + 1 >= len(parsed) or parsed[idx + 1]["kind"] not in ("action", "parenthetical"):
                problems.append({"line": idx + 1, "issue": "character cue with no dialogue"})
    if action == "analyze":
        speakers = {}
        for item in parsed:
            if item["kind"] == "character":
                speakers[item["name"]] = speakers.get(item["name"], 0) + 1
        data = {"lines": len(parsed), "speakers": speakers,
                "scenes": sum(1 for p in parsed if p["kind"] == "scene"),
                "problems": problems}
        return {"output": core.table([[k, v] for k, v in sorted(speakers.items(), key=lambda kv: -kv[1])],
                                     ["speaker", "lines"]) + "\n\nproblems: %d" % len(problems)
                if speakers else "no dialogue detected", "data": data}
    if fmt == "chat":
        out = []
        for item in parsed:
            if item["kind"] == "character":
                out.append("**%s:** %s" % (item["name"], item.get("content", "")))
            elif item["kind"] == "action":
                out.append("_%s_" % item["content"])
        return {"output": "\n\n".join(out), "data": {"format": "chat", "problems": problems}}
    if fmt == "stage":
        out = []
        for item in parsed:
            if item["kind"] == "scene":
                out.append("SCENE: %s" % item["content"])
            elif item["kind"] == "character":
                out.append("%s: %s" % (item["name"].title(), item.get("content", "")))
            elif item["kind"] == "action":
                out.append("[%s]" % item["content"])
        return {"output": "\n\n".join(out), "data": {"format": "stage", "problems": problems}}
    fountain = []
    for item in parsed:
        if item["kind"] == "scene":
            fountain.append(item["content"].upper() if not item["content"].isupper() else item["content"])
        elif item["kind"] == "character":
            fountain.append("\n" + item["name"].upper())
        elif item["kind"] == "parenthetical":
            fountain.append(item["content"])
        elif item["kind"] == "action":
            fountain.append(item["content"])
        fountain.append("")
    return {"output": "\n".join(fountain).strip(), "data": {"format": fmt, "problems": problems},
            "warnings": ["%d structural problem(s) in the script" % len(problems)] if problems else []}


def interactive_fiction(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    action = ctx.params["action"]
    nodes = int(ctx.params["nodes"])
    rng = core.seeded_rng(ctx.params["seed"])
    if action == "validate":
        declared = set(re.findall(r"^##\s*(\w[\w-]*)", text, re.M))
        links = set(re.findall(r"->\s*(\w[\w-]*)", text))
        missing = sorted(links - declared)
        orphan = sorted(declared - links - {sorted(declared)[0]} if declared else [])
        data = {"nodes": sorted(declared), "broken_links": missing, "unreachable": orphan}
        output = "nodes: %d\nbroken links: %s\nunreachable: %s" % (
            len(declared), ", ".join(missing) or "none", ", ".join(orphan) or "none")
        return {"output": output, "data": data,
                "warnings": ["%d broken link(s)" % len(missing)] if missing else []}
    if action == "outline":
        sections = re.findall(r"^##\s*(\w[\w-]*)(.*?)(?=^##\s|\Z)", text, re.M | re.S)
        lines = ["%s -> %s" % (name, ", ".join(re.findall(r"->\s*(\w[\w-]*)", body)) or "(ending)")
                 for name, body in sections]
        return {"output": "\n".join(lines) or "no nodes found", "data": {"nodes": len(sections)}}
    names = ["start"] + ["node_%d" % i for i in range(1, nodes - 1)] + ["ending"]
    out = []
    for idx, name in enumerate(names):
        out.append("## %s" % name)
        if idx == 0:
            out.append("Opening scene: establish the situation and the first choice.")
            out.append("-> %s" % names[1])
        elif idx == len(names) - 1:
            out.append("Resolution: pay off the choice made at the start.")
        else:
            out.append("Scene %d: raise the stakes." % idx)
            out.append("-> %s" % names[idx + 1])
            out.append("-> %s" % rng.choice(names[: idx + 1]))
        out.append("")
    return {"output": "\n".join(out),
            "data": {"nodes": len(names), "structure": "linear spine with one backward link per node"}}


HOMOPHONES = [("their", "there"), ("to", "too"), ("your", "you're"), ("its", "it's"),
              ("then", "than"), ("weather", "whether"), ("peace", "piece"), ("right", "write"),
              ("bare", "bear"), ("seas", "seize")]


def wordplay_maker(ctx):
    text = ctx.require_text(minimum=1)
    kind = ctx.params["kind"]
    count = int(ctx.params["count"])
    rng = core.seeded_rng(ctx.params["seed"])
    words = [w.strip(".,;:!?") for w in text.split() if w.strip()]
    blocks = []
    if kind in ("anagram", "all"):
        picks = rng.shuffle(words)[: max(1, min(count, len(words)))]
        rows = [["".join(rng.shuffle(list(w))), w] for w in picks]
        blocks.append("## Anagrams\n" + core.table(rows, ["anagram", "source word"]))
    if kind in ("palindrome", "all"):
        found = [w for w in words if w.lower() == w.lower()[::-1] and len(w) > 2]
        joined = "".join(words).lower()
        blocks.append("## Palindromes\n" + ("\n".join("- %s" % w for w in found) if found
                                            else "- none in the supplied words (%d characters checked)"
                                            % len(joined)))
    if kind in ("acrostic", "all"):
        lines = []
        for word in words[:count]:
            lines.append("%s..." % word[:1].upper() + " " + rng.choice(
                ["begins the story", "opens the scene", "turns the key", "closes the door"]))
        blocks.append("## Acrostic scaffold\n" + "\n".join(lines))
    if kind in ("homophone", "all"):
        rows = []
        for a, b in HOMOPHONES:
            if re.search(r"\b%s\b" % a, text, re.I) or re.search(r"\b%s\b" % b, text, re.I):
                rows.append([a, b])
        blocks.append("## Homophones to watch\n" + (core.table(rows, ["word", "look-alike sound"])
                                                     if rows else "- none matched from the built-in set"))
    if kind in ("riddle", "all"):
        rows = []
        for word in words[:count]:
            rows.append(["I am not the whole, yet I start %s." % word,
                         word])
        blocks.append("## Riddle scaffolds\n" + core.table(rows, ["clue", "answer word"]))
    return {"output": "\n\n".join(blocks), "data": {"kind": kind, "words": len(words), "count": count}}


PHONEMES = {
    "fantasy": {"start": ["ael", "bran", "cael", "dorn", "el", "fen", "gal", "ith", "mor", "nyx", "syl", "thal"],
                "mid": ["a", "e", "i", "o", "ae", "ia", "or", "an", "el"],
                "end": ["dor", "wyn", "mere", "thil", "gard", "ia", "or", "ael", "ith"]},
    "scifi": {"start": ["ax", "cy", "dur", "ek", "hex", "ion", "kry", "lux", "neo", "orb", "quant", "vex"],
              "mid": ["a", "i", "o", "ex", "yn", "ar", "on"],
              "end": ["on", "ix", "ar", "um", "ex", "is", "os", "ax"]},
    "realistic": {"start": ["al", "ber", "car", "dan", "ed", "fran", "gil", "har", "jo", "mar", "ros", "sam"],
                  "mid": ["a", "e", "i", "o", "an", "el", "in"],
                  "end": ["ton", "ley", "ford", "son", "er", "man", "field", "brook"]},
    "brand": {"start": ["zen", "lum", "ver", "not", "str", "quest", "bright", "nu", "clarity", "shift"],
              "mid": ["a", "o", "i", "ify", "ly"],
              "end": ["ly", "io", "hub", "works", "lab", "base", "flow", "wise"]},
    "place": {"start": ["ash", "bram", "cold", "dun", "east", "fair", "green", "high", "kings", "north", "stone", "west"],
              "mid": ["a", "o", "en", "er"],
              "end": ["ford", "bridge", "haven", "hold", "wick", "moor", "vale", "stead", "reach"]},
}


def naming_studio(ctx):
    text = ctx.text.strip()
    style = ctx.params["style"] if ctx.params["style"] != "fantasy" or not text else ctx.params["style"]
    count = int(ctx.params["count"])
    max_len = int(ctx.params["max_len"])
    rng = core.seeded_rng(ctx.params["seed"])
    parts = PHONEMES.get(style, PHONEMES["fantasy"])
    roots = [r.strip() for r in re.split(r"[,\s]+", ctx.params["roots"]) if r.strip()]
    names = []
    attempts = 0
    while len(names) < count and attempts < count * 40:
        attempts += 1
        if roots and rng.next_int(3) == 0:
            candidate = rng.choice(roots) + rng.choice(parts["end"])
        else:
            candidate = rng.choice(parts["start"]) + rng.choice(parts["mid"]) + rng.choice(parts["end"])
        candidate = candidate[:1].upper() + candidate[1:]
        if len(candidate) > max_len:
            continue
        if candidate in names:
            continue
        names.append(candidate)
    rows = [[n, len(n), _name_note(n)] for n in names]
    return {"output": core.table(rows, ["name", "length", "shape"]),
            "data": {"style": style, "names": names, "seed": ctx.params["seed"],
                     "deterministic": True}}


def _name_note(name):
    vowels = sum(1 for c in name.lower() if c in "aeiouy")
    ratio = vowels / max(1, len(name))
    shape = "balanced" if 0.3 <= ratio <= 0.55 else ("vowel-heavy" if ratio > 0.55 else "consonant-heavy")
    return shape + (" + hard stop" if name.endswith(("k", "t", "x", "d")) else "")


def lore_continuity(ctx):
    text = ctx.require_text(minimum=60)
    checks = ctx.params["checks"]
    issues = []
    names = {}
    for match in re.finditer(r"\b([A-Z][a-z]{2,})(?:\s+([A-Z][a-z]{2,}))?", text):
        name = match.group(0)
        names[name] = names.get(name, 0) + 1
    if "names" in checks:
        variants = {}
        for name in names:
            key = name.lower().replace(" ", "")
            variants.setdefault(key, set()).add(name)
        for key, forms in variants.items():
            if len(forms) > 1:
                issues.append({"kind": "name-variant", "detail": ", ".join(sorted(forms))})
    dates = re.findall(r"\b\d{1,4}\s?(?:BC|BCE|AD|CE)?\b|\b(?:year|epoch|age)\s+\w+", text)
    if "dates" in checks and dates:
        numeric = [d for d in dates if d.strip().isdigit()]
        if numeric and len(set(numeric)) == len(numeric) and len(numeric) > 6:
            issues.append({"kind": "many-distinct-dates",
                           "detail": "%d distinct dates; check the timeline" % len(numeric)})
    numbers = re.findall(r"\b\d+\b", text)
    if "numbers" in checks:
        counts = {}
        for number in numbers:
            counts[number] = counts.get(number, 0) + 1
        repeated = {k: v for k, v in counts.items() if v > 2}
        if repeated:
            issues.append({"kind": "repeated-numbers",
                           "detail": ", ".join("%s x%d" % (k, v) for k, v in repeated.items())})
    titles = re.findall(r"\b(?:Lord|Lady|King|Queen|Captain|Doctor|Master|Elder)\s+[A-Z][a-z]+", text)
    if "titles" in checks:
        seen = {}
        for title in titles:
            base = title.split()[-1]
            seen.setdefault(base, set()).add(title.split()[0])
        for base, kinds in seen.items():
            if len(kinds) > 1:
                issues.append({"kind": "title-conflict", "detail": "%s uses %s" % (base, ", ".join(sorted(kinds)))})
    data = {"issues": issues, "entities": len(names), "dates": len(dates)}
    rows = [[i["kind"], i["detail"]] for i in issues]
    output = core.table(rows, ["issue", "detail"]) if rows else "no continuity problems detected"
    return {"output": output, "data": data,
            "warnings": ["%d continuity flag(s)" % len(issues)] if issues else []}


RP_TABLES = {
    "npc": {"names": ["Mira", "Hale", "Orin", "Sable", "Tamsin", "Bex", "Corin", "Nadia"],
            "traits": ["quietly observant", "impatient but fair", "overly formal", "warm and chaotic"],
            "wants": ["a missing ledger", "safe passage", "an apology", "a second chance"]},
    "encounter": {"setup": ["a collapsed bridge", "a crowded market", "a foggy crossroads", "an abandoned mill"],
                  "complication": ["someone is injured", "a toll must be paid", "the path is watched"],
                  "stakes": ["lose a day", "lose the cargo", "lose trust with the village"]},
    "tavern": {"names": ["The Bent Lantern", "The Copper Kettle", "The Salt Crow", "The Wandering Ox"],
               "detail": ["smells of woodsmoke", "has a locked cellar", "is run by two siblings"],
               "rumor": ["a storm is coming", "the road north is closed", "someone is paying in old coin"]},
    "loot": {"items": ["a brass compass that points wrong", "a sealed letter", "six silver coins",
                       "a chipped knife", "a map with one corner burned"]},
    "rumor": {"claims": ["the well sings at dusk", "the mayor has a second house", "no one returns by the north gate"],
              "reliability": ["probably true", "half true", "almost certainly false"]},
}


def roleplaying_text(ctx):
    kind = ctx.params["kind"]
    count = int(ctx.params["count"])
    rng = core.seeded_rng(ctx.params["seed"])
    kinds = ["npc", "encounter", "tavern", "loot", "rumor"] if kind == "all" else [kind]
    blocks = []
    for k in kinds:
        table = RP_TABLES.get(k, RP_TABLES["npc"])
        rows = []
        for idx in range(count):
            if k == "npc":
                rows.append([rng.choice(table["names"]), rng.choice(table["traits"]),
                             rng.choice(table["wants"])])
            elif k == "encounter":
                rows.append([rng.choice(table["setup"]), rng.choice(table["complication"]),
                             rng.choice(table["stakes"])])
            elif k == "tavern":
                rows.append([rng.choice(table["names"]), rng.choice(table["detail"]),
                             rng.choice(table["rumor"])])
            elif k == "loot":
                rows.append([rng.choice(table["items"])])
            else:
                rows.append([rng.choice(table["claims"]), rng.choice(table["reliability"])])
        headers = {"npc": ["name", "trait", "wants"], "encounter": ["setup", "complication", "stakes"],
                   "tavern": ["name", "detail", "rumor"], "loot": ["item"],
                   "rumor": ["claim", "reliability"]}[k]
        blocks.append("## %s\n%s" % (k, core.table(rows, headers)))
    return {"output": "\n\n".join(blocks),
            "data": {"kinds": kinds, "count": count, "seed": ctx.params["seed"],
                     "deterministic": True}}


def lyrics_designer(ctx):
    text = ctx.require_text(minimum=1)
    scheme = ctx.params["scheme"]
    line_count = int(ctx.params["lines"])
    count = int(ctx.params["count"])
    rng = core.seeded_rng(ctx.params["seed"])
    words = [w for w in re.split(r"[\s,/]+", text) if w.strip()]
    if not words:
        raise core.InvalidInput("supply theme words to build lines from")
    rhyme_sets = {}
    for word in words:
        key = word.lower()[-2:] if len(word) > 2 else word.lower()
        rhyme_sets.setdefault(key, []).append(word)
    rhyme_keys = [k for k, v in rhyme_sets.items() if len(v) > 1] or list(rhyme_sets)
    if not rhyme_keys:
        raise core.InvalidInput("not enough words to build rhyme sets")
    blocks = []
    for _ in range(count):
        lines = []
        pattern = scheme if scheme != "free" else ""
        for idx in range(line_count):
            key = rhyme_keys[idx % len(rhyme_keys)] if pattern else rng.choice(rhyme_keys)
            vowel = rng.choice(rhyme_sets[key])
            others = rng.shuffle([w for w in words if w.lower() != vowel.lower()])
            line = " ".join([vowel] + others[: 4 + rng.next_int(4)])
            lines.append(line)
        blocks.append("\n".join(lines))
        if pattern:
            mapping = {letter: [] for letter in set(pattern.replace(" ", ""))}
            for idx, letter in enumerate(pattern.replace(" ", "")):
                mapping[letter].append(idx + 1)
            blocks.append("# rhyme scheme %s -> lines %s" % (
                pattern, "; ".join("%s: %s" % (k, v) for k, v in sorted(mapping.items()))))
    data = {"scheme": scheme, "lines": line_count, "rhyme_sets": {k: v for k, v in list(rhyme_sets.items())[:8]}}
    return {"output": "\n\n".join(blocks), "data": data,
            "warnings": ["line assembly is deterministic scaffolding; rhyme is approximated by word endings"]}


HANDLERS = {
    "professional.meeting-text-assistant": meeting_assistant,
    "professional.business-template-builder": business_templates,
    "professional.learning-material-creator": learning_materials,
    "professional.resume-and-application-text-editor": resume_editor,
    "professional.customer-support-response-maker": support_response,
    "professional.legal-style-plain-language-assistant": legal_plain_language,
    "creative.poetry-and-verse-maker": poetry_maker,
    "creative.dialogue-and-script-writer": dialogue_script,
    "creative.interactive-fiction-writer": interactive_fiction,
    "creative.riddle-and-wordplay-maker": wordplay_maker,
    "creative.naming-and-neologism-studio": naming_studio,
    "creative.lore-and-continuity-editor": lore_continuity,
    "creative.roleplaying-text-generator": roleplaying_text,
    "creative.lyrics-and-chant-designer": lyrics_designer,
}
