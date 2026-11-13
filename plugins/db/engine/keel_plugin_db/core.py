"""The Database plugin's core: connections, what a query does, running it safely, the schema, and suggestions.

A connection is {name, kind, url, env}: kind postgres | mysql | sqlite, env local | test | staging | prod. The api keeps
the url (with its password) encrypted; it reaches the engine in a call's keys and stays in memory. Since keel 0.18.38 a
key also says what agents may do with it (agent_access none | read | write_approve, ask_first) and whether it is a
project's own or a global one (scope); one keel cannot reach now comes with `unavailable` instead of a url.
Access `private` (#96): agents run reads, but the rows go only to the person (private.py keeps them).

    classify(sql, kind)      read | change | schema | never, and why (sqlglot reads the SQL; no word search)
    query(conn, sql, ...)    a read runs in a read-only transaction (200 rows, 15 s); a change only on a local or test
                             database: without `confirm` it runs and rolls back to count the rows, with it for real
    schema(conn)             tables, columns, primary and foreign keys (at most 300: truncated and total say so)
    test(conn)               can keel connect, which server, how many tables, in how many ms
    databases(conn)          the databases on the server (the editor's Default database)
    suggest(root)            connections the project names: docker compose, .env.example, Spring's config, SQLite files

    connections(keys)        the connections a call carries (the api sends each as the key `db:<name>`)
    PluginError              keel's refusal of a part (status, message, hint): the app answers it as a 4xx

keel runs in Docker: `localhost` in a url means the person's computer, so the engine connects to host.docker.internal.

keel's rules are a second line, not the first: give each connection a least-privilege database role (a read-only user
for reads, no superuser, no file or server functions), so the database itself refuses what keel's checks might miss.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import sqlglot
import yaml
from sqlglot import exp

from keel_engine.extensions import PartError as PluginError

logging.getLogger("sqlglot").setLevel(logging.ERROR)

KINDS = {"postgres": "postgres", "postgresql": "postgres", "mysql": "mysql", "mariadb": "mysql", "sqlite": "sqlite",
         "sqlite3": "sqlite"}
ENVS = ("local", "test", "staging", "prod")
CHANGE_ENVS = ("local", "test")
MAX_ROWS = 200
TIMEOUT_S = 15
CONNECT_S = 5
MAX_TABLES = 300
SECRET_COL = re.compile(r"pass(word|wd)?|secret|token|hash|salt|api_?key|private_?key|credential", re.I)
# functions that act even inside a read-only transaction (end sessions, read server files, reach other servers) and
# those that run a string as SQL (query_to_xml, cursor_to_xml, ts_stat, dblink …): keel 0.18.55 refuses them by name in
# the parsed statement and anywhere in its text, so a name inside a string literal (SQL another function runs) counts too
_DANGER_NAMES = (r"pg_(terminate|cancel)_backend|pg_reload_conf|pg_rotate_logfile|pg_read_(binary_)?file|pg_ls_\w+|"
                 r"pg_stat_file|pg_promote|pg_file_\w+|pg_logdir_ls|lo_\w+|dblink\w*|set_config|"
                 r"(query|cursor)_to_xml\w*|(table|schema|database)_to_xml\w*|ts_stat|ts_rewrite|"
                 r"load_file|sys_(exec|eval)")
DANGER_FN = re.compile(rf"^({_DANGER_NAMES})$", re.I)
DANGER_CALL = re.compile(rf"(?<![\w$])({_DANGER_NAMES})\s*\(", re.I)
# MySQL and MariaDB run what is inside /*! … */ and /*M! … */ (sqlglot reads them as comments); and their file access
MYSQL_EXEC_COMMENT = re.compile(r"/\*M?!")
FILE_ACCESS = re.compile(r"(?i)\bINTO\s+(OUTFILE|DUMPFILE)\b|\bLOAD\s+(DATA|XML)\b")
COMPOSE = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
ADDRESS_HINT = ("Use postgres://user:password@host:5432/db, mysql://user:password@host:3306/db "  # keel:allow-secret
                "or sqlite:path/to/file.db.")


def connections(keys: dict | None) -> dict[str, dict]:
    """{name: {name, kind, url, env, ...}} from the call's keys `db:<name>` (the api sends each connection as JSON). One
    keel cannot reach now has no url but says why (`unavailable`)."""
    out = {}
    for k, v in (keys or {}).items():
        if k.startswith("db:") and v:
            try:
                d = json.loads(v)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(d, dict) and (d.get("url") or d.get("unavailable")):
                out[k[3:]] = {**d, "name": d.get("name") or k[3:]}
    return out


class DbError(Exception):
    """keel's refusal of a database call. `safe` (#96): the message holds nothing from the data, so an agent on a
    private connection may read it; a database's own message can quote values (a bad cast, a key that exists)."""

    def __init__(self, status: int, message: str, hint: str = "", *, safe: bool = True, code: str = "", from_db: bool = False):
        super().__init__(message)
        self.status = status
        self.hint = hint
        self.safe = safe
        self.code = code
        self.from_db = from_db          # the database ran the query and refused it (else keel refused it first)


# none < private < read < write_approve: what agents may do with a connection
ACCESS = ("none", "private", "read", "write_approve")


