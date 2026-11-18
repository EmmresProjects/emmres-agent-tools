# Ultimate Thinking Memory: operating guide

This Skill Tool gives a single command-line entry point for 350 thinking, memory, and reasoning capabilities across 35 categories. It runs deterministic checks, keeps persistent memory and ledgers, builds checkpoints, and produces worksheets. It does not replace human judgment. Each capability carries a support level that says how much of the work the tool does, and the rest is yours.

## Run it

From any working folder (paths resolve against the current directory):

```
python3 tools-engine.py help
python3 tools-engine.py capabilities                 # list all 350
python3 tools-engine.py capabilities --category 17   # one category
python3 tools-engine.py categories
python3 tools-engine.py describe C24.02              # one capability, its handler, and its sibling operations
python3 tools-engine.py run C24.02 --input plan.json --option deadline=12 --output-dir out --state-dir state
python3 tools-engine.py selfcheck                    # verify the package is complete and consistent
```

Run `help` or `describe` before first use. Any capability ID (for example `C17.04`) or slug (`statistical-reasoning-analyst`) works with `run`.

Use `--op FAMILY:op` to run another operation from the same family. For example, `run C13.02 --op K:restore` restores a checkpoint, and `run C01.10 --op L:verify` checks a ledger. `describe` lists the sibling operations for each capability.

Common options for `run`:

| Option | Meaning |
| --- | --- |
| `--input PATH` | File or folder the capability reads (text, JSON, or a folder). |
| `--option key=value` / `-o` | Named parameter. Repeatable. Values are strings, parsed by the handler. |
| `--output-dir DIR` | Where artifacts, `<slug>.result.json`, and `<slug>.report.md` are written. Default `thinking-output/`. |
| `--state-dir DIR` | Persistent state: ledgers, library, memory, checkpoints index, backups. Default `thinking-state/`. |
| `--as-of YYYY-MM-DD` | Reference date for freshness, expiry, and progress checks. Default: today (UTC). This flag takes precedence over `-o as_of=`, so use one or the other. |
| `--timeout SECONDS` | Stops the run cleanly. Default from policy (60 s), maximum 600 s. |
| `--approve` | Records that a person approved a gated action. See Approval gates. |
| `--format text\|json` | Markdown report to stdout (default) or the full JSON envelope. |
| `--no-save` | Do not write result and report files. |

## Files in this folder

| File | Role |
| --- | --- |
| `tools-engine.py` | Command-line entry point. Loads every handler module, enforces timeouts, cancellation, approval, and exit codes, and writes results. |
| `tools-core.py` | Shared runtime: errors, validated input reading, atomic writes, file locks, hash-chained ledgers, timers, signal handling, the Context object, and report rendering. |
| `tools-text.py` | Handlers for families W (worksheet), R (requirements), A (text audits), E (evidence), X (combine and vary), C (context control). |
| `tools-graph.py` | Handlers for families D (dependencies and scheduling) and N (networks and concept maps). |
| `tools-quant.py` | Handlers for families P (decision scoring) and Q (quantitative reasoning), including a safe expression evaluator with dimensional checks. |
| `tools-files.py` | Handlers for families S (structure checks) and K (checkpoints: pack, verify, latest, handoff, restore). |
| `tools-state.py` | Handlers for families L (ledgers), B (knowledge library), M (memory), and V (action governance). |
| `tools-selftest.py` | Self-test suite. Runs in a temporary folder and checks each family against known answers, plus the gates, timeouts, and recovery paths. |
| `asset-capability-catalog.json` | The 350 capabilities: ID, slug, name, category, description, family, handler, and support level. |
| `asset-policy.json` | Limits, defaults, approval tiers, memory durability weights, source tiers, cue lists, patterns, and governance keywords. Edit this file to tune behavior. |
| `asset-worksheet-template.md` | Template filled by every worksheet capability (family W). |
| `asset-example-notes.md` | Sample document for the text families. |
| `asset-example-evidence.json` | Sample claims and sources for family E. |
| `asset-example-decision.json` | Sample criteria, alternatives, dimensions, risks, checks, votes, and allocations for families P. |
| `asset-example-quant.json` | Sample inputs for family Q. Each handler reads only the keys it needs. |
| `asset-example-tasks.json` | Sample task network for family D. |
| `asset-example-graph.json` | Sample graph with a cycle and an orphan node for family N. |
| `asset-example-actions.json` | Sample actions for family V (classification, approval gate, preview, rollback). |
| `knowledge-readme.md` | This guide. |

