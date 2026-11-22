#!/usr/bin/env python3
"""Voice AI Skill Toolkit — unified CLI entry point.

Production toolkit built from the Voice AI Skill Catalog (500 skill concepts
across 50 categories). One unified tool implements the shared, repeatable
workflow every catalog skill describes: define the task and constraints,
produce editable and traceable outputs, support human review, and validate
intelligibility, naturalness, latency, privacy, consent, technical quality,
accessibility and delivery.

Usage:
  python3 tools-voice-skill-tool.py <command> [options]
  python3 tools-voice-skill-tool.py help
  python3 tools-voice-skill-tool.py capabilities [--json]

Commands (see `help` for details):
  categories, list, search, show        catalog browsing
  brief                                 production brief / work order
  init-package                          scaffold a work-package folder
  audio-check                           WAV measurement + profile conformance
  transcript-check                      SRT/VTT/JSON transcript validation
  consent-check                         consent record validation
  validate-package                      whole-package validation
  qa-report                             human-review QA/handoff report
  selftest                              built-in end-to-end test
  capabilities, help, version           meta

Exit codes: 0 success · 1 internal error · 2 invalid input · 3 checks failed ·
130 cancelled. All commands accept --json unless noted. No network access,
no external services, no credentials required or accepted.
"""

import importlib.util
import json
import os
import sys

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name, filename):
    # Share one module instance per name via sys.modules so that classes
    # (e.g. tools_common.ToolError) keep a single identity across siblings.
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_common = _load_sibling("tools_common", "tools-common.py")
_catalog = _load_sibling("tools_catalog", "tools-catalog.py")
_brief = _load_sibling("tools_brief", "tools-brief.py")
_audio = _load_sibling("tools_audiocheck", "tools-audiocheck.py")
_validate = _load_sibling("tools_validate", "tools-validate.py")

ToolError = _common.ToolError
TOOLKIT_VERSION = "1.0.0"


def _print_skill_line(s):
    print("%-3s %-38s %-52s tier:%s" % (s["number"], s["id"][:38], s["name"][:52], s["tier"]))


def _cmd_categories(args):
    cats = _catalog.all_categories()
    if args.json:
        _common.emit_json({"ok": True, "command": "categories", "count": len(cats), "categories": cats})
        return 0
    print("Voice AI Skill Catalog — 50 categories (500 skills)\n")
    for c in cats:
        print("%2d. %s  [tier: %s]" % (c["id"], c["name"], c["tier"]))
        print("    covers: %s\n" % c["covers"])
    return 0


def _cmd_list(args):
    skills = _catalog.filter_skills(category=args.category, tier=args.tier)
    if args.json:
        _common.emit_json({"ok": True, "command": "list", "count": len(skills), "skills": skills})
        return 0
    print("%d skill(s)\n" % len(skills))
    print("%-3s %-38s %-52s %s" % ("#", "id", "name", "tier"))
    for s in skills:
        _print_skill_line(s)
    return 0


def _cmd_search(args):
    hits, total = _catalog.search_skills(args.query, category=args.category, tier=args.tier, limit=args.limit)
    if args.json:
        _common.emit_json({"ok": True, "command": "search", "query": args.query,
                           "count": len(hits), "total_matches": total, "hits": hits})
        return 0
    print("%d match(es) for '%s' (showing %d)\n" % (total, args.query, len(hits)))
    print("%-3s %-38s %-52s %s" % ("#", "id", "name", "tier"))
    for s in hits:
        _print_skill_line(s)
    return 0


def _resolve_or_fail(query):
    skill, candidates = _catalog.resolve_skill(query)
    if skill is None:
        if candidates:
            listing = "\n".join("  - %s (%s, %s)" % (c["name"], c["number"], c["id"]) for c in candidates[:12])
            raise ToolError("Ambiguous skill '%s'. Matches:\n%s" % (query, listing))
        raise ToolError("Unknown skill '%s'. Use `search` or `list` to find one." % query)
    return skill


def _cmd_show(args):
    skill = _resolve_or_fail(args.skill)
    if args.json:
        _common.emit_json({"ok": True, "command": "show", "skill": skill})
        return 0
    print("%s (catalog %s)" % (skill["name"], skill["number"]))
    print("id:          %s" % skill["id"])
    print("category:    %d. %s" % (skill["category_id"], skill["category_name"]))
    print("tier:        %s" % skill["tier"])
    print("focus:       %s" % skill["focus"])
    print("description: %s" % skill["description"])
    return 0


