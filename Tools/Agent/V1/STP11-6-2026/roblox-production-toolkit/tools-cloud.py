"""Cloud family: gated Open Cloud adapter, webhook verification, OAuth PKCE URL generation,
auth readiness checks, and release evidence evaluation.

Loaded as rbx_cloud by tools-roblox.py.

Safety model (all enforced in code, not in documentation alone):
- Default is dry-run: a request plan is built from an operator-supplied OpenAPI document and
  validated, but no network connection is opened.
- Execution requires all of: options.execute true, --approve, network.enabled true in the
  configuration, the operation listed in network.allowed_operations, the host listed in
  network.allowed_hosts over HTTPS (or HTTP when allow_http is explicitly true), and the API key
  present in the environment variable named by network.api_key_env. The key value is never read
  into output, logs, or artifacts; only its presence is checked.
- Redirects are refused. Retries are bounded. POST is retried only after HTTP 429.
- Publishing is Saved-only (versionType "Saved"). Live publication is not a supported mode here.
- No token exchange, no secret storage, no arbitrary endpoints. The request path comes only from the
  operator's spec and is validated against a strict pattern.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets as _secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import rbx_core as core
from rbx_core import chk

FAMILY = "cloud"
SEGMENT_RE = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
SCOPE_RE = re.compile(r"^[a-z][a-z0-9_-]*(?::[a-z0-9_*-]+)+$")
ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
RETRY_STATUS = {429, 500, 502, 503, 504}


def out(result, findings, artifacts=None, warnings=None):
    return {"result": result, "findings": findings, "artifacts": artifacts or {}, "warnings": warnings or []}


def _net(ctx):
    return ctx.config["network"]


# ---------------------------------------------------------------- OpenAPI handling

def load_spec(path_text, ctx):
    if not isinstance(path_text, str) or not path_text.endswith(".json"):
        core.fail("INPUT_INVALID", "spec_path must be a path to a JSON OpenAPI document.")
    p = Path(path_text).expanduser()
    if not p.is_absolute() and not p.is_file() and path_text.startswith("roblox-production-toolkit/"):
        p = Path(__file__).resolve().parents[1] / path_text
    if not p.is_file() or p.is_symlink():
        core.fail("RESOURCE_MISSING", "spec_path does not point to a readable file.")
    if p.stat().st_size > ctx.config["limits"]["input_bytes"]:
        core.fail("INPUT_LIMIT", "OpenAPI document exceeds the input size limit.")
    spec = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or not str(spec.get("openapi", "")).startswith("3."):
        core.fail("SCHEMA_INVALID", "Only OpenAPI 3.x documents are supported.")
    return spec


def _resolve(node, spec, depth=0):
    """Inline internal #/components references; external references are refused."""
    if depth > 30:
        core.fail("SCHEMA_INVALID", "Schema reference depth exceeded.")
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/"):
                core.fail("SCHEMA_INVALID", "Only internal #/ references are supported.")
            target = spec
            for part in ref[2:].split("/"):
                if not isinstance(target, dict) or part not in target:
                    core.fail("SCHEMA_INVALID", "Unresolvable schema reference.", ref=ref)
                target = target[part]
            return _resolve(target, spec, depth + 1)
        return {k: _resolve(v, spec, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, spec, depth + 1) for v in node]
    return node


def find_operation(spec, operation_id, role_rx):
    matches = []
    for path, item in spec.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE") or not isinstance(op, dict):
                continue
            if op.get("operationId") == operation_id:
                matches.append((method.upper(), path, op))
    if len(matches) != 1:
        core.fail("NOT_FOUND", "operationId must match exactly one operation in the supplied spec.", operation_id=operation_id, matches=len(matches))
    method, path, op = matches[0]
    if role_rx and not role_rx.search(operation_id):
        core.fail("POLICY_DENIED", "operationId does not belong to this capability's allowed role.", operation_id=operation_id)
    return method, path, op


