# android-artifact-recovery — knowledge and operating guide

A reusable Skill Tool component for lawful Android artifact recovery, compatibility
analysis, package rebuilding and signing. This folder is **not** a complete skill: there
is no `SKILL.md` here. It is designed to be copied directly beside a future package's
root `SKILL.md`.

- **Skill Tool name:** `android-artifact-recovery`
- **Primary entry point:** `android-artifact-recovery/tools-android-artifact-recovery.py`
- **Capability source:** `Android_Agent_Skill_Catalog.pdf` (November 2026 edition, Linux
  x86_64 target) — 24 categories, 324 catalogued skill concepts.
- **Runtime:** Python 3.9+ standard library for the core lane; optional host binaries
  (Java, Android build-tools, apktool) unlock the mutating lane.

---

## 1. Purpose

Android preservation and repair work fails when a tool claims more than it can prove.
This Skill Tool follows one rule: **every statement must be backed by something the tool
actually measured, and anything else must be reported as untested.**

It lets an agent:

1. Identify what an artifact really is, regardless of its filename.
2. Decode and audit package internals without an Android SDK.
3. Rebuild and sign a package it is authorized to modify, with a preserved original and
   a chain-of-custody ledger.
4. Produce evidence reports and batch inventories that separate observed facts from
   untested claims.
5. State precisely which external tools, devices and host features are unavailable
   instead of pretending they worked.

### Use and safety boundary

The tool is for applications, devices, data, services and assets that the operator owns
or is explicitly authorized to inspect, modify, test, preserve or migrate.

It deliberately does **not** implement, and will not help with, bypassing authentication,
purchases, DRM, licensing, device integrity, certificate protections or private-storage
controls. There is no network capability, no exploit code and no dynamic instrumentation
in this component.

---

## 2. Complete capability list

Twenty-six commands split into a core lane and an optional host-tool lane.
`tools-android-artifact-recovery.py capabilities` prints this list with machine-readable
lane metadata, the catalog index and the asset inventory. Each command entry carries
`lane` (`core` | `optional`), `runtime`, and `degrades_without_tools` — the last one marks
commands that still answer usefully when the host binaries are absent (`doctor`,
`self-test`, `verify`, `certificate`).

### Core lane — pure Python standard library, always available (21 commands)

| Command | What it does | Catalog categories |
| --- | --- | --- |
| `capabilities` | Command list, lanes, catalog index, asset inventory, exit codes | 20, 22, 24 |
| `doctor` | Host profile: CPU, memory, kernel, disk, JDK, Android SDK roots, KVM | 1, 10, 11, 24 |
| `self-test` | Tool probes with the full status vocabulary (see §5) | 1, 24 |
| `identify` | True format detection from magic bytes + container structure | 3, 21 |
| `zip-health` | Central directory, duplicates, traversal, trailing bytes, alignment | 3, 4 |
| `badging` | Package identity, SDK levels, components, permissions, ABIs, launchers | 4, 20 |
| `manifest` | Binary AXML decode: attributes, components, intent-filters, XML export | 4 |
| `arsc` | `resources.arsc` packages, types, configs, locales, densities, values | 4, 17 |
| `dex` | DEX headers, checksums, counts, class-name histogram, multidex | 5 |
| `elf` | ABI, `PT_LOAD` alignment, `DT_NEEDED`, 16 KB readiness, strings | 7, 8 |
| `abi-matrix` | 32/64-bit lane coverage per ABI folder | 7, 18 |
| `page16k` | Per-library 16 KB page readiness (ELF **and** ZIP alignment) | 18, 23 |
| `entry` | Entry listing with filters, optional bounded extraction | 14, 17 |
| `aab-inspect` | AAB modules, proto manifests, dex/libs, `BundleConfig.pb` | 3 |
| `exported-audit` | `android:exported` rules against the declared `targetSdk` | 4, 12, 16 |
| `compat-scan` | Rule-driven compatibility scan (`asset-compat-rules.json`) | 18, 20, 21, 23 |
| `report` | Markdown + JSON evidence report for one artifact | 19, 20 |
| `batch` | Directory sweep with per-item status and CSV/JSON index | 20 |
| `triage` | Failure-text classifier routed to the earliest actionable stage | 13, 15 |
| `strings-scan` | Bounded substring scan for toolchain breadcrumbs | 8 |

### Optional lane — needs host binaries, reports MISSING instead of degrading silently (5 commands)

| Command | Requires | What it does | Categories |
| --- | --- | --- | --- |
| `apktool` | `java` + apktool JAR | `decode` / `build` with state file and build log | 3, 4, 5, 6 |
| `sign` | `apksigner` + keystore | Development signing; **approval required** | 9, 19 |
| `verify` | `apksigner` | Signature scheme verification (v1/v2/v3) | 9, 24 |
| `certificate` | `apksigner` (v1 fallback) | Signer DNs and SHA-256/SHA-1/MD5 digests | 9, 16 |
| `rebuild` | apktool + zipalign + apksigner | preserve → decode → patch → build → align → sign → verify | 3, 9, 19 |
| `preserve` | core only | Read-only copies + hashes + custody record; **approval required** | 19 |

