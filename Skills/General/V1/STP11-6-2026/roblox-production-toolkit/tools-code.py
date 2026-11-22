"""Code family: Luau code generation, static checks, and text-level code tools.

Loaded as rbx_code by tools-roblox.py. Everything here is local and deterministic:
- Generation writes Luau source text as an artifact. It is never executed by this tool.
- Static checks use a heuristic Luau lexer (comments and strings stripped). They are lint
  aids, not a Luau parser or type checker. Real type checking needs luau-analyze, which this
  tool does not install or require.
- Pure operations (clamp, remap, lerp, sum, mean) and pipeline/retry/signal/token-bucket
  simulations are executed in Python so their outputs can be inspected and tested.
"""
import difflib
import hashlib
import base64
import math
import os
import re
import secrets as _secrets
import rbx_core as core
from rbx_core import chk

FAMILY = "code"
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
LUAU_RESERVED = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local", "nil", "not",
                 "or", "repeat", "return", "then", "true", "until", "while", "continue", "type", "export"}
TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
LUAU_TYPES = {"number": "number", "string": "string", "boolean": "boolean", "Vector3": "Vector3", "Color3": "Color3",
              "CFrame": "CFrame", "table": "{ [any]: any }"}
GUARDS = {"number": 'typeof(value.{f}) == "number"', "string": 'typeof(value.{f}) == "string"',
          "boolean": 'typeof(value.{f}) == "boolean"', "Vector3": 'typeof(value.{f}) == "Vector3"',
          "Color3": 'typeof(value.{f}) == "Color3"', "CFrame": 'typeof(value.{f}) == "CFrame"',
          "table": 'typeof(value.{f}) == "table"'}
PURE_OPS = ("clamp", "remap", "lerp", "sum", "mean")
PROP_KINDS = {"Anchored": "boolean", "CanCollide": "boolean", "Enabled": "boolean", "Transparency": "unit",
              "Size": "Vector3", "Position": "Vector3", "Name": "string", "Material": "string", "Color": "Color3"}


def out(result, findings, artifacts=None, warnings=None):
    return {"result": result, "findings": findings, "artifacts": artifacts or {}, "warnings": warnings or []}


def _limit_source(src, ctx, key="source"):
    limit = ctx.config["studio"]["luau_script_chars"]
    if len(src) > limit:
        core.fail("INPUT_LIMIT", "Luau source exceeds the configured character limit.", field=key, limit=limit, actual=len(src))
    return src


def _lstr(value):
    if not isinstance(value, str) or any(ord(c) < 32 and c not in "\n\t" for c in value):
        core.fail("INPUT_INVALID", "Text placed into Luau must be printable.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


def _lnum(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        core.fail("INPUT_INVALID", "Numeric literal required.")
    return repr(float(value)) if isinstance(value, float) else str(value)


def _ident(value, field):
    if not isinstance(value, str) or not IDENT.match(value) or value in LUAU_RESERVED:
        core.fail("INPUT_INVALID", f"{field} must be a Luau identifier.", value=str(value)[:40])
    return value


# ---------------------------------------------------------------- heuristic Luau lexer

LONG_OPEN = re.compile(r"\[(=*)\[")


def strip_luau(src):
    """Remove comments and string contents, keeping line breaks so line numbers stay correct."""
    out_parts, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("--", i):
            m = LONG_OPEN.match(src, i + 2)
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, m.end())
                j = n if j < 0 else j + len(close)
            else:
                j = src.find("\n", i)
                j = n if j < 0 else j
            out_parts.append("\n" * src.count("\n", i, j))
            i = j
            continue
        if src[i] in "\"'":
            q, j = src[i], i + 1
            while j < n and src[j] != q and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out_parts.append('""')
            out_parts.append("\n" * src.count("\n", i, min(j, n)))
            i = j + 1
            continue
        m = LONG_OPEN.match(src, i)
        if m:
            close = "]" + m.group(1) + "]"
            j = src.find(close, m.end())
            j = n if j < 0 else j + len(close)
            out_parts.append('""')
            out_parts.append("\n" * src.count("\n", i, j))
            i = j
            continue
        out_parts.append(src[i])
        i += 1
    return "".join(out_parts)


ALLOC_RE = re.compile(r"Instance\.new\s*\(|=\s*\{(?!\s*\})")
YIELD_INVOKE = re.compile(r":\s*Invoke(?:Client|Server)\s*\(")
SERVER_ONLY_APIS = re.compile(r"\b(DataStoreService|ServerStorage|ServerScriptService|MessagingService|OrderedDataStore)\b")
DATASTORE_CALL = re.compile(r":\s*(GetAsync|SetAsync|UpdateAsync|IncrementAsync|RemoveAsync)\s*\(")
DECL_LOCAL = re.compile(r"\blocal\s+(?:function\s+)?([A-Za-z_][\w\s,]*)")
DECL_FUNC_PARAMS = re.compile(r"\bfunction\b[^(]*\(([^)]*)\)")
DECL_FOR = re.compile(r"\bfor\s+([A-Za-z_][\w\s,]*?)\s*(?:=|in\b)")
ASSIGN = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:[+\-*/]?=)(?!=)")


def luau_issues(src):
    """Return heuristic findings as (rule, severity, line, message)."""
    code = strip_luau(src)
    lines = code.split("\n")
    issues = []
    rules = [
        ("deprecated_wait", "warning", re.compile(r"(?<![\w.:])wait\s*\("), "wait() is deprecated; use task.wait()."),
        ("deprecated_spawn", "warning", re.compile(r"(?<![\w.:])spawn\s*\("), "spawn() is deprecated; use task.spawn()."),
        ("deprecated_delay", "warning", re.compile(r"(?<![\w.:])delay\s*\("), "delay() is deprecated; use task.delay()."),
        ("dynamic_code", "fail", re.compile(r"\b(loadstring|getfenv|setfenv)\s*\("), "Dynamic code or environment access is not allowed."),
        ("yielding_invoke", "warning", YIELD_INVOKE, "RemoteFunction invocation can yield indefinitely; wrap it with a timeout."),
        ("unbounded_wait", "warning", re.compile(r":\s*Wait\s*\(\s*\)"), "Unbounded :Wait() on a signal can hang; add a timeout."),
    ]
    for idx, line in enumerate(lines, 1):
        for rule, sev, rx, msg in rules:
            if rx.search(line):
                issues.append((rule, sev, idx, msg))
        if re.match(r"^\s*[A-Za-z_][\w.\[\]\"]*:(?:Connect|Once)\s*\(", line):
            issues.append(("connection_not_stored", "warning", idx, "Connection is not stored; it cannot be disconnected later."))
        if DATASTORE_CALL.search(line):
            window = "\n".join(lines[max(0, idx - 4):idx])
            if "pcall" not in window and "xpcall" not in window:
                issues.append(("datastore_unprotected", "fail", idx, "DataStore call is not wrapped in pcall; failures will error the script."))
    # possible globals: assigned names never declared as locals, parameters, loop variables, or functions
    declared = set()
    for m in DECL_LOCAL.finditer(code):
        for name in m.group(1).split(","):
            declared.add(name.strip().split()[0] if name.strip() else "")
    for m in DECL_FUNC_PARAMS.finditer(code):
        for p in m.group(1).split(","):
            if p.strip():
                declared.add(p.strip().split(":")[0].strip())
    for m in DECL_FOR.finditer(code):
        for name in m.group(1).split(","):
            if name.strip():
                declared.add(name.strip().split()[0])
    for idx, line in enumerate(lines, 1):
        m = ASSIGN.match(line)
        if m and not re.match(r"^\s*local\b", line) and m.group(1) not in declared and m.group(1) not in LUAU_RESERVED:
            issues.append(("possible_global", "warning", idx, f"'{m.group(1)}' is assigned without local; it may be a global."))
    # hot-loop allocations: allocations within 40 lines after a per-frame or while-true loop
    for idx, line in enumerate(lines, 1):
        if re.search(r"RunService\.(?:Heartbeat|RenderStepped|Stepped)|while\s+true\s+do", line):
            for j in range(idx, min(len(lines), idx + 40) + 1):
                if ALLOC_RE.search(lines[j - 1]):
                    issues.append(("hot_loop_allocation", "warning", j, "Allocation inside a per-frame or unbounded loop."))
    # block balance (approximate keyword count)
    opens = len(re.findall(r"\b(function|if|do|repeat)\b", code))
    closes = len(re.findall(r"\b(end|until)\b", code))
    if opens != closes:
        issues.append(("block_balance", "fail", 0, f"Block keywords do not balance (openers {opens}, closers {closes})."))
    return issues


