# Roblox Production Toolkit

A local, deterministic toolkit for Roblox production work. It generates Luau, runs static checks, validates structure and maps, builds content and reports, runs engine and asset helpers, and simulates gameplay systems. Optional Open Cloud adapters exist, but they are off by default and never run without explicit approval.

## What is in the folder

| File | Role |
| --- | --- |
| `tools-roblox.py` | The CLI. Routes each capability to its handler and writes outputs. |
| `tools-core.py` | Shared helpers: input checks, findings, output writer, cancellation, component loader. |
| `tools-source.py` | Reads the catalog and turns natural-language selections into capability IDs. |
| `tools-engine.py` | Engine and asset helpers: physics, camera, pathfinding, UI, textures, avatar fitting (61 labels, plus `fitting`). |
| `tools-structure.py` | Studio structure, remotes, protocols, attributes, validation rules (48 labels). |
| `tools-simulate.py` | Deterministic simulations: payloads, prediction, matchmaking, inventory, dialogue, and more (127 labels). |
| `tools-content.py` | Reports, safety and moderation flows, localization, onboarding, and content plans (64 labels). |
| `tools-code.py` | Luau generators, a heuristic lint, three-way merge, refactor analysis, secret scanning, plugin edit operations (24 labels). |
| `tools-cloud.py` | Optional Open Cloud adapters: OpenAPI request planning, publish planning, webhooks, OAuth PKCE (10 labels). |
| `tools-selftest.py` | Built-in tests (`self-test`) and package checks (`check-package`). |
| `asset-catalog.json` | 340 capabilities in 34 categories. Each row is `[id, name, description]`. |
| `asset-recipes.json` | Per-capability metadata, input example, acceptance text, and routing label. |
| `asset-default-config.json` | Limits and policy. Network is disabled, and no cloud operations are allowed. |
| `asset-openapi-example.json` | Synthetic OpenAPI 3.0.3 document used by the cloud examples. It is not the live Roblox spec. |
| `asset-*.json`, `asset-luau-runtime.luau` | Reference data: avatar rules, place templates, policy, request schema, UI tokens, Luau runtime. |

## Commands

Run from this folder with `python3 tools-roblox.py <command>`. Inline JSON, `@path/to/file.json`, or `-` (stdin) is accepted wherever JSON is taken. Python 3.10 or newer is required.

- `help`: list commands.
- `capabilities [--category NAME] [--family F] [--engine E] [--query TEXT]`: list capabilities.
- `describe ID`: show a capability, its controls, and its acceptance criteria.
- `example ID`: print the example input for a capability.
- `inspect --input TEXT`: parse a pasted list of categories or skill names and show what it selects.
- `validate --capability ID (--input JSON | --request JSON | --use-examples)`: check input without writing files.
- `run --capability ID (--input JSON | --use-examples) [--output DIR] [--dry-run]`: run one capability and write `result.json` and `report.md`.
- `batch --category NAME | --capability ID ... --use-examples [--output DIR]`: run several capabilities. It continues after a per-capability error and stops on cancellation.
- `self-test`: run the built-in checks.
- `check-package [--root DIR]`: verify files, JSON, Python syntax, and routing.

Outputs default to `./roblox-output/<capability>`. Outputs inside this folder are refused. `--dry-run` runs the handler in memory and writes nothing. A batch writes to `<output>/<umbrella>/`, where the umbrella is the category slug, or `<dominant-engine>-suite` when a batch spans several categories.

Exit codes: `0` success; `2` usage or validation error; `3` findings failed with `--fail-on-findings`; `77` approval required; `73` output exists without `--overwrite`; `124` timeout; `130` cancelled.

## Safety defaults

- Execution is local by default. Nothing in the default configuration contacts the network.
- Cloud execution requires all of the following: `network.enabled` set to true, an operation on `allowed_operations`, an approval (`--approve`), and the API key present in the environment variable named by `network.api_key_env`. The key value is never read into outputs.
- Cloud requests use HTTPS only. HTTP requires `allow_http`, which is off by default. Hosts must be on `allowed_hosts`.
- Redirects are never followed. 429 and 5xx responses are retried with bounded backoff. POST is retried only on 429, and a POST that fails before a response is never retried.
- Publishing is limited to Saved versions. A place file is uploaded only when the operation declares an `application/octet-stream` body. Otherwise the request stays a plan.
- Webhooks verify HMAC-SHA256 signatures with a 300-second window, a constant-time comparison, and duplicate-delivery checks.
- The OAuth helper generates a PKCE S256 challenge. It does not exchange tokens.
- Plugin generation is edit-only: it produces `set_property`, `add_tag`, `remove_tag`, `set_attribute`, and `destroy` operations.

## Verifying the toolkit

```
python3 tools-roblox.py self-test
python3 tools-roblox.py check-package
```

`self-test` runs the 340 recipe examples in memory, checks that cloud dry runs open no sockets, runs an end-to-end CLI round trip into a temporary folder, and confirms that the CLI refuses missing input and refuses output inside the toolkit. It reports `status: ok` only when every check passes.

The recipe examples are minimal inputs that satisfy each handler's contract. They are not realistic production data. Many examples produce `fail` or `review` findings on purpose, because the input is deliberately thin. Treat those findings as demonstrations of the checks, not as defects.

## Limits and what was not verified

- The Luau lint in `tools-code.py` is a heuristic line-and-token check. It is not a Luau parser. Generated Luau is not compiled or executed here. No Luau checker is configured (`runtime.luau_checker` is null).
- The OpenAPI document in `asset-openapi-example.json` is synthetic. It has not been checked against the live Roblox Open Cloud service. Operator-supplied specs need the same review.
- Catalog descriptions were transcribed from the source material and have not been re-checked against an external source.
- Cloud behaviour (retries, redirects, approval gate, and publish handling) was tested against a local mock server with test-only overrides. It has not been tested against live Roblox endpoints.
