# google-slides-agent-catalog — Skill Tool knowledge base

This folder is a reusable **Skill Tool component**: the runnable part that a future Copilot
skill loads. It deliberately contains **no `SKILL.md`** — the parent skill owns `SKILL.md` at
the package root, and `google-slides-agent-catalog/` is copied beside it unchanged.

The Skill Tool turns the *Google Slides Agent Skill Catalog* (34 categories, 408 candidate
capabilities, supplied as `Google_Slides_Agent_Skill_Catalog.pdf`) into something an agent can
actually operate: an ingestible capability catalog, a request-to-capability planner, a
deterministic risk and approval gate, an implementation-spec generator, a coverage scorer and
an allowlisted live-API adapter.

---

## 1. Purpose and capability list

The catalog document lists candidate capabilities but implements none of them. This Skill Tool
provides the execution layer those capabilities need in order to be planned, approved,
specified, measured and — where credentials exist — applied.

### 1.1 Primary entry point commands (`tools-gslides-catalog.py`)

| Command | What it actually does | Side effects |
| --- | --- | --- |
| `help` | Full command reference, options, exit codes, safety rules. | none |
| `capabilities` | Machine-readable capability list of this tool (13 commands). | none |
| `categories` | Lists the 34 catalog categories with purpose, skill count and configured phase. | none |
| `list` | Lists skills with `--category` (number, id or name fragment), `--search`, `--limit`. | none |
| `describe` | Resolves one skill or one category: purpose, phase, risk tier, risk evidence, gates, adjacent capabilities to avoid confusing it with. | none |
| `plan` | Tokenises a Slides request, matches it against all 408 skills, classifies risk per match, orders work into configured phases with checkpoints, collects availability gates and approval requirements, validates the result against the plan schema, and writes the plan JSON. | writes plan JSON when `--out` is used |
| `validate` | Re-validates any plan document: schema subset plus semantic checks (approval coverage, tier names, reason traceability). | none |
| `spec` | Renders a 12-section implementation specification for one capability from the packaged template, pre-filled with catalog, risk, phase, gate and neighbour data, with explicit `Decide:` review prompts. | writes Markdown when `--out` is used |
| `coverage` | Scores implemented/partial/planned/absent/unmapped skills per category and overall, lists gaps, and can fail a pipeline via `--min-coverage`. | writes report JSON when `--out` is used |
| `ingest` | Parses plain text, Markdown, copied HTML (tags, CSS classes, `data-*` attributes, numbering marks and extra spacing removed) or PDF into a validated catalog document. | writes catalog JSON when `--out` is used |
| `approve-request` | Emits an **unsigned** approval record bound to a SHA-256 fingerprint of the exact plan, listing every gated step and its reasons. | writes record JSON when `--out` is used |
| `approve-check` | Verifies a **signed** approval record: fingerprint match, `status`, named approver, ISO-8601 timestamp inside the age limit, and acknowledgement of every gated skill. | none |
| `doctor` | Checks configuration, catalog, risk policy, plan schema, spec template, phase coverage, Python version and API adapter presence. | none |

### 1.2 Live adapter operations (`tools-slides-api.py`)

Read (no approval token): `get-presentation`, `get-file`, `list-permissions`.
Write (operator approval token required): `create-presentation`, `batch-update`,
`create-permission`.

`batch-update` accepts only request kinds from a 34-entry allowlist of native Google Slides
`presentations.batchUpdate` requests. Five destructive kinds (`deleteObject`, `deleteText`,
`deleteTableRow`, `deleteTableColumn`, `ungroupObjects`) additionally require
`--confirm-destructive`. Anonymous `type: "anyone"` Drive permissions are refused outright.

### 1.3 Catalog coverage represented

* 34 categories, 408 skills, extracted with 0 validation errors from the supplied document.
* 18 executable phases, each with a purpose, a checkpoint and the Slides surfaces it touches.
* 7 risk tiers, 7 classification rule sets and 16 hard-gate terms.
* 6 availability/platform gates (account, admin policy, media permissions, fonts, browser
  capability, staged rollout) attached to the categories they affect.

