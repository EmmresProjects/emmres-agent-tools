"""Statistics, experimentation and predictive-support engine.

Covers catalog categories 25 (Statistical Analysis and Experimentation) and the
deterministic, review-oriented parts of 26 (Data Mining, Segmentation, Predictive
Models). Every function returns numbers plus the interpretation limits that apply;
no automated decisioning is performed.
"""

from __future__ import annotations

import importlib.util
import math
import random
import re
import sys
from datetime import date, datetime
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
D = _sdf_import("sdf_data", "tools-sdf-data.py")
SkillToolError = C.SkillToolError


# ==========================================================================
# descriptive statistics
# ==========================================================================


def describe(table, columns=None, group_by=None):
    cols = [c for c in (columns or table["columns"]) if _is_numeric_column(table, c)]
    if not cols:
        raise SkillToolError("NO_NUMERIC_COLUMNS", "No numeric columns were found to summarise.",
                             "Name numeric columns with --columns, or clean/coerce types first.")
    groups = {}
    if group_by:
        gi = C.column_index(table, group_by)
        for row in table["rows"]:
            groups.setdefault(str(row[gi]), []).append(row)
    else:
        groups = {"all": table["rows"]}
    out_rows = []
    for gname, rows in sorted(groups.items()):
        for col in cols:
            ci = C.column_index(table, col)
            vals = C.nums([r[ci] for r in rows])
            if not vals:
                continue
            q1, q3 = C.percentile(vals, 0.25), C.percentile(vals, 0.75)
            out_rows.append({
                "group": gname if group_by else None,
                "column": col,
                "n": len(vals),
                "mean": C.mean(vals),
                "median": C.median(vals),
                "stdev": C.stdev(vals),
                "min": min(vals),
                "max": max(vals),
                "q1": q1,
                "q3": q3,
                "iqr": q3 - q1,
                "cv": C.safe_div(C.stdev(vals), C.mean(vals)),
                "skew": C.skew(vals),
                "kurtosis": C.kurtosis(vals),
                "missing": len(rows) - len(vals),
            })
    return {"summaries": out_rows,
            "interpretation": "Skew beyond +/-1 or heavy kurtosis means the mean alone is misleading; report median and IQR with it."}


def _is_numeric_column(table, name, share=0.8):
    ci = C.column_index(table, name)
    vals = [r[ci] for r in table["rows"] if not C.is_empty(r[ci])]
    if not vals:
        return False
    ok = sum(1 for v in vals if C.to_number(v) is not None)
    return ok / len(vals) >= share


def correlate(table, x, y, method="pearson"):
    xi, yi = C.column_index(table, x), C.column_index(table, y)
    xs, ys = [], []
    for row in table["rows"]:
        a, b = C.to_number(row[xi]), C.to_number(row[yi])
        xs.append(a)
        ys.append(b)
    pairs = [(a, b) for a, b in zip(xs, ys) if a is not None and b is not None]
    if len(pairs) < 3:
        raise SkillToolError("INSUFFICIENT_DATA", "Need at least 3 complete pairs to correlate.")
    xa, ya = [p[0] for p in pairs], [p[1] for p in pairs]
    if method.lower() == "spearman":
        r, n = C.spearman(xa, ya)
    elif method.lower() == "pearson":
        r, n = C.pearson(xa, ya)
    else:
        raise SkillToolError("BAD_ARGUMENT", f"Unknown correlation method {method!r}.", "Use pearson or spearman.")
    if r is None:
        raise SkillToolError("DEGENERATE_DATA", "Correlation is undefined (a variable is constant).")
    df = n - 2
    t = r * math.sqrt(df / max(1e-12, 1 - r * r))
    p = 2 * C.t_sf(abs(t), df)
    return {"x": x, "y": y, "method": method.lower(), "r": r, "n": n, "t": t, "df": df,
            "p_value": p, "r_squared": r * r,
            "caveat": "Association only. A significant correlation does not establish causation or direction."}


