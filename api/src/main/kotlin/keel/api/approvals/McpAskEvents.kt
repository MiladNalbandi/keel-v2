package keel.api.approvals

import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.events.str
import keel.api.notifications.NotificationService
import org.springframework.stereotype.Component

/**
 * keel2 mcp --write's questions (engine keel_engine/approvals.py ask_person): Claude Code's acting tool waits for the
 * person's answer in the Inbox. For such a question the engine sends helper.permission and helper.permission.answered
 * on the session "mcp", as keel 0.15.1 did; this makes them the "Claude Code asks" notification and clears it. The
 * questions of KeelBot's own chats are its plugin's (plugins/keelbot, HelperEvents).
 */
@Component
class McpAskEvents(private val notifications: NotificationService) : EngineEventHandler {
    override val prefix = "helper.permission"

    override fun handle(event: EngineEvent) {
        if (event.threadId != MCP) return
        val d = event.data
        when (event.type) {
            "helper.permission" -> notifications.create("review", event.projectId, d.str("title") ?: "Claude Code asks",
                d.str("command")?.take(300) ?: "", "/inbox", threadId = d.str("id"), step = "permission")
            "helper.permission.answered" -> notifications.markDone(d.str("id") ?: "")
        }
    }

    companion object {
        /** The engine's session for keel2 mcp's questions (keel_engine/approvals.py MCP_SESSION). */
        const val MCP = ApprovalInboxSource.MCP
    }
}
