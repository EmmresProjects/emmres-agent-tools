# Cloud Production Operations Skill Tool

## Purpose and safety boundary

`cloud-production-operations` is a reusable, offline, provider-neutral planning and evidence-governance component for the complete **Cloud, Server, and Production Deployment Skill Catalog**. It unifies all **18 categories and 249 catalog capabilities** in one folder.

The tool turns exact catalog names and descriptions into reviewable work items, safeguards, input gates, evidence controls, JSON artifacts, and Markdown runbooks. It accepts individual capabilities, categories, several categories, JSON, plain text, Markdown, and copied HTML. Selecting a category expands and deduplicates every related capability into the same request.

**Important boundary:** this component does not provision, configure, deploy, rotate, delete, publish, purchase, fail over, or otherwise change a cloud or production system. It does not call provider APIs, invoke provider CLIs, fetch evidence URLs, execute arbitrary commands, or use an AI model. `execute` intent means **approval-gated external execution handoff**, not execution by this tool. Every plan records zero provider calls and zero shell commands. A readiness result is not approval and is not proof that an external change occurred.

This boundary makes every independent catalog, selection, validation, planning, policy, evidence, rendering, and packaging function testable without credentials. Provider-specific execution remains the responsibility of a separately approved executor.

## Files, roles, and loading order

The component contains eight files, all directly inside `cloud-production-operations/`:

| File | Role |
|---|---|
| `cloud-production-operations/tools-cloudops.py` | Primary runnable CLI and only operational entry point. Parses commands, loads configuration, emits structured artifacts, and enforces exit codes. |
| `cloud-production-operations/tools-engine.py` | Shared catalog, selection, schema, risk, planning, approval, evidence, Markdown, deadline, and atomic-write engine. Loaded dynamically by the primary CLI; not a standalone command. |
| `cloud-production-operations/tools-test.py` | Standard-library unit and real-CLI regression suite. Kept separate so tests can invoke the production entry point in isolated temporary directories without affecting runtime code. |
| `cloud-production-operations/asset-capabilities.json` | Complete reusable catalog: 18 categories, 249 stable IDs, exact names, source descriptions, and catalog numbering. |
| `cloud-production-operations/asset-default-policy.json` | Active default input/output limits, approval windows, evidence freshness, execution gates, and safe-output policy. |
| `cloud-production-operations/asset-request-schema.json` | JSON Schema 2020-12 request contract for editors and external validators. The runtime implements the bounded subset used by this schema without downloading the `$schema` identifier. |
| `cloud-production-operations/asset-example-request.json` | Valid two-capability preview request, reusable as a template and test fixture. |
| `cloud-production-operations/knowledge-readme.md` | This single operational knowledge file. |

Loading order for request commands:

1. `tools-cloudops.py` loads sibling `tools-engine.py` without creating bytecode caches.
2. The CLI loads `--policy`, enforces its timeout ceiling, then loads `--catalog`.
3. `validate`, `plan`, `assess`/`evaluate`, and `run` load `--schema` and `--input`.
4. The engine resolves and validates the unified selection, creates the plan, and, where requested, assesses evidence.
5. The CLI renders JSON or Markdown to stdout or atomically to `--output`.

The test runner loads the same engine and invokes `tools-cloudops.py` as a subprocess without a shell. No internal subfolder, installation catalog, hidden address, or machine-specific path is required.

## Setup and dependencies

### Runtime

- Python **3.10 or newer**, 64-bit recommended for large all-catalog reports.
- Standard library only. There is no `pip install`, package manager, compiler, JavaScript runtime, provider CLI, network service, database, credential, or paid dependency.
- Tested with **Python 3.13.16 on Linux**. The code uses portable Python and `pathlib`; Windows/macOS are supported by the language constructs but were not separately executed in this environment.
- Commands below use **bash** explicitly and start at the future skill root containing the parent package's root `SKILL.md` beside this component folder.

Check the runtime:

```bash
python3 --version
```

No setup command is needed. Preserve the folder name in examples or adjust every command consistently if a parent package chooses a different stable folder name.

### Primary command

Show built-in help:

```bash
python3 cloud-production-operations/tools-cloudops.py --help
```

Show the tool version:

```bash
python3 cloud-production-operations/tools-cloudops.py --version
```

Run the complete validate + plan + assessment pipeline with the included valid preview request:

```bash
python3 cloud-production-operations/tools-cloudops.py run --input cloud-production-operations/asset-example-request.json
```

Run the regression suite:

```bash
python3 cloud-production-operations/tools-test.py
```

## Complete operational capability list

The CLI has six primary operations, with aliases for discoverability:

| Operation | Implemented behavior |
|---|---|
| `capabilities` (alias `capability-list`) | Lists all 249 entries, searches IDs/names/descriptions/categories, filters by exact capability/category, and accepts a selection file. |
| `select` (alias `resolve`) | Resolves plain text, Markdown, copied HTML, or selection JSON; ignores markup and descriptions; expands categories; deduplicates capabilities; reports unknown declared names. |
| `validate` | Applies defaults, validates the bundled request schema subset, rejects sensitive keys and placeholder ownership, resolves selections, and reports execution input blockers separately. |
| `plan` | Produces capability-specific work items from every selected catalog description, risk/flags, preflight, safeguards, global controls, evidence controls, verification, recovery context, and execution-handoff status. |
| `assess` (alias `evaluate`) | Evaluates input-backed global controls and supplied evidence for selected task/verification controls, including freshness, duplicates, unknown controls, explicit failures, and not-applicable policy. |
| `run` | Emits one combined validation-backed plan and assessment artifact. It never skips the evidence assessment or performs an external change. |

Every catalog entry in the appendix is reachable by all request commands through its stable ID. No listed catalog capability is implemented as a live provider integration; each receives the same complete offline planning and evidence pipeline plus category-specific evidence guidance and content-derived tasks/safeguards.

## Command reference and examples

### 1. Discover and search capabilities

List the complete catalog as JSON:

```bash
python3 cloud-production-operations/tools-cloudops.py capabilities
```

Use the explicit capability-list alias and search text:

```bash
python3 cloud-production-operations/tools-cloudops.py capability-list --query "smoke testing"
```

Resolve one category into all of its capabilities:

```bash
python3 cloud-production-operations/tools-cloudops.py capabilities --category identity-access-and-privileged-operations
```

Combine categories and individual capabilities; duplicates are removed:

```bash
python3 cloud-production-operations/tools-cloudops.py capabilities --category billing-finops-and-capacity-economics --category domains-dns-certificates-and-edge-routing --capability dns-management
```

Render a human-readable catalog:

```bash
python3 cloud-production-operations/tools-cloudops.py capabilities --query backup --format markdown
```

### 2. Resolve copied selections

Given a relative file `selection.html` containing copied list items, classes, data attributes, numbering, and descriptions:

```bash
python3 cloud-production-operations/tools-cloudops.py select --input selection.html
```

The same operation works for `.txt`, `.md`, and selection `.json` files. HTML tags, CSS classes, data attributes, script/style contents, list numbers, Markdown emphasis, heading marks, checkbox marks, extra spacing, and `Description:` lines are ignored. Exact names are matched case-insensitively after punctuation/whitespace normalization. Unknown declared `Skill Name:` values produce warnings when at least one valid selection exists; a selection with no valid names fails safely.

A text selection can go directly into `plan`; supply accountable context because there is no safe default owner or service:

```bash
python3 cloud-production-operations/tools-cloudops.py plan --input selection.md --service orders-api --owner platform-team --provider azure --environment staging
```

For text input, environment defaults to `production` and provider to `unspecified`; service and owner are required through overrides. JSON input retains its own context unless explicitly overridden.

### 3. Validate a request

```bash
python3 cloud-production-operations/tools-cloudops.py validate --input cloud-production-operations/asset-example-request.json
```

`valid_for_planning` means the request schema and selection are usable. `valid_for_execution` is intentionally false for preview requests and becomes true only for an `execute` request with no execution input blockers. Validation does not assert that evidence or an external system is healthy.

### 4. Generate a plan

JSON to stdout:

```bash
python3 cloud-production-operations/tools-cloudops.py plan --input cloud-production-operations/asset-example-request.json
```

Markdown to a configurable relative output:

```bash
python3 cloud-production-operations/tools-cloudops.py plan --input cloud-production-operations/asset-example-request.json --format markdown --output cloud-production-operations-plan.md
```

If the output exists, the command fails without changing it. After reviewing the existing file, explicit replacement is:

```bash
python3 cloud-production-operations/tools-cloudops.py plan --input cloud-production-operations/asset-example-request.json --format markdown --output cloud-production-operations-plan.md --force
```

`--force` authorizes only replacement of that output file. It does not satisfy an approval control or authorize any cloud action.

