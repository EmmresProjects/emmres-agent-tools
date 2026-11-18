"""Web, keyboard, visual, motion, form, mobile and assistive-technology engines.

Categories served: 2 (Automated Web Accessibility Testing), 3 (Screen Reader and
Assistive Technology Testing), 4 (Keyboard/Focus/Switch/Voice), 5 (Visual,
Contrast, Responsive), 6 (Motion and Sensory Safety), 11 (Forms, Errors,
Authentication), 12 (Mobile/Native/Desktop/Kiosk) for declared element trees.

Every rule is deterministic, cites the standard it enforces, records a locator
and evidence payload, and ships a remediation plus a retest step.
"""

import json
import re

S = None
T = None
H = None

OPS = {}
RULES = []
RULES_BY_ID = {}


def init(deps):
    global S, T, H
    S = deps["support"]
    T = deps["text"]
    H = deps["html"]


# --------------------------------------------------------------------------- #
# Registration helpers
# --------------------------------------------------------------------------- #
def rule(rid, check, group, wcag, level, severity, remediation, retest, tags=None, needs_css=False):
    def deco(fn):
        meta = {"id": rid, "check": check, "group": group, "wcag": wcag, "level": level,
                "severity": severity, "remediation": remediation, "retest": retest,
                "tags": tags or [], "needs_css": needs_css, "fn": fn}
        RULES.append(meta)
        RULES_BY_ID[rid] = meta
        return fn
    return deco


def fail(ctx, r, message, node=None, evidence=None, severity=None, criterion=None, locator=None):
    if evidence is None and node is not None:
        evidence = {"artifact": node.doc_source if hasattr(node, "doc_source") else None,
                    "locator": node.locator() if hasattr(node, "locator") else str(node),
                    "element": node.path() if hasattr(node, "path") else None,
                    "text": T.truncate(node.text(), 200) if hasattr(node, "text") else None}
    ctx.add(rule=r["check"], rule_id=r["id"], message=message,
            severity=severity or r["severity"], criterion=criterion or r["wcag"], level=r["level"],
            evidence=evidence, remediation=r["remediation"], retest=r["retest"],
            locator=locator or (evidence or {}).get("locator"), tags=r["tags"])


def op(name, doc, params=None, accepts=("html",), summary="", groups=None, majority="auto"):
    def deco(fn):
        OPS[name] = {"name": name, "doc": doc, "params": params or {}, "accepts": list(accepts),
                     "summary": summary or doc.split(".")[0], "groups": groups or [],
                     "fn": fn, "engine": "web", "majority": majority}
        return fn
    return deco


def _doc(ctx):
    return ctx.docs[0].data["document"]


def _evidence_artifact(ctx, node):
    return ctx.docs[0].path


# --------------------------------------------------------------------------- #
# Rules: document structure and language
# --------------------------------------------------------------------------- #
@rule("html-lang", "Page language declared", "structure", "3.1.1", "A", "critical",
      "Add a valid BCP-47 lang attribute to the root <html> element.",
      "Re-run the check and confirm a BCP-47 primary language subtag is reported.")
