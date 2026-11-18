# Offline Neural Voice — Skill Tool Reference

`offline-neural-voice` is a complete, working Skill Tool for **local, CPU-only, air‑gapped capable neural
text-to-speech** with rights gating, expressive delivery presets, waveform mastering, measurable quality
assurance, packaging and lifecycle governance.

It implements the capability map of *Offline Neural Voice Skill Catalog, Edition 1.0 (Nov 2026)* — 18
categories, 626 catalogued skill concepts — as **one unified Skill Tool** with a single primary entry point
and six supporting modules. Every capability listed below is implemented and was executed during
validation; nothing is described as working that was not run.

This folder is a **Skill Tool component**, not a standalone Copilot skill. It contains no `SKILL.md`; a
parent package supplies exactly one `SKILL.md` at its root, and this folder is copied beside it.

---

## 1. Purpose

Deliver speech from text with no cloud service, no API key, no GPU and no first-run download, while
proving — with measurements, not adjectives — that the output is correct, licence-appropriate and
reproducible.

Four non-negotiable properties, enforced in code rather than in prose:

| Property | Enforcement |
|---|---|
| Local execution | ONNX Runtime CPU inference; sockets are blocked for the whole synthesis path (`nvcore.offline_guard`), proven per run |
| CPU compatibility | 16-bit PCM pipeline, int8/float32 ONNX Runtime, single-threaded by default, measured on a 2-core sandbox |
| No required API key | No credential, token or paid endpoint is read anywhere; the only network call is the optional model download, which is approval gated |
| Explicit verification | Every run emits metrics, checksums, phoneme transcripts and a pass/warn/fail verdict; QA is a separate tool that re-measures the finished file |

---

## 2. Folder contents

The folder is **flat**: every file sits directly in `offline-neural-voice/`, every name carries one of the
three allowed prefixes, and there are no subfolders. Runtime state (models, audio, journal) is created
elsewhere — see *State directory* below — so copying or packaging the folder never has to skip anything.

```
offline-neural-voice/
├── tools-nv-voice.py                 primary entry point: 25 commands
├── tools-nvcore.py                   shared runtime (config, path seal, offline guard, WAV, loudness, journal)
├── tools-nv-engine.py                phonemizer, phoneme ids, ONNX VITS engine, piper adapter, fallback engine
├── tools-nv-text.py                  text normalisation, lexicon, inline directions, chunking, script guard
├── tools-nv-master.py                mastering chain, loudness normalisation, limiter, WAV/MP3 export
├── tools-nv-qa.py                    measurable QA, ASR transcript comparison, acceptance scenario
├── tools-nv-ops.py                   fetch/import models, manifest, package, diagnostics, benchmark
├── tools-nv-config.py                configuration workbench (show/validate/init/diff/migrate/explain)
├── knowledge-readme.md               this file (the single knowledge document)
├── asset-default-config.json         merged-runtime configuration (all defaults)
├── asset-model-registry.json         pinned voices: digests, sizes, sample rate, speakers, licence, source
├── asset-voice-presets.json          9 versioned delivery presets
├── asset-rights-policy.json          licence catalogue, use gates, consent rules, fallback disclosure
├── asset-pronunciation-lexicon.json  20 pronunciation entries (12 active, 8 off-by-default library)
├── asset-acceptance-fixture.json     the video-greeting acceptance case and tolerances
├── asset-batch-manifest.json         batch plan template consumed by `batch`
├── asset-result-schema.json          JSON Schema every command result validates against
├── asset-config-schema.json          JSON Schema for configuration documents
└── asset-caption-policy.json         SRT/VTT/TXT caption rules and degraded-audio labelling
```

**19 files** — 8 `tools-*`, 10 `asset-*`, 1 `knowledge-*`. No subfolders, no archives, no hidden files, no
`SKILL.md` (a parent package supplies exactly one `SKILL.md` at its root).

### State directory (where output actually goes)

Everything the tool *writes* lives in a per-user state directory, outside the flat folder:

| Purpose | Default path | Config key |
|---|---|---|
| installed voice pairs | `~/.local/state/offline-neural-voice/models/` | `paths.models_dir` |
| rendered audio, captions | `~/.local/state/offline-neural-voice/output/` | `paths.output_dir` |
| evidence bundles | `~/.local/state/offline-neural-voice/output/evidence/` | `paths.evidence_dir` |
| journal, scratch, ASR models | `~/.local/state/offline-neural-voice/work/` | `paths.work_dir` |

`paths.state_dir` is the single knob: change it and every derived path follows (all six are explicit keys,
so you can also point them individually at any location inside `paths.state_dir`). The directory is created
on first use. Paths are reported back in envelopes as `~/.local/state/...`, so results stay portable
between machines.

Why this shape: a Skill Tool folder may only contain prefixed files, and shipping a 140 MB model inside it
would also break the size budget. The trade-off is that the ZIP alone is not immediately runnable — you
complete one install step (`fetch-model`, §8.1) and everything works afterwards. `doctor` tells you exactly
what is missing until then.

### Path rules

* Paths you pass on the command line (`--audio`, `--text-file`, `--source`, `--model`) are **sealed**:
  relative to the skill folder, no `..`, no absolute paths unless
  `execution.allow_absolute_paths=true` is set by an operator.
* Paths that come from **configuration** may live in `paths.state_dir`; nothing else is auto-trusted.
* Paths inside the state directory are always accepted, which is what makes
  `qa --audio ~/.local/state/offline-neural-voice/output/x.wav` work without loosening the seal.

### Why each tool exists (loading order)

