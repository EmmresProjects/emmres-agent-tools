"""Structured text (12) and programming/code-text (12) capabilities.

Includes a dependency-free YAML subset parser/dumper, a CSV toolkit built on the
standard ``csv`` module, a small template engine, and language-aware string
escaping. Python source handling uses the standard ``ast`` module.
"""

from __future__ import annotations

import ast
import csv
import difflib
import html
import io
import json
import re
import re as _re
import textwrap
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

EXPORTS = ["yaml_load", "yaml_dump", "simple_template_render", "case_convert",
           "RESERVED_WORDS", "json_safe_load"]


# --------------------------------------------------------------------------
# YAML subset
# --------------------------------------------------------------------------


class YamlError(Exception):
    pass


def _strip_comment(line):
    out, quote = [], None
    for idx, char in enumerate(line):
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            out.append(char)
            continue
        if char == "#" and (idx == 0 or line[idx - 1] in " \t"):
            break
        out.append(char)
    return "".join(out).rstrip()


def _scalar(token):
    text = token.strip()
    if text == "" or text == "~" or text.lower() == "null":
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+([eE][-+]?\d+)?", text):
        return float(text)
    if text.startswith("[") or text.startswith("{"):
        try:
            return json.loads(text.replace("'", '"'))
        except json.JSONDecodeError:
            return text
    return text


def yaml_load(text):
    """Parse a practical YAML subset: block maps, sequences, scalars, comments."""
    lines = []
    for raw in core.canonical_newlines(text).split("\n"):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError("tab indentation is not allowed in YAML (line %r)" % raw.strip()[:40])
        stripped = _strip_comment(raw)
        if not stripped.strip() or stripped.strip() in ("---", "..."):
            continue
        lines.append((len(stripped) - len(stripped.lstrip()), stripped.strip()))
    if not lines:
        return None
    pos = [0]

    def parse_block(indent):
        if pos[0] >= len(lines):
            return None
        current_indent, content = lines[pos[0]]
        if current_indent < indent:
            return None
        if content.startswith("- "):
            return parse_seq(current_indent)
        return parse_map(current_indent)

    def parse_seq(indent):
        items = []
        while pos[0] < len(lines):
            cur_indent, content = lines[pos[0]]
            if cur_indent != indent or not content.startswith("-"):
                break
            rest = content[1:].strip()
            pos[0] += 1
            if not rest:
                items.append(parse_block(indent + 1))
            elif re.match(r"^[^:]+:\s", rest) or rest.endswith(":"):
                # inline mapping start: "- key: value"
                key, _, value = rest.partition(":")
                entry = {}
                if value.strip():
                    entry[key.strip()] = _scalar(value)
                else:
                    entry[key.strip()] = parse_block(indent + 2)
                while pos[0] < len(lines):
                    nxt_indent, nxt = lines[pos[0]]
                    if nxt_indent <= indent or nxt.startswith("- "):
                        break
                    if ":" not in nxt:
                        break
                    k, _, v = nxt.partition(":")
                    pos[0] += 1
                    entry[k.strip()] = _scalar(v) if v.strip() else parse_block(nxt_indent + 1)
                items.append(entry)
            else:
                items.append(_scalar(rest))
        return items

    def parse_map(indent):
        result = {}
        while pos[0] < len(lines):
            cur_indent, content = lines[pos[0]]
            if cur_indent < indent:
                break
            if cur_indent > indent:
                break
            if content.startswith("- "):
                break
            if ":" not in content:
                raise YamlError("expected 'key: value' but found %r" % content[:40])
            key, _, value = content.partition(":")
            key = key.strip().strip("\"'")
            if key in result:
                raise YamlError("duplicate key %r" % key)
            pos[0] += 1
            if value.strip():
                result[key] = _scalar(value)
            else:
                nxt = lines[pos[0]] if pos[0] < len(lines) else None
                if nxt and nxt[0] > indent:
                    result[key] = parse_block(nxt[0])
                elif nxt and nxt[0] == indent and nxt[1].startswith("- "):
                    result[key] = parse_seq(indent)
                else:
                    result[key] = None
        return result

    parsed = parse_block(lines[0][0])
    if pos[0] != len(lines):
        raise YamlError("could not parse line %r (check indentation)" % lines[pos[0]][1][:40])
    return parsed


def yaml_dump(obj, indent=2, level=0):
    pad = " " * (indent * level)
    if isinstance(obj, dict):
        if not obj:
            return pad + "{}"
        lines = []
        for key, value in obj.items():
            if isinstance(value, (dict, list)) and value:
                lines.append("%s%s:" % (pad, key))
                lines.append(yaml_dump(value, indent, level + 1))
            else:
                lines.append("%s%s: %s" % (pad, key, _yaml_scalar(value)))
        return "\n".join(lines)
    if isinstance(obj, list):
        if not obj:
            return pad + "[]"
        lines = []
        for item in obj:
            if isinstance(item, (dict, list)) and item:
                rendered = yaml_dump(item, indent, level + 1).split("\n")
                first = rendered[0].lstrip()
                lines.append("%s- %s" % (pad, first))
                lines.extend(rendered[1:])
            else:
                lines.append("%s- %s" % (pad, _yaml_scalar(item)))
        return "\n".join(lines)
    return pad + _yaml_scalar(obj)


