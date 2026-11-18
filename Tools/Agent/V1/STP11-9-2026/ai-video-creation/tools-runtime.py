"""ai-video-creation / tools-runtime.py

Bounded, reproducible execution engine for the unified production plan.

Guarantees implemented here (not just documented):
  * dry-run is the default; --mode execute is required to write artifacts
  * no shell, no subprocess, no network, no arbitrary code execution
  * provider allowlist; unconfigured providers fail with a structured error
  * human approval gate for sensitive stages, verified against a local store
  * per-stage and whole-job timeouts, bounded retries, cancellation file,
    .partial cleanup, checkpointing, and --resume recovery
  * earlier successful stages are preserved on failure

All sibling modules are injected by the primary entry point (dependency
injection), keeping this module importable on its own.
"""

from __future__ import annotations

import datetime
import json
import re
import secrets
import time
from pathlib import Path


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


class StageError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class Runtime:
    def __init__(self, config, policy, gates_def, style_presets, delivery_presets,
                 brief_schema, registry, deps, root=None):
        self.config = config
        self.policy = policy
        self.gates_def = gates_def
        self.style_presets = style_presets
        self.delivery_presets = delivery_presets
        self.brief_schema = brief_schema
        self.registry = registry
        self.deps = deps  # {"contracts":..., "planner":..., "media":..., "catalog":...}
        self.root = root
        self.out = config.get("output", {})
        self.retry_cfg = config.get("retries", {})
        self.timeouts = config.get("timeouts", {})

    # ------------------------------------------------------------------
    # Approval store
    # ------------------------------------------------------------------

    def record_approval(self, approvals_file, capability_id, reason, actor="operator",
                        ttl_hours=24, confirm=False, brief=None):
        if not confirm:
            raise StageError("CONFIRMATION_REQUIRED",
                             "Recording an approval is a privileged action; pass --confirm explicitly.")
        if not reason or not capability_id:
            raise StageError("INVALID_APPROVAL", "--capability and --reason are required.")
        p = Path(approvals_file).expanduser()
        store = {"schema_version": "1.0", "approvals": []}
        if p.is_file():
            try:
                store = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise StageError("APPROVAL_STORE_INVALID", "Approval store is not valid JSON.")
        wildcard = str(capability_id).strip().lower() in ("*", "all", "any")
        cid = None if wildcard else self.deps["catalog"].find_capability(self.registry, capability_id)[0]
        if not wildcard and not cid:
            raise StageError("UNKNOWN_CAPABILITY", "Capability '%s' is not in the registry." % capability_id)
        expires = (datetime.datetime.now(datetime.timezone.utc) +
                   datetime.timedelta(hours=float(ttl_hours))).replace(microsecond=0).isoformat()
        rec = {
            "token": secrets.token_hex(16),
            "capability_id": "*" if wildcard else cid["id"],
            "capability_name": "all capabilities (job-wide)" if wildcard else cid["name"],
            "reason": reason,
            "actor": actor,
            "brief_id": self.deps["contracts"].deterministic_id(json.dumps(brief, sort_keys=True)) if brief else None,
            "issued_at": _now(),
            "expires_at": expires,
            "revoked": False,
        }
        store.setdefault("approvals", []).append(rec)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".partial")
        tmp.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)
        return {"kind": "approval-record", "status": "recorded", "store": str(p),
                "token": rec["token"],
                "capability": rec["capability_name"], "expires_at": expires,
                "scope": rec["capability_id"],
                "note": "Pass this token with --approval-token for sensitive stages. Use --capability all for job-wide scope."}

    def revoke_approval(self, approvals_file, token, confirm=False):
        if not confirm:
            raise StageError("CONFIRMATION_REQUIRED", "Revocation requires --confirm.")
        p = Path(approvals_file).expanduser()
        if not p.is_file():
            raise StageError("APPROVAL_STORE_MISSING", "No approval store at %s" % p)
        store = json.loads(p.read_text(encoding="utf-8"))
        found = 0
        for rec in store.get("approvals", []):
            if rec.get("token") == token:
                rec["revoked"] = True
                found += 1
        if not found:
            raise StageError("TOKEN_NOT_FOUND", "Token not present in the approval store.")
        p.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
        return {"kind": "approval-revocation", "status": "revoked", "count": found, "store": str(p)}

    def list_approvals(self, approvals_file):
        p = Path(approvals_file).expanduser()
        if not p.is_file():
            return {"kind": "approval-list", "status": "empty", "approvals": [], "store": str(p)}
        store = json.loads(p.read_text(encoding="utf-8"))
        rows = [{k: r.get(k) for k in ("token", "capability_name", "reason", "actor", "issued_at",
                                       "expires_at", "revoked")} for r in store.get("approvals", [])]
        return {"kind": "approval-list", "status": "ok", "approvals": rows, "store": str(p)}

    # ------------------------------------------------------------------
    # Stage execution
    # ------------------------------------------------------------------

    def _artifacts_so_far(self, out_dir):
        loaded = {}
        for key, name in (("prompt", "stage-prompt.json"), ("shotlist", "stage-shotlist.json"),
                          ("captions", "stage-captions.json"), ("validation", "stage-validation.json"),
                          ("delivery", "stage-delivery.json")):
            p = out_dir / name
            if p.is_file():
                try:
                    loaded[key] = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
        return loaded

    def _write_stage_file(self, out_dir, name, obj, text=None):
        p = out_dir / name
        body = text if text is not None else json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
        max_bytes = int(self.config.get("limits", {}).get("max_output_bytes", 8 * 1024 * 1024))
        if len(body.encode("utf-8")) > max_bytes:
            raise StageError("OUTPUT_TOO_LARGE", "Stage output exceeds %d bytes." % max_bytes)
        tmp = p.with_name(p.name + ".partial")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(p)
        return p.name

    def _caption_source(self, brief, out_dir):
        """Real caption input: inline text, a user path, or shot-derived cues."""
        raw = brief.get("captions_text")
        if raw:
            return raw, "brief.captions_text"
        src = brief.get("captions_source")
        if src:
            p = Path(src).expanduser()
            if p.is_file():
                return p.read_text(encoding="utf-8"), str(p)
        shot_path = out_dir / "stage-shotlist.json"
        if shot_path.is_file():
            try:
                sl = json.loads(shot_path.read_text(encoding="utf-8"))
                cues = [{"index": i, "start": s["start_seconds"], "end": s["end_seconds"],
                         "text": s["description"][:90]} for i, s in enumerate(sl.get("shots", []), 1)]
                if cues:
                    return json.dumps(cues), "shot-derived JSON cues"
            except (OSError, json.JSONDecodeError, KeyError):
                pass
        return json.dumps([{"index": 1, "start": 0.0, "end": min(4.0, float(brief.get("duration_seconds", 4))),
                            "text": str(brief.get("objective", "Describe the on-screen action."))[:90]}]), "brief objective"

    def _run_stage(self, stage, plan, out_dir, brief, mode, provider, approval_verified, fault_stage=None):
        """Produce real editable artifacts. Returns (files, detail)."""
        op = stage.get("operation", "contract")
        files, detail = [], {"operation": op, "provider": provider}
        C, P, M = self.deps["contracts"], self.deps["planner"], self.deps["media"]
        if fault_stage and fault_stage == stage["id"]:
            raise StageError("INJECTED_STAGE_ERROR", "Fault injected for recovery testing at stage '%s'." % stage["id"],
                             retryable=True)

        if op == "compile-prompt":
            ps = P.compile_prompt(brief, self.style_presets, self.config,
                                  variants=int(brief.get("prompt_variants", 1)))
            files.append(self._write_stage_file(out_dir, "stage-prompt.json", ps))
            files.append(self._write_stage_file(out_dir, "stage-prompt.md", None, P.prompt_markdown(ps)))
            detail["variants"] = len(ps["variants"])
            detail["ambiguity"] = ps["ambiguity_audit"]["status"]
        elif op == "shotlist":
            sl = P.shotlist(brief, self.style_presets, self.config)
            files.append(self._write_stage_file(out_dir, "stage-shotlist.json", sl))
            files.append(self._write_stage_file(out_dir, "stage-shotlist.md", None, P.shotlist_markdown(sl)))
            detail["shots"] = sl["shot_count"]
        elif op == "captions":
            raw, origin = self._caption_source(brief, out_dir)
            cues, fmt = M.parse_captions(raw, max_cues=int(self.config["limits"]["max_caption_cues"]))
            rep = M.validate_captions(cues, self.config)
            rep["source"] = origin
            rep["detected_format"] = fmt
            files.append(self._write_stage_file(out_dir, "stage-captions.json", rep))
            files.append(self._write_stage_file(out_dir, "stage-captions.srt", None, M.to_srt(cues)))
            files.append(self._write_stage_file(out_dir, "stage-captions.vtt", None, M.to_vtt(cues)))
            detail["cue_count"] = rep["cue_count"]
            detail["status"] = rep["status"]
        elif op == "validate":
            errors, warnings = C.semantic_validate_brief(brief, self.config, self.policy, self.brief_schema)
            rep = {"kind": "validation-report", "status": "fail" if errors else "pass",
                   "errors": errors, "warnings": warnings, "checked_at": _now()}
            files.append(self._write_stage_file(out_dir, "stage-validation.json", rep))
            detail["errors"] = len(errors)
            detail["warnings"] = len(warnings)
        elif op == "gates":
            artifacts = self._artifacts_so_far(out_dir)
            job = {"brief": brief, "plan": plan, "provider": provider}
            rep = M.evaluate_gates(job, self.gates_def, self.config, artifacts,
                                   approvals={"verified": approval_verified})
            files.append(self._write_stage_file(out_dir, "stage-gates.json", rep))
            detail["status"] = rep["status"]
            detail["failed"] = rep["failed"]
        elif op == "delivery":
            rep = M.build_delivery_manifest({**brief, "job_id": plan.get("job_id"),
                                             "umbrella": plan.get("umbrella"),
                                             "provider": provider},
                                            self.delivery_presets, self.config, out_dir,
                                            self._artifacts_so_far(out_dir))
            files.append(self._write_stage_file(out_dir, "stage-delivery.json", rep))
            detail["file_count"] = rep["file_count"]
        elif op == "approval":
            rep = {"kind": "approval-check", "status": "verified" if approval_verified else "pending",
                   "reasons": stage.get("approval_reasons", []),
                   "capabilities": [c["name"] for c in stage.get("capabilities", [])],
                   "note": "Sensitive capability: human review is mandatory for generative execution."}
            files.append(self._write_stage_file(out_dir, "stage-approval.json", rep))
            detail["status"] = rep["status"]
        elif op == "plan":
            excerpt = {"kind": "stage-plan", "stage": stage, "job_id": plan.get("job_id"),
                       "stage_order": plan.get("stage_order", [])}
            files.append(self._write_stage_file(out_dir, "stage-plan.json", excerpt))
            detail["stage_count"] = len(plan.get("stage_order", []))
        else:  # "contract": the editable job envelope used by generative capabilities
            cap_id = (stage.get("capabilities") or [{}])[0].get("id")
            cap = next((c for c in self.registry.get("capabilities", []) if c["id"] == cap_id), None)
            if cap is None:
                cap = {"id": "unassigned", "name": stage["name"], "slug": re.sub(r"[^a-z0-9]+", "-", stage["name"].lower()),
                       "category": "Unified capabilities", "family": "general", "local_operation": "contract",
                       "quality_gates": list(self.gates_def.get("default_gate_ids", []))}
            contract = C.build_contract(cap, self.config, self.policy, self.gates_def, brief)
            env = {"kind": "job-envelope", "status": "spec_ready", "job_id": plan.get("job_id"),
                   "stage": stage["id"], "contract": contract,
                   "assumptions": ["No generative model is bundled with this Skill Tool.",
                                   "All unspecified details are left for operator review."],
                   "dependencies": ["configured provider adapter", "approved brief", "reference rights metadata"],
                   "unresolved_issues": ["Generative media execution requires provider '%s'." % provider],
                   "provider_required": provider != "local"}
            files.append(self._write_stage_file(out_dir, "stage-contract.json", env))
            detail["capability"] = cap.get("name")
            detail["status"] = "spec_ready"
        return files, detail

    def _cleanup_partials(self, out_dir):
        removed = []
        for p in out_dir.glob("*.partial"):
            try:
                p.unlink()
                removed.append(p.name)
            except OSError:
                pass
        return removed

    def _checkpoint_path(self, out_dir):
        return out_dir / self.out.get("checkpoint_name", "checkpoint.json")

    def _save_checkpoint(self, out_dir, state):
        self._write_stage_file(out_dir, self.out.get("checkpoint_name", "checkpoint.json"), state)

    def execute(self, plan, out_dir, mode="dry-run", provider=None, approval_token=None,
                approvals_file="approvals.json", resume=False, fault_stage=None):
        """Execute or simulate a plan. Returns a structured result dictionary."""
        if not isinstance(plan, dict) or not plan.get("stages"):
            raise StageError("INVALID_PLAN", "Plan must contain a non-empty 'stages' array.")
        provider = provider or self.config["providers"]["default"]
        enabled = self.config["providers"].get("enabled", [])
        out_dir = Path(out_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        cp_path = self._checkpoint_path(out_dir)
        job_id = plan.get("job_id", "unknown")
        if resume and cp_path.is_file():
            state = json.loads(cp_path.read_text(encoding="utf-8"))
            if state.get("job_id") != job_id:
                raise StageError("RESUME_JOB_MISMATCH", "Checkpoint belongs to a different job_id.")
        else:
            state = {"schema_version": "1.0", "kind": "run-checkpoint", "job_id": job_id,
                     "created_at": _now(), "updated_at": _now(), "mode": mode, "provider": provider,
                     "status": "running", "stages": {}, "earliest_failure": None, "recovery": None}
        brief = plan.get("brief", {}) or {}
        token_ok = False
        if approval_token:
            token_ok, _ = self.deps["contracts"].verify_approval_token(approval_token, approvals_file)
        max_attempts = max(1, int(self.retry_cfg.get("max_attempts", 1)))
        backoff = float(self.retry_cfg.get("backoff_seconds", 0.0))
        deadline = time.monotonic() + float(self.timeouts.get("job_seconds", 180))
        cancel_file = out_dir / self.out.get("cancel_file_name", "CANCEL")
        cancel_file_run = Path.cwd() / self.out.get("cancel_file_name", "CANCEL")
        cancelled = False
        stages_by_id = {s["id"]: s for s in plan["stages"]}
        order = plan.get("stage_order") or [s["id"] for s in plan["stages"]]
        result = {"kind": "run-result", "job_id": job_id, "mode": mode, "provider": provider,
                  "output_dir": str(out_dir), "completed": [], "simulated": [], "skipped": [],
                  "failed": None, "needs_approval": None, "cancelled": False, "files": [],
                  "recovery": None, "warnings": []}

        for sid in order:
            stage = stages_by_id.get(sid)
            if not stage:
                continue
            if time.monotonic() > deadline:
                state["status"] = "timeout"
                state["earliest_failure"] = {"stage": sid, "code": "JOB_TIMEOUT"}
                state["recovery"] = "Raise timeouts.job_seconds in --config, then rerun with --resume."
                result["recovery"] = state["recovery"]
                result["failed"] = {"stage": sid, "code": "JOB_TIMEOUT",
                                    "message": "Whole-job deadline exceeded."}
                break
            if cancel_file.exists() or cancel_file_run.exists():
                cancelled = True
                result["cancelled"] = True
                state["status"] = "cancelled"
                state["recovery"] = "Remove the CANCEL file, then rerun with --resume to continue pending stages."
                result["recovery"] = state["recovery"]
                break
            prev = state["stages"].get(sid, {})
            if resume and prev.get("status") in ("completed", "simulated"):
                result["skipped"].append(sid)
                continue
            if mode == "execute" and stage.get("approval_required"):
                cap_id = (stage.get("capabilities") or [{}])[0].get("id")
                ok, why = self.deps["contracts"].verify_approval_token(
                    approval_token, approvals_file, capability_id=cap_id)
                if not ok:
                    result["needs_approval"] = {"stage": sid, "code": "APPROVAL_REQUIRED",
                                                "message": why,
                                                "capabilities": [c["name"] for c in stage.get("capabilities", [])]}
                    state["status"] = "needs_approval"
                    state["stages"][sid] = {"status": "needs_approval", "attempts": 0}
                    state["earliest_failure"] = {"stage": sid, "code": "APPROVAL_REQUIRED"}
                    state["recovery"] = ("Run: approve --confirm --capability <id> --reason <text> --output %s; "
                                         "then rerun with --approval-token <token> --resume." % approvals_file)
                    result["recovery"] = state["recovery"]
                    break
            if mode == "execute" and provider not in enabled:
                err = {"stage": sid, "code": "PROVIDER_UNAVAILABLE",
                       "message": "Provider '%s' is not enabled. Available: %s." % (provider, enabled)}
                result["failed"] = err
                state["status"] = "error"
                state["stages"][sid] = {"status": "failed", "attempts": 0, "error": err}
                if not state["earliest_failure"]:
                    state["earliest_failure"] = err
                state["recovery"] = ("Enable the provider in a --config override (or use --provider local), "
                                     "then rerun with --resume to keep completed stages.")
                result["recovery"] = state["recovery"]
                break
            if mode == "dry-run":
                state["stages"][sid] = {"status": "simulated", "attempts": 0,
                                        "planned_files": ["stage-%s.*" % stage.get("operation", "contract")],
                                        "capabilities": [c["name"] for c in stage.get("capabilities", [])]}
                result["simulated"].append(sid)
                continue
            # --mode execute: bounded retries with per-stage deadline
            stage_deadline = time.monotonic() + float(self.timeouts.get("stage_seconds", 30))
            last_error = None
            for attempt in range(1, max_attempts + 1):
                if time.monotonic() > min(deadline, stage_deadline):
                    last_error = StageError("STAGE_TIMEOUT", "Stage deadline exceeded.", retryable=False)
                    break
                try:
                    files, detail = self._run_stage(stage, plan, out_dir, brief, mode, provider,
                                                    token_ok, fault_stage=fault_stage)
                    state["stages"][sid] = {"status": "completed", "attempts": attempt,
                                            "files": files, "detail": detail}
                    result["completed"].append(sid)
                    result["files"].extend(files)
                    last_error = None
                    break
                except StageError as exc:
                    last_error = exc
                    if not exc.retryable or attempt == max_attempts:
                        break
                    time.sleep(min(backoff, 0.5))
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    last_error = StageError("STAGE_ERROR", "%s: %s" % (type(exc).__name__, exc), retryable=True)
                    if attempt == max_attempts:
                        break
                    time.sleep(min(backoff, 0.5))
            if last_error is not None:
                self._cleanup_partials(out_dir)
                err = {"stage": sid, "code": last_error.code, "message": last_error.message,
                       "attempts": max_attempts}
                result["failed"] = err
                state["status"] = "error"
                state["stages"][sid] = {"status": "failed", "attempts": max_attempts, "error": err}
                if not state["earliest_failure"]:
                    state["earliest_failure"] = err
                state["recovery"] = ("Fix the cause, then rerun with --resume %s; completed stages are preserved." % out_dir)
                result["recovery"] = state["recovery"]
                break

        removed = self._cleanup_partials(out_dir)
        if removed:
            result["warnings"].append("Removed partial files: %s" % ", ".join(removed))
        if result["failed"]:
            result["status"] = "error"
        elif result["needs_approval"]:
            result["status"] = "needs_approval"
        elif cancelled:
            result["status"] = "cancelled"
        elif mode == "dry-run":
            result["status"] = "simulated"
            state["status"] = "simulated"
        else:
            result["status"] = "completed"
            state["status"] = "completed"
        state["updated_at"] = _now()
        state["last_result"] = {k: v for k, v in result.items() if k != "files"}
        self._save_checkpoint(out_dir, state)
        report = {"kind": "run-report", "job_id": job_id, "status": result["status"],
                  "mode": mode, "provider": provider, "generated_at": _now(),
                  "stages": state["stages"], "earliest_failure": state["earliest_failure"],
                  "recovery": state["recovery"], "warnings": result["warnings"],
                  "cost": self.deps["media"].estimate_cost(plan, self.config)}
        self._write_stage_file(out_dir, self.out.get("report_name", "run-report.json"), report)
        result["files"].append(self.out.get("report_name", "run-report.json"))
        result["checkpoint"] = self.out.get("checkpoint_name", "checkpoint.json")
        return result

    def report(self, run_dir):
        d = Path(run_dir).expanduser()
        cp = d / self.out.get("checkpoint_name", "checkpoint.json")
        if not cp.is_file():
            raise StageError("RUN_NOT_FOUND", "No checkpoint.json in %s" % d)
        state = json.loads(cp.read_text(encoding="utf-8"))
        stages = state.get("stages", {})
        counts = {}
        for sid, s in stages.items():
            counts[s.get("status", "unknown")] = counts.get(s.get("status", "unknown"), 0) + 1
        files = sorted(p.name for p in d.iterdir() if p.is_file() and not p.name.endswith(".partial"))
        return {"kind": "run-summary", "job_id": state.get("job_id"), "status": state.get("status"),
                "mode": state.get("mode"), "provider": state.get("provider"),
                "created_at": state.get("created_at"), "updated_at": state.get("updated_at"),
                "stage_status_counts": counts, "stage_details": stages,
                "earliest_failure": state.get("earliest_failure"), "recovery": state.get("recovery"),
                "files": files,
                "next_steps": (["Fix the reported cause and rerun with --resume"] if state.get("status") in
                               ("error", "cancelled", "needs_approval", "timeout") else
                               ["Review editable artifacts", "Run gates", "Request approval before publishing"])}
