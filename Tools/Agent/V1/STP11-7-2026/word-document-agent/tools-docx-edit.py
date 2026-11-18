#!/usr/bin/env python3
"""Mutating content operations for the word-document-agent skill tool:
text replacement, metadata, styles, fields, comments, tracked changes,
protection, sanitisation, mail merge, format conversion, backups and the
advisory ``plan`` / ``route`` helpers.

Loaded by the entry point as ``tools_docx_edit``.
"""

from __future__ import annotations

import base64
import csv
import datetime
import hashlib
import importlib.util as _ilu
import io
import json
import os
import re
import shutil
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
A = _bootstrap("tools_docx_audit", "tools-docx-audit.py")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _core_root(doc):
    return B.ensure_core_part(doc)


def _set_core_prop(doc, name: str, value: str):
    root = _core_root(doc)
    tag = None
    for child in root:
        if local(child.tag).lower() == name.lower():
            tag = child
            break
    if tag is None:
        prefixes = {"title": "dc:title", "subject": "dc:subject", "creator": "dc:creator",
                    "keywords": "cp:keywords", "description": "dc:description",
                    "lastModifiedBy": "cp:lastModifiedBy", "revision": "cp:revision",
                    "category": "cp:category", "contentStatus": "cp:contentStatus",
                    "identifier": "dc:identifier", "language": "dc:language",
                    "version": "cp:version", "created": "dcterms:created",
                    "modified": "dcterms:modified"}
        if name.lower() not in {k.lower() for k in prefixes}:
            raise ToolError("USAGE", "unknown core property: %s" % name,
                            {"supported": sorted(prefixes)})
        name = next(k for k in prefixes if k.lower() == name.lower())
        tag = ET.SubElement(root, qn(prefixes[name]))
        if name in ("created", "modified"):
            tag.set(qn("xsi:type"), "dcterms:W3CDTF")
    tag.text = value
    doc.mark_dirty("docProps/core.xml")


def _remove_app_prop(doc, name: str) -> bool:
    if not doc.has("docProps/app.xml"):
        return False
    root = doc.xml("docProps/app.xml")
    for child in list(root):
        if local(child.tag).lower() == name.lower():
            root.remove(child)
            doc.mark_dirty("docProps/app.xml")
            return True
    return False


def _touch_modified(doc):
    _set_core_prop(doc, "modified", _now())
    root = _core_root(doc)
    rev = None
    for child in root:
        if local(child.tag).lower() == "revision":
            rev = child
            break
    try:
        current = int(rev.text or "1") if rev is not None else 1
    except ValueError:
        current = 1
    _set_core_prop(doc, "revision", str(current + 1))


def _drop_relationships_to(doc, part_names: set[str]):
    for rels_part in [p for p in doc.iter_parts() if p.endswith(".rels")]:
        try:
            root = doc.xml(rels_part)
        except ToolError:
            continue
        base = os.path.dirname(rels_part.replace("_rels/", "").rsplit(".rels", 1)[0])
        for rel in list(root.findall(qn("pr:Relationship"))):
            if (attr(rel, "TargetMode") or "Internal") != "Internal":
                continue
            target = os.path.normpath(os.path.join(base, attr(rel, "Target") or "")).replace("\\", "/")
            if target in part_names:
                root.remove(rel)
                doc.mark_dirty(rels_part)


def remove_parts(doc, parts) -> list[str]:
    removed = []
    for part in parts:
        if doc.has(part):
            doc.remove_part(part)
            removed.append(part)
    if removed:
        _drop_relationships_to(doc, set(removed))
    return removed


# --------------------------------------------------------------------------
# text replacement
# --------------------------------------------------------------------------

def _text_nodes(p):
    """(node, start, end) offsets over the visible text of paragraph ``p``."""
    nodes = []
    offset = 0
    for node in iter_text_nodes(p):
        text = node.text or ""
        nodes.append((node, offset, offset + len(text)))
        offset += len(text)
    return nodes


def replace_in_paragraph(p, pattern, replacement, regex=False, case_sensitive=True,
                         whole_word=False, limit=None) -> list[dict]:
    nodes = _text_nodes(p)
    if not nodes:
        return []
    full = "".join(node.text or "" for node, _, _ in nodes)
    flags = 0 if case_sensitive else re.IGNORECASE
    if regex:
        regex_text = pattern
    else:
        regex_text = re.escape(pattern)
        if whole_word:
            regex_text = r"\b" + regex_text + r"\b"
    try:
        compiled = re.compile(regex_text, flags)
    except re.error as exc:
        raise ToolError("USAGE", "invalid regular expression %r: %s" % (pattern, exc))
    matches = list(compiled.finditer(full))
    if limit is not None:
        matches = matches[:limit]
    if not matches:
        return []
    events = []
    for match in reversed(matches):  # mutate from the end so offsets stay valid
        start, end = match.span()
        new_text = replacement
        if callable(replacement):
            new_text = replacement(match)
        elif regex:
            new_text = match.expand(replacement)
        touched = [(node, s, e) for node, s, e in nodes if e > start and s < end]
        if not touched:
            continue
        first_node, first_start, _ = touched[0]
        first_prefix = (first_node.text or "")[: max(0, start - first_start)]
        first_node.text = first_prefix + new_text
        for node, s, e in touched[1:]:
            if e <= end:
                node.text = ""
            else:
                node.text = (node.text or "")[end - s:]
        for node, _, _ in touched:
            if node.text and (node.text != node.text.strip() or "  " in node.text):
                node.set(qn("xml:space"), "preserve")
        events.append({"match": match.group(0), "replacement": new_text})
    return list(reversed(events))


