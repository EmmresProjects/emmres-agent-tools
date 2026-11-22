#!/usr/bin/env python3
"""tools-oaa-state.py -- durable memory, approvals, audit and dead-letter operations.

Loaded by `tools-oaa.py`. Registers:

  memory.put       store a classified durable memory (poisoning screen applied)
  memory.get       read one memory record
  memory.search    term-overlap search across active memories
  memory.list      list memories by class/subject
  memory.resolve   resolve competing memories with a documented scoring rule
  memory.compact   merge near-duplicate memories while keeping reversible history
  memory.delete    soft or hard delete (hard delete requires approval)
  memory.export    export approved memory slices with provenance
  memory.purge     expire memories whose retention window elapsed
  approval.request / approval.decide / approval.list
  dlq.list / dlq.resolve
  audit.verify / audit.timeline
  state.get / state.set / state.list / state.report
"""
from __future__ import annotations

import json
import os
import re

import importlib.util as _ilu
import sys as _sys

# Load the shared kernel exactly once per process so every module sees the same
# exception types, configuration objects and audit chain instance.
_KERNEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "tools-oaa-kernel.py")
if "oaa_kernel" in _sys.modules:
    K = _sys.modules["oaa_kernel"]
else:
    _spec = _ilu.spec_from_file_location("oaa_kernel", _KERNEL_PATH)
    K = _ilu.module_from_spec(_spec)
    _sys.modules["oaa_kernel"] = K
    _spec.loader.exec_module(K)

POISON_PATTERNS = [
    (r"(?i)ignore (all |any )?(previous|prior) (instructions|rules)", "override"),
    (r"(?i)\byou (must|shall) always\b", "imperative_rule"),
    (r"(?i)\bnever (tell|inform|reveal)\b", "concealment"),
    (r"(?i)\bapproval (is|was) (not required|waived|granted)\b", "policy_bypass"),
    (r"(?i)\b(exfiltrate|send all secrets?)\b", "exfiltration"),
    (r"(?i)\bdo not (log|audit|record)\b", "audit_evasion"),
]

CLS_CONFLICT_GROUPS = ("preference", "project", "decision", "procedure", "lesson")


def _terms(text):
    return {t for t in re.findall(r"[a-z0-9][a-z0-9_\-']+", str(text).lower())
            if len(t) > 2 and t not in K._STOP}


