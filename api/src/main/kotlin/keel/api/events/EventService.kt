package keel.api.events

import keel.api.budget.ProviderUsageStore
import keel.api.common.Json
import keel.api.common.Time
import keel.api.notifications.NotificationService
import org.slf4j.LoggerFactory
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service

data class EngineEvent(
    val type: String = "",
    val threadId: String = "",
    val projectId: String = "",
    val step: String? = null,
    val at: String? = null,
    val callId: String? = null,
    val data: Map<String, Any?> = emptyMap(),
)

/**
 * Stores engine events: agent calls become jobs (agent_calls + agent_steps), thread status follows
 * the flow, some events become notifications, and every event fans out over SSE.
 */
@Service
class EventService(
    private val jdbc: JdbcTemplate,
    private val notifications: NotificationService,
    private val hub: EventHub,
    private val usage: ProviderUsageStore,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    @Synchronized
    fun ingest(events: List<EngineEvent>): Int {
        var stored = 0
        for (e in events) {
            if (e.type.isBlank()) continue
            try {
                apply(e)
                stored++
            } catch (ex: Exception) {
                log.warn("could not store event {} for {}: {}", e.type, e.threadId, ex.message)
            }
            hub.publish(e.projectId.ifBlank { null }, e.type, e)
        }
        return stored
    }

    private fun apply(e: EngineEvent) {
        val at = e.at ?: Time.now()
        val d = e.data
        jdbc.update(
            "INSERT INTO events(thread_id, project_id, type, step, call_id, at, data_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            e.threadId, e.projectId, e.type, e.step, e.callId, at, Json.write(d),
        )
        val link = if (e.projectId.isNotBlank()) "/projects/${e.projectId}/flow" else null

        when (e.type) {
            "thread.started" -> upsertThread(e, "running", at)
            "step.started" -> {
                upsertThread(e, null, at)
                jdbc.update("UPDATE threads SET current = ?, phase = COALESCE(?, phase), updated_at = ? WHERE id = ?", e.step, d.str("phase"), at, e.threadId)
            }
            "step.finished" -> upsertThread(e, null, at)

            "agent.started" -> {
                val id = e.callId ?: "${e.threadId}:${e.step}:$at"
                jdbc.update(
                    """INSERT INTO agent_calls(id, project_id, thread_id, agent, provider, model, step, phase, ac, status, started_at, mode)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
                       ON CONFLICT(id) DO UPDATE SET status = 'running', mode = COALESCE(excluded.mode, agent_calls.mode)""",
                    id, e.projectId, e.threadId, d.str("agent"), d.str("provider"), d.str("model"), e.step,
                    d.str("phase"), d.str("ac"), at, modeOf(d.str("provider"), d.str("mode")),
                )
                upsertThread(e, "running", at)
                jdbc.update("UPDATE threads SET phase = COALESCE(?, phase), ac = COALESCE(?, ac) WHERE id = ?", d.str("phase"), d.str("ac"), e.threadId)
                val agent = d.str("agent") ?: "An agent"
                notifications.create("started", e.projectId, "$agent started", listOfNotNull(d.str("phase"), d.str("ac")).joinToString(" · "), "/jobs/$id")
            }
            "agent.step" -> {
                val id = e.callId ?: return
                ensureCall(id, e, at)
                val n = d.long("n") ?: ((jdbc.queryForObject("SELECT COALESCE(MAX(n), 0) FROM agent_steps WHERE call_id = ?", Long::class.java, id) ?: 0L) + 1)
                val kind = d.str("kind") ?: "text"
                val inserted = jdbc.update(
                    """INSERT OR IGNORE INTO agent_steps(call_id, n, at, kind, text, tool, server, path, diff, ms, ok, output)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    id, n, at, kind, d.str("text"), d.str("tool"), d.str("server"), d.str("path"), d.str("diff"),
                    d.long("ms"), d["ok"]?.let { if (it == true || it.toString() == "true") 1 else 0 }, d.str("output"),
                )
                if (inserted > 0) {
                    val mcp = if (kind == "tool" && !d.str("server").isNullOrBlank()) 1 else 0
                    jdbc.update("UPDATE agent_calls SET steps_count = steps_count + 1, mcp_calls = mcp_calls + ? WHERE id = ?", mcp, id)
                }
            }
            "agent.finished" -> {
                val id = e.callId ?: return
                ensureCall(id, e, at)
                val status = d.str("status") ?: "done"
                jdbc.update(
                    """UPDATE agent_calls SET status = ?, ended_at = ?, tokens_in = ?, tokens_out = ?, tokens_cached = ?, cost_usd = ?,
                       premium_requests = ?, result = ? WHERE id = ?""",
                    status, at, d.long("tokens_in") ?: 0, d.long("tokens_out") ?: 0, d.long("tokens_cached") ?: 0, d.double("cost_usd") ?: 0.0,
                    d.long("premium_requests") ?: 0, d["result"]?.let { if (it is String) it else Json.write(it) }, id,
                )
                if (status == "failed") {
                    val agent = jdbc.queryForObject("SELECT COALESCE(agent, 'An agent') FROM agent_calls WHERE id = ?", String::class.java, id)
                    notifications.create("failed", e.projectId, "$agent failed", d.str("result")?.take(200) ?: "The agent step did not finish.", "/jobs/$id")
                }
            }

            "provider.usage" -> {
                val provider = d.str("provider") ?: return
                @Suppress("UNCHECKED_CAST")
                val windows = (d["windows"] as? List<*>).orEmpty().filterIsInstance<Map<String, Any?>>()
                usage.store(provider, windows, d.str("source") ?: "last run", d.str("at") ?: at)
            }
            "gate.waiting" -> {
                upsertThread(e, "waiting", at)
                notifications.create("review", e.projectId, d.str("title") ?: "A gate waits for you", d.str("detail") ?: "", link,
                    threadId = e.threadId, step = e.step)
                changed(e)
            }
            "gate.decided" -> {
                upsertThread(e, "running", at)
                // Decided from anywhere (Flow page, Inbox, MCP, or by the engine's run mode): the gate's notification is done.
                if (d.str("gate") != "unlock") notifications.markDone(e.threadId)
                changed(e)
            }
            "budget.warn" -> notifications.create("budget", e.projectId, "The flow is near its budget", d.str("detail") ?: budgetText(d), link)
            "budget.stop" -> {
                upsertThread(e, "waiting", at)
                notifications.create("budget", e.projectId, "A budget cap stopped the flow", d.str("detail") ?: budgetText(d), link)
            }
            "guard.refused" -> notifications.create(
                "failed", e.projectId, "Guard reverted an edit",
                d.str("text") ?: listOfNotNull(d.str("agent"), d.str("path")?.let { "tried to change $it" }, d.str("phase")?.let { "in $it" }).joinToString(" "),
                link,
            )
            "index.done" -> {
                val repo = "/projects/${e.projectId}/repo"
                if (d.str("status") == "ready") {
                    notifications.create("finished", e.projectId, "Index ready: ${d.long("files") ?: 0} files, ${d.long("symbols") ?: 0} symbols",
                        "Agents can use the code graph for this project.", repo)
                } else {
                    notifications.create("failed", e.projectId, "Index failed: ${d.str("error")?.take(160) ?: "unknown reason"}",
                        "Agents find their way with grep instead. Rebuild it from the Repo page.", repo)
                }
            }
            "thread.done" -> {
                upsertThread(e, "done", at)
                notifications.markDone(e.threadId, read = false)
                notifications.create("finished", e.projectId, "The flow is done", d.str("title") ?: "All steps finished.", link)
                changed(e)
            }
            "thread.failed" -> {
                upsertThread(e, "failed", at)
                jdbc.update("UPDATE threads SET error = ? WHERE id = ?", d.str("error"), e.threadId)
                notifications.markDone(e.threadId, read = false)
                notifications.create("failed", e.projectId, "The flow failed", d.str("error")?.take(200) ?: "A step failed.", link)
                changed(e)
            }
        }
    }

    /** What waits for a person changed: every tab's sidebar (project waiting counts, the Inbox badge) reloads. */
    private fun changed(e: EngineEvent) {
        if (e.projectId.isNotBlank()) hub.publish(e.projectId, "project.changed", mapOf("id" to e.projectId))
    }

    /** How a job runs: the engine's "subscription" and "opencode" are both the plan; only "api" bills a key. */
    private fun modeOf(provider: String?, mode: String?): String? = when {
        provider == "fake" -> "fake"
        provider == null -> null
        mode == "api" -> "api"
        mode == null -> null
        else -> "subscription"
    }

    private fun budgetText(d: Map<String, Any?>): String {
        val used = d.long("tokens") ?: d.long("used")
        val cap = d.long("cap_tokens") ?: d.long("cap")
        return if (used != null && cap != null) "${used / 1000}k of ${cap / 1000}k tokens used." else ""
    }

    private fun upsertThread(e: EngineEvent, status: String?, at: String) {
        if (e.threadId.isBlank()) return
        val n = jdbc.update(
            "UPDATE threads SET status = COALESCE(?, status), updated_at = ? WHERE id = ?",
            status, at, e.threadId,
        )
        if (n == 0) {
            jdbc.update(
                "INSERT INTO threads(id, project_id, workflow_id, title, status, current, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                e.threadId, e.projectId, e.data.str("workflow_id") ?: e.data.str("workflow"), e.data.str("title") ?: "", status ?: "running", e.step, at, at,
            )
        }
    }

    /** A step can arrive before its agent.started when the engine retries; keep the job anyway. */
    private fun ensureCall(id: String, e: EngineEvent, at: String) {
        jdbc.update(
            "INSERT OR IGNORE INTO agent_calls(id, project_id, thread_id, step, status, started_at) VALUES (?, ?, ?, ?, 'running', ?)",
            id, e.projectId, e.threadId, e.step, at,
        )
    }
}

private fun Map<String, Any?>.str(key: String): String? = this[key]?.let { if (it is String) it else it.toString() }?.takeIf { it.isNotEmpty() }
private fun Map<String, Any?>.long(key: String): Long? = when (val v = this[key]) {
    is Number -> v.toLong()
    is String -> v.toDoubleOrNull()?.toLong()
    else -> null
}
private fun Map<String, Any?>.double(key: String): Double? = when (val v = this[key]) {
    is Number -> v.toDouble()
    is String -> v.toDoubleOrNull()
    else -> null
}
