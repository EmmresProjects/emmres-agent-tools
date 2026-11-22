#!/usr/bin/env python3
"""tools-rwt-numbers.py - Quantitative accuracy engines.

Capabilities: quantity/unit extraction (ranges, uncertainty, n=, denominators),
number & unit rechecking (percent change, ratios, unit conversion), proportion
statistics (SE, z for two proportions), effect sizes & meta-analysis preparation
(Cohen's d, odds ratio, risk ratio + standard errors), Cohen's/Fleiss' kappa for
reviewer agreement, chronology consistency, and chart-vs-text consistency from
provided chart values.
"""
import argparse
import json
import math
import os
import re
import sys
from datetime import datetime

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_n", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_n"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

QUANTITY_RE = re.compile(
    r"(?P<value>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<pm>±|\+/-)\s*(?P<unc>\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-zµμ°%/][A-Za-z0-9µμ°%/\-\^]*)?"
    r"|(?P<v2>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit2>%|kg|g|mg|µg|ug|ng|mm|cm|m|km|mL|ml|L|l|mol|mmol|IU|kcal|J|kJ|Hz|kHz|MHz|GPa|Pa|mmHg|°C|°F|K|US\$|USD|PHP|EUR|dollars?|pesos)\b"
    r"|n\s*=\s*(?P<n>\d+)"
    r"|(?P<pct>\d+(?:\.\d+)?)\s*%"
    r"|(?P<range>\d+(?:\.\d+)?)\s*(?:to|-|–)\s*(?P<range_hi>\d+(?:\.\d+)?)\s*(?P<range_unit>[A-Za-zµμ°/][A-Za-z0-9µμ°/\-\^]*)?",
    re.I)

def cmd_extract(args):
    if not os.path.isfile(args.file):
        raise C.ValidationError(f"File not found: {args.file}")
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    out = []
    for m in QUANTITY_RE.finditer(text):
        rec = {"raw": C.normalize_ws(m.group(0))}
        if m.group("value"):
            rec.update(kind="value±uncertainty", value=float(m.group("value").replace(",", "")),
                       uncertainty=float(m.group("unc")), unit=m.group("unit"))
        elif m.group("v2"):
            rec.update(kind="value+unit", value=float(m.group("v2").replace(",", "")),
                       unit=m.group("unit2"))
        elif m.group("n"):
            rec.update(kind="sample_size", value=int(m.group("n")), unit=None)
        elif m.group("pct"):
            rec.update(kind="percentage", value=float(m.group("pct")), unit="%")
        elif m.group("range"):
            lo, hi = float(m.group("range")), float(m.group("range_hi"))
            rec.update(kind="range", low=lo, high=hi, unit=m.group("range_unit"),
                       ordered=lo <= hi)
        else:
            continue
        rec["context"] = C.normalize_ws(text[max(0, m.start() - 60):m.end() + 60])
        out.append(rec)
    return {"quantities": out, "count": len(out),
            "note": "Extraction is syntactic; denominators, sampling frames and analysis sets require human verification."}

