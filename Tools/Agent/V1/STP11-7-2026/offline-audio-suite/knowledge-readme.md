# Offline Audio Suite — Skill Tool

## Purpose and capability scope

A local-first, reusable component distilled from the attached **Full Offline Audio and Music Production Skill Catalog** (32 categories, 450 proposed skills). This folder implements a dependable deterministic offline spine for project setup, WAV inspection/QC, conservative PCM processing, manifest batches, rights declarations, local packaging/checksums, and storage/runtime planning. It has no network client and only uses Python's standard library.

**Scope honesty:** The source PDF is a planning catalog for future capabilities; it is not executable requirements for 450 actual DAW/model/device operations. This component makes the independent non-proprietary workflows usable now and states limitations rather than pretending neural synthesis/ASR, DAW/plugin control, recording hardware, restoration ML, immersive rendering, codec encoders, legal determinations, or calibrated listening are implemented. Many catalog concepts can be planned or handed off via the project structure and ledger; model- and device-dependent operations need an authorized local runtime/adapter and qualified review. The operations actually implemented are listed in the CLI (`capabilities`).

## Files and execution order

All files are directly at this Skill Tool root; there are no internal folders or `SKILL.md`.

1. `tools-audio.py` — primary command-line interface. It loads the reusable DSP/format layer next to itself, validates inputs, writes structured JSON results, and exits nonzero on invalid input or failed gates.
2. `tools-audio-core.py` — shared standard-library RIFF/WAV parser, analyzer, resampler/channel mapper, and PCM writer used by the CLI.
3. `asset-default-config.json` — actual portable project contract copied into every new scaffold.
4. `asset-batch-manifest.schema.json` — offline schema reference for manifest jobs accepted by `batch`.
5. `knowledge-readme.md` — this usage, coverage, safety, and validation reference.

## Setup and exact commands

Requires Python 3.9 or later; no pip packages, system codecs, service, credentials, or network access. Copy this folder beside the future skill-root `SKILL.md`; run from this folder (or set the working directory to it). Do not rename individual files unless all references are updated.

```sh
python3 tools-audio.py --help
python3 tools-audio.py capabilities
python3 tools-audio.py scaffold --output ../my-audio-project --name "Field Recording"
python3 tools-audio.py tone --output ../my-audio-project/recordings/test-tone.wav --duration 2 --frequency 1000 --rate 48000 --channels 1 --level-dbfs -18
python3 tools-audio.py analyze --input ../my-audio-project/recordings/test-tone.wav --output ../my-audio-project/reports/tone-analysis.json
python3 tools-audio.py qc --input ../my-audio-project/recordings/test-tone.wav --max-peak-dbfs -1 --min-duration 1
```

Use `python` instead of `python3` where that is the local Python 3 executable. The source directory itself is never modified by `process`; outputs are new files unless a user explicitly passes `--overwrite` for an existing output. No command executes arbitrary shell, plugins, macros, models, or scripts.

## Command reference

All successful command results are JSON. For commands with `--output` report options, JSON is written to that path and stdout. Fatal errors emit `{"ok":false,"error":"..."}` to stderr and exit 2. `qc`, `rights-check`, and `batch` also exit 2 when a gate/job fails. `--help` is built in on every command.