---

## 2. Files, roles and load order

| File | Role | Loaded by |
| --- | --- | --- |
| `tools-gslides-catalog.py` | **Primary entry point.** CLI, command dispatch, approval fingerprinting, output envelope, exit codes. | operator / agent |
| `tools-catalog-core.py` | Shared core: configuration, path resolution, source reading (txt/md/html/pdf), HTML stripping, catalog parsing, catalog validation, catalog indexing, atomic JSON/text writes, error type. | primary entry point (first), plan engine (reuses the loaded instance), API adapter |
| `tools-plan-engine.py` | Planning engine: tokenisation, scoring, risk classification, hard gates, phase ordering, plan assembly and rendering, specification rendering, coverage computation, local schema-subset validator. | primary entry point (second) |
| `tools-slides-api.py` | Optional live Google Slides/Drive adapter with allowlist, approval gate, destructive confirmation, timeouts, retries, dry-run. Independent entry point. | operator / agent, invoked separately |
| `tools-self-test.py` | Test harness: 16 groups exercising every command, the approval flow, a stub HTTP server and a clean-skill-root copy. | operator / CI |
| `knowledge-readme.md` | This document. The only knowledge file. | humans / agent |
| `asset-slides-catalog.json` | The packaged catalog dataset: 34 categories / 408 skills with ids, descriptions, categories, purposes and extraction provenance. Drives every command. | primary entry point, plan engine |
| `asset-default-config.json` | Runtime configuration: resource paths, limits, matching weights, stopwords, synonyms, 18 phases (ordered), 6 gates, approval policy, API endpoints, spec defaults. | every tool |
| `asset-risk-policy.json` | Risk tiers with ranks, approval requirements and execution notes; classification rules; hard-gate terms; always-review categories. | primary entry point, plan engine |
| `asset-plan-schema.json` | Local validation schema for plan documents and the list of schema keywords the validator supports. | primary entry point (`plan`, `validate`) |
| `asset-spec-template.md` | The 12-point implementation specification template from the catalog document; its `## N. Title` headings drive `spec` rendering and can be replaced. | primary entry point (`spec`) |

Execution order when any command runs: `tools-gslides-catalog.py` → loads `tools-catalog-core.py`
→ loads `tools-plan-engine.py` (which reuses the already-loaded core instance so
`SkillToolError` identity matches) → reads `asset-default-config.json` → loads the asset it needs.
`tools-slides-api.py` is standalone: it loads only the core.

All internal references are relative to the future skill root, for example
`google-slides-agent-catalog/asset-slides-catalog.json`, and every path in code is resolved from
the tool file's own directory, so the folder can be renamed or moved as a unit.

---

## 3. Setup and dependencies

* Python **3.8+**, standard library only. Built and tested on **CPython 3.13.16**.
* No third-party packages are required for planning, gating, specification, coverage, ingest of
  text/Markdown/HTML, or the API adapter's transport.
* **Optional:** `pypdf` for ingesting `.pdf` sources:
  `python3 -m pip install 'pypdf>=3.9,<7'`
  Without it, PDF ingest fails fast with code `pdf-support-missing` and a hint; everything else
  keeps working.
* No credentials, network access or Google account is needed for any command except the live
  adapter operations, which need a token you supply at run time.
* No dependency manifest file is shipped because the runtime requires none; if you add
  third-party dependencies, ship them in a prefixed dependency file of your own naming and
  document the install command here.

Install-free usage from the future skill root:

```sh
# bash / zsh / PowerShell all work; commands are shown with the shell named
cd <skill-root>
python3 google-slides-agent-catalog/tools-gslides-catalog.py doctor
```

On Windows with PowerShell:

```powershell
py google-slides-agent-catalog/tools-gslides-catalog.py doctor
```

---

## 4. Inputs, outputs, parameters and configuration

### 4.1 Global options (any command)

