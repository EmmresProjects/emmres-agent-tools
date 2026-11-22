# Android Agent Toolkit (v1.0.0) — README

A stdlib-only Python 3 command-line toolkit for Android engineering on Linux x86_64. It implements the 324 skills of the *Android Agent Skill Catalog* (24 categories, Nov 2026) as one structured CLI, with local pure-Python analysis, a whitelisted adapter registry for external SDK tools, and honest status reporting when a tool, device, licence, network or host feature is missing.

Entry point: `tools-android-agent-toolkit.py`

---

## 1. Purpose

- Analyse APK, AAB, APKS, XAPK and APKM artifacts without installing them (ZIP health, manifest, resources, DEX, ELF, signing, ABI/16 KB page alignment, media).
- Run a startup self-test that reports what the host can and cannot do, using the status vocabulary below.
- Triage crash, ANR, native tombstone and installer logs; retrace obfuscated stack traces.
- Check storage (SQLite, asset folders, file formats), static security heuristics, compatibility rules, and recovery plans.
- Preserve evidence: SHA-256 provenance records, originals snapshots, release ZIPs, and comparison reports.
- Route external tools (git, apktool, adb, java, gradle, jadx, …) only through a fixed whitelist, with approval gates and timeouts.

The toolkit does **not** bypass DRM, licence checks or authentication; it does not sign, publish, flash, or modify devices. Those actions are either not implemented or are gated by `--yes` and are reported as `DEVICE REQUIRED`, `LICENSE REQUIRED` or `MISSING` when the requirement is absent.

---

## 2. Folder layout (flat, no subfolders)

```
android-agent-toolkit/
  knowledge-readme.md              this file
  tools-android-agent-toolkit.py   entry point (CLI)
  tools-toolkit-core.py            shared core (errors, config, approval, subprocess runner)
  tools-artifact-analysis.py       APK/AAB/DEX/AXML/ARSC/ELF/signing/ABI/media analysis
  tools-tool-adapters.py           whitelisted external tool registry, device (adb) listing
  tools-host-environment.py        host profile, SDK, JDK routing, build inspection, integrity pins, self-test
  tools-triage-reporting.py        triage, retrace, storage, security, compat, preserve, recovery
  tools-catalog-engine.py          catalog and capabilities queries
  asset-catalog-skills.json        324 skills, 24 categories, tiers, requirements
  asset-compat-matrix.json         API levels, 37 behaviour rules, SDK matrix kinds, installer error codes
  asset-default-config.json        limits, approval rules, paths, exit codes, scan patterns, reporting
  asset-tool-registry.json         93 whitelisted tools with version probes and read-only argument rules
  asset-selftest-suite.json        42 startup self-test entries (14 critical)
  asset-sample-failures.txt        sample crash/ANR/tombstone/installer log for triage
```

Keep all files together. The entry point finds its siblings by path, so it works from any current directory as long as the folder is intact.

---

## 3. Setup and dependencies

| Item | Requirement | Status in the tested sandbox |
|---|---|---|
| Python | 3.8+ (stdlib only, no pip packages) | Python 3.13.16 used for all tests |
| OpenJDK | 17 baseline (21 accepted by AGP/Gradle) | Only JDK 11 present → `WRONG VERSION` |
| apktool | `apktool` on PATH, or `APKTOOL_JAR=/path/apktool-<version>.jar` (pin it) | **MISSING** |
| git, zip, unzip, file, strings, readelf, objdump | baseline host tools | FOUND |
| adb (platform-tools) | optional, for `device list` | **MISSING** |
| Android SDK | `ANDROID_HOME` or `ANDROID_SDK_ROOT` | **not set** → `MISSING` |
| `/dev/kvm` | emulator acceleration | absent → `HOST FEATURE REQUIRED` |

No Python packages are needed at runtime. Build-time helpers used during development (PDF text extraction) are not part of the package.

Set optional environment variables when needed:

```bash
export ANDROID_HOME=/path/to/Android/Sdk      # SDK root (sdk *, selftest tool checks)
export JAVA_HOME=/path/to/jdk-17             # JDK 17 for sdk jdk-route / Gradle
export APKTOOL_JAR=/path/to/apktool-2.x.jar  # apktool via jar
```

