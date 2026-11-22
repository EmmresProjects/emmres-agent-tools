# Cloud Production Deployment — Skill Tool

**Folder:** `cloud-production-deployment/` · **Version:** 1.0.0 (engine 1.0.0) · **Status:** production-ready, self-contained, offline

A catalog-driven production-readiness toolchain for cloud, server, and deployment
work. It is built from the 30-category, 1,212-skill *Cloud, Server, and Production
Deployment — Skill Catalog* (Edition 1.0, November 2026), which ships inside this
folder as structured data (`asset-catalog.json`).

It is a **Skill Tool**, not a complete Copilot skill: it carries no `SKILL.md`,
makes **no network calls**, holds **no credentials**, and **never mutates cloud
infrastructure**. It validates, scores, plans, and documents. Human operators and
review gates execute the work; this tool makes their decisions structured,
evidenced, and repeatable.

---

## 1. Purpose and complete capability list

| # | Command | Capability |
|---|---------|------------|
| 1 | `capabilities` | Print the tool's own capability list and catalog statistics. |
| 2 | `version` | Print tool and engine versions. |
| 3 | `categories` | List all 30 catalog categories with per-category skill counts. |
| 4 | `list --category X [--pattern P]` | Enumerate the skill concepts in one category, filterable by archetype. |
| 5 | `search QUERY [--category X] [--pattern P] [--regex] [--limit N]` | Substring or regex search across all 1,212 skill names + descriptions. |
| 6 | `show --skill NAME\|ID` | Full record for one skill: id, category, archetype, catalog description, next steps. |
| 7 | `plan (--skill S \| --category C) [--provider P] [--environment E] [--rpo-minutes N] [--rto-minutes N] [--format md\|json]` | Generate a production deployment plan: phases with exit criteria, 4 approval gates, a 12-item evidence checklist, rollback procedure, escalation path, and the catalog's Core Production Acceptance reference. Skill-scoped plans are archetype-aware; category-scoped plans produce an adoption roadmap for all skills in the category. |
| 8 | `assess --config FILE [--rubric FILE] [--strict] [--format text\|json\|md]` | Score a deployment config JSON against the bundled 44-check production acceptance rubric (6 domains): pass/warn/fail per check with remediation + evidence hints, weighted 0–100 score, risk rating, per-domain rollup, retest procedure. Exit code 1 on `fail` (CI gate). |
| 9 | `decision-matrix --options FILE [--criteria FILE] [--memo TEXT] [--format text\|json\|md]` | Weighted decision matrix (9 catalog criteria: availability, latency, data residency, security & compliance, operational burden, recovery targets, scalability, lock-in, total cost). Ranks 2–12 options, computes a 0–100 weighted fit, and records every rejected option with the points it lost per decisive criterion — matching the catalog's decision-matrix skills ("with the rejected options recorded"). |
| 10 | `validate --kind config\|options --file FILE` | Structural validation of a deployment config or a decision-options file without scoring — designed for CI pre-commit/pre-apply gates. |
| 11 | `selftest` | Offline integrity check of every bundled asset and every engine path (10 checks). |
| 12 | `tools-build-catalog.py` | Maintenance: regenerate `asset-catalog.json` from the catalog PDF (needs `pypdf`) or from a plain-text dump; strict integrity gate (30 categories / 1,212 skills / sequential numbering). Not needed for normal use. |

Every capability is reachable through `tools-production-deployment.py`; shared logic
lives in `tools-deployment-engine.py` (imported by the CLI, no second copy of any rule).

---

## 2. Setup and dependencies

**Requirement: Python 3.8 or newer.** That is all. The tool uses only the Python
standard library (`argparse`, `json`, `re`, `importlib`, `datetime`, ...). No
packages, no network, no accounts, no cloud CLIs.

Verify your interpreter:

```sh
python3 --version        # expect 3.8+
```

Optional dependency — **only** for rebuilding `asset-catalog.json` from the PDF later:

