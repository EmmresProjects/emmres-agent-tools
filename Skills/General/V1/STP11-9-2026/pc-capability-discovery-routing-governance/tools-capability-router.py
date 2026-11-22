#!/usr/bin/env python3
"""capability-discovery-routing-governance — primary command entry point.

Implements the Capability Discovery, Routing, and Governance layer for the
PC Agent Skill Catalog: capability listing and ingest, host inventory, startup
self-test, host-aware routing, lawful-use gate, risk-based approval, reversible
change planning, evidence/provenance, task decomposition, artifact routing,
operational boundary enforcement, clean-state validation, and toolchain version
policy. Offline by design: no network access, no embedded secrets.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

# Keep the packaged folder free of __pycache__ (structure contract).
sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent

# The shared core uses a hyphenated tools- filename, so load it by path.
_CORE_PATH = HERE / "tools-governance-core.py"
_spec = importlib.util.spec_from_file_location("governance_core", _CORE_PATH)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

EXIT_OK = core.EXIT_OK
EXIT_USAGE = core.EXIT_USAGE
EXIT_APPROVAL = core.EXIT_APPROVAL
EXIT_DENIAL = core.EXIT_DENIAL
EXIT_FAILURE = core.EXIT_FAILURE
EXIT_CANCEL = core.EXIT_CANCEL

HELP_TEXT = """\
capability-discovery-routing-governance (skill tool)
Primary entry point: tools-capability-router.py

USAGE
  python3 capability-discovery-routing-governance/tools-capability-router.py <command> [options]

COMMANDS
  help                              Show this help.
  capabilities                      List catalog capabilities.
      --category SEL (repeatable)   Select by number, "category N", or name substring.
      --search TEXT                 Score skills against a query.
      --from PATH                   Parse a plain-text, Markdown, or HTML capability list.
      --out PATH                    Write parsed/listed capability JSON to PATH.
      --limit N                     Limit search results (default 10).
      --json                        Emit JSON instead of text.
  inventory                         Probe every defined tool; emits contract rows.
      --out PATH                    Write the result JSON to PATH.
      --json                        Emit JSON (default for this command).
  selftest                          Resolve every selected capability to a contract status.
      --skill ID (repeatable)       Select skills by id (cNNsNN).
      --category SEL (repeatable)   Select whole categories.
      --search TEXT                 Select via catalog search.
      --limit N                     Cap the number of rows.
      --assume HOST                 Re-evaluate against linux|windows|macos.
      --out PATH                    Write rows JSON to PATH (rows reference it).
      --fail-on-not-found           Exit 5 if any row is not FOUND.
  route                             Host-aware routing decision.
      --skill ID (repeatable)       Route specific skill ids.
      --job TEXT                    Route free-form job text via catalog search.
      --max N                       Max matches for --job (default 5, cap 20).
      --assume HOST                 Simulate another host type.
      --evidence PATH               Append a JSONL evidence record.
  gate                              Lawful-use gate (allow/deny/incomplete).
      --action TEXT (required)
      --authorized/--owns-artifact/--license-ok/--privacy-ok yes|no
      --evidence PATH
  risk                              Classify an action and check approval.
      --action TEXT (required)
      --approve --approver NAME --reason TEXT   Grant higher approval levels.
      --evidence PATH
  plan-change                       Reversible change planner.
      --target PATH --action TEXT   Hash, back up, and write a change plan.
      --rollback PLAN.json          Restore the verified backup (recovery path).
      --plan-out PATH --approve --approver NAME --reason TEXT --evidence PATH
  provenance                        Evidence ledger.
      record --event E --evidence PATH [--source PATH] [--command TEXT]
               [--skill ID] [--note TEXT] [--actor NAME]
      show --evidence PATH [--last N] [--validate]
  decompose                         Break a goal into inspect..handoff stages.
      --goal TEXT (required)
  artifact-route                    Identify an artifact and select a safe handler.
      --file PATH (required)
  boundary                          Operational boundary enforcer.
      --request TEXT (required) [--evidence PATH]
  clean-state                       Repeat a command in fresh directories (needs --approve).
      --argv '["cmd","arg",...]' (required) --repeat N (2..3) --approve [--evidence PATH]
  version-policy                    Check installed tools against version pins.
      --tool ID (repeatable) [--evidence PATH]

GLOBAL OPTIONS (after the command)
  --catalog PATH --probes PATH --policy PATH --schema PATH   Override asset files
  --compact                        Single-line JSON output
  --actor NAME                     Actor recorded in evidence records
  --debug                          Re-raise unexpected errors with a traceback

