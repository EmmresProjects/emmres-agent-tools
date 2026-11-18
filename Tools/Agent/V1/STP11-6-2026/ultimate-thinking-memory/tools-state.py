"""Persistent state and governance handlers.

Families: L (hash-chained ledgers), B (versioned knowledge library), M (scoped memory with
sensitivity, expiry, and conflict handling), V (action governance: classification, approval
gate, preview, idempotency, stop checks, escalation, audit, least privilege, rollback plan).
Approval is required for personal-data memory, deletion of expired memory, restores into
non-empty folders, and any gated action classified high or critical. Loaded by tools-engine.py.
"""
import datetime
import importlib.util
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
HANDLERS = {}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,60}$")
RESERVED = {"ledger", "limit", "where", "field", "as_of", "base", "library", "memory", "key", "query", "k",
            "include_retired", "include_personal", "delete", "reason", "approved", "manifest", "target", "name",
            "skill_tool", "other", "required", "window", "relation", "direction", "changed", "top", "check_dir",
            "docs", "request", "budget_tokens", "per_class", "min_recall", "threshold", "nominal", "delta", "max_share",
            "critical_below", "pass_threshold", "limit", "status", "archive", "title", "modules", "risk_aversion",
            "horizon", "deadline", "tolerance", "steps", "sense", "ledger_name", "n", "seed", "percentile", "check"}


def _load_core():
    name = "tools_core_shared"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TOOL_DIR / "tools-core.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()


def handler(key):
    def register(fn):
        HANDLERS[key] = fn
        return fn
    return register


def _ledger_path(ctx, name):
    if not NAME_RE.match(name):
        raise core.InputError("ledger/library/memory names must be lowercase letters, digits, dots, hyphens")
    return ctx.state_dir / "ledgers" / f"{name}.jsonl"


def _data_from_input_or_options(ctx):
    if ctx.input_path is not None:
        if ctx.input_path.suffix.lower() == ".json":
            data = ctx.read_json()
            if not isinstance(data, dict):
                raise core.InputError("record input must be a JSON object")
            return data
        return {"text": ctx.read_text()}
    data = {k: v for k, v in ctx.options.items() if k not in RESERVED}
    if not data:
        raise core.InputError("provide --input <record.json> or record fields as --option key=value")
    return data


# ====================================================================== L
@handler("L:append")
def l_append(ctx):
    name = ctx.opt("ledger") or ctx.slug
    path = _ledger_path(ctx, name)
    data = _data_from_input_or_options(ctx)
    rec = core.ledger_append(path, ctx.slug, data, ctx.policy)
    return {"summary": {"ledger": name, "seq": rec["seq"], "hash": rec["hash"][:16] + "...", "ts": rec["ts"]},
            "items": [{"seq": rec["seq"], "ts": rec["ts"], "record": core.excerpt(core.canonical(data), 200)}]}


@handler("L:list")
def l_list(ctx):
    name = ctx.opt("ledger") or ctx.slug
    records, problems = core.ledger_read(_ledger_path(ctx, name), ctx.policy)
    where = ctx.opt("where")
    if where:
        if "=" not in where:
            raise core.InputError("where must look like field=value")
        key, value = where.split("=", 1)
        records = [r for r in records if str(r.get("data", {}).get(key)) == value]
    limit = ctx.opt_int("limit", 20, 1, 1000)
    shown = records[-limit:]
    items = [{"seq": r["seq"], "ts": r["ts"], "record": core.excerpt(core.canonical(r["data"]), 200)} for r in reversed(shown)]
    return {"summary": {"ledger": name, "matching": len(records), "shown": len(items), "damaged_lines": len(problems)},
            "items": items}


@handler("L:verify")
def l_verify(ctx):
    name = ctx.opt("ledger") or ctx.slug
    records, problems = core.ledger_read(_ledger_path(ctx, name), ctx.policy)
    ok, bad_seq, detail = core.ledger_verify(records)
    items = [{"problem": p["problem"], "line": p["line"]} for p in problems]
    if not ok:
        items.append({"problem": detail, "line": bad_seq})
    return {"summary": {"ledger": name, "records": len(records), "chain": "intact" if ok and not problems else "broken",
                        "damaged_lines": len(problems)},
            "items": items,
            "recovery": "" if ok and not problems else "Restore the ledger from the latest checkpoint or move the damaged file aside; do not edit hashes by hand."}