# ---------------------------------------------------------------- static analysis handlers

def h_code_review(d, cap, ctx):
    src = _limit_source(core.text(d, "source", maximum=300000), ctx)
    side = core.text(d, "side", maximum=10, required=False) or "shared"
    if side not in ("client", "server", "shared"):
        core.fail("INPUT_INVALID", "side must be client, server or shared.")
    tests = core.boolean(d, "tests_present", False)
    issues = luau_issues(src)
    header = src.lstrip().split("\n", 1)[0].strip()
    strict = header in ("--!strict",)
    code = strip_luau(src)
    authority = SERVER_ONLY_APIS.findall(code) if side == "client" else []
    cleanup_risk = [i for i in issues if i[0] == "connection_not_stored"]
    findings = [chk("review.strict", strict, "File declares --!strict." if strict else "Add --!strict so type errors surface early.", "warning"),
                chk("review.balance", not any(i[0] == "block_balance" for i in issues), "Block keywords balance." if not any(i[0] == "block_balance" for i in issues) else "Block keywords do not balance; check end/until placement.", "fail"),
                chk("review.dynamic", not any(i[0] == "dynamic_code" for i in issues), "No dynamic code or environment access." if not any(i[0] == "dynamic_code" for i in issues) else "Dynamic code or environment access found.", "fail"),
                chk("review.datastore", not any(i[0] == "datastore_unprotected" for i in issues), "DataStore calls are protected by pcall." if not any(i[0] == "datastore_unprotected" for i in issues) else "Unprotected DataStore calls found.", "fail"),
                chk("review.authority", not authority, "No server-only API in client code." if not authority else "Client code uses server-only APIs: " + ", ".join(sorted(set(authority))), "fail"),
                chk("review.cleanup", not cleanup_risk, "Connections are stored for cleanup." if not cleanup_risk else f"{len(cleanup_risk)} connection(s) are not stored.", "warning"),
                chk("review.tests", tests, "Tests are present for this module." if tests else "Add tests for this module.", "info"),
                chk("review.limits", None, "Heuristic checks only; run luau-analyze for typing and syntax before release.")]
    summary = {}
    for rule, sev, line, msg in issues:
        summary[rule] = summary.get(rule, 0) + 1
    detail = [{"rule": r, "severity": s, "line": l, "message": m} for r, s, l, m in issues[:200]]
    return out({"issues": len(issues), "by_rule": summary, "strict": strict, "side": side}, findings, {"code-review.json": detail})


def h_script_audit(d, cap, ctx):
    src = _limit_source(core.text(d, "source", maximum=300000), ctx)
    cps = core.number(d, "calls_per_second", minimum=0, maximum=100000)
    budget = core.number(d, "allocation_budget_per_second", default=500, minimum=0, maximum=10000000)
    issues = [i for i in luau_issues(src) if i[0] in ("hot_loop_allocation", "deprecated_wait", "deprecated_spawn", "connection_not_stored")]
    allocs_per_call = sum(1 for i in issues if i[0] == "hot_loop_allocation")
    projected = allocs_per_call * cps
    findings = [chk("script_audit.allocations", projected <= budget, f"Projected {projected:.0f} hot-loop allocations per second within budget {budget:.0f}." if projected <= budget else f"Projected {projected:.0f} hot-loop allocations per second exceed the budget of {budget:.0f}.", "warning"),
                chk("script_audit.deprecated", not any(i[0].startswith("deprecated") for i in issues), "No deprecated scheduler calls." if not any(i[0].startswith("deprecated") for i in issues) else "Replace deprecated scheduler calls with task.* functions.", "warning"),
                chk("script_audit.connections", not any(i[0] == "connection_not_stored" for i in issues), "All connections are stored." if not any(i[0] == "connection_not_stored" for i in issues) else "Store connections so they can be disconnected.", "warning"),
                chk("script_audit.profile", None, "Static estimate only; confirm with the Studio MicroProfiler before optimizing.")]
    return out({"hot_loop_allocation_sites": allocs_per_call, "calls_per_second": cps, "projected_allocations_per_second": projected,
                "issues": [{"rule": r, "line": l, "message": m} for r, s, l, m in issues[:100]]}, findings)


def h_remote_audit(d, cap, ctx):
    remotes = core.array(d, "remotes", required=True, kind=dict, maximum=500)
    payload_policy = core.integer(d, "policy_payload_bytes", default=16384, minimum=1, maximum=10000000)
    allowed_types = {"number", "string", "boolean", "table", "Vector3", "CFrame", "Color3", "Instance"}
    rows, failing = [], []
    for r in remotes:
        name = core.text(r, "name", maximum=120)
        kind = core.text(r, "kind", maximum=20)
        direction = core.text(r, "direction", maximum=30)
        types = core.array(r, "arg_types", maximum=30, kind=str)
        max_bytes = core.integer(r, "max_payload_bytes", default=payload_policy, minimum=1, maximum=10000000)
        validates = core.boolean(r, "validates_types", False)
        authority = core.boolean(r, "checks_authority", False)
        rate = r.get("rate_limit_per_second")
        if kind not in ("RemoteEvent", "RemoteFunction"):
            core.fail("INPUT_INVALID", "kind must be RemoteEvent or RemoteFunction.", remote=name)
        if direction not in ("client_to_server", "server_to_client"):
            core.fail("INPUT_INVALID", "direction must be client_to_server or server_to_client.", remote=name)
        unknown = [t for t in types if t not in allowed_types]
        if unknown:
            core.fail("INPUT_INVALID", "arg_types contains an unsupported type.", remote=name, types=unknown)
        problems = []
        if not validates:
            problems.append("types not validated")
        if direction == "client_to_server" and not authority:
            problems.append("authority not checked")
        if direction == "client_to_server" and "Instance" in types:
            problems.append("client supplies Instance references")
        if direction == "client_to_server" and kind == "RemoteFunction":
            problems.append("client-to-server RemoteFunction can yield")
        if direction == "client_to_server" and rate is None:
            problems.append("no rate limit")
        if max_bytes > payload_policy:
            problems.append(f"payload policy {max_bytes} > {payload_policy}")
        rows.append({"name": name, "kind": kind, "direction": direction, "problems": problems})
        if problems:
            failing.append(f"{name}: " + ", ".join(problems))
    hard = [f for f in failing if "types not validated" in f or "authority not checked" in f or "Instance" in f]
    findings = [chk("remote_audit.validation", not any("types not validated" in f for f in failing), "Every remote validates argument types." if not any("types not validated" in f for f in failing) else "Remotes without type validation: " + "; ".join(f for f in failing if "types not validated" in f)[:300], "fail"),
                chk("remote_audit.authority", not any("authority not checked" in f for f in failing), "Client-to-server remotes check authority." if not any("authority not checked" in f for f in failing) else "Remotes without authority checks: " + "; ".join(f for f in failing if "authority not checked" in f)[:300], "fail"),
                chk("remote_audit.instances", not any("Instance references" in f for f in failing), "Clients send no Instance references." if not any("Instance references" in f for f in failing) else "Client-supplied Instance references allow path abuse.", "fail"),
                chk("remote_audit.rate", not any("no rate limit" in f for f in failing), "Client remotes are rate limited." if not any("no rate limit" in f for f in failing) else "Some client remotes have no rate limit.", "warning"),
                chk("remote_audit.payload", not any("payload policy" in f for f in failing), "Payloads are within the size policy." if not any("payload policy" in f for f in failing) else "Some remotes exceed the payload policy.", "warning")]
    return out({"remotes": len(rows), "failing": len(failing), "hard_failures": len(hard), "rows": rows}, findings)


