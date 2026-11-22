# console-agent-workbench — knowledge and operations guide

A production Skill Tool for lawful game-console preservation, media verification, provenance
recording, guardrail enforcement, retention control, worker routing, and archival handoff.

It is a **reusable component** for a future Copilot skill. It deliberately ships **no `SKILL.md`**;
copy the `console-agent-workbench/` folder beside a parent package's root `SKILL.md` and reference
`console-agent-workbench/tools-console-agent.py` from that skill.

| | |
|---|---|
| Skill Tool name | `console-agent-workbench` |
| Primary entry point | `console-agent-workbench/tools-console-agent.py` |
| Runtime | Python 3.9+ (tested on 3.13.16), **standard library only** |
| Source catalog | *Console Agent Skill Catalog* — 36 categories, 491 skills |
| Skills executed directly | 29 of 491 (every other skill is reachable through routing, plans, and reports) |
| Files in this folder | 16 (7 `tools-*`, 1 `knowledge-*`, 8 `asset-*`) |
| Network use | none — no downloads, uploads, telemetry, or remote services |
| Third-party packages | none |

---

## 1. Purpose

The source catalog describes 491 proposed skills across 36 console-preservation categories. Most of
those skills need physical hardware, licensed SDKs, or third-party tools. This Skill Tool implements
the part of that catalog that software can honestly own — the **cross-cutting foundation** — as one
unified, runnable workbench:

* knowing what a host can and cannot do,
* refusing and recording unlawful work,
* hashing, identifying, and verifying media,
* recording provenance that cannot be silently edited,
* planning reversible experiments,
* deciding where work must run,
* and producing a redistribution-safe report.

Everything else in the catalog stays discoverable: `catalog` searches all 491 skills, and each skill
carries a routing class that says what it actually needs (hardware, an official program, an external
tool, or a workbench command).

### Capability list (9 families, 16 commands)

| Family | Commands | What is implemented |
|---|---|---|
| discovery-and-selftest | `inventory`, `selftest`, `help`, `capabilities` | bounded tool probing, catalog status vocabulary, 12 real smoke checks, gap recommendations |
| legal-guardrails | `gate`, `scan`, `redact` | signed operator authorization, restricted-material scanning in 4 modes, policy redaction |
| provenance-and-evidence | `provenance`, `hash` | schema-validated provenance records, hash-chained append-only ledger, one-pass manifests |
| media-identification-and-verification | `inspect`, `verify` | header parsing for 10 media families, DAT verification, block-level repeated-read comparison |
| planning | `plan` | 9-stage decomposition, baselines, stop conditions, rollback, platform addenda |
| reporting-and-handoff | `report` | Markdown/self-contained-HTML/JSON report, artifact minimization, redaction |
| evidence-retention | `retention` | retention classes, reversible quarantine, restore, approval-gated hard delete |
| routing | `route` | worker/hardware/official-program routing with explicit unmet requirements |
| catalog-retrieval | `catalog` | search, filter, and statistics over 36 categories / 491 skills |

### Deliberately not implemented

These are stated here and by `capabilities --include-unimplemented` so no claim outruns the code:

* running emulators, debuggers, or FPGA toolchains (they are probed, routed, and reported — never wrapped),
* reading physical media (cartridge dumpers, optical drives, logic analyzers are hardware),
* parsing No-Intro / Redump / TOSEC / MAME **XML** DATs (a documented JSON DAT format is used instead),
* parsing proprietary containers that need licensed documentation or decryption (PBP, XEX, SELF, RVZ, CHD),
* any decryption, DRM/account/anti-cheat circumvention, or piracy support,
* any network access whatsoever.

---

## 2. Setup

Nothing to install beyond Python.

```bash
# bash — verify the runtime
python3 --version                     # 3.9 or newer

# bash — run from anywhere; resources resolve relative to the script, not the cwd
python3 console-agent-workbench/tools-console-agent.py help
python3 console-agent-workbench/tools-console-agent.py capabilities
python3 console-agent-workbench/tools-console-agent.py selftest --gaps
```

Optional external tools are **never required**. When present they are detected by `inventory`:
MAME, RetroArch, Mednafen, Ghidra (`analyzeHeadless`), radare2/Rizin, `chdman`, FFmpeg/FFprobe,
7-Zip, cmake, ninja, gcc, clang, gdb, lldb, QEMU, Verilator, Icarus Verilog, Yosys, nextpnr,
git, sqlite3, sha256sum, b3sum, Aaru, ddrescue, ExifTool, ImageMagick.

```powershell
# PowerShell (Windows) equivalent invocation
py -3 console-agent-workbench\tools-console-agent.py help
```

`tools-console-agent.py` is also directly executable on POSIX if the executable bit is preserved:
`./console-agent-workbench/tools-console-agent.py help`.

---