| Option | Meaning |
| --- | --- |
| `--config <path>` | Configuration file. Default: `asset-default-config.json` beside the primary tool. May be given before or after the subcommand. |
| `--json` | Emit a single JSON envelope on stdout instead of human-readable lines. May be given before or after the subcommand. |
| `--version` | Print the tool version. |

JSON envelope shape:

```json
{
  "ok": true,
  "exit_code": 0,
  "plan": { "...": "command specific payload" },
  "lines": ["human readable lines (omitted in --json output)"]
}
```

Errors are emitted on stderr as
`{"ok": false, "error": {"code": "...", "message": "...", "hint": "...", "details": {}}, "exit_code": 2}`
or, without `--json`, as `ERROR [code] message` plus `hint:`.

### 4.2 Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 2 | Usage, input, configuration or credential error (bad path, unknown id, invalid JSON, missing token, refused URL). |
| 3 | `plan` produced a valid plan but approval gates are open and `--fail-on-approval` was used. |
| 4 | No catalog capability matched the request (`plan`) or no skill matched a filter (`list`). |
| 5 | A document failed validation (plan schema, catalog validation, coverage threshold, `doctor` failure). |
| 6 | Approval gate denied (`approve-check`), or a write operation attempted without a valid approval token. |
| 7 | HTTP error returned by Google (live adapter). |
| 8 | Network failure or timeout after retries (live adapter). |
| 130 | Interrupted by Ctrl-C; no partial output file is left behind (writes are atomic via temp file + rename). |

### 4.3 Command parameters

`plan`: `--request <text|->` (stdin with `-`), `--request-file <path>`, `--category <sel>`
(repeatable: number, id or name fragment), `--max-matches <n>`, `--out <path>`,
`--fail-on-approval`, `--catalog <path>`, `--policy <path>`.

`spec`: `--skill <id>` (required), `--request <text>`, `--template <path>`, `--out <path>`,
`--catalog`, `--policy`.

`coverage`: `--manifest <path>` (required), `--min-coverage <0-1>`, `--out <path>`, `--catalog`.

`ingest`: `--source <path>` (required), `--out <path>`, `--validate`.

`approve-request`: `--plan <path>` (required), `--out <path>`.
`approve-check`: `--plan <path>`, `--record <path>` (both required).

`tools-slides-api.py`: `<operation>`, `--presentation-id`, `--file-id`, `--title`,
`--requests-file`, `--body-file`, `--out`, `--dry-run`, `--approve`, `--confirm-destructive`,
`--timeout`, `--retries`, `--slides-base-url`, `--drive-base-url`, `--config`, `--json`,
`--list-ops`.

### 4.4 Configuration file (`asset-default-config.json`)

| Block | Purpose |
| --- | --- |
| `paths` | `catalog`, `risk_policy`, `plan_schema`, `spec_template`, `output_dir`. Relative values resolve beside the configuration file. `output_dir: null` means "stdout only unless `--out` is given". |
| `limits` | `max_source_bytes` (8 MiB), `max_skills` (2000), `max_matches` (15), `max_batch_requests` (100), `api_timeout_seconds` (20), `api_max_retries` (3), `api_backoff_seconds` (0.5), `max_report_items` (500). Clamped in code to absolute ceilings (`tools-catalog-core.py`), so a hostile config cannot remove the limits. |
| `match` | `min_score`, `name_weight`, `description_weight`, `category_weight`, `max_query_tokens`, `extra_stopwords`, `synonyms`. Matching is fully configurable without code edits. |
| `phases` | The ordered execution model: `id`, `label`, `purpose`, `checkpoint`, `surfaces`, `categories`. All 34 catalog categories must map to a phase or `doctor` and `plan` report it. |
| `gates` | Platform/availability requirements attached to categories, surfaced in plans and specifications. |
| `approval` | `token_env` for the live adapter's operator token, `token_file` (unused by default), `max_age_hours` (72) for signed approval records. Tokens are never stored here. |
| `spec_defaults` | Owner, version, deprecation policy and success-condition sentence used by `spec`. |
| `api` | Slides/Drive base URLs, access-token environment variable name, user agent. Point these at a proxy or a stub when testing. |