@handler("L:group")
def l_group(ctx):
    name = ctx.opt("ledger") or ctx.slug
    field = ctx.opt("field")
    if not field:
        raise core.InputError("option field=<data field to group by> is required")
    records, _ = core.ledger_read(_ledger_path(ctx, name), ctx.policy)
    counts = Counter(str(r.get("data", {}).get(field, "(missing)")) for r in records)
    items = [{"value": k, "count": v} for k, v in counts.most_common()]
    return {"summary": {"ledger": name, "field": field, "records": len(records), "distinct": len(items)},
            "items": items, "note": "Repeated values are candidate patterns; confirm the cause before writing a rule."}


# ====================================================================== B
def _library_path(ctx):
    name = ctx.opt("library") or "knowledge-library"
    if not NAME_RE.match(name):
        raise core.InputError("library name must be lowercase letters, digits, dots, hyphens")
    return ctx.state_dir / "library" / f"{name}.json", name


@handler("B:add")
def b_add(ctx):
    data = _data_from_input_or_options(ctx)
    title = str(data.get("title", "")).strip()
    key = str(data.get("key") or core.slugify(title or ctx.slug))
    if not NAME_RE.match(key):
        key = core.slugify(key)
    body = str(data.get("body", data.get("text", ""))).strip()
    if len(body) > 20000:
        raise core.InputError("body is larger than 20000 characters; store a file reference instead")
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        raise core.InputError("tags must be a list")
    fields = {"title": title or key, "body": body, "tags": [str(t) for t in tags],
              "trigger": str(data.get("trigger", "")), "source": str(data.get("source", "")),
              "capability": ctx.slug}
    path, lib = _library_path(ctx)
    now = core.now_iso()

    def mutate(store):
        entries = store.setdefault("entries", {})
        if key not in entries:
            entries[key] = {"key": key, "status": "active", "versions": [{"version": 1, "ts": now, **fields}],
                            "current_version": 1}
            return "created", 1
        entry = entries[key]
        current = entry["versions"][-1]
        same = all(current.get(k) == v for k, v in fields.items() if k != "capability")
        if same:
            return "unchanged", entry["current_version"]
        if entry.get("status") == "retired":
            entry["status"] = "active"
        version = len(entry["versions"]) + 1
        entry["versions"].append({"version": version, "ts": now, **fields})
        entry["current_version"] = version
        return "updated", version

    status, version = core.locked_json_update(path, {"library": lib, "entries": {}}, mutate, ctx.policy)
    return {"summary": {"library": lib, "key": key, "status": status, "version": version},
            "items": [{"key": key, "status": status, "version": version, "title": fields["title"]}]}


def _tokens(text, policy):
    return core.content_words(text, policy["stopwords"])


@handler("B:search")
def b_search(ctx):
    query = ctx.opt("query")
    if not query and ctx.input_path is not None:
        query = ctx.read_text()
    if not query:
        raise core.InputError("provide option query=<text> or --input <text file> describing the situation")
    path, lib = _library_path(ctx)
    store = core.read_json_file(path, ctx.policy) if path.exists() else {"entries": {}}
    qset = _tokens(query, ctx.policy)
    include_retired = ctx.opt_bool("include_retired", False)
    k = ctx.opt_int("k", 5, 1, 100)
    rows = []
    for key, entry in store.get("entries", {}).items():
        ctx.tick()
        if entry.get("status") == "retired" and not include_retired:
            continue
        cur = entry["versions"][-1]
        title_hits = qset & _tokens(cur.get("title", ""), ctx.policy)
        tag_hits = qset & _tokens(" ".join(cur.get("tags", [])), ctx.policy)
        trig_hits = qset & _tokens(cur.get("trigger", ""), ctx.policy)
        body_hits = qset & _tokens(cur.get("body", ""), ctx.policy)
        score = 3 * len(title_hits) + 2 * len(tag_hits) + 2 * len(trig_hits) + len(body_hits)
        if score:
            rows.append({"key": key, "score": score, "title": cur.get("title", ""), "version": entry["current_version"],
                         "status": entry.get("status", "active"),
                         "matched": ", ".join(sorted(title_hits | tag_hits | trig_hits | body_hits))[:200]})
    rows.sort(key=lambda r: (-r["score"], r["key"]))
    if not rows:
        ctx.warnings.append("no library entry matches this query; create one with B:add after the work is verified")
    return {"summary": {"library": lib, "entries": len(store.get("entries", {})), "matches": len(rows),
                        "returned": min(k, len(rows))}, "items": rows[:k]}