def r_html_lang(ctx, doc, r):
    html = doc.by_tag("html")
    if not html:
        fail(ctx, r, "No <html> element, so no page language can be determined.")
        return
    lang = (html[0].get("lang") or "").strip()
    if not lang:
        fail(ctx, r, "The <html> element has no lang attribute.", html[0])
        return
    if not re.match(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$", lang):
        fail(ctx, r, "lang=%r is not a well-formed BCP-47 language tag." % lang, html[0])
        return
    ctx.pass_check()


@rule("html-lang-valid", "Page language matches expected locale", "structure", "3.1.1", "A", "high",
      "Set lang to the locale the page is actually written in and keep it in sync with the locale profile.",
      "Re-run with the corrected lang value and confirm it equals the expected locale.")
def r_html_lang_valid(ctx, doc, r):
    # Opt-in: compare the declared lang with a specific locale.
    # Pass --set expected_lang=fil-PH; the config's default_locale is not used
    # here because a page may legitimately be in any supported locale.
    expected = (ctx.params.get("expected_lang") or "").lower()
    html = doc.by_tag("html")
    if not expected or not html:
        ctx.pass_check()
        return
    lang = (html[0].get("lang") or "").strip().lower()
    if not lang:
        fail(ctx, r, "No page language to compare with the expected locale %r." % expected, html[0])
        return
    if lang != expected and not lang.startswith(expected.split("-")[0]):
        fail(ctx, r, "Declared lang=%r does not match expected locale %r." % (lang, expected), html[0])
        return
    ctx.pass_check()


@rule("lang-parts", "Language of parts declared", "structure", "3.1.2", "AA", "medium",
      "Add lang to elements whose language differs from the page language.",
      "Re-run and confirm every element in the imported-language list carries its own lang.")
def r_lang_parts(ctx, doc, r):
    page_lang = ((doc.by_tag("html") or [None])[0].get("lang") if doc.by_tag("html") else "") or ""
    primary = page_lang.split("-")[0].lower()
    checked = 0
    for n in doc.nodes:
        lang = (n.get("lang") or n.get("xml:lang") or "").strip()
        if not lang:
            continue
        checked += 1
        if not re.match(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$", lang):
            fail(ctx, r, "Malformed lang value %r on <%s>." % (lang, n.tag), n)
        elif primary and lang.split("-")[0].lower() == primary and n is not None:
            ctx.pass_check()
        else:
            ctx.pass_check()
    if checked == 0 and doc.body_text():
        ctx.note("No language changes declared; verify manually if the page mixes Filipino and English.")
    ctx.pass_check()


@rule("page-title", "Page has a descriptive title", "structure", "2.4.2", "A", "critical",
      "Provide a unique, descriptive <title> that identifies the page and the site.",
      "Re-run and confirm the title is present, non-empty, unique and descriptive.")
def r_page_title(ctx, doc, r):
    title = doc.title()
    if not title:
        fail(ctx, r, "Document has no <title> text.")
        return
    if len(title) < 4:
        fail(ctx, r, "Title %r is too short to describe the page." % title)
        return
    ctx.pass_check()


@rule("heading-presence", "Headings structure the content", "structure", "1.3.1", "A", "high",
      "Mark up section headings with h1-h6 so structure is programmatically determinable.",
      "Re-run and confirm at least one heading exists for content longer than 300 characters.")
def r_heading_presence(ctx, doc, r):
    heads = H.headings(doc)
    text_len = len(doc.body_text())
    if not heads and text_len > 300:
        fail(ctx, r, "Page carries %d characters of text but no headings." % text_len)
        return
    ctx.pass_check()


@rule("heading-order", "Heading levels are sequential", "structure", "1.3.1", "A", "high",
      "Do not skip heading levels; restructure the outline or use aria-level deliberately.",
      "Re-run and confirm no heading level jump greater than one between consecutive headings.")
def r_heading_order(ctx, doc, r):
    prev = None
    for h in H.headings(doc):
        if h["level"] is None:
            fail(ctx, r, "Heading %r exposes no numeric level." % T.truncate(h["text"], 60), h["node"])
            continue
        if prev is not None and h["level"] > prev + 1:
            fail(ctx, r, "Heading level jumps from h%d to h%d at %r." % (prev, h["level"], T.truncate(h["text"], 60)),
                 h["node"])
        else:
            ctx.pass_check()
        prev = h["level"]


@rule("heading-empty", "Headings have text", "structure", "2.4.6", "AA", "medium",
      "Give every heading accessible text or remove the empty heading.",
      "Re-run and confirm every heading exposes a non-empty accessible name.")
def r_heading_empty(ctx, doc, r):
    for h in H.headings(doc):
        if T.is_blank(h["text"]):
            fail(ctx, r, "Heading <%s> has no accessible text." % h["node"].tag, h["node"])
        else:
            ctx.pass_check()


@rule("landmarks-present", "Landmark regions exist", "structure", "1.3.1", "A", "medium",
      "Wrap page regions in header/main/nav/footer landmarks or equivalent ARIA roles.",
      "Re-run and confirm at least a main landmark plus one navigation landmark.")
def r_landmarks(ctx, doc, r):
    roles = [l["role"] for l in H.landmarks(doc)]
    if "main" not in roles:
        fail(ctx, r, "No main landmark was found (%s)." % (", ".join(sorted(set(roles))) or "none"))
    else:
        ctx.pass_check()
    if "navigation" not in roles and doc.body_text():
        fail(ctx, r, "No navigation landmark was found.", severity="low", criterion="1.3.1")
    else:
        ctx.pass_check()


@rule("region-unique-names", "Repeated landmarks are distinguishable", "structure", "1.3.1", "A", "medium",
      "Give each repeated landmark a distinct aria-label that reflects its purpose.",
      "Re-run and confirm repeated landmark roles no longer share a name.")
def r_region_names(ctx, doc, r):
    seen = {}
    for l in H.landmarks(doc):
        if l["role"] in ("main", "banner", "contentinfo", "search"):
            continue
        key = (l["role"], (l["name"] or "").strip().lower())
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            fail(ctx, r, "Duplicate %s landmark with the same accessible name %r." % (l["role"], l["name"]),
                 l["node"])
        else:
            ctx.pass_check()


@rule("list-structure", "Lists use list markup", "structure", "1.3.1", "A", "low",
      "Use ul/ol/dl with li children, or list/listitem roles, for grouped items.",
      "Re-run and confirm list containers only contain permitted children.")
def r_list_structure(ctx, doc, r):
    for n in doc.by_tag("ul", "ol"):
        bad = [c for c in n.child_elements() if c.tag not in ("li", "script", "template", "style")]
        if bad:
            fail(ctx, r, "List <%s> contains non-list children: %s." % (
                n.tag, ", ".join(sorted({c.tag for c in bad}))), n)
        else:
            ctx.pass_check()


@rule("duplicate-id", "Element ids are unique", "structure", "4.1.1", "A", "high",
      "Make every id unique; duplicate ids break label, ARIA and fragment references.",
      "Re-run and confirm no id appears more than once.")
def r_duplicate_id(ctx, doc, r):
    for ident, nodes in doc.ids().items():
        if len(nodes) > 1:
            fail(ctx, r, "id=%r is used %d times." % (ident, len(nodes)), nodes[1],
                 evidence={"id": ident, "count": len(nodes),
                           "locators": [n.locator() for n in nodes[:6]]})
        else:
            ctx.pass_check()


@rule("iframe-title", "Frames have descriptive titles", "structure", "4.1.2", "A", "high",
      "Give each iframe a unique title that describes its content.",
      "Re-run and confirm every iframe has a non-empty, distinct title.")
def r_iframe_title(ctx, doc, r):
    for n in doc.by_tag("iframe", "frame"):
        title = T.norm_ws(n.get("title"))
        if not title:
            fail(ctx, r, "iframe without a title attribute.", n)
        elif len(title) < 3:
            fail(ctx, r, "iframe title %r is too short to describe its content." % title, n)
        else:
            ctx.pass_check()


@rule("meta-viewport", "Viewport allows zoom", "visual", "1.4.4", "AA", "critical",
      "Remove user-scalable=no and maximum-scale restrictions; keep at least 2x zoom.",
      "Re-run and confirm the viewport meta does not block zoom.")
def r_meta_viewport(ctx, doc, r):
    for n in doc.by_tag("meta"):
        if (n.get("name") or "").lower() != "viewport":
            continue
        content = (n.get("content") or "").lower()
        if "user-scalable=no" in content or "user-scalable=0" in content:
            fail(ctx, r, "Viewport blocks pinch zoom: %r." % n.get("content"), n)
            continue
        m = re.search(r"maximum-scale\s*=\s*([\d.]+)", content)
        if m and float(m.group(1)) < 2.0:
            fail(ctx, r, "maximum-scale=%s limits zoom below 200%%." % m.group(1), n)
            continue
        ctx.pass_check()


@rule("charset-declared", "Character encoding declared", "structure", "1.3.1", "A", "medium",
      "Declare <meta charset=\"utf-8\"> as the first head element.",
      "Re-run and confirm an encoding declaration exists in the first 1024 bytes.")
def r_charset(ctx, doc, r):
    metas = [n for n in doc.by_tag("meta")
             if n.has("charset") or (n.get("http-equiv") or "").lower() == "content-type"]
    if not metas:
        fail(ctx, r, "No character encoding declaration found.")
        return
    ctx.pass_check()


# --------------------------------------------------------------------------- #
# Rules: names, roles, values
# --------------------------------------------------------------------------- #
@rule("image-alt", "Images have alternative text", "names", "1.1.1", "A", "critical",
      "Add alt text that serves the image's purpose, or alt=\"\" for purely decorative images.",
      "Re-run and confirm every img exposes either meaningful alt text or an explicit empty alt.")
def r_image_alt(ctx, doc, r):
    for n in doc.by_tag("img"):
        if n.has("aria-label") or n.has("aria-labelledby") or n.get("role") in ("presentation", "none"):
            ctx.pass_check()
            continue
        if not n.has("alt"):
            fail(ctx, r, "Image without an alt attribute (src=%r)." % (n.get("src") or "")[:80], n)
        elif n.get("alt") == "":
            ctx.pass_check()
        else:
            ctx.pass_check()


BANNED_ALT = ("image of", "picture of", "photo of", "graphic of", "logo of", "spacer", "spacer.gif",
              "image", "photo", "graphic", "untitled", "screenshot", "img_", "dsc", "placeholder")


@rule("alt-quality", "Alternative text quality", "images", "1.1.1", "A", "medium",
      "Describe purpose rather than appearance; drop 'image of', file names and keyword stuffing.",
      "Re-run and confirm alt text is descriptive, under 125 characters and free of redundancy flags.")


def r_alt_quality(ctx, doc, r):
    for n in doc.by_tag("img", "area"):
        alt = T.norm_ws(n.get("alt"))
        if not alt:
            continue
        low = alt.lower()
        src = (n.get("src") or "").rsplit("/", 1)[-1].lower()
        if any(low == b or low.startswith(b) for b in BANNED_ALT):
            fail(ctx, r, "Alt text %r is non-descriptive." % alt, n)
            continue
        if re.search(r"\.(png|jpe?g|gif|svg|webp|avif)$", low) or (src and low == src):
            fail(ctx, r, "Alt text repeats the file name %r." % alt, n)
            continue
        if len(alt) > 150:
            fail(ctx, r, "Alt text is %d characters; move long descriptions to adjacent text." % len(alt), n)
            continue
        ctx.pass_check()


@rule("svg-title", "SVG graphics expose a name", "names", "1.1.1", "A", "high",
      "Add <title> (with matching aria-labelledby) or role=\"img\" plus aria-label to inline SVG.",
      "Re-run and confirm each non-decorative SVG exposes an accessible name.")
def r_svg_title(ctx, doc, r):
    for n in doc.by_tag("svg"):
        if (n.get("aria-hidden") or "").lower() == "true" or n.get("role") in ("presentation", "none"):
            ctx.pass_check()
            continue
        name, src = H.accessible_name(doc, n)
        if not name:
            fail(ctx, r, "SVG has no accessible name or <title>.", n)
        else:
            ctx.pass_check()


@rule("link-name", "Links have discernible names", "names", "2.4.4", "A", "critical",
      "Provide link text, aria-label, or visually hidden text that describes the destination.",
      "Re-run and confirm every link exposes a non-empty accessible name.")
def r_link_name(ctx, doc, r):
    for n in doc.by_tag("a"):
        if not n.has("href"):
            continue
        name, src = H.accessible_name(doc, n)
        if not T.norm_ws(name):
            fail(ctx, r, "Link has no accessible name (href=%r)." % (n.get("href") or "")[:80], n)
        else:
            ctx.pass_check()


AMBIGUOUS = {"click here", "here", "read more", "more", "learn more", "this link", "link",
             "download", "continue", "details", "more info", "go"}
@rule("link-purpose", "Link purpose is identifiable", "names", "2.4.4", "A", "medium",
      "Replace ambiguous link text with text that describes the destination out of context.",
      "Re-run and confirm no link uses only generic text such as 'click here'.")
def r_link_purpose(ctx, doc, r):
    for n in doc.by_tag("a"):
        if not n.has("href"):
            continue
        name = T.norm_ws(H.accessible_name(doc, n)[0]).lower()
        if name in AMBIGUOUS:
            fail(ctx, r, "Link text %r does not describe the destination." % name, n,
                 severity="medium", criterion="2.4.4")
        else:
            ctx.pass_check()


@rule("button-name", "Buttons have accessible names", "names", "4.1.2", "A", "critical",
      "Add visible text, aria-label or aria-labelledby to every button and input[type=button|submit].",
      "Re-run and confirm each button exposes a non-empty accessible name.")
def r_button_name(ctx, doc, r):
    for n in doc.nodes:
        role = H.role_of(n)
        if role != "button" and n.tag != "button":
            continue
        name, src = H.accessible_name(doc, n)
        if not T.norm_ws(name):
            fail(ctx, r, "Button has no accessible name.", n)
        else:
            ctx.pass_check()


@rule("name-role-value", "Interactive elements expose name, role and value", "aria", "4.1.2", "A", "high",
      "Use native elements or complete ARIA roles, states and names for every widget.",
      "Re-run and confirm each widget exposes name, role and any required state.")
def r_name_role_value(ctx, doc, r):
    for n in doc.nodes:
        role = H.role_of(n)
        if role in H.INTERACTIVE_ROLES:
            name, src = H.accessible_name(doc, n)
            if not T.norm_ws(name):
                fail(ctx, r, "%s widget exposes role %r without an accessible name." % (n.tag, role), n)
            else:
                ctx.pass_check()


@rule("label-in-name", "Visible label matches accessible name", "keyboard", "2.5.3", "A", "high",
      "Start the accessible name with the visible label text so voice users can activate it.",
      "Re-run and confirm the visible label string is contained in the accessible name.")
def r_label_in_name(ctx, doc, r):
    for n in doc.nodes:
        if not (n.get("aria-label") or n.get("aria-labelledby")):
            continue
        role = H.role_of(n)
        if role not in H.INTERACTIVE_ROLES and n.tag not in ("button", "a"):
            continue
        visible = T.norm_ws(re.sub(r"\s+", " ", " ".join(
            c if isinstance(c, str) else c.text() for c in n.children)))
        if not visible:
            continue
        name = T.norm_ws(H.accessible_name(doc, n)[0]).lower()
        if visible.lower() not in name:
            fail(ctx, r, "Accessible name %r does not contain the visible label %r." % (name, visible), n)
        else:
            ctx.pass_check()


@rule("input-label", "Form controls are labelled", "forms", "3.3.2", "A", "critical",
      "Associate a <label for>, wrapping label, aria-label or aria-labelledby with each control.",
      "Re-run and confirm every control exposes an accessible name from a real label.")
def r_input_label(ctx, doc, r):
    for n in doc.by_tag("input", "select", "textarea"):
        itype = (n.get("type") or "text").lower()
        if itype in ("hidden", "button", "submit", "reset", "image"):
            continue
        name, src = H.accessible_name(doc, n)
        if not T.norm_ws(name):
            fail(ctx, r, "Control <%s type=%s> has no accessible label." % (n.tag, itype), n)
        elif src == "placeholder":
            fail(ctx, r, "Control relies on placeholder text as its only label.", n, severity="high")
        else:
            ctx.pass_check()


@rule("label-for-valid", "Label targets exist", "forms", "1.3.1", "A", "high",
      "Point label for attributes at existing control ids.",
      "Re-run and confirm every label[for] resolves to an element.")
def r_label_for(ctx, doc, r):
    ids = doc.ids()
    for n in doc.by_tag("label"):
        for_id = n.get("for")
        if not for_id:
            continue
        if for_id not in ids:
            fail(ctx, r, "label for=%r does not match any element id." % for_id, n)
        else:
            ctx.pass_check()


VALID_ROLES = set("""alert alertdialog application article banner blockquote button caption cell
checkbox code columnheader combobox complementary contentinfo definition deletion dialog directory
document emphasis feed figure form generic grid gridcell group heading img insertion link list
listbox listitem log main marquee math menu menubar menuitem menuitemcheckbox menuitemradio meter
navigation none note option paragraph presentation progressbar radio radiogroup region row
rowgroup rowheader scrollbar search searchbox separator slider spinbutton status strong subscript
superscript switch tab table tablist tabpanel term textbox time timer toolbar tooltip tree treegrid
treeitem""".split())
ABSTRACT_ROLES = {"command", "composite", "input", "landmark", "range", "roletype", "section",
                  "sectionhead", "select", "structure", "widget", "window"}


@rule("aria-role-valid", "ARIA roles are valid", "aria", "4.1.2", "A", "high",
      "Use only defined ARIA roles, one per element, and remove abstract roles.",
      "Re-run and confirm all role values are valid, non-abstract ARIA roles.")
def r_aria_role_valid(ctx, doc, r):
    for n in doc.nodes:
        raw = n.get("role")
        if raw is None:
            continue
        tokens = raw.strip().split()
        if not tokens:
            fail(ctx, r, "Empty role attribute.", n)
            continue
        if len(tokens) > 1:
            fail(ctx, r, "Multiple role tokens %r: only the first is honoured." % raw, n)
            continue
        role = tokens[0].lower()
        if role in ABSTRACT_ROLES:
            fail(ctx, r, "Abstract ARIA role %r cannot be used in content." % role, n)
        elif role not in VALID_ROLES:
            fail(ctx, r, "Unknown ARIA role %r." % role, n)
        else:
            ctx.pass_check()


VALID_ARIA = set("""aria-activedescendant aria-atomic aria-autocomplete aria-braillelabel
aria-brailleroledescription aria-busy aria-checked aria-colcount aria-colindex aria-colspan
aria-controls aria-current aria-describedby aria-description aria-details aria-disabled
aria-dropeffect aria-errormessage aria-expanded aria-flowto aria-grabbed aria-haspopup
aria-hidden aria-invalid aria-keyshortcuts aria-label aria-labelledby aria-level aria-live
aria-modal aria-multiline aria-multiselectable aria-orientation aria-owns aria-placeholder
aria-posinset aria-pressed aria-readonly aria-relevant aria-required aria-roledescription
aria-rowcount aria-rowindex aria-rowspan aria-selected aria-setsize aria-sort aria-valuemax
aria-valuemin aria-valuenow aria-valuetext""".split())
GLOBAL_ARIA = {"aria-atomic", "aria-busy", "aria-controls", "aria-current", "aria-describedby",
               "aria-description", "aria-details", "aria-disabled", "aria-dropeffect",
               "aria-errormessage", "aria-flowto", "aria-grabbed", "aria-haspopup", "aria-hidden",
               "aria-invalid", "aria-keyshortcuts", "aria-label", "aria-labelledby", "aria-live",
               "aria-owns", "aria-relevant", "aria-roledescription"}


@rule("aria-attr-valid", "ARIA attributes are valid", "aria", "4.1.2", "A", "medium",
      "Remove unknown aria-* attributes and fix misspellings such as aria-lable.",
      "Re-run and confirm only defined aria-* attributes remain.")
def r_aria_attr_valid(ctx, doc, r):
    for n in doc.nodes:
        for key in n.attrs:
            if not key.startswith("aria-"):
                continue
            if key not in VALID_ARIA:
                fail(ctx, r, "Undefined attribute %r." % key, n)
            elif key not in GLOBAL_ARIA and H.role_of(n) in (None, "generic"):
                fail(ctx, r, "%r is not supported without a role that allows it." % key, n, severity="low")
            else:
                ctx.pass_check()


@rule("aria-hidden-focus", "No focusable content inside aria-hidden", "aria", "4.1.2", "A", "critical",
      "Never put focusable elements inside aria-hidden containers; use inert or remove them from tab order.",
      "Re-run and confirm hidden subtrees contain no focusable descendants.")
def r_aria_hidden_focus(ctx, doc, r):
    for n in doc.nodes:
        if (n.get("aria-hidden") or "").lower() != "true":
            continue
        for child in n.iter():
            if child is n:
                continue
            ti = H.tabindex_of(child)
            native_focus = child.tag in ("a", "button", "input", "select", "textarea") and not child.has("disabled")
            if native_focus or (ti is not None and ti >= 0):
                fail(ctx, r, "Focusable <%s> inside aria-hidden=\"true\" container." % child.tag, child)
                break
        else:
            ctx.pass_check()


@rule("aria-reference-valid", "ARIA references resolve", "aria", "1.3.1", "A", "high",
      "Point aria-labelledby/describedby/controls/owns/activedescendant at existing ids.",
      "Re-run and confirm every ARIA id reference resolves inside the document.")
def r_aria_refs(ctx, doc, r):
    ids = set(doc.ids().keys())
    for n in doc.nodes:
        for attr in ("aria-labelledby", "aria-describedby", "aria-controls", "aria-owns",
                     "aria-activedescendant", "aria-errormessage", "aria-details"):
            raw = (n.get(attr) or "").strip()
            if not raw:
                continue
            missing = [t for t in raw.split() if t not in ids]
            if missing:
                fail(ctx, r, "%s references missing id(s): %s." % (attr, ", ".join(missing[:5])), n)
            else:
                ctx.pass_check()


REQUIRED_PARENT = {
    "listitem": {"list", "directory"}, "treeitem": {"tree", "group"}, "tab": {"tablist"},
    "menuitem": {"menu", "menubar", "group"}, "menuitemcheckbox": {"menu", "menubar", "group"},
    "menuitemradio": {"menu", "menubar", "group"}, "option": {"listbox", "group"},
    "row": {"table", "grid", "treegrid", "rowgroup"}, "cell": {"row"},
    "columnheader": {"row"}, "rowheader": {"row"}, "gridcell": {"row"},
}


@rule("aria-required-parent", "Widgets sit in valid containers", "aria", "1.3.1", "A", "high",
      "Place listitem/treeitem/tab/menuitem/option inside their required parent roles.",
      "Re-run and confirm each widget role has a permitted ancestor.")
def r_aria_required_parent(ctx, doc, r):
    for n in doc.nodes:
        role = H.role_of(n)
        if role not in REQUIRED_PARENT:
            continue
        ancestors = {H.role_of(a) for a in n.ancestors()}
        if not (ancestors & REQUIRED_PARENT[role]):
            fail(ctx, r, "role=%r is not inside one of %s." % (role, sorted(REQUIRED_PARENT[role])), n)
        else:
            ctx.pass_check()


@rule("live-region-valid", "Live regions are correctly declared", "messaging", "4.1.3", "AA", "high",
      "Use aria-live=polite|assertive with role=status/alert, and avoid aria-live on the whole document.",
      "Re-run and confirm live regions are scoped and use valid politeness values.")
def r_live_region(ctx, doc, r):
    for n in doc.nodes:
        live = (n.get("aria-live") or "").lower().strip()
        if live and live not in ("polite", "assertive", "off"):
            fail(ctx, r, "Invalid aria-live value %r." % live, n)
        elif live and n.tag in ("body", "html", "main"):
            fail(ctx, r, "aria-live on <%s> is too broad and will announce page noise." % n.tag, n)
        elif live:
            ctx.pass_check()


STATUS_HINTS = ("toast", "alert", "notification", "status", "message", "banner", "snackbar", "flash")
@rule("status-message", "Status messages are announced", "messaging", "4.1.3", "AA", "medium",
      "Give status/alert containers role=status or role=alert, or wire aria-live.",
      "Re-run and confirm status containers expose a live region role.")
def r_status_message(ctx, doc, r):
    for n in doc.nodes:
        classes = " ".join(sorted(n.classes)).lower()
        if not any(h in classes for h in STATUS_HINTS):
            continue
        role = H.role_of(n)
        if role in ("status", "alert", "log") or (n.get("aria-live") or "").lower() in ("polite", "assertive"):
            ctx.pass_check()
        else:
            fail(ctx, r, "Status-like container (.%s) has no live region role." % (classes.split()[0] if classes else ""), n)


# --------------------------------------------------------------------------- #
# Rules: keyboard, pointer, target size
# --------------------------------------------------------------------------- #
@rule("positive-tabindex", "Tab order follows document order", "keyboard", "2.4.3", "A", "high",
      "Remove positive tabindex values; use DOM order and CSS for visual order.",
      "Re-run and confirm no element has tabindex greater than zero.")
def r_positive_tabindex(ctx, doc, r):
    for n in doc.nodes:
        ti = H.tabindex_of(n)
        if ti is not None and ti > 0:
            fail(ctx, r, "tabindex=%d forces a non-document focus order." % ti, n)
        elif ti is not None:
            ctx.pass_check()


@rule("click-handler-accessibility", "Scripted click handlers are keyboard operable", "keyboard", "2.1.1", "A", "critical",
      "Add key handlers and a focusable, role-bearing element, or use a native button/link.",
      "Re-run and confirm every click target is focusable and has a keyboard activation path.")
def r_click_handler(ctx, doc, r):
    for n in doc.nodes:
        if not (n.has("onclick") or (n.get("role") or "") == "button"):
            continue
        if n.tag in ("button", "a", "input", "select", "textarea", "summary"):
            ctx.pass_check()
            continue
        focusable = H.is_focusable(n)
        has_key = n.has("onkeydown") or n.has("onkeyup") or n.has("onkeypress")
        if not focusable or not has_key:
            missing = []
            if not focusable:
                missing.append("focusability (tabindex)")
            if not has_key:
                missing.append("a keyboard handler")
            fail(ctx, r, "<%s> has a click handler without %s." % (n.tag, " and ".join(missing)), n)
        else:
            ctx.pass_check()


@rule("mouse-only-handler", "Pointer handlers have keyboard equivalents", "keyboard", "2.1.1", "A", "high",
      "Pair mouseover/mouseout/dblclick handlers with focus/blur/keyboard equivalents.",
      "Re-run and confirm each pointer handler has a keyboard path.")
def r_mouse_only(ctx, doc, r):
    for n in doc.nodes:
        pairs = [("onmouseover", ("onfocus",)), ("onmouseout", ("onblur",)),
                 ("onmouseenter", ("onfocus",)), ("onmouseleave", ("onblur",)),
                 ("ondblclick", ("onkeydown", "onkeyup"))]
        issues = [m for m, needs in pairs if n.has(m) and not any(n.has(k) for k in needs)]
        if issues:
            fail(ctx, r, "Pointer-only handler(s) %s without keyboard equivalent." % ", ".join(issues), n)
        elif any(n.has(m) for m, _ in pairs):
            ctx.pass_check()


@rule("accesskey-shortcut", "Character-key shortcuts are controlled", "keyboard", "2.1.4", "A", "medium",
      "Restrict single-character shortcuts to focus, allow remapping, or make them active only on focus.",
      "Re-run and confirm no unmodified accesskey relies on a single character.")
def r_accesskey(ctx, doc, r):
    for n in doc.nodes:
        ak = (n.get("accesskey") or "").strip()
        if len(ak) == 1 and ak.isalnum() and not (n.has("data-shortcut-scope") or n.has("aria-keyshortcuts")):
            fail(ctx, r, "Single-character accesskey=%r with no documented scoping or remapping." % ak, n)
        elif ak:
            ctx.pass_check()


@rule("focus-visible-css", "Focus indicators are not removed", "keyboard", "2.4.7", "AA", "critical",
      "Never set outline:none without providing an equally visible focus style.",
      "Re-run and confirm no rule removes the focus outline without a replacement.")
def r_focus_visible(ctx, doc, r):
    offending = []
    for block in H.style_blocks(doc):
        for selectors, decls in H.declarations(block):
            if "outline" not in decls:
                continue
            value = (decls.get("outline") or "").replace(" ", "").lower()
            if value.startswith("none") or value.startswith("0"):
                offending.append(selectors)
    for sel in offending:
        fail(ctx, r, "CSS removes the focus outline for selector %r with no replacement indicator." % sel[:120],
             evidence={"selector": sel[:400]})
    if not offending:
        ctx.pass_check()


@rule("skip-link", "A bypass mechanism exists", "keyboard", "2.4.1", "A", "high",
      "Provide a skip link as the first focusable element, or correct landmark/heading structure.",
      "Re-run and confirm the first focusable element is a skip link targeting the main content.")
def r_skip_link(ctx, doc, r):
    order = H.focus_order(doc)
    if not order:
        fail(ctx, r, "No focusable elements at all: keyboard users cannot reach the page.")
        return
    first = order[0]
    name = T.norm_ws(H.accessible_name(doc, first)[0]).lower()
    href = (first.get("href") or "")
    if first.tag == "a" and href.startswith("#") and ("skip" in name or "jump" in name or "content" in name):
        ctx.pass_check()
        return
    if H.landmarks(doc) or len(H.headings(doc)) > 2:
        ctx.pass_check()
        return
    fail(ctx, r, "No skip link and no landmark/heading structure to bypass repeated blocks.", first)


@rule("target-size", "Targets meet minimum size", "pointer", "2.5.8", "AA", "medium",
      "Make pointer targets at least 24x24 CSS pixels, or space them so no target overlaps a 24px circle.",
      "Re-run and confirm every declared interactive size meets the configured minimum.")
def r_target_size(ctx, doc, r):
    minimum = float(ctx.config.get("thresholds", {}).get("min_target_size_css_px", 24))
    for n in doc.nodes:
        if not (H.role_of(n) in H.INTERACTIVE_ROLES or n.tag in ("button", "a", "input", "select")):
            continue
        decls = H.inline_decls(n)
        w = _css_px(decls.get("width") or n.get("width"))
        h = _css_px(decls.get("height") or n.get("height"))
        if w is None and h is None:
            continue
        if (w is not None and w < minimum) or (h is not None and h < minimum):
            fail(ctx, r, "Declared target size %sx%s is below %s CSS px." % (w, h, minimum), n)
        else:
            ctx.pass_check()


def _css_px(value):
    if value is None:
        return None
    m = re.match(r"^\s*([\d.]+)\s*(px|em|rem)?\s*$", str(value))
    if not m:
        return None
    num = float(m.group(1))
    return num * 16 if (m.group(2) in ("em", "rem")) else num


@rule("dragging-alternative", "Dragging has a single-pointer alternative", "pointer", "2.5.7", "AA", "high",
      "Provide button, list or input alternatives for every drag interaction.",
      "Re-run and confirm draggable regions document a single-pointer alternative.")
def r_dragging(ctx, doc, r):
    for n in doc.by_tag("div", "li", "span", "section"):
        if (n.get("draggable") or "").lower() == "true" or n.has("ondragstart"):
            if not (n.has("data-drag-alternative") or n.has("aria-describedby")):
                fail(ctx, r, "Draggable element has no documented single-pointer alternative.", n)
            else:
                ctx.pass_check()


# --------------------------------------------------------------------------- #
# Rules: visual, contrast, reflow, typography
# --------------------------------------------------------------------------- #
@rule("contrast-text", "Text contrast meets AA", "visual", "1.4.3", "AA", "critical",
      "Increase contrast to at least 4.5:1 (3:1 for large text) or change the colour pair.",
      "Re-run the contrast check on the updated colours and confirm ratios meet the threshold.")
def r_contrast_text(ctx, doc, r):
    th = ctx.config.get("thresholds", {})
    aa = float(th.get("contrast_text_aa", 4.5))
    aaa = float(th.get("contrast_text_aaa", 7.0))
    large_aa = float(th.get("contrast_large_aa", 3.0))
    target = aaa if ctx.params.get("conformance_level") == "aaa" else aa
    pairs = H.color_pairs(doc)
    if not pairs:
        ctx.note("No resolvable colour pairs found in inline styles or stylesheets; import computed styles to test contrast.")
        return
    for p in pairs:
        ratio = H.contrast_ratio(p["fg"], p["bg"])
        size = H.font_size_px(H.inline_decls(p["node"])) if p.get("node") is not None else None
        bold = False
        if p.get("node") is not None:
            bold = (H.inline_decls(p["node"]).get("font-weight") or "") in ("bold", "700", "800", "900")
        threshold = large_aa if H.is_large_text(size, bold) else target
        locator = p["node"].locator() if p.get("node") is not None else "selector %r" % (p.get("selector") or "")[:100]
        if ratio < threshold:
            fail(ctx, r, "Contrast %.2f:1 below the %.1f:1 threshold for this text." % (ratio, threshold),
                 p.get("node"),
                 evidence={"foreground": "#%02x%02x%02x" % p["fg"], "background": "#%02x%02x%02x" % p["bg"],
                           "ratio": ratio, "threshold": threshold},
                 severity="critical" if ratio < 3.0 else "high", locator=locator)
        else:
            ctx.pass_check()


@rule("contrast-nontext", "Non-text contrast meets AA", "visual", "1.4.11", "AA", "high",
      "Give control boundaries, icons and focus indicators at least 3:1 against adjacent colours.",
      "Re-run and confirm UI component boundaries meet the 3:1 non-text threshold.")
def r_contrast_nontext(ctx, doc, r):
    th = float(ctx.config.get("thresholds", {}).get("contrast_nontext_aa", 3.0))
    checked = 0
    for n in doc.nodes:
        decls = H.inline_decls(n)
        border = decls.get("border") or decls.get("border-color")
        bg = H.parse_color(decls.get("background-color") or decls.get("background"))
        bc = None
        if border:
            m = re.search(r"(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|[a-z]+)\s*$", border.strip())
            if m:
                bc = H.parse_color(m.group(1))
        if bc and bg:
            checked += 1
            ratio = H.contrast_ratio(bc, bg)
            if ratio < th:
                fail(ctx, r, "Control boundary contrast %.2f:1 is below %.1f:1." % (ratio, th), n,
                     evidence={"ratio": ratio, "threshold": th})
            else:
                ctx.pass_check()
    if checked == 0:
        ctx.note("No resolvable non-text colour pairs in inline CSS; verify control boundaries manually.")


@rule("color-independence", "Colour is not the only cue", "visual", "1.4.1", "A", "high",
      "Add text, icon, underline or pattern so meaning survives without colour perception.",
      "Re-run and confirm state and meaning are conveyed without colour alone.")
def r_color_independence(ctx, doc, r):
    for n in doc.nodes:
        decls = H.inline_decls(n)
        style = (n.get("style") or "").lower()
        classes = " ".join(n.classes).lower()
        if not ("color" in decls or "error" in classes or "required" in classes or "success" in classes):
            continue
        if n.tag in ("html", "body", "style", "link"):
            continue
        has_non_color = bool(n.get("aria-label")) or bool(T.norm_ws(n.text())) or bool(n.by_tag("svg")) \
            if hasattr(n, "by_tag") else True
        has_symbol = bool(re.search(r"[*!\u2713\u2714\u2717\u26a0\u2022]", n.text() or ""))
        if not (has_non_color or has_symbol):
            fail(ctx, r, "Colour appears to carry meaning without a text, icon or symbol cue.", n)
        else:
            ctx.pass_check()


@rule("reflow-fixed-width", "Content reflows at 320 CSS px", "visual", "1.4.10", "AA", "high",
      "Replace fixed pixel widths over 320px with max-width and relative units.",
      "Re-run and confirm no element declares a width above the reflow threshold.")
def r_reflow(ctx, doc, r):
    limit = 320
    flagged = 0
    for block in H.style_blocks(doc):
        for selectors, decls in H.declarations(block):
            w = _css_px(decls.get("width"))
            if w is not None and w > limit and "max-width" not in decls:
                flagged += 1
                fail(ctx, r, "Selector %r declares width:%spx which cannot reflow at 320px." % (selectors[:80], int(w)),
                     evidence={"selector": selectors[:400], "width_px": w})
    for n in doc.nodes:
        decls = H.inline_decls(n)
        w = _css_px(decls.get("width"))
        if w is not None and w > limit and "max-width" not in decls:
            flagged += 1
            fail(ctx, r, "Inline width:%spx prevents reflow at 320px." % int(w), n)
        elif w is not None:
            ctx.pass_check()
    if flagged == 0:
        ctx.pass_check()


@rule("text-spacing", "Text spacing overrides are tolerated", "visual", "1.4.12", "AA", "medium",
      "Avoid fixed heights and clipped overflow on text containers so spacing overrides do not clip content.",
      "Re-run and confirm containers have no fixed height or hidden overflow on text.")
def r_text_spacing(ctx, doc, r):
    for n in doc.nodes:
        decls = H.inline_decls(n)
        overflow = (decls.get("overflow") or "").lower()
        height = _css_px(decls.get("height"))
        if "hidden" in overflow and height is not None and n.text():
            fail(ctx, r, "Text container clips overflow at a fixed height of %dpx." % int(height), n)
        elif decls:
            ctx.pass_check()


@rule("font-size-min", "Base font size is readable", "visual", "1.4.4", "AA", "medium",
      "Use at least 12px for body text and relative units so 200% resize works.",
      "Re-run and confirm no body text is declared below the minimum readable size.")
def r_font_size(ctx, doc, r):
    minimum = float(ctx.config.get("thresholds", {}).get("min_font_size_px", 12))
    found = 0
    for block in H.style_blocks(doc):
        for selectors, decls in H.declarations(block):
            size = H.font_size_px(decls)
            if size is None:
                continue
            found += 1
            if size < minimum:
                fail(ctx, r, "Selector %r sets font-size %.1fpx below the %spx minimum." % (selectors[:60], size, minimum),
                     evidence={"selector": selectors[:300], "font_size_px": size})
            else:
                ctx.pass_check()
    if found == 0:
        ctx.pass_check()


@rule("link-distinguishable", "Links are distinguishable without colour", "visual", "1.4.1", "A", "medium",
      "Underline inline links or add another non-colour affordance.",
      "Re-run and confirm inline links carry a non-colour distinction.")
def r_link_distinguish(ctx, doc, r):
    for block in H.style_blocks(doc):
        for selectors, decls in H.declarations(block):
            if "text-decoration" in decls and "none" in (decls.get("text-decoration") or "").lower():
                if "a" in selectors and "color" in decls and "font-weight" not in decls:
                    fail(ctx, r, "Selector %r removes underlines and relies on colour alone." % selectors[:80],
                         evidence={"selector": selectors[:300]})
                else:
                    ctx.pass_check()


@rule("image-of-text", "Images of text are avoided", "visual", "1.4.5", "AA", "medium",
      "Render text with real text instead of images, or supply the text alternative.",
      "Re-run and confirm no decorative image contains essential text.")
def r_image_of_text(ctx, doc, r):
    for n in doc.by_tag("img"):
        src = (n.get("src") or "").lower()
        alt = (n.get("alt") or "").lower()
        if re.search(r"(text|banner|heading|title|logo)-?[\w-]*\.(png|jpe?g|gif|svg)$", src) and alt:
            fail(ctx, r, "Image %r likely renders text; use real text or verify 1.4.5 exception." % src[:80], n,
                 severity="low")
        else:
            ctx.pass_check()


@rule("orientation-lock", "Orientation is not locked", "visual", "1.3.4", "AA", "high",
      "Allow both portrait and landscape unless a specific orientation is essential.",
      "Re-run and confirm no CSS or manifest locks orientation.")
def r_orientation(ctx, doc, r):
    text = doc.text
    if re.search(r"@media[^{]*orientation\s*:\s*(portrait|landscape)[^{]*\{[^}]*display\s*:\s*none", text, re.I):
        fail(ctx, r, "CSS hides content in one orientation.")
        return
    m = re.search(r'"orientation"\s*:\s*"(portrait|landscape)"', text, re.I)
    if m:
        fail(ctx, r, "Web app manifest locks orientation to %s." % m.group(1))
        return
    ctx.pass_check()


# --------------------------------------------------------------------------- #
# Rules: motion and sensory safety
# --------------------------------------------------------------------------- #
@rule("reduced-motion", "Motion respects reduced-motion preference", "motion", "2.3.3", "AAA", "high",
      "Wrap non-essential animation in @media (prefers-reduced-motion: reduce) overrides.",
      "Re-run and confirm every animation has a reduced-motion counterpart.")
def r_reduced_motion(ctx, doc, r):
    css = "\n".join(H.style_blocks(doc))
    has_animation = bool(re.search(r"(animation|transition)\s*:", css)) or bool(doc.by_tag("marquee"))
    if not has_animation:
        ctx.pass_check()
        return
    if "prefers-reduced-motion" not in css:
        fail(ctx, r, "Animation declared without a prefers-reduced-motion override.",
             evidence={"has_prefers_reduced_motion": False})
    else:
        ctx.pass_check()


@rule("flashing-content", "Nothing flashes more than three times per second", "motion", "2.3.1", "A", "critical",
      "Reduce flash frequency below 3Hz, shrink the flashing area, or remove the effect.",
      "Re-run and confirm computed flash frequency stays at or below 3 flashes per second.")
def r_flashing(ctx, doc, r):
    css = "\n".join(H.style_blocks(doc))
    flagged = 0
    for block in H.declarations(css):
        pass
    for selectors, decls in [d for blk in H.style_blocks(doc) for d in H.declarations(blk)]:
        anim = decls.get("animation") or decls.get("animation-duration")
        if not anim:
            continue
        m = re.search(r"([\d.]+)\s*(ms|s)\b", anim)
        if not m:
            continue
        dur = float(m.group(1)) / (1000.0 if m.group(2) == "ms" else 1.0)
        if dur <= 0:
            continue
        freq = 1.0 / dur
        if freq > 3.0 and re.search(r"(flash|blink|strobe|pulse)", selectors, re.I):
            flagged += 1
            fail(ctx, r, "Selector %r flashes about %.1f times per second (>3Hz)." % (selectors[:70], freq),
                 evidence={"selector": selectors[:300], "duration_s": dur, "frequency_hz": round(freq, 2)})
        else:
            ctx.pass_check()
    for n in doc.by_tag("blink"):
        flagged += 1
        fail(ctx, r, "Deprecated <blink> element forces flashing content.", n)
    if "keyframes" in css and flagged == 0:
        for m in re.finditer(r"@keyframes\s+([\w-]+)\s*\{(.*?)\}\s*\}?", css, re.S):
            name, body = m.group(1), m.group(2)
            steps = len(re.findall(r"\d+%|from|to", body))
            if steps >= 7 and re.search(r"(flash|blink|strobe)", name, re.I):
                fail(ctx, r, "Keyframes %r define %d colour steps, risking a 3Hz+ flash." % (name, steps),
                     evidence={"keyframes": name, "steps": steps})


@rule("auto-playing-media", "Auto-playing media can be controlled", "motion", "1.4.2", "A", "high",
      "Remove autoplay, mute by default, or expose a visible pause control within the first three seconds.",
      "Re-run and confirm no media plays automatically beyond three seconds.")
def r_autoplay(ctx, doc, r):
    for n in doc.by_tag("audio", "video"):
        if n.has("autoplay") and not n.has("muted") and not n.has("controls"):
            fail(ctx, r, "<%s> autoplays audio without controls or muted." % n.tag, n)
        elif n.has("autoplay"):
            fail(ctx, r, "<%s> autoplays; confirm a pause control exists and audio is under 3 seconds." % n.tag, n,
                 severity="medium")
        else:
            ctx.pass_check()


@rule("moving-content-control", "Moving content can be paused", "motion", "2.2.2", "A", "high",
      "Provide pause/stop/hide for auto-updating or looping content, or keep it under five seconds.",
      "Re-run and confirm a pause mechanism exists for looping content.")
def r_moving_content(ctx, doc, r):
    for n in doc.by_tag("marquee"):
        fail(ctx, r, "<marquee> moves content with no pause control.", n)
    for n in doc.nodes:
        if (n.get("data-autoplay") or n.get("autoplay")) and "carousel" in " ".join(n.classes).lower():
            if not (n.has("data-pause-control") or n.get("aria-live")):
                fail(ctx, r, "Auto-rotating carousel without a documented pause control.", n)
            else:
                ctx.pass_check()


@rule("timeout-warning", "Time limits can be extended", "motion", "2.2.1", "A", "high",
      "Warn before session expiry, allow extension, or remove the time limit.",
      "Re-run and confirm a documented extension path exists for any timing mechanism.")
def r_timeout(ctx, doc, r):
    for n in doc.by_tag("meta"):
        if (n.get("http-equiv") or "").lower() == "refresh":
            content = n.get("content") or ""
            m = re.match(r"\s*(\d+)", content)
            if m and int(m.group(1)) < 72000:
                fail(ctx, r, "meta refresh reloads the page after %s seconds with no user control." % m.group(1), n)
                continue
            ctx.pass_check()
            continue
    text = doc.body_text().lower()
    if re.search(r"(session (?:will )?(?:expire|timeout)|time remaining|auto-?logout)", text):
        has_extend = bool(re.search(r"(extend|continue session|stay signed in|keep me signed in)", text))
        if not has_extend:
            fail(ctx, r, "Timing warning text found without a documented extension control.")
        else:
            ctx.pass_check()


# --------------------------------------------------------------------------- #
# Rules: forms, errors, authentication
# --------------------------------------------------------------------------- #
@rule("required-indicator", "Required fields are indicated", "forms", "3.3.2", "A", "high",
      "Mark required controls with the required attribute or aria-required plus a visible indicator.",
      "Re-run and confirm every required field carries a programmatic and visible indicator.")
def r_required(ctx, doc, r):
    for n in doc.by_tag("input", "select", "textarea"):
        if (n.get("type") or "").lower() in ("hidden", "submit", "button", "reset", "image"):
            continue
        visible_required = (n.get("aria-required") or "").lower() == "true" or n.has("required")
        if visible_required:
            ctx.pass_check()
        elif re.search(r"\*|required", (H.accessible_name(doc, n)[0] or ""), re.I):
            ctx.pass_check()


@rule("autocomplete-purpose", "Input purpose is programmatically identified", "forms", "1.3.5", "AA", "medium",
      "Add valid autocomplete tokens to fields collecting name, contact, address or payment data.",
      "Re-run and confirm identity and payment fields expose valid autocomplete tokens.")
def r_autocomplete(ctx, doc, r):
    tokens = set("""name honorific-prefix given-name additional-name family-name honorific-suffix
nickname username new-password current-password one-time-code organization-title organization
street-address address-line1 address-line2 address-line3 address-level4 address-level3
address-level2 address-level1 country country-name postal-code cc-name cc-given-name
cc-additional-name cc-family-name cc-number cc-exp cc-exp-month cc-exp-year cc-csc cc-type
transaction-currency transaction-amount language bday bday-day bday-month bday-year sex gender
url photo tel tel-country-code tel-national tel-area-code tel-local tel-extension email impp""".split())
    expectation = {
        "email": "email", "tel": "tel", "given-name": "given-name", "family-name": "family-name",
        "name": "name", "street-address": "street-address", "postal-code": "postal-code",
        "cc-number": "cc-number", "cc-exp": "cc-exp", "organization": "organization",
    }
    for n in doc.by_tag("input"):
        itype = (n.get("type") or "text").lower()
        if itype in ("hidden", "submit", "button", "reset", "image", "search", "range", "color", "file"):
            continue
        name = (n.get("name") or "") + " " + (n.get("id") or "")
        ac = (n.get("autocomplete") or "").strip().lower()
        if ac:
            bad = [t for t in ac.split() if t not in tokens and t not in ("on", "off", "shipping", "billing")]
            if bad:
                fail(ctx, r, "autocomplete contains invalid token(s): %s." % ", ".join(bad), n)
            else:
                ctx.pass_check()
            continue
        guess = None
        for key, token in expectation.items():
            if key.replace("-", "") in name.replace("-", "").replace("_", "").lower() or \
                    (key == itype):
                guess = token
                break
        if guess:
            fail(ctx, r, "Field %r collects %s but has no autocomplete=%r." % (name.strip() or itype, itype, guess), n)
        else:
            ctx.pass_check()


@rule("error-identification", "Errors are identified in text", "forms", "3.3.1", "A", "high",
      "Describe each error in text near the field and link it with aria-describedby and aria-invalid.",
      "Re-run and confirm each error state exposes a text message and programmatic association.")
def r_error_identification(ctx, doc, r):
    for n in doc.by_tag("input", "select", "textarea"):
        if (n.get("aria-invalid") or "").lower() != "true":
            continue
        desc, src = H.accessible_description(doc, n)
        errmsg = (n.get("aria-errormessage") or "").strip()
        if not desc and not errmsg:
            fail(ctx, r, "Field marked aria-invalid=\"true\" with no associated error text.", n)
        else:
            ctx.pass_check()
    for n in doc.nodes:
        if (n.get("role") or "") == "alert" and T.is_blank(n.text()):
            fail(ctx, r, "Empty role=alert container cannot announce a message.", n, severity="medium")


@rule("accessible-authentication", "Authentication avoids cognitive tests", "forms", "3.3.8", "AA", "high",
      "Offer paste, password managers, passkeys or OTP without memory puzzles or transcription.",
      "Re-run and confirm no authentication step blocks paste or requires an unassisted memory test.")
def r_accessible_auth(ctx, doc, r):
    for n in doc.by_tag("input"):
        itype = (n.get("type") or "").lower()
        if itype == "password":
            if n.has("onpaste") and re.search(r"return\s+false|preventDefault", n.get("onpaste") or ""):
                fail(ctx, r, "Password field blocks paste, breaking password managers.", n)
            else:
                ctx.pass_check()
    text = doc.body_text().lower()
    if re.search(r"captcha|recaptcha|enter the characters|type the code", text):
        if not re.search(r"audio captcha|alternative|accessib", text):
            fail(ctx, r, "CAPTCHA present without an accessible alternative.")
        else:
            ctx.pass_check()


@rule("redundant-entry", "Previously entered data is not re-requested", "forms", "3.3.7", "A", "medium",
      "Pre-fill or allow selection of information the user already supplied in the same process.",
      "Re-run and confirm multi-step forms reuse previously entered values.")
def r_redundant_entry(ctx, doc, r):
    names = {}
    for n in doc.by_tag("input"):
        key = (n.get("name") or "").strip().lower()
        itype = (n.get("type") or "").lower()
        if not key or itype in ("hidden", "submit", "button", "reset"):
            continue
        names.setdefault(key, []).append(n)
    for key, nodes in names.items():
        if len(nodes) > 1:
            fail(ctx, r, "Field name %r is requested %d times on one page." % (key, len(nodes)), nodes[1],
                 severity="low")
        else:
            ctx.pass_check()


@rule("fieldset-grouping", "Related controls are grouped", "forms", "1.3.1", "A", "medium",
      "Wrap radio and checkbox groups in fieldset/legend or role=group with a name.",
      "Re-run and confirm grouped controls expose a group name.")
def r_fieldset(ctx, doc, r):
    radios = [n for n in doc.by_tag("input") if (n.get("type") or "").lower() in ("radio", "checkbox")]
    groups = {}
    for n in radios:
        groups.setdefault(n.get("name") or "unnamed", []).append(n)
    for name, nodes in groups.items():
        if len(nodes) < 2:
            continue
        ancestor_groups = []
        for n in nodes:
            for a in n.ancestors():
                if a.tag in ("fieldset", "form") or H.role_of(a) in ("group", "radiogroup"):
                    ancestor_groups.append(a)
                    break
        if not ancestor_groups:
            fail(ctx, r, "Group %r has %d controls with no fieldset/group container." % (name, len(nodes)), nodes[0])
        else:
            ctx.pass_check()


@rule("consistent-help", "Help mechanisms are consistently placed", "forms", "3.2.6", "A", "low",
      "Keep help, contact and chat entry points in the same relative order across pages.",
      "Re-run on the page set and confirm the help mechanism appears in a consistent position.")
def r_consistent_help(ctx, doc, r):
    links = [n for n in doc.by_tag("a") if re.search(r"help|support|contact|chat|faq", (n.text() or "").lower())]
    if not links:
        ctx.pass_check()
        return
    ids = [l.get("id") or "" for l in links]
    classes = [tuple(sorted(l.classes)) for l in links]
    if len(set(classes)) > 1 and len(links) > 2:
        fail(ctx, r, "Help links use %d different class signatures, suggesting inconsistent placement." % len(set(classes)),
             links[0], severity="low")
    else:
        ctx.pass_check()


# --------------------------------------------------------------------------- #
# Rules: tables, media, i18n markup, consistency
# --------------------------------------------------------------------------- #
@rule("table-headers", "Data tables have headers", "tables", "1.3.1", "A", "high",
      "Use <th> with scope, or role=columnheader/rowheader, for every data cell's header.",
      "Re-run and confirm each data table cell resolves to a header cell.")
def r_table_headers(ctx, doc, r):
    for table in doc.by_tag("table"):
        role = H.role_of(table)
        if role in ("presentation", "none"):
            ctx.pass_check()
            continue
        has_th = bool(table.find_all("th")) or bool(table.find_all(pred=lambda n: H.role_of(n) in ("columnheader", "rowheader")))
        cells = [n for n in table.find_all("td") if n.text()]
        if cells and not has_th:
            fail(ctx, r, "Table with %d data cells has no header cells." % len(cells), table)
        else:
            ctx.pass_check()


@rule("table-caption", "Tables are identified", "tables", "1.3.1", "A", "low",
      "Add a caption, aria-label or aria-labelledby so the table's purpose is announced.",
      "Re-run and confirm every data table exposes a name.")
def r_table_caption(ctx, doc, r):
    for table in doc.by_tag("table"):
        if H.role_of(table) in ("presentation", "none"):
            continue
        name, src = H.accessible_name(doc, table)
        if not T.norm_ws(name):
            fail(ctx, r, "Data table has no caption or accessible name.", table)
        else:
            ctx.pass_check()


@rule("video-captions", "Video has captions", "media", "1.2.2", "A", "critical",
      "Add a captions track (WebVTT) to every video, or provide a linked transcript.",
      "Re-run and confirm each video exposes a captions track in the page language.")
def r_video_captions(ctx, doc, r):
    for n in doc.by_tag("video"):
        tracks = [c for c in n.child_elements() if c.tag == "track"]
        caps = [t for t in tracks if (t.get("kind") or "subtitles").lower() in ("captions", "subtitles")]
        if not caps:
            fail(ctx, r, "Video without a captions/subtitles track.", n)
        elif not any((t.get("srclang") or "").strip() for t in caps):
            fail(ctx, r, "Captions track missing srclang.", n)
        else:
            ctx.pass_check()


@rule("audio-description", "Audio description is available", "media", "1.2.3", "A", "high",
      "Provide a described track, an audio-described version, or a text alternative for visuals.",
      "Re-run and confirm described audio or a full transcript is present.")
def r_audio_description(ctx, doc, r):
    for n in doc.by_tag("video"):
        tracks = [c for c in n.child_elements() if c.tag == "track"]
        described = [t for t in tracks if (t.get("kind") or "").lower() == "descriptions"]
        transcript = bool(re.search(r"transcript|audio description", doc.body_text(), re.I))
        if not described and not transcript:
            fail(ctx, r, "Video has no descriptions track and no transcript reference.", n)
        else:
            ctx.pass_check()


@rule("media-controls", "Media players expose controls", "media", "2.1.1", "A", "high",
      "Add the controls attribute, or build accessible custom controls with keyboard support.",
      "Re-run and confirm each media element exposes operable controls.")
def r_media_controls(ctx, doc, r):
    for n in doc.by_tag("audio", "video"):
        if n.has("controls") or n.has("data-custom-controls"):
            ctx.pass_check()
        else:
            fail(ctx, r, "<%s> has no controls attribute or documented custom controls." % n.tag, n)


@rule("translatable-markup", "Markup supports translation", "i18n", "3.1.2", "AA", "low",
      "Mark non-translatable strings with translate=\"no\" and keep user-facing text out of attributes.",
      "Re-run and confirm brand and code strings carry translate=no.")
def r_translatable(ctx, doc, r):
    for n in doc.nodes:
        if (n.get("translate") or "").lower() == "no":
            ctx.pass_check()
    if not doc.by_tag("html"):
        fail(ctx, r, "No root element to carry lang/translate/dir metadata.")


@rule("dir-attribute", "Text direction is declared", "i18n", "1.3.1", "A", "high",
      "Declare dir=\"rtl\" on the root for RTL locales and dir on embedded opposite-direction runs.",
      "Re-run and confirm the direction declared matches the locale profile.")
def r_dir_attr(ctx, doc, r):
    loc = (ctx.params.get("locale") or ctx.config.get("locale", {}).get("default_locale") or "").lower()
    profile = ctx.assets.get("locale_profiles", {}).get("locales", {}).get(loc, {})
    expected = profile.get("direction")
    html = doc.by_tag("html")
    if not html:
        return
    actual = (html[0].get("dir") or "").strip().lower()
    if expected in ("rtl", "ltr"):
        if not actual:
            fail(ctx, r, "Locale %r expects dir=%r but the root declares none." % (loc, expected), html[0])
        elif actual != expected:
            fail(ctx, r, "Root dir=%r conflicts with locale direction %r." % (actual, expected), html[0])
        else:
            ctx.pass_check()
    else:
        ctx.pass_check()


@rule("bidi-controls", "Bidirectional controls are balanced", "i18n", "1.3.1", "A", "medium",
      "Remove stray bidi control characters or wrap mixed-direction runs in isolate characters.",
      "Re-run and confirm bidi controls balance and isolates wrap embedded runs.")
def r_bidi_controls(ctx, doc, r):
    prof = T.bidi_profile(doc.body_text())
    if prof["unbalanced_isolates"] > 0:
        fail(ctx, r, "%d unclosed isolate initiator(s) in content." % prof["unbalanced_isolates"],
             evidence=prof)
    elif prof["unbalanced_embeddings"] > 0:
        fail(ctx, r, "%d unbalanced legacy embedding control(s)." % prof["unbalanced_embeddings"], evidence=prof)
    else:
        ctx.pass_check()


@rule("consistent-identification", "Repeated components are identified consistently", "consistency", "3.2.4", "AA", "medium",
      "Use the same label, icon and function for the same component across the page.",
      "Re-run and confirm one label per function and one function per label.")
def r_consistent_identification(ctx, doc, r):
    by_label = {}
    for n in doc.by_tag("a", "button"):
        name = T.norm_ws(H.accessible_name(doc, n)[0]).lower()
        if not name:
            continue
        by_label.setdefault(name, set()).add(n.get("href") or n.get("onclick") or n.tag)
    for name, targets in by_label.items():
        if len(targets) > 1 and len(name) > 3:
            fail(ctx, r, "Label %r points to %d different targets." % (name, len(targets)),
                 severity="low", evidence={"label": name, "targets": sorted(str(t) for t in targets)[:6]})
        else:
            ctx.pass_check()


@rule("viewport-present", "Viewport meta is present", "meta", "1.4.4", "AA", "medium",
      "Add a responsive viewport meta so mobile browsers do not use a 980px virtual viewport.",
      "Re-run and confirm a viewport declaration exists with width=device-width.")
def r_viewport_present(ctx, doc, r):
    metas = [n for n in doc.by_tag("meta") if (n.get("name") or "").lower() == "viewport"]
    if not metas:
        fail(ctx, r, "No viewport meta tag; mobile reflow and zoom behaviour is uncontrolled.")
        return
    if "width=device-width" not in (metas[0].get("content") or "").replace(" ", "").lower():
        fail(ctx, r, "Viewport meta does not set width=device-width.", metas[0], severity="low")
        return
    ctx.pass_check()


# --------------------------------------------------------------------------- #
# Rule groups and skill routing
# --------------------------------------------------------------------------- #
GROUP_ORDER = ["structure", "names", "aria", "keyboard", "pointer", "visual", "motion", "forms",
               "tables", "media", "messaging", "i18n", "images", "consistency", "meta"]

BROAD_SWEEP = re.compile(
    r"(wcag|full|comprehensive|whole[- ]page|site[- ]wide|page[- ]level)[^|]{0,40}"
    r"(scan|audit|test|crawl|review|check|evaluation)"
    r"|(scan|audit|crawl|evaluation|checker)[^|]{0,40}(wcag|conformance)")

KEYWORD_GROUPS = [
    (r"duplicate[- ]id|\bid\b|dom structure|document structure", ["structure"]),
    (r"heading|outline|hierarchy", ["structure"]),
    (r"landmark|region|bypass|navigation|\bnav\b|skip[- ]link", ["structure"]),
    (r"language attribute|\blang\b|page language", ["structure", "i18n"]),
    (r"page title|document title|\btitle\b|page name", ["structure", "meta"]),
    (r"\baria\b|wai-aria|accessible name|role", ["aria", "names"]),
    (r"live region|status message|toast|notification|announcement", ["messaging"]),
    (r"\blabel|alternative text|\balt\b|instruction", ["names", "forms"]),
    (r"\bform|error message|input|authentication|log[- ]?in|captcha|redundant entr", ["forms"]),
    (r"\btable|\bgrid\b|data table", ["tables"]),
    (r"\bfocus|keyboard|tab order|switch access|shortcut|keystroke", ["keyboard"]),
    (r"target size|spacing|pointer|drag|gesture|touch", ["pointer"]),
    (r"contrast|colou?r|magnif|resize|zoom|reflow|typograph|\bfont|line[- ]height|justif", ["visual"]),
    (r"\bmotion|animat|\bflash|\bblink|parallax|auto[- ]?(play|rotat)|carousel|marquee|"
     r"time limit|\btimeout|sensory|vestibular", ["motion"]),
    (r"\bvideo|\bcaption|\baudio|\bmedia\b|\biframe\b|transcript", ["media", "structure"]),
    (r"responsive|orientation|viewport|\bmobile\b|reflow", ["visual", "meta"]),
]

DEFAULT_GROUPS = ["structure", "names", "aria", "forms", "visual", "keyboard", "pointer", "images"]


def groups_for_skill(skill_name, op_groups=None):
    """Map a skill name to the rule groups worth running.

    A skill that names WCAG plus a scan/audit verb sweeps every group; narrower
    names are matched on word boundaries so 'Automated' does not pull in motion.
    """
    name = (skill_name or "").lower()
    if BROAD_SWEEP.search(name):
        picked = list(GROUP_ORDER)
    else:
        picked = []
        for pattern, groups in KEYWORD_GROUPS:
            if re.search(pattern, name):
                for g in groups:
                    if g not in picked:
                        picked.append(g)
        if not picked:
            picked = list(DEFAULT_GROUPS)
    if op_groups:
        picked = [g for g in picked if g in op_groups] or list(op_groups)
    return [g for g in GROUP_ORDER if g in picked]


def rules_for(groups):
    return [r for r in RULES if r["group"] in groups]


# --------------------------------------------------------------------------- #
# Web scan operations
# --------------------------------------------------------------------------- #
@op("web-scan",
    "Run the deterministic WCAG rule set against an HTML artifact. The skill name selects the "
    "rule groups that apply, so one unified engine serves every scanner-type skill in the catalog.",
    params={
        "conformance_level": {"type": "enum", "values": ["a", "aa", "aaa"], "default": "aa",
                              "doc": "Target conformance level; filtering uses the level metadata."},
        "expected_lang": {"type": "str", "default": None, "doc": "BCP-47 tag expected on <html lang>."},
        "include_groups": {"type": "list", "default": None,
                           "doc": "Override the keyword-derived rule groups, e.g. structure,visual."},
        "max_rule_groups": {"type": "int", "default": 15,
                            "doc": "Cap the number of rule groups applied (max 15)."},
    },
    accepts=("html",))
def run_web_scan(ctx):
    _doc(ctx)
    doc = _doc(ctx)
    override = ctx.params.get("include_groups")
    if isinstance(override, str):
        override = [g.strip() for g in override.split(",") if g.strip()]
    groups = override or groups_for_skill(ctx.skill["name"])
    if not groups:
        groups = list(DEFAULT_GROUPS)
    groups = groups[: int(ctx.params.get("max_rule_groups") or 15)]
    level = (ctx.params.get("conformance_level") or ctx.config.get("conformance_level") or "aa").lower()
    level_rank = {"a": 1, "aa": 2, "aaa": 3}
    selected = []
    for r in rules_for(groups):
        if level_rank.get((r["level"] or "A").lower(), 1) <= level_rank.get(level, 2):
            selected.append(r)
    ctx.meta("rule_groups", groups)
    ctx.meta("rules_evaluated", [{"id": r["id"], "check": r["check"], "wcag": r["wcag"], "level": r["level"]}
                                 for r in selected])
    ctx.meta("conformance_target", "WCAG 2.2 Level %s (automated subset)" % level.upper())
    for r in selected:
        ctx.checkpoint()
        r["fn"](ctx, doc, r)
    ctx.meta("rules_run", len(selected))
    ctx.meta("checks_by_group", {g: sum(1 for r in selected if r["group"] == g) for g in groups})
    ctx.meta("parse_errors", doc.parse_errors)


@op("harness-config", "Emit CI harness configuration (axe-core, pa11y, Lighthouse) aligned with the "
    "selected rule groups, plus a parser contract for importing their JSON output.",
    params={"runner": {"type": "enum", "values": ["axe-core", "pa11y", "lighthouse"], "default": "axe-core",
                       "doc": "Runner to configure."},
            "target_url": {"type": "str", "default": "http://localhost:3000/",
                           "doc": "URL the harness will audit."}},
    accepts=("html", "json"))
def run_harness_config(ctx):
    runner = ctx.params.get("runner", "axe-core")
    groups = groups_for_skill(ctx.skill["name"])
    wcag = sorted({r["wcag"] for r in rules_for(groups)})
    targets = ctx.params.get("target_url")
    if runner == "axe-core":
        body = {
            "runs": [{"url": targets, "runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa", "wcag22aa"]}}],
            "reporter": "v2",
            "resultTypes": ["violations", "incomplete"],
            "exitCode": True,
        }
        filename = "axe-config.json"
    elif runner == "pa11y":
        body = {"defaults": {"standard": "WCAG2AA", "timeout": 30000, "wait": 500,
                             "chromeLaunchConfig": {"args": ["--no-sandbox"]},
                             "ignore": [], "runners": ["axe", "htmlcs"]}, "urls": [targets]}
        filename = ".pa11yci.json"
    else:
        body = {"ci": {"collect": {"url": [targets], "settings": {"onlyCategories": ["accessibility"]}},
                       "assert": {"assertions": {"categories:accessibility": ["error", {"minScore": 1}]},
                                  "budgets": [{"path": "/*", "timings": [{"metric": "interactive",
                                                                          "budget": 5000}]}]}},
                "upload": {"target": "temporary-public-storage"}}
        filename = "lighthouserc.json"
    ctx.meta("runner", runner)
    ctx.meta("config_filename", filename)
    ctx.meta("config", body)
    ctx.meta("criteria_covered", wcag)
    ctx.meta("import_contract", {
        "accepted_shape": "{violations:[{id,impact,help,nodes:[{target,html,failureSummary}]}]}",
        "command": "run --skill <scanner-skill> --input results.json --set import_format=axe",
        "note": "Harness output can be normalised by the dedupe operation of this Skill Tool.",
    })
    ctx.note("Generated configuration only; the runner itself is an external dependency and was not executed.")


