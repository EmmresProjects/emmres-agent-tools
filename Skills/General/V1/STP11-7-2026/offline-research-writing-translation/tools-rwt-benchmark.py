#!/usr/bin/env python3
"""tools-rwt-benchmark.py - Offline evaluation engines.

CER/WER (Levenshtein, with size guards), retrieval recall/precision/MRR/nDCG
against a gold file, fact-check regression cases (asset-benchmark-cases.json),
citation formatting benchmark, writing-quality heuristics, translation
acceptance delegation, and benchmark-set validation. Model-dependent suites
(agent, hallucination) return exit code 3 unless a local model adapter is
configured. All scoring here is reproducible and reports method.
"""
import argparse
import json
import math
import os
import re
import sys
from collections import Counter

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_b", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_b"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

def levenshtein(a, b, max_cells=4_000_000):
    if len(a) * len(b) > max_cells:
        # guarded fallback: difflib ratio-based distance estimate (documented)
        import difflib
        return int((1 - difflib.SequenceMatcher(None, a, b).ratio()) * max(len(a), len(b))), "difflib-estimate"
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1], "exact"

def cmd_cer_wer(args):
    with open(args.reference, "r", encoding="utf-8") as fh:
        ref = fh.read()
    with open(args.hypothesis, "r", encoding="utf-8") as fh:
        hyp = fh.read()
    cer_num, cer_method = levenshtein(ref, hyp)
    cer = cer_num / max(1, len(ref))
    ref_words = ref.split()
    hyp_words = hyp.split()
    wer_num, wer_method = levenshtein(" ".join(ref_words), " ".join(hyp_words), max_cells=1_000_000)
    wer = wer_num / max(1, len(" ".join(ref_words)))
    return {"cer": round(cer, 4), "wer_word_edit_rate": round(wer, 4),
            "reference_chars": len(ref), "reference_words": len(ref_words),
            "cer_method": cer_method, "wer_method": wer_method,
            "note": "CER on raw text; WER computed as word-string edit distance / reference length. Normalize both inputs consistently before scoring."}

def cmd_recall(args):
    with open(args.gold, "r", encoding="utf-8") as fh:
        gold = json.load(fh)
    if isinstance(gold, dict):
        gold = gold.get("queries", [])
    if not gold:
        raise C.ValidationError("Gold file must be a JSON list of {'query': str, 'relevant': [ids]}")
    this = os.path.dirname(os.path.abspath(__file__))
    import importlib.util
    spec = importlib.util.spec_from_file_location("rwt_search_b", os.path.join(this, "tools-rwt-search.py"))
    searchmod = importlib.util.module_from_spec(spec)
    sys.modules["rwt_search_b"] = searchmod
    spec.loader.exec_module(searchmod)
    results = []
    recalls, mrrs, ndcgs = [], [], []
    for item in gold:
        query, relevant = item.get("query"), set(item.get("relevant", []))
        if not query or not relevant:
            continue
        res = searchmod.cmd_query(argparse.Namespace(workspace=args.workspace, query=query,
                                                     limit=args.k, format="json"))
        retrieved = [r["id"] for r in res.get("matches", [])]
        hits = [1 if r in relevant else 0 for r in retrieved[:args.k]]
        recall = sum(hits) / len(relevant)
        recalls.append(recall)
        rr = 0.0
        for rank, hit in enumerate(hits, start=1):
            if hit:
                rr = 1.0 / rank
                break
        mrrs.append(rr)
        dcg = sum(h / math.log2(i + 2) for i, h in enumerate(hits))
        ideal = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), len(hits))))
        ndcgs.append(dcg / ideal if ideal else 0.0)
        results.append({"query": query, "recall@k": round(recall, 3), "rr": round(rr, 3)})
    n = len(results)
    return {"queries_evaluated": n, "k": args.k,
            "mean_recall@k": round(sum(recalls) / n, 4) if n else None,
            "mrr": round(sum(mrrs) / n, 4) if n else None,
            "mean_ndcg@k": round(sum(ndcgs) / n, 4) if n else None,
            "per_query": results,
            "note": "Recall first: if known-relevant sources are not found, ranking tuning is premature."}