@dataclass
class Conn:
    name: str
    kind: str
    url: str
    env: str = "local"
    driver: str = ""
    scope: str = "project"
    agent_access: str = "read"      # none | private | read | write_approve (the api already made prod read at most)
    ask_first: bool = False

    @property
    def agent_may(self) -> str:
        """What agents may really do here: staging and prod are read only for them, always (whatever the key says)."""
        access = self.agent_access if self.agent_access in ACCESS else "read"
        return "read" if access == "write_approve" and self.env not in CHANGE_ENVS else access


def kind_of(url: str) -> str | None:
    scheme = url.strip().removeprefix("jdbc:").split(":", 1)[0].split("+", 1)[0].lower()
    return KINDS.get(scheme)


def conn_of(d: dict) -> Conn:
    """A connection from the api's JSON ({name, kind?, url, env, ...}); a bad one raises DbError 400, one keel cannot reach
    now 409 with the reason."""
    if not d.get("url") and d.get("unavailable"):
        raise DbError(409, str(d["unavailable"]))
    url = str(d.get("url") or "").strip()
    kind = KINDS.get(str(d.get("kind") or "").lower()) or kind_of(url)
    if not url or not kind:
        raise DbError(400, "keel cannot read this database address.", ADDRESS_HINT)
    env = str(d.get("env") or "local").lower()
    if env not in ENVS:
        raise DbError(400, f"Unknown kind of database: {env}.", f"Use one of: {', '.join(ENVS)}.")
    return Conn(str(d.get("name") or "local"), kind, url, env, str(d.get("driver") or kind),
                str(d.get("scope") or "project"), str(d.get("agent_access") or "read"), bool(d.get("ask_first")))


_SECRET_PARAM = re.compile(r"(?i)([?&;])(password|passwd|pwd|pass|secret|token|api_?key|access_?token|sslpassword)=([^&;#]*)")
_SCHEME_RE = re.compile(r"^((?:jdbc:)?[A-Za-z][A-Za-z0-9+.\-]*://)(.*)$", re.S)
_QUERY_START = re.compile(r"[?;][A-Za-z_][\w.\-]*=")


def _userinfo_end(rest: str) -> int:
    """Where user:password ends in `rest` (the address after scheme://): the LAST @ before the query (a password may hold
    unencoded @ / ? #), or -1 when there is no password. keel 0.18.55: the first @ left a password's tail visible."""
    q = _QUERY_START.search(rest)
    at = rest.rfind("@", 0, q.start() if q else len(rest))
    if at < 0:
        return -1
    info = rest[:at]
    colon = info.find(":")
    return at if colon >= 0 and ("@" not in info or colon < info.find("@")) else -1


def masked_url(url: str) -> str:
    """The url with every secret as •••, to show: the password of user:pw@host (the password may hold any character, up to
    the last @ before the host) and the value of ?password=, &pwd=, ;secret=, token= … in the query (any case)."""
    out = url
    m = _SCHEME_RE.match(out)
    if m:
        head, rest = m.groups()
        at = _userinfo_end(rest)
        if at >= 0:
            user = rest[:at].split(":", 1)[0]
            out = f"{head}{user}:•••@{rest[at + 1:]}"
    return _SECRET_PARAM.sub(lambda g: f"{g.group(1)}{g.group(2)}=•••", out)


def _in_docker() -> bool:
    return Path("/.dockerenv").exists()


def _host(host: str | None) -> str:
    h = host or "localhost"
    return "host.docker.internal" if h in ("localhost", "127.0.0.1", "::1") and _in_docker() else h


# ------------------------------------------------------------------ what a query does

def _walk_has(node: exp.Expression, types: tuple) -> bool:
    return any(isinstance(n, types) for n in node.walk())


def _danger_fn(st: exp.Expression, text: str = "") -> str:
    """The name of a server-acting function the statement calls (in a SELECT, an UPDATE, an INSERT … anywhere), or
    names in its text (a string literal another function runs as SQL, a dollar-quoted body), or ''."""
    for fn in st.find_all(exp.Anonymous, exp.Func):
        name = fn.name if isinstance(fn, exp.Anonymous) else (fn.sql_name() if hasattr(fn, "sql_name") else "")
        if name and DANGER_FN.match(str(name)):
            return str(name)
    m = DANGER_CALL.search(text)
    return m.group(1) if m else ""