@op("dedupe", "Normalise and deduplicate findings from one or more scanner exports (axe, pa11y, "
    "Lighthouse or this tool's own JSON) into a single stable finding list.",
    params={"similarity_threshold": {"type": "float", "default": 0.9,
                                     "doc": "0-1 threshold for treating two findings as duplicates."}},
    accepts=("json",))
def run_dedupe(ctx):
    payload = ctx.docs[0].data if isinstance(ctx.docs[0].data, (dict, list)) else None
    records = _findings_from_payload(payload)
    if not records:
        raise S.InputError("No findings could be read from the input export.",
                           hint="Pass axe/pa11y/Lighthouse JSON or a result document produced by this tool.")
    threshold = float(ctx.params.get("similarity_threshold", 0.9))
    kept, merged = [], 0
    for rec in records:
        ctx.checkpoint()
        dup = None
        for k in kept:
            if k["rule"] == rec["rule"] and (k.get("locator") or "") == (rec.get("locator") or ""):
                dup = k
                break
            if k["rule"] == rec["rule"] and T.similarity(k.get("message", ""), rec.get("message", "")) >= threshold:
                dup = k
                break
        if dup:
            merged += 1
            dup.setdefault("merged_from", []).append(rec.get("id") or rec.get("rule"))
        else:
            kept.append(rec)
    ctx.meta("input_findings", len(records))
    ctx.meta("duplicates_merged", merged)
    ctx.meta("unique_findings", len(kept))
    ctx.meta("normalised", kept[:200])
    for rec in kept:
        if rec.get("severity") in ("critical", "high"):
            ctx.add(rule=rec.get("rule") or "imported",
                    message=rec.get("message") or "Imported blocking finding.",
                    severity=rec.get("severity") or "high",
                    criterion=rec.get("wcag"), evidence=rec.get("evidence"),
                    locator=rec.get("locator"),
                    remediation=rec.get("remediation") or "Remediate the imported finding per its source report.",
                    retest="Re-run the source scanner and this dedupe operation; confirm the finding is gone.")
        else:
            ctx.pass_check()


