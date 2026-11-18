# knowledge-readme.md - Audio Production Suite (skill tool component)

This folder is a **reusable Skill Tool component**, not a complete Copilot skill. It contains no
`SKILL.md`. Copy it beside a parent package's root `SKILL.md` and it works unchanged: every path
inside the package is relative to this folder and every asset is resolved from the folder that
holds `tools-audio-suite.py`.

- **Skill Tool name:** `audio-production-suite`
- **Primary entry point:** `tools-audio-suite.py`
- **Runtime:** Python 3.9+ (verified on 3.13.16), standard library only, no third-party packages
- **Scope:** all 28 categories and 280 capabilities of the *Ultimate Audio, Music, and SFX
  Skill Catalog*

---

## 1. Purpose

The catalog defines 280 audio capabilities but implements none of them. This Skill Tool turns the
catalog into an executable production surface: it plans work against any capability or category,
measures and gates real audio files, applies bounded processing, builds signed delivery packages,
and refuses risky actions without a recorded human approval.

It is deliberately honest about its boundary: it does **not** synthesize music, sing, generate
speech, separate stems, run ASR, or encode MP3/AAC. Those require external engines (see
[Section 14 - Limitations](#14-limitations-and-unavailable-integrations)). What it does provide, for every
one of the 280 capabilities, is the surrounding production machinery that most failures actually
come from: briefs, contracts, routing, measurable quality gates, reproducible processing, format
correctness, delivery proof, and approval control.

### What every capability gets

| Toolkit operation | Meaning for a capability |
| --- | --- |
| `plan` | Merge the capability's category playbook with a delivery preset into a staged production plan with controls, checks, deliverables, risks, and approval gates |
| `spec` | Emit the full contract sheet: inputs, outputs, controls, checks, recovery, targets, commands |
| `route` | Match free-text requirements to the right capability or category, with alternatives and coverage |
| `gate` | Objective pass/warn/fail measurement of a rendered artifact against a preset |
| `analyze` | Loudness, true peak, LRA, spectrum, stereo, defects, structural integrity |
| `process` | Bounded DSP chain (gain, filters, normalize, limit, trim, fade, resample, channel map, dither) |
| `batch` | Apply a recipe across a folder with per-file isolation and structured results |
| `manifest` | Checksummed delivery manifest validated against a packaged JSON schema |
| `regression` | Compare a candidate render against an approved reference (deltas + null test) |
| `sync-map` / `session-sheet` / `midi-map` / `capture-log` / `provenance` / `rights-check` / `repair-plan` | Delivered as structured plan/spec sections, action lists, and approval gates rather than as fixed binary formats |
| `generate-spec` | Delivered as a generation-control specification plus provenance and disclosure gates; actual generation stays external |

---

## 2. Complete capability list (28 categories, 280 capabilities)

Ids are stable (`C<category>-S<skill>`) and are the fastest thing to pass to `--capability`.
Run `python3 tools-audio-suite.py describe --capability C18-S03` for any single record, or
`capabilities --category <index|slug>` for one category.

| # | Category | Id range | Capabilities |
| --- | --- | --- | --- |
| 1 | Creative Direction, Production Strategy, and Orchestration | `C01-S01`-`C01-S10` | Audio Creative Director; Music Production Showrunner; Audio Scope Architect; Sonic Brand Strategist; Reference Track Analyst; Audio Feasibility Analyst; Mood and Emotion Mapper; Platform and Listening Context Adapter; Audio Quality Bar Designer; End-to-End Audio Production Orchestrator |
| 2 | Musical Ideation, Theory, and Song Architecture | `C02-S01`-`C02-S10` | Musical Concept Generator; Key, Scale, and Mode Designer; Chord Progression Composer; Song Form Architect; Motif and Leitmotif Designer; Counterpoint and Voice-Leading Specialist; Rhythmic Concept Designer; Dynamic Arc Planner; Musical Constraint Composer; Composition Variant Explorer |
| 3 | Melody, Harmony, Bass, and Rhythm Composition | `C03-S01`-`C03-S10` | Lead Melody Composer; Hook and Earworm Designer; Harmony and Reharmonization Artist; Bass Line Composer; Drum Pattern Composer; Polyrhythm and Advanced Meter Composer; Arpeggio and Ostinato Designer; Call-and-Response Composer; Transition and Fill Composer; Cadence and Ending Designer |
| 4 | Lyrics, Topline, and Vocal Songwriting | `C04-S01`-`C04-S10` | Lyric Concept and Theme Writer; Verse Lyric Writer; Chorus and Refrain Writer; Topline Melody Writer; Rhyme, Meter, and Prosody Editor; Rap and Spoken-Flow Writer; Multilingual Song Adaptation Writer; Character and Narrative Songwriter; Vocal Harmony Arranger; Lyric Sensitivity and Originality Reviewer |
| 5 | Arrangement, Orchestration, and Instrumentation | `C05-S01`-`C05-S10` | Pop and Contemporary Arranger; Orchestral Arranger; Jazz and Big Band Arranger; Electronic Music Arranger; Acoustic and Folk Arranger; Rock and Metal Arranger; Choral Arranger; Hybrid Score Orchestrator; Instrument Substitution Specialist; Playable Part and Range Validator |
| 6 | Genre and Style Production | `C06-S01`-`C06-S10` | Pop Music Producer; Hip-Hop and Trap Producer; Electronic Dance Music Producer; Rock, Punk, and Metal Producer; Jazz, Soul, Funk, and R&B Producer; Classical and Contemporary Concert Producer; Folk, Country, and Americana Producer; World and Regional Music Producer; Ambient, Experimental, and Noise Producer; Genre Fusion Designer |
| 7 | Film, Television, Trailer, and Narrative Scoring | `C07-S01`-`C07-S10` | Film Score Composer; Television and Episodic Composer; Trailer Music Composer; Documentary Score Composer; Comedy Score Specialist; Horror and Suspense Composer; Action and Adventure Composer; Animation Score Composer; Source Music and Diegetic Cue Designer; Picture Conform and Cue Revision Specialist |
| 8 | Game, Interactive, XR, and Adaptive Audio | `C08-S01`-`C08-S10` | Game Music Composer; Horizontal Resequencing Designer; Vertical Remixing Designer; Interactive Music Logic Architect; Game Sound Effects Designer; Spatial Game Audio Designer; Procedural Audio Designer; VR and AR Audio Designer; Middleware Integration Specialist; Interactive Audio Performance Optimizer |
| 9 | Sound Effects, Foley, and Sonic Worldbuilding | `C09-S01`-`C09-S10` | General Sound Effects Designer; Cinematic Impact and Whoosh Designer; Foley Performance Designer; Creature and Monster Sound Designer; Weapon and Combat Sound Designer; Vehicle and Machinery Sound Designer; Magic, Sci-Fi, and Technology Sound Designer; Nature and Environmental Sound Designer; Cartoon and Stylized SFX Designer; SFX Variation and Anti-Repetition Designer |
| 10 | Field Recording, Sampling, and Source Capture | `C10-S01`-`C10-S10` | Field Recording Planner; Stereo and Multichannel Field Recordist; Foley Recording Engineer; Instrument Sampling Producer; Impulse Response Capture Specialist; Location Dialogue Recordist; Ambience and Room Tone Recordist; Sampling Rights and Provenance Logger; Recording Metadata and Catalog Builder; Capture Quality and Backup Verifier |
| 11 | Synthesis, Sampling, and Instrument Creation | `C11-S01`-`C11-S10` | Subtractive Synthesis Designer; FM and Phase-Distortion Synthesis Designer; Wavetable and Vector Synthesis Designer; Granular Synthesis Designer; Physical Modeling Instrument Designer; Modular Synthesis Patch Designer; Sampler Instrument Builder; Drum Kit and Percussion Instrument Builder; Preset and Macro Designer; Synthetic Instrument Validation Specialist |
| 12 | MIDI, Sequencing, Notation, and Performance Data | `C12-S01`-`C12-S10` | MIDI Performance Programmer; Groove and Humanization Designer; Drum MIDI Editor; MPE and Expressive Controller Designer; Tempo and Meter Map Builder; Audio-to-MIDI Transcriber; Score Engraver; Tablature and Lead Sheet Creator; MIDI Routing and Synchronization Specialist; Performance Data Cleanup Specialist |
| 13 | Recording Engineering and Studio Production | `C13-S01`-`C13-S10` | Studio Session Planner; Microphone Selection and Placement Engineer; Gain Staging and Signal Flow Engineer; Multitrack Recording Engineer; Live Band Recording Engineer; Acoustic Instrument Recording Specialist; Amplifier and Reamping Specialist; Studio Monitoring and Calibration Specialist; Take and Playlist Manager; Recording Session Recovery Specialist |
| 14 | Voice, Speech, Dialogue, and Spoken Audio Creation | `C14-S01`-`C14-S10` | Voice Casting and Profile Designer; Voice Direction Planner; Narration and Voiceover Producer; Dialogue Performance Editor; ADR and Dubbing Producer; Synthetic Speech Generator; Singing Voice Synthesis Designer; Pronunciation and Phoneme Specialist; Podcast and Audiobook Voice Producer; Voice Consent and Identity Safety Manager |
| 15 | Vocal Production, Tuning, and Choir Design | `C15-S01`-`C15-S10` | Lead Vocal Producer; Vocal Comping Editor; Pitch Correction Specialist; Vocal Timing Editor; Vocal Cleanup Specialist; Vocal Layering and Double Designer; Choir and Ensemble Builder; Vocal Effects Designer; De-Essing and Presence Specialist; Vocal Production QA Reviewer |
| 16 | Audio Editing, Cleanup, Restoration, and Forensics | `C16-S01`-`C16-S10` | Waveform and Clip Editor; Noise Reduction Specialist; De-Reverb and Room Reduction Specialist; Click, Crackle, and Dropout Repairer; Clipping and Distortion Repairer; Spectral Repair Artist; Phase and Polarity Repair Specialist; Wow, Flutter, and Speed Repair Specialist; Archive Audio Restoration Specialist; Audio Forensic Authenticity Reviewer |
| 17 | Mixing, Dynamics, EQ, and Spatial Production | `C17-S01`-`C17-S10` | Static Mix and Balance Engineer; Equalization Specialist; Compression and Dynamics Specialist; Saturation and Harmonic Color Designer; Reverb and Delay Designer; Stereo Image and Depth Designer; Bus and Parallel Processing Engineer; Automation and Movement Mixer; Immersive and Surround Mixer; Mix Translation and Revision Specialist |
| 18 | Mastering, Loudness, Sequencing, and Release Preparation | `C18-S01`-`C18-S10` | Stereo Mastering Engineer; Album Sequencing Specialist; Loudness and True-Peak Specialist; Stem Mastering Engineer; Vinyl Pre-Mastering Specialist; Digital and Streaming Mastering Specialist; Broadcast and Podcast Mastering Specialist; Mastering Quality Control Reviewer; Alternate Master and Clean Version Builder; Release Master Manifest Builder |
| 19 | Podcasts, Radio, Broadcast, and Long-Form Speech | `C19-S01`-`C19-S10` | Podcast Format and Episode Producer; Interview Podcast Editor; Narrative Podcast Producer; Radio Imaging and Jingle Producer; Broadcast Delay and Compliance Editor; Audiobook Producer; Speech Intelligibility Optimizer; Chaptering and Searchable Transcript Builder; Ad Insertion and Segment Versioning Producer; Long-Form Consistency Reviewer |
| 20 | Live Sound, Performance, and Event Audio | `C20-S01`-`C20-S10` | Live Sound System Designer; Front-of-House Mix Engineer; Monitor Mix Engineer; Stage Patch and Input Coordinator; Live Broadcast and Stream Audio Mixer; Wireless Frequency Coordinator; Feedback Detection and Suppression Specialist; Show Control and Playback Operator; Live Multitrack Capture Engineer; Event Audio Safety and Contingency Manager |
| 21 | Spatial, Immersive, Acoustic, and Installation Audio | `C21-S01`-`C21-S10` | Binaural Audio Designer; Ambisonic Recording and Mixing Specialist; Object-Based Immersive Mixer; Acoustic Space Simulation Designer; Sound Installation Composer; Museum and Exhibition Audio Designer; Projection and Performance Spatial Audio Designer; Architectural Acoustics Review Assistant; Head-Tracked Audio Designer; Immersive Downmix and Compatibility Reviewer |
| 22 | Audio Software, Plugins, Hardware, and Automation | `C22-S01`-`C22-S10` | DAW Session Template Architect; Audio Plugin and DSP Developer; Audio Unit and Format Integration Specialist; Batch Audio Processing Engineer; Audio Scripting and Macro Developer; Hardware Synth and Studio Integration Specialist; Network Audio Engineer; Audio Test Signal and Measurement Tool Builder; Plugin Preset and Asset Manager; Audio Pipeline Health Monitor |
| 23 | Audio Analysis, Transcription, Search, and Intelligence | `C23-S01`-`C23-S10` | Beat, Downbeat, and Tempo Detector; Key, Chord, and Scale Analyzer; Melody and Polyphonic Transcriber; Instrument and Sound Event Classifier; Speaker Diarization and Transcript Aligner; Audio Similarity and Duplicate Finder; Sample and SFX Search Engine; Mix and Master Diagnostic Analyzer; Musical Structure and Section Detector; Audio Insight and Recommendation Agent |
| 24 | Generative Audio, Music, Voice, and Transformation | `C24-S01`-`C24-S10` | Text-to-Music Director; Text-to-SFX Designer; Text-to-Speech Director; Text-to-Singing Director; Audio-to-Audio Style Transformer; Music Continuation and Extension Artist; Stem and Source Separation Specialist; Voice Conversion Safety Specialist; Generative Consistency and Artifact Reviewer; Generated Audio Provenance Manager |
| 25 | Accessibility, Localization, Safety, Rights, and Ethics | `C25-S01`-`C25-S10` | Audio Accessibility Producer; Multilingual Dubbing and Localization Producer; Hearing Safety and Exposure Reviewer; Photosensitivity-Adjacent Audio Safety Reviewer; Music and Sample Rights Checker; Voice and Likeness Consent Manager; Cultural and Sacred Audio Reviewer; Explicit and Clean Content Versioning Specialist; Content Authenticity and Disclosure Reviewer; Privacy and Sensitive Audio Editor |
| 26 | Formats, Encoding, Metadata, Delivery, and Archival | `C26-S01`-`C26-S10` | Audio Format and Codec Specialist; Sample Rate and Bit Depth Conversion Specialist; Channel Layout and Mapping Specialist; Metadata and Tagging Specialist; Broadcast Wave and Timecode Specialist; Streaming and Download Encoding Specialist; Game Audio Asset Exporter; Audio Delivery Package Builder; Long-Term Audio Archivist; Playback and Import Compatibility Tester |
| 27 | Quality Assurance, Testing, Collaboration, and Recovery | `C27-S01`-`C27-S10` | Critical Listening QA Reviewer; Musical Accuracy Reviewer; Audio File Structural Validator; Loudness and Compliance Validator; Phase, Mono, and Polarity Validator; Automated Audio Regression Tester; Collaborative Review Manager; Session Dependency and Relink Resolver; Audio Disaster Recovery Specialist; Final Approval and Handoff Coordinator |
| 28 | Advanced Autonomous Audio Systems | `C28-S01`-`C28-S10` | Prompt-to-Production Audio Planner; Multimodal Sonic Brief Interpreter; Autonomous Composition Builder; Autonomous SFX Asset Builder; Autonomous Voice Production Builder; Audio Capability Router; Audio Repair Agent; Adaptive Quality and Compute Manager; Human Review and Approval Coordinator; End-to-End Audio Intelligence Orchestrator |
---

## 3. Packaged files, roles, and load order

Everything sits directly in this folder. There are no subfolders, no generated caches, and no
`SKILL.md`.

### Tools (runnable code)

| File | Role | Loaded by |
| --- | --- | --- |
| `tools-audio-suite.py` | **Primary entry point.** Command line surface, argument handling, output envelopes, exit codes, approval gate wiring, `selftest`. | executed directly |
| `tools-audio-common.py` | Shared plumbing: structured `ToolError`, result envelope, atomic writes, path resolution, resource limits, `Timer`, `load_sibling()` with one canonical module identity per file. | every other tool |
| `tools-audio-io.py` | RIFF/WAVE reader, structural inspector, writer (PCM 8/16/24/32 + float 32/64), TPDF dither with optional noise shaping, windowed-sinc polyphase resampler, channel mapping, crossfade concatenation, deterministic test-signal generator. | suite, qa, dsp, delivery |
| `tools-audio-qa.py` | Measurement and gating: K-weighting, gated integrated loudness, short-term max, loudness range, 4x oversampled true peak, peak/RMS/crest, DC, clipping runs, click detection, silence and noise floor, stereo correlation and mono fold-down loss, octave-band spectrum (in-house radix-2 FFT), preset gates, regression with null test, markdown report. | suite, dsp, delivery |
| `tools-audio-dsp.py` | Twelve bounded processing operations plus ordered recipe application with a per-step audit trace. | suite |
| `tools-capability-registry.py` | Catalog access layer: load, validate, list, exact lookup, weighted search, fuzzy resolve, statistics. Single source of truth for capability names. | suite, planner |
| `tools-audio-production-planner.py` | Brief validation, plan building and rendering, capability contract sheets, requirement routing. | suite |
| `tools-audio-delivery.py` | Manifest building with SHA-256, audio probing, preset compliance checks, JSON-schema-subset validation, checksum re-verification, markdown manifest. | suite |
| `tools-audio-approvals.py` | Approval ledger: request -> grant -> consume, revoke, list, verify, TTL expiry, action/target matching. | suite |

**Load order.** `tools-audio-suite.py` imports `tools-audio-common.py` first, then loads the other
tools on demand through `common.load_sibling()`. Each loader registers one canonical module name
(`aps_audio_common`, `aps_audio_io`, `aps_audio_qa`, `aps_audio_dsp`, `aps_capability_registry`,
`aps_audio_production_planner`, `aps_audio_delivery`, `aps_audio_approvals`), so every module
shares one `ToolError` class and one limits table. Each tool file can also be executed on its own
for a help summary, but the suite is the only command interface you need.

### Assets (data, templates, policies)

| File | Purpose | Read by |
| --- | --- | --- |
| `asset-capability-catalog.json` | 28 categories x 10 capabilities with ids, names, descriptions, per-capability operations and keywords. | registry, planner |
| `asset-category-playbooks.json` | One playbook per category: stages, controls, objective checks, deliverables, approval gates, limits, metrics. | planner, describe/spec |
| `asset-delivery-presets.json` | 10 delivery presets binding a medium to loudness/peak/format targets, deliverables, and a default recipe. | qa gates, planner, delivery |
| `asset-qa-policy.json` | Thresholds for every gate plus the gate catalogue (id, label, metric, severity). | qa, planner |
| `asset-processing-recipes.json` | 10 named operation chains (streaming, club, podcast, broadcast, cinema, score stem, SFX, game, archive, dialogue). | dsp/recipe/batch, planner |
| `asset-approval-policy.json` | Seven action classes that require human approval, their risk level, approver, and TTL. | approvals, planner |
| `asset-delivery-manifest.schema.json` | JSON Schema (draft-07 subset) the manifest validator enforces. | delivery |
| `asset-plan-template.md` | Markdown plan template with `{{PLACEHOLDER}}` tokens; the planner fails loudly if any token is left unreplaced. | planner |

`knowledge-readme.md` (this file) is the only documentation file in the folder.

---

## 4. Setup

```sh
# from the folder that contains tools-audio-suite.py
sh -c 'python3 --version'          # expect 3.9 or newer
sh -c 'python3 tools-audio-suite.py version'
sh -c 'python3 tools-audio-suite.py selftest --clean'
```

- **No installs.** No `pip`, no virtualenv, no network access, no credentials, no external service.
- **Supported Python:** 3.9-3.14. Verified on CPython 3.13.16 (Linux). The code avoids
  `audioop` (removed in 3.13), `distutils`, and any 3.13-only syntax beyond `from __future__`
  annotations.
- **Working directory:** irrelevant. Assets, templates, and sibling modules resolve from the
  folder that contains `tools-audio-suite.py`. Inputs and outputs may be absolute or relative to
  where you run the command.
- **Platform:** pure Python file I/O; no shelling out, no device access, no OS-specific code
  beyond `sys.byteorder` handling for WAV byte order.
- **Optional environment variable:** `APS_DEBUG=1` re-raises the real traceback instead of the
  packaged `unexpected_error` envelope.

If you copy the folder next to a parent `SKILL.md`, keep the filenames unchanged: the exact
case-sensitive names above are referenced in code and in this document.

---

## 5. Quick start

```sh
# 1. see the catalog
sh -c 'python3 tools-audio-suite.py capabilities'
sh -c 'python3 tools-audio-suite.py capabilities --query "mastering loudness true peak"'

# 2. build a plan for a brief
sh -c 'python3 tools-audio-suite.py plan --title "Night Drive single" \
  --goal "master a synthwave single for streaming" --medium music --duration 210 \
  --rights-status cleared --out plan.md --format markdown'

# 3. get the contract for one capability
sh -c 'python3 tools-audio-suite.py spec --capability C18-S03 --preset streaming-music --format markdown'

# 4. measure and gate an existing master
sh -c 'python3 tools-audio-suite.py analyze --input master.wav --preset streaming-music --format markdown'

# 5. process with a packaged recipe, then re-measure
sh -c 'python3 tools-audio-suite.py recipe --input mix.wav --output master.wav --recipe streaming-safe-normalize'
sh -c 'python3 tools-audio-suite.py analyze --input master.wav --preset streaming-music --strict'

# 6. build and verify a delivery package
sh -c 'python3 tools-audio-suite.py manifest --input delivery --preset streaming-music --out delivery/manifest.json --format markdown'
sh -c 'python3 tools-audio-suite.py validate-manifest --manifest delivery/manifest.json'
sh -c 'python3 tools-audio-suite.py verify-manifest --manifest delivery/manifest.json'
```

---

## 6. Command reference

All commands accept `--out PATH`, `--format {json,markdown,both}`, and the global options
`--ledger`, `--approval`, `--timeout`, `--quiet`, `--json` (before **or** after the command name).
JSON is the default output; markdown is available where a human report exists.

### 6.1 `help [command]`
Prints the global help, or the argparse help for one command.
```sh
sh -c 'python3 tools-audio-suite.py help'
sh -c 'python3 tools-audio-suite.py help analyze'
```

### 6.2 `version`
Prints suite name, version, Python version, the resolved skill root, the tool list, the asset list,
and the active limits table.

### 6.3 `capabilities`
Lists the 28 categories, or searches the 280 capabilities.

| Option | Meaning |
| --- | --- |
| `--query TEXT` | Weighted search over names, descriptions, keywords, and category names. Common words are down-weighted; a query with no defensible match returns `count: 0`. |
| `--category ID` | Category index (`18`), slug, or name; lists that category's capabilities. |
| `--limit N` | Maximum rows (default 10). |

```sh
sh -c 'python3 tools-audio-suite.py capabilities --category 18'
sh -c 'python3 tools-audio-suite.py capabilities --query "creature sound design" --limit 3'
```

### 6.4 `describe --capability ID|NAME`
Full record for one capability (id, description, operations, category playbook stages) or one
category. Accepts ids (`C18-S03`), category shorthand (`C18`, `c18`, `category 18`), slugs, names,
or fuzzy text.
```sh
sh -c 'python3 tools-audio-suite.py describe --capability C09-S04 --format markdown'
```

### 6.5 `route --requirements FILE|TEXT`
Maps requirements to capabilities. Delimit inline requirements with `;` or pass a text file with
one requirement per line. Returns primary match, alternatives, score, unmatched lines, and
coverage.
```sh
sh -c 'python3 tools-audio-suite.py route --requirements "master to -16 LUFS;design a creature voice;zzz"'
sh -c 'python3 tools-audio-suite.py route --requirements requirements.txt --limit 2'
```
Routing is a ranked suggestion, not an authority: confirm the primary hit before committing budget.

### 6.6 `plan`
Builds a production plan from a brief (JSON file and/or flags; flags win).

| Option | Meaning |
| --- | --- |
| `--brief PATH` | JSON brief. Allowed keys: `title, goal, audience, medium, preset, duration_seconds, deadline, rights_status, constraints, capabilities, notes`. Unknown keys are rejected. |
| `--goal TEXT` | **Required** unless the brief supplies it. |
| `--title`, `--audience`, `--notes`, `--deadline` | Free text. |
| `--medium` | Maps to a preset (`music`, `podcast`, `broadcast`, `film`, `game`, `sfx`, `archive`, ...). |
| `--preset ID` | Explicit preset, overriding the medium mapping. |
| `--duration SECONDS` | Numeric, validated. |
| `--rights-status` | `unknown, cleared, pending, restricted, public-domain, licensed, generated`. |
| `--constraint TEXT` | Repeatable; also accepted as a `;`-separated brief field. |
| `--capability ID|NAME` | Repeatable; pins capabilities instead of routing from text. |

Output: JSON with capabilities, stages, controls, checks, deliverables, metrics, targets,
assumptions, risks, approval gates, and next commands - or a Markdown plan from
`asset-plan-template.md` (`--format markdown`).
```sh
sh -c 'python3 tools-audio-suite.py plan --goal "podcast episode master" --medium podcast --out plan.md --format markdown'
sh -c 'python3 tools-audio-suite.py plan --brief brief.json --out plan --format both'
```

### 6.7 `spec --capability ID --preset ID`
Contract sheet for one capability or a whole category: inputs, outputs, controls, checks, recovery,
stages, targets, limits, approval gates, operations, and copy-ready commands.

### 6.8 `inspect --input FILE.wav [--max-seconds N]`
Structural validation without decoding: RIFF chunk table, `fmt` fields, `data` size, truncation,
declared-vs-actual sizes, block alignment, valid bits, channel mask, sample format support,
duration. Missing file -> `not_found`; non-RIFF -> `invalid_audio`; truncated -> reported with
`structurally_valid: false` (and refused by `read` paths).

### 6.9 `analyze --input FILE.wav [--preset ID] [--max-seconds N] [--skip-true-peak] [--strict]`
Full measurement plus gating. See [Section 8 - measurement standards](#8-measurement-standards-and-calibration) for definitions
and [Section 9 - delivery presets](#9-delivery-presets) for presets. `--strict` exits `3` when the verdict is `fail`.
`--skip-true-peak` uses sample peak instead (about 30 % faster on long files).

### 6.10 `regression --reference FILE --candidate FILE [--strict]`
Duration, loudness, true-peak deltas, and a sample-aligned null test with residual level. Files of
different rate, channel count, or length skip the null test and say so.

### 6.11 `process` and 6.12 `recipe`
Both run an ordered operation chain on one file. `process` takes explicit operations; `recipe`
loads a packaged chain (and may be combined with extra `--op`s).

| Option | Meaning |
| --- | --- |
| `--input`, `--output` | Source and destination WAV. |
| `--op NAME[:VALUE]` | Repeatable, applied in order. See [Section 7 - DSP operations](#7-dsp-operations-reference). |
| `--ops-json PATH` | JSON file containing a list of operation objects, or `{"ops": [...]}`. |
| `--recipe ID` | Packaged recipe id (required for `recipe`). |
| `--bits` | `8, 16, 24, 32` for PCM; `32, 64` for float. |
| `--sample-format` | `pcm` (default) or `float`. |
| `--dither`, `--shaping {0,1,2}` | TPDF dither and optional 1st/2nd-order noise shaping. |
| `--dry-run` | Validates the whole chain and reports what would happen; writes nothing. |

Nothing is written until every operation succeeds, and existing outputs require a
`destructive-overwrite` approval (either is refused with a structured error and a hint).
```sh
sh -c 'python3 tools-audio-suite.py process --input in.wav --output out.wav --op highpass:80 --op normalize:-16 --op limit:-1.5 --bits 16 --dither'
sh -c 'python3 tools-audio-suite.py recipe --input in.wav --output out.wav --recipe podcast-speech-master --dry-run'
```

### 6.13 `batch`
Applies a recipe (plus optional extra `--op`s) across a folder.
`--pattern` (default `*.wav`), `--recursive`, `--max-files`, `--continue-on-error`, `--dry-run`.
Per-file results are isolated: one corrupt file fails alone when `--continue-on-error` is set,
otherwise the run stops at the first failure. Overwriting existing outputs needs a
`destructive-overwrite` approval targeted at the **output directory**; more than 10 files needs a
`bulk-batch` approval.

### 6.14 `concat`
Joins two or more files with an equal-power crossfade. Files with different sample rates or channel
counts are converted to the first file's rate and the widest channel layout; `--sample-rate`
overrides the target rate. `--crossfade` is in seconds.

### 6.15 `split`
Cuts one file into segments.
- `--at "2.0,4.5"` explicit cut times in seconds (validated against duration).
- `--silence SECONDS` cut at the middle of silent runs at least this long, at `--threshold-db`
  (default -45), dropping cuts that would create segments shorter than `--min-segment`
  (default 1.0 s).
Every segment gets a 3 ms equal-power edge guard so cuts cannot click.

### 6.16 `manifest`
Builds the delivery manifest for a folder.

| Option | Meaning |
| --- | --- |
| `--input DIR` | Delivery folder. |
| `--preset ID` | Preset used for format and gate checks. |
| `--out PATH` | Manifest path (default `<dir>/manifest.json`). The manifest never lists itself. |
| `--report PATH` | Optional markdown summary written alongside. |
| `--no-analyze` | Skip per-file loudness analysis (structural probe only). |
| `--max-files N` | Refuse folders above this count (default 5000). |
| `--sign-off-name NAME` | Sign the manifest. Requires a `publish-deliverable` approval. |
| `--rights-status` | Recorded with the sign-off. |
| `--strict` | Exit `3` if any check fails. |

### 6.17 `validate-manifest --manifest PATH [--schema PATH]`
Validates against `asset-delivery-manifest.schema.json` (or your own schema) using the supported
keyword subset, and rejects absolute paths or `..` traversal in `files[].path`.

### 6.18 `verify-manifest --manifest PATH [--root DIR]`
Recomputes SHA-256 for every listed file. Reports missing and mismatched files; exit `2` when the
package no longer matches the manifest.

### 6.19 `approvals request|grant|revoke|list|verify`
| Sub-action | Key options | Effect |
| --- | --- | --- |
| `request` | `--action ID --target PATH --requested-by NAME --note TEXT` | Creates a `pending` entry and prints the id. |
| `grant` | `--id ID --by NAME --note TEXT` | Marks `granted` and sets an expiry (`token_ttl_minutes`, default 120). |
| `revoke` | `--id ID --note TEXT` | Marks `revoked`; a revoked approval can never be used. |
| `list` | `--status STATE --limit N` | Lists entries and counts. |
| `verify` | `--id ID --action ID --target PATH --consume` | Checks status, action, target, expiry; `--consume` marks single use. |
| all | `--ledger PATH` | Ledger file (default `approval-ledger.json` in the current directory). |

### 6.20 `selftest [--workdir DIR] [--clean] [--strict]`
Runs the 20 built-in checks listed in [Section 17 - validation](#17-validation-actually-performed). Artifacts go to
`.aps-selftest/` inside the folder by default - pass `--workdir` to keep the package folder clean.

---

## 7. DSP operations reference

| `--op` | Parameters (range / default) | Behaviour |
| --- | --- | --- |
| `gain` | `db` (-120...60, 0) | Static gain. |
| `polarity` | `invert` (true) | Polarity inversion. |
| `dc-block` | `order` (2 or 4) | 10 Hz high-pass for DC/offset removal. |
| `highpass` | `hz` (10...20000, 80), `q` (0.1...20, 0.707), `order` (2 or 4) | RBJ biquad high-pass, optionally cascaded twice. |
| `lowpass` | same ranges | RBJ biquad low-pass. |
| `channels` | `mode`: `mono`, `stereo`, `swap`, `select:N`, `strip:N` | Equal-gain mono fold-down, dual-mono upmix, swap, single-channel extract, single-channel removal. |
| `resample` | `rate` (8000...768000), `quality` `low|medium|high` (8/16/32 half-taps) | Windowed-sinc polyphase conversion with anti-alias cutoff. |
| `trim` | `start_sec`, `end_sec` | Hard trim with a guaranteed 2 ms edge guard. |
| `trim-silence` | `threshold_db` (-50), `pad_sec` (0.1) | Removes leading/trailing silence; refuses if nothing would remain. |
| `fade` | `in_sec`, `out_sec` | Equal-power sine fades. |
| `normalize` | `target_lufs` (-50...0) **or** `target_dbtp` | Measures gated integrated loudness (or sample peak) and applies the gain; refuses gains above +60 dB. |
| `limit` | `ceiling_dbtp` (-20...0, -1), `lookahead_ms` (0.5...50, 5), `release_ms` (5...2000, 80) | Look-ahead peak limiter. The gain curve is computed from the *future* window, so it needs no delay: unlimited passages stay sample-identical, verified in `selftest`. A final safety scale guarantees the ceiling. |

`--op` shorthand values: `--op gain:-6`, `--op highpass:80`, `--op normalize:-16`, `--op limit:-1.5`,
`--op resample:48000`, `--op channels:mono`, `--op fade:0.05`, `--op trim-silence:-50`,
`--op polarity`, `--op dc-block`. For anything with more than one parameter use `--ops-json`.

Operations are validated **before** the first sample is touched (unknown op, out-of-range value,
wrong arity, empty chain, more than 32 operations). A chain is atomic: failure leaves no partial
output file.

---

## 8. Measurement standards and calibration

| Measurement | Implementation | Notes |
| --- | --- | --- |
| Integrated loudness | BS.1770-4 K-weighting (canonical pre-warped bilinear derivation, valid at any rate) + 400 ms blocks / 100 ms hop, absolute gate -70 LUFS, relative gate -10 LU | Calibrated against EBU Tech 3341 semantics: stereo 1 kHz sine at -23 dBFS reads **-23.04 LUFS** at 22.05, 44.1, 48 and 96 kHz; -33 dBFS reads -33.04 LUFS; -20 dBFS reads -20.04 LUFS. Tolerance +/-0.1 LU. |
| Short-term max | 3 s blocks, 100 ms hop, gated at -70 | Reported as `short_term_max_lufs`. |
| Loudness range | 3 s blocks, 1 s step, -70 absolute and -20 relative gates, 10th/95th percentiles | EBU Tech 3342 style. |
| True peak | 4x oversampling with a windowed-sinc kernel | **Estimate** (+/-0.5 dB in practice): verified -6 dBFS 997 Hz sine reads -6.00 dBTP. Use a dedicated TP meter for certification. |
| Sample peak / RMS / crest | Direct | dBFS relative to full scale 1.0. |
| DC offset | Mean per channel | Gate default 0.002. |
| Clipping | Runs of >=3 consecutive samples at or above 0.9995 | Gate default 0 runs. |
| Clicks | Second-difference outliers gated by local energy | Heuristic; gate default 2 per minute. |
| Silence | 50 ms frames below `silence_threshold_dbfs` (-60) | Leading/trailing/longest/ratio. |
| Noise floor | 10th percentile of 50 ms frame RMS | Estimate of the quiet floor, not a calibrated measurement. |
| Stereo correlation | Pearson correlation overall and per 100 ms frame | Gate default >= -0.2. |
| Mono fold-down loss | (mono RMS) - (stereo RMS) in dB | 0 dB = no loss; -6 dB gate. |
| Spectrum | Radix-2 FFT, 4096-point Hann frames, 8 bands (sub...air) in dB relative to total | Report only, no gate. |

All analysis is read-only. Measurement never modifies its input.

---

## 9. Delivery presets

`asset-delivery-presets.json` - each preset carries targets, container format, deliverables, and a
default recipe. Preset ids are used by `analyze`, `manifest`, `plan`, `spec`, and `recipe`
defaults.

| Preset | Integrated | True peak | Sample rate | Bits | Channels |
| --- | --- | --- | --- | --- | --- |
| `streaming-music` | -14 LUFS +/-1 | -1.0 dBTP | 44100 | 24 | 2 |
| `club-playback` | -9 LUFS +/-1.5 | -0.5 dBTP | 44100 | 24 | 2 |
| `podcast-speech` | -16 LUFS +/-1 | -1.5 dBTP | 48000 | 24 | 1 |
| `broadcast-r128` | -23 LUFS +/-0.5 | -1.0 dBTP | 48000 | 24 | 2 |
| `cinema-feature` | -27 LUFS +/-2 | -3.0 dBTP | 48000 | 24 | 2 |
| `film-score-stems` | -23 LUFS +/-2 | -6.0 dBTP | 48000 | 24 | 2 |
| `audiobook` | -19 LUFS +/-1.5 | -3.0 dBTP | 44100 | 24 | 1 |
| `game-interactive` | -18 LUFS +/-2 | -1.0 dBTP | 48000 | 24 | 2 |
| `sfx-library` | -20 LUFS +/-3 | -3.0 dBTP | 48000 | 24 | 2 |
| `archival-master` | none (no lift) | -1.0 dBTP | 96000 | 24 | 2 |

Gates map straight onto these numbers: loudness target +/- tolerance, true-peak ceiling, LRA guide,
sample rate, minimum bit depth, channel count (channel count is a warning for `sfx-library` and
`film-score-stems`, where mono and multi-channel items are expected).

**Add a preset** by appending to `presets` with the same fields; then reference it in
`asset-processing-recipes.json` (`"preset": "<id>"`) if a recipe should default to it. No code
change is required.

---

## 10. Recipes

`asset-processing-recipes.json` (validated on every use):

| Recipe | Preset | Chain summary |
| --- | --- | --- |
| `streaming-safe-normalize` | streaming-music | dc-block -> normalize -14 LUFS -> limit -1 dBTP |
| `club-loud-normalize` | club-playback | dc-block -> normalize -9 LUFS -> limit -0.5 dBTP |
| `podcast-speech-master` | podcast-speech | mono -> trim silence -> HPF 80 Hz -> normalize -16 LUFS -> limit -1.5 -> fades -> 48 kHz |
| `broadcast-headroom-transfer` | broadcast-r128 | dc-block -> normalize -23 LUFS -> limit -1 dBTP -> 48 kHz |
| `cinema-headroom-transfer` | cinema-feature | dc-block -> limit -3 dBTP -> 48 kHz (no level lift) |
| `film-stem-headroom` | film-score-stems | dc-block -> limit -6 dBTP (no level lift) |
| `sfx-cleanup` | sfx-library | trim silence -> dc-block -> HPF 30 Hz -> edge fades -> peak -3 dBFS |
| `game-asset-normalize` | game-interactive | trim silence -> dc-block -> normalize -18 LUFS -> limit -1 -> 48 kHz |
| `archive-transfer` | archival-master | dc-block only; rate change stays an explicit, separate decision |
| `dialogue-cleanup` | broadcast-r128 | HPF 90 Hz -> dc-block -> normalize -23 LUFS -> limit -3 dBTP |

Recipes are *defaults*, not mastering. Each chain is auditable step by step in the JSON `chain`
array, with peak and duration before/after every step.

---

## 11. QA policy

`asset-qa-policy.json` holds every threshold. Change a value here rather than editing code:

| Key | Default | Used by |
| --- | --- | --- |
| `default_preset` | `streaming-music` | fallback when no preset is given |
| `integrated_tolerance_lu` | 1.0 | preset fallback tolerance |
| `silence_threshold_dbfs` | -60 | silence detection |
| `click_sensitivity` | 8.0 | click detector threshold vs local energy |
| `max_clipped_runs` | 0 | clipping gate |
| `max_dc_offset` | 0.002 | DC gate |
| `max_clicks_per_minute` | 2 | click gate |
| `min_correlation` | -0.2 | correlation gate |
| `max_mono_loss_db` | -6.0 | mono fold-down gate |
| `max_silence_ratio` | 0.98 | silence gate |
| `check_integrity`, `check_clipping`, `check_dc`, `check_clicks`, `check_stereo`, `check_silence` | true | enable or disable each gate family |

The `gates` array is the human catalogue (id, label, metric, severity) consumed by `spec` output;
severities are advisory labels, while the actual pass/warn/fail logic lives in
`tools-audio-qa.py:evaluate_gates`.

---

## 12. Approvals (safety interlock)

`asset-approval-policy.json` defines seven action classes: `destructive-overwrite`,
`delete-masters`, `publish-deliverable`, `rights-signoff`, `voice-likeness`, `bulk-batch`,
`irreversible-repair`.

The suite enforces three of them directly:

| Trigger | Action id | Target the approval must name |
| --- | --- | --- |
| `process`/`recipe`/`concat`/`split` writing over an existing file | `destructive-overwrite` | the exact output file |
| `batch` writing over existing outputs | `destructive-overwrite` | the output directory |
| `batch` of more than 10 files | `bulk-batch` | the output directory |
| `manifest --sign-off-name` | `publish-deliverable` | the delivery directory |

Workflow:

```sh
# 1. request
sh -c 'python3 tools-audio-suite.py approvals request --action destructive-overwrite \
  --target /work/delivery/master.wav --requested-by "Mixer" --note "re-print after EQ fix" --ledger approvals.json'

# 2. a human grants it (this is the approval step; keep the ledger in version control or a vault)
sh -c 'python3 tools-audio-suite.py approvals grant --id APR-1A2B3C4D --by "Audio Lead" --ledger approvals.json'

# 3. the action consumes it
sh -c 'python3 tools-audio-suite.py process --input mix.wav --output /work/delivery/master.wav \
  --recipe streaming-safe-normalize --approval APR-1A2B3C4D --ledger approvals.json'
```

Guarantees: a pending, revoked, expired, wrong-action, wrong-target, or already-consumed approval is
refused with a specific error code; grants expire after `token_ttl_minutes` (default 120); each
grant authorises exactly one action. `--dry-run` never consumes an approval.

The remaining action classes (`delete-masters`, `rights-signoff`, `voice-likeness`,
`irreversible-repair`) are enforced procedurally: the planner and contract sheets list them as
blocking gates, and the manifest records the approval id and rights status used for sign-off. The
suite has no delete operation at all, so "delete masters" can only happen outside the tool - on
purpose.

---

## 13. Delivery manifests

`manifest` builds:

- `files[]` - every delivered file with `path` (relative), `role` (master/stem/alternate/reference/
  transcript/document/asset inferred from the filename), `bytes`, `sha256`, and for WAV files an
  `audio` block with sample rate, channels, bit depth, duration, structural validity, integrated
  loudness, true peak, verdict, and failed gates.
- `totals` - file count, total bytes, audio file count.
- `checks[]` - audio present, structural validity, per-file preset gates, format match against the
  preset.
- `sign_off` - approver, approval id, rights status, timestamp (only with `--sign-off-name`).
- The manifest never includes itself or its own `--report` file, so rebuilding is idempotent.

**Schema validation subset** (implemented in `tools-audio-delivery.py:validate_instance`, not a
third-party library): `type` (including lists of types), `required`, `properties`, `items`,
`enum`, `pattern`, `minimum`, `maximum`, and boolean schemas. Keywords outside this subset in a
custom schema are ignored - document any extension you rely on. Path traversal and absolute paths
in `files[].path` are rejected separately.

---

## 14. Limitations and unavailable integrations

This tool implements planning, measurement, bounded DSP, delivery proof, and approval control. It
does **not** implement, and does not claim to implement:

| Not implemented | Why | What the tool does instead |
| --- | --- | --- |
| Music composition, arrangement, performance rendering | Generative/notation engines are external and non-deterministic | Plans, contract sheets, stage/control/check lists, delivery and gate specs for the composition capabilities |
| Text-to-speech, text-to-music, text-to-SFX, voice conversion, singing synthesis | Requires an authorized external model and consent record | Generation-control specification, consent/approval gates, provenance and disclosure fields, artifact review checklist |
| Source separation, ASR, diarization, instrument classification, beat/key detection | Requires trained models | Analysis of what is measurable from the signal (loudness, spectrum, stereo, defects) plus routing to the right capability |
| MP3 / AAC / Opus / FLAC / ALAC / DSD encoding, ADM BWF, Atmos rendering | Requires codec libraries and licensed renderers | PCM and IEEE-float WAV writing with dither and noise shaping; format selection guidance; encode externally then re-run `analyze`/`manifest` |
| Notation engraving, DAW session files, plugin formats | Application-specific | MIDI/notation guidance in specs; delivery manifests of whatever files you export |
| Real-time audio, hardware control, console/DSP integration, network audio | Needs devices and drivers | System, patch, cue, and contingency documents; measurement of rendered files |
| Loudness compliance certification, legal clearance, hearing-safety sign-off | Needs calibrated hardware and qualified humans | Measurements, thresholds, warnings, and approval gates - explicitly advisory |

Additional honest caveats:

- True peak is a 4x oversampled estimate, not a certified meter.
- Click, noise-floor, and silence detection are heuristics with policy thresholds you can tune.
- Capability routing is a weighted keyword/name/difflib match. It ranks and explains; it does not
  understand musical intent.
- Loudness of multi-channel files above stereo uses unit channel weights (no surround weighting or
  LFE exclusion).
- The K-weighting derivation matches the standard at all tested rates (-23.04 LUFS at 22.05-96 kHz);
  a 0.04 LU offset remains versus the strict nominal reading, inside the +/-0.1 LU tolerance.

---

## 15. Limits, performance, and timeouts

| Limit | Default | Override |
| --- | --- | --- |
| Audio length per file | 600 s | `--max-seconds` (hard ceiling 7200 s) |
| Channels | 32 | fixed in `tools-audio-common.py:LIMITS` |
| Files per batch | 500 | `--max-files` |
| Manifest files | 5000 | `--max-files` |
| Operations per chain | 32 | fixed |
| Command time budget | 300 s | `--timeout` |
| Approval validity | 120 min | `asset-approval-policy.json:token_ttl_minutes` |
| Memory | decoded audio is float64 per channel (~16 bytes/frame stereo) | shorten the file or raise `--max-seconds` deliberately |

Measured on one core of the verification machine (Python 3.13, 30 s stereo 44.1 kHz file):
decode 0.8 s; full `analyze` 13.7 s (~2x faster than realtime); `analyze --skip-true-peak` 9.7 s;
44.1->48 kHz resample 8.1 s (~3.7x realtime); normalize + limit 3.6 s. Long-form work should use
`--skip-true-peak`, segment the file, and raise `--timeout` in step.

Cancellation: `Ctrl-C` aborts before the atomic write, so no partial output file is left behind.
Timeouts raise `timeout` with the stage name and elapsed seconds.

---

## 16. Safety rules encoded in the tool

1. Existing files are never overwritten without a matching, granted, unexpired, unused
   `destructive-overwrite` approval.
2. Delivery sign-off requires a `publish-deliverable` approval; the approval id is written into the
   manifest.
3. Batches above 10 files require a `bulk-batch` approval.
4. Nothing is deleted by any command. There is no delete operation.
5. All processing is offline, local, and bounded; no shell execution, no network calls, no
   arbitrary code evaluation from data files.
6. `--dry-run` validates and reports without writing and without consuming approvals.
7. Voice, likeness, rights, and cultural material are surfaced as blocking approval gates in plans,
   specs, and manifests rather than silently assumed cleared.
8. Every write is atomic (temp file + `os.replace`), and every analysis input is opened read-only.

---

## 17. Validation actually performed

Everything below was executed in this build on CPython 3.13.16 (Linux, x86-64). No test result here
is projected or assumed.

### 17.1 Built-in `selftest` - 20 checks, 20 passed

| Check | Result |
| --- | --- |
| `catalog.integrity` | 28 categories, 280 capabilities, 28 playbooks |
| `qa.loudness_calibration_48k` | -23.04 LUFS for a -23 dBFS stereo sine |
| `qa.loudness_calibration_44k` | -23.04 LUFS at 44.1 kHz |
| `qa.true_peak_sanity` | -6.00 dBTP for a -6 dBFS 997 Hz sine |
| `dsp.gain` | -6 dB op measured -6.00 dB (peak -23.00 -> -29.00 dBFS) |
| `dsp.normalize_and_limit` | -14.00 LUFS, true peak -13.75 dBTP (under ceiling) |
| `dsp.trim_silence` | 2.0 s padded file -> 1.2 s |
| `dsp.resample` | 48 -> 44.1 kHz, duration preserved to < 2 ms |
| `dsp.limiter_ceiling` | -0.2 dBFS input -> -1.00 dBTP |
| `dsp.limiter_transparency` | signal below the ceiling is sample-identical (max delta 0.0) |
| `io.float32_roundtrip` | max error < 1e-6, format reported `float32` |
| `errors.missing_file` | `not_found` |
| `errors.bad_argument` | `invalid_argument` |
| `errors.truncated` | `corrupt_audio` (a half-truncated WAV is refused before decode) |
| `planner.plan` | plan rendered with no unreplaced placeholders |
| `planner.route` | 2/2 requirement lines routed (coverage 1.0) |
| `delivery.manifest_roundtrip` | schema valid + checksums verified |
| `delivery.tamper_detection` | appended byte -> mismatch reported |
| `approvals.lifecycle` | pending blocked, grant accepted once, reuse refused |
| `qa.regression` | identical files pass; -6 dB version fails loudness comparison |

### 17.2 Manual command-line matrix (all executed)

`help`, `help analyze`, `version`, `capabilities` (list, query, category, junk query, bad category),
`describe` (id, fuzzy name, category shorthand, missing id), `route` (inline and file), `plan`
(flags, JSON brief, `--format both`, missing goal, bad preset), `spec` (capability and category),
`inspect` (24-bit mono, 16-bit, truncated, non-RIFF, `--max-seconds` guard), `analyze`
(markdown + JSON, preset gates, timeout guard), `regression` (identical and changed),
`process` (chain, dry-run, overwrite gating, approval flow, float output, `--ops-json`),
`recipe` (podcast master, unknown recipe), `batch` (dry-run, real, overwrite gating, corrupt-file
isolation with and without `--continue-on-error`), `concat` (same and mixed rates),
`split` (`--at`, silence with `--min-segment`, out-of-range time), `manifest` (build, rebuild,
`--report`, sign-off approval), `validate-manifest` (valid and deliberately invalid),
`verify-manifest` (clean and tampered), `approvals` (request, grant, list, verify, reuse, revoke
path), `selftest` (JSON and markdown).

### 17.3 Format and numeric checks

- WAV round-trips through 8/16/24/32-bit PCM and 32/64-bit float: max absolute error 1.1e-2 (8-bit),
  4.3e-5 (16-bit), 5.6e-8 (24-bit), 2.3e-10 (32-bit), 7.3e-9 (float32), 0.0 (float64); every file
  re-inspected as structurally valid.
- Loudness calibration at 22 050 / 44 100 / 48 000 / 96 000 Hz: -23.06 / -23.04 / -23.04 / -23.03
  LUFS for the same -23 dBFS sine; level sweep -20/-23/-33 dBFS tracked within 0.04 LU.
- Limiter holds the ceiling exactly on a hot signal and is bit-transparent below it.
- Manifest rebuild is idempotent (same file count and totals when the manifest lives inside the
  delivery folder).

### 17.4 Packaging checks

Performed after the folder was finalized: structure and prefix audit, no `SKILL.md`, no subfolders,
no caches, ZIP built with the folder at the archive root, archive inspected, then extracted to a
clean temporary location and the primary tool re-run from the extracted copy beside a test
`SKILL.md`. Details are in the delivery message rather than duplicated here.

---

## 18. Troubleshooting and recovery

`unexpected_error` never hides the cause silently: set `APS_DEBUG=1` to get the traceback.

| Error code | Meaning | Recovery |
| --- | --- | --- |
| `not_found` | Input file or folder missing | Check the path; paths are resolved from the current directory |
| `invalid_path` / `io_error` | Empty or unwritable path | Create the parent directory; check permissions |
| `invalid_audio` | Not RIFF/WAVE, or a missing `fmt`/`data` chunk | Convert to WAV, then re-run |
| `corrupt_audio` | Truncated or inconsistent chunk table | Recover the original capture; `inspect` shows which chunk disagrees |
| `unsupported_format` | Sample format outside PCM 8/16/24/32 and float 32/64 | Convert externally, then re-run |
| `limit_exceeded` | Duration, channels, files, or a limit above the hard ceiling | Raise the specific flag deliberately, or split the work |
| `timeout` | Command exceeded `--timeout` | Raise `--timeout`, use `--skip-true-peak`, or segment the file |
| `approval_required` / `approval_not_granted` / `approval_expired` / `approval_consumed` / `approval_action_mismatch` / `approval_target_mismatch` / `approval_revoked` | Approval missing or not usable for this action and target | Request -> grant -> use once; targets must match exactly |
| `unknown_preset` / `unknown_recipe` / `unknown_operation` / `unknown_category` | Identifier typo | The error lists the valid ids |
| `invalid_recipe` / `invalid_brief` / `invalid_catalog` | Malformed data file | The error names the offending field or file |
| `nothing_to_keep` | `trim-silence` would delete everything | Lower `threshold_db` or drop the op |
| `nothing_to_split` | No silence or no valid cut | Use `--at`, lower `--threshold-db`, lower `--min-segment` |
| `unsafe_gain` | Normalisation would add more than +60 dB | Check the source; apply an explicit `gain` first |
| `missing_asset` / `missing_module` | A packaged file was renamed or removed | Restore the exact filename from the ZIP |
| `empty_delivery` | Manifest run on an empty folder | Point `--input` at the folder that holds the deliverables |

Recovery pattern that matches the catalog's own contract: find the earliest failing layer, keep the
approved work, repair only the responsible component, re-run the linked checks (`analyze`,
`regression`, `verify-manifest`), and record any changed deliverable before sign-off.

---

## 19. Extension and maintenance

- **New preset:** append to `asset-delivery-presets.json`; no code change. Optionally add a recipe
  that names it.
- **New recipe:** append to `asset-processing-recipes.json` using only documented ops; then
  `sh -c 'python3 tools-audio-suite.py recipe --input a.wav --output b.wav --recipe <id> --dry-run'`
  to validate it.
- **New capability or category:** add the entry to `asset-capability-catalog.json` **and** a
  matching playbook in `asset-category-playbooks.json`. The registry validation refuses a catalog
  whose categories and playbooks do not line up one-to-one.
- **Threshold change:** edit `asset-qa-policy.json`. Re-run `selftest`; the calibration checks are
  independent of policy thresholds.
- **New DSP operation:** add a handler to `HANDLERS` and to `NUMERIC_OPS` in
  `tools-audio-suite.py` for the shorthand form, then extend `selftest`.
- **New command:** add `cmd_*` and register it in `COMMANDS`, `HELP_TEXT`, and the parser in
  `build_parser()`; keep the `common.ok(...)` envelope and `common.ToolError` error path so output
  stays machine-readable.
- **Versioning:** bump `VERSION` in `tools-audio-common.py` and the `schema_version` fields of any
  asset you change. `manifest_version` stays `1.0.0` while the shape is unchanged.
- **Invariants to preserve:** one knowledge file only, `tools-`/`asset-` prefixes, no subfolders, no
  `SKILL.md`, relative paths, standard-library-only runtime, atomic writes, approval gating before
  any overwrite or sign-off.
