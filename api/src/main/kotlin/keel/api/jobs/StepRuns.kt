package keel.api.jobs

import com.fasterxml.jackson.annotation.JsonInclude
import keel.api.knowledge.DocHistoryService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.time.Instant

/**
 * keel 0.18.11 what one step of a flow really did (the Flow page's blocks): its agent calls added up per step and
 * criterion. A try is a round of calls (calls that overlap in time, like parallel lanes, are one try); the model and
 * effort are the last call's, the time is the calls' own working time (not the time a gate waited), and the result is
 * the first line of what the last call answered.
 */
data class StepRun(
    val step: String,
    val ac: String?,
    val tries: Int,
    val status: String,
    val agent: String?,
    val provider: String?,
    val model: String?,
    val effort: String?,
    val startedAt: String,
    val endedAt: String?,
    val ms: Long,
    val tokensIn: Long,
    val tokensOut: Long,
    val tokensCached: Long,
    val costUsd: Double,
    val result: String?,
    val callId: String,
)

/** 0.18.64 one side of a step diff: an agent call (id, agent) or a document version (id, version). */
data class DiffSide(val id: String, val agent: String?, val version: Int?, val at: String)
data class StepRunCount(val step: String, val ac: String?, val runs: Int, val kind: String)

/** 0.18.64 GET .../flows/{tid}/step-diff: kind document | answer | none (`why`) | list (`steps`, no step asked). */
@JsonInclude(JsonInclude.Include.NON_NULL)
data class StepDiff(
    val step: String?,
    val ac: String?,
    val kind: String,
    val runs: Int,
    val path: String? = null,
    val before: DiffSide? = null,
    val after: DiffSide? = null,
    val diff: String? = null,
    val why: String? = null,
    val steps: List<StepRunCount>? = null,
)

@Service
class StepRunService(private val jdbc: JdbcTemplate) {

    private data class Call(
        val id: String, val step: String, val ac: String?, val status: String, val agent: String?, val provider: String?, val model: String?,
        val effort: String?, val startedAt: String, val endedAt: String?, val tokensIn: Long, val tokensOut: Long, val tokensCached: Long,
        val costUsd: Double, val result: String?,
    )

    fun of(project: String, thread: String, now: Instant = Instant.now()): List<StepRun> {
        val calls = jdbc.query(
            """SELECT id, step, ac, status, agent, provider, model, effort, started_at, ended_at, tokens_in, tokens_out, tokens_cached,
                      cost_usd, result
               FROM agent_calls WHERE project_id = ? AND thread_id = ? ORDER BY started_at, id""",
            { rs, _ ->
                Call(
                    rs.getString("id"), rs.getString("step") ?: "", rs.getString("ac"), rs.getString("status"), rs.getString("agent"),
                    rs.getString("provider"), rs.getString("model"), rs.getString("effort"), rs.getString("started_at"), rs.getString("ended_at"),
                    rs.getLong("tokens_in"), rs.getLong("tokens_out"), rs.getLong("tokens_cached"), rs.getDouble("cost_usd"), rs.getString("result"),
                )
            },
            project, thread,
        )
        return calls.groupBy { it.step to it.ac }.map { (key, cs) ->
            val last = cs.last()
            StepRun(
                step = key.first, ac = key.second, tries = tries(cs, now), status = last.status, agent = last.agent, provider = last.provider,
                model = last.model, effort = last.effort, startedAt = cs.first().startedAt, endedAt = last.endedAt,
                ms = cs.sumOf { (end(it, now) - start(it)).coerceAtLeast(0) },
                tokensIn = cs.sumOf { it.tokensIn }, tokensOut = cs.sumOf { it.tokensOut }, tokensCached = cs.sumOf { it.tokensCached },
                costUsd = cs.sumOf { it.costUsd }, result = firstLine(last.result), callId = last.id,
            )
        }
    }

