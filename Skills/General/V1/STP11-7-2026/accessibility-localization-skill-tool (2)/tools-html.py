"""Dependency-free HTML/CSS inspection layer.

Builds a tolerant element tree with source positions, then exposes the modern
browser-shaped facts the accessibility engines need: implicit and explicit
roles, focusability, the accessible-name computation (HTML-AAM/accname subset),
description, landmarks, headings, form controls, CSS declaration harvesting and
WCAG contrast maths.
"""

import re
from html.parser import HTMLParser

S = None
T = None


def init(deps):
    global S, T
    S = deps["support"]
    T = deps["text"]


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}
RAW_TEXT = {"script", "style", "textarea", "title"}
AUTO_CLOSE = {"p", "li", "dt", "dd", "option", "optgroup", "tr", "td", "th", "thead", "tbody",
              "tfoot", "colgroup", "caption", "figure"}

LANDMARK_ROLES = {"banner", "main", "navigation", "contentinfo", "complementary", "search",
                  "form", "region", "application"}
INTERACTIVE_ROLES = {"button", "link", "checkbox", "radio", "switch", "tab", "menuitem",
                     "menuitemcheckbox", "menuitemradio", "option", "slider", "spinbutton",
                     "textbox", "searchbox", "combobox", "listbox", "treeitem", "gridcell",
                     "scrollbar"}
FORM_CONTROLS = {"input", "select", "textarea", "button", "output", "meter", "progress"}

INPUT_TYPE_ROLE = {
    "button": "button", "submit": "button", "reset": "button", "image": "button",
    "checkbox": "checkbox", "radio": "radio", "range": "slider", "number": "spinbutton",
    "search": "searchbox", "email": "textbox", "tel": "textbox", "url": "textbox",
    "text": "textbox", "password": "textbox", "file": "button", "color": "button",
    "date": None, "time": None, "datetime-local": None, "month": None, "week": None,
    "hidden": None,
}

IMPLICIT_ROLES = {
    "a": "link", "article": "article", "aside": "complementary", "body": "generic",
    "button": "button", "datalist": "listbox", "dd": "definition", "details": "group",
    "dialog": "dialog", "dt": "term", "fieldset": "group", "figure": "figure",
    "footer": "contentinfo", "form": "form", "h1": "heading", "h2": "heading",
    "h3": "heading", "h4": "heading", "h5": "heading", "h6": "heading",
    "header": "banner", "hr": "separator", "img": "img", "input": "textbox",
    "li": "listitem", "main": "main", "math": "math", "menu": "list", "meter": "meter",
    "nav": "navigation", "ol": "list", "optgroup": "group", "option": "option",
    "output": "status", "p": "paragraph", "progress": "progressbar", "section": "region",
    "select": "combobox", "summary": "button", "table": "table", "tbody": "rowgroup",
    "td": "cell", "textarea": "textbox", "tfoot": "rowgroup", "th": "columnheader",
    "thead": "rowgroup", "tr": "row", "ul": "list",
}

NAMED_COLORS = {
    "black": "000000", "white": "ffffff", "red": "ff0000", "green": "008000", "blue": "0000ff",
    "yellow": "ffff00", "gray": "808080", "grey": "808080", "silver": "c0c0c0",
    "maroon": "800000", "navy": "000080", "teal": "008080", "olive": "808000",
    "purple": "800080", "lime": "00ff00", "aqua": "00ffff", "cyan": "00ffff",
    "fuchsia": "ff00ff", "magenta": "ff00ff", "orange": "ffa500", "brown": "a52a2a",
    "pink": "ffc0cb", "gold": "ffd700", "transparent": None, "inherit": None,
    "currentcolor": None,
}