def classify(sql: str, kind: str = "postgres") -> tuple[str, str]:
    """("read" | "change" | "schema" | "never", why). One statement; keel decides from the parsed SQL."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        return "never", "The query is empty."
    dialect = _dialect(kind)
    if kind == "mysql" and MYSQL_EXEC_COMMENT.search(text):
        return "never", ("keel does not run MySQL's executable comments (/*! … */, /*M! … */): MySQL runs what is "
                         "inside them. Write the SQL without them.")
    if FILE_ACCESS.search(text):
        return "never", "keel does not read or write server files (INTO OUTFILE, INTO DUMPFILE, LOAD DATA)."
    try:
        stmts = [s for s in sqlglot.parse(text, read=dialect) if s is not None]
    except Exception as exc:  # sqlglot's ParseError, TokenError
        return "never", f"keel could not read the SQL: {str(exc).splitlines()[0][:200]}"
    if len(stmts) != 1:
        return "never", "One statement at a time: keel runs a single query."
    st = stmts[0]
    head = text.split(None, 1)[0].upper()
    bad = _danger_fn(st, text)
    if bad:
        return "never", f"keel does not call {bad}(): it acts on the server, not on data."
    if isinstance(st, exp.Command):
        if head == "EXPLAIN":
            m = re.match(r"(?is)^explain\s+(\([^)]*\)\s*)?(analy[sz]e\s+)?(verbose\s+)?(.*)$", text)
            runs = bool(m and (m.group(2) or (m.group(1) and re.search(r"analy[sz]e", m.group(1), re.I))))
            if not runs:
                return "read", ""
            inner, why = classify(m.group(4) if m else "", kind)
            return ("read", "") if inner == "read" else ("never", "EXPLAIN ANALYZE runs the statement: only for a read.")
        return "never", f"keel does not run {head} statements."
    if isinstance(st, (exp.Show, exp.Describe)):
        return "read", ""
    if isinstance(st, exp.Pragma):
        return ("read", "") if kind == "sqlite" and "=" not in text else ("never", "keel does not change SQLite's settings.")
    if isinstance(st, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)):
        if st.args.get("into"):
            return "never", "SELECT … INTO creates a table: change the schema with a migration."
        if _walk_has(st, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
            return "never", "This query reads and changes data at once: split it into a read and a change."
        return "read", ""
    if isinstance(st, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        return "change", ""
    if isinstance(st, exp.Drop) and str(st.args.get("kind") or "").upper() in ("DATABASE", "SCHEMA"):
        return "never", "keel never drops a database or a schema."
    if isinstance(st, (exp.Create, exp.Alter, exp.Drop)):
        return "schema", "Change the schema with the project's migrations (a db:migrate step), not with raw SQL."
    if isinstance(st, exp.TruncateTable):
        return "never", "keel never empties a table with TRUNCATE."
    return "never", f"keel does not run {head} statements."


def _dialect(kind: str) -> str:
    return {"postgres": "postgres", "mysql": "mysql", "sqlite": "sqlite"}.get(kind, "postgres")


def _parse(text: str, kind: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(text, read=_dialect(kind))
    except Exception:  # sqlglot's ParseError, TokenError
        return None


def _every_row(text: str, kind: str) -> bool:
    """An UPDATE or a DELETE without a WHERE: it changes every row of its table."""
    st = _parse(text, kind)
    return isinstance(st, (exp.Update, exp.Delete)) and not st.args.get("where")


def _is_star(p: exp.Expression) -> bool:
    return isinstance(p, exp.Star) or (isinstance(p, exp.Column) and isinstance(p.this, exp.Star))


def _branches(node: exp.Expression) -> list[exp.Select]:
    """The SELECTs whose columns are the result's: one, or each side of a UNION / INTERSECT / EXCEPT."""
    if isinstance(node, exp.Select):
        return [node]
    if isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
        return _branches(node.left) + _branches(node.right)
    if isinstance(node, exp.Subquery):
        return _branches(node.this)
    return []


def secret_columns(text: str, kind: str, cols: list) -> list[int]:
    """The result's columns that hold a secret: named like one, or (keel 0.18.55) computed from one under another name
    (`password_hash AS h`, `upper(token)`, a subquery's or a CTE's alias of it). keel reads the SQL; when it cannot, only
    the names count."""
    names = [str(c).lower() for c in cols]
    hidden = {i for i, c in enumerate(names) if SECRET_COL.search(c)}
    st = _parse(text, kind)
    if st is None:
        return sorted(hidden)
    tainted: set[str] = set()

    def secret(node: exp.Expression) -> bool:
        return any(SECRET_COL.search(c.name or "") or (c.name or "").lower() in tainted for c in node.find_all(exp.Column))

    grown = True
    while grown:                                  # an alias of an alias of a secret is a secret too
        grown = False
        for al in st.find_all(exp.Alias):
            a = (al.alias or "").lower()
            if a and a not in tainted and secret(al.this):
                tainted.add(a)
                grown = True
    hidden |= {i for i, c in enumerate(names) if c in tainted}
    for sel in _branches(st):
        projs = list(sel.expressions)
        positional = len(projs) == len(cols) and not any(_is_star(p) for p in projs)
        for i, p in enumerate(projs):
            if _is_star(p) or not secret(p):
                continue
            if positional:
                hidden.add(i)
            else:
                out = (p.alias_or_name or "").lower()
                hidden |= {j for j, c in enumerate(names) if c == out}
    return sorted(hidden)


# ------------------------------------------------------------------ connecting

def _keel_data() -> list[Path]:
    from keel_engine import config

    out = [Path("/data")]
    try:
        out.append(config.data_dir())
    except OSError:
        pass
    return out


def _inside(p: Path, folder: Path) -> bool:
    return p == folder or folder in p.parents


def _sqlite_path(conn: Conn, root: str) -> Path:
    """The SQLite file, only inside the project folder (after symlinks), never in keel's own data (keel 0.18.55: an
    absolute path such as /data/keel.db was keel's own database)."""
    raw = unquote(re.sub(r"^sqlite3?:(//)?", "", conn.url.strip()))
    hint = "Use a SQLite file inside the project folder, for example data/app.db."
    if not root:
        raise DbError(403, "keel opens a SQLite file only inside a project folder, and this call has no project.", hint)
    base = Path(root).resolve()
    p = Path(raw)
    real = (p if p.is_absolute() else base / p).resolve()
    if any(_inside(real, d.resolve()) for d in _keel_data()):
        raise DbError(403, "keel never opens its own data folder (keel's own database) as a connection.", hint)
    if not _inside(real, base):
        raise DbError(403, f"The SQLite file {raw} is not inside the project folder.", hint)
    return real


