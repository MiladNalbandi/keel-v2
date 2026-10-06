"""The database schema the Map draws: SQL migrations -> tables, columns, keys, indexes, views (runtime/sqlschema.py),
and where the mapper finds the migrations (runtime/mapper.py)."""

import json
import subprocess
from pathlib import Path

from keel_engine.runtime import mapper, sqlschema


def parse(*texts: str) -> dict:
    return sqlschema.parse([(f"V{i + 1}__m.sql", t) for i, t in enumerate(texts)])


def table(schema: dict, tid: str) -> dict:
    return next(t for t in schema["tables"] if t["id"] == tid)


def col(t: dict, name: str) -> dict:
    return next(c for c in t["columns"] if c["name"] == name)


def fks(schema: dict) -> set:
    return {(r["from"], tuple(r["from_columns"]), r["to"], tuple(r["to_columns"])) for r in schema["relations"] if r["kind"] == "fk"}


# ------------------------------------------------------------------ CREATE TABLE

def test_columns_types_nullability_defaults_and_inline_keys():
    s = parse("""
create table team (id bigserial primary key, name varchar(80) not null unique);
CREATE TABLE IF NOT EXISTS player (
    id         uuid         NOT NULL DEFAULT gen_random_uuid(),
    team_id    BIGINT       REFERENCES team(id) ON DELETE SET NULL ON UPDATE CASCADE,
    nick       character varying(40) DEFAULT 'anon'::character varying NOT NULL,
    score      NUMERIC(10, 2) DEFAULT 0,
    born_at    timestamp with time zone,
    tags       text[],
    rating     double precision NULL,
    PRIMARY KEY (id)
);
""")
    team, player = table(s, "team"), table(s, "player")
    assert col(team, "id") | {} == {**col(team, "id"), "pk": True, "nullable": False, "identity": True, "type": "bigserial"}
    assert col(team, "name")["unique"] and not col(team, "name")["nullable"] and col(team, "name")["type"] == "varchar(80)"
    assert player["primary_key"]["columns"] == ["id"] and col(player, "id")["pk"]
    assert col(player, "id")["default"] == "gen_random_uuid()"
    assert col(player, "nick")["type"] == "character varying(40)" and col(player, "nick")["default"] == "'anon'::character varying"
    assert not col(player, "nick")["nullable"] and col(player, "score")["default"] == "0"
    assert col(player, "score")["type"] == "NUMERIC(10, 2)" and col(player, "born_at")["type"] == "timestamp with time zone"
    assert col(player, "tags")["type"] == "text[]" and col(player, "rating")["type"] == "double precision"
    assert col(player, "team_id")["fk"] == {"table": "team", "column": "id", "missing": False}
    rel = next(r for r in s["relations"] if r["from"] == "player")
    assert rel["to"] == "team" and rel["on_delete"] == "SET NULL" and rel["on_update"] == "CASCADE" and rel["nullable"]


def test_composite_keys_over_several_lines_and_named_constraints():
    s = parse("""
create table wave (
    project_id uuid not null,
    wave_id    varchar(64) not null,
    constraint pk_wave primary key (project_id, wave_id)
);
create table wave_level_wave (
    project_id    uuid not null,
    wave_id       varchar(64) not null,
    position      integer not null,
    constraint pk_wlw primary key (wave_id, position),
    constraint fk_wlw_wave foreign key (project_id, wave_id)
        references wave (project_id, wave_id) on delete cascade,
    constraint uq_wlw_position unique (project_id, position),
    constraint ck_position check (position >= 0 and (position < 1000))
);
""")
    w, wlw = table(s, "wave"), table(s, "wave_level_wave")
    assert w["primary_key"] == {"name": "pk_wave", "columns": ["project_id", "wave_id"], "cite": {"rel": "V1__m.sql", "line": 5}}
    assert col(w, "project_id")["pk"] and col(w, "wave_id")["pk"]
    fk = wlw["foreign_keys"][0]
    assert fk["name"] == "fk_wlw_wave" and fk["columns"] == ["project_id", "wave_id"] and fk["ref_columns"] == ["project_id", "wave_id"]
    assert fk["on_delete"] == "CASCADE" and fk["cite"]["line"] == 12
    assert wlw["uniques"] == [{"name": "uq_wlw_position", "columns": ["project_id", "position"], "cite": {"rel": "V1__m.sql", "line": 14}}]
    assert wlw["checks"] == 1
    assert fks(s) == {("wave_level_wave", ("project_id", "wave_id"), "wave", ("project_id", "wave_id"))}


