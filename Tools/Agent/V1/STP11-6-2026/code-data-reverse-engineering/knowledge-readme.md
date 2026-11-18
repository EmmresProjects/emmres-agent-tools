# Code–Data–Reverse-Engineering Skill Tool

## Purpose

`code-data-reverse-engineering` is a reusable, **local and static** technical-artifact analysis component. It turns supplied files or source trees into bounded, machine-readable evidence reports without executing the content, contacting a network service, modifying an input, or exposing detected secret values in normal reports.

The supplied catalog is a broad 30-category proposal rather than one selected, executable skill. This component implements the shared safe inspection, preservation, validation, comparison, source-review, archive-safety, encoding, and reporting layer that can be used across the catalog. It intentionally does **not** claim to implement every platform-specific, dynamic-analysis, decompiler, proprietary-format, decryption, repair, or security-bypass capability in that catalog.

**Primary entry point:** `code-data-reverse-engineering/tools-artifact-inspector.py`  
**Supporting implementation:** `code-data-reverse-engineering/tools-artifact-core.py`

Run commands below from the future skill root: the directory that contains the parent package’s single `SKILL.md` and the `code-data-reverse-engineering/` folder.

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py help
python3 code-data-reverse-engineering/tools-artifact-inspector.py capability-list
```

`help` and `capability-list` are both built-in commands. `capabilities` is an alias for `capability-list`.

## Runtime and dependencies

- **Runtime:** CPython 3.11 or newer. Python 3.11 is required because the implementation uses the standard-library `tomllib` module.
- **Dependencies:** none beyond the Python standard library.
- **Network:** deliberately unavailable to this tool; it makes no HTTP, DNS, package-manager, cloud, or provider calls.
- **External binaries:** none are invoked. This avoids silently depending on `file`, `readelf`, `objdump`, `binwalk`, `7z`, Ghidra, debuggers, or platform-specific SDKs.

Check the runtime with an explicit shell command:

```bash
bash -lc 'python3 --version'
```

## Included files and loading order

| File | Role |
|---|---|
| `code-data-reverse-engineering/tools-artifact-inspector.py` | Primary runnable launcher. It dynamically loads the direct sibling core file by exact filename. |
| `code-data-reverse-engineering/tools-artifact-core.py` | Reusable implementation: CLI, parsers, report writer, safety gates, bounded readers, and operation handlers. It is not meant to be invoked directly. |
| `code-data-reverse-engineering/asset-default-policy.json` | Active default limits, source/build mappings, redacted secret indicators, static risk indicators, and approval policy. Loaded at runtime. |
| `code-data-reverse-engineering/asset-magic-signatures.json` | Active signature catalog used for top-level and embedded-content identification. Loaded at runtime. |
| `code-data-reverse-engineering/knowledge-readme.md` | This complete operational and maintenance guide. |

All package-internal resources are direct siblings in `code-data-reverse-engineering/`; no internal subdirectories, hidden dependencies, or generated state are required.

## Safety model and approvals

The default operating mode is **read-only static inspection**:

- The tool never executes, imports, installs, loads as code, debugs, emulates, instruments, or uploads the inspected artifact.
- It never opens a network connection.
- It refuses symlink input for single-file operations and skips symlinks while walking source/case trees.
- It limits input size, parser reads, scanning, archive recursion, archive entries, decompression output, source files, compare size, output ranges, and wall-clock time.
- It records direct evidence separately from interpretations and limitations in each successful report.
- Potential secrets are reported as redacted indicators; secret values are not placed in normal source/string/decoded-text evidence.
- The program performs no automatic retries. `--retries` must remain `0`, preventing unintended repeat reads or writes.

Explicit approval is required for every write-oriented workflow:

| Action | Required flag | Additional boundary |
|---|---|---|
| Verified case copy | `--approve-copy` | `case-init --output-dir` must name a new directory. |
| Archive extraction | `--approve-extract` | `extract --output-dir` must name a new directory. Unsafe paths, links, special files, duplicate targets, encrypted ZIP entries, oversized entries, and excessive compression ratios are rejected. |
| Saving decoded bytes | `--approve-write` | `decode --decoded-output` must not replace an input. |
| Replacing a report or decoded-output file | `--overwrite --approve-overwrite` together | Both flags are necessary; a report never overwrites by default. |

Case-copy and extraction writes are staged in a temporary sibling directory, fsynced where the platform allows, verified, and atomically renamed only after success. A failed staging directory is removed. Read-only permissions on copied case originals are best effort; the SHA-256 manifest remains the portable integrity record.

## Common global options

Global options may appear before or after the command. Use lower values to tighten the active policy. Values cannot exceed the active policy; use a reviewed custom policy only when a higher cap is appropriate.

```text
--policy PATH                    Conservative JSON override; see Configuration.
--output PATH, -o PATH           Save a report instead of writing it to stdout.
--format json|markdown           Report format; JSON is the default and complete format.
--quiet                          Suppress the one-line report-path completion object.
--timeout SECONDS                Bound one operation; default and upper bound come from policy.
--retries 0                      Required value; automatic retries are intentionally unavailable.
--max-file-bytes BYTES           Lower maximum regular-file size.
--max-read-bytes BYTES           Lower maximum whole-file parser/decoder read.
--max-scan-bytes BYTES           Lower bytes inspected for strings and embedded signatures.
--max-archive-entries COUNT      Lower archive metadata entry limit.
--max-archive-depth COUNT        Lower recursive nested-archive depth.
--max-source-files COUNT         Lower recursive source/case tree file count.
--max-compare-bytes BYTES        Lower limit for positional binary diff ranges.
--overwrite --approve-overwrite  Together allow a report/decoded-output replacement.
```

Examples:

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py \
  --timeout 30 --max-scan-bytes 1048576 triage artifact.bin

python3 code-data-reverse-engineering/tools-artifact-inspector.py \
  inspect archive.zip --recursive-archives --output artifact-report.json --format markdown
```

