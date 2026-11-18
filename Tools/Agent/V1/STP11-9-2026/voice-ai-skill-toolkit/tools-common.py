#!/usr/bin/env python3
"""Shared runtime helpers for the voice-ai-skill-toolkit Skill Tool.

This module is imported by the other tools-* files via importlib (filenames
contain hyphens, so normal imports are impossible). It centralises:

  * TOOL_ROOT resolution (folder that contains this file)
  * asset loading (always resolved relative to this folder, never CWD)
  * sibling module loading
  * structured JSON / text output
  * fatal error handling with documented exit codes

Exit codes used across the toolkit:
  0   success
  1   unexpected internal error
  2   invalid input / usage error (bad arguments, unreadable files, parse errors)
  3   checks ran but the verdict is FAIL (e.g. validate-package, qa-report)
  130 cancelled by the user (Ctrl-C)
"""

import importlib.util
import json
import os
import sys

sys.dont_write_bytecode = True

TOOL_ROOT = os.path.dirname(os.path.abspath(__file__))

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_CHECK_FAILED = 3
EXIT_CANCELLED = 130

MAX_JSON_BYTES = 16 * 1024 * 1024        # 16 MiB cap for JSON inputs
MAX_TEXT_BYTES = 32 * 1024 * 1024        # 32 MiB cap for transcripts / text
MAX_AUDIO_BYTES = 512 * 1024 * 1024      # 512 MiB cap for audio analysis


class ToolError(Exception):
    """Fatal, user-facing error. Carries a process exit code."""

    def __init__(self, message, exit_code=EXIT_USAGE):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def asset_path(filename):
    """Absolute path of a packaged asset (resolved from this file's folder)."""
    path = os.path.join(TOOL_ROOT, filename)
    if not os.path.isfile(path):
        raise ToolError(
            "Packaged asset missing: %s (expected next to tools-common.py)" % filename,
            EXIT_INTERNAL,
        )
    return path


def load_asset_json(filename):
    path = asset_path(filename)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ToolError("Asset %s is not valid JSON: %s" % (filename, exc), EXIT_INTERNAL)


def load_asset_text(filename):
    path = asset_path(filename)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def load_sibling(module_name, filename):
    """Load tools-<name>.py next to this file as a module (cached by name)."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = os.path.join(TOOL_ROOT, filename)
    if not os.path.isfile(path):
        raise ToolError(
            "Required tool module missing: %s (expected next to tools-common.py)" % filename,
            EXIT_INTERNAL,
        )
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def check_readable_file(path, max_bytes, label):
    if not path:
        raise ToolError("%s path is empty." % label, EXIT_USAGE)
    if not os.path.exists(path):
        raise ToolError("%s not found: %s" % (label, path), EXIT_USAGE)
    if not os.path.isfile(path):
        raise ToolError("%s is not a regular file: %s" % (label, path), EXIT_USAGE)
    size = os.path.getsize(path)
    if size == 0:
        raise ToolError("%s is empty: %s" % (label, path), EXIT_USAGE)
    if size > max_bytes:
        raise ToolError(
            "%s exceeds the %d MiB limit (%s): %s" % (label, max_bytes // (1024 * 1024), size, path),
            EXIT_USAGE,
        )
    return size


def read_json_file(path, label="JSON file", max_bytes=MAX_JSON_BYTES):
    check_readable_file(path, max_bytes, label)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except UnicodeDecodeError as exc:
        raise ToolError("%s is not UTF-8 text: %s (%s)" % (label, path, exc), EXIT_USAGE)
    except json.JSONDecodeError as exc:
        raise ToolError("%s is not valid JSON: %s (%s)" % (label, path, exc), EXIT_USAGE)


def emit_json(payload):
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def emit(payload, as_json):
    if as_json:
        emit_json(payload)
    else:
        text = payload.get("_text")
        if text is None:
            emit_json(payload)
        else:
            sys.stdout.write(text if text.endswith("\n") else text + "\n")


def fail(message, exit_code=EXIT_USAGE):
    sys.stderr.write("error: %s\n" % message)
    return exit_code