```sh
python3 -m pip install pypdf   # tested with pypdf 6.x
```

Clone/copy the Skill Tool folder beside the parent package's `SKILL.md` (it is
designed to be copied verbatim next to a future `SKILL.md`; all asset lookups resolve
relative to the tool file's own location, never relative to the current directory).

All run commands below assume your current directory is the skill root (the folder
that contains `cloud-production-deployment/`); the equivalent `cd cloud-production-deployment`
form works too. Commands are shown for POSIX shells; on Windows use `py -3` instead of
`python3` and adjust quoting.

**First run:**

```sh
python3 cloud-production-deployment/tools-production-deployment.py selftest
```

expected tail: `selftest: 10/10 checks passed`

**Primary entry point:** `cloud-production-deployment/tools-production-deployment.py`
**Engine (loaded by the entry point, also usable as a library):** `cloud-production-deployment/tools-deployment-engine.py`
**Maintenance tool:** `cloud-production-deployment/tools-build-catalog.py`

---

## 3. Command reference with examples

In every example, replace `--format text` with `json` (or `md` where offered) for
machine-readable output. Global flag (before the subcommand): `--catalog PATH`
overrides the bundled catalog dataset (advanced). All `--output PATH` writes are
atomic and refuse to overwrite an existing file unless `--force` is given.

### 3.1 capabilities

```sh
python3 cloud-production-deployment/tools-production-deployment.py capabilities
python3 cloud-production-deployment/tools-production-deployment.py capabilities --format json
```

Prints the capability list, versions, and catalog statistics (30 categories, 1,212
skills, archetype distribution: 524 focused-capability, 214 evaluation-gate,
190 runbook-definition, 143 operate-service, 109 provision-from-code, 32 decision-matrix).

### 3.2 categories

```sh
python3 cloud-production-deployment/tools-production-deployment.py categories
```

Lists the 30 categories with skill counts. Use it to discover category numbers for
`list`, `plan --category`, and `search --category`.

### 3.3 list

```sh
python3 cloud-production-deployment/tools-production-deployment.py list --category 9
python3 cloud-production-deployment/tools-production-deployment.py list --category 23 --pattern runbook-definition --format json
```

`--category` accepts: number (`23`), id (`cat-23`), slug (`backups-retention-...`),
or a case-insensitive name substring (`backups`). `--pattern` filters by archetype
(`focused-capability | provision-from-code | runbook-definition | decision-matrix | evaluation-gate | operate-service`).

### 3.4 search

```sh
python3 cloud-production-deployment/tools-production-deployment.py search 'rollback' --limit 10
python3 cloud-production-deployment/tools-production-deployment.py search 'restore' --category 23
python3 cloud-production-deployment/tools-production-deployment.py search '^(backup|restore)' --regex --limit 20
python3 cloud-production-deployment/tools-production-deployment.py search 'OIDC' --format json
```

Substring search by default; `--regex` compiles the query case-insensitively
(invalid regex → exit 2, `error[invalid-regex]`). `--limit` is clamped to 1–200.
Output reports total hits vs. shown.

### 3.5 show

```sh
python3 cloud-production-deployment/tools-production-deployment.py show --skill 'Cloud Region Selection'
python3 cloud-production-deployment/tools-production-deployment.py show --skill 'tls-pki-certificates-and-cryptographic-trust:certificate-expiry-alert'
```

`--skill` accepts: full id, exact (case-insensitive) name, or a **unique**
substring — otherwise you get exit 2 with `error[ambiguous]` and the candidate list,
or `error[not-found]`. Six skill names legitimately exist in two categories
(e.g. *Quarterly Access Review*, *Incident Runbook*, *Database Restore Test*) — use
the id form then.

### 3.6 plan

