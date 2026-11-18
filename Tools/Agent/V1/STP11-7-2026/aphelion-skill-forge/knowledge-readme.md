# Aphelion Skill Forge — knowledge and operating guide

`aphelion-skill-forge` is a reusable **Skill Tool**: the runnable component that a
future Copilot skill calls. It is not a complete skill package. It contains no
`SKILL.md`; the parent package owns the single root `SKILL.md` that sits beside
this folder.

**What it does:** it takes a capability specification — a single capability, a
numbered Skill Tool list, one category, or several categories, in plain text,
Markdown, copied HTML or JSON — and produces a complete, runnable Skill Tool
folder that unifies every related capability behind one entry point. Each
generated folder is written to disk and then **executed** by the forge before it
is reported as usable.

---

## 1. Purpose and capability list

The forge answers one question: *"here is a capability list, what does the
reusable Skill Tool look like, and does it actually run?"*

### 1.1 Forge commands

| Command | Purpose | Writes files |
|---|---|---|
| `help` | Full help, marker syntax, exit codes | no |
| `capabilities` | List the forge's own commands, supported generators, and the bundled catalog dataset (34 categories / 408 concepts) | no |
| `capabilities --catalog-list` | List every bundled catalog category with number, slug, domain, risk | no |
| `extract` | Parse a specification; report items, categories, focus lines, warnings | no |
| `unify` | Resolve **one** unified Skill Tool specification: name, title, domains, artifacts, checks, risk, approval reasons, capability list | no |
| `validate-spec` | Validate an input and show what would be written | no |
| `scaffold` | Render, write, then execute the generated folder; optionally package it | yes |
| `package` | ZIP an existing folder and inspect the archive shape | yes (zip) |
| `doctor` | Environment, asset and folder-layout checks | no |

### 1.2 Capabilities of a generated Skill Tool

Every generated folder exposes the same four commands, so the parent skill has
one predictable interface:

| Generated command | What it does |
|---|---|
| `help` | Usage for the generated tool |
| `capabilities` | Every unified capability with id, risk level and approval gate |
| `plan` | Validates input, builds an ordered gated plan (`plan.json`) |
| `run` | Validates input, enforces approval gates, walks every step, writes `plan.json`, `capabilities.json`, `report.md`, optional `checkpoints.jsonl`, runs the checks |
| `validate` | Re-runs the declared checks against an existing output folder |

### 1.3 The end-to-end pipeline

```
input (md / txt / html / json / stdin)
   │  normalise: strip tags, CSS classes, data attributes, numbering marks
   ▼
extract  ── items + categories + focus lines  (structured, with warnings)
   │  unify: one umbrella name, merged capability list, domain profile,
   ▼         artifacts, quality checks, risk + approval gate
scaffold ── tools-<name>.py, tools-<name>-checks.py,
   │        knowledge-readme.md, asset-<name>-manifest.json
   │  verify: execute help → capabilities → plan → run → validate,
   ▼          missing-input error path, approval-gate block path
package  ── <name>.zip with the folder directly at the archive root
```

---

## 2. Requirements and setup

- **Python 3.9+**, standard library only. Verified on Python 3.13.16.
- No third-party packages, no network access, no API keys, no credentials.
- Total folder size is under 400 KB including the bundled catalog dataset.
- Works on Linux, macOS and Windows. Use `python` instead of `python3` where
  that is the interpreter name.

Nothing to install. Run the tools directly:

```sh
# POSIX shell
sh -c 'cd aphelion-skill-forge && python3 tools-skillforge.py help'

# PowerShell
powershell -NoProfile -Command "cd aphelion-skill-forge; python tools-skillforge.py help"
```

Verify the environment before first use:

```sh
python3 tools-skillforge.py doctor --format text
```

Expected: every line `PASS`, exit code `0`. `doctor` checks the interpreter
version, the presence of every asset, policy and dataset parsing, and the
folder-layout rules.

---

## 3. Files in this Skill Tool