@handler("B:versions")
def b_versions(ctx):
    key = ctx.opt("key")
    if not key:
        raise core.InputError("option key=<entry key> is required")
    path, lib = _library_path(ctx)
    if not path.exists():
        raise core.InputError("library does not exist yet; add an entry first")
    store = core.read_json_file(path, ctx.policy)
    entry = store.get("entries", {}).get(key)
    if entry is None:
        raise core.InputError(f"no library entry named {key!r}")
    items = [{"version": v["version"], "ts": v["ts"], "title": v.get("title", ""),
              "summary": core.excerpt(v.get("body", ""), 120)} for v in entry["versions"]]
    return {"summary": {"key": key, "versions": len(items), "current": entry["current_version"],
                        "status": entry.get("status", "active")}, "items": items}


@handler("B:retire")
def b_retire(ctx):
    key = ctx.opt("key")
    if not key:
        raise core.InputError("option key=<entry key> is required")
    path, lib = _library_path(ctx)
    reason = ctx.opt("reason", "superseded or no longer valid")
    now = core.now_iso()

    def mutate(store):
        entry = store.get("entries", {}).get(key)
        if entry is None:
            raise core.InputError(f"no library entry named {key!r}")
        entry["status"] = "retired"
        entry["retired"] = {"ts": now, "reason": reason}
        return entry["current_version"]

    version = core.locked_json_update(path, {"library": lib, "entries": {}}, mutate, ctx.policy)
    return {"summary": {"key": key, "status": "retired", "version": version, "note": "retired, not deleted"},
            "items": [{"key": key, "reason": reason}]}


# ====================================================================== M
SENSITIVITY = ("public", "internal", "personal", "secret")


def _memory_path(ctx):
    name = ctx.opt("memory") or "task-memory"
    if not NAME_RE.match(name):
        raise core.InputError("memory name must be lowercase letters, digits, dots, hyphens")
    return ctx.state_dir / "memory" / f"{name}.json", name


def _norm(value):
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _durability(entry, policy):
    d = policy["memory_durability"]
    if entry.get("expires"):
        return d["ttl"]
    if entry.get("review_by"):
        return d["review"]
    return d["permanent"]


def _live(entry, as_of):
    if entry.get("status") not in ("active", "conflict"):
        return False
    if entry.get("expires") and entry["expires"] < as_of.isoformat():
        return False
    return True