def replace_document(doc, pattern, replacement, params=None) -> dict:
    params = params or {}
    regex = str(params.get("regex", "false")).lower() in ("1", "true", "yes")
    case_sensitive = str(params.get("case_sensitive", "true")).lower() in ("1", "true", "yes")
    whole_word = str(params.get("whole_word", "false")).lower() in ("1", "true", "yes")
    style_filter = params.get("style")
    dry_run = str(params.get("dry_run", "false")).lower() in ("1", "true", "yes")
    include_headers = str(params.get("include_headers", "true")).lower() in ("1", "true", "yes")
    limit = int(doc.cfg["limits"]["max_replacements"])
    if not pattern:
        raise ToolError("USAGE", "replace needs --param find=TEXT")
    if replacement is None:
        replacement = ""
    remaining = limit
    total = 0
    paragraphs_touched = 0
    samples = []
    parts = [doc.main_part]
    if include_headers:
        parts += [p for p in doc.iter_parts()
                  if re.match(r"^word/(header|footer)\d*\.xml$", p)]
    for part in parts:
        root = doc.xml(part)
        for p in root.iter(W_P):
            C.checkpoint("replace")
            if style_filter:
                ppr = p.find(qn("w:pPr"))
                style_el = ppr.find(qn("w:pStyle")) if ppr is not None else None
                if style_el is None or attr(style_el, "w:val") != style_filter:
                    continue
            events = replace_in_paragraph(p, pattern, replacement, regex=regex,
                                          case_sensitive=case_sensitive,
                                          whole_word=whole_word, limit=remaining)
            if events:
                paragraphs_touched += 1
                total += len(events)
                remaining -= len(events)
                if len(samples) < 10:
                    samples.extend(events[:10 - len(samples)])
                if not dry_run:
                    doc.mark_dirty(part)
            if remaining <= 0:
                raise ToolError("LIMIT", "replacement count exceeded limits.max_replacements; "
                                         "narrow the pattern", {"limit": limit})
    if not dry_run and total:
        _touch_modified(doc)
    return {"operation": "replace", "replacements": total,
            "paragraphs_touched": paragraphs_touched, "dry_run": dry_run,
            "pattern": pattern, "replacement": replacement, "samples": samples,
            "limit": limit, "parts_scanned": len(parts)}


# --------------------------------------------------------------------------
# metadata
# --------------------------------------------------------------------------

def metadata(doc, params) -> dict:
    result = {"operation": "metadata"}
    changed = []
    fields = {
        "title": params.get("set_title") or params.get("title"),
        "subject": params.get("set_subject") or params.get("subject"),
        "creator": params.get("set_author") or params.get("author"),
        "keywords": params.get("set_keywords") or params.get("keywords"),
        "description": params.get("set_description") or params.get("description"),
        "category": params.get("set_category") or params.get("category"),
        "contentStatus": params.get("set_status") or params.get("status"),
        "language": params.get("set_language") or params.get("doc_language"),
        "lastModifiedBy": params.get("set_last_modified_by"),
    }
    for name, value in fields.items():
        if value is not None:
            _set_core_prop(doc, name, str(value))
            changed.append(name)
    if params.get("add_custom"):
        spec = params["add_custom"]
        props = custom_property_root(doc)
        if isinstance(spec, str):
            pairs = [p.partition("=") for p in spec.split(";") if p.strip()]
        else:
            pairs = [(str(k), "=", str(v)) for k, v in dict(spec).items()]
        for name, _, value in pairs:
            if not name.strip():
                continue
            existing = [el for el in props if el.get("name") == name.strip()]
            for el in existing:
                props.remove(el)
            pid = max([int(el.get("pid") or 2) for el in props] + [1]) + 1
            prop = ET.SubElement(props, "{%s}property" % CUSTOM_NS)
            prop.set("fmtid", "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}")
            prop.set("pid", str(pid))
            prop.set("name", name.strip())
            vt = ET.SubElement(prop, qn("vt:lpwstr"))
            vt.text = value
            changed.append("custom:" + name.strip())
        doc.mark_dirty("docProps/custom.xml")
    if params.get("remove_custom"):
        props = custom_property_root(doc)
        names = {n.strip() for n in str(params["remove_custom"]).split(";") if n.strip()}
        for el in list(props):
            if el.get("name") in names:
                props.remove(el)
                changed.append("removed-custom:" + el.get("name"))
        doc.mark_dirty("docProps/custom.xml")
    if str(params.get("clear_personal", "false")).lower() in ("1", "true", "yes"):
        author = doc.cfg["defaults"]["sanitized_author"]
        _set_core_prop(doc, "creator", author)
        _set_core_prop(doc, "lastModifiedBy", author)
        _remove_app_prop(doc, "Company")
        _remove_app_prop(doc, "Manager")
        changed.extend(["creator", "lastModifiedBy", "personal-cleared"])
    if changed:
        _touch_modified(doc)
    result["changed"] = changed
    result["properties"] = {"core": A.core_properties(doc), "app": A.app_properties(doc),
                            "custom": A.custom_properties(doc)}
    if not changed:
        result["note"] = ("no changes requested; pass --param set_title=..., set_author=..., "
                          "add_custom=Name=Value or clear_personal=true")
    return result


CUSTOM_PROPS_PART = "docProps/custom.xml"
CT_CUSTOM_PROPS = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
REL_CUSTOM_PROPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"


def custom_property_root(doc):
    if not doc.has(CUSTOM_PROPS_PART):
        root = ET.Element("{%s}Properties" % CUSTOM_NS)
        doc.add_xml(CUSTOM_PROPS_PART, root, CT_CUSTOM_PROPS)
        doc.add_relationship(doc.main_part, REL_CUSTOM_PROPS, "/" + CUSTOM_PROPS_PART)
    return doc.xml(CUSTOM_PROPS_PART)


# --------------------------------------------------------------------------
# styles
# --------------------------------------------------------------------------

