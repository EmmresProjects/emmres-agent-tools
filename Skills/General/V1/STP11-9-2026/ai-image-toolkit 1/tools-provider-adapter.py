#!/usr/bin/env python3
"""Engine job specs, ComfyUI-style graphs and the optional HTTP provider adapter.

Operations: engine.job, engine.comfy_graph, engine.control_plan, provider.check, provider.batch.

Nothing is sent over the network unless ALL of these hold:
  1. provider.enabled is true in the config,
  2. the endpoint host is listed in provider.allowed_hosts (http or https only),
  3. the caller passes execute=true AND --approve,
  4. a credential, if required, is read from the environment variable named in provider.api_key_env.
Without these, every operation only writes validated job JSON files.
"""
import base64
import datetime as _dt
import hashlib
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load_module(filename, name):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, _HERE / filename)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


C = _load_module("tools-common.py", "aiit_common")
COMPUTE = C.load_sibling("tools-compute-engine.py")
ToolError, P, op, write_json, sha256_file = C.ToolError, C.P, C.op, C.write_json, C.sha256_file

JOB_TYPES = ["txt2img", "img2img", "inpaint", "outpaint", "upscale", "controlnet", "ip-adapter", "face-restore"]
CONTROL_TYPES = ["controlnet", "pose", "depth", "edge", "segmentation", "ip-adapter", "reference"]
PREPROCESSOR_HINT = {"pose": "openpose", "depth": "depth-estimator", "edge": "canny-or-hed",
                     "segmentation": "segmentation-map", "ip-adapter": "reference-embedding",
                     "reference": "reference-embedding", "controlnet": "as-supplied"}
IMG_MAGIC = {"png": b"\x89PNG\r\n\x1a\n", "jpeg": b"\xff\xd8\xff", "webp": b"RIFF"}


# ----------------------------------------------------------------- shared validation

def validate_control(spec):
    """Return a normalised control spec or raise ToolError."""
    t = spec.get("type")
    if t not in CONTROL_TYPES:
        raise ToolError("invalid_input", f"control type must be one of {CONTROL_TYPES}", field="controls")
    try:
        strength = float(spec.get("strength", 1.0))
        start = float(spec.get("start", 0.0))
        end = float(spec.get("end", 1.0))
    except (TypeError, ValueError):
        raise ToolError("invalid_input", "control strength/start/end must be numbers", field="controls")
    if not 0 <= strength <= 2:
        raise ToolError("invalid_input", "control strength must be between 0 and 2", field="controls")
    if not (0 <= start < end <= 1):
        raise ToolError("invalid_input", "control window needs 0 <= start < end <= 1", field="controls")
    img = spec.get("image")
    if img and not Path(img).is_file():
        raise ToolError("not_found", f"control image not found: {img}", field="controls")
    return {"type": t, "strength": strength, "start": start, "end": end, "image": img,
            "preprocessor": spec.get("preprocessor") or PREPROCESSOR_HINT[t],
            "role": spec.get("role") or t}


def _image_size(path):
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(path) as im:
        return im.size


