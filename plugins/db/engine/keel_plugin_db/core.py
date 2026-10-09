"""The Database plugin's core: connections, what a query does, running it safely, the schema, and suggestions.

A connection is {name, kind, url, env}: kind postgres | mysql | sqlite, env local | test | staging | prod. The api keeps
the url (with its password) encrypted; it reaches the engine in a call's keys and stays in memory.

    classify(sql, kind)      read | change | schema | never, and why (sqlglot reads the SQL; no word search)
    query(conn, sql, ...)    a read runs in a read-only transaction (200 rows, 15 s); a change only on a local or test
                             database: without `confirm` it runs and rolls back to count the rows, with it for real
    schema(conn)             tables, columns, primary and foreign keys
    test(conn)               can keel connect, which server, how many tables
    suggest(root)            connections the project names: docker compose, .env.example, Spring's config, SQLite files

    connections(keys)        the connections a call carries (the api sends each as the key `db:<name>`)
    PluginError              keel's refusal of a part (status, message, hint): the app answers it as a 4xx

keel runs in Docker: `localhost` in a url means the person's computer, so the engine connects to host.docker.internal.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import json
import logging
import re
import sqlite3
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
# functions that act even inside a read-only transaction (end sessions, read server files, reach other servers)
DANGER_FN = re.compile(r"^(pg_(terminate|cancel)_backend|pg_reload_conf|pg_rotate_logfile|pg_read_(binary_)?file|pg_ls_dir|"
                       r"pg_stat_file|pg_promote|lo_(import|export)|dblink\w*|set_config|load_file|sys_(exec|eval))$", re.I)
COMPOSE = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
ADDRESS_HINT = ("Use postgres://user:password@host:5432/db, mysql://user:password@host:3306/db "  # keel:allow-secret
                "or sqlite:path/to/file.db.")


def connections(keys: dict | None) -> dict[str, dict]:
    """{name: {name, kind, url, env}} from the call's keys `db:<name>` (the api sends each connection as JSON)."""
    out = {}
    for k, v in (keys or {}).items():
        if k.startswith("db:") and v:
            try:
                d = json.loads(v)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(d, dict) and d.get("url"):
                out[k[3:]] = {**d, "name": d.get("name") or k[3:]}
    return out


class DbError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status = status
        self.hint = hint


@dataclass
class Conn:
    name: str
    kind: str
    url: str
    env: str = "local"


def kind_of(url: str) -> str | None:
    scheme = url.strip().removeprefix("jdbc:").split(":", 1)[0].split("+", 1)[0].lower()
    return KINDS.get(scheme)


def conn_of(d: dict) -> Conn:
    """A connection from the api's JSON ({name, kind?, url, env}); a bad one raises DbError 400."""
    url = str(d.get("url") or "").strip()
    kind = KINDS.get(str(d.get("kind") or "").lower()) or kind_of(url)
    if not url or not kind:
        raise DbError(400, "keel cannot read this database address.", ADDRESS_HINT)
    env = str(d.get("env") or "local").lower()
    if env not in ENVS:
        raise DbError(400, f"Unknown kind of database: {env}.", f"Use one of: {', '.join(ENVS)}.")
    return Conn(str(d.get("name") or "local"), kind, url, env)


def masked_url(url: str) -> str:
    """The url with its password as •••, to show."""
    p = urlsplit(url.removeprefix("jdbc:"))
    if p.password:
        host = p.hostname or ""
        netloc = f"{p.username}:•••@{host}" + (f":{p.port}" if p.port else "")
        return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))
    return url


def _in_docker() -> bool:
    return Path("/.dockerenv").exists()


def _host(host: str | None) -> str:
    h = host or "localhost"
    return "host.docker.internal" if h in ("localhost", "127.0.0.1", "::1") and _in_docker() else h


# ------------------------------------------------------------------ what a query does

def _walk_has(node: exp.Expression, types: tuple) -> bool:
    return any(isinstance(n, types) for n in node.walk())