| Order | File | Role | Why it is separate |
|---|---|---|---|
| 1 | `tools-nvcore.py` | config merge/validation, sealed path resolution, offline socket guard, atomic writes, JSON-Schema validator, WAV I/O, resampling, BS.1770 loudness, true peak, limiter, cancellation, journal with crash recovery, exit codes | Everything depends on it; keeping it separate means one implementation of safety-critical primitives instead of six |
| 2 | `tools-nv-engine.py` | espeak/pipeline phonemization, phoneme-id mapping, ONNX VITS inference, piper-tts reference adapter, degraded fallback engine, voice resolution and integrity verification | The only file that touches model runtimes; can be swapped or stubbed without touching text, mastering or QA |
| 3 | `tools-nv-text.py` | number/date/money/time/unit expansion, markup and emoji stripping, IPA lexicon, inline directions, sentence chunking, script guard | Pure functions with no audio or model dependency; independently testable |
| 4 | `tools-nv-master.py` | trim, high-pass, de-ess, EQ, resample, loudness normalisation, limiter, fades, WAV and MP3 export | Deterministic DSP, testable with synthetic tones |
| 5 | `tools-nv-qa.py` | threshold checks, ASR transcript comparison, regression comparison, acceptance scenario | Measurement must not share code paths with production in a way that could hide a fault; it re-decodes the file |
| 6 | `tools-nv-ops.py` | model intake (download or local import), manifest, release packaging, diagnostics, benchmark | The only network-capable code, isolated so the air gap is auditable |
| 7 | `tools-nv-config.py` | configuration workbench | Operational tooling; also the documented entry for the validator the runtime points to |
| — | `tools-nv-voice.py` | **primary entry point**: argument parsing, capability routing, approval gates, result envelopes | One documented entry point; all commands reachable from it |

Modules 2–6 are importable by the entry point through `nvcore.load_sibling()`, which loads the
hyphenated filenames by path and registers them as `nvengine`, `nvtext`, `nvmaster`, `nvqa`, `nvops`.
Each module also runs its own self test: `python tools-nv-engine.py selftest`.

---

## 3. Requirements and installation

### Runtime

* Python 3.9 – 3.13 (validated on CPython 3.13.16).
* Any x86-64 or ARM64 CPU with 2 or more cores; ~64 MiB disk per installed voice model.
* Linux, macOS or Windows. Linux paths were exercised; commands below use `bash`.
* **Required packages**

```bash
bash -lc 'pip install numpy onnxruntime'
```

* **Recommended packages** (each unlocks a feature; all were installed and used during validation)

```bash
bash -lc 'pip install piper-tts lameenc soundfile jsonschema faster-whisper'
```

| Package | Needed for | Behaviour when missing |
|---|---|---|
| `numpy` | ONNX tensors, waveform arrays | required — `dependencies` reports it, `synthesize` fails with exit 3 |
| `onnxruntime` | CPU inference | required — same |
| `piper-tts` | espeak-ng phonemization bridge, reference-parity checks | falls back to the `espeak-ng` executable, then to manual `[[ipa]]` input |
| `lameenc` | MP3 export | MP3 is skipped with a warning; WAV still produced |
| `soundfile` | MP3 decode verification, QA of MP3 files | `qa` verifies WAV only; MP3 verification warns |
| `jsonschema` | independent schema cross-check in tests | the built-in validator (`nvcore.validate_json`) is always used |
| `onnx` | graph/opset/metadata inspection | `inspect-model` falls back to a container header probe |
| `faster-whisper` | optional transcript accuracy check | `qa --transcript` reports `available:false` and a warn verdict |

* **Optional system binary:** `espeak-ng` (used only if `piper-tts` is absent).
* **Offline installation:** build a wheelhouse once on a connected machine and install with
  `pip install --no-index --find-links <wheelhouse> numpy onnxruntime piper-tts lameenc soundfile`.
  No package is downloaded at synthesis time.

### Air-gapped start

```bash
bash -lc 'python pip-free-check >/dev/null 2>&1 || true'   # ignored; informational only
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py doctor'
```

`doctor` prints 13 checks (packages, CPU, memory, registry, installed voices, presets, rights policy,
output/work writability, phonemizer, journal, offline policy) and returns non-zero if any fails.

---

## 4. Exact commands

All commands run **from inside `offline-neural-voice/`** so that relative asset paths resolve. The shell
is stated explicitly; add `-B` to avoid writing `__pycache__` into the packaged folder.

### Primary entry point

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py --help'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py list-tools'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py list-categories --format text'
```

### The canonical acceptance run

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py acceptance --overwrite'
```

Produces `~/.local/state/offline-neural-voice/output/acceptance-greeting.wav`, `.mp3` and
`~/.local/state/offline-neural-voice/output/evidence/acceptance-greeting.acceptance.json`
with 15 checks, and exits 0 only when every check passes.

