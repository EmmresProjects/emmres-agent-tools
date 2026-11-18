# Offline Neural Voice — Skill Tool Handbook

CPU-first, air-gapped neural text-to-speech. Bundles a real neural engine
(Piper, ONNX), checksum-verified pinned wheels, an SSML-lite direction format,
a mastering chain targeting streaming delivery (**−14 LUFS**, **−1 dBFS** peak),
signal validation, consent/provenance controls, and a manifest-driven batch mode.
All processing is local: **no cloud endpoints, no API keys, no telemetry, and
no silent fallback to non-neural (formant) synthesis.**

**One-time model acquisition:** the 63 MB neural voice model is *not* inside
this bundle (delivery-size constraint of the distribution channel). Everything
else ships offline-verified. One command fetches the voice model with pinned
URL + SHA-256 (see **§4a Voice model acquisition**), or you can install it from
a local file with zero network access (`--from-file`). Until the model is
acquired, installation, validation, mastering, normalization, consent,
provenance, batch and self-test (except the end-to-end greeting check) all
work; only `synthesize`/`batch` need the model.

Built from the *Offline Neural Voice System — Required and Missing Items Skill
Catalog* (32 categories / 480 proposed skills). This folder is one unified
Skill Tool; the catalog maps onto it command-by-command in
`capabilities` output and in **§13 Catalog coverage** below. It is a component,
not a full Copilot skill: it intentionally contains no `SKILL.md`.

- Skill Tool folder: `offline-neural-voice-system/`
- Primary entry point: `tools-offline-voice.py`
- Version: 1.0.0 · Tool/schema: `ov-provenance-v1`

---

## 1. Quick start

```bash
cd offline-neural-voice-system

# 1) Verify the bundled wheels (no changes made; reports model status)
python3 tools-ov-setup.py --check-only

# 2) One-time acquisition of the 63 MB voice model. Pick ONE:
python3 tools-ov-fetch-model.py --yes                 # network: pinned URL + sha256 verify
python3 tools-ov-fetch-model.py --from-file /path/en_US-lessac-medium.onnx --yes   # air-gapped
python3 tools-ov-setup.py --with-model                # wheels + model in one step

# 3) Install the engine into a private venv (offline; ~1 minute; no root)
python3 tools-ov-setup.py          # exits 8 (not an error) until the model is present

# 4) Speak the acceptance phrase with the default friendly male 30s voice
python3 tools-offline-voice.py synthesize \
  --ssml-file asset-greeting-welcome.ssml \
  --output greeting.wav

# 5) Check it against the greeting acceptance bounds
python3 tools-offline-voice.py validate --input greeting.wav --for-greeting

# 6) Run the self-test suite (includes the end-to-end greeting check)
python3 tools-run-tests.py
```

Expected: duration ≈ 2.5–4.0 s, peak exactly −1.0 dBFS, loudness ≈ −14 LUFS,
0 clipped samples, `ok: true` from `validate`.

Requirements: Linux x86_64 with CPython 3.13 (validated; see §11 for other
platforms), ~40 MB RAM for synthesis, and ~350 MB free disk (venv + 63 MB voice
model). No `ffmpeg`, no system `espeak-ng`, no GPU required.

---

## 2. File inventory and roles (load/execution order)

Everything is flat in this folder and prefixed by role. Execution order for a
synthesis run:

