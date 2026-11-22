#!/usr/bin/env python3
"""tools-oaa.py -- primary entry point for the offline-agents-automation Skill Tool.

A single offline, dependency-free command surface over 24 capability categories of
offline agent and automation operations: local knowledge indexing and retrieval,
durable memory and state, governed workflow execution with approvals and recovery,
answer-integrity guarding, evaluation and regression gating, registries, planners,
air-gap verification, tamper-evident audit, and filesystem/artifact management.

Usage
-----
  python3 tools-oaa.py help
  python3 tools-oaa.py capabilities
  python3 tools-oaa.py capabilities --ops
  python3 tools-oaa.py catalog --category 12
  python3 tools-oaa.py <group> <action> [--param value ... | --params '<json>']
  python3 tools-oaa.py selftest

Global options
--------------
  --config PATH        merge a JSON configuration file over the baseline
  --set key=value      override one configuration key (repeatable, dotted keys)
  --params JSON        operation parameters as a JSON object
  --input PATH         read operation parameters from a JSON file ("-" for stdin)
  --out PATH           write the structured result to a file
  --dry-run            report what would happen without mutating state
  --approve            human approval for actions that require it
  --compact            single-line JSON output
  --quiet              suppress the result body on success
  --trace              include a correlation id and timing in the envelope
  --workspace PATH     base directory for the workspace root (default: cwd)

Every command returns a JSON envelope: {"ok": true, "result": ...} or
{"ok": false, "error": {"code": ..., "message": ..., "details": ...}}.
Exit codes: 0 success, 2 invalid input, 3 not found, 4 policy denied,
5 approval required, 6 budget exceeded, 7 invalid state, 8 transient,
9 provider failed, 10 integrity failed, 11 cancelled, 12 workflow failed.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import os
import sys
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
if "oaa_kernel" in sys.modules:
    K = sys.modules["oaa_kernel"]
else:
    _spec = _ilu.spec_from_file_location("oaa_kernel",
                                         os.path.join(_HERE, "tools-oaa-kernel.py"))
    K = _ilu.module_from_spec(_spec)
    sys.modules["oaa_kernel"] = K
    _spec.loader.exec_module(K)

MODULE_FILES = [
    ("oaa_files", "tools-oaa-files.py"),
    ("oaa_knowledge", "tools-oaa-knowledge.py"),
    ("oaa_state", "tools-oaa-state.py"),
    ("oaa_guard", "tools-oaa-guard.py"),
    ("oaa_ops", "tools-oaa-ops.py"),
    ("oaa_eval", "tools-oaa-eval.py"),
    ("oaa_workflow", "tools-oaa-workflow.py"),
]

# Category coverage: honest mapping from catalog categories to the operations that
# actually serve them. status values:
#   implemented        full deterministic implementation in this package
#   implemented-partial a working subset, with the remaining part named in the note
#   adapter-required   the package supplies contracts, registries or planners only;
#                      a local model, runtime or system service must be supplied
#   not-implemented    out of scope for this package; see the note
CATEGORY_COVERAGE = {
    1: ("adapter-required", ["plan.quantization", "plan.hardware", "plan.context",
                            "registry.add", "registry.list", "registry.license",
                            "provider.infer"],
        "Portfolio registries, quantization/hardware/context planners and a provider "
        "adapter are implemented. Model weights, runtimes and profilers must be "
        "supplied locally."),
    2: ("implemented-partial", ["index.build", "index.query", "index.audit",
                               "index.rebuild", "eval.dataset"],
        "Local chunking, BM25 + hashed-vector hybrid retrieval, dedupe, freshness "
        "rebuild and retrieval evaluation are implemented. Neural embedding and "
        "cross-encoder reranking require a provider adapter."),
    3: ("adapter-required", ["provider.infer", "registry.add", "guard.redact"],
        "Vision, speech, OCR and multimodal stages need local models and runtimes. "
        "Media redaction detection is not implemented; only text redaction is."),
    4: ("adapter-required", ["plan.hardware", "plan.quantization", "plan.capacity",
                            "provider.infer"],
        "Sizing and admission math is implemented. Serving runtimes, batching, KV "
        "cache and speculative decoding are runtime concerns handled through the "
        "provider adapter."),
    5: ("implemented", ["index.build", "index.query", "index.audit",
                        "index.snapshot", "index.rebuild"],
        "File ingestion, structure-aware chunking, near-duplicate detection, "
        "integrity audit, snapshots and incremental rebuild with deletion "
        "propagation are implemented over a local JSON index."),
    6: ("implemented", ["memory.put", "memory.get", "memory.search", "memory.list",
                        "memory.resolve", "memory.compact", "memory.delete",
                        "memory.export", "memory.purge", "state.set", "state.get",
                        "state.list", "state.report"],
        "Classified durable memory, retention/expiry, conflict resolution, "
        "compaction, poisoning screen, provenance and export are implemented on "
        "SQLite."),
    7: ("implemented-partial", ["fs.read", "fs.write", "fs.delete", "fs.list",
                               "fs.hash", "workflow.run", "provider.infer"],
        "Path-scoped filesystem control, resource ceilings, step timeouts, "
        "cancellation and quarantine-by-convention are implemented. OS-level "
        "container/VM isolation is not included."),
    8: ("implemented-partial", ["policy.check", "policy.token-issue",
                               "policy.token-verify", "approval.request",
                               "approval.decide", "guard.redact"],
        "Least-privilege policy, default-deny, roles, expiring HMAC capability "
        "tokens and approval gates are implemented. A secret vault with encryption "
        "and an identity provider are not included."),
    9: ("implemented", ["workflow.run", "workflow.validate", "workflow.resume",
                        "workflow.status", "workflow.list", "workflow.cancel",
                        "dlq.list", "dlq.resolve", "state.set", "state.get"],
        "Durable workflow engine with dependency ordering, retries, checkpoints, "
        "compensation, cancellation, dead-letter queue and idempotent replay logic."),
    10: ("adapter-required", ["workflow.run", "workflow.status", "audit.timeline"],
         "Multi-agent coordination is expressed through workflow steps, approvals "
         "and correlation-id tracing. Agent role registries, quorum and debate "
         "controllers are not implemented."),
    11: ("implemented", ["registry.add", "registry.list", "registry.license",
                         "guard.schema", "audit.timeline", "workflow.status"],
         "Versioned registries with checksums, a JSON Schema subset validator, run "
         "manifests and config digests provide reproducibility records."),
    12: ("implemented", ["guard.check", "guard.cite", "guard.schema", "guard.calc",
                         "guard.injection", "guard.redact"],
         "Deterministic grounding, citation verification, contradiction-free claim "
         "scoring, exact arithmetic recheck, schema validation and injection "
         "detection are implemented."),
    13: ("implemented", ["eval.run", "eval.compare", "eval.dataset"],
         "Dataset runner over retrieval/grounding/calc/schema/injection tasks, "
         "baseline comparison with regression gating, and dataset derivation."),
    14: ("implemented", ["workflow.resume", "workflow.status", "dlq.list",
                         "dlq.resolve", "state.report", "audit.verify"],
         "Bounded retries with jitter, failure classification, checkpoints, resume, "
         "dead-letter handling, compensation and audit-chain verification."),
    15: ("implemented-partial", ["guard.injection", "guard.redact", "airgap.check",
                                "policy.check"],
         "Prompt-injection detection, secret redaction, egress/air-gap static "
         "scanning and command-surface denial are implemented. Malware scanning, "
         "dynamic analysis and signature verification are not included."),
    16: ("implemented-partial", ["airgap.check"],
         "Static air-gap verification of trees, configuration and egress logs is "
         "implemented. Firewall, DNS, namespace and certificate authority services "
         "are host responsibilities."),
    17: ("not-implemented", ["registry.add", "registry.license", "fs.hash"],
         "Package/model installation, mirrored repositories, ring rollouts and "
         "image building require host tooling. This package only records and "
         "verifies checksums and license metadata."),
    18: ("implemented", ["plan.capacity", "plan.hardware", "plan.quantization",
                         "plan.context", "state.report"],
         "Capacity, throughput, queue, storage-growth, memory-fit and context "
         "budget calculators with documented formulas."),
    19: ("implemented", ["audit.verify", "audit.timeline", "audit.append",
                         "state.report", "artifact.list"],
         "Hash-chained HMAC-sealed audit log, verification, timeline reconstruction, "
         "artifact provenance manifests and privacy redaction."),
    20: ("implemented", ["approval.request", "approval.decide", "approval.list",
                         "workflow.cancel"],
         "Approval gates with evidence, decisions, cancellation and full audit. "
         "Operator consoles and shift handover tooling are not included."),
    21: ("adapter-required", ["guard.schema", "artifact.write", "fs.write",
                             "eval.dataset"],
         "File and JSON/CSV export packaging with checksums and lineage is "
         "implemented. Live database connectors, ETL runners and CDC are not; use "
         "a local client through the provider/file boundary."),
    22: ("not-implemented", ["workflow.run", "fs.write", "provider.infer"],
         "Repository edits, builds and test harnesses require host toolchains. The "
         "engine can orchestrate such steps only through registered operations."),
    23: ("implemented-partial", ["index.build", "index.query", "guard.redact",
                                "artifact.write", "guard.schema"],
         "Text/Markdown/HTML/CSV/JSON ingestion, redaction, validation and artifact "
         "packaging are implemented. PDF and Office parsing or generation is not "
         "included; convert with an approved local tool first."),
    24: ("not-implemented", ["registry.add", "plan.hardware", "airgap.check"],
         "Fleet orchestration and edge provisioning require host and network "
         "tooling. Inventory compatibility checking and manuals are not included."),
}

GROUP_ORDER = ["knowledge", "integrity", "memory", "state", "workflow", "oversight",
               "reliability", "evaluation", "planning", "registry", "security",
               "observability", "files", "provider", "general"]

GLOBAL_FLAGS = {
    "--config": True, "--set": True, "--params": True, "--input": True,
    "--out": True, "--dry-run": False, "--approve": False, "--compact": False,
    "--quiet": False, "--trace": False, "--workspace": True,
}

USAGE = """offline-agents-automation -- offline agent and automation Skill Tool