## Commands and implemented capabilities

### `case-init`: intake, provenance, and verified copies

Creates a new preservation case with copies in `originals/` and a `case-manifest.json` in the **user-selected output directory**. Runtime output directories are intentionally not part of this Skill Tool package.

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py case-init \
  --input artifact.bin --input project-directory \
  --output-dir preservation-case --case-id CASE-001 \
  --source-note 'Received from authorized owner' --approve-copy
```

For each eligible regular file it records its case-relative path, byte count, and SHA-256; it independently hashes the source and copied bytes before publishing the case directory. It skips symlinks and special files in directory input and fails if a limit is exceeded. This supports the catalog’s intake coordination, read-only guardian, evidence/custody, multi-artifact organization, and preservation-planning needs.

### `triage`: unknown file and binary evidence survey

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py \
  --output triage.json triage artifact.bin
```

Triage produces direct evidence including:

- file metadata and full-file MD5, SHA-1, SHA-256, SHA3-256, and BLAKE2b-256 digests;
- top-level signature matches from `code-data-reverse-engineering/asset-magic-signatures.json`;
- bounded entropy samples, with the explicit warning that entropy is not proof of encryption or packing;
- UTF-8/UTF-16/BOM and binary heuristics;
- bounded ASCII, UTF-16LE, and UTF-16BE strings with configured sensitive matches redacted;
- bounded embedded signature locations, not automatic carving;
- defensive structural parsing where recognized: ELF, PE/COFF, Mach-O/universal binaries, WebAssembly, Java class header, DEX header, SQLite header, and `ar` archives.

ELF evidence includes class, endianness, program headers, sections, a bounded symbol sample, and dynamic undefined-symbol candidates. PE evidence includes COFF/optional-header facts, sections, import DLLs, export-name samples, and data directories. Mach-O evidence includes headers, bounded load-command summaries, UUIDs where present, and fat architecture entries. These are static format observations rather than decompilation or behavioral claims.

### `inspect`: combined static inspection

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py \
  inspect artifact-or-archive --recursive-archives --output inspect.json
```

`inspect` always runs triage and then selects independent safe checks:

- recognized ZIP/TAR-family archive: archive inventory, optionally bounded recursive nested inspection;
- recognized source/build/configuration file: a one-file source scan;
- every file: best-effort `validate --kind auto`, with an unavailable/failed validation recorded rather than converted into a successful claim.

It is appropriate for a reproducible initial evidence ledger, not as a substitute for a format- or platform-specific analyst.

### `validate`: supported parser and integrity checks

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py validate configuration.json --kind json
python3 code-data-reverse-engineering/tools-artifact-inspector.py validate database.sqlite --kind sqlite
python3 code-data-reverse-engineering/tools-artifact-inspector.py validate package.tar.gz --kind tar
python3 code-data-reverse-engineering/tools-artifact-inspector.py validate module.wasm --kind wasm
```

Supported `--kind` values are `auto`, `json`, `toml`, `ini`, `xml`, `sqlite`, `zip`, `tar`, `gzip`, `bzip2`, `xz`, `elf`, `pe`, `macho`, `wasm`, `java-class`, `dex`, `ar`, and `text`.

