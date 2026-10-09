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
 * engine (with who, for its own log); then it is an event in keel's event log (who and when: plugin.installed,
 * plugin.updated, …), the restart banner shows, and an install says so in a notification: "<Title> is installed:
 * restart keel to use it". The engine answers an install or update when it is done.
 */
@Service
class PluginChanges(
    private val marketplace: Marketplace,
    private val restarts: RestartPlan,
    private val notifications: NotificationService,
    private val log: PluginEventLog,
) {
    /** Installs and updates the api runs now: the engine's plugin.install.done for them is told by the api itself. */
    private val running = ConcurrentHashMap.newKeySet<String>()

    /** When the api told about an install lately: the engine's event that comes after the answer is an echo. */
    private val told = ConcurrentHashMap<String, Long>()

    fun install(name: String, version: String?, by: String = PERSON, request: String? = null): JsonNode {
        val res = waiting(name) { marketplace.install(name, version, by) }
        done("plugin.installed", name, res, version, by, mapOf("request" to request, "installed" to res.path("installed").takeIf { it.isArray }))
        return res
    }

    /** [allowMore]: a person approved the permissions the new version adds. */
    fun update(name: String, version: String?, by: String = PERSON, allowMore: Boolean = false, request: String? = null): JsonNode {
        val res = waiting(name) { marketplace.update(name, version, by, allowMore) }
        // the version before: its entry in `installed` says it (from)
        val from = res.path("from").asText("").ifBlank { null }
            ?: res.path("installed").firstOrNull { it.path("name").asText() == name }?.path("from")?.asText("")?.ifBlank { null }
        done("plugin.updated", name, res, version, by, mapOf("from" to from, "request" to request))
        return res
    }

    fun rollback(name: String, by: String = PERSON): JsonNode {
        val res = marketplace.rollback(name, by)
        done("plugin.rolled_back", name, res, null, by, mapOf("from" to res.path("from").asText("").ifBlank { null }))
        return res
    }

    fun switch(name: String, on: Boolean, by: String = PERSON): JsonNode {
        val res = marketplace.switch(name, on, by)
        restarts.changed()
        log.record(if (on) "plugin.turned_on" else "plugin.turned_off",
            mapOf("name" to name, "by" to by, "also" to res.path("also").takeIf { it.isArray && it.size() > 0 }))
        return res
    }

    fun remove(name: String, data: String, by: String = PERSON): JsonNode {
        val res = marketplace.remove(name, data, by)
        restarts.changed()
        log.record("plugin.removed", mapOf("name" to name, "version" to res.path("removed").asText("").ifBlank { null }, "data" to data,
            "by" to by, "back_to_image" to res.path("back_to_image").asText("").ifBlank { null }))
        return res
    }

    fun installFile(path: String, by: String = PERSON): JsonNode {
        val file = path.trim().substringAfterLast('/')
        val guess = FILE_NAME.find(file)?.groupValues?.get(1) ?: file
        val res = waiting(guess) { marketplace.installFile(path, by) }
        val name = res.path("name").asText("").ifBlank { guess }
        done("plugin.installed", name, res, null, by, mapOf("source" to "file", "path" to path))
        return res
    }

    /**
     * The engine says an install or update ended (its event plugin.install.done). One the api ran tells the person
     * already; one from elsewhere (keel2 plugins install) is told here, once.
     */
    fun engineDone(data: Map<String, Any?>) {
        val name = data["name"]?.toString()?.ifBlank { null } ?: return
        restarts.changed()
        if (name in running) return
        val now = System.currentTimeMillis()
        val answered = told.remove(name)
        if (answered != null && now - answered < TOLD_MS) return
        val update = data["update"] == true
        tell(if (update) "plugin.updated" else "plugin.installed", name, data["title"]?.toString(), data["version"]?.toString())
        told[name] = now          // the same event again is an echo too
    }

    private fun <T> waiting(name: String, call: () -> T): T {
        running += name
        try {
            return call()
        } finally {
            running -= name
        }
    }

    private fun done(type: String, name: String, res: JsonNode, asked: String?, by: String, more: Map<String, Any?> = emptyMap()) {
        restarts.changed()
        val version = res.path("version").asText("").ifBlank { asked }
        log.record(type, mapOf("name" to name, "version" to version, "by" to by) + more.filterValues { it != null })
        tell(type, name, res.path("title").asText("").ifBlank { null } ?: titleOf(name), version)
        val now = System.currentTimeMillis()
        told.values.removeIf { now - it > TOLD_MS }
        if (type != "plugin.rolled_back") told[name] = now
    }

    /** An installed plugin's title (a rollback's answer has none); null when the engine does not say. */
    private fun titleOf(name: String): String? = runCatching {
        marketplace.installed().path("plugins").firstOrNull { it.path("name").asText() == name }?.path("title")?.asText("")?.ifBlank { null }
    }.getOrNull()

    private fun tell(type: String, name: String, title: String?, version: String?) {
        val shown = title ?: name
        val head = when (type) {
            "plugin.rolled_back" -> "$shown is back on ${version ?: "its kept version"}"
            "plugin.updated" -> "$shown is updated"
            else -> "$shown is installed"
        }
        notifications.create("finished", null, "$head: restart keel to use it",
            listOfNotNull(version?.let { "Version $it." }, "Control › Plugins has the Restart keel button.").joinToString(" "), "/plugins")
    }

    companion object {
        const val PERSON = "person"
        const val TOLD_MS = 5 * 60_000L
        /** hello-1.0.0.kplug: the plugin's name before its version. */
        private val FILE_NAME = Regex("""^(.+?)-\d[^/]*\.kplug$""")
    }
}

/** keel's event log: the marketplace's events, with who and when, like the engine's (table events, the web's stream). */
@Component
class PluginEventLog(private val events: ObjectProvider<EventService>) {
    fun record(type: String, data: Map<String, Any?>) {
        events.getObject().ingest(listOf(EngineEvent(type = type, threadId = THREAD, step = "plugins", data = data.filterValues { it != null })))
    }

    companion object {
        /** The engine's marketplace events use this thread id too. */
        const val THREAD = "marketplace"
    }
}

/** The engine's plugin.install.done (an install from elsewhere, keel2 plugins install) becomes the notification. */
@Component
class PluginInstallEvents(private val changes: ObjectProvider<PluginChanges>) : EngineEventHandler {
    override val prefix = "plugin.install.done"

    override fun handle(event: EngineEvent) = changes.getObject().engineDone(event.data)
}
