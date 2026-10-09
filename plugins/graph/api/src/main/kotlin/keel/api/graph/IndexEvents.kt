package keel.api.graph

import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.notifications.NotificationService
import org.springframework.stereotype.Component

/**
 * The end of a project's scan (engine runtime/scan.py, index.done): a notification says whether agents get the code
 * graph. The Graph plugin's (plugins/graph), in keel's package keel.api.graph; moved from keel.api.projects.
 */
@Component
class IndexEvents(private val notifications: NotificationService) : EngineEventHandler {
    override val prefix = "index.done"

    override fun handle(event: EngineEvent) {
        if (event.type != prefix) return
        val d = event.data
        val repo = "/projects/${event.projectId}/repo"
        if (d.str("status") == "ready") {
            notifications.create("finished", event.projectId, "Index ready: ${d.long("files") ?: 0} files, ${d.long("symbols") ?: 0} symbols",
                "Agents can use the code graph for this project.", repo)
        } else {
            notifications.create("failed", event.projectId, "Index failed: ${d.str("error")?.take(160) ?: "unknown reason"}",
                "Agents find their way with grep instead. Rebuild it from the Repo page.", repo)
        }
    }
}

// An event's fields, as keel's own handlers read them (keel.api.events keeps its helpers internal to keel's module).
private fun Map<String, Any?>.str(key: String): String? = this[key]?.let { if (it is String) it else it.toString() }?.takeIf { it.isNotEmpty() }

private fun Map<String, Any?>.long(key: String): Long? = when (val v = this[key]) {
    is Number -> v.toLong()
    is String -> v.toDoubleOrNull()?.toLong()
    else -> null
}
