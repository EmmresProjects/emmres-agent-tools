# iOS/iPadOS Agent Skill Toolkit — knowledge, operations and contracts

**Skill Tool name:** `ios-ipados-agent-toolkit`
**Primary entry point:** `ios-ipados-agent-toolkit/tools-iosh.py`
**Runtime:** Python 3.9+ (standard library only; verified on CPython 3.13.16, Linux x86_64)
**Source catalog:** *iOS and iPadOS Agent Skill Catalog* — 354 capabilities across 29 categories
**Policy version:** 1.0.0 · **Report schema:** 1.0.0

This folder is a reusable Skill Tool, not a complete skill: it has no `SKILL.md`. It is designed to be
copied beside a parent package's single root `SKILL.md` without modification.

---

## 1. Purpose

Turn the 354 catalogued iOS/iPadOS capabilities into one runnable, evidence-producing tool that works
**entirely on a Linux inspection host**, while being explicit and honest about everything Apple
requires macOS, an Apple account, a physical device, a network path or a commercial licence to do.

The tool never pretends to be Xcode. It does two jobs extremely well:

1. **Linux-native inspection** — IPA/ZIP structure, binary plists, Mach-O load commands, entitlements,
   provisioning profiles, crash reports, Xcode projects/schemes/xcconfigs, privacy manifests, secret
   exposure, migration blockers, test-result merging, dSYM UUID matching, evidence gating.
2. **Routing, planning and governance** — every catalogued capability is reachable through
   `capabilities`, `plan`, `route`, `gate` and `health`, with the exact host/account/device/licence
   requirement, the approval gate, and the command that performs or hands off the work.

Everything the tool reports is either **observed** (a parsed artifact, a probed command, a hashed file)
or **explicitly labelled** as routed, external, reference-only or blocked. There are no simulated
builds, no invented test results and no claims of installability.

---

## 2. Quick start

```sh
# 1. nothing to install; confirm the host
python3 ios-ipados-agent-toolkit/tools-iosh.py probe

# 2. prove the parsers work (bundled lawful fixtures)
python3 ios-ipados-agent-toolkit/tools-iosh.py health

# 3. see what is reachable, and how
python3 ios-ipados-agent-toolkit/tools-iosh.py capabilities --category 9
python3 ios-ipados-agent-toolkit/tools-iosh.py capabilities --implemented-only

# 4. inspect a real artifact
python3 ios-ipados-agent-toolkit/tools-iosh.py inspect-ipa --ipa build/MyApp.ipa \
    --json-out reports/ipa.json
python3 ios-ipados-agent-toolkit/tools-iosh.py inspect-binary \
    --binary build/MyApp.app/MyApp --entitlements --closure

# 5. plan an Apple-side capability and hand it to the macOS worker
python3 ios-ipados-agent-toolkit/tools-iosh.py plan --category 13 --have-macos --write plan.json

# 6. refuse an out-of-scope request
python3 ios-ipados-agent-toolkit/tools-iosh.py route --action "bypass activation lock"
```

Run from any directory: all paths are relative to the future skill root, the tool locates its own
assets from its file location, and all report paths you pass are resolved relative to the current
directory.

### Shell

Commands above use POSIX `sh`/`bash` with an explicit `python3`. On Windows use
`python tools-iosh.py ...`; the tool itself is portable (no shell, no POSIX-only APIs in required
paths, `os.path`/`pathlib` throughout).

---

## 3. Packaging layout and file roles

```
ios-ipados-agent-toolkit/
├── tools-iosh.py                    PRIMARY entry point: all 31 commands, CLI, rendering, planning
├── tools-iosh_core.py               shared runtime: limits, statuses, policy, routing, reports,
│                                    command runner, artifact sniffing, human/JSON emission
├── tools-iosh_apple.py              Apple format parsers: plist (XML+binary), Mach-O (thin+fat),
│                                    code signatures/entitlements, ZIP/IPA, provisioning, crash, dSYM
├── tools-iosh_ops.py                policy-bearing operations: inventory, plans, gate, workspace,
│                                    paths, pins, cache, licence/secret scans, health, matrix,
│                                    project reader, privacy, signing readiness, triage, snapshots,
│                                    binary diff, evidence packaging, report validation
├── tools-iosh_fixtures.py           regenerates and verifies the asset-fixture-* files
├── tools-iosh_selftest.py           repeatable 67-case regression suite (passes on a bare Linux host)
├── knowledge-readme.md              this file
├── asset-capability-catalog.json    all 354 capabilities: id, name, slug, description, implementation
│                                    kind, command, requirements, approval gates
├── asset-policy.json                authorization/approval/routing/licence policy (overridable)
├── asset-report.schema.json         JSON Schema (draft-07) for every machine report
├── asset-test-matrix.json           Appendix B Simulator/device matrix as schedulable lanes
├── asset-export-options.template.plist  safe defaults merged into generated ExportOptions.plist
└── asset-fixture-*                  15 lawful synthetic fixtures (one flat family):
    asset-fixture-app.ipa             IPA assembled from the fixtures below
    asset-fixture-arm64.dylib         hand-built arm64 Mach-O with LC_UUID/LC_BUILD_VERSION/dylibs
    asset-fixture-fat-macho           two-slice universal binary (arm64 + x86_64 Simulator)
    asset-fixture-info.plist          compliant Info.plist (XML)
    asset-fixture-info-broken.plist   non-compliant Info.plist used to prove the auditors fire
    asset-fixture-privacy.xcprivacy   privacy manifest with required-reason declarations
    asset-fixture.mobileprovision     CMS-shaped profile envelope with placeholder cert bytes
    asset-fixture-crash.ips           modern .ips crash report
    asset-fixture-project.pbxproj     real ASCII-plist Xcode project
    asset-fixture-scheme.xcscheme     shared scheme referencing the fixture target
    asset-fixture-config.xcconfig     xcconfig with includes and variable expansion
    asset-fixture-appdelegate.swift   source file the project references
    asset-fixture-xctest-results.json XCTest shard with a retried test
```

**No `SKILL.md`, no internal subfolders, no unprefixed files, no placeholder code.** Every file
sits directly in the folder and is referenced by its exact relative, case-sensitive name.

