#!/usr/bin/env python3
"""Input ingestion for the video-production-orchestrator Skill Tool.

Module role: turn messy human input (plain text, Markdown, copied HTML, numbered
capability lists, whole categories) into a clean normalised structure:

    {
      "source_format": "markdown" | "html" | "text",
      "categories": [{"name": str, "skills": [{"name": str, "description": str, "id": str|None}]}],
      "skills": [{"name","description","id","category"}],
      "warnings": [str]
    }

Design rules implemented here:
  * HTML tags, CSS classes, data-* attributes, list-marker markup and repeated
    spacing never reach the extracted names or descriptions.
  * "Skill Name:" / "Description:" labels are optional; bullet or numbered list
    lines are accepted.
  * A category heading ("Category 4: Media Ingest ...", an <h2>, a Markdown
    heading, or "Media Ingest, Organization, and Preparation (10 skills)") becomes
    the umbrella name for the skills that follow it.
  * When no category name is supplied, one deterministic lowercase-hyphenated
    name is derived from the shared purpose of the collected capabilities.

Public functions: detect_format, clean_markup, parse_source_text,
normalise_request, derive_umbrella_name, normalise_name.
"""

from __future__ import annotations

import html
import re

MAX_ITEMS = 500
MAX_NAME_CHARS = 120
MAX_DESC_CHARS = 1200

TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.S | re.I)
BLOCK_TAG_RE = re.compile(
    r"</?(?:br|p|div|li|ul|ol|h[1-6]|tr|table|section|article|header|footer|blockquote)\b[^>]*>",
    re.I,
)
INLINE_TAG_RE = re.compile(r"</?(?:span|em|strong|b|i|a|code|small|mark|u|sup|sub|font|td|th)\b[^>]*>", re.I)
INLINE_MARK = "\x00"
LIST_MARK = re.compile(r"^\s*(?:[-*+\u2022\u25cf\u25aa>]+\s+|\d{1,3}\s*[.)]\s*|\(\d{1,3}\)\s*)")
NAME_LABEL = re.compile(
    r"^\s*(?:skill\s*name|capability|name|tool)\s*[:\-\u2013]\s*", re.I)
DESC_LABEL = re.compile(r"^\s*(?:description|desc|purpose|summary)\s*[:\-\u2013]\s*", re.I)
CATEGORY_LINE = re.compile(
    r"^\s*(?:category|capability group|group)\s*#?\s*(\d{1,3})?\s*[:\-\u2013]?\s*(.+?)\s*$", re.I)
CATEGORY_HEADING = re.compile(r"^\s*(\d{1,3})\.\s+(.{3,120}?)\s*\(\s*\d+\s+skills?\s*\)\s*$", re.I)
SKILLS_COUNT = re.compile(r"\s*[\(\[]\s*\d+\s+(?:skills?|capabilities)\s*[\)\]]\s*", re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "that", "this", "when", "then", "than",
    "their", "there", "these", "those", "each", "every", "using", "used", "use", "user",
    "video", "videos", "skill", "skills", "capability", "capabilities", "creating", "create",
    "creates", "produce", "produces", "production", "across", "while", "without", "within",
    "after", "before", "over", "under", "such", "also", "more", "most", "must", "should",
    "will", "can", "may", "helps", "build", "builds", "make", "makes",
    # units, durations and generic adjectives carry no naming value
    "second", "seconds", "minute", "minutes", "hour", "hours", "vertical",
    "horizontal", "square", "short", "long", "new", "final", "full", "quick",
    "simple", "first", "please", "need", "want", "clean", "make",
}


def detect_format(raw: str) -> str:
    """Guess 'html', 'markdown' or 'text' from the raw input."""
    sample = raw[:4000]
    if re.search(r"<(?:html|body|div|ul|ol|li|p|table|span|h[1-6])\b", sample, re.I):
        if len(TAG_RE.findall(sample)) >= 2:
            return "html"
    if re.search(r"^\s{0,3}#{1,6}\s+\S", sample, re.M) or "```" in sample or re.search(r"\|\s*-{2,}", sample):
        return "markdown"
    return "text"