def _connect(conn: Conn, root: str, *, read: bool):
    if conn.kind == "postgres":
        import psycopg

        p = urlsplit(conn.url.removeprefix("jdbc:"))
        q = _params(p.query)
        host = q.get("host") if str(q.get("host") or "").startswith("/") else _host(p.hostname)   # a socket folder
        user, password = _login(p, q)
        c = _within(lambda: psycopg.connect(
            host=host, port=p.port or 5432, user=user or None, password=password or None,
            dbname=unquote((p.path or "/").lstrip("/")) or None, connect_timeout=max(1, round(CONNECT_S)), application_name="keel",
            **({"sslmode": q["sslmode"]} if q.get("sslmode") else {})))
        c.read_only = read
        with c.cursor() as cur:
            cur.execute(f"SET statement_timeout = {TIMEOUT_S * 1000}")
        return c
    if conn.kind == "mysql":
        import pymysql

        p = urlsplit(conn.url.removeprefix("jdbc:"))
        q = _params(p.query)
        sock = q.get("unix_socket") or q.get("socket")
        user, password = _login(p, q)
        c = _within(lambda: pymysql.connect(
            host=_host(p.hostname), port=p.port or 3306, user=user or None, password=password,
            database=unquote((p.path or "/").lstrip("/")) or None, **({"unix_socket": sock} if sock else {}),
            connect_timeout=max(1, round(CONNECT_S)), read_timeout=TIMEOUT_S + 5, write_timeout=TIMEOUT_S + 5,
            autocommit=False, charset="utf8mb4"))
        with c.cursor() as cur:
            for stmt in ("SET SESSION max_execution_time = %d" % (TIMEOUT_S * 1000),
                         "SET SESSION max_statement_time = %d" % TIMEOUT_S):   # MySQL, MariaDB
                try:
                    cur.execute(stmt)
                except Exception:
                    pass
            cur.execute("START TRANSACTION READ ONLY" if read else "START TRANSACTION")
        return c
    path = _sqlite_path(conn, root)
    if not path.is_file():
        raise DbError(404, f"There is no SQLite file at {path}.", "Check the path; it is relative to the project folder.")
    c = sqlite3.connect(f"file:{path}?mode={'ro' if read else 'rw'}", uri=True, timeout=CONNECT_S)
    deadline = time.monotonic() + TIMEOUT_S
    c.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    return c


def _login(p, q: dict[str, str]) -> tuple[str, str]:
    """The user and the password: from user:password@ in the address, else from ?user=&password= (a JDBC address)."""
    user = unquote(p.username) if p.username else q.get("user", "")
    password = unquote(p.password) if p.password else q.get("password", "")
    return user, password


def _within(connect):
    """The driver's connect, cut at keel's own timeout (CONNECT_S, the one the api and the editor say): a driver may try
    each address of a host, each with its own timeout. A connection that comes too late is closed."""
    box: dict = {}
    lock = threading.Lock()
    done = threading.Event()

    def run():
        try:
            c = connect()
        except BaseException as exc:  # noqa: BLE001 - handed to the caller
            box["error"] = exc
        else:
            with lock:
                late = box.get("late")
                box["conn"] = c
            if late:
                try:
                    c.close()
                except Exception:
                    pass
        finally:
            done.set()

    threading.Thread(target=run, name="keel-db-connect", daemon=True).start()
    if not done.wait(CONNECT_S + 0.5):
        with lock:
            box["late"] = True
            c = box.get("conn")
        if c is None:
            raise TimeoutError(f"no answer within {CONNECT_S:g} s")
    if "error" in box:
        raise box["error"]
    return box["conn"]


def _params(query: str) -> dict[str, str]:
    from urllib.parse import parse_qsl

    return {k: v for k, v in parse_qsl(query or "", keep_blank_values=True)}


def scrub(text: str, url: str) -> str:
    """The text without the address's password: every address in it masked, and the password itself as •••."""
    out = re.sub(r"(?:jdbc:)?[A-Za-z][A-Za-z0-9+.\-]*://\S+", lambda m: masked_url(m.group(0)), text or "")
    try:
        p = urlsplit((url or "").removeprefix("jdbc:"))
        pw = unquote(p.password or "") or _params(p.query).get("password") or ""
    except ValueError:
        pw = ""
    return out.replace(pw, "•••") if len(pw) >= 3 else out


def _connect_error(conn: Conn, exc: Exception) -> DbError:
    msg = scrub(str(exc).strip().splitlines()[0][:300], conn.url) if str(exc).strip() else type(exc).__name__
    hint = "Check the address, the user and the password in Connections › Databases, and that the database runs."
    if _in_docker() and conn.kind != "sqlite":
        hint += " keel runs in Docker: localhost means your computer (host.docker.internal)."
    return DbError(502, f"keel could not reach {conn.name}: {msg}", hint)


def _duration(v: _dt.timedelta, kind: str = "") -> str:
    """An interval as ISO-8601 (P1DT2H3M4.5S); MySQL's TIME as MySQL writes it (-10:30:00)."""
    us = v // _dt.timedelta(microseconds=1)
    sign, us = ("-" if us < 0 else ""), abs(us)
    if kind == "mysql":
        h, rest = divmod(us, 3_600_000_000)
        m, rest = divmod(rest, 60_000_000)
        s, frac = divmod(rest, 1_000_000)
        return f"{sign}{h:02d}:{m:02d}:{s:02d}" + (f".{frac:06d}".rstrip("0") if frac else "")
    days, rest = divmod(us, 86_400_000_000)
    h, rest = divmod(rest, 3_600_000_000)
    m, rest = divmod(rest, 60_000_000)
    secs = rest / 1_000_000
    t = "".join(f"{n}{u}" for n, u in ((h, "H"), (m, "M")) if n) + (f"{secs:.6f}".rstrip("0").rstrip(".") + "S" if rest else "")
    out = f"P{days}D" if days else "P"
    if t:
        out += "T" + t
    return sign + (out if out != "P" else "PT0S")


