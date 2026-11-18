# knowledge-readme.md — HumanSphere Holistic Support Skill Tool

Everything you need to install, run, extend, audit, and recover this Skill Tool.
This is the only knowledge file in the package; assets hold data, tool files hold code.

---

## 1. Purpose

`humansphere-holistic-support` turns the **HumanSphere Holistic Support Skill Catalog**
(18 categories, 178 support skills, extracted from `HumanSphere_Holistic_Support_Skill_Catalog.pdf`)
into a working, reusable capability component for a future Copilot skill.

It does five jobs end to end:

1. **Route** a stated human need to the *smallest useful set* of catalog skills, with an
   explainable reason for every pick and the governance check always attached.
2. **Plan** a safety-first support response using the catalog's six-step bundle
   (Listen → Clarify → Support → Protect → Act → Review) and a real, checkable artifact.
3. **Review** a drafted response against a data-driven governance policy: harmful advice,
   missed urgency, false authority, diagnosis claims, guarantees, coercion, dependency,
   stereotypes, unsourced high-stakes claims, minor-safety risks, and exposed identifiers.
4. **Escalate and hand off**: assess urgency, produce a facts-only handoff or referral
   summary with identifiers masked, and route to official source pointers.
5. **Personalise with consent**: an opt-in preference profile with correctable, deletable
   memory, tone/directness control, and identity used only as the user states it.

It also **ingests its own inputs**: a single capability, a numbered Skill Tool list, one
category, or several categories pasted as plain text, Markdown, or copied HTML is parsed
into a validated catalog asset that the rest of the tool can immediately use.

### Capability list (all reachable through the CLI)

| Capability | Command | Serves catalog areas |
| --- | --- | --- |
| Full capability list, grouped by category, with risk flags | `capabilities` | 1–18 |
| Skill listing / filtering by category or risk | `list` | 1–18 |
| Single-skill detail with operating prompts | `show` | 1–18 |
| Keyword search with scored matches | `search` | 1–18 |
| Smallest-set routing for a stated need | `route` | 178 (Catalog Router), 1–17 |
| Full support plan (Markdown artifact) | `plan` | 1–14, 150, 178 |
| Response safety review | `review` | 169–178 |
| Urgency assessment and escalation wording | `urgency` | 21–28, 174 |
| Identifier masking before sharing | `redact` | 175, 28 |
| Facts-only handoff / referral pack | `handoff` | 28, 158, 168 |
| Official-source resource lookup (offline registry) | `resources` | 161–167 |
| Governance summary + engine self-check | `governance` | 169–178 |
| Catalog extraction from text / Markdown / HTML | `parse-source` | input handling |
| Asset + policy validation | `validate` | all |
| Plan template placeholder check | `template-check` | 150, 178 |
| Consent-based preference memory | `profile init/show/set/remember/forget/validate` | 149–158 |
| Bundled test suite | `self-test` | quality |

The 18 categories covered: Emotional Support and Everyday Care; Mental Wellness and
Psychological Skills; Crisis, Safety, and High-Risk Support; Physical Wellness and Daily
Health Support; Financial Well-Being and Practical Money Support; Technology Support and
Digital Life; Spiritual, Meaning, and Values Support; Identity, Personality, and
Expression; Relationships, Family, and Social Support; Work, Study, and Life Direction;
Culture, Language, and Belonging; Neurodiversity and Accessibility Support; Creativity,
Play, and Personal Growth; Practical Daily Life Support; Specialized Human Support Roles;
Personalization, Memory, and Continuity; Professional Referral and Service Navigation;
Quality, Ethics, and Safety Governance.

The authoritative 178-skill list lives in `humansphere-holistic-support/asset-skill-catalog.json`
and is printed by the `capabilities` command; it is not duplicated here.

---

## 2. Files, roles, and load order

All files sit directly inside the Skill Tool folder. There are **12 files**: 6 `tools-`,
1 `knowledge-`, 5 `asset-`.

| File | Class | Role |
| --- | --- | --- |
| `tools-humansphere.py` | tool | **Primary entry point.** CLI, argument parsing, all commands, text/JSON rendering, exit codes, error handling. |
| `tools-catalog-engine.py` | tool | Shared library: catalog load/normalise/validate, tag and risk derivation, description→operating-prompt generation, search, smallest-set routing with deadline, atomic writes. |
| `tools-safety-engine.py` | tool | Urgency assessment, response review, PII detection/redaction, policy validation, governance summary, engine self-check. |
| `tools-plan-builder.py` | tool | Plan building and Markdown rendering through the template, support-mode and tone logic, preference-profile memory with consent gates, JSON Schema subset validator, resource selection, handoff packet building. |
| `tools-input-parser.py` | tool | Input handling: HTML→text normalisation, plain-text/Markdown/HTML extraction of categories, skill names, descriptions, numbering; single-capability fallback; category filtering. |
| `tools-self-test.py` | tool | Executable test suite (63 tests) over the engines and the real CLI. |
| `knowledge-readme.md` | knowledge | This file: setup, commands, schemas, safety rules, limits, tests, recovery, extension. |
| `asset-skill-catalog.json` | asset | The 178 skills in 18 categories with `id`, `name`, `slug`, `category`, `category_id`, `description`, `tags`, `risk`, `needs_human_referral`, `risk_reasons`. |
| `asset-safety-policy.json` | asset | All governance data: urgency signals/levels/actions/escalation wording, 11 response rules, 2 conditional rules, PII patterns, scope boundaries, approval gates, review checklist. |
| `asset-support-resources.json` | asset | 21 official-source pointers across `global`, `ph`, `us`, `uk`, `eu`, `au`, `ca`, `in`, with adopter rules. |
| `asset-preference-profile.schema.json` | asset | JSON Schema (draft-07 style, validated with a documented subset) for the preference profile. |
| `asset-plan-template.md` | asset | Editable Markdown plan template with 15 required `{{PLACEHOLDER}}` slots. |

