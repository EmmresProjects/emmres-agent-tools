# code-data-reverse-engineering

A unified, reusable **Skill Tool component** for code, data, binary analysis, reverse
engineering, and technical preservation. It implements the full attached catalog
(**30 categories, 260 skills**) behind **51 real commands** in one folder. It is a
workbench, not a Copilot skill: there is no `SKILL.md` here and nothing is auto-executed.

- **Folder / tool name:** `code-data-reverse-engineering`
- **Primary entry point:** `tools-retool.py`
- **Runtime:** Python **3.10+** (developed and tested on **3.13.16**). **Standard library only** — no third-party packages, no network access, no paid services, no embedded secrets.
- **Default posture:** read-only. Inputs are never modified. Writes go only to new, user-specified output paths. Dual-use and destructive operations are gated (see Safety).

---

## Run commands

Run from the directory that **contains** the `code-data-reverse-engineering/` folder
(for example beside a parent root `SKILL.md`). Paths below are relative to that future
skill root, use forward slashes, and are case-sensitive.

POSIX shell (`sh` / `bash` / `zsh`):

```sh
python3 code-data-reverse-engineering/tools-retool.py --help
python3 code-data-reverse-engineering/tools-retool.py capabilities
python3 code-data-reverse-engineering/tools-retool.py selftest
python3 code-data-reverse-engineering/tools-retool.py identify path/to/artifact.bin
```

Windows (`pwsh` / `cmd`):

```pwsh
py -3 code-data-reverse-engineering\tools-retool.py --help
py -3 code-data-reverse-engineering\tools-retool.py capabilities
```

Global flags (before the subcommand):

| Flag | Meaning |
|---|---|
| `--config PATH` | JSON config overriding `asset-config.json` (limits, paths, external tools, output). |
| `--output-format json\|text\|md` | Rendering of the result envelope. Default `json` (structured). |
| `--output PATH` | Write the rendered result to a **new** file (refuses to overwrite without `--approve`). |
| `--version` | Print the tool version. |

Every command returns a structured envelope: `ok`, `tool`, `tool_version`, `command`,
`started_at`, `duration_ms`, `inputs`, `result`, `warnings`, `limitations`, `errors`.
On failure the envelope carries `error.code`, `error.message`, and `error.details`.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Runtime error (including unexpected exceptions, reported structurally) |
| 2 | Usage / validation error (bad input, unknown format, missing required arg) |
| 3 | Approval or purpose required (a gate blocked the action) |
| 4 | A configured limit was exceeded |

---

## Dependencies

**Required:** Python 3.10+ standard library only. Modules used include `argparse`,
`ast`, `hashlib`, `zlib`, `bz2`, `lzma`, `struct`, `sqlite3`, `zipfile`, `tarfile`,
`json`, `csv`, `re`, `pickletools`, `plistlib`, `tomllib` (3.11+; guarded on 3.10),
`subprocess`, `unicodedata`, `difflib`, `pathlib`. **Install nothing.**

**Optional external tools (allowlist, read-only):** the `external` command can probe and
run a fixed allowlist of static inspection tools if they are installed: `file`, `readelf`,
`objdump`, `nm`, `strings`, `unzip`, `tar`, `gzip`, `7z`, `binwalk`, `radare2`, `jadx`.
They are invoked with fixed argument templates, **no shell**, a timeout
(`external_timeout_s`, default 20s), and capped output. They are never required; every
capability also has a built-in implementation. **Dynamic tools (`gdb`, `strace`, `adb`)
are detected for availability but never executed** — the tool emits a bounded runbook
instead (`plan`).

Install commands and supported versions for the optional tools (only if you want the
external adapters; the tool itself needs none):

```sh
# Debian/Ubuntu examples — versions are whatever your distro ships; adapters are version-tolerant.
sudo apt-get install binutils file unzip tar gzip p7zip-full   # readelf/objdump/nm, file, archives
# binwalk, radare2, jadx are optional and distribution-specific; consult your package manager.
```

No dependency file is shipped because the runtime requires none.

---

## Files in this folder