Run commands from the directory that contains the `android-agent-toolkit/` folder:

```bash
python3 android-agent-toolkit/tools-android-agent-toolkit.py --version
```

Do not run the `tools-*.py` modules directly; only the entry point registers the shared core.

---

## 4. Global options and exit codes

Global options may appear before or after the group name:
`--config PATH` (user JSON, deep-merged over the asset defaults), `--json` (structured output), `--out PATH` (output file or directory, command dependent; default `./out`), `--yes` (approve destructive or state-changing actions), `--verbose`, `--version`.

| Exit | Meaning |
|---|---|
| 0 | OK (informational states such as FOUND, PASS, VALID, DRY RUN, CLEANED) |
| 1 | Error: not found, parse failure (including malformed `--config` JSON), limit exceeded, failed check, internal |
| 2 | Usage or invalid input (wrong file type, bad option combination, non-container input) |
| 3 | Approval required (re-run with `--yes` after confirming) |
| 4 | Missing requirement (tool, device, network, licence, host feature) |

Exit-code rule: probes that only report presence (`tool locate`, `sdk jdk-route`, `build inspect`) exit 0 and carry the status. Commands whose job depends on an absent requirement (`device list`, `sdk avds`, `sdk inventory`, `sdk licenses`, `sdk footprint`, and `tool run` on a missing tool) exit 4.

### JSON envelope

With `--json`, every command prints one object:

```json
{
  "ok": true,
  "command": "artifact zip-health",
  "status": "OK",
  "data": { "...": "command-specific fields" },
  "warnings": [],
  "error": null
}
```

Usage errors (missing or invalid arguments) under `--json` return the same envelope with `command` set to `usage` and exit 2. On failure, `ok` is `false` and `error` is `{ "code": "E_…", "message": "…", "hint": "…", "status": "…", "data": {…} }`. Internal `_text` rendering keys are removed from JSON output.

### Status vocabulary

`FOUND`, `MISSING`, `WRONG VERSION`, `BROKEN`, `LICENSE REQUIRED`, `DEVICE REQUIRED`, `NETWORK REQUIRED`, `HOST FEATURE REQUIRED`, plus command states such as `OK`, `PASS`, `FAIL`, `UNKNOWN`, `VALID`, `DRY RUN`, `CLEANED`, `NOT RUN`.

---

## 5. Capabilities (catalog)

The catalog covers all 324 skills in 24 categories. Each skill has a tier:

| Tier | Count | Meaning |
|---|---|---|
| local | 144 | implemented in pure Python inside this toolkit |
| adapter | 99 | runs a whitelisted external tool; reports MISSING if absent |
| composite | 54 | combines local and adapter steps |
| knowledge | 27 | reference guidance only; no executable step |

Categories (skill count):
1. Android Environment Bootstrap and Fleet Readiness (12)
2. Android SDK and Platform Management (10)
3. APKTool, APK, AAB, APKS, XAPK, and APKM Handling (12)
4. Manifest, Resources, AAPT, and Packaging Semantics (12)
5. DEX, Smali, Java, Kotlin, and Bytecode Analysis (16)
6. Build Systems and Source Project Recovery (14)
7. Native Android, NDK, CMake, and ABI Engineering (15)
8. ELF, ARM, Binary Inspection, and Controlled Emulation (13)
9. Signing, Certificates, Identity, and Integrity (12)
10. Devices, ADB, Fastboot, and Physical Test Operations (14)
11. Emulator, Virtualization, and Test Matrix Operations (12)
12. UI, Instrumentation, and End-to-End Test Automation (13)
13. Crash, ANR, Native Tombstone, and Failure Triage (12)
14. Storage, Databases, OBB, Assets, Backups, and Saves (14)
15. Network, TLS, APIs, and Authorized Offline Compatibility (15)
16. Security, Privacy, and Authorized Dynamic Analysis (14)
17. Media, Texture, Audio, Video, and Game Assets (14)
18. Compatibility Knowledge and API Behavior (14)
19. Preservation, Provenance, and Reproducible Release Packaging (12)
20. Automated Reports, Decision Engines, and Agent Workflows (13)
21. Legacy Application and Game Recovery Scenarios (12)
22. Quality, Validation, and Catalog Governance (14)
23. Additional High-Value Android Engineering Skills (18)
24. Startup Self-Test and Operational Status Skills (17)

