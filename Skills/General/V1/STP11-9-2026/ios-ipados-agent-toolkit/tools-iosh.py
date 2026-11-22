#!/usr/bin/env python3
"""tools-iosh.py -- primary entry point for the iOS/iPadOS Agent Skill Tool.

Capability surface (see `capabilities`):
  probe            Linux host inventory
  route            platform/authorization routing decision
  plan             validated execution plan for any catalog capability
  gate             evidence-based completion gate
  workspace        isolated inspection workspace with preserved originals
  paths            cross-host path normalisation
  pins             record / compare tool version pins
  cache            offline artifact cache with Apple-license guard
  license-scan     Apple / commercial license compliance gate
  secrets-scan     accidental credential exposure scan
  health           startup self-tests and status aggregation
  matrix           Simulator / device test matrix planner
  detect           artifact type auto-detection (despite extensions)
  inspect-ipa      IPA / app bundle structure report
  inspect-plist    property-list audit, conversion and privacy strings
  inspect-project  Xcode project, scheme, xcconfig inspection
  profile          provisioning profile decode and expiry monitoring
  inspect-binary   Mach-O, entitlements, dylibs, Objective-C/Swift metadata
  privacy          privacy manifest and required-reason API checks
  signing-readiness  signing/identity/profile/bundle consistency gate
  crash-triage     crash report parse, triage and dSYM matching
  dsym-index       dSYM storage index by UUID
  diagnose         log/diagnostic text classification
  migration-scan   source migration readiness scan
  export-options   ExportOptions.plist planner
  merge-results    merge sharded test result JSON
  snapshot         hash an artifact tree and compare with a baseline (reproducibility audit)
  binary-diff      compare two authorized Mach-O builds
  report           evidence packager + JSON report validation
  validate-report  schema check of a generated report
  capabilities     machine-readable capability list
  help             built-in help

Run `python3 tools-iosh.py help` for the full option reference.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
def _bootstrap_modules() -> None:
    """Load the sibling tools-* modules.

    The required ``tools-`` filename prefix cannot be a Python identifier, so the
    modules are loaded from their exact filenames and registered under importable
    aliases (tools_iosh_core, tools_iosh_apple, tools_iosh_ops) in the documented
    load order.
    """
    import importlib.util
    for filename, module_name in (
        ("tools-iosh_core.py", "tools_iosh_core"),
        ("tools-iosh_apple.py", "tools_iosh_apple"),
        ("tools-iosh_ops.py", "tools_iosh_ops"),
    ):
        if module_name in sys.modules:
            continue
        path = HERE / filename
        if not path.exists():
            raise SystemExit(f"missing required Skill Tool module: {path.as_posix()}")
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)


_bootstrap_modules()

import tools_iosh_core as core
import tools_iosh_apple as apple
import tools_iosh_ops as ops

from tools_iosh_core import (ACCOUNT_REQUIRED, BROKEN, DEVICE_REQUIRED, FOUND, LICENSE_REQUIRED,
                             MACOS_REQUIRED, MISSING, NETWORK_REQUIRED, OK, SKIPPED, WARN,
                             ApprovalRequired, CommandFailure, InputError, LimitError, Limits,
                             PolicyError, Report, ToolError, UsageError)

CATALOG_PATH = HERE / "asset-capability-catalog.json"
POLICY_PATH = HERE / "asset-policy.json"
SCHEMA_DIR = HERE / "asset-report.schema.json"
EXPORT_TEMPLATE = HERE / "asset-export-options.template.plist"
MATRIX_TEMPLATE = HERE / "asset-test-matrix.json"
# fixture files live flat beside the tool (asset-fixture-*); IOSH_FIXTURES_DIR can point elsewhere
FIXTURES = Path(os.environ.get("IOSH_FIXTURES_DIR") or HERE)

CAPABILITY_COUNT_NOTE = ("354 capabilities from the 29-category iOS/iPadOS Agent Skill "
                         "Catalog are reachable through this CLI")


# ==========================================================================
# catalog access
# ==========================================================================
def load_catalog() -> Dict[str, Any]:
    if not CATALOG_PATH.exists():
        raise ToolError(f"capability catalog missing: {CATALOG_PATH.name}",
                        code="catalog-missing",
                        remediation="restore asset-capability-catalog.json beside the tool")
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def find_capability(catalog: Dict[str, Any], needle: str) -> List[Dict[str, Any]]:
    n = needle.strip().lower()
    hits = []
    for cat in catalog["categories"]:
        for s in cat["skills"]:
            hay = f"{s['id']} {s['name']} {s['slug']} {s['description']} {cat['name']}".lower()
            if n in hay:
                hits.append({**s, "category_number": cat["number"], "category_name": cat["name"],
                             "category_purpose": cat["purpose"]})
    return hits


# ==========================================================================
# command implementations
# ==========================================================================
def cmd_capabilities(args, limits: Limits) -> Report:
    r = Report("capabilities", limits)
    cat = load_catalog()
    if args.category:
        cats = [c for c in cat["categories"] if str(c["number"]) == str(args.category)]
        if not cats:
            raise InputError(f"no category numbered {args.category} (1-29)", code="category-not-found")
    else:
        cats = cat["categories"]
    rows = []
    for c in cats:
        for s in c["skills"]:
            if args.implemented_only and s["implementation"] not in ("native",):
                continue
            rows.append({
                "id": s["id"], "category": c["number"], "category_name": c["name"],
                "name": s["name"], "slug": s["slug"], "implementation": s["implementation"],
                "command": s["command"], "requires": s["requires"],
                "approval": [a for a in s.get("approval_reasons", [])],
                "description": s["description"],
            })
    r.data = {
        "summary": {
            "catalog": cat["catalog_title"],
            "categories": len(cats),
            "capabilities_listed": len(rows),
            "catalog_total": cat["capability_count"],
            "note": CAPABILITY_COUNT_NOTE,
        },
        "commands": sorted({row["command"].split()[0] for row in rows
                            if row["implementation"] == "native"}),
        "capabilities": rows,
    }
    if args.json_only:
        return r
    r.add("capabilities/loaded", "info",
          f"{len(rows)} capability entries loaded from {cat['category_count']} catalog categories")
    return r


def cmd_probe(args, limits: Limits) -> Report:
    r = Report("probe", limits, {"paths": args.paths})
    inv = ops.host_inventory(limits)
    for group, entries in inv["groups"].items():
        present = [k for k, v in entries.items() if v["path"]]
        r.add(f"probe/{group}", "info" if present else "warn",
              f"{group}: {len(present)}/{len(entries)} available"
              + (f" ({', '.join(sorted(present))})" if present else ""))
    if not inv["host"]["is_x86_64"]:
        r.add("probe/arch", "warn", f"host is {inv['host']['machine']}, catalog targets Linux x86_64")
    if inv["disk"]["free_bytes"] < 2 << 30:
        r.add("probe/disk", "warn", "less than 2 GiB free; large artifact work may fail")
    for p in args.paths:
        path = Path(p)
        if path.exists():
            r.data.setdefault("paths", {})[p] = {"exists": True,
                                                 "kind": apple.sniff_kind(path)}
        else:
            r.add("probe/path-missing", "warn", f"declared path does not exist: {p}", path=p)
    r.data["summary"] = {
        "host": inv["host"]["system"] + " " + inv["host"]["machine"],
        "python": inv["host"]["python"],
        "macos": inv["host"]["is_macos"],
        "groups_available": sum(1 for g in inv["groups"].values() for v in g.values() if v["path"]),
        "groups_total": sum(len(g) for g in inv["groups"].values()),
        "usb_devices_visible": inv["usb"]["count"],
        "disk_free": core.human_bytes(inv["disk"]["free_bytes"]),
    }
    r.data["inventory"] = inv
    r.next_actions.append("run 'health' to separate MISSING tools from DEVICE/MACOS REQUIRED ones")
    return r


def cmd_route(args, limits: Limits) -> Report:
    r = Report("route", limits, {"action": args.action})
    decision = core.classify_request(args.action, core.load_policy(args.policy),
                                     have_macos=args.have_macos, have_device=args.have_device,
                                     have_account=args.have_account, network=args.network)
    r.data = {"decision": decision.as_dict()}
    r.data["summary"] = {"classification": decision.classification, "status": decision.status,
                         "approval_gates": ", ".join(a["id"] for a in decision.approvals) or "none",
                         "denied": ", ".join(d["id"] for d in decision.denied) or "none"}
    level = "info" if decision.accepted and not decision.approvals else "warn"
    r.add("route/classified", level,
          f"'{args.action[:80]}' classified {decision.classification} -> {decision.status}")
    for d in decision.denied:
        r.add(f"route/denied-{d['id']}", "error", d["reason"])
    for a in decision.approvals:
        r.add(f"route/approval-{a['id']}", "warn",
              f"needs approval ({a['reason']}); matched: {a['matched']}",
              remediation=f"add --approve {a['id']} once the side effect is accepted")
    if decision.denied:
        raise PolicyError("request refused by the skill policy", details={"denied": decision.denied},
                          remediation="the toolkit never bypasses ownership, passcode, DRM, purchase "
                                      "or credential controls")
    return r


def cmd_plan(args, limits: Limits) -> Report:
    cat = load_catalog()
    policy = core.load_policy(args.policy)
    if args.capability:
        hits = find_capability(cat, args.capability)
        if not hits:
            raise InputError(f"no capability matches '{args.capability}'", code="capability-not-found",
                             remediation="run 'capabilities' to list every id, name and slug")
        if len(hits) > 1 and not any(h["id"] == args.capability.lower() for h in hits):
            names = ", ".join(f"{h['id']}={h['name']}" for h in hits[:6])
            raise InputError(f"'{args.capability}' matches {len(hits)} capabilities: {names}",
                             code="capability-ambiguous",
                             remediation="use the capability id, e.g. --capability c09-01")
        selected = [h for h in hits if h["id"] == args.capability.lower()] or hits[:1]
    else:
        cats = [c for c in cat["categories"] if str(c["number"]) == str(args.category)] \
            if args.category else cat["categories"]
        if args.category and not cats:
            raise InputError(f"no category numbered {args.category}", code="category-not-found")
        selected = [{**s, "category_number": c["number"], "category_name": c["name"]}
                    for c in cats for s in c["skills"]]

    r = Report("plan", limits, {"category": args.category, "capability": args.capability})
    steps = ops.build_plan(cat, selected, limits, policy, target=args.target,
                           have_macos=args.have_macos, have_device=args.have_device,
                           have_account=args.have_account, network=args.network,
                           approvals=args.approve)
    r.data = steps
    s = steps["summary"]
    r.add("plan/built", "info",
          f"plan {steps['plan_id']}: {s['steps']} steps, {s['native_steps']} runnable here, "
          f"{s['routed_steps']} routed to macOS/device/account/licensed hosts")
    for warn in steps["warnings"]:
        r.add("plan/warning", "warn", warn)
    if steps["approval_required"]:
        for a in steps["approval_required"]:
            r.add(f"plan/approval-{a['id']}", "warn", f"step needs approval: {a['reason']}")
    if args.write:
        out = core.ensure_output_path(args.write)
        core.write_json(out, steps)
        r.data["written_to"] = out.as_posix()
    if args.execute:
        if args.dry_run:
            r.add("plan/execute", "warn", "--execute combined with --dry-run: nothing was executed")
        else:
            exec_report = ops.execute_plan(steps, limits, approvals=args.approve)
            r.data["execution"] = exec_report
            for res in exec_report["results"]:
                lvl = "info" if res["status"] in (OK, SKIPPED) else \
                      "error" if res["status"] in (core.FAIL, BROKEN) else "warn"
                r.add("plan/step", lvl, f"{res['step_id']} {res['capability']}: {res['status']}"
                                        + (f" ({res.get('detail','')[:160]})" if res.get("detail") else ""))
    r.next_actions.append("re-run with --write plan.json to keep the plan as evidence")
    return r


def cmd_gate(args, limits: Limits) -> Report:
    r = Report("gate", limits, {"files": args.evidence, "claim": args.claim})
    result = ops.completion_gate(args.claim, args.evidence, args.required, limits)
    r.data = result
    r.data["summary"] = {
        "claim": args.claim[:70] if args.claim else "(not stated)",
        "verdict": result["verdict"],
        "checks_passed": f"{sum(1 for c in result['checks'] if c['ok'])}/{len(result['checks'])}",
    }
    for c in result["checks"]:
        r.add(f"gate/{c['name']}", "info" if c["ok"] else "error",
              f"{c['name']}: {'ok' if c['ok'] else 'not satisfied'} -- {c['detail']}")
    if result["verdict"] != "PASS":
        r.add("gate/blocked", "error",
              "completion must not be claimed: open the artifact, parse it, and re-run with evidence",
              remediation="pass --evidence <file> for every artifact named in the claim")
    return r


def cmd_workspace(args, limits: Limits) -> Report:
    r = Report("workspace", limits, {"name": args.name, "into": args.into})
    ws = ops.create_workspace(args.name, args.inputs, limits, root=args.into, clean=args.clean)
    r.data = {"workspace": ws}
    r.data["summary"] = {"workspace": ws["root"], "inputs": len(ws["inputs"]),
                         "originals_preserved": ws["originals_preserved"],
                         "manifest": ws["manifest"]}
    for i in ws["inputs"]:
        r.add("workspace/copied", "info", f"{i['role']} {i['name']} ({core.human_bytes(i['bytes'])}) "
                                          f"sha256={i['sha256'][:12]}", path=i["destination"])
    for note in ws["notes"]:
        r.add("workspace/note", "warn", note)
    r.next_actions.append(f"point later commands at the copied inputs under {ws['root']}")
    return r


def cmd_paths(args, limits: Limits) -> Report:
    r = Report("paths", limits)
    result = ops.normalise_paths(args.paths, args.to_scheme)
    r.data = {"mapping": result}
    r.data["summary"] = {"paths_mapped": len(result["map"]),
                         "target_scheme": result["target_scheme"],
                         "conflicts": len(result["conflicts"])}
    for src, dst in result["map"].items():
        r.add("paths/mapped", "info", f"{src} -> {dst}")
    for c in result["conflicts"]:
        r.add("paths/conflict", "warn", c)
    return r


def cmd_pins(args, limits: Limits) -> Report:
    r = Report("pins", limits, {"action": args.pins_action})
    state = core.state_dir(args.state_dir)
    pins_file = state / "tool-pins.json"
    if args.pins_action == "record":
        if not args.tools:
            raise UsageError("pins record needs --tool (repeatable)", code="missing-argument")
        rec = ops.record_pins(args.tools, limits, pins_file)
        r.data = {"pins": rec}
        r.data["summary"] = {"recorded": len(rec["tools"]), "file": rec["file"],
                             "host": rec["host"]["system"]}
        for t in rec["tools"]:
            r.add("pins/recorded", "info", f"{t['name']}: {t['status']} {t.get('version','')}".strip(),
                  path=t.get("path") or "")
    else:
        cmp = ops.compare_pins(pins_file, limits)
        r.data = {"comparison": cmp}
        r.data["summary"] = {"baseline": cmp["baseline_file"], "drift": cmp["drift"],
                             "unchanged": cmp["unchanged"], "missing": cmp["missing"]}
        for item in cmp["items"]:
            if item["verdict"] != "unchanged":
                r.add(f"pins/drift-{item['verdict']}", "warn" if item["verdict"] == "missing" else "error",
                      f"{item['name']}: pinned '{item['pinned']}' now '{item['current'] or 'absent'}'")
    return r


def cmd_cache(args, limits: Limits) -> Report:
    r = Report("cache", limits, {"action": args.cache_action})
    result = ops.cache_manage(args.cache_action, args.cache_dir, args.add, args.name, limits,
                              args.check, policy=core.load_policy(args.policy))
    r.data = result
    r.data["summary"] = result.get("summary", {})
    for note in result["notes"]:
        r.add("cache/note", "info", note)
    for bad in result["clipped"]:
        r.add("cache/apple-component-blocked", "error", bad)
    return r


def cmd_license_scan(args, limits: Limits) -> Report:
    r = Report("license-scan", limits, {"roots": args.paths})
    result = ops.license_scan(args.paths, limits, policy=core.load_policy(args.policy))
    r.data = result
    r.data["summary"] = {"files_scanned": result["files_scanned"],
                         "apple_findings": len(result["apple"]),
                         "commercial_findings": len(result["commercial"])}
    for hit in result["apple"]:
        r.add("license/apple-component", "error",
              f"Apple-licensed component detected ({hit['marker']}); it must not be copied into a "
              "Linux image or redistributed", path=hit["path"])
    for hit in result["commercial"]:
        r.add("license/commercial-tool", "warn",
              f"commercial tool artifact ({hit['marker']}) requires a licence on the host",
              path=hit["path"])
    if not result["apple"] and not result["commercial"]:
        r.add("license/clean", "info", "no Apple-licensed or commercial tool payloads detected")
    return r


def cmd_secrets_scan(args, limits: Limits) -> Report:
    r = Report("secrets-scan", limits, {"roots": args.paths})
    result = ops.secrets_scan(args.paths, limits)
    r.data = result
    r.data["summary"] = {"files_scanned": result["files_scanned"],
                         "findings": len(result["findings"]),
                         "skipped_binary": result["skipped_binary"]}
    for f in result["findings"]:
        r.add(f"secrets/{f['kind']}", "error" if f["severity"] == "high" else "warn",
              f"{f['kind']} exposure: {f['preview']}",
              path=f["path"], remediation=f["remediation"])
    if not result["findings"]:
        r.add("secrets/clean", "info", "no credential-like material found in the scanned roots")
    return r


def cmd_health(args, limits: Limits) -> Report:
    r = Report("health", limits, {"macos_probe": args.macos_probe})
    health = ops.health_report(limits, core.load_policy(args.policy),
                               macos_probe=args.macos_probe, tools=args.tool,
                               include_fixtures=not args.no_fixtures, fixtures_dir=FIXTURES)
    r.data = health
    s = health["summary"]
    r.data["summary"] = {"tools_found": f"{s['found']}/{s['checked']}",
                         "missing_but_required_elsewhere": s["required_elsewhere"],
                         "fixture_checks": f"{s['fixtures_passed']}/{s['fixtures_run']}",
                         "macos_worker": s["macos_worker"]}
    for row in health["tools"]:
        lvl = "info" if row["status"] == FOUND else (
            "warn" if row["status"] in (DEVICE_REQUIRED, MACOS_REQUIRED, LICENSE_REQUIRED) else "error")
        r.add(f"health/{row['name']}", lvl,
              f"{row['name']}: {row['status']}" + (f" -- {row['version'][:90]}" if row['version'] else ""),
              remediation=row.get("remediation", ""))
    for fx in health["fixtures"]:
        r.add(f"health/fixture-{fx['name']}", "info" if fx["ok"] else "error",
              f"fixture {fx['name']}: {'pass' if fx['ok'] else 'FAIL'} -- {fx['detail']}")
    if health["macos_probe"]:
        mp = health["macos_probe"]
        for key, value in mp["checks"].items():
            r.add(f"health/macos-{key}", "info" if value["ok"] else "error",
                  f"macOS worker {key}: {value['detail']}")
    if not args.no_fixtures and s["fixtures_failed"]:
        r.add("health/fixtures-failed", "error",
              f"{s['fixtures_failed']} fixture checks failed; treat dependent capabilities as BROKEN")
    return r


def cmd_matrix(args, limits: Limits) -> Report:
    r = Report("matrix", limits, {"template": MATRIX_TEMPLATE.name})
    try:
        plan = ops.build_matrix(limits, template=MATRIX_TEMPLATE, retries=args.lane_retries,
                               clean_state=args.clean_state, runtimes=args.runtimes,
                               include_device=args.include_device)
    except FileNotFoundError as exc:
        raise ToolError(f"matrix template missing: {exc}", code="asset-missing") from exc
    r.data = plan
    r.data["summary"] = {"lanes": len(plan["lanes"]),
                         "simulator_lanes": sum(1 for l in plan["lanes"] if l["environment"] == "simulator"),
                         "device_lanes": sum(1 for l in plan["lanes"] if l["environment"] == "device"),
                         "retries": plan["policy"]["retries"],
                         "note": "Simulator lanes need MACOS HOST REQUIRED; device lanes need an owned device"}
    for lane in plan["lanes"]:
        r.add("matrix/lane", "info",
              f"{lane['id']}: {lane['environment']} {lane['device_hint']} / {lane['runtime_hint']} "
              f"focus={lane['focus']}")
    if args.write:
        out = core.ensure_output_path(args.write)
        core.write_json(out, plan)
        r.data["written_to"] = out.as_posix()
    return r


SYNONYMS = {
    "macho": {"macho-dylib", "macho-fat", "binary"},
    "macho-dylib": {"macho", "macho-fat", "binary"},
    "macho-fat": {"macho", "macho-dylib", "binary"},
    "plist": {"xml", "privacy-manifest"},
    "xml": {"plist", "xcconfig"},
    "ipa": {"zip"},
    "zip": {"ipa"},
    "xcarchive": {"zip"},
    "crash-report": {"log", "ips"},
    "dsym": {"directory"},
}


def cmd_detect(args, limits: Limits) -> Report:
    r = Report("detect", limits, {"roots": args.paths})
    results = []
    for root in args.paths:
        p = Path(root)
        if not p.exists():
            raise InputError(f"path not found: {root}", code="input-missing")
        walk = [p] if p.is_file() else [f for f in sorted(p.rglob("*")) if f.is_file()
                                        or f.suffix in (".app", ".xcarchive", ".dSYM", ".framework",
                                                        ".xcframework", ".xcresult")]
        for f in walk[: limits.max_entries]:
            if f.is_file() and f.stat().st_size == 0:
                r.add("detect/empty-file", "info", f"{f.name} is empty (0 bytes): it is a placeholder or "
                                                   f"a failed export, not an artifact",
                      path=f.as_posix())
                results.append({"path": f.as_posix(), "kind": "empty", "by": "size", "magic": None,
                                "extension": f.suffix, "size": 0})
                continue
            try:
                info = apple.sniff_kind(f)
            except OSError as exc:
                r.add("detect/unreadable", "warn", f"{f.name}: {exc}", path=f.as_posix())
                continue
            info["path"] = f.as_posix()
            info["size"] = f.stat().st_size if f.is_file() else None
            results.append(info)
            declared = f.suffix.lower()
            declared_kind = core.EXT_HINTS.get(declared)
            synonyms = {info["kind"], *SYNONYMS.get(info["kind"], set())}
            if declared_kind and info["kind"] != "unknown" and declared_kind not in synonyms:
                r.add("detect/mismatch", "warn",
                      f"extension '{declared}' suggests {declared_kind} but content is {info['kind']}; "
                      f"the extension is misleading", path=f.as_posix())
    r.data = {"artifacts": results}
    kinds: Dict[str, int] = {}
    for i in results:
        kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
    r.data["summary"] = {"artifacts": len(results),
                         "kinds": ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()))}
    r.add("detect/done", "info", f"{len(results)} artifacts classified; "
                                 f"{len(kinds)} distinct kinds")
    return r


def cmd_inspect_ipa(args, limits: Limits) -> Report:
    p = core.ensure_readable_file(args.ipa, limits, what="IPA/artifact")
    r = Report("inspect-ipa", limits, {"ipa": p.name, "sha256": core.sha256_file(p)})
    if not zipfile.is_zipfile(p):
        raise InputError("input is not a ZIP-based IPA or app archive", code="ipa-not-zip",
                         remediation="use 'detect' to identify the artifact first")
    data = apple.inspect_ipa(p, limits)
    r.data = data
    b = data.get("bundle", {})
    r.data["summary"] = {
        "bundle_id": b.get("bundle_id", "(unknown)"),
        "version": f"{b.get('short_version','?')} ({b.get('build_version','?')})",
        "minimum_os": b.get("minimum_os") or data.get("minimum_os") or "?",
        "architectures": ", ".join(data.get("architectures", [])) or "?",
        "device_family": b.get("device_family", []),
        "extensions": len(data.get("extensions", [])),
        "frameworks": len(data.get("frameworks", [])),
        "encrypted": data.get("encrypted"),
        "zip_healthy": data["container"]["zip"]["healthy"],
    }
    for f in data["container"]["zip"]["issues"]:
        r.add("ipa/zip-" + f["code"].split("/", 1)[-1], f["level"], f["message"], path=f.get("entry", ""))
    for code, level, msg in data.get("findings", []):
        r.add(code, level, msg)
    if data.get("extensions"):
        for e in data.get("extension_details", []):
            r.add("ipa/extension", "info",
                  f"{e['bundle']} point={e['extension_point'] or '?'} "
                  f"principal={e['principal_class'] or '?'}", path=e["bundle"])
    for fw in data.get("framework_details", []):
        lvl = "warn" if fw.get("error") or fw.get("encrypted") else "info"
        r.add("ipa/framework", lvl,
              f"{fw['bundle']} arch={','.join(fw.get('architectures', []) or []) or '?'} "
              f"min_os={fw.get('min_os') or '?'}" + (f" error={fw['error']}" if fw.get("error") else ""),
              path=fw["bundle"])
    if data.get("privacy_manifests"):
        r.add("ipa/privacy-manifest", "info",
              f"{len(data['privacy_manifests'])} PrivacyInfo.xcprivacy file(s) present")
    r.next_actions.append("run 'profile' on the embedded.mobileprovision and 'inspect-plist' on Info.plist "
                          "for entitlement-level detail")
    return r


def cmd_inspect_plist(args, limits: Limits) -> Report:
    p = core.ensure_readable_file(args.plist, limits, what="property list")
    r = Report("inspect-plist", limits, {"plist": p.name})
    parsed = apple.load_plist_file(p, limits)
    r.data = {"plist": {"format": parsed["format"], "sha256": parsed["sha256"],
                        "keys": sorted(parsed["value"].keys())[:200],
                        "value": apple._jsonable(parsed["value"])}}
    audit = ops.audit_info_plist(parsed["value"], path=p.name, privacy=args.privacy)
    r.data["audit"] = audit
    r.data["summary"] = {"format": parsed["format"], "keys": len(parsed["value"]),
                         "bundle_id": parsed["value"].get("CFBundleIdentifier", "(not an Info.plist)")
                         if isinstance(parsed["value"], dict) else "?",
                         "issues": len(audit["issues"])}
    for issue in audit["issues"]:
        r.add(issue["code"], issue["level"], issue["message"], remediation=issue.get("remediation", ""))
    if audit.get("privacy_strings"):
        strings = audit["privacy_strings"]
        r.data["privacy_strings"] = strings
        declared = [x for x in strings if x["verdict"] in ("present", "empty", "vague", "placeholder")]
        for entry in declared:
            if entry["verdict"] != "present":
                r.add("plist/privacy-string", "error",
                      f"{entry['key']}: {entry['verdict']}"
                      + (f" ({entry['note']})" if entry.get("note") else ""),
                      remediation="write a specific, user-facing purpose string")
        missing = [x for x in strings if x["verdict"] == "omit-unless-used"]
        r.add("plist/privacy-coverage", "info",
              f"{sum(1 for x in declared if x['verdict'] == 'present')} valid purpose string(s); "
              f"{len(missing)} API category/ies need a string only if the code links them: "
              + ", ".join(x["key"] for x in missing[:8]))
        for entry in strings:
            if entry["verdict"] == "not-applicable":
                r.add("plist/privacy-key-name", "warn",
                      f"{entry['key']}: {entry['note']}",
                      remediation="rename the key to NSUserTrackingUsageDescription")
    if args.convert:
        out = core.ensure_output_path(args.out, default=Path(f"{p.stem}.converted."
                                                            f"{'plist' if args.convert=='binary' else 'xml.plist'}"))
        out.write_bytes(apple.convert_plist(p.read_bytes(), args.convert))
        # verify round-trip
        back = apple.parse_plist_bytes(out.read_bytes())["value"]
        same = back == apple.parse_plist_bytes(p.read_bytes())["value"]
        r.data["converted"] = {"path": out.as_posix(), "target": args.convert,
                               "round_trip_equal": same,
                               "sha256": core.sha256_file(out)}
        r.add("plist/converted", "info" if same else "error",
              f"wrote {args.convert} plist to {out.as_posix()} (round-trip "
              f"{'preserved all values' if same else 'CHANGED values'})")
    return r


def cmd_inspect_project(args, limits: Limits) -> Report:
    r = Report("inspect-project", limits, {"project": args.project})
    result = ops.inspect_project(args.project, limits, workspace=args.workspace,
                                 diff_configs=args.diff_configs, xcconfig=args.xcconfig,
                                 destinations=args.destinations)
    r.data = result
    s = result["summary"]
    r.data["summary"] = s
    for obj in result["targets"]:
        r.add("project/target", "info",
              f"target {obj['name']} type={obj['product_type']} files={obj['sources']} "
              f"configs={len(obj['configurations'])}")
    for issue in result["issues"]:
        r.add(issue["code"], issue["level"], issue["message"], path=issue.get("path", ""),
              remediation=issue.get("remediation", ""))
    if result.get("schemes"):
        r.add("project/schemes", "info" if result["schemes"]["shared"] else "warn",
              f"{len(result['schemes']['shared'])} shared schemes, "
              f"{len(result['schemes']['user'])} user schemes")
    if result.get("xcconfig"):
        # resolved values and xcconfig issues are already reported through result["issues"]
        r.add("project/xcconfig-summary", "info",
              f"{len(result['xcconfig']['resolved'])} resolved xcconfig value(s), "
              f"{len(result['xcconfig']['issues'])} issue(s)")
    if result.get("destinations"):
        for d in result["destinations"]:
            r.add("project/destination", "info", f"{d['kind']}: {d['detail']}")
    return r


def cmd_profile(args, limits: Limits) -> Report:
    p = core.ensure_readable_file(args.profile, limits, what="provisioning profile")
    r = Report("profile", limits, {"profile": p.name})
    data = apple.parse_mobileprovision(p, limits)
    r.data = {"profile": data}
    s = data["summary"]
    r.data["summary"] = {"uuid": s["uuid"], "name": s["name"], "team": s["team_name"],
                         "method": s["distribution_method"],
                         "devices": s["device_count"],
                         "expires": s["expiration_date"],
                         "days_until_expiry": s.get("days_until_expiry")}
    r.add("profile/decoded", "info",
          f"{s['name'] or s['uuid']} ({s['distribution_method']}) app_id={s['application_identifier']} "
          f"team={s['team_name']}")
    days = s.get("days_until_expiry")
    horizon = args.expiry_days
    if isinstance(days, int):
        if days < 0:
            r.add("profile/expired", "error", f"profile expired {-days} day(s) ago",
                  remediation="regenerate the profile before building or installing")
        elif days <= 7:
            r.add("profile/expiring", "error", f"profile expires in {days} day(s)")
        elif days <= horizon:
            r.add("profile/expiring-soon", "warn", f"profile expires in {days} day(s) (horizon {horizon})")
    if s["get_task_allow"]:
        r.add("profile/development-entitlement", "warn",
              "get-task-allow is true: this profile is development-only and cannot ship to the App Store")
    if args.diff_entitlements:
        other = core.ensure_readable_file(args.diff_entitlements, limits, what="comparison profile")
        diff = ops.diff_entitlements(s["entitlement_keys"], s, apple.parse_mobileprovision(other, limits))
        r.data["entitlement_diff"] = diff
        for k, v in diff["missing_in_second"].items():
            r.add("profile/entitlement-missing", "error", f"{k} allowed by the first profile only: {v}")
        for k, v in diff["extra_in_second"].items():
            r.add("profile/entitlement-extra", "warn", f"{k} present in the second profile only: {v}")
    r.next_actions.append("compare the profile's allowed entitlements with the signed app using "
                          "inspect-binary --entitlements")
    return r


def cmd_inspect_binary(args, limits: Limits) -> Report:
    p = Path(args.binary)
    if not p.exists():
        raise InputError(f"binary not found: {args.binary}", code="input-missing")
    targets: List[Path] = []
    if p.is_dir():
        for f in sorted(p.rglob("*")):
            if f.is_file() and f.suffix in ("", ".dylib") and f.stat().st_size > 4096:
                with f.open("rb") as fh:
                    if fh.read(4) in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
                        targets.append(f)
            if len(targets) >= args.max_binaries:
                break
    else:
        targets = [p]
    if not targets:
        raise InputError("no Mach-O files found in the given path", code="macho-not-found")

    r = Report("inspect-binary", limits, {"binary": p.as_posix(), "binaries": len(targets)})
    results = []
    all_issues: List[Dict[str, Any]] = []
    for t in targets:
        try:
            data = t.read_bytes()
            info = apple.parse_macho(data, path=t.as_posix())
        except (InputError, LimitError, OSError) as exc:
            r.add("binary/unparsable", "warn", f"{t.name}: {getattr(exc, 'message', exc)}",
                  path=t.as_posix())
            continue
        entry = {"path": t.as_posix(), "fat": info["fat"], "slices": info["slices"],
                 "architectures": info["architectures"]}
        if args.entitlements or args.objc or args.swift:
            sigs = []
            for s in info["slices"]:
                if s["has_code_signature"]:
                    sig = apple.parse_code_signature(data, s["code_signature_offset"],
                                                     s["code_signature_size"])
                    sigs.append({"arch": s["arch"], **sig.as_dict()})
            entry["code_signature"] = sigs
        if args.objc:
            entry["objc"] = apple.objc_metadata(data)
        if args.swift:
            entry["swift"] = apple.swift_metadata(data)
        if args.closure:
            entry["closure"] = ops.dylib_closure(info, base_dir=t.parent)
        results.append(entry)
        issues = ops.audit_macho(entry, base_dir=t.parent)
        all_issues.extend(issues)
        for i in issues:
            r.add(i["code"], i["level"], i["message"], path=t.as_posix(),
                  remediation=i.get("remediation", ""))
    r.data = {"binaries": results, "issues": all_issues}
    archs = sorted({a for e in results for a in e["architectures"]})
    r.data["summary"] = {"binaries": len(results), "architectures": ", ".join(archs),
                         "fat_binaries": sum(1 for e in results if e["fat"]),
                         "signed": sum(1 for e in results if e.get("code_signature")),
                         "issues": len(all_issues)}
    r.add("binary/inspected", "info", f"{len(results)} Mach-O image(s): arches {', '.join(archs) or '?'}")
    if args.entitlements:
        for e in results:
            for sig in e.get("code_signature", []):
                if sig.get("entitlements"):
                    keys = sorted(sig["entitlements"].keys())
                    r.add("binary/entitlements", "info",
                          f"{Path(e['path']).name}[{sig['arch']}] id={sig.get('identifier')} "
                          f"team={sig.get('team_id')} keys={', '.join(keys)[:200]}",
                          path=e["path"])
    return r


def cmd_privacy(args, limits: Limits) -> Report:
    r = Report("privacy", limits, {"target": args.target})
    result = ops.privacy_audit(args.target, limits, required_reasons=args.required_reasons)
    r.data = result
    s = result["summary"]
    r.data["summary"] = {"manifests": s["manifests"], "api_categories": s["api_categories"],
                         "declared_reasons": s["declared_reasons"],
                         "tracking_usage": s["tracking_usage"], "files_scanned": s["files_scanned"]}
    for m in result["manifests"]:
        r.add("privacy/manifest", "info", f"{m['path']}: {m['keys']} keys, "
                                          f"reasons={len(m['reasons'])}, tracking={m['tracking']}")
    for issue in result["issues"]:
        r.add(issue["code"], issue["level"], issue["message"], path=issue.get("path", ""),
              remediation=issue.get("remediation", ""))
    if not result["manifests"]:
        r.add("privacy/no-manifest", "warn",
              "no PrivacyInfo.xcprivacy found; App Store submissions require one in many cases",
              remediation="add a privacy manifest to the app and to every bundled SDK that needs one")
    return r


def cmd_signing_readiness(args, limits: Limits) -> Report:
    r = Report("signing-readiness", limits, {"ipa": args.ipa, "profile": args.profile,
                                             "identity": args.identity, "purpose": args.purpose})
    result = ops.signing_readiness(args.ipa, args.profile, args.identity, limits,
                                   team_id=args.team_id, bundle_id=args.bundle_id,
                                   purpose=args.purpose, approve=args.approve)
    r.data = result
    r.data["summary"] = {"verdict": result["verdict"], "purpose": result.get("purpose", "development"),
                         "identity": result["identity"]["subject"] or "(not supplied)",
                         "profile_days_left": result["profile"].get("days_until_expiry"),
                         "entitlement_conflicts": len(result["entitlement_conflicts"]),
                         "nested_components": result["nested"]["count"]}
    for check in result["checks"]:
        r.add(f"signing/{check['name']}", "info" if check["ok"] else "error",
              f"{check['name']}: {check['detail']}")
    for c in result["entitlement_conflicts"]:
        r.add("signing/entitlement-conflict", "error", c)
    r.next_actions.append("record THIS verdict in any build or install claim: readiness is not proof "
                          "of a successful device install")
    return r


def cmd_crash_triage(args, limits: Limits) -> Report:
    p = core.ensure_readable_file(args.crash, limits, what="crash report")
    r = Report("crash-triage", limits, {"crash": p.name})
    crash = apple.parse_crash_report(p, limits)
    triage = ops.triage_crash(crash)
    r.data = {"crash": crash, "triage": triage}
    r.data["summary"] = {"class": triage["class"], "confidence": triage["confidence"],
                         "exception": (triage.get("dtype")
                                       or crash["exception"].get("type", "")
                                       or crash.get("exception_type", "")),
                         "triggered_frames": len(crash["frames"]),
                         "suspect_images": ", ".join(triage["suspects"][:4]) or "(none)"}
    r.add("crash/classified", "info" if triage["class"] == "unknown" else "warn",
          f"{triage['class']}: {triage['reason']}")
    for hint in triage["hints"]:
        r.add("crash/hint", "info", hint)
    for img in crash["binary_images"]:
        if not img.get("uuid"):
            r.add("crash/image-without-uuid", "warn", f"binary image {img.get('name')} has no UUID; "
                                                      "symbolication will fail for its frames")
    if args.dsym_dir:
        match = ops.match_dsyms(crash, args.dsym_dir, limits)
        r.data["symbolication"] = match
        for name, info in match["matched"].items():
            r.add("crash/dsym-matched", "info", f"{name} {info['uuid']} -> {info['file']}")
        system_images = {img.get("name") for img in crash["binary_images"]
                         if str(img.get("path", "")).startswith(("/usr/lib/", "/System/Library/",
                                                                 "/usr/libexec/"))}
        for name, uuid in match["unmatched"].items():
            if name in system_images:
                r.add("crash/dsym-system-image", "info",
                      f"{name} is an OS image; its symbols come from the platform, not from your dSYMs")
                continue
            lvl = "error" if name in triage["suspects"] else "warn"
            r.add("crash/dsym-unmatched", lvl,
                  f"no dSYM for {name} {uuid}; frames from this image stay unresolved",
                  remediation="index the archive's dSYMs and re-run with --dsym-dir")
    r.next_actions.append("routes: signing -> signing-readiness, architecture -> inspect-binary, "
                          "watchdog -> performance lane")
    return r


def cmd_dsym_index(args, limits: Limits) -> Report:
    r = Report("dsym-index", limits, {"paths": args.paths, "state": args.state_dir})
    state = core.state_dir(args.state_dir)
    result = ops.index_dsyms(args.paths, state, limits, tag=args.tag)
    r.data = result
    r.data["summary"] = {"indexed": result["indexed"], "updated": result["updated"],
                         "failed": result["failed"], "index": result["index_file"]}
    for rec in result["records"][:40]:
        r.add("dsym/indexed", "info", f"{rec['uuid']} {rec['arch']} {Path(rec['file']).name}",
              path=rec["file"])
    for bad in result["errors"]:
        r.add("dsym/unreadable", "warn", bad)
    return r


def cmd_diagnose(args, limits: Limits) -> Report:
    p = core.ensure_readable_file(args.log, limits, what="log or diagnostic text")
    r = Report("diagnose", limits, {"log": p.name, "kind": args.kind})
    result = ops.diagnose_text(p, limits, kind=args.kind, patterns=args.pattern)
    r.data = result
    r.data["summary"] = {"lines": result["lines"], "matches": len(result["matches"]),
                         "classes": ", ".join(result["classes"]) or "(none)"}
    for m in result["matches"][:60]:
        r.add(f"diagnose/{m['class']}", "warn" if m["class"] != "benign" else "info",
              f"line {m['line']}: {m['text'][:220]}", remediation=m.get("remediation", ""))
    if not result["matches"]:
        r.add("diagnose/clean", "info", "no known failure signatures matched the input")
    return r


def cmd_migration_scan(args, limits: Limits) -> Report:
    root = core.ensure_readable_dir(args.project) if not Path(args.project).is_file() else Path(args.project)
    r = Report("migration-scan", limits, {"project": root.as_posix()})
    result = ops.migration_scan(root, limits, deprecations=args.deprecations)
    r.data = result
    r.data["summary"] = {"files": result["files"], "blockers": len(result["blockers"]),
                         "deprecations": len(result["deprecations"]),
                         "obsolete_arch_refs": len(result["arch_references"]),
                         "verdict": result["verdict"]}
    for b in result["blockers"]:
        r.add("migration/blocker", "error", b["message"], path=b.get("path", ""),
              remediation=b.get("remediation", ""))
    for d in result["deprecations"][:40]:
        r.add("migration/deprecation", "warn", f"{d['symbol']} ({d['path']}:{d['line']})",
              path=d.get("path", ""), remediation=d.get("remediation", ""))
    if result["arch_references"]:
        for a in result["arch_references"][:20]:
            r.add("migration/obsolete-arch", "warn",
                  f"armv7 reference at {a['path']}:{a['line']}: {a['text'][:120]}",
                  remediation="replace binary-only armv7 dependencies with XCFramework slices or source")
    if not result["blockers"] and not result["deprecations"]:
        r.add("migration/readiness", "info", "no catalogued migration blockers found in the source tree")
    return r


def cmd_export_options(args, limits: Limits) -> Report:
    r = Report("export-options", limits, {"method": args.method})
    result = ops.export_options(args.method, team_id=args.team_id, bundle_id=args.bundle_id,
                                signing_style=args.signing_style, profile=args.profile_name,
                                identity=args.identity, template=EXPORT_TEMPLATE,
                                upload_symbols=args.upload_symbols,
                                manage_app_version=args.manage_app_version)
    r.data = result
    r.data["summary"] = {"method": args.method, "signing_style": args.signing_style,
                         "keys": len(result["plist"]),
                         "would_distribute": args.method in ("app-store", "app-store-connect", "ad-hoc",
                                                            "enterprise")}
    out = core.ensure_output_path(args.out, default=Path("ExportOptions.plist"))
    import plistlib
    out.write_bytes(plistlib.dumps(result["plist"], fmt=plistlib.FMT_XML, sort_keys=True))
    back = plistlib.loads(out.read_bytes())
    result["written_to"] = out.as_posix()
    result["round_trip_equal"] = back == result["plist"]
    r.add("export-options/written", "info", f"wrote {out.as_posix()} "
                                           f"(round-trip {'verified' if result['round_trip_equal'] else 'FAILED'})")
    for note in result["notes"]:
        r.add("export-options/note", "warn" if "approval" in note or "cannot" in note else "info", note)
    if args.method in ("app-store", "app-store-connect"):
        r.add("export-options/approval", "warn",
              "an App Store export method produces an upload-ready package: distributing it requires "
              "explicit approval", remediation="add --approve publishing to any upload step")
    return r


def cmd_merge_results(args, limits: Limits) -> Report:
    for f in args.results:
        core.ensure_readable_file(f, limits, what="result file")
    r = Report("merge-results", limits, {"results": [Path(f).name for f in args.results]})
    merged = ops.merge_test_results(args.results, limits)
    r.data = merged
    r.data["summary"] = {"shards": merged["shards"], "tests": merged["totals"]["tests"],
                         "passed": merged["totals"]["passed"], "failed": merged["totals"]["failed"],
                         "flaky_retried": merged["totals"]["retried"]}
    out = core.ensure_output_path(args.out, default=Path("merged-results.json"))
    core.write_json(out, merged)
    r.data["written_to"] = out.as_posix()
    for f in merged["failures"][:40]:
        r.add("merge/failure", "error", f"{f['identifier']} @ {f['destination']}: {f['message'][:180]}")
    for f in merged.get("first_failure_evidence", [])[:20]:
        first = f.get("first_failure") or {}
        r.add("merge/first-failure-evidence", "info",
              f"{f['identifier']} failed on attempt {first.get('attempt')} and passed on attempt "
              f"{f['attempt']}; first-failure evidence kept: {str(first.get('message', ''))[:140]}")
    if merged["conflicts"]:
        for c in merged["conflicts"]:
            r.add("merge/conflict", "warn", c)
    r.next_actions.append("first-failure evidence is retained per test id; retries never overwrite it")
    return r


def cmd_report(args, limits: Limits) -> Report:
    r = Report("report", limits, {"out": args.out,
                                  "out_kind": core.sniff_kind(Path(args.out))
                                  if Path(args.out).exists() else "new-path"})
    bundle = ops.build_evidence_pack(args.evidence, args.claim, args.out, limits,
                                     tool_version=core.TOOL_VERSION)
    r.data = bundle
    r.data["summary"] = {"files": bundle["file_count"], "bytes": bundle["total_bytes"],
                         "manifest": bundle["manifest_path"], "claim": args.claim or "(none)"}
    if bundle["missing"]:
        for m in bundle["missing"]:
            r.add("report/missing-evidence", "warn", f"evidence path not found: {m}")
    for w in bundle["warnings"]:
        r.add("report/warning", "warn", w)
    r.add("report/packaged", "info",
          f"{bundle['file_count']} file(s), {core.human_bytes(bundle['total_bytes'])} indexed")
    return r


def cmd_validate_report(args, limits: Limits) -> Report:
    p = core.ensure_readable_file(args.report, limits, what="report JSON")
    r = Report("validate-report", limits, {"report": p.name})
    result = ops.validate_report(p, SCHEMA_DIR)
    r.data = result
    r.data["summary"] = {"verdict": result["verdict"], "purpose": result.get("purpose", "development"), "errors": len(result["errors"]),
                         "schema": result["schema_version"]}
    for e in result["errors"]:
        r.add("validate/schema", "error", e)
    if result["verdict"] == "PASS":
        r.add("validate/ok", "info", "report satisfies asset-report.schema.json")
    return r


def cmd_snapshot(args, limits: Limits) -> Report:
    root = core.ensure_readable_dir(args.root, what="artifact tree") if Path(args.root).is_dir() \
        else core.ensure_readable_file(args.root, limits, what="artifact")
    r = Report("snapshot", limits, {"root": root.as_posix(), "previous": args.previous})
    result = ops.snapshot_artifacts(root, limits, workspace=args.workspace,
                                    previous=args.previous)
    r.data = result
    diff = result.get("diff")
    r.data["summary"] = {"files": len(result["files"]),
                         "bytes": result["total_bytes"],
                         "duplicate_content_groups": len(result["duplicate_content_groups"]),
                         "compared_with": (diff or {}).get("previous", "(baseline only)"),
                         "identical": (diff or {}).get("identical", "n/a")}
    out = core.ensure_output_path(args.out, default=Path("artifact-snapshot.json"))
    core.write_json(out, result)
    result["written_to"] = out.as_posix()
    r.add("snapshot/written", "info", f"{len(result['files'])} file hashes written to "
                                     f"{out.as_posix()} (timestamps excluded)")
    if diff:
        lvl = "info" if diff["identical"] else "warn"
        r.add("snapshot/compare", lvl,
              f"added={len(diff['added'])} removed={len(diff['removed'])} "
              f"changed={len(diff['changed'])} since {diff['previous']}")
        for p_ in diff["changed"][:20]:
            r.add("snapshot/changed", "warn", f"content changed: {p_}")
        for p_ in diff["removed"][:20]:
            r.add("snapshot/removed", "warn", f"missing now: {p_}")
        r.add("snapshot/caveat", "info", diff["caveat"])
    for digest, paths in list(result["duplicate_content_groups"].items())[:10]:
        r.add("snapshot/duplicate-content", "info",
              f"{len(paths)} files share content {digest[:12]}: {', '.join(paths[:4])}")
    if args.previous:
        r.next_actions.append("keep both snapshot JSON files as the build-audit evidence")
    else:
        r.next_actions.append("re-run after a clean rebuild with --previous "
                              "<this snapshot> to prove reproducibility")
    return r


def cmd_binary_diff(args, limits: Limits) -> Report:
    first = core.ensure_readable_file(args.first, limits, what="first binary")
    second = core.ensure_readable_file(args.second, limits, what="second binary")
    if args.approve:
        decision = core.classify_request("compare two authorized builds", core.load_policy(args.policy))
        core.require_not_denied(decision)
    r = Report("binary-diff", limits, {"first": first.name, "second": second.name})
    result = ops.binary_diff(first, second, limits)
    r.data = result
    r.data["summary"] = {"identical_bytes": result["identical_bytes"],
                         "uuid_same": result["uuid"]["same"],
                         "arch_added": ", ".join(result["architectures_added"]) or "none",
                         "arch_removed": ", ".join(result["architectures_removed"]) or "none",
                         "size_delta": (result["second"]["size"] - result["first"]["size"])}
    if result["identical_bytes"]:
        r.add("diff/identical", "info", "the two images are byte-identical")
    else:
        r.add("diff/different", "info",
              f"byte difference: {result['first']['sha256'][:12]} vs {result['second']['sha256'][:12]}")
    if not result["uuid"]["same"]:
        r.add("diff/uuid", "warn", "LC_UUID differs: dSYMs, crash reports and caches are NOT "
                                   "interchangeable between these builds")
    if result["architectures_added"] or result["architectures_removed"]:
        r.add("diff/architectures", "warn",
              f"added {result['architectures_added']}, removed {result['architectures_removed']}")
    if result["dylibs_added"] or result["dylibs_removed"]:
        r.add("diff/dylibs", "warn", f"dylibs added {result['dylibs_added']}, "
                                     f"removed {result['dylibs_removed']}")
    if not result["min_os"]["same"]:
        r.add("diff/min-os", "warn", f"minimum OS {result['min_os']['first']} -> "
                                     f"{result['min_os']['second']}")
    if not result["platform"]["same"]:
        r.add("diff/platform", "warn", f"platform {result['platform']['first']} -> "
                                       f"{result['platform']['second']}")
    return r


def cmd_help(args, limits) -> Report:  # replaced by argparse help in main()
    raise UsageError("use --help")


# ==========================================================================
# argument parsing
# ==========================================================================
class _Parser(argparse.ArgumentParser):
    def error(self, message):  # type: ignore[override]
        raise UsageError(f"{message}", remediation="run with help or --help for the full reference")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="tools-iosh.py",
                description="iOS/iPadOS Agent Skill Tool -- 354 catalogued capabilities across 29 "
                            "categories, Linux-native inspection with explicit macOS/device/account "
                            "routing.",
                formatter_class=argparse.RawDescriptionHelpFormatter,
                epilog="""examples:
  python3 tools-iosh.py capabilities
  python3 tools-iosh.py route --action "codesign the ipa and upload to testflight"
  python3 tools-iosh.py inspect-ipa --ipa App.ipa --json-out reports/ipa.json
  python3 tools-iosh.py plan --category 13 --have-macos --write plan.json
  python3 tools-iosh.py health --json-out reports/health.json
