# powerful-extractor-compressor — Skill Tool knowledge

`knowledge-readme.md` is the single documentation file for this Skill Tool. It
covers purpose, commands, schemas, safety rules, recipes, limits, troubleshooting
and the validation that was actually performed.

Folder name: `powerful-extractor-compressor/`
Primary entry point: `powerful-extractor-compressor/tools-pec.py`
Version: 1.0.0 · Runtime: Python 3.9+ standard library (optional extras listed below)

---

## 1. Purpose

A unified, production-oriented Skill Tool for **safe archive extraction,
compression, organization, verification, recovery and packaging**. It implements
capabilities harvested from the *Powerful Extractor / Compressor Skill Catalog*
(18 categories, 144 proposed skills) as one coherent tool with 14 commands.
`coverage` maps every one of the 144 catalog skills to either a command
(107 rows: 95 full, 12 with a documented reduction) or an explicit out-of-scope
reason (37 rows). See §4.

Design commitments enforced in code:

| Commitment | Where it lives |
| --- | --- |
| Unique job id, immutable source fingerprint, machine-readable result | `tools-pec-runtime.py` (`Job`, `compute_fingerprint`), envelope on every command |
| Idempotent-by-default, never silently overwrite | `policy.overwrite = never` default, `ApprovalGate` for `replace` |
| Bounded work: cancel, deadline, retries, checkpoints, partial success | `Cancellation`, `with_retries`, `Checkpoint`, `status: partial` |
| Untrusted archives never executed; path/link/bomb/nesting/count defense | `normalize_entry_path`, `safe_join`, `preflight`, ratio/size/count checks |
| Created archives are format-tested and round-trip verified | `create --verify`, `verify --round-trip` |
| Provenance for every output file | `pec-manifest.json` + `lineage` block + `CUSTODY.json` in evidence recipes |
| Failures name the smallest affected unit; verified work is kept | per-entry error records, checkpoint resume, `--resume` |
| Publish is staged then atomically exposed | `extraction.publish = atomic` (default) |

---

## 2. Files, roles and load order

Everything sits directly in `powerful-extractor-compressor/`; there are no
subfolders. Only `tools-pec.py` is executed directly — the rest are loaded from
that folder by absolute file path (hyphenated filenames cannot be imported).

| Order | File | Role |
| --- | --- | --- |
| — | `tools-pec.py` | **Primary entry point.** CLI parsing, all 13 commands, job envelope, exit codes. Loads the four modules below. |
| 1 | `tools-pec-runtime.py` | Shared runtime: `PecError`, exit codes, `Config` (merge + validation), `Job`, JSON-lines `Logger`, `Cancellation`, `with_retries`, `ApprovalGate`, size/duration parsing, atomic JSON helpers. Imported by every other module. |
| 2 | `tools-pec-formats.py` | Format adapters: magic-byte detection, normalized `Entry` model, listing and streaming for ZIP/ZIP64, TAR (any compression), 7z, RAR, gzip/bzip2/xz/zstd/lz4 streams, archive creation, AES-256 ZIP creation, multi-volume collection/diagnosis/assembly, native integrity tests. |
| 3 | `tools-pec-core.py` | Engine: path normalization and traversal defense, preflight, `ExtractionJob` (staged/direct, checkpoints, nesting, organize, metadata strip, manifest, atomic publish), verification (`verify_manifest`, `round_trip_check`, hashing), secret/PII scanning, file-type mismatch detection, metadata sanitation. |
| 4 | `tools-pec-packager.py` | The 11 packaging recipes, recipe discovery/validation, staged build + manifest + index + atomic publication. |
| 5 | `tools-pec-selftest.py` | The 29-case end-to-end battery behind `selftest`. |
| asset | `asset-catalog-coverage.json` | All 144 catalog skills with status (`implemented` / `partial` scope / `out-of-scope`), the owning command and a note. Read by `coverage`. |
| asset | `asset-default-config.json` | Bundled default configuration (merged under any user config). |
| asset | `asset-recipes.json` | Declarative recipe definitions: title, handler, description, option defaults. |
| asset | `asset-content-types.json` | Extension → category map used by `organize`, `media`, and content-type audits. |
| asset | `asset-secret-patterns.json` | 16 detection rules (credentials, keys, tokens, PII) with severity/confidence. |
| asset | `asset-handoff-template.md` | Template rendered into `HANDOFF.md` by the `project-handoff` recipe. |

Load order at runtime is runtime → formats → core → packager → selftest; each
module is registered in `sys.modules` under a stable alias (`pec_runtime`,
`pec_formats`, `pec_core`, `pec_packager`, `pec_selftest`) so repeated imports
are cheap and identity is shared.

This folder contains **no `SKILL.md`** and no subfolders, so it can be copied
directly beside a skill's root `SKILL.md`. Path depth is 2 levels
(`powerful-extractor-compressor/<file>`), inside the 3-level limit.

---

## 3. Setup

### 3.1 Required

* Python 3.9 or newer (validated on CPython 3.13.16, Linux).
* Standard library only for ZIP/ZIP64, TAR (+gzip/bzip2/xz), gzip, bzip2, xz,
  SHA-256/512/BLAKE2, JSON manifests, checkpoints and all safety logic.

### 3.2 Optional backends (install only what you need)

```sh
# 7z read/write (also enables 7z listing, extraction, testing)
python3 -m pip install "py7zr>=0.21"

# Zstandard streams (read + write)
python3 -m pip install "zstandard>=0.22"

# RAR read (still needs a system unrar/rar binary for actual decompression)
python3 -m pip install "rarfile>=4.1"

# LZ4 frames
python3 -m pip install "lz4>=4.3"

# AES-256 encrypted ZIP creation and reading
python3 -m pip install "pyzipper>=0.3.6"

# Optional fast hashing for verify --hash-alg blake3
python3 -m pip install "blake3>=0.4"
```

