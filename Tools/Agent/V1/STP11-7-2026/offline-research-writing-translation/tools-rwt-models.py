#!/usr/bin/env python3
"""tools-rwt-models.py - Optional local model adapters (OCR, STT, MT, LLM, embeddings, rerank, VLM).

No models are bundled. Each adapter is configured in the workspace config under
"models": {kind: {"command": ["exe", "arg1", ...], "timeout_seconds": N, "input": "file|arg|stdin"}}.
The tool invokes the command with argv (NO shell), bounded time, and captures
stdout. Unconfigured adapters return exit code 3 with the exact configuration
instructions. A deterministic fallback exists only where noted (summarize:
extractive; everything else: none).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_mod", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_mod"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

MODEL_KINDS = ["ocr", "stt", "mt", "llm", "embed", "rerank", "vlm", "summarize", "diarize", "translate-document"]

def _workspace_config(args):
    cfg_path = os.path.join(args.workspace, C.CONFIG_PATH) if hasattr(args, "workspace") else C.CONFIG_PATH
    if not os.path.isfile(cfg_path):
        return {}, None
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return json.load(fh), cfg_path

def get_adapter(kind, workspace):
    cfg_path = os.path.join(workspace, C.CONFIG_PATH)
    if not os.path.isfile(cfg_path):
        return None, "no RWT workspace at this path (.rwt/workspace.json missing)"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    model = (cfg.get("models") or {}).get(kind)
    if not model:
        return None, f"no local '{kind}' model configured in {C.CONFIG_PATH}"
    command = model.get("command")
    if not command or not isinstance(command, list):
        return None, f"models.{kind}.command must be a JSON list, e.g. [\"whisper-local\", \"--model\", \"large\"]"
    if not shutil.which(command[0]) and not os.path.isfile(command[0]):
        return None, f"models.{kind}.command[0] '{command[0]}' not found on PATH or filesystem"
    return {"command": command, "timeout_seconds": int(model.get("timeout_seconds", 300)),
            "input": model.get("input", "file")}, None

def run_adapter(kind, args, stdin_text=None):
    adapter, err = get_adapter(kind, args.workspace)
    if err:
        raise C.UnavailableError(
            f"Local '{kind}' model unavailable: {err}. "
            f"Configure it in {args.workspace}/{C.CONFIG_PATH} under \"models\": "
            f"{{\"{kind}\": {{\"command\": [\"your-local-model\", \"--args\"], \"timeout_seconds\": 300}}}}. "
            "The command receives the input file path as its final argument (or stdin when input=stdin) "
            "and must print results to stdout. Nothing is downloaded automatically.")
    cmd = list(adapter["command"])
    if adapter["input"] == "file":
        if not getattr(args, "input_file", None):
            raise C.ValidationError("This adapter expects --input-file")
        cmd = cmd + [args.input_file]
    elif adapter["input"] == "arg":
        if not getattr(args, "text", None):
            raise C.ValidationError("This adapter expects --text")
        cmd = cmd + [args.text]
    try:
        proc = subprocess.run(cmd, input=(stdin_text or getattr(args, "text", "") or "").encode("utf-8") if adapter["input"] == "stdin" else None,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=adapter["timeout_seconds"], check=False)
    except subprocess.TimeoutExpired:
        raise C.UnavailableError(f"models.{kind} timed out after {adapter['timeout_seconds']}s")
    out = proc.stdout.decode("utf-8", errors="replace")
    err_out = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise C.UnavailableError(f"models.{kind} exited {proc.returncode}: {err_out[:500]}")
    return {"adapter": kind, "returncode": proc.returncode, "stdout": out[:200000],
            "stderr_tail": err_out[-2000:], "timeout_seconds": adapter["timeout_seconds"]}

def cmd_generic(kind):
    def _cmd(args):
        return run_adapter(kind, args)
    return _cmd

def cmd_mt(args):
    return run_adapter("mt", args)

def cmd_ocr(args):
    return run_adapter("ocr", args)

def cmd_stt(args):
    if getattr(args, "diarize", False):
        return run_adapter("diarize", args)
    return run_adapter("stt", args)

def cmd_embed(args):
    return run_adapter("embed", args)

def cmd_rerank(args):
    return run_adapter("rerank", args)

def cmd_llm(args):
    return run_adapter("llm", args)

def cmd_vlm(args):
    return run_adapter("vlm", args)

def cmd_model_route(args):
    cfg, _ = _workspace_config(args)
    models = (cfg or {}).get("models", {})
    pair = args.pair.lower()
    risk = args.risk.lower()
    content = args.content_type.lower()
    available = {k: v for k, v in models.items() if isinstance(v, dict) and v.get("command")}
    domain_pref = {"legal": ["mt"], "medical": ["mt"], "technical": ["mt"], "general": ["mt"]}.get(content, ["mt"])
    chosen = None
    for pref in domain_pref:
        if pref in available:
            chosen = pref
            break
    order_of_preference = ["mt", "llm"]
    if not chosen:
        for pref in order_of_preference:
            if pref in available:
                chosen = pref
    high_risk = risk in ("high", "critical")
    return {"language_pair": pair, "risk": risk, "content_type": content,
            "configured_models": sorted(available),
            "selected": chosen,
            "requirements": (["domain-adapted model preferred", "human bilingual review mandatory",
                              "back-translation of high-risk segments"] if high_risk else
                             ["post-editing required", "terminology enforced (terms.consistency)"]),
            "fallback": "human translation (no MT) when no suitable local model is configured" if not chosen else None,
            "note": "Routing is a policy decision from the local registry; it never sends text out of the environment."}

def cmd_summarize(args):
    adapter, _err = get_adapter("summarize", args.workspace)
    if adapter:
        return run_adapter("summarize", args)
    # deterministic extractive fallback via the text engine
    this = os.path.dirname(os.path.abspath(__file__))
    import importlib.util
    spec = importlib.util.spec_from_file_location("rwt_text_models", os.path.join(this, "tools-rwt-text.py"))
    textmod = importlib.util.module_from_spec(spec)
    sys.modules["rwt_text_models"] = textmod
    spec.loader.exec_module(textmod)
    result = textmod.cmd_summarize(argparse.Namespace(file=args.input_file, query=args.query, sentences=args.sentences))
    result["method"] = "extractive-fallback (no local generative model configured)"
    result["abstractive"] = "configure models.summarize for a local multilingual model"
    return result

COMMANDS = {
    "mt": cmd_mt, "ocr": cmd_ocr, "stt": cmd_stt, "embed": cmd_embed,
    "rerank": cmd_rerank, "llm": cmd_llm, "vlm": cmd_vlm,
    "route": cmd_model_route, "summarize": cmd_summarize,
}

def build_parser(prog="rwt models"):
    p = argparse.ArgumentParser(prog=prog, description="Local model adapters (none bundled; configure in workspace.json).")
    sub = p.add_subparsers(dest="action", metavar="action")

    def add_adapter(name, fn, helptext, needs_file=True):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--workspace", default=".")
        sp.add_argument("--input-file", required=True, help="Path passed to the local model command")
        sp.add_argument("--text")
        if name == "stt":
            sp.add_argument("--diarize", action="store_true", help="Use the 'diarize' adapter instead")
        sp.set_defaults(func=fn)

    add_adapter("mt", cmd_mt, "Offline MT via configured local command (argv, no shell)")
    add_adapter("ocr", cmd_ocr, "Offline OCR via configured local command")
    add_adapter("stt", cmd_stt, "Offline speech-to-text via configured local command")
    add_adapter("embed", cmd_embed, "Local embeddings via configured local command")
    add_adapter("rerank", cmd_rerank, "Local reranker via configured local command")

    sp = sub.add_parser("llm", help="Local multilingual LLM via configured local command")
    sp.add_argument("--workspace", default="."); sp.add_argument("--text", required=True)
    sp.add_argument("--input-file"); sp.set_defaults(func=cmd_llm)

    add_adapter("vlm", cmd_vlm, "Local vision-language model via configured local command")

    sp = sub.add_parser("route", help="Deterministic routing policy over configured local models")
    sp.add_argument("--workspace", default="."); sp.add_argument("--pair", default="en-es")
    sp.add_argument("--risk", default="medium", choices=["low", "medium", "high", "critical"])
    sp.add_argument("--content-type", default="general"); sp.set_defaults(func=cmd_model_route)

    sp = sub.add_parser("summarize", help="Summarize: local model if configured, else deterministic extractive fallback")
    sp.add_argument("--workspace", default="."); sp.add_argument("--input-file", required=True)
    sp.add_argument("--text"); sp.add_argument("--query", default=None)
    sp.add_argument("--sentences", type=int, default=4); sp.set_defaults(func=cmd_summarize)
    return p

def run(argv):
    parser = build_parser(prog="rwt models")
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


def main():
    try:
        out = run(sys.argv[1:])
    except C.RwtError as exc:
        print(json.dumps({"error": str(exc), "exit_code": exc.exit_code}, ensure_ascii=False))
        sys.exit(exc.exit_code)
    except C.ValidationError as exc:
        print(json.dumps({"error": str(exc), "exit_code": 2}, ensure_ascii=False))
        sys.exit(2)
    if isinstance(out, int):
        sys.exit(out)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