An `execute` plan can be generated while blockers remain so reviewers can see the required work. Blockers appear in `validation.execution_blockers`; they prevent a ready assessment until corrected.

### 5. Assess evidence

Create a plan, use its actual control IDs to attach non-sensitive evidence to the request's `evidence` array, then run:

```bash
python3 cloud-production-operations/tools-cloudops.py assess --input request-with-evidence.json
```

`evaluate` is an exact alias. Evidence `source` values are provenance strings only; the tool never opens or fetches them. For preview intent, task evidence is optional, but supplied failed, stale, future-dated, duplicate, or unknown evidence fails the assessment. For execute intent, every task and verification control requires current passing evidence.

### 6. Run the complete pipeline

```bash
python3 cloud-production-operations/tools-cloudops.py run --input request-with-evidence.json --output cloud-production-operations-result.json
```

The output contains `plan` and `assessment`. Exit code 3 means the artifact was produced successfully but is not ready; inspect `assessment.failures`, correct the request or perform the external work, and rerun. It does not mean the tool attempted a partial cloud change.

### Custom assets, policy, schema, and limits

Every operational path is configurable:

```bash
python3 cloud-production-operations/tools-cloudops.py plan --input request.json --catalog custom-capabilities.json --policy custom-policy.json --schema custom-request-schema.json --timeout-seconds 60 --output custom-plan.json
```

Custom catalogs must preserve the documented structure, unique valid IDs, non-empty descriptions, and accurate counts. A custom schema must use only the runtime-supported schema subset described below. Unsupported keywords, excessive schema complexity, missing runtime fields, an execute-by-default request, or approval-by-default are rejected instead of being silently ignored. Custom policies are type/range checked; expired approvals cannot be enabled, and atomic writes, sensitive-key protection, and overwrite approval cannot be disabled. Custom limits do not remove file-format or approval safety checks.

## Request schema and defaults

The normative reusable schema is `cloud-production-operations/asset-request-schema.json`. Runtime validation supports the subset it uses: `type`, `const`, `enum`, `properties`, `required`, `additionalProperties: false`, `anyOf`, array `items`, `maxItems`, `uniqueItems`, string length bounds, `format: date-time`, and integer minimum/maximum. It is not advertised as a general-purpose JSON Schema implementation. Defaults are applied recursively before validation.

### Top-level shape

| Field | Type/default | Meaning |
|---|---|---|
| `schema_version` | Required constant `1.0` | Request contract version. |
| `selection` | Required object | `capabilities`, `categories`, and/or `text`. At least one resolved capability is required. |
| `context` | Required object | Service, owner, provider, environment, and optional scope metadata. |
| `change_intent` | `preview` or `execute`; default `preview` | Preview planning versus approval-gated external handoff. Neither performs external work. |
| `approval` | Object with safe false/empty defaults | Attributable approval decision and UTC window. |
| `recovery` | Object with empty/zero defaults | Rollback, non-sensitive backup reference, RTO, and RPO. |
| `verification` | Object with empty arrays | Measurable success criteria and monitoring observations. |
| `evidence` | Array; default empty | Current control-specific evidence records. |

Unknown properties are rejected. Keys whose names indicate passwords, secrets, tokens, API/private/access keys, or credentials are rejected recursively; use a non-sensitive reference instead. Automated key checks cannot identify every secret embedded in free text, so callers must also redact values, summaries, paths, and provenance.

### `selection`

- `capabilities`: up to 249 unique strings; each is a stable ID or exact catalog name.
- `categories`: up to 18 unique strings; each is a stable category ID or exact category name. Every related capability is included.
- `text`: up to 200,000 characters of plain text, Markdown, or copied HTML.
- Multiple selectors are unioned in first-resolution order and deduplicated by ID.
- Numbering marks are syntax, not catalog identity. Category names, not their numbers, should be used in JSON.

### `context`

| Field | Allowed/required value |
|---|---|
| `service` | Required non-empty real service name; placeholders such as `tbd` and `unassigned` are rejected. |
| `owner` | Required accountable owner/team; placeholders are rejected. |
| `provider` | `unspecified`, `aws`, `azure`, `gcp`, `oracle`, `hybrid`, `self-hosted`, or `other`. Execute requires a provider other than `unspecified`. |
| `environment` | `development`, `test`, `staging`, or `production`. |
| `regions` | Up to 50 unique strings; default empty. These are metadata only and are not queried. |
| `change_ticket` | Optional for preview; required by default for execute. |
| `data_classification` | `public`, `internal`, `confidential`, `personal`, `regulated`, or `secret`; default `internal`. This label never accepts the data itself. |
| `blast_radius` | Scope statement; required for production or high-risk execute requests. |

### `approval`

Fields are `approved` (boolean), `approver`, `reference`, `approved_at`, and `expires_at`. Timestamps must be ISO 8601 with `Z` or an explicit offset. Production execution and high/critical, destructive, security-sensitive, financial, or publishing execution require approval by default. Approved requests need an approver, reference, valid time window, non-future approval time, future expiry, and approval age no greater than 24 hours. Expiry must be after approval time.

Supplying `approved: true` only records an assertion for evaluation; the tool cannot verify an external approval system. The external executor must independently verify it.

### `recovery` and `verification`

- `rollback_strategy`: required for production or high/critical execute requests by default.
- `backup_reference`: required for capabilities classified as destructive. Use a locator/reference, never backup contents or credentials.
- `rto_minutes`, `rpo_minutes`: integers from 0 through 525600; recorded for planning. Zero means unspecified, not an achieved recovery objective.
- `success_criteria`: at least one measurable item is required for execute.
- `monitoring`: at least one item is required for production execute.

### `evidence` items

Each item requires:

```json
{
  "control_id": "deployment-smoke-testing:task-01",
  "status": "passed",
  "source": "change-system://EXAMPLE-123/smoke-result",
  "observed_at": "2026-11-07T09:00:00Z",
  "summary": "Non-sensitive observed result summary.",
  "justification": ""
}
```

Statuses are `passed`, `failed`, and `not-applicable`. Required controls reject not-applicable evidence under the default policy, even with a justification. Evidence older than 720 hours or more than 300 seconds in the future fails. Duplicate controls and controls outside the plan fail. Global input controls are evaluated from the request and do not accept substitute evidence items.

The timestamp above is illustrative request data, not a claim of observed evidence. Test fixtures use synthetic `test://` provenance and perform no provider action.

## Output artifacts

All JSON output is UTF-8, indented, and parseable. Markdown escapes user/catalog values for safe table and heading rendering. Output paths may be stdout or a configurable file. File output is bounded, written to a temporary sibling, flushed, and atomically replaced only after success. Temporary files are cleaned on errors and cooperative cancellation checks.

### Plan

`cloud-production-operations-plan` includes:

- Stable `plan_id` derived from the normalized request with evidence excluded.
- Generation time, context, intent, recovery, verification, approval, selection, and policy version.
- Explicit `performed_by_this_tool: false`, `live_provider_calls: 0`, and `shell_commands_executed: 0`.
- Capability name, ID, category, exact source description, heuristic risk, and destructive/financial/security/publishing flags.
- Content-derived tasks with stable control IDs, provider-neutral preflight, safeguards, verification, and category-specific suggested evidence.
- Global input controls and separate execution blockers.
- Counts for categories, capabilities, tasks, and required evidence controls.

The work-item splitter preserves every comma/semicolon-delimited concept in each curated description. Some resulting phrases inherit the leading action verb and therefore require human provider-specific refinement; they are not executable commands.

### Assessment and run

`cloud-production-operations-assessment` includes readiness/status, failure list, control results, counts, and `external_execution_performed: false`. Status values are:

- `preview-assessment-passed`: no supplied preview evidence or input control failed; optional work may remain pending.
- `ready-for-external-handoff`: all current input and required evidence controls passed. External execution has still not occurred.
- `not-ready`: one or more failures/blockers remain.

`cloud-production-operations-run` wraps both artifacts and duplicates the top-level readiness status for automation.

### Capability and selection outputs

`capability-list` groups complete capability metadata under selected categories. `selection-result` returns resolved IDs, names, categories, descriptions, risk, warnings, and count. `validation-report` separates schema/selection findings from execution blockers.

## Risk and approval rules

Risk is a deterministic planning heuristic, not a compliance certification or threat model:

- `critical`: selected highly destructive, recovery, signing, key, break-glass, immutability, or legal-hold patterns.
- `high`: privileged, network, compute, release, data, recovery, secret, security, IaC, incident, runtime, or other sensitive patterns.
- `low`: primarily documentation, design, selection, inventory, review, assessment, training, or catalog work without higher-risk flags.
- `medium`: all remaining entries.

