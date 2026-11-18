# knowledge-readme.md — core-synthesis-modules Skill Tool

Unified Skill Tool for catalog category **"Core Synthesis Scripts and Modules"**
(category 12) from the *Offline Neural Voice System — Required and Missing Items
Skill Catalog* (November 2026). This folder is one reusable Skill Tool
component: it contains the complete, working implementation of the category's
15 proposed skills, unified behind one CLI, plus the shared audio pipeline,
provider routing, contract tests, and acceptance gate it needs.

The folder is designed to be copied directly beside a parent package's root
`SKILL.md` without modification. It contains **no** `SKILL.md`, no internal
subfolders, and every file is prefixed `tools-`, `knowledge-`, or `asset-`.

---

## 1. Purpose and Capability List

The capability is the heart of the offline neural voice system: turn text into
complete, intelligible, mastered WAV audio on a CPU-first, air-gapped machine,
through a provider router that prefers a local ONNX neural engine, falls back
to local Piper, and finally to a deterministic offline stub for pipeline
validation — with rigorous acceptance checks for the required greeting sample.

All 15 catalog capabilities of the category are implemented, each with a clear
responsibility. `python3 tools-voice.py capabilities` lists them at runtime.

| # | Catalog capability | CLI command | Role |
|---|---|---|---|
| 1 | synthesize.py Builder | `synthesize` | End-to-end: text → prosody plan → provider → mastered WAV |
| 2 | model_loader.py Builder | `model-loader` | Model resolution, checksum integrity, capability report |
| 3 | tokenizer.py Builder | `tokenize` | Vocabulary encode/decode with word spans |
| 4 | phonemizer.py Builder | `phonemize` | G2P + pronunciation dictionaries |
| 5 | voice_presets.py Builder | `presets` | Voice preset management + validation |
| 6 | sentence_chunker.py Builder | `chunk` | Long-text chunking |
| 7 | pronunciation.py Builder | `pronunciation` | Acronym expansion + override application |
| 8 | provider_local_onnx.py Builder | `providers` / `synthesize --provider onnx` | ONNX Runtime engine |
| 9 | provider_piper.py Builder | `providers` / `synthesize --provider piper` | Local Piper engine (subprocess) |
| 10 | provider_router.py Builder | `providers` | Priority, health checks, deadline, fallback, recovery |
| 11 | Audio Pipeline Module Builder | `audio` | WAV I/O, loudness/peak mastering, signal validation |
| 12 | Configuration Loader Builder | `config` | Load, merge, validate configuration |
| 13 | Structured Error Module Builder | `errors` | Error registry + exit-code standard |
| 14 | Core Module Contract Tester | `contract-test` | Per-module contract tests |
| 15 | Core Synthesis Acceptance Gate | `acceptance` | Full offline acceptance run for the required phrase |

**Umbrella name:** `core-synthesis-modules` (category 12 name shortened to a
lowercase hyphenated folder name).

---

## 2. Setup, Dependencies, and Exact Commands

### Runtime

- **Python 3.10+** (developed and tested on Python 3.13). The tool itself is
  **standard-library only** (`argparse`, `wave`, `struct`, `array`, `math`,
  `json`, `hashlib`, `os`, `re`, `shutil`, `subprocess`, `tempfile`, `time`,
  `importlib`). No network access is ever used.
- **Optional:** `onnxruntime` + `numpy` — enables the ONNX neural engine
  (provider 1). Without them, the router falls back automatically.
  Install: `pip install onnxruntime` (tested with onnxruntime 1.31.1,
  numpy 2.3.5).
- **Optional:** `piper` binary on `PATH` — enables the Piper fallback engine
  (provider 2). Without it, the router reports it unavailable and continues.
- **Optional:** `espeak-ng` / `phonemizer` — production G2P integrations. The
  tool ships a deterministic rule-based G2P fallback and never depends on them.

### Exact commands

Run from anywhere (all paths are resolved relative to the skill root):