def _add_brief_arguments(p):
    p.add_argument("--skill", required=True, help="Skill slug, catalog number (e.g. 2.3) or unique name.")
    p.add_argument("--objective", help="Task objective for the brief (default: derived from the catalog entry).")
    p.add_argument("--language", default="en", help="BCP-47 language tag (default: en).")
    p.add_argument("--locale", help="Locale subtag refinement, e.g. US, GB, PH.")
    p.add_argument("--preset", help="Voice direction preset id from asset-direction-presets.json.")
    p.add_argument("--delivery-profile", help="Delivery profile id from asset-delivery-profiles.json.")
    p.add_argument("--sample-rate", type=int, help="Sample rate in Hz (default: profile value or 24000).")
    p.add_argument("--channels", type=int, help="Channel count (default: profile value or 1).")
    p.add_argument("--speaker-auth", help="Who authorized the voice/recording (recorded in the brief).")
    p.add_argument("--requester", help="Who requested the work (recorded in the brief).")
    p.add_argument("--tier-override", choices=["standard", "review", "elevated"],
                   help="Override the catalog tier for this brief/package.")


def _cmd_brief(args):
    skill = _resolve_or_fail(args.skill)
    brief_text, opts = _brief.build_brief(skill, args)
    if args.output:
        out = args.output
        parent = os.path.dirname(os.path.abspath(out))
        if not os.path.isdir(parent):
            raise ToolError("Output directory does not exist: %s" % parent)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(brief_text)
        if args.json:
            _common.emit_json({"ok": True, "command": "brief", "output": out,
                               "skill": skill["id"], "tier": opts["tier"], "bytes": len(brief_text)})
        else:
            print("brief written to %s (%d bytes)" % (out, len(brief_text)))
        return 0
    if args.json:
        _common.emit_json({"ok": True, "command": "brief", "skill": skill["id"],
                           "tier": opts["tier"], "brief": brief_text})
    else:
        sys.stdout.write(brief_text)
    return 0


def _cmd_init_package(args):
    result = _brief.init_package(args)
    if args.json:
        _common.emit_json(result)
    else:
        print("work package scaffolded at %s (id %s, tier %s)" % (result["package_dir"], result["package_id"], result["tier"]))
        for name in result["files_written"]:
            print("  wrote %s" % name)
        for name in result["files_skipped"]:
            print("  skipped %s (exists; use --force to overwrite tool-managed files)" % name)
        print("\nnext steps:")
        for step in result["next_steps"]:
            print("  - %s" % step)
    return 0


def _cmd_audio_check(args):
    result = _audio.run_audio_check(args.file, profile_id=args.profile,
                                    ffmpeg=args.ffmpeg, timeout=args.timeout)
    if args.json:
        _common.emit_json(dict({"command": "audio-check"}, **result))
        return 0 if result["verdict"] != "fail" else _common.EXIT_CHECK_FAILED
    m = result["measured"]
    print("audio-check: %s" % args.file)
    print("  format:       %s (%s)" % (m["format"], m["encoding"]))
    print("  sample rate:  %d Hz    channels: %d    width: %d bytes" % (m["sample_rate"], m["channels"], m["sample_width_bytes"]))
    print("  duration:     %.3f s (%d frames)" % (m["duration_seconds"], m["frames"]))
    print("  peak:         %.2f dBFS    rms: %.2f dBFS    crest: %.2f dB" % (m["peak_dbfs"], m["rms_dbfs"], m["crest_factor_db"]))
    print("  dc offset:    %.6f    clipped: %.4f%%    silence(<-50dBFS): %.2f%%" % (m["dc_offset"], m["clipped_percent"], m["silence_percent"]))
    if "integrated_lufs" in m:
        print("  integrated:   %.2f LUFS (ffmpeg ebur128)    true peak: %s dBTP" % (m["integrated_lufs"], m.get("true_peak_dbtp")))
    for w in m.get("warnings", []):
        print("  warning:      %s" % w)
    if result["profile"]:
        print("  profile:      %s" % result["profile"])
    print("  checks:")
    for c in result["checks"]:
        print("    [%-4s] %s — %s" % (c["status"].upper(), c["name"], c["detail"]))
    print("  verdict:      %s" % result["verdict"].upper())
    return 0 if result["verdict"] != "fail" else _common.EXIT_CHECK_FAILED