External binaries are optional and are only used through a fixed argv list
(never a shell): `7z`/`7zz`/`7za` (or `backends.seven_zip` in config) and
`unrar` (or `backends.unrar`). Check what is available:

```sh
python3 tools-pec.py config backends
```

Formats without a backend fail with exit **6** and a message naming the missing
package — they are never reported as working.

### 3.3 Run it

Always invoke from a shell with an explicit interpreter:

```sh
# bash / zsh
python3 powerful-extractor-compressor/tools-pec.py --help
python3 powerful-extractor-compressor/tools-pec.py caps

# PowerShell (Windows)
py powerful-extractor-compressor/tools-pec.py caps
```

`--help`, `-h`, `caps`, `caps --json`, `package --list` and `package --show`
all work without touching any input data.

---

## 4. Capability list and catalog mapping

`python3 tools-pec.py caps` prints this list; `caps --json` returns it as data;
`caps --filter scan` searches it.

| Command | What it does | Catalog skills covered |
| --- | --- | --- |
| `detect` | Real container type from magic bytes and structure; exposes extension mismatches and self-extracting payload offsets | Format Signature Detector, File-Type Mismatch Detector |
| `inspect` | Preflight: counts, expanded size, ratio, encryption, volumes, capacity, blocking checks, largest entries | Preflight Archive Inspector, Cost and Resource Estimator, Decompression Bomb Detector, Encrypted Archive Detector, Missing Volume Diagnoser |
| `list` | Entry listing with glob/regex/extension/size/date/index/count filters | Single-Item Precision Extractor, Selective Pattern Extractor, Range and Portion Extractor |
| `extract` | Streamed extraction, traversal + link + bomb defense, checkpoints/resume, nested unwrapping, content-type organization, metadata strip, atomic publish, per-file manifest | 21 catalog skills including Universal Archive Extractor, Zip-Slip Defense Guard, Checkpointed Extraction Runner, Nested Archive Unwrapper, Split-Volume Archive Assembler |
| `create` | Deterministic ZIP/ZIP64/TAR/TAR.GZ/TAR.BZ2/TAR.XZ/7z and gzip/bz2/xz/zstd streams, levels, stored-media heuristics, AES-256 ZIP, volume splitting, `--verify` | Universal Archive Builder, Single-Bundle Compressor, Multi-Volume Compressor, Encrypted Archive Creator, Removable Media Splitter, Compression Profile Selector |
| `verify` | SHA-256/512/BLAKE2/SHA-1/MD5 hashing, expected-digest match, native archive test, manifest audit, directory inventory, round-trip re-hash | Checksum Verification Engine, Archive Test Runner, Extraction Completeness Auditor, Round-Trip Compression Verifier, Chain-of-Custody Recorder, Partial Success Reporter |
| `organize` | Content-type sorting, collision-safe renames, junk removal, timestamp epoch normalization | Content-Type Sorter, Collision-Safe Renamer, Archive-to-Folder Organizer, Folder Structure Rebuilder, Manifest and Inventory Builder |
| `scan` | Secret/PII pattern scan, disguised-type detection, metadata sanitation, policy gate | Secret and Sensitive-Data Scanner, File-Type Mismatch Detector, Metadata Sanitizer, Policy-Aware Include Exclude Engine |
| `package` | 11 declarative packaging recipes with manifests | Source Release, Project Handoff, Evidence Collection, Dataset Delivery, Media Delivery, Update Patch, Redacted Sharing, Compliance Evidence, Retention Policy, Separate-Zip, Single-Bundle, License/Notice collection |
| `batch` | Many archives, bounded workers, per-job logs, duplicate-work skip, partial summary | Multi-Archive Batch Extractor, Parallel Extraction Scheduler, Power Extraction Orchestrator, Duplicate-Work Preventer, Failure Reproduction Packager |
| `jobs` | Job records, `resume` from stored parameters, `clean` behind approval | Resumable Job Controller, Temporary-State Recovery Manager, Job Summary Report Generator, Retry Policy Engine, Archive Disposal Workflow, Secure Deletion Coordinator |
| `config` | Show/validate/init configuration, backend routing, filesystem capability profile | Configuration Validator, Tool Capability Router, Filesystem Capability Profiler, Adaptive Resource Governor |
| `coverage` | Maps all 144 catalog skills to commands, marking full, partial and out-of-scope rows with reasons | Provenance and Version Registry, Tool Capability Router (reporting half) |
| `selftest` | 29-case end-to-end battery (safety, integrity, recovery, packaging, validation) | Security Regression Test Suite, Boundary and Stress Test Runner, Golden Manifest Regression Checker, Clean-State Reproducibility Tester, Performance Regression Benchmark |

Catalog coverage summary (`python3 tools-pec.py coverage`):

```
Catalog coverage: 95 implemented, 12 partial, 37 out of scope (of 144 catalog skills)
```

The 37 out-of-scope rows are almost entirely network/cloud/distributed/observer
capabilities (object storage, SMB/NFS, cloud drives, HTTP APIs, webhooks,
distributed workers, dashboard publishers, directory watchers) plus parsers this
Skill Tool deliberately does not ship (mailbox files, disk images, installer
payloads, document text). Every such row is listed with its reason in
`asset-catalog-coverage.json` and in `coverage --state-filter out-of-scope`, so
nothing is silently claimed.

Deliberate scope decisions (no fake features):

