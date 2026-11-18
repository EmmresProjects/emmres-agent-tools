#!/usr/bin/env python3
"""Export operations for the word-document-agent skill tool.

Native exports implemented here: plain text, Markdown, single-file HTML
(with inline CSS and base64 images), and a structured JSON document
model. PDF/XPS export is implemented as a configurable external-adapter
call that refuses to run unless a converter is configured explicitly.

Loaded by the entry point as ``tools_docx_export``.
"""

from __future__ import annotations

import base64
import html
import importlib.util as _ilu
import json
import os
import re
import shlex
import subprocess
import sys as _sys

from xml.etree import ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))


def _bootstrap(module_name, filename):
    if module_name in _sys.modules:
        return _sys.modules[module_name]
    spec = _ilu.spec_from_file_location(module_name, os.path.join(_HERE, filename))
    module = _ilu.module_from_spec(spec)
    _sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


C = _bootstrap("tools_docx_core", "tools-docx-core.py")
for _name in dir(C):
    if not _name.startswith("_"):
        globals()[_name] = getattr(C, _name)


# --------------------------------------------------------------------------
# document model
# --------------------------------------------------------------------------

def _run_style(run) -> dict:
    rpr = run.find(qn("w:rPr"))
    style = {"bold": False, "italic": False, "underline": False, "strike": False,
             "code": False, "color": None, "size": None}
    if rpr is None:
        return style
    style["bold"] = rpr.find(qn("w:b")) is not None
    style["italic"] = rpr.find(qn("w:i")) is not None
    style["underline"] = rpr.find(qn("w:u")) is not None
    style["strike"] = rpr.find(qn("w:strike")) is not None
    rstyle = rpr.find(qn("w:rStyle"))
    style["code"] = rstyle is not None and attr(rstyle, "w:val") == "VerbatimChar"
    style["color"] = attr(rpr.find(qn("w:color")), "w:val")
    style["size"] = attr(rpr.find(qn("w:sz")), "w:val")
    return style


def _paragraph_model(p, doc, rels) -> dict:
    style = None
    ppr = p.find(qn("w:pPr"))
    level = None
    ordered = False
    if ppr is not None:
        pstyle = ppr.find(qn("w:pStyle"))
        style = attr(pstyle, "w:val") if pstyle is not None else None
        outline = ppr.find(qn("w:outlineLvl"))
        if outline is not None and attr(outline, "w:val") is not None:
            level = int(attr(outline, "w:val")) + 1
        numpr = ppr.find(qn("w:numPr"))
        if numpr is not None:
            ordered = True
        jc = ppr.find(qn("w:jc"))
        align = attr(jc, "w:val") if jc is not None else None
    else:
        align = None
    if style and style.lower().startswith("heading") and level is None:
        match = re.match(r"^heading(\d)$", style.lower())
        if match:
            level = int(match.group(1))
    runs = []
    images = []
    for el in p.iter():
        if el.tag == W_R:
            rpr = el.find(qn("w:rPr"))
            vanish = rpr is not None and rpr.find(qn("w:vanish")) is not None
            text = "".join((t.text or "") for t in el.iter(W_T))
            for drawing in el.iter(W_DRAWING):
                blip = drawing.find(".//" + qn("a:blip"))
                rid = attr(blip, "r:embed") if blip is not None else None
                props = drawing.find(".//" + W_DOCPR)
                images.append({
                    "rel_id": rid,
                    "part": rels.get(rid, {}).get("part") if rid else None,
                    "alt": (attr(props, "descr") or "") if props is not None else "",
                    "width_emu": attr(drawing.find(".//" + qn("wp:extent")), "cx"),
                })
            if text and not vanish:
                run = {"text": text, **_run_style(el)}
                runs.append(run)
    return {"type": "heading" if level else "paragraph", "level": level,
            "style": style, "align": align, "ordered_list_item": ordered,
            "runs": runs, "images": images,
            "text": "".join(r["text"] for r in runs),
            "has_bold": any(r["bold"] for r in runs),
            "has_italic": any(r["italic"] for r in runs),
            "hidden_only": bool(runs) and all(
                (el.find(qn("w:rPr")) is not None and
                 el.find(qn("w:rPr")).find(qn("w:vanish")) is not None)
                for el in p.iter(W_R))}


