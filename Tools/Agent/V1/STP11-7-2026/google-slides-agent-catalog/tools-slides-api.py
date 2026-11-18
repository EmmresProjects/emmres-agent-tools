#!/usr/bin/env python3
"""Optional live Google Slides / Drive adapter for the google-slides-agent-catalog Skill Tool.

Why a separate file
-------------------
The catalog planner is deliberately inert: it never touches Google services. Real state
changes need credentials, allowlisting, approval gates, timeouts and retries, which belong
in a dedicated, auditable adapter.

Scope of this adapter
---------------------
Read operations
    get-presentation      GET  slides.googleapis.com/v1/presentations/{id}
    get-file              GET  googleapis.com/drive/v3/files/{id}
    list-permissions      GET  googleapis.com/drive/v3/files/{id}/permissions
Write operations (require an operator approval token)
    create-presentation   POST slides.googleapis.com/v1/presentations
    batch-update          POST slides.googleapis.com/v1/presentations/{id}:batchUpdate
    create-permission     POST googleapis.com/drive/v3/files/{id}/permissions

Every batchUpdate request kind must appear in the allowlist below. Destructive kinds
additionally require --confirm-destructive on top of the approval token.

Credentials are never stored in this package: the OAuth access token is read from the
environment variable named by the configuration (default GSLIDES_ACCESS_TOKEN), and the
approval token from approval.token_env (default GSLIDES_SKILL_APPROVAL_TOKEN).

No live API call was made while building this package (no credentials available); the
transport, retry, allowlist, approval and error paths are exercised against a local stub
HTTP server by tools-self-test.py. See knowledge-readme.md.

Exit codes
    0 success   2 usage/input error   6 approval denied
    7 HTTP error from the service   8 network failure or timeout
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_NAME = "tools-slides-api.py"
TOOL_VERSION = "1.0.0"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DENIED = 6
EXIT_HTTP = 7
EXIT_NETWORK = 8

MAX_RESPONSE_BYTES = 262144
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,200}$")

READ_OPERATIONS = {
    "get-presentation": {
        "service": "slides",
        "method": "GET",
        "path": "presentations/{presentation_id}",
        "requires": ["presentation_id"],
        "summary": "Read presentation structure, slides and page elements.",
    },
    "get-file": {
        "service": "drive",
        "method": "GET",
        "path": "files/{file_id}?fields=id,name,mimeType,webViewLink,owners,modifiedTime",
        "requires": ["file_id"],
        "summary": "Read Drive file metadata for a presentation.",
    },
    "list-permissions": {
        "service": "drive",
        "method": "GET",
        "path": "files/{file_id}/permissions?fields=permissions(id,type,role,emailAddress,domain)",
        "requires": ["file_id"],
        "summary": "Read the permission list for access auditing.",
    },
}

WRITE_OPERATIONS = {
    "create-presentation": {
        "service": "slides",
        "method": "POST",
        "path": "presentations",
        "requires": ["title"],
        "summary": "Create an empty presentation with a title.",
    },
    "batch-update": {
        "service": "slides",
        "method": "POST",
        "path": "presentations/{presentation_id}:batchUpdate",
        "requires": ["presentation_id", "requests"],
        "summary": "Apply an allowlisted list of Slides requests in one atomic call.",
    },
    "create-permission": {
        "service": "drive",
        "method": "POST",
        "path": "files/{file_id}/permissions",
        "requires": ["file_id", "body"],
        "summary": "Grant a Drive permission; always approval-gated.",
    },
}

ALLOWED_BATCH_REQUESTS = {
    "createSlide", "deleteObject", "duplicateObject", "groupObjects", "ungroupObjects",
    "createShape", "createImage", "createLine", "createTable", "createVideo",
    "createSheetsChart", "refreshSheetsChart", "insertText", "deleteText",
    "replaceAllText", "createParagraphBullets", "insertTableRows", "insertTableColumns",
    "deleteTableRow", "deleteTableColumn", "updateTextStyle", "updateParagraphStyle",
    "updateShapeProperties", "updateImageProperties", "updateLineProperties",
    "updateVideoProperties", "updatePageProperties", "updateSlideProperties",
    "updatePageElementTransform", "updatePageElementsZOrder", "updateTableCellProperties",
    "updateTableRowProperties", "updateTableColumnProperties", "updateSlidesPosition",
}

DESTRUCTIVE_BATCH_REQUESTS = {
    "deleteObject", "deleteText", "deleteTableRow", "deleteTableColumn", "ungroupObjects",
}

HELP_TEXT = """google-slides-api — allowlisted Google Slides / Drive adapter

