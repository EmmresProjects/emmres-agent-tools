#!/usr/bin/env python3
"""tools-oaa-ops.py -- planners, license compliance, registries and air-gap verification.

Loaded by `tools-oaa.py`. Registers:

  plan.quantization  memory/throughput plan for a quantization choice (real arithmetic)
  plan.hardware      model-to-host fit recommendation (RAM/VRAM/disk/cores)
  plan.context       context-window budget and truncation/summarization plan
  plan.capacity      offered load, service rate, workers, storage growth forecast
  airgap.check       static air-gap verification of a tree plus configuration
  registry.add       record a model/prompt/tool/dataset/artifact version with checksum
  registry.list      list registry entries by kind
  registry.license   license compliance decision for a declared use

All planners are deterministic calculators over documented formulas (see
knowledge-readme.md). They never contact a model or a network service.
"""
from __future__ import annotations

import os
import re

import importlib.util as _ilu
import sys as _sys

# Load the shared kernel exactly once per process so every module sees the same
# exception types, configuration objects and audit chain instance.
_KERNEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "tools-oaa-kernel.py")
if "oaa_kernel" in _sys.modules:
    K = _sys.modules["oaa_kernel"]
else:
    _spec = _ilu.spec_from_file_location("oaa_kernel", _KERNEL_PATH)
    K = _ilu.module_from_spec(_spec)
    _sys.modules["oaa_kernel"] = K
    _spec.loader.exec_module(K)

GIB = 1024.0 ** 3

# bytes per parameter by weight format (documented approximations)
FORMAT_BYTES = {
    "fp32": 4.0, "fp16": 2.0, "bf16": 2.0, "int8": 1.0, "int4": 0.5,
    "gptq-int4": 0.55, "awq-int4": 0.55, "gguf-q8_0": 0.85, "gguf-q5_k_m": 0.72,
    "gguf-q4_k_m": 0.60, "gguf-q4_0": 0.55,
}
FORMAT_QUALITY_RISK = {
    "fp32": ("reference", 0.0),
    "fp16": ("negligible", 0.005),
    "bf16": ("negligible", 0.005),
    "int8": ("low", 0.01),
    "int4": ("medium", 0.03),
    "gptq-int4": ("medium", 0.025),
    "awq-int4": ("medium", 0.025),
    "gguf-q8_0": ("low", 0.012),
    "gguf-q5_k_m": ("low", 0.015),
    "gguf-q4_k_m": ("medium", 0.025),
    "gguf-q4_0": ("high", 0.04),
}
FORMAT_SPEED_INDEX = {  # relative throughput index, CPU-only friendly formats lower
    "fp32": 1.0, "fp16": 1.6, "bf16": 1.6, "int8": 2.1, "int4": 2.4,
    "gptq-int4": 2.4, "awq-int4": 2.4, "gguf-q8_0": 1.9, "gguf-q5_k_m": 2.2,
    "gguf-q4_k_m": 2.6, "gguf-q4_0": 2.8,
}