All files sit directly in `code-data-reverse-engineering/` (no subfolders). 20 files total.

### Code (`tools-*`)

| File | Role |
|---|---|
| `tools-retool.py` | **Primary entry point.** CLI, argument parsing, module loading, dispatch, structured error handling, `capabilities`. |
| `tools-core.py` | Shared core: config/policy loading, envelopes, input validation, safe read/write, approval/purpose gates, hashing, entropy, hexdump, endian helpers, case storage, chained custody log. |
| `tools-identify.py` | Identification, entropy mapping, strings, hexdump, encoding detection/repair, Unicode security, codec (hex/base16/32/58/64/85/percent), numbers/varints, XOR key recovery, carving. |
| `tools-formats.py` | ~40 binary/container parsers (ELF/PE/Mach-O/class/DEX/WASM/ZIP/TAR/OCI/GZIP/PNG/JPEG/GIF/RIFF/MP4/PDF/CFB/DTB/MBR/GPT/bootimg/SquashFS/ext/FAT/UEFI/ROMs/disks/minidump/AXML/ARSC/GGUF/safetensors/pickle/SPIR-V/DXBC/sourcemap/WAD/PAK/Unity/RPM/plist), sections, symbols + demangling, structure mining. |
| `tools-disasm.py` | Built-in disassembler subsets for x86, x64, ARM64, RISC-V32/64 (incl. compressed RVC), MIPS32, 6502, EVM; CFG, value tracking, calling-convention/frame heuristics, pseudocode. |
| `tools-data.py` | Structured/serialized data: JSON/TOML/INI/YAML-lint/XML/CSV, DER, protobuf, msgpack, CBOR, BSON, Java-serialization walk (no instantiation), read-only SQLite, regf census, FlatBuffers/Cap'n Proto framing. |
| `tools-code.py` | Source outline (~25 languages), dependency/security/algorithm review, regex explain + ReDoS heuristics, SQL validation/review, build/CI/IaC audits, compiler-error diagnosis, shell command translation, redacted secrets scan, gated triage. |
| `tools-network.py` | Offline capture/protocol analysis: PCAP/PCAPNG, Ethernet/VLAN/raw-IP/SLL, IPv4/IPv6/ARP, TCP/UDP, DNS, TLS ClientHello (SNI/ALPN), HTTP text, WebSocket frames, token/JWT fingerprinting (unverified). |
| `tools-ops.py` | Archives (inspect/validate/extract/repack), compression (detect/validate/roundtrip/benchmark), hashing + CRC presets + fuzzy + CRC reconstruction, diff, duplicates, signature inspection, conservative repair, reversible patch, conversion, deterministic fuzzing, external adapters, spec authoring + safe parser emission. |
| `tools-case.py` | Case workflow: intake, inventory, custody, evidence ledger, authorization, verify, survey, plan (runbooks), report, preserve, timeline, compatibility matrix. |
| `tools-selftest.py` | Built-in regression suite (53 checks) and legal minimal fixture generator for every supported format family. |

### Assets (`asset-*`)

| File | Purpose |
|---|---|
| `asset-capabilities.json` | Registry mapping all **260** catalog skills (ids `"<category>.<n>"`) to real commands, with coverage legend (`implemented`, `implemented-with-limits`, `adapter-and-plan`). Drives `capabilities`. |
| `asset-config.json` | Default configuration (`retool.config.v1`): limits, output, safety, external-tool allowlist, paths. Override with `--config`. |
| `asset-policy.json` | Safety policy (`retool.policy.v1`): principles, gated operations, redaction rules, prohibited actions. |
| `asset-signatures.json` | **182** unique offset/hex magic signatures used by identification and carving. |
| `asset-layouts.json` | **21** real byte layouts (types/endian/count/bitfields) used by `hexdump --layout`, `numbers --spec`, `structs --layout`, and `spec`. |
| `asset-rules.json` | Rule packs: code, secrets, shell, docker, ci, iac, documents, persistence, anti_analysis, packers, crypto_api, suspicious_imports. |
| `asset-report-template.md` | 10-section Markdown report template with `{{placeholder}}` slots used by `report`. |
| `asset-plans.json` | **20** bounded runbook templates (`retool.plans.v1`) used by `plan`; never auto-executed. |