def document_model(doc) -> dict:
    rels = doc.rels(doc.main_part)
    body = doc.body()
    blocks = []

    def walk(container, depth=0):
        for el in list(container):
            if el.tag == W_P:
                model = _paragraph_model(el, doc, rels)
                if model["text"].strip() or model["images"]:
                    blocks.append(model)
            elif el.tag == qn("w:tbl"):
                rows = []
                for tr in el.findall(qn("w:tr")):
                    row = []
                    for tc in tr.findall(qn("w:tc")):
                        row.append("\n".join(para_text(p) for p in tc.findall(W_P)))
                    rows.append(row)
                blocks.append({"type": "table", "rows": rows,
                               "columns": max((len(r) for r in rows), default=0)})
            elif el.tag == qn("w:sdt"):
                content = el.find(qn("w:sdtContent"))
                pr = el.find(qn("w:sdtPr"))
                alias = attr(pr.find(qn("w:alias")), "w:val") if pr is not None and pr.find(qn("w:alias")) is not None else None
                blocks.append({"type": "control", "alias": alias})
                if content is not None:
                    walk(content, depth + 1)
            elif el.tag == qn("w:sectPr"):
                continue

    walk(body)
    return {"blocks": blocks,
            "metadata": {"properties": {k.lower(): v for k, v in
                                        {kk: vv for kk, vv in
                                         ((x, y) for x, y in _core_items(doc))}.items()}},
            "sections": len(list(doc.document().iter(qn("w:sectPr")))),
            "relationships": {rid: info["target"] for rid, info in rels.items()}}


def _core_items(doc):
    if not doc.has("docProps/core.xml"):
        return []
    root = doc.xml("docProps/core.xml")
    return [(local(el.tag), (el.text or "")) for el in root]


# --------------------------------------------------------------------------
# markdown renderer
# --------------------------------------------------------------------------

def _md_inline(runs) -> str:
    out = []
    for run in runs:
        text = run["text"]
        if not text.strip():
            out.append(text)
            continue
        if run["code"]:
            text = "`%s`" % text
        if run["bold"]:
            text = "**%s**" % text
        if run["italic"]:
            text = "*%s*" % text
        out.append(text)
    return "".join(out)


def to_markdown(doc, model=None) -> str:
    model = model or document_model(doc)
    lines = []
    title = ""
    for key, value in _core_items(doc):
        if key.lower() == "title":
            title = value
    if title:
        lines.append("# %s" % title)
        lines.append("")
    in_list = False
    for block in model["blocks"]:
        if block["type"] == "table":
            rows = block["rows"]
            if not rows:
                continue
            lines.append("| " + " | ".join(c.replace("\n", " ") for c in rows[0]) + " |")
            lines.append("|" + "|".join(" --- " for _ in rows[0]) + "|")
            for row in rows[1:]:
                lines.append("| " + " | ".join(c.replace("\n", " ") for c in row) + " |")
            lines.append("")
            in_list = False
            continue
        if block["type"] == "control":
            lines.append("_[form field: %s]_" % (block["alias"] or "unnamed"))
            lines.append("")
            continue
        if block["type"] == "heading":
            in_list = False
            lines.append("%s %s" % ("#" * min(6, block["level"] or 1),
                                    _md_inline(block["runs"])))
            lines.append("")
            continue
        text = _md_inline(block["runs"])
        if block.get("ordered_list_item"):
            lines.append("- " + text.strip())
            in_list = True
            continue
        if in_list:
            lines.append("")
            in_list = False
        for image in block["images"]:
            lines.append("![%s](%s)" % (image["alt"] or "image",
                                        os.path.basename(image["part"] or "image")))
            lines.append("")
        if text.strip():
            lines.append(text)
            lines.append("")
    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------------------------------