`preserve` runs in the core lane but still requires `--confirm`. Every catalog category
maps to at least one command; the 324-skill mapping ships in
`asset-skill-catalog.json` and is summarised by `capabilities`.

---

## 3. Files in this Skill Tool

| File | Type | Role |
| --- | --- | --- |
| `tools-android-artifact-recovery.py` | primary entry point | CLI, 26 commands, tool discovery, envelope/output |
| `tools-ax-parsers.py` | runtime module | AXML, ZIP layout, artifact sniffing, value formatting |
| `tools-ax-formats.py` | runtime module | `resources.arsc`, DEX, ELF, protobuf/AAB, hashing |
| `tools-android-artifact-recovery-tests.py` | validation harness | 103 self-contained checks; builds its own fixtures |
| `knowledge-readme.md` | knowledge | this document (the only knowledge file) |
| `asset-skill-catalog.json` | asset | 24 categories / 324 skills with command mapping |
| `asset-compat-rules.json` | asset | editable rule pack consumed by `compat-scan` |
| `asset-default-config.json` | asset | default limits, paths, signing and policy values |
| `asset-self-test-plan.json` | asset | probe plan consumed by `self-test` |
| `asset-agent-prompts.json` | asset | seven operating procedures for a host agent |

**Load order.** `tools-android-artifact-recovery.py` is the only entry point. Python module
names cannot contain hyphens, so it loads its siblings by path through a small helper
(`_load_sibling`) that registers them as `ax_parsers` and `ax_formats` in
`sys.modules`. `tools-ax-formats.py` imports from `tools_ax_parsers`, and the loader
registers that alias before loading it. The two runtime modules are never executed
directly. Assets are read at call time by the command that needs them, so a missing asset
degrades one command instead of breaking the tool.

---

## 4. Setup

### 4.1 Requirements

| Component | Version | Needed for |
| --- | --- | --- |
| Python | 3.9+ (tested on 3.13.16) | everything |
| Java | 11+ (catalog baseline: OpenJDK 17) | apktool, apksigner |
| Android build-tools | 34.0.0 tested (35+ for `zipalign -P`) | signing, alignment, `aapt` cross-check |
| apktool JAR | 2.9.3 tested | decode/rebuild |
| GNU `patch` | any | `rebuild --patch` |

No third-party Python packages are required. Nothing is installed by the tool.

### 4.2 Environment variables

| Variable | Purpose |
| --- | --- |
| `ANDROID_HOME` / `ANDROID_SDK_ROOT` | locate `build-tools/` automatically |
| `APKTOOL_JAR` | path to a versioned apktool JAR |
| `ANDROID_TOOLKIT_KS_PASS` | keystore password (kept out of argv) |
| `ANDROID_TOOLKIT_KS_ALIAS` | keystore key alias |

### 4.3 Install

```bash
# 1. Unzip the delivery archive; the Skill Tool folder appears directly at the root.
unzip android-artifact-recovery.zip -d ./skills/
chmod +x ./skills/android-artifact-recovery/tools-android-artifact-recovery.py
chmod +x ./skills/android-artifact-recovery/tools-android-artifact-recovery-tests.py

# 2. Smoke test (no Android tools needed).
cd ./skills/android-artifact-recovery
python3 tools-android-artifact-recovery.py capabilities
python3 tools-android-artifact-recovery.py self-test
```

### 4.4 Exact commands

Use `bash` on Linux/macOS. On Windows use `python` in place of `python3`; the core lane
avoids POSIX-only calls where practical, but `doctor` reports `statvfs`/`uname` data only
on POSIX hosts.

```bash
# discovery
python3 tools-android-artifact-recovery.py --help
python3 tools-android-artifact-recovery.py capabilities
python3 tools-android-artifact-recovery.py doctor
python3 tools-android-artifact-recovery.py self-test

# analysis (core lane)
python3 tools-android-artifact-recovery.py identify game.apk backup.aab
python3 tools-android-artifact-recovery.py badging game.apk --format text
python3 tools-android-artifact-recovery.py manifest game.apk --tree --xml-out manifest.xml
python3 tools-android-artifact-recovery.py arsc game.apk --filter string
python3 tools-android-artifact-recovery.py dex game.apk --classes
python3 tools-android-artifact-recovery.py elf game.apk --strings
python3 tools-android-artifact-recovery.py abi-matrix game.apk
python3 tools-android-artifact-recovery.py page16k game.apk
python3 tools-android-artifact-recovery.py zip-health game.apk
python3 tools-android-artifact-recovery.py entry game.apk --filter ".so"
python3 tools-android-artifact-recovery.py aab-inspect bundle.aab
python3 tools-android-artifact-recovery.py exported-audit game.apk
python3 tools-android-artifact-recovery.py compat-scan game.apk --out findings.json
python3 tools-android-artifact-recovery.py triage --input logcat.txt
python3 tools-android-artifact-recovery.py report game.apk --outdir evidence/
python3 tools-android-artifact-recovery.py batch ./builds --outdir evidence/batch

# optional lane
export APKTOOL_JAR=/opt/tools/apktool_2.9.3.jar
python3 tools-android-artifact-recovery.py --build-tools-dir "$ANDROID_HOME/build-tools/35.0.0" \
    apktool decode game.apk --outdir work/decoded
python3 tools-android-artifact-recovery.py --build-tools-dir "$ANDROID_HOME/build-tools/35.0.0" \
    verify work/signed.apk

# approval-gated commands (refuse with exit 4 without --confirm)
export ANDROID_TOOLKIT_KS_PASS=...          # never pass secrets on the command line
python3 tools-android-artifact-recovery.py preserve game.apk --outdir originals \
    --authorization "owner approval 2026-11-09" --confirm
python3 tools-android-artifact-recovery.py --build-tools-dir "$BT" \
    rebuild game.apk --patch change.patch --keystore debug.p12 \
    --intent "fix 16 KB alignment" --authorization "owner approval" \
    --outdir work/rebuild --confirm
```