def _value(v, kind: str = ""):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (decimal.Decimal, uuid.UUID)):
        return str(v)
    if isinstance(v, (dict, list)):                      # json and jsonb (and arrays) as JSON, not Python's repr
        return json.dumps(v, ensure_ascii=False, default=str)
    if isinstance(v, _dt.timedelta):
        return _duration(v, kind)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat(sep=" ") if isinstance(v, _dt.datetime) else v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    return str(v)


_SERVER_SIDE = re.compile(r"(?is)^[\s(]*(select|with|values|table)\b")


def _cursor(c, conn: Conn, sql: str, stream: bool):
    """A cursor that does not pull the whole result into memory before keel takes its first `limit` rows: the drivers
    buffer everything on execute by default (psycopg, pymysql), so a read streams: a server-side cursor on PostgreSQL
    (SELECT, WITH, VALUES, TABLE only), an unbuffered one on MySQL. SQLite already reads row by row."""
    if stream and conn.kind == "postgres" and _SERVER_SIDE.match(sql):
        return c.cursor(name="keel_read")
    if stream and conn.kind == "mysql":
        import pymysql.cursors

        return c.cursor(pymysql.cursors.SSCursor)
    return c.cursor()


def _run(c, conn: Conn, sql: str, limit: int, *, stream: bool = False) -> tuple[list[str], list[list], bool, int]:
    cur = _cursor(c, conn, sql, stream)
    try:
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchmany(limit + 1)] if cols else []
        return cols, rows[:limit], len(rows) > limit, cur.rowcount
    finally:
        cur.close()


# ------------------------------------------------------------------ running

def query(conn: Conn, sql: str, *, root: str = "", allow_change: bool = False, confirm: bool = False, mask: bool = False,
          limit: int = MAX_ROWS, max_rows: int = MAX_ROWS) -> dict:
    """Run one query. A read: {columns, rows, count, truncated}. A change (allow_change, a local or test database):
    {changed, done}; without `confirm` keel runs it in a transaction it rolls back, to count the rows. `max_rows`: a
    private result (only the person reads it) may hold more rows than a model gets."""
    cls, why = classify(sql, conn.kind)
    text = (sql or "").strip().rstrip(";").strip()
    out = {"connection": conn.name, "env": conn.env, "kind": cls, "sql": text}
    if cls in ("never", "schema"):
        raise DbError(400, why, "keel runs reads, and changes of data on a local or test database.")
    if cls == "change":
        if not allow_change:
            raise DbError(403, "This query changes data, and here keel only reads.",
                          "KeelBot gives a change as a button; run it from there or from Map › Query.")
        if conn.env not in CHANGE_ENVS:
            raise DbError(403, f"{conn.name} is a {conn.env} database: keel only reads there.",
                          "Changes of data run only on a local or test database.")
    t0 = time.monotonic()
    try:
        c = _connect(conn, root, read=cls == "read")
    except DbError:
        raise
    except Exception as exc:
        raise _connect_error(conn, exc) from exc
    limit = max(1, min(limit, max_rows))
    try:
        cols, rows, truncated, count = _run(c, conn, text, limit, stream=cls == "read")
        if cls == "change":
            (c.commit if confirm else c.rollback)()
        else:
            c.rollback()
    except Exception as exc:
        try:
            c.rollback()
        except Exception:
            pass
        msg = str(exc).strip().splitlines()[0][:400] if str(exc).strip() else type(exc).__name__
        if "interrupted" in msg.lower() or "statement timeout" in msg.lower() or "max_execution_time" in msg.lower():
            raise DbError(408, f"The query took longer than {TIMEOUT_S} seconds.", "Add a WHERE or a LIMIT.") from exc
        code, safe = sql_error(conn.kind, exc)
        raise DbError(400, f"The database refused the query: {scrub(msg, conn.url)}", safe=safe, code=code,
                      from_db=True) from exc
    finally:
        c.close()
    ms = int((time.monotonic() - t0) * 1000)
    if cls == "change":
        return {**out, "changed": max(count, 0), "done": confirm, "ms": ms, "every_row": _every_row(text, conn.kind)}
    hidden = secret_columns(text, conn.kind, cols) if mask else []
    shown = [[("•••" if i in hidden and v is not None else _value(v, conn.kind)) for i, v in enumerate(r)] for r in rows]
    return {**out, "columns": cols, "rows": shown, "count": len(shown), "truncated": truncated, "masked": [cols[i] for i in hidden],
            "limit": limit, "ms": ms}


# #96 a database's error messages that name only what the query itself says (a table, a column, a function, the syntax
# near a word): an agent on a private connection may read these. Every other message can quote a value from the data
# ("invalid input syntax for type integer: \"ann@example.com\"", "Truncated incorrect DOUBLE value: 'ann'").
PG_SAFE = ("42", "25006")                   # syntax error or access rule violation; read-only transaction
MYSQL_SAFE = {1044, 1046, 1049, 1051, 1052, 1054, 1064, 1109, 1142, 1143, 1146, 1149, 1222, 1241, 1248, 1305, 1630, 1792}
SQLITE_SAFE = ("no such table", "no such column", "no such function", "near \"", "ambiguous column name", "incomplete input",
               "unrecognized token", "wrong number of arguments", "misuse of aggregate", "attempt to write a readonly")