### 4.5 Data formats

**Catalog document** (as produced by `ingest`, consumed by everything):

```json
{
  "schema_version": "1.0",
  "source_name": "catalog.txt",
  "counts": {"categories": 2, "skills": 4},
  "categories": [{
    "number": 1, "id": "deck-foundations", "name": "Deck Foundations", "slug": "deck-foundations",
    "purpose": "Establish the file before content work.", "skill_count": 2,
    "skills": [{"id": "deck-starter", "name": "Deck Starter", "number": 1,
                "description": "Creates a presentation with the approved name and folder.",
                "category_id": "deck-foundations", "category_name": "Deck Foundations"}]
  }],
  "warnings": []
}
```

**Plan document** — validated against `asset-plan-schema.json`; top-level keys are
`plan_version`, `generated_by`, `request`, `catalog`, `matches`, `phases`, `approvals`, `gates`,
`validation`, `notes`. Each match carries `score`, `matched_terms` and a `risk` block
(`tier`, `rank`, `approval_required`, `matched_terms`, optional `hard_gate_terms`); each phase
carries ordered `steps` with per-step risk and the Slides `surfaces` involved.

**Coverage manifest** — either form is accepted:

```json
{"skills": {"presentation-creation-router": "implemented", "image-crop-specialist": "partial"}}
```
```json
{"skills": ["presentation-creation-router", "image-crop-specialist"]}
```
Statuses: `implemented` (weight 1.0), `partial` (0.5), `planned` (0), `absent` (0); skills not
mentioned count as `unmapped`. Coverage is computed per category and overall.

**Approval record** — produced by `approve-request`, signed by a human:

```json
{
  "record_version": "1.0",
  "status": "approved",
  "plan_fingerprint": "sha256:...",
  "gated_skills": ["slide-delete-and-recovery-manager", "export-fidelity-test"],
  "approval_requirements": [{"skill_id": "...", "tier": "destructive", "reasons": ["risk terms: delete"]}],
  "approver": "Dana Reyes (content owner)",
  "approved_at": "2026-11-07T02:39:18Z",
  "acknowledged_skills": ["slide-delete-and-recovery-manager", "export-fidelity-test"]
}
```

---

## 5. Capability examples (every command, real invocations)

```bash
# 0. Health check (do this first after copying the folder beside SKILL.md)
python3 google-slides-agent-catalog/tools-gslides-catalog.py doctor

# 1. Capability list
python3 google-slides-agent-catalog/tools-gslides-catalog.py capabilities

# 2. Browse the catalog
python3 google-slides-agent-catalog/tools-gslides-catalog.py categories
python3 google-slides-agent-catalog/tools-gslides-catalog.py list --category 9 --limit 5
python3 google-slides-agent-catalog/tools-gslides-catalog.py list --search "alt text" --json

# 3. Inspect one capability (risk evidence + adjacent capabilities)
python3 google-slides-agent-catalog/tools-gslides-catalog.py describe --skill image-alt-text-author

# 4. Plan a real task
python3 google-slides-agent-catalog/tools-gslides-catalog.py plan \
  --request "apply the brand theme, fix text overflow, then publish for client review" \
  --out plan.json

# 5. Plan from a file, or from stdin
python3 google-slides-agent-catalog/tools-gslides-catalog.py plan --request-file brief.txt --out plan.json
printf 'crop images and add alt text' | python3 google-slides-agent-catalog/tools-gslides-catalog.py plan --request - 

# 6. Re-validate a plan later (for example after a merge or hand-edit)
python3 google-slides-agent-catalog/tools-gslides-catalog.py validate --plan plan.json

# 7. Turn a capability into an implementation specification
python3 google-slides-agent-catalog/tools-gslides-catalog.py \
  spec --skill public-publishing-gate --out spec-public-publishing-gate.md

# 8. Measure how much of the catalog is implemented
python3 google-slides-agent-catalog/tools-gslides-catalog.py \
  coverage --manifest coverage-manifest.json --min-coverage 0.6 --out coverage.json

# 9. Ingest a different catalog (text, Markdown, copied HTML or PDF)
python3 google-slides-agent-catalog/tools-gslides-catalog.py ingest --source other-catalog.md --out catalog.json --validate

# 10. Governed approval of a gated plan
python3 google-slides-agent-catalog/tools-gslides-catalog.py approve-request --plan plan.json --out record.json
#    ... an authorised operator edits record.json: status=approved, approver, approved_at, acknowledged_skills ...
python3 google-slides-agent-catalog/tools-gslides-catalog.py approve-check --plan plan.json --record record.json

# 11. Optional live calls (credentials required, see section 8)
python3 google-slides-agent-catalog/tools-slides-api.py get-presentation --presentation-id <id> --dry-run
python3 google-slides-agent-catalog/tools-slides-api.py get-presentation --presentation-id <id> --out presentation.json
python3 google-slides-agent-catalog/tools-slides-api.py batch-update --presentation-id <id> \
  --requests-file requests.json --approve "$GSLIDES_SKILL_APPROVAL_TOKEN" --out result.json

# 12. Full self-test
python3 google-slides-agent-catalog/tools-self-test.py
```