    /**
     * 0.18.64 what a step's last run changed against the run before (a retry, a send-back, a reject; the MCP tool
     * keel_flow_diff): the document the step wrote (the versions keel kept of it in this flow), else its agent's answer
     * (the newest call against the same agent's call before). No step: the steps that ran more than once.
     */
    fun diff(project: String, thread: String, step: String?, ac: String?, now: Instant = Instant.now()): StepDiff {
        val calls = jdbc.query(
            """SELECT id, step, ac, status, agent, provider, model, effort, started_at, ended_at, tokens_in, tokens_out, tokens_cached,
                      cost_usd, result
               FROM agent_calls WHERE project_id = ? AND thread_id = ? ORDER BY started_at, id""",
            { rs, _ ->
                Call(
                    rs.getString("id"), rs.getString("step") ?: "", rs.getString("ac"), rs.getString("status"), rs.getString("agent"),
                    rs.getString("provider"), rs.getString("model"), rs.getString("effort"), rs.getString("started_at"), rs.getString("ended_at"),
                    rs.getLong("tokens_in"), rs.getLong("tokens_out"), rs.getLong("tokens_cached"), rs.getDouble("cost_usd"), rs.getString("result"),
                )
            },
            project, thread,
        )
        val docs = jdbc.query(
            "SELECT id, path, version, step, at, content FROM doc_versions WHERE project_id = ? AND thread_id = ? AND deleted = 0 ORDER BY version",
            { rs, _ -> Doc(rs.getString("id"), rs.getString("path"), rs.getInt("version"), rs.getString("step") ?: "", rs.getString("at"), rs.getString("content")) },
            project, thread,
        )
        if (step.isNullOrBlank()) {
            val byCalls = calls.groupBy { it.step to it.ac }.mapNotNull { (k, cs) ->
                tries(cs, now).takeIf { it > 1 }?.let { StepRunCount(k.first, k.second, it, "answer") }
            }
            val byDocs = docs.groupBy { it.step to it.path }.mapNotNull { (k, vs) ->
                vs.size.takeIf { it > 1 }?.let { StepRunCount(k.first, null, it, "document ${k.second}") }
            }
            return StepDiff(step = null, ac = null, kind = "list", runs = 0, steps = byDocs + byCalls)
        }
        // the document: the newest file this step wrote twice or more in this flow
        val written = docs.filter { it.step == step }.groupBy { it.path }.values.filter { it.size > 1 }.maxByOrNull { it.last().at }
        if (written != null && ac.isNullOrBlank()) {
            val (a, b) = written.takeLast(2)
            return StepDiff(step, null, "document", written.size, path = b.path,
                before = DiffSide(a.id, null, a.version, a.at), after = DiffSide(b.id, null, b.version, b.at),
                diff = DocHistoryService.unified(a.content.orEmpty(), b.content.orEmpty(), b.path))
        }
        val mine = calls.filter { it.step == step && (ac.isNullOrBlank() || it.ac == ac) && it.result != null && it.status == "done" }
        val b = mine.lastOrNull()
        val a = b?.let { last -> mine.dropLast(1).lastOrNull { it.agent == last.agent } }
        if (a == null || b == null) {
            val ran = calls.count { it.step == step && (ac.isNullOrBlank() || it.ac == ac) }
            return StepDiff(step, ac, "none", ran, why = if (ran == 0) "has no agent call in this flow (keel_flow_diff with no step lists the steps that ran more than once)."
                else "ran once (or its other runs ended without an answer), so there is nothing to compare.")
        }
        return StepDiff(step, ac, "answer", mine.count { it.agent == b.agent },
            before = DiffSide(a.id, a.agent, null, a.startedAt), after = DiffSide(b.id, b.agent, null, b.startedAt),
            diff = DocHistoryService.unified(a.result.orEmpty(), b.result.orEmpty(), "$step.answer"))
    }

    private data class Doc(val id: String, val path: String, val version: Int, val step: String, val at: String, val content: String?)

    /** Rounds of calls: a call that starts after every earlier call ended is a new try. */
    private fun tries(cs: List<Call>, now: Instant): Int {
        var n = 0
        var busyUntil = Long.MIN_VALUE
        cs.sortedBy { start(it) }.forEach { c ->
            if (start(c) >= busyUntil) n++
            busyUntil = maxOf(busyUntil, end(c, now))
        }
        return n
    }

    private fun start(c: Call): Long = millis(c.startedAt) ?: 0L
    private fun end(c: Call, now: Instant): Long = c.endedAt?.let(::millis) ?: if (c.status == "running") now.toEpochMilli() else start(c)
    private fun millis(t: String): Long? = try { Instant.parse(t).toEpochMilli() } catch (e: Exception) { null }

    companion object {
        /** The first line that says something, without markdown marks, at most 100 characters. */
        fun firstLine(text: String?): String? {
            val line = text.orEmpty().lineSequence()
                .map { it.trim().trimStart('#', '>', '-', '*', ' ').replace("**", "").replace("__", "").replace("`", "").trim() }
                .firstOrNull { it.isNotEmpty() && !it.all { ch -> ch == '-' || ch == '=' || ch == '|' } }
                ?.replace(Regex("\\s+"), " ")
                ?: return null
            return if (line.length <= 100) line else line.take(99).trimEnd() + "…"
        }
    }
}