def styles(doc, params) -> dict:
    mode = (params.get("mode") or "list").lower()
    inventory = A.style_inventory(doc)
    if mode == "list":
        return {"operation": "styles", "mode": "list", "defined": inventory["defined"],
                "used": inventory["used"], "used_but_undefined": inventory["used_but_undefined"],
                "unused_defined": inventory["unused_defined"],
                "headings_defined": inventory["headings_defined"],
                "styles": inventory["styles"]}
    if mode == "apply":
        style = params.get("style")
        if not style:
            raise ToolError("USAGE", "styles --param mode=apply needs --param style=NAME")
        defined = {s["id"] for s in inventory["styles"]} | \
                  {s["name"] for s in inventory["styles"] if s["name"]}
        if style not in defined:
            raise ToolError("NOT_FOUND", "style not defined in the document: %s" % style,
                            {"hint": "styles --param mode=list to see available styles",
                             "available": sorted(d for d in defined if d)[:40]})
        match = params.get("match")
        index = params.get("paragraph_index")
        applied = 0
        for idx, p in enumerate(iter_paragraphs(doc.document(), include_tables=False)):
            if index is not None and idx != int(index):
                continue
            if match and match.lower() not in para_text(p).lower():
                continue
            ppr = ensure_child(p, W_PPR)
            style_el = ppr.find(qn("w:pStyle"))
            if style_el is None:
                style_el = ET.Element(qn("w:pStyle"))
                ppr.insert(0, style_el)
            style_el.set(qn("w:val"), str(style))
            applied += 1
        doc.mark_dirty(doc.main_part)
        _touch_modified(doc)
        return {"operation": "styles", "mode": "apply", "style": style,
                "paragraphs_styled": applied, "filter": match or "all"}
    if mode == "normalize":
        strip = str(params.get("strip", "false")).lower() in ("1", "true", "yes")
        direct_runs = 0
        direct_paragraphs = 0
        stripped = 0
        samples = []
        for p in iter_paragraphs(doc.document()):
            ppr = p.find(W_PPR)
            direct_para = ppr is not None and (ppr.find(qn("w:spacing")) is not None
                                               or ppr.find(qn("w:ind")) is not None)
            if direct_para:
                direct_paragraphs += 1
            for r in p.iter(W_R):
                rpr = r.find(qn("w:rPr"))
                if rpr is None or len(rpr) == 0:
                    continue
                direct_runs += 1
                if len(samples) < 10:
                    samples.append({"paragraph_style": attr(ppr.find(qn("w:pStyle")), "w:val")
                                    if ppr is not None and ppr.find(qn("w:pStyle")) is not None
                                    else None,
                                    "properties": [local(c.tag) for c in rpr]})
                if strip:
                    removable = [c for c in rpr if local(c.tag) in
                                 ("sz", "szCs", "color", "rFonts", "b", "i")]
                    for child in removable:
                        rpr.remove(child)
                    stripped += 1
        if strip and direct_runs:
            doc.mark_dirty(doc.main_part)
        return {"operation": "styles", "mode": "normalize",
                "runs_with_direct_formatting": direct_runs,
                "paragraphs_with_direct_spacing": direct_paragraphs,
                "stripped_runs": stripped, "samples": samples,
                "note": "report mode only unless --param strip=true"}
    raise ToolError("USAGE", "unknown styles mode: %r" % mode,
                    {"supported": ["list", "apply", "normalize"]})


# --------------------------------------------------------------------------
# fields
# --------------------------------------------------------------------------

def fields(doc, params) -> dict:
    mode = (params.get("mode") or "list").lower()
    if mode == "list":
        return {"operation": "fields", "mode": "list", **A.field_inventory(doc)}
    if mode == "insert":
        code = params.get("code")
        if not code:
            raise ToolError("USAGE", "fields --param mode=insert needs --param code='PAGE'")
        p = B.make_paragraph("")
        B.append_field(p, code, display=params.get("display") or "")
        doc.body().insert(_insert_index(doc.body()), p)
        doc.mark_dirty(doc.main_part)
        _touch_modified(doc)
        return {"operation": "fields", "mode": "insert", "code": code,
                "type": field_type(code)}
    if mode in ("update", "refresh", "mark-dirty"):
        marked = 0
        for part in doc.iter_parts():
            if not part.endswith(".xml"):
                continue
            try:
                root = doc.xml(part)
            except ToolError:
                continue
            part_changed = False
            for el in root.iter(qn("w:fldChar")):
                if attr(el, "w:fldCharType") == "begin":
                    el.set(qn("w:dirty"), "true")
                    marked += 1
                    part_changed = True
            for el in root.iter(W_FLDSIMPLE):
                if attr(el, "w:fldLock") not in ("1", "true"):
                    marked += 1
                    part_changed = True
            if part_changed:
                doc.mark_dirty(part)
        _mark_update_fields(doc)
        return {"operation": "fields", "mode": "update", "fields_marked": marked,
                "note": "Word recomputes dirty fields on open; the tool cannot paginate "
                        "or evaluate fields itself."}
    if mode in ("lock", "unlock"):
        want_lock = mode == "lock"
        changed = 0
        for part in doc.iter_parts():
            if not part.endswith(".xml"):
                continue
            try:
                root = doc.xml(part)
            except ToolError:
                continue
            for el in root.iter(qn("w:fldChar")):
                if attr(el, "w:fldCharType") == "begin":
                    if want_lock:
                        el.set(qn("w:fldLock"), "true")
                    elif el.get(qn("w:fldLock")) is not None:
                        del el.attrib[qn("w:fldLock")]
                    changed += 1
            for el in root.iter(W_FLDSIMPLE):
                if want_lock:
                    el.set(qn("w:fldLock"), "true")
                elif el.get(qn("w:fldLock")) is not None:
                    del el.attrib[qn("w:fldLock")]
                changed += 1
            if changed:
                doc.mark_dirty(part)
        _touch_modified(doc)
        return {"operation": "fields", "mode": mode, "fields_changed": changed}
    raise ToolError("USAGE", "unknown fields mode: %r" % mode,
                    {"supported": ["list", "insert", "update", "lock", "unlock"]})


def _insert_index(body) -> int:
    children = list(body)
    if children and children[-1].tag == W_SECTPR:
        return len(children) - 1
    return len(children)


def _mark_update_fields(doc):
    settings = doc.settings()
    if settings.find(qn("w:updateFields")) is None:
        el = ET.SubElement(settings, qn("w:updateFields"))
        el.set(qn("w:val"), "true")
    doc.mark_dirty("word/settings.xml")


# --------------------------------------------------------------------------
# comments
# --------------------------------------------------------------------------