```bash
# capability list and help
python3 tools-voice.py capabilities
python3 tools-voice.py help

# required acceptance sample (full offline gate)
python3 tools-voice.py acceptance

# contract tests (all modules)
python3 tools-voice.py contract-test --verbose

# synthesize the required phrase
python3 tools-voice.py synthesize --text "Greetings, friends! Welcome to my video."

# forced provider / preset / output path
python3 tools-voice.py synthesize --text "Hello world" --provider stub --preset friendly-male-30s --out output/hello.wav

# configuration and providers
python3 tools-voice.py config --check
python3 tools-voice.py providers --check

# module tools
python3 tools-voice.py tokenize --text "Hi there" --spans
python3 tools-voice.py phonemize --text "NASA and TTS work offline"
python3 tools-voice.py pronunciation --text "NASA and TTS work offline"
python3 tools-voice.py presets --list
python3 tools-voice.py presets --name friendly-male-30s
python3 tools-voice.py chunk --text "A long paragraph..." --max-chars 200
python3 tools-voice.py model-loader
python3 tools-voice.py model-loader --provider onnx --checksum
python3 tools-voice.py errors --list
python3 tools-voice.py errors --code PROVIDER_FAILURE

# audio pipeline
python3 tools-voice.py audio --in greeting.wav --check
python3 tools-voice.py audio --in greeting.wav --out greeting-mastered.wav --master --overwrite
```

`--json` (before or after the subcommand) switches output to machine-readable
JSON. `--config <file>` merges a user config over the defaults.

### File layout (all files directly inside the Skill Tool folder)

```
core-synthesis-modules/
├── tools-voice.py              # PRIMARY ENTRY POINT — CLI dispatch, exit codes
├── tools-core.py               # shared core: config, errors, tokenizer, phonemizer,
│                               #   presets, chunker, prosody planner, model loader
├── tools-providers.py          # stub / onnx / piper providers + router
├── tools-audio.py              # WAV I/O, BS.1770-4 loudness, soft clip, mastering,
│                               #   signal validation
├── tools-contract.py           # contract tests + acceptance gate
├── knowledge-readme.md         # this file (the only knowledge file)
├── asset-default-config.json   # default configuration (schema + values)
├── asset-voice-presets.json    # friendly-male-30s + 2 more presets
├── asset-pronunciation-dictionary.json  # acronyms + overrides
├── asset-vocabulary.json       # local tokenizer vocabulary (printable ASCII)
├── asset-acceptance-phrases.json       # required phrase + gate thresholds
└── asset-test-model.onnx       # deterministic ONNX fixture model (see §7)
```

**Loading order:** `tools-voice.py` loads `tools-core.py`, `tools-audio.py`,
`tools-providers.py`, and `tools-contract.py` via `importlib` (the hyphenated
filenames cannot be imported with plain `import` syntax). Sibling modules are
loaded lazily and cached in `sys.modules`. `tools-core.py` and
`tools-audio.py` have no internal dependencies; `tools-providers.py` depends
on `tools-core.py` + `tools-audio.py`; `tools-contract.py` depends on all
three; `tools-voice.py` depends on all four.

---

## 3. Inputs, Outputs, Parameters, Schemas, Configuration

### CLI input surface

| Option | Applies to | Meaning |
|---|---|---|
| `--text` | synthesize, tokenize, phonemize, chunk, pronunciation | input text (required, ≤ `limits.max_text_chars`, default 8000) |
| `--provider {auto,onnx,piper,stub}` | synthesize, acceptance | provider preference; `auto` = router order |
| `--preset NAME` | synthesize | voice preset (default `preset_default`) |
| `--out PATH` | synthesize, audio | output WAV path |
| `--overwrite` | synthesize, audio | approval flag required to replace an existing file |
| `--no-master` | synthesize | skip loudness/peak mastering |
| `--in PATH` / `--check` / `--master` | audio | input WAV, validate, or master+write |
| `--config PATH` | (global) | user config JSON merged over defaults |
| `--json` | (global or per-command) | machine-readable output |
| `--max-chars` / `--max-tokens` / `--overlap` | chunk | chunking limits |
| `--name` / `--list` | presets | show/validate one preset / list all |
| `--checksum` / `--provider` | model-loader | verify checksum of one provider's model |
| `--out-dir` | acceptance | acceptance output directory |
| `--verbose` | contract-test | per-test output |