**Load order (handled automatically, no setup step):**
`tools-humansphere.py` imports `tools-catalog-engine.py` → `tools-safety-engine.py` →
`tools-plan-builder.py` (which itself imports the catalog and safety engines) →
`tools-input-parser.py` (which imports the catalog engine). Modules register in
`sys.modules` under the names `hs_catalog_engine`, `hs_safety_engine`, `hs_plan_builder`,
`hs_input_parser`, so a module is executed once and exception classes stay identical.
Every default asset path is resolved **relative to the tool folder itself**
(`Path(__file__).parent`), so the tool runs correctly from any working directory and from
any parent skill root, with no absolute paths, environment variables, or install step.

---

## 3. Requirements and setup

* **Python 3.9 or newer** (developed and tested on CPython 3.13.16, Linux).
* **Standard library only.** No `pip install`, no third-party package, no virtualenv.
* No network access is used or required; no credentials, endpoints, or paid services.
* No build step: the files are the artifact.

Verify the runtime, then run the built-in checks:

```bash
# bash / zsh (POSIX)
python3 --version
python3 humansphere-holistic-support/tools-humansphere.py version
python3 humansphere-holistic-support/tools-humansphere.py validate
python3 humansphere-holistic-support/tools-humansphere.py self-test --quiet
```

```powershell
# PowerShell (Windows)
python --version
python .\humansphere-holistic-support\tools-humansphere.py version
python .\humansphere-holistic-support\tools-humansphere.py validate
python .\humansphere-holistic-support\tools-humansphere.py self-test --quiet
```

`validate` must print `Asset validation: PASS` and `self-test` must print `OK`.
If `python3` is not on the PATH, use `python` or the full interpreter path.

---

## 4. Quick start

```bash
# 1. See every capability
python3 humansphere-holistic-support/tools-humansphere.py capabilities

# 2. Route one stated need
python3 humansphere-holistic-support/tools-humansphere.py route \
  --query "I feel overwhelmed, my laptop is failing, and I am worried about school costs"

# 3. Produce a full support plan as a Markdown artifact
python3 humansphere-holistic-support/tools-humansphere.py plan \
  --query "I feel overwhelmed, my laptop is failing, and I am worried about school costs" \
  --region ph --out reports/support-plan.md

# 4. Review a drafted reply before sending it
python3 humansphere-holistic-support/tools-humansphere.py review \
  --input-file drafts/reply.md --user-file notes/user-message.md

# 5. Build a facts-only handoff summary
python3 humansphere-holistic-support/tools-humansphere.py handoff \
  --facts "dizzy since Tuesday; took my dose twice today" \
  --question "Is this dose safe?" --recipient "clinic triage nurse" \
  --out reports/handoff.md --approve
```

Add `--json` (global, or on any subcommand) for machine-readable output.

---

## 5. Command reference

Global options accepted **before** the command (and repeated on every subcommand for
convenience): `--json`, `--quiet`, `--catalog`, `--policy`, `--resources`, `--template`,
`--schema`, `--profile`, `--timeout`, `--max-skills`, `--version`.

| Global option | Default | Meaning |
| --- | --- | --- |
| `--catalog PATH` | `asset-skill-catalog.json` | Catalog asset to load. |
| `--policy PATH` | `asset-safety-policy.json` | Safety/governance policy. |
| `--resources PATH` | `asset-support-resources.json` | Official-source registry. |
| `--template PATH` | `asset-plan-template.md` | Plan template. |
| `--schema PATH` | `asset-preference-profile.schema.json` | Profile schema. |
| `--profile PATH` | `asset-preference-profile.json` | Profile file (created by `profile init`; optional). |
| `--timeout SECS` | `10` | Cooperative deadline for routing/planning; clamped to 0.05–300. |
| `--max-skills N` | `5` | Skills selected per route; clamped to 1–8. |

### 5.1 `capabilities`
Prints the full capability list grouped by category, with skill ids, risk level, and a
`[human referral]` flag. `--category NAME|ID` narrows it to one category.

```bash
python3 humansphere-holistic-support/tools-humansphere.py capabilities
python3 humansphere-holistic-support/tools-humansphere.py capabilities --category 3
python3 humansphere-holistic-support/tools-humansphere.py capabilities --json
```

### 5.2 `list`
Tabular skill list. Filters: `--category NAME|ID`, `--risk standard|elevated|high`.

```bash
python3 humansphere-holistic-support/tools-humansphere.py list --risk high
python3 humansphere-holistic-support/tools-humansphere.py list --category "Neurodiversity and Accessibility Support"
```

### 5.3 `show`
One skill by numeric id, slug, or name, with the operating prompts generated from its
description. Ambiguous names produce an `ambiguous_skill` error that tells you to use the id.

```bash
python3 humansphere-holistic-support/tools-humansphere.py show --skill 51
python3 humansphere-holistic-support/tools-humansphere.py show --skill crisis-handoff-coordinator
```