Flags independently identify destructive, financial, security-sensitive, and publishing concerns. Production/high-risk scope, explicit approvals, least privilege, bounded blast radius, rollback, current evidence, monitoring, and post-change verification are represented as gates. Destructive actions additionally require a backup reference; financial and publishing actions require their accountable external approvers. No flag or risk label grants permission.

## Limits, retries, cancellation, and recovery

Default limits come from `cloud-production-operations/asset-default-policy.json`:

| Limit | Default |
|---|---:|
| Request/selection input | 2 MiB |
| Catalog | 8 MiB |
| Policy/schema independently | 1 MiB hard reader bound |
| Resolved capabilities | 249 |
| Evidence items | 5,000 |
| Selection text | 200,000 characters |
| Other individual strings | 4,000 characters unless the schema is stricter |
| Rendered output | 32 MiB |
| Default cooperative deadline | 30 seconds |
| Maximum configurable deadline | 300 seconds |
| Evidence age | 720 hours |
| Evidence future skew | 300 seconds |
| Approval age | 24 hours |

Custom policies have independent hard ceilings: 64 MiB for request/catalog input, 128 MiB for output, 1,000 capabilities, 10,000 evidence items, 100,000 characters for a general string, and 3,600 seconds for timeout settings. Selection text remains hard-capped at 200,000 characters. These ceilings do not imply that large custom catalogs have been performance-tested; the shipped 249-capability catalog is the tested configuration.

The deadline is checked between file loads, selection work, capability plan generation, evidence items, and output commit. It is cooperative, not an operating-system hard interrupt of an individual standard-library call. `SIGINT`/cancellation returns 130 where caught. No output temporary file is intentionally retained.

There are **zero automatic retries**. Local parsing, planning, and atomic writes are deterministic; automatic retries could mask invalid input or complicate overwrite authorization. Recovery is to correct the reported input, retain the previous valid output, and rerun with a fresh deadline. Reissuing `--force` is an explicit reviewed decision, not a retry policy.

There are no background jobs, open sockets, provider sessions, temporary credential files, or external resources to clean up. Standard output cannot be rolled back after a consumer receives it; use `--output` when atomic file recovery is required.

## Safety and unavailable integrations

- No embedded secrets, private endpoints, paid services, provider credentials, or external downloads.
- No arbitrary code, shell, network, provider API, email, status-page, ticketing, financial, or publishing integration.
- No live Terraform/OpenTofu/Bicep/ARM/CloudFormation/Pulumi/Ansible execution and no Kubernetes, database, DNS, certificate, IAM, or security-device mutation.
- No evidence-source fetching, cryptographic evidence verification, external approval verification, or real RTO/RPO measurement. Source strings and recovery numbers are claims to be verified externally.
- No AI model or prompt configuration is used. Policy, provider label, paths, catalog, schema, and output storage are configurable through files/arguments.
- The tool cannot prevent a hosting, catalog, permission, or platform failure in a separate executor. Local package validation does not prove a hosted integration is accessible.
- JSON Schema's public `$schema` URI is an identifier only and is never fetched.

## Troubleshooting and recovery

| Symptom/code | Cause and recovery |
|---|---|
| `input-not-found`, exit 4 | Check the exact case-sensitive relative path from the future skill root. Do not assume an installation catalog path. |
| `invalid-json`, exit 2 | Fix the reported line/column. Selection text should use `.txt`, `.md`, or `.html`, not `.json`. |
| `schema-required`, `schema-type`, `schema-enum`, exit 2 | Compare the request with `asset-request-schema.json` and `asset-example-request.json`. CLI overrides no longer hide missing required sections. |
| `unknown-capability` / `unknown-category` | Use `capabilities --query` to find the exact ID. Close matches may be included in error details. |
| `no-capability-selected` | Supply at least one exact capability/category name or ID; descriptions alone are intentionally ignored. |
| `real-service-required` / `real-owner-required` | Replace placeholders with accountable operational identities. |
| `sensitive-key-forbidden` | Remove the secret value and use a non-sensitive reference. Also inspect free text for secrets the key check cannot detect. |
| `output-exists`, exit 4 | The old output was preserved. Review it, choose another path, or explicitly rerun with `--force`. |
| `timeout`, exit 6 | Split a very large selection or raise `--timeout-seconds` within policy. No external work was attempted. |
| `cancelled`, exit 130 | Rerun after the interruption; check the output path before authorizing replacement. |
| Assessment exit 3 | Open the JSON/Markdown artifact and inspect `failures`; this is a valid not-ready result, not a crashed command. |
| `approval-expired` / `approval-too-old` | Obtain fresh external approval and update both timestamps/reference. Do not reuse stale evidence. |
| `required-evidence-missing` | Use the exact task/verification control IDs from the current plan and attach current passing evidence. |
| `stale-evidence` / `future-evidence` | Collect fresh evidence and correct source clock/timezone problems. |
| `internal-error`, exit 5 | Preserve the stable error message, verify Python 3.10+, and run `tools-test.py`. The CLI deliberately does not dump request contents or a traceback. |

To extend the catalog, edit names/descriptions/IDs in `asset-capabilities.json`, preserve unique IDs and counts, run all tests, and update the appendix in this file. To add a provider adapter, keep it outside this offline component unless it has its own validated bounded implementation, explicit approval model, tests, and documentation; do not represent an adapter as available before testing it. When renaming any packaged file, update the loading defaults, tests, commands, and every reference in this knowledge file together.

## Validation and tests actually completed

The following were executed locally with Python 3.13.16; no provider integration was tested or claimed:

1. Main `--help`, `--version`, `capabilities`, and `capability-list` commands completed successfully.
2. Catalog integrity checks confirmed 18 categories, 249 unique capabilities, valid unique IDs, accurate counts, and non-empty exact descriptions.
3. Every one of the 249 capabilities was individually validated and planned through the shared engine with non-empty tasks and verification controls.
4. One real `tools-cloudops.py plan` request selected all 249 IDs and produced all 18 categories, more than 1,000 work items, unique controls, and zero provider/shell execution.
5. Plain text, Markdown, copied HTML, and selection JSON were resolved through the real `select` interface. Category expansion, markup/data-attribute removal, description ignoring, deduplication, and unknown-name warnings were checked.
6. Plain-text input was planned through the real CLI with service, owner, provider, and environment overrides.
7. Valid, missing-section, unknown-capability, missing-file, malformed-JSON, and sensitive-key inputs were checked with structured output and expected exit codes.
8. JSON and Markdown plans were opened/parsed/read. Changing the service changed the plan ID and content.
9. Preview `run` was tested with failed evidence and then corrected passing evidence; the recovery transitioned from exit 3/not-ready to exit 0/preview-assessment-passed.
10. Production execute handoff was tested with missing approval/change/rollback/monitoring gates, then with complete synthetic current approval and evidence to reach ready-for-external-handoff. Expired approval was then rejected. A separate critical data-deletion request was checked for the destructive backup gate. No external execution occurred in any case.
11. Stale, future-dated, duplicate, and unknown evidence controls were rejected; the `evaluate` alias was exercised.
12. Existing output was preserved without `--force`; explicit `--force` replaced it after input changed; no temporary files remained.
13. Timeout, cancellation, and pre-commit atomic-write cleanup paths were exercised directly.
14. Markdown assessment, schema/policy parsing, invalid catalog counts, empty selection errors, unsafe expired-approval policy, and unsupported custom-schema keywords were checked.
15. `tools-test.py` completed all 14 test methods successfully after the corrections described in its regression coverage.
16. An independent re-extraction of the attached PDF matched all 249 packaged name/description pairs exactly. All JSON assets parsed, all documented component references resolved with exact case, the appendix contained all 249 IDs, and no machine-specific absolute or parent-traversal reference was found.

## Package compatibility validation

- The component contains exactly **8 files**: three `tools-` files, four useful `asset-` files, and one `knowledge-readme.md`. It has no internal subfolders, no `SKILL.md`, no caches, no temporary files, and no unclassified files.
- A clean temporary skill root was created with one test `SKILL.md` beside `cloud-production-operations/`. Main help, capability listing, and the complete preview `run` succeeded using only relative paths. The combined test root had nine files including the single test `SKILL.md`; the fixture and generated outputs were removed and were not packaged.
- Every component path is two levels deep from the future skill root (`cloud-production-operations/<filename>`), within the three-level limit. A future package with one root `SKILL.md` has nine files, well below 500.
- A preflight ZIP was opened and inspected: eight entries, the component folder directly at the archive root, no outer wrapper, no nested archive, and no packaged `SKILL.md`. Extraction produced the completed component immediately.
- Extracted files matched the originals byte-for-byte. The extracted primary tool completed a relative-path preview run, and all 14 regression tests passed again from the extracted files.
- The final delivery container is named `cloud-production-operations-skill-tool.zip`. The same structure, extraction, reference, and relative-run checks are repeated on that final archive after this documentation is finalized. No test `SKILL.md` or archive is included inside it.
- No hosted resource is required by the component. A failure in a future hosted executor, application catalog, permission system, or provider platform would be external to these local package checks.