### Load order (important if you import rather than execute)

The mandatory `tools-` filename prefix makes the files invalid Python identifiers, so the primary
entry point loads them by path and registers importable aliases:

```
tools-iosh.py
  └─ _bootstrap_modules()
       1. tools-iosh_core.py   -> sys.modules["tools_iosh_core"]
       2. tools-iosh_apple.py  -> sys.modules["tools_iosh_apple"]
       3. tools-iosh_ops.py    -> sys.modules["tools_iosh_ops"]
```

`tools-iosh_fixtures.py` uses the same bootstrap. If you import the modules yourself, reuse that
function (`from tools_iosh_core import ...` then works normally).

### Why four tools files

| file | functional reason to be separate |
| --- | --- |
| `tools-iosh.py` | the only file that is executed; keeps CLI surface and rendering independent of parsing |
| `tools-iosh_core.py` | cross-cutting contracts (limits, statuses, policy, reports, runner) reused by all others; a change here is reviewed once |
| `tools-iosh_apple.py` | pure format parsers with no policy; reusable by future skills and testable in isolation |
| `tools-iosh_ops.py` | policy-bearing operations; separated so parsers never accidentally gain side effects |
| `tools-iosh_fixtures.py` | fixture generation is a maintenance activity, not a runtime path; kept out of the entry point |
| `tools-iosh_selftest.py` | the regression suite drives only the public CLI, so it proves the shipped surface rather than internal functions |

---

## 4. Command reference

Global options apply to every command and may appear **before or after** the subcommand:

| option | meaning |
| --- | --- |
| `--json-out PATH` | write the machine report (schema below) |
| `--human-out PATH` | write the human report |
| `--format human\|json\|jsonl\|both` | stdout format (default `human`; `jsonl` streams findings) |
| `--verbose` | include a `data` dump and remediation hints |
| `--dry-run` | plan without side effects; external commands are not executed |
| `--policy FILE` | policy overlay (see `asset-policy.json` → `extension_howto`) |
| `--state-dir DIR` | writable state root for pins, cache index, dSYM index (default `$IOSH_STATE_DIR` or the system temp dir) |
| `--max-entries`, `--max-file-bytes`, `--max-total-bytes`, `--max-depth`, `--timeout`, `--retries`, `--max-workers`, `--max-findings`, `--max-output-bytes`, `--max-disk-ratio` | resource limits (§7) |

Exit codes: `0` OK/WARN, `1` FAIL (findings or failed checks), `2` usage, `3` invalid input
(including "not the artifact type this command needs"), `4` limit exceeded, `5` policy block or
approval required, `130` operator cancellation. Every exit path prints a structured report, including
argparse usage errors.

### 4.1 Host, routing and planning

**`probe`** — inventories the Linux host: CPU, memory, load, disk, USB presence (including Apple
Vendor ID `05ac` detection), FUSE availability, loopback binding, and 68 candidate tools in six
groups (`linux-host`, `device-linux`, `apple-macos`, `dependency`, `analysis`, `media`). Reports which
groups are usable now. `--paths` validates declared artifact paths and sniffs their real type.

**`route --action "..."`** — classifies an intended action *before* anything runs:
`linux-capable`, `macos-required`, `physical-device-required`, `apple-account-required`,
`network-required` or `license-required`, in that precedence order (hard platform boundaries first).
Reports the catalogued status vocabulary (`OK`, `MACOS HOST REQUIRED`, `PHYSICAL DEVICE REQUIRED`,
`APPLE DEVELOPER ACCOUNT REQUIRED`, `NETWORK REQUIRED`, `LICENSE REQUIRED`, `BLOCKED`) plus every
approval gate the action trips, with the exact `--approve <gate>` remediation. Denied actions exit 5.

Host facts are yours to state: `--have-macos`, `--have-device`, `--have-account`, `--network`.

**`plan`** — turns any capability, category or the whole catalog into an execution plan:

```sh
python3 tools-iosh.py plan --capability c09-01            # IPA Auto-Detector
python3 tools-iosh.py plan --capability signing-readiness  # name or slug also accepted
python3 tools-iosh.py plan --category 2 --have-macos --have-account --write plan.json
python3 tools-iosh.py plan --category 1 --execute --dry-run   # nothing runs; plan only
```

Each step carries `kind` (`native`, `native-blocked`, `external-tool`, `routed`, `reference`),
`runner` (the exact command), `requires`, `blockers` and `approval` gates. `--execute` runs **only**
bare self-sufficient commands (`probe`, `health`, `capabilities`); everything else is listed as
`SKIPPED` with the command to run, because inventing artifact paths would break the input contract.

**`gate --claim "..." --evidence F... [--required KIND...]`** — the completion gate. It infers required
evidence from the claim (`install`→IPA, `build`/`tests passed`/`upload`→report, `sign`→profile,
`crash`→crash report, `symbolicat`→dSYM), opens and parses every supplied artifact with the matching
parser, and returns `PASS` only when each check is satisfied. Unclassified evidence is sniffed and
parsed with its real parser, never just `os.stat`-ed.

### 4.2 Inspection (Linux-native)

**`detect --paths P...`** — identifies IPA, ZIP, .app, .xcarchive, .xcframework, .framework, .dSYM,
.xcresult, plist (XML/binary), mobileprovision, Mach-O (thin/fat), SQLite, privacy manifest, crash
reports and more **despite misleading extensions** (magic bytes + ZIP first-member probe + extension
hints with a synonym table). Empty files are called out as placeholders, not artifacts.

**`inspect-ipa --ipa FILE`** — one command, twelve capabilities: ZIP health (CRCs, duplicates, case
collisions, traversal paths, encryption, nesting, compression-ratio bomb guard), Payload discovery
(zero/multiple `.app` detection), bundle inventory, identity (`CFBundleIdentifier`, versions,
executable, package type), deployment target and platform, device family and capabilities,
architectures (incl. 32-bit and Simulator-slice warnings), extensions with extension points and
principal classes, embedded frameworks with per-slice arch/min-OS/rpath/encryption, FairPlay
encryption status from `LC_ENCRYPTION_INFO(_64)`, nested code signing order, privacy-manifest
presence, localization inventory, and a human+JSON report.