@handler("M:add")
def m_add(ctx):
    data = _data_from_input_or_options(ctx)
    if "key" not in data or "value" not in data:
        raise core.InputError("memory records need 'key' and 'value'")
    sensitivity = str(data.get("sensitivity", "internal"))
    if sensitivity not in SENSITIVITY:
        raise core.InputError(f"sensitivity must be one of {', '.join(SENSITIVITY)}")
    if sensitivity == "secret":
        raise core.InputError("secrets must not be stored in memory; store a reference to the secret manager instead")
    if sensitivity == "personal" and not ctx.approve:
        ctx.require_approval("personal information needs explicit approval before it is stored in memory",
                             {"key": str(data["key"]), "sensitivity": sensitivity})
    try:
        confidence = float(data.get("confidence", 0.7))
    except (TypeError, ValueError) as exc:
        raise core.InputError("confidence must be a number between 0 and 1") from exc
    if not 0 <= confidence <= 1 or not math.isfinite(confidence):
        raise core.InputError("confidence must be between 0 and 1")
    as_of = ctx.as_of()
    expires = None
    if data.get("expires"):
        expires = core.parse_date(data["expires"], "expires").isoformat()
    elif data.get("ttl_days") is not None:
        expires = (as_of + datetime.timedelta(days=int(data["ttl_days"]))).isoformat()
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        raise core.InputError("tags must be a list")
    path, mem = _memory_path(ctx)
    key = str(data["key"]).strip()
    value = str(data["value"]).strip()
    if not key or not value or len(value) > 4000:
        raise core.InputError("key must be non-empty and value must be 1-4000 characters")
    now = core.now_iso()

    def mutate(store):
        entries = store.setdefault("entries", [])
        same_key = [e for e in entries if _norm(e["key"]) == _norm(key) and _live(e, as_of)]
        for e in same_key:
            if _norm(e["value"]) == _norm(value):
                e["uses"] = e.get("uses", 0) + 1
                e["last_reinforced"] = now
                return {"status": "reinforced", "id": e["id"], "conflicts_with": []}
        store["counter"] = store.get("counter", 0) + 1
        new_id = f"m-{store['counter']:04d}"
        status = "conflict" if same_key else "active"
        for e in same_key:
            e["status"] = "conflict"
        entry = {"id": new_id, "key": key, "value": value, "scope": str(data.get("scope", "task")),
                 "sensitivity": sensitivity, "confidence": confidence, "source": str(data.get("source", "user-provided")),
                 "created": now, "expires": expires, "review_by": data.get("review_by"),
                 "tags": [str(t) for t in tags], "exceptions": data.get("exceptions", ""),
                 "uses": 0, "status": status, "capability": ctx.slug}
        entries.append(entry)
        return {"status": status, "id": new_id, "conflicts_with": [e["id"] for e in same_key]}

    result = core.locked_json_update(path, {"memory": mem, "entries": [], "counter": 0}, mutate, ctx.policy)
    return {"summary": {"memory": mem, **result, "expires": expires or "none"},
            "items": [{"id": result["id"], "status": result["status"], "key": key,
                       "conflicts_with": ", ".join(result["conflicts_with"])}]}


def _entries(ctx):
    path, mem = _memory_path(ctx)
    if not path.exists():
        return path, mem, []
    return path, mem, core.read_json_file(path, ctx.policy).get("entries", [])


@handler("M:recall")
def m_recall(ctx):
    query = ctx.opt("query")
    if not query and ctx.input_path is not None:
        query = ctx.read_text()
    if not query:
        raise core.InputError("provide option query=<text> or --input <text file>")
    _, mem, entries = _entries(ctx)
    qset = _tokens(query, ctx.policy)
    as_of = ctx.as_of()
    include_personal = ctx.opt_bool("include_personal", False)
    k = ctx.opt_int("k", 5, 1, 100)
    rows = []
    for e in entries:
        if not _live(e, as_of) or (e["sensitivity"] == "personal" and not include_personal):
            continue
        hits = qset & _tokens(f"{e['key']} {e['value']} {' '.join(e.get('tags', []))}", ctx.policy)
        if hits:
            score = len(hits) / math.sqrt(len(qset)) * e["confidence"]
            rows.append({"id": e["id"], "key": e["key"], "value": core.excerpt(e["value"], 160), "scope": e["scope"],
                         "confidence": e["confidence"], "status": e["status"], "score": round(score, 3),
                         "matched": ", ".join(sorted(hits))})
    rows.sort(key=lambda r: (-r["score"], r["id"]))
    return {"summary": {"memory": mem, "candidates": len(rows), "returned": min(k, len(rows)),
                        "personal_included": include_personal}, "items": rows[:k],
            "note": "Use only the returned entries; the set is intentionally small to protect context."}


@handler("M:rank")
def m_rank(ctx):
    _, mem, entries = _entries(ctx)
    as_of = ctx.as_of()
    rows = []
    for e in entries:
        if not _live(e, as_of):
            continue
        importance = e["confidence"] * _durability(e, ctx.policy) * (1 + math.log(1 + e.get("uses", 0)))
        rows.append({"id": e["id"], "key": e["key"], "importance": round(importance, 3),
                     "durability": "ttl" if e.get("expires") else ("review" if e.get("review_by") else "permanent"),
                     "uses": e.get("uses", 0)})
    rows.sort(key=lambda r: (-r["importance"], r["id"]))
    return {"summary": {"memory": mem, "ranked": len(rows)}, "items": rows}