### Outputs

- **WAV audio**: 16-bit PCM mono (default), peak ≤ -1 dBFS, integrated
  loudness ≈ -14 LUFS, duration complete (no cut-off ending), written with a
  `.meta.json` sidecar containing the prosody plan, provider metadata,
  mastering report, validation report, and output sha256.
- **Structured JSON reports** for every command (`--json`), including
  `result`, `error.code`, `error.message`, `error.details`, and `exit_code`.
- **Exit-code standard** (structured errors module):
  `0` success · `1` internal · `2` usage · `3` input/config validation ·
  `4` provider failure · `5` audio validation failure · `6` acceptance
  failure · `7` unsupported / approval required.

### Configuration schema (asset-default-config.json)

```jsonc
{
  "schema_version": "1.0.0",
  "skill_tool": { "name": "core-synthesis-modules", "offline_first": true, ... },
  "limits": {
    "max_text_chars": 8000,          // input validation
    "max_chunk_chars": 600,          // chunk default
    "synthesis_timeout_seconds": 120,// router deadline
    "provider_retries": 1,
    "onnx_intra_op_threads": 1       // ONNX session threads
  },
  "audio": {
    "sample_rate": 22050,
    "sample_width_bytes": 2,         // 2=16-bit, 3=24-bit, 4=32-bit PCM
    "channels": 1,
    "target_peak_dbfs": -1.0,        // peak safety ceiling
    "target_lufs": -14.0,            // online-video loudness target
    "loudness_normalization": true,
    "silence_threshold_db": -45.0,
    "overwrite_requires_approval": true
  },
  "preset_default": "friendly-male-30s",
  "providers": {
    "order": ["onnx", "piper", "stub"],
    "onnx": {
      "enabled": true,
      "model_path": "asset-test-model.onnx",   // relative to skill root
      "model_name": "test-model-fixture",
      "expected_sha256": "6b4ebb15...98400",   // integrity gate
      "input_tokens": 256,
      "frames_per_token": 1102
    },
    "piper": {
      "enabled": true,
      "executable": "piper",
      "allowed_executables": ["piper"],  // subprocess allowlist
      "model_path": "models/piper/en_US-amy-medium.onnx",
      "timeout_seconds": 60
    },
    "stub": { "enabled": true, "quality_warning": true }
  },
  "paths": { "output_dir": "output" }
}
```

User configs are deep-merged over this file; validation rejects unknown
provider references, non-positive sample rates, peak targets above 0 dBFS, etc.

### Voice preset schema (asset-voice-presets.json)

Presets carry the friendly-male-30s voice target: English `en-US`,
male-presenting, 30s age style, warm tone, welcoming emotion, conversational
delivery, natural energy, medium pace, slight smile (0.25), chest resonance
(0.4), plus **rules** the prosody planner enforces:

- `greeting-pause-after-friends`: pause 250 ms after the word *friends*
- `welcome-emphasis`: gain 1.35 on the word *welcome*

Prosody parameters (`char_ms`, `word_gap_ms`, comma/sentence pauses,
`emphasis_gain`) drive the prosody plan. Each preset carries its own
validation ranges (`min_rate`, `max_pitch`, `smile_range`, ...) enforced by
`presets --name NAME` and the preset validation in the contract tests.

### Required acceptance phrase (asset-acceptance-phrases.json)

```
Greetings, friends! Welcome to my video.
```

