"""Capability intake + catalog registry adapter.

Handles plain text, Markdown and copied HTML capability sources: strips tags,
CSS classes, data attributes, numbering marks and extra spacing, then extracts
real category names, Skill Tool names and descriptions into ONE unified
capability spec. Also loads/validates/queries the bundled 24-category,
242-skill registry extracted from the source blueprint.

Standard library only. Imported by tools-work-splitter.py and tools-selftest.py.
"""

from __future__ import annotations

import html
import importlib.util
import re
import sys
from collections import Counter
from pathlib import Path

# Keep the Skill Tool folder free of generated cache subfolders at runtime.
sys.dont_write_bytecode = True


def _load_sibling(module_name, filename):
    """Load a sibling tools-*.py file whose hyphenated name is not importable."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


engine = _load_sibling("tools_workflow_engine", "tools-workflow-engine.py")

# Input normalisation -------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_ATTR_RE = re.compile(r"\b(?:class|style|data-[\w-]+|id|role|aria-[\w-]+)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_CSS_RULE_RE = re.compile(r"^\s*[.#@][\w-].*\{|^\s*[{].*[}]|^\s*[}]\s*$|^\s*@media|^\s*[\w-]+\s*:\s*[^;]+;\s*$")
_HEADING_OPEN_RE = re.compile(r"<h([1-6])\b[^>]*>", re.I)
_HEADING_CLOSE_RE = re.compile(r"</h[1-6]>", re.I)
_BLOCK_RE = re.compile(r"</?(?:p|li|div|section|tr|br|ul|ol|table|td|th|article|header|footer)\b[^>]*>", re.I)
_NUMBER_RE = re.compile(r"^\s*(?:(?:\d+\.)*\d+[\.\)]|\(\d+\)|[•*\-–—▪]|[a-zA-Z][\.\)])\s+")
_SKILLS_HINT_RE = re.compile(r"\|\s*\d+\s+skills?\s*$", re.I)
_CATEGORY_PREFIX_RE = re.compile(r"^(?:category|section|group)\s*(?:\d+\s*)?[:.\-–]\s*", re.I)

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "before", "but", "by", "can", "checks",
    "each", "every", "for", "from", "how", "if", "in", "into", "is", "it", "its", "of",
    "on", "or", "remain", "returns", "that", "the", "their", "this", "to", "use", "uses",
    "using", "when", "where", "which", "while", "with", "without", "not", "are", "all",
    "check", "where", "whether", "them", "they", "has", "have", "does", "do", "then",
    "than", "so", "such", "also", "any", "only", "during", "across", "after", "before",
    "new", "one", "two", "well", "made", "make", "makes", "user", "users", "content",
}


def strip_markup(text):
    """Remove HTML tags, CSS, class/data attributes and HTML comments."""
    if not isinstance(text, str):
        raise engine.ToolError("ERR_INTAKE_TYPE", "source must be text", "input")
    t = _COMMENT_RE.sub("\n", text)
    t = _SCRIPT_RE.sub("\n", t)
    t = _HEADING_OPEN_RE.sub(lambda m: "\n" + "#" * int(m.group(1)) + " ", t)
    t = _HEADING_CLOSE_RE.sub("\n", t)
    t = _BLOCK_RE.sub("\n", t)
    t = _TAG_RE.sub(" ", t)
    t = html.unescape(t)
    t = _ATTR_RE.sub(" ", t)
    t = t.replace("\u00a0", " ").replace("\uf0b7", " ").replace("\u2022", " ")
    return t


def normalize_lines(text, source_format="auto"):
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _CSS_RULE_RE.search(line):
            continue
        if re.fullmatch(r"[.#][\w-]+.*", line) and "{" in line:
            continue
        level = 0
        if line.startswith("#"):
            m = re.match(r"^(#+)\s*", line)
            level = len(m.group(1))
            line = line[m.end():].strip()
        numbered = False
        for _ in range(2):
            m = _NUMBER_RE.match(line)
            if not m:
                break
            numbered = True
            line = line[m.end():].strip()
        line = re.sub(r"\s{2,}", " ", line).strip()
        line = _SKILLS_HINT_RE.sub("", line).strip()
        if not line:
            continue
        out.append({"text": line, "numbered": numbered, "markdown_level": level,
                    "is_heading": _looks_like_heading(line)})
    return out


def _looks_like_heading(text):
    if len(text) > 78 or text.endswith((".", ";", ":")) or "," in text and len(text) > 60:
        return False
    if len(text.split()) > 9:
        return False
    if not text or not text[0].isupper():
        return False
    if re.search(r"\b(it|the|this|returns|checks|creates|builds|defines|identifies|ensures)\b", text, re.I):
        return False
    return True


def _shared_purpose_slug(names, descriptions):
    words = Counter()
    for text in list(names) + list(descriptions)[:40]:
        for w in re.findall(r"[a-z]{4,}", text.lower()):
            if w not in STOPWORDS:
                words[w] += 1
    top = [w for w, _ in sorted(words.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    return engine.slugify("-".join(top))[:48] if top else "unified-capabilities"


def parse_source(text, registry=None, category_filter=None, name_override=None, source_format="auto"):
    """Parse a capability source into ONE unified Skill Tool specification."""
    cleaned = strip_markup(text)
    # Single-line "Name: description" or bare-goal sources are still one capability.
    nonempty = [l.strip() for l in cleaned.splitlines() if l.strip()]
    if len(nonempty) == 1:
        one = nonempty[0]
        if ":" in one:
            head, _, tail = one.partition(":")
            cleaned = head.strip() + "\n" + tail.strip()
        else:
            cleaned = "Inline Capability Request\n" + one
    records = normalize_lines(cleaned, source_format)
    if not records:
        raise engine.ToolError("ERR_INTAKE_EMPTY", "no usable capability text after cleaning markup and numbering", "input")

    if source_format == "auto":
        lowered = cleaned.lower()
        if "<div" in lowered or "<span" in lowered or "<li" in lowered or "<h2" in lowered:
            source_format = "html"
        elif re.search(r"^#{1,6}\s", cleaned, re.M) or re.search(r"^\s*[-*]\s+\*\*", cleaned, re.M):
            source_format = "markdown"
        else:
            source_format = "text"

    known = {}
    if registry:
        for c in registry.get("categories", []):
            known[c["name"].lower()] = c["name"]

    categories, warnings = [], []
    orphan = {"name": None, "skills": []}
    current_cat = None
    current_skill = None
    has_orphan = False

    def flush():
        nonlocal current_skill, has_orphan
        if current_skill is not None:
            desc = " ".join(current_skill["_desc"])
            desc = re.sub(r"\s{2,}", " ", re.sub(r"(\w)-\s+(\w)", r"\1-\2", desc)).strip()
            current_skill["description"] = desc
            del current_skill["_desc"]
            if len(desc) < 15:
                warnings.append("capability '%s' has a very short description" % current_skill["name"])
            if current_cat is None:
                orphan["skills"].append(current_skill)
                has_orphan = True
            else:
                current_cat["skills"].append(current_skill)
            current_skill = None

    def category_name_for(rec):
        raw = rec["text"]
        low = raw.lower().strip()
        hint = _CATEGORY_PREFIX_RE.match(raw)
        if hint:
            return raw[hint.end():].strip().rstrip(":")
        if low in known:
            return known[low]
        if rec["markdown_level"] in (1, 2) and rec["is_heading"]:
            return re.sub(r"^category\s*\d*\s*[:.\-]?\s*", "", raw, flags=re.I).strip().rstrip(":")
        if _CATEGORY_PREFIX_RE.match(raw):
            return _CATEGORY_PREFIX_RE.sub("", raw).strip()
        return None

    for rec in records:
        cname = category_name_for(rec)
        if cname:
            flush()
            current_cat = {"name": cname, "skills": []}
            categories.append(current_cat)
            continue
        if rec["is_heading"] and (rec["numbered"] or rec["markdown_level"] >= 3):
            flush()
            current_skill = {"name": re.sub(r"\*\*|__|`", "", rec["text"]).strip(), "_desc": []}
            continue
        if current_skill is not None:
            current_skill["_desc"].append(rec["text"])
        elif rec["is_heading"] and not rec["numbered"]:
            # Plain-text single capability: "Name" on one line, description on the next.
            current_skill = {"name": rec["text"], "_desc": []}
        else:
            warnings.append("ignored unattached line: %.60s" % rec["text"])
    flush()
    if has_orphan and orphan["skills"]:
        categories.insert(0, orphan)

    # Normalise category containers (un-numbered list case uses a null-category bucket).
    norm_categories = []
    for c in categories:
        if not isinstance(c, dict) or "skills" not in c:
            continue
        norm_categories.append({"name": c.get("name"), "slug": engine.slugify(c["name"]) if c.get("name") else None,
                                "skill_count": len(c["skills"]), "skills": c["skills"]})

    if category_filter:
        wanted = category_filter.lower().strip()
        selected = [c for c in norm_categories if c["name"] and (wanted == c["name"].lower() or wanted == (c["slug"] or ""))]
        if not selected:
            raise engine.ToolError("ERR_CATEGORY_NOT_FOUND", "category %r not found in source" % category_filter, "category",
                                   {"available": [c["name"] for c in norm_categories if c["name"]]})
        norm_categories = selected

    capabilities = []
    for ci, c in enumerate(norm_categories):
        for si, s in enumerate(c["skills"]):
            capabilities.append({
                "id": "C%03d" % (len(capabilities) + 1),
                "name": s["name"], "description": s.get("description", ""),
                "slug": engine.slugify(s["name"]),
                "category": c["name"] or "uncategorized",
                "source_number": si + 1,
            })
    if not capabilities:
        raise engine.ToolError("ERR_INTAKE_NO_CAPABILITIES", "no capability names and descriptions could be extracted", "input")

    named = [c for c in norm_categories if c["name"]]
    if name_override:
        slug = engine.slugify(name_override)
        umbrella_name = name_override
    elif len(named) == 1:
        umbrella_name = named[0]["name"]
        slug = engine.slugify(umbrella_name)
    elif len(named) > 1:
        # Several related categories: use the source blueprint title when it is
        # available, otherwise derive a short name from the shared purpose.
        source_title = (registry or {}).get("source", "").split("|")[0].strip()
        umbrella_name = source_title or ("Unified capabilities (%d categories)" % len(named))
        slug = engine.slugify(source_title) if source_title else _shared_purpose_slug(
            [c["name"] for c in capabilities], [c["description"] for c in capabilities])
    elif len(capabilities) == 1:
        umbrella_name = capabilities[0]["name"]
        slug = engine.slugify(umbrella_name)
    else:
        umbrella_name = "Unified capabilities from shared purpose"
        slug = _shared_purpose_slug([c["name"] for c in capabilities], [c["description"] for c in capabilities])
    return {
        "source_format": source_format,
        "umbrella": {"name": umbrella_name, "slug": slug, "category_count": len(named)},
        "categories": norm_categories,
        "capabilities": capabilities,
        "unified": {
            "folder_name": slug,
            "capability_count": len(capabilities),
            "capability_ids": [c["id"] for c in capabilities],
            "decision": "one unified Skill Tool folder containing all related listed capabilities",
        },
        "counts": {"categories": len(norm_categories), "named_categories": len(named), "capabilities": len(capabilities)},
        "warnings": warnings[:25],
    }


# Registry ------------------------------------------------------------------
def load_registry(config, budget):
    ref = config["catalog"]["registry_path"]
    path = engine.resolve_reference(ref)
    data = engine.read_json(path, budget, config["limits"]["max_input_bytes"], "catalog registry")
    validate_registry(data, config)
    return data


def validate_registry(data, config=None):
    if not isinstance(data, dict) or not isinstance(data.get("categories"), list):
        raise engine.ToolError("ERR_REGISTRY", "catalog registry must contain a categories array", "catalog")
    total = sum(len(c.get("skills", [])) for c in data["categories"])
    if data.get("skill_count") != total or data.get("category_count") != len(data["categories"]):
        raise engine.ToolError("ERR_REGISTRY", "catalog registry counts do not match contents", "catalog")
    seen = set()
    for c in data["categories"]:
        for s in c["skills"]:
            if not s.get("name") or not s.get("description"):
                raise engine.ToolError("ERR_REGISTRY", "catalog entry missing name/description", "catalog")
            key = (c["name"], s["name"])
            if key in seen:
                raise engine.ToolError("ERR_REGISTRY", "duplicate catalog entry %s" % (key,), "catalog")
            seen.add(key)
    return {"categories": len(data["categories"]), "skills": total, "valid": True}


def registry_categories(registry):
    return [{"number": c["number"], "name": c["name"], "slug": c["slug"], "skill_count": c["skill_count"]}
            for c in registry["categories"]]


def find_category(registry, name):
    key = str(name).strip().lower()
    for c in registry["categories"]:
        if key in (c["name"].lower(), str(c["number"]), c["slug"], c["slug"].replace("-and-", "-")):
            return c
    raise engine.ToolError("ERR_CATEGORY_NOT_FOUND", "category %r not found in registry" % name, "category",
                           {"available": [c["name"] for c in registry["categories"]]})


def search_registry(registry, query, limit=20):
    q = str(query).lower().strip()
    hits = []
    for c in registry["categories"]:
        for s in c["skills"]:
            hay = (s["name"] + " " + s["description"]).lower()
            if q in hay:
                hits.append({"category": c["name"], "number": s["number"], "name": s["name"],
                             "id": s["id"], "description": s["description"]})
    return {"query": query, "matches": len(hits), "results": hits[:limit], "truncated": len(hits) > limit}


def _stream_for(name):
    n = name.lower()
    for key, stream in (("research", "research"), ("search", "research"), ("evidence", "research"),
                        ("writ", "drafting"), ("edit", "drafting"), ("document", "drafting"),
                        ("code", "coding"), ("api", "coding"), ("web", "coding"),
                        ("image", "media"), ("video", "media"), ("audio", "media"), ("design", "media"),
                        ("test", "validation"), ("verif", "validation"), ("audit", "validation"),
                        ("check", "validation"), ("assembl", "validation")):
        if key in n:
            return stream
    return "general"


def project_from_spec(spec, category=None):
    caps = [c for c in spec["capabilities"] if not category or c["category"].lower() == category.lower()]
    if not caps:
        raise engine.ToolError("ERR_INTAKE_NO_CAPABILITIES", "no capabilities selected for project", "input")
    requirements = [{"id": c["id"], "text": c["name"] + ": " + c["description"], "critical": False} for c in caps]
    packages = []
    prev = []
    for i, c in enumerate(caps):
        packages.append({
            "id": "WP%03d" % (i + 1), "name": c["name"],
            "depends_on": [], "duration": 1.0, "stream": _stream_for(c["name"]),
            "requirement_ids": [c["id"]], "inputs": [], "outputs": [c["slug"] + "-output"],
            "pass_conditions": ["%s behavior verified" % c["name"]],
        })
    return {
        "title": spec["umbrella"]["name"],
        "goal": "Deliver one unified Skill Tool for %d related capabilities" % len(caps),
        "requirements": requirements,
        "files": [],
        "work_packages": packages,
        "tool_calls": max(1, len(caps) // 2),
        "uncertainty": 0.2,
        "verification_burden": min(50, len(caps)),
        "context_chars": sum(len(c["description"]) for c in caps),
        "parallel_streams": len({p["stream"] for p in packages}),
        "source_format": spec["source_format"],
    }


def capability_brief(registry, category_name, config, budget, stages=None):
    cat = find_category(registry, category_name)
    project = engine.validate_project(project_from_spec({
        "umbrella": {"name": cat["name"], "slug": cat["slug"]},
        "capabilities": [{"id": "C%03d" % (i + 1), "name": s["name"], "description": s["description"],
                          "slug": s["id"], "category": cat["name"], "source_number": s["number"]}
                         for i, s in enumerate(cat["skills"])],
        "source_format": "json",
    }), config)
    units = engine.decompose(project, config)
    score = engine.score_request(project, units, config)
    estimate = engine.estimate_counts(project, units, config)
    y = int(stages or estimate["recommended"])
    stage_list = engine.build_stages(units, y, config)
    return {
        "category": {"number": cat["number"], "name": cat["name"], "slug": cat["slug"]},
        "capabilities": [{"number": s["number"], "name": s["name"], "id": s["id"], "description": s["description"],
                          "returns": _extract_clause(s["description"], "It returns"),
                          "checks": _extract_clause(s["description"], "It checks")} for s in cat["skills"]],
        "unified_tool": {"name": engine.slugify(cat["name"]), "capability_count": len(cat["skills"])},
        "assessment": {"one_turn_feasible": score["one_turn"], "weighted_score": score["weighted_score"]},
        "stage_plan": {"recommended": estimate["recommended"], "selected": len(stage_list),
                       "stages": [{"label": s["label"], "unit_ids": s["unit_ids"],
                                   "requirement_ids": s["requirement_ids"], "is_final": s["is_final"]} for s in stage_list]},
        "dependency_graph": engine.dependency_graph(units, engine.topological_order(units)),
        "critical_path": engine.critical_path(units, config),
        "parallel_streams": engine.parallel_streams(units, config),
        "tradeoffs": engine.tradeoffs(stage_list, estimate["recommended"], config),
    }


def _extract_clause(description, marker):
    idx = description.find(marker)
    if idx < 0:
        return ""
    tail = description[idx + len(marker):].strip()
    stop = tail.find(". It ")
    return (tail[:stop] if stop >= 0 else tail).strip().rstrip(".")