class Node(object):
    __slots__ = ("tag", "attrs", "children", "parent", "line", "col", "text_only")

    def __init__(self, tag, attrs=None, parent=None, line=0, col=0):
        self.tag = (tag or "").lower()
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent
        self.line = line
        self.col = col

    # -- basics ------------------------------------------------------------ #
    def get(self, name, default=None):
        return self.attrs.get(name, default)

    def has(self, name):
        return name in self.attrs

    @property
    def classes(self):
        return set((self.get("class") or "").split())

    def child_elements(self):
        return [c for c in self.children if isinstance(c, Node)]

    def iter(self):
        yield self
        for c in self.child_elements():
            for n in c.iter():
                yield n

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def find_all(self, tag=None, pred=None):
        out = []
        for n in self.iter():
            if n is self:
                continue
            if tag and n.tag != tag:
                continue
            if pred and not pred(n):
                continue
            out.append(n)
        return out

    def text(self):
        parts = []
        for c in self.children:
            if isinstance(c, Node):
                if c.tag in ("script", "style"):
                    continue
                parts.append(c.text())
            else:
                parts.append(c)
        return T.norm_ws(" ".join(parts)) if parts else ""

    def raw_text(self):
        parts = []
        for c in self.children:
            parts.append(c.raw_text() if isinstance(c, Node) else c)
        return "".join(parts)

    def locator(self, extra=None):
        bits = ["<%s>" % self.tag]
        if self.get("id"):
            bits.append("#" + self.get("id"))
        cls = sorted(self.classes)
        if cls:
            bits.append("." + ".".join(cls[:2]))
        base = "line %d" % self.line
        if extra:
            base += ", " + extra
        return "%s (%s)" % ("".join(bits), base)

    def path(self):
        chain = []
        node = self
        while node is not None and node.tag not in ("[document]",):
            chain.append(node.tag)
            node = node.parent
        return " > ".join(reversed(chain))