| Order | File | Role |
|---|---|---|
| 1 | `tools-offline-voice.py` | **Primary entry point / CLI.** Parses args, loads config, orchestrates normalization → SSML → synthesis → mastering → validation → provenance. |
| 2 | `tools-ov-core.py` | Shared core: skill-root paths, config loader, error type + exit codes, WAV I/O (16-bit PCM), SHA-256 helpers, capability tables. |
| 3 | `tools-ov-text.py` | Text normalization, verbalization, segmentation, chunking, WER, phrase matching (pure stdlib). |
| 4 | `tools-ov-ssml.py` | SSML-lite parser/validator/plan builder with a security filter. |
| 5 | `tools-ov-audio.py` | Mastering + validation DSP (DC removal, de-click, compressor, trim, fades, lookahead brickwall limiter, BS.1770-style LUFS, estimators). |
| 6 | `tools-ov-providers.py` | Engine registry, health probing, in-process or venv-worker execution, timeout, retries, quality warnings. |
| 7 | `tools-ov-engine-worker.py` | Isolated synthesis worker executed inside the offline venv (or in-process). Reads a bounded job JSON, writes WAV via Piper. Never touches the network. |
| 8 | `tools-ov-safety.py` | Consent policy engine + local consent-record store. |
| 9 | `tools-ov-setup.py` | Offline installer: verifies SHA-256 of every bundled file, creates `.ov-venv`, installs pinned wheels with `--require-hashes`, smoke-tests imports. |
| 10 | `tools-run-tests.py` | Unittest suite (34 tests) + CLI contract tests + end-to-end greeting acceptance test. |
| 11 | `tools-ov-fetch-model.py` | One-time, approval-gated acquisition of the 63 MB voice model (network pinned URL + SHA-256, or air-gapped `--from-file`). |
| — | `asset-voice-lessac-medium.onnx.json` | Voice config for the model (phoneme map, inference defaults, en_US, medium, 22 050 Hz). |
| — | `asset-voice-lessac-medium.onnx` | **Not in this bundle** (63 MB; delivery-size constraint). Installed via `tools-ov-fetch-model.py`; SHA-256 verified on install. |
| — | `asset-voice-presets.json` | Voice/delivery presets (`friendly-male-30s` + 4 more). |
| — | `asset-pronunciation-dictionary.json` | Acronym expansions and custom pronunciations. |
| — | `asset-acceptance-phrases.json` | Required greeting acceptance text/SSML + regression corpus. |
| — | `asset-safety-policy.json` | Prohibited uses, sensitive contexts, minor protection, disclosure line. |
| — | `asset-greeting-welcome.ssml` | The catalog's acceptance SSML (pause after *friends*, emphasis on *Welcome*). |
| — | `asset-config-example.json` | Annotated defaults; copy to `ov-config.json` and pass `--config`. |
| — | `dependencies-*.whl` | Pinned, unmodified runtime wheels (piper-tts 1.8.0, onnxruntime 1.32.0, numpy 2.5.4, protobuf 7.36.2, flatbuffers 25.12.19, packaging 26.3, pathvalidate 3.3.1). |
| — | `dependencies-requirements-lock.txt` | Same pins with SHA-256 hashes (used by `tools-ov-setup.py`). |
| — | `dependencies-checksums.txt` | SHA-256 of every wheel + the model pair (`sha256sum -c` compatible). Also used by `tools-ov-fetch-model.py` to verify the model after download/copy. |
| — | `dependencies-notices.md` | Licenses and attribution; **piper-tts is GPL-3.0-or-later** — read this file before commercial redistribution. |
| — | `knowledge-readme.md` | This document (the only knowledge file). |

`__pycache__` and `.ov-venv/` are created locally at install/run time and are
not part of the distribution.

---

## 3. Setup (offline, deterministic)

```bash
python3 tools-ov-setup.py                # create .ov-venv and install (offline)
python3 tools-ov-setup.py --with-model   # wheels + one-time model acquisition in one step
python3 tools-ov-setup.py --check-only   # verify checksums + report, install nothing
python3 tools-ov-setup.py --force        # rebuild the venv from scratch
python3 tools-ov-setup.py --venv-dir /path/to/venv --report install-report.json
```

What setup does: (1) checks CPython ≥ 3.9 and the platform note
(bundled wheels target Linux x86_64 / CPython 3.13); (2) verifies
`dependencies-checksums.txt` for all 9 pinned files; a **mismatch** aborts the
install (tampering), a **missing** voice model is reported as acquisition-needed
(not tampering); (3) creates a private venv (default `<skill-root>/.ov-venv`);
(4) stages the `dependencies-*.whl` files under canonical wheel filenames in a
temp dir (pip requires this; the temp dir is deleted afterwards) and installs with
`--no-index --find-links=<staged> --require-hashes -r dependencies-requirements-lock.txt`;
(5) smoke-tests `import piper, onnxruntime, numpy` inside the venv. It is
idempotent (healthy venv → no-op), refuses to touch the system Python, uses no
network by default (`--with-model` opts into the pinned, hash-verified model
download), and aborts on any checksum mismatch. When the engine is installed
but the model is absent, setup exits `8` with the exact acquisition command.

## 4a. Voice model acquisition (one-time, optional in code, required for synthesis)

The bundle ships everything except the 63 MB neural voice model, which the
delivery channel cannot carry. It is acquired exactly once, then the install is
just as offline as before:

```bash
# Option A — network (one-time; pinned, hash-verified)
python3 tools-ov-fetch-model.py --yes

# Option B — air-gapped (USB stick etc.): copy en_US-lessac-medium.onnx to this machine, then
python3 tools-ov-fetch-model.py --from-file /media/usb/en_US-lessac-medium.onnx --yes

# Option C — everything in one shot
python3 tools-ov-setup.py --with-model
```

Pinned facts (see `tools-ov-fetch-model.py`): URL
`https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx`,
size **63 201 294 bytes**, SHA-256 **5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f**,
repository tagged `license:mit` (dataset `lessac`, single speaker, 22 050 Hz;
MIT redistribution attribution in `dependencies-notices.md`). The installer
verifies size + SHA-256 **before** the model is moved into place, refuses to
overwrite a valid model without `--force`, never stores credentials, and never
contacts anywhere else. Without `--yes` (and without a TTY confirmation) the
network is not touched. `tools-ov-fetch-model.py --check` reports model
status (`valid|missing|corrupt`) and exits 0/8.