def _cmd_transcript_check(args):
    result = _validate.check_transcript(args.file)
    if args.json:
        _common.emit_json(dict({"command": "transcript-check"}, **result))
        return 0 if result["verdict"] != "fail" else _common.EXIT_CHECK_FAILED
    print("transcript-check: %s" % args.file)
    print("  format: %s    cues: %d    words: %d    span: %.2fs    wpm: %.1f" % (
        result["format"], result["cue_count"], result["word_count"], result["span_seconds"], result["wpm"]))
    if not result["issues"]:
        print("  issues: none")
    for issue in result["issues"]:
        print("  [%s] cue %d: %s" % (issue["severity"].upper(), issue["cue"], issue["issue"]))
    print("  verdict: %s" % result["verdict"].upper())
    return 0 if result["verdict"] != "fail" else _common.EXIT_CHECK_FAILED


def _cmd_consent_check(args):
    record = _common.read_json_file(args.file, "Consent record")
    result = _validate.check_consent_record(record)
    if args.json:
        _common.emit_json(dict({"command": "consent-check"}, **result))
        return 0 if result["verdict"] != "fail" else _common.EXIT_CHECK_FAILED
    print("consent-check: %s" % args.file)
    if not result["issues"]:
        print("  issues: none")
    for issue in result["issues"]:
        print("  [%s] %s: %s" % (issue["severity"].upper(), issue["field"], issue["issue"]))
    print("  verdict: %s" % result["verdict"].upper())
    return 0 if result["verdict"] != "fail" else _common.EXIT_CHECK_FAILED


def _cmd_validate_package(args):
    result = _validate.validate_package(args.dir, delivery_profile=args.profile,
                                        ffmpeg=args.ffmpeg, timeout=args.timeout)
    if args.json:
        _common.emit_json(dict({"command": "validate-package"}, **result))
        return 0 if result["verdict"] != "fail" else _common.EXIT_CHECK_FAILED
    print("validate-package: %s" % args.dir)
    print("  package: %s    tier: %s    consent required: %s" % (
        result.get("package_id"), result.get("tier"), result.get("consent_required")))
    for c in result["checks"]:
        print("  [%-4s] %s — %s" % (c["status"].upper(), c["name"], c["detail"]))
    print("  verdict: %s" % result["verdict"].upper())
    return 0 if result["verdict"] != "fail" else _common.EXIT_CHECK_FAILED


def _cmd_qa_report(args):
    report, result = _validate.render_qa_report(args.dir, delivery_profile=args.profile,
                                                ffmpeg=args.ffmpeg, timeout=args.timeout)
    if args.output:
        parent = os.path.dirname(os.path.abspath(args.output))
        if not os.path.isdir(parent):
            raise ToolError("Output directory does not exist: %s" % parent)
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(report)
        path = args.output
    else:
        path = None
        sys.stdout.write(report)
    if args.json:
        _common.emit_json({"ok": result["verdict"] != "fail", "command": "qa-report",
                           "verdict": result["verdict"],
                           "output": path, "package_id": result.get("package_id")})
    elif path:
        print("QA report written to %s (verdict %s)" % (path, result["verdict"].upper()))
    return 0 if result["verdict"] != "fail" else _common.EXIT_CHECK_FAILED


def _cmd_selftest(args):
    selftest = _load_sibling("tools_selftest", "tools-selftest.py")
    argv = []
    if args.keep:
        argv.append("--keep")
    return selftest.main(argv)


def _cmd_capabilities(args):
    caps = _catalog.capability_index()
    if args.json:
        _common.emit_json({"ok": True, "command": "capabilities",
                           "toolkit_version": TOOLKIT_VERSION, "capabilities": caps})
        return 0
    print("Voice AI Skill Toolkit — capabilities (v%s)\n" % TOOLKIT_VERSION)
    for cap in caps:
        print("  %-18s %s" % (cap["command"], cap["summary"]))
    return 0