## Complete catalog capability reference

For any row, use the stable ID as the example selector:

```bash
python3 cloud-production-operations/tools-cloudops.py capabilities --capability <stable-id>
```

Or place it in the request as `"selection": {"capabilities": ["<stable-id>"]}`. The description is the exact catalog-defined purpose used to derive the work items. Risk labels are the local heuristic described above, not source-catalog claims.

### 1. Cloud Strategy, Accounts, and Organization (12 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category cloud-strategy-accounts-and-organization`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `cloud-account-provisioning` | Cloud Account Provisioning | Create and validate cloud accounts, subscriptions, projects, tenants, billing ownership, support plans, and break-glass access across AWS, Microsoft Azure, Google Cloud, Oracle Cloud, or another approved provider. | critical |
| `multi-account-cloud-organization-design` | Multi-Account Cloud Organization Design | Design landing-zone hierarchies, management groups, folders, organizational units, account vending, guardrails, and workload separation for development, staging, production, security, and shared services. | low |
| `cloud-landing-zone-bootstrap` | Cloud Landing Zone Bootstrap | Deploy a secure landing zone with centralized identity, logging, policy, networking, security services, and account baselines using Azure Landing Zones, AWS Control Tower, or Google Cloud enterprise foundations. | medium |
| `cloud-provider-selection` | Cloud Provider Selection | Compare providers against region availability, service maturity, regulatory needs, latency, operational skills, portability, support, and total cost of ownership. | medium |
| `hybrid-cloud-architecture` | Hybrid Cloud Architecture | Connect on-premises systems and cloud environments through private links, VPNs, dedicated circuits, identity federation, routing, DNS, observability, and shared governance. | medium |
| `multi-cloud-architecture` | Multi-Cloud Architecture | Define which workloads belong on each provider, prevent accidental complexity, standardize identity and telemetry, and establish exit, portability, and failure-isolation strategies. | medium |
| `region-and-availability-zone-selection` | Region and Availability-Zone Selection | Select regions and zones using latency, residency, service availability, cost, carbon, disaster-risk, and recovery requirements. | medium |
| `cloud-service-quota-management` | Cloud Service Quota Management | Inventory quotas and limits, forecast demand, request increases, and alert before deployments fail from exhausted capacity. | medium |
| `cloud-support-plan-management` | Cloud Support Plan Management | Choose and operate support plans, severity paths, escalation contacts, technical account management, and incident case procedures. | medium |
| `cloud-resource-naming-and-tagging` | Cloud Resource Naming and Tagging | Enforce consistent names, ownership, environment, cost center, data classification, lifecycle, and compliance tags. | medium |
| `cloud-resource-inventory` | Cloud Resource Inventory | Continuously discover, classify, and reconcile resources, owners, configurations, dependencies, and unsupported assets. | low |
| `cloud-architecture-decision-records` | Cloud Architecture Decision Records | Create concise ADRs recording context, options, decision, trade-offs, security impact, cost impact, and rollback conditions. | medium |

### 2. Billing, FinOps, and Capacity Economics (12 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category billing-finops-and-capacity-economics`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `billing-configuration` | Billing Configuration | Configure payment profiles, invoices, tax settings, billing accounts, cost centers, budgets, permissions, and finance contacts. | medium |
| `cloud-budget-guardrails` | Cloud Budget Guardrails | Create monthly and annual budgets with forecast alerts, anomaly thresholds, approval gates, and escalation routes. | medium |
| `cost-allocation-and-chargeback` | Cost Allocation and Chargeback | Map spend to teams, products, tenants, environments, and customers using tags, labels, accounts, and allocation rules. | medium |
| `cloud-cost-anomaly-detection` | Cloud Cost Anomaly Detection | Detect unusual spend by service, account, region, SKU, or tag and route actionable alerts with likely causes. | medium |
| `rightsizing-advisor` | Rightsizing Advisor | Evaluate CPU, memory, storage, network, and request patterns to recommend safer instance, database, and container sizes. | medium |
| `reserved-capacity-and-savings-plans` | Reserved Capacity and Savings Plans | Model commitment options, utilization risk, break-even points, coverage targets, and purchase timing. | medium |
| `autoscaling-cost-optimization` | Autoscaling Cost Optimization | Tune scaling signals, cooldowns, minimums, maximums, spot capacity, and schedules without weakening reliability. | medium |
| `storage-lifecycle-cost-optimization` | Storage Lifecycle Cost Optimization | Move data among hot, cool, archive, and deletion tiers based on access patterns, retention, restore time, and retrieval charges. | high |
| `data-transfer-cost-control` | Data Transfer Cost Control | Map egress paths, cross-zone traffic, NAT usage, CDN opportunities, replication costs, and private connectivity charges. | medium |
| `unit-economics-dashboard` | Unit Economics Dashboard | Calculate cost per request, active user, tenant, transaction, deployment, model inference, or business outcome. | medium |
| `capacity-forecasting` | Capacity Forecasting | Forecast compute, database, network, queue, and storage demand using growth, seasonality, launches, and failure headroom. | medium |
| `finops-policy-enforcement` | FinOps Policy Enforcement | Evaluate resources against budgets, approved SKUs, idle-resource rules, ownership requirements, and disposal schedules. | medium |

### 3. Identity, Access, and Privileged Operations (13 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category identity-access-and-privileged-operations`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `identity-and-access-management` | Identity and Access Management | Design users, groups, roles, service principals, workload identities, federation, conditional access, and least-privilege policies. | high |
| `single-sign-on-integration` | Single Sign-On Integration | Integrate cloud consoles and production tools with Microsoft Entra ID, Okta, Google Workspace, SAML, or OIDC. | high |
| `role-based-access-control-design` | Role-Based Access Control Design | Create role models aligned to job duties, environments, data classes, approvals, and separation of duties. | high |
| `policy-as-code-for-access` | Policy-as-Code for Access | Define and test access policies using validated templates, version control, review, simulation, and automated deployment. | high |
| `privileged-access-management` | Privileged Access Management | Implement just-in-time elevation, approval, session recording, time limits, credential isolation, and emergency access. | high |
| `break-glass-access-management` | Break-Glass Access Management | Create protected emergency identities, offline recovery instructions, monitoring, periodic tests, and post-use review. | critical |
| `workload-identity-federation` | Workload Identity Federation | Replace static cloud keys with short-lived identities for CI/CD, Kubernetes, GitHub Actions, and external workloads. | high |
| `service-account-lifecycle` | Service Account Lifecycle | Create, scope, rotate, monitor, and retire machine identities while preventing shared credentials and orphaned access. | high |
| `access-review-automation` | Access Review Automation | Run periodic owner attestations, detect stale privileges, remove departed-user access, and preserve audit evidence. | high |
| `authentication-hardening` | Authentication Hardening | Require phishing-resistant MFA, device or location conditions, secure recovery, session limits, and risk-based controls. | high |
| `authorization-testing` | Authorization Testing | Test allow and deny paths, tenant isolation, privilege escalation, confused-deputy risks, and policy regressions. | high |
| `deployment-credentials-management` | Deployment Credentials Management | Issue short-lived deployment credentials, bind them to environments, restrict actions, rotate safely, and audit every use. | high |
| `code-signing-credentials-management` | Code-Signing Credentials Management | Protect signing keys through HSM or managed key services, approval workflows, timestamping, rotation, and revocation. | critical |