def cmd_recheck(args):
    """Recompute percentage-point change, relative change, ratio, or unit conversion from source numbers."""
    op = args.op
    r = {"op": op}
    try:
        reported_raw = args.reported
        if isinstance(reported_raw, str) and reported_raw.strip().endswith("%"):
            reported_raw = reported_raw.strip().rstrip("%").strip()
        if op == "percent-change":
            old, new = float(args.old), float(args.new)
            if old == 0:
                raise C.ValidationError("old value is 0; relative percent change is undefined")
            r.update(absolute_change=new - old, percent_change=(new - old) / abs(old) * 100,
                     reported=float(reported_raw) if reported_raw is not None else None,
                     match=(abs(((new - old) / abs(old) * 100) - float(reported_raw)) < 0.05
                            if reported_raw is not None else None))
        elif op == "percentage-points":
            a, b = float(args.old), float(args.new)
            r.update(percentage_points=b - a, percent_relative=((b - a) / a * 100 if a else None))
        elif op == "ratio":
            a, b = float(args.old), float(args.new)
            r.update(ratio=a / b if b else None, inverse=b / a if a else None)
        elif op == "percent-of":
            part, whole = float(args.old), float(args.new)
            r.update(percent=(part / whole * 100 if whole else None), part=part, whole=whole)
        elif op == "unit-convert":
            v = float(args.old)
            conv = convert_unit(v, args.from_unit, args.to_unit)
            r.update(input=v, from_unit=args.from_unit, to_unit=args.to_unit, converted=conv)
        elif op == "proportion-z":
            x1, n1, x2, n2 = float(args.x1), float(args.n1), float(args.x2), float(args.n2)
            if min(n1, n2) <= 0:
                raise C.ValidationError("sample sizes must be positive")
            p1, p2 = x1 / n1, x2 / n2
            pooled = (x1 + x2) / (n1 + n2)
            se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
            z = (p1 - p2) / se if se else None
            diff = p1 - p2
            se_unpooled = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
            r.update(p1=round(p1, 4), p2=round(p2, 4), difference=round(diff, 4),
                     se=round(se_unpooled, 4), z=round(z, 4) if z is not None else None)
        else:
            raise C.ValidationError(f"Unknown op {op}")
    except ValueError:
        raise C.ValidationError("Numeric argument expected")
    return r

UNIT_FACTORS = {  # to base unit (m, g, s, K, mol, Pa, J, Hz)
    "km": ("m", 1000), "cm": ("m", 0.01), "mm": ("m", 0.001), "m": ("m", 1),
    "kg": ("g", 1000), "g": ("g", 1), "mg": ("g", 0.001), "ug": ("g", 1e-6), "µg": ("g", 1e-6), "ng": ("g", 1e-9),
    "L": ("ml", 1000), "l": ("ml", 1000), "ml": ("ml", 1), "mL": ("ml", 1),
    "kJ": ("J", 1000), "J": ("J", 1), "kcal": ("J", 4184),
    "kHz": ("Hz", 1000), "MHz": ("Hz", 1e6), "Hz": ("Hz", 1),
    "kPa": ("Pa", 1000), "GPa": ("Pa", 1e9), "Pa": ("Pa", 1), "mmHg": ("Pa", 133.322),
    "mmol": ("mol", 0.001), "mol": ("mol", 1),
}

def convert_unit(v, src, dst):
    if src == dst:
        return v
    # temperature special cases
    temps = {"°C": "C", "C": "C", "°F": "F", "F": "F", "K": "K"}
    if src in temps and dst in temps:
        c = {"C": v, "F": (v - 32) * 5 / 9, "K": v - 273.15}[temps[src]]
        return {"C": c, "F": c * 9 / 5 + 32, "K": c + 273.15}[temps[dst]]
    if src not in UNIT_FACTORS or dst not in UNIT_FACTORS:
        raise C.ValidationError(f"No conversion factor for {src} -> {dst} (supported: {sorted(UNIT_FACTORS)} + temperatures)")
    base_src, f_src = UNIT_FACTORS[src]
    base_dst, f_dst = UNIT_FACTORS[dst]
    if base_src != base_dst:
        raise C.ValidationError(f"Incompatible quantities: {src} ({base_src}) vs {dst} ({base_dst})")
    return v * f_src / f_dst