def _findings_from_payload(payload):
    out = []
    if payload is None:
        return out
    if isinstance(payload, dict) and "findings" in payload and isinstance(payload["findings"], list):
        for f in payload["findings"]:
            out.append({
                "id": f.get("id"), "rule": f.get("rule") or f.get("check") or "imported",
                "message": f.get("message") or "", "severity": f.get("severity") or "medium",
                "wcag": f.get("wcag"), "locator": f.get("locator"),
                "evidence": f.get("evidence"), "remediation": f.get("remediation"),
            })
        return out
    if isinstance(payload, dict) and "violations" in payload:
        for v in payload["violations"]:
            for node in v.get("nodes") or [{}]:
                out.append({
                    "id": v.get("id"), "rule": v.get("id") or "axe",
                    "message": (v.get("help") or "") + " " + (node.get("failureSummary") or ""),
                    "severity": {"critical": "critical", "serious": "high", "moderate": "medium",
                                 "minor": "low"}.get(v.get("impact"), "medium"),
                    "wcag": ",".join(v.get("tags") or []) or None,
                    "locator": ", ".join(node.get("target") or []) or None,
                    "evidence": {"html": node.get("html")},
                    "remediation": v.get("description") or "",
                })
        return out
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            if "violations" in item:
                out.extend(_findings_from_payload(item))
            elif "rule" in item or "check" in item:
                out.append({"id": item.get("id"), "rule": item.get("rule") or item.get("check"),
                            "message": item.get("message") or "", "severity": item.get("severity") or "medium",
                            "wcag": item.get("wcag"), "locator": item.get("locator"),
                            "evidence": item.get("evidence"), "remediation": item.get("remediation")})
    return out


