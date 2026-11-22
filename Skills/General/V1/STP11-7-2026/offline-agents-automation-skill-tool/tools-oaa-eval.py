#!/usr/bin/env python3
"""tools-oaa-eval.py -- offline evaluation, benchmarking and regression gating.

Loaded by `tools-oaa.py`. Registers:

  eval.run      run the bundled or supplied dataset and produce a scored report
  eval.compare  compare a report with a stored baseline and flag regressions
  eval.dataset  derive a retrieval evaluation dataset from an existing index

Task types supported by the runner: retrieval, grounding, calc, schema, injection.
Everything runs locally; no model call is required, and a report records the exact
configuration, dataset checksum and per-case evidence so results are reproducible.
"""
from __future__ import annotations

import json
import os
import time

import importlib.util as _ilu
import sys as _sys

# Load the shared kernel and sibling modules exactly once per process.
_HERE = os.path.dirname(os.path.abspath(__file__))
if "oaa_kernel" in _sys.modules:
    K = _sys.modules["oaa_kernel"]
else:
    _spec = _ilu.spec_from_file_location("oaa_kernel",
                                        os.path.join(_HERE, "tools-oaa-kernel.py"))
    K = _ilu.module_from_spec(_spec)
    _sys.modules["oaa_kernel"] = K
    _spec.loader.exec_module(K)


def _load_sibling(name, filename):
    if name in _sys.modules:
        return _sys.modules[name]
    spec = _ilu.spec_from_file_location(name, os.path.join(_HERE, filename))
    module = _ilu.module_from_spec(spec)
    _sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


KNOW = _load_sibling("oaa_knowledge", "tools-oaa-knowledge.py")
GUARD = _load_sibling("oaa_guard", "tools-oaa-guard.py")

TASKS = ("retrieval", "grounding", "calc", "schema", "injection")


def resolve_dataset(ctx, dataset):
    """Resolve a dataset path: workspace scope first, then the bundled asset."""
    if dataset:
        try:
            return ctx.workspace.resolve_asset(dataset, must_exist=True)
        except K.OAAError:
            raise K.NotFoundError("dataset not found", dataset=dataset,
                                  read_roots=ctx.workspace.read_scope.describe(),
                                  bundled=K.resource_path(
                                      os.path.basename(str(dataset))))
    bundled = K.resource_path(K.EVAL_DATASET_FILENAME)
    if not os.path.isfile(bundled):
        raise K.NotFoundError("bundled evaluation dataset is missing",
                              asset=K.EVAL_DATASET_FILENAME)
    return bundled


def run_case(cfg, case, retrieval_mode="hybrid"):
    """Execute one evaluation case and return a structured result."""
    task = case.get("task")
    case_id = case.get("id", "unnamed")
    started = time.time()
    try:
        if task == "retrieval":
            index = KNOW.make_in_memory_index(cfg, case.get("corpus", []),
                                              strategy="paragraph")
            results = KNOW.rank_chunks(cfg, index, case.get("query", ""),
                                       mode=retrieval_mode)
            expect = case.get("expect", {})
            top = results[0] if results else None
            problems = []
            if expect.get("top_source"):
                if not top:
                    problems.append("no retrieval results")
                elif top["source"] != expect["top_source"]:
                    problems.append("top source was %s, expected %s"
                                    % (top["source"], expect["top_source"]))
            if top:
                top_terms = KNOW.normalize_terms(top["text"])
                for term in expect.get("must_include_terms", []):
                    wanted = KNOW.normalize_terms(term)
                    if wanted and not set(wanted).issubset(set(top_terms)):
                        problems.append("top chunk missing term: %s" % term)
            elif not problems:
                problems.append("no retrieval results")
            return {"id": case_id, "task": task, "passed": not problems,
                    "problems": problems,
                    "observed": {"top_source": top["source"] if top else None,
                                 "top_score": top["score"] if top else None,
                                 "results": len(results)}}
        if task == "grounding":
            report = GUARD.grounding_report(cfg, case.get("answer"),
                                            case.get("evidence", []),
                                            {"require_citations": False})
            expect = case.get("expect", {})
            problems = []
            if "supported_ratio_min" in expect and \
                    report["supported_ratio"] < expect["supported_ratio_min"]:
                problems.append("supported_ratio %.3f below %.3f"
                                % (report["supported_ratio"],
                                   expect["supported_ratio_min"]))
            if "supported_ratio_max" in expect and \
                    report["supported_ratio"] > expect["supported_ratio_max"]:
                problems.append("supported_ratio %.3f above %.3f"
                                % (report["supported_ratio"],
                                   expect["supported_ratio_max"]))
            if "verified_citations" in expect and \
                    report["citations"]["verified"] != expect["verified_citations"]:
                problems.append("verified citations %d != %d"
                                % (report["citations"]["verified"],
                                   expect["verified_citations"]))
            return {"id": case_id, "task": task, "passed": not problems,
                    "problems": problems,
                    "observed": {"supported_ratio": report["supported_ratio"],
                                 "grounded": report["grounded"]}}
        if task == "calc":
            result = GUARD.evaluate_calc(case.get("expression"),
                                        case.get("expect_value"),
                                        case.get("tolerance", 1e-6))
            problems = [] if result["matches_expected"] else [
                "computed %r but expected %r" % (result["exact_as_float"],
                                                 case.get("expect_value"))]
            return {"id": case_id, "task": task, "passed": not problems,
                    "problems": problems,
                    "observed": {"value": result["exact_as_float"],
                                 "consistent": result["consistent"]}}
        if task == "schema":
            errors = GUARD.validate_schema(case.get("document"),
                                           case.get("schema", {}))
            expect = case.get("expect", {})
            problems = []
            if expect.get("valid") is not None and bool(errors) == expect["valid"]:
                problems.append("validity mismatched: %d errors" % len(errors))
            if "min_errors" in expect and len(errors) < expect["min_errors"]:
                problems.append("expected at least %d errors, saw %d"
                                % (expect["min_errors"], len(errors)))
            return {"id": case_id, "task": task, "passed": not problems,
                    "problems": problems,
                    "observed": {"errors": len(errors),
                                 "first_error": errors[0] if errors else None}}
        if task == "injection":
            result = GUARD.detect_injection(case.get("text", ""))
            expect = case.get("expect", {})
            problems = []
            if expect.get("detected") is not None and \
                    result["detected"] != expect["detected"]:
                problems.append("detected=%s but expected %s"
                                % (result["detected"], expect["detected"]))
            return {"id": case_id, "task": task, "passed": not problems,
                    "problems": problems,
                    "observed": {"detected": result["detected"],
                                 "severity": result["severity"]}}
        raise K.InputError("unknown evaluation task", task=task, id=case_id,
                           allowed=list(TASKS))
    except K.OAAError as exc:
        return {"id": case_id, "task": task, "passed": False,
                "problems": ["error: %s" % exc.message],
                "observed": exc.to_dict().get("error")}
    finally:
        _ = round((time.time() - started) * 1000, 2)