* **Executable/SFX handling** is inspection only. `detect` reports the embedded
  payload offset and refuses to run the file. There is no “extract from EXE”
  action; use an approved external tool after reviewing the report.
* **Network, object storage, SMB/NFS, cloud-drive, distributed worker, webhook,
  and HTTP API** capabilities from catalog categories 12 and 14 are **not
  implemented**. This Skill Tool is local-first and never opens a socket. See §11.
* **Malware scanning** is not bundled (no engine, no signatures). `scan` covers
  secrets/PII and disguised file types; route `scan --json` output to your own
  engine if you need AV verdicts.
* **Disk images (ISO/DMG/WIM/CAB)** are detected and reported, but not mounted
  or extracted: that needs OS-level tooling the standard library does not offer.

---

## 5. Commands in depth

### Global options (before or after the command)

```
--config PATH        JSON config merged over bundled defaults
--state DIR          state dir for jobs/logs/checkpoints/tmp (default .pec-state)
--json               one JSON envelope on stdout (progress goes to stderr)
--quiet              suppress progress/notice output
--approve ACTION     grant a gated action (OVERWRITE, DELETE, DISPOSE, DECRYPT, PUBLISH-ENCRYPTED)
--approval-file P    JSON approval records with approved_by/expires
--dry-run            plan only, write nothing
--deadline SECONDS   wall-clock budget (accepts 30, 45s, 10m, 2h)
```

### 5.0 `coverage`

```sh
python3 tools-pec.py coverage                      # full list, grouped by catalog category
python3 tools-pec.py coverage --state-filter partial
python3 tools-pec.py coverage --verbose --json
```

Reads `asset-catalog-coverage.json` (144 rows) and prints, per catalog skill,
`implemented`, `partial` (with the reduction) or `out-of-scope` (with the reason)
plus the command that owns it. Exit 0 always; it is a reporting command.

### 5.1 `detect`

```sh
python3 tools-pec.py detect payload.zip photo.jpg maybe.7z
```

Prints a table of detected id, kind, size and whether the extension matches.
Exit 0 when everything is recognized, 2 when a path is missing or a file is
unrecognized (errors are reported per path), 5 when detection worked but some
files are unknown. JSON shape: `{"detections": [...], "errors": [...], "count": n}`.

Detection covers: ZIP/ZIP64, TAR (ustar/GNU/checksum-verified), 7z, RAR4/RAR5,
gzip, bzip2, xz, zstd, lz4, lzip, CAB, WIM, ar/.deb, ISO9660/UDF, DMG (UDIF),
SQLite, PDF, OLE2, PE/ELF executables. Disguised ZIP-family files (`.docx`,
`.jar`, `.epub`, …) are marked `extension_match: true` via a family list.

### 5.2 `inspect`

```sh
python3 tools-pec.py inspect archive.7z --json
python3 tools-pec.py inspect archive.zip --password-file ./pw.txt
```

Checks performed: entry count, expanded size, expansion ratio, largest entry,
nesting budget, volume completeness, traversal/absolute-path entries, platform
junk, free disk space vs. estimated need. Any failed check lands in `blocking`
and the command exits 3, so `inspect` is a safe gate before `extract`.

### 5.3 `list`

```sh
python3 tools-pec.py list archive.zip                       # names only
python3 tools-pec.py list archive.zip --long                # + size, date, mode, link targets
python3 tools-pec.py list archive.zip --include '*.csv' --since 2024-01-01
python3 tools-pec.py list archive.zip --regex '^docs/.*\.md$' --limit 20
python3 tools-pec.py list archive.zip --index-range 3:9 --json
```

Filters accepted anywhere a selection is supported: `--include GLOB` (repeatable),
`--exclude GLOB`, `--regex RE`, `--ext .csv` (repeatable), `--min-size`/`--max-size`
(`10K`, `4MiB`, `2G`), `--since`/`--until` (`YYYY-MM-DD`), `--index-range LOW:HIGH`
(1-based inclusive), `--limit N`.

### 5.4 `extract`

```sh
# basic, atomic publish (default)
python3 tools-pec.py extract payload.zip -o out/

# selective + sanitized
python3 tools-pec.py extract payload.zip -o out/ --ext .csv --ext .xlsx --strip-metadata

# untrusted content: quarantine permissions, no secret scan, strict limits
python3 tools-pec.py --config strict.json extract payload.zip -o /srv/quarantine/inbox --quarantine --publish direct

# nested unwrapping (max 5 levels by default)
python3 tools-pec.py extract bundle.zip -o out/ --nest --max-depth 3

# resume an interrupted job with the same key
python3 tools-pec.py extract big.zip -o out/ --job-key nightly-1 --resume

# many archives into one destination, per-archive folders otherwise
python3 tools-pec.py extract a.zip b.zip -o out/
python3 tools-pec.py extract a.zip b.zip -o out/ --merge
```

Behaviour: entries are streamed (never fully buffered), each file is hashed while
written, checkpoints are appended per verified entry, the manifest is written
last, and the staging directory is published with a single atomic rename
(`--publish direct` writes in place instead, which is what checkpoint resume
uses). Existing destinations follow `--overwrite never|suffix|replace`; `replace`
requires `--approve OVERWRITE`.

Per-archive result fields: `status` (`completed` | `partial` | `failed`),
`entries_total/selected/written/skipped/failed`, `bytes_written`,
`throughput_mbps`, `manifest`, `warnings`, `skips`, `renames`, `errors`,
`scan_findings`, `nested`, `fingerprint`.

Exit codes: 0 complete, 5 partial (some entries failed), 4 failed (nothing
written — e.g. encryption), 3 policy/preflight refusal, 7 approval needed, 9
cancelled.