def server_base(spec):
    servers = spec.get("servers") or []
    if not isinstance(servers, list) or not servers or not isinstance(servers[0], dict) or not isinstance(servers[0].get("url"), str):
        core.fail("SCHEMA_INVALID", "The spec must declare servers[0].url.")
    url = servers[0]["url"]
    if "{" in url:
        core.fail("SCHEMA_INVALID", "Templated server URLs are not supported.")
    return url.rstrip("/")


def build_request(spec, operation_id, path_params, query, body, role_rx, ctx):
    method, path, op = find_operation(spec, operation_id, role_rx)
    base = server_base(spec)
    parsed = urllib.parse.urlsplit(base)
    net = _net(ctx)
    https_ok = parsed.scheme == "https" or (parsed.scheme == "http" and net.get("allow_http") is True)
    host_ok = parsed.hostname in net.get("allowed_hosts", [])
    declared = [p for p in op.get("parameters", []) if isinstance(p, dict)]
    rendered = path
    for prm in [p for p in declared if p.get("in") == "path"]:
        name = prm.get("name")
        if name not in path_params:
            core.fail("INPUT_INVALID", "Missing required path parameter.", parameter=name)
        val = str(path_params[name])
        if not SEGMENT_RE.match(val) or val in (".", ".."):
            core.fail("INPUT_INVALID", "Path parameter contains unsupported characters.", parameter=name)
        rendered = rendered.replace("{" + name + "}", urllib.parse.quote(val, safe=""))
    if "{" in rendered:
        core.fail("INPUT_INVALID", "Unfilled path template in the spec path.", path=path)
    allowed_query = {p.get("name"): p for p in declared if p.get("in") == "query"}
    qs = {}
    for k, v in query.items():
        if k not in allowed_query:
            core.fail("INPUT_INVALID", "Query parameter is not declared by the spec.", parameter=k)
        if not isinstance(v, (str, int, float, bool)):
            core.fail("INPUT_INVALID", "Query values must be scalar.", parameter=k)
        qs[k] = str(v).lower() if isinstance(v, bool) else str(v)
    for name, prm in allowed_query.items():
        if prm.get("required") and name not in qs:
            core.fail("INPUT_INVALID", "Missing required query parameter.", parameter=name)
    body_bytes, body_schema = None, None
    rb = op.get("requestBody")
    content_types = []
    if rb is not None:
        content = (rb.get("content") or {}) if isinstance(rb, dict) else {}
        content_types = sorted(content.keys())
        if "application/json" in content:
            body_schema = _resolve(content["application/json"].get("schema", {}), spec)
            if body is None:
                if rb.get("required"):
                    core.fail("INPUT_INVALID", "Request body is required.")
            else:
                core.check_json(body)
                validate_body(body, body_schema)
                body_bytes = json.dumps(body, sort_keys=True).encode("utf-8")
        elif body is not None and "application/octet-stream" not in content_types:
            core.fail("INPUT_INVALID", "This operation does not accept a JSON body.", content_types=content_types)
    elif body is not None:
        core.fail("INPUT_INVALID", "This operation has no request body.")
    url = base + rendered + ("?" + urllib.parse.urlencode(sorted(qs.items())) if qs else "")
    reasons = []
    if not https_ok:
        reasons.append("server URL is not HTTPS")
    if not host_ok:
        reasons.append(f"host {parsed.hostname} is not in network.allowed_hosts")
    allowlisted = operation_id in net.get("allowed_operations", [])
    if not allowlisted:
        reasons.append("operation is not in network.allowed_operations")
    return {"operation_id": operation_id, "method": method, "url": url, "host": parsed.hostname, "path": rendered,
            "query": qs, "body_bytes": body_bytes, "body_sha256": hashlib.sha256(body_bytes).hexdigest() if body_bytes else None,
            "allowlisted": allowlisted, "host_allowed": host_ok, "https": https_ok, "blocked_reasons": reasons,
            "mutating": method in MUTATING, "content_types": content_types}


def validate_body(body, schema):
    """Validate a JSON body with the internal schema subset supported by tools-core."""
    try:
        core.validate_schema(body, schema)
    except core.ToolError as exc:
        raise core.ToolError("SCHEMA_INVALID", "Request body does not match the spec schema.", {"reason": exc.message}, status=exc.status)