EXIT CODES
  0 success | 1 unexpected internal error | 2 usage or input error
  3 approval required | 4 policy denial (gate/boundary) | 5 integrity or
  readiness failure | 130 cancelled (temporary files cleaned up)

EXAMPLES
  python3 capability-discovery-routing-governance/tools-capability-router.py help
  python3 capability-discovery-routing-governance/tools-capability-router.py capabilities --category 8
  python3 capability-discovery-routing-governance/tools-capability-router.py selftest --skill c08s01
  python3 capability-discovery-routing-governance/tools-capability-router.py route --job "inspect PE headers"
  python3 capability-discovery-routing-governance/tools-capability-router.py risk --action "delete all user files"
"""


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--catalog", help="override capability catalog JSON path")
    common.add_argument("--probes", help="override self-test probes JSON path")
    common.add_argument("--policy", help="override policy pack JSON path")
    common.add_argument("--schema", help="override evidence schema JSON path")
    common.add_argument("--compact", action="store_true", help="single-line JSON")
    common.add_argument("--actor", help="actor name for evidence records")
    common.add_argument("--debug", action="store_true",
                        help="re-raise unexpected errors with a traceback")

    parser = argparse.ArgumentParser(
        prog="tools-capability-router.py",
        description="Capability discovery, routing, and governance skill tool",
        add_help=True,
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("help", parents=[common], help="show built-in help")

    p = sub.add_parser("capabilities", parents=[common],
                       help="list or parse capabilities")
    p.add_argument("--category", action="append", default=None)
    p.add_argument("--search", default=None)
    p.add_argument("--from", dest="from_path", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("inventory", parents=[common],
                       help="probe every defined tool")
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true", help="emit JSON (default)")

    p = sub.add_parser("selftest", parents=[common],
                       help="startup self-test over catalog capabilities")
    p.add_argument("--skill", action="append", default=None)
    p.add_argument("--category", action="append", default=None)
    p.add_argument("--search", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--assume", choices=["linux", "windows", "macos"], default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--fail-on-not-found", action="store_true")

    p = sub.add_parser("route", parents=[common], help="host-aware task router")
    p.add_argument("--skill", action="append", default=None)
    p.add_argument("--job", default=None)
    p.add_argument("--max", type=int, default=None)
    p.add_argument("--assume", choices=["linux", "windows", "macos"], default=None)
    p.add_argument("--evidence", default=None)

    p = sub.add_parser("gate", parents=[common], help="lawful-use gate")
    p.add_argument("--action", required=True)
    p.add_argument("--authorized", default=None)
    p.add_argument("--owns-artifact", default=None)
    p.add_argument("--license-ok", default=None)
    p.add_argument("--privacy-ok", default=None)
    p.add_argument("--evidence", default=None)

    p = sub.add_parser("risk", parents=[common], help="risk classifier and approver")
    p.add_argument("--action", required=True)
    p.add_argument("--approve", action="store_true")
    p.add_argument("--approver", default=None)
    p.add_argument("--reason", default=None)
    p.add_argument("--evidence", default=None)

    p = sub.add_parser("plan-change", parents=[common],
                       help="reversible change planner and rollback")
    p.add_argument("--target", default=None)
    p.add_argument("--action", default="modify target file after backup")
    p.add_argument("--rollback", default=None)
    p.add_argument("--plan-out", default=None)
    p.add_argument("--approve", action="store_true")
    p.add_argument("--approver", default=None)
    p.add_argument("--reason", default=None)
    p.add_argument("--evidence", default=None)

    p = sub.add_parser("provenance", parents=[common],
                       help="evidence and provenance recorder")
    psub = p.add_subparsers(dest="prov_command", required=True)
    pr = psub.add_parser("record", parents=[common], help="append an evidence record")
    pr.add_argument("--event", required=True)
    pr.add_argument("--evidence", required=True)
    pr.add_argument("--source", action="append", default=None)
    pr.add_argument("--command", dest="commands", action="append", default=None)
    pr.add_argument("--skill", default=None)
    pr.add_argument("--note", default=None)
    pv = psub.add_parser("show", parents=[common], help="read evidence records")
    pv.add_argument("--evidence", required=True)
    pv.add_argument("--last", type=int, default=None)
    pv.add_argument("--validate", action="store_true")

    p = sub.add_parser("decompose", parents=[common],
                       help="task decomposer into ordered stages")
    p.add_argument("--goal", required=True)

    p = sub.add_parser("artifact-route", parents=[common],
                       help="artifact handling route selector")
    p.add_argument("--file", required=True)

    p = sub.add_parser("boundary", parents=[common],
                       help="operational boundary enforcer")
    p.add_argument("--request", required=True)
    p.add_argument("--evidence", default=None)

    p = sub.add_parser("clean-state", parents=[common],
                       help="clean-state validation manager")
    p.add_argument("--argv", required=True)
    p.add_argument("--repeat", type=int, default=2)
    p.add_argument("--approve", action="store_true")
    p.add_argument("--evidence", default=None)

    p = sub.add_parser("version-policy", parents=[common],
                       help="toolchain version policy manager")
    p.add_argument("--tool", action="append", default=None)
    p.add_argument("--evidence", default=None)

    return parser


def load_assets(args):
    return core.Assets(
        base_dir=HERE,
        catalog=args.catalog,
        probes=args.probes,
        policy=args.policy,
        schema=args.schema,
    )


def emit(envelope, exit_code, compact=False):
    print(json.dumps(envelope, indent=None if compact else 2, ensure_ascii=False))
    return exit_code


def parse_bool(value):
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered in ("yes", "y", "true", "t", "1"):
        return True
    if lowered in ("no", "n", "false", "f", "0"):
        return False
    raise core.ToolError(
        f"invalid yes/no value: {value}", EXIT_USAGE,
        {"accepted": ["yes", "no", "true", "false", "1", "0"]})


def effective_host(assume=None):
    host = core.host_info()
    if assume:
        host["host_type"] = assume
        host["virtualization_available"] = core.virtualization_available(assume)
    return host


def row_evidence_path(out_path):
    return str(out_path) if out_path else None


def write_json(path, data):
    target = Path(path)
    if target.parent and str(target.parent) not in ("", "."):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def summarize_rows(rows):
    by_status = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    return {"total": len(rows), "by_status": by_status}


def maybe_append_evidence(args, assets, event, result_summary, commands=None,
                          skill_id=None, sources=None):
    if not getattr(args, "evidence", None):
        return None
    record = core.make_evidence_record(
        event, core.host_info(), actor=args.actor, skill_id=skill_id,
        sources=sources or [], commands=commands or [],
        result=result_summary,
    )
    return core.append_evidence(args.evidence, record, assets.schema)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_help(args):
    print(HELP_TEXT)
    return EXIT_OK


def cmd_capabilities(args, assets):
    catalog = assets.catalog
    filters = [bool(args.category), bool(args.search), bool(args.from_path)]
    if sum(filters) > 1:
        # Priority: --from > --category > --search, applied in that order.
        pass

    if args.from_path:
        text = core.read_input_file(
            args.from_path, assets.policy["limits"].get("parse_input_max_bytes",
                                                        5 * 1024 * 1024))
        parsed = core.parse_capability_text(text, origin=str(args.from_path))
        result = {
            "source": str(args.from_path),
            "format": parsed["format"],
            "category_headers": parsed["categories"],
            "entry_count": len(parsed["entries"]),
            "entries": parsed["entries"],
        }
        if args.out:
            write_json(args.out, result)
            result["written_to"] = str(args.out)
        if args.json:
            return emit(core.ok("capabilities", result), EXIT_OK, args.compact)
        print(f"parsed {result['entry_count']} capability entries "
              f"from {args.from_path} (format: {result['format']})")
        for entry in parsed["entries"]:
            cat = f" [{entry['category']}]" if entry.get("category") else ""
            desc = f" — {entry['description']}" if entry.get("description") else ""
            print(f"  - {entry['name']}{cat}{desc}")
        if parsed["categories"]:
            print("category headers: " + "; ".join(parsed["categories"]))
        return EXIT_OK

    if args.category:
        pairs = core.select_skills(catalog, category_selector=args.category)
        cats = []
        seen = set()
        for cat, _skill in pairs:
            if cat["id"] not in seen:
                seen.add(cat["id"])
                cats.append(cat)
        result = {
            "selected_categories": [
                {"id": c["id"], "name": c["name"], "skill_count": len(c["skills"])}
                for c in cats
            ],
            "skill_count": len(pairs),
            "skills": [
                {"id": s["id"], "name": s["name"], "description": s["description"],
                 "risk_hint": s.get("risk_hint")}
                for _c, s in pairs
            ],
        }
        if args.out:
            write_json(args.out, result)
            result["written_to"] = str(args.out)
        if args.json:
            return emit(core.ok("capabilities", result), EXIT_OK, args.compact)
        for c in cats:
            print(f"{c['id']}. {c['name']} ({len(c['skills'])} skills)")
        for s in result["skills"]:
            print(f"  {s['id']}  {s['name']}")
        return EXIT_OK

    if args.search:
        limit = max(1, min(args.limit, 50))
        hits = core.search_catalog(catalog, args.search, limit=limit)
        result = {
            "query": args.search,
            "match_count": len(hits),
            "matches": [
                {"id": h["skill"]["id"], "score": h["score"],
                 "category_id": h["category"]["id"],
                 "category": h["category"]["name"],
                 "name": h["skill"]["name"],
                 "description": h["skill"]["description"]}
                for h in hits
            ],
        }
        if args.out:
            write_json(args.out, result)
            result["written_to"] = str(args.out)
        if args.json:
            return emit(core.ok("capabilities", result), EXIT_OK, args.compact)
        if not hits:
            print(f"no capability matches for: {args.search}")
            return EXIT_OK
        for h in result["matches"]:
            print(f"  {h['id']}  score={h['score']}  {h['name']}")
        return EXIT_OK

    # Summary listing (default).
    result = {
        "catalog": catalog.get("catalog"),
        "title": catalog.get("title"),
        "category_count": catalog["category_count"],
        "skill_count": catalog["skill_count"],
        "id_format": catalog.get("id_format"),
        "requirement_source": catalog.get("requirement_source"),
        "categories": [
            {"id": c["id"], "name": c["name"], "skill_count": len(c["skills"])}
            for c in catalog["categories"]
        ],
    }
    if args.out:
        write_json(args.out, result)
        result["written_to"] = str(args.out)
    if args.json:
        return emit(core.ok("capabilities", result), EXIT_OK, args.compact)
    print(f"{catalog.get('title')} — {result['category_count']} categories, "
          f"{result['skill_count']} skills (id format {result['id_format']})")
    for c in result["categories"]:
        print(f"{c['id']:>2}. {c['name']} ({c['skill_count']} skills)")
    return EXIT_OK


def build_inventory_rows(assets, runner, host, evidence_path):
    contract_fields = assets.probes["contract"]["required_fields"]
    rows = []
    for probe in assets.probes["probes"]:
        probe_id = probe["id"]
        run = runner.run(probe_id)
        pin = runner.pins.get(probe_id)
        requirements = [f"tool:{probe_id}"]
        if pin:
            requirements.append(f"pin:>= {pin}")
        if run["status"] == "MISSING":
            action = (f"install {probe['test_command'].split()[0]} or route work "
                      "to a worker that provides it")
        elif run["status"] == "WRONG VERSION":
            action = (f"upgrade {probe_id} to >= {pin} or update version_pins in "
                      "asset-policy-pack.json")
        elif run["status"] == "BROKEN":
            action = f"repair {probe_id}: {run.get('message', '')[:120]}"
        elif run["status"] == "FOUND" and pin and not run.get("detected_version"):
            action = f"verify {probe_id} version manually (pin >= {pin} unverified)"
        else:
            action = "none required"
        row = {
            "capability_id": f"tool:{probe_id}",
            "status": run["status"],
            "detected_command": run.get("detected_command"),
            "detected_version": run.get("detected_version"),
            "host_type": host["host_type"],
            "architecture": host["architecture"],
            "requirements": requirements,
            "test_command": run.get("test_command"),
            "exit_code": run.get("exit_code"),
            "evidence_path": evidence_path,
            "recommended_action": action,
            "message": run.get("message", ""),
        }
        for field in contract_fields:
            if field not in row:
                row[field] = None
        rows.append(row)
    return rows


def cmd_inventory(args, assets):
    host = effective_host(None)
    runner = core.ProbeRunner(assets)
    evidence_path = row_evidence_path(args.out)
    rows = build_inventory_rows(assets, runner, host, evidence_path)
    summary = summarize_rows(rows)
    result = {"host": host, "rows": rows, "summary": summary,
              "probe_count": len(rows)}
    if args.out:
        write_json(args.out, result)
    maybe_append_evidence(
        args, assets, "inventory",
        {"summary": summary}, commands=["inventory"])
    return emit(core.ok("inventory", result), EXIT_OK, args.compact)


def select_selftest_pairs(assets, args):
    catalog = assets.catalog
    pairs = []
    seen = set()
    if args.skill:
        for skill_id in args.skill:
            cat, skill = assets.find_skill(skill_id)
            if skill["id"] not in seen:
                seen.add(skill["id"])
                pairs.append((cat, skill))
        return pairs
    if args.category or args.search:
        pairs = core.select_skills(catalog, category_selector=args.category,
                                   search=args.search, limit=args.limit)
        return pairs
    return core.select_skills(catalog)


def cmd_selftest(args, assets):
    host = effective_host(args.assume)
    runner = core.ProbeRunner(assets)
    pairs = select_selftest_pairs(assets, args)
    evidence_path = row_evidence_path(args.out)
    rows = [
        core.resolve_skill(skill, cat, runner, host, evidence_path=evidence_path)
        for cat, skill in pairs
    ]
    summary = summarize_rows(rows)
    readiness = "pass" if all(r["status"] == "FOUND" for r in rows) else "fail"
    result = {
        "host": host,
        "scope": {
            "skill_ids": [s["id"] for _c, s in pairs][:50],
            "skill_count": len(pairs),
            "assume": args.assume,
        },
        "summary": summary,
        "readiness": readiness,
        "rows": rows,
        "contract_fields": assets.probes["contract"]["required_fields"],
        "status_precedence": assets.probes["contract"]["status_precedence"],
    }
    if args.out:
        write_json(args.out, result)
    maybe_append_evidence(
        args, assets, "selftest",
        {"summary": summary, "readiness": readiness},
        commands=["selftest" + (f" --assume {args.assume}" if args.assume else "")])
    exit_code = EXIT_OK
    if args.fail_on_not_found and readiness != "pass":
        exit_code = EXIT_FAILURE
    return emit(core.ok("selftest", result), exit_code, args.compact)


def routing_for(skill, category, row, host, policy):
    req = skill["requires"]
    gates = []
    if row["status"] in (
        "MACOS HOST REQUIRED", "WINDOWS HOST REQUIRED", "GPU REQUIRED",
        "NETWORK REQUIRED", "VIRTUALIZATION REQUIRED", "LICENSE REQUIRED",
        "MISSING",
    ):
        gates.append(row["status"])
    tools_missing = [
        r["probe_id"].split(":", 1)[-1]
        for r in row.get("tool_results", []) if r["status"] == "MISSING"
    ]
    tools_broken = [
        r["probe_id"].split(":", 1)[-1]
        for r in row.get("tool_results", []) if r["status"] == "BROKEN"
    ]
    tools_wrong = [
        r["probe_id"].split(":", 1)[-1]
        for r in row.get("tool_results", []) if r["status"] == "WRONG VERSION"
    ]
    workers = []
    status = row["status"]
    if status == "MACOS HOST REQUIRED":
        workers.append("macos-worker")
    elif status == "WINDOWS HOST REQUIRED":
        workers.append("windows-worker")
    elif status == "GPU REQUIRED":
        workers.append("gpu-worker")
    elif status == "NETWORK REQUIRED":
        workers.append("network-enabled-worker")
    elif status == "VIRTUALIZATION REQUIRED":
        workers.append("virtualization-worker")
    elif status == "LICENSE REQUIRED":
        workers.append("license-confirmation")
    if tools_missing or tools_broken or tools_wrong:
        names = sorted(set(tools_missing + tools_broken + tools_wrong))
        workers.append("provision tools: " + ", ".join(names))
    if not workers:
        workers.append(f"local-{host['host_type']}")

    risk = skill.get("risk_hint", "read_only")
    approval = policy.get("risk_levels", {}).get(risk, {}).get("approval", "none")
    flags = policy.get("approval_flags", {}).get(approval, "")
    preferred = req.get("preferred_host", host["host_type"])
    notes = []
    if preferred != host["host_type"] and preferred in req.get("hosts", []):
        notes.append(f"preferred host is {preferred}; current host lane is acceptable")
    if status == "FOUND" and not (req.get("tools") or req.get("software")):
        notes.append("no external tool dependencies; agent-executable capability")

    return {
        "status": status,
        "runnable_now": status == "FOUND",
        "current_host": host["host_type"],
        "preferred_host": preferred,
        "acceptable_hosts": req.get("hosts", []),
        "worker_recommendation": workers,
        "gates_failed": gates,
        "tools_missing": tools_missing,
        "tools_broken": tools_broken,
        "tools_wrong_version": tools_wrong,
        "risk_hint": risk,
        "approval_required": approval,
        "approval_flags_needed": flags,
        "requirement_source": "heuristic from asset-capability-catalog.json",
        "notes": notes,
    }


def cmd_route(args, assets):
    if bool(args.skill) == bool(args.job):
        raise core.ToolError(
            "provide exactly one of --skill or --job", EXIT_USAGE,
            {"example": "route --skill c08s01  or  route --job 'inspect PE headers'"})
    policy = assets.policy
    host = effective_host(args.assume)
    runner = core.ProbeRunner(assets)

    if args.skill:
        targets = []
        for skill_id in args.skill:
            targets.append(assets.find_skill(skill_id))
        results = []
        for cat, skill in targets:
            row = core.resolve_skill(skill, cat, runner, host)
            results.append({
                "skill": {"id": skill["id"], "name": skill["name"],
                          "category_id": cat["id"], "category": cat["name"],
                          "description": skill["description"]},
                "routing": routing_for(skill, cat, row, host, policy),
                "selftest_row": row,
            })
        result = {"host": host, "routings": results}
        maybe_append_evidence(
            args, assets, "route",
            {"skills": [r["skill"]["id"] for r in results],
             "statuses": [r["routing"]["status"] for r in results]},
            commands=["route --skill " + ",".join(args.skill or [])])
        return emit(core.ok("route", result), EXIT_OK, args.compact)

    max_matches = args.max if args.max else policy["limits"].get("route_max_matches", 5)
    cap = policy["limits"].get("route_max_matches_cap", 20)
    max_matches = max(1, min(int(max_matches), cap))
    hits = core.search_catalog(assets.catalog, args.job, limit=max_matches)
    matches = []
    for hit in hits:
        skill, cat = hit["skill"], hit["category"]
        row = core.resolve_skill(skill, cat, runner, host)
        matches.append({
            "score": hit["score"],
            "skill": {"id": skill["id"], "name": skill["name"],
                      "category_id": cat["id"], "category": cat["name"]},
            "routing": routing_for(skill, cat, row, host, policy),
        })
    warnings = []
    if not matches:
        warnings.append("no catalog capability matched the job text")
    result = {"host": host, "job": args.job, "match_count": len(matches),
              "matches": matches}
    maybe_append_evidence(
        args, assets, "route",
        {"job": args.job, "match_count": len(matches)},
        commands=[f"route --job {args.job!r}"])
    return emit(core.ok("route", result, warnings), EXIT_OK, args.compact)


def cmd_gate(args, assets):
    policy = assets.policy
    declarations = {
        "authorized": parse_bool(args.authorized),
        "owns_artifact": parse_bool(getattr(args, "owns_artifact", None)),
        "license_ok": parse_bool(getattr(args, "license_ok", None)),
        "privacy_ok": parse_bool(getattr(args, "privacy_ok", None)),
    }
    decision = core.gate_evaluate(args.action, declarations, policy)
    result = decision
    exit_code = EXIT_OK if decision["decision"] == "ALLOW" else EXIT_DENIAL
    maybe_append_evidence(
        args, assets, "gate",
        {"decision": decision["decision"], "reasons": decision["reasons"]},
        commands=[f"gate --action {args.action!r}"])
    envelope = core.ok("gate", result) if exit_code == EXIT_OK else core.fail(
        "gate",
        f"gate decision: {decision['decision']}",
        exit_code,
        {"decision": decision["decision"], "reasons": decision["reasons"],
         "missing_declarations": decision["missing_declarations"]},
    )
    return emit(envelope, exit_code, args.compact)


def cmd_risk(args, assets):
    policy = assets.policy
    classification = core.classify_action(args.action, policy)
    level = classification["approval_required"]
    granted, missing = core.approval_flags_present(
        level, args.approve, args.approver, args.reason)
    result = {
        "action": args.action,
        "classes": classification["classes"],
        "risk_class": classification["risk_class"],
        "rank": classification["rank"],
        "approval_required": level,
        "approval_granted": granted,
        "missing_approval": missing,
        "how_to_approve": policy["approval_flags"].get(level, ""),
        "satisfied": granted,
    }
    maybe_append_evidence(
        args, assets, "risk",
        {"risk_class": classification["risk_class"],
         "approval_required": level, "granted": granted},
        commands=[f"risk --action {args.action!r}"])
    if not granted:
        raise core.ToolError(
            f"approval level '{level}' required for risk class "
            f"'{classification['risk_class']}'",
            EXIT_APPROVAL,
            {"risk_class": classification["risk_class"],
             "approval_required": level,
             "missing": missing,
             "how_to_approve": policy["approval_flags"].get(level, "")},
        )
    return emit(core.ok("risk", result), EXIT_OK, args.compact)


def cmd_plan_change(args, assets):
    if bool(args.target) == bool(args.rollback):
        raise core.ToolError(
            "provide exactly one of --target (create plan) or --rollback (recover)",
            EXIT_USAGE,
            {"example": "plan-change --target file.cfg --approve",
             "rollback_example": "plan-change --rollback file.cfg.pcbak-....json "
                                 "--approve --approver NAME --reason TEXT"})
    if args.rollback:
        outcome = core.rollback_change_plan(
            args.rollback, assets.policy, assets.schema,
            approve=args.approve, approver=args.approver, reason=args.reason,
            evidence_path=args.evidence, actor=args.actor)
        result = {"mode": "rollback", **outcome}
        return emit(core.ok("plan-change", result), EXIT_OK, args.compact)

    outcome = core.create_change_plan(
        args.target, args.action, assets.policy, assets.schema,
        approve=args.approve, approver=args.approver, reason=args.reason,
        plan_out=args.plan_out, evidence_path=args.evidence,
        actor=args.actor)
    result = {"mode": "create", **outcome}
    return emit(core.ok("plan-change", result), EXIT_OK, args.compact)


def cmd_provenance(args, assets):
    if args.prov_command == "record":
        if args.skill:
            assets.find_skill(args.skill)  # validates id
        sources = []
        for src in (args.source or [])[:assets.policy["limits"].get(
                "provenance_max_sources", 32)]:
            sources.append(core.source_entry(src))
        commands = []
        for cmd_text in (args.commands or []):
            if len(cmd_text) > assets.policy["limits"].get(
                    "provenance_max_command_len", 2000):
                raise core.ToolError("command string exceeds max length", EXIT_USAGE)
            commands.append(cmd_text)
        record = core.make_evidence_record(
            args.event, core.host_info(), actor=args.actor,
            skill_id=args.skill, sources=sources, commands=commands,
            note=args.note)
        path = core.append_evidence(args.evidence, record, assets.schema)
        result = {"record": record, "evidence_path": path,
                  "line_appended": True}
        return emit(core.ok("provenance", result), EXIT_OK, args.compact)

    view = core.read_evidence(args.evidence, assets.schema,
                              last_n=args.last, validate=args.validate)
    if args.validate and not view["valid"]:
        raise core.ToolError("evidence validation failed", EXIT_FAILURE,
                             {"invalid_lines": view["invalid_lines"],
                              "validation_problems": view["validation_problems"]})
    return emit(core.ok("provenance", view), EXIT_OK, args.compact)


def cmd_decompose(args, assets):
    result = core.decompose_goal(args.goal)
    return emit(core.ok("decompose", result), EXIT_OK, args.compact)


def cmd_artifact_route(args, assets):
    result = core.detect_artifact(args.file)
    return emit(core.ok("artifact-route", result), EXIT_OK, args.compact)


def cmd_boundary(args, assets):
    policy = assets.policy
    check = core.boundary_check(args.request, policy)
    result = {"request": args.request, **check}
    maybe_append_evidence(
        args, assets, "boundary",
        {"allowed": check["allowed"],
         "matches": [m["id"] for m in check["matches"]]},
        commands=[f"boundary --request {args.request!r}"])
    if not check["allowed"]:
        raise core.ToolError(
            "request violates an operational boundary",
            EXIT_DENIAL,
            {"matches": check["matches"], "note": check["note"]},
        )
    return emit(core.ok("boundary", result), EXIT_OK, args.compact)


def cmd_clean_state(args, assets):
    policy = assets.policy
    if not args.approve:
        level = policy.get("execution_gates", {}).get("clean-state", "operator")
        raise core.ToolError(
            "clean-state executes commands and requires explicit approval",
            EXIT_APPROVAL,
            {"approval_required": level,
             "missing": ["--approve"],
             "how_to_approve": policy["approval_flags"].get(level, ""),
             "bounded": "no shell, argv capped, timeout enforced, temp dirs removed"})
    try:
        argv = json.loads(args.argv)
    except json.JSONDecodeError as exc:
        raise core.ToolError(f"--argv is not valid JSON: {exc}", EXIT_USAGE,
                             {"example": "--argv '[\"python3\",\"-c\",\"print(1)\"]'"})
    result = core.run_clean_state(argv, args.repeat, policy)
    maybe_append_evidence(
        args, assets, "clean-state",
        {"hidden_state": result["hidden_state"],
         "repeats": result["repeats"]},
        commands=[" ".join(argv)])
    return emit(core.ok("clean-state", result), EXIT_OK, args.compact)


def cmd_version_policy(args, assets):
    runner = core.ProbeRunner(assets)
    pins = assets.policy.get("version_pins", {})
    selected = args.tool or sorted(pins)
    unknown = [t for t in selected if t not in pins and t not in runner.probes]
    if unknown:
        raise core.ToolError("unknown tool for version policy", EXIT_USAGE,
                             {"unknown": unknown,
                              "known_tools": sorted(pins)[:10]})
    rows = []
    for tool in selected:
        pin = pins.get(tool)
        run = runner.run(tool)
        if run["status"] == "MISSING":
            pin_status = "missing"
            action = f"install {tool}" + (f" >= {pin}" if pin else "")
        elif run["status"] == "WRONG VERSION":
            pin_status = "unsupported"
            action = (f"upgrade {tool} to >= {pin} or update the pin in "
                      "asset-policy-pack.json")
        elif run["status"] == "BROKEN":
            pin_status = "broken"
            action = f"repair {tool} installation"
        elif run.get("detected_version") and pin:
            pin_status = ("supported" if core.version_at_least(
                run["detected_version"], pin) else "unsupported")
            action = "none required" if pin_status == "supported" else (
                f"upgrade {tool} to >= {pin}")
        else:
            pin_status = "unversioned"
            action = (f"verify {tool} version manually"
                      if pin else "none required")
        row = {
            "capability_id": f"tool:{tool}",
            "status": run["status"],
            "detected_command": run.get("detected_command"),
            "detected_version": run.get("detected_version"),
            "host_type": core.host_info()["host_type"],
            "architecture": core.host_info()["architecture"],
            "requirements": ([f"pin:>= {pin}"] if pin else []),
            "test_command": run.get("test_command"),
            "exit_code": run.get("exit_code"),
            "evidence_path": None,
            "recommended_action": action,
            "pin": pin,
            "pin_status": pin_status,
        }
        rows.append(row)

    combinations = []
    for combo in assets.policy.get("capability_combinations", []):
        present = [t for t in combo.get("requires_any", [])
                   if runner.run(t)["status"] == "FOUND"]
        combinations.append({
            "id": combo["id"],
            "reason": combo.get("reason", ""),
            "requires_any": combo.get("requires_any", []),
            "present": present,
            "status": "available" if present else "unavailable",
        })

    summary = {
        "tools_checked": len(rows),
        "supported": sum(1 for r in rows if r["pin_status"] == "supported"),
        "unsupported": sum(1 for r in rows if r["pin_status"] == "unsupported"),
        "missing": sum(1 for r in rows if r["pin_status"] == "missing"),
        "combinations_available": sum(1 for c in combinations
                                      if c["status"] == "available"),
        "combinations_total": len(combinations),
    }
    result = {"rows": rows, "combinations": combinations, "summary": summary}
    maybe_append_evidence(args, assets, "version-policy", summary,
                          commands=["version-policy"])
    return emit(core.ok("version-policy", result), EXIT_OK, args.compact)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "help": cmd_help,
    "capabilities": cmd_capabilities,
    "inventory": cmd_inventory,
    "selftest": cmd_selftest,
    "route": cmd_route,
    "gate": cmd_gate,
    "risk": cmd_risk,
    "plan-change": cmd_plan_change,
    "provenance": cmd_provenance,
    "decompose": cmd_decompose,
    "artifact-route": cmd_artifact_route,
    "boundary": cmd_boundary,
    "clean-state": cmd_clean_state,
    "version-policy": cmd_version_policy,
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        print(HELP_TEXT)
        return EXIT_USAGE
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: 0 for --help, 2 for usage errors
        code = int(exc.code or 0)
        if code == 0:
            return EXIT_OK
        envelope = core.fail(
            argv[0] if argv else "usage",
            "usage error; run 'help' or --help for usage",
            EXIT_USAGE,
            {"argv": argv, "argparse_exit": code},
        )
        return emit(envelope, EXIT_USAGE, False)
    if not args.command:
        print(HELP_TEXT)
        return EXIT_USAGE
    compact = getattr(args, "compact", False)
    debug = getattr(args, "debug", False)
    try:
        core.install_cancel_handler()
        handler = COMMANDS[args.command]
        if args.command == "help":
            return handler(args)
        assets = load_assets(args)
        return handler(args, assets)
    except core.ToolError as exc:
        envelope = core.fail(args.command, exc.message, exc.exit_code, exc.details)
        return emit(envelope, exc.exit_code, compact)
    except KeyboardInterrupt:
        core.cleanup_temps()
        envelope = core.fail(args.command, "cancelled by user", EXIT_CANCEL, {})
        return emit(envelope, EXIT_CANCEL, compact)
    except Exception as exc:  # noqa: BLE001 — structured failure boundary
        if debug:
            traceback.print_exc()
        envelope = core.fail(
            args.command, f"internal error: {exc.__class__.__name__}: {exc}",
            1, {"type": exc.__class__.__name__})
        return emit(envelope, 1, compact)


if __name__ == "__main__":
    sys.exit(main())