def _job_id(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------- HTTP helpers

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _provider_settings(cfg):
    return cfg["provider"]


def _check_endpoint(cfg, path_key="submit_path"):
    prov = _provider_settings(cfg)
    if not prov.get("enabled"):
        raise ToolError("unavailable", "provider is disabled in the config",
                        hint="Set provider.enabled=true and provider.endpoint in a config file passed with --config.")
    endpoint = prov.get("endpoint") or ""
    u = urllib.parse.urlparse(endpoint)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ToolError("invalid_input", "provider.endpoint must be an http or https URL", field="provider.endpoint")
    if u.username or u.password:
        raise ToolError("invalid_input", "credentials must not be embedded in the endpoint URL")
    if u.hostname not in prov.get("allowed_hosts", []):
        raise ToolError("invalid_input", f"host '{u.hostname}' is not in provider.allowed_hosts",
                        hint="Add the host to allowed_hosts only if you trust it.")
    return endpoint.rstrip("/") + prov.get(path_key, "/")


def _headers(cfg, required=True):
    h = {"Content-Type": "application/json", "User-Agent": f"ai-image-toolkit/{C.TOOLKIT_VERSION}"}
    env = cfg["provider"].get("api_key_env") or ""
    if env:
        key = os.environ.get(env)
        if not key:
            if not required:  # health probes report the state instead of failing
                return h
            raise ToolError("unavailable", f"credential environment variable {env} is not set",
                            hint="Export the key in your shell; it is never stored in files.")
        h["Authorization"] = "Bearer " + key
    return h


def _post(url, payload, cfg):
    """POST JSON with bounded retries on transient failures. Returns (bytes, content_type)."""
    opener = urllib.request.build_opener(_NoRedirect)
    body = json.dumps(payload).encode("utf-8")
    attempts = cfg["retries"]["max_attempts"]
    backoff = cfg["retries"]["backoff_seconds"]
    timeout = cfg["provider"]["timeout_seconds"]
    last = None
    for n in range(1, attempts + 1):
        req = urllib.request.Request(url, data=body, headers=_headers(cfg), method="POST")
        try:
            with opener.open(req, timeout=timeout) as resp:
                limit = cfg["limits"]["max_file_bytes"]
                data = resp.read(limit + 1)
                if len(data) > limit:
                    raise ToolError("runtime_error", "provider response exceeds limits.max_file_bytes")
                return data, resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (401, 403):
                raise ToolError("unavailable", f"provider rejected the credential ({last})",
                                hint="Check the key in the environment variable named by provider.api_key_env.")
            if e.code not in (408, 429) and e.code < 500:
                raise ToolError("runtime_error", f"provider rejected the request ({last})")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = f"{type(e).__name__}: {getattr(e, 'reason', e)}"
        if n < attempts:
            time.sleep(backoff * (2 ** (n - 1)))
    raise ToolError("timeout" if "timed out" in str(last).lower() else "unavailable",
                    f"provider unreachable after {attempts} attempt(s): {last}")


def _get(url, cfg):
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers=_headers(cfg, required=False), method="GET")
    t0 = time.time()
    try:
        with opener.open(req, timeout=cfg["provider"]["timeout_seconds"]) as resp:
            return {"status": resp.status, "latency_ms": round((time.time() - t0) * 1000, 1)}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "latency_ms": round((time.time() - t0) * 1000, 1)}
    except (urllib.error.URLError, OSError) as e:
        raise ToolError("unavailable", f"health check failed: {getattr(e, 'reason', e)}")


def _decode_result(raw, ctype):
    """Accept raw image bytes or JSON with image_base64. Verify the magic bytes."""
    data = raw
    if "json" in (ctype or "").lower():
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ToolError("runtime_error", "provider returned non-JSON text")
        b64 = obj.get("image_base64") or (obj.get("images") or [None])[0]
        if not b64:
            raise ToolError("runtime_error", "provider JSON has no image_base64 field")
        try:
            data = base64.b64decode(b64, validate=True)
        except (ValueError, TypeError):
            raise ToolError("runtime_error", "provider image_base64 is not valid base64")
    kind = None
    if data.startswith(IMG_MAGIC["png"]):
        kind = "png"
    elif data.startswith(IMG_MAGIC["jpeg"]):
        kind = "jpg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = "webp"
    if kind is None:
        raise ToolError("runtime_error", "provider response is not PNG, JPEG or WEBP")
    return data, kind


def _submit(payload, cfg, out_path):
    url = _check_endpoint(cfg, "submit_path")
    raw, ctype = _post(url, payload, cfg)
    data, kind = _decode_result(raw, ctype)
    final = Path(out_path).with_suffix("." + kind)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_suffix(final.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, final)
    return str(final), len(data)


def _b64_file(path):
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


# ----------------------------------------------------------------- engine.control_plan

def op_control_plan(p, ctx):
    spec = validate_control({"type": p["control_type"], "image": p.get("image"), "strength": p["strength"],
                             "start": p["start"], "end": p["end"], "preprocessor": p.get("preprocessor"),
                             "role": p.get("role")})
    size = _image_size(spec["image"]) if spec["image"] else None
    warnings = []
    if size and (size[0] % 8 or size[1] % 8):
        warnings.append("control image size is not a multiple of 8; resize before use")
    if spec["strength"] > 1.2:
        warnings.append("strength above 1.2 often overrides the prompt; test lower values first")
    result = {"control": spec, "image_size": list(size) if size else None, "warnings_list": warnings,
              "note": "Control specification only. The preprocessor named here must be run by your engine or a separate tool."}
    path = ctx.out_path(p.get("output"), f"control-{spec['type']}.json")
    write_json(path, result)
    ctx.record(path)
    result["control_file"] = str(path)
    return result


# ----------------------------------------------------------------- engine.job