```sh
# Single skill, Markdown plan to stdout
python3 cloud-production-deployment/tools-production-deployment.py plan --skill 'Cloud Region Selection'

# Written to a file atomically; existing files are never overwritten without --force
python3 cloud-production-deployment/tools-production-deployment.py plan --skill 'Kubernetes Cluster Provisioning' \
    --provider gcp --environment staging --rpo-minutes 30 --rto-minutes 120 \
    --output plan-eks.md

# Whole category → adoption roadmap (all 40 skills, each with archetype + acceptance hint)
python3 cloud-production-deployment/tools-production-deployment.py plan --category 23 --provider azure

# Machine-readable
python3 cloud-production-deployment/tools-production-deployment.py plan --skill 'Cloud Region Selection' --format json
```

**Providers:** `aws azure gcp oracle cloudflare digitalocean self-hosted multi-cloud`
**Environments:** `development test staging production shared-services security logging network sandbox`
**Defaults:** `--provider aws --environment production --rpo-minutes 15 --rto-minutes 60`.

Skill-scoped plans select phases from the skill's archetype (six archetype templates:
provision-from-code, runbook-definition, decision-matrix, evaluation-gate,
operate-service, focused-capability), each phase carrying **exit criteria**.
Every plan includes: the catalog intent of the chosen skill, 4 approval gates
(design → window → go/no-go → closure), a 12-item evidence checklist, a rollback
procedure, a 4-level escalation path, and the catalog's Core Production Acceptance
reference. Plans are generated from code + templates — they are starting points to
be refined under change governance, not substitutes for review.

`--template PATH` overrides `asset-plan-template.md` (must contain all `{{PLACEHOLDER}}`
tokens; unfilled placeholders abort with `error[template]`).

### 3.7 assess

```sh
# Human-readable report (bundled example passes 100/100)
python3 cloud-production-deployment/tools-production-deployment.py assess --config cloud-production-deployment/asset-example-deployment.json

# CI gate: markdown report to file, JSON to stdout from the same run
python3 cloud-production-deployment/tools-production-deployment.py assess --config deploy.json --format md --output acceptance.md
python3 cloud-production-deployment/tools-production-deployment.py assess --config deploy.json --format json > acceptance.json
echo "exit=$?   # 1 when verdict == fail"

# Release-blocking strict mode: any warn becomes a fail verdict
python3 cloud-production-deployment/tools-production-deployment.py assess --config deploy.json --strict

# Custom rubric (keep the schema of asset-assessment-rubric.json)
python3 cloud-production-deployment/tools-production-deployment.py assess --config deploy.json --rubric my-rubric.json
```

**Input:** a deployment config JSON (schema in §4.1). **Output:** score 0–100,
verdict (`pass`/`warn`/`fail`), risk (`low`/`moderate`/`high`/`critical`), per-domain
rollup (`cost`, `governance`, `operations`, `privacy`, `reliability`, `security`),
per-check result with severity, weight, reason, remediation and evidence hint, and a
5-step retest procedure. Checks whose preconditions don't apply (e.g. WAF checks on
`"exposure": "internal"`) are reported as `skip` and excluded from scoring.

**Verdict logic** (thresholds in the rubric, therefore configurable): any failing
`critical` or `high` check, or score < `failBelow` (50) → `fail`; any failing
`medium`/`low` check, any `warn`, or score < `passBelow` (85) → `warn`; otherwise
`pass`. Risk bands: ≥90 low · ≥75 moderate · ≥50 high · <50 critical; a critical-severity
failure forces `critical` risk. **Exit codes:** `0` for pass/warn, `1` for fail.

### 3.8 decision-matrix

```sh
python3 cloud-production-deployment/tools-production-deployment.py decision-matrix --options options.json
python3 cloud-production-deployment/tools-production-deployment.py decision-matrix --options options.json --format md --output decision.md
python3 cloud-production-deployment/tools-production-deployment.py decision-matrix --options options.json --memo "Reviewed by release council 2026-11-12."
```