| Kind | Actual validation behavior |
|---|---|
| JSON | Parses with duplicate-key recording; later values are not silently treated as unique. |
| TOML / INI | Strict standard-library parsing and top-level/section summary. |
| XML | Parses only XML without `DOCTYPE` or `ENTITY` declarations; declarations are rejected to avoid entity-expansion/external-entity risk. |
| SQLite | Reads the header and opens with SQLite URI `mode=ro`, enables `query_only`, performs `PRAGMA quick_check`, and lists up to 1,000 schema objects. |
| ZIP / TAR | Opens archive metadata and applies entry, declared expansion, duplicate, link, path, encryption, and ratio checks. |
| GZIP / BZIP2 / XZ | Bounded streaming decompression with SHA-256 of decompressed bytes; no decoded file is written. |
| ELF / PE / Mach-O / Wasm / Java / DEX / ar | Bounded static header/table range parsing; malformed offsets and sizes are rejected. |
| Text | Bounded readability and encoding heuristic only; this is not a grammar validator. |

`auto` uses signatures and a few conservative suffix hints. Specify `--kind` when a mislabeled or embedded format must be tested deliberately.

### `archive-list`: archive safety and nested container inventory

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py \
  archive-list package.zip --recursive --output archive-inventory.json
```

Native inventory supports ZIP and TAR-family files, including compressed TAR when Python’s standard library recognizes it. Per entry it reports normalized path, sizes, type, compression details where available, and hazards. It flags absolute/traversal path components, duplicate normalized paths, links, special entries, ZIP encryption, and compression ratios above policy. `--recursive` opens only recognized nested ZIP/TAR data under the byte and depth limits; it does not extract the outer archive to disk.

The tool deliberately does not invoke third-party RAR, 7-Zip, CAB, ISO, WIM, DMG, filesystem-image, APK-rebuild, or proprietary resource handlers.

### `extract`: approval-gated safe extraction

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py extract package.zip \
  --output-dir extracted-package --approve-extract --output extraction-report.json
```

Extraction supports ZIP and TAR-family input. The destination directory must not exist. The tool refuses unsafe archive paths, ZIP symlinks/encryption, TAR links/devices/FIFOs, duplicate target paths, unacceptable entry counts, excessive declared/unpacked bytes, and excessive ZIP compression ratio. It writes an `extraction-manifest.json` beside extracted content with size and SHA-256 evidence.

It does **not** repack, sign, publish, install, or launch archives. Those actions remain intentionally outside this safe static component.

### `compare`: byte-level integrity comparison

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py \
  compare prior.bin revised.bin --output comparison.json
```

The command computes full-file multi-hashes for both inputs. If both files are at or below `max_compare_bytes`, it also reports bounded exact `replace`, `insert`, and `delete` ranges using positional byte sequence comparison. If either file is larger, full hashes still establish equality/inequality but positional ranges are intentionally omitted rather than partially guessed.

### `source-scan`: polyglot static code/build review

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py \
  source-scan source-tree --output source-report.json
```

Recognized extensions include C/C++, Objective-C, C#, Java, Kotlin, Swift, Rust, Go, Python, JavaScript, TypeScript, shell, PowerShell, CMD/batch, SQL, PHP, Ruby, Perl, Lua, R, Scala, Dart, Vue, assembly, YAML, JSON, TOML, INI, XML, Gradle, Terraform, and HCL. Recognized build/configuration filenames include `Makefile`, `CMakeLists.txt`, `Dockerfile`, `package.json`, `pyproject.toml`, `pom.xml`, Gradle files, `Cargo.toml`, `go.mod`, CI YAML names, and related common names configured in `code-data-reverse-engineering/asset-default-policy.json`.

For each bounded text file it emits language, encoding heuristic, import/dependency candidates, definition candidates, and configured static findings. The built-in findings flag indicators such as unsafe C-family string APIs, shell-execution APIs, Python/JavaScript dynamic evaluation, common unsafe deserialization APIs, weak-hash references, disabled TLS verification, dynamic-SQL patterns, and likely secret material. Secret values are redacted. Import and definition mapping are regular-expression heuristics—not compiler, call-graph, data-flow, or vulnerability proofs.

Use `--include-hidden` only when hidden project files are in authorized scope.

### `decode`: common encoding validation and controlled output

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py \
  decode encoded.txt --encoding base64 --output decode-report.json