## Support levels: what the tool does and does not do

Every capability has one of three support levels. Read the level before relying on the output.

| Level | Count | What the tool does | What you must do |
| --- | --- | --- | --- |
| **automated** (211) | 211 | Deterministic calculation or structure check with reproducible output. | Check the inputs. Confirm the numbers match the situation. |
| **assisted** (35) | 35 | Keyword or pattern heuristic that flags candidates. Recall and precision are imperfect. | Review every flag. Treat "no finding" as "nothing matched", not as "clean". |
| **worksheet** (104) | 104 | Produces a structured template with the fields to fill in. It does not make the judgment. | A person or agent must answer each field with evidence and mark assumptions. |

Assisted operations include all A (text audit) operations, R:coverage, E:ground, C:drift, C:compress, V:classify, and V:escalate. Worksheet operations are all W:worksheet capabilities. Cognitive judgments (for example, whether a goal is well framed, or whether a risk is acceptable) are never scored by this tool.

## Operations by family

Operation keys are `FAMILY:op`. Each capability's default operation appears in `describe`. The table shows what each operation does.

**S: structure checks** (automated)
- `S:check`: validate JSON, Markdown (links, headings), Python syntax, and ZIP integrity. Set `skill_tool=true` to check this folder's naming and layout.
- `S:compare`: first differing line between two files (`other=PATH`).
- `S:sections`: required headings are present (`required=a,b,c`).
- `S:env`: Python version, importable modules (`modules=`), writable state and output folders, free disk.
- `S:join`: duplicate headings and broken links across Markdown files in a folder.
- `S:exists`: paths from a JSON list exist; absolute and `..` paths are rejected.

**K: checkpoints** (automated)
- `K:pack`: archive a folder or a JSON list of files into `<name>.zip` plus `<name>.manifest.json` with SHA-256 hashes. Writes an index record.
- `K:verify`: compare files with a manifest. Reports ok, changed, missing, and unexpected files. `archive=true` also checks the ZIP hash.
- `K:latest`: the newest checkpoint whose files and archive still verify.
- `K:handoff`: a continuation note (decisions, open issues, blockers, next actions, pinned details) as Markdown.
- `K:restore`: restore a checkpoint into `target=DIR`. Requires approval if the target is not empty. Backs up overwritten files first and rolls back on failure.

**L: ledgers** (automated). Hash-chained, append-only JSON lines in `state/ledgers/<name>.jsonl`.
- `L:append`: add a record (JSON input or `key=value` options). `ledger=NAME` selects the ledger.
- `L:list`: recent records, optionally filtered with `where=field=value`, `limit=N`.
- `L:verify`: recompute the hash chain. Reports broken links and damaged lines.
- `L:group`: count records by `field=NAME`.

**B: knowledge library** (automated). Versioned entries in `state/library/knowledge-library.json`.
- `B:add`: create or update an entry by `key` (default: slug of the title). Identical content is reported as unchanged. Changes create a new version.
- `B:search`: rank active entries against `query=` text or an input file. Retired entries are hidden unless `include_retired=true`.
- `B:versions`: version history for `key=`.
- `B:retire`: mark an entry retired with a reason. Nothing is deleted.

**M: memory** (automated). Scoped facts in `state/memory/task-memory.json`.
- `M:add`: store a key, value, scope, sensitivity, confidence (0 to 1), source, tags, and expiry (`expires=YYYY-MM-DD` or `ttl_days`). Same value reinforces the entry. A different value is kept as a conflict. Secrets are refused. Personal data requires approval.
- `M:recall`: the smallest relevant set for `query=`. Personal entries are excluded unless `include_personal=true`. Expired entries are excluded.
- `M:rank`: importance = confidence × durability × (1 + ln(1 + uses)). Durability weights: permanent 1.0, review 0.7, expiring 0.5.
- `M:consolidate`: entries with the same key and value are marked superseded, keeping the highest-confidence entry with provenance. Nothing is deleted.
- `M:expire`: mark expired entries as of `as_of`. `delete=true` removes them and requires approval.
- `M:conflicts`: keys with more than one live value, with the newest and most confident entry and a resolution prompt.