def sql_error(kind: str, exc: Exception) -> tuple[str, bool]:
    """(the database's error code, whether its message is safe for an agent that may not see the data)."""
    if kind == "postgres":
        code = str(getattr(exc, "sqlstate", "") or "")
        return code, bool(code) and code.startswith(PG_SAFE)
    if kind == "mysql":
        n = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        return (str(n) if n is not None else ""), n in MYSQL_SAFE
    msg = str(exc).strip().lower()
    return getattr(exc, "sqlite_errorname", "") or "", msg.startswith(SQLITE_SAFE)


def test(conn: Conn, root: str = "") -> dict:
    """{ok, server, tables, ms} or {ok: false, error, hint, ms}."""
    t0 = time.monotonic()
    ms = lambda: int((time.monotonic() - t0) * 1000)  # noqa: E731
    try:
        c = _connect(conn, root, read=True)
    except DbError as exc:
        return {"ok": False, "error": str(exc), "hint": exc.hint, "ms": ms()}
    except Exception as exc:
        e = _connect_error(conn, exc)
        return {"ok": False, "error": str(e), "hint": e.hint, "ms": ms()}
    try:
        server = {"postgres": "SHOW server_version", "mysql": "SELECT VERSION()", "sqlite": "SELECT sqlite_version()"}[conn.kind]
        _, rows, _, _ = _run(c, conn, server, 1)
        version = str(rows[0][0]) if rows else ""
        c.rollback()
    except Exception as exc:
        return {"ok": False, "error": scrub(str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__,
                                            conn.url), "hint": "", "ms": ms()}
    finally:
        c.close()
    try:
        sch = schema(conn, root)                     # a broken SQLite file fails here, not on connect
    except DbError as exc:
        return {"ok": False, "error": str(exc), "hint": exc.hint, "ms": ms()}
    tables = sch.get("total", len(sch["tables"]))
    label = {"postgres": "PostgreSQL", "mysql": "MySQL", "sqlite": "SQLite"}[conn.kind]
    if conn.kind == "mysql" and ("mariadb" in version.lower() or conn.driver == "mariadb"):
        label = "MariaDB"
    return {"ok": True, "server": f"{label} {version.split()[0].split('-')[0] if version else ''}".strip(), "tables": tables,
            "ms": ms()}


SYSTEM_DBS = {"information_schema", "mysql", "performance_schema", "sys"}


def databases(conn: Conn, root: str = "") -> dict:
    """{databases: [{name}]}: the databases on the server, without the server's own (the editor's Default database). A
    PostgreSQL address without a database connects to `postgres` to read the list."""
    if conn.kind == "sqlite":
        return {"databases": [{"name": "main"}]}
    c_conn = conn
    if conn.kind == "postgres" and not urlsplit(conn.url.removeprefix("jdbc:")).path.strip("/"):
        p = urlsplit(conn.url.removeprefix("jdbc:"))
        c_conn = Conn(conn.name, conn.kind, urlunsplit((p.scheme, p.netloc, "/postgres", p.query, p.fragment)), conn.env)
    try:
        c = _connect(c_conn, root, read=True)
    except DbError:
        raise
    except Exception as exc:
        raise _connect_error(conn, exc) from exc
    try:
        sql = ("SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY 1" if conn.kind == "postgres"
               else "SHOW DATABASES")
        _, rows, _, _ = _run(c, c_conn, sql, 1000)
        c.rollback()
    except Exception as exc:
        raise DbError(400, f"keel could not list the databases: {scrub(str(exc).splitlines()[0][:300], conn.url)}") from exc
    finally:
        c.close()
    return {"databases": [{"name": str(r[0])} for r in rows if str(r[0]) not in SYSTEM_DBS]}