python3 code-data-reverse-engineering/tools-artifact-inspector.py \
  decode encoded.txt --encoding base64 --decoded-output decoded.bin --approve-write
```

Supported encodings are `auto`, `base16`, `base32`, `base64`, `base85`, `ascii85`, `percent`, `utf8`, `utf16le`, and `utf16be`. Reports include source/decoded byte counts, decoded hashes, encoding heuristic, hex preview, and redacted text preview. Full decoded bytes are only written after explicit approval.

This command does not guess XOR keys, decrypt material, recover credentials, or bypass protection.

### `regex-check`: conservative regular-expression review

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py \
  regex-check --pattern '(a+)+$' --output regex-report.json
```

This compiles a Python regular expression (up to 4,096 characters) without matching it against a subject. It identifies conservative nested-quantifier, quantified-alternation, backreference, and stacked-wildcard indicators. An indicator is an advisory for human review, not a proof of catastrophic behavior on a particular engine or input.

## Report contracts

Every successful JSON report has this stable envelope:

```json
{
  "tool": "code-data-reverse-engineering",
  "tool_version": "1.0.0",
  "operation": "triage",
  "status": "ok",
  "created_utc": "UTC ISO-8601 timestamp",
  "safety": {},
  "limits": {},
  "evidence": {},
  "findings": [],
  "interpretations": [],
  "limitations": []
}
```

- `evidence` contains directly observed metadata, hashes, parser output, bounded inventories, or validation output.
- `findings` contains configured static indicators. A finding is never silently upgraded into a proof of exploitability, a verified secret, malware, provenance, or authorship.
- `interpretations` describes how evidence should be read.
- `limitations` names intentionally unavailable behavior or bounded coverage.

Operational errors return JSON with `status: "error"`, a stable `error.code`, human-readable `error.message`, and structured `error.details`. Typical codes include `missing_input`, `invalid_input`, `input_too_large`, `malformed_structure`, `invalid_json`, `invalid_archive`, `archive_limit_exceeded`, `unsafe_archive_path`, `approval_required`, `output_exists`, `timeout`, and `canceled`. The successful report schema is not emitted after an operation failure.

Markdown output is a human-readable rendering of the same envelope. JSON is the complete automation format.

## Configuration

The active defaults live in `code-data-reverse-engineering/asset-default-policy.json`. It controls byte/count/time limits, source extension/name mappings, secret-redaction indicators, static-risk rules, and approval declarations. The magic catalog lives in `code-data-reverse-engineering/asset-magic-signatures.json`.

A custom policy is optional and intentionally restricted to these top-level fields:

```json
{
  "defaults": {
    "max_file_bytes": 67108864,
    "timeout_seconds": 120
  },
  "source_extensions": {
    ".custom": "custom-language"
  },
  "source_filenames": {
    "custombuild": "custom-build"
  }
}
```

Run it with:

```bash
python3 code-data-reverse-engineering/tools-artifact-inspector.py \
  --policy reviewed-policy.json triage artifact.bin
```

Only numeric defaults and source mappings can be overridden. Secret and risk regular expressions remain package-controlled, so a custom policy cannot introduce arbitrary expensive regular expressions into the scanner. Do not modify the bundled asset filenames without also updating the sibling loader/reference code and rerunning validation.

## Limits, cancellation, recovery, and maintenance

Default caps are visible in every report and in `code-data-reverse-engineering/asset-default-policy.json`. Important defaults include a 64 MiB input-file cap, 16 MiB whole-file parser read cap, 8 MiB strings/signature scan cap, 512 MiB declared/decompressed archive cap, 10,000 archive entries, depth 3 nested archive inventory, 2,000 source/case files, 8 MiB exact positional comparison limit, and a 120-second operation timeout.

- Send `SIGINT` or `SIGTERM` to request cancellation. Long loops check the cancellation flag and return a structured `canceled` error when control returns to the main loop.
- A timeout produces structured `timeout` output. Reduce file/scan/archive limits or inspect a smaller sample before raising a reviewed policy cap.
- Writes use temporary siblings plus cleanup. If a process is forcibly killed by the operating system, inspect for a dot-prefixed staging directory beside the requested output path before retrying; it is safe to remove only after confirming it belongs to the failed run.
- No retry strategy is applicable: there are no provider/network calls, and repeating a local malformed input would not repair it.
- If an output file already exists, choose a new path or explicitly supply both overwrite approval flags. The tool never overwrites an input path.
- For a malformed artifact, preserve the original with `case-init`, run `triage`, use `validate --kind` for the expected grammar/container, and retain the returned parser failure as evidence. Do not use this tool to repair bytes automatically.

