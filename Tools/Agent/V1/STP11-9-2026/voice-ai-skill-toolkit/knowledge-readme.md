# Voice AI Skill Toolkit — Knowledge and Operations Guide

Audience: Copilot agents and human operators working inside a skill package.
This file documents the complete capability, setup, inputs/outputs, safety
rules, tests performed, limits, and recovery procedures for the
`voice-ai-skill-toolkit/` Skill Tool folder. It contains no secrets and makes
no network calls.

---

## 1. Purpose

The Voice AI Skill Catalog defines 500 reusable voice-AI skills across 50
categories (speech production through delivery and recovery). Every catalog
skill describes the same production contract:

> Defines inputs and constraints, produces editable and traceable outputs,
> supports human review, and validates intelligibility, naturalness, latency,
> privacy, consent, technical quality, accessibility, and delivery
> requirements appropriate to the task.

This toolkit implements that contract as one unified, runnable production tool.
It is **not** a text-to-speech or ASR engine and does not synthesize or
transcribe audio by itself; it is the working layer around voice production:
catalog navigation, production briefs, work-package scaffolding, deterministic
measurement (WAV analysis), transcript and consent validation, whole-package
QA, and sign-off reporting — for any of the 500 catalog skills.

Source material: the Voice AI Skill Catalog document supplied with the
request (50 categories × 10 skills = 500 skills; descriptions preserved
verbatim in `voice-ai-skill-toolkit/asset-voice-skill-catalog.json`).

### What it is not

- Not a runtime for neural TTS/ASR models and not a model host.
- Not a publishing, payment, credential, or distribution system. Those actions
  are out of scope by design and always require separate human approval.
- Not a substitute for human listening review, legal review, or consent.

---

## 2. Capability list

Run `python3 tools-voice-skill-tool.py capabilities` for the live list.

| Command | Capability |
|---|---|
| `categories` | List all 50 catalog categories with coverage text and risk tier |
| `list` | List skills; filter by `--category` (id or name) and `--tier` |
| `search` | Keyword search across all 500 skills (name, focus, description) |
| `show` | Full catalog entry for one skill (slug, number, focus, description, tier) |
| `brief` | Render a production brief/work order for any skill from the template |
| `init-package` | Scaffold a complete work-package folder (manifest, brief, templates; consent record for elevated tiers; lexicon for pronunciation skills) |
| `audio-check` | Measure a PCM WAV (format, duration, peak/RMS/crest dBFS, DC offset, clipping %, silence %) and check it against a delivery profile |
| `transcript-check` | Validate SRT / VTT / JSON transcripts (syntax, ordering, overlaps, CPS, WPM) |
| `consent-check` | Validate a voice consent record (required fields, disclosures, retention, revocation, dates) |
| `validate-package` | Whole-package validation: manifest schema, catalog resolution, approvals, consent gate, declared artifacts, embedded audio/transcript checks. Exit 3 on FAIL |
| `qa-report` | Render a Markdown QA / handoff report with a human sign-off block. Exit 3 on FAIL |
| `selftest` | 23-check end-to-end test, no network, temp directory, self-cleaning |
| `capabilities` | Machine-readable capability list (`--json`) |
| `version` | Toolkit and catalog versions |
| `help` | Full command reference |

---

## 3. Setup and dependencies

**Runtime:** Python 3.9+ (developed and tested on CPython 3.13.16). Standard
library only — there is nothing to `pip install`.

**Optional:** `ffmpeg` on `PATH` (or `--ffmpeg /path/to/ffmpeg`) adds true
integrated LUFS and true-peak measurement via the `ebur128` filter in
`audio-check`, `validate-package` and `qa-report`. Without ffmpeg those
measurements are skipped explicitly (marked `SKIPPED` / warnings), never
guessed. Everything else works without it. ffmpeg was **not available in the
build environment**, so the ffmpeg code path is implemented but untested —
see Section 11.

**Optional system check:** `python3 --version`

### Exact commands

From the folder containing this file's parent (the future skill root):

```sh
python3 voice-ai-skill-toolkit/tools-voice-skill-tool.py help
python3 voice-ai-skill-toolkit/tools-voice-skill-tool.py capabilities
python3 voice-ai-skill-toolkit/tools-voice-skill-tool.py selftest
```

From inside the Skill Tool folder:

```sh
cd voice-ai-skill-toolkit
python3 tools-voice-skill-tool.py help
```