**`inspect-plist --plist FILE [--privacy] [--convert xml|binary] [--out P]`** —
XML **and** binary plist parsing with full type preservation (ints incl. signed 64-bit, reals, dates,
data, UTF-16 strings, UIDs, nested arrays/dicts, cyclic-reference guard, 64-level depth cap), Info.plist
audit (required keys, reverse-DNS bundle id, unexpanded `$(VAR)` build variables, `UIDeviceFamily`,
scene manifest, typed arrays, launch experience, export-compliance declaration, retired
architecture capabilities), privacy purpose-string audit (empty/vague/placeholder/missing, with the
correct `NSUserTrackingUsageDescription` naming), and **verified** conversion (writes the file, re-reads
it, compares values, reports `round_trip_equal`).

**`inspect-project --project P [--workspace] [--diff-configs] [--xcconfig] [--destinations]`** —
a real `project.pbxproj` (ASCII plist) tokenizer/parser — no Xcode needed. Reports targets, build
phases, configurations, settings, dependencies, shell phases (flagging network access), file
references that do not resolve, shared vs user schemes, schemes referencing missing targets,
configuration differences, resolved `.xcconfig` values with include/variable handling, and the valid
build destinations with the reason each one needs (or does not need) signing.

**`profile --profile FILE [--expiry-days N] [--diff-entitlements OTHER]`** — extracts the XML payload
from the CMS envelope of `embedded.mobileprovision` (no `security` needed), reporting UUID, name, team,
application identifier and bundle pattern, distribution method (development/ad-hoc/enterprise/app-store),
device count, certificate count, creation/expiry dates, days remaining, entitlement keys, APS
environment and beta-reports flag. Expired/expiring profiles escalate to `error`.

**`inspect-binary --binary FILE|DIR [--entitlements] [--closure] [--objc] [--swift]`** — Mach-O thin and
universal parsing: header, architecture (incl. `arm64e`), file type, `LC_UUID`, `LC_BUILD_VERSION`
platform/min-OS/SDK (plus legacy `LC_VERSION_MIN_IPHONEOS`), `LC_ENCRYPTION_INFO`, dylib/weak/rpath/
install-name commands, `LC_MAIN`, segments, and code-signature blob parsing (`identifier`, `teamID`,
`flags`, embedded entitlements plist). `--closure` resolves `@rpath`/`@loader_path`/`@executable_path`
references against the bundle and lists unresolved dependencies (a launch failure in waiting).
`--objc`/`--swift` harvest Objective-C sections/selectors/framework classes and Swift mangled-symbol
and section evidence — clearly labelled as string-level heuristics, with demangling routed to macOS.

**`privacy --target FILE|DIR`** — aggregates `PrivacyInfo.xcprivacy` manifests (inside an IPA or in a
tree), validates required-reason API categories against documented reason codes, cross-checks source
for required-reason API usage that is **not declared**, flags tracking without domains, and reports
`collected data types` purposes (`NSPrivacyCollectedDataTypePurposes`).

**`signing-readiness`** — the signing gate (`--purpose development|distribution`):

```sh
python3 tools-iosh.py signing-readiness \
  --ipa build/MyApp.ipa --profile build/MyApp.mobileprovision \
  --identity "Apple Distribution: Example (ABCDE12345)" \
  --team-id ABCDE12345 --bundle-id com.example.app --purpose distribution
```

Checks identity declaration (rejecting key material on the command line), profile validity and
purpose, team match, bundle-id-vs-pattern match, embedded-profile agreement, FairPlay encryption,
nested component count, and signed-but-not-allowed entitlements. `READY` means *internally
consistent*, never "installs on a device".

**`crash-triage --crash FILE [--dsym-dir DIR]`** — parses modern `.ips` (JSON header + body) and
legacy text `.crash` reports: process, bundle, OS, hardware, exception type/signal/codes, termination
namespace and indicator, `asi` notes, triggered thread and frames, binary images with UUIDs. Classifies
into the crash taxonomy (signing, dyld, architecture, watchdog, memory, Swift runtime, ObjC exception,
native signal, privacy, ATS, retired service, test failure, build error, device trust) with hints, and
matches binary-image UUIDs against an indexed dSYM directory.

**`dsym-index --paths P... [--tag T]`** — stores dSYM/Mach-O UUID → file records in the state
directory, reporting new/changed/failed entries.

**`diagnose --log FILE [--kind build|test|device|runtime] [--pattern REGEX...]`** — applies the same
failure taxonomy line by line to `xcodebuild`, test, syslog and unified-log text, with custom regex
support and per-class remediation.

**`migration-scan --project P [--deprecations]`** — inventories deployment targets, Swift versions,
obsolete architecture references (`armv7`, `i386`, `VALID_ARCHS`) and deprecated API symbols;
blockers include deployment targets below iOS 11 (where 32-bit-only apps stop running) and 32-bit
architecture references (a real 64-bit build needs source or equivalent authorized redevelopment).

**`merge-results --results F... [--out FILE]`** — merges sharded XCTest JSON: totals, per-destination
identity, conflict detection, and **final failures vs retained first-failure evidence** for retried
tests, so a passing retry never erases a regression.

**`export-options --method ... --team-id ... [--bundle-id] [--signing-style] [--profile-name] [--identity]`** —
generates a validated `ExportOptions.plist` (team-id format check, method mapping incl.
`app-store-connect`, manual-signing completeness, identity/method compatibility warnings, template
defaults merge), writes it and verifies the round-trip. Any App Store method is flagged as requiring
the `publishing` approval gate.

### 4.3 Governance, evidence and environment

**`workspace --name NAME --inputs P... [--into DIR] [--clean]`** — creates `ws-NAME/inputs/{artifact,
metadata,binary,log,symbols,project}`, `work/`, `output/`, `logs/`; **copies** inputs (originals never
move), hashes both original and copy, and writes a workspace manifest. `--clean` refuses to reuse.

