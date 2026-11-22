#!/usr/bin/env python3
"""Self-test for the Ultimate Thinking Memory Skill Tool.

Copies this Skill Tool folder into a fresh temporary root (named ultimate-thinking-memory), then runs
the engine from that copy. All state and outputs go to a separate temporary work folder, so the
Skill Tool folder itself is never written to. Checks: package layout, known answers for each family,
approval gates, injection-safe expressions, ledger tamper detection, checkpoint round trips, timeouts,
cancellation, and usage errors.

Usage:  python3 tools-selftest.py            (exit 0 when every check passes)
        python3 tools-selftest.py --keep     (keep the temporary folders for inspection)
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFIXES = ("tools-", "knowledge-", "asset-")
SKILL_NAME = "ultimate-thinking-memory"


class Suite:
    def __init__(self, keep=False):
        self.keep = keep
        self.tmp = Path(tempfile.mkdtemp(prefix="utm-selftest-"))
        self.root = self.tmp / SKILL_NAME
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.results = []
        self.engine = self.root / "tools-engine.py"
        self.catalog = None

    # ------------------------------------------------------------ setup
    def install_copy(self):
        self.root.mkdir()
        copied = 0
        for p in sorted(HERE.iterdir()):
            if p.is_file() and p.name.startswith(PREFIXES):
                shutil.copy2(p, self.root / p.name)
                copied += 1
        self.catalog = json.loads((self.root / "asset-capability-catalog.json").read_text(encoding="utf-8"))
        return copied

    def cap_for(self, op):
        for c in self.catalog["capabilities"]:
            if c["default_handler"] == op:
                return c["id"], None
        fam = op.split(":", 1)[0]
        c = next(c for c in self.catalog["capabilities"] if c["family"] == fam)
        return c["id"], op

    # ------------------------------------------------------------ helpers
    def check(self, name, ok, detail=""):
        self.results.append((name, bool(ok), detail))

    def engine_call(self, *args, cwd=None):
        proc = subprocess.run([sys.executable, str(self.engine), *args], capture_output=True, text=True,
                              cwd=str(cwd or self.work), timeout=300)
        return proc.returncode, proc.stdout, proc.stderr

    def run_op(self, op, inp=None, opts=(), extra=(), state=None, out=None, as_of="2026-11-05"):
        cid, override = self.cap_for(op)
        args = ["run", cid, "--format", "json", "--as-of", as_of,
                "--output-dir", str(out or self.work / "out"), "--state-dir", str(state or self.work / "state")]
        if override:
            args += ["--op", override]
        if inp is not None:
            args += ["--input", str(inp)]
        for kv in opts:
            args += ["-o", kv]
        args += list(extra)
        code, stdout, stderr = self.engine_call(*args)
        env = None
        if stdout.strip():
            try:
                env = json.loads(stdout)
            except ValueError:
                env = None
        return code, env, stderr

    def summary(self, env):
        return (env or {}).get("result", {}).get("summary", {}) or {}

    def items(self, env):
        return (env or {}).get("result", {}).get("items", []) or []

    def near(self, a, b, tol=1e-4):
        return a is not None and abs(float(a) - float(b)) <= tol

    def write(self, name, content, folder=None):
        path = (folder or self.work) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (dict, list)):
            path.write_text(json.dumps(content, indent=2), encoding="utf-8")
        else:
            path.write_text(content, encoding="utf-8")
        return path

    # ------------------------------------------------------------ groups
    def test_package(self):
        code, out, err = self.engine_call("selfcheck", cwd=self.root)
        try:
            report = json.loads(out)
        except ValueError:
            report = {"status": "unparseable", "problems": [err[-200:]]}
        self.check("selfcheck reports ok", code == 0 and report.get("status") == "ok", str(report.get("problems")))
        code, out, _ = self.engine_call("capabilities", "--json", cwd=self.root)
        caps = json.loads(out) if code == 0 else []
        self.check("350 capabilities listed", len(caps) == 350, f"{len(caps)} listed")
        code, out, _ = self.engine_call("categories", cwd=self.root)
        self.check("35 categories listed", code == 0 and len([l for l in out.splitlines() if l.strip()]) == 35)
        code, out, _ = self.engine_call("help", cwd=self.root)
        self.check("help prints usage", code == 0 and "Usage" in out)
        code, out, _ = self.engine_call("describe", "C01.01", cwd=self.root)
        self.check("describe shows handler", code == 0 and "W:worksheet" in out)
        names = sorted(p.name for p in self.root.iterdir())
        self.check("only prefixed files, no subfolders",
                   all(n.startswith(PREFIXES) for n in names) and not any((self.root / n).is_dir() for n in names), str(names))
        self.check("exactly one knowledge file", sum(1 for n in names if n.startswith("knowledge-")) == 1)
        self.check("no SKILL.md", "SKILL.md" not in names)
        code, env, _ = self.run_op("S:check", inp=self.root, opts=["skill_tool=true"])
        self.check("S:check passes on this folder", env and self.summary(env).get("verdict") == "pass",
                   str(self.items(env)[:3]))

    def test_known_answers(self):
        fx = self.root
        code, env, _ = self.run_op("Q:stats", inp=fx / "asset-example-quant.json")
        s = self.summary(env)
        self.check("Q:stats mean is 5", self.near(s.get("mean"), 5.0))
        self.check("Q:stats sample SD is 2.138", self.near(s.get("sd_sample"), 2.13809, 1e-4))
        code, env, _ = self.run_op("Q:bayes", inp=fx / "asset-example-quant.json")
        self.check("Q:bayes posterior 0.1538", self.near(self.summary(env).get("posterior"), 0.153846, 1e-5))
        code, env, _ = self.run_op("Q:ev", inp=fx / "asset-example-quant.json")
        self.check("Q:ev expected value 52", self.near(self.summary(env).get("expected_value"), 52.0, 1e-9))
        code, env, _ = self.run_op("Q:pert", inp=fx / "asset-example-quant.json")
        self.check("Q:pert total mean 9.667", self.near(self.summary(env).get("total_mean"), 9.667, 1e-3))
        code, env, _ = self.run_op("Q:faulttree", inp=fx / "asset-example-quant.json")
        self.check("Q:faulttree top event 0.01495", self.near(self.summary(env).get("top_event_probability"), 0.01495, 1e-6))
        code, env, _ = self.run_op("Q:progress", inp=fx / "asset-example-quant.json")
        self.check("Q:progress ETA 2027-01-26", self.summary(env).get("eta_date") == "2027-01-26", str(self.summary(env)))
        code, env, _ = self.run_op("Q:allocate", inp=fx / "asset-example-quant.json")
        alloc = {i["item"]: i["allocated"] for i in self.items(env)}
        self.check("Q:allocate splits 100 as 25/75 by weight", alloc.get("design") == 25.0 and alloc.get("build") == 75.0, str(alloc))
        code, env, _ = self.run_op("Q:diminish", inp=fx / "asset-example-quant.json")
        self.check("Q:diminish stops at step 4", "step 4" in self.summary(env).get("recommendation", ""))
        code, env, _ = self.run_op("Q:optimize", inp=fx / "asset-example-quant.json")
        self.check("Q:optimize best value 7.25", self.near(self.summary(env).get("best_value"), 7.25, 1e-9))
        code, env, _ = self.run_op("Q:stress", inp=fx / "asset-example-quant.json")
        self.check("Q:stress base result 7", self.near(self.summary(env).get("base_result"), 7.0, 1e-9))
        code, env, _ = self.run_op("Q:sample-size", inp=fx / "asset-example-quant.json")
        self.check("Q:sample-size 63 per group", self.summary(env).get("per_group") == 63, str(self.summary(env)))
        code, env, _ = self.run_op("Q:brier", inp=fx / "asset-example-quant.json")
        self.check("Q:brier score 0.0967", self.near(self.summary(env).get("brier_score"), 0.0967, 1e-4))
        code, env, _ = self.run_op("Q:calibrate", inp=fx / "asset-example-quant.json")
        self.check("Q:calibrate flags overconfidence", "overconfident" in self.summary(env).get("verdict", ""))
        code, env, _ = self.run_op("Q:integrity", inp=fx / "asset-example-quant.json")
        self.check("Q:integrity one Holm-significant test", self.summary(env).get("holm_significant") == 1)
        code, env, _ = self.run_op("Q:evaluate", inp=fx / "asset-example-quant.json")
        self.check("Q:evaluate a*b+c = 7", self.near(self.summary(env).get("value"), 7.0, 1e-9))
        code, env, _ = self.run_op("Q:montecarlo", inp=fx / "asset-example-quant.json")
        self.check("Q:montecarlo reproducible with seed", code == 0 and self.summary(env).get("seed") == 7)
        code, env, _ = self.run_op("Q:stability", inp=fx / "asset-example-quant.json")
        self.check("Q:stability elasticity present", "condition_estimate" in self.summary(env))

        bad = self.write("bad-expr.json", {"expression": "__import__('os').system('echo hi')", "variables": {}})
        code, env, _ = self.run_op("Q:evaluate", inp=bad)
        self.check("Q:evaluate refuses code injection", env is not None and env.get("status") == "error")
        power = self.write("power.json", {"expression": "9 ** 9 ** 9", "variables": {}})
        code, env, _ = self.run_op("Q:evaluate", inp=power)
        self.check("Q:evaluate caps huge powers", env is not None and env.get("status") == "error")

        code, env, _ = self.run_op("D:critical", inp=fx / "asset-example-tasks.json")
        self.check("D:critical makespan 11", self.near(self.summary(env).get("makespan"), 11.0, 1e-9))
        code, env, _ = self.run_op("D:backward", inp=fx / "asset-example-tasks.json", opts=["deadline=10"])
        self.check("D:backward flags a 10-day deadline as infeasible", self.summary(env).get("feasible") is False)
        cyc = self.write("cycle.json", {"tasks": [{"id": "a", "deps": ["b"]}, {"id": "b", "deps": ["a"]}]})
        code, env, _ = self.run_op("D:topo", inp=cyc)
        self.check("D:topo rejects a cycle", env is not None and env.get("status") == "error" and "cycle" in " ".join(env.get("errors", [])).lower(),
                   str(env and env.get("errors")))

        code, env, _ = self.run_op("N:cycles", inp=fx / "asset-example-graph.json")
        self.check("N:cycles finds one loop", self.summary(env).get("loops") == 1)
        code, env, _ = self.run_op("N:audit", inp=fx / "asset-example-graph.json")
        self.check("N:audit finds the orphan node", any(i.get("subject") == "vendor" for i in self.items(env)))

        code, env, _ = self.run_op("P:matrix", inp=fx / "asset-example-decision.json")
        self.check("P:matrix ranks vendor-a first", self.summary(env).get("top") == "vendor-a")
        code, env, _ = self.run_op("P:score", inp=fx / "asset-example-decision.json")
        self.check("P:score weighted average 0.617", self.near(self.summary(env).get("weighted_average"), 0.617, 1e-3))
        code, env, _ = self.run_op("P:gate", inp=fx / "asset-example-decision.json")
        self.check("P:gate blocks on a critical failure", self.summary(env).get("verdict") == "BLOCK")
        code, env, _ = self.run_op("P:risk", inp=fx / "asset-example-decision.json")
        self.check("P:risk scores R1 as 160", any(i.get("id") == "R1" and i.get("score") == 160.0 for i in self.items(env)))

        code, env, _ = self.run_op("A:pii", inp=fx / "asset-example-notes.md")
        self.check("A:pii finds the email", self.summary(env).get("findings", 0) >= 1)
        self.check("A:pii output is redacted", "pm@example.com" not in json.dumps(env or {}))
        code, env, _ = self.run_op("A:causal", inp=fx / "asset-example-notes.md")
        self.check("A:causal flags 'drives'", any(i.get("match") == "drives" for i in self.items(env)))
        code, env, _ = self.run_op("R:extract", inp=fx / "asset-example-notes.md")
        self.check("R:extract finds requirements", self.summary(env).get("requirements", 0) >= 1)
        code, env, _ = self.run_op("E:quality", inp=fx / "asset-example-evidence.json")
        grade = {i["claim"]: i["grade"] for i in self.items(env)}
        self.check("E:quality grades C1 strong via primary data", grade.get("C1") == "strong", str(grade))
        code, env, _ = self.run_op("E:conflicts", inp=fx / "asset-example-evidence.json")
        self.check("E:conflicts finds one conflict", self.summary(env).get("conflicts") == 1)
        code, env, _ = self.run_op("E:map", inp=fx / "asset-example-evidence.json")
        self.check("E:map finds the unsupported claim", self.summary(env).get("gaps") == 1)
        bad_src = self.write("bad-src.json", {"sources": [{"id": "S", "type": "primary"}], "claims": []})
        code, env, _ = self.run_op("E:rank", inp=bad_src)
        self.check("E:rank rejects unknown source types", env is not None and env.get("status") == "error")
        code, env, _ = self.run_op("C:compress", inp=fx / "asset-example-notes.md")
        s = self.summary(env)
        self.check("C:compress never expands the text", s.get("output_tokens_est", 1e9) <= s.get("input_tokens_est", 0), str(s))
        combine = self.write("combine.json", {"elements": {"a": ["x", "y"], "b": ["1", "2"], "c": ["p", "q"]}})
        code, env, _ = self.run_op("X:combine", inp=combine)
        self.check("X:combine yields 8 combinations", self.summary(env).get("possible") == 8)
        vary = self.write("vary.json", {"base": {"c": "email", "l": "short"}, "variants": {"c": ["email", "blog"]}})
        code, env, _ = self.run_op("X:vary", inp=vary)
        self.check("X:vary skips identical values", self.summary(env).get("variants") == 1, str(self.summary(env)))
        code, env, _ = self.run_op("W:worksheet", inp=fx / "asset-example-notes.md")
        artifact = Path((env or {}).get("result", {}).get("artifact") or "")
        text = artifact.read_text(encoding="utf-8") if artifact.is_file() else ""
        self.check("W:worksheet fills every placeholder", text and "{{" not in text and "Generated (UTC)" in text)

    def test_state_and_gates(self):
        state = self.work / "state-gates"
        out = self.work / "out-gates"
        led_opts = ["ledger=selftest"]
        for n in range(3):
            code, env, _ = self.run_op("L:append", opts=led_opts + [f"step=s{n}"], state=state, out=out)
        self.check("L:append writes records", code == 0)
        code, env, _ = self.run_op("L:verify", opts=led_opts, state=state, out=out)
        self.check("L:verify reports an intact chain", self.summary(env).get("chain") == "intact")
        ledger = state / "ledgers" / "selftest.jsonl"
        original = ledger.read_text(encoding="utf-8")
        ledger.write_text(original.replace('"s1"', '"tampered"'), encoding="utf-8")
        code, env, _ = self.run_op("L:verify", opts=led_opts, state=state, out=out)
        self.check("L:verify detects tampering", self.summary(env).get("chain") == "broken")
        ledger.write_text(original, encoding="utf-8")
        code, env, _ = self.run_op("L:list", opts=led_opts + ["where=step=s2", "limit=5"], state=state, out=out)
        self.check("L:list filters with where=", self.summary(env).get("matching") == 1)

        lib = self.work / "lib.json"
        self.write("lib.json", {"title": "Checkout copy review", "body": "Check the refund wording before publishing.",
                                "tags": ["checkout", "legal"], "trigger": "checkout copy changes"})
        code, env, _ = self.run_op("B:add", inp=lib, state=state, out=out)
        self.check("B:add creates an entry", self.items(env)[0].get("status") == "created")
        code, env, _ = self.run_op("B:add", inp=lib, state=state, out=out)
        self.check("B:add with identical content is unchanged", self.items(env)[0].get("status") == "unchanged")
        code, env, _ = self.run_op("B:search", opts=["query=refund wording publishing"], state=state, out=out)
        self.check("B:search finds the entry", self.summary(env).get("matches") == 1)
        code, env, _ = self.run_op("B:retire", opts=["key=checkout-copy-review", "reason=test"], state=state, out=out)
        code, env, _ = self.run_op("B:search", opts=["query=refund wording publishing"], state=state, out=out)
        self.check("B:retire hides the entry from search", self.summary(env).get("matches") == 0)

        personal = self.write("personal.json", {"key": "contact", "value": "jane@example.com", "sensitivity": "personal"})
        code, env, _ = self.run_op("M:add", inp=personal, state=state, out=out)
        self.check("M:add personal data needs approval (exit 3)", code == 3 and env and env.get("status") == "approval_required", str(code))
        code, env, _ = self.run_op("M:add", inp=personal, state=state, out=out, extra=["--approve"])
        self.check("M:add personal data with --approve completes", code == 0 and env and env.get("status") == "completed")
        secret = self.write("secret.json", {"key": "token", "value": "abc", "sensitivity": "secret"})
        code, env, _ = self.run_op("M:add", inp=secret, state=state, out=out)
        self.check("M:add refuses secrets", code == 1 and env and env.get("status") == "error")
        a1 = self.write("m1.json", {"key": "units", "value": "metric", "confidence": 0.9, "expires": "2027-06-01"})
        a2 = self.write("m2.json", {"key": "units", "value": "imperial", "confidence": 0.5})
        self.run_op("M:add", inp=a1, state=state, out=out)
        code, env, _ = self.run_op("M:add", inp=a2, state=state, out=out)
        self.check("M:add keeps a different value as a conflict", self.items(env)[0].get("status") == "conflict")
        code, env, _ = self.run_op("M:conflicts", state=state, out=out)
        self.check("M:conflicts lists the units conflict", self.summary(env).get("conflicting_keys") == 1)
        code, env, _ = self.run_op("M:expire", as_of="2027-12-31", opts=["delete=true"], state=state, out=out)
        self.check("M:expire delete needs approval (exit 3)", code == 3, str(code))
        code, env, _ = self.run_op("M:expire", as_of="2027-12-31", state=state, out=out)
        self.check("M:expire without delete marks entries", code == 0 and self.summary(env).get("marked_expired", 0) >= 1)

        actions = self.root / "asset-example-actions.json"
        code, env, _ = self.run_op("V:classify", inp=actions, state=state, out=out)
        tiers = {i["id"]: i["tier"] for i in self.items(env)}
        self.check("V:classify rates the delete as critical", tiers.get("A2") == "critical", str(tiers))
        code, env, _ = self.run_op("V:gate", inp=actions, state=state, out=out)
        self.check("V:gate exits 3 when approval is missing", code == 3 and env and env.get("status") == "approval_required", str(code))
        code, env, _ = self.run_op("V:gate", inp=actions, state=state, out=out, opts=["approved=A2,A3,A4"])
        self.check("V:gate proceeds when every gated action is approved", code == 0 and self.summary(env).get("verdict") == "proceed")
        code, env, _ = self.run_op("V:idempotent", inp=actions, state=state, out=out)
        code, env, _ = self.run_op("V:idempotent", inp=actions, state=state, out=out)
        self.check("V:idempotent flags repeated keys", self.summary(env).get("duplicates") == 3)
        stop = self.write("stop.json", {"limits": {"max_actions": 5}, "observed": {"actions": 7}})
        code, env, _ = self.run_op("V:stop-check", inp=stop, state=state, out=out)
        self.check("V:stop-check returns STOP on breach", self.summary(env).get("verdict") == "STOP")
        audit = self.write("audit.json", {"allowed_actions": ["read"], "allowed_targets": ["team/"],
                                          "performed": [{"action": "delete", "target": "shared/x"}]})
        code, env, _ = self.run_op("V:audit", inp=audit, state=state, out=out)
        self.check("V:audit flags an out-of-scope action", self.summary(env).get("violations") == 1)
        priv = self.write("priv.json", {"requested": [{"permission": "admin", "scope": "*"}], "needed": []})
        code, env, _ = self.run_op("V:privilege", inp=priv, state=state, out=out)
        self.check("V:privilege flags broad admin", self.summary(env).get("excess_or_broad") == 1)

    def test_checkpoints(self):
        proj = self.work / "proj"
        self.write("plan.md", "# Plan\n\nStep one.\n", folder=proj)
        self.write("data/data.json", '{"a": 1}\n', folder=proj)
        state = self.work / "state-k"
        out = self.work / "out-k"
        code, env, _ = self.run_op("K:pack", inp=proj, opts=["name=snap"], state=state, out=out)
        manifest = out / "snap.manifest.json"
        self.check("K:pack writes archive and manifest", code == 0 and manifest.is_file() and (out / "snap.zip").is_file())
        code, env, _ = self.run_op("K:verify", inp=manifest, opts=["archive=true"], state=state, out=out)
        self.check("K:verify passes on untouched files", self.summary(env).get("verdict") == "verified", str(self.summary(env)))
        code, env, _ = self.run_op("K:latest", state=state, out=out)
        self.check("K:latest returns the verified snapshot", self.summary(env).get("latest_verified") == "snap")
        (proj / "plan.md").write_text("changed\n", encoding="utf-8")
        self.write("extra.txt", "new\n", folder=proj)
        code, env, _ = self.run_op("K:verify", inp=manifest, state=state, out=out)
        self.check("K:verify reports a changed file", self.summary(env).get("changed") == 1)
        self.check("K:verify reports an unexpected file", self.summary(env).get("unexpected") == 1)
        empty = self.work / "restore-empty"
        code, env, _ = self.run_op("K:restore", inp=manifest, opts=[f"target={empty}"], state=state, out=out, extra=[])
        self.check("K:restore into an empty folder works", code == 0 and (empty / "plan.md").read_text(encoding="utf-8") == "# Plan\n\nStep one.\n")
        full = self.work / "restore-full"
        self.write("plan.md", "old\n", folder=full)
        code, env, _ = self.run_op("K:restore", inp=manifest, opts=[f"target={full}"], state=state, out=out)
        self.check("K:restore into a non-empty folder needs approval (exit 3)", code == 3, str(code))
        code, env, _ = self.run_op("K:restore", inp=manifest, opts=[f"target={full}"], state=state, out=out, extra=["--approve"])
        backups = list((state / "backups").rglob("plan.md")) if (state / "backups").exists() else []
        self.check("approved restore backs up the overwritten file",
                   code == 0 and any(b.read_text(encoding="utf-8") == "old\n" for b in backups))
        archive = out / "snap.zip"
        good = archive.read_bytes()
        archive.write_bytes(good + b"x")
        code, env, _ = self.run_op("K:restore", inp=manifest, opts=[f"target={self.work / 'restore-bad'}"], state=state, out=out)
        self.check("K:restore refuses a tampered archive", code == 1 and env and "hash" in " ".join(env.get("errors", [])))
        archive.write_bytes(good)
        code, env, _ = self.run_op("K:handoff", inp=self.write("notes.json", {"next_actions": ["verify first"]}),
                                   state=state, out=out, opts=["title=Selftest"])
        handoff = Path(self.summary(env).get("artifact") or "")
        self.check("K:handoff writes a Markdown note", handoff.is_file() and "# Handoff" in handoff.read_text(encoding="utf-8"))

    def test_cli_and_limits(self):
        code, _, _ = self.engine_call("run", "ZZ.99", "--no-save")
        self.check("unknown capability exits 2", code == 2)
        code, _, _ = self.engine_call("run", "C01.01", "-o", "novalue", "--no-save")
        self.check("malformed option exits 2", code == 2)
        code, _, _ = self.engine_call("run", "C01.01", "--input", str(self.work / "missing.md"), "--no-save")
        self.check("missing input exits 2", code == 2)
        code, _, _ = self.engine_call("run", "C01.01", "--timeout", "99999", "--no-save")
        self.check("timeout above the maximum exits 2", code == 2)
        code, _, _ = self.engine_call("run", "C01.01", "--op", "Q:stats", "--no-save")
        self.check("operation from another family exits 2", code == 2)
        code, _, _ = self.engine_call("bogus")
        self.check("unknown command exits 2", code == 2)
        mc = self.write("mc.json", json.loads((self.root / "asset-example-quant.json").read_text(encoding="utf-8")) | {"n": 200000})
        code, env, _ = self.run_op("Q:montecarlo", inp=mc, extra=["--timeout", "0.2"])
        self.check("timeout stops the run and exits 4", code == 4 and env and env.get("status") == "timeout", str(code))
        if os.name == "posix":
            proc = subprocess.Popen([sys.executable, str(self.engine), "run", self.cap_for("Q:montecarlo")[0],
                                     "--op", "Q:montecarlo", "--input", str(mc), "--timeout", "60", "--no-save",
                                     "--format", "json", "--output-dir", str(self.work / "out-c"),
                                     "--state-dir", str(self.work / "state-c")], cwd=str(self.work),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(0.4)
            proc.send_signal(signal.SIGTERM)
            stdout, _ = proc.communicate(timeout=60)
            self.check("SIGTERM cancels the run (exit 130)", proc.returncode == 130, f"exit {proc.returncode}")
        default_run = self.work / "default-cwd"
        default_run.mkdir()
        code, out, _ = self.engine_call("run", "C01.01", "--input", str(self.root / "asset-example-notes.md"), cwd=default_run)
        created = (default_run / "thinking-output" / "objective-clarifier.report.md").is_file()
        self.check("default output folder is created beside the working folder", code == 0 and created)
        self.check("result and report files are written",
                   (default_run / "thinking-output" / "objective-clarifier.result.json").is_file())

    def test_hygiene(self):
        leftovers = [p.name for p in self.root.iterdir() if p.name.startswith(("thinking-", "__pycache__", ".")) or p.suffix == ".lock"]
        self.check("test runs leave no state in the Skill Tool folder", not leftovers, str(leftovers))
        total = sum(1 for _ in self.root.rglob("*") if _.is_file())
        self.check("package stays within 500 files", total <= 500, f"{total} files")
        deepest = max((len(p.relative_to(self.root).parts) for p in self.root.rglob("*")), default=0)
        self.check("paths stay within three levels", deepest <= 3, f"depth {deepest}")

    def run_all(self):
        copied = self.install_copy()
        self.check("copied prefixed files to a clean root", copied >= 19, f"{copied} files")
        for group in (self.test_package, self.test_known_answers, self.test_state_and_gates,
                      self.test_checkpoints, self.test_cli_and_limits, self.test_hygiene):
            try:
                group()
            except Exception as exc:  # a crash in one group is a failure, not a silent pass
                self.check(f"{group.__name__} completed", False, f"{type(exc).__name__}: {exc}")
        failed = [r for r in self.results if not r[1]]
        for name, ok, detail in self.results:
            print(("PASS " if ok else "FAIL ") + name + ("" if ok or not detail else f"  [{detail[:160]}]"))
        print(f"\n{len(self.results) - len(failed)} passed, {len(failed)} failed, of {len(self.results)} checks")
        print(f"temporary root: {self.tmp}")
        if not self.keep:
            shutil.rmtree(self.tmp, ignore_errors=True)
        return 0 if not failed else 1


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(Suite(keep="--keep" in sys.argv).run_all())