- `capabilities`: list implemented handlers and a short scope summary.
- `scaffold --output DIR [--name TEXT]`: create a clean production tree (recordings, takes, edits, sessions, midi, samples, stems, mixes, masters, transcripts, rights, archive, reports) and `project-config.json` copied from `asset-default-config.json`. Refuses to populate a non-empty existing directory.
- `environment [--path DIR] [--output JSON]`: report Python/platform, free bytes, offline/stdlib-only status, and explicit lack of hardware/model/plugin control. The path must exist or have an existing parent.
- `budget --duration SEC [--rate HZ] [--channels N] [--bits 16|24|32] [--copies N] [--margin FRACTION] [--output JSON]`: estimate uncompressed PCM storage. It is arithmetic, not a disk reservation or a model/stem cache forecast.
- `tone --output WAV [--duration SEC] [--frequency HZ] [--rate HZ] [--channels 1|2] [--level-dbfs DB] [--bits 16|24|32]`: deterministic sine test signal. Bounded to 0.01–600 seconds, 20–20000 Hz, 8–192 kHz, and -60 to -0.1 dBFS.
- `analyze --input WAV [--output JSON]`: reports format, rate, channels, frames/duration, bit depth, sample peak, sample RMS, clipped-sample count, near-zero sample fraction, and per-channel DC/peak/RMS. This is not integrated LUFS or true peak.
- `qc --input WAV [--max-peak-dbfs DB] [--min-duration SEC] [--max-duration SEC] [--output JSON]`: checks non-empty content, sample peak threshold, and duration range. A failed check is reported and exits 2. Thresholds cannot establish listening quality, sync, rights, or delivery compliance.
- `process --input WAV --output WAV --operation normalize|gain|trim|convert [parameters] [--overwrite]`: normalize peak to -24..0 dBFS; apply -60..+24 dB gain; trim by start/end seconds; or convert sample rate (linear interpolation), mono/stereo channel mapping and/or PCM depth (16/24/32). Source and destination must differ. `trim` uses `--start-seconds` and optional `--end-seconds`; `normalize` uses `--target-dbfs`; `gain` uses `--gain-db`; `convert` uses `--sample-rate-hz`, `--channels`, `--bit-depth`. Mono-to-stereo duplicates, stereo-to-mono averages; no surround mapping. Linear interpolation is intentionally simple, not a mastering-grade sample-rate converter. Gain above digital full scale clips at output; inspect the resulting report.
- `batch --input MANIFEST.json [--output REPORT.json] [--overwrite]`: process at most 10,000 jobs sequentially. Paths resolve relative to the manifest's directory and are blocked from escaping that directory. Jobs that pass keep their outputs if another job fails; fix failures and rerun. Existing output protection defaults on. JSON jobs use `operation`, `input`, `output`, optional `params`; see `asset-batch-manifest.schema.json`.
- `rights-check --input LEDGER.json [--output REPORT.json]`: require nonempty `assets`, each with `asset`, `rights_status` (`authorized`, `owned`, `public_domain`, `licensed`), `evidence`, explicit `release_approved: true`, and `consent_record` when `consent_required: true`. A declaration check does not validate evidence or grant rights; never treat a passing result as a legal opinion.
- `package --input PROJECT_DIR --output ARCHIVE.zip [--rights-ledger LEDGER.json] [--release --approve-release] [--overwrite]`: create a local ZIP and a SHA-256/size manifest. Symlinks are excluded; unsafe output inside the source directory is blocked. Normal local archival does not publish anything. `--release` is only a local release-readiness gate and requires both an explicit acknowledgement and a passing ledger; it still does not upload, distribute, or authorize legally.
- `verify --input ARCHIVE.zip [--output REPORT.json]`: reject unsafe/duplicate ZIP paths, parse the package manifest, and verify listed file sizes and SHA-256 hashes by bounded streaming (maximum 256 MB per listed file and 2 GB expanded total). Extraction is not performed.

### WAV input/output contract

Reads little-endian RIFF/WAVE PCM 8/16/24/32-bit and IEEE float32/float64 (including standard extensible subtypes), mono or multichannel for measurement; rejects compressed WAV, big-endian RIFX, malformed chunks, nonfinite float data, unsupported channel layouts for conversion, files above 128,000,000 bytes, more than 10,000,000 frames, or more than 8,000,000 decoded sample values. The writer also guards classic RIFF's 32-bit chunk-size ceiling; practical decoded-sample limits are reached far earlier. Process output is uncompressed PCM WAV only. Writing and resizing happen via a same-directory temporary file then atomic replace. Output folders are created as needed.

## Batch manifest example

Save beside audio files as `jobs.json`, change filenames to existing local WAVs, and run from this Skill Tool folder:

```json
{
  "jobs": [
    {"operation": "normalize", "input": "recordings/interview.wav", "output": "masters/interview-peak.wav", "params": {"target_dbfs": -1.0, "bit_depth": 24}},
    {"operation": "trim", "input": "recordings/interview.wav", "output": "edits/interview-opening.wav", "params": {"start_seconds": 0.0, "end_seconds": 12.5, "bit_depth": 24}},
    {"operation": "convert", "input": "recordings/interview.wav", "output": "edits/interview-mono-44k.wav", "params": {"sample_rate_hz": 44100, "channels": 1, "bit_depth": 24}}
  ]
}
```