def schema(conn: Conn, root: str = "") -> dict:
    """{connection, kind, tables: [{name, columns: [{name, type, nullable, pk}], fks: [{column, table, ref}]}]}."""
    try:
        c = _connect(conn, root, read=True)
    except DbError:
        raise
    except Exception as exc:
        raise _connect_error(conn, exc) from exc
    tables: dict[str, dict] = {}
    total = 0

    def table(name: str) -> dict:
        return tables.setdefault(name, {"name": name, "columns": [], "fks": []})

    try:
        if conn.kind == "sqlite":
            user_tables = "FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            _, rows, _, _ = _run(c, conn, f"SELECT name {user_tables} ORDER BY name", MAX_TABLES)
            _, n, _, _ = _run(c, conn, f"SELECT count(*) {user_tables}", 1)
            total = int(n[0][0]) if n else len(rows)
            for (name,) in rows:
                t = table(name)
                q = name.replace('"', '""')
                _, cols, _, _ = _run(c, conn, f'PRAGMA table_info("{q}")', 500)
                t["columns"] = [{"name": r[1], "type": r[2], "nullable": not r[3], "pk": bool(r[5])} for r in cols]
                _, fks, _, _ = _run(c, conn, f'PRAGMA foreign_key_list("{q}")', 200)
                t["fks"] = [{"column": r[3], "table": r[2], "ref": r[4]} for r in fks]
        else:
            where = ("c.table_schema NOT IN ('pg_catalog', 'information_schema')" if conn.kind == "postgres"
                     else "c.table_schema = DATABASE()")
            # keel 0.18.55: the project's own schemas first (PostgreSQL's search_path, `public`; MySQL reads only the
            # connected database), so a cut at MAX_TABLES drops other schemas' tables, not the project's
            path: list[str] = []
            if conn.kind == "postgres":
                _, sp, _, _ = _run(c, conn, "SELECT current_schemas(false)", 1)
                path = [str(x) for x in (sp[0][0] if sp and sp[0] and sp[0][0] else [])] or ["public"]
            first = ("array_position(current_schemas(false), c.table_schema::name) NULLS LAST, " if conn.kind == "postgres"
                     else "")
            _, cols, _, _ = _run(c, conn, "SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.is_nullable "
                                          "FROM information_schema.columns c JOIN information_schema.tables t "
                                          "ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                                          f"WHERE {where} AND t.table_type = 'BASE TABLE' "
                                          f"ORDER BY {first}c.table_schema, c.table_name, c.ordinal_position", 20_000)
            rank = {s_: i for i, s_ in enumerate(path)}
            cols = sorted(cols, key=lambda r: (rank.get(r[0], len(rank)), r[0], r[1]))     # stable: column order stays
            twhere = where.replace("c.table_schema", "t.table_schema")
            _, n, _, _ = _run(c, conn, f"SELECT count(*) FROM information_schema.tables t WHERE {twhere} "
                                       "AND t.table_type = 'BASE TABLE'", 1)
            total = int(n[0][0]) if n else len({(r[0], r[1]) for r in cols})

            def tname(schema_name: str, name: str) -> str:
                return name if conn.kind == "mysql" or schema_name == "public" else f"{schema_name}.{name}"

            for sch, name, col, typ, nullable in cols:
                if len(tables) >= MAX_TABLES and tname(sch, name) not in tables:
                    continue
                table(tname(sch, name))["columns"].append({"name": col, "type": typ, "nullable": nullable == "YES", "pk": False})
            kwhere = where.replace("c.table_schema", "tc.table_schema")
            _, pks, _, _ = _run(c, conn, "SELECT tc.table_schema, tc.table_name, k.column_name FROM information_schema.table_constraints tc "
                                         "JOIN information_schema.key_column_usage k ON k.constraint_name = tc.constraint_name "
                                         "AND k.table_schema = tc.table_schema AND k.table_name = tc.table_name "
                                         f"WHERE tc.constraint_type = 'PRIMARY KEY' AND {kwhere}", 20_000)
            for sch, name, col in pks:
                for cdef in tables.get(tname(sch, name), {}).get("columns", []):
                    if cdef["name"] == col:
                        cdef["pk"] = True
            if conn.kind == "postgres":
                fk_sql = ("SELECT tc.table_schema, tc.table_name, k.column_name, u.table_schema, u.table_name, u.column_name "
                          "FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage k "
                          "ON k.constraint_name = tc.constraint_name AND k.table_schema = tc.table_schema "
                          "JOIN information_schema.constraint_column_usage u ON u.constraint_name = tc.constraint_name "
                          f"AND u.constraint_schema = tc.table_schema WHERE tc.constraint_type = 'FOREIGN KEY' AND {kwhere}")
            else:
                fk_sql = ("SELECT k.table_schema, k.table_name, k.column_name, k.referenced_table_schema, k.referenced_table_name, "
                          "k.referenced_column_name FROM information_schema.key_column_usage k "
                          "WHERE k.referenced_table_name IS NOT NULL AND k.table_schema = DATABASE()")
            _, fks, _, _ = _run(c, conn, fk_sql, 20_000)
            for sch, name, col, rsch, rname, rcol in fks:
                if tname(sch, name) in tables:
                    tables[tname(sch, name)]["fks"].append({"column": col, "table": tname(rsch, rname), "ref": rcol})
        c.rollback()
    except DbError:
        raise
    except Exception as exc:
        raise DbError(400, f"keel could not read the schema: {str(exc).splitlines()[0][:300]}") from exc
    finally:
        c.close()
    total = max(total, len(tables))
    return {"connection": conn.name, "kind": conn.kind, "tables": list(tables.values()), "truncated": total > len(tables),
            "total": total}


# ------------------------------------------------------------------ suggestions

def _env_list(env) -> dict[str, str]:
    if isinstance(env, dict):
        return {str(k): "" if v is None else str(v) for k, v in env.items()}
    out = {}
    for item in env or []:
        k, _, v = str(item).partition("=")
        out[k.strip()] = v.strip()
    return out


def _default(v: str | None) -> str:
    """`${DB_PASSWORD:-secret}` / `${DB_PASSWORD:secret}` → secret; another `${...}` → ""."""
    v = str(v or "")
    m = re.fullmatch(r"\$\{[^}:]+:-?([^}]*)\}", v)
    if m:
        return m.group(1)
    return "" if "${" in v else v


def _published(ports, inner: int) -> int | None:
    for p in ports or []:
        s = str(p.get("published") if isinstance(p, dict) else p)
        target = str(p.get("target")) if isinstance(p, dict) else s.rsplit(":", 1)[-1]
        if target.split("/")[0] == str(inner):
            host = s.split(":")[-2] if s.count(":") >= 1 and not isinstance(p, dict) else s
            try:
                return int(str(host).split("/")[0])
            except ValueError:
                return None
    return None