def test_quoted_names_schemas_and_references_without_columns():
    s = parse('''
CREATE TABLE "auth"."Users" ("Id" SERIAL PRIMARY KEY, "E-mail" TEXT NOT NULL);
CREATE TABLE public.orders (
  id int primary key,
  user_id int references auth."Users",
  "group" text
);
CREATE TABLE `shop`.`items` (`id` INT NOT NULL AUTO_INCREMENT, `order_id` INT, PRIMARY KEY (`id`),
  KEY `ix_order` (`order_id`), CONSTRAINT `fk_items_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE [dbo].[Audit] ([Id] INT IDENTITY(1,1) NOT NULL, [At] DATETIME2 NULL, CONSTRAINT [PK_Audit] PRIMARY KEY CLUSTERED ([Id]));
''')
    users = table(s, "auth.Users")
    assert users["schema"] == "auth" and users["name"] == "Users" and [c["name"] for c in users["columns"]] == ["Id", "E-mail"]
    orders = table(s, "orders")                               # public is the default schema: the id is the bare name
    assert orders["schema"] == "public" and col(orders, "user_id")["fk"] == {"table": "auth.Users", "column": "Id", "missing": False}
    assert col(orders, "group")["type"] == "text"
    items = table(s, "shop.items")
    assert col(items, "id")["identity"] and col(items, "id")["pk"] and items["indexes"][0]["name"] == "ix_order"
    assert items["foreign_keys"][0]["ref_table"] == "orders"
    audit = table(s, "Audit")
    assert col(audit, "Id")["identity"] and col(audit, "Id")["pk"] and col(audit, "At")["type"] == "DATETIME2"


def test_foreign_key_to_a_table_outside_the_migrations_is_kept_but_not_drawn():
    s = parse("create table note (id int primary key, owner uuid references auth.users(id));")
    note = table(s, "note")
    assert note["foreign_keys"][0]["missing"] and note["foreign_keys"][0]["ref_name"] == "auth.users"
    assert col(note, "owner")["fk"]["missing"] and not [r for r in s["relations"] if r["kind"] == "fk"]


def test_self_reference_and_one_to_one():
    s = parse("""
create table node (id int primary key, parent_id int references node(id));
create table profile (user_id int primary key references node(id), bio text);
""")
    rels = {r["from"]: r for r in s["relations"]}
    assert rels["node"]["self"] and not rels["node"]["one_to_one"]
    assert rels["profile"]["one_to_one"] and not rels["profile"]["nullable"]


# ------------------------------------------------------------------ ALTER / DROP / RENAME

def test_alter_table_add_drop_rename_and_alter_columns():
    s = parse(
        """
create table players (id bigint primary key, name text, legacy int, nick varchar(10));
create table teams (id bigint primary key);
""",
        """
ALTER TABLE players ADD COLUMN team_id BIGINT;
ALTER TABLE players ADD CONSTRAINT fk_team FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE RESTRICT;
alter table players drop column legacy;
alter table players rename column name to display_name;
ALTER TABLE players ALTER COLUMN nick TYPE varchar(40), ALTER COLUMN nick SET NOT NULL, ALTER nick SET DEFAULT 'x';
ALTER TABLE players
    ADD COLUMN IF NOT EXISTS joined_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS team_id BIGINT;
""",
    )
    p = table(s, "players")
    assert [c["name"] for c in p["columns"]] == ["id", "display_name", "nick", "team_id", "joined_at"]
    assert col(p, "nick") | {} == {**col(p, "nick"), "type": "varchar(40)", "nullable": False, "default": "'x'"}
    assert col(p, "joined_at")["cite"] == {"rel": "V2__m.sql", "line": 8} and col(p, "joined_at")["default"] == "now()"
    assert p["foreign_keys"][0]["name"] == "fk_team" and p["foreign_keys"][0]["on_delete"] == "RESTRICT"
    assert col(p, "team_id")["fk"]["table"] == "teams"
    whats = [c["what"] for c in p["changes"]]
    assert whats[:4] == ["added column team_id", "added foreign key", "dropped column legacy", "renamed column name to display_name"]
    assert all(c["rel"] == "V2__m.sql" for c in p["changes"])


def test_drop_constraint_and_drop_column_take_their_keys_along():
    s = parse("""
create table a (id int primary key);
create table b (id int primary key, a_id int, x int, constraint fk_b_a foreign key (a_id) references a(id), constraint uq_x unique (x));
alter table b drop constraint fk_b_a;
alter table b drop constraint uq_x;
create table c (id int primary key, a_id int references a(id));
alter table c drop column a_id;
""")
    assert not table(s, "b")["foreign_keys"] and not table(s, "b")["uniques"] and not col(table(s, "b"), "x")["unique"]
    assert not table(s, "c")["foreign_keys"] and not s["relations"]


