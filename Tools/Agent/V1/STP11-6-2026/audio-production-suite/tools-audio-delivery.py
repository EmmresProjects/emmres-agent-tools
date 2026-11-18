#!/usr/bin/env python3
"""tools-audio-delivery.py - delivery manifests, checksums, schema validation, verification.

Builds a signed manifest for a delivery folder: every file with its SHA-256, probe
data for WAV files, preset compliance checks, and an approval record. Also validates
a manifest against the packaged schema and re-verifies checksums later.

Run directly for its own help: python3 tools-audio-delivery.py --help
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import sys

sys.dont_write_bytecode = True  # keep the packaged folder free of __pycache__

COMMON_FILENAME = "tools-audio-common.py"
IO_FILENAME = "tools-audio-io.py"
QA_FILENAME = "tools-audio-qa.py"
SCHEMA_ASSET = "asset-delivery-manifest.schema.json"
PRESETS_ASSET = "asset-delivery-presets.json"
MANIFEST_VERSION = "1.0.0"
AUDIO_EXTENSIONS = (".wav", ".bwf", ".wave")
ROLE_HINTS = (
    ("master", ("master", "final", "print")),
    ("stem", ("stem", "stems")),
    ("alternate", ("instrumental", "acapella", "clean", "explicit", "radio", "karaoke", "alt")),
    ("reference", ("ref", "reference", "rough")),
    ("transcript", ("transcript", "chapter", "cue", "lyrics", "srt", "vtt")),
    ("document", ("manifest", "notes", "readme", "sheet", "report")),
)


def _load():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import importlib.util

    def load(name, filename):
        if name in sys.modules:
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(name, os.path.join(here, filename))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    common = load("aps_audio_common", COMMON_FILENAME)
    aio = load("aps_audio_io", IO_FILENAME)
    qa = load("aps_audio_qa", QA_FILENAME)
    return common, aio, qa


common, aio, qa = _load()


def sha256_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _role_for(relative_path):
    lowered = relative_path.lower()
    for role, hints in ROLE_HINTS:
        for hint in hints:
            if hint in lowered:
                return role
    return "asset"


def _iter_files(root):
    collected = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if name.startswith(".aps-") or name.endswith(".aps-tmp"):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            collected.append(full)
    return collected


def build_manifest(root, preset="streaming-music", notes="", analyze_audio=True,
                   max_files=None, sign_off=None, roles=None, exclude=None):
    root = common.resolve_path(root, must_exist=True, kind="dir")
    presets = common.load_asset(PRESETS_ASSET)
    if preset not in presets["presets"]:
        raise common.ToolError("unknown_preset", "Unknown preset %r" % preset,
                               {"available_presets": sorted(presets["presets"].keys())})
    preset_def = presets["presets"][preset]
    limit = max_files or common.LIMITS["manifest_max_files"]
    excluded = {os.path.abspath(item) for item in (exclude or [])}
    files = [item for item in _iter_files(root) if os.path.abspath(item) not in excluded]
    if len(files) > limit:
        raise common.ToolError("limit_exceeded",
                               "Delivery folder has %s files, above the limit of %s" % (len(files), limit),
                               hint="Raise --max-files deliberately or split the delivery.")
    entries = []
    checks = []
    audio_count = 0
    total_bytes = 0
    for full in files:
        relative = os.path.relpath(full, root).replace(os.sep, "/")
        size = os.path.getsize(full)
        total_bytes += size
        entry = {
            "path": relative,
            "role": (roles or {}).get(relative) or _role_for(relative),
            "bytes": size,
            "sha256": sha256_file(full),
        }
        if relative.lower().endswith(AUDIO_EXTENSIONS):
            audio_count += 1
            try:
                audio, meta = aio.read_wav(full, max_seconds=common.LIMITS["hard_max_seconds"])
                entry["audio"] = {
                    "sample_rate": meta["sample_rate"],
                    "channels": meta["channels"],
                    "bit_depth": meta["bit_depth"],
                    "duration_seconds": meta["duration_seconds"],
                    "structurally_valid": meta["structurally_valid"],
                    "true_peak_dbtp": None,
                    "integrated_lufs": None,
                }
                if analyze_audio:
                    report = qa.analyze(full, preset=preset)
                    entry["audio"]["true_peak_dbtp"] = report["metrics"]["level"]["true_peak_dbtp"]
                    entry["audio"]["integrated_lufs"] = report["metrics"]["loudness"]["integrated_lufs"]
                    entry["audio"]["verdict"] = report["verdict"]
                    entry["audio"]["failed_gates"] = [g["gate"] for g in report["gates"]
                                                      if g["status"] == "fail"]
            except common.ToolError as exc:
                entry["audio"] = {"structurally_valid": False, "error": exc.message}
                checks.append({"check": "audio.readable", "status": "fail",
                               "detail": "%s: %s" % (relative, exc.message)})
        entries.append(entry)

    if not entries:
        raise common.ToolError("empty_delivery", "The delivery folder contains no files",
                               {"root": root})
    if audio_count == 0:
        checks.insert(0, {"check": "audio.present", "status": "fail",
                          "detail": "No WAV files found; this is an audio delivery preset"})
    else:
        checks.insert(0, {"check": "audio.present", "status": "pass",
                          "detail": "%s WAV file(s) found" % audio_count})

    invalid = [entry["path"] for entry in entries
               if entry.get("audio") and entry["audio"].get("structurally_valid") is False]
    checks.append({"check": "audio.structural", "status": "fail" if invalid else "pass",
                   "detail": ("Invalid: " + ", ".join(invalid)) if invalid else "All WAV files parse cleanly"})

    failing = [entry["path"] for entry in entries
               if entry.get("audio", {}).get("verdict") == "fail"]
    checks.append({"check": "audio.preset_gates",
                   "status": ("skipped" if not analyze_audio else ("fail" if failing else "pass")),
                   "detail": ("Below preset gates: " + ", ".join(failing)) if failing
                             else ("Analysis disabled" if not analyze_audio else "All files meet the preset gates")})

    expected = preset_def["format"]
    mismatched = []
    for entry in entries:
        audio = entry.get("audio") or {}
        if not audio or audio.get("sample_rate") is None:
            continue
        if audio["sample_rate"] != expected["sample_rate"]:
            mismatched.append("%s: %s Hz vs %s Hz" % (entry["path"], audio["sample_rate"],
                                                      expected["sample_rate"]))
        if expected.get("bit_depth") and audio["bit_depth"] < expected["bit_depth"]:
            mismatched.append("%s: %s bits vs %s bits" % (entry["path"], audio["bit_depth"],
                                                          expected["bit_depth"]))
    checks.append({"check": "delivery.format_match", "status": "warn" if mismatched else "pass",
                   "detail": "; ".join(mismatched) if mismatched else
                             "Sample rate and bit depth match the %s preset" % preset})

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "created_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "generated_by": "audio-production-suite/%s" % common.VERSION,
        "root": os.path.basename(root.rstrip(os.sep)) or root,
        "preset": preset,
        "notes": notes,
        "files": entries,
        "totals": {"file_count": len(entries), "total_bytes": total_bytes,
                   "audio_files": audio_count},
        "checks": checks,
    }
    if sign_off:
        manifest["sign_off"] = {
            "approved_by": sign_off["approved_by"],
            "approval_id": sign_off["approval_id"],
            "rights_status": sign_off.get("rights_status", "unknown"),
            "approved_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
        }
    manifest["_root_path"] = root
    return manifest


# --- schema validation (JSON Schema subset) --------------------------------
def _type_ok(value, expected):
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_instance(instance, schema, path="$"):
    """Validate against the JSON Schema keywords this package actually uses."""
    errors = []
    if schema is True or schema == {}:
        return errors
    expected = schema.get("type")
    if expected:
        options = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(instance, option) for option in options):
            errors.append({"path": path, "error": "expected type %s" % "/".join(options),
                           "value_type": type(instance).__name__})
            return errors
    if "enum" in schema and instance not in schema["enum"]:
        errors.append({"path": path, "error": "value not in enum %s" % schema["enum"]})
    if isinstance(instance, str):
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, instance):
            errors.append({"path": path, "error": "value does not match pattern %s" % pattern})
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append({"path": path, "error": "value below minimum %s" % schema["minimum"]})
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append({"path": path, "error": "value above maximum %s" % schema["maximum"]})
    if isinstance(instance, dict):
        for required in schema.get("required", []):
            if required not in instance:
                errors.append({"path": path, "error": "missing required property %r" % required})
        properties = schema.get("properties", {})
        for key, subschema in properties.items():
            if key in instance:
                errors.extend(validate_instance(instance[key], subschema, "%s.%s" % (path, key)))
    if isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(instance):
                errors.extend(validate_instance(item, items, "%s[%d]" % (path, index)))
    return errors


def validate_manifest(manifest_path, schema_path=None):
    manifest = common.read_json(manifest_path, "delivery manifest")
    schema = (common.read_json(schema_path, "manifest schema") if schema_path
              else common.load_asset(SCHEMA_ASSET))
    errors = validate_instance(manifest, schema)
    present = {entry["path"] for entry in manifest.get("files", [])}
    for entry in manifest.get("files", []):
        entry_path = entry.get("path", "")
        if entry_path.startswith("/") or ".." in entry_path.split("/"):
            errors.append({"path": "$.files", "error": "relative path escapes the delivery root: %r" % entry_path})
    return {"manifest": common.resolve_path(manifest_path), "valid": not errors,
            "error_count": len(errors), "errors": errors[:50],
            "file_count": len(present)}


def verify_manifest(manifest_path, root=None):
    """Recompute checksums and confirm the package is byte-identical to the manifest."""
    manifest = common.read_json(manifest_path, "delivery manifest")
    base = root or os.path.join(os.path.dirname(common.resolve_path(manifest_path)),
                                manifest.get("root") or ".")
    base = os.path.abspath(base)
    if not os.path.isdir(base):
        alt = os.path.join(os.path.dirname(os.path.abspath(manifest_path)))
        if os.path.isdir(alt):
            base = alt
        else:
            raise common.ToolError("not_found", "Delivery root not found: %s" % base,
                                   {"manifest_root": manifest.get("root")},
                                   hint="Pass --root with the folder that holds the delivered files.")
    results = []
    missing = []
    mismatched = []
    for entry in manifest.get("files", []):
        full = os.path.join(base, entry["path"])
        if not os.path.isfile(full):
            missing.append(entry["path"])
            results.append({"path": entry["path"], "status": "missing"})
            continue
        digest = sha256_file(full)
        status = "pass" if digest == entry["sha256"] else "mismatch"
        if status == "mismatch":
            mismatched.append(entry["path"])
        results.append({"path": entry["path"], "status": status,
                        "expected": entry["sha256"], "actual": digest})
    return {
        "manifest": common.resolve_path(manifest_path),
        "root": base,
        "verified": not missing and not mismatched,
        "missing": missing,
        "mismatched": mismatched,
        "files": results,
        "checked": len(results),
    }


def manifest_markdown(manifest):
    lines = ["# Delivery manifest", ""]
    lines.append("- Created: %s" % manifest["created_at"])
    lines.append("- Preset: `%s`" % manifest["preset"])
    lines.append("- Files: %s (%s bytes, %s audio)"
                 % (manifest["totals"]["file_count"], manifest["totals"]["total_bytes"],
                    manifest["totals"]["audio_files"]))
    lines.append("")
    lines.append("| Path | Role | Bytes | Sample rate | Bits | LUFS | dBTP | SHA-256 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for entry in manifest["files"]:
        audio = entry.get("audio") or {}
        lines.append("| `%s` | %s | %s | %s | %s | %s | %s | `%s` |"
                     % (entry["path"], entry["role"], entry["bytes"],
                        audio.get("sample_rate", "-"), audio.get("bit_depth", "-"),
                        audio.get("integrated_lufs", "-"), audio.get("true_peak_dbtp", "-"),
                        entry["sha256"][:16] + "..."))
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    lines.append("| Check | Status | Detail |")
    lines.append("| --- | --- | --- |")
    for check in manifest["checks"]:
        lines.append("| %s | %s | %s |" % (check["check"], check["status"], check.get("detail", "")))
    if manifest.get("sign_off"):
        sign = manifest["sign_off"]
        lines.append("")
        lines.append("Signed off by %s (approval `%s`, rights `%s`) at %s"
                     % (sign["approved_by"], sign["approval_id"], sign.get("rights_status", "unknown"),
                        sign.get("approved_at", "")))
    return "\n".join(lines) + "\n"


def main(argv=None):
    print("""tools-audio-delivery.py - delivery manifests and verification (library + help).

Functions: build_manifest(), validate_manifest(errors against the packaged schema),
verify_manifest(checksum re-verification), sha256_file(), manifest_markdown()

Use the suite entry point:
  python3 tools-audio-suite.py manifest --input delivery --preset streaming-music --out delivery/manifest.json
  python3 tools-audio-suite.py verify-manifest --manifest delivery/manifest.json
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