### 4. Domains, DNS, Certificates, and Edge Routing (12 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category domains-dns-certificates-and-edge-routing`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `domain-name-registration` | Domain Name Registration | Select, register, renew, lock, and document domains with ownership, recovery contacts, and transfer protections. | high |
| `dns-management` | DNS Management | Create authoritative zones and records, manage TTLs, validate changes, protect DNSSEC, and maintain rollback plans. | high |
| `dns-migration` | DNS Migration | Migrate zones between providers with inventory, low-TTL preparation, parallel validation, cutover, and rollback. | high |
| `split-horizon-dns` | Split-Horizon DNS | Design internal and external DNS views without leaking private records or creating inconsistent resolution. | high |
| `dns-failover-routing` | DNS Failover Routing | Configure health-based, weighted, latency, geolocation, and disaster-recovery routing with tested failure behavior. | high |
| `tls-certificate-lifecycle` | TLS Certificate Lifecycle | Request, validate, deploy, renew, inventory, rotate, and revoke TLS certificates using ACME or managed certificate services. | high |
| `private-certificate-authority` | Private Certificate Authority | Operate internal PKI, trust chains, issuance profiles, revocation, renewal, key custody, and certificate policy. | high |
| `mutual-tls-deployment` | Mutual TLS Deployment | Implement client and service certificates, trust distribution, certificate rotation, and failed-authentication diagnostics. | high |
| `content-delivery-network` | Content Delivery Network | Configure cache policies, origins, compression, purge, signed access, geo-controls, TLS, and observability for a CDN. | high |
| `global-traffic-management` | Global Traffic Management | Route users across regions using health, latency, sovereignty, capacity, and controlled failover policies. | high |
| `reverse-proxy-configuration` | Reverse Proxy Configuration | Configure NGINX, Envoy, HAProxy, Apache, or managed proxies for routing, headers, timeouts, buffering, and TLS. | high |
| `edge-redirect-and-header-policy` | Edge Redirect and Header Policy | Enforce canonical hosts, HTTPS, HSTS, CSP, cache headers, security headers, and safe redirect behavior. | high |

### 5. Network Architecture and Connectivity (13 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category network-architecture-and-connectivity`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `virtual-network-design` | Virtual Network Design | Design CIDR plans, subnets, route tables, network segmentation, private endpoints, gateways, and growth headroom. | high |
| `ip-address-management` | IP Address Management | Allocate, document, detect conflicts, reserve, reclaim, and report private and public address space. | high |
| `security-group-and-firewall-rules` | Security Group and Firewall Rules | Implement least-privilege ingress and egress rules, ownership, expiration, review, and automated drift checks. | high |
| `private-service-connectivity` | Private Service Connectivity | Connect workloads to managed services without public exposure using private endpoints and service networking. | high |
| `site-to-site-vpn` | Site-to-Site VPN | Deploy redundant tunnels, routing, encryption, monitoring, failover, and documented troubleshooting. | high |
| `dedicated-cloud-interconnect` | Dedicated Cloud Interconnect | Plan and operate ExpressRoute, Direct Connect, Cloud Interconnect, or equivalent circuits with resilient routing. | high |
| `transit-network-architecture` | Transit Network Architecture | Centralize routing, inspection, shared services, and cross-account connectivity while limiting blast radius. | high |
| `network-address-translation` | Network Address Translation | Size and monitor NAT gateways or instances, control egress, avoid port exhaustion, and reduce excess cost. | high |
| `egress-control` | Egress Control | Restrict outbound destinations through proxies, firewalls, DNS policy, allowlists, inspection, and exception workflows. | high |
| `load-balancer` | Load Balancer | Configure Layer 4 or Layer 7 load balancing, listeners, certificates, health probes, algorithms, draining, and access logs. | high |
| `service-mesh-networking` | Service Mesh Networking | Operate service discovery, mTLS, retries, timeouts, circuit breaking, telemetry, and traffic policy with Istio, Linkerd, or equivalent. | high |
| `network-performance-testing` | Network Performance Testing | Measure latency, throughput, loss, DNS time, connection reuse, TLS overhead, and cross-zone behavior. | high |
| `ipv6-enablement` | IPv6 Enablement | Plan dual-stack addressing, routing, security, DNS, telemetry, and application compatibility. | high |

### 6. Compute, Hosting, Containers, and Orchestration (15 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category compute-hosting-containers-and-orchestration`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `production-server-or-hosting` | Production Server or Hosting | Provision and harden production virtual machines, platform hosting, serverless runtimes, or managed application services. | high |
| `linux-server-hardening` | Linux Server Hardening | Apply secure baselines, patching, SSH controls, host firewall, auditing, time sync, file permissions, and minimal packages. | high |
| `windows-server-hardening` | Windows Server Hardening | Apply Microsoft security baselines, patching, Defender controls, PowerShell logging, RDP restrictions, and service hardening. | high |
| `golden-image-pipeline` | Golden Image Pipeline | Build, scan, version, test, sign, and retire machine images using Packer or provider image builders. | high |
| `container-image-build` | Container Image Build | Create reproducible, minimal, multi-stage images with pinned dependencies, non-root users, metadata, and health checks. | high |
| `container-registry` | Container Registry | Operate ECR, ACR, Artifact Registry, GHCR, or another registry with access controls, retention, replication, and scanning. | high |
| `container-image-signing` | Container Image Signing | Sign and verify images and attestations using Cosign, Notary, or a managed signing service. | high |
| `kubernetes-cluster-provisioning` | Kubernetes Cluster Provisioning | Create secure managed or self-managed clusters with node pools, networking, identity, upgrades, and audit logging. | high |
| `kubernetes-workload-deployment` | Kubernetes Workload Deployment | Deploy validated manifests, Helm charts, or operators with requests, limits, probes, policies, and rollout safety. | high |
| `kubernetes-admission-control` | Kubernetes Admission Control | Enforce image, privilege, network, resource, label, and provenance policies using Gatekeeper, Kyverno, or native controls. | high |
| `kubernetes-upgrade-management` | Kubernetes Upgrade Management | Assess deprecations, test add-ons, drain nodes, sequence control-plane and worker upgrades, and validate rollback. | high |
| `serverless-production-deployment` | Serverless Production Deployment | Configure functions or containers with concurrency, cold-start controls, permissions, networking, retries, and observability. | high |
| `platform-as-a-service-deployment` | Platform-as-a-Service Deployment | Operate Azure App Service, Google Cloud Run, AWS App Runner, Render, Fly.io, or equivalent with production safeguards. | high |
| `gpu-workload-hosting` | GPU Workload Hosting | Provision drivers, orchestration, scheduling, model storage, observability, quotas, and cost controls for GPU workloads. | high |
| `job-and-batch-compute` | Job and Batch Compute | Run scheduled or large-scale jobs with queues, idempotency, retries, checkpoints, priorities, and cost-aware capacity. | high |

### 7. CI/CD, Release Engineering, and Supply Chain (16 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category ci-cd-release-engineering-and-supply-chain`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `ci-cd-service` | CI/CD Service | Configure GitHub Actions, Azure Pipelines, GitLab CI/CD, Jenkins, CircleCI, or another service with secure runners and approvals. | high |
| `pipeline-architecture` | Pipeline Architecture | Design reusable stages for build, lint, unit test, security scan, package, deploy, verify, promote, and rollback. | high |
| `self-hosted-runner-operations` | Self-Hosted Runner Operations | Isolate, autoscale, patch, monitor, and replace build runners while preventing secret and workspace leakage. | high |
| `artifact-repository-management` | Artifact Repository Management | Store, checksum, sign, replicate, retain, quarantine, and promote immutable build artifacts. | high |
| `dependency-locking-and-reproducible-builds` | Dependency Locking and Reproducible Builds | Pin dependencies and toolchains, verify checksums, use hermetic builds, and detect non-reproducible outputs. | high |
| `software-bill-of-materials` | Software Bill of Materials | Generate and publish CycloneDX or SPDX SBOMs, associate them with releases, and query exposure during incidents. | high |
| `slsa-provenance` | SLSA Provenance | Produce verifiable build provenance and progressively strengthen source, build, and dependency integrity. | high |
| `environment-promotion` | Environment Promotion | Promote the same tested artifact through development, staging, canary, and production with explicit controls. | high |
| `blue-green-deployment` | Blue-Green Deployment | Operate parallel production environments, validate the inactive environment, switch traffic, and preserve fast rollback. | high |
| `canary-release` | Canary Release | Shift a measured traffic percentage, compare service levels and errors, pause automatically, then expand or roll back. | high |
| `feature-flag-operations` | Feature Flag Operations | Release code separately from features, define owners, safe defaults, targeting, audit, expiry, and kill switches. | high |
| `database-migration-pipeline` | Database Migration Pipeline | Apply backward-compatible schema changes, expand-and-contract patterns, locks checks, backups, and rollback procedures. | high |
| `release-orchestration` | Release Orchestration | Coordinate application, infrastructure, database, configuration, documentation, support, and stakeholder readiness. | high |
| `release-notes-automation` | Release Notes Automation | Generate customer and operator release notes from approved changes while excluding secrets and internal-only details. | high |
| `rollback-automation` | Rollback Automation | Restore the last known good application, configuration, infrastructure, and database state with verification gates. | high |
| `deployment-freeze-management` | Deployment Freeze Management | Enforce risk-based freeze windows, exceptions, approvals, emergency changes, and resumption checks. | high |