def test_rename_table_keeps_the_keys_that_point_at_it():
    s = parse(
        "create table person (id int primary key); create table pet (id int primary key, owner_id int references person(id));",
        "ALTER TABLE person RENAME TO owner; RENAME TABLE pet TO animal;",
    )
    assert {t["id"] for t in s["tables"]} == {"owner", "animal"}
    assert fks(s) == {("animal", ("owner_id",), "owner", ("id",))}
    assert table(s, "owner")["changes"][0]["what"] == "renamed from person"


def test_rename_column_follows_into_foreign_keys_that_reference_it():
    s = parse("create table a (code text primary key); create table b (a_code text references a(code));",
              "alter table a rename column code to key_code;")
    assert fks(s) == {("b", ("a_code",), "a", ("key_code",))}


def test_drop_table_removes_it_and_the_keys_to_it():
    s = parse("create table a (id int primary key); create table b (id int, a_id int references a(id)); create table tmp (id int);",
              "DROP TABLE IF EXISTS tmp, a CASCADE;")
    assert [t["id"] for t in s["tables"]] == ["b"] and not table(s, "b")["foreign_keys"]


def test_mysql_modify_and_change_column():
    s = parse("CREATE TABLE t (id INT PRIMARY KEY, n VARCHAR(10), m INT);",
              "ALTER TABLE t MODIFY COLUMN n VARCHAR(200) NOT NULL, CHANGE m amount BIGINT DEFAULT 0;")
    t = table(s, "t")
    assert col(t, "n")["type"] == "VARCHAR(200)" and not col(t, "n")["nullable"]
    assert col(t, "amount")["type"] == "BIGINT" and col(t, "amount")["default"] == "0"


def test_create_table_if_not_exists_does_not_replace_an_existing_table():
    s = parse("create table a (id int primary key, b int);", "create table if not exists a (id int);")
    assert [c["name"] for c in table(s, "a")["columns"]] == ["id", "b"]


# ------------------------------------------------------------------ indexes, views, comments

def test_indexes_unique_partial_and_dropped():
    s = parse("""
create table w (project_id uuid, wave_order int, published boolean, email text, config jsonb);
create unique index uq_wave_order on w (project_id, wave_order);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_pub ON ONLY w (project_id, published desc) WHERE published;
create unique index uq_email on w (lower(email));
create index ix_config on w using gin (config);
create index ix_gone on w (email);
drop index if exists ix_gone;
""")
    w = table(s, "w")
    idx = {i["name"]: i for i in w["indexes"]}
    assert set(idx) == {"uq_wave_order", "ix_pub", "uq_email", "ix_config"}
    assert idx["uq_wave_order"]["unique"] and idx["uq_wave_order"]["columns"] == ["project_id", "wave_order"]
    assert idx["ix_pub"]["where"] == "published" and idx["ix_pub"]["columns"] == ["project_id", "published"]
    assert idx["uq_email"]["columns"] == ["lower(email)"] and idx["ix_config"]["method"] == "gin"
    assert idx["ix_config"]["cite"]["line"] == 6


def test_views_with_columns_and_the_tables_they_read():
    s = parse("""
create table users (id int primary key, name text, team_id int);
create table teams (id int primary key, title text);
CREATE OR REPLACE VIEW user_teams AS
  SELECT u.id, u.name AS user_name, t.title team, count(*) as n
  FROM users u JOIN teams t ON t.id = u.team_id GROUP BY 1, 2, 3;
create materialized view if not exists all_users (uid, uname) as select id, name from users;
create view gone as select * from users;
drop view gone;
""")
    v = table(s, "user_teams")
    assert v["kind"] == "view" and [c["name"] for c in v["columns"]] == ["id", "user_name", "team", "n"]
    assert v["uses"] == ["users", "teams"] and v["definition"].startswith("SELECT u.id")
    mv = table(s, "all_users")
    assert mv["kind"] == "materialized view" and [c["name"] for c in mv["columns"]] == ["uid", "uname"] and mv["uses"] == ["users"]
    assert {(r["from"], r["to"]) for r in s["relations"] if r["kind"] == "uses"} == {("user_teams", "users"), ("user_teams", "teams"),
                                                                                    ("all_users", "users")}
    assert "gone" not in {t["id"] for t in s["tables"]}