def op_engine_job(p, ctx):
    jt = p["job_type"]
    if jt not in JOB_TYPES:
        raise ToolError("invalid_input", f"job_type must be one of {JOB_TYPES}", field="job_type")
    if jt == "txt2img" and not (p.get("prompt") or "").strip():
        raise ToolError("invalid_input", "txt2img needs 'prompt'", field="prompt")
    needs_image = jt in ("img2img", "inpaint", "outpaint", "upscale", "face-restore", "controlnet", "ip-adapter")
    if needs_image and not p.get("image"):
        raise ToolError("invalid_input", f"{jt} needs 'image'", field="image")
    if jt == "inpaint" and not p.get("mask"):
        raise ToolError("invalid_input", "inpaint needs 'mask' (white = area to regenerate)", field="mask")
    if jt in ("img2img", "inpaint", "face-restore") and p.get("denoise") is None:
        raise ToolError("invalid_input", f"{jt} needs 'denoise' between 0 and 1", field="denoise")
    warnings = []
    if p.get("image") and p.get("mask"):
        si, sm = _image_size(p["image"]), _image_size(p["mask"])
        if si and sm and si != sm:
            raise ToolError("invalid_input", f"mask size {sm} does not match image size {si}", field="mask")
        if si is None:
            warnings.append("Pillow not installed: mask and image sizes were not compared")
    w, h = p.get("width"), p.get("height")
    if w and h and (w % 8 or h % 8):
        raise ToolError("invalid_input", "width and height must be multiples of 8", field="width")
    if w and h and (w % 64 or h % 64):
        warnings.append("width/height are not multiples of 64; many engines expect 64-multiples")
    controls = []
    for c in (p.get("controls") or []):
        if not isinstance(c, dict):
            raise ToolError("invalid_input", "each control must be an object", field="controls")
        controls.append(validate_control(c))
    refs = p.get("references") or []
    for r in refs:
        if not Path(r).is_file():
            raise ToolError("not_found", f"reference not found: {r}", field="references")
    payload = {
        "job_type": jt, "edit": p.get("edit"), "model": p.get("model"), "prompt": p.get("prompt"),
        "negative_prompt": p.get("negative_prompt") or "", "seed": p.get("seed"), "steps": p.get("steps"),
        "cfg": p.get("cfg"), "width": w, "height": h, "denoise": p.get("denoise"),
        "controls": controls, "extra": p.get("extra") or {},
        "input_hashes": {},
    }
    for key in ("image", "mask"):
        if p.get(key):
            payload["input_hashes"][key] = sha256_file(p[key])
    if refs:
        payload["input_hashes"]["references"] = [sha256_file(r) for r in refs]
    job_id = _job_id({k: v for k, v in payload.items()})
    record = {"job_id": job_id, "status": "prepared", "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
              "payload": payload, "input_files": {k: p[k] for k in ("image", "mask") if p.get(k)},
              "reference_files": refs, "warnings_list": warnings}
    out_json = ctx.out_path(p.get("output"), f"job-{job_id}.json")
    if p.get("execute"):
        if not ctx.approve:
            raise ToolError("approval_required", "executing an engine job needs explicit approval",
                            hint="Re-run with --execute --approve after confirming cost and destination.")
        body = dict(payload)
        if p.get("image"):
            body["image_base64"] = _b64_file(p["image"])
        if p.get("mask"):
            body["mask_base64"] = _b64_file(p["mask"])
        image_out = ctx.out_path(None, f"result-{job_id}")
        final, n = _submit(body, ctx.cfg, image_out)
        record["status"] = "completed"
        record["result_file"] = final
        record["result_bytes"] = n
        ctx.record(final)
    write_json(out_json, record)
    ctx.record(out_json)
    return {"job_file": str(out_json), "job_id": job_id, "status": record["status"],
            "result_file": record.get("result_file"), "warnings_list": warnings,
            "note": "Job written. Use --execute --approve only when the provider.endpoint is one you control."}


# ----------------------------------------------------------------- engine.comfy_graph

