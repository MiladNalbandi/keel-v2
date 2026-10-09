"""A small YAML reader for keel-plugin.yml and the marketplace files (this tool uses the standard library only).

It reads the YAML these files use: block mappings and lists, flow mappings and lists ({ a: x, b: [y, z] }), plain,
'single' and "double" quoted text, | and > blocks, comments. Plain words become what PyYAML's safe_load makes of them
(YAML 1.1: true/false/yes/no/on/off, null/~, whole numbers, numbers with a point), except dates, which stay text.
Anchors, aliases, tags and more than one document are refused with a clear message.
"""

from __future__ import annotations

import re

from . import PluginToolError


class YamlError(PluginToolError):
    """The text is not YAML this reader understands; the message names the line."""


_NULL = {"", "~", "null", "Null", "NULL"}
_TRUE = {"true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"}
_FALSE = {"false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"}
_INT = re.compile(r"^[-+]?(0|[1-9][0-9_]*)$")
_HEX = re.compile(r"^[-+]?0x[0-9a-fA-F_]+$")
_OCT = re.compile(r"^[-+]?0[0-7_]+$")
_FLOAT = re.compile(r"^[-+]?([0-9][0-9_]*)?\.[0-9_]*([eE][-+][0-9]+)?$")
_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
            "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0"}


def plain(word: str):
    """A plain (unquoted) word as YAML 1.1 reads it."""
    if word in _NULL:
        return None
    if word in _TRUE:
        return True
    if word in _FALSE:
        return False
    if _INT.match(word):
        return int(word.replace("_", ""))
    if _HEX.match(word):
        return int(word.replace("_", ""), 16)
    if _OCT.match(word):
        return int(word.replace("_", ""), 8)
    if _FLOAT.match(word) and any(c.isdigit() for c in word):
        return float(word.replace("_", ""))
    if word in (".inf", ".Inf", ".INF", "+.inf"):
        return float("inf")
    if word in ("-.inf", "-.Inf", "-.INF"):
        return float("-inf")
    if word in (".nan", ".NaN", ".NAN"):
        return float("nan")
    return word


class _Line:
    __slots__ = ("indent", "no", "text")

    def __init__(self, no: int, indent: int, text: str):
        self.no, self.indent, self.text = no, indent, text


def _strip_comment(text: str) -> str:
    """The line without its comment: a # at the start or after a space, outside quotes."""
    quote = ""
    prev = " "
    last_sig = ""   # the last character that was not a space, outside quotes
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if quote == "'" and c == "'" and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            if quote == '"' and c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"" and (last_sig in ("", ":", "-", "[", "{", ",", "?") and prev in " [{,"):
            quote = c
        elif c == "#" and prev in " \t":
            return text[:i].rstrip()
        if not quote and not c.isspace():
            last_sig = c
        prev = c
        i += 1
    return text.rstrip()


