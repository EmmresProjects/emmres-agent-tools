#!/usr/bin/env python3
"""tools-catalog-engine.py -- layer 2 of the offline-game-development Skill Tool.

Role in the Skill Tool
    Everything that knows what a capability record is: catalog loading and validation, the
    six description archetypes and their template round trip, derived fields (tags, owner,
    difficulty, estimate, engine scope, risk class), selection from human tokens, search,
    and the plain text / Markdown / HTML / CSV / JSON ingest that can rebuild a dataset from
    a pasted catalog.

    Data only: no argparse, no report layout, no writes.
"""

from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_KIT_ALIAS = "ogd_tools-runtime-kit"


def _load_kit():
    """Import the sibling runtime kit whether or not the CLI already loaded it."""
    if _KIT_ALIAS in sys.modules:
        return sys.modules[_KIT_ALIAS]
    import importlib.util

    spec = importlib.util.spec_from_file_location(_KIT_ALIAS, os.path.join(_HERE, "tools-runtime-kit.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[_KIT_ALIAS] = module
    spec.loader.exec_module(module)
    return module


kit = _load_kit()

DATASET_KEYS = ("schema_version", "dataset_id", "source_document", "category_count", "entry_count",
                "categories")
ENTRY_KEYS = ("id", "ref", "name", "slug", "category", "category_title", "layer", "archetype",
              "subject", "description", "tags", "role", "difficulty", "est_days", "engines",
              "risk_class", "risk_reasons")
ARCHETYPE_NAMES = ("implement", "define", "evaluate", "operate", "create", "provide")

# Kept in sync with offline-game-development/asset-archetypes.json on purpose: the code must
# still describe a capability correctly if the asset is missing, and 'validate' reports drift.
CATALOG_TEMPLATES = {
    "implement": "Implement {subject} as a focused reusable game-development capability with declared "
                 "inputs and outputs, offline dependencies, rights metadata, performance budgets, "
                 "diagnostics, tests, and target-build acceptance criteria.",
    "define": "Define {subject} with prerequisites, owners, platform and engine constraints, ordered "
              "steps, license gates, quality budgets, fallback paths, required evidence, and a verified "
              "completion condition.",
    "evaluate": "Evaluate {subject} with repeatable automated and manual checks, record the engine, "
                "platform, hardware, scene or build, measured result, pass threshold, failure evidence, "
                "and exact remediation and regression retest.",
    "operate": "Operate {subject} through versioned configuration, bounded CPU, GPU, memory, storage, "
               "and network budgets, safe persistence, structured diagnostics, offline fallback "
               "behavior, and automated gameplay regression tests.",
    "create": "Create {subject} from pinned local inputs with license and integrity checks, deterministic "
              "settings, platform metadata, rollback or cleanup behavior, and a post-build import, "
              "launch, or playback validation.",
    "provide": "Provide {subject} with capability detection, offline mocks, platform-specific "
               "implementations, error recovery, compatibility tests, and documented limitations for "
               "every supported target.",
}
VERB_TO_ARCHETYPE = {verb.capitalize(): verb for verb in ARCHETYPE_NAMES}

LAYERS = (
    ("production-control-plane", 1, 1),
    ("engine-packs", 2, 4),
    ("runtime-skills", 5, 12),
    ("content-skills", 13, 24),
    ("release-skills", 25, 34),
)

# Engine conventions are hints for a human or CI runner; this tool never runs an engine.
ENGINES = {
    "engine-neutral": {
        "label": "Engine-neutral",
        "conventions": "Keep the capability inside the engine's own project layout and record the "
                       "engine-specific step as a to-do row rather than a command.",
        "settings": "Settings live in the project's versioned configuration file; content stays inside "
                    "one versioned content root",
        "check": "open the project in the pinned editor and run the reference case",
    },
    "godot": {
        "label": "Godot 4.x",
        "conventions": "Export templates are installed per editor version; keep the template version "
                       "equal to the editor.",
        "settings": "Settings live in ProjectSettings plus export_presets.cfg; content root "
                    "scenes/, assets/, autoload/",
        "check": "godot --headless --path . --quit",
    },
    "unity": {
        "label": "Unity 6.x LTS",
        "conventions": "Packages are pinned through the manifest and cached in the project's Library "
                       "folder; a version bump is a project-wide change.",
        "settings": "Settings live in ProjectSettings plus Packages/manifest.json; content root "
                    "Assets/, Assets/Resources, Assets/Scenes",
        "check": "Unity -batchmode -quit -projectPath . -executeMethod OfflineCheck.Run",
    },
    "unreal": {
        "label": "Unreal Engine 5.x",
        "conventions": "Cooked data depends on the engine build and on every plugin in the .uplugin "
                       "set; pin the engine to an exact source commit or installer version.",
        "settings": "Settings live in DefaultGame.ini, DefaultEngine.ini, and the .uproject plugin "
                    "list; content root Content/, Config/, Source/",
        "check": "UnrealEditor -run=Commandlet -NullRHI -Unattended",
    },
    "roblox": {
        "label": "Roblox Studio",
        "conventions": "Project state is split between place files and the platform's version history; "
                       "keep a local .rbxlx copy in the bundle for offline review.",
        "settings": "Settings live in Studio Game Settings plus the place file; content root "
                    "ServerScriptService/, ReplicatedStorage/",
        "check": "roblox-studio --mode headless --place local-copy.rbxlx --run-tests",
    },
}
ENGINE_IDS = list(ENGINES)


# ---------------------------------------------------------------------------
# small text helpers shared by the other layers
# ---------------------------------------------------------------------------
def slugify(text: str) -> str:
    out = (text or "").replace("C#", "c-sharp").replace("C++", "c-plus-plus")
    out = out.replace("2D", "2d").replace("3D", "3d")
    out = re.sub(r"[^a-z0-9]+", "-", out.lower()).strip("-")
    return re.sub(r"-{2,}", "-", out)


def arch_map(arch) -> dict:
    """Accept either the whole archetype asset or its 'archetypes' map."""
    if not isinstance(arch, dict):
        return {}
    nested = arch.get("archetypes")
    return nested if isinstance(nested, dict) and nested else arch


def arch_rules(arch) -> list:
    """The inference rules live on the asset, not inside the per-archetype map."""
    if isinstance(arch, dict):
        return list(arch.get("inference_rules") or (arch.get("archetypes") or {}).get("inference_rules")
                    or [])
    return []


def describe(archetype: str, subject: str) -> str:
    """The catalog's own wording for a capability of this archetype."""
    template = CATALOG_TEMPLATES.get(archetype)
    if template is None:
        raise kit.UsageError(f"unknown archetype {archetype!r}",
                             hint=f"archetypes are {'|'.join(ARCHETYPE_NAMES)}", where="--archetype")
    return template.format(subject=(subject or "").strip() or "this capability")


def match_archetype(description: str, arch: dict | None = None) -> tuple:
    """(archetype, subject) read back out of a catalog sentence.

    The description carries its own archetype: the leading verb names it, and the sentence's
    tail is the archetype template. A description written by hand is returned with its leading
    verb only when that verb names a real archetype, otherwise ('', '') so the caller decides.
    """
    text = kit.tidy(description)
    if not text:
        return "", ""
    verb = text.split(" ", 1)[0].strip(".,;:")
    archetype = VERB_TO_ARCHETYPE.get(verb) or VERB_TO_ARCHETYPE.get(verb.capitalize())
    if archetype is None:
        return "", ""
    lookup = arch_map(arch)
    templates = {}
    for name in ARCHETYPE_NAMES:
        spec = lookup.get(name) or {}
        templates[name] = spec.get("catalog_template") or CATALOG_TEMPLATES[name]
    template = templates[archetype]
    prefix, _, suffix = template.partition("{subject}")
    prefix, suffix = prefix.strip(), suffix.strip()
    if text.lower().startswith(prefix.lower()) and text.endswith(suffix):
        subject = re.sub(r"[ ,;:.]+$", "", re.sub(r"^[ ,;:]+", "", text[len(prefix):len(text) - len(suffix)]))
        if subject:
            return archetype, re.sub(r"\s+", " ", subject)
    return archetype, ""


def layer_for(number: int) -> str:
    for name, low, high in LAYERS:
        if low <= int(number) <= high:
            return name
    return "release-skills"


_TOPIC_RULES = [
    ("license", "rights governance"), ("licensing", "rights governance"), ("rights", "rights governance"),
    ("provenance", "provenance"), ("governance", "rights governance"), ("attribution", "rights governance"),
    ("moderation", "community"), ("community", "community"), ("ugc", "community"), ("modding", "community"),
    ("submission", "store submission"), ("store", "store submission"), ("installer", "packaging"),
    ("packaging", "packaging"), ("signing", "signing"), ("certificate", "signing"),
    ("patch", "distribution"), ("distribution", "packaging"),
    ("test", "testing"), ("qa", "testing"), ("regression", "testing"), ("simulation", "gameplay"),
    ("profiling", "performance"), ("performance", "performance"), ("optimization", "performance"),
    ("memory", "performance"), ("thermal", "performance"), ("battery", "performance"),
    ("budget", "performance"), ("load time", "performance"), ("crash", "diagnostics"),
    ("diagnostic", "diagnostics"), ("log", "diagnostics"), ("debug", "diagnostics"),
    ("telemetry", "analytics"), ("analytics", "analytics"), ("build", "build automation"),
    ("ci", "build automation"), ("cache", "build automation"), ("dependency", "build automation"),
    ("save", "persistence"), ("persistence", "persistence"), ("database", "persistence"),
    ("migration", "persistence"), ("network", "netcode"), ("netcode", "netcode"),
    ("replication", "netcode"), ("matchmaking", "online services"), ("backend", "online services"),
    ("auth", "security"), ("security", "security"), ("anti-cheat", "security"), ("privacy", "security"),
    ("shader", "rendering"), ("rendering", "rendering"), ("material", "materials"), ("texture", "materials"),
    ("pbr", "materials"), ("lighting", "lighting"), ("vfx", "vfx"), ("cinematic", "cinematics"),
    ("camera", "camera"), ("mesh", "art-3d"), ("model", "art-3d"), ("photogrammetry", "art-3d"),
    ("rigging", "animation"), ("animation", "animation"), ("skeleton", "animation"),
    ("sprite", "art-2d"), ("ui", "user interface"), ("hud", "user interface"), ("menu", "user interface"),
    ("accessibility", "accessibility"), ("subtitle", "accessibility"), ("colorblind", "accessibility"),
    ("localization", "localization"), ("font", "localization"), ("culture", "localization"),
    ("audio", "audio"), ("music", "audio"), ("sound", "audio"), ("voice", "audio"), ("mix", "audio"),
    ("dialogue", "narrative"), ("narrative", "narrative"), ("quest", "narrative"),
    ("monetization", "monetization"), ("economy", "economy"), ("progression", "economy"),
    ("reward", "economy"), ("level", "level design"), ("terrain", "level design"), ("world", "level design"),
    ("procedural", "procedural content"), ("physics", "physics"), ("collision", "physics"),
    ("navigation", "navigation"), ("pathfinding", "navigation"), ("ai", "gameplay ai"),
    ("behavior", "gameplay ai"), ("input", "input"), ("controller", "input"), ("haptic", "input"),
    ("vr", "xr"), ("xr", "xr"), ("headset", "xr"), ("locomotion", "xr"),
    ("generation", "offline generation"), ("generator", "offline generation"),
    ("sdk", "platform toolchain"), ("compiler", "platform toolchain"), ("export", "platform toolchain"),
    ("engine", "engine environment"), ("editor", "engine environment"), ("version control", "source control"),
    ("git", "source control"), ("snapshot", "lifecycle"), ("release", "lifecycle"),
    ("prototype", "planning"), ("scope", "planning"), ("plan", "planning"), ("roadmap", "planning"),
    ("vision", "planning"), ("budget", "performance"), ("operations", "live operations"),
    ("service", "live operations"), ("live service", "live operations"),
]
_APPROVAL_MARKERS = ("publish", "submission", "signing", "credential", "monetization", "store",
                     "console sdk", "device flash", "payout", "tax")
_HEAVY_MARKERS = ("generator", "bake", "cook", "photogrammetry", "build", "packaging", "installer",
                  "rendering", "simulation", "audio", "texture", "mesh", "import", "export",
                  "compression", "profiling", "benchmark")
_ROLE_BY_TOPIC = [
    ("rights governance", "release-manager"), ("store submission", "release-manager"),
    ("signing", "build-engineer"), ("packaging", "build-engineer"), ("build automation", "build-engineer"),
    ("platform toolchain", "build-engineer"), ("engine environment", "tools-engineer"),
    ("source control", "tools-engineer"), ("testing", "qa-lead"), ("diagnostics", "support-engineer"),
    ("performance", "performance-engineer"), ("audio", "audio-lead"), ("animation", "animation-lead"),
    ("art-3d", "3d-artist"), ("art-2d", "2d-artist"), ("materials", "technical-artist"),
    ("rendering", "technical-artist"), ("lighting", "lighting-artist"), ("vfx", "vfx-artist"),
    ("cinematics", "cinematics-lead"), ("user interface", "ui-programmer"),
    ("accessibility", "accessibility-lead"), ("localization", "localization-lead"),
    ("narrative", "narrative-designer"), ("monetization", "live-ops-lead"), ("economy", "economy-designer"),
    ("community", "community-manager"), ("live operations", "live-ops-lead"), ("analytics", "analytics-lead"),
    ("security", "security-engineer"), ("netcode", "network-programmer"),
    ("online services", "backend-engineer"), ("persistence", "gameplay-programmer"),
    ("physics", "gameplay-programmer"), ("navigation", "ai-programmer"), ("gameplay ai", "ai-programmer"),
    ("input", "gameplay-programmer"), ("xr", "xr-engineer"), ("level design", "level-designer"),
    ("procedural content", "technical-designer"), ("offline generation", "technical-artist"),
    ("lifecycle", "producer"), ("planning", "producer"), ("gameplay", "gameplay-programmer"),
]
_DIFFICULTY_BY_EST = ((2, "Foundational"), (5, "Intermediate"), (9, "Advanced"), (10 ** 9, "Expert"))


def topic_tags(name: str, category_title: str = "", description: str = "") -> list:
    """Topic tags derived from the capability name and its category title.

    Matching is on word boundaries and case-insensitive, so 'audit' does not tag 'audio' and
    'ai' does not tag 'maintain'. Longest phrase wins; a topic is never tagged twice.
    """
    hay = f"{kit.tidy(name)} {kit.tidy(category_title)}"
    found = []
    for phrase, tag in _TOPIC_RULES:
        if not re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", hay.lower()):
            continue
        if tag in found:
            continue
        found.append(tag)
    if not found:
        found = ["general"]
    return found


def role_for(tags: list, archetype: str, arch=None) -> str:
    for tag in tags:
        for needle, role in _ROLE_BY_TOPIC:
            if tag == needle:
                return role
    default = (arch_map(arch).get(archetype) or {}).get("default_owner")
    return str(default) if default else "producer"


def estimate_for(tags: list, description: str, archetype: str) -> tuple:
    """(est_days, difficulty) from the size of the work the description claims, not a guess."""
    words = len((description or "").split())
    base = {"implement": 5, "create": 4, "operate": 4, "evaluate": 3, "provide": 3, "define": 2}
    days = base.get(archetype, 3)
    days += 1 if words >= 40 else 0
    days += 1 if len(tags) >= 3 else 0
    days += 2 if "art-3d" in tags or "photogrammetry" in tags or "build automation" in tags else 0
    days = max(1, min(days, 25))
    for ceiling, label in _DIFFICULTY_BY_EST:
        if days <= ceiling:
            return days, label
    return days, "Expert"


def risk_for(name: str, description: str, tags: list) -> tuple:
    """(risk_class, reasons): approval-sensitive, resource-heavy, or routine."""
    hay = f"{kit.tidy(name)} {kit.tidy(description)}".lower()
    reasons = [marker for marker in _APPROVAL_MARKERS if marker in hay]
    if reasons:
        return "approval-required", reasons[:4]
    heavy = [marker for marker in _HEAVY_MARKERS if marker in hay]
    if heavy:
        return "resource-heavy", heavy[:4]
    return "routine", []


def engine_scope(name: str, description: str, category_title: str) -> list:
    """Which engine packs a capability is bound to; naming none means all four stay in scope."""
    hay = f"{kit.tidy(name)} {kit.tidy(description)} {kit.tidy(category_title)}".lower()
    markers = {"godot": ("godot", "gdscript", "projectsettings"),
               "unity": ("unity", "prefab", "gameobject", "addressables"),
               "unreal": ("unreal", "blueprint", "uproject", "niagara"),
               "roblox": ("roblox", "rbxlx", "replicatedstorage")}
    found = [engine for engine, needles in markers.items() if any(n in hay for n in needles)]
    return sorted(found) or ["godot", "unity", "unreal", "roblox"]


# ---------------------------------------------------------------------------
# dataset normalization
# ---------------------------------------------------------------------------
def normalize_dataset(raw: dict, arch: dict | None = None, source: str = "") -> tuple:
    """Accept the nested shipped shape or a flat {categories, entries} shape.

    Returns (dataset, findings) where every capability record already carries the derived
    fields 'export' and the generators read. Raises InputError when the file has no usable
    categories at all.
    """
    if not isinstance(raw, dict):
        raise kit.InputError(f"the catalog dataset must be a JSON object ({source or 'input'})",
                             hint=f"the shipped shape is documented in "
                                  f"{kit.SKILL_TOOL_NAME}/knowledge-readme.md", where=source)
    findings: list = []
    cats_raw = raw.get("categories")
    if not isinstance(cats_raw, list) or not cats_raw:
        raise kit.InputError(f"no 'categories' array found in {source or 'the dataset'}",
                             hint="the dataset needs categories[] whose entries[] hold the capabilities",
                             where=source)
    flat_entries = raw.get("entries") or raw.get("capabilities") or []
    categories = []
    by_number: dict = {}
    for pos, cat in enumerate(cats_raw, start=1):
        if not isinstance(cat, dict):
            findings.append({"severity": "error", "code": "E-CAT-TYPE",
                             "detail": f"categories[{pos - 1}] is {type(cat).__name__}, expected object",
                             "location": f"{source}:categories[{pos - 1}]"})
            continue
        number = cat.get("number") or pos
        try:
            number = int(str(number).lstrip("cC") or pos)
        except ValueError:
            findings.append({"severity": "error", "code": "E-CAT-NUMBER",
                             "detail": f"category number {cat.get('number')!r} is not an integer",
                             "location": f"{source}:categories[{pos - 1}]"})
            number = pos
        title = kit.tidy(str(cat.get("title") or cat.get("name") or ""))
        if not title:
            findings.append({"severity": "error", "code": "E-CAT-TITLE",
                             "detail": "a category has no title", "location": f"{source}:c{number:02d}"})
        if number in by_number:
            findings.append({"severity": "error", "code": "E-CAT-DUP",
                             "detail": f"duplicate category number {number}",
                             "location": f"{source}:c{number:02d}"})
        declared = int(cat.get("declared_count") or cat.get("count") or 0)
        entry_source = cat.get("entries")
        if entry_source is None and isinstance(flat_entries, list) and flat_entries:
            entry_source = [e for e in flat_entries
                            if int(str(e.get("category") or e.get("category_id") or 0)) == number]
        entries = []
        for idx, entry in enumerate(entry_source or [], start=1):
            record = normalize_entry(entry, number, title, idx, arch, source, findings)
            if record is not None:
                entries.append(record)
        category = {"number": number, "title": title, "slug": cat.get("slug") or slugify(title),
                    "declared_count": declared or len(entries), "count": len(entries),
                    "layer": layer_for(number), "entries": entries}
        categories.append(category)
        by_number[number] = category
    records = [entry for category in categories for entry in category["entries"]]
    declared_total = raw.get("entry_count") or raw.get("counts", {}).get("entries")
    if declared_total and int(declared_total) != len(records):
        findings.append({"severity": "error", "code": "E-COUNT-MISMATCH",
                         "detail": f"the dataset declares {declared_total} capabilities but holds "
                                   f"{len(records)}", "location": f"{source}:entry_count"})
    if not records:
        raise kit.InputError(f"no capability entries found in {source or 'the dataset'}",
                             hint="each category object needs an entries[] array", where=source)
    dataset = dict(raw)
    dataset["categories"] = categories
    dataset["category_count"] = len(categories)
    dataset["entry_count"] = len(records)
    dataset["records"] = records
    dataset.setdefault("fingerprint", kit.fingerprint([
        {key: entry.get(key) for key in ("id", "ref", "name", "slug", "category", "archetype",
                                         "subject", "description")} for entry in records]))
    return dataset, findings


def normalize_entry(entry: dict, number: int, category_title: str, index: int, arch, source: str,
                    findings: list) -> dict | None:
    """One capability record, with derived fields; None (plus a finding) when unusable."""
    location = f"{source}:c{number:02d}e{index:02d}"
    if not isinstance(entry, dict):
        findings.append({"severity": "error", "code": "E-ENTRY-TYPE",
                         "detail": f"entry #{index} in category {number} is "
                                   f"{type(entry).__name__}, expected object", "location": location})
        return None
    name = kit.tidy(str(entry.get("name") or entry.get("title") or ""))
    description = kit.tidy(str(entry.get("description") or ""))
    if not name:
        findings.append({"severity": "error", "code": "E-ENTRY-NAME",
                         "detail": f"entry #{index} in category {number} has no name", "location": location})
        return None
    if len(description) < 20:
        findings.append({"severity": "error", "code": "E-ENTRY-DESC",
                         "detail": f"'{name[:40]}' has no usable description", "location": location})
        return None
    entry_id = str(entry.get("id") or f"c{number:02d}e{index:02d}")
    index_no = int(entry.get("index") or entry_id.split("e")[-1] or index)
    archetype = str(entry.get("archetype") or "")
    subject = kit.tidy(str(entry.get("subject") or ""))
    if archetype not in ARCHETYPE_NAMES:
        found, subject_from_desc = match_archetype(description, arch)
        archetype = found or str(entry.get("inferred_archetype") or "implement")
        subject = subject or subject_from_desc
    if not subject:
        subject = slugify(name).replace("-", " ") or kit.tidy(name).lower()
    slug = str(entry.get("slug") or slugify(name))
    tags = list(entry.get("tags") or []) or topic_tags(name, category_title, description)
    est_days = int(entry.get("est_days") or 0)
    difficulty = str(entry.get("difficulty") or "")
    if not est_days or not difficulty:
        est_days, computed = estimate_for(tags, description, archetype)
        difficulty = difficulty or computed
    role = str(entry.get("role") or "") or role_for(tags, archetype, arch)
    risk_class, risk_reasons = risk_for(name, description, tags)
    record = {key: value for key, value in {
        "id": entry_id, "ref": str(entry.get("ref") or f"{number}.{index_no}"), "name": name,
        "slug": slug, "category": number, "category_title": category_title,
        "layer": layer_for(number), "archetype": archetype, "subject": subject,
        "description": description, "tags": tags, "role": role, "difficulty": difficulty,
        "est_days": est_days, "engines": list(entry.get("engines") or [])
        or engine_scope(name, description, category_title),
        "risk_class": str(entry.get("risk_class") or risk_class),
        "risk_reasons": list(entry.get("risk_reasons") or risk_reasons),
    }.items()}
    for key in ("archetype_source", "notes", "evidence_kind", "owner"):
        if entry.get(key) not in (None, ""):
            record[key] = entry[key]
    record["search_text"] = kit.tidy(f"{name} {description} {' '.join(tags)} {category_title}").lower()
    record["template_ok"] = describe(archetype, subject) == description
    if not record["template_ok"]:
        findings.append({"severity": "warning", "code": "W-DESC-TEMPLATE",
                         "detail": f"'{name[:36]}' does not match its archetype template; treat the "
                                   f"stored description as the authority", "location": location})
    return record


def load_catalog(path: str, arch: dict | None = None, max_mb: float = 64, deadline=None) -> tuple:
    """Read and validate a dataset file -> (dataset, findings). Structured errors, never a traceback."""
    raw = kit.load_json(path, max_mb or 64, deadline)
    dataset, findings = normalize_dataset(raw, arch, source=os.path.basename(path))
    problems = [f for f in findings if f["severity"] == "error"]
    if problems:
        raise kit.InputError(f"the catalog dataset is not valid: {problems[0]['detail']}",
                             hint="run 'validate --dataset' for the whole finding list, or re-import the "
                                  "source text", where=path, findings=problems[:20])
    return dataset, findings


def validate_dataset(dataset: dict, arch: dict | None = None) -> list:
    """Structural and content checks over a normalized dataset. Returns findings."""
    findings: list = []
    for key in DATASET_KEYS:
        if key not in dataset:
            findings.append({"severity": "error", "code": "E-MISSING-KEY",
                             "detail": f"top-level key '{key}' is missing", "location": "<root>"})
    seen: set = set()
    records = dataset.get("records") or [e for c in dataset.get("categories", []) for e in c["entries"]]
    for record in records:
        location = record.get("id", "?")
        missing = [key for key in ENTRY_KEYS if key not in record]
        if missing:
            findings.append({"severity": "error", "code": "E-RECORD-KEYS",
                             "detail": f"{location} is missing {missing}", "location": location})
            continue
        if record["id"] in seen:
            findings.append({"severity": "error", "code": "E-DUP-ID",
                             "detail": f"duplicate capability id {record['id']}", "location": location})
        seen.add(record["id"])
        if not re.match(r"^c\d{2}e\d{2}$", record["id"]):
            findings.append({"severity": "error", "code": "E-ID-FORMAT",
                             "detail": f"id {record['id']!r} is not c<NN>e<NN>", "location": location})
        expected_ref = ""
        try:  # the human reference is '<category>.<entry>' with no zero padding: 13.5, not 13.05
            expected_ref = f"{record['category']}.{int(str(record['id']).split('e')[1])}"
        except (IndexError, ValueError):
            expected_ref = str(record.get("ref") or "")
        if record["ref"] != expected_ref:
            findings.append({"severity": "warning", "code": "W-REF-DRIFT",
                             "detail": f"ref {record['ref']!r} does not follow "
                                       f"<category>.<entry>", "location": location})
        if record["archetype"] not in ARCHETYPE_NAMES:
            findings.append({"severity": "error", "code": "E-ARCHETYPE",
                             "detail": f"unknown archetype {record['archetype']!r}", "location": location})
        if record["layer"] not in [layer[0] for layer in LAYERS]:
            findings.append({"severity": "error", "code": "E-LAYER",
                             "detail": f"unknown layer {record['layer']!r}", "location": location})
        if not record["description"]:
            findings.append({"severity": "error", "code": "E-EMPTY-DESC",
                             "detail": "description must not be empty", "location": location})
    for category in dataset.get("categories", []):
        declared = int(category.get("declared_count") or 0)
        actual = int(category.get("count") or len(category.get("entries", [])))
        if declared and declared != actual:
            findings.append({"severity": "error", "code": "E-CAT-COUNT",
                             "detail": f"category '{category['title']}' declares {declared} capabilities, "
                                       f"found {actual}", "location": f"c{category['number']:02d}"})
    return findings


# ---------------------------------------------------------------------------
# ingest: plain text, Markdown, HTML, CSV, TSV, JSON
# ---------------------------------------------------------------------------
_NAME_LABELS = ("skill name:", "skill:", "capability:", "name:", "title:")
_DESC_LABELS = ("description:", "summary:")


def parse_source(text: str, arch: dict | None = None, origin: str = "", fmt: str = "auto",
                 findings: list | None = None) -> dict:
    """Parse a pasted catalog into the shipped dataset shape.

    Markdown, plain text, and HTML (the shape a copy from a wiki or a documentation table
    arrives in) are all accepted: tags, CSS, and row numbering are stripped first, so the
    markup never changes what is extracted. Returns {schema_version, dataset_id,
    source_document, category_count, entry_count, categories, stats}. Findings - dropped
    lines, inferred archetypes, count mismatches - are appended to the 'findings' list the
    caller passes in.
    """
    sink = findings if findings is not None else []
    fmt = (fmt or "auto").lower()
    if fmt in ("csv", "tsv"):
        return parse_delimited(text, fmt, sink, origin)
    if fmt == "json":
        return parse_json_source(text, arch, sink, origin)
    if fmt not in ("auto", "markdown", "html", "text"):
        raise kit.UsageError(f"unsupported source format {fmt!r}",
                             hint="choose from auto|markdown|html|text|csv|tsv|json", where="--input-format")
    detected = []
    if fmt in ("auto", "html") and re.search(r"(?is)<(table|div|p|html|body|span|b|i|td|ul|li)\b",
                                             text or ""):
        text = kit.strip_html(text)
        detected.append("html")
        fmt = "html"
    text = kit.strip_markdown(text or "")
    text = kit.normalize_text(text)
    detected.append("markdown-normalized")

    lines = _rejoin_wrapped(text.split("\n"))
    categories: list = []
    current: dict | None = None
    pending: dict | None = None
    unclassified = 0

    def flush():
        nonlocal pending
        if pending is None:
            return
        if not pending.get("description"):
            pending["description"] = ""
        if current is not None and pending["name"]:
            current["entries"].append(pending)
        else:
            unclassified += 1
        pending = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) < 3:                                    # stray table cell, bullet, or rule
            unclassified += 1
            continue
        heading = re.match(r"^(#{1,6})\s*(.*)$", stripped)
        if heading and kit.tidy(heading.group(2)).lower() not in ("skill name", "description", "skill",
                                                                  "capability", "name", "title"):
            level, body = len(heading.group(1)), re.sub(r"^\d+(?:\.\d+)*\s*[.)]?\s*", "",
                                                       heading.group(2).strip())
            flush()
            if _looks_like_category(body, level):
                current = {"number": len(categories) + 1,
                           "title": kit.title_case(re.sub(r"^\d+\s*[.)]?\s*", "", body)),
                           "declared_count": _trailing_count(stripped), "entries": []}
                categories.append(current)
            elif current is not None:
                pending = {"index": len(current["entries"]) + 1, "name": _strip_label(body)}
            else:
                unclassified += 1
            continue
        label = _line_info(stripped)
        if label is None and _is_wrapped_text(stripped, pending):
            pending["description"] = kit.tidy(pending["description"] + " " + stripped)
            continue          # a description that wrapped onto the next source line
        if label is None:
            if current is not None and re.match(r"^\d+\s+skill\s+(concepts|skills)\b", stripped, re.I):
                current["declared_count"] = int(stripped.split()[0])
                continue
            if not stripped or re.match(r"^(Full Offline|Catalog Edition|Offline-first|Layer \d)", stripped,
                                        re.I):
                continue
            unclassified += 1
            continue
        kind, value = label
        if current is None:
            current = {"number": len(categories) + 1,
                       "title": derive_category_name(value or stripped, len(categories)),
                       "declared_count": 0, "entries": []}
            categories.append(current)
            pending = None
        if kind == "name":
            if pending is not None and not pending.get("description"):
                pending["name"] = value or pending["name"]      # heading plus its label line
            else:
                flush()
                pending = {"index": len(current["entries"]) + 1, "name": value}
        else:
            if pending is None or not pending.get("name"):
                pending = {"index": len(current["entries"]) + 1, "name": _auto_title(value)}
            pending["description"] = kit.tidy(f"{pending.get('description', '')} {value}")
    flush()

    categories = [c for c in categories if c["entries"]]
    for position, category in enumerate(categories, start=1):
        category["number"] = position
    if not categories:
        raise kit.InputError("no capabilities were found in the source text",
                             hint="an entry needs a 'Skill Name:' (or heading) line plus a "
                                  "'Description:' line; see "
                                  f"{kit.SKILL_TOOL_NAME}/asset-sample-source.md for the exact shape",
                             where=origin or "source text", sample_lines=[l for l in text.split("\n")[:5]])
    inferred = 0
    for category in categories:
        for entry in category["entries"]:
            archetype, subject = match_archetype(entry["description"], arch)
            if archetype:
                entry["archetype"], entry["subject"] = archetype, subject or slugify(entry["name"]).replace("-", " ")
                entry["archetype_source"] = "catalog" if entry["subject"] else "verb"
            else:
                archetype = infer_archetype_from_name(entry["name"], arch)
                entry["archetype"] = archetype
                entry["subject"] = slugify(entry["name"]).replace("-", " ")
                entry["description"] = describe(archetype, entry["subject"])
                entry["archetype_source"] = "inferred"
                inferred += 1
                findings_line = {"severity": "warning", "code": "W-INFERRED-ARCHETYPE",
                                 "detail": f"'{entry['name'][:36]}' has no catalog-style description; "
                                           f"archetype '{archetype}' and the description wording were "
                                           f"generated", "location": entry["name"]}
                sink.append(findings_line)
            entry["name"] = kit.tidy(entry["name"])
            entry["slug"] = slugify(entry["name"])
            entry["description"] = kit.tidy(entry["description"])
            if entry["description"].lower().startswith(entry["name"].lower() + " "):
                # a heading-only source can swallow the name into the description
                entry["description"] = describe(entry["archetype"], entry["subject"])
        declared, actual = category["declared_count"], len(category["entries"])
        if declared and declared != actual:
            sink.append({"severity": "warning", "code": "W-COUNT-MISMATCH",
                         "detail": f"category '{category['title']}' declares {declared} capabilities, "
                                   f"parsed {actual}", "location": category["title"]})
    total = sum(len(c["entries"]) for c in categories)
    if inferred:
        pass
    return {
        "schema_version": "1.0",
        "dataset_id": "imported-catalog",
        "source_document": {"title": os.path.basename(origin) if origin else "pasted source text",
                            "imported": True, "input_formats": detected or [fmt]},
        "category_count": len(categories),
        "entry_count": total,
        "categories": [{"number": c["number"], "title": c["title"], "slug": slugify(c["title"]),
                        "declared_count": c["declared_count"] or len(c["entries"]), "entries": c["entries"]}
                       for c in categories],
        "stats": {"inferred_archetypes": inferred, "unclassified_lines": unclassified},
    }


_LABEL_ONLY = re.compile(r"(?i)^(skill\s+name|skill|capability|name|title|description|summary)\s*:?$")


def _rejoin_wrapped(lines: list) -> list:
    """Two repairs the raw shapes need: a label split from its value, and a wrapped description."""
    glued: list = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if glued and _LABEL_ONLY.fullmatch(text.rstrip(":")) and glued[-1].rstrip().endswith(":"):
            glued[-1] = glued[-1].rstrip() + " " + text      # <b>Skill Name:</b> alone on a line
            continue
        glued.append(text)
    return glued


_STRUCTURE_START = re.compile(r"^(?:#|\d+(?:\.\d+)*[.)]?\s+\S|\d+\s+skill\b|[-*+\u2022]\s)", re.I)


def _is_wrapped_text(stripped: str, pending: dict | None) -> bool:
    """Is this bare line the tail of the description above it?

    Yes while the sentence is unfinished (no closing period), and also after one when the fragment
    opens in lower case - that is how a wrapped catalog sentence looks. A line that opens a
    structure of its own (a heading, a numbered heading, a bullet, a '42 skill concepts' count)
    is never swallowed into the description above it.
    """
    if pending is None or not pending.get("description") or not stripped or len(stripped) > 400:
        return False
    if _STRUCTURE_START.match(stripped):
        return False
    if _line_info(stripped) is not None:
        return False
    if not str(pending["description"]).rstrip().endswith("."):
        return True
    return stripped[:1].islower()


def _line_info(line: str) -> tuple | None:
    """('name', value) or ('description', value) for a labelled capability line, else None."""
    low = line.lower()
    for prefix in _NAME_LABELS:
        if low.startswith(prefix):
            value = _strip_label(line[len(prefix):].strip())
            return ("name", value) if value else None
    for prefix in _DESC_LABELS:
        if low.startswith(prefix):
            value = line[len(prefix):].strip()
            return ("description", value) if value else None
    return None


def _strip_label(text: str) -> str:
    return re.sub(r"^(skill name|skill|capability|name|title)\s*[:\-]\s*", "", text, flags=re.I).strip()


def _looks_like_category(body: str, level: int) -> bool:
    if re.match(r"^\d+\.\d+", body):
        return False          # "1.2 Name" is an entry, not a category
    if level == 1:
        return True
    low = body.lower()
    if re.match(r"^category\b", low) or "skill concepts" in low or "acceptance criteria" in low:
        return True
    return level == 2 and len(body.split()) <= 9


def _trailing_count(text: str) -> int:
    match = re.search(r"(\d+)\s+skill\s+(concepts|skills)", text, re.I)
    return int(match.group(1)) if match else 0


def _auto_title(description: str) -> str:
    words = re.sub(r"[^A-Za-z0-9#+. ]", "", description or "").split()
    return kit.title_case(" ".join(words[:5])) or "Untitled Capability"


def derive_category_name(title: str, index: int = 0) -> str:
    """Category title for a heading-less source, taken from the first entry's words."""
    base = re.sub(r"^\d+(?:\.\d+)*\s*[.)]?\s*", "", kit.tidy(title))
    base = _strip_label(base)
    base = re.sub(r"^(generator|tool|system|module)\b", "", base, flags=re.I).strip()
    words = [word for word in re.split(r"[\s/\u2014-]+", base) if word]
    if not words:
        return f"Imported Category {index + 1}"
    return kit.title_case(" ".join(words[:3]))[:70]


def infer_archetype_from_name(name: str, arch=None) -> str:
    """Classify a capability the source did not label, using the asset's inference rules."""
    rules = arch_rules(arch)
    lowered = kit.tidy(name).lower()
    for rule in rules or []:
        needles = rule.get("if_name_contains") or []
        if any(str(needle).lower() in lowered for needle in needles):
            return rule.get("then", "define")
    for rule in DEFAULT_INFERENCE:
        if any(needle in lowered for needle in rule[0]):
            return rule[1]
    if isinstance(arch, dict):
        return str(arch.get("default_archetype") or arch_map(arch).get("default_archetype") or "define")
    return "define"


DEFAULT_INFERENCE = [
    (("test", "audit", "review", "benchmark", "verification", "acceptance", "profiler"), "evaluate"),
    (("policy", "brief", "plan", "matrix", "definition", "guideline", "standard", "spec", "roadmap",
      "governance"), "define"),
    (("generator", "builder", "packager", "installer", "bundler", "exporter", "importer", "converter",
      "compiler", "scaffold", "cook", "patch"), "create"),
    (("server", "service", "runtime", "manager", "daemon", "queue", "monitor", "scheduler", "worker",
      "gateway"), "operate"),
    (("kit", "template", "interface", "adapter", "bridge", "sdk", "wrapper", "mock", "shim"), "provide"),
]


def parse_delimited(text: str, dialect: str = "csv", findings: list | None = None,
                    origin: str = "") -> dict:
    """CSV/TSV with a name column and a description column; a category column splits groups."""
    sink = findings if findings is not None else []
    import csv as _csv

    sample = (text or "")[:8192]
    delimiter = "\t" if dialect == "tsv" else ","
    if dialect != "tsv":
        try:
            delimiter = _csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
        except Exception:
            delimiter = ","
    rows = list(_csv.reader(io_strings(text), delimiter=delimiter))
    if len(rows) < 2:
        raise kit.InputError(f"the {dialect.upper()} source has no data rows ({origin or 'input'})",
                             hint="row 1 is the header; a name column and a description column are "
                                  "required", where=origin or f"{dialect} input")
    header = [kit.tidy(cell).lower().replace(" ", "_").replace("-", "_") for cell in rows[0]]
    columns = [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in rows[1:]]
    pick = lambda *names: next((name for name in names if name in header), None)  # noqa: E731
    name_key = pick("name", "skill_name", "skill", "capability", "title")
    desc_key = pick("description", "summary", "text", "detail")
    cat_key = pick("category", "category_name", "group", "category_title")
    if not name_key or not desc_key:
        raise kit.InputError(f"the {dialect.upper()} source needs a name column and a description column",
                             hint=f"header found: {header}", where=origin or f"{dialect} header")
    buckets: dict = {}
    skipped = 0
    for pos, row in enumerate(columns, start=2):
        name = kit.tidy(_csv_cell(row.get(name_key)))
        description = kit.tidy(_csv_cell(row.get(desc_key)))
        if not name or not description:
            skipped += 1
            continue
        bucket = kit.tidy(_csv_cell(row.get(cat_key))) if cat_key else ""
        buckets.setdefault(bucket or "Imported Capabilities", []).append(
            {"index": len(buckets.get(bucket or "Imported Capabilities", [])) + 1, "name": name,
             "description": description})
    if skipped:
        sink.append({"severity": "warning", "code": "W-DROPPED-LINES",
                     "detail": f"{skipped} row(s) had no name or no description and were skipped",
                     "location": origin or f"{dialect} rows"})
    categories = [{"number": num, "title": kit.title_case(title), "declared_count": len(entries),
                   "entries": entries} for num, (title, entries) in enumerate(buckets.items(), start=1)]
    inferred = 0
    for category in categories:
        for entry in category["entries"]:
            archetype, subject = match_archetype(entry["description"], None)
            if not archetype:
                archetype = infer_archetype_from_name(entry["name"], None)
                subject = slugify(entry["name"]).replace("-", " ")
                entry["description"] = describe(archetype, subject)
                entry["archetype_source"] = "inferred"
                inferred += 1
                sink.append({"severity": "warning", "code": "W-INFERRED-ARCHETYPE",
                             "detail": f"'{entry['name'][:36]}' has no catalog-style description; the "
                                       f"archetype '{archetype}' and its wording were generated",
                             "location": entry["name"]})
            entry["slug"] = slugify(entry["name"])
            entry["subject"] = subject
            entry["archetype"] = archetype
    return {
        "schema_version": "1.0", "dataset_id": "imported-catalog",
        "source_document": {"title": os.path.basename(origin) if origin else f"pasted {dialect}",
                            "imported": True, "input_formats": [dialect]},
        "category_count": len(categories),
        "entry_count": sum(len(c["entries"]) for c in categories),
        "categories": [{"number": c["number"], "title": c["title"], "slug": slugify(c["title"]),
                        "declared_count": c["declared_count"], "entries": c["entries"]}
                       for c in categories],
        "stats": {"inferred_archetypes": inferred, "unclassified_lines": skipped},
    }


def io_strings(text: str):
    import io

    return io.StringIO((text or "").replace("\r\n", "\n"))


def _csv_cell(value) -> str:
    return "" if value is None else str(value)


def parse_json_source(text: str, arch: dict | None = None, findings: list | None = None,
                      origin: str = "") -> dict:
    """A JSON array of {name/Skill Name, description[, category]} objects, or a full dataset."""
    import json

    try:
        raw = json.loads(text or "")
    except ValueError as exc:
        raise kit.InputError(f"invalid JSON in the source: {exc}",
                             hint="pass an array of capability objects, or a dataset object with "
                                  "categories[]", where=origin or "json input") from exc
    sink = findings if findings is not None else []
    if isinstance(raw, dict) and raw.get("categories"):
        dataset, inner = normalize_dataset(raw, arch, source=origin)
        sink.extend(item for item in inner if item["severity"] == "error")
        if inner:
            raise kit.InputError(f"the JSON dataset is not valid: {inner[0]['detail']}",
                                 hint="fix the location named in the finding, then re-run import",
                                 where=origin or "json input")
        return dataset
    items = raw if isinstance(raw, list) else [raw]
    buckets: dict = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = kit.tidy(str(item.get("name") or item.get("Skill Name") or item.get("title") or ""))
        desc = kit.tidy(str(item.get("description") or item.get("Description") or ""))
        if not name or not desc:
            continue
        cat = kit.tidy(str(item.get("category") or item.get("category_title") or "Imported Capabilities"))
        buckets.setdefault(cat, []).append({"name": name, "description": desc,
                                            "tags": item.get("tags") or [],
                                            "archetype": item.get("archetype") or "",
                                            "subject": item.get("subject") or ""})
    if not buckets:
        raise kit.InputError("the JSON source holds no usable capability objects",
                             hint="each object needs a name and a description; see "
                                  f"{kit.SKILL_TOOL_NAME}/asset-sample-source.md",
                             where=origin or "json input")
    categories = [{"number": num, "title": kit.title_case(title), "declared_count": len(entries),
                   "entries": [dict(entry, index=pos) for pos, entry in enumerate(entries, start=1)]}
                  for num, (title, entries) in enumerate(buckets.items(), start=1)]
    for category in categories:
        for entry in category["entries"]:
            entry["slug"] = slugify(entry["name"])
            if entry["archetype"] not in ARCHETYPE_NAMES:
                entry["archetype"] = infer_archetype_from_name(entry["name"], arch)
                entry["subject"] = slugify(entry["name"]).replace("-", " ")
    return {
        "schema_version": "1.0", "dataset_id": "imported-catalog",
        "source_document": {"title": os.path.basename(origin) if origin else "pasted json",
                            "imported": True, "input_formats": ["json"]},
        "category_count": len(categories),
        "entry_count": sum(len(c["entries"]) for c in categories),
        "categories": categories,
        "stats": {"inferred_archetypes": 0, "unclassified_lines": 0},
    }


# ---------------------------------------------------------------------------
# catalog access
# ---------------------------------------------------------------------------
class Catalog:
    """Lookup, selection, and search over a normalized dataset."""

    def __init__(self, dataset: dict, arch: dict | None = None):
        self.dataset = dataset
        self.arch = arch or {}
        self.categories = {category["number"]: category for category in dataset["categories"]}
        self.categories_by_slug = {category["slug"]: category for category in dataset["categories"]}
        self.records = [CatalogEntry(record, self) for record in dataset["records"]]
        self.index = {record["id"]: record for record in self.records}
        self.by_slug: dict = {}
        for record in self.records:
            self.by_slug.setdefault(record["slug"], []).append(record)
        self.meta = {"dataset_id": dataset.get("dataset_id"), "fingerprint": dataset.get("fingerprint"),
                     "category_count": dataset.get("category_count"),
                     "entry_count": dataset.get("entry_count"),
                     "source_document": (dataset.get("source_document") or {}).get("title"),
                     "tagline": (dataset.get("source_document") or {}).get("tagline"),
                     "edition": (dataset.get("source_document") or {}).get("edition")}

    # -- lookup ----------------------------------------------------------
    def get(self, entry_id: str) -> "CatalogEntry":
        record = self.index.get(str(entry_id))
        if record is None:
            raise kit.NotFoundError(f"no capability with id {entry_id!r}",
                                    hint="check 'list' output, or use a substring from the name with "
                                         "'search'; closest ids: " + (", ".join(self.suggest(entry_id))
                                                                     or "none"),
                                    where=str(entry_id), closest=self.suggest(entry_id))
        return record

    def suggest(self, token: str, limit: int = 4) -> list:
        low = kit.tidy(token).lower()
        scored = []
        for record in self.records:
            score = 0
            if low and (record["id"].startswith(low) or record["slug"].startswith(low)):
                score = 3
            elif low and (low in record["slug"] or low in record["name"].lower()):
                score = 2
            elif low and low in record["search_text"]:
                score = 1
            if score:
                scored.append((score, -len(record["slug"]), record["id"]))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        if scored:
            return [item[2] for item in scored[:limit]]
        # a typo matches nothing by substring, so fall back to the nearest wording we have
        by_text = {}
        for record in self.records:
            by_text.setdefault(record["slug"].lower(), record["id"])
            by_text.setdefault(record["name"].lower(), record["id"])
        import difflib

        near = difflib.get_close_matches(low, list(by_text), n=limit, cutoff=0.6)
        seen: list = []
        for text in near:
            identifier = by_text.get(text)
            if identifier and identifier not in seen:
                seen.append(identifier)
        return seen[:limit]

    def resolve(self, token: str) -> "ResolveResult":
        """A capability token -> the records it names: an id, a short id, a slug, or a name fragment."""
        token = str(token or "").strip()
        if not token:
            return ResolveResult([], error="empty capability reference")
        if token.startswith(("./", "../", "/", "offline-game-development/", "entries/")):
            return ResolveResult([], error=f"{token!r} looks like a file path, not a capability; pass an "
                                          f"id such as 'c13e05' or a name such as 'Sprite Generator'")
        ref = token.split("/")[-1].split("#")[-1].strip()
        if ref in self.index:
            return ResolveResult([self.index[ref]])
        if re.fullmatch(r"c?\d{1,2}[.e]?\d{1,3}", ref.lower()):
            digits = re.findall(r"\d+", ref)
            if len(digits) == 2:
                candidate = f"c{int(digits[0]):02d}e{int(digits[1]):02d}"
                if candidate in self.index:
                    return ResolveResult([self.index[candidate]])
                return ResolveResult([], error=f"no capability {candidate} (category {int(digits[0])} has "
                                              f"{self.categories.get(int(digits[0]), {}).get('count', 0)} "
                                              f"entries)", where=token)
        slug = slugify(ref)
        matches = list(self.by_slug.get(slug, []))
        if not matches:
            matches = [record for entry_slug, bucket in self.by_slug.items()
                       if slug and (slug in entry_slug or entry_slug in slug) for record in bucket]
        if len(matches) == 1:
            return ResolveResult(matches)
        if matches:
            unique = []
            for record in matches:
                if record not in unique:
                    unique.append(record)
            return ResolveResult(unique[:8], error=f"{token!r} matches {len(unique)} capabilities",
                                 ambiguous=True)
        return ResolveResult([], error=f"no capability matches {token!r}",
                             suggestions=self.suggest(token)[:3])

    def select(self, tokens, require: bool = True, limit: int = 0, deadline=None) -> tuple:
        """(records, skipped): resolve many tokens into one ordered, de-duplicated selection."""
        if isinstance(tokens, str):
            tokens = [tokens] if tokens else []
        chosen: list = []
        skipped: list = []
        for token in tokens or []:
            if deadline:
                deadline.tick(f"select {token}")
            found = self.resolve(token)
            if not found:
                if require:
                    hint_bits = []
                    if found.suggestions:
                        hint_bits.append("did you mean " + ", ".join(found.suggestions) + "?")
                    hint_bits.append("'list' shows every capability id")
                    raise kit.NotFoundError(found.error or f"no capability matches {token!r}",
                                            hint="; ".join(hint_bits), where=str(token))
                skipped.append({"token": str(token), "reason": found.error})
                continue
            if found.ambiguous:
                ids = [record["id"] for record in found]
                raise kit.AmbiguousError(found.error or f"{token!r} is ambiguous",
                                         hint="pass the exact id; several capabilities share this wording",
                                         where=str(token), ids=ids[:12])
            record = found[0]
            if record not in chosen:
                chosen.append(record)
            if limit and len(chosen) >= limit:
                break
        return chosen, skipped

    def all_for(self, category_id: str = "", layer: str = "", tag: str = "", difficulty: str = "",
                role: str = "", engine: str = "", risk: str = "", deadline=None) -> list:
        rows = list(self.records)
        if category_id:
            wanted = self._category_numbers(category_id)
            rows = [r for r in rows if r["category"] in wanted]
        if layer:
            rows = [r for r in rows if kit.tidy(layer).lower() == r["layer"]]
        if tag:
            needle = kit.tidy(tag).lower()
            rows = [r for r in rows if any(needle == t for t in r["tags"])]
        if difficulty:
            rows = [r for r in rows if kit.tidy(difficulty).lower() == r["difficulty"].lower()]
        if role:
            rows = [r for r in rows if kit.tidy(role).lower() == r["role"].lower()]
        if engine:
            rows = [r for r in rows if engine in r["engines"]]
        if risk:
            rows = [r for r in rows if kit.tidy(risk).lower() == r["risk_class"].lower()]
        if deadline:
            deadline.tick("browse")
        return rows

    def _category_numbers(self, token: str) -> set:
        token = kit.tidy(str(token))
        if not token:
            return set(self.categories)
        if re.fullmatch(r"c?\d{1,2}", token.lower()):
            return {int(token.lower().lstrip("cC"))} & set(self.categories)
        if token.lower().lstrip("cC").isdigit():
            return {int(token.lower().lstrip("cC"))} & set(self.categories)
        slug = slugify(token)
        found = {number for number, category in self.categories.items() if category["slug"] == slug}
        if not found:
            found = {number for number, category in self.categories.items()
                     if slug and slug in category["slug"]}
        if not found:
            needle = token.lower()
            found = {number for number, category in self.categories.items()
                     if needle in category["title"].lower()}
        if not found:
            raise kit.NotFoundError(f"no category matches {token!r}",
                                    hint="run 'categories' for the exact titles and numbers",
                                    where=str(token))
        return found

    def search(self, query: str, category_id: str = "", limit: int = 25, offset: int = 0,
               deadline=None) -> dict:
        terms = re.findall(r"[a-z0-9+#.]{2,}", (query or "").lower())
        if not terms:
            raise kit.UsageError("search needs at least one word of two or more characters",
                                 hint="for example: search \"shader determinism\"", where="query")
        scoped = self.all_for(category_id=category_id, deadline=deadline)
        scored = []
        for record in scoped:
            if deadline:
                deadline.tick("search")
            name = record["name"].lower()
            tags = " ".join(record["tags"])
            score = 0
            for term in terms:
                if term in name:
                    score += 5
                if term in tags:
                    score += 3
                if term in record["search_text"]:
                    score += 1
            if score:
                scored.append({"record": record, "score": score,
                               "matched_terms": [t for t in terms if t in record["search_text"]]})
        scored.sort(key=lambda item: (-item["score"], item["record"]["id"]))
        total = len(scored)
        window = scored[offset:offset + limit] if limit else scored[offset:]
        return {"query": kit.tidy(query), "terms": terms, "total": total, "offset": offset,
                "limit": limit, "hits": window,
                "truncated": bool(limit and offset + limit < total)}

    # -- helpers other layers use ---------------------------------------
    def category(self, record: dict) -> dict:
        return self.categories.get(record["category"], {"number": record["category"], "title": "",
                                                        "slug": "", "count": 0, "layer": ""})

    def umbrella_name(self, records) -> str:
        """One unified name for a selection: the shared category title, else a combined label."""
        titles = []
        for record in records:
            title = self.category(record).get("title", "")
            if title and title not in titles:
                titles.append(title)
        if len(titles) == 1:
            return titles[0]
        if not titles:
            return "Selected Capabilities"
        return f"{titles[0]} (+{len(titles) - 1} more categories)"

    def engine_conventions(self, engine: str) -> dict:
        return ENGINES.get(engine, ENGINES["engine-neutral"])

    def template_ok(self, record: dict) -> bool:
        return describe(record["archetype"], record["subject"]) == record["description"]

    def __len__(self) -> int:
        return len(self.records)


class CatalogEntry(dict):
    """A capability record; attribute access keeps generator code readable."""

    def __init__(self, record: dict, catalog: Catalog):
        super().__init__(record)
        object.__setattr__(self, "_catalog", catalog)

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    @property
    def category(self) -> dict:
        return self._catalog.category(self)

    @property
    def ref_path(self) -> str:
        """How a spec or plan cites the record: relative to the future skill root."""
        return f"{kit.SKILL_TOOL_NAME}/asset-catalog.json#{self['id']}"

    @property
    def gates(self) -> list:
        spec = (self._catalog.arch.get("archetypes") or {}).get(self["archetype"], {})
        return spec.get("gates", [])

    @property
    def evidence_kinds(self) -> list:
        spec = (self._catalog.arch.get("archetypes") or {}).get(self["archetype"], {})
        return spec.get("evidence_kinds", [])

    @property
    def sections(self) -> list:
        spec = (self._catalog.arch.get("archetypes") or {}).get(self["archetype"], {})
        return spec.get("sections", [])

    @property
    def plan_phase(self) -> str:
        spec = (self._catalog.arch.get("archetypes") or {}).get(self["archetype"], {})
        return spec.get("plan_phase", "decide")


class ResolveResult(list):
    """Matches of one token: empty with .error when nothing matched, .ambiguous when several did."""

    def __init__(self, items=(), error: str = "", ambiguous: bool = False, suggestions=()):
        super().__init__(items)
        self.error = error
        self.ambiguous = ambiguous
        self.suggestions = list(suggestions)


__all__ = [name for name in dir() if not name.startswith("_")]