def validate_graph(graph):
    errors = []
    if not isinstance(graph, dict) or not graph:
        return ["graph is empty"]
    for nid, node in graph.items():
        if not isinstance(node, dict) or "class_type" not in node or "inputs" not in node:
            errors.append(f"node {nid} needs class_type and inputs")
            continue
        for k, v in node["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], int):
                if v[0] not in graph:
                    errors.append(f"node {nid} input {k} links to missing node {v[0]}")
    return errors


def op_comfy_graph(p, ctx):
    w, h = p["width"], p["height"]
    if w % 8 or h % 8:
        raise ToolError("invalid_input", "width and height must be multiples of 8", field="width")
    g = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": p["checkpoint"]}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": p["prompt"], "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": p.get("negative_prompt") or "", "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"seed": p["seed"], "steps": p["steps"], "cfg": p["cfg"],
                                                   "sampler_name": p["sampler"], "scheduler": p["scheduler"],
                                                   "denoise": 1.0, "model": ["1", 0], "positive": ["2", 0],
                                                   "negative": ["3", 0], "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": p["output_prefix"], "images": ["6", 0]}},
    }
    pos_ref, neg_ref = ["2", 0], ["3", 0]
    if p.get("control_model"):
        if not p.get("control_image"):
            raise ToolError("invalid_input", "control_model needs control_image", field="control_image")
        if not Path(p["control_image"]).is_file():
            raise ToolError("not_found", "control_image not found", field="control_image")
        g["8"] = {"class_type": "LoadImage", "inputs": {"image": Path(p["control_image"]).name}}
        g["9"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": p["control_model"]}}
        g["10"] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
            "positive": pos_ref, "negative": neg_ref, "control_net": ["9", 0], "image": ["8", 0],
            "strength": p.get("control_strength", 1.0), "start_percent": 0.0, "end_percent": 1.0}}
        g["5"]["inputs"]["positive"] = ["10", 0]
        g["5"]["inputs"]["negative"] = ["10", 1]
    errors = validate_graph(g)
    if errors:
        raise ToolError("runtime_error", f"generated graph failed link check: {errors}")
    out = ctx.out_path(p.get("output"), "comfy-api-graph.json")
    write_json(out, g)
    ctx.record(out)
    return {"graph_file": str(out), "node_count": len(g), "link_check": "passed",
            "note": "Follows the ComfyUI API prompt JSON layout (node id -> class_type + inputs). Load the model names your server actually has. Not executed against a live server by this tool."}


# ----------------------------------------------------------------- provider.check

def op_provider_check(p, ctx):
    prov = ctx.cfg["provider"]
    preview_url = None
    issues = []
    try:
        preview_url = _check_endpoint(ctx.cfg, "submit_path")
    except ToolError as e:
        issues.append(e.message)
    env = prov.get("api_key_env") or ""
    key_state = None
    if env:
        key_state = "set" if os.environ.get(env) else "not set"
    result = {"enabled": bool(prov.get("enabled")), "endpoint_host": urllib.parse.urlparse(prov.get("endpoint") or "").hostname,
              "allowed_hosts": prov.get("allowed_hosts"), "submit_url": preview_url, "credential_env": env or None,
              "credential_state": key_state, "timeout_seconds": prov.get("timeout_seconds"),
              "issues": issues}
    if p["mode"] == "probe":
        if issues:
            raise ToolError("invalid_input", "; ".join(issues), field="provider.endpoint")
        url = urllib.parse.urlparse(preview_url)
        base = f"{url.scheme}://{url.netloc}" + prov.get("health_path", "/health")
        result["health"] = _get(base, ctx.cfg)
        result["health_url"] = base
    else:
        result["mode_note"] = "dry-run: no network request was made"
    return result


# ----------------------------------------------------------------- provider.batch

def _atomic_write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def op_provider_batch(p, ctx):
    req = p["request"]
    if not isinstance(req, dict) or "prompt" not in req or "axes" not in req:
        raise ToolError("invalid_input", "request needs 'prompt' and 'axes' (see asset-sample-sweep.json)", field="request")
    limit = ctx.limits["max_batch_items"]
    extra = {"model": req.get("model") or "default", "width": req.get("width", 512), "height": req.get("height", 512)}
    items = COMPUTE.sweep_items(req["prompt"], req.get("negative_prompt") or "", req["axes"],
                                req.get("name_template") or "{sampler}_s{steps}_cfg{cfg}_seed{seed}",
                                min(limit, req.get("max_items", limit)), extra_fields=extra)
    manifest_path = Path(p["manifest"]) if p.get("manifest") else ctx.cap_dir / "batch-manifest.json"
    state = {}
    if manifest_path.exists() and p["resume"]:
        try:
            state = {it["id"]: it for it in json.loads(manifest_path.read_text(encoding="utf-8")).get("items", [])}
        except json.JSONDecodeError:
            raise ToolError("invalid_input", "batch manifest is corrupt; move it aside and re-run", field="manifest")
    if p["execute"]:
        if not ctx.approve:
            raise ToolError("approval_required", "batch execution submits many jobs; needs --execute --approve",
                            hint="Review the dry-run manifest first.")
        _check_endpoint(ctx.cfg, "submit_path")
    records = []
    for it in items:
        prev = state.get(it["id"])
        records.append(prev if prev and prev.get("status") == "completed" else
                       {"id": it["id"], "settings": it["settings"], "status": "prepared", "error": None, "file": None})
    run_count = 0
    try:
        for rec in records:
            if rec["status"] == "completed" or not p["execute"]:
                continue
            if run_count >= p["limit"]:
                break
            run_count += 1
            payload = {"prompt": req["prompt"], "negative_prompt": req.get("negative_prompt") or "",
                       "model": req.get("model"), "width": req.get("width", 512), "height": req.get("height", 512),
                       "settings": rec["settings"], "job_id": rec["id"]}
            try:
                final, n = _submit(payload, ctx.cfg, ctx.cap_dir / "batch" / rec["id"])
                rec.update({"status": "completed", "file": final, "error": None, "bytes": n})
                ctx.record(final)
            except ToolError as e:
                rec.update({"status": "failed", "error": e.message})
                if e.code in ("unavailable", "timeout"):
                    break
    finally:
        # Progress is always saved, including after Ctrl-C or a provider outage.
        _atomic_write(manifest_path, {"items": records, "execute": p["execute"],
                                      "updated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")})
        ctx.record(manifest_path)
    counts = {}
    for r in records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"manifest_file": str(manifest_path), "item_count": len(records), "status_counts": counts,
            "executed_this_run": run_count if p["execute"] else 0, "dry_run": not p["execute"],
            "first_items": records[:3],
            "note": "Dry run writes the manifest only. Re-run with --execute --approve to submit; completed items are skipped on resume."}