All commands are non-interactive, scriptable, and honor `--json` for
machine-readable output (except `selftest`, which prints text; `qa-report`
prints the report or writes `--output`).

---

## 4. Folder layout and file roles

```
voice-ai-skill-toolkit/
├── tools-voice-skill-tool.py     ← PRIMARY ENTRY POINT (CLI, all commands)
├── tools-common.py               runtime helpers: asset loading, exit codes, JSON/text output, file guards
├── tools-catalog.py              catalog access: load, resolve categories/skills, search, capability index
├── tools-brief.py                brief rendering + work-package scaffolding (brief, init-package)
├── tools-audiocheck.py           PCM WAV analysis + delivery-profile conformance + optional ffmpeg ebur128
├── tools-validate.py             JSON-Schema-subset validator, transcript/consent/package validation, qa-report renderer
├── tools-selftest.py             23-check end-to-end self test (invoked via `selftest`)
├── knowledge-readme.md           this file
├── asset-voice-skill-catalog.json    all 500 skills + 50 categories (verbatim descriptions), tiers, quality dimensions
├── asset-delivery-profiles.json      10 delivery/loudness profiles (broadcast, podcast, ACX-style audiobook, IVR, TTS, assistant, accessibility, clinical, voicemail)
├── asset-work-package-schema.json    JSON Schema (draft-07) for work-package.json manifests
├── asset-consent-record-template.json  consent record template (copy into elevated packages)
├── asset-direction-presets.json      12 voice-direction presets for briefs (pacing, emotion, articulation)
├── asset-brief-template.md           brief template with {{TOKEN}} placeholders substituted by `brief`
├── asset-safety-policy.md            consent/safety/governance policy the gates enforce
└── asset-sample-lexicon.txt          ARPABET sample lexicon (fixture/reference for pronunciation skills)
```

Execution order when running any command:
`tools-voice-skill-tool.py` → `tools-common.py` (assets, guards) → the one
sibling module implementing the command (`tools-catalog.py`, `tools-brief.py`,
`tools-audiocheck.py` and/or `tools-validate.py`). Modules share a single
instance of `tools-common.py` through `sys.modules` so exception classes keep
one identity. No install step, no build step, no network.

All internal asset paths are resolved relative to the tool's own location at
runtime (via `__file__`), so the folder works beside any `SKILL.md` without
modification. All references in this document are relative to the future skill
root, e.g. `voice-ai-skill-toolkit/asset-voice-skill-catalog.json`.

---

## 5. Inputs, outputs and parameters

### 5.1 Command: `categories [--json]`

Lists the 50 categories. Output per category: id, name, covers, tier, skill count.

### 5.2 Command: `list [--category X] [--tier Y] [--json]`

- `--category`: numeric id (`2`) or unique name substring (`text-norm`…
  case-insensitive substring). Unknown/ambiguous → exit 2.
- `--tier`: `standard` | `review` | `elevated`.

Text output: aligned table (`number`, `id`, `name`, `tier`). JSON: `{"ok": true,
"count": N, "skills": [...]}`.

### 5.3 Command: `search QUERY [--category X] [--tier Y] [--limit N] [--json]`

Substring match over name, id, number, focus, category and description.
Default limit 25. `total_matches` reports the full match count before limiting.

### 5.4 Command: `show SKILL [--json]`

`SKILL` resolution order: exact slug (`number-verbalization-engine`) → catalog
number (`2.3`) → case-insensitive exact name → unique substring. Ambiguous or
unknown queries print candidates and exit 2.

### 5.5 Command: `brief --skill SKILL [options] [--output FILE] [--json]`

Renders the complete production brief (sections: task definition, inputs and
constraints, source catalog entry, construction-pattern workflow, acceptance
criteria mapped to the 8 quality dimensions, consent/approval gate, traceable
deliverables, recovery, sign-off table).

Options: `--objective TEXT`, `--language TAG` (default `en`), `--locale SUB`,
`--preset ID` (from `asset-direction-presets.json`: `calm-narration`,
`warm-conversational`, `broadcast-news`, `ivr-friendly`, `energetic-promo`,
`empathetic-support`, `authoritative-legal`, `clinical-clear`,
`childrens-gentle`, `tutorial-instructor`, `sports-play-by-play`,
`accessibility-screen-reader`), `--delivery-profile ID` (from
`asset-delivery-profiles.json`: `broadcast-r128`, `atsc-a85`, `podcast`,
`audiobook-acx`, `ivr-telephony`, `web-tts`, `voice-assistant`,
`accessibility-screen-reader`, `clinical-speech`, `voicemail`),
`--sample-rate HZ`, `--channels N`, `--speaker-auth TEXT`, `--requester TEXT`,
`--tier-override standard|review|elevated`.