USAGE
  python3 tools-slides-api.py <operation> [options]

READ OPERATIONS (no approval token)
  get-presentation   --presentation-id <id>
  get-file           --file-id <id>
  list-permissions   --file-id <id>

WRITE OPERATIONS (approval token required)
  create-presentation  --title <text>
  batch-update         --presentation-id <id> --requests-file <json>
  create-permission    --file-id <id> --body-file <json>

OPTIONS
  --out <path>                Write the JSON result to this file
  --json                      Structured JSON envelope on stdout
  --dry-run                   Build and print the request without sending it
  --approve <token>           Operator approval token (or set the env var from config)
  --confirm-destructive       Required in addition to --approve for destructive requests
  --timeout <seconds>         Request timeout (default from config)
  --retries <n>               Retry count for 429/5xx/network errors (default from config)
  --slides-base-url <url>     Override the Slides API base URL (testing/proxy)
  --drive-base-url <url>      Override the Drive API base URL (testing/proxy)
  --config <path>             Configuration file
  --list-ops                  Print the operation table and exit
  --help                      This text

SAFETY
  * Access token: env var GSLIDES_ACCESS_TOKEN (or api.token_env in the configuration).
  * Approval token: env var GSLIDES_SKILL_APPROVAL_TOKEN, or --approve.
  * Tokens are never printed, logged or written to output files.
  * Only the request kinds in the allowlist can be sent; unknown kinds are rejected.
  * Non-HTTPS base URLs are refused unless the host is localhost/127.0.0.1 (testing only).
