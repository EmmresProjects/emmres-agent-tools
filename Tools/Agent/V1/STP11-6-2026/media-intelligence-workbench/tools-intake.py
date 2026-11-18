#!/usr/bin/env python3
"""Request intake for the Media Intelligence Workbench.

Turns pasted plain text, Markdown, copied HTML or a numbered skill list into a
normalised capability request:

    {"umbrella": "...", "categories": [{"name": ..., "skills": [
        {"name": ..., "description": ...}]}], "skills": [...]}

Housekeeping done here:
* strips HTML tags, CSS/JS blocks, comments, data-* attributes and entities
* removes list numbering, bullets and extra whitespace
* recognises "Skill Name: ... / Description: ..." pairs, "Name — description",
  Markdown "**Name** — description" and Markdown/HTML table rows
* detects category headings so related entries collapse under one umbrella

Usable directly:
    python3 tools-intake.py --in request.txt
    python3 tools-intake.py --text "1. Skill Name: X Description: Y"
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys

MAX_TEXT_CHARS_DEFAULT = 4_000_000

_SCRIPT_RE = re.compile(r"<(script|style|noscript)\b.*?</\1\s*>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9-]*)((?:\s+[^<>]*?)?)/?>", re.S)
_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table", "tbody",
    "thead", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
    "footer", "hr", "blockquote", "pre", "dd", "dt", "dl", "figure", "figcaption",
}
_SKIP_TEXT_TAGS = {"style", "script", "noscript", "svg", "head", "meta", "link"}

_NUMBER_RE = re.compile(r"^[\s>*-]*\(?\d{1,3}[.)\]:-]?\s+")
_BULLET_RE = re.compile(r"^[\s>*-]*[•*+\u2013\u2014-]\s+")
_SKILLNAME_RE = re.compile(r"^\s*(?:skill\s*name|name|skill|capability|tool)\s*[:\-]\s*(.+)$", re.I)
_DESC_RE = re.compile(r"^\s*(?:description|desc|summary|purpose|about)\s*[:\-]\s*(.+)$", re.I)
_CATEGORY_RE = re.compile(
    r"^\s*(?:category\s*[:\-]?\s*)?\(?\d{1,2}[.)]?\s+(.+)$", re.I)
_HEADING_MD_RE = re.compile(r"^#{1,6}\s+(.+)$")
_COUNT_LINE_RE = re.compile(
    r"^(?:\d{1,3}\s+)?(?:(?:proposed|the|all|total)\s+)*(?:skills?|entries|capabilities)\s*[:.]?$",
    re.I)
_DASH_SPLIT_RE = re.compile(r"\s+[—–\-:]\s+|\s*\|\s*")
_INLINE_RE = re.compile(
    r"^(.*?)\s*(?:\u2014|\u2013|\||\s-\s|\s:\s)\s*"
    r"(?:description|desc|summary)?\s*[:\-]?\s*(.+)$", re.I)


class IntakeError(Exception):
    code = "intake-error"


def strip_html(text):
    """Convert copied HTML to newline-separated plain text."""
    text = _SCRIPT_RE.sub(" ", text)
    text = _COMMENT_RE.sub(" ", text)

    def repl(match):
        tag = match.group(1).lower()
        if tag in _SKIP_TEXT_TAGS:
            return " "
        return "\n" if tag in _BLOCK_TAGS else " "

    text = _TAG_RE.sub(repl, text)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    return text


def _clean(line):
    line = line.replace("\t", " ")
    line = re.sub(r"\s{2,}", " ", line)
    return line.strip()


def _strip_marks(line):
    prev = None
    cur = line.strip()
    while prev != cur:
        prev = cur
        cur = _NUMBER_RE.sub("", cur)
        cur = _BULLET_RE.sub("", cur)
        cur = cur.strip().lstrip("#").strip()
    return cur.strip().strip("*_`").strip()


def normalise(text, max_chars=MAX_TEXT_CHARS_DEFAULT):
    """Strip markup, numbering and layout noise; return clean lines."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if len(text) > max_chars:
        raise IntakeError("request text exceeds %d characters" % max_chars)
    if "<" in text and ">" in text and _TAG_RE.search(text):
        text = strip_html(text)
    lines = []
    for raw in text.splitlines():
        line = _clean(raw)
        if not line:
            continue
        lines.append(line)
    return lines


def _looks_like_category(line):
    if len(line) > 90:
        return False
    if _SKILLNAME_RE.match(line) or _DESC_RE.match(line):
        return False
    stripped = _strip_marks(line)
    if not stripped or len(stripped.split()) > 12:
        return False
    if stripped.endswith(".") and len(stripped.split()) > 6:
        return False
    return True