| File | Role | Why it is separate |
|---|---|---|
| `tools-skillforge.py` | **Primary entry point.** CLI: argument parsing, command dispatch, approval gate, exit codes, structured output. | Single documented entry point. |
| `tools-skillforge-lib.py` | Shared internals: input acquisition, HTML/text normalisation, extraction, unification, policy/risk evaluation, folder validation, retry/timeout/checkpoint primitives. | Used by every other file; keeps parsing logic in one tested place. |
| `tools-skillforge-generate.py` | Renderer and verifier: builds the four generated files, writes the folder, executes the generated tool to prove it runs, and builds/inspects the delivery ZIP. | Rendering and verification are the fragile part; isolating them keeps the CLI readable and lets the self-test call them directly. |
| `tools-skillforge-selftest.py` | End-to-end regression suite through the real CLI, including a clean-room extraction re-run beside a test `SKILL.md`. | Runnable proof that the claims in this document stay true after edits. |
| `knowledge-readme.md` | This document. | Exactly one knowledge file, as required. |
| `asset-default-policy.json` | Operating policy: limits, timeouts, retries, risk levels, 10 risk/approval rules, 25 domain profiles. | Data, not code: tune limits and risk patterns without touching a tool file. |
| `asset-aphelion-catalog.json` | Reference dataset extracted from the supplied `Aphelion_Agent_Skill_Catalog.pdf`: 34 categories, 408 catalog skill concepts, each with focus, domain, risk, artifacts and checks. | Lets the forge recognise and enrich catalog-derived specifications, and drives `--catalog <n>`. |
| `asset-spec-example.md` | Worked example specification in the supported Markdown format. | A real, parseable fixture the self-test reuses. |

Loading and execution order: `tools-skillforge.py` imports
`tools-skillforge-lib.py`, then `tools-skillforge-generate.py`, both by file
path (so the folder can be renamed without edits). The generated folder's
`tools-<name>.py` loads its sibling `tools-<name>-checks.py` the same way at run
time.

---

## 4. Accepted input

| Format | Example marker | Notes |
|---|---|---|
| Numbered Skill Tool list | `1. Skill Name: Intent Clarifier` then `Description: ...` | The catalog's native format |
| Category + focus | `Category 7: Presentation and Slide Engineering` / `Focus: ...` | Category names the umbrella |
| Markdown headings | `## Presentation and Slide Engineering` | Promoted to a category when a focus line or a numbered block follows |
| Label pairs | `Name: X` / `Description: Y` | Also `Tool Name`, `Title`, `Purpose`, `Capability`, `Summary` |
| Copied HTML | `<h2 class="cat">…</h2> … <li data-id="3">Skill Name: X</li>` | Tags, CSS, `class`, `id`, `aria-*`, `data-*` attributes and inline styles are stripped |
| JSON | `asset-aphelion-catalog.json` shape, or any file whose text contains the markers above | Parsed as text; the catalog dataset is the structured path |
| Bundled catalog | `--catalog 7` or `--catalog presentation-and-slide-engineering` | Reads straight from `asset-aphelion-catalog.json` |

Ignored automatically: numbering marks, bullets, bold/italic markers, HTML
tags, CSS rules, template residue (`{{ }}`, `<%% %%>`), repeated page headers,
table-of-contents lines such as `(12 skills)`, and cover-page text. When no
structured markers exist at all, the forge falls back to one capability per
sentence or bullet and says so in `mode: fallback`.

The real catalog PDF text (72 pages) parses to **exactly 408 items in 34
categories, 12 each** — verified, see §9.

---

## 5. Outputs

### 5.1 Generated Skill Tool folder

```
<name>/
├── tools-<name>.py                  # primary runnable entry point
├── tools-<name>-checks.py           # validation checks, loaded by path
├── knowledge-readme.md              # the generated tool's own documentation
└── asset-<name>-manifest.json       # capabilities, risk, artifacts, checks
```

Four files, no subfolders, no `SKILL.md`. A zip built by `package` or
`--package` contains that folder directly at the archive root, with no wrapper
folder and no nested archive.

