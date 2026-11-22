# accessibility-localization — unified Skill Tool

One deterministic, dependency-free tool that covers the whole **Accessibility and Localization**
capability umbrella described by the *Accessibility and Localization Skill Catalog*
(Catalog Edition 1.0, November 2026): **25 categories, 970 skill concepts**, routed onto
**77 executable operations** across five engines.

Every catalog skill resolves to a real operation, so a request such as "audit these captions"
or "check the Filipino terminology" becomes an executable command with reproducible evidence,
not a suggestion list.

```
capabilities → 25 categories, 970 skill concepts, 77 operations
routing      → 970/970 skills mapped to implemented operations
self-test    → 115 tests, 115 passed, 0 failed, 0 skipped (77/77 operations exercised)
```

---

## 1. Requirements and install

| Item | Value |
|---|---|
| Python | 3.8+ (verified on **CPython 3.13.16**) |
| Third-party packages | **none** — standard library only |
| Network access | **none required**; no endpoints are called |
| Secrets | **none embedded**; the tool never stores credentials |
| Install | copy the folder anywhere; run `python3 tools-a11yl10n.py …` from it |

There is no build step and no `pip install`. The tool locates its own assets (the seven
`asset-*.json` files) next to the script, so the flat folder is portable as-is.

```bash
cd accessibility-and-localization
python3 tools-a11yl10n.py doctor        # 11 environment checks
```

`doctor` verifies the Python version, the config, all five shipped assets, the catalog
(25/970), routing completeness, the operation registry (77 across 5 engines) and output
writability. It exits `0` when every check passes.

---

## 2. Five-minute quickstart

```bash
# 1. What is in the tool?
python3 tools-a11yl10n.py capabilities

# 2. Find a skill (by name, description or category)
python3 tools-a11yl10n.py search "caption quality"

# 3. Inspect a skill and the operation it routes to
python3 tools-a11yl10n.py describe 9.10
python3 tools-a11yl10n.py describe op:captions-audit

# 4. Scan a page and write JSON + Markdown evidence
python3 tools-a11yl10n.py run --skill 2.1 --input page.html --out out --format both

# 5. Prove the tool itself works
python3 tools-a11yl10n.py self-test --verbose
```

`run` prints a one-line human summary, writes nothing outside `--out`, and returns a
process exit code you can gate CI on (see §6).

---

## 3. Command reference

`python3 tools-a11yl10n.py <command> [options]`

| Command | What it does | Useful flags |
|---|---|---|
| `capabilities` | Category list with skill counts and the operations serving each category | `--json` |
| `list` | Every skill with its routed operation and route type (`rule`/`override`/`default`) | `--category N`, `--op NAME`, `--json` |
| `search` | Ranked skill search over name, description and category | `--limit N`, `--json` |
| `describe` | Full detail for a skill (`C02-S25`, `2.25`, slug, name) or an operation (`op:web-scan`, bare `web-scan`) | `--json` |
| `run` | Execute the operation a skill routes to | see §5 |
| `validate` | Pre-flight: skill resolution, parameter coercion, config, assets, approval need, input type/size/hash — **without executing** | same input flags as `run` |
| `doctor` | Environment, asset, catalog, routing and output checks | `--json` |
| `routing` | The skill → operation table; `--unimplemented` lists skills with no operation (currently none) | `--category N`, `--op NAME`, `--unimplemented`, `--json` |
| `self-test` | Fixture-based regression suite against every engine | `--verbose`, `--keep`, `--json` |

Global options: `--config PATH`, `--timeout SECONDS`, `--verbose`.

### `run` options

| Option | Meaning |
|---|---|
| `--skill` | Skill id (`C02-S25`), list number (`2.25`), slug or unique name |
| `--input` | Primary artifact (type is checked against the operation contract) |
| `--reference` | Secondary artifact: source bundle, baseline, TMX memory, etc. |
| `--set KEY=VALUE` | Operation parameter (repeatable; validated and type-coerced) |
| `--out DIR` | Output directory (default `config.output.dir`, i.e. `a11yl10n-out`) |
| `--format` | `both` \| `json` \| `md` \| `sarif` \| `junit` |
| `--dry-run` | Compute results and skip state-changing writes |
| `--approve` | Approve a state-changing/publishing operation (see §8) |
| `--no-write` | Print results, write nothing |
| `--json` / `--quiet` | Full result document / one-line JSON summary |
| `--fail-on-findings` | Exit `7` when blocking findings exist (CI behaviour) |
| `--timeout` | Override `limits.timeout_seconds` for this run (accepts `0` to force a deadline) |