Options file schema in §4.2. Criteria default to `asset-decision-criteria.json`
(weights sum 100; verified at load). Output: ranked table (score = Σ weight ×
(score−1)/4, i.e. every criterion contributes 0–weight points), the winner, and a
**recorded rejection list**: for every non-winner, the gap to the winner and its
three most decisive criterion shortfalls in points lost. Scores are always 1 (worst
fit) – 5 (best fit), including for lower-better real-world metrics (5 = lowest cost,
burden, or lock-in). Typical flow: `show --skill 'Cloud Provider Decision Matrix'` →
score → `decision-matrix` → attach output to the change/decision record.

### 3.9 validate

```sh
python3 cloud-production-deployment/tools-production-deployment.py validate --kind config --file deploy.json
python3 cloud-production-deployment/tools-production-deployment.py validate --kind options --file options.json --format json
```

`config`: checks JSON parse, 1 MiB size cap, object structure, presence of the
`service`/`environment`/`provider` identifiers, and lists all top-level keys.
`options`: full decision-matrix input validation (option count 2–12, unique names,
every criterion scored 1–5, weights sum 100). Use it in CI before `assess` /
`decision-matrix`. Exit 0 on success, 2 on validation failure.

### 3.10 selftest

```sh
python3 cloud-production-deployment/tools-production-deployment.py selftest
python3 cloud-production-deployment/tools-production-deployment.py selftest --format json
```

Ten offline checks: catalog load + internal consistency, every skill carries a known
archetype, search and id resolution work, rubric loads with positive weights, criteria
weights sum to 100, the bundled example config scores `pass`, a plan renders with no
unfilled placeholders, and the decision matrix ranks deterministically. Run after
any edit to this folder. Exit 0/1.

### 3.11 tools-build-catalog.py (maintenance only)

```sh
# Rebuild the dataset from the source PDF (requires pypdf)
python3 cloud-production-deployment/tools-build-catalog.py --pdf Cloud_Server_and_Production_Deployment_Skill_Catalog.pdf

# ...or from a previously extracted plain-text dump
python3 cloud-production-deployment/tools-build-catalog.py --text catalog_raw.txt --output asset-catalog.json
```

Atomically rewrites `asset-catalog.json` (temp file + rename, never half-written).
Integrity gates before writing: 30 categories, 1,212 skills, gapless per-category
numbering, globally unique skill ids — anything else aborts with exit 3
(`--allow-partial` downgrades this to a warning, intended only for mid-migration
catalog editions). Verified during development: the `--pdf` and `--text` paths both
reproduce the committed `asset-catalog.json` byte-for-byte after JSON normalization.

---

## 4. Input schemas

### 4.1 Deployment config (`assess`, `validate --kind config`)

Top-level JSON object. Every key feeds specific rubric checks (see
`asset-assessment-rubric.json` — each check documents `path`, `op`, `expect`,
`remediation`, `evidence`). Keys and types (all optional individually; missing keys
fail the checks that need them, they never crash the tool):