## 3. File inventory and load order

All files live directly inside `console-agent-workbench/`. There are no subfolders.

| File | Prefix | Role |
|---|---|---|
| `tools-console-agent.py` | tools | **Primary entry point.** Argument parsing, all 16 command handlers, capability map, error→exit-code mapping. |
| `tools-caw-runtime.py` | tools | Shared runtime: resource resolution, JSON envelope, exit codes, status vocabulary, bounded walker, hashing primitives, timeout-guarded subprocess probes, host/hardware detection. |
| `tools-caw-guard.py` | tools | Guard policy loading/validation, restricted-material scanning, redaction, authorization records, approval tokens. |
| `tools-caw-media.py` | tools | Hashing and manifests, 10 media/container detectors, DAT loading/verification, repeated-read cross-check. |
| `tools-caw-provenance.py` | tools | JSON-Schema-subset validator, provenance records, hash-chained ledger, plan/decomposition builder, retention evaluate/apply/restore. |
| `tools-caw-report.py` | tools | Evidence classification, section builders, template substitution, empty-section pruning, Markdown→HTML renderer. |
| `tools-test-suite.py` | tools | Integration test suite: 17 tests, 154 assertions, driving the real CLI. |
| `asset-capability-catalog.json` | asset | All 36 categories and 491 skills (name + description) with the routing class and any implementing command. |
| `asset-guard-policy.json` | asset | 10 file rules, 6 content rules, 8 redaction rules, 4 scan modes, gated actions, retention classes. |
| `asset-provenance-schema.json` | asset | JSON Schema for provenance records (validated by the built-in subset validator). |
| `asset-probe-manifest.json` | asset | 30 tool probes, hardware classes, priorities, remediations. |
| `asset-worker-profiles.json` | asset | 10 worker profiles, platform routing table, requirement→status labels. |
| `asset-plan-playbook.json` | asset | 9 stages, baselines, stop conditions, rollback, approvals, 11 platform addenda. |
| `asset-report-template.md` | asset | Report skeleton with `{{placeholders}}`; editable without touching code. |
| `asset-dat-template.json` | asset | Documented trusted-DAT format with **zero entries** (hashes are never invented). |
| `knowledge-readme.md` | knowledge | This file. |

**Load order:** the entry point registers `tools-caw-runtime.py` in `sys.modules` as `caw_runtime`,
then loads `caw_guard`, `caw_media`, `caw_provenance`, and `caw_report`, which all `import
caw_runtime`. The four `caw_*` modules are imported, never executed directly. Loading is explicit
because the filenames are prefixed with `tools-` and are therefore not importable by name. If a
module is missing, the entry point exits 1 with a fatal message naming the file.

---

## 4. Interface contract

### 4.1 Envelope

Every command prints one JSON object (default) or a human summary (`--format text`):

```json
{
  "ok": true,
  "skill": "console-agent-workbench",
  "skill_version": "1.0.0",
  "command": "inspect",
  "exit_code": 0,
  "generated_at": "2026-11-09T01:47:23Z",
  "warnings": [],
  "errors": [],
  "data": { "...": "command-specific" },
  "elapsed_ms": 42
}
```

`data` is the stable machine interface; `--format text` never changes it. Field order and key names
are stable; new keys may be added.

### 4.2 Exit codes

| Code | Meaning |
|---|---|
| 0 | success (also used when a guardrail verdict is `review`: the operator must decide) |
| 1 | unexpected internal error (with `--verbose` the traceback is included) |
| 2 | invalid input / validation failure / unknown argument |
| 3 | policy block (scan verdict `block`, invalid authorization, `redact --check` found hits) |
| 4 | approval required, missing, expired, or not covering the target set |
| 5 | missing path or resource |
| 6 | checks failed (missing capabilities, unverified DAT match, divergent reads, broken ledger, unidentified artifact, unmet routing requirements) |

### 4.3 Global options

`--format {json,text}`, `--workdir PATH` (default `caw-work`), `--policy PATH`, `--catalog PATH`,
`--timeout SECONDS`, `--verbose`, `--version`. Global options may be given before **or** after the
subcommand; the subcommand value wins.

### 4.4 Default limits

From `tools-caw-runtime.py` `DEFAULT_LIMITS`: `max_files` 20000, `max_depth` 24,
`max_read_bytes` 65536, `command_timeout_seconds` 10, `overall_timeout_seconds` 300, no per-file
hash cap by default (content hashing is capped at 512 MiB inside `inspect`/`verify` unless overridden).
Every limit is overridable per command (`--max-files`, `--max-depth`, `--max-read-bytes`,
`--hash-cap`, `--timeout`). Exceeding a limit produces a `skipped` entry with a reason — never a
silent truncation.

### 4.5 Status vocabulary

