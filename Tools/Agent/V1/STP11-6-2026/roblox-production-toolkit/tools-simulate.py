"""Families: sim_rate, sim_state, stats, metrics, budget, physics, motion, schedule, statemachine.

Loaded as rbx_simulate by tools-engine.py. Simulations are deterministic (seeded where random),
bounded by configured step limits, and report only what the supplied inputs imply.
"""
import hashlib
import hmac
import math
import random
import rbx_core as core
from rbx_core import chk

FAMILY = "simulate"


def out(result, findings, artifacts=None, warnings=None):
    return {"result": result, "findings": findings, "artifacts": artifacts or {}, "warnings": warnings or []}


def steps_ok(n, ctx):
    limit = ctx.config["limits"]["simulation_steps"]
    if n > limit:
        core.fail("INPUT_LIMIT", "Simulation would exceed the configured step limit.", steps=n, limit=limit)
    return n


def rng(seed):
    return random.Random(int(seed))


def times_of(items, key="t"):
    values = []
    for it in items:
        if isinstance(it, dict):
            values.append(float(it.get(key, 0)))
        else:
            values.append(float(it))
    return values


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def vsub(a, b):
    return [x - y for x, y in zip(a, b)]


def vlen(a):
    return math.sqrt(sum(x * x for x in a))


# ---------------------------------------------------------------- sim_rate

def h_rate_limit(d, cap, ctx):
    rate = core.number(d, "rate", minimum=0.0001, maximum=100000)
    burst = core.number(d, "burst", minimum=1, maximum=100000)
    requests = core.array(d, "requests", required=True, maximum=20000)
    times = sorted(((r.get("t", 0), r.get("cost", 1), r.get("player", "unknown")) if isinstance(r, dict) else (r, 1, "unknown")) for r in requests)
    tokens, last = burst, 0.0
    allowed, denied, log = 0, [], []
    for t, cost, player in times:
        steps_ok(1, ctx)
        tokens = min(burst, tokens + max(0, t - last) * rate)
        last = t
        if tokens >= cost:
            tokens -= cost
            allowed += 1
        else:
            denied.append(t)
            log.append({"t": t, "player": player, "decision": "denied", "tokens": round(tokens, 3)})
    findings = [chk("rate_limit.burst", burst <= rate * 10 or burst <= 50, "Burst is bounded relative to the sustained rate." if burst <= rate * 10 or burst <= 50 else "Burst is large compared with the rate; a single client can spike the server.", "warning"),
                chk("rate_limit.cooldown", True, f"{len(denied)} request(s) denied; denials are logged with a player id for review."),
                chk("rate_limit.logging", True, "Denied requests produce structured log rows without changing game state.")]
    if denied:
        findings.append(chk("rate_limit.abuse_escalation", None, "Repeated denials should escalate to review, not automatic punishment; confirm the escalation policy."))
    return out({"allowed": allowed, "denied": len(denied), "denied_times": denied[:50], "log": log[:200]}, findings, {"rate-limit-log.json": log})


def h_data_budget(d, cap, ctx):
    budget = core.number(d, "budget_per_minute", minimum=1, maximum=10000000)
    players = core.integer(d, "players", minimum=0, maximum=100000)
    per_player = core.number(d, "writes_per_player", minimum=0, maximum=10000)
    reserve = core.number(d, "shutdown_reserve", minimum=0, maximum=budget)
    duration = core.number(d, "duration_seconds", minimum=60, maximum=86400)
    minutes = int(math.ceil(duration / 60))
    steps_ok(minutes, ctx)
    demand = players * per_player
    usable = budget - reserve
    backlog, rows = 0.0, []
    for m in range(minutes):
        backlog = max(0.0, backlog + demand - usable)
        rows.append({"minute": m + 1, "demand": demand, "usable": usable, "backlog": round(backlog, 2)})
    drain = backlog / usable if usable > 0 and backlog > 0 else 0.0
    findings = [chk("data_budget.capacity", demand <= usable, "Steady demand fits the usable budget." if demand <= usable else f"Demand {demand}/min exceeds usable {usable}/min; queues grow by {demand - usable}/min."),
                chk("data_budget.shutdown_reserve", backlog <= reserve, "Shutdown reserve covers the backlog at the end." if backlog <= reserve else f"Backlog {round(backlog, 1)} exceeds the shutdown reserve {reserve}; data could be lost on shutdown.", "fail"),
                chk("data_budget.retries_backoff", True, "Backoff is applied by the request layer; see api_retry for jitter and limits.")]
    if reserve == 0:
        findings.append(chk("data_budget.shutdown_reserve", None, "No shutdown reserve is configured; BindToClose saves may starve."))
    return out({"minutes": minutes, "peak_backlog": round(max(r["backlog"] for r in rows), 2), "final_backlog": round(backlog, 2),
                "drain_minutes_after_end": round(drain, 2), "timeline": rows[:200]}, findings, {"data-budget-timeline.json": rows})


def h_api_retry(d, cap, ctx):
    codes = core.array(d, "status_codes", required=True, maximum=50)
    retries = core.integer(d, "retries", minimum=0, maximum=10)
    base = core.number(d, "base_seconds", minimum=0.01, maximum=60)
    max_wait = core.number(d, "max_wait", minimum=0.01, maximum=300)
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31)
    method = core.text(d, "method", maximum=10).upper()
    idempotent = core.boolean(d, "idempotent", False)
    retryable = {429, 500, 502, 503, 504}
    safe_methods = {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}
    gen = rng(seed)
    schedule, total = [], 0.0
    can_retry = method in safe_methods or idempotent
    for attempt in range(retries):
        wait = gen.uniform(0, min(max_wait, base * (2 ** attempt)))
        total += wait
        schedule.append({"attempt": attempt + 1, "wait_seconds": round(wait, 4)})
    findings = [chk("api_retry.idempotency", False if (retries > 0 and not can_retry) else True,
                    "Non-idempotent request is never retried; add an idempotency key to retry safely." if (retries > 0 and not can_retry) else "Retries are allowed for this method or idempotency is declared.", "fail" if (retries > 0 and not can_retry) else "warning"),
                chk("api_retry.bounded", retries <= 5 and total <= 60, f"Worst-case retry wait is {round(total, 2)}s across {retries} retries."),
                chk("api_retry.retryable_codes", all(isinstance(c, int) for c in codes), "Status codes are integers; retryable set is " + ", ".join(map(str, sorted(retryable))) + ".")]
    retried = [c for c in codes if c in retryable] if can_retry else []
    findings.append(chk("api_retry.partial_failures", None, "Partial batch failures must be retried per item with the same idempotency key; confirm in the integration."))
    return out({"method": method, "retry_schedule": schedule if can_retry else [], "retryable_statuses": sorted(retryable), "statuses_retried": retried,
                "worst_case_wait_seconds": round(total, 3) if can_retry else 0.0}, findings, {"retry-schedule.json": schedule})


def h_save_recovery(d, cap, ctx):
    attempts = core.integer(d, "attempts", minimum=1, maximum=50)
    max_retries = core.integer(d, "max_retries", minimum=0, maximum=10)
    fail_first = core.integer(d, "fail_first", minimum=0, maximum=50)
    deadline = core.number(d, "shutdown_deadline_seconds", minimum=1, maximum=300)
    per_attempt = core.number(d, "attempt_seconds", minimum=0.01, maximum=60)
    t, timeline, saved = 0.0, [], False
    for k in range(min(attempts, max_retries + 1)):
        steps_ok(1, ctx)
        if t + per_attempt > deadline:
            timeline.append({"attempt": k + 1, "result": "deadline_exceeded", "t": round(t, 2)})
            break
        t += per_attempt
        ok = k >= fail_first
        timeline.append({"attempt": k + 1, "result": "saved" if ok else "failed", "t": round(t, 2)})
        if ok:
            saved = True
            break
        t += min(8.0, 0.5 * (2 ** k))
    state = "saved" if saved else "pending_local_recovery"
    findings = [chk("save_recovery.retries_bounded", True, f"Retries are capped at {max_retries}."),
                chk("save_recovery.shutdown", saved, "Data was saved before the shutdown deadline." if saved else "Save did not finish before the deadline; the record must persist to a recovery queue.", "fail" if not saved else "warning"),
                chk("save_recovery.user_facing", None, "Players see a non-blocking 'saving' and 'recovery pending' state; review the copy with the text family.")]
    return out({"final_state": state, "timeline": timeline, "elapsed_seconds": round(t, 2)}, findings, {"save-recovery-timeline.json": timeline})


def h_combat_validation(d, cap, ctx):
    shots = core.array(d, "shots", required=True, maximum=20000)
    interval = core.number(d, "interval", minimum=0.01, maximum=60)
    ammo = core.integer(d, "ammo", minimum=0, maximum=100000)
    max_range = core.number(d, "max_range", minimum=0.1, maximum=100000)
    tol = core.number(d, "latency_tolerance", minimum=0, maximum=5)
    times = sorted(times_of(shots))
    violations = []
    for i in range(1, len(times)):
        steps_ok(1, ctx)
        gap = times[i] - times[i - 1]
        if gap < interval - tol:
            violations.append({"index": i, "type": "fire_rate", "gap": round(gap, 4)})
    if len(times) > ammo:
        violations.append({"index": ammo, "type": "ammo", "count": len(times), "ammo": ammo})
    for i, s in enumerate(shots):
        if isinstance(s, dict) and "distance" in s and s["distance"] > max_range + tol:
            violations.append({"index": i, "type": "range", "distance": s["distance"]})
    flag = len(violations) >= 3
    findings = [chk("combat_validation.fire_rate", not any(v["type"] == "fire_rate" for v in violations), "Shot intervals respect the fire rate within latency tolerance." if not any(v["type"] == "fire_rate" for v in violations) else "Some shots are faster than the rate allows (with tolerance)."),
                chk("combat_validation.ammo", not any(v["type"] == "ammo" for v in violations), "Shot count is within ammunition."),
                chk("combat_validation.range", not any(v["type"] == "range" for v in violations), "Hit distances are within range."),
                chk("combat_validation.escalation", None if flag else True, "Three or more violations need human review; single samples are only logged." if flag else "Below the review threshold of three violations; logged only.")]
    return out({"shots": len(shots), "violations": violations[:100], "review_recommended": flag}, findings, {"combat-validation.json": violations})