Output: stdout, or `--output FILE` (parent directory must exist; exit 2
otherwise). The tool fails loudly (internal error) rather than emit a brief
containing unresolved template tokens.

### 5.6 Command: `init-package --skill SKILL --dir PATH [brief options] [--title T] [--package-id ID] [--force] [--json]`

Scaffolds a work-package folder:

| File | Always? | Purpose |
|---|---|---|
| `work-package.json` | yes | manifest conforming to `asset-work-package-schema.json` (status `draft`, empty inputs/outputs/approvals) |
| `brief.md` | yes | the rendered production brief |
| `normalized-text.txt` | yes | editable normalized-speech script scaffold |
| `transcript.srt` | yes | one-cue starter transcript (replace before delivery) |
| `consent-record.json` | elevated tier only | copy of `asset-consent-record-template.json` to fill in |
| `lexicon.txt` | pronunciation-family skills only | copy of `asset-sample-lexicon.txt` |

Safety: refuses to write into a non-empty directory unless `--force`; even
with `--force` only the tool-managed files above are overwritten — unknown
user files are never deleted or modified. `--package-id` must match
`^[a-z0-9][a-z0-9-]{2,63}$`; the default is derived from skill slug + date.

### 5.7 Command: `audio-check FILE.wav [--profile ID] [--ffmpeg PATH] [--timeout SEC] [--json]`

PCM WAV only: 8-bit unsigned, 16/24/32-bit signed little-endian PCM.
Measures: `duration_seconds`, `sample_rate`, `channels`, `sample_width_bytes`,
`peak_dbfs`, `rms_dbfs`, `crest_factor_db`, `dc_offset` (fraction of full
scale), `clipped_percent`, `silence_percent` (samples below −50 dBFS),
`zero_crossings`. With ffmpeg available and `--profile` set (or explicit
`--ffmpeg`), also `integrated_lufs` and `true_peak_dbtp` via ebur128.

Profile checks (see `asset-delivery-profiles.json` for field semantics):
sample rate, channels, sample peak, true peak (ffmpeg), integrated loudness
± tolerance (ffmpeg), ACX-style RMS window (stdlib). Verdicts: `fail` on
format/rate/channel/limit failures or clipping > 1 %; `warn` on minor clipping,
silence > 60 %, DC offset > 0.5 %FS; else `pass`. `--json` exits 3 on verdict
`fail`.

Limits: ≤ 512 MiB file, single pass, bounded memory. 24-bit decoding is a
pure-Python loop — expect seconds for very long files. Non-PCM containers
(float WAV, MP3, M4A) are rejected with exit 2 and a conversion hint; ffmpeg
is the supported converter.

### 5.8 Command: `transcript-check FILE [--json]`

Formats: `.srt`, `.vtt` (WEBVTT header optional, `MM:SS.mmm` and
`HH:MM:SS.mmm`), `.json` (`[{start,end,text}, …]`, seconds as numbers, or
`{"segments": [...]}`). Checks: timing syntax, `end > start`, non-negative
timestamps, monotonic starts (warn), overlaps (warn), empty cues (warn),
reading speed > 21 CPS (warn). Reports `cue_count`, `word_count`,
`span_seconds`, `wpm`. Verdict `fail` on any parse/time error, `warn` on
style issues, else `pass`. Limit 32 MiB.

### 5.9 Command: `consent-check FILE.json [--json]`

Validates against `asset-consent-record-template.json` semantics:
`voice_owner_name`, `signed_by`, `signature_date` (valid ISO date), `scope`
(non-empty `use_cases`; valid `term_start`/`term_end` with end ≥ start),
`disclosures` (every flag must be `true`; `ai_generated_voice_disclosed` and
`synthesis_or_cloning_disclosed` mandatory), `data_handling` (`retention_days`
integer ≥ 1; `deletion_on_revocation` warn-true), `revocation` (`contact`,
`process` required; `lead_time_days` integer ≥ 0), witness name (warn).
Verdict `fail` if any required element fails; `warn` for advisory gaps; else
`pass`. Exit 3 on `fail`.