**`paths --paths P... [--to-scheme posix|macos-relative|windows]`** — normalises Linux/macOS/Windows
paths to skill-root-relative form and flags drive/UNC absolutes and `..` traversal.

**`pins record --tool NAME...` / `pins compare`** — records exact versions, paths, sizes, mtimes and
SHA-256 for tools (persisted in the state dir) and reports drift/missing on later hosts.

**`cache init|add|list|verify|check --cache-dir DIR [--add P...] [--name N]`** — an offline artifact
cache that **refuses** Apple-licensed payloads (Xcode trees, platform SDKs, Simulator runtime bundles,
Apple licence text) and verifies SHA-256 on demand.

**`license-scan --paths P...`** — scans trees for Apple-licensed markers and commercial tool payloads;
exit `FAIL` on any Apple component, with the licence rule stated in the report.

**`secrets-scan --paths P...`** — finds private keys, `.p8` App Store Connect keys, generic API
keys/tokens, bearer tokens, credentials embedded in URLs, JWTs, provisioning device lists and `.p12`
references. Previews are shape-preserving (`BEGIN*`, masked matches) — matched secrets are never
echoed in full.

**`health [--tool NAME...] [--macos-probe FILE] [--no-fixtures]`** — startup self-tests. Each tool is
reported `FOUND` (with its real version line) or, when absent and Apple-side by nature,
`MACOS HOST REQUIRED` / `PHYSICAL DEVICE REQUIRED` / `LICENSE REQUIRED` — never "BROKEN" merely because
no Mac or device is attached. Then nine fixture proofs run (§9). `--macos-probe` folds a JSON capture
from the macOS worker (raw capture or a `health --format json` report) into a worker verdict.

**`matrix [--runtimes "iOS 15.0,iOS 17.0"] [--include-device] [--lane-retries N] [--write FILE]`** —
materialises `asset-test-matrix.json` into labelled lanes with environment, device/runtime hints,
focus, required checks and the status each lane implies (`MACOS HOST REQUIRED` / `PHYSICAL DEVICE
REQUIRED`). The tool schedules lanes; it never boots a Simulator or a device.

**`report --evidence P... --claim "..." --out DIR [--json-out FILE]`** — evidence packager: opens every
file, sniffs and parses where a parser exists, records bytes and SHA-256, lists missing items, and
writes `evidence-manifest.json`. Originals are read in place.

**`snapshot --root DIR [--previous FILE] [--workspace LABEL] [--out FILE]`** — hashes every file in an
artifact tree (content only, timestamps excluded so two hosts compare), reports duplicate-content
groups, and — with `--previous` — lists added/removed/changed files. This is the evidence for a
reproducible-build audit and for idempotency ("rerun changes nothing"). It states the caveat plainly:
identical hashes prove file equality, not build reproducibility, which also needs identical toolchain,
SDK, settings, dependency locks and environment.

**`binary-diff --first FILE --second FILE`** — compares two authorized Mach-O builds: byte identity,
`LC_UUID`, platform, minimum OS, architectures and dylib sets. A differing UUID is flagged as blocking
because dSYMs, crash reports and caches are then not interchangeable.

**`validate-report --report FILE`** — checks a report against the documented contract (required fields
and types, finding levels, status vocabulary, schema version) and returns `PASS`/`FAIL`.

**`capabilities [--category N] [--implemented-only] [--json-only]`** — the capability list: id
(`c09-04`), category, name, slug, implementation kind, serving command, requirements, approval gates
and description for all 354 entries.

---

## 5. The capability model (how 354 catalog entries map to one tool)

`asset-capability-catalog.json` records for every entry an `implementation` kind:

| kind | count | meaning |
| --- | --- | --- |
| `native` | 84 | fully implemented here and runnable now on Linux |
| `plan-only` | 124 | validated plan/routed command; the Apple-side step executes on the macOS/device/account host |
| `external` | 65 | requires a named external tool (`security`, `codesign`, `otool`, `sqlite3`, `ffprobe`, `ipsw`, `jtool2`, Hopper/IDA, pymobiledevice3, go-ios, device clouds…); this tool classifies, probes and routes it |
| `reference` | 81 | knowledge guidance (compatibility packs, governance) with no code path |

Every entry also carries `requires` (`macos-required`, `apple-account-required`,
`physical-device-required`, `network-required`, `license-required`) and `approval_gates` derived from
the shipped policy, so `plan`, `route` and `gate` agree with the catalog by construction.

Coverage per category (native/plan/external/reference):