def comments(doc, params) -> dict:
    mode = (params.get("mode") or "list").lower()
    inventory = A.comment_inventory(doc)
    if mode == "list":
        return {"operation": "comments", "mode": "list", **inventory}
    if mode == "add":
        text = params.get("comment") or params.get("text")
        if not text:
            raise ToolError("USAGE", "comments --param mode=add needs --param comment=TEXT")
        author = params.get("author") or doc.cfg["defaults"]["sanitized_author"]
        anchor_text = params.get("anchor_text")
        target = None
        for p in iter_paragraphs(doc.document(), include_tables=False):
            if anchor_text and anchor_text.lower() in para_text(p).lower():
                target = p
                break
        if target is None:
            paragraphs = list(iter_paragraphs(doc.document(), include_tables=False))
            if not paragraphs:
                raise ToolError("NOT_FOUND", "document has no paragraphs to anchor a comment")
            if anchor_text:
                raise ToolError("NOT_FOUND", "anchor text not found: %s" % anchor_text)
            target = paragraphs[0]
        root = ensure_comments_part(doc)
        ids = [int(attr(el, "w:id")) for el in root.findall(qn("w:comment"))
               if (attr(el, "w:id") or "").isdigit()]
        new_id = str(max(ids) + 1 if ids else 0)
        comment = ET.SubElement(root, qn("w:comment"))
        comment.set(qn("w:id"), new_id)
        comment.set(qn("w:author"), str(author))
        comment.set(qn("w:date"), _now())
        comment.set(qn("w:initials"), "".join(w[0] for w in str(author).split()[:2]) or "A")
        cp = B._sub(comment, "w:p")
        B.add_inline(cp, str(text), doc=doc)
        start = ET.Element(qn("w:commentRangeStart"))
        start.set(qn("w:id"), new_id)
        target.insert(0, start)
        end = ET.Element(qn("w:commentRangeEnd"))
        end.set(qn("w:id"), new_id)
        target.append(end)
        run = ET.SubElement(target, W_R)
        ref = B._sub(run, "w:commentReference")
        ref.set(qn("w:id"), new_id)
        doc.mark_dirty(doc.main_part)
        doc.mark_dirty("word/comments.xml")
        _touch_modified(doc)
        return {"operation": "comments", "mode": "add", "id": new_id, "author": author,
                "text": str(text), "anchor_paragraph_style": para_style(target)}
    if mode in ("remove", "delete", "clear"):
        removed_parts = remove_parts(doc, [p for p in doc.iter_parts()
                                           if p.startswith("word/comments")])
        count = 0
        for part in doc.iter_parts():
            if not part.endswith(".xml"):
                continue
            try:
                root = doc.xml(part)
            except ToolError:
                continue
            changed = False
            for parent in root.iter():
                for child in list(parent):
                    if child.tag in (qn("w:commentRangeStart"), qn("w:commentRangeEnd"),
                                     qn("w:commentReference")):
                        parent.remove(child)
                        changed = True
                        count += 1
            if changed:
                doc.mark_dirty(part)
        _touch_modified(doc)
        return {"operation": "comments", "mode": "remove", "references_removed": count,
                "parts_removed": removed_parts}
    raise ToolError("USAGE", "unknown comments mode: %r" % mode,
                    {"supported": ["list", "add", "remove"]})


def ensure_comments_part(doc):
    part = "word/comments.xml"
    if not doc.has(part):
        root = ET.Element(qn("w:comments"))
        doc.add_xml(part, root, CT_COMMENTS)
        existing = [info for info in doc.rels(doc.main_part).values() if info["type"] == REL_COMMENTS]
        if not existing:
            doc.add_relationship(doc.main_part, REL_COMMENTS, "comments.xml")
        return root
    return doc.xml(part)


# --------------------------------------------------------------------------
# tracked changes
# --------------------------------------------------------------------------

def revisions(doc, params) -> dict:
    mode = (params.get("mode") or "report").lower()
    before = A.revision_inventory(doc)
    if mode in ("report", "list", "inspect"):
        return {"operation": "revisions", "mode": "report", **before}
    if mode not in ("accept-all", "reject-all"):
        raise ToolError("USAGE", "unknown revisions mode: %r" % mode,
                        {"supported": ["report", "accept-all", "reject-all"]})
    accept = mode == "accept-all"
    removed = 0
    unwrapped = 0
    for part in doc.iter_parts():
        if not part.endswith(".xml"):
            continue
        try:
            root = doc.xml(part)
        except ToolError:
            continue
        changed = _resolve_revisions(root, accept)
        removed += changed["removed"]
        unwrapped += changed["unwrapped"]
        if changed["removed"] or changed["unwrapped"]:
            doc.mark_dirty(part)
    _touch_modified(doc)
    after = A.revision_inventory(doc)
    return {"operation": "revisions", "mode": mode, "before": before["counts"],
            "after": after["counts"], "elements_removed": removed,
            "elements_unwrapped": unwrapped}


def _resolve_revisions(root, accept: bool) -> dict:
    removed = 0
    unwrapped = 0
    # property-change records are metadata about previous state: always dropped
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag in PROPERTY_CHANGE_TAGS:
                parent.remove(child)
                removed += 1
    drop_tags = {qn("w:del") if accept else qn("w:ins")}
    if accept:
        drop_tags.add(qn("w:moveFrom"))
    else:
        drop_tags.add(qn("w:moveTo"))
    keep_tags = {qn("w:ins") if accept else qn("w:del")} | \
                ({qn("w:moveTo")} if accept else {qn("w:moveFrom")})
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag in drop_tags:
                if child.tag in (qn("w:del"), qn("w:moveFrom")) and not accept:
                    pass
                parent.remove(child)
                removed += 1
            elif child.tag in keep_tags:
                index = list(parent).index(child)
                parent.remove(child)
                text_converted = 0
                for offset, grandchild in enumerate(list(child)):
                    for node in grandchild.iter():
                        if node.tag == W_DELTEXT:
                            node.tag = qn("w:t")
                            text_converted += 1
                        elif node.tag == qn("w:delInstrText"):
                            node.tag = qn("w:instrText")
                    parent.insert(index + offset, grandchild)
                unwrapped += 1
    # row-level and cell-level change marks
    for container in root.iter(qn("w:trPr")):
        for child in list(container):
            if child.tag in (qn("w:ins"), qn("w:del")):
                keep = (child.tag == qn("w:ins")) == accept
                container.remove(child)
                if keep:
                    continue
                removed += 1
    return {"removed": removed, "unwrapped": unwrapped}


# --------------------------------------------------------------------------
# protection
# --------------------------------------------------------------------------

def _hash_password(password: str, salt: bytes, spin_count: int = 100000) -> str:
    """ECMA-376 password hash (SHA-512, salt + UTF-16LE password, iterated)."""
    digest = hashlib.sha512(salt + password.encode("utf-16-le")).digest()
    for index in range(spin_count):
        digest = hashlib.sha512(digest + index.to_bytes(4, "little")).digest()
    return base64.b64encode(digest).decode("ascii")


