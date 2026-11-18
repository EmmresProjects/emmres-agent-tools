# Ultimate Translation Skill Tool

`ultimate-translation` is a reusable Skill Tool component for the 16 categories and 144 capabilities in the user-supplied **Ultimate Translation Skill Catalog**. It is one unified tool, not 144 folders and not a complete Copilot skill. It deliberately has no `SKILL.md`; copy this folder beside a future parent package's root `SKILL.md`.

The primary entry point is `ultimate-translation/tools-translate.py`. It provides real editable-file translation workflows, deterministic offline QA and planning, configurable HTTP AI/OCR/speech adapters, resumable checkpoints, structured errors, review evidence, and release gates. It does **not** pretend that a glossary is universal machine translation, that pseudolocalization is translation, or that heuristics establish linguistic or legal correctness.

## Quick start

The validated runtime is **Python 3.13.16**; use Python 3.13.x for the tested configuration. The code uses Python 3.10+ language features, but older or other runtime versions have not been validated and are not claimed as tested. No Python package installation is needed for TXT, Markdown, HTML/XML, JSON, CSV/TSV, OOXML, subtitles, ICU subset, planning, project governance, repository scanning, or PCM/WAV preparation.

From the future skill root:

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py --help'
bash -lc 'python3 ultimate-translation/tools-translate.py capabilities --format text'
```

Install the optional YAML, PDF, and font integrations:

```sh
bash -lc 'python3 -m pip install -r ultimate-translation/tools-requirements.txt'
```

Run a deterministic offline translation using the supplied example glossary:

```sh
bash -lc 'printf "Hello world.\n" > input.txt'
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.txt output.txt --source en --target es --glossary ultimate-translation/asset-glossary.csv'
bash -lc 'cat output.txt'
# Hola mundo.
```

The glossary provider translates exact segments and approved phrases only. Uncovered text remains unchanged and is flagged for review. For arbitrary languages or text, configure an approved HTTP translation provider or use qualified human translation through exchange files.

Run all repeatable tests:

```sh
bash -lc 'python3 ultimate-translation/tools-selftest.py -v'
```

## Files, responsibilities, and loading order

Every file is directly inside `ultimate-translation/`; there are no internal subfolders. The primary module loads siblings by their actual location, so commands work from any current directory and do not depend on an installation path.

| File | Functional role |
|---|---|
| `ultimate-translation/tools-translate.py` | Primary CLI, argument validation, command dispatch, reports, batch orchestration, overwrite protection, and output routing. |
| `ultimate-translation/tools-core.py` | Loaded first. Shared models, configuration, bounded I/O, atomic writes, language/script heuristics, segmentation, token guardian, glossary/memory loading, checkpoints, cancellation, and translation engine. |
| `ultimate-translation/tools-providers.py` | Loaded second. Offline glossary and pseudolocalization providers plus bounded HTTP chat, transcription, and vision adapters. |
| `ultimate-translation/tools-formats.py` | Loaded third. Editable format handlers, OOXML ZIP/XML preservation, PDF extraction/reconstruction, intake, and exchange packages. |
| `ultimate-translation/tools-quality.py` | Loaded fourth. Bilingual QA, term extraction, memory build/clean/TMX/XLIFF, deltas, accessibility, sampling, and release audits. |
| `ultimate-translation/tools-media.py` | Loaded fifth. Subtitle timing, voice-over plans, PCM/WAV analysis/preparation, transcript exports, visual plans, and font audits. |
| `ultimate-translation/tools-project.py` | Loaded sixth. Locale/ICU support, planning, stateful governance, handoffs, repository/security, narrative graphs, and static-site packaging. |
| `ultimate-translation/tools-catalog.py` | Loaded seventh. Validates and filters the operational registry of all 144 capabilities and checks command reachability. |
| `ultimate-translation/tools-selftest.py` | Separate repeatable integration suite. It creates temporary fixtures and a loopback mock provider; it is not imported at runtime. |
| `ultimate-translation/tools-requirements.txt` | Supported optional dependency ranges. |

There are **20 Skill Tool files**: 10 `tools-` files, 9 `asset-` files, and exactly one `knowledge-` file.

### Assets

| Asset | Purpose |
|---|---|
| `ultimate-translation/asset-default-config.json` | Safe portable defaults for limits, retries, providers, review, subtitles, PDF, atomic output, and checkpoint suffixes. Network access is off by default. |
| `ultimate-translation/asset-capability-registry.json` | Runtime-validated operational mapping of all 144 source-catalog entries to commands and offline/hybrid/provider modes. Used by `capabilities` and `plan --capability`; it is not a packaging manifest. |
| `ultimate-translation/asset-glossary.csv` | Small editable English–Spanish glossary for deterministic offline use and tests. It is an example starter resource, not a pre-approved production termbase for an organization. |
| `ultimate-translation/asset-locales.json` | Compact locale planning presets for direction, fallback, date/number conventions, fonts, and risks. Current CLDR and market review remain required. |
| `ultimate-translation/asset-provider-prompts.json` | Editable prompt policies for translation, transcreation, back-translation, cultural/inclusive review, glossing, learning, sign-language preparation, dubbing, meetings, pronunciation, narrative, and bilingual review. |
| `ultimate-translation/asset-style-guide.json` | Reusable brand, channel, inclusivity, claims, and review policy. |
| `ultimate-translation/asset-exchange-schema.json` | Operational schema used by `validate exchange` for translation-safe segment packages. |
| `ultimate-translation/asset-ocr-schema.json` | Operational schema used to validate OCR sidecars before visual planning. |
| `ultimate-translation/asset-transliteration.json` | Configurable basic Cyrillic/Greek mappings with explicit reversibility and pronunciation limitations. |

## Command model, output, and exit behavior

Built-in command help is available for every operation:

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --help'
bash -lc 'python3 ultimate-translation/tools-translate.py project --help'
```

Most inspection/report commands emit UTF-8 JSON to stdout or to `--output`. Translation commands require explicit input and output paths and print a concise JSON summary; the full report defaults to `<output>.report.json`, and resumable state to `<output>.checkpoint.json`. All report files are JSON. Errors are structured JSON on stderr:

```json
{
  "ok": false,
  "error": {
    "code": "input_not_found",
    "message": "File does not exist: input.txt"
  },
  "timestamp": "..."
}
```

Exit codes:

- `0`: command completed. A QA report can still contain defects; inspect its `summary` and use `validate ci` for gating.
- `1`: unexpected internal failure. Rerun with global `--debug` for a traceback after checking inputs.
- `2`: invalid input, unsupported format, provider response failure, or a batch with one or more failed files.
- `3`: explicit approval was missing for overwrite, network, sensitive approval, or deletion.
- `5`: `validate ci` rejected the supplied QA/release report.

Translation and reconstruction use atomic temporary-file replacement and clean temporary files on failure. Existing outputs are refused by default. `--approve-overwrite` creates `<output>.backup` before replacement. In-place source replacement therefore requires explicit approval and leaves recovery evidence. Project updates also create `<project>.backup`.

## Providers and configuration

### Offline glossary provider

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.csv output.csv --source en --target es --provider glossary --glossary ultimate-translation/asset-glossary.csv --columns title,note'
```

Glossary CSV columns are `source,target,source_locale,target_locale,status,term_type,case_sensitive,forbidden,definition,part_of_speech,notes,pronunciation`. JSON glossaries can be an array or an object with a `terms` array. Required fields are `source` and `target`; defaults are supplied for the other fields. Locale filtering accepts language matches such as `es` and `es-MX`. Duplicate or incomplete entries fail validation. Translation uses approved exact segments first, then longest approved phrases while protecting tokens. Missing coverage is never invented.

### Pseudolocalization provider

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py pseudolocalize input.json pseudo.json --target qps-ploc --pseudo-expansion 0.35 --pseudo-accents --pseudo-brackets --pseudo-bidi'
```

Pseudolocalization supports accents, configurable expansion from 0 to 3 times source length, brackets, and bidirectional controls. It is for encoding, hard-coded-text, token, and layout testing only.

### Configurable HTTP provider

The chat adapter accepts an OpenAI-compatible `model` plus `messages` request and reads `choices[0].message.content` or `output_text`. Compatibility with a particular hosted service is not claimed without testing that service. The endpoint must be the **full approved HTTPS endpoint**, without URL credentials, query, or fragment. Plain HTTP is accepted only for literal loopback testing. Redirects are not followed. Request size, response size, time, retries, and backoff are bounded.

Default environment variables:

| Purpose | Variables |
|---|---|
| Chat translation/adaptation | `ULTIMATE_TRANSLATION_ENDPOINT`, `ULTIMATE_TRANSLATION_MODEL` |
| Speech transcription | `ULTIMATE_TRANSLATION_TRANSCRIPTION_ENDPOINT`, `ULTIMATE_TRANSLATION_TRANSCRIPTION_MODEL` |
| Vision/OCR | `ULTIMATE_TRANSLATION_VISION_ENDPOINT`, `ULTIMATE_TRANSLATION_VISION_MODEL` |
| Authentication | `ULTIMATE_TRANSLATION_API_KEY` and optional `ULTIMATE_TRANSLATION_ORGANIZATION` |

Different environment variable names can be configured with `provider.model_env`, `provider.endpoint_env`, `provider.api_key_env`, and `provider.organization_env`; OCR/transcription commands also accept `--endpoint-env` and `--model-env`. Secrets are read only from the environment and are never written to reports or checkpoints. Provider error bodies and invalid model content are not echoed or persisted because they can contain source text.

A user-supplied `provider-config.json` can contain:

```json
{
  "version": 1,
  "provider": {
    "name": "http",
    "request_options": {
      "temperature": 0.1,
      "max_tokens": 4096
    }
  },
  "translation": {
    "allow_network": true
  },
  "limits": {
    "http_timeout_seconds": 45,
    "http_retries": 3,
    "retry_backoff_seconds": 1.0
  }
}
```

This file is an example the user creates, not a packaged credential or endpoint. Set the approved full endpoint and model in the environment, then explicitly approve data transmission:

```sh
bash -lc 'export ULTIMATE_TRANSLATION_ENDPOINT="$APPROVED_FULL_CHAT_ENDPOINT"; export ULTIMATE_TRANSLATION_MODEL="$APPROVED_CHAT_MODEL"; export ULTIMATE_TRANSLATION_API_KEY="$APPROVED_API_KEY"; python3 ultimate-translation/tools-translate.py translate input.md output.md --config provider-config.json --source en --target es --provider http --allow-network'
```

Both `translation.allow_network=true` and `--allow-network` are required. Do not send confidential, personal, export-controlled, legal, medical, or unreleased material to an external provider without organizational authorization. Check data retention, region, subprocessor, license, and deletion terms separately; this package cannot control a hosted platform.

Allowed `provider.request_options` are `temperature`, `top_p`, `max_tokens`, `max_completion_tokens`, `seed`, `response_format`, and `reasoning_effort`. Static `provider.extra_headers` cannot set authorization, proxy authorization, host, content length, or cookies. No provider shell command or arbitrary adapter code is executed.

### Prompt and policy overrides

The sibling prompt asset is loaded automatically. A configuration's `prompts` object overrides same-named prompt keys. `translation`, `review`, and `subtitles` objects control behavior. `--style` accepts a JSON style policy and sends it as bounded provider context. Prompt changes should be reviewed like code and tested for JSON-schema compliance, token preservation, uncertainty exposure, and unsafe instruction injection.

## Limits, timeouts, retries, cancellation, and recovery

`ultimate-translation/asset-default-config.json` defines portable defaults. Configuration values are validated before work.

| Limit | Default | Purpose |
|---|---:|---|
| `max_input_bytes` | 52,428,800 | Per input file or media upload. |
| `max_output_bytes` | 104,857,600 | Rendered output and uncompressed OOXML ceiling. |
| `max_segments` | 50,000 | Per translation job. |
| `max_segment_chars` | 12,000 | Extraction/chunk target. |
| `batch_segments` | 12 | Maximum provider segments per request. |
| `batch_chars` | 18,000 | Maximum combined source characters per batch. |
| `context_chars` | 3,000 | Rolling long-form context retained across batches. |
| `http_timeout_seconds` | 45 | Per HTTP request; hard maximum 300. |
| `http_retries` | 3 | Chat retries for 429/5xx and transient transport errors; hard maximum 10. Zero is supported. |
| `http_response_bytes` | 4,194,304 | Provider response ceiling. |
| `retry_backoff_seconds` | 1.0 | Exponential backoff with bounded jitter and delay. |
| `checkpoint_every_batches` | 1 | Checkpoint frequency. |
| `max_audio_chunks` | 500 | WAV preparation ceiling. |
| `max_repository_files` | 5,000 | Static repository scan ceiling. |

Chat requests retry bounded transient failures. Multipart transcription uploads are **not automatically replayed**, preventing duplicate jobs or charges; verify provider receipt before retrying. Requests are not followed across redirects. Cancellation via SIGINT/SIGTERM or provider failure saves completed batches, source fingerprint, options fingerprint, segment order, rolling context, and structured error state.

Resume the identical job:

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate long-book.md localized-book.md --source en --target es --provider http --allow-network --config provider-config.json --resume'
```

A changed source, target, options fingerprint, or segment order causes `checkpoint_mismatch` rather than unsafe reuse. Keep the same `--checkpoint` path. On successful resume, checkpoint status becomes `complete`. Batch rerun with `--resume` skips completed files only when source and output SHA-256 fingerprints match the prior report; failed or missing files remain isolated in `batch-report.json`.

Recovery procedure:

1. Read the structured error and checkpoint `status`; do not delete evidence first.
2. Confirm source and options have not changed. If they changed, choose a new output/checkpoint job.
3. Correct credentials, endpoint, quota, malformed input, or provider availability externally.
4. Rerun the identical command with `--resume`.
5. Run `qa`, inspect uncertainty, and parse/render the output before approval.
6. Use `<output>.backup` or `<project>.backup` if an approved overwrite needs reversal.

## Supported input and output formats

| Format family | Extensions | Translation behavior and boundaries |
|---|---|---|
| Plain text | `.txt` | Ordered sentence/line segments, encoding detection, exact whitespace around spans. UTF-16 is rendered as UTF-8 with a warning. |
| Markdown | `.md`, `.markdown` | Preserves frontmatter, fenced/indented code, inline code, reference definitions, tags, headings/list markers, and link destinations. |
| HTML/XML | `.html`, `.htm`, `.xhtml`, `.xml`, `.resx`, `.ttml`, `.dfxp` | Human text and explicit attributes such as `alt`, `title`, `placeholder`, `aria-label`, and description metadata. Preserves tags, IDs, classes, data attributes, scripts/styles, and machine values. Entity spelling can normalize. Malformed markup needs review. |
| Structured data | `.json`, `.arb`, `.yaml`, `.yml` | Selected string values only; ID/URL/path/locale-like keys are skipped by default. `--keys` supports dotted/glob selection. JSON order/types are preserved. YAML uses safe load/dump and normalizes comments, anchors, tags, and scalar styling with a warning. |
| Delimited data | `.csv`, `.tsv` | Header/column selection, stable row identity, quoting, delimiters, empty/null fields, and line endings. No direct database driver is included. |
| Office | `.docx`, `.xlsx`, `.pptx` | Direct OOXML part editing preserves unmodified ZIP entries, relationships, formulas, fields, styles, embedded media, and package order. Handles known document/header/footer/note/comment/shared-string/slide/diagram/chart containers. Raster/OLE/proprietary extension text needs OCR or vendor tooling; visual expansion needs application review. |
| Resources | `.properties`, `.strings`, `.po` | Single-line property/String values and single-line PO entries with empty `msgstr`. Multiline/plural PO and escaped multiline resources are left unchanged and warned about. |
| Subtitles | `.srt`, `.vtt`, `.ass`, `.ssa` | Cue text translation preserves indices, timestamps, settings, styles, and override tokens. Timing adjustment supports these four formats. TTML translation uses the XML handler; TTML timing adjustment is application-specific. |
| PDF | `.pdf` | `pypdf` extraction and `reportlab` editable text-first PDF reconstruction with optional configured Unicode TrueType font. Layout reflows; exact original coordinates, figures, forms, annotations, links, and redaction appearance are not guaranteed. Scanned pages require external rasterization and OCR. |
| Audio | PCM `.wav` | Local sample assessment, PCM16 normalize/trim, and all-integer-PCM chunking. Speech recognition/diarization uses a configurable multipart provider. Other codecs require authorized external conversion. |
| Images | PNG/JPEG/WebP/GIF | Configurable vision OCR returns text, confidence, reading order, and boxes. `visual` creates editable replacement/layout plans; it does not repaint pixels. |
| Fonts | TTF/OTF supported by `fontTools` | Glyph cmap, shaping/vertical tables, embedding flags, and license metadata checks. Rendering and legal clearance remain external. |
| JSON sidecars | Transcript, OCR, narrative, QA, release, project | Validated editable intermediate representations for manual correction, recovery, review, and integration. |

`inspect` identifies the actual magic/family, encoding/extraction risks, embedded OOXML assets, stable content fingerprint, segment/word counts, estimated effort, and sidecar requirements. Unknown binaries are not executed.

## Translation options and structured outputs

Common translation options:

- `--source auto|LOCALE`, required `--target LOCALE`: source heuristic or explicit locale and regional target.
- `--provider glossary|http|pseudo`, `--glossary`, `--memory`: provider, approved terms, and approved/reviewed JSON memory.
- `--tone`, `--mode`, `--audience`, `--domain`, `--brand-voice`, `--reading-level`, `--channel`, `--character-voice`, `--purpose`: bounded prompt controls.
- `--vernacular`, `--accessibility`, `--dubbing`, `--lip-sync`: preserve vernacular identity or apply specialized context; lip-sync output remains provisional.
- `--style FILE`: brand/style policy.
- `--set KEY=VALUE`: additional bounded provider options; arbitrary code, endpoints, credentials, and paths are not accepted through this option.
- `--keys` and `--columns`: designated structured values.
- `--report`, `--checkpoint`, `--resume`: storage and recovery.
- `--allow-unit-conversion`: QA accepts supported approximate unit conversions rather than silently allowing arbitrary changes.

The full translation report contains input/output paths and fingerprints, locales, detection evidence, provider, format, segment count, reuse, confidence, uncertainties, alternatives, back-translation when supplied, review flags, objective defects, format warnings, checkpoint path, and output size. Downstream users must preserve these fields when constructing review queues.

## Quality, memory, exchange, and review schemas

### QA

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py qa --source source.json --target localized.json --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv --translation-report localized.json.report.json --accessibility --output qa-report.json'
```

Checks include segment counts and path alignment, omissions/additions, unchanged text, repeated-source inconsistency, numbers, currencies, dates/times, percentages, units and approved conversions, placeholders, tags and order, links, escapes, forbidden terms, approved terminology literals, proper nouns, punctuation balance, UI limits, expansion, and RTL script presence. Defects have category, severity, evidence, excerpts, recommendation, objective/preference status, and workflow state. `release_ready` is false for blocking findings, review findings, or high-risk domains. It is not a semantic quality score.

### Translation memory

JSON memory contains `version`, `source_locale`, `target_locale`, `segments`, `summary`, and optional `conflicts`. Each segment needs `id`, `source`, `target`, locale fields, `status`, and can include context, metadata, origin, dates, and reuse count. Only `approved` or `reviewed` records are reused by translation. Building those statuses requires `--confirm-sensitive`; default status is `draft`.

`memory build` deduplicates exact normalized pairs and exposes conflicting targets. `memory clean` quarantines invalid structures, duplicates, corrupted tags/tokens, quantity mismatches, stale/rejected/expired rows, and confident language mismatches. `memory tmx` exports TMX 1.4. `align` emits JSON, CSV, Markdown, or TMX parallel text.

### Exchange and conversion

