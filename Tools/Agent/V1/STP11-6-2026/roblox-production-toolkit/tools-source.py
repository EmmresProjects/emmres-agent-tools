"""Bounded importer for plain text, Markdown and copied HTML capability lists (stdlib only).

Extracts category headings, "Skill Name" entries and "Description" text, ignoring markup,
numbering, bullets, CSS classes and spacing. Loaded as rbx_source by tools-roblox.py.
"""
from html.parser import HTMLParser
import re
import unicodedata

BLOCKS = {"p", "div", "li", "ul", "ol", "br", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
          "section", "article", "table", "pre", "blockquote", "dt", "dd", "option"}
IGNORE = {"script", "style", "noscript", "template", "svg", "head", "button", "input", "select", "textarea"}
NUMBER = re.compile(r"^\s*(?:\d{1,3}(?:\.\d{1,3})*[.)]\s+|\d{1,3}\s+(?=\S)|[-*+\u2022]\s+)")
SKILL = re.compile(r"^skill\s*(?:tool\s*)?name\s*[:\-\u2013\u2014]\s*(.+)$", re.I)
DESC = re.compile(r"^description\s*[:\-\u2013\u2014]\s*(.*)$", re.I)
CATEGORY = re.compile(r"^category\s*[:\-\u2013\u2014]\s*(.+)$", re.I)
MAX_LINES = 20000


def slug(value, fallback="roblox-capability"):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")[:90].rstrip("-") or fallback


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.ignored = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in IGNORE:
            self.ignored += 1
        if tag in BLOCKS and not self.ignored:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in IGNORE:
            self.ignored = max(0, self.ignored - 1)
        if tag in BLOCKS and not self.ignored:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def plain_lines(source):
    """Return cleaned logical lines. Fenced code and horizontal rules are ignored."""
    if re.search(r"<\s*[a-zA-Z][^>]*>", source):
        parser = TextExtractor()
        parser.feed(source)
        parser.close()
        source = "".join(parser.parts)
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    lines, fence = [], False
    for raw in text.split("\n")[:MAX_LINES]:
        line = raw.strip()
        if line.startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence or not line or re.fullmatch(r"[-=_*]{3,}", line):
            continue
        for part in re.split(r"<\s*br\s*/?\s*>", line, flags=re.I):
            part = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", part)
            part = re.sub(r"^\s{0,3}#{1,6}\s*", "", part)
            part = re.sub(r"^\s*>\s?", "", part)
            part = NUMBER.sub("", part)
            part = re.sub(r"[*_`]", "", part)
            part = re.sub(r"\s+", " ", part).strip()
            if part:
                lines.append(part)
    return lines


def plain_text(source):
    return "\n".join(plain_lines(source))


def _norm(value):
    return re.sub(r"\s+", " ", value).strip(" :\uff1a").casefold()


def parse_source(source, known_categories=(), max_items=500, max_description=8000):
    """Return {"categories": [...], "capability_count": n, "warnings": [...]}.

    known_categories: category names from the catalog; a line matching one is always a heading.
    """
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Source must contain at least one capability name, description or category.")
    lines = plain_lines(source)
    known = {_norm(name): name for name in known_categories}
    categories, warnings = [], []
    state = {"cat": None, "cap": None, "in_desc": False}

    def start_category(name, is_known):
        name = name.strip()
        if not name or len(name) > 160:
            raise ValueError("Category names must contain 1-160 characters.")
        canonical = known.get(_norm(name), name)
        found = next((c for c in categories if c["slug"] == slug(canonical)), None)
        if found is None:
            if len(categories) >= 100:
                raise ValueError("At most 100 source categories are supported.")
            found = {"name": canonical, "slug": slug(canonical), "known": is_known or _norm(name) in known, "capabilities": []}
            categories.append(found)
        state.update(cat=found, cap=None, in_desc=False)

    def start_capability(name):
        if state["cat"] is None:
            start_category("roblox-capabilities", False)
        if not name or len(name) > 160:
            raise ValueError("Skill names must contain 1-160 characters.")
        if sum(len(c["capabilities"]) for c in categories) >= max_items:
            raise ValueError(f"Source exceeds {max_items} capabilities.")
        cap = {"name": name, "slug": slug(name), "description": ""}
        state["cat"]["capabilities"].append(cap)
        state.update(cap=cap, in_desc=False)

    def is_known(line):
        return _norm(CATEGORY.sub(r"\1", line)) in known

    for index, line in enumerate(lines):
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        cat_match = CATEGORY.match(line)
        skill_match, desc_match = SKILL.match(line), DESC.match(line)
        if cat_match:
            start_category(cat_match.group(1), True)
        elif is_known(line):
            start_category(line, True)
        elif skill_match:
            start_capability(skill_match.group(1).strip())
        elif desc_match:
            cap = state["cap"]
            if cap is None or cap["description"]:
                raise ValueError("A Description appeared without a preceding Skill Name.")
            cap["description"] = desc_match.group(1).strip()
            state["in_desc"] = True
        elif DESC.match(nxt):
            start_capability(line)
        elif SKILL.match(nxt):
            start_category(line, False)
        elif state["in_desc"] and state["cap"] is not None:
            cap = state["cap"]
            cap["description"] = (cap["description"] + " " + line).strip()
        elif len(line) <= 100 and not re.search(r"[.!?;]$", line):
            start_category(line, False)
        else:
            warnings.append(f"Ignored unlabelled text: {line[:60]}")

    total = 0
    for category in categories:
        for cap in category["capabilities"]:
            cap["description"] = re.sub(r"\s+", " ", cap["description"]).strip()
            if not cap["description"]:
                warnings.append(f"No description found for {cap['name']}.")
            if len(cap["description"]) > max_description:
                raise ValueError(f"Description for {cap['name']} exceeds {max_description} characters.")
            total += 1
    categories = [c for c in categories if c["capabilities"] or c["known"]]
    if not categories:
        raise ValueError("No usable category headings, Skill Names or descriptions were found.")
    return {"schema_version": 1, "categories": categories, "capability_count": total, "warnings": warnings}


def select_capabilities(parsed, catalog, category_of):
    """Resolve parsed items to catalog capability ids.

    catalog: asset-catalog.json dict. category_of: {capability_id: category_id}.
    Category-only headings select every capability in that catalog category.
    """
    cats = {_norm(c["name"]): c for c in catalog["categories"]}
    caps = catalog["capabilities"]
    by_name = {_norm(c[1]): c for c in caps}
    by_slug = {c[0]: c for c in caps}
    selected, resolved, unresolved = [], [], []
    for category in parsed["categories"]:
        cat_entry = cats.get(_norm(category["name"]))
        if not category["capabilities"] and cat_entry is not None:
            members = [c for c in caps if category_of.get(c[0]) == cat_entry["id"]]
            for member in members:
                selected.append(member[0])
                resolved.append({"id": member[0], "name": member[1], "category": cat_entry["name"], "match": "category"})
            continue
        for cap in category["capabilities"]:
            match = by_name.get(_norm(cap["name"])) or by_slug.get(cap["slug"])
            if match is None:
                unresolved.append({"name": cap["name"], "category": category["name"], "description": cap["description"]})
                continue
            selected.append(match[0])
            resolved.append({"id": match[0], "name": match[1], "category": category["name"],
                             "match": "name", "source_description": cap["description"]})
    ids = list(dict.fromkeys(selected))
    names = [c["slug"] for c in parsed["categories"]]
    umbrella = names[0] if len(names) == 1 else "roblox-production-toolkit"
    return {"umbrella": umbrella, "capabilities": ids, "resolved": resolved, "unresolved": unresolved}