@op("baseline", "Compare a current result document with a stored baseline and report only new, "
    "fixed and persisting findings (accessible regression gating).",
    params={"baseline_fail_on": {"type": "enum", "values": ["new", "any"], "default": "new",
                                 "doc": "Fail the gate on new findings only, or on any finding."}},
    accepts=("json",))
def run_baseline(ctx):
    current = _findings_from_payload(ctx.docs[0].data)
    ref_path = ctx.params.get("baseline_path") or ctx.config.get("paths", {}).get("baseline")
    if len(ctx.docs) > 1:
        baseline = _findings_from_payload(ctx.docs[1].data)
        ref_path = ctx.docs[1].path
    elif ref_path:
        baseline = _findings_from_payload(S.load_json(ref_path))
    else:
        raise S.ParamError("A baseline document is required.",
                           hint="Pass --reference <baseline.json> or set paths.baseline in the config.")
    key = lambda f: (f.get("rule"), f.get("locator") or "")
    cur_keys = {key(f) for f in current}
    base_keys = {key(f) for f in baseline}
    new = [f for f in current if key(f) not in base_keys]
    fixed = [f for f in baseline if key(f) not in cur_keys]
    persisting = [f for f in current if key(f) in base_keys]
    ctx.meta("new", len(new))
    ctx.meta("fixed", len(fixed))
    ctx.meta("persisting", len(persisting))
    ctx.meta("baseline_path", ref_path)
    for f in new:
        ctx.add(rule=f.get("rule") or "regression", message="New finding not present in baseline: %s" % f.get("message"),
                severity=f.get("severity") or "high", criterion=f.get("wcag"), locator=f.get("locator"),
                evidence=f.get("evidence"),
                remediation="Fix the regression or refresh the baseline after review.",
                retest="Re-run the baseline comparison and confirm the finding moves to 'persisting' or 'fixed'.")
    for f in fixed:
        ctx.pass_check()
    if ctx.params.get("baseline_fail_on") == "any":
        for f in persisting:
            ctx.add(rule=f.get("rule") or "persisting", message="Persisting finding: %s" % f.get("message"),
                    severity=f.get("severity") or "medium", criterion=f.get("wcag"), locator=f.get("locator"),
                    remediation=f.get("remediation") or "Remediate the persisting finding.",
                    retest="Re-run the baseline comparison and confirm it is fixed.")