def _register(registry):
    reg = registry

    # ---------------------------------------------------------------- eval.run
    @reg.op("eval.run",
            "Run an evaluation dataset and produce a scored, reproducible report.",
            params=["dataset", "tasks", "retrieval_mode", "save_report",
                    "baseline_label"],
            group="evaluation", mutating=True,
            examples=['{"dataset": "asset-eval-dataset.jsonl"}'])
    def eval_run(ctx, params):
        dataset_path = resolve_dataset(ctx, params.get("dataset"))
        rows = K.read_jsonl(dataset_path)
        if not rows:
            raise K.InputError("dataset is empty", dataset=dataset_path)
        if len(rows) > 20000:
            raise K.BudgetError("dataset exceeds the 20000 case ceiling",
                                cases=len(rows))
        tasks = params.get("tasks")
        if tasks:
            if isinstance(tasks, str):
                tasks = [t.strip() for t in tasks.split(",") if t.strip()]
            unknown = [t for t in tasks if t not in TASKS]
            if unknown:
                raise K.InputError("unknown evaluation tasks", tasks=unknown,
                                   allowed=list(TASKS))
            rows = [r for r in rows if r.get("task") in tasks]
        mode = params.get("retrieval_mode", "hybrid")
        if mode not in ("bm25", "dense", "hybrid"):
            raise K.InputError("retrieval_mode must be bm25, dense or hybrid",
                               mode=mode)
        started = time.time()
        results = [run_case(ctx.cfg, case, retrieval_mode=mode) for case in rows]
        by_task = {}
        for result in results:
            bucket = by_task.setdefault(result["task"],
                                        {"total": 0, "passed": 0, "failures": []})
            bucket["total"] += 1
            if result["passed"]:
                bucket["passed"] += 1
            else:
                bucket["failures"].append({"id": result["id"],
                                           "problems": result["problems"]})
        for bucket in by_task.values():
            bucket["accuracy"] = round(bucket["passed"] / bucket["total"], 4)
        passed = sum(1 for r in results if r["passed"])
        report = {
            "schema": "oaa-eval-report/1",
            "generated_at": K.now_iso(),
            "dataset": os.path.basename(dataset_path),
            "dataset_sha256": K.sha256_file(dataset_path),
            "cases": len(results),
            "retrieval_mode": mode,
            "tasks": by_task,
            "overall": {"total": len(results), "passed": passed,
                        "score": round(passed / len(results), 4)},
            "duration_ms": round((time.time() - started) * 1000, 2),
            "config_digest": K.sha256_obj({
                "retrieval": ctx.cfg.get("retrieval"),
                "guard": ctx.cfg.get("guard"),
            }),
            "provider": ctx.provider.info(),
            "cases_detail": results,
        }
        out = params.get("save_report")
        if out:
            path = ctx.workspace.scope.resolve(out)
            K.write_json(path, report)
            report["report_path"] = ctx.workspace.rel(path)
            report["report_sha256"] = K.sha256_file(path)
        ctx.emit("eval.run", action="eval.run", target=report["dataset"],
                 score=report["overall"]["score"], cases=len(results))
        return report

    # ---------------------------------------------------------------- eval.compare
    @reg.op("eval.compare",
            "Compare an evaluation report with a stored baseline and gate regressions.",
            params=["report", "baseline", "tolerance", "block_on_regression"],
            group="evaluation",
            examples=['{"report": "baselines/current.json", "baseline": '
                      '"baselines/release-1.0.json"}'])
    def eval_compare(ctx, params):
        def load(kind, value):
            if not value:
                raise K.InputError("eval.compare requires params.%s" % kind)
            candidate = os.path.join(ctx.workspace.root, value)
            path = candidate if os.path.isfile(candidate) else value
            if not os.path.isfile(path):
                raise K.NotFoundError("%s not found" % kind, value=value)
            return K.read_json(path, max_bytes=64 * 1024 * 1024), path

        current, current_path = load("report", params.get("report"))
        baseline, baseline_path = load("baseline", params.get("baseline"))
        tolerance = float(params.get("tolerance",
                                     K.get_path(ctx.cfg, "evaluation.tolerance", 0.0)))
        block = bool(params.get("block_on_regression",
                                K.get_path(ctx.cfg,
                                           "evaluation.block_on_regression", True)))
        regressions, improvements = [], []
        current_score = float(current.get("overall", {}).get("score", 0.0))
        baseline_score = float(baseline.get("overall", {}).get("score", 0.0))
        delta = round(current_score - baseline_score, 4)
        if delta < -tolerance:
            regressions.append({"scope": "overall", "baseline": baseline_score,
                                "current": current_score, "delta": delta})
        elif delta > tolerance:
            improvements.append({"scope": "overall", "baseline": baseline_score,
                                 "current": current_score, "delta": delta})
        for task, bucket in sorted(baseline.get("tasks", {}).items()):
            base_acc = float(bucket.get("accuracy", 0.0))
            cur_acc = float(current.get("tasks", {}).get(task, {})
                            .get("accuracy", 0.0))
            task_delta = round(cur_acc - base_acc, 4)
            entry = {"scope": task, "baseline": base_acc, "current": cur_acc,
                     "delta": task_delta}
            if task_delta < -tolerance:
                regressions.append(entry)
            elif task_delta > tolerance:
                improvements.append(entry)
        result = {
            "ok": not regressions,
            "regressed": bool(regressions),
            "regressions": regressions,
            "improvements": improvements,
            "tolerance": tolerance,
            "current": {"path": current_path, "score": current_score,
                        "dataset": current.get("dataset")},
            "baseline": {"path": baseline_path, "score": baseline_score,
                         "dataset": baseline.get("dataset")},
            "blocked": bool(regressions) and block,
        }
        ctx.emit("eval.compare", action="eval.compare",
                 regressed=result["regressed"], blocked=result["blocked"])
        if result["blocked"]:
            raise K.IntegrityError(
                "evaluation regression exceeds tolerance",
                regressions=regressions, tolerance=tolerance)
        return result

    # ---------------------------------------------------------------- eval.dataset
    @reg.op("eval.dataset",
            "Derive retrieval evaluation cases from an existing index.",
            params=["index", "output", "limit", "min_chars"], group="evaluation",
            mutating=True,
            examples=['{"index": "kb", "output": "artifacts/eval-from-index.jsonl"}'])
    def eval_dataset(ctx, params):
        index = KNOW.load_index(ctx, params.get("index", "default"))
        out = params.get("output") or params.get("out")
        if not out:
            raise K.InputError("eval.dataset requires params.output")
        limit = int(params.get("limit", 10))
        min_chars = int(params.get("min_chars", 80))
        cases = []
        for chunk in index["chunks"]:
            if len(chunk["text"]) < min_chars:
                continue
            sentence = chunk["text"].split(". ")[0].strip()
            if len(sentence) < 40:
                continue
            cases.append({
                "id": "derived-%03d" % (len(cases) + 1),
                "task": "retrieval",
                "query": sentence[:200],
                "corpus": [{"source": c["source"], "text": c["text"]}
                           for c in index["chunks"]],
                "expect": {"top_source": chunk["source"]},
                "derived_from_index": index["name"],
            })
            if len(cases) >= limit:
                break
        if not cases:
            raise K.StateError("index produced no usable evaluation cases",
                               index=index["name"], min_chars=min_chars)
        path = ctx.workspace.scope.resolve(out)
        K.ensure_parent(path)
        with open(path, "w", encoding="utf-8") as fh:
            for case in cases:
                fh.write(json.dumps(case, ensure_ascii=False) + "\n")
        ctx.emit("eval.dataset", action="eval.dataset", target=ctx.workspace.rel(path),
                 cases=len(cases))
        return {"path": ctx.workspace.rel(path), "cases": len(cases),
                "sha256": K.sha256_file(path)}


def register(registry):
    _register(registry)
    return registry