def _cmd_version(args):
    catalog = _catalog.load_catalog()
    info = {
        "toolkit_version": TOOLKIT_VERSION,
        "catalog": catalog.get("schema"),
        "catalog_counts": catalog.get("counts"),
        "python": sys.version.split()[0],
    }
    if args.json:
        _common.emit_json({"ok": True, "command": "version", **info})
    else:
        print("voice-ai-skill-toolkit %s" % TOOLKIT_VERSION)
        print("catalog: %s (%d categories, %d skills)" % (
            catalog.get("schema"), catalog["counts"]["categories"], catalog["counts"]["skills"]))
        print("python:  %s (stdlib only)" % info["python"])
    return 0


HELP_TEXT = """Voice AI Skill Toolkit — unified CLI

USAGE
  python3 tools-voice-skill-tool.py <command> [options]

CATALOG BROWSING
  categories [--json]                      List the 50 categories and tiers
  list [--category ID|NAME] [--tier TIER] [--json]
                                           List skills (default: all 500, tier in standard|review|elevated)
  search QUERY [--category X] [--tier Y] [--limit N] [--json]
                                           Keyword search across all 500 skills
  show SKILL [--json]                      Full catalog entry for SKILL
                                           (slug | catalog number like 2.3 | exact name | unique substring)

PRODUCTION WORKFLOW
  brief --skill SKILL [options] [--output FILE] [--json]
      options: --objective TEXT --language TAG --locale SUB
               --preset ID --delivery-profile ID --sample-rate N --channels N
               --speaker-auth TEXT --requester TEXT --tier-override TIER
      Render a production brief for the skill from asset-brief-template.md.
  init-package --skill SKILL --dir PATH [--title T] [--package-id ID]
               [same brief options] [--force] [--json]
      Scaffold work-package.json, brief.md, normalized-text.txt,
      transcript.srt; plus consent-record.json (elevated tier) and
      lexicon.txt (pronunciation-family skills).

MEASUREMENT AND VALIDATION
  audio-check FILE.wav [--profile ID] [--ffmpeg PATH] [--timeout SEC] [--json]
      WAV measurement: format, duration, peak/RMS/crest dBFS, DC offset,
      clipping %, silence %, plus delivery-profile conformance.
      ffmpeg is optional: enables true LUFS + true peak (ebur128);
      otherwise those checks are SKIPPED, never guessed.
  transcript-check FILE.(srt|vtt|json) [--json]
      Cue syntax, ordering, overlaps, reading speed (CPS), WPM.
  consent-check FILE.json [--json]
      Voice consent record validation (required fields, disclosures,
      retention, revocation, dates).
  validate-package DIR [--profile ID] [--ffmpeg PATH] [--timeout SEC] [--json]
      Manifest schema, catalog resolution, approvals, consent gate,
      declared artifacts; embedded audio/transcript checks. Exit 3 on FAIL.
  qa-report DIR [--output FILE] [--profile ID] [--ffmpeg PATH] [--json]
      Markdown QA/handoff report with human sign-off block. Exit 3 on FAIL.

META
  capabilities [--json]        Machine-readable capability list
  selftest [--keep] [--json]   End-to-end test in a temp directory (no network)
  version [--json]             Toolkit/catalog versions
  help                         This help

EXIT CODES
  0 success · 1 internal error · 2 invalid input · 3 checks failed · 130 cancelled

ASSETS (resolved next to the tools, referenced from the skill root)
  asset-voice-skill-catalog.json   500-skill catalog extracted from the source PDF
  asset-delivery-profiles.json     loudness/format delivery targets
  asset-work-package-schema.json   manifest schema (draft-07 subset)
  asset-consent-record-template.json
  asset-direction-presets.json     voice-direction presets for briefs
  asset-brief-template.md          brief template used by `brief`
  asset-safety-policy.md           consent/safety/governance policy
  asset-sample-lexicon.txt         ARPABET sample lexicon fixture

No network, external services, credentials or paid APIs are used or required.
Publishing, distribution, payments and credential handling are out of scope
by design and always require separate human approval.
"""