class _Reader:
    def __init__(self, text: str):
        self.raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.lines: list[_Line] = []
        started = False
        for no, line in enumerate(self.raw, start=1):
            body = line.lstrip(" ")
            if body.startswith("\t") and body.strip():
                raise YamlError(f"line {no}: use spaces, not tabs, to indent")
            stripped = _strip_comment(body) if not body.startswith("#") else ""
            if not stripped.strip():
                self.lines.append(_Line(no, -1, ""))   # blank or comment only
                continue
            if stripped.startswith("%"):
                raise YamlError(f"line {no}: YAML directives (%) are not read")
            if stripped in ("---", "...") or stripped.startswith("--- "):
                if started and stripped.startswith("---"):
                    raise YamlError(f"line {no}: only one YAML document is read")
                if stripped.startswith("--- ") and stripped[4:].strip():
                    raise YamlError(f"line {no}: write the document under '---', not on its line")
                self.lines.append(_Line(no, -1, ""))
                continue
            started = True
            self.lines.append(_Line(no, len(line) - len(body), stripped))

    # ---- navigation over lines with content
    def next_content(self, i: int) -> int:
        while i < len(self.lines) and self.lines[i].indent < 0:
            i += 1
        return i

    def at(self, i: int) -> _Line | None:
        return self.lines[i] if i < len(self.lines) else None

    # ---- the document
    def document(self):
        i = self.next_content(0)
        if i >= len(self.lines):
            return None
        value, i = self.block(i, self.lines[i].indent)
        i = self.next_content(i)
        if i < len(self.lines):
            ln = self.lines[i]
            raise YamlError(f"line {ln.no}: this line does not fit the indentation above it")
        return value

    def block(self, i: int, indent: int):
        ln = self.lines[i]
        if _is_item(ln.text):
            return self.sequence(i, indent)
        if _key_split(ln.text, ln.no) is not None:
            return self.mapping(i, indent)
        value, i = self.inline(i, ln.text, indent - 1)
        return value, i

    def sequence(self, i: int, indent: int):
        out = []
        while True:
            i = self.next_content(i)
            ln = self.at(i)
            if ln is None or ln.indent != indent or not _is_item(ln.text):
                if ln is not None and ln.indent > indent:
                    raise YamlError(f"line {ln.no}: this line is indented more than the list item above it")
                return out, i
            rest = ln.text[1:].lstrip(" ")
            if not rest:
                j = self.next_content(i + 1)
                nxt = self.at(j)
                if nxt is not None and nxt.indent > indent:
                    value, i = self.block(j, nxt.indent)
                else:
                    value, i = None, i + 1
            else:
                column = indent + (len(ln.text) - len(rest))
                if _is_item(rest) or _key_split(rest, ln.no) is not None:
                    # "- key: value" or "- - x": a block that starts on the item's line
                    self.lines[i] = _Line(ln.no, column, rest)
                    value, i = self.block(i, column)
                else:
                    value, i = self.inline(i, rest, indent)
            out.append(value)

    def mapping(self, i: int, indent: int):
        out: dict = {}
        while True:
            i = self.next_content(i)
            ln = self.at(i)
            if ln is None or ln.indent != indent or _is_item(ln.text):
                if ln is not None and ln.indent > indent:
                    raise YamlError(f"line {ln.no}: this line is indented more than the key above it")
                return out, i
            split = _key_split(ln.text, ln.no)
            if split is None:
                raise YamlError(f"line {ln.no}: expected 'key: value' here")
            key, rest = split
            if not rest:
                j = self.next_content(i + 1)
                nxt = self.at(j)
                if nxt is not None and (nxt.indent > indent or (nxt.indent == indent and _is_item(nxt.text))):
                    value, i = self.block(j, nxt.indent)
                else:
                    value, i = None, i + 1
            else:
                value, i = self.inline(i, rest, indent)
            out[key] = value

    def inline(self, i: int, text: str, indent: int):
        """The value that starts on line i with text; it may go on over the next lines (flow, quotes, plain)."""
        ln = self.lines[i]
        head = text[:1]
        if head in "&*!":
            raise YamlError(f"line {ln.no}: anchors (&), aliases (*) and tags (!) are not read")
        if head in "|>":
            return self.block_text(i, text, indent)
        if head in "[{\"'":
            joined, i = text, i + 1
            while True:
                try:
                    parser = _Flow(joined, ln.no)
                    value = parser.value(block=True)
                    parser.end()
                    return value, i
                except _Unfinished:
                    j = self.next_content(i)
                    nxt = self.at(j)
                    if nxt is None or (nxt.indent <= indent and head not in "\"'"):
                        raise YamlError(f"line {ln.no}: this value is not closed") from None
                    joined, i = joined + (" " if head in "[{" else "\n") + nxt.text.strip(), j + 1
        # a plain word, folded with the more indented lines under it
        words = [text.strip()]
        i += 1
        while True:
            j = self.next_content(i)
            nxt = self.at(j)
            if nxt is None or nxt.indent <= indent:
                break
            words.append(nxt.text.strip())
            i = j + 1
        joined = " ".join(words)
        if ": " in joined and len(words) > 1:
            raise YamlError(f"line {ln.no}: a plain value goes on over lines that look like 'key: value'; "
                            "indent the keys or quote the value")
        return plain(joined), i

    def block_text(self, i: int, header: str, indent: int):
        """A | (literal) or > (folded) block, with the - and + chomping marks."""
        ln = self.lines[i]
        m = re.match(r"^([|>])([-+]?)([1-9]?)([-+]?)$", header.strip())
        if not m:
            raise YamlError(f"line {ln.no}: the block header '{header}' is not read")
        style, chomp = m.group(1), m.group(2) or m.group(4)
        body: list[str] = []
        k = ln.no   # raw index of the next line (ln.no is 1-based)
        width = None
        while k < len(self.raw):
            line = self.raw[k]
            stripped = line.lstrip(" ")
            if stripped == "":
                body.append("")
                k += 1
                continue
            cur = len(line) - len(stripped)
            if cur <= indent:
                break
            width = cur if width is None else width
            if cur < width:
                break
            body.append(line[width:])
            k += 1
        # the lines used: everything up to raw line k
        i = next((n for n, x in enumerate(self.lines) if x.no > k), len(self.lines))
        while body and body[-1] == "" and chomp != "+":
            body.pop()
        if style == "|":
            text = "\n".join(body)
        else:
            # folded: a line break between two plain lines becomes a space; next to a more indented line it stays;
            # each empty line is a line break
            text, prev, empties = "", None, 0
            for line in body:
                if line == "":
                    empties += 1
                    continue
                kind = "more" if line.startswith(" ") else "plain"
                if prev is None:
                    text += "\n" * empties
                elif prev == kind == "plain":
                    text += "\n" * empties if empties else " "
                else:
                    text += "\n" * (empties + 1)
                text += line
                prev, empties = kind, 0
            text += "\n" * empties
        if chomp != "-" and body:
            text += "\n"
        return text, i