""")
    p.add_argument("--json-out", metavar="PATH", default=None,
                   help="write the machine report here (relative paths preferred)")
    p.add_argument("--human-out", metavar="PATH", default=None, help="write the human report here")
    p.add_argument("--format", choices=["human", "json", "jsonl", "both"], default="human",
                   help="stdout format (default: human)")
    p.add_argument("--verbose", action="store_true", help="include data dumps and remediations")
    p.add_argument("--dry-run", action="store_true",
                   help="plan without side effects; external commands are not executed")
    p.add_argument("--policy", metavar="FILE", default=None,
                   help="policy overlay JSON (see asset-policy.json)")
    p.add_argument("--state-dir", metavar="DIR", default=None,
                   help="writable state directory for pins, cache index, dSYM index (default: $IOSH_STATE_DIR)")
    p.add_argument("--max-entries", type=int, default=None)
    p.add_argument("--max-file-bytes", type=int, default=None)
    p.add_argument("--max-total-bytes", type=int, default=None)
    p.add_argument("--max-depth", type=int, default=None)
    p.add_argument("--timeout", type=int, default=None, help="per-command timeout in seconds")
    p.add_argument("--retries", type=int, default=None)
    p.add_argument("--max-workers", type=int, default=None)
    p.add_argument("--max-findings", type=int, default=None)
    p.add_argument("--max-output-bytes", type=int, default=None)
    p.add_argument("--max-disk-ratio", type=float, default=None)

    sub = p.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Parser)

    def add(name, help_text, func):
        sp = sub.add_parser(name, help=help_text, description=help_text, formatter_class=argparse.RawDescriptionHelpFormatter)
        sp.set_defaults(func=func)
        return sp

    sp = add("capabilities", "list catalogued capabilities and the command that serves each one",
             cmd_capabilities)
    sp.add_argument("--category", help="restrict to one catalog category number (1-29)")
    sp.add_argument("--implemented-only", action="store_true",
                    help="only capabilities runnable by this CLI right now")
    sp.add_argument("--json-only", action="store_true", help="suppress the summary finding")

    sp = add("probe", "inventory the Linux host (CPU, memory, disk, USB, toolchains)", cmd_probe)
    sp.add_argument("--paths", nargs="*", default=[], help="declared paths to validate")

    sp = add("route", "classify an action and decide where it may run", cmd_route)
    sp.add_argument("--action", required=True, help="free-text description of the intended action")
    sp.add_argument("--have-macos", action="store_true")
    sp.add_argument("--have-device", action="store_true")
    sp.add_argument("--have-account", action="store_true")
    sp.add_argument("--network", action="store_true")

    sp = add("plan", "build (and optionally execute) a validated plan for any capability", cmd_plan)
    sp.add_argument("--category", help="catalog category number 1-29")
    sp.add_argument("--capability", help="capability id (c09-01), name or slug")
    sp.add_argument("--target", choices=["auto", "linux", "macos"], default="auto")
    sp.add_argument("--have-macos", action="store_true")
    sp.add_argument("--have-device", action="store_true")
    sp.add_argument("--have-account", action="store_true")
    sp.add_argument("--network", action="store_true")
    sp.add_argument("--approve", action="append", default=[], metavar="GATE")
    sp.add_argument("--write", metavar="FILE", help="write the plan JSON")
    sp.add_argument("--execute", action="store_true",
                    help="run the runnable steps (native probes only; never destructive)")

    sp = add("gate", "block completion claims that lack opened, parsed evidence", cmd_gate)
    sp.add_argument("--claim", required=True, help="the completion claim being checked")
    sp.add_argument("--evidence", nargs="*", default=[], help="files that support the claim")
    sp.add_argument("--required", nargs="*", default=[],
                    choices=["ipa", "plist", "macho", "zip", "json", "profile", "crash", "log",
                             "report", "dsym", "xcode-project", "none"],
                    help="artifact kinds that must be present and parseable")

    sp = add("workspace", "create an isolated inspection workspace that preserves originals",
             cmd_workspace)
    sp.add_argument("--name", required=True)
    sp.add_argument("--inputs", nargs="*", default=[])
    sp.add_argument("--into", default=".")
    sp.add_argument("--clean", action="store_true", help="refuse to reuse an existing workspace")

    sp = add("paths", "normalise paths between Linux and macOS workers", cmd_paths)
    sp.add_argument("--paths", nargs="+", required=True)
    sp.add_argument("--to-scheme", choices=["posix", "macos-relative", "windows"], default="posix")

    sp = add("pins", "record or compare exact tool versions", cmd_pins)
    sp.add_argument("pins_action", choices=["record", "compare"])
    sp.add_argument("--tool", dest="tools", action="append", default=[])

    sp = add("cache", "offline artifact cache with the Apple-license guard", cmd_cache)
    sp.add_argument("cache_action", choices=["init", "add", "list", "verify", "check"])
    sp.add_argument("--cache-dir", default=None)
    sp.add_argument("--add", nargs="*", default=[])
    sp.add_argument("--name", default=None)
    sp.add_argument("--check", nargs="*", default=[])

    sp = add("license-scan", "detect Apple-licensed or commercial payloads that must not be copied",
             cmd_license_scan)
    sp.add_argument("--paths", nargs="+", required=True)

    sp = add("secrets-scan", "find accidental credential material in source and bundles",
             cmd_secrets_scan)
    sp.add_argument("--paths", nargs="+", required=True)

    sp = add("health", "startup self-tests, tool status aggregation and fixture proofs", cmd_health)
    sp.add_argument("--tool", action="append", default=[],
                    help="limit to specific tools (repeatable)")
    sp.add_argument("--macos-probe", metavar="FILE",
                    help="JSON captured on the macOS worker by 'health --format json'")
    sp.add_argument("--no-fixtures", action="store_true", help="skip bundled fixture round-trips")

    sp = add("matrix", "build Simulator/device test lanes from the catalog matrix", cmd_matrix)
    sp.add_argument("--runtimes", default=None, help="comma separated runtime hints, oldest first")
    sp.add_argument("--include-device", action="store_true")
    sp.add_argument("--clean-state", action="store_true", default=True)
    sp.add_argument("--lane-retries", type=int, default=0,
                    help="bounded retries per lane (0 = first failure is the result)")
    sp.add_argument("--write", metavar="FILE")

    sp = add("detect", "identify artifacts even when extensions lie", cmd_detect)
    sp.add_argument("--paths", nargs="+", required=True)

    sp = add("inspect-ipa", "structure, identity, architecture and encryption report", cmd_inspect_ipa)
    sp.add_argument("--ipa", required=True)

    sp = add("inspect-plist", "audit, convert and privacy-check a property list", cmd_inspect_plist)
    sp.add_argument("--plist", required=True)
    sp.add_argument("--privacy", action="store_true", help="audit NS*UsageDescription coverage")
    sp.add_argument("--convert", choices=["xml", "binary"], default=None)
    sp.add_argument("--out", default=None)

    sp = add("inspect-project", "read .xcodeproj/.xcworkspace without Xcode", cmd_inspect_project)
    sp.add_argument("--project", required=True)
    sp.add_argument("--workspace", action="store_true")
    sp.add_argument("--diff-configs", action="store_true")
    sp.add_argument("--xcconfig", action="store_true")
    sp.add_argument("--destinations", action="store_true")

    sp = add("profile", "decode an embedded.mobileprovision and monitor expiry", cmd_profile)
    sp.add_argument("--profile", required=True)
    sp.add_argument("--expiry-days", type=int, default=30)
    sp.add_argument("--diff-entitlements", metavar="OTHER_PROFILE", default=None)

    sp = add("inspect-binary", "Mach-O load commands, entitlements, dylibs, ObjC/Swift metadata",
             cmd_inspect_binary)
    sp.add_argument("--binary", required=True)
    sp.add_argument("--max-binaries", type=int, default=200)
    sp.add_argument("--entitlements", action="store_true")
    sp.add_argument("--closure", action="store_true")
    sp.add_argument("--objc", action="store_true")
    sp.add_argument("--swift", action="store_true")

    sp = add("privacy", "privacy manifest and required-reason API checks", cmd_privacy)
    sp.add_argument("--target", required=True)
    sp.add_argument("--required-reasons", metavar="FILE", default=None)

    sp = add("signing-readiness", "identity/profile/bundle/entitlement readiness gate",
             cmd_signing_readiness)
    sp.add_argument("--ipa", default=None)
    sp.add_argument("--profile", default=None)
    sp.add_argument("--identity", default=None, help="codesign identity string (never a private key)")
    sp.add_argument("--team-id", default=None)
    sp.add_argument("--bundle-id", default=None)
    sp.add_argument("--purpose", choices=["development", "distribution"], default="development",
                    help="intended install purpose; changes which profile checks apply")
    sp.add_argument("--approve", action="append", default=[], metavar="GATE")

    sp = add("crash-triage", "parse a crash report, classify it and match dSYMs", cmd_crash_triage)
    sp.add_argument("--crash", required=True)
    sp.add_argument("--dsym-dir", default=None)

    sp = add("dsym-index", "index dSYM UUIDs into the state directory", cmd_dsym_index)
    sp.add_argument("--paths", nargs="+", required=True)
    sp.add_argument("--tag", default="")

    sp = add("diagnose", "classify xcodebuild/device log text into the failure taxonomy", cmd_diagnose)
    sp.add_argument("--log", required=True)
    sp.add_argument("--kind", choices=["auto", "build", "test", "device", "runtime"], default="auto")
    sp.add_argument("--pattern", nargs="*", default=[], help="extra regexes to classify")

    sp = add("migration-scan", "source migration blockers, deprecations and 32-bit references",
             cmd_migration_scan)
    sp.add_argument("--project", required=True)
    sp.add_argument("--deprecations", action="store_true")

    sp = add("export-options", "generate a validated ExportOptions.plist", cmd_export_options)
    sp.add_argument("--method", required=True,
                    choices=["development", "ad-hoc", "enterprise", "app-store", "app-store-connect",
                             "validation", "release-testing", "debugging"])
    sp.add_argument("--team-id", required=True)
    sp.add_argument("--bundle-id", default=None)
    sp.add_argument("--signing-style", choices=["automatic", "manual"], default="automatic")
    sp.add_argument("--profile-name", dest="profile_name", default=None)
    sp.add_argument("--identity", default=None)
    sp.add_argument("--upload-symbols", action="store_true")
    sp.add_argument("--manage-app-version", action="store_true")
    sp.add_argument("--out", default=None)

    sp = add("merge-results", "merge sharded XCTest result JSON without losing failure identity",
             cmd_merge_results)
    sp.add_argument("--results", nargs="+", required=True)
    sp.add_argument("--out", default=None)

    sp = add("report", "package an evidence bundle and validate every artifact it indexes", cmd_report)
    sp.add_argument("--evidence", nargs="+", required=True)
    sp.add_argument("--claim", default="")
    sp.add_argument("--out", required=True, help="output directory for the evidence bundle")
    sp.add_argument("--json-out", metavar="PATH", default=None,
                    help="also write this command's report JSON here")

    sp = add("snapshot", "hash an artifact tree, detect duplicates and compare with a baseline",
             cmd_snapshot)
    sp.add_argument("--root", required=True)
    sp.add_argument("--previous", default=None, help="earlier snapshot JSON to compare against")
    sp.add_argument("--workspace", default=None,
                    help="label prefix for paths (e.g. the workspace name) so two builds compare")
    sp.add_argument("--out", default=None)

    sp = add("binary-diff", "compare two authorized Mach-O builds (UUID, arch, dylibs, size)",
             cmd_binary_diff)
    sp.add_argument("--first", required=True)
    sp.add_argument("--second", required=True)
    sp.add_argument("--approve", action="append", default=[], metavar="GATE")

    sp = add("validate-report", "schema-validate a report produced by this tool", cmd_validate_report)
    sp.add_argument("--report", required=True)

    return p


# ==========================================================================
# main
# ==========================================================================
# subcommands whose own parser defines --json-out as a value option
SUBCOMMAND_JSON_OUT = {"report"}
GLOBAL_VALUE_FLAGS = {"--json-out", "--human-out", "--format", "--policy", "--state-dir",
                      "--max-entries", "--max-file-bytes", "--max-total-bytes", "--max-depth",
                      "--timeout", "--retries", "--max-workers", "--max-findings",
                      "--max-output-bytes", "--max-disk-ratio"}
GLOBAL_BOOL_FLAGS = {"--verbose", "--dry-run"}


KNOWN_SUBCOMMANDS = {
    "capabilities", "probe", "route", "plan", "gate", "workspace", "paths", "pins", "cache",
    "license-scan", "secrets-scan", "health", "matrix", "detect", "inspect-ipa", "inspect-plist",
    "inspect-project", "profile", "inspect-binary", "privacy", "signing-readiness",
    "crash-triage", "dsym-index", "diagnose", "migration-scan", "export-options",
    "merge-results", "report", "validate-report", "snapshot", "binary-diff",
}


def hoist_global_flags(argv: Sequence[str]) -> List[str]:
    """Allow global options before OR after the subcommand.

    argparse only accepts parent options before the subcommand, which is a poor
    experience for long inspection commands. Global flags are therefore moved to
    the front; everything else keeps its original order.
    """
    head: List[str] = []
    tail: List[str] = []
    i = 0
    items = list(argv)
    # find the subcommand first so subcommand-scoped flags are left untouched
    subcommand = next((tok for tok in items if tok in KNOWN_SUBCOMMANDS), None)
    while i < len(items):
        token = items[i]
        name = token.split("=", 1)[0]
        if subcommand in SUBCOMMAND_JSON_OUT and name == "--json-out":
            tail.append(token)
            i += 1
            continue
        if name in GLOBAL_VALUE_FLAGS:
            if "=" in token:
                head.append(token)
                i += 1
                continue
            if i + 1 >= len(items):
                tail.append(token)
                i += 1
                continue
            head.extend([token, items[i + 1]])
            i += 2
            continue
        if token in GLOBAL_BOOL_FLAGS:
            head.append(token)
            i += 1
            continue
        tail.append(token)
        i += 1
    return head + tail


def resolve_limits(args) -> Limits:
    return core.default_limits(
        max_entries=args.max_entries, max_file_bytes=args.max_file_bytes,
        max_total_bytes=args.max_total_bytes, max_depth=args.max_depth,
        timeout_s=args.timeout, retries=args.retries, max_workers=args.max_workers,
        max_findings=args.max_findings, max_output_bytes=args.max_output_bytes,
        max_disk_ratio=args.max_disk_ratio,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    try:
        args = parser.parse_args(hoist_global_flags(raw_argv))
    except ToolError as exc:  # usage errors raised by _Parser.error
        report = Report("cli", core.default_limits())
        report.error(exc)
        report.add(exc.code, "error", exc.message, remediation=exc.remediation)
        fmt = "human"
        json_out = None
        for i, token in enumerate(raw_argv):
            if token == "--format" and i + 1 < len(raw_argv):
                fmt = raw_argv[i + 1]
            elif token.startswith("--format="):
                fmt = token.split("=", 1)[1]
            elif token == "--json-out" and i + 1 < len(raw_argv):
                json_out = Path(raw_argv[i + 1])
        core.emit(report, fmt="json" if fmt == "json" else "human",
                  json_path=json_out)
        return exc.exit_code
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        limits = resolve_limits(args)
        json_path = core.ensure_output_path(args.json_out) if args.json_out else None
        human_path = core.ensure_output_path(args.human_out) if args.human_out else None
        report = args.func(args, limits)
        if args.dry_run and getattr(args, "execute", False):
            pass
        return core.emit(report, fmt=args.format, verbose=args.verbose,
                         json_path=json_path, human_path=human_path)
    except ToolError as exc:
        report = Report(args.command, core.default_limits())
        report.error(exc)
        report.add(exc.code, "error", exc.message, remediation=exc.remediation)
        if exc.details:
            report.data["details"] = exc.details
        code = core.emit(report, fmt="json" if args.format == "json" else "human",
                         verbose=args.verbose)
        return exc.exit_code or code
    except SystemExit as exc:  # argparse --help and hard usage exits
        return int(exc.code or 0)
    except KeyboardInterrupt:
        sys.stderr.write("cancelled by operator\n")
        return 130


if __name__ == "__main__":
    sys.exit(main())