Requirements that gate a skill: `approval` (55), `device` (34), `tool:adb` (27), `network` (9), `tool:gradle` (8), `tool:apktool` (5), `credentials` (5), `tool:jadx` (4), `tool:ndk` (4), `tool:emulator` (4), `host:kvm` (4), `authorization` (4), plus others.

---

## 6. Command reference with examples

All examples assume the current directory is the parent of `android-agent-toolkit/`. Shorthand: `T=android-agent-toolkit/tools-android-agent-toolkit.py`.

### 6.1 Version, help, capabilities

```bash
T=android-agent-toolkit/tools-android-agent-toolkit.py
python3 $T --version                       # 1.0.0
python3 $T --help
python3 $T capabilities                    # all 324 skills
python3 $T capabilities --category 13      # one category (1–24)
python3 $T capabilities --requires device  # filter by requirement
```

### 6.2 catalog

```bash
python3 $T catalog stats
python3 $T catalog list --category 5
python3 $T catalog list --search signing
python3 $T catalog search "ELF"
python3 $T catalog show 01.01              # one skill by id
python3 $T catalog contract 01.01          # input/output contract for a skill
python3 $T catalog validate                # every route resolves; 324 skills, 24 categories
```

### 6.3 host, sdk, build

```bash
python3 $T host profile                    # platform, CPUs, RAM, disk
python3 $T host env                        # ANDROID_HOME, JAVA_HOME, etc.
python3 $T host limits                     # effective limits after --config
python3 $T sdk inventory [--health] [--caches]   # exit 4 when no SDK root
python3 $T sdk licenses                    # exit 4 when no SDK root
python3 $T sdk footprint
python3 $T sdk avds                        # DEVICE REQUIRED when no AVD
python3 $T sdk jdk-route                   # WRONG VERSION when only JDK 11
python3 $T sdk matrix --kind build-tools   # kinds: platforms, build-tools, ndk, system-images
python3 $T build inspect <project-dir>     # Gradle/Maven/CMake detection, wrapper check
python3 $T build compat                    # build compatibility notes
```

### 6.4 integrity (SHA-256 pins)

```bash
python3 $T integrity verify --files app.apk
python3 $T integrity pins-init                     # creates toolkit-pins.json (approval if it exists)
python3 $T integrity pins-add --files app.apk
python3 $T integrity pins-verify                   # exit 1 on mismatch or missing file
```

### 6.5 selftest

```bash
python3 $T selftest run                    # full suite; writes selftest-status.json under --out (creates --out if missing)
python3 $T selftest run --only tool-apktool
python3 $T selftest status                 # last stored result
```

Critical entries: `builtin-axml-package`, `builtin-dex-header`, `builtin-elf-machine`, `builtin-zip-fixture`, `builtin-triage-sample`, `tool-apktool` (first repair), `tool-java`, `tool-git`, `tool-zip`, `tool-unzip`, `tool-file`, `tool-strings`, `tool-readelf`, `tool-objdump`. A critical failure exits 1 and still prints the full table.

### 6.6 workspace (dry-run by default)

```bash
python3 $T workspace clean --target ./my-project     # dry run: lists items matching workspace_clean globs
python3 $T workspace clean --target ./my-project --yes
```

Deletion requires `--yes`. Keystores, sources and other non-cache files are never matched. The filesystem root and the home directory are refused.

### 6.7 artifact (read-only analysis)

