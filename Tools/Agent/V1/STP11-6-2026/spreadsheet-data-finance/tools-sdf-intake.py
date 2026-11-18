"""Intake, compatibility, inspection and catalog-parsing engine.

Covers catalog category 1 (Universal Spreadsheet Intake and Compatibility) and the
request-parsing side of the tool.

Loaded by tools-sdf.py; can also be exercised directly through that launcher.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET


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

TABULAR_EXT = {".csv", ".tsv", ".txt", ".psv"}
WORKBOOK_EXT = {".xlsx", ".xlsm", ".xltx", ".xltm"}
LEGACY_EXT = {".xls", ".xlsb", ".ods"}
OTHER_EXT = {".json", ".jsonl", ".ndjson", ".html", ".htm", ".xml"}

ENCODING_CANDIDATES = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]


# ==========================================================================
# text decoding and dialect inference
# ==========================================================================


def decode_bytes(raw: bytes):
    """Decode bytes with an explicit, reported encoding decision."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if raw.startswith(bom):
            try:
                return raw.decode(enc), enc, []
            except UnicodeDecodeError:
                pass
    for enc in ENCODING_CANDIDATES:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        warnings = []
        if enc in {"cp1252", "latin-1"}:
            warnings.append(
                f"Input was not valid UTF-8; decoded as {enc}. Non-ASCII characters may be ambiguous - "
                "confirm identifiers before use."
            )
        if "\ufffd" in text:
            warnings.append("Replacement characters detected; some bytes could not be decoded.")
        return text, enc, warnings
    raise SkillToolError("DECODE_FAILED", "Input bytes could not be decoded with any supported encoding.",
                         "Re-export the file as UTF-8 and retry.")


def sniff_dialect(text: str) -> dict:
    """Infer delimiter, quoting, header presence and line endings from sample text."""
    sample = text[:20000]
    lines = [ln for ln in sample.splitlines() if ln.strip()]
    counts = {d: 0 for d in [",", "\t", ";", "|"]}
    for line in lines[:50]:
        for d in counts:
            counts[d] += line.count(d)
    best = max(counts, key=lambda d: counts[d]) if any(counts.values()) else ","
    if counts[best] == 0:
        best = ","
    quote = '"'
    if sample.count("'") > sample.count('"') * 3 and sample.count("'") >= 2:
        quote = "'"
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines[:30]), delimiters=",;\t|")
        best = dialect.delimiter
        quote = dialect.quotechar or '"'
    except csv.Error:
        pass
    header = True
    if lines:
        try:
            header = csv.Sniffer().has_header("\n".join(lines[:30]))
        except csv.Error:
            first = lines[0].split(best)
            header = not all(re.fullmatch(r"[\d.,\-+%\s]*", c or "") for c in first)
    crlf = "\r\n" in sample
    return {
        "delimiter": best,
        "quotechar": quote,
        "doublequote": True,
        "escapechar": "\\" if '\\"' in sample else "",
        "has_header": bool(header),
        "line_ending": "CRLF" if crlf else "LF",
        "sample_lines": len(lines[:50]),
        "delimiter_counts": counts,
    }


def inspect_encoding(raw: bytes) -> dict:
    text, enc, warnings = decode_bytes(raw)
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    return {
        "encoding": enc,
        "decoded_chars": len(text),
        "non_ascii_chars": non_ascii,
        "has_bom": raw.startswith(b"\xef\xbb\xbf"),
        "replacement_chars": text.count("\ufffd"),
        "warnings": warnings,
        "line_ending": "CRLF" if "\r\n" in text[:20000] else "LF",
    }


# ==========================================================================
# tabular readers
# ==========================================================================


def _xlsx_table(path: Path, sheet=None, header=True, data_only=True, max_rows=None):
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover
        raise SkillToolError("MISSING_DEPENDENCY", "openpyxl is required to read .xlsx workbooks.",
                             "Install with: python -m pip install 'openpyxl>=3.1,<4'")

    warnings = []
    wb = load_workbook(filename=str(path), data_only=data_only, read_only=True)
    try:
        names = wb.sheetnames
        if sheet in (None, ""):
            target = names[0]
        else:
            if sheet not in names:
                raise SkillToolError("UNKNOWN_SHEET", f"Sheet {sheet!r} not found.",
                                     "Available sheets: " + ", ".join(names), {"sheets": names})
            target = sheet
        ws = wb[target]
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if max_rows and i >= max_rows:
                warnings.append(f"Row limit {max_rows} reached while reading sheet {target!r}.")
                break
            rows.append(list(row))
    finally:
        wb.close()

    while rows and all(C.is_empty(v) for v in rows[-1]):
        rows.pop()
    if not rows:
        return C.make_table([], [], {"sheet": target, "sheets": names}), warnings
    if header:
        cols = [("" if C.is_empty(v) else str(v).strip()) for v in rows[0]]
        # de-duplicate blank/duplicated headers
        seen = {}
        for i, c in enumerate(cols):
            base = c or f"column_{i + 1}"
            if base in seen:
                seen[base] += 1
                cols[i] = f"{base}_{seen[base]}"
                warnings.append(f"Duplicate header {base!r} renamed to {cols[i]!r}.")
            else:
                seen[base] = 0
                cols[i] = base
        body = rows[1:]
    else:
        cols = [f"column_{i + 1}" for i in range(len(rows[0]))]
        body = rows
    table = C.make_table(cols, body, {"sheet": target, "sheets": names, "source": path.name})
    return table, warnings