Written per run, inside `--output-dir`:

| File | Content |
|---|---|
| `plan.json` | Plan id, timestamp, source excerpt, ordered steps with stop conditions and evidence, risk and approval reasons |
| `capabilities.json` | The resolved capability list with ids, summaries and risk levels |
| `report.md` | Human-readable execution record: steps, evidence, check results, declared artifacts, declared quality checks, approval record |
| `checkpoints.jsonl` | Only with `--checkpoint`; append-only progress for recovery |

### 5.2 Forge output

JSON by default (`--format json`), so a parent skill can machine-read every
result. `--format text` gives a compact human summary. Errors are always
structured:

```json
{"ok": false, "error": {"code": "E_INPUT_MISSING",
                        "message": "Input not found: brief.md",
                        "hint": "Check the path, or pass --input - to read stdin."}}
```

### 5.3 Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `2` | Usage error (bad flag, invalid name, existing output without `--force`, unknown catalog number) |
| `3` | Input error (missing, directory, empty, binary, wrong encoding, too large) |
| `4` | Approval required — a risk trigger was matched and not approved |
| `5` | A declared check failed |
| `130` | Cancelled (Ctrl-C) |

---

## 6. Parameters

### 6.1 Forge

| Flag | Default | Meaning |
|---|---|---|
| `--input <path>` / `--input -` | – | Specification file or stdin |
| `--catalog <number|slug>` | – | Drive from the bundled catalog instead of a file |
| `--out <dir>` | `out` | Parent directory for the generated folder |
| `--name <slug>` | derived | Override the folder name; validated, not silently repaired |
| `--title <text>` | derived | Override the human title |
| `--generator <id>` | `python` | `python` is implemented and executed; `node`, `shell`, `none` emit specification files only |
| `--approve "<reason>"` | – | Repeatable. Records a human approval for one listed reason |
| `--package` / `--zip <path>` | off / `<out>/aphelion-skill-tool.zip` | Package the folder and inspect the archive |
| `--force` | off | Replace an existing folder (otherwise `E_OUTPUT_EXISTS`) |
| `--no-verify` | off | Skip executing the generated tool — not recommended |
| `--checkpoint` | off | Write `.forge-checkpoints.jsonl` next to the output |
| `--policy <path>` | – | Override `defaults`, `risk_levels`, `rules`, `profiles` |
| `--catalog-data <path>` | bundled | Use a different catalog dataset |
| `--format json|text` | `json` | Structured or human output |
| `--timeout <seconds>` | `120` | Wall-clock budget; the forge fails cleanly instead of hanging |
| `--report <path>` | – | Write the scaffold JSON result to a file |

### 6.2 A generated Skill Tool

| Flag | Default | Meaning |
|---|---|---|
| `--input <path>` / `-` | required | Task brief or payload; validated before any work |
| `--output-dir <dir>` | `out` | Destination; created if absent; nothing outside it is touched |
| `--approve "<reason>"` | – | Repeatable; required per approval reason when the plan is gated |
| `--checkpoint` | off | Append `checkpoints.jsonl` after each step |
| `--timeout <seconds>` | from policy (`120`) | Time budget; remaining steps are marked `cancelled` on expiry |
| `--max-input-bytes <n>` | from policy (`4000000`) | Hard input cap |
| `--format json|text` | `json` | Structured or human output |
| `--plan-path <path>` | `--output-dir/plan.json` | Explicit plan destination (`plan` command) |

### 6.3 Policy file