def correlation_matrix(table, columns=None):
    cols = [c for c in (columns or table["columns"]) if _is_numeric_column(table, c)]
    if len(cols) < 2:
        raise SkillToolError("INSUFFICIENT_DATA", "Need at least two numeric columns for a correlation matrix.")
    matrix = []
    for a in cols:
        row = []
        for b in cols:
            try:
                row.append(round(correlate(table, a, b)["r"], 4))
            except SkillToolError:
                row.append(None)
        matrix.append(row)
    return {"columns": cols, "matrix": matrix}


# ==========================================================================
# regression
# ==========================================================================


def regress(table, y, predictors, add_intercept=True):
    yi = C.column_index(table, y)
    pi = [C.column_index(table, p) for p in predictors]
    X, Y, used = [], [], 0
    for row in table["rows"]:
        yv = C.to_number(row[yi])
        xv = [C.to_number(row[i]) for i in pi]
        if yv is None or any(v is None for v in xv):
            continue
        X.append(([1.0] if add_intercept else []) + [float(v) for v in xv])
        Y.append(float(yv))
        used += 1
    if used < len(predictors) + 2:
        raise SkillToolError("INSUFFICIENT_DATA",
                             f"Need more complete rows ({used}) than parameters ({len(predictors) + 1}).",
                             "Collect more data or reduce predictors.")
    res = C.ols(X, Y)
    names = (["intercept"] if add_intercept else []) + list(predictors)
    coeffs = []
    for i, name in enumerate(names):
        coeffs.append({
            "term": name,
            "coefficient": res["coefficients"][i],
            "std_error": res["std_errors"][i],
            "t": res["t_stats"][i],
            "p_value": res["p_values"][i],
            "significant_5pct": (res["p_values"][i] is not None and res["p_values"][i] < 0.05),
        })
    return {
        "target": y,
        "predictors": predictors,
        "n": res["n"],
        "df_resid": res["df_resid"],
        "coefficients": coeffs,
        "r2": res["r2"],
        "adj_r2": res["adj_r2"],
        "rmse": res["rmse"],
        "residual_summary": {
            "mean": C.mean(res["residuals"]),
            "stdev": C.stdev(res["residuals"]),
            "min": min(res["residuals"]),
            "max": max(res["residuals"]),
        },
        "diagnostics": _regression_diagnostics(res, X, names),
        "caveats": [
            "Linear model: relationships outside the observed range are extrapolation.",
            "Check residual plots for curvature and heteroskedasticity before relying on p-values.",
        ],
    }


def _regression_diagnostics(res, X, names):
    notes = []
    r2 = res["r2"]
    if r2 is not None and r2 < 0.2:
        notes.append(f"Low explanatory power (R^2={r2:.3f}); predictors do not explain much variation.")
    if r2 is not None and r2 > 0.99:
        notes.append("R^2 above 0.99 is unusual and often indicates a duplicated or derived predictor.")
    resid = res["residuals"]
    if resid:
        sd = C.stdev(resid)
        big = [r for r in resid if sd and abs(r) > 3 * sd]
        if big:
            notes.append(f"{len(big)} residual(s) exceed 3 standard deviations; investigate as outliers.")
    if len(X) < 30:
        notes.append("Small sample: t-based p-values assume approximate normality of residuals.")
    return {"notes": notes, "residual_outliers": len(big) if resid else 0}


# ==========================================================================
# hypothesis tests and experiments
# ==========================================================================


