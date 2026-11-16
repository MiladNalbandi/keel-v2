package keel.api.marketplace

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.node.ObjectNode
import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.events.EventService
import keel.api.notifications.NotificationService
import keel.api.pluginhost.PluginHost
import keel.api.pluginhost.PluginsReloaded
import org.springframework.beans.factory.ObjectProvider
import org.springframework.context.ApplicationEventPublisher
import org.springframework.stereotype.Component
import org.springframework.stereotype.Service
import java.util.concurrent.ConcurrentHashMap

/**
 * What a person (or an approved request) changes: install, update, roll back, remove, on or off. Each goes to the
 * engine (with who, for its own log); then it is an event in keel's event log (who and when: plugin.installed,
 * plugin.updated, …), the restart banner shows, and an install says so in a notification: "<Title> is installed:
 * restart keel to use it". The engine answers an install or update when it is done.
 *
 * v0.17.1 after each change the engine applies what needs no restart (POST /marketplace/apply-live: a plugin whose parts
 * are only web and/or content), the plugin host reads run/resolved.json again, and the answer carries `live` {applied,
 * names: {name: applied | restart | same}, pending_restart}. Only a change that waits marks the restart as due.
 */
@Service
class PluginChanges(
    private val marketplace: Marketplace,
    private val restarts: RestartPlan,
    private val notifications: NotificationService,
    private val log: PluginEventLog,
    private val host: PluginHost,
    private val publisher: ApplicationEventPublisher,
) {
    /** Installs and updates the api runs now: the engine's plugin.install.done for them is told by the api itself. */
    private val running = ConcurrentHashMap.newKeySet<String>()

    /** When the api told about an install lately: the engine's event that comes after the answer is an echo. */
    private val told = ConcurrentHashMap<String, Long>()

    fun install(name: String, version: String?, by: String = PERSON, request: String? = null): JsonNode {
        val res = waiting(name) { marketplace.install(name, version, by) }
        val now = live(res, listOf(name) + names(res.path("installed")) + names(res.path("turned_on")))
        done("plugin.installed", name, res, version, by, mapOf("request" to request, "installed" to res.path("installed").takeIf { it.isArray }), now)
        return res
    }

    /** [allowMore]: a person approved the permissions the new version adds. */
    fun update(name: String, version: String?, by: String = PERSON, allowMore: Boolean = false, request: String? = null): JsonNode {
        val res = waiting(name) { marketplace.update(name, version, by, allowMore) }
        // the version before: its entry in `installed` says it (from)
        val from = res.path("from").asText("").ifBlank { null }
            ?: res.path("installed").firstOrNull { it.path("name").asText() == name }?.path("from")?.asText("")?.ifBlank { null }
        val now = live(res, listOf(name) + names(res.path("installed")) + names(res.path("turned_on")))
        done("plugin.updated", name, res, version, by, mapOf("from" to from, "request" to request), now)
        return res
    }

    fun rollback(name: String, by: String = PERSON): JsonNode {
        val res = marketplace.rollback(name, by)
        val now = live(res, listOf(name))
        done("plugin.rolled_back", name, res, null, by, mapOf("from" to res.path("from").asText("").ifBlank { null }), now)
        return res
    }

    fun switch(name: String, on: Boolean, by: String = PERSON): JsonNode {
        val res = marketplace.switch(name, on, by)
        live(res, listOf(name) + names(res.path("also")))
        log.record(if (on) "plugin.turned_on" else "plugin.turned_off",
            mapOf("name" to name, "by" to by, "also" to res.path("also").takeIf { it.isArray && it.size() > 0 }))
        return res
    }

    fun remove(name: String, data: String, by: String = PERSON): JsonNode {
        val res = marketplace.remove(name, data, by)
        live(res, listOf(name))
        log.record("plugin.removed", mapOf("name" to name, "version" to res.path("removed").asText("").ifBlank { null }, "data" to data,
            "by" to by, "back_to_image" to res.path("back_to_image").asText("").ifBlank { null }))
        return res
    }

    fun installFile(path: String, by: String = PERSON, force: Boolean = false): JsonNode {
        val file = path.trim().substringAfterLast('/')
        val guess = FILE_NAME.find(file)?.groupValues?.get(1) ?: file
        val res = waiting(guess) { marketplace.installFile(path, by, force) }
        val name = res.path("name").asText("").ifBlank { guess }
        // the same version again (force) has changed files: a plugin with code needs the restart even then
        val now = live(res, listOf(name), sameWaits = true)
        done("plugin.installed", name, res, null, by, mapOf("source" to "file", "path" to path), now)
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

    /** The plugin names in an answer's list: ["a", …] or [{name: "a"}, …]. */
    private fun names(list: JsonNode): List<String> =
        list.takeIf { it.isArray }?.mapNotNull { (if (it.isTextual) it.asText() else it.path("name").asText("")).ifBlank { null } }.orEmpty()

    /**
     * Apply what needs no restart now, and add `live` to the engine's answer. True when [main] was applied now. A
     * change that waits (or an engine that does not say) marks the restart as due; with [sameWaits] a plugin with
     * code whose version did not change waits too (its files did).
     */
    private fun live(res: JsonNode, touched: List<String>, sameWaits: Boolean = false): Boolean {
        val names = touched.distinct()
        val live = marketplace.applyLive(names)
        if (live == null) {
            restarts.changed()
            return false
        }
        if (live.path("applied").size() > 0) {
            host.reload()
            publisher.publishEvent(PluginsReloaded(host.plugins.map { it.name }))
        }
        val verdict = live.path("names")
        val waits = names.any { verdict.path(it).asText(RESTART).let { v -> v == RESTART || (sameWaits && v == SAME) } }
        if (waits) restarts.changed()
        (res as? ObjectNode)?.let {
            it.set<JsonNode>("live", live)
            if (live.has("pending_restart")) it.set<JsonNode>("pending_restart", live.path("pending_restart"))
        }
        return names.firstOrNull()?.let { verdict.path(it).asText("") == APPLIED } ?: false
    }

    private fun done(type: String, name: String, res: JsonNode, asked: String?, by: String, more: Map<String, Any?> = emptyMap(),
                     appliedNow: Boolean = false) {
        val version = res.path("version").asText("").ifBlank { asked }
        log.record(type, mapOf("name" to name, "version" to version, "by" to by) + more.filterValues { it != null })
        tell(type, name, res.path("title").asText("").ifBlank { null } ?: titleOf(name), version, appliedNow)
        val now = System.currentTimeMillis()
        told.values.removeIf { now - it > TOLD_MS }
        if (type != "plugin.rolled_back") told[name] = now
    }

    /** An installed plugin's title (a rollback's answer has none); null when the engine does not say. */
    private fun titleOf(name: String): String? = runCatching {
        marketplace.installed().path("plugins").firstOrNull { it.path("name").asText() == name }?.path("title")?.asText("")?.ifBlank { null }
    }.getOrNull()

    private fun tell(type: String, name: String, title: String?, version: String?, appliedNow: Boolean = false) {
        val shown = title ?: name
        val head = when (type) {
            "plugin.rolled_back" -> "$shown is back on ${version ?: "its kept version"}"
            "plugin.updated" -> "$shown is updated"
            else -> "$shown is installed"
        }
        if (appliedNow) {
            notifications.create("finished", null, "$head: it works now",
                listOfNotNull(version?.let { "Version $it." }, "It adds only pages or content, so keel did not need a restart.").joinToString(" "), "/plugins")
            return
        }
        notifications.create("finished", null, "$head: restart keel to use it",
            listOfNotNull(version?.let { "Version $it." }, "Settings › Plugins has the Restart keel button.").joinToString(" "), "/plugins")
    }

    companion object {
        const val PERSON = "person"
        /** What the engine's apply-live says for one plugin. */
        const val APPLIED = "applied"
        const val RESTART = "restart"
        const val SAME = "same"
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