```bash
python3 $T artifact detect app.apk                 # family, subtype, declared-vs-actual type
python3 $T artifact zip-health app.apk [--align] [--no-crc]
python3 $T artifact pkginfo app.apk                # package, versionCode, SDK levels, sources
python3 $T artifact axml AndroidManifest.xml       # compiled (binary AXML) manifest only; text XML is rejected with E_PARSE
python3 $T artifact pkginfo ./decoded-app          # apktool-decoded directory (text manifest), components and launcher activities
python3 $T artifact arsc app.apk                   # resources.arsc packages
python3 $T artifact dex classes.dex [--strings-filter TEXT]
python3 $T artifact elf lib.so [--symbols]         # class, machine (AArch64 etc.), imports/exports
python3 $T artifact signing app.apk [--compare other.apk]   # schemes v1/v2/v3, cert summary
python3 $T artifact abi app.apk [--page-size]      # ABIs, 16 KB page alignment (ELF evidence)
python3 $T artifact media app.apk                  # image/audio/video details
python3 $T artifact inventory app.apk [--sizes]    # entry counts and size breakdown
```

### 6.8 package

```bash
python3 $T package splits-resolve bundle.xapk --abi arm64-v8a --api 30 --density 480 --locale en
python3 $T package normalize bundle.xapk --out ./unpacked [--script] --yes   # XAPK/APKM/APKS only
python3 $T package convert-advice app.apk          # supported routes and not-supported routes
python3 $T package manifest-diff old.apk new.apk
```

`splits-resolve` on a plain APK returns `E_INPUT` (exit 2) by design: it expects an APKS/XAPK/APKM set or an extracted split directory. `normalize` writes an `install-plan.json` next to the extracted members.

### 6.9 triage

```bash
python3 $T triage analyze asset-sample-failures.txt        # blocks: java_crash, anr, native_tombstone, native_link_failure, installer_failure
python3 $T --json triage analyze crash.log anr.txt
python3 $T triage retrace --mapping mapping.txt --trace trace.txt   # R8/ProGuard retrace
```

Blocks are split on `--- BLOCK <n>: <title> ---` headers. A log without headers is treated as one block, so add a header before appending a new failure to a log. Classification is weighted-signal scoring; a block can carry several kinds (`kinds`, primary `kind`) and reports `fields`, `findings` and `suggestions`.

### 6.10 storage

```bash
python3 $T storage sqlite game.db                 # integrity_check, schema summary
python3 $T storage sqlite game.db --diff old.db   # tables added/removed, column changes, row counts
python3 $T storage assets ./assets                # file types, duplicate groups
python3 $T storage validate saves.json            # JSON/SQLite/PNG validators; UNKNOWN when no validator
```

### 6.11 security (static heuristics)

```bash
python3 $T security scan app.apk                  # manifest flags, secret-like patterns, cleartext
python3 $T security scan --decoded ./decoded-app  # apktool-decoded tree (apk optional)
```

Output is a static heuristic list, not a vulnerability assessment. Expect informational false positives (for example, an SVG `xmlns` string flagged as cleartext HTTP).

### 6.12 compat

```bash
python3 $T compat check app.apk                              # minSdk/targetSdk and applicable rules
python3 $T compat check app.apk --device-api 23 --release --out compat.md
python3 $T compat levels                                      # API level table
python3 $T compat matrix                                      # test-matrix lanes, AGP/JDK pairing
python3 $T compat rules --topic installer                     # topics include 64bit, page-size-16kb, webview, tls, …
```

### 6.13 preserve (evidence)

```bash
python3 $T preserve provenance --files app.apk --out ./evidence --tools [--note "rc1"]   # provenance-manifest.json
python3 $T preserve snapshot --files app.apk --out ./snap      # copies to snap/originals/ and verifies SHA-256
python3 $T preserve package --files app.apk notes.txt --out ./release.zip --yes
python3 $T preserve report --apk app-v1.apk app-v2.apk --out compare.md
python3 $T preserve report --gate selftest-status.json          # release gate; exit 1 on blocking items
```

- `--out` for `snapshot` must have an existing parent directory; the toolkit creates only the leaf.
- Writing into a non-empty snapshot directory requires `--yes` (exit 3 otherwise). Files already stored under `originals/` are immutable evidence: a name clash is rejected with `E_INPUT` (exit 2) even with `--yes`. Snapshot the new file under a different name, or remove the stale snapshot deliberately first.
- Duplicate input names are rejected with `E_INPUT`. The name `provenance-manifest.json` is reserved.

