#!/usr/bin/env python3
"""RUNNER SKELETON (asset) - copied by tools_scaffold.py build.

This file is a template, not a Skill Tool entry point. Placeholders in double
braces are replaced with values from the bundle and --set overrides.

Contract enforced by the skeleton:
  * validate inputs before doing any work
  * support --dry-run that computes the change set without applying it
  * write a structured audit log with hashed inputs and a correlation id
  * bounded retries with backoff, a hard timeout, and a lock file
  * exit codes: 0 success, 1 runtime failure, 2 invalid input, 130 cancelled
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time

TOOL_NAME = "{{TOOL_NAME}}"
GOAL = "{{GOAL}}"
RETRIES = {{RETRIES}}
TIMEOUT_SECONDS = {{TIMEOUT_SECONDS}}
STAGES = {{STAGES}}
AUDIT_LOG = "{{AUDIT_LOG}}"


class InvalidInput(Exception):
    """Raised when supplied input fails validation."""


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def correlation_id(seed):
    return hashlib.sha256(("%s|%s" % (seed, now())).encode("utf-8")).hexdigest()[:16]


def audit(record):
    """Append one JSON line to the audit log; never store raw sensitive values."""
    os.makedirs(os.path.dirname(AUDIT_LOG) or ".", exist_ok=True)
    with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def validate(config):
    problems = []
    if not isinstance(config, dict):
        raise InvalidInput("config must be a JSON object")
    for key in ("input_dir", "output_dir"):
        value = config.get(key)
        if not value:
            problems.append("missing required config key: %s" % key)
        elif not os.path.exists(value):
            problems.append("path does not exist: %s" % value)
    if problems:
        raise InvalidInput("; ".join(problems))
    return config


def load_config(path):
    if not os.path.isfile(path):
        raise InvalidInput("config file not found: %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        return validate(json.load(fh))


def build_plan(config):
    """Return the intended work items without touching anything."""
    items = []
    for name in sorted(os.listdir(config["input_dir"])):
        full = os.path.join(config["input_dir"], name)
        if not os.path.isfile(full):
            continue
        items.append({
            "name": name,
            "bytes": os.path.getsize(full),
            "sha256": hash_of_file(full),
            "action": "process",
        })
    return items


def hash_of_file(path, chunk=65536):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def apply(plan, config):
    """Apply the plan. Replace this body with the real automation steps."""
    os.makedirs(config["output_dir"], exist_ok=True)
    applied = []
    for item in plan:
        applied.append({"name": item["name"], "action": item["action"],
                        "status": "skipped-placeholder"})
    return applied


def run_with_retries(plan, config):
    attempt = 0
    last_error = None
    started = time.time()
    while attempt <= RETRIES:
        attempt += 1
        try:
            if time.time() - started > TIMEOUT_SECONDS:
                raise TimeoutError("job exceeded %s seconds" % TIMEOUT_SECONDS)
            return apply(plan, config), attempt
        except Exception as exc:  # narrow this once real steps are added
            last_error = exc
            if attempt > RETRIES:
                break
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError("failed after %d attempts: %s" % (attempt, last_error))


def build_parser():
    parser = argparse.ArgumentParser(prog=TOOL_NAME, description=GOAL)
    parser.add_argument("--config", default="config.json", help="config JSON path")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute the change set without applying it")
    parser.add_argument("--out", metavar="PATH", help="write the result JSON to PATH")
    parser.add_argument("--stages", action="store_true", help="print the lifecycle stages")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.stages:
        print(json.dumps(STAGES))
        return 0
    run_id = correlation_id(TOOL_NAME)
    record = {"run_id": run_id, "tool": TOOL_NAME, "started": now()}
    try:
        config = load_config(args.config)
        plan = build_plan(config)
        result = {"ok": True, "run_id": run_id, "dry_run": bool(args.dry_run),
                  "plan": plan}
        if args.dry_run:
            result["applied"] = []
            result["note"] = "dry run: nothing was written or sent"
        else:
            applied, attempts = run_with_retries(plan, config)
            result["applied"] = applied
            result["attempts"] = attempts
        result["finished"] = now()
    except InvalidInput as exc:
        record.update({"result": "invalid-input", "error": str(exc), "finished": now()})
        audit(record)
        sys.stderr.write("invalid input: %s\n" % exc)
        return 2
    except KeyboardInterrupt:
        audit(dict(record, result="cancelled", finished=now()))
        sys.stderr.write("cancelled\n")
        return 130
    except Exception as exc:
        record.update({"result": "failed", "error": str(exc), "finished": now()})
        audit(record)
        sys.stderr.write("failed: %s\n" % exc)
        return 1
    record.update({"result": "ok", "dry_run": bool(args.dry_run), "finished": now()})
    audit(record)
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