def _cmd_help(args):
    sys.stdout.write(HELP_TEXT)
    return 0


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="tools-voice-skill-tool.py",
        description="Voice AI Skill Toolkit — 500 skills / 50 categories from the Voice AI Skill Catalog.",
        epilog="Run 'help' for the full command reference. Exit codes: 0 ok, 1 error, 2 bad input, 3 checks failed, 130 cancelled.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("categories", help="List the 50 catalog categories.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_categories)

    p = sub.add_parser("list", help="List skills, optionally filtered.")
    p.add_argument("--category", help="Category id (1-50) or unique name substring.")
    p.add_argument("--tier", choices=["standard", "review", "elevated"])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("search", help="Keyword search across all 500 skills.")
    p.add_argument("query")
    p.add_argument("--category", help="Restrict to a category id or name substring.")
    p.add_argument("--tier", choices=["standard", "review", "elevated"])
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_search)

    p = sub.add_parser("show", help="Show one skill's catalog entry.")
    p.add_argument("skill")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser("brief", help="Render a production brief for a skill.")
    _add_brief_arguments(p)
    p.add_argument("--output", help="Write the brief to FILE instead of stdout.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_brief)

    p = sub.add_parser("init-package", help="Scaffold a voice work-package folder.")
    _add_brief_arguments(p)
    p.add_argument("--dir", required=True, help="Target package directory (created if missing).")
    p.add_argument("--title", help="Manifest title (default: skill name).")
    p.add_argument("--package-id", help="Manifest package_id (default: derived from skill and date).")
    p.add_argument("--force", action="store_true", help="Overwrite tool-managed files if the directory is not empty.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_init_package)

    p = sub.add_parser("audio-check", help="Measure a WAV file and check a delivery profile.")
    p.add_argument("file", help="PCM WAV file (8/16/24/32-bit).")
    p.add_argument("--profile", help="Delivery profile id from asset-delivery-profiles.json.")
    p.add_argument("--ffmpeg", help="Explicit path to an ffmpeg executable (optional).")
    p.add_argument("--timeout", type=int, default=120, help="ffmpeg timeout in seconds (default 120).")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_audio_check)

    p = sub.add_parser("transcript-check", help="Validate an SRT/VTT/JSON transcript.")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_transcript_check)

    p = sub.add_parser("consent-check", help="Validate a voice consent record.")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_consent_check)

    p = sub.add_parser("validate-package", help="Validate a whole work-package directory.")
    p.add_argument("dir")
    p.add_argument("--profile", help="Delivery profile id (overrides manifest delivery_profile).")
    p.add_argument("--ffmpeg", help="Explicit path to an ffmpeg executable (optional).")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_validate_package)

    p = sub.add_parser("qa-report", help="Render a Markdown QA/handoff report for a package.")
    p.add_argument("dir")
    p.add_argument("--output", help="Write the report to FILE instead of stdout.")
    p.add_argument("--profile", help="Delivery profile id (overrides manifest delivery_profile).")
    p.add_argument("--ffmpeg", help="Explicit path to an ffmpeg executable (optional).")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_qa_report)

    p = sub.add_parser("selftest", help="Run the built-in end-to-end self test.")
    p.add_argument("--keep", action="store_true", help="Keep the temporary fixture directory.")
    p.add_argument("--json", action="store_true", help="(accepted for consistency; test prints text)")
    p.set_defaults(func=_cmd_selftest)

    p = sub.add_parser("capabilities", help="Print the capability list.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_capabilities)

    p = sub.add_parser("version", help="Print toolkit and catalog versions.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_version)

    p = sub.add_parser("help", help="Show the full command reference.")
    p.set_defaults(func=_cmd_help)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help(sys.stdout)
        return 0
    try:
        return args.func(args)
    except BrokenPipeError:
        # Downstream (e.g. `| head`) closed the pipe: exit quietly.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        return 0
    except ToolError as exc:
        return _common.fail(exc.message, exc.exit_code)
    except KeyboardInterrupt:
        sys.stderr.write("cancelled by user\n")
        return _common.EXIT_CANCELLED
    except Exception as exc:  # noqa: BLE001 — last-resort guard, documented exit path
        if os.environ.get("VOICE_TOOL_DEBUG"):
            raise
        return _common.fail("unexpected internal error: %s (set VOICE_TOOL_DEBUG=1 for traceback)" % exc,
                            _common.EXIT_INTERNAL)


if __name__ == "__main__":
    sys.exit(main())