The output paths above are examples only. Each job is isolated: a malformed job produces an indexed error without discarding successful outputs. Reruns skip nothing automatically; an already-existing destination is safely rejected unless `--overwrite` is deliberately enabled.

## Safety, consent, and approval

- All processing is local. There is no network code or background process.
- Never overwrite original recordings; processing requires distinct input/output. Overwrite needs the explicit flag and is limited to the named output. There is no delete operation.
- Batch paths must stay within the manifest folder; package paths are normalized and symlinks excluded. Inputs are bounded by file/frame and job-count limits; jobs are sequential with no retries or concurrency. Keyboard interruption stops before subsequent jobs; completed files remain valid and should be re-inspected.
- Rights/consent and release are critical human decisions. The ledger gate checks required fields and explicit declarations only. Do not generate/clone a person's voice, publish, or distribute without documented authority, valid rights, consent, and required approvals. This tool has no synthetic voice or publishing capability.
- Tone generation is test audio, not a calibration certificate. Avoid unsafe monitoring levels. Listen and use calibrated meters for acceptance.

## Catalog coverage and limits

The complete named catalog from the PDF is indexed below so the source capability set is preserved. The implemented command families map primarily to session scaffolding and standards, recording/QC measurements, safe WAV format/conversion/editing, batch/reproducibility, basic mastering preparation, rights evidence completeness, project archiving, and acceptance reporting. **Listed capability names do not imply an executable handler.** The following require external/local systems not provided here: OS/driver/device installation and control; microphone/room judgment and monitoring calibration; DAW/plugin/session formats; local licensed voice/ASR/separation/music-generation/singing models and accelerators; advanced denoise/restoration, pitch/time processing, EQ/dynamics/reverb/mixing/spatial render; MIDI/notation; MP3/AAC/FLAC/OGG/Opus codecs; broadcast BWF metadata, integrated loudness/true-peak standards; captions/ASR; hardware/thermal/latency tests; independent legal/licensing evidence review. These are explicitly not claimed as implemented. Offline, local integrations can be added later as separately validated adapters without changing the core contracts.

### Source catalog index: all 32 categories and 450 proposed skills

#### 1. Offline Installation, Dependencies, and Workstation Readiness (14 catalog entries)

1. Air-Gapped Audio Suite Installer
2. Clean Offline Dependency Bundle Builder
3. Clean-State Installation Tester
4. Offline-Only Production Tester
5. Audio Driver Configuration Manager
6. Plugin Compatibility Auditor
7. Model Runtime Dependency Manager
8. Workstation Readiness Auditor
9. Temporary Disk Budget Planner
10. Offline License Activation Planner
11. Environment Baseline Recorder
12. Update Staging and Rollback Manager
13. Multi-OS Deployment Planner
14. Audio Environment Health Monitor

#### 2. Session Architecture, Templates, and File Standards (14 catalog entries)

1. Audio Project Scaffolder
2. DAW Session Template Builder
3. Track Naming Standard Designer
4. Session Naming Validator
5. Sample Rate Standard Manager
6. Bit Depth Standard Manager
7. Channel Layout Standard Manager
8. Session Timebase Manager
9. Project Configuration Author
10. Relative Media Path Enforcer
11. Session Dependency Grapher
12. Show-Wide Audio Settings Synchronizer
13. Legacy Session Migration Planner
14. Session Bootstrap Validator

#### 3. Recording, Interfaces, Microphones, and Monitoring (14 catalog entries)

1. Recording Signal-Flow Planner
2. Microphone Selection Assistant
3. Microphone Placement Planner
4. Gain Staging Operator
5. Audio Interface Configuration Operator
6. Clock and Synchronization Manager
7. Low-Latency Monitoring Builder
8. Redundant Recording Planner
9. Room Noise Surveyor
10. Acoustic Treatment Planner
11. Phase-Coherent Multi-Mic Operator
12. Location Recording Packager
13. Recording Quality Validator
14. Hearing-Safe Monitoring Manager