---

## 4. Catalog coverage

Each category page in the catalog maps onto one primary engine; within a category, skills are
routed by name pattern first, then by an explicit override table, then by the category default.

| # | Category | Skills | Primary operations |
|---|---|---|---|
| 1 | Accessibility Standards, Governance, and Conformance | 40 | standards-map, conformance-report, policy-document, register, workflow, metrics-report, procurement-score, remediation-plan, maturity-assessment, severity-classify, claim-validate |
| 2 | Automated Web Accessibility Testing | 45 | web-scan, harness-config, dedupe, baseline, ci-gate |
| 3 | Screen Reader and Assistive Technology Testing | 44 | at-probe, at-script, at-matrix, at-findings |
| 4 | Keyboard, Focus, Switch, and Voice Input | 41 | web-scan, at-script, test-matrix |
| 5 | Visual Accessibility, Contrast, and Responsive Presentation | 44 | web-scan, contrast-check, token-audit |
| 6 | Motion, Animation, Flashing, and Sensory Safety | 27 | motion-audit, web-scan |
| 7 | Cognitive, Learning, Neurodiversity, and Plain Language | 35 | plain-language-audit, cognitive-support-audit |
| 8 | Images, Alt Text, Graphics, Charts, and Visual Media | 35 | alt-text-audit, chart-accessibility |
| 9 | Audio, Video, Captions, Subtitles, and Audio Description | 46 | captions-audit, caption-quality-gate, media-player-audit, audio-description-script |
| 10 | Accessible Documents, PDFs, and Office Files | 45 | document-audit, document-remediation-plan |
| 11 | Forms, Errors, Authentication, and Transactions | 36 | web-scan |
| 12 | Mobile, Native App, Desktop, Kiosk, and Device Accessibility | 31 | element-tree-audit, test-matrix |
| 13 | Localization Architecture and Internationalization Engineering | 47 | i18n-audit, locale-format-audit, hardcoded-scan, icu-validate, expansion-budget, translation-qa, coverage-matrix |
| 14 | Translation Memory, Terminology, and Language Assets | 38 | term-audit, tm-audit, tm-match |
| 15 | Translation, Transcreation, and Human Review | 44 | review-plan, translation-qa, translation-brief, mqm-score |
| 16 | Filipino, English, and Philippine Locale Support | 41 | filipino-review, philippine-locale-check, language-readiness, bidi-audit, font-coverage |
| 17 | Right-to-Left, Bidirectional, and Complex Scripts | 30 | rtl-layout-audit, bidi-audit, script-rendering |
| 18 | Fonts, Typography, Glyph Coverage, and Rendering | 34 | font-coverage, typography-audit, font-license |
| 19 | Pseudolocalization and Localization Testing | 36 | pseudolocalize, pseudo-audit |
| 20 | Content Design, Inclusive Language, and Cultural Adaptation | 31 | cultural-adaptation-audit, inclusive-language-audit, plain-language-audit |
| 21 | Accessibility and Localization for AI Systems | 35 | conversational-interface-audit, ai-output-audit, ai-translation-audit |
| 22 | Human Research, Usability, and Participatory Testing | 40 | research-plan, findings-synthesis, sus-score, review-plan, evidence-package |
| 23 | Quality Assurance, CI/CD, Metrics, and Reporting | 42 | test-plan, coverage-matrix, metrics-report, test-matrix, ci-gate, severity-classify, at-matrix, evidence-package, retention-schedule, baseline |
| 24 | Design Systems, Components, and Developer Enablement | 42 | component-contract-audit, token-audit, code-review-checklist, lint-config |
| 25 | Operations, Support, Procurement, and Lifecycle | 41 | operational-runbook, lifecycle-schedule, procurement-score, intake-triage, policy-document |