`inventory` reports exactly one of the catalog's statuses: `FOUND`, `MISSING`, `WRONG VERSION`,
`BROKEN`, `BIOS OR FIRMWARE REQUIRED FROM USER`, `USER-OWNED MEDIA REQUIRED`, `OFFICIAL SDK REQUIRED`,
`LICENSE REQUIRED`, `WINDOWS HOST REQUIRED`, `MACOS HOST REQUIRED`, `PHYSICAL HARDWARE REQUIRED`,
`OFFICIAL DEVKIT REQUIRED`, `GPU REQUIRED`, `FPGA HARDWARE REQUIRED`, `VIRTUALIZATION REQUIRED`,
`NETWORK REQUIRED`. Each capability record carries every field the catalog requires:
`capability_id`, `status`, `command_or_probe`, `detected_version`, `host_type`, `architecture`,
`license_state`, `hardware_state`, `required_user_material`, `test_result`, `evidence_path`,
`recommended_action`.

---

## 5. Command reference

Every example is runnable from a future skill root. `CLI` below stands for
`python3 console-agent-workbench/tools-console-agent.py`.

### 5.1 `help`
```bash
$CLI help                     # the whole help text
$CLI help scan                # detailed help for one command
```

### 5.2 `capabilities`
```bash
$CLI capabilities                                  # 9 families + catalog totals
$CLI capabilities --include-unimplemented          # plus the explicit non-goals
```

### 5.3 `catalog` — retrieve from all 491 skills
```bash
$CLI catalog                                       # totals, routing histogram
$CLI catalog --list-categories                     # 36 rows with skill counts
$CLI catalog --category 17                         # one category by index
$CLI catalog --category "rom, dat"                 # or by name substring
$CLI catalog --search "hash" --limit 10            # search names and descriptions
$CLI catalog --skill "Repeated-Read Comparator"    # exact-ish single skill
$CLI catalog --routing official-program            # filter by routing class
$CLI catalog --implemented-only                    # the 29 executed skills
```

Routing classes: `workbench-automated`, `workbench-assisted`, `guided-workflow`, `external-tool`,
`hardware-dependent`, `hardware-or-emulator-dependent`, `official-program`.

### 5.4 `inventory`
```bash
$CLI inventory --priority P0                 # probe only P0 tools
$CLI inventory --priority P0 --priority P1    # several priorities
$CLI inventory --no-hardware                 # skip device-class probing
$CLI inventory --max-probes 5 --out work/inventory.json
$CLI inventory --licenses work/licenses.json # {"tool:mame": "licensed", ...}
```
Device-class probing is read-only and generic: optical drives (`/dev/sr*`), serial readers
(`/dev/ttyACM*`, `/dev/ttyUSB*`), block devices, USB device count, `/dev/dri` + `nvidia-smi`/
`vulkaninfo` for GPU. **It never claims which console is attached** — identity needs operator
confirmation. `license_state` is `unknown` unless the operator supplies `--licenses`; licenses are
not detectable in software.

### 5.5 `selftest`
```bash
$CLI selftest --gaps
$CLI selftest --out work/evidence/selftest.json
$CLI selftest --gaps --full-probes
```
12 checks, each with `status`, `detail`, `duration_ms`: python-runtime, json-contract,
hashing-correctness (crc32 `cbf43926` for `b"123456789"` and hashlib cross-checks), catalog-load
(36/491), policy-load, dat-load, redaction-rules, ledger-chain, report-render, plan-build,
media-parsers (synthesises an iNES file and asserts mapper/banks/size), inventory-subset.
The evidence file is a full envelope written to `--out` or `<workdir>/evidence/selftest-<id>.json`.
Exit 6 if any check fails.

### 5.6 `gate` — lawful-use gate and approvals
```bash
# operator attestation (--attest is mandatory; without it the command refuses)
$CLI gate --action authorize --operator "A. Operator" \
    --material media:owner-cartridge-01 --material firmware:console-bios \
    --statement "Retail purchases, personally owned" --ttl-hours 48 --out work/authz.json

$CLI gate --action verify --record work/authz.json --requires media
$CLI gate --action list --dir work/authz

# approval token for a gated action
$CLI gate --action approve --gated-action retention-apply --operator "A. Operator" \
    --scope work/evidence --ttl-minutes 30 --out work/approval.json
$CLI gate --action check-approval --gated-action retention-apply \
    --approval work/approval.json --scope work/evidence
```
Authorization records are signed with `record_sha256` over the canonical JSON body; any edit is
detected. Approvals are signed with `payload_sha256` **and** carry the approved `entry_list`
(path + size). A target set is accepted only if every target appears in the approved list with the
same path and size, so a token cannot be reused for files the operator never saw. Both records
expire. See §7 for the rules.