#### 4. Natural Offline Neural Voice and Speech Synthesis (14 catalog entries)

1. Offline Neural Voice Runtime Manager
2. Natural Voice Casting Assistant
3. Pronunciation Lexicon Builder
4. Voice Performance Direction Builder
5. Multi-Take Voice Generator
6. Dialogue Duration Adapter
7. Neural Voice Consistency Validator
8. Neural Voice Artifact Detector
9. Neural Voice Editing Operator
10. Multilingual Voice Localizer
11. Voice Model Provenance Recorder
12. Voice Consent Gate
13. Synthetic Voice Disclosure Manager
14. Neural Voice Regression Suite

#### 5. Offline Speech Recognition, Diarization, and Transcription (14 catalog entries)

1. Offline Speech-to-Text Runtime Manager
2. ASR Model Router
3. Vocabulary and Hotword Manager
4. Speaker Diarization Runtime Manager
5. Speaker Turn Editor
6. Word Timestamp Aligner
7. Transcript Punctuation and Formatting Operator
8. ASR Transcript Validator
9. Transcript Confidence Calibrator
10. Multi-Pass ASR Reconciler
11. Multilingual Transcript Manager
12. Meeting Transcript Builder
13. Subtitle Transcript Exporter
14. ASR Evaluation Runner

#### 6. Speech Enhancement, Denoising, and Restoration (14 catalog entries)

1. Offline Speech Enhancement Runtime Manager
2. Offline Noise Removal Operator
3. Broadband Denoise Specialist
4. Hum and Buzz Removal Operator
5. De-Click and De-Crackle Operator
6. De-Clip Repair Operator
7. Spectral Repair Specialist
8. Reverberation Reduction Operator
9. Wind and Plosive Repairer
10. Dialogue Isolation Operator
11. Bandwidth Extension Operator
12. Dropout and Gap Repairer
13. Audio Restoration Chain Builder
14. Restoration Quality Validator

#### 7. Source Separation and Stem Extraction (13 catalog entries)

1. Offline Source Separation Runtime Manager
2. Vocal and Accompaniment Separator
3. Music Stem Separator
4. Dialogue-Music-Effects Separator
5. Drum Component Separator
6. Segment-Adaptive Separation Operator
7. Separation Artifact Detector
8. Stem Phase and Sum Validator
9. Stem Cleanup Operator
10. Separation Model Benchmark Runner
11. Source Separation Provenance Recorder
12. Rights-Aware Separation Gate
13. Stem Package Builder

#### 8. Voice Conversion, Singing Voice, and Vocal Production (14 catalog entries)

1. Offline Voice Conversion Runtime Manager
2. Voice Conversion Consent Validator
3. Timbre Conversion Operator
4. Pitch Extraction and Correction Operator
5. Formant and Character Editor
6. Offline Singing Voice Model Manager
7. Singing Phoneme and Lyric Aligner
8. Vocal Performance Synthesizer
9. Vocal Comping Operator
10. Vocal Timing Editor
11. Vocal Tuning Specialist
12. Vocal Layering and Harmony Builder
13. Converted Voice Artifact Detector
14. Vocal Production Validation Gate

#### 9. Offline Music Generation and Assisted Composition (14 catalog entries)

1. Offline Music Generation Runtime Manager
2. Music Generation Brief Compiler
3. Text-to-Music Operator
4. MIDI Generation Operator
5. Continuation and Variation Generator
6. Music Inpainting Operator
7. Structure-Controlled Music Generator
8. Stem-Aware Music Generator
9. Reference Conditioning Manager
10. Generated Music Similarity Reviewer
11. Generated Music Triage
12. Music Model License Auditor
13. Music Generation Provenance Recorder
14. Generated Music Regression Suite

#### 10. Sound-Effect Generation and Sound Design (14 catalog entries)

1. Offline Sound-Effect Generation Runtime Manager
2. Text-to-Sound Operator
3. Impact Sound Designer
4. Whoosh and Transition Designer
5. Creature Sound Designer
6. Interface Sound Designer
7. Vehicle and Machine Sound Designer
8. Ambience Generator
9. Procedural Sound Effect Builder
10. Sound Effect Layering Operator
11. Sound Effect Loop Builder
12. Generated SFX Artifact Detector
13. Sound Effect Rights and Provenance Recorder
14. Sound Design Validation Gate