def cmd_audit_stat(args):
    with open(args.input, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    checks = []
    design = (data.get("design") or "unknown").lower()
    reported_p = data.get("p_value")
    ci_lo, ci_hi = data.get("ci_low"), data.get("ci_high")
    effect = data.get("effect")
    n = data.get("n")
    checks.append({"check": "sample_size_reported", "pass": bool(n), "value": n})
    checks.append({"check": "uncertainty_reported", "pass": (ci_lo is not None and ci_hi is not None) or data.get("sd") or data.get("se") is not None})
    checks.append({"check": "effect_size_reported", "pass": effect is not None})
    checks.append({"check": "significance_threshold", "pass": reported_p is not None and 0 <= float(reported_p) <= 1})
    if ci_lo is not None and ci_hi is not None and effect is not None:
        contains = float(ci_lo) <= float(effect) <= float(ci_hi)
        checks.append({"check": "ci_contains_point_estimate", "pass": bool(contains)})
    if design in ("observational", "cohort", "cross-sectional", "case-control"):
        checks.append({"check": "causal_language_review",
                       "pass": None,
                       "note": "Observational design: causal verbs in the text require explicit causal-assumption review (confounding, selection, measurement)."})
    if data.get("multiple_comparisons") and not data.get("adjusted"):
        checks.append({"check": "multiple_testing", "pass": False,
                       "note": "Multiple comparisons flagged without adjustment (Bonferroni/FDR); interpret p-values accordingly."})
    if data.get("correlation_reported") and re.search(r"\b(caus|effect|leads? to|prevent)\b", json.dumps(data), re.I):
        checks.append({"check": "correlation_causation", "pass": False,
                       "note": "Causal wording alongside correlation results; reword or justify."})
    failed = [c for c in checks if c.get("pass") is False]
    return {"design": design, "checks": checks, "failed": len(failed),
            "verdict": "review-required" if failed else "no-blocking-flags",
            "note": "This audit flags reporting gaps; it does not replace a statistician."}

def cmd_chart_text(args):
    with open(args.chart, "r", encoding="utf-8") as fh:
        chart = json.load(fh)
    chart_vals = list(chart.get("values", []))
    for row in chart.get("series", []) or []:
        chart_vals.extend(row.get("values", []) if isinstance(row, dict) else [])
    if not chart_vals:
        if chart.get("csv") and os.path.isfile(chart["csv"]):
            with open(chart["csv"], "r", encoding="utf-8") as fh:
                for line in fh:
                    for cell in line.split(","):
                        try:
                            chart_vals.append(float(cell))
                        except ValueError:
                            pass
    with open(args.text, "r", encoding="utf-8") as fh:
        text = fh.read()
    nums = [float(n) for n in re.findall(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text)]
    scale = args.tolerance
    matched, unmatched = [], []
    for n in nums:
        hit = next((v for v in chart_vals if abs(float(v) - n) <= scale * max(1.0, abs(float(v)))), None)
        (matched if hit is not None else unmatched).append(n)
    return {"chart_values_loaded": len(chart_vals), "numbers_in_text": len(nums),
            "matched": matched, "not_in_chart": unmatched,
            "ok": not unmatched,
            "note": f"Tolerance ±{scale * 100:.1f}%. Axes, units and error bars in the figure itself are not parseable from JSON — verify visually."}

def cmd_chronology(args):
    with open(args.input, "r", encoding="utf-8") as fh:
        events = json.load(fh)
    if isinstance(events, dict):
        events = events.get("events", [])
    if not isinstance(events, list) or len(events) < 2:
        raise C.ValidationError("Provide a JSON list (or {'events': [...]}) of {label, date, kind?}")
    parsed = []
    problems = []
    for e in events:
        iso, uncertain = C.parse_date(e.get("date", ""))
        parsed.append({**e, "iso": iso, "uncertain": uncertain})
        if not iso:
            problems.append({"label": e.get("label"), "issue": "unparseable date"})
    for i in range(1, len(parsed)):
        prev, cur = parsed[i - 1], parsed[i]
        if prev.get("iso") and cur.get("iso") and cur["iso"] < prev["iso"]:
            problems.append({"label": cur.get("label"),
                             "issue": f"sequence impossible: {cur.get('iso')} precedes {prev.get('iso')} ({prev.get('label')})"})
    for e in parsed:
        if e.get("kind") == "reporting" and e.get("event_date"):
            ed, _ = C.parse_date(e["event_date"])
            if ed and e.get("iso") and ed > e["iso"]:
                problems.append({"label": e.get("label"),
                                 "issue": "reporting date before event date"})
    anachronisms = [e["label"] for e in parsed
                    if e.get("iso") and e.get("source_published")
                    and str(e["source_published"]) > e["iso"]]
    return {"events": parsed, "problems": problems, "anachronism_flags": anachronisms,
            "ok": not problems and not anachronisms}

def _norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))