### 5.7 `scan` — restricted-material boundary
```bash
$CLI scan --path work/package --mode package      # block on critical/high
$CLI scan --path work/package --mode redistribute
$CLI scan --path work/package --mode bundle
$CLI scan --path work/reports --mode report
$CLI scan --path work --max-files 500 --max-depth 4 --out work/scan.json
```
Verdict by mode (`block` / `review` / `clear`):

| mode | blocks on | reviews on |
|---|---|---|
| `package` | critical, high | medium, low |
| `redistribute` | critical, high | medium |
| `bundle` | critical | high, medium, low |
| `report` | nothing | everything |

Rules cover BIOS/firmware names, key/certificate containers, private key material, leaked-SDK
markers, devkit serials, cartridge and optical images, save memory, archives, oversized binaries,
PEM key blocks, credential assignments, console key blobs, NDA material, circumvention markers.
Extensions that collide with ordinary files (`.md`, `.bin`, `.dat`, `.img`, …) are checked against
documented console signatures; without a signature they are reported at severity `low` — so a
Markdown file is never mistaken for a Mega Drive image at blocking severity. Symlinks are not
followed and are reported.

**Self-scan exemption.** `asset-guard-policy.json`, `asset-capability-catalog.json`, and
`knowledge-readme.md` must quote the very patterns and skill descriptions they document, so they are
listed in the policy's `scan_exempt_filenames`. The exemption is by exact filename and is always
reported in the scan's `skipped` list, so it is visible rather than hidden; it can never cover
media, keys, firmware, or operator files. Scanning this folder with `--mode package` therefore
returns **CLEAR** with one `low` finding (`asset-report-template.md`, an ambiguous `.md` extension).

### 5.8 `redact`
```bash
$CLI redact --in work/report.md --out work/report.clean.md
$CLI redact --in work/report.md --check      # exit 3 when anything would change
```
Rules: home/absolute paths (POSIX and Windows), PEM private keys, e-mail addresses, credential
values, bearer tokens, devkit serials, IPv4 addresses. Redaction is applied to **every** string in a
rendered report too, so a leaky input cannot reach the output.

### 5.9 `hash`
```bash
$CLI hash --path dumps --out work/manifest.json
$CLI hash --path dumps --algorithm crc32 --algorithm sha256
$CLI hash --path dumps --ext gb --ext nes --no-recursive
$CLI hash --path big.iso --hash-cap 1048576
```
Single pass computes every requested algorithm (`crc32`, `md5`, `sha1`, `sha256`, `blake2b`);
`manifest_sha256` digests the whole manifest. Files above `--hash-cap` (default 512 MiB for the
manifest path) keep their `size` and are listed in `skipped` with a reason.

### 5.10 `inspect`
```bash
$CLI inspect --path dumps/title.nes
$CLI inspect --path dumps/a.gb --path dumps/b.iso --out work/inspect.json
$CLI inspect --path dumps/title.nes --no-hash
```
Detectors and the fields they actually parse:

| format | evidence used |
|---|---|
| `ines` | `NES\x1a`, mapper/submapper, PRG/CHR banks, mirroring, battery, trainer, NES 2.0 detection, console type, timing, header-implied vs actual size |
| `fds` / `fds-raw` | `FDS\x1a` copier header with declared sides; raw `\x01*N` disk images |
| `snes` | LoROM/HiROM/ExHiROM header candidates, title, mapping byte, ROM size, region, version, checksum **and complement consistency** |
| `genesis` | `SEGA` at `0x100`, console/domestic/overseas names, serial, region code, ROM start/end, SRAM window |
| `gb` | Nintendo logo at `0x104`, title, CGB flag, cartridge type, MBC, ROM/RAM size, destination, and a **recomputed header checksum** |
| `gba` | GBA logo head, title, game/maker code, version, **recomputed header complement** |
| `n64` | z64/v64/n64 magic, byte order, ROM name, game code, clock rate, entry point |
| `iso9660` | primary volume descriptor at sector 16: system/volume identifiers, block count, block size, size consistency, PlayStation/Sega system hint |
| `zip` | entry count, uncompressed size, encryption flags, **nested archive list** |
| `psx-memcard` | `MC` frame magic, 131072-byte frame count, header-less variants |
| `raw-save` | size-based guess only, marked with a warning and low confidence |
| `unknown` | magic hex + entropy, exit 6 |

### 5.11 `verify`
```bash
$CLI verify --dat work/trusted-dat.json --path dumps
$CLI verify --dat work/trusted-dat.json --path dumps --require-match    # exit 6 on any gap
$CLI verify --crosscheck --path read1.bin --path read2.bin --path read3.bin
```
DAT mode matches in `match_order` (`sha256` → `sha1` → `md5` → `crc32+size`) and reports
`verified`, `verified-crc32`, `size-mismatch`, `not-verified`, or `unknown` per file, plus the DAT
id/version/path actually used. Cross-check mode compares two or more independent reads block by
block and reports the first differing byte offset and the number of differing blocks; identical
reads are grouped by SHA-256.

