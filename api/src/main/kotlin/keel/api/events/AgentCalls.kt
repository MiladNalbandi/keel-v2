package keel.api.events

import keel.api.common.Json
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component

/**
 * Agent calls (jobs): one row per agent run (agent_calls), its steps (agent_steps) and how it ended. Core keeps a flow's
 * agents from agent.* events; a part keeps its own runs the same way (KeelBot's helper.* events), so the budget, Live
 * agents and Jobs count them.
 */
@Component
class AgentCalls(private val jdbc: JdbcTemplate) {

    /** The call's id: the engine's call id, else one made from the thread, the step and the time. */
    fun idOf(e: EngineEvent, at: String): String = e.callId ?: "${e.threadId}:${e.step}:$at"

    /** A run started (again, after a retry: it runs once more). */
    fun start(e: EngineEvent, at: String, agent: String?, ac: String?): String {
        val d = e.data
        val id = idOf(e, at)
        jdbc.update(
            """INSERT INTO agent_calls(id, project_id, thread_id, agent, provider, model, step, phase, ac, status, started_at, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
               ON CONFLICT(id) DO UPDATE SET status = 'running', mode = COALESCE(excluded.mode, agent_calls.mode)""",
            id, e.projectId, e.threadId, agent, d.str("provider"), d.str("model"), e.step,
            d.str("phase"), ac, at, modeOf(d.str("provider"), d.str("mode")),
        )
        return id
    }

    /** One step of a run (text, tool, read, edit, ...); a step that arrives twice is kept once. */
    fun step(e: EngineEvent, at: String) {
        val d = e.data
        val id = e.callId ?: return
        ensure(id, e, at)
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

    /** The run ended: its status, tokens, cost and result. Returns the call id (null when the event names none). */
    fun finish(e: EngineEvent, at: String): String? {
        val d = e.data
        val id = e.callId ?: return null
        ensure(id, e, at)
        jdbc.update(
            """UPDATE agent_calls SET status = ?, ended_at = ?, tokens_in = ?, tokens_out = ?, tokens_cached = ?, cost_usd = ?,
               premium_requests = ?, result = ? WHERE id = ?""",
            d.str("status") ?: "done", at, d.long("tokens_in") ?: 0, d.long("tokens_out") ?: 0, d.long("tokens_cached") ?: 0,
            d.double("cost_usd") ?: 0.0, d.long("premium_requests") ?: 0, d["result"]?.let { if (it is String) it else Json.write(it) }, id,
        )
        return id
    }

    /** A step can arrive before its start when the engine retries; keep the job anyway. */
    private fun ensure(id: String, e: EngineEvent, at: String) {
        jdbc.update(
            "INSERT OR IGNORE INTO agent_calls(id, project_id, thread_id, step, status, started_at) VALUES (?, ?, ?, ?, 'running', ?)",
            id, e.projectId, e.threadId, e.step, at,
        )
    }

    /** How a job runs: the engine's "subscription" and "opencode" are both the plan; only "api" bills a key. */
    private fun modeOf(provider: String?, mode: String?): String? = when {
        provider == "fake" -> "fake"
        provider == null -> null
        mode == "api" -> "api"
        mode == null -> null
        else -> "subscription"
    }
}

/** An event's text field, null when it is missing or empty. */
internal fun Map<String, Any?>.str(key: String): String? = this[key]?.let { if (it is String) it else it.toString() }?.takeIf { it.isNotEmpty() }

internal fun Map<String, Any?>.long(key: String): Long? = when (val v = this[key]) {
    is Number -> v.toLong()
    is String -> v.toDoubleOrNull()?.toLong()
    else -> null
}

internal fun Map<String, Any?>.double(key: String): Double? = when (val v = this[key]) {
    is Number -> v.toDouble()
    is String -> v.toDoubleOrNull()
    else -> null
}
