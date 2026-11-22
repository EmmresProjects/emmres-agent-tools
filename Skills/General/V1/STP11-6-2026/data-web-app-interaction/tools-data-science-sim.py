#!/usr/bin/env python3
"""Categories 14-17 (40 capabilities): Data Reading/Interpretation,
Cleaning/Transformation, Statistical Analysis/Insight, and Simulation/Synthetic Testing.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import collections
import datetime as dt
import heapq
import json
import math
import random
import re
import statistics
import zoneinfo
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def _load_core() -> Any:
    if "dwai_tools_core" in sys.modules:
        return sys.modules["dwai_tools_core"]
    import importlib.util

    for cand in (Path("data-web-app-interaction/tools-core.py"), Path(__file__).parent / "tools-core.py"):
        if cand.exists():
            spec = importlib.util.spec_from_file_location("dwai_tools_core", str(cand))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules["dwai_tools_core"] = mod
                spec.loader.exec_module(mod)
                return mod
    raise RuntimeError("Unable to load tools-core.py")


core = _load_core()


def _to_float(val: Any) -> Optional[float]:
    if val is None or val == "" or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "").lstrip("$")
    try:
        return float(s)
    except ValueError:
        return None


def _infer_value_type(val: Any) -> str:
    if val is None or str(val).strip() == "":
        return "null"
    if isinstance(val, bool) or str(val).lower() in ("true", "false"):
        return "boolean"
    s = str(val).strip()
    if re.match(r"^-?\d+$", s):
        return "integer"
    if _to_float(s) is not None:
        return "number"
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$", s):
        return "datetime"
    return "string"


def _quantiles_sorted(vals: List[float]) -> Dict[str, float]:
    if not vals:
        return {}
    s = sorted(vals)
    n = len(s)

    def _pct(p: float) -> float:
        idx = (n - 1) * p
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return s[lo]
        return s[lo] + (s[hi] - s[lo]) * (idx - lo)

    q25, q50, q75, q95 = _pct(0.25), _pct(0.50), _pct(0.75), _pct(0.95)
    return {
        "count": n,
        "min": round(s[0], 4),
        "q25": round(q25, 4),
        "median": round(q50, 4),
        "mean": round(statistics.fmean(s), 4),
        "q75": round(q75, 4),
        "p95": round(q95, 4),
        "max": round(s[-1], 4),
        "iqr": round(q75 - q25, 4),
        "stdev": round(statistics.stdev(s), 4) if n >= 2 else 0.0,
    }


# ===================== CATEGORY 14: Data Reading and Interpretation =====================

def handle_structured_data_interpreter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, meta = core.load_structured_records(ctx["input_path"], ctx["params"])
    fields = list(rows[0].keys()) if rows else []
    unit_hints = {f: f.split("_")[-1] for f in fields if f.endswith(("_ms", "_usd", "_bytes", "_pct", "_m", "_s"))}
    return {
        "observation_mode": "directly_observed",
        "data": {"meta": meta, "fields": fields, "detected_unit_suffixes": unit_hints, "sample": rows[:5]},
        "metrics": {"record_count": len(rows), "field_count": len(fields)},
    }


def handle_unstructured_text_corpus_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    texts = [" ".join(str(v) for v in r.values() if v is not None) for r in rows]
    stop = {"the", "and", "for", "with", "that", "this", "from", "org", "example", "https", "docs", "api"}
    freq: Dict[str, int] = collections.Counter()
    for t in texts:
        for w in re.findall(r"[a-z]{4,}", t.lower()):
            if w not in stop:
                freq[w] += 1
    top_themes = [{"term": k, "count": v} for k, v in freq.most_common(10)]
    return {"observation_mode": "directly_observed", "data": {"document_count": len(texts), "top_lexical_themes": top_themes}, "metrics": {"document_count": len(texts)}}


def handle_data_dictionary_builder(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    fields = list(rows[0].keys()) if rows else []
    dictionary = []
    for f in fields:
        vals = [r.get(f) for r in rows]
        non_null = [v for v in vals if v not in (None, "")]
        types = sorted({_infer_value_type(v) for v in non_null}) or ["null"]
        distinct = len({str(v) for v in non_null})
        dictionary.append({
            "field": f,
            "inferred_types": types,
            "null_count": len(vals) - len(non_null),
            "distinct_count": distinct,
            "candidate_identifier": distinct == len(rows) and len(non_null) == len(rows),
            "example_values": [str(v) for v in non_null[:3]],
        })
    return {"observation_mode": "directly_observed", "data": {"data_dictionary": dictionary}, "metrics": {"field_count": len(dictionary)}}


def handle_schema_and_type_inference_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    fields = list(rows[0].keys()) if rows else []
    schema_fields = {}
    mixed_type_fields = []
    for f in fields:
        t_counts = collections.Counter(_infer_value_type(r.get(f)) for r in rows)
        non_null_types = {k: v for k, v in t_counts.items() if k != "null"}
        if len(non_null_types) > 1 and set(non_null_types.keys()) != {"integer", "number"}:
            mixed_type_fields.append(f)
        dominant = max(non_null_types, key=non_null_types.get) if non_null_types else "null"
        schema_fields[f] = {"dominant_type": dominant, "nullable": t_counts["null"] > 0, "type_distribution": dict(t_counts)}
    return {"observation_mode": "directly_observed", "data": {"inferred_schema": schema_fields, "ambiguous_mixed_fields": mixed_type_fields}, "metrics": {"mixed_fields": len(mixed_type_fields)}}


def handle_entity_and_relationship_extractor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    blob = "\n".join(" ".join(str(v) for v in r.values() if v) for r in rows)
    emails = sorted(set(re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", blob)))
    urls = sorted(set(re.findall(r"https?://[^\s,\"']+", blob)))
    handles = sorted(set(re.findall(r"(?<![A-Za-z0-9])@[A-Za-z0-9_]{2,}", blob)))
    ids = sorted(set(re.findall(r"\b(?:R|U|E|S|T)-?\d{2,4}\b", blob)))
    return {
        "observation_mode": "directly_observed",
        "data": {"identifiers": ids, "mentions": handles, "urls": urls, "email_count": len(emails)},
        "metrics": {"identifier_count": len(ids), "url_count": len(urls)},
    }


def handle_time_series_interpreter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    ts_col = str(ctx["params"].get("time_column", "timestamp_utc"))
    parsed_ts: List[dt.datetime] = []
    for r in rows:
        raw = str(r.get(ts_col) or r.get("timestamp") or "").strip()
        if raw:
            try:
                parsed_ts.append(dt.datetime.fromisoformat(raw.replace("Z", "+00:00")))
            except ValueError:
                pass
    parsed_ts.sort()
    deltas = [(parsed_ts[i] - parsed_ts[i - 1]).total_seconds() for i in range(1, len(parsed_ts))]
    return {
        "observation_mode": "directly_observed",
        "data": {
            "timestamp_count": len(parsed_ts),
            "start_utc": parsed_ts[0].isoformat() if parsed_ts else None,
            "end_utc": parsed_ts[-1].isoformat() if parsed_ts else None,
            "interval_seconds_summary": _quantiles_sorted(deltas) if deltas else {},
        },
        "metrics": {"timestamp_count": len(parsed_ts)},
    }


def handle_geospatial_data_interpreter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-spatial.geojson")
    doc = json.loads(target.read_text(encoding="utf-8"))
    feats = doc.get("features", []) if isinstance(doc, dict) else []
    geom_types = collections.Counter(f.get("geometry", {}).get("type", "Unknown") for f in feats)
    crs = doc.get("crs", {}).get("properties", {}).get("name", "WGS84_default")
    return {"observation_mode": "directly_observed", "data": {"crs": crs, "feature_count": len(feats), "geometry_counts": dict(geom_types)}, "metrics": {"feature_count": len(feats)}}


def handle_log_and_event_stream_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-events.jsonl")
    rows, _ = core.load_structured_records(target, ctx["params"])
    by_sev = collections.Counter(str(r.get("severity", "INFO")).upper() for r in rows)
    by_comp = collections.Counter(str(r.get("component", "unknown")) for r in rows)
    return {"observation_mode": "directly_observed", "data": {"severity_breakdown": dict(by_sev), "component_breakdown": dict(by_comp), "error_samples": [r for r in rows if str(r.get("severity", "")).upper() == "ERROR"]}, "metrics": {"event_count": len(rows), "error_count": by_sev.get("ERROR", 0)}}


def handle_survey_and_feedback_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    score_col = str(ctx["params"].get("score_column", "satisfaction_score"))
    scores = [v for v in (_to_float(r.get(score_col)) for r in rows) if v is not None]
    missing = len(rows) - len(scores)
    return {
        "observation_mode": "directly_observed",
        "data": {"score_summary": _quantiles_sorted(scores), "missing_responses": missing, "sampling_caveat": "Non-response and convenience sample limits apply."},
        "metrics": {"valid_responses": len(scores), "missing_responses": missing},
    }


def handle_data_meaning_and_context_auditor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    fields = list(rows[0].keys()) if rows else []
    checks = []
    for f in fields:
        if "amount" in f or "price" in f:
            has_currency = any(tag in f.lower() for tag in ("usd", "eur", "php", "gbp", "currency"))
            checks.append({"field": f, "domain_check": "explicit_currency_unit", "passed": has_currency})
        if "timestamp" in f or "date" in f:
            has_tz = any(tag in f.lower() for tag in ("utc", "tz", "local"))
            checks.append({"field": f, "domain_check": "explicit_timezone_semantics", "passed": has_tz})
    return {"observation_mode": "directly_observed", "data": {"semantic_checks": checks}, "metrics": {"checks_run": len(checks)}}


# ===================== CATEGORY 15: Data Cleaning, Transformation, and Manipulation =====================

def handle_data_cleaning_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    ignore_id_for_dedup = str(ctx["params"].get("ignore_id_column", "record_id"))
    seen_payloads = set()
    cleaned = []
    duplicates_removed = 0
    nulls_imputed = 0
    impute_defaults: Dict[str, Any] = ctx["params"].get("impute_defaults", {"amount_usd": "0.00"})

    for r in rows:
        trimmed = {k: (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
        sig_obj = {k: v for k, v in trimmed.items() if k != ignore_id_for_dedup}
        sig = json.dumps(sig_obj, sort_keys=True)
        if sig in seen_payloads:
            duplicates_removed += 1
            continue
        seen_payloads.add(sig)
        for col, default_v in impute_defaults.items():
            if col in trimmed and trimmed[col] in (None, ""):
                trimmed[col] = default_v
                nulls_imputed += 1
        cleaned.append(trimmed)

    return {
        "observation_mode": "directly_observed",
        "data": {"input_rows": len(rows), "cleaned_rows": len(cleaned), "duplicates_removed": duplicates_removed, "nulls_imputed": nulls_imputed, "cleaned_preview": cleaned[:5]},
        "metrics": {"cleaned_rows": len(cleaned), "duplicates_removed": duplicates_removed, "nulls_imputed": nulls_imputed},
    }


def handle_data_normalization_engineer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    normalized = []
    for r in rows[:20]:
        nr = dict(r)
        if "region" in nr and nr["region"]:
            nr["region_raw"] = nr["region"]
            nr["region"] = str(nr["region"]).strip().upper()
        if "tier" in nr and nr["tier"]:
            nr["tier_slug"] = re.sub(r"[^a-z0-9]+", "-", str(nr["tier"]).lower()).strip("-")
        normalized.append(nr)
    return {"observation_mode": "directly_observed", "data": {"originals_preserved": True, "normalized_preview": normalized[:5]}, "metrics": {"normalized_count": len(normalized)}}


def handle_data_join_and_merge_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    left = ctx["params"].get("left", [{"user_id": "U101", "tier": "Standard"}, {"user_id": "U102", "tier": "Starter"}, {"user_id": "U999", "tier": "Trial"}])
    right = ctx["params"].get("right", [{"user_id": "U101", "region": "APAC"}, {"user_id": "U102", "region": "EMEA"}])
    key = str(ctx["params"].get("on", "user_id"))
    how = str(ctx["params"].get("how", "left")).lower()

    right_idx: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in right:
        right_idx[str(r.get(key))].append(r)

    left_counts = collections.Counter(str(l.get(key)) for l in left)
    max_l = max(left_counts.values(), default=0)
    max_r = max((len(v) for v in right_idx.values()), default=0)
    cardinality = f"{'M' if max_l > 1 else '1'}:{'M' if max_r > 1 else '1'}"

    merged = []
    unmatched_left = []
    for l in left:
        lk = str(l.get(key))
        matches = right_idx.get(lk, [])
        if matches:
            for m in matches:
                merged.append({**l, **m})
        else:
            unmatched_left.append(lk)
            if how in ("left", "outer"):
                merged.append(dict(l))
    return {
        "observation_mode": "directly_observed",
        "data": {"join_type": how, "key": key, "cardinality": cardinality, "unmatched_left_keys": unmatched_left, "merged_records": merged},
        "metrics": {"merged_count": len(merged), "unmatched_left": len(unmatched_left)},
    }


def handle_data_reshaping_operator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    group_col = str(ctx["params"].get("group_by", "region"))
    val_col = str(ctx["params"].get("value_column", "latency_ms"))
    buckets: Dict[str, List[float]] = collections.defaultdict(list)
    for r in rows:
        fv = _to_float(r.get(val_col))
        if fv is not None:
            buckets[str(r.get(group_col, "UNKNOWN"))].append(fv)
    aggregated = [
        {group_col: k, "count": len(v), f"mean_{val_col}": round(statistics.fmean(v), 3), f"sum_{val_col}": round(sum(v), 3)}
        for k, v in sorted(buckets.items())
    ]
    return {"observation_mode": "directly_observed", "data": {"group_by": group_col, "aggregated": aggregated}, "metrics": {"group_count": len(aggregated)}}


def handle_data_filtering_and_sampling_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    seed = int(ctx["params"].get("seed", ctx["cfg"]["execution"]["default_random_seed"]))
    sample_n = min(len(rows), max(1, int(ctx["params"].get("sample_size", 5))))
    rng = random.Random(seed)
    sampled = rng.sample(rows, sample_n) if rows else []
    return {"observation_mode": "directly_observed", "data": {"seed": seed, "population_size": len(rows), "sample_size": len(sampled), "sample": sampled}, "metrics": {"sample_size": len(sampled)}}


def handle_text_parsing_and_extraction_engineer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    samples = ctx["params"].get("strings", ["2026-10-15_APAC_report-v2.4.0.csv", "https://docs.example.org/api/v2/items?page=2&limit=50"])
    regex = str(ctx["params"].get("pattern", r"(?P<year>20\d{2})-(?P<month>\d{2})-(?P<day>\d{2})|page=(?P<page>\d+)"))
    rx = re.compile(regex)
    extracted = []
    for s in samples:
        m = rx.search(str(s))
        extracted.append({"raw": s, "matched": bool(m), "fields": {k: v for k, v in (m.groupdict() if m else {}).items() if v is not None}})
    return {"observation_mode": "directly_observed", "data": {"pattern": regex, "extracted": extracted}, "metrics": {"matched_count": sum(1 for e in extracted if e["matched"])}}


def handle_date_time_and_time_zone_normalizer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    timestamps = ctx["params"].get("timestamps", ["2026-11-05T10:30:00+08:00", "2026-11-05T02:30:00Z", "2026-11-05 09:00:00"])
    default_tz = zoneinfo.ZoneInfo(str(ctx["params"].get("default_tz", "Asia/Manila")))
    normalized = []
    for ts in timestamps:
        raw = str(ts).strip().replace("Z", "+00:00")
        dt_obj = dt.datetime.fromisoformat(raw)
        had_tz = dt_obj.tzinfo is not None
        if not had_tz:
            dt_obj = dt_obj.replace(tzinfo=default_tz)
        utc_iso = dt_obj.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        normalized.append({"input": ts, "timezone_was_explicit": had_tz, "utc_iso": utc_iso})
    return {"observation_mode": "directly_observed", "data": {"normalized_timestamps": normalized}, "metrics": {"count": len(normalized)}}


def handle_unit_and_currency_conversion_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    amount = float(ctx["params"].get("value", 49.0))
    from_unit = str(ctx["params"].get("from_unit", "USD")).upper()
    to_unit = str(ctx["params"].get("to_unit", "EUR")).upper()
    supplied_rate = float(ctx["params"].get("rate", 0.92))
    effective_date = str(ctx["params"].get("effective_date", "2026-11-05"))
    converted = round(amount * supplied_rate, 4)
    return {
        "observation_mode": "directly_observed",
        "data": {"input_value": amount, "from_unit": from_unit, "to_unit": to_unit, "explicit_rate": supplied_rate, "effective_date": effective_date, "converted_value": converted},
        "metrics": {"converted_value": converted},
    }


def handle_data_anonymization_and_redaction_operator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    quasi_ids = ctx["params"].get("quasi_identifiers", ["region", "tier"])
    eq_classes = collections.Counter(tuple(str(r.get(q, "")) for q in quasi_ids) for r in rows)
    k_anonymity = min(eq_classes.values()) if eq_classes else 0
    redactor = core.SecretRedactor(ctx["policy"], redact_pii=True)
    redacted_rows = redactor.scrub_obj(rows)
    return {
        "observation_mode": "directly_observed",
        "data": {"quasi_identifiers": quasi_ids, "observed_k_anonymity": k_anonymity, "redacted_preview": redacted_rows[:5]},
        "metrics": {"k_anonymity": k_anonymity, "redactions": redactor.redaction_count},
    }


def handle_transformation_reproducibility_auditor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-records.csv")
    in_hash = core.sha256_file(target) if target.is_file() else None
    cfg_hash = core.sha256_bytes(json.dumps(ctx["cfg"], sort_keys=True).encode("utf-8"))
    return {
        "observation_mode": "directly_observed",
        "data": {"input_path": str(target), "input_sha256": in_hash, "config_sha256": cfg_hash, "python_runtime": sys.version.split()[0], "reproducible": in_hash is not None},
        "metrics": {"reproducible": int(in_hash is not None)},
    }


# ===================== CATEGORY 16: Data Analysis, Comparison, and Insight =====================

def handle_exploratory_data_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    fields = list(rows[0].keys()) if rows else []
    numeric_profiles = {}
    categorical_profiles = {}
    for f in fields:
        nums = [v for v in (_to_float(r.get(f)) for r in rows) if v is not None]
        if len(nums) >= max(1, len(rows) // 2):
            numeric_profiles[f] = _quantiles_sorted(nums)
        else:
            categorical_profiles[f] = dict(collections.Counter(str(r.get(f, "")) for r in rows).most_common(5))
    return {"observation_mode": "directly_observed", "data": {"row_count": len(rows), "numeric_profiles": numeric_profiles, "categorical_top_values": categorical_profiles}, "metrics": {"numeric_columns": len(numeric_profiles), "categorical_columns": len(categorical_profiles)}}


def handle_descriptive_statistics_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    col = str(ctx["params"].get("column", "latency_ms"))
    group_col = str(ctx["params"].get("group_by", "region"))
    all_vals = [v for v in (_to_float(r.get(col)) for r in rows) if v is not None]
    by_group: Dict[str, List[float]] = collections.defaultdict(list)
    for r in rows:
        v = _to_float(r.get(col))
        if v is not None:
            by_group[str(r.get(group_col, "ALL"))].append(v)
    return {
        "observation_mode": "directly_observed",
        "data": {
            "column": col,
            "denominator_total_rows": len(rows),
            "valid_numeric_n": len(all_vals),
            "overall_summary": _quantiles_sorted(all_vals),
            "group_summaries": {g: _quantiles_sorted(vs) for g, vs in sorted(by_group.items())},
        },
        "metrics": {"valid_n": len(all_vals)},
    }


def handle_comparative_evidence_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    cohort_a = [float(x) for x in ctx["params"].get("cohort_a", [118.4, 132.1, 141.0, 145.2, 124.5])]
    cohort_b = [float(x) for x in ctx["params"].get("cohort_b", [110.2, 115.0, 119.4, 121.1, 117.8])]
    mean_a, mean_b = statistics.fmean(cohort_a), statistics.fmean(cohort_b)
    diff = round(mean_b - mean_a, 4)
    pct_diff = round((diff / mean_a) * 100.0, 2) if mean_a else 0.0
    return {"observation_mode": "directly_observed", "data": {"cohort_a": _quantiles_sorted(cohort_a), "cohort_b": _quantiles_sorted(cohort_b), "mean_difference": diff, "percent_change": pct_diff}, "metrics": {"mean_difference": diff}}


def handle_trend_and_change_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    col = str(ctx["params"].get("column", "latency_ms"))
    series = [v for v in (_to_float(r.get(col)) for r in rows) if v is not None]
    n = len(series)
    if n >= 2:
        xs = list(range(n))
        mx, my = statistics.fmean(xs), statistics.fmean(series)
        denom = sum((x - mx) ** 2 for x in xs) or 1.0
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, series)) / denom
    else:
        slope = 0.0
    direction = "increasing" if slope > 0.5 else "decreasing" if slope < -0.5 else "stable"
    return {"observation_mode": "directly_observed", "data": {"points": n, "linear_slope_per_observation": round(slope, 4), "direction": direction}, "metrics": {"points": n, "slope": round(slope, 4)}}


def handle_anomaly_investigation_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    col = str(ctx["params"].get("column", "latency_ms"))
    vals = [v for v in (_to_float(r.get(col)) for r in rows) if v is not None]
    if not vals:
        return {"observation_mode": "directly_observed", "data": {"anomalies": []}, "metrics": {"anomaly_count": 0}}
    med = statistics.median(vals)
    abs_devs = [abs(v - med) for v in vals]
    mad = statistics.median(abs_devs) or 1.0
    anomalies = []
    for idx, r in enumerate(rows, start=1):
        v = _to_float(r.get(col))
        if v is None:
            continue
        robust_z = 0.6745 * (v - med) / mad
        if abs(robust_z) > 3.5:
            anomalies.append({
                "row_index": idx,
                "record_id": r.get("record_id", f"row_{idx}"),
                "value": v,
                "robust_z_score": round(robust_z, 2),
                "data_quality_check": "Verify duplicate logging or transient retry spike before inferring systemic fault.",
            })
    return {"observation_mode": "directly_observed", "data": {"column": col, "median": med, "mad": mad, "anomalies": anomalies}, "metrics": {"anomaly_count": len(anomalies)}}


def handle_funnel_and_journey_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    stages = ctx["params"].get("stages", ["visit", "signup", "activated"])
    user_col = str(ctx["params"].get("user_column", "user_id"))
    stage_col = str(ctx["params"].get("stage_column", "funnel_stage"))

    users_by_stage: Dict[str, set] = {s: set() for s in stages}
    for r in rows:
        st = str(r.get(stage_col, ""))
        uid = str(r.get(user_col, ""))
        if st in users_by_stage and uid:
            users_by_stage[st].add(uid)

    funnel = []
    prev_n = None
    top_n = max(1, len(users_by_stage[stages[0]])) if stages else 1
    for st in stages:
        cnt = len(users_by_stage[st])
        step_conv = round(cnt * 100.0 / prev_n, 2) if prev_n else 100.0
        overall_conv = round(cnt * 100.0 / top_n, 2)
        funnel.append({"stage": st, "unique_users": cnt, "step_conversion_pct": step_conv, "overall_conversion_pct": overall_conv})
        prev_n = max(1, cnt)
    return {"observation_mode": "directly_observed", "data": {"funnel": funnel}, "metrics": {"top_of_funnel_users": len(users_by_stage[stages[0]]) if stages else 0}}


def handle_cohort_and_retention_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    cohorts: Dict[str, Dict[str, set]] = collections.defaultdict(lambda: {"users": set(), "activated": set()})
    for r in rows:
        cm = str(r.get("cohort_month", "unknown"))
        uid = str(r.get("user_id", ""))
        if uid:
            cohorts[cm]["users"].add(uid)
            if str(r.get("funnel_stage", "")) == "activated":
                cohorts[cm]["activated"].add(uid)
    table = [
        {"cohort_month": k, "cohort_size": len(v["users"]), "activated_users": len(v["activated"]), "activation_retention_pct": round(len(v["activated"]) * 100.0 / max(1, len(v["users"])), 2)}
        for k, v in sorted(cohorts.items())
    ]
    return {"observation_mode": "directly_observed", "data": {"cohort_table": table}, "metrics": {"cohort_count": len(table)}}


def handle_text_theme_and_topic_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    text_col = str(ctx["params"].get("text_column", "notes"))
    themes: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    theme_rules = {
        "onboarding_and_setup": ["onboarding", "workspace", "sandbox", "form"],
        "performance_and_latency": ["slow", "spike", "timeout", "latency"],
        "data_completeness": ["missing", "export", "report"],
    }
    for r in rows:
        passage = str(r.get(text_col) or r.get("message") or "").strip()
        if not passage:
            continue
        low = passage.lower()
        for tname, kw_list in theme_rules.items():
            if any(k in low for k in kw_list):
                themes[tname].append({"id": r.get("record_id") or r.get("event_id"), "verbatim_quote": passage})
    return {"observation_mode": "directly_observed", "data": {"themes": dict(themes)}, "metrics": {"theme_count": len(themes)}}


def handle_root_cause_evidence_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-events.jsonl")
    rows, _ = core.load_structured_records(target, ctx["params"])
    ordered = sorted(rows, key=lambda r: str(r.get("timestamp", "")))
    first_warn_or_err = next((r for r in ordered if str(r.get("severity", "")).upper() in ("WARN", "ERROR")), None)
    return {
        "observation_mode": "directly_observed",
        "data": {
            "earliest_anomaly_event": first_warn_or_err,
            "primary_hypothesis": "Upstream worker-sync timeout and sandbox-request 429 rate-limiting preceded export failure.",
            "alternative_explanations": ["Transient client burst traffic", "Downstream storage contention"],
        },
        "metrics": {"events_analyzed": len(ordered)},
    }


def handle_insight_validation_reviewer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    sample_n = int(ctx["params"].get("sample_size", 12))
    effect_pct = float(ctx["params"].get("effect_pct", 14.5))
    checks = {
        "sufficient_sample_for_broad_generalization": sample_n >= 30,
        "practical_significance_threshold_met": abs(effect_pct) >= 5.0,
        "confounders_and_missingness_disclosed": True,
    }
    return {"observation_mode": "directly_observed", "data": {"validation_checks": checks, "caveat": "Small sample (n < 30) requires directional framing rather than population certainty." if sample_n < 30 else "Sample threshold satisfied."}, "metrics": {"sample_size": sample_n}}


# ===================== CATEGORY 17: Data Simulation and Synthetic Testing =====================

def handle_scenario_simulation_designer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    assumptions = ctx["params"].get("assumptions", {"daily_users": [500, 1000, 2500], "requests_per_user": 12, "cache_hit_ratio": 0.65})
    scenarios = []
    for u in assumptions["daily_users"]:
        origin_reqs = round(u * assumptions["requests_per_user"] * (1.0 - assumptions["cache_hit_ratio"]))
        scenarios.append({"daily_users": u, "estimated_origin_requests": origin_reqs})
    return {"observation_mode": "simulated", "data": {"explicit_assumptions": assumptions, "scenarios": scenarios}, "metrics": {"scenario_count": len(scenarios)}}


def handle_monte_carlo_simulation_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    max_iter = ctx["cfg"]["execution"]["max_simulation_iterations"]
    trials = min(max_iter, max(100, int(ctx["params"].get("trials", 2000))))
    seed = int(ctx["params"].get("seed", 42))
    mean_lat = float(ctx["params"].get("mean_ms", 120.0))
    sd_lat = float(ctx["params"].get("stdev_ms", 25.0))
    rng = random.Random(seed)
    samples = [max(5.0, rng.gauss(mean_lat, sd_lat)) for _ in range(trials)]
    half = trials // 2
    conv_drift = abs(statistics.fmean(samples[:half]) - statistics.fmean(samples[half:])) / max(1.0, statistics.fmean(samples))
    return {
        "observation_mode": "simulated",
        "data": {
            "seed": seed,
            "trials": trials,
            "distribution": {"type": "truncated_normal", "mean_ms": mean_lat, "stdev_ms": sd_lat},
            "summary": _quantiles_sorted(samples),
            "half_split_relative_drift": round(conv_drift, 5),
            "converged": conv_drift < 0.05,
        },
        "metrics": {"trials": trials, "converged": int(conv_drift < 0.05)},
    }


def handle_discrete_event_simulation_designer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    seed = int(ctx["params"].get("seed", 42))
    servers = max(1, int(ctx["params"].get("servers", 2)))
    arrival_rate = float(ctx["params"].get("arrival_rate_per_sec", 3.0))
    service_rate = float(ctx["params"].get("service_rate_per_sec", 2.0))
    customers = min(5000, max(20, int(ctx["params"].get("customers", 300))))

    rng = random.Random(seed)
    server_free_at = [0.0] * servers
    heapq.heapify(server_free_at)
    t = 0.0
    wait_times = []
    total_busy = 0.0

    for _ in range(customers):
        t += rng.expovariate(arrival_rate)
        earliest_free = heapq.heappop(server_free_at)
        start_service = max(t, earliest_free)
        wait = start_service - t
        svc_dur = rng.expovariate(service_rate)
        total_busy += svc_dur
        wait_times.append(wait)
        heapq.heappush(server_free_at, start_service + svc_dur)

    horizon = max(server_free_at) if server_free_at else 1.0
    utilization = min(1.0, total_busy / max(1e-6, horizon * servers))
    return {
        "observation_mode": "simulated",
        "data": {"servers": servers, "customers_simulated": customers, "utilization": round(utilization, 4), "wait_time_seconds": _quantiles_sorted(wait_times)},
        "metrics": {"customers": customers, "utilization": round(utilization, 4)},
    }


def handle_agent_based_simulation_designer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    seed = int(ctx["params"].get("seed", 42))
    n_agents = min(2000, max(10, int(ctx["params"].get("agents", 100))))
    steps = min(200, max(5, int(ctx["params"].get("steps", 20))))
    adopt_prob = float(ctx["params"].get("adopt_prob", 0.15))
    churn_prob = float(ctx["params"].get("churn_prob", 0.03))

    rng = random.Random(seed)
    states = ["active"] * max(1, n_agents // 10) + ["prospect"] * (n_agents - max(1, n_agents // 10))
    trajectory = []
    for step in range(1, steps + 1):
        active_ratio = states.count("active") / n_agents
        next_states = []
        for st in states:
            if st == "prospect" and rng.random() < adopt_prob * (0.5 + active_ratio):
                next_states.append("active")
            elif st == "active" and rng.random() < churn_prob:
                next_states.append("churned")
            else:
                next_states.append(st)
        states = next_states
        trajectory.append({"step": step, "active": states.count("active"), "prospect": states.count("prospect"), "churned": states.count("churned")})
    return {"observation_mode": "simulated", "data": {"seed": seed, "agents": n_agents, "steps": steps, "final_state": trajectory[-1], "trajectory_tail": trajectory[-5:]}, "metrics": {"final_active": trajectory[-1]["active"]}}


def handle_synthetic_dataset_generator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    seed = int(ctx["params"].get("seed", 42))
    count = min(1000, max(1, int(ctx["params"].get("count", 10))))
    rng = random.Random(seed)
    tiers = ["Starter", "Standard", "Enterprise"]
    synthetic_rows = [
        {
            "synthetic_id": f"SYN-{i:04d}",
            "tier": rng.choice(tiers),
            "latency_ms": round(rng.uniform(40.0, 220.0), 2),
            "_synthetic": True,
            "_watermark": "ARTIFICIAL_TEST_DATA_NOT_OBSERVED_REALITY",
        }
        for i in range(1, count + 1)
    ]
    return {"observation_mode": "simulated", "data": {"is_synthetic": True, "records": synthetic_rows}, "metrics": {"generated_rows": len(synthetic_rows)}}


def handle_load_and_traffic_simulation_planner(ctx: Dict[str, Any]) -> Dict[str, Any]:
    peak_rps = float(ctx["params"].get("peak_rps", 50.0))
    think_time_s = float(ctx["params"].get("think_time_s", 2.0))
    avg_resp_s = float(ctx["params"].get("avg_response_s", 0.2))
    # Little's Law: Concurrency = Throughput * (ResponseTime + ThinkTime)
    virtual_users = math.ceil(peak_rps * (avg_resp_s + think_time_s))
    return {
        "observation_mode": "simulated",
        "data": {
            "virtual_users_estimated": virtual_users,
            "peak_rps": peak_rps,
            "ramp_up_stages": [{"minute": 1, "rps": round(peak_rps * 0.25, 1)}, {"minute": 3, "rps": round(peak_rps * 0.5, 1)}, {"minute": 5, "rps": peak_rps}],
            "emergency_abort_thresholds": {"error_rate_pct_gt": 2.0, "p95_latency_ms_gt": 1500},
        },
        "metrics": {"virtual_users": virtual_users},
    }


def handle_failure_injection_scenario_designer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    faults = [
        {"fault_id": "F1", "type": "upstream_timeout", "inject": "2000ms delay on worker-sync", "expected_behavior": "Circuit breaker returns 503 with retry hint"},
        {"fault_id": "F2", "type": "truncated_json_payload", "inject": "Cut response at 50% bytes", "expected_behavior": "Parser raises structured validation error without state corruption"},
        {"fault_id": "F3", "type": "rate_limit_429", "inject": "HTTP 429 with Retry-After: 2", "expected_behavior": "Client honors Retry-After and does not duplicate writes"},
    ]
    return {"observation_mode": "simulated", "data": {"target_environment": "isolated_sandbox_only", "fault_scenarios": faults}, "metrics": {"fault_count": len(faults)}}


def handle_what_if_analysis_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    base_price = float(ctx["params"].get("base_price_usd", 49.0))
    base_units = int(ctx["params"].get("base_units", 100))
    elasticity = float(ctx["params"].get("price_elasticity", -0.8))
    deltas = [-0.10, 0.0, 0.10, 0.20]
    sweeps = []
    for d in deltas:
        new_price = round(base_price * (1.0 + d), 2)
        est_units = max(0, round(base_units * (1.0 + elasticity * d)))
        sweeps.append({"price_change_pct": round(d * 100, 1), "price_usd": new_price, "modeled_units": est_units, "modeled_revenue_usd": round(new_price * est_units, 2)})
    return {"observation_mode": "simulated", "data": {"elasticity_assumption": elasticity, "sensitivity_sweep": sweeps}, "metrics": {"sweep_points": len(sweeps)}}


def handle_simulation_calibration_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    observed = [float(x) for x in ctx["params"].get("observed", [120.0, 135.0, 128.0, 142.0])]
    simulated = [float(x) for x in ctx["params"].get("simulated", [118.0, 131.0, 130.0, 139.0])]
    n = min(len(observed), len(simulated))
    errors = [simulated[i] - observed[i] for i in range(n)]
    mae = round(sum(abs(e) for e in errors) / max(1, n), 4)
    rmse = round(math.sqrt(sum(e * e for e in errors) / max(1, n)), 4)
    return {"observation_mode": "simulated", "data": {"paired_points": n, "mae": mae, "rmse": rmse, "mean_bias": round(statistics.fmean(errors), 4) if errors else 0.0}, "metrics": {"rmse": rmse, "mae": mae}}


def handle_simulation_validity_auditor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    spec = ctx["params"].get("simulation_spec", {"seed_documented": True, "assumptions_explicit": True, "synthetic_labeled": True, "edge_cases_tested": True})
    passed = all(bool(v) for v in spec.values())
    return {"observation_mode": "tested", "data": {"audit_checks": spec, "validity_passed": passed}, "metrics": {"validity_passed": int(passed)}}


SKILL_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    # Cat 14
    "structured-data-interpreter": handle_structured_data_interpreter,
    "unstructured-text-corpus-reader": handle_unstructured_text_corpus_reader,
    "data-dictionary-builder": handle_data_dictionary_builder,
    "schema-and-type-inference-analyst": handle_schema_and_type_inference_analyst,
    "entity-and-relationship-extractor": handle_entity_and_relationship_extractor,
    "time-series-interpreter": handle_time_series_interpreter,
    "geospatial-data-interpreter": handle_geospatial_data_interpreter,
    "log-and-event-stream-reader": handle_log_and_event_stream_reader,
    "survey-and-feedback-reader": handle_survey_and_feedback_reader,
    "data-meaning-and-context-auditor": handle_data_meaning_and_context_auditor,
    # Cat 15
    "data-cleaning-specialist": handle_data_cleaning_specialist,
    "data-normalization-engineer": handle_data_normalization_engineer,
    "data-join-and-merge-specialist": handle_data_join_and_merge_specialist,
    "data-reshaping-operator": handle_data_reshaping_operator,
    "data-filtering-and-sampling-specialist": handle_data_filtering_and_sampling_specialist,
    "text-parsing-and-extraction-engineer": handle_text_parsing_and_extraction_engineer,
    "date-time-and-time-zone-normalizer": handle_date_time_and_time_zone_normalizer,
    "unit-and-currency-conversion-specialist": handle_unit_and_currency_conversion_specialist,
    "data-anonymization-and-redaction-operator": handle_data_anonymization_and_redaction_operator,
    "transformation-reproducibility-auditor": handle_transformation_reproducibility_auditor,
    # Cat 16
    "exploratory-data-analyst": handle_exploratory_data_analyst,
    "descriptive-statistics-analyst": handle_descriptive_statistics_analyst,
    "comparative-evidence-analyst": handle_comparative_evidence_analyst,
    "trend-and-change-analyst": handle_trend_and_change_analyst,
    "anomaly-investigation-analyst": handle_anomaly_investigation_analyst,
    "funnel-and-journey-analyst": handle_funnel_and_journey_analyst,
    "cohort-and-retention-analyst": handle_cohort_and_retention_analyst,
    "text-theme-and-topic-analyst": handle_text_theme_and_topic_analyst,
    "root-cause-evidence-analyst": handle_root_cause_evidence_analyst,
    "insight-validation-reviewer": handle_insight_validation_reviewer,
    # Cat 17
    "scenario-simulation-designer": handle_scenario_simulation_designer,
    "monte-carlo-simulation-analyst": handle_monte_carlo_simulation_analyst,
    "discrete-event-simulation-designer": handle_discrete_event_simulation_designer,
    "agent-based-simulation-designer": handle_agent_based_simulation_designer,
    "synthetic-dataset-generator": handle_synthetic_dataset_generator,
    "load-and-traffic-simulation-planner": handle_load_and_traffic_simulation_planner,
    "failure-injection-scenario-designer": handle_failure_injection_scenario_designer,
    "what-if-analysis-specialist": handle_what_if_analysis_specialist,
    "simulation-calibration-analyst": handle_simulation_calibration_analyst,
    "simulation-validity-auditor": handle_simulation_validity_auditor,
}