# ----------------------------------------------------------------- registry

OPS = {
    "engine.job": op(op_engine_job, [
        P("job_type", "choice", True, choices=JOB_TYPES), P("edit", "str", help="Label for the edit, e.g. object-removal"),
        P("model", "str"), P("prompt", "str"), P("negative_prompt", "str"),
        P("seed", "int", min=0, max=2**32 - 1), P("steps", "int", min=1, max=500), P("cfg", "float", min=0, max=30),
        P("width", "int", min=64, max=8192), P("height", "int", min=64, max=8192),
        P("denoise", "float", min=0, max=1), P("image", "file"), P("mask", "file"),
        P("references", "files", help="Reference images (IP-Adapter / multi-reference)"),
        P("controls", "json", help="List of control objects (see engine.control_plan)"),
        P("extra", "json"), P("execute", "bool", default=False, help="Submit to the configured provider (needs --approve)"),
        P("output", "outfile"),
    ], "Validate and write an engine job spec; submit only when enabled and approved"),
    "engine.comfy_graph": op(op_comfy_graph, [
        P("checkpoint", "str", True, help="Checkpoint file name your server has"),
        P("prompt", "str", True), P("negative_prompt", "str"),
        P("width", "int", default=1024, min=64, max=8192), P("height", "int", default=1024, min=64, max=8192),
        P("steps", "int", default=25, min=1, max=500), P("cfg", "float", default=6.0, min=0, max=30),
        P("sampler", "str", default="euler"), P("scheduler", "str", default="normal"),
        P("seed", "int", default=0, min=0, max=2**32 - 1), P("output_prefix", "str", default="toolkit"),
        P("control_model", "str", help="ControlNet model file name"),
        P("control_image", "file"), P("control_strength", "float", default=1.0, min=0, max=2),
        P("mode", "choice", default="api", choices=["api"]), P("output", "outfile"),
    ], "Build and link-check a ComfyUI API-format graph (optional ControlNet branch)"),
    "engine.control_plan": op(op_control_plan, [
        P("control_type", "choice", True, choices=CONTROL_TYPES),
        P("image", "file"), P("strength", "float", default=1.0, min=0, max=2),
        P("start", "float", default=0.0, min=0, max=1), P("end", "float", default=1.0, min=0, max=1),
        P("preprocessor", "str"), P("role", "str"), P("output", "outfile"),
    ], "Validate a control (ControlNet, pose, depth, edge, segmentation, IP-Adapter) and its window"),
    "provider.check": op(op_provider_check, [
        P("mode", "choice", default="dry-run", choices=["dry-run", "probe"]),
    ], "Validate provider config (dry-run) or probe the health endpoint (probe)"),
    "provider.batch": op(op_provider_batch, [
        P("request", "json", True, help="Batch request JSON or @file (see asset-sample-sweep.json)"),
        P("execute", "bool", default=False, help="Submit items (needs --approve)"),
        P("limit", "int", default=50, min=1, max=500, help="Max items submitted in one run"),
        P("resume", "bool", default=True), P("manifest", "outfile"),
    ], "Expand a sweep, keep a resumable manifest, and optionally submit each item"),
}