### 5.1 What `plan` reports for a real request

For `--request "publish the deck to the web and share it read-only with external reviewers"` the
plan returns 15 ranked matches spanning `distribute`, `access`, `protect`, `assure`, `automate`,
`support`, `prepare` and `interop` phases, 11 approval gates, 4 platform gates and a SHA-256
fingerprint. The publish step is forced into `security-sensitive` by the hard-gate term
`publish to web`, and every approval entry shows the exact policy terms that caused it
(`risk terms: public, privacy, exposure`).

### 5.2 What `spec` produces

Twelve sections, all filled: identifier and registration, purpose plus derived success
condition, triggers/non-triggers with the three adjacent capabilities that should *not* be
confused with it, inputs and account prerequisites plus the category's platform gates,
outputs and side effects, Slides surfaces for the phase and the allowlisted API route,
permission and privacy boundaries derived from the risk tier, the checkpointed execution
sequence with rollback, availability detection and a fallback ladder, validation checks,
normal/boundary/failure/repair/regression cases, and version/ownership/deprecation policy.
Lines that only the owning team can decide are marked `Decide:`.

### 5.3 How matching and risk classification work

* Text is lowercased, tokenised, stop-worded (built-in list plus `match.extra_stopwords`),
  synonym-mapped (`match.synonyms`) and lightly stemmed.
* Each catalog skill is scored: `name_weight` (3) per token found in the skill name,
  `description_weight` (1) per token in the description, `category_weight` (1) per token in the
  category name/purpose. Skills below `match.min_score` (3.0) are dropped.
* Ordering is deterministic: score desc, distinct matched terms desc, category number, skill
  number, skill id. The same request always yields the same plan and fingerprint.
* Risk: each policy rule counts the distinct keywords it finds in the skill's name and
  description; the tier with the most distinct hits wins and ties go to the higher rank
  (`tie_breaker: higher-rank`). Any `hard_gate_terms` hit in the request **or** in the skill
  description forces approval even if the tier itself is normally ungated. Tiers at rank 3 and
  above require approval (`destructive` 3, `external-distribution` 4, `access-change` 5,
  `security-sensitive` 6).

---

## 6. Safety and approval rules

1. **Planning is inert.** No command in the primary tool opens a network connection, edits a
   presentation or shells out. The only outbound code lives in `tools-slides-api.py`, which is
   never invoked by the primary tool.
2. **Gated tiers.** Destructive, external-distribution, access-change and security-sensitive
   work always produces approval entries. `--fail-on-approval` turns that into exit code 3 for
   pipelines.