Global options (`--config`, `--format`, `--out`, `--timeout`, `--build-tools-dir`,
`--apktool-jar`) are accepted **before or after** the subcommand.

### 4.5 Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | usage error (unknown command, unknown flag, bad option value, no command) |
| 2 | invalid input / parse failure |
| 3 | required dependency missing |
| 4 | approval required (`--confirm` absent) |
| 5 | partial success / internal error guard |
| 6 | configured limit or timeout reached |
| 130 | interrupted |

---

## 5. Self-test status vocabulary

`self-test` reads `asset-self-test-plan.json` and reports one status per probe:

`FOUND` · `MISSING` · `WRONG VERSION` · `BROKEN` · `LICENSE REQUIRED` ·
`DEVICE REQUIRED` · `NETWORK REQUIRED` · `HOST FEATURE REQUIRED`

Detection rules that matter in practice:

- A tool that prints a banner on **stderr** and exits non-zero (for example `zipalign`
  given an unknown flag) is `FOUND`, not `BROKEN`; a probe is only `BROKEN` when the
  process produced no output at all.
- `zipalign` prints no version string; the build-tools `source.properties`
  `Pkg.Revision` value is used instead.
- `apksigner` ships as a **shell wrapper** around `lib/apksigner.jar`. The tool prefers the
  wrapper and falls back to `java -jar lib/apksigner.jar`.
- `adb` present with no authorized device reports `DEVICE REQUIRED` through the plan's
  `needs` field; a missing `/dev/kvm` reports `HOST FEATURE REQUIRED`.
- `min_version` / `minimum_major` in the plan downgrade a probe to `WRONG VERSION`.

`LICENSE REQUIRED` and `NETWORK REQUIRED` exist in the vocabulary for SDK-licence and
download lanes. This component never downloads anything or auto-accepts licences, so it
does not emit them itself.

---

## 6. Inputs, outputs, parameters

### 6.1 Result envelope

Every command prints one JSON object (or a text rendering with `--format text`):

```json
{
  "ok": true,
  "command": "badging",
  "tool": {"name": "android-artifact-recovery", "version": "1.0.0"},
  "generated_at": "2026-11-09T04:55:00Z",
  "duration_seconds": 0.41,
  "exit_code": 0,
  "data": {},
  "findings": [{"rule_id": "COMPAT-EXPORTED", "severity": "critical",
                "title": "...", "detail": "...", "component": "...",
                "evidence": {}, "remediation": "..."}],
  "artifacts": [{"path": "...", "role": "signed-apk", "size_bytes": 273870,
                 "sha256": "..."}],
  "tool_status": {"apksigner": {"status": "FOUND", "version": "0.9", "path": "..."}},
  "warnings": [],
  "errors": [{"code": "error", "message": "...", "hint": "...", "details": {}}],
  "limitations": []
}
```

- `findings` are sorted critical → info. Severities: `critical`, `high`, `medium`, `low`,
  `info`.
- `artifacts` always carries the sha256 of anything the command produced, so a report is
  self-verifying.
- `limitations` is where the tool states what it could **not** observe. Treat it as part
  of the result, not as noise.
- `--out FILE` writes the same payload to disk; `--format text` renders a readable report.

### 6.2 Command parameters

| Command | Key parameters |
| --- | --- |
| `identify` | positional `inputs...` |
| `zip-health` | `input` |
| `badging` | `input`, `--aapt-crosscheck` |
| `manifest` | `input`, `--member`, `--tree`, `--xml-out` |
| `arsc` | `input` (APK, `resources.arsc`, or decoded dir), `--filter` |
| `dex` | `input` (APK, `.dex`, or dir), `--classes` |
| `elf` | `input` (APK, `.so`, or dir), `--strings` |
| `abi-matrix` | `input` |
| `page16k` | `input` (APK or `.so`) |
| `entry` | `input`, `--filter`, `--extract DIR` |
| `aab-inspect` | `input` |
| `exported-audit` | `input` |
| `compat-scan` | `input` |
| `apktool` | `decode\|build`, `input`, `--outdir`, `--srcdir`, `--output`, `--workdir`, `--no-res`, `--no-src`, `--use-aapt2`, `--force` |
| `sign` | `input`, `--output`, `--keystore`, `--ks-pass`, `--ks-alias`, `--min-sdk`, `--v1`, `--v2`, `--force`, `--confirm` |
| `verify` | `input` |
| `rebuild` | `input`, `--patch`, `--intent`, `--authorization`, `--keystore`, `--ks-pass`, `--ks-alias`, `--outdir`, `--output`, `--workdir`, `--confirm` |
| `preserve` | `inputs...`, `--outdir`, `--note`, `--authorization`, `--force`, `--confirm` |
| `report` | `input`, `--outdir` |
| `batch` | `input` (directory), `--pattern`, `--outdir` |
| `triage` | `--input FILE` or `--text STR` |
| `strings-scan` | `input`, `--needles a,b,c` |