`convert extract` emits a version-1 package with `source_fingerprint`, locales, kind, and `segments`; each segment has `id`, `source`, `target`, `context`, and `metadata`. Validate it with `validate exchange`, which uses `ultimate-translation/asset-exchange-schema.json`. `convert apply` verifies the source fingerprint, exact IDs/source text, complete nonempty targets, and then renders the original format. `convert xliff` emits XLIFF 1.2 for vendor exchange. The schema validator supports the documented subset: type, required, properties, items, enum, additionalProperties, item/length limits, pattern, and numeric min/max; it is not a complete JSON Schema implementation.

### Human review

`review` applies decisions to a QA report without erasing objective defects. Decision JSON:

```json
[
  {
    "segment_id": "segment-000001",
    "decision": "changes-requested",
    "reviewer": "accountable-reviewer",
    "comment": "Correct the quantity and recheck terminology",
    "evidence": "Source line 4",
    "date": "2026-11-05"
  }
]
```

Allowed decisions are `accept`, `reject`, `changes-requested`, and `escalate`. Release sign-off is a separate `project update` approval requiring `--confirm-sensitive`.

## Locale, ICU, subtitles, audio, visual, and web details

### Locale and message-format support

`locale plan` reports language/region, script, direction, fallback, cardinal categories, date/number conventions, font/input requirements, risks, and test cases. The built-in plural engine covers common cardinal systems including English-like, French-like, Arabic, Slavic, Polish, Czech/Slovak, Welsh, Irish, Hebrew, and other-only languages. It is CLDR-inspired, not a pinned CLDR replacement.

`locale message` validates balanced ICU structure and required `other` branches, generates branch tests, and executes a bounded subset with `--values-json`: simple variables, `plural`, `select`, `selectordinal` (English plus other-only locales), nested messages, and `#`. It does not claim full ICU apostrophe/date/time/number skeleton or ordinal coverage. Verify final messages in the consuming framework's pinned ICU/CLDR runtime, especially gender agreement and inflection.

### Subtitles and dubbing

`subtitles adjust` supports offset, shot-change snapping, minimum gap, duration/CPS constraints, overlap repair, and line wrapping without dropping text. `subtitles qa` checks cue counts, timing changes, duration, CPS, line lengths/count, overlaps, and caption conventions. `subtitles package` creates character/casting briefs, actor-ready lines, time limits, file names, pickup flags, pronunciation entries, and implementation IDs. Picture lock and human production approval remain mandatory. No speech synthesis or verified lip sync is performed.

### Audio and transcripts

`audio assess` reports PCM metadata, duration, RMS/peak, clipping, silence, DC offset, channel imbalance, and risks. `audio prepare` writes immutable chunks and `audio-chunks.json` with source offsets for timestamp recovery. PCM16 normalization and silence trimming are optional; all integer PCM widths can be chunked without cleanup.

`transcribe` sends authorized audio to the configured multipart endpoint with language, clean/verbatim/detailed style, timestamps, and optional diarization. It normalizes speaker/time/confidence fields and exports JSON, SRT, VTT, or Markdown. A manually corrected transcript JSON can be supplied instead of audio, enabling speaker correction without a provider. `--target` translates timestamped segments through glossary or HTTP and marks review required. Summary/action-item generation uses `adapt meeting`; owners and deadlines are never invented locally.

### OCR, visual design, and fonts

`ocr` sends an authorized image to a vision-capable chat endpoint and validates text blocks and four-coordinate boxes. `visual` validates against `ultimate-translation/asset-ocr-schema.json`, optionally translates blocks, orders reading flow, checks OCR confidence and expansion, and creates layer-by-layer font/alignment/background/effects review actions. Packaging, labels, warnings, ingredients, legal lines, and claims remain high-risk. No pixels are repainted and no OCR certainty is invented.

`font` checks cmap coverage, RTL shaping tables, vertical metrics, embedding bits, and license metadata against representative text. It does not prove glyph aesthetics, actual fallback, complex shaping in an application, or redistribution rights.

### Static sites and repositories

`web` copies authorized source/localized static files into locale folders and emits `localization-routes.json` with route maps, hreflang groups, fallback rules, asset fingerprints, and deployment review requirements. It does not crawl a CMS, alter a live router, or publish. Add hreflang tags through the site template and validate staging.

`repository` performs bounded static scanning of resource files, flat JSON catalogs, hard-coded UI string candidates, missing keys, stable file fingerprints, and secret patterns. It skips symlinks, hidden/build/dependency folders, oversized files, and non-UTF-8 data. It never builds or executes a repository. Engine-specific Unity/Unreal/proprietary assets and dynamic keys need dedicated adapters.

## Project state, handoffs, release, and security

Initialize and update state:

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project init --project project.json --name "Product localization" --locales es-MX,ar --owner accountable-owner'
bash -lc 'python3 ultimate-translation/tools-translate.py project update --project project.json --event event.json --expected-revision 1'
```

Optimistic `expected-revision` checking prevents stale writes. Supported event types and required fields:

- `asset.stage`: `asset_id`, `stage`; released/published/approved stages need confirmation.
- `asset.upsert`: `asset` object with stable `id`.
- `blocker.add`/`blocker.resolve`: blocker/asset/owner evidence.
- `defect.add`/`defect.resolve`: severity, category, evidence, resolution.
- `review.add`: asset, reviewer, decision, evidence, comments.
- `decision.add`: question, approved decision, rationale, owner, effective date, affected assets, future-use guidance.
- `feedback.add`: source and text plus optional asset/segment/locale/decision.
- `risk.add`/`risk.update`: category, description, severity, mitigation, trigger, owner, status.
- `approval.add`: role, accountable owner, date, scope, approved flag, exceptions; approved sign-off needs `--confirm-sensitive`.
- `vendor.add` and `inventory.add`: handoff ownership and searchable asset metadata.
- `status.set`: planned/in-progress/blocked/complete or sensitive approved/released/published state.

`project status`, `metrics`, `feedback`, and `sample` report assets, locale coverage, blockers, defect density/severity, rework, review state, decisions, risks, approvals, duplicate/conflicting feedback, prioritized post-release corrections, and reproducible risk-stratified review samples. Unmeasured terminology/reuse metrics are reported as `NOT_MEASURED`, never fabricated.

`project handoff` creates a CRC-validated ZIP with editable assets, source/project state, glossary/style guidance, instructions, acceptance criteria, file map, and return-validation requirements. Nested archives and unsafe paths are rejected. `--archive` marks an approved-archive handoff; actual retention/deletion policy remains external.

A release manifest lists `locales`, `assets`, `approvals`, and optionally `required_approvals`. Each asset supplies `localized_path`, locale coverage, open blocking defect count, and required accessibility/font verification flags. `project release` validates file presence within the manifest root, locale coverage, defects, fonts, accessibility, and accountable approvals. Manifest evidence is not independently signed proof; verify signatures and rendered builds in the release system.

`secure scan` reports secret types, line, fingerprint, and severity without reproducing values. `secure redact` always writes a separate file. `secure cleanup --approve-delete` unlinks only explicit regular non-symlink files after approval. This is not guaranteed forensic erasure on SSDs, copy-on-write storage, backups, or synchronized platforms.

## Safety and approval rules

- Network sharing requires both configuration and `--allow-network`; use HTTPS and approved endpoints. No live endpoint or credential is embedded.
- Existing source/output replacement requires `--approve-overwrite` and creates a backup.
- Approved/reviewed memory status, project sign-off, and release/publish states require `--confirm-sensitive`.
- Irreversible cleanup requires `--approve-delete`; redaction cannot replace its source.
- Legal, privacy, medical, pharmaceutical, financial, patent, and safety content always requires qualified human expert review. Dosage, negation, claims, dependencies, units, and warnings are objective QA priorities.
- Publishing itself is not implemented. Handoffs, static-site folders, and release manifests are local review artifacts, not permission to deploy.
- No arbitrary user code, shell, macro, repository build, or executable document content is run. The self-test launches only this tool's own CLI and a loopback test server.
- High-risk or uncertain output remains editable and reviewable. Never convert a provider confidence value into automatic legal, clinical, accessibility, or release approval.
- External catalogs, hosted permissions, model availability, quotas, retention, and platform failures cannot be prevented by this package.

## Complete catalog capability guide

Mode meanings: **offline** works locally without credentials; **hybrid** has local deterministic handling and can use glossary/memory/HTTP depending on content; **provider** needs a configured approved provider for the non-deterministic AI portion. Every example uses paths relative to the future skill root. `provider-config.json`, inputs, project state, and event/report files in examples are user-supplied runtime files, not missing packaged assets. Examples with HTTP require the environment setup above and qualified review.

### Category 1: Core Translation and Language Intelligence

#### catalog-01-01 — Ultimate Translator

Translate text of any practical length across languages while preserving meaning, intent, tone, terminology, structure, numbers, names, and formatting. Detect source languages, handle mixed- language content, explain uncertainties, and produce literal, natural, formal, or audience-adapted versions.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-01-02 — Language Auto-Detection and Segmentation

Identify languages, scripts, dialect shifts, code-switching, borrowed words, and untranslated spans at sentence or token level. Split multilingual inputs into translation-ready segments without damaging context.

- **Mode:** offline
- **Operation:** `detect`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py detect input.txt --target-locale es'
```

#### catalog-01-03 — Context-Aware Long-Form Translator

Translate books, reports, manuals, archives, and large collections in ordered chunks with rolling context, chapter memory, terminology consistency, cross-reference preservation, and resumable progress.

- **Mode:** hybrid
- **Operation:** `translate --resume`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --resume input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-01-04 — Meaning and Intent Preserver

Protect implied meaning, emotional force, politeness, humor, ambiguity, and speaker intent instead of translating words mechanically. Flag meanings that cannot be carried directly into the target language.

- **Mode:** provider
- **Operation:** `translate --mode natural`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --mode natural input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-01-05 — Register and Tone Adapter

Translate into formal, casual, academic, technical, legal, friendly, persuasive, child- friendly, or brand-specific language while retaining the original message.

- **Mode:** provider
- **Operation:** `translate --tone formal`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --tone formal input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-01-06 — Dialect and Regional Variant Translator