Verify at any time: `capabilities --json` reports `{"category_count": 25, "skill_count": 970}`,
and `routing --unimplemented` prints zero rows.

---

## 5. Operation reference (77 operations, 5 engines)

Accepted input kinds: `html`, `css`, `text`, `strings` (JSON/JSONL/PO-like bundles), `json`,
`jsonl`, `captions` (WebVTT/SRT), `tmx`, `tbx`, `font` (TTF/OTF), `document` (PDF/DOCX/EPUB).

### 5.1 Web engine — categories 2–6, 11, 12 · 14 operations · 70 rules in 15 rule groups

Rule groups: `structure`, `names`, `images`, `aria`, `keyboard`, `pointer`, `visual`, `motion`,
`forms`, `tables`, `media`, `messaging`, `i18n`, `consistency`, `meta`.

| Operation | Purpose | Accepts |
|---|---|---|
| `web-scan` | Deterministic WCAG 2.2 rule set over an HTML artifact; rule groups chosen by skill name, parameters or a full sweep for "WCAG scanner" skills; filtered by `conformance_level` | html |
| `harness-config` | Emit axe-core/pa11y/Lighthouse CI configuration aligned to the selected groups, plus an import contract for their JSON | html, json |
| `dedupe` | Normalise and deduplicate findings from axe/pa11y/Lighthouse/this tool into one stable list | json |
| `baseline` | Compare results with a stored baseline; report new / fixed / persisting findings | json (+ `--reference`) |
| `ci-gate` | Evaluate results against severity thresholds, count budgets and required coverage | json |
| `at-probe` | Expected name, role, description, states and announcement for every interactive element | html |
| `at-script` | Reproducible AT test script (NVDA, JAWS, VoiceOver, TalkBack, Narrator, Orca) with pinned environment and expected announcements | html |
| `at-matrix` | AT × browser × OS coverage matrix with risk weight and cost estimate | html, json |
| `at-findings` | Normalise recorded AT session notes into findings with severity and retest steps | json, jsonl |
| `element-tree-audit` | Audit a declared element tree (Android, UIKit, kiosk, web component) for names, roles, target size, focus order | json |
| `test-matrix` | Cross-browser/device/locale matrix with pinned versions, risk weighting and a time budget | json, html |
| `contrast-check` | WCAG contrast ratios for declared colour pairs, classified against text/large-text/non-text thresholds | html, css, json |
| `token-audit` | Design-token audit: colour pairings, typography scale, spacing, focus tokens | json |
| `motion-audit` | Flash frequency, infinite loops, autoplay, reduced-motion handling (WCAG 2.2.2 / 2.3.x) | html, css, json |

### 5.2 Media engine — categories 8, 9, 10 · 8 operations

| Operation | Purpose | Accepts |
|---|---|---|
| `alt-text-audit` | Alt presence, redundancy, file names, keyword stuffing, length, decorative handling, context fit | html, json |
| `chart-accessibility` | Chart name, long description or data-table alternative, colour-independence cues | html, json |
| `captions-audit` | WebVTT/SRT timing, overlaps, CPS, line length/count, minimum duration, speakers, structure | captions |
| `caption-quality-gate` | Accuracy/readability/coverage/completeness gate with failing cues listed | captions, json |
| `audio-description-script` | Build a description script and verify each line fits its gap at a sustainable rate | json |
| `media-player-audit` | Caption/description tracks, controls, autoplay, keyboard access, poster text, language metadata | html |
| `document-audit` | PDF tagging/language/title/alt/encryption; OOXML headings, alt text, tables; EPUB accessibility metadata | document |
| `document-remediation-plan` | Ordered remediation grouped by authoring-tool step with owners and retests | json |

### 5.3 Localization engine — categories 13–19 · 23 operations