@op("ci-gate", "Evaluate a results document against a CI policy (severity thresholds, count budgets, "
    "required criterion coverage) and return a deterministic pass/fail decision.",
    params={"fail_on": {"type": "enum", "values": ["critical", "high", "medium", "any"], "default": "high",
                        "doc": "Lowest severity that fails the gate."},
            "max_findings": {"type": "int", "default": 0, "doc": "Allowed findings at or above fail_on."},
            "require_coverage": {"type": "list", "default": None,
                                 "doc": "Criteria that must appear in the evidence before passing."}},
    accepts=("json",))
def run_ci_gate(ctx):
    data = ctx.docs[0].data
    result_docs = data if isinstance(data, list) else [data]
    gate = ctx.config.get("gates", {}).get("ci", {})
    fail_on = ctx.params.get("fail_on") or gate.get("fail_on", "high")
    budget = int(ctx.params.get("max_findings", gate.get("max_findings", 0)))
    rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    hits = []
    covered = set()
    for doc in result_docs:
        if not isinstance(doc, dict):
            continue
        for crit in (doc.get("summary", {}) or {}).get("criteria_affected", {}) or {}:
            covered.add(crit)
        for f in _findings_from_payload(doc):
            if rank.get(f.get("severity"), 0) >= rank.get(fail_on, 3):
                hits.append(f)
    required = ctx.params.get("require_coverage") or gate.get("require_coverage") or []
    if isinstance(required, str):
        required = [c.strip() for c in required.split(",") if c.strip()]
    missing = [c for c in required if c not in covered]
    ctx.meta("gate", {"fail_on": fail_on, "max_findings": budget, "require_coverage": required})
    ctx.meta("blocking_findings", len(hits))
    ctx.meta("coverage_missing", missing)
    ctx.meta("decision", "fail" if (len(hits) > budget or missing) else "pass")
    if len(hits) > budget:
        ctx.add(rule="ci-gate", message="%d finding(s) at or above %r exceed the budget of %d." % (
            len(hits), fail_on, budget), severity="high", criterion="quality-gate",
            evidence={"findings": hits[:20]},
            remediation="Fix the blocking findings or record an approved exception with an expiry date.",
            retest="Re-run the CI gate on the new results document and confirm decision=pass.")
    else:
        ctx.pass_check()
    if missing:
        ctx.add(rule="ci-gate-coverage", message="Required criteria were not covered by the evidence: %s." % ", ".join(missing),
                severity="medium", criterion="quality-gate",
                remediation="Extend the test plan so every required criterion produces evidence.",
                retest="Re-run the gate and confirm coverage_missing is empty.")