Usage:
  python3 tools-oaa.py <command> [options]

Meta commands:
  help [command]        show this help or the help of one operation
  capabilities          list implemented capabilities and catalog coverage
  capabilities --ops    list every operation with its parameters
  catalog               show the 24 catalog categories and coverage status
  catalog --category N  show one category and its 12 skills
  ops                   alias for "capabilities --ops"
  config                print the effective merged configuration
  version               print the version envelope
  selftest [--verbose]  run the bundled self-test suite

Operation commands (group action):
  index  build|query|audit|snapshot|rebuild
  guard  check|cite|schema|injection|redact|calc
  memory put|get|search|list|resolve|compact|delete|export|purge
  state  set|get|list|report
  workflow validate|run|resume|status|list|cancel
  approval request|decide|list
  dlq    list|resolve
  audit  verify|timeline|append
  eval   run|compare|dataset
  plan   quantization|hardware|context|capacity
  registry add|list|license
  airgap check
  fs     read|write|delete|list|hash
  artifact write|list
  provider infer
  policy check|token-issue|token-verify|roles

Options (global):
  --config PATH     merge a JSON configuration file over the baseline
  --set k=v         override a dotted configuration key (repeatable)
  --params '<json>' operation parameters as a JSON object
  --input PATH      read operation parameters from a JSON file ('-' = stdin)
  --out PATH        write the structured result to a file
  --dry-run         validate and authorize without mutating state
  --approve         explicit human approval for approval-gated actions
  --compact         single-line JSON output
  --quiet           suppress the result body when the command succeeds
  --trace           add correlation id and duration to the envelope
  --workspace PATH  base directory for the workspace root (default: cwd)