### Self tests

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nvcore.py selftest'
bash -lc 'cd offline-neural-voice && python3 -B tools-nvcore.py selftest-all'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-engine.py selftest --model ~/.local/state/offline-neural-voice/models/en_US-joe-medium.onnx'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-config.py selftest'
```

`selftest-all` runs every module self test in loading order and aggregates the result.

### Global options (accepted by the entry point and every subcommand)

| Option | Meaning |
|---|---|
| `--config FILE` | configuration JSON merged over the built-in defaults |
| `--format json\|text` | `json` (default, machine readable) or `text` (screen-reader friendly, never colour-only) |
| `--verbose` | include full data in text output |
| `--quiet` | no stdout on success |
| `--no-journal` | do not append to the audit journal |
| `--actor NAME` | label recorded in the journal |

Exit codes: `0` ok · `1` fail · `2` usage · `3` dependency · `4` invalid input · `5` approval required ·
`130` cancelled · `143` interrupted. `python3 -B tools-nv-voice.py explain exit-codes` prints them.

---

## 5. Command reference

25 commands. Each accepts `--format json` (a result envelope) and returns one of the documented exit codes.

### Discovery

| Command | What it does |
|---|---|
| `list-tools` | every command, its capability categories, the seven tools and ten assets |
| `list-categories` | the 18 catalog categories and the commands that implement each |
| `list-voices [--voice ID] [--use USE] [--redistribute-use] [--details]` | registry voices, install state, licence decision per use |
| `list-presets` | 9 presets with resolved engine scales |
| `help <command>` | the argparse help text plus the capability mapping |

### Model and environment

| Command | What it does |
|---|---|
| `fetch-model --voice ID [--plan] [--approve --approval-token T --approved-by NAME] [--source-dir DIR]` | the only network step; `--plan` prints URLs, digests and sizes and stops; `--source-dir` imports from a local folder instead (air-gapped) |
| `inspect-model [--voice ID \| --model PATH --voice-config PATH] [--speaker S]` | ONNX producer, opset, IR version, node count, parameter count, initializer bytes, inputs/outputs, op histogram, footprint, CPU feasibility, runtime provider probe |
| `compatibility` | model/voice/tokenizer/opset/runtime matrix for every registry voice, phonemizer reachability, engine availability |
| `doctor` | 13 environment/asset checks with remediation text |
| `offline-check` | proves the socket guard blocks outbound connections; reports environment reachability separately |
| `dependencies` | per-package availability, version, purpose and install command |

### Text preparation

| Command | What it does |
|---|---|
| `prepare-text --text "..." \| --text-file F \| stdin [--voice ID] [--locale TAG] [--chunk-chars N]` | normalisation plan: prepared text, per-rewrite audit, chunks, inline directions, phoneme transcription, phoneme-map coverage, script guard report |

### Synthesis

| Command | What it does |
|---|---|
| `synthesize` | text → phonemes → ONNX VITS → mastering → WAV (+MP3, +captions, +evidence). Key options: `--voice --speaker --preset --name --engine --seed --locale --use --mp3 --captions --target-lufs --cancel-file --plan --overwrite --approve --approval-token` |
| `preset-build [--preset ID ...]` | renders the same sentence under several presets for A/B listening |
| `batch --plan-file F` | synthesises a JSON plan of clips, each allowed its own voice/preset/engine; exits non-zero unless every clip succeeds |

`synthesize --plan` performs no synthesis: it resolves the voice (including integrity verification), the
rights decision, the preset, the text preparation, the planned writes and a `plan_token`.

### Quality and evidence

| Command | What it does |
|---|---|
| `qa --audio FILE [--expect-text T] [--transcript] [--asr-model M] [--min-seconds X --max-seconds Y]` | re-decodes the file and checks: decodable, 16-bit PCM, duration window, integrated loudness, true peak, clipped samples, DC offset, silence ratio, RMS window, buzz proxy, offline policy, optional ASR word accuracy |
| `compare --file-a A --file-b B` | metric deltas between two files |
| `evidence --audio FILE [--expect-text T] [--transcript]` | combined bundle: audio report, QA verdict, environment, offline probe, dependency report, tool checksums |
| `acceptance [--overwrite]` | runs the catalogued video-greeting scenario end to end and writes an acceptance evidence file |

### Operations

| Command | What it does |
|---|---|
| `license-audit [--voice ID] [--out F]` | rights decision for each voice across personal, commercial, redistribution and cloning uses, with obligations |
| `manifest --directory DIR` | versioned manifest with per-file SHA-256, sizes and provenance pins |
| `package --source DIR --name N [--plan] [--approve --approval-token T]` | approved release folder of plain files (no archives) plus its own manifest |
| `journal [--tail N] [--kind K] [--recover]` | audit trail tail and stale-intent recovery |
| `benchmark [--runs N] [--voice ID]` | phonemizer, model load and inference timing, real-time factor |
| `explain <topic>` | `exit-codes`, `failures`, `commands`, `engines` |

### Configuration workbench

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-config.py show --format text'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-config.py init --out work/my-config.json'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-config.py validate --config work/my-config.json'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-config.py diff --config work/my-config.json --format text'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-config.py migrate --config work/old.json --out work/new.json'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-config.py explain execution.offline'
```

---

## 6. Catalog coverage (18 categories → implementation)