### 5.5 `create`

```sh
python3 tools-pec.py create src/ -o release.zip --root-folder project --verify
python3 tools-pec.py create src/ -o release.tar.xz --format tar.xz --level 9
python3 tools-pec.py create data.csv -o data.csv.zst --format zstd --level 8
python3 tools-pec.py create src/ -o media.zip --volume-size 2G --level 0
python3 tools-pec.py create src/ -o private.zip --encrypt --password-file pw.txt --approve PUBLISH-ENCRYPTED
```

* Formats: `zip`, `zip64`, `tar`, `tar.gz`, `tgz`, `tar.bz2`, `tar.xz`, `7z`
  (needs `py7zr`), `gzip`, `bz2`, `xz`, `zstd` (needs `zstandard`).
* Deterministic by default: zeroed timestamps, stable ordering, fixed modes —
  two runs over the same input produce the same SHA-256 (validated).
* `--volume-size SIZE` splits the finished archive into `.001`, `.002`, … files;
  the first volume holds the readable head and is the anchor path for extraction.
* `--encrypt` writes AES-256 ZIP via `pyzipper` (never legacy ZipCrypto) and is
  gated behind `--approve PUBLISH-ENCRYPTED` because it publishes protected data.
* `--verify` runs the native test and a round-trip re-hash before returning 0.

### 5.6 `verify`

```sh
python3 tools-pec.py verify release.zip --test --round-trip
python3 tools-pec.py verify release.zip --expect <sha256>
python3 tools-pec.py verify --manifest out/pec-manifest.json --dir out/
python3 tools-pec.py verify out/ --write-manifest inventory.json
python3 tools-pec.py verify file.bin --hash-alg blake2b
```

* `--test` uses the format-native tester (zipfile CRC pass, TAR read pass, 7z/unrar when present).
* `--round-trip` extracts into an isolated scratch directory, re-hashes every
  file, re-reads every entry from the archive, compares both against the fresh
  manifest, then deletes the scratch directory.
* `--manifest` audits a tree against a manifest: matched, missing, mismatched
  (with expected/actual digests) and unexpected files. Any mismatch exits 4.
* `--write-manifest` produces a `pec.manifest/1` inventory (`kind: inventory`)
  with `path`, `size`, `sha256` per file.

### 5.7 `organize`

```sh
python3 tools-pec.py organize out/ --strip-metadata --epoch 2020-01-01
python3 tools-pec.py organize out/ -o sorted/ --collision hash
```

Moves files into category folders from `asset-content-types.json` (documents,
images, audio, video, subtitles, code, data, executables, fonts, archives,
certificates, unknown), keeps `pec-manifest*` at the root, resolves collisions
with `~N` suffixes or an 8-character content hash, and can strip junk files and
normalize mtimes. Files at the top level of a flat archive are not reorganized
(only files inside subfolders are moved), so single-file archives stay put.

### 5.8 `scan`

```sh
python3 tools-pec.py scan out/ --gate block            # exit 3 when findings exist
python3 tools-pec.py scan out/ --min-confidence high --json
python3 tools-pec.py scan out/ --no-secrets --strip-metadata --epoch 2019-01-01
python3 tools-pec.py scan photo.jpg                    # mismatch check for single files
```

Rules live in `asset-secret-patterns.json` (AWS keys, GitHub/Slack/Google/OpenAI
tokens, PEM blocks, JWTs, generic password assignments, connection strings, auth
headers, card numbers, SSNs, emails, phone numbers, Azure keys). Matches are
always redacted in output; no rule performs an online check. Findings are capped
by `scanner.max_findings` (default 500) and filtered by
`scanner.min_confidence`.

### 5.9 `package`

```sh
python3 tools-pec.py package --list
python3 tools-pec.py package --show source-release
python3 tools-pec.py package source-release src/ -o dist/ --set version=v2.0
python3 tools-pec.py package update-patch base/ target/ -o dist/patch --approve OVERWRITE
python3 tools-pec.py package evidence case_files/ -o dist/ --set case_reference=INC-42
python3 tools-pec.py package dataset data/ -o dist/dataset --set volume_size=512MiB
```

Every recipe validates options first (unknown keys are rejected with exit 2),
stages its output, writes `pec-package-manifest.json` plus a human
`pec-package-index.md`, hashes the artifacts, then publishes. Recipe defaults
live in `asset-recipes.json` — the CLI, `--show` and this document all read the
same file.

| Recipe | Options (defaults) | Produces |
| --- | --- | --- |
| `single-bundle` | `format=zip`, `level=6`, `root_folder=null`, `name=null` | one archive + manifest |
| `separate-zip` | `group_by=top-level` (`extension`, `size-class`), `format=zip`, `level=6` | one archive per group |
| `source-release` | `version=v1`, `format=tar.gz`, `level=6`, `deterministic=true`, `round_trip=true` | release archive, secrets/generated files excluded, round-trip checked |
| `project-handoff` | `project`, `owner`, `format=zip`, `root_folder=handoff` | handoff archive with rendered `HANDOFF.md` |
| `evidence` | `case_reference`, `collector`, `read_only=true`, `archive=true` | read-only collection + `CUSTODY.json` |
| `dataset` | `volume_size=0`, `include`, `exclude` | `volumes/volume-NNN/` + per-volume manifests + `RECONSTRUCTION.md` |
| `media` | `deliverable`, `archive=false`, `level=0` | category folders, byte-for-byte copies |
| `update-patch` | two sources: base, target | changed/added files + `ROLLBACK.json` |
| `redacted` | `label`, `archive=true`, `exclude` | tree or ZIP + `OMITTED.md` + manifest of omissions |
| `compliance-evidence` | `evidence_files=[...]`, `policy_reference`, `reviewer` | evidence bundle + `EVIDENCE-SUMMARY.json` |
| `retention-labels` | `retention_days=365`, `classification=internal`, `legal_hold=false` | `RETENTION.json` labels |

