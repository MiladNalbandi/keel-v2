package keel.api.events

import keel.api.budget.ProviderUsageStore
import keel.api.common.Json
import keel.api.common.Time
import keel.api.notifications.NoticeText
import keel.api.notifications.NoticeText.ASK
import keel.api.notifications.NoticeText.FAIL
import keel.api.notifications.NoticeText.OK
import keel.api.notifications.NoticeText.RUN
import keel.api.notifications.NoticeText.SKIP
import keel.api.notifications.NotificationService
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.ObjectProvider
import org.springframework.context.ApplicationEventPublisher
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

/** 0.18.64 events kept in the events table only: no screen gets them over SSE (agent.prompt: an agent call's whole prompt). */
val RECORD_ONLY = setOf("agent.prompt")

/** Published (in-process) after an engine event is stored, for features that follow threads (v0.5.0 tasks). */
data class EngineEventStored(val event: EngineEvent)

/**
 * Stores engine events: agent calls become jobs (agent_calls + agent_steps), thread status follows
 * the flow, some events become notifications, and every event fans out over SSE. A part's own events go to its
 * [EngineEventHandler] (by prefix): KeelBot's helper.*, the Graph plugin's index.done, approval.*.
 */
@Service
class EventService(
    private val jdbc: JdbcTemplate,
    private val notifications: NotificationService,
    private val hub: EventHub,
    private val usage: ProviderUsageStore,
    private val publisher: ApplicationEventPublisher,
    private val calls: AgentCalls,
    found: ObjectProvider<EngineEventHandler>,
    private val docs: keel.api.knowledge.DocHistoryService,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    /** Read on first use: a handler may itself need a bean that needs this service. */
    private val handlers by lazy { found.orderedStream().toList() }

    @Synchronized
    fun ingest(events: List<EngineEvent>): Int {
        var stored = 0
        for (raw in events) {
            if (raw.type.isBlank()) continue
            // keel 0.18.2 live text (helper.delta, agent.delta: the text an agent writes, so far): shown, never stored;
            // the step that follows holds the whole message
            if (isLive(raw.type)) {
                hub.publish(raw.projectId.ifBlank { null }, EventHub.channelOf(raw.type), raw)
                continue
            }
            // v0.15.9 a file version goes to doc_versions; the events table and SSE get it without its content
            val e = if (raw.type == "doc.version") runCatching { docs.record(raw) }.getOrElse {
                log.warn("could not store a version of {}: {}", raw.data["path"], it.message)
                raw.copy(data = raw.data - "content")
            } else raw
            try {
                apply(e)
                stored++
                publisher.publishEvent(EngineEventStored(e))
            } catch (ex: Exception) {
                log.warn("could not store event {} for {}: {}", e.type, e.threadId, ex.message)
            }
            // 0.18.64 a record only (an agent call's whole prompt, up to 120 KB): kept for GET /jobs/{id}/prompt, no screen shows it
            if (e.type in RECORD_ONLY) continue
            hub.publish(e.projectId.ifBlank { null }, EventHub.channelOf(e.type), e)
        }
        return stored
    }

    /** Live events: many per answer, only for the screens that watch it now: the text so far (`.delta`), and KeelBot's
     *  tool calls as keel TUI's tool lines show them (helper.tool; the helper.step beside each one is the record). */
    fun isLive(type: String): Boolean = type.endsWith(".delta") || type == "helper.tool"

    private fun apply(e: EngineEvent) {
        val at = e.at ?: Time.now()
        val d = e.data
        jdbc.update(
            "INSERT INTO events(thread_id, project_id, type, step, call_id, at, data_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            e.threadId, e.projectId, e.type, e.step, e.callId, at, Json.write(d),
        )
        core(e, at)
        val stored = e.copy(at = at)
        for (h in handlers) {
            if (!e.type.startsWith(h.prefix)) continue
            // one part's broken handler must not take the event from core or from the other parts
            try {
                h.handle(stored)
            } catch (ex: Exception) {
                log.warn("{} could not handle {} for {}: {}", h.javaClass.simpleName, e.type, e.threadId, ex.message)
            }
        }
    }

    /** Core's own side effects: threads, a flow's agent calls, gates, budget, provider usage and their notifications. */
    private fun core(e: EngineEvent, at: String) {
        val d = e.data
        val link = if (e.projectId.isNotBlank()) "/projects/${e.projectId}/flow" else null

        when (e.type) {
            "thread.started" -> upsertThread(e, "running", at)
            "step.started" -> {
                upsertThread(e, null, at)
                jdbc.update("UPDATE threads SET current = ?, phase = COALESCE(?, phase), updated_at = ? WHERE id = ?", e.step, d.str("phase"), at, e.threadId)
            }
            "step.finished" -> upsertThread(e, null, at)
            "keel.applied" -> docs.applied(e)

            "agent.started" -> {
                val id = calls.start(e, at, d.str("agent"), d.str("ac"))
                upsertThread(e, "running", at)
                jdbc.update("UPDATE threads SET phase = COALESCE(?, phase), ac = COALESCE(?, ac) WHERE id = ?", d.str("phase"), d.str("ac"), e.threadId)
                val agent = d.str("agent") ?: "An agent"
                notifications.create("started", e.projectId, "$RUN ${who(e)}$agent started", listOfNotNull(d.str("phase"), d.str("ac")).joinToString(" · "), "/jobs/$id",
                    threadId = e.threadId, step = e.step)
            }
            "agent.step" -> calls.step(e, at)
            // 0.18.17 the usage so far of a call that still runs: the Budget page, day caps and flow lists see it
            "agent.usage" -> calls.usage(e, at)
            "agent.finished" -> {
                val id = calls.finish(e, at) ?: return
                if (d.str("status") == "failed") {
                    val agent = jdbc.queryForObject("SELECT COALESCE(agent, 'An agent') FROM agent_calls WHERE id = ?", String::class.java, id)
                    val result = d.str("result") ?: "The agent step did not finish."
                    // B3 with its thread: the next decided gate (or the flow's end) marks it done, like the gate's own notification
                    notifications.create("failed", e.projectId, "$FAIL ${who(e)}$agent failed", result, "/jobs/$id", summary = failLines(result),
                        threadId = e.threadId, step = e.step)
                }
            }
            // 0.18.15 a code step (the setup ladder, a test suite, a commit) is a job too: stored like an agent's call,
            // so the Flow page, Jobs, keel TUI and KeelBot see what runs; its own event names keep agent.* for agents.
            // It starts too often to notify; a failed one does.
            "code.started" -> {
                calls.start(e, at, d.str("agent") ?: "keel", d.str("ac"))
                upsertThread(e, "running", at)
            }
            "code.step" -> calls.step(e, at)
            "code.finished" -> {
                val id = calls.finish(e, at) ?: return
                if (d.str("status") == "failed") {
                    val result = d.str("result") ?: "The step did not finish."
                    notifications.create("failed", e.projectId, "$FAIL ${who(e)}a code step failed", result, "/jobs/$id", summary = failLines(result),
                        threadId = e.threadId, step = e.step)
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
                // 0.18.55 a title a person understands and the engine's short lines (older engines: lines from the detail)
                val title = NoticeText.gateTitle(notifications.projectName(e.projectId), d.str("kind"), d.str("flow") ?: workflowOf(e.threadId),
                    e.step, d.str("step_name"), d.str("phase"), d.str("ac"), d.str("title"))
                val lines = (d["summary"] as? List<*>)?.mapNotNull { it?.toString() }?.takeIf { it.isNotEmpty() }
                notifications.create("review", e.projectId, title, d.str("detail") ?: d.str("title") ?: "", link,
                    threadId = e.threadId, step = e.step, summary = lines)
                changed(e)
            }
            "gate.decided" -> {
                upsertThread(e, "running", at)
                // Decided from anywhere (Flow page, Inbox, MCP, or by the engine's run mode): the gate's notification is done.
                if (d.str("gate") != "unlock") notifications.markDone(e.threadId)
                changed(e)
            }
            "budget.warn" -> notifications.create("budget", e.projectId, "$ASK ${who(e)}the flow is near its budget", d.str("detail") ?: budgetText(d), link,
                summary = budgetLines(d), threadId = e.threadId, step = e.step)
            "budget.stop" -> {
                upsertThread(e, "waiting", at)
                notifications.create("budget", e.projectId, "$ASK ${who(e)}a budget cap stopped the flow", d.str("detail") ?: budgetText(d), link,
                    summary = budgetLines(d), threadId = e.threadId, step = e.step)
            }
            "guard.refused" -> {
                val tried = listOfNotNull(d.str("agent"), d.str("path")?.let { "tried to change $it" }).joinToString(" ")
                notifications.create(
                    "failed", e.projectId, "$FAIL ${who(e)}guard undid an edit",
                    d.str("text") ?: listOfNotNull(tried.ifBlank { null }, d.str("phase")?.let { "in $it" }).joinToString(" "),
                    link, summary = listOfNotNull(tried.ifBlank { null }?.let { "$FAIL $it" }, d.str("phase")?.let { "$SKIP the edit was put back ($it step)" }),
                    threadId = e.threadId, step = e.step,
                )
            }
            "thread.done" -> {
                upsertThread(e, "done", at)
                notifications.markDone(e.threadId, read = false)
                @Suppress("UNCHECKED_CAST")
                val warned = (d["warnings"] as? List<*>).orEmpty().mapNotNull { it?.toString()?.ifBlank { null } }
                notifications.create("finished", e.projectId, NoticeText.doneTitle(flowLabel(e)), d.str("title") ?: "All steps finished.", link,
                    summary = if (warned.isEmpty()) listOf("$OK All steps finished")
                    else NoticeText.clip(listOf("$SKIP Finished with ${warned.size} warning${if (warned.size == 1) "" else "s"}") + warned.map { "$SKIP $it" }))
                changed(e)
            }
            "thread.failed" -> {
                upsertThread(e, "failed", at)
                jdbc.update("UPDATE threads SET error = ? WHERE id = ?", d.str("error"), e.threadId)
                notifications.markDone(e.threadId, read = false)
                val error = d.str("error") ?: "A step failed."
                notifications.create("failed", e.projectId, NoticeText.failedTitle(flowLabel(e), error), error, link,
                    summary = failLines(error, 2))
                changed(e)
            }
        }
    }

    /** What waits for a person changed: every tab's sidebar (project waiting counts, the Inbox badge) reloads. */
    private fun changed(e: EngineEvent) {
        if (e.projectId.isNotBlank()) hub.publish(e.projectId, "project.changed", mapOf("id" to e.projectId))
    }

    /** "shop: " before a title that names no flow, so the bell list says which project (blank: no project). */
    private fun who(e: EngineEvent): String = notifications.projectName(e.projectId.ifBlank { null })?.let { "$it: " } ?: ""

    /** The flow's name in a done or failed title: its own title, or "<project>: <workflow>" when it has none. */
    private fun flowLabel(e: EngineEvent): String? {
        val row = jdbc.query("SELECT title, workflow_id FROM threads WHERE id = ?", { rs, _ -> rs.getString(1) to rs.getString(2) }, e.threadId)
            .firstOrNull()
        row?.first?.ifBlank { null }?.let { return it }
        val wf = row?.second ?: e.data.str("workflow_id") ?: e.data.str("workflow") ?: return null
        return listOfNotNull(notifications.projectName(e.projectId.ifBlank { null }), wf).joinToString(": ")
    }

    private fun workflowOf(threadId: String): String? =
        jdbc.query("SELECT workflow_id FROM threads WHERE id = ?", { rs, _ -> rs.getString(1) }, threadId).firstOrNull()

    /** A failure's short lines: its first facts, each with ✗ (no exception class names). */
    private fun failLines(text: String, max: Int = NoticeText.MAX_LINES): List<String> =
        NoticeText.clip(NoticeText.fromBody(text, max).mapIndexed { i, l ->
            if (l.startsWith("+")) l else "$FAIL " + if (i == 0) NoticeText.shortReason(l, NoticeText.LINE) else l
        }, max)

    private fun budgetLines(d: Map<String, Any?>): List<String>? = budgetText(d).trimEnd('.').ifBlank { null }?.let { listOf("$ASK $it") }

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
}