```json
{
  "defaults": {
    "max_input_bytes": 4000000, "max_items": 500, "max_tool_files": 12,
    "max_assets": 8, "max_output_bytes": 8000000, "max_file_bytes": 1048576,
    "timeout_seconds": 120, "retries": 2, "retry_backoff_seconds": 0.25,
    "strict": false, "require_approval_for_risk": ["elevated", "high"],
    "allow_command_provider": false, "command_provider_allowlist": [],
    "checkpoint_every_step": true, "profile_min_keywords": 2, "max_profiles": 2
  },
  "risk_levels": {
    "low": {"rank": 1, "approval": false},
    "standard": {"rank": 2, "approval": false},
    "elevated": {"rank": 3, "approval": true},
    "high": {"rank": 4, "approval": true}
  },
  "rules": [{"id": "credentials", "label": "Credentials, secrets, or key material",
             "risk": "high", "patterns": ["credential|secret|api[ _-]?key|password|token"]}],
  "profiles": {"research-evidence": {"keywords": ["citation", "source"],
                                      "domain": "research-evidence",
                                      "artifacts": ["evidence-table.md"],
                                      "checks": ["each claim carries a source and date"],
                                      "risk": "standard"}}
}
```

`--policy` deep-merges `defaults` and `risk_levels`, and replaces `rules` and
`profiles` wholesale.

---

## 7. Examples for every capability

### 7.1 `help`

```sh
python3 tools-skillforge.py help
```

Prints argparse help for every command plus an explanation of accepted markers
and exit codes.

### 7.2 `capabilities`

```sh
python3 tools-skillforge.py capabilities --format text
python3 tools-skillforge.py capabilities --catalog-list --format json
```

The first lists the eight forge commands and the four generator targets. The
second lists all 34 catalog categories with slugs, domains and risks.

### 7.3 `extract`

```sh
python3 tools-skillforge.py extract --input asset-spec-example.md --format text
```

```
mode: catalog
items: 5, categories: 1
  [5] Turnaround Reporting and Evidence - Weekly operating evidence for delivery reviews, ...
```

With `--input /path/to/catalog.txt` (the extracted PDF text) the same command
reports `items: 408, categories: 34`.

### 7.4 `unify`

```sh
python3 tools-skillforge.py unify --input asset-spec-example.md --format text
```

```
name:      turnaround-reporting-and-evidence
title:     Turnaround Reporting and Evidence
mode:      catalog
domains:   research-evidence
risk:      high (approval required: yes)
  approval: Publishing or external distribution
artifacts: evidence-table.md, citations.json
checks:    each claim carries a source and date; competing claims compared; ...
capabilities (5):
  weekly-metrics-compiler     high   Weekly Metrics Compiler
  evidence-pack-assembler     low    Evidence Pack Assembler
  ...
```

### 7.5 `validate-spec`

```sh
python3 tools-skillforge.py validate-spec --input asset-spec-example.md --format text
```

Shows the name, the item and category counts, the risk, and exactly which files
would be written — without writing anything.

### 7.6 `scaffold` (the main path)

```sh
python3 tools-skillforge.py scaffold \
  --input asset-spec-example.md \
  --out ./out \
  --approve "Publishing or external distribution" \
  --checkpoint --package --format text
```

```
turnaround-reporting-and-evidence -> ./out/turnaround-reporting-and-evidence
  asset-turnaround-reporting-and-evidence-manifest.json
  knowledge-readme.md
  tools-turnaround-reporting-and-evidence-checks.py
  tools-turnaround-reporting-and-evidence.py
layout: ok
verification: passed
  - help ok
  - capabilities ok (5)
  - plan ok
  - run ok (plan, report, capabilities)
  - validate ok
  - missing-input error ok
  - approval gate blocks unapproved run
zip: ./out/aphelion-skill-tool.zip (4 entries, shape ok)
```

### 7.7 Scaffolding straight from the bundled catalog

```sh
# an elevated-risk category: shows the gate
python3 tools-skillforge.py scaffold --catalog 15 --out ./out
# -> exit 4, E_APPROVAL_REQUIRED, "Schema or data contract change"

# approve the named reason
python3 tools-skillforge.py scaffold --catalog 15 --out ./out \
  --approve "Schema or data contract change" --package
# -> databases-and-data-engineering/ with 12 unified capabilities, verified
```

All 34 categories can be scaffolded the same way:

```sh
for n in $(seq 1 34); do
  python3 tools-skillforge.py unify --catalog "$n" --format json \
    | python3 -c 'import json,sys; d=json.load(sys.stdin)["risk_gate"]; print("\n".join(d["reasons"]))'
done
```

