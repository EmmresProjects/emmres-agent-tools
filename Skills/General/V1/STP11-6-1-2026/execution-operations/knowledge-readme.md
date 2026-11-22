# Execution Operations — Skill Tool

A reusable, standard-library-only command execution and operations component for a future
Copilot skill. It turns a general capability ("execute, gate, observe, recover, verify and
document command work") into working software: 26 operations reachable from one entry point,
7 data assets, and one shared engine.

This folder is a **Skill Tool component**, not a complete skill. It contains no `SKILL.md`;
a parent package supplies that file at the skill root. Copy this folder directly beside the
parent `SKILL.md` and it runs unchanged.

- Folder name: `execution-operations`
- Primary entry point: `execution-operations/tools-nexus.py`
- Shared engine: `execution-operations/tools-nexuslib.py`
- Catalog umbrella: 20 categories, 200 catalogued capabilities (`asset-skill-map.json`)

---

## 1. Purpose and scope

The source catalog describes a broad library of agent skills for command execution, orchestration,
recovery, verification, and scalable automation. This Skill Tool implements the shared mechanical
core of that library as a single unified component:

| Concern | What the tool actually does |
| --- | --- |
| Execution control | Runs one command under a hard deadline, in a chosen shell dialect, inside a validated working directory. |
| Safety | Classifies risk from a rule asset, blocks high/critical actions until an explicit logged approval is supplied, and refuses scope escapes. |
| Observation | Produces a JSON envelope, a JSONL trace, a per-run record, and a rendered Markdown report. |
| Recovery | Bounded retries with jittered backoff, stall detection, cooperative cancellation, checkpoints, partial-result evidence. |
| Orchestration | Executes a validated DAG workflow with bounded parallelism, dependency blocking, per-node retries and artifacts. |
| Diagnosis | Maps exit codes, signals, and stderr patterns to states, layers, and concrete probes. |
| Transformation | Deterministic dataset transform/validate over JSON, JSONL, CSV, TSV; bounded SQLite queries. |
| Documentation | Skill-map routing so every catalogued capability is reachable and traceable to an operation. |

**What it is not:** a shell replacement, a CI server, a cloud/SSH control plane, a secrets vault,
or a general remote-execution fabric. See §10 for the exact limitations.

---

## 2. Requirements and setup

| Requirement | Detail |
| --- | --- |
| Python | 3.8 or newer. Verified on CPython 3.13.16. No third-party packages. |
| OS | Any POSIX host or Windows. Verified end-to-end on Linux x86_64. |
| Shells | `bash`/`sh` execute verified. `zsh`, `powershell`, `cmd` are supported by the tool but were **not** available on the verification host (see §10). |
| Optional | `flock` (only relevant to the generated cron line), `systemd`, `schtasks` — none are required by the tool itself. |

There is nothing to install and no `pip` step. Place the folder where the skill can reach it and run:

```bash
python3 execution-operations/tools-nexus.py --help
python3 execution-operations/tools-nexus.py capabilities
```

Working directory rules: `paths.work_root` defaults to `.` (the directory you run from). All
state, traces, run records, and reports default under `.nexus/` inside that work root. Use
`--work-root DIR` on any invocation to point somewhere else.

---

## 3. Files in this component

| File | Prefix | Role | Required? |
| --- | --- | --- | --- |
| `execution-operations/tools-nexus.py` | tools- | **Primary runnable entry point.** CLI, argument validation, operation dispatch, envelope and error shaping. | Yes |
| `execution-operations/tools-nexuslib.py` | tools- | Shared engine: config, scope, risk, redaction, execution, DAG orchestration, transforms, SQLite, HTTP, reports. Loaded by path from the entry point (see below). | Yes |
| `execution-operations/asset-default-config.json` | asset- | All tunable defaults: paths, scope, execution limits, retry, orchestration, approval, network, data, database, and asset references. | Yes |
| `execution-operations/asset-policy-rules.json` | asset- | Risk rules, secret patterns, scope rules, and approval policy consumed by `risk`, `exec`, `run-plan`, `orchestrate`, `http`, `db-query`. | Yes |
| `execution-operations/asset-exit-codes.json` | asset- | Exit-code, signal, and stderr-pattern interpretation table. | Yes |
| `execution-operations/asset-shell-dialects.json` | asset- | 53 single-pass translation rules across bash, sh, zsh, cmd, PowerShell. | Used by `translate`, `exec`, `doctor` |
| `execution-operations/asset-skill-map.json` | asset- | The catalog umbrella: 20 categories, 200 capabilities, each with domains, risk, and the operations that implement it. | Used by `capabilities`, `route`, `plan`, `doctor` |
| `execution-operations/asset-workflow-schema.json` | asset- | Published JSON Schema for workflow files executed by `orchestrate`. | Used by `validate-workflow`, `orchestrate` |
| `execution-operations/asset-report-template.md` | asset- | Markdown report template with `{{placeholders}}` filled by `report`. | Used by `report` |
| `execution-operations/knowledge-readme.md` | knowledge- | This document: the only documentation file. | Yes |

Loading order: the entry point imports the engine, the engine resolves every asset through
`asset-default-config.json`, and each operation loads only the assets it needs. No asset is
optional at runtime except the ones noted above.

`tools-nexuslib.py` is not a valid Python module name, so `tools-nexus.py` loads it by path with
`importlib` and registers it under the module name `nexuslib`; references to `nx.*` in the entry point resolve to that object. If the file is missing, the entry point exits 5 with an explicit
message instead of failing obscurely.

---

## 4. Output contract

Every command prints one JSON envelope on stdout (JSON is the default; add `--text` for a compact
human summary):

```json
{
  "ok": true,
  "operation": "exec",
  "tool": "execution-operations",
  "version": "1.0.0",
  "correlation_id": "nex-20261106T024042Z-c69d",
  "data": { "...": "operation-specific payload" },
  "error": null,
  "warnings": [],
  "artifacts": ["/abs/path/to/run-record.json"],
  "steps": [{"step": "risk", "level": "low", "flags": []}],
  "next_actions": ["Verify declared artifacts, then record completion evidence."]
}
```

On failure the same shape is returned with `ok: false` and
`error: {code, message, details}`. Blocked or partially completed operations still return the
`data`, `steps`, and `artifacts` gathered before the failure.

Process exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Operation completed and its own verification passed. |
| 2 | Invalid input, invalid config, missing file, or bad usage. |
| 3 | Blocked by policy: approval required, scope denied, network blocked, DB write blocked. |
| 4 | The work ran but failed (nonzero exit, missing artifact, failed validation, workflow failure). |
| 5 | Internal error (a traceback tail is included in the envelope). |
| 130 | Cancelled by signal. |

Global options, accepted before or after the operation name:
`--config FILE`, `--work-root DIR`, `--state-dir DIR`, `--json`, `--text`, `--version`, `--help`.

---

## 5. Operation reference

26 operations. Every example below was executed against the real interface; see §9.

### 5.1 `capabilities` — operation list and catalog listing
```bash
python3 execution-operations/tools-nexus.py capabilities
python3 execution-operations/tools-nexus.py capabilities --category "Failure Recovery"
python3 execution-operations/tools-nexus.py capabilities --skill "Canary Controller"
python3 execution-operations/tools-nexus.py capabilities --domain security --out caps.json
```
Returns all 26 operations, total catalog counts, and per-category capabilities with the operations
that implement each. Exact capability names short-circuit fuzzy matching. Unknown filters return
`category_not_found` / `skill_not_found` / `domain_not_found`.

### 5.2 `route` — objective → catalogued capabilities
```bash
python3 execution-operations/tools-nexus.py route --query "parallel build and test with rollback"
python3 execution-operations/tools-nexus.py route --category "Testing, Verification, and Quality Gates"
```
Scores capabilities by name, description, domain, and category, then returns the matching
capabilities plus `recommended_operations`. Unmatched input returns `no_route` with a suggestion.

### 5.3 `plan` — executable plan with risk pre-checks
```bash
python3 execution-operations/tools-nexus.py plan \
  --objective "build then verify" \
  --commands "mkdir -p build" "echo data > build/a.txt" "rm -rf build" \
  --out plan.json
```
Each step carries `risk.level`, `risk.flags`, and `requires_approval`. A plan with no `--commands`
still returns the capability mapping but is marked `executable: false` and warns.

### 5.4 `run-plan` — sequential execution of a plan
```bash
python3 execution-operations/tools-nexus.py run-plan --file plan.json --dry-run
python3 execution-operations/tools-nexus.py run-plan --file plan.json --approve --approval-ref "ops: disposable dir"
```
Stops at the first failure or blocked step, records every step result, verifies per-step
`artifacts` when present, and appends each step to the trace.

### 5.5 `exec` — one bounded command
```bash
# basic
python3 execution-operations/tools-nexus.py exec --command "python3 -V"

# script file, extra environment, captured streams
python3 execution-operations/tools-nexus.py exec --file job.sh --shell bash --env GREETING=hello --capture-dir logs

# deadline, heartbeat-gated stall detection, retries, artifacts, report
python3 execution-operations/tools-nexus.py exec \
  --command "make -j4 test" --timeout 900 --heartbeat --stall-timeout 240 \
  --retries 2 --artifact build/report.xml --report report.md --title "Nightly tests"

# resumable: skip when the checkpoint already records this exact command
python3 execution-operations/tools-nexus.py exec --command "python3 etl.py" --checkpoint etl.json --resume
```
Key flags: `--shell`, `--cwd`, `--timeout`, `--retries`, `--heartbeat`, `--heartbeat-pattern`,
`--stall-timeout`, `--checkpoint`, `--resume`, `--artifact` (repeatable), `--env` (repeatable),
`--capture-dir`, `--report`, `--title`, `--no-trace`, `--dry-run`, `--approve`, `--approval-ref`,
`--allow-outside`.

Exit-code interpretation falls back predictably: a code in `asset-exit-codes.json` wins, then a
signal mapping, then a stderr pattern; any other non-zero code becomes state `failure`, category
`general`, with an explicit "read the first error line" action.

Behaviour worth knowing: retries happen only when the interpreted state is retryable and the
attempt budget allows; a timed-out, stalled, or cancelled run is never retried. Heartbeat
enforcement is opt-in (`--heartbeat`) so quiet-but-healthy jobs are not killed by default; without
it the tool still reports `idle_seconds`.

### 5.6 `orchestrate` — workflow DAG
```bash
python3 execution-operations/tools-nexus.py validate-workflow --workflow workflow.json
python3 execution-operations/tools-nexus.py orchestrate --workflow workflow.json --max-parallel 2 --report wf.md
python3 execution-operations/tools-nexus.py orchestrate --workflow workflow.json --dry-run
```
Validates membership, unknown properties, exactly-one-of `command|argv|op|noop`, dependency
references, self-dependency, and cycles before running. Ready nodes run concurrently up to
`max_parallel`; a failed node blocks its dependents (reason recorded); per-node `retries`,
`timeout_seconds`, `artifacts`, `requires_approval`, and `dialect` are honoured. A checkpoint file
(default `.nexus/runs/orchestrator.json`, name from `meta.checkpoint`) lets a rerun skip completed
nodes. Status is `success`, `failed`, `blocked`, `cancelled`, or `partial`.

Workflow node kinds: `command` (shell text, dialect via `dialect`), `argv` (direct exec, no shell),
`op` (built-in: `validate`, `trace`), `noop` (structural).

### 5.7 `risk` — risk classification
```bash
python3 execution-operations/tools-nexus.py risk "rm -rf /"
python3 execution-operations/tools-nexus.py risk --file deploy.sh --level high
```
Returns `risk_level`, matched rules with `reason` and `safer` alternatives, `approval_required`, and
any secret-like values found (as redacted findings).

### 5.8 `scope-check` — path scope
```bash
python3 execution-operations/tools-nexus.py scope-check orders.csv ./new-dir --allow-create
python3 execution-operations/tools-nexus.py scope-check /etc/passwd     # exits 3
```

### 5.9 `contract` — execution contract validation
```bash
python3 execution-operations/tools-nexus.py contract --file contract.json
python3 execution-operations/tools-nexus.py contract --contract-json '{"command":"ls",...}'
```
Requires `command`, `expected_inputs`, `permitted_side_effects`, `success_signals`,
`failure_signals`, `output_paths`, `cleanup`; rejects empty `success_signals`/`failure_signals`/
`output_paths` and success signals that name no observable evidence. An empty `expected_inputs` or
`permitted_side_effects` list is accepted and means "none" — an honest read-only contract. Also returns the risk assessment of the contract's command.

### 5.10 `approve` — audit-only approval record
```bash
python3 execution-operations/tools-nexus.py approve \
  --command "terraform destroy -auto-approve" \
  --approval-ref "ops-lead: teardown of sandbox stack"
```
Writes an append-only JSONL record to `.nexus/state/audit.jsonl` (path configurable). It never
executes anything. The resulting entry is evidence, **not** a capability token: execution-time
approval still requires `--approve` with `--approval-ref`.

### 5.11 `estimate` — duration and stage budget
```bash
python3 execution-operations/tools-nexus.py estimate --units 400 --unit-seconds 0.8 --concurrency 4 --warmup 5 --latency 0.2
```
Deterministic arithmetic across setup, execution, verification, repair/retry, and export/cleanup,
plus `recommended_timeout_seconds` (1.5×) and a confidence label. It is an estimate, not a guarantee.

### 5.12 `parse-log` — log signal extraction
```bash
python3 execution-operations/tools-nexus.py parse-log --file app.log --dedupe --out summary.json
```
Handles JSONL and plain text, extracts level/timestamp/message/correlation/error ids, counts levels,
returns `first_error`, bounded `error_excerpts`, and (for small logs) all records; repeated
level+message pairs can be collapsed with `--dedupe` while the original file is never modified.

### 5.13 `trace` — reproducible execution trace
```bash
python3 execution-operations/tools-nexus.py trace --show --limit 5
python3 execution-operations/tools-nexus.py trace --command "manual probe" --exit-code 0 --note "checked disk"
```
Trace entries (`.nexus/runs/trace.jsonl`) record command, shell, working directory, duration, exit
code, state, artifacts, and correlation id.

### 5.14 `diagnose` — layered failure triage
```bash
python3 execution-operations/tools-nexus.py diagnose --exit-code 127 --stderr-text "bash: nope: command not found"
python3 execution-operations/tools-nexus.py diagnose --stderr err.log
python3 execution-operations/tools-nexus.py diagnose --signal 9
```
Returns interpretation (state, category, retryable, meaning, actions), suspected layers out of the
11-layer triage model, and 2–6 concrete probes to run next.

### 5.15 `secrets-scan` — credential discovery
```bash
python3 execution-operations/tools-nexus.py secrets-scan src/ config/ --out findings.json
```
Scans files (or a directory tree, `.git` excluded) for 9 credential pattern classes. Findings report
pattern id, line, value length, and a truncated SHA-256 fingerprint — never the value itself.

### 5.16 `translate` — shell dialect translation
```bash
python3 execution-operations/tools-nexus.py translate --command 'export FOO=bar; echo $FOO; cat /tmp/x.txt' --from bash --to powershell
python3 execution-operations/tools-nexus.py translate --command '$env:FOO = "bar"; Get-Content .\notes.txt' --from powershell --to bash
```
53 rules across bash/sh/zsh/cmd/PowerShell, applied in a single left-to-right pass so a rule never
re-processes another rule's output. Rules warn when approximate or explicitly unsupported
(for example `chmod` on cmd emits a `REM unsupported` comment and a warning). Translation is textual:
it does not execute or verify the target.

### 5.17 `schedule` — scheduler definitions as text
```bash
python3 execution-operations/tools-nexus.py schedule \
  --command "/usr/local/bin/nightly-report" --every 15 --unit minutes \
  --timezone Asia/Manila --overlap skip --missed run_once --retries 2 --name nightly-report --out schedule.md
python3 execution-operations/tools-nexus.py schedule --spec sched.json --format cron
```
Validates cron tokens against field ranges and rejects unknown interval, overlap, and missed-run
policies. Emits cron (with a `flock` wrapper for skip/queue overlap), a systemd service+timer pair,
a GitHub Actions workflow, and a Windows `schtasks` command. **Nothing is installed or registered.**

### 5.18 `data-transform` — deterministic dataset pipeline
```bash
python3 execution-operations/tools-nexus.py data-transform --input orders.csv --spec transform.json \
  --output summary.json --output-format json
```
Steps, executed in this fixed order: `filter`, `map`, `rename`, `select`, `drop_empty`, `dedupe`,
`sort`, `aggregate`, `limit`. Filter operators: `eq, ne, gt, gte, lt, lte, contains, in, not_in,
matches, exists, not_exists, is_null`. Map functions: `identity, lower, upper, trim, int, float,
bool, len, concat, substr, split`. Aggregate functions: `sum, avg, min, max, count_distinct`.
Unknown steps, operators, or functions are rejected with the supported list. Numeric-looking strings
sort and aggregate numerically. Output is re-parsed for JSON/JSONL before success is reported.

### 5.19 `data-validate` — dataset contract checks
```bash
python3 execution-operations/tools-nexus.py data-validate --input orders.csv --spec validate.json
```
Checks `required`, `types`, `unique`, `ranges`, `patterns`, `min_rows`, `max_rows`. For CSV/TSV input
every cell is text, so numeric/boolean type checks mean "must parse" — this is reported as
`delimited_input: true`. Exit code 4 when checks fail; the full issue list is in the envelope.

### 5.20 `db-query` — bounded SQLite access
```bash
python3 execution-operations/tools-nexus.py db-query --db demo.db --sql "select name, qty from items where qty > :min" --param min=3
python3 execution-operations/tools-nexus.py db-query --db demo.db --sql "update items set qty=qty+1 where id=1" --allow-write --approve --approval-ref "fixture: intentional"
```
Read-only by default (SQLite `mode=ro` URI). Mutating statements require `--allow-write`; destructive
statements additionally require `--approve`. Multiple statements are refused. Named parameters are
bound, never interpolated. Results are capped by `database.max_rows`. Optional
`database.allowed_roots` confines which database files may be opened.

### 5.21 `http` — one bounded HTTP request
```bash
python3 execution-operations/tools-nexus.py --config net.json http --url https://api.internal/health
python3 execution-operations/tools-nexus.py --config net.json http --url https://api.internal/items --method POST --body '{"a":1}' --approve --approval-ref "ops: create item"
```
Network is denied unless `network.allow_network` is true **and** the host is in
`network.allowed_hosts` (or `--allow-host`). `GET`/`HEAD` are read-only; every mutating method
requires approval. Credential headers (`Authorization`, `Cookie`, `X-Api-Key`) are refused outright.
Timeouts, retry count, and response size are bounded; responses are redacted and optionally saved
with `--out`. Mutating requests are **not** retried after a server response, so a retry cannot
duplicate the side effect.

### 5.22 `verify` — artifact verification
```bash
python3 execution-operations/tools-nexus.py verify dist/ report.json --require-json --min-bytes 1
```
Checks existence, size, JSON parseability, and records SHA-256 for each artifact; directories are
counted. Exit code 4 on any failure. Structural verification is not a semantic review.

### 5.23 `report` — Markdown report rendering
```bash
python3 execution-operations/tools-nexus.py report --from-trace --output trace-report.md --title "Last run"
python3 execution-operations/tools-nexus.py report --run .nexus/runs/run-nex-....json --output run.md
```
Renders `asset-report-template.md` with run data: result table, risk/approval state, artifact table
with hashes, diagnosis row, verification table, output excerpt, and next actions.

### 5.24 `doctor` — environment self-check
```bash
python3 execution-operations/tools-nexus.py doctor
```
Checks Python version, each shell's availability, SQLite version, work-root existence,
state-dir writability, every referenced asset (existence and JSON validity), that every operation
named in the skill map exists, catalog counts, and the network posture. `fail` blocks (exit 2);
`warn` is informational.

### 5.25 `checkpoint` — resumable state
```bash
python3 execution-operations/tools-nexus.py checkpoint --file etl.json --set phase=load --complete shard-1
python3 execution-operations/tools-nexus.py checkpoint --file etl.json --show
python3 execution-operations/tools-nexus.py checkpoint --file etl.json --reset
```
`--reset` deletes a checkpoint file — the only destructive action in this operation, and it is
scoped to the named file only. `exec --checkpoint` writes the same file format automatically.

### 5.26 `validate-workflow`
See §5.6. Validates structure without executing; returns `errors`, `warnings`, `node_count`.

---

## 6. Catalog coverage (20 categories, 200 capabilities)

`asset-skill-map.json` is the umbrella: every one of the 200 catalogued capabilities is listed with
its category, description, domains, typical risk, and the operations that implement it. The map is
data, not decoration — `capabilities`, `route`, `plan`, and `doctor` all read it, and `doctor`
fails if the map references an operation that does not exist.

| # | Category | Capabilities | Primary operations |
| --- | --- | --- | --- |
| 1 | Command, Shell, and Execution Control | 10 | plan, risk, run-plan, exec, contract, translate, diagnose |
| 2 | Timeouts, Scheduling, and Long-Running Work | 10 | estimate, exec (timeout/heartbeat/checkpoint), schedule, checkpoint |
| 3 | Parallelism, Multitasking, and Orchestration | 10 | orchestrate, validate-workflow, plan, run-plan |
| 4 | Safety, Permissions, and Change Control | 10 | risk, scope-check, contract, approve, exec |
| 5 | Failure Recovery and Resilience | 10 | diagnose, parse-log, trace, checkpoint, run-plan |
| 6 | Observability, Logging, and Diagnostics | 10 | parse-log, trace, report, doctor, diagnose |
| 7 | Environment, Dependency, and Runtime Management | 10 | doctor, exec, verify, translate |
| 8 | Code Creation and Software Engineering | 10 | plan, run-plan, verify, contract |
| 9 | Testing, Verification, and Quality Gates | 10 | run-plan, verify, report, trace |
| 10 | Debugging and Root-Cause Analysis | 10 | diagnose, parse-log, trace, exec |
| 11 | Data Engineering and File Transformation | 10 | data-transform, data-validate, verify |
| 12 | Databases, Storage, and Search Systems | 10 | db-query, data-validate, verify |
| 13 | Web, API, and Network Operations | 10 | http, diagnose, verify |
| 14 | Version Control, Builds, and Delivery | 10 | plan, run-plan, risk, trace, report |
| 15 | Cloud, Containers, and Infrastructure | 10 | risk, plan, run-plan, doctor |
| 16 | Security Engineering and Secure Operations | 10 | risk, secrets-scan, contract, approve |
| 17 | Documentation, Reports, and Knowledge Assets | 10 | report, plan, verify |
| 18 | Agent Planning, Reasoning, and Coordination | 10 | plan, orchestrate, route, estimate |
| 19 | Performance, Scale, and Optimization | 10 | estimate, parse-log, diagnose, report |
| 20 | Specialized Automation and Interface Skills | 10 | route, translate, schedule, plan |

Honest framing of coverage: the *mechanics* of each capability are implemented (bounded execution,
gating, retries, evidence, DAG control, diagnosis, transforms, bounded network/DB access, reporting).
Capabilities that require a specific external system — a cloud provider CLI, a Kubernetes cluster,
a CI service, a message queue, a remote host — are covered as far as the local command/HTTP surface
allows; there is no vendor SDK integration. See §10.

---

## 7. Configuration

Precedence: `asset-default-config.json` → `--config FILE` (or `NEXUS_CONFIG`) → `--work-root` /
`--state-dir` overrides. Unknown keys are preserved but ignored.

| Key | Default | Meaning |
| --- | --- | --- |
| `paths.work_root` | `.` | Base directory for relative paths and the default scope boundary. |
| `paths.state_dir` | `.nexus/state` | Audit trail and durable state. |
| `paths.run_dir` | `.nexus/runs` | Trace, run records, workflow checkpoints. |
| `paths.log_dir` | `.nexus/logs` | Reserved for captured streams. |
| `scope.allowed_roots` | `[]` | When non-empty, only these roots are permitted. |
| `scope.denied_roots` | `[]` | Always-permitted denials, evaluated first. |
| `scope.allow_outside_work_root` | `false` | Master switch for escaping the work root. |
| `execution.default_shell` | `auto` | `auto` picks bash on POSIX, PowerShell/cmd on Windows. |
| `execution.default_timeout_seconds` | `120` | Deadline when `--timeout` is absent. |
| `execution.max_timeout_seconds` | `3600` | Upper bound; larger values are rejected. |
| `execution.stall_timeout_seconds` | `300` | No-heartbeat window before a stall is declared. |
| `execution.heartbeat_pattern` | `^(\[?heartbeat\]?\|PROGRESS\|STEP\|TICK\|OK:)` | Line pattern that counts as progress. |
| `execution.kill_grace_seconds` | `5` | Grace between SIGTERM and SIGKILL. |
| `execution.max_output_bytes` / `max_output_lines` | `200000` / `400` | Output capture caps. |
| `retry.max_attempts` | `3` | Default total attempts (override with `--retries`, max 10). |
| `retry.base_delay_seconds` / `factor` / `max_delay_seconds` / `jitter_ratio` | `0.5` / `2.0` / `20` / `0.25` | Backoff shape. |
| `retry.retry_on_states` | `retryable_failure, transient_network, resource_saturation` | Extra states that may retry. |
| `orchestration.max_parallel` | `4` | Concurrent workflow nodes. |
| `orchestration.max_nodes` | `200` | Workflow size limit. |
| `orchestration.block_dependents_on_failure` | `true` | Whether a failed node blocks dependents. |
| `approval.required_levels` | `["high","critical"]` | Risk levels that need `--approve`. Empty list disables the gate (do not do this in production). |
| `approval.token_env` | `NEXUS_APPROVAL_TOKEN` | Reserved hook; not used for enforcement. |
| `approval.require_reference` | `true` | `--approve` without `--approval-ref` is refused. |
| `approval.audit_file` | `audit.jsonl` | File under `state_dir`. |
| `network.allow_network` | `false` | Master switch for `http`. |
| `network.allowed_hosts` | `[]` | Exact hostnames permitted. |
| `network.timeout_seconds` / `max_attempts` / `max_response_bytes` | `15` / `2` / `100000` | Request bounds. |
| `data.max_rows` | `100000` | Row cap for transforms and validation. |
| `database.allowed_roots` | `[]` | When non-empty, only databases under these roots open. |
| `database.read_only_by_default` | `true` | `--allow-write` is required to mutate. |
| `database.max_rows` | `5000` | Result row cap. |
| `limits.max_command_length` | `8000` | Longer command text is rejected. |
| `limits.max_workflow_bytes` | `1048576` | Workflow/read ceiling for large inputs. |
| `limits.max_log_records` | `20000` | Log records parsed per call. |
| `*_file` keys | asset filenames | Relative asset references; all resolve inside this folder. |

Example overlay that tightens the scope and refuses to run outside a project directory:

```json
{
  "paths": {"work_root": "/srv/project"},
  "scope": {"allowed_roots": ["/srv/project"], "denied_roots": ["/srv/project/secrets"]},
  "approval": {"required_levels": ["medium", "high", "critical"]},
  "execution": {"default_timeout_seconds": 60, "stall_timeout_seconds": 120}
}
```

---

## 8. Safety and approval rules

1. **Explicit approval gate.** Any command classified `high` or `critical` by `asset-policy-rules.json`
   is blocked unless `--approve --approval-ref "<who and why>"` is supplied. `--approve` without a
   reference is refused (`approval_reference_missing`). Every approval is appended to the audit log
   with the risk flags, command hash, operator, host, and correlation id.
2. **No self-escalation.** There is deliberately no way for a command, workflow node, or plan step to
   grant itself approval. Approval is a CLI-level human decision.
3. **Scope boundary.** Working directories and checked paths must sit inside `work_root` or
   `allowed_roots` and outside `denied_roots`; ambiguous or wildcard-broad targets are rejected.
   `--allow-outside` is an explicit, per-invocation opt-in.
4. **Network is off by default.** `http` requires both `network.allow_network` and an explicit host
   allowlist. Credentials in headers are refused. Mutating methods require approval and are not
   retried after a server response.
5. **Database writes are off by default.** Read-only connection, single statement, parameter binding,
   row caps, and approval for destructive statements.
6. **Secrets never leave.** Output, stderr, commands, trace entries, and reports pass through the
   redaction patterns; `secrets-scan` reports fingerprints, not values. `risk` and `diagnose` warn
   when secret-like content appears in their input.
7. **Destructive operations are named, not hidden.** `rm -rf`, `mkfs`, `dd of=/dev/*`, privilege
   escalation, history rewrite, unguarded `UPDATE`/`DELETE`, cloud destruction, container pruning,
   publishing, credential mutation, firewall flushes, and remote deletes each carry their own rule
   with a `safer` alternative shown in the failure envelope.
8. **Scheduling and publishing stay manual.** `schedule` writes text only. `approve` records intent
   only. Nothing in this tool publishes, deploys, or registers a job.
9. **Recovery over repetition.** Retries are bounded and only for retryable states; timed-out,
   stalled, and cancelled runs are never retried; checkpoints allow resume instead of restart;
   cancellation escalates SIGTERM → SIGKILL with a grace period.

---

## 9. Validation actually performed

Environment: Linux x86_64, CPython 3.13.16, bash and sh present; zsh, PowerShell, and cmd absent.

Run through the real CLI, with outputs inspected:

1. **Help and capability list** — `--help` (26 operations listed) and `capabilities`
   (26 operations, 20 categories, 200 capabilities). Filtered listings with `--category`,
   `--skill` (exact match returns 1), `--domain`, and `--out` verified.
2. **Execution paths** — successful command; failing command classified `dependency_fault` and not
   retried; transient failure retried once then succeeded (2 attempts recorded); `--file` script with
   `--env`; `--capture-dir`; `--artifact` present and missing; `--dry-run`; `--cwd` outside scope
   rejected.
3. **Limits and termination** — `--timeout 1` on `sleep 5` → `timed_out` in 1.06 s; heartbeat-gated
   stall (`--heartbeat --stall-timeout 1`) → `stalled`; heartbeat-respecting loop → `success` with
   6 heartbeats; SIGINT during a run → child terminated, state `cancelled`, exit 130.
4. **Approval gating** — `rm -rf /` blocked with `approval_required` and full risk matches; same
   action with `--approve --approval-ref` executed (under the work root); `--approve` without a
   reference refused; approval audit entries written; orchestrate node with `requires_approval`
   blocked then approved.
5. **Recovery** — checkpoint written by `exec`, `--resume` skipped the completed unit; `checkpoint`
   set/complete/show/reset; orchestrate rerun skipped completed nodes.
6. **Orchestration** — 9-node workflow: parallel shards, dependency merge, `argv` node (direct exec,
   no shell), `op: validate` node, `noop` node, deliberate failing node, blocked dependent
   (`dependency failed: always-fails`), artifact verification, status `failed` with per-node states.
   `validate-workflow` accepted the valid DAG and rejected an invalid one listing four separate
   errors (unknown property, duplicate id, missing node kind, two node kinds at once), plus a clean
   3-node cycle (`workflow contains a dependency cycle among: a, b, c`). `orchestrate` refused the
   invalid DAG with `workflow_invalid` instead of running it.
7. **Diagnosis** — exit 127 + stderr → `dependency_fault` with dependency probes; missing input
   rejected; 15 exit-code families and 15 stderr patterns available.
8. **Logs, trace, reports** — JSONL and plain logs parsed (levels, first error, correlation ids,
   malformed line counted); trace recorded and read back; Markdown reports rendered from a live run,
   from `--run`, and from `--from-trace`, and inspected.
9. **Translation** — forward (bash→PowerShell, bash→cmd, bash→sh) and reverse
   (PowerShell→bash, cmd→sh) verified on multi-command lines, including variable expansion
   (`$FOO` → `$env:FOO`, `%FOO%`), `set`/`export`, `ls`/`dir`, `cat`/`Get-Content`, `rm -rf`,
   `mkdir -p`, `kill`, `pwd`, and chained commands. Relative paths convert both ways
   (`./notes.txt` ↔ `.\notes.txt`). A Windows drive path captured inside a command argument stays
   unconverted because rules run in a single pass, and the response says so explicitly with a
   "Windows drive paths remain in the output" warning. Unsupported `chmod` on cmd emits an explicit
   comment plus warning; an unknown dialect is rejected.
10. **Scheduling** — `--every 15 --unit minutes`, cron spec files, timezone/overlap/missed/retries
    recorded, `flock` wrapper for `skip`, invalid minute (99) rejected; artifacts written and opened.
11. **Data** — filter/map/sort/aggregate over CSV produced `{city: Manila, count: 2, total: 320.0,
    mean: 160.0}`; numeric-string sort ordered 0,50,80,120,200 correctly; JSON/JSONL/CSV output
    re-parsed; unknown spec step rejected with the supported list; validation passed a clean CSV and
    reported six issue classes on a dirty CSV (rows, required, type, duplicate, range, pattern);
    delimited type semantics confirmed.
12. **Database** — read-only `SELECT` with a bound parameter; write blocked by default; destructive
    statement blocked without approval; approved `UPDATE` applied (1 row); multi-statement query
    rejected; missing database reported.
13. **HTTP** — allowlisted GET returned 200 and was saved with `--out`; POST blocked without approval,
    ran with approval, returned 501 from the fixture server and was **not** retried; unreachable port
    produced `http_unreachable` with both attempts; credential header refused; `ftp://` rejected;
    blocked host reported the exact configuration fix.
14. **Contracts, verification, scope, approvals** — valid contract accepted; incomplete contract
    listed six precise errors and still reported its critical risk; `verify` detected invalid JSON;
    `scope-check` allowed in-root paths and denied `/etc/passwd`; `approve` wrote an audit record.
15. **Configuration and recovery from bad input** — an overlay lowering `approval.required_levels`
    to `[]` allowed a high-risk command that the default config blocks (proves config drives
    behaviour); missing config file → `config_not_found`; malformed JSON config → `config_invalid`
    with line number; unknown operation → argparse usage error; missing required flags → exit 2 with
    a hint.
16. **Full regression sweep** — a 36-case script exercised all 26 operations plus invalid input,
    unknown operation, approval blocking, DB write blocking, and network blocking: 36/36 passed after
    the final edits (help, capabilities, filtered capabilities, route, plan, run-plan dry-run and real,
    exec success/failure/approval-block/dry-run/timeout, orchestrate, validate-workflow, risk,
    scope-check, contract, approve, estimate, parse-log, trace, diagnose, secrets-scan, translate,
    schedule, data-transform, data-validate, db-query, DB write block, HTTP block, verify, report,
    doctor, checkpoint).
17. **Portability** — the folder was copied into a clean temporary skill root beside a test
    `SKILL.md` and re-run with relative paths only (`python3 execution-operations/tools-nexus.py …`);
    `doctor`, `capabilities`, and `exec` all passed. The same was repeated from the extracted ZIP
    (§11).

Not performed, and therefore not claimed: execution with PowerShell, cmd, or zsh on this host;
Windows and macOS runs; orchestration at the 200-node ceiling; concurrent multi-process use of one
database; any cloud, SSH, or Kubernetes integration; verification of the generated scheduler files by
installing them on a live scheduler.

---

## 10. Limits and unavailable integrations

- **No remote execution fabric.** SSH, WinRM, cloud CLIs, and container runtimes are reached only if
  they exist as local commands; there is no connector or credential handling for them.
- **No vendor SDKs.** Cloud/container capabilities are covered by risk rules, planning, and local
  command execution — not by provider APIs.
- **Network access is opt-in and host-allowlisted.** There is no proxy negotiation, OAuth flow, or
  secret injection. Credentials must not be passed on the command line.
- **Database support is SQLite only.** No Postgres/MySQL drivers (standard library has none).
- **Schemas are enforced in code.** `asset-workflow-schema.json` documents the contract; validation is
  implemented in `tools-nexuslib.py` (`validate_workflow`, no `jsonschema` dependency) and covers required fields,
  unknown properties, node-kind exclusivity, reference validity, and cycles.
- **Translation is textual.** It does not parse the target language, and constructs without a rule are
  left unchanged with a warning.
- **Scheduling emits text.** Installing timers, editing crontabs, or registering tasks is out of scope
  by design.
- **Shells must exist on the host.** `--shell zsh|powershell|cmd` on a host without them fails with
  `shell_unavailable` instead of degrading silently.
- **Output capture is capped** (`max_output_bytes`, `max_output_lines`); large output is truncated and
  flagged, not streamed.
- **Approval is not cryptographically attested.** It is a logged, explicit human decision. There is no
  signature or token verification; `approval.token_env` is a reserved hook.
- **Windows-specific behaviour** (signal handling via `CREATE_NEW_PROCESS_GROUP`, cmd quoting) is
  implemented but untested here.

---

## 11. Troubleshooting and recovery

| Symptom | Cause | Fix |
| --- | --- | --- |
| `scope_outside_work_root` | Target or `--cwd` is outside `paths.work_root` | Run from the intended directory, set `--work-root`, or add `scope.allowed_roots`; `--allow-outside` is a per-invocation opt-in. |
| `approval_required` | Risk level is in `approval.required_levels` | Read the `matches[].safer` suggestions, then rerun with `--approve --approval-ref "who/why"`. |
| `approval_reference_missing` | `--approve` without `--approval-ref` | Always name the approver and the reason; it is written to the audit log. |
| `shell_unavailable` | Dialect binary not installed | Install the shell, or let `--shell auto` pick an available one; check `doctor`. |
| `invalid_shell` | Typo or unsupported dialect | Supported: `auto, bash, sh, zsh, cmd, powershell`. |
| `timed_out` | Deadline hit | Raise `--timeout` only with progress evidence; otherwise reduce payload or raise concurrency. `estimate` helps size it. |
| `stalled` | No heartbeat inside the stall window | Verify `--heartbeat-pattern` matches your job output; investigate deadlock, blocked I/O, or a hung child. |
| `cancelled` / exit 130 | Operator signal | Resume from `--checkpoint`; confirm cleanup ran. |
| `network_blocked` | Network disabled or host not allowlisted | Set `network.allow_network: true` and add the exact host, or pass `--allow-host`. |
| `db_write_blocked` | Read-only default | Take a backup, then `--allow-write` (plus `--approve` for destructive statements). |
| `query_multiple_statements` | More than one SQL statement | Submit one statement per call. |
| `workflow_invalid` | Structural problem | The envelope lists every error with the node index; fix ids, `depends_on`, cycles, or node kinds. |
| `asset_missing` / `config_missing` | A referenced asset or the default config is gone | Restore the file; every asset lives directly inside `execution-operations/`. |
| Exit 5 with "missing shared engine" | `tools-nexuslib.py` is not beside the entry point | Restore the file; both tools- files must sit in the same folder. |
| `config_invalid` | Malformed JSON overlay | The error gives line and column. |
| `no_route` | Query matched nothing | Use broader terms (`recovery`, `testing`, `deploy`, `logs`) or `--category`. |
| Report shows `-` for a field | Field absent from the source run record | Pass a run record produced by `exec`/`orchestrate`, or rebuild with `--from-trace`. |
| Partial data after failure | Operation blocked or failed mid-way | The envelope still carries `data`, `steps`, and `artifacts`; use the `run_record` path for the full evidence. |

Recovery pattern for a long job: run with `--checkpoint job.json --resume`. On interruption, fix the
cause, rerun the same command, and completed units are skipped. Use `checkpoint --show` to confirm
state and `--reset` to force a clean start.

---

## 12. Extension and maintenance

- **Add a risk rule or secret pattern.** Append to `asset-policy-rules.json`
  (`rules[]` for risk, `secret_patterns[]` for redaction). Each rule needs `id`, `level`, `category`,
  a Python-compatible `pattern`, `reason`, and `safer`. Regexes are compiled at load; an invalid
  pattern raises `policy_invalid` at first use, so run `doctor` and a `risk` smoke test after editing.
- **Teach a new exit code.** Add to `asset-exit-codes.json` under `exit_codes`; add stderr heuristics
  under `stderr_patterns`. `interpret` prefers the numeric code, then signals, then stderr patterns.
- **Add a shell rule.** Append to `asset-shell-dialects.json` `rules[]` with `from`/`to` maps.
  Command-anchored rules start with `(?:(?<=^)|(?<=[;&|]))` so they match after a separator; use
  `{1}`, `{2}` for capture groups, `approximate: true` for non-equivalent mappings, and
  `unsupported_targets` when the target cannot express the construct.
- **Add an operation.** Implement `cmd_<name>` in `tools-nexus.py`, register it in `build_parser()`
  and add a line to `OPERATIONS`; `capabilities` and `doctor` pick it up automatically. Put shared
  logic in `tools-nexuslib.py` rather than duplicating it.
- **Add a workflow op node.** Register a handler with `@nx.register_op("name")` (or assign into
  `OP_NODE_HANDLERS`) so `{"op": "name"}` nodes work.
- **Extend the catalog.** Add capabilities to `asset-skill-map.json`; every `operations` value must
  exist in `OPERATIONS` or `doctor` fails — this is the guard that keeps the map honest.
- **Keep the folder flat.** No subfolders, no `SKILL.md` here, one knowledge file, prefixed
  filenames. If you rename an asset, update the matching `*_file` key in `asset-default-config.json`
  and every reference in this document.