### 5.10 Command: `validate-package DIR [--profile ID] [--ffmpeg PATH] [--timeout SEC] [--json]`

Runs, in order:

1. `work-package.json` presence and schema validation (draft-07 subset:
   type/required/properties/items/enum/pattern/length/range/`#/definitions`
   `$ref`).
2. `skill_id` resolution against the catalog; derives tier (or honors
   `--tier`-overridden `tier`/`consent_required` fields in the manifest).
3. Approval consistency: status `approved|delivered|archived` requires ≥ 1
   approval with `decision=approved`; otherwise warn/fail.
4. Consent gate: package needs `consent-record.json` when tier is `elevated`
   or `consent_required` is true; the record is validated with the same rules
   as `consent-check`. Missing or invalid → fail.
5. Declared `outputs`: existence on disk (paths must stay inside the package
   directory — escapes fail), and embedded checks: `.wav`/audio outputs run
   `audio-check` (profile from `--profile` or manifest `delivery_profile`);
   `.srt`/`.vtt`/`.json` transcripts run `transcript-check`; other files are
   existence-checked only.

Verdict `fail` propagates out with exit 3. Path escapes, unreadable manifests,
and unknown skills are `fail`. An output declared with no `path` is a `warn`.

### 5.11 Command: `qa-report DIR [--output FILE] [--profile ID] [--ffmpeg PATH] [--json]`

Runs `validate-package` internally, then writes a Markdown report: package
header (skill, tier, status, language, verdict), a checks table, a human
review checklist (listening, intelligibility, naturalness, technical
measurement, consent/privacy, accessibility, delivery spec, clean-state
handoff), and a sign-off table. With `--output`, writes the file (parent must
exist) and prints the verdict; without, prints to stdout. Exit 3 on `fail`.

### 5.12 Command: `selftest [--keep]`

Builds temporary fixtures (1.0 s 440 Hz 16 kHz mono sine WAV, a full-scale
clipping WAV, valid/invalid SRTs, valid/invalid consent records, scaffold
packages for a standard and an elevated skill, completed standard package with
real artifacts) and drives the real CLI via subprocess. 23 checks must pass;
exit 0/1. `--keep` preserves the temp directory for inspection. Tested and
passing on the build host (see Section 10).

### 5.13 Exit codes

`0` success · `1` unexpected internal error (set `VOICE_TOOL_DEBUG=1` for a
traceback) · `2` invalid input/usage · `3` checks ran and the verdict is FAIL
(`audio-check`, `transcript-check`, `consent-check`, `validate-package`,
`qa-report`) · `130` cancelled (Ctrl-C).

---

## 6. Worked example

```sh
cd voice-ai-skill-toolkit
python3 tools-voice-skill-tool.py brief --skill 2.3 --preset ivr-friendly \
    --delivery-profile ivr-telephony --requester "ops-team" --output /tmp/brief.md
python3 tools-voice-skill-tool.py init-package --skill 2.3 --dir /tmp/pkg \
    --requester "ops-team" --title "IVR booking line"
# … edit /tmp/pkg/normalized-text.txt with the real script,
#   record/synthesize audio as output.wav, write final transcript.srt,
#   set outputs/approvals/status in work-package.json …
python3 tools-voice-skill-tool.py validate-package /tmp/pkg
python3 tools-voice-skill-tool.py qa-report /tmp/pkg --output /tmp/pkg/qa-report.md
```

For an elevated skill (e.g. `--skill 10.1` cloning governance, `--skill 37.x`
biometrics), `init-package` additionally writes `consent-record.json`;
`validate-package` refuses to pass until the record is complete and signed and
an approval with `decision=approved` is recorded. See
`asset-safety-policy.md`.

---

## 7. Configuration

No environment variables are required. One optional: `VOICE_TOOL_DEBUG=1`
(re-raise unexpected exceptions with tracebacks; for development). Behavior is
configured through command flags only — there are no hidden global config
files, no caches, no state outside the target directories you pass in.

`asset-delivery-profiles.json`, `asset-direction-presets.json` and
`asset-work-package-schema.json` are plain JSON and may be extended by
authorized maintainers (keep the `id` fields unique; keep the schema a
draft-07 subset the validator understands; keep preset ids lowercase).

