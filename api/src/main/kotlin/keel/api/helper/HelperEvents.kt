package keel.api.helper

import keel.api.common.Time
import keel.api.events.AgentCalls
import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.events.str
import keel.api.notifications.NotificationService
import org.springframework.stereotype.Component

/**
 * KeelBot's engine events (engine runtime/helper.py). Its turns are agent calls (agent "helper", so the budget, Live
 * agents and Jobs count them) but never a flow. A command that waits for the person's OK is a notification until it is
 * answered (the question itself is core's: keel.api.approvals).
 */
@Component
class HelperEvents(private val calls: AgentCalls, private val notifications: NotificationService) : EngineEventHandler {
    override val prefix = "helper."

    override fun handle(event: EngineEvent) {
        val e = event
        val at = e.at ?: Time.now()
        val d = e.data
        when (e.type) {
            "helper.started" -> calls.start(e, at, d.str("agent") ?: "helper", null)
            "helper.step" -> calls.step(e, at)
            "helper.finished" -> calls.finish(e, at)
            // a Fix or side chat's command waits for the person's OK (a card in KeelBot's panel and the Inbox)
            "helper.permission" -> if (e.threadId == MCP) {
                // keel2 mcp --write: Claude Code waits for the answer in the Inbox
                notifications.create("review", e.projectId, d.str("title") ?: "Claude Code asks", d.str("command")?.take(300) ?: "",
                    "/inbox", threadId = d.str("id"), step = "permission")
            } else notifications.create("review", e.projectId, "KeelBot asks to run a command",
                d.str("command")?.take(300) ?: "", "/projects/${e.projectId}/repo", threadId = e.threadId, step = "permission")
            "helper.permission.answered" -> notifications.markDone((if (e.threadId == MCP) d.str("id") else e.threadId) ?: "")
        }
    }

    companion object {
        /** The engine's session for keel2 mcp's questions (runtime/helper.py MCP_SESSION). */
        const val MCP = "mcp"
    }
}