| Key | Type | Feeds checks (examples) |
|---|---|---|
| `service`, `environment`, `provider`, `region`, `exposure` | string (`exposure`: `public`\|`internal`) | identifiers echoed in the report; `exposure` gates the WAF/rate-limit checks |
| `naming.standardCompliant` | bool | `gov.naming-standard` |
| `tags.required.{owner, costCenter, environment, service, dataClassification, backupPolicy}` | object with all six non-empty | `gov.required-tags` (op `required_keys`) |
| `iac.{reviewed, pinnedArtifacts, policyChecked, remoteStateLocked, driftDetection}` | bool | `gov.iac-*` |
| `change.approvalRequired` | bool | `gov.change-approval` |
| `iam.{leastPrivilege, shortLivedCredentials}` | bool | `gov.iam-least-privilege`, `gov.short-lived-credentials` |
| `secrets.noEmbedded` | bool | `sec.secrets-not-embedded` (critical) |
| `secrets.rotationDays` | int ≤ 180 | `sec.secret-rotation` (warn-only) |
| `tls.{validCertificate, minVersion (`"1.2"`\|`"1.3"`), daysToExpiry}` | bool / enum / int | `sec.tls-*` |
| `encryption.{atRest, inTransit}` | bool | `sec.encryption-*` (critical-weight) |
| `network.{wafEnabled, rateLimited, noUnintendedPublicServices}` | bool | `sec.waf-enabled` & `sec.rate-limited` apply only when `exposure=public`; `sec.no-unintended-public` (critical) always applies |
| `vulnerability.{scanPassed, criticalFindings}` | bool / int | `sec.vuln-scan-passed`, `sec.no-critical-findings` |
| `logging.{centralized, auditTrail}` | bool | `ops.centralized-logs`, `sec.audit-trail` |
| `backup.{enabled, restoreTested, rpoMinutes, frequencyHours}` | bool/int | `rel.backup-enabled` (critical), `rel.restore-tested` (critical) |
| `dr.{rpoMinutes (≤60), rtoMinutes (≤240), drillCompleted}` | int / bool | `rel.rpo-defined`, `rel.rto-defined`, `rel.failover-drill` |
| `availability.{replicas (≥2), failoverTested}` | int / bool | `rel.replicas`, `rel.failover-tested` |
| `rollback.{tested, strategy (`blue-green`\|`canary`\|`rolling`\|`recreate`)}` | bool / enum | `rel.rollback-tested`, `rel.rollback-strategy` |
| `capacity.loadTestPassed` | bool | `rel.load-test` |
| `monitoring.{goldenSignals, sloDefined}` | bool | `ops.golden-signals`, `ops.slo-defined` (warn-only) |
| `alerting.{tested, onCallDefined}` | bool | `ops.alert-delivery-tested`, `ops.on-call-defined` |
| `runbook.exists` | bool | `ops.runbook-exists` |
| `ownership.registryUpdated` | bool | `ops.ownership-registry` |
| `cost.{budgetAlert, monthlyBudgetUsd (>0)}` | bool / number | `cost.budget-alert`, `cost.budget-defined` (warn-only) |
| `privacy.{dataClassification (`public`\|`internal`\|`confidential`\|`restricted`), retentionPolicy}` | enum / bool | `prv.data-classification`, `prv.retention-policy` |

Custom rubrics may use any of these engine ops: `eq neq gt gte lt lte in not_in
present truthy falsy contains matches required_keys`. `matches` is a regex search;
`contains` needs a list/str actual; numeric ops coerce ints/floats and reject bools.
A check may declare `"appliesWhen": {"path": "...", "equals": ...}` (skipped otherwise)
and `"onFail": "warn"` (downgrade fail → warn) and a `severity` of
`low|medium|high|critical` (critical/high fails force a `fail` verdict). See
`asset-assessment-rubric.json` for 44 worked examples.

### 4.2 Decision options (`decision-matrix`, `validate --kind options`)

```json
{
  "context": "What is being decided, for whom, by when (optional free text)",
  "memo": "Decision memo appended to the report (optional; --memo overrides)",
  "options": [
    { "name": "Option A", "notes": "free text kept in the record (optional)",
      "scores": { "availability": 5, "latency": 4, "residency": 4, "security": 4,
                  "operational-burden": 3, "recovery-targets": 4, "scalability": 5,
                  "lock-in": 3, "total-cost": 3 } },
    { "name": "Option B", "scores": { "...": "1-5 for every one of the 9 criteria" } }
  ]
}
```

Rules: 2–12 options; names unique (case-insensitive); every criterion id from
`asset-decision-criteria.json` must be present exactly as named; scores are numbers
1–5 (bools rejected); criteria weights must sum to 100 (checked at load). Missing or
invalid scores abort with exit 2 and a per-option problem list — the matrix is never
computed on partial data.

### 4.3 Limits