| # | Category | Skills | n/p/e/r | Native commands |
| --- | --- | --- | --- | --- |
| 1 | Linux Host Readiness and Cross-Platform Routing | 12 | 11/1/0/0 | probe, route, workspace, paths, pins, cache, license-scan, health |
| 2 | macOS Worker, Xcode, and SDK Operations | 12 | 2/9/1/0 | health, export-options |
| 3 | Swift, Objective-C, C, C++, Source Compilation | 12 | 0/12/0/0 | routed to Xcode/clang |
| 4 | Dependency Managers and Project Generation | 12 | 0/12/0/0 | routed (SwiftPM, CocoaPods, Carthage, Tuist, XcodeGen, Bazel) |
| 5 | Xcode Projects, Schemes, Workspaces, Build Diagnostics | 12 | 10/0/0/2 | inspect-project, diagnose, cache, snapshot |
| 6 | Simulator Runtime and simctl Automation | 12 | 2/10/0/0 | health, matrix (simctl is macOS-only) |
| 7 | Linux Device Communication and Pairing | 12 | 0/0/12/0 | external: libimobiledevice family |
| 8 | pymobiledevice3, go-ios, Modern Device Services | 12 | 0/0/12/0 | external |
| 9 | IPA, App Bundle, ZIP, Artifact Inspection | 12 | 12/0/0/0 | inspect-ipa, detect |
| 10 | Plist, Entitlements, Provisioning, Metadata | 12 | 12/0/0/0 | inspect-plist, profile, inspect-binary |
| 11 | Mach-O, Framework, ObjC/Swift Binary Analysis | 12 | 6/1/5/0 | inspect-binary |
| 12 | ARM64 Disassembly, Research, Emulation | 12 | 2/0/3/7 | route (research scope guard), binary-diff |
| 13 | Signing Identities, Keychains, Provisioning | 12 | 1/9/2/0 | inspect-ipa (signing order) → routed codesign |
| 14 | Fastlane, App Store Connect, TestFlight, Distribution | 12 | 0/12/0/0 | routed + publishing gate |
| 15 | XCResult, XCTest, XCUITest, Test Plans | 12 | 2/10/0/0 | merge-results, matrix |
| 16 | Debugging, Unified Logs, Crashes, Symbolication | 12 | 4/1/2/5 | crash-triage, dsym-index, diagnose |
| 17 | Performance, Instruments, MetricKit, Energy | 12 | 0/12/0/0 | routed (physical hardware) |
| 18 | UI, Accessibility, Localization, iPad Experience | 12 | 0/12/0/0 | routed (Simulator/device lanes) |
| 19 | User Data, Backups, Core Data, SQLite, Containers | 12 | 0/0/2/10 | external: sqlite3, backup tooling |
| 20 | Network, ATS, APNs, StoreKit, Service Compatibility | 12 | 0/0/12/0 | external + routed |
| 21 | Privacy, Security, Authorized Dynamic Analysis | 12 | 4/0/0/8 | privacy, secrets-scan, inspect-ipa, route |
| 22 | Graphics, Metal, Media, Assets, Spatial | 12 | 0/10/2/0 | routed (metal/metallib) + ffprobe/identify |
| 23 | Unity, Unreal, Cocos2d-x, Game Migration | 12 | 0/12/0/0 | routed (Unity/Unreal on macOS) |
| 24 | Compatibility Knowledge Packs | 12 | 0/0/0/12 | reference (this document) |
| 25 | Source Migration and Modernization | 12 | 2/0/0/10 | migration-scan |
| 26 | Agent Automation, Reports, Orchestration | 12 | 7/1/0/4 | inspect-ipa, signing-readiness, crash-triage, matrix, report, gate |
| 27 | Hosted Device Clouds and CI/CD | 12 | 0/0/12/0 | external (GitHub/Azure/MacStadium/device clouds) |
| 28 | Startup Self-Tests and Operational Health | 16 | 2/0/0/14 | health |
| 29 | Skill Quality, Governance, Maintenance | 14 | 5/0/0/9 | validate-report, license-scan, secrets-scan, snapshot |

Total: **84 native + 124 plan-only + 65 external + 81 reference = 354**.

---

## 6. Inputs, outputs, schemas

### Inputs accepted
* artifacts: `.ipa`, `.zip`, `.app` trees, `.xcarchive`, `.framework`/`.xcframework` dirs, Mach-O files
  and `.dSYM` bundles
* metadata: XML/binary plists, `embedded.mobileprovision`, `PrivacyInfo.xcprivacy`
* project sources: `.xcodeproj` (`project.pbxproj`), `.xcworkspace`, `.xcconfig`, `*.xcscheme`
* diagnostics: `.ips`, `.crash`, plain log text, XCTest result JSON shards
* free text: capability ids/names/slugs, category numbers, action descriptions for `route`

### Outputs
* human report on stdout (always)
* machine report via `--json-out` (schema: `asset-report.schema.json`)
* command-specific JSON in `data` (always has a `summary` object)
* generated files: converted plists, `ExportOptions.plist`, plan JSON, matrix JSON, merged results,
  workspace manifests, evidence manifests, dSYM/pin/cache indexes in the state dir

### Report schema (abridged)

```json
{
  "schema_version": "1.0.0",
  "tool": {"name": "ios-ipados-agent-toolkit", "version": "1.0.0", "policy_version": "1.0.0"},
  "run_id": "1f6ea9a95ea7",
  "command": "inspect-ipa",
  "generated_at": "2026-11-09T01:50:04Z",
  "status": "OK | WARN | FAIL",
  "ok": true,
  "host": {"system": "Linux", "machine": "x86_64", "python": "3.13.16", "is_macos": false},
  "inputs": {"ipa": "MyApp.ipa", "sha256": "..."},
  "limits": {"max_entries": 200000, "timeout_s": 120, "retries": 0, "...": "..."},
  "counts": {"info": 5, "warn": 0, "error": 0, "critical": 0},
  "findings": [{"code": "ipa/arch-64", "level": "info", "message": "...", "path": "..."}],
  "data": {"summary": {"bundle_id": "com.example.app"}, "bundle": {}, "signing": {}},
  "errors": [{"code": "input-missing", "message": "...", "remediation": "..."}],
  "next_actions": ["..."],
  "truncated": false
}
```

Findings use the stable code namespace `area/name` (`ipa/*`, `plist/*`, `macho/*`, `privacy/*`,
`signing/*`, `crash/*`, `license/*`, `secrets/*`, `plan/*`, `gate/*`, `route/*`, `health/*`, …).
Levels: `info`, `warn`, `error`, `critical`. Status: `FAIL` if any error/critical or any recorded
exception, `WARN` if any warning, else `OK`.

### Configuration
* `asset-policy.json` (or `--policy FILE` overlay) — denied requests, approval gates, routing
  keywords, licence markers, self-test list and per-tool status when absent.
* `asset-capability-catalog.json` — capability metadata; regenerate only from an updated catalog.
* `asset-test-matrix.json` — lane definitions for `matrix`.
* `asset-export-options.template.plist` — defaults merged into generated export options.
* `--state-dir` / `IOSH_STATE_DIR` — where pins, cache index, dSYM index live.
* `IOSH_FIXTURES_DIR` — point `health` at a different fixture directory (used by the packaging tests).

---

## 7. Limits, timeouts, retries, cancellation, cleanup