Manual fallback (if you manage your own environment): `pip install --no-index
--find-links=. piper-tts==1.8.0` after verifying checksums yourself. The CLI
auto-detects: piper importable in the current interpreter → in-process engine;
else `.ov-venv/bin/python` → subprocess worker; else exit 3 with instructions.

---

## 4. Operating modes and exit codes

- Output is **JSON by default** (`--human` for readable text). Errors are JSON on
  stderr with the same shape: `{ok:false, error, code, details}`.
- Exit codes: `0` ok · `1` partial · `2` usage/validation · `3` neural engine
  unavailable · `4` safety gate blocked · `5` I/O · `6` batch had failures ·
  `7` dependency/checksum/config · `8` voice model acquisition required
  (run `tools-ov-fetch-model.py --yes`).
- Bounded work: manifest ≤ 200 items; SSML input ≤ 20 000 chars; plain text ≤
  5 000 chars (configurable); per-call timeout ≤ `synthesis_timeout_s`
  (default 120 s); `engine_retries` bounded retries (default 1); worker jobs are
  JSON with hard segment/text caps; child processes run without a shell.

---

## 5. Command reference

Run `python3 tools-offline-voice.py --help`, `--help` on any subcommand, or the
`capabilities` command. All commands accept `--config ov-config.json`, `--human`.

### synthesize — one utterance → mastered WAV
```bash
python3 tools-offline-voice.py synthesize --text "Bonjour tout le monde" --output out.wav
python3 tools-offline-voice.py synthesize --ssml-file asset-greeting-welcome.ssml --output greeting.wav
python3 tools-offline-voice.py synthesize --text-file notes.txt --preset warm-narrator --loudness-target -16 --output vo.wav
python3 tools-offline-voice.py synthesize --ssml '<speak>Hi.<break time="300ms"/><emphasis level="strong">There</emphasis></speak>' --output hi.wav
```
Inputs (exactly one): `--text`, `--text-file`, `--ssml`, `--ssml-file`.
Key options: `--preset` (see `asset-voice-presets.json`), `--engine auto|piper|piper-custom`,
`--model` / `--model-config` (Piper-compatible ONNX; custom models),
`--length-scale` (>1 slower), `--rate-percent` (`+15` = faster),
`--noise-scale`, `--noise-w`, `--speaker-id` (multi-speaker models),
`--loudness-target` (LUFS, default −14), `--peak-dbfs` (default −1),
`--sample-rate` (resample; linear interpolation, documented approximation),
`--no-master`, `--no-trim`, `--no-declick`, `--no-compress`,
`--no-provenance`, `--no-normalize`, `--real-person/--speaker-name/...`
(consent gate — see §8), `--venv`, `--synthesis-timeout`, `--engine-retries`.

Text normalization runs by default in plain-text mode (numbers, dates, currency,
URLs, emails, acronyms, pronunciation dictionary). SSML mode is explicit markup;
each segment is still normalized for numbers/acronyms.
Output: WAV 16-bit PCM mono (22 050 Hz by default; `--sample-rate` to change) +
`<output>.provenance.json` sidecar. Report includes duration, peak, LUFS,
clip count, DC offset, engine, attempts, warnings.

### batch — manifest-driven synthesis
Manifest: JSON array, `{"items": [...]}`, or JSONL. Each item:
`{ "id": "intro", "text": "..." }` or `text_file` / `ssml` / `ssml_file`,
plus optional `preset`, `length_scale`, `noise_scale`, `noise_w`, `speaker_id`,
`rate_percent`. ≤ 200 items.
```bash
python3 tools-offline-voice.py batch --manifest jobs.jsonl --output-dir out/ --resume
```
Writes `out/<id>.wav` (+ provenance) per item and `out/batch-report.json`;
`--resume` skips existing WAVs; `--force` re-synthesizes; exit `6` if any item failed.

### normalize — text normalization
```bash
python3 tools-offline-voice.py normalize --text "On 2026-11-15 we hit 99% at $1,249.99 — see https://example.com"
python3 tools-offline-voice.py normalize --text-file script.txt --raw
```
Handles: NFKC unicode, zero-width removal, control chars, smart punctuation,
whitespace collapse, currency (`$€£¥` incl. decimals), percent, ordinals,
decimals, integers with separators, ISO dates (`YYYY-MM-DD`), US dates
(`MM/DD/YYYY`), times (`HH:MM`, `3:45 PM`), units (kg, cm, mi, mph, GB…),
URLs, emails, symbols (`& @ # …`), common emoji (unmapped emoji are dropped
and reported), acronym expansion and pronunciation dictionary
(`asset-pronunciation-dictionary.json`). Toggle with `--no-symbols`,
`--no-acronyms`, `--no-dictionary`. Note: byte order of passes is
numbers→symbols last, URLs/emails first; homographs are not disambiguated
(documented in the asset).