def _http(req_plan, ctx, api_key):
    net = _net(ctx)
    timeout = min(float(net["timeout_seconds"]), ctx.remaining())
    max_bytes = ctx.config["limits"]["http_response_bytes"]
    retries = int(net["retries"])
    backoff = float(net["backoff_seconds"])
    max_wait = float(net["max_retry_wait_seconds"])
    method = req_plan["method"]

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    headers = {"Accept": "application/json", net["auth_header"]: net["auth_prefix"] + api_key}
    body = req_plan.get("_body")
    if body is not None:
        headers["Content-Type"] = req_plan.get("_content_type", "application/json")
    attempt, log = 0, []
    while True:
        ctx.check()
        req = urllib.request.Request(req_plan["url"], data=body, method=method, headers=headers)
        try:
            with opener.open(req, timeout=timeout) as resp:
                data = resp.read(max_bytes + 1)
                if len(data) > max_bytes:
                    core.fail("RESPONSE_TOO_LARGE", "Response exceeded the configured byte limit.", limit=max_bytes)
                return {"status": resp.status, "body": data.decode("utf-8", "replace")[:4000], "attempts": attempt + 1, "log": log}
        except urllib.error.HTTPError as exc:
            status = exc.code
            detail = exc.read(4000).decode("utf-8", "replace")
            retriable = status in RETRY_STATUS and attempt < retries and (method != "POST" or status == 429)
            if not retriable:
                return {"status": status, "body": detail, "attempts": attempt + 1, "log": log}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # A POST that failed without a response may have been processed; do not retry it.
            if method == "POST" or attempt >= retries:
                core.fail("NETWORK_ERROR", "Request failed before a response was received.", reason=type(exc).__name__)
        wait = min(backoff * (2 ** attempt), max_wait)
        log.append({"attempt": attempt + 1, "wait_seconds": wait})
        time.sleep(wait)
        ctx.check()
        attempt += 1


def _execute_gate(req_plan, ctx):
    net = _net(ctx)
    reasons = list(req_plan["blocked_reasons"])
    if net.get("enabled") is not True:
        reasons.append("network.enabled is false")
    key_present = bool(os.environ.get(net["api_key_env"]))
    if not key_present:
        reasons.append(f"environment variable {net['api_key_env']} is not set")
    if reasons:
        core.fail("POLICY_DENIED", "Live execution is blocked by policy.", reasons=reasons)
    ctx.approve("open_cloud_request", {"operation_id": req_plan["operation_id"], "method": req_plan["method"], "host": req_plan["host"]})
    return os.environ[net["api_key_env"]]


def run_operation(d, cap, ctx, spec_role, extra=None):
    spec_path = core.text(d, "spec_path", maximum=500)
    spec = load_spec(spec_path, ctx)
    operation_id = core.text(d, "operation_id", maximum=128)
    if not ID_RE.match(operation_id):
        core.fail("INPUT_INVALID", "operation_id has unsupported characters.")
    path_params = core.obj(d, "path_params")
    query = core.obj(d, "query")
    body = d.get("body")
    execute = core.boolean(d, "execute", False)
    role_rx = re.compile(spec_role) if spec_role else None
    req = build_request(spec, operation_id, path_params, query, body, role_rx, ctx)
    if req["body_bytes"] is not None:
        req["_body"] = req["body_bytes"]
    if extra:
        req.update(extra(d, ctx, req))
    findings = [chk("cloud.https", req["https"], "Server URL uses HTTPS." if req["https"] else "Server URL must use HTTPS.", "fail"),
                chk("cloud.host", req["host_allowed"], "Host is in the configured allowlist." if req["host_allowed"] else f"Host {req['host']} is not allowlisted.", "fail"),
                chk("cloud.allowlist", req["allowlisted"], "Operation is allowlisted for execution." if req["allowlisted"] else "Operation is not allowlisted; the request is a plan only.", "warning"),
                chk("cloud.mutating", None if (req["mutating"] and execute) else True,
                    ("Mutating request executed after approval; review the response and audit record." if req["mutating"] and execute
                     else ("Mutating request is planned only (dry run)." if req["mutating"] else "Read-only request.")), "info"),
                chk("cloud.spec", None, "The OpenAPI document is operator-supplied; verify it against the official specification before live use.")]
    plan = {k: v for k, v in req.items() if not k.startswith("_") and k != "body_bytes"}
    plan["executable"] = not req["blocked_reasons"]
    plan["dry_run"] = not execute
    if execute:
        api_key = _execute_gate(req, ctx)
        response = _http(req, ctx, api_key)
        plan["response"] = response
        findings.append(chk("cloud.response", 200 <= response["status"] < 300, f"HTTP {response['status']} after {response['attempts']} attempt(s).", "fail" if not 200 <= response["status"] < 300 else "info"))
    arts = {f"{operation_id}-request-plan.json": plan}
    return out({"operation_id": operation_id, "method": req["method"], "host": req["host"], "path": req["path"], "dry_run": not execute,
                "executable": plan["executable"], "blocked_reasons": req["blocked_reasons"]}, findings, arts)