Translate between regional forms such as European and Brazilian Portuguese, Latin American and Peninsular Spanish, Simplified and Traditional Chinese usage, and other locale-specific variants.

- **Mode:** provider
- **Operation:** `translate --target pt-BR`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --target pt-BR input.txt localized.txt --provider http --allow-network --config provider-config.json'
```

#### catalog-01-07 — Transliteration and Script Converter

Convert names and text between writing systems using configurable standards, pronunciation-based rendering, reversible transliteration where possible, and side-by-side native script.

- **Mode:** offline
- **Operation:** `transliterate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py transliterate --text "Привет" --scheme cyrillic-latin-basic'
```

#### catalog-01-08 — Low-Resource Language Assistant

Support scarce-resource languages through careful segmentation, glossing, user-provided dictionaries, parallel examples, confidence labels, and human-review queues rather than inventing certainty.

- **Mode:** provider
- **Operation:** `translate --domain low-resource --glossary TERMS`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain low-resource --glossary ultimate-translation/asset-glossary.csv input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-01-09 — Pidgin, Creole, and Vernacular Translator

Handle vernacular grammar, nonstandard spelling, community expressions, and mixed vocabularies while preserving identity and avoiding forced standardization.

- **Mode:** provider
- **Operation:** `translate --vernacular`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --vernacular input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-01-10 — Parallel Bilingual Output Builder

Produce source-target parallel text by sentence, paragraph, table row, or timecode for review, study, publishing, and translation-memory import.

- **Mode:** offline
- **Operation:** `align`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py align --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv'
```

### Category 2: Localization Planning and Orchestration

#### catalog-02-01 — Localization Project Orchestrator

Plan and coordinate end-to-end localization across content, software, media, reviewers, vendors, languages, milestones, dependencies, approvals, and release gates.

- **Mode:** offline
- **Operation:** `plan`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py plan --words 1000 --locales es-MX,ar --domain general --content-type text'
```

#### catalog-02-02 — Locale Coverage Planner

Define target locales, language-region pairs, scripts, fallback behavior, plural rules, date and number conventions, fonts, input requirements, and market-specific risks.

- **Mode:** offline
- **Operation:** `locale plan`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py locale plan --locales es-MX,pt-BR,ar,zh-TW'
```

#### catalog-02-03 — Translation Workflow Router

Route content to machine translation, language models, subject specialists, native reviewers, transcreators, or legal review based on risk, content type, confidence, and deadline.

- **Mode:** offline
- **Operation:** `plan`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py plan --words 1000 --locales es-MX,ar --domain general --content-type text'
```

#### catalog-02-04 — Localization Scope Estimator

Estimate word counts, subtitle minutes, interface strings, screenshots, audio hours, review effort, complexity, dependencies, and delivery phases from supplied assets.

- **Mode:** offline
- **Operation:** `inspect`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py inspect input.txt --output intake-report.json'
```

#### catalog-02-05 — Localization Status Tracker

Track every asset, language, owner, stage, blocker, review state, defect, and final approval with resumable checkpoints and concise progress summaries.

- **Mode:** offline
- **Operation:** `project status`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project status --project project.json'
```

#### catalog-02-06 — Multi-Vendor Handoff Manager

Prepare consistent handoff packages for agencies and freelancers with instructions, glossaries, style guides, file maps, references, acceptance criteria, and return validation.

- **Mode:** offline
- **Operation:** `project handoff`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project handoff  --project project.json --output localization-handoff.zip --files localized.txt --glossary ultimate-translation/asset-glossary.csv --style ultimate-translation/asset-style-guide.json'
```

#### catalog-02-07 — Continuous Localization Coordinator

Connect changing source content to incremental translation, identify new or modified strings, avoid retranslating unchanged material, and prepare release-ready deltas.

- **Mode:** offline
- **Operation:** `delta`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py delta --previous previous.json --current current.json --source-locale en --target-locale es --memory memory.json'
```

#### catalog-02-08 — Human-in-the-Loop Review Designer

Design review stages, reviewer roles, sampling rates, escalation rules, adjudication methods, and evidence requirements for reliable multilingual output.

- **Mode:** offline
- **Operation:** `project sample`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project sample --project project.json --sample-rate 0.2'
```

### Category 3: Terminology, Memory, and Style

#### catalog-03-01 — Terminology Extraction Specialist

Extract candidate terms, names, abbreviations, product labels, commands, domain phrases, and forbidden translations from source materials with context examples.

- **Mode:** offline
- **Operation:** `terms extract`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py terms extract --input input.txt --max-terms 100'
```

#### catalog-03-02 — Multilingual Glossary Builder

Create approved source-target termbases with definitions, part of speech, locale, capitalization, gender, usage notes, prohibited variants, and review status.

- **Mode:** offline
- **Operation:** `terms validate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py terms validate --input ultimate-translation/asset-glossary.csv'
```

#### catalog-03-03 — Translation Memory Builder

Create reusable aligned source-target segments, remove duplicates, normalize formatting, attach metadata, and preserve context for future translations.

- **Mode:** offline
- **Operation:** `memory build`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py memory build --source source.txt --target localized.txt --source-locale en --target-locale es --output memory.json'
```

#### catalog-03-04 — Translation Memory Cleaner

Detect mismatched pairs, stale translations, duplicates, inconsistent punctuation, corrupted tags, wrong languages, and low-quality segments before reuse.

- **Mode:** offline
- **Operation:** `memory clean`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py memory clean --input memory.json --source-locale en --target-locale es --output cleaned-memory.json'
```

#### catalog-03-05 — Terminology Consistency Enforcer

Check translations against approved terms and flag missing, altered, inflected, or contextually incorrect terminology without blindly replacing valid variants.

- **Mode:** offline
- **Operation:** `terms check`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py terms check --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv'
```

#### catalog-03-06 — Brand Voice Localization

Translate to a defined brand voice using approved vocabulary, sentence rhythm, inclusivity rules, prohibited claims, and channel-specific style.

- **Mode:** provider
- **Operation:** `translate --style STYLE`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --style ultimate-translation/asset-style-guide.json input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-03-07 — Multilingual Style Guide Author

Create practical rules for tone, punctuation, capitalization, numbers, units, names, links, UI length, gender, accessibility, and reviewer decisions per locale.

- **Mode:** offline
- **Operation:** `terms style`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py terms style --target-locale es-MX --style ultimate-translation/asset-style-guide.json --glossary ultimate-translation/asset-glossary.csv --output style-guide.md'
```

#### catalog-03-08 — Name and Proper-Noun Authority

Maintain consistent treatment of people, places, organizations, fictional terms, titles, honorifics, and pronunciation across all assets.

- **Mode:** offline
- **Operation:** `terms check`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py terms check --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv'
```

### Category 4: Documents, Files, and Structured Content

#### catalog-04-01 — Universal File Translation Intake

Inspect supplied files, identify formats, encodings, embedded assets, tables, comments, metadata, hidden text, and extraction risks, then choose a safe translation path.

- **Mode:** offline
- **Operation:** `inspect`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py inspect input.txt --output intake-report.json'
```

#### catalog-04-02 — DOCX Translation Preserver

Translate Word documents while preserving headings, styles, lists, tables, footnotes, endnotes, comments, links, captions, fields, headers, footers, and page structure.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.docx localized.docx --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-04-03 — PDF Translation and Reconstruction

Extract or OCR PDF content, translate it, and reconstruct readable pages while preserving reading order, tables, labels, figures, links, and redaction boundaries.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.pdf localized.pdf --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-04-04 — Spreadsheet Localization

Translate workbooks while preserving formulas, cell types, named ranges, validations, comments, hidden sheets, filters, charts, references, and locale-sensitive number formats.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.xlsx localized.xlsx --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-04-05 — Presentation Localization

Translate slide text, speaker notes, diagrams, charts, and image labels while managing text expansion, font coverage, alignment, and visual hierarchy.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.pptx localized.pptx --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-04-06 — Plain Text and Markdown Translator

Translate TXT and Markdown while protecting headings, code fences, links, frontmatter, placeholders, references, lists, and inline markup.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.md localized.md --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-04-07 — HTML and XML Safe Translator

Translate human-readable nodes and attributes while preserving tags, entities, namespaces, IDs, templates, scripts, styles, and machine-readable values.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.html localized.html --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-04-08 — JSON and YAML Localization

Translate only designated values while preserving keys, syntax, escapes, nesting, ordering requirements, variables, and schema constraints.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.json localized.json --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-04-09 — CSV and Database Export Translator

Translate selected columns in delimited or tabular exports while preserving row identity, delimiters, quoting, nulls, encodings, and import compatibility.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.csv localized.csv --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-04-10 — OCR and Scanned Document Translator

Recognize text from scans and photos, restore reading order, mark uncertain characters, translate the content, and keep coordinates for review or re-layout.

- **Mode:** provider
- **Operation:** `ocr`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py ocr page.png --target es --allow-network --config provider-config.json --output ocr-blocks.json'
```

### Category 5: Websites, Pages, and Online Content

#### catalog-05-01 — Website Localization Architect

Inventory pages, templates, components, CMS entries, metadata, structured data, forms, cookies, navigation, errors, and dynamic content for complete website localization.