Gate thresholds: duration 2500–4000 ms; friends-pause ≥ 200 ms;
welcome emphasis gain ≥ 1.2; peak ≤ -1 dBFS; loudness within ±1.5 LU of
-14 LUFS.

---

## 4. Explanation and Examples for Every Capability

### synthesize
Builds the prosody plan from text + preset, routes to a provider, masters the
audio (DC removal → ITU-R BS.1770-4 loudness normalization → soft clip →
hard peak limit), validates the signal, and writes WAV + `.meta.json`.
Example: `python3 tools-voice.py synthesize --text "Hello world" --out output/hello.wav`.

### model-loader
Resolves model paths relative to the skill root, verifies sha256 against the
config, and reports per-provider capability (found/integrity). Example:
`python3 tools-voice.py model-loader --provider onnx --checksum`.

### tokenize
Char-level vocabulary encode/decode; `--spans` adds word alignment
(`word_index`, `char_position`). Example: `python3 tools-voice.py tokenize --text "Hi there" --spans`.

### phonemize
Applies the pronunciation dictionary (acronym expansion, overrides) then a
deterministic rule-based G2P fallback (digraphs, silent-e, c/g/s softening).
Example: `python3 tools-voice.py phonemize --text "NASA and TTS work offline"` →
`N A S A and T T S work off line` → ARPAbet-style phoneme sequence.

### presets
Lists, shows, and validates presets against their own ranges. Example:
`python3 tools-voice.py presets --name friendly-male-30s`.

### chunk
Sentence-aware chunking (breaks at `.!?`, then `,`, then words), with
optional overlap. Example: `python3 tools-voice.py chunk --text "..." --max-chars 200`.

### pronunciation
Shows the expanded text and every applied rule (acronym vs override).
Example: `python3 tools-voice.py pronunciation --text "The GPU is from NASA"`.

### providers
Health-checks every configured provider and shows the routing order.
`--check` exits 4 if no provider is available. The router tries providers in
order, records each failure in the fallback chain, and returns the first
success with any quality warnings attached.

### audio
Validates (`--check`) or masters (`--master`) any WAV: reads the header,
measures peak/loudness, and on mastering writes a normalized, peak-safe
WAV. Example: `python3 tools-voice.py audio --in greeting.wav --out out.wav --master`.

### config
Loads/merges/validates configuration. `--check` validates and exits 3 on
errors; `--show` prints the merged config.

### errors
Prints the structured error registry (codes, exit codes, suggestions).
`--code NAME` shows one entry. All runtime errors are reported through this
registry; unknown codes are rejected.

### contract-test
Runs 17 contract tests (see §6) and exits 1 if any fails.

### acceptance
Runs the full offline acceptance gate (see §5).

---

## 5. Safety and Approval Rules

- **Offline-first:** the tool never opens sockets, never calls APIs, never
  downloads anything. The `offline-guard` contract test statically scans all
  `tools-*.py` files for network imports (`socket`, `urllib`, `http`,
  `requests`, `ssl`, ...) and fails if any appear.
- **Subprocess bounds (Piper):** allowlisted executable names only (no
  absolute paths, no shell), no `shell=True`, hard timeout, temp directory
  cleaned up in a `finally` block.
- **Overwrite approval:** writing to an existing output file requires
  `--overwrite`; otherwise the command exits 7 (`APPROVAL_REQUIRED`).
- **Deterministic:** same input + config → byte-identical WAV output
  (verified in the acceptance gate and the router contract test).
- **Quality honesty:** the stub provider is a deterministic offline
  *test* synthesizer. It is never presented as a neural voice — every result
  it produces carries an explicit `quality_warning`, and the acceptance gate
  refuses to claim listening quality for it (or for anything else).
- **Human listening checks are never automated or claimed as passed** — the
  acceptance gate reports them as `REQUIRED-HUMAN-REVIEW`.
- **Timeouts/deadlines:** the router wraps synthesis with a deadline
  (`limits.synthesis_timeout_seconds`, default 120 s); providers check the
  deadline between words; Piper gets a subprocess timeout.