### 5.10 `batch`

```sh
python3 tools-pec.py batch incoming/ -o extracted/ --workers 4 --nest --skip-duplicates
python3 tools-pec.py batch 'downloads/*.zip' -o out/ --fail-fast
```

Expands directories/globs, keeps only files whose content is a supported
archive, then extracts with a bounded thread pool (`--workers`, capped by
`limits.max_workers` and 16). Each archive gets its own job id, log and
destination folder. `--skip-duplicates` consults `state/jobs/index.jsonl` and
skips fingerprints that already completed. Output: per-archive status plus
`succeeded`/`failed` counts; exit 5 for partial, 4 when nothing succeeded.

### 5.11 `jobs`

```sh
python3 tools-pec.py jobs list --limit 10
python3 tools-pec.py jobs show --job-id 20261106T031718Z-a919c9ad
python3 tools-pec.py jobs resume --job-id 20261106T031718Z-a919c9ad
python3 tools-pec.py jobs clean --older-than 30 --dry-run
python3 tools-pec.py jobs clean --older-than 30 --approve DELETE
```

`resume` rebuilds the extract parameters stored in the job record and continues
with `--resume` semantics (direct publish, checkpoints reused). `clean` is
destructive and always requires approval.

### 5.12 `config`

```sh
python3 tools-pec.py config show
python3 tools-pec.py config validate
python3 tools-pec.py config init -o my-config.json
python3 tools-pec.py config backends
python3 tools-pec.py config fs --path /srv/output
python3 tools-pec.py coverage --state-filter out-of-scope
```

`config fs` probes atomic rename, case sensitivity, sparse support, free space
and path length limits, and reports them so limits can be chosen realistically.

### 5.13 `selftest`

```sh
python3 tools-pec.py selftest
python3 tools-pec.py selftest --work /tmp/pec-tests --keep
python3 tools-pec.py selftest --only zip-slip --only bomb
```

---

## 6. Configuration

Merge order (later wins): built-in defaults → `asset-default-config.json` →
`$PEC_CONFIG` → `--config PATH` → `$PEC_STATE_DIR` / `--state`.

State directory (`state_dir`, default `.pec-state`) holds:

```
<job_id>.jsonl logs · jobs/<job_id>.json records · jobs/index.jsonl
checkpoints/<key>.jsonl · tmp/<job_id>-stage/
```

Key settings (full list in `asset-default-config.json`):

| Key | Default | Meaning |
| --- | --- | --- |
| `limits.max_entries` | 200000 | entries accepted from one archive |
| `limits.max_total_bytes` | 20 GiB | expanded bytes allowed per job |
| `limits.max_entry_bytes` | 8 GiB | single-entry ceiling |
| `limits.max_ratio` | 200 | expanded/compressed ratio ceiling |
| `limits.max_depth` | 5 | nested archive recursion limit |
| `limits.max_path_length` | 255 | normalized output path limit |
| `limits.max_seconds` | 3600 | per-job budget (also `--deadline`) |
| `limits.io_chunk_bytes` | 1 MiB | streaming chunk |
| `limits.max_workers` | 4 | batch parallelism |
| `limits.retries` / `retry_backoff_seconds` | 2 / 0.5 | transient retry policy |
| `policy.overwrite` | `never` | `never` / `suffix` / `replace` (replace gated) |
| `policy.allow_symlinks` / `allow_hardlinks` | false | links must stay inside the root even when enabled |
| `policy.allow_absolute_paths` | false | never enable for untrusted input |
| `policy.strip_executable_bits` | true | extracted files get mode 0644 |
| `policy.quarantine_permissions` | `0500` | mode used with `--quarantine` |
| `policy.protected_roots` | `/etc`, `/usr`, `/bin`, … | destinations inside these are refused |
| `policy.allowed_output_roots` | `[]` | when set, destinations must be inside one |
| `policy.requires_approval` | overwrite, delete, decrypt, publish-encrypted, dispose | gated actions |
| `policy.secret_gate` | `warn` | `block` turns findings into exit 3 |
| `policy.metadata_junk` | `.DS_Store`, `Thumbs.db`, `__MACOSX/`, … | junk for strip/sanitize |
| `compression.store_extensions` | media/archive extensions | stored, not re-compressed |
| `extraction.publish` | `atomic` | `atomic` or `direct` |
| `extraction.organize` | `none` | `by-content-type` |
| `extraction.hash_algorithm` | `sha256` | manifest/inventory digest |
| `extraction.default_password_env` | `PEC_ARCHIVE_PASSWORD` | password source when no flag is given |
| `scanner.*` | see asset | scanning on/off, caps, text extensions |
| `backends.*` | auto | explicit 7z/unrar paths, module names |

`config validate` checks types, ranges, enum choices and cross-field
consistency (`max_entry_bytes <= max_total_bytes`, sane volume size) and exits 2
with a per-field problem list.

---

## 7. Output schemas

All are JSON; every job writes `jobs/<job_id>.json` with a `pec.result/1`
envelope and appends to `jobs/index.jsonl`.

### 7.1 Result envelope (`pec.result/1`)