def clean_markup(raw: str) -> str:
    """Strip HTML/scripts/markdown decoration and collapse whitespace.

    Returns plain text where block elements become line breaks.
    """
    text = SCRIPT_RE.sub(" ", raw)
    text = re.sub(r"<li\b[^>]*>", "\n- ", text, flags=re.I)   # keep list semantics
    text = BLOCK_TAG_RE.sub("\n", text)
    text = INLINE_TAG_RE.sub(INLINE_MARK, text)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    # Markdown emphasis / code decoration, but keep words intact.
    text = re.sub(r"(\*\*|__|\*|_|`)", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)          # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)        # links -> text
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)   # markdown headings
    text = re.sub(r"^\s*\|", "", text, flags=re.M)
    text = re.sub(r"\|", " | ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [_join_inline_segments(line) for line in text.splitlines()]
    return "\n".join(line for line in (ln.strip() for ln in lines) if line)


def _join_inline_segments(line: str) -> str:
    """Rebuild a line whose inline elements were replaced by INLINE_MARK.

    ``<span>Dialogue Editor</span> <em>Selects, cleans audio.</em>`` becomes
    ``Dialogue Editor \u2013 Selects, cleans audio.`` so the name/description pair
    survives markup flattening. Otherwise segments are joined with one space.
    """
    if INLINE_MARK not in line:
        return line
    prefix = ""
    mark = LIST_MARK.match(line)
    if mark:                       # keep "- " / "1. " outside the segment merge
        prefix = mark.group(0).strip() + " "
        line = line[mark.end():]
    segments = [seg.strip() for seg in line.split(INLINE_MARK)]
    segments = [seg for seg in segments if seg]
    if not segments:
        return ""
    if len(segments) == 1:
        return prefix + segments[0]
    head = segments[0]
    tail = " ".join(segments[1:]).strip()
    head_is_name = (
        len(head) <= 80
        and len(head.split()) <= 12
        and not head.endswith((".", "!", "?", ":"))
        and len(tail) >= 8
        and not tail.startswith((",", ";", "."))
    )
    return prefix + (f"{head} \u2013 {tail}" if head_is_name else " ".join(segments))


def normalise_name(name: str) -> str:
    name = re.sub(r"\s+", " ", str(name or "")).strip(" \t-\u2013:;.,|")
    name = SKILLS_COUNT.sub("", name)
    return name[:MAX_NAME_CHARS]


def _clean_description(desc: str) -> str:
    desc = re.sub(r"\s+", " ", str(desc or "")).strip(" \t:;,-")
    return desc[:MAX_DESC_CHARS]


def parse_source_text(raw: str) -> dict:
    """Parse arbitrary capability text into categories + skills."""
    if not isinstance(raw, str):
        raise TypeError("raw input must be a string")
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    fmt = detect_format(text)
    cleaned = clean_markup(text)
    lines = [ln for ln in cleaned.split("\n") if ln.strip()]

    categories: list[dict] = []
    warnings: list[str] = []
    current: dict | None = None
    pending_skill: dict | None = None
    in_index = False

    def ensure_category(name=None):
        nonlocal current
        if current is None:
            current = {"name": normalise_name(name) if name else None, "skills": []}
            categories.append(current)
        return current

    for index, line in enumerate(lines):
        stripped = line.strip()
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""

        # Skip an explicit "Category Index" / table-of-contents block.
        if re.match(r"^(category index|table of contents|contents|index)\b", stripped, re.I):
            in_index = True
            continue
        idx_match = CATEGORY_HEADING.match(stripped)
        if idx_match:
            # "1. Planning (10 skills)" style entries belong to the index.
            if in_index or not current or not current["skills"]:
                in_index = True
                continue
            name = normalise_name(idx_match.group(2))
            categories.append({"name": name, "skills": []})
            current = categories[-1]
            continue
        if in_index and not stripped.startswith(("Category", "category")) and not LIST_MARK.match(stripped):
            in_index = False

        cat_direct = re.match(r"^\s*category\s*#?\s*(\d{1,3})?\s*[:\-\u2013]\s*(.+)$", stripped, re.I)
        if cat_direct:
            name = normalise_name(cat_direct.group(2))
            if name and not CATEGORY_HEADING.match(stripped):
                categories.append({"name": name, "skills": []})
                current = categories[-1]
                in_index = False
                continue

        # "Category Name (10 skills)" heading without the word Category.
        bare_heading = re.match(r"^(.{3,120}?)\s*[\(\[]\s*(\d{1,3})\s+(?:skills?|capabilities)\s*[\)\]]\s*$", stripped, re.I)
        if bare_heading and not LIST_MARK.match(stripped):
            name = normalise_name(bare_heading.group(1))
            categories.append({"name": name, "skills": []})
            current = categories[-1]
            continue

        has_mark = bool(LIST_MARK.match(stripped))
        body = LIST_MARK.sub("", stripped).strip()

        if DESC_LABEL.match(body) and pending_skill is not None:
            pending_skill["description"] = _clean_description(DESC_LABEL.sub("", body))
            pending_skill = None
            continue

        if NAME_LABEL.match(body):
            payload = NAME_LABEL.sub("", body)
            inline_desc = re.split(r"\s*\b(?:description|desc|purpose|summary)\s*[:\-\u2013]\s*", payload,
                                   maxsplit=1, flags=re.I)
            name = normalise_name(inline_desc[0])
            if len(inline_desc) > 1:
                cat = ensure_category()
                cat["skills"].append({"name": name,
                                      "description": _clean_description(inline_desc[1]),
                                      "source_id": None})
                pending_skill = None
                continue
            if not name:
                continue
            cat = ensure_category()
            pending_skill = {"name": name, "description": "", "source_id": None}
            cat["skills"].append(pending_skill)
            continue

        has_explicit_description = bool(re.search(r"\bdescription\s*[:\-\u2013]", body, re.I))
        if pending_skill is not None and pending_skill["description"] == "" and not has_mark and not has_explicit_description:
            # Continuation line of the previous description.
            pending_skill["description"] = _clean_description(pending_skill["description"] + " " + body)
            continue

        if has_explicit_description:
            name_part, desc_part = re.split(r"\bdescription\s*[:\-\u2013]", body, maxsplit=1, flags=re.I)
            name = normalise_name(LIST_MARK.sub("", NAME_LABEL.sub("", name_part)))
            if name:
                cat = ensure_category()
                cat["skills"].append({"name": name, "description": _clean_description(desc_part), "source_id": None})
                pending_skill = None
                continue

        if has_mark:
            # Bullet with "Name - description", "Name: description", bare name, or
            # a continuation sentence belonging to the previous bare-name bullet.
            if (pending_skill is not None and pending_skill["description"] == ""
                    and _is_continuation(body, pending_skill["name"])
                    and not NAME_LABEL.match(body)):
                pending_skill["description"] = _clean_description(body)
                pending_skill = None
                continue
            name, desc = _split_inline(body)
            if not name:
                continue
            cat = ensure_category()
            entry = {"name": name, "description": desc, "source_id": None}
            cat["skills"].append(entry)
            pending_skill = entry if desc == "" else None
            continue

        # Unmarked table/paragraph pair: short title line followed by a sentence.
        if (pending_skill is None and not has_mark
                and 1 <= len(body.split()) <= 8 and body[:1].isupper()
                and not body.endswith((":", ".", "!", "?"))
                and current is not None and current["skills"]
                and _is_continuation(next_line, body)):
            cat = ensure_category()
            entry = {"name": normalise_name(body), "description": "", "source_id": None}
            cat["skills"].append(entry)
            pending_skill = entry
            continue

        # Paragraph that is not a marked item: treat as category heading when it
        # is short and title-like, otherwise as a loose capability sentence.
        if len(body) <= 90 and not body.endswith((".", "!", "?")) and _looks_like_heading(body):
            categories.append({"name": normalise_name(body), "skills": []})
            current = categories[-1]
            pending_skill = None
            continue

        # Unmarked "Name - description" line (common when HTML is pasted).
        name, desc = _split_inline(body)
        if desc and len(name.split()) <= 8 and name[:1].isupper():
            cat = ensure_category()
            cat["skills"].append({"name": name, "description": desc, "source_id": None})
            pending_skill = None
            continue

        if _looks_like_capability(body):
            name, desc = _split_inline(body)
            cat = ensure_category()
            cat["skills"].append({"name": name, "description": desc, "source_id": None})

    # ---- post-processing -------------------------------------------------
    cleaned_categories = []
    for cat in categories:
        skills, seen = [], set()
        for sk in cat["skills"]:
            name = normalise_name(sk["name"])
            if not name or len(name) < 2:
                continue
            desc = _clean_description(sk.get("description") or "")
            key = name.lower()
            if key in seen:
                warnings.append(f"duplicate capability name dropped: {name}")
                continue
            seen.add(key)
            skills.append({**sk, "name": name, "description": desc})
        if skills:
            cleaned_categories.append({"name": cat["name"], "skills": skills})
        elif cat["name"]:
            warnings.append(f"category '{cat['name']}' contained no parseable capabilities")

    total = sum(len(c["skills"]) for c in cleaned_categories)
    if total > MAX_ITEMS:
        raise ValueError(f"input contains {total} capabilities; the limit is {MAX_ITEMS}")

    flat = []
    for cat in cleaned_categories:
        for sk in cat["skills"]:
            flat.append({**sk, "category": cat["name"]})

    if not flat:
        warnings.append("no capabilities detected; check that the input contains names or list items")
    if len(cleaned_categories) > 1 and any(c["name"] is None for c in cleaned_categories):
        warnings.append("some capabilities were collected before any category heading")

    return {
        "source_format": fmt,
        "categories": cleaned_categories,
        "skills": flat,
        "warnings": warnings,
    }


def _looks_like_heading(body: str) -> bool:
    words = body.split()
    if not 1 <= len(words) <= 14:
        return False
    caps = sum(1 for w in words if w[:1].isupper())
    return caps >= max(1, len(words) - 2) and not re.search(r"\d+\.\s", body)


def _looks_like_capability(body: str) -> bool:
    return len(body.split()) >= 3 and re.search(r"\b(create|creates|build|builds|design|designs|write|writes|edit|edits|manage|manages|review|reviews|detect|detects|generate|generates|convert|converts|plan|plans|prepare|prepares|produce|produces)\b", body, re.I) is not None


INLINE_SPLIT = re.compile(r"\s*(?:::?\s+|\u2013|\u2014|\s-\s)")


def _split_inline(body: str):
    """Split 'Name - description', 'Name: description', 'Name \u2013 description'."""
    body = re.sub(r"\s+", " ", body).strip()
    match = INLINE_SPLIT.search(body)
    if match and match.start() >= 2 and len(body) - match.end() >= 8:
        name = body[: match.start()]
        if not re.search(r"\d{1,2}[:.]\d{2}$", name):   # never split on '12:30'
            return normalise_name(name), _clean_description(body[match.end():].lstrip(" -:\u2013\u2014"))
    return normalise_name(body), ""


def _is_continuation(body: str, previous_name: str = "") -> bool:
    """True when a bare list line is really the description of the item above.

    Deliberately narrow: a short name (<= 4 words) followed by a longer sentence
    that starts lower-case or ends with sentence punctuation.
    """
    body = body.strip()
    words = body.split()
    if len(words) < 5:
        return False
    if len(previous_name.split()) > 4:
        return False
    if len(words) <= len(previous_name.split()):
        return False
    return body[:1].islower() or body.endswith((".", "!", "?"))


def derive_umbrella_name(skills_or_categories) -> str:
    """Create a short lowercase-hyphenated umbrella name from shared purpose."""
    if isinstance(skills_or_categories, dict):
        skills = skills_or_categories.get("skills", [])
        categories = skills_or_categories.get("categories", [])
    else:
        skills = list(skills_or_categories or [])
        categories = []
    named = [c.get("name") for c in categories if c.get("name")]
    if named:
        first = re.sub(r"\s*\(.*?\)\s*", "", named[0])
        slug = re.sub(r"[^a-z0-9]+", "-", first.lower().replace("&", " and ")).strip("-")
        return re.sub(r"-{2,}", "-", slug)[:60] or "capability-tool"

    words: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for index, sk in enumerate(skills):
        blob = f"{sk.get('name','')} {sk.get('description','')}".lower()
        for position, token in enumerate(re.findall(r"[a-z][a-z\-]{3,}", blob)):
            token = token.strip("-")
            if token in STOPWORDS or len(token) < 4:
                continue
            words[token] = words.get(token, 0) + 1
            first_seen.setdefault(token, index * 1000 + position)
    # Most frequent first; ties keep the order the words appeared in the source.
    ranked = sorted(words.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))[:2]
    if not ranked:
        # Fallback: the first meaningful words of the first capability name.
        for sk in skills:
            candidate = [w for w in re.findall(r"[a-z]{3,}", str(sk.get("name", "")).lower())
                         if w not in STOPWORDS]
            if candidate:
                return re.sub(r"-{2,}", "-", "-".join(candidate[:2]))[:60]
        return "capability-tool"
    slug = "-".join(w for w, _ in ranked)
    return re.sub(r"-{2,}", "-", slug)[:60]