- **Mode:** offline
- **Operation:** `inspect`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py inspect input.txt --output intake-report.json'
```

#### catalog-05-02 — Webpage Translator

Translate visible page content, alternative text, labels, tooltips, placeholders, validation messages, metadata, and accessibility text while preserving links and layout.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.html localized.html --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-05-03 — CMS Content Localizer

Prepare and localize entries, taxonomies, reusable blocks, menus, SEO fields, media metadata, and publishing states for content management systems.

- **Mode:** hybrid
- **Operation:** `translate --keys FIELDS`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --keys title,description input.json localized.json --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-05-04 — SEO Multilingual Localizer

Localize titles, descriptions, headings, URLs, keywords, internal anchors, structured data, and search intent without keyword stuffing or false claims.

- **Mode:** provider
- **Operation:** `translate --domain seo`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain seo input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-05-05 — E-commerce Catalog Localizer

Translate product names, variants, specifications, sizing, ingredients, warnings, shipping details, reviews, filters, and marketplace attributes consistently.

- **Mode:** hybrid
- **Operation:** `translate --columns COLUMNS`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --columns title,description input.csv localized.csv --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-05-06 — Web Form and Error Message Localizer

Translate labels, hints, consent text, validation, success states, and error recovery language with clear, accessible, culturally appropriate wording.

- **Mode:** provider
- **Operation:** `translate --accessibility`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --accessibility input.html localized.html --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-05-07 — Right-to-Left Web QA

Check bidirectional text, mirroring, alignment, icons, punctuation, numbers, mixed scripts, forms, tables, and responsive behavior for RTL locales.

- **Mode:** offline
- **Operation:** `qa`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py qa --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv --output qa-report.json'
```

#### catalog-05-08 — Static Site Localization Packager

Create locale folders, translated page files, route maps, hreflang plans, fallback rules, asset lists, and deployment-ready handoffs.

- **Mode:** offline
- **Operation:** `web`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py web site-en --localized es=site-es --output-dir localized-site --default-locale en'
```

### Category 6: Software, Apps, and Interface Localization

#### catalog-06-01 — Software String Localizer

Translate resource bundles and string catalogs while protecting keys, placeholders, escape sequences, markup, accelerator keys, and developer comments.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.properties localized.properties --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-06-02 — Complete UI Text Localizer

Translate every user-facing button, menu, label, dialog, tooltip, notification, onboarding step, empty state, error, help link, and accessibility announcement.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-06-03 — Mobile App Localizer

Localize iOS and Android resources, permission prompts, store text, widgets, notifications, deep links, screenshots, and device-specific layout constraints.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.xml localized.xml --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-06-04 — Desktop Application Localizer

Localize menus, ribbons, installers, system tray items, preferences, dialogs, shortcuts, status messages, update flows, and help files.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.properties localized.properties --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-06-05 — Plural and Gender Rule Engineer

Implement locale-aware plural, grammatical gender, select, and inflection logic using message-format patterns and test cases.

- **Mode:** offline
- **Operation:** `locale plural`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py locale plural --locale ar --values 0,1,2,3,11'
```

#### catalog-06-06 — Placeholder and Token Guardian

Protect variables, interpolation tokens, ICU syntax, printf specifiers, HTML fragments, commands, paths, and runtime markers during translation.

- **Mode:** offline
- **Operation:** `qa`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py qa --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv --output qa-report.json'
```

#### catalog-06-07 — UI Length and Expansion Tester

Predict and detect clipping, wrapping, truncation, overlap, tiny controls, and unusable navigation caused by translated text expansion.

- **Mode:** offline
- **Operation:** `qa`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py qa --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv --output qa-report.json'
```

#### catalog-06-08 — Pseudolocalization Generator

Generate accented, expanded, bracketed, bidirectional, and stress-test strings to expose hard-coded text, encoding issues, and layout defects before translation.

- **Mode:** offline
- **Operation:** `pseudolocalize`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py pseudolocalize input.txt localized.txt --target qps-ploc'
```

#### catalog-06-09 — Installer and Update Localizer

Translate installation, repair, upgrade, rollback, license, privacy, progress, failure, and restart flows without weakening safety instructions.

- **Mode:** provider
- **Operation:** `translate --domain safety`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain safety input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-06-10 — App Store Listing Localizer

Localize app title, subtitle, description, keywords, release notes, subscription text, screenshots, promotional copy, and review-response templates by market.

- **Mode:** provider
- **Operation:** `adapt transcreate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt transcreate input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

### Category 7: Games and Interactive Entertainment

#### catalog-07-01 — Full Game Localization Orchestrator

Coordinate complete localization of dialogue, UI, quests, items, tutorials, lore, achievements, maps, subtitles, voice, store pages, patches, and platform text.

- **Mode:** offline
- **Operation:** `plan`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py plan --words 1000 --locales es-MX,ar --domain general --content-type text'
```

#### catalog-07-02 — Game String and Asset Extractor

Inventory translatable strings from scripts, tables, engines, resource files, images, textures, videos, and build outputs while preserving stable identifiers.

- **Mode:** offline
- **Operation:** `repository`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py repository source-repository --output repository-report.json'
```

#### catalog-07-03 — Dialogue and Character Voice Localizer

Translate dialogue while preserving character identity, relationships, humor, speech patterns, emotional arcs, lore, and line-length constraints.

- **Mode:** provider
- **Operation:** `translate --character-voice VOICE`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --character-voice approved-character-policy input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-07-04 — Quest and Narrative Continuity Checker

Verify names, objectives, branching choices, callbacks, chronology, pronouns, variables, and consequences across nonlinear stories.

- **Mode:** hybrid
- **Operation:** `narrative`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py narrative branching-script.json --output narrative-audit.json'
```

#### catalog-07-05 — Game UI and HUD Localizer

Translate menus, inventory, combat text, controls, prompts, tutorials, tooltips, maps, and heads-up displays with strict space and readability constraints.

- **Mode:** provider
- **Operation:** `translate --domain game-ui`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain game-ui input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-07-06 — Item, Ability, and Lore Terminologist

Build and enforce consistent names and descriptions for items, skills, factions, locations, creatures, currencies, statistics, and worldbuilding concepts.

- **Mode:** offline
- **Operation:** `terms check`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py terms check --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv'
```

#### catalog-07-07 — In-Game Subtitle Localizer

Create readable, timed subtitles for cinematics and gameplay while handling speaker IDs, sound cues, line breaks, safe areas, and rapid dialogue.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.srt localized.srt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-07-08 — Game Voice-Over Localization Planner

Prepare casting briefs, pronunciation guides, scripts, takes, file naming, timing constraints, pickups, and implementation metadata for dubbed games.

- **Mode:** offline
- **Operation:** `subtitles package`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py subtitles package source.srt --target localized.srt --target-locale es --title "Localized production" --output voiceover-plan.json'
```

#### catalog-07-09 — Branching Script Localization

Translate conditional and branching dialogue while preserving variables, logic, state conditions, choice labels, and re-entry context.

- **Mode:** offline
- **Operation:** `narrative`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py narrative branching-script.json --output narrative-audit.json'
```

#### catalog-07-10 — Game Localization QA

Test builds for untranslated text, overflow, broken tokens, wrong context, inconsistent lore, audio-subtitle mismatch, encoding defects, and progression blockers.

- **Mode:** hybrid
- **Operation:** `qa`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py qa --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv --output qa-report.json'
```

### Category 8: Audio, Speech, and Transcription

#### catalog-08-01 — Universal Audio Transcriber

Transcribe clear or noisy recordings with timestamps, speaker turns, language detection, punctuation, confidence markers, and configurable verbatim detail.

- **Mode:** provider
- **Operation:** `transcribe`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py transcribe input.wav transcript.json --source en --diarize --allow-network --config provider-config.json'
```

#### catalog-08-02 — Multilingual Speech Translator

Convert speech into translated text while tracking speakers, code-switching, domain vocabulary, names, numbers, and uncertain audio.

- **Mode:** provider
- **Operation:** `transcribe --target LOCALE`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py transcribe --target es input.wav transcript.json --source en --diarize --allow-network --config provider-config.json'
```

#### catalog-08-03 — Speaker Diarization Specialist

Separate and label speakers across interviews, meetings, panels, calls, lectures, and overlapping conversation, with manual correction support.

- **Mode:** provider
- **Operation:** `transcribe --diarize`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py transcribe --diarize input.wav transcript.json --source en --diarize --allow-network --config provider-config.json'
```

#### catalog-08-04 — Audio Cleanup and Transcription Prep

Assess noise, clipping, silence, channel imbalance, speed, and segmentation, then prepare audio chunks and metadata for reliable transcription.

- **Mode:** offline
- **Operation:** `audio prepare`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py audio prepare input.wav --output-dir audio-chunks --chunk-seconds 30'
```

#### catalog-08-05 — Podcast Localization Producer

Create translated transcripts, show notes, chapters, subtitles, dubbed scripts, localized titles, descriptions, and publication assets for podcasts.

- **Mode:** provider
- **Operation:** `transcribe`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py transcribe input.wav transcript.json --source en --diarize --allow-network --config provider-config.json'
```

#### catalog-08-06 — Meeting and Lecture Translator

Produce multilingual transcripts, summaries, action items, definitions, questions, and time-linked notes from meetings, classes, and presentations.

- **Mode:** provider
- **Operation:** `adapt meeting`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt meeting input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-08-07 — Pronunciation Lexicon Builder

Create pronunciation entries for names, brands, technical terms, acronyms, and fictional vocabulary using native-script guidance or phonetic notation.

- **Mode:** provider
- **Operation:** `adapt pronunciation`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt pronunciation input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-08-08 — Accessible Audio Description Script Translator

Translate audio-description scripts while preserving timing, concise visual detail, character identification, and respectful accessibility language.

- **Mode:** provider
- **Operation:** `translate --domain audio-description`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain audio-description input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

### Category 9: Subtitles, Captions, and Video Localization

#### catalog-09-01 — Subtitle File Translator

Translate SRT, VTT, ASS, SSA, TTML, and related subtitle formats while preserving sequence numbers, timestamps, styles, positioning, and metadata.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.srt localized.srt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-09-02 — Subtitle Timing and Segmentation Editor

Adjust cue boundaries, reading speed, shot changes, pauses, line breaks, overlaps, and synchronization without losing meaning.

- **Mode:** offline
- **Operation:** `subtitles adjust`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py subtitles adjust input.srt --offset-ms 250 --output adjusted.srt'
```

#### catalog-09-03 — Closed Caption Localizer

Translate dialogue plus sound effects, music cues, speaker labels, and accessibility context using consistent caption conventions.

- **Mode:** hybrid
- **Operation:** `translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate input.srt localized.srt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-09-04 — Video Transcription and Translation

Transcribe all spoken content, identify on-screen text, translate both, and produce time- aligned scripts, captions, and review notes.

