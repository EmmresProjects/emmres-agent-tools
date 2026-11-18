#!/usr/bin/env python3
"""ai-video-creation / tools-ai-video.py  --  PRIMARY ENTRY POINT

Unified, vendor-neutral AI video creation Skill Tool component.

One folder, one CLI. It ingests capability catalogs (plain text, Markdown, or
copied HTML), exposes the 47-category / 564-capability registry extracted from
the AI Video Creation Skill Catalog, and implements every catalog capability as
a reachable command/handler with a real local operation: routing, planning,
prompt compilation, shot lists, captions, validation, quality gates, bounded
execution with approval/retry/cancellation/recovery, delivery manifests, and
built-in tests.

Run from the future skill root:
    bash -lc 'python3 ai-video-creation/tools-ai-video.py help'
    bash -lc 'python3 ai-video-creation/tools-ai-video.py capabilities'

Python 3.9+ standard library only. No network, no shell, no credentials.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path

# Keep the Skill Tool folder flat: no __pycache__ subfolder is ever created.
sys.dont_write_bytecode = True

TOOL_DIR = Path(__file__).resolve().parent
TOOL_FOLDER = TOOL_DIR.name
EXIT_CODES = {"ok": 0, "validation": 2, "approval": 3, "runtime": 4, "not_found": 5, "internal": 6}


def load_sibling(filename, module_name):
    path = TOOL_DIR / filename
    if not path.is_file():
        raise SystemExit("FATAL: required module missing: %s" % path)
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Loading order: foundation, catalog, planner, media, runtime, selftest.
C = load_sibling("tools-contracts.py", "avc_contracts")
K = load_sibling("tools-catalog.py", "avc_catalog")
P = load_sibling("tools-planner.py", "avc_planner")
M = load_sibling("tools-media.py", "avc_media")
R = load_sibling("tools-runtime.py", "avc_runtime")
S = load_sibling("tools-selftest.py", "avc_selftest")
DEPS = {"contracts": C, "catalog": K, "planner": P, "media": M, "runtime": R}

COMMANDS = [
    ("help", "Built-in help, command list, and quickstart."),
    ("capabilities", "Capability-list command: list commands, categories, operations, and all 564 capabilities."),
    ("ingest", "Parse a single capability, numbered list, one category, or several categories from TXT/MD/HTML into one unified registry."),
    ("describe", "Show the full recommended skill contract for any catalog capability."),
    ("route", "Route a brief to ranked catalog capabilities with transparent scores."),
    ("plan", "Build one unified, dependency-ordered production plan with gates, approvals, limits, and fallbacks."),
    ("prompt", "Compile deterministic editable positive/negative prompt variants with an ambiguity audit."),
    ("shotlist", "Build a bounded shot list, timecodes, and an EDL from a script or brief."),
    ("captions", "Parse SRT/VTT/JSON cues, validate timing and reading speed, emit normalized SRT/VTT."),
    ("validate", "Validate a brief/job against the JSON schema and semantic safety rules."),
    ("gates", "Evaluate the seven catalog quality gates against a job and its artifacts."),
    ("run", "Bounded execution of a plan or any single capability (--capability): dry-run default, local provider, approval, retries, cancellation, cleanup, resume."),
    ("approve", "Record, revoke, or list human approvals for sensitive capabilities."),
    ("deliver", "Build a checksummed delivery manifest from local artifacts and a delivery preset."),
    ("report", "Summarize a run directory from its checkpoint, including recovery steps."),
    ("selftest", "Run the built-in test suite over every major capability and recovery path."),
    ("doctor", "Check runtime, config, schemas, registry integrity, resource references, and folder layout."),
]


class Context:
    def __init__(self, args):
        self.args = args
        self.cfg, self.cfg_origin = C.load_config(args.config, args.root)
        self.root = args.root
        self.resources = self.cfg["resources"]
        self.schema = C.load_json(self.resources["brief_schema"], args.root)
        self.gates_def = C.load_json(self.resources["quality_gates"], args.root)
        self.style = C.load_json(self.resources["style_presets"], args.root)
        self.delivery = C.load_json(self.resources["delivery_presets"], args.root)
        self.policy = C.load_json(self.resources["approval_policy"], args.root)
        self.registry = self._load_registry()
        self.deps = dict(DEPS)

    def _load_registry(self):
        ref = self.args.catalog
        if ref:
            reg = K.load_registry(ref)
        else:
            reg = C.load_json(self.resources["catalog"], self.root)
            issues = K.validate_registry(reg)
            if issues:
                raise C.ToolError("INVALID_REGISTRY", "Bundled capability registry failed validation.",
                                  details={"issues": issues[:10]})
        return reg

    def resource_text(self, key):
        return C.load_text(self.resources[key], self.root,
                           max_bytes=int(self.cfg["limits"]["max_input_bytes"]))

    def brief(self):
        value, origin = C.load_user_json(self.args.input, self.args.text,
                                         max_bytes=int(self.cfg["limits"]["max_input_bytes"]))
        if not isinstance(value, dict):
            raise C.ToolError("INVALID_BRIEF", "Brief must be a JSON object.", path=origin)
        return value, origin

    def runtime(self):
        return R.Runtime(self.cfg, self.policy, self.gates_def, self.style, self.delivery,
                         self.schema, self.registry, self.deps, root=self.root)


# --------------------------------------------------------------------------
# Rendering / output
# --------------------------------------------------------------------------

def render_text(env):
    d = env.get("data", {})
    cmd = env.get("command")
    lines = ["ai-video-creation | %s | status: %s" % (cmd, env["status"])]
    for e in env.get("errors", []):
        lines.append("ERROR %s: %s" % (e.get("code"), e.get("message")))
    for w in env.get("warnings", []):
        lines.append("WARN %s" % (w if isinstance(w, str) else w.get("message", w)))
    if cmd == "help":
        lines += ["", "Commands:"] + ["  %-14s %s" % (n, desc) for n, desc in d.get("commands", [])]
        lines += ["", "Quickstart:"] + ["  " + x for x in d.get("quickstart", [])]
    elif cmd == "capabilities":
        s = d.get("summary", {})
        lines.append("umbrella=%s categories=%s capabilities=%s" %
                     (s.get("umbrella"), s.get("categories"), s.get("capabilities")))
        if d.get("categories_only"):
            for c in d["categories_only"]:
                lines.append("  [%02d] %s (%d)" % (c["id"], c["name"], c["skill_count"]))
        for c in d.get("capabilities", []):
            lines.append("  %-14s %-58s [%s/%s]" % (c["id"], c["name"][:58], c["family"], c["local_operation"]))
        if d.get("total") is not None:
            lines.append("showing %d of %d" % (len(d.get("capabilities", [])), d["total"]))
    elif cmd == "describe":
        c = d.get("contract", {})
        lines += ["%s: %s" % (c.get("capability_id"), c.get("capability_name")),
                  "category: %s | operation: %s" % (c.get("category"), c.get("local_operation")),
                  "trigger: %s" % c.get("trigger"),
                  "approval required: %s %s" % (c.get("human_approval", {}).get("required"),
                                                c.get("human_approval", {}).get("reasons", [])),
                  "gates: %s" % ", ".join(g["id"] for g in c.get("quality_gates", []))]
    elif cmd == "plan":
        p = d.get("plan", {})
        lines += ["job_id=%s provider=%s" % (p.get("job_id"), p.get("provider")),
                  "stage order: %s" % " -> ".join(p.get("stage_order", []))]
        for s in p.get("stages", []):
            lines.append("  %-14s %-14s approval=%s caps=%d" %
                         (s["id"], s["operation"], s["approval_required"], len(s["capabilities"])))
    elif cmd == "run":
        lines.append("job_id=%s mode=%s provider=%s" % (d.get("job_id"), d.get("mode"), d.get("provider")))
        lines.append("completed: %s" % (", ".join(d.get("completed", [])) or "none"))
        lines.append("simulated: %s" % (", ".join(d.get("simulated", [])) or "none"))
        if d.get("failed"):
            lines.append("FAILED: %s" % d["failed"])
        if d.get("needs_approval"):
            lines.append("NEEDS APPROVAL: %s" % d["needs_approval"])
        if d.get("recovery"):
            lines.append("recovery: %s" % d["recovery"])
        lines.append("artifacts: %s" % (", ".join(d.get("files", [])) or "none"))
    elif cmd == "selftest":
        lines.append("passed=%d failed=%d" % (d.get("passed", 0), d.get("failed", 0)))
        for t in d.get("tests", []):
            lines.append("  [%s] %s: %s" % (t["status"].upper(), t["name"], t["detail"])[:220])
    elif cmd == "doctor":
        lines.append("status=%s python=%s" % (d.get("status"), d.get("python")))
        for chk in d.get("checks", []):
            lines.append("  [%s] %s: %s" % (chk["status"].upper(), chk["name"], chk["detail"])[:220])
    else:
        keys = [k for k in ("kind", "status", "cue_count", "shot_count", "variants", "total",
                            "job_id", "capabilities", "categories", "file_count", "error_count") if k in d]
        for k in keys:
            lines.append("%s: %s" % (k, json.dumps(d[k], ensure_ascii=False)[:200]))
        if "preview" in d:
            lines.append("preview: %s" % str(d["preview"])[:300])
        if d.get("artifact"):
            lines.append("artifact: %s" % d["artifact"])
    if env.get("recovery"):
        lines.append("recovery: %s" % env["recovery"])
    return "\n".join(lines)


def emit(args, command, status="ok", data=None, errors=None, warnings=None, recovery=None, exit_code=0):
    env = C.envelope(command, status, data, errors, warnings, recovery,
                     limits={"max_input_bytes": args.cfg["limits"]["max_input_bytes"],
                             "job_seconds": args.cfg["timeouts"]["job_seconds"]} if hasattr(args, "cfg") else {},
                     meta={"config": getattr(args, "cfg_origin", None)} if hasattr(args, "cfg_origin") else {})
    if getattr(args, "format", "json") == "text":
        print(render_text(env))
    else:
        print(json.dumps(env, indent=2, ensure_ascii=False))
    return exit_code


def save_artifact(path, obj, text=None, suffix=None):
    """Write the primary artifact for a command. Returns its path string."""
    if not path:
        return None
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        p.write_text(text, encoding="utf-8")
    else:
        p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------
# Command handlers
# --------------------------------------------------------------------------

def cmd_help(ctx, args):
    return emit(args, "help", data={
        "commands": COMMANDS,
        "primary": "tools-ai-video.py",
        "quickstart": [
            "python3 ai-video-creation/tools-ai-video.py capabilities --category captions --limit 5",
            "python3 ai-video-creation/tools-ai-video.py describe --capability c17-s01",
            "python3 ai-video-creation/tools-ai-video.py plan --input ai-video-creation/asset-example-brief.json --output plan.json",
            "python3 ai-video-creation/tools-ai-video.py run --input plan.json --output-dir out --mode execute --provider local",
            "python3 ai-video-creation/tools-ai-video.py selftest",
        ],
        "note": "Use --help with any command for all flags. Absolute filesystem paths are accepted only for user --input/--output; packaged references use /asset-*.json relative to the future skill root.",
    })


def cmd_capabilities(ctx, args):
    reg = ctx.registry
    summary = K.registry_summary(reg)
    data = {"commands": COMMANDS, "summary": summary, "operations": sorted(summary["operations"]),
            "families": summary["families"]}
    if args.categories_only:
        data["categories_only"] = reg.get("categories", [])
        return emit(args, "capabilities", data=data)
    caps, total = K.search_capabilities(reg, args.query, args.category, args.family,
                                        limit=args.limit, offset=args.offset)
    data["capabilities"] = [{k: c[k] for k in ("id", "name", "slug", "category", "family",
                                               "local_operation", "requires_approval")} for c in caps]
    data["total"] = total
    data["limit"] = args.limit
    data["offset"] = args.offset
    return emit(args, "capabilities", data=data)


def cmd_ingest(ctx, args):
    raw, origin = C.load_user_input(args.input, args.text,
                                    max_bytes=int(ctx.cfg["limits"]["max_input_bytes"]))
    reg, info = K.parse_catalog(raw, umbrella=args.name, source_name=origin)
    issues = K.validate_registry(reg)
    status = "ok" if not issues else "error"
    path = save_artifact(args.output, reg)
    data = {"kind": "ingest-result", "status": "ok" if not issues else "invalid",
            "umbrella": reg["umbrella"], "origin": origin,
            "stats": info["stats"], "categories": reg["categories"],
            "capabilities_preview": [{k: c[k] for k in ("id", "name", "category", "description", "local_operation")}
                                     for c in reg["capabilities"][:8]],
            "registry_artifact": path, "issues": issues,
            "reuse": "Pass --catalog %s to any other command." % (path or "<saved-registry.json>")}
    return emit(args, "ingest", status=status, data=data,
                warnings=[{"code": "INGEST_NOTE", "message": w} for w in info["warnings"]],
                errors=[{"code": "REGISTRY_INVALID", "message": i} for i in issues],
                exit_code=0 if not issues else EXIT_CODES["validation"])


def cmd_describe(ctx, args):
    cap, suggestions = K.find_capability(ctx.registry, args.capability)
    if not cap:
        return emit(args, "describe", status="error",
                    data={"query": args.capability, "suggestions": suggestions},
                    errors=[{"code": "CAPABILITY_NOT_FOUND", "message": "No matching capability."}],
                    recovery="Run 'capabilities --query <term>' to find an exact id or name.",
                    exit_code=EXIT_CODES["not_found"])
    contract = C.build_contract(cap, ctx.cfg, ctx.policy, ctx.gates_def)
    path = save_artifact(args.output, contract)
    return emit(args, "describe", data={"kind": "capability-contract", "capability": cap,
                                        "contract": contract, "artifact": path})


def cmd_route(ctx, args):
    brief, origin = ctx.brief()
    errors, warnings = C.semantic_validate_brief(brief, ctx.cfg, ctx.policy, ctx.schema)
    if errors and args.strict:
        return emit(args, "route", status="error", data={"errors": errors},
                    errors=errors, recovery="Fix the brief or rerun without --strict.",
                    exit_code=EXIT_CODES["validation"])
    rows, meta = P.route(ctx.registry, brief, ctx.cfg, brief.get("capabilities"))
    path = save_artifact(args.output, {"kind": "route-result", "matches": rows, "meta": meta})
    return emit(args, "route", data={"kind": "route-result", "origin": origin, "meta": meta,
                                     "matches": rows, "artifact": path,
                                     "preview": [r["name"] for r in rows[:5]]},
                warnings=warnings)


def cmd_plan(ctx, args):
    brief, origin = ctx.brief()
    errors, warnings = C.semantic_validate_brief(brief, ctx.cfg, ctx.policy, ctx.schema)
    if errors and args.strict:
        return emit(args, "plan", status="error", errors=errors,
                    recovery="Fix the brief or rerun without --strict.", exit_code=EXIT_CODES["validation"])
    rows, meta = P.route(ctx.registry, brief, ctx.cfg, brief.get("capabilities"))
    plan = P.build_plan(ctx.registry, brief, ctx.cfg, ctx.policy, rows, gates=ctx.gates_def)
    plan["validation"] = {"errors": errors, "warnings": warnings}
    if errors:
        plan["warnings"].append("Brief has validation errors; execution will still be simulated in dry-run.")
    path = save_artifact(args.output, plan)
    return emit(args, "plan", data={"kind": "plan-result", "origin": origin, "plan": plan,
                                    "artifact": path, "route_meta": meta}, warnings=warnings)


def cmd_prompt(ctx, args):
    brief, origin = ctx.brief()
    errors, warnings = C.semantic_validate_brief(brief, ctx.cfg, ctx.policy, ctx.schema)
    if errors and args.strict:
        return emit(args, "prompt", status="error", errors=errors, exit_code=EXIT_CODES["validation"])
    ps = P.compile_prompt(brief, ctx.style, ctx.cfg, variants=args.variants)
    path = save_artifact(args.output, ps)
    md = save_artifact(args.markdown, None, text=P.prompt_markdown(ps))
    return emit(args, "prompt", data={"kind": "prompt-result", "origin": origin, "prompt_set": ps,
                                      "variants": len(ps["variants"]), "artifact": path, "markdown": md,
                                      "preview": ps["variants"][0]["positive_prompt"][:220]},
                warnings=warnings)


def cmd_shotlist(ctx, args):
    brief, origin = ctx.brief()
    errors, warnings = C.semantic_validate_brief(brief, ctx.cfg, ctx.policy, ctx.schema)
    if errors and args.strict:
        return emit(args, "shotlist", status="error", errors=errors, exit_code=EXIT_CODES["validation"])
    sl = P.shotlist(brief, ctx.style, ctx.cfg)
    path = save_artifact(args.output, sl)
    md = save_artifact(args.markdown, None, text=P.shotlist_markdown(sl))
    return emit(args, "shotlist", data={"kind": "shotlist-result", "origin": origin, "shot_list": sl,
                                        "shot_count": sl["shot_count"], "artifact": path, "markdown": md,
                                        "preview": [s["id"] + " " + s["in_timecode"] for s in sl["shots"][:4]]},
                warnings=warnings)


def cmd_captions(ctx, args):
    raw, origin = C.load_user_input(args.input, args.text,
                                    max_bytes=int(ctx.cfg["limits"]["max_input_bytes"]))
    cues, fmt = M.parse_captions(raw, args.format_input, int(ctx.cfg["limits"]["max_caption_cues"]))
    rep = M.validate_captions(cues, ctx.cfg)
    rep["origin"] = origin
    path = save_artifact(args.output, rep)
    srt = save_artifact(args.srt, None, text=M.to_srt(cues))
    vtt = save_artifact(args.vtt, None, text=M.to_vtt(cues))
    return emit(args, "captions", status="ok" if rep["status"] != "fail" else "error",
                data={"kind": "captions-result", "origin": origin, "detected_format": fmt,
                      "cue_count": rep["cue_count"], "status": rep["status"], "report": rep,
                      "artifact": path, "srt": srt, "vtt": vtt,
                      "preview": [c["text"][:60] for c in cues[:3]]},
                warnings=[{"code": x["code"], "message": x["message"]}
                          for x in rep["issues"] if x["severity"] == "warning"],
                errors=[{"code": x["code"], "message": x["message"]}
                        for x in rep["issues"] if x["severity"] == "error"],
                exit_code=0 if rep["status"] != "fail" else EXIT_CODES["validation"])


def cmd_validate(ctx, args):
    value, origin = C.load_user_json(args.input, args.text,
                                     max_bytes=int(ctx.cfg["limits"]["max_input_bytes"]))
    errors, warnings = C.semantic_validate_brief(value, ctx.cfg, ctx.policy, ctx.schema)
    rep = {"kind": "validation-report", "origin": origin, "status": "fail" if errors else "pass",
           "errors": errors, "warnings": warnings,
           "schema": C.resolve_resource(ctx.resources["brief_schema"], ctx.root).name}
    path = save_artifact(args.output, rep)
    return emit(args, "validate", status="ok" if not errors else "error",
                data={"kind": "validation-result", "origin": origin, "status": rep["status"],
                      "error_count": len(errors), "warning_count": len(warnings), "report": rep,
                      "artifact": path},
                errors=errors, warnings=warnings,
                recovery="Fix the listed paths in the brief; see /asset-brief-schema.json." if errors else None,
                exit_code=0 if not errors else EXIT_CODES["validation"])


def cmd_gates(ctx, args):
    job, origin = C.load_user_json(args.input, args.text,
                                   max_bytes=int(ctx.cfg["limits"]["max_input_bytes"]))
    artifacts = {}
    for key in ("prompt", "shotlist", "captions", "validation", "delivery"):
        p = getattr(args, "artifact_" + key, None)
        if p:
            artifacts[key] = C.load_user_json(path=p)[0]
    approvals = {"verified": bool(args.approval_token)}
    if args.approval_token:
        ok, _ = C.verify_approval_token(args.approval_token, args.store)
        approvals["verified"] = ok
    rep = M.evaluate_gates(job, ctx.gates_def, ctx.cfg, artifacts, approvals)
    rep["origin"] = origin
    path = save_artifact(args.output, rep)
    return emit(args, "gates", status="ok" if rep["status"] == "pass" else "error",
                data={"kind": "gates-result", "origin": origin, "status": rep["status"],
                      "failed": rep["failed"], "deferred": rep["deferred"], "report": rep,
                      "artifact": path, "preview": [g["id"] + "=" + g["status"] for g in rep["gates"]]},
                recovery="Remediate failed gates using their remediation strings, then rerun." if rep["failed"] else None,
                exit_code=0 if rep["status"] == "pass" else EXIT_CODES["validation"])


def cmd_run(ctx, args):
    if args.capability:
        cap, suggestions = K.find_capability(ctx.registry, args.capability)
        if not cap:
            return emit(args, "run", status="error",
                        data={"capability": args.capability, "suggestions": suggestions},
                        errors=[{"code": "CAPABILITY_NOT_FOUND", "message": "No matching capability."}],
                        recovery="Use 'capabilities --query <term>' to find an exact id or name.",
                        exit_code=EXIT_CODES["not_found"])
        if args.input or args.text:
            brief, origin = C.load_user_json(args.input, args.text,
                                             max_bytes=int(ctx.cfg["limits"]["max_input_bytes"]))
        else:
            brief = {"title": cap["name"],
                     "objective": "Execute the %s capability offline with structured, editable outputs." % cap["name"],
                     "duration_seconds": 8, "fps": 24, "aspect_ratio": "16:9",
                     "resolution": "1920x1080", "style_preset": "cinematic",
                     "provider": args.provider or ctx.cfg["providers"]["default"],
                     "captions_required": False}
            origin = "generated-minimal-brief"
        plan = P.build_single_capability_plan(ctx.registry, brief, ctx.cfg, ctx.policy, cap,
                                              ctx.gates_def, args.provider)
    else:
        plan, origin = C.load_user_json(args.input, args.text,
                                        max_bytes=int(ctx.cfg["limits"]["max_input_bytes"]))
    if args.test_mode and not ctx.cfg.get("testing", {}).get("allow_fault_injection"):
        return emit(args, "run", status="error",
                    errors=[{"code": "FAULT_INJECTION_DISABLED",
                             "message": "Set testing.allow_fault_injection=true in --config for --test-mode."}],
                    exit_code=EXIT_CODES["validation"])
    rt = ctx.runtime()
    res = rt.execute(plan, args.output_dir, mode=args.mode, provider=args.provider,
                     approval_token=args.approval_token, approvals_file=args.store,
                     resume=args.resume, fault_stage=args.fail_stage)
    status_map = {"completed": "ok", "simulated": "ok", "error": "error", "needs_approval": "needs_approval",
                  "cancelled": "cancelled"}
    code = {"error": EXIT_CODES["runtime"], "needs_approval": EXIT_CODES["approval"],
            "cancelled": EXIT_CODES["validation"]}.get(res["status"], 0)
    return emit(args, "run", status=status_map.get(res["status"], res["status"]),
                data={"kind": "run-result", "origin": origin, **res},
                warnings=[{"code": "RUN_WARNING", "message": w} for w in res["warnings"]],
                errors=[{"code": res["failed"]["code"], "message": res["failed"]["message"]}] if res["failed"] else [],
                recovery=res["recovery"], exit_code=code)


def cmd_approve(ctx, args):
    rt = ctx.runtime()
    if args.revoke:
        rec = rt.revoke_approval(args.store, args.revoke, confirm=args.confirm)
    elif args.list:
        rec = rt.list_approvals(args.store)
    else:
        if not args.capability or not args.reason:
            return emit(args, "approve", status="error",
                        errors=[{"code": "MISSING_APPROVAL_FIELDS",
                                 "message": "--capability and --reason are required unless --list or --revoke is used."}],
                        exit_code=EXIT_CODES["validation"])
        rec = rt.record_approval(args.store, args.capability, args.reason, actor=args.actor,
                                 ttl_hours=args.ttl_hours, confirm=args.confirm)
    path = save_artifact(args.output, rec)
    return emit(args, "approve", data={"kind": "approve-result", "result": rec, "artifact": path,
                                       "preview": rec.get("token") or rec.get("status")})


def cmd_deliver(ctx, args):
    brief, origin = ctx.brief()
    errors, warnings = C.semantic_validate_brief(brief, ctx.cfg, ctx.policy, ctx.schema)
    if errors and args.strict:
        return emit(args, "deliver", status="error", errors=errors, exit_code=EXIT_CODES["validation"])
    man = M.build_delivery_manifest({**brief, "job_id": brief.get("job_id", C.deterministic_id(brief)),
                                     "umbrella": ctx.registry.get("umbrella"),
                                     "provider": brief.get("provider", ctx.cfg["providers"]["default"])},
                                    ctx.delivery, ctx.cfg, args.artifacts_dir)
    path = save_artifact(args.output, man)
    return emit(args, "deliver", data={"kind": "deliver-result", "origin": origin, "manifest": man,
                                       "file_count": man["file_count"], "total_bytes": man["total_bytes"],
                                       "artifact": path, "preview": [f["path"] for f in man["files"][:5]]},
                warnings=[{"code": "DELIVERY_NOTE", "message": w} for w in man["warnings"]])


def cmd_report(ctx, args):
    rep = ctx.runtime().report(args.run_dir)
    path = save_artifact(args.output, rep)
    return emit(args, "report", data={"kind": "report-result", "summary": rep, "artifact": path,
                                      "preview": rep["stage_status_counts"]})


def cmd_selftest(ctx, args):
    results = S.run_all(ctx, args)
    path = save_artifact(args.output, results)
    failed = results.get("failed", 0)
    return emit(args, "selftest", status="ok" if failed == 0 else "error",
                data={"kind": "selftest-result", "passed": results.get("passed", 0), "failed": failed,
                      "tests": results.get("tests", []), "artifact": path},
                errors=[{"code": "SELFTEST_FAILURE", "message": t["detail"]}
                        for t in results.get("tests", []) if t["status"] != "pass"],
                exit_code=0 if failed == 0 else EXIT_CODES["validation"])


def cmd_doctor(ctx, args):
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "status": "pass" if ok else "fail", "detail": str(detail)})

    add("python", sys.version_info >= (3, 9), "Python %s" % sys.version.split()[0])
    add("config", True, "%s (version %s)" % (ctx.cfg_origin, ctx.cfg.get("config_version")))
    add("brief schema", bool(ctx.schema.get("properties")), "%d properties" % len(ctx.schema.get("properties", {})))
    issues = K.validate_registry(ctx.registry)
    add("registry", not issues, "%d categories / %d capabilities; %s" %
        (len(ctx.registry.get("categories", [])), len(ctx.registry.get("capabilities", [])),
         "valid" if not issues else issues[:3]))
    missing = []
    for key, ref in ctx.resources.items():
        try:
            p = C.resolve_resource(ref, ctx.root)
            if key.endswith("json"):
                json.loads(p.read_text(encoding="utf-8"))
            add("resource " + key, True, "%s -> %s" % (ref, p.name))
        except Exception as exc:
            missing.append(key)
            add("resource " + key, False, "%s: %s" % (ref, exc))
    folder = TOOL_DIR
    subs = [p.name for p in folder.iterdir() if p.is_dir()]
    unprefixed = [p.name for p in folder.iterdir() if p.is_file() and not
                  p.name.startswith(("tools-", "knowledge-", "asset-"))]
    knowledge = [p.name for p in folder.iterdir() if p.name.startswith("knowledge-")]
    add("folder layout", not subs and not unprefixed and len(knowledge) == 1 and not (folder / "SKILL.md").exists(),
        "subfolders=%s unprefixed=%s knowledge=%s SKILL.md=%s" %
        (subs, unprefixed, knowledge, (folder / "SKILL.md").exists()))
    add("providers", ctx.cfg["providers"].get("allow_external") is False,
        "enabled=%s network=%s shell=%s" % (ctx.cfg["providers"]["enabled"],
                                            ctx.cfg["providers"].get("network"), ctx.cfg["providers"].get("shell")))
    add("approval store", True, ctx.cfg["output"]["approvals_file"])
    overall = "ok" if all(c["status"] == "pass" for c in checks) else "error"
    return emit(args, "doctor", status=overall,
                data={"kind": "doctor-report", "status": overall, "python": sys.version.split()[0],
                      "tool_dir": TOOL_FOLDER, "checks": checks, "file_count": len(list(folder.iterdir()))},
                errors=[{"code": "DOCTOR_FAILURE", "message": c["detail"]}
                        for c in checks if c["status"] == "fail"],
                exit_code=0 if overall == "ok" else EXIT_CODES["runtime"])


HANDLERS = {
    "help": cmd_help, "capabilities": cmd_capabilities, "ingest": cmd_ingest,
    "describe": cmd_describe, "route": cmd_route, "plan": cmd_plan, "prompt": cmd_prompt,
    "shotlist": cmd_shotlist, "captions": cmd_captions, "validate": cmd_validate,
    "gates": cmd_gates, "run": cmd_run, "approve": cmd_approve, "deliver": cmd_deliver,
    "report": cmd_report, "selftest": cmd_selftest, "doctor": cmd_doctor,
}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="Future skill root that holds /asset-*.json files (default: parent of the tool folder).")
    common.add_argument("--config", help="Path to a JSON config override merged over /asset-default-config.json.")
    common.add_argument("--catalog", help="Path to an ingested registry JSON to use instead of the bundled catalog.")
    common.add_argument("--format", choices=["json", "text"], default="json", help="Structured JSON (default) or human text.")
    common.add_argument("--output", help="Primary artifact file for this command (JSON, or Markdown where noted).")

    p = argparse.ArgumentParser(prog="tools-ai-video.py",
                                description="Unified AI video creation Skill Tool component (primary entry point).",
                                epilog="Run 'help' or 'capabilities' first. All references are skill-root-relative.")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, help_text, extra=None, parents=(common,)):
        sp = sub.add_parser(name, help=help_text, parents=list(parents))
        if extra:
            extra(sp)
        return sp

    add("help", COMMANDS[0][1])
    c = add("capabilities", COMMANDS[1][1])
    c.add_argument("--query", default="", help="Filter capabilities by name/category/description.")
    c.add_argument("--category", default="", help="Filter by category name or number.")
    c.add_argument("--family", default="", help="Filter by operation family.")
    c.add_argument("--limit", type=int, default=20, help="Maximum capabilities to show.")
    c.add_argument("--offset", type=int, default=0, help="Skip this many matches.")
    c.add_argument("--categories-only", action="store_true", help="Show only category names and counts.")

    i = add("ingest", COMMANDS[2][1])
    i.add_argument("--input", help="Source file: plain text, Markdown, or copied HTML.")
    i.add_argument("--text", help="Inline source text instead of a file.")
    i.add_argument("--name", help="Override the umbrella name (lowercase-hyphenated).")

    d = add("describe", COMMANDS[3][1])
    d.add_argument("--capability", required=True, help="Capability id (c17-s01), slug, or exact name.")

    def brief_args(sp):
        sp.add_argument("--input", help="Brief JSON file, e.g. /asset-example-brief.json (as a real path).")
        sp.add_argument("--text", help="Inline brief JSON instead of a file.")
        sp.add_argument("--strict", action="store_true", help="Fail on brief validation errors.")

    r = add("route", COMMANDS[4][1]); brief_args(r)
    pl = add("plan", COMMANDS[5][1]); brief_args(pl)
    pr = add("prompt", COMMANDS[6][1]); brief_args(pr)
    pr.add_argument("--variants", type=int, default=1, help="Number of prompt variants (bounded by config).")
    pr.add_argument("--markdown", help="Write a human-readable prompt Markdown file here.")
    sh = add("shotlist", COMMANDS[7][1]); brief_args(sh)
    sh.add_argument("--markdown", help="Write a human-readable shot list + EDL Markdown file here.")

    ca = add("captions", COMMANDS[8][1])
    ca.add_argument("--input", help="SRT/VTT/JSON caption file.")
    ca.add_argument("--text", help="Inline caption text.")
    ca.add_argument("--format-input", choices=["auto", "srt", "vtt", "json"], default="auto", help="Caption source format.")
    ca.add_argument("--srt", help="Write normalized SRT here.")
    ca.add_argument("--vtt", help="Write normalized WebVTT here.")

    v = add("validate", COMMANDS[9][1])
    v.add_argument("--input", help="Brief/job JSON file to validate.")
    v.add_argument("--text", help="Inline JSON to validate.")

    g = add("gates", COMMANDS[10][1])
    g.add_argument("--input", help="Job JSON (brief + optional plan) to evaluate.")
    g.add_argument("--text", help="Inline job JSON.")
    g.add_argument("--artifact-prompt", help="Prompt-set JSON artifact.")
    g.add_argument("--artifact-shotlist", help="Shot-list JSON artifact.")
    g.add_argument("--artifact-captions", help="Caption report JSON artifact.")
    g.add_argument("--artifact-validation", help="Validation report JSON artifact.")
    g.add_argument("--artifact-delivery", help="Delivery manifest JSON artifact.")
    g.add_argument("--approval-token", help="Approval token to satisfy rights-consent.")
    g.add_argument("--store", default="approvals.json", help="Approval store path.")

    ru = add("run", COMMANDS[11][1])
    ru.add_argument("--input", help="Plan JSON produced by 'plan' (or a brief JSON when using --capability).")
    ru.add_argument("--text", help="Inline plan JSON (or inline brief JSON).")
    ru.add_argument("--capability", help="Run ANY single catalog capability directly as a one-stage job.")
    ru.add_argument("--output-dir", default="artifacts", help="Directory for stage artifacts and checkpoint.json.")
    ru.add_argument("--mode", choices=["dry-run", "execute"], default="dry-run", help="Default is dry-run.")
    ru.add_argument("--provider", help="Provider adapter name (default: local).")
    ru.add_argument("--approval-token", help="Token from 'approve' for sensitive stages.")
    ru.add_argument("--store", default="approvals.json", help="Approval store path.")
    ru.add_argument("--resume", action="store_true", help="Continue from checkpoint.json, keeping completed stages.")
    ru.add_argument("--test-mode", action="store_true", help="Permit --fail-stage when config allows fault injection.")
    ru.add_argument("--fail-stage", help="Inject a retryable stage failure for recovery drills.")

    ap = add("approve", COMMANDS[12][1])
    ap.add_argument("--capability", help="Capability id or name to approve.")
    ap.add_argument("--reason", help="Human-visible reason for the approval.")
    ap.add_argument("--actor", default="operator", help="Who approved.")
    ap.add_argument("--ttl-hours", type=float, default=24, help="Token lifetime in hours.")
    ap.add_argument("--confirm", action="store_true", help="Required for recording or revoking an approval.")
    ap.add_argument("--store", default="approvals.json", help="Approval store path.")
    ap.add_argument("--revoke", help="Revoke a token.")
    ap.add_argument("--list", action="store_true", help="List recorded approvals.")

    de = add("deliver", COMMANDS[13][1]); brief_args(de)
    de.add_argument("--artifacts-dir", default="artifacts", help="Directory of local artifacts to checksum.")

    re = add("report", COMMANDS[14][1])
    re.add_argument("--run-dir", default="artifacts", help="Run directory containing checkpoint.json.")

    add("selftest", COMMANDS[15][1])
    add("doctor", COMMANDS[16][1])
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        ctx = Context(args)
        args.cfg = ctx.cfg
        args.cfg_origin = ctx.cfg_origin
        if args.command == "help":
            return cmd_help(ctx, args)
        return HANDLERS[args.command](ctx, args)
    except C.ToolError as exc:
        env = C.envelope(args.command, "error", {"kind": "error"},
                         errors=[exc.as_dict()], recovery=exc.recovery)
        print(json.dumps(env, indent=2, ensure_ascii=False))
        code = EXIT_CODES["validation"] if exc.code.startswith(("INPUT", "INVALID", "RESOURCE", "MISSING", "EMBEDDED", "UNSAFE", "TOO")) else EXIT_CODES["runtime"]
        return code
    except R.StageError as exc:
        env = C.envelope(args.command, "error", {"kind": "error"},
                         errors=[{"code": exc.code, "message": exc.message}],
                         recovery="Fix the reported cause and rerun; use --resume for plan execution.")
        print(json.dumps(env, indent=2, ensure_ascii=False))
        return EXIT_CODES["runtime"]
    except (FileNotFoundError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        env = C.envelope(args.command, "error", {"kind": "error"},
                         errors=[{"code": type(exc).__name__.upper(), "message": str(exc)}],
                         recovery="Check the command arguments and the referenced files.")
        print(json.dumps(env, indent=2, ensure_ascii=False))
        return EXIT_CODES["validation"]
    except KeyboardInterrupt:
        env = C.envelope(args.command, "cancelled", errors=[{"code": "INTERRUPTED", "message": "Interrupted by user."}])
        print(json.dumps(env, indent=2, ensure_ascii=False))
        return 130
    except Exception as exc:  # pragma: no cover - last-resort structured failure
        env = C.envelope(args.command, "error", {"kind": "error"},
                         errors=[{"code": "UNEXPECTED", "message": "%s: %s" % (type(exc).__name__, exc),
                                  "trace": traceback.format_exc(limit=3)}])
        print(json.dumps(env, indent=2, ensure_ascii=False))
        return EXIT_CODES["internal"]


if __name__ == "__main__":
    sys.exit(main())