### 8. Databases, Caches, Messaging, and Data Services (15 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category databases-caches-messaging-and-data-services`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `production-database` | Production Database | Provision a resilient managed or self-hosted database with encryption, private networking, backups, monitoring, and access controls. | high |
| `database-engine-selection` | Database Engine Selection | Choose PostgreSQL, MySQL, SQL Server, Oracle, MongoDB, DynamoDB, Cosmos DB, or alternatives based on workload and operations. | high |
| `database-high-availability` | Database High Availability | Configure replication, failover, quorum, fencing, connection behavior, and recovery verification. | high |
| `database-performance-tuning` | Database Performance Tuning | Analyze slow queries, indexes, statistics, locks, buffers, connection pools, vacuuming, and resource constraints. | high |
| `database-connection-pooling` | Database Connection Pooling | Configure PgBouncer, application pools, limits, timeouts, health checks, and leak detection. | high |
| `database-access-governance` | Database Access Governance | Separate application, migration, reporting, admin, and break-glass identities with audited privileges. | critical |
| `database-encryption-and-key-rotation` | Database Encryption and Key Rotation | Encrypt storage and connections, manage customer keys where required, and rehearse rotation. | high |
| `database-backup-and-point-in-time-recovery` | Database Backup and Point-in-Time Recovery | Configure snapshots, logs, retention, cross-region copies, restore points, and restore verification. | high |
| `read-replica-operations` | Read Replica Operations | Create and monitor replicas, lag, routing, promotion, consistency expectations, and failure behavior. | high |
| `cache-architecture` | Cache Architecture | Deploy Redis, Memcached, or managed caches with eviction, persistence, clustering, TLS, and stampede protection. | high |
| `message-queue-operations` | Message Queue Operations | Operate SQS, Service Bus, Pub/Sub, RabbitMQ, or equivalent with dead-letter handling, retries, ordering, and idempotency. | high |
| `event-streaming-platform` | Event Streaming Platform | Operate Kafka, Event Hubs, Kinesis, or Pub/Sub streams with partitions, schemas, retention, replay, and consumer lag controls. | high |
| `schema-registry-governance` | Schema Registry Governance | Version and validate event schemas, compatibility rules, ownership, evolution, and deprecation. | high |
| `search-cluster-operations` | Search Cluster Operations | Operate Elasticsearch, OpenSearch, or managed search with mappings, shards, snapshots, security, and query monitoring. | high |
| `vector-database-operations` | Vector Database Operations | Operate vector indexes with embedding-version tracking, metadata filters, recall testing, backup, privacy, and cost controls. | high |

### 9. Storage, Backup, Retention, and Recovery (15 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category storage-backup-retention-and-recovery`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `object-storage` | Object Storage | Configure buckets or containers with encryption, access policy, versioning, lifecycle, replication, logging, and public-access blocks. | high |
| `block-storage-operations` | Block Storage Operations | Select volume classes, provision IOPS and throughput, snapshot, resize, encrypt, and monitor attached storage. | high |
| `file-storage-operations` | File Storage Operations | Deploy NFS, SMB, or managed file systems with permissions, performance tiers, backup, and mount resilience. | high |
| `backup-storage` | Backup Storage | Create isolated, encrypted, immutable, and access-controlled backup repositories separate from production credentials. | high |
| `backup-policy-design` | Backup Policy Design | Define scope, frequency, retention, immutability, geographic copies, ownership, monitoring, and exceptions. | high |
| `data-retention-policy` | Data-Retention Policy | Translate legal, privacy, contractual, and operational requirements into system- specific retention and deletion rules. | high |
| `legal-hold-support` | Legal Hold Support | Suspend deletion for selected identities or records, preserve chain of custody, and audit release of holds. | critical |
| `immutable-backup-protection` | Immutable Backup Protection | Use object lock, vault lock, WORM, separate accounts, MFA deletion, and ransomware-resistant controls. | critical |
| `backup-restoration-tests` | Backup Restoration Tests | Run scheduled restores to isolated environments, verify integrity and usability, measure RTO/RPO, and record evidence. | high |
| `disaster-recovery-plan` | Disaster-Recovery Plan | Document scenarios, dependencies, roles, communication, recovery order, data loss limits, alternate access, and return to normal. | high |
| `disaster-recovery-exercise` | Disaster-Recovery Exercise | Run tabletop, partial, and full failover exercises with injects, timed checkpoints, observers, and corrective actions. | high |
| `cross-region-recovery` | Cross-Region Recovery | Replicate services and data, manage DNS or traffic failover, protect consistency, and validate regional independence. | high |
| `ransomware-recovery` | Ransomware Recovery | Detect destructive activity, isolate access, preserve evidence, recover clean data, rotate credentials, and validate trust. | critical |
| `data-deletion-verification` | Data Deletion Verification | Prove deletion from primary, replica, cache, search, analytics, export, and backup paths according to policy. | critical |
| `archive-retrieval-operations` | Archive Retrieval Operations | Catalog archives, estimate retrieval time and charges, restore safely, validate content, and reapply retention. | high |

### 10. Secrets, Keys, Configuration, and Trust (10 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category secrets-keys-configuration-and-trust`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `secrets-manager` | Secrets Manager | Store, retrieve, scope, audit, rotate, and revoke application secrets using Key Vault, Secrets Manager, Secret Manager, Vault, or equivalent. | high |
| `secret-rotation-automation` | Secret Rotation Automation | Rotate database, API, certificate, and service credentials without downtime using staged or dual credentials. | critical |
| `encryption-key-management` | Encryption Key Management | Create, authorize, rotate, disable, schedule deletion, and audit keys in KMS, Key Vault, Cloud KMS, or HSMs. | critical |
| `hardware-security-module-operations` | Hardware Security Module Operations | Provision and operate HSM partitions, ceremonies, quorum, backup, firmware, audit, and disaster recovery. | critical |
| `configuration-management` | Configuration Management | Version environment configuration, validate schema, separate secrets, audit changes, and support safe rollback. | high |
| `dynamic-configuration-delivery` | Dynamic Configuration Delivery | Distribute runtime configuration with caching, consistency, staged rollout, validation, and failure-safe defaults. | high |
| `public-key-infrastructure-inventory` | Public Key Infrastructure Inventory | Discover certificates and keys, owners, use, trust chains, algorithms, expiration, and rotation status. | high |
| `certificate-pinning-governance` | Certificate Pinning Governance | Assess pinning risk, design backup pins or alternatives, rotate safely, and prevent client lockout. | high |
| `token-signing-key-rotation` | Token Signing Key Rotation | Rotate JWT or session signing keys with overlapping verification, key IDs, revocation, and cache control. | critical |
| `time-synchronization` | Time Synchronization | Operate NTP or cloud time services, monitor drift, secure sources, and prevent authentication and logging failures. | high |

### 11. Observability, Monitoring, Logging, and Alerts (15 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category observability-monitoring-logging-and-alerts`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `monitoring-system` | Monitoring System | Implement infrastructure, platform, application, dependency, and business telemetry with ownership and actionable thresholds. | medium |
| `logging-system` | Logging System | Centralize structured logs using OpenTelemetry, Azure Monitor, Cloud Logging, CloudWatch, Elastic, Splunk, or equivalent. | medium |
| `alerting-system` | Alerting System | Create symptom-based alerts with severity, routing, deduplication, inhibition, runbooks, escalation, and maintenance windows. | medium |
| `error-tracking` | Error Tracking | Capture exceptions, stack traces, release context, users affected, breadcrumbs, ownership, and regression signals using Sentry or equivalent. | medium |
| `uptime-monitoring` | Uptime Monitoring | Run external HTTP, DNS, TLS, TCP, and API checks from multiple regions with clear availability objectives. | medium |
| `distributed-tracing` | Distributed Tracing | Instrument services with trace context, sampling, baggage controls, dependency spans, and latency analysis. | medium |
| `opentelemetry-instrumentation` | OpenTelemetry Instrumentation | Standardize traces, metrics, and logs using SDKs, collectors, semantic conventions, processors, and exporters. | medium |
| `metrics-platform-operations` | Metrics Platform Operations | Operate Prometheus, Grafana, managed metrics, or equivalent with cardinality controls, retention, recording rules, and federation. | medium |
| `log-pipeline-engineering` | Log Pipeline Engineering | Parse, enrich, redact, buffer, route, index, retain, and archive logs while controlling loss and cost. | medium |
| `sensitive-data-log-redaction` | Sensitive Data Log Redaction | Detect and remove secrets, tokens, personal data, payment data, and regulated identifiers before storage. | high |
| `service-level-objectives` | Service-Level Objectives | Define SLIs, SLOs, error budgets, windows, burn-rate alerts, exceptions, and decision rules. | medium |
| `synthetic-transaction-monitoring` | Synthetic Transaction Monitoring | Exercise critical user journeys such as sign-in, checkout, upload, API calls, and recovery from outside production. | medium |
| `real-user-monitoring` | Real User Monitoring | Measure browser and mobile performance, errors, geography, device experience, and release regressions. | medium |
| `observability-dashboard-design` | Observability Dashboard Design | Build role-specific dashboards for service health, capacity, dependencies, releases, incidents, and business impact. | low |
| `telemetry-cost-governance` | Telemetry Cost Governance | Control ingestion, labels, sampling, retention, indexes, and duplicate signals without hiding important failures. | medium |