# plain text renderer
# --------------------------------------------------------------------------

def to_text(doc, model=None, width: int = 80) -> str:
    model = model or document_model(doc)
    lines = []
    for block in model["blocks"]:
        if block["type"] == "table":
            for row in block["rows"]:
                lines.append(" | ".join(c.replace("\n", " ") for c in row))
            lines.append("")
            continue
        if block["type"] == "control":
            lines.append("[form field: %s]" % (block["alias"] or "unnamed"))
            continue
        if block["type"] == "heading":
            lines.append(block["text"].strip())
            lines.append("=" * min(width, max(3, len(block["text"].strip()))))
            continue
        for image in block["images"]:
            lines.append("[image: %s%s]" % (os.path.basename(image["part"] or "image"),
                                            " - " + image["alt"] if image["alt"] else ""))
        if block["text"].strip():
            lines.append(block["text"])
            lines.append("")
    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------------------------------
# html renderer
# --------------------------------------------------------------------------

HTML_CSS = """
:root { color-scheme: light dark; }
body { font-family: Calibri, Carlito, "Segoe UI", Arial, sans-serif; line-height: 1.5;
       max-width: 52rem; margin: 2.5rem auto; padding: 0 1.25rem; color: #1b1b1b; background: #fff; }
h1, h2, h3, h4, h5, h6 { line-height: 1.25; margin: 1.6em 0 .5em; }
h1 { font-size: 2rem; } h2 { font-size: 1.6rem; } h3 { font-size: 1.3rem; }
table { border-collapse: collapse; width: 100%; margin: 1.2em 0; }
th, td { border: 1px solid #c8c8c8; padding: .45rem .6rem; text-align: left; vertical-align: top; }
th { background: #f2f2f2; }
figure { margin: 1.4em 0; }
img { max-width: 100%; height: auto; }
figcaption { font-size: .85rem; color: #555; }
code { font-family: Consolas, "Courier New", monospace; background: #f4f4f4; padding: .1em .3em;
       border-radius: .2em; }
.docmeta { border-top: 1px solid #ddd; margin-top: 3rem; padding-top: .8rem; font-size: .8rem; color: #666; }
"""


def _html_inline(runs) -> str:
    out = []
    for run in runs:
        text = html.escape(run["text"])
        if run["code"]:
            text = "<code>%s</code>" % text
        if run["strike"]:
            text = "<s>%s</s>" % text
        if run["underline"]:
            text = "<u>%s</u>" % text
        if run["italic"]:
            text = "<em>%s</em>" % text
        if run["bold"]:
            text = "<strong>%s</strong>" % text
        out.append(text)
    return "".join(out)