| Operation | Purpose | Accepts |
|---|---|---|
| `i18n-audit` | Resource-bundle audit: ICU syntax, placeholder integrity, untranslated/empty/duplicate values, key naming, orphaned arguments | strings |
| `icu-validate` | ICU MessageFormat grammar, plural/select options, mandatory `other` branches | strings |
| `hardcoded-scan` | User-facing strings hard-coded in markup/JS, and unsafe concatenation | html, text |
| `locale-format-audit` | Dates, times, numbers, currency, percentages, phone numbers, addresses vs. the locale profile | text, html, strings, json |
| `expansion-budget` | Source/target length ratios vs. the locale expansion budget; truncation risk | json, strings |
| `tm-audit` | TMX 1.4 segment counts, language pairs, empty/duplicate segments, copy-source risk | tmx |
| `term-audit` | Termbase/glossary validation: missing translations, forbidden terms, inconsistent approved terms, do-not-translate hits | json, tbx |
| `tm-match` | Leverage analysis with exact/fuzzy/no-match bands per segment | json (+ `--reference` TMX) |
| `translation-qa` | Bilingual QA: placeholders, tags, numbers, untranslated, punctuation, whitespace, terminology, length anomalies | json |
| `mqm-score` | MQM-style severity-weighted quality score with pass/fail verdict | json |
| `review-plan` | Sampling size, reviewer roles, rounds, checklists and required evidence | json, text |
| `translation-brief` | Brief with audience, purpose, register, terminology, constraints, acceptance criteria | strings, json, text |
| `filipino-review` | Orthography (ng/nang, mga), loanwords, code-switching, formality, inclusive language, terminology | text, strings, json, html |
| `philippine-locale-check` | Date/time order, peso formatting, phone numbering, address and name order, government terminology | text, strings, json, html |
| `language-readiness` | Coverage, font proof, reviewer availability, profile presence, direction, blockers → go / conditional / no-go | json, strings |
| `bidi-audit` | Base direction, unbalanced embedding/isolate controls, mirroring, embedded numbers | text, strings, json, html |
| `rtl-layout-audit` | Physical CSS properties, `dir` declarations, icon mirroring, text alignment | html, css |
| `script-rendering` | PUA/unassigned characters, combining marks, presentation forms, CJK compatibility, normalisation | text, strings, json, html |
| `font-coverage` | Parses the font `cmap` and proves glyph coverage per script; tofu and fallback risk | font |
| `typography-audit` | Line height, letter/word spacing, line length, justification, all-caps runs, minimum size | html, css, text |
| `pseudolocalize` | Generate accented/expanded/bidi/mirrored bundles without touching placeholders | strings |
| `pseudo-audit` | Untranslated strings, placeholder corruption, markup damage, unbracketed strings, over-expansion | strings, json |
| `font-license` | Reads the font `name` table and `OS/2 fsType` to decide embedding permission | font |

### 5.4 Content engine — categories 7, 20, 21 · 7 operations

| Operation | Purpose | Accepts |
|---|---|---|
| `plain-language-audit` | Reading ease, grade level, sentence/word length, passive voice, nominalisations, jargon, all-caps, abbreviations | text, html, strings, json |
| `cognitive-support-audit` | Consistent help, error prevention, timeout warnings, form length, headings, instructions | html |
| `inclusive-language-audit` | Disability/person-first wording, gendered language, age bias, violent/colonial metaphors, unnecessary jargon | text, html, strings, json |
| `cultural-adaptation-audit` | Idioms, humour, gestures, colour meaning, holidays, names/addresses/currency examples, religious/regional references | text, html, strings, json |
| `ai-output-audit` | Gate model output: alt-text quality, reading level, glossary adherence, placeholder safety, locale formatting | json, text |
| `ai-translation-audit` | Gate MT output: placeholder integrity, glossary, untranslated segments, format consistency, length ratio, back-translation | json |
| `conversational-interface-audit` | Streaming announcements, focus handling, keyboard, errors, citations, human escalation | json, html |

### 5.5 Governance engine — categories 1, 22–25 · 25 operations

`standards-map`, `conformance-report`*, `policy-document`, `register`*, `workflow`,
`severity-classify`, `remediation-plan`, `maturity-assessment`, `claim-validate`,
`risk-register-seed`, `research-plan`, `sus-score`, `findings-synthesis`, `test-plan`,
`coverage-matrix`, `metrics-report`, `evidence-package`, `retention-schedule`,
`component-contract-audit`, `lint-config`, `code-review-checklist`, `procurement-score`,
`operational-runbook`, `lifecycle-schedule`, `intake-triage`.