def cmd_meta_analysis(args):
    with open(args.input, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    studies = data.get("studies", [])
    if not studies:
        raise C.ValidationError("Input JSON must contain 'studies': [{n1,n2?,mean1,mean2?,sd1,sd2?,...}]")
    out = []
    for s in studies:
        rec = {"id": s.get("id") or s.get("study")}
        m = (s.get("measure") or "md").lower()
        try:
            if m == "md":  # mean difference
                d = float(s["mean1"]) - float(s["mean2"])
                se = math.sqrt(float(s["sd1"]) ** 2 / float(s["n1"]) + float(s["sd2"]) ** 2 / float(s["n2"]))
                rec.update(effect=round(d, 4), se=round(se, 4), measure="md")
            elif m == "smd":  # Cohen's d
                pooled = math.sqrt(((float(s["n1"]) - 1) * float(s["sd1"]) ** 2 + (float(s["n2"]) - 1) * float(s["sd2"]) ** 2)
                                   / (float(s["n1"]) + float(s["n2"]) - 2))
                d = (float(s["mean1"]) - float(s["mean2"])) / pooled
                rec.update(effect=round(d, 4), measure="smd",
                           se=round(math.sqrt((float(s["n1"]) + float(s["n2"])) / (float(s["n1"]) * float(s["n2"])) + d ** 2 / (2 * (float(s["n1"]) + float(s["n2"])))), 4))
            elif m == "orr":  # odds ratio
                a, b, c, d_ = float(s["a"]), float(s["b"]), float(s["c"]), float(s["d"])
                if min(a, b, c, d_) == 0:
                    a, b, c, d_ = a + 0.5, b + 0.5, c + 0.5, d_ + 0.5  # Haldane correction
                    rec["correction"] = "Haldane 0.5 added"
                OR = (a * d_) / (b * c)
                rec.update(effect=round(math.log(OR), 4), odds_ratio=round(OR, 4),
                           se=round(math.sqrt(1 / a + 1 / b + 1 / c + 1 / d_), 4), measure="log-or")
            elif m == "rr":  # risk ratio
                a, n1, c, n2 = float(s["a"]), float(s["n1"]), float(s["c"]), float(s["n2"])
                p1, p2 = a / n1, c / n2
                rr = p1 / p2 if p2 else None
                rec.update(effect=round(math.log(rr), 4) if rr else None, risk_ratio=round(rr, 4) if rr else None,
                           se=round(math.sqrt((1 - p1) / (p1 * n1) + (1 - p2) / (p2 * n2)), 4) if rr else None, measure="log-rr")
            else:
                rec["error"] = f"unknown measure {m}"
        except (KeyError, ValueError, ZeroDivisionError) as exc:
            rec["error"] = f"could not compute: {exc}"
        if rec.get("se"):
            rec["weight_iv_fixed"] = round(1 / rec["se"] ** 2, 4)
        out.append(rec)
    ok = [r for r in out if "error" not in r and r.get("se")]
    pooled_effect = None
    if ok:
        w = sum(1 / r["se"] ** 2 for r in ok)
        pooled_effect = sum(r["effect"] / r["se"] ** 2 for r in ok) / w
    return {"studies": out, "studies_computed": len(ok),
            "pooled_effect_fixed_effect": round(pooled_effect, 4) if pooled_effect is not None else None,
            "heterogeneity": "not computed (needs I²/Cochran Q; provide study-level variances) — pre-analysis only",
            "note": "Preparation aid only. Fixed-effect pooling shown; random-effects, I², subgroup and sensitivity analyses are required before publication.",
            "review_required": True}

def cmd_kappa(args):
    with open(args.input, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "matrix" in data:
        matrix = data["matrix"]
        n_raters = len(matrix[0]) if matrix else 0
        labels = data.get("labels") or sorted({c for row in matrix for c in row})
    else:  # two raters as two lists
        r1, r2 = data["rater1"], data["rater2"]
        if len(r1) != len(r2):
            raise C.ValidationError("rater1 and rater2 must have equal length")
        labels = data.get("labels") or sorted(set(r1) | set(r2))
        idx = {l: i for i, l in enumerate(labels)}
        matrix = [[r1[i], r2[i]] for i in range(len(r1))]
    n = len(matrix)
    if n == 0:
        raise C.ValidationError("no ratings provided")
    observed_agree = sum(1 for row in matrix if len(set(row)) == 1) / n
    # Fleiss' kappa
    n_raters = len(matrix[0])
    counts = [[row.count(l) for l in labels] for row in matrix]
    P_i = [(sum(c * c for c in row_counts) - n_raters) / (n_raters * (n_raters - 1)) for row_counts in counts]
    P_bar = sum(P_i) / n
    p_j = [sum(row_counts[j] for row_counts in counts) / (n * n_raters) for j in range(len(labels))]
    P_e = sum(p * p for p in p_j)
    kappa = (P_bar - P_e) / (1 - P_e) if P_e != 1 else 1.0
    if kappa < 0.2:
        interp = "poor"
    elif kappa < 0.4:
        interp = "fair"
    elif kappa < 0.6:
        interp = "moderate"
    elif kappa < 0.8:
        interp = "substantial"
    else:
        interp = "almost perfect"
    return {"ratings": n, "raters": n_raters, "observed_agreement": round(observed_agree, 4),
            "fleiss_kappa": round(kappa, 4), "interpretation": interp,
            "note": "Interpretation bands: Landis & Koch (1977). Report CIs in publication-grade work."}

COMMANDS = {
    "extract": cmd_extract, "recheck": cmd_recheck, "audit-stat": cmd_audit_stat,
    "chart-text": cmd_chart_text, "chronology": cmd_chronology,
    "meta-analysis": cmd_meta_analysis, "kappa": cmd_kappa,
}

def build_parser(prog="rwt numbers"):
    p = argparse.ArgumentParser(prog=prog, description="Quantitative verification engines.")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("extract", help="Extract values, units, ranges, n=, percentages and uncertainties")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("recheck", help="Recompute percent-change, percentage-points, ratios, conversions, proportion z-tests")
    sp.add_argument("--op", required=True,
                    choices=["percent-change", "percentage-points", "ratio", "percent-of", "unit-convert", "proportion-z"])
    sp.add_argument("--old", default="0"); sp.add_argument("--new", default="0")
    sp.add_argument("--reported", default=None)
    sp.add_argument("--from-unit", default=None); sp.add_argument("--to-unit", default=None)
    sp.add_argument("--x1", default="0"); sp.add_argument("--n1", default="1")
    sp.add_argument("--x2", default="0"); sp.add_argument("--n2", default="1")
    sp.set_defaults(func=cmd_recheck)

    sp = sub.add_parser("audit-stat", help="Flag reporting gaps in a reported statistical result (JSON)")
    sp.add_argument("--input", required=True); sp.set_defaults(func=cmd_audit_stat)

    sp = sub.add_parser("chart-text", help="Check prose numbers against provided chart values (JSON or CSV)")
    sp.add_argument("--chart", required=True, help="JSON {values:[...], csv?} or {series:[{values:[...]}]}")
    sp.add_argument("--text", required=True); sp.add_argument("--tolerance", type=float, default=0.02)
    sp.set_defaults(func=cmd_chart_text)

    sp = sub.add_parser("chronology", help="Detect impossible sequences and event/reporting date conflicts")
    sp.add_argument("--input", required=True, help="JSON list of {label, date, kind?, source_published?}")
    sp.set_defaults(func=cmd_chronology)

    sp = sub.add_parser("meta-analysis", help="Prepare effect sizes (MD/SMD/OR/RR) and fixed-effect pooling")
    sp.add_argument("--input", required=True, help="JSON {studies:[...]}"); sp.set_defaults(func=cmd_meta_analysis)

    sp = sub.add_parser("kappa", help="Fleiss' kappa for inter-rater / inter-coder agreement")
    sp.add_argument("--input", required=True, help="JSON {rater1:[...], rater2:[...]} or {matrix:[[...]], labels?}")
    sp.set_defaults(func=cmd_kappa)
    return p

def run(argv):
    parser = build_parser(prog="rwt numbers")
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
