"""Data preparation engine: profiling, cleaning, dedupe, joins, reshaping,
aggregation, window calculations, resampling, sampling, mapping and allocation.

Covers catalog categories 6, 9 and the deterministic parts of 25/26 that operate
on tables (outlier detection, RFM segmentation, forecasting helpers live in
tools-sdf-stats.py).

All functions are pure: they take a table dict {columns, rows, meta} and return
new tables plus an auditable list of transformation steps.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


def _sdf_import(alias, filename):
    if alias in sys.modules:
        return sys.modules[alias]
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


C = _sdf_import("sdf_common", "tools-sdf-common.py")
SkillToolError = C.SkillToolError


def lineage(steps, kind, detail):
    steps.append({"step": len(steps) + 1, "kind": kind, "detail": detail})
    return steps


# ==========================================================================
# profiling
# ==========================================================================


def profile(table, top_n=5, sample_values=3):
    rows, ncols = C.table_shape(table)
    columns = []
    for ci, name in enumerate(table["columns"]):
        values = [r[ci] for r in table["rows"]]
        non_empty = [v for v in values if not C.is_empty(v)]
        nums = C.nums(non_empty)
        numeric_share = len(nums) / len(non_empty) if non_empty else 0.0
        identifier = C.looks_like_identifier(name, non_empty) and not (
            non_empty and sum(1 for v in non_empty if C.parse_date(v)) / len(non_empty) >= 0.8)
        dates = [C.parse_date(v) for v in non_empty] if numeric_share < 0.9 else []
        date_share = sum(1 for d in dates if d) / len(non_empty) if non_empty else 0.0
        freq = {}
        for v in non_empty:
            k = str(v)
            freq[k] = freq.get(k, 0) + 1
        top = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
        inferred = "text"
        if numeric_share >= 0.9 and non_empty:
            inferred = "number"
            if all(isinstance(v, (int, float)) and float(v).is_integer() for v in nums):
                inferred = "integer"
        if date_share >= 0.8 and non_empty and inferred == "text":
            inferred = "date"
        if non_empty and all(isinstance(v, bool) or str(v).strip().lower() in C.TRUE_WORDS for v in non_empty):
            inferred = "boolean"
        if identifier:
            inferred = "identifier"
        col = {
            "name": name,
            "inferred_type": inferred,
            "non_empty": len(non_empty),
            "missing": len(values) - len(non_empty),
            "missing_pct": round(100 * (len(values) - len(non_empty)) / len(values), 2) if values else 0.0,
            "distinct": len(freq),
            "top_values": [{"value": k, "count": v} for k, v in top],
            "samples": [str(v) for v in non_empty[:sample_values]],
        }
        if nums and numeric_share >= 0.9 and not identifier:
            col["numeric"] = {
                "min": min(nums), "max": max(nums), "mean": C.mean(nums),
                "median": C.median(nums), "stdev": C.stdev(nums),
                "negative_count": sum(1 for x in nums if x < 0),
                "zero_count": sum(1 for x in nums if x == 0),
            }
        if date_share >= 0.8 and non_empty:
            real = [d for d in dates if d]
            if real:
                col["date_range"] = {"min": min(real).isoformat(), "max": max(real).isoformat()}
        duplicate_key_like = col["distinct"] == len(non_empty) and len(non_empty) > 1
        col["unique_candidate"] = bool(duplicate_key_like and col["missing"] == 0)
        columns.append(col)
    return {
        "rows": rows,
        "columns": ncols,
        "duplicate_rows": _duplicate_row_count(table),
        "columns_profile": columns,
        "empty_columns": [c["name"] for c in columns if c["non_empty"] == 0],
        "constant_columns": [c["name"] for c in columns if c["distinct"] == 1 and c["non_empty"] > 0],
        "candidate_keys": [c["name"] for c in columns if c["unique_candidate"]],
    }


def _duplicate_row_count(table):
    seen = set()
    dupes = 0
    for row in table["rows"]:
        key = tuple(str(v) for v in row)
        if key in seen:
            dupes += 1
        else:
            seen.add(key)
    return dupes


# ==========================================================================
# cleaning
# ==========================================================================


def clean(table, options=None):
    """Non-destructive cleaning: original values are kept in a change log."""
    opts = options or {}
    trim = opts.get("trim", True)
    collapse_space = opts.get("collapse_whitespace", True)
    fix_case = opts.get("case", "none")  # none|upper|lower|title
    blanks = set(opts.get("blank_tokens", ["", "-", "n/a", "N/A", "null", "None", "NULL"]))
    fill_missing = opts.get("fill_missing")  # {column: value}
    type_map = opts.get("types", {})  # {column: number|integer|date|text|boolean}
    dedupe = opts.get("dedupe", False)
    dedupe_keys = opts.get("dedupe_keys")
    numeric_columns = opts.get("numeric_columns")

    steps = []
    changes = []
    out = C.make_table(table["columns"], [list(r) for r in table["rows"]], table.get("meta"))
    cols = out["columns"]

    def note(row_i, ci, before, after, reason):
        if len(changes) < 5000:
            changes.append({"row": row_i + 2, "column": cols[ci], "before": before, "after": after, "reason": reason})

    for ri, row in enumerate(out["rows"]):
        for ci, val in enumerate(row):
            if isinstance(val, str):
                new = val
                if trim:
                    new = new.strip()
                if collapse_space:
                    new = re.sub(r"[ \t\u00a0]+", " ", new)
                if fix_case == "upper":
                    new = new.upper()
                elif fix_case == "lower":
                    new = new.lower()
                elif fix_case == "title":
                    new = re.sub(r"\b([a-z])", lambda m: m.group(1).upper(), new.lower())
                if new in blanks or new.strip() == "":
                    if new != "" and new.strip() != "":
                        note(ri, ci, val, None, "blank_token_normalised")
                    new = None
                if new != val:
                    if new is not None:
                        note(ri, ci, val, new, "whitespace_or_case")
                    row[ci] = new

    if numeric_columns or type_map:
        targets = set(numeric_columns or []) | {k for k, v in (type_map or {}).items() if v in {"number", "integer"}}
        for name in targets:
            ci = C.column_index(out, name)
            for ri, row in enumerate(out["rows"]):
                if C.is_empty(row[ci]):
                    continue
                parsed, ok = C.parse_number_locale(row[ci])
                if not ok:
                    note(ri, ci, row[ci], None, "unparseable_number_set_null")
                    row[ci] = None
                    continue
                target = type_map.get(name, "number")
                val = int(round(parsed)) if target == "integer" and float(parsed).is_integer() else parsed
                if val != row[ci]:
                    note(ri, ci, row[ci], val, "numeric_coercion")
                row[ci] = val
            steps = lineage(steps, "type_coercion", f"{name} -> {type_map.get(name, 'number')}")

    for name, kind in (type_map or {}).items():
        if kind not in {"date", "boolean", "text"}:
            continue
        ci = C.column_index(out, name)
        for ri, row in enumerate(out["rows"]):
            if C.is_empty(row[ci]):
                continue
            before = row[ci]
            if kind == "date":
                parsed = C.parse_date(before, dayfirst=opts.get("dayfirst", True))
                new = parsed.isoformat() if parsed else None
            elif kind == "boolean":
                new = C.to_bool(before, default=None)
            else:
                new = str(before)
            if new != before:
                note(ri, ci, before, new, f"cast_{kind}")
                row[ci] = new
            if new is None:
                note(ri, ci, before, None, f"invalid_{kind}")
        steps = lineage(steps, "type_coercion", f"{name} -> {kind}")

    for name, value in (fill_missing or {}).items():
        ci = C.column_index(out, name)
        filled = 0
        for ri, row in enumerate(out["rows"]):
            if C.is_empty(row[ci]):
                note(ri, ci, None, value, "explicit_fill")
                row[ci] = value
                filled += 1
        steps = lineage(steps, "imputation", f"{name} missing -> {value!r} ({filled} cells)")

    if dedupe:
        keys = dedupe_keys or out["columns"]
        idx = [C.column_index(out, k) for k in keys]
        seen = set()
        kept = []
        removed = 0
        for row in out["rows"]:
            k = tuple(str(row[i]).strip().lower() if isinstance(row[i], str) else row[i] for i in idx)
            if k in seen:
                removed += 1
                continue
            seen.add(k)
            kept.append(row)
        out["rows"] = kept
        steps = lineage(steps, "dedupe", f"keys={keys}; removed {removed} rows")

    steps.insert(0, {"step": 0, "kind": "input", "detail": f"{len(table['rows'])} rows x {len(table['columns'])} columns"})
    return {
        "table": out,
        "changes": changes,
        "change_count": len(changes),
        "steps": steps,
        "rows_before": len(table["rows"]),
        "rows_after": len(out["rows"]),
    }


def dedupe_scores(table, keys, fuzzy_threshold=0.0):
    """Find exact and near-duplicate groups; returns groups and survivorship proposal."""
    idx = [C.column_index(table, k) for k in keys]
    groups = {}
    for ri, row in enumerate(table["rows"]):
        k = "|".join(_norm_token(row[i]) for i in idx)
        groups.setdefault(k, []).append(ri)
    exact = [{"key": k, "rows": [r + 2 for r in rows], "count": len(rows)} for k, rows in groups.items() if len(rows) > 1]
    near = []
    if fuzzy_threshold > 0:
        tokens = [(k, rows) for k, rows in groups.items()]
        for i in range(len(tokens)):
            for j in range(i + 1, len(tokens)):
                sim = _similarity(tokens[i][0], tokens[j][0])
                if sim >= fuzzy_threshold:
                    near.append({"a": tokens[i][0], "b": tokens[j][0], "similarity": round(sim, 4),
                                 "rows": [r + 2 for r in tokens[i][1] + tokens[j][1]]})
    survivors = []
    for g in exact:
        rows = [table["rows"][r - 2] for r in g["rows"]]
        survivors.append({"key": g["key"], "keep_row": g["rows"][0], "drop_rows": g["rows"][1:],
                          "sample": rows[0][:min(4, len(rows[0]))]})
    return {
        "exact_duplicate_groups": exact,
        "near_duplicate_pairs": near[:200],
        "duplicate_row_count": sum(len(g["rows"]) - 1 for g in exact),
        "survivorship_proposal": survivors[:200],
        "note": "Survivorship keeps the first occurrence; review before merging personal or financial records.",
    }


def _norm_token(v):
    if v is None:
        return ""
    s = str(v).strip().lower()
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s)


def _similarity(a, b):
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


# ==========================================================================
# joins / reshaping
# ==========================================================================


def join(left, right, left_keys, right_keys, how="inner", suffixes=("_x", "_y")):
    how = how.lower()
    if how not in {"inner", "left", "right", "full", "anti", "semi"}:
        raise SkillToolError("BAD_ARGUMENT", f"Unknown join type {how!r}.",
                             "Use inner, left, right, full, anti or semi.")
    if len(left_keys) != len(right_keys):
        raise SkillToolError("BAD_ARGUMENT", "left_keys and right_keys must have the same length.")

    l_idx = [C.column_index(left, k) for k in left_keys]
    r_idx = [C.column_index(right, k) for k in right_keys]
    rkeys = [right["columns"][i] for i in r_idx]

    def key_of(row, idx):
        return tuple(_norm_token(row[i]) for i in idx)

    right_map = {}
    for ri, row in enumerate(right["rows"]):
        right_map.setdefault(key_of(row, r_idx), []).append(row)

    out_cols = list(left["columns"])
    for j, name in enumerate(right["columns"]):
        if j in r_idx and name in left["columns"]:
            continue
        if name in out_cols:
            base = name + suffixes[1]
            n = 2
            while base in out_cols:
                base = f"{name}{suffixes[1]}{n}"
                n += 1
            out_cols.append(base)
        else:
            out_cols.append(name)
    pass_right_idx = [j for j in range(len(right["columns"])) if j not in r_idx]

    rows = []
    matched_right = set()
    left_keys_seen = set()
    for row in left["rows"]:
        k = key_of(row, l_idx)
        left_keys_seen.add(k)
        matches = right_map.get(k)
        if how in {"inner", "left", "full"}:
            if matches:
                for m in matches:
                    rows.append(list(row) + [m[j] for j in pass_right_idx])
                    matched_right.add(id(m))
            elif how in {"left", "full"}:
                rows.append(list(row) + [None] * len(pass_right_idx))
        elif how == "right":
            if matches:
                for m in matches:
                    rows.append(list(row) + [m[j] for j in pass_right_idx])
                    matched_right.add(id(m))
        elif how == "anti":
            if not matches:
                rows.append(list(row) + [None] * len(pass_right_idx))
        elif how == "semi":
            if matches:
                rows.append(list(row) + [None] * len(pass_right_idx))
    if how in {"right", "full"}:
        for rk, ms in right_map.items():
            for m in ms:
                if id(m) not in matched_right:
                    rows.append([None] * len(left["columns"]) + [m[j] for j in pass_right_idx])
    cols = out_cols if how not in {"anti", "semi"} else list(left["columns"])
    if how in {"anti", "semi"}:
        rows = [r[: len(left["columns"])] for r in rows]

    right_total = len(right["rows"])
    unmatched_right = right_total - len(matched_right)
    stats = {
        "how": how,
        "left_rows": len(left["rows"]),
        "right_rows": right_total,
        "output_rows": len(rows),
        "matched_right_rows": len(matched_right),
        "unmatched_right_rows": unmatched_right,
        "duplicate_right_matches": max(0, len(matched_right) - len(right_map)),
        "cardinality": "one-to-many" if any(len(v) > 1 for v in right_map.values()) else "one-to-one",
        "left_keys": left_keys,
        "right_keys": rkeys,
    }
    warnings = []
    if stats["duplicate_right_matches"]:
        warnings.append("Right table has duplicate keys: output rows were multiplied.")
    if how in {"inner", "left"} and unmatched_right:
        warnings.append(f"{unmatched_right} right-side rows had no left match (excluded).")
    return {"table": C.make_table(cols, rows, {"join": stats}), "stats": stats, "warnings": warnings}


def pivot(table, index, columns, values, agg="sum", fill=0):
    ridx = [C.column_index(table, c) for c in index]
    cidx = C.column_index(table, columns) if columns else None
    vidx = C.column_index(table, values) if values else None
    col_keys = []
    buckets = {}
    for row in table["rows"]:
        ck = str(row[cidx]) if cidx is not None else "value"
        if ck not in col_keys:
            col_keys.append(ck)
        ik = tuple("" if C.is_empty(row[i]) else str(row[i]) for i in ridx)
        buckets.setdefault(ik, {}).setdefault(ck, []).append(row[vidx] if vidx is not None else 1)
    col_keys.sort()
    out_cols = list(index) + col_keys
    rows = []
    for ik in sorted(buckets):
        row = list(ik)
        for ck in col_keys:
            vals = buckets[ik].get(ck, [])
            row.append(_aggregate(vals, agg, fill))
        rows.append(row)
    return C.make_table(out_cols, rows, {"pivot": {"index": index, "columns": columns, "values": values, "agg": agg}})


def unpivot(table, id_columns, value_columns=None, var_name="variable", value_name="value", drop_nulls=True):
    id_idx = [C.column_index(table, c) for c in id_columns]
    if value_columns:
        v_idx = [C.column_index(table, c) for c in value_columns]
    else:
        v_idx = [i for i in range(len(table["columns"])) if i not in id_idx]
    cols = list(id_columns) + [var_name, value_name]
    rows = []
    for row in table["rows"]:
        ids = [row[i] for i in id_idx]
        for i in v_idx:
            val = row[i]
            if drop_nulls and C.is_empty(val):
                continue
            rows.append(ids + [table["columns"][i], val])
    return C.make_table(cols, rows, {"unpivot": {"ids": id_columns, "values": [table["columns"][i] for i in v_idx]}})


# ==========================================================================
# aggregation / windows / resampling
# ==========================================================================


def _aggregate(values, agg, fill=0):
    vals = C.nums(values)
    agg = agg.lower()
    if agg == "sum":
        return sum(vals) if vals else fill
    if agg == "count":
        return len([v for v in values if not C.is_empty(v)])
    if agg == "count_distinct":
        return len({str(v) for v in values if not C.is_empty(v)})
    if agg == "average" or agg == "mean":
        return C.mean(vals) if vals else fill
    if agg == "median":
        return C.median(vals) if vals else fill
    if agg == "min":
        return min(vals) if vals else fill
    if agg == "max":
        return max(vals) if vals else fill
    if agg in {"stdev", "std"}:
        sd = C.stdev(vals)
        return sd if sd is not None else fill
    if agg.startswith("p") and agg[1:].replace(".", "").isdigit():
        q = float(agg[1:]) / 100.0 if float(agg[1:]) > 1 else float(agg[1:])
        p = C.percentile(vals, q)
        return p if p is not None else fill
    if agg == "first":
        return values[0] if values else fill
    if agg == "last":
        return values[-1] if values else fill
    raise SkillToolError("BAD_AGGREGATION", f"Unsupported aggregation {agg!r}.",
                         "Use sum, count, count_distinct, average, median, min, max, stdev, p90, first, last.")


def aggregate(table, group_by, measures):
    """measures: [{"column": c, "agg": "sum", "name": optional}]"""
    g_idx = [C.column_index(table, g) for g in group_by]
    groups = {}
    order = []
    for row in table["rows"]:
        k = tuple("" if C.is_empty(row[i]) else str(row[i]) for i in g_idx)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(row)
    out_cols = list(group_by)
    specs = []
    for m in measures:
        col = m.get("column")
        agg = m.get("agg", "sum")
        name = m.get("name") or f"{agg}_{col}" if col else f"{agg}_rows"
        out_cols.append(name)
        specs.append((col, agg))
    out_cols.append("row_count")
    rows = []
    for k in order:
        bucket = groups[k]
        row = list(k)
        for col, agg in specs:
            if col is None:
                row.append(len(bucket))
            else:
                ci = C.column_index(table, col)
                row.append(_aggregate([r[ci] for r in bucket], agg))
        row.append(len(bucket))
        rows.append(row)
    rows.sort(key=lambda r: (-(C.to_number(r[len(group_by)]) or 0), [str(x) for x in r[: len(group_by)]]))
    return C.make_table(out_cols, rows, {"group_by": group_by, "measures": measures})


def window(table, column, functions, order_by=None, partition_by=None, date_column=None):
    """Add running-total / rank / moving-average / lag / pct-of-total columns."""
    ci = C.column_index(table, column)
    part_idx = [C.column_index(table, p) for p in (partition_by or [])]
    order_idx = C.column_index(table, order_by) if order_by else None
    date_idx = C.column_index(table, date_column) if date_column else None
    rows = list(table["rows"])
    if order_idx is not None:
        rows.sort(key=lambda r: (tuple(str(r[i]) for i in part_idx), _sort_val(r[order_idx])))
    elif date_idx is not None:
        rows.sort(key=lambda r: (tuple(str(r[i]) for i in part_idx), _sort_val(r[date_idx])))
    partitions = {}
    for row in rows:
        partitions.setdefault(tuple(str(r[i]) for i in part_idx), []).append(row)
    new_cols = list(table["columns"])
    added = []
    for fn in functions:
        name = fn if isinstance(fn, str) else fn.get("name", "window")
        new_cols.append(name)
        added.append((name, fn))
    out_rows = []
    for key, bucket in partitions.items():
        vals = [C.to_number(r[ci]) for r in bucket]
        total = sum(v for v in vals if v is not None)
        n = len([v for v in vals if v is not None])
        running = 0.0
        for i, row in enumerate(bucket):
            extra = []
            for name, fn in added:
                kind = fn if isinstance(fn, str) else fn.get("type", fn.get("name"))
                size = 3 if isinstance(fn, str) else int(fn.get("size", 3))
                v = vals[i]
                if kind == "running_total":
                    running += v or 0.0
                    extra.append(running)
                elif kind == "rank":
                    extra.append(1 + sum(1 for x in vals if x is not None and v is not None and x > v))
                elif kind == "dense_rank":
                    uniq = sorted({x for x in vals if x is not None}, reverse=True)
                    extra.append(uniq.index(v) + 1 if v in uniq else None)
                elif kind == "moving_average":
                    window_vals = [x for x in vals[max(0, i - size + 1): i + 1] if x is not None]
                    extra.append(C.mean(window_vals) if window_vals else None)
                elif kind == "lag":
                    extra.append(vals[i - 1] if i >= 1 else None)
                elif kind == "lead":
                    extra.append(vals[i + 1] if i + 1 < len(vals) else None)
                elif kind == "pct_of_total":
                    extra.append(v / total if (total not in (0, None) and v is not None) else None)
                elif kind == "cumulative_share":
                    upto = sum(x for x in vals[: i + 1] if x is not None)
                    extra.append(upto / total if total not in (0, None) else None)
                elif kind == "pct_change":
                    prev = vals[i - 1] if i >= 1 else None
                    extra.append((v - prev) / prev if (prev not in (None, 0) and v is not None) else None)
                elif kind == "zscore":
                    sd = C.stdev([x for x in vals if x is not None])
                    m = C.mean([x for x in vals if x is not None])
                    extra.append((v - m) / sd if (sd and v is not None) else None)
                else:
                    extra.append(None)
            out_rows.append(list(row) + extra)
    return C.make_table(new_cols, out_rows, {"window": column, "partition_by": partition_by, "order_by": order_by})


def _sort_val(v):
    n = C.to_number(v)
    if n is not None:
        return (0, n, "")
    d = C.parse_date(v)
    if d:
        return (0, d.toordinal(), "")
    return (1, 0, str(v))


def resample(table, date_column, value_column, freq="month", agg="sum", fill_gaps=True):
    di = C.column_index(table, date_column)
    vi = C.column_index(table, value_column)
    buckets = {}
    for row in table["rows"]:
        d = C.parse_date(row[di])
        if not d:
            continue
        key = _period_key(d, freq)
        buckets.setdefault(key, []).append(row[vi])
    keys = sorted(buckets)
    if fill_gaps and keys:
        keys = _fill_periods(keys, freq)
    rows = [[k, _aggregate(buckets.get(k, []), agg, 0)] for k in keys]
    return C.make_table(["period", value_column], rows,
                        {"freq": freq, "agg": agg, "date_column": date_column})


def _period_key(d, freq):
    f = freq.lower()
    if f in {"day", "daily"}:
        return d.isoformat()
    if f in {"week", "weekly"}:
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if f in {"month", "monthly"}:
        return f"{d.year:04d}-{d.month:02d}"
    if f in {"quarter", "quarterly"}:
        return f"{d.year:04d}-Q{(d.month - 1) // 3 + 1}"
    if f in {"year", "annual", "yearly"}:
        return f"{d.year:04d}"
    raise SkillToolError("BAD_ARGUMENT", f"Unsupported frequency {freq!r}.",
                         "Use day, week, month, quarter or year.")


def _fill_periods(keys, freq):
    f = freq.lower()
    try:
        if f in {"month", "monthly"}:
            allk = []
            start = datetime.strptime(keys[0], "%Y-%m").date()
            end = datetime.strptime(keys[-1], "%Y-%m").date()
            cur = start
            while cur <= end:
                allk.append(f"{cur.year:04d}-{cur.month:02d}")
                cur = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
            return allk
        if f in {"quarter", "quarterly"}:
            allk = []
            y0, q0 = int(keys[0][:4]), int(keys[0][-1])
            y1, q1 = int(keys[-1][:4]), int(keys[-1][-1])
            y, q = y0, q0
            while (y, q) <= (y1, q1):
                allk.append(f"{y:04d}-Q{q}")
                q += 1
                if q > 4:
                    q = 1
                    y += 1
            return allk
        if f in {"year", "annual", "yearly"}:
            return [f"{y:04d}" for y in range(int(keys[0]), int(keys[-1]) + 1)]
    except (ValueError, IndexError):
        return keys
    return keys


def sample(table, n=None, frac=None, method="random", stratify_by=None, seed=42, review_column=None):
    rng = random.Random(seed)
    rows = list(table["rows"])
    total = len(rows)
    if total == 0:
        raise SkillToolError("EMPTY_INPUT", "Cannot sample an empty table.")
    if n is None:
        f = float(frac if frac is not None else 0.1)
        if not 0 < f <= 1:
            raise SkillToolError("BAD_ARGUMENT", "frac must be within (0,1].")
        n = max(1, int(round(total * f)))
    if n > total:
        raise SkillToolError("BAD_ARGUMENT", f"Sample size {n} exceeds row count {total}.",
                             "Reduce --size or raise the input size.")
    method = method.lower()
    chosen = []
    if method == "random":
        chosen = rng.sample(range(total), n)
    elif method == "systematic":
        step = total / n
        chosen = [min(total - 1, int(i * step + rng.random() * step)) for i in range(n)]
    elif method == "stratified":
        if not stratify_by:
            raise SkillToolError("BAD_ARGUMENT", "stratified sampling requires --stratify-by.",
                                 "Pass one or more column names.")
        groups = {}
        for i, row in enumerate(rows):
            groups.setdefault(str(row[C.column_index(table, stratify_by)]), []).append(i)
        for key, idxs in sorted(groups.items()):
            share = len(idxs) / total
            take = max(1, min(len(idxs), int(round(n * share))))
            chosen.extend(rng.sample(idxs, take))
        chosen = chosen[:n]
    elif method == "review":
        if not review_column:
            raise SkillToolError("BAD_ARGUMENT", "review sampling requires --review-column.")
        ci = C.column_index(table, review_column)
        risky = [i for i, r in enumerate(rows) if C.to_bool(r[ci], False) or str(r[ci]).lower() in {"yes", "review", "flag"}]
        rest = [i for i in range(total) if i not in set(risky)]
        take_risky = min(len(risky), max(0, n // 2))
        chosen = rng.sample(risky, take_risky) if take_risky else []
        remaining = n - len(chosen)
        if remaining > 0 and rest:
            chosen += rng.sample(rest, min(remaining, len(rest)))
    else:
        raise SkillToolError("BAD_ARGUMENT", f"Unknown sampling method {method!r}.",
                             "Use random, systematic, stratified or review.")
    chosen = sorted(set(chosen))
    out = C.make_table(table["columns"], [rows[i] for i in chosen])
    return {"table": out, "sampled_rows": len(out["rows"]), "population_rows": total,
            "method": method, "seed": seed, "note": "Reproducible with the same seed, input and method."}


# ==========================================================================
# mapping / allocation
# ==========================================================================


def crosswalk(table, source_column, mapping, target_name=None, strict=False):
    ci = C.column_index(table, source_column)
    mapping_norm = {_norm_token(k): v for k, v in mapping.items()}
    target = target_name or f"{source_column}_mapped"
    cols = list(table["columns"]) + [target]
    rows = []
    unmapped = {}
    for row in table["rows"]:
        key = _norm_token(row[ci])
        val = mapping_norm.get(key)
        if val is None:
            unmapped[str(row[ci])] = unmapped.get(str(row[ci]), 0) + 1
        rows.append(list(row) + [val])
    if strict and unmapped:
        raise SkillToolError("MAPPING_INCOMPLETE", f"{len(unmapped)} distinct source values are not mapped.",
                             "Add them to the mapping file or run without --strict to collect an exception queue.",
                             {"unmapped": dict(sorted(unmapped.items(), key=lambda kv: -kv[1])[:50])})
    return {"table": C.make_table(cols, rows), "unmapped": unmapped,
            "unmapped_count": sum(unmapped.values()), "mapping_size": len(mapping_norm)}


def allocate(total, weights):
    """Largest-remainder allocation that always sums exactly to total."""
    if not weights:
        raise SkillToolError("BAD_ARGUMENT", "weights must be non-empty.")
    w = [max(0.0, float(x)) for x in weights]
    s = sum(w)
    if s <= 0:
        w = [1.0] * len(w)
        s = float(len(w))
    raw = [total * x / s for x in w]
    floors = [math.floor(v) for v in raw]
    remainder = total - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - floors[i]), i))
    for i in order[: int(round(remainder))]:
        floors[i] += 1
    return {"allocated": floors, "weights": w, "total": total, "check_sum": sum(floors)}


def allocate_table(table, amount_column, driver_columns, group_by=None, rounding=2):
    ai = C.column_index(table, amount_column)
    d_idx = [C.column_index(table, d) for d in driver_columns]
    g_idx = [C.column_index(table, g) for g in (group_by or [])]
    groups = {}
    for ri, row in enumerate(table["rows"]):
        g = tuple(str(row[i]) for i in g_idx) or ("__all__",)
        groups.setdefault(g, []).append(ri)
    cols = list(table["columns"]) + ["allocated_amount", "allocation_share"]
    rows = [list(r) for r in table["rows"]]
    for g, idxs in groups.items():
        total = sum(C.to_number(table["rows"][i][ai]) or 0.0 for i in idxs)
        weights = []
        for i in idxs:
            w = 1.0
            for di in d_idx:
                w *= C.to_number(table["rows"][i][di]) or 0.0
            weights.append(w)
        if sum(weights) <= 0:
            weights = [1.0] * len(idxs)
        raw = [total * w / sum(weights) for w in weights]
        scaled = [int(round(v * 10 ** rounding)) for v in raw]
        target = int(round(total * 10 ** rounding))
        diff = target - sum(scaled)
        if diff:
            scaled[0] += diff
        for pos, i in enumerate(idxs):
            allocated = scaled[pos] / 10 ** rounding
            share = allocated / total if total else None
            rows[i] = rows[i] + [allocated, share]
    return C.make_table(cols, rows, {"driver_columns": driver_columns, "group_by": group_by,
                                     "check": "allocated amounts sum to the group total"})


def outliers(table, columns=None, method="iqr", threshold=1.5, sd_threshold=3.0, max_flags=1000):
    cols = columns or table["columns"]
    flags = []
    summary = []
    for name in cols:
        ci = C.column_index(table, name)
        vals = [C.to_number(r[ci]) for r in table["rows"]]
        clean = [v for v in vals if v is not None]
        if len(clean) < 4:
            continue
        lo = hi = None
        if method.lower() == "iqr":
            q1, q3 = C.percentile(clean, 0.25), C.percentile(clean, 0.75)
            iqr = q3 - q1
            lo, hi = q1 - threshold * iqr, q3 + threshold * iqr
        elif method.lower() == "zscore":
            m, sd = C.mean(clean), C.stdev(clean)
            if sd:
                lo, hi = m - sd_threshold * sd, m + sd_threshold * sd
        elif method.lower() == "mad":
            med = C.median(clean)
            mad = C.median([abs(v - med) for v in clean])
            if mad:
                lo, hi = med - threshold * 1.4826 * mad, med + threshold * 1.4826 * mad
        else:
            raise SkillToolError("BAD_ARGUMENT", f"Unknown outlier method {method!r}.", "Use iqr, zscore or mad.")
        if lo is None:
            continue
        hits = 0
        for ri, v in enumerate(vals):
            if v is not None and (v < lo or v > hi):
                hits += 1
                if len(flags) < max_flags:
                    flags.append({"column": name, "row": ri + 2, "value": v,
                                  "lower_bound": C.round_half_up(lo, 4), "upper_bound": C.round_half_up(hi, 4),
                                  "direction": "low" if v < lo else "high"})
        summary.append({"column": name, "method": method, "lower_bound": C.round_half_up(lo, 4),
                        "upper_bound": C.round_half_up(hi, 4), "flagged": hits})
    return {"flags": flags, "flagged_count": len(flags), "per_column": summary,
            "policy": "Flagged values are routed for review; nothing is deleted or overwritten."}


def type_enforcement(table, schema):
    """schema: {column: type} applied with an exceptions list rather than silent loss."""
    out = C.make_table(table["columns"], [list(r) for r in table["rows"]])
    exceptions = []
    for name, kind in schema.items():
        ci = C.column_index(out, name)
        for ri, row in enumerate(out["rows"]):
            raw = row[ci]
            if C.is_empty(raw):
                continue
            new = raw
            if kind in {"number", "decimal", "integer", "currency"}:
                parsed, ok = C.parse_number_locale(raw)
                if not ok:
                    exceptions.append({"row": ri + 2, "column": name, "value": raw, "issue": f"not_{kind}"})
                    continue
                new = int(round(parsed)) if kind == "integer" else parsed
            elif kind == "date":
                d = C.parse_date(raw)
                if not d:
                    exceptions.append({"row": ri + 2, "column": name, "value": raw, "issue": "not_date"})
                    continue
                new = d.isoformat()
            elif kind == "boolean":
                b = C.to_bool(raw, default=None)
                if b is None:
                    exceptions.append({"row": ri + 2, "column": name, "value": raw, "issue": "not_boolean"})
                    continue
                new = b
            elif kind == "text":
                new = str(raw)
            elif kind == "percent":
                parsed, ok = C.parse_number_locale(raw)
                if not ok:
                    exceptions.append({"row": ri + 2, "column": name, "value": raw, "issue": "not_percent"})
                    continue
                new = parsed
            row[ci] = new
    return {"table": out, "exceptions": exceptions[:1000], "exception_count": len(exceptions)}