### 6.14 recovery

```bash
python3 $T recovery assess app.apk                # ordered plan from read-only evidence
```

A damaged or non-ZIP container returns `E_PARSE` (exit 1), and a non-container input returns `E_INPUT` (exit 2).

### 6.15 tool (whitelisted adapters)

```bash
python3 $T tool list                              # 93 registry tools; installed count
python3 $T tool locate apktool                    # FOUND / MISSING with install hint
python3 $T tool run git -- --version              # read-only argument rules → no approval
python3 $T tool run git -- status --porcelain
# Non-read-only arguments (for example push, commit, tag) need --yes; they are not run without it.
```

Rules: only registry names run; `shell=True` is never used; every call has a timeout and an output cap; read-only argument patterns run without approval; anything else needs `--yes`.

### 6.16 device (read-only)

```bash
python3 $T device list                            # adb devices; exit 4 if adb missing or no ready device
python3 $T --json device list
```

No device-state change commands (install, flash, wipe, shell writes) are implemented.

---

## 7. Safety rules

1. **Originals are preserved.** Analysis commands read files; `preserve snapshot` copies and verifies SHA-256 before anything else is written.
2. **Approval gate.** `delete`, overwrite of non-empty output, non-read-only tool runs, device-state change, signing, publishing and credential use require `--yes`. Without it the toolkit returns exit 3 and changes nothing.
3. **No shell execution.** External commands use argument lists with `shell=False`, a timeout, and a capped output buffer.
4. **Whitelist only.** `tool run` accepts only the 93 registry names.
5. **No DRM, licence or authentication bypass.** Licence-gated SDK items report `LICENSE REQUIRED`.
6. **No 64-bit claim without evidence.** `artifact elf` and `artifact abi` report the class and machine of the library that was actually inspected. A 64-bit claim applies only to that library, not to the whole app or to runtime behaviour.
7. **Install success is not runtime compatibility.** The compat tools reason from manifest and ELF evidence and rule text; they do not run the app.
8. **Static heuristics are not proof.** Security findings are labelled as heuristics.
9. **Path safety.** Input and output paths are checked; ZIP path traversal (`../`) is reported as `path-traversal`.

---

## 8. Schemas and input/output summary

| Input | Accepted | Output |
|---|---|---|
| APK, AAB, APKS, XAPK, APKM, ZIP | `artifact *`, `package *`, `preserve *`, `recovery assess` | JSON `data` in the envelope |
| Binary AXML, DEX, ELF, PNG/other media | `artifact axml|dex|elf|media` | structured fields |
| apktool-decoded directory (text AndroidManifest.xml) | `artifact pkginfo <dir>`, `security scan --decoded <dir>` | manifest summary, components, launcher activities |
| Log text files | `triage analyze` | blocks with `kind`, `findings`, `suggestions` |
| Mapping + stack trace text | `triage retrace` | `retraced_text`, `frames_resolved`, `frames_unresolved` |
| SQLite database | `storage sqlite` | `integrity`, schema, diff |
| Directory of files / decoded tree | `storage assets`, `security scan --decoded`, `build inspect` | inventory, findings |
| Results JSON (self-test status) | `preserve report --gate` | `pass`, `rows`, `blocking` |
| `--config` JSON | any command | merged configuration (invalid JSON → E_PARSE, exit 1) |

Key output files:
- `selftest-status.json` (under `--out`): suite id, per-entry results, summary, critical failures.
- `toolkit-pins.json` (pin store): SHA-256 pins for `integrity`.
- `provenance-manifest.json`: file names, sizes, SHA-256, tool versions, notes.
- `install-plan.json`: from `package normalize`.
- Markdown reports: `preserve report --out *.md`, `compat check --out *.md`.

---

## 9. Validations actually completed

These were run through the real CLI in this sandbox (Python 3.13.16, Linux x86_64, 2 CPUs, ~1.9 GiB RAM):

