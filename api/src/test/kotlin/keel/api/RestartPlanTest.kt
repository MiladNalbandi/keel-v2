package keel.api

import keel.api.marketplace.MarketplaceRules
import keel.api.marketplace.RestartPlan
import keel.api.pluginhost.ApiExit
import keel.api.pluginhost.PluginRestart
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.api.io.TempDir
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.datasource.DriverManagerDataSource
import java.nio.file.Path
import java.time.Instant
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit

/** Restart now or when no agent step runs (docs/plugins/13-step4-contract.md §6), with its own small database. */
class RestartPlanTest {
    @TempDir lateinit var dir: Path

    private val exits = CopyOnWriteArrayList<Int>()

    private fun jdbc(): JdbcTemplate {
        val jdbc = JdbcTemplate(DriverManagerDataSource("jdbc:sqlite:${dir.resolve("mw_restart.db")}"))
        jdbc.execute("CREATE TABLE agent_calls (id TEXT PRIMARY KEY, status TEXT NOT NULL, started_at TEXT NOT NULL)")
        return jdbc
    }

    private fun plan(jdbc: JdbcTemplate, supervised: Boolean = true) =
        RestartPlan(jdbc, PluginRestart(if (supervised) "1" else "", ApiExit { exits += it }))

    private fun call(jdbc: JdbcTemplate, id: String, startedSecondsAgo: Long = 5) =
        jdbc.update("INSERT INTO agent_calls(id, status, started_at) VALUES (?, 'running', ?)", id, Instant.now().minusSeconds(startedSecondsAgo).toString())

    private fun awaitExit(): Int? {
        val until = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
        while (exits.isEmpty() && System.nanoTime() < until) Thread.sleep(20)
        return exits.firstOrNull()
    }

    @Test
    fun `when idle waits until no agent step runs, then restarts with code 75`() {
        val jdbc = jdbc()
        val plan = plan(jdbc)
        call(jdbc, "c1")
        val answer = plan.whenIdle()
        assertThat(answer.restarting).isFalse()
        assertThat(answer.scheduled).isTrue()
        assertThat(answer.running).isEqualTo(1)
        assertThat(plan.state(false).pending).isTrue()
        assertThat(plan.tick()).isFalse()
        Thread.sleep(PluginRestart.DELAY_MS + 100)
        assertThat(exits).isEmpty()

        jdbc.update("UPDATE agent_calls SET status = 'done'")
        assertThat(plan.tick()).isTrue()
        assertThat(awaitExit()).isEqualTo(PluginRestart.EXIT_CODE)
    }

    @Test
    fun `a call that never ended long ago does not hold the restart, and an idle keel restarts at once`() {
        val jdbc = jdbc()
        call(jdbc, "old", startedSecondsAgo = (RestartPlan.STALE_HOURS + 1) * 3600)
        val answer = plan(jdbc).whenIdle()
        assertThat(answer.restarting).isTrue()
        assertThat(awaitExit()).isEqualTo(PluginRestart.EXIT_CODE)
    }

    @Test
    fun `now restarts at once, even while agents run`() {
        val jdbc = jdbc()
        call(jdbc, "c1")
        assertThat(plan(jdbc).now().restarting).isTrue()
        assertThat(awaitExit()).isEqualTo(PluginRestart.EXIT_CODE)
    }

    @Test
    fun `the rule restart_when_idle restarts by itself after an approved install, only when it is on`() {
        val jdbc = jdbc()
        call(jdbc, "c1")
        val plan = plan(jdbc)
        assertThat(plan.afterApprovedInstall(MarketplaceRules(restartWhenIdle = false))).isFalse()
        assertThat(plan.scheduled).isFalse()
        assertThat(plan.afterApprovedInstall(MarketplaceRules(restartWhenIdle = true))).isTrue()
        assertThat(plan.scheduled).isTrue()
        // keel-start does not run this one: the rule cannot restart it, nothing waits
        val alone = plan(jdbc, supervised = false)
        assertThat(alone.afterApprovedInstall(MarketplaceRules(restartWhenIdle = true))).isFalse()
        assertThat(alone.scheduled).isFalse()
        assertThrows<keel.api.common.Conflict> { alone.whenIdle() }
        assertThrows<keel.api.common.Conflict> { alone.now() }
        assertThat(alone.state(false).supervised).isFalse()
    }
}