def classify(sql: str, kind: str = "postgres") -> tuple[str, str]:
    """("read" | "change" | "schema" | "never", why). One statement; keel decides from the parsed SQL."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        return "never", "The query is empty."
    dialect = {"postgres": "postgres", "mysql": "mysql", "sqlite": "sqlite"}.get(kind, "postgres")
    try:
        stmts = [s for s in sqlglot.parse(text, read=dialect) if s is not None]
    except Exception as exc:  # sqlglot's ParseError, TokenError
        return "never", f"keel could not read the SQL: {str(exc).splitlines()[0][:200]}"
    if len(stmts) != 1:
        return "never", "One statement at a time: keel runs a single query."
    st = stmts[0]
    head = text.split(None, 1)[0].upper()
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
        for fn in st.find_all(exp.Anonymous, exp.Func):
            name = fn.name if isinstance(fn, exp.Anonymous) else (fn.sql_name() if hasattr(fn, "sql_name") else "")
            if name and DANGER_FN.match(str(name)):
                return "never", f"keel does not call {name}(): it acts on the server, not on data."
        return "read", ""
    if isinstance(st, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        return "change", ""
    if isinstance(st, exp.Drop) and str(st.args.get("kind") or "").upper() in ("DATABASE", "SCHEMA"):
        return "never", "keel never drops a database or a schema."
    if isinstance(st, (exp.Create, exp.Alter, exp.Drop)):
        return "schema", "Change the schema with the project's migrations (a db:migrate step), not with raw SQL."
    if isinstance(st, exp.TruncateTable):
        return "never", "keel never empties a table with TRUNCATE."
    return "never", f"keel does not run {type(st).__name__.upper()} statements."


# ------------------------------------------------------------------ connecting

def _sqlite_path(conn: Conn, root: str) -> Path:
    raw = re.sub(r"^sqlite3?:(//)?", "", conn.url.strip())
    p = Path(unquote(raw))
    if not p.is_absolute():
        p = Path(root or ".") / raw
    return p


def _connect(conn: Conn, root: str, *, read: bool):
    if conn.kind == "postgres":
        import psycopg

        p = urlsplit(conn.url.removeprefix("jdbc:"))
        c = psycopg.connect(host=_host(p.hostname), port=p.port or 5432, user=unquote(p.username or "") or None,
                            password=unquote(p.password or "") or None, dbname=(p.path or "/").lstrip("/") or None,
                            connect_timeout=CONNECT_S, application_name="keel")
        c.read_only = read
        with c.cursor() as cur:
            cur.execute(f"SET statement_timeout = {TIMEOUT_S * 1000}")
        return c
    if conn.kind == "mysql":
        import pymysql

        p = urlsplit(conn.url.removeprefix("jdbc:"))
        c = pymysql.connect(host=_host(p.hostname), port=p.port or 3306, user=unquote(p.username or "") or None,
                            password=unquote(p.password or ""), database=(p.path or "/").lstrip("/") or None,
                            connect_timeout=CONNECT_S, read_timeout=TIMEOUT_S + 5, write_timeout=TIMEOUT_S + 5,
                            autocommit=False, charset="utf8mb4")
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


def _connect_error(conn: Conn, exc: Exception) -> DbError:
    msg = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__
    hint = "Check the address, the user and the password in Connections › Databases, and that the database runs."
    if _in_docker() and conn.kind != "sqlite":
        hint += " keel runs in Docker: localhost means your computer (host.docker.internal)."
    return DbError(502, f"keel could not reach {conn.name}: {msg}", hint)


def _value(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (decimal.Decimal, uuid.UUID)):
        return str(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat(sep=" ") if isinstance(v, _dt.datetime) else v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    return str(v)


def _run(c, conn: Conn, sql: str, limit: int) -> tuple[list[str], list[list], bool, int]:
    cur = c.cursor()
    try:
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchmany(limit + 1)] if cols else []
        return cols, rows[:limit], len(rows) > limit, cur.rowcount
    finally:
        cur.close()


# ------------------------------------------------------------------ running

def query(conn: Conn, sql: str, *, root: str = "", allow_change: bool = False, confirm: bool = False, mask: bool = False,
          limit: int = MAX_ROWS) -> dict:
    """Run one query. A read: {columns, rows, count, truncated}. A change (allow_change, a local or test database):
    {changed, done}; without `confirm` keel runs it in a transaction it rolls back, to count the rows."""
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
    try:
        cols, rows, truncated, count = _run(c, conn, text, max(1, min(limit, MAX_ROWS)))
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
        raise DbError(400, f"The database refused the query: {msg}") from exc
    finally:
        c.close()
    ms = int((time.monotonic() - t0) * 1000)
    if cls == "change":
        return {**out, "changed": max(count, 0), "done": confirm, "ms": ms}
    hidden = [i for i, col in enumerate(cols) if mask and SECRET_COL.search(str(col))]
    shown = [[("•••" if i in hidden and v is not None else _value(v)) for i, v in enumerate(r)] for r in rows]
    return {**out, "columns": cols, "rows": shown, "count": len(shown), "truncated": truncated, "masked": [cols[i] for i in hidden],
            "ms": ms}


def test(conn: Conn, root: str = "") -> dict:
    """{ok, server, tables} or {ok: false, error, hint}."""
    try:
        c = _connect(conn, root, read=True)
    except DbError as exc:
        return {"ok": False, "error": str(exc), "hint": exc.hint}
    except Exception as exc:
        e = _connect_error(conn, exc)
        return {"ok": False, "error": str(e), "hint": e.hint}
    try:
        server = {"postgres": "SHOW server_version", "mysql": "SELECT VERSION()", "sqlite": "SELECT sqlite_version()"}[conn.kind]
        _, rows, _, _ = _run(c, conn, server, 1)
        version = str(rows[0][0]) if rows else ""
        c.rollback()
    finally:
        c.close()
    tables = len(schema(conn, root)["tables"])
    label = {"postgres": "PostgreSQL", "mysql": "MySQL", "sqlite": "SQLite"}[conn.kind]
    return {"ok": True, "server": f"{label} {version.split()[0] if version else ''}".strip(), "tables": tables}


def schema(conn: Conn, root: str = "") -> dict:
    """{connection, kind, tables: [{name, columns: [{name, type, nullable, pk}], fks: [{column, table, ref}]}]}."""
    try:
        c = _connect(conn, root, read=True)
    except DbError:
        raise
    except Exception as exc:
        raise _connect_error(conn, exc) from exc
    tables: dict[str, dict] = {}

    def table(name: str) -> dict:
        return tables.setdefault(name, {"name": name, "columns": [], "fks": []})

    try:
        if conn.kind == "sqlite":
            _, rows, _, _ = _run(c, conn, "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                                          "ORDER BY name", MAX_TABLES)
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
            _, cols, _, _ = _run(c, conn, "SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.is_nullable "
                                          "FROM information_schema.columns c JOIN information_schema.tables t "
                                          "ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                                          f"WHERE {where} AND t.table_type = 'BASE TABLE' "
                                          "ORDER BY c.table_schema, c.table_name, c.ordinal_position", 20_000)

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
    return {"connection": conn.name, "kind": conn.kind, "tables": list(tables.values())}


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