### 6.3 Configuration

`asset-default-config.json` is loaded automatically; pass `--config FILE` to override.
Unknown keys are merged, so a partial override is safe.

```json
{
  "paths":   {"apktool_jar": "", "build_tools_dir": "", "android_home": "", "work_dir": ""},
  "limits":  {"max_parse_bytes": 268435456, "max_entries_listed": 5000,
              "max_artifacts_per_batch": 200, "subprocess_timeout_seconds": 600},
  "signing": {"keystore_env": "ANDROID_TOOLKIT_KS_PASS",
              "keystore_alias_env": "ANDROID_TOOLKIT_KS_ALIAS",
              "refuse_to_overwrite_input": true},
  "policy":  {"min_target_sdk_for_store_upload": 35, "require_exported_from_api": 31,
              "page_size_bytes": 16384}
}
```

`policy.page_size_bytes` drives both `zip-health` and `page16k`; set it to `4096` when
auditing pre-Android-15 hardware requirements.

### 6.4 Compatibility rule schema

`asset-compat-rules.json` is the extension point for `compat-scan`. Rules live in the
`compat_rules` array; `fact_vocabulary` documents the facts the shipped rules use. Three
rule forms:

```json
{"id": "COMPAT-X", "fact": "application.debuggable", "expected": "truthy",
 "severity": "high", "title": "...", "detail": "...", "remediation": "..."}

{"id": "COMPAT-Y", "when": {"target_sdk_lt": 35}, "severity": "medium",
 "title": "...", "detail": "targetSdk is {target}", "remediation": "..."}

{"id": "COMPAT-Z", "check": "exported_missing", "when": {"target_sdk_gte": 31},
 "severity": "critical", "title": "...", "detail": "{count} component(s) ...",
 "remediation": "..."}
```

- Gates in `when`: `target_sdk_lt`, `target_sdk_gte`, `min_sdk_lt`, `abi_missing_all`.
- `fact` addresses the `facts` object (`application.debuggable`, `min_sdk`, ...);
  `expected` may be a literal, `"truthy"` or `"falsey"`.
- `check: "exported_missing"` runs the built-in component probe.
- `{target}` and `{count}` are substituted into `detail`.
- A rule whose gate does not match appears in `data.skipped_rules`, so silence is visible.
- An unknown `check` value raises a warning instead of failing silently.

---

## 7. Capability-by-capability explanation with examples

### 7.1 `identify` — trust structure, not filenames

Reads magic bytes, then the ZIP central directory, and classifies: `apk`, `aab`, `apks`,
`xapk`, `apkm`, `jar`, `arsc-container`, `android-library-archive`, `obb-zip`, `pdf`,
`png`/`jpeg`/`mp4`/`ogg`/`flac`/`mp3`/`wav`, `ktx`/`ktx2`/`dds`, `json`-style `text`,
`xml-text`, `binary-xml`, `java-class`, `android-static-lib`, `elf`, `dex`,
`malformed-zip`, `unknown-binary`, `empty`, `missing`.

```bash
python3 tools-android-artifact-recovery.py identify suspicious.apk
```

```
kind: "text"   notes: ["extension/extension-like mismatch: suffix '.apk' suggests ['apk']"]
```

A mismatch raises `ART-002` (low); an unreadable archive raises `ART-001` (high).

### 7.2 `badging` — the offline `aapt dump badging`

Pure-Python AXML decode, no SDK needed. Reports package, versionCode/Name, min/target/
compile SDK, permissions, requested features, every component with its intent-filters,
launchable activities, DEX list, ABI folders, asset count and signature-file presence.

```bash
python3 tools-android-artifact-recovery.py badging editor.apk --aapt-crosscheck
```

`--aapt-crosscheck` adds `data.aapt_crosscheck` **only when `aapt` exists**; otherwise it
warns and the result stands on the internal parser. Verified against build-tools 34.0.0
`aapt` on real APKs: package, versionCode, versionName, compileSdk, minSdk and targetSdk
matched exactly.

Findings: `MAN-001` (no `uses-sdk`), `MAN-002` (no launcher activity).

### 7.3 `zip-health` — archive forensics