| # | Catalog category | Implemented by | What is genuinely delivered |
|---|---|---|---|
| 1 | Neural Models, Weights, and Voice Assets | `list-voices`, `inspect-model`, `compatibility`, `fetch-model`, engine integrity checks | Pinned registry (5 voices, MD5 + SHA-256, sizes, sample rate, speaker maps), ONNX graph/opset/interface inspection, multi-speaker `sid` support, speech-rate-correct voice resolution, digest verification before every synthesis |
| 2 | Offline Dependencies and Installation | `dependencies`, `doctor`, `fetch-model --source-dir` | Local-only dependency inventory with versions and install strings; wheelhouse instructions; sneakernet model intake with digest checks |
| 3 | Voice Engine and Neural Inference Tools | `synthesize`, `benchmark`, `preset-build`, `tools-nv-engine.py` | Own ONNX VITS CPU pipeline (frame-for-frame parity with the reference implementation at `noise_scale=0`), piper-tts adapter, warm-up/load timing, bounded inference timeout, deterministic seed control |
| 4 | Voice Presets and Performance Direction | `list-presets`, `preset-build`, `synthesize --preset`, `asset-voice-presets.json` | 9 versioned presets mapping delivery direction to `length_scale`, `noise_scale`, `noise_w`, EQ curve and loudness target, with hard bounds |
| 5 | Text Preparation, Normalization, and Pronunciation | `prepare-text`, `synthesize`, `tools-nv-text.py`, lexicon asset | Number/ordinal/year/decimal/money/percent/date/time/unit expansion, abbreviation and symbol expansion, markup and emoji removal, HTML entity decoding, IPA and respelling lexicon, inline `{pause:ms}`/`*emphasis*` directions, chunking, **unsupported-script guard** |
| 6 | Audio Rendering, Mastering, and Export | `synthesize`, `compare`, `tools-nv-master.py` | Trim, 70 Hz high-pass, de-ess, EQ curves, band-limited resample, two-pass loudness normalisation, look-ahead limiter, fades, deterministic 16-bit PCM WAV (atomic), verified MP3 |
| 7 | Quality Assurance and Acceptance Testing | `qa`, `evidence`, `acceptance`, `tools-nv-qa.py` | 11 measured checks per file plus transcript accuracy and acceptance scenario; every check carries measured value, expected range, cause and remediation |
| 8 | Skill Components, Scripts, and References | `list-tools`, `explain`, this document | Seven tools with documented roles and loading order; single knowledge document; 10 machine-readable assets |
| 9 | Offline Package and Distribution | `package`, `manifest` | Approval-gated release folder, SHA-256 manifest, provenance pins, no archives, no wrapper folders |
| 10 | Sandbox, Runtime, and Resource Management | `doctor`, `benchmark`, `offline-check`, sealed paths | Thread control, memory envelope reporting, per-utterance and per-run deadlines, cooperative cancellation, root-sealed paths, atomic writes |
| 11 | Safety, Rights, Consent, and Provenance | `license-audit`, `synthesize --use`, `journal`, `asset-rights-policy.json` | Licence catalogue with commercial/redistribution/share-alike flags, consent records for cloning, blocking decisions, disclosure of degraded engines, provenance in every evidence bundle |
| 12 | Fallbacks, Routing, and Resilience | `synthesize --engine`, engine auto-routing, `explain failures` | Three engines with automatic, **disclosed** degradation; retries/timeouts; journal intent + recovery for interrupted runs |
| 13 | Final Acceptance Scenario: Video Greeting | `acceptance`, `asset-acceptance-fixture.json` | The exact catalogued case: 2.5–4.0 s, ≤ −1 dBTP, ≈ −14 LUFS, PCM WAV, optional decodable MP3, offline, with prosody and integrity checks |
| 14 | Performance, Benchmarking, and Optimization | `benchmark`, `inspect-model` footprint | Median/ min/max load and inference timing, real-time factor, parameter and initializer byte counts |
| 15 | Integration, Interfaces, and Automation | JSON envelopes, `batch`, exit codes, `asset-result-schema.json` | Every command emits a schema-validated envelope; batch plans; stable exit codes |
| 16 | Diagnostics, Observability, and Support | `doctor`, `journal`, `explain`, structured errors with hints | Actionable error codes, hints, details; audit journal with rotation; recovery scan |
| 17 | Accessibility, Localization, and Inclusive Output | `--format text`, locale profiles, captions, presets | Screen-reader-friendly text output with status words (never colour-only), plain-language mode, en-US/en-GB profiles, SRT/VTT/TXT captions, slow-speech and high-articulation presets, degraded-audio caption label |
| 18 | Governance, Release, and Lifecycle Management | `manifest`, `package`, `acceptance`, `license-audit`, config migration | Version pinning of models/presets/config, change records inside every evidence bundle, release gate, reproducible build verification, config migration |

Overlapping catalog entries are intentionally **not** removed: each is assigned to the single command that
owns it, and shared logic is reused through `nvcore`.

---

## 7. Inputs, outputs, parameters

### Result envelope (stdout, `--format json`)

Every command prints one JSON object validated against `asset-result-schema.json`:

```json
{
  "schema_version": 1,
  "ok": true,
  "tool": "offline-neural-voice",
  "command": "synthesize",
  "skill": "offline-neural-voice",
  "skill_version": "1.0.0",
  "generated_at": "2026-11-07T12:00:00Z",
  "started_at": "2026-11-07T11:59:58Z",
  "duration_s": 2.31,
  "approval_required": false,
  "cancelled": false,
  "errors": [],
  "warnings": ["..."],
  "artifacts": [{"kind": "mastered-wav", "path": "output/x.wav", "bytes": 112280, "sha256": "…"}],
  "data": {"summary": "…", "…": "command specific"}
}
```

On failure `ok` is `false`, `errors` carries `{code, message, hint, details}` and the process exit code is
non-zero. Errors always carry a `hint` when a corrective action exists.

### Configuration

Three layers, highest wins: **built-in defaults** < **`--config` file** < **`ONV_*` environment**.

| Key | Default | Effect |
|---|---|---|
| `execution.offline` | `true` | blocks sockets during synthesis/QA |
| `execution.timeout_seconds` | `120` | per-utterance inference timeout |
| `execution.run_timeout_seconds` | `900` | whole-run deadline |
| `execution.strict_checksum` | `true` | verify digests against the registry before synthesis |
| `execution.allow_fallback_engine` | `true` | permit the clearly labelled degraded engine |
| `execution.fail_on_blocked_rights` | `true` | block synthesis when the rights gate fails |
| `execution.max_chars` | `4000` | hard text limit per request |
| `execution.script_guard` | `block` | `block`/`warn`/`off` for characters the voice cannot pronounce |
| `execution.allow_absolute_paths` | `false` | operator override for trusted absolute paths |
| `inference.intra_op_threads` | `1` | ONNX Runtime CPU threads |
| `inference.providers` | `["CPUExecutionProvider"]` | execution providers |
| `synthesis.seed` | `1337` | sampler seed (noise scaling is stochastic — see §11) |
| `mastering.target_lufs` | `-14.0` | integrated loudness target |
| `mastering.max_true_peak_dbfs` | `-1.0` | limiter ceiling |
| `mastering.loudness_tolerance_lu` | `1.0` | accepted loudness deviation |
| `mastering.loudness_correction_passes` | `3` | bounded gain-into-limiter passes that pull the master onto target |
| `mastering.max_limiter_drive_db` | `6.0` | ceiling on how far those passes may push the limiter |
| `qa.min_duration_s` / `qa.max_duration_s` | `2.5` / `4.0` | duration window for the **acceptance** scenario only |
| `qa.default_min_duration_s` / `qa.default_max_duration_s` | `0.0` / `0.0` | duration window for a standalone `qa` run; `0` means "no bound", so a 1 s sound bite is not judged against a 30 s scenario |
| `qa.min_word_accuracy` | `0.85` | ASR accuracy threshold |
| `safety.require_approval_for_release` | `true` | packaging/publish approval |
| `safety.require_consent_for_cloning` | `true` | consent record required |
| `journal.enabled` / `journal.max_bytes` | `true` / `2000000` | audit trail and rotation |
| `paths.*` | `models`, `output`, `work`, … | all relative; escapes are rejected |

