package keel.api.helper

import keel.api.approvals.McpAskEvents
import keel.api.common.Time
import keel.api.events.AgentCalls
import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.notifications.NotificationService
import org.springframework.stereotype.Component

/**
 * KeelBot's engine events (the plugin's engine, keel_plugin_keelbot.helper). Its turns are agent calls (agent "helper",
 * so the budget, Live agents and Jobs count them) but never a flow. A command that waits for the person's OK is a
 * notification until it is answered (the question itself is core's: keel.api.approvals). keel2 mcp's questions come on
 * the same events with the session "mcp": core makes their notification ([McpAskEvents]).
 */
@Component
class HelperEvents(private val calls: AgentCalls, private val notifications: NotificationService) : EngineEventHandler {
    override val prefix = "helper."

    override fun handle(event: EngineEvent) {
        val e = event
        val at = e.at ?: Time.now()
        val d = e.data
        when (e.type) {
            "helper.started" -> calls.start(e, at, d.text("agent") ?: "helper", null)
            "helper.step" -> calls.step(e, at)
            "helper.finished" -> calls.finish(e, at)
            // a Fix or side chat's command waits for the person's OK (a card in KeelBot's panel and the Inbox)
            "helper.permission" -> if (e.threadId != McpAskEvents.MCP) notifications.create("review", e.projectId,
                "KeelBot asks to run a command", d.text("command")?.take(300) ?: "", "/projects/${e.projectId}/repo",
                threadId = e.threadId, step = "permission")
            "helper.permission.answered" -> if (e.threadId != McpAskEvents.MCP) notifications.markDone(e.threadId ?: "")
        }
    }
}

/** A field of an event's data as text (core's own helper is internal to keel's api). */
private fun Map<String, Any?>.text(key: String): String? =
    this[key]?.let { if (it is String) it else it.toString() }?.takeIf { it.isNotEmpty() }