### 12. Security Engineering and Protection (17 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category security-engineering-and-protection`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `vulnerability-scanning` | Vulnerability Scanning | Scan source, dependencies, images, hosts, cloud configuration, APIs, and running workloads; prioritize exploitable risk and verify remediation. | high |
| `web-application-firewall` | Web Application Firewall | Configure managed and custom WAF rules, exclusions, bot controls, logging, staged enforcement, and false-positive review. | high |
| `ddos-protection` | DDoS Protection | Enable network and application DDoS protections, capacity baselines, scrubbing escalation, runbooks, and attack simulations. | high |
| `rate-limiting` | Rate Limiting | Implement per-user, tenant, IP, token, route, and cost-based limits with burst handling and clear client responses. | high |
| `cloud-security-posture-management` | Cloud Security Posture Management | Continuously assess cloud configuration against CIS Benchmarks, provider baselines, custom policy, and risk ownership. | high |
| `container-runtime-security` | Container Runtime Security | Detect suspicious processes, file changes, privilege use, network behavior, and escape attempts in containers. | high |
| `endpoint-detection-and-response` | Endpoint Detection and Response | Deploy EDR to production hosts, protect agents, tune detections, isolate systems, and validate telemetry. | high |
| `intrusion-detection` | Intrusion Detection | Monitor network and host events, enrich detections, reduce noise, and connect response playbooks. | high |
| `security-information-and-event-management` | Security Information and Event Management | Normalize and correlate security events in Microsoft Sentinel, Splunk, Chronicle, or equivalent with tested detections. | high |
| `api-security-testing` | API Security Testing | Test authentication, authorization, object access, input handling, mass assignment, abuse limits, and schema conformance. | high |
| `penetration-test-coordination` | Penetration Test Coordination | Define scope and rules, prepare safe access, monitor activity, triage findings, and verify fixes. | high |
| `threat-modeling` | Threat Modeling | Model assets, trust boundaries, attackers, abuse cases, mitigations, residual risk, and security requirements. | high |
| `secure-baseline-compliance` | Secure Baseline Compliance | Assess operating systems, Kubernetes, databases, networks, and cloud services against hardened baselines. | high |
| `malware-scanning-for-uploads` | Malware Scanning for Uploads | Quarantine and scan uploads, enforce type and size controls, prevent execution, and manage detection outcomes. | high |
| `security-patch-management` | Security Patch Management | Inventory exposure, prioritize critical fixes, test compatibility, deploy by risk window, and verify closure. | high |
| `attack-surface-discovery` | Attack Surface Discovery | Find public hosts, domains, certificates, storage, APIs, shadow services, and expired assets. | high |
| `zero-trust-architecture` | Zero Trust Architecture | Apply explicit verification, least privilege, device and workload identity, segmentation, continuous evaluation, and assumed breach. | high |

### 13. Infrastructure as Code and Configuration Automation (13 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category infrastructure-as-code-and-configuration-automation`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `infrastructure-as-code-state-storage` | Infrastructure-as-Code State Storage | Store Terraform or OpenTofu state in encrypted, versioned, access-controlled backends with locking, backup, and recovery. | high |
| `terraform-module-engineering` | Terraform Module Engineering | Create versioned, composable modules with validated inputs, outputs, examples, tests, and upgrade guidance. | high |
| `opentofu-deployment` | OpenTofu Deployment | Operate OpenTofu plans and applies with remote state, provider controls, policy, review, and drift handling. | high |
| `bicep-and-arm-deployment` | Bicep and ARM Deployment | Author and validate Azure infrastructure with reusable modules, what-if review, deployment stacks, and safe parameters. | high |
| `cloudformation-engineering` | CloudFormation Engineering | Build AWS stacks and StackSets with change sets, drift detection, rollback controls, and policy validation. | high |
| `pulumi-infrastructure-automation` | Pulumi Infrastructure Automation | Create typed infrastructure programs with protected secrets, previews, testing, and stack governance. | high |
| `configuration-management-automation` | Configuration Management Automation | Use Ansible, Chef, Puppet, Salt, or cloud-native tools for convergent, idempotent, audited configuration. | high |
| `infrastructure-policy-as-code` | Infrastructure Policy as Code | Enforce organizational rules with OPA, Conftest, Sentinel, Azure Policy, SCPs, or organization policies. | high |
| `infrastructure-plan-review` | Infrastructure Plan Review | Summarize additions, changes, replacements, deletions, cost, security impact, dependencies, and rollback risk. | high |
| `infrastructure-drift-detection` | Infrastructure Drift Detection | Compare desired and actual state, classify drift, identify ownership, and remediate safely. | high |
| `ephemeral-environment-provisioning` | Ephemeral Environment Provisioning | Create short-lived preview or test environments with isolation, seeded data, budgets, TTLs, and automatic cleanup. | high |
| `resource-import-and-adoption` | Resource Import and Adoption | Bring existing resources under IaC without unintended replacement, downtime, or loss of configuration. | high |
| `iac-testing` | IaC Testing | Run formatting, linting, static analysis, policy tests, unit tests, integration tests, and deployment smoke tests. | high |

### 14. Reliability, Incidents, and Production Operations (14 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category reliability-incidents-and-production-operations`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `incident-response-coordination` | Incident Response Coordination | Declare incidents, set roles, establish communication, stabilize service, track actions, and close with verified recovery. | high |
| `on-call-program-design` | On-Call Program Design | Define rotations, escalation, access, compensation guidance, training, handoffs, health checks, and sustainable load. | high |
| `runbook-authoring` | Runbook Authoring | Create executable runbooks with symptoms, prerequisites, safeguards, commands, decision points, rollback, and verification. | high |
| `automated-incident-remediation` | Automated Incident Remediation | Automate bounded, reversible responses such as restart, failover, scale-out, queue pause, or certificate renewal. | high |
| `post-incident-review` | Post-Incident Review | Produce blameless timelines, contributing factors, detection and response gaps, corrective actions, owners, and due dates. | high |
| `problem-management` | Problem Management | Identify recurring patterns, maintain known errors, prioritize permanent fixes, and verify recurrence reduction. | high |
| `chaos-engineering` | Chaos Engineering | Run controlled failure experiments with hypotheses, blast-radius limits, abort conditions, telemetry, and learning capture. | high |
| `game-day-facilitation` | Game Day Facilitation | Exercise teams and systems against realistic outages, degraded dependencies, security events, and communication failures. | high |
| `capacity-incident-prevention` | Capacity Incident Prevention | Detect saturation, forecast exhaustion, reserve headroom, test scaling, and eliminate hard limits before demand peaks. | high |
| `dependency-resilience` | Dependency Resilience | Apply timeouts, bounded retries, jitter, circuit breakers, bulkheads, fallbacks, and graceful degradation. | high |
| `production-readiness-review` | Production Readiness Review | Evaluate architecture, security, observability, capacity, support, deployment, rollback, recovery, and ownership before launch. | high |
| `operational-maturity-assessment` | Operational Maturity Assessment | Score services across ownership, reliability, automation, security, documentation, testing, recovery, and cost. | high |
| `maintenance-window-operations` | Maintenance Window Operations | Plan notifications, backups, access, sequencing, verification, rollback, and completion reporting. | high |
| `service-decommissioning` | Service Decommissioning | Drain traffic, archive or delete data, revoke access, remove infrastructure, update dependencies, and verify cost cessation. | critical |

