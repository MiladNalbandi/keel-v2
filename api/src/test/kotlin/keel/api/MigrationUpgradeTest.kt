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
 * keel's database history across the plugin track. The released keel 0.15.4 ends at V14__thread_hidden.sql (the run
 * history's "delete"); the plugin track's approvals table came after it, as V15__approvals.sql. A keel that ran 0.15.4
 * must start on the track: Flyway applies only V15, and 0.15.4's rows and history stay as they were. No Spring: Flyway
 * on a throw-away SQLite file, as keel's api runs it.
 */
class MigrationUpgradeTest {
    /** The migrations keel 0.15.4 shipped, in order (the same files as core's V1–V14 here). */
    private val released = listOf(
        "V1__init.sql", "V2__caps.sql", "V3__tokens_cached.sql", "V4__step_output.sql", "V5__provider_usage.sql",
        "V6__inbox_notifications.sql", "V7__tasks_jira.sql", "V8__flow_worktrees.sql", "V9__quality_runs.sql",
        "V10__workflow_folders.sql", "V11__plugins.sql", "V12__ci_seen.sql", "V13__review.sql", "V14__thread_hidden.sql",
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
    fun `core's migrations are 0_15_4's V1 to V14, then the plugin track's V15 approvals`() {
        val names = core().keys
        assertThat(names).containsAll(released)
        assertThat(names - released.toSet()).containsExactly("V15__approvals.sql")
        // one file per version: a second V14 would stop every keel at start
        val versions = names.map { it.substringBefore("__") }
        assertThat(versions).doesNotHaveDuplicates()
    }

    @Test
    fun `a keel database from 0_15_4 migrates to the plugin track, only V15 runs and the data stays`(@TempDir dir: Path) {
        val scripts = core()
        val old = Files.createDirectories(dir.resolve("keel-0.15.4"))
        released.forEach { Files.writeString(old.resolve(it), scripts.getValue(it)) }
        val ds = sqlite(dir.resolve("keel.db"))

        // keel 0.15.4 starts: its fourteen migrations, and a flow deleted from the run history
        assertThat(flyway(ds, "filesystem:$old").migrate().migrationsExecuted).isEqualTo(14)
        ds.connection.use { c ->
            c.createStatement().executeUpdate(
                "INSERT INTO projects (id, name, root, created_at) VALUES ('p1', 'demo', '/w/demo', '2026-10-01T00:00:00Z')",
            )
            c.createStatement().executeUpdate(
                "INSERT INTO threads (id, project_id, title, status, created_at, updated_at, hidden_at) " +
                    "VALUES ('t1', 'p1', 'Player ranks', 'done', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', '2026-10-02T00:00:00Z')",
            )
        }
        assertThat(history(ds).last()).isEqualTo("14 V14__thread_hidden.sql 1")
        assertThat(tables(ds)).doesNotContain("approvals")

        // the plugin track starts on the same file: Flyway checks 0.15.4's history (same checksums) and adds V15
        val track = flyway(ds, "classpath:db/migration")
        val result = track.migrate()
        assertThat(result.migrationsExecuted).isEqualTo(1)
        assertThat(result.migrations.map { it.filepath.substringAfterLast('/') }).containsExactly("V15__approvals.sql")
        assertThat(track.validateWithResult().validationSuccessful).isTrue()

        val h = history(ds)
        assertThat(h).hasSize(15)
        assertThat(h.take(14).map { it.substringAfter(' ').substringBefore(' ') }).containsExactlyElementsOf(released)
        assertThat(h.last()).isEqualTo("15 V15__approvals.sql 1")
        assertThat(tables(ds)).contains("approvals", "threads")
        ds.connection.use { c ->
            c.createStatement().executeQuery("SELECT hidden_at FROM threads WHERE id = 't1'").use { rs ->
                assertThat(rs.next()).isTrue()
                assertThat(rs.getString(1)).isEqualTo("2026-10-02T00:00:00Z")
            }
            c.createStatement().executeUpdate(
                "INSERT INTO approvals (id, project_id, kind, source, created_at) VALUES ('a1', 'p1', 'permission', 'keelbot', '2026-10-03T00:00:00Z')",
            )
        }

        // a second start changes nothing
        assertThat(flyway(ds, "classpath:db/migration").migrate().migrationsExecuted).isEqualTo(0)
    }
}