### 5.12 `provenance`
```bash
$CLI provenance --action create --material dumps/title.gb --operator "A. Operator" \
    --media-type cartridge --region USA --revision "1.0" \
    --method "cartridge reader" --reader "reader-x" --hardware "handheld" --firmware "1.4" \
    --reads 2 --software-version reader=2.1 --ledger work/ledger.jsonl --out work/record.json
$CLI provenance --action validate --record work/record.json
$CLI provenance --action append --record work/record.json --ledger work/ledger.jsonl --operator "A. Operator"
$CLI provenance --action verify-ledger --ledger work/ledger.jsonl
$CLI provenance --action list --ledger work/ledger.jsonl
```
`create` hashes the material, auto-fills detected format/platform from `inspect`, validates against
`asset-provenance-schema.json`, then optionally appends to the ledger. The ledger is append-only
JSONL; each line stores `seq`, `prev_sha256`, `record`, and `entry_sha256`, so editing, removing, or
reordering a line breaks `verify-ledger` at that sequence number.

### 5.13 `plan`
```bash
$CLI plan --goal "preserve a Game Boy cartridge" --platform game-boy --risk low
$CLI plan --goal "hand off a PS5 build" --platform ps5 --stage acquire --stage handoff
$CLI plan --decompose --goal "audit a library"
$CLI plan --goal "preserve" --out work/plan.json
```
Stages: `acquire → preserve → identify → inspect → reproduce → test → compare → package → handoff`.
Every plan carries `baseline`, `stop_conditions`, `rollback`, `approvals`, and the platform addendum
(`cartridge`, `game-boy`, `nes`, `snes`, `optical`, `arcade`, `ps4`, `ps5`, `xbox`, `switch`, `fpga`).
Unknown stages and unknown risk levels are rejected with exit 2.

### 5.14 `report`
```bash
$CLI report --input work/record.json --input work/inspect.json --input work/scan.json \
    --input work/inventory.json --input work/verify.json --input work/plan.json --input work/route.json \
    --operator "A. Operator" --goal "verify a cartridge" --title "Cartridge report" \
    --limitation "single reader used" --next-step "populate a trusted DAT" \
    --report-format html --out work/report.html
$CLI report --input work/record.json --report-format json --out work/report.json
```
Inputs are any JSON envelopes produced by the other commands (auto-classified by `command` /
`record_type`), so the report is assembled from real evidence only. Sections are emitted only when
they have content — empty sections are removed, never left as placeholders. HTML output is
self-contained (inline CSS, no external fonts/scripts/images/URLs) and works inside a sandboxed
iframe. `--allow-content` records that content inclusion was permitted; default behaviour is
metadata-only. `--template` swaps the skeleton; unsupported placeholders are rejected with exit 2.

### 5.15 `retention`
```bash
$CLI retention --action policy
$CLI retention --action evaluate --dir work/evidence
$CLI retention --action apply --dir work/evidence --approval work/approval.json      # quarantine (reversible)
$CLI retention --action restore --dir work/evidence/caw-quarantine                    # recovery
$CLI retention --action apply --dir work/evidence --hard-delete --approval work/hard-approval.json
$CLI retention --action evaluate --dir work/evidence --now 2027-01-01T00:00:00Z        # testing aid
```
Classes from `asset-guard-policy.json`: `records` 3650 d, `dumps` 3650 d, `logs` 180 d,
`captures` 180 d, `crash` 90 d, `temporary` 7 d, default 365 d. `apply` moves due files into
`<root>/caw-quarantine/`, preserving relative paths and writing a manifest with each file's SHA-256;
`restore` moves them back and re-verifies the hashes. Hard delete requires the separate
`retention-hard-delete` approval action.

### 5.16 `route`
```bash
$CLI route --list-profiles
$CLI route --platform nes
$CLI route --platform ps5                       # official program + unmet SDK/devkit/account
$CLI route --require cartridge-dumping
$CLI route --official --require ps5-development
```
Profiles: `linux-x86_64-core`, `linux-gpu-worker`, `windows-console-worker`, `macos-console-worker`,
`fpga-lab`, `physical-console-lab`, `official-playstation-partner`, `official-xbox-gdk`,
`official-nintendo-dev`, `steamworks-verification`. Unmet requirements are returned with the
catalog's status labels (for example `OFFICIAL SDK REQUIRED`, `PHYSICAL HARDWARE REQUIRED`) and the
command exits 6, so a caller cannot mistake a routed plan for a ready one.

---

## 6. Data formats