- `--version`, `--help`, `capabilities` (text, `--json`, `--category 13`, `--requires device`, `--requires host:kvm`).
- `catalog validate`, `stats`, `show`, `contract`, `search`, `list`: 324 skills, 24 categories, all routes resolve.
- `host profile`, `host env`, `host limits`; `--config` valid and malformed (malformed JSON → E_PARSE, exit 1) with the flag placed before the group, after the group, and in the middle.
- `sdk jdk-route` (WRONG VERSION: JDK 11 only), `sdk inventory`, `sdk licenses` (exit 4), `sdk footprint`, `sdk avds` (DEVICE REQUIRED), `sdk matrix --kind build-tools`, unknown kind → exit 2.
- `build inspect` (Gradle without wrapper → warning), `build compat`.
- `integrity verify`, `pins-init` (second run → exit 3), `pins-add`, `pins-verify` (match, changed file → exit 1, missing file → exit 1, missing `--files` → exit 2).
- `selftest run` (full: exit 1 because critical `tool-apktool` MISSING, `tool-java` WRONG VERSION; full table printed), `selftest run --only` per entry, `selftest status`.
- `artifact detect`, `zip-health` (with and without `--align`/`--no-crc`), `pkginfo`, `axml`, `arsc`, `dex`, `elf --symbols`, `signing --compare`, `abi --page-size` (directory input too), `media`, `inventory --sizes`; on a real F-Droid APK (973 entries, 0 bad CRCs, v1+v2+v3 signing, four ABIs, 16 KB-aligned libraries).
- `artifact detect` on a truncated or non-archive file; wrong file type → exit 2; missing file → exit 1.
- Synthetic XAPK set: `package splits-resolve` (chooses arm64 ABI and xxhdpi density splits), `package normalize` (extract + `install-plan.json`; approval required when output is non-empty).
- `package convert-advice`, `package manifest-diff` (identical → 0 differences), `splits-resolve` on plain APK → exit 2.
- `triage analyze` on the sample log (5 blocks, correct kinds, text and JSON), using a relative and an absolute path, and a missing file → exit 1; `triage retrace` (3 of 4 frames resolved, line 7 → 73); mapping-as-trace rejected.
- `storage sqlite` (integrity, `--diff`, missing DB exit 1, non-SQLite exit 1); `storage assets` (duplicate group detected); `storage validate` (JSON FOUND, binary UNKNOWN, SQLite quick_check).
- `security scan` on the APK (13 findings: 2 medium, 11 informational) and `--decoded` on a synthetic tree (debuggable flag and AWS-key pattern detected).
- `compat check` (minSdk 24, targetSdk 37, 23 rules; with `--device-api 23 --release --out` → INSTALL_FAILED_OLDER_SDK), `compat levels`, `compat matrix`, `compat rules --topic installer`, unknown topic → exit 1.
- `preserve provenance` (with `--tools`; repeat without `--yes` → exit 3; with `--yes` → OK), `preserve snapshot` (copy + SHA-256 verify; missing parent → exit 1; repeat → exit 3), `preserve package` (approval for overwrite; reserved name → exit 2; `--out` with non-zip extension → exit 2), `preserve report --apk` (duplicate input compared once), `preserve report --gate` (blocking item → exit 1).
- `recovery assess` on the real APK (no blocking issues), on a truncated ZIP (E_PARSE exit 1), on a text file (exit 2), on a missing file (exit 1).
- `tool list`, `tool locate` (git FOUND, apktool MISSING with install hint), `tool run git -- --version` (OK), `tool run git -- push` (approval exit 3), `tool run apktool` (MISSING exit 4), `tool run nosuchtool` (not whitelisted, exit 1), `tool run` without name (exit 2).
- `workspace clean` (dry-run default, `--yes` deletes only cache/build items, keystore and sources kept, root refused).
- `device list`: no adb (exit 4); with a test-double `adb` on PATH: no device → DEVICE REQUIRED exit 4; one `device` and one `unauthorized` → FOUND with `ready_count` 1.