---

## 8. Safety and approval rules

1. **Consent first.** Voice cloning, dataset capture/curation, healthcare,
   legal/evidentiary and biometric work (catalog categories 10, 11, 12, 35,
   36, 37 — the toolkit's `elevated` tier) requires a complete, signed consent
   record and human approval. `validate-package` fails otherwise. `review`
   tier (9, 14, 24, 26, 33, 38, 39, 47) demands a documented reviewer before
   delivery. Tiers are a built-in heuristic encoded in
   `asset-voice-skill-catalog.json`; override per brief/package with
   `--tier-override` only with a recorded justification.
2. **No impersonation or deception.** The toolkit must not be used to clone or
   synthesize real people's voices without authorization, to impersonate, or
   to misrepresent AI output as human where disclosure is required.
   `asset-safety-policy.md` is binding.
3. **Human review is part of the definition of done.** A tool PASS is
   evidence for reviewers, never a release decision.
4. **Approval-gated side effects.** This toolkit performs none: it never
   publishes, posts, distributes, pays, sends messages, edits credentials or
   deletes unknown files. Publishing/financial/credential actions require
   separate human approval through your normal process.
5. **Privacy.** Do not put personal data beyond the task's need into
   manifests/briefs/reports. Voice data and embeddings are personal data —
   encrypt where offered, minimize retention, honor revocation within the
   recorded lead time.
6. **Bounded execution.** Inputs are size-capped; JSON is validated before
   use; package-relative paths may not escape the package directory; ffmpeg is
   invoked with a 120 s default timeout and no shell; there is no `eval`, no
   network, no plugin system. Arbitrary code execution is impossible through
   any documented input.
7. **Clinical/legal scope.** Healthcare and legal capabilities are workflow
   and documentation support only — non-diagnostic, non-advisory; route to
   licensed professionals.

---

## 9. Tier model and quality dimensions

Quality dimensions embedded in the catalog (and in brief Section 5):
`intelligibility`, `naturalness`, `latency`, `privacy`, `consent`,
`technical-quality`, `accessibility`, `delivery`. Every brief requires an
explicit per-dimension review before sign-off.

Tiers (`asset-voice-skill-catalog.json`): `elevated` (categories 10, 11, 12,
35, 36, 37 — consent + approval mandatory), `review` (9, 14, 24, 26, 33, 38,
39, 47 — human review strongly recommended), `standard` (everything else).

---

## 10. Validation and tests actually completed

Environment: Linux, CPython 3.13.16, no ffmpeg installed, no network used.

- `selftest`: **23/23 checks pass** — help/capabilities/categories/list/
  search/show (incl. unknown-skill exit 2), brief rendering (no unresolved
  tokens), WAV measurement (sine pass, clipping fail, profile rate-mismatch
  fail), transcript accept/reject, consent accept/reject, package scaffolding
  (standard and elevated), empty-draft validation behavior, completed-package
  PASS, elevated consent-gate enforcement, qa-report rendering.
- Manual end-to-end runs: brief for an elevated skill (consent gate text
  verified), elevated package scaffolded and completed with a filled consent
  record and approval → `validate-package` PASS; unknown skill in manifest →
  `fail`, exit 3; `qa-report` renders correct Markdown; explicit
  `--ffmpeg /nonexistent` → exit 2; 8/16/24-bit PCM decoding verified against
  generated files; VTT transcripts verified; ambiguous skill query prints
  candidates.
- Failure-path tests: missing files (exit 2), invalid JSON/SRT, backwards cue
  times, incomplete consent record, blank elevated-tier consent template
  (blocked), package path escaping (blocked by code path — see Section 12).

Not tested / unavailable: the ffmpeg ebur128 path (ffmpeg not installed in
build environment; implementation present, optionally configurable via
`--ffmpeg`); WAV > memory-bounded chunks beyond ~tens of minutes of 24-bit
audio (slow but functional); non-PCM formats (by design — rejected). No claim
is made beyond the above.

---

## 11. Limits and unavailable integrations

- No network access, no external APIs, no credentials. Provider integrations
  (ElevenLabs, Azure Speech, AWS Polly, Google Cloud TTS/STT, etc.) are
  **not** implemented and intentionally absent — briefs name delivery targets
  and constraints; provider contracts are inputs you supply, not calls the
  tool makes.
- ffmpeg ebur128 measurement unverified on this host (absent). All other
  measurements are pure stdlib PCM analysis.
- LUFS without ffmpeg is not estimated as a number on purpose: `rms_dbfs` is
  reported and labeled as unweighted RMS; loudness-critical deliveries should
  run with ffmpeg or a calibrated meter.
- JSON-Schema support is a deliberate subset (see 5.10), sufficient for
  `asset-work-package-schema.json`; it is not a general draft-07 validator.
- Transcript WPM/CPS thresholds (21 CPS, monotonicity, overlap) are advisory
  heuristics for English-ish reading rates; adjust expectations for other
  languages and for verbatim reading.
- Delivery profile loudness targets are vendor-neutral defaults; always
  confirm the binding spec in your delivery contract.

## 12. Troubleshooting and recovery

| Symptom | Likely cause | Recovery |
|---|---|---|
| `error: Unknown skill ...` | slug/number typo | `search` or `list --category N` to find the id |
| `error: Ambiguous skill ...` | substring matches several | pick one from the candidate list shown |
| `Not a PCM WAV file ...` | float/compressed WAV, MP3/M4A | `ffmpeg -i in -c:a pcm_s16le out.wav` then re-check |
| `manifest declares no outputs` (warn) | fresh scaffold | add real outputs to `work-package.json` once produced |
| exit 3 from `validate-package` | one or more FAIL checks | fix the named artifact; re-run — never edit the verdict by hand |
| consent gate FAIL on scaffold | template `consent-record.json` is intentionally blank | fill every field, run `consent-check`, then `validate-package` |
| `output path escapes the package directory` | absolute path or `../` in manifest | use package-relative paths only |
| brief contains `{{TOKEN}}` error | template drift | restore `asset-brief-template.md` from the package; tokens must match `build_brief`'s map exactly |
| `set VOICE_TOOL_DEBUG=1` message for an unexpected error | genuine bug | rerun with the env var and attach the traceback to the maintainer |
| deliverable corrupted after handoff defect | — | open an incident, quarantine derived artifacts (esp. consent-derived models), re-issue from the last approved `work-package.json` |

Recovery principles: originals are never modified in place by this tool; all
generated files live in directories you choose; re-running any command is
idempotent except `init-package --force`, which deterministically regenerates
only its managed files.

## 13. Extension and maintenance

- **Add a delivery profile**: append to `profiles` in
  `asset-delivery-profiles.json` with a unique `id`; `audio-check` picks it up
  automatically. Document the source of the loudness numbers.
- **Add a direction preset**: append to `presets` in
  `asset-direction-presets.json`; reference it with `brief --preset`.
- **Extend the manifest schema**: edit `asset-work-package-schema.json`
  (draft-07 subset) *and* keep `tools-validate.py`'s subset validator
  compatible — new types beyond object/array/string/integer/number/boolean/
  null are not supported.
- **New catalog content**: the catalog is data, not code. Regenerate
  `asset-voice-skill-catalog.json` from the source document with the same
  extraction rules (category headings `^N. Name`, skills `N. Skill Name:`,
  descriptions verbatim, pointers truncated at the next heading) and bump its
  `schema` version. Do not hand-edit individual skills.
- **New commands**: add a `cmd_*` + subparser in `tools-voice-skill-tool.py`,
  keep logic in the sibling module, add a `capabilities` entry, extend
  `selftest`, and document here.
- **Release checklist**: `selftest` green → `help`/`capabilities` accurate →
  file references in this document still exist → ZIP rebuilt without nested
  archives → clean-root extraction test (see below).

### Clean-root verification (repeatable)

```sh
mkdir -p /tmp/skill-root && cd /tmp/skill-root
cp -r <path>/voice-ai-skill-toolkit .
printf -- '---\nname: test-skill\n---\n' > SKILL.md
python3 voice-ai-skill-toolkit/tools-voice-skill-tool.py selftest
python3 voice-ai-skill-toolkit/tools-voice-skill-tool.py list --category 10
```

## 14. File count and package compatibility

16 files, all directly inside `voice-ai-skill-toolkit/`, no subfolders, no
SKILL.md, no nested archives. Every path in this document is relative to the
future skill root (`voice-ai-skill-toolkit/...`). Depth ≤ 2 from the skill
root. The folder may sit beside a parent `SKILL.md` unmodified; the final ZIP
contains exactly this folder at its root.