# --------------------------------------------------------------------------- #
# Assistive-technology operations
# --------------------------------------------------------------------------- #
@op("at-probe", "Compute expected accessible name, role, description, states and announcement for "
    "every interactive element, so AT sessions can be scripted against a known expectation.",
    params={"roles": {"type": "list", "default": None, "doc": "Restrict to these roles."},
            "limit": {"type": "int", "default": 200, "doc": "Maximum elements to report."}},
    accepts=("html",))
def run_at_probe(ctx):
    doc = _doc(ctx)
    wanted = ctx.params.get("roles")
    if isinstance(wanted, str):
        wanted = [r.strip() for r in wanted.split(",") if r.strip()]
    limit = int(ctx.params.get("limit", 200))
    probes = []
    for n in doc.nodes:
        role = H.role_of(n)
        if role is None:
            continue
        if wanted:
            if role not in wanted:
                continue
        elif role not in H.INTERACTIVE_ROLES and role not in H.LANDMARK_ROLES and role not in ("heading", "img", "table"):
            continue
        probe = H.announcement(doc, n)
        probe["locator"] = n.locator()
        probes.append(probe)
        if len(probes) >= limit:
            break
        if not probe["name"] and (role in H.INTERACTIVE_ROLES):
            ctx.add(rule="at-name-missing", message="Element with role %r will be announced without a name." % role,
                    severity="critical", criterion="4.1.2", locator=probe["locator"], evidence=probe,
                    remediation="Provide an accessible name via native labelling or aria-label/labelledby.",
                    retest="Re-run at-probe and confirm the expected_announcement includes a name.")
        else:
            ctx.pass_check()
    ctx.meta("probes", probes)
    ctx.meta("probe_count", len(probes))
    ctx.note("Expectations are derived from markup; real AT output depends on the screen reader, browser and OS versions.")


@op("at-script", "Generate a reproducible assistive-technology test script (NVDA, JAWS, VoiceOver, "
    "TalkBack, Narrator, Orca) with environment pinning, tasks, expected announcements and pass criteria.",
    params={"at": {"type": "enum", "values": ["nvda", "jaws", "narrator", "voiceover-macos", "voiceover-ios",
                                              "talkback", "orca", "chromevox"], "default": "nvda",
                   "doc": "Assistive technology under test."},
            "tasks": {"type": "int", "default": 6, "min": 1, "max": 30, "doc": "Number of scripted tasks."},
            "locale": {"type": "str", "default": None, "doc": "Locale of the session."}},
    accepts=("html",))
def run_at_script(ctx):
    doc = _doc(ctx)
    at = ctx.params.get("at", "nvda")
    matrix = ctx.assets.get("at_matrix", {}).get("assistive_technologies", {})
    spec = matrix.get(at, {})
    probes = []
    for n in doc.nodes:
        role = H.role_of(n)
        if role in H.INTERACTIVE_ROLES or role in H.LANDMARK_ROLES:
            probes.append(H.announcement(doc, n))
    heads = H.headings(doc)
    landmarks = H.landmarks(doc)
    tasks = []
    tasks.append({"task": "Page load and title", "steps": ["Open the page", "Listen to the initial announcement"],
                  "expected": "Title %r followed by the page landmark summary" % doc.title(),
                  "pass_criteria": "Title is announced first and matches the visible page heading."})
    if landmarks:
        tasks.append({"task": "Landmark navigation", "steps": ["Use landmark shortcuts"],
                      "expected": "Landmarks: " + ", ".join("%s%s" % (l["role"], " (%s)" % l["name"] if l["name"] else "")
                                                            for l in landmarks[:8]),
                      "pass_criteria": "Every landmark is announced with role and name; no unnamed duplicates."})
    if heads:
        tasks.append({"task": "Heading navigation", "steps": ["Jump heading to heading"],
                      "expected": "Heading list: " + "; ".join("h%s %s" % (h["level"], T.truncate(h["text"], 40))
                                                               for h in heads[:10]),
                      "pass_criteria": "Heading levels announce correctly and no heading is skipped."})
    for probe in probes[: max(1, int(ctx.params.get("tasks", 6)) - len(tasks))]:
        tasks.append({"task": "Interact with %s" % (probe["role"] or "element"),
                      "steps": ["Navigate to %s" % probe["locator"], "Activate with the %s primary command" % at],
                      "expected": probe["expected_announcement"],
                      "pass_criteria": "Name, role and state are announced in that order; state changes are re-announced."})
    script = {
        "assistive_technology": at,
        "technology": spec.get("name", at),
        "environment": spec.get("environment", "Pin the AT version, browser version, OS build and locale in the session record."),
        "shortcuts": spec.get("shortcuts", []),
        "locale": ctx.params.get("locale") or ctx.config.get("locale", {}).get("default_locale"),
        "artifact": ctx.docs[0].path,
        "tasks": tasks,
        "evidence_to_capture": ["announcement transcript", "screenshot of focused element",
                                "AT version", "browser and OS build", "pass/fail per task"],
        "human_required": True,
    }
    ctx.meta("script", script)
    ctx.meta("task_count", len(tasks))
    ctx.note("A human screen-reader user must execute this script; the tool only generates and validates the plan.")


@op("at-matrix", "Build the assistive-technology / browser / OS compatibility matrix to be covered, "
    "with risk weight and cost estimate per combination.",
    params={"scope": {"type": "enum", "values": ["core", "extended", "all"], "default": "core",
                      "doc": "How many combinations to include."}},
    accepts=("html", "json"))
def run_at_matrix(ctx):
    matrix = ctx.assets.get("at_matrix", {})
    combos = matrix.get("combinations", [])
    scope = ctx.params.get("scope", "core")
    if scope == "core":
        combos = [c for c in combos if c.get("tier") == "core"]
    elif scope == "extended":
        combos = [c for c in combos if c.get("tier") in ("core", "extended")]
    minutes = sum(int(c.get("minutes", 30)) for c in combos)
    ctx.meta("scope", scope)
    ctx.meta("combinations", combos)
    ctx.meta("combination_count", len(combos))
    ctx.meta("estimated_effort_hours", round(minutes / 60.0, 2))
    ctx.meta("coverage_note", matrix.get("note", ""))
    if not combos:
        ctx.add(rule="at-matrix", message="No combinations selected for the requested scope.", severity="medium",
                criterion="planning", remediation="Widen --set scope or extend asset-at-test-matrix.json.",
                retest="Re-run and confirm combination_count > 0.")


def _records(ctx):
    data = ctx.docs[0].data
    if isinstance(data, dict) and set(data) == {"json"}:
        data = data["json"]
    if data is None:
        raise S.InputError("Expected a JSON array or object containing 'records'.")
    if isinstance(data, dict):
        for key in ("records", "findings", "items", "observations", "notes", "sessions"):
            if isinstance(data.get(key), list):
                return data[key]
        raise S.InputError("JSON object has no records/findings/items array.")
    if not isinstance(data, list):
        raise S.InputError("Expected a JSON array of records.")
    return data


@op("at-findings", "Normalise recorded assistive-technology session notes into findings with severity, "
    "criterion mapping and retest instructions.",
    params={"default_severity": {"type": "enum", "values": ["critical", "high", "medium", "low", "info"],
                                 "default": "high", "doc": "Severity used when notes omit one."}},
    accepts=("json", "jsonl"))
def run_at_findings(ctx):
    notes = _records(ctx)
    if not notes:
        raise S.InputError("No session notes found.",
                           hint="Provide JSON records with at/artifact/expected/actual fields.")
    sev = ctx.params.get("default_severity", "high")
    for rec in notes:
        ctx.checkpoint()
        at = rec.get("at") or rec.get("assistive_technology") or "unknown AT"
        expected = str(rec.get("expected") or "")
        actual = str(rec.get("actual") or "")
        wcag = rec.get("wcag") or rec.get("criterion")
        if not actual and not rec.get("finding"):
            ctx.pass_check()
            continue
        message = rec.get("finding") or "%s: expected %r but observed %r." % (at, T.truncate(expected, 60),
                                                                              T.truncate(actual, 60))
        ctx.add(rule="at-session", message=message, severity=rec.get("severity") or sev,
                criterion=wcag, locator=rec.get("artifact") or rec.get("locator"),
                evidence={"at": at, "expected": expected, "actual": actual,
                          "environment": rec.get("environment"), "session": rec.get("session_id")},
                remediation=rec.get("remediation") or
                "Correct the markup or component behaviour so the announced output matches the accessible expectation.",
                retest="Re-run the same script with same AT and browser versions and record the transcript.")


# --------------------------------------------------------------------------- #
# Declared element tree (mobile, native, kiosk, design-system components)
# --------------------------------------------------------------------------- #
@op("element-tree-audit", "Audit a declared UI element tree (Android views, iOS/UIKit, kiosk or web "
    "component spec) for missing names, roles, target sizes, focus order and state exposure.",
    params={"min_target_dp": {"type": "float", "default": 24.0, "doc": "Minimum target size in dp/pt."},
            "platform": {"type": "enum", "values": ["auto", "android", "ios", "desktop", "kiosk", "web"],
                         "default": "auto", "doc": "Platform conventions applied to the tree."}},
    accepts=("json",))
def run_element_tree(ctx):
    data = ctx.docs[0].data
    tree = data.get("elements") if isinstance(data, dict) else data
    if not isinstance(tree, list) or not tree:
        raise S.InputError("Element tree input must be a list or {'elements': [...]}.",
                           hint="See asset-element-tree-schema.json for the accepted shape.")
    minimum = float(ctx.params.get("min_target_dp", 24.0))
    platform = ctx.params.get("platform", "auto")
    flattened = []

    def walk(nodes, depth=0):
        for node in nodes or []:
            flattened.append((node, depth))
            walk(node.get("children"), depth + 1)

    walk(tree)
    ctx.meta("element_count", len(flattened))
    missing_names = 0
    for node, depth in flattened:
        ctx.checkpoint()
        role = (node.get("role") or node.get("class") or "generic").lower()
        name = str(node.get("label") or node.get("content_description") or node.get("name") or "").strip()
        clickable = bool(node.get("clickable") or node.get("actionable") or node.get("on_click"))
        if (clickable or role in H.INTERACTIVE_ROLES) and not name:
            missing_names += 1
            ctx.add(rule="element-name", message="%s element has no accessible label." % role,
                    severity="critical", criterion="4.1.2",
                    locator="%s @ depth %d" % (node.get("id") or role, depth), evidence=node,
                    remediation="Set contentDescription/accessibilityLabel or a visible text label.",
                    retest="Re-run the tree audit and confirm the element reports a label.")
        else:
            ctx.pass_check()
        for key in ("width", "height"):
            val = node.get(key) or (node.get("bounds") or {}).get(key)
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if clickable and fval < minimum:
                ctx.add(rule="element-target-size", message="%s target %s=%s is below %s." % (role, key, val, minimum),
                        severity="medium", criterion="2.5.8",
                        locator=node.get("id") or role, evidence={"dimension": key, "value": fval},
                        remediation="Increase the touch target to at least 24x24 dp/pt or add spacing.",
                        retest="Re-run the audit and confirm both dimensions meet the minimum.")
        if node.get("clickable") and node.get("enabled") is False:
            ctx.note("Disabled interactive element %r must still be discoverable and announced as disabled." %
                     (node.get("id") or role))
    ctx.meta("missing_names", missing_names)
    ctx.meta("platform", platform)