# ---------------------------------------------------------------- capability handlers

def h_openapi_client(d, cap, ctx):
    return run_operation(d, cap, ctx, None)


def h_data_api(d, cap, ctx):
    return run_operation(d, cap, ctx, r"(?i)datastore|entry|data")


def h_universe_api(d, cap, ctx):
    return run_operation(d, cap, ctx, r"(?i)universe|place")


def h_messaging_api(d, cap, ctx):
    return run_operation(d, cap, ctx, r"(?i)message")


def h_asset_api(d, cap, ctx):
    return run_operation(d, cap, ctx, r"(?i)asset")


def _publish_file(d, ctx, execute):
    file_path = core.text(d, "file_path", maximum=500)
    p = Path(file_path).expanduser()
    if p.suffix.lower() not in (".rbxl", ".rbxlx"):
        core.fail("INPUT_INVALID", "Publish input must be a .rbxl or .rbxlx file.")
    if p.is_symlink():
        core.fail("RESOURCE_MISSING", "Place file is a symlink.")
    if not p.is_file():
        if execute:
            core.fail("RESOURCE_MISSING", "Place file does not exist.")
        return p, {"file_name": p.name, "file_bytes": None, "file_sha256": None, "file_present": False}
    max_bytes = _net(ctx).get("max_upload_bytes", 104857600)
    size = p.stat().st_size
    if size > max_bytes:
        core.fail("INPUT_LIMIT", "Place file exceeds network.max_upload_bytes.", limit=max_bytes, actual=size)
    digest = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return p, {"file_name": p.name, "file_bytes": size, "file_sha256": digest.hexdigest()}


def h_publish(d, cap, ctx):
    body = d.get("body", {"versionType": "Saved"})
    if not isinstance(body, dict):
        core.fail("INPUT_INVALID", "body must be an object.")
    if body.get("versionType", "Saved") != "Saved":
        core.fail("POLICY_DENIED", "Only Saved versions can be prepared; live publication is not supported by this tool.", version_type=body.get("versionType"))
    execute = core.boolean(d, "execute", False)
    path, meta = _publish_file(d, ctx, execute)
    dd = dict(d)
    dd["body"] = {"versionType": "Saved"}

    def extra(_d, _ctx, req):
        info = dict(meta)
        info["version_type"] = "Saved"
        reasons = list(req["blocked_reasons"])
        if not meta.get("file_present", True):
            reasons.append("place file not found; the upload cannot be verified in a dry run")
        if "application/octet-stream" in req["content_types"]:
            info["upload_mode"] = "binary"
            if execute:
                with path.open("rb") as fh:
                    info["_body"] = fh.read(_net(_ctx)["max_upload_bytes"] + 1)
                info["_content_type"] = "application/octet-stream"
        else:
            info["upload_mode"] = "none"
            reasons.append("spec declares no application/octet-stream body for this operation; the place file would not be uploaded")
        info["blocked_reasons"] = reasons
        return info

    return run_operation(dd, cap, ctx, r"(?i)publish|place|version", extra)