3. **Fingerprint-bound human approval.** `approve-request` writes an unsigned record containing
   `sha256` of the plan's decision surface (request text, match ids, tiers, gated skills).
   `approve-check` approves only when the record names an approver, carries an ISO-8601
   `approved_at` inside `approval.max_age_hours` (72 h default), acknowledges every gated
   skill and matches the current plan's fingerprint. Editing the plan after approval
   invalidates the record — verified by test.
4. **No self-approval path.** The tool never sets `status: approved`, never fills `approver`,
   and never writes a signature. That action is deliberately left to a human editing the
   record.
5. **Live write calls need an operator token** from `GSLIDES_SKILL_APPROVAL_TOKEN` (or
   `--approve`); a mismatched token is rejected (`approval-mismatch`). Destructive batch
   requests need `--confirm-destructive` as well.
6. **Anonymous sharing is blocked.** `create-permission` refuses `type: "anyone"`; publishing
   must go through the governed flow (`publish-to-web-privacy-auditor`,
   `public-publishing-gate`, `governed-release-gate`).
7. **Token handling.** The OAuth token is read from `GSLIDES_ACCESS_TOKEN` at call time,
   placed only in the `Authorization` header, redacted in all output (`<redacted>`), and never
   written to result files. Verified by test.
8. **Transport safety.** Plain `http` base URLs are refused unless the host is
   `localhost`/`127.0.0.1`, so an OAuth token cannot be sent in clear text by accident.
9. **Bounded execution.** Timeouts 1–120 s, retries 0–8 with capped exponential backoff,
   batch size ≤ `max_batch_requests`, source size ≤ `max_source_bytes`, offsets capped at
   500 skills / 5000 absolute, responses truncated at 256 KiB, and no arbitrary
   code/shell/URL execution surface at all.
10. **Atomic writes.** JSON and Markdown are written to `<path>.tmp` and renamed, so an
    interrupt cannot leave a half-written deliverable; Ctrl-C exits 130 without partial output.

---

## 7. Validation and tests actually performed

Performed in this build environment (CPython 3.13.16, Linux sandbox), against the packaged files:

`python3 tools-self-test.py` → **16/16 test groups passed (~5.6 s)**.

| # | Test group | What was proven |
| --- | --- | --- |
| 1 | help and capability list | `help` renders usage and exit codes; `capabilities --json` advertises 13 commands. |
| 2 | doctor reports healthy environment | config, catalog (34/408), policy (7 tiers/7 rules), schema, template (12 sections), phase mapping (34/34), Python, adapter presence — all pass, exit 0. |
| 3 | categories and list filters | 34 categories / 408 skills; `--category 9` returns exactly 12 skills; `--search "alt text"` narrows results and includes `image-alt-text-author`. |
| 4 | describe skill and category | `image-alt-text-author` resolves to `reversible-edit` / phase `build` with neighbours; `--category localization` returns 12; unknown id exits 2 with a "Did you mean" hint. |
| 5 | plan produces ordered, schema-valid plan | exit 0, `validation.status: ok`, approval gates present for a sharing request, phases sorted by configured order, every phase has steps and a checkpoint, plan file written, `validate` re-passes. |
| 6 | changed request changes the result | "crop images and write alt text" → `images-and-image-formatting`; "translate the deck into Japanese" → `localization-and-multilingual-slides`. |
| 7 | category mode and exit codes | `--category 22` returns only accessibility skills; "publish to the web" with `--fail-on-approval` exits 3 and records hard-gate terms. |
| 8 | invalid input handling | missing request, unknown category (with hint), stopword-only request, missing file, malformed JSON and a schema-invalid plan (exit 5) are all reported with codes; recovery hints verified. |
| 9 | stdin and file requests | `--request -` and `--request-file` both parse and appear in `request.text`. |
| 10 | spec rendering | 12/12 sections filled; output differs between skills; unknown skill exits 2. |
| 11 | ingest txt/Markdown/HTML/PDF | Markdown and copied HTML (tags, classes, `data-*`, numbering) both parse to 2 categories / 4 skills with correct purposes; a hand-built valid PDF parses to 2 skills (optional `pypdf` present); empty source and missing source exit 2 with codes `source-empty`, `source-not-found`. |
| 12 | coverage, threshold, recovery | totals computed exactly (3 implemented / 1 partial / 1 planned / 1 absent / 402 unmapped, 34 category rows, gaps listed); `--min-coverage` failure exits 5; typo in a skill id exits 2 with the correct suggestion, and the corrected manifest then succeeds. |
| 13 | approval flow | unsigned record denied (6); signed record approved (0); editing the plan afterwards denied with a fingerprint problem (6); an old `approved_at` denied with an age problem (6). |
| 14 | API adapter policy | 6 operations listed; dry run redacts the token; write without approval → `approval-required`; disallowed request kind → `requests-not-allowed`; destructive kind without confirmation → `destructive-confirmation-required`; with confirmation it proceeds; non-local plain-http base URL → `insecure-base-url`; missing token → `missing-credentials`. |
| 15 | API adapter transport (local stub) | Real HTTP round trip against a stub server: read 200 with the bearer header set and the token absent from output; approved write sends the expected body; wrong approval token → `approval-mismatch`; a 429 then 200 is retried (2 attempts, 2 requests observed); a dead port after retries exits 8 with `network`. |
| 16 | clean skill root | Folder copied beside a test `SKILL.md`, no `SKILL.md` and no subfolder inside it, all 11 files prefixed; `plan`, `spec` and `doctor` run from the parent root using relative paths only (`--out plan-out.json`) and produce output where requested. |