EDIT_TYPES = {"readonly": "readOnly", "read-only": "readOnly", "comments": "comments",
              "forms": "forms", "form": "forms", "tracked": "trackedChanges",
              "trackedchanges": "trackedChanges"}


def apply_protection(doc, params) -> dict:
    mode = (params.get("mode") or params.get("edit") or "readonly").lower()
    edit = EDIT_TYPES.get(mode)
    if not edit:
        raise ToolError("USAGE", "unknown protection mode: %r" % mode,
                        {"supported": sorted(set(EDIT_TYPES.values()))})
    settings = doc.settings()
    existing = settings.find(qn("w:documentProtection"))
    if existing is not None:
        settings.remove(existing)
    prot = ET.SubElement(settings, qn("w:documentProtection"))
    prot.set(qn("w:edit"), edit)
    prot.set(qn("w:enforcement"), "1")
    password = params.get("password")
    if password:
        salt = os.urandom(16)
        hash_value = _hash_password(str(password), salt)
        prot.set(qn("w:algorithmName"), "SHA-512")
        prot.set(qn("w:hashValue"), hash_value)
        prot.set(qn("w:salt"), base64.b64encode(salt).decode("ascii"))
        prot.set(qn("w:spinCount"), "100000")
        prot.set(qn("w:cryptProviderType"), "rsaFull")
        prot.set(qn("w:cryptAlgorithmClass"), "hash")
        prot.set(qn("w:cryptAlgorithmType"), "typeAny")
        prot.set(qn("w:cryptAlgorithmSid"), "14")
    doc.mark_dirty("word/settings.xml")
    _touch_modified(doc)
    return {"operation": "protect", "edit": edit, "enforced": True,
            "password_set": bool(password),
            "note": ("enforcement without a password is a convenience control, not security; "
                     "password hashing follows ISO/IEC 29500-1 17.15.1.29 and must be "
                     "verified in Word before distribution") if not password else
                    ("password hash written per ISO/IEC 29500-1 17.15.1.29; it was not "
                     "verifiable in this environment - test in Word before distribution")}


def remove_protection(doc, params=None) -> dict:
    settings = doc.settings(create=False)
    if settings is None:
        return {"operation": "unprotect", "removed": False, "note": "no settings part"}
    prot = settings.find(qn("w:documentProtection"))
    if prot is None:
        return {"operation": "unprotect", "removed": False, "note": "document was not protected"}
    settings.remove(prot)
    doc.mark_dirty("word/settings.xml")
    _touch_modified(doc)
    return {"operation": "unprotect", "removed": True}


def protect(doc, params) -> dict:
    mode = (params.get("mode") or "").lower()
    if mode in ("remove", "off", "unprotect"):
        return remove_protection(doc, params)
    return apply_protection(doc, params)


# --------------------------------------------------------------------------
# sanitize
# --------------------------------------------------------------------------

def sanitize(doc, params, policy=None, apply_changes: bool = True) -> dict:
    policy = policy or A.load_policy(doc.cfg)
    targets = [t for t in policy.get("sanitize", {}).get("targets", []) if t.get("enabled", True)]
    only = params.get("targets")
    wanted = {t.strip() for t in str(only).split(",")} if only else None
    actions = []
    findings = []
    for target in targets:
        C.checkpoint("sanitize")
        if wanted and target["id"] not in wanted:
            continue
        action = target.get("action", "report-only")
        hit = _probe_target(doc, target)
        if not hit["present"]:
            continue
        findings.append({"id": target["id"], "severity": target.get("severity", "info"),
                         "present": True, "detail": hit["detail"], "action": action})
        if not apply_changes or action == "report-only":
            continue
        try:
            done = _apply_target(doc, target)
        except ToolError as exc:
            actions.append({"id": target["id"], "applied": False, "error": exc.message})
            continue
        actions.append({"id": target["id"], "applied": True, "detail": done})
    if apply_changes and actions:
        _touch_modified(doc)
    return {"operation": "sanitize", "applied": apply_changes,
            "findings": findings, "actions": actions,
            "targets_evaluated": len([t for t in targets if not wanted or t["id"] in wanted]),
            "remaining_risk": _remaining_risk(doc) if apply_changes else None}


def _probe_target(doc, target) -> dict:
    action = target.get("action")
    if action in ("set-property", "remove-property"):
        prop = target.get("property")
        if str(prop).lower() in ("creator", "lastmodifiedby", "title", "subject", "keywords",
                                 "identifier"):
            value = {k.lower(): v for k, v in A.core_properties(doc).items()}.get(
                str(prop).lower(), "")
            return {"present": bool(value), "detail": "%s=%r" % (prop, value)}
        value = A.app_properties(doc).get(prop, "")
        return {"present": bool(value), "detail": "%s=%r" % (prop, value)}
    if action in ("remove-part", "remove-part-pattern"):
        if target.get("part"):
            parts = [target["part"]] if doc.has(target["part"]) else []
        else:
            pattern = re.compile(target.get("pattern", "$^"))
            parts = [p for p in doc.iter_parts() if pattern.search(p)]
        return {"present": bool(parts), "detail": parts}
    if action == "remove-comments":
        count = A.comment_inventory(doc)["count"]
        return {"present": count > 0, "detail": {"comments": count}}
    if action in ("accept-revisions",):
        count = A.revision_inventory(doc)["total"]
        return {"present": count > 0, "detail": {"revisions": count}}
    if action == "remove-hidden-text":
        hidden = A.hidden_content(doc)
        return {"present": hidden["hidden_text_count"] > 0,
                "detail": {"hidden_runs": hidden["hidden_text_count"]}}
    if action == "report-only":
        if target["id"] == "white-text":
            white = A.hidden_content(doc)["white_text_runs"]
            return {"present": bool(white), "detail": {"white_text_runs": len(white)}}
        if target["id"] == "macros":
            macros = A.package_risks(doc)["macros"]
            return {"present": bool(macros), "detail": macros}
        if target["id"] == "embedded-objects":
            objects = A.package_risks(doc)["embedded_objects"]
            return {"present": bool(objects), "detail": objects}
        if target["id"] == "external-links":
            external = A.package_risks(doc)["external_relationships"]
            return {"present": bool(external), "detail": external[:20]}
    return {"present": False, "detail": None}