def _yaml_scalar(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or re.search(r"[:#\-{}\[\],&*?|<>=!%@`\"']", text) or text.strip() != text:
        return json.dumps(text, ensure_ascii=False)
    return text


# --------------------------------------------------------------------------
# other shared helpers
# --------------------------------------------------------------------------


RESERVED_WORDS = {
    "python": {"and", "as", "assert", "async", "await", "break", "class", "continue", "def",
               "del", "elif", "else", "except", "finally", "for", "from", "global", "if",
               "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise",
               "return", "try", "while", "with", "yield", "True", "False", "None"},
    "javascript": {"break", "case", "catch", "class", "const", "continue", "debugger",
                   "default", "delete", "do", "else", "export", "extends", "finally", "for",
                   "function", "if", "import", "in", "instanceof", "let", "new", "return",
                   "super", "switch", "this", "throw", "try", "typeof", "var", "void",
                   "while", "with", "yield", "await", "null", "true", "false"},
    "java": {"abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
             "class", "const", "continue", "default", "do", "double", "else", "enum",
             "extends", "final", "finally", "float", "for", "if", "implements", "import",
             "instanceof", "int", "interface", "long", "native", "new", "package",
             "private", "protected", "public", "return", "short", "static", "strictfp",
             "super", "switch", "synchronized", "this", "throw", "throws", "transient",
             "try", "void", "volatile", "while", "true", "false", "null"},
    "go": {"break", "case", "chan", "const", "continue", "default", "defer", "else",
           "fallthrough", "for", "func", "go", "goto", "if", "import", "interface", "map",
           "package", "range", "return", "select", "struct", "switch", "type", "var"},
    "rust": {"as", "break", "const", "continue", "crate", "else", "enum", "extern", "false",
             "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod", "move", "mut",
             "pub", "ref", "return", "self", "static", "struct", "super", "trait", "true",
             "type", "unsafe", "use", "where", "while", "async", "await", "dyn"},
    "csharp": {"abstract", "as", "base", "bool", "break", "byte", "case", "catch", "char",
               "checked", "class", "const", "continue", "decimal", "default", "delegate",
               "do", "double", "else", "enum", "event", "explicit", "extern", "false",
               "finally", "fixed", "float", "for", "foreach", "goto", "if", "implicit",
               "in", "int", "interface", "internal", "is", "lock", "long", "namespace",
               "new", "null", "object", "operator", "out", "override", "params", "private",
               "protected", "public", "readonly", "ref", "return", "sbyte", "sealed",
               "short", "sizeof", "stackalloc", "static", "string", "struct", "switch",
               "this", "throw", "true", "try", "typeof", "uint", "ulong", "unchecked",
               "unsafe", "ushort", "using", "virtual", "void", "volatile", "while"},
    "sql": {"select", "from", "where", "insert", "update", "delete", "create", "drop",
            "table", "index", "join", "group", "order", "by", "having", "limit", "union",
            "into", "values", "set", "and", "or", "not", "null", "primary", "key"},
    "generic": set(),
}


def json_safe_load(text):
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, exc


def case_convert(text, case):
    words = re.findall(r"[A-Za-z0-9]+", text)
    if not words:
        return ""
    lowered = [w.lower() for w in words]
    if case == "snake":
        return "_".join(lowered)
    if case == "upper":
        return "_".join(w.upper() for w in lowered)
    if case == "kebab":
        return "-".join(lowered)
    if case == "dot":
        return ".".join(lowered)
    if case == "camel":
        return lowered[0] + "".join(w.capitalize() for w in lowered[1:])
    if case == "pascal":
        return "".join(w.capitalize() for w in lowered)
    raise core.InvalidParam("unknown case %r" % case)


def simple_template_render(template, context, strict=True):
    """Tiny {{var}} / {{#if var}} / {{#each list}} engine."""
    missing = []

    def lookup(path):
        current = context
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                missing.append(path)
                return ""
        return current

    def each_block(match):
        key, body = match.group(1), match.group(2)
        items = lookup(key)
        if not isinstance(items, list):
            return ""
        out = []
        for item in items:
            scope = dict(context)
            if isinstance(item, dict):
                scope.update(item)
            else:
                scope["this"] = item
            out.append(simple_template_render(body, scope, strict=False))
        return "".join(out)

    def if_block(match):
        key, body = match.group(1), match.group(2)
        value = lookup(key)
        return simple_template_render(body, context, strict=False) if value else ""

    rendered = re.sub(r"\{\{#each ([^}]+)\}\}(.*?)\{\{/each\}\}", each_block, template, flags=re.S)
    rendered = re.sub(r"\{\{#if ([^}]+)\}\}(.*?)\{\{/if\}\}", if_block, rendered, flags=re.S)
    rendered = re.sub(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}", lambda m: str(lookup(m.group(1))), rendered)
    if strict and missing:
        raise core.InvalidInput("template variables missing from context: %s"
                                % ", ".join(sorted(set(missing))),
                                hint="Pass --set context='{\"name\": \"value\"}'")
    return rendered


# --------------------------------------------------------------------------
# structured formats
# --------------------------------------------------------------------------


def markdown_repair(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    action = ctx.params["action"]
    max_line = int(ctx.params["max_line"])
    lines = core.canonical_newlines(text).split("\n")
    issues = []

    def lint():
        level_stack = [0]
        for idx, line in enumerate(lines, 1):
            if re.match(r"^#{1,6}\s", line):
                level = len(line) - len(line.lstrip("#"))
                if level > level_stack[-1] + 1:
                    issues.append({"line": idx, "issue": "heading-level-jump",
                                   "detail": "h%d after h%d" % (level, level_stack[-1])})
                level_stack.append(level)
                if not line[level:].startswith(" "):
                    issues.append({"line": idx, "issue": "heading-missing-space", "detail": line[:40]})
            if line.rstrip() != line:
                issues.append({"line": idx, "issue": "trailing-space", "detail": repr(line[-6:])})
            if max_line and len(line) > max_line:
                issues.append({"line": idx, "issue": "long-line", "detail": "%d chars" % len(line)})
            if re.match(r"^\s*[-*+]\s", line) is None and re.match(r"^\s*\d+\.\s", line):
                pass
        fences = sum(1 for line in lines if line.strip().startswith("```"))
        if fences % 2:
            issues.append({"line": 0, "issue": "unbalanced-code-fence", "detail": "%d fences" % fences})
        for idx in range(1, len(lines)):
            prev, cur = lines[idx - 1].strip(), lines[idx].strip()
            if re.match(r"^#{1,6}\s", cur) and prev and not prev.startswith("#"):
                issues.append({"line": idx + 1, "issue": "heading-needs-blank-line", "detail": cur[:40]})
            if re.match(r"^\s*[-*+]\s", cur) and prev and not re.match(r"^(\s*[-*+]\s|$)", prev) \
                    and not prev.startswith("#"):
                issues.append({"line": idx + 1, "issue": "list-needs-blank-line", "detail": cur[:40]})

    if action in ("lint", "repair"):
        lint()
        out = "\n".join(lines)
        if action == "repair" and ctx.params["fix"]:
            out = re.sub(r"[ \t]+\n", "\n", out)
            out = re.sub(r"\n{3,}", "\n\n", out)
            out = re.sub(r"^(#{1,6})([^ #])", r"\1 \2", out, flags=re.M)
            out = re.sub(r"(?m)^(\s*)([-*+])([^\s])", r"\1\2 \3", out)
            out = re.sub(r"(?m)^(#{1,6}\s.*?)$", r"\1\n", out)
            out = re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"
        if action == "lint":
            if not issues:
                return {"output": "markdown lint: no issues found",
                        "data": {"issues": [], "count": 0}}
            rows = [[i["line"], i["issue"], i["detail"]] for i in issues]
            return {"output": core.table(rows, ["line", "issue", "detail"]),
                    "data": {"issues": issues, "count": len(issues)}}
        return {"output": out, "data": {"issues": issues, "count": len(issues),
                                        "repaired": True}}
    if action == "outline":
        tree = []
        for line in lines:
            match = re.match(r"^(#{1,6})\s+(.*)$", line)
            if match:
                tree.append({"level": len(match.group(1)), "title": match.group(2).strip()})
        output = "\n".join("%s%s" % ("  " * (t["level"] - 1), t["title"]) for t in tree) or "no headings found"
        return {"output": output, "data": {"headings": tree, "count": len(tree)}}
    if action == "to-html":
        try:
            import markdown  # optional, never required
            return {"output": markdown.markdown(text), "data": {"converter": "markdown"}}
        except ImportError:
            out = []
            in_code = False
            for line in lines:
                if line.strip().startswith("```"):
                    in_code = not in_code
                    out.append("<pre><code>" if in_code else "</code></pre>")
                    continue
                if in_code:
                    out.append(html.escape(line))
                    continue
                match = re.match(r"^(#{1,6})\s+(.*)$", line)
                if match:
                    out.append("<h%d>%s</h%d>" % (len(match.group(1)), html.escape(match.group(2)), len(match.group(1))))
                elif re.match(r"^\s*[-*+]\s", line):
                    out.append("<li>%s</li>" % html.escape(re.sub(r"^\s*[-*+]\s", "", line)))
                elif line.strip():
                    out.append("<p>%s</p>" % html.escape(line))
            converted = "\n".join(out)
            return {"output": converted,
                    "data": {"converter": "builtin", "note": "basic Markdown subset only"}}
    # table normalisation
    rows = []
    for line in lines:
        if "|" in line and not re.match(r"^\s*\|?[\s:|-]+\|?\s*$", line):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    if not rows:
        raise core.InvalidInput("no markdown table rows found in the input")
    header, body = rows[0], rows[1:]
    return {"output": core.md_table_plain(body, header),
            "data": {"columns": len(header), "rows": len(body)}}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.headings = []
        self._skip = 0
        self._current_href = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "a":
            self._current_href = dict(attrs).get("href")
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("#" * int(tag[1]) + " ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag == "li":
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        text = data.strip()
        if text:
            self.parts.append(text)
            if re.match(r"^h[1-6]$", "") is None:
                pass

    def text(self):
        joined = "".join(self.parts)
        joined = re.sub(r"[ \t]+", " ", joined)
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        return joined.strip()


def html_editor(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    action = ctx.params["action"]
    if action in ("to-text", "outline"):
        parser = _TextExtractor()
        try:
            parser.feed(text)
            parser.close()
        except Exception as exc:  # HTMLParser is forgiving; guard anyway
            raise core.InvalidInput("could not parse HTML: %s" % exc)
        plain = parser.text()
        if action == "outline":
            headings = re.findall(r"^(#{1,6})\s+(.*)$", plain, re.M)
            out = "\n".join("%s%s" % ("  " * (len(h) - 1), t) for h, t in headings) or "no headings found"
            return {"output": out, "data": {"headings": headings, "mode": "outline"}}
        data = {"mode": "to-text", "chars": len(plain),
                "links": re.findall(r'href="([^"]+)"', text)[:50],
                "tags": len(re.findall(r"<[^>]+>", text))}
        if not ctx.params["keep_links"]:
            plain = re.sub(r"https?://\S+", "", plain).strip()
        return {"output": plain, "data": data}
    if action == "encode":
        encoded = "".join(ch if ord(ch) < 128 and ch not in "&<>\"'" else "&#%d;" % ord(ch) for ch in text)
        return {"output": encoded, "data": {"mode": "encode", "entities": encoded.count("&#")}}
    if action == "decode":
        decoded = html.unescape(text)
        return {"output": decoded, "data": {"mode": "decode",
                                           "changed": decoded != text}}
    escaped = html.escape(text, quote=True)
    return {"output": escaped, "data": {"mode": "escape"}}


def xml_specialist(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    action = ctx.params["action"]
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise core.InvalidInput("XML is not well formed: %s" % exc,
                                hint="Well-formedness errors report line and column numbers.")
    if action in ("parse", "validate"):
        count = sum(1 for _ in root.iter())
        data = {"root": root.tag, "elements": count,
                "attributes": len(root.attrib),
                "well_formed": True}
        return {"output": "well formed XML: root <%s>, %d element(s)" % (root.tag, count), "data": data}
    if action == "to-text":
        texts = [t.strip() for t in root.itertext() if t.strip()]
        return {"output": "\n".join(texts), "data": {"text_nodes": len(texts)}}
    if action == "escape":
        return {"output": html.escape(text), "data": {"mode": "escape"}}
    indent = int(ctx.params["indent"])
    _indent_tree(root, 0, indent)
    body = ET.tostring(root, encoding="unicode")
    return {"output": body, "data": {"mode": "pretty", "indent": indent}}


def _indent_tree(elem, level=0, spaces=2):
    pad = "\n" + " " * (spaces * level)
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = pad + " " * spaces
        for child in elem:
            _indent_tree(child, level + 1, spaces)
        if not child.tail or not child.tail.strip():
            child.tail = pad
        if not elem[-1].tail or not elem[-1].tail.strip():
            elem[-1].tail = pad


def json_editor(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    action = ctx.params["action"]
    indent = int(ctx.params["indent"])
    if action in ("validate", "to-jsonl", "to-json", "pretty", "minify"):
        is_jsonl = action == "to-jsonl" or (action == "validate" and text.lstrip().startswith("{")
                                            and "\n" in text.strip() and _looks_like_jsonl(text))
        if is_jsonl and action in ("validate", "to-json"):
            records = []
            for line_no, line in enumerate(text.strip().split("\n"), 1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise core.InvalidInput("JSONL line %d is invalid: %s" % (line_no, exc.msg),
                                            details={"line": line_no, "column": exc.colno})
            if action == "to-json":
                return {"output": json.dumps(records, indent=indent, ensure_ascii=False),
                        "data": {"records": len(records)}}
            keys = sorted({k for r in records if isinstance(r, dict) for k in r})
            return {"output": "valid JSONL: %d record(s), keys: %s" % (len(records), ", ".join(keys[:20])),
                    "data": {"records": len(records), "keys": keys}}
        data, exc = json_safe_load(text)
        if exc is not None:
            raise core.InvalidInput("invalid JSON: %s" % exc.msg,
                                    hint="Position line %d column %d." % (exc.lineno, exc.colno),
                                    details={"line": exc.lineno, "column": exc.colno})
        if action == "validate":
            return {"output": "valid JSON: %s" % _json_shape(data),
                    "data": {"valid": True, "shape": _json_shape(data)}}
        if action == "to-jsonl":
            records = data if isinstance(data, list) else [data]
            return {"output": "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                    "data": {"records": len(records)}}
        if action == "minify":
            return {"output": json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                    "data": {"mode": "minify"}}
        return {"output": json.dumps(data, indent=indent, ensure_ascii=False),
                "data": {"mode": "pretty", "indent": indent}}
    if action == "escape-strings":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"output": json.dumps(text, ensure_ascii=False)[1:-1],
                    "data": {"mode": "escape-strings", "treated_as": "raw text"}}
        escaped = json.dumps(data, ensure_ascii=True)
        return {"output": escaped, "data": {"mode": "escape-strings", "ascii_only": True}}
    unescaped = re.sub(r"\\(u[0-9a-fA-F]{4}|.)",
                       lambda m: chr(int(m.group(1)[1:], 16)) if m.group(1).startswith("u")
                       else {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f"}.get(m.group(1), m.group(1)),
                       text)
    return {"output": unescaped, "data": {"mode": "unescape"}}


def _looks_like_jsonl(text):
    lines = [ln for ln in text.strip().split("\n") if ln.strip()]
    if len(lines) < 2:
        return False
    try:
        for line in lines:
            json.loads(line)
        return True
    except json.JSONDecodeError:
        return False


def _json_shape(data):
    if isinstance(data, dict):
        return "object with %d key(s): %s" % (len(data), ", ".join(list(data)[:10]))
    if isinstance(data, list):
        kind = "array of %d item(s)" % len(data)
        if data and all(isinstance(x, dict) for x in data):
            keys = sorted({k for x in data for k in x})
            return kind + " with keys: " + ", ".join(keys[:10])
        return kind
    return type(data).__name__


def yaml_editor(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    action = ctx.params["action"]
    issues = []
    for idx, line in enumerate(core.canonical_newlines(text).split("\n"), 1):
        stripped = line.lstrip(" ")
        if line.startswith("\t") or " \t" in line[:len(line) - len(stripped)]:
            issues.append({"line": idx, "issue": "tab-indentation"})
        if stripped.count(":") > 1 and not stripped.startswith("#") and ":" in stripped:
            after = stripped.split(":", 1)[1]
            if ":" in after and not after.strip().startswith(("http", '"', "'")):
                issues.append({"line": idx, "issue": "unquoted-colon"})
        if line.rstrip() != line:
            issues.append({"line": idx, "issue": "trailing-space"})
    try:
        parsed = yaml_load(text)
        parse_error = None
    except YamlError as exc:
        parsed, parse_error = None, str(exc)
    if action == "lint":
        if parse_error:
            return {"output": "YAML parse error: %s" % parse_error,
                    "data": {"issues": issues, "parse_error": parse_error}}
        rows = [[i["line"], i["issue"]] for i in issues]
        return {"output": (core.table(rows, ["line", "issue"]) if rows else "yaml lint: no issues found"),
                "data": {"issues": issues, "parse_error": None}}
    if parse_error:
        raise core.InvalidInput("cannot convert YAML: %s" % parse_error)
    if action == "to-json":
        return {"output": json.dumps(parsed, indent=int(ctx.params["indent"]), ensure_ascii=False),
                "data": {"mode": "to-json"}}
    if action == "from-json":
        return {"output": yaml_dump(parsed, int(ctx.params["indent"])),
                "data": {"mode": "from-json"}}
    repaired = "\n".join(line.replace("\t", "  ").rstrip() for line in core.canonical_newlines(text).split("\n"))
    return {"output": repaired, "data": {"issues": issues, "repaired": True}}


def csv_specialist(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    action = ctx.params["action"]
    delimiter = ctx.params["delimiter"]
    if delimiter == "auto":
        delimiter = _sniff_delimiter(text)
    quotechar = ctx.params["quotechar"] or '"'
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter, quotechar=quotechar))
    if not rows:
        raise core.InvalidInput("no rows found")
    header = rows[0] if ctx.params["has_header"] else None
    width = len(rows[0])
    ragged = [idx + 1 for idx, row in enumerate(rows) if len(row) != width]
    if action == "sniff":
        return {"output": "delimiter=%r rows=%d columns=%d" % (delimiter, len(rows), width),
                "data": {"delimiter": delimiter, "rows": len(rows), "columns": width}}
    if action == "validate":
        data = {"rows": len(rows), "columns": width, "ragged_rows": ragged,
                "quoted_fields": text.count(quotechar) // 2}
        output = ("valid delimited text: %d row(s), %d column(s)" % (len(rows), width)
                  if not ragged else "ragged rows at line(s): %s" % ", ".join(map(str, ragged[:20])))
        return {"output": output, "data": data,
                "warnings": ["%d row(s) have a different column count" % len(ragged)] if ragged else []}
    if action == "table":
        return {"output": core.table(rows[1:] if header else rows, header or ["c%d" % i for i in range(width)]),
                "data": {"rows": len(rows) - (1 if header else 0), "columns": width}}
    if action == "to-json":
        records = []
        for row in (rows[1:] if header else rows):
            if header:
                records.append({header[i] if i < len(header) else "col%d" % i: row[i]
                                for i in range(len(row))})
            else:
                records.append(row)
        return {"output": json.dumps(records, indent=2, ensure_ascii=False),
                "data": {"records": len(records)}}
    out = io.StringIO()
    writer = csv.writer(out, delimiter=ctx.params["to_delimiter"] or "\t",
                        quotechar=quotechar, quoting=csv.QUOTE_MINIMAL)
    writer.writerows(rows)
    return {"output": out.getvalue(), "data": {"rows": len(rows), "from": repr(delimiter),
                                               "to": repr(ctx.params["to_delimiter"])}}


def _sniff_delimiter(text):
    sample = text[:4000]
    votes = {d: sample.count(d) for d in (",", ";", "\t", "|")}
    best = max(votes, key=votes.get)
    return best if votes[best] else ","


REGEX_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}


def regex_toolkit(ctx):
    pattern = ctx.params["pattern"].strip()
    if not pattern:
        raise core.InvalidInput("give --set pattern=<regex>")
    if len(pattern) > core.HARD_LIMITS["max_regex_pattern"]:
        raise core.InvalidParam("pattern longer than %d characters" % core.HARD_LIMITS["max_regex_pattern"])
    if re.search(r"(\([^)]*[*+][^)]*\)[*+])|(\[[^\]]*\][*+]\s*[*+])", pattern):
        ctx.warn("pattern may backtrack catastrophically; input size should stay small")
    flags = 0
    for letter in ctx.params["flags"]:
        flags |= REGEX_FLAGS.get(letter, 0)
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        raise core.InvalidParam("invalid regular expression: %s" % exc)
    action = ctx.params["action"]
    max_matches = int(ctx.params["max_matches"])
    text = ctx.text or ""
    if action == "explain":
        return {"output": explain_regex(pattern), "data": {"pattern": pattern,
                                                          "groups": compiled.groups}}
    if not text:
        raise core.InvalidInput("this action needs input text")
    if action == "search":
        matches = []
        for match in compiled.finditer(text):
            matches.append({"match": match.group(0), "start": match.start(), "end": match.end(),
                            "groups": list(match.groups())})
            if len(matches) >= max_matches:
                break
        rows = [[m["start"], m["end"], m["match"][:60], ", ".join(g or "" for g in m["groups"])[:40]]
                for m in matches]
        return {"output": (core.table(rows, ["start", "end", "match", "groups"]) if rows
                           else "no matches"),
                "data": {"count": len(matches), "matches": matches[:max_matches]}}
    if action == "extract":
        group = int(ctx.params["group"])
        values = [m.group(group) for m in compiled.finditer(text) if m.group(group) is not None]
        return {"output": "\n".join(values[:max_matches]),
                "data": {"count": len(values), "group": group, "values": values[:max_matches]}}
    if action == "replace":
        new, count = compiled.subn(ctx.params["replacement"], text, count=max_matches)
        return {"output": new, "data": {"replacements": count}}
    parts = compiled.split(text, maxsplit=max_matches)
    return {"output": "\n".join(parts[:max_matches]), "data": {"parts": len(parts)}}


def explain_regex(pattern):
    """Very small, honest regex explainer (token level, no semantic guessing)."""
    tokens = [
        (r"\\d", "a digit 0-9"), (r"\\w", "a word character"), (r"\\s", "a whitespace character"),
        (r"\\.", "a literal dot"), (r"\^", "start of line/string"), (r"\$", "end of line/string"),
        (r"\(\.\*\)", "capture: anything"), (r"\.\*", "anything (greedy)"),
        (r"\.\*?", "anything (lazy)"), (r"\.\+?", "one or more of anything"),
        (r"\[[^\]]+\]", "a character class"), (r"\([^)]*\)", "a capture group"),
        (r"[*+?]", "a quantifier"), (r"\\b", "a word boundary"),
    ]
    lines = []
    for token, meaning in tokens:
        hits = len(re.findall(token, pattern))
        if hits:
            lines.append("- `%s` x%d - %s" % (token, hits, meaning))
    if not lines:
        lines.append("- literal characters only")
    return "\n".join(lines)


def template_builder(ctx):
    template = ctx.require_text(minimum=1, allow_empty=True)
    context_text = ctx.params["context"] or "{}"
    try:
        context = json.loads(context_text) if context_text.strip().startswith("{") else yaml_load(context_text)
    except (json.JSONDecodeError, YamlError) as exc:
        raise core.InvalidInput("context must be a JSON or simple YAML object: %s" % exc)
    if not isinstance(context, dict):
        raise core.InvalidInput("context must be an object with named values")
    action = ctx.params["action"]
    variables = sorted(set(re.findall(r"\{\{\s*#?(?:each|if)?\s*([A-Za-z0-9_.]+)\s*\}\}", template)))
    variables = [v for v in variables if v not in ("each", "if")]
    if action == "variables":
        missing = [v for v in variables if v.split(".")[0] not in context]
        data = {"variables": variables, "missing": missing}
        return {"output": "\n".join("%s%s" % (v, "  (MISSING)" if v in missing else "") for v in variables)
                or "no variables", "data": data}
    if action == "validate":
        rendered = simple_template_render(template, context, strict=bool(ctx.params["strict"]))
        return {"output": rendered, "data": {"variables": variables, "valid": True}}
    rendered = simple_template_render(template, context, strict=bool(ctx.params["strict"]))
    return {"output": rendered, "data": {"variables": variables,
                                         "unresolved": re.findall(r"\{\{[^}]+\}\}", rendered)}}


def diff_patch(ctx):
    a = ctx.require_text(minimum=1, allow_empty=True)
    b = ctx.require_second_text()
    mode = ctx.params["mode"]
    context_lines = int(ctx.params["context_lines"])
    a_lines = core.canonical_newlines(a).split("\n")
    b_lines = core.canonical_newlines(b).split("\n")
    if mode == "unified":
        diff = list(difflib.unified_diff(a_lines, b_lines, fromfile="a/input.txt",
                                         tofile="b/revised.txt", lineterm="", n=context_lines))
        return {"output": "\n".join(diff) or "(no differences)",
                "data": {"changed_lines": sum(1 for d in diff if d[:1] in "+-" and not d.startswith(("+++", "---")))}}
    if mode == "word":
        sm = difflib.SequenceMatcher(None, a.split(), b.split(), autojunk=False)
        out = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                out.extend(a.split()[i1:i2])
            elif tag == "replace":
                out.append("[-%s-]{+%s+}" % (" ".join(a.split()[i1:i2]), " ".join(b.split()[j1:j2])))
            elif tag == "delete":
                out.append("[-%s-]" % " ".join(a.split()[i1:i2]))
            elif tag == "insert":
                out.append("{+%s+}" % " ".join(b.split()[j1:j2]))
        return {"output": " ".join(out), "data": {"similarity": round(sm.ratio(), 3)}}
    if mode == "stats":
        sm = difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False)
        added = deleted = changed = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "insert":
                added += j2 - j1
            elif tag == "delete":
                deleted += i2 - i1
            elif tag == "replace":
                changed += max(i2 - i1, j2 - j1)
        return {"output": "similarity %.3f | +%d -%d ~%d" % (sm.ratio(), added, deleted, changed),
                "data": {"similarity": round(sm.ratio(), 3), "added": added,
                         "deleted": deleted, "changed": changed}}
    # apply: treat the first text as the patch payload applied to the second text
    patch_text = a
    target = b
    if not patch_text.lstrip().startswith(("---", "@@")):
        raise core.InvalidInput("apply mode expects a unified diff as the first input",
                               hint="Pass the diff with --text and the target with --text2.")
    try:
        result = _apply_patch(target, patch_text)
    except ValueError as exc:
        raise core.InvalidInput("patch did not apply cleanly: %s" % exc,
                                hint="Regenerate the diff against the current target file.")
    return {"output": result, "data": {"applied": True}}


def _apply_patch(target, patch_text):
    lines = core.canonical_newlines(target).split("\n")
    offset = 0
    for hunk in re.split(r"(?m)^(?=@@ )", patch_text):
        header = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", hunk.strip().split("\n")[0])
        if not header:
            continue
        old_start = int(header.group(1)) - 1
        body = [ln for ln in hunk.split("\n")[1:]] if hunk.strip() else []
        removes = [ln[1:] for ln in body if ln.startswith("-")]
        adds = [ln[1:] for ln in body if ln.startswith("+")]
        target_slice = lines[old_start + offset: old_start + offset + len(removes)]
        if target_slice != removes:
            raise ValueError("context mismatch at original line %d" % (old_start + 1))
        lines[old_start + offset: old_start + offset + len(removes)] = adds
        offset += len(adds) - len(removes)
    return "\n".join(lines)


def schema_to_text(ctx):
    text = ctx.require_text(minimum=2)
    data, exc = json_safe_load(text)
    if exc is not None:
        raise core.InvalidInput("input must be a JSON Schema object: %s" % exc.msg)
    if not isinstance(data, dict):
        raise core.InvalidInput("JSON Schema must be an object")
    properties = data.get("properties") or {}
    required = set(data.get("required") or [])
    fmt = ctx.params["format"]
    rows = []
    for name, spec in properties.items():
        rows.append([name, spec.get("type", "any"),
                     "yes" if name in required else "no",
                     (spec.get("description") or "")[:80],
                     json.dumps(spec.get("enum")) if spec.get("enum") else ""])
    if fmt == "json-table":
        return {"output": json.dumps([dict(zip(["field", "type", "required", "description", "enum"], r)) for r in rows],
                                     indent=2),
                "data": {"fields": len(rows)}}
    if fmt == "html-form":
        out = ["<form>"]
        for name, typ, req, desc, _enum in rows:
            input_type = {"string": "text", "integer": "number", "number": "number",
                          "boolean": "checkbox", "array": "text", "object": "text"}.get(typ, "text")
            out.append('  <label for="%s">%s%s</label>' % (name, name, " *" if req == "yes" else ""))
            out.append('  <input id="%s" name="%s" type="%s">' % (name, name, input_type))
        out.append("</form>")
        return {"output": "\n".join(out), "data": {"fields": len(rows)}}
    if fmt == "plantuml":
        out = ["@startuml", "class %s {" % (data.get("title") or "Schema")]
        for name, typ, req, _desc, _e in rows:
            out.append("  %s : %s%s" % (name, typ, "" if req == "yes" else "?"))
        out.append("}", "@enduml")
        return {"output": "\n".join(out), "data": {"fields": len(rows)}}
    if fmt == "typed-dict":
        mapping = {"string": "str", "integer": "int", "number": "float", "boolean": "bool",
                   "array": "list", "object": "dict"}
        lines = ["from typing import TypedDict", "", "class Record(TypedDict):"]
        for name, typ, req, _d, _e in rows:
            base = mapping.get(typ, "object")
            lines.append("    %s: %s%s" % (name, base, "" if req == "yes" else "  # optional"))
        return {"output": "\n".join(lines), "data": {"fields": len(rows)}}
    output = core.md_table_plain(rows, ["field", "type", "required", "description", "enum"])
    return {"output": output, "data": {"fields": len(rows), "title": data.get("title")}}


def frontmatter_editor(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", core.canonical_newlines(text), re.S)
    if not match:
        raise core.InvalidInput("no YAML front matter found",
                                hint="Front matter must start with a '---' line and end with a '---' line.")
    raw_meta, body = match.group(1), match.group(2)
    try:
        meta = yaml_load(raw_meta) or {}
    except YamlError as exc:
        raise core.InvalidInput("front matter is not valid YAML: %s" % exc)
    action = ctx.params["action"]
    if action == "to-json":
        return {"output": json.dumps(meta, indent=2, ensure_ascii=False),
                "data": {"keys": sorted(meta)}}
    if action == "read":
        rows = [[k, json.dumps(v, ensure_ascii=False)] for k, v in meta.items()]
        return {"output": core.table(rows, ["key", "value"]) + "\n\nbody chars: %d" % len(body),
                "data": {"meta": meta, "body_chars": len(body)}}
    key = ctx.params["key"].strip()
    if not key:
        raise core.InvalidInput("give --set key=<front matter key>")
    if action == "set":
        value = ctx.params["value"]
        meta[key] = _scalar(value)
        return {"output": "---\n%s\n---\n%s" % (yaml_dump(meta), body),
                "data": {"key": key, "value": meta[key]}}
    meta.pop(key, None)
    return {"output": "---\n%s\n---\n%s" % (yaml_dump(meta), body), "data": {"removed": key}}


def roundtrip_guardian(ctx):
    text = ctx.require_text(minimum=2)
    target = ctx.params["to"]
    data, exc = json_safe_load(text)
    if exc is not None:
        raise core.InvalidInput("round-trip guardian needs JSON input: %s" % exc.msg)
    if target == "json":
        rendered = json.dumps(data, indent=2, ensure_ascii=False)
        back = json.loads(rendered)
    elif target == "yaml":
        rendered = yaml_dump(data)
        back = yaml_load(rendered)
    elif target == "csv":
        if not (isinstance(data, list) and data and isinstance(data[0], dict)):
            raise core.InvalidInput("CSV conversion needs a non-empty array of objects")
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=sorted({k for row in data for k in row}))
        writer.writeheader()
        writer.writerows(data)
        rendered = out.getvalue()
        back = list(csv.DictReader(io.StringIO(rendered)))
    else:
        if not (isinstance(data, list) and data and isinstance(data[0], dict)):
            raise core.InvalidInput("markdown table conversion needs an array of objects")
        headers = sorted({k for row in data for k in row})
        rendered = core.md_table_plain([[json.dumps(row.get(h, ""), ensure_ascii=False) for h in headers] for row in data], headers)
        back = [dict(zip(headers, [c.strip() for c in line.strip().strip("|").split("|")]))
                for line in rendered.split("\n")[2:]]
    equal = back == data or all(
        str(back[k]) == str(data[k]) for k in data) if isinstance(data, dict) else back == data
    data_out = {"target": target, "lossless": bool(equal),
                "input_type": type(data).__name__,
                "rendered_chars": len(rendered)}
    if not equal:
        data_out["differences"] = _first_differences(data, back)
    return {"output": rendered, "data": data_out,
            "warnings": [] if equal else ["round trip is lossy: %d difference(s)" % len(data_out.get("differences", []))]}


def _first_differences(a, b, path="", out=None):
    out = out if out is not None else []
    if len(out) >= 10:
        return out
    if type(a) is not type(b):
        out.append({"path": path or "$", "before": repr(a)[:60], "after": repr(b)[:60]})
    elif isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append({"path": "%s.%s" % (path, key), "before": repr(a.get(key))[:60],
                            "after": repr(b.get(key))[:60]})
            else:
                _first_differences(a[key], b[key], "%s.%s" % (path, key), out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append({"path": path, "before": "%d items" % len(a), "after": "%d items" % len(b)})
        for idx, (x, y) in enumerate(zip(a, b)):
            _first_differences(x, y, "%s[%d]" % (path, idx), out)
    elif a != b:
        out.append({"path": path or "$", "before": repr(a)[:60], "after": repr(b)[:60]})
    return out


# --------------------------------------------------------------------------
# code text
# --------------------------------------------------------------------------


def code_documentation(ctx):
    text = ctx.require_text(minimum=5)
    language = ctx.params["language"]
    style = ctx.params["style"]
    action = ctx.params["action"]
    if language == "python":
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            raise core.InvalidInput("Python source does not parse: %s" % exc.msg,
                                    details={"line": exc.lineno})
        items = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                args = []
                if not isinstance(node, ast.ClassDef):
                    args = [a.arg for a in node.args.args]
                items.append({
                    "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                    "name": node.name, "line": node.lineno, "args": args,
                    "docstring": ast.get_docstring(node) or "",
                    "returns": getattr(getattr(node, "returns", None), "id", None),
                })
        if action == "extract":
            out = json.dumps(items, indent=2, ensure_ascii=False)
            return {"output": out, "data": {"symbols": len(items), "items": items}}
        blocks = []
        for item in items:
            if item["docstring"]:
                blocks.append("%s %s\n%s" % (item["kind"], item["name"],
                                             textwrap.indent(item["docstring"], "    ")))
                continue
            sig = "%s(%s)" % (item["name"], ", ".join(item["args"]))
            if style == "numpy":
                doc = "%s\n%s\n\nParameters\n----------\n%s\n\nReturns\n-------\n%s" % (
                    sig, " " * len(sig), "\n".join("%s : type\n    description" % a for a in item["args"]) or "none",
                    "type\n    description")
            elif style == "google":
                doc = "%s.\n\nArgs:\n%s\n\nReturns:\n    description" % (
                    sig, "\n".join("    %s: description" % a for a in item["args"]) or "    none")
            elif style == "jsdoc":
                doc = "/**\n%s\n */" % "\n".join(
                    [" * %s" % sig] + [" * @param {type} %s - description" % a for a in item["args"]])
            else:
                doc = '"""%s."""' % sig
            blocks.append("# %s %s\n%s" % (item["kind"], item["name"], doc))
        return {"output": "\n\n".join(blocks), "data": {"symbols": len(items), "style": style}}
    # generic languages: regex-based symbol extraction
    pattern = r"(?:def|function|func|fn|class|public\s+\w+|private\s+\w+)\s+([A-Za-z_][A-Za-z0-9_]*)"
    symbols = [{"name": m.group(1), "line": text[:m.start()].count("\n") + 1}
               for m in re.finditer(pattern, text)]
    if action == "extract":
        return {"output": json.dumps(symbols, indent=2), "data": {"symbols": len(symbols)}}
    blocks = ['/**\n * %s\n * @returns {type} description\n */' % s["name"] for s in symbols]
    return {"output": "\n".join(blocks) or "no symbols detected",
            "data": {"symbols": len(symbols), "language": language}}


def identifier_naming(ctx):
    text = ctx.require_text(minimum=1)
    language = ctx.params["language"]
    case = ctx.params["case"]
    reserved = RESERVED_WORDS.get(language, set())
    suggestions = []
    for raw in re.split(r"[\n,]+", text):
        phrase = raw.strip()
        if not phrase:
            continue
        converted = case_convert(phrase, case)
        valid = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*|[\w.-]+", converted) is not None
        suggestions.append({
            "input": phrase, "identifier": converted, "case": case,
            "reserved_word": converted.lower() in reserved,
            "valid_identifier": valid and not converted.lower() in reserved,
            "length": len(converted),
        })
    rows = [[s["input"][:40], s["identifier"], "yes" if s["reserved_word"] else "",
             "yes" if s["valid_identifier"] else "no", s["length"]] for s in suggestions]
    warnings = []
    if any(s["reserved_word"] for s in suggestions):
        warnings.append("some suggestions collide with %s reserved words" % language)
    output = "\n".join(s["identifier"] for s in suggestions) if len(suggestions) == 1 else \
        core.table(rows, ["input", "identifier", "reserved", "valid", "len"])
    return {"output": output, "data": {"suggestions": suggestions, "language": language,
                                       "reserved_count": len(reserved)},
            "warnings": warnings}


def string_escaper(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    language = ctx.params["language"]
    direction = ctx.params["direction"]
    quote = '"' if ctx.params["quote"] == "double" else "'"
    if direction == "unescape":
        if language in ("json",):
            try:
                out = json.loads('"%s"' % text.replace('"', '\\"')) if not text.startswith('"') else json.loads(text)
            except json.JSONDecodeError:
                raise core.InvalidInput("not a valid JSON string literal")
        elif language == "python":
            out = text.encode("utf-8").decode("unicode_escape")
        elif language == "html":
            out = html.unescape(text)
        elif language == "sql":
            out = text.replace("''", "'")
        elif language == "shell":
            out = re.sub(r"'\\''", "'", text.strip("'"))
        elif language == "csv":
            out = next(csv.reader(io.StringIO(text)))[0]
        elif language == "regex":
            out = re.sub(r"\\(.)", r"\1", text)
        else:
            out = re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), text)
        return {"output": out, "data": {"language": language, "direction": "unescape"}}
    if language == "python":
        inner = text.encode("unicode_escape").decode("ascii").replace('"', '\\"')
        out = "%s%s%s" % (quote, inner, quote)
    elif language in ("json", "javascript"):
        out = json.dumps(text, ensure_ascii=False)
        if quote == "'" and language == "javascript":
            out = "'" + out[1:-1].replace("'", "\\'").replace('\\"', '"') + "'"
    elif language == "c":
        out = '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
    elif language == "shell":
        out = "'" + text.replace("'", "'\\''") + "'"
    elif language == "sql":
        out = "'" + text.replace("'", "''") + "'"
    elif language == "html":
        out = html.escape(text, quote=True)
    elif language == "csv":
        buf = io.StringIO()
        csv.writer(buf, quoting=csv.QUOTE_MINIMAL).writerow([text])
        out = buf.getvalue().rstrip("\r\n")
    else:  # regex
        out = re.escape(text)
    return {"output": out, "data": {"language": language, "direction": "escape",
                                    "quoted": language != "regex",
                                    "length": len(out)}}


PROMPT_ANTIPATTERNS = [
    ("vague-quantity", r"\b(some|several|many|a few|various)\b", "replace with an explicit number or range"),
    ("unbounded-request", r"\b(etc\.?|and so on|such as)\b", "list the required items explicitly"),
    ("conflicting-scope", r"\b(always|never)\b", "check for contradictions with other rules"),
    ("ambiguous-pronoun", r"\b(it|this|that)\b(?!\s+\w)", "name the referent explicitly"),
    ("double-negative-rule", r"\b(do not|don't|never)\b.*\b(not|avoid|without)\b", "rephrase as a positive rule"),
    ("no-output-contract", r"\b(output|return|respond)\b", "state the exact output shape"),
]


def prompt_editor(ctx):
    text = ctx.require_text(minimum=10)
    action = ctx.params["action"]
    target = ctx.params["target"]
    findings = []
    for name, pattern, advice in PROMPT_ANTIPATTERNS:
        for match in re.finditer(pattern, text, re.I):
            findings.append({"kind": name, "match": match.group(0),
                             "offset": match.start(), "advice": advice})
    has_role = bool(re.search(r"\byou are\b|\brole\b", text, re.I))
    has_format = bool(re.search(r"\bformat\b|\bjson\b|\bmarkdown\b|\breturn\b", text, re.I))
    has_length = bool(re.search(r"\b(words|characters|sentences|bullets)\b", text, re.I))
    data = {"findings": findings, "count": len(findings),
            "has_role": has_role, "has_output_contract": has_format, "has_length_limit": has_length}
    if action == "lint":
        rows = [[f["kind"], f["match"][:30], f["advice"]] for f in findings]
        output = core.table(rows, ["kind", "match", "advice"]) if rows else "no prompt antipatterns found"
        output += "\n\nrole: %s | output contract: %s | length limit: %s" % (
            "yes" if has_role else "MISSING", "yes" if has_format else "MISSING",
            "yes" if has_length else "MISSING")
        return {"output": output, "data": data}
    scaffold = """ROLE
You are <role> working for <audience>.

TASK
<one sentence describing the single job this %s prompt performs>

INPUT
<what the prompt receives, with field names>

CONSTRAINTS
- <hard requirement, measurable>
- <forbidden behaviour>
- <length / format limits>

OUTPUT CONTRACT
<exact shape: schema, headings, or field list>

CHECKS
- <how the reader can verify the output>

ORIGINAL PROMPT (preserved for reference)
%s""" % (target, text)
    return {"output": scaffold, "data": data,
            "warnings": ["%d antipattern(s) found in the supplied prompt" % len(findings)] if findings else []}


def dsl_author(ctx):
    text = ctx.text or ""
    action = ctx.params["action"]
    start = ctx.params["start"] or "document"
    if action == "scaffold":
        grammar = """(* EBNF scaffold for a %s grammar *)
%s = { statement } ;
statement = assignment | expression | comment ;
assignment = identifier , "=" , expression ;
expression = term , { ( "+" | "-" ) , term } ;
term = factor , { ( "*" | "/" ) , factor } ;
factor = number | identifier | "(" , expression , ")" ;
comment = "(*" , { character } , "*)" ;
identifier = letter , { letter | digit | "_" } ;
number = digit , { digit } , [ "." , digit , { digit } ] ;
letter = "A" | "B" | ... | "z" ;
digit = "0" | "1" | ... | "9" ;""" % (start, start)
        if action == "railroad":
            pass
        return {"output": grammar, "data": {"start_symbol": start, "rules": 9}}
    if not text.strip():
        raise core.InvalidInput("DSL validation needs the grammar text as input")
    if action == "railroad":
        rules = re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*;$", text, re.M)
        out = []
        for name, body in rules:
            out.append("%s" % name)
            parts = [p.strip() for p in re.split(r"\|\s*", body)]
            for part in parts:
                out.append("  ├── %s" % part[:100])
        return {"output": "\n".join(out) or "no EBNF rules found",
                "data": {"rules": len(rules)}}
    # validate
    problems = []
    defined = set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", text, re.M))
    referenced = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", text))
    keywords = {"EBNF"}
    undefined = {r for r in referenced - defined - keywords
                 if r.islower() and len(r) > 1 and r not in ("letter", "digit")}
    for name in sorted(undefined)[:10]:
        problems.append({"kind": "possibly-undefined-rule", "name": name})
    if not re.search(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=", text, re.M):
        problems.append({"kind": "no-rules-found"})
    for match in re.finditer(r"(['\"])(.*?)\1", text):
        if match.group(2) == "":
            problems.append({"kind": "empty-terminal", "offset": match.start()})
    data = {"rules": len(defined), "problems": problems}
    output = ("grammar parsed: %d rule(s)%s" % (len(defined),
              "" if not problems else "; %d problem(s): %s" % (len(problems), ", ".join(p["kind"] for p in problems))))
    return {"output": output, "data": data,
            "warnings": ["%d grammar problem(s) found" % len(problems)] if problems else []}


def prose_to_spec(ctx):
    text = ctx.require_text(minimum=40)
    format = ctx.params["format"]
    musts = []
    for sentence in core.sentence_split(text):
        if re.search(r"\b(must|shall|should|needs? to|required|will)\b", sentence, re.I):
            musts.append(sentence.strip())
    nouns = sorted({w for w in core.word_tokens(text) if w[:1].isupper() and len(w) > 2})
    verbs = sorted({w.lower() for w in core.word_tokens(text)
                    if w.lower() in {"create", "update", "delete", "read", "send", "store",
                                     "validate", "calculate", "return", "fetch", "list", "render"}})
    if format == "gherkin":
        out = ["Feature: %s" % (text.split(".")[0][:70] or "Derived feature"), ""]
        for idx, must in enumerate(musts, 1):
            out.append("  Scenario: requirement %d" % idx)
            out.append("    Given the system is configured")
            out.append("    When %s" % re.sub(r"^(the system\s+)", "", must.rstrip(".")[:90]).lower())
            out.append("    Then the expected outcome is observable")
            out.append("")
        return {"output": "\n".join(out), "data": {"requirements": len(musts)}}
    if format == "json":
        spec = {"inputs": nouns[:10], "actions": verbs, "requirements": musts,
                "language": ctx.params["language"],
                "tests_required": bool(ctx.params["include_tests"])}
        return {"output": json.dumps(spec, indent=2, ensure_ascii=False),
                "data": {"requirements": len(musts)}}
    if format == "pseudocode":
        out = ["PROCEDURE main(input):"]
        for must in musts:
            out.append("    # %s" % must.rstrip(".")[:100])
            out.append("    result <- step()")
        out.append("    RETURN result")
        return {"output": "\n".join(out), "data": {"requirements": len(musts)}}
    lines = ["# Specification: derived from prose", "", "## Requirements"]
    lines.extend("- %s" % m for m in musts) if musts else lines.append("- (none detected)")
    lines += ["", "## Data mentioned", ", ".join(nouns[:20]) or "(none)", "",
              "## Actions", ", ".join(verbs) or "(none)", "",
              "## Test plan"]
    if ctx.params["include_tests"]:
        lines.extend("- Given/When/Then for each requirement above" for _ in musts[:5])
    else:
        lines.append("- not requested")
    lines += ["", "## Open questions", "- Confirm acceptance criteria for each requirement.",
              "- Confirm error handling and limits."]
    return {"output": "\n".join(lines), "data": {"requirements": len(musts), "nouns": nouns[:20]}}


def cli_composer(ctx):
    action = ctx.params["action"]
    shell = ctx.params["shell"]
    if action == "quote":
        text = ctx.require_text(minimum=1, allow_empty=True)
        return {"output": shell_quote(text, shell), "data": {"shell": shell}}
    if action == "parse":
        text = ctx.require_text(minimum=1)
        tokens = shell_split(text, shell)
        return {"output": json.dumps(tokens, indent=2), "data": {"tokens": tokens}}
    command = ctx.params["command"].strip()
    if not command:
        raise core.InvalidInput("give --set command=<program> (plus --set args=a,b,c)")
    args = [shell_quote(a, shell) for a in ctx.params["args"]]
    composed = " ".join([command] + args)
    if not ctx.params["dry_run"]:
        ctx.gate("compose a runnable command line")
    return {"output": composed,
            "data": {"shell": shell, "executed": False,
                     "note": "the Skill Tool only composes text; it never executes commands"}}


def shell_quote(text, shell="bash"):
    if shell in ("powershell",):
        return "'" + text.replace("'", "''") + "'"
    if shell == "cmd":
        return '"%s"' % text.replace('"', '""')
    if re.fullmatch(r"[A-Za-z0-9_./:=@%+-]+", text or ""):
        return text
    return "'" + text.replace("'", "'\\''") + "'"


def shell_split(text, shell="bash"):
    import shlex
    try:
        return shlex.split(text, posix=shell not in ("cmd", "powershell"))
    except ValueError as exc:
        raise core.InvalidInput("cannot parse command line: %s" % exc)


FIRST_NAMES = ["Ada", "Grace", "Alan", "Edsger", "Barbara", "Ken", "Dennis", "Margaret",
               "Linus", "Radia", "Tim", "Anita", "Yuki", "Kwame", "Sofia", "Ravi", "Nora",
               "Diego", "Leila", "Sven", "Mei", "Omar", "Priya", "Jonas"]
LAST_NAMES = ["Lovelace", "Hopper", "Turing", "Dijkstra", "Liskov", "Thompson", "Ritchie",
              "Hamilton", "Torvalds", "Perlman", "Berners-Lee", "Borg", "Tanaka", "Mensah",
              "Russo", "Iyer", "Nakamura", "Alvarez", "Haddad", "Lindqvist", "Chen", "Farah",
              "Nair", "Weber"]
DOMAINS = ["example.com", "example.org", "test.invalid", "sample.io"]


def test_data(ctx):
    kind = ctx.params["kind"]
    count = int(ctx.params["count"])
    rng = core.seeded_rng(ctx.params["seed"])
    schema_text = ctx.params["schema"]
    rows = []
    if kind in ("names", "emails", "records", "json"):
        for idx in range(count):
            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            rows.append({"id": idx + 1, "first_name": first, "last_name": last,
                         "email": "%s.%s@%s" % (first.lower(), last.lower(),
                                                rng.choice(DOMAINS)),
                         "created": "20%02d-%02d-%02d" % (rng.next_int(20) + 5, rng.next_int(12) + 1, rng.next_int(28) + 1)})
    elif kind == "uuid":
        for _ in range(count):
            hexes = "".join("%x" % rng.next_int(16) for _ in range(32))
            rows.append("%s-%s-4%s-%s%s-%s" % (hexes[:8], hexes[8:12], hexes[13:16],
                                               "89ab"[rng.next_int(4)], hexes[17:20], hexes[20:32]))
    elif kind == "dates":
        for _ in range(count):
            rows.append("20%02d-%02d-%02dT%02d:%02d:%02dZ" % (
                rng.next_int(20) + 5, rng.next_int(12) + 1, rng.next_int(28) + 1,
                rng.next_int(24), rng.next_int(60), rng.next_int(60)))
    elif kind == "text":
        words = ["alpha", "beta", "gamma", "delta", "report", "client", "review", "draft",
                 "summary", "budget", "queue", "release", "check", "update"]
        for _ in range(count):
            rows.append(" ".join(rng.choice(words) for _ in range(6 + rng.next_int(10))).capitalize() + ".")
    else:  # json with schema
        if schema_text:
            try:
                schema = json.loads(schema_text)
            except json.JSONDecodeError as exc:
                raise core.InvalidInput("--set schema must be JSON: %s" % exc.msg)
            props = schema.get("properties", {})
            for idx in range(count):
                record = {}
                for name, spec in props.items():
                    typ = spec.get("type", "string")
                    record[name] = {"string": "%s_%d" % (name, idx + 1),
                                    "integer": idx + 1, "number": round(rng.next_int(1000) / 10, 2),
                                    "boolean": bool(rng.next_int(2)),
                                    "array": [], "object": {}}.get(typ, None)
                rows.append(record)
        else:
            rows = [{"id": i + 1} for i in range(count)]
    if kind in ("records", "json"):
        output = json.dumps({"records": rows, "seed": ctx.params["seed"]}, indent=2, ensure_ascii=False)
    elif kind == "uuid" or kind == "dates" or kind == "text":
        output = "\n".join(str(r) for r in rows)
    else:
        output = json.dumps(rows, indent=2, ensure_ascii=False)
    return {"output": output,
            "data": {"kind": kind, "count": len(rows), "seed": ctx.params["seed"],
                     "deterministic": True}}


def machine_formatter(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    fmt = ctx.params["format"]
    action = ctx.params["action"]
    detected = fmt
    if fmt == "auto":
        stripped = text.strip()
        if stripped.startswith(("{", "[")) and _looks_like_jsonl(text) is False:
            detected = "json"
        elif stripped.startswith("<"):
            detected = "xml"
        elif re.search(r"^\s*\w+=[^\s]+", stripped, re.M):
            detected = "logfmt"
        elif "\t" in text:
            detected = "tsv"
        elif "," in stripped.split("\n")[0] and len(stripped.split("\n")[0].split(",")) > 1:
            detected = "csv"
        else:
            detected = "text"
    if action == "detect":
        return {"output": detected, "data": {"format": detected}}
    if action == "validate":
        if detected == "json":
            is_jsonl = _looks_like_jsonl(text)
            if is_jsonl:
                bad = []
                for idx, line in enumerate(text.strip().split("\n"), 1):
                    try:
                        json.loads(line)
                    except json.JSONDecodeError as exc:
                        bad.append({"line": idx, "error": exc.msg})
                return {"output": "valid JSONL" if not bad else "invalid JSONL",
                        "data": {"format": "jsonl", "errors": bad},
                        "warnings": ["%d invalid line(s)" % len(bad)] if bad else []}
            data, exc = json_safe_load(text)
            if exc:
                raise core.InvalidInput("invalid JSON: %s" % exc.msg)
            return {"output": "valid JSON: %s" % _json_shape(data), "data": {"format": "json"}}
        if detected in ("csv", "tsv"):
            rows = list(csv.reader(io.StringIO(text), delimiter="\t" if detected == "tsv" else ","))
            widths = {len(r) for r in rows}
            return {"output": "valid %s: %d row(s)" % (detected, len(rows)),
                    "data": {"format": detected, "rows": len(rows), "column_counts": sorted(widths)},
                    "warnings": [] if len(widths) <= 1 else ["inconsistent column counts: %s" % sorted(widths)]}
        return {"output": "no structural validation available for %r" % detected,
                "data": {"format": detected}}
    if detected == "json":
        if _looks_like_jsonl(text):
            records = [json.loads(line) for line in text.strip().split("\n") if line.strip()]
            return {"output": "[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n]",
                    "data": {"converted": "jsonl->json", "records": len(records)}}
        data, exc = json_safe_load(text)
        if exc:
            raise core.InvalidInput("invalid JSON: %s" % exc.msg)
        return {"output": json.dumps(data, indent=2, ensure_ascii=False),
                "data": {"converted": "json pretty"}}
    if detected in ("csv", "tsv"):
        delimiter = "\t" if detected == "tsv" else ","
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        header = rows[0] if rows else []
        return {"output": core.table(rows[1:], header or ["col%d" % i for i in range(len(header))]),
                "data": {"converted": "%s->table" % detected, "rows": len(rows)}}
    if detected == "logfmt":
        pairs = []
        for line in text.strip().split("\n"):
            record = dict(re.findall(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)', line))
            pairs.append({k: v.strip('"') for k, v in record.items()})
        return {"output": json.dumps(pairs, indent=2, ensure_ascii=False),
                "data": {"converted": "logfmt->json", "records": len(pairs)}}
    return {"output": text, "data": {"converted": "none", "format": detected}}


HANDLERS = {
    "structured-formats.markdown-author-and-repairer": markdown_repair,
    "structured-formats.html-text-and-entity-editor": html_editor,
    "structured-formats.xml-and-sgml-text-specialist": xml_specialist,
    "structured-formats.json-string-and-jsonl-editor": json_editor,
    "structured-formats.yaml-text-safety-editor": yaml_editor,
    "structured-formats.csv-and-delimited-text-specialist": csv_specialist,
    "structured-formats.regular-expression-text-toolkit": regex_toolkit,
    "structured-formats.template-language-text-builder": template_builder,
    "structured-formats.diff-and-patch-text-creator": diff_patch,
    "structured-formats.schema-to-text-generator": schema_to_text,
    "structured-formats.frontmatter-and-metadata-editor": frontmatter_editor,
    "structured-formats.structured-text-round-trip-guardian": roundtrip_guardian,
    "code-text.code-comment-and-documentation-writer": code_documentation,
    "code-text.identifier-naming-assistant": identifier_naming,
    "code-text.string-literal-escaper": string_escaper,
    "code-text.prompt-and-system-instruction-editor": prompt_editor,
    "code-text.domain-specific-language-author": dsl_author,
    "code-text.prose-to-code-specification-builder": prose_to_spec,
    "code-text.command-line-text-composer": cli_composer,
    "code-text.test-data-and-fixture-text-maker": test_data,
    "code-text.machine-output-formatter": machine_formatter,
}