### 5.4 `search`
Scored keyword search. Name hits score 3.0, tag hits 1.5, description hits 0.5.
`--limit` defaults to 10, capped at 50.

```bash
python3 humansphere-holistic-support/tools-humansphere.py search --query "cannot sleep and worry about money" --limit 5
```

### 5.5 `route`
Selects the smallest useful set of skills for a stated need.

* Name-token hit 3.0, tag hit 1.5, description hit 0.5, whole-name phrase +4.0, plus a
  per-category keyword boost (+0.6 per matched category keyword) that only amplifies a
  skill which already has a real lexical match.
* **Category-only representative:** if a whole domain is relevant but no skill in it
  reuses the query's words (for example "rent" matching the financial category), exactly
  one representative skill from that category is added, with the reason
  `No skill wording matched directly; category keywords matched: rent.`
* **Role separation:** pass one allows at most **two skills per category**; pass two fills
  any remaining slots only if the need is concentrated in one domain. This prevents a
  5-skill pick from collapsing into five near-duplicates.
* `--max-skills` caps the selection (hard cap 8).
* `Skill 169 Support Response Safety Reviewer` is **pinned** to every selection unless
  `--no-governance` is passed, so scope/consent/escalation stays visible.
* If nothing matches, a deterministic fallback is returned and labelled `Fallback:`.
* Output explains every pick: matched terms, score, and 1–8 operating prompts.

```bash
python3 humansphere-holistic-support/tools-humansphere.py route --query "my mother keeps calling, I feel guilty" --max-skills 4
python3 humansphere-holistic-support/tools-humansphere.py route --query "panic before exams" --json
```

### 5.6 `plan`
Builds and renders the full support plan through `asset-plan-template.md`.

Options: `--query` (required), `--out PATH`, `--approve`, `--region CODE`,
`--max-skills`, `--timeout`, `--no-governance`.

The plan contains: stated need, detected support mode (listening / planning / research /
teaching / drafting / practice / escalation), urgency level and required actions,
selected-skill table, per-skill prompts, the six-step bundle with an `Escalate first` step
when urgency is detected, human-support options from the resource registry, consent and
boundary notes, a review checklist, and the limits of the plan.
The rendered Markdown is then run through the safety review; findings are reported on
stderr and the exit code follows the review verdict.

```bash
python3 humansphere-holistic-support/tools-humansphere.py plan \
  --query "I want to stop doomscrolling before bed" --region us --out plans/evening.md
python3 humansphere-holistic-support/tools-humansphere.py plan --query "I feel hopeless" --json
```

### 5.7 `review`
Reviews a drafted response. Inputs: `--text` or `--input-file` (exactly one);
optionally `--user-text` / `--user-file` to enable the missed-urgency rule.
`--out` writes the JSON report (needs `--approve` to overwrite).

```bash
python3 humansphere-holistic-support/tools-humansphere.py review \
  --text "I am your therapist, you only need me." --user-text "I feel hopeless"
# Verdict: FAIL, 4 block findings, exit code 2

python3 humansphere-holistic-support/tools-humansphere.py review \
  --text "I hear you. Please contact your local emergency number now and stay with someone safe." \
  --user-text "I want to kill myself"
# Verdict: PASS, exit code 0
```

### 5.8 `urgency`
Assesses the user's text and returns the level (`none` < `elevated` < `urgent` <
`critical`), matched signals with evidence, the required actions for that level, and
ready-to-use escalation wording.

```bash
python3 humansphere-holistic-support/tools-humansphere.py urgency --text "I have chest pain right now"
python3 humansphere-holistic-support/tools-humansphere.py urgency --input-file notes/message.txt --json
```

Exit code is 1 when human handoff is required (`urgent`/`critical`), otherwise 0.

### 5.9 `redact`
Masks e-mail addresses, card-like numbers, phone-like numbers, and national-ID-like
strings. `--out` writes the redacted text (needs `--approve` to overwrite).

```bash
python3 humansphere-holistic-support/tools-humansphere.py redact --text "write to someone@example.com"
```

### 5.10 `handoff`
Builds a facts-only summary for a clinician, hotline, guardian, or responder:
who (only consented/known fields), user-reported facts as bullets, urgency level,
questions for the recipient, documents to bring, access needs, masked identifiers,
limits, and a consent line. `--urgency-level` overrides the detected level (validated
against the policy). Writing the file requires `--approve` (export gate).

```bash
python3 humansphere-holistic-support/tools-humansphere.py handoff \
  --facts "chest tightness since 7am; no history" \
  --question "Do I need the emergency department?" --document "medication list" \
  --out reports/handoff.md --approve
```

### 5.11 `resources`
Queries the offline registry: `--region CODE`, `--kind KIND` (repeatable),
`--no-global`, `--limit` (default 8, max 20). Returns **official source pointers**, not a
live directory.

```bash
python3 humansphere-holistic-support/tools-humansphere.py resources --region ph --kind crisis
python3 humansphere-holistic-support/tools-humansphere.py resources --region us --kind shelter --kind food --json
```

### 5.12 `governance`
Prints the policy summary (urgency levels and required actions, response rules,
conditional rules, scope boundaries, approval gates, PII patterns, review checklist) and
runs the engine self-check. Exit code 2 if the self-check fails.

```bash
python3 humansphere-holistic-support/tools-humansphere.py governance
```