def _apply_target(doc, target) -> dict:
    action = target.get("action")
    if action == "set-property":
        prop = target["property"]
        setting = target.get("replacement_setting")
        value = doc.cfg["defaults"].get(setting, "Author") if setting else ""
        _set_core_prop(doc, prop, value)
        return {"property": prop, "value": value}
    if action == "remove-property":
        prop = target["property"]
        if str(prop).lower() in ("creator", "lastmodifiedby", "title", "subject", "keywords",
                                 "identifier"):
            _set_core_prop(doc, prop, "")
            return {"property": prop, "removed": True}
        return {"property": prop, "removed": _remove_app_prop(doc, prop)}
    if action == "remove-part":
        return {"parts_removed": remove_parts(doc, [target["part"]])}
    if action == "remove-part-pattern":
        pattern = re.compile(target.get("pattern", "$^"))
        parts = [p for p in doc.iter_parts() if pattern.search(p)]
        return {"parts_removed": remove_parts(doc, parts)}
    if action == "remove-comments":
        return comments(doc, {"mode": "remove"})
    if action == "accept-revisions":
        return revisions(doc, {"mode": "accept-all"})
    if action == "remove-hidden-text":
        removed = 0
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
            changed = False
            for parent in root.iter():
                for run in list(parent):
                    if run.tag != W_R:
                        continue
                    rpr = run.find(qn("w:rPr"))
                    if rpr is not None and rpr.find(qn("w:vanish")) is not None:
                        parent.remove(run)
                        removed += 1
                        changed = True
            if changed:
                doc.mark_dirty(part)
        return {"hidden_runs_removed": removed}
    return {"skipped": action}


def _remaining_risk(doc) -> dict:
    risks = A.package_risks(doc)
    hidden = A.hidden_content(doc)
    return {
        "macros": len(risks["macros"]),
        "embedded_objects": len(risks["embedded_objects"]),
        "external_relationships": len(risks["external_relationships"]),
        "white_text_runs": len(hidden["white_text_runs"]),
        "tracked_changes": A.revision_inventory(doc)["total"],
        "comments": A.comment_inventory(doc)["count"],
        "personal_metadata_fields": len([
            item for item in hidden["personal_metadata"]
            if str(item.get("value", "")).strip() !=
            doc.cfg["defaults"].get("sanitized_author", "Author")]),
    }


# --------------------------------------------------------------------------
# merge (mail merge from JSON/CSV record sets)
# --------------------------------------------------------------------------

def merge(doc, params, output_dir: str | None = None) -> dict:
    source = params.get("data_file") or params.get("source_file")
    if not source:
        raise ToolError("USAGE", "merge needs --param data-file=records.json|csv")
    records = load_records(source, doc.cfg)
    pattern = params.get("placeholder") or doc.cfg["defaults"]["placeholder_pattern"]
    limit = int(doc.cfg["limits"]["max_merge_records"])
    if len(records) > limit:
        raise ToolError("LIMIT", "record set exceeds limits.max_merge_records",
                        {"records": len(records), "limit": limit})
    name_field = params.get("name_field") or "id"
    combine = str(params.get("combine", "false")).lower() in ("1", "true", "yes")
    missing = (params.get("missing") or "keep").lower()
    results = []
    rendered_docs = []
    for index, record in enumerate(records, start=1):
        C.checkpoint("merge")
        clone = _clone_document(doc)
        keys = set()
        for key, value in record.items():
            keys.add(str(key))
            token = pattern.replace("key", str(key))
            replace_document(clone, token, "" if value is None else str(value),
                             {"regex": False, "case_sensitive": True})
        leftover = _find_tokens(clone, pattern)
        if leftover and missing == "error":
            raise ToolError("POLICY", "record %d leaves placeholders unfilled: %s"
                            % (index, ", ".join(sorted(leftover)[:10])),
                            {"record": index, "fields": sorted(leftover)[:10]})
        rendered_docs.append(clone)
        results.append({"record": index, "fields": sorted(keys),
                        "unfilled": sorted(leftover)})
    written = []
    if combine:
        target = os.path.join(output_dir or ".", "merged-combined.docx")
        merged = _clone_document(doc)
        body = merged.body()
        for idx, clone in enumerate(rendered_docs):
            for child in list(clone.body()):
                if child.tag == W_SECTPR:
                    continue
                body.insert(_insert_index(body), child)
            if idx < len(rendered_docs) - 1:
                body.insert(_insert_index(body), B.make_page_break_paragraph())
        merged.mark_dirty(merged.main_part)
        merged.save(target)
        written.append(target)
    else:
        for index, clone in enumerate(rendered_docs, start=1):
            record = records[index - 1]
            label = str(record.get(name_field) or index)
            label = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-") or str(index)
            target = os.path.join(output_dir or ".", "merged-%03d-%s.docx" % (index, label))
            clone.save(target)
            written.append(target)
    return {"operation": "merge", "records": len(records), "files_written": written,
            "combined": combine, "name_field": name_field, "pattern": pattern,
            "detail": results[:50], "truncated_detail": len(results) > 50}


def load_records(source, cfg) -> list[dict]:
    path = os.path.abspath(os.path.expanduser(str(source)))
    if not os.path.isfile(path):
        raise ToolError("NOT_FOUND", "record file not found: %s" % source)
    raw = open(path, "r", encoding="utf-8-sig").read()
    try:
        if path.lower().endswith(".json"):
            data = json.loads(raw)
            if isinstance(data, dict):
                data = data.get("records") or data.get("rows") or [data]
            if not isinstance(data, list):
                raise ToolError("USAGE", "JSON record set must be a list of objects")
        else:
            rows = list(csv.DictReader(io.StringIO(raw)))
            data = rows
    except json.JSONDecodeError as exc:
        raise ToolError("USAGE", "record file is not valid JSON: %s" % exc, {"path": path})
    records = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ToolError("USAGE", "record %d is not an object" % (index + 1))
        records.append({str(k): ("" if v is None else v) for k, v in item.items()})
    if not records:
        raise ToolError("USAGE", "record set is empty: %s" % source)
    return records


def _find_tokens(doc, pattern: str) -> set:
    regex = re.escape(pattern).replace(re.escape("key"), "([A-Za-z0-9_.-]+)")
    found = set()
    for p in iter_paragraphs(doc.document()):
        for match in re.finditer(regex, para_text(p)):
            found.add(match.group(1))
    return found