`*` = approval-gated (see §8).

---

## 6. Exit codes

| Code | Meaning | Typical cause |
|---|---|---|
| 0 | Success | Operation completed; results written or printed |
| 1 | Internal error | Unexpected exception — the message includes a trace for reporting |
| 2 | Input/parameter error | Missing/unsupported artifact, bad `--set` value, wrong input kind, unreadable config |
| 3 | Approval required | State-changing or publishing operation without `--approve` |
| 4 | Not found / ambiguous | Unknown skill, ambiguous name, unimplemented route |
| 5 | Limit exceeded | File size, record count, finding count or text length over `limits.*` |
| 6 | Deadline exceeded | `limits.timeout_seconds` (or `--timeout`) reached — message says what was in flight |
| 7 | Gate failure | Blocking findings present with `--fail-on-findings`, or `ci-gate`/`claim-validate` failed |

Every error is a single JSON object: `{"error": {"code", "message", "hint"?, "details"?},
"exit_code": N}` — parseable, no stack traces on stdout.

---

## 7. Configuration, assets and limits

`asset-config.json` is the single config; `--config other.json` deep-merges over it.

| Key | Contents |
|---|---|
| `limits` | `max_file_bytes` 8 388 608 · `max_files` 200 · `max_records` 50 000 · `timeout_seconds` 120 · `read_retries` 2 · `max_findings` 5 000 · `max_text_chars` 4 000 000 |
| `gates` | `ci.fail_on = high`, `max_findings = 0` · `release.fail_on = critical`, `require_human_review = true` |
| `thresholds` | contrast (4.5 / 7.0 / 3.0 / 3.0), `min_target_size_css_px` 24, caption CPS 17, min caption 1.0 s, 42 chars/line, 2 lines, expansion budget 35 %, reading grade 9.0 (Filipino 10), min font size 12 px, register age 90 days, MQM pass 90.0 |
| `locale` | `default_locale = fil-PH`, `source_locale = en-PH`, supported list, `fallback_locale = en-PH`, note that fil-PH / tl-PH / en-PH are never interchangeable |
| `output` | `dir = a11yl10n-out`, `format = both`, `write_evidence_hashes = true` |
| `paths` | `baseline`, `register`, `evidence_dir` — empty by default so the tool never invents absolute paths |
| `approvals` | Which actions need `--approve`, and the rule behind it |
| `providers` | Empty and disabled: **no network integrations ship with the tool** |
| `severity_model`, `maturity_model`, `product`, `conformance_level`, `skill_tool` | Scoring inputs and identity used in reports |

Assets shipped beside the code (all prefixed `asset-`):

| File | Contents |
|---|---|
| `asset-catalog.json` | The full 25-category / 970-skill catalog with ids and slugs |
| `asset-wcag-2.2.json` | 87 WCAG 2.2 success criteria (32 A, 24 AA, 31 AAA) with titles, levels, principles and the 4.1.1 removal note |
| `asset-config.json` | Defaults described above |
| `asset-locale-profiles.json` | 8 locale profiles (en-PH, fil-PH, tl-PH, ceb-PH, ar, he, ja-JP, en-US): direction, script, required charset, formats, reviewer requirements |
| `asset-fil-en-terminology.json` | Filipino/English terminology: do-not-translate lists, jargon, ~23 approved/forbidden term pairs, inclusive language, cultural rules, accessibility vocabulary |
| `asset-mqm-typology.json` | MQM severity weights (critical 10 / major 5 / minor 1), 22 error categories, pass threshold, sampling defaults |
| `asset-at-test-matrix.json` | 8 assistive technologies, 12 AT × browser × OS combinations with tiers, minutes and risk, platform budgets, research instruments, accommodations |

### Limits, timeouts, retries and recovery

* Size/records/text/findings are checked **before** work starts and again at checkpoints.
* Every long loop calls `ctx.checkpoint()`; the deadline is enforced there and in file reads,
  so a run fails fast with exit `6` instead of hanging.