```json
{
  "schema": "pec.result/1",
  "skill": "powerful-extractor-compressor",
  "version": "1.0.0",
  "job_id": "20261106T031718Z-a919c9ad",
  "command": "extract",
  "status": "completed",
  "exit_code": 0,
  "fingerprint": "sha256:...",
  "started_at": "2026-11-06T03:17:18Z",
  "finished_at": "2026-11-06T03:17:18Z",
  "duration_seconds": 0.003,
  "params": {"paths": ["payload.zip"], "output": "out/"},
  "counts": {},
  "warnings": [],
  "errors": [],
  "artifacts": ["out/pec-manifest.json"],
  "result": { "...command payload..." },
  "state_dir": ".pec-state",
  "log_path": ".pec-state/logs/<job_id>.jsonl"
}
```

### 7.2 Extraction manifest (`pec.manifest/1`) — written inside every extraction

```json
{
  "schema": "pec.manifest/1",
  "job_id": "...", "created_at": "...", "status": "completed",
  "source": "/abs/archive.zip",
  "source_fingerprint": "sha256:...",
  "container_type": "zip",
  "destination": "/abs/out",
  "counts": {"entries": 5, "skipped": 0, "failed": 0, "bytes": 50073},
  "preflight": {"entries": 6, "expanded_bytes": 50073, "ratio": 0.999},
  "entries": [
    {"path": "docs/readme.txt", "source_entry": "docs/readme.txt", "size": 12,
     "sha256": "...", "mtime": 1762408560.0, "mode": "0o644", "method": 8,
     "container": "payload.zip", "depth": 0, "parent": null}
  ],
  "skips": [], "errors": [], "warnings": [], "scan_findings": [],
  "lineage": {"schema": "pec.lineage/1", "depth": 0, "parent_entry": null,
              "chain": ["payload.zip"]}
}
```

A `pec-manifest.md` human summary sits next to it.

### 7.3 Checkpoint (`pec.checkpoint/1`) — JSON lines, one per verified entry

```json
{"ts":"...","name":"docs/readme.txt","path":"/stage/docs/readme.txt","size":12,
 "sha256":"...","rel":"docs/readme.txt","schema":"pec.checkpoint/1"}
```

### 7.4 Inventory (`pec.manifest/1`, `kind: inventory`) — `verify --write-manifest`

```json
{"schema":"pec.manifest/1","kind":"inventory","created_at":"...","root":"/abs/dir",
 "count":8,"bytes":123456,"entries":[{"path":"a.csv","size":23,"sha256":"..."}]}
```

### 7.5 Package manifest — every recipe

```json
{"schema":"pec.manifest/1","kind":"package-manifest","recipe":"source-release",
 "job_id":"...","created_at":"...","destination":"/abs/dist",
 "sources":["/abs/src"],"source_fingerprint":"sha256:...",
 "counts":{"files":2,"bytes":17},"entries":[{"rel":"LICENSE","size":12,"sha256":"..."}],
 "notes":["archive sha256 ...","round-trip check: ok"],"warnings":[],
 "artifacts":[{"path":"src-v2.0.tar.gz","bytes":212,"sha256":"..."}],
 "release":{"version":"v2.0","excluded_rules":["..."],"archive":{...}}}
```

Recipe-specific blocks (`release`, `handoff`, `evidence`, `dataset`, `media`,
`patch`, `redaction`, `compliance`, `retention`, `groups`) are added alongside.

---

## 8. Safety and approval rules

1. **No execution.** Archive content is never run. PE/ELF payloads are reported,
   not launched. Self-extracting archives are reported with the payload offset.
2. **Path defense.** `..` segments, absolute paths, drive/UNC prefixes and NUL
   bytes are refused before any write (exit 3). Windows device names are
   rewritten with a leading `_`; invalid characters are replaced; long components
   are truncated with a hash suffix so collisions cannot silently merge.
3. **Link defense.** Symlinks/hardlinks are skipped unless explicitly allowed and
   their resolved target stays inside the extraction root.
4. **Bomb defense.** Entry count, expanded bytes, per-entry bytes and ratio are
   checked in `inspect`/preflight and re-checked while streaming (`max_entry_bytes`),
   so a lying header still cannot fill the disk.
5. **Destination defense.** `policy.protected_roots` and
   `policy.allowed_output_roots` are enforced; nothing is written outside them.
6. **No silent overwrite.** Default is `never`; `suffix` renames; `replace`
   requires `--approve OVERWRITE` (or an approval-file record).
7. **Gated actions.** `overwrite`, `delete`, `dispose`, `decrypt`,
   `publish-encrypted` are refused with exit 7 until approved. Approvals carry
   `approved_by` and optional `expires`.
8. **Secrets.** Passwords come from `--password-env`, `--password-file` or
   `PEC_ARCHIVE_PASSWORD`; they are never written to logs, manifests or the job
   record. Scan output always redacts matched values.
9. **Quarantine.** `--quarantine` applies restrictive permissions (0500 by
   default) and `--publish direct` can be combined to keep content inside an
   isolated root.
10. **Publishing.** Outputs are staged, validated and atomically renamed; a
    failed or cancelled job never leaves a half-published tree. `--dry-run` never
    writes, and `jobs clean` always requires approval.

---

## 9. Validation and tests actually performed

Environment: Linux sandbox, CPython 3.13.16, no `7z`/`unrar`/`zstd`/`lz4`
binaries; `py7zr 1.1.4`, `zstandard 0.25.0`, `pyzipper 0.4.0` were installed to
exercise those adapters. `rarfile` and `lz4` were **not** installed, so RAR and
LZ4 paths report exit 6 (verified).

`python3 tools-pec.py selftest` → **29/29 passed**. Cases and the concrete
assertions they make:

