package keel.api

import org.assertj.core.api.Assertions.assertThat
import org.flywaydb.core.Flyway
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import org.springframework.core.io.support.PathMatchingResourcePatternResolver
import org.sqlite.SQLiteDataSource
import java.nio.file.Files
import java.nio.file.Path
import javax.sql.DataSource

/**
 * keel's database history across the plugin track. The released keel 0.16.1 ends at V15__doc_history.sql (0.15.9's file
 * history; 0.15.4 added V14__thread_hidden.sql); the plugin track's approvals table comes after it, as
 * V16__approvals.sql (it was V14, then V15, before main's releases took those numbers), then the workflow marketplace's
 * columns, V17__workflow_market.sql (docs/plugins/21-workflow-marketplace.md), and 0.18.6's skill marketplace columns,
 * V18__skill_market.sql, 0.18.9's effort of each agent call, V19__agent_call_effort.sql, and Code Review's multi-line
 * comments, V20__review_line_range.sql, the review agents' new names, V21__review_agent_names.sql, then the Database
 * plugin's global connections, V22__db_connection_scopes.sql, then the phone's paired devices, V23__devices.sql, then the task automations, V24__task_automations.sql, then the notifications' short lines, V25__notification_summary.sql, then what a run works on, V26__thread_source.sql, then the browsers' sign-in sessions, V27__web_sessions.sql. A keel
 * that ran 0.16.1 must start on the track: Flyway applies only V16 to V25, and 0.16.1's rows and history stay as they were. No Spring: Flyway on a
 * throw-away SQLite file, as keel's api runs it.
 */
class MigrationUpgradeTest {
    /** The migrations keel 0.16.1 shipped, in order (the same files as core's V1–V15 here). */
    private val released = listOf(
        "V1__init.sql", "V2__caps.sql", "V3__tokens_cached.sql", "V4__step_output.sql", "V5__provider_usage.sql",
        "V6__inbox_notifications.sql", "V7__tasks_jira.sql", "V8__flow_worktrees.sql", "V9__quality_runs.sql",
        "V10__workflow_folders.sql", "V11__plugins.sql", "V12__ci_seen.sql", "V13__review.sql", "V14__thread_hidden.sql",
        "V15__doc_history.sql",
    )

    private fun core(): Map<String, String> =
        PathMatchingResourcePatternResolver().getResources("classpath:db/migration/*.sql")
            .associate { it.filename!! to it.inputStream.use { s -> s.readAllBytes().toString(Charsets.UTF_8) } }

    private fun sqlite(file: Path): DataSource = SQLiteDataSource().apply { url = "jdbc:sqlite:$file" }

    private fun flyway(ds: DataSource, location: String) = Flyway.configure().dataSource(ds).locations(location).load()

    private fun history(ds: DataSource): List<String> = ds.connection.use { c ->
        c.createStatement().executeQuery("SELECT version, script, success FROM flyway_schema_history ORDER BY installed_rank").use { rs ->
            buildList { while (rs.next()) add("${rs.getString(1)} ${rs.getString(2)} ${rs.getInt(3)}") }
        }
    }

    private fun tables(ds: DataSource): Set<String> = ds.connection.use { c ->
        c.createStatement().executeQuery("SELECT name FROM sqlite_master WHERE type = 'table'").use { rs ->
            buildSet { while (rs.next()) add(rs.getString(1)) }
        }
    }

    @Test
    fun `core's migrations are 0_16_1's V1 to V15, then the plugin track's V16 approvals`() {
        val names = core().keys
        assertThat(names).containsAll(released)
        assertThat(names - released.toSet()).containsExactlyInAnyOrder("V16__approvals.sql", "V17__workflow_market.sql", "V18__skill_market.sql", "V19__agent_call_effort.sql",
            "V20__review_line_range.sql", "V21__review_agent_names.sql", "V22__db_connection_scopes.sql", "V23__devices.sql", "V24__task_automations.sql", "V25__notification_summary.sql", "V26__thread_source.sql", "V27__web_sessions.sql")
        // one file per version: a second V15 would stop every keel at start
        val versions = names.map { it.substringBefore("__") }
        assertThat(versions).doesNotHaveDuplicates()
    }

