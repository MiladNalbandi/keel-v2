package keel.api.ui

import keel.api.common.BadRequest
import keel.api.common.Db
import keel.api.common.Json
import keel.api.events.EventHub
import keel.api.projects.ProjectService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.time.Instant

/**
 * keel 0.18.6: what the person is looking at — the project, and per project the KeelBot chat — shared by every keel
 * front end (the dashboard's tabs, keel TUI), so they stay on the same project and chat. Kept in the settings table
 * (scope `ui.focus`); each change goes out as the live event `ui.focus` with the client that made it, so that one does
 * not follow its own change.
 * keel 0.18.63: a window can keep its own project and chat ("Follow other keel windows" off: the web's Settings › This
 * browser, keel TUI's /follow and /settings). It then neither reads nor changes this focus, so the focus is what the
 * windows that follow share; nothing here changes for it.
 */
data class UiFocus(
    val project: String? = null,
    /** project id → the KeelBot chat (helper session id) open on it */
    val chats: Map<String, String> = emptyMap(),
    /** who changed it last: "web", "tui", … and that client's own random id */
    val by: String? = null,
    val client: String? = null,
    val at: String? = null,
)

/** A change: the project, and/or the chat open on [chatProject] (default: [project]); [close] closes that chat. */
data class UiFocusPatch(
    val project: String? = null,
    val chat: String? = null,
    val close: Boolean = false,
    val chatProject: String? = null,
    val by: String? = null,
    val client: String? = null,
)

@Service
class UiFocusService(private val jdbc: JdbcTemplate, private val projects: ProjectService, private val hub: EventHub) {

    fun get(): UiFocus {
        val raw = Db.settingsJson(jdbc, SCOPE) ?: return UiFocus()
        val m = Json.readMap(raw)
        @Suppress("UNCHECKED_CAST")
        val chats = (m["chats"] as? Map<String, Any?>)?.mapNotNull { (k, v) -> (v as? String)?.let { k to it } }?.toMap() ?: emptyMap()
        // a project that is gone (removed) is not where anyone looks any more
        val project = (m["project"] as? String)?.takeIf { projects.find(it) != null }
        return UiFocus(project, chats.filterKeys { projects.find(it) != null }, m["by"] as? String, m["client"] as? String, m["at"] as? String)
    }

    @Synchronized
    fun set(p: UiFocusPatch): UiFocus {
        p.project?.let { projects.require(it) }
        val now = get()
        val chatPid = p.chatProject ?: p.project ?: now.project
        val chats = if (p.chat.isNullOrBlank() && !p.close) now.chats else {
            if (chatPid == null) throw BadRequest("Which project is the chat on?", "Send chat_project (or project) with chat.")
            projects.require(chatPid)
            if (p.close) now.chats - chatPid else now.chats + (chatPid to p.chat!!.trim())
        }
        val next = UiFocus(p.project ?: now.project, chats, p.by?.take(20), p.client?.take(64), Instant.now().toString())
        if (next.project == now.project && next.chats == now.chats) return now
        Db.saveSettings(jdbc, SCOPE, next)
        hub.publish(null, EVENT, next)
        return next
    }

    companion object {
        const val SCOPE = "ui.focus"
        const val EVENT = "ui.focus"
    }
}