### segment — segmentation & chunking
```bash
python3 tools-offline-voice.py segment --text-file essay.txt --max-chars 600
```
Sentence segmentation with abbreviation protection (`Dr.`, `a.m.`, `e.g.` …),
then length-bounded chunking at clause boundaries. Long inputs to
`synthesize` are chunked automatically (`max_chunk_chars`); outputs are
concatenated with `sentence_silence` spacing.

### ssml-check — validate SSML-lite
```bash
python3 tools-offline-voice.py ssml-check --text-file asset-greeting-welcome.ssml
```
Prints the parsed plan: segments, pauses, emphasis flags, warnings, estimated
seconds. Rejects DOCTYPE/ENTITY, `<script>`, event handlers, `javascript:`/`data:`
URIs, external http(s) URLs. **SSML-lite subset** (no full W3C SSML):

| Tag | Attributes | Behavior |
|---|---|---|
| `<speak>` | — | root (optional; bare text input also works) |
| `<break>` | `time="240ms"` / `strength="weak\|medium\|strong\|x-strong\|none"` (also x-weak) | pause inserted between segments (max 5 000 ms) |
| `<emphasis>` | `level="strong\|moderate\|reduced"` | strong → 0.94× rate + emphasis flag; reduced → 1.05× |
| `<prosody>` | `rate="x-slow\|slow\|medium\|fast\|x-fast\|+15%"`, `pitch="±Nst"` / keywords, `volume="±NdB"` / keywords | rate & volume are applied per segment; **pitch is recorded but not applied** (engine limitation, see §10) |
| `<say-as>` | `interpret-as="cardinal\|ordinal\|characters\|date\|currency\|digits"` | verbalizes the inner text |
| `<sub>` | `alias="..."` | speaks the alias instead (homograph fix) |
| `<phoneme>` | `alphabet`, `ph` | validated and recorded; not injected (engine has no raw-phoneme API); use the pronunciation dictionary instead |
| `<voice>` | `name` | ignored with warning; voice comes from `--preset`/`--model` |
| unknown tag | — | dropped; **inner text kept**; warning reported |

### validate — signal validation (no engine needed)
```bash
python3 tools-offline-voice.py validate --input out.wav
python3 tools-offline-voice.py validate --input greeting.wav --for-greeting \
    --transcript "Greetings, friends! Welcome to my video."
```
Checks: file integrity (frames vs header), non-empty, duration bounds
(`--duration-min/max`, or `--for-greeting` = 2.5–4.0 s), sample peak vs
`--peak-dbfs` (+0.05 dB tolerance), estimated true peak (4× linear
interpolation), BS.1770-style integrated LUFS vs `--loudness-target` (±2 LU
tolerance), clipped sample count, DC offset (warns > 0.02), leading/trailing
silence ratio, noise floor (10th-percentile windowed RMS), impulsive-click
count (abs jump ≥ 0.5 FS; >30 warns), **buzz heuristic** (spectral flatness
via numpy; labeled heuristic), mono/stereo. Optional `--transcript` adds
phrase-match (default phrase = acceptance phrase) and `--reference` adds WER.
Exit 0/2. `numpy` is used when importable (the venv provides it); spectral
checks are reported as skipped otherwise.

### master — post-process an existing WAV
```bash
python3 tools-offline-voice.py master --input raw.wav --output master.wav --loudness-target -14
python3 tools-offline-voice.py master --input in.wav --output out.wav --sample-rate 44100
```
Chain: mixdown mono → DC removal → de-click (isolated-spike detector) →
crest compressor (default −14 dBFS, 3:1, 5 ms/90 ms) → silence trim (−45 dBFS,
40 ms pad) → 6 ms fades → gain to LUFS target → **lookahead brickwall limiter**
→ optional resample. Report shows every step's numbers.

### wer / phrase-match — completeness scoring
```bash
python3 tools-offline-voice.py wer --reference "our build passed" --hypothesis "our build did pass"
python3 tools-offline-voice.py phrase-match --phrase "Greetings, friends! Welcome to my video." --transcript "..."
```
WER = (S+D+I)/N with Levenshtein alignment. phrase-match reports word
coverage and the exact missing words (case/punctuation-insensitive).
**No ASR model is bundled** (see §10) — supply a transcript (e.g. from your
own speech-to-text step) for completeness verification.