Test doubles (for example, the fake `adb` used for device-state parsing) were used only outside the package. The package contains no test fixtures except the embedded self-test blobs.

Final pass on the packaged ZIP (extracted to a clean location and re-run):
- 93-command table covering every group, with expected exit codes (valid, invalid, missing and wrong-type input).
- Changed-input checks: integrity verify and pins-verify flip on file change; provenance SHA-256 follows content; triage block count rises when a block is added; security findings differ between debuggable and hardened manifests; sqlite diff reports only the changed table.
- 80 `--json` envelopes parsed; `ok` matches exit status 0.
- 78 README command examples parsed; each group and subcommand exists and each documented flag is accepted by its help text. Every file named in the layout section exists and matches the folder.
- 12 relative-path commands run from a clean temp skill root with one test SKILL.md beside the folder.

Re-validation pass (continuation session, fresh sandbox, Python 3.13.16):
- Folder restored and byte-compared against the previously packaged ZIP: identical. All groups re-run: `--help`/`--version`, `capabilities` (324), `catalog validate` (324 skills, 24 categories, 0 unrouted), `host profile`, `sdk jdk-route`, `build inspect`, `integrity` pins cycle (init → add → verify OK → tampered copy CHANGED exit 1), `selftest run`, `workspace clean`, `artifact` battery (detect, zip-health, pkginfo, arsc, dex, signing, abi, media, inventory) on a real F-Droid APK (1,227 entries, 0 bad CRCs, v1+v2+v3 signing), `triage analyze` on the shipped sample (all 5 kinds), `storage sqlite --diff` (reports only changed table/columns), `security scan` APK and `--decoded` (debuggable + cleartext detected), `compat check --device-api --release --out`, `preserve` provenance/snapshot/package/report gates (exit 3 without `--yes`; snapshot originals immutable → name clash E_INPUT even with `--yes`; report --gate exit 1 on 2 blocking items), `recovery assess`, `device list` exit 4, `tool run` whitelisted (git, curl, node) vs non-whitelisted (`nc`, `wget`, `bash` → E_NOT_FOUND exit 1), malformed `--config` → E_PARSE exit 1, missing input → JSON envelope.
- Defect found and fixed: `selftest run` crashed its built-in `dex-header`, `elf-machine` and `zip-fixture` checks with FileNotFoundError when the default `--out` directory did not exist (earlier testing always had an existing `./out`, which masked it). `cmd_selftest_run` now creates `--out` before running; all five builtin checks PASS from a clean directory. README updated to match (selftest `--out` creation; snapshot originals are never replaced, even with `--yes`).

---

## 10. Limits

- **Not run on a real device or emulator.** No device-state, install, launch, UI-automation or runtime-compatibility test has been executed. `device` commands are read-only.
- **apktool, jadx, baksmali, gradle, ndk-build, cmake, aapt2, apksigner, zipalign, bundletool, keytool, sqlite3 CLI, ffmpeg and Android SDK are not installed** in the test sandbox. Their adapters are verified only for MISSING reporting, the whitelist and the approval gate, not for execution of their real functions.
- **JDK 17 is not installed** (JDK 11 is). The `tool-java` self-test is critical and reports WRONG VERSION until JDK 17 or 21 is installed and `JAVA_HOME` is set.
- **No Android SDK** was available: `sdk inventory`, `sdk licenses`, `sdk footprint` return MISSING or exit 4 here.
- **16 KB page-size and 64-bit claims are static.** They come from the ELF headers and load-segment alignment of the libraries inspected. No arm64 device or runtime test was run.
- **Single-sample evidence.** The real-APK tests used one F-Droid APK (org.fdroid.fdroid 2.0.2). The XAPK split tests used a synthetic container.
- **Security and compat outputs are heuristics.** They are not vulnerability assessments and do not prove runtime behaviour.
- **Triage** classifies the five block kinds listed in §6.9; unrecognised formats are `unknown`.
- **Retrace** requires an R8/ProGuard mapping file; it does not reconstruct missing mappings.
- **Recovery** works from static evidence only and does not repair files.
- **Python version.** Tested on 3.13 only; the stated 3.8 minimum is not separately tested.
- **Duplicate inputs are rejected, not merged.** `preserve package` with two files of the same name returns `E_INPUT` (exit 2). `sdk licenses` and `sdk inventory` return exit 4 without an SDK, by design.
- **Not implemented:** APK signing, keystore creation, publishing, device install/flash/wipe, emulator creation, DRM handling, authentication bypass, and any action on services that require credentials or network access.