#### 11. Licensed Instrument, Sample, and MIDI Libraries (14 catalog entries)

1. Licensed Instrument Library Manager
2. Licensed Sample Library Manager
3. MIDI Sound Library Manager
4. Sample Ingest Operator
5. Sample Search Taxonomy Builder
6. Duplicate Sample Detector
7. Instrument Articulation Map Builder
8. Multisample Instrument Builder
9. Sample Loop and Root-Key Validator
10. Library License Auditor
11. Library Portability Packager
12. Library Integrity Scanner
13. Cold-Storage Library Archivist
14. Library Deprecation Manager

#### 12. Plugin Hosting, VST, LV2, CLAP, and Signal Chains (14 catalog entries)

1. VST and LV2 Plugin Host Manager
2. Plugin Inventory Registry
3. Plugin Scan and Quarantine Operator
4. Plugin Preset Library Manager
5. Signal Chain Template Builder
6. Plugin Latency Compensation Validator
7. Plugin State Recall Tester
8. Plugin Automation Validator
9. Plugin Sandboxing Manager
10. Plugin Replacement Planner
11. Plugin Chain Regression Tester
12. Plugin License and Activation Auditor
13. Plugin Performance Profiler
14. Plugin Freeze and Commit Operator

#### 13. Editing, Comping, Alignment, and Time Processing (14 catalog entries)

1. Multitrack Editing Operator
2. Dialogue Comping Operator
3. Music Take Comping Operator
4. Transient Editing Specialist
5. Time Stretching Operator
6. Pitch Shifting Operator
7. Elastic Audio Timing Editor
8. Audio-to-Grid Aligner
9. Phase-Aligned Multitrack Editor
10. Room Tone and Ambience Editor
11. Breath and Mouth Noise Editor
12. Crossfade Quality Validator
13. Edit Decision List Manager
14. Editorial Change Conformer

#### 14. MIDI Editing, Sequencing, and Performance (14 catalog entries)

1. MIDI Editor
2. MIDI Quantization Specialist
3. MIDI Humanization Operator
4. MIDI Cleanup Operator
5. Tempo Map Builder
6. MIDI Groove Extractor
7. Drum MIDI Editor
8. Orchestral MIDI Programmer
9. MPE and Expressive MIDI Manager
10. MIDI Articulation Manager
11. MIDI Transform Builder
12. MIDI Loop and Clip Manager
13. MIDI Export Validator
14. MIDI Performance Regression Tester

#### 15. Composition, Harmony, Arrangement, and Orchestration (14 catalog entries)

1. Composition Brief Interpreter
2. Melody Development Assistant
3. Harmony and Voice-Leading Assistant
4. Rhythm and Groove Designer
5. Song Structure Planner
6. Cue Structure Planner
7. Arrangement Density Manager
8. Orchestration Assistant
9. Counterpoint and Inner-Voice Reviewer
10. Motif and Theme Tracker
11. Transition and Modulation Builder
12. Arrangement Variant Generator
13. Playability Validator
14. Composition Revision Tracker

#### 16. Score Notation, Parts, and Music Engraving (14 catalog entries)

1. Score Notation Engine Manager
2. MIDI-to-Notation Converter
3. Notation Cleanup Operator
4. Instrument Transposition Manager
5. Orchestral Score Engraver
6. Part Extraction Operator
7. Percussion Notation Specialist
8. Guitar and Tablature Engraver
9. Lead Sheet Builder
10. Lyric Underlay Manager
11. Notation Playback Mapper
12. MusicXML Interchange Validator
13. PDF Score Export Validator
14. Score and Parts Approval Gate

#### 17. Equalization and Spectral Processing (13 catalog entries)

1. Professional Equalizer Operator
2. Resonance Detection Specialist
3. Low-Frequency Cleanup Operator
4. Dialogue EQ Specialist
5. Vocal EQ Specialist
6. Instrument EQ Specialist
7. Dynamic EQ Operator
8. Mid-Side EQ Operator
9. Linear-Phase EQ Planner
10. Match EQ Operator
11. Spectral Balance Analyzer
12. EQ Gain-Match Validator
13. EQ Phase and Latency Validator