def _similarity(a, b):
    ta, tb = _terms(a), _terms(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def detect_poisoning(content):
    hits = []
    for pattern, label in POISON_PATTERNS:
        match = re.search(pattern, str(content))
        if match:
            hits.append({"signal": label, "match": match.group(0)[:120]})
    return hits


def _register(registry):
    reg = registry

    # ---------------------------------------------------------------- memory.put
    @reg.op("memory.put",
            "Store a classified durable memory with provenance and retention.",
            params=["cls", "subject", "content", "confidence", "provenance",
                    "ttl_days", "ttl_seconds", "allow_flagged"],
            group="memory", mutating=True,
            examples=['{"cls": "lesson", "subject": "indexing", '
                      '"content": "Rebuild indexes after adding a source."}'])
    def memory_put(ctx, params):
        missing = [k for k in ("cls", "subject", "content") if not params.get(k)]
        if missing:
            raise K.InputError("memory.put requires cls, subject and content",
                               missing=missing)
        flags = detect_poisoning(params["content"])
        if flags and not params.get("allow_flagged"):
            ctx.emit("memory.poison_blocked", action="memory.put",
                     signals=[f["signal"] for f in flags])
            raise K.PolicyError(
                "content looks instruction-like or policy-bypassing and was not stored",
                signals=flags,
                remediation="review the content; re-run with allow_flagged=true to "
                            "store it for analysis only")
        ttl_days = params.get("ttl_days")
        if ttl_days is None and params.get("ttl_seconds") is not None:
            ttl_days = float(params["ttl_seconds"]) / 86400.0
        record = ctx.store.memory_put(
            cls=params["cls"], subject=params["subject"],
            content=params["content"],
            confidence=float(params.get("confidence", 0.8)),
            provenance=params.get("provenance") or {"created_by": "local-operator"},
            ttl_days=ttl_days)
        ctx.emit("memory.put", action="memory.put", target=record["id"],
                 cls=record["cls"], subject=record["subject"])
        out = dict(record)
        out["poisoning_signals"] = flags
        return out

    # ---------------------------------------------------------------- memory.get
    @reg.op("memory.get", "Read one memory record by id.",
            params=["id"], group="memory",
            examples=['{"id": "mem-0123456789ab"}'])
    def memory_get(ctx, params):
        if not params.get("id"):
            raise K.InputError("memory.get requires params.id")
        return ctx.store.memory_get(params["id"])

    # ---------------------------------------------------------------- memory.list
    @reg.op("memory.list", "List memories filtered by class or subject.",
            params=["cls", "subject", "include_expired"], group="memory",
            examples=['{"cls": "lesson"}'])
    def memory_list(ctx, params):
        rows = ctx.store.memory_list(cls=params.get("cls"),
                                     subject=params.get("subject"),
                                     include_expired=bool(
                                         params.get("include_expired")))
        return {"count": len(rows), "memories": rows}

    # ---------------------------------------------------------------- memory.search
    @reg.op("memory.search", "Search active memories by term overlap.",
            params=["query", "cls", "subject", "top_k"], group="memory",
            examples=['{"query": "index rebuild"}'])
    def memory_search(ctx, params):
        query = params.get("query")
        if not isinstance(query, str) or not query.strip():
            raise K.InputError("memory.search requires params.query")
        q_terms = _terms(query)
        if not q_terms:
            raise K.InputError("query contains no usable terms", query=query)
        rows = ctx.store.memory_list(cls=params.get("cls"),
                                     subject=params.get("subject"))
        hits = []
        for row in rows:
            r_terms = _terms(row["content"]) | _terms(row["subject"])
            if not r_terms:
                continue
            score = len(q_terms & r_terms) / len(q_terms)
            if score > 0:
                hits.append({"score": round(score, 4), "memory": row})
        hits.sort(key=lambda h: -h["score"])
        top_k = int(params.get("top_k", 5))
        return {"query": query, "terms": sorted(q_terms), "hits": hits[:top_k],
                "searched": len(rows)}

    # ---------------------------------------------------------------- memory.resolve
    @reg.op("memory.resolve",
            "Resolve competing memories using confidence, recency, specificity and "
            "authority.",
            params=["subject", "cls", "apply"], group="memory", mutating=False,
            examples=['{"subject": "deployment-window"}'])
    def memory_resolve(ctx, params):
        rows = ctx.store.memory_list(cls=params.get("cls"),
                                     subject=params.get("subject"))
        if len(rows) < 2:
            return {"conflicts": [], "checked": len(rows),
                    "note": "fewer than two active memories; nothing to resolve"}
        groups = {}
        for row in rows:
            groups.setdefault((row["cls"], row["subject"]), []).append(row)
        conflicts = []
        for (cls, subject), items in sorted(groups.items()):
            if len(items) < 2:
                continue
            scored = []
            newest = max(i["updated_at"] for i in items)
            for item in items:
                recency = 1.0 if item["updated_at"] == newest else 0.5
                authority = float(item["provenance"].get("authority", 0.5))
                specificity = min(1.0, len(_terms(item["content"])) / 25.0)
                score = (0.4 * float(item["confidence"]) + 0.3 * recency
                         + 0.2 * specificity + 0.1 * authority)
                scored.append((round(score, 4), item))
            scored.sort(key=lambda pair: (-pair[0], pair[1]["updated_at"]))
            winner = scored[0]
            conflicts.append({
                "cls": cls,
                "subject": subject,
                "winner": {"id": winner[1]["id"], "score": winner[0],
                           "content": winner[1]["content"]},
                "superseded": [{"id": item["id"], "score": score,
                                "content": item["content"]}
                               for score, item in scored[1:]],
                "rule": "0.4*confidence + 0.3*recency + 0.2*specificity "
                        "+ 0.1*authority",
            })
            if params.get("apply"):
                for score, item in scored[1:]:
                    ctx.store.memory_delete(item["id"], hard=False)
        if params.get("apply") and conflicts:
            ctx.emit("memory.resolve", action="memory.resolve",
                     conflicts=len(conflicts), applied=True)
        return {"conflicts": conflicts, "checked": len(rows),
                "applied": bool(params.get("apply"))}

    # ---------------------------------------------------------------- memory.compact
    @reg.op("memory.compact",
            "Merge near-duplicate memories, keeping provenance and reversible history.",
            params=["cls", "subject", "threshold"], group="memory", mutating=True,
            examples=['{"cls": "lesson", "threshold": 0.7}'])
    def memory_compact(ctx, params):
        threshold = float(params.get("threshold", 0.7))
        rows = ctx.store.memory_list(cls=params.get("cls"),
                                     subject=params.get("subject"))
        groups = {}
        for row in rows:
            groups.setdefault((row["cls"], row["subject"]), []).append(row)
        merged = []
        for (cls, subject), items in sorted(groups.items()):
            used = set()
            for i, first in enumerate(items):
                if first["id"] in used:
                    continue
                cluster = [first]
                used.add(first["id"])
                for other in items[i + 1:]:
                    if other["id"] in used:
                        continue
                    if _similarity(first["content"], other["content"]) >= threshold:
                        cluster.append(other)
                        used.add(other["id"])
                if len(cluster) < 2:
                    continue
                content = max(cluster, key=lambda c: float(c["confidence"]))["content"]
                confidence = max(float(c["confidence"]) for c in cluster)
                provenance = {
                    "compacted_from": [c["id"] for c in cluster],
                    "sources": sorted({str(c["provenance"].get("source", "unknown"))
                                       for c in cluster}),
                    "compacted_at": K.now_iso(),
                    "created_by": "memory.compact",
                }
                new = ctx.store.memory_put(cls=cls, subject=subject, content=content,
                                           confidence=confidence,
                                           provenance=provenance)
                for c in cluster:
                    ctx.store.memory_delete(c["id"], hard=False)
                merged.append({"new_id": new["id"], "merged_ids":
                               [c["id"] for c in cluster], "cls": cls,
                               "subject": subject})
        ctx.emit("memory.compact", action="memory.compact", merged=len(merged))
        return {"merged": merged, "clusters": len(merged),
                "checked": len(rows), "threshold": threshold}

    # ---------------------------------------------------------------- memory.delete
    @reg.op("memory.delete",
            "Delete a memory. Hard deletion requires approval.",
            params=["id", "hard"], group="memory", mutating=True,
            approval_action="memory.delete.hard",
            examples=['{"id": "mem-0123456789ab", "hard": false}'])
    def memory_delete(ctx, params):
        if not params.get("id"):
            raise K.InputError("memory.delete requires params.id")
        hard = bool(params.get("hard"))
        if hard:
            ctx.guard("fs.delete", approval_token=params.get("approval_token"))
        result = ctx.store.memory_delete(params["id"], hard=hard)
        ctx.emit("memory.delete", action="memory.delete", target=params["id"],
                 hard=hard)
        return result

    # ---------------------------------------------------------------- memory.export
    @reg.op("memory.export",
            "Export approved memory slices with provenance to a JSON file.",
            params=["output", "cls", "subject"], group="memory", mutating=True,
            examples=['{"output": "artifacts/memory-export.json", "cls": "lesson"}'])
    def memory_export(ctx, params):
        out = params.get("output") or params.get("out")
        if not out:
            raise K.InputError("memory.export requires params.output")
        path = ctx.workspace.scope.resolve(out)
        rows = ctx.store.memory_list(cls=params.get("cls"),
                                     subject=params.get("subject"),
                                     include_expired=False)
        payload = {"exported_at": K.now_iso(),
                   "correlation_id": ctx.correlation_id,
                   "count": len(rows),
                   "memories": rows}
        K.write_json(path, payload)
        ctx.emit("memory.export", action="memory.export", target=ctx.workspace.rel(path),
                 count=len(rows))
        return {"path": ctx.workspace.rel(path), "count": len(rows),
                "sha256": K.sha256_file(path)}

    # ---------------------------------------------------------------- memory.purge
    @reg.op("memory.purge",
            "Expire memories whose retention window has elapsed.",
            params=[], group="memory", mutating=True,
            examples=['{}'])
    def memory_purge(ctx, params):
        result = ctx.store.memory_purge_expired()
        ctx.emit("memory.purge", action="memory.purge", **result)
        return result

    # ---------------------------------------------------------------- approvals
    @reg.op("approval.request",
            "Open a human approval request with evidence and consequences.",
            params=["action", "subject", "reason", "evidence"], group="oversight",
            mutating=True,
            examples=['{"action": "artifact.publish", "subject": "report.json", '
                      '"reason": "publish to shared drive"}'])
    def approval_request(ctx, params):
        missing = [k for k in ("action", "subject", "reason") if not params.get(k)]
        if missing:
            raise K.InputError("approval.request requires action, subject and reason",
                               missing=missing)
        entry = ctx.store.approval_request(params["action"], params["subject"],
                                           params["reason"],
                                           params.get("evidence") or {})
        ctx.emit("approval.request", action=params["action"],
                 target=params["subject"], approval_id=entry["id"])
        return entry

    @reg.op("approval.decide",
            "Approve or deny a pending approval request.",
            params=["id", "decision", "decider"], group="oversight", mutating=True,
            examples=['{"id": "apv-0123456789ab", "decision": "approve"}'])
    def approval_decide(ctx, params):
        if not params.get("id"):
            raise K.InputError("approval.decide requires params.id")
        decision = str(params.get("decision", "")).lower()
        if decision not in ("approve", "deny", "approved", "denied"):
            raise K.InputError("decision must be approve or deny", value=decision)
        entry = ctx.store.approval_decide(
            params["id"], approve=decision.startswith("approv"),
            decider=params.get("decider", "local-operator"))
        ctx.emit("approval.decide", action=entry["action"], target=entry["id"],
                 decision=entry["status"])
        return entry

    @reg.op("approval.list", "List approval requests.", params=["status"],
            group="oversight", examples=['{"status": "pending"}'])
    def approval_list(ctx, params):
        rows = ctx.store.approval_list(status=params.get("status"))
        return {"count": len(rows), "approvals": rows}

    # ---------------------------------------------------------------- dlq
    @reg.op("dlq.list", "List dead-letter entries from failed workflow steps.",
            params=["include_resolved"], group="reliability",
            examples=['{}'])
    def dlq_list(ctx, params):
        rows = ctx.store.dlq_list(include_resolved=bool(
            params.get("include_resolved")))
        return {"count": len(rows), "entries": rows}

    @reg.op("dlq.resolve", "Mark a dead-letter entry as resolved.",
            params=["id"], group="reliability", mutating=True,
            examples=['{"id": "dlq-0123456789ab"}'])
    def dlq_resolve(ctx, params):
        if not params.get("id"):
            raise K.InputError("dlq.resolve requires params.id")
        return ctx.store.dlq_resolve(params["id"])

    # ---------------------------------------------------------------- audit
    @reg.op("audit.verify",
            "Verify the tamper-evident audit chain (strict mode raises on failure).",
            params=["strict"], group="observability", examples=['{"strict": true}'])
    def audit_verify(ctx, params):
        report = ctx.audit.verify()
        ctx.emit("audit.verified", action="audit.verify",
                 ok=report["ok"], problems=len(report["problems"]))
        if params.get("strict") and not report["ok"]:
            raise K.IntegrityError("audit chain integrity check failed",
                                   problems=report["problems"][:20],
                                   records=report["records"])
        return report

    @reg.op("audit.timeline",
            "Reconstruct an audit timeline, optionally filtered by correlation id.",
            params=["correlation_id", "limit"], group="observability",
            examples=['{"correlation_id": "cor-0123456789ab"}'])
    def audit_timeline(ctx, params):
        events = ctx.audit.timeline(correlation_id=params.get("correlation_id"),
                                    limit=int(params.get("limit", 200)))
        return {"count": len(events), "events": events}

    @reg.op("audit.append", "Append a structured audit event.",
            params=["event", "action", "target", "detail"], group="observability",
            mutating=True,
            examples=['{"event": "operator.note", "detail": {"note": "checked"}}'])
    def audit_append(ctx, params):
        if not params.get("event"):
            raise K.InputError("audit.append requires params.event")
        record = ctx.audit.append({
            "event": params["event"], "action": params.get("action", ""),
            "target": params.get("target", ""),
            "correlation_id": ctx.correlation_id,
            "detail": params.get("detail") or {},
        })
        return {"seq": record["seq"], "hash": record["hash"]}

    # ---------------------------------------------------------------- kv state
    @reg.op("state.set", "Write a value into durable key/value state.",
            params=["scope", "key", "value"], group="state", mutating=True,
            examples=['{"scope": "config", "key": "window", "value": "sat"}'])
    def state_set(ctx, params):
        for key in ("scope", "key"):
            if not params.get(key):
                raise K.InputError("state.set requires scope and key", missing=key)
        if "value" not in params:
            raise K.InputError("state.set requires params.value")
        return ctx.store.kv_set(params["scope"], params["key"], params["value"])

    @reg.op("state.get", "Read a value from durable key/value state.",
            params=["scope", "key"], group="state",
            examples=['{"scope": "config", "key": "window"}'])
    def state_get(ctx, params):
        for key in ("scope", "key"):
            if not params.get(key):
                raise K.InputError("state.get requires scope and key", missing=key)
        value = ctx.store.kv_get(params["scope"], params["key"])
        if value is None:
            raise K.NotFoundError("state key not found", scope=params["scope"],
                                  key=params["key"])
        return {"scope": params["scope"], "key": params["key"], "value": value}

    @reg.op("state.list", "List all keys in a state scope.", params=["scope"],
            group="state", examples=['{"scope": "config"}'])
    def state_list(ctx, params):
        if not params.get("scope"):
            raise K.InputError("state.list requires params.scope")
        rows = ctx.store.kv_list(params["scope"])
        return {"scope": params["scope"], "count": len(rows), "items": rows}

    @reg.op("state.report", "Report state store tables and schema version.",
            params=[], group="state", examples=['{}'])
    def state_report(ctx, params):
        report = ctx.store.describe()
        report["workspace"] = ctx.workspace.root
        return report


def register(registry):
    _register(registry)
    return registry