Parses the central directory directly, so data offsets are usable for alignment work.
Reports EOCD/trailer bytes, ZIP64, APK Signing Block presence, duplicate names, path
traversal names, stored vs deflated counts, 4-byte misalignment, and native libraries not
aligned to the configured page size.

```bash
python3 tools-android-artifact-recovery.py zip-health game.apk
```

Findings: `ZIP-001` structural problem, `ZIP-002` duplicates, `ZIP-003` traversal
(critical), `ZIP-004` native libs not page aligned, `ZIP-005` trailing bytes.

### 7.4 `manifest` — binary XML decode

Emits the root attribute map, every component, every intent-filter (actions, categories,
data scheme/host/mimeType) and the raw manifest SHA-256. `--tree` includes the full element
tree; `--xml-out` writes a readable XML rendering.

```bash
python3 tools-android-artifact-recovery.py manifest game.apk --xml-out manifest.xml
```

Attribute names resolve through the AXML resource map, so `android:exported` appears by
name even when the string-pool entry is empty (obfuscated or stripped maps).

### 7.5 `arsc` — resource table

Walks `RES_TABLE` → packages → types → configs → entries and resolves `ResTable_config`
into qualifier labels (`es-rES`, `xxxhdpi`, `v31`, `sw600dp`, `night`, ...). Accepts an
APK, a standalone `resources.arsc`, or a decoded directory.

```bash
python3 tools-android-artifact-recovery.py arsc game.apk --filter string
```

The summary reports entry count, config count, string-pool size, a per-type histogram, the
locale list and the density list.

### 7.6 `dex` — bytecode container

Header version, declared vs actual file size, Adler-32 checksum, SHA-1 signature,
string/type/proto/field/method/class counts, and an optional package histogram decoded
from `type_ids` → `string_ids` (`--classes`).

```bash
python3 tools-android-artifact-recovery.py dex game.apk --classes
```

`DEX-001` flags a checksum/signature mismatch (bytes changed after compilation);
`DEX-002` flags unusual DEX versions.

### 7.7 `elf`, `abi-matrix`, `page16k` — native code

`elf` decodes ELF32/ELF64 headers, `PT_LOAD` segments, `DT_NEEDED`, `DT_SONAME`, `RPATH`
and the GNU build-id, then evaluates 16 KB page readiness:

- 64-bit ABI (`arm64-v8a`, `x86_64`, `riscv64`),
- every `PT_LOAD` has `p_align >= 16384`, and
- `p_vaddr % p_align == p_offset % p_align` for each segment.

`abi-matrix` aggregates per ABI folder and states the device-capability consequence
(64-bit only / both / 32-bit only). `page16k` additionally checks the **ZIP data offset**
alignment, which determines whether the loader can mmap the library straight from the APK.

```bash
python3 tools-android-artifact-recovery.py page16k game.apk
# {"verdict": "PASS", "libraries_checked": 4, "failures": 0, ...}
```

Findings: `ELF-001`, `ABI-001` (no 64-bit library), `ABI-002`, `P16-001`.

**Honesty rules encoded here:** a package with no native code returns `NOT APPLICABLE`,
never `PASS`; a 32-bit-only package is reported as unable to run on 64-bit-only devices;
and the tool never suggests that re-packaging converts 32-bit code to 64-bit.

### 7.8 `aab-inspect` — App Bundles without bundletool

Reads the AAB ZIP structure, then decodes each `<module>/manifest/AndroidManifest.xml`
proto-XML and `BundleConfig.pb`.

The proto manifest decoder is **schema-tolerant on purpose**: AAPT2 releases have emitted
the element-name field at field 2 and at field 3, so the decoder accepts the first
candidate that decodes to a syntactically valid tag name rather than pinning a tool
version. Verified against `bundletool 1.17.2 dump manifest` on a real bundle: package,
versionCode, versionName, minSdk, targetSdk and activity names matched.

```bash
python3 tools-android-artifact-recovery.py aab-inspect app.aab
```

`BUNDLE-METADATA` and `META-INF` are excluded from module accounting so they never appear
as fake modules.

### 7.9 `exported-audit` and `compat-scan` — compatibility rules

`exported-audit` classifies every activity/service/receiver/provider:

| Status | Meaning | Severity |
| --- | --- | --- |
| `missing-explicit-exported` | intent-filter present, no `android:exported`, targetSdk ≥ 31 | critical |
| `exported-unprotected` | explicitly exported, no permission guard | high/medium |
| `implicit-exported` | intent-filter present, targetSdk < 31 (pre-31 default) | medium |
| `ok` / `implicit-not-exported` | safe or not reachable | info |

`compat-scan` evaluates `asset-compat-rules.json` and reports the evaluated count, the
skipped list and the rules version, so a rule that never ran is visible. Shipped rules:
`COMPAT-EXPORTED`, `COMPAT-TARGET-SDK-POLICY`, `COMPAT-CLEARTEXT`, `COMPAT-DEBUGGABLE`,
`COMPAT-BACKUP`, `COMPAT-32BIT-ONLY`, `COMPAT-EXTRACT-NATIVE-LIBS`,
`COMPAT-LEGACY-STORAGE`, `COMPAT-MIN-SDK-BELOW-21`,
`COMPAT-NETWORK-SECURITY-CONFIG`.