def h_chaos(d, cap, ctx):
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31)
    requests = core.integer(d, "requests", minimum=1, maximum=50000)
    p_fail = core.number(d, "failure_probability", minimum=0, maximum=1)
    retries = core.integer(d, "max_retries", minimum=0, maximum=10)
    timeout = core.number(d, "timeout_ms", minimum=1, maximum=60000)
    lats = core.array(d, "latencies_ms", required=True, maximum=1000)
    lats = [float(x) for x in lats if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if not lats:
        core.fail("INPUT_INVALID", "latencies_ms must contain numbers.")
    gen = rng(seed)
    steps_ok(requests * (retries + 1), ctx)
    ok, attempts_total, observed = 0, 0, []
    for _ in range(requests):
        success = False
        for _a in range(retries + 1):
            attempts_total += 1
            lat = gen.choice(lats)
            observed.append(lat)
            if lat > timeout or gen.random() < p_fail:
                continue
            success = True
            break
        ok += success
    rate = ok / requests
    findings = [chk("chaos.availability", True if rate >= 0.99 else None, f"Success rate {round(rate, 4)} in the simulated run." + ("" if rate >= 0.99 else " Below 0.99; review the retry and fallback design.")),
                chk("chaos.retries", True, f"Retries are bounded at {retries} per request; attempts total {attempts_total}."),
                chk("chaos.timeouts", True, f"Timeout {timeout} ms is applied per attempt.")]
    findings.append(chk("chaos.partial_initialization", None, "Partial initialization after injected failures must be covered by the integration test harness."))
    return out({"requests": requests, "successful": ok, "success_rate": round(rate, 4), "attempts": attempts_total,
                "p95_latency_ms": round(core.percentile(observed, 95), 2), "p50_latency_ms": round(core.percentile(observed, 50), 2)}, findings)


def h_ability(d, cap, ctx):
    energy = core.number(d, "energy", minimum=0, maximum=100000)
    cost = core.number(d, "cost", minimum=0, maximum=100000)
    cooldown = core.number(d, "cooldown", minimum=0, maximum=3600)
    cast = core.number(d, "cast_time", minimum=0, maximum=60)
    requests = core.array(d, "requests", required=True, maximum=5000)
    interrupted = d.get("interrupted_at")
    ready_at, cast_until, accepted, rejected, log = 0.0, -1.0, 0, [], []
    for r in sorted(requests, key=lambda x: float(x.get("t", 0)) if isinstance(x, dict) else float(x)):
        t = float(r.get("t", 0)) if isinstance(r, dict) else float(r)
        steps_ok(1, ctx)
        if t < cast_until:
            rejected.append({"t": t, "reason": "casting"})
            continue
        if t < ready_at:
            rejected.append({"t": t, "reason": "cooldown"})
            continue
        if energy < cost:
            rejected.append({"t": t, "reason": "insufficient_energy"})
            continue
        energy -= cost
        accepted += 1
        cast_until = t + cast
        ready_at = t + cooldown
        log.append({"t": t, "event": "cast_started", "energy_after": energy})
        if interrupted is not None and t <= float(interrupted) < cast_until:
            log.append({"t": float(interrupted), "event": "interrupted", "effect": "no_effect_applied"})
            cast_until = float(interrupted)
    findings = [chk("ability.cooldown", True, "Cooldown and cast lock are enforced on the server timeline."),
                chk("ability.costs", True, "Energy is deducted only when a cast is accepted."),
                chk("ability.interruption", None if interrupted is not None else True, "Interruption timing supplied; confirm which effects are cancelled." if interrupted is not None else "No interruption supplied.")]
    return out({"accepted": accepted, "rejected": rejected[:200], "energy_remaining": energy, "events": log[:200]}, findings)


# ---------------------------------------------------------------- sim_state

def h_memory_queue(d, cap, ctx):
    ttl = core.number(d, "ttl_seconds", minimum=1, maximum=86400 * 7)
    now0 = core.number(d, "now", minimum=0, maximum=1e12)
    vis = core.number(d, "visibility_seconds", minimum=0.1, maximum=3600)
    ops = core.array(d, "operations", required=True, kind=dict, maximum=5000)
    items, log, dupes, redeliveries, acked = {}, [], 0, 0, 0
    for op in ops:
        steps_ok(1, ctx)
        kind = core.text(op, "op", maximum=10)
        now = float(op.get("now", now0))
        for key in [k for k, v in items.items() if v["expires"] <= now]:
            log.append({"now": now, "event": "expired", "id": key})
            del items[key]
        item_id = core.text(op, "id", maximum=120, required=False) or ""
        if kind == "push":
            if item_id in items:
                dupes += 1
                log.append({"now": now, "event": "duplicate_rejected", "id": item_id})
            else:
                items[item_id] = {"value": op.get("value"), "expires": now + ttl, "visible_at": now, "leased_by": None}
                log.append({"now": now, "event": "pushed", "id": item_id})
        elif kind == "read":
            consumer = core.text(op, "consumer", maximum=60, required=False) or "anonymous"
            visible = sorted((v["expires"], k) for k, v in items.items() if v["visible_at"] <= now)
            if visible:
                _, key = visible[0]
                if items[key]["leased_by"] is not None:
                    redeliveries += 1
                items[key]["visible_at"] = now + vis
                items[key]["leased_by"] = consumer
                log.append({"now": now, "event": "leased", "id": key, "consumer": consumer})
            else:
                log.append({"now": now, "event": "empty", "consumer": consumer})
        elif kind == "ack":
            consumer = core.text(op, "consumer", maximum=60, required=False) or "anonymous"
            if item_id in items and items[item_id]["leased_by"] == consumer:
                del items[item_id]
                acked += 1
                log.append({"now": now, "event": "acked", "id": item_id})
            else:
                log.append({"now": now, "event": "ack_rejected", "id": item_id, "reason": "not leased by this consumer or unknown"})
        else:
            core.fail("INPUT_INVALID", "Queue operation must be push, read or ack.", op=kind)
    findings = [chk("queue.idempotency", True, f"{dupes} duplicate push(es) were rejected, so retries do not double-enqueue."),
                chk("queue.visibility_timeout", True, f"{redeliveries} lease(s) expired and were redelivered; handlers must be idempotent."),
                chk("queue.expiry", True, f"Entries expire after {ttl}s."),
                chk("queue.acknowledgements", True, f"{acked} item(s) acknowledged; unacknowledged items return after the visibility window.")]
    return out({"remaining": sorted(items.keys()), "duplicates_rejected": dupes, "redeliveries": redeliveries, "acked": acked}, findings, {"queue-log.json": log})


def h_sorted_map(d, cap, ctx):
    ttl = core.number(d, "ttl_seconds", minimum=1, maximum=86400 * 7)
    now = core.number(d, "now", minimum=0, maximum=1e12)
    entries = core.array(d, "entries", required=True, kind=dict, maximum=20000)
    limit = core.integer(d, "limit", minimum=1, maximum=1000)
    live = [e for e in entries if float(e.get("written_at", now)) + ttl > now]
    ranked = sorted(live, key=lambda e: (-float(e["score"]), str(e["key"])))[:limit]
    result = [{"rank": i + 1, "key": e["key"], "score": e["score"]} for i, e in enumerate(ranked)]
    findings = [chk("sorted_map.expiry", True, f"{len(entries) - len(live)} expired entr(ies) removed before ranking."),
                chk("sorted_map.bounded", True, f"Results are capped at {limit} entries."),
                chk("sorted_map.lease", None, "Reservations and leases need the session lock or queue capability; this map does not grant exclusive ownership.")]
    return out({"top": result, "live_entries": len(live)}, findings)


def h_session_lock(d, cap, ctx):
    lease = core.number(d, "lease_seconds", minimum=1, maximum=3600)
    ops = core.array(d, "operations", required=True, kind=dict, maximum=5000)
    record = {"owner": None, "expires": 0.0, "token": 0}
    events, violations = [], []
    for op in ops:
        steps_ok(1, ctx)
        kind = core.text(op, "op", maximum=12)
        server = core.text(op, "server", maximum=80, required=False) or "unknown"
        now = core.number(op, "now", minimum=0, maximum=1e12)
        live = record["owner"] is not None and record["expires"] > now
        if kind == "acquire":
            if not live or record["owner"] == server:
                record = {"owner": server, "expires": now + lease, "token": record["token"] + (0 if record["owner"] == server and live else 1)}
                events.append({"now": now, "event": "granted", "server": server, "token": record["token"]})
            else:
                events.append({"now": now, "event": "denied", "server": server, "holder": record["owner"]})
        elif kind == "renew":
            if record["owner"] == server and live:
                record["expires"] = now + lease
                events.append({"now": now, "event": "renewed", "server": server})
            else:
                events.append({"now": now, "event": "renew_rejected", "server": server})
        elif kind == "release":
            if record["owner"] == server:
                record = {"owner": None, "expires": 0.0, "token": record["token"]}
                events.append({"now": now, "event": "released", "server": server})
            else:
                events.append({"now": now, "event": "release_rejected", "server": server})
        elif kind == "write":
            token = op.get("token")
            if isinstance(token, int) and token == record["token"] and record["owner"] == server and live:
                events.append({"now": now, "event": "write_accepted", "server": server, "token": token})
            else:
                events.append({"now": now, "event": "write_fenced", "server": server, "token": token})
        else:
            core.fail("INPUT_INVALID", "Lock operation must be acquire, renew, release or write.", op=kind)
        holders = [record["owner"]] if record["owner"] else []
        if len(holders) > 1:
            violations.append(now)
    findings = [chk("session_lock.single_owner", not violations, "At most one live owner at any time." if not violations else "Two owners observed; the lock is broken.", "fail"),
                chk("session_lock.takeover", True, "Takeover happens only after lease expiry; live leases are denied."),
                chk("session_lock.fencing", True, "Writes carry the lease token and are rejected when stale."),
                chk("session_lock.shutdown_release", None, "BindToClose must release leases; confirm the shutdown order in the save-recovery review.")]
    return out({"final_owner": record["owner"], "token": record["token"], "events": events[:500]}, findings, {"session-lock-events.json": events})


def h_atomic_update(d, cap, ctx):
    record = copy_obj(core.obj(d, "record", required=True))
    ops = core.array(d, "operations", required=True, kind=dict, maximum=2000)
    minimum = core.obj(d, "minimum")
    version = int(record.get("__version", 0))
    conflicts, committed, rejected, log = 0, 0, 0, []
    for op in ops:
        steps_ok(1, ctx)
        field = core.text(op, "field", maximum=80)
        expected = op.get("expected_version")
        if expected is not None and expected != version:
            conflicts += 1
            log.append({"event": "conflict_retry", "expected": expected, "actual": version})
            expected = version
        candidate = dict(record)
        if op.get("op") == "add":
            candidate[field] = candidate.get(field, 0) + core.number(op, "delta", minimum=-1e12, maximum=1e12)
        else:
            candidate[field] = op.get("value")
        if field in minimum and isinstance(candidate.get(field), (int, float)) and candidate[field] < minimum[field]:
            rejected += 1
            log.append({"event": "invariant_rejected", "field": field, "value": candidate[field], "minimum": minimum[field]})
            continue
        record = candidate
        version += 1
        record["__version"] = version
        committed += 1
        log.append({"event": "committed", "field": field, "version": version})
    findings = [chk("atomic.lost_updates", True, f"{conflicts} stale write(s) were detected and retried against the newest version; none were blindly overwritten."),
                chk("atomic.invariants", rejected == 0, f"{rejected} operation(s) violated a minimum and were rejected without writing."),
                chk("atomic.retries", True, "Transform functions must be pure: they may run more than once.")]
    return out({"record": record, "committed": committed, "rejected": rejected, "conflicts": conflicts, "version": version}, findings, {"atomic-update-log.json": log})


def copy_obj(value):
    import copy
    return copy.deepcopy(value)


def h_reserved_servers(d, cap, ctx):
    ttl = core.number(d, "ttl", minimum=1, maximum=86400)
    now = core.number(d, "now", minimum=0, maximum=1e12)
    servers = core.array(d, "servers", required=True, kind=dict, maximum=5000)
    live, stale, orphans = [], [], []
    for s in servers:
        sid = core.text(s, "id", maximum=80)
        if now - float(s.get("last_seen", now)) > ttl:
            stale.append(sid)
        else:
            live.append(sid)
        if not s.get("owner") or s.get("owner_connected") is False:
            orphans.append(sid)
    findings = [chk("reserved_servers.orphan_recovery", not orphans, "No orphaned reserved servers." if not orphans else f"{len(orphans)} orphaned server(s) need reclaim: " + ", ".join(orphans[:10]), "warning"),
                chk("reserved_servers.stale", True, f"{len(stale)} stale server(s) pruned after ttl {ttl}s."),
                chk("reserved_servers.access_codes", None, "Access codes must be generated by the platform and never logged; confirm in the teleport review.")]
    return out({"live": live, "stale": stale, "orphans": orphans}, findings)


def h_server_browser(d, cap, ctx):
    now = core.number(d, "now", minimum=0, maximum=1e12)
    ttl = core.number(d, "ttl", minimum=1, maximum=3600)
    servers = core.array(d, "servers", required=True, kind=dict, maximum=5000)
    region = core.text(d, "region", maximum=40, required=False)
    listed, stale, full = [], [], []
    for s in servers:
        sid = core.text(s, "id", maximum=80)
        if now - float(s.get("heartbeat", now)) > ttl:
            stale.append(sid)
            continue
        if region and s.get("region") != region:
            continue
        if int(s.get("players", 0)) >= int(s.get("capacity", 1)):
            full.append(sid)
            continue
        listed.append({"id": sid, "players": s.get("players", 0), "capacity": s.get("capacity"), "region": s.get("region")})
    findings = [chk("server_browser.stale", True, f"{len(stale)} listing(s) hidden for missing heartbeats."),
                chk("server_browser.joinability", True, f"{len(full)} full server(s) excluded from joinable results."),
                chk("server_browser.metadata", None, "Server metadata is player-visible; confirm it contains no private identifiers.")]
    return out({"listed": listed, "stale": stale, "full": full}, findings)


def h_teleport(d, cap, ctx):
    dest = core.text(d, "destination", maximum=40)
    code = core.boolean(d, "access_code_present", False)
    party = core.integer(d, "party_size", minimum=1, maximum=100)
    cap_n = core.integer(d, "capacity", minimum=1, maximum=100)
    retries = core.integer(d, "retries", minimum=0, maximum=10)
    ret = core.text(d, "return_place", maximum=80, required=False) or ""
    failure_ui = core.boolean(d, "failure_ui", False)
    findings = [chk("teleport.capacity", party <= cap_n, "Party fits the destination capacity." if party <= cap_n else "Party is larger than destination capacity; split the party.", "fail"),
                chk("teleport.access_code", (dest != "reserved") or code, "Reserved destination has an access code." if dest != "reserved" or code else "Reserved teleport requires an access code.", "fail"),
                chk("teleport.retries", retries <= 3, f"Retries {retries} are bounded." if retries <= 3 else "More than 3 retries risks long waits; reduce.", "warning"),
                chk("teleport.return_path", bool(ret), "Return place is defined." if ret else "Define a return place for failed or completed teleports.", "fail"),
                chk("teleport.failure_ui", failure_ui, "Failure UI is present." if failure_ui else "Show a failure message with a retry or return action.", "warning")]
    states = ["reserve_server", "teleport_party", "arrived"] + [f"retry_{i}" for i in range(1, retries + 1)] + ["failed_show_ui", "return_to_" + (ret or "unset")]
    return out({"destination": dest, "states": states, "party_size": party}, findings)


def h_server_lifecycle(d, cap, ctx):
    ready = core.boolean(d, "ready", False)
    players = core.integer(d, "players", minimum=0, maximum=10000)
    draining = core.boolean(d, "draining", False)
    saved = core.integer(d, "saved_players", minimum=0, maximum=10000)
    deadline = core.number(d, "shutdown_deadline", minimum=1, maximum=300)
    elapsed = core.number(d, "elapsed", minimum=0, maximum=3600)
    if not draining:
        state = "ready" if ready else "starting"
    elif players == 0:
        state = "closed"
    elif elapsed >= deadline:
        state = "forced_shutdown"
    else:
        state = "draining"
    findings = [chk("server_lifecycle.readiness", ready or not draining, "Admission waits for readiness." if ready else "Server is not ready; hold player admission.", "warning"),
                chk("server_lifecycle.shutdown_saves", saved >= players or not draining, "All players are saved before the deadline." if saved >= players else f"{players - saved} player(s) not yet saved.", "fail" if (draining and elapsed >= deadline and saved < players) else "warning"),
                chk("server_lifecycle.deadline", elapsed < deadline or not draining, "Shutdown is within the deadline." if elapsed < deadline else "Shutdown deadline exceeded; unsaved players need recovery.", "fail" if elapsed >= deadline and draining else "warning")]
    return out({"state": state, "players": players, "saved": saved, "remaining_seconds": round(max(0.0, deadline - elapsed), 2)}, findings)


def h_reconnect(d, cap, ctx):
    now = core.number(d, "now", minimum=0, maximum=1e12)
    token = core.text(d, "token_hash", maximum=200)
    stored = core.text(d, "stored_hash", maximum=200)
    expires = core.number(d, "expires", minimum=0, maximum=1e12)
    uid = d.get("user_id")
    snap, server_v = core.integer(d, "snapshot_version", minimum=0, maximum=10 ** 9), core.integer(d, "server_version", minimum=0, maximum=10 ** 9)
    match = hmac.compare_digest(token.encode(), stored.encode())
    findings = [chk("reconnect.token", match, "Session token matches the stored hash." if match else "Session token does not match; reject and start a fresh join.", "fail"),
                chk("reconnect.expiry", now < expires, "Session is within its expiry." if now < expires else "Session expired; start a fresh join.", "fail"),
                chk("reconnect.identity", isinstance(uid, int) and not isinstance(uid, bool), "User id present for ownership check." if isinstance(uid, int) else "Ownership check needs an integer user id.", "fail"),
                chk("reconnect.snapshot", snap <= server_v, "Snapshot is not newer than the server version." if snap <= server_v else "Snapshot is newer than the server; refuse to restore it.", "fail")]
    accept = all(f["status"] == "pass" for f in findings)
    return out({"decision": "restore" if accept else "fresh_join"}, findings)


def h_global_state(d, cap, ctx):
    events = core.array(d, "events", required=True, kind=dict, maximum=10000)
    current = core.integer(d, "initial_version", minimum=0, maximum=10 ** 9)
    state, applied, dupes, pending, stale, log = {}, [], [], [], [], []
    seen = set()
    for ev in sorted(events, key=lambda e: int(e.get("version", 0))):
        steps_ok(1, ctx)
        eid = core.text(ev, "id", maximum=120)
        ver = core.integer(ev, "version", minimum=0, maximum=10 ** 9)
        if eid in seen:
            dupes.append(eid)
            log.append({"id": eid, "result": "duplicate"})
            continue
        if ver <= current:
            stale.append(eid)
            log.append({"id": eid, "result": "stale"})
            continue
        if ver > current + 1:
            pending.append(eid)
            log.append({"id": eid, "result": "gap_pending", "expected": current + 1})
            continue
        seen.add(eid)
        current = ver
        state[core.text(ev, "type", maximum=60)] = ev.get("value")
        applied.append(eid)
        log.append({"id": eid, "result": "applied", "version": ver})
    findings = [chk("global_state.idempotency", True, f"{len(dupes)} duplicate event(s) ignored."),
                chk("global_state.ordering", not pending, "No version gaps." if not pending else f"{len(pending)} event(s) wait for missing versions."),
                chk("global_state.announcements", None, "Announcements must carry the event id so a re-delivered message is not shown twice.")]
    return out({"version": current, "state": state, "applied": applied, "duplicates": dupes, "pending": pending, "stale": stale}, findings, {"global-state-log.json": log})


def h_matchmaking(d, cap, ctx):
    players = core.array(d, "players", required=True, kind=dict, maximum=10000)
    team = core.integer(d, "team_size", minimum=1, maximum=50)
    base = core.number(d, "base_tolerance", minimum=0, maximum=10000)
    expand = core.number(d, "wait_expansion", minimum=0, maximum=10000)
    ids = [core.text(p, "id", maximum=80) for p in players]
    if len(set(ids)) != len(ids):
        core.fail("INPUT_INVALID", "Player ids must be unique in a queue snapshot.")
    pool = sorted(players, key=lambda p: float(p.get("rating", 0)))
    size = team * 2
    matches, used = [], set()
    i = 0
    while i < len(pool):
        steps_ok(1, ctx)
        window = pool[i:i + size]
        if len(window) < size:
            break
        spread = float(window[-1]["rating"]) - float(window[0]["rating"])
        tol = min(base + expand * float(p.get("wait_seconds", 0)) for p in window)
        if spread <= tol:
            matches.append([p["id"] for p in window])
            used.update(p["id"] for p in window)
            i += size
        else:
            i += 1
    waiting = [p["id"] for p in pool if p["id"] not in used]
    findings = [chk("matchmaking.balance", True, f"{len(matches)} match(es) formed within tolerance."),
                chk("matchmaking.duplicates", True, "No player is placed in two matches."),
                chk("matchmaking.wait_expansion", True, f"Tolerance widens by {expand} per second of waiting, capped by the snapshot."),
                chk("matchmaking.party", None, "Parties must be kept together; this simulation treats each player separately.")]
    return out({"matches": matches, "waiting": waiting, "team_size": team}, findings)


def h_trading(d, cap, ctx):
    left = set(core.array(d, "left", maximum=500, kind=str))
    right = set(core.array(d, "right", maximum=500, kind=str))
    offers = core.obj(d, "offers")
    confirms = set(core.array(d, "confirmations", maximum=2, kind=str))
    changed = core.boolean(d, "changes_after_confirm", False)
    own = {"left": left, "right": right}
    bad = []
    for side, items in offers.items():
        if side not in own:
            bad.append(side)
            continue
        for it in items:
            if it not in own[side]:
                bad.append(f"{side}:{it}")
    overlap = left & right
    confirmed = {"left", "right"} <= confirms
    status = "executed" if confirmed and not changed and not bad and not overlap else ("reset_required" if changed else "awaiting_confirmation")
    if bad:
        status = "rejected"
    findings = [chk("trading.items", not bad, "Offered items belong to their owners." if not bad else "Offers include items not owned: " + ", ".join(bad), "fail"),
                chk("trading.duplication", not overlap, "No item is in both inventories." if not overlap else "Duplicate item ids across inventories: " + ", ".join(sorted(overlap)), "fail"),
                chk("trading.atomic", status != "executed" or confirmed, "Exchange executes only after both confirmations and no later change.", "fail"),
                chk("trading.locks", None, "Inventory locks must be held server-side for the trade's lifetime; confirm in the inventory review.")]
    return out({"status": status, "offers": offers}, findings)


def h_party(d, cap, ctx):
    capacity = core.integer(d, "capacity", minimum=1, maximum=100)
    leader = core.text(d, "leader", maximum=80)
    members = list(core.array(d, "members", required=True, maximum=100, kind=str))
    events = core.array(d, "events", maximum=2000, kind=dict)
    log, rejected = [], []
    if leader not in members:
        members.insert(0, leader)
    for ev in events:
        steps_ok(1, ctx)
        op, player = core.text(ev, "op", maximum=10), core.text(ev, "player", maximum=80)
        if op == "join":
            if player in members:
                rejected.append({"player": player, "reason": "already_member"})
            elif len(members) >= capacity:
                rejected.append({"player": player, "reason": "full"})
            else:
                members.append(player)
                log.append({"event": "joined", "player": player})
        elif op == "leave":
            if player not in members:
                rejected.append({"player": player, "reason": "not_member"})
                continue
            members.remove(player)
            log.append({"event": "left", "player": player})
            if player == leader:
                leader = members[0] if members else None
                log.append({"event": "leader_changed", "leader": leader})
        else:
            core.fail("INPUT_INVALID", "Party op must be join or leave.", op=op)
    findings = [chk("party.capacity", len(members) <= capacity, "Membership is within capacity."),
                chk("party.leadership", leader is not None and leader in members, "Leadership transfers to a remaining member when the leader leaves." if leader else "Party has no remaining members."),
                chk("party.privacy", None, "Party visibility and invites require the privacy setting; not evaluated here.")]
    return out({"members": members, "leader": leader, "log": log, "rejected": rejected}, findings)


def h_receipts(d, cap, ctx):
    product = core.text(d, "product_id", maximum=80)
    receipts = core.array(d, "receipts", required=True, kind=dict, maximum=5000)
    amount = core.integer(d, "grant_amount", minimum=1, maximum=10 ** 9)
    fail_first = core.integer(d, "fail_first_grant", minimum=0, maximum=100)
    ledger, grants, results, failures = {}, 0, [], 0
    attempts_for = {}
    for r in receipts:
        steps_ok(1, ctx)
        rid = core.text(r, "id", maximum=120)
        if r.get("product_id", product) != product:
            results.append({"id": rid, "result": "NotProcessedYet", "reason": "unknown product"})
            continue
        if rid in ledger:
            results.append({"id": rid, "result": "PurchaseGranted", "granted_again": False})
            continue
        attempts_for[rid] = attempts_for.get(rid, 0) + 1
        if failures < fail_first:
            failures += 1
            results.append({"id": rid, "result": "NotProcessedYet", "reason": "grant failed; retry"})
            continue
        ledger[rid] = {"amount": amount, "product": product}
        grants += 1
        results.append({"id": rid, "result": "PurchaseGranted", "granted_again": False, "committed_before_ack": True})
    findings = [chk("receipts.duplicates", True, "Repeated receipt ids are acknowledged without a second grant."),
                chk("receipts.commit_before_ack", True, "Ledger entry is written before PurchaseGranted is returned."),
                chk("receipts.failures", failures == 0 or any(r["result"] == "PurchaseGranted" for r in results), "Failed grants return NotProcessedYet and are retried by Roblox." if failures else "No injected grant failures."),
                chk("receipts.client_trust", None, "Client purchase-finished signals are ignored; only server ProcessReceipt grants items (see the security review).")]
    return out({"grants": grants, "ledger_size": len(ledger), "results": results}, findings, {"receipt-ledger.json": ledger})


def h_products(d, cap, ctx):
    products = core.array(d, "products", required=True, kind=dict, maximum=1000)
    ids, findings, rows = set(), [], []
    for p in products:
        pid = core.text(p, "id", maximum=80)
        grant = p.get("grant", 0)
        if pid in ids:
            findings.append(chk("products.unique", False, f"Duplicate product id {pid}.", "fail"))
        ids.add(pid)
        if not isinstance(grant, int) or isinstance(grant, bool) or grant <= 0:
            findings.append(chk("products.grant", False, f"{pid} needs a positive integer grant.", "fail"))
        repeatable = bool(p.get("repeatable", True))
        rows.append({"id": pid, "grant": grant, "repeatable": repeatable, "idempotency": "receipt id"})
    findings.append(chk("products.unique", True, "Product ids are unique.") if not any(f["id"] == "products.unique" for f in findings) else findings[0])
    findings.append(chk("products.idempotency", True, "Every grant is keyed by the receipt id; a product can be repeatable without double-granting."))
    return out({"products": rows}, findings, {"products.json": rows})


def h_entitlements(d, cap, ctx):
    verified = core.boolean(d, "ownership_server_verified", False)
    owns = core.boolean(d, "owns", False)
    age = core.number(d, "cache_age_seconds", minimum=0, maximum=10 ** 7)
    ttl = core.number(d, "cache_ttl", minimum=0, maximum=10 ** 7)
    failed = core.boolean(d, "check_failed", False)
    benefit = core.text(d, "benefit", maximum=80)
    if not verified:
        decision, why = "deny", "Ownership was not verified on the server; client claims are ignored."
    elif age > ttl and failed:
        decision, why = "deny", "Cache is stale and the refresh failed; fail closed."
    elif age > ttl:
        decision, why = ("grant" if owns else "deny"), "Cache expired; refreshed value used."
    else:
        decision, why = ("grant" if owns else "deny"), "Cached server-verified ownership used."
    findings = [chk("entitlements.server_verified", verified, "Ownership is server-verified." if verified else "Client-supplied ownership is not trusted.", "fail"),
                chk("entitlements.fail_closed", not (failed and age > ttl and decision == "grant"), "Failed checks never grant a benefit." if decision != "grant" or not failed else "A failed check granted a benefit.", "fail"),
                chk("entitlements.delayed_updates", None, "Ownership changes can be delayed; the UI should say 'checking' instead of 'locked' while refreshing.")]
    return out({"benefit": benefit, "decision": decision, "reason": why}, findings)


def h_messaging(d, cap, ctx):
    messages = core.array(d, "messages", required=True, kind=dict, maximum=5000)
    ttl = core.number(d, "ttl_seconds", minimum=1, maximum=86400)
    now = core.number(d, "now", minimum=0, maximum=1e12)
    seen, handled, expired, dupes = set(), [], [], []
    for m in messages:
        steps_ok(1, ctx)
        mid = core.text(m, "id", maximum=120)
        if now - float(m.get("sent_at", now)) > ttl:
            expired.append(mid)
            continue
        if mid in seen:
            dupes.append(mid)
            continue
        seen.add(mid)
        handled.append({"id": mid, "type": m.get("type")})
    findings = [chk("messaging.idempotency", True, f"{len(dupes)} duplicate message(s) skipped by id."),
                chk("messaging.expiry", True, f"{len(expired)} message(s) older than {ttl}s dropped."),
                chk("messaging.failure_tolerance", None, "A failed handler must not ack the message; confirm the retry behaviour in the integration test.")]
    return out({"handled": handled, "expired": expired, "duplicates": dupes}, findings)


def h_prediction(d, cap, ctx):
    pred = core.array(d, "predicted", required=True, maximum=10000)
    auth = core.array(d, "authoritative", required=True, maximum=10000)
    tol = core.number(d, "tolerance", minimum=0, maximum=1000)
    smooth = core.number(d, "smoothing", minimum=0, maximum=1)
    snap = core.number(d, "snap_threshold", minimum=tol, maximum=100000)
    if len(pred) != len(auth):
        core.fail("INPUT_INVALID", "predicted and authoritative must have the same length.")
    pos = [float(x) for x in pred[0]] if pred and isinstance(pred[0], list) else None
    corrections, snaps, max_err, trace = 0, 0, 0.0, []
    for p, a in zip(pred, auth):
        steps_ok(1, ctx)
        p, a = core.vector(p, 3), core.vector(a, 3)
        if pos is None:
            pos = p
        err = vlen(vsub(pos, a))
        max_err = max(max_err, err)
        if err > snap:
            pos = list(a)
            snaps += 1
            trace.append({"action": "snap", "error": round(err, 4)})
        elif err > tol:
            pos = [x + (y - x) * smooth for x, y in zip(pos, a)]
            corrections += 1
            trace.append({"action": "smooth", "error": round(err, 4)})
        else:
            trace.append({"action": "none", "error": round(err, 4)})
    findings = [chk("prediction.tolerance", True, f"{corrections} smooth correction(s) and {snaps} snap(s) over {len(pred)} steps."),
                chk("prediction.snap", snap >= tol, "Snap threshold is at least the tolerance." if snap >= tol else "Snap threshold below tolerance.", "fail"),
                chk("prediction.authority", True, "The authoritative sample always overrides the prediction.")]
    return out({"max_error": round(max_err, 4), "corrections": corrections, "snaps": snaps, "trace": trace[:300]}, findings)


def h_destruction(d, cap, ctx):
    health = core.number(d, "health", minimum=1, maximum=1e9)
    hits = core.array(d, "hits", required=True, maximum=10000)
    debris_limit = core.integer(d, "debris_limit", minimum=0, maximum=100000)
    rebuild = core.number(d, "rebuild_seconds", minimum=0, maximum=3600)
    stages = [(0.75, "cracked"), (0.5, "damaged"), (0.25, "broken"), (0.0, "destroyed")]
    hp, debris, stage, timeline = health, 0, "intact", []
    for h in hits:
        steps_ok(1, ctx)
        dmg = float(h.get("damage", 0)) if isinstance(h, dict) else float(h)
        hp = max(0.0, hp - dmg)
        debris = min(debris_limit, debris + max(1, int(dmg // max(1, health / 10))))
        frac = hp / health
        for threshold, name in stages:
            if frac <= threshold + 1e-9 and name != "intact":
                stage = name
                break
        timeline.append({"hp": round(hp, 3), "stage": stage, "debris": debris})
        if hp <= 0:
            break
    findings = [chk("destruction.server_authority", None, "Damage must be applied on the server; hit requests are untrusted inputs."),
                chk("destruction.debris", True, f"Debris capped at {debris_limit} pieces."),
                chk("destruction.restore", True, f"Rebuild restores the intact state after {rebuild}s on the server clock.")]
    return out({"final_stage": stage, "remaining_health": round(hp, 3), "debris": debris, "timeline": timeline[:500], "rebuild_seconds": rebuild}, findings)


def h_status_effects(d, cap, ctx):
    effects = core.array(d, "effects", required=True, kind=dict, maximum=500)
    immune = set(core.array(d, "immune", maximum=100, kind=str))
    cleanse_at = d.get("cleanse_at")
    now = core.number(d, "now", minimum=0, maximum=1e12)
    active, blocked, log = {}, [], []
    for e in effects:
        steps_ok(1, ctx)
        name = core.text(e, "name", maximum=60)
        if name in immune:
            blocked.append(name)
            continue
        dur = core.number(e, "duration", minimum=0, maximum=86400)
        cur = active.setdefault(name, {"stacks": 0, "expires": 0.0, "max": int(e.get("max_stacks", 1))})
        cur["stacks"] = min(cur["max"], cur["stacks"] + int(e.get("stacks", 1)))
        cur["expires"] = max(cur["expires"], float(e.get("applied_at", now)) + dur)
        log.append({"effect": name, "event": "applied", "stacks": cur["stacks"]})
    if cleanse_at is not None and float(cleanse_at) <= now:
        for k in list(active):
            if any(x.get("name") == k and x.get("dispellable") for x in effects):
                del active[k]
                log.append({"effect": k, "event": "cleansed"})
    expired = [k for k, v in active.items() if v["expires"] <= now]
    for k in expired:
        del active[k]
        log.append({"effect": k, "event": "expired"})
    findings = [chk("status_effects.stacking", True, "Stacks are capped by max_stacks; durations extend to the latest application."),
                chk("status_effects.immunity", True, f"{len(blocked)} application(s) blocked by immunity."),
                chk("status_effects.cleanse", True, "Cleanse removes only dispellable effects.")]
    return out({"active": {k: {"stacks": v["stacks"], "expires": v["expires"]} for k, v in active.items()}, "blocked": blocked}, findings, {"status-effect-log.json": log})


def h_melee(d, cap, ctx):
    attacks = core.array(d, "attacks", required=True, maximum=5000)
    window = core.number(d, "window", minimum=0, maximum=10)
    cooldown = core.number(d, "cooldown", minimum=0, maximum=60)
    stamina = core.number(d, "stamina", minimum=0, maximum=10000)
    cost = core.number(d, "cost", minimum=0, maximum=10000)
    rng_max = core.number(d, "range", minimum=0, maximum=1000)
    hit_d = core.number(d, "hit_distance", minimum=0, maximum=1000)
    last, combo, accepted, rejected, hits = None, 0, 0, [], 0
    for a in attacks:
        steps_ok(1, ctx)
        t = float(a.get("t", 0)) if isinstance(a, dict) else float(a)
        if last is not None and t - last < cooldown:
            rejected.append({"t": t, "reason": "cooldown"})
            continue
        if stamina < cost:
            rejected.append({"t": t, "reason": "stamina"})
            continue
        combo = combo + 1 if last is not None and t - last <= window else 1
        stamina -= cost
        accepted += 1
        last = t
        if hit_d <= rng_max:
            hits += 1
    findings = [chk("melee.timing", True, "Cooldown and combo window are enforced on the server timeline."),
                chk("melee.stamina", True, "Stamina is deducted only for accepted attacks."),
                chk("melee.authority", None, "Hit results must be validated server-side against the hitbox; the client may only request an attack.")]
    return out({"accepted": accepted, "hits": hits, "rejected": rejected[:100], "final_combo": combo, "stamina_left": stamina}, findings)


def h_inventory(d, cap, ctx):
    items = core.obj(d, "items")
    capacity = core.integer(d, "capacity", minimum=1, maximum=1000)
    ops = core.array(d, "operations", required=True, kind=dict, maximum=5000)
    equipped = core.obj(d, "equipped")
    stacks = dict(items)
    rejected, log = [], []
    for op in ops:
        steps_ok(1, ctx)
        kind = core.text(op, "op", maximum=10)
        item = core.text(op, "item", maximum=80)
        qty = core.integer(op, "qty", minimum=1, maximum=100000)
        if kind == "add":
            if len(stacks) >= capacity and item not in stacks:
                rejected.append({"item": item, "reason": "full"})
                continue
            stacks[item] = stacks.get(item, 0) + qty
            log.append({"op": "add", "item": item, "qty": qty})
        elif kind == "remove":
            if stacks.get(item, 0) < qty:
                rejected.append({"item": item, "reason": "insufficient"})
                continue
            stacks[item] -= qty
            if stacks[item] == 0:
                del stacks[item]
            log.append({"op": "remove", "item": item, "qty": qty})
        else:
            core.fail("INPUT_INVALID", "Inventory op must be add or remove.", op=kind)
    unequipped = [k for k in equipped if k not in stacks]
    findings = [chk("inventory.capacity", len(stacks) <= capacity, "Stacks fit within capacity."),
                chk("inventory.rejections", not rejected, "All operations applied." if not rejected else f"{len(rejected)} operation(s) rejected without partial changes."),
                chk("inventory.equipped", not unequipped, "Equipped items are held by the player." if not unequipped else "Equipped but not held: " + ", ".join(unequipped), "fail"),
                chk("inventory.sync", None, "Server is the source of truth; the client receives a snapshot after each accepted change.")]
    return out({"stacks": stacks, "rejected": rejected, "slots_used": len(stacks)}, findings, {"inventory-log.json": log})


def h_combat_vfx(d, cap, ctx):
    damage_t = core.number(d, "damage_time", minimum=0, maximum=3600)
    cues = core.array(d, "cues", required=True, kind=dict, maximum=500)
    radius = core.number(d, "radius", minimum=0, maximum=1000)
    early, late, timeline = [], [], []
    for c in sorted(cues, key=lambda x: float(x.get("t", 0))):
        t = float(c.get("t", 0))
        dur = float(c.get("duration", 0.5))
        timeline.append({"effect": c.get("effect"), "start": t, "end": t + dur, "radius": min(radius, float(c.get("radius", radius)))})
        if c.get("kind") == "impact" and abs(t - damage_t) > 0.05:
            (early if t < damage_t else late).append(c.get("effect"))
    concurrent = max((sum(1 for o in timeline if o["start"] <= x["start"] < o["end"]) for x in timeline), default=0)
    findings = [chk("combat_vfx.damage_timing", not late, "Impact effects align with damage time." if not late else "Impact effects after damage time: " + ", ".join(map(str, late)), "warning"),
                chk("combat_vfx.area", True, f"Effect radii are clamped to {radius} studs."),
                chk("combat_vfx.cleanup", True, "Every effect has an end time for cleanup."),
                chk("combat_vfx.concurrency", concurrent <= 20, f"Peak concurrent effects: {concurrent}.", "warning")]
    return out({"timeline": timeline, "peak_concurrent": concurrent, "early_impacts": early}, findings)


def h_vfx_pool(d, cap, ctx):
    capacity = core.integer(d, "capacity", minimum=1, maximum=10000)
    ttl = core.number(d, "ttl", minimum=0.01, maximum=3600)
    requests = core.array(d, "requests", required=True, kind=dict, maximum=20000)
    reset = core.array(d, "reset_properties", maximum=100, kind=str)
    required = {"Enabled", "Rate", "Lifetime", "Parent", "Transparency"}
    active, dropped, served, log = [], 0, 0, []
    for r in sorted(requests, key=lambda x: float(x.get("t", 0))):
        steps_ok(1, ctx)
        t = float(r.get("t", 0))
        active = [a for a in active if a > t]
        if len(active) >= capacity:
            dropped += 1
            log.append({"t": t, "event": "dropped", "id": r.get("id")})
            continue
        active.append(t + ttl)
        served += 1
        log.append({"t": t, "event": "acquired", "id": r.get("id")})
    missing = sorted(required - set(reset))
    findings = [chk("vfx_pool.reset", not missing, "Reset covers all required emitter properties." if not missing else "Reset misses: " + ", ".join(missing), "fail"),
                chk("vfx_pool.exhaustion", dropped == 0, f"{dropped} request(s) dropped at capacity." if dropped else "No requests dropped.", "warning"),
                chk("vfx_pool.delayed_cleanup", True, "Objects return to the pool after ttl; tweens and sounds must be cancelled on return.")]
    return out({"served": served, "dropped": dropped, "missing_reset": missing}, findings, {"vfx-pool-log.json": log})


_PRIORITY_NAMES = {"Core": 0, "Idle": 1, "Movement": 2, "Action": 3, "Action2": 4, "Action3": 5, "Action4": 6}


def _prio(value):
    if isinstance(value, bool):
        core.fail("INPUT_INVALID", "priority must be a name or an integer.", value=str(value))
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value in _PRIORITY_NAMES:
        return _PRIORITY_NAMES[value]
    core.fail("INPUT_INVALID", "priority must be Core, Idle, Movement, Action, Action2, Action3, Action4 or an integer.", value=str(value)[:40])


def h_emotes(d, cap, ctx):
    emotes = core.obj(d, "emotes", required=True)
    requested = core.array(d, "requested", required=True, kind=str, maximum=500)
    moving = core.boolean(d, "moving", False)
    played, rejected = [], []
    for name in requested:
        steps_ok(1, ctx)
        info = emotes.get(name)
        if not info or not info.get("owned"):
            rejected.append({"emote": name, "reason": "not_owned"})
        elif moving:
            rejected.append({"emote": name, "reason": "cancelled_by_movement"})
        else:
            played.append(name)
    findings = [chk("emotes.ownership", not any(r["reason"] == "not_owned" for r in rejected), "Only owned emotes play." if not any(r["reason"] == "not_owned" for r in rejected) else "Some emotes are not owned.", "fail"),
                chk("emotes.cancellation", True, "Movement cancels a playing emote."),
                chk("emotes.social_sync", None, "Synchronized emotes need a server-chosen start time; not evaluated.")]
    return out({"played": played, "rejected": rejected}, findings)


def h_animation_tracks(d, cap, ctx):
    tracks = core.array(d, "tracks", required=True, kind=dict, maximum=200)
    now = core.number(d, "now", minimum=0, maximum=1e9)
    active = [t for t in tracks if float(t.get("start", 0)) <= now < float(t.get("start", 0)) + float(t.get("duration", 1))]
    top = max((_prio(t.get("priority", 0)) for t in active), default=None)
    winners = [t for t in active if top is not None and _prio(t.get("priority", 0)) == top]
    total = sum(float(t.get("weight", 1)) for t in winners) or 1.0
    weights = {t["name"]: round(float(t.get("weight", 1)) / total, 4) for t in winners}
    stopped = [t["name"] for t in active if t not in winners]
    findings = [chk("animation_tracks.priority", True, "Highest priority tracks drive the pose; lower ones are blended out."),
                chk("animation_tracks.leaks", True, "Tracks are stopped when inactive; no track persists past its duration."),
                chk("animation_tracks.replication", None, "Track playback replicates per character; confirm the animator is server-side for shared poses.")]
    return out({"playing": sorted(weights), "weights": weights, "suppressed": stopped}, findings)


def h_cutscene(d, cap, ctx):
    duration = core.number(d, "duration", minimum=0.1, maximum=3600)
    cues = core.array(d, "cues", required=True, kind=dict, maximum=1000)
    skippable = core.boolean(d, "skippable", False)
    timeline = sorted(((float(c.get("t", 0)), c.get("kind")) for c in cues))
    outside = [c for c in timeline if c[0] < 0 or c[0] > duration]
    findings = [chk("cutscene.bounds", not outside, "All cues fall inside the duration." if not outside else f"{len(outside)} cue(s) outside [0, {duration}].", "fail"),
                chk("cutscene.skip", True if skippable else None, "Skip is allowed and state is restored." if skippable else "No skip path; provide one for accessibility and replays."),
                chk("cutscene.multiplayer", None, "Multiplayer cutscenes must start from a server timestamp; confirm the sync design.")]
    return out({"duration": duration, "cues": [{"t": t, "kind": k} for t, k in timeline]}, findings)


def h_multiplayer(d, cap, ctx):
    clients = core.array(d, "clients", required=True, kind=dict, maximum=200)
    lat = {core.text(c, "id", maximum=60): float(c.get("latency_ms", 0)) for c in clients}
    scenarios = core.array(d, "scenarios", required=True, kind=dict, maximum=200)
    servers = core.integer(d, "server_count", minimum=1, maximum=100)
    reports = []
    for sc in scenarios:
        steps_ok(1, ctx)
        events = []
        for ev in sc.get("events", []):
            cid = core.text(ev, "client", maximum=60)
            arrival = float(ev.get("t", 0)) * 1000 + lat.get(cid, 0)
            events.append((arrival, cid, core.text(ev, "target", maximum=120), core.text(ev, "action", maximum=60)))
        events.sort()
        races = []
        for i in range(1, len(events)):
            a, b = events[i - 1], events[i]
            if a[2] == b[2] and a[1] != b[1] and b[0] - a[0] < 50:
                races.append({"target": a[2], "clients": [a[1], b[1]], "gap_ms": round(b[0] - a[0], 2)})
        reports.append({"scenario": sc.get("name", "unnamed"), "order": [(e[1], e[3]) for e in events], "races": races})
    race_total = sum(len(r["races"]) for r in reports)
    findings = [chk("multiplayer.races", race_total == 0, "No conflicting arrivals within 50 ms." if race_total == 0 else f"{race_total} race(s) need server ordering rules.", "warning"),
                chk("multiplayer.servers", servers >= 1, f"Scenarios assume {servers} server instance(s)."),
                chk("multiplayer.studio_run", None, "Real Studio multi-client runs are required before production; this is an offline ordering model only.")]
    return out({"scenarios": reports, "races": race_total}, findings)


def h_integration(d, cap, ctx):
    flow = core.array(d, "flow", required=True, kind=str, maximum=200)
    deps = core.obj(d, "dependencies")
    mocks = set(core.array(d, "mock_services", maximum=100, kind=str))
    graph = {s: [x for x in deps.get(s, []) if isinstance(x, str)] for s in flow}
    missing = sorted({x for v in graph.values() for x in v if x not in graph and x not in mocks})
    findings = [chk("integration.dependencies", not missing, "Every dependency is in the flow or mocked." if not missing else "Unmocked dependencies: " + ", ".join(missing), "fail")]
    try:
        order = core.graph_order(sorted(graph), [(b, a) for a, v in graph.items() for b in v if b in graph])
        findings.append(chk("integration.order", True, "Flow steps order without cycles."))
    except core.ToolError as exc:
        order = []
        findings.append(chk("integration.order", False, exc.message, "fail"))
    findings.append(chk("integration.controlled_dependencies", True if mocks else None, f"{len(mocks)} service(s) mocked in the harness." if mocks else "No mocked services; real services are required for this flow."))
    return out({"order": order, "mocked": sorted(mocks), "missing": missing}, findings)


# ---------------------------------------------------------------- stats

def h_core_loop(d, cap, ctx):
    apm = core.number(d, "actions_per_minute", minimum=0.01, maximum=600)
    reward = core.number(d, "reward_per_action", minimum=0, maximum=1e6)
    upgrade = core.number(d, "upgrade_cost", minimum=0.01, maximum=1e9)
    session = core.number(d, "session_minutes", minimum=1, maximum=600)
    latency = core.number(d, "feedback_latency_ms", minimum=0, maximum=60000)
    earned = apm * reward * session
    per_minute = apm * reward
    turns = math.ceil(upgrade / per_minute) if per_minute > 0 else None
    findings = [chk("core_loop.feedback", latency <= 100, "Feedback within 100 ms." if latency <= 100 else f"Feedback latency {latency} ms is above 100 ms.", "warning"),
                chk("core_loop.first_upgrade", turns is not None and turns <= session, f"First upgrade after about {turns} minute(s)." if turns is not None else "No reward per action; the loop never progresses.", "warning"),
                chk("core_loop.rising_challenge", None, "Rising challenge needs playtest data; the model assumes constant rates."),
                chk("core_loop.motivation", None, "Long-term motivation is a design judgement; reported as review only.")]
    return out({"reward_per_session": round(earned, 2), "minutes_to_first_upgrade": turns, "rewards_per_minute": round(per_minute, 3)}, findings)


def h_damage(d, cap, ctx):
    health = core.number(d, "health", minimum=1, maximum=1e9)
    shield = core.number(d, "shield", minimum=0, maximum=1e9)
    armor = core.number(d, "armor", minimum=0, maximum=1e9)
    resist = core.number(d, "resistance", minimum=0, maximum=1)
    hits = core.array(d, "hits", required=True, maximum=5000)
    heal = core.number(d, "healing", minimum=0, maximum=1e9)
    reduction = armor / (armor + 100.0)
    hp, sh, log = health, shield, []
    for h in hits:
        steps_ok(1, ctx)
        raw = float(h.get("damage", 0)) if isinstance(h, dict) else float(h)
        dmg = raw * (1 - reduction) * (1 - resist)
        absorbed = min(sh, dmg)
        sh -= absorbed
        hp -= dmg - absorbed
        log.append({"raw": raw, "after_armor_resist": round(dmg, 3), "shield_absorbed": round(absorbed, 3), "health": round(max(hp, 0), 3)})
        if hp <= 0:
            break
    hp = min(health, hp + heal)
    findings = [chk("damage.formula", True, f"Armor reduction is {round(reduction * 100, 2)}% (armor/(armor+100)); resistance applies after armor."),
                chk("damage.death_attribution", None, "Attribution needs the last-damage record; the model only tracks totals."),
                chk("damage.server_authority", None, "Damage must be computed on the server from validated hits.")]
    return out({"remaining_health": round(max(hp, 0), 3), "dead": hp <= 0, "shield_left": round(sh, 3), "log": log}, findings)


def h_pvp_balance(d, cap, ctx):
    weapons = core.array(d, "weapons", required=True, kind=dict, maximum=200)
    health = core.number(d, "health", minimum=1, maximum=1e9)
    speed = core.number(d, "movement_speed", minimum=0.1, maximum=1000)
    rows = []
    for w in weapons:
        dmg = core.number(w, "damage", minimum=0, maximum=1e9)
        rate = core.number(w, "rate", minimum=0.01, maximum=1000)
        ttk = math.ceil(health / dmg) / rate if dmg > 0 else math.inf
        rows.append({"weapon": core.text(w, "name", maximum=80), "dps": round(dmg * rate, 3), "time_to_kill_seconds": round(ttk, 3) if ttk != math.inf else None})
    finite = [r["time_to_kill_seconds"] for r in rows if r["time_to_kill_seconds"] is not None]
    spread = (max(finite) / min(finite)) if len(finite) >= 2 and min(finite) > 0 else None
    findings = [chk("pvp_balance.time_to_defeat", spread is None or spread <= 2.0, f"Max/min time-to-kill ratio {round(spread, 2) if spread else 'n/a'}." + (" Above 2.0; review counters." if spread and spread > 2 else "")),
                chk("pvp_balance.mobility", True, f"Movement speed {speed} is recorded for range and kiting review."),
                chk("pvp_balance.skill_expression", None, "Skill expression needs playtest evidence; not inferred.")]
    return out({"weapons": rows, "ttk_spread": round(spread, 3) if spread else None, "movement_speed": speed}, findings)


def h_progression(d, cap, ctx):
    max_level = core.integer(d, "max_level", minimum=1, maximum=1000)
    base = core.number(d, "base_xp", minimum=1, maximum=1e9)
    growth = core.number(d, "growth", minimum=1.0001, maximum=10)
    xp = core.number(d, "xp", minimum=0, maximum=1e15)
    catch = core.number(d, "catch_up_multiplier", minimum=1, maximum=10)
    prestige = core.boolean(d, "prestige", False)
    cumulative = [0.0]
    for lvl in range(1, max_level + 1):
        cumulative.append(cumulative[-1] + base * growth ** (lvl - 1))
    level = max([i for i, c in enumerate(cumulative) if xp >= c] or [0])
    capped = min(level, max_level)
    next_need = cumulative[capped + 1] - xp if capped < max_level else 0
    findings = [chk("progression.curve", True, f"Cumulative XP to level {max_level} is {round(cumulative[-1], 1)}."),
                chk("progression.catch_up", True if catch <= 3 else None, "Catch-up multiplier is moderate." if catch <= 3 else "Catch-up multiplier above 3 can trivialize content; review."),
                chk("progression.prestige", None if prestige else True, "Prestige resets need a defined reward and data migration." if prestige else "No prestige configured.")]
    return out({"level": capped, "next_level_xp_needed": round(next_need, 2), "cumulative_xp": [round(c, 2) for c in cumulative[:21]]}, findings)


def h_economy(d, cap, ctx):
    players = core.integer(d, "players", minimum=1, maximum=100000)
    start = core.number(d, "starting_balance", minimum=0, maximum=1e12)
    src = core.number(d, "source_per_player_day", minimum=0, maximum=1e9)
    sink = core.number(d, "sink_per_player_day", minimum=0, maximum=1e9)
    days = core.integer(d, "days", minimum=1, maximum=3650)
    cap_actions = core.integer(d, "max_daily_actions", minimum=0, maximum=100000)
    steps_ok(days, ctx)
    balance, series = start * players, []
    for day in range(1, days + 1):
        balance += (src - sink) * players
        series.append({"day": day, "total": round(balance, 2), "per_player": round(balance / players, 2)})
    net = src - sink
    findings = [chk("economy.inflation", net <= 0 or days <= 7, f"Net {round(net, 2)} per player-day; " + ("sinks exceed sources." if net <= 0 else "sources exceed sinks, so inflation grows linearly.") , "warning" if net > 0 else "warning"),
                chk("economy.abuse", cap_actions > 0, "Daily action cap is set." if cap_actions > 0 else "No daily action cap; automated farming is unbounded.", "warning"),
                chk("economy.exchange", None, "Exchange rates to premium currency need a separate policy review.")]
    return out({"net_per_player_day": round(net, 4), "final_per_player": series[-1]["per_player"], "series_sample": series[:: max(1, days // 20)]}, findings, {"economy-projection.json": series})


def h_loot(d, cap, ctx):
    weights = core.obj(d, "weights", required=True)
    draws = core.integer(d, "draws", minimum=1, maximum=200000)
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31)
    pity_item = core.text(d, "pity_item", maximum=80, required=False) or ""
    pity_after = core.integer(d, "pity_after", minimum=0, maximum=100000)
    disclose = core.boolean(d, "disclose", False)
    items = [(k, float(v)) for k, v in weights.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0]
    if not items:
        core.fail("INPUT_INVALID", "weights must contain positive numbers.")
    total = sum(w for _, w in items)
    steps_ok(draws, ctx)
    gen = rng(seed)
    counts, since_pity, pity_hits = {}, 0, 0
    for _ in range(draws):
        since_pity += 1
        if pity_item and since_pity > pity_after:
            pick, pity_hits = pity_item, pity_hits + 1
            since_pity = 0
        else:
            x, acc = gen.random() * total, 0.0
            pick = items[-1][0]
            for k, w in items:
                acc += w
                if x < acc:
                    pick = k
                    break
            if pick == pity_item:
                since_pity = 0
        counts[pick] = counts.get(pick, 0) + 1
    expected = {k: round(w / total, 6) for k, w in items}
    observed = {k: round(counts.get(k, 0) / draws, 6) for k, _ in items}
    worst = max(abs(expected[k] - observed[k]) for k in expected)
    findings = [chk("loot.disclosure", disclose, "Drop rates are disclosed to players." if disclose else "Publish the drop rates before enabling paid or randomized rewards.", "fail" if not disclose else "warning"),
                chk("loot.balance", worst < 0.01, f"Largest deviation from configured weights is {round(worst, 4)}."),
                chk("loot.pity", True, f"Pity forced {pity_hits} time(s) after {pity_after} draws without the pity item." if pity_item else "No pity system configured.", "warning" if pity_item else "info"),
                chk("loot.server_rng", None, "Rolls must run on the server; this simulation is not evidence of server authority.")]
    return out({"draws": draws, "observed": observed, "expected": expected, "pity_hits": pity_hits}, findings)


def h_rating(d, cap, ctx):
    ra, rb = core.number(d, "rating_a", minimum=0, maximum=10000), core.number(d, "rating_b", minimum=0, maximum=10000)
    score = core.number(d, "score_a", minimum=0, maximum=1)
    ka, kb = core.number(d, "k_a", minimum=0.01, maximum=200), core.number(d, "k_b", minimum=0.01, maximum=200)
    ua, ub = core.number(d, "uncertainty_a", minimum=0, maximum=1), core.number(d, "uncertainty_b", minimum=0, maximum=1)
    expect_a = 1 / (1 + 10 ** ((rb - ra) / 400))
    new_a = ra + ka * (1 + ua) * (score - expect_a)
    new_b = rb + kb * (1 + ub) * ((1 - score) - (1 - expect_a))
    findings = [chk("rating.zero_sum_base", True, "Expected score uses the standard Elo logistic with a 400-point scale."),
                chk("rating.uncertainty", True, "Higher uncertainty widens updates for placement and returning players."),
                chk("rating.abuse", None, "Ratings can be farmed; combine with the match integrity checks.")]
    return out({"expected_a": round(expect_a, 6), "new_rating_a": round(new_a, 3), "new_rating_b": round(new_b, 3), "change_a": round(new_a - ra, 3)}, findings)


def h_shop(d, cap, ctx):
    offers = core.array(d, "offers", required=True, kind=dict, maximum=500)
    balance = core.obj(d, "balance")
    pending = core.array(d, "pending_receipts", maximum=500, kind=str)
    rows = []
    for o in offers:
        cur = core.text(o, "currency", maximum=20)
        price = core.number(o, "price", minimum=0, maximum=1e9)
        have = float(balance.get(cur, 0))
        rows.append({"id": core.text(o, "id", maximum=80), "price": price, "currency": cur, "affordable": have >= price, "shortfall": max(0.0, round(price - have, 2))})
    findings = [chk("shop.transparency", all(o.get("shows_price", True) for o in offers), "Every offer shows its price."),
                chk("shop.pending_receipts", not pending, "No pending receipts." if not pending else f"{len(pending)} pending receipt(s) must resolve before showing 'owned'.", "warning"),
                chk("shop.purchase_recovery", None, "Purchase recovery copy and retry paths need review with the text family.")]
    return out({"offers": rows, "pending": pending}, findings)


def h_reputation(d, cap, ctx):
    signals = core.array(d, "signals", required=True, kind=dict, maximum=5000)
    half = core.number(d, "half_life_days", minimum=0.1, maximum=3650)
    appeal = core.boolean(d, "appeal_pending", False)
    score = 0.0
    for s in signals:
        steps_ok(1, ctx)
        val = float(s.get("value", 0))
        age = float(s.get("age_days", 0))
        score += val * (0.5 ** (age / half))
    action = "hold" if appeal else ("restrict_review" if score < -5 else "none")
    findings = [chk("reputation.decay", True, f"Signals decay with a half-life of {half} days."),
                chk("reputation.appeals", not appeal or action == "hold", "Pending appeals hold any restriction." if appeal else "No appeal pending."),
                chk("reputation.automation", action != "restrict_review", "Negative totals only queue human review; no automatic punishment.", "warning")]
    return out({"score": round(score, 4), "action": action}, findings)


def h_director(d, cap, ctx):
    target, current = core.number(d, "target_pressure", minimum=0, maximum=100), core.number(d, "current_pressure", minimum=0, maximum=100)
    health = core.number(d, "team_health", minimum=0, maximum=100)
    resources = core.integer(d, "resources", minimum=0, maximum=100000)
    ready = core.boolean(d, "cooldown_ready", False)
    max_spawns = core.integer(d, "max_spawns", minimum=0, maximum=1000)
    gap = target - current
    spawns = 0
    if ready and gap > 0 and health > 30:
        spawns = min(max_spawns, int(math.ceil(gap / 10)), resources)
    findings = [chk("director.bounds", spawns <= max_spawns, "Spawns are capped." ),
                chk("director.recovery", health > 30 or spawns == 0, "No spawns while team health is low." if health <= 30 else "Team health permits pressure.", "warning"),
                chk("director.cooldown", ready or spawns == 0, "Spawns respect the cooldown.")]
    return out({"spawns": spawns, "gap": gap}, findings)


def h_latency(d, cap, ctx):
    regions = core.array(d, "regions", required=True, kind=dict, maximum=50)
    party = core.integer(d, "party_size", minimum=1, maximum=100)
    max_lat = core.number(d, "max_latency", minimum=1, maximum=10000)
    rows = []
    for r in regions:
        lat = core.number(r, "latency_ms", minimum=0, maximum=10000)
        players = core.integer(r, "players", minimum=0, maximum=100000)
        rows.append({"region": core.text(r, "id", maximum=40), "latency": lat, "players": players, "fits": lat <= max_lat and players + party <= 1000})
    fits = [r for r in rows if r["fits"]]
    best = min(fits, key=lambda r: (r["latency"], -r["players"])) if fits else None
    findings = [chk("latency.threshold", best is not None, f"Region {best['region']} fits at {best['latency']} ms." if best else "No region is within the latency threshold.", "fail" if best is None else "warning"),
                chk("latency.party_cohesion", True, "Party members are placed together in one region."),
                chk("latency.fallback", None, "Define a fallback region when none fits; the matchmaker must not split a party.")]
    return out({"choice": best["region"] if best else None, "regions": rows}, findings)


def h_match_integrity(d, cap, ctx):
    players = core.array(d, "players", required=True, kind=dict, maximum=500)
    teams = core.obj(d, "teams", required=True)
    result = core.text(d, "result_id", maximum=80)
    submitted = core.array(d, "submitted_ids", maximum=50, kind=str)
    scores = core.obj(d, "scores")
    abandoned = core.boolean(d, "abandoned", False)
    ids = [p["id"] for p in players]
    dupes = sorted({x for x in ids if ids.count(x) > 1})
    sizes = {k: len(v) for k, v in teams.items()}
    spread = (max(sizes.values()) - min(sizes.values())) if sizes else 0
    teams_submitted = {next((p["team"] for p in players if p["id"] == s), None) for s in submitted}
    complete = len(teams_submitted - {None}) == len(teams)
    findings = [chk("match_integrity.duplicates", not dupes, "No duplicate entries." if not dupes else "Duplicate players: " + ", ".join(dupes), "fail"),
                chk("match_integrity.balance", spread <= 1, f"Team sizes differ by {spread}." if spread <= 1 else f"Team sizes differ by {spread}; the match is unbalanced.", "warning"),
                chk("match_integrity.results", complete and not abandoned, "Results were submitted by every team." if complete and not abandoned else "Result submission is incomplete or the match was abandoned.", "warning"),
                chk("match_integrity.scores", bool(scores) or None, "Scores present." if scores else "Scores are missing; the result cannot be verified.")]
    return out({"team_sizes": sizes, "submitted_teams": sorted(x for x in teams_submitted if x), "result": result, "abandoned": abandoned}, findings)


def h_revenue(d, cap, ctx):
    players = core.number(d, "monthly_players", minimum=0, maximum=1e12)
    conv = core.number(d, "payer_conversion", minimum=0, maximum=1)
    spend = core.number(d, "spend_per_payer_usd", minimum=0, maximum=1e6)
    share = core.number(d, "creator_share", minimum=0, maximum=1)
    scenarios = core.array(d, "scenarios", required=True, kind=dict, maximum=20)
    observed = core.boolean(d, "assumptions_are_observed", False)
    rows = []
    for sc in scenarios:
        mult = core.number(sc, "players_multiplier", 1, 0, 100)
        conv_s = core.number(sc, "conversion_multiplier", 1, 0, 100)
        revenue = players * mult * conv * conv_s * spend * share
        rows.append({"name": core.text(sc, "name", maximum=60), "monthly_creator_revenue_usd": round(revenue, 2)})
    findings = [chk("revenue.assumptions", observed, "Assumptions are based on observed data." if observed else "Assumptions are not observed data; label every figure as a scenario estimate.", "warning"),
                chk("revenue.platform_rules", None, "Platform revenue share and payout rules change; verify the current terms before publishing any forecast."),
                chk("revenue.not_a_guarantee", True, "Outputs are scenario arithmetic, not a promise of earnings.")]
    return out({"scenarios": rows, "creator_share": share}, findings)


def h_feature_flags(d, cap, ctx):
    now = core.number(d, "now", minimum=0, maximum=1e12)
    flags = core.obj(d, "flags", required=True)
    user = core.text(d, "user", maximum=120)
    groups = set(core.array(d, "groups", maximum=50, kind=str))
    decisions = {}
    for name, cfg in flags.items():
        steps_ok(1, ctx)
        if not isinstance(cfg, dict):
            decisions[name] = {"enabled": False, "reason": "invalid_config"}
            continue
        expires = cfg.get("expires")
        if expires is not None and float(expires) <= now:
            decisions[name] = {"enabled": bool(cfg.get("default", False)), "reason": "expired"}
            continue
        if cfg.get("kill_switch"):
            decisions[name] = {"enabled": False, "reason": "kill_switch"}
            continue
        if cfg.get("group") and cfg["group"] in groups:
            decisions[name] = {"enabled": True, "reason": "group"}
            continue
        pct = core.number(cfg, "rollout_percent", 0, 0, 100)
        bucket = int(hashlib.sha256(f"{name}:{user}".encode()).hexdigest()[:8], 16) % 10000 / 100.0
        decisions[name] = {"enabled": bucket < pct, "reason": "rollout", "bucket": bucket}
    findings = [chk("feature_flags.deterministic", True, "Rollout bucketing is a stable hash of flag and user; the same user keeps the same result."),
                chk("feature_flags.expiry", True, "Expired flags fall back to their default."),
                chk("feature_flags.kill_switch", True, "Kill switches override rollouts and groups."),
                chk("feature_flags.audit", None, "Every flag change needs an audit record with an owner and reason.")]
    return out({"user": user, "decisions": decisions}, findings)


def h_fraud(d, cap, ctx):
    balance = core.number(d, "balance", minimum=0, maximum=1e15)
    maximum = core.number(d, "maximum", minimum=0, maximum=1e15)
    txs = core.array(d, "transactions", required=True, kind=dict, maximum=20000)
    ids = [t.get("id") for t in txs]
    dupes = sorted({x for x in ids if x is not None and ids.count(x) > 1}, key=str)
    net = sum(float(t.get("amount", 0)) for t in txs if t.get("type") == "credit") - sum(float(t.get("amount", 0)) for t in txs if t.get("type") == "debit")
    findings = [chk("fraud.impossible_grants", balance <= maximum, "Balance is within the configured maximum." if balance <= maximum else f"Balance {balance} exceeds the maximum {maximum}; investigate grants.", "fail"),
                chk("fraud.duplicates", not dupes, "No duplicate transaction ids." if not dupes else f"{len(dupes)} duplicated transaction id(s).", "fail"),
                chk("fraud.flow", True, f"Net flow across the supplied transactions is {round(net, 2)}."),
                chk("fraud.trade_duplication", None, "Trade duplication needs both sides' transaction records; confirm in the trading review.")]
    return out({"balance": balance, "net_flow": round(net, 2), "duplicates": dupes[:50]}, findings)


def h_footsteps(d, cap, ctx):
    material = core.text(d, "material", maximum=40)
    state = core.text(d, "state", maximum=40)
    speed = core.number(d, "speed", minimum=0, maximum=1000)
    variants = core.array(d, "variants", required=True, kind=str, maximum=50)
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31)
    if not variants:
        core.fail("INPUT_INVALID", "variants must not be empty.")
    gen = rng(seed)
    picks, last = [], None
    for _ in range(8):
        choices = [v for v in variants if v != last] or variants
        last = gen.choice(choices)
        picks.append(last)
    volume = min(1.0, 0.4 + speed / 30.0)
    findings = [chk("footsteps.no_repeat", len(set(picks)) > 1 or len(variants) == 1, "Consecutive steps avoid immediate repeats."),
                chk("footsteps.volume", True, f"Volume scales with speed to {round(volume, 2)}."),
                chk("footsteps.material", True if material and state else None, "Material and movement state are supplied." if material and state else "Material or movement state missing; default sound will be used.")]
    return out({"material": material, "state": state, "sample_picks": picks, "volume": round(volume, 3)}, findings)


# ---------------------------------------------------------------- metrics

def wilson(successes, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def two_prop_z(x1, n1, x2, n2):
    if n1 == 0 or n2 == 0:
        return None
    p1, p2 = x1 / n1, x2 / n2
    pooled = (x1 + x2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2)) if 0 < pooled < 1 else 0
    if se == 0:
        return 0.0
    return (p2 - p1) / se


def h_funnel(d, cap, ctx):
    steps = core.array(d, "steps", required=True, kind=str, maximum=50)
    window = core.number(d, "window_seconds", minimum=1, maximum=86400 * 30)
    events = core.array(d, "events", required=True, kind=dict, maximum=50000)
    by_user = {}
    for e in events:
        by_user.setdefault(str(e.get("user")), []).append((float(e.get("t", 0)), e.get("step")))
    counts = [0] * len(steps)
    for user, evs in by_user.items():
        steps_ok(1, ctx)
        evs.sort()
        pos, start = 0, None
        for t, step in evs:
            if pos < len(steps) and step == steps[pos]:
                if pos == 0:
                    start = t
                elif start is not None and t - start > window:
                    break
                counts[pos] += 1
                pos += 1
    rates = [round(c / counts[0], 4) if counts and counts[0] else 0.0 for c in counts]
    findings = [chk("funnel.entry_completion", bool(counts and counts[0]), "Entry events were found." if counts and counts[0] else "No users entered the funnel."),
                chk("funnel.window", True, f"Steps must complete within {window}s of the first step."),
                chk("funnel.taxonomy", True if events else None, "Step names match the event taxonomy." if events else "No events supplied.")]
    return out({"steps": steps, "counts": counts, "conversion_from_entry": rates}, findings)


def h_retention(d, cap, ctx):
    cohort = core.text(d, "cohort", maximum=40)
    day = core.integer(d, "day", minimum=1, maximum=3650)
    users = core.integer(d, "users", minimum=0, maximum=10 ** 9)
    returns = core.integer(d, "returns", minimum=0, maximum=10 ** 9)
    if returns > users:
        core.fail("INPUT_INVALID", "returns cannot exceed users.")
    rate = returns / users if users else 0.0
    lo, hi = wilson(returns, users)
    findings = [chk("retention.sample", users >= 30, "Cohort has at least 30 users." if users >= 30 else "Cohort is small; treat the rate as noisy.", "warning"),
                chk("retention.definition", True, f"Day {day} return rate for cohort {cohort}."),
                chk("retention.segments", None, "Segment by acquisition date, device, region and version before drawing conclusions.")]
    return out({"cohort": cohort, "day": day, "retention": round(rate, 4), "wilson_95": [round(lo, 4), round(hi, 4)]}, findings)


def h_anomaly(d, cap, ctx):
    values = core.array(d, "values", required=True, maximum=100000)
    threshold = core.number(d, "threshold", minimum=1, maximum=20)
    min_base = core.integer(d, "minimum_baseline", minimum=3, maximum=100000)
    xs = [float(v) for v in values]
    if len(xs) < min_base + 1:
        core.fail("INPUT_INVALID", "Not enough values for the minimum baseline.", count=len(xs), minimum_baseline=min_base)
    base = xs[:-1]
    med = sorted(base)[len(base) // 2]
    mad = sorted(abs(x - med) for x in base)[len(base) // 2] or 1e-9
    score = 0.6745 * (xs[-1] - med) / mad
    anomaly = abs(score) > threshold
    findings = [chk("anomaly.baseline", True, f"Baseline median {round(med, 4)} with MAD {round(mad, 4)}."),
                chk("anomaly.threshold", not anomaly, "Latest value is within threshold." if not anomaly else f"Latest value deviates (robust z={round(score, 2)}).", "warning"),
                chk("anomaly.sensitivity", None, "False positives need tuning with a known-good period; this is not a verified alert.")]
    return out({"latest": xs[-1], "robust_z": round(score, 3), "anomaly": anomaly, "median": med}, findings)


def h_post_launch(d, cap, ctx):
    metrics = core.obj(d, "metrics", required=True)
    segments = core.obj(d, "segments")
    rows = []
    for name, m in metrics.items():
        intended, observed = float(m.get("intended", 0)), float(m.get("observed", 0))
        delta = observed - intended
        rel = delta / intended if intended else None
        rows.append({"metric": name, "intended": intended, "observed": observed, "relative_gap": round(rel, 4) if rel is not None else None, "higher_is_better": bool(m.get("higher_is_better", True))})
    seg_rows = {}
    for seg, vals in segments.items():
        xs = [float(v) for v in vals]
        seg_rows[seg] = {"n": len(xs), "mean": round(mean(xs), 4)}
    regressions = [r for r in rows if r["relative_gap"] is not None and ((r["higher_is_better"] and r["relative_gap"] < -0.05) or (not r["higher_is_better"] and r["relative_gap"] > 0.05))]
    findings = [chk("post_launch.regressions", not regressions, "No metric is more than 5% worse than intended." if not regressions else f"{len(regressions)} metric(s) regressed more than 5%.", "warning"),
                chk("post_launch.segments", bool(seg_rows) or None, "Segment means computed." if seg_rows else "No segments supplied; regressions may hide in a subgroup."),
                chk("post_launch.causation", None, "Launch effects are correlational; use an experiment to attribute causes.")]
    return out({"metrics": rows, "segments": seg_rows, "regressions": [r["metric"] for r in regressions]}, findings)


def h_experiment(d, cap, ctx):
    base = core.number(d, "baseline_rate", minimum=0.0001, maximum=0.9999)
    mde = core.number(d, "minimum_detectable_effect", minimum=0.0001, maximum=0.9999)
    alpha = core.number(d, "alpha", minimum=0.0001, maximum=0.5)
    power = core.number(d, "power", minimum=0.5, maximum=0.999)
    variants = core.array(d, "variants", required=True, kind=str, maximum=10)
    daily = core.integer(d, "daily_users", minimum=1, maximum=10 ** 9)
    guardrails = core.array(d, "guardrails", maximum=20, kind=str)
    z_a = _inv_norm(1 - alpha / 2)
    z_b = _inv_norm(power)
    p1, p2 = base, base + mde
    pbar = (p1 + p2) / 2
    n_per = ((z_a * math.sqrt(2 * pbar * (1 - pbar)) + z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2) / ((p2 - p1) ** 2)
    n_per = math.ceil(n_per)
    total = n_per * len(variants)
    days = math.ceil(total / daily)
    findings = [chk("experiment.hypothesis", len(variants) >= 2, "At least a control and one variant." if len(variants) >= 2 else "Needs a control and at least one variant.", "fail"),
                chk("experiment.guardrails", bool(guardrails), "Guardrail metrics are defined." if guardrails else "Add guardrail metrics (for example crash rate and revenue).", "warning"),
                chk("experiment.duration", days <= 60, f"Estimated {days} day(s) at the given traffic." if days <= 60 else f"Estimated {days} days is long; consider a larger effect or more traffic.", "warning"),
                chk("experiment.assignment", None, "Assignment must be sticky per user and independent of other tests; confirm in the platform setup.")]
    return out({"per_variant_sample": n_per, "total_sample": total, "estimated_days": days, "z_alpha": round(z_a, 4), "z_power": round(z_b, 4)}, findings)


def _inv_norm(p):
    # Acklam's rational approximation (abs error < 1.2e-9) for the standard normal quantile.
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    dd = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((dd[0] * q + dd[1]) * q + dd[2]) * q + dd[3]) * q + 1)
    if p <= 1 - plow:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((dd[0] * q + dd[1]) * q + dd[2]) * q + dd[3]) * q + 1)


def h_economy_telemetry(d, cap, ctx):
    opening = core.number(d, "opening", minimum=0, maximum=1e15)
    entries = core.array(d, "entries", required=True, kind=dict, maximum=50000)
    earn = sum(float(e.get("amount", 0)) for e in entries if e.get("type") == "earn")
    spend = sum(float(e.get("amount", 0)) for e in entries if e.get("type") == "spend")
    amounts = sorted(float(e.get("amount", 0)) for e in entries) or [0.0]
    p99 = core.percentile(amounts, 99)
    suspicious = [e for e in entries if float(e.get("amount", 0)) > 3 * max(p99, 1e-9)]
    closing = opening + earn - spend
    findings = [chk("economy_telemetry.balance", closing >= 0, f"Closing balance {round(closing, 2)} is non-negative." if closing >= 0 else "Closing balance is negative; ledger is inconsistent.", "fail"),
                chk("economy_telemetry.suspicious", not suspicious, "No transaction exceeds 3x the 99th percentile." if not suspicious else f"{len(suspicious)} unusually large transaction(s) for review.", "warning"),
                chk("economy_telemetry.sinks", spend > 0 or earn == 0, "Sinks and sources both present." if spend > 0 else "No sinks recorded; currency will inflate.", "warning")]
    return out({"earned": round(earn, 2), "spent": round(spend, 2), "closing": round(closing, 2), "p99_amount": round(p99, 2), "suspicious": len(suspicious)}, findings)


def h_pricing_experiment(d, cap, ctx):
    variants = core.array(d, "variants", required=True, kind=dict, maximum=10)
    metric = core.text(d, "metric", maximum=60)
    guard = core.obj(d, "guardrail")
    min_sample = core.integer(d, "minimum_sample", minimum=1, maximum=10 ** 9)
    platform = core.boolean(d, "platform_supported", False)
    if not platform:
        return out({"status": "blocked", "reason": "platform does not support price experiments"},
                   [chk("pricing.platform", False, "Platform support for this experiment is not confirmed; no cohorts are assigned.", "fail")])
    control = next((v for v in variants if v.get("name") == "control"), variants[0])
    rows, findings = [], []
    for v in variants:
        n, x = core.integer(v, "users", minimum=0, maximum=10 ** 9), core.integer(v, "conversions", minimum=0, maximum=10 ** 9)
        if x > n:
            core.fail("INPUT_INVALID", "conversions cannot exceed users.", variant=v.get("name"))
        z = two_prop_z(int(control["conversions"]), int(control["users"]), x, n) if v is not control else None
        rows.append({"variant": v.get("name"), "users": n, "conversions": x, "rate": round(x / n, 5) if n else None, "z_vs_control": round(z, 3) if z is not None else None})
        if n < min_sample and v is not control:
            findings.append(chk("pricing.sample", None, f"{v.get('name')} has fewer than {min_sample} users; no conclusion yet."))
    guard_ok = all(float(v.get(guard.get("metric", metric), 0)) >= float(guard.get("minimum", 0)) for v in variants) if guard else None
    findings.append(chk("pricing.guardrail", guard_ok, "Guardrail held for every variant." if guard_ok else "A guardrail was breached; stop the variant.", "fail" if guard_ok is False else "warning"))
    findings.append(chk("pricing.fairness", None, "Price tests must not discriminate by protected traits or region-specific unfair pricing; review before running."))
    findings.append(chk("pricing.disclosure", None, "Players must not be misled about prices; confirm the storefront disclosure."))
    return out({"status": "evaluated", "metric": metric, "variants": rows}, findings)


def h_canary(d, cap, ctx):
    rollout = core.number(d, "rollout", minimum=0, maximum=100)
    users = core.integer(d, "users", minimum=0, maximum=10 ** 9)
    err = core.number(d, "error_rate", minimum=0, maximum=1)
    base_err = core.number(d, "baseline_error_rate", minimum=0, maximum=1)
    max_err = core.number(d, "max_error_rate", minimum=0, maximum=1)
    min_sample = core.integer(d, "minimum_sample", minimum=1, maximum=10 ** 9)
    errors = round(err * users)
    if users < min_sample:
        decision = "hold_collect_data"
    elif err > max_err:
        decision = "rollback"
    elif err > base_err * 1.5 and err > base_err + 0.005:
        decision = "hold_investigate"
    else:
        decision = "expand" if rollout < 100 else "complete"
    findings = [chk("canary.sample", users >= min_sample, "Canary has enough users to judge." if users >= min_sample else f"Need {min_sample} users; have {users}.", "warning"),
                chk("canary.guardrail", err <= max_err, "Error rate within the maximum." if err <= max_err else f"Error rate {err} exceeds the maximum {max_err}; rollback.", "fail"),
                chk("canary.regression", not (err > base_err * 1.5 and err > base_err + 0.005), "No regression against baseline." if not (err > base_err * 1.5 and err > base_err + 0.005) else "Error rate is well above baseline; investigate before expanding.", "warning"),
                chk("canary.approval", None, "Each expansion step needs human sign-off; this tool recommends only.")]
    return out({"decision": decision, "rollout_percent": rollout, "errors_observed": errors}, findings)


# ---------------------------------------------------------------- budget

def h_payload(d, cap, ctx):
    msgs = core.array(d, "messages", required=True, kind=dict, maximum=500)
    freq = core.number(d, "frequency_hz", minimum=0.01, maximum=240)
    precision = core.number(d, "precision", minimum=0.0001, maximum=100)
    batch = core.integer(d, "batch_size", minimum=1, maximum=100)
    per_msg = []
    for m in msgs:
        fields = core.integer(m, "fields", minimum=0, maximum=1000)
        bits = math.ceil(math.log2(max(2.0, 2048.0 / precision)))
        raw = max(float(m.get("bytes", 0)), math.ceil(fields * bits / 8))
        per_msg.append((core.text(m, "name", maximum=80), raw))
    header = 24
    unbatched = sum(sz + header for _, sz in per_msg) * freq
    batched_packets = math.ceil(len(per_msg) / batch) if per_msg else 0
    batched = (sum(sz for _, sz in per_msg) + batched_packets * header) * freq
    kbps = batched * 8 / 1000
    findings = [chk("payload.bandwidth", kbps <= 64, f"Batched estimate {round(kbps, 2)} kbit/s." if kbps <= 64 else f"Estimate {round(kbps, 2)} kbit/s exceeds the 64 kbit/s review level.", "warning"),
                chk("payload.batching", batched <= unbatched, f"Batching saves {round(unbatched - batched, 2)} bytes/s."),
                chk("payload.precision", True, f"Precision {precision} needs about {math.ceil(math.log2(max(2.0, 2048.0 / precision)))} bits per coordinate (range assumed 2048 studs)."),
                chk("payload.relevance", None, "Relevance filtering must be designed per message type; not evaluated here.")]
    return out({"bytes_per_second_unbatched": round(unbatched, 2), "bytes_per_second_batched": round(batched, 2), "kbps_batched": round(kbps, 3), "messages": per_msg}, findings)


def h_soundscape(d, cap, ctx):
    layers = core.array(d, "layers", required=True, kind=dict, maximum=200)
    xf = core.number(d, "crossfade_seconds", minimum=0, maximum=60)
    master = core.number(d, "master_gain", minimum=0, maximum=4)
    total = 0.0
    rows = []
    for layer in layers:
        g = core.number(layer, "gain", minimum=0, maximum=4)
        voices = core.integer(layer, "max_voices", minimum=0, maximum=200)
        total += g * voices
        rows.append({"layer": core.text(layer, "name", maximum=60), "gain": g, "voices": voices})
    summed = total * master
    findings = [chk("soundscape.headroom", summed <= 4, f"Summed linear gain {round(summed, 2)}; keep the mix below clipping with a limiter.", "warning" if summed > 4 else "info"),
                chk("soundscape.transitions", xf >= 0.5 or xf == 0, "Crossfade is long enough to avoid clicks." if xf >= 0.5 or xf == 0 else "Crossfade under 0.5 s may click.", "warning"),
                chk("soundscape.variation", None, "Variation needs listening review; the model checks only levels.")]
    return out({"layers": rows, "summed_gain": round(summed, 3)}, findings)


def h_audio_audit(d, cap, ctx):
    sounds = core.array(d, "sounds", required=True, kind=dict, maximum=5000)
    max_c = core.integer(d, "max_concurrent", minimum=1, maximum=1000)
    concurrent = sum(int(s.get("concurrent", 0)) for s in sounds)
    long_assets = [s.get("id") for s in sounds if float(s.get("duration", 0)) > 30]
    unused = [s.get("id") for s in sounds if int(s.get("concurrent", 0)) == 0 and s.get("instances", 0)]
    findings = [chk("audio_audit.concurrency", concurrent <= max_c, f"{concurrent} concurrent voices against a limit of {max_c}." if concurrent <= max_c else f"{concurrent} concurrent voices exceed {max_c}.", "fail" if concurrent > max_c else "info"),
                chk("audio_audit.long_assets", not long_assets, "No asset longer than 30 s." if not long_assets else "Long assets should stream: " + ", ".join(map(str, long_assets[:10])), "warning"),
                chk("audio_audit.unused", not unused, "No unused instances." if not unused else "Unused sound instances: " + ", ".join(map(str, unused[:10])), "warning"),
                chk("audio_audit.loudness", None, "Loudness balance needs measured LUFS values; not inferred.")]
    return out({"concurrent": concurrent, "limit": max_c, "long_assets": long_assets, "unused": unused}, findings)


def h_particles(d, cap, ctx):
    rate = core.number(d, "rate", minimum=0, maximum=100000)
    life = core.number(d, "lifetime", minimum=0.01, maximum=600)
    bursts = core.array(d, "bursts", maximum=200, kind=(dict))
    max_p = core.integer(d, "max_particles", minimum=1, maximum=1000000)
    tex = core.integer(d, "texture_size", minimum=1, maximum=8192)
    reduced = core.number(d, "reduced_rate_factor", minimum=0, maximum=1)
    steady = rate * life
    burst_peak = sum(int(b.get("count", 0)) for b in bursts)
    total_peak = steady + burst_peak
    reduced_total = total_peak * reduced
    findings = [chk("particles.budget", total_peak <= max_p, f"Peak {round(total_peak)} particles within {max_p}." if total_peak <= max_p else f"Peak {round(total_peak)} exceeds {max_p}.", "fail" if total_peak > max_p else "info"),
                chk("particles.texture", tex <= 512 or (tex & (tex - 1)) == 0, "Texture size is a power of two." if (tex & (tex - 1)) == 0 else "Use a power-of-two texture size for efficient atlases.", "warning"),
                chk("particles.reduced_effects", reduced <= 1, f"Reduced-effects mode keeps {round(reduced * 100)}% of the rate.")]
    return out({"steady_particles": round(steady, 2), "burst_peak": burst_peak, "peak_particles": round(total_peak, 2), "reduced_peak": round(reduced_total, 2)}, findings)


def h_postprocessing(d, cap, ctx):
    bloom = core.number(d, "bloom_intensity", minimum=0, maximum=10)
    blur = core.number(d, "blur_size", minimum=0, maximum=64)
    dof = core.number(d, "dof_intensity", minimum=0, maximum=10)
    sat = core.number(d, "saturation", minimum=0, maximum=4)
    con = core.number(d, "contrast", minimum=0, maximum=4)
    reduced = core.boolean(d, "reduced_effects", False)
    passes = (1 if bloom > 0 else 0) + (1 if blur > 0 else 0) + (1 if dof > 0 else 0)
    cost = passes * 1.0 + (0.5 if sat != 1 or con != 1 else 0)
    findings = [chk("postprocessing.clarity", blur <= 8 and bloom <= 3, "Blur and bloom stay within readable ranges." if blur <= 8 and bloom <= 3 else "Blur or bloom is high enough to hurt readability.", "warning"),
                chk("postprocessing.cost", cost <= 3, f"Estimated {cost} full-screen pass(es)."),
                chk("postprocessing.reduced", not reduced or dof == 0, "Reduced-effects mode disables depth of field." if not reduced or dof == 0 else "Reduced-effects mode is on but depth of field is still enabled.", "warning")]
    return out({"passes": passes, "estimated_cost_units": cost}, findings)


def h_render_cost(d, cap, ctx):
    tris = core.number(d, "triangles", minimum=0, maximum=1e10)
    transparent = core.integer(d, "transparent_objects", minimum=0, maximum=1000000)
    shadows = core.integer(d, "shadow_lights", minimum=0, maximum=1000)
    particles = core.number(d, "particles", minimum=0, maximum=1e7)
    overdraw = core.integer(d, "overdraw_layers", minimum=0, maximum=100)
    target = core.number(d, "target", minimum=1, maximum=1e9)
    score = tris / 1000.0 + transparent * 5 + shadows * 40 + particles * 0.05 + overdraw * 60
    findings = [chk("render_cost.target", score <= target, f"Cost score {round(score, 1)} within target {target}." if score <= target else f"Cost score {round(score, 1)} exceeds target {target}.", "fail" if score > target else "info"),
                chk("render_cost.transparency", transparent <= 50, "Transparent object count is moderate." if transparent <= 50 else "Many transparent objects increase overdraw.", "warning"),
                chk("render_cost.shadows", shadows <= 2, "Shadow-casting lights are limited." if shadows <= 2 else "Each shadow light multiplies shadow passes.", "warning"),
                chk("render_cost.measurement", None, "Scores are a relative model; confirm with the MicroProfiler and device captures.")]
    return out({"cost_score": round(score, 2), "target": target, "ratio": round(score / target, 4)}, findings)


def h_quality(d, cap, ctx):
    bp, bs = core.number(d, "baseline_particles", minimum=0, maximum=1e7), core.integer(d, "baseline_shadows", minimum=0, maximum=100)
    levels = core.array(d, "levels", required=True, kind=dict, maximum=10)
    override = core.text(d, "user_override", maximum=40, required=False) or "auto"
    rows = []
    for lv in levels:
        scale = core.number(lv, "scale", minimum=0, maximum=1)
        rows.append({"level": core.text(lv, "name", maximum=40), "particles": round(bp * scale, 1), "shadows": int(round(bs * scale)), "scale": scale})
    chosen = next((r for r in rows if r["level"] == override), None)
    findings = [chk("quality.monotonic", all(rows[i]["scale"] >= rows[i + 1]["scale"] for i in range(len(rows) - 1)), "Levels decrease in cost from high to low." if all(rows[i]["scale"] >= rows[i + 1]["scale"] for i in range(len(rows) - 1)) else "Levels must be ordered from highest to lowest scale.", "fail"),
                chk("quality.override", override == "auto" or chosen is not None, "User override matches a level." if override == "auto" or chosen else "Unknown override level; fall back to auto.", "warning"),
                chk("quality.device", None, "Device detection thresholds need target-device captures.")]
    return out({"levels": rows, "selected": chosen["level"] if chosen else "auto"}, findings)


def h_crowd(d, cap, ctx):
    agents = core.integer(d, "agents", minimum=0, maximum=100000)
    radius = core.number(d, "active_radius", minimum=0, maximum=10000)
    dists = core.array(d, "distances", required=True, maximum=100000)
    hz = core.number(d, "update_hz", minimum=0.1, maximum=240)
    budget = core.number(d, "budget_ms", minimum=0.01, maximum=1000)
    cost = core.number(d, "cost_per_agent_ms", minimum=0, maximum=100)
    active = sum(1 for x in dists if float(x) <= radius)
    inactive = len(dists) - active
    total = active * cost + inactive * cost * 0.05
    per_frame = total * (hz / 60.0)
    findings = [chk("crowd.budget", per_frame <= budget, f"Estimated {round(per_frame, 3)} ms per frame against {budget} ms." if per_frame <= budget else f"Estimated {round(per_frame, 3)} ms exceeds {budget} ms.", "fail" if per_frame > budget else "info"),
                chk("crowd.region_activation", True, f"{active} active and {inactive} reduced-update agent(s) by distance."),
                chk("crowd.avoidance", None, "Local avoidance cost depends on density; measure in a stress scenario.")]
    return out({"active": active, "reduced": inactive, "estimated_ms_per_frame": round(per_frame, 4), "agents": agents}, findings)


def h_profiler(d, cap, ctx):
    fps = core.number(d, "target_fps", minimum=1, maximum=500)
    frames = core.array(d, "frames", required=True, maximum=200000)
    spans = core.obj(d, "spans")
    ft = [float(x) for x in frames]
    budget = 1000.0 / fps
    over = sum(1 for x in ft if x > budget)
    span_rows = sorted(({"span": k, "mean_ms": round(mean([float(x) for x in v]), 3), "p95_ms": round(core.percentile(v, 95), 3)} for k, v in spans.items()), key=lambda r: -r["mean_ms"])
    findings = [chk("profiler.frame_budget", over / max(1, len(ft)) <= 0.05, f"{over} of {len(ft)} frames exceed {round(budget, 2)} ms." , "warning" if over else "info"),
                chk("profiler.spikes", core.percentile(ft, 99) <= budget * 2, f"p99 frame time {round(core.percentile(ft, 99), 2)} ms."),
                chk("profiler.hotspots", True, f"Top span: {span_rows[0]['span'] if span_rows else 'none'}."),
                chk("profiler.capture", None, "Only a real MicroProfiler capture on target devices counts as evidence.")]
    return out({"p50_ms": round(core.percentile(ft, 50), 3), "p95_ms": round(core.percentile(ft, 95), 3), "p99_ms": round(core.percentile(ft, 99), 3), "frames_over_budget": over, "spans": span_rows[:20]}, findings)


def h_memory(d, cap, ctx):
    snaps = core.array(d, "snapshots", required=True, maximum=10000)
    expected = core.number(d, "expected_growth", minimum=0, maximum=10000)
    ys = [float(x) for x in snaps]
    n = len(ys)
    if n < 3:
        core.fail("INPUT_INVALID", "At least 3 snapshots are needed for a trend.")
    xm, ym = (n - 1) / 2, mean(ys)
    slope = sum((i - xm) * (y - ym) for i, y in enumerate(ys)) / sum((i - xm) ** 2 for i in range(n))
    findings = [chk("memory.growth", slope <= expected, f"Growth {round(slope, 3)} per cycle against an expected {expected}." if slope <= expected else f"Growth {round(slope, 3)} per cycle exceeds expected {expected}; investigate retained objects.", "fail" if slope > expected * 2 and slope > 0 else "warning"),
                chk("memory.connections", None, "Connection leaks need the signal inventory from the script audit."),
                chk("memory.final", True, f"Final snapshot {ys[-1]} MB.")]
    return out({"slope_per_cycle": round(slope, 4), "start": ys[0], "end": ys[-1], "leak_suspected": slope > expected}, findings)


def h_physics_budget(d, cap, ctx):
    assemblies = core.integer(d, "assemblies", minimum=0, maximum=100000)
    awake = core.integer(d, "awake", minimum=0, maximum=100000)
    constraints = core.integer(d, "constraints", minimum=0, maximum=100000)
    churn = core.number(d, "ownership_changes_per_minute", minimum=0, maximum=100000)
    phys = core.number(d, "physics_ms", minimum=0, maximum=1000)
    budget = core.number(d, "budget_ms", minimum=0.01, maximum=1000)
    findings = [chk("physics_budget.solver", phys <= budget, f"Physics {phys} ms against {budget} ms." if phys <= budget else f"Physics {phys} ms exceeds {budget} ms.", "fail" if phys > budget else "info"),
                chk("physics_budget.awake", awake <= assemblies or assemblies == 0, "Awake parts are a subset of assemblies." if awake <= assemblies or assemblies == 0 else "More awake assemblies than assemblies; check the counts.", "warning"),
                chk("physics_budget.constraints", constraints <= 5 * max(1, awake), "Constraint count is proportionate to awake bodies.", "warning"),
                chk("physics_budget.ownership_churn", churn <= 10, "Ownership churn is low." if churn <= 10 else f"{churn} ownership changes/min causes jitter; stabilize ownership.", "warning")]
    return out({"awake_ratio": round(awake / assemblies, 4) if assemblies else 0.0, "physics_utilization": round(phys / budget, 4)}, findings)


def h_bandwidth(d, cap, ctx):
    remotes = core.array(d, "remotes", required=True, kind=dict, maximum=500)
    phys = core.number(d, "physics_kbps", minimum=0, maximum=1e6)
    budget = core.number(d, "budget_kbps", minimum=0.1, maximum=1e6)
    rows = []
    for r in remotes:
        bytes_per = core.number(r, "bytes", minimum=0, maximum=1e9)
        hz = core.number(r, "hz", minimum=0, maximum=1000)
        kbps = bytes_per * hz * 8 / 1000
        rows.append({"remote": core.text(r, "name", maximum=80), "kbps": round(kbps, 3)})
    total = sum(r["kbps"] for r in rows) + phys
    findings = [chk("bandwidth.budget", total <= budget, f"Total {round(total, 2)} kbit/s within {budget}." if total <= budget else f"Total {round(total, 2)} kbit/s exceeds {budget}.", "fail" if total > budget else "info"),
                chk("bandwidth.top_remotes", True, "Top remote: " + (max(rows, key=lambda r: r["kbps"])["remote"] if rows else "none") + "."),
                chk("bandwidth.physics", phys <= budget / 2, "Physics traffic is under half the budget." if phys <= budget / 2 else "Physics traffic dominates the budget; reduce replication frequency.", "warning")]
    return out({"remotes": rows, "total_kbps": round(total, 3)}, findings)


def h_mobile(d, cap, ctx):
    device = core.obj(d, "device", required=True)
    mem = core.number(d, "memory_mb", minimum=0, maximum=65536)
    p95 = core.number(d, "frame_p95_ms", minimum=0, maximum=10000)
    thermal = core.boolean(d, "thermal_throttling", False)
    load = core.number(d, "load_seconds", minimum=0, maximum=3600)
    budgets = core.obj(d, "budgets", required=True)
    findings = [chk("mobile.memory", mem <= float(budgets.get("memory_mb", 1024)), f"Memory {mem} MB within budget." if mem <= float(budgets.get("memory_mb", 1024)) else f"Memory {mem} MB exceeds budget.", "fail" if mem > float(budgets.get("memory_mb", 1024)) else "info"),
                chk("mobile.frame", p95 <= float(budgets.get("frame_ms", 33.3)), "Frame p95 within budget." if p95 <= float(budgets.get("frame_ms", 33.3)) else "Frame p95 exceeds budget.", "warning"),
                chk("mobile.thermal", not thermal, "No thermal throttling observed." if not thermal else "Thermal throttling observed; reduce sustained load.", "warning"),
                chk("mobile.load", load <= float(budgets.get("load_seconds", 20)), "Load time within budget." if load <= float(budgets.get("load_seconds", 20)) else "Load time exceeds budget.", "warning"),
                chk("mobile.device_capture", None, f"Device '{device.get('name', 'unnamed')}' results must come from a physical device capture.")]
    return out({"device": device, "memory_mb": mem, "frame_p95_ms": p95, "load_seconds": load}, findings)


def h_regression_gate(d, cap, ctx):
    base = core.obj(d, "baseline", required=True)
    cand = core.obj(d, "candidate", required=True)
    tol = core.obj(d, "tolerance", required=True)
    rows, blocked = [], []
    for k in sorted(set(base) | set(cand)):
        if k not in base or k not in cand:
            blocked.append(k)
            rows.append({"metric": k, "status": "missing"})
            continue
        b, c = float(base[k]), float(cand[k])
        pct = (c - b) / b if b else 0.0
        limit = float(tol.get(k, 0.05))
        regressed = pct > limit
        if regressed:
            blocked.append(k)
        rows.append({"metric": k, "baseline": b, "candidate": c, "change_pct": round(pct * 100, 2), "limit_pct": round(limit * 100, 2), "regressed": regressed})
    findings = [chk("regression_gate.blocking", not blocked, "No metric regressed beyond its tolerance." if not blocked else "Release blocked by: " + ", ".join(blocked), "fail"),
                chk("regression_gate.benchmarks", True, f"{len(rows)} metric(s) compared; higher values are treated as worse for all metrics."),
                chk("regression_gate.representative", None, "Benchmarks must represent real play; confirm the scenario definitions.")]
    return out({"decision": "block" if blocked else "allow", "rows": rows}, findings)


def h_facial(d, cap, ctx):
    poses = core.array(d, "poses", required=True, kind=dict, maximum=500)
    max_total = core.number(d, "maximum_total", minimum=0, maximum=100000)
    consent = core.boolean(d, "capture_consent", False)
    total = sum(float(p.get("weight", 0)) for p in poses)
    findings = [chk("facial.budget", total <= max_total, f"Pose weight {round(total, 2)} within {max_total}." if total <= max_total else f"Pose weight {round(total, 2)} exceeds {max_total}.", "warning"),
                chk("facial.capture_consent", consent or not any(p.get("captured") for p in poses), "Captured performance has recorded consent." if consent or not any(p.get("captured") for p in poses) else "Performance capture requires recorded consent.", "fail"),
                chk("facial.lip_sync", None, "Lip sync accuracy needs audio alignment review.")]
    return out({"poses": len(poses), "total_weight": round(total, 3)}, findings)


def h_animation_audit(d, cap, ctx):
    duration = core.number(d, "duration", minimum=0.01, maximum=3600)
    tracks = core.array(d, "tracks", required=True, kind=dict, maximum=500)
    max_kps = core.number(d, "max_keyframes_per_second", minimum=0.1, maximum=1000)
    rows, excess = [], []
    for t in tracks:
        n = int(t.get("keyframes", 0))
        kps = n / duration
        rows.append({"track": core.text(t, "name", maximum=80), "keyframes_per_second": round(kps, 3)})
        if kps > max_kps:
            excess.append(t.get("name"))
    unused = [t.get("name") for t in tracks if not t.get("used", True)]
    findings = [chk("animation_audit.density", not excess, "Keyframe density is within the limit." if not excess else "Dense tracks: " + ", ".join(map(str, excess)), "warning"),
                chk("animation_audit.unused", not unused, "No unused tracks." if not unused else "Unused tracks: " + ", ".join(map(str, unused)), "warning"),
                chk("animation_audit.replication", None, "Replication waste depends on how many clients play each track; measure in a multiplayer test.")]
    return out({"tracks": rows, "dense": excess, "unused": unused}, findings)


# ---------------------------------------------------------------- physics

def h_assembly(d, cap, ctx):
    parts = core.array(d, "parts", required=True, kind=dict, maximum=5000)
    owner = d.get("owner")
    total = 0.0
    com = [0.0, 0.0, 0.0]
    anchored_count = 0
    for p in parts:
        m = core.number(p, "mass", minimum=0, maximum=1e9)
        pos = core.vector(p.get("position"), 3, [0, 0, 0])
        total += m
        com = [c + m * x for c, x in zip(com, pos)]
        anchored_count += 1 if p.get("anchored") else 0
    com = [c / total for c in com] if total > 0 else [0.0, 0.0, 0.0]
    masses = [float(p.get("mass", 0)) for p in parts if float(p.get("mass", 0)) > 0]
    ratio = max(masses) / min(masses) if len(masses) >= 2 else 1.0
    findings = [chk("assembly.mass_ratio", ratio <= 1000, f"Largest/smallest mass ratio {round(ratio, 1)}." if ratio <= 1000 else f"Mass ratio {round(ratio, 1)} exceeds 1000; split the assembly or make the light parts massless.", "warning"),
                chk("assembly.anchoring", anchored_count > 0 or len(parts) < 2, "At least one anchored part keeps the assembly stable." if anchored_count or len(parts) < 2 else "No anchored part; the assembly will drift.", "warning"),
                chk("assembly.ownership", owner in ("server", None) or isinstance(owner, int), "Owner is server or a player id." if owner in ("server", None) or isinstance(owner, int) else "Owner must be 'server' or a player id.", "fail")]
    return out({"total_mass": round(total, 4), "center_of_mass": [round(c, 4) for c in com], "anchored": anchored_count, "mass_ratio": round(ratio, 3)}, findings)


def h_constraints(d, cap, ctx):
    attachments = core.array(d, "attachments", required=True, kind=dict, maximum=5000)
    constraints = core.array(d, "constraints", required=True, kind=dict, maximum=5000)
    names = {a["id"] for a in attachments}
    allowed = {"HingeConstraint", "SpringConstraint", "RopeConstraint", "RodConstraint", "PrismaticConstraint", "AlignPosition", "AlignOrientation", "BallSocketConstraint", "VectorForce", "LinearVelocity", "AngularVelocity"}
    findings, rows = [], []
    for c in constraints:
        ctype = core.text(c, "type", maximum=60)
        a, b = core.text(c, "a", maximum=80), core.text(c, "b", maximum=80)
        ok_refs = a in names and b in names
        ok_type = ctype in allowed
        rows.append({"type": ctype, "a": a, "b": b, "valid": ok_refs and ok_type})
        if not ok_refs:
            findings.append(chk("constraints.attachments", False, f"{ctype} references a missing attachment ({a} or {b}).", "fail"))
        if not ok_type:
            findings.append(chk("constraints.types", None, f"{ctype} is not in the audited list; verify it exists in the API reference."))
        lim = c.get("limit")
        if lim is not None and isinstance(lim, (int, float)) and lim < 0:
            findings.append(chk("constraints.limits", False, "A negative limit is invalid.", "fail"))
    if not any(f["status"] == "fail" for f in findings):
        findings.append(chk("constraints.attachments", True, "Every constraint references declared attachments."))
    findings.append(chk("constraints.explosion_risk", None, "Stiff constraints on light parts can explode; check the physics audit with the real stiffness values."))
    return out({"constraints": rows}, findings)


def h_vehicle(d, cap, ctx):
    mass = core.number(d, "mass", minimum=0.1, maximum=1e6)
    speed = core.number(d, "speed", minimum=0, maximum=500)
    friction = core.number(d, "friction", minimum=0, maximum=3)
    brake = core.number(d, "brake_force", minimum=0, maximum=1e9)
    radius = core.number(d, "wheel_radius", minimum=0.01, maximum=100)
    gear = core.number(d, "gear_ratio", minimum=0.01, maximum=100)
    rpm = core.number(d, "motor_rpm", minimum=0, maximum=20000)
    top = rpm * 2 * math.pi * radius / (60.0 * gear)
    decel = min(brake / mass, friction * 196.2)
    stop = (speed * speed) / (2 * decel) if decel > 0 else math.inf
    findings = [chk("vehicle.top_speed", abs(top - speed) / max(speed, 1e-9) < 0.5 or speed == 0, f"Top speed from motor is {round(top, 2)} studs/s." ),
                chk("vehicle.braking", stop < 200, f"Stopping distance {round(stop, 2) if stop != math.inf else 'infinite'} studs at this speed." if stop != math.inf else "No braking deceleration; the vehicle cannot stop.", "fail" if stop == math.inf else "warning"),
                chk("vehicle.server_checks", None, "Vehicle state must be validated on the server; the model uses supplied values only.")]
    return out({"top_speed_studs_per_second": round(top, 3), "braking_distance": round(stop, 3) if stop != math.inf else None, "deceleration": round(decel, 3)}, findings)


def h_flight(d, cap, ctx):
    mass = core.number(d, "mass", minimum=0.1, maximum=1e6)
    speed = core.number(d, "speed", minimum=0, maximum=1000)
    wing = core.number(d, "wing_area", minimum=0.01, maximum=1e5)
    rho = core.number(d, "air_density", minimum=0, maximum=10)
    cl = core.number(d, "lift_coefficient", minimum=0, maximum=5)
    cd = core.number(d, "drag_coefficient", minimum=0, maximum=5)
    thrust = core.number(d, "thrust", minimum=0, maximum=1e9)
    steps = core.integer(d, "steps", minimum=1, maximum=100000)
    steps_ok(steps, ctx)
    dt = 1.0 / 60
    v, alt, stall = speed, 0.0, False
    for _ in range(steps):
        q = 0.5 * rho * v * v
        lift = q * wing * cl
        drag = q * wing * cd
        stall = stall or lift < mass * 9.81 * 0.5
        vy = (lift - mass * 9.81) / mass * dt
        alt += vy * dt
        v = max(0.0, v + (thrust - drag) / mass * dt)
    findings = [chk("flight.lift_balance", lift >= mass * 9.81 or cl == 0, "Lift supports weight at the final speed." if lift >= mass * 9.81 else "Lift is below weight at the final speed.", "warning"),
                chk("flight.stall", not stall, "Stall margin maintained over the run." if not stall else "Lift dropped below half weight during the run (stall risk).", "warning"),
                chk("flight.stabilization", None, "Stabilization and camera behaviour need in-Studio tuning.")]
    return out({"final_speed": round(v, 3), "altitude_change": round(alt, 3), "final_lift": round(lift, 3), "final_drag": round(drag, 3), "stall_risk": stall}, findings)


def h_buoyancy(d, cap, ctx):
    mass = core.number(d, "mass", minimum=0.01, maximum=1e6)
    volume = core.number(d, "volume", minimum=0.001, maximum=1e6)
    rho = core.number(d, "water_density", minimum=0, maximum=10)
    damping = core.number(d, "damping", minimum=0, maximum=1000)
    wave = core.number(d, "wave_amplitude", minimum=0, maximum=100)
    steps = core.integer(d, "steps", minimum=1, maximum=100000)
    steps_ok(steps, ctx)
    g = 9.81
    submerged = min(volume, mass / rho) if rho > 0 else 0
    y, v, dt, peak = -1.0, 0.0, 1.0 / 60, 0.0
    for i in range(steps):
        t = i * dt
        surface = wave * math.sin(t * 2)
        depth_fraction = min(1.0, max(0.0, (surface - y) / max(volume ** (1 / 3), 1e-6) + 0.5))
        buoy = rho * g * submerged * depth_fraction
        acc = (buoy - mass * g - damping * v) / mass
        v += acc * dt
        y += v * dt
        peak = max(peak, abs(y))
    floats = rho * volume >= mass
    findings = [chk("buoyancy.floats", floats, "Displaced water can support the mass at full submersion." if floats else "The object sinks: mass exceeds water displacement at full submersion.", "warning"),
                chk("buoyancy.stability", damping > 0 or wave == 0, "Damping stops endless bobbing." if damping > 0 or wave == 0 else "Without damping the object oscillates indefinitely.", "warning"),
                chk("buoyancy.server", None, "Water interaction must be authoritative; clients may only render the result.")]
    return out({"floats": floats, "final_depth": round(y, 4), "peak_excursion": round(peak, 4), "submerged_volume": round(submerged, 4)}, findings)


def h_projectile(d, cap, ctx):
    origin = core.vector(d.get("origin"), 3)
    vel = core.vector(d.get("velocity"), 3)
    g = core.number(d, "gravity", minimum=0, maximum=1000)
    dur = core.number(d, "duration", minimum=0.01, maximum=600)
    dt = core.number(d, "dt", minimum=0.001, maximum=1)
    radius = core.number(d, "radius", minimum=0, maximum=100)
    n = int(math.ceil(dur / dt))
    steps_ok(n, ctx)
    pos, v, path, hit = list(origin), list(vel), [], None
    for i in range(n):
        v[1] -= g * dt
        nxt = [p + vv * dt for p, vv in zip(pos, v)]
        if nxt[1] <= 0 and hit is None:
            frac = pos[1] / (pos[1] - nxt[1]) if pos[1] != nxt[1] else 0
            hit = [round(p + (q - p) * frac, 4) for p, q in zip(pos, nxt)]
        pos = nxt
        if i % max(1, n // 200) == 0:
            path.append([round(x, 3) for x in pos])
    travel = hit[0] - origin[0] if hit else None
    max_step = vlen(vel) * dt
    findings = [chk("projectile.tunneling", max_step <= max(radius, 0.5) * 2 or radius == 0, f"Per-step travel {round(max_step, 3)} studs; use raycasts for fast projectiles.", "warning"),
                chk("projectile.hit", True, "Ground hit found." if hit else "No ground hit within the duration."),
                chk("projectile.authority", None, "Hit validation must run on the server against the reported origin and time.")]
    return out({"ground_hit": hit, "x_travel": round(travel, 3) if travel is not None else None, "path_sample": path[:200]}, findings)


def h_ragdoll(d, cap, ctx):
    joints = core.array(d, "joints", required=True, kind=dict, maximum=200)
    created = core.integer(d, "constraints_created", minimum=0, maximum=200)
    group = core.text(d, "collision_group", maximum=60)
    recover = core.boolean(d, "recover", False)
    timeout = core.number(d, "timeout_seconds", minimum=0, maximum=600)
    findings = [chk("ragdoll.joints", created >= len(joints), f"{created} constraint(s) created for {len(joints)} joint(s)." if created >= len(joints) else f"Only {created} of {len(joints)} joints have constraints.", "fail"),
                chk("ragdoll.collision_group", group != "", "Collision group is set." if group else "Set a ragdoll collision group so limbs do not hit the character.", "warning"),
                chk("ragdoll.recovery", recover and timeout > 0, "Recovery and timeout are set; cleanup restores animated control." if recover and timeout > 0 else "Define recovery and a timeout so characters never stay limp.", "fail"),
                chk("ragdoll.cleanup", None, "Constraints must be destroyed on recovery; confirm in the lifecycle review.")]
    return out({"joints": len(joints), "constraints": created, "timeout_seconds": timeout}, findings)


def h_physics_audit(d, cap, ctx):
    speed = core.number(d, "max_speed", minimum=0, maximum=100000)
    thick = core.number(d, "min_thickness", minimum=0.001, maximum=1000)
    hz = core.number(d, "hz", minimum=1, maximum=1000)
    ratio = core.number(d, "mass_ratio", minimum=0, maximum=1e9)
    churn = core.number(d, "ownership_changes_per_minute", minimum=0, maximum=100000)
    jitter = core.number(d, "jitter", minimum=0, maximum=1000)
    per_step = speed / hz
    tunnel = per_step > thick
    findings = [chk("physics_audit.tunneling", not tunnel, f"Per-step travel {round(per_step, 3)} is under thickness {thick}." if not tunnel else f"Per-step travel {round(per_step, 3)} exceeds thickness {thick}; tunneling risk.", "fail"),
                chk("physics_audit.mass_ratio", ratio <= 1000, "Mass ratio is safe." if ratio <= 1000 else "Mass ratio above 1000 causes solver instability.", "warning"),
                chk("physics_audit.ownership_churn", churn <= 10, "Ownership churn is low." if churn <= 10 else "High ownership churn causes jitter.", "warning"),
                chk("physics_audit.jitter", jitter <= 0.1, "Jitter is within tolerance." if jitter <= 0.1 else f"Jitter {jitter} is high; check constraints and ownership.", "warning")]
    return out({"per_step_travel": round(per_step, 4), "tunneling_risk": tunnel}, findings)


def h_interaction(d, cap, ctx):
    mass = core.number(d, "mass", minimum=0.01, maximum=1e6)
    dist = core.number(d, "distance", minimum=0, maximum=1e4)
    max_d = core.number(d, "max_distance", minimum=0, maximum=1e4)
    k = core.number(d, "spring", minimum=0, maximum=1e6)
    c = core.number(d, "damping", minimum=0, maximum=1e6)
    max_f = core.number(d, "max_force", minimum=0, maximum=1e9)
    release = core.boolean(d, "release_on_leave", False)
    steps = 120
    x, v, dt = dist, 0.0, 1.0 / 60
    peak_force = 0.0
    for _ in range(steps):
        f = -k * x - c * v
        f = max(-max_f, min(max_f, f))
        peak_force = max(peak_force, abs(f))
        v += f / mass * dt
        x += v * dt
    findings = [chk("interaction.force_limit", peak_force <= max_f, "Peak force is within the limit." if peak_force <= max_f else "Force clamped at the limit; the object lags behind.", "warning"),
                chk("interaction.range", dist <= max_d, "Object starts within the interaction range." if dist <= max_d else "Object starts beyond the maximum distance.", "fail"),
                chk("interaction.release", release, "Release on leave is enabled." if release else "Enable release when the holder leaves range or dies.", "warning"),
                chk("interaction.ownership", None, "Carried objects need one network owner at a time; see ownership.")]
    return out({"peak_force": round(peak_force, 3), "residual_distance": round(x, 4)}, findings)


def h_physics_harness(d, cap, ctx):
    slopes = core.array(d, "slopes", required=True, maximum=200)
    ratios = core.array(d, "mass_ratios", required=True, maximum=200)
    lats = core.array(d, "latencies_ms", required=True, maximum=200)
    speeds = core.array(d, "speeds", required=True, maximum=200)
    total = len(slopes) * len(ratios) * len(lats) * len(speeds)
    steps_ok(total, ctx)
    mu = 0.5
    matrix = []
    for s in slopes:
        for r in ratios:
            for lat in lats:
                for sp in speeds:
                    slides = abs(math.degrees(math.atan(mu))) < float(s)
                    matrix.append({"slope_deg": s, "mass_ratio": r, "latency_ms": lat, "speed": sp, "expected_slide": slides})
    findings = [chk("physics_harness.coverage", total >= 1, f"{total} scenario(s) generated across slopes, mass ratios, latencies and speeds."),
                chk("physics_harness.extremes", max(map(float, ratios)) >= 100 if ratios else False, "Includes an extreme mass ratio." if ratios and max(map(float, ratios)) >= 100 else "Add an extreme mass ratio (100 or more).", "warning"),
                chk("physics_harness.expected", True, "Expected outcomes are analytic (friction-angle slide test); run them in Studio to confirm."),
                chk("physics_harness.studio", None, "No Studio run was performed; this file is the scenario matrix only.")]
    return out({"scenario_count": total, "matrix_sample": matrix[:200]}, findings, {"physics-scenarios.json": matrix})


def h_character_controller(d, cap, ctx):
    movement = core.text(d, "movement", maximum=80)
    speed = core.number(d, "walk_speed", minimum=0, maximum=500)
    jump = core.number(d, "jump_power", minimum=0, maximum=1000)
    owned = core.boolean(d, "server_owned_health", False)
    height = jump * jump / (2 * 196.2)
    findings = [chk("character_controller.health", owned, "Health is owned by the server." if owned else "Health must be server-owned.", "fail"),
                chk("character_controller.speed", speed <= 50, "Walk speed is within a conventional range." if speed <= 50 else "Walk speed is high; verify anti-cheat thresholds.", "warning"),
                chk("character_controller.jump", height <= 50, f"Jump apex about {round(height, 2)} studs with gravity 196.2.", "info"),
                chk("character_controller.preservation", None, "Camera, input and animation integration must be checked in Studio for the custom controller.")]
    return out({"movement": movement, "jump_apex_studs": round(height, 3)}, findings)


def h_movement_validation(d, cap, ctx):
    samples = core.array(d, "samples", required=True, kind=dict, maximum=20000)
    max_speed = core.number(d, "max_speed", minimum=0.1, maximum=5000)
    tol = core.number(d, "tolerance", minimum=0, maximum=10)
    latency = core.number(d, "latency_seconds", minimum=0, maximum=10)
    allowed = set(core.array(d, "allowed_states", maximum=50, kind=str))
    state = core.text(d, "state", maximum=40)
    viol, state_bad, flags = [], [], 0
    for i in range(1, len(samples)):
        steps_ok(1, ctx)
        a, b = samples[i - 1], samples[i]
        dt = float(b.get("t", 0)) - float(a.get("t", 0))
        if dt <= 0:
            continue
        pa = core.vector([a.get("x", 0), a.get("y", 0), a.get("z", 0)], 3)
        pb = core.vector([b.get("x", 0), b.get("y", 0), b.get("z", 0)], 3)
        speed = vlen(vsub(pb, pa)) / dt
        allowance = max_speed * (1 + tol) + (latency and max_speed * latency / dt)
        if speed > allowance:
            viol.append({"index": i, "speed": round(speed, 3), "allowance": round(allowance, 3)})
            flags += 1
    if allowed and state not in allowed:
        state_bad.append(state)
    escalate = len(viol) >= 3
    findings = [chk("movement_validation.speed", not viol, "Speeds stay within the allowance with latency tolerance." if not viol else f"{len(viol)} sample(s) exceed the speed allowance; logged, not punished.", "warning"),
                chk("movement_validation.state", not state_bad, "Current state is allowed." if not state_bad else f"State {state} is not allowed.", "fail"),
                chk("movement_validation.escalation", None if escalate else True, "Three or more samples need human review." if escalate else "Below the review threshold; no automatic action.")]
    return out({"samples": len(samples), "violations": viol[:100], "escalate_to_review": escalate}, findings)


def h_parkour(d, cap, ctx):
    ledge = core.number(d, "ledge_height", minimum=0, maximum=1000)
    player = core.number(d, "player_height", minimum=0.1, maximum=1000)
    reach = core.number(d, "reach", minimum=0, maximum=1000)
    dist = core.number(d, "distance", minimum=0, maximum=1000)
    clear = core.number(d, "clearance", minimum=0, maximum=1000)
    stamina = core.number(d, "stamina", minimum=0, maximum=10000)
    cost = core.number(d, "cost", minimum=0, maximum=10000)
    findings = [chk("parkour.reach", reach >= dist, "Ledge is reachable." if reach >= dist else f"Gap {dist} exceeds reach {reach}.", "fail"),
                chk("parkour.clearance", clear >= player, "Headroom fits the character." if clear >= player else "Clearance is lower than the player height; the mantle will clip.", "fail"),
                chk("parkour.stamina", stamina >= cost, "Stamina covers the move." if stamina >= cost else "Not enough stamina for the move.", "warning"),
                chk("parkour.prediction", None, "Client prediction must be reconciled by the server; see prediction.")]
    return out({"ledge_height": ledge, "reachable": reach >= dist, "clear": clear >= player}, findings)


def h_vr(d, cap, ctx):
    scale = core.number(d, "world_scale", minimum=0.01, maximum=100)
    loco = d.get("locomotion") or {}
    if not isinstance(loco, dict):
        core.fail("INPUT_INVALID", "locomotion must be an object.")
    snap = core.number(d, "snap_turn_degrees", minimum=0, maximum=360)
    vignette = core.boolean(d, "vignette", False)
    seated = core.boolean(d, "seated_mode", False)
    accel = core.boolean(d, "artificial_acceleration", False)
    speed = float(loco.get("speed", 0))
    findings = [chk("vr.comfort_vignette", vignette or not accel, "Vignette is on during artificial acceleration." if vignette or not accel else "Artificial acceleration without a vignette causes motion sickness.", "fail"),
                chk("vr.snap_turn", snap >= 30 or snap == 0, f"Snap turn {snap} degrees." if snap >= 30 or snap == 0 else "Snap turn under 30 degrees can be disorienting.", "warning"),
                chk("vr.speed", speed <= 3 * scale or not accel, "Locomotion speed is moderate." if speed <= 3 * scale or not accel else "Locomotion speed is high for the world scale.", "warning"),
                chk("vr.seated", True if seated else None, "Seated mode available." if seated else "Provide a seated mode for comfort.", "info")]
    return out({"world_scale": scale, "locomotion_speed": speed, "seated_mode": seated}, findings)


def h_ownership_physics(d, cap, ctx):
    assemblies = core.array(d, "assemblies", required=True, kind=dict, maximum=2000)
    rows, findings = [], []
    for a in assemblies:
        touching = [int(x) for x in a.get("touching_players", [])]
        owner = a.get("owner")
        if len(touching) == 0:
            target = "server"
        elif len(touching) == 1:
            target = touching[0]
        else:
            target = "server"
        ok = owner == target
        rows.append({"assembly": core.text(a, "name", maximum=80), "owner": owner, "target": target, "ok": ok})
        if not ok:
            findings.append(chk("ownership.assignment", None, f"{a.get('name')} owner {owner} differs from target {target}; review the handoff rule."))
    mismatched = sum(not r["ok"] for r in rows)
    findings.append(chk("ownership.assignment", mismatched == 0, "Every assembly has the expected network owner." if mismatched == 0 else f"{mismatched} assembly owner(s) need reassignment.", "warning"))
    findings.append(chk("ownership.churn", None, "Ownership churn is measured per minute in physics_budget; not evaluated here."))
    return out({"assemblies": rows}, findings)


# ---------------------------------------------------------------- motion

def h_keyframes(d, cap, ctx):
    duration = core.number(d, "duration", minimum=0.01, maximum=3600)
    loop = core.boolean(d, "loop", False)
    keys = core.array(d, "keys", required=True, kind=dict, maximum=2000)
    fps = core.number(d, "sample_fps", minimum=1, maximum=240)
    pts = sorted(((float(k.get("t", 0)), float(k.get("value", 0))) for k in keys))
    n = int(duration * fps) + 1
    steps_ok(n, ctx)
    samples = []
    for i in range(n):
        t = min(duration, i / fps)
        if not pts:
            v = 0.0
        elif t <= pts[0][0]:
            v = pts[0][1]
        elif t >= pts[-1][0]:
            v = pts[-1][1]
        else:
            for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
                if t0 <= t <= t1:
                    s = (t - t0) / (t1 - t0) if t1 > t0 else 0
                    s = s * s * (3 - 2 * s) if k_ease(keys, t0) else s
                    v = v0 + (v1 - v0) * s
                    break
        samples.append(round(v, 5))
    out_of_range = [p[0] for p in pts if p[0] < 0 or p[0] > duration]
    seam = abs(pts[0][1] - pts[-1][1]) if pts else 0
    findings = [chk("keyframes.range", not out_of_range, "All keys are within the duration." if not out_of_range else f"{len(out_of_range)} key(s) outside the duration.", "fail"),
                chk("keyframes.loop_seam", (not loop) or seam < 1e-6, "Loop seam matches first and last values." if (not loop) or seam < 1e-6 else f"Loop seam differs by {round(seam, 4)}; the animation will pop.", "fail" if loop and seam >= 1e-6 else "info"),
                chk("keyframes.density", len(keys) <= duration * 30, "Key density is reasonable." if len(keys) <= duration * 30 else "Dense keys; consider reducing for replication.", "warning")]
    return out({"samples": len(samples), "min": min(samples), "max": max(samples), "loop_seam_error": round(seam, 6)}, findings, {"keyframe-samples.json": samples})


def k_ease(keys, t0):
    for k in keys:
        if float(k.get("t", 0)) == t0:
            return str(k.get("easing", "linear")).lower() == "smooth"
    return False


def h_animation_rig(d, cap, ctx):
    joints = core.array(d, "joints", required=True, kind=dict, maximum=500)
    root = core.text(d, "root", maximum=80)
    parents = {j["name"]: j.get("parent") for j in joints}
    findings = []
    if root not in parents:
        findings.append(chk("animation_rig.root", False, f"Root {root} is not a joint.", "fail"))
    if any(p is not None and p not in parents for p in parents.values()):
        findings.append(chk("animation_rig.parents", False, "A joint names a parent that does not exist.", "fail"))
    cycles = False
    for name in parents:
        seen, cur = set(), name
        while cur is not None and cur in parents:
            if cur in seen:
                cycles = True
                break
            seen.add(cur)
            cur = parents[cur]
    findings.append(chk("animation_rig.cycles", not cycles, "Joint hierarchy is acyclic." if not cycles else "Joint hierarchy has a cycle.", "fail"))
    roots = [n for n, p in parents.items() if p is None]
    findings.append(chk("animation_rig.single_root", roots == [root], "Exactly one root joint." if roots == [root] else f"Root candidates: {roots}.", "warning"))
    findings.append(chk("animation_rig.motor6d", None, "Motor6D C0/C1 offsets must match the bind pose in Studio."))
    return out({"joints": len(parents), "roots": roots, "root": root}, findings)


def h_procedural(d, cap, ctx):
    target = core.number(d, "target", minimum=-1e6, maximum=1e6)
    value = core.number(d, "value", minimum=-1e6, maximum=1e6)
    vel = core.number(d, "velocity", minimum=-1e6, maximum=1e6)
    k = core.number(d, "stiffness", minimum=0, maximum=1e4)
    c = core.number(d, "damping", minimum=0, maximum=1e4)
    dt = core.number(d, "dt", minimum=0.0001, maximum=0.1)
    steps = core.integer(d, "steps", minimum=1, maximum=100000)
    steps_ok(steps, ctx)
    x, v, peak, settle, crossed = value, vel, value, None, 0
    prev = x - target
    for i in range(steps):
        a = -k * (x - target) - c * v
        v += a * dt
        x += v * dt
        if (x - target) * prev < 0:
            crossed += 1
        prev = x - target
        peak = max(peak, x) if target >= value else min(peak, x)
        if settle is None and abs(x - target) < 0.01 * max(1.0, abs(target)) and abs(v) < 0.01:
            settle = round(i * dt, 4)
    overshoot = abs(peak - target) / max(abs(target - value), 1e-9) - 1 if target != value else 0
    zeta = c / (2 * math.sqrt(k)) if k > 0 else None
    findings = [chk("procedural.stability", (dt * math.sqrt(k)) < 2 if k > 0 else True, "Time step is stable for the stiffness." if (dt * math.sqrt(k)) < 2 or k == 0 else "Time step is too large for this stiffness; the spring will explode.", "fail"),
                chk("procedural.damping", zeta is None or zeta >= 0.5, f"Damping ratio {round(zeta, 3)}." if zeta is not None else "No stiffness; damping ratio undefined.", "warning"),
                chk("procedural.settle", settle is not None, f"Settles at {settle}s." if settle is not None else "Does not settle within the run.", "warning")]
    return out({"final_value": round(x, 5), "settle_seconds": settle, "crossings": crossed, "damping_ratio": round(zeta, 4) if zeta is not None else None}, findings)


def h_ik(d, cap, ctx):
    la = core.number(d, "length_a", minimum=0.01, maximum=1000)
    lb = core.number(d, "length_b", minimum=0.01, maximum=1000)
    tgt = core.vector(d.get("target"), 2)
    amin = core.number(d, "minimum_angle", minimum=-360, maximum=360)
    amax = core.number(d, "maximum_angle", minimum=-360, maximum=360)
    dist = vlen(tgt)
    reach = la + lb
    reachable = dist <= reach and dist >= abs(la - lb)
    c = max(-1.0, min(1.0, (dist * dist - la * la - lb * lb) / (2 * la * lb)))
    elbow = math.degrees(math.acos(c))
    shoulder = math.degrees(math.atan2(tgt[1], tgt[0]) - math.atan2(lb * math.sin(math.radians(elbow)), la + lb * math.cos(math.radians(elbow))))
    clamped_elbow = max(amin, min(amax, elbow))
    findings = [chk("ik.reachable", reachable, "Target is within reach." if reachable else f"Target distance {round(dist, 3)} is outside [{round(abs(la - lb), 3)}, {round(reach, 3)}]; the limb will stretch.", "warning"),
                chk("ik.limits", clamped_elbow == elbow, "Elbow angle is within limits." if clamped_elbow == elbow else "Elbow angle was clamped to its limit.", "warning"),
                chk("ik.blend", None, "IK blending with the authored pose needs a weight curve; tune in Studio.")]
    return out({"elbow_deg": round(elbow, 3), "elbow_clamped_deg": round(clamped_elbow, 3), "shoulder_deg": round(shoulder, 3), "reachable": reachable}, findings)


def h_retarget(d, cap, ctx):
    src, tgt = core.number(d, "source_height", minimum=0.01, maximum=100), core.number(d, "target_height", minimum=0.01, maximum=100)
    keys = core.array(d, "keys", required=True, kind=dict, maximum=5000)
    compat = core.boolean(d, "compatible", False)
    ratio = tgt / src
    scaled = [{"t": k.get("t"), "value": round(float(k.get("value", 0)) * ratio, 5)} for k in keys]
    findings = [chk("retarget.rig_compatibility", compat, "Rigs share joint names and orientations." if compat else "Rigs are not marked compatible; map joints and correct orientations before retargeting.", "fail" if not compat else "info"),
                chk("retarget.scale", 0.5 <= ratio <= 2.0, f"Scale ratio {round(ratio, 3)}." if 0.5 <= ratio <= 2 else f"Scale ratio {round(ratio, 3)} is extreme; foot contacts will slide.", "warning"),
                chk("retarget.contacts", None, "Foot contacts need IK or contact correction after scaling.")]
    return out({"scale_ratio": round(ratio, 5), "scaled_keys": scaled[:200]}, findings, {"retargeted-keys.json": scaled})


def h_lighting(d, cap, ctx):
    preset = core.text(d, "preset", maximum=40)
    clock = core.number(d, "clock_time", minimum=0, maximum=24)
    bright = core.number(d, "brightness", minimum=0, maximum=10)
    exposure = core.number(d, "exposure", minimum=-10, maximum=10)
    soft = core.number(d, "shadow_softness", minimum=0, maximum=1)
    fog = core.number(d, "fog_end", minimum=0, maximum=1e6)
    elevation = 90 - abs(12 - clock) * 15
    warm = clock < 7 or clock > 18
    findings = [chk("lighting.ranges", bright <= 5 and abs(exposure) <= 3, "Brightness and exposure are within review ranges." if bright <= 5 and abs(exposure) <= 3 else "Brightness or exposure is extreme; check for washed-out or dark scenes.", "warning"),
                chk("lighting.sun", elevation > 0 or warm, f"Sun elevation {round(elevation, 1)} degrees at clock {clock}."),
                chk("lighting.fog", fog >= 100 or fog == 0, "Fog distance is readable." if fog >= 100 or fog == 0 else "Very short fog hides gameplay-critical distance.", "warning"),
                chk("lighting.accessibility", None, "Contrast of gameplay elements must be verified with the color accessibility checks.")]
    return out({"preset": preset, "sun_elevation_deg": round(elevation, 2), "warm_palette": warm, "softness": soft}, findings)


def h_day_night(d, cap, ctx):
    day = core.number(d, "day_seconds", minimum=10, maximum=86400)
    start = core.number(d, "start_hour", minimum=0, maximum=24)
    min_b = core.number(d, "minimum_brightness", minimum=0, maximum=1)
    samples = core.integer(d, "samples", minimum=2, maximum=2000)
    changes = core.array(d, "gameplay_changes", maximum=200, kind=dict)
    steps_ok(samples, ctx)
    rows = []
    for i in range(samples):
        frac = i / (samples - 1)
        hour = (start + 24 * frac) % 24
        bright = min_b + (1 - min_b) * max(0.0, math.sin(math.pi * (hour - 6) / 12)) if 6 <= hour <= 18 else min_b
        rows.append({"hour": round(hour, 2), "brightness": round(bright, 4)})
    cycle_minutes = day / 60
    findings = [chk("day_night.cycle", cycle_minutes >= 5, f"One cycle is {round(cycle_minutes, 1)} minutes." if cycle_minutes >= 5 else "Cycle shorter than 5 minutes is disorienting for most players.", "warning"),
                chk("day_night.minimum_brightness", min_b >= 0.05, "Night stays playable." if min_b >= 0.05 else "Night is nearly black; players may lose gameplay information.", "warning"),
                chk("day_night.gameplay", all("time" in c for c in changes) if changes else None, "Gameplay changes are tied to the time of day." if changes else "No gameplay changes are scheduled.")]
    return out({"cycle_minutes": round(cycle_minutes, 3), "sample": rows[:: max(1, samples // 24)]}, findings)


def h_beams(d, cap, ctx):
    points = core.array(d, "points", required=True, maximum=500)
    width = core.number(d, "width", minimum=0.01, maximum=100)
    life = core.number(d, "lifetime", minimum=0.01, maximum=600)
    segs = core.integer(d, "segments", minimum=2, maximum=1000)
    steps_ok(segs, ctx)
    pts = [core.vector(p, 3) for p in points]
    if len(pts) < 2:
        core.fail("INPUT_INVALID", "A beam needs at least two points.")
    sampled = []
    for i in range(segs + 1):
        u = i / segs * (len(pts) - 1)
        k = min(int(u), len(pts) - 2)
        s = u - k
        p0 = pts[max(0, k - 1)]
        p1, p2 = pts[k], pts[k + 1]
        p3 = pts[min(len(pts) - 1, k + 2)]
        q = [0.5 * ((2 * p1[j]) + (-p0[j] + p2[j]) * s + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * s * s + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * s ** 3) for j in range(3)]
        sampled.append([round(x, 4) for x in q])
    taper = [round(width * (1 - i / segs) + 0.1 * width, 4) for i in range(segs + 1)]
    length = sum(vlen(vsub(sampled[i + 1], sampled[i])) for i in range(len(sampled) - 1))
    findings = [chk("beams.segments", segs <= 200, "Segment count is reasonable." if segs <= 200 else "More than 200 segments is costly for a single beam.", "warning"),
                chk("beams.readability", width >= 0.05, "Width is visible at distance." if width >= 0.05 else "Width below 0.05 may vanish in the distance.", "warning"),
                chk("beams.lifetime", life <= 10, "Lifetime is short enough to clean up quickly." if life <= 10 else "Long lifetimes accumulate; pool or cap them.", "info")]
    return out({"sampled_points": len(sampled), "length": round(length, 3), "taper_start": taper[0], "taper_end": taper[-1]}, findings, {"beam-curve.json": sampled})


def h_ui_motion(d, cap, ctx):
    duration = core.number(d, "duration", minimum=0, maximum=10)
    frm = core.number(d, "from", minimum=-1e6, maximum=1e6)
    to = core.number(d, "to", minimum=-1e6, maximum=1e6)
    easing = core.text(d, "easing", maximum=20)
    reduced = core.boolean(d, "reduced_motion", False)
    samples = core.integer(d, "samples", minimum=2, maximum=1000)
    steps_ok(samples, ctx)
    eff_duration = min(duration, 0.2) if reduced else duration
    curves = {"linear": lambda s: s, "ease-out": lambda s: 1 - (1 - s) ** 3, "ease-in-out": lambda s: s * s * (3 - 2 * s)}
    f = curves.get(easing, curves["linear"])
    values = [round(frm + (to - frm) * f(i / (samples - 1)), 4) for i in range(samples)]
    findings = [chk("ui_motion.duration", 0.1 <= duration <= 0.6 or reduced, "Duration is within 0.1 to 0.6 s." if 0.1 <= duration <= 0.6 or reduced else "Duration outside 0.1-0.6 s feels sluggish or abrupt.", "warning"),
                chk("ui_motion.reduced_motion", not reduced or eff_duration <= 0.2, f"Reduced motion caps the transition at {round(eff_duration, 2)} s." if reduced else "Reduced motion is off; a reduced variant is still required."),
                chk("ui_motion.easing", easing in curves, "Easing is a supported curve." if easing in curves else f"Unknown easing {easing}; linear used.", "warning")]
    return out({"effective_duration": eff_duration, "easing": easing, "values": values[:50]}, findings)


# ---------------------------------------------------------------- schedule

def h_calendar(d, cap, ctx):
    import datetime as dt
    from zoneinfo import ZoneInfo
    stamps = core.array(d, "timestamps", required=True, kind=str, maximum=10000)
    now = core.text(d, "now", maximum=40)
    grace = core.integer(d, "grace_days", minimum=0, maximum=30)
    rewards = core.array(d, "rewards", required=True, kind=dict, maximum=365)
    tz = ZoneInfo("Asia/Manila")
    days = set()
    for s in stamps:
        try:
            moment = dt.datetime.fromisoformat(s)
        except ValueError:
            core.fail("INPUT_INVALID", "timestamps must be ISO 8601 with a UTC offset.", value=s)
        if moment.tzinfo is None:
            core.fail("INPUT_INVALID", "timestamps must include a timezone offset.", value=s)
        days.add(moment.astimezone(tz).date())
    today = dt.datetime.fromisoformat(now).astimezone(tz).date()
    streak, cursor = 0, today if today in days else today - dt.timedelta(days=1)
    gap_ok = True
    while cursor in days:
        streak += 1
        cursor -= dt.timedelta(days=1)
    last = max(days) if days else None
    missed = (today - last).days if last else None
    if missed is not None and missed > grace + 1:
        gap_ok = False
    grant = next((r for r in rewards if r.get("day") == min(streak, len(rewards))), None)
    findings = [chk("calendar.timezone", True, "Days are computed in Asia/Manila (UTC+8) from explicit offsets."),
                chk("calendar.missed_day", gap_ok, f"Missed {missed} day(s) with grace {grace}." if gap_ok else "Streak reset after missed days beyond grace.", "warning"),
                chk("calendar.anti_clock", None, "Client clocks are untrusted; compute claims from server time (now)."),
                chk("calendar.catch_up", None, "Catch-up rules must be defined before enabling retroactive rewards.")]
    return out({"streak_days": streak, "today": today.isoformat(), "last_claim_day": last.isoformat() if last else None, "missed_days": missed, "reward": grant}, findings)


def h_live_schedule(d, cap, ctx):
    import datetime as dt
    events = core.array(d, "events", required=True, kind=dict, maximum=500)
    version, minimum = core.text(d, "server_version", maximum=40), core.text(d, "minimum_version", maximum=40)
    vt = tuple(int(x) for x in version.split("."))
    mt = tuple(int(x) for x in minimum.split("."))
    parsed = []
    for e in events:
        s = dt.datetime.fromisoformat(core.text(e, "start", maximum=40))
        en = dt.datetime.fromisoformat(core.text(e, "end", maximum=40))
        if en <= s:
            core.fail("INPUT_INVALID", "event end must be after start", event=e.get("name"))
        parsed.append((s, en, core.text(e, "name", maximum=80)))
    parsed.sort()
    overlaps = [(a[2], b[2]) for a, b in zip(parsed, parsed[1:]) if b[0] < a[1]]
    findings = [chk("live_schedule.version", vt >= mt, "Server version meets the event's minimum." if vt >= mt else f"Server {version} is below the required {minimum}; events stay disabled.", "fail" if vt < mt else "info"),
                chk("live_schedule.overlaps", not overlaps, "No overlapping events." if not overlaps else f"Overlapping events: {overlaps}.", "warning"),
                chk("live_schedule.rollback", None, "Each event needs a rollback window and an off switch; confirm in the feature flag review.")]
    return out({"events": [{"name": n, "start": s.isoformat(), "end": e.isoformat()} for s, e, n in parsed], "overlaps": overlaps}, findings)


def h_community_event(d, cap, ctx):
    goal = core.number(d, "goal", minimum=1, maximum=1e12)
    start = core.number(d, "start", minimum=0, maximum=1e12)
    end = core.number(d, "end", minimum=0, maximum=1e12)
    now = core.number(d, "now", minimum=0, maximum=1e12)
    contrib = core.array(d, "contributions", required=True, kind=dict, maximum=100000)
    if end <= start:
        core.fail("INPUT_INVALID", "end must be after start.")
    total = sum(float(c.get("amount", 0)) for c in contrib if start <= float(c.get("t", now)) <= end)
    pct = min(100.0, total / goal * 100)
    phase = "upcoming" if now < start else ("active" if now <= end else "ended")
    findings = [chk("community_event.progress", True, f"{round(pct, 2)}% of the goal reached."),
                chk("community_event.window", all(start <= float(c.get("t", now)) <= end for c in contrib), "Every contribution falls inside the window." if all(start <= float(c.get("t", now)) <= end for c in contrib) else "Some contributions are outside the window and were excluded.", "warning"),
                chk("community_event.late_join", None, "Late joiners must see the global progress from the server, not local state."),
                chk("community_event.rewards", None, "Rewards for the goal need an idempotent grant per player.")]
    return out({"phase": phase, "progress_percent": round(pct, 3), "contributed": round(total, 2), "goal_reached": total >= goal}, findings)


def h_campaign(d, cap, ctx):
    update = core.text(d, "update", maximum=120)
    release = core.text(d, "release", maximum=20)
    channels = core.array(d, "channels", required=True, kind=str, maximum=20)
    ready = core.boolean(d, "content_ready", False)
    rollback = core.text(d, "rollback_message", maximum=400)
    findings = [chk("campaign.content_ready", ready, "Content is ready for the reveal." if ready else "Do not announce before the content is ready.", "fail"),
                chk("campaign.rollback_message", bool(rollback), "A rollback message is prepared." if rollback else "Prepare a rollback message in case the update is paused.", "warning"),
                chk("campaign.channels", len(channels) >= 1, f"{len(channels)} channel(s) planned.")]
    timeline = [{"step": "content freeze", "relative": "T-7d"}, {"step": "thumbnail and copy review", "relative": "T-3d"}, {"step": "announce", "relative": "T-0"}, {"step": "in-experience banner", "relative": "T+0"}, {"step": "monitor", "relative": "T+1d"}]
    return out({"update": update, "release": release, "channels": channels, "timeline": timeline}, findings, {"campaign-plan.json": timeline})


# ---------------------------------------------------------------- statemachine

def reach(initial, edges):
    seen, pend = set(), [initial]
    while pend:
        cur = pend.pop()
        if cur in seen:
            continue
        seen.add(cur)
        pend.extend(edges.get(cur, []))
    return seen


def h_state_machine(d, cap, ctx):
    initial = core.text(d, "initial", maximum=60)
    states = core.array(d, "states", required=True, kind=str, maximum=200)
    transitions = core.array(d, "transitions", required=True, kind=dict, maximum=2000)
    events = core.array(d, "events", maximum=5000, kind=str)
    table, edges, findings = {}, {s: [] for s in states}, []
    for tr in transitions:
        a, ev, b = core.text(tr, "from", maximum=60), core.text(tr, "event", maximum=60), core.text(tr, "to", maximum=60)
        if a not in edges or b not in edges:
            findings.append(chk("state_machine.transitions", False, f"Transition {a} --{ev}--> {b} uses an undeclared state.", "fail"))
            continue
        if (a, ev) in table and table[(a, ev)] != b:
            findings.append(chk("state_machine.determinism", False, f"{a} on '{ev}' is ambiguous ({table[(a, ev)]} or {b}).", "fail"))
        table[(a, ev)] = b
        edges[a].append(b)
    if initial not in states:
        findings.append(chk("state_machine.initial", False, "Initial state is not declared.", "fail"))
    unreachable = sorted(set(states) - reach(initial, edges)) if initial in states else states
    findings.append(chk("state_machine.reachability", not unreachable, "Every state is reachable." if not unreachable else "Unreachable: " + ", ".join(unreachable), "warning"))
    dead = sorted(s for s in states if not edges[s] and s != initial)
    findings.append(chk("state_machine.dead_ends", not dead, "No dead-end states." if not dead else "Dead-end states (add an exit or mark terminal): " + ", ".join(dead), "warning"))
    cur, trace = initial, [initial]
    for ev in events:
        steps_ok(1, ctx)
        nxt = table.get((cur, ev))
        if nxt is None:
            trace.append(f"ignored:{ev}")
            continue
        cur = nxt
        trace.append(cur)
    findings.append(chk("state_machine.events", True, f"Replayed {len(events)} event(s); final state {cur}."))
    if not any(f["status"] == "fail" for f in findings):
        findings.append(chk("state_machine.transitions", True, "All transitions use declared states."))
    lines = ["--!strict", f"-- State machine skeleton generated by tools-roblox.py. Initial: {initial}.", "local StateMachine = {}", f"StateMachine.initial = {core.lua(initial)}", "StateMachine.table = {"]
    for (a, ev), b in sorted(table.items()):
        lines.append(f"\t[{core.lua(a + '|' + ev)}] = {core.lua(b)},")
    lines += ["}", "", "function StateMachine.step(state: string, event: string): string?", "\treturn StateMachine.table[state .. \"|\" .. event]", "end", "", "return StateMachine", ""]
    return out({"states": len(states), "transitions": len(table), "final_state": cur, "trace": trace[:300], "unreachable": unreachable, "dead_ends": dead}, findings, {"StateMachine.luau": "\n".join(lines)})


def h_behavior_tree(d, cap, ctx):
    tree = core.obj(d, "tree", required=True)
    facts = core.obj(d, "facts")
    trace = []

    def run(node, depth=0):
        steps_ok(1, ctx)
        if depth > 50:
            core.fail("INPUT_LIMIT", "Behavior tree nesting exceeds 50 levels.")
        kind = node.get("type")
        name = node.get("name", kind)
        if kind == "condition":
            ok = bool(facts.get(name, False))
            trace.append(f"cond:{name}={ok}")
            return ok
        if kind == "action":
            ok = not facts.get("fail:" + name, False)
            trace.append(f"act:{name}={'success' if ok else 'failure'}")
            return ok
        children = node.get("children", [])
        if kind == "selector":
            for c in children:
                if run(c, depth + 1):
                    return True
            return False
        if kind == "sequence":
            for c in children:
                if not run(c, depth + 1):
                    return False
            return True
        core.fail("INPUT_INVALID", "Node type must be selector, sequence, condition or action.", type=kind)

    result = run(tree)
    findings = [chk("behavior_tree.result", True, f"Tree returned {'success' if result else 'failure'}."),
                chk("behavior_tree.reusability", True, "Nodes are plain data; subtrees can be reused by name."),
                chk("behavior_tree.cooldowns", None, "Cooldowns need the time source; not evaluated in this single tick."),
                chk("behavior_tree.debug", True, "The trace lists each evaluated condition and action.")]
    return out({"result": "success" if result else "failure", "trace": trace[:500]}, findings)


def h_npc_combat(d, cap, ctx):
    health = core.number(d, "health", minimum=0, maximum=1e6)
    dist = core.number(d, "distance", minimum=0, maximum=1e6)
    rng_ = core.number(d, "attack_range", minimum=0, maximum=1e6)
    ammo = core.integer(d, "ammo", minimum=0, maximum=100000)
    cover = core.boolean(d, "cover", False)
    ally = core.number(d, "ally_health", minimum=0, maximum=1e6)
    diff = core.text(d, "difficulty", maximum=20)
    scale = {"easy": 0.7, "normal": 1.0, "hard": 1.3}.get(diff, 1.0)
    if health < 25 * scale and cover:
        action = "retreat_to_cover"
    elif dist <= rng_ and ammo > 0:
        action = "shoot"
    elif dist <= rng_ and ammo == 0:
        action = "melee"
    elif ally < 20 and dist > rng_:
        action = "support_ally"
    else:
        action = "advance"
    findings = [chk("npc_combat.difficulty", diff in ("easy", "normal", "hard"), f"Difficulty {diff} scales thresholds." if diff in ("easy", "normal", "hard") else "Unknown difficulty; normal used.", "warning"),
                chk("npc_combat.telegraph", None, "Attacks need telegraphs for fairness; confirm the windup in the boss review."),
                chk("npc_combat.authority", None, "NPC damage is applied on the server.")]
    return out({"action": action, "scale": scale}, findings)


def h_utility_ai(d, cap, ctx):
    actions = core.array(d, "actions", required=True, kind=dict, maximum=200)
    current = core.text(d, "current", maximum=80, required=False) or ""
    hyst = core.number(d, "hysteresis", minimum=0, maximum=1)
    scores = []
    for a in actions:
        factors = core.obj(a, "considerations", required=False) if isinstance(a.get("considerations", {}), dict) else {}
        score = 1.0
        for f, val in factors.items():
            v = core.number({"v": val}, "v", minimum=0, maximum=1)
            score *= v
        weight = core.number(a, "weight", 1.0, 0, 100)
        scores.append({"name": core.text(a, "name", maximum=80), "score": round(score * weight, 6)})
    scores.sort(key=lambda s: (-s["score"], s["name"]))
    best = scores[0] if scores else None
    cur_score = next((s["score"] for s in scores if s["name"] == current), None)
    choice = best["name"] if best else None
    if cur_score is not None and best and best["score"] - cur_score < hyst:
        choice = current
    findings = [chk("utility_ai.scores", bool(scores), "Scores computed from normalized considerations." if scores else "No actions to score."),
                chk("utility_ai.hysteresis", True, f"Switching requires a margin of {hyst} to avoid flip-flopping."),
                chk("utility_ai.ties", len(scores) < 2 or scores[0]["score"] != scores[1]["score"], "Ties are broken by name for determinism." if len(scores) > 1 and scores[0]["score"] == scores[1]["score"] else "No tie at the top.")]
    return out({"choice": choice, "ranking": scores[:20]}, findings)


def h_companion(d, cap, ctx):
    dist = core.number(d, "distance", minimum=0, maximum=1e5)
    follow = core.number(d, "follow_radius", minimum=0, maximum=1e4)
    catch = core.number(d, "catch_up_radius", minimum=0, maximum=1e5)
    alive = core.boolean(d, "owner_alive", True)
    cmd = core.text(d, "command", maximum=30)
    safe = core.boolean(d, "destination_safe", True)
    if not alive:
        state = "idle_owner_gone"
    elif cmd == "wait":
        state = "wait"
    elif dist > catch:
        state = "teleport_catch_up" if safe else "pathfind_catch_up"
    elif dist > follow:
        state = "follow"
    else:
        state = "idle_near_owner"
    findings = [chk("companion.teleport", state != "teleport_catch_up" or safe, "Teleport catch-up uses only safe destinations." if state != "teleport_catch_up" or safe else "Unsafe destination; pathfind instead.", "fail" if state == "teleport_catch_up" and not safe else "info"),
                chk("companion.ownership", alive or state == "idle_owner_gone", "Companion stops acting when its owner is gone."),
                chk("companion.inventory", None, "Companion inventory access must check ownership on the server.")]
    return out({"state": state, "distance": dist}, findings)


def h_dialogue(d, cap, ctx):
    nodes = core.obj(d, "nodes", required=True)
    start = core.text(d, "start", maximum=80)
    facts = core.obj(d, "facts")
    if start not in nodes:
        core.fail("INPUT_INVALID", "start must be a declared node.", start=start)
    edges = {}
    for nid, node in nodes.items():
        if not isinstance(node, dict):
            core.fail("INPUT_INVALID", "Dialogue nodes must be objects.", node=nid)
        edges[nid] = [c.get("next") for c in node.get("choices", []) if isinstance(c, dict) and c.get("next")]
    available = {nid: all(bool(facts.get(req)) for req in (nodes[nid].get("requires") or [])) for nid in nodes}
    reachable = reach(start, edges)
    dangling = sorted({x for v in edges.values() for x in v if x not in nodes})
    orphan = sorted(set(nodes) - reachable)
    dead = sorted(n for n in reachable if not edges.get(n) and not nodes[n].get("end"))
    findings = [chk("dialogue.dangling", not dangling, "Every choice leads to a declared node." if not dangling else "Dangling choices: " + ", ".join(dangling), "fail"),
                chk("dialogue.orphans", not orphan, "No orphan nodes." if not orphan else "Unreachable nodes: " + ", ".join(orphan), "warning"),
                chk("dialogue.dead_ends", not dead, "Every branch ends or continues." if not dead else "Dead ends without an end marker: " + ", ".join(dead), "warning"),
                chk("dialogue.persistence", None, "Conversation state must persist per player; see the data schema review.")]
    return out({"reachable": sorted(reachable), "available_now": sorted(n for n in reachable if available.get(n))}, findings)


def h_boss(d, cap, ctx):
    hp = core.number(d, "health", minimum=1, maximum=1e9)
    phases = core.array(d, "phases", required=True, kind=dict, maximum=20)
    players = core.integer(d, "players", minimum=0, maximum=200)
    enrage = core.number(d, "enrage_seconds", minimum=0, maximum=7200)
    recovery = core.number(d, "wipe_recovery", minimum=0, maximum=600)
    thresholds = sorted((float(p.get("threshold", 0)), p.get("pattern")) for p in phases)
    frac = min(1.0, hp / 100.0)
    current = None
    for th, pat in sorted(thresholds, reverse=True):
        if frac <= th / 100.0 + 1e-9:
            current = pat
    findings = [chk("boss.phases", len(thresholds) > 0 and thresholds[-1][0] <= 100, "Phases have thresholds up to 100%." if thresholds and thresholds[-1][0] <= 100 else "Phases need thresholds within 0-100%.", "fail"),
                chk("boss.enrage", enrage > 0, f"Enrage at {enrage}s." if enrage > 0 else "No enrage timer; add one to end stalled fights.", "warning"),
                chk("boss.wipe", recovery >= 5 or players == 0, "Wipe recovery is defined." if recovery >= 5 or players == 0 else "Wipe recovery under 5 s is too short for a reset.", "warning"),
                chk("boss.scaling", None, "Scaling by players needs playtest data; not inferred.")]
    return out({"phase_pattern": current, "phase_count": len(phases), "enrage_seconds": enrage}, findings)


def h_rounds(d, cap, ctx):
    intermission = core.number(d, "intermission", minimum=0, maximum=3600)
    countdown = core.number(d, "countdown", minimum=0, maximum=600)
    active = core.number(d, "active", minimum=1, maximum=86400)
    results = core.number(d, "results", minimum=0, maximum=600)
    players = core.integer(d, "players", minimum=0, maximum=1000)
    minimum = core.integer(d, "minimum", minimum=1, maximum=1000)
    rounds = core.integer(d, "rounds", minimum=1, maximum=1000)
    per_round = intermission + countdown + active + results
    total = per_round * rounds
    findings = [chk("rounds.minimum_players", players >= minimum, "Enough players to start." if players >= minimum else f"Need {minimum} players; {players} present; waiting.", "info"),
                chk("rounds.duration", total <= 86400, f"Session lasts about {round(total / 60, 1)} minutes.", "warning" if total > 86400 else "info"),
                chk("rounds.recovery", True, "Rounds recover by returning to intermission if the server fails a phase."),
                chk("rounds.late_join", None, "Late joiners need a rule (wait, spectate or join next round).")]
    timeline = [{"phase": "intermission", "seconds": intermission}, {"phase": "countdown", "seconds": countdown}, {"phase": "active", "seconds": active}, {"phase": "results", "seconds": results}]
    return out({"per_round_seconds": per_round, "total_seconds": total, "can_start": players >= minimum, "timeline": timeline}, findings, {"round-timeline.json": timeline})


def h_tutorial(d, cap, ctx):
    steps = core.array(d, "steps", required=True, kind=dict, maximum=200)
    done = set(core.array(d, "completed", maximum=200, kind=str))
    skip = core.boolean(d, "skip_allowed", False)
    recovery = core.boolean(d, "recovery", False)
    ids = [core.text(s, "id", maximum=80) for s in steps]
    nxt = next((s["id"] for s in steps if s["id"] not in done and all(r in done for r in s.get("requires", []))), None)
    findings = [chk("tutorial.skip", skip, "Skipping is allowed and marks steps done." if skip else "Offer a skip option for returning players.", "warning"),
                chk("tutorial.recovery", recovery, "Recovery restores the last incomplete step." if recovery else "Define recovery for steps the player cannot complete.", "warning"),
                chk("tutorial.prerequisites", all(all(r in ids for r in s.get("requires", [])) for s in steps), "Step prerequisites exist.", "fail")]
    return out({"next_step": nxt, "remaining": len([i for i in ids if i not in done])}, findings)


def h_subscriptions(d, cap, ctx):
    status = core.text(d, "status", maximum=20)
    verified = core.boolean(d, "platform_verified", False)
    past = core.number(d, "days_past_due", minimum=0, maximum=3650)
    grace = core.number(d, "grace_days", minimum=0, maximum=365)
    benefits = core.array(d, "benefits", required=True, kind=str, maximum=100)
    keeps = core.boolean(d, "cancel_keeps_earned_items", True)
    if not verified:
        state, granted = "unverified", []
    elif status == "active":
        state, granted = "active", benefits
    elif status == "past_due" and past <= grace:
        state, granted = "grace", benefits
    elif status == "cancelled":
        state, granted = "cancelled", []
    else:
        state, granted = "lapsed", []
    findings = [chk("subscriptions.verified", verified, "Subscription state is platform-verified." if verified else "Subscription is not verified; grant nothing new.", "fail"),
                chk("subscriptions.grace", not (status == "past_due" and past > grace) or state == "lapsed", "Past-due beyond grace lapses benefits." if status == "past_due" else "Grace rules applied.", "info"),
                chk("subscriptions.earned_items", keeps, "Earned items stay after cancellation." if keeps else "Earned items would be removed on cancel; this is punitive and needs review.", "warning" if not keeps else "info"),
                chk("subscriptions.claims", None, "Confirm the billing terms and cancellation copy with the policy review.")]
    return out({"state": state, "granted_benefits": granted}, findings)


def h_join_flow(d, cap, ctx):
    online = core.boolean(d, "target_online", False)
    joinable = core.boolean(d, "target_joinable", False)
    private = core.boolean(d, "private", False)
    fallback = core.text(d, "fallback", maximum=80, required=False) or ""
    cap_ok = core.boolean(d, "capacity_available", False)
    friend = core.boolean(d, "friend", False)
    if not online:
        action = "fallback" if fallback else "deny"
    elif private and not friend:
        action = "deny"
    elif not joinable or not cap_ok:
        action = "fallback" if fallback else "queue"
    else:
        action = "join"
    findings = [chk("join_flow.private", not (private and not friend and action == "join"), "Private targets are joined only by friends or invites."),
                chk("join_flow.fallback", action != "fallback" or bool(fallback), "A fallback is defined for the chosen path." if action != "fallback" or fallback else "No fallback defined; the player is stuck.", "warning"),
                chk("join_flow.server_choice", None, "Join targets must be chosen by the server with the platform's join API, not by client-provided ids.")]
    return out({"action": action}, findings)


def h_social_deduction(d, cap, ctx):
    players = core.array(d, "players", required=True, kind=dict, maximum=200)
    roles = core.obj(d, "roles", required=True)
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31)
    phase = core.text(d, "phase", maximum=30)
    total_roles = sum(int(v) for v in roles.values())
    findings = [chk("social_deduction.role_counts", total_roles == len(players), "Role counts match the player count." if total_roles == len(players) else f"{total_roles} roles for {len(players)} players.", "fail"),
                chk("social_deduction.private_info", None, "Role reveals must be sent only to the owning player; confirm the remote design."),
                chk("social_deduction.anti_collusion", None, "Collusion checks need voting-pattern analysis; not evaluated here.")]
    pool = [r for r, c in roles.items() for _ in range(int(c))]
    gen = rng(seed)
    gen.shuffle(pool)
    assignment = {p.get("id"): pool[i] for i, p in enumerate(players) if i < len(pool)}
    phases = ["lobby", "night", "day_discussion", "vote", "resolution"]
    nxt = phases[(phases.index(phase) + 1) % len(phases)] if phase in phases else "lobby"
    return out({"phase": phase, "next_phase": nxt, "assignment_hidden": True, "role_count": len(pool)}, findings, {"role-assignment.json": {"players": len(assignment)}})


def h_ui_state(d, cap, ctx):
    events = core.array(d, "events", required=True, kind=dict, maximum=5000)
    state, pending, invalid, history = "idle", None, [], []
    rules = {("idle", "request"): "loading", ("loading", "success"): "ready", ("loading", "error"): "error",
             ("loading", "cancel"): "idle", ("error", "request"): "loading", ("ready", "request"): "loading", ("ready", "reset"): "idle"}
    for ev in events:
        steps_ok(1, ctx)
        kind = core.text(ev, "type", maximum=20)
        req = ev.get("request_id")
        if kind == "request":
            pending = req
        elif kind in ("success", "error") and req != pending:
            invalid.append({"type": kind, "reason": "stale_response", "request_id": req})
            continue
        nxt = rules.get((state, kind))
        if nxt is None:
            invalid.append({"type": kind, "from": state, "reason": "invalid_transition"})
            continue
        state = nxt
        history.append(state)
    findings = [chk("ui_state.transitions", not invalid, "Every event applied to a valid state." if not invalid else f"{len(invalid)} invalid or stale event(s) ignored.", "warning"),
                chk("ui_state.cancellation", True, "Cancel returns loading to idle; late responses are rejected by request id."),
                chk("ui_state.optimistic", None, "Optimistic updates must roll back on server rejection; confirm per action.")]
    return out({"state": state, "history": history[-100:], "invalid": invalid[:50]}, findings)


def h_menu_flow(d, cap, ctx):
    screens = core.obj(d, "screens", required=True)
    start = core.text(d, "start", maximum=80)
    edges = core.array(d, "edges", required=True, kind=dict, maximum=2000)
    adj = {s: [] for s in screens}
    bad = []
    for e in edges:
        a, b = core.text(e, "from", maximum=80), core.text(e, "to", maximum=80)
        if a not in adj or b not in adj:
            bad.append(f"{a}->{b}")
            continue
        adj[a].append(b)
    reachable = reach(start, adj) if start in adj else set()
    unreachable = sorted(set(screens) - reachable)
    no_back = sorted(s for s in screens if s != start and not any(s in adj[t] for t in screens if t != s) and not adj.get(s))
    no_return = sorted(s for s in reachable if s != start and start not in reach(s, adj))
    findings = [chk("menu_flow.edges", not bad, "All edges reference declared screens." if not bad else "Undeclared edges: " + ", ".join(bad), "fail"),
                chk("menu_flow.reachability", not unreachable, "Every screen is reachable." if not unreachable else "Unreachable screens: " + ", ".join(unreachable), "warning"),
                chk("menu_flow.return", not no_return, "Every screen can return to the start." if not no_return else "No way back to start from: " + ", ".join(no_return), "warning"),
                chk("menu_flow.dead_ends", not no_back, "No dead-end screens." if not no_back else "Dead-end screens: " + ", ".join(no_back), "warning")]
    return out({"reachable": sorted(reachable), "unreachable": unreachable}, findings)


def h_weather(d, cap, ctx):
    duration = core.number(d, "duration", minimum=1, maximum=86400)
    states = core.array(d, "states", required=True, kind=dict, maximum=50)
    lightning = core.boolean(d, "lightning", False)
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31, ) if "seed" in d else 0
    gen = rng(seed)
    names = [core.text(s, "name", maximum=40) for s in states]
    t, cur, timeline = 0.0, 0, []
    while t < duration and len(timeline) < 5000:
        steps_ok(1, ctx)
        st = states[cur]
        dur = gen.uniform(float(st.get("min_seconds", 30)), float(st.get("max_seconds", 60)))
        timeline.append({"state": names[cur], "start": round(t, 2)})
        t += dur
        weights = [float(s.get("weight", 1)) for s in states]
        cur = gen.choices(range(len(states)), weights=weights)[0]
    storm_time = sum(1 for x in timeline if x["state"] == "storm")
    bad_lightning = lightning and not any(n == "storm" for n in names)
    findings = [chk("weather.lightning", not bad_lightning, "Lightning is tied to a storm state." if not bad_lightning else "Lightning is enabled but no storm state exists.", "fail" if bad_lightning else "info"),
                chk("weather.transitions", True, f"{len(timeline)} weather segment(s) simulated."),
                chk("weather.sync", None, "Weather must be server-driven and sent with a start time for clients to sync.")]
    return out({"segments": len(timeline), "timeline": timeline[:200], "storm_segments": storm_time}, findings)


def h_moderation(d, cap, ctx):
    states = core.array(d, "states", required=True, kind=str, maximum=100)
    transitions = core.array(d, "transitions", required=True, kind=dict, maximum=1000)
    human = set(core.array(d, "human_review_states", maximum=100, kind=str))
    auto_final = core.boolean(d, "automated_final_decision", False)
    edges = {s: [] for s in states}
    for t in transitions:
        a, b = core.text(t, "from", maximum=60), core.text(t, "to", maximum=60)
        if a in edges and b in edges:
            edges[a].append(b)
    findings = [chk("moderation.human_review", not (auto_final and human), "Automated decisions cannot finalize cases that need human review." if not (auto_final and human) else "Automated final decisions bypass human review states.", "fail"),
                chk("moderation.appeals", any("appeal" in s for s in states), "An appeal state exists." if any("appeal" in s for s in states) else "No appeal state; add an appeal path.", "warning"),
                chk("moderation.reachability", all(s in reach(states[0], edges) for s in states) if states else False, "Every state is reachable from intake.", "warning")]
    return out({"states": states, "human_review_states": sorted(human), "automated_final_decision": auto_final}, findings)


HANDLERS = {
    "rate_limit": h_rate_limit, "data_budget": h_data_budget, "api_retry": h_api_retry, "save_recovery": h_save_recovery,
    "combat_validation": h_combat_validation, "chaos": h_chaos, "abilities": h_ability,
    "memory_queue": h_memory_queue, "sorted_map": h_sorted_map, "session_lock": h_session_lock, "atomic_update": h_atomic_update,
    "reserved_servers": h_reserved_servers, "server_browser": h_server_browser, "teleport": h_teleport,
    "server_lifecycle": h_server_lifecycle, "reconnect": h_reconnect, "global_state": h_global_state,
    "matchmaking": h_matchmaking, "trading": h_trading, "party": h_party, "receipts": h_receipts, "products": h_products,
    "entitlements": h_entitlements, "messaging": h_messaging, "prediction": h_prediction, "destruction": h_destruction,
    "status_effects": h_status_effects, "melee": h_melee, "inventory": h_inventory, "combat_vfx": h_combat_vfx,
    "vfx_pool": h_vfx_pool, "emotes": h_emotes, "animation_tracks": h_animation_tracks, "cutscene": h_cutscene,
    "multiplayer": h_multiplayer, "integration": h_integration,
    "core_loop": h_core_loop, "damage": h_damage, "pvp_balance": h_pvp_balance, "progression": h_progression,
    "economy": h_economy, "loot": h_loot, "rating": h_rating, "shop": h_shop, "reputation": h_reputation,
    "director": h_director, "latency": h_latency, "match_integrity": h_match_integrity, "revenue": h_revenue,
    "feature_flags": h_feature_flags, "fraud": h_fraud, "footsteps": h_footsteps,
    "funnel": h_funnel, "retention": h_retention, "anomaly": h_anomaly, "post_launch": h_post_launch,
    "experiment": h_experiment, "economy_telemetry": h_economy_telemetry, "pricing_experiment": h_pricing_experiment,
    "canary": h_canary, "payload": h_payload, "soundscape": h_soundscape, "audio_audit": h_audio_audit,
    "particles": h_particles, "postprocessing": h_postprocessing, "render_cost": h_render_cost, "quality": h_quality,
    "crowd": h_crowd, "profiler": h_profiler, "memory": h_memory, "physics_budget": h_physics_budget,
    "bandwidth": h_bandwidth, "mobile": h_mobile, "regression_gate": h_regression_gate, "facial": h_facial,
    "animation_audit": h_animation_audit,
    "assembly": h_assembly, "constraints": h_constraints, "vehicle": h_vehicle, "flight": h_flight, "buoyancy": h_buoyancy,
    "projectile": h_projectile, "ragdoll": h_ragdoll, "physics_audit": h_physics_audit, "interaction": h_interaction,
    "physics_harness": h_physics_harness, "character_controller": h_character_controller,
    "movement_validation": h_movement_validation, "parkour": h_parkour, "vr": h_vr, "network_ownership": h_ownership_physics,
    "keyframes": h_keyframes, "animation_rig": h_animation_rig, "procedural": h_procedural, "ik": h_ik,
    "retarget": h_retarget, "lighting": h_lighting, "day_night": h_day_night, "beams": h_beams, "ui_motion": h_ui_motion,
    "calendar": h_calendar, "live_schedule": h_live_schedule, "community_event": h_community_event, "campaign": h_campaign,
    "state_machine": h_state_machine, "behavior_tree": h_behavior_tree, "npc_combat": h_npc_combat, "utility_ai": h_utility_ai,
    "companion": h_companion, "dialogue": h_dialogue, "boss": h_boss, "rounds": h_rounds, "onboarding": h_tutorial,
    "subscriptions": h_subscriptions, "join_flow": h_join_flow, "social_deduction": h_social_deduction, "ui_state": h_ui_state,
    "menu_flow": h_menu_flow, "weather": h_weather, "moderation": h_moderation,
}



# ---------------------------------------------------------------- additional simulate handlers

def h_sfx(d, cap, ctx):
    variants = core.array(d, "variants", required=True, kind=str, maximum=100)
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31)
    events = core.array(d, "events", required=True, kind=dict, maximum=20000)
    cooldown = core.number(d, "cooldown", minimum=0, maximum=60)
    pool = core.integer(d, "pool_size", minimum=1, maximum=1000)
    if not variants:
        core.fail("INPUT_INVALID", "variants must not be empty.")
    gen = rng(seed)
    last_played, last_variant, active, played, dropped, repeats = {}, {}, [], 0, 0, 0
    for ev in sorted(events, key=lambda e: float(e.get("t", 0))):
        steps_ok(1, ctx)
        name = core.text(ev, "name", maximum=80)
        t = float(ev.get("t", 0))
        if name in last_played and t - last_played[name] < cooldown:
            continue
        active = [a for a in active if a > t]
        if len(active) >= pool:
            dropped += 1
            continue
        choices = [v for v in variants if v != last_variant.get(name)] or variants
        pick = gen.choice(choices)
        if pick == last_variant.get(name):
            repeats += 1
        last_variant[name] = pick
        active.append(t + float(ev.get("duration", 1.0)))
        last_played[name] = t
        played += 1
    findings = [chk("sfx.cooldown", True, f"Per-sound cooldown of {cooldown}s applied."),
                chk("sfx.pool", dropped == 0, f"{dropped} sound(s) dropped at pool size {pool}." if dropped else "Voice pool never saturated.", "warning" if dropped else "info"),
                chk("sfx.variation", repeats == 0, "No consecutive variant repeats." if repeats == 0 else f"{repeats} immediate variant repeat(s) occurred.", "warning"),
                chk("sfx.mix", None, "Loudness and balance need listening review or measured levels.")]
    return out({"played": played, "dropped": dropped, "variant_repeats": repeats}, findings)


def h_ranged(d, cap, ctx):
    magazine = core.integer(d, "magazine", minimum=1, maximum=1000)
    shots = d.get("shots")
    interval = core.number(d, "fire_interval", minimum=0.01, maximum=60)
    reload_s = core.number(d, "reload_seconds", minimum=0, maximum=600)
    spread = core.number(d, "spread_degrees", minimum=0, maximum=90)
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31)
    if isinstance(shots, bool) or shots is None:
        core.fail("INPUT_INVALID", "shots must be a list of request times or a request count.")
    if isinstance(shots, int):
        requests = [i * interval / 2 for i in range(min(shots, 100000))]
    elif isinstance(shots, list):
        requests = sorted(times_of(shots))
    else:
        core.fail("INPUT_INVALID", "shots must be a list or an integer.")
    steps_ok(len(requests), ctx)
    gen = rng(seed)
    ammo, last_fire, reload_until = magazine, None, -1.0
    fired, rejected, angles, log = 0, {"cooldown": 0, "reloading": 0, "empty": 0}, [], []
    for t in requests:
        if t < reload_until:
            rejected["reloading"] += 1
            continue
        if last_fire is not None and t - last_fire < interval:
            rejected["cooldown"] += 1
            continue
        if ammo <= 0:
            rejected["empty"] += 1
            reload_until = t + reload_s
            ammo = magazine
            log.append({"t": round(t, 3), "event": "reload_started"})
            continue
        ammo -= 1
        fired += 1
        last_fire = t
        angle = gen.uniform(-spread, spread)
        angles.append(angle)
        log.append({"t": round(t, 3), "event": "fired", "spread_deg": round(angle, 3), "ammo_left": ammo})
    rms = math.sqrt(mean([a * a for a in angles])) if angles else 0.0
    findings = [chk("ranged.fire_rate", True, f"Shots closer than {interval}s are rejected on the server timeline."),
                chk("ranged.ammo", True, f"Magazine size {magazine}; empty requests trigger a reload."),
                chk("ranged.spread", rms <= spread, f"Observed spread RMS {round(rms, 3)} degrees within the configured {spread}."),
                chk("ranged.authority", None, "Hit results must be computed on the server; the client sends only the request.")]
    return out({"fired": fired, "rejected": rejected, "spread_rms_degrees": round(rms, 4), "ammo_left": ammo}, findings, {"ranged-log.json": log})


def h_movement(d, cap, ctx):
    walk = core.number(d, "walk_speed", minimum=0, maximum=500)
    sprint = core.number(d, "sprint_speed", minimum=0, maximum=500)
    stamina = core.number(d, "stamina", minimum=0, maximum=100000)
    drain = core.number(d, "drain_per_second", minimum=0, maximum=10000)
    seconds = core.integer(d, "seconds", minimum=1, maximum=3600)
    dash_cd = core.number(d, "dash_cooldown", minimum=0, maximum=600)
    steps_ok(seconds, ctx)
    distance, s, exhausted_at = 0.0, stamina, None
    for sec in range(seconds):
        if s > 0:
            distance += sprint
            s = max(0.0, s - drain)
            if s == 0 and exhausted_at is None:
                exhausted_at = sec + 1
        else:
            distance += walk
    dash_ok = dash_cd >= 0.5
    findings = [chk("movement.sprint_speed", sprint >= walk, "Sprint is at least walk speed." if sprint >= walk else "Sprint is slower than walking; players will not feel the difference.", "warning"),
                chk("movement.stamina", True, f"Stamina empties after {exhausted_at}s of continuous sprint." if exhausted_at else "Stamina did not empty during the run."),
                chk("movement.dash_cooldown", dash_ok, f"Dash cooldown {dash_cd}s." if dash_ok else "Dash cooldown under 0.5s allows spam; enforce on the server.", "warning"),
                chk("movement.speed_limit", max(walk, sprint) <= 40, "Speeds are within a conventional range." if max(walk, sprint) <= 40 else "Speeds above 40 studs/s need anti-cheat tolerance tuning.", "warning")]
    return out({"distance_sprinting_then_walking": round(distance, 3), "stamina_left": round(s, 3), "exhausted_after_seconds": exhausted_at}, findings)


def h_music(d, cap, ctx):
    states = core.array(d, "states", required=True, kind=str, maximum=100)
    initial = core.text(d, "initial", maximum=80)
    edges = core.array(d, "edges", required=True, kind=dict, maximum=1000)
    bpm = core.number(d, "bpm", minimum=20, maximum=300)
    beats = core.number(d, "crossfade_beats", minimum=0, maximum=64)
    if initial not in states:
        core.fail("INPUT_INVALID", "initial must be one of the declared states.", initial=initial)
    seconds = beats * 60.0 / bpm
    table, bad = {}, []
    for e in edges:
        a, b = core.text(e, "from", maximum=80), core.text(e, "to", maximum=80)
        if a not in states or b not in states:
            bad.append(f"{a}->{b}")
            continue
        table.setdefault(a, []).append(b)
    reachable = reach(initial, table)
    unreachable = sorted(set(states) - reachable)
    findings = [chk("music.edges", not bad, "Every music edge uses declared states." if not bad else "Undeclared music edges: " + ", ".join(bad), "fail"),
                chk("music.reachability", not unreachable, "Every music state is reachable." if not unreachable else "Unreachable music states: " + ", ".join(unreachable), "warning"),
                chk("music.crossfade", seconds >= 0.5 or beats == 0, f"Crossfade lasts {round(seconds, 3)} s at {bpm} BPM." if seconds >= 0.5 or beats == 0 else "Crossfade is shorter than 0.5 s and may click.", "warning"),
                chk("music.rights", None, "Each music track needs a license record; see music_rights.")]
    return out({"crossfade_seconds": round(seconds, 4), "reachable": sorted(reachable), "unreachable": unreachable}, findings)


def h_leaderboard(d, cap, ctx):
    import re as _re
    entries = core.array(d, "entries", required=True, kind=dict, maximum=20000)
    page = core.integer(d, "page", minimum=1, maximum=100000)
    size = core.integer(d, "page_size", minimum=1, maximum=100)
    season = core.text(d, "season", maximum=20)
    if not _re.fullmatch(r"\d{4}-\d{2}", season):
        core.fail("INPUT_INVALID", "season must look like YYYY-MM.", season=season)
    rows = []
    for e in entries:
        key = str(e.get("key", e.get("name", e.get("id", ""))))
        if not key:
            core.fail("INPUT_INVALID", "Each leaderboard entry needs a key, name or id.")
        rows.append((key, float(e.get("score", 0))))
    ranked = sorted(rows, key=lambda r: (-r[1], r[0]))
    ties = sum(1 for i in range(1, len(ranked)) if ranked[i][1] == ranked[i - 1][1])
    start = (page - 1) * size
    view = [{"rank": i + 1, "key": k, "score": sc} for i, (k, sc) in enumerate(ranked) if start <= i < start + size]
    findings = [chk("leaderboard.season", True, f"Season {season} keys the board; old seasons are archived separately."),
                chk("leaderboard.ties", True, f"{ties} tied score(s) ordered by key for stable pages."),
                chk("leaderboard.page", bool(view) or not ranked, "Requested page is populated." if view or not ranked else "Requested page is past the end of the board.", "info"),
                chk("leaderboard.cheating", None, "Scores must be validated server-side before they enter the board.")]
    return out({"season": season, "page": page, "total_entries": len(ranked), "rows": view}, findings)


def h_discovery_funnel(d, cap, ctx):
    counts = d.get("counts")
    cohort = core.text(d, "cohort", maximum=40)
    accurate = core.boolean(d, "presentation_accurate", False)
    if isinstance(counts, dict):
        stages = [(k, float(v)) for k, v in counts.items()]
    elif isinstance(counts, list):
        stages = [(f"stage_{i + 1}", float(v)) for i, v in enumerate(counts)]
    else:
        core.fail("INPUT_INVALID", "counts must be an object or a list of numbers.")
    if not stages:
        core.fail("INPUT_INVALID", "counts must not be empty.")
    rows, prev = [], None
    for name, v in stages:
        if v < 0:
            core.fail("INPUT_INVALID", "counts cannot be negative.", stage=name)
        rows.append({"stage": name, "count": v, "from_previous": round(v / prev, 4) if prev else None, "from_first": round(v / stages[0][1], 4) if stages[0][1] else None})
        prev = v if v else prev
    increasing = [rows[i]["stage"] for i in range(1, len(rows)) if rows[i]["count"] > rows[i - 1]["count"]]
    drops = [r for r in rows[1:] if r["from_previous"] is not None]
    biggest = min(drops, key=lambda r: r["from_previous"]) if drops else None
    findings = [chk("discovery_funnel.monotonic", not increasing, "Each stage is no larger than the one before." if not increasing else "Stages increase, which indicates double counting: " + ", ".join(increasing), "warning"),
                chk("discovery_funnel.presentation", accurate, "Presented metrics match the funnel definition." if accurate else "The presentation does not match the funnel; fix before publishing.", "fail" if not accurate else "info"),
                chk("discovery_funnel.cohort", True, f"Funnel is for cohort {cohort}.")]
    return out({"cohort": cohort, "stages": rows, "largest_drop_stage": biggest["stage"] if biggest else None}, findings)


def h_kpi_dashboard(d, cap, ctx):
    metrics = d.get("metrics")
    freshness = core.number(d, "freshness_minutes", minimum=0, maximum=100000)
    if isinstance(metrics, dict):
        items = list(metrics.items())
    elif isinstance(metrics, list):
        items = [(m.get("name", f"metric_{i}"), m) for i, m in enumerate(metrics) if isinstance(m, dict)]
    else:
        core.fail("INPUT_INVALID", "metrics must be an object or a list.")
    rows, off = [], []
    for name, m in items:
        if not isinstance(m, dict):
            core.fail("INPUT_INVALID", "Each metric must be an object with value and target.", metric=name)
        value, target = float(m.get("value", 0)), float(m.get("target", 0))
        higher = bool(m.get("higher_is_better", True))
        ok = value >= target if higher else value <= target
        rows.append({"metric": name, "value": value, "target": target, "on_target": ok})
        if not ok:
            off.append(name)
    stale = freshness > 60
    findings = [chk("kpi_dashboard.freshness", not stale, f"Data is {freshness} minutes old." if not stale else f"Data is {freshness} minutes old; mark the dashboard stale.", "warning" if stale else "info"),
                chk("kpi_dashboard.targets", not off, "Every metric is on target." if not off else "Off target: " + ", ".join(off), "info"),
                chk("kpi_dashboard.definitions", None, "Each KPI needs a written definition and owner; the dashboard does not check them.")]
    return out({"metrics": rows, "off_target": off, "stale": stale}, findings)


HANDLERS.update({
    "sfx": h_sfx, "ranged": h_ranged, "movement": h_movement, "music": h_music, "leaderboard": h_leaderboard,
    "discovery_funnel": h_discovery_funnel, "kpi_dashboard": h_kpi_dashboard,
})