* File reads retry transient `OSError`s `read_retries` times with a short backoff.
* Writes are atomic (temp file + `os.replace`) so a crash never leaves a half-written result.
* Recovery is intentional and stateless: fix the input or raise the limit and re-run the same
  command. The self-test asserts this (`invalid JSON input returns 2 and is recoverable` →
  `recovery run succeeds after fixing the input`).
* No partial state is kept between runs except the two governance ledgers you explicitly
  write (`register_path`, evidence files), both hash-chained.

---

## 8. Approval gates and destructive actions

`--approve` is required for operations that persist governance state or publish claims:

| Operation | Gated modes | Why |
|---|---|---|
| `register` | `append`, `update` (read/verify are safe) | Appends to the hash-chained risk/debt/ownership/waiver/exception ledger |
| `conformance-report` | publishing a VPAT/ACR style report | External claim about conformance |
| `retention-schedule` / evidence deletion | purge actions | Deletes retained evidence |
| `pseudolocalize` and other bundle writers | `output_path` writes | Writes generated files to a caller-supplied path |

Without `--approve` the run stops with exit `3` and
`{"code": "approval_required", "details": {"action": "register(append)"}}`.
`--dry-run` computes everything and refuses the write, and is also the recommended way to
preview a gated operation.

---

## 9. Outputs and the evidence model

`--format json` writes the full result document; `md` a human report; `sarif` a SARIF 2.1.0
log; `junit` JUnit XML; `both` = json + md. `--format junit|sarif` is what CI consumes.

Result document (`schema: a11yl10n.result/1`):

```json
{
  "skill": {"id": "C02-S01", "name": "Automated WCAG Scanner", "slug": "…", "category": {…}},
  "op": "web-scan",
  "run": {"tool_version": "1.0.0", "started_at": "…Z", "duration_ms": 26, "status": "ok",
           "limits": {…}, "ruleset": {"catalog": "…", "edition": "Catalog Edition 1.0 (November 2026)",
                                      "operation": "web-scan", "engine": "web"},
           "inputs": [{"path": "page.html", "kind": "html", "bytes": 263, "sha256": "…"}]},
  "params": {…},
  "summary": {"findings": 8, "by_severity": {…}, "blocking": 3, "criteria_affected": {…},
               "checks_run": 26, "checks_passed": 18, "evidence_score": 0.69, "pass": false,
               "automated_claim_limit": "Automated checks do not establish conformance. …",
               "human_review_queue": […]},
  "findings": [{"id": "F0004", "rule": "image-alt", "check": "Images have alternative text",
                 "severity": "critical", "wcag": "1.1.1", "level": "A", "locator": "<img> (line 6)",
                 "message": "Image without an alt attribute (src='logo.png').",
                 "evidence": {…}, "remediation": "…", "retest": "…"}],
  "notes": [{"severity": "info", "message": "…"}],
  "extras": {…}
}
```

Every finding carries **artifact, locator, rule, criterion, severity, evidence, remediation and
retest** — the minimum an accessible defect report needs to be actionable and re-testable.
Input hashes and pinned ruleset/catalog/edition make each report reproducible.

`summary.automated_claim_limit` is present in every result: **an automated score is never
presented as conformance.** Human, AT and linguistic review stays in `human_review_queue`.

---

## 10. Standards and locale coverage

* **WCAG 2.2** (W3C Recommendation, 5 Oct 2023) as the primary rule set, including the new
  2.2 criteria (2.4.11, 2.4.12, 2.4.13, 2.5.7, 2.5.8, 3.2.6, 3.3.7, 3.3.8, 3.3.9) and the
  removal of 4.1.1 (still reportable for 2.0/2.1 policies).
* Mapped onward to **EN 301 549**, **Section 508**, **PDF/UA**, **EPUB Accessibility**,
  **VPAT 2.5 / ACR**, **ISO 30071-1**, **WAI-ARIA**, Unicode/CLDR/ICU, TMX 1.4, TBX and MQM.
* Philippine focus: **en-PH**, **fil-PH**, **tl-PH**, **ceb-PH** (plus ar, he, ja-JP, en-US),
  RA 7277 and RA 11106 (Filipino Sign Language) references, Filipino orthography and
  approved/forbidden terminology, and readiness assessment for Cebuano, Ilocano, Hiligaynon,
  Waray, Kapampangan, Pangasinan and Bikol.