### 7.10 `apktool` — decode and rebuild with state

State is written to `<workdir>/apktool-state.json` after every operation, so a later agent
turn can resume without guessing paths. Builds also keep a timestamped log under
`<workdir>/logs/`.

```bash
python3 tools-android-artifact-recovery.py apktool decode game.apk --outdir work/decoded
python3 tools-android-artifact-recovery.py apktool build --srcdir work/decoded \
    --output work/rebuilt.apk
```

Refuses to overwrite a non-empty destination without `--force`. A failed build is reported
as `APT-001` (high) with `stdout_tail`/`stderr_tail` plus the log path.

### 7.11 `sign`, `verify`, `certificate` — identity

`sign` **requires `--confirm`** and **refuses to overwrite the input APK**. The keystore
password comes from `ANDROID_TOOLKIT_KS_PASS` or `--ks-pass` and is handed to apksigner as
`env:APKSIGNER_KS_PASS`, never as a plain argv value. The command always runs
`apksigner verify` afterwards and reports which schemes verified.

`verify` reports `VERIFIED` / `NOT VERIFIED` plus v1/v2/v3 booleans and the signer count.
`certificate` prints signer DNs and SHA-256/SHA-1/MD5 digests; without `apksigner` it
degrades to listing which `META-INF/*.RSA|DSA|EC` files exist and states explicitly that
digests were **not** verified.

### 7.12 `rebuild` — the approval-gated pipeline

Stages, each recorded in `<outdir>/rebuild-ledger.json`:

1. `preserve` — copy the input to `originals/`, `chmod 0440`, record both hashes.
2. `decode` — apktool decode into `decoded/`.
3. `patch` — optional `--patch file.diff`, applied with `patch -p1 --batch --forward`.
4. `build` — apktool build to `<name>-unsigned.apk`.
5. `align` — `zipalign -P <page> -f 4` when supported, otherwise `-f 4` **plus a warning
   that page alignment was not applied**.
6. `sign` — apksigner to `--output` (or `<name>-signed.apk`).
7. `verify` — apksigner verify; `RB-002` (high) if it fails.

The ledger records `--intent`, `--authorization`, the patch hash, tool versions, output
hashes and the verification result. A decode or build failure still writes the ledger, so
a failed attempt is evidence too.

### 7.13 `preserve`, `report`, `batch`, `triage`, `strings-scan`

- `preserve` (approval-gated) writes read-only copies plus `preservation-record.json` with
  source path, copy path, size, both hashes and the authorization note.
- `report` produces `<stem>-report.md` and `<stem>-report.json`: identity table, resources,
  archive facts, native coverage, findings with remediation, and a *Limits and untested
  claims* section that always states no device run occurred.
- `batch` sweeps a directory (default `*.apk,*.aab,*.apks,*.xapk`), isolates failures as
  `status=error` rows instead of aborting, and writes `batch-index.csv` +
  `batch-index.json`.
- `triage` matches 18 known failure signatures (signature mismatch, no matching ABI,
  `UnsatisfiedLinkError`, manifest malformed, cleartext blocked, ANR, tombstone, ...),
  returns the earliest actionable stage, and warns when nothing matched.
- `strings-scan` performs a bounded substring scan for toolchain breadcrumbs (default
  needles: `Android`, `ndk`, `clang`, `apktool`, `kotlin`, `Unity`).

---

## 8. Safety and approval rules

1. **Approval is enforced in code, not in prose.** `sign`, `rebuild` and `preserve` raise
   `ApprovalRequired` and exit 4 unless `--confirm` is present.
2. **Originals are never overwritten.** `sign` rejects an `--output` equal to the input;
   `rebuild` copies the input into `originals/` before any mutation; `preserve` writes
   read-only copies.
3. **Secrets stay out of argv.** Keystore passwords come from the environment and are
   forwarded to apksigner as `env:` references.
4. **Extraction is traversal-safe.** `entry --extract` resolves each target and refuses
   anything escaping the output directory (`ENT-001`, `ENT-002`).
5. **No network, no arbitrary execution.** The tool never downloads anything. The only
   subprocesses are `java -jar apktool`, `apksigner`, `zipalign`, `patch` and version
   probes, each with an explicit timeout.
6. **Scope is stated, not assumed.** Modification work expects `--intent` and
   `--authorization`; the ledger persists them next to the artifact hashes.
7. **Refusal is a feature.** Requests to bypass authentication, licensing, DRM, device
   integrity or certificate protections are out of scope; there is no code path for them,
   and `asset-agent-prompts.json` forbids the claim.

---

## 9. Limits and unavailable integrations

Measured limits of this component — not placeholders.