def to_html(doc, model=None, embed_images: bool = True) -> str:
    model = model or document_model(doc)
    title = "Document"
    meta_rows = []
    for key, value in _core_items(doc):
        if key.lower() == "title" and value.strip():
            title = value.strip()
        if value.strip():
            meta_rows.append((key, value))
    parts = [
        "<!DOCTYPE html>",
        '<html lang="%s">' % _language(doc),
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>%s</title>" % html.escape(title),
        "<style>%s</style>" % HTML_CSS.strip(),
        "</head>",
        "<body>",
        "<main>",
    ]
    in_list = False
    for block in model["blocks"]:
        if block["type"] == "table":
            if in_list:
                parts.append("</ul>")
                in_list = False
            rows = block["rows"]
            if rows:
                parts.append("<table>")
                parts.append("<thead><tr>" + "".join(
                    "<th scope=\"col\">%s</th>" % html.escape(c) for c in rows[0]) + "</tr></thead>")
                if len(rows) > 1:
                    parts.append("<tbody>")
                    for row in rows[1:]:
                        parts.append("<tr>" + "".join("<td>%s</td>" % html.escape(c)
                                                      for c in row) + "</tr>")
                    parts.append("</tbody>")
                parts.append("</table>")
            continue
        if block["type"] == "control":
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append('<p class="form-field"><em>Form field: %s</em></p>'
                         % html.escape(block["alias"] or "unnamed"))
            continue
        images_html = []
        for image in block["images"]:
            src = ""
            if image["part"] and doc.has(image["part"]):
                if embed_images:
                    data = doc.entries[image["part"]]
                    ext = os.path.splitext(image["part"])[1].lstrip(".").lower()
                    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                            "gif": "image/gif", "bmp": "image/bmp"}.get(ext, "application/octet-stream")
                    src = "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))
                else:
                    src = os.path.basename(image["part"])
            if not src:
                continue
            images_html.append('<figure><img src="%s" alt="%s">%s</figure>' % (
                src, html.escape(image["alt"] or ""),
                "<figcaption>%s</figcaption>" % html.escape(image["alt"])
                if image["alt"] else ""))
        if images_html:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.extend(images_html)
        if block["type"] == "heading":
            if in_list:
                parts.append("</ul>")
                in_list = False
            level = min(6, max(1, block["level"] or 1))
            if level == 1 and title and not block["text"].strip() == title:
                level = 2
            parts.append("<h%d>%s</h%d>" % (level, _html_inline(block["runs"]), level))
            continue
        if not block["text"].strip():
            continue
        if block.get("ordered_list_item"):
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append("<li>%s</li>" % _html_inline(block["runs"]))
            continue
        if in_list:
            parts.append("</ul>")
            in_list = False
        parts.append("<p>%s</p>" % _html_inline(block["runs"]))
    if in_list:
        parts.append("</ul>")
    parts.append("</main>")
    if meta_rows:
        parts.append('<footer class="docmeta">')
        parts.extend("<div>%s: %s</div>" % (html.escape(k), html.escape(v))
                     for k, v in meta_rows)
        parts.append("</footer>")
    parts.extend(["</body>", "</html>"])
    return "\n".join(parts)


def _language(doc) -> str:
    styles = doc.styles(create=False)
    if styles is not None:
        defaults = styles.find(qn("w:docDefaults"))
        if defaults is not None:
            lang = defaults.find(".//" + qn("w:lang"))
            value = attr(lang, "w:val") if lang is not None else None
            if value:
                return value
    return "en"


# --------------------------------------------------------------------------
# pdf adapter
# --------------------------------------------------------------------------