Use that output to collect the approval tokens a batch run needs.

### 7.8 Scaffolding an entire catalog into one unified tool

```sh
python3 tools-skillforge.py scaffold --input catalog.txt \
  --name aphelion-full-catalog --out ./out \
  --approve "Credentials, secrets, or key material" \
  --approve "Destructive or irreversible operation" \
  --approve "Publishing or external distribution" \
  --approve "Sensitive or personal data handling"
```

Produces one folder whose `capabilities` command lists all 408 concepts as a
single ordered workflow. Verified: 408 capabilities, checks pass, folder stays
under 1 MB per file.

### 7.9 `package`

```sh
python3 tools-skillforge.py package --folder ./out/turnaround-reporting-and-evidence \
  --zip ./out/turnaround-review-tool.zip --format text
```

```
zip: ./out/turnaround-review-tool.zip
entries: 4, root: turnaround-reporting-and-evidence
archive: ok
layout: ok
```

`package` refuses archives with a wrapper folder, a nested archive, an entry
deeper than two levels, or a `SKILL.md` inside the folder.

### 7.10 `doctor`

```sh
python3 tools-skillforge.py doctor --format text
```

### 7.11 Running a generated Skill Tool

```sh
cd out/turnaround-reporting-and-evidence

python3 tools-turnaround-reporting-and-evidence.py help
python3 tools-turnaround-reporting-and-evidence.py capabilities

# plan only
python3 tools-turnaround-reporting-and-evidence.py plan \
  --input brief.md --output-dir run1

# execute (approval required for this spec)
python3 tools-turnaround-reporting-and-evidence.py run \
  --input brief.md --output-dir run1 --checkpoint \
  --approve "Publishing or external distribution" --format text

# re-check an existing folder
python3 tools-turnaround-reporting-and-evidence.py validate --output-dir run1
```

### 7.12 Failure and recovery loops

| Situation | Command | Result |
|---|---|---|
| Missing input | `... run --input nope.md --output-dir out` | `E_INPUT_MISSING`, exit `3`, nothing written |
| Unapproved risk | `... run --input brief.md --output-dir out` | `E_APPROVAL_REQUIRED`, exit `4`, `plan.json` untouched |
| Blank approval | `--approve "   "` | Still blocked: blank tokens do not satisfy a gate |
| Pathological input | `--max-input-bytes 1000` with a 5 KB file | `E_INPUT_TOO_LARGE`, exit `3` |
| Wasted run | re-run with `--force` | Folder replaced only when `--force` is given |
| Timeout | lower `--timeout` | `E_TIMEOUT`, exit `2`, remaining steps `cancelled` |

---

## 8. Safety and approval rules

### 8.1 What is enforced in code

1. **Approval gates are real.** `scaffold` returns exit `4` and writes nothing
   while an approval reason is unapproved; a generated `run` does the same. The
   decision is made from the specification text and the policy rules, not from
   an honour system. The self-test asserts both paths.
2. **Blank approvals never count.** `--approve ""` and `--approve "   "` are
   rejected for both the forge and generated tools.
3. **Risk levels with no named reason still gate.** An `elevated`/`high`
   specification with no matched rule requires an explicit
   `--approve "risk level elevated"` token.
4. **Notifications are printed.** `unify` and `scaffold` list every approval
   trigger and the matched rule id, so a human sees what they are approving.
5. **Nothing dangerous is implemented.** No delete, publish, deploy, payment,
   credential, or network action exists anywhere in this codebase. There is no
   `allow_command_provider` path enabled: arbitrary command execution is not
   implemented, so it cannot be misused.
6. **Writes are bounded.** Only `--out`/`--output-dir` is written. Inputs are
   read-only. `--force` is required to replace an existing folder.
7. **Inputs are validated before work.** Size, existence, type, encoding and
   emptiness are checked first; invalid input produces a structured error and
   no partial output.