### provenance — rights & metadata records
```bash
python3 tools-offline-voice.py provenance write --wav out.wav --text-file in.txt
python3 tools-offline-voice.py provenance verify --input out.wav
python3 tools-offline-voice.py provenance show   --input out.wav
```
Every `synthesize`/`batch` run writes `<wav>.provenance.json` unless
`--no-provenance`: SHA-256 of output file and input text, engine, model file
+ SHA-256, preset, engine params, mastering report, creation timestamp,
disclosure line, license summary (`ov-provenance-v1`). `verify` re-checks the
output hash and required disclosure fields and exits non-zero on mismatch.

### consent — real-person/voice-clone gate
```bash
python3 tools-offline-voice.py consent check --real-person --speaker-name "Jane Doe" --context general
python3 tools-offline-voice.py consent grant --speaker-name "Jane Doe" \
    --grantor "Jane Doe (signed authorization, 2026-11-01)" --scope "product intro videos" --expires 2027-11-01
python3 tools-offline-voice.py consent list
python3 tools-offline-voice.py consent revoke --id consent-xxxx --confirm
```
`check` returns allow/deny/review with required actions. **Deny**: any
synthesis involving a minor; any request matching a prohibited category
(non-consensual cloning, impersonation fraud, voiceprint spoofing,
disinformation, harassment, sexual content — see `asset-safety-policy.json`);
real-person cloning/impersonation without an active, unexpired consent record.
**Review**: sensitive contexts (political ads, medical/legal/financial advice,
grief, court). `grant` stores a local JSON record (`./consent-records/`,
override with `--consent-dir` or `OV_CONSENT_DIR`); `revoke` is destructive and
requires `--confirm`. The same gate runs automatically inside `synthesize`
when `--real-person`, `--speaker-name`, `--involves-minor`, `--context` or
`--requested-use` are passed — a denial aborts synthesis with exit 4 before any
audio is produced. A consent record is an operator-maintained artifact; keep
the underlying written authorization. This tool is **not** a voice-cloning
tool (see §9).

### benchmark — timing & real-time factor
```bash
python3 tools-offline-voice.py benchmark --runs 3
```
Synthesizes the greeting (or `--text`) N times into a temp dir (cleaned
afterwards), reports per-run wall time, audio duration, RTF, LUFS, peak, and
the average RTF. Reference measurement on this machine: RTF ≈ 0.5 (synthesis
is ~2× faster than real time on 2 CPU cores).

### diagnose — environment diagnostic (offline)
```bash
python3 tools-offline-voice.py diagnose
```
Reports platform/python/CPU/RAM/disk, current-interpreter and venv engine
status, model presence + SHA-256 integrity, bundled checksum status, and the
setup command. Exits `7` if bundled checksums fail.

### self-test / version / capabilities
```bash
python3 tools-offline-voice.py self-test          # full suite incl. e2e greeting check
python3 tools-offline-voice.py self-test --quick  # unit tests only
python3 tools-offline-voice.py version
python3 tools-offline-voice.py capabilities       # JSON: 16 operations + 32 catalog categories mapped
```

---

## 6. Configuration

`--config file.json` or `OV_CONFIG` env var. Relative `model`, `model_config`,
`venv` resolve against the skill root. Keys (defaults in
`tools-ov-core.py: default_config`, annotated example in
`asset-config-example.json`):

`engine` (auto|piper|piper-custom), `preset`, `model`, `model_config`,
`loudness_target_lufs` (−14), `peak_dbfs` (−1), `sample_rate` (22050),
`sentence_silence` (0.3), `trim_silence_db` (−45), `trim_pad_ms` (40),
`fade_ms` (6), `max_text_chars` (5000), `max_chunk_chars` (600),
`synthesis_timeout_s` (120), `engine_retries` (1), `venv`, `provenance` (true),
`compressor` (`{enabled, threshold_db: −14, ratio: 3.0, attack_ms: 5,
release_ms: 90}`), `disclosure` (string embedded in provenance).

## 7. Voice presets (`asset-voice-presets.json`)

`friendly-male-30s` (default): warm, welcoming, conversational, medium pace,
natural energy, en-US, 30s age style, slight-smile delivery guidance,
`length_scale 1.08`, `noise_scale 0.667`, `noise_w 0.8`, mastering −14 LUFS /
−1 dBFS. Also: `warm-narrator` (narration, −16 LUFS), `bright-enthusiastic`
(promotional), `calm-instructional`, `empathetic-checkin`. Presets map to
engine params + SSML emphasis conventions + mastering targets; the model itself
is the bundled en_US-lessac-medium voice (Piper has no pitch/emotion latent
controls — see §10). List/inspect presets with any JSON viewer.

## 8. Safety and approval rules

1. **Consent gate is enforced before synthesis** whenever real-person/clone/minor
   signals are present (see `consent` above). Denials are hard stops (exit 4).
