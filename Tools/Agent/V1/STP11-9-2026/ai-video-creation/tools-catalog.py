"""ai-video-creation / tools-catalog.py

Catalog ingestion and registry services.

Handles a single capability, a numbered Skill Tool list, one category, or
several categories arriving as plain text, Markdown, or copied HTML. Markup,
CSS classes, data attributes, numbering marks, and excess whitespace are
discarded; the real category names, Skill Tool Names, and Descriptions are
extracted and combined into ONE unified registry.

Also provides registry validation, lookup, search, and capability
classification shared by the planner and runtime.

Pure Python standard library; loaded by the primary entry point.
"""

from __future__ import annotations

import difflib
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

SCHEMA_VERSION = "1.0"
DEFAULT_GATE_IDS = [
    "temporal-coherence", "prompt-source-fidelity", "technical-validity",
    "accessibility", "rights-consent", "reproducibility", "handoff-readiness",
]


def slugify(value):
    s = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)


# --------------------------------------------------------------------------
# Markup stripping
# --------------------------------------------------------------------------

_BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "tr", "td", "th", "table", "h1", "h2", "h3",
               "h4", "h5", "h6", "br", "hr", "section", "article", "blockquote", "pre", "dt", "dd"}
_SKIP_TAGS = {"script", "style", "head", "noscript", "svg"}
_VOID_TAGS = {"meta", "link", "br", "hr", "img", "input", "source", "area", "base", "col", "embed", "param", "track", "wbr"}