| Missing capability | Status in the tested environment | What the tool does instead |
| --- | --- | --- |
| Device install / launch / runtime tests | `adb` MISSING, no device attached | `doctor` and `self-test` report `DEVICE REQUIRED`; reports say runtime is untested |
| Emulator lanes | `/dev/kvm` absent → `HOST FEATURE REQUIRED` | `doctor` warns; no emulator is bundled |
| SDK licence acceptance / unattended SDK install | Not implemented by design | `LICENSE REQUIRED` exists in the vocabulary; acceptance is never automated |
| `bundletool` (`build-apks`, APK-set generation) | Not bundled | `aab-inspect` decodes structure and manifests only |
| JADX / baksmali / dex2jar decompilation | Not implemented | `dex` reports headers and class-name inventory only |
| Frida / dynamic instrumentation | Not implemented by design | Out of scope (see §8) |
| Media transcoding, ASTC/KTX texture conversion | Not implemented | `identify` classifies containers; no conversion is claimed |
| `zipalign -P` (16 KB page flag) | Needs build-tools 35+; host had 34.0.0 | Falls back to `-f 4` **and warns** that page alignment was not applied |
| `android:exported` enforcement on pre-31 targets | Platform does not enforce it | Reported as `implicit-exported` (medium) with a warning |
| Signature verification without `apksigner` | Not possible in pure Python for v2/v3 | `certificate` degrades to a v1 file listing and says digests were not verified |
| `.apks` / `.xapk` / `.apkm` install planning | Format detection only | `identify` classifies them; no install plan is produced |
| Hosted or catalog resources | None referenced | Every path in this package is relative and local |

**Statements the tool will never make:** that a rebuilt APK can update an installed app
signed with another key; that 32-bit code is now 64-bit; that a package is 16 KB ready
without both ELF and ZIP alignment evidence; that a runtime test passed when no device was
used.

---

## 10. Validation and tests actually completed

### 10.1 Harness

```bash
python3 tools-android-artifact-recovery-tests.py \
    --apk /path/to/real.apk --aab /path/to/real.aab \
    --build-tools-dir "$ANDROID_HOME/build-tools/34.0.0" \
    --apktool-jar /path/to/apktool_2.9.3.jar
```

**Result: 103 passed, 0 failed** (Python 3.13.16, OpenJDK 11.0.18, build-tools 34.0.0,
apktool 2.9.3). Groups:

1. **Core parsers** — hand-assembled AXML (with and without `android:exported`), minimal
   `resources.arsc` with two configs, DEX with a corrupt Adler-32, ELF64 with 4 KB and
   16 KB `PT_LOAD` alignment, proto-XML manifest for the AAB decoder.
2. **ZIP layout** — clean archive, duplicate names, `../` traversal, truncated file.
3. **CLI contract** — `--help`, `capabilities` (24 categories, 324 skills, all assets
   present), missing input → exit 2, non-APK → exit 2 with detected kind, unknown command
   and bad option value → exit 1, `--out` writing.
4. **Artifact commands** — identify / badging / exported-audit / compat-scan / zip-health /
   dex / page16k / triage / report on generated fixtures, plus real-artifact cross-checks.
5. **Approval gates** — `sign`, `rebuild`, `preserve` all refuse with exit 4.
6. **Preserve and batch honesty** — custody record hash equality, mode `0440`, a repeated
   preserve that does not reuse the read-only copy, and `batch` marking non-packages
   `status=skipped` instead of `ok`.
7. **Mutation** — two fixtures differing in package, targetSdk and `exported` produce
   different findings; explicit `exported="false"` yields no critical finding.
8. **Host lane** — self-test statuses, apktool decode (manifest + state file), apktool
   rebuild to APK.

### 10.2 Manual verification performed

| Check | Evidence |
| --- | --- |
| `badging` vs `aapt dump badging` | Package, versionCode, versionName, compileSdk, min/target SDK identical on real APKs |
| `aab-inspect` vs `bundletool dump manifest` | Package, versionCode, versionName, min/target SDK and activity list identical |
| Real APK parsing | 12.4 MB F-Droid client: 1119 entries, 59 444 resource entries, 123 configs, 2 DEX (classes.dex + classes2.dex), 4 ABI folders (arm64-v8a, armeabi-v7a, x86, x86_64), 61 components, 29 permissions, 8 assets |
| Real APK badging vs aapt | F-Droid: package/versionCode/versionName/minSdk 23/targetSdk 30 and editor: 1.98/198/minSdk 21/targetSdk 35 all identical to `aapt dump badging` |
| AAB module discovery | bundle reports 4 modules: base, assets, initialInstall, java |
| Full `rebuild` pipeline | preserve → decode → patch → build → align → sign → verify all `ok`; v1+v2+v3 verified |
| Patch took effect | Rebuilt package reports `versionName 1.98-recovered` vs original `1.98` |
| Original untouched | Input sha256 identical to the preserved copy after the run |
| Preserved permissions | `chmod 0440` confirmed via `stat` |
| apktool round-trip | Decoded → rebuilt APK preserves package, version, minSdk and all 4 components |
| Error paths | Missing input (2), non-APK (2), approval gates (4), missing keystore password (2), missing tool (3) |
| `page16k` on a real APK | `verdict PASS`, 4 libraries, 0 failures |
| `batch` | 5 files (3 real artifacts + 2 junk): `analyzed 3 / skipped 2 / failures 0`, CSV + JSON written, no crash |
| `verify` / `certificate` | `VERIFIED {"v1": true, "v2": true, "v3": true}`, signer DN + 3 digests |
| Missing-asset behaviour | `capabilities` reports each asset's presence; the dependent command degrades |