def h_remote_risk(d, cap, ctx):
    calls = core.array(d, "calls", required=True, kind=dict, maximum=500)
    recommend = core.boolean(d, "recommend_async", False)
    rows, sync_no_timeout, hot = [], [], []
    for c in calls:
        remote = core.text(c, "remote", maximum=120)
        invoke = core.text(c, "invoke", maximum=20)
        if invoke not in ("InvokeServer", "InvokeClient"):
            core.fail("INPUT_INVALID", "invoke must be InvokeServer or InvokeClient.", remote=remote)
        timeout = c.get("timeout_seconds")
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60):
            core.fail("INPUT_INVALID", "timeout_seconds must be a number between 0 and 60, or null.", remote=remote)
        rate = core.number(c, "calls_per_second", default=0, minimum=0, maximum=100000)
        rows.append({"remote": remote, "invoke": invoke, "timeout_seconds": timeout, "calls_per_second": rate})
        if timeout is None:
            sync_no_timeout.append(remote)
        if rate > 10:
            hot.append(remote)
    wrapper = None
    if recommend and sync_no_timeout:
        wrapper = "\n".join([
            "-- Generated by tools-roblox.py (remote_risk). Not compiled or executed by this tool.",
            "local function invokeWithTimeout(remote: Instance, timeout: number, ...: any): (boolean, any)",
            "\tlocal done, ok, result = false, false, nil",
            "\ttask.spawn(function(...: any)",
            "\t\tif remote:IsA(\"RemoteFunction\") then",
            "\t\t\tok, result = pcall(function(...: any) return (remote :: any):InvokeServer(...) end, ...)",
            "\t\tend",
            "\t\tdone = true",
            "\tend, ...)",
            "\tlocal started = os.clock()",
            "\twhile not done and os.clock() - started < timeout do",
            "\t\ttask.wait()",
            "\tend",
            "\treturn done and ok, if done then result else \"timeout\"",
            "end",
            "",
            "return invokeWithTimeout",
            ""])
    findings = [chk("remote_risk.timeouts", not sync_no_timeout, "Every invoke has a timeout." if not sync_no_timeout else f"{len(sync_no_timeout)} invoke(s) have no timeout and can hang: " + ", ".join(sync_no_timeout[:10]), "fail"),
                chk("remote_risk.rate", not hot, "No invoke exceeds 10 calls per second." if not hot else "High-rate invokes: " + ", ".join(hot[:10]), "warning"),
                chk("remote_risk.wrapper", wrapper is not None or not recommend or not sync_no_timeout, "Timeout wrapper generated." if wrapper else "Wrapper not needed or not requested.", "info")]
    arts = {"remote-timeout-wrapper.luau": wrapper} if wrapper else {}
    return out({"calls": len(rows), "without_timeout": sync_no_timeout, "high_rate": hot}, findings, arts)


SENSITIVE = {"currency", "inventory", "ownership", "score", "stats", "position", "data", "purchase", "role"}


def h_authority(d, cap, ctx):
    actions = core.array(d, "actions", required=True, kind=dict, maximum=500)
    rows = []
    for a in actions:
        name = core.text(a, "name", maximum=120)
        changes = core.array(a, "changes", maximum=30, kind=str)
        origin = core.text(a, "origin", maximum=10)
        if origin not in ("client", "server"):
            core.fail("INPUT_INVALID", "origin must be client or server.", action=name)
        validated = core.boolean(a, "validated", False)
        limited = core.boolean(a, "rate_limited", False)
        sensitive = bool(SENSITIVE & set(changes))
        if origin == "server":
            klass = "server_authoritative"
        elif sensitive and not validated:
            klass = "client_proposed_unvalidated"
        elif sensitive and not limited:
            klass = "client_validated_not_rate_limited"
        elif sensitive:
            klass = "client_validated_rate_limited"
        else:
            klass = "client_cosmetic_or_local" if not validated else "client_validated_non_sensitive"
        rows.append({"action": name, "changes": changes, "classification": klass, "sensitive": sensitive})
    bad = [r["action"] for r in rows if r["classification"] == "client_proposed_unvalidated"]
    weak = [r["action"] for r in rows if r["classification"] == "client_validated_not_rate_limited"]
    findings = [chk("authority.unvalidated", not bad, "No sensitive state change is accepted from the client unvalidated." if not bad else "Sensitive client changes without validation: " + ", ".join(bad[:10]), "fail"),
                chk("authority.rate", not weak, "Sensitive client actions are rate limited." if not weak else "Sensitive client actions without rate limits: " + ", ".join(weak[:10]), "warning"),
                chk("authority.design", None, "Confirm the classification with the server design; the client must only send requests.")]
    return out({"actions": rows}, findings)


SECRET_PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}")),
    ("assigned_secret", re.compile(r"(?i)(api[_-]?key|client[_-]?secret|secret|token|password|passwd)\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']")),
]


def h_secrets(d, cap, ctx):
    src = _limit_source(core.text(d, "source", maximum=300000), ctx)
    replicated = core.boolean(d, "replicated", False)
    env = core.text(d, "secret_env", maximum=64, required=False) or ""
    hits = []
    for idx, line in enumerate(src.split("\n"), 1):
        for name, rx in SECRET_PATTERNS:
            for m in rx.finditer(line):
                hits.append({"kind": name, "line": idx, "length": len(m.group(0)), "masked": "[masked]"})
    env_ok = bool(re.match(r"^[A-Z][A-Z0-9_]{2,63}$", env)) if env else None
    findings = [chk("secrets.none", not hits, "No secret-like literal found in the source." if not hits else f"{len(hits)} secret-like literal(s) found; values are masked in the report.", "fail"),
                chk("secrets.replicated", not (replicated and hits), "Replicated code contains no secret literal." if not (replicated and hits) else "Replicated code contains a secret; clients can read it.", "fail"),
                chk("secrets.env", env_ok if env else None, f"Secret is read from environment variable {env}." if env_ok else ("Use an uppercase environment variable name (for example ROBLOX_OPEN_CLOUD_API_KEY)." if env else "Name the environment variable that holds the secret.")),
                chk("secrets.storage", None, "In-experience secrets belong in HttpService:GetSecret on the server; external tools use environment variables.")]
    return out({"hits": hits, "replicated": replicated, "secret_env_valid": env_ok}, findings)