- **Mode:** provider
- **Operation:** `transcribe`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py transcribe input.wav transcript.json --source en --diarize --allow-network --config provider-config.json'
```

#### catalog-09-05 — On-Screen Text Localizer

Detect and translate titles, lower thirds, signs, labels, interfaces, credits, callouts, and burned-in text with replacement timing and placement notes.

- **Mode:** provider
- **Operation:** `visual`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py visual ocr-blocks.json --target es --output visual-plan.json'
```

#### catalog-09-06 — Dubbing Script Adapter

Adapt translated dialogue for natural performance, timing, mouth movements, character voice, breath, emotion, and local audience expectations.

- **Mode:** provider
- **Operation:** `adapt dubbing`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt dubbing input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-09-07 — Lip-Sync Dialogue Adapter

Rewrite lines to match visible mouth movement, duration, pauses, emphasis, and acting beats while preserving plot-critical meaning.

- **Mode:** provider
- **Operation:** `adapt lip-sync`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt lip-sync input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-09-08 — Voice-Over Production Packager

Prepare actor-ready scripts, cue sheets, pronunciation notes, file names, character references, time limits, pickups, and delivery structure.

- **Mode:** offline
- **Operation:** `subtitles package`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py subtitles package source.srt --target localized.srt --target-locale es --title "Localized production" --output voiceover-plan.json'
```

#### catalog-09-09 — Video Localization QA

Check translated video for subtitle sync, reading speed, cropping, text-safe areas, missing graphics, audio balance, pronunciation, and final export integrity.

- **Mode:** hybrid
- **Operation:** `subtitles qa`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py subtitles qa source.srt --target localized.srt --output subtitle-qa.json'
```

#### catalog-09-10 — Short-Form Social Video Localizer

Localize hooks, captions, overlays, hashtags, calls to action, voice scripts, and platform descriptions for brief vertical or square videos.

- **Mode:** provider
- **Operation:** `adapt campaign`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt campaign input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

### Category 10: Images, Visual Text, and Design

#### catalog-10-01 — Image Text Detector and Translator

Locate text in screenshots, posters, comics, diagrams, signs, scans, and product images, then translate it with bounding boxes and reading order.

- **Mode:** provider
- **Operation:** `ocr`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py ocr page.png --target es --allow-network --config provider-config.json --output ocr-blocks.json'
```

#### catalog-10-02 — Screenshot Localization

Translate interface screenshots, preserve context, identify truncation risks, and provide replacement text mapped to every visible control and message.

- **Mode:** hybrid
- **Operation:** `visual`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py visual ocr-blocks.json --target es --output visual-plan.json'
```

#### catalog-10-03 — Graphic Text Replacement Planner

Create a layer-by-layer plan for replacing text in raster or editable artwork, including fonts, size, color, alignment, effects, and expansion handling.

- **Mode:** offline
- **Operation:** `visual`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py visual ocr-blocks.json --target es --output visual-plan.json'
```

#### catalog-10-04 — Comic and Manga Localizer

Translate dialogue, captions, sound effects, signs, notes, and cultural references while preserving character voice, panel order, balloon limits, and visual humor.

- **Mode:** hybrid
- **Operation:** `visual`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py visual ocr-blocks.json --target es --output visual-plan.json'
```

#### catalog-10-05 — Diagram and Infographic Localizer

Translate labels, legends, axes, callouts, instructions, citations, and alt text while keeping relationships and data accurate.

- **Mode:** hybrid
- **Operation:** `visual`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py visual ocr-blocks.json --target es --output visual-plan.json'
```

#### catalog-10-06 — Packaging and Label Localizer

Translate product packaging, ingredients, directions, warnings, claims, certifications, legal lines, and variable fields with market-specific review flags.

- **Mode:** hybrid
- **Operation:** `visual --domain packaging`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py visual --domain packaging ocr-blocks.json --target es --output visual-plan.json'
```

#### catalog-10-07 — Optical Layout Reconstruction

Rebuild OCR-derived text blocks with corrected order, spacing, paragraphs, tables, columns, and visual grouping after translation.

- **Mode:** offline
- **Operation:** `visual`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py visual ocr-blocks.json --target es --output visual-plan.json'
```

#### catalog-10-08 — Multilingual Font Compatibility Auditor

Check glyph coverage, shaping, ligatures, diacritics, vertical writing, RTL behavior, fallback fonts, licensing notes, and visual consistency.

- **Mode:** offline
- **Operation:** `font`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py font brand-font.ttf --text "Localized text 123" --locales es-MX --output font-audit.json'
```

### Category 11: Legal, Policy, Medical, and Technical Domains

#### catalog-11-01 — Legal Translation Assistant

Translate contracts, policies, notices, filings, and evidence with clause alignment, defined- term consistency, jurisdiction flags, and explicit human-review requirements.

- **Mode:** provider
- **Operation:** `translate --domain legal`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain legal input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-11-02 — Privacy and Consent Localizer

Translate privacy notices, consent dialogs, cookie text, data rights, retention language, and permissions without simplifying legally material distinctions.

- **Mode:** provider
- **Operation:** `translate --domain legal`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain legal input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-11-03 — Medical Translation Assistant

Translate patient information, forms, instructions, reports, and educational content with terminology control, dosage preservation, uncertainty flags, and clinical review routing.

- **Mode:** provider
- **Operation:** `translate --domain medical`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain medical input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-11-04 — Pharmaceutical Label Localizer

Translate medicine labels, contraindications, warnings, storage, usage instructions, adverse reactions, and packaging text with strict number and unit checks.

- **Mode:** provider
- **Operation:** `translate --domain pharmaceutical`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain pharmaceutical input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-11-05 — Technical Manual Translator

Translate procedures, specifications, warnings, parts, controls, diagrams, tables, and troubleshooting steps while preserving numbering and cross-references.

- **Mode:** provider
- **Operation:** `translate --domain technical`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain technical input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-11-06 — Software Documentation Localizer

Translate API guides, tutorials, release notes, command references, examples, warnings, and UI references while protecting code and identifiers.

- **Mode:** provider
- **Operation:** `translate --domain software-docs`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain software-docs input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-11-07 — Scientific and Academic Translator

Translate papers, abstracts, methods, results, equations, citations, units, terminology, and figure text without overstating conclusions.

- **Mode:** provider
- **Operation:** `translate --domain scientific`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain scientific input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-11-08 — Financial Content Localizer

Translate reports, disclosures, statements, product information, and investor materials while preserving figures, periods, currencies, definitions, and risk language.

- **Mode:** provider
- **Operation:** `translate --domain financial`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain financial input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-11-09 — Safety-Critical Instruction Translator

Translate emergency, industrial, transportation, and operational instructions with controlled language, warning hierarchy, exact units, and mandatory expert validation.

- **Mode:** provider
- **Operation:** `translate --domain safety`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain safety input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-11-10 — Patent and Claims Translator

Translate technical patent material with consistent terms, claim dependency preservation, figure references, and clear separation between literal and interpretive wording.

- **Mode:** provider
- **Operation:** `translate --domain patent`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain patent input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

### Category 12: Cultural Adaptation and Transcreation

#### catalog-12-01 — Marketing Transcreator

Recreate campaign meaning, persuasion, rhythm, and emotional effect for a target market rather than translating literally, with rationale and back-translation.

- **Mode:** provider
- **Operation:** `adapt transcreate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt transcreate input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-12-02 — Slogan and Tagline Adapter

Generate concise local alternatives that preserve brand intent, memorability, tone, and constraints, then screen for awkward or harmful interpretations.

- **Mode:** provider
- **Operation:** `adapt transcreate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt transcreate input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-12-03 — Humor, Idiom, and Wordplay Adapter

Replace jokes, idioms, puns, rhymes, and cultural references with functional equivalents while documenting unavoidable trade-offs.

- **Mode:** provider
- **Operation:** `adapt transcreate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt transcreate input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-12-04 — Cultural Sensitivity Reviewer

Flag stereotypes, taboo topics, symbols, gestures, imagery, colors, names, examples, and assumptions that may be confusing or harmful in a locale.

- **Mode:** provider
- **Operation:** `adapt cultural-review`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt cultural-review input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-12-05 — Inclusive Language Localizer

Apply respectful, audience-appropriate inclusive language while accounting for grammatical gender, identity terms, disability language, and local norms.

- **Mode:** provider
- **Operation:** `adapt inclusive-review`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt inclusive-review input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-12-06 — Campaign Variant Generator

Produce locale-specific headline, body, CTA, social, email, and ad variants within channel limits and approved claim boundaries.

- **Mode:** provider
- **Operation:** `adapt campaign`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt campaign input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-12-07 — Back-Translation and Intent Check

Translate localized content back into the source language to expose meaning drift, omissions, added claims, tone changes, and cultural substitutions.

- **Mode:** provider
- **Operation:** `adapt back-translate`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt back-translate input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-12-08 — Local Market Readability Adapter

Adjust sentence length, vocabulary, examples, measurement systems, references, and reading level for the target audience without changing facts.

- **Mode:** provider
- **Operation:** `translate --reading-level LEVEL`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --reading-level 6 input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

### Category 13: Quality Assurance and Validation

#### catalog-13-01 — Multilingual Quality Assurance Engine

Check accuracy, completeness, fluency, terminology, grammar, spelling, punctuation, formatting, tags, numbers, names, consistency, and locale conventions.

- **Mode:** hybrid
- **Operation:** `qa`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py qa --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv --output qa-report.json'
```

#### catalog-13-02 — Source-to-Target Completeness Checker

Detect untranslated, omitted, duplicated, added, reordered, or mismatched segments across files, pages, slides, strings, subtitles, and audio scripts.

- **Mode:** offline
- **Operation:** `qa`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py qa --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv --output qa-report.json'
```

#### catalog-13-03 — Number, Date, and Unit Validator

Compare all quantities, currencies, dates, times, percentages, ranges, signs, separators, conversions, and measurement units against the source.

- **Mode:** offline
- **Operation:** `qa`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py qa --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv --output qa-report.json'
```