8. **Timeouts and retries are bounded.** Operations run under a wall-clock
   budget; only transient I/O errors are retried, with exponential backoff, and
   every failure is reported with its step name and attempt number.

### 8.2 Default approval triggers (`asset-default-policy.json`)

| Rule id | Risk | Example match |
|---|---|---|
| `destructive` | high | delete, wipe, overwrite, drop table, force-push |
| `credentials` | high | credential, secret, api key, password, private key |
| `publishing` | high | publish, deploy, send email, go-live |
| `financial` | high | payment, invoice, refund, payroll, wire |
| `privileged-infrastructure` | high | production, terraform apply, kubectl apply, IAM, DNS |
| `sensitive-data` | elevated | personal data, PII, PHI, biometric, consent |
| `security-testing` | high | penetration, exploit, vulnerability scan, red team |
| `code-execution` | elevated | shell, subprocess, curl pipe, webhook |
| `legal-advice` | high | legal advice, contract clause, statute, litigation |
| `schema-change` | elevated | migration, alter table, breaking change |

Tune with `--policy`: add rules, change `require_approval_for_risk`, or raise
limits.

### 8.3 Rules for the operator

- Read `unify --format text` before approving anything; it lists what matched.
- Approve a **named reason**, in a human decision, not in a loop that approves
  everything blindly.
- Treat a generated folder as a **plan-and-validate** runtime. It does not
  fabricate the professional deliverable of a capability.
- Deleting a run is safe: only its `--output-dir` is affected.

---

## 9. Validation and tests actually performed

Run the whole suite yourself:

```sh
python3 tools-skillforge-selftest.py --verbose
```

Result on Python 3.13.16, Linux: **43 of 43 tests passed** (the count includes
the doctor check that only passes once this file exists).

| # | Test | Result |
|---|---|---|
| 1 | `help` runs and lists commands | pass |
| 2 | `capabilities` returns 8 commands as JSON | pass |
| 3 | `capabilities --catalog-list` reports 34 categories | pass |
| 4 | `doctor` reports a healthy environment | pass |
| 5 | `extract` reads the example spec: 5 items | pass |
| 6 | `extract` strips HTML tags, CSS and `data-*` (2 items, exact names) | pass |
| 7 | `extract` resolves a markdown heading category | pass |
| 8 | `extract --input -` reads stdin | pass |
| 9 | Missing input → `E_INPUT_MISSING`, exit 3 | pass |
| 10 | Empty input → `E_EMPTY_INPUT`, exit 3 | pass |
| 11 | Binary input → `E_BINARY_INPUT`, exit 3 | pass |
| 12 | Directory input rejected, exit 3 | pass |
| 13 | Input over `max_input_bytes` → `E_INPUT_TOO_LARGE` | pass |
| 14 | Unknown `--generator` rejected, exit 2 | pass |
| 15 | `unify` produces one spec with 5 capabilities | pass |
| 16 | `unify` detects the approval trigger in prose | pass |
| 17 | `--name` overrides the folder name | pass |
| 18 | Invalid `--name` rejected (`E_BAD_NAME`) | pass |
| 19 | `--catalog 7` yields 12 capabilities | pass |
| 20 | Unknown catalog number → `E_CATALOG_SELECTION` | pass |
| 21 | `scaffold` blocks unapproved high-risk build, exit 4 | pass |
| 22 | Blocked generation writes no folder | pass |
| 23 | Blank `--approve` does not satisfy the gate | pass |
| 24 | Approval matching is case-insensitive and unblocks | pass |
| 25 | `scaffold` writes and verifies a runnable folder | pass |
| 26 | Folder holds exactly the four required files | pass |
| 27 | Folder has no `SKILL.md` and no subfolder | pass |
| 28 | Generated tool executed during verification (≥6 steps) | pass |
| 29 | Package shape valid (folder at archive root) | pass |
| 30 | Existing folder protected without `--force` (`E_OUTPUT_EXISTS`) | pass |
| 31 | Generated tool runs and writes `report.md` + `plan.json` | pass |
| 32 | Report contains Steps, Checks and Declared artifacts sections | pass |
| 33 | Generated tool validates its own output folder | pass |
| 34 | Different input yields a different unified spec | pass |
| 35 | Matching input selects the `presentation-engineering` profile | pass |
| 36 | Archive opens and passes its CRC test | pass |
| 37 | Archive places the folder directly at its root | pass |
| 38 | Archive contains no nested archive | pass |
| 39 | Archive contains no `SKILL.md` | pass |
| 40 | Extracted tool runs from a clean skill root with relative paths | pass |
| 41 | Extracted tool completes a run using relative paths | pass |
| 42 | Clean root holds exactly one `SKILL.md`, outside the Skill Tool | pass |
| 43 | Every archive path is at most three levels deep | pass |