| Case | Assertion actually checked |
| --- | --- |
| `detect` | ZIP/TAR identified by content; a ZIP renamed to `.txt` is still detected as ZIP and flagged `extension_match: false` |
| `list-select` | 3 entries listed; `*.csv` glob and `docs/.*\.md$` regex select exactly the expected entries |
| `extract` | 3 entries written, manifest is `pec.manifest/1`, `verify_manifest` reports ok |
| `extract-select` | extension filter writes exactly 1 file |
| `zip-slip` | `../../escaped.txt` and `/absolute.txt` blocked, 2 failures recorded, nothing written outside the root, error kind `traversal` |
| `bomb` | with `max_ratio=5`, a 2 MiB/2 KiB archive is refused by `inspect` with a blocking `expansion-ratio` check |
| `create` | ZIP created, two runs produce the identical SHA-256, native test ok, entry names stable |
| `create-formats` | `tar`, `tar.gz`, `tar.xz`, `gzip` all created and pass their native tests |
| `targz` | `.tar.gz` opens as TAR (not as a raw gzip stream) and extracts to the expected path |
| `round-trip` | extracted files re-hash to the manifest digests, every archive entry is re-read, scratch dir removed |
| `tamper` | appending one byte makes `verify_manifest` report a mismatch (not ok) |
| `secrets` | an AWS key written during extraction is found by rule `aws-access-key` |
| `organize` | categories `documents` + `images` created, no spurious `audio` |
| `nested` | `inner.zip` inside `outer.zip` is unwrapped, `<name>__unpacked` tree contains `docs/readme.txt`, lineage recorded |
| `volumes` | 300 KB archive split into ≥4 volumes, family detected complete, reassembled bytes hash-equal to the source, native test passes |
| `volume-gap` | a set missing `.003` reports `missing: [3]` and `complete: false` |
| `checkpoint` | second run with the same `--job-key` writes 0 entries and skips 3 |
| `cancel` | a pre-requested cancellation raises exit 9 |
| `approval` | unapproved `overwrite` raises 7; an approval file with `approved_by` grants it |
| `overwrite` | `suffix` records renames, `never` records blocked entries |
| `recipe-release` | release archive excludes `.env` (and other secret patterns), produces a sha256 note |
| `recipe-patch` | `update-patch` marks modified/added files and ships `ROLLBACK.json`; `evidence` ships `CUSTODY.json` |
| `recipe-handoff` | `HANDOFF.md` rendered into the archive with project/owner/size substitutions |
| `recipe-options` | unknown `--set` key is rejected with exit 2 |
| `config` | invalid ranges, invalid enum choices and cross-field inconsistency are reported per field |
| `parsing` | `4MiB`, `10K`, `2048`, `90`, `2m` parse exactly; `banana` is rejected |
| `metadata` | `.DS_Store` removed and mtimes normalized to the requested epoch |
| `multi-zip` | `separate-zip` groups by extension (`txt` group exists) |
| `mismatch` | a ZIP named `photo.jpg` is reported as a disguised type |

CLI-level checks run against real files (all commands):

* `--help`, `caps`, `caps --json`, `caps --filter`, `caps --backends`,
  `package --list`, `package --show` — exit 0, correct text/JSON.
* `detect` with a missing path → exit 2, per-path error, other paths still reported.
* `extract` of an archive containing `../../escape.txt` → exit 5, `failed: 1`,
  escape file never created.
* `extract` twice with `--overwrite suffix` → `readme~2.txt` etc. created.
* `verify --manifest` on a tampered tree → mismatches listed, exit 4;
  `--json` emits one envelope with `status: failed`.
* `create --verify` on ZIP, TAR.XZ, 7z → native test + round trip ok;
  `create --encrypt` without approval → exit 7; with approval + password file →
  AES-256 ZIP, `inspect` reports 5 encrypted entries, extraction with the
  password works, without it exits 4 with per-entry encryption errors.
* `create --volume-size` on a 200 KB source → 4 volumes; deleting `.002` makes
  `inspect` report `missing: [2, 3]` and `extract` refuse with exit 4.
* `batch` over 3 archives + one decoy text file → 3/3 extracted, decoy ignored.
* `jobs list/show/resume/clean` — resume reuses checkpoints (0 written, 5
  skipped) in the same destination; `clean` without approval → exit 7, with an
  approval file → removes state entries.
* `config init` then `config validate --config` → valid, with the source chain
  `builtin, asset-default-config.json, <file>`.
* `scan --gate block` on a tree containing an AWS key → exit 3 with redacted hits.
* Missing/omitted backends (`7z` creation before `py7zr`, `zstd` creation before
  `zstandard`, `inspect` of a fake `.7z`) → exit 6 with an installation hint and
  `safe_to_extract: false`.
* Deadline/cancel: a requested cancellation exits 9; an unreadable/corrupt
  archive is refused at preflight with `safe_to_extract: false`.
* `organize`, `--dry-run` for `create`, `extract`, `scan`, `jobs clean` — no
  files written while planning.

Not tested (and therefore not claimed): RAR extraction, LZ4 streams, CAB/ISO/
WIM/DMG content extraction, encryption other than AES-256 ZIP, Windows-specific
paths (code paths exist and fail closed, but no Windows run was performed).

---

## 10. Limits and known constraints

* **Local only.** No network, object storage, SMB/NFS, webhook or HTTP API.
* **Single-stream codecs** (`.gz`, `.bz2`, `.xz`, `.zst`, `.lz4` that are not
  TAR) expose one synthetic entry named from the file; nesting refuses them with
  a clear warning instead of guessing. `detect` labels TAR-inside-compression
  containers (`*.tar.gz`, `*.tgz`, `*.tar.xz`, `*.tbz2`, `*.tzst`) with
  `"container": "tar"`, and `list`/`extract` open them as TAR automatically.
