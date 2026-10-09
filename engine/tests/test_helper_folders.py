"""v0.15.2 KeelBot's chat folders: core keeps KeelBot's engine tables (runtime/migrate.py), so core adds the folders
table and the chats' folder column. The folders themselves are tested with the KeelBot plugin
(plugins/keelbot/engine/tests/test_helper_folders.py)."""


async def test_an_older_helper_table_gets_its_folder_column(tmp_path):
    import aiosqlite
    from keel_engine.runtime import migrate
    async with aiosqlite.connect(tmp_path / "m.db") as conn:
        # the table as v0.15.1 made it: no folder column, no folders table
        await conn.execute("""create table helper_sessions (
          id text primary key, project text not null, root text not null, mode text not null, title text not null,
          model_json text not null, engine_session text, status text not null, error text, thread_id text,
          tokens_in integer not null default 0, tokens_out integer not null default 0, tokens_cached integer not null default 0,
          cost_usd real not null default 0, turns integer not null default 0, created_at text not null, updated_at text not null,
          grants_json text not null default '[]', phase text, worktree text, branch text, base_sha text)""")
        await conn.execute("insert into helper_sessions (id, project, root, mode, title, model_json, status, created_at, updated_at) "
                           "values ('h_old', 'p', '/w', 'ask', 't', '{}', 'idle', 'x', 'x')")
        await migrate.migrate(conn)
        await migrate.migrate(conn)
        async with conn.execute("select folder from helper_sessions where id = 'h_old'") as cur:
            assert await cur.fetchone() == (None,)
        async with conn.execute("select count(*) from helper_folders") as cur:
            assert await cur.fetchone() == (0,)