Environment overrides (examples): `ONV_OFFLINE=false`, `ONV_TARGET_LUFS=-16`, `ONV_SEED=7`,
`ONV_MAX_CHARS=800`, `ONV_MODELS_DIR=models`.

Full lists: `python3 -B tools-nv-config.py show --format text`. The `--format` flag is accepted after the
subcommand on both the entry point and the workbench.

### Voice registry

`asset-model-registry.json` pins, per voice: `id`, `aliases`, `language`, `espeak_voice`, `quality`,
`sample_rate`, `num_speakers`, `license_id`, upstream `source` (repository/revision/index key), download
`base_url`, and `files` with `model`, `config`, `model_md5`, `model_size`, `config_md5`,
`config_size`, and `model_sha256`/`config_sha256` once verified locally.

Shipped voices:

| id | lang | gender / age style | quality / rate | speakers | licence | state |
|---|---|---|---|---|---|---|
| `en-us-joe-medium` (default) | en-US | male / 30s | medium / 22 050 | 1 | CC0 dataset | **installed, SHA-256 pinned** |
| `en-us-bryce-medium` | en-US | male / adult | medium / 22 050 | 1 | public domain | registry only |
| `en-gb-northern-english-male-medium` | en-GB | male / adult | medium / 22 050 | 1 | CC BY-SA 4.0 | registry only |
| `en-us-libritts-r-medium` | en-US | mixed | medium / 22 050 | **904** | CC BY 4.0 | **installed, SHA-256 pinned** |
| `en-us-amy-low` | en-US | female / adult | low / 16 000 | 1 | Mimic3 dataset terms (unresolved) | registry only; commercial/redistribution **blocked** |

### Presets

`asset-voice-presets.json` ships `warm-friendly` (default), `neutral-narration`, `bright-promo`,
`calm-support`, `slow-accessibility`, `high-articulation`, `documentary-male-30s`, `expressive-story`,
`announcement`. Each sets `length_scale`, `noise_scale`, `noise_w`, `eq_curve` and mastering targets, and
is clamped to `length 0.45–2.2`, `noise 0.2–1.2`.

---

## 8. What each capability does (worked examples)

### 8.1 Fetch or import a voice

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py fetch-model --voice en-us-bryce-medium --plan'
```
Prints the two URLs, expected MD5, expected size, target paths and a `plan_token`, and writes nothing.

```bash
bash -lc 'cd offline-neural-voice && TOKEN=$(python3 -B tools-nv-voice.py fetch-model --voice en-us-bryce-medium --plan | python3 -c "import json,sys;print(json.load(sys.stdin)[\"data\"][\"plan_token\"])") && python3 -B tools-nv-voice.py fetch-model --voice en-us-bryce-medium --approve --approval-token "$TOKEN" --approved-by "s.operator"'
```

Air-gapped alternative (no network at all, still digest-verified):

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py fetch-model --voice en-us-bryce-medium --source-dir ~/piper-downloads'
```

Downloads are capped at 400 MiB, retried twice, written to a temporary file and renamed atomically; a
digest mismatch deletes the partial file and installs nothing.

### 8.2 Synthesize speech

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py synthesize --text "Greetings, friends! Welcome to my video." --voice en-us-joe-medium --preset warm-friendly --mp3 --captions'
```

Result (measured on the build machine): 2.52 s, −14.99 LUFS, −1.00 dBTP, engine `onnx-vits`, 0 clipped
samples, artifact `output/greetings-friends-welcome-to.wav` plus `.mp3`, `.srt`, `.vtt`, `.txt` and an
evidence JSON.

Dry run first:

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py synthesize --text "Draft line." --voice en-us-joe-medium --plan'
```

Multi-speaker:

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py synthesize --text "Multi speaker check." --voice en-us-libritts-r-medium --speaker 103 --name multi'
```

### 8.3 Inspect and prove compatibility

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py inspect-model --voice en-us-joe-medium --format text'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py compatibility --format text'
```

Observed for `en-us-joe-medium`: producer `pytorch 2.0.0`, opset 15, IR 8, 2 755 nodes, 15 650 556
parameters, 59.70 MiB of weights, inputs `input`, `input_lengths`, `scales`, provider
`CPUExecutionProvider`.

### 8.4 Prepare text

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py prepare-text --text "The launch is 2026-11-07 at 09:30 for \$19.99, 12% faster." --voice en-us-joe-medium --format text'
```

Produces e.g. `The launch is November seventh twenty twenty-six at nine thirty o'clock for nineteen
dollars and ninety-nine cents, twelve percent faster.` with each rewrite listed.

### 8.5 Quality assurance

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py qa --audio ~/.local/state/offline-neural-voice/output/acceptance-greeting.wav --expect-text "Greetings, friends! Welcome to my video."'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py qa --audio ~/.local/state/offline-neural-voice/output/acceptance-greeting.wav --expect-text "Greetings, friends! Welcome to my video." --transcript'
```

`--transcript` runs local faster-whisper ASR and reports word accuracy plus missing/extra words. When a
local ASR model is not staged and the policy is offline, it returns `available:false` with a warn verdict
rather than downloading.

### 8.6 Acceptance

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py acceptance --overwrite'
```

15 checks: decodable, 16-bit PCM, duration 2.5–4.0 s, loudness window, true peak, clipped samples, DC
offset, silence ratio, RMS, buzz proxy, offline policy, rights decision, engine, MP3 decodability, prosody.

**Duration calibration.** VITS sampling makes clip length vary from run to run (the same text rendered
2.44 s, 2.52 s and 2.50 s across three runs), which straddles the 2.5 s floor of the catalogued window.
`acceptance` therefore renders a *timed* take: `engine.solve_length_scale()` brackets the target with a
few cheap probes and interpolates between them, then synthesises the audio that is actually measured. The
fixture asks for `duration_target_s: 3.25` (the middle of the window) with a 0.10 s tolerance, so ordinary
sampling variance cannot push the result across a boundary. Every probe is recorded in the evidence file
under the `duration-calibration` check, so the chosen `length_scale` is auditable rather than magic.

`qa` on its own file is deliberately **not** held to the 2.5–4.0 s acceptance window: it reports the
measured duration and passes unless you supply `--min-seconds/--max-seconds` or change
`qa.default_min_duration_s`. Use `acceptance` when you want the catalogued scenario's bounds enforced.

### 8.7 Rights

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py license-audit --voice en-us-amy-low --format text'
```