def _clone_document(doc):
    return type(doc).from_entries(dict(doc.entries) | {
        part: serialize_xml(tree) for part, tree in doc._trees.items()
    }, doc.cfg)


# --------------------------------------------------------------------------
# format conversion and save-a-copy
# --------------------------------------------------------------------------

FORMAT_TARGETS = {
    "docx": (CT_MAIN, ".docx"),
    "docm": (CT_MAIN_MACRO, ".docm"),
    "dotx": (CT_TEMPLATE, ".dotx"),
    "dotm": (CT_TEMPLATE_MACRO, ".dotm"),
}


def convert(doc, params) -> dict:
    target = (params.get("format") or params.get("to") or "").lower().lstrip(".")
    if target not in FORMAT_TARGETS:
        raise ToolError("USAGE", "convert needs --param format=docx|docm|dotx|dotm",
                        {"supported": sorted(FORMAT_TARGETS)})
    content_type, extension = FORMAT_TARGETS[target]
    before = doc.format()
    doc.ensure_override(doc.main_part, content_type)
    macros = A.package_risks(doc)["macros"]
    notes = []
    if before in ("docm", "dotm") and target in ("docx", "dotx"):
        notes.append("package still contains %d macro part(s); they are inert only if the "
                     "content type is honoured, so remove them deliberately" % len(macros))
    if before in ("dotx", "dotm") and target in ("docx", "docm"):
        notes.append("document converted from template to document type")
    _touch_modified(doc)
    return {"operation": "convert", "from": before, "to": target,
            "expected_extension": extension, "main_content_type": content_type,
            "notes": notes}


def save_a_copy(doc, output_path: str, params=None) -> dict:
    params = params or {}
    root = _core_root(doc)
    if str(params.get("anonymize", "false")).lower() in ("1", "true", "yes"):
        _set_core_prop(doc, "lastModifiedBy", doc.cfg["defaults"]["sanitized_author"])
    existing_title = A.core_properties(doc).get("title") or ""
    suffix = params.get("title_suffix") or "(copy)"
    if str(params.get("stamp_title", "true")).lower() in ("1", "true", "yes"):
        _set_core_prop(doc, "title", (existing_title + " " + suffix).strip())
    _touch_modified(doc)
    doc.mark_dirty("docProps/core.xml")
    doc.save(output_path)
    return {"operation": "copy", "written": output_path,
            "bytes": os.path.getsize(output_path),
            "sha256": hashlib.sha256(open(output_path, "rb").read()).hexdigest(),
            "title": A.core_properties(doc).get("title")}


# --------------------------------------------------------------------------
# backups
# --------------------------------------------------------------------------

def _registry_path(cfg) -> str:
    return os.path.join(state_path(cfg, "backup_dir"), "registry.json")


def _load_registry(cfg) -> list[dict]:
    path = _registry_path(cfg)
    if not os.path.isfile(path):
        return []
    try:
        return json.loads(open(path, "r", encoding="utf-8").read())
    except json.JSONDecodeError:
        return []


def _save_registry(cfg, entries: list[dict]):
    atomic_write_text(_registry_path(cfg), json.dumps(entries, indent=1))


def backup(input_path: str, cfg, params=None) -> dict:
    params = params or {}
    source = validate_input_path(input_path, cfg)
    backup_dir = state_path(cfg, "backup_dir")
    data = open(source, "rb").read()
    digest = hashlib.sha256(data).hexdigest()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = "%s-%s-%s%s" % (re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(source)),
                           stamp, digest[:10], os.path.splitext(source)[1])
    path = os.path.join(backup_dir, name)
    atomic_write_bytes(path, data)
    registry = _load_registry(cfg)
    entry = {"id": digest[:12], "source": source, "backup": path, "sha256": digest,
             "bytes": len(data), "created": _now(), "label": params.get("label")}
    registry.append(entry)
    limit = int(cfg["limits"]["max_backups_per_source"])
    same = [e for e in registry if e["source"] == source]
    if len(same) > limit:
        for stale in same[: len(same) - limit]:
            try:
                os.unlink(stale["backup"])
            except OSError:
                pass
            registry.remove(stale)
    _save_registry(cfg, registry)
    return {"operation": "backup", "backup": path, "id": entry["id"],
            "sha256": digest, "bytes": len(data), "registry_entries": len(registry)}


def list_backups(input_path: str | None, cfg) -> dict:
    registry = _load_registry(cfg)
    source = os.path.abspath(os.path.expanduser(input_path)) if input_path else None
    entries = [e for e in registry if not source or e["source"] == source]
    return {"operation": "backup", "mode": "list",
            "backups": sorted(entries, key=lambda e: e["created"], reverse=True),
            "count": len(entries)}


def restore(input_path: str | None, output_path: str, cfg, params=None) -> dict:
    params = params or {}
    backup_id = params.get("id") or params.get("backup_id")
    backup_path = params.get("backup")
    registry = _load_registry(cfg)
    entry = None
    if backup_id:
        entry = next((e for e in registry if e["id"] == backup_id), None)
    elif backup_path:
        entry = next((e for e in registry
                      if os.path.abspath(e["backup"]) == os.path.abspath(backup_path)), None)
        if entry is None and os.path.isfile(backup_path):
            entry = {"id": "ad-hoc", "backup": os.path.abspath(backup_path), "sha256": None,
                     "source": input_path, "created": None, "bytes": None}
    elif input_path:
        candidates = [e for e in registry
                      if e["source"] == os.path.abspath(os.path.expanduser(input_path))]
        entry = sorted(candidates, key=lambda e: e["created"])[-1] if candidates else None
    if entry is None:
        raise ToolError("NOT_FOUND", "no backup matches the request",
                        {"registry": _registry_path(cfg), "count": len(registry)})
    data = open(entry["backup"], "rb").read()
    digest = hashlib.sha256(data).hexdigest()
    if entry.get("sha256") and digest != entry["sha256"]:
        raise ToolError("INTEGRITY", "backup file hash does not match the registry",
                        {"backup": entry["backup"], "expected": entry["sha256"],
                         "actual": digest})
    atomic_write_bytes(output_path, data)
    return {"operation": "restore", "written": output_path, "backup": entry["backup"],
            "id": entry.get("id"), "verified": True, "bytes": len(data)}