def normalise_request(request: str) -> dict:
    """Full pipeline: raw request -> parsed, categorised, umbrella-named plan input."""
    if request is None or not str(request).strip():
        raise ValueError("request text is empty")
    parsed = parse_source_text(str(request))
    names = [c["name"] for c in parsed["categories"] if c["name"]]
    if names:
        umbrella = names[0] if len(names) == 1 else derive_umbrella_name(
            {"categories": [{"name": names[0]}], "skills": parsed["skills"]})
    else:
        umbrella = derive_umbrella_name(parsed)
    umbrella = re.sub(r"[^a-z0-9]+", "-", umbrella.lower()).strip("-") or "capability-tool"
    if umbrella == "capability-tool":
        # Free-text request with no list: name it from its own first meaningful words.
        words = [w for w in re.findall(r"[a-z]{3,}", str(request).lower())
                 if w not in STOPWORDS and w not in {"clean", "make", "need", "want", "please", "help"}]
        if words:
            umbrella = "-".join(words[:2])[:60]
    parsed["umbrella_name"] = umbrella
    parsed["skill_count"] = len(parsed["skills"])
    parsed["category_count"] = len(parsed["categories"])
    return parsed


def split_values(raw_values, limit=MAX_ITEMS):
    """Split comma/pipe/newline separated CLI values into a clean list."""
    out: list[str] = []
    for value in raw_values or []:
        for part in re.split(r"[,\n|;]+", str(value)):
            part = part.strip()
            if part:
                out.append(part)
    if len(out) > limit:
        raise ValueError(f"too many values ({len(out)}); limit is {limit}")
    return out
