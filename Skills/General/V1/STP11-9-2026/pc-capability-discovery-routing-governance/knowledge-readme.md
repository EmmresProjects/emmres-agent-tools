# knowledge-readme — capability-discovery-routing-governance

Skill Tool component for a future Copilot skill. This folder is the reusable
governance layer for the **PC Agent Skill Catalog** (30 categories, 400 skills):
it inventories capabilities, runs the startup self-test contract, routes work to
the right host/worker, gates lawful use, enforces approval and operational
boundaries, plans reversible changes with provenance, and validates clean state.
It is offline by design: no network calls, no embedded secrets, no paid
credentials, no private endpoints.

---

## 1. Purpose and capability list

Primary entry point: `capability-discovery-routing-governance/tools-capability-router.py`

| # | Command | Catalog capability implemented |
|---|---------|-------------------------------|
| 1 | `help` | Built-in help and usage (all commands, exit codes, examples) |
| 2 | `capabilities` | Capability discovery + list; accepts one capability, numbered lists, one or several categories, plain text, Markdown, or copied HTML |
| 3 | `inventory` | PC Capability Inventory (skill 1.1) — detects installed tools, versions, and missing dependencies |
| 4 | `selftest` | Startup Self-Test Orchestrator (skill 1.2) — deterministic probe statuses over catalog capabilities |
| 5 | `route` | Host-Aware Task Router (skill 1.3) — routes skill ids or job text to linux/windows/gpu/virtualization/network/macos lanes |
| 6 | `gate` | Lawful Use Gate (skill 1.4) — authorization, ownership, license, privacy declarations plus prohibited-condition scan |
| 7 | `risk` | Risk-Based Execution Approver (skill 1.8) — classifies read-only, reversible, privileged, networked, signing, publishing, financial, credential, security-sensitive, destructive actions and requires the matching approval level |
| 8 | `plan-change` | Reversible Change Planner (skill 1.5) — hash, verified backup, change plan, rollback recovery |
| 9 | `provenance` | Evidence and Provenance Recorder (skill 1.6) — JSONL evidence ledger with schema validation |
| 10 | `decompose` | PC Task Decomposer (skill 1.10) — inspect, preserve, reproduce, repair, test, package, verify, handoff |
| 11 | `artifact-route` | Artifact Handling Route Selector (skill 1.11) — magic-byte detection and safe handler routing for PE/ELF/ZIP/7z/RAR/CAB/ISO/WIM/disk images/packages/media |
| 12 | `boundary` | Operational Boundary Enforcer (skill 1.12) — refuses DRM, anti-cheat, activation, piracy, credential, account, access-control, and privacy bypass requests |
| 13 | `clean-state` | Clean-State Validation Manager (skill 1.7) — repeats a bounded command in fresh directories and compares stdout hashes |
| 14 | `version-policy` | Toolchain Version Policy Manager (skill 1.9) — pins vs. installed versions plus capability-combination availability |

Together these cover all 12 skills of catalog category 1 (Capability Discovery,
Routing, and Governance) and operate over the full 400-skill catalog as data.
Every catalog skill is reachable through `capabilities`, `selftest`, and
`route`; skills that need Windows, GPU, virtualization, network, license, or
macOS resources are reported with the matching contract status and routed to
that worker lane instead of being executed locally.

## 2. Package layout

All files live directly in `capability-discovery-routing-governance/` (no
subfolders, no SKILL.md — the future skill root keeps exactly one SKILL.md
beside this folder):

| File | Role |
|------|------|
| `capability-discovery-routing-governance/tools-capability-router.py` | Primary runnable entry point; argument parsing, dispatch, JSON envelopes |
| `capability-discovery-routing-governance/tools-governance-core.py` | Shared library: asset loading, probe runner, skill resolution, risk/gate/boundary logic, hashing, evidence, change plans, artifact detection, input parsing, decomposition, clean-state, cancellation cleanup |
| `capability-discovery-routing-governance/tools-checks.py` | Verification suite that exercises the primary entry point through its real interface using relative paths |
| `capability-discovery-routing-governance/knowledge-readme.md` | This single knowledge file |
| `capability-discovery-routing-governance/asset-capability-catalog.json` | All 30 categories / 400 skills with ids, descriptions, per-skill `requires` metadata, risk hints |
| `capability-discovery-routing-governance/asset-selftest-probes.json` | Startup self-test contract (statuses, required fields, precedence) and 40 calibrated probe definitions |
| `capability-discovery-routing-governance/asset-policy-pack.json` | Risk levels and keywords, approval flags, gate declarations, prohibited-boundary patterns, version pins, capability combinations, runtime limits |
| `capability-discovery-routing-governance/asset-evidence-schema.json` | Schemas for self-test rows, evidence records, and change plans |