def test_comments_on_tables_and_columns():
    s = parse("create table t (id int, n int COMMENT 'mysql says');", "COMMENT ON TABLE t IS 'the t'; COMMENT ON COLUMN t.id IS 'it''s the id';")
    t = table(s, "t")
    assert t["comment"] == "the t" and col(t, "id")["comment"] == "it's the id" and col(t, "n")["comment"] == "mysql says"


# ------------------------------------------------------------------ what the reader must not trip on

def test_comments_strings_and_function_bodies_do_not_split_statements():
    s = parse("""
-- create table nope (id int);
/* create table nope2 (id int); /* nested */ still a comment; */
create function f() returns trigger as $body$
begin
  execute 'create table nope3 (id int);';
  return new;
end;
$body$ language plpgsql;
create table yes (
  id int, -- the id; with a semicolon
  note text default 'a;b' # not a comment here
);
INSERT INTO yes VALUES (1, 'create table nope4 (id int);');
""")
    assert [t["id"] for t in s["tables"]] == ["yes"] and [c["name"] for c in table(s, "yes")["columns"]] == ["id", "note"]
    assert col(table(s, "yes"), "note")["default"].startswith("'a;b'")


def test_down_sections_of_dbmate_and_goose_are_ignored():
    s = parse(
        "-- migrate:up\ncreate table a (id int);\n-- migrate:down\ndrop table a;\n",
        "-- +goose Up\ncreate table b (id int);\n-- +goose StatementBegin\nselect 1;\n-- +goose StatementEnd\n-- +goose Down\ndrop table b;\n",
    )
    assert [t["id"] for t in s["tables"]] == ["a", "b"]


def test_sql_server_go_batches_and_partitions_and_temp_tables():
    s = parse("CREATE TABLE a (id INT)\nGO\nCREATE TABLE b (id INT)\nGO\n",
              "create temp table scratch (x int); create table m (id int, at date) partition by range (at);"
              " create table m_2024 partition of m for values from ('2024-01-01') to ('2025-01-01');")
    assert [t["id"] for t in s["tables"]] == ["a", "b", "m"]


def test_an_unreadable_statement_is_counted_not_fatal():
    s = parse("create table ok (id int); alter table ok add column;  create table ok2 (id int);")
    assert {t["id"] for t in s["tables"]} == {"ok", "ok2"}


def test_lines_point_at_the_file_and_line_of_each_definition():
    s = sqlschema.parse([("db/migration/V1__a.sql", "\n\ncreate table a (\n  id int primary key,\n\n  n text\n);\n"),
                         ("db/migration/V2__b.sql", "alter table a\n  add column m int;\n")])
    a = table(s, "a")
    assert a["cite"] == {"rel": "db/migration/V1__a.sql", "line": 3}
    assert [(c["name"], c["cite"]["line"], c["cite"]["rel"][-9:]) for c in a["columns"]] == [
        ("id", 4, "V1__a.sql"), ("n", 6, "V1__a.sql"), ("m", 2, "V2__b.sql")]
    assert s["files"] == ["db/migration/V1__a.sql", "db/migration/V2__b.sql"]


# ------------------------------------------------------------------ where the mapper looks

def write(repo, rel, text):
    f = Path(repo) / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def commit(repo):
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], cwd=repo, capture_output=True)


def test_flyway_versions_and_vendor_folders_apply_in_version_order(repo):
    base = "svc/src/main/resources/db"
    write(repo, f"{base}/migration/V10__c.sql", "alter table a add column c int;")
    write(repo, f"{base}/migration/V2__a.sql", "create table a (id int primary key);")
    write(repo, f"{base}/vendor/postgresql/V2.1__b.sql", "alter table a add column b jsonb;")
    write(repo, f"{base}/migration/R__view.sql", "create or replace view av as select id, b, c from a;")
    write(repo, f"{base}/migration/U2__undo.sql", "drop table a;")
    commit(repo)
    m = mapper.build(str(repo))
    assert m["sources"]["migrations"] == [f"{base}/migration/V2__a.sql", f"{base}/vendor/postgresql/V2.1__b.sql",
                                          f"{base}/migration/V10__c.sql", f"{base}/migration/R__view.sql"]
    a = table(m["schema"], "a")
    assert [c["name"] for c in a["columns"]] == ["id", "b", "c"]
    assert m["counts"]["tables"] == 1 and m["counts"]["views"] == 1