- **Recovery:** provider failures are recorded in the fallback chain
  (`meta.fallback_chain`) and the router proceeds to the next provider;
  all-provider failure raises structured `PROVIDER_FAILURE` with per-provider
  details. Piper temp files are always removed.
- **No secrets:** no credentials, tokens, or private endpoints exist in the
  package; model paths are configurable.

---

## 6. Validation and Tests Actually Completed

All of the following were executed with Python 3.13.16, onnxruntime 1.31.1,
numpy 2.3.5, in the delivery sandbox, and all passed:

**Contract suite — `python3 tools-voice.py contract-test --verbose` (17/17):**

| Test | Result | Detail |
|---|---|---|
| config-loader | PASS | preset_default=friendly-male-30s |
| tokenizer | PASS | vocab=96 chars, encode/decode/spans |
| phonemizer | PASS | dictionary + rule G2P |
| pronunciation | PASS | acronym expansion applied |
| voice-presets | PASS | 3 presets, friendly-male-30s valid |
| sentence-chunker | PASS | 2 chunks, boundaries preserved |
| prosody-planner | PASS | friends pause + welcome emphasis planned |
| model-loader | PASS | ONNX fixture sha256 verified |
| wav-writer | PASS | 22050-frame roundtrip + header valid |
| loudness-meter | PASS | 1 kHz @ -20 dBFS → -23.00 LUFS |
| peak-limiter | PASS | limited to -1.00 dBFS |
| stub-provider | PASS | deterministic samples |
| onnx-provider | PASS | real inference, 28 tokens, ~3 ms |
| piper-provider | PASS (skipped) | binary absent — health reporting verified |
| provider-router | PASS | fallback + byte-identical determinism |
| structured-errors | PASS | code→exit_code mapping |
| offline-guard | PASS | no network imports in any tool file |

**Acceptance gate — `python3 tools-voice.py acceptance` (all 10 automated
checks PASS, provider = onnx):**

- offline guard · plan rules (friends pause, welcome emphasis) · synthesis ·
  WAV reopen · duration **2909 ms** (2.5–4 s) · friends pause measured
  **279.6 ms** ≥ 200 ms · welcome gain **1.35** with RMS boost (-8.03 vs
  -10.77 dBFS) · peak **-5.59 dBFS** ≤ -1 · loudness **-14.0 LUFS** ·
  determinism (second run byte-identical sha256)
- Listening checks reported REQUIRED-HUMAN-REVIEW (warmth, friendliness,
  robotic buzz, naturalness).

**Acceptance gate via stub fallback — `--provider stub` (all PASS):** duration
3139 ms, loudness -14.77 LUFS (within ±1.5 LU), peak -1.0 dBFS, friends
pause 339.5 ms, determinism identical.

**Loudness meter cross-check** against the pyloudnorm reference (ITU-R
BS.1770): agreement within 0.09 LU on 1 kHz sine (-20 dBFS → -23.00 vs
-23.05), noise, speech-like, and 22 kHz signals.

**CLI paths verified:** capabilities list, config check/show with overrides
(invalid config rejected, exit 3), synthesize (onnx + stub + fallback chain),
overwrite approval (exit 7 without `--overwrite`, exit 0 with), empty text
(exit 3), audio check/master/invalid-file (exit 5), tokenize/spans,
phonemize, pronunciation, presets list/show, chunk, model-loader +
checksum, providers health (piper reported unavailable, exit 0; health
structure verified), errors registry, long-text chunked synthesis (6 chunks,
ONNX provider, ~8 s each), cross-process determinism (identical sha256 from
two separate processes).

---

## 7. Limits and Unavailable Integrations