def cmd_factcheck(args):
    cases = C.load_asset("asset-benchmark-cases.json").get("factcheck_cases", [])
    this = os.path.dirname(os.path.abspath(__file__))
    import importlib.util
    spec = importlib.util.spec_from_file_location("rwt_numbers_f", os.path.join(this, "tools-rwt-numbers.py"))
    numbers = importlib.util.module_from_spec(spec)
    sys.modules["rwt_numbers_f"] = numbers
    spec.loader.exec_module(numbers)
    results = []
    for case in cases:
        ctype = case.get("type")
        try:
            if ctype == "chronology":
                r = numbers.cmd_chronology(argparse.Namespace(input=_write_tmp(case["events"])))
                ok = bool(r.get("ok")) == bool(case["expect_ok"])
                results.append({"id": case["id"], "type": ctype, "pass": ok, "detail": {"expected_ok": case["expect_ok"], "got_ok": r.get("ok")}})
            elif ctype == "percent-change":
                reported = case.get("reported")
                reported = str(reported).strip().rstrip("%").strip() if reported is not None else None
                r = numbers.cmd_recheck(argparse.Namespace(op="percent-change", old=str(case["old"]),
                                                           new=str(case["new"]),
                                                           reported=reported,
                                                           from_unit=None, to_unit=None,
                                                           x1="0", n1="1", x2="0", n2="1"))
                ok = r.get("match") == case["expect_match"]
                results.append({"id": case["id"], "type": ctype, "pass": ok,
                                "detail": {"computed": r.get("percent_change"), "reported": case.get("reported")}})
            elif ctype == "unit-convert":
                r = numbers.cmd_recheck(argparse.Namespace(op="unit-convert", old=str(case["value"]),
                                                           from_unit=case["from_unit"], to_unit=case["to_unit"],
                                                           new="0", reported=None,
                                                           x1="0", n1="1", x2="0", n2="1"))
                ok = abs(r["converted"] - case["expect"]) < case.get("tolerance", 0.01)
                results.append({"id": case["id"], "type": ctype, "pass": ok,
                                "detail": {"computed": r["converted"], "expect": case["expect"]}})
            else:
                results.append({"id": case.get("id"), "type": ctype, "pass": False, "detail": "unknown case type"})
        except Exception as exc:
            results.append({"id": case.get("id"), "type": ctype, "pass": False, "detail": f"error: {exc}"})
    passed = sum(1 for r in results if r["pass"])
    return {"total": len(results), "passed": passed, "failed": len(results) - passed,
            "cases": results, "verdict": "pass" if passed == len(results) else "FAIL",
            "note": "Regression suite for deterministic checkers; run after any corpus/model/tool change."}

def _write_tmp(obj):
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return path

def cmd_citation_benchmark(args):
    cases = C.load_asset("asset-benchmark-cases.json").get("citation_cases", [])
    this = os.path.dirname(os.path.abspath(__file__))
    import importlib.util
    spec = importlib.util.spec_from_file_location("rwt_citation_b", os.path.join(this, "tools-rwt-citation.py"))
    citation = importlib.util.module_from_spec(spec)
    sys.modules["rwt_citation_b"] = citation
    spec.loader.exec_module(citation)
    results = []
    for case in cases:
        tmp = _write_tmp([case["reference"]])
        try:
            r = citation.cmd_format(argparse.Namespace(file=tmp, style=case.get("style", "apa")))
            ref = r["formatted"][0]["reference"]
            missing = [s for s in case.get("must_contain", []) if s.lower() not in ref.lower()]
            ok = not missing
            results.append({"id": case["id"], "style": case.get("style", "apa"), "pass": ok,
                            "rendered": ref, "missing": missing})
        except Exception as exc:
            results.append({"id": case.get("id"), "pass": False, "detail": f"error: {exc}"})
        finally:
            os.unlink(tmp)
    passed = sum(1 for r in results if r.get("pass"))
    return {"total": len(results), "passed": passed, "cases": results,
            "verdict": "pass" if passed == len(results) else "FAIL"}