**V: action governance** (automated, except V:classify and V:escalate which are assisted). Input is a JSON list of actions with `id`, `description`, `targets`, `reversible`, `rollback`, and `idempotency_key`.
- `V:classify`: tier each action (low, medium, high, critical) from governance keywords.
- `V:gate`: exit code 3 unless every high or critical action is approved (`approved=A1,A2` or `--approve`). Runs nothing itself.
- `V:preview`: Markdown table of actions, targets, reversibility, and rollback. Warns about irreversible actions with no rollback.
- `V:idempotent`: register each `idempotency_key`; repeats are flagged so the action is not run twice.
- `V:stop-check`: `limits` (`max_<metric>`) against `observed`. Any breach returns STOP.
- `V:escalate`: route actions that touch identity or legal, financial, privacy, physical, or credential matters, or whose authority is unclear, to the named role.
- `V:audit`: compare performed actions (in the input or a ledger, `ledger=NAME`) with allowed actions and target prefixes.
- `V:privilege`: flag requested permissions that are broad or not needed.
- `V:rollback`: Markdown rollback plan. Reports irreversible actions with no rollback path.

**W: worksheet** (worksheet). `W:worksheet` fills `asset-worksheet-template.md` with the capability's fields and any source text (first 1500 characters). The tool does not answer the fields.

