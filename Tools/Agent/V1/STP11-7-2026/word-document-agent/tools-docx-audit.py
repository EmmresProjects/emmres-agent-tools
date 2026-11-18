#!/usr/bin/env python3
"""Inspection, accessibility, proofing and release-gate logic for the
word-document-agent skill tool.

Everything here is read-only with respect to the document: functions
return structured evidence, findings and verdicts. Mutating operations
live in ``tools-docx-edit.py`` and ``tools-docx-structure.py``.

Loaded by the entry point as ``tools_docx_audit``.
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import os
import re
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

B = _bootstrap("tools_docx_build", "tools-docx-build.py")

DYNAMIC_FIELD_TYPES = {"INCLUDETEXT", "INCLUDE", "DDE", "DDEAUTO", "LINK", "EMBED",
                       "IMPORT", "DATABASE", "RDF"}
AMBIGUOUS_LINK_DEFAULT = ["click here", "here", "read more", "more", "link", "this link",
                          "download"]


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------

def load_policy(cfg) -> dict:
    name = cfg.get("policy_files", {}).get("inspection_policy",
                                           "asset-inspection-policy.json")
    policy = load_asset_json(name, required=False)
    if not policy:
        policy = {"sanitize": {"targets": []}, "a11y": {"rules": []},
                  "proofing": {"checks": []}, "qa_gate": {}, "risk": {}}
    return policy


def load_capability_map(cfg) -> dict:
    name = cfg.get("policy_files", {}).get("capability_map", "asset-capability-map.json")
    data = load_asset_json(name, required=True)
    if "categories" not in data:
        raise ToolError("POLICY", "capability map asset is missing its 'categories' list",
                        {"asset": name})
    return data


# --------------------------------------------------------------------------
# metadata
# --------------------------------------------------------------------------

CORE_FIELDS = ["title", "subject", "creator", "keywords", "description",
               "lastModifiedBy", "revision", "created", "modified", "category",
               "contentStatus", "identifier", "language", "version", "lastPrinted"]


def core_properties(doc) -> dict:
    out = {}
    if not doc.has("docProps/core.xml"):
        return out
    root = doc.xml("docProps/core.xml")
    for el in root:
        value = el.text if el.text is not None else "".join(el.itertext())
        out[local(el.tag)] = (value or "").strip()
    return out


def app_properties(doc) -> dict:
    out = {}
    if not doc.has("docProps/app.xml"):
        return out
    root = doc.xml("docProps/app.xml")
    for el in root:
        out[local(el.tag)] = (el.text or "").strip()
    return out


def custom_properties(doc) -> list[dict]:
    if not doc.has("docProps/custom.xml"):
        return []
    root = doc.xml("docProps/custom.xml")
    props = []
    for prop in root:
        name = prop.get("name") or ""
        value = prop.find(qn("vt:lpwstr"))
        if value is None:
            for child in prop:
                value = child
                break
        props.append({"name": name, "value": (value.text if value is not None else "") or ""})
    return props


def protection_state(doc) -> dict:
    settings = doc.settings(create=False)
    if settings is None:
        return {"protected": False}
    prot = settings.find(qn("w:documentProtection"))
    if prot is None:
        return {"protected": False}
    return {"protected": True, "edit": attr(prot, "w:edit"),
            "enforced": attr(prot, "w:enforcement") in ("1", "true"),
            "crypt_provider": attr(prot, "w:cryptProviderType")}


# --------------------------------------------------------------------------
# content and structure
# --------------------------------------------------------------------------

def heading_outline(doc, max_items: int = 200) -> list[dict]:
    items = []
    for idx, p in enumerate(iter_paragraphs(doc.document(), include_tables=False)):
        ppr = p.find(qn("w:pPr"))
        level = None
        style_id = None
        if ppr is not None:
            style_el = ppr.find(qn("w:pStyle"))
            if style_el is not None:
                style_id = attr(style_el, "w:val")
                if style_id and style_id.lower().startswith("heading"):
                    match = re.match(r"^heading(\d)$", style_id.lower())
                    if match:
                        level = int(match.group(1))
            outline_el = ppr.find(qn("w:outlineLvl"))
            if outline_el is not None and attr(outline_el, "w:val") is not None:
                try:
                    level = int(attr(outline_el, "w:val")) + 1
                except ValueError:
                    pass
        if level:
            items.append({"index": idx, "level": level, "style": style_id,
                          "text": truncate(para_text(p), 160)})
            if len(items) >= max_items:
                break
    return items


def style_inventory(doc) -> dict:
    styles_root = doc.styles(create=False)
    defined = []
    if styles_root is not None:
        for style in styles_root.findall(qn("w:style")):
            name_el = style.find(qn("w:name"))
            defined.append({"id": attr(style, "w:styleId"), "type": attr(style, "w:type"),
                            "name": attr(name_el, "w:val") if name_el is not None else None,
                            "default": attr(style, "w:default") in ("1", "true")})
    used = {}
    for part in doc.iter_parts():
        if not part.endswith(".xml") or part.endswith(".rels"):
            continue
        if not (part == doc.main_part or "header" in part or "footer" in part
                or "footnotes" in part or "endnotes" in part or "comments" in part):
            continue
        try:
            root = doc.xml(part)
        except ToolError:
            continue
        for el in root.iter(qn("w:pStyle")):
            value = attr(el, "w:val")
            if value:
                used[value] = used.get(value, 0) + 1
        for el in root.iter(qn("w:rStyle")):
            value = attr(el, "w:val")
            if value:
                used[value] = used.get(value, 0) + 1
    defined_ids = {s["id"] for s in defined}
    return {
        "defined": len(defined),
        "used": used,
        "unused_defined": sorted(defined_ids - set(used))[:50],
        "used_but_undefined": sorted(set(used) - defined_ids)[:50],
        "headings_defined": [s["id"] for s in defined
                             if (s["id"] or "").lower().startswith("heading")],
        "styles": defined[:200],
    }


def font_inventory(doc) -> dict:
    fonts = {}
    for part in doc.iter_parts():
        if not part.endswith(".xml"):
            continue
        if not (part == doc.main_part or part.startswith("word/header")
                or part.startswith("word/footer") or part == "word/styles.xml"):
            continue
        try:
            root = doc.xml(part)
        except ToolError:
            continue
        for el in root.iter(qn("w:rFonts")):
            for key in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
                value = attr(el, key)
                if value:
                    fonts[value] = fonts.get(value, 0) + 1
    embedded = [p for p in doc.iter_parts() if p.startswith("word/fonts/")]
    return {"used": fonts, "embedded_font_parts": embedded,
            "distinct": len(fonts)}


def section_inventory(doc) -> list[dict]:
    sections = []
    for idx, sect in enumerate(doc.document().iter(qn("w:sectPr"))):
        pg_sz = sect.find(qn("w:pgSz"))
        pg_mar = sect.find(qn("w:pgMar"))
        cols = sect.find(qn("w:cols"))
        headers = [attr(el, "w:type") for el in sect.findall(qn("w:headerReference"))]
        footers = [attr(el, "w:type") for el in sect.findall(qn("w:footerReference"))]
        sections.append({
            "index": idx,
            "page_width_twips": int(pg_sz.get(qn("w:w"))) if pg_sz is not None and pg_sz.get(qn("w:w")) else None,
            "page_height_twips": int(pg_sz.get(qn("w:h"))) if pg_sz is not None and pg_sz.get(qn("w:h")) else None,
            "orientation": (attr(pg_sz, "w:orient") or "portrait") if pg_sz is not None else "portrait",
            "margins_twips": {k: int(pg_mar.get(qn("w:" + k))) for k in
                              ("top", "bottom", "left", "right")}
            if pg_mar is not None and all(pg_mar.get(qn("w:" + k)) for k in
                                          ("top", "bottom", "left", "right")) else None,
            "columns": int(cols.get(qn("w:num")) or 1) if cols is not None else 1,
            "header_types": headers,
            "footer_types": footers,
            "break_type": attr(sect.find(qn("w:type")), "w:val"),
        })
    return sections


def table_inventory(doc) -> list[dict]:
    tables = []
    for idx, tbl in enumerate(doc.document().iter(qn("w:tbl"))):
        rows = tbl.findall(qn("w:tr"))
        counts = [len(r.findall(qn("w:tc"))) for r in rows]
        header_tr = rows[0].find(qn("w:trPr")) if rows else None
        header_cells = rows[0].findall(qn("w:tc")) if rows else []
        tables.append({
            "index": idx,
            "rows": len(rows),
            "columns": max(counts) if counts else 0,
            "irregular_columns": len(set(counts)) > 1,
            "repeat_header": bool(header_tr is not None and
                                  header_tr.find(qn("w:tblHeader")) is not None),
            "empty_header_cells": sum(1 for tc in header_cells if not cell_text(tc).strip()),
            "nested": bool(list(tbl.iter(qn("w:tbl")))[1:]) if hasattr(tbl, "iter") else False,
            "style": attr(tbl.find(qn("w:tblPr")).find(qn("w:tblStyle")), "w:val")
            if tbl.find(qn("w:tblPr")) is not None
            and tbl.find(qn("w:tblPr")).find(qn("w:tblStyle")) is not None else None,
        })
    return tables


def link_inventory(doc) -> list[dict]:
    links = []
    rels = doc.rels(doc.main_part)
    for part in [doc.main_part] + [p for p in doc.iter_parts()
                                   if re.match(r"^word/(header|footer)\d*\.xml$", p)]:
        try:
            root = doc.xml(part)
        except ToolError:
            continue
        for link in root.iter(qn("w:hyperlink")):
            rid = attr(link, "r:id")
            anchor = attr(link, "w:anchor")
            text = "".join((t.text or "") for t in link.iter(qn("w:t")))
            target = rels.get(rid, {}).get("target") if rid else None
            mode = rels.get(rid, {}).get("mode") if rid else "Internal"
            links.append({"part": part, "text": text, "target": target or ("#" + anchor if anchor else None),
                          "kind": "anchor" if anchor else ("external" if mode == "External" else "internal"),
                          "rid": rid})
    return links


def bookmark_inventory(doc) -> dict:
    names = {}
    dupes = []
    for part in doc.iter_parts():
        if not part.endswith(".xml"):
            continue
        try:
            root = doc.xml(part)
        except ToolError:
            continue
        for el in root.iter(qn("w:bookmarkStart")):
            name = attr(el, "w:name")
            if not name:
                continue
            if name in names:
                dupes.append(name)
            names[name] = names.get(name, 0) + 1
    hidden = [n for n in names if n.startswith("_")]
    return {"count": len(names), "names": sorted(names)[:200], "duplicates": dupes,
            "internal_only_names": hidden,
            "user_names": sorted(n for n in names if not n.startswith("_"))[:200]}


def image_inventory(doc) -> list[dict]:
    images = []
    for part in doc.iter_parts():
        if not part.endswith(".xml"):
            continue
        try:
            root = doc.xml(part)
        except ToolError:
            continue
        for drawing in root.iter(qn("w:drawing")):
            props = list(drawing.iter(W_DOCPR))
            alt = attr(props[0], "descr") if props else None
            title = attr(props[0], "title") if props else None
            images.append({"part": part, "alt_text": alt or "",
                           "title": title or "", "has_alt": bool((alt or "").strip())})
        for pict in root.iter(qn("w:pict")):
            rendered = ET.tostring(pict, encoding="unicode")
            if "WaterMark" in rendered or "textpath" in rendered:
                continue  # decorative text watermark, not a content image
            images.append({"part": part, "alt_text": "", "title": "", "has_alt": False,
                           "kind": "vml-pict"})
    return images


def revision_inventory(doc) -> dict:
    counts = {"insertions": 0, "deletions": 0, "moves": 0, "property_changes": 0}
    authors = {}
    dates = []
    for part in doc.iter_parts():
        if not part.endswith(".xml"):
            continue
        try:
            root = doc.xml(part)
        except ToolError:
            continue
        for el in root.iter():
            if el.tag == qn("w:ins"):
                counts["insertions"] += 1
            elif el.tag == qn("w:del"):
                counts["deletions"] += 1
            elif el.tag in (qn("w:moveFrom"), qn("w:moveTo")):
                counts["moves"] += 1
            elif el.tag in PROPERTY_CHANGE_TAGS:
                counts["property_changes"] += 1
            else:
                continue
            author = attr(el, "w:author")
            if author:
                authors[author] = authors.get(author, 0) + 1
            date = attr(el, "w:date")
            if date:
                dates.append(date)
    total = sum(counts.values())
    return {"counts": counts, "total": total, "authors": authors,
            "first_date": min(dates) if dates else None,
            "last_date": max(dates) if dates else None,
            "has_tracked_changes": total > 0}


def comment_inventory(doc) -> dict:
    root = doc.comments()
    if root is None:
        return {"count": 0, "authors": {}, "comments": [], "orphan_refs": 0}
    comments = []
    authors = {}
    for el in root.findall(qn("w:comment")):
        author = attr(el, "w:author") or ""
        authors[author] = authors.get(author, 0) + 1
        text = "".join((t.text or "") for t in el.iter(qn("w:t")))
        comments.append({"id": attr(el, "w:id"), "author": author,
                         "initials": attr(el, "w:initials"),
                         "date": attr(el, "w:date"),
                         "text": truncate(text, 200)})
    ids = {c["id"] for c in comments}
    referenced = set()
    for part in doc.iter_parts():
        if not part.endswith(".xml"):
            continue
        try:
            part_root = doc.xml(part)
        except ToolError:
            continue
        for el in part_root.iter(qn("w:commentReference")):
            referenced.add(attr(el, "w:id"))
    return {"count": len(comments), "authors": authors, "comments": comments,
            "orphan_refs": len(referenced - ids),
            "unreferenced_comments": sorted(ids - referenced)}


def hidden_content(doc) -> dict:
    hidden_runs = []
    white_runs = []
    tiny_runs = []
    hidden_rows = 0
    for part in doc.iter_parts():
        if not part.endswith(".xml"):
            continue
        if not (part == doc.main_part or part.startswith("word/header")
                or part.startswith("word/footer")):
            continue
        try:
            root = doc.xml(part)
        except ToolError:
            continue
        for p_index, p in enumerate(root.iter(W_P)):
            for r in p.iter(W_R):
                rpr = r.find(qn("w:rPr"))
                text = "".join((t.text or "") for t in r.iter(qn("w:t")))
                if rpr is None:
                    continue
                if rpr.find(qn("w:vanish")) is not None and text.strip():
                    hidden_runs.append({"part": part, "paragraph": p_index,
                                        "text": truncate(text, 120)})
                color = attr(rpr.find(qn("w:color")), "w:val")
                if color and text.strip():
                    if color.upper() in ("FFFFFF", "WHITE"):
                        white_runs.append({"part": part, "paragraph": p_index,
                                           "text": truncate(text, 120)})
                size = attr(rpr.find(qn("w:sz")), "w:val")
                if size and text.strip():
                    try:
                        if int(size) < 12:  # 6 pt or smaller
                            tiny_runs.append({"part": part, "paragraph": p_index,
                                              "size_half_points": int(size),
                                              "text": truncate(text, 80)})
                    except ValueError:
                        pass
        for tr in root.iter(qn("w:tr")):
            trpr = tr.find(qn("w:trPr"))
            if trpr is not None and trpr.find(qn("w:hidden")) is not None:
                hidden_rows += 1
    personal = []
    core = core_properties(doc)
    for key in ("creator", "lastModifiedBy"):
        if (core.get(key) or "").strip():
            personal.append({"field": key, "value": core[key]})
    app = app_properties(doc)
    for key in ("Company", "Manager"):
        if (app.get(key) or "").strip():
            personal.append({"field": "app." + key, "value": app[key]})
    return {"hidden_runs": hidden_runs, "white_text_runs": white_runs,
            "tiny_text_runs": tiny_runs, "hidden_rows": hidden_rows,
            "hidden_text_count": len(hidden_runs),
            "personal_metadata": personal,
            "invisible_content_total": len(hidden_runs) + len(white_runs) + len(tiny_runs) + hidden_rows}


def package_risks(doc) -> dict:
    macros = [p for p in doc.iter_parts() if p.lower().endswith("vbaproject.bin")]
    embeddings = [p for p in doc.iter_parts() if p.startswith("word/embeddings/")]
    custom_xml = [p for p in doc.iter_parts() if p.startswith("customXml/")]
    active_x = [p for p in doc.iter_parts() if p.startswith("word/activeX/")]
    external = []
    for part in doc.iter_parts():
        if not part.endswith(".rels"):
            continue
        owner = part.replace("_rels/", "").rsplit(".rels", 1)[0]
        try:
            root = doc.xml(part)
        except ToolError:
            continue
        for rel in root.findall(qn("pr:Relationship")):
            if (attr(rel, "TargetMode") or "") == "External":
                external.append({"owner": owner, "type": attr(rel, "Type"),
                                 "target": attr(rel, "Target")})
    fields = [f for f in all_fields(doc)
              if field_type(f["code"]) in DYNAMIC_FIELD_TYPES]
    settings = doc.settings(create=False)
    protected_view = False
    if settings is not None:
        protected_view = settings.find(qn("w:forceUpgrade")) is not None or \
            settings.find(qn("w:removePersonalInformation")) is not None
    return {
        "macros": macros,
        "macro_enabled_format": doc.format() in ("docm", "dotm"),
        "embedded_objects": embeddings,
        "custom_xml_parts": custom_xml,
        "activex_parts": active_x,
        "external_relationships": external,
        "dynamic_fields": [{"type": field_type(f["code"]), "code": truncate(f["code"], 120)}
                           for f in fields],
        "strict_protected_view_flags": protected_view,
    }


def all_fields(doc) -> list[dict]:
    out = []
    for item in field_codes_from_paragraphs(doc.document()):
        out.append({"code": item["code"], "type": field_type(item["code"]),
                    "locked": item["locked"], "dirty": item["dirty"],
                    "simple": item["simple"],
                    "switches": field_switches(item["code"]),
                    "arguments": field_arguments(item["code"])})
    return out


def field_inventory(doc) -> dict:
    fields = all_fields(doc)
    by_type = {}
    for f in fields:
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1
    stale_types = {"TOC", "PAGEREF", "REF", "SEQ", "NUMPAGES", "STYLEREF", "SECTIONPAGES",
                   "FIGUREREF", "TABLEREF"}
    return {"total": len(fields), "by_type": by_type,
            "locked": sum(1 for f in fields if f["locked"]),
            "stale_candidates": sorted({f["type"] for f in fields
                                        if f["type"] in stale_types}),
            "dynamic": [f for f in fields if f["type"] in DYNAMIC_FIELD_TYPES],
            "fields": fields[:300]}


def notes_inventory(doc) -> dict:
    out = {}
    for kind, part in (("footnotes", "word/footnotes.xml"), ("endnotes", "word/endnotes.xml")):
        if not doc.has(part):
            out[kind] = {"count": 0}
            continue
        root = doc.xml(part)
        notes = [el for el in root if local(el.tag) == kind[:-1]
                 and attr(el, "w:type") in (None, "normal")]
        out[kind] = {"count": len(notes),
                     "texts": [truncate("".join((t.text or "") for t in n.iter(qn("w:t"))), 120)
                               for n in notes[:100]]}
    return out


def content_control_inventory(doc) -> list[dict]:
    controls = []
    for sdt in doc.document().iter(qn("w:sdt")):
        pr = sdt.find(qn("w:sdtPr"))
        alias = attr(pr.find(qn("w:alias")), "w:val") if pr is not None and pr.find(qn("w:alias")) is not None else None
        tag = attr(pr.find(qn("w:tag")), "w:val") if pr is not None and pr.find(qn("w:tag")) is not None else None
        kinds = [local(child.tag) for child in (pr if pr is not None else [])]
        controls.append({"alias": alias, "tag": tag,
                         "kind": next((k for k in kinds if k not in
                                       ("alias", "tag", "id", "showingPlcHdr")), "text")})
    return controls


def word_count(doc) -> dict:
    text = doc.text(include_tables=True, limit_chars=doc.cfg["limits"]["max_scan_text_chars"])
    words = re.findall(r"[^\s]+", text)
    chars = len(text)
    paragraphs = len(text.split("\n"))
    return {"words": len(words), "characters": chars, "characters_no_spaces":
            len(re.sub(r"\s", "", text)), "paragraphs": paragraphs,
            "pages_estimate": max(1, round(len(words) / 500 + 0.49))}


# --------------------------------------------------------------------------
# accessibility
# --------------------------------------------------------------------------

def a11y_audit(doc, policy=None) -> dict:
    policy = policy or {"a11y": {}}
    rules = {r["id"]: r for r in policy.get("a11y", {}).get("rules", [])}
    ambiguous = policy.get("a11y", {}).get("ambiguous_link_text", AMBIGUOUS_LINK_DEFAULT)
    min_half = policy.get("a11y", {}).get("min_body_half_points", 16)
    findings = []

    def add(rule_id, message, evidence=None, severity=None):
        rule = rules.get(rule_id, {})
        findings.append({
            "rule": rule_id,
            "severity": severity or rule.get("severity", "warning"),
            "message": message,
            "why": rule.get("why"),
            "evidence": evidence or {},
        })

    props = core_properties(doc)
    if not (props.get("title") or "").strip():
        add("doc-title-missing", "document Title property is empty")
    lang_default = doc.styles(create=False)
    has_lang = False
    if lang_default is not None:
        defaults = lang_default.find(qn("w:docDefaults"))
        if defaults is not None:
            has_lang = defaults.find(".//" + qn("w:lang")) is not None
    if not has_lang:
        add("doc-lang-missing", "no default proofing language is set")

    outline = heading_outline(doc)
    previous = 0
    for item in outline:
        if previous and item["level"] > previous + 1:
            add("heading-skip", "heading level jumps from %d to %d" % (previous, item["level"]),
                {"text": item["text"], "index": item["index"], "level": item["level"]})
        if not item["text"].strip():
            add("empty-heading", "heading has no text",
                {"index": item["index"], "level": item["level"]})
        previous = item["level"]
    if not outline:
        add("outline-empty", "no headings found; navigation pane and TOC will be empty")
    words = word_count(doc)["words"]
    if words > 800 and len(outline) < 3:
        add("headings-missing", "long document with fewer than three headings",
            {"words": words, "headings": len(outline)})

    for image in image_inventory(doc):
        if not image["has_alt"]:
            add("image-no-alt", "image without alternative text", {"part": image["part"]})
    for link in link_inventory(doc):
        text = (link["text"] or "").strip().lower()
        if text in [a.lower() for a in ambiguous] or len(text) <= 2:
            add("ambiguous-link-text", "link text is not descriptive: %r" % link["text"],
                {"text": link["text"], "target": link["target"]})
    for table in table_inventory(doc):
        if table["rows"] > 1 and not table["repeat_header"]:
            add("table-no-header-row", "table does not repeat its header row",
                {"table_index": table["index"], "rows": table["rows"]})
        if table["empty_header_cells"]:
            add("table-empty-header", "header row has empty cells",
                {"table_index": table["index"], "empty_cells": table["empty_header_cells"]})
        if table["irregular_columns"]:
            add("table-irregular-columns", "table rows have different numbers of cells",
                {"table_index": table["index"]})
    hidden = hidden_content(doc)
    if hidden["hidden_text_count"]:
        add("hidden-content", "hidden text present", {"count": hidden["hidden_text_count"],
                                                     "samples": hidden["hidden_runs"][:5]})
    tiny = [r for r in hidden["tiny_text_runs"] if r["size_half_points"] < min_half]
    if tiny:
        add("font-below-minimum", "%d text runs are smaller than %g pt"
            % (len(tiny), min_half / 2.0), {"count": len(tiny), "samples": tiny[:5]})
    for sdt in doc.document().iter(qn("w:sdt")):
        pr = sdt.find(qn("w:sdtPr"))
        alias = attr(pr.find(qn("w:alias")), "w:val") if pr is not None and pr.find(qn("w:alias")) is not None else None
        tag = attr(pr.find(qn("w:tag")), "w:val") if pr is not None and pr.find(qn("w:tag")) is not None else None
        if not alias or not tag:
            add("untagged-form-field", "content control without title/tag",
                {"alias": alias, "tag": tag})
    if any(r.get("severity") == "error" for r in findings):
        pass
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    score = max(0, 100 - counts["error"] * 15 - counts["warning"] * 4 - counts["info"] * 1)
    return {"findings": findings, "counts": counts, "score": min(100, score),
            "outline_entries": len(outline), "words": words}


# --------------------------------------------------------------------------
# proofing
# --------------------------------------------------------------------------

def proofing(doc, policy=None) -> dict:
    policy = policy or {"proofing": {}}
    checks = [c for c in policy.get("proofing", {}).get("checks", []) if c.get("enabled", True)]
    ignore = [t.lower() for t in policy.get("proofing", {}).get("ignore_terms", [])]
    limit = int(policy.get("proofing", {}).get("max_findings", 500))
    max_chars = doc.cfg["limits"]["max_scan_text_chars"]
    findings = []
    scanned = 0
    empty_run = 0
    for idx, p in enumerate(iter_paragraphs(doc.document(), include_tables=True)):
        C.checkpoint("proofing")
        text = para_text(p)
        scanned += len(text)
        if scanned > max_chars:
            break
        if not text.strip():
            empty_run += 1
        else:
            empty_run = 0
        for check in checks:
            if check["id"] == "empty-paragraph-run":
                continue
            for pattern in check.get("patterns", []):
                try:
                    for match in re.finditer(pattern, text, flags=re.MULTILINE):
                        snippet = text[max(0, match.start() - 30):match.end() + 30]
                        if any(term in snippet.lower() for term in ignore):
                            continue
                        findings.append({
                            "check": check["id"], "severity": check.get("severity", "info"),
                            "paragraph": idx, "match": match.group(0),
                            "context": truncate(snippet, 140),
                            "why": check.get("why"),
                        })
                        if len(findings) >= limit:
                            break
                except re.error as exc:
                    raise ToolError("POLICY", "invalid proofing pattern %r: %s"
                                    % (pattern, exc))
        if len(findings) >= limit:
            break
    min_run = next((c.get("min_run", 3) for c in checks if c["id"] == "empty-paragraph-run"), 3)
    if empty_run >= min_run:
        findings.append({"check": "empty-paragraph-run", "severity": "info",
                         "paragraph": None, "match": "%d empty paragraphs" % empty_run,
                         "context": "run of empty paragraphs at end of scan",
                         "why": "layout made with Enter instead of paragraph spacing"})
    counts = {}
    for finding in findings:
        counts[finding["check"]] = counts.get(finding["check"], 0) + 1
    return {"findings": findings, "counts": counts, "total": len(findings),
            "truncated": len(findings) >= limit, "chars_scanned": scanned}


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------

def inspect(doc, params=None, cfg=None, policy=None) -> dict:
    params = params or {}
    cfg = cfg or doc.cfg
    policy = policy or load_policy(cfg)
    deep = str(params.get("deep", "false")).lower() in ("1", "true", "yes")
    profile = {
        "file": {
            "name": os.path.basename(doc.path) if doc.path != "<memory>" else None,
            "format": doc.format(),
            "bytes": os.path.getsize(doc.path) if os.path.exists(str(doc.path)) else None,
            "parts": len(doc.iter_parts()),
            "sha256": doc.hash() if os.path.exists(str(doc.path)) else None,
            "main_part": doc.main_part,
        },
        "properties": {
            "core": core_properties(doc),
            "app": app_properties(doc),
            "custom": custom_properties(doc),
            "protection": protection_state(doc),
        },
        "content": {
            "counts": word_count(doc),
            "outline": heading_outline(doc),
            "sections": section_inventory(doc),
            "tables": table_inventory(doc),
            "images": image_inventory(doc),
            "fields": field_inventory(doc),
            "notes": notes_inventory(doc),
            "styles": style_inventory(doc),
            "fonts": font_inventory(doc),
            "bookmarks": bookmark_inventory(doc),
            "links": link_inventory(doc),
            "controls": content_control_inventory(doc),
        },
        "review": {
            "revisions": revision_inventory(doc),
            "comments": comment_inventory(doc),
        },
        "hidden": hidden_content(doc),
        "risk": package_risks(doc),
    }
    profile["accessibility"] = a11y_audit(doc, policy)
    profile["proofing"] = proofing(doc, policy)
    if not deep:
        profile["content"]["styles"].pop("styles", None)
        profile["content"]["fields"].pop("fields", None)
        profile["review"]["comments"].pop("comments", None)
        profile["content"]["bookmarks"].pop("names", None)
    return profile


def inspect_summary(profile: dict) -> str:
    content = profile["content"]
    review = profile["review"]
    hidden = profile["hidden"]
    lines = [
        "format=%s  parts=%s  words=%s" % (profile["file"]["format"],
                                           profile["file"]["parts"],
                                           content["counts"]["words"]),
        "headings=%d  tables=%d  images=%d  fields=%d  sections=%d" % (
            len(content["outline"]), len(content["tables"]), len(content["images"]),
            content["fields"]["total"], len(content["sections"])),
        "tracked_changes=%d  comments=%d  hidden_runs=%d  personal_fields=%d" % (
            review["revisions"]["total"], review["comments"]["count"],
            hidden["hidden_text_count"], len(hidden["personal_metadata"])),
        "a11y: errors=%d warnings=%d score=%d   proofing: %d finding(s)" % (
            profile["accessibility"]["counts"].get("error", 0),
            profile["accessibility"]["counts"].get("warning", 0),
            profile["accessibility"]["score"], profile["proofing"]["total"]),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# release gate
# --------------------------------------------------------------------------

def qa_gate(doc, params=None, cfg=None, policy=None) -> dict:
    params = params or {}
    cfg = cfg or doc.cfg
    policy = policy or load_policy(cfg)
    gate_cfg = policy.get("qa_gate", {})
    report_only = str(params.get("report_only", "false")).lower() in ("1", "true", "yes")
    checks = []

    def check(cid, passed, severity, detail):
        checks.append({"id": cid, "passed": bool(passed), "severity": severity,
                       "detail": detail})

    integrity_issues = []
    for rid, info in doc.rels(doc.main_part).items():
        if info["mode"] == "Internal" and not doc.has(info["part"]):
            integrity_issues.append("relationship %s -> missing part %s" % (rid, info["part"]))
    check("integrity", not integrity_issues, "error",
          {"dangling_relationships": integrity_issues})

    structure_problems = []
    outline = heading_outline(doc)
    if not outline and word_count(doc)["words"] > 300:
        structure_problems.append("no headings in a document longer than 300 words")
    for table in table_inventory(doc):
        if table["irregular_columns"]:
            structure_problems.append("table %d has irregular rows" % table["index"])
    check("structure", not structure_problems, "warning", {"problems": structure_problems})

    a11y = a11y_audit(doc, policy)
    a11y_errors = [f for f in a11y["findings"] if f["severity"] == "error"]
    if gate_cfg.get("require_alt_text", True):
        alt_missing = [f for f in a11y["findings"] if f["rule"] == "image-no-alt"]
    else:
        alt_missing = []
    check("accessibility", not a11y_errors and not alt_missing, "error",
          {"errors": len(a11y_errors), "score": a11y["score"],
           "top_rules": sorted({f["rule"] for f in a11y["findings"]})[:10]})

    proof = proofing(doc, policy)
    proof_errors = [f for f in proof["findings"] if f["severity"] == "error"]
    placeholders = [f for f in proof["findings"] if f["check"] == "placeholder-leftover"]
    proof_failed = bool(proof_errors)
    check("proofing", not proof_failed, "error",
          {"errors": len(proof_errors), "placeholders": len(placeholders),
           "findings": proof["total"]})

    revisions = revision_inventory(doc)
    if gate_cfg.get("require_no_tracked_changes", True):
        check("tracked-changes", revisions["total"] == 0, "error",
              {"total": revisions["total"], "authors": revisions["authors"]})
    comments = comment_inventory(doc)
    if gate_cfg.get("require_no_comments", False):
        check("comments", comments["count"] == 0, "warning",
              {"count": comments["count"], "authors": comments["authors"]})

    failed = [c for c in checks if not c["passed"]]
    fail_severities = set(gate_cfg.get("fail_on_severity", ["error"]))
    blocking = [c for c in failed if c["severity"] in fail_severities]
    warning_limit = int(gate_cfg.get("max_warnings", 100))
    warnings = [f for f in a11y["findings"] if f["severity"] == "warning"] + \
               [f for f in proof["findings"] if f["severity"] == "warning"]
    too_many_warnings = len(warnings) > warning_limit
    verdict = "pass"
    if blocking or too_many_warnings:
        verdict = "fail"
    elif failed or warnings:
        verdict = "pass-with-warnings"
    if report_only:
        verdict = "report-only:" + verdict
    return {
        "verdict": verdict,
        "blocking_checks": [c["id"] for c in blocking],
        "checks": checks,
        "warnings": len(warnings),
        "warning_limit": warning_limit,
        "a11y_score": a11y["score"],
        "proofing_findings": proof["total"],
        "revisions": revisions["total"],
        "report_only": report_only,
        "next_actions": _next_actions(checks, a11y, proof),
    }


def _next_actions(checks, a11y, proof) -> list[str]:
    actions = []
    by_id = {c["id"]: c for c in checks}
    if not by_id.get("tracked-changes", {"passed": True})["passed"]:
        actions.append("revisions --param mode=accept-all --approve (or reject-all)")
    if not by_id.get("accessibility", {"passed": True})["passed"]:
        rules = sorted({f["rule"] for f in a11y["findings"] if f["severity"] == "error"})
        if "image-no-alt" in rules:
            actions.append("add alternative text when the image is inserted "
                           "(image --param alt=...)")
        if "doc-title-missing" in rules:
            actions.append("metadata --param set_title=\"...\"")
        if "doc-lang-missing" in rules:
            actions.append("format --param scope=character --param language=en-US")
        if "heading-skip" in rules:
            actions.append("styles --param mode=apply --param style=Heading2 "
                           "(fix outline levels)")
    if proof["findings"]:
        actions.append("proof --param fail_on=error to inspect placeholders and typos")
    if not by_id.get("structure", {"passed": True})["passed"]:
        actions.append("inspect to review table structure, then table --param mode=structure")
    return actions


# --------------------------------------------------------------------------
# evaluation / capability scoring
# --------------------------------------------------------------------------

EVIDENCE_PROBES = {
    2: lambda p: len(p["hidden"]["personal_metadata"]) > 0,
    5: lambda p: p["content"]["styles"]["defined"] > 10,
    7: lambda p: p["content"]["fonts"]["distinct"] > 0,
    9: lambda p: len(p["content"]["outline"]) > 0,
    11: lambda p: len(p["content"]["styles"]["used"]) > 0,
    15: lambda p: len(p["content"]["sections"]) > 1,
    16: lambda p: p["content"]["sections"][0]["margins_twips"] is not None if p["content"]["sections"] else False,
    17: lambda p: any(s["columns"] > 1 for s in p["content"]["sections"]),
    18: lambda p: any(s["header_types"] or s["footer_types"] for s in p["content"]["sections"]),
    19: lambda p: len(p["content"]["tables"]) > 0,
    20: lambda p: any(t["repeat_header"] for t in p["content"]["tables"]),
    22: lambda p: len(p["content"]["images"]) > 0,
    31: lambda p: p["content"]["fields"]["total"] > 0,
    32: lambda p: "TOC" in p["content"]["fields"]["by_type"],
    33: lambda p: p["content"]["notes"]["footnotes"]["count"] > 0
    or p["content"]["notes"]["endnotes"]["count"] > 0,
    37: lambda p: p["review"]["comments"]["count"] > 0,
    38: lambda p: p["review"]["revisions"]["total"] > 0,
    39: lambda p: p["accessibility"]["counts"].get("error", 0) == 0,
    41: lambda p: len(p["content"]["controls"]) > 0,
    45: lambda p: p["file"]["format"] in ("docx", "docm"),
    51: lambda p: True,
}


def score_document(doc, cfg, map_data) -> dict:
    profile = inspect(doc, {"deep": True}, cfg)
    categories = []
    for category in map_data["categories"]:
        probe = EVIDENCE_PROBES.get(category["id"])
        evidence = None
        if probe is not None:
            try:
                evidence = bool(probe(profile))
            except Exception:  # pragma: no cover - defensive
                evidence = None
        ops = sorted({s["op"] for s in category["skills"]})
        categories.append({
            "id": category["id"],
            "name": category["name"],
            "purpose": category["purpose"],
            "skills": len(category["skills"]),
            "operations": ops,
            "native_operations": sorted({s["op"] for s in category["skills"]
                                         if s["engine"] == "native"}),
            "advisory_skills": sum(1 for s in category["skills"] if s["engine"] == "advisory"),
            "document_evidence": evidence,
        })
    native_ops = sorted({s["op"] for c in map_data["categories"] for s in c["skills"]
                         if s["engine"] == "native"})
    return {
        "categories": len(categories),
        "skills": sum(c["skills"] for c in categories),
        "native_operations": native_ops,
        "advisory_skills": sum(c["advisory_skills"] for c in categories),
        "document": {"format": profile["file"]["format"],
                     "words": profile["content"]["counts"]["words"],
                     "a11y_score": profile["accessibility"]["score"]},
        "detail": categories,
    }