2. **Disclosure**: any publicly distributed audio must carry the disclosure
   line from `asset-safety-policy.json` ("This audio was produced by a synthetic
   neural voice…"). The automatic provenance sidecar embeds it.
3. **No minor voices**, ever; the gate denies these unconditionally.
4. **No impersonation** of real people without an active consent record —
   including "sounds like X", "in the voice of X" requests (matched against the
   requested-use text), even if `--real-person` is not passed.
5. **Prohibited uses** (fraud, voiceprint spoofing, disinformation, harassment,
   sexual content) are denied by keyword policy; see the asset for the
   authoritative list and trigger terms.
6. **Publishing, financial and legal actions** (e.g., releasing audio,
   contracting, ad placement) are outside this tool — a human approval step is
   required; the tool records provenance but never grants publication rights.
7. **Destructive actions** (`consent revoke`) require `--confirm`;
   `--force` on batch/setup overwrites only tool-created outputs/venv.
8. **No network**: no command in this tool fetches anything. `tools-ov-setup.py`
   installs exclusively from the bundled wheels. Do not point `--find-links` at
   the network.
9. This tool **does not clone voices** and does not record or verify identity;
   consent records represent the operator's assertion and must be genuine.

## 9. Rights, licenses, provenance

- Tool code: MIT. **piper-tts / espeak-ng: GPL-3.0-or-later** (separate
  unmodified component; aggregate distribution). See
  `dependencies-notices.md` for the full table and the
  "if you cannot ship GPL" remediation path.
- Voice model: `en_US-lessac-medium`, rhasspy/piper-voices (repo tagged
  `license:mit`), dataset `lessac`, single speaker, 22 050 Hz. **Acquired
  one-time via `tools-ov-fetch-model.py`** (not inside this ZIP); SHA-256
  verified at install. Not a clone of a specific real person. Verify
  redistribution terms for your jurisdiction.
- Provenance: sidecar `*.provenance.json` (schema `ov-provenance-v1`) — keep it
  next to every distributed WAV; `provenance verify` re-validates hashes.

## 10. Validation actually completed

Run by the builder on Linux x86_64, CPython 3.13.18, 2026-11-15:

| Check | Result |
|---|---|
| `tools-ov-setup.py` from clean state (fresh `.ov-venv`, no network) | **PASS** — checksums 9/9, install OK, import smoke OK |
| Clean temporary skill root (folder copied beside a test `SKILL.md`, all commands via relative paths) | **PASS** — setup, synthesis, validation, self-test OK |
| Voice-model acquisition: `--check` (valid/missing/corrupt reporting), `--from-file` air-gapped install with SHA-256 verification, tampered-file abort | **PASS** (bucket-copy tests, 2026-11-15) |
| Missing-model behavior: `setup --check-only` reports model note + `ready:false`; `synthesize` exits 3 with the exact fetch command; `setup` exits 8 until model present | **PASS** |
| Small-bundle end-to-end (ZIP without the 63 MB model, fetched via `--from-file`): setup → synthesis → validation → 34-test suite | **PASS** (3.09 s greeting, −1.0 dBFS, −14.46 LUFS) |
| Unit/integration suite `tools-run-tests.py` (normalization, segmentation/chunking, SSML parse + security rejects, mastering, validators, WER, phrase-match, consent allow/deny/grant/revoke, CLI contract incl. exit codes) | **PASS — 34 tests, 0 failures** (also with `--quick` and in the clean root) |
| End-to-end greeting acceptance (`--for-greeting`: 2.5–4.0 s, peak ≤ −1 dBFS, no clipping) | **PASS** — e.g. 3.22 s / 3.13–3.48 s across runs, peak −1.0 dBFS, 0 clipped, LUFS −14.1…−14.7 |
| SSML acceptance asset parses to 3 segments (240 ms pause, emphasized *Welcome*) | **PASS** |
| Synthesis via system python → venv worker subprocess **and** via venv python → in-process engine | **PASS** (both paths) |
| Batch synthesis (JSONL manifest) + `--resume` skip behavior + `batch-report.json` | **PASS** (3/3 items ok; resume skipped all) |
| Consent gate: deny (no record) → grant → allow → revoke → deny; unit test covers minor-denial and prohibited-use denial | **PASS** (CLI + tests) |
| `provenance verify` recomputes output hash | **PASS** |
| `benchmark` (RTF ≈ 0.5) | **PASS** |
| `diagnose` (reports OK; checksum failure path exercised by design when files are tampered — reported as exit 7) | **PASS** |
| Negative/invalid inputs: unknown command (exit 2), missing input file (exit 5), bad SSML/script injection (rejected, exit 2), over-size manifest (rejected), unknown preset (rejected) | **PASS** (covered in tests / manual runs) |
| Recovery: `--resume` in batch; worker failure → error report + exit 3; setup `--force` rebuild; checksum mismatch aborts install | **PASS** |

Human listening review (catalog §23/§24 "acceptance" sign-off) is **not**
performed by software and remains the release owner's responsibility — the tool
gives you the measured evidence (duration, LUFS, peak, clipping, buzz
heuristic) but does not claim the voice is pleasant.

## 11. Limits and unavailable integrations

- **Voice model is not inside the ZIP** — by delivery-size constraint, not by
  choice. This is the one deliberate exception to "fully offline from the
  moment of unzip". Acquire it with `tools-ov-fetch-model.py --yes` (one-time
  63 MB download, pinned URL + SHA-256) or air-gapped with `--from-file`; then
  the install is permanently offline. If the model cannot be obtained in your
  environment, every capability except actual speech synthesis still works
  (validation, mastering, normalization, consent, provenance, batch planning,
  self-test minus the greeting check), and `synthesize` fails with exit 3 plus
  the exact remedy — it never fakes audio.