#### 18. Dynamics, Compression, De-Essing, Gating, and Limiting (14 catalog entries)

1. Compressor Operator
2. Dialogue Compression Specialist
3. Vocal Compression Specialist
4. Bus Compression Operator
5. Parallel Compression Builder
6. Multiband Compression Operator
7. De-Esser Operator
8. Gate and Expander Operator
9. Transient Shaper Operator
10. True-Peak Limiter Operator
11. Limiter Distortion Detector
12. Dynamics Gain-Staging Validator
13. Sidechain Routing Manager
14. Dynamics Regression Tester

#### 19. Reverb, Delay, Modulation, and Spatial Effects (13 catalog entries)

1. Reverb Operator
2. Convolution Reverb Library Manager
3. Dialogue Reverb Matching Specialist
4. Music Reverb Designer
5. Surround Reverb Operator
6. Delay Designer
7. Chorus and Ensemble Operator
8. Flanger and Phaser Operator
9. Creative Modulation Builder
10. Reverb and Delay Automation Manager
11. Effect Tail and Transition Editor
12. Spatial Effect Phase Validator
13. Impulse Response Capture Operator

#### 20. Mixing, Routing, Automation, and Stem Production (14 catalog entries)

1. Mix Architecture Planner
2. Static Mix Builder
3. Dialogue Mix Operator
4. Music Mix Operator
5. Effects and Foley Mix Operator
6. Automation Writing Specialist
7. VCA and Group Control Manager
8. Reference Mix Comparator
9. Mono Compatibility Validator
10. Small-Speaker Translation Tester
11. Stem Exporter
12. Mix Recall Validator
13. Mix Version Manager
14. Mix Approval Gate

#### 21. Immersive, Surround, Binaural, and Spatial Audio (14 catalog entries)

1. Surround Session Architect
2. 5.1 Mix Operator
3. 7.1 Mix Operator
4. Ambisonic Production Operator
5. Binaural Renderer Manager
6. Object-Based Audio Planner
7. Spatial Panning Automation Operator
8. LFE Management Specialist
9. Downmix Compatibility Validator
10. Spatial Metadata Validator
11. Immersive Loudness Validator
12. Speaker Calibration Planner
13. Headphone Spatial Review Workflow
14. Immersive Delivery Package Builder

#### 22. Mastering, Loudness, Metering, and Delivery Control (15 catalog entries)

1. Mastering Session Architect
2. Mastering Equalization Operator
3. Mastering Compression Operator
4. Mastering Saturation and Clipping Operator
5. Mastering Limiter Operator
6. Loudness Meter Manager
7. LUFS Validation Operator
8. True-Peak Meter Operator
9. Phase and Stereo Analyzer
10. Spectrum and Tonal Balance Analyzer
11. Album and Program Sequencing Operator
12. Dither and Noise-Shaping Operator
13. Codec Preview and Damage Analyzer
14. Mastering Quality-Control Gate
15. Master Delivery Set Builder

#### 23. Codec, Format, Conversion, and Metadata (14 catalog entries)

1. WAV Export Operator
2. FLAC Export Operator
3. MP3 Encoding Operator
4. AAC Encoding Operator
5. OGG Vorbis Encoding Operator
6. Opus Encoding Operator
7. Audio Format Router
8. Sample Rate Conversion Operator
9. Bit Depth Conversion Operator
10. Channel Mapping Converter
11. Broadcast Wave Metadata Manager
12. Music Metadata Manager
13. Codec Availability Auditor
14. Round-Trip Codec Validator

#### 24. Audio Quality Control and Automated Validation (16 catalog entries)

1. Audio File Integrity Checker
2. Silence and Dropout Detector
3. Clipping and Over Detector
4. Click and Pop Detector
5. Hum and Noise Detector
6. Phase and Polarity Validator
7. Channel Layout Validator
8. Sample Rate and Bit Depth Validator
9. Duration and Sync Validator
10. Loudness Compliance Validator
11. True-Peak Compliance Validator
12. Spectral Artifact Detector
13. Duplicate Audio Detector
14. File Naming and Package Validator
15. Full-Length Playback Reviewer
16. Automated QC Report Builder