def to_pdf(doc, output_path: str, cfg, params=None) -> dict:
    params = params or {}
    external = cfg.get("external", {})
    converter = params.get("converter") or external.get("pdf_converter") or ""
    allow = str(params.get("allow_subprocess",
                           external.get("allow_subprocess", False))).lower() in ("1", "true", "yes")
    if not converter:
        raise ToolError(
            "EXTERNAL_UNAVAILABLE",
            "PDF export needs an external converter; none is configured",
            {
                "reason": "the tool never bundles or silently installs a PDF engine",
                "enable": [
                    "install LibreOffice (soffice) or another headless converter, or use Word",
                    "set external.pdf_converter in asset-default-config.json, or pass "
                    "--param converter=/path/to/soffice",
                    "set external.allow_subprocess to true (or pass --param allow_subprocess=true)",
                ],
                "native_alternatives": ["export --param format=html", "export --param format=md",
                                        "export --param format=txt"],
                "word_path": "File > Save As > PDF, or File > Export > Create PDF/XPS",
            })
    if not allow:
        raise ToolError(
            "EXTERNAL_UNAVAILABLE",
            "a PDF converter is configured but subprocess execution is disabled",
            {"converter": converter,
             "enable": "set external.allow_subprocess=true (or pass --param allow_subprocess=true) "
                       "after reviewing the binary path"})
    if doc.path == "<memory>":
        raise ToolError("USAGE", "PDF export requires the document to exist on disk; "
                                 "write the .docx first")
    input_bytes = doc.to_bytes()
    work_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(work_dir, exist_ok=True)
    staged = os.path.join(work_dir, ".wda-pdf-source.docx")
    atomic_write_bytes(staged, input_bytes)
    args = []
    for item in external.get("pdf_converter_args", ["{input}", "--headless",
                                                    "--convert-to", "pdf", "--outdir", "{outdir}"]):
        args.append(str(item).replace("{input}", staged).replace("{outdir}", work_dir)
                    .replace("{output}", os.path.abspath(output_path)))
    timeout = int(external.get("timeout_seconds", 180))
    command = [converter] + args
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                   check=False)
    except FileNotFoundError:
        raise ToolError("EXTERNAL_UNAVAILABLE", "PDF converter not found: %s" % converter,
                        {"converter": converter})
    except subprocess.TimeoutExpired:
        raise ToolError("LIMIT", "PDF conversion exceeded the configured timeout",
                        {"timeout_seconds": timeout})
    finally:
        try:
            os.unlink(staged)
        except OSError:
            pass
    produced = None
    candidates = [os.path.abspath(output_path),
                  os.path.join(work_dir, ".wda-pdf-source.pdf"),
                  os.path.join(work_dir, os.path.splitext(os.path.basename(staged))[0] + ".pdf")]
    for candidate in candidates:
        if os.path.isfile(candidate):
            produced = candidate
            break
    if produced is None:
        raise ToolError("EXTERNAL_UNAVAILABLE", "converter produced no PDF",
                        {"returncode": completed.returncode,
                         "stdout": (completed.stdout or "")[-1000:],
                         "stderr": (completed.stderr or "")[-1000:]})
    if produced != os.path.abspath(output_path):
        os.replace(produced, os.path.abspath(output_path))
    with open(output_path, "rb") as fh:
        header = fh.read(5)
    if header != b"%PDF-":
        raise ToolError("INTEGRITY", "converter output is not a PDF file",
                        {"output": output_path})
    return {"operation": "export", "format": "pdf", "written": output_path,
            "bytes": os.path.getsize(output_path), "converter": converter,
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "").strip()[:400]}


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------

def export(doc, params, output_path: str, cfg) -> dict:
    fmt = (params.get("format") or "").lower().lstrip(".") or \
        os.path.splitext(output_path)[1].lstrip(".").lower()
    if not fmt:
        raise ToolError("USAGE", "export needs --param format=txt|md|html|json|pdf "
                                 "or an output extension")
    model = document_model(doc)
    if fmt in ("txt", "text"):
        text = to_text(doc, model)
        atomic_write_text(output_path, text)
        return {"operation": "export", "format": "txt", "written": output_path,
                "bytes": os.path.getsize(output_path), "blocks": len(model["blocks"])}
    if fmt in ("md", "markdown"):
        text = to_markdown(doc, model)
        atomic_write_text(output_path, text)
        return {"operation": "export", "format": "md", "written": output_path,
                "bytes": os.path.getsize(output_path), "blocks": len(model["blocks"])}
    if fmt in ("html", "htm"):
        text = to_html(doc, model,
                       embed_images=str(params.get("embed_images", "true")).lower()
                       in ("1", "true", "yes"))
        atomic_write_text(output_path, text)
        return {"operation": "export", "format": "html", "written": output_path,
                "bytes": os.path.getsize(output_path), "blocks": len(model["blocks"])}
    if fmt == "json":
        atomic_write_text(output_path, json.dumps(model, indent=1, ensure_ascii=False))
        return {"operation": "export", "format": "json", "written": output_path,
                "bytes": os.path.getsize(output_path), "blocks": len(model["blocks"])}
    if fmt == "pdf":
        return to_pdf(doc, output_path, cfg, params)
    raise ToolError("USAGE", "unsupported export format: %s" % fmt,
                    {"supported": ["txt", "md", "html", "json", "pdf"]})
