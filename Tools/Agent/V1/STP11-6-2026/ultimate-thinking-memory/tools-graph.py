"""Dependency and graph handlers.

Families: D (dependency planning: ordering, critical path, ranking, next step, deadlines, WIP)
and N (knowledge and system graphs: audit, cycles, impact, leverage, tree, file index, export).
Loaded by tools-engine.py; do not run directly.
"""
import importlib.util
import json
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
HANDLERS = {}


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


def _num(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise core.InputError(f"{label} must be a number, got {value!r}")
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise core.InputError(f"{label} must be finite")
    if minimum is not None and value < minimum:
        raise core.InputError(f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise core.InputError(f"{label} must be <= {maximum}")
    return value


# ====================================================================== D
DONE = {"done", "complete", "completed"}
BLOCKED = {"blocked"}
DOING = {"doing", "in-progress", "in_progress", "active"}


def load_tasks(ctx):
    data = ctx.read_json()
    if isinstance(data, dict):
        data = data.get("tasks")
    if not isinstance(data, list) or not data:
        raise core.InputError("input must be a non-empty list of tasks, or an object with a 'tasks' list")
    if len(data) > ctx.policy["limits"]["max_items"]:
        raise core.InputError(f"too many tasks ({len(data)}); the limit is {ctx.policy['limits']['max_items']}")
    tasks = {}
    for n, raw in enumerate(data, start=1):
        if not isinstance(raw, dict) or "id" not in raw:
            raise core.InputError(f"task {n} must be an object with an 'id'")
        tid = str(raw["id"])
        if tid in tasks:
            raise core.InputError(f"duplicate task id {tid!r}")
        deps = raw.get("deps", [])
        if not isinstance(deps, list):
            raise core.InputError(f"deps of {tid} must be a list")
        tasks[tid] = {
            "id": tid,
            "deps": [str(d) for d in deps],
            "duration": _num(raw.get("duration", 1), f"duration of {tid}", 0),
            "value": _num(raw.get("value", 1), f"value of {tid}", 0),
            "effort": _num(raw.get("effort", 1), f"effort of {tid}", 0.000001),
            "risk": _num(raw.get("risk", 0), f"risk of {tid}", 0, 10),
            "status": str(raw.get("status", "todo")).strip().lower(),
            "deadline": None if raw.get("deadline") is None else _num(raw["deadline"], f"deadline of {tid}", 0),
        }
    for t in tasks.values():
        missing = [d for d in t["deps"] if d not in tasks]
        if missing:
            raise core.InputError(f"task {t['id']} depends on unknown task(s): {', '.join(missing)}")
    return tasks


def topo_order(tasks):
    indeg = {t: len(v["deps"]) for t, v in tasks.items()}
    succ = defaultdict(list)
    for t, v in tasks.items():
        for d in v["deps"]:
            succ[d].append(t)
    ready = sorted([t for t, n in indeg.items() if n == 0])
    queue = deque(ready)
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for nxt in sorted(succ[node]):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(tasks):
        cyclic = sorted(t for t in tasks if t not in order)
        raise core.InputError(f"dependency cycle detected among: {', '.join(cyclic)}; remove or reverse one dependency")
    return order


def schedule(tasks, order):
    es, ef = {}, {}
    for t in order:
        es[t] = max([ef[d] for d in tasks[t]["deps"]] or [0.0])
        ef[t] = es[t] + tasks[t]["duration"]
    makespan = max(ef.values()) if ef else 0.0
    succ = defaultdict(list)
    for t, v in tasks.items():
        for d in v["deps"]:
            succ[d].append(t)
    lf, ls = {}, {}
    for t in reversed(order):
        lf[t] = min([ls[s] for s in succ[t]] or [makespan])
        ls[t] = lf[t] - tasks[t]["duration"]
    slack = {t: ls[t] - es[t] for t in order}
    return es, ef, ls, lf, slack, makespan


@handler("D:topo")
def d_topo(ctx):
    tasks = load_tasks(ctx)
    order = topo_order(tasks)
    level = {}
    for t in order:
        level[t] = 1 + max([level[d] for d in tasks[t]["deps"]] or [-1])
    items = [{"order": i, "id": t, "level": level[t], "parallel_group": level[t] + 1,
              "deps": ", ".join(tasks[t]["deps"]), "status": tasks[t]["status"]}
             for i, t in enumerate(order, start=1)]
    groups = defaultdict(list)
    for t in order:
        groups[level[t]].append(t)
    return {"summary": {"tasks": len(tasks), "sequential_steps": len(groups),
                        "max_parallel": max(len(v) for v in groups.values())},
            "items": items,
            "parallel_groups": {str(k + 1): v for k, v in sorted(groups.items())},
            "note": "Tasks in the same parallel group share no dependency and may run concurrently."}


@handler("D:critical")
def d_critical(ctx):
    tasks = load_tasks(ctx)
    order = topo_order(tasks)
    es, ef, ls, lf, slack, makespan = schedule(tasks, order)
    tol = 1e-9
    critical = [t for t in order if abs(slack[t]) <= tol]
    # walk back from a critical finisher with EF == makespan
    finishers = [t for t in critical if abs(ef[t] - makespan) <= tol]
    path = []
    if finishers:
        cur = sorted(finishers)[0]
        path.append(cur)
        while True:
            preds = [d for d in tasks[cur]["deps"] if d in critical and abs(ef[d] - es[cur]) <= tol]
            if not preds:
                break
            cur = sorted(preds)[0]
            path.append(cur)
        path.reverse()
    bottleneck = max(critical, key=lambda t: tasks[t]["duration"]) if critical else ""
    items = [{"id": t, "duration": tasks[t]["duration"], "earliest_start": es[t], "earliest_finish": ef[t],
              "latest_start": ls[t], "latest_finish": lf[t], "slack": round(slack[t], 6),
              "critical": "yes" if t in critical else ""} for t in order]
    return {"summary": {"tasks": len(tasks), "makespan": makespan, "critical_path": " -> ".join(path),
                        "bottleneck": bottleneck, "critical_tasks": len(critical)},
            "items": items}


@handler("D:rank")
def d_rank(ctx):
    tasks = load_tasks(ctx)
    items = []
    for t in tasks.values():
        score = (t["value"] / t["effort"]) * (1.0 - t["risk"] / 10.0)
        items.append({"id": t["id"], "score": round(max(score, 0.0), 3), "value": t["value"],
                      "effort": t["effort"], "risk": t["risk"], "status": t["status"]})
    items.sort(key=lambda x: (-x["score"], x["id"]))
    for i, it in enumerate(items, start=1):
        it["rank"] = i
    return {"summary": {"tasks": len(items), "top": items[0]["id"] if items else ""},
            "items": items, "formula": "score = value / effort * (1 - risk / 10)"}


@handler("D:next")
def d_next(ctx):
    tasks = load_tasks(ctx)
    max_risk = ctx.policy["defaults"]["max_safe_risk"]
    done = {t for t, v in tasks.items() if v["status"] in DONE}
    items = []
    for t in tasks.values():
        if t["status"] in DONE:
            continue
        deps_done = all(d in done for d in t["deps"])
        blocked = t["status"] in BLOCKED
        ready = deps_done and not blocked
        score = (t["value"] / t["effort"]) * (1.0 - t["risk"] / 10.0)
        items.append({"id": t["id"], "ready": "yes" if ready else "no",
                      "reason": "blocked status" if blocked else ("waiting on: " + ", ".join(d for d in t["deps"] if d not in done) if not deps_done else ""),
                      "score": round(max(score, 0.0), 3), "risk": t["risk"],
                      "safe": "yes" if t["risk"] <= max_risk else "no"})
    ready_safe = sorted([i for i in items if i["ready"] == "yes" and i["safe"] == "yes"],
                        key=lambda x: (-x["score"], x["id"]))
    recommended = ready_safe[0]["id"] if ready_safe else ""
    ready_any = [i for i in items if i["ready"] == "yes"]
    if not recommended and ready_any:
        ctx.warnings.append("ready tasks exist but none is within the safe-risk threshold; a person should approve the riskiest one")
    if not ready_any:
        ctx.warnings.append("no task is ready; resolve blockers or dependencies first")
    items.sort(key=lambda x: (x["ready"] != "yes", -x["score"], x["id"]))
    return {"summary": {"recommended_next": recommended or "(none)", "ready": len(ready_any),
                        "max_safe_risk": max_risk},
            "items": items}


@handler("D:backward")
def d_backward(ctx):
    tasks = load_tasks(ctx)
    order = topo_order(tasks)
    es, ef, ls_fwd, lf_fwd, slack_fwd, makespan = schedule(tasks, order)
    deadline = ctx.opt_float("deadline", None, 0)
    if deadline is None:
        deadline = makespan  # no deadline given: check against the earliest possible finish
    succ = defaultdict(list)
    for t, v in tasks.items():
        for d in v["deps"]:
            succ[d].append(t)
    lf, ls = {}, {}
    for t in reversed(order):
        bounds = [ls[s] for s in succ[t]]
        if tasks[t]["deadline"] is not None:
            bounds.append(tasks[t]["deadline"])  # a task's own deadline also caps its finish
        lf[t] = min(bounds or [deadline])
        ls[t] = lf[t] - tasks[t]["duration"]
    items = []
    late = 0
    for t in order:
        slack = ls[t] - es[t]
        if slack < -1e-9:
            late += 1
        items.append({"id": t, "latest_start": ls[t], "earliest_start": es[t], "slack": round(slack, 6),
                      "status": "late" if slack < -1e-9 else "on track"})
    return {"summary": {"deadline": deadline, "makespan": makespan, "late_tasks": late,
                        "feasible": late == 0}, "items": items}


@handler("D:wip")
def d_wip(ctx):
    tasks = load_tasks(ctx)
    limit = ctx.opt_int("limit", ctx.policy["defaults"]["wip_limit"], 1)
    doing = [t for t in tasks.values() if t["status"] in DOING]
    items = [{"id": t["id"], "status": t["status"], "over_limit": "yes" if len(doing) > limit else ""} for t in doing]
    if len(doing) > limit:
        ctx.warnings.append(f"{len(doing)} tasks in progress exceeds the limit of {limit}; finish or pause before starting more")
    return {"summary": {"in_progress": len(doing), "limit": limit, "within_limit": len(doing) <= limit}, "items": items}


# ====================================================================== N
def load_graph(ctx):
    if ctx.input_path is not None and ctx.input_path.suffix.lower() == ".json":
        data = ctx.read_json()
        if not isinstance(data, dict) or not isinstance(data.get("edges", []), list):
            raise core.InputError("graph JSON must be an object with an 'edges' list (and optional 'nodes' list)")
        declared = None
        if "nodes" in data:
            declared = set()
            for n in data["nodes"]:
                declared.add(str(n["id"] if isinstance(n, dict) else n))
        edges = []
        for raw in data.get("edges", []):
            if not isinstance(raw, dict) or "from" not in raw or "to" not in raw:
                raise core.InputError("every edge needs 'from' and 'to'")
            edges.append({"from": str(raw["from"]), "to": str(raw["to"]), "relation": str(raw.get("relation", ""))})
    else:
        text = ctx.read_text()
        declared = None
        edges = []
        for lineno, raw in enumerate(core.split_lines(text), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) != 3:
                    raise core.InputError(f"line {lineno}: use 'A | relation | B'")
                edges.append({"from": parts[0], "to": parts[2], "relation": parts[1]})
            elif "->" in line:
                a, b = [p.strip() for p in line.split("->", 1)]
                edges.append({"from": a, "to": b, "relation": ""})
            else:
                raise core.InputError(f"line {lineno}: expected 'A | relation | B' or 'A -> B'")
    if len(edges) > ctx.policy["limits"]["max_items"]:
        raise core.InputError(f"too many edges ({len(edges)}); the limit is {ctx.policy['limits']['max_items']}")
    nodes = set(declared or set())
    for e in edges:
        nodes.add(e["from"])
        nodes.add(e["to"])
    return nodes, edges, declared


def _adjacency(edges, relation=None):
    out, inc = defaultdict(list), defaultdict(list)
    for e in edges:
        if relation is not None and e["relation"] != relation:
            continue
        out[e["from"]].append(e["to"])
        inc[e["to"]].append(e["from"])
    return out, inc


@handler("N:audit")
def n_audit(ctx):
    nodes, edges, declared = load_graph(ctx)
    touched = set()
    for e in edges:
        touched.add(e["from"])
        touched.add(e["to"])
    items = []
    for n in sorted(nodes - touched):
        items.append({"rule": "orphan node", "subject": n, "note": "no edges; connect it or remove it"})
    for e in edges:
        if e["from"] == e["to"]:
            items.append({"rule": "self-loop", "subject": e["from"], "note": "node refers to itself"})
        if not e["relation"]:
            items.append({"rule": "unlabeled edge", "subject": f"{e['from']} -> {e['to']}", "note": "add a relation label"})
        if declared is not None and (e["from"] not in declared or e["to"] not in declared):
            items.append({"rule": "dangling edge", "subject": f"{e['from']} -> {e['to']}", "note": "endpoint not in declared nodes"})
    groups = defaultdict(set)
    for n in nodes:
        groups[re.sub(r"[^a-z0-9]", "", n.lower())].add(n)
    for key, names in groups.items():
        if len(names) > 1:
            items.append({"rule": "duplicate name", "subject": ", ".join(sorted(names)), "note": "names differ only in case or punctuation; consolidate"})
    counts = defaultdict(int)
    for it in items:
        counts[it["rule"]] += 1
    summary = {"nodes": len(nodes), "edges": len(edges), "findings": len(items), **dict(counts)}
    return {"summary": summary, "items": items}


def _scc(nodes, out):
    index, low, on_stack, stack, result = {}, {}, set(), [], []
    counter = [0]
    for start in sorted(nodes):
        if start in index:
            continue
        work = [(start, iter(sorted(out.get(start, []))))]
        index[start] = low[start] = counter[0]
        counter[0] += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(out.get(nxt, [])))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    comp.append(member)
                    if member == node:
                        break
                result.append(sorted(comp))
    return result


@handler("N:cycles")
def n_cycles(ctx):
    nodes, edges, _ = load_graph(ctx)
    out, _ = _adjacency(edges)
    loops = []
    for comp in _scc(nodes, out):
        if len(comp) > 1 or (comp and comp[0] in out.get(comp[0], [])):
            loops.append({"loop": " <-> ".join(comp), "size": len(comp)})
    summary = {"loops": len(loops), "acyclic": not loops}
    note = ("A causal or dependency diagram should be acyclic; a loop here means a dependency is circular."
            if not loops else "Each loop is a feedback cycle. Label it reinforcing or balancing by reading the relations.")
    return {"summary": summary, "items": loops, "note": note}


@handler("N:impact")
def n_impact(ctx):
    nodes, edges, _ = load_graph(ctx)
    changed_raw = ctx.opt("changed")
    if not changed_raw:
        raise core.InputError("option changed=<node1,node2> is required")
    changed = [c.strip() for c in changed_raw.split(",") if c.strip()]
    unknown = [c for c in changed if c not in nodes]
    if unknown:
        raise core.InputError(f"changed node(s) not in graph: {', '.join(unknown)}")
    direction = ctx.opt("direction", "downstream")
    if direction not in ("downstream", "upstream"):
        raise core.InputError("direction must be downstream or upstream")
    out, inc = _adjacency(edges)
    adj = out if direction == "downstream" else inc
    seen = {c: 0 for c in changed}
    frontier = deque((c, 0) for c in changed)
    via = {}
    while frontier:
        node, depth = frontier.popleft()
        ctx.tick()
        for nxt in adj.get(node, []):
            if nxt not in seen:
                seen[nxt] = depth + 1
                via[nxt] = node
                frontier.append((nxt, depth + 1))
    items = [{"node": n, "depth": d, "reached_from": via.get(n, "(changed)")}
             for n, d in sorted(seen.items(), key=lambda x: (x[1], x[0]))]
    affected = [i for i in items if i["depth"] > 0]
    return {"summary": {"changed": len(changed), "affected": len(affected), "direction": direction},
            "items": items}


@handler("N:leverage")
def n_leverage(ctx):
    nodes, edges, _ = load_graph(ctx)
    out, inc = _adjacency(edges)
    top = ctx.opt_int("top", 10, 1, 1000)
    rows = [{"node": n, "in": len(inc.get(n, [])), "out": len(out.get(n, [])),
             "total": len(inc.get(n, [])) + len(out.get(n, []))} for n in nodes]
    rows.sort(key=lambda r: (-r["total"], -r["out"], r["node"]))
    return {"summary": {"nodes": len(nodes), "max_total_degree": rows[0]["total"] if rows else 0},
            "items": rows[:top],
            "note": "High total degree means coupling; high out-degree means leverage over downstream items."}


@handler("N:tree")
def n_tree(ctx):
    nodes, edges, _ = load_graph(ctx)
    relation = ctx.opt("relation")
    out, inc = _adjacency(edges, relation)
    roots = sorted(n for n in nodes if not inc.get(n))
    items, printed, skipped = [], set(), 0

    def walk(node, depth, path):
        nonlocal skipped
        ctx.tick()
        if node in path:
            skipped += 1
            return
        printed.add(node)
        items.append({"depth": depth, "node": "  " * depth + node})
        for child in sorted(out.get(node, [])):
            walk(child, depth + 1, path | {node})

    for r in roots:
        walk(r, 0, frozenset())
    unreached = sorted(nodes - printed)
    return {"summary": {"roots": len(roots), "nodes_printed": len(printed), "cycles_skipped": skipped,
                        "relation": relation or "(all)"}, "items": items,
            "unreached": unreached}


@handler("N:index")
def n_index(ctx):
    root = ctx.input_path if ctx.input_path.is_dir() else ctx.input_path.parent
    files = core.walk_files(root, ctx.policy, extensions=core.TEXT_EXTENSIONS,
                            skip_dirs={ctx.state_dir.name, ctx.output_dir.name})
    texts = {}
    for f in files:
        if f.stat().st_size <= ctx.policy["limits"]["max_scan_file_bytes"]:
            try:
                texts[str(f.relative_to(root))] = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
        ctx.tick()
    names = {name: Path(name).name for name in texts}
    items = []
    for src, body in texts.items():
        for dst, base in names.items():
            if dst != src and base and base in body:
                items.append({"from": src, "relation": "references", "to": dst})
    summary = {"files_indexed": len(texts), "references": len(items),
               "isolated_files": len(texts) - len({i["from"] for i in items} | {i["to"] for i in items})}
    return {"summary": summary, "items": items}


def _mermaid_id(index):
    return f"n{index}"


@handler("N:export")
def n_export(ctx):
    nodes, edges, _ = load_graph(ctx)
    ordered = sorted(nodes)
    ids = {n: _mermaid_id(i) for i, n in enumerate(ordered)}
    lines = ["graph LR"]
    for n in ordered:
        label = n.replace('"', "'")
        lines.append(f'    {ids[n]}["{label}"]')
    for e in edges:
        label = e["relation"].replace('"', "'") if e["relation"] else ""
        arrow = f"-->|{label}|" if label else "-->"
        lines.append(f"    {ids[e['from']]} {arrow} {ids[e['to']]}")
    md = [f"# Graph export: {ctx.capability['name']}", "", f"- Nodes: {len(nodes)}", f"- Edges: {len(edges)}", "",
          "## Diagram (Mermaid)", "", "```mermaid", *lines, "```", "", "## Edges", "",
          "| From | Relation | To |", "| --- | --- | --- |"]
    md += [f"| {e['from']} | {e['relation']} | {e['to']} |" for e in edges]
    md_path = ctx.write_artifact(f"{ctx.slug}.graph.md", "\n".join(md) + "\n")
    json_path = ctx.write_artifact(f"{ctx.slug}.graph.json",
                                   json.dumps({"nodes": ordered, "edges": edges}, indent=2, ensure_ascii=False) + "\n")
    return {"summary": {"nodes": len(nodes), "edges": len(edges), "markdown": md_path, "json": json_path},
            "items": [], "note": "The Mermaid block renders in most Markdown viewers."}
