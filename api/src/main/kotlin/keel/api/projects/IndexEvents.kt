package keel.api.projects

import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.events.long
import keel.api.events.str
import keel.api.notifications.NotificationService
import org.springframework.stereotype.Component

/** The end of a project's scan (engine runtime/scan.py, index.done): a notification says whether agents get the code graph. */
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
