#!/usr/bin/env python3
"""android-agent-toolkit :: primary entry point (module 7 of 7, the only file you run).

Usage:
  python3 android-agent-toolkit/tools-android-agent-toolkit.py --help
  python3 android-agent-toolkit/tools-android-agent-toolkit.py capabilities
  python3 android-agent-toolkit/tools-android-agent-toolkit.py <group> <command> [options] [--json]

Global options (accepted before or after the group):
  --config PATH   JSON file deep-merged over asset-default-config.json
  --json          emit the structured JSON envelope instead of text
  --out PATH      output file or directory (command dependent; default: ./out)
  --yes           approve destructive, privileged, signing or state-changing actions
  --verbose       include extra detail and tracebacks for internal errors
  --version       print the toolkit version

Exit codes: 0 ok | 1 error/failed check | 2 usage or invalid input | 3 approval required |
            4 requirement missing (tool, device, host feature, license, network)

Load order: tools-toolkit-core.py -> tools-artifact-analysis.py -> tools-tool-adapters.py ->
tools-host-environment.py -> tools-triage-reporting.py -> tools-catalog-engine.py -> this file.
"""

import argparse
import importlib.util
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))


def _bootstrap_core():
    path = os.path.join(HERE, "tools-toolkit-core.py")
    if not os.path.isfile(path):
        sys.stderr.write("error: tools-toolkit-core.py is missing next to this entry point.\n")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("aatk_core", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aatk_core"] = mod
    spec.loader.exec_module(mod)
    return mod


core = _bootstrap_core()
ARTIFACT_MOD = core.get_sibling("tools-artifact-analysis.py")
ADAPTER_MOD = core.get_sibling("tools-tool-adapters.py")
HOST_MOD = core.get_sibling("tools-host-environment.py")
TRIAGE_MOD = core.get_sibling("tools-triage-reporting.py")
CATALOG_MOD = core.get_sibling("tools-catalog-engine.py")

HANDLERS = {}
for _mod in (HOST_MOD, TRIAGE_MOD, ARTIFACT_MOD, ADAPTER_MOD, CATALOG_MOD):
    HANDLERS.update(_mod.COMMANDS)

GLOBAL_DEFAULTS = {"config": None, "json": False, "out": None, "yes": False, "verbose": False}


def _global_parent():
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("global options")
    g.add_argument("--config", metavar="PATH", default=argparse.SUPPRESS, help="user config JSON (deep-merged)")
    g.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="structured JSON output")
    g.add_argument("--out", metavar="PATH", default=argparse.SUPPRESS,
                   help="output file or directory (command dependent; default ./out)")
    g.add_argument("--yes", action="store_true", default=argparse.SUPPRESS,
                   help="approve destructive/privileged/state-changing actions")
    g.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS, help="extra detail")
    return p


class _Parser(argparse.ArgumentParser):
    """argparse parser whose usage errors are JSON envelopes when --json is requested."""

    def error(self, message):
        if "--json" in sys.argv[1:]:
            env = {"ok": False, "command": "usage", "status": "ERROR", "data": None, "warnings": [],
                   "error": {"code": "E_USAGE", "message": message,
                             "hint": "Run with --help to see the command syntax.", "status": None, "data": None}}
            sys.stdout.write(json.dumps(env, indent=2, default=str) + "\n")
            sys.exit(2)
        super().error(message)