### Knowledge (`knowledge-*`)

| File | Purpose |
|---|---|
| `knowledge-readme.md` | This document: the single knowledge file. |

---

## Command reference (51)

Grouped by area. `PATH` is an input file; outputs are always new paths.

**Meta:** `capabilities` (`--category N`, `--search TERM`, `--skills`), `selftest` (`--verbose`), `fixtures --out DIR`.

**Case & evidence:** `intake PATHS… --case DIR`, `inventory DIR`, `custody --case DIR [--add-event JSON --event KIND --actor NAME]`, `ledger --case DIR [--add KIND TEXT --ref REF]`, `authorize --case DIR --purpose P --statement TEXT`, `verify --case DIR`, `survey PATH [--case DIR]`, `plan --template NAME [--target PATH --outdir DIR --case DIR --out FILE]`, `report --case DIR [--out FILE]`, `preserve --case DIR --out DIR [--zip]`, `timeline [--case DIR --logs … --pcap …]`, `compat`.

**Identification & bytes:** `identify PATHS…`, `entropy PATH [--window N --stride N]`, `strings PATH [--min N --limit N --encoding all|ascii|utf16le|utf16be]`, `hexdump PATH [--offset N --length N --width N --layout NAME]`, `encoding PATH [--repair MODE --out FILE --approve]`, `unicode [PATH] [--text TEXT]`, `codec [--input-file PATH | --text TEXT] [--mode MODE | --encode TARGET] [--layers N --out FILE]`, `numbers PATH [--offset N --types LIST | --varint KIND | --spec NAME] [--count N]`, `xor [PATH] [--text TEXT] [--key HEX --max-keylen N --top N --out FILE]`, `carve PATH [--out DIR]`.

**Formats & code:** `parse PATH [--format auto|elf|pe|…]`, `sections PATH`, `symbols PATH [--demangle]`, `disasm PATH --arch ARCH [--offset N --length N --base-addr N --max-insns N --endian le|big]`, `cfg PATH --arch ARCH [--pseudocode]`, `structs PATH [--layout NAME --offset N --count N] [--pointers] [--stride-scan]`.

**Data:** `data PATH [--format auto|json|toml|ini|yaml|xml|csv|der|protobuf|msgpack|cbor|bson|java-serialization|sqlite|regf|flatbuffers|capnp]`.

**Source & security:** `code PATH [--lang auto|… --mode all|outline|deps|security|algorithm|encoding]`, `regex PATTERN [--test STR…]`, `sql [PATH] [--statement SQL]`, `build [PATH] [--mode auto|make|cmake|docker|compose|ci|shell|terraform|ansible|errors|translate] [--text TEXT --to powershell|cmd]`, `secrets PATHS… --purpose P`, `triage PATH --purpose P`.

**Network:** `network [PATH] [--mode auto|pcap|pcapng|http|tls|dns|websocket|tokens] [--text TEXT]`.

**Operations:** `archive PATH [--mode inspect|validate|extract|repack --out PATH --approve]`, `compress PATH [--mode detect|validate|roundtrip|benchmark]`, `hash PATHS… [--algos LIST --fuzzy --crc-recon HEX --width 16|32]`, `diff A B [--mode bytes|strings|sections|json]`, `duplicates DIR`, `signatures PATH`, `repair PATH --mode MODE [--out FILE --approve]`, `patch PATH --spec FILE [--apply --out FILE --approve]`, `convert PATH --to json|csv|hex|base64 [--out FILE]`, `fuzz PATH [--out DIR --count N --seed N --differential --run-builtins]`, `external [--list | --tool NAME --check | --tool NAME --run PATH]`, `spec --layout NAME [--out FILE --emit-parser FILE]`.

### Worked examples