Loading order at runtime: `tools-capability-router.py` imports
`tools-governance-core.py` (same folder, loaded by path because the filename
contains hyphens), then the core loads the four `asset-*.json` files from the
skill folder. Asset paths can be overridden per run.

## 3. Requirements and setup

- **Runtime:** Python 3.9+ (standard library only; no pip packages required).
- **Shell:** POSIX `sh`/`bash` (commands below are written for `sh -c`).
- **Host:** Linux x86_64 is the design target (the catalog baseline host).
  Windows and macOS hosts run the same code; host gates then change statuses
  accordingly.
- **No network** is used or required. No credentials are embedded.

Run from the directory that contains the skill folder (the future skill root):

```sh
python3 capability-discovery-routing-governance/tools-capability-router.py help
python3 capability-discovery-routing-governance/tools-capability-router.py capabilities
python3 capability-discovery-routing-governance/tools-checks.py
```

Or explicitly through a shell:

```sh
sh -c 'python3 capability-discovery-routing-governance/tools-capability-router.py help'
```

## 4. Command reference

Global options (placed after the command): `--catalog PATH`, `--probes PATH`,
`--policy PATH`, `--schema PATH` (asset overrides), `--compact` (single-line
JSON), `--actor NAME` (recorded in evidence), `--debug` (traceback on
unexpected errors).

Operational commands print one JSON envelope:

```json
{"ok": true, "command": "...", "result": {...}, "errors": [], "warnings": []}
```

Failures use `"ok": false` with
`errors: [{"message", "exit_code", "details"}]`.

**Exit codes:** `0` success · `1` unexpected internal error · `2` usage or
input error · `3` approval required · `4` policy denial (gate/boundary) ·
`5` integrity or readiness failure · `130` cancelled (SIGINT; temporary files
cleaned up).

### 4.1 `capabilities`

Lists the bundled catalog, selects categories, searches, or ingests external
capability lists.

```sh
# summary: 30 categories, 400 skills
python3 capability-discovery-routing-governance/tools-capability-router.py capabilities

# one category by number or by name substring (repeatable for several categories)
python3 capability-discovery-routing-governance/tools-capability-router.py capabilities --category 8
python3 capability-discovery-routing-governance/tools-capability-router.py capabilities --category "PE, COFF" --json

# search (text table by default, --json for machine output)
python3 capability-discovery-routing-governance/tools-capability-router.py capabilities --search "archive safety" --limit 3

# ingest plain text, Markdown, or copied HTML; write parsed JSON
python3 capability-discovery-routing-governance/tools-capability-router.py capabilities --from list.md --out parsed.json --json
```

Accepted `--from` inputs (HTML tags, CSS class remnants, and numbering marks
are ignored):

- `Skill Name:` / `Description:` lists (catalog format, any page copy)
- numbered or bulleted Markdown lists, including `**Name**: description` and
  `Name — description`
- copied HTML such as `<li><b>Name</b> — description</li>` (headings become
  category headers)
- a single bare capability name on its own line

### 4.2 `inventory`

Probes all 40 defined tools and emits one row per probe with the catalog's
required self-test fields (`capability_id` is `tool:<id>` here).

```sh
python3 capability-discovery-routing-governance/tools-capability-router.py inventory
python3 capability-discovery-routing-governance/tools-capability-router.py inventory --out evidence/inventory.json
```

### 4.3 `selftest`