def t_test(sample_a, sample_b=None, mu=0.0, paired=False, alpha=0.05):
    a = C.nums(sample_a)
    if len(a) < 2:
        raise SkillToolError("INSUFFICIENT_DATA", "Test requires at least 2 numeric observations in group A.")
    if sample_b is None:
        if len(a) < 2:
            raise SkillToolError("INSUFFICIENT_DATA", "One-sample test needs n >= 2.")
        m, sd = C.mean(a), C.stdev(a)
        se = sd / math.sqrt(len(a))
        t = (m - mu) / se if se else None
        df = len(a) - 1
        p = 2 * C.t_sf(abs(t), df) if t is not None else None
        return _test_result("one_sample_t", t, df, p, alpha,
                            {"mean": m, "sd": sd, "n": len(a), "mu0": mu, "se": se})
    b = C.nums(sample_b)
    if len(b) < 2:
        raise SkillToolError("INSUFFICIENT_DATA", "Test requires at least 2 numeric observations in group B.")
    if paired:
        if len(a) != len(b):
            raise SkillToolError("BAD_ARGUMENT", "Paired test requires equal sample sizes.",
                                 f"Got {len(a)} and {len(b)} after dropping blanks.")
        diffs = [x - y for x, y in zip(a, b)]
        return t_test(diffs, None, mu=mu, alpha=alpha)
    ma, mb = C.mean(a), C.mean(b)
    va, vb = C.variance(a), C.variance(b)
    na, nb = len(a), len(b)
    se = math.sqrt(va / na + vb / nb)
    t = (ma - mb) / se if se else None
    df_num = (va / na + vb / nb) ** 2
    df_den = (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)
    df = df_num / df_den if df_den else na + nb - 2
    p = 2 * C.t_sf(abs(t), df) if t is not None else None
    pooled_sd = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    d = (ma - mb) / pooled_sd if pooled_sd else None
    return _test_result("welch_two_sample_t", t, df, p, alpha,
                        {"mean_a": ma, "mean_b": mb, "n_a": na, "n_b": nb, "se": se,
                         "difference": ma - mb, "cohens_d": d})


def _test_result(test, stat, df, p, alpha, extra):
    sig = (p is not None and p < alpha)
    return {"test": test, "statistic": stat, "df": df, "p_value": p, "alpha": alpha,
            "significant": sig,
            "decision": ("Reject the null hypothesis at alpha=%.2f." % alpha) if sig
                        else ("Fail to reject the null hypothesis at alpha=%.2f." % alpha),
            "details": extra,
            "caveat": "Statistical significance is not business materiality; report the effect size with the p-value."}