    @Test
    fun `a keel database from 0_16_1 migrates to the plugin track, only V16 runs and the data stays`(@TempDir dir: Path) {
        val scripts = core()
        val old = Files.createDirectories(dir.resolve("keel-0.16.1"))
        released.forEach { Files.writeString(old.resolve(it), scripts.getValue(it)) }
        val ds = sqlite(dir.resolve("keel.db"))

        // keel 0.16.1 starts: its fifteen migrations, a flow deleted from the run history, a spec's version
        assertThat(flyway(ds, "filesystem:$old").migrate().migrationsExecuted).isEqualTo(15)
        ds.connection.use { c ->
            c.createStatement().executeUpdate(
                "INSERT INTO projects (id, name, root, created_at) VALUES ('p1', 'demo', '/w/demo', '2026-10-01T00:00:00Z')",
            )
            c.createStatement().executeUpdate(
                "INSERT INTO threads (id, project_id, title, status, created_at, updated_at, hidden_at) " +
                    "VALUES ('t1', 'p1', 'Player ranks', 'done', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', '2026-10-02T00:00:00Z')",
            )
            c.createStatement().executeUpdate(
                "INSERT INTO doc_versions (id, project_id, path, kind, version, sha256, content, size, by_kind, at) " +
                    "VALUES ('dv-1', 'p1', '.keel/specs/ranks.md', 'spec', 1, 'abc', '# Ranks', 7, 'agent', '2026-10-02T00:00:00Z')",
            )
            c.createStatement().executeUpdate(
                "INSERT INTO db_connections (id, project_id, name, kind, env, shown, created_at) " +
                    "VALUES ('db1', 'p1', 'local', 'postgres', 'local', 'postgres://app:•••@localhost/app', '2026-10-01T00:00:00Z')", // keel:allow-secret
            )
        }
        assertThat(history(ds).last()).isEqualTo("15 V15__doc_history.sql 1")
        assertThat(tables(ds)).doesNotContain("approvals")

        // the plugin track starts on the same file: Flyway checks 0.16.1's history (same checksums) and adds V16
        val track = flyway(ds, "classpath:db/migration")
        val result = track.migrate()
        assertThat(result.migrationsExecuted).isEqualTo(12)
        assertThat(result.migrations.map { it.filepath.substringAfterLast('/') })
            .containsExactly("V16__approvals.sql", "V17__workflow_market.sql", "V18__skill_market.sql", "V19__agent_call_effort.sql",
            "V20__review_line_range.sql", "V21__review_agent_names.sql", "V22__db_connection_scopes.sql", "V23__devices.sql", "V24__task_automations.sql", "V25__notification_summary.sql", "V26__thread_source.sql", "V27__web_sessions.sql")
        assertThat(track.validateWithResult().validationSuccessful).isTrue()

        val h = history(ds)
        assertThat(h).hasSize(27)
        assertThat(h.take(15).map { it.substringAfter(' ').substringBefore(' ') }).containsExactlyElementsOf(released)
        assertThat(h.drop(15)).containsExactly("16 V16__approvals.sql 1", "17 V17__workflow_market.sql 1", "18 V18__skill_market.sql 1", "19 V19__agent_call_effort.sql 1",
            "20 V20__review_line_range.sql 1", "21 V21__review_agent_names.sql 1", "22 V22__db_connection_scopes.sql 1", "23 V23__devices.sql 1", "24 V24__task_automations.sql 1", "25 V25__notification_summary.sql 1", "26 V26__thread_source.sql 1", "27 V27__web_sessions.sql 1")
        assertThat(tables(ds)).contains("approvals", "threads", "doc_versions", "flow_applied", "devices", "task_automations", "task_watch", "task_runs")
        ds.connection.use { c ->
            c.createStatement().executeQuery("SELECT hidden_at FROM threads WHERE id = 't1'").use { rs ->
                assertThat(rs.next()).isTrue()
                assertThat(rs.getString(1)).isEqualTo("2026-10-02T00:00:00Z")
            }
            c.createStatement().executeQuery("SELECT content FROM doc_versions WHERE id = 'dv-1'").use { rs ->
                assertThat(rs.next()).isTrue()
                assertThat(rs.getString(1)).isEqualTo("# Ranks")
            }
            // V22: a connection from before keeps its row; the Database plugin reads its address into the fields later
            c.createStatement().executeQuery("SELECT name, driver, connect_mode, agent_access, keep_password FROM db_connections WHERE id = 'db1'").use { rs ->
                assertThat(rs.next()).isTrue()
                assertThat(listOf(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5)))
                    .containsExactly("local", null, "url", "read", "keel")
            }
            c.createStatement().executeUpdate(
                "INSERT INTO approvals (id, project_id, kind, source, created_at) VALUES ('a1', 'p1', 'permission', 'keelbot', '2026-10-03T00:00:00Z')",
            )
        }

        // a second start changes nothing
        assertThat(flyway(ds, "classpath:db/migration").migrate().migrationsExecuted).isEqualTo(0)
    }
}