- **Platform**: bundled wheels target Linux x86_64 / CPython 3.13
  (piper-tts is abi3 cp39+; the numpy/onnxruntime pins are cp313). Other
  platforms/Pythons: create venv manually, `pip download` matching wheels on
  the target, re-generate `dependencies-requirements-lock.txt` +
  `dependencies-checksums.txt` (sha256), and update this file. The
  re-download requires network once; the resulting bundle is offline again.
- **Engine scope**: primary engine is Piper ONNX (VITS-family). There is **no**
  Kokoro/F5-TTS/XTTS/StyleTTS2/Fish-Speech/MeloTTS provider bundled — those
  models are far larger (0.3–2 GB) and/or have non-commercial licenses; they
  are out of scope for the bundle. `--model` accepts any Piper-compatible ONNX
  voice; genuinely different architectures need a new provider module.
- **No pitch control**: Piper cannot shift pitch; `<prosody pitch>` and
  pitch-related preset guidance are recorded in provenance but not applied.
  Chest resonance / smile effect are delivery notes, not DSP.
- **Emphasis**: approximated via local rate reduction + optional pauses; there
  is no phoneme-level stress override.
- **`<phoneme>` tags** are validated/recorded, not spoken; use the pronunciation
  dictionary and `<sub alias>` for reliable results.
- **No ASR bundled**: WER/phrase-match need a transcript you provide; they do
  not "listen" to the WAV. For automated ASR verification, wire an offline
  recognizer (e.g., whisper.cpp) into your own CI and pass its transcript to
  `phrase-match`.
- **Buzz detector** is a labeled heuristic (spectral flatness), not a gate;
  listening remains authoritative.
- **Loudness** is a BS.1770-4 approximation (K-weighting + gated blocks,
  short-clip fallback); integrate with a reference meter before broadcast
  delivery. Resampling is linear, not polyphase. True peak is a 4×-interpolated
  estimate.
- **Formats**: WAV PCM16 in/out only. No MP3/FLAC/AAC containers (avoids an
  ffmpeg dependency); transcode downstream with your own tools.
- **GPU**: unused; Piper via onnxruntime CPU. `--engine piper-custom` with CUDA
  builds is possible by swapping the onnxruntime wheel yourself.
- **Duration stochasticity**: Piper's noise is random per run; the greeting
  typically lands 2.5–3.5 s. If it reads short, use the SSML asset (adds the
  240 ms pause) and/or `--length-scale 1.1`.
- **Consent records** are local JSON; they are evidence of operator diligence,
  not legal authorization or identity verification.

## 12. Troubleshooting & recovery

| Symptom | Fix |
|---|---|
| `exit 3`, "Neural engine is not ready" | Read the message: it names the missing piece. Model → `python3 tools-ov-fetch-model.py --yes` (or `--from-file`). Engine → `python3 tools-ov-setup.py`; then `diagnose` to see the probe. If the venv broke: `tools-ov-setup.py --force`. |
| `exit 8` from setup | Not tampering — the voice model is absent. Acquire it (§4a). `--check-only` still verifies the wheels fine. |
| Setup aborts with checksum mismatch | The bundle was tampered with or corrupted — re-fetch the folder; do not bypass. |
| "Not a valid WAV file" / "Unsupported sample width" | Input must be PCM16 WAV; convert first (`ffmpeg -i in.mp3 -acodec pcm_s16le -ac 1 out.wav`). |
| Silence trimmed that should be kept (SSML leading break) | Use `--no-trim`. |
| Loudness below target even after limiting | Source peaked too high (Piper normalizes to full scale); this is expected and reported honestly — the limiter protects the ceiling; do not chain another +gain. |
| Clipped samples reported by `validate` | Source was already clipping; re-master with lower target: `master --loudness-target -16`. |
| Batch item failed; rest ok | Read `batch-report.json` → per-item `error`; fix the item; re-run with `--resume` (only failures re-synthesize? No — skipped existing are kept; failures are re-attempted because their WAV is absent). |
| Worker timeout | Raise `synthesis_timeout_s` in config; check load (RAM pressure with 2 GB hosts). |
| De-esser/pop artifacts | De-click handles isolated spikes only; heavy artifacts mean the model mis-pronounced — fix the text/pronunciation dictionary, not the limiter. |
| `consent check` denies expected request | Inspect `reasons`; ensure consent record not expired (`consent list`); grant/re-grant. |
| Want MIT-only engine | Remove GPL wheels; `pip install piper-tts` yourself (§9); rest of tool unchanged. |