#### catalog-13-04 — Tag and Placeholder Validator

Verify that protected tags, tokens, variables, links, shortcuts, escapes, and markup remain valid, complete, and correctly ordered.

- **Mode:** offline
- **Operation:** `qa`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py qa --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv --output qa-report.json'
```

#### catalog-13-05 — Linguistic Defect Classifier

Classify errors by severity and type, capture evidence, recommend corrections, track recurrence, and distinguish preference from objective defect.

- **Mode:** offline
- **Operation:** `qa`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py qa --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv --output qa-report.json'
```

#### catalog-13-06 — Bilingual Reviewer Assistant

Present aligned source and target content with glossary hits, context, risk flags, comments, decisions, and accept-reject workflows.

- **Mode:** hybrid
- **Operation:** `review`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py review qa-report.json --decisions review-decisions.json --output reviewed-report.json'
```

#### catalog-13-07 — Consistency Checker Across Assets

Compare names, terms, UI labels, capitalization, slogans, and recurring sentences across documents, apps, subtitles, websites, and marketing files.

- **Mode:** offline
- **Operation:** `qa`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py qa --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv --output qa-report.json'
```

#### catalog-13-08 — Locale Formatting Validator

Check decimal and thousands separators, currencies, addresses, phone numbers, postal codes, calendars, time formats, sorting, collation, and directionality.

- **Mode:** offline
- **Operation:** `qa`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py qa --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv --output qa-report.json'
```

#### catalog-13-09 — Regression Localization Tester

Compare new localized builds or files against prior approved versions to surface new defects, lost translations, changed IDs, and layout regressions.

- **Mode:** offline
- **Operation:** `delta`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py delta --previous previous.json --current current.json --source-locale en --target-locale es --memory memory.json'
```

#### catalog-13-10 — Final Release Readiness Auditor

Confirm all requested locales, assets, approvals, defects, exports, fonts, metadata, accessibility elements, and delivery structures are complete before release.

- **Mode:** offline
- **Operation:** `project release`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project release --project project.json --manifest release-manifest.json'
```

### Category 14: Accessibility and Learning

#### catalog-14-01 — Accessible Multilingual Content Localizer

Translate headings, link purpose, alt text, labels, instructions, tables, reading order, captions, and plain-language content without weakening accessibility.

- **Mode:** provider
- **Operation:** `translate --accessibility`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --accessibility input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-14-02 — Easy-Language Translator

Create simplified versions with short sentences, common words, explicit steps, and controlled concepts while preserving essential meaning and safety.

- **Mode:** provider
- **Operation:** `translate --tone easy-language`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --tone easy-language input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-14-03 — Sign-Language Script Adapter

Prepare spoken or written content for sign-language interpretation by clarifying structure, names, timing, spatial references, and visual context.

- **Mode:** provider
- **Operation:** `adapt sign-language`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt sign-language input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-14-04 — Language Learning Parallelizer

Create aligned translations with vocabulary notes, grammar explanations, pronunciation help, graded difficulty, exercises, and answer keys.

- **Mode:** hybrid
- **Operation:** `align --learning`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py align --learning --source source.txt --target localized.txt --source-locale en --target-locale es --glossary ultimate-translation/asset-glossary.csv'
```

#### catalog-14-05 — Interlinear Gloss Builder

Produce word-by-word or morpheme-by-morpheme glosses with grammatical labels, literal translation, and natural translation for linguistic study.

- **Mode:** provider
- **Operation:** `adapt gloss`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py adapt gloss input.txt --original source.txt --source-locale en --target-locale es --allow-network --config provider-config.json --output adaptation.json'
```

#### catalog-14-06 — Reading-Level Localizer

Adapt translations to a specified reading level while preserving required terminology, facts, instructions, and respectful tone.

- **Mode:** provider
- **Operation:** `translate --reading-level LEVEL`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --reading-level 6 input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-14-07 — Multilingual Assessment Localizer

Translate tests, rubrics, choices, instructions, distractors, and scoring notes while protecting difficulty, validity, and answer equivalence.

- **Mode:** provider
- **Operation:** `translate --domain assessment`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --domain assessment input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-14-08 — Alt Text Translation Specialist

Translate alternative text concisely while preserving the image's purpose, relevant visual details, functional meaning, and surrounding context.

- **Mode:** provider
- **Operation:** `translate --accessibility`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --accessibility input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

### Category 15: Automation, Integration, and Delivery

#### catalog-15-01 — Batch Translation Pipeline

Process large folders and mixed file sets with format detection, protected-content rules, resumable jobs, naming conventions, error logs, and per-file validation.

- **Mode:** hybrid
- **Operation:** `batch`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py batch input-files --output-dir localized-files --source en --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-15-02 — Localization API Integrator

Design requests, authentication handling, batching, retries, quotas, idempotency, callbacks, and validation for translation and localization services.

- **Mode:** provider
- **Operation:** `translate --provider http --allow-network`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py translate --provider http --allow-network input.txt localized.txt --target es --provider http --allow-network --config provider-config.json'
```

#### catalog-15-03 — Repository Localization Assistant

Scan source repositories for resources, hard-coded text, locale files, duplicate strings, missing keys, and changes, then prepare reviewable updates.

- **Mode:** offline
- **Operation:** `repository`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py repository source-repository --output repository-report.json'
```

#### catalog-15-04 — CI Localization Gate

Add automated checks for missing translations, broken message syntax, placeholder mismatches, unsupported locales, pseudolocalization defects, and untranslated releases.

- **Mode:** offline
- **Operation:** `validate ci`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py validate ci --input qa-report.json --fail-on-review --require-release-ready'
```

#### catalog-15-05 — File Format Conversion Coordinator

Convert assets into translation-safe intermediate formats and back while preserving IDs, metadata, structure, fidelity, and audit trails.

- **Mode:** offline
- **Operation:** `convert`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py convert extract input.txt exchange.json --source-locale en --target-locale es'
```

#### catalog-15-06 — Localization Package Builder

Assemble clean delivery folders containing localized assets, source references, termbases, memories, QA reports, reviewer notes, and import instructions.

- **Mode:** offline
- **Operation:** `project handoff`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project handoff  --project project.json --output localization-handoff.zip --files localized.txt --glossary ultimate-translation/asset-glossary.csv --style ultimate-translation/asset-style-guide.json'
```

#### catalog-15-07 — Multilingual Content Synchronizer

Keep equivalent content aligned across locales when source pages, product data, help articles, policies, or campaigns change.

- **Mode:** offline
- **Operation:** `delta`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py delta --previous previous.json --current current.json --source-locale en --target-locale es --memory memory.json'
```

#### catalog-15-08 — Translation Change Detector

Identify source edits at segment level, classify their impact, reuse valid prior translations, and isolate only content that needs retranslation.

- **Mode:** offline
- **Operation:** `delta`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py delta --previous previous.json --current current.json --source-locale en --target-locale es --memory memory.json'
```

#### catalog-15-09 — Secure Translation Workspace Manager

Apply least-access handling, temporary-file controls, redaction options, retention rules, secret detection, and safe handoff practices for sensitive materials.

- **Mode:** offline
- **Operation:** `secure`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py secure scan --input input.txt --output security-scan.json'
```

#### catalog-15-10 — Localization Archive Curator

Organize approved translations, versions, glossaries, memories, source snapshots, decisions, and release packages for reliable future reuse.

- **Mode:** offline
- **Operation:** `project handoff --archive`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project handoff --archive --project project.json --output localization-handoff.zip --files localized.txt --glossary ultimate-translation/asset-glossary.csv --style ultimate-translation/asset-style-guide.json'
```

### Category 16: Review, Reporting, and Governance

#### catalog-16-01 — Localization Decision Log

Record disputed terms, approved solutions, rationale, owner, affected assets, effective date, and future-use guidance.

- **Mode:** offline
- **Operation:** `project update`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project update  --project project.json --event event.json --expected-revision 1'
```

#### catalog-16-02 — Reviewer Feedback Consolidator

Merge comments from multiple reviewers, remove duplicates, surface conflicts, preserve evidence, and create an actionable correction list.

- **Mode:** offline
- **Operation:** `project feedback`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project feedback --project project.json'
```

#### catalog-16-03 — Localization Metrics Reporter

Report volume, completion, turnaround, rework, defect density, severity, terminology compliance, reuse, and review status without hiding quality risks.

- **Mode:** offline
- **Operation:** `project metrics`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project metrics --project project.json'
```

#### catalog-16-04 — Language Quality Sampling Planner

Define representative samples by risk, content type, locale, change level, and reviewer capacity, with escalation when defects exceed thresholds.

- **Mode:** offline
- **Operation:** `project sample`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project sample --project project.json --sample-rate 0.2'
```

#### catalog-16-05 — Multilingual Content Inventory

Create a searchable catalog of every source and localized asset, language, owner, version, location, status, dependency, and last approval.

- **Mode:** offline
- **Operation:** `project status`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project status --project project.json'
```

#### catalog-16-06 — Approval and Sign-Off Coordinator

Collect linguistic, product, legal, accessibility, and release approvals with clear scope, exceptions, dates, and accountable owners.

- **Mode:** offline
- **Operation:** `project update --confirm-sensitive`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project update --confirm-sensitive --project project.json --event event.json --expected-revision 1'
```

#### catalog-16-07 — Localization Risk Register

Track risks involving meaning, law, safety, culture, privacy, schedule, vendors, compatibility, fonts, and unsupported formats with mitigations and triggers.