---

## 11. Testing: what was actually run

```bash
python3 tools-a11yl10n.py self-test --verbose     # add --keep to inspect fixtures
```

The suite builds 44 fixtures in a throwaway temp dir (removed unless `--keep`), then runs
**115 checks**: 84 operation cases driven through the real CLI (`run … --json --no-write`), 28
CLI behaviour cases, plus determinism, routing-stability and a coverage gate that fails if any
registered operation is untested.

Last recorded run in this workspace:

```
------------------------------------------------------------------------------
total 115 | passed 115 | failed 0 | skipped 0 | 9.2s
operations exercised: 77 of 77 registered
RESULT: PASS
```

Also verified by hand in this workspace:

| Check | Result |
|---|---|
| `doctor` (11 checks) | all `ok: true`, exit 0 |
| `capabilities --json` | `{"category_count": 25, "skill_count": 970}` |
| `routing --unimplemented` | `0 row(s); 77 implemented operations available` |
| End-to-end scan of a real page | 8 findings (3 critical), JSON + Markdown written, exit 0 |
| SARIF 2.1.0 export | valid JSON, 8 results, version 2.1.0 |
| JUnit export | well-formed XML |
| Oversized input (9 MB vs 8 MB limit) | exit 5 with `limit_exceeded` + `details` |
| `--config` with a lower size limit and custom output dir | limit honoured (exit 5), output written to the custom dir |
| Missing config / unknown skill / missing input | exit 2 / 4 / 2 with structured errors |
| `--timeout 0` | exit 6, recoverable |
| Approval-gated register append without `--approve` | exit 3; with `--approve` the JSONL ledger is written and hash-verified |
| Secrets review of all 19 files | none found |
| Fresh unzip of the delivered ZIP into an empty directory | `doctor` ok on all 11 checks; self-test 115/115 passed — no hidden dependency on the build machine |

Cases whose fixture needs an unavailable resource are marked `skipped` rather than failing
(e.g. no `.ttf` on the host for `font-coverage`); on this machine the system DejaVu font was
found, so both font operations ran for real instead of being skipped.

---

## 12. Known limitations (read before relying on results)

1. **Automated ≠ conformant.** Every report says so explicitly. Rule engines are a subset of
   WCAG and cannot judge purpose, meaning, alt-text quality or cognitive load.
2. **Declared inputs only.** The tool parses artifacts; it does not launch a browser, so
   computed styles, hover/focus rendering and live-region timing are out of scope — use
   `at-probe`, `at-script` and `harness-config` to drive those checks in a real browser.
3. **Font binary support** is TTF/OTF (sfnt `cmap`, `name`, `OS/2`). WOFF/WOFF2 and `.ttc`
   collections are rejected with a clear `InputError` rather than guessed at.
4. **`web-scan` locale matching is opt-in**: pass `--set expected_lang=fil-PH`. It is not
   inferred from config, because a page may legitimately serve any supported locale.
5. **Report anchors** in `asset-wcag-2.2.json` are derived from criterion titles — verify deep
   links against the current W3C document before publishing them externally.
6. **Rule sets are opinionated.** Thresholds live in `asset-config.json`; adjust them to your
   policy instead of editing engine code.
7. **Single-artifact scope.** Site-wide crawl orchestration is deliberately out of scope; feed
   per-page results to `metrics-report`, `coverage-matrix` or `baseline` to aggregate.
8. **No network integrations.** `providers.external_scanners` is empty by design; add a local
   command only if your environment provides one.

---

## 13. Extending the tool

Loading is dynamic (`importlib` with prefixed module names, because filenames contain hyphens),
so extensions are additive:

```python
# tools-engines-web.py (or any engine)
@rule("my-check", "Short check label", "forms", "3.3.1", "A", "high",
      "What to change.", "How to retest.")
def r_my_check(ctx, doc, r):
    for node in doc.by_tag("input"):
        if node.get("aria-invalid") and not node.get("aria-describedby"):
            fail(ctx, r, "Invalid control with no described-by error text.", node)
        else:
            ctx.pass_check()

# New operation:
@op("my-op", "One paragraph on what it does and returns.",
    params={"mode": {"type": "enum", "values": ["a", "b"], "default": "a", "doc": "…"}},
    accepts=("json",), approval=False)
def run_my_op(ctx):
    ctx.meta("rows", 0)
```