### 6.1 Provenance record (`asset-provenance-schema.json`)
Top level: `schema_version`, `record_type`, `record_id`, `operator`, `created_at`, `material`,
`dump`, `integrity`, `provenance`, `rights`. Hashes are lowercase hex with enforced lengths
(`crc32` 8, `md5` 32, `sha1` 40, `sha256` 64); `created_at` is ISO-8601; `ownership` and
`redistribution` are closed enumerations; unknown properties are rejected. The validator implements
a documented **subset** of JSON Schema (`type`, `required`, `properties`, `additionalProperties`,
`enum`, `pattern`, `format: date-time`, `items`, `minItems`/`maxItems`, `minLength`/`maxLength`,
`minimum`/`maximum`). It is not a general-purpose JSON Schema implementation.

### 6.2 Trusted DAT (`asset-dat-template.json`)
```json
{
  "dat_id": "local-trusted-dat",
  "name": "Operator-maintained trusted DAT",
  "version": "1970-01-01",
  "match_order": ["sha256", "sha1", "md5", "crc32+size"],
  "entries": [
    {"name": "Title (USA)", "system": "NES", "region": "USA", "revision": "1.0",
     "status": "verified", "size": 40976,
     "crc32": "00000000", "sha1": "…40 hex…", "sha256": "…64 hex…"}
  ]
}
```
The shipped template has **no entries on purpose** — hashes are never invented. Each entry needs
`name` plus at least one hash. Populate it from a DAT source you are licensed to use; the tool does
not fetch anything.

### 6.3 Ledger line
```json
{"seq": 1, "appended_at": "2026-11-09T01:46:37Z", "appended_by": "A. Operator",
 "prev_sha256": "000…0", "record": {…}, "entry_sha256": "…"}
```

### 6.4 Guard policy knobs
`file_rules[]` (`kind`: `extension` | `filename_pattern` | `size_threshold` | `always`, plus
`severity`, `category`, `reason`, `remediation`, optional `ambiguous_extensions`),
`content_rules[]` (`kind`: `content_regex`), `redaction_rules[]` (`pattern` + `replacement`),
`verdicts_by_mode`, `approval_required_actions`, `retention`, `authorization_materials`,
`scan_exempt_filenames` (exact filenames skipped by `scan`, always listed in `skipped`).

---

## 7. Safety and approval rules

1. **Authorization first.** `gate --action authorize` requires `--attest`; the record is signed and
   expires. Without a valid record covering the material, work must not proceed.
2. **No restricted material leaves the workspace.** `scan --mode package` and `--mode redistribute`
   block on critical and high findings. BIOS/firmware, key material, certificates, leaked SDKs,
   signing keys, DRM/account bypasses, and archives are blocked outright.
3. **Reports are metadata only.** No command embeds media bytes, key material, or firmware. Every
   rendered string passes through the redaction rules.
4. **Approval before destructive or sensitive actions.** `retention-apply`, `retention-hard-delete`,
   `publish-report`, `write-firmware`, and `flash-hardware` are gated in the policy. Today
   `retention apply` is the wired implementation; `write-firmware`/`flash-hardware` exist as gated
   policy names for a parent skill to honour — this workbench never writes to hardware.
5. **Approvals are scoped and tamper-evident.** The approved file list (path + size) is inside the
   signed payload; every target must appear in it, and the hash chain must verify.
6. **Official programs are never substituted.** PS4, PS5, PS5 Pro, Xbox One, Xbox Series X|S, Switch,
   and Switch 2 work routes to PlayStation Partners, Microsoft GDK, or the Nintendo Developer Portal
   and reports `OFFICIAL SDK REQUIRED` / `OFFICIAL DEVKIT REQUIRED` / `LICENSE REQUIRED`.
7. **Read-only by default.** Inspection never writes to the inspected file; media operations are
   out of scope; the only write paths are the operator's own `--out` files, the workdir, and
   quarantined evidence.
8. **Emulation is not certification.** A successful inspection or verification says nothing about
   title compatibility, redistribution rights, or platform certification.

---

## 8. Validation performed

All of the following was executed on this package on Python 3.13.16 (Linux x86_64, Debian 13).

**Interface**
* `help`, `help scan`, `capabilities`, `capabilities --include-unimplemented` — exit 0, text and JSON.
* `--version` prints `console-agent-workbench 1.0.0`; unknown command → exit 2.
* `pyflakes` over all seven `tools-*.py` files — no findings.

**`selftest`** — 12/12 checks pass, evidence file written outside the skill folder.

**Test suite** — `python3 console-agent-workbench/tools-test-suite.py` → **17/17 tests passed**
(154 assertions), each test in its own temporary workspace:

| test | what it proves |
|---|---|
| `test_help_and_capabilities` | help, per-command help, capability families, 491-skill total, refusal of an unknown topic |
| `test_catalog` | totals, category by index, search, single skill, implemented-only ≥ 29, invalid category → 2 |
| `test_inspect_formats` | all 10 detectors produce the expected `detected_format`; NES mapper/size, NES 2.0, GB header checksum, GBA complement, nested-archive detection, unknown file → 6 |
| `test_hash_manifest` | crc32/md5/sha1/sha256 match hashlib and zlib exactly; manifest file written; single-algorithm mode; invalid algorithm → 2; hash cap keeps size |
| `test_verify_dat` | sha256 match, `unknown` for a non-member, `--require-match` → 6, size mismatch detected, malformed DAT → 2 |
| `test_crosscheck` | identical reads consistent; one flipped byte detected at the exact offset with 1 differing block; single file → 2 |
| `test_scan_guardrails` | package mode blocks key/firmware/PEM/credential/ROM findings (exit 3); clean tree clear; report mode review; missing path → 5; `--out` file valid |
| `test_redact` | e-mail, home path, token, IPv4 all removed; `--check` → 3; clean file → 0 |
| `test_gate_authorization` | attestation required, record signed, `--requires` mismatch → 3, tampered record detected, list works |
| `test_provenance_and_ledger` | record schema-valid, hashes match the file, detected format recorded, invalid record → 2 with JSON-pointer problems, ledger seq/verify/list, edited ledger line breaks the chain → 6 |
| `test_plan` | nine stages in order, NES addendum applied, stage subset, decompose, invalid stage → 2, missing goal → 2 |
| `test_route` | profile list, NES → physical lab (or unmet hardware), PS5 → official partner with unmet SDK/devkit/account and exit 6, requirement-based routing, unknown platform → 2 |
| `test_report` | Markdown/HTML/JSON render, no `{{` left, no empty section, redaction applied to output, no external URLs in HTML, custom template with an unknown placeholder → 2 |
| `test_retention_with_approval` | evaluate classes, apply without approval → 4, wrong-scope approval → 4, correct approval quarantines exactly the due files, restore returns bytes, hard delete needs its own action |
| `test_inventory_and_selftest` | every status field present and inside the catalog vocabulary, priority filter, probe cap, selftest evidence file |
| `test_error_handling` | exit codes 5/2/4 across nine invalid inputs; error envelopes stay structured |
| `test_no_state_leaks_into_skill_folder` | the skill folder is byte-identical (same file list) after running commands |

**Manual end-to-end run** (documented for reuse): authorize → `inspect` → `hash` → `scan` →
`verify` → `plan` → `route` → `provenance create` + ledger → `report --report-format html`. Verified
that: the ledger verified at 2 entries, the PS5 route exited 6 with `OFFICIAL SDK REQUIRED`,
`OFFICIAL DEVKIT REQUIRED`, `LICENSE REQUIRED`, the scan blocked a key-bearing package with exit 3,
and the rendered HTML was self-contained with a populated artifacts/guardrails/plan/routing tables.

**Self-scan** — `scan --path console-agent-workbench --mode package` returns **CLEAR** (16 files,
1 low finding for the ambiguous `.md` report template, 3 files exempted as documented above).
Trigger-shaped test fixtures (PEM marker, credential-shaped token, key-blob name) are assembled at
runtime so this package never ships a literal secret-shaped string of its own.

**Packaging checks** — folder contains no `SKILL.md`, no subfolder, no unprefixed file; every
internal reference resolves relative to the script directory; the ZIP contains the folder at its root
with no wrapper and no nested archive; the archive was extracted to a clean temporary skill root
beside a test `SKILL.md` and the CLI was re-run from there, including `selftest` and the test suite.

---

## 9. Limits and unavailable integrations

* **No hardware access.** Cartridge/optical dumping, logic analysis, capture, and controller testing
  cannot be performed. `route` and `retention` model them; the workbench never opens a device.
* **No emulators or debuggers are wrapped.** They are probed (`inventory`) and named in plans; this
  tool does not launch them.
* **No XML DAT support.** No-Intro/Redump/TOSEC/MAME DATs must be converted to the documented JSON
  shape by the operator. Track/pregap/sector-level disc verification is **not** implemented;
  only whole-file hashing.
* **No proprietary format parsing.** PBP/XEX/SELF/RVZ/CHD/pkg internals are out of scope.
* **No decryption, key handling, or DRM work** — by design, and enforced by the scan policy.
* **No network features**, no hosted lookups, no DAT downloads, no telemetry.
* **License state is not detectable.** `inventory` reports `unknown` unless `--licenses` supplies it.
* **Device identity is not detectable.** Presence checks only; confirm the actual console yourself.
* **JSON Schema subset.** The validator is intentionally small; it is not a full JSON Schema engine.
* **Heuristics are labelled as such.** `raw-save` is a size guess (confidence 0.4); SNES checksum
  *recomputation* is not attempted — only checksum/complement header consistency is verified, and
  the report says so.