### 5.13 `parse-source`
Extracts categories, skill names, and descriptions from pasted or saved input and can
write a validated catalog asset. See §6 for the extraction rules.

Options: `--text` **or** `--input-file` (exactly one), `--format auto|text|markdown|md|html`,
`--category NAME` (apply to every extracted skill), `--keep-category NAME` (repeatable),
`--title`, `--out PATH`, `--approve`.

```bash
python3 humansphere-holistic-support/tools-humansphere.py parse-source \
  --input-file notes/new-skills.md --out assets/catalog-extra.json
python3 humansphere-holistic-support/tools-humansphere.py parse-source \
  --text "Wind-down routine designer for people who cannot stop scrolling at night"
python3 humansphere-holistic-support/tools-humansphere.py parse-source \
  --input-file page.html --keep-category "Accessibility" --json
```

### 5.14 `validate`
Checks every asset: catalog loads with the expected counts, policy structure and all
regular expressions compile, engine self-check passes, template has all 15 placeholders,
resource registry entries are well formed, schema accepts the default profile, and the
optional profile (if present) validates. Exit code 0 = PASS, 2 = FAIL.

```bash
python3 humansphere-holistic-support/tools-humansphere.py validate
python3 humansphere-holistic-support/tools-humansphere.py validate --json
```

### 5.15 `template-check`
Reports present, missing, and unknown `{{PLACEHOLDERS}}` in the plan template.

```bash
python3 humansphere-holistic-support/tools-humansphere.py template-check --template asset-plan-template.md
```

### 5.16 `self-test`
Runs the bundled 63-test suite and returns 0 on success, 2 on failure. `--quiet` gives
dot-style output.

```bash
python3 humansphere-holistic-support/tools-humansphere.py self-test --quiet
python3 humansphere-holistic-support/tools-self-test.py            # same suite, direct
```

### 5.17 `version`
Prints tool name, version, Python version, and entry-point filename.

### 5.18 `profile` family

```bash
# create (nothing is written without --consent)
python3 humansphere-holistic-support/tools-humansphere.py --profile prefs.json profile init --consent --region ph

# read
python3 humansphere-holistic-support/tools-humansphere.py --profile prefs.json profile show

# change preferences (needs --consent)
python3 humansphere-holistic-support/tools-humansphere.py --profile prefs.json profile set \
  --set preferences.tone gentle --set preferences.directness 4 \
  --set identity.name_used Mika --set identity.pronouns she/her \
  --set boundaries.never_do "call me honey, call me sweetie" --consent

# store one memory entry (needs --consent; same key is corrected, not duplicated)
python3 humansphere-holistic-support/tools-humansphere.py --profile prefs.json profile remember \
  --key goals.main --value "finish thesis" --consent

# delete one entry or all entries (needs --approve)
python3 humansphere-holistic-support/tools-humansphere.py --profile prefs.json profile forget --key goals.main --approve
python3 humansphere-holistic-support/tools-humansphere.py --profile prefs.json profile forget --all --approve

# validate against the schema
python3 humansphere-holistic-support/tools-humansphere.py --profile prefs.json profile validate
```

Value coercion in `profile set`: `true/false/yes/no/on/off` → boolean, integers → int,
`[..]`/`{..}` → JSON, comma-separated → list, anything else → string.

---

## 6. Input handling rules

`parse-source` accepts four input shapes and never requires the caller to pre-clean them.

1. **A single capability** (plain sentence). No structured markers are found, so the whole
   input becomes one skill: `mode: single-capability`, name = the first line (up to 90
   characters on a word boundary, hyphenation preserved), description = the full text.
2. **A numbered Skill Tool list** — `1. Skill Name: X` followed by `Description: Y`,
   with descriptions allowed to wrap across lines (hyphenated wraps are joined, so
   `CBT-` + `style` becomes `CBT-style`).
3. **One category** — the category name becomes the umbrella and all its skills are
   grouped under it (`--category` can supply the name when the input has no heading).
4. **Several categories** — every heading is captured in order; `--keep-category` keeps
   only the requested group(s).

Accepted syntax variants per skill: `Skill Name: X` / `Name: X` / `Skill: X` (with `-`,
`:` or an en/em dash), `**Bold Name** — description`, `1. Name — description`,
`- Name — description`, and Markdown/HTML headings (`# Digital Wellbeing`,
`<h2>1. Accessibility</h2>`) for categories. `Description:`, `Summary:`, and `Purpose:`
labels are all understood.

**Ignored automatically:** HTML tags, CSS classes, `data-*` attributes, `<script>`/`<style>`
blocks, comments, entity encodings, numbering marks, bullet glyphs, repeated 2+ spaces,
page headers/footers (`CATALOG | Page 4`, `===== PAGE 4 =====`), section noise lines
(`Focused skills for this part…`, `Catalog only…`), and decoration lines.

Category ids are assigned by first appearance **of a skill**, so decorative or empty
headings can never shift ids. Skill ids: the explicit number is kept when present and
unique; otherwise ids are reassigned sequentially so every id stays unique.

**Guards:** input is rejected if it is empty, unparseable, larger than 2 000 000 bytes, or
has an unsupported `--format`. Nothing is written unless `--out` is given, and an existing
`--out` file is refused without `--approve`.

---

## 7. The catalog asset

`asset-skill-catalog.json` structure:

```json
{
  "schema": "humansphere.skill-catalog/1",
  "title": "HumanSphere Holistic Support Skill Catalog",
  "source": "HumanSphere_Holistic_Support_Skill_Catalog.pdf (18 categories, 178 skills)",
  "counts": { "skills": 178, "categories": 18 },
  "categories": [ { "id": 1, "name": "Emotional Support and Everyday Care", "skill_count": 10 } ],
  "skills": [
    {
      "id": "1",
      "name": "Empathic Listening Companion",
      "slug": "empathic-listening-companion",
      "category": "Emotional Support and Everyday Care",
      "category_id": 1,
      "description": "Responds with warmth, reflects the user’s words, ...",
      "tags": ["empathic", "listen", "companion", "..."],
      "risk": "standard",
      "needs_human_referral": false,
      "risk_reasons": []
    }
  ]
}
```

Derivation rules (all deterministic, all reproducible from the description):

* **Tags** — normalised, stopword-filtered, lightly stemmed terms from name + description +
  category, capped at 24 per skill.
* **Risk** — `high` if the skill is in category 3 (Crisis, Safety, and High-Risk Support) or
  mentions self-harm, suicide, overdose, poison, abuse, crisis, emergency, danger,
  red-flag, withdrawal, harassment, or violence; `elevated` if it is in categories 5, 12,
  17, 18 or mentions medical, medication, diagnosis, clinician, prescriber, pharmacist,
  legal, financial, fraud, scam, debt, disability, minors, licensed, authority, hardship,
  guardian; otherwise `standard`.
* **needs_human_referral** — true for `high`/`elevated`, or when the description mentions
  licensed / professional / clinician / pharmacist / prescriber / authorities / navigator /
  referral / qualified.
* **Operating prompts** — the description's action clauses, split only at real action
  boundaries and converted to imperative form (`Responds with warmth, asks gentle
  questions` → `Respond with warmth`, `Ask gentle questions`). One to eight prompts per
  skill; no skill produces zero.

Catalog limits: 2 000 skills maximum per catalog, unique ids enforced, non-empty name
enforced, `catalog_empty` / `duplicate_skill_id` / `catalog_too_large` reported as
structured errors.

---

## 8. Safety and approval rules

### 8.1 Urgency levels (from `asset-safety-policy.json`)

| Level | Rank | Meaning | Effect |
| --- | --- | --- | --- |
| `none` | 0 | no urgency signal | plan still names one human option once, without alarm |
| `elevated` | 1 | significant distress | offer support plus one low-barrier human option; no diagnosis or promises |
| `urgent` | 2 | possible medical/personal emergency | direct to emergency care now; no home diagnosis |
| `critical` | 3 | immediate danger, self-harm, or violence risk | escalate to qualified human help without delay; distance from means; stay with someone safe |

Signals are grouped (`U-SELF-HARM`, `U-VIOLENCE`, `U-MEDICAL`, `U-WITHDRAWAL`,
`U-DISTRESS`) and each match returns the evidence text. Every level carries
`required_actions`, and levels 1–3 carry ready-to-use `escalation_text`.

### 8.2 Response rules (what `review` blocks or warns about)

| Rule | Severity | Blocks |
| --- | --- | --- |
| `R-DEPENDENCY` | block | exclusivity, isolation, secrecy ("you only need me", "don't talk to anyone else") |
| `R-ROMANTIC-CLAIM` | block | claimed real romantic/exclusive relationship |
| `R-FALSE-AUTHORITY` | block | claimed licence or professional role |
| `R-DIAGNOSIS` | block | assigning a diagnosis or clinical label |
| `R-GUARANTEE` | block | guaranteed outcomes, "100% safe", risk-free |
| `R-PRESSURE` | block | coercion, guilt, "do what I say" |
| `R-URGENCY-MISSED` | block | urgency in the user's text with no escalation in the draft |
| `R-STEREOTYPE` | warn | stereotyping or inferring protected identity |
| `R-UNSOURCED-HIGH-STAKES` | warn | law/medicine/money claims without a current official source |
| `R-ASYMMETRY-DECEPTION` | warn | unlabelled fiction presented as lived fact |
| `R-MINOR-RISK` | warn | secrecy from parents/guardians, unsupervised meetings |
| `R-PII-EXPOSURE` | warn | identifiers present in the output |

### 8.3 Scope boundaries (never crossed)
medical care · mental health treatment · legal advice · financial advice and
authorisation · religious authority · relationship status. Each boundary lists what is not
allowed, who to route to, and the safe alternative (organise facts, prepare questions,
psychoeducation, coping practice, appointment preparation).

### 8.4 Approval gates

| Gate | Trigger | Required flag |
| --- | --- | --- |
| `A-OVERWRITE` | overwrite an existing catalog, plan, profile, or export file | `--approve` |
| `A-MEMORY-DELETE` | delete a memory entry or profile (consent withdrawal) | `--approve` |
| `A-MEMORY-WRITE` | persist any preference memory | `--consent` |
| `A-EXPORT` | write a handoff / referral / escalation summary | `--approve` |
| `A-EXTERNAL-SEND` | send, publish, or transmit anything | **not implemented** — the tool performs no network or messaging operations at all |

Refusals print `BLOCKED [approval_required]` (or `consent_required`) with a hint and exit
code 3; nothing is written and no partial file is left behind.

### 8.5 Exit codes