Every verification step runs the generated tool as a **subprocess** with `-B`
(no bytecode written), so the delivered folder stays clean and the check
exercises the real CLI, not an in-process function call.

Additional inspections performed by hand during development:

- The supplied `Aphelion_Agent_Skill_Catalog.pdf` (72 pages) was parsed with
  `pypdf`; its extracted text was fed to `extract` and produced exactly
  **408 items / 34 categories / 12 items per category**, matching the catalog's
  own claim of "34 categories | 408 skill concepts".
- A 408-capability unified build (`--name aphelion-full-catalog`) was generated,
  executed and validated: `capabilities ok (408)`, all checks pass.
- `--catalog 15` (`Databases and Data Engineering`) was gated, approved, built
  and executed: `capabilities ok (12)`, verification passed.
- One real defect was found and fixed during this work: the `plan_written`
  check ran before `plan.json` was written. Ordering was corrected and the
  suite now passes. Two further defects were found and fixed the same way: a
  duplicate module instance that turned a clean error into a traceback, and
  blank approval tokens accepted as approvals.
- `gzip`/`zipfile` integrity of the delivered archive was verified with
  `ZipFile.testzip()` in the suite, and the archive was extracted into a clean
  directory to re-run the tool.

Not performed, and not claimed: any test on Windows or macOS, any test with
Python 3.9–3.12 interpreters, and any test of the `node`, `shell` or `none`
generators, which emit specification files only and are labelled as such.

---

## 10. Limits and unavailable integrations

- **No LLM, no network, no connectors.** The forge is deterministic. It does not
  call a model, fetch a URL, or reach any service. The bundled catalog is a
  static dataset, not a live catalog.
- **Generated tools are plans, gates and validators — not doers.** A capability
  named "Financial Model Builder" appears in `capabilities`, gets a step, a stop
  condition and a quality check, and its declared artifact is reported as
  missing until real work fills it. The generated checks report which declared
  artifacts are present so the gap is explicit; they never create content.
- **`python` is the only executed generator.** `--generator node|shell|none`
  writes `knowledge-readme.md` and the manifest, and labels the folder as
  specification-only. No JavaScript or shell runner is implemented, and the
  tools do not claim otherwise.
- **The catalog dataset is a reference aid.** It carries the supplied catalog's
  categories, concepts and focus lines plus forge-added operating fields
  (domain, risk, artifacts, checks). It is not an implemented set of skills.
- **Sequential, single-process, single-machine.** No parallel execution, no
  distributed state, no database.
- **Timeouts are cooperative.** A run checks the budget between steps; an
  individual file write cannot be interrupted mid-call.
- **Extraction is heuristic.** Markers are recognised by pattern, and unusual
  layouts can mis-split (the suite covers the four documented shapes). Use
  `extract` first and check the reported items when a source is irregular.
- **No hosted resource is required or referenced.** Every reference in this
  document resolves inside this folder.

---

## 11. Troubleshooting and recovery