def _from_compose(root: Path) -> list[dict]:
    out = []
    for name in COMPOSE:
        f = root / name
        if not f.is_file():
            continue
        try:
            doc = yaml.safe_load(f.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        for svc, d in ((doc or {}).get("services") or {}).items():
            image = str((d or {}).get("image") or "").lower()
            env = _env_list((d or {}).get("environment"))
            if "postgres" in image or "postgis" in image or "timescale" in image:
                port = _published(d.get("ports"), 5432)
                if not port:
                    continue
                user = _default(env.get("POSTGRES_USER")) or "postgres"
                pw = _default(env.get("POSTGRES_PASSWORD"))
                db = _default(env.get("POSTGRES_DB")) or user
                auth = f"{user}:{pw}" if pw else user
                out.append({"name": svc, "kind": "postgres", "url": f"postgres://{auth}@localhost:{port}/{db}",
                            "source": f"{name} (service {svc})", "password": bool(pw)})
            elif "mysql" in image or "mariadb" in image:
                port = _published(d.get("ports"), 3306)
                if not port:
                    continue
                user = _default(env.get("MYSQL_USER") or env.get("MARIADB_USER")) or "root"
                pw = _default(env.get("MYSQL_PASSWORD") or env.get("MARIADB_PASSWORD")) if user != "root" else \
                    _default(env.get("MYSQL_ROOT_PASSWORD") or env.get("MARIADB_ROOT_PASSWORD"))
                db = _default(env.get("MYSQL_DATABASE") or env.get("MARIADB_DATABASE"))
                auth = f"{user}:{pw}" if pw else user
                out.append({"name": svc, "kind": "mysql", "url": f"mysql://{auth}@localhost:{port}/{db}",
                            "source": f"{name} (service {svc})", "password": bool(pw)})
    return out


def _jdbc(url: str, user: str, pw: str) -> str | None:
    m = re.match(r"jdbc:(postgresql|mysql|mariadb)://([^/?]+)/([^?;]+)", url.strip())
    if not m:
        return None
    scheme = "postgres" if m.group(1) == "postgresql" else "mysql"
    auth = f"{user}:{pw}@" if user and pw else (f"{user}@" if user else "")
    return f"{scheme}://{auth}{m.group(2)}/{m.group(3)}"


def _from_files(root: Path) -> list[dict]:
    out = []
    for name in (".env.example", ".env.sample", ".env.template"):
        f = root / name
        if not f.is_file():
            continue
        vals = {}
        for line in f.read_text(errors="replace").splitlines():
            k, sep, v = line.partition("=")
            if sep and not k.strip().startswith("#"):
                vals[k.strip()] = v.strip().strip('"').strip("'")
        for key in ("DATABASE_URL", "DB_URL", "POSTGRES_URL", "MYSQL_URL"):
            if kind_of(vals.get(key, "")):
                out.append({"name": "local", "kind": kind_of(vals[key]), "url": vals[key], "source": f"{name} ({key})",
                            "password": bool(urlsplit(vals[key]).password)})
        if vals.get("SPRING_DATASOURCE_URL"):
            u = _jdbc(vals["SPRING_DATASOURCE_URL"], vals.get("SPRING_DATASOURCE_USERNAME", ""), vals.get("SPRING_DATASOURCE_PASSWORD", ""))
            if u:
                out.append({"name": "local", "kind": kind_of(u), "url": u, "source": f"{name} (SPRING_DATASOURCE_URL)",
                            "password": bool(urlsplit(u).password)})
    for f in sorted(root.glob("**/src/main/resources/application*.y*ml"))[:6] + sorted(root.glob("**/src/main/resources/application*.properties"))[:6]:
        if "node_modules" in f.parts or "build" in f.parts:
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if f.suffix == ".properties":
            props = dict(re.findall(r"^\s*spring\.datasource\.(url|username|password)\s*=\s*(.*?)\s*$", text, re.M))
        else:
            try:
                doc = yaml.safe_load(text) or {}
            except yaml.YAMLError:
                continue
            props = ((doc.get("spring") or {}).get("datasource") or {}) if isinstance(doc, dict) else {}
        u = _jdbc(_default(props.get("url")), _default(props.get("username")), _default(props.get("password"))) if props.get("url") else None
        if u:
            out.append({"name": "local", "kind": kind_of(u), "url": u, "source": f"{f.relative_to(root)} (spring.datasource)",
                        "password": bool(urlsplit(u).password)})
    skip = {"node_modules", ".git", ".keel", ".codegraph", "build", "target", ".venv", "venv", "dist"}   # keel's own index
    found = 0
    for f in sorted(root.rglob("*")):
        if found >= 3 or len(f.relative_to(root).parts) > 4:
            continue
        if f.suffix in (".db", ".sqlite", ".sqlite3") and f.is_file() and not (set(f.relative_to(root).parts) & skip):
            out.append({"name": f.stem, "kind": "sqlite", "url": f"sqlite:{f.relative_to(root).as_posix()}",
                        "source": f.relative_to(root).as_posix(), "password": False})
            found += 1
    return out


def suggest(root: str) -> list[dict]:
    """The databases the project names, newest-looking first; each {name, kind, url, env: local, source, password}.
    keel reads compose files, .env.example and Spring's config, never .env (the person's secrets)."""
    r = Path(root)
    if not r.is_dir():
        return []
    seen, out = set(), []
    for s in _from_compose(r) + _from_files(r):
        key = (s["kind"], masked_url(s["url"]))
        if key not in seen:
            seen.add(key)
            out.append({**s, "env": "local", "shown": masked_url(s["url"])})
    return out