Additional checks run during the build:

* The supplied PDF re-parsed through `ingest` yields exactly 34 categories / 408 skills with
  0 validation errors, matching the document's own summary (also 34/408).
* `python3 -m py_compile` clean on all five `tools-*.py` files.
* Release ZIP inspected: Skill Tool folder at the ZIP root, no wrapper folder, no nested
  archives, 11 files, maximum path depth 2 (`google-slides-agent-catalog/<file>`).
* ZIP extracted into a clean temporary location and the primary command re-run successfully
  from the extracted files.

**Not tested (state honestly):** live calls to Google Slides API v1 / Drive API v3 (no OAuth
credentials or Google account in this environment), behaviour against real Google files,
admin-policy variations, and non-Linux platforms. The adapter's transport, retry, allowlist and
approval logic are covered by the stub-server test; anything requiring a real Google tenant
must be verified by the operator before production use.

---

## 8. Limits and unavailable integrations

* **No live Google calls in the primary tool** by design; only `tools-slides-api.py` can act on
  Google files, and only with a token the operator supplies.
* **Live adapter untested against Google.** Credentials were unavailable. Documented in
  section 7; verify in your tenant before relying on it.
* **No OAuth flow is implemented.** You must obtain a short-lived access token yourself (for
  example from your own installed-app/service-account flow or an existing tool) and export it
  as `GSLIDES_ACCESS_TOKEN`. Scopes needed: `presentations` (Slides) and `drive.metadata.readonly`
  / `drive.file` (Drive) depending on the operation.
* **`create-permission` cannot grant public ("anyone") access** — intentional.
* **PDF ingest depends on `pypdf`**; without it that source type is unavailable.
* **Matching is lexical, not semantic.** No embeddings or LLM call is involved, so unusual
  phrasing may need `--category`/`--search` or a synonym added to `match.synonyms`.
* **Coverage is manifest-driven.** The tool does not inspect a codebase; you supply the
  implemented/partial statuses.
* **The catalog is a snapshot.** It describes Google Slides as of the supplied document; feature
  availability still varies by account, edition, region, browser and staged rollout — every plan
  therefore carries platform gates rather than assumptions.
* **Approval is an attestation, not an identity system.** `approve-check` verifies the shape and
  binding of a human-signed record; identity proofing is the parent platform's job.

---

