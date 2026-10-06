package keel.api.flow

import keel.api.events.EngineEventStored
import org.springframework.context.event.EventListener
import org.springframework.stereotype.Component

/** v0.7.x: a flow that a flow hands over to (change → feature) works where its parent worked: the same worktree. */
@Component
class FlowWorktreeEvents(private val flows: FlowService) {
    @EventListener
    fun on(stored: EngineEventStored) {
        val e = stored.event
        if (e.type != "thread.started") return
        val parent = (e.data["parent"] as? Map<*, *>)?.get("thread_id")?.toString() ?: return
        if (parent.isNotBlank() && e.threadId.isNotBlank()) flows.inheritWorktree(e.threadId, parent)
    }
}