def _delimited_table(path: Path, delimiter=None, encoding=None, header=True,
                     decimal=".", thousands=",", max_rows=None, quotechar=None):
    raw = path.read_bytes()
    text, enc, warnings = decode_bytes(raw) if not encoding else (raw.decode(encoding), encoding, [])
    dialect = sniff_dialect(text)
    delim = delimiter or dialect["delimiter"]
    quote = quotechar or dialect["quotechar"]
    if delimiter:
        dialect["delimiter"] = delimiter
    reader = csv.reader(io.StringIO(text), delimiter=delim, quotechar=quote)
    rows = []
    bad_rows = 0
    width = None
    limit = max_rows or C.limits()["max_rows"]
    for i, row in enumerate(reader):
        if i >= limit:
            warnings.append(f"Row limit {limit} reached; file truncated.")
            break
        if width is None:
            width = len(row)
        elif len(row) != width:
            bad_rows += 1
            if len(row) < width:
                row = row + [""] * (width - len(row))
        rows.append(row)
    if not rows:
        raise SkillToolError("EMPTY_INPUT", f"No data rows found in {path.name}.", "Check delimiter and encoding.")
    if not header:
        rows = [[f"column_{i + 1}" for i in range(len(rows[0]))]] + rows
        dialect["has_header"] = False
    cols = [str(c).strip() for c in rows[0]]
    table = C.make_table(cols, rows[1:])
    table["meta"] = {
        "encoding": enc,
        "dialect": dialect,
        "malformed_rows_padded": bad_rows,
        "decimal": decimal,
        "thousands": thousands,
        "source": path.name,
        "sheets": [],
    }
    # locale-aware numeric promotion on obvious numeric columns
    promoted = _promote_numeric(table, decimal, thousands)
    table["meta"]["numeric_columns_promoted"] = promoted
    return table, warnings


def _promote_numeric(table, decimal=".", thousands=","):
    """Promote only unambiguous numeric columns; identifiers keep their text form."""
    promoted = []
    cols = table["columns"]
    for ci, name in enumerate(cols):
        values = [r[ci] for r in table["rows"] if not C.is_empty(r[ci])]
        if len(values) < 1:
            continue
        if C.looks_like_identifier(name, values):
            continue
        parsed = [C.parse_number_locale(v, decimal, thousands) for v in values]
        if sum(1 for _, ok in parsed if ok) == len(values):
            for r in table["rows"]:
                if not C.is_empty(r[ci]):
                    val = C.parse_number_locale(r[ci], decimal, thousands)[0]
                    if isinstance(val, float) and val.is_integer() and abs(val) < 1e15:
                        val = int(val)
                    r[ci] = val
            promoted.append(name)
    return promoted