LICENSES = {
    "apache-2.0": {"family": "permissive", "uses": ["internal", "redistribution",
                                                    "commercial", "derivative"],
                   "obligations": ["include license text", "state changes made",
                                   "retain NOTICE file if present"]},
    "mit": {"family": "permissive", "uses": ["internal", "redistribution",
                                             "commercial", "derivative"],
            "obligations": ["include copyright and license text"]},
    "bsd-3-clause": {"family": "permissive", "uses": ["internal", "redistribution",
                                                      "commercial", "derivative"],
                     "obligations": ["include copyright and license text",
                                     "do not use names for endorsement"]},
    "bsd-2-clause": {"family": "permissive", "uses": ["internal", "redistribution",
                                                      "commercial", "derivative"],
                     "obligations": ["include copyright and license text"]},
    "isc": {"family": "permissive", "uses": ["internal", "redistribution",
                                             "commercial", "derivative"],
            "obligations": ["include copyright and license text"]},
    "unlicense": {"family": "public-domain", "uses": ["internal", "redistribution",
                                                      "commercial", "derivative"],
                  "obligations": []},
    "cc0-1.0": {"family": "public-domain", "uses": ["internal", "redistribution",
                                                    "commercial", "derivative"],
                "obligations": []},
    "cc-by-4.0": {"family": "attribution", "uses": ["internal", "redistribution",
                                                    "commercial", "derivative"],
                  "obligations": ["attribute the source", "link the license",
                                  "indicate changes"]},
    "cc-by-sa-4.0": {"family": "share-alike", "uses": ["internal", "redistribution",
                                                       "commercial", "derivative"],
                     "obligations": ["attribute the source", "share adaptations "
                                     "under the same license"]},
    "cc-by-nc-4.0": {"family": "non-commercial", "uses": ["internal", "derivative"],
                     "obligations": ["attribute the source", "no commercial use",
                                     "no redistribution for commercial purposes"]},
    "gpl-3.0": {"family": "strong-copyleft", "uses": ["internal", "redistribution",
                                                      "commercial", "derivative"],
                "obligations": ["ship complete corresponding source",
                                "same license for derivatives",
                                "no additional restrictions"]},
    "agpl-3.0": {"family": "network-copyleft", "uses": ["internal", "redistribution",
                                                        "derivative"],
                 "obligations": ["offer source to network users",
                                 "same license for derivatives"],
                 "caveats": ["network use counts as distribution"]},
    "mpl-2.0": {"family": "weak-copyleft", "uses": ["internal", "redistribution",
                                                    "commercial", "derivative"],
                "obligations": ["publish modifications to MPL-covered files"]},
    "llama-3-community": {"family": "community", "uses": ["internal", "commercial",
                                                          "derivative"],
                          "obligations": ["include acceptable use policy",
                                          "display 'Built with Llama' attribution",
                                          "name derivatives with the Llama prefix"],
                          "caveats": ["700M monthly active user threshold requires "
                                      "a separate license", "redistribution of "
                                      "weights requires the agreement to travel with "
                                      "them"]},
    "llama-2-community": {"family": "community", "uses": ["internal", "commercial",
                                                          "derivative"],
                          "obligations": ["acceptable use policy applies",
                                          "attribution required"],
                          "caveats": ["100M monthly active user threshold"]},
    "gemma-terms": {"family": "community", "uses": ["internal", "commercial",
                                                    "derivative"],
                    "obligations": ["pass through use restrictions",
                                    "publish notices"],
                    "caveats": ["prohibited use policy applies"]},
    "qwen-research": {"family": "research-only", "uses": ["internal"],
                      "obligations": ["research use only"],
                      "caveats": ["no commercial deployment"]},
    "proprietary": {"family": "proprietary", "uses": ["internal"],
                    "obligations": ["terms of the vendor agreement apply"],
                    "caveats": ["no redistribution, no derivative distribution"]},
    "research-only": {"family": "research-only", "uses": ["internal"],
                      "obligations": ["research use only"],
                      "caveats": ["no commercial deployment"]},
    "unknown": {"family": "unknown", "uses": [],
                "obligations": ["obtain a verified license before any use"],
                "caveats": ["cannot be approved for activation"]},
}

NETWORK_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(socket|urllib|urllib3|requests|httpx|http\.client|"
    r"ftplib|smtplib|telnetlib|paramiko|boto3|botocore|websockets|aiohttp)\b",
    re.MULTILINE)
URL_RE = re.compile(r"\b([a-z][a-z0-9+.\-]*)://([^\s\"'<>)\]]+)", re.IGNORECASE)
EGRESS_KEYS = ("destination", "host", "url", "target", "endpoint")