#### 25. Restoration, Archival Audio, and Forensics (14 catalog entries)

1. Archival Audio Ingest Operator
2. Tape Transfer Specialist
3. Vinyl and Disc Transfer Specialist
4. Wow and Flutter Correction Operator
5. Azimuth and Phase Correction Operator
6. Legacy Codec and Container Rescue
7. Forensic Audio Enhancement Operator
8. Audio Authenticity Screening Assistant
9. Preservation Master Builder
10. Access Copy Builder
11. Archival Metadata Registrar
12. Archive Integrity Verifier
13. Long-Term Format Migration Planner
14. Restoration Provenance Recorder

#### 26. Rights, Licensing, Consent, and Provenance (14 catalog entries)

1. Music Rights Manifest Builder
2. Voice Consent System
3. Sample License Auditor
4. Instrument Library License Auditor
5. Plugin and Software License Auditor
6. Performance Release Manager
7. AI Audio Model License Auditor
8. Generated Audio Provenance Recorder
9. Cue Sheet Generator
10. Attribution and Notices Generator
11. Rights Expiration Monitor
12. Third-Party Audio Quarantine
13. Release Rights Gate
14. Rights Evidence Packager

#### 27. Localization, Dubbing, Captions, and Accessibility (14 catalog entries)

1. Localization Source Package Builder
2. Dub Script Adaptation Assistant
3. ADR Cue Sheet Builder
4. Dub Recording Session Manager
5. Dub Dialogue Editor
6. Alternate Language Mix Manager
7. Caption Audio Cue Author
8. Descriptive Transcript Builder
9. Audio Description Production Planner
10. Pronunciation and Terminology Glossary Manager
11. Localized Audio QC Operator
12. Accessibility Track Metadata Manager
13. Accessibility Playback Validator
14. Localization Delivery Packager

#### 28. Podcast, Broadcast, Film, Game, and Interactive Audio (14 catalog entries)

1. Podcast Production Template Builder
2. Podcast Dialogue Editor
3. Broadcast Audio Compliance Manager
4. Film Dialogue-Music-Effects Stem Builder
5. Film Printmaster Operator
6. Game Audio Asset Builder
7. Interactive Music System Designer
8. Game Audio Loudness and Peak Normalizer
9. Audio Middleware Package Builder
10. Runtime Audio Memory Budgeter
11. Adaptive Dialogue System Builder
12. Interactive Audio Regression Tester
13. Trailer and Promo Mix Builder
14. Live Playback and Show-Control Planner

#### 29. Performance, Latency, and Resource Optimization (14 catalog entries)

1. Audio Pipeline Benchmark Designer
2. Round-Trip Latency Measurer
3. Buffer Size Optimizer
4. Plugin CPU Profiler
5. Neural Model Performance Profiler
6. Disk Streaming Optimizer
7. Session Freeze Strategy Planner
8. Voice Count and Polyphony Manager
9. Render Time Estimator
10. Resource Threshold Monitor
11. Low-Latency Neural Processing Planner
12. Thermal and Power Stability Tester
13. Performance Regression Tester
14. Capacity Forecast Planner

#### 30. Automation, Batch Processing, and Reproducibility (14 catalog entries)

1. Audio Batch Processor
2. DAW Scripting Operator
3. Command-Line Audio Pipeline Builder
4. Reproducible Render Script Builder
5. Manifest-Driven Stem Exporter
6. Batch Loudness Normalizer
7. Batch Transcript Generator
8. Batch Separation Orchestrator
9. Batch Neural Voice Generator
10. Job Scheduler for Audio Production
11. Failure Resume Manager
12. Audio Pipeline Smoke Test Builder
13. Automation Safety Sandbox
14. Automation Regression Suite

#### 31. Backup, Packaging, Archival, and Handoff (14 catalog entries)