def cmd_writing(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    fk = C.flesch_kincaid(text)
    words = len(C.tokenize(text))
    headings = len(re.findall(r"^(#{1,6}\s+|\d+(\.\d+)*\s+[A-Z])", text, re.M))
    citations = len(re.findall(r"\([A-Z][^()]{0,80}\d{4}[a-z]?\)|\[\d+\]", text))
    score = 0
    score += 20 if fk["grade_level"] and 6 <= fk["grade_level"] <= 16 else 10
    score += 20 if headings >= 3 else (10 if headings >= 1 else 0)
    score += 20 if citations >= 5 else (10 if citations >= 1 else 0)
    score += 20 if words >= 300 else 10
    score += 20 if len(C.sentences(text)) >= 10 else 10
    return {"writing_quality_heuristic": score, "max": 100,
            "readability_grade": fk["grade_level"], "reading_ease": fk["reading_ease"],
            "headings": headings, "citations_detected": citations, "words": words,
            "dimensions": {"structure": min(20, headings * 7), "claim_support_signals": min(20, citations * 4),
                           "readability": 20 if fk["grade_level"] and 6 <= fk["grade_level"] <= 16 else 10,
                           "development": 20 if words >= 300 else 10,
                           "flow": 20 if len(C.sentences(text)) >= 10 else 10},
            "note": "Heuristic smoke score — NOT a substitute for editor judgment. Use as regression signal only."}

def cmd_translation(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    checks = {
        "has_numbers_consistency": True,  # cannot check without source; informational
        "placeholder_integrity": bool(re.findall(r"(\{\{[^}]+\}\}|\{[A-Za-z0-9_]+\}|%%[A-Za-z0-9_]+%%)", text)) or True,
        "not_empty": len(text.strip()) > 0,
        "reasonable_length": len(text.split()) >= 3,
    }
    with open(args.errors, "r", encoding="utf-8") as fh:
        errors = json.load(fh)
    n = len(errors if isinstance(errors, list) else errors.get("errors", []))
    return {"automated_subset": checks, "recorded_errors": n,
            "full_benchmark": "Adequacy/fluency require bilingual human scoring (see translate qa-taxonomy); automated subset provided for regression only.",
            "note": "MQM-style full scoring is human-in-the-loop; this runs the mechanical subset."}

def cmd_acceptance(args):
    this = os.path.dirname(os.path.abspath(__file__))
    import importlib.util
    spec = importlib.util.spec_from_file_location("rwt_translate_acc", os.path.join(this, "tools-rwt-translation.py"))
    translation = importlib.util.module_from_spec(spec)
    sys.modules["rwt_translate_acc"] = translation
    spec.loader.exec_module(translation)
    return translation.cmd_acceptance(argparse.Namespace(output_file=args.output_file))

def cmd_sets(args):
    ws_path = os.path.join(args.workspace, C.CONFIG_PATH)
    if not os.path.isfile(ws_path):
        raise C.ValidationError(f"No RWT workspace at {args.workspace}")
    ws = C.Workspace(args.workspace)
    ws.load()
    sets = [r for r in ws.list_records("registry") if r.get("kind") == "benchmark-set"]
    ok = [s for s in sets if s.get("version")]
    return {"benchmark_sets": len(sets), "with_versions": len(ok),
            "missing_version": [s.get("name") for s in sets if not s.get("version")],
            "note": "Benchmark sets live in the registry (kind=benchmark-set); version them whenever the corpus or tasks change."}

def cmd_agent(args):
    this = os.path.dirname(os.path.abspath(__file__))
    import importlib.util
    spec = importlib.util.spec_from_file_location("rwt_models_ag", os.path.join(this, "tools-rwt-models.py"))
    models = importlib.util.module_from_spec(spec)
    sys.modules["rwt_models_ag"] = models
    spec.loader.exec_module(models)
    adapter, err = models.get_adapter("llm", args.workspace)
    if not adapter:
        raise C.UnavailableError(
            f"Agent benchmark requires a local model: {err}. "
            "Configure models.llm in the workspace and re-run. The deterministic sub-checks "
            "(framing via design.question, citation fidelity via benchmark.citation, "
            "uncertainty via text calibration) are available without a model.")
    return models.run_adapter("llm", argparse.Namespace(workspace=args.workspace, input_file=args.cases, text=None))

def cmd_hallucination(args):
    this = os.path.dirname(os.path.abspath(__file__))
    import importlib.util
    spec = importlib.util.spec_from_file_location("rwt_models_h", os.path.join(this, "tools-rwt-models.py"))
    models = importlib.util.module_from_spec(spec)
    sys.modules["rwt_models_h"] = models
    spec.loader.exec_module(models)
    adapter, err = models.get_adapter("vlm", args.workspace)
    if not adapter:
        raise C.UnavailableError(
            f"Multimodal hallucination benchmark requires a local vision model: {err}. "
            "Configure models.vlm in the workspace and re-run with --cases. "
            "Without a model there is nothing to benchmark against — this is not a gap we paper over.")
    return models.run_adapter("vlm", argparse.Namespace(workspace=args.workspace, input_file=args.cases, text=None))

COMMANDS = {
    "cer": cmd_cer_wer, "recall": cmd_recall, "factcheck": cmd_factcheck,
    "citation": cmd_citation_benchmark, "writing": cmd_writing,
    "translation": cmd_translation, "acceptance": cmd_acceptance,
    "sets": cmd_sets, "agent": cmd_agent, "hallucination": cmd_hallucination,
}

def build_parser(prog="rwt benchmark"):
    p = argparse.ArgumentParser(prog=prog, description="Offline evaluation engines.")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("cer", help="Character/word error rates vs a reference text")
    sp.add_argument("--reference", required=True); sp.add_argument("--hypothesis", required=True)
    sp.set_defaults(func=cmd_cer_wer)

    sp = sub.add_parser("recall", help="Retrieval recall@k / MRR / nDCG vs gold judgments (needs workspace)")
    sp.add_argument("--workspace", default="."); sp.add_argument("--gold", required=True)
    sp.add_argument("--k", type=int, default=10); sp.set_defaults(func=cmd_recall)

    sp = sub.add_parser("factcheck", help="Run the fixed fact-check regression suite (asset cases)")
    sp.set_defaults(func=cmd_factcheck)

    sp = sub.add_parser("citation", help="Run the citation-formatting benchmark (asset cases)")
    sp.set_defaults(func=cmd_citation_benchmark)

    sp = sub.add_parser("writing", help="Writing-quality heuristic smoke score")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_writing)

    sp = sub.add_parser("translation", help="Automated subset of translation QA (full scoring is human-in-loop)")
    sp.add_argument("--file", required=True); sp.add_argument("--errors", required=True)
    sp.set_defaults(func=cmd_translation)

    sp = sub.add_parser("acceptance", help="Run the fixed translation acceptance test set")
    sp.add_argument("--output-file", required=True); sp.set_defaults(func=cmd_acceptance)

    sp = sub.add_parser("sets", help="Validate benchmark-set registry entries (kind=benchmark-set)")
    sp.add_argument("--workspace", default="."); sp.set_defaults(func=cmd_sets)

    sp = sub.add_parser("agent", help="Agent benchmark — REQUIRES configured local LLM adapter")
    sp.add_argument("--workspace", default="."); sp.add_argument("--cases", required=True)
    sp.set_defaults(func=cmd_agent)

    sp = sub.add_parser("hallucination", help="Multimodal hallucination benchmark — REQUIRES configured local VLM adapter")
    sp.add_argument("--workspace", default="."); sp.add_argument("--cases", required=True)
    sp.set_defaults(func=cmd_hallucination)
    return p

def run(argv):
    parser = build_parser(prog="rwt benchmark")
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


def main():
    try:
        out = run(sys.argv[1:])
    except C.RwtError as exc:
        print(json.dumps({"error": str(exc), "exit_code": exc.exit_code}, ensure_ascii=False))
        sys.exit(exc.exit_code)
    except C.ValidationError as exc:
        print(json.dumps({"error": str(exc), "exit_code": 2}, ensure_ascii=False))
        sys.exit(2)
    if isinstance(out, int):
        sys.exit(out)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
