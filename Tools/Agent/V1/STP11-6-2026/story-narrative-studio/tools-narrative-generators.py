#!/usr/bin/env python3
"""tools-narrative-generators.py - deterministic narrative planning engine.

Loaded by ``tools-story-narrative.py`` and ``tools-revision-planner.py`` through
``core.load_sibling_module``. Never run directly.

Each ``build_*`` function takes a validated spec dict and returns::

    (data, markdown, warnings)

where ``data`` is JSON-serialisable (stored in the project bundle) and
``markdown`` is a human-editable deliverable.

Design rule: outputs are *structured production scaffolding* - beats, budgets,
constraints, cast decisions, scene cards, page plans, node graphs and prompt
blocks that a writer or downstream model completes. Everything is derived from
the packed assets and from the spec, so changing ``--seed``, ``--genre``,
``--format`` or the target length visibly changes the result. No network calls,
no hidden state.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import importlib.util as _ilu

_HERE = os.path.dirname(os.path.abspath(__file__))
if "sns_core" in sys.modules:  # share one core instance across all tools- files
    core = sys.modules["sns_core"]
else:
    _SPEC = _ilu.spec_from_file_location("sns_core", os.path.join(_HERE, "tools-narrative-core.py"))
    core = _ilu.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(core)
    sys.modules["sns_core"] = core

ANGLES = [
    ("inversion", "Inversion",
     "Give the genre's expected power to the character least allowed to hold it."),
    ("constraint", "Constraint",
     "Remove the resource everyone in this genre relies on, and keep everything else the same."),
    ("witness", "Witness",
     "Center the person whose job is to record events rather than decide them."),
    ("aftermath", "Aftermath",
     "Begin after the event the genre usually dramatises, in the wreckage of it."),
    ("bargain", "Bargain",
     "Make the price of solving the problem a betrayal of the protagonist's stated value."),
    ("double-life", "Double Life",
     "Split the protagonist between two institutions that each demand full loyalty."),
    ("custody", "Custody",
     "Put the contested thing - a person, a rule, a record - in the protagonist's hands and make everyone want it."),
]

ROLE_PLAN = [
    ("protagonist", "carries the thematic question and must choose wrongly before choosing well"),
    ("antagonist", "holds the strongest opposing answer and acts first"),
    ("ally", "offers the competence the protagonist lacks and pays for it"),
    ("foil", "wants the same thing by an unacceptable method"),
    ("mentor", "equips the protagonist incompletely and then becomes unavailable"),
    ("love-interest", "offers a competing future that the premise makes expensive"),
    ("supporting", "controls a resource the plot needs at the worst moment"),
]

WOUNDS = [
    "They were believed over someone who was right, and the cost landed on that person.",
    "They kept a promise that should have been broken, and someone else paid the interest.",
    "They were protected from a consequence and have been paying interest on it since.",
    "They told the truth once and lost the only home available to them.",
    "They stayed silent in a room where speaking would have changed the outcome.",
    "They inherited an obligation that was never theirs to accept.",
    "They were the only one who survived, and the survival was not chosen.",
]

TACTICS = [
    "ask directly using the other person's own vocabulary",
    "trade a secret that is not theirs to trade",
    "invoke the rule everyone exploits and no one defends",
    "threaten the one material thing the other person cannot replace",
    "comply in public and refuse in private",
    "bring a witness the opponent cannot dismiss",
    "offer a smaller, sincere truth to protect the large one",
]

TURNS = [
    ("trust", "suspicion", "an ally reveals they already knew"),
    ("safe", "exposed", "the hiding place is named out loud"),
    ("in control", "out of time", "the deadline moves forward without warning"),
    ("certain", "wrong", "the evidence is genuine but supports the opposite claim"),
    ("alone", "responsible", "someone else's failure becomes the protagonist's problem"),
    ("hopeful", "indebted", "help is accepted with a price attached"),
]

FACE = {
    "children": ("(5-8)", (6, 10)),
    "middle-grade": ("(8-12)", (11, 13)),
    "ya": ("(13-18)", (15, 18)),
    "adult": ("(adult)", (26, 54)),
    "general": ("(all ages)", (20, 60)),
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def get_structures():
    return core.load_asset("structures")


def get_genres():
    return core.load_asset("genres")


def get_catalog():
    return core.load_asset("catalog")


def get_prompts():
    return core.load_asset("prompts")


def resolve_format(fmt, allow_custom=False):
    formats = get_structures()["formats"]
    key = core.require_choice(fmt, "format", list(formats.keys()), default=None, allow_custom=True)
    if key in formats:
        return key, formats[key], None
    if not allow_custom:
        raise core.StudioError(
            "format %r has no preset." % key, field="format",
            hint="Use a listed format, or add --allow-custom to run with generic budgets.",
        )
    return key, {
        "label": key.replace("-", " ").title(),
        "default_words": 20000,
        "avg_scene_words": 1500,
        "dialogue_ratio": 0.30,
        "max_pov": 2,
        "structure": "three-act",
        "chapter_words": 3000,
        "deliverables": ["brief", "premise", "outline", "scene cards", "audit"],
        "_custom": True,
    }, "Format %r has no preset; generic production budgets were applied." % key


def resolve_genre(gid, allow_custom=False):
    genres = get_genres()["genres"]
    key = core.require_choice(gid, "genre", list(genres.keys()), default=None, allow_custom=True)
    if key in genres:
        return key, genres[key], None
    if not allow_custom:
        raise core.StudioError(
            "genre %r has no preset." % key, field="genre",
            hint="Use a listed genre, or add --allow-custom to run with generic genre envelopes.",
        )
    blank = {
        "label": key.replace("-", " ").title(),
        "conventions": [], "reader_promise": "", "pitfalls": [], "motifs": [],
        "sensory_palette": [], "conflicts": [], "tone_words": [],
        "name_pool": {"first": ["Mara", "Tobias", "Ines", "Kofi", "Yuna", "Dov", "Rani", "Per"],
                      "surname": ["Vale", "Okoro", "Brandt", "Nakamura", "Reyes", "Haddad", "Lund"]},
        "required_systems": [], "content_notes": "",
        "roles": ["clerk", "courier", "nurse", "teacher", "mechanic", "archivist"],
        "settings": ["a town with one road out", "a building everyone must pass through",
                     "a workplace on the night shift", "a border between two obligations"],
        "pressures": ["a deadline nobody chose", "a debt that comes due this week",
                      "a decision that cannot wait for better information"],
        "antagonist_answers": ["the cost is justified by the outcome",
                               "someone must decide, and it will not be you",
                               "the old arrangement was kinder than the new one"],
    }
    return key, blank, "Genre %r has no preset; generic genre envelopes were applied." % key


def resolve_audience(aid, allow_custom=False):
    audiences = get_structures()["audiences"]
    key = core.require_choice(aid, "audience", list(audiences.keys()), default=None, allow_custom=True)
    if key in audiences:
        return key, audiences[key], None
    if not allow_custom:
        raise core.StudioError(
            "audience %r has no preset." % key, field="audience",
            hint="Use children, middle-grade, ya, adult or general, or add --allow-custom.",
        )
    return key, {
        "label": key.replace("-", " ").title(),
        "reading_level": "Declared by the brief.",
        "content": "Declared by the brief.",
        "pov_limit": "Declared by the brief.",
        "length_note": "Declared by the brief.",
    }, "Audience %r has no preset; the brief must state its own envelope." % key


def resolve_structure(name, format_key, warns, allow_custom=False):
    presets = get_structures()["structures"]
    if name:
        key = core.require_choice(name, "structure", list(presets.keys()), allow_custom=True)
        if key in presets:
            return key, presets[key]
        if not allow_custom:
            raise core.StudioError(
                "structure %r has no preset." % key, field="structure",
                hint="Use a listed structure, or add --allow-custom to fall back to three-act.",
            )
        warns.append("Structure %r has no preset; three-act layout was used." % key)
    default = get_structures()["formats"].get(format_key, {}).get("structure", "three-act")
    if default not in presets:
        default = "three-act"
    return default, presets[default]


def spec_from_args(args, warnings=None):
    """Normalize CLI/API arguments into a validated spec dict."""
    warnings = warnings if warnings is not None else []
    if not getattr(args, "format", None):
        raise core.StudioError("--format is required.", field="format",
                               hint="Try --format novel, short-story, screenplay, comic, visual-novel or game.")
    if not getattr(args, "genre", None):
        raise core.StudioError("--genre is required.", field="genre",
                               hint="Try --genre fantasy, mystery, literary or cyberpunk.")
    allow_custom = bool(getattr(args, "allow_custom", False))
    format_key, fmt, w = resolve_format(args.format, allow_custom)
    if w:
        warnings.append(w)
    genre_key, genre, w = resolve_genre(args.genre, allow_custom)
    if w:
        warnings.append(w)
    audience_key, audience, w = resolve_audience(getattr(args, "audience", None) or "general", allow_custom)
    if w:
        warnings.append(w)
    structure_key, structure = resolve_structure(
        getattr(args, "structure", None), format_key, warnings, allow_custom)

    target = getattr(args, "words", None)
    if target is None:
        target = fmt["default_words"]
    target = core.require_int(target, "words", 100, core.LIMITS["max_words"])
    seed = getattr(args, "seed", None)
    seed = core.require_int(seed, "seed", 0, 2_000_000_000, default=20260101)

    spec = {
        "title": core.require_text(getattr(args, "title", None), "title", min_len=1,
                                  max_len=160, default="Untitled Narrative Project"),
        "format": format_key,
        "format_label": fmt["label"],
        "genre": genre_key,
        "genre_label": genre["label"],
        "audience": audience_key,
        "audience_key": audience_key,
        "audience_label": audience["label"],
        "structure": structure_key,
        "structure_label": structure["label"],
        "target_words": target,
        "beats": getattr(args, "beats", None),
        "scenes": getattr(args, "scenes", None),
        "cast": getattr(args, "cast", None),
        "pages": getattr(args, "pages", None),
        "nodes": getattr(args, "nodes", None),
        "routes": getattr(args, "routes", None),
        "themes": core.parse_list(getattr(args, "themes", None), "themes"),
        "non_negotiables": core.parse_list(getattr(args, "non_negotiables", None), "non_negotiables"),
        "boundaries": core.parse_list(getattr(args, "boundaries", None), "boundaries"),
        "style": getattr(args, "style", None) or "plain",
        "platform": getattr(args, "platform", None) or "",
        "deadline": getattr(args, "deadline", None) or "",
        "seed": seed,
        "format_preset": fmt,
        "genre_preset": genre,
        "audience_preset": audience,
        "structure_preset": structure,
    }
    validate_spec(spec)
    return spec, warnings


def validate_spec(spec):
    if len(spec["title"]) > 160:
        raise core.StudioError("--title must be 160 characters or fewer.", field="title")
    styles = [v["id"] for v in get_prompts()["style_voices"]]
    if spec["style"] not in styles:
        raise core.StudioError(
            "--style=%r is not available. Choose one of: %s" % (spec["style"], ", ".join(styles)),
            field="style",
        )
    for field in ("beats", "scenes", "cast", "pages", "nodes", "routes"):
        value = spec.get(field)
        if value is not None:
            spec[field] = core.require_int(value, field, 1, core.LIMITS["max_items"])
    return spec


def thematic_question(spec):
    themes = spec["themes"]
    conflicts = spec["genre_preset"]["conflicts"] or ["duty against desire"]
    if themes:
        primary = themes[0]
        secondary = themes[1] if len(themes) > 1 else None
        question = "What does it cost to hold %s when %s is the only way forward?" % (
            primary.lower(), secondary.lower() if secondary else "the safer choice"
        )
    else:
        conflict = conflicts[0]
        first, _, second = conflict.partition(" against ")
        question = "Which matters more, %s or %s - and what does choosing one cost the other?" % (
            first, second or "the safer choice")
    return question


def logline(spec, rng, angle=None):
    genre = spec["genre_preset"]
    name = rng.choice(genre["name_pool"]["first"])
    surname = rng.choice(genre["name_pool"]["surname"])
    role = rng.choice(genre["roles"])
    setting = rng.choice(genre["settings"])
    pressure = rng.choice(genre["pressures"])
    conflict = rng.choice(genre["conflicts"])
    protagonist = spec.get("forced_protagonist") or "%s %s" % (name, surname)
    first, _, second = conflict.partition(" against ")
    line = (
        "%s, a %s in %s, must act when %s, but every available method turns %s "
        "into a betrayal of %s."
        % (protagonist, role, setting, pressure, first, second or "the person they are protecting")
    )
    if angle:
        line += " (%s lens: %s)" % (angle[1], angle[2])
    return protagonist, line


def _lower_first(sentence):
    if not sentence:
        return sentence
    if sentence[:2].isupper() and sentence[:2] not in ("I ", "I'"):
        return sentence[0].lower() + sentence[1:]
    return sentence



# --------------------------------------------------------------------------
# Premise
# --------------------------------------------------------------------------
def build_premise(spec, count=3):
    rng = core.seeded_rng(spec["seed"], spec["title"], spec["genre"], "premise")
    genre = spec["genre_preset"]
    question = thematic_question(spec)
    angles = rng.sample(ANGLES, k=min(count, len(ANGLES)))
    directions = []
    for i, angle in enumerate(angles, 1):
        protagonist, line = logline(spec, rng, angle)
        conflict = rng.choice(genre["conflicts"])
        _, _, second = conflict.partition(" against ")
        second = second or "the safer choice"
        motif = rng.choice(genre["motifs"]) if genre["motifs"] else "light"
        sensory = rng.choice(genre["sensory_palette"]) if genre["sensory_palette"] else "cold air"
        wound = rng.choice(WOUNDS)
        tactic = rng.choice(TACTICS)
        turn = rng.choice(TURNS)
        opponent_answer = rng.choice(genre["antagonist_answers"]) if genre["antagonist_answers"] else "the cost is justified by the outcome"
        directions.append({
            "index": i,
            "angle_id": angle[0],
            "angle": angle[1],
            "angle_move": angle[2],
            "protagonist": protagonist,
            "logline": line,
            "opening_image": "First image: %s. In the background sits the %s that will decide everything, unexplained." % (
                sensory, motif),
            "central_pressure": "%s. The opposition answers the thematic question with: \"%s\"." % (
                rng.choice(genre["pressures"]).capitalize().rstrip("."), opponent_answer),
            "lie_believed": "They behave as though %s is a law of nature, because %s" % (
                second, _lower_first(wound)),
            "climax_cost": "To win, the protagonist must %s - publicly, in front of a witness who cannot be bought." % tactic,
            "structural_risk": "%s - %s" % (
                angle[2],
                "this narrows commercial appeal" if angle[0] in ("witness", "aftermath") else "this can flatten the middle if the opposition has no initiative",
            ),
            "turn_type": "%s to %s (%s)" % turn,
            "thematic_question": question,
            "differentiation": "The premise binds the %s envelope to a %s budget and refuses the genre's default resolution. Check it against these conventions: %s" % (
                genre["label"], spec["format_label"],
                "; ".join(genre["conventions"][:2]) or "no preset conventions",
            ),
        })

    premise = {
        "title": spec["title"],
        "thematic_question": question,
        "reader_promise": genre["reader_promise"] or "A promise the brief must state explicitly.",
        "promise_of_premise": "The scenes the audience was sold by the logline: %s" % (
            ", ".join(genre["motifs"][:3]) or "define three concrete set pieces"),
        "genre_pitfalls": genre["pitfalls"],
        "audience": spec["audience_preset"]["label"],
        "format": spec["format_label"],
        "seed": spec["seed"],
        "directions": directions,
        "selected": directions[0]["index"],
    }
    return premise, render_premise_md(premise, spec), []


def render_premise_md(premise, spec):
    prompts = get_prompts()
    tpl = prompts["templates"]["premise_core"]["body"]
    out = ["# Premise Pack - %s" % premise["title"], ""]
    out.append("*%s | %s | %s | %s words | structure: %s*" % (
        spec["format_label"], spec["genre_label"], spec["audience_label"],
        format(spec["target_words"], ","), spec["structure_label"]))
    out.append("")
    out.append("## Thematic question")
    out.append("")
    out.append(premise["thematic_question"])
    out.append("")
    out.append("## Reader promise")
    out.append("")
    out.append(premise["reader_promise"])
    out.append("")
    out.append("## Promise of the premise")
    out.append("")
    out.append(premise["promise_of_premise"])
    out.append("")
    out.append("## Directions")
    for d in premise["directions"]:
        out.append("")
        out.append("### %d. %s" % (d["index"], d["angle"]))
        out.append("")
        out.append("- **Move:** %s" % d["angle_move"])
        out.append("- **Logline:** %s" % d["logline"])
        out.append("- **Opening image:** %s" % d["opening_image"])
        out.append("- **Central pressure:** %s" % d["central_pressure"])
        out.append("- **Lie believed:** %s" % d["lie_believed"])
        out.append("- **Climax cost:** %s" % d["climax_cost"])
        out.append("- **Structural risk:** %s" % d["structural_risk"])
        out.append("- **Differentiation check:** %s" % d["differentiation"])
    out.append("")
    out.append("## Genre obligations")
    out.append("")
    for item in spec["genre_preset"]["conventions"] or ["No preset conventions; state them in the brief."]:
        out.append("- %s" % item)
    out.append("")
    out.append("## Pitfalls to design out")
    out.append("")
    for item in premise["genre_pitfalls"] or ["No preset pitfalls recorded."]:
        out.append("- %s" % item)
    out.append("")
    out.append("## Writing prompt block")
    out.append("")
    out.append("```text")
    out.append(core.render_template(tpl, {
        "role": prompts["roles"]["showrunner"],
        "title": premise["title"],
        "format": spec["format_label"],
        "genre": spec["genre_label"],
        "audience": spec["audience_label"],
        "thematic_question": premise["thematic_question"],
    }))
    out.append("")
    out.append("Pick one direction and record the choice in the project bundle before outlining.")
    out.append("```")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Brief
# --------------------------------------------------------------------------
def build_brief(spec):
    genre, fmt, aud = spec["genre_preset"], spec["format_preset"], spec["audience_preset"]
    question = thematic_question(spec)
    answers = list(genre["antagonist_answers"]) or []
    while len(answers) < 3:
        answers.append("the brief must supply a competing answer %d" % (len(answers) + 1))
    deliverables = []
    for item in fmt["deliverables"]:
        deliverables.append(item)
    emotional = "Register: %s. The feeling is earned through %s and never stated in dialogue." % (
        ", ".join(genre["tone_words"][:3]) or "the register the brief must name",
        " and ".join((genre["conflicts"] or ["a costly choice"])[:2]),
    )
    brief = {
        "title": spec["title"],
        "format": spec["format_label"],
        "format_key": spec["format"],
        "genre": spec["genre_label"],
        "audience": spec["audience_label"],
        "audience_key": spec["audience"],
        "target_words": spec["target_words"],
        "structure": spec["structure_label"],
        "platform": spec["platform"],
        "deadline": spec["deadline"],
        "emotional_promise": emotional,
        "thematic_question": question,
        "answers": answers[:3],
        "reading_level": aud["reading_level"],
        "content_limits": aud["content"] + ((" Genre note: " + genre["content_notes"]) if genre["content_notes"] else ""),
        "pov_limit": aud["pov_limit"],
        "length_note": aud["length_note"],
        "language_policy": "Consistent register: %s. Profanity and dialect policy must be stated here before drafting." % (
            next((v["guidance"] for v in get_prompts()["style_voices"] if v["id"] == spec["style"]),
                 "plain").rstrip("."),
        ),
        "non_negotiables": list(dict.fromkeys(spec["non_negotiables"] + [
            "Deliver the emotional promise before the final image.",
            "Every major character answers the thematic question differently.",
            "No unresolved promise to the reader at the end.",
        ])),
        "boundaries": spec["boundaries"] or [
            "Do not reproduce plots, prose, or named characters from existing works.",
            "Do not present invention as documented fact.",
            "Do not exceed the audience content envelope.",
        ],
        "deliverables": deliverables,
        "originality_note": "Name the three closest existing works and write one sentence each on the specific difference in premise, structure, and voice. Unlisted similarity is a risk, not a defence.",
        "open_decisions": [
            "Which premise direction is selected, and why the others are rejected.",
            "The single irreversible choice at the climax and who witnesses it.",
            "The chapter, page, or node unit that the production plan will schedule.",
        ],
        "production_budget": {
            "scenes_estimated": max(1, round(spec["target_words"] / max(1, fmt["avg_scene_words"]))),
            "chapters_estimated": max(1, round(spec["target_words"] / max(1, fmt["chapter_words"]))),
            "avg_scene_words": fmt["avg_scene_words"],
            "chapter_words": fmt["chapter_words"],
            "dialogue_ratio_target": fmt["dialogue_ratio"],
            "max_pov": fmt["max_pov"],
            "seed": spec["seed"],
        },
    }
    md_template = core.load_asset("brief_template")
    rendered = core.render_template(md_template, {
        "title": brief["title"],
        "format": brief["format"],
        "genre": brief["genre"],
        "audience": brief["audience"],
        "target_words": format(brief["target_words"], ","),
        "structure": brief["structure"],
        "platform": brief["platform"] or "not stated - record it before production",
        "deadline": brief["deadline"] or "not stated - record milestone dates",
        "emotional_promise": brief["emotional_promise"],
        "thematic_question": brief["thematic_question"],
        "answer_1": brief["answers"][0],
        "answer_2": brief["answers"][1],
        "answer_3": brief["answers"][2],
        "reading_level": brief["reading_level"],
        "content_limits": brief["content_limits"],
        "pov_limit": brief["pov_limit"],
        "language_policy": brief["language_policy"],
        "non_negotiables": "\n".join("- " + x for x in brief["non_negotiables"]),
        "boundaries": "\n".join("- " + x for x in brief["boundaries"]),
        "deliverables": "\n".join("- " + x for x in brief["deliverables"]),
        "originality_note": brief["originality_note"],
        "open_1": brief["open_decisions"][0],
        "open_2": brief["open_decisions"][1],
        "open_3": brief["open_decisions"][2],
    })
    budget = brief["production_budget"]
    footer = [
        "",
        "## Production budget",
        "",
        "| Item | Value |",
        "| --- | --- |",
        "| Estimated scenes | %s |" % budget["scenes_estimated"],
        "| Estimated chapters / units | %s |" % budget["chapters_estimated"],
        "| Average scene length | %s words |" % format(budget["avg_scene_words"], ","),
        "| Chapter length target | %s words |" % format(budget["chapter_words"], ","),
        "| Dialogue ratio target | %.0f%% |" % (budget["dialogue_ratio_target"] * 100),
        "| POV ceiling | %s |" % budget["max_pov"],
        "| Audience length envelope | %s |" % brief["length_note"],
        "| Format preset budget | %s words |" % format(spec["format_preset"]["default_words"], ","),
        "",
        "## Brief interview prompts",
        "",
        get_prompts()["templates"]["brief_questions"]["body"],
        "",
    ]
    return brief, rendered.rstrip() + "\n" + "\n".join(footer), []


# --------------------------------------------------------------------------
# Outline
# --------------------------------------------------------------------------
def _beat_targets(spec, structure, beat_count):
    beats = structure["beats"]
    if beat_count == len(beats):
        return beats
    rng = core.seeded_rng(spec["seed"], "beats", beat_count)
    chosen = []
    if beat_count < len(beats):
        keep = sorted(set([0, len(beats) - 1] + rng.sample(range(1, len(beats) - 1), beat_count - 2)))
        chosen = [beats[i] for i in keep]
    else:
        chosen = list(beats)
        extra = beat_count - len(beats)
        for i in range(extra):
            base = beats[1 + (i % max(1, len(beats) - 2))]
            clone = dict(base)
            clone["key"] = "%s-extra-%d" % (base["key"], i + 1)
            clone["name"] = "%s (escalation %d)" % (base["name"], i + 1)
            clone["turning_point"] = False
            chosen.append(clone)
        chosen.sort(key=lambda b: b["position"])
    return chosen


def build_outline(spec, scene_count=None):
    rng = core.seeded_rng(spec["seed"], spec["title"], "outline")
    structure = spec["structure_preset"]
    fmt = spec["format_preset"]
    genre = spec["genre_preset"]
    beat_count = spec["beats"] or len(structure["beats"])
    beats = _beat_targets(spec, structure, beat_count)
    scene_count = scene_count or spec["scenes"] or max(
        len(beats), round(spec["target_words"] / max(1, fmt["avg_scene_words"])))
    scene_count = core.require_int(scene_count, "scenes", len(beats), core.LIMITS["max_items"])

    tension_total = sum(b["tension"] for b in beats)
    protagonist, _ = logline(spec, rng)
    antagonist_name = "%s %s" % (rng.choice(genre["name_pool"]["first"]), rng.choice(genre["name_pool"]["surname"]))
    allocated = 0
    rows = []
    for index, beat in enumerate(beats, 1):
        words = int(round(spec["target_words"] * (beat["tension"] / tension_total)))
        allocated += words
        scene_share = max(1, int(round(scene_count * (beat["tension"] / tension_total))))
        tension = beat["tension"]
        sensory = rng.choice(genre["sensory_palette"]) if genre["sensory_palette"] else "cold air"
        conflict = rng.choice(genre["conflicts"]) if genre["conflicts"] else "duty against desire"
        tactic = rng.choice(TACTICS)
        turn = rng.choice(TURNS)
        rows.append({
            "index": index,
            "key": beat["key"],
            "name": beat["name"],
            "act": beat["act"],
            "position": round(beat["position"] * 100, 1),
            "tension": tension,
            "turning_point": beat["turning_point"],
            "purpose": beat["purpose"],
            "target_words": words,
            "scenes": scene_share,
            "pov": protagonist if index % 3 else "%s (justify the switch)" % antagonist_name,
            "conflict": conflict,
            "tactic": tactic,
            "turn": "%s to %s - %s" % turn,
            "sensory_anchor": sensory,
            "cast_on_stage": [protagonist] if beat["act"] == 1 else [protagonist, antagonist_name],
            "cause": "Caused by the previous beat's choice; if it cannot be stated, the beat is a coincidence." if index > 1 else "Opens the story in a state the viewer can name.",
            "cost": "Someone loses something they cannot recover in this beat.",
            "setup_paid": "Name the earlier planted detail this beat pays." if beat["turning_point"] else "No payment due; plant rather than pay.",
            "theme_test": "Ask the thematic question in action: %s" % thematic_question(spec),
        })
    # distribute rounding drift into the climax
    drift = spec["target_words"] - allocated
    if rows:
        rows[-1]["target_words"] = max(200, rows[-1]["target_words"] + drift)
    acts = {}
    for row in rows:
        acts.setdefault(row["act"], {"words": 0, "scenes": 0, "beats": 0})
        acts[row["act"]]["words"] += row["target_words"]
        acts[row["act"]]["scenes"] += row["scenes"]
        acts[row["act"]]["beats"] += 1

    warnings = []
    total_scene_alloc = sum(r["scenes"] for r in rows)
    if total_scene_alloc != scene_count:
        warnings.append(
            "Scene allocation was normalised from %s to %s across %s beats; adjust per-beat counts by hand if exact parity matters."
            % (total_scene_alloc, scene_count, len(rows)))
    outline = {
        "title": spec["title"],
        "structure": spec["structure_label"],
        "structure_key": spec["structure"],
        "format": spec["format_label"],
        "genre": spec["genre_label"],
        "audience": spec["audience_label"],
        "target_words": spec["target_words"],
        "scene_count": total_scene_alloc,
        "seed": spec["seed"],
        "protagonist": protagonist,
        "antagonist": antagonist_name,
        "thematic_question": thematic_question(spec),
        "acts": acts,
        "beats": rows,
        "pacing_curve": [r["tension"] for r in rows],
    }
    return outline, render_outline_md(outline, spec), warnings


def render_outline_md(outline, spec):
    prompts = get_prompts()
    tpl = prompts["templates"]["outline_beat"]["body"]
    out = ["# Beat Sheet - %s" % outline["title"], ""]
    out.append("*%s | %s | %s | %s words | %s*" % (
        outline["format"], outline["genre"], outline["audience"],
        format(outline["target_words"], ","), outline["structure"]))
    out.append("")
    out.append("Protagonist: **%s**  |  Opposition: **%s**" % (outline["protagonist"], outline["antagonist"]))
    out.append("")
    out.append("Thematic question: %s" % outline["thematic_question"])
    out.append("")
    out.append("## Act budget")
    out.append("")
    out.append("| Act | Beats | Scenes | Words | Share |")
    out.append("| --- | --- | --- | --- | --- |")
    for act in sorted(outline["acts"]):
        data = outline["acts"][act]
        share = data["words"] / max(1, outline["target_words"])
        out.append("| %s | %s | %s | %s | %.0f%% |" % (
            act, data["beats"], data["scenes"], format(data["words"], ","), share * 100))
    out.append("")
    out.append("## Beats")
    for b in outline["beats"]:
        out.append("")
        out.append("### %d. %s%s" % (b["index"], b["name"], " (turning point)" if b["turning_point"] else ""))
        out.append("")
        out.append("| Field | Value |")
        out.append("| --- | --- |")
        out.append("| Act / position | %s / %s%% |" % (b["act"], b["position"]))
        out.append("| Tension target | %.2f |" % b["tension"])
        out.append("| Scene budget | %s %s, %s words |" % (
            b["scenes"], "scene" if b["scenes"] == 1 else "scenes", format(b["target_words"], ",")))
        out.append("| POV | %s |" % b["pov"])
        out.append("| Live conflict | %s |" % b["conflict"])
        out.append("| Tactics in play | %s |" % b["tactic"])
        out.append("| Value shift | %s |" % b["turn"])
        out.append("| Cast on stage | %s |" % ", ".join(b["cast_on_stage"]))
        out.append("| Sensory anchor | %s |" % b["sensory_anchor"])
        out.append("")
        out.append("**Function:** %s" % b["purpose"])
        out.append("")
        out.append("**Causal obligation:** %s" % b["cause"])
        out.append("")
        out.append("**Cost paid:** %s" % b["cost"])
        out.append("")
        out.append("**Setup paid:** %s" % b["setup_paid"])
        out.append("")
        out.append("<details><summary>Expansion prompt</summary>")
        out.append("")
        out.append("```text")
        out.append(core.render_template(tpl, {
            "beat_index": b["index"], "beat_name": b["name"],
            "position_percent": b["position"], "purpose": b["purpose"],
            "tension": b["tension"], "act": b["act"],
        }))
        out.append("```")
        out.append("")
        out.append("</details>")
    out.append("")
    out.append("## Tension curve")
    out.append("")
    out.append("```text")
    for b in outline["beats"]:
        bar = "#" * int(round(b["tension"] * 40))
        out.append("%-28s %s %.2f" % (b["name"][:28], bar, b["tension"]))
    out.append("```")
    out.append("")
    out.append("Diagnostic: the curve must rise across acts, flatten only for a deliberate rest, and peak once at the climax.")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Characters
# --------------------------------------------------------------------------
def build_characters(spec, count=None):
    rng = core.seeded_rng(spec["seed"], spec["title"], "characters")
    genre = spec["genre_preset"]
    fmt = spec["format_preset"]
    count = count or spec["cast"] or min(8, max(3, fmt["max_pov"] + 4))
    count = core.require_int(count, "cast", 1, 60)
    used = set()
    protagonist, _ = logline(spec, core.seeded_rng(spec["seed"], spec["title"], "premise"))
    protagonist = protagonist.split(",")[0]
    used.add(protagonist)

    used_firsts = {protagonist.split(" ")[0].lower()}

    def new_name():
        for _ in range(400):
            first = rng.choice(genre["name_pool"]["first"])
            if first.lower() in used_firsts:
                continue
            name = "%s %s" % (first, rng.choice(genre["name_pool"]["surname"]))
            if name not in used:
                used.add(name)
                used_firsts.add(first.lower())
                return name
        raise core.StudioError("Ran out of unique names in the genre pool; extend --cast or the name pool.",
                              code=core.EXIT_LIMIT, field="cast")

    conflicts = genre["conflicts"] or ["duty against desire"]
    answers = genre["antagonist_answers"] or ["the cost is justified by the outcome"]
    palette = genre["sensory_palette"] or ["cold air"]
    age_range = FACE.get(spec["audience_key"], ((20, 60)))[1]
    need_templates = [
        "to accept that %s cannot be bought with %s",
        "to admit that %s was never the thing standing in the way of %s",
        "to stop paying for %s with %s",
    ]
    chars = []
    for i in range(count):
        if i == 0:
            role, function = ROLE_PLAN[0]
            name = protagonist
            age = rng.randint(*age_range)
        elif i == 1 and spec.get("forced_antagonist"):
            role, function = ROLE_PLAN[1]
            name = spec["forced_antagonist"]
            used.add(name)
            age = rng.randint(*age_range)
        else:
            role, function = ROLE_PLAN[min(i, len(ROLE_PLAN) - 1)]
            name = new_name()
            age = rng.randint(*age_range)
        conflict_a, _, conflict_b = conflicts[i % len(conflicts)].partition(" against ")
        conflict_b = conflict_b or "the safer choice"
        want = "to hold on to %s and never say what %s costs" % (conflict_a, conflict_b)
        need = need_templates[i % len(need_templates)] % (
            conflict_b, rng.choice(["silence", "competence", "sacrifice", "loyalty", "endurance"]))
        char = {
            "id": core.slugify(name, "character-%d" % (i + 1)),
            "name": name,
            "role": role,
            "role_function": function,
            "age": age,
            "want": want,
            "need": need,
            "wound": rng.choice(WOUNDS),
            "lie": "They believe that %s is the only safe ground, and that choosing %s would prove the wound right." % (
                conflict_a, conflict_b),
            "competence": rng.choice([
                "reads a room before speaking", "keeps a ledger nobody else can follow",
                "fixes things under time pressure", "remembers exact wording",
                "can make an enemy feel understood", "moves quietly through institutions",
            ]),
            "incompetence": rng.choice([
                "cannot ask for help without offering payment",
                "misreads silence as agreement", "over-prepares and misses the moment",
                "confuses loyalty with obedience", "cannot lie about small things",
            ]),
            "voice": rng.choice([
                "short declaratives, questions instead of statements",
                "long qualified sentences that never quite commit",
                "trade vocabulary used as armour",
                "warm opener, cold closer",
                "numbers and nouns, no adjectives",
            ]),
            "sample_line": "\"It is not the %s that worries me. It is who noticed the %s.\"" % (
                rng.choice(genre["motifs"] or ["door"]), rng.choice(palette)),
            "gesture": rng.choice(["taps a ring once when lying", "counts exits", "rewrites lists",
                                   "holds a cup without drinking", "stands in doorways", "checks the time twice"]),
            "answer_to_theme": answers[i % len(answers)] if role in ("protagonist", "antagonist", "foil", "mentor") else "an answer the scene must reveal",
            "cost_choice": "The choice that would cost them most: %s" % rng.choice([
                "admit the debt is theirs", "let the record show the truth",
                "choose the stranger over the kin", "stay when leaving is survivable",
                "break a promise made to someone dead",
            ]),
        }
        chars.append(char)
    relationships = []
    for i, char in enumerate(chars[1:], 1):
        rel_type = ["owes", "protects", "distrusts", "was raised beside", "competes with", "loves"][i % 6]
        relationships.append({"from": chars[0]["id"], "to": char["id"], "type": rel_type,
                              "state": "unspoken obligation at scene one"})
    data = {
        "title": spec["title"],
        "cast": chars,
        "relationships": relationships,
        "cast_count": len(chars),
        "pov_ceiling": fmt["max_pov"],
        "seed": spec["seed"],
        "audience": spec["audience_label"],
    }
    warnings = []
    if len(chars) > 12 and fmt["max_pov"] <= 2:
        warnings.append("Cast of %s with a %s POV ceiling: assign off-page functions or expect screen-time dilution."
                        % (len(chars), fmt["max_pov"]))
    return data, render_characters_md(data, spec), warnings


def render_characters_md(data, spec):
    prompts = get_prompts()
    tpl = prompts["templates"]["character_card"]["body"]
    out = ["# Cast Sheet - %s" % data["title"], ""]
    out.append("*%s | %s | POV ceiling: %s*" % (spec["format_label"], spec["audience_label"], data["pov_ceiling"]))
    out.append("")
    out.append("| # | Name | Role | Age | Wants | Needs |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    for i, c in enumerate(data["cast"], 1):
        out.append("| %s | %s | %s | %s | %s | %s |" % (
            i, c["name"], c["role"], c["age"], c["want"], c["need"]))
    out.append("")
    out.append("## Relationship web")
    out.append("")
    for rel in data["relationships"]:
        out.append("- **%s** %s **%s** - %s" % (rel["from"], rel["type"], rel["to"], rel["state"]))
    out.append("")
    out.append("## Cards")
    for c in data["cast"]:
        out.append("")
        out.append("### %s - %s" % (c["name"], c["role"]))
        out.append("")
        out.append("- **Function:** %s" % c["role_function"])
        out.append("- **Want:** %s" % c["want"])
        out.append("- **Need:** %s" % c["need"])
        out.append("- **Wound:** %s" % c["wound"])
        out.append("- **Lie believed:** %s" % c["lie"])
        out.append("- **Competence:** %s" % c["competence"])
        out.append("- **Incompetence:** %s" % c["incompetence"])
        out.append("- **Voice:** %s" % c["voice"])
        out.append("- **Sample line:** %s" % c["sample_line"])
        out.append("- **Habitual gesture:** %s" % c["gesture"])
        out.append("- **Answer to the thematic question:** %s" % c["answer_to_theme"])
        out.append("- **Cost choice:** %s" % c["cost_choice"])
        out.append("")
        out.append("<details><summary>Card prompt</summary>")
        out.append("")
        out.append("```text")
        out.append(core.render_template(tpl, {
            "name": c["name"], "role": c["role"], "age": c["age"],
            "function": c["role_function"],
        }))
        out.append("```")
        out.append("")
        out.append("</details>")
    out.append("")
    out.append("## Distinctness test")
    out.append("")
    out.append("Remove every dialogue tag from one scene. If two speakers cannot be told apart, one of them needs a different tactic vocabulary, not a different accent.")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Scenes
# --------------------------------------------------------------------------
def build_scenes(spec, outline, cast=None):
    rng = core.seeded_rng(spec["seed"], spec["title"], "scenes")
    genre = spec["genre_preset"]
    palette = genre["sensory_palette"] or ["cold air"]
    protagonist = outline["protagonist"]
    antagonist = outline["antagonist"]
    cast_names = [c["name"] for c in (cast or {}).get("cast", [])] or [protagonist, antagonist]
    locations = genre["settings"] or ["a room with one door"]
    scenes = []
    number = 1
    for beat in outline["beats"]:
        for k in range(beat["scenes"]):
            tension = core.clamp(beat["tension"] + (0.05 if k else -0.02), 0.1, 1.0)
            pov = protagonist if (number % 3) else (cast_names[1] if len(cast_names) > 1 else protagonist)
            other = antagonist if pov == protagonist else protagonist
            turn = rng.choice(TURNS)
            tactics = rng.sample(TACTICS, k=3)
            target = max(150, int(round(beat["target_words"] / max(1, beat["scenes"]))))
            scenes.append({
                "scene_id": "sc-%03d" % number,
                "scene_number": number,
                "title": beat["name"] if beat["scenes"] == 1 else "%s (%d of %d)" % (
                    beat["name"], k + 1, beat["scenes"]),
                "beat": beat["name"],
                "act": beat["act"],
                "tension": round(tension, 2),
                "pov": pov,
                "location": rng.choice(locations),
                "time": rng.choice(["continuous with the previous scene", "later the same day",
                                    "the following morning", "two hours before the previous scene"]),
                "target_words": target,
                "goal": "%s wants %s from %s before the scene ends." % (
                    pov, rng.choice(["a name", "an admission", "a signature", "the ledger",
                                     "permission", "silence", "a promise", "the letter"]), other),
                "obstacle": "%s will not give it, because honouring %s would cost them %s." % (
                    other,
                    *(lambda c: (c.partition(" against ")[0], c.partition(" against ")[2] or "the thing they protect"))(
                        rng.choice(genre["conflicts"] or ["duty against desire"]))),
                "tactics": tactics,
                "turn": "%s to %s - %s" % turn,
                "consequence": "This scene forces %s in the next beat." % rng.choice([
                    "a public commitment", "a broken promise", "an alliance of convenience",
                    "a lie that must be maintained", "a departure", "a confession to the wrong person",
                ]),
                "sensory": rng.choice(palette),
                "knowledge_in": "Who knows what at entry: %s" % rng.choice([
                    "the protagonist knows the deadline but not the price",
                    "the opposition knows the price but not the deadline",
                    "both know the fact and neither knows the other knows",
                ]),
                "knowledge_out": "One knowledge state must change here; name it before drafting.",
                "cut_test": "If deleted: %s" % rng.choice([
                    "the climax loses its only witness",
                    "the opposition's plan becomes unmotivated",
                    "the protagonist's lie is never tested",
                    "the cost is never made concrete",
                ]),
                "dialogue_ratio": spec["format_preset"]["dialogue_ratio"],
                "status": "proposed",
            })
            number += 1
    data = {
        "title": spec["title"],
        "scene_count": len(scenes),
        "avg_target_words": round(sum(s["target_words"] for s in scenes) / max(1, len(scenes))),
        "total_target_words": sum(s["target_words"] for s in scenes),
        "target_words": spec["target_words"],
        "scenes": scenes,
        "seed": spec["seed"],
    }
    warnings = []
    if abs(data["total_target_words"] - spec["target_words"]) > spec["target_words"] * 0.1:
        warnings.append("Scene word budgets total %s against a %s target; rebalance before drafting."
                        % (format(data["total_target_words"], ","), format(spec["target_words"], ",")))
    return data, render_scenes_md(data, spec), warnings


def render_scenes_md(data, spec):
    prompts = get_prompts()
    tpl = prompts["templates"]["scene_card"]["body"]
    card = core.load_asset("scene_card_template")
    out = ["# Scene Cards - %s" % data["title"], ""]
    out.append("*%s scenes | %s words budgeted | %s target | %s*" % (
        data["scene_count"], format(data["total_target_words"], ","),
        format(data["target_words"], ","), spec["format_label"]))
    out.append("")
    out.append("| # | Beat | POV | Location | Tension | Words | Turn |")
    out.append("| --- | --- | --- | --- | --- | --- | --- |")
    for s in data["scenes"]:
        out.append("| %s | %s | %s | %s | %.2f | %s | %s |" % (
            s["scene_number"], s["beat"], s["pov"], s["location"], s["tension"],
            s["target_words"], s["turn"]))
    out.append("")
    out.append("Each card below is rendered from `asset-scene-card-template.md`; the YAML block is the "
               "editable record, the text below it is the drafting contract.")
    out.append("")
    for s in data["scenes"]:
        values = {
            "scene_id": s["scene_id"], "scene_number": s["scene_number"], "title": s["title"],
            "pov": s["pov"], "location": s["location"], "time": s["time"],
            "target_words": s["target_words"], "goal": s["goal"], "obstacle": s["obstacle"],
            "tactic_1": s["tactics"][0], "tactic_2": s["tactics"][1], "tactic_3": s["tactics"][2],
            "turn": s["turn"], "consequence": s["consequence"],
            "knowledge_in": s["knowledge_in"], "knowledge_out": s["knowledge_out"],
            "props": "inventory and injuries must match the previous card",
            "timeline": "%s, %s" % (s["beat"], s["time"]),
            "sensory": s["sensory"], "cut_test": s["cut_test"],
            "causality_note": "caused by the previous beat's choice; unstated causes are flagged in the audit",
            "agency_note": "POV chooses under pressure in the first third",
            "tension": "%.2f" % s["tension"], "pacing_note": "%.0f%% dialogue budget" % (s["dialogue_ratio"] * 100),
        }
        out.append("---")
        out.append("")
        out.append("## Scene %s - %s" % (s["scene_number"], s["title"]))
        out.append("")
        out.append(core.render_template(card, values).rstrip())
        out.append("")
        out.append("<details><summary>Drafting prompt</summary>")
        out.append("")
        out.append("```text")
        out.append(core.render_template(tpl, {
            "number": s["scene_number"], "title": s["title"], "pov": s["pov"],
            "location": s["location"], "time": s["time"], "target_words": s["target_words"],
            "goal": s["goal"], "obstacle": s["obstacle"],
            "tactics": "; ".join(s["tactics"]), "turn": s["turn"],
            "consequence": s["consequence"], "sensory": s["sensory"],
        }))
        out.append("```")
        out.append("")
        out.append("</details>")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Comic / sequential art
# --------------------------------------------------------------------------
def build_comic(spec, page_count=None):
    rng = core.seeded_rng(spec["seed"], spec["title"], "comic")
    structure = spec["structure_preset"]
    pages = page_count or spec["pages"] or 22
    pages = core.require_int(pages, "pages", 4, core.LIMITS["max_pages"])
    beats = structure["beats"]
    rows = []
    for page in range(1, pages + 1):
        position = page / pages
        nearest = min(beats, key=lambda b: abs(b["position"] - position))
        tension = core.clamp(nearest["tension"] + (position - nearest["position"]) * 0.4, 0.15, 1.0)
        panels = core.clamp(3 + int(round(tension * 4)) + (1 if page % 5 == 0 else 0), 2, 9)
        if page % 5 == 0 or nearest["turning_point"]:
            panels = core.clamp(panels - 3, 2, 9)
        words = int(round(core.clamp(52 - tension * 26, 8, 60)))
        rows.append({
            "page": page,
            "beat": nearest["name"],
            "beat_key": nearest["key"],
            "act": nearest["act"],
            "tension": round(tension, 2),
            "panels": panels,
            "splash": bool(nearest["turning_point"] and page % 5 == 0) or page == pages,
            "dialogue_words": words,
            "hook": "Page-turn hook: %s" % rng.choice([
                "a door opens onto the wrong room",
                "a name is said aloud that should not be known",
                "a hand closes on the object",
                "someone is already waiting",
                "the rule is broken in plain sight",
                "the camera finds the thing that moved",
            ]),
            "beat_purpose": nearest["purpose"],
        })
    for row in rows:
        if row["page"] == 1:
            row["hook"] = "Establishing spread: ordinary world, one unexplained detail held in the background."
    data = {
        "title": spec["title"],
        "pages": rows,
        "page_count": len(rows),
        "total_panels": sum(r["panels"] for r in rows),
        "total_dialogue_words": sum(r["dialogue_words"] for r in rows),
        "splashes": sum(1 for r in rows if r["splash"]),
        "lettering_budget_words": spec["format_preset"]["avg_scene_words"],
        "seed": spec["seed"],
        "format": spec["format_label"],
    }
    warnings = []
    if data["total_dialogue_words"] > 900:
        warnings.append("Lettering budget %s words is heavy for %s pages; trim per page."
                        % (data["total_dialogue_words"], pages))
    if data["total_panels"] / pages > 7:
        warnings.append("Average %s panels per page exceeds a sustainable monthly schedule."
                        % round(data["total_panels"] / pages, 1))
    return data, render_comic_md(data, spec), warnings


def render_comic_md(data, spec):
    prompts = get_prompts()
    tpl = prompts["templates"]["comic_page"]["body"]
    out = ["# Page Breakdown - %s" % data["title"], ""]
    out.append("*%s pages | %s panels | %s dialogue words | %s splashes | %s*" % (
        data["page_count"], data["total_panels"], data["total_dialogue_words"],
        data["splashes"], data["format"]))
    out.append("")
    out.append("| Page | Beat | Act | Panels | Splash | Dialogue words | Tension |")
    out.append("| --- | --- | --- | --- | --- | --- | --- |")
    for p in data["pages"]:
        out.append("| %s | %s | %s | %s | %s | %s | %.2f |" % (
            p["page"], p["beat"], p["act"], p["panels"],
            "yes" if p["splash"] else "-", p["dialogue_words"], p["tension"]))
    out.append("")
    out.append("## Page plans")
    for p in data["pages"]:
        out.append("")
        out.append("### Page %s - %s" % (p["page"], p["beat"]))
        out.append("")
        out.append("- **Panels:** %s%s" % (p["panels"], " (splash)" if p["splash"] else ""))
        out.append("- **Hook:** %s" % p["hook"])
        out.append("- **Contract bubble:** hold total dialogue to %s words" % p["dialogue_words"])
        out.append("- **Beat function:** %s" % p["beat_purpose"])
        out.append("")
        out.append("```text")
        out.append(core.render_template(tpl, {
            "page": p["page"], "title": p["beat"], "hook": p["hook"],
            "panel_count": p["panels"], "splash": "yes" if p["splash"] else "no",
            "bubble_budget": p["dialogue_words"],
        }))
        out.append("```")
    out.append("")
    out.append("## Thumbnail grid")
    out.append("")
    out.append("```text")
    for p in data["pages"]:
        out.append("p%02d %-34s panels %d%s" % (p["page"], p["beat"][:34], p["panels"],
                                                "  SPLASH" if p["splash"] else ""))
    out.append("```")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Game / visual novel
# --------------------------------------------------------------------------
def build_game(spec, node_count=None, routes=None):
    rng = core.seeded_rng(spec["seed"], spec["title"], "game")
    genre = spec["genre_preset"]
    node_count = node_count or spec["nodes"] or 14
    node_count = core.require_int(node_count, "nodes", 6, core.LIMITS["max_items"])
    routes = routes or spec["routes"] or 3
    routes = core.require_int(routes, "routes", 2, 12)
    if node_count < routes + 4:
        raise core.StudioError(
            "%s nodes cannot support %s routes plus a common route." % (node_count, routes),
            field="nodes", hint="Use at least routes + 4 nodes, or reduce --routes.",
        )
    cast = ["Ines", "Dov", "Rani", "Kofi", "Yuna", "Per", "Sena", "Bo"]
    route_names = ["route-%s" % core.slugify(cast[i % len(cast)]) for i in range(routes)]
    entering = [("met-%s" % cast[i % len(cast)].lower()) for i in range(routes)]
    ending_ids = ["n-%02d" % (node_count - routes + 1 + i) for i in range(routes)]
    first_ending = node_count - routes + 1

    nodes = []
    for i in range(1, node_count + 1):
        node_id = "n-%02d" % i
        if i == 1:
            node_type = "start"
        elif i >= first_ending:
            node_type = "ending"
        else:
            node_type = "choice"
        choices = []
        if node_type != "ending":
            if i >= first_ending - 1:
                # route-lock node: every ending must be reachable from here
                for j, ending in enumerate(ending_ids):
                    choices.append({
                        "id": "%s-c%d" % (node_id, j + 1),
                        "text": rng.choice([
                            "Choose %s and accept the debt that comes with them." % cast[j % len(cast)],
                            "Commit to %s's plan and sign for it." % cast[j % len(cast)],
                            "Follow %s out of the district before the tier drops." % cast[j % len(cast)],
                        ]),
                        "target": ending,
                        "sets": {"flags": [route_names[j]], "route": [route_names[j]]},
                        "requires": [entering[j]],
                        "consequence": "Locks the %s ending and closes the other routes." % route_names[j],
                    })
            else:
                for j in range(3 if i % 4 == 0 else 2):
                    target = "n-%02d" % min(first_ending - 1, i + 1 + (j % 2))
                    sets = []
                    if i == 2:
                        sets = [entering[j % routes]]
                    elif i == 3:
                        sets = [entering[(j + 1) % routes]]
                    else:
                        sets = [rng.choice(["debt+1", "trust+1", "witnessed"])]
                    choices.append({
                        "id": "%s-c%d" % (node_id, j + 1),
                        "text": rng.choice([
                            "Tell the truth about the %s." % rng.choice(genre["motifs"] or ["letter"]),
                            "Keep the %s and say nothing." % rng.choice(genre["motifs"] or ["letter"]),
                            "Ask the other person to decide.",
                            "Leave before the question is finished.",
                            "Pay the price in public.",
                        ]),
                        "target": target,
                        "sets": {"flags": sets, "route": []},
                        "requires": [] if i < 6 else [entering[j % routes]],
                        "consequence": rng.choice([
                            "closes one route and opens a new scene in the same location",
                            "adds a cost that must be paid by the midpoint",
                            "changes who trusts the player at the route lock",
                        ]),
                    })
        nodes.append({
            "id": node_id,
            "type": node_type,
            "title": rng.choice(["The %s" % rng.choice(genre["motifs"] or ["threshold"]),
                                 "Before the %s" % rng.choice(["hearing", "storm", "shift", "ferry"]),
                                 "What the %s Showed" % rng.choice(["record", "ledger", "photograph"])]),
            "act": 1 if i <= node_count * 0.3 else (2 if i <= node_count * 0.7 else 3),
            "situation": rng.choice(genre["pressures"] or ["a decision that cannot wait"]),
            "choices": choices,
            "ends_route": node_type == "ending",
        })

    # graph validation: reachability, dead ends, and flag satisfiability
    reachable = {"n-01"}
    flags_available = set()
    frontier = ["n-01"]
    by_id = {n["id"]: n for n in nodes}
    while frontier:
        current = frontier.pop()
        for choice in by_id[current]["choices"]:
            flags_available.update(choice["sets"]["flags"])
            if choice["target"] not in reachable:
                reachable.add(choice["target"])
                frontier.append(choice["target"])
    graph_warnings = []
    for node in nodes:
        if node["id"] not in reachable:
            graph_warnings.append("Node %s is unreachable from the start." % node["id"])
        if node["type"] == "choice" and not node["choices"]:
            graph_warnings.append("Node %s is an unintended dead end." % node["id"])
        if node["type"] == "ending" and not node["choices"]:
            continue
        for choice in node["choices"]:
            for required in choice["requires"]:
                if required not in flags_available:
                    graph_warnings.append(
                        "Choice %s requires flag %r that no earlier choice sets." % (choice["id"], required))
    unreachable_endings = [e for e in ending_ids if e not in reachable]
    if unreachable_endings:
        graph_warnings.append("Unreachable endings: %s." % ", ".join(unreachable_endings))

    data = {
        "title": spec["title"],
        "format": spec["format_label"],
        "nodes": nodes,
        "node_count": len(nodes),
        "routes": route_names,
        "endings": ending_ids,
        "flags": sorted(flags_available),
        "reachable": sorted(reachable),
        "seed": spec["seed"],
        "graph_warnings": graph_warnings,
    }
    return data, render_game_md(data, spec), graph_warnings


def render_game_md(data, spec):
    prompts = get_prompts()
    tpl = prompts["templates"]["game_node"]["body"]
    out = ["# Node Graph - %s" % data["title"], ""]
    out.append("*%s nodes | %s routes | %s endings | %s*" % (
        data["node_count"], len(data["routes"]), len(data["endings"]), data["format"]))
    out.append("")
    out.append("Routes: %s" % ", ".join(data["routes"]))
    out.append("")
    out.append("Endings: %s" % ", ".join(data["endings"]))
    out.append("")
    out.append("## Graph map")
    out.append("")
    out.append("```text")
    for node in data["nodes"]:
        if node["choices"]:
            targets = ", ".join("%s->%s" % (c["id"], c["target"]) for c in node["choices"])
            out.append("%s [%s] %s" % (node["id"], node["type"], targets))
        else:
            out.append("%s [%s] END" % (node["id"], node["type"]))
    out.append("```")
    out.append("")
    out.append("## Nodes")
    for node in data["nodes"]:
        out.append("")
        out.append("### %s - %s (%s)" % (node["id"], node["title"], node["type"]))
        out.append("")
        out.append("- **Act:** %s" % node["act"])
        out.append("- **Situation:** %s" % node["situation"])
        for c in node["choices"]:
            out.append("- **Choice %s:** %s -> %s (sets: %s; requires: %s) - %s" % (
                c["id"], c["text"], c["target"], c["sets"]["flags"] or "none",
                c["requires"] or "none", c["consequence"]))
        if not node["choices"]:
            out.append("- **Terminal node:** must close the route's emotional promise.")
        out.append("")
        out.append("```text")
        out.append(core.render_template(tpl, {
            "node_id": node["id"], "title": node["title"], "node_type": node["type"],
            "conditions": ", ".join(sorted({r for c in node["choices"] for r in c["requires"]})) or "none",
            "situation": node["situation"],
            "choices": "; ".join(c["text"] for c in node["choices"]) or "terminal",
            "flags_set": ", ".join(sorted({f for c in node["choices"] for f in c["sets"]["flags"]})) or "none",
            "flags_required": ", ".join(sorted({r for c in node["choices"] for r in c["requires"]})) or "none",
        }))
        out.append("```")
    out.append("")
    if data["graph_warnings"]:
        out.append("## Graph warnings")
        out.append("")
        for w in data["graph_warnings"]:
            out.append("- %s" % w)
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Export (from a project bundle)
# --------------------------------------------------------------------------
def build_export(project, kind, word_budget=None):
    prompts = get_prompts()
    outline = project.get("outline") or {}
    premise = project.get("premise") or {}
    brief = project.get("brief") or {}
    characters = project.get("characters") or {}
    title = project.get("title") or brief.get("title") or "Untitled Narrative Project"
    kind = core.require_choice(kind, "kind",
                               ["synopsis", "treatment", "style-sheet", "bible-summary", "pitch"])
    budget = core.require_int(word_budget, "word-budget", 50, 20000, default=500)
    warnings = []
    if not outline:
        warnings.append("Project has no outline; the export uses whatever sections exist.")

    beats = outline.get("beats", [])
    lines = ["# %s - %s" % (kind.replace("-", " ").title(), title), ""]
    if kind == "synopsis":
        lines.append("*Target length: %s words*" % budget)
        lines.append("")
        lines.append("**Premise.** %s" % (
            premise.get("directions", [{}])[0].get("logline", brief.get("emotional_promise", "Premise not yet generated."))))
        lines.append("")
        lines.append("**Thematic question.** %s" % (outline.get("thematic_question") or premise.get("thematic_question") or "Not stated."))
        lines.append("")
        lines.append("**Spine.**")
        lines.append("")
        for beat in beats:
            lines.append("- *%s:* %s" % (beat.get("name"), beat.get("purpose")))
        lines.append("")
        lines.append("**Cast.**")
        lines.append("")
        for c in characters.get("cast", [])[:8]:
            lines.append("- %s (%s): wants %s; needs %s." % (c["name"], c["role"], c["want"], c["need"]))
        lines.append("")
        lines.append("```text")
        lines.append(core.render_template(prompts["templates"]["export_synopsis"]["body"],
                                          {"word_budget": budget}))
        lines.append("```")
    elif kind == "treatment":
        lines.append("*Target length: %s words*" % budget)
        lines.append("")
        for act in sorted(outline.get("acts", {})):
            data = outline["acts"][act]
            lines.append("## Act %s - %s words, %s scenes" % (act, format(data["words"], ","), data["scenes"]))
            lines.append("")
            for beat in [b for b in beats if b.get("act") == act]:
                lines.append("- **%s:** %s Cost: %s" % (beat["name"], beat["purpose"], beat["cost"]))
            lines.append("")
        lines.append("```text")
        lines.append(core.render_template(prompts["templates"]["export_treatment"]["body"],
                                          {"word_budget": budget}))
        lines.append("```")
    elif kind == "style-sheet":
        lines.append("```text")
        lines.append(core.render_template(prompts["templates"]["export_style_sheet"]["body"], {}))
        lines.append("```")
        lines.append("")
        lines.append("## Register options")
        for voice in prompts["style_voices"]:
            lines.append("- **%s** - %s" % (voice["label"], voice["guidance"]))
    elif kind == "bible-summary":
        bible = project.get("bible") or {}
        if not bible:
            warnings.append("No bible stored in this project; the summary lists the required sections and the command to attach one.")
            lines.append("No continuity bible is attached to this project yet.")
            lines.append("")
            lines.append("Store one, then rerun this export:")
            lines.append("")
            lines.append("```sh")
            lines.append("python3 tools-story-narrative.py continuity --project <project>.json \\")
            lines.append("  --bible <bible>.json --input <manuscript>.md")
            lines.append("```")
            lines.append("")
            lines.append("A bible must validate against the packed schema and carry these sections:")
            lines.append("")
            for section, description in (
                ("characters", "identity, role, want, need, wound, relationships, knowledge states"),
                ("locations", "places with constraints the plot can use"),
                ("factions", "collective actors, goals, resources, oppositions"),
                ("rules", "world, magic or technology rules with a stated cost"),
                ("items", "objects that carry obligation, ownership or evidence"),
                ("timeline", "ordered events with location and participants"),
                ("threads", "questions opened and paid, with status"),
                ("sources", "claims with confidence and citation"),
                ("glossary", "invented terms, meaning and first use"),
            ):
                lines.append("- **%s** - %s" % (section, description))
            lines.append("")
        lines.append("Bible sections present: %s" % (
            ", ".join(k for k in bible.keys()) if bible else "none"))
        lines.append("")
        for key in ("characters", "locations", "factions", "rules", "threads", "sources"):
            items = bible.get(key) or []
            lines.append("## %s (%s)" % (key.title(), len(items)))
            for item in items[:20]:
                label = item.get("name") or item.get("statement") or item.get("question") or item.get("claim") or item.get("term") or item.get("id")
                lines.append("- %s" % label)
            lines.append("")
    else:  # pitch
        first = (premise.get("directions") or [{}])[0]
        lines.append("**Logline.** %s" % first.get("logline", "Not yet generated."))
        lines.append("")
        lines.append("**Format and length.** %s, %s words." % (brief.get("format", "unknown"), format(brief.get("target_words", 0), ",")))
        lines.append("")
        lines.append("**Audience.** %s" % brief.get("audience", "unknown"))
        lines.append("")
        lines.append("**Comparable position.** %s" % brief.get("originality_note", "Not stated."))
        lines.append("")
        lines.append("**Why now.** State the cultural or production reason in one sentence; no invented market data.")
    lines.append("")
    data = {"kind": kind, "title": title, "word_budget": budget}
    return data, "\n".join(lines), warnings