def test_prisma_supabase_and_golang_migrate_folders_down_files_skipped(repo):
    write(repo, "prisma/migrations/20240102000000_more/migration.sql", 'ALTER TABLE "User" ADD COLUMN "age" INTEGER;')
    write(repo, "prisma/migrations/20240101000000_init/migration.sql", 'CREATE TABLE "User" ("id" SERIAL NOT NULL, CONSTRAINT "User_pkey" PRIMARY KEY ("id"));')
    write(repo, "db/migrations/000001_post.up.sql", "create table post (id int);")
    write(repo, "db/migrations/000001_post.down.sql", "drop table post;")
    write(repo, "src/test/resources/db/migration/V1__test_only.sql", "create table test_only (id int);")
    commit(repo)
    m = mapper.build(str(repo))
    assert {t["id"] for t in m["schema"]["tables"]} == {"User", "post"}
    assert [c["name"] for c in table(m["schema"], "User")["columns"]] == ["id", "age"]
    assert not [f for f in m["sources"]["migrations"] if "down" in f or "test" in f]


def test_map_config_points_at_the_migrations(repo):
    write(repo, "sql/schema/001.sql", "create table configured (id int primary key);")
    write(repo, "db/migration/V1__other.sql", "create table other (id int);")
    write(repo, ".keel/config.yml", "version: 4\nmap:\n  migrations: [sql/schema]\n")
    commit(repo)
    m = mapper.build(str(repo))
    assert [t["id"] for t in m["schema"]["tables"]] == ["configured"]
    assert m["sources"]["configured"] and m["sources"]["looked_in"] == ["sql/schema"]


def test_no_migrations_says_where_keel_looked(repo):
    m = mapper.build(str(repo))
    assert "er" not in m["levels"] and m["schema"]["tables"] == []
    assert any("db/migration" in x for x in m["sources"]["looked_in"]) and "map.migrations" in m["sources"]["config"]


def test_nothing_is_capped_and_every_column_is_in_the_er_level(repo):
    cols = ",\n".join(f"  c{i} int" for i in range(30))
    sql = "\n".join(f"create table t{i:02d} (id int primary key, ref int references t00(id),\n{cols});" for i in range(40))
    write(repo, "db/migration/V1__many.sql", sql)
    commit(repo)
    m = mapper.build(str(repo))
    assert m["counts"]["tables"] == 40 and len(m["schema"]["tables"]) == 40 and m["counts"]["relations"] == 40
    er = m["levels"]["er"]
    assert len(er["nodes"]) == 40 and all(len(n["rows"]) == 32 for n in er["nodes"])
    assert len([r for r in m["schema"]["relations"] if r["self"]]) == 1


def test_endpoints_carry_summary_and_tags(repo):
    write(repo, "contracts/openapi.yaml", "openapi: 3.0.0\npaths:\n  /a:\n    get: {summary: List a, tags: [alpha], operationId: listA}\n")
    commit(repo)
    m = mapper.build(str(repo))
    assert m["api"]["endpoints"] == [{"method": "GET", "path": "/a", "cite": {"rel": "contracts/openapi.yaml", "line": 3},
                                      "summary": "List a", "tags": ["alpha"], "operation": "listA"}]


def test_the_shop_fixture_the_web_draws():
    """tests/fixtures/schema_shop: ~25 tables with many foreign keys (web/src/test/fixtures/er-shop.json is its output)."""
    base = Path(__file__).parent / "fixtures" / "schema_shop"
    files = sorted(base.glob("*.sql"), key=lambda f: mapper._order(f.name))
    s = sqlschema.parse([(f"db/migration/{f.name}", f.read_text()) for f in files])
    assert s["skipped"] == 0
    assert len([t for t in s["tables"] if t["kind"] == "table"]) == 28 and table(s, "order_totals")["kind"] == "view"
    assert len([r for r in s["relations"] if r["kind"] == "fk"]) == 41
    assert ("shipment_line", ("order_id", "line_no"), "order_line", ("order_id", "line_no")) in fks(s)      # composite
    assert ("billing.refund", ("payment_id",), "billing.payment", ("id",)) in fks(s)                         # schemas
    assert col(table(s, "app_user"), "full_name")["nullable"]                                                 # renamed later
    assert col(table(s, "product"), "weight_grams")["cite"]["rel"] == "db/migration/V5__support.sql"
    assert table(s, "billing.invoice")["foreign_keys"][0]["ref_table"] == "customer_order"
    web = Path(__file__).parents[2] / "web" / "src" / "test" / "fixtures" / "er-shop.json"
    assert json.loads(web.read_text()) == {"tables": s["tables"], "relations": s["relations"]}