### 15. Testing, Validation, and Production Acceptance (15 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category testing-validation-and-production-acceptance`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `production-acceptance-tests` | Production Acceptance Tests | Run go-live checks covering critical journeys, authentication, authorization, data integrity, performance, monitoring, backups, rollback, and support readiness. | high |
| `deployment-smoke-testing` | Deployment Smoke Testing | Verify health endpoints, key APIs, dependent services, queues, databases, static assets, and telemetry immediately after deployment. | medium |
| `integration-testing-environment` | Integration Testing Environment | Provision representative dependencies, identities, data, network policies, and observability for repeatable integration tests. | medium |
| `end-to-end-test-automation` | End-to-End Test Automation | Automate critical user and operator journeys with stable selectors, seeded data, retries limited to infrastructure noise, and captured evidence. | medium |
| `load-testing` | Load Testing | Measure throughput, latency percentiles, saturation, errors, autoscaling, and recovery using realistic traffic models. | medium |
| `stress-testing` | Stress Testing | Increase load beyond expected peaks to identify breaking points, failure modes, and safe operating limits. | medium |
| `soak-testing` | Soak Testing | Run sustained workloads long enough to reveal leaks, fragmentation, queue growth, cache drift, and resource exhaustion. | medium |
| `failover-testing` | Failover Testing | Trigger component, zone, region, database, route, and dependency failures and verify recovery behavior and data consistency. | medium |
| `backup-restore-validation` | Backup Restore Validation | Restore selected and full datasets, run application checks, compare integrity, and document achieved recovery times. | medium |
| `security-regression-testing` | Security Regression Testing | Retest fixed vulnerabilities, authentication, authorization, headers, secrets exposure, and policy controls on every release. | high |
| `infrastructure-acceptance-testing` | Infrastructure Acceptance Testing | Validate resources, policies, routes, encryption, logging, backups, tags, and ownership after provisioning. | high |
| `contract-testing` | Contract Testing | Verify provider and consumer API or event contracts, compatibility, versioning, and failure responses. | medium |
| `synthetic-data-test-management` | Synthetic Data Test Management | Generate privacy-safe, representative, edge-case data and prevent production data leakage into lower environments. | high |
| `release-verification-and-observation` | Release Verification and Observation | Observe service and business metrics after release against baselines, error budgets, and automatic rollback thresholds. | medium |
| `resilience-test-suite` | Resilience Test Suite | Continuously test timeout, retry, failover, degraded dependency, quota exhaustion, and partial outage behavior. | medium |

### 16. Privacy, Compliance, Governance, and Documentation (16 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category privacy-compliance-governance-and-documentation`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `privacy-and-security-documentation` | Privacy and Security Documentation | Maintain system descriptions, data flows, controls, policies, risks, recovery procedures, evidence, and accountable owners. | high |
| `data-classification` | Data Classification | Classify public, internal, confidential, personal, regulated, and secret data and map handling controls. | high |
| `data-inventory-and-flow-mapping` | Data Inventory and Flow Mapping | Document data sources, purposes, fields, processors, transfers, storage, retention, and deletion paths. | high |
| `privacy-impact-assessment` | Privacy Impact Assessment | Assess necessity, proportionality, risks, safeguards, retention, rights handling, vendors, and residual risk. | high |
| `compliance-control-mapping` | Compliance Control Mapping | Map technical and operational controls to ISO 27001, SOC 2, NIST CSF, PCI DSS, HIPAA, GDPR, or applicable frameworks. | medium |
| `evidence-collection-automation` | Evidence Collection Automation | Collect versioned, time-bounded evidence from cloud, identity, CI/CD, tickets, scans, backups, and monitoring. | medium |
| `policy-exception-management` | Policy Exception Management | Record scope, risk, compensating controls, approver, expiry, owner, and review status for exceptions. | medium |
| `change-management` | Change Management | Classify changes, assess impact, obtain approvals, schedule work, record evidence, and conduct post-change review. | medium |
| `asset-ownership-governance` | Asset Ownership Governance | Assign accountable business and technical owners and detect orphaned services, data, credentials, and domains. | medium |
| `third-party-risk-review` | Third-Party Risk Review | Assess vendor security, privacy, resilience, data access, subprocessors, contractual controls, and exit strategy. | high |
| `data-processing-agreement-tracking` | Data Processing Agreement Tracking | Track contractual privacy obligations, approved purposes, geographies, subprocessors, breach notice, and deletion. | high |
| `audit-trail-integrity` | Audit Trail Integrity | Protect, retain, time-synchronize, restrict, and verify audit logs against alteration and unauthorized access. | medium |
| `records-of-processing-activities` | Records of Processing Activities | Maintain processing purposes, categories, recipients, transfers, retention, safeguards, and responsible parties. | medium |
| `data-subject-request-operations` | Data Subject Request Operations | Discover, export, correct, restrict, or delete personal data across systems with identity verification and audit. | high |
| `security-architecture-documentation` | Security Architecture Documentation | Maintain trust boundaries, data flows, identities, controls, dependencies, assumptions, and approved deviations. | low |
| `operational-handover-documentation` | Operational Handover Documentation | Deliver ownership, architecture, dependencies, credentials process, dashboards, alerts, runbooks, recovery, and support contacts. | low |

### 17. Application Platform and Runtime Engineering (14 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category application-platform-and-runtime-engineering`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `production-environment-configuration` | Production Environment Configuration | Create environment-specific settings with validation, safe defaults, feature controls, endpoint mapping, and rollback. | high |
| `application-health-endpoint-design` | Application Health Endpoint Design | Expose startup, liveness, readiness, dependency, and diagnostic signals without leaking sensitive data. | high |
| `graceful-shutdown` | Graceful Shutdown | Stop accepting work, drain connections, finish or checkpoint jobs, release locks, and exit within platform deadlines. | high |
| `session-management` | Session Management | Secure cookies and tokens, rotate identifiers, set expiry, revoke sessions, prevent fixation, and scale session storage. | high |
| `background-worker-operations` | Background Worker Operations | Run workers with concurrency controls, idempotency, visibility timeouts, retries, dead letters, and safe shutdown. | high |
| `scheduled-task-operations` | Scheduled Task Operations | Operate cron or managed schedulers with locking, missed-run policy, observability, retries, and time-zone clarity. | high |
| `file-upload-pipeline` | File Upload Pipeline | Validate size and type, quarantine, scan, store privately, transform safely, and issue time-limited access. | high |
| `outbound-email-production` | Outbound Email Production | Configure verified domains, SPF, DKIM, DMARC, provider credentials, bounce handling, suppression, and deliverability monitoring. | high |
| `webhook-delivery-platform` | Webhook Delivery Platform | Sign payloads, retry safely, preserve ordering expectations, deduplicate, expose delivery logs, and rotate secrets. | high |
| `api-gateway-operations` | API Gateway Operations | Configure routing, authentication, quotas, transformation, caching, versioning, logging, and staged deployment. | high |
| `api-versioning-and-deprecation` | API Versioning and Deprecation | Manage compatibility, sunset notices, usage tracking, migration guides, and controlled retirement. | high |
| `tenant-isolation` | Tenant Isolation | Enforce identity, data, cache, queue, storage, encryption, and observability boundaries between tenants. | high |
| `runtime-upgrade-management` | Runtime Upgrade Management | Upgrade language runtimes and frameworks with compatibility tests, dependency audits, canaries, and rollback. | high |
| `static-asset-delivery` | Static Asset Delivery | Fingerprint, compress, cache, invalidate, secure, and monitor JavaScript, CSS, fonts, and media assets. | high |

### 18. Business Continuity, Communications, and Service Management (12 capabilities)

Example unified category selection: `python3 cloud-production-operations/tools-cloudops.py capabilities --category business-continuity-communications-and-service-management`.

| Stable ID | Skill Tool Name | Catalog description used for planning | Risk |
|---|---|---|---|
| `business-impact-analysis` | Business Impact Analysis | Identify critical services, dependencies, maximum tolerable downtime, recovery priorities, data loss limits, and business consequences. | medium |
| `service-catalog-management` | Service Catalog Management | Maintain service owners, purpose, tier, dependencies, SLAs, SLOs, support path, data class, and lifecycle. | low |
| `status-page-operations` | Status Page Operations | Publish accurate component status, incident updates, maintenance notices, subscriptions, and resolution summaries. | medium |
| `incident-communication` | Incident Communication | Prepare audience-specific updates with impact, scope, workaround, next update time, and confirmed facts. | medium |
| `stakeholder-escalation` | Stakeholder Escalation | Route operational, security, privacy, financial, legal, and executive escalations using defined triggers and contacts. | high |
| `customer-support-readiness` | Customer Support Readiness | Provide support teams with release notes, known issues, diagnostics, workarounds, escalation, and incident linkage. | medium |
| `sla-and-ola-management` | SLA and OLA Management | Define measurable commitments, exclusions, dependencies, reporting, breach handling, and internal operating agreements. | medium |
| `vendor-outage-management` | Vendor Outage Management | Detect vendor failures, open priority cases, activate workarounds, communicate impact, and track service credits. | medium |
| `operational-training` | Operational Training | Train operators through labs, shadow rotations, simulated incidents, access checks, and competency verification. | low |
| `knowledge-base-maintenance` | Knowledge Base Maintenance | Keep troubleshooting, architecture, ownership, recovery, and common-failure guidance current and searchable. | low |
| `continuity-contact-tree` | Continuity Contact Tree | Maintain primary and alternate contacts, secure offline copies, test notifications, and update departure changes. | medium |
| `production-launch-command-center` | Production Launch Command Center | Coordinate go-live owners, checklists, telemetry, support, decisions, rollback authority, and timed status reviews. | medium |