`en-us-amy-low` returns `block` for commercial release and public redistribution because its dataset terms
are unresolved; `en-us-joe-medium` (CC0) and `en-us-bryce-medium` (public domain) return `allow`.

### 8.8 Package a release

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py package --source output --name demo --plan'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py package --source output --name demo --approve --approval-token <TOKEN> --approved-by "s.operator"'
```

Creates `~/.local/state/offline-neural-voice/packages/demo/` containing the audio, captions, evidence and `package-manifest.json` — plain
files only, no archive.

### 8.9 Diagnose

```bash
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py doctor --format text'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py offline-check --format text'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py explain failures --format text'
```

### 8.10 Cancel and recover

```bash
bash -lc 'cd offline-neural-voice && (sleep 1; touch ~/cancel.flag) & python3 -B tools-nv-voice.py synthesize --text "Cancel me mid-run please." --voice en-us-joe-medium --name cancel-demo --cancel-file ~/cancel.flag'
bash -lc 'cd offline-neural-voice && rm -f ~/cancel.flag && python3 -B tools-nv-voice.py journal --recover --format text'  # after rm ~/cancel.flag
```

The cancelled run exits 130 and leaves **no** partial WAV (atomic write). A stale intent from a killed
process is reported and cleared by `journal --recover`.

---

## 9. Safety, approval and rights rules

1. **Sealed paths.** Every path must be relative and stay inside the skill root; `..`, absolute paths and
   drive letters are rejected unless `execution.allow_absolute_paths=true` is set by an operator. Allowed
   roots can be extended with `paths.allowed_roots`.
2. **Offline guard.** During synthesis, `socket.socket`, `socket.create_connection` and
   `socket.getaddrinfo` are patched to raise. Any attempt is recorded and reported. The guard is lifted
   only inside `fetch-model`, which is itself approval gated.
3. **Approval gates.** These actions *require* `--approve --approval-token <plan token>` and are refused
   with exit 5 otherwise: model download (`fetch-model`), release packaging (`package`), and synthesis when
   the rights decision is not `allow`. The token is a SHA-256 prefix of the canonical plan, so an approval
   cannot be reused for a different plan.
4. **Rights gates.** `safety.require_approval_for_release` and `execution.fail_on_blocked_rights` block
   licences that do not permit the requested use. Voice cloning additionally requires
   `--consent-record ID --consent-by NAME`.
5. **No silent downgrade.** If a neural engine fails, the fallback engine is used only when
   `execution.allow_fallback_engine=true`, and the result is marked `degraded:true` with a disclosure
   string that is also written into the evidence bundle and into captions
   (`DEGRADED ENGINE OUTPUT`).
6. **No silent text loss.** Unsupported scripts are blocked by default (`execution.script_guard=block`)
   because espeak-ng would otherwise drop the characters and still return "success".
7. **No silent integrity pass.** Digests are verified before synthesis; a mismatch deletes nothing and
   aborts with details. When only a size is known, that is stated as a warning, never as verification.
8. **Destructive actions.** `package` refuses an existing destination; `export_wav` refuses to overwrite
   without `--overwrite`; the journal writes an intent file before multi-file operations so a crash is
   detectable and recoverable.
9. **Privacy.** No telemetry, no analytics, no remote logging. Everything stays in the skill folder.

---

## 10. Validation actually performed

Environment: CPython 3.13.16, Linux x86-64, 2 vCPU, 1.9 GiB RAM, `onnxruntime` 1.31.1, `onnx` 1.23.2,
`numpy` 2.3.5, `piper-tts` 1.8.0, `lameenc`, `soundfile` 0.13.1 (libsndfile 1.2.2), `jsonschema` 4.26.0,
`faster-whisper` 1.2.1, `pyloudnorm` used only as an external cross-check.

| Test | Result |
|---|---|
| `tools-nvcore.py selftest` | **10/10 pass** — WAV round-trip, BS.1770 calibration (1 kHz −20 dBFS sine reads −23.05 LUFS against the −23.01 reference), true peak, resampling 22 050→16 000, schema validator accept/reject, offline guard blocks a connection, journal intent+recovery, text utilities, sealed-path rejection, result envelope vs shipped schema |
| `tools-nv-engine.py selftest --model ~/.local/state/offline-neural-voice/models/en_US-joe-medium.onnx` | **6/6 pass** — phoneme framing, unknown-phoneme reporting, espeak bridge produces sentence groups, fallback engine, ONNX inference, **frame-for-frame parity with piper-tts** (56 576 samples at `noise_scale=0`) |
| `tools-nv-text.py selftest` | **9/9 pass** — integers/decimals/negatives, ordinals, dates, money+percent, markup, IPA+chunking, directions, limit rejection, lexicon |
| `tools-nv-master.py selftest` | **4/4 pass** — chain reaches −14.01 LUFS at −9.17 dBTP on a test tone, WAV export, MP3 encode+decode, overwrite protection |
| `tools-nv-qa.py selftest` | **5/5 pass** — word diff, verdict aggregation, check primitives, real-file assessment, checklist |
| `tools-nv-ops.py selftest` | **4/4 pass** — manifest, package plan, fetch plan, download cap |
| `tools-nv-config.py selftest` | **4/4 pass** — defaults, schema accept/reject, traversal rejection, migration merge |
| `tools-nvcore.py selftest-all` | **7/7 tool self tests pass** |
| Command sweep (29 scenarios, JSON and text formats, repeated after every code change) | **29/29 pass** |
| Flat-folder guarantee | after a full sweep, `ls` on the skill folder shows only the 19 prefixed files and no subdirectories; all runtime state is created under `paths.state_dir` |
| Schema conformance of command results | 22 commands validated against `asset-result-schema.json`: **0 failures** |
| Real model intake | `en-us-joe-medium` (63 201 294 B) and `en-us-libritts-r-medium` (78 580 914 B) downloaded through the approval flow, digest-verified, then also exercised through `--source-dir` local import |
| Real synthesis | WAV + MP3 + SRT/VTT/TXT + evidence produced for single-sentence, multi-sentence, multi-speaker and batch runs |
| ASR transcript | faster-whisper `base.en` transcribed the synthesized greeting exactly; word accuracy **1.0** |
| Acceptance scenario | **pass**, 15 checks, run 5 times consecutively with no failure: 2.866 / 2.898 / 2.949 / 2.954 / 3.042 s, −14.21 to −14.25 LUFS, −1.000 dBTP, PCM WAV and MP3 both decoded |
| Loudness convergence | all 5 presets render within 0.05 LU of their own target (−14.41 / −16.03 / −14.33 / −16.04 / −14.34), true peak pinned at −1.0002 dBTP; verified again on a 16.26 s paragraph (−14.15), a 0.46 s word (−14.01) and a 4.34 s shouted line (−14.38) |
| Duration calibration | solver brackets and interpolates: `length_scale` 1.06 → 2.66 s, 1.30 → 3.09 s, 1.36 → 3.18 s, converging inside the 2.5–4.0 s window; every probe recorded in the evidence bundle |
| Integrity failure path | Appending one byte to `en_US-joe-medium.onnx.json` aborts synthesis with `integrity verification failed`, the expected/actual SHA-256 and a remediation hint; restored afterwards |
| Script-guard path | `你好，世界` is rejected with the Han characters, counts and a remediation hint instead of producing silence; `script_guard=warn` passes it with a warning |
| Rights blocking path | `en-us-amy-low` blocked for commercial/redistribution; `--use` and licence flags verified through `license-audit` |
| Cancellation | cancel file mid-run → exit 130, no partial WAV; identical command without the file succeeded afterwards |
| Crash recovery | synthetic stale intent reported and cleared by `journal --recover` |
| Approval failure | wrong/absent token → exit 5, `approval_required:true`, no writes |
| Invalid input | unknown voice → exit 4; traversal path → exit 4; missing file → exit 4; malformed config → exit 4 with the schema problem; unknown command → exit 2 |
| Stub-free fallback | `--engine buzz-fallback` produced degraded audio with warnings and a disclosure string, and the caption label `DEGRADED ENGINE OUTPUT` |
| Clean-room reference test | The folder was copied beside a single test `SKILL.md` in a fresh temporary root and the primary entry point was run there with relative paths only; see §13 |

---

## 11. Limits and known constraints

* **Not fully deterministic by default.** VITS sampling injects noise; with the default
  `noise_scale=0.667` two runs of the same text differ slightly in length (measured 3.264 s vs 3.296 s).
  `noise_scale=0` and `noise_w=0` produce byte-identical audio, which is how the parity test is defined.
  Use a preset with zero noise when bit-exact reproduction is required.
* **English only by default.** The shipped voices are en-US/en-GB. Other languages work only if you add a
  Piper voice for that language to the registry *and* the phonemizer supports it; the script guard will
  otherwise block the text. Non-espeak phoneme types (pinyin, Hebrew, Japanese, Thai, text) are **not**
  implemented here.
* **Mastering is two-stage.** Normalisation sets the gain, then a look-ahead limiter clamps the ceiling.
  Limiting removes energy, so a bounded gain-into-limiter correction (≤ 6 dB, ≤ 3 passes) follows it; on the
  shipped material this lands every preset within 0.05 LU of target with true peak held at −1.0 dBTP.
  Extremely dense material can still exhaust the 6 dB budget and settle slightly under target — the
  `loudness_ok` flag in the master report says so rather than hiding it.
* **Loudness meter is an implementation of BS.1770-4** (K-weighting, 400 ms blocks, absolute and relative
  gating) validated against a calibrated tone; it is not a certified meter and does not replace a
  broadcast lab measurement.
* **True peak uses 4× linear interpolation**, a conservative estimate, not the ITU oversampled filter.
* **Pure-Python DSP is slow** relative to C: a 3-second clip masters in ~0.4–0.6 s; a 10-minute narration
  takes proportionally longer. Resampling and loudness are the hot paths.
* **`numpy`/`onnxruntime` are mandatory** for neural synthesis. Without them only the degraded fallback
  engine is available (and only when allowed).
* **ASR is optional and never downloads silently.** With offline policy on and no staged model,
  `qa --transcript` reports `available:false`.
* **Model licensing is not legal advice.** The policy asset encodes the upstream dataset terms recorded in
  the Hugging Face model cards; verify before commercial release.
* **Upstream drift detected and handled:** the `en_US-libritts_r-medium` pair currently served by the
  origin is 78 580 914 bytes (MD5 `bb2c2776…`) with a 20 123-byte config, while the upstream `voices.json`
  index still records 81 472 438 bytes / `76debe50…`. The registry records the drift under
  `files.upstream_digest_mismatch` (indexed vs served digests, date) and **pins the digest and size of the
  bytes that can actually be downloaded** — for the model *and* the config — so a fresh `fetch-model`
  succeeds and the local integrity check still means something. The entry also carries
  `license_id_verified: false` and synthesis emits a drift warning naming both sizes. `en_US-joe-medium`, `en_US-bryce-medium` and `en_GB-northern_english_male-medium` match
  the upstream index exactly.
* **`fetch-model` needs HTTPS access to the pinned origin.** In a fully sealed network it fails cleanly;
  use `--source-dir` instead. If the origin becomes unreachable, that is a hosting/catalog issue, not a
  packaging defect — the tool can only verify what it is given.
* **Shipped models:** the folder ships `en-us-joe-medium` and `en-us-libritts-r-medium` so the tool works
  out of the box. They are redistributable (CC0 / CC BY 4.0) but **not** covered by this folder's own
  licence; keep the model cards with any redistribution.
* **Two voices installed ⇒ no implicit selection.** Pass `--voice`, or rely on the registry default via
  commands that resolve it (`preset-build`, `benchmark`, `batch` with plan defaults).

---

## 12. Troubleshooting and recovery

| Symptom | Cause | Fix |
|---|---|---|
| `missing-dependency` (exit 3) | numpy/onnxruntime absent | `pip install numpy onnxruntime`, then `doctor` |
| `invalid-input: model file is not installed` | voice not fetched | `fetch-model --voice <id> --plan`, then approve or use `--source-dir` |
| `invalid-input: integrity verification failed` | file changed or pin is stale | reinstall via `fetch-model`; do not synthesize from the file |
| `policy-blocked` (exit 1) | licence does not permit the requested `--use` | pick another voice or record an approval after review |
| `approval-required` (exit 5) | destructive/publishing action | run with `--plan`, review, then `--approve --approval-token <token>` |
| `cancelled` (exit 130) | cancel file, deadline or Ctrl-C | remove the cancel file; no partial artifact exists |
| `unknown-phoneme` / missing phonemes | text needs sounds the voice lacks | add a lexicon entry, rewrite the word, or change voice; `prepare-text` shows coverage |
| Unsupported-script error | non-Latin text with an English voice | `execution.script_guard=warn` to override, or use a matching voice |
| Silence in the output, `buzz-detection` warn | degraded engine or broken model | `list-voices --details`, `inspect-model`, restore a good model |
| `journal: recovered interrupted operation` | a previous run was killed | re-verify the artifact with `qa`; then re-run |
| MP3 not produced | `lameenc`/`soundfile` missing | `pip install lameenc soundfile` |
| Text spoken oddly | normalisation over- or under-expanded | `prepare-text` to see rewrites; adjust `execution.max_chars`, lexicon or `--locale` |
| Loudness outside window | preset target vs. `mastering.target_lufs` | pass `--target-lufs`, or edit the preset |

Recovery procedure after any crash: `python3 -B tools-nv-voice.py journal --recover`, then
`qa --audio <artifact>`, then re-run the failed command with `--overwrite`.

---

## 13. Portability, copying and the future parent skill

1. Copy the **whole** `offline-neural-voice/` folder beside your package's `SKILL.md`
   (`<package-root>/SKILL.md` + `<package-root>/offline-neural-voice/…`). No file is edited on copy.
2. Keep this folder free of `SKILL.md`; the parent package owns the single root `SKILL.md`.
3. Path depth from the future skill root is at most 3 levels
   (`offline-neural-voice/models/<file>`, `offline-neural-voice/output/evidence/<file>`).
4. Every internal reference is relative and case-sensitive (`asset-*.json`, `tools-*.py`, `models/…`);
   no absolute path, drive letter, tenant path or generated URL appears in any shipped file.
5. Verified clean-room: the folder was copied into a fresh temporary directory that also contained a single
   test `SKILL.md`, and `python3 -B tools-nv-voice.py` help/doctor/qa/acceptance all resolved their assets
   with relative paths only.
6. If you rename an asset, update `paths.*` in `asset-default-config.json` (and any overrides); every
   reference flows through that configuration, plus the `list-tools` inventory.
7. To add a voice: append an entry to `asset-model-registry.json` with digests, add the licence to
   `asset-rights-policy.json`, then `fetch-model --voice <new-id> --plan`.

### Extension points

* **New engine** → subclass the `synthesize(SynthesisRequest) -> EngineResult` contract in
  `tools-nv-engine.py` and register it in `build_engine()` and `engine_availability()`.
* **New preset** → append to `asset-voice-presets.json` (validated by the schema, clamped at use).
* **New QA check** → return `_check(...)` from `tools-nv-qa.py`; it automatically participates in the
  verdict and the acceptance report.
* **New normalisation rule** → add a method to `TextPreparer` and call it from `run()`; every change is
  recorded in `rewrites`, so audits stay complete.
* **Different language** → add a registry voice and extend `SCRIPT_SUPPORT` in `tools-nv-text.py`.

### Maintenance

* Re-run `python3 -B tools-nvcore.py selftest-all` after any edit.
* Bump `skill.config_version` and `registry_version`/`presets_version` when pins change; evidence bundles
  record them so old outputs remain attributable.
* Journal rotation keeps `paths.journal_path` bounded (`journal.max_bytes`, default 2 MB); rotated files are
  named `journal.<epoch>.jsonl`.

---

## 14. Dependencies and versions used

| Component | Version used | Required |
|---|---|---|
| Python | 3.13.16 (3.9+ supported) | yes |
| numpy | 2.3.5 | yes (neural) |
| onnxruntime | 1.31.1 | yes (neural) |
| piper-tts | 1.8.0 | no (phonemization bridge, parity checks) |
| onnx | 1.23.2 | no (deep inspection) |
| lameenc | installed | no (MP3 export) |
| soundfile | 0.13.1 (libsndfile 1.2.2) | no (MP3 decode) |
| jsonschema | 4.26.0 | no (independent schema cross-check) |
| faster-whisper | 1.2.1 | no (ASR transcript check) |

Install everything with:

```bash
bash -lc 'pip install numpy onnxruntime piper-tts onnx lameenc soundfile jsonschema faster-whisper'
```

---

## 15. Quick reference card

```bash
# health, then the canonical acceptance run
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py doctor --format text'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py acceptance --overwrite'

# everyday use
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py synthesize --text "Hello, world." --voice en-us-joe-medium --preset warm-friendly --mp3 --captions'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py qa --audio output/hello-world.wav --expect-text "Hello, world."'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py license-audit --format text'

# everything discoverable
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py list-tools --format text'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py list-categories --format text'
bash -lc 'cd offline-neural-voice && python3 -B tools-nv-voice.py explain failures --format text'
```