| Symptom | Cause | Fix |
|---|---|---|
| `E_MODULE_MISSING` | a `tools-*` file was moved or renamed | keep all four `tools-*` files in one folder |
| `E_ASSET_MISSING` | policy or dataset deleted | restore `asset-default-policy.json` / `asset-aphelion-catalog.json` |
| `E_ASSET_INVALID` | edited JSON has a syntax error | validate with `python3 -m json.tool asset-default-policy.json` |
| `E_POLICY_INVALID` / `E_POLICY_REGEX` | bad override shape or bad regex | fix the override; regexes are compiled with `re` |
| `E_INPUT_MISSING` | relative path from the wrong directory | use an absolute path or `--input -` |
| `E_ENCODING` | input is not text in a supported encoding | save as UTF-8 |
| `E_NO_CAPABILITIES` | source has no recognisable markers | add `1. Skill Name:` / `Description:` lines, or let the sentence fallback run |
| `E_TOO_MANY_ITEMS` | more items than `max_items` | raise `max_items` or split the input |
| `E_OUTPUT_EXISTS` | folder already present | re-run with `--force`, or change `--out` |
| `E_APPROVAL_REQUIRED` (exit 4) | risk rule matched | read the reported reasons, obtain human approval, re-run with `--approve "<reason>"` |
| `E_FILE_TOO_LARGE` | a rendered file exceeded `max_file_bytes` | reduce capabilities or raise the limit |
| `E_VERIFY_FAILED` | the generated tool did not run | a forge defect: re-run with `--no-verify` to inspect, keep the folder for diagnosis |
| `E_TIMEOUT` | budget exhausted | raise `--timeout` |
| exit 5 | a declared check failed | read `report.md`, fix the cause, re-run `validate` |

**Recovery.** Every run writes only inside its own `--output-dir`; delete that
folder to undo a run. With `--checkpoint`, `.forge-checkpoints.jsonl` (forge) or
`checkpoints.jsonl` (generated tool) records progress so a long run can be
reviewed or resumed by re-running with the same output directory. If generation
fails partway, `--force` rebuilds cleanly from the same input; the input is
never modified.

**Cancel.** Ctrl-C prints `E_CANCELLED` and exits `130`; no cleanup is required
beyond removing the run folder.

---

## 12. Extending and maintaining

**Add a capability to a generated tool.** Edit
`asset-<name>-manifest.json` → `capabilities`, then run `capabilities` again.
No code change is needed; the plan, checks and report pick the new entry up.

**Add or change a quality check.** Edit the `checks` array in the manifest, or
edit `SPEC_CHECKS` in `tools-<name>-checks.py`. Built-in checks live in
`BUILT_INS`; each is a function `(plan, out_dir, manifest) -> {name, status,
detail}` and a raising check is reported as a failure without hiding the others.

**Teach the forge a new domain.** Add a profile to `profiles` in
`asset-default-policy.json` with `keywords`, `domain`, `artifacts`, `checks` and
`risk`. A profile needs at least `profile_min_keywords` distinct matches to be
selected; matching a dataset category is authoritative.

**Change risk policy.** Add a rule to `rules`, or override
`require_approval_for_risk`. Rules are plain regexes over the specification
text; they are validated when loaded.

**Refresh the catalog dataset.** Re-extract the PDF text and rebuild
`asset-aphelion-catalog.json` keeping the same shape (`categories[]` with
`number`, `name`, `slug`, `focus`, `domain`, `risk`, `artifacts`, `checks`,
`skills[]`). `--catalog <n>` and category-driven enrichment keep working as long
as that shape holds.

**Add a generator.** Implement a render function alongside `render_folder` in
`tools-skillforge-generate.py`, extend `GENERATOR_EXTENSIONS` in
`tools-skillforge-lib.py`, and add a verification branch so the new output is
executed — then update the `generators` list in `capabilities` and delete the
"specification-only" note for it. Do not advertise a generator before its
verification branch exists.

**Maintenance loop.** After any edit: `python3 tools-skillforge.py doctor`
then `python3 tools-skillforge-selftest.py`. Both must pass before delivery.
Keep exactly one `knowledge-readme.md`, keep every file prefixed with `tools-`,
`knowledge-` or `asset-`, keep zero subfolders, and never add a `SKILL.md`
inside this folder.
