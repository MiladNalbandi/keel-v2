package keel.api.marketplace

import keel.api.pluginhost.PluginRestart
import org.slf4j.LoggerFactory
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Service
import java.time.Instant

/** What GET /api/plugins says about a restart. [running]: agent steps that run now (a restart when idle waits for 0). */
data class RestartState(val pending: Boolean, val scheduled: Boolean, val supervised: Boolean, val running: Int)

/** POST /api/plugins/restart's answer. */
data class RestartAnswer(val restarting: Boolean, val scheduled: Boolean, val running: Int)

/**
 * A new or changed plugin loads only after keel restarts (keel-start resolves the plugins again). Now: [PluginRestart]
 * at once (the api ends with code 75). When idle: keel waits until no agent step runs (the agent calls of flows and
 * KeelBot that run, table agent_calls), then restarts; it checks every few seconds. The rule restart_when_idle does
 * this by itself after an approved install.
 */
@Service
class RestartPlan(private val jdbc: JdbcTemplate, private val restart: PluginRestart) {
    private val log = LoggerFactory.getLogger(javaClass)

    @Volatile private var waits = false

    /** Something changed through this api since keel started (an install, update, rollback, removal, on or off). */
    @Volatile private var changes = false

    /** A restart waits until no agent step runs. */
    val scheduled: Boolean get() = waits

    fun changed() {
        changes = true
    }

    /** Agent steps that run now. A call that started more than [STALE_HOURS] hours ago and never ended does not count. */
    fun running(): Int {
        val since = Instant.now().minusSeconds(STALE_HOURS * 3600).toString()
        return jdbc.queryForObject(
            "SELECT COUNT(*) FROM agent_calls WHERE status = 'running' AND started_at >= ?", Int::class.java, since,
        ) ?: 0
    }

    fun state(pendingFromEngine: Boolean): RestartState =
        RestartState(pendingFromEngine || changes || waits, waits, restart.canRestart, running())

    /** Restart now (409 when keel-start does not run keel). */
    fun now(): RestartAnswer {
        restart.restart()
        return RestartAnswer(restarting = true, scheduled = scheduled, running = running())
    }

    /** Restart as soon as no agent step runs: at once when none runs now (409 when keel-start does not run keel). */
    fun whenIdle(): RestartAnswer {
        if (!restart.canRestart) throw restart.refusal()
        waits = true
        val restarting = tick()
        return RestartAnswer(restarting = restarting, scheduled = true, running = running())
    }

    /** The rule restart_when_idle after an approved install: keel restarts by itself when it can. False when it cannot. */
    fun afterApprovedInstall(rules: MarketplaceRules): Boolean {
        if (!rules.restartWhenIdle) return false
        if (!restart.canRestart) {
            log.info("plugins: the rule restart_when_idle is on, but keel-start does not run keel: restart it by hand")
            return false
        }
        whenIdle()
        return true
    }

    /** True when it restarts now: a restart waits and no agent step runs. */
    @Scheduled(fixedDelay = CHECK_MS, initialDelay = CHECK_MS)
    fun tick(): Boolean {
        if (!waits || running() > 0) return false
        return try {
            restart.restart()
            true
        } catch (e: Exception) {
            log.warn("plugins: the restart that waited could not start: {}", e.message)
            waits = false
            false
        }
    }

    companion object {
        const val CHECK_MS = 5_000L
        const val STALE_HOURS = 6L
    }
}