def _is_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _key_split(text: str, no: int):
    """(key, rest) when the line is 'key: value' or 'key:', else None."""
    if text[:1] in "[{":
        return None
    if text[:1] in "\"'":
        try:
            parser = _Flow(text, no)
            key = parser.quoted()
        except _Unfinished:
            return None
        rest = text[parser.pos:]
        if rest.startswith(":") and (len(rest) == 1 or rest[1] == " "):
            return key, rest[1:].strip()
        return None
    m = re.match(r"^([^#]*?):(?: (.*)|$)", text)
    if not m or text.startswith("? "):
        return None
    key = m.group(1).strip()
    if not key:
        raise YamlError(f"line {no}: a key is empty")
    value = plain(key)
    return (value if not isinstance(value, float) else key), (m.group(2) or "").strip()


class _Unfinished(Exception):
    pass


class _Flow:
    """{…}, […] and quoted text, on one (joined) line."""

    def __init__(self, text: str, no: int):
        self.text, self.no, self.pos = text, no, 0

    def fail(self, why: str):
        raise YamlError(f"line {self.no}: {why}")

    def skip(self):
        while self.pos < len(self.text) and self.text[self.pos] in " \t\n":
            self.pos += 1

    def end(self):
        self.skip()
        if self.pos != len(self.text):
            self.fail(f"unexpected text after the value: '{self.text[self.pos:][:40]}'")

    def value(self, block: bool = False):
        self.skip()
        if self.pos >= len(self.text):
            raise _Unfinished()
        c = self.text[self.pos]
        if c == "{":
            return self.mapping()
        if c == "[":
            return self.sequence()
        if c in "\"'":
            return self.quoted()
        if c in "&*!":
            self.fail("anchors (&), aliases (*) and tags (!) are not read")
        return plain(self.word(stop=",]}" if not block else ""))

    def word(self, stop: str, key: bool = False) -> str:
        start = self.pos
        while self.pos < len(self.text):
            c = self.text[self.pos]
            if c in stop:
                break
            if c == ":" and key and (self.pos + 1 == len(self.text) or self.text[self.pos + 1] in " ,]}"):
                break
            if c == "#" and self.pos > start and self.text[self.pos - 1] == " ":
                break
            self.pos += 1
        return self.text[start:self.pos].strip()

    def sequence(self) -> list:
        self.pos += 1
        out = []
        while True:
            self.skip()
            if self.pos >= len(self.text):
                raise _Unfinished()
            if self.text[self.pos] == "]":
                self.pos += 1
                return out
            out.append(self.value())
            self.skip()
            if self.pos >= len(self.text):
                raise _Unfinished()
            if self.text[self.pos] == ",":
                self.pos += 1
            elif self.text[self.pos] != "]":
                self.fail("expected ',' or ']' in a [list]")

    def mapping(self) -> dict:
        self.pos += 1
        out: dict = {}
        while True:
            self.skip()
            if self.pos >= len(self.text):
                raise _Unfinished()
            if self.text[self.pos] == "}":
                self.pos += 1
                return out
            if self.text[self.pos] in "\"'":
                key = self.quoted()
            else:
                word = self.word(stop=",}", key=True)
                key = plain(word)
                key = word if isinstance(key, float) else key
            self.skip()
            if self.pos >= len(self.text):
                raise _Unfinished()
            value = None
            if self.text[self.pos] == ":":
                self.pos += 1
                self.skip()
                if self.pos < len(self.text) and self.text[self.pos] not in ",}":
                    value = self.value()
            out[key] = value
            self.skip()
            if self.pos >= len(self.text):
                raise _Unfinished()
            if self.text[self.pos] == ",":
                self.pos += 1
            elif self.text[self.pos] != "}":
                self.fail("expected ',' or '}' in a {mapping}")

    def quoted(self) -> str:
        q = self.text[self.pos]
        self.pos += 1
        out = []
        while True:
            if self.pos >= len(self.text):
                raise _Unfinished()
            c = self.text[self.pos]
            if q == "'" and c == "'":
                if self.text[self.pos + 1:self.pos + 2] == "'":
                    out.append("'")
                    self.pos += 2
                    continue
                self.pos += 1
                return _fold("".join(out))
            if q == '"' and c == '"':
                self.pos += 1
                return _fold("".join(out))
            if q == '"' and c == "\\":
                out.append(self._escape())
                continue
            out.append(c)
            self.pos += 1

    def _escape(self) -> str:
        e = self.text[self.pos + 1:self.pos + 2]
        if not e:
            raise _Unfinished()
        if e in _ESCAPES:
            self.pos += 2
            return _ESCAPES[e]
        if e == "\n":   # an escaped line break joins the lines
            self.pos += 2
            return ""
        size = {"x": 2, "u": 4, "U": 8}.get(e)
        digits = self.text[self.pos + 2:self.pos + 2 + (size or 0)]
        if not size or len(digits) != size or not all(d in "0123456789abcdefABCDEF" for d in digits):
            self.fail(f"'\\{e}' is not an escape YAML knows")
        self.pos += 2 + size
        return chr(int(digits, 16))


def _fold(text: str) -> str:
    """Quoted text over several lines: one line break becomes a space, an empty line a line break."""
    if "\n" not in text:
        return text
    parts = [p.strip() for p in text.split("\n")]
    out = parts[0]
    for p in parts[1:]:
        out += "\n" if p == "" else ("" if out.endswith("\n") else " ") + p
    return out


def loads(text: str):
    """The value of a YAML document (None when it is empty)."""
    return _Reader(text).document()