class _Builder(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.root = Node("[document]")
        self.stack = [self.root]
        self.errors = []

    def _close_to(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return True
        return False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        line, col = self.getpos()
        attr_map = {}
        for k, v in attrs:
            k = (k or "").lower()
            if k in attr_map:
                attr_map[k] = (attr_map[k] or "") + " " + (v or "")
            else:
                attr_map[k] = v if v is not None else ""
        if tag in AUTO_CLOSE:
            self._close_to(tag)
        node = Node(tag, attr_map, self.stack[-1], line, col)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        line, col = self.getpos()
        node = Node(tag, {(k or "").lower(): (v or "") for k, v in attrs}, self.stack[-1], line, col)
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in VOID:
            return
        self._close_to(tag)

    def handle_data(self, data):
        self.stack[-1].children.append(data)


class Document(object):
    def __init__(self, text, source="<inline>"):
        self.source = source
        self.text = text or ""
        builder = _Builder()
        try:
            builder.feed(self.text)
            builder.close()
        except Exception as exc:  # html.parser is tolerant; record and continue
            builder.errors.append(str(exc))
        self.root = builder.root
        self.parse_errors = builder.errors
        self.nodes = list(self.root.iter())

    # -- lookups ----------------------------------------------------------- #
    def by_tag(self, *tags):
        tags = set(tags)
        return [n for n in self.nodes if n.tag in tags]

    def by_role(self, *roles):
        roles = set(roles)
        return [n for n in self.nodes if role_of(n) in roles]

    def ids(self):
        out = {}
        for n in self.nodes:
            if n.get("id"):
                out.setdefault(n.get("id"), []).append(n)
        return out

    def body_text(self):
        body = self.by_tag("body") or self.by_tag("html") or [self.root]
        return body[0].text()

    def title(self):
        t = self.by_tag("title")
        return T.norm_ws(t[0].text()) if t else ""

    def lang_attrs(self):
        return [(n, n.get("lang")) for n in self.by_tag("html")]


def parse(text, source="<inline>"):
    return Document(text, source)


# --------------------------------------------------------------------------- #
# Roles, names, states
# --------------------------------------------------------------------------- #
def role_of(node):
    explicit = (node.get("role") or "").strip().split()
    if explicit:
        return explicit[0].lower()
    tag = node.tag
    if tag == "input":
        itype = (node.get("type") or "text").lower()
        if itype == "hidden":
            return None
        if itype == "image":
            return "button"
        return INPUT_TYPE_ROLE.get(itype, "textbox")
    if tag == "a":
        return "link" if node.has("href") else "generic"
    if tag == "th":
        return "rowheader" if (node.get("scope") or "").lower() == "row" else "columnheader"
    if tag == "section":
        return "region" if (node.has("aria-label") or node.has("aria-labelledby") or node.has("title")) else "generic"
    if tag == "header":
        parent = node.parent
        return "banner" if parent is not None and parent.tag in ("[document]", "body") else "generic"
    if tag == "footer":
        parent = node.parent
        return "contentinfo" if parent is not None and parent.tag in ("[document]", "body") else "generic"
    if tag == "img":
        return "presentation" if (node.get("alt") == "" and not node.has("aria-label")) else "img"
    return IMPLICIT_ROLES.get(tag)


def is_aria_hidden(node):
    for n in [node] + list(node.ancestors()):
        if (n.get("aria-hidden") or "").lower() == "true":
            return True
        if n.has("hidden"):
            return True
        style = (n.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style:
            return True
    return False


def is_focusable(node):
    if is_aria_hidden(node):
        return False
    if node.has("disabled") and node.tag in FORM_CONTROLS:
        return False
    tabindex = node.get("tabindex")
    if tabindex is not None:
        try:
            return int(str(tabindex).strip()) >= 0 or node.tag in FORM_CONTROLS or node.has("contenteditable")
        except ValueError:
            return False
    if node.tag in ("a", "area") and node.has("href"):
        return True
    if node.tag in ("button", "select", "textarea"):
        return True
    if node.tag == "input":
        return (node.get("type") or "text").lower() != "hidden"
    if node.tag in ("audio", "video") and node.has("controls"):
        return True
    if node.tag == "iframe":
        return True
    if node.has("contenteditable") and (node.get("contenteditable") or "").lower() != "false":
        return True
    if node.tag == "summary" and node.parent is not None and node.parent.tag == "details":
        return True
    return False


def tabindex_of(node):
    raw = node.get("tabindex")
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def _labelledby_text(doc, node):
    ids = (node.get("aria-labelledby") or "").split()
    if not ids:
        return ""
    idmap = doc.ids()
    parts = []
    for i in ids:
        for target in idmap.get(i, []):
            parts.append(target.text())
    return T.norm_ws(" ".join(parts))


def _native_label_text(doc, node):
    if node.get("id"):
        for lab in doc.by_tag("label"):
            if lab.get("for") == node.get("id"):
                return lab.text()
    parent = node.parent
    while parent is not None:
        if parent.tag == "label":
            return parent.text()
        parent = parent.parent
    return ""


def accessible_name(doc, node):
    """HTML-AAM accname subset; returns (name, source)."""
    name = _labelledby_text(doc, node)
    if name:
        return name, "aria-labelledby"
    if node.get("aria-label") is not None:
        label = T.norm_ws(node.get("aria-label"))
        if label:
            return label, "aria-label"
    tag = node.tag
    if tag == "img" or tag == "area":
        if node.has("alt"):
            return T.norm_ws(node.get("alt")), "alt"
        return T.norm_ws(node.get("title")), "title"
    if tag in ("input", "select", "textarea"):
        itype = (node.get("type") or "text").lower()
        if itype in ("submit", "button", "reset"):
            if node.has("value"):
                return T.norm_ws(node.get("value")), "value"
            return {"submit": "Submit", "reset": "Reset"}.get(itype, ""), "default"
        if itype == "image":
            return T.norm_ws(node.get("alt") or node.get("value") or node.get("title")), "alt"
        native = _native_label_text(doc, node)
        if native:
            return native, "label"
        if node.get("placeholder") and not node.has("aria-label"):
            return T.norm_ws(node.get("placeholder")), "placeholder"
        return T.norm_ws(node.get("title")), "title"
    if tag == "fieldset":
        legends = [c for c in node.child_elements() if c.tag == "legend"]
        if legends:
            return legends[0].text(), "legend"
    if tag == "figure":
        caps = [c for c in node.child_elements() if c.tag == "figcaption"]
        if caps:
            return caps[0].text(), "figcaption"
    if tag == "table":
        caps = [c for c in node.child_elements() if c.tag == "caption"]
        if caps:
            return caps[0].text(), "caption"
    if tag == "svg":
        titles = [c for c in node.child_elements() if c.tag == "title"]
        if titles:
            return titles[0].text(), "svg-title"
        return T.norm_ws(node.get("aria-label")), "aria-label"
    if tag in ("iframe", "frame"):
        return T.norm_ws(node.get("title")), "title"
    text = node.text()
    if text:
        return text, "contents"
    return T.norm_ws(node.get("title")), "title"


def accessible_description(doc, node):
    ids = (node.get("aria-describedby") or "").split()
    idmap = doc.ids()
    parts = []
    for i in ids:
        for target in idmap.get(i, []):
            parts.append(target.text())
    if parts:
        return T.norm_ws(" ".join(parts)), "aria-describedby"
    if node.get("aria-description"):
        return T.norm_ws(node.get("aria-description")), "aria-description"
    if node.get("title") and not accessible_name(doc, node)[0]:
        return T.norm_ws(node.get("title")), "title"
    return "", ""


def states(node):
    out = {}
    for key in ("aria-expanded", "aria-pressed", "aria-checked", "aria-selected", "aria-current",
                "aria-disabled", "aria-required", "aria-invalid", "aria-haspopup", "aria-busy",
                "aria-live", "aria-modal", "aria-orientation", "aria-valuenow", "aria-valuemin",
                "aria-valuemax", "aria-sort", "aria-level", "aria-controls", "aria-owns",
                "aria-activedescendant", "aria-autocomplete", "aria-multiselectable"):
        if node.has(key):
            out[key] = node.get(key)
    for flag in ("disabled", "required", "readonly", "checked", "multiple", "open"):
        if node.has(flag):
            out["html:" + flag] = node.get(flag) or flag
    return out


def announcement(doc, node):
    """Expected screen-reader announcement for a node, as far as it is derivable."""
    name, name_src = accessible_name(doc, node)
    role = role_of(node)
    desc, desc_src = accessible_description(doc, node)
    st = states(node)
    spoken = name or ""
    chunks = [spoken]
    if role and role not in ("generic", "presentation", "none"):
        chunks.append(role)
    for key in ("aria-expanded", "aria-checked", "aria-pressed", "aria-selected", "aria-disabled"):
        if key in st:
            chunks.append("%s %s" % (key.replace("aria-", ""), st[key]))
    if "aria-level" in st:
        chunks.append("level %s" % st["aria-level"])
    if "aria-required" in st or "html:required" in st:
        chunks.append("required")
    return {
        "name": name,
        "name_source": name_src,
        "role": role,
        "description": desc,
        "description_source": desc_src,
        "states": st,
        "expected_announcement": ", ".join([c for c in chunks if c]),
        "focusable": is_focusable(node),
        "tabindex": tabindex_of(node),
        "locator": node.locator(),
    }


# --------------------------------------------------------------------------- #
# Structure helpers
# --------------------------------------------------------------------------- #
def headings(doc):
    out = []
    for n in doc.nodes:
        role = role_of(n)
        if role == "heading" or n.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if role in ("presentation", "none"):
                continue
            level = None
            if n.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(n.tag[1])
            elif n.get("aria-level"):
                try:
                    level = int(str(n.get("aria-level")).strip())
                except ValueError:
                    level = None
            if is_aria_hidden(n) and not n.get("aria-level"):
                continue
            name, src = accessible_name(doc, n)
            out.append({"level": level, "text": name, "node": n, "name_source": src})
    return out


def landmarks(doc):
    out = []
    for n in doc.nodes:
        role = role_of(n)
        if role in LANDMARK_ROLES:
            name, src = accessible_name(doc, n)
            out.append({"role": role, "name": name, "node": n})
    return out


def focus_order(doc):
    items = [n for n in doc.nodes if is_focusable(n)]
    positive = [n for n in items if (tabindex_of(n) or 0) > 0]
    positive.sort(key=lambda n: tabindex_of(n))
    zero = [n for n in items if not (tabindex_of(n) or 0) > 0]
    return positive + zero


# --------------------------------------------------------------------------- #
# CSS
# --------------------------------------------------------------------------- #
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)


def style_blocks(doc):
    blocks = []
    for n in doc.by_tag("style"):
        blocks.append(n.raw_text())
    for n in doc.by_tag("link"):
        if "stylesheet" in (n.get("rel") or "").lower():
            blocks.append("/* external stylesheet: %s */" % (n.get("href") or ""))
    return blocks


def declarations(block):
    out = []
    for selectors, body in _RULE.findall(_COMMENT.sub("", block or "")):
        decls = {}
        for part in body.split(";"):
            if ":" not in part:
                continue
            k, v = part.split(":", 1)
            decls[k.strip().lower()] = v.strip()
        out.append((selectors.strip(), decls))
    return out


def inline_decls(node):
    decls = {}
    for part in (node.get("style") or "").split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        decls[k.strip().lower()] = v.strip()
    return decls


def parse_color(value):
    if not value:
        return None
    v = value.strip().lower()
    if v in NAMED_COLORS:
        hexv = NAMED_COLORS[v]
        if hexv is None:
            return None
        v = "#" + hexv
    m = re.match(r"^#([0-9a-f]{3,8})$", v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        elif len(h) == 4:
            h = "".join(h[i] * 2 for i in range(3))
        elif len(h) == 8:
            h = h[:6]
        if len(h) != 6:
            return None
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = re.match(r"^rgba?\(([^)]+)\)$", v)
    if m:
        parts = [p.strip() for p in re.split(r"[,\s/]+", m.group(1)) if p.strip()]
        if len(parts) < 3:
            return None
        try:
            if all(p.endswith("%") for p in parts[:3]):
                rgb = tuple(int(round(float(p.rstrip("%")) * 2.55)) for p in parts[:3])
            else:
                rgb = tuple(int(float(p)) for p in parts[:3])
        except ValueError:
            return None
        alpha = 1.0
        if len(parts) > 3:
            try:
                alpha = float(parts[3].rstrip("%"))
                if parts[3].endswith("%"):
                    alpha /= 100.0
            except ValueError:
                alpha = 1.0
        if alpha < 1.0:
            return None  # compositing unknown without a layout engine
        return tuple(max(0, min(255, c)) for c in rgb)
    return None


def rel_luminance(rgb):
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(fg, bg):
    l1, l2 = rel_luminance(fg), rel_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return round((hi + 0.05) / (lo + 0.05), 2)


def color_pairs(doc):
    """Best-effort foreground/background pairs from inline styles and stylesheets."""
    pairs = []
    for n in doc.nodes:
        decls = inline_decls(n)
        if "color" in decls or "background-color" in decls or "background" in decls:
            fg = parse_color(decls.get("color"))
            bg_raw = decls.get("background-color") or decls.get("background")
            bg = parse_color(bg_raw)
            if fg and bg:
                pairs.append({"fg": fg, "bg": bg, "node": n})
    for block in style_blocks(doc):
        for selectors, decls in declarations(block):
            fg = parse_color(decls.get("color"))
            bg = parse_color(decls.get("background-color") or decls.get("background"))
            if fg and bg:
                pairs.append({"fg": fg, "bg": bg, "selector": selectors})
    return pairs


def font_size_px(decls, base=16.0):
    val = (decls.get("font-size") or "").strip().lower()
    if not val:
        return None
    m = re.match(r"^([\d.]+)\s*(px|pt|em|rem|%)?$", val)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2) or "px"
    if unit == "px":
        return num
    if unit == "pt":
        return num * 4.0 / 3.0
    if unit in ("em", "rem"):
        return num * base
    if unit == "%":
        return num / 100.0 * base
    return None


def is_large_text(size_px, bold=False):
    if size_px is None:
        return False
    if bold:
        return size_px >= 18.66
    return size_px >= 24.0