```sh
T=code-data-reverse-engineering/tools-retool.py
python3 $T capabilities --category 12                 # one catalog category
python3 $T identify firmware.bin                      # signatures + structural probes
python3 $T parse program.elf                          # auto-detect + full parse
python3 $T disasm program.elf --arch x64 --offset 0x1000 --length 64
python3 $T data capture.msgpack                       # decode serialized data
python3 $T network capture.pcap                       # flows, DNS, HTTP, TLS hints
python3 $T code app.py --mode security                # rule-based security review
python3 $T secrets . --purpose defensive              # redacted secret fingerprints
python3 $T hash a.bin b.bin --algos sha256,md5 --fuzzy
python3 $T archive bundle.zip --mode validate
python3 $T intake sample.bin --case ./case1           # hash + immutable copy + custody
python3 $T authorize --case ./case1 --purpose research --statement "Authorized local analysis."
python3 $T survey sample.bin --case ./case1
python3 $T report --case ./case1 --out ./case1/report.md
python3 $T preserve --case ./case1 --out ./pkg --zip
python3 $T selftest                                   # 53-check regression suite
```

---

## Safety model

- **Read-only by default.** Inputs are opened read-only and never modified. Repairs,
  patches, conversions, extractions, and repacks write only to **new** output paths.
- **Approval gate (`--approve`, exit 3).** Writing a repair, applying a binary patch,
  overwriting an existing output, or repacking over an existing path requires `--approve`.
  Dry-run is the default and shows the exact change plan. Patches require a matching
  precondition SHA-256 and produce a `.bak` backup plus a reverse-patch rollback plan.
- **Purpose gate (`--purpose`, exit 3).** Dual-use operations (`triage`, `secrets`) require
  an explicit purpose from `defensive | interoperability | research | preservation`.
  Secret findings are **redacted** (rule id, offset, line, 6-char fingerprint only).
- **No arbitrary execution.** No `shell=True`, no `eval` of artifact content, no network
  access, and analyzed artifacts are **never executed**. Pickle is walked with
  `pickletools` (never unpickled); Java serialization is walked structurally (never
  instantiated). External tools run only from the allowlist with fixed args + timeouts.
- **Dynamic analysis is plan-only.** Debuggers/tracers/device tools are never launched;
  `plan` emits bounded, authorized runbooks for use in an isolated environment.
- **Prohibited** (enforced by policy and design): weaponization/exploit development,
  circumventing access controls/licensing/DRM, bypassing anti-debug/integrity checks,
  executing untrusted artifacts, network scanning/remote interaction, printing
  unredacted credentials.

## Limits, timeouts, retries, cancellation, cleanup, recovery

All limits are enforced before and during work (defaults in `asset-config.json`,
overridable via `--config`): `max_file_bytes` 256 MiB, `max_read_bytes` 64 MiB,
`max_strings` 5000, `max_archive_entries` 20000, `max_extract_bytes` 256 MiB,
`max_extract_ratio` 200 (zip-bomb guard), `max_nesting_depth` 4,
`max_disasm_instructions` 4000, `max_cfg_instructions` 20000, `max_json_depth` 200,
`max_sql_rows` 1000, `max_inventory_files` 20000, `max_xor_key_len` 24,
`max_fuzz_cases` 200, `max_carve_hits` 500, `command_timeout_s` 30,
`external_timeout_s` 20, `retries` 1, `retry_backoff_s` 0.25.

- **Timeouts/retries:** external tool calls are bounded by `external_timeout_s` with
  `retries`/`retry_backoff_s`; parser recursion is depth- and node-capped.
- **Cancellation:** `Ctrl-C` produces a structured `cancelled` envelope (exit 1).
- **Cleanup/recovery:** archives are path-sanitized (no traversal/absolute members) and
  size/ratio-checked before extraction; repairs are conservative and only accepted if the
  result re-parses; conversions perform a reopen check; patches are reversible; cases are
  re-verifiable from a preserved copy (`verify` re-hashes copies and checks the custody
  chain). Exceeding a limit returns exit 4 with a structured `limit-exceeded` error.

## Configuration