class _TextExtractor(HTMLParser):
    """Discards tags and ALL attributes (class=, data-*=, id=, style=...)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            # Container tags such as <head> always have an end tag; void tags
            # such as <meta> must NOT increase the skip depth.
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS or tag in _VOID_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK_TAGS or tag in _VOID_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)

    def handle_entityref(self, name):
        if not self._skip_depth:
            self.parts.append(html.unescape("&%s;" % name))

    def handle_charref(self, name):
        if not self._skip_depth:
            self.parts.append(html.unescape("&#%s;" % name))


def strip_markup(raw):
    """Return clean text from HTML fragments/documents, Markdown, or plain text."""
    if not isinstance(raw, str):
        raise ValueError("Catalog source must be text.")
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    # Only run the HTML parser when markup is actually present.
    if re.search(r"<\s*[a-zA-Z!/]", text):
        p = _TextExtractor()
        try:
            p.feed(text)
            p.close()
            text = "".join(p.parts)
        except Exception:
            text = re.sub(r"<[^>]*>", "\n", text)
        text = html.unescape(text)
    # Markdown emphasis / code fences / headings / list markers are visual noise.
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s{0,3}```.*$", "", text, flags=re.M)
    text = re.sub(r"\*\*|__|`|\*(?=\s)|(?<=\s)\*", "", text)
    return text


_NUMBERING = re.compile(r"^\s*(?:\d+|[ivxlcdm]+|[A-Za-z])\s*[.)\]:-]\s+", re.I)
_BULLETS = re.compile(r"^\s*[•●▪‣◦*\-–—]\s+")


def clean_line(line):
    line = line.replace("\u00a0", " ").replace("\t", " ")
    line = _NUMBERING.sub("", line)
    line = _BULLETS.sub("", line)
    return re.sub(r"\s+", " ", line).strip()


def normalize_paragraph(text):
    return re.sub(r"\s+", " ", str(text)).strip()


_CAT_PATTERNS = [
    re.compile(r"^Category\s+(\d+)\s*[:.\-–—]\s*(.+)$", re.I),
    re.compile(r"^Category\s*[:.\-–—]\s*(.+)$", re.I),
    re.compile(r"^Category\s+(\d+)\s+(.+)$", re.I),
]
_PURPOSE = re.compile(r"^Category purpose\s*[:.\-–—]\s*(.*)$", re.I)
_UMBRELLA_HINT = re.compile(r"^(?:category\s*[:.\-]?\s*)?(?:umbrella|tool name|skill tool|title|package)\s*[:.\-–—]\s*(.+)$", re.I)
_SKILL_LABEL = re.compile(
    r"^(?:Skill Tool Name|Skill Name|Tool Name|Capability Name|Capability|Name|Tool)\s*[:.\-–—]\s*(.+)$", re.I)
_DESC_LABEL = re.compile(r"^Description\s*[:.\-–—]\s*(.*)$", re.I)
_MD_INLINE = re.compile(r"^(.+?)\s*[—–]\s+(.{12,})$")
_STOP_WORDS = {"skill", "skills", "tool", "tools", "catalog", "catalogue", "category",
               "categories", "list", "capability", "capabilities", "creation", "a", "the",
               "and", "of", "for", "to", "comprehensive", "map", "blueprint"}


def derive_umbrella(lines, fallback=None):
    """Short lowercase-hyphenated umbrella name from the source title/purpose."""
    for line in lines[:12]:
        m = _UMBRELLA_HINT.match(line)
        if m:
            cand = slugify(m.group(1))
            if cand:
                return cand
    for line in lines[:12]:
        if not line or _is_boundary(line):
            continue
        words = [w for w in slugify(line).split("-") if w and w not in _STOP_WORDS and len(w) > 1]
        if words:
            return "-".join(words[:3])
    if fallback:
        cand = slugify(Path(str(fallback)).stem)
        if cand:
            return "-".join([w for w in cand.split("-") if w not in _STOP_WORDS][:3]) or "unified-capabilities"
    return "unified-capabilities"


# Default operation family per category of the source catalog. Skill-name
# keywords below refine it. This keeps foundation capabilities such as
# "Prompt and Vision Embedding Encoder" in the generation family instead of
# misfiling them under delivery because they contain the word "encoder".
CATEGORY_DEFAULTS = {
    1: ("generation", "contract"), 2: ("prompting", "compile-prompt"), 3: ("generation", "contract"),
    4: ("generation", "contract"), 5: ("generation", "contract"), 6: ("generation", "contract"),
    7: ("generation", "contract"), 8: ("generation", "contract"), 9: ("generation", "contract"),
    10: ("timeline", "shotlist"), 11: ("timeline", "shotlist"), 12: ("timeline", "shotlist"),
    13: ("generation", "contract"), 14: ("generation", "contract"), 15: ("generation", "contract"),
    16: ("generation", "contract"), 17: ("captioning", "captions"), 18: ("captioning", "captions"),
    19: ("timeline", "shotlist"), 20: ("generation", "contract"), 21: ("generation", "contract"),
    22: ("generation", "contract"), 23: ("generation", "contract"), 24: ("timeline", "shotlist"),
    25: ("timeline", "shotlist"), 26: ("generation", "contract"), 27: ("generation", "contract"),
    28: ("generation", "contract"), 29: ("generation", "contract"), 30: ("timeline", "shotlist"),
    31: ("generation", "contract"), 32: ("generation", "contract"), 33: ("generation", "contract"),
    34: ("generation", "contract"), 35: ("assurance", "gates"), 36: ("generation", "contract"),
    37: ("generation", "contract"), 38: ("delivery", "delivery"), 39: ("assurance", "gates"),
    40: ("safety", "approval"), 41: ("planning", "plan"), 42: ("generation", "contract"),
    43: ("planning", "plan"), 44: ("planning", "plan"), 45: ("generation", "contract"),
    46: ("generation", "contract"), 47: ("planning", "plan"),
}


def classify_capability(name, category="", description="", category_id=None):
    """Assign a real local operation family. Only implemented operations are used."""
    n = (name or "").lower()
    c = (category or "").lower()
    if re.search(r"caption|subtitle|transcript|sdh|closed[- ]caption|accessib|localiz|translat", n):
        return "captioning", "captions"
    if re.search(r"publish|upload|distribut|release tracker|consent|rights|licen|privacy|provenance|watermark|safety|bias|deepfake|disclosure|approval|policy|guardrail|moderation|sensitive", n):
        return "safety", "approval"
    if re.search(r"validat|compliance|verification|schema|test case|benchmark|specification|availability|conform", n):
        return "assurance", "validate"
    if re.search(r"quality|evaluat|audit|inspect|detect|metric|score|diagnos|review|checker|comparison|measur", n):
        return "assurance", "gates"
    if re.search(r"delivery|export|compress|packag|stream|bitrate|codec|transcod|playback|cdn|format conversion|\bencoding\b|\bencoder\b", n) and not re.search(r"embedding", n):
        return "delivery", "delivery"
    if re.search(r"shot list|storyboard|screenplay|script|scene|timeline|sequence|edit decision|story development|beat[- ]sync|choreograph|transition|montage", n):
        return "timeline", "shotlist"
    if re.search(r"planner|plan|schedul|router|orchestrat|dependency|pipeline|workflow|queue|coordinator|registry|catalog|governance|version|fallback|telemetry|cost estimat|naming", n):
        return "planning", "plan"
    if (re.search(r"prompt|brief|style vocabulary|negative|ambiguity|creative intent|instruction", n)
            and not re.search(r"embedding|autoencoder|tokeniz|denois|sampler|latent", n)):
        return "prompting", "compile-prompt"
    if category_id in CATEGORY_DEFAULTS:
        return CATEGORY_DEFAULTS[category_id]
    if re.search(r"caption|subtitle|accessib|localiz|translat", c):
        return "captioning", "captions"
    if re.search(r"safety|rights|consent|provenance", c):
        return "safety", "approval"
    if re.search(r"quality|evaluation|assurance", c):
        return "assurance", "gates"
    if re.search(r"encoding|delivery|compression", c):
        return "delivery", "delivery"
    if re.search(r"story|shot|preproduction|timeline|edit|postproduction|scene|screenwriting", c):
        return "timeline", "shotlist"
    if re.search(r"prompt|brief|creative", c):
        return "prompting", "compile-prompt"
    if re.search(r"automation|pipeline|governance|orchestration|management|metadata|compute|mlops|api", c):
        return "planning", "plan"
    return "general", "contract"


_SENSITIVE = re.compile(
    r"likeness|identity|voice clon|voice|face|clone|deepfake|publish|rights|consent|release|"
    r"medical|clinical|political|news|factual|biometric|minor|child|training|fine[- ]tun|"
    r"dataset|sensitive|privacy|security|credential|financial|watch", re.I)


def _is_boundary(line):
    return (bool(_PURPOSE.match(line)) or bool(_SKILL_LABEL.match(line)) or
            bool(_DESC_LABEL.match(line)) or any(p.match(line) for p in _CAT_PATTERNS))


def _category_header(line):
    for idx, pat in enumerate(_CAT_PATTERNS):
        m = pat.match(line)
        if m:
            if idx == 0:
                return int(m.group(1)), m.group(2).strip()
            if idx == 1:
                return None, m.group(1).strip()
            return int(m.group(1)), m.group(2).strip()
    return False


def parse_catalog(raw, umbrella=None, source_name="input"):
    """Parse a capability source into one unified registry.

    Supported shapes (mixed freely):
      Category 3: Text-to-Video Generation
      Category purpose: ...
      1. Skill Name: Cinematic Text-to-Video Generator
      Description: ... (may wrap across lines)
      ## Category: Captioning, Subtitles, and Accessibility
      1. **Subtitle Timing Aligner** — Description ...
      <div class="row" data-id="7"><b>Skill Name:</b> ...</div>
      A single 'Skill Name: X / Description: Y' pair with no category.
    """
    text = strip_markup(raw)
    lines = [clean_line(l) for l in text.splitlines()]
    # PDF and copied tables often wrap a category heading over two lines
    # ("... and Latent" / "Video"). Join short continuation lines that appear
    # before "Category purpose:" or the first skill entry.
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line and _category_header(line) is not False:
            parts = [line]
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if (not nxt or _PURPOSE.match(nxt) or _SKILL_LABEL.match(nxt) or
                        _DESC_LABEL.match(nxt) or _category_header(nxt) is not False or
                        len(nxt) > 60 or nxt.endswith((".", ":", ";"))):
                    break
                parts.append(nxt)
                j += 1
            merged.append(normalize_paragraph(" ".join(parts)))
            i = j
            continue
        merged.append(line)
        i += 1
    lines = merged
    warnings = []
    categories, caps, pending_desc = [], [], []
    current_cat = None
    current_skill = None
    purpose = ""
    cat_counter = 0
    skill_counter = 0

    def flush_skill():
        nonlocal current_skill, pending_desc
        if current_skill is None:
            pending_desc = []
            return
        desc = normalize_paragraph(" ".join(pending_desc))
        if not desc:
            desc = "Reusable agent capability for %s. It accepts a brief plus relevant assets and constraints, " \
                   "performs %s tasks, and returns editable outputs with assumptions, dependencies, and " \
                   "unresolved issues clearly marked." % (
                       current_cat["purpose"] or current_cat["name"] if current_cat else "the shared purpose",
                       slugify(current_skill["name"]).replace("-", " "))
            warnings.append("Filled a missing description for '%s' from the category purpose." % current_skill["name"])
        if len(desc) > 4000:
            desc = desc[:4000].rstrip() + " ..."
            warnings.append("Truncated an over-long description for '%s'." % current_skill["name"])
        current_skill["description"] = desc
        family, op = classify_capability(current_skill["name"], current_skill["category"], desc,
                                         current_skill.get("category_id"))
        current_skill["family"] = family
        current_skill["local_operation"] = op
        current_skill["safety_class"] = "high-impact" if _SENSITIVE.search(current_skill["name"]) else "standard"
        current_skill["requires_approval"] = current_skill["safety_class"] == "high-impact"
        current_skill["quality_gates"] = list(DEFAULT_GATE_IDS)
        caps.append(current_skill)
        current_skill = None
        pending_desc = []

    def start_category(name, explicit_id=None):
        nonlocal current_cat, cat_counter, skill_counter, purpose
        flush_skill()
        cat_counter += 1
        skill_counter = 0
        cid = explicit_id if explicit_id else cat_counter
        purpose = ""
        current_cat = {"id": cid, "name": normalize_paragraph(name), "slug": slugify(name),
                       "purpose": "", "skill_count": 0}
        categories.append(current_cat)

    def start_skill(name):
        nonlocal current_skill, skill_counter, pending_desc
        flush_skill()
        skill_counter += 1
        if current_cat is None:
            start_category("Unified capabilities")
        current_skill = {
            "id": "c%02d-s%02d" % (current_cat["id"], skill_counter),
            "number": skill_counter,
            "name": normalize_paragraph(name),
            "slug": slugify(name),
            "category_id": current_cat["id"],
            "category": current_cat["name"],
            "category_purpose": current_cat["purpose"],
        }
        pending_desc = []
        current_cat["skill_count"] += 1

    nonempty = [l for l in lines if l]
    labeled = any(_is_boundary(l) or _category_header(l) is not False for l in nonempty)
    # A running header is only treated as a title when it looks like one, so a
    # bare "Skill Name: ..." first line is never mistaken for a document title.
    document_title = ""
    for l in nonempty[:5]:
        if _is_boundary(l) or _category_header(l) is not False:
            continue
        if re.search(r"\b(catalog|skills?|tools?|capabilit|list|blueprint|index)\b", l, re.I) or l.isupper():
            document_title = l
        break
    for line in lines:
        if not line:
            continue
        # Ignore PDF/page chrome and repeated running headers in copied sources.
        if (re.fullmatch(r"Page\s+\d+", line, re.I) or
                (document_title and line == document_title) or
                re.match(r"^Prepared for\b", line, re.I)):
            continue
        # Appendix sections are guidance, not catalog entries.
        if re.match(r"^(Recommended Skill Contract|Suggested Skill Families for First Implementation|Scope Note)\b",
                    line, re.I):
            break
        m = _UMBRELLA_HINT.match(line)
        if m and not umbrella:
            umbrella = slugify(m.group(1))
            continue
        hdr = _category_header(line)
        if hdr is not False:
            start_category(hdr[1], hdr[0])
            continue
        pm = _PURPOSE.match(line)
        if pm:
            flush_skill()
            purpose = normalize_paragraph(pm.group(1))
            if current_cat:
                current_cat["purpose"] = purpose
            continue
        sm = _SKILL_LABEL.match(line)
        if sm:
            start_skill(sm.group(1))
            continue
        dm = _DESC_LABEL.match(line)
        if dm and current_skill is not None:
            pending_desc.append(dm.group(1))
            continue
        md = _MD_INLINE.match(line) if labeled else None
        if md and not _DESC_LABEL.match(line):
            # Markdown form: "1. **Name** — Description" (numbering already stripped).
            start_skill(md.group(1))
            pending_desc.append(md.group(2))
            continue
        if current_skill is not None:
            # continuation of a wrapped description
            pending_desc.append(line)
            continue
        if not labeled and current_cat is None and len(line) > 3 and len(line) < 120:
            # Unlabeled plain-text list: treat short lines as capability names.
            start_skill(line)
            continue
        if current_cat is not None and purpose and len(line) > 10 and not current_skill:
            # Loose prose before the first labeled skill: treat as category purpose extension.
            current_cat["purpose"] = normalize_paragraph(current_cat["purpose"] + " " + line)[:600]
    flush_skill()

    # Single-capability shorthand: "Name: X" with no Description -> handled above.
    if not caps:
        meaningful = [l for l in lines if l and not _is_boundary(l)]
        name = meaningful[0] if meaningful else "Unified capability"
        desc = normalize_paragraph(" ".join(meaningful[1:])) or (
            "Reusable agent capability. It accepts a brief and returns editable, structured outputs.")
        start_skill(name)
        pending_desc.append(desc)
        flush_skill()
        warnings.append("Source contained no labeled skill entries; created one capability from the text.")

    # Keep category purpose consistent on capabilities (purpose may be set after first skill).
    by_id = {c["id"]: c for c in categories}
    for cap in caps:
        cat = by_id.get(cap["category_id"])
        if cat:
            cap["category_purpose"] = cat["purpose"]
            cat["skill_count"] = sum(1 for x in caps if x["category_id"] == cat["id"])

    umbrella = slugify(umbrella or derive_umbrella(lines, source_name if source_name != "inline" else None)) or "unified-capabilities"
    if umbrella == "unified-capabilities" and len(caps) == 1:
        umbrella = "-".join(caps[0]["slug"].split("-")[:3]) or umbrella
    registry = {
        "schema_version": SCHEMA_VERSION,
        "umbrella": umbrella,
        "source": {"title": (lines[0] if lines else source_name)[:160], "kind": "ingested capability source",
                   "category_count": len(categories), "capability_count": len(caps), "origin": source_name},
        "quality_gate_ids": list(DEFAULT_GATE_IDS),
        "categories": categories,
        "capabilities": caps,
    }
    issues = validate_registry(registry)
    return registry, {"warnings": warnings, "issues": issues,
                      "stats": {"categories": len(categories), "capabilities": len(caps),
                                "families": _family_counts(caps)}}


def _family_counts(caps):
    out = {}
    for c in caps:
        out[c["family"]] = out.get(c["family"], 0) + 1
    return dict(sorted(out.items()))


def validate_registry(reg):
    """Structural integrity checks. Returns a list of issue strings (empty == valid)."""
    issues = []
    if not isinstance(reg, dict):
        return ["registry is not a JSON object"]
    for key in ("schema_version", "umbrella", "source", "categories", "capabilities"):
        if key not in reg:
            issues.append("missing key: %s" % key)
    if issues:
        return issues
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", reg["umbrella"]):
        issues.append("umbrella name is not lowercase-hyphenated")
    cats = reg.get("categories", [])
    caps = reg.get("capabilities", [])
    if not isinstance(cats, list) or not isinstance(caps, list):
        return ["categories/capabilities must be arrays"]
    if not caps:
        issues.append("registry has no capabilities")
    seen_ids, seen_slugs = set(), set()
    cat_ids = {c.get("id") for c in cats}
    for i, cap in enumerate(caps):
        for key in ("id", "name", "slug", "description", "category", "local_operation", "family"):
            if not cap.get(key):
                issues.append("capabilities[%d] missing %s" % (i, key))
        if cap.get("id") in seen_ids:
            issues.append("duplicate capability id: %s" % cap.get("id"))
        seen_ids.add(cap.get("id"))
        if cap.get("slug") in seen_slugs:
            issues.append("duplicate capability slug: %s" % cap.get("slug"))
        seen_slugs.add(cap.get("slug"))
        if cap.get("category_id") not in cat_ids:
            issues.append("capability %s references an unknown category" % cap.get("id"))
        if not isinstance(cap.get("quality_gates"), list):
            issues.append("capability %s has no quality gates" % cap.get("id"))
    for c in cats:
        if c.get("skill_count") != sum(1 for x in caps if x.get("category_id") == c.get("id")):
            issues.append("category %s skill_count is inconsistent" % c.get("id"))
    return issues


def load_registry(ref, root=None, loader=None):
    """Load a registry JSON from a path or via an injected loader, then validate it.

    loader(ref, root) -> parsed JSON; when omitted, ref is treated as a real file path.
    """
    if loader is not None:
        reg = loader(ref, root)
    else:
        p = Path(ref).expanduser()
        if not p.is_file():
            raise FileNotFoundError("Registry file not found: %s" % p)
        reg = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(reg, dict):
        raise ValueError("Registry must be a JSON object")
    issues = validate_registry(reg)
    if issues:
        raise ValueError("Invalid registry: %s" % "; ".join(issues[:8]))
    return reg


def find_capability(reg, query):
    """Exact id/slug/name, then substring, then close-match. Returns (cap, suggestions)."""
    caps = reg.get("capabilities", [])
    q = str(query or "").strip()
    ql = q.lower()
    for c in caps:
        if ql in (c["id"].lower(), c["slug"].lower(), c["name"].lower()):
            return c, []
    subs = [c for c in caps if ql and (ql in c["name"].lower() or ql in c["slug"].lower())]
    if len(subs) == 1:
        return subs[0], []
    suggestions = [c["name"] for c in subs[:8]]
    if not suggestions:
        close = difflib.get_close_matches(ql, [c["slug"] for c in caps], n=5, cutoff=0.55)
        suggestions = [next(c["name"] for c in caps if c["slug"] == s) for s in close]
    return None, suggestions


def search_capabilities(reg, query="", category=None, family=None, limit=20, offset=0):
    caps = reg.get("capabilities", [])
    q = (query or "").strip().lower()
    cat = (category or "").strip().lower()
    fam = (family or "").strip().lower()
    out = []
    for c in caps:
        if q and q not in ("%s %s %s %s" % (c["name"], c["slug"], c["category"], c["description"])).lower():
            continue
        if cat and cat not in c["category"].lower() and cat != str(c["category_id"]):
            continue
        if fam and fam != c["family"].lower():
            continue
        out.append(c)
    total = len(out)
    return out[offset:offset + limit], total


def registry_summary(reg):
    cats = reg.get("categories", [])
    caps = reg.get("capabilities", [])
    return {
        "umbrella": reg.get("umbrella"),
        "categories": len(cats),
        "capabilities": len(caps),
        "families": _family_counts(caps),
        "operations": dict(sorted((op, sum(1 for c in caps if c["local_operation"] == op))
                                  for op in {c["local_operation"] for c in caps})),
        "approval_required": sum(1 for c in caps if c.get("requires_approval")),
        "category_names": [c["name"] for c in cats],
    }


def registry_to_json(reg):
    return json.dumps(reg, indent=2, ensure_ascii=False)