* **Scan is pattern-based.** A clean verdict is not proof of lawfulness; it means no configured rule
  matched within the read limits. Files above the read cap are content-checked on their prefix only.
* **Nested-archive detection is extension-based** inside ZIP listings.
* **Content rules are keyword-based and cannot read intent.** They match pattern text wherever it
  appears, including in documentation; that is why the packaged documentation assets are exempted by
  filename and why the exemption is surfaced in every scan result.

---

## 10. Troubleshooting and recovery

| Symptom | Cause | Action |
|---|---|---|
| exit 1 with `required tool module is missing` | a `tools-caw-*.py` file was not copied | copy all seven `tools-*.py` files into the same folder |
| `policy rule … invalid pattern` / `policy is missing required keys` | edited `asset-guard-policy.json` | fix the regex or restore the shipped asset; `--policy` can point at a known-good copy |
| `no skill name contains …` (exit 2) | catalog search miss | use `catalog --list-categories` first; search matches names/descriptions only |
| scan verdict `review` but exit 0 | mode treats medium/low as review, not block | use `--mode package` for delivery decisions, or resolve the findings |
| exit 4 on `retention apply` | no token, expired token, wrong action, or targets outside the approved list | issue a token over the exact evidence root: `gate --action approve --gated-action retention-apply --scope <root>` |
| quarantined files needed again | normal recovery path | `retention --action restore --dir <root>/caw-quarantine`; hashes are re-verified |
| `verify-ledger` reports a broken chain | a ledger line was edited/reordered | do not "repair" it in place: keep the file as evidence, start a new ledger, note the break in the report |
| `inspect` returns `unknown` (exit 6) | no documented header matched | confirm the file is what you think it is; check size/extension; use `catalog --search` for the platform's identification skill |
| `report` exit 2 with `unknown placeholders` | custom template has unsupported `{{…}}` | align the template with the supported keys in `asset-report-template.md` |
| `inventory` shows many `MISSING` | a bare host | expected — `selftest --gaps` and `route` tell you the smallest next addition |
| probe timeouts | a slow first-run tool | raise `--timeout`; the probe is bounded and never retried automatically |

**Maintenance**

* Bump `VERSION` in `tools-caw-runtime.py`; `--version`, envelopes, and reports read it.
* Extend the catalog: edit `asset-capability-catalog.json` (`categories[].skills[]`, plus
  `implemented_by` when a command covers a skill). `selftest` asserts 36 categories / 491 skills —
  update that check if the catalog is intentionally extended.
* Add a scan rule: append to `file_rules`/`content_rules` in `asset-guard-policy.json`; run
  `selftest` and `tools-test-suite.py -k scan`.
* Add an inspection format: append a detector to `DETECTORS` in `tools-caw-media.py` and a case to
  `test_inspect_formats`.
* Add a command: implement `cmd_<name>` in `tools-console-agent.py`, register it in `HANDLERS`, add
  the subparser in `build_parser()`, then document it here (§5) and in `CAPABILITIES`.
* Add a platform: extend `platform_routing` / `workers` in `asset-worker-profiles.json` and
  `platform_addenda` in `asset-plan-playbook.json`.
* Keep every path relative to the skill root and every filename prefixed (`tools-`, `asset-`,
  `knowledge-`). Do not add subfolders or a `SKILL.md` here.

---

## 11. Quick recipes

**Verify a cartridge dump and hand it off**
```bash
$CLI gate --action authorize --operator "A. Operator" --material media:cart-01 --attest --out work/authz.json
$CLI hash --path dumps --out work/manifest.json
$CLI inspect --path dumps/title.gb --out work/inspect.json
$CLI verify --dat work/trusted-dat.json --path dumps --require-match --out work/verify.json
$CLI provenance --action create --material dumps/title.gb --operator "A. Operator" \
     --media-type cartridge --region USA --revision "1.0" --reads 2 \
     --ledger work/ledger.jsonl --out work/record.json
$CLI provenance --action verify-ledger --ledger work/ledger.jsonl
$CLI report --input work/record.json --input work/inspect.json --input work/verify.json \
     --operator "A. Operator" --goal "cartridge handoff" --report-format html --out work/report.html
```

**Decide whether a package may be shared**
```bash
$CLI scan --path work/package --mode redistribute --out work/scan.json
$CLI redact --in work/package/notes.md --check        # exit 3 means: redact before sharing
$CLI route --platform switch                           # confirms the official-program boundary
```

**Clean up evidence under policy, reversibly**
```bash
$CLI retention --action evaluate --dir work/evidence
$CLI gate --action approve --gated-action retention-apply --operator "A. Operator" \
     --scope work/evidence --out work/approval.json
$CLI retention --action apply --dir work/evidence --approval work/approval.json
# undo: $CLI retention --action restore --dir work/evidence/caw-quarantine
```