def _merge_hunks(base, other):
    sm = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [(i1, i2, other[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def _apply(base, start, end, hunks):
    text, pos = [], start
    for i1, i2, rep in sorted(hunks):
        text.extend(base[pos:i1])
        text.extend(rep)
        pos = i2
    text.extend(base[pos:end])
    return text


def three_way(base, ours, theirs):
    ha = [("ours", h) for h in _merge_hunks(base, ours)]
    ht = [("theirs", h) for h in _merge_hunks(base, theirs)]
    allh = sorted(ha + ht, key=lambda x: (x[1][0], x[1][1]))
    groups = []
    for side, (i1, i2, rep) in allh:
        if groups and (i1 < groups[-1]["end"] or (i1 == i2 == groups[-1]["start"] == groups[-1]["end"])):  # overlap, or two insertions at one point
            g = groups[-1]
            g["end"] = max(g["end"], i2)
            g["items"].append((side, (i1, i2, rep)))
        else:
            groups.append({"start": i1, "end": i2, "items": [(side, (i1, i2, rep))]})
    result, pos, conflicts = [], 0, []
    for g in groups:
        result.extend(base[pos:g["start"]])
        sides = {s for s, _ in g["items"]}
        if len(sides) == 1:
            result.extend(_region(base, g, [h for _, h in g["items"]]))
        else:
            o_h = [h for s, h in g["items"] if s == "ours"]
            t_h = [h for s, h in g["items"] if s == "theirs"]
            if o_h and t_h and sorted(o_h) == sorted(t_h):
                result.extend(_region(base, g, o_h))
            else:
                ours_text = _region(base, g, o_h) if o_h else base[g["start"]:g["end"]]
                theirs_text = _region(base, g, t_h) if t_h else base[g["start"]:g["end"]]
                conflicts.append(g["start"])
                result.extend(["<<<<<<< ours"] + ours_text + ["======="] + theirs_text + [">>>>>>> theirs"])
        pos = g["end"]
    result.extend(base[pos:])
    return result, conflicts


def _region(base, g, hunks):
    return _apply(base, g["start"], g["end"], hunks)


def h_merge(d, cap, ctx):
    base = core.text(d, "base", maximum=300000, required=False) or ""
    ours = core.text(d, "ours", maximum=300000, required=False) or ""
    theirs = core.text(d, "theirs", maximum=300000, required=False) or ""
    if max(len(base), len(ours), len(theirs)) > ctx.config["limits"]["source_bytes"]:
        core.fail("INPUT_LIMIT", "Merge input exceeds the source size limit.")
    b, o, t = base.split("\n"), ours.split("\n"), theirs.split("\n")
    merged, conflicts = three_way(b, o, t)
    text = "\n".join(merged)
    clean = not conflicts
    findings = [chk("merge.conflicts", clean, "Non-overlapping changes merged cleanly." if clean else f"{len(conflicts)} true conflict(s) need a human decision; markers are in the output.", "fail" if not clean else "info"),
                chk("merge.markers", not conflicts or "<<<<<<< ours" in text, "Conflict markers are written where needed."),
                chk("merge.review", None, "Review the merged text before committing; this resolves text, not semantics.")]
    return out({"conflicts": len(conflicts), "lines": len(merged), "clean": clean}, findings, {"merged.txt": text})


def h_refactor(d, cap, ctx):
    src = _limit_source(core.text(d, "source", maximum=300000), ctx)
    module = _ident(core.text(d, "module_name", maximum=64), "module_name")
    lines = src.split("\n")
    code = strip_luau(src).split("\n")
    funcs = []
    for i, line in enumerate(code):
        m = re.match(r"^\s*(?:local\s+)?function\s+([A-Za-z_][\w.:]*)", line)
        if m:
            depth, end = 0, None
            for j in range(i, len(code)):
                depth += len(re.findall(r"\b(function|if|do|repeat)\b", code[j])) - len(re.findall(r"\b(end|until)\b", code[j]))
                if depth <= 0 and j > i:
                    end = j
                    break
            funcs.append({"name": m.group(1), "start": i + 1, "lines": (end - i + 1) if end is not None else None})
    long_funcs = [f for f in funcs if f["lines"] and f["lines"] > 40]
    seen = {}
    for i in range(len(lines) - 2):
        block = tuple(x.strip() for x in lines[i:i + 3])
        if all(len(x) >= 8 for x in block):
            seen.setdefault(block, []).append(i + 1)
    dupes = [{"lines": v, "text": list(k)} for k, v in seen.items() if len(v) > 1]
    has_return = any(re.match(r"^\s*return\s+\w+\s*$", x) for x in lines[-5:])
    recs = []
    for f in long_funcs:
        recs.append(f"Split {f['name']} ({f['lines']} lines) into smaller functions.")
    for dup in dupes[:20]:
        recs.append(f"Extract the repeated block at lines {', '.join(map(str, dup['lines']))} into a shared function.")
    if not has_return:
        recs.append(f"Return a table from {module} so other modules can require it.")
    findings = [chk("refactor.long_functions", not long_funcs, "No function over 40 lines." if not long_funcs else f"{len(long_funcs)} function(s) over 40 lines.", "warning"),
                chk("refactor.duplicates", not dupes, "No repeated three-line blocks." if not dupes else f"{len(dupes)} repeated three-line block(s).", "warning"),
                chk("refactor.module_return", has_return, "Module returns a value." if has_return else "Module does not visibly return a value.", "warning"),
                chk("refactor.behavior", None, "Recommendations preserve behavior only when the extracted code is unchanged; run tests after applying.")]
    return out({"functions": funcs[:200], "recommendations": recs}, findings, {"refactor-plan.json": {"module": module, "functions": funcs, "recommendations": recs}})


# ---------------------------------------------------------------- generators

def _header(title):
    return [f"--!strict", f"-- {title}", "-- Generated by tools-roblox.py. Review before use; this tool does not run Luau.", ""]


def h_luau_module(d, cap, ctx):
    name = _ident(core.text(d, "module_name", maximum=64), "module_name")
    op = core.text(d, "operation", maximum=20)
    if op not in PURE_OPS:
        core.fail("INPUT_INVALID", "operation must be one of clamp, remap, lerp, sum, mean.", allowed=list(PURE_OPS))
    lo = core.number(d, "minimum", default=0, minimum=-1e9, maximum=1e9)
    hi = core.number(d, "maximum", default=1, minimum=-1e9, maximum=1e9)
    if lo > hi:
        core.fail("INPUT_INVALID", "minimum must not exceed maximum.")
    body = {
        "clamp": ["function M.clamp(value: number): number", f"\treturn math.clamp(value, {_lnum(lo)}, {_lnum(hi)})", "end"],
        "remap": ["function M.remap(value: number): number", f"\tlocal t = (value - {_lnum(lo)}) / math.max({_lnum(hi)} - {_lnum(lo)}, 1e-9)", "\treturn math.clamp(t, 0, 1)", "end"],
        "lerp": ["function M.lerp(a: number, b: number, t: number): number", "\treturn a + (b - a) * math.clamp(t, 0, 1)", "end"],
        "sum": ["function M.sum(values: { number }): number", "\tlocal total = 0", "\tfor _, v in values do", "\t\ttotal += v", "\tend", "\treturn total", "end"],
        "mean": ["function M.mean(values: { number }): number?", "\tif #values == 0 then", "\t\treturn nil", "\tend", "\tlocal total = 0", "\tfor _, v in values do", "\t\ttotal += v", "\tend", "\treturn total / #values", "end"],
    }[op]
    lines = _header(f"{name}: deterministic {op} module") + ["local M = {}", ""] + body + ["", "return M", ""]
    samples = d.get("samples", [])
    if not isinstance(samples, list) or len(samples) > 200:
        core.fail("INPUT_INVALID", "samples must be an array of at most 200 items.")
    expected = [{"input": s, "output": _pure(op, s, lo, hi)} for s in samples]
    findings = [chk("luau_module.operation", True, f"Generated {op} with bounds [{lo}, {hi}]."),
                chk("luau_module.typed", True, "Module is --!strict with typed parameters."),
                chk("luau_module.vectors", True, f"{len(expected)} test vector(s) computed by the Python reference.")]
    return out({"operation": op, "vectors": len(expected)}, findings, {f"{name}.luau": "\n".join(lines), f"{name}-vectors.json": expected})


def _pure(op, value, lo, hi):
    if op == "clamp":
        return min(max(float(value), lo), hi)
    if op == "remap":
        return min(max((float(value) - lo) / max(hi - lo, 1e-9), 0.0), 1.0)
    if op == "lerp":
        a, b, t = value
        return a + (b - a) * min(max(t, 0), 1)
    if op == "sum":
        return sum(float(v) for v in value)
    if op == "mean":
        return (sum(float(v) for v in value) / len(value)) if value else None
    raise core.ToolError("INTERNAL", "Unknown operation.")


def h_unit_tests(d, cap, ctx):
    op = core.text(d, "operation", maximum=20)
    if op not in PURE_OPS:
        core.fail("INPUT_INVALID", "operation must be one of clamp, remap, lerp, sum, mean.", allowed=list(PURE_OPS))
    cases = core.array(d, "cases", required=True, kind=dict, maximum=500)
    lo = core.number(d, "minimum", default=0, minimum=-1e9, maximum=1e9)
    hi = core.number(d, "maximum", default=1, minimum=-1e9, maximum=1e9)
    results, passed = [], 0
    for idx, c in enumerate(cases):
        inp = c.get("input")
        exp = c.get("expected")
        try:
            got = _pure(op, inp, lo, hi)
            ok = got is not None and exp is not None and math.isclose(float(got), float(exp), rel_tol=1e-9, abs_tol=1e-9)
        except (TypeError, ValueError):
            got, ok = "error", False
        passed += ok
        results.append({"case": idx, "input": inp, "expected": exp, "actual": got, "passed": ok})
    failing = [r["case"] for r in results if not r["passed"]]
    harness = "\n".join(_header(f"Test harness for {op}") + ["local M = require(script.Parent.Module)", "local failures = 0"] +
                        [f"do local ok = math.abs(M.{op}({_lnum_list(c['input'], op)}) - {_lnum(float(c['expected']))}) < 1e-9 if not ok then failures += 1 end end"
                         for c in cases if c.get("expected") is not None] +
                        ["assert(failures == 0, \"unit test failures: \" .. failures)", ""])
    findings = [chk("unit_tests.all", not failing, f"All {len(cases)} case(s) pass." if not failing else f"{len(failing)} case(s) fail: {failing[:10]}", "fail"),
                chk("unit_tests.harness", True, "Harness generated; it runs inside Studio only when executed by the operator.")]
    return out({"cases": len(cases), "passed": passed, "failed": len(cases) - passed, "results": results}, findings, {"unit-test-harness.luau": harness})


def _lnum_list(value, op):
    if op == "lerp":
        a, b, t = value
        return f"{_lnum(a)}, {_lnum(b)}, {_lnum(t)}"
    if op in ("sum", "mean"):
        return "{ " + ", ".join(_lnum(v) for v in value) + " }"
    return _lnum(value)


def h_assertions(d, cap, ctx):
    name = _ident(core.text(d, "module_name", maximum=64), "module_name")
    inv = core.array(d, "invariants", required=True, kind=dict, maximum=100)
    ops = {">=": "<", "<=": ">", ">": "<=", "<": ">=", "==": "~=", "~=": "=="}
    lines = _header(f"{name}: development-only invariants") + ["local RunService = game:GetService(\"RunService\")", "local M = {}", "",
                                                             "function M.check(state: { [string]: any })", "\tif not RunService:IsStudio() then", "\t\treturn", "\tend"]
    rows = []
    for item in inv:
        iname = _ident(core.text(item, "name", maximum=64), "invariant.name")
        field = _ident(core.text(item, "field", maximum=64), "invariant.field")
        op = core.text(item, "op", maximum=4)
        if op not in ops:
            core.fail("INPUT_INVALID", "invariant.op must be one of >=, <=, >, <, ==, ~=.", invariant=iname)
        val = item.get("value")
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            core.fail("INPUT_INVALID", "invariant.value must be a finite number.", invariant=iname)
        lines.append(f"\t-- invariant: {iname}")
        lines.append(f"\tif type(state[\"{field}\"]) == \"number\" and state[\"{field}\"] {ops[op]} {_lnum(val)} then")
        lines.append(f"\t\terror(\"invariant {iname} violated\", 2)")
        lines.append("\tend")
        rows.append({"name": iname, "field": field, "op": op, "value": val})
    lines += ["end", "", "return M", ""]
    findings = [chk("assertions.scope", True, f"{len(rows)} invariant(s); checks run only in Studio."),
                chk("assertions.messages", True, "Messages name the invariant only; no internal state is printed.")]
    return out({"invariants": rows}, findings, {f"{name}.luau": "\n".join(lines)})


def h_strict_types(d, cap, ctx):
    tname = _ident(core.text(d, "type_name", maximum=64), "type_name")
    mname = _ident(core.text(d, "module_name", maximum=64), "module_name")
    fields = core.obj(d, "fields", required=True)
    rows = []
    for f, t in fields.items():
        _ident(f, "field")
        if t not in LUAU_TYPES:
            core.fail("INPUT_INVALID", "Unsupported field type.", field=f, allowed=sorted(LUAU_TYPES))
        rows.append((f, t))
    decl = ["export type " + tname + " = {"] + [f"\t{f}: {LUAU_TYPES[t]}," for f, t in rows] + ["}", ""]
    guards = " and ".join(GUARDS[t].format(f=f) for f, t in rows) or "true"
    lines = _header(f"{mname}: typed boundary for {tname}") + decl + ["local M = {}", "",
                                                                     f"function M.is{tname}(value: any): boolean", "\tif typeof(value) ~= \"table\" then", "\t\treturn false", "\tend",
                                                                     f"\treturn {guards}", "end", "", "return M", ""]
    findings = [chk("strict_types.fields", bool(rows), f"{len(rows)} typed field(s)." if rows else "No fields defined; the guard accepts any table."),
                chk("strict_types.guard", True, "Type guard checks every declared field.")]
    return out({"type": tname, "fields": dict(rows)}, findings, {f"{mname}.luau": "\n".join(lines)})


def h_codegen(d, cap, ctx):
    mname = _ident(core.text(d, "module_name", maximum=64), "module_name")
    server = core.boolean(d, "server_only", False)
    fields = core.obj(d, "fields", required=True)
    rows = []
    for f, t in fields.items():
        _ident(f, "field")
        if t not in LUAU_TYPES:
            core.fail("INPUT_INVALID", "Unsupported field type.", field=f, allowed=sorted(LUAU_TYPES))
        rows.append((f, t))
    guard = ["if not game:GetService(\"RunService\"):IsServer() then", f"\terror(\"{mname} is server-only\")", "end", ""] if server else []
    decl = ["export type Payload = {"] + [f"\t{f}: {LUAU_TYPES[t]}," for f, t in rows] + ["}", ""]
    check = " and ".join(GUARDS[t].format(f=f) for f, t in rows) or "true"
    lines = _header(f"{mname}: typed module and remote payload") + guard + decl + ["local M = {}", "", "function M.validate(value: any): boolean",
                                                                                 "\tif typeof(value) ~= \"table\" then", "\t\treturn false", "\tend", f"\treturn {check}", "end", "", "return M", ""]
    schema = {"module": mname, "server_only": server, "payload": {f: t for f, t in rows}, "rule": "Validate every incoming payload on the server before use."}
    findings = [chk("codegen.fields", bool(rows), f"{len(rows)} payload field(s)." if rows else "No payload fields; the validator accepts any table."),
                chk("codegen.server_only", server or None, "Server-only guard included." if server else "Module is shared; confirm it contains no secrets or server logic.")]
    return out({"module": mname, "fields": len(rows), "server_only": server}, findings, {f"{mname}.luau": "\n".join(lines), f"{mname}-schema.json": schema})


def h_module_api(d, cap, ctx):
    mname = _ident(core.text(d, "module_name", maximum=64), "module_name")
    deps = core.array(d, "dependencies", maximum=50, kind=str)
    server = core.boolean(d, "server_only", False)
    bad = [x for x in deps if not IDENT.match(x)]
    self_dep = mname in deps
    dupes = sorted({x for x in deps if deps.count(x) > 1})
    guard = ["if not game:GetService(\"RunService\"):IsServer() then", f"\terror(\"{mname} is server-only\")", "end", ""] if server else []
    lines = _header(f"{mname}: Init/Start/Destroy lifecycle") + guard + ["local M = {}", "local initialized = false", "local started = false", "local deps: { [string]: any } = {}", "",
                                                                       "function M.Init(dependencies: { [string]: any }?)",
                                                                       f"\tassert(not initialized, \"{mname} is already initialized\")",
                                                                       "\tdeps = dependencies or {}", "\tinitialized = true", "end", "",
                                                                       "function M.Start()", f"\tassert(initialized, \"{mname} must be initialized first\")", "\tstarted = true", "end", "",
                                                                       "function M.Destroy()", "\tstarted = false", "\tinitialized = false", "\ttable.clear(deps)", "end", "",
                                                                       "M.Dependencies = " + "{ " + ", ".join(_lstr(x) for x in deps) + " }", "", "return M", ""]
    findings = [chk("module_api.names", not bad, "Dependency names are identifiers." if not bad else f"Invalid dependency names: {bad}", "fail"),
                chk("module_api.self", not self_dep, "Module does not depend on itself." if not self_dep else "Module depends on itself.", "fail"),
                chk("module_api.duplicates", not dupes, "No duplicate dependencies." if not dupes else f"Duplicate dependencies: {dupes}", "warning"),
                chk("module_api.lifecycle", True, "Init must run before Start; Destroy resets state.")]
    return out({"module": mname, "dependencies": deps, "server_only": server}, findings, {f"{mname}.luau": "\n".join(lines)})


def h_luau_class(d, cap, ctx):
    cname = _ident(core.text(d, "class_name", maximum=64), "class_name")
    initial = core.obj(d, "initial")
    inits = []
    for f, v in initial.items():
        _ident(f, "field")
        if isinstance(v, bool):
            inits.append(f"\tself.{f} = {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            inits.append(f"\tself.{f} = {_lnum(v)}")
        elif isinstance(v, str):
            inits.append(f"\tself.{f} = {_lstr(v)}")
        else:
            core.fail("INPUT_INVALID", "initial values must be numbers, strings or booleans.", field=f)
    lines = list(_header(f"{cname}: composition-friendly class with scoped cleanup")) + [f"local {cname} = {{}}", f"{cname}.__index = {cname}", "",
                                                                                         f"function {cname}.new()", f"\tlocal self = setmetatable({{}}, {cname})", "\tself._connections = {}"] + inits + \
        ["\treturn self", "end", "", f"function {cname}:Track(connection: RBXScriptConnection)", "\ttable.insert(self._connections, connection)", "\treturn connection", "end", "",
         f"function {cname}:Destroy()", "\tfor _, connection in self._connections do", "\t\tconnection:Disconnect()", "\tend", "\ttable.clear(self._connections)", "end", "",
         f"return {cname}", ""]
    findings = [chk("luau_class.fields", True, f"{len(initial)} initial field(s)."),
                chk("luau_class.cleanup", True, "Destroy disconnects every tracked connection.")]
    return out({"class": cname, "fields": sorted(initial)}, findings, {f"{cname}.luau": "\n".join(lines)})


def h_functional(d, cap, ctx):
    op = core.text(d, "operation", maximum=10)
    if op not in ("map", "filter", "reduce"):
        core.fail("INPUT_INVALID", "operation must be map, filter or reduce.")
    values = core.array(d, "values", required=True, maximum=10000)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        core.fail("INPUT_INVALID", "values must be finite numbers.")
    m = core.number(d, "multiplier", default=1, minimum=-1e6, maximum=1e6)
    values = [float(v) for v in values]
    if op == "map":
        result = [v * m for v in values]
        luau = ["local function run(values: { number }): { number }", "\tlocal out = {}", "\tfor i, v in values do", f"\t\tout[i] = v * {_lnum(m)}", "\tend", "\treturn table.freeze(out)", "end"]
    elif op == "filter":
        result = [v for v in values if v > m]
        luau = ["local function run(values: { number }): { number }", "\tlocal out = {}", "\tfor _, v in values do", f"\t\tif v > {_lnum(m)} then", "\t\t\ttable.insert(out, v)", "\t\tend", "\tend", "\treturn table.freeze(out)", "end"]
    else:
        result = sum(values) * m
        luau = ["local function run(values: { number }): number", "\tlocal total = 0", "\tfor _, v in values do", "\t\ttotal += v", "\tend", f"\treturn total * {_lnum(m)}", "end"]
    lines = _header(f"Immutable {op} pipeline") + ["local Pipeline = {}", ""] + luau + ["", "Pipeline.run = run", "", "return Pipeline", ""]
    findings = [chk("functional.semantics", True, f"{op} uses multiplier {m}; filter keeps values above the multiplier, reduce returns sum times multiplier."),
                chk("functional.immutable", True, "Outputs are frozen with table.freeze; inputs are not modified.")]
    return out({"operation": op, "result": result if op != "reduce" else round(result, 9), "count": len(result) if op != "reduce" else len(values)}, findings,
               {f"{op}-pipeline.luau": "\n".join(lines)})


def h_async(d, cap, ctx):
    timeout = core.number(d, "timeout_seconds", minimum=0.01, maximum=300)
    retries = core.integer(d, "retries", minimum=0, maximum=10)
    backoff = core.number(d, "backoff_seconds", minimum=0, maximum=60)
    schedule, t = [], 0.0
    for attempt in range(retries + 1):
        start = t
        t += timeout
        schedule.append({"attempt": attempt, "starts_at": round(start, 6), "times_out_at": round(start + timeout, 6)})
        if attempt < retries:
            wait = backoff * (2 ** attempt)
            t += wait
            schedule[-1]["backoff_after"] = round(wait, 6)
    worst = round(t, 6)
    lines = _header("Cancellable retry with timeout and exponential backoff") + [
        "local function withRetry(fn: () -> any, cancel: { cancelled: boolean }?): (boolean, any)",
        "\tlocal lastError: any = nil",
        f"\tfor attempt = 0, {retries} do",
        "\t\tif cancel and cancel.cancelled then",
        "\t\t\treturn false, \"cancelled\"",
        "\t\tend",
        "\t\tlocal done, ok, result = false, false, nil",
        "\t\ttask.spawn(function()",
        "\t\t\tok, result = pcall(fn)",
        "\t\t\tdone = true",
        "\t\tend)",
        "\t\tlocal started = os.clock()",
        f"\t\twhile not done and os.clock() - started < {_lnum(timeout)} and not (cancel and cancel.cancelled) do",
        "\t\t\ttask.wait()",
        "\t\tend",
        "\t\tif done and ok then",
        "\t\t\treturn true, result",
        "\t\tend",
        "\t\tlastError = if done then result else \"timeout\"",
        f"\t\tif attempt < {retries} then",
        f"\t\t\ttask.wait({_lnum(backoff)} * 2 ^ attempt)",
        "\t\tend",
        "\tend",
        "\treturn false, lastError",
        "end", "", "return withRetry", ""]
    findings = [chk("async.bounded", worst < 3600, f"Worst-case duration {worst} seconds." if worst < 3600 else f"Worst-case duration {worst} seconds exceeds one hour.", "warning"),
                chk("async.cancel", True, "The wrapper checks a cancel flag before each attempt and while waiting.")]
    return out({"attempts": retries + 1, "worst_case_seconds": worst, "schedule": schedule}, findings, {"async-retry.luau": "\n".join(lines)})


def h_signal(d, cap, ctx):
    listeners = core.integer(d, "listeners", minimum=0, maximum=10000)
    events = core.integer(d, "events", minimum=0, maximum=100000)
    disconnect_after = core.integer(d, "disconnect_after", minimum=0, maximum=100000)
    deliveries = events * listeners
    if deliveries > ctx.config["limits"]["simulation_steps"] * 100:
        core.fail("INPUT_LIMIT", "Simulation exceeds the event delivery limit.", deliveries=deliveries)
    delivered = 0
    counts = [0] * listeners
    connected = [True] * listeners
    for _ in range(events):
        for i in range(listeners):
            if connected[i]:
                delivered += 1
                counts[i] += 1
                if disconnect_after and counts[i] >= disconnect_after:
                    connected[i] = False
    live = sum(connected)
    leaked = sum(1 for i in range(listeners) if connected[i] and not disconnect_after)
    lines = _header("Scoped signal bus") + ["local Signal = {}", "Signal.__index = Signal", "",
                                           "function Signal.new()", "\treturn setmetatable({ _handlers = {}, _nextId = 0 }, Signal)", "end", "",
                                           "function Signal:Connect(handler: (...any) -> ())",
                                           "\tself._nextId += 1", "\tlocal id = self._nextId", "\tself._handlers[id] = handler",
                                           "\treturn { Disconnect = function() self._handlers[id] = nil end }", "end", "",
                                           "function Signal:Fire(...: any)", "\tfor _, handler in table.clone(self._handlers) do", "\t\thandler(...)", "\tend", "end", "",
                                           "return Signal", ""]
    findings = [chk("signal.delivery", True, f"{delivered} delivery(ies) simulated across {listeners} listener(s)."),
                chk("signal.leaks", leaked == 0 if listeners else True, "No listener stays connected without a disconnect rule." if leaked == 0 else f"{leaked} listener(s) never disconnect; store and disconnect them.", "warning")]
    return out({"deliveries": delivered, "live_connections": live, "never_disconnecting": leaked}, findings, {"signal-bus.luau": "\n".join(lines)})


def h_examples(d, cap, ctx):
    concept = core.text(d, "concept", maximum=120)
    rate = core.number(d, "rate", minimum=0.01, maximum=1000)
    burst = core.integer(d, "burst", minimum=1, maximum=1000)
    safe_concept = " ".join(concept.split())
    steps = 10 * 2 * int(math.ceil(rate))
    if steps > ctx.config["limits"]["simulation_steps"]:
        core.fail("INPUT_LIMIT", "Example simulation exceeds the step limit.", steps=steps)
    tokens, last, allowed, dt = float(burst), 0.0, 0, 1.0 / (2 * rate)
    t = 0.0
    total = steps
    for _ in range(total):
        tokens = min(float(burst), tokens + (t - last) * rate)
        last = t
        if tokens >= 1:
            tokens -= 1
            allowed += 1
        t += dt
    lines = list(_header(f"Example: {safe_concept}")) + ["local TokenBucket = {}", "TokenBucket.__index = TokenBucket", "",
                                                         f"function TokenBucket.new(rate: number, burst: number)",
                                                         "\treturn setmetatable({ rate = rate, burst = burst, tokens = burst, last = os.clock() }, TokenBucket)", "end", "",
                                                         "function TokenBucket:TryTake(): boolean",
                                                         "\tlocal now = os.clock()", "\tself.tokens = math.min(self.burst, self.tokens + (now - self.last) * self.rate)", "\tself.last = now",
                                                         "\tif self.tokens >= 1 then", "\t\tself.tokens -= 1", "\t\treturn true", "\tend", "\treturn false", "end", "",
                                                         f"return TokenBucket -- example: TokenBucket.new({_lnum(rate)}, {burst})", ""]
    findings = [chk("examples.simulation", True, f"Simulated {total} request(s) at {2 * rate}/s; {allowed} allowed."),
                chk("examples.concept", len(safe_concept) >= 3, "Concept is named in the example header." if len(safe_concept) >= 3 else "Give the concept a descriptive name.", "info")]
    return out({"concept": safe_concept, "requests": total, "allowed": allowed, "burst": burst, "rate_per_second": rate}, findings, {"example-token-bucket.luau": "\n".join(lines)})


def h_lifecycle(d, cap, ctx):
    cname = _ident(core.text(d, "class_name", maximum=64), "class_name")
    pool = core.integer(d, "pool_size", minimum=0, maximum=10000)
    ttl = core.number(d, "ttl_seconds", minimum=0, maximum=86400)
    lines = _header(f"{cname} pool with scoped teardown") + [f"local {cname}Pool = {{}}", f"{cname}Pool.__index = {cname}Pool", "",
                                                             f"function {cname}Pool.new(size: number)",
                                                             "\tlocal self = setmetatable({ free = {}, active = {} }, " + f"{cname}Pool)",
                                                             "\tfor _ = 1, size do", f"\t\ttable.insert(self.free, Instance.new(\"{cname}\"))", "\tend", "\treturn self", "end", "",
                                                             f"function {cname}Pool:Acquire(): Instance",
                                                             "\tlocal item = table.remove(self.free) or Instance.new(\"" + cname + "\")",
                                                             "\tself.active[item] = os.clock()", "\treturn item", "end", "",
                                                             f"function {cname}Pool:Release(item: Instance)",
                                                             "\tif self.active[item] == nil then return end", "\tself.active[item] = nil",
                                                             "\titem.Parent = nil", "\ttable.insert(self.free, item)", "end", "",
                                                             f"function {cname}Pool:Teardown()",
                                                             "\tfor _, item in self.free do item:Destroy() end",
                                                             "\tfor item in self.active do item:Destroy() end",
                                                             "\ttable.clear(self.free)", "\ttable.clear(self.active)", "end", "",
                                                             f"function {cname}Pool:ReapIdle(ttl: number)", "\tlocal now = os.clock()",
                                                             "\tfor item, since in self.active do",
                                                             "\t\tif now - since >= ttl then self:Release(item) end",
                                                             "\tend", "end", "",
                                                             f"return {cname}Pool", ""]
    findings = [chk("lifecycle.pool_size", pool <= 500, f"Pool pre-creates {pool} instance(s)." if pool <= 500 else f"Pool of {pool} pre-created instances is large; measure memory.", "warning"),
                chk("lifecycle.ttl", ttl <= 3600, "Idle reaping interval is set." if ttl > 0 else "No ttl: idle instances are kept until Teardown.", "info"),
                chk("lifecycle.teardown", True, "Teardown destroys every pooled and active instance.")]
    return out({"class": cname, "pool_size": pool, "ttl_seconds": ttl}, findings, {f"{cname}-pool.luau": "\n".join(lines)})


def h_factory(d, cap, ctx):
    cname = _ident(core.text(d, "class_name", maximum=64), "class_name")
    defaults = core.obj(d, "defaults")
    maximum = core.integer(d, "maximum", minimum=1, maximum=10000)
    rows, problems = [], []
    for p, v in defaults.items():
        kind = PROP_KINDS.get(p)
        if kind is None:
            problems.append(f"{p}: property not in the validated set")
            continue
        ok = (kind == "boolean" and isinstance(v, bool)) or (kind == "string" and isinstance(v, str)) or \
             (kind == "unit" and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1) or \
             (kind == "Vector3" and isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)) or \
             (kind == "Color3" and isinstance(v, str) and bool(re.fullmatch(r"#[0-9a-fA-F]{6}", v)))
        if not ok:
            problems.append(f"{p}: value does not match {kind}")
        rows.append((p, kind, v))
    if problems:
        core.fail("INPUT_INVALID", "Factory defaults are invalid.", problems=problems)
    default_lines = []
    for p, kind, v in rows:
        if kind == "boolean":
            lit = "true" if v else "false"
        elif kind == "string":
            lit = _lstr(v)
        elif kind == "unit":
            lit = _lnum(v)
        elif kind == "Vector3":
            lit = f"Vector3.new({v[0]}, {v[1]}, {v[2]})"
        else:
            lit = f"Color3.fromHex({_lstr(v)})"
        default_lines.append(f"\t{p} = {lit},")
    lines = _header(f"{cname} factory with bounded creation") + [f"local {cname}Factory = {{}}", "", "local live = 0", f"local maximum = {maximum}", "",
                                                                 "local defaults = {"] + default_lines + ["}", "",
                                                                 f"function {cname}Factory.create(overrides: {{ [string]: any }}?): Instance",
                                                                 "\tif live >= maximum then", f"\t\terror(\"{cname} limit reached\")", "\tend",
                                                                 f"\tlocal instance = Instance.new(\"{cname}\")",
                                                                 "\tfor k, v in defaults do", "\t\t(instance :: any)[k] = v", "\tend",
                                                                 "\tfor k, v in (overrides or {}) do", "\t\tif defaults[k] == nil then error(\"unknown property \" .. tostring(k)) end",
                                                                 "\t\t(instance :: any)[k] = v", "\tend",
                                                                 "\tlive += 1", "\treturn instance", "end", "",
                                                                 f"function {cname}Factory.destroy(instance: Instance)", "\tlive = math.max(0, live - 1)", "\tinstance:Destroy()", "end", "",
                                                                 f"return {cname}Factory", ""]
    findings = [chk("factory.defaults", True, f"{len(rows)} validated default(s)."),
                chk("factory.limit", True, f"Creation stops at {maximum} live instances; destroy releases a slot.")]
    return out({"class": cname, "defaults": len(rows), "maximum": maximum}, findings, {f"{cname}-factory.luau": "\n".join(lines)})


def _lit(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _lstr(value)
    return _lnum(value)


def h_plugin(d, cap, ctx):
    pname = core.text(d, "plugin_name", maximum=64)
    if not re.match(r"^[A-Za-z][A-Za-z0-9 _-]{0,63}$", pname):
        core.fail("INPUT_INVALID", "plugin_name must be a short ASCII name.")
    ops = core.array(d, "operations", required=True, kind=dict, maximum=20)
    confirm = core.boolean(d, "require_confirmation", True)
    allowed_kinds = {"set_property", "add_tag", "remove_tag", "set_attribute", "destroy"}
    rows, errors, body = [], [], []
    for o in ops:
        kind = core.text(o, "kind", maximum=30)
        if kind not in allowed_kinds:
            errors.append(f"{kind}: not an edit-only operation")
            continue
        if kind == "destroy":
            if not confirm:
                errors.append("destroy requires require_confirmation")
            body.append("\t\tinst:Destroy()")
            rows.append({"kind": kind})
        elif kind == "set_property":
            prop = core.text(o, "property", maximum=40)
            if prop not in PROP_KINDS:
                errors.append(f"set_property: {prop} is not in the validated property set")
                continue
            value = o.get("value")
            expected = PROP_KINDS[prop]
            ok = (expected == "boolean" and isinstance(value, bool)) or (expected == "string" and isinstance(value, str)) or \
                 (expected == "unit" and isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1) or \
                 (expected == "Vector3" and isinstance(value, list) and len(value) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)) or \
                 (expected == "Color3" and isinstance(value, str) and bool(re.fullmatch(r"#[0-9a-fA-F]{6}", value)))
            if not ok:
                errors.append(f"set_property: value for {prop} does not match {expected}")
                continue
            if expected == "Vector3":
                lit = f"Vector3.new({value[0]}, {value[1]}, {value[2]})"
            elif expected == "Color3":
                lit = f"Color3.fromHex({_lstr(value)})"
            else:
                lit = _lit(value)
            body.append(f"\t\tlocal okSet, setErr = pcall(function() (inst :: any).{prop} = {lit} end)")
            body.append(f"\t\tif not okSet then warn(\"{prop} not set on \" .. inst:GetFullName() .. \": \" .. tostring(setErr)) end")
            rows.append({"kind": kind, "property": prop})
        elif kind in ("add_tag", "remove_tag"):
            tag = core.text(o, "tag", maximum=64)
            if not TAG_RE_CODE.match(tag):
                errors.append(f"{kind}: tag must be an identifier")
                continue
            fn = "AddTag" if kind == "add_tag" else "RemoveTag"
            body.append(f"\t\tCollectionService:{fn}(inst, {_lstr(tag)})")
            rows.append({"kind": kind, "tag": tag})
        elif kind == "set_attribute":
            attr = core.text(o, "attribute", maximum=64)
            if not TAG_RE_CODE.match(attr):
                errors.append("set_attribute: attribute must be an identifier")
                continue
            value = o.get("value")
            if not (isinstance(value, (str, bool)) or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))):
                errors.append(f"set_attribute: {attr} value must be a string, number or boolean")
                continue
            body.append(f"\t\tinst:SetAttribute({_lstr(attr)}, {_lit(value)})")
            rows.append({"kind": kind, "attribute": attr})
    if errors:
        core.fail("POLICY_DENIED", "Plugin operations violate edit-only policy or validation.", problems=errors)
    if not body:
        core.fail("INPUT_INVALID", "At least one valid operation is required.")
    name_lit = _lstr(pname)
    lines = _header(f"{pname}: edit-only Studio plugin") + [
        "local ChangeHistoryService = game:GetService(\"ChangeHistoryService\")",
        "local Selection = game:GetService(\"Selection\")",
        "local CollectionService = game:GetService(\"CollectionService\")", "",
        f"local toolbar = plugin:CreateToolbar({name_lit})",
        f"local button = toolbar:CreateButton({name_lit}, {_lstr('Run the configured edits on the selection')}, \"\")", "",
        "local MAX_SELECTION = 500",
        "local armedUntil = 0", "",
        "local function run()",
        "\tlocal selected = Selection:Get()",
        "\tif #selected > MAX_SELECTION then",
        "\t\twarn(\"Selection exceeds \" .. MAX_SELECTION .. \" objects; nothing changed.\")",
        "\t\treturn",
        "\tend",
        f"\tChangeHistoryService:SetWaypoint({_lstr('Before ' + pname)})",
        "\tfor _, inst in selected do",
    ] + body + [
        "\tend",
        f"\tChangeHistoryService:SetWaypoint({_lstr('After ' + pname)})",
        "end", "",
    ]
    if confirm:
        lines += [
            "button.Click:Connect(function()",
            "\tif os.clock() < armedUntil then",
            "\t\tarmedUntil = 0",
            "\t\trun()",
            "\telse",
            "\t\tarmedUntil = os.clock() + 5",
            "\t\twarn(\"Click again within 5 seconds to confirm.\")",
            "\tend",
            "end)", "",
        ]
    else:
        lines += ["button.Click:Connect(run)", ""]
    text = "\n".join(lines)
    destructive = any(r["kind"] == "destroy" for r in rows)
    findings = [chk("plugin.edit_only", True, "Only edit operations are generated; no HTTP, DataStore or network calls."),
                chk("plugin.confirmation", confirm or not destructive, "Two-click confirmation is generated." if confirm else "Confirmation is disabled; no destructive operation is present.", "fail" if not confirm and destructive else "info"),
                chk("plugin.history", True, "Each run is wrapped in ChangeHistoryService waypoints for undo.")]
    return out({"plugin": pname, "operations": rows, "confirmation": confirm}, findings, {f"{pname.replace(' ', '')}-plugin.luau": text})


TAG_RE_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def h_inspector(d, cap, ctx):
    title = core.text(d, "title", maximum=80)
    fields = core.array(d, "fields", required=True, kind=dict, maximum=100)
    rows = []
    for f in fields:
        name = core.text(f, "attribute", maximum=64)
        _ident(name, "attribute")
        typ = core.text(f, "type", maximum=10)
        if typ not in ("number", "string", "boolean"):
            core.fail("INPUT_INVALID", "inspector field type must be number, string or boolean.", attribute=name)
        lo = f.get("min")
        hi = f.get("max")
        if typ != "number" and (lo is not None or hi is not None):
            core.fail("INPUT_INVALID", "min and max apply only to number fields.", attribute=name)
        rows.append({"attribute": name, "type": typ, "required": bool(f.get("required", False)), "min": lo, "max": hi})
    sample = d.get("sample", {})
    if not isinstance(sample, dict):
        core.fail("INPUT_INVALID", "sample must be an object.")
    issues = []
    for r in rows:
        if r["attribute"] not in sample:
            if r["required"]:
                issues.append(f"{r['attribute']}: required but missing from sample")
            continue
        v = sample[r["attribute"]]
        if r["type"] == "number":
            ok = isinstance(v, (int, float)) and not isinstance(v, bool)
            if ok and r["min"] is not None and v < r["min"]:
                issues.append(f"{r['attribute']}: below min {r['min']}")
            if ok and r["max"] is not None and v > r["max"]:
                issues.append(f"{r['attribute']}: above max {r['max']}")
        elif r["type"] == "string":
            ok = isinstance(v, str)
        else:
            ok = isinstance(v, bool)
        if not ok:
            issues.append(f"{r['attribute']}: expected {r['type']}")
    safe_title = " ".join(title.split())
    lines = _header(f"{safe_title}: attribute inspector configuration") + ["local Config = {}", "", "Config.fields = {"] + [
        f"\t{{ attribute = {_lstr(r['attribute'])}, type = {_lstr(r['type'])}, required = {'true' if r['required'] else 'false'}" +
        (f", min = {_lnum(r['min'])}" if r["min"] is not None else "") + (f", max = {_lnum(r['max'])}" if r["max"] is not None else "") + " }," for r in rows] + [
        "}", "", "function Config.validate(attributes: { [string]: any }): (boolean, { string })",
        "\tlocal problems = {}", "\tfor _, field in Config.fields do",
        "\t\tlocal v = attributes[field.attribute]",
        "\t\tif v == nil then",
        "\t\t\tif field.required then table.insert(problems, field.attribute .. \" is required\") end",
        "\t\telseif typeof(v) ~= field.type then",
        "\t\t\ttable.insert(problems, field.attribute .. \" must be \" .. field.type)",
        "\t\telseif field.type == \"number\" and ((field.min and v < field.min) or (field.max and v > field.max)) then",
        "\t\t\ttable.insert(problems, field.attribute .. \" is out of range\")",
        "\t\tend", "\tend", "\treturn #problems == 0, problems", "end", "", "return Config", ""]
    findings = [chk("inspector.sample", not issues, "Sample attributes match the schema." if not issues else f"{len(issues)} sample problem(s): " + "; ".join(issues[:5]), "fail" if issues else "info"),
                chk("inspector.schema", True, f"{len(rows)} attribute(s) in the inspector schema.")]
    return out({"title": safe_title, "fields": len(rows), "sample_problems": issues}, findings,
               {"inspector-config.json": {"title": safe_title, "fields": rows}, "inspector-config.luau": "\n".join(lines)})


def h_conventions(d, cap, ctx):
    files = core.array(d, "files", required=True, kind=dict, maximum=2000)
    max_lines = core.integer(d, "max_lines", default=400, minimum=10, maximum=100000)
    name_rx = re.compile(r"^[A-Z][A-Za-z0-9]*(?:\.(?:server|client))?\.(?:lua|luau)$")
    rows = []
    for f in files:
        path = core.text(f, "path", maximum=300)
        kind = core.text(f, "kind", maximum=10)
        lines = core.integer(f, "lines", minimum=0, maximum=10000000)
        base = path.rsplit("/", 1)[-1]
        problems = []
        if not name_rx.match(base):
            problems.append("name must be PascalCase with .lua/.luau and optional .server/.client")
        if kind == "server" and ".server." not in base:
            problems.append("server script must use .server.luau")
        if kind == "client" and ".client." not in base:
            problems.append("client script must use .client.luau")
        if kind == "module" and re.search(r"\.(server|client)\.", base):
            problems.append("module must not use .server or .client")
        if kind not in ("module", "server", "client"):
            problems.append("kind must be module, server or client")
        if lines > max_lines:
            problems.append(f"{lines} lines exceeds {max_lines}")
        if ".." in path or path.startswith("/"):
            problems.append("path must be relative without traversal")
        rows.append({"path": path, "problems": problems})
    bad = [r for r in rows if r["problems"]]
    findings = [chk("conventions.files", not bad, "All files follow the conventions." if not bad else f"{len(bad)} file(s) break conventions.", "fail" if bad else "info"),
                chk("conventions.size", all(not any("exceeds" in p for p in r["problems"]) for r in rows), f"Files are at most {max_lines} lines.", "warning")]
    return out({"files": len(rows), "violations": len(bad), "rows": rows}, findings)


# ---------------------------------------------------------------- registry

HANDLERS = {
    "factory": h_factory, "lifecycle": h_lifecycle, "luau_module": h_luau_module, "strict_types": h_strict_types,
    "module_api": h_module_api, "luau_class": h_luau_class, "functional": h_functional, "async": h_async,
    "signal": h_signal, "refactor": h_refactor, "remote_risk": h_remote_risk, "authority": h_authority,
    "remote_audit": h_remote_audit, "secrets": h_secrets, "script_audit": h_script_audit, "unit_tests": h_unit_tests,
    "assertions": h_assertions, "plugin": h_plugin, "inspector": h_inspector, "codegen": h_codegen, "merge": h_merge,
    "code_review": h_code_review, "conventions": h_conventions, "examples": h_examples,
}