- **Mode:** offline
- **Operation:** `project update`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project update  --project project.json --event event.json --expected-revision 1'
```

#### catalog-16-08 — Post-Release Feedback Analyzer

Collect and categorize user reports, store reviews, support tickets, telemetry clues, and reviewer findings to prioritize localization fixes.

- **Mode:** offline
- **Operation:** `project feedback`
- **Example:**

```sh
bash -lc 'python3 ultimate-translation/tools-translate.py project feedback --project project.json'
```


## Troubleshooting

| Symptom | Cause and recovery |
|---|---|
| `glossary_required` | The offline provider has no matching terms. Supply a valid glossary, use pseudo only for layout tests, or configure HTTP. |
| Untranslated residual text with glossary provider | Coverage is intentionally incomplete. Add reviewed terms, use translation memory, or route unmatched spans to a qualified translator/provider. |
| `token_mismatch` | The provider changed a marker, tag, variable, link, escape, or path. Retry a smaller batch or reject the output; do not manually delete the finding. |
| `checkpoint_mismatch` | Source, options, target, or segment order changed. Start a new output/checkpoint rather than forcing reuse. |
| `network_approval_required` | Set `translation.allow_network=true` and pass `--allow-network` after authorizing data sharing. |
| `insecure_endpoint` | Use the full approved HTTPS URL. HTTP is only for loopback tests; query/credential URLs and redirects are rejected. |
| `provider_response_invalid` | The endpoint is not returning the documented structured JSON. Inspect compatibility without logging secrets or source bodies, adjust prompts/model, and rerun with `--resume`. |
| `no_translatable_content` | Check `--keys`/`--columns`, `translatable=false`, empty `msgstr`, code-only content, or scanned/binary input. Run `inspect`. |
| `dependency_missing` | Install `ultimate-translation/tools-requirements.txt` for YAML/PDF/font operations. |
| `font_coverage` during PDF output | Set `pdf.font_path` to an authorized Unicode TrueType font with target glyph coverage and rerun. |
| Scanned PDF has no text | Rasterize authorized pages externally to PNG/JPEG, run `ocr`, correct the sidecar, and use `visual`; no PDF rasterizer is bundled. |
| YAML comments or anchors changed | This is warned behavior of safe load/dump. Use JSON or a YAML round-trip adapter for comment-critical workflows. |
| QA flags a valid inflected term | It is a review finding, not blind replacement evidence. Record a context decision in the glossary/project log. |
| `revision_conflict` | Re-read project state, reconcile changes, and apply the event with the current revision. |
| Handoff rejected | Remove nested archives/unsafe paths, include `handoff.json`, and rerun CRC/path validation. |
| CI exit 5 | The supplied report has blocking defects, requested review findings, or lacks release readiness. Correct source/output and rerun real QA; do not edit the gate result. |

## Extension and maintenance

1. Keep the primary entry point and all resources directly in `ultimate-translation/` with correct `tools-`, `asset-`, or `knowledge-` prefixes. Do not add a second knowledge file or a `SKILL.md`.
2. Add shared behavior to the appropriate module. Preserve load order and avoid circular imports. Add a new format by implementing extraction, stable IDs, rendering, intake, limits, invalid-input handling, and round-trip tests in `ultimate-translation/tools-formats.py`.
3. Add providers only through bounded validated adapters. Never embed credentials, private endpoints, arbitrary commands, or unbounded retries/uploads. Update environment setup and limitations here.
4. Keep stable segment IDs, source fingerprints, exact case-sensitive resource references, forward slashes, and future-skill-root-relative documentation paths.
5. Update `ultimate-translation/asset-capability-registry.json` only when operational behavior changes. Keep 144 unique source entries unless the source catalog itself changes, and run `capabilities` preflight.
6. Add valid, invalid, changed-input, recovery, and output-inspection tests to `ultimate-translation/tools-selftest.py`. Do not claim hosted compatibility from the loopback mock.
7. Run syntax compilation, help, capability preflight, the full self-test, clean-root copy test, ZIP extraction test, and case-sensitive reference checks before replacing a released archive.
8. Recalibrate locale/plural data against a pinned CLDR/ICU release and effort estimates against real vendor/reviewer metrics. Review prompts and glossary approvals as controlled production content.

## Validation actually completed

Validation was performed in the supplied Linux/Python environment with Python 3.13.16, PyYAML 6.0.3, pypdf 6.21.4, reportlab 4.5.1, and fonttools 4.68.0. No live external translation, OCR, speech, CMS, publishing, or credential service was used.

The repeatable `ultimate-translation/tools-selftest.py` suite completed **20 test groups with 0 failures, 0 errors, and 0 skipped**:

1. Main help, full 144-entry/16-category capability list, and command-reachability preflight.
2. Exact offline translation, changed-input producing changed output, missing input, malformed JSON, and structured errors.
3. JSON/YAML/CSV translation, pseudolocalization, ID/URL/type preservation, and selected columns.
4. HTML/Markdown/properties behavior, including scripts/styles/data attributes, entities, inline/fenced code, frontmatter, and links.
5. DOCX/XLSX/PPTX ZIP/XML round trips, notes/comments/shared strings/charts, formulas, field instructions, media bytes, entry order, and CRC.
6. Real PDF creation, extraction, translation, reconstruction, and pypdf inspection of the translated text.
7. Number/unit QA, glossary checks, reviewer decisions, memory build/clean, and TMX parsing/output.
8. Exchange schema/apply, delta reuse and changed segments, memory reuse, learning alignment, exercises, and answer keys.
9. SRT translation, timestamp preservation, timing adjustment, QA, and voice-over packaging.
10. Generated PCM/WAV assessment, normalize/trim/chunk outputs, manifest offsets, manual transcript correction/export, and glossary speech translation.
11. OCR sidecar schema/visual plan and a programmatically generated test font with missing-glyph detection; no machine font path was assumed.
12. Arabic cardinal categories, nested ICU gender/plural rendering, locale plans, Cyrillic forward/reverse transliteration, and multilingual detection.
13. High-risk routing, project optimistic revisions, approval rejection/confirmation, metrics, sampling, handoff creation, and CRC validation.
14. Repository hard-coded text/secret findings, redaction, cleanup approval/deletion, static-site routes, narrative graph checks, and CI failure exit.
15. Loopback HTTP translation with protected interpolation plus a planned second-batch failure and successful `--resume` from the first checkpoint.
16. Loopback multipart transcription, vision OCR, transcreation, and back-translation response validation.
17. Mixed-file batch success, fingerprint-based resume skip, and execution from a clean temporary skill root containing one test `SKILL.md` beside `ultimate-translation/`.
18. PO and Apple strings translation, VTT/ASS/TTML timestamp preservation, terminology extraction/validation/style/pronunciation, and XLIFF XML parsing.
19. Batch partial failure followed by corrected-input recovery, successful and rejected release manifests, CI rejection, and conflicting-feedback consolidation.
20. Campaign, cultural/inclusive review, gloss, learning, sign-language preparation, dubbing/lip-sync, meeting, pronunciation, narrative, and bilingual-review protocol routes, plus rejection of an HTTP redirect without output creation.

The suite's tests are integration tests through the real CLI, not mocked command handlers. Provider tests exercise the real HTTP client against a local mock, which validates protocol, limits, structured parsing, and recovery but **does not certify a hosted provider, model quality, OCR accuracy, speech accuracy, or diarization quality**.

## Package compatibility and archive validation

The delivery container is `ultimate-translation-skill-tool.zip`. The following structural and execution checks were completed on the assembled archive:

- Opened the ZIP and ran its CRC check; all 20 Skill Tool file entries passed. The archive contains no nested ZIP or other archive.
- Confirmed `ultimate-translation/` is the only Skill Tool folder at the ZIP root, with no outer package/wrapper folder. Extracting produces that completed folder immediately.
- Confirmed every archived name uses forward slashes, contains no absolute path or parent traversal, has correct prefixes, and is at most two levels below the future skill root (`ultimate-translation/<file>`), within the three-level limit.
- Confirmed no `SKILL.md` exists inside the Skill Tool, exactly one `knowledge-readme.md` exists, and no internal subfolders or unclassified files are packaged.
- Extracted into a clean temporary location, placed one temporary test `SKILL.md` beside `ultimate-translation/`, and reran main help, all-144 capability preflight, and offline text translation using only relative paths. The extracted output was `Hola mundo.` as expected.
- Compared SHA-256 fingerprints of every source folder file with its extracted counterpart; all matched before runtime caches were created. All 19 documentation references to packaged files resolved with exact case-sensitive spelling, and all 144 generated capability examples parsed through the real CLI.
- The tested future combined package had 21 files when one test `SKILL.md` was added, well below the 500-file limit. The temporary test `SKILL.md` is not included in the delivery ZIP.

The ZIP is only the delivery container. Copy/extract `ultimate-translation/` beside the parent skill's root `SKILL.md`; do not add a second `SKILL.md` inside this component.

## Known limits and unavailable integrations

- There is no universal offline neural machine translator. Offline translation is limited to approved glossary/memory reuse; pseudo is stress testing. Arbitrary fluent translation, back-translation, cultural review, transcreation, interlinear glossing, meeting summaries, and AI bilingual review require a configured provider or human workflow.
- No live hosted integration was available or tested. Model names, endpoints, quotas, licenses, and credentials remain user-configured. Local mock success must not be represented as hosted success.
- No local OCR engine or PDF rasterizer is bundled. Scanned PDF pages must be rendered to images by an approved external tool before `ocr`; redaction and publication need independent legal/visual verification.
- No local speech recognizer or overlap-capable diarizer is bundled. No FFmpeg/media extraction, video decoding, screen-text detection in video, speech synthesis, audio mixing, or verified lip sync is included.
- PDF reconstruction is text-first and reflowed, not a faithful page clone. OOXML layout/rendering, OLE objects, proprietary extensions, raster text, and application-specific fields need target-application inspection.
- YAML round-trip styling/comments, multiline/plural PO, multiline resource escapes, malformed HTML, TTML timing adjustment, direct databases, CMS publishing, proprietary game engines, and framework-specific resource semantics require additional adapters or manual conversion.
- Language detection, readability, terminology, semantic/cultural checks, repository scans, and secret scans are heuristic where stated. They cannot prove accuracy, fluency, inclusivity, completeness of secret detection, legal compliance, or absence of risk.
- Locale and ICU support is a bounded common-locale subset, not full CLDR. Font inspection is not rendered shaping proof or legal clearance.
- Handoff and release checks validate supplied evidence and file integrity; they do not independently authenticate reviewers, sign approvals, deploy, publish, or control external retention/deletion.