def _num(params, key, default=None, minimum=None, maximum=None, integer=False):
    value = params.get(key, default)
    if value is None:
        raise K.InputError("missing required parameter", parameter=key)
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise K.InputError("parameter must be numeric", parameter=key, value=value)
    if minimum is not None and number < minimum:
        raise K.InputError("parameter below minimum", parameter=key, value=number,
                           minimum=minimum)
    if maximum is not None and number > maximum:
        raise K.InputError("parameter above maximum", parameter=key, value=number,
                           maximum=maximum)
    return number


def _format_bytes(num_bytes):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num_bytes) < 1024.0 or unit == "TiB":
            return "%.2f %s" % (num_bytes, unit)
        num_bytes /= 1024.0
    return "%.2f TiB" % num_bytes


def _register(registry):
    reg = registry

    # ---------------------------------------------------------------- plan.quantization
    @reg.op("plan.quantization",
            "Plan weights/KV memory, fit and quality risk for a quantization choice.",
            params=["params_b", "format", "available_gb", "context_tokens", "layers",
                    "kv_heads", "head_dim", "kv_bytes", "overhead_gb"],
            group="planning",
            examples=['{"params_b": 7, "format": "gguf-q4_k_m", "available_gb": 16, '
                      '"context_tokens": 8192}'])
    def plan_quantization(ctx, params):
        params_b = _num(params, "params_b", minimum=0.1, maximum=2000)
        fmt = str(params.get("format", "int4")).lower()
        if fmt not in FORMAT_BYTES:
            raise K.InputError("unknown weight format", format=fmt,
                               allowed=sorted(FORMAT_BYTES))
        context = _num(params, "context_tokens", 8192, minimum=128, maximum=2000000,
                       integer=True)
        layers = _num(params, "layers", 32, minimum=1, maximum=200, integer=True)
        kv_heads = _num(params, "kv_heads", 8, minimum=1, maximum=256, integer=True)
        head_dim = _num(params, "head_dim", 128, minimum=8, maximum=1024, integer=True)
        kv_bytes = _num(params, "kv_bytes", 2, minimum=1, maximum=4)
        overhead_gb = _num(params, "overhead_gb", 0.75, minimum=0.0, maximum=512)
        available_gb = params.get("available_gb")
        weights_bytes = params_b * 1e9 * FORMAT_BYTES[fmt]
        kv_bytes_total = 2 * layers * kv_heads * head_dim * context * kv_bytes
        total_gb = (weights_bytes + kv_bytes_total) / GIB + overhead_gb
        quality, risk = FORMAT_QUALITY_RISK[fmt]
        result = {
            "params_b": params_b,
            "format": fmt,
            "bytes_per_param": FORMAT_BYTES[fmt],
            "weights": {"bytes": int(weights_bytes),
                        "human": _format_bytes(weights_bytes)},
            "kv_cache": {"bytes": int(kv_bytes_total),
                         "human": _format_bytes(kv_bytes_total),
                         "context_tokens": context},
            "overhead_gb": overhead_gb,
            "total_required_gb": round(total_gb, 3),
            "quality": {"risk": quality,
                        "expected_relative_degradation": risk},
            "speed_index": FORMAT_SPEED_INDEX[fmt],
            "notes": [
                "Weights = params_b * 1e9 * bytes_per_param.",
                "KV cache = 2 * layers * kv_heads * head_dim * context * kv_bytes.",
                "total_required_gb adds the configured runtime overhead allowance.",
            ],
        }
        if available_gb is not None:
            available = _num(params, "available_gb", minimum=0.1, maximum=100000)
            headroom_gb = available - total_gb
            result["fit"] = {
                "available_gb": available,
                "headroom_gb": round(headroom_gb, 3),
                "fits": headroom_gb >= 0,
                "safe_fit_at_90pct": total_gb <= available * 0.9,
                "utilization_pct": round(total_gb / available * 100, 1),
            }
            result["recommendation"] = (
                "accept" if total_gb <= available * 0.9 else
                "reduce_context_or_quantize" if headroom_gb >= 0 else
                "does_not_fit")
        else:
            result["recommendation"] = "no_capacity_supplied"
        ctx.emit("plan.quantization", action="plan.quantization", format=fmt,
                 total_gb=result["total_required_gb"])
        return result

    # ---------------------------------------------------------------- plan.hardware
    @reg.op("plan.hardware",
            "Match candidate models to a local host's RAM, VRAM, disk and cores.",
            params=["models", "host"], group="planning",
            examples=['{"models": [{"name": "7b-q4", "params_b": 7, "format": '
                      '"gguf-q4_k_m"}], "host": {"ram_gb": 32, "vram_gb": 12, '
                      '"disk_gb": 500}}'])
    def plan_hardware(ctx, params):
        models = params.get("models")
        host = params.get("host")
        if not isinstance(models, list) or not models:
            raise K.InputError("plan.hardware requires a non-empty models list")
        if not isinstance(host, dict):
            raise K.InputError("plan.hardware requires a host object")
        ram_gb = float(host.get("ram_gb", 0))
        vram_gb = float(host.get("vram_gb", 0))
        disk_gb = float(host.get("disk_gb", 0))
        if ram_gb <= 0:
            raise K.InputError("host.ram_gb must be positive", value=ram_gb)
        evaluations = []
        for model in models:
            if not isinstance(model, dict) or "params_b" not in model:
                raise K.InputError("each model needs name and params_b")
            fmt = str(model.get("format", "int4")).lower()
            if fmt not in FORMAT_BYTES:
                raise K.InputError("unknown weight format", format=fmt,
                                   allowed=sorted(FORMAT_BYTES))
            params_b = float(model["params_b"])
            context = int(model.get("context_tokens", 8192))
            layers = int(model.get("layers", 32))
            kv_heads = int(model.get("kv_heads", 8))
            head_dim = int(model.get("head_dim", 128))
            weights_gb = params_b * 1e9 * FORMAT_BYTES[fmt] / GIB
            kv_per_token = 2 * layers * kv_heads * head_dim * 2 / GIB
            kv_gb = kv_per_token * context
            disk_gb_needed = weights_gb * 1.1
            gpu_fit = vram_gb >= (weights_gb + kv_gb) * 1.05
            cpu_fit = ram_gb >= (weights_gb + kv_gb) * 1.15
            placement = "gpu" if gpu_fit else ("cpu" if cpu_fit else "does-not-fit")
            budget_gb = (vram_gb if placement == "gpu" else ram_gb)
            max_context = 0
            if placement != "does-not-fit":
                max_context = int(max(0, (budget_gb / 1.05 - weights_gb)
                                      / kv_per_token))
            evaluations.append({
                "model": model.get("name", "unnamed"),
                "format": fmt,
                "weights_gb": round(weights_gb, 2),
                "kv_cache_gb_at_context": round(kv_gb, 2),
                "kv_gb_per_1k_tokens": round(kv_per_token * 1000, 4),
                "disk_gb_needed": round(disk_gb_needed, 2),
                "disk_fits": disk_gb <= 0 or disk_gb_needed <= disk_gb,
                "placement": placement,
                "max_context_tokens": min(max_context, 2000000) if max_context else 0,
                "quality_risk": FORMAT_QUALITY_RISK[fmt][0],
                "throughput_index": FORMAT_SPEED_INDEX[fmt],
            })
        viable = [e for e in evaluations if e["placement"] != "does-not-fit"
                  and e["disk_fits"]]
        viable.sort(key=lambda e: (-e["throughput_index"], -e["max_context_tokens"]))
        ctx.emit("plan.hardware", action="plan.hardware",
                 evaluated=len(evaluations), viable=len(viable))
        return {
            "host": {"ram_gb": ram_gb, "vram_gb": vram_gb, "disk_gb": disk_gb},
            "evaluations": evaluations,
            "recommended": viable[0] if viable else None,
            "note": "Placement prefers GPU when VRAM covers weights plus KV cache "
                    "with 5 percent margin, otherwise CPU with 15 percent margin.",
        }

    # ---------------------------------------------------------------- plan.context
    @reg.op("plan.context",
            "Budget a context window across system, user, evidence and output tokens.",
            params=["max_context_tokens", "system_tokens", "user_tokens",
                    "output_reserve_tokens", "chunk_avg_tokens", "max_chunks"],
            group="planning",
            examples=['{"max_context_tokens": 8192, "system_tokens": 600, '
                      '"user_tokens": 200, "chunk_avg_tokens": 250}'])
    def plan_context(ctx, params):
        max_ctx = _num(params, "max_context_tokens",
                       K.get_path(ctx.cfg, "provider.max_context_tokens", 8192),
                       minimum=256, maximum=2000000, integer=True)
        system_tokens = _num(params, "system_tokens", 600, minimum=0,
                             maximum=1000000, integer=True)
        user_tokens = _num(params, "user_tokens", 200, minimum=0, maximum=1000000,
                           integer=True)
        output_reserve = _num(params, "output_reserve_tokens",
                              max(256, int(max_ctx * 0.15)), minimum=0,
                              maximum=1000000, integer=True)
        chunk_avg = _num(params, "chunk_avg_tokens", 250, minimum=1, maximum=100000,
                         integer=True)
        max_chunks = _num(params, "max_chunks", 12, minimum=1, maximum=500,
                          integer=True)
        fixed = system_tokens + user_tokens + output_reserve
        remaining = max_ctx - fixed
        problems = []
        if remaining <= 0:
            problems.append("fixed allocations already exceed the context window")
            remaining = 0
        chunks_fit = min(max_chunks, remaining // chunk_avg)
        evidence_tokens = chunks_fit * chunk_avg
        spare = remaining - evidence_tokens
        strategy = "full_context" if spare > chunk_avg else "tight"
        if chunks_fit == 0:
            strategy = "summarize_or_abstain"
        result = {
            "max_context_tokens": max_ctx,
            "allocation": {
                "system": system_tokens,
                "user": user_tokens,
                "output_reserve": output_reserve,
                "evidence_budget": evidence_tokens,
                "unused": max(0, spare),
            },
            "chunks": {"fits": chunks_fit,
                       "requested_max": max_chunks,
                       "avg_tokens": chunk_avg},
            "strategy": strategy,
            "truncation_plan": [
                "keep system instructions whole",
                "keep the current user turn whole",
                "drop the lowest scoring evidence chunk first",
                "summarize remaining evidence above %d tokens" % (chunk_avg * 2),
                "abstain with an explicit gap statement if nothing survives",
            ],
            "problems": problems,
        }
        if spare > 0 and spare < chunk_avg:
            result["problems"].append("unused tail smaller than one chunk; "
                                      "consider shorter chunks")
        ctx.emit("plan.context", action="plan.context", strategy=strategy)
        return result

    # ---------------------------------------------------------------- plan.capacity
    @reg.op("plan.capacity",
            "Forecast throughput, queue depth, worker need and storage growth.",
            params=["concurrent_users", "requests_per_user_per_hour",
                    "avg_input_tokens", "avg_output_tokens", "tokens_per_second",
                    "workers", "target_utilization", "queue_capacity",
                    "audit_events_per_request", "audit_event_bytes"],
            group="planning",
            examples=['{"concurrent_users": 20, "requests_per_user_per_hour": 6, '
                      '"tokens_per_second": 40, "workers": 2}'])
    def plan_capacity(ctx, params):
        users = _num(params, "concurrent_users", 1, minimum=1, maximum=100000,
                     integer=True)
        rate = _num(params, "requests_per_user_per_hour", 6, minimum=0,
                    maximum=100000)
        avg_in = _num(params, "avg_input_tokens", 1200, minimum=1, maximum=1000000,
                      integer=True)
        avg_out = _num(params, "avg_output_tokens", 400, minimum=1, maximum=1000000,
                       integer=True)
        tps = _num(params, "tokens_per_second", 30, minimum=0.1, maximum=100000)
        workers = _num(params, "workers", 1, minimum=1, maximum=10000, integer=True)
        target = _num(params, "target_utilization", 0.7, minimum=0.05, maximum=1.0)
        queue_capacity = _num(params, "queue_capacity", 100, minimum=1,
                              maximum=1000000, integer=True)
        events = _num(params, "audit_events_per_request", 6, minimum=0, maximum=1000,
                      integer=True)
        event_bytes = _num(params, "audit_event_bytes", 600, minimum=0,
                           maximum=1000000, integer=True)
        requests_per_second = users * rate / 3600.0
        tokens_per_request = avg_in + avg_out
        service_seconds = tokens_per_request / tps
        capacity_rps = workers / service_seconds
        utilization = requests_per_second / capacity_rps if capacity_rps else 0.0
        workers_needed = max(1, int(-(-requests_per_second * service_seconds
                                      // target)))
        queue_growth = max(0.0, requests_per_second - capacity_rps)
        steady_state_queue = (utilization ** 2) / (1 - utilization) \
            if 0 < utilization < 1 else float("inf")
        storage_per_day_mb = (requests_per_second * 86400 * events * event_bytes
                              / (1024 * 1024))
        result = {
            "offered_load_rps": round(requests_per_second, 4),
            "service_seconds_per_request": round(service_seconds, 3),
            "capacity_rps": round(capacity_rps, 4),
            "utilization": round(utilization, 4),
            "workers_needed_for_target": workers_needed,
            "target_utilization": target,
            "queue": {
                "capacity": queue_capacity,
                "growth_rps": round(queue_growth, 4),
                "saturated": queue_growth > 0,
                "approx_steady_state_waiting": (round(steady_state_queue, 2)
                                                if steady_state_queue != float("inf")
                                                else "unbounded"),
                "overflow_in_seconds": (round(queue_capacity / queue_growth, 1)
                                        if queue_growth > 0 else None),
            },
            "storage": {
                "audit_mb_per_day": round(storage_per_day_mb, 2),
                "audit_gb_per_year": round(storage_per_day_mb * 365 / 1024, 3),
            },
            "verdict": ("ok" if utilization <= target else
                        "add_workers" if queue_growth <= 0 else "overloaded"),
            "notes": [
                "offered_load_rps = concurrent_users * requests_per_user_per_hour / 3600",
                "service_seconds = (avg_input_tokens + avg_output_tokens) / tokens_per_second",
                "capacity_rps = workers / service_seconds",
            ],
        }
        ctx.emit("plan.capacity", action="plan.capacity",
                 verdict=result["verdict"], utilization=result["utilization"])
        return result

    # ---------------------------------------------------------------- airgap.check
    @reg.op("airgap.check",
            "Statically verify a tree and the configuration against air-gap rules.",
            params=["path", "egress_log", "max_files"], group="security",
            examples=['{"path": "artifacts"}'])
    def airgap_check(ctx, params):
        cfg = ctx.cfg.get("airgap", {})
        mode = cfg.get("mode", "offline")
        allowed_hosts = {str(h).lower() for h in cfg.get("allowed_hosts", [])}
        blocked_hosts = {str(h).lower() for h in cfg.get("blocked_hosts", [])}
        blocked_schemes = {str(s).lower() for s in cfg.get("blocked_schemes", [])}
        allowed_schemes = {str(s).lower() for s in cfg.get("allowed_schemes", [])}
        extensions = {str(e).lower() for e in cfg.get("scan_extensions", [])}
        max_files = int(params.get("max_files", cfg.get("max_files_scanned", 2000)))
        findings = []
        config_problems = []
        loopback = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
        if mode == "offline" and (allowed_hosts - loopback):
            config_problems.append({
                "issue": "offline_mode_with_non_loopback_allowlist",
                "hosts": sorted(allowed_hosts - loopback)})
        if mode == "offline" and allowed_schemes & {"http", "https"}:
            config_problems.append({
                "issue": "offline_mode_allows_http_schemes",
                "schemes": sorted(allowed_schemes & {"http", "https"})})
        target = params.get("path", ".")
        root = ctx.workspace.read_scope.resolve(target, must_exist=True)
        files = []
        if os.path.isfile(root):
            files = [root]
        else:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for name in sorted(filenames):
                    if os.path.splitext(name)[1].lower() in extensions:
                        files.append(os.path.join(dirpath, name))
                    if len(files) >= max_files:
                        break
                if len(files) >= max_files:
                    break
        scanned = 0
        for path in files:
            try:
                text = K.read_text(path, max_bytes=int(
                    K.get_path(ctx.cfg, "limits.max_document_bytes", 2000000)))
            except (K.OAAError, OSError):
                continue
            scanned += 1
            rel = ctx.workspace.source_id(path)
            for match in URL_RE.finditer(text):
                scheme = match.group(1).lower()
                host = match.group(2).split("/")[0].split("@")[-1].split(":")[0].lower()
                if scheme in allowed_schemes and host in allowed_hosts:
                    continue
                if scheme in blocked_schemes or host in blocked_hosts:
                    findings.append({
                        "type": "external_reference",
                        "severity": "high" if host in blocked_hosts else "medium",
                        "path": rel, "scheme": scheme, "host": host,
                        "reason": "blocked scheme or host in offline mode",
                    })
            for match in NETWORK_IMPORT_RE.finditer(text):
                findings.append({
                    "type": "network_capable_import", "severity": "medium",
                    "path": rel, "module": match.group(1),
                    "reason": "module can open sockets; verify it is unused offline",
                })
        egress_checked = 0
        egress_problems = []
        egress_log = params.get("egress_log")
        if egress_log:
            log_path = ctx.workspace.read_scope.resolve(egress_log, must_exist=True)
            rows = K.read_jsonl(log_path)
            for index, row in enumerate(rows):
                destination = ""
                for key in EGRESS_KEYS:
                    if isinstance(row, dict) and row.get(key):
                        destination = str(row[key])
                        break
                if not destination:
                    egress_problems.append({"record": index,
                                            "issue": "no destination field"})
                    continue
                host = destination.split("//")[-1].split("/")[0].split(":")[0].lower()
                egress_checked += 1
                if host not in allowed_hosts:
                    egress_problems.append({
                        "record": index, "host": host,
                        "issue": "destination outside the allowlist"})
        ok = not config_problems and not egress_problems and not any(
            f["severity"] == "high" for f in findings)
        result = {
            "ok": ok,
            "mode": mode,
            "scanned_files": scanned,
            "findings": findings[:200],
            "finding_count": len(findings),
            "config_problems": config_problems,
            "egress": {"records_checked": egress_checked,
                       "problems": egress_problems},
            "limits": {"max_files": max_files, "truncated": len(files) >= max_files},
            "note": "Static analysis only. It does not prove runtime isolation; "
                    "combine with host firewall or namespace policy.",
        }
        ctx.emit("airgap.check", action="airgap.check", ok=ok,
                 findings=len(findings))
        return result

    # ---------------------------------------------------------------- registry
    @reg.op("registry.add",
            "Record a versioned model, prompt, tool, dataset or artifact with checksum.",
            params=["kind", "name", "version", "file", "license", "metadata",
                    "status"], group="registry", mutating=True,
            examples=['{"kind": "model", "name": "qwen2.5-7b-instruct", "version": '
                      '"1.0.0", "license": "apache-2.0"}'])
    def registry_add(ctx, params):
        kind = str(params.get("kind", "")).lower()
        allowed = ("model", "prompt", "tool", "dataset", "artifact", "workflow")
        if kind not in allowed:
            raise K.InputError("kind must be one of the registry kinds", kind=kind,
                               allowed=list(allowed))
        name = params.get("name")
        version = params.get("version")
        if not name or not version:
            raise K.InputError("registry.add requires name and version")
        if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", str(version)):
            raise K.InputError("version must look like 1, 1.0 or 1.0.0",
                               version=version)
        entry = {
            "kind": kind, "name": str(name), "version": str(version),
            "status": str(params.get("status", "staged")),
            "license": params.get("license"),
            "metadata": params.get("metadata") or {},
            "recorded_at": K.now_iso(),
            "recorded_by": "local-operator",
            "correlation_id": ctx.correlation_id,
        }
        if params.get("file"):
            path = ctx.workspace.scope.resolve(params["file"], must_exist=True)
            entry["file"] = ctx.workspace.rel(path)
            entry["sha256"] = K.sha256_file(path)
            entry["bytes"] = os.path.getsize(path)
        license_name = str(entry.get("license") or "unknown").lower()
        if license_name in LICENSES:
            entry["license_family"] = LICENSES[license_name]["family"]
            entry["license_obligations"] = LICENSES[license_name]["obligations"]
        else:
            entry["license_family"] = "unknown"
            entry["license_obligations"] = ["obtain and record a verified license"]
        key = "%s@%s" % (entry["name"], entry["version"])
        scope_name = "registry.%s" % kind
        existing = ctx.store.kv_get(scope_name, key)
        if existing and existing.get("sha256") and entry.get("sha256") \
                and existing["sha256"] != entry["sha256"]:
            raise K.IntegrityError(
                "an entry with this name and version already exists with a different "
                "checksum", existing_sha256=existing["sha256"],
                new_sha256=entry["sha256"])
        ctx.store.kv_set(scope_name, key, entry)
        ctx.emit("registry.add", action="registry.add", target=key, kind=kind)
        return entry

    @reg.op("registry.list", "List registry entries, optionally by kind.",
            params=["kind"], group="registry", examples=['{"kind": "model"}'])
    def registry_list(ctx, params):
        kinds = [str(params["kind"]).lower()] if params.get("kind") else \
            ["model", "prompt", "tool", "dataset", "artifact", "workflow"]
        entries = []
        for kind in kinds:
            for item in ctx.store.kv_list("registry.%s" % kind):
                entries.append(item["value"])
        entries.sort(key=lambda e: (e.get("kind", ""), e.get("name", ""),
                                    e.get("version", "")))
        return {"count": len(entries), "entries": entries}

    @reg.op("registry.license",
            "Decide whether a declared license permits a specific use.",
            params=["license", "use", "attribution"], group="registry",
            examples=['{"license": "llama-3-community", "use": "commercial"}'])
    def registry_license(ctx, params):
        name = str(params.get("license", "")).lower()
        use = str(params.get("use", "internal")).lower()
        allowed_uses = ("internal", "redistribution", "commercial", "derivative")
        if use not in allowed_uses:
            raise K.InputError("use must be one of %s" % (allowed_uses,), use=use)
        if not name:
            raise K.InputError("registry.license requires params.license")
        if name not in LICENSES:
            return {"license": name, "use": use, "permitted": False,
                    "family": "unverified",
                    "reason": "license is not in the local compliance matrix; treat as "
                              "unknown until a verified copy is supplied",
                    "obligations": ["supply the license text for review"],
                    "review_required": True}
        entry = LICENSES[name]
        permitted = use in entry["uses"]
        return {
            "license": name,
            "use": use,
            "permitted": permitted,
            "family": entry["family"],
            "reason": ("declared use is covered" if permitted else
                       "declared use is not granted by this license"),
            "obligations": entry.get("obligations", []),
            "caveats": entry.get("caveats", []),
            "attribution_required": "attribute the source" in entry.get(
                "obligations", []),
            "review_required": entry["family"] in ("community", "unknown",
                                                   "proprietary", "research-only"),
            "note": "Informational compliance aid only; it is not legal advice.",
        }


def register(registry):
    _register(registry)
    return registry