| Limit | Value |
|---|---|
| Deployment config file | ≤ rubric `maxConfigBytes` (bundled: 1 MiB) |
| Options file | ≤ 256 KiB |
| Catalog / rubric / criteria / template assets | ≤ 8 MB each |
| `search --limit` | ≤ 200 |
| Decision options | 2–12 |
| Output writes | atomic (temp + rename); existing files need `--force` |
| Runtime | fully local; no timeouts needed, no retries, no network |

---

## 5. Safety and approval rules

1. **This tool changes nothing it does not own.** It reads files you name and writes
   files you opt into via `--output` (atomically, never silently overwriting). It
   never calls cloud APIs, CLIs, shells, package managers, or the network. There is
   no `apply`, no `destroy`, no credential handling anywhere in the codebase.
2. **It is advisory by design.** `assess` and `decision-matrix` produce evidence and
   scores; they do not grant approval, and a green report is not a release approval.
   Release authority stays with your change-governance process. The generated `plan`
   embeds this: 4 explicit approval gates (design, window, go/no-go, closure) and a
   rollback procedure are mandatory sections, not optional extras.
3. **Destructive / irreversible / production actions** (applying IaC, deleting or
   rotating resources, failing over, widening access) must be executed by authorized
   humans or your own pipelines under change control, with the evidence this tool
   helps you structure. Never use model- or tool-generated commands to perform them
   without review.
4. **No secrets in inputs.** The config schema has no secret fields — do not add any
   (keys, tokens, passwords). Reports echo only service/environment/provider/region/
   exposure identifiers. If you must keep one in a config for other tooling, it will
   still never be printed by this tool, but prefer a vault path reference.
5. **Approval before destructive operations applies to your use of this tool's
   outputs too:** e.g., when `assess` flags `sec.no-unintended-public` or
   `sec.secrets-not-embedded` as critical, remediation means controlled changes under
   a change ticket with rollback — not an emergency console edit.
6. **Data handling:** catalog data is local and contains no personal data; your config
   files stay wherever you keep them; nothing leaves the machine.

---

## 6. Validation and tests actually completed

Executed on 2026-11-06/07 against this exact folder (Python 3.13, Linux):

- `selftest`: **10/10 PASS** (catalog consistency 30/1,212; rubric loads; criteria sum 100; example config → verdict `pass`, score 100.0; plan renders with no unfilled placeholders; decision matrix deterministic).
- **Every subcommand exercised through its real interface**: `capabilities` (text + JSON), `categories`, `list` (valid + invalid category → exit 2), `search` (substring, regex, invalid regex → exit 2, zero-hit, `--limit`), `show` (by name, by id, ambiguous → exit 2 with candidate list, unknown → exit 2), `plan` (skill md stdout, file write, `--force` overwrite, category-to-json, invalid provider (argparse → 2), missing `--skill/--category` → 2, negative RPO → 2), `assess` (pass 100.0/100 exit 0; doctored failing config → verdict `fail`, score 78.8, risk `critical`, exit 1; internal-exposure config → WAF/rate-limit checks `skip`; warn-only → exit 0; `--strict` → verdict `fail`, exit 1; `--format json|md` outputs parsed/reviewed; missing file and malformed JSON → exit 2 with structured `error[kind]`), `decision-matrix` (3-option fixture ranks deterministically; md output reviewed; malformed inputs — missing scores / out-of-range 9 / duplicate names / 1 option / list `scores` → exit 2 with per-option problem list), `validate` (config OK; options OK; options-file-as-config → exit 2), `selftest --format json` (parsed), `tools-build-catalog.py` (integrity gate; rebuild from `--text` and from the source PDF both reproduce `asset-catalog.json` with identical parse results: 30/1,212).
- **Overwrite protection**: second `--output` run refuses with `error[output-exists]` (exit 2); `--force` succeeds.
- **Broken pipe** (`| head`) handled without tracebacks; Ctrl+C path returns 130 by construction (`KeyboardInterrupt` handler).
- **Clean-room portability**: folder copied beside a minimal test `SKILL.md` in a fresh temp directory; `capabilities`, `selftest` (10/10), `assess --config cloud-production-deployment/asset-example-deployment.json` (verdict `pass`), and `search` all ran correctly **using only relative paths**, with no `__pycache__` left behind in the package.
- **Static scan**: no absolute paths, hostnames, URLs, credentials, or machine-specific references in any shipped file; the folder contains no `SKILL.md`, no subfolders, and no non-prefixed files.
- Not performed (and not claimed): live cloud API verification, penetration tests, DR drills, supply-chain scanning of this folder itself. The tool does not attempt them by design.