def build_parser():
    gp = _global_parent()
    parser = _Parser(
        prog="tools-android-agent-toolkit.py", parents=[gp],
        description="Android Agent Toolkit: local, structured Android artifact, host, build, "
                    "triage, compatibility and preservation tooling. Run 'capabilities' to list every skill.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit codes: 0 ok, 1 error, 2 usage/input, 3 approval required, 4 requirement missing.")
    parser.add_argument("--version", action="version", version="%(prog)s " + core.TOOLKIT_VERSION)
    sub = parser.add_subparsers(dest="group", metavar="<group>")
    routes = set()
    groups = {}
    group_subs = {}

    def add(group, name, help_text):
        if group not in groups:
            groups[group] = sub.add_parser(group, parents=[gp], help="%s commands" % group,
                                           description="%s commands" % group)
            group_subs[group] = groups[group].add_subparsers(dest="command", metavar="<command>")
        p = group_subs[group].add_parser(name, parents=[gp], help=help_text, description=help_text)
        routes.add((group, name))
        return p


    cap = sub.add_parser("capabilities", parents=[gp], help="list every skill capability with its toolkit command",
                         description="List all 324 catalog skills with tier, requirements and toolkit command.")
    cap.add_argument("--category", type=int, default=None, help="category number 1-24")
    cap.add_argument("--requires", default=None, help="filter by requirement, e.g. authorization, device, tool:adb")
    routes.add(("capabilities", None))

    c = add("catalog", "list", "list catalog skills")
    c.add_argument("--search", default=None)
    c.add_argument("--category", type=int, default=None)
    add("catalog", "search", "search catalog skills").add_argument("term")
    add("catalog", "show", "show one skill record").add_argument("skill_id")
    add("catalog", "contract", "inputs, outputs and approval needs for one skill").add_argument("skill_id")
    add("catalog", "stats", "catalog counts by category, tier and requirement")
    add("catalog", "validate", "verify every skill resolves to a registered command")

    add("host", "profile", "host capability profile (CPU, KVM, memory, disk, tools)")
    add("host", "env", "environment variables, SDK roots and search paths")
    add("host", "limits", "configured limits, timeouts and approval policy")

    sdk = add("sdk", "inventory", "installed SDK packages (with --health and --caches)")
    sdk.add_argument("--health", action="store_true")
    sdk.add_argument("--caches", action="store_true")
    add("sdk", "licenses", "read-only SDK license audit")
    add("sdk", "footprint", "SDK disk usage by directory")
    add("sdk", "avds", "list Android Virtual Devices")
    add("sdk", "jdk-route", "find JDK installs and route to OpenJDK 17")
    m = add("sdk", "matrix", "recommended SDK lanes (platforms, build-tools, ndk, system-images)")
    m.add_argument("--kind", default="platforms", help="platforms|build-tools|ndk|system-images")

    add("build", "inspect", "detect build systems in a project dir (no execution)").add_argument("path")
    add("build", "compat", "AGP / Gradle / JDK compatibility table")

    add("integrity", "verify", "hash files and compare with pins").add_argument("--files", nargs="+", required=True)
    add("integrity", "pins-init", "create an empty pin store in --out")
    add("integrity", "pins-add", "pin current hashes of files").add_argument("--files", nargs="+", required=True)
    add("integrity", "pins-verify", "verify all pinned files")

    st = add("selftest", "run", "run the startup self-test suite")
    st.add_argument("--only", default=None, help="run one suite entry by id")
    add("selftest", "status", "show the last stored self-test result")

    wc = add("workspace", "clean", "remove generated build/cache output (dry run unless --yes)")
    wc.add_argument("--target", required=True)

    art = {}
    for name, hlp in (("detect", "magic-byte and container classification"),
                      ("zip-health", "ZIP structure, CRC, traversal, duplicates"),
                      ("pkginfo", "reconciled package identity"),
                      ("axml", "decode binary AndroidManifest.xml or APK manifest"),
                      ("arsc", "resources.arsc summary"),
                      ("dex", "DEX header, checksum and string inventory"),
                      ("elf", "ELF headers, dynamic section and page alignment"),
                      ("signing", "APK signature schemes and certificates"),
                      ("abi", "native ABI coverage and 16 KB readiness"),
                      ("media", "image/audio/video/texture headers"),
                      ("inventory", "categorised entry inventory with compression stats")):
        art[name] = add("artifact", name, hlp)
    art["detect"].add_argument("file")
    art["zip-health"].add_argument("file")
    art["zip-health"].add_argument("--align", action="store_true", help="also check 4-byte and 16 KB alignment")
    art["zip-health"].add_argument("--no-crc", action="store_true", help="skip CRC verification")
    art["pkginfo"].add_argument("file")
    art["axml"].add_argument("file")
    art["arsc"].add_argument("file")
    art["dex"].add_argument("file")
    art["dex"].add_argument("--strings-filter", default=None, help="regex applied to DEX strings")
    art["elf"].add_argument("file")
    art["elf"].add_argument("--symbols", action="store_true")
    art["signing"].add_argument("file")
    art["signing"].add_argument("--compare", default=None, help="second APK to compare certificates with")
    art["abi"].add_argument("target")
    art["abi"].add_argument("--page-size", action="store_true", help="report only 16 KB page readiness")
    art["media"].add_argument("file")
    art["inventory"].add_argument("file")
    art["inventory"].add_argument("--sizes", action="store_true")

    pk = {}
    pk["splits-resolve"] = add("package", "splits-resolve", "choose split APKs for a device spec")
    pk["splits-resolve"].add_argument("target")
    pk["splits-resolve"].add_argument("--abi", default=None)
    pk["splits-resolve"].add_argument("--api", type=int, default=None)
    pk["splits-resolve"].add_argument("--density", type=int, default=None)
    pk["splits-resolve"].add_argument("--locale", default=None)
    pk["normalize"] = add("package", "normalize", "unpack XAPK/APKM/APKS into APKs and install-plan.json")
    pk["normalize"].add_argument("target")
    pk["normalize"].add_argument("--script", action="store_true", help="also write an adb install script")
    add("package", "convert-advice", "conversion route advice (what is and is not possible)").add_argument("file")
    md = add("package", "manifest-diff", "compare two manifests field by field")
    md.add_argument("a")
    md.add_argument("b")

    tr = add("triage", "analyze", "classify crash, ANR, tombstone and installer log blocks")
    tr.add_argument("logs", nargs="+")
    rt = add("triage", "retrace", "retrace an obfuscated stack trace with a ProGuard/R8 mapping")
    rt.add_argument("--mapping", required=True)
    rt.add_argument("--trace", required=True)

    sq = add("storage", "sqlite", "inspect an SQLite database read-only (or --diff two)")
    sq.add_argument("db")
    sq.add_argument("--diff", default=None)
    add("storage", "assets", "inventory an assets/OBB directory with duplicate detection").add_argument("dir")
    add("storage", "validate", "validate a save, backup or data file").add_argument("file")

    sec = add("security", "scan", "static security findings for an APK or decoded directory")
    sec.add_argument("apk", nargs="?", default=None)
    sec.add_argument("--decoded", default=None, help="decoded APK directory instead of an APK")

    cc = add("compat", "check", "check an APK against behaviour rules and a device API")
    cc.add_argument("apk")
    cc.add_argument("--device-api", type=int, default=None)
    cc.add_argument("--release", action="store_true")
    add("compat", "levels", "Android API level reference")
    add("compat", "matrix", "test lanes and AGP/Gradle/JDK matrix")
    ru = add("compat", "rules", "behaviour rules, optionally filtered by topic")
    ru.add_argument("--topic", default=None)

    pr = add("preserve", "provenance", "record hashes and metadata of files (and optional tool versions)")
    pr.add_argument("--files", nargs="+", required=True)
    pr.add_argument("--note", default=None)
    pr.add_argument("--tools", action="store_true", help="include versions of installed registry tools")
    add("preserve", "snapshot", "copy originals read-only into a snapshot directory").add_argument(
        "--files", nargs="+", required=True)
    add("preserve", "package", "zip files with a provenance record (--out must end .zip)").add_argument(
        "--files", nargs="+", required=True)
    rp = add("preserve", "report", "compare APKs, or run a release gate on a results file")
    rp.add_argument("--apk", nargs="+", default=None)
    rp.add_argument("--gate", default=None, help="results JSON (e.g. selftest-status.json) to gate on")

    add("recovery", "assess", "ordered recovery plan from read-only APK evidence").add_argument("apk")

    add("tool", "list", "whitelisted tools and whether each is installed")
    tloc = add("tool", "locate", "locate and version-check one whitelisted tool")
    tloc.add_argument("name")
    trun = add("tool", "run", "run a whitelisted tool: tool run <name> -- <args>")
    trun.add_argument("name")
    trun.add_argument("tool_args", nargs=argparse.REMAINDER)

    add("device", "list", "list adb devices (DEVICE REQUIRED when none is attached)")

    return parser, routes