**R: requirements** (R:extract, R:checks automated; R:coverage assisted)
- `R:extract`: numbered requirements from a document, typed as hard, preference, or open, using cue lists in the policy.
- `R:coverage`: keyword overlap between requirements and files in `check_dir=DIR` (default: the input's folder). Assisted.
- `R:checks`: a verification checklist derived from the requirements.

**A: text audits** (all assisted). Each flags candidates for review.
- `A:claims` (unsourced figures), `A:fallacy` (pattern-based), `A:ambiguity` (vague terms), `A:consistency`, `A:clarity` (sentence length and readability), `A:pii` (emails, card-like numbers, Luhn-checked; output is redacted), `A:causal` (causal wording), `A:bias-cues`, `A:questions`, `A:terminology` (case and spelling variants of a term), `A:tone`, `A:actions` (action items), `A:classify`, `A:query-sanitize` (removes personal data from a query).

**E: evidence** (automated, except E:ground assisted). Input: `{"sources": [...], "claims": [...]}`.
- `E:map`: claims with no evidence (gaps) and evidence references that do not resolve (dangling).
- `E:quality`: best source tier per claim (strong, moderate, weak, unsupported).
- `E:freshness`: source age against limits (`as_of`).
- `E:conflicts`: topics where claims disagree, with authority-based resolution suggestions.
- `E:compare`: agreement across claims in the same topic.
- `E:rank`: sources by tier, then recency.
- `E:cite`: references, flagging incomplete ones.
- `E:saturation`: stops when new sources add few new claims (`window=N`).
- `E:bias`: distribution of source fields such as region or funding.
- `E:ground`: keyword overlap between each claim and documents in `docs=DIR` (assisted).

**X: combine and vary** (automated)
- `X:combine`: all combinations of role options (`elements` object), up to `limit`.
- `X:vary`: one-attribute variants of a base object.

**C: context control** (automated, except C:drift and C:compress which are assisted)
- `C:budget`: keeps the highest-priority items within `budget_tokens`.
- `C:dedupe`: removes exact and near-duplicate lines.
- `C:compress`: drops filler lines and keeps detail-rich lines within a budget. Assisted; check the artifact.
- `C:drift`: how much of the work text stays on `request=PATH`. Assisted.
- `C:pin`: lists exact details (dates, URLs, paths, numbers) that must survive summarizing.
- `C:prune`: keeps the top `per_class` items per class.

**D: dependencies and scheduling** (automated). Input: `{"tasks": [{"id", "deps", "duration", "value", "effort", "risk", "status", "deadline"}]}`.
- `D:topo`: dependency order and parallel groups. Rejects cycles.
- `D:critical`: earliest and latest times, slack, critical path, makespan.
- `D:rank`: value per effort, discounted by risk.
- `D:next`: ready tasks, the best safe next step.
- `D:backward`: latest starts for `deadline=N` (days from start, same unit as durations). Task-level deadlines also cap finish times.
- `D:wip`: work in progress against `limit=N`.

**N: networks** (automated). Input: `{"nodes": [...], "edges": [{"from", "to", "relation"}]}`, or text lines `from relation to`.
- `N:audit`: orphans, self-loops, unlabeled edges.
- `N:cycles`: strongly connected loops.
- `N:impact`: nodes reached from `changed=a,b` (`direction=downstream` or `upstream`).
- `N:leverage`: most connected nodes (`top=N`).
- `N:tree`: indented outline from roots, optionally by `relation=`.
- `N:index`: references between files in a folder.
- `N:export`: Markdown and JSON graph files.

**P: decision scoring** (automated). Inputs are JSON objects with the keys named below. Scores are higher-is-better unless noted.
- `P:score`: weighted average of `dimensions` (name, score, min, max, weight, critical). Verdict bands use `critical_below` and `pass_threshold`.
- `P:matrix`: weighted sum of `criteria` (name to weight) and `alternatives` (name to criterion scores).
- `P:pareto`: non-dominated alternatives.
- `P:weight-sensitivity`: whether the top alternative changes when each weight moves by `delta` (default 20%).
- `P:weakest`: lowest dimensions with the gap to the pass threshold.
- `P:compare`: regression against `other=PATH` (a newer score file).
- `P:risk`: `risks` with likelihood, impact, detectability (1 to 10 each) or severity, occurrence, detection. Score = product; bands are high, medium, low.
- `P:gate`: `checks` (name, result pass/warn/fail, critical). Any critical failure blocks.
- `P:consensus`: `items` mapping to reviewer votes (agree, disagree, abstain).
- `P:portfolio`: `allocations` (name to percent). Checks the total and `max_share`.

**Q: quantitative reasoning** (automated). Keys are read per operation. `asset-example-quant.json` contains a sample for each.
- `Q:stats`: mean, median, sample SD, 95% CI for `values`.
- `Q:bayes`: posterior from `prior`, `sensitivity`, `false_positive`.
- `Q:bounds`: range of `expression` over `ranges` (corners), or low/high.
- `Q:montecarlo`: samples of `expression` over `distributions` (`uniform`, `triangular`, `normal`, `lognormal`, `fixed`), with `n` (max 200000), `seed`, and `percentiles`.
- `Q:sensitivity`: one-at-a-time swing of `expression` over `ranges`, using `base` values and `delta_pct`.
- `Q:ev`: expected value and downside for `outcomes` (name, probability, value), with `risk_aversion`.
- `Q:scenarios`: expected value, worst case, and regret across `scenarios`.
- `Q:optimize`: grid search of `objective` over `search` ranges with `constraints` and `sense` (min or max).
- `Q:sample-size`: per-group size for `type` (means or proportions), with `alpha`, `power`, `sd`, `difference`, `dropout_pct`.
- `Q:faulttree`: top-event probability from a `tree` of basic, and, or nodes (independence assumed).
- `Q:forecast`: naive, mean, drift, and moving-average forecasts of `series`; `horizon=N`.
- `Q:calibrate`: coverage and error of forecast intervals (`forecasts` with point, low, high, actual).
- `Q:error`: MAE, RMSE, MAPE, and bias of `expected` against `actual`.
- `Q:brier`: Brier score and calibration bins for `forecasts` with p and outcome.
- `Q:integrity`: attrition by group (`groups`) and Holm-adjusted p-values (`pvalues`).
- `Q:allocate`: distributes `total` by weight within each item's min and max.
- `Q:pert`: PERT means, spreads, and an 80th percentile for `items`.
- `Q:diminish`: where returns fall below `threshold` (`gains` list).
- `Q:progress`: rate, estimated finish date, and confidence from `done`, `total`, and `start`.
- `Q:stress`: combined shocks (`shocks` as multipliers) on `expression` with `base` values.
- `Q:margin`: buffered totals and utilization against `capacity`.
- `Q:visual`: suggested chart type from data-shape flags.
- `Q:project`: cost over `years` with growth and maintenance.
- `Q:evaluate`: safe evaluation of `expression` with `variables`.
- `Q:stability`: elasticity and float32 versus float64 agreement.
- `Q:crossvalidate`: agreement between `expression_a` and `expression_b`.
- `Q:dimension`: units consistency of `expression` given `units`.

Expressions accept numbers, the operators `+ - * / % **`, parentheses, the constants `pi` and `e`, and the functions `sqrt`, `log`, `log10`, `exp`, `abs`, `min`, `max`, `round`, `floor`, `ceil`, `sin`, `cos`, `tan`, and `pow`. Anything else (attributes, unknown names, imports, strings) is refused. Expressions are limited to 500 characters.

## Input format notes

- Text inputs are UTF-8 and limited by `max_input_bytes` (policy).
- Options are strings. Numbers, booleans (`true`, `1`, `yes`, `on`), and dates are parsed by the handler. An unparseable value stops the run with exit code 1 and a message that names the option.
- JSON inputs must be objects or lists as described above. Missing keys produce an error that says which key is needed.

## Approval gates

Some actions change or expose data and need a person to confirm. The tool stops with exit code 3 and writes an approval record, and it runs only after you re-run the same command with `--approve`.

| Gate | Trigger |
| --- | --- |
| `K:restore` | Target folder exists and is not empty. Overwritten files are backed up under `state/backups/`. |
| `M:add` | `sensitivity=personal`. Secrets (`sensitivity=secret`) are always refused. |
| `M:expire` | `delete=true`. Expired memory is removed permanently. |
| `V:gate` | Any action classified high or critical is not approved. Approve by ID with `approved=A1,A2` or all at once with `--approve`. |

Approval means a person has confirmed. Do not pass `--approve` on behalf of a person without that confirmation. A run that was stopped for approval records the reason and the affected IDs under `approval` in `<slug>.result.json`. A run that went ahead with `--approve` records `approved_flag: true` in the same file.

## State, outputs, and recovery

- `state-dir` holds ledgers (`ledgers/`), the library (`library/`), memory (`memory/`), governance records (`governance/`), the checkpoint index (`checkpoints/index.jsonl`), restore backups (`backups/`), and temporary staging (`tmp/`).
- `output-dir` holds artifacts (Markdown, JSON, text) and per-run `<slug>.result.json` and `<slug>.report.md`.
- Writes are atomic: content goes to a temporary file, then replaces the target. Interrupted writes leave the previous version intact.
- Concurrent writers to the same state file are serialized with a sidecar `.lock` file (POSIX `fcntl`, created inside the state folder). Where `fcntl` is unavailable, locking is a no-op, so use one writer at a time.
- Ledgers are hash-chained. Run `L:verify` after a crash or a copy. A broken chain points to the first bad record. Do not edit hashes by hand; restore from a checkpoint or move the damaged file aside.
- If the memory, library, or governance state file is unreadable, the tool stops and asks you to restore or move it aside.

## Limits, timeouts, and cancellation

- Limits live in `asset-policy.json` under `limits`: input size, items, scanned files, combinations, grid points, Monte Carlo samples (200000), expression length, ledger records, checkpoint files and bytes, and write retries.
- Every run has a timeout (`--timeout`, default 60 s, maximum 600 s). Long handlers check the budget as they run. A hard alarm stops the run if they do not.
- SIGINT and SIGTERM cancel the run cleanly, write a result envelope with status `cancelled`, and exit 130.
- Temporary files are removed at exit.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Completed. Read `summary.verdict` and `items` for the findings. |
| 1 | Tool error: invalid input data or option values (the message names the option or key), unreadable state, or a restore that was rolled back. |
| 2 | Usage error: unknown command, capability, or operation; malformed `--option`; missing input path; timeout out of range. |
| 3 | Approval required. Nothing was changed. Re-run with `--approve` after a person confirms. |
| 4 | Timeout. Nothing is partially reported as complete. |
| 130 | Cancelled by a signal. |

## Dependencies

- Python 3.9 or newer. Developed and tested on Python 3.13.
- Python standard library only: `argparse`, `json`, `ast`, `zipfile`, `hashlib`, `statistics`, `random`, `math`, `re`, `tempfile`, `signal`, `fcntl` (POSIX; optional).
- No network access, no package installs, no background services.

## Limitations

- Support levels are honest, not exhaustive. Assisted results are candidates. Worksheet results are structure only.
- Keyword and pattern rules (A, R:coverage, E:ground, C:drift, C:compress, V:classify, V:escalate) miss paraphrases and can flag harmless text. Read every flag.
- Statistical and quantitative results are only as good as the inputs. Independence assumptions (fault trees, PERT totals, Monte Carlo inputs) are stated in each output.
- The expression evaluator is restricted by design. It cannot call external functions or read files.
- The tool does not execute the actions it governs. `V:gate` decides whether they may proceed; the caller runs them.
- Checkpoints and restores cover files, not running processes, databases, or remote services.
- Memory, library, and ledger files are local JSON and are not encrypted. Do not store secrets; `M:add` refuses them, but other files are not scanned for them unless you run `A:pii`.
- Output is only as current as `--as-of` and the system clock make it.
