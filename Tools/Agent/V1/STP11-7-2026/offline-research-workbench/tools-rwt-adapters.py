"""tools-rwt-adapters.py - configurable local model/tool adapter layer.

Every model-dependent capability in this Skill Tool (machine translation, OCR, speech,
embeddings, reranking, PDF/Office text extraction) goes through this module. Adapters are
local executables declared in the configuration; the workbench never opens a network
connection and never uses a shell. When an adapter is not configured the dependent
operations fail with exit code 4 and an explicit "unavailable" payload instead of
inventing output.

Wire protocol (one JSON request on stdin, one JSON response on stdout):
  mt       {"text","src_lang","tgt_lang"}            -> {"translation"}
  embed    {"texts":[...]}                            -> {"vectors":[[float,...],...]}
  ocr      {"path","language"}                        -> {"text","confidence"}
  asr      {"path","language"}                        -> {"segments":[{"start_ms","end_ms","text","speaker"}]}
  extract  {"path"}                                   -> {"text"}
  rerank   {"query","passages":[...]}                 -> {"scores":[float,...]}
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time

NAME = "adapters"
ROLE = "local model/tool adapter invocation with timeouts, retries and hard failure when unavailable"
GROUPS = [("adapters", "Inspect, test and use configured local model/tool adapters")]
deps: dict = {}

KINDS = ("mt", "embed", "ocr", "asr", "extract", "rerank")
REQUIRED_KEYS = {
    "mt": ("translation",), "embed": ("vectors",), "ocr": ("text",), "asr": ("segments",),
    "extract": ("text",), "rerank": ("scores",),
}


def adapter_spec(ctx, kind: str) -> dict:
    if kind not in KINDS:
        raise deps["store"].ToolError(2, f"unknown adapter kind '{kind}'", {"kinds": list(KINDS)})
    return dict(((ctx.cfg.get("adapters") or {}).get("kinds") or {}).get(kind) or {})


def resolve_command(ctx, kind: str) -> list | None:
    spec = adapter_spec(ctx, kind)
    command = spec.get("command")
    if not command:
        return None
    argv = shlex.split(command) if isinstance(command, str) else [str(x) for x in command]
    if not argv:
        return None
    exe = argv[0]
    if os.path.sep in exe:
        if not os.path.isabs(exe):
            exe = os.path.join(ctx.root, exe)
        if not os.path.exists(exe):
            return None
        argv[0] = exe
    elif shutil.which(exe) is None:
        return None
    return argv


def adapter_status(ctx) -> dict:
    out = {}
    for kind in KINDS:
        spec = adapter_spec(ctx, kind)
        argv = resolve_command(ctx, kind)
        out[kind] = {
            "configured": bool(spec.get("command")),
            "available": bool(argv),
            "command": spec.get("command"),
            "timeout_seconds": spec.get("timeout_seconds"),
            "requires": spec.get("description", "").split(".")[0],
        }
    return out


def invoke(ctx, kind: str, payload: dict, timeout: int | None = None) -> dict:
    """Run a configured local adapter. Raises ToolError(4) when unavailable or failing."""
    store = deps["store"]
    spec = adapter_spec(ctx, kind)
    argv = resolve_command(ctx, kind)
    if not argv:
        raise store.ToolError(
            4,
            f"no local '{kind}' adapter is configured or the configured command is not installed; "
            f"this capability requires an approved offline model",
            {"kind": kind, "configure": "set adapters.kinds." + kind + ".command in a config file",
             "protocol": spec.get("description", ""),
             "configured": bool(spec.get("command"))})
    limits = ctx.cfg.get("limits", {})
    timeout = timeout or spec.get("timeout_seconds") or limits.get("adapter_timeout_seconds", 60)
    retries = int(spec.get("retries", limits.get("retries", 0)) or 0)
    max_chars = int(spec.get("max_chars", 200000))
    request = json.dumps(payload, ensure_ascii=False)
    if len(request) > max_chars:
        raise store.ToolError(2, f"adapter request exceeds {max_chars} characters for '{kind}'")
    last_error = None
    for attempt in range(1, retries + 2):
        started = time.time()
        try:
            proc = subprocess.run(argv, input=request.encode("utf-8"),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout, check=False, shell=False)
        except subprocess.TimeoutExpired:
            last_error = f"adapter '{kind}' timed out after {timeout}s"
            ctx.audit("adapter.timeout", kind, {"attempt": attempt, "timeout": timeout})
            continue
        except OSError as exc:
            last_error = f"adapter '{kind}' could not start: {exc}"
            continue
        elapsed = round(time.time() - started, 3)
        if proc.returncode != 0:
            last_error = (f"adapter '{kind}' exited {proc.returncode}: "
                          f"{proc.stderr.decode('utf-8', 'replace')[:400]}")
            continue
        try:
            response = json.loads(proc.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = f"adapter '{kind}' returned invalid JSON: {exc}"
            continue
        missing = [k for k in REQUIRED_KEYS[kind] if k not in response]
        if missing:
            last_error = f"adapter '{kind}' response missing keys: {missing}"
            continue
        ctx.audit("adapter.invoke", kind, {"attempt": attempt, "elapsed_s": elapsed,
                                           "command": os.path.basename(argv[0])})
        ctx.commit()
        return {"ok": True, "kind": kind, "elapsed_s": elapsed, "attempt": attempt,
                "result": response}
    raise store.ToolError(4, last_error or f"adapter '{kind}' unavailable",
                          {"kind": kind, "attempts": retries + 1})


# ---------------------------------------------------------------- ops

def op_check(ctx) -> dict:
    status = adapter_status(ctx)
    available = [k for k, v in status.items() if v["available"]]
    return {"status": "ok", "adapters": status, "available": available,
            "unavailable": [k for k, v in status.items() if not v["available"]],
            "note": "Capabilities that depend on an unavailable adapter fail with exit code 4 "
                    "instead of fabricating output."}


def op_run(ctx, kind: str, payload: str) -> dict:
    try:
        request = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise deps["store"].ToolError(2, f"--payload must be JSON: {exc}")
    if not isinstance(request, dict):
        raise deps["store"].ToolError(2, "--payload must be a JSON object")
    result = invoke(ctx, kind, request)
    return {"status": "ok", **result}


def op_extract_text(ctx, input_path: str, output: str | None = None) -> dict:
    store = deps["store"]
    if not input_path or not os.path.exists(input_path):
        raise store.ToolError(2, f"input not found: {input_path}")
    ext = os.path.splitext(input_path)[1].lower()
    if ext in store.TEXT_SUFFIXES:
        text = store.read_text(input_path)
        via = "builtin-text-reader"
    else:
        result = invoke(ctx, "extract", {"path": os.path.abspath(input_path)})
        text = result["result"].get("text", "")
        via = "extract-adapter"
        if not text.strip():
            audit = {"file": os.path.basename(input_path)}
            ctx.audit("adapters.extract-empty", input_path, audit)
            raise store.ToolError(4, "extract adapter returned no text", {"input": input_path})
    out = ctx.out_path(output or f"extracted-{store.slug(os.path.basename(input_path))}.txt")
    store.write_text(out, store.normalize_text(text))
    return {"status": "ok", "input": input_path, "output": out, "via": via,
            "words": store.words(text), "chars": len(text)}


def register(groups, common):
    g = groups["adapters"]

    def sub(cmd, help_text, func, /, **opts):
        p = g.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)

    sub("check", "Report which local adapters are configured and installed", op_check)
    sub("run", "Invoke a configured adapter with a JSON payload", op_run,
        kind=(("--kind",), dict(required=True, choices=list(KINDS))),
        payload=(("--payload",), dict(required=True)))
    sub("extract-text", "Extract text from PDF/Office/EPUB via the local extract adapter", op_extract_text,
        input_path=(("--input", "-i"), dict(required=True)),
        output=(("--output", "-o"), dict(default=None)))