def _strip_text(value):
    """Remove the internal '_text' rendering hints from JSON output."""
    if isinstance(value, dict):
        return {k: _strip_text(v) for k, v in value.items() if k != "_text"}
    if isinstance(value, list):
        return [_strip_text(v) for v in value]
    return value


def _render(env, args):
    if args.json:
        return json.dumps(_strip_text(env), indent=2, default=str)
    return core.render_text(env, verbose=args.verbose)


def main(argv=None):
    parser, routes = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        parser.print_help(sys.stderr)
        return 2
    args = parser.parse_args(argv)
    for k, v in GLOBAL_DEFAULTS.items():
        if not hasattr(args, k):
            setattr(args, k, v)

    if args.group is None:
        parser.print_help()
        return 0
    if args.group == "capabilities":
        command_key = ("capabilities", None)
    else:
        if getattr(args, "command", None) is None:
            parser.print_help(sys.stderr)
            return 2
        command_key = (args.group, args.command)

    try:
        cfg = core.load_config(args.config)
        default_out = os.path.join(os.getcwd(), (cfg.get("paths") or {}).get("default_output_dir", "out"))
        args.out_dir = core.expand_path(args.out) if args.out else core.expand_path(default_out)
        if args.group == "catalog" and args.command == "validate":
            args._routes = routes
        handler = HANDLERS.get(command_key)
        if handler is None:
            raise core.ToolkitError("E_USAGE", "Unknown command: %s" % " ".join(
                x for x in command_key if x), hint="Run: capabilities  or  --help")
        env = handler(args, cfg)
        env["ok"] = True
        print(_render(env, args))
        return 0
    except core.ToolkitError as exc:
        env = core.envelope_error(" ".join(x for x in command_key if x), exc)
        if args.json:
            print(json.dumps(_strip_text(env), indent=2, default=str))
        else:
            sys.stderr.write(core.render_text(env, verbose=args.verbose) + "\n")
        return exc.exit_code
    except KeyboardInterrupt:
        sys.stderr.write("interrupted; no partial output was committed\n")
        return 1
    except Exception as exc:  # noqa: BLE001 - last-resort structured error
        env = {"ok": False, "command": " ".join(x for x in command_key if x), "status": "ERROR",
               "error": {"code": "E_INTERNAL", "message": "%s: %s" % (type(exc).__name__, exc),
                         "hint": "Re-run with --verbose for a traceback; report the command and input."}}
        if args.verbose:
            env["error"]["traceback"] = traceback.format_exc()
        if args.json:
            print(json.dumps(env, indent=2, default=str))
        else:
            sys.stderr.write(core.render_text(env, verbose=args.verbose) + "\n")
        return 1


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # quiet exit when piped into head/less
    except (ImportError, AttributeError, ValueError):
        pass
    sys.exit(main())