1. Reproducible Audio Project Packager
2. Session Dependency Collector
3. Bundle Completeness Validator
4. Project Checksum Manifest Builder
5. Audio Project Snapshot Manager
6. Incremental Backup Operator
7. 3-2-1 Backup Planner
8. Sample Library Backup Manager
9. Archive Tier Manager
10. Disaster Recovery Drill Runner
11. Long-Term Session Archive Builder
12. Recipient Handoff Guide Writer
13. Master Delivery Package Builder
14. Project Decommissioning Operator

#### 32. Documentation, Training, Governance, and Final Acceptance (16 catalog entries)

1. Offline Audio Production Handbook Builder
2. Role-Based Quick Start Author
3. Standard Operating Procedure Generator
4. Configuration Reference Generator
5. Troubleshooting Decision Tree Builder
6. Known-Issue Register Manager
7. Reference Audio Project Maintainer
8. Training Exercise Generator
9. Operator Competency Checklist Builder
10. Shift and Team Handoff Writer
11. Audio Standards Registry
12. Skill Interface Standard Author
13. Skill Dependency Graph Manager
14. Skill Catalog Governance Manager
15. Postmortem and Lessons-Learned Manager
16. Final Audio Acceptance Gate

## Validation performed

Validation results below are limited to tests actually run for this delivered version. The suite uses only Python's standard library. Commands were executed from the Skill Tool root and again after copying/extracting into clean roots beside a test `SKILL.md`; no test depends on network access.

- `python3 tools-audio.py --help` and `capabilities` (including JSON file output): passed.
- Scaffold, generated 48 kHz stereo test tones, and parsed both PCM24 and independently constructed IEEE-float32 WAV: passed.
- `analyze` and passing `qc` reports; failing sample-peak QC returns a nonzero code: passed.
- Each `process` operation (`normalize`, `gain`, `trim`, `convert`) produced a readable WAV with expected metadata; a second, differently leveled input produced the expected changed measurement after processing: passed.
- Batch run with one successful and one missing-input job retained the successful output, reported the failed item, and exited nonzero; manifest path traversal was rejected: passed.
- Missing input, malformed WAV, pre-existing destination, invalid rights ledger, and release attempt without explicit approval/evidence were rejected with nonzero status: passed.
- Valid rights declarations passed field validation; project ZIP creation, package-manifest presence, ZIP CRC and SHA-256/size verification passed. Explicit release mode passed only when supplied both `--approve-release` and the passing ledger; unsafe ZIP paths were rejected. Explicit `--overwrite` succeeded and implicit overwrite was rejected. (Legal evidence authenticity is not tested or asserted.)
- Copied the Skill Tool beside a temporary test `SKILL.md` and ran commands from its own directory with relative paths: passed. The final archive is separately extracted and re-run, then structurally inspected before delivery.

These checks validate basic local behavior only; they do not validate audible quality, true peak, delivery loudness, device/DAW/plugin/model operation, legal rights, or platform hosting.

## Troubleshooting and recovery

- `Unsupported WAV encoding` or malformed chunk: transcode a copy with an approved offline tool; retain the original and verify its hash.
- `Output exists`: inspect the destination, choose a new name, or explicitly approve `--overwrite` for that output.
- `batch` failures: use each indexed error, correct the source/manifest, re-run; successful prior output remains on disk.
- `sample_peak_ceiling` failure: reduce gain or normalize to a lower ceiling; this is sample peak only and does not certify true peak.
- `rights-check` failure: obtain and review missing evidence/consent/approval with the responsible rights owner. Do not invent ledger facts.
- Package verification failure: preserve the original archive, rebuild from source in a new path, verify again, and investigate changed/corrupt media.
- Need higher-grade resampling, codec support, loudness/true-peak, neural inference, DAW automation, or hardware tests: use a separately licensed local tool/runtime and add a bounded adapter plus regression tests; do not silently substitute this implementation.

## Extension and maintenance

Keep all component files flat at this Skill Tool root and prefixed `tools-`, `asset-`, or `knowledge-`. Add a new handler only when it can validate inputs, bound resources, produce structured results, preserve sources, document its real dependencies, and be tested offline. Add dependency-free reference fixtures rather than copyrighted audio. Re-run the full validation matrix after changes; update this single readme and any schemas whenever command parameters or behavior changes. The future parent package must have exactly one root `SKILL.md`; this folder itself intentionally has none.