Resolves catalog capabilities to contract statuses. Scope defaults to all 400
skills; narrow it with repeatable `--skill`, repeatable `--category`,
`--search`, `--limit`; simulate another host with `--assume`; persist rows with
`--out` (each row's `evidence_path` then points at that file); enforce
readiness with `--fail-on-not-found` (exit 5).

```sh
python3 capability-discovery-routing-governance/tools-capability-router.py selftest --skill c08s01
python3 capability-discovery-routing-governance/tools-capability-router.py selftest --category 29 --limit 3
python3 capability-discovery-routing-governance/tools-capability-router.py selftest --assume windows --skill c01s01
python3 capability-discovery-routing-governance/tools-capability-router.py selftest --out evidence/selftest.json --fail-on-not-found
```

Skill ids are `cNNsNN` (category NN, skill NN), e.g. `c08s01` = category 8,
skill 1 (PE Header Inspector). Unknown ids exit 2 with the id format.

### 4.4 `route`

```sh
# by id (repeatable)
python3 capability-discovery-routing-governance/tools-capability-router.py route --skill c11s01

# by free-form job text (catalog search + per-match routing), default 5 matches, cap 20
python3 capability-discovery-routing-governance/tools-capability-router.py route --job "inspect PE headers" --max 3

# evaluate against an assumed host
python3 capability-discovery-routing-governance/tools-capability-router.py route --job "build electron app" --assume windows
```

Each routing result carries `status`, `runnable_now`, `preferred_host`,
`acceptable_hosts`, `worker_recommendation` (local lane or the required worker),
`gates_failed`, missing/broken/wrong-version tools, `risk_hint`,
`approval_required`, and `approval_flags_needed`.

### 4.5 `gate`

Four declarations (`yes`/`no`): `--authorized`, `--owns-artifact`,
`--license-ok`, `--privacy-ok`. The action text is also scanned against the
prohibited-boundary patterns. Decision `ALLOW` exits 0; `DENY` (refused
declaration or prohibited condition) and `INCOMPLETE` (missing declarations)
exit 4.

```sh
python3 capability-discovery-routing-governance/tools-capability-router.py gate \
  --action "read and hash the installer" \
  --authorized yes --owns-artifact yes --license-ok yes --privacy-ok yes
```

### 4.6 `risk`

Classifies the action text, then checks whether the required approval has been
granted. Approval levels: `none` → `operator` (`--approve`) → `administrator`
(`--approve --approver NAME`) → `external` (`--approve --approver NAME
--reason TEXT`). Ungranted approval exits 3 with the exact missing flags.

```sh
python3 capability-discovery-routing-governance/tools-capability-router.py risk --action "read-only hash report"
python3 capability-discovery-routing-governance/tools-capability-router.py risk --action "delete all user files"          # exit 3
python3 capability-discovery-routing-governance/tools-capability-router.py risk --action "delete all user files" \
  --approve --approver "ops-lead" --reason "sanctioned cleanup ticket 42"
```

### 4.7 `plan-change` (recovery path)

```sh
# 1) plan: hashes the target, copies a verified backup, writes a change plan
python3 capability-discovery-routing-governance/tools-capability-router.py plan-change \
  --target config/app.conf --action "modify configuration file" --approve

# 2) operator performs the edit ...

# 3) rollback: verifies backup integrity, restores, marks the plan rolled_back
python3 capability-discovery-routing-governance/tools-capability-router.py plan-change \
  --rollback config/app.conf.pcbak-<id> --approve --approver "ops-lead" --reason "restore verified backup"
```

Exactly one of `--target` (create) or `--rollback` (recover) is required.
Approval follows the classified risk of `--action` (default
"modify configuration file" is `reversible` → operator level). Rollback is
`external` level and refuses to run twice or when the backup hash does not
match the plan.

### 4.8 `provenance`

```sh
python3 capability-discovery-routing-governance/tools-capability-router.py provenance record \
  --event manual --evidence evidence/ledger.jsonl \
  --source config/app.conf --command "selftest --skill c01s01" \
  --skill c01s01 --note "before change" --actor operator

python3 capability-discovery-routing-governance/tools-capability-router.py provenance show \
  --evidence evidence/ledger.jsonl --last 20 --validate
```

`--event` must be one of the schema values (`manual`, `selftest`, `inventory`,
`route`, `gate`, `risk`, `plan-created`, `plan-rolled-back`, `boundary`,
`clean-state`, `version-policy`). Sources are streamed and SHA-256 hashed.
Validation failures exit 2 (record) or 5 (show --validate).

### 4.9 `decompose`

```sh
python3 capability-discovery-routing-governance/tools-capability-router.py decompose --goal "repair the failing launcher"
```

Returns the eight ordered stages (inspect → preserve → reproduce → repair →
test → package → verify → handoff), each with purpose, actions, outputs, and
exit criteria.

### 4.10 `artifact-route`

```sh
python3 capability-discovery-routing-governance/tools-capability-router.py artifact-route --file ./payload.exe
```

Detects format from magic bytes (PE with machine/subsystem bits, ELF, ZIP/JAR/
APK/MSIX, 7z, RAR, CAB, ISO, WIM, QCOW2/VHD/VHDX/VMDK, tar/gzip/xz/bzip2/zstd,
deb, rpm, ar, CFB/MSI, PDF, PNG/JPEG/GIF) and returns a safe handler,
extraction policy (list-first, traversal rejection; this tool never extracts or
executes), risk, SHA-256, and related catalog capability ids.

### 4.11 `boundary`

```sh
python3 capability-discovery-routing-governance/tools-capability-router.py boundary --request "list archive entries and hash the originals"   # exit 0
python3 capability-discovery-routing-governance/tools-capability-router.py boundary --request "dump the password hashes"                      # exit 4
```

### 4.12 `clean-state`

Executes a bounded command in fresh, sanitized directories and compares stdout
hashs across repeats (2–3). Requires `--approve`. No shell is used; argv is a
JSON array capped by policy; timeouts apply; temporary directories are always
removed (including on SIGINT).

```sh
python3 capability-discovery-routing-governance/tools-capability-router.py clean-state \
  --argv '["python3","-c","print(42)"]' --repeat 2 --approve
```

`hidden_state`: `none` (identical), `detected` (outputs differ),
`inconclusive` (non-zero exits or timeouts).

### 4.13 `version-policy`

```sh
python3 capability-discovery-routing-governance/tools-capability-router.py version-policy
python3 capability-discovery-routing-governance/tools-capability-router.py version-policy --tool java --tool git
```

Compares installed versions with `version_pins` (`pin_status`: supported /
unsupported / missing / broken / unversioned) and evaluates
`capability_combinations` (windows-cross-build, compatibility-lanes,
virtualization-lanes, managed-dotnet-lane, media-lane).

## 5. Status contract (startup self-test)

Statuses (from the catalog contract, plus one documented extension):

`FOUND`, `MISSING`, `WRONG VERSION`, `BROKEN`, `LICENSE REQUIRED`,
`WINDOWS HOST REQUIRED`, `GPU REQUIRED`, `VIRTUALIZATION REQUIRED`,
`NETWORK REQUIRED`, `MACOS HOST REQUIRED` (extension for skills that delegate
to a macOS worker).

Precedence (also stored in `asset-selftest-probes.json`): host/resource gates
first (macos → windows → gpu → virtualization → network → license), then tool
results (`BROKEN` → `MISSING` → `WRONG VERSION` → `FOUND`).

Every self-test row contains the catalog's required fields:
`capability_id`, `status`, `detected_command`, `detected_version`,
`host_type`, `architecture`, `requirements`, `test_command`, `exit_code`,
`evidence_path`, `recommended_action` — plus `name`, `category`,
`risk_hint`, `hosts`, `preferred_host`, `tool_results`, `warnings`.

Probe semantics: presence via `PATH` lookup (first matching alternative
command), timeout (default 10 s) with one retry, `require_success` probes turn
non-zero exits into `BROKEN`, Python-module probes (`python3 -c "import
pefile, lief"`) turn import failures into `MISSING`, and version pins
(`>=`) produce `WRONG VERSION`. A present tool whose version cannot be parsed
stays `FOUND` with an explicit "pin unverified" message.

## 6. Safety and approval rules

- **Offline:** no network tools, sockets, or downloads exist in the code.
- **No secrets:** no credentials, tokens, endpoints, or license keys are
  embedded anywhere in the package.
- **Approval required** (exit 3 until granted): any risk class above
  `read_only` uses at least `--approve`; `administrator` also needs
  `--approver`; `external` (signing, publishing, financial, credential,
  security-sensitive, destructive, and rollback) also needs `--reason`.
- **Policy denial** (exit 4): gate `DENY`/`INCOMPLETE` and boundary blocks.
  Boundary patterns cover DRM bypass, anti-cheat bypass, activation/license
  circumvention, piracy artifacts, credential theft, purchase/account bypass,
  access-control bypass, and privacy-control bypass — while explicitly
  preserving legitimate compatibility, preservation, and inspection work.
- **Bounded execution:** only `clean-state` executes operator-supplied argv,
  always behind `--approve`, without a shell, with size/argv/timeout caps and
  guaranteed temp-directory cleanup.
- **Reversible by default:** `plan-change` refuses to act without a verified
  backup whose SHA-256 matches; rollback verifies the backup again before
  restoring.
- **Read-only inspection:** `artifact-route` identifies and routes only; it
  never extracts, mounts, or executes artifacts.
- **Originals preserved:** hashing is streamed; backups are written next to
  the target as `<target>.pcbak-<plan_id>` with `<target>.pcplan-<plan_id>.json`.

## 7. Configuration

All behavior data lives in the four `asset-*.json` files (edit them, no code
changes needed):

- **Risk/approval/boundary/limits/pins:** `asset-policy-pack.json`
  (`risk_levels`, `risk_keywords`, `prohibited` regexes, `version_pins`,
  `capability_combinations`, `limits`, `approval_flags`, `execution_gates`).
- **Probes:** `asset-selftest-probes.json` (`commands`, `version_patterns`,
  `require_success`, `timeout_sec`, `retries`; `contract` documents statuses
  and required fields).
- **Catalog requirements:** `asset-capability-catalog.json` — each skill's
  `requires` (`hosts`, `preferred_host`, `tools`, `software`, `network`, `gpu`,
  `virtualization`, `license`, `privileged`) is heuristic metadata derived from
  category defaults plus keyword rules (see `requirement_source` in the file).
  Refine any `requires` block to correct routing for your environment.
- **Schemas:** `asset-evidence-schema.json` (self-test row, evidence record,
  change plan).

Limits enforced from `policy.limits`: probe timeout/retries, input parse size
(5 MiB), asset size (50 MiB), route match cap (20), clean-state timeout
(30 s)/repeats (3)/argv (32 entries × 4096 chars), plan backup cap (2 GiB),
evidence record caps.

## 8. Validation performed

Run from the skill root with POSIX `sh` (all commands relative):

1. `python3 capability-discovery-routing-governance/tools-capability-router.py help`
   — exit 0, usage printed (also verified with no arguments → exit 2).
2. `python3 capability-discovery-routing-governance/tools-checks.py` — the
   bundled suite (structure, knowledge references, help, capability listing,
   ingest of Markdown/HTML/plain inputs, inventory and self-test contract
   fields, scope/limit/assume/out flags, routing by id and job text with
   changed-input comparison, gate allow/incomplete/deny, risk unapproved and
   approved, boundary allow/block, plan-change approval gate and full
   plan→modify→rollback recovery round-trip, double-rollback rejection,
   provenance record/show/validate, artifact detection for ELF/PE/JAR/text,
   decomposition, clean-state approval/identical/volatile/invalid input,
   version-policy, and envelope/error shape).
3. Full catalog self-test (`selftest`) — 400 rows, statuses sum to 400,
   contract fields present on every row; `inventory` — 40 probe rows.
4. Negative cases exercised: unknown skill id, unknown category, missing input
   files, invalid yes/no values, invalid `--argv` JSON, `--repeat` out of
   range, both/neither `--skill` and `--job`, both/neither `--target` and
   `--rollback`, missing evidence ledger, unknown version-policy tool.
5. Recovery path: backup integrity, restore, plan status transition to
   `rolled_back`, and rejection of a second rollback.
6. Clean temporary skill root containing one test `SKILL.md` beside this
   folder: help, `capabilities --json`, and `selftest --skill c01s01` re-run
   using only relative paths — verified.
7. Final delivery ZIP inspected: skill folder at ZIP root, no wrapper folder,
   no nested archives; extracted copy re-runs `help` and a self-test.

Final suite result: **68 checks run, 68 passed, 0 failed**
(`python3 capability-discovery-routing-governance/tools-checks.py` →
`all checks passed`, exit 0). The same suite was repeated from a clean
temporary skill root containing one test `SKILL.md` beside this folder —
68/68 passed there as well, using only relative paths, and no `__pycache__`
or other stray files were produced.

## 9. Limits and unavailable integrations

- **Worker lanes are not executed here.** Windows, GPU, network-enabled,
  virtualization, and macOS capabilities are reported
  (`WINDOWS HOST REQUIRED`, `GPU REQUIRED`, `NETWORK REQUIRED`,
  `VIRTUALIZATION REQUIRED`, `MACOS HOST REQUIRED`) and routed, not run. This
  matches the catalog rule: use real workers when a task requires them.
- **GPU capability is never claimed**: GPU skills always report
  `GPU REQUIRED` because the contract probe set cannot verify GPU drivers.
- **Virtualization** is detected via `/dev/kvm` on Linux only.
- **Software presence = PATH lookup.** Directory-installed tools (Ghidra,
  IDA, ILSpy, WinDbg, …) report `MISSING` until their launcher is on PATH.
  Add a probe with a version command to `asset-selftest-probes.json` to
  upgrade a soft dependency to a versioned probe.
- **Requirement metadata is heuristic** (category defaults + keywords). It is
  editable data, not code; refine `requires` blocks where a routing decision
  looks wrong for your estate.
- **Known host findings on the validation machine** (illustrative, not
  asserted as universal): `cmake`, `ninja`, `clang`, `sqlite3`, `ffmpeg`,
  `7z`, `r2`, `wine`, `qemu`, `dotnet`, `mono`, MinGW, `pefile`/`lief` were
  absent (`MISSING`); installed `java` was 11 against the OpenJDK 17 baseline
  pin (`WRONG VERSION`). `git`, `python3`, `gcc`, `node`, `npm`, `openssl`,
  `readelf`/`objdump`/`strings`, `file`, `zip`/`unzip`, `make`, ImageMagick
  were `FOUND`.
- **No macOS/Windows execution** was possible in the validation environment;
  those paths are covered by `--assume` simulations and the gate logic only.
- **Not included by design:** SKILL.md (belongs to the future skill root),
  network fetchers, cloud services, package installation, archive extraction,
  binary patching, and anything requiring credentials.

## 10. Troubleshooting and recovery

| Symptom | Meaning | Action |
|---------|---------|--------|
| exit 2 + `unknown skill id` | bad `cNNsNN` | run `capabilities --search ...` to find ids |
| exit 3 + `missing` list | approval not granted | re-run with the listed flags |
| exit 4 on `gate`/`boundary` | denied or incomplete | supply missing declarations or rephrase the request lawfully |
| exit 5 on `selftest --fail-on-not-found` | readiness failure | read `rows[].recommended_action` |
| `WRONG VERSION` | below `version_pins` | upgrade the tool or edit the pin in `asset-policy-pack.json` |
| `MISSING` for a tool you installed | not on PATH | add it to PATH or add an alternative command to the probe |
| plan rollback refuses | backup hash mismatch or already rolled back | inspect `plan.backup_sha256`; re-plan from the original source |
| evidence validation fails | record outside schema | check `event` enum and required fields in `asset-evidence-schema.json` |
| cancelled mid-run (130) | SIGINT | temp directories were removed; re-run; ledgers/appends are line-atomic enough to inspect with `provenance show` |
| unexpected internal error (1) | bug or bad asset | re-run with `--debug` for a traceback; validate the four asset files with `python3 -m json.tool` |

Recovery inventory: `plan-change --rollback` restores originals from verified
backups; `provenance show --validate` audits the ledger; `clean-state`
re-verifies determinism; every command is re-runnable and idempotent except
ledger appends (by design).

## 11. Extension and maintenance

- **Add a probe:** append an object to `probes` in
  `capability-discovery-routing-governance/asset-selftest-probes.json`
  (`id`, `test_command`, `commands`, `kind`, `version_patterns`,
  `require_success`, `timeout_sec`, `retries`) and, if versioned, a pin in
  `capability-discovery-routing-governance/asset-policy-pack.json`.
- **Add or correct a capability requirement:** edit the skill's `requires` in
  `capability-discovery-routing-governance/asset-capability-catalog.json`.
- **Add a prohibited pattern:** append to `prohibited` in
  `capability-discovery-routing-governance/asset-policy-pack.json` (regex must
  compile; keep it order-agnostic with two alternatives when both word orders
  matter).
- **Add a handler for a new artifact format:** extend `HANDLERS` and the
  detection chain in `capability-discovery-routing-governance/tools-governance-core.py`.
- **Re-run validation** after any change:
  `python3 capability-discovery-routing-governance/tools-checks.py`.
- **Versioning:** asset files carry `version` fields; bump them when content
  changes materially.

## 12. Compatibility with the future skill package

- This folder copies beside a root `SKILL.md` unchanged; it contains no
  `SKILL.md` and no subfolders, and every file uses the `tools-`,
  `knowledge-`, or `asset-` prefix.
- Combined-package depth stays within three levels from the skill root; the
  8 packaged files keep the combined package far below the 500-file limit.
- All internal references are relative, forward-slashed, and case-sensitive,
  rooted at `capability-discovery-routing-governance/…`.
