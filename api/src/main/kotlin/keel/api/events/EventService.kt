package keel.api.events

import keel.api.budget.ProviderUsageStore
import keel.api.common.Json
import keel.api.common.Time
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

/** Published (in-process) after an engine event is stored, for features that follow threads (v0.5.0 tasks). */
data class EngineEventStored(val event: EngineEvent)

/**
 * Stores engine events: agent calls become jobs (agent_calls + agent_steps), thread status follows
 * the flow, some events become notifications, and every event fans out over SSE. A part's own events go to its
 * [EngineEventHandler] (by prefix): KeelBot's helper.*, the index's index.done, approval.*.
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
) {
    private val log = LoggerFactory.getLogger(javaClass)

    /** Read on first use: a handler may itself need a bean that needs this service. */
    private val handlers by lazy { found.orderedStream().toList() }

    @Synchronized
    fun ingest(events: List<EngineEvent>): Int {
        var stored = 0
        for (e in events) {
            if (e.type.isBlank()) continue
            try {
                apply(e)
                stored++
                publisher.publishEvent(EngineEventStored(e))
            } catch (ex: Exception) {
                log.warn("could not store event {} for {}: {}", e.type, e.threadId, ex.message)
            }
            hub.publish(e.projectId.ifBlank { null }, EventHub.channelOf(e.type), e)
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

            "agent.started" -> {
                val id = calls.start(e, at, d.str("agent"), d.str("ac"))
                upsertThread(e, "running", at)
                jdbc.update("UPDATE threads SET phase = COALESCE(?, phase), ac = COALESCE(?, ac) WHERE id = ?", d.str("phase"), d.str("ac"), e.threadId)
                val agent = d.str("agent") ?: "An agent"
                notifications.create("started", e.projectId, "$agent started", listOfNotNull(d.str("phase"), d.str("ac")).joinToString(" · "), "/jobs/$id")
            }
            "agent.step" -> calls.step(e, at)
            "agent.finished" -> {
                val id = calls.finish(e, at) ?: return
                if (d.str("status") == "failed") {
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