Defaults (all overridable, all echoed in the report's `limits`):

| limit | default | protects against |
| --- | --- | --- |
| `--max-entries` | 200 000 | pathological trees/archives |
| `--max-file-bytes` | 512 MiB | accidental whole-image parsing |
| `--max-total-bytes` | 2 GiB | extraction/packaging blow-ups |
| `--max-depth` | 24 | deep nesting |
| `--timeout` | 120 s per external command | hung tools |
| `--retries` | 0 (max 10) | unbounded retry loops; retries apply to idempotent probes only |
| `--max-workers` | 4 (1–32) | concurrency |
| `--max-findings` | 5 000 | report explosion (`truncated: true` when hit) |
| `--max-output-bytes` | 1 MiB per command | captured-output blow-up |
| `--max-disk-ratio` | 0.90 | writing into a nearly full volume |

Behaviour guarantees: ZIP entry counts, per-entry sizes, total declared sizes, compression ratios
(>100:1 with >50 MiB) and CRC failures are bounded and reported; bplist nesting is capped at 64 with a
cyclic-reference guard; Mach-O load-command walking is bounded by `sizeofcmds` and truncated slices is
reported; CRC verification samples at most 4 000 entries under 8 MiB. `Ctrl-C` exits `130` without
partial writes (all writes go through a temp file + `os.replace`). Writes fail fast on disk pressure
before extraction begins.

---

## 8. Safety, approval and authorization rules

**Scope.** Use this tool only for applications, devices, accounts, data, services and assets that you
own or are explicitly authorized to inspect, build, modify, test, preserve, migrate, sign or distribute.

**Hard refusals (never overridable, `exit 5`).** Activation Lock/ownership bypass, passcode/biometric/
Secure Enclave bypass, DRM or FairPlay circumvention ("decrypt IPA", receipt tampering), purchase
bypass, credential/certificate theft, and identity or team impersonation. Patterns live in
`asset-policy.json → authorization.denied_requests`; `route` refuses them, `plan` surfaces them, and no
command implements them.

**Approval gates (`--approve <gate>`).** `destructive-device`, `signing`, `publishing`,
`data-extraction`, `dynamic-analysis`, `network-capture`, `spend`. Any step or action that trips a gate
is reported with the gate id and reason; nothing destructive, publishing, signing, credential-touching
or data-extracting executes without an explicit approval recorded on the command line. Approvals are
per-invocation, never persisted.

**Truth rules enforced by design.**
* Simulator success never counts as device proof; `matrix` labels every lane with the environment it
  requires and `signing-readiness` says `READY` ≠ installable.
* A 32-bit-only app cannot run on iOS 11+; a genuine 64-bit rebuild needs source (reported by
  `inspect-ipa`, `migration-scan`, `inspect-plist`).
* FairPlay-encrypted binaries are reported as unmodifiable without authorized unencrypted sources.
* Anything requiring macOS, an Apple account, a device, a network path or a licence is labelled with
  those exact words, never simulated.
* No secrets are echoed: `secrets-scan` masks matches, `commands` reject key material on the CLI, and
  Apple credentials are never read from files by this tool.

**Credential handling.** Identity *names* only (never `PRIVATE KEY` blocks). `.p8`/`.p12`/profile
contents are treated as secrets; `cache`/`license-scan` never redistribute Apple components.

**No arbitrary execution.** External commands are allow-listed by binary name, executed without a
shell, with shell metacharacters rejected, per-command timeouts, output caps and optional retries.
`--dry-run` executes nothing.

---

## 9. Validation actually performed

Environment: Linux x86_64, CPython 3.13.16, no Xcode, no Apple account, no iOS device attached.

**Fixture proofs (`health`, run against the flat `asset-fixture-*` files)** — 9/9 pass on every run:

| proof | what it demonstrates |
| --- | --- |
| `plist-roundtrip` | XML → binary → dict preserves all 21 keys (types included) |
| `ipa-inspect` | bundle `com.example.fixture`, arch `arm64`, ZIP healthy, 1 extension, 2 frameworks |
| `macho-parse` | `arm64`, platform `ios`, min OS `14.0.0`, UUID `0F1E2D3C-…` |
| `profile-decode` | UUID `11111111-2222-…`, method `development`, CMS envelope parsed |
| `crash-triage` | `.ips` parsed, 3 frames, classified `swift-runtime` |
| `project-parse` | 1 target, 1 source, 1 shared scheme from a real `project.pbxproj` |
| `external-sqlite3` / `external-plistutil` / `external-ffmpeg` | absence is reported as a configurable external dependency, not a false capability |

**Regression suite (`tools-iosh_selftest.py`)** — 67/67 pass on a bare Linux host (no Xcode, no Apple
account, no device), driving only the public CLI with relative paths: help and the full 354-row
capability list, category coverage, probe/health/tool filters, all three routing classes plus a policy
refusal, plan by category/id/ambiguity/unknown and `--execute` safety, `detect`, `inspect-ipa`,
`inspect-plist` (valid, broken, privacy, conversion round-trip), `inspect-project` in both flat and
folder shapes, `profile`, `inspect-binary`, `privacy`, `crash-triage`, `diagnose`, `migration-scan`,
misleading-extension detection, workspace creation and `--clean` refusal, path normalisation, gate
pass/block, evidence packing, secret scanning (clean and hitting), licence blocking, cache refusal,
pins record/compare, snapshot baseline/change detection, binary diff, matrix lanes, result merging,
export options (valid and rejected team id), signing readiness in both purposes, dSYM indexing, crash
symbolication matching, six error/limit paths, the truncation→recovery path, report validation in both
directions, JSONL streaming, and snapshot idempotency across two runs.

Reproduce all of it:

```sh
python3 ios-ipados-agent-toolkit/tools-iosh_selftest.py          # 67 checks
python3 ios-ipados-agent-toolkit/tools-iosh_fixtures.py --check  # 7 fixture proofs
python3 ios-ipados-agent-toolkit/tools-iosh.py health            # 9/9 fixture proofs in context
```

**Format-parser conformance (run during development, reproducible with
`python3 tools-iosh_fixtures.py --check` and the parser harness described below):**
* binary plist parser vs `plistlib`: identical results across integer widths (incl. signed 8-byte),
  reals, dates, data, UTF-16/emoji/CJK/RTL strings, empty containers, nested 400-element arrays and 50
  randomised documents — the only differences found were two genuine parser bugs (count-marker advance,
  8-byte signedness) which were fixed.
* Mach-O: thin arm64/x86_64 and two-slice universal parsing, `LC_UUID`/`LC_BUILD_VERSION`/`LC_SEGMENT_64`/
  `LC_ID_DYLIB`/`LC_LOAD_DYLIB`/`LC_CODE_SIGNATURE` offsets verified against hand-computed layouts.
* ZIP/IPA: CRC verification, duplicate/case-collision/traversal/nesting detection exercised on crafted
  archives; `inspect-ipa` on the fixture IPA returns bundle id, version `1.4.2 (142)`, min OS `14.0`,
  arch `arm64`, 1 extension, 2 frameworks, encrypted `false`, ZIP healthy.

**Command-level tests (all executed):**

| command | input | observed result |
| --- | --- | --- |
| `--help`, `capabilities` | — | 29 subcommands listed; category 9 returns 12 entries |
| `route` | "codesign the ipa and upload to testflight" | `macos-required`, gates `signing` + `publishing` |
| `route` | "bypass activation lock on a used iphone" | `BLOCKED`, policy-refused, exit 5 |
| `route` | "frida attach to the running app" | `dynamic-analysis` gate reported |
| `plan` | `--category 13 --have-macos` | 12 steps, 1 native, 11 routed, 3 approval gates |
| `plan` | `--capability simctl` | ambiguity error listing 13 matches + id remediation (exit 3) |
| `plan` | `--capability c09-01 --execute` | step `SKIPPED` with the exact command to run |
| `gate` | claim + IPA + crash | `PASS` 3/3 after evidence was sniffed and parsed |
| `gate` | claim "builds and installs" + IPA only | `BLOCKED`, missing report evidence |
| `workspace` | IPA + plist | copies under `inputs/artifact`, `inputs/metadata`, hashes both |
| `paths` | macOS/Windows/traversal paths | mapped + 2 traversal/absolute conflicts flagged |
| `pins` | `record --tool python3 --tool git` | versions captured with paths and SHA-256 |
| `cache` | add dylib / add file containing an Xcode path | cached (verified) / **refused** (`LICENSE`-style block) |
| `license-scan` | tree containing an Xcode marker | FAIL with `license/apple-component` |
| `secrets-scan` | fixtures tree | clean, 6 files scanned, binaries skipped |
| `health` | default | 3/22 host tools, 16 required-elsewhere, 9/9 fixtures |
| `health --macos-probe` | a report JSON | worker verdict `INCOMPLETE` with per-check detail |
| `matrix` | `--runtimes --include-device` | 7 lanes, hints applied, device lanes labelled |
| `detect` | fixtures directory | 12 artifacts, 10 kinds, empty file called out |
| `inspect-ipa` | fixture IPA | full report as above |
| `inspect-plist` | valid / broken Info.plist | 0 issues + privacy coverage / 6 issues + purpose-string errors |
| `inspect-plist --convert binary` | XML plist | written, re-read, `round_trip_equal: true` |
| `inspect-project` | `.xcodeproj` | 1 target, 2 configurations, 1 shared scheme, xcconfig resolved |
| `profile` | fixture profile | UUID, method, 1 device, 57 days left, development-only warning |
| `inspect-binary` | dylib and extracted `.app` | 1 / 4 images, arch `arm64`, closure resolved |
| `privacy` | fixtures tree | manifest aggregated, 2 API categories, codes validated |
| `signing-readiness` | IPA + profile + identity | `READY` (purpose `development`); BLOCKED for `distribution` |
| `crash-triage` | `.ips` | `swift-runtime`, `EXC_BREAKPOINT`, 3 frames |
| `dsym-index` | dylib | 1 UUID indexed |
| `diagnose` | linker error log | 3 `build-error` matches |
| `migration-scan` | fixtures tree | obsolete-arch blocker reported |
| `export-options` | app-store-connect | written + round-trip verified + publishing gate warning |
| `merge-results` | XCTest shard | 3 tests, 1 first-failure retained, 0 final failures |
| `report` | 2 evidence files | manifest written, both parsed |
| `validate-report` | generated report / foreign JSON | `PASS` / 13 schema errors |

**Error, limit and recovery paths tested:** missing input (exit 3), wrong artifact type (exit 3),
`--max-file-bytes 100` on a 1.8 KiB plist (exit 4), unknown capability (exit 3), ambiguous capability
(exit 3), policy denial (exit 5), missing required argument (structured usage report, exit 2). Recovery
path exercised: `inspect-plist --convert binary` failing on a truncated plist → raising a structured
`plist-invalid` error with a remediation, then succeeding after supplying the complete file; and
`health --macos-probe` recovering a report produced on another host.

**Not performed (and therefore not claimed):** any real `xcodebuild`/`simctl`/`codesign` run, any
device pairing, any App Store Connect call, any upload, any Instruments capture, any GPU/performance
measurement. Those paths are routed, and their absence is reported as `MACOS HOST REQUIRED`,
`PHYSICAL DEVICE REQUIRED` or `APPLE DEVELOPER ACCOUNT REQUIRED` by `health`, `route` and `plan`.

---

## 10. Known limits and unavailable integrations

| Area | Status in this Skill Tool |
| --- | --- |
| Xcode, xcodebuild, xcrun, simctl, actool, ibtool, metal, atos, dsymutil, otool, codesign, security | `MACOS HOST REQUIRED` — planned/routed only; no emulation |
| Simulator boot, install, log stream, screenshots | `MACOS HOST REQUIRED` — lanes scheduled by `matrix` |
| Physical devices (libimobiledevice, pymobiledevice3, go-ios, ios-deploy, ifuse, iproxy) | `PHYSICAL DEVICE REQUIRED` — provide a macOS worker file with `--macos-probe` when the tool is on that host |
| Simulator screenshots, crash collection from devices | `PHYSICAL DEVICE REQUIRED` / `MACOS HOST REQUIRED` |
| App Store Connect API, TestFlight, Transporter, provisioning renewal | `APPLE DEVELOPER ACCOUNT REQUIRED` + `publishing`/`signing` gates |
| Swift/Clang compilation, SwiftPM/CocoaPods/Carthage/Tuist/XcodeGen/Bazel builds | routed; this tool plans and diagnoses, it does not compile |
| Instruments, MetricKit parsing, thermal/energy, Metal frame capture | routed to physical hardware; no capture here |
| Dynamic analysis (Frida/Objection), traffic interception (mitmproxy/Charles/Proxyman/Wireshark) | require `dynamic-analysis`/`network-capture` approval and external tooling |
| Commercial tools (Hopper, IDA, jtool2, PVRTexTool, device clouds) | `LICENSE REQUIRED` |
| Deep static analysis (Ghidra/r2/Rizin/LIEF/Capstone/Unicorn) | external; `inspect-binary` supplies the Mach-O facts they need |
| Swift symbol demangling, `atos` symbolication | macOS-only; `crash-triage` proves the UUID match and hands off |
| `.xcresult` binary parsing | not implemented; `merge-results` handles JSON shard summaries |
| Encrypted/FairPlay binaries | reported as unmodifiable without authorized sources — by design |
| 32-bit-only apps | reported as unable to run on iOS 11+ — by design |

---

## 11. Troubleshooting and recovery

| symptom | cause | action |
| --- | --- | --- |
| `missing required Skill Tool module` | a `tools-*.py` file was renamed/moved | restore the exact filenames; the bootstrap loads by name |
| `capability catalog missing` | `asset-capability-catalog.json` not beside the tool | restore it next to `tools-iosh.py` |
| `input-missing` / `input-not-file` | path typo or wrong cwd | run `probe --paths <p>` and `detect --paths <p>` |
| `ipa-not-zip` | not an IPA/ZIP (binaries, plists) | use `detect`, then `inspect-plist`/`inspect-binary`/`profile` |
| `plist-invalid` | truncated or non-plist payload | re-export the plist; `--convert xml` requires a complete parse |
| `file-too-large` / `limit-exceeded` (exit 4) | artifact exceeds a limit | raise the specific limit (`--max-file-bytes`) or extract a slice into a `workspace` |
| `policy-blocked` (exit 5) | request matches a denial pattern | rephrase to an authorized action; denials are never overridable |
| `approval-required` (exit 5) | a gate is unmet | add `--approve <gate>` after reviewing the side effect |
| `usage-error` (exit 2) with a structured report | missing/invalid argument | run `<command> --help` |
| `health` reports `EXTERNAL TOOL MISSING` | optional tool absent (`sqlite3`, `plistutil`, `ffmpeg`) | install it, or accept that those capabilities stay routed |
| `macos_worker: INCOMPLETE` | the probe file lacks versions/runtimes | capture a fresh `health --format json` on the worker |
| findings truncated | `--max-findings` reached | raise it or narrow the input; the report sets `truncated: true` |
| `plan --execute` steps say `SKIPPED` | the step needs artifact arguments | run the printed command yourself |
| `validate-report` fails on a foreign JSON | not a report produced by this tool | validate only `--json-out` files, or adapt the tool's schema |
| `secrets-scan` flags the tool's own `.py` files | the detector's pattern literals live in them | scan application/source trees, not the Skill Tool folder (documented limitation) |
| `snapshot` reports different hashes on two machines | line endings, generated files or timestamps-in-filenames | normalise EOLs and exclude generated artifacts; the tool flags duplicates and shows exactly which paths differ |

**Recovery pattern.** Preserve the last passing output → isolate the earliest failing stage → make one
reversible change → rerun only the affected command with the same limits. All writes are atomic
(`temp file` + `os.replace`), originals are never modified, and `workspace` guarantees a copy-on-read
base you can discard.

**Extension guide.**
* new inspection capability → add a parser to `tools-iosh_apple.py`, a command to `tools-iosh.py`, a
  `asset-fixture-*` file to `tools-iosh_fixtures.py`, and a proof to `_run_fixtures()` in
  `tools-iosh_ops.py`, then a case in `tools-iosh_selftest.py`.
* new policy → edit `asset-policy.json` (denials, gates, routing keywords) — no code change needed.
* new capability metadata → update the catalog JSON and `capabilities` picks it up automatically;
  `plan`/`route` honour `requires` and `approval_gates`.
* new external tool → add it to `COMMAND_ALLOWLIST` in `tools-iosh_core.py` (reviewed change), then
  probe it in `TOOL_GROUPS` (`tools-iosh_ops.py`).
* keep the schema stable: additive fields only inside `1.x`; bump `SCHEMA_VERSION` and add an upgrade
  note here for breaking changes.

**Maintenance.**
* `python3 tools-iosh_fixtures.py` regenerates the flat `asset-fixture-*` files; `--check` proves them.
* Re-run `health` after any toolchain change; compare with `pins compare`.
* Re-run `capabilities --implemented-only` when the catalog or commands change to keep §5 accurate.
* The policy's `self_test.host_status_when_absent` map is the single place that decides whether an
  absent tool is `MISSING` or merely required elsewhere — keep it truthful.

**Failure taxonomy** (shared by `diagnose`, `crash-triage`, `gate` and the plan blockers): *host*
(macOS/device/account/network/licence), *input* (missing, wrong type, truncated, oversized),
*archive* (CRC, duplicate, traversal, bomb), *project* (missing reference, scheme, configuration),
*compile/link*, *sign*, *provision*, *install*, *runtime*, *service* (retired API, APNs/StoreKit),
*policy/authorization*. Every finding code maps to one of these, which is how a future orchestrator can
route a failure without parsing prose.

---

## 12. Attribution and licensing of this Skill Tool

No Apple code, SDK content, certificate material, provisioning private material, or real user data is
included. The `asset-fixture-*` files are synthesised by `tools-iosh_fixtures.py` (a hand-built
Mach-O, a plist, a CMS-shaped profile envelope with placeholder certificate bytes, an `.ips` crash, a
`.pbxproj`, an IPA assembled from those parts, and one deliberately broken Info.plist). The fixture
profile contains a placeholder certificate blob that is not a certificate.

Apple, Xcode, iPhone, iPad, App Store, TestFlight and related marks belong to Apple Inc. Using this
Skill Tool does not grant any Apple licence; Xcode and Apple SDKs remain governed by the Apple
Developer Agreement and must never be redistributed or copied into a Linux image (enforced by `cache`,
`license-scan` and the policy gate).