Checklist for a new rule or operation:

1. Register it with `@rule(...)` / `@op(...)` inside an engine (never a new file).
2. Add a `CASES` entry to `tools-selftest.py` with a real fixture and expected exit,
   findings count, rule ids and `extras` keys.
3. Run `self-test`; the coverage gate fails until the new operation is exercised.
4. If it changes a start-of-run contract, update `describe` output expectations and this file.

Because `ROUTES`/`OVERRIDES` in `tools-catalog.py` decide which catalog skills reach an
operation, a new operation also needs at least one skill routed to it — `routing` shows any
orphan, and the self-test fails on orphans.

---

## 14. File inventory (19 files, flat, no subfolders)

| File | Role |
|---|---|
| `tools-a11yl10n.py` | Primary CLI: capabilities/list/search/describe/run/validate/doctor/routing/self-test |
| `tools-catalog.py` | Catalog loader, regex routing table, override table, resolution with hints, summaries |
| `tools-support.py` | Errors, config validation, bounded atomic IO, limits/deadline, finding model, Markdown/SARIF/JUnit renderers, result assembly, context |
| `tools-text.py` | Segmentation, readability, script/bidi analysis, ICU MessageFormat parser, similarity, term matching |
| `tools-html.py` | HTML/CSS layer: node tree with line locators, roles, focusability, accessible names, headings, colour math |
| `tools-engines-web.py` | Categories 2–6, 11, 12 — 14 operations, 70 rules |
| `tools-engines-media.py` | Categories 8, 9, 10 — 8 operations |
| `tools-engines-l10n.py` | Categories 13–19 — 23 operations, incl. font binary parsing |
| `tools-engines-content.py` | Categories 7, 20, 21 — 7 operations |
| `tools-engines-gov.py` | Categories 1, 22–25 — 25 operations, incl. hash-chained register |
| `tools-selftest.py` | Fixture generator + 115-check regression suite + coverage gate |
| `asset-catalog.json` | 25 categories / 970 skills |
| `asset-wcag-2.2.json` | 87 WCAG 2.2 success criteria |
| `asset-config.json` | Defaults: limits, thresholds, gates, locales, output, approvals |
| `asset-locale-profiles.json` | 8 locale profiles with formats and reviewer requirements |
| `asset-fil-en-terminology.json` | Filipino/English terminology, inclusive language, cultural rules |
| `asset-mqm-typology.json` | MQM severity weights, 22 categories, pass threshold |
| `asset-at-test-matrix.json` | 8 ATs, 12 combinations, budgets, instruments, accommodations |
| `knowledge-readme.md` | This document: usage, contracts, coverage, verification, limitations |

---

## 15. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `exit 4`, "No skill matches" | Use `capabilities` / `search`; ids look like `C02-S25`, list numbers like `2.25` |
| `exit 2`, "Input type '.json' is not usable" | The operation's `accepts` list is printed in the error and in `describe op:<name>` |
| `exit 3`, `approval_required` | Human review first, then re-run the identical command with `--approve` |
| `exit 5` on a big file | Raise `limits.max_file_bytes` in your config, or split the artifact |
| `exit 6` | Raise `--timeout`/`limits.timeout_seconds`; the error names the step in flight |
| `exit 7` in CI | Expected: blocking findings, or a gate policy that you set in `gates.ci` |
| `doctor` says config `ok: false` | The `--config` path does not exist — omit it or fix the path |
| Nothing written to disk | You passed `--no-write` or `--dry-run`; otherwise check `--out`/`output.dir` |
| `font-coverage` says WOFF2 unsupported | Convert to TTF/OTF, or decompress the WOFF2 first |

---

*Licence-free, self-contained, and built to be handed to a team: one folder, one entry point,
one config, seven assets, and a test suite that keeps the 970-skill promise honest.*