---

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `selftest run` exit 1 with `tool-apktool MISSING` | apktool not installed or not pinned | Download a versioned apktool jar from the official ibotpeaches release channel, set `APKTOOL_JAR`, then `integrity pins-add` |
| `tool-java WRONG VERSION (11)` | only JDK 11 installed | Install OpenJDK 17 (or 21) and set `JAVA_HOME`; check with `sdk jdk-route` |
| `E_TOOL_MISSING` / exit 4 for `tool run` | tool not on PATH or in SDK | `tool locate <name>` for the install hint |
| `E_APPROVAL` exit 3 | action changes state or overwrites | Review the target, then repeat with `--yes` |
| `E_NOT_FOUND` exit 1 | wrong path | Relative paths resolve against the current directory |
| `E_INPUT` exit 2 on `snapshot` | parent directory missing | Create the parent directory; the toolkit creates only the leaf |
| `E_PARSE` exit 1 on `recovery assess` | container is damaged or not ZIP | Re-download or re-copy the file; check with `artifact zip-health` |
| `E_PARSE` exit 1 with `--config` | malformed JSON | Fix the JSON syntax; the toolkit does not edit config files |
| `DEVICE REQUIRED` exit 4 | adb sees no `device`-state entry | Authorise USB debugging or start an emulator |

---

## 12. Tool file roles and loading order

Loading order (performed by the entry point):

1. `tools-toolkit-core.py` — loaded first as module `aatk_core`: error types and exit mapping, config merge (`asset-default-config.json` + `--config`), path and approval checks, subprocess runner (`shell=False`, timeouts, output caps), JSON and text rendering.
2. `tools-artifact-analysis.py` — artifact parsers (ZIP, AXML, ARSC, DEX, ELF, signing, ABI, media, inventory, splits).
3. `tools-tool-adapters.py` — whitelisted registry (`asset-tool-registry.json`), locate and version probes, `tool run`, `device list`.
4. `tools-host-environment.py` — host profile, SDK inventory, JDK routing, build inspection, integrity pins, self-test runner.
5. `tools-triage-reporting.py` — triage, retrace, storage, security, compat, preserve, recovery.
6. `tools-catalog-engine.py` — catalog and capability queries over `asset-catalog-skills.json`.
7. `tools-android-agent-toolkit.py` — the entry point: builds the argument parser, dispatches each group, handles exit codes and output.

The entry point is the only file you run. Modules 1–6 are imported by path and are not separate programs.

---

## 13. Asset file roles

| File | Purpose |
|---|---|
| `asset-catalog-skills.json` | Source of the 324 skills and their tiers, requirements and command routes |
| `asset-compat-matrix.json` | API levels, 37 behaviour rules, test-matrix lanes, SDK matrix kinds, 19 installer error codes |
| `asset-default-config.json` | Limits, approval rules, default paths, exit codes, tool search dirs, scan patterns, reporting |
| `asset-tool-registry.json` | Whitelist of 93 tools with executable candidates, version probes, read-only argument rules and install hints |
| `asset-selftest-suite.json` | Startup self-test entries (42), including embedded binary fixtures for the parser checks |
| `asset-sample-failures.txt` | Sample crash, ANR, tombstone and installer log used by `triage analyze` and the self-test |

---

## 14. Quick start

```bash
T=android-agent-toolkit/tools-android-agent-toolkit.py
python3 $T --version
python3 $T capabilities --category 13
python3 $T catalog validate
python3 $T selftest run               # expect exit 1 on this host until apktool and JDK 17 are present
python3 $T artifact pkginfo my-app.apk
python3 $T security scan my-app.apk
python3 $T preserve snapshot --files my-app.apk --out ./snap
```