| Code | Meaning |
| --- | --- |
| `0` | success / pass |
| `1` | completed with warnings (review warn findings; urgency requiring human handoff) |
| `2` | blocked by policy or invalid asset/verdict fail |
| `3` | approval or consent required |
| `4` | input error (missing/invalid file, conflicting flags, unknown key, bad category) |
| `5` | timeout (operation exceeded `--timeout`) |

---

## 9. Preference profile

The profile is **opt-in, correctable, and deletable**. Nothing is stored unless the command
includes `--consent`; overwrites and deletions need `--approve`.

Sections and allowed values (full schema in `asset-preference-profile.schema.json`):

* `consent` — `granted` (bool), `granted_at`, `scope` (list), `note`.
* `identity` — `name_used`, `pronouns`, `age_band` (`under-13` … `65-plus`, `not-stated`),
  `languages`, `roles`. Only user-stated values; never inferred.
* `preferences` — `tone` (gentle/neutral/direct/energetic/formal/playful),
  `directness` (1–5), `format` (brief/structured/detailed),
  `reading_level` (plain/standard/advanced), `humor` (none/gentle/dry/playful/absurd/witty),
  `examples` (bool), `max_list_items` (1–20).
* `accessibility` — `needs`, `sensory`, `reading`.
* `context` — `region`, `timezone`, `goals`, `limits`, `values`.
* `boundaries` — `never_do`, `topics_to_avoid`, `require_approval_for`.
* `persona` — optional fictional style: `enabled`, `label`, `voice`, `values`,
  `fiction_disclosure` (always / on-request / when-roleplay-starts). Style only; it can
  never assert a real identity, licence, or relationship.
* `memory` — `entries[]` of `{key, value, added_at, source}` with
  `source ∈ user-stated | user-corrected | user-imported`, capped at 200 entries.

Top-level keys are validated against a fixed set; unknown sections are rejected with the
list of valid keys. Schema validation uses a documented JSON Schema **subset** —
`type`, `required`, `properties`, `additionalProperties`, `enum`, `items`, `minimum`,
`maximum`, `minItems`, `maxItems`, `pattern`, `minLength`, `maxLength` — implemented in
`tools-plan-builder.py` with no third-party dependency. Keywords outside that subset
(for example `$ref`, `oneOf`, `format`) are ignored by design.

The plan consumes the profile for tone/directness/format directives, personalization notes
(name, pronouns, languages, access needs, persona label), and boundary lines. With no
consent granted, the plan states that no memory is stored or applied and uses portable
defaults (neutral, directness 3/5, structured, plain language).

---

## 10. Plan template placeholders

`asset-plan-template.md` must keep all 15 slots; `template-check` and `validate` verify them.

| Placeholder | Content |
| --- | --- |
| `{{TITLE}}` | plan title with detected support mode |
| `{{QUERY}}` | the stated need |
| `{{SUPPORT_MODE}}` | mode + why it was chosen |
| `{{URGENCY_LEVEL}}` | `none`/`elevated`/`urgent`/`critical` |
| `{{URGENCY_LABEL}}` | human-readable level description |
| `{{PERSONALIZATION}}` | consent-aware personalization summary |
| `{{GENERATED_AT}}` | UTC timestamp |
| `{{SAFETY_SECTION}}` | urgency signals, required actions, escalation wording |
| `{{SKILLS_TABLE}}` | selected skills with risk and selection reason |
| `{{SKILL_DETAIL}}` | per-skill prompts as checkboxes |
| `{{BUNDLE_SECTION}}` | Listen → Clarify → Support → Protect → Act → Review |
| `{{RESOURCES}}` | official source pointers for the region |
| `{{PROFILE_SECTION}}` | tone directive, boundaries, memory statement |
| `{{REVIEW_SECTION}}` | success checks + policy checklist |
| `{{LIMITS}}` | what the plan cannot do |

Unknown placeholders are reported (not fatal); a missing placeholder is fatal
(`template_incomplete`) so a plan can never silently lose its safety section.

---

## 11. Resource registry

Fields per entry: `id`, `name`, `region` (`global`, `ph`, `us`, `uk`, `eu`, `au`, `ca`,
`in`), `kinds` (emergency, crisis, medical, poison, shelter, food, benefits, legal,
financial, school, workplace, community, faith, disaster, digital-safety),
`official_url`, `contact_hint`, `verify_note`.

**Deliberate design:** the registry ships **official source pointers**, not a verified
contact directory. `contact_hint` repeats only what the official page publishes (for
example the emergency number of that region) and always carries a `verify_note` telling the
reader to confirm on the official page first. Adopters are expected to add and correct
entries for their own region before using the tool with real users. When no entry matches,
the tool says so and instructs the assistant to look the service up *with* the user rather
than inventing one.

---

## 12. Limits and unavailable integrations

* **No network access.** No live source lookups, no URL fetching, no sending, publishing,
  messaging, account access, payments, or calendar writes. `R-UNSOURCED-HIGH-STAKES` and
  `Accessibility`-style checks therefore only flag unsourced claims; they cannot verify
  them. Live verification remains the assistant's or the user's job.
* **Not a semantic model.** Routing, search, and step generation are keyword/rule based and
  fully explainable. A semantically phrased need with no lexical overlap falls back to the
  clarification skills (`Fallback:` label) — widen `--max-skills`, add tags, or rephrase.
* **Not a clinician, lawyer, financial adviser, spiritual authority, or real companion.**
  It prepares, organises, and routes; it never diagnoses, prescribes, rules, authorises, or
  speaks for a tradition.