* **True spanned ZIPs** (`.z01` + `.zip`) are read by `zipfile` directly; byte
  chunks (`.001`, `.002`) are concatenated after validation. Concatenating a
  true spanned set is refused with a message to use `zip -s 0`.
* **RAR** needs both `rarfile` and an `unrar`/`rar` binary; `rarfile` alone
  cannot decompress. **LZ4** needs the `lz4` package. **BLAKE3** needs `blake3`.
* **`--encrypt`** writes ZIP AES-256 only (pyzipper). 7z/TAR encryption is not
  implemented.
* **Metadata fidelity**: times and Unix modes are preserved; ACLs, xattrs,
  ADS/resource forks and Windows attributes are not carried across filesystems.
* **Bomb detection** is heuristic (declared sizes plus streaming byte counters);
  a crafted archive whose header understates real content is stopped by the
  per-entry byte cap, not by the ratio check alone.
* **Concurrency** is a bounded thread pool; there are no distributed workers,
  leases or heartbeats.
* **`jobs resume`** replays stored parameters; options added after the original
  run are not retrofitted.
* **Malware scanning** is out of scope (no engine). Secrets/PII scanning is
  pattern based and will produce false positives/negatives; treat `--gate block`
  as a tripwire, not a compliance proof.

---

## 11. Troubleshooting and recovery

| Symptom | Cause | Fix |
| --- | --- | --- |
| exit 6, “7z backend unavailable” | no `py7zr` and no `7z` binary | `python3 -m pip install py7zr`, or set `backends.seven_zip` |
| exit 6, “RAR backend unavailable” | `rarfile` missing, or no `unrar` binary | install both; RAR cannot be decompressed by pure Python |
| exit 3, “entry escapes the extraction root” | traversal entry | expected; the entry is reported in `errors` and skipped |
| exit 3, “destination … outside policy.allowed_output_roots” | allowlist configured | extract inside an allowed root or update the config |
| exit 7, approval required | gated action | review, then `--approve OVERWRITE`/`DELETE`/… or `--approval-file` |
| exit 9 | cancelled or `--deadline` exceeded | re-run with `--resume --job-key KEY` (or `jobs resume`) |
| exit 4 on extraction | nothing could be written (e.g. encryption without a password) | supply `--password-env`/`--password-file` |
| `status: partial` | some entries failed | inspect `errors[]`; verified entries are kept and checkpointed |
| interrupted job | process killed | `jobs list` → `jobs resume --job-id ID`, or `--resume --job-key KEY` |
| destination existed | atomic publish refuses to merge silently | use `--overwrite suffix`, or `--approve OVERWRITE` with `--overwrite replace` |
| stale state | old jobs/logs/checkpoints | `jobs clean --older-than 30 --dry-run` then `--approve DELETE` |
| want to inspect failures | need fixtures | `selftest --work DIR --keep`, then read `state/logs/*.jsonl` |

Recovery model: entries are checkpointed only after their hash is written, so a
resume never trusts a half-written file; `verify --manifest` is the authoritative
“is this output complete” check, and `jobs/<id>.json` retains parameters for
replay. `PEC_DEBUG=1` adds a traceback to internal errors.

---

## 12. Extension and maintenance

* **New format**: add a signature to `SIGNATURES` in `tools-pec-formats.py`, an
  entry reader in `iter_entry_bytes`, and (if creatable) a spec in
  `CREATE_FORMATS`. Nothing else needs to change; `detect`, `inspect`, `list`,
  `extract`, `verify` pick it up automatically.
* **New filter**: extend `Selector` in `tools-pec-core.py` and expose a flag in
  `build_parser()` + `build_selector()`.
* **New packaging scenario**: add `handle_*` in `tools-pec-packager.py`, register
  it in `HANDLERS`, then declare it in `asset-recipes.json` (title, handler,
  options with defaults). `package --list/--show` and the CLI validate it
  automatically.
* **New scan rule**: append to `patterns` in `asset-secret-patterns.json` with
  `id`, `label`, `regex`, `severity`, `confidence`, `enabled`. Test with a
  self-test case or a scratch file.
* **New content category**: extend `asset-content-types.json`; `organize`,
  `media` and the classification audit follow it.
* **Versioning**: bump `VERSION` in `tools-pec-runtime.py`; it flows into every
  envelope, manifest and log line. Keep manifests backward-compatible; they are
  already versioned by `schema`.
* **Regression safety**: run `tools-pec.py selftest` after any change; it covers
  the safety envelope, so a regression in traversal/bomb/approval/checkpoint
  handling fails loudly.

---

## 13. Quick reference

```sh
# preflight then extract safely
python3 tools-pec.py inspect payload.zip
python3 tools-pec.py extract payload.zip -o out/ --strip-metadata

# build, split, verify and check round trip
python3 tools-pec.py create src/ -o dist/app.zip --verify
python3 tools-pec.py create src/ -o dist/app.zip --volume-size 2G --level 0
python3 tools-pec.py verify dist/app.zip --test --round-trip

# evidence-grade collection and audit
python3 tools-pec.py package evidence case/ -o dist/ --set case_reference=INC-42
python3 tools-pec.py verify --manifest out/pec-manifest.json --dir out/

# untrusted batch intake
python3 tools-pec.py batch incoming/ -o quarantine/ --quarantine --workers 2
python3 tools-pec.py scan quarantine/ --gate block --min-confidence high

# self-check
python3 tools-pec.py selftest
```

Entry point name (exact, case-sensitive):
`python3 tools-pec.py <command>` from inside `powerful-extractor-compressor/`, or
`python3 powerful-extractor-compressor/tools-pec.py <command>` from a skill root.