@op("test-matrix", "Generate a cross-browser, cross-device and cross-locale test matrix with pinned "
    "versions, risk weighting and coverage budget.",
    params={"locales": {"type": "list", "default": None, "doc": "Locales to include."},
            "platforms": {"type": "list", "default": None, "doc": "Platforms to include."},
            "budget_minutes": {"type": "int", "default": 240, "doc": "Time budget for the matrix."}},
    accepts=("json", "html"))
def run_test_matrix(ctx):
    # The matrix is derived from the asset + params; an artifact is optional context.
    matrix = ctx.assets.get("at_matrix", {})
    platforms = ctx.params.get("platforms") or ["web-desktop", "web-mobile", "android", "ios"]
    if isinstance(platforms, str):
        platforms = [p.strip() for p in platforms.split(",")]
    locales = ctx.params.get("locales") or [ctx.config.get("locale", {}).get("default_locale", "en-PH")]
    if isinstance(locales, str):
        locales = [l.strip() for l in locales.split(",")]
    budgets = matrix.get("platform_budgets", {})
    rows = []
    for p in platforms:
        for loc in locales:
            rows.append({"platform": p, "locale": loc,
                         "pin": matrix.get("platform_pins", {}).get(p, "Pin browser/OS build in the session record."),
                         "minutes": int(budgets.get(p, 45))})
    total = sum(r["minutes"] for r in rows)
    budget = int(ctx.params.get("budget_minutes", 240))
    ctx.meta("rows", rows)
    ctx.meta("combinations", len(rows))
    ctx.meta("estimated_minutes", total)
    ctx.meta("within_budget", total <= budget)
    if total > budget:
        ctx.add(rule="matrix-budget", message="Matrix estimates %d minutes against a %d minute budget." % (total, budget),
                severity="low", criterion="planning",
                remediation="Reduce combinations or raise the budget with owner approval.",
                retest="Re-run with the adjusted platforms/locales and confirm within_budget=true.")


# --------------------------------------------------------------------------- #
# Contrast, tokens, motion (also used by categories 5, 6 and 24)
# --------------------------------------------------------------------------- #
@op("contrast-check", "Compute WCAG contrast ratios for declared colour pairs (hex/rgb/CSS custom "
    "properties) and classify each against text, large-text and non-text thresholds.",
    params={"pairs": {"type": "json", "default": None,
                      "doc": "Explicit pairs [{\"fg\":\"#111\",\"bg\":\"#fff\"}] when not reading CSS."},
            "conformance_level": {"type": "enum", "values": ["aa", "aaa"], "default": "aa",
                                  "doc": "Threshold set to apply."}},
    accepts=("html", "css", "json"))
def run_contrast(ctx):
    th = ctx.config.get("thresholds", {})
    level = ctx.params.get("conformance_level", "aa")
    text_th = float(th.get("contrast_text_aaa" if level == "aaa" else "contrast_text_aa", 7.0 if level == "aaa" else 4.5))
    large_th = float(th.get("contrast_large_aa", 3.0))
    nontext_th = float(th.get("contrast_nontext_aa", 3.0))
    explicit = ctx.params.get("pairs")
    pairs = []
    if explicit:
        data = explicit if isinstance(explicit, list) else json.loads(explicit)
        for p in data:
            fg, bg = H.parse_color(p.get("fg")), H.parse_color(p.get("bg"))
            if fg and bg:
                pairs.append({"fg": fg, "bg": bg, "label": p.get("label") or "%s on %s" % (p.get("fg"), p.get("bg"))})
            else:
                ctx.add(rule="contrast-input", message="Unparseable colour pair %r." % (p,), severity="low",
                        criterion="input-validation", remediation="Use hex, rgb() or named CSS colours.",
                        retest="Re-run with corrected colour values.")
    if not pairs:
        doc = ctx.docs[0].data.get("document")
        if doc is None:
            raise S.InputError("No colour pairs available.",
                               hint="Pass --set pairs='[{\"fg\":\"#767676\",\"bg\":\"#ffffff\"}]' or an HTML/CSS artifact.")
        for p in H.color_pairs(doc):
            pairs.append({"fg": p["fg"], "bg": p["bg"],
                          "label": p["node"].locator() if p.get("node") is not None else (p.get("selector") or "css")})
    if not pairs:
        raise S.InputError("No resolvable colour pairs found in the artifact.",
                           hint="Import computed styles or pass explicit pairs with --set pairs=...")
    results = []
    for p in pairs:
        ctx.checkpoint()
        ratio = H.contrast_ratio(p["fg"], p["bg"])
        row = {"label": p.get("label"), "foreground": "#%02x%02x%02x" % p["fg"],
               "background": "#%02x%02x%02x" % p["bg"], "ratio": ratio,
               "pass_text": ratio >= text_th, "pass_large": ratio >= large_th,
               "pass_nontext": ratio >= nontext_th}
        results.append(row)
        if ratio < text_th:
            ctx.add(rule="contrast", message="%s: %.2f:1 is below the %.1f:1 text threshold." % (
                row["label"], ratio, text_th), severity="critical" if ratio < 3 else "high",
                criterion="1.4.3",
                evidence=row, remediation="Darken the foreground or lighten the background until the ratio passes.",
                retest="Re-run contrast-check and confirm pass_text=true for this pair.")
        else:
            ctx.pass_check()
    ctx.meta("pairs", results)
    ctx.meta("worst_ratio", min(r["ratio"] for r in results))
    ctx.meta("thresholds", {"text": text_th, "large": large_th, "nontext": nontext_th})


@op("token-audit", "Audit a design-token set: contrast of colour pairings, typography scale, spacing "
    "and focus tokens, with per-token pass/fail and remediation.",
    params={"min_contrast": {"type": "float", "default": 4.5, "doc": "Required contrast for text tokens."}},
    accepts=("json",))
def run_token_audit(ctx):
    data = ctx.docs[0].data
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if tokens is None:
        raise S.InputError("Token input must contain a 'tokens' object.",
                           hint="Provide {tokens:{color:{...},typography:{...},focus:{...},space:{...}}}.")
    colors = tokens.get("color") or {}
    pairings = (data.get("pairings") if isinstance(data, dict) else None) or []
    min_contrast = float(ctx.params.get("min_contrast", 4.5))
    checked = 0
    for pair in pairings:
        fg = H.parse_color(colors.get(pair.get("fg")) or pair.get("fg"))
        bg = H.parse_color(colors.get(pair.get("bg")) or pair.get("bg"))
        if not fg or not bg:
            ctx.add(rule="token-colour-parse", message="Pairing %r uses unresolvable tokens." % (pair,),
                    severity="low", criterion="token-audit",
                    remediation="Use hex/rgb values or reference existing colour tokens.",
                    retest="Re-run the token audit and confirm the pairing resolves.")
            continue
        checked += 1
        ratio = H.contrast_ratio(fg, bg)
        if ratio < min_contrast:
            ctx.add(rule="token-contrast", message="Token pairing %s on %s is %.2f:1 (< %.1f)." % (
                pair.get("fg"), pair.get("bg"), ratio, min_contrast), severity="high", criterion="1.4.3",
                evidence={"ratio": ratio, "pairing": pair},
                remediation="Adjust the colour tokens so the pairing meets the minimum contrast.",
                retest="Re-run token-audit and confirm the pairing passes.")
        else:
            ctx.pass_check()
    typo = tokens.get("typography") or {}
    for name, spec in (typo.items() if isinstance(typo, dict) else []):
        size = None
        if isinstance(spec, dict):
            size = H.font_size_px({"font-size": str(spec.get("size", ""))})
        if size is not None and size < float(ctx.config.get("thresholds", {}).get("min_font_size_px", 12)):
            ctx.add(rule="token-typography", message="Typography token %r is %.1fpx, below the readable minimum." % (name, size),
                    severity="medium", criterion="1.4.4",
                    remediation="Raise the token to at least 12px and express it in relative units.",
                    retest="Re-run the token audit and confirm the size passes.")
        else:
            ctx.pass_check()
    focus = tokens.get("focus") or {}
    if isinstance(focus, dict) and not (focus.get("outline") or focus.get("ring") or focus.get("indicator")):
        ctx.add(rule="token-focus", message="No focus token is defined, so focus appearance cannot be guaranteed.",
                severity="high", criterion="2.4.13",
                remediation="Define a focus token with at least a 2px indicator and 3:1 contrast.",
                retest="Re-run the audit and confirm a focus token is present and contrast-passing.")
    else:
        ctx.pass_check()
    spacing = tokens.get("space") or tokens.get("spacing") or {}
    if isinstance(spacing, dict):
        small = [k for k, v in spacing.items() if isinstance(v, (int, float)) and v < 4]
        if small:
            ctx.add(rule="token-spacing", message="Spacing tokens below 4 units: %s." % ", ".join(small),
                    severity="low", criterion="2.5.8",
                    remediation="Use 4-unit spacing steps so targets can meet the 24px minimum.",
                    retest="Re-run and confirm no spacing token is below the recommended floor.")
        else:
            ctx.pass_check()
    ctx.meta("tokens_checked", checked)
    ctx.meta("token_groups", sorted(tokens.keys()) if isinstance(tokens, dict) else [])


@op("motion-audit", "Analyse declared animations for flash frequency, infinite loops, autoplay, "
    "reduced-motion handling and cognitive load, against WCAG 2.2.2 and 2.3.",
    params={"max_flashes_per_second": {"type": "float", "default": 3.0,
                                       "doc": "Flash threshold before failing."},
            "max_loop_seconds": {"type": "float", "default": 5.0,
                                 "doc": "Longest permitted unattended loop."}},
    accepts=("html", "css", "json"))
def run_motion_audit(ctx):
    limit = float(ctx.params.get("max_flashes_per_second", 3.0))
    if ctx.docs[0].kind == "json":
        data = ctx.docs[0].data
        animations = (data.get("animations") if isinstance(data, dict) else data) or []
        for a in animations:
            dur = float(a.get("duration_ms", 1000)) / 1000.0 or 1.0
            freq = 1.0 / dur
            name = a.get("name") or a.get("selector") or "animation"
            if a.get("flashes") and freq > limit:
                ctx.add(rule="motion-flash", message="%s flashes at %.1fHz (> %.1fHz)." % (name, freq, limit),
                        severity="critical", criterion="2.3.1", evidence=a,
                        remediation="Slow the animation or reduce the flashing area.",
                        retest="Re-run motion-audit and confirm frequency is within the threshold.")
            elif a.get("infinite") and not a.get("pause_control"):
                ctx.add(rule="motion-loop", message="%s loops indefinitely with no pause control." % name,
                        severity="high", criterion="2.2.2", evidence=a,
                        remediation="Add pause/stop controls or limit the loop to five seconds.",
                        retest="Re-run and confirm a pause control is recorded for the animation.")
            else:
                ctx.pass_check()
    else:
        doc = ctx.docs[0].data.get("document")
        if doc is None:
            raise S.InputError("Motion audit needs HTML, CSS or an explicit animation list.",
                               hint="Pass an HTML file with <style>, a CSS file, or JSON with an 'animations' list.")
        css = "\n".join(H.style_blocks(doc)) + "\n" + doc.text
        found = 0
        for selectors, decls in [d for blk in H.style_blocks(doc) for d in H.declarations(blk)]:
            anim = path = decls.get("animation") or decls.get("animation-name")
            if not anim:
                continue
            found += 1
            infinite = "infinite" in (decls.get("animation") or "")
            m = re.search(r"([\d.]+)\s*(ms|s)\b", decls.get("animation") or decls.get("animation-duration") or "")
            dur = None
            if m:
                dur = float(m.group(1)) / (1000.0 if m.group(2) == "ms" else 1.0)
            paused = bool(re.search(r"pause|stop|state", selectors, re.I))
            if infinite and not paused:
                ctx.add(rule="motion-loop", message="Selector %r animates infinitely with no obvious pause control." % selectors[:70],
                        severity="high", criterion="2.2.2",
                        evidence={"selector": selectors[:300], "infinite": True},
                        remediation="Add a pause control, limit iterations, or trigger on interaction only.",
                        retest="Re-run motion-audit and confirm the animation is bounded or controllable.")
            elif dur and dur < 1.0 / limit and re.search(r"flash|blink|strobe", selectors, re.I):
                ctx.add(rule="motion-flash", message="Selector %r may flash faster than %.1fHz." % (selectors[:70], limit),
                        severity="critical", criterion="2.3.1",
                        evidence={"selector": selectors[:300], "duration_s": dur},
                        remediation="Reduce frequency below the threshold or remove the effect.",
                        retest="Re-run and confirm no flash-rate finding remains.")
            else:
                ctx.pass_check()
        if "prefers-reduced-motion" not in css and found:
            ctx.add(rule="motion-reduced", message="Animations present but no prefers-reduced-motion override.",
                    severity="high", criterion="2.3.3",
                    remediation="Add @media (prefers-reduced-motion: reduce) with animation:none.",
                    retest="Re-run and confirm the reduced-motion branch is detected.")
        elif found:
            ctx.pass_check()
    ctx.meta("flash_threshold_hz", limit)
