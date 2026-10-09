package keel.api.marketplace

import com.fasterxml.jackson.databind.JsonNode
import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.events.EventService
import keel.api.notifications.NotificationService
import org.springframework.beans.factory.ObjectProvider
import org.springframework.stereotype.Component
import org.springframework.stereotype.Service
import java.util.concurrent.ConcurrentHashMap

/**
 * What a person (or an approved request) changes: install, update, roll back, remove, on or off. Each goes to the
 * engine; then it is an event in keel's event log (who and when; plugin.installed, plugin.updated, …), the restart
 * banner shows, and an install says so in a notification: "<Title> is installed: restart keel to use it".
 */
@Service
class PluginChanges(
    private val marketplace: Marketplace,
    private val restarts: RestartPlan,
    private val notifications: NotificationService,
    private val log: PluginEventLog,
) {
    /** Plugins whose install the engine answered as done lately: its event plugin.install.done for it is an echo. */
    private val told = ConcurrentHashMap<String, Long>()

    fun install(name: String, version: String?, by: String = PERSON, request: String? = null): JsonNode {
        val res = marketplace.install(name, version)
        done("plugin.installed", name, res, version, by, mapOf("request" to request))
        return res
    }

    fun update(name: String, version: String?, by: String = PERSON): JsonNode {
        val res = marketplace.update(name, version)
        done("plugin.updated", name, res, version, by)
        return res
    }

    fun rollback(name: String, by: String = PERSON): JsonNode {
        val res = marketplace.rollback(name)
        done("plugin.rolled_back", name, res, null, by)
        return res
    }

    fun switch(name: String, on: Boolean, by: String = PERSON): JsonNode {
        val res = marketplace.switch(name, on)
        restarts.changed()
        log.record(if (on) "plugin.turned_on" else "plugin.turned_off", mapOf("name" to name, "by" to by, "also" to res.path("dependents").takeIf { it.isArray }))
        return res
    }

    fun remove(name: String, data: String, by: String = PERSON): JsonNode {
        val res = marketplace.remove(name, data)
        restarts.changed()
        log.record("plugin.removed", mapOf("name" to name, "data" to data, "by" to by))
        return res
    }

    fun installFile(path: String, by: String = PERSON): JsonNode {
        val res = marketplace.installFile(path)
        val file = path.trim().substringAfterLast('/')
        val name = res.path("name").asText("").ifBlank { FILE_NAME.find(file)?.groupValues?.get(1) ?: file }
        done("plugin.installed", name, res, null, by, mapOf("source" to "file", "path" to path))
        return res
    }

    /**
     * The engine says an install ended (its event plugin.install.done, when it installs in the background): tell the
     * person, once.
     */
    fun engineDone(data: Map<String, Any?>) {
        val name = data["name"]?.toString()?.ifBlank { null } ?: return
        restarts.changed()
        val now = System.currentTimeMillis()
        val answered = told.remove(name)
        if (answered != null && now - answered < TOLD_MS) return
        tell("plugin.installed", name, data["title"]?.toString(), data["version"]?.toString())
        told[name] = now          // the same event again is an echo too
    }

    private fun done(type: String, name: String, res: JsonNode, asked: String?, by: String, more: Map<String, Any?> = emptyMap()) {
        restarts.changed()
        val version = res.path("version").asText("").ifBlank { asked }
        log.record(type, mapOf("name" to name, "version" to version, "by" to by) + more.filterValues { it != null })
        // an install the engine runs in the background tells the person with its event plugin.install.done
        if (res.path("status").asText("") in BACKGROUND) {
            told.remove(name)
            return
        }
        tell(type, name, res.path("title").asText("").ifBlank { null }, version, res.path("restart").takeIf { it.isBoolean }?.asBoolean())
        val now = System.currentTimeMillis()
        told.values.removeIf { now - it > TOLD_MS }
        if (type == "plugin.installed") told[name] = now
    }

    private fun tell(type: String, name: String, title: String?, version: String?, restart: Boolean? = null) {
        val shown = title ?: name
        val head = if (type == "plugin.rolled_back") "$shown is back on ${version ?: "its kept version"}" else "$shown is installed"
        val text = if (restart == false) head else "$head: restart keel to use it"
        notifications.create("finished", null, text, listOfNotNull(version?.let { "Version $it." }, "Control › Plugins has the Restart keel button.")
            .joinToString(" "), "/plugins")
    }

    companion object {
        const val PERSON = "person"
        /** An engine answer with one of these statuses: the install goes on in the background. */
        val BACKGROUND = setOf("started", "running", "queued")
        const val TOLD_MS = 5 * 60_000L
        /** hello-1.0.0.kplug: the plugin's name before its version. */
        private val FILE_NAME = Regex("""^(.+?)-\d[^/]*\.kplug$""")
    }
}

/** keel's event log: the marketplace's events, with who and when, like the engine's (table events, the web's stream). */
@Component
class PluginEventLog(private val events: ObjectProvider<EventService>) {
    fun record(type: String, data: Map<String, Any?>) {
        events.getObject().ingest(listOf(EngineEvent(type = type, step = "plugins", data = data.filterValues { it != null })))
    }
}

/** The engine's plugin.install.done (an install it ran in the background) becomes the "is installed" notification. */
@Component
class PluginInstallEvents(private val changes: ObjectProvider<PluginChanges>) : EngineEventHandler {
    override val prefix = "plugin.install.done"

    override fun handle(event: EngineEvent) = changes.getObject().engineDone(event.data)
}
