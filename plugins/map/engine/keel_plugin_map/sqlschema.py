"""SQL migrations -> the schema they leave behind, for the Map page's database diagram.

    parse([(rel, text), ...]) -> {tables, relations, files, skipped}

The files are applied in the order given (the caller sorts them). This is a tolerant reader of DDL, not a SQL engine:
it understands CREATE TABLE / VIEW / INDEX, ALTER TABLE (add, drop, rename, alter and modify columns, add and drop
constraints, rename the table), DROP TABLE / VIEW / INDEX, RENAME TABLE and COMMENT ON, in the PostgreSQL, MySQL,
SQLite, H2 and SQL Server spellings that migrations use. Anything else (DML, functions, grants) is skipped, and a
statement it cannot read is counted in `skipped` rather than failing the map.

Every table, column, key and index carries the file and line it came from (`cite: {rel, line}`), and every table its
`changes`: the later statements that altered it.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

DEFAULT_SCHEMAS = {"public", "dbo", "main"}
ACTIONS = ("cascade", "restrict", "set null", "set default", "no action")

# A column definition's type ends at the first of these (at depth 0).
TYPE_STOP = {
    "not", "null", "default", "primary", "unique", "references", "check", "constraint", "collate", "generated",
    "auto_increment", "autoincrement", "identity", "comment", "on", "as", "encode", "charset", "deferrable",
    "initially", "compression", "storage", "visible", "invisible", "sparse", "rowguidcol", "key", "masked", "with",
}
DEFAULT_STOP = TYPE_STOP - {"with", "key"}
TABLE_CONSTRAINT = {"constraint", "primary", "unique", "foreign", "check", "exclude", "like", "fulltext", "spatial", "period"}
DOWN_MARK = re.compile(r"^[ \t]*--[ \t]*(migrate:down|\+goose[ \t]+down)\b.*$", re.I | re.M)
UP_MARK = re.compile(r"^[ \t]*--[ \t]*(migrate:up|\+goose[ \t]+up)\b.*$", re.I | re.M)


# ------------------------------------------------------------------ lexing

@dataclass
class Tok:
    kind: str      # id (a bare word), qid (a quoted identifier), str, num, op
    val: str       # the identifier without its quotes, the string's content, the operator
    pos: int       # offset of the first character in the file
    end: int       # offset after the last character

    def word(self, *words: str) -> bool:
        return self.kind == "id" and self.val.lower() in words

    @property
    def ident(self) -> bool:
        return self.kind in ("id", "qid")


def _blank(text: str, a: int, b: int) -> str:
    """Spaces for text[a:b], newlines kept, so offsets and line numbers stay true."""
    return text[:a] + re.sub(r"[^\n]", " ", text[a:b]) + text[b:]


def strip_down_sections(text: str) -> str:
    """dbmate (`-- migrate:down`) and goose (`-- +goose Down`) keep the undo in the same file: drop it."""
    while (m := DOWN_MARK.search(text)):
        up = UP_MARK.search(text, m.end())
        text = _blank(text, m.start(), up.start() if up else len(text))
    return text


def tokens(text: str) -> list[list[Tok]]:
    """The statements of a file as token lists: comments dropped, split at a top-level `;` or a line that is only GO."""
    out: list[list[Tok]] = []
    cur: list[Tok] = []
    i, n = 0, len(text)

    def end_stmt():
        nonlocal cur
        if cur:
            out.append(cur)
        cur = []

    while i < n:
        c = text[i]
        if c in " \t\r\n\f":
            i += 1
        elif text.startswith("--", i) or (c == "#" and text[text.rfind("\n", 0, i) + 1:i].strip() == ""):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if text.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        elif c == "'" or (c in "eEnN" and text.startswith("'", i + 1)):
            start = i
            i += 1 if c == "'" else 2
            escapes = c in "eE"
            buf = []
            while i < n:
                if escapes and text[i] == "\\" and i + 1 < n:
                    buf.append(text[i + 1])
                    i += 2
                elif text[i] == "'":
                    if text.startswith("''", i):
                        buf.append("'")
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    buf.append(text[i])
                    i += 1
            cur.append(Tok("str", "".join(buf), start, i))
        elif c in '"`':
            j = i + 1
            while j < n and not (text[j] == c and not text.startswith(c * 2, j)):
                j += 2 if text.startswith(c * 2, j) else 1
            cur.append(Tok("qid", text[i + 1:j].replace(c * 2, c), i, min(j + 1, n)))
            i = j + 1
        elif c == "[" and (m := re.match(r"\[([^\]\n\[]*[A-Za-z_][^\]\n\[]*)\]", text[i:])) and not (i and text[i - 1].isalnum()):
            cur.append(Tok("qid", m.group(1), i, i + m.end()))
            i += m.end()
        elif c == "$" and (m := re.match(r"\$([A-Za-z_]\w*)?\$", text[i:])):
            close = text.find(m.group(0), i + m.end())
            j = n if close < 0 else close + m.end()
            cur.append(Tok("str", text[i + m.end():close if close >= 0 else n], i, j))
            i = j
        elif c.isdigit():
            m = re.match(r"\d+(\.\d+)?", text[i:])
            cur.append(Tok("num", m.group(0), i, i + m.end()))
            i += m.end()
        elif c.isalpha() or c == "_" or ord(c) > 127:
            m = re.match(r"[\w$]+", text[i:])
            w = m.group(0)
            line_start = text.rfind("\n", 0, i) + 1
            line_end = text.find("\n", i)
            line_end = n if line_end < 0 else line_end
            if w.lower() == "go" and not text[line_start:i].strip() and not text[i + len(w):line_end].strip():
                end_stmt()
            else:
                cur.append(Tok("id", w, i, i + len(w)))
            i += len(w)
        elif c == ";":
            end_stmt()
            i += 1
        else:
            cur.append(Tok("op", c, i, i + 1))
            i += 1
    end_stmt()
    return out


# ------------------------------------------------------------------ a cursor over one statement

class Cur:
    def __init__(self, toks: list[Tok], text: str):
        self.t, self.i, self.text = toks, 0, text

    def peek(self, k: int = 0) -> Tok | None:
        j = self.i + k
        return self.t[j] if 0 <= j < len(self.t) else None

    def done(self) -> bool:
        return self.i >= len(self.t)

    def next(self) -> Tok | None:
        tok = self.peek()
        self.i += 1
        return tok

    def at(self, *words: str) -> bool:
        """The next tokens are these keywords (a word may be "a|b")."""
        for k, w in enumerate(words):
            tok = self.peek(k)
            if not tok or not tok.word(*w.split("|")):
                return False
        return True

    def eat(self, *words: str) -> bool:
        if self.at(*words):
            self.i += len(words)
            return True
        return False

    def op(self, ch: str) -> bool:
        tok = self.peek()
        if tok and tok.kind == "op" and tok.val == ch:
            self.i += 1
            return True
        return False

    def name(self) -> tuple[str | None, str, Tok] | None:
        """A possibly qualified name: `a`, `s.a`, `"S"."A"`, `db.s.a` (the last two parts count)."""
        tok = self.peek()
        if not tok or not tok.ident:
            return None
        parts = [tok]
        self.i += 1
        while self.peek() and self.peek().kind == "op" and self.peek().val == "." and self.peek(1) and self.peek(1).ident:
            parts.append(self.peek(1))
            self.i += 2
        schema = parts[-2].val if len(parts) > 1 else None
        return schema, parts[-1].val, parts[-1]

    def group(self) -> list[Tok] | None:
        """The tokens inside the next (...), the cursor after its `)`; None when no `(` is next."""
        tok = self.peek()
        if not tok or tok.kind != "op" or tok.val != "(":
            return None
        depth, start = 0, self.i
        while not self.done():
            tok = self.next()
            if tok.kind == "op" and tok.val == "(":
                depth += 1
            elif tok.kind == "op" and tok.val == ")":
                depth -= 1
                if depth == 0:
                    return self.t[start + 1:self.i - 1]
        return self.t[start + 1:]

    def skip_value(self):
        """One token, or a whole (...) group."""
        if self.group() is None:
            self.next()

    def src(self, toks: list[Tok]) -> str:
        return re.sub(r"\s+", " ", self.text[toks[0].pos:toks[-1].end]).strip() if toks else ""


def split_top(toks: list[Tok]) -> list[list[Tok]]:
    """Split at commas outside parentheses."""
    out, cur, depth = [], [], 0
    for tok in toks:
        if tok.kind == "op" and tok.val in "([":
            depth += 1
        elif tok.kind == "op" and tok.val in ")]":
            depth -= 1
        if tok.kind == "op" and tok.val == "," and depth == 0:
            out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def column_list(toks: list[Tok]) -> list[str]:
    """`(a, b DESC, lower(c), d(10))` -> ["a", "b", "lower(c)", "d"]."""
    out = []
    for item in split_top(toks):
        if not item:
            continue
        first = item[0]
        if first.ident and (len(item) == 1 or not (item[1].kind == "op" and item[1].val == "(")):
            out.append(first.val)
        elif first.ident and len(item) >= 4 and item[2].kind == "num" and item[3].val == ")":
            out.append(first.val)                             # MySQL prefix length: name(10)
        else:
            out.append("".join(t.val if t.kind != "str" else f"'{t.val}'" for t in item))
    return out


# ------------------------------------------------------------------ the schema being built

def _key(schema: str | None, name: str) -> tuple[str, str]:
    s = (schema or "").lower()
    return ("" if s in DEFAULT_SCHEMAS else s, name.lower())


@dataclass
class Table:
    name: str
    schema: str | None
    kind: str                       # table | view | materialized view
    cite: dict
    columns: list[dict] = field(default_factory=list)
    pk: dict | None = None          # {name, columns, cite}
    uniques: list[dict] = field(default_factory=list)
    indexes: list[dict] = field(default_factory=list)
    fks: list[dict] = field(default_factory=list)
    checks: int = 0
    comment: str | None = None
    changes: list[dict] = field(default_factory=list)
    definition: str | None = None
    uses: list[tuple[str | None, str]] = field(default_factory=list)

    def col(self, name: str) -> dict | None:
        low = name.lower()
        return next((c for c in self.columns if c["name"].lower() == low), None)


class Schema:
    def __init__(self):
        self.tables: dict[tuple[str, str], Table] = {}
        self.index_owner: dict[str, tuple[str, str]] = {}   # index name -> table key
        self.files: list[str] = []
        self.skipped = 0
        self.rel = ""
        self.text = ""
        self._lines: list[int] = []

    # ---- positions
    def start_file(self, rel: str, text: str):
        self.rel, self.text = rel, text
        self._lines = [m.end() for m in re.finditer(r"\n", text)]
        self.files.append(rel)

    def cite(self, tok: Tok) -> dict:
        return {"rel": self.rel, "line": bisect.bisect_right(self._lines, tok.pos) + 1}

    def get(self, schema: str | None, name: str) -> Table | None:
        return self.tables.get(_key(schema, name))

    def note(self, t: Table, tok: Tok, what: str):
        t.changes.append({**self.cite(tok), "what": what})


# ------------------------------------------------------------------ statements

def _action(c: Cur) -> str | None:
    for a in ACTIONS:
        if c.eat(*a.split()):
            return a.upper()
    return None


def _references(c: Cur) -> dict:
    """After REFERENCES: target [(cols)] [MATCH ..] [ON DELETE a] [ON UPDATE a] [DEFERRABLE ...]."""
    nm = c.name()
    cols = c.group()
    fk = {"ref_schema": nm[0] if nm else None, "ref_name": nm[1] if nm else "", "ref_columns": column_list(cols) if cols else [],
          "on_delete": None, "on_update": None}
    while not c.done():
        if c.eat("on", "delete"):
            fk["on_delete"] = _action(c)
        elif c.eat("on", "update"):
            fk["on_update"] = _action(c)
        elif c.at("match") or c.at("deferrable") or c.at("not", "deferrable") or c.at("initially"):
            c.next()
            if c.peek() and c.peek().word("deferrable", "full", "partial", "simple", "deferred", "immediate"):
                c.next()
        else:
            break
    return fk


def _column(s: Schema, t: Table, toks: list[Tok], c_text: str) -> dict | None:
    """One column definition: name type [constraints]."""
    c = Cur(toks, c_text)
    name_tok = c.next()
    if not name_tok or not name_tok.ident:
        return None
    type_toks: list[Tok] = []
    depth = 0
    while not c.done():
        tok = c.peek()
        if depth == 0 and tok.kind == "id":
            low = tok.val.lower()
            if low == "with" and type_toks and c.peek(1) and c.peek(1).word("time", "local"):
                pass                                          # timestamp with time zone
            elif low == "character" and c.peek(1) and c.peek(1).word("set"):
                break
            elif low in TYPE_STOP and not (low == "key" and not type_toks):
                break
        if tok.kind == "op" and tok.val in "([":
            depth += 1
        elif tok.kind == "op" and tok.val in ")]":
            depth -= 1
        type_toks.append(c.next())
    col = {"name": name_tok.val, "type": re.sub(r"\s+(?=[(\[])", "", c.src(type_toks)), "nullable": True, "default": None,
           "pk": False, "unique": False, "fk": None, "identity": False, "generated": None, "comment": None,
           "cite": s.cite(name_tok)}
    if col["type"].lower() in ("serial", "bigserial", "smallserial", "serial4", "serial8", "serial2"):
        col["identity"] = True
    pending = None
    while not c.done():
        tok = c.peek()
        if c.eat("constraint"):
            nm = c.next()
            pending = nm.val if nm else None
        elif c.eat("not", "null"):
            col["nullable"] = False
        elif c.eat("null"):
            col["nullable"] = True
        elif c.eat("default"):
            start = c.i
            c.skip_value()                                    # the first token always belongs to it: DEFAULT NULL
            depth = 0
            while not c.done():
                nxt = c.peek()
                if depth == 0 and nxt.kind == "id" and nxt.val.lower() in DEFAULT_STOP:
                    break
                if nxt.kind == "op" and nxt.val == "(":
                    depth += 1
                elif nxt.kind == "op" and nxt.val == ")":
                    depth -= 1
                c.next()
            col["default"] = c.src(c.t[start:c.i]) or None
        elif c.eat("primary", "key"):
            col["pk"], col["nullable"] = True, False
            t.pk = {"name": pending, "columns": [col["name"]], "cite": s.cite(tok)}
            c.eat("asc") or c.eat("desc")
            if c.eat("autoincrement"):
                col["identity"] = True
            pending = None
        elif c.eat("unique"):
            c.eat("key")
            col["unique"] = True
            pending = None
        elif c.eat("references"):
            fk = _references(c)
            t.fks.append({"name": pending, "columns": [col["name"]], **fk, "cite": s.cite(tok)})
            pending = None
        elif c.eat("check"):
            c.group()
            t.checks += 1
            pending = None
        elif c.at("generated"):
            c.next()
            c.eat("always") or c.eat("by", "default")
            c.eat("as")
            if c.eat("identity"):
                col["identity"] = True
                c.group()
            else:
                g = c.group()
                col["generated"] = c.src(g) if g else "generated"
                c.eat("stored") or c.eat("virtual")
        elif c.eat("as"):
            g = c.group()
            col["generated"] = c.src(g) if g else "generated"
            c.eat("stored") or c.eat("virtual") or c.eat("persisted")
        elif c.eat("auto_increment") or c.eat("autoincrement"):
            col["identity"] = True
        elif c.eat("identity"):
            col["identity"] = True
            c.group()
        elif c.eat("comment"):
            nxt = c.next()
            col["comment"] = nxt.val if nxt and nxt.kind == "str" else None
        elif c.eat("collate") or c.eat("charset") or c.eat("character", "set") or c.eat("on", "update") or c.eat("encode") \
                or c.eat("compression") or c.eat("storage"):
            c.skip_value()
        else:
            c.next()
    return col


def _table_constraint(s: Schema, t: Table, toks: list[Tok], text: str) -> bool:
    """A table-level PRIMARY KEY / UNIQUE / FOREIGN KEY / CHECK / KEY / INDEX. False when it is not one."""
    c = Cur(toks, text)
    first = c.peek()
    name = None
    if c.eat("constraint"):
        nm = c.next()
        name = nm.val if nm else None
    tok = c.peek()
    if not tok:
        return name is not None
    if c.eat("primary", "key"):
        c.eat("clustered") or c.eat("nonclustered")
        cols = column_list(c.group() or [])
        t.pk = {"name": name, "columns": cols, "cite": s.cite(first)}
        for cn in cols:
            if (col := t.col(cn)):
                col["pk"], col["nullable"] = True, False
        return True
    if c.eat("unique"):
        c.eat("key") or c.eat("index")
        c.eat("nulls", "not", "distinct") or c.eat("nulls", "distinct")
        if c.peek() and c.peek().ident and not c.at("clustered") and not c.at("nonclustered"):
            name = name or c.peek().val
            c.next()
        c.eat("clustered") or c.eat("nonclustered")
        cols = column_list(c.group() or [])
        t.uniques.append({"name": name, "columns": cols, "cite": s.cite(first)})
        if len(cols) == 1 and (col := t.col(cols[0])):
            col["unique"] = True
        return True
    if c.eat("foreign", "key"):
        if c.peek() and c.peek().ident:
            name = name or c.next().val
        cols = column_list(c.group() or [])
        if c.eat("references"):
            t.fks.append({"name": name, "columns": cols, **_references(c), "cite": s.cite(first)})
        return True
    if c.eat("check"):
        t.checks += 1
        return True
    if c.at("exclude") or c.at("like") or c.at("period"):
        return True
    method = None
    if c.at("fulltext") or c.at("spatial"):
        method = c.next().val.lower()
    if c.eat("key") or c.eat("index"):
        idx_name = c.next().val if c.peek() and c.peek().ident else None
        if c.eat("using"):
            method = c.next().val.lower()
        cols = column_list(c.group() or [])
        t.indexes.append({"name": idx_name or name, "columns": cols, "unique": False, "method": method, "where": None,
                          "cite": s.cite(first)})
        return True
    return name is not None


def _is_constraint(toks: list[Tok]) -> bool:
    first = toks[0]
    if first.kind != "id":
        return False
    low = first.val.lower()
    if low in TABLE_CONSTRAINT:
        return True
    if low in ("key", "index"):                                # MySQL `KEY name (cols)`, not a column called key
        nxt = toks[1] if len(toks) > 1 else None
        return bool(nxt) and ((nxt.kind == "op" and nxt.val == "(") or (nxt.ident and len(toks) > 2 and toks[2].val == "("))
    return False


def create_table(s: Schema, c: Cur, first: Tok):
    guarded = c.eat("if", "not", "exists")
    nm = c.name()
    if not nm:
        return
    schema, name, _ = nm
    if guarded and s.get(schema, name):
        return                                                # IF NOT EXISTS on a table that exists: nothing happens
    if c.at("partition", "of") or c.at("of"):
        return                                                # a partition: its parent is the table
    body = c.group()
    t = Table(name=name, schema=schema, kind="table", cite=s.cite(first))
    if body is None:
        if c.eat("as"):                                       # CREATE TABLE x AS SELECT ...
            _view_columns(s, t, c.t[c.i:], c.text)
            t.definition = c.src(c.t[c.i:])[:2000]
        elif c.eat("like"):
            src_nm = c.name()
            if src_nm and (src := s.get(src_nm[0], src_nm[1])):
                t.columns = [dict(col) for col in src.columns]
        else:
            return
    else:
        for el in split_top(body):
            if not el:
                continue
            if el[0].word("like") and len(el) > 1:
                sub = Cur(el[1:], c.text)
                src_nm = sub.name()
                if src_nm and (src := s.get(src_nm[0], src_nm[1])):
                    t.columns += [{**dict(col), "cite": s.cite(el[0])} for col in src.columns]
                continue
            if _is_constraint(el):
                _table_constraint(s, t, el, c.text)
            elif (col := _column(s, t, el, c.text)):
                if not t.col(col["name"]):
                    t.columns.append(col)
        if t.pk:                                              # constraints may come before the columns they name
            for cn in t.pk["columns"]:
                if (col := t.col(cn)):
                    col["pk"], col["nullable"] = True, False
        for u in t.uniques:
            if len(u["columns"]) == 1 and (col := t.col(u["columns"][0])):
                col["unique"] = True
    s.tables[_key(schema, name)] = t


def _apply_column_def(col: dict, new: dict):
    """MODIFY / CHANGE / ALTER COLUMN with a full definition: the type and its constraints replace the old ones."""
    for k in ("type", "nullable", "default", "identity", "generated", "comment"):
        col[k] = new[k]
    if new["pk"]:
        col["pk"] = True
    if new["unique"]:
        col["unique"] = True


def alter_table(s: Schema, c: Cur, first: Tok):
    c.eat("if", "exists")
    c.eat("only")
    nm = c.name()
    if not nm:
        return
    t = s.get(nm[0], nm[1])
    if not t:
        return
    verb = None
    for action in split_top(c.t[c.i:]):
        if not action:
            continue
        a = Cur(action, c.text)
        tok = a.peek()
        if a.at("add|drop|rename|alter|modify|change|set|owner|enable|disable|validate|attach|detach|replica|cluster|inherit|no|reset|force"):
            verb = tok.val.lower()
            a.next()
        elif verb != "add":
            continue
        rest = action[a.i:]
        if verb == "add":
            a.eat("column")
            a.eat("if", "not", "exists")
            rest = action[a.i:]
            if not rest:
                continue
            if _is_constraint(rest):
                before = (len(t.fks), len(t.uniques), len(t.indexes))
                _table_constraint(s, t, rest, c.text)
                after = (len(t.fks), len(t.uniques), len(t.indexes))
                what = ("foreign key" if after[0] > before[0] else "unique" if after[1] > before[1] else "index" if after[2] > before[2]
                        else "primary key" if rest[0].word("primary") or (len(rest) > 2 and rest[2].word("primary")) else "constraint")
                s.note(t, tok, f"added {what}")
            elif (col := _column(s, t, rest, c.text)):
                if not t.col(col["name"]):
                    t.columns.append(col)
                    if col["pk"]:
                        t.pk = {"name": None, "columns": [col["name"]], "cite": col["cite"]}
                    s.note(t, tok, f"added column {col['name']}")
        elif verb == "drop":
            if a.eat("constraint"):
                a.eat("if", "exists")
                cn = a.next()
                _drop_constraint(t, cn.val if cn else "")
                s.note(t, tok, f"dropped constraint {cn.val if cn else ''}")
            elif a.eat("primary", "key"):
                _drop_pk(t)
                s.note(t, tok, "dropped primary key")
            elif a.eat("foreign", "key") or a.eat("index") or a.eat("key"):
                cn = a.next()
                _drop_constraint(t, cn.val if cn else "")
            elif a.at("not", "null") or a.at("default"):
                continue
            else:
                a.eat("column")
                a.eat("if", "exists")
                cn = a.next()
                if cn and cn.ident and (col := t.col(cn.val)):
                    _drop_column(t, col["name"])
                    s.note(t, tok, f"dropped column {col['name']}")
        elif verb == "rename":
            if a.eat("to") or a.eat("as"):
                new = a.name()
                if new:
                    _rename_table(s, t, new[1], new[0] if new[0] else t.schema)
                    s.note(t, tok, f"renamed from {nm[1]}")
            elif a.eat("constraint") or a.eat("index") or a.eat("key"):
                old = a.next()
                a.eat("to")
                new = a.next()
                if old and new:
                    for coll in (t.fks, t.uniques, t.indexes):
                        for x in coll:
                            if (x.get("name") or "").lower() == old.val.lower():
                                x["name"] = new.val
                    if t.pk and (t.pk.get("name") or "").lower() == old.val.lower():
                        t.pk["name"] = new.val
            else:
                a.eat("column")
                old = a.next()
                a.eat("to")
                new = a.next()
                if old and new and (col := t.col(old.val)):
                    _rename_column(s, t, col["name"], new.val)
                    s.note(t, tok, f"renamed column {old.val} to {new.val}")
        elif verb in ("alter", "modify", "change"):
            a.eat("column")
            cn = a.peek()
            if not cn or not cn.ident:
                continue
            col = t.col(cn.val)
            if verb == "change":                              # CHANGE old new definition
                a.next()
                if col and (new := _column(s, t, action[a.i:], c.text)):
                    if new["name"] != col["name"]:
                        _rename_column(s, t, col["name"], new["name"])
                    _apply_column_def(col, new)
                    s.note(t, tok, f"changed column {new['name']}")
                continue
            if verb == "modify":
                if col and (new := _column(s, t, action[a.i:], c.text)):
                    _apply_column_def(col, new)
                    s.note(t, tok, f"modified column {col['name']}")
                continue
            a.next()
            if not col:
                continue
            if a.eat("set", "data", "type") or a.eat("type"):
                type_toks = []
                while not a.done() and not a.at("using") and not a.at("collate"):
                    type_toks.append(a.next())
                col["type"] = re.sub(r"\s+(?=[(\[])", "", a.src(type_toks))
            elif a.eat("set", "not", "null"):
                col["nullable"] = False
            elif a.eat("drop", "not", "null"):
                col["nullable"] = True
            elif a.eat("set", "default"):
                col["default"] = a.src(action[a.i:]) or None
            elif a.eat("drop", "default"):
                col["default"] = None
            elif a.at("add", "generated"):
                col["identity"] = True
            elif a.at("set") or a.at("drop") or a.at("reset") or a.at("restart"):
                continue
            elif (new := _column(s, t, action[a.i - 1:], c.text)):    # SQL Server: ALTER COLUMN c int NOT NULL
                _apply_column_def(col, new)
            s.note(t, tok, f"altered column {col['name']}")
        elif verb == "set" and a.eat("schema"):
            new_schema = a.next()
            if new_schema:
                _rename_table(s, t, t.name, new_schema.val)


def _drop_pk(t: Table):
    if t.pk:
        for cn in t.pk["columns"]:
            if (col := t.col(cn)):
                col["pk"] = False
    t.pk = None


def _drop_constraint(t: Table, name: str):
    low = name.lower()
    if t.pk and (t.pk.get("name") or "").lower() == low:
        _drop_pk(t)
    t.fks = [f for f in t.fks if (f.get("name") or "").lower() != low]
    gone = [u for u in t.uniques if (u.get("name") or "").lower() == low]
    t.uniques = [u for u in t.uniques if u not in gone]
    for u in gone:
        if len(u["columns"]) == 1 and (col := t.col(u["columns"][0])):
            col["unique"] = any(u2["columns"] == u["columns"] for u2 in t.uniques)
    t.indexes = [i for i in t.indexes if (i.get("name") or "").lower() != low]


def _drop_column(t: Table, name: str):
    low = name.lower()
    t.columns = [c for c in t.columns if c["name"].lower() != low]
    t.fks = [f for f in t.fks if low not in (x.lower() for x in f["columns"])]
    t.uniques = [u for u in t.uniques if low not in (x.lower() for x in u["columns"])]
    t.indexes = [i for i in t.indexes if low not in (x.lower() for x in i["columns"])]
    if t.pk and low in (x.lower() for x in t.pk["columns"]):
        _drop_pk(t)


def _rename_column(s: Schema, t: Table, old: str, new: str):
    low = old.lower()
    sub = lambda cols: [new if x.lower() == low else x for x in cols]   # noqa: E731
    if (col := t.col(old)):
        col["name"] = new
    for coll in (t.fks, t.uniques, t.indexes):
        for x in coll:
            x["columns"] = sub(x["columns"])
    if t.pk:
        t.pk["columns"] = sub(t.pk["columns"])
    tkey = _key(t.schema, t.name)
    for other in s.tables.values():                           # foreign keys that point at the renamed column
        for f in other.fks:
            if _key(f["ref_schema"] or other.schema, f["ref_name"]) == tkey or _key(f["ref_schema"], f["ref_name"]) == tkey:
                f["ref_columns"] = sub(f["ref_columns"])


def _rename_table(s: Schema, t: Table, new_name: str, new_schema: str | None):
    old_key = _key(t.schema, t.name)
    for other in s.tables.values():
        for f in other.fks:
            if _key(f["ref_schema"], f["ref_name"]) == old_key or _key(f["ref_schema"] or other.schema, f["ref_name"]) == old_key:
                f["ref_schema"], f["ref_name"] = new_schema, new_name
    s.tables.pop(old_key, None)
    t.name, t.schema = new_name, new_schema
    s.tables[_key(new_schema, new_name)] = t
    for k, v in list(s.index_owner.items()):
        if v == old_key:
            s.index_owner[k] = _key(new_schema, new_name)


def drop(s: Schema, c: Cur):
    if c.eat("table"):
        c.eat("if", "exists")
        while not c.done():
            nm = c.name()
            if nm:
                key = _key(nm[0], nm[1])
                s.tables.pop(key, None)
                for other in s.tables.values():               # CASCADE (or the database refused): no dangling keys
                    other.fks = [f for f in other.fks if _key(f["ref_schema"] or other.schema, f["ref_name"]) != key
                                 and _key(f["ref_schema"], f["ref_name"]) != key]
            if not c.op(","):
                break
    elif c.eat("view") or c.eat("materialized", "view"):
        c.eat("if", "exists")
        while not c.done():
            nm = c.name()
            if nm and (t := s.get(nm[0], nm[1])) and t.kind != "table":
                s.tables.pop(_key(nm[0], nm[1]), None)
            if not c.op(","):
                break
    elif c.eat("index"):
        c.eat("concurrently")
        c.eat("if", "exists")
        names = []
        while not c.done():
            nm = c.name()
            if nm:
                names.append(nm[1])
            if not c.op(","):
                break
        on = c.name() if c.eat("on") else None                  # MySQL: DROP INDEX name ON table
        for n in names:
            key = _key(on[0], on[1]) if on else s.index_owner.get(n.lower())
            t = s.tables.get(key) if key else None
            if t:
                t.indexes = [i for i in t.indexes if (i.get("name") or "").lower() != n.lower()]


def create_index(s: Schema, c: Cur, first: Tok, unique: bool, method: str | None):
    c.eat("concurrently")
    c.eat("if", "not", "exists")
    name = None
    if not c.at("on"):
        nm = c.name()
        name = nm[1] if nm else None
    if c.eat("using"):
        method = c.next().val.lower()
    if not c.eat("on"):
        return
    c.eat("only")
    nm = c.name()
    if not nm or not (t := s.get(nm[0], nm[1])):
        return
    if c.eat("using"):
        method = c.next().val.lower()
    cols = column_list(c.group() or [])
    where = None
    while not c.done():
        if c.eat("where"):
            where = c.src(c.t[c.i:])
            break
        c.next()
    t.indexes.append({"name": name, "columns": cols, "unique": unique, "method": method, "where": where, "cite": s.cite(first)})
    if name:
        s.index_owner[name.lower()] = _key(t.schema, t.name)
    if unique and len(cols) == 1 and not where and (col := t.col(cols[0])):
        col["unique"] = True
    s.note(t, first, f"added {'unique ' if unique else ''}index {name or ''}".rstrip())


def _view_columns(s: Schema, t: Table, toks: list[Tok], text: str):
    """The select list of the main SELECT (column names or aliases) and the tables after FROM / JOIN."""
    depth, sel, frm = 0, None, None
    for k, tok in enumerate(toks):
        if tok.kind == "op" and tok.val == "(":
            depth += 1
        elif tok.kind == "op" and tok.val == ")":
            depth -= 1
        elif depth == 0 and tok.word("select") and sel is None:
            sel = k + 1
        elif depth == 0 and tok.word("from") and sel is not None and frm is None:
            frm = k
    for k, tok in enumerate(toks):                            # every FROM / JOIN at any depth: what the view reads
        if tok.word("from", "join") and k + 1 < len(toks) and toks[k + 1].ident:
            sub = Cur(toks[k + 1:], text)
            nm = sub.name()
            if nm and (nm[0], nm[1]) not in t.uses:
                t.uses.append((nm[0], nm[1]))
    if sel is None:
        return
    items = split_top(toks[sel:frm if frm is not None else len(toks)])
    if items and items[0] and items[0][0].word("distinct", "all"):
        items[0] = items[0][1:]
    if len(items) == 1 and items[0] and items[0][-1].kind == "op" and items[0][-1].val == "*" and len(t.uses) == 1:
        src = s.get(*t.uses[0])
        if src:
            t.columns = [{**dict(col), "pk": False, "unique": False, "fk": None} for col in src.columns]
        return
    for item in items:
        if not item:
            continue
        last = item[-1]
        if len(item) >= 2 and item[-2].word("as") and last.ident:
            name = last.val
        elif last.ident and (len(item) == 1 or (item[-2].kind == "op" and item[-2].val == ".") or item[-2].ident or item[-2].val == ")"):
            name = last.val
        elif last.kind == "op" and last.val == "*":
            name = "*"
        else:
            name = item[0].val
        t.columns.append({"name": name, "type": "", "nullable": True, "default": None, "pk": False, "unique": False, "fk": None,
                          "identity": False, "generated": None, "comment": None, "cite": s.cite(item[0])})


def create_view(s: Schema, c: Cur, first: Tok, kind: str):
    c.eat("if", "not", "exists")
    nm = c.name()
    if not nm:
        return
    t = Table(name=nm[1], schema=nm[0], kind=kind, cite=s.cite(first))
    names = c.group()
    while not c.done() and not c.at("as"):
        c.next()
    if not c.eat("as"):
        return
    query = c.t[c.i:]
    _view_columns(s, t, query, c.text)
    if names:
        cols = column_list(names)
        t.columns = [{"name": n, "type": "", "nullable": True, "default": None, "pk": False, "unique": False, "fk": None,
                      "identity": False, "generated": None, "comment": None, "cite": t.cite} for n in cols]
    t.definition = c.src(query)[:2000]
    s.tables[_key(nm[0], nm[1])] = t


def comment_on(s: Schema, c: Cur):
    if c.eat("table") or c.eat("view") or c.eat("materialized", "view"):
        nm = c.name()
        if nm and (t := s.get(nm[0], nm[1])) and c.eat("is"):
            tok = c.next()
            t.comment = tok.val if tok and tok.kind == "str" else None
    elif c.eat("column"):
        tok = c.peek()
        parts = []
        while tok and tok.ident:
            parts.append(tok.val)
            c.next()
            if not c.op("."):
                break
            tok = c.peek()
        if len(parts) >= 2 and (t := s.get(parts[-3] if len(parts) > 2 else None, parts[-2])) and (col := t.col(parts[-1])) and c.eat("is"):
            tok = c.next()
            col["comment"] = tok.val if tok and tok.kind == "str" else None


def statement(s: Schema, toks: list[Tok], text: str):
    c = Cur(toks, text)
    first = c.peek()
    if c.eat("create"):
        c.eat("or", "replace")
        c.eat("or", "alter")
        temp = False
        while c.at("global|local|temp|temporary|unlogged|transient|volatile|external|virtual|recursive|secure|force"):
            temp = temp or c.peek().word("temp", "temporary")
            c.next()
        if c.eat("table"):
            if not temp:
                create_table(s, c, first)
        elif c.eat("materialized", "view"):
            create_view(s, c, first, "materialized view")
        elif c.eat("view"):
            create_view(s, c, first, "view")
        else:
            unique = bool(c.eat("unique"))
            method = None
            while c.at("clustered|nonclustered|fulltext|spatial|bitmap"):
                tok = c.next()
                if tok.word("fulltext", "spatial", "bitmap"):
                    method = tok.val.lower()
            if c.eat("index"):
                create_index(s, c, first, unique, method)
    elif c.eat("alter", "table"):
        alter_table(s, c, first)
    elif c.eat("drop"):
        drop(s, c)
    elif c.eat("rename", "table"):                            # MySQL: RENAME TABLE a TO b, c TO d
        while not c.done():
            old = c.name()
            if not c.eat("to"):
                break
            new = c.name()
            if old and new and (t := s.get(old[0], old[1])):
                _rename_table(s, t, new[1], new[0] if new[0] else t.schema)
                s.note(t, first, f"renamed from {old[1]}")
            if not c.op(","):
                break
    elif c.eat("comment", "on"):
        comment_on(s, c)


# ------------------------------------------------------------------ the result

def _resolve(s: Schema, owner: Table, schema: str | None, name: str) -> Table | None:
    if schema:
        hit = s.get(schema, name)
        if hit:
            return hit
    hit = s.get(owner.schema, name) or s.get(None, name)
    if hit:
        return hit
    same = [t for t in s.tables.values() if t.name.lower() == name.lower()]
    return same[0] if len(same) == 1 else None


def table_id(t: Table) -> str:
    return f"{t.schema}.{t.name}" if t.schema and t.schema.lower() not in DEFAULT_SCHEMAS else t.name


def result(s: Schema) -> dict:
    ids = {_key(t.schema, t.name): table_id(t) for t in s.tables.values()}
    tables, relations = [], []
    for t in sorted(s.tables.values(), key=lambda t: table_id(t).lower()):
        tid = table_id(t)
        fks_out = []
        for n, f in enumerate(t.fks):
            target = _resolve(s, t, f["ref_schema"], f["ref_name"])
            ref_cols = list(f["ref_columns"]) or (list(target.pk["columns"]) if target and target.pk else [])
            to = ids[_key(target.schema, target.name)] if target else None
            fk = {"name": f["name"], "columns": list(f["columns"]), "ref_table": to,
                  "ref_name": f"{f['ref_schema']}.{f['ref_name']}" if f["ref_schema"] else f["ref_name"], "ref_columns": ref_cols,
                  "on_delete": f["on_delete"], "on_update": f["on_update"], "cite": f["cite"], "missing": target is None}
            fks_out.append(fk)
            for i, cn in enumerate(f["columns"]):
                if (col := t.col(cn)) and not col["fk"]:
                    col["fk"] = {"table": to or fk["ref_name"], "column": ref_cols[i] if i < len(ref_cols) else None,
                                 "missing": target is None}
            if target:
                cols = [t.col(cn) for cn in f["columns"]]
                key_sets = ([t.pk["columns"]] if t.pk else []) + [u["columns"] for u in t.uniques] + \
                    [i["columns"] for i in t.indexes if i["unique"] and not i["where"]]
                mine = {x.lower() for x in f["columns"]}       # the FK holds a whole key of its table: one row per parent
                one = any(ks and {x.lower() for x in ks} <= mine for ks in key_sets)
                relations.append({"id": f"fk:{tid}:{f['name'] or n}", "kind": "fk", "name": f["name"], "from": tid,
                                  "from_columns": list(f["columns"]), "to": to, "to_columns": ref_cols,
                                  "on_delete": f["on_delete"], "on_update": f["on_update"],
                                  "nullable": any(col is None or col["nullable"] for col in cols), "one_to_one": one,
                                  "self": to == tid, "cite": f["cite"]})
        uses = []
        for sch, nm in t.uses:
            target = _resolve(s, t, sch, nm)
            if target and target is not t:
                uid = ids[_key(target.schema, target.name)]
                if uid not in uses:
                    uses.append(uid)
                    relations.append({"id": f"uses:{tid}:{uid}", "kind": "uses", "name": None, "from": tid, "from_columns": [],
                                      "to": uid, "to_columns": [], "on_delete": None, "on_update": None, "nullable": False,
                                      "one_to_one": False, "self": False, "cite": t.cite})
        out = {"id": tid, "name": t.name, "schema": t.schema, "kind": t.kind, "cite": t.cite, "comment": t.comment,
               "columns": t.columns, "primary_key": t.pk, "uniques": t.uniques, "indexes": t.indexes, "foreign_keys": fks_out,
               "checks": t.checks, "changes": t.changes}
        if t.kind != "table":
            out["definition"] = t.definition
            out["uses"] = uses
        tables.append(out)
    return {"tables": tables, "relations": relations, "files": s.files, "skipped": s.skipped}


def parse(files: list[tuple[str, str]]) -> dict:
    s = Schema()
    for rel, text in files:
        text = strip_down_sections(text)
        s.start_file(rel, text)
        for toks in tokens(text):
            try:
                statement(s, toks, text)
            except Exception:                                 # noqa: BLE001 - one odd statement never fails the map
                s.skipped += 1
    return result(s)