@handler("M:consolidate")
def m_consolidate(ctx):
    path, mem, _ = _entries(ctx)
    if not path.exists():
        raise core.InputError("memory store does not exist yet")
    as_of = ctx.as_of()

    def mutate(store):
        groups = defaultdict(list)
        for e in store.get("entries", []):
            if _live(e, as_of):
                groups[(_norm(e["key"]), _norm(e["value"]))].append(e)
        items = []
        for (k, v), members in groups.items():
            if len(members) < 2:
                continue
            members.sort(key=lambda e: (-e["confidence"], e["created"], e["id"]))
            keeper = members[0]
            for other in members[1:]:
                other["status"] = "superseded"
                other["superseded_by"] = keeper["id"]
                keeper.setdefault("provenance", [keeper["source"]])
                if other["source"] not in keeper["provenance"]:
                    keeper["provenance"].append(other["source"])
                items.append({"kept": keeper["id"], "superseded": other["id"], "key": keeper["key"]})
        return items

    items = core.locked_json_update(path, {"memory": mem, "entries": [], "counter": 0}, mutate, ctx.policy)
    return {"summary": {"memory": mem, "superseded": len(items)}, "items": items,
            "note": "Duplicates are marked superseded, not deleted; provenance is kept on the keeper."}


@handler("M:expire")
def m_expire(ctx):
    path, mem, _ = _entries(ctx)
    if not path.exists():
        raise core.InputError("memory store does not exist yet")
    as_of = ctx.as_of().isoformat()
    delete = ctx.opt_bool("delete", False)
    if delete and not ctx.approve:
        ctx.require_approval("deleting expired memory is irreversible; a person must approve",
                             {"memory": mem, "as_of": as_of})

    def mutate(store):
        marked = []
        for e in store.get("entries", []):
            if e.get("status") in ("active", "conflict") and e.get("expires") and e["expires"] < as_of:
                e["status"] = "expired"
                marked.append(e["id"])
        removed = []
        if delete:
            keep = []
            for e in store.get("entries", []):
                if e.get("status") == "expired":
                    removed.append(e["id"])
                else:
                    keep.append(e)
            store["entries"] = keep
        return marked, removed

    marked, removed = core.locked_json_update(path, {"memory": mem, "entries": [], "counter": 0}, mutate, ctx.policy)
    items = [{"id": i, "action": "marked expired"} for i in marked] + [{"id": i, "action": "deleted"} for i in removed]
    return {"summary": {"memory": mem, "as_of": as_of, "marked_expired": len(marked), "deleted": len(removed)},
            "items": items}


@handler("M:conflicts")
def m_conflicts(ctx):
    _, mem, entries = _entries(ctx)
    as_of = ctx.as_of()
    groups = defaultdict(list)
    for e in entries:
        if _live(e, as_of):
            groups[_norm(e["key"])].append(e)
    items = []
    for key, members in groups.items():
        values = {_norm(m["value"]) for m in members}
        if len(values) < 2:
            continue
        newest = max(members, key=lambda m: (m["created"], m["confidence"]))
        best = max(members, key=lambda m: (m["confidence"], m["created"]))
        items.append({"key": key, "values": " | ".join(f"{m['id']}={core.excerpt(m['value'], 60)}" for m in members),
                      "newest": newest["id"], "most_confident": best["id"],
                      "resolution": "keep both until a person confirms; prefer the newer, more confident entry only if no evidence contradicts it"})
    return {"summary": {"memory": mem, "conflicting_keys": len(items)}, "items": items}


# ====================================================================== V
GOV_TIERS = ("low", "medium", "high", "critical")