### 10.3 What was **not** tested

- Installation, launch, ANR and crash behaviour on a real device or emulator (no device,
  no KVM).
- `zipalign -P` (16 KB page flag): the tested build-tools release was 34.0.0, which has no
  `-P`; only the fallback path and its warning were exercised.
- `.apks` / `.xapk` / `.apkm` handling beyond format detection.
- Windows and macOS hosts. The core lane avoids POSIX-only calls where practical, but
  `doctor` degrades on non-POSIX systems.

---

## 11. Troubleshooting and recovery

| Symptom | Cause | Recovery |
| --- | --- | --- |
| `apktool is MISSING` (exit 3) | no JAR found | set `APKTOOL_JAR` or pass `--apktool-jar` |
| `apksigner is MISSING` | build-tools not on PATH | pass `--build-tools-dir` or set `ANDROID_HOME` |
| `zipalign is BROKEN` | probe produced no output | re-extract build-tools; confirm the binary is executable |
| `keystore password not provided` | env var unset and no `--ks-pass` | export `ANDROID_TOOLKIT_KS_PASS` |
| `refusing to overwrite the input APK` | `--output` equals `--input` | sign to a new path; keep the original |
| `refusing to overwrite <path>` in `preserve` | the copy already exists (a prior run left it read-only) | use a fresh `--outdir`, or `--force` when replacing is intended |
| `approval required for action: sign` (exit 4) | `--confirm` missing | re-run with `--confirm` after recording authorization |
| apktool build fails (`APT-001`) | resource or smali error | read `stdout_tail`/`stderr_tail` and the log path; fix and rebuild |
| Patched rebuild still shows the old versionName | patch targeted `AndroidManifest.xml` | apktool stores `versionName`/`versionCode` in `apktool.yml`; patch that file |
| `page16k` FAIL after a rebuild | old `zipalign` without `-P` | rebuild with build-tools 35+; the fallback warning already said so |
| `badging` reports `MAN-002` | no MAIN/LAUNCHER filter | expected for library or split APKs; ignore |
| `compat-scan` skips a rule | its `when` gate did not match | check `data.skipped_rules` and the declared `targetSdk` |
| `unexpected internal error` (exit 5) | toolkit defect | `details.traceback_tail` holds a 6-line trace; report it with the artifact kind |

**Recovery pattern for a damaged artifact:** `identify` → `zip-health` (structural
diagnosis) → if `malformed-zip`, stop and report; do not extract. A truncated archive
cannot be repaired by this tool and must be re-fetched from an authorized source.

**Recovery pattern for a failed rebuild:** the ledger is written even on failure. Read
`stages[]` to find the first `ok: false`, then fix that stage: decode failures usually mean
mismatched framework files, patch failures mean the diff did not apply cleanly, and build
failures name the offending resource or smali file.

---

## 12. Extension and maintenance

**Add a compatibility rule** — edit `asset-compat-rules.json` (see §6.4) and bump
`version`. No code change is needed; `compat-scan` reports `rules_version` and
`rules_evaluated`.

**Add a self-test probe** — edit `asset-self-test-plan.json`:
`{"id": "...", "tool": "<key>", "args": [...], "required": bool, "needs": "device"|"host_feature", "min_version": "x.y"}`.
Tool keys resolved by `resolve_tools`: `python3`, `java`, `apktool`, `aapt`, `aapt2`,
`apksigner`, `zipalign`, `dexdump`, `adb`, `fastboot`, `bundletool`, `patch`, `kvm`.

**Add a failure signature** — append to `TRIAGE_SIGNATURES` in the primary tool:
`(regex, stage, severity, title, next_step)`.

**Add an artifact type** — extend `detect_artifact`/`_classify_zip` in
`tools-ax-parsers.py` and the `ext_expected` table so extension mismatches are caught.

**Add a command** — write a `cmd_*` handler returning an `Envelope`, register it in
`COMMAND_HELP` and `HANDLERS`, and add a subparser in `build_parser` (use the `add()`
helper, which already applies the common options).

**Periodic review** (suggested quarterly): bump `policy.min_target_sdk_for_store_upload`
and `policy.require_exported_from_api` when store policy changes; re-run the harness with
`--apk`/`--aab` fixtures; re-check `aab-inspect` against `bundletool dump manifest` if a
new AAPT2 changes the proto field numbering (the tolerant decoder should absorb it, but
confirm).

**Invariants to preserve when editing:**

- exactly one `knowledge-readme.md`; no internal subfolders; every file prefixed
  `tools-`, `knowledge-` or `asset-`;
- no `SKILL.md` in this folder;
- all references relative (`android-artifact-recovery/asset-...`), forward slashes;
- approval gates on `sign`, `rebuild`, `preserve`;
- one JSON envelope per command, and `limitations` populated whenever a claim is
  unverified.