- **The ONNX fixture model is a test model, not a production voice.**
  `asset-test-model.onnx` (built with the ONNX opset-17 graph described in
  the build notes below; sha256 pinned in `asset-default-config.json`) maps
  token ids to deterministic sine-formant frames. It validates the entire
  pipeline end-to-end offline. For production, point
  `providers.onnx.model_path` at a real model (Kokoro / Piper ONNX export /
  F5-TTS / StyleTTS2 / XTTS-style exports) and verify its license and
  checksum; the code path is unchanged.
- **Model input capacity:** the fixture accepts ≤ 256 tokens (≈ 40 words).
  Longer text must be chunked (`chunk`) and synthesized per chunk; the router
  also falls back to the stub with a clear reason when the ONNX model cannot
  accept the input.
- **Sample-peak vs true-peak:** the limiter and validator use sample peak
  (the catalog's true-peak metering with 4× oversampling is a separate
  category). The -1 dBFS ceiling is therefore conservative.
- **Loudness normalization is one-pass:** after normalization the chain
  applies a soft clipper and a final hard limiter; if the source crest factor
  is extreme, final loudness can land within ~1.6 LU of target (verified for
  the stub). Production post-processing can iterate.
- **Piper binary** is not shipped (license/size); the provider is fully
  implemented and bounded but reports `PROVIDER_UNAVAILABLE` when absent.
  `espeak-ng` / `phonemizer` G2P integrations are optional and not used by
  the built-in fallback G2P.
- **No GPU paths** are implemented or claimed; CPU-only by design (the
  optional PyTorch engine of the catalog is a separate category).
- **Human listening checks** (warmth, friendliness, robotic buzz,
  naturalness, age style, male-presenting perception) require a human; the
  gate reports them as pending, never as passed.
- **Air-gapped install of onnxruntime/numpy:** the tool does not download
  anything, but the optional ONNX runtime must be installed beforehand (e.g.,
  via a wheelhouse — see the installation category of the catalog). Without
  it, the stub path still works end-to-end.

---

## 8. Troubleshooting, Recovery, Extension, Maintenance

**Troubleshooting**
- `PROVIDER_UNAVAILABLE: onnxruntime not installed` → `pip install onnxruntime`
  or accept automatic fallback to the stub (with quality warning).
- `MODEL_NOT_FOUND` → check `providers.onnx.model_path` relative to the skill
  root; `python3 tools-voice.py model-loader` shows what was resolved.
- `MODEL_INTEGRITY` → sha256 mismatch; restore the model from a trusted
  source, then `model-loader --provider onnx --checksum`.
- `TIMEOUT` → raise `limits.synthesis_timeout_seconds` or chunk the text.
- `APPROVAL_REQUIRED` → pass `--overwrite` to replace existing output.
- `AUDIO_VALIDATION_FAILED` → inspect `audio_validation.checks` in the
  report; re-run with mastering enabled (default).

**Recovery:** provider fallback chains are recorded in
`<output>.meta.json` (`provider.fallback_chain`). Piper temp files are
always cleaned up. Failed runs leave no partial outputs (the WAV is written
only after validation passes).

**Extension**
- New voice presets: add an entry to `asset-voice-presets.json` following the
  schema (voice descriptor, prosody, rules, validation ranges).
- New pronunciation rules: extend `asset-pronunciation-dictionary.json`.
- New providers: subclass `ProviderBase` in `tools-providers.py`, register
  it in `PROVIDER_CLASSES`, and add its config block + order entry.
- New acceptance checks: extend `acceptance_gate()` in `tools-contract.py`
  and the thresholds in `asset-acceptance-phrases.json`.
- Parent-skill integration: copy this folder beside the root `SKILL.md`; all
  internal references are relative to the skill root and case-sensitive.

**Maintenance:** run `contract-test` after any change to shared modules; run
`acceptance` after any change to providers, presets, or mastering; keep the
model sha256 in the config in sync with the model file. The fixture model can
be rebuilt from the graph description (token ids → `80 + (id % 24)*12` Hz
sine partials with a per-token 1102-frame envelope at 22050 Hz, opset 17,
`Mod(fmod=1)`) if a rebuild is ever needed.