If you change any file here, re-run: `selftest`, one full `assess --config asset-example-deployment.json`, one `plan`, one `decision-matrix` fixture run, and the clean-room check from §8.

---

## 7. Limits and unavailable integrations

- **Static catalog snapshot.** `asset-catalog.json` encodes Edition 1.0 (Nov 2026).
  New editions require a rebuild with `tools-build-catalog.py` (integrity-gated).
- **Archetypes are derived, not editorial.** Each skill's `pattern` is classified
  from its catalog description wording (e.g. "pass, warn, and fail thresholds" ⇒
  `evaluation-gate`). It affects plan phasing and filtering only; it does not change
  the catalog text.
- **No live probing.** The tool cannot verify DNS, TLS, endpoints, cloud state, or
  credentials. `plan` and its evidence checklist tell you *what* to verify and with
  what evidence; a human (or your pipeline) performs and attaches it.
- **Assess is a control checklist, not a pentest/audit.** The 44 bundled checks map
  to the catalog's Core Production Acceptance Reference; passing does not certify
  security, reliability, or compliance. Extend or replace the rubric via `--rubric`
  for your standards (SOC 2, ISO 27001, org baselines) — keep the schema stable.
- **Provider breadth.** Provider names are recognized for plan labeling and defaults
  only (`aws azure gcp oracle cloudflare digitalocean self-hosted multi-cloud`); no
  provider SDKs are used.
- **Windows/macOS:** fully supported via `python3`/`py -3`; only path-separator
  differences apply (all internal paths use pathlib/forward slashes).
- **Scale:** the catalog is queried in-memory (~550 KB JSON); sub-millisecond for all
  operations on any modern machine.

---

## 8. Troubleshooting, recovery, extension, maintenance

**Common issues**

| Symptom | Fix |
|---|---|
| `error[not-found]: No skill matches ...` | Use `search <term>`; remember six skill names exist in two categories — disambiguate with the full `id` from `search`/`list --format json` output. |
| `error[ambiguous]` | Pass the full id. Candidates are printed in the error. |
| `error[invalid-json]` with line/column | Validate the file (`python3 -m json.tool file.json`); keep configs ≤ 1 MiB; no comments/trailing commas. |
| `error[output-exists]` | Pass `--force`, or choose a fresh `--output` path. |
| `error[too-large]` | Split or trim the input; raise `maxConfigBytes` inside a custom rubric if truly needed. |
| `selftest` FAIL on catalog | Do not hand-edit `asset-catalog.json`; rebuild via `tools-build-catalog.py`; if counts differ, the source changed — review the diff carefully before accepting. |
| Plans look generic for my service | Pass `--skill` with the most specific catalog entry, add `--provider`/`--environment`/`--rpo-minutes`/`--rto-minutes`; refine the generated plan under your change process. |
| WAF/rate-limit checks show `skip` | Expected when `exposure` ≠ `public`. Set `"exposure": "public"` only if it truly is internet-facing. |
| Non-zero exit in CI | `assess` exits 1 on `fail` (or any warn under `--strict`); that is the gate doing its job — read the FAIL block, remediate, retest. |

**Recovery:** if a rebuild corrupts `asset-catalog.json`, restore it from version
control or re-run `tools-build-catalog.py` — both paths are deterministic and both
reproduce the same parse. All writes (`--output`, dataset rebuild) are atomic.
Nothing else in your workspace is touched; cleanup is limited to the `.tmp` sibling
it removes automatically on failure.