"""


def _load_core():
    path = os.path.join(HERE, "tools-catalog-core.py")
    spec = importlib.util.spec_from_file_location("gslides_catalog_core_api", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Unable to load shared core module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gslides_catalog_core_api"] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()


def _redact(headers):
    safe = {}
    for key, value in headers.items():
        if key.lower() in ("authorization", "x-goog-user-project"):
            safe[key] = "<redacted>"
        else:
            safe[key] = value
    return safe


def _operation_table():
    rows = []
    for name, spec in list(READ_OPERATIONS.items()) + list(WRITE_OPERATIONS.items()):
        rows.append(
            {
                "operation": name,
                "kind": "read" if name in READ_OPERATIONS else "write",
                "service": spec["service"],
                "method": spec["method"],
                "summary": spec["summary"],
                "requires": spec["requires"],
                "approval_required": name in WRITE_OPERATIONS,
            }
        )
    return rows


def validate_batch_requests(payload, limit):
    """Validate the batchUpdate request list against the allowlist."""
    if not isinstance(payload, list):
        raise core.SkillToolError(
            "requests-invalid",
            "The requests file must contain a JSON array of Slides request objects.",
            hint='Example: [{"replaceAllText": {"containsText": {"text": "Old"}, "replaceText": "New"}}]',
        )
    if not payload:
        raise core.SkillToolError("requests-invalid", "The requests array is empty.")
    if len(payload) > limit:
        raise core.SkillToolError(
            "requests-too-many",
            f"{len(payload)} requests supplied; the limit is {limit}.",
            hint="Raise limits.max_batch_requests in the configuration or split the call.",
        )
    kinds = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict) or len(entry) != 1:
            raise core.SkillToolError(
                "requests-invalid",
                f"Request {index} must be an object with exactly one request kind.",
            )
        kind = next(iter(entry))
        if kind not in ALLOWED_BATCH_REQUESTS:
            import difflib

            close = difflib.get_close_matches(kind, list(ALLOWED_BATCH_REQUESTS), n=2, cutoff=0.5)
            raise core.SkillToolError(
                "requests-not-allowed",
                f"Request kind {kind!r} is not in the allowlist.",
                hint=("Did you mean: " + ", ".join(close)) if close else
                     "Only native Google Slides batchUpdate request kinds are accepted.",
            )
        kinds.append(kind)
    return kinds


def _check_base_url(url, what):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise core.SkillToolError("bad-base-url", f"{what} must be an http(s) URL, got {url!r}.")
    if parsed.scheme != "https":
        host = (parsed.hostname or "").lower()
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise core.SkillToolError(
                "insecure-base-url",
                f"{what} uses plain http for host {host!r}; refusing to send an OAuth token in clear text.",
                hint="Use https, or point at localhost for testing.",
            )
    return url.rstrip("/")


class Adapter:
    def __init__(self, args):
        self.args = args
        self.config = core.load_config(getattr(args, "config", None))
        self.limits = core.limits(self.config)
        api_cfg = self.config.get("api") or {}
        self.slides_base = _check_base_url(
            args.slides_base_url or api_cfg.get("slides_base_url", "https://slides.googleapis.com/v1"),
            "Slides base URL",
        )
        self.drive_base = _check_base_url(
            args.drive_base_url or api_cfg.get("drive_base_url", "https://www.googleapis.com/drive/v3"),
            "Drive base URL",
        )
        self.token_env = api_cfg.get("token_env", "GSLIDES_ACCESS_TOKEN")
        self.user_agent = api_cfg.get("user_agent", "google-slides-agent-catalog/1.0")
        self.approval_env = (self.config.get("approval") or {}).get(
            "token_env", "GSLIDES_SKILL_APPROVAL_TOKEN"
        )
        self.timeout = float(
            args.timeout if args.timeout is not None else self.limits["api_timeout_seconds"]
        )
        self.timeout = max(1.0, min(self.timeout, 120.0))
        self.retries = int(
            args.retries if args.retries is not None else self.limits["api_max_retries"]
        )
        self.retries = max(0, min(self.retries, 8))
        self.backoff = self.limits["api_backoff_seconds"]

    # -- request building -----------------------------------------------------------------
    def build(self, op):
        if op in READ_OPERATIONS:
            spec = READ_OPERATIONS[op]
        elif op in WRITE_OPERATIONS:
            spec = WRITE_OPERATIONS[op]
        else:
            raise core.SkillToolError(
                "unknown-operation",
                f"Unknown operation {op!r}.",
                hint="Run: python3 tools-slides-api.py --list-ops",
            )
        args = self.args
        presentation_id = getattr(args, "presentation_id", None)
        file_id = getattr(args, "file_id", None)
        for required in spec["requires"]:
            if required in ("presentation_id", "file_id"):
                value = presentation_id if required == "presentation_id" else file_id
                if not value:
                    raise core.SkillToolError(
                        "missing-argument",
                        f"{op} needs --{required.replace('_', '-')}.",
                    )
                if not ID_PATTERN.match(value):
                    raise core.SkillToolError(
                        "invalid-id",
                        f"{required} {value!r} is not a valid Google file id "
                        "(3-200 characters of letters, digits, '-' or '_').",
                    )
            if required == "title" and not (getattr(args, "title", None) or "").strip():
                raise core.SkillToolError("missing-argument", "create-presentation needs --title.")
            if required == "requests" and not getattr(args, "requests_file", None):
                raise core.SkillToolError("missing-argument", "batch-update needs --requests-file.")
            if required == "body" and not getattr(args, "body_file", None):
                raise core.SkillToolError("missing-argument", "create-permission needs --body-file.")

        base = self.slides_base if spec["service"] == "slides" else self.drive_base
        path = spec["path"].format(presentation_id=presentation_id, file_id=file_id)
        url = f"{base}/{path}"
        body = None
        kinds = []

        if op == "create-presentation":
            body = {"title": (getattr(args, "title", None) or "").strip()[:300]}
        elif op == "batch-update":
            requests_path = core.resolve_path(args.requests_file)
            payload = core.load_json(requests_path, "Requests file")
            kinds = validate_batch_requests(payload, self.limits["max_batch_requests"])
            body = {"requests": payload}
        elif op == "create-permission":
            body_path = core.resolve_path(args.body_file)
            payload = core.load_json(body_path, "Permission body")
            if not isinstance(payload, dict):
                raise core.SkillToolError("body-invalid", "Permission body must be a JSON object.")
            allowed_keys = {"type", "role", "emailAddress", "domain", "allowFileDiscovery",
                            "view", "transferOwnership"}
            unknown = [key for key in payload if key not in allowed_keys]
            if unknown:
                raise core.SkillToolError(
                    "body-invalid",
                    "Unsupported permission field(s): " + ", ".join(sorted(unknown)),
                    hint="Allowed fields: " + ", ".join(sorted(allowed_keys)),
                )
            if payload.get("type") == "anyone":
                raise core.SkillToolError(
                    "public-sharing-blocked",
                    "Anonymous 'anyone' permissions are blocked by this adapter.",
                    hint="Use Publish to web through the governed release flow instead.",
                )
            body = payload

        # A dry run must be inspectable without credentials; live calls always require a token.
        token = "dry-run-token-not-sent" if getattr(self.args, "dry_run", False) else self.access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        return {
            "operation": op,
            "kind": "read" if op in READ_OPERATIONS else "write",
            "method": spec["method"],
            "url": url,
            "headers": headers,
            "body": body,
            "batch_kinds": kinds,
            "approval_required": op in WRITE_OPERATIONS,
        }

    # -- credentials and approval ---------------------------------------------------------
    def access_token(self):
        token = os.environ.get(self.token_env, "").strip()
        if not token:
            raise core.SkillToolError(
                "missing-credentials",
                f"No OAuth access token found in environment variable {self.token_env}.",
                hint=(
                    "Obtain a short-lived Slides/Drive token from your own authorised flow and export "
                    f"{self.token_env}. Use --dry-run to inspect the request without a token."
                ),
            )
        return token

    def check_approval(self, request):
        if not request["approval_required"]:
            return {"required": False, "state": "not-required"}
        supplied = (getattr(self.args, "approve", None) or os.environ.get(self.approval_env, "")).strip()
        expected = os.environ.get(self.approval_env, "").strip()
        if not supplied:
            raise core.SkillToolError(
                "approval-required",
                f"{request['operation']} changes remote state and needs an operator approval token.",
                hint=(
                    "Have the operator export " + self.approval_env +
                    " (or pass --approve) after reviewing the plan and approval record."
                ),
            )
        if expected and supplied != expected:
            raise core.SkillToolError(
                "approval-mismatch",
                "The supplied approval token does not match the operator-issued token.",
            )
        destructive = sorted(set(request["batch_kinds"]) & DESTRUCTIVE_BATCH_REQUESTS)
        if destructive and not getattr(self.args, "confirm_destructive", False):
            raise core.SkillToolError(
                "destructive-confirmation-required",
                "Destructive request kind(s) present: " + ", ".join(destructive),
                hint="Re-run with --confirm-destructive in addition to the approval token.",
            )
        return {
            "required": True,
            "state": "approved",
            "attestation": f"operator token supplied via {self.approval_env}",
        }

    # -- transport ------------------------------------------------------------------------
    def send(self, request):
        data = None
        if request["body"] is not None:
            data = json.dumps(request["body"]).encode("utf-8")
        attempt = 0
        last_error = None
        while attempt <= self.retries:
            attempt += 1
            http_request = urllib.request.Request(
                request["url"], data=data, headers=request["headers"], method=request["method"]
            )
            try:
                with urllib.request.urlopen(http_request, timeout=self.timeout) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    truncated = len(raw) > MAX_RESPONSE_BYTES
                    body = raw[:MAX_RESPONSE_BYTES].decode("utf-8", errors="replace")
                    return {
                        "http_status": response.status,
                        "body": _maybe_json(body),
                        "truncated": truncated,
                        "attempts": attempt,
                        "retried": attempt > 1,
                    }
            except urllib.error.HTTPError as error:
                body = ""
                try:
                    body = error.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
                except Exception:
                    body = ""
                retryable = error.code in (429, 500, 502, 503, 504)
                last_error = {
                    "code": error.code,
                    "body": _maybe_json(body),
                    "retryable": retryable,
                    "attempts": attempt,
                }
                if not retryable or attempt > self.retries:
                    return {"error": last_error, **last_error}
                time.sleep(min(self.backoff * (2 ** (attempt - 1)), 8.0))
            except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
                reason = getattr(error, "reason", error)
                last_error = {
                    "code": "network",
                    "message": f"{type(error).__name__}: {reason}",
                    "retryable": True,
                    "attempts": attempt,
                }
                if attempt > self.retries:
                    return {"error": last_error, **last_error}
                time.sleep(min(self.backoff * (2 ** (attempt - 1)), 8.0))
        return {"error": last_error or {"code": "unknown"}, **(last_error or {})}


def _maybe_json(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text



def run(args):
    if getattr(args, "list_ops", False):
        table = _operation_table()
        lines = [f"{TOOL_NAME} {TOOL_VERSION} — allowlisted operations", ""]
        for row in table:
            gate = "approval required" if row["approval_required"] else "read-only"
            lines.append(f"  {row['operation']:<20} {row['kind']:<5} {row['method']:<4} "
                         f"{row['service']:<6} [{gate}]  {row['summary']}")
        lines.append("")
        lines.append(f"Allowed batchUpdate request kinds: {len(ALLOWED_BATCH_REQUESTS)} "
                     "(destructive kinds: " + ", ".join(sorted(DESTRUCTIVE_BATCH_REQUESTS)) + ")")
        return {"operations": table, "allowed_batch_requests": sorted(ALLOWED_BATCH_REQUESTS),
                "destructive_batch_requests": sorted(DESTRUCTIVE_BATCH_REQUESTS)}, lines, EXIT_OK

    op = getattr(args, "operation", None)
    if not op:
        return {"help": HELP_TEXT}, HELP_TEXT.rstrip("\n").split("\n"), EXIT_OK

    adapter = Adapter(args)
    request = adapter.build(op)
    if request["approval_required"]:
        approval = adapter.check_approval(request)
    else:
        approval = {"required": False, "state": "not-required"}

    public_request = {
        "operation": request["operation"],
        "kind": request["kind"],
        "method": request["method"],
        "url": request["url"],
        "headers": _redact(request["headers"]),
        "body": request["body"],
        "approval": approval,
    }

    if getattr(args, "dry_run", False):
        lines = [
            f"DRY-RUN {op} ({request['kind']}) — no request was sent",
            f"  {request['method']} {request['url']}",
            f"  headers: {json.dumps(_redact(request['headers']))}",
            f"  approval: {approval['state']}",
        ]
        if request["body"] is not None:
            lines.append("  body: " + json.dumps(request["body"])[:800])
        payload = {"dry_run": True, "request": public_request, "exit_code": EXIT_OK}
        return _finish(payload, lines, EXIT_OK, args)

    result = adapter.send(request)
    if "error" in result:
        code = EXIT_NETWORK if result["error"]["code"] == "network" else EXIT_HTTP
        message = result["error"].get("message") or f"HTTP {result['error'].get('code')}"
        lines = [
            f"FAILED {op} after {result['error'].get('attempts', 0)} attempt(s): {message}",
            f"  retryable: {result['error'].get('retryable')}",
            "  recovery: re-check the file id, credentials and sharing; the tool made no partial change",
            "            beyond what the service itself reports in the error body.",
        ]
        payload = {"operation": op, "request": public_request, "error": result["error"]}
        return _finish(payload, lines, code, args)

    lines = [
        f"OK {op} ({request['kind']}) — HTTP {result['http_status']} after {result['attempts']} attempt(s)"
        + (" (retried)" if result["retried"] else ""),
        f"  approval: {approval['state']}",
    ]
    if isinstance(result["body"], dict):
        keys = ", ".join(sorted(result["body"].keys())[:8])
        lines.append(f"  response keys: {keys or '(empty)'}")
        presentation_id = result["body"].get("presentationId") or result["body"].get("id")
        if presentation_id:
            lines.append(f"  id: {presentation_id}")
    payload = {
        "operation": op,
        "request": public_request,
        "http_status": result["http_status"],
        "attempts": result["attempts"],
        "response": result["body"],
        "truncated": result["truncated"],
    }
    return _finish(payload, lines, EXIT_OK, args)


def _finish(payload, lines, exit_code, args):
    payload = dict(payload)
    payload["ok"] = exit_code == EXIT_OK
    payload["exit_code"] = exit_code
    out = getattr(args, "out", None)
    if out:
        path = core.resolve_path(out)
        core.save_json(path, {key: value for key, value in payload.items() if key != "lines"})
        lines.append(f"Wrote result: {path}")
        payload["out_path"] = path
    payload["lines"] = lines
    return payload, lines, exit_code


def build_parser():
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Allowlisted, approval-gated Google Slides / Drive adapter.",
    )
    parser.add_argument("operation", nargs="?", help="Operation name (see --list-ops)")
    parser.add_argument("--presentation-id")
    parser.add_argument("--file-id")
    parser.add_argument("--title")
    parser.add_argument("--requests-file")
    parser.add_argument("--body-file")
    parser.add_argument("--out")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--approve")
    parser.add_argument("--confirm-destructive", action="store_true")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--retries", type=int)
    parser.add_argument("--slides-base-url")
    parser.add_argument("--drive-base-url")
    parser.add_argument("--config")
    parser.add_argument("--list-ops", action="store_true", dest="list_ops")
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {TOOL_VERSION}")
    return parser


def _hoist_globals(argv):
    """Allow --json/--config anywhere on the command line."""
    hoisted = []
    rest = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--json":
            hoisted.append(token)
            index += 1
        elif token == "--config" and index + 1 < len(argv):
            hoisted.extend([token, argv[index + 1]])
            index += 2
        elif token.startswith("--config="):
            hoisted.append(token)
            index += 1
        else:
            rest.append(token)
            index += 1
    return hoisted + rest


def main(argv=None):
    argv = _hoist_globals(list(sys.argv[1:] if argv is None else argv))
    if not argv:
        sys.stdout.write(HELP_TEXT)
        return EXIT_OK
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        payload, lines, exit_code = run(args)
    except core.SkillToolError as error:
        payload = error.as_dict()
        payload["exit_code"] = EXIT_USAGE
        if getattr(args, "json", False):
            json.dump(payload, sys.stderr, indent=2)
            sys.stderr.write("\n")
        else:
            sys.stderr.write(f"ERROR [{error.code}] {error.message}\n")
            if error.hint:
                sys.stderr.write(f"  hint: {error.hint}\n")
        return EXIT_USAGE
    except KeyboardInterrupt:
        sys.stderr.write("Interrupted: the HTTP request was abandoned; no retry was attempted.\n")
        return 130
    if getattr(args, "json", False):
        json.dump({key: value for key, value in payload.items() if key != "lines"},
                  sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        for line in lines:
            sys.stdout.write(str(line) + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
