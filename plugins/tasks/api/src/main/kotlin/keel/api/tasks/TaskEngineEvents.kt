package keel.api.tasks

import keel.api.events.EngineEvent
import keel.api.events.EngineEventStored
import org.slf4j.LoggerFactory
import org.springframework.context.event.EventListener
import org.springframework.stereotype.Component
import java.util.concurrent.Executors

/**
 * Follows the flows that tasks started: the PR the flow opened, its end, its failure, a hand-off to another flow. The
 * event thread only checks (one indexed query) whether a task follows the thread; the work (Jira, GitHub) runs on one
 * background worker, in order, so ingesting engine events never waits for Jira.
 */
@Component
class TaskEngineEvents(private val tasks: TaskService, private val store: TaskStore, private val props: TaskProperties) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "task-lifecycle").apply { isDaemon = true } }

    @EventListener
    fun on(stored: EngineEventStored) {
        val e = stored.event
        val follows = when (e.type) {
            "thread.started" -> (e.data["parent"] as? Map<*, *>)?.get("thread_id")?.toString()?.let { store.byThread(it) } != null
            "step.finished" -> TaskService.PR_OPENED.containsMatchIn(e.data["note"]?.toString() ?: "") && store.byThread(e.threadId) != null
            "thread.done", "thread.failed" -> e.threadId.isNotBlank() && store.byThread(e.threadId) != null
            else -> false
        }
        if (!follows) return
        if (props.inlineEffects) run(e) else worker.execute { run(e) }
    }

    private fun run(e: EngineEvent) {
        try {
            tasks.onEngineEvent(e)
        } catch (ex: Exception) {
            log.warn("task lifecycle for {} {}: {}", e.type, e.threadId, ex.message)
        }
    }
}