def _kw_regex(kw):
    if kw.endswith("*"):
        return re.compile(r"(?<![a-z])" + re.escape(kw[:-1]) + r"[a-z]*", re.I)
    return re.compile(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", re.I)


def load_actions(ctx):
    if ctx.input_path is not None and ctx.input_path.suffix.lower() == ".json":
        data = ctx.read_json()
        raw = data.get("actions", data) if isinstance(data, dict) else data
        if not isinstance(raw, list) or not raw:
            raise core.InputError("actions must be a non-empty list (or an object with an 'actions' list)")
    else:
        lines = [l.strip() for l in ctx.read_text().splitlines() if l.strip()]
        if not lines:
            raise core.InputError("no actions found in input")
        raw = [{"description": l} for l in lines]
    if len(raw) > ctx.policy["limits"]["max_items"]:
        raise core.InputError("too many actions")
    actions = []
    for n, a in enumerate(raw, start=1):
        if isinstance(a, str):
            a = {"description": a}
        if not isinstance(a, dict):
            raise core.InputError(f"action {n} must be an object or a string")
        targets = a.get("targets", [])
        if isinstance(targets, str):
            targets = [targets]
        actions.append({"id": str(a.get("id", f"A{n}")), "description": str(a.get("description", "")),
                        "targets": [str(t) for t in targets], "reversible": bool(a.get("reversible", True)),
                        "rollback": str(a.get("rollback", "")), "idempotency_key": a.get("idempotency_key"),
                        "allowed": a.get("allowed")})
    return actions


def classify(action, policy):
    text = " ".join([action["description"]] + action["targets"])
    kw = policy["governance_keywords"]
    matched = {cls: [w for w in words if _kw_regex(w).search(text)] for cls, words in kw.items()}
    hit = {cls for cls, ws in matched.items() if ws}
    rev = action["reversible"]
    if hit & {"credential", "privileged"}:
        tier = "critical"
    elif (hit & {"destructive", "irreversible", "financial"}) and not rev:
        tier = "critical"
    elif hit & {"irreversible", "external", "privacy", "identity_legal", "physical", "financial", "destructive"}:
        tier = "high"
    elif "modify" in hit:
        tier = "medium"
    else:
        tier = "low"
    approval = tier in policy["approval_tiers"]
    return {"tier": tier, "approval_required": approval, "categories": sorted(hit)}


@handler("V:classify")
def v_classify(ctx):
    actions = load_actions(ctx)
    items, counts = [], Counter()
    for a in actions:
        ctx.tick()
        c = classify(a, ctx.policy)
        counts[c["tier"]] += 1
        items.append({"id": a["id"], "tier": c["tier"], "approval": "required" if c["approval_required"] else "not required",
                      "reversible": "yes" if a["reversible"] else "no", "categories": ", ".join(c["categories"]) or "none",
                      "action": core.excerpt(a["description"], 120)})
    return {"summary": {"actions": len(actions), **{t: counts.get(t, 0) for t in GOV_TIERS},
                        "approval_required": sum(1 for i in items if i["approval"] == "required")},
            "items": items, "note": "Keyword classification is assisted: a person confirms the tier of each action."}


@handler("V:gate")
def v_gate(ctx):
    actions = load_actions(ctx)
    approved_raw = ctx.opt("approved", "")
    approved = {x.strip() for x in approved_raw.split(",") if x.strip()}
    needing = []
    items = []
    for a in actions:
        c = classify(a, ctx.policy)
        items.append({"id": a["id"], "tier": c["tier"], "approval": "required" if c["approval_required"] else "not required",
                      "approved": "yes" if (ctx.approve or a["id"] in approved or not c["approval_required"]) else "no"})
        if c["approval_required"] and not (ctx.approve or a["id"] in approved):
            needing.append(a["id"])
    if needing:
        ctx.require_approval("these actions need a human approval before they run: " + ", ".join(needing),
                             {"pending_ids": needing})
    return {"summary": {"actions": len(actions), "verdict": "proceed", "approved_ids": len(approved)},
            "items": items, "note": "This gate does not execute anything; the caller runs the approved actions."}


@handler("V:preview")
def v_preview(ctx):
    actions = load_actions(ctx)
    rows, warnings = [], []
    for a in actions:
        c = classify(a, ctx.policy)
        if not a["reversible"] and not a["rollback"]:
            warnings.append(f"{a['id']} is irreversible and has no rollback or compensating step")
        rows.append({"id": a["id"], "action": a["description"], "targets": ", ".join(a["targets"]) or "(unspecified)",
                     "reversible": "yes" if a["reversible"] else "no", "tier": c["tier"],
                     "rollback": a["rollback"] or "(none)"})
    lines = ["# Action preview", "", f"- Generated (UTC): {core.now_iso()}", f"- Actions: {len(rows)}",
             "- Nothing has been executed. Approve the gated actions before running them.", "",
             "| ID | Action | Targets | Reversible | Tier | Rollback |", "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append("| " + " | ".join(str(r[k]).replace("|", "/") for k in ("id", "action", "targets", "reversible", "tier", "rollback")) + " |")
    if warnings:
        lines += ["", "## Warnings"] + [f"- {w}" for w in warnings]
    path = ctx.write_artifact(f"{ctx.slug}.preview.md", "\n".join(lines) + "\n")
    return {"summary": {"actions": len(rows), "warnings": len(warnings), "artifact": path},
            "items": rows, "warnings_list": warnings}


@handler("V:idempotent")
def v_idempotent(ctx):
    actions = load_actions(ctx)
    keyed = [a for a in actions if a["idempotency_key"]]
    if not keyed:
        raise core.InputError("each action needs an 'idempotency_key' for this operation")
    path = ctx.state_dir / "governance" / "idempotency.json"
    now = core.now_iso()

    def mutate(store):
        out = []
        for a in keyed:
            k = str(a["idempotency_key"])
            if k in store:
                out.append({"id": a["id"], "key": k, "status": "duplicate: do not execute again",
                            "first_seen": store[k]["first_seen"], "first_action": store[k]["action_id"]})
            else:
                store[k] = {"first_seen": now, "action_id": a["id"], "description": core.excerpt(a["description"], 120)}
                out.append({"id": a["id"], "key": k, "status": "new: registered", "first_seen": now, "first_action": a["id"]})
        return out

    items = core.locked_json_update(path, {}, mutate, ctx.policy)
    dup = sum(1 for i in items if i["status"].startswith("duplicate"))
    return {"summary": {"actions": len(items), "duplicates": dup, "new": len(items) - dup}, "items": items}


@handler("V:stop-check")
def v_stop_check(ctx):
    data = ctx.read_json()
    if not isinstance(data, dict) or not isinstance(data.get("limits"), dict):
        raise core.InputError("stop-check input needs 'limits' (max_<metric>: number) and 'observed' (<metric>: number)")
    observed = data.get("observed", {})
    if not isinstance(observed, dict):
        raise core.InputError("'observed' must be an object")
    items, breaches, warnings = [], [], []
    for key, limit in data["limits"].items():
        if not key.startswith("max_"):
            raise core.InputError(f"limit {key!r} must start with max_")
        metric = key[4:]
        lim = core_num(limit, key)
        if metric not in observed:
            warnings.append(f"no observed value for {metric}")
            items.append({"metric": metric, "limit": lim, "observed": "", "status": "not observed"})
            continue
        val = core_num(observed[metric], metric)
        status = "STOP" if val > lim else "ok"
        if status == "STOP":
            breaches.append(metric)
        items.append({"metric": metric, "limit": lim, "observed": val, "status": status})
    ctx.warnings.extend(warnings)
    return {"summary": {"verdict": "STOP" if breaches else "CONTINUE", "breaches": len(breaches)},
            "items": items, "note": "Stop before damage or waste grows; resume only after a person reviews the breach."}


def core_num(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise core.InputError(f"{label} must be a finite number")
    return float(value)


_ESCALATION_ROLES = {"identity_legal": "legal or compliance owner", "financial": "budget owner",
                     "privacy": "data protection owner", "physical": "operations owner",
                     "credential": "security owner", "privileged": "security owner"}
_AUTHORITY = re.compile(r"(?i)(unclear who|not sure who|no owner|who approves|on behalf of|authority)")


@handler("V:escalate")
def v_escalate(ctx):
    actions = load_actions(ctx)
    items = []
    for a in actions:
        c = classify(a, ctx.policy)
        reasons = [cat for cat in c["categories"] if cat in _ESCALATION_ROLES]
        if _AUTHORITY.search(a["description"]):
            reasons.append("ambiguous authority")
        if reasons:
            roles = sorted({_ESCALATION_ROLES.get(r, "requesting person") for r in reasons if r != "ambiguous authority"}
                           | ({"requesting person"} if "ambiguous authority" in reasons else set()))
            items.append({"id": a["id"], "reasons": ", ".join(reasons), "route_to": ", ".join(roles),
                          "action": core.excerpt(a["description"], 120)})
    return {"summary": {"actions": len(actions), "escalate": len(items)}, "items": items,
            "note": "Escalated items must be decided by the named person; the tool does not decide them."}


@handler("V:audit")
def v_audit(ctx):
    data = ctx.read_json()
    if not isinstance(data, dict):
        raise core.InputError("audit input must be an object with 'allowed_actions' and 'allowed_targets'")
    allowed_actions = set(data.get("allowed_actions", []))
    prefixes = list(data.get("allowed_targets", []))
    performed = list(data.get("performed", []))
    ledger_name = ctx.opt("ledger")
    if ledger_name:
        records, _ = core.ledger_read(_ledger_path(ctx, ledger_name), ctx.policy)
        performed += [r.get("data", {}) for r in records]
    items = []
    for n, p in enumerate(performed, start=1):
        action = str(p.get("action", ""))
        target = str(p.get("target", ""))
        problems = []
        if allowed_actions and action not in allowed_actions:
            problems.append("action not allowed")
        if prefixes and not any(target.startswith(x) for x in prefixes):
            problems.append("target outside allowed scope")
        if problems:
            items.append({"record": n, "action": action, "target": target, "violation": "; ".join(problems)})
    return {"summary": {"performed": len(performed), "violations": len(items),
                        "verdict": "within boundary" if not items else "boundary violated"}, "items": items}


_BROAD_SCOPES = {"*", "all", "/", "~", "admin", "root"}
_BROAD_PERMS = {"admin", "shell", "delete", "root"}


@handler("V:privilege")
def v_privilege(ctx):
    data = ctx.read_json()
    if not isinstance(data, dict) or not isinstance(data.get("requested"), list):
        raise core.InputError("privilege input needs 'requested' and 'needed' lists of {permission, scope}")
    needed = data.get("needed", [])
    items = []
    for r in data["requested"]:
        perm = str(r.get("permission", ""))
        scope = str(r.get("scope", ""))
        covered = False
        for n in needed:
            n_scope = str(n.get("scope", ""))
            if str(n.get("permission", "")) == perm and n_scope and (scope == n_scope or scope.startswith(n_scope)):
                covered = True
        flags = []
        if scope in _BROAD_SCOPES or scope.strip() == "":
            flags.append("broad scope")
        if perm in _BROAD_PERMS:
            flags.append("high-impact permission")
        if not covered:
            flags.append("not needed for the task")
        if flags:
            items.append({"permission": perm, "scope": scope or "(none)", "flags": "; ".join(flags)})
    return {"summary": {"requested": len(data["requested"]), "excess_or_broad": len(items),
                        "verdict": "minimal" if not items else "reduce before granting"}, "items": items}


@handler("V:rollback")
def v_rollback(ctx):
    actions = load_actions(ctx)
    rows, gaps = [], []
    for a in actions:
        if not a["reversible"] and not a["rollback"]:
            gaps.append(a["id"])
            status = "GAP: irreversible with no rollback"
        elif not a["rollback"]:
            status = "reversible; no explicit rollback written"
        else:
            status = "covered"
        rows.append({"id": a["id"], "reversible": "yes" if a["reversible"] else "no",
                     "rollback": core.excerpt(a["rollback"] or "(none)", 160), "status": status})
    lines = ["# Rollback plan", "", f"- Generated (UTC): {core.now_iso()}", f"- Gaps: {len(gaps)}",
             "- Executing a rollback is a separate, approved action. This document does not run anything.", "",
             "| ID | Reversible | Rollback | Status |", "| --- | --- | --- | --- |"]
    lines += [f"| {r['id']} | {r['reversible']} | {str(r['rollback']).replace('|', '/')} | {r['status']} |" for r in rows]
    path = ctx.write_artifact(f"{ctx.slug}.rollback.md", "\n".join(lines) + "\n")
    return {"summary": {"actions": len(rows), "gaps": len(gaps), "artifact": path,
                        "verdict": "ready" if not gaps else "not ready"}, "items": rows}