* **Policy rules are pattern based.** Novel phrasing can evade a regex; the review is a
  safety net, not a proof. Keep the human review gate for high-impact decisions.
* **Resource entries are pointers, not verified contact data** (see §11).
* **No PDF/DOCX reading.** The bundled catalog is already extracted; to import other
  PDFs/DOCX files, convert them to text/Markdown first and use `parse-source`. This keeps
  the tool dependency-free.
* **Telemetry, logging, and persistence beyond the profile file:** none. Nothing is written
  except the files you name with `--out` and the profile you create with `profile init`.
* **Timeout is cooperative.** The deadline is checked inside catalog scans; a single
  pathological regular expression supplied in a custom policy could still exceed it — use
  bounded patterns (the shipped policy does).

Bounded constants (`LIMITS` in `tools-catalog-engine.py`): max query 4 000 chars, max
source 2 000 000 bytes, max catalog 2 000 skills, max tags 24 per skill, max steps 8 per
skill, skills per route default 5 / hard cap 8, search results cap 50, profile memory
200 entries, timeout default 10 s (0.05–300 s).

---

## 13. Validation and tests actually completed

All of the following were executed on CPython **3.13.16** (Linux), standard library only.

**Automated suite — 63 tests, all passing.**
`python3 humansphere-holistic-support/tools-self-test.py` → `Ran 63 tests ... OK`
Also run through the CLI: `tools-humansphere.py self-test --quiet` → `OK`.

Coverage per area:

* **Catalog engine (17)** — 178 skills in 18 categories; category counts match membership;
  every skill has a name, a ≥20-character description, tags, and a valid risk level; id/slug/
  name lookup; not-found error; risk derivation for crisis vs listening skills; imperative
  step generation for all 178 skills (no empty steps, no third-person openings); search
  ranking and empty-query rejection; explainable routing of the sample need (five picks,
  at least three categories, a money skill and a listening skill present); governance pin
  on/off; deterministic fallback; query length guard;
  deadline raising `EngineTimeout`; empty-catalog and duplicate-id rejection; overwrite
  approval gate plus reload; category-diversity cap (≤2 per category), and the
  category-only representative surfacing the financial domain for a sleep-and-rent need.
* **Safety engine (12)** — policy validity and engine self-check; four urgency levels with
  the expected classification; required actions and escalation text; harmful draft blocked
  with three expected rule ids; missed-escalation detection; safe escalating draft passing;
  disease words not misread as diagnosis; empty input rejection; PII redaction and
  `R-PII-EXPOSURE`; scope boundaries, approval gates, and review checklist present; invalid
  policy rejected.
* **Plan builder (16)** — template completeness and missing-placeholder failure; default
  profile passes the schema; schema subset catches bad enum/range; consent gate and
  overwrite gate on save; dotted key get/set with unknown-key rejection; value coercion;
  memory add/correct/forget; support-mode classification for six inputs; tone directive
  from preferences; full plan render with all eight sections and no leftover `{{`; crisis
  plan escalates first and passes review; region filtering; handoff facts-only + redaction
  + urgency override validation; export approval gate.
* **Input parser (8)** — plain text, two-category Markdown, copied HTML with classes/`data-*`
  stripped, page-noise and hyphen-join handling, single-capability fallback, category
  filtering, empty and oversized input rejection, determinism across two runs.
* **CLI end-to-end (10)** — `--help` and the capability-list command; `capabilities --json`
  returning 178/18; `route` and `plan --out` (including exit code 3 on unapproved
  overwrite); review exit codes 2/0; `urgency --json` critical level and `redact`; handoff
  export gate and file content; full profile lifecycle (init without consent → 3, with
  consent → 0, set, remember, forget refused → 3, forget approved → 0); `parse-source`
  producing a catalog the CLI then loads; `validate` and `governance` self-check;
  structured error paths (`catalog_missing`, empty query, conflicting input); `version` and
  `search`.

**Manual command verification:** `--help` (top-level plus `plan`), `capabilities` (text and
JSON), `list`, `show`, `search`, `route`, `plan` (default and `--out` with `--approve`),
`review` (fail/pass), `urgency`, `redact`, `handoff` (with and without `--approve`),
`resources`, `governance`, `parse-source` (text, Markdown, HTML, single capability, and
re-parsing the shipped PDF text back into 178 skills / 18 categories), `validate`,
`template-check`, `self-test`, `version`, and the whole `profile` family — all inspected
by reading their rendered output.

**Verified failure and recovery paths:** missing catalog, malformed catalog JSON, missing
template placeholders, conflicting `--text`/`--input-file`, empty query, unknown skill,
invalid policy JSON, unknown profile section, invalid tone value, unapproved overwrite,
unapproved memory deletion, profile init without consent, and `profile validate` reporting
a schema failure. Each returned a structured message and the documented exit code.

**Packaging verification:** the folder was copied into a clean temporary skill root beside a
test `SKILL.md`; `capabilities`, `plan --out reports/plan.md` (relative output path), and the
full test suite were re-run there successfully using only relative paths. The folder
contains no `SKILL.md`, no subfolders, no absolute paths, and every file is prefixed.
File count: **12** (well under the 500-file limit); maximum path depth: **2 levels** from the
future skill root (`humansphere-holistic-support/<file>`).