def proportion_test(successes_a, n_a, successes_b, n_b, alpha=0.05):
    for v, name in ((n_a, "n_a"), (n_b, "n_b")):
        if v <= 0:
            raise SkillToolError("BAD_ARGUMENT", f"{name} must be positive.")
    pa, pb = successes_a / n_a, successes_b / n_b
    pooled = (successes_a + successes_b) / (n_a + n_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    z = (pa - pb) / se if se else None
    p = 2 * C.norm_sf(abs(z)) if z is not None else None
    diff = pa - pb
    se_unpooled = math.sqrt(pa * (1 - pa) / n_a + pb * (1 - pb) / n_b)
    ci = (diff - 1.959964 * se_unpooled, diff + 1.959964 * se_unpooled)
    return {"test": "two_proportion_z", "rate_a": pa, "rate_b": pb, "difference": diff,
            "favours": "a" if diff > 0 else ("b" if diff < 0 else "neither"),
            "difference_definition": "rate_a - rate_b",
            "z": z, "p_value": p, "alpha": alpha, "significant": bool(p is not None and p < alpha),
            "ci95_difference": ci,
            "caveat": "Normal approximation; needs at least 5 successes and 5 failures in each arm."}


def chi_square(table, row_column, column_column, alpha=0.05):
    ri, ci = C.column_index(table, row_column), C.column_index(table, column_column)
    rows_k, cols_k = [], []
    counts = {}
    for row in table["rows"]:
        r, c = str(row[ri]), str(row[ci])
        if r not in rows_k:
            rows_k.append(r)
        if c not in cols_k:
            cols_k.append(c)
        counts[(r, c)] = counts.get((r, c), 0) + 1
    if len(rows_k) < 2 or len(cols_k) < 2:
        raise SkillToolError("INSUFFICIENT_DATA", "Chi-square needs at least 2 rows and 2 columns of categories.")
    row_tot = {r: sum(counts.get((r, c), 0) for c in cols_k) for r in rows_k}
    col_tot = {c: sum(counts.get((r, c), 0) for r in rows_k) for c in cols_k}
    total = sum(row_tot.values())
    stat = 0.0
    expected_low = 0
    for r in rows_k:
        for c in cols_k:
            expected = row_tot[r] * col_tot[c] / total
            if expected < 5:
                expected_low += 1
            observed = counts.get((r, c), 0)
            if expected > 0:
                stat += (observed - expected) ** 2 / expected
    df = (len(rows_k) - 1) * (len(cols_k) - 1)
    p = C.chi2_sf(stat, df)
    k = min(len(rows_k), len(cols_k))
    cramers_v = math.sqrt(stat / (total * (k - 1))) if total and k > 1 else None
    warnings = []
    if expected_low:
        warnings.append(f"{expected_low} cells have expected counts below 5; chi-square approximation is weak.")
    return {"test": "chi_square_independence", "chi2": stat, "df": df, "p_value": p, "alpha": alpha,
            "significant": p < alpha, "cramers_v": cramers_v, "n": total,
            "contingency": {r: {c: counts.get((r, c), 0) for c in cols_k} for r in rows_k},
            "warnings": warnings,
            "caveat": "Association only; no causal claim and no direction of effect."}


def ab_test(visitors_a, conversions_a, visitors_b, conversions_b, alpha=0.05):
    base = proportion_test(conversions_a, visitors_a, conversions_b, visitors_b, alpha)
    lift = (base["rate_b"] - base["rate_a"]) / base["rate_a"] if base["rate_a"] else None
    rel = abs(lift) if lift is not None else None
    pooled = (conversions_a + conversions_b) / (visitors_a + visitors_b)
    se = math.sqrt(2 * pooled * (1 - pooled) / min(visitors_a, visitors_b)) if visitors_a and visitors_b else None
    power = None
    if se and rel is not None and pooled not in (0, 1):
        effect = abs(base["rate_b"] - base["rate_a"])
        if se > 0:
            z_effect = effect / se
            power = C.norm_sf(1.959964 - z_effect) + C.norm_cdf(-1.959964 - z_effect)
    return {**base, "relative_lift": lift,
            "practical_note": "Decide using the confidence interval on the business-relevant effect, not the p-value alone.",
            "approx_power_at_observed_effect": power,
            "sample_check": {"visitors_a": visitors_a, "visitors_b": visitors_b,
                             "min_visitors_met": min(visitors_a, visitors_b) >= 100}}


def sample_size(effect_size=None, power=0.8, alpha=0.05, baseline_rate=None, mde=None, two_sided=True, finite_population=None):
    if mde is not None and baseline_rate is not None:
        p1, p2 = baseline_rate, baseline_rate + mde
        pbar = (p1 + p2) / 2
        z_alpha = C.norm_ppf(1 - alpha / (2 if two_sided else 1))
        z_beta = C.norm_ppf(power)
        num = (z_alpha * math.sqrt(2 * pbar * (1 - pbar)) + z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
        n = math.ceil(num / (mde ** 2))
        basis = "two-proportion"
    else:
        if effect_size is None:
            raise SkillToolError("BAD_ARGUMENT", "Provide --effect-size (Cohen's d) or --baseline-rate with --mde.",
                                 "Example: --effect-size 0.2 --power 0.8 --alpha 0.05")
        if effect_size <= 0:
            raise SkillToolError("BAD_ARGUMENT", "effect_size must be positive.")
        z_alpha = C.norm_ppf(1 - alpha / (2 if two_sided else 1))
        z_beta = C.norm_ppf(power)
        n = math.ceil(((z_alpha + z_beta) / effect_size) ** 2)
        basis = "two-sample-means"
    adjusted = n
    if finite_population:
        adjusted = math.ceil(n / (1 + (n - 1) / finite_population))
    return {"basis": basis, "per_group_n": n, "total_n": n * 2,
            "finite_population_adjusted_total": adjusted * 2 if finite_population else None,
            "power": power, "alpha": alpha, "effect_size": effect_size, "mde": mde,
            "caveat": "Assumes independent observations and no peeking; sequential testing inflates false positives."}


def control_chart(values, subgroup=1, sigma_rule=3, label=""):
    vals = C.nums(values)
    if len(vals) < 5:
        raise SkillToolError("INSUFFICIENT_DATA", "Control chart needs at least 5 numeric points.")
    if subgroup > 1:
        groups = [vals[i:i + subgroup] for i in range(0, len(vals) - subgroup + 1, subgroup)]
        points = [C.mean(g) for g in groups]
        n = subgroup
    else:
        points = vals
        n = 1
    center = C.mean(points)
    sd = C.stdev(points) if n == 1 else C.stdev(points) / math.sqrt(n)
    ucl, lcl = center + sigma_rule * sd, center - sigma_rule * sd
    flags = []
    for i, v in enumerate(points):
        if v > ucl or v < lcl:
            flags.append({"index": i + 1, "value": v, "rule": "beyond_control_limit"})
    if len(points) >= 9:
        for i in range(8, len(points)):
            window = points[i - 8:i + 1]
            if all(x > center for x in window) or all(x < center for x in window):
                flags.append({"index": i + 1, "value": points[i], "rule": "nine_points_one_side"})
    return {"label": label, "center_line": center, "ucl": ucl, "lcl": lcl, "sigma": sd,
            "points": points, "signals": flags, "signal_count": len(flags),
            "note": "Random-looking variation is expected; a signal means investigate the process, not the point."}


def monte_carlo(iterations=10000, seed=42, variables=None, formula="sum", buckets=20):
    """variables: [{"name":"revenue","dist":"normal","mean":100,"sd":15}]"""
    if not variables:
        raise SkillToolError("BAD_ARGUMENT", "Provide --variables with distribution definitions.",
                             'Example: [{"name":"units","dist":"normal","mean":1000,"sd":120}]')
    if iterations < 100 or iterations > 2_000_000:
        raise SkillToolError("BAD_ARGUMENT", "iterations must be between 100 and 2,000,000.")
    rng = random.Random(seed)
    results = []
    for _ in range(iterations):
        vals = {}
        for v in variables:
            dist = v.get("dist", "normal")
            if dist == "normal":
                vals[v["name"]] = rng.gauss(v["mean"], v["sd"])
            elif dist == "lognormal":
                mu, sigma = v["mean"], v["sd"]
                vals[v["name"]] = rng.lognormvariate(math.log(max(mu, 1e-9)), sigma)
            elif dist == "uniform":
                vals[v["name"]] = rng.uniform(v["low"], v["high"])
            elif dist == "triangular":
                vals[v["name"]] = rng.triangular(v["low"], v.get("mode", (v["low"] + v["high"]) / 2), v["high"])
            elif dist == "bernoulli":
                vals[v["name"]] = 1.0 if rng.random() < v["p"] else 0.0
            elif dist == "poisson":
                lam = v["lambda"]
                # Knuth
                L, k, p = math.exp(-lam), 0, 1.0
                while True:
                    k += 1
                    p *= rng.random()
                    if p <= L:
                        break
                vals[v["name"]] = float(k - 1)
            elif dist == "constant":
                vals[v["name"]] = float(v["value"])
            else:
                raise SkillToolError("BAD_ARGUMENT", f"Unknown distribution {dist!r}.",
                                     "Use normal, lognormal, uniform, triangular, bernoulli, poisson or constant.")
        if formula == "sum":
            results.append(sum(vals.values()))
        elif formula == "product":
            out = 1.0
            for x in vals.values():
                out *= x
            results.append(out)
        else:
            raise SkillToolError("BAD_ARGUMENT", f"Unknown aggregation {formula!r}.", "Use sum or product.")
    results.sort()
    hist = {}
    lo, hi = results[0], results[-1]
    width = (hi - lo) / buckets if hi > lo else 1.0
    for v in results:
        b = int((v - lo) / width) if width else 0
        b = min(b, buckets - 1)
        hist[b] = hist.get(b, 0) + 1
    histogram = [{"lower": lo + i * width, "upper": lo + (i + 1) * width, "count": hist.get(i, 0)} for i in range(buckets)]
    return {
        "iterations": iterations, "seed": seed, "variables": variables,
        "mean": C.mean(results), "median": C.median(results), "stdev": C.stdev(results),
        "p10": C.percentile(results, 0.10), "p50": C.percentile(results, 0.50),
        "p90": C.percentile(results, 0.90), "p95": C.percentile(results, 0.95),
        "min": results[0], "max": results[-1],
        "histogram": histogram,
        "note": "Independent sampling unless correlations are encoded in the formula; document inputs and seed.",
    }


def forecast(values, periods=3, method="auto", seasonality=None):
    vals = C.nums(values)
    if len(vals) < 3:
        raise SkillToolError("INSUFFICIENT_DATA", "Forecast needs at least 3 historical points.")
    n = len(vals)
    chosen = method.lower()
    fitted = []
    if chosen == "auto":
        chosen = "holt" if n >= 6 else "linear"
    if chosen == "naive":
        fc = [vals[-1]] * periods
        fitted = vals[:-1]
    elif chosen == "seasonal_naive":
        s = int(seasonality or 12)
        if n < s + 1:
            raise SkillToolError("INSUFFICIENT_DATA", f"Seasonal naive needs at least {s + 1} points.")
        fc = [vals[n - s + (i % s)] for i in range(periods)]
        fitted = vals[s:]
    elif chosen == "mean":
        m = C.mean(vals)
        fc = [m] * periods
        fitted = [m] * n
    elif chosen == "linear":
        X = [[1.0, float(i)] for i in range(n)]
        res = C.ols(X, [float(v) for v in vals])
        a, b = res["coefficients"]
        fc = [a + b * (n + i) for i in range(periods)]
        fitted = res["fitted"]
    elif chosen == "holt":
        alpha, beta = 0.4, 0.2
        level, trend = vals[0], vals[1] - vals[0]
        fitted = [vals[0]]
        for i in range(1, n):
            prev_level = level
            level = alpha * vals[i] + (1 - alpha) * (level + trend)
            trend = beta * (level - prev_level) + (1 - beta) * trend
            fitted.append(level)
        fc = [level + (i + 1) * trend for i in range(periods)]
    elif chosen == "drift":
        drift = (vals[-1] - vals[0]) / (n - 1)
        fc = [vals[-1] + drift * (i + 1) for i in range(periods)]
        fitted = vals
    else:
        raise SkillToolError("BAD_ARGUMENT", f"Unknown forecast method {chosen!r}.",
                             "Use naive, seasonal_naive, mean, linear, holt or drift.")
    acc = _accuracy([v for v in vals[len(vals) - len(fitted):]], fitted)
    return {"method": chosen, "history_points": n, "periods": periods,
            "forecast": fc, "fitted": fitted[-20:], "in_sample_accuracy": acc,
            "caveat": "Forecasts are projections from historical pattern only; no causal drivers or shocks are modeled here."}


def _accuracy(actual, predicted):
    pairs = [(a, p) for a, p in zip(actual, predicted) if a is not None and p is not None]
    if not pairs:
        return {}
    errs = [a - p for a, p in pairs]
    mae = sum(abs(e) for e in errs) / len(errs)
    rmse = math.sqrt(sum(e * e for e in errs) / len(errs))
    mape_vals = [abs(e) / abs(a) for e, (a, _) in zip(errs, pairs) if a]
    mape = sum(mape_vals) / len(mape_vals) if mape_vals else None
    return {"n": len(pairs), "mae": mae, "rmse": rmse, "mape": mape,
            "bias": sum(errs) / len(errs),
            "note": "MAPE is undefined when actuals are zero; bias sign shows systematic over/under-forecasting."}


def forecast_accuracy(actuals, predictions):
    a, p = C.nums(actuals), C.nums(predictions)
    if len(a) != len(p):
        raise SkillToolError("BAD_ARGUMENT", f"Length mismatch: {len(a)} actuals vs {len(p)} predictions.")
    return _accuracy(a, p)


# ==========================================================================
# segmentation / mining
# ==========================================================================


def rfm(table, customer_column, date_column, amount_column, as_of=None, quintiles=5):
    ci = C.column_index(table, customer_column)
    di = C.column_index(table, date_column)
    ai = C.column_index(table, amount_column)
    as_of_date = C.parse_date(as_of) if as_of else None
    agg = {}
    skipped = 0
    for row in table["rows"]:
        d = C.parse_date(row[di])
        amt = C.to_number(row[ai])
        cust = row[ci]
        if not d or amt is None or C.is_empty(cust):
            skipped += 1
            continue
        agg.setdefault(str(cust), {"last": d, "freq": 0, "monetary": 0.0})
        rec = agg[str(cust)]
        rec["freq"] += 1
        rec["monetary"] += float(amt)
        if d > rec["last"]:
            rec["last"] = d
    if not agg:
        raise SkillToolError("INSUFFICIENT_DATA", "No usable customer/date/amount rows for RFM.")
    if as_of_date is None:
        as_of_date = max(v["last"] for v in agg.values())
    rows = []
    for cust, rec in agg.items():
        recency = (as_of_date - rec["last"]).days
        rows.append({"customer": cust, "recency_days": recency, "frequency": rec["freq"],
                     "monetary": C.round_half_up(rec["monetary"], 2), "last_purchase": rec["last"].isoformat()})

    def score(field, reverse):
        vals = [r[field] for r in rows]
        ranked = sorted(set(vals), reverse=reverse)
        buckets = max(1, len(ranked))
        for r in rows:
            pos = ranked.index(r[field])
            r[f"{field[:3]}_score"] = min(quintiles, max(1, int(math.floor((buckets - pos - 1) / buckets * quintiles)) + 1))

    score("recency_days", reverse=True)   # lower recency = better
    score("frequency", reverse=False)
    score("monetary", reverse=False)
    for r in rows:
        r["rfm_score"] = f"{r['rec_score']}{r['fre_score']}{r['mon_score']}"
        total = r["rec_score"] + r["fre_score"] + r["mon_score"]
        r["segment"] = ("champions" if total >= quintiles * 2.4 else
                        "loyal" if total >= quintiles * 1.9 else
                        "potential" if total >= quintiles * 1.4 else
                        "at_risk" if r["rec_score"] <= 2 else "needs_attention")
    rows.sort(key=lambda r: (-r["monetary"]))
    return {"as_of": as_of_date.isoformat(), "customers": len(rows), "rows": rows,
            "skipped_rows": skipped,
            "segments": _tally(rows, "segment"),
            "note": "Scores are relative to this dataset only; recalibrate before comparing across periods."}


def _tally(rows, field):
    out = {}
    for r in rows:
        out[r[field]] = out.get(r[field], 0) + 1
    return out


def cohort(table, customer_column, date_column, value_column=None, period="month", max_periods=12):
    """Retention matrix: distinct customers active in each period after their first period."""
    ci = C.column_index(table, customer_column)
    di = C.column_index(table, date_column)
    vi = C.column_index(table, value_column) if value_column else None
    first = {}
    events = []
    for row in table["rows"]:
        d = C.parse_date(row[di])
        if not d:
            continue
        cust = str(row[ci])
        events.append((cust, d, C.to_number(row[vi]) if vi is not None else 1.0))
        if cust not in first or d < first[cust]:
            first[cust] = d
    if not events:
        raise SkillToolError("INSUFFICIENT_DATA", "No rows with readable dates for cohort analysis.")
    cohort_key = {c: D._period_key(d, period) for c, d in first.items()}
    all_periods = sorted({D._period_key(d, period) for _, d, _ in events})
    period_index = {k: i for i, k in enumerate(all_periods)}
    members = {}
    active = {}
    for cust, d, val in events:
        ck = cohort_key[cust]
        members.setdefault(ck, set()).add(cust)
        offset = period_index[D._period_key(d, period)] - period_index[ck]
        if 0 <= offset <= max_periods:
            active.setdefault(ck, {}).setdefault(offset, set()).add(cust)
    rows = []
    for ck in sorted(members):
        base = len(members[ck])
        row = {"cohort": ck, "cohort_size": base}
        for p in range(max_periods + 1):
            users = active.get(ck, {}).get(p, set())
            row[f"p{p}"] = len(users)
            row[f"p{p}_pct"] = round(len(users) / base, 4) if base else 0.0
        rows.append(row)
    cols = ["cohort", "cohort_size"] + [f"p{p}" for p in range(max_periods + 1)] + \
           [f"p{p}_pct" for p in range(max_periods + 1)]
    return {"period": period, "max_periods": max_periods, "cohorts": rows,
            "table": C.make_table(cols, [[r[c] for c in cols] for r in rows]),
            "note": "Cohorts are calendar periods of first activity; later gaps in activity are not netted off."}


def basket(table, transaction_column, item_column, min_support=0.01, max_pairs=200):
    ti = C.column_index(table, transaction_column)
    ii = C.column_index(table, item_column)
    baskets = {}
    for row in table["rows"]:
        baskets.setdefault(str(row[ti]), set()).add(str(row[ii]))
    n = len(baskets)
    if n == 0:
        raise SkillToolError("INSUFFICIENT_DATA", "No transactions found.")
    item_counts = {}
    for items in baskets.values():
        for it in items:
            item_counts[it] = item_counts.get(it, 0) + 1
    pair_counts = {}
    for items in baskets.values():
        lst = sorted(items)
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                pair_counts[(lst[i], lst[j])] = pair_counts.get((lst[i], lst[j]), 0) + 1
    results = []
    for (a, b), cnt in pair_counts.items():
        support = cnt / n
        if support < min_support:
            continue
        conf_ab = cnt / item_counts[a]
        conf_ba = cnt / item_counts[b]
        lift = support / ((item_counts[a] / n) * (item_counts[b] / n))
        results.append({"item_a": a, "item_b": b, "support": support, "confidence_a_to_b": conf_ab,
                        "confidence_b_to_a": conf_ba, "lift": lift, "count": cnt})
    results.sort(key=lambda r: (-r["lift"], -r["support"]))
    return {"transactions": n, "distinct_items": len(item_counts), "pairs": results[:max_pairs],
            "pair_count": len(results), "min_support": min_support,
            "note": "Lift > 1 means items co-occur more than chance; validate with a holdout period before acting."}


def score_records(table, weights, output_column="score", transform=None, clamp=None):
    """Transparent linear scorecard. weights: {column: weight}."""
    specs = []
    for col, w in weights.items():
        ci = C.column_index(table, col)
        vals = [C.to_number(r[ci]) for r in table["rows"]]
        clean = [v for v in vals if v is not None]
        lo, hi = (min(clean), max(clean)) if clean else (0, 1)
        specs.append({"column": col, "index": ci, "weight": float(w), "min": lo, "max": hi})
    cols = list(table["columns"]) + [output_column] + [f"{s['column']}_scaled" for s in specs]
    rows = []
    for row in table["rows"]:
        score = 0.0
        extras = []
        for s in specs:
            v = C.to_number(row[s["index"]])
            if v is None:
                scaled = 0.0
            else:
                rng = s["max"] - s["min"]
                scaled = (v - s["min"]) / rng if rng else 1.0
            if transform == "log":
                scaled = math.log1p(max(0.0, scaled))
            score += s["weight"] * scaled
            extras.append(C.round_half_up(scaled, 6))
        if clamp:
            score = max(clamp[0], min(clamp[1], score))
        rows.append(list(row) + [C.round_half_up(score, 6)] + extras)
    return {"table": C.make_table(cols, rows),
            "weights": weights,
            "normalisation": "min-max across the supplied dataset",
            "note": "Scores rank records for human review. They do not make eligibility, credit or employment decisions."}


def assumption_audit(table, columns):
    out = []
    for name in columns:
        ci = C.column_index(table, name)
        raw = [r[ci] for r in table["rows"]]
        vals = C.nums(raw)
        missing = sum(1 for v in raw if C.is_empty(v))
        notes = []
        if len(vals) < 30:
            notes.append("Fewer than 30 observations: distributional tests are unreliable.")
        if missing:
            notes.append(f"{missing} missing values ({missing / len(raw):.1%}); check whether missingness is informative.")
        if vals:
            sd = C.stdev(vals)
            sk = C.skew(vals)
            if sk is not None and abs(sk) > 1:
                notes.append(f"Skew {sk:.2f}: consider a transform or non-parametric test.")
            if sd and C.mean(vals):
                cv = sd / C.mean(vals)
                if cv > 1.5:
                    notes.append(f"Coefficient of variation {cv:.2f} is high; variance assumptions may not hold.")
            if len(set(vals)) == 1:
                notes.append("Constant column: contributes no information.")
        out.append({"column": name, "n": len(vals), "missing": missing, "notes": notes})
    return {"columns": out,
            "verdict": "review_required" if any(c["notes"] for c in out) else "assumptions_plausible"}