Examples:
  python3 tools-oaa.py index build --params '{"source": "docs", "index": "kb"}'
  python3 tools-oaa.py index query --index kb --query "backup window"
  python3 tools-oaa.py guard check --params '{"answer": {"text": "..."}, "evidence": [{"source": "a.md", "text": "..."}]}'
  python3 tools-oaa.py workflow run --file asset-golden-workflow.json
  python3 tools-oaa.py eval run --save-report baselines/current.json
  python3 tools-oaa.py capabilities

Exit codes: 0 ok, 2 invalid input, 3 not found, 4 policy denied, 5 approval
required, 6 budget exceeded, 7 invalid state, 8 transient, 9 provider failed,
10 integrity failed, 11 cancelled, 12 workflow failed.
"""


# --------------------------------------------------------------------------------------
# Registry assembly
# --------------------------------------------------------------------------------------


def load_modules(verbose=False):
    modules = {}
    for name, filename in MODULE_FILES:
        path = os.path.join(_HERE, filename)
        if not os.path.isfile(path):
            raise K.StateError("required tool file is missing", file=filename)
        if name in sys.modules:
            modules[name] = sys.modules[name]
            continue
        spec = _ilu.spec_from_file_location(name, path)
        module = _ilu.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # pragma: no cover - defensive
            raise K.StateError("failed to load tool file", file=filename,
                               error=str(exc))
        modules[name] = module
        if verbose:
            print("loaded %s" % filename, file=sys.stderr)
    registry = K.Registry()
    for name, _ in MODULE_FILES:
        module = modules[name]
        if hasattr(module, "register"):
            module.register(registry)
    return registry, modules


def build_context(cfg, args):
    workspace = K.Workspace(cfg, base_dir=args.get("workspace") or os.getcwd())
    dry = bool(args.get("dry_run"))
    if dry:
        # A dry run must not create or mutate the workspace.
        pass
    else:
        workspace.ensure()
    store = K.StateStore(workspace.state_db, cfg)
    audit = K.AuditChain(cfg, workspace)
    policy = K.PolicyEngine(cfg, workspace)
    provider = K.make_provider(cfg)
    ctx = K.Context(cfg, workspace, store, audit, policy, provider=provider,
                    dry_run=dry, approve=bool(args.get("approve")),
                    run_id=K.new_id("run"), logger=lambda m, level="info":
                    print(m, file=sys.stderr))
    return ctx, store


# --------------------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------------------


class Args(dict):
    def __getattr__(self, item):
        return self.get(item)


def parse_args(argv):
    """Split argv into meta/command tokens, global options and operation params."""
    meta = argv[0] if argv else "help"
    rest = argv[1:]
    args = Args(meta=meta, params={}, sets=[], positional=[], flags={})
    index = 0
    params = {}
    while index < len(rest):
        token = rest[index]
        if token in GLOBAL_FLAGS:
            if GLOBAL_FLAGS[token]:
                index += 1
                if index >= len(rest):
                    raise K.InputError("option requires a value", option=token)
                value = rest[index]
                if token == "--set":
                    args["sets"].append(value)
                elif token == "--params":
                    try:
                        parsed = json.loads(value)
                    except json.JSONDecodeError as exc:
                        raise K.InputError("--params is not valid JSON",
                                           error=str(exc))
                    if not isinstance(parsed, dict):
                        raise K.InputError("--params must be a JSON object")
                    params.update(parsed)
                else:
                    args[token.lstrip("-").replace("-", "_")] = value
            else:
                args[token.lstrip("-").replace("-", "_")] = True
        elif token.startswith("--"):
            key = token[2:].replace("-", "_")
            if index + 1 < len(rest) and not rest[index + 1].startswith("--"):
                index += 1
                params[key] = K.coerce_scalar(rest[index])
            else:
                params[key] = True
        else:
            args["positional"].append(token)
        index += 1
    args["params"] = params
    return args


def merge_param_files(args):
    params = dict(args.get("params") or {})
    source = args.get("input")
    if source:
        if source == "-":
            raw = sys.stdin.read()
        else:
            path = source
            if not os.path.isfile(path):
                raise K.InputError("--input file not found", path=source)
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise K.InputError("--input is not valid JSON", error=str(exc))
        if not isinstance(parsed, dict):
            raise K.InputError("--input must contain a JSON object")
        merged = dict(parsed)
        merged.update(params)
        params = merged
    return params


# --------------------------------------------------------------------------------------
# Meta commands
# --------------------------------------------------------------------------------------


def cmd_version(args):
    return {"name": "offline-agents-automation", "version": K.VERSION,
            "schema_version": K.SCHEMA_VERSION,
            "python": sys.version.split()[0],
            "tool_files": [filename for _, filename in MODULE_FILES]}


def cmd_config(args, cfg, sources):
    redacted = json.loads(json.dumps(cfg))
    return {"sources": sources, "effective_config": redacted}


def coverage_summary():
    catalog = K.read_json(K.resource_path(K.CATALOG_FILENAME))
    rows = []
    counts = {}
    for category in catalog["categories"]:
        status, ops, note = CATEGORY_COVERAGE[category["position"]]
        counts[status] = counts.get(status, 0) + 1
        rows.append({
            "position": category["position"],
            "name": category["name"],
            "skills": len(category["skills"]),
            "status": status,
            "operations": ops,
            "note": note,
        })
    return catalog, rows, counts


def cmd_capabilities(args, registry):
    catalog, rows, counts = coverage_summary()
    total_skills = sum(row["skills"] for row in rows)
    if args.get("positional") and args["positional"][0] == "--ops":
        pass
    result = {
        "skill_tool": "offline-agents-automation",
        "version": K.VERSION,
        "entry_point": "tools-oaa.py",
        "operation_count": len(registry.ops),
        "operations": registry.describe(),
        "catalog": {
            "title": catalog["title"],
            "categories": len(rows),
            "skills": total_skills,
            "status_counts": counts,
            "coverage": rows,
        },
        "capability_list": [
            "local knowledge ingestion, chunking, hybrid retrieval and index audit",
            "durable classified memory with retention, resolution and compaction",
            "governed workflow execution with approvals, retries and compensation",
            "checkpoint/resume, dead-letter handling and cancellation",
            "grounding, citation, schema, injection, redaction and arithmetic guards",
            "evaluation runner, baseline comparison and regression gating",
            "deterministic planners for quantization, hardware, context and capacity",
            "registries with checksums and license compliance decisions",
            "static air-gap and egress verification",
            "hash-chained tamper-evident audit with provenance manifests",
            "scoped filesystem, artifact and provider-adapter boundary",
        ],
    }
    if args.get("ops") or args["params"].get("ops") or (
            args.get("positional") and "--ops" in args["positional"]):
        return {"operations": registry.describe(),
                "operation_count": len(registry.ops),
                "groups": _group_ops(registry)}
    return result


def _group_ops(registry):
    groups = {}
    for entry in registry.describe():
        groups.setdefault(entry["group"], []).append(entry["name"])
    return {group: sorted(groups[group]) for group in
            sorted(groups, key=lambda g: (GROUP_ORDER.index(g)
                                          if g in GROUP_ORDER else 99, g))}


def cmd_catalog(args):
    catalog, rows, counts = coverage_summary()
    requested = args["params"].get("category")
    if requested is None and args["positional"]:
        for token in args["positional"]:
            if token.isdigit():
                requested = int(token)
    if requested is not None:
        try:
            position = int(requested)
        except (TypeError, ValueError):
            raise K.InputError("--category must be an integer", value=requested)
        match = next((c for c in catalog["categories"]
                      if c["position"] == position), None)
        if match is None:
            raise K.NotFoundError("category not found", category=position,
                                  available=len(catalog["categories"]))
        status, ops, note = CATEGORY_COVERAGE[position]
        return {"category": match["name"], "position": position, "status": status,
                "operations": ops, "coverage_note": note,
                "skills": match["skills"]}
    return {"title": catalog["title"], "categories": len(rows),
            "skills": sum(r["skills"] for r in rows),
            "status_counts": counts,
            "design_principles": catalog["design_principles"],
            "coverage": [{"position": r["position"], "name": r["name"],
                          "skills": r["skills"], "status": r["status"]}
                         for r in rows]}


def cmd_help(args, registry):
    topic = args["positional"][0] if args["positional"] else None
    if not topic:
        return {"usage": USAGE}
    name = topic
    if name not in registry.ops and "." not in name:
        matches = [op for op in registry.names()
                   if op.split(".")[0] == name or op.startswith(name + ".")]
        if len(matches) == 1:
            name = matches[0]
        elif matches:
            return {"group": topic, "operations":
                    [registry.get(op)["name"] for op in matches],
                    "detail": {op: {"summary": registry.get(op)["summary"],
                                    "params": registry.get(op)["params"]}
                               for op in matches}}
    entry = registry.get(name)
    return {
        "operation": entry["name"],
        "summary": entry["summary"],
        "group": entry["group"],
        "parameters": entry["params"],
        "mutating": entry["mutating"],
        "approval_action": entry["approval_action"],
        "examples": entry["examples"],
        "invoke": "python3 tools-oaa.py %s --params '<json>'" % (
            entry["name"].replace(".", " ", 1)),
    }


def cmd_policy(args, ctx, registry):
    action = args["positional"][0] if args["positional"] else None
    params = args["params"]
    if action == "check":
        target = params.get("action") or (args["positional"][1]
                                          if len(args["positional"]) > 1 else None)
        if not target:
            raise K.InputError("policy check requires an action",
                               example="policy check --action fs.delete")
        roles = params.get("roles")
        if isinstance(roles, str):
            roles = [r.strip() for r in roles.split(",") if r.strip()]
        authorized, reason = ctx.policy.is_authorized(
            target, roles=roles)
        result = {"action": target, "authorized": authorized, "reason": reason,
                  "requires_approval": ctx.policy.requires_approval(target),
                  "roles": roles or K.get_path(ctx.cfg, "policy.default_roles")}
        ctx.audit.append({"event": "policy.check", "action": target,
                          "decision": "allow" if authorized else "deny",
                          "correlation_id": ctx.correlation_id,
                          "detail": {"reason": reason}})
        if not authorized:
            raise K.PolicyError("action denied by policy", action=target,
                                reason=reason)
        return result
    if action == "token-issue":
        target = params.get("action")
        if not target:
            raise K.InputError("token-issue requires --action")
        ctx.guard("policy.token.issue")
        token = ctx.policy.issue_token(target, subject=params.get("subject",
                                                                 "local-operator"),
                                       ttl_seconds=params.get("ttl_seconds"),
                                       constraints=params.get("constraints"))
        ctx.emit("policy.token_issued", action=target,
                 token_id=token["token_id"])
        return {"token": K.encode_token(token), "claims": token}
    if action == "token-verify":
        token = params.get("token") or args["params"].get("token")
        if not token:
            raise K.InputError("token-verify requires --token")
        claims = ctx.policy.verify_token(token, action=params.get("action"))
        return {"valid": True, "claims": claims}
    if action == "roles":
        return {"roles": K.get_path(ctx.cfg, "policy.roles", {}),
                "require_approval": K.get_path(ctx.cfg, "policy.require_approval"),
                "denied_actions": K.get_path(ctx.cfg, "policy.denied_actions"),
                "default_deny": K.get_path(ctx.cfg, "policy.default_deny")}
    raise K.InputError("unknown policy action", action=action,
                       allowed=["check", "token-issue", "token-verify", "roles"])


def cmd_selftest(args):
    path = os.path.join(_HERE, "tools-oaa-tests.py")
    if not os.path.isfile(path):
        raise K.StateError("self-test file is missing", file="tools-oaa-tests.py")
    spec = _ilu.spec_from_file_location("oaa_tests", path)
    module = _ilu.module_from_spec(spec)
    sys.modules["oaa_tests"] = module
    spec.loader.exec_module(module)
    verbose = bool(args.get("verbose")) or "--verbose" in args["positional"]
    report = module.run_suite(verbose=verbose)
    return report


# --------------------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------------------


def resolve_operation(meta, positional, registry):
    if meta in registry.ops:
        return meta, positional
    if not positional:
        if meta in ("version", "config", "capabilities", "catalog", "help", "ops",
                    "selftest", "policy", "docs"):
            return None, positional
        matches = [op for op in registry.names() if op.split(".")[0] == meta]
        if not matches:
            raise K.NotFoundError(
                "unknown command", command=meta,
                available_groups=sorted({op.split(".")[0] for op in registry.names()}),
                meta_commands=["help", "capabilities", "catalog", "config",
                               "version", "selftest", "policy"])
        raise K.InputError("command needs an action", command=meta,
                           available=[op.split(".", 1)[1] for op in matches])
    candidate = "%s.%s" % (meta, positional[0])
    if candidate in registry.ops:
        return candidate, positional[1:]
    matches = [op for op in registry.names() if op.split(".")[0] == meta]
    raise K.NotFoundError("unknown action", command="%s %s" % (meta, positional[0]),
                          available=[op.split(".", 1)[1] for op in matches])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    started = time.time()
    try:
        args = parse_args(argv)
        registry, modules = load_modules()
        meta = args["meta"]
        if meta in ("-h", "--help", "help"):
            return _emit({"ok": True, "result": cmd_help(args, registry)}, args)
        if meta in ("--version", "version"):
            return _emit({"ok": True, "result": cmd_version(args)}, args)
        if meta == "capabilities" or meta == "ops":
            payload = cmd_capabilities(args, registry)
            return _emit({"ok": True, "result": payload}, args)
        if meta == "catalog":
            return _emit({"ok": True, "result": cmd_catalog(args)}, args)
        overrides = {}
        for pair in args["sets"]:
            if "=" not in pair:
                raise K.InputError("--set expects key=value", value=pair)
            key, value = pair.split("=", 1)
            overrides[key.strip()] = value.strip()
        cfg, sources = K.load_config(args.get("config"), overrides)
        if meta == "config":
            return _emit({"ok": True, "result": cmd_config(args, cfg, sources)}, args)
        if meta == "selftest":
            return _emit({"ok": True, "result": cmd_selftest(args)}, args)
        if meta == "policy":
            ctx, store = build_context(cfg, args)
            try:
                payload = cmd_policy(args, ctx, registry)
                return _emit({"ok": True, "result": payload}, args,
                             correlation_id=ctx.correlation_id, started=started)
            finally:
                store.close()
        op_name, remaining = resolve_operation(meta, args["positional"], registry)
        if op_name is None:
            raise K.NotFoundError("unknown command", command=meta)
        args["positional"] = remaining
        params = merge_param_files(args)
        params = _bind_positional(registry, op_name, params, remaining)
        ctx, store = build_context(cfg, args)
        try:
            payload = execute_op(ctx, registry, op_name, params)
            envelope = {"ok": True, "result": payload}
            return _emit(envelope, args, correlation_id=ctx.correlation_id,
                         started=started)
        finally:
            store.close()
    except K.OAAError as exc:
        return _emit(exc.to_dict(), args if "args" in dir() else Args(),
                     code=exc.exit_code, started=started)
    except KeyboardInterrupt:
        error = K.CancelledError("interrupted by operator")
        return _emit(error.to_dict(), Args(), code=error.exit_code, started=started)
    except Exception as exc:  # pragma: no cover - unexpected faults
        error = {"ok": False,
                 "error": {"code": "UNEXPECTED", "message": str(exc),
                           "trace": traceback.format_exc()[-2000:]}}
        return _emit(error, Args(), code=1, started=started)


def _bind_positional(registry, op_name, params, remaining):
    """Allow `policy check --action x`-style shorthand: map bare ints/strings to
    declared parameter names in order when a positional token is present."""
    entry = registry.get(op_name)
    declared = entry["params"]
    if not remaining:
        return params
    merged = dict(params)
    for index, token in enumerate(remaining):
        if index < len(declared) and declared[index] not in merged:
            merged[declared[index]] = K.coerce_scalar(token)
    return merged


def execute_op(ctx, registry, op_name, params):
    entry = registry.get(op_name)
    if ctx.dry_run and entry["mutating"]:
        decision = ctx.policy.is_authorized(op_name)
        return {
            "dry_run": True,
            "operation": op_name,
            "mutating": True,
            "authorized": decision[0],
            "reason": decision[1],
            "requires_approval": ctx.policy.requires_approval(
                entry["approval_action"] or op_name),
            "would_receive_params": params,
            "note": "no state was mutated; remove --dry-run to execute",
        }
    ctx.emit("op.start", action=op_name)
    result = entry["handler"](ctx, params)
    ctx.emit("op.finish", action=op_name)
    return result


def _emit(envelope, args, code=0, correlation_id=None, started=None):
    if args.get("trace") and started is not None:
        envelope["trace"] = {
            "correlation_id": correlation_id,
            "duration_ms": round((time.time() - started) * 1000, 2),
        }
    out = args.get("out")
    if out and envelope.get("ok"):
        path = os.path.abspath(out)
        K.write_json(path, envelope)
        if not args.get("quiet"):
            print(json.dumps({"ok": True, "result": {"written": path}}, indent=2))
        return code
    if args.get("quiet") and envelope.get("ok"):
        return code
    indent = None if args.get("compact") else 2
    text = json.dumps(envelope, indent=indent, ensure_ascii=False, default=str)
    stream = sys.stdout if envelope.get("ok") else sys.stderr
    print(text, file=stream)
    return code


if __name__ == "__main__":
    sys.exit(main())