**Extension points**

- **New checks / stricter org policy:** copy `asset-assessment-rubric.json`, add
  checks (ops: `eq neq gt gte lt lte in not_in present truthy falsy contains matches
  required_keys`; optional `appliesWhen`, `onFail: warn`), bump `rubricVersion`, run
  `--rubric your-rubric.json`. Keep `verdictThresholds` semantics intact.
- **Different scoring of designs:** copy `asset-decision-criteria.json`, adjust
  weights (must still sum to 100) or add criteria, bump `criteriaVersion`.
- **Plan wording/gates:** override with `--template` (all `{{PLACEHOLDER}}` tokens
  required) or edit phases in `PATTERN_PHASES` inside `tools-deployment-engine.py`.
- **New archetype:** add a `PATTERN_RULES` regex in `tools-build-catalog.py`, a
  `PATTERN_PHASES` block in `tools-deployment-engine.py`, and rebuild the dataset.
- **Library use:** `tools-deployment-engine.py` exposes `Catalog`, `load_rubric`,
  `assess_config`, `load_criteria`, `evaluate_decision`, `build_plan`,
  `render_plan_markdown` — import it the same way the CLI does (importlib, since the
  filename is hyphenated).

**Maintenance checklist (after any change):** run `selftest`; run the example
`assess` (expect 100.0/pass); spot-check one `plan` and one `decision-matrix`;
confirm no file in the folder gained a SKILL.md or a subfolder; re-create the
delivery ZIP; re-run the clean-room check (§6).

---

## 9. File inventory (this folder)

| File | Role |
|---|---|
| `tools-production-deployment.py` | **Primary entry point** — CLI: argument parsing, command implementations, output rendering, atomic `--output` writes, exit codes. |
| `tools-deployment-engine.py` | Engine library loaded by the entry point: asset loading (size-capped, structured errors), catalog engine, assessment engine (ops/scoring/verdict/risk), decision-matrix engine, plan builder + template renderer. No CLI of its own; no I/O except reading its own folder and writing via the shared atomic writer. |
| `tools-build-catalog.py` | Maintenance: parse the catalog PDF (`--pdf`, needs `pypdf`) or a text dump (`--text`) into `asset-catalog.json`; integrity-gated, atomic. |
| `knowledge-readme.md` | This document — the single knowledge file for the Skill Tool. |
| `asset-catalog.json` | Structured dataset of the source catalog: 30 categories, 1,212 skills (id, number, name, description, archetype), pattern legend, and the catalog's Core Production Acceptance Reference + Design Notes. Generated; edit via `tools-build-catalog.py` only. |
| `asset-assessment-rubric.json` | 44 acceptance checks in 6 domains (governance, security, reliability, operations, cost, privacy): path/op/expect, severity, weight, `appliesWhen`, `onFail`, remediation, evidence hint; score scale, risk bands, verdict thresholds, retest procedure. |
| `asset-decision-criteria.json` | 9 weighted decision criteria (sum 100) with directions, descriptions and 1/3/5 score guides — the catalog's decision-matrix vocabulary. |
| `asset-plan-template.md` | Markdown frame for generated plans; `{{PLACEHOLDER}}` tokens filled by the engine; override with `--template`. |
| `asset-example-deployment.json` | Realistic production example config (checkout-api on AWS) that passes the rubric 100/100 — used as `assess` input in docs and in `selftest`. Safe fixture; contains no secrets. |

**ZIP packaging:** this folder is distributed inside a single delivery ZIP containing
the folder directly at the archive root. The ZIP is a delivery container only and is
not a Copilot skill package; the combined package skill root will hold the parent
`SKILL.md` beside this folder.

**Future skill-root reference convention:** always `cloud-production-deployment/<file>`
from the skill root, e.g. `cloud-production-deployment/tools-production-deployment.py`,
`cloud-production-deployment/asset-catalog.json`. No absolute paths anywhere.