def h_webhook(d, cap, ctx):
    body = core.text(d, "body", maximum=200000, required=False)
    if body is None:
        core.fail("INPUT_INVALID", "body is required.")
    env = core.text(d, "secret_env", maximum=64)
    if not re.match(r"^[A-Z][A-Z0-9_]{2,63}$", env):
        core.fail("INPUT_INVALID", "secret_env must be an uppercase environment variable name.")
    signature = core.text(d, "signature", maximum=200)
    timestamp = core.integer(d, "timestamp", minimum=0, maximum=4102444800)
    now = core.integer(d, "now", default=int(time.time()), minimum=0, maximum=4102444800)
    scheme = core.text(d, "signature_scheme", default="hmac-sha256", maximum=20)
    delivery = core.text(d, "delivery_id", maximum=128, required=False) or ""
    seen = set(core.array(d, "seen_ids", maximum=10000, kind=str))
    if scheme != "hmac-sha256":
        core.fail("INPUT_INVALID", "Only hmac-sha256 is supported.")
    secret = os.environ.get(env)
    if not secret:
        status, reason = "blocked", f"environment variable {env} is not set"
        expected = None
    else:
        expected = hmac.new(secret.encode("utf-8"), f"{timestamp}.{body}".encode("utf-8"), hashlib.sha256).hexdigest()
        given = signature[len("sha256="):] if signature.startswith("sha256=") else signature
        if abs(now - timestamp) > 300:
            status, reason = "rejected", "timestamp outside the 300-second window"
        elif not hmac.compare_digest(expected, given.lower()):
            status, reason = "rejected", "signature mismatch"
        elif delivery and delivery in seen:
            status, reason = "duplicate", "delivery id already processed"
        else:
            status, reason = "accepted", "signature valid, fresh, and not a duplicate"
    findings = [chk("webhook.secret", secret is not None, "Secret environment variable is set (value not shown)." if secret else reason, "fail" if not secret else "info"),
                chk("webhook.signature", status not in ("rejected",), reason.capitalize() + "." if status != "rejected" else "Signature rejected: " + reason + ".", "fail"),
                chk("webhook.replay", status != "duplicate", "Delivery is new." if status != "duplicate" else "Duplicate delivery ignored; process each delivery once.", "info"),
                chk("webhook.comparison", True, "Signatures are compared in constant time.")]
    return out({"status": status, "reason": reason, "delivery_id": delivery or None}, findings)


def h_oauth(d, cap, ctx):
    endpoint = core.text(d, "authorization_endpoint", maximum=500)
    client_id = core.text(d, "client_id", maximum=200)
    redirect = core.text(d, "redirect_uri", maximum=500)
    scopes = core.array(d, "scopes", required=True, kind=str, maximum=50)
    parsed = urllib.parse.urlsplit(endpoint)
    loopback = parsed.hostname in ("localhost", "127.0.0.1") and urllib.parse.urlsplit(redirect).hostname in ("localhost", "127.0.0.1")
    if parsed.scheme != "https":
        core.fail("POLICY_DENIED", "authorization_endpoint must use HTTPS.")
    rp = urllib.parse.urlsplit(redirect)
    if rp.scheme != "https" and not (rp.scheme == "http" and loopback):
        core.fail("POLICY_DENIED", "redirect_uri must be HTTPS, or HTTP on localhost for development.")
    bad = [s for s in scopes if not SCOPE_RE.match(s)]
    if bad:
        core.fail("INPUT_INVALID", "Scopes contain unsupported characters.", scopes=bad[:5])
    state = core.text(d, "state", default=None, maximum=256, required=False) or _secrets.token_urlsafe(24)
    verifier = core.text(d, "code_verifier", maximum=128, required=False) or _secrets.token_urlsafe(48)[:64]
    if not 43 <= len(verifier) <= 128 or not re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier):
        core.fail("INPUT_INVALID", "code_verifier must be 43-128 unreserved characters.")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "scope": " ".join(sorted(set(scopes))),
              "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    url = endpoint + ("&" if parsed.query else "?") + urllib.parse.urlencode(params)
    findings = [chk("oauth.pkce", True, "PKCE S256 challenge included."),
                chk("oauth.state", len(state) >= 16, "State is set and checked on return." if len(state) >= 16 else "State must be at least 16 characters.", "fail"),
                chk("oauth.scopes", True, f"{len(set(scopes))} distinct scope(s); request only what the feature needs."),
                chk("oauth.secret", True, "No client secret is generated or stored; the verifier is returned for the caller to keep."),
                chk("oauth.exchange", None, "Token exchange is not performed by this tool; it needs the operator's server-side flow.")]
    return out({"authorization_url": url, "state": state, "code_challenge_method": "S256", "scopes": sorted(set(scopes))}, findings,
               {"oauth-pkce.json": {"authorization_url": url, "code_verifier": verifier, "code_challenge": challenge, "state": state,
                                    "note": "Keep code_verifier private until the code exchange; do not commit this file."}})


