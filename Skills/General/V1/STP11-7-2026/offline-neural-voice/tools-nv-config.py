#!/usr/bin/env python3
"""tools-nv-config.py - configuration workbench for the Offline Neural Voice Skill Tool.

Creates, validates, diffs, migrates and explains the configuration document that
controls every other tool. Safe to run at any time; it never touches audio.

Usage
-----
  python tools-nv-config.py show     [--format json|text]
  python tools-nv-config.py validate --config work/my-config.json
  python tools-nv-config.py init     --out work/my-config.json [--force]
  python tools-nv-config.py diff     --config work/my-config.json
  python tools-nv-config.py migrate  --config work/old-config.json --out work/new-config.json
  python tools-nv-config.py explain  execution.offline

Commands
  show       print the fully merged configuration and where each layer came from
  validate   check a file against the schema and the structural rules
  init       write a complete configuration file (defaults plus overrides)
  diff       compare a file with the built-in defaults
  migrate    upgrade an older configuration document to the current schema version
  explain    describe one key, its type, default and effect
  selftest   run the configuration self test

Precedence: built-in defaults < --config file < ONV_* environment < CLI flags.
Every path in the document is relative to the Skill Tool folder and may not
escape it (parent traversal and absolute paths are rejected).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if "nvcore" not in sys.modules:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("nvcore", _HERE / "tools-nvcore.py")
    _module = _ilu.module_from_spec(_spec)
    sys.modules["nvcore"] = _module
    _spec.loader.exec_module(_module)

import nvcore

TOOL = "offline-neural-voice/config"
EXPLAINED_KEYS = {
    "execution.offline": ("boolean", "hard-blocks sockets during synthesis and QA"),
    "execution.allow_network": ("boolean", "permits absolute paths only when explicitly set; "
                                           "network use is still limited to fetch-model"),
    "execution.timeout_seconds": ("number", "per-utterance inference timeout in seconds"),
    "execution.run_timeout_seconds": ("number", "whole-run deadline; cancellation is cooperative"),
    "execution.strict_checksum": ("boolean", "verify model digests against the registry before use"),
    "execution.allow_fallback_engine": ("boolean", "allow the clearly labelled degraded engine"),
    "execution.fail_on_blocked_rights": ("boolean", "block synthesis when the rights gate fails"),
    "execution.max_chars": ("integer", "hard character limit for one synthesis request"),
    "execution.allow_absolute_paths": ("boolean", "operator override for trusted absolute paths"),
    "execution.overwrite": ("boolean", "allow replacing existing output artifacts by default"),
    "inference.intra_op_threads": ("integer", "ONNX Runtime CPU threads; keep at CPU count or less"),
    "inference.providers": ("array", "execution providers passed to onnxruntime"),
    "synthesis.length_scale": ("number", "pace multiplier used when a preset omits one"),
    "synthesis.seed": ("integer", "sampler seed; noise scales are stochastic, so takes differ"),
    "mastering.target_lufs": ("number", "integrated loudness target for mastered audio"),
    "mastering.max_true_peak_dbfs": ("number", "true peak ceiling enforced by the limiter"),
    "mastering.loudness_tolerance_lu": ("number", "accepted deviation from the loudness target"),
    "qa.min_duration_s": ("number", "lower bound used by acceptance checks"),
    "qa.max_duration_s": ("number", "upper bound used by acceptance checks"),
    "qa.min_word_accuracy": ("number", "minimum ASR word accuracy when transcript checks run"),
    "safety.require_approval_for_release": ("boolean", "package and publish paths need approval"),
    "safety.require_approval_for_delete": ("boolean", "destructive operations need approval"),
    "safety.require_consent_for_cloning": ("boolean", "cloned voices require a consent record"),
    "journal.enabled": ("boolean", "append-only audit trail"),
    "journal.max_bytes": ("integer", "journal size before it rotates"),
    "paths.models_dir": ("string", "where installed models live (relative)"),
    "paths.output_dir": ("string", "where finished audio is written (relative)"),
    "paths.journal_path": ("string", "audit journal file (relative)"),
    "paths.allowed_roots": ("array", "extra directories allowed for inputs, still inside the skill"),
}


def _emit(payload, args, *, text_lines=None):
    if args.quiet and payload.get("ok"):
        return nvcore.EXIT_OK
    if args.format == "text" and text_lines is not None:
        sys.stdout.write("\n".join(text_lines) + "\n")
    else:
        nvcore.print_json(payload)
    return nvcore.EXIT_OK if payload.get("ok") else nvcore.EXIT_FAIL


def cmd_show(args):
    cfg = nvcore.load_config(args.config, root=nvcore.skill_root())
    payload = nvcore.result_envelope(
        ok=True, tool=TOOL, command="show", data={
            "summary": "effective configuration from %d layer(s)" % len(cfg.sources),
            "sources": cfg.sources,
            "root": ".",
            "allow_absolute_paths": cfg.allow_absolute,
            "config": cfg.as_dict(),
            "warnings": getattr(cfg, "warnings", []),
        })
    lines = ["sources: %s" % ", ".join(cfg.sources),
             "absolute paths allowed: %s" % cfg.allow_absolute, ""]
    for section, values in cfg.data.items():
        if not isinstance(values, dict):
            lines.append("%s = %s" % (section, values))
            continue
        lines.append("[%s]" % section)
        for key, value in values.items():
            lines.append("  %-28s %s" % (key, json.dumps(value)))
        lines.append("")
    return _emit(payload, args, text_lines=lines)


def cmd_validate(args):
    path = Path(args.config) if args.config else None
    if path is None:
        raise nvcore.UsageError("validate needs --config <file>",
                                hint="create one with `init --out work/my-config.json`")
    if not path.is_absolute():
        path = nvcore.skill_root() / args.config
    if not path.is_file():
        raise nvcore.InputError("configuration file not found: %s" % args.config)
    problems = []
    warnings = []
    data = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        problems.append("invalid JSON at line %s column %s: %s" % (exc.lineno, exc.colno, exc.msg))
    schema_errors = []
    if data is not None:
        structural, extra = nvcore.validate_config_dict(data)
        problems.extend(structural)
        warnings.extend(extra.get("warnings", []))
        schema_path = nvcore.skill_root() / "asset-config-schema.json"
        if schema_path.is_file():
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            schema_errors = nvcore.validate_json(data, schema)
            problems.extend("schema: %s" % e for e in schema_errors)
        try:
            cfg = nvcore.load_config(args.config, root=nvcore.skill_root())
            for key in ("paths.models_dir", "paths.output_dir", "paths.work_dir"):
                cfg.resolve(cfg.get(key))
        except nvcore.NVError as exc:
            problems.append("path validation: %s" % exc.message)
    ok = not problems
    payload = nvcore.result_envelope(
        ok=ok, tool=TOOL, command="validate",
        data={"summary": "%s: %s" % (args.config, "valid" if ok else "%d problem(s)" % len(problems)),
              "file": args.config, "valid": ok, "problems": problems,
              "schema_errors": schema_errors, "warnings": warnings},
        errors=[] if ok else [{"code": "invalid-config", "message": p, "hint": None}
                              for p in problems])
    lines = ["%s: %s" % (args.config, "VALID" if ok else "INVALID")]
    lines.extend("  problem: %s" % p for p in problems)
    lines.extend("  warning: %s" % w for w in warnings)
    return _emit(payload, args, text_lines=lines)


def cmd_init(args):
    cfg_file = args.out or "work/my-config.json"
    target = nvcore.skill_root() / cfg_file if not Path(cfg_file).is_absolute() else Path(cfg_file)
    if target.exists() and not args.force:
        raise nvcore.InputError("refusing to overwrite %s" % cfg_file,
                                hint="pass --force to replace it")
    data = json.loads(json.dumps(nvcore.DEFAULT_CONFIG))
    data["skill"]["config_version"] = nvcore.__version__
    if args.offline:
        data["execution"]["offline"] = True
    if args.threads:
        data["inference"]["intra_op_threads"] = int(args.threads)
    nvcore.atomic_write_json(target, data, overwrite=True)
    payload = nvcore.result_envelope(
        ok=True, tool=TOOL, command="init",
        data={"summary": "wrote %s with %d sections" % (cfg_file, len(data) - 1),
              "file": cfg_file, "bytes": target.stat().st_size,
              "next": "python tools-nv-config.py validate --config %s" % cfg_file},
        artifacts=[{"kind": "config", "path": cfg_file,
                    "sha256": nvcore.sha256_file(target)}])
    return _emit(payload, args, text_lines=["wrote %s (%d bytes)" % (cfg_file, target.stat().st_size)])


def cmd_diff(args):
    if not args.config:
        raise nvcore.UsageError("diff needs --config <file>")
    path = Path(args.config) if Path(args.config).is_absolute() else nvcore.skill_root() / args.config
    data = nvcore.read_json(path, what="configuration file")
    default = nvcore.DEFAULT_CONFIG
    changes = []

    def walk(base, overlay, prefix=""):
        for key, value in overlay.items():
            dotted = "%s.%s" % (prefix, key) if prefix else key
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                walk(base[key], value, dotted)
            elif base.get(key) != value:
                changes.append({"key": dotted, "default": base.get(key), "configured": value})

    walk(default, data)
    payload = nvcore.result_envelope(
        ok=True, tool=TOOL, command="diff",
        data={"summary": "%d key(s) differ from the built-in defaults" % len(changes),
              "changes": changes, "file": args.config})
    lines = ["%s: %d change(s)" % (args.config, len(changes))]
    lines.extend("  %s: %s -> %s" % (c["key"], json.dumps(c["default"]), json.dumps(c["configured"]))
                 for c in changes)
    return _emit(payload, args, text_lines=lines)


def cmd_migrate(args):
    if not args.config:
        raise nvcore.UsageError("migrate needs --config <file>")
    path = Path(args.config) if Path(args.config).is_absolute() else nvcore.skill_root() / args.config
    data = nvcore.read_json(path, what="configuration file")
    notes = []
    version = data.get("schema_version")
    if version == nvcore.CONFIG_SCHEMA_VERSION:
        notes.append("already at schema version %d; only missing keys are filled in" % version)
    else:
        notes.append("upgrading schema version %r to %d" % (version, nvcore.CONFIG_SCHEMA_VERSION))
    merged = nvcore._deep_merge(nvcore.DEFAULT_CONFIG, data)
    merged["schema_version"] = nvcore.CONFIG_SCHEMA_VERSION
    renamed = {"paths.assets_dir": "paths.registry_path"}
    for old, new in renamed.items():
        old_section, old_key = old.split(".")
        new_section, new_key = new.split(".")
        if old_key in merged.get(old_section, {}):
            merged[new_section][new_key] = merged[old_section].pop(old_key)
            notes.append("renamed %s to %s" % (old, new))
    out = args.out or args.config
    target = Path(out) if Path(out).is_absolute() else nvcore.skill_root() / out
    nvcore.atomic_write_json(target, merged, overwrite=True)
    payload = nvcore.result_envelope(
        ok=True, tool=TOOL, command="migrate",
        data={"summary": "migrated %s -> schema %d" % (args.config, nvcore.CONFIG_SCHEMA_VERSION),
              "file": out, "notes": notes},
        artifacts=[{"kind": "config", "path": out, "sha256": nvcore.sha256_file(target)}])
    return _emit(payload, args, text_lines=["migrated %s -> %s" % (args.config, out)] + notes)


def cmd_explain(args):
    key = args.key
    if key not in EXPLAINED_KEYS:
        raise nvcore.InputError("unknown configuration key %r" % key,
                                hint="known keys: %s" % ", ".join(sorted(EXPLAINED_KEYS)[:8]) + ", ...")
    kind, effect = EXPLAINED_KEYS[key]
    default = nvcore.DEFAULT_CONFIG
    value = None
    for part in key.split("."):
        if isinstance(default, dict) and part in default:
            default = default[part]
            value = default
        else:
            value = None
            break
    payload = nvcore.result_envelope(
        ok=True, tool=TOOL, command="explain",
        data={"summary": "%s (%s): %s" % (key, kind, effect),
              "key": key, "type": kind, "default": value, "effect": effect})
    lines = ["%s" % key, "  type:    %s" % kind, "  default: %s" % json.dumps(value),
             "  effect:  %s" % effect]
    return _emit(payload, args, text_lines=lines)


def selftest(args=None):
    checks = []

    def check(name, fn):
        try:
            checks.append({"check": name, "ok": True, "detail": fn()})
        except Exception as exc:
            checks.append({"check": name, "ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)})

    def t_defaults():
        cfg = nvcore.load_config(None, root=nvcore.skill_root())
        assert cfg.get("mastering.target_lufs") == -14.0
        assert cfg.offline is True
        return "built-in defaults load with offline=true"

    def t_schema():
        path = nvcore.skill_root() / "asset-config-schema.json"
        data = json.loads(path.read_text())
        errors = nvcore.validate_json(nvcore.DEFAULT_CONFIG, data)
        assert not errors, errors[:3]
        broken = json.loads(json.dumps(nvcore.DEFAULT_CONFIG))
        broken["execution"]["offline"] = "yes"
        assert nvcore.validate_json(broken, data), "schema accepted a bad boolean"
        return "the shipped schema accepts defaults and rejects a wrong type"

    def t_traversal():
        cfg = nvcore.load_config(None, root=nvcore.skill_root())
        try:
            cfg.resolve("../../etc/passwd")
            raise AssertionError("traversal accepted")
        except nvcore.InputError:
            return "parent traversal is rejected"

    def t_migrate():
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "old.json"
        tmp.write_text(json.dumps({"schema_version": 0,
                                   "paths": {"assets_dir": "assets"}}), encoding="utf-8")
        merged = nvcore._deep_merge(nvcore.DEFAULT_CONFIG, json.loads(tmp.read_text()))
        merged["schema_version"] = nvcore.CONFIG_SCHEMA_VERSION
        assert merged["execution"]["offline"] is True
        return "an old partial document merges up to the current schema"

    check("defaults", t_defaults)
    check("schema", t_schema)
    check("traversal", t_traversal)
    check("migrate", t_migrate)
    ok = all(c["ok"] for c in checks)
    payload = {"ok": ok, "tool": TOOL, "version": nvcore.__version__, "checks": checks}
    if args is not None and getattr(args, "format", "text") == "json":
        nvcore.print_json(payload)
    else:
        for item in checks:
            print("[%s] %s %s" % ("PASS" if item["ok"] else "FAIL", item["check"], item["detail"]))
        print("\n%s: %d/%d configuration checks passed" % (
            "PASS" if ok else "FAIL", sum(1 for c in checks if c["ok"]), len(checks)))
    return nvcore.EXIT_OK if ok else nvcore.EXIT_FAIL


def build_parser():
    parser = argparse.ArgumentParser(prog="tools-nv-config.py",
                                     description="Configuration workbench for the Offline "
                                                 "Neural Voice Skill Tool.")
    parser.add_argument("--quiet", action="store_true")
    # --format lives on the subparsers so it works where people type it, after the
    # command, exactly like the primary entry point.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--format", choices=["json", "text"], default="text",
                        help="json (machine readable) or text (screen friendly)")
    subs = parser.add_subparsers(dest="command")

    show = subs.add_parser("show", help="print the merged configuration", parents=[common])
    show.add_argument("--config")
    show.set_defaults(func=cmd_show)

    validate = subs.add_parser("validate", help="validate a configuration file", parents=[common])
    validate.add_argument("--config")
    validate.set_defaults(func=cmd_validate)

    init = subs.add_parser("init", help="write a complete configuration file", parents=[common])
    init.add_argument("--out", default="work/my-config.json")
    init.add_argument("--force", action="store_true")
    init.add_argument("--offline", action="store_true", help="force offline mode (default)")
    init.add_argument("--threads", type=int, help="ONNX intra-op thread count")
    init.set_defaults(func=cmd_init)

    diff = subs.add_parser("diff", help="compare a file with the built-in defaults", parents=[common])
    diff.add_argument("--config")
    diff.set_defaults(func=cmd_diff)

    migrate = subs.add_parser("migrate", help="upgrade a configuration document", parents=[common])
    migrate.add_argument("--config")
    migrate.add_argument("--out")
    migrate.set_defaults(func=cmd_migrate)

    explain = subs.add_parser("explain", help="describe one configuration key", parents=[common])
    explain.add_argument("key")
    explain.set_defaults(func=cmd_explain)

    selftest_cmd = subs.add_parser("selftest", help="run the configuration self test", parents=[common])
    selftest_cmd.set_defaults(func=lambda a: selftest(a))
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "format", None):
        args.format = "text"
    if not getattr(args, "command", None):
        parser.print_help()
        return nvcore.EXIT_USAGE
    return nvcore.run_cli(lambda _argv: args.func(args), tool_name=TOOL, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