Copy `asset-config.json`, edit limits/paths/external tools/output, and pass
`--config my-config.json`. Providers/models/prompts are not applicable (no LLM or network
calls); the configurable surface is limits, output format, storage paths, the external-tool
allowlist, and safety gates. Policy lives in `asset-policy.json`.

---

## Limitations (honest, by design)

- **Disassembly is a built-in subset**, not a full decoder: common integer/branch/load-store
  forms per ISA; x86/x64 cover the frequent opcodes (no AVX-512/EVEX completeness), ARM64
  covers the common A64 subset, RISC-V covers RV32/64I + common C (RVC) forms, MIPS32 and
  6502 cover the base sets, EVM covers the opcode map incl. PUSH0/TLOAD/TSTORE/MCOPY.
  Complex/unknown encodings are reported as `.byte/.half/.word`, not guessed. Cross-checked
  against `objdump` where available.
- **No dynamic execution / emulation / debugging.** Those are plan-only.
- **No TCP stream reassembly**; PCAP analysis is per-packet with flow/DNS/HTTP/TLS-hint
  extraction. TLS reads the ClientHello (SNI/ALPN/ciphers); **no decryption**.
- **JWT/tokens are decoded without verification**; API keys are fingerprinted, never printed.
- **Signature inspection does not verify trust chains** (PE Authenticode PKCS#7 structure,
  Mach-O CodeDirectory/entitlements, APK Sig Block v2/v3, JAR digests recomputed, DER/PEM
  structure). Cryptographic validity is reported, not asserted as trusted.
- **Compression:** built-in gzip/bzip2/xz/zlib; zstd/lz4/brotli/lzma-alone are detect-only
  (need external libs). Byte-identical recompression is not guaranteed across encoder
  versions; content round-trip is the meaningful check.
- **YAML** is lint + a flat-subset mapping (no full nested round-trip). **protobuf** is
  schema-less (field numbers/wire types, inferred names). **SQLite** is opened read-only
  (`mode=ro&immutable=1`); schema/stats by default, not full row dumps.
- **Demangling** covers common Itanium nested-name/primitive forms; complex templates skipped.
- Some formats are **framing/identification only** where full semantics need external
  tooling (e.g., FlatBuffers/Cap'n Proto framing, regf census, some ROM/disk variants);
  these are marked `implemented-with-limits` or `adapter-and-plan` in the registry.

## Tests actually completed

- **Built-in regression suite: 53/53 passed** (`selftest`), covering identification,
  entropy/strings/hexdump, encoding/Unicode/codec/numbers/XOR/carve, ~all format parsers,
  sections/symbols/structs, disassembly (x64/ARM64/RISC-V incl. RVC/MIPS/6502/EVM) + CFG,
  data serializers + SQLite, code/regex/SQL/build/secrets gates, network (PCAP/HTTP/
  WebSocket), archive/compress/hash/CRC/diff/repair/patch/convert/fuzz/spec/external, the
  full case workflow (intake→authorize→ledger→survey→plan→report→preserve→verify→timeline→
  compat), and error paths (missing/invalid input, purpose gate, input-changes-output).
- **Disassembler cross-validation** against GNU `objdump` on a compiled `gcc -O0` fixture
  (matching instruction boundaries/mnemonics) and hand-verified RVC/EVM encodings.
- **Real-binary parsing** verified on `/bin/true` (ELF64) and system DER certificates.
- **Clean-state rerun:** cases verified again from a preserved copy; the suite regenerates
  fixtures deterministically and cleans its temp root.

## Package compatibility

- The folder copies **unmodified** beside a parent root `SKILL.md`; all internal references
  are relative to the skill root with forward slashes and exact case. No absolute, drive,
  parent-traversal, catalog, tenant, install, or generated URLs are embedded.
- All files are directly inside the folder (depth 1); no subfolders, no `SKILL.md`, no
  unclassified files. Exactly one knowledge file. No nested archives, no caches/logs/temp
  files, no installed dependencies, no machine-specific paths.
- Verify any referenced resource exists case-sensitively before relying on it; the tool
  validates inputs and asset references at runtime and reports structured errors otherwise.