def h_auth(d, cap, ctx):
    env = core.text(d, "api_key_env", maximum=64)
    if not re.match(r"^[A-Z][A-Z0-9_]{2,63}$", env):
        core.fail("INPUT_INVALID", "api_key_env must be an uppercase environment variable name.")
    scopes = core.array(d, "scopes", required=True, kind=str, maximum=50)
    rotation = core.integer(d, "rotation_days", minimum=1, maximum=3650)
    oauth = core.boolean(d, "oauth", False)
    bad = [s for s in scopes if not SCOPE_RE.match(s)]
    wildcard = [s for s in scopes if "*" in s]
    present = bool(os.environ.get(env))
    findings = [chk("auth.environment", present, f"{env} is set (value not read or shown)." if present else f"{env} is not set in this environment.", "warning"),
                chk("auth.scopes_valid", not bad, "Scopes match the expected format." if not bad else "Invalid scopes: " + ", ".join(bad[:5]), "fail"),
                chk("auth.least_privilege", not wildcard, "No wildcard scopes." if not wildcard else "Wildcard scopes are not least privilege: " + ", ".join(wildcard[:5]), "warning"),
                chk("auth.rotation", rotation <= 90, f"Rotate every {rotation} days." if rotation <= 90 else f"Rotation interval {rotation} days is longer than 90.", "warning"),
                chk("auth.flow", True, "OAuth applications use PKCE; API keys stay in the environment." if oauth else "API key flow: keep the key in the environment only.", "info")]
    return out({"environment_ready": present, "scopes": sorted(set(scopes)), "rotation_days": rotation, "oauth": oauth}, findings)


def h_deployment_verification(d, cap, ctx):
    version = core.text(d, "version", maximum=40)
    checks = core.array(d, "checks", required=True, kind=dict, maximum=500)
    live = core.boolean(d, "live_observation", False)
    rows = []
    for c in checks:
        name = core.text(c, "name", maximum=120)
        passed = c.get("passed")
        if passed not in (True, False, None):
            core.fail("INPUT_INVALID", "checks.passed must be true, false, or null.", check=name)
        rows.append({"name": name, "passed": passed, "evidence": core.text(c, "evidence", maximum=300, required=False) or ""})
    failed = [r["name"] for r in rows if r["passed"] is False]
    unverified = [r["name"] for r in rows if r["passed"] is None]
    decision = "blocked" if failed else ("review" if unverified else "ready")
    findings = [chk("deployment.checks", not failed, "No failed checks." if not failed else "Failed checks: " + ", ".join(failed), "fail"),
                chk("deployment.evidence", not unverified, "Every check has a result." if not unverified else f"{len(unverified)} check(s) have no result.", "warning"),
                chk("deployment.live", not live, "No live observation is claimed by this tool." if not live else "Live observations must be attached as evidence; this tool does not query services.", "info")]
    return out({"version": version, "decision": decision, "checks": rows}, findings, {"deployment-verification.json": {"version": version, "decision": decision, "checks": rows}})


HANDLERS = {
    "publish": h_publish, "asset_api": h_asset_api, "auth": h_auth, "deployment_verification": h_deployment_verification,
    "openapi_client": h_openapi_client, "data_api": h_data_api, "universe_api": h_universe_api, "messaging_api": h_messaging_api,
    "webhook": h_webhook, "oauth": h_oauth,
}