When maintaining the component, keep resources as direct sibling files, update exact case-sensitive references, run the validation commands below, and verify a clean future skill root. Add a format parser only when it has bounds checks, resource caps, tests with valid and malformed fixtures, and a documented report contract.

## Known limitations and unavailable integrations

- No dynamic execution, debugger, emulator, device, ADB, Frida, tracer, memory-dump runtime, sandbox, network capture, TLS handshake, cloud, service reconstruction, signing, certificate-chain/revocation, publish, install, financial, credential, or privileged action is implemented.
- No disassembly, decompilation, control-flow recovery, calling-convention recovery, source translation, source patching, build reconstruction, compiler invocation, code signing, cryptographic key recovery, license bypass, anti-analysis bypass, exploit development, malware execution, or encryption/XOR key guessing is implemented.
- PE and Mach-O reports are structural—not full Authenticode, Code Signing, relocation, resource, PDB, dyld, or ABI compatibility validators. ELF reports are structural and bounded—not a replacement for a linker, debugger, or disassembler.
- The archive path validator supports ZIP and TAR-family input only. It does not support safe extraction/repackaging for RAR, 7-Zip, CAB, ISO, DMG, APK/AAB rebuilding, proprietary game packages, filesystem images, firmware filesystems, or nested formats beyond bounded ZIP/TAR inventory.
- YAML is recognized for source scanning but not grammar validation because PyYAML is intentionally not bundled. Protocol Buffers, CBOR, MessagePack, BSON, FlatBuffers, ASN.1, registry hives, OLE streams, mobile package resource tables, firmware partition/filesystem formats, multimedia codecs, VM disks, container image layers, and model formats are only identified when a magic signature applies; they are not parsed deeply.
- Hashes demonstrate byte identity, not trusted origin, authorship, safety, or certificate trust. MD5/SHA-1 are reported for compatibility/fingerprinting only and must not be treated as modern security assurances.
- Hosted resource failures, permissions, catalog access, hardware/device availability, licenses, and external platform failures cannot be solved by this package. They must be reported as hosting, catalog, permission, platform, or external-tool limitations in an investigation record.

## Validation completed before delivery

The following tests were actually run against temporary fixtures outside the package, using the primary entry point and the standard library runtime:

1. Ran `help` and `capability-list`; inspected the command/help text and the complete capability JSON.
2. Compiled both Python files with `python3 -m py_compile`; parsed both bundled JSON assets with `python3 -m json.tool`.
3. Triaged a copied real ELF executable; confirmed ELF signature, structural section parsing, and dynamic undefined-symbol evidence.
4. Validated a well-formed JSON fixture; validated a deliberately malformed JSON fixture and confirmed structured `invalid_json` output.
5. Created a SQLite fixture, ran read-only SQLite validation, and confirmed `PRAGMA quick_check` returned `ok`.
6. Created and validated a GZIP fixture; confirmed bounded decompression byte count and output hash reporting.
7. Created a ZIP containing a nested ZIP; ran recursive `archive-list` and confirmed the nested archive inventory. Created a TAR.GZ fixture; listed and extracted it successfully.
8. Created an archive with `../escape.txt`; confirmed `extract --approve-extract` returned structured `unsafe_archive_path`, did not publish the destination directory, and cleaned staging output. Confirmed extraction without `--approve-extract` returned `approval_required`.
9. Scanned a small Python/build tree; confirmed import edges, an `eval` static-risk finding, and a generic secret assignment finding with the value redacted.
10. Compared changed byte fixtures and confirmed a positional `replace` range. Re-triaged a JSON fixture after adding one byte and confirmed both its size and SHA-256 changed. Base64-decoded a fixture, wrote it only with `--approve-write`, and compared its bytes to the expected plaintext.
11. Ran `regex-check` on `(a+)+$` and confirmed a nested-quantifier advisory without matching a subject string.
12. Confirmed missing input and missing approval paths return structured `missing_input` and `approval_required` errors. Created an approved case copy and verified the case manifest’s hashes and copied read-only mode.
13. Rendered a Markdown capability report and verified that global options work before or after the command.
14. Copied the final folder beside a test parent `SKILL.md` in a clean temporary skill root, ran the primary entry point with only relative paths, extracted the final ZIP to a second clean location, and reran the primary entry point there.

No claim is made for a test not listed above. The package has **5 files** and contains no `SKILL.md`; its future parent skill supplies the single root `SKILL.md`.