Not performed, and therefore not claimed: live verification of any external resource,
network or messaging integration, PDF/DOCX ingestion, load testing beyond the documented
caps, and any runtime other than CPython 3.13 (3.9+ is targeted and the code uses no
version-specific syntax, but only 3.13 was executed).

---

## 14. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ERROR [catalog_missing]` | wrong `--catalog` path | run from the skill root and pass `humansphere-holistic-support/asset-skill-catalog.json`, or omit `--catalog` to use the file next to the entry point |
| `ERROR [catalog_invalid_json]` | hand-edited catalog breaks JSON | re-generate with `parse-source --out`, or restore the shipped catalog |
| `BLOCKED [approval_required]` with exit 3 | overwrite / export / delete without a flag | re-run with `--approve` (and `--consent` for memory writes) |
| `ERROR [template_incomplete]` | edited template lost placeholders | `template-check` to list them, then restore the missing `{{SLOTS}}` |
| `ERROR [unknown_key]` | profile section typo | use one of consent, identity, preferences, accessibility, context, boundaries, persona, memory |
| `ERROR [profile_invalid]` | value outside the schema (e.g. tone `shouty`) | `profile validate` for the full list, then set an allowed value |
| `ERROR [timeout]` / exit 5 | `--timeout` too small for the catalog | raise `--timeout`, lower `--max-skills`, or shorten the query |
| `ERROR [skill_not_found]` / `[ambiguous_skill]` | name does not match, or matches several | use the numeric id from `list` |
| `ERROR [category_not_found]` | category name differs | `capabilities` prints exact names and ids |
| `ERROR [input_too_large]` / `[query_too_long]` | over the documented caps | split the file; shorten the query |
| Plan says `Fallback:` | no lexical overlap with the catalog | add tags/description words to the catalog, or rephrase the need |
| Review warns but a human sees nothing wrong | a pattern fired on benign phrasing | tune `asset-safety-policy.json` patterns; severity is data, not code |

### Recovery

* **Atomic writes.** Every write goes to `<name>.tmp` in the same directory and is then
  atomically replaced, so an interrupted run never leaves a half-written catalog, plan,
  profile, or export. If a leftover `*.tmp` exists, delete it — the real file is intact.
* **Cancellation.** `Ctrl+C` exits with code 4 and a message; no partial output is kept.
* **Corrupt profile.** Restore your backup, or re-create it:
  `profile init --consent --approve` then re-add preferences. Memory entries can always be
  re-added; nothing else is affected.
* **Corrupt catalog/policy/template.** Restore the shipped asset, or regenerate a catalog
  from source text with `parse-source --out ... --approve`.
* **Wrong policy edit.** `validate` names the failing section; the policy is data only, so
  restoring the shipped file always recovers the engine.

---

## 15. Extension and maintenance

* **Add skills without code:** append to `asset-skill-catalog.json` (unique `id`, `name`,
  `description`, `category`, `category_id`; omit `tags`/`risk` to have them derived), or
  paste a list into `parse-source --out extras.json` and pass `--catalog extras.json`.
  Keep skill ids unique and descriptions in the same "verb-first" style so operating
  prompts stay clean.
* **Add governance rules:** append to `response_rules` (`id`, `severity`, `title`,
  `patterns`, `remediation`) or `urgency.signals` (`id`, `level`, `label`, `patterns`).
  Run `validate` — every pattern is compiled and invalid regexes are reported. Add a level's
  `required_actions` and `escalation_text` whenever you add a new level.
* **Add regions or services:** append entries to `asset-support-resources.json`; never store
  an unverified phone number, and always fill `verify_note`.
* **Change plan shape:** edit `asset-plan-template.md`, keep the 15 placeholders, run
  `template-check`.
* **Change personalization:** extend `asset-preference-profile.schema.json` *and* the
  `preferences` handling in `tools-plan-builder.py` (`tone_directive`,
  `personalization_note`). Unknown schema keywords are ignored by the subset validator.
* **Tune routing:** `CATEGORY_KEYWORDS`, `STOPWORDS`, `VERB_MAP` (imperative conversion),
  and the scoring weights in `engine.route`/`engine.search` in
  `tools-catalog-engine.py`. Re-run `self-test` after any change — the suite asserts the
  sample need still returns `Student Money Planner`.
* **Version bumps:** `TOOL_VERSION` in `tools-humansphere.py`, `version` in the policy and
  registry assets, and `schema` strings only if you change their shapes.
* **Regression discipline:** `python3 humansphere-holistic-support/tools-humansphere.py validate && python3 humansphere-holistic-support/tools-humansphere.py self-test --quiet`
  after every edit; both must pass before shipping.

---

## 16. Skill package compatibility

* This folder contains **no `SKILL.md`**; it is a component of a future Copilot skill, not a
  complete skill.
* Copy the whole `humansphere-holistic-support/` folder beside the parent package's root
  `SKILL.md` without modification. The combined package must have exactly one `SKILL.md` at
  its root — this folder adds none.
* All files sit directly in the folder (no internal subfolders), every filename is prefixed
  `tools-`, `knowledge-`, or `asset-`, and every internal reference is relative to the
  future skill root using forward slashes
  (for example `humansphere-holistic-support/asset-skill-catalog.json`).
* Maximum path depth contributed by this folder: 2 levels. File count: 12.
* The tool locates its own assets relative to `tools-humansphere.py`, so it keeps working
  after the folder is renamed only insofar as its internal references are untouched —
  internal references use file names, never the folder name, so a renamed folder still
  works when invoked by its new path.