def flatten_json(obj, sep=".", max_depth=12):
    """Flatten nested JSON into rows of scalar values (arrays repeat parent keys)."""
    records = []

    def walk(node, prefix, acc):
        if len(prefix.split(sep)) > max_depth:
            acc[prefix + sep + "__truncated"] = str(node)
            records.append(acc)
            return
        if isinstance(node, dict):
            if not node:
                records.append(dict(acc))
                return
            for k, v in node.items():
                walk(v, f"{prefix}{sep}{k}" if prefix else str(k), acc)
        elif isinstance(node, list):
            if not node:
                acc[prefix] = ""
                records.append(dict(acc))
            elif all(not isinstance(x, (dict, list)) for x in node):
                acc[prefix] = json.dumps(node, ensure_ascii=False)
                records.append(dict(acc))
            else:
                for item in node:
                    walk(item, prefix, dict(acc))
        else:
            acc[prefix] = node
            records.append(acc)

    walk(obj, "", {})
    if not records:
        return C.make_table([], [])
    cols = []
    for r in records:
        for k in r:
            if k not in cols:
                cols.append(k)
    return C.make_table(cols, [[r.get(c) for c in cols] for r in records])


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._table is None:
                self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(c for c in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None
            self._depth -= 1

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def html_tables(text: str):
    p = _TableParser()
    p.feed(text)
    return p.tables


def _html_table(path: Path, index=0, max_rows=None):
    raw = path.read_bytes()
    text, enc, warnings = decode_bytes(raw)
    tables = html_tables(text)
    if not tables:
        raise SkillToolError("NO_TABLE_FOUND", "No HTML <table> element found in the input.",
                             "Confirm the file contains a table; or convert it to CSV first.")
    if index >= len(tables):
        raise SkillToolError("BAD_ARGUMENT", f"Table index {index} out of range (found {len(tables)}).",
                             f"Use --sheet-index between 0 and {len(tables) - 1}.")
    rows = tables[index]
    if max_rows:
        rows = rows[:max_rows]
    cols = [c or f"column_{i + 1}" for i, c in enumerate(rows[0])]
    table = C.make_table(cols, rows[1:], {"encoding": enc, "tables_found": len(tables), "source": path.name})
    return table, warnings


def _xml_table(path: Path, max_rows=None):
    raw = path.read_bytes()
    text, enc, warnings = decode_bytes(raw)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SkillToolError("BAD_XML", f"XML could not be parsed: {exc}", "Repair the XML or export as CSV.")
    records = []
    for el in root.iter():
        if len(list(el)) and any(len(list(ch)) for ch in el):
            continue
        row = {}
        node = el
        path_parts = []
        while node is not None:
            path_parts.append(node.tag.split("}")[-1])
            node = None
        row["/".join(reversed(path_parts))] = (el.text or "").strip()
        for k, v in el.attrib.items():
            row[f"{'/'.join(reversed(path_parts))}@{k}"] = v
        if row:
            records.append(row)
        if max_rows and len(records) >= max_rows:
            break
    if not records:
        raise SkillToolError("EMPTY_INPUT", "XML contained no leaf values.", "Check the XML structure.")
    cols = []
    for r in records:
        for k in r:
            if k not in cols:
                cols.append(k)
    table = C.make_table(cols, [[r.get(c) for c in cols] for r in records], {"encoding": enc, "source": path.name})
    return table, warnings


def _json_table(path: Path, max_rows=None):
    raw = path.read_bytes()
    text, enc, warnings = decode_bytes(raw)
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        objs = []
        for i, line in enumerate(text.splitlines()):
            if not line.strip():
                continue
            try:
                objs.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SkillToolError("BAD_JSON", f"Line {i + 1} is not valid JSON: {exc}", "Fix the JSONL line.")
        obj = objs
    else:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SkillToolError("BAD_JSON", f"JSON could not be parsed: {exc}", "Fix the JSON syntax or pass a .jsonl file.")
    if isinstance(obj, dict):
        dict_values = [v for v in obj.values() if isinstance(v, list)]
        if len(obj) and len(dict_values) == len(obj) and all(
            all(not isinstance(x, (dict, list)) for x in v) for v in dict_values
        ):
            cols = list(obj.keys())
            n = max((len(v) for v in dict_values), default=0)
            rows = [[obj[c][i] if i < len(obj[c]) else None for c in cols] for i in range(n)]
            table = C.make_table(cols, rows, {"encoding": enc, "shape": "columnar", "source": path.name})
        elif isinstance(list(obj.values())[0] if obj else None, (dict, list)):
            table = flatten_json(obj)
            table["meta"].update({"encoding": enc, "shape": "flattened_object", "source": path.name})
        else:
            table = C.make_table(list(obj.keys()), [list(obj.values())], {"encoding": enc, "shape": "single_row", "source": path.name})
    elif isinstance(obj, list):
        table = flatten_json(obj)
        table["meta"].update({"encoding": enc, "shape": "array_of_objects", "source": path.name})
    else:
        table = C.make_table(["value"], [[obj]], {"encoding": enc, "source": path.name})
    if max_rows:
        table["rows"] = table["rows"][:max_rows]
        table["meta"]["row_limit"] = max_rows
    return table, warnings


def read_table(path, sheet=None, delimiter=None, encoding=None, header=True,
               decimal=".", thousands=",", html_index=0, max_rows=None):
    """Read any supported tabular source into a table dict. Returns (table, warnings)."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in WORKBOOK_EXT:
        return _xlsx_table(p, sheet=sheet, header=header, max_rows=max_rows)
    if ext in TABULAR_EXT:
        return _delimited_table(p, delimiter=delimiter, encoding=encoding, header=header,
                                decimal=decimal, thousands=thousands, max_rows=max_rows)
    if ext == ".json" or ext in {".jsonl", ".ndjson"}:
        return _json_table(p, max_rows=max_rows)
    if ext in {".html", ".htm"}:
        return _html_table(p, index=html_index, max_rows=max_rows)
    if ext == ".xml":
        return _xml_table(p, max_rows=max_rows)
    if ext in {".xls", ".xlsb", ".ods"}:
        raise SkillToolError(
            "LEGACY_FORMAT_UNSUPPORTED",
            f"The installed readers cannot open {ext} files directly.",
            "Convert with the 'convert' command (tools available depend on installed libraries), "
            "or export to .xlsx/.csv and retry.",
            {"extension": ext},
        )
    raise SkillToolError("UNSUPPORTED_FORMAT", f"Unsupported input extension {ext!r}.",
                         "Supported: " + ", ".join(sorted(WORKBOOK_EXT | TABULAR_EXT | LEGACY_EXT | OTHER_EXT)))


def table_to_csv_text(table, delimiter=",", quotechar='"', line_ending="\n") -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, quotechar=quotechar, lineterminator=line_ending, quoting=csv.QUOTE_MINIMAL)
    w.writerow(table["columns"])
    for row in table["rows"]:
        w.writerow(["" if v is None else v for v in row])
    return buf.getvalue()


# ==========================================================================
# workbook structure, integrity, links, hidden content
# ==========================================================================


def _parts(path: Path):
    try:
        with zipfile.ZipFile(path) as z:
            return z.namelist()
    except zipfile.BadZipFile:
        return []


def structure(path):
    """Inventory sheets, names, tables, formulas, charts, pivots, links, protection."""
    from openpyxl import load_workbook

    p = Path(path)
    ext = p.suffix.lower()
    if ext not in WORKBOOK_EXT:
        raise SkillToolError("UNSUPPORTED_FORMAT", "structure requires an .xlsx/.xlsm workbook.",
                            "Convert the file first with the 'convert' command.")
    wb = load_workbook(filename=str(p), data_only=False)
    sheets = []
    for ws in wb.worksheets:
        formulas = 0
        errors = 0
        merged = len(getattr(ws, "merged_cells", []).ranges) if getattr(ws, "merged_cells", None) else 0
        max_col = 0
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formulas += 1
                    if re.search(r"#(REF|VALUE|N/A|DIV/0|NAME\?|NUM|SPILL|null)", cell.value):
                        errors += 1
                if cell.column > max_col:
                    max_col = cell.column
        hidden_rows = [r for r, d in getattr(ws, "row_dimensions", {}).items() if d.hidden]
        hidden_cols = [c for c, d in getattr(ws, "column_dimensions", {}).items() if d.hidden]
        charts = len(getattr(ws, "_charts", []))
        images = len(getattr(ws, "_images", []))
        tables = list(getattr(ws, "tables", {}) or {})
        dv = len(getattr(ws, "data_validations", None).dataValidation if getattr(ws, "data_validations", None) else [])
        cf = len(list(ws.conditional_formatting)) if ws.conditional_formatting else 0
        comments = sum(
            1 for row in ws.iter_rows() for cell in row if getattr(cell, "comment", None) is not None
        )
        hyperlinks = sum(1 for row in ws.iter_rows() for cell in row if getattr(cell, "hyperlink", None) is not None)
        sheets.append({
            "name": ws.title,
            "state": ws.sheet_state,
            "dimensions": ws.dimensions,
            "max_row": ws.max_row,
            "max_column": max_col,
            "formula_cells": formulas,
            "formula_error_strings": errors,
            "merged_regions": merged,
            "hidden_rows": len(hidden_rows),
            "hidden_columns": len(hidden_cols),
            "charts": charts,
            "images": images,
            "tables": tables,
            "data_validations": dv,
            "conditional_format_ranges": cf,
            "comments": comments,
            "hyperlinks": hyperlinks,
            "frozen_panes": ws.freeze_panes,
            "protection": ws.protection.sheet,
            "print_area": ws.print_area,
        })
    defined = []
    try:
        for name, dn in wb.defined_names.items():
            defined.append({"name": name, "value": str(dn.value), "hidden": bool(getattr(dn, "hidden", False))})
    except Exception:
        pass
    parts = _parts(p)
    props = wb.properties
    data = {
        "file": p.name,
        "format": ext.lstrip("."),
        "workbook_protection": bool(wb.security),
        "sheet_count": len(sheets),
        "sheets": sheets,
        "named_ranges": defined,
        "external_link_parts": [x for x in parts if x.startswith("xl/externalLinks")],
        "data_connection_parts": [x for x in parts if "connections" in x or "queryTables" in x],
        "pivot_parts": [x for x in parts if "pivot" in x.lower()],
        "vba_project": next((x for x in parts if x.endswith("vbaProject.bin")), None),
        "properties": {
            "creator": getattr(props, "creator", None),
            "last_modified_by": getattr(props, "lastModifiedBy", None),
            "created": getattr(props, "created", None),
            "modified": getattr(props, "modified", None),
            "title": getattr(props, "title", None),
        },
        "package_parts": len(parts),
    }
    wb.close()
    data["totals"] = {
        "formula_cells": sum(s["formula_cells"] for s in sheets),
        "charts": sum(s["charts"] for s in sheets),
        "comments": sum(s["comments"] for s in sheets),
        "hidden_sheets": sum(1 for s in sheets if s["state"] != "visible"),
        "protected_sheets": sum(1 for s in sheets if s["protection"]),
        "tables": sum(len(s["tables"]) for s in sheets),
    }
    return data


def hidden_content(path):
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path), data_only=True)
    findings = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            findings.append({"kind": "sheet_state", "sheet": ws.title, "detail": ws.sheet_state})
        hidden_rows = [r for r, d in getattr(ws, "row_dimensions", {}).items() if d.hidden]
        if hidden_rows:
            findings.append({"kind": "hidden_rows", "sheet": ws.title,
                             "detail": f"{len(hidden_rows)} hidden rows", "sample": hidden_rows[:20]})
        hidden_cols = [c for c, d in getattr(ws, "column_dimensions", {}).items() if d.hidden]
        if hidden_cols:
            findings.append({"kind": "hidden_columns", "sheet": ws.title,
                             "detail": f"{len(hidden_cols)} hidden columns", "sample": hidden_cols[:20]})
        for row in ws.iter_rows():
            for cell in row:
                if getattr(cell, "comment", None) is not None:
                    findings.append({"kind": "comment", "sheet": ws.title, "cell": cell.coordinate,
                                     "detail": (cell.comment.text or "")[:200]})
    for name, dn in getattr(wb, "defined_names", {}).items():
        if getattr(dn, "hidden", False):
            findings.append({"kind": "hidden_name", "detail": name, "target": str(dn.value)})
    parts = _parts(Path(path))
    for part in parts:
        if "veryHidden" in part:
            findings.append({"kind": "very_hidden_part", "detail": part})
    wb.close()
    return {"findings": findings, "count": len(findings)}


def external_links(path):
    p = Path(path)
    from openpyxl import load_workbook

    findings = []
    wb = load_workbook(filename=str(p), data_only=False)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str):
                    if v.startswith("=") and "[" in v and "]" in v:
                        findings.append({"kind": "external_formula_reference", "sheet": ws.title,
                                         "cell": cell.coordinate, "detail": v[:200]})
                    if "http://" in v or "https://" in v:
                        findings.append({"kind": "url_in_cell", "sheet": ws.title, "cell": cell.coordinate,
                                         "detail": v[:200]})
                if getattr(cell, "hyperlink", None) is not None and cell.hyperlink.target:
                    tgt = cell.hyperlink.target
                    if tgt.startswith("http"):
                        findings.append({"kind": "hyperlink", "sheet": ws.title, "cell": cell.coordinate, "detail": tgt})
    for name, dn in getattr(wb, "defined_names", {}).items():
        val = str(dn.value)
        if "[" in val and "]" in val:
            findings.append({"kind": "external_name", "detail": name, "target": val})
    wb.close()
    parts = _parts(p)
    ext_parts = [x for x in parts if x.startswith("xl/externalLinks")]
    for part in ext_parts:
        try:
            with zipfile.ZipFile(p) as z:
                xml = z.read(part).decode("utf-8", "replace")
            for m in re.findall(r'Target="([^"]+)"', xml)[:20]:
                findings.append({"kind": "external_link_part", "detail": part, "target": m})
        except KeyError:
            continue
    risks = []
    for f in findings:
        if f["kind"] == "hyperlink" and f["detail"].lower().startswith("http"):
            risks.append("Outbound hyperlink present; verify the destination before distribution.")
    return {
        "findings": findings,
        "count": len(findings),
        "external_link_parts": ext_parts,
        "risk_notes": sorted(set(risks)),
        "verdict": "external_dependencies_present" if findings else "self_contained",
    }


def triage(path):
    """Diagnose damaged workbooks and produce a repair plan without mutating input."""
    p = Path(path)
    problems = []
    repair_steps = []
    zip_ok = zipfile.is_zipfile(p)
    if not zip_ok:
        problems.append({"part": "container", "issue": "not_a_zip", "detail": "File is not a valid OOXML ZIP container."})
        repair_steps.append("Open in a spreadsheet application and re-save as .xlsx, or restore from backup.")
        return {"container_ok": False, "problems": problems, "repair_plan": repair_steps, "recoverability": "low"}
    bad_parts = []
    xml_errors = []
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        try:
            crc = z.testzip()
            if crc:
                bad_parts.append(crc)
                problems.append({"part": crc, "issue": "crc_mismatch", "detail": "ZIP CRC check failed."})
        except Exception as exc:
            problems.append({"part": "container", "issue": "crc_check_failed", "detail": str(exc)})
        for name in names:
            if name.endswith(".xml") or name.endswith(".rels"):
                try:
                    ET.fromstring(z.read(name))
                except Exception as exc:
                    xml_errors.append({"part": name, "detail": str(exc)[:200]})
        present = set(names)
    for e in xml_errors:
        problems.append({"part": e["part"], "issue": "malformed_xml", "detail": e["detail"]})
    required = {
        "[Content_Types].xml": "missing_content_types",
        "xl/workbook.xml": "missing_workbook",
        "xl/_rels/workbook.xml.rels": "missing_workbook_rels",
    }
    for part, issue in required.items():
        if part not in present:
            problems.append({"part": part, "issue": issue, "detail": "Required package part is absent."})
    if "xl/styles.xml" not in present:
        problems.append({"part": "xl/styles.xml", "issue": "missing_styles", "detail": "Number formats may be lost."})
    if bad_parts or xml_errors:
        repair_steps += [
            "Copy the original file; never repair in place.",
            "Re-save through a spreadsheet application to regenerate XML parts.",
            "Recover values with a read-only pass (data_only) and rebuild formulas from documentation.",
        ]
    load_error = None
    try:
        from openpyxl import load_workbook

        wb = load_workbook(filename=str(p), data_only=True, read_only=True)
        n = len(wb.sheetnames)
        wb.close()
    except Exception as exc:
        load_error = f"{type(exc).__name__}: {exc}"[:300]
        problems.append({"part": "openpyxl_load", "issue": "load_failed", "detail": load_error})
        repair_steps.append("Use a streaming reader or export to CSV from the authoring application.")
    if not problems:
        repair_steps.append("No repair needed: container, XML parts and workbook load all validated.")
    score = 0
    if zip_ok:
        score += 40
    if not bad_parts:
        score += 20
    if not xml_errors:
        score += 20
    if load_error is None:
        score += 20
    return {
        "container_ok": zip_ok,
        "problems": problems,
        "problem_count": len(problems),
        "repair_plan": repair_steps,
        "recoverability_score": score,
        "recoverability": "high" if score >= 90 else "medium" if score >= 60 else "low",
        "sheet_count": n if load_error is None else None,
    }


def compare_workbooks(path_a, path_b, sample_limit=200):
    from openpyxl import load_workbook

    a = load_workbook(filename=str(path_a), data_only=False)
    b = load_workbook(filename=str(path_b), data_only=False)
    diffs = []
    only_a = [s for s in a.sheetnames if s not in b.sheetnames]
    only_b = [s for s in b.sheetnames if s not in a.sheetnames]
    for name in a.sheetnames:
        if name not in b.sheetnames:
            continue
        wa, wb_ = a[name], b[name]
        maxr = max(wa.max_row, wb_.max_row)
        maxc = max(wa.max_column, wb_.max_column)
        for r in range(1, maxr + 1):
            for c in range(1, maxc + 1):
                ca, cb = wa.cell(row=r, column=c), wb_.cell(row=r, column=c)
                va, vb = ca.value, cb.value
                if va == vb:
                    fmt_a = ca.number_format
                    fmt_b = cb.number_format
                    if fmt_a != fmt_b:
                        diffs.append({"sheet": name, "cell": ca.coordinate, "kind": "number_format",
                                      "from": fmt_a, "to": fmt_b})
                    continue
                kind = "value"
                if isinstance(va, str) and isinstance(vb, str) and va.startswith("=") and vb.startswith("="):
                    kind = "formula"
                elif (va is None) != (vb is None):
                    kind = "added" if va is None else "removed"
                diffs.append({"sheet": name, "cell": ca.coordinate, "kind": kind,
                              "from": va if not isinstance(va, str) else va[:200],
                              "to": vb if not isinstance(vb, str) else vb[:200]})
                if len(diffs) >= sample_limit * 20:
                    break
    parts_a = set(_parts(Path(path_a)))
    parts_b = set(_parts(Path(path_b)))
    summary = {}
    for d in diffs:
        summary[d["kind"]] = summary.get(d["kind"], 0) + 1
    a.close()
    b.close()
    return {
        "sheets_only_in_first": only_a,
        "sheets_only_in_second": only_b,
        "diff_count": len(diffs),
        "diff_summary": summary,
        "diffs": diffs[:sample_limit],
        "truncated": len(diffs) > sample_limit,
        "package_parts_added": sorted(parts_b - parts_a)[:50],
        "package_parts_removed": sorted(parts_a - parts_b)[:50],
        "material_change": bool(diffs or only_a or only_b or (parts_a != parts_b)),
    }


# ==========================================================================
# import manifest and legacy conversion
# ==========================================================================


def build_manifest(paths, include_hashes=True):
    entries = []
    warnings = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files = sorted([x for x in p.rglob("*") if x.is_file()])
        else:
            files = [p]
        for f in files:
            ext = f.suffix.lower()
            entry = {
                "file": f.name,
                "path_display": str(f),
                "extension": ext,
                "size_bytes": f.stat().st_size,
                "sha256": C.sha256_file(f) if include_hashes else None,
            }
            if ext in TABULAR_EXT | OTHER_EXT:
                raw_bytes = f.read_bytes()
                info = inspect_encoding(raw_bytes)
                entry.update({"encoding": info["encoding"], "line_ending": info["line_ending"]})
                if ext in TABULAR_EXT:
                    text, _, _ = decode_bytes(raw_bytes)
                    entry["dialect"] = sniff_dialect(text)
            elif ext in WORKBOOK_EXT:
                try:
                    st = structure(f)
                    entry["sheets"] = [s["name"] for s in st["sheets"]]
                    entry["row_counts"] = {s["name"]: s["max_row"] for s in st["sheets"]}
                    entry["formula_cells"] = st["totals"]["formula_cells"]
                except Exception as exc:
                    warnings.append(f"{f.name}: structure could not be read ({type(exc).__name__}).")
            elif ext in LEGACY_EXT:
                entry["parse_rule"] = "requires conversion before import"
                warnings.append(f"{f.name}: legacy format {ext}; convert before analysis.")
            else:
                warnings.append(f"{f.name}: unrecognised extension {ext!r}; recorded without parsing.")
            entries.append(entry)
    payload = {
        "generated_by": f"{C.TOOL_NAME} {C.VERSION}",
        "file_count": len(entries),
        "total_bytes": sum(e["size_bytes"] for e in entries),
        "entries": entries,
        "warnings": warnings,
    }
    payload["manifest_digest"] = C.hashlib.sha256(
        json.dumps([e["sha256"] for e in entries], sort_keys=True).encode()
    ).hexdigest()
    return payload


def convert_workbook(src, dst):
    """Convert supported legacy formats to .xlsx, or say clearly what is missing."""
    from openpyxl import Workbook, load_workbook

    s, d = Path(src), Path(dst)
    sext = s.suffix.lower()
    if sext in WORKBOOK_EXT:
        wb = load_workbook(filename=str(s))
        wb.save(str(d))
        return {"converted": True, "route": "copy_through_openpyxl", "sheets": wb.sheetnames}
    if sext in TABULAR_EXT:
        table, warnings = read_table(s)
        wb = Workbook()
        ws = wb.active
        ws.title = "data"
        ws.append(table["columns"])
        for row in table["rows"]:
            ws.append(row)
        wb.save(str(d))
        return {"converted": True, "route": "delimited_to_xlsx", "sheets": ["data"],
                "rows": len(table["rows"]), "warnings": warnings}
    if sext == ".ods":
        try:
            import odf  # noqa: F401
        except ImportError:
            raise SkillToolError(
                "MISSING_DEPENDENCY",
                "ODS conversion needs the optional 'odfpy' package, which is not installed in this environment.",
                "Install with: python -m pip install 'odfpy>=1.4' and retry.",
            )
        raise SkillToolError("CONVERSION_UNSUPPORTED",
                             "odfpy is present but the ODS conversion route is not implemented in this build.",
                             "Export to .xlsx from the spreadsheet application instead.")
    if sext == ".xls":
        try:
            import xlrd
        except ImportError:
            raise SkillToolError(
                "MISSING_DEPENDENCY",
                "Legacy .xls conversion needs 'xlrd>=2.0' which is not installed in this environment.",
                "Install with: python -m pip install 'xlrd>=2.0' and retry.",
            )
        book = xlrd.open_workbook(str(s), formatting_info=False)
        wb = Workbook()
        wb.remove(wb.active)
        total = 0
        for sh in book.sheets():
            ws = wb.create_sheet(sh.name[:31])
            for r in range(sh.nrows):
                ws.append([_xls_cell(sh.cell(r, c)) for c in range(sh.ncols)])
            total += sh.nrows
        wb.save(str(d))
        return {"converted": True, "route": "xlrd_to_xlsx", "sheets": book.sheet_names(),
                "rows": total, "note": "Formulas and VBA are not preserved by this route (values only)."}
    if sext == ".xlsb":
        raise SkillToolError(
            "CONVERSION_UNSUPPORTED",
            "Binary .xlsb workbooks cannot be read by the standard-library/openpyxl stack.",
            "Re-save the file as .xlsx in Excel, or install a dedicated xlsb reader before retrying.",
        )
    raise SkillToolError("UNSUPPORTED_FORMAT", f"Cannot convert {sext!r}.", "Supported: .xls, .csv, .tsv, .xlsx")


def _xls_cell(cell):
    import xlrd

    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate.xldate_as_datetime(cell.value, 0).date().isoformat()
        except Exception:
            return cell.value
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    return cell.value


# ==========================================================================
# catalog / request parsing (input handling)
# ==========================================================================


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|br|table|section)>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return text


def normalize_text(text: str) -> str:
    if "<" in text and ">" in text:
        text = strip_html(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    return text


SKILL_RE = re.compile(r"skill\s*name\s*:\s*(.+?)(?=\s*description\s*:|\n|$)", re.I)
DESC_RE = re.compile(r"description\s*:\s*(.+?)(?=\n\s*\d+\.\s*skill\s*name\s*:|\n\s*skill\s*name\s*:|\Z)", re.I | re.S)
CATEGORY_RE = re.compile(r"^\s*(?:-\s*)?(?:\d+[.)]\s*)?(?:\d+[.)]\s*)?([A-Z][^:\n]{2,90}?)\s*$")


def parse_catalog(text: str) -> dict:
    """Extract categories, Skill Tool names and descriptions from Markdown/plain/HTML text."""
    norm = normalize_text(text)
    lines = norm.split("\n")
    categories = []
    skills = []
    current = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        head = re.match(r"^(?:#+\s*)?(?:-+\s*)?(\d{1,2})[.)]\s*(.+?)\s*$", stripped)
        is_category = False
        if head and len(head.group(2)) < 120 and not re.search(r"skill\s*name|description", stripped, re.I):
            nxt = "\n".join(lines[i + 1:i + 4]).lower()
            if "skill name" in nxt or re.search(r"\d+\s+proposed\s+skill", nxt):
                is_category = True
        if is_category:
            current = {"index": int(head.group(1)), "name": re.sub(r"\s+", " ", head.group(2)).strip(" .:\u2013-")}
            categories.append(current)
            continue
        if re.search(r"skill\s*name\s*:", stripped, re.I):
            m = SKILL_RE.search(stripped)
            if not m:
                continue
            name = m.group(1).strip(" .;:")
            rest = stripped[m.end():].strip()
            desc = ""
            dm = re.search(r"description\s*:\s*(.*)$", rest, re.I)
            if dm:
                desc = dm.group(1).strip()
            if not desc:
                tail = norm[m.end():]
                dm2 = DESC_RE.search(tail)
                if dm2:
                    desc = re.sub(r"\s+", " ", dm2.group(1)).strip()
            desc = re.sub(r"\s*\d+\s*proposed skills.*$", "", desc, flags=re.I).strip(" .;")
            skills.append({
                "name": name,
                "description": desc,
                "category": current["name"] if current else None,
                "category_index": current["index"] if current else None,
            })
    # fallback: "- Name: ... Description: ..." style bullets without "Skill Name"
    if not skills:
        for line in lines:
            m = re.match(r"^\s*(?:[-*\u2022]|\d+[.)])\s*([A-Z][\w /&()+.,'-]{2,80}?)\s*[:\u2013-]\s*(.{15,})$", line)
            if m:
                skills.append({"name": m.group(1).strip(), "description": m.group(2).strip(),
                               "category": categories[0]["name"] if categories else None,
                               "category_index": categories[0]["index"] if categories else None})
    cats_named = {s["category"] for s in skills if s["category"]}
    return {
        "category_count": len(categories) or len(cats_named),
        "skill_count": len(skills),
        "categories": categories,
        "skills": skills,
        "uncategorized": [s["name"] for s in skills if not s["category"]],
    }


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s or "capability"