Recovery guarantees: failed synthesis never writes a partial WAV (raw temp file
is deleted on error); batch resume is idempotent; setup rollback = delete
`.ov-venv` and re-run; provenance sidecars are additive, never destructive.

## 13. Catalog coverage (honest mapping)

`capabilities` prints this machine-readably. Summary: **all 32 categories are
addressed**; most concrete skills are implemented as working code; a few are
tool-supported workflows or deliberately out-of-scope, listed below.

Implemented as working code: offline install & readiness (1), engine routing +
health/timeout/retry + quality warnings + no-silent-formant policy (2), model
integrity/capability checks for Piper-compatible models (3–4), friendly-male-30s
preset + delivery presets (5–6), rate/pause/emphasis control via SSML-lite +
`length_scale` (7), text normalization (8, 17), acronyms/pronunciation via
dictionary + espeak G2P in-engine (9), segmentation/chunking (10), SSML-lite +
security filter (11), core synthesis modules + unified CLI + batch (12–13),
per-process model load, no cross-run cache (14), voice-delivery consistency via
provenance (15), en-US locale handling (16), WAV containers + resampling (18–19),
mastering −14 LUFS/−1 dBFS (20), signal validation incl. silence/clipping/NaN/DC/
buzz heuristic (21), WER + phrase completeness (22), greeting acceptance gates
(24), regression tests + benchmark (25, 29), consent/minor/prohibited-use gates
(26), provenance/metadata/audit (27), package structure (28, 30), fallback
policy + failure handling with explicit errors (31), this documentation (32),
install/package builder (1, 30). Note: the delivery bundle deliberately omits
the 63 MB voice model (§4a) — `tools-ov-fetch-model.py` provides the pinned,
hash-verified acquisition path the catalog's bundle builder assumes.

Tool-supported workflows (tool does its part; human does the rest):
listening review & A/B testing (23) — scorecard data comes from `validate`/
`benchmark`, the decision is human; human approval before publishing (24, 26).

Not bundled, by design: non-Piper neural architectures (Kokoro, F5-TTS, XTTS,
StyleTTS2, Fish Speech, MeloTTS), a CUDA stack, an ASR model, MP3/FLAC/AAC
codecs, voice cloning itself, any network service. See §11.

## 14. Extension & maintenance

- **New preset**: add to `asset-voice-presets.json` (name, engine_params,
  mastering). No code change.
- **New voice model**: drop in `asset-voice-*.onnx(.json)` or pass `--model`;
  add its SHA-256 to `dependencies-checksums.txt` + a lock entry; verify with
  `setup --check-only`. Confirm the model license; update `dependencies-notices.md`.
- **Pronunciation fixes**: edit `asset-pronunciation-dictionary.json`
  (`acronyms` exact-match; `words` whole-word, case-insensitive).
- **Other platforms**: see §11. Keep wheels **unmodified**; bump versions only
  by re-downloading all of them and re-pinning hashes; run the full suite
  (`tools-run-tests.py`) after any change.
- **Policy changes**: edit `asset-safety-policy.json` (triggers/`prohibited_uses`,
  `sensitive_contexts`); re-run `tools-run-tests.py` (consent tests must pass).
- **SSML-lite extensions**: extend `tools-ov-ssml.py` (parser + `_SAY_AS_INTERPRET`)
  and add tests; keep the security filter first in the pipeline.
- **Provider addition**: implement the worker contract in `tools-ov-engine-worker.py`
  (job JSON in → `{ok, ...}` JSON out) and register the engine in
  `tools-ov-providers.py.engine_probe/resolve_engine`; never add a silent
  non-neural fallback.
- **Versioning**: bump `VERSION` in `tools-ov-core.py`; schema `ov-provenance-v1`
  stays until a breaking change.

## 15. License

Skill Tool code: MIT (see §9). Bundled components and the voice model carry
their own licenses — the obligatory notices file is
`dependencies-notices.md`. The GPL-3.0-or-later piper-tts component is called
out explicitly in every relevant section of this document; read it before
redistributing assembled audio commercially.
