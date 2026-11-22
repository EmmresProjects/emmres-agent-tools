"""Visualization engine: dependency-free SVG charts, workbook dashboards, integrity audit.

SVG output renders anywhere (browser, image viewer, embed in HTML) without any
library. Workbook dashboards use native Excel charts so they stay editable.

Covers catalog categories 11 (Charts, Graphs and Data Visualization) and
12 (Dashboard and KPI Systems) where it touches rendering.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from xml.sax.saxutils import escape


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

PALETTE = ["#1f4e79", "#2e75b6", "#548235", "#bf8f00", "#c00000", "#7030a0",
           "#0f7b6c", "#8c4a1f", "#5b6573", "#a6a6a6"]

CHART_KINDS = {"column", "bar", "stacked_column", "line", "area", "scatter", "pie", "donut", "waterfall"}


def _nice_max(v):
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    base = 10 ** exp
    for mult in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= base * mult:
            return base * mult
    return base * 10


def _fmt(v):
    if v is None:
        return ""
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"{v / 1_000:.1f}k"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:.2f}"
    return f"{v:,.0f}"


def svg_chart(table, kind="column", x=None, y=None, title="", subtitle="",
              width=920, height=520, palette=None, y_zero=True, show_values=True,
              x_label="", y_label="", footnote="", horizontal=None):
    kind = (kind or "column").lower()
    if kind not in CHART_KINDS:
        raise SkillToolError("BAD_ARGUMENT", f"Unknown chart kind {kind!r}.", "Use: " + ", ".join(sorted(CHART_KINDS)))
    colors = list(palette or PALETTE)
    cols = table["columns"]
    if not cols:
        raise SkillToolError("EMPTY_INPUT", "Cannot chart an empty table.")
    if x is None:
        x = cols[0]
    if y is None:
        candidates = [c for c in cols if c != x and _numeric_share(table, c) >= 0.8]
        if not candidates:
            raise SkillToolError("NO_NUMERIC_COLUMNS", "No numeric column available for the value axis.",
                                 "Pass --y with a numeric column.")
        y = candidates[0]
        if len(candidates) > 1:
            y = "-".join(candidates)
    if isinstance(y, list):
        series = y
    elif "," in str(y):
        series = [token.strip() for token in str(y).split(",") if token.strip()]
    else:
        series = [token.strip() for token in str(y).split("-") if token.strip()]
    for s in series:
        C.column_index(table, s)
    xi = C.column_index(table, x)
    series_idx = [C.column_index(table, s) for s in series]
    raw_points = []
    for row in table["rows"]:
        label = "" if C.is_empty(row[xi]) else str(row[xi])
        vals = [C.to_number(row[i]) for i in series_idx]
        if all(v is None for v in vals):
            continue
        raw_points.append((label, vals))
    # aggregate repeated categories so a bar per row never masquerades as a category
    aggregated = False
    labels_seen = [p[0] for p in raw_points]
    if len(labels_seen) != len(set(labels_seen)) and kind not in {"scatter"}:
        order, bucket = [], {}
        for label, vals in raw_points:
            if label not in bucket:
                bucket[label] = [0.0] * len(series)
                order.append(label)
            for i, v in enumerate(vals):
                if v is not None:
                    bucket[label][i] += v
        points = [(label, bucket[label]) for label in order]
        aggregated = True
    else:
        points = raw_points
    if not points:
        raise SkillToolError("EMPTY_INPUT", "No numeric data points to plot.")
    if len(points) > 400 and kind not in {"line", "area", "scatter"}:
        raise SkillToolError("TOO_MANY_CATEGORIES",
                             f"{len(points)} categories is too many for a {kind} chart.",
                             "Aggregate first (aggregate command) or use a line chart.")

    # geometry
    pad_l, pad_r, pad_t, pad_b = 84, 40, 96 if title else 56, 96
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    out = []
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
               f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title or kind + " chart")}">')
    out.append(f'<rect width="{width}" height="{height}" fill="#ffffff"/>')
    out.append(f'<style>text{{font-family:Inter,Segoe UI,Arial,Helvetica,sans-serif;fill:#1b1f24;}}'
               f'.axis{{stroke:#c9d1d9;stroke-width:1;}} .grid{{stroke:#eef1f4;stroke-width:1;}}'
               f'.lbl{{font-size:12px;}} .ttl{{font-size:20px;font-weight:600;}} .sub{{font-size:13px;fill:#5b6573;}}'
               f'.note{{font-size:11px;fill:#6b7480;}} .val{{font-size:11px;font-weight:600;}}</style>')
    if title:
        out.append(f'<text class="ttl" x="{pad_l}" y="38">{escape(title)}</text>')
    if subtitle:
        out.append(f'<text class="sub" x="{pad_l}" y="60">{escape(subtitle)}</text>')

    # value axis domain
    if kind == "scatter":
        flat = [vals[1] for _, vals in points if len(vals) > 1 and vals[1] is not None] or [0.0]
    else:
        flat = [v for _, vals in points for v in vals if v is not None]
    if kind == "waterfall":
        flat = _waterfall_totals(flat)
    vmin, vmax = min(flat), max(flat)
    if kind in {"column", "bar", "stacked_column", "area", "waterfall"} and y_zero:
        vmin = min(0.0, vmin)
        vmax = max(0.0, vmax)
    if vmin == vmax:
        vmax = vmin + 1
    span = vmax - vmin
    ticks = 5
    ymin_t = vmin
    ymax_t = _nice_max(vmax) if vmax > 0 else 0.0
    if vmin < 0:
        ymin_t = -_nice_max(abs(vmin))
    tmin, tmax = ymin_t, ymax_t if ymax_t != ymin_t else ymin_t + 1

    def ypos(v):
        return pad_t + plot_h - (v - tmin) / (tmax - tmin) * plot_h

    def xpos(i):
        n = len(points)
        if kind in {"bar"}:
            return pad_l + plot_w / 2
        return pad_l + (i + 0.5) * plot_w / n

    is_cartesian = kind not in {"pie", "donut"}
    # grid and value axis
    for i in range(ticks + 1) if is_cartesian else []:
        v = tmin + (tmax - tmin) * i / ticks
        yp = ypos(v)
        out.append(f'<line class="grid" x1="{pad_l}" y1="{yp:.1f}" x2="{pad_l + plot_w}" y2="{yp:.1f}"/>')
        out.append(f'<text class="lbl" x="{pad_l - 10}" y="{yp + 4:.1f}" text-anchor="end">{escape(_fmt(v))}</text>')
    if is_cartesian:
        if tmin < 0 < tmax:
            out.append(f'<line class="axis" x1="{pad_l}" y1="{ypos(0):.1f}" '
                       f'x2="{pad_l + plot_w}" y2="{ypos(0):.1f}"/>')
        out.append(f'<line class="axis" x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}"/>')
    effective_y_label = y_label or (series[1] if kind == "scatter" and len(series) > 1 else "")
    if effective_y_label:
        out.append(f'<text class="lbl" x="{pad_l - 60}" y="{pad_t - 12}" '
                   f'text-anchor="start">{escape(effective_y_label)}</text>')
    if x_label:
        out.append(f'<text class="lbl" x="{pad_l + plot_w / 2}" y="{pad_t + plot_h + 46}" '
                   f'text-anchor="middle">{escape(x_label)}</text>')

    n = len(points)
    if kind in {"column", "stacked_column"}:
        slot = plot_w / n
        bar_w = slot * 0.62 / len(series)
        base = ypos(max(0.0, tmin)) if tmin < 0 else ypos(0)
        for i, (label, vals) in enumerate(points):
            if kind == "stacked_column":
                acc_pos, acc_neg = 0.0, 0.0
                for si, v in enumerate(vals):
                    if v is None:
                        continue
                    y0 = acc_pos if v >= 0 else acc_neg
                    acc = y0 + v
                    y_a, y_b = ypos(y0), ypos(acc)
                    out.append(f'<rect x="{pad_l + i * slot + slot * 0.19:.1f}" y="{min(y_a, y_b):.1f}" '
                               f'width="{slot * 0.62:.1f}" height="{abs(y_b - y_a):.1f}" fill="{colors[si % len(colors)]}">'
                               f'<title>{escape(label)} - {escape(series[si])}: {v:,.2f}</title></rect>')
                    if v >= 0:
                        acc_pos = acc
                    else:
                        acc_neg = acc
            else:
                for si, v in enumerate(vals):
                    if v is None:
                        continue
                    x0 = pad_l + i * slot + slot * 0.19 + si * bar_w
                    yv = ypos(v)
                    top = min(yv, base)
                    h = max(1.0, abs(base - yv))
                    out.append(f'<rect x="{x0:.1f}" y="{top:.1f}" width="{bar_w * 0.92:.1f}" height="{h:.1f}" '
                               f'fill="{colors[si % len(colors)]}" rx="2"><title>{escape(label)}'
                               f'{" - " + escape(series[si]) if len(series) > 1 else ""}: {v:,.2f}</title></rect>')
                    if show_values and n <= 14:
                        ty = top - 6 if v >= 0 else top + h + 14
                        out.append(f'<text class="val" x="{x0 + bar_w * 0.46:.1f}" y="{ty:.1f}" '
                                   f'text-anchor="middle">{escape(_fmt(v))}</text>')
    elif kind == "bar":
        slot = plot_h / n
        bar_h = slot * 0.6 / len(series)
        for i, (label, vals) in enumerate(points):
            for si, v in enumerate(vals):
                if v is None:
                    continue
                base = ypos(0)
                x_end = pad_l + (v - tmin) / (tmax - tmin) * plot_w
                y0 = pad_t + i * slot + slot * 0.2 + si * bar_h
                out.append(f'<rect x="{min(base, x_end):.1f}" y="{y0:.1f}" width="{abs(x_end - base):.1f}" '
                           f'height="{bar_h * 0.92:.1f}" fill="{colors[si % len(colors)]}" rx="2">'
                           f'<title>{escape(label)}: {v:,.2f}</title></rect>')
                if show_values and n <= 20:
                    out.append(f'<text class="val" x="{max(base, x_end) + 6:.1f}" y="{y0 + bar_h * 0.75:.1f}">'
                               f'{escape(_fmt(v))}</text>')
    elif kind in {"line", "area"}:
        for si, s in enumerate(series):
            path = []
            for i, (label, vals) in enumerate(points):
                v = vals[si]
                if v is None:
                    continue
                path.append(f"{xpos(i):.1f},{ypos(v):.1f}")
            if path:
                if kind == "area":
                    area = f"M {path[0].split(',')[0]},{ypos(max(0.0, tmin))} L " + " L ".join(path) + \
                           f" L {path[-1].split(',')[0]},{ypos(max(0.0, tmin))} Z"
                    out.append(f'<path d="{area}" fill="{colors[si % len(colors)]}" opacity="0.18"/>')
                out.append(f'<polyline points="{" ".join(path)}" fill="none" stroke="{colors[si % len(colors)]}" '
                           f'stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>')
                for i, (label, vals) in enumerate(points):
                    v = vals[si]
                    if v is None:
                        continue
                    out.append(f'<circle cx="{xpos(i):.1f}" cy="{ypos(v):.1f}" r="3.2" fill="#ffffff" '
                               f'stroke="{colors[si % len(colors)]}" stroke-width="2">'
                               f'<title>{escape(label)} - {escape(s)}: {v:,.2f}</title></circle>')
                if show_values and n <= 12:
                    for i, (label, vals) in enumerate(points):
                        v = vals[si]
                        if v is None:
                            continue
                        out.append(f'<text class="val" x="{xpos(i):.1f}" y="{ypos(v) - 10:.1f}" '
                                   f'text-anchor="middle">{escape(_fmt(v))}</text>')
    elif kind == "scatter":
        if len(series) < 2:
            raise SkillToolError("BAD_ARGUMENT", "Scatter needs two numeric columns: --y 'xcol,ycol' or --y xcol-ycol.",
                                 "Provide both columns separated by a comma or hyphen.")
        xs = [vals[0] for _, vals in points]
        ys = [vals[1] for _, vals in points]
        xs_clean = [v for v in xs if v is not None]
        ys_clean = [v for v in ys if v is not None]
        if xs_clean and ys_clean:
            xmin, xmax = min(xs_clean), max(xs_clean)
            ymin2, ymax2 = min(ys_clean), max(ys_clean)
            for i, (label, vals) in enumerate(points):
                if vals[0] is None or vals[1] is None:
                    continue
                px = pad_l + (vals[0] - xmin) / ((xmax - xmin) or 1) * plot_w
                py = pad_t + plot_h - (vals[1] - ymin2) / ((ymax2 - ymin2) or 1) * plot_h
                out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="{colors[0]}" opacity="0.72">'
                           f'<title>{escape(label)}: {vals[0]:,.2f}, {vals[1]:,.2f}</title></circle>')
            if len(xs_clean) > 2:
                r, _ = C.pearson(xs, ys)
                if r is not None:
                    out.append(f'<text class="note" x="{pad_l + plot_w - 6}" '
                               f'y="{pad_t + 16}" text-anchor="end">Pearson r = {r:.3f}</text>')
    elif kind in {"pie", "donut"}:
        vals = [v for _, vv in points for v in vv if v is not None]
        total = sum(vals)
        if total == 0:
            raise SkillToolError("DEGENERATE_DATA", "Pie chart values sum to zero.")
        cx, cy = pad_l + plot_w / 2, pad_t + plot_h / 2
        r = min(plot_w, plot_h) / 2 - 18
        angle = -math.pi / 2
        for i, (label, vv) in enumerate([(l, v[0]) for l, v in points]):
            v = vv
            if v is None or v == 0:
                continue
            frac = v / total
            end = angle + frac * 2 * math.pi
            x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
            x2, y2 = cx + r * math.cos(end), cy + r * math.sin(end)
            large = 1 if frac > 0.5 else 0
            d = (f"M {cx:.1f},{cy:.1f} L {x1:.1f},{y1:.1f} A {r:.1f},{r:.1f} 0 {large},1 {x2:.1f},{y2:.1f} Z")
            out.append(f'<path d="{d}" fill="{colors[i % len(colors)]}" stroke="#ffffff" stroke-width="1.5">'
                       f'<title>{escape(label)}: {v:,.2f} ({frac:.1%})</title></path>')
            mid = (angle + end) / 2
            label_r = r * (0.78 if kind == "donut" else 0.62)
            lx, ly = cx + label_r * math.cos(mid), cy + label_r * math.sin(mid)
            if frac >= 0.05:
                out.append(f'<text class="lbl" x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" '
                           f'style="fill:#ffffff;font-weight:600">{frac:.0%}</text>')
            angle = end
        if kind == "donut":
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r * 0.55:.1f}" fill="#ffffff"/>')
            out.append(f'<text x="{cx:.1f}" y="{cy + 6:.1f}" text-anchor="middle" '
                       f'style="font-size:18px;font-weight:600;fill:#1b1f24">'
                       f'{escape(_fmt(total))}</text>')
    elif kind == "waterfall":
        slot = plot_w / n
        running = 0.0
        for i, (label, vals) in enumerate(points):
            v = vals[0]
            if v is None:
                continue
            start, end = running, running + v
            y_a, y_b = ypos(start), ypos(end)
            colour = colors[2] if v >= 0 else colors[4]
            out.append(f'<rect x="{pad_l + i * slot + slot * 0.19:.1f}" y="{min(y_a, y_b):.1f}" '
                       f'width="{slot * 0.62:.1f}" height="{max(1.0, abs(y_b - y_a)):.1f}" fill="{colour}" rx="2">'
                       f'<title>{escape(label)}: {v:,.2f}</title></rect>')
            if n <= 18:
                out.append(f'<text class="val" x="{pad_l + i * slot + slot * 0.5:.1f}" '
                           f'y="{min(y_a, y_b) - 6:.1f}" text-anchor="middle">{escape(_fmt(v))}</text>')
            if i < n - 1:
                out.append(f'<line x1="{pad_l + i * slot + slot * 0.81:.1f}" y1="{y_b:.1f}" '
                           f'x2="{pad_l + (i + 1) * slot + slot * 0.19:.1f}" y2="{y_b:.1f}" '
                           f'stroke="#9aa5b1" stroke-dasharray="3,3"/>')
            running = end

    # category labels
    if kind not in {"bar", "scatter", "pie", "donut"}:
        max_labels = 14
        step = 1 if n <= max_labels else math.ceil(n / max_labels)
        limit = max(6, min(16, int(plot_w / max(1, n) / 6.4)))
        for i, (label, _) in enumerate(points):
            if i % step:
                continue
            y = pad_t + plot_h + 18
            text = label if len(label) <= limit else label[: limit - 1] + "\u2026"
            out.append(f'<text class="lbl" x="{xpos(i):.1f}" y="{y}" text-anchor="middle">'
                       f'{escape(text)}</text>')
    elif kind == "bar":
        for i, (label, _) in enumerate(points):
            out.append(f'<text class="lbl" x="{pad_l - 10}" y="{pad_t + i * (plot_h / n) + plot_h / n * 0.55:.1f}" '
                       f'text-anchor="end">{escape(label[:20])}</text>')

    # legend
    if kind == "scatter":
        out.append(f'<text class="lbl" x="{pad_l + plot_w / 2}" y="{pad_t + plot_h + 26}" '
                   f'text-anchor="middle">{escape(series[0] if series else "")} (x)</text>')
    if (len(series) > 1 and kind != "scatter") or kind in {"pie", "donut"}:
        items = series if len(series) > 1 else [l for l, _ in points][:10]
        lx = pad_l
        ly = pad_t + plot_h + (26 if kind in {"pie", "donut"} else 68)
        for i, name in enumerate(items):
            if lx > width - 180:
                lx = pad_l
                ly += 18
            out.append(f'<rect x="{lx}" y="{ly - 9}" width="11" height="11" fill="{colors[i % len(colors)]}"/>')
            out.append(f'<text class="lbl" x="{lx + 16}" y="{ly}">{escape(str(name)[:24])}</text>')
            lx += 22 + len(str(name)[:24]) * 6.6
    note_text = footnote
    if aggregated:
        note_text = (note_text + " | " if note_text else "") + "Repeated categories are summed."
    if note_text:
        out.append(f'<text class="note" x="{pad_l}" y="{height - 10}">{escape(note_text)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def _waterfall_totals(values):
    out = []
    running = 0.0
    for v in values:
        running += v
        out.extend([running, v])
    return out or [0.0]


def _numeric_share(table, name):
    ci = C.column_index(table, name)
    vals = [r[ci] for r in table["rows"] if not C.is_empty(r[ci])]
    if not vals:
        return 0.0
    return sum(1 for v in vals if C.to_number(v) is not None) / len(vals)


def viz_audit(spec):
    """Static checks for misleading or inaccessible charts.

    spec: {kind, title, y_axis_min, categories, series_count, units, color_only, labels}
    """
    issues = []
    kind = (spec.get("kind") or "column").lower()
    if not spec.get("title"):
        issues.append({"severity": "medium", "issue": "missing_title",
                       "detail": "Charts need a title that states the metric, unit and period."})
    if not spec.get("source_note"):
        issues.append({"severity": "low", "issue": "missing_source",
                       "detail": "Add a source/footnote so readers can trace the numbers."})
    ymin = spec.get("y_axis_min")
    if kind in {"bar", "column", "stacked_column", "area"} and ymin not in (None, 0, 0.0):
        issues.append({"severity": "high", "issue": "truncated_axis",
                       "detail": f"Bar-type charts must start at zero; y_axis_min is {ymin}."})
    if kind == "pie" and (spec.get("categories") or 0) > 7:
        issues.append({"severity": "medium", "issue": "excessive_slices",
                       "detail": "More than 7 slices is hard to read; group the tail into 'Other' or use a bar chart."})
    if (spec.get("categories") or 0) > 40:
        issues.append({"severity": "medium", "issue": "overplotting",
                       "detail": "Many categories: aggregate, facet, or switch to a line chart."})
    if spec.get("color_only"):
        issues.append({"severity": "high", "issue": "color_only_encoding",
                       "detail": "Add direct labels, patterns or text so the meaning survives grayscale printing."})
    if spec.get("inconsistent_units"):
        issues.append({"severity": "high", "issue": "mixed_units",
                       "detail": "Series on one axis must share the same unit; use two axes or separate charts."})
    if spec.get("missing_labels"):
        issues.append({"severity": "medium", "issue": "unlabelled_axes",
                       "detail": "Label both axes with the metric name and unit."})
    if spec.get("dual_axis_without_note"):
        issues.append({"severity": "low", "issue": "dual_axis",
                       "detail": "Dual axes invite false correlations; note the choice in the subtitle."})
    if not spec.get("alt_text"):
        issues.append({"severity": "low", "issue": "missing_alt_text",
                       "detail": "Accessible charts need a text alternative describing the key takeaway."})
    score = max(0, 100 - sum({"high": 25, "medium": 10, "low": 5}[i["severity"]] for i in issues))
    return {"issues": issues, "issue_count": len(issues), "integrity_score": score,
            "verdict": "pass" if score >= 80 else "revise"}


# ==========================================================================
# workbook dashboards (native Excel objects)
# ==========================================================================


def build_dashboard(path, kpis, data_sheets, title="Dashboard", palette=None, theme=None, overwrite=False):
    """kpis: [{"label","value","format","delta","status"}]; data_sheets: [{"name","table","chart":{"kind","x","y"}}]"""
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, PieChart, Reference
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    colors = list(palette or PALETTE)
    wb = Workbook()
    dash = wb.active
    dash.title = "Dashboard"
    ink = (theme or {}).get("ink", "1B1F24")
    muted = (theme or {}).get("muted", "5B6573")
    header_fill = (theme or {}).get("header_fill", "1F4E79")
    card_fill = (theme or {}).get("card_fill", "F2F6FA")

    dash["A1"] = title
    dash["A1"].font = Font(size=20, bold=True, color=ink)
    dash["A2"] = "KPI status reflects the definition, target and tolerance supplied in the build spec."
    dash["A2"].font = Font(size=10, color=muted)

    # KPI cards: 4 per row
    col = 1
    row = 4
    for i, kpi in enumerate(kpis):
        if i and i % 4 == 0:
            row += 4
            col = 1
        label_cell = dash.cell(row=row, column=col, value=str(kpi.get("label", f"KPI {i + 1}")))
        label_cell.font = Font(size=9, bold=True, color=muted)
        val_cell = dash.cell(row=row + 1, column=col, value=kpi.get("value"))
        val_cell.font = Font(size=18, bold=True, color=ink)
        if kpi.get("format"):
            val_cell.number_format = kpi["format"]
        status = str(kpi.get("status", "") or "").lower()
        status_cell = dash.cell(row=row + 2, column=col,
                                value={"behind": "▼ behind", "ahead": "▲ ahead", "on_target": "● on target"}.get(status, kpi.get("delta", "")))
        status_cell.font = Font(size=10, bold=True,
                                color={"behind": "C00000", "ahead": "548235", "on_target": "1F4E79"}.get(status, muted))
        for r in (row, row + 1, row + 2):
            dash.cell(row=r, column=col).fill = PatternFill("solid", fgColor=card_fill)
            dash.cell(row=r, column=col).alignment = Alignment(horizontal="left", vertical="center")
        dash.merge_cells(start_row=row, start_column=col, end_row=row, end_column=col + 1)
        dash.merge_cells(start_row=row + 1, start_column=col, end_row=row + 1, end_column=col + 1)
        dash.merge_cells(start_row=row + 2, start_column=col, end_row=row + 2, end_column=col + 1)
        col += 2

    chart_row = row + 6
    for sheet_i, spec in enumerate(data_sheets):
        name = str(spec["name"])[:31]
        ws = wb.create_sheet(name)
        table = spec["table"]
        for ci, cname in enumerate(table["columns"], start=1):
            cell = ws.cell(row=1, column=ci, value=cname)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=header_fill)
        for ri, rowvals in enumerate(table["rows"], start=2):
            for ci, v in enumerate(rowvals, start=1):
                ws.cell(row=ri, column=ci, value=v)
        ws.freeze_panes = "A2"
        for ci in range(1, len(table["columns"]) + 1):
            ws.column_dimensions[get_column_letter(ci)].width = 16
        chart_spec = spec.get("chart")
        if chart_spec:
            kind = (chart_spec.get("kind") or "column").lower()
            xcol = chart_spec.get("x") or table["columns"][0]
            ycol = chart_spec.get("y") or next((c for c in table["columns"] if _numeric_share(table, c) >= 0.8), None)
            if ycol:
                xi = C.column_index(table, xcol) + 1
                yi = C.column_index(table, ycol) + 1
                n_rows = len(table["rows"]) + 1
                if kind in {"line", "area"}:
                    chart = LineChart()
                elif kind in {"pie", "donut"}:
                    chart = PieChart()
                else:
                    chart = BarChart()
                    chart.type = "col" if kind != "bar" else "bar"
                chart.title = chart_spec.get("title") or f"{ycol} by {xcol}"
                chart.height = chart_spec.get("height", 8)
                chart.width = chart_spec.get("width", 16)
                data = Reference(ws, min_col=yi, min_row=1, max_row=n_rows)
                cats = Reference(ws, min_col=xi, min_row=2, max_row=n_rows)
                chart.add_data(data, titles_from_data=True)
                chart.set_categories(cats)
                if kind not in {"line", "area"}:
                    chart.y_axis.majorGridlines = None
                anchor = chart_spec.get("anchor") or _grid_anchor(sheet_i)
                dash.add_chart(chart, anchor)
        dash.cell(row=3, column=1, value="")
    dash.freeze_panes = "A3"
    dash.sheet_view.showGridLines = False
    for i in range(1, 13):
        dash.column_dimensions[get_column_letter(i)].width = 14
    dash.page_setup.orientation = "landscape"
    dash.page_setup.fitToWidth = 1
    dash.sheet_properties.pageSetUpPr.fitToPage = True
    last_row = row + 6 + 16 * ((len(data_sheets) + 1) // 2)
    dash.print_area = f"A1:R{max(24, last_row)}"
    wb.save(str(path))
    return {"file": Path(path).name, "sheets": wb.sheetnames, "kpis": len(kpis), "charts": len(data_sheets)}


def _grid_anchor(index):
    """Two charts per row starting at column F, 16 rows apart."""
    col = "F" if index % 2 == 0 else "R"
    row_no = 2 + (index // 2) * 17
    return f"{col}{row_no}"


def chart_selection_advisor(intent, categories=None, series=1, has_time=False):
    """Recommend a chart form from a stated analytical intent."""
    intent_l = (intent or "").lower()
    if any(w in intent_l for w in ["trend", "over time", "history", "seasonal"]):
        rec, why = "line", "Trend over time reads best as a line with a shared time axis."
    elif any(w in intent_l for w in ["compare", "ranking", "rank", "versus", "by product"]):
        rec, why = "bar", "Comparisons across categories are most accurate as bars."
    elif any(w in intent_l for w in ["composition", "share", "mix", "percentage of"]):
        rec, why = ("pie" if (categories or 5) <= 6 else "stacked_column"), \
            "Share of a whole: pie only when few slices, otherwise 100% stacked columns."
    elif any(w in intent_l for w in ["distribution", "spread", "histogram", "variation"]):
        rec, why = "column", "Distributions need a histogram-style column chart of binned counts."
    elif any(w in intent_l for w in ["relationship", "correlation", "driver"]):
        rec, why = "scatter", "Two continuous measures relate best as a scatter with a trend note."
    elif any(w in intent_l for w in ["bridge", "walk", "waterfall", "movement"]):
        rec, why = "waterfall", "Bridges need ordered increments from opening to closing."
    elif any(w in intent_l for w in ["variance", "budget", "actual vs"]):
        rec, why = "column", "Variance by period reads well as paired or diverging columns."
    else:
        rec, why = "column", "Default to columns for like-for-like categories with a zero baseline."
    cautions = []
    if series and series > 4 and rec in {"line", "column"}:
        cautions.append("More than 4 series on one axis is hard to read; facet or highlight one series.")
    if not has_time and rec == "line":
        cautions.append("A line implies continuity; categories without a time order should be bars.")
    return {"recommended": rec, "reason": why, "cautions": cautions,
            "always": ["Zero baseline for bars", "Units in the axis label", "Source and as-of date in the footnote"]}