# --------------------------------------------------------------------------
# advisory plan and routing
# --------------------------------------------------------------------------

def route_request(query: str, map_data: dict, limit: int = 5) -> dict:
    tokens = {t for t in re.split(r"[^a-z0-9]+", (query or "").lower()) if len(t) > 2}
    scored = []
    for category in map_data["categories"]:
        for skill in category["skills"]:
            name_tokens = {t for t in re.split(r"[^a-z0-9]+", skill["name"].lower())
                           if len(t) > 2}
            purpose_tokens = {t for t in re.split(r"[^a-z0-9]+", category["purpose"].lower())
                              if len(t) > 2}
            overlap = len(tokens & (name_tokens | set(skill.get("triggers", []))))
            score = overlap * 3 + len(tokens & purpose_tokens)
            if query and query.lower() in skill["name"].lower():
                score += 8
            if score:
                scored.append({
                    "skill": skill["name"], "category": category["name"],
                    "category_id": category["id"], "op": skill["op"],
                    "engine": skill["engine"], "params": skill["params"], "score": score,
                })
    scored.sort(key=lambda item: (-item["score"], item["category_id"], item["skill"]))
    return {"query": query, "candidates": scored[:limit],
            "unmatched": not scored}


def build_plan(skill_name: str, map_data: dict, category_id: int | None = None) -> dict:
    needle = (skill_name or "").strip().lower()
    for category in map_data["categories"]:
        if category["name"].lower() == needle or ("category %d" % category["id"]) == needle:
            return {
                "kind": "category",
                "category": category["name"],
                "category_id": category["id"],
                "purpose": category["purpose"],
                "surfaces": category.get("surfaces") or [],
                "skills": [{"skill": s["name"], "op": s["op"], "engine": s["engine"]}
                           for s in category["skills"]],
                "automation": {
                    "command": "python3 tools-word-doc-agent.py route --param "
                               "query=\"%s\"" % category["name"],
                    "note": "Category-level entry: route narrows to one skill, plan "
                            "expands a single skill into steps."},
                "sequence": [
                    "1. backup the document before destructive steps",
                    "2. run the implementing operation for each skill you need",
                    "3. finish with qa to prove the result on the recipient path",
                ],
            }
    exact = None
    for category in map_data["categories"]:
        if category_id and category["id"] != int(category_id):
            continue
        for skill in category["skills"]:
            if skill["name"].lower() == needle:
                exact = (category, skill)
                break
        if exact:
            break
    if exact is None:
        routed = route_request(skill_name, map_data, limit=1)
        if not routed["candidates"]:
            raise ToolError("NOT_FOUND", "no catalog skill matches %r" % skill_name,
                            {"hint": "route --param query=... to search the catalog"})
        best = routed["candidates"][0]
        for category in map_data["categories"]:
            if category["id"] == best["category_id"]:
                for skill in category["skills"]:
                    if skill["name"] == best["skill"]:
                        exact = (category, skill)
                        break
    category, skill = exact
    surfaces = category.get("surfaces") or []
    op = skill["op"]
    engine = skill["engine"]
    params = skill["params"]
    steps = [
        "Confirm the active Word environment (desktop/Windows, desktop/Mac, web, mobile), "
        "license and administrator policy; this skill assumes the surface '%s'." % (surfaces[0] if surfaces else "Word desktop"),
        "Take a last-known-good copy before changing the document (backup --input FILE); "
        "restore it if the result fails verification.",
        "Perform the change: %s." % _describe_surfaces(surfaces, skill["name"]),
        "Keep the edit reversible: prefer named styles, native Word structures and "
        "tracked changes during review.",
        "Verify the artefact itself: reopen, update fields, and check the recipient path "
        "(qa --input FILE, then inspect the affected section).",
    ]
    automation = None
    if engine == "native":
        example = " ".join(
            "%s %s" % (key, value if isinstance(value, (int, float)) else '"%s"' % value)
            for key, value in params.items())
        automation = {
            "op": op,
            "command": "python3 tools-word-doc-agent.py %s --input FILE%s" % (
                op, (" --param " + example.replace(" ", " --param ")) if example else ""),
            "note": "The tool performs this capability on the OOXML package.",
        }
    else:
        automation = {
            "op": op,
            "command": "python3 tools-word-doc-agent.py inspect --input FILE --json",
            "note": "Word UI or service operation: the tool reports the document state that "
                    "the step depends on and verifies the outcome, but the UI action itself "
                    "stays manual.",
        }
    return {
        "skill": skill["name"],
        "category": category["name"],
        "category_id": category["id"],
        "purpose": category["purpose"],
        "engine": engine,
        "implementing_operation": op,
        "surfaces": surfaces,
        "prerequisites": [
            "a .docx/.docm/.dotx/.dotm file or a blank document",
            "write access to the output location",
            "approval when the change is destructive or publishing-related",
        ],
        "steps": steps,
        "automation": automation,
        "verification": [
            "python3 tools-word-doc-agent.py qa --input OUT.docx",
            "python3 tools-word-doc-agent.py inspect --input OUT.docx --json --deep",
            "reopen in Word and update fields before delivery",
        ],
        "rollback": "restore the pre-change copy: backup --input FILE (before) then "
                    "restore --input FILE --param id=<id> --output FILE",
        "exclusions": [
            "no cloud/tenant-specific configuration is changed",
            "no Word UI automation is attempted; unavailable UI-only behaviour is reported",
        ],
    }


def _describe_surfaces(surfaces: list, skill_name: str) -> str:
    if not surfaces:
        return skill_name
    return "%s (Word surfaces: %s)" % (skill_name, ", ".join(str(s) for s in surfaces[:8]))


def describe_capabilities(map_data: dict) -> dict:
    ops = {}
    for category in map_data["categories"]:
        for skill in category["skills"]:
            info = ops.setdefault(skill["op"], {"catalog_skills": 0, "categories": set(),
                                                "example": skill["name"],
                                                "engine": skill["engine"]})
            info["catalog_skills"] += 1
            info["categories"].add(category["id"])
    return {op: {"catalog_skills": info["catalog_skills"],
                 "categories": sorted(info["categories"]),
                 "example_skill": info["example"],
                 "engine": info["engine"]} for op, info in sorted(ops.items())}