def parse_request(text, max_chars=MAX_TEXT_CHARS_DEFAULT):
    """Parse a request into categories, skills and an umbrella name."""
    lines = normalise(text, max_chars)
    categories = []
    skills = []
    current_category = None
    pending_name = None
    last_skill = None
    prev_line = ""

    def ensure_category(name):
        nonlocal current_category
        for c in categories:
            if c["name"].lower() == name.lower():
                current_category = c
                return c
        current_category = {"name": name, "skills": []}
        categories.append(current_category)
        return current_category

    nonlocal_last = [None]

    def add_skill(name, description):
        name = _strip_marks(name)
        description = _clean(description or "")
        if not name:
            return
        skill = {"name": name, "description": description}
        target = current_category
        if target is None:
            target = ensure_category(None)
        target["skills"].append(skill)
        skills.append(skill)
        nonlocal_last[0] = skill

    for raw in lines:
        line = raw
        last_skill = nonlocal_last[0]
        # "10 proposed skills" style count lines carry no capability information
        if _COUNT_LINE_RE.match(_strip_marks(line)) or _COUNT_LINE_RE.match(line):
            continue

        # wrapped continuation of the previous description (common in PDF/HTML copy)
        cleaned_cont = _strip_marks(line)
        if (last_skill is not None and cleaned_cont
                and not _NUMBER_RE.match(line) and not _BULLET_RE.match(line)
                and not _SKILLNAME_RE.match(cleaned_cont)
                and not _DESC_RE.match(cleaned_cont)
                and not _HEADING_MD_RE.match(line)
                and prev_line
                and not prev_line.rstrip().endswith((".", "!", "?", ":", ";"))):
            last_skill["description"] = (last_skill["description"].rstrip() + " "
                                         + cleaned_cont).strip()
            prev_line = line
            continue
        prev_line = line
        # markdown heading => category
        mh = _HEADING_MD_RE.match(line)
        if mh and _looks_like_category(_strip_marks(mh.group(1))):
            ensure_category(_strip_marks(mh.group(1)))
            pending_name = None
            continue

        m_name = _SKILLNAME_RE.match(_strip_marks(line)) or _SKILLNAME_RE.match(line)
        if m_name:
            captured = _clean(m_name.group(1))
            inline = _INLINE_RE.match(captured)
            if inline:
                add_skill(inline.group(1), inline.group(2))
                pending_name = None
            else:
                pending_name = captured
            continue
        m_desc = _DESC_RE.match(_strip_marks(line)) or _DESC_RE.match(line)
        if m_desc:
            if pending_name:
                add_skill(pending_name, m_desc.group(1))
                pending_name = None
            continue

        cleaned = _strip_marks(line)
        # "Name — description" or "Name | description"
        if cleaned and (_DASH_SPLIT_RE.search(cleaned) or "|" in line):
            parts = [p for p in _DASH_SPLIT_RE.split(cleaned) if p.strip()]
            if len(parts) >= 2:
                name = parts[0].strip()
                desc = " - ".join(p.strip() for p in parts[1:]).strip()
                if name and desc and len(name.split()) <= 10 and len(desc.split()) >= 3:
                    add_skill(name, desc)
                    pending_name = None
                    continue
            # table row with category column: "3 | Category Name | description"
            if "|" in line:
                cells = [c.strip() for c in line.split("|") if c.strip()]
                if len(cells) >= 2:
                    if _looks_like_category(cells[0]) and len(cells) == 2:
                        ensure_category(_strip_marks(cells[0]))
                        continue

        if _looks_like_category(line):
            ensure_category(_strip_marks(line))
            pending_name = None
            continue

        if pending_name and len(cleaned.split()) >= 2:
            add_skill(pending_name, cleaned)
            pending_name = None
            continue

        # fallback: numbered line with no separator still counts as a capability
        if _NUMBER_RE.match(line) and len(cleaned.split()) >= 2:
            words = cleaned.split()
            add_skill(" ".join(words[:4]), " ".join(words[4:]) or cleaned)

    # keep only named categories that actually hold entries
    categories = [c for c in categories if c["name"] and c["skills"]]
    for c in categories:
        known = {id(x) for x in c["skills"]}
        c["skills"] = [x for x in c["skills"] if id(x) in known]

    umbrella = categories[0]["name"] if len(categories) == 1 else None
    if umbrella is None and len(categories) > 1:
        umbrella = "unified %s" % _common_words([c["name"] for c in categories])
    if umbrella is None and skills:
        umbrella = _slugify(skills[0]["name"]) if len(skills) == 1 else "unified capability set"

    return {
        "source_line_count": len(lines),
        "category_count": len(categories),
        "skill_count": len(skills),
        "umbrella": umbrella,
        "slug": _slugify(umbrella or ""),
        "categories": categories,
        "skills": skills,
        "warnings": [] if skills else ["no capability entries were detected in the input"],
    }


def _common_words(names):
    token_lists = [set(re.split(r"[^a-z0-9]+", n.lower())) - {"and", "for", "the", "of", "a"}
                   for n in names]
    if not token_lists:
        return "capability set"
    shared = set.intersection(*token_lists)
    shared = {w for w in shared if w}
    if shared:
        return " ".join(sorted(shared, key=lambda w: (-len(w), w))[:3]) + " workflows"
    return "capability set"


def _slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug


def main(argv=None):
    ap = argparse.ArgumentParser(description="Parse a capability request in plain "
                                             "text, Markdown or copied HTML.")
    ap.add_argument("--in", dest="infile", help="input file (text/md/html)")
    ap.add_argument("--text", help="inline request text")
    ap.add_argument("--out", help="write parsed JSON here (default stdout)")
    args = ap.parse_args(argv)
    if args.infile:
        if not os.path.isfile(args.infile):
            print(json.dumps({"error": "file not found: %s" % args.infile}))
            return 2
        with open(args.infile, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    elif args.text:
        text = args.text
    else:
        ap.print_help()
        return 2
    try:
        parsed = parse_request(text)
    except IntakeError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    blob = json.dumps(parsed, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(blob + "\n")
        print("wrote %s (%d skills, %d categories)"
              % (args.out, parsed["skill_count"], parsed["category_count"]))
    else:
        print(blob)
    return 0 if parsed["skills"] else 1


if __name__ == "__main__":
    sys.exit(main())