## 9. Troubleshooting and recovery

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `[config-not-found]` | Configuration missing or path wrong. | Run from the skill root, or pass `--config <path>` pointing at a copy of the config. |
| `[catalog-invalid]` | The catalog asset no longer validates. | Re-run `ingest --source <original document> --out catalog.json`, then point `paths.catalog` at it. |
| `[unknown-category]` / `[unknown-skill]` | Selector does not match. | The hint lists valid choices or close matches; `categories` and `list --json` show exact ids. |
| `[empty-request]` | Every word was a stopword. | Describe concrete Slides actions ("crop images", "reorder slides", "export PDF"). |
| exit 4 from `plan` | Nothing in the catalog matched. | Rephrase, or constrain with `--category`. |
| exit 5 from `validate` | Plan schema or semantics failed. | Read the printed issue paths (`plan.matches[2].risk.tier: …`) and fix the producer. |
| `[pdf-support-missing]` | Optional dependency absent. | `python3 -m pip install 'pypdf>=3.9,<7'`, or ingest a text/Markdown export. |
| `[manifest-invalid]` | Manifest shape or status word wrong. | Use `{"skills": {id: status}}` with statuses `implemented|partial|planned|absent`. |
| exit 6 from `approve-check` | Gate not satisfied. | Read the `problem` lines: status, approver, timestamp age, unacknowledged skills, or fingerprint mismatch after a plan edit. |
| `[approval-required]` from the adapter | Write op without an operator token. | Have the operator export `GSLIDES_SKILL_APPROVAL_TOKEN` (or pass `--approve`) after reviewing the plan and approval record. |
| `[missing-credentials]` | No `GSLIDES_ACCESS_TOKEN`. | Obtain a short-lived token from your own authorised flow and export it. |
| exit 8 from the adapter | Network failure/timeout. | Check connectivity and the base URL; raise `--timeout`/`--retries` within limits. |
| exit 7 from the adapter | Google returned an HTTP error. | The error body is included; fix the file id, scopes or sharing, then retry. |
| `doctor` FAIL on `phase-mapping` | A category in the catalog has no phase. | Add the category id to a phase's `categories` array in the configuration. |

Recovery principles: every write is atomic, plans are reproducible from the request text, the
approval record can always be regenerated with `approve-request`, and a failed live call made no
change beyond what Google itself reports.

---

## 10. Extension and maintenance

* **New capability in the catalog:** run `ingest` on the updated document (`--validate`), save the
  result over `asset-slides-catalog.json`, then run `doctor`. If a new category appears, add it to
  a phase in `asset-default-config.json`.
* **Different house style or ordering:** edit the `phases` array; ordering of `phases` is the
  ordering of plans.
* **Different risk posture:** edit `asset-risk-policy.json` — tiers, ranks, `approval_required`,
  keyword rules, `hard_gate_terms`, `always_review_categories`. `validate` checks that approval
  reasons remain traceable to policy terms.
* **Different specification template:** replace `asset-spec-template.md` (keep `## N. Title`
  headings 1–12) or pass `--template`. Sections whose number has no generator keep their
  template body.
* **Better matching:** extend `match.synonyms` and `match.extra_stopwords`, or tune
  `match.min_score` and the weights. No code change required.
* **New live operation:** add an entry to `READ_OPERATIONS` or `WRITE_OPERATIONS` in
  `tools-slides-api.py`, add its `requires` arguments, and (for batch kinds) extend
  `ALLOWED_BATCH_REQUESTS`. Keep write operations approval-gated, then extend
  `tools-self-test.py` with the new case.
* **New command:** implement `cmd_<name>(session, args)` returning `(payload, exit_code)`, add it
  to `COMMANDS`, `CAPABILITIES`, `HELP_TEXT` and the parser, and cover it in the self-test.
* **Before any release:** run `tools-self-test.py`, `doctor`, and confirm the folder still has no
  `SKILL.md`, no subfolders and only `tools-`/`knowledge-`/`asset-` prefixed files.

Fixed conventions to preserve: stable lowercase-kebab skill ids come from the catalog names;
`plan_version` 1.0 is the document contract (bump on breaking change); the approval fingerprint
covers request text, match ids/tiers and gated skills — changing that surface invalidates old
records by design.
