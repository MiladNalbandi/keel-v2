package keel.api.notifications

import keel.api.common.Db
import keel.api.common.KvStore
import keel.api.common.Time
import keel.api.events.EventHub
import keel.api.settings.SettingsService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service

data class Notification(
    val id: Long,
    val type: String,
    val projectId: String?,
    val title: String,
    val body: String,
    val link: String?,
    val at: String,
    val read: Boolean,
    /** v0.4.1: the thread and step of a gate's notification ("review"), and whether that gate was decided since. */
    val threadId: String? = null,
    val step: String? = null,
    val done: Boolean = false,
)

data class NotificationKinds(
    val review: Boolean = true,
    val failed: Boolean = true,
    val budget: Boolean = true,
    val finished: Boolean = true,
    val started: Boolean = false,
) {
    fun allows(type: String) = when (type) {
        "review" -> review
        "failed" -> failed
        "budget" -> budget
        "finished" -> finished
        "started" -> started
        else -> true
    }
}

data class NotificationSettings(
    val sound: Boolean = true,
    val volume: Double = 0.5,
    val tone: String = "chime",
    val popup: Boolean = true,
    val desktop: Boolean = false,
    val scope: String = "all",
    val kinds: NotificationKinds = NotificationKinds(),
    val quiet: Boolean = false,
)

@Service
class NotificationService(
    private val jdbc: JdbcTemplate,
    private val kv: KvStore,
    private val hub: EventHub,
    private val settings: SettingsService,
) {
    fun settings(): NotificationSettings = kv.get<NotificationSettings>(KEY) ?: NotificationSettings()

    fun saveSettings(s: NotificationSettings): NotificationSettings {
        kv.put(KEY, s)
        return s
    }

    /**
     * Creates a notification unless the user turned that kind off, or the project's
     * `notify` setting says "needs_you" (review only) or "none".
     */
    fun create(type: String, projectId: String?, title: String, body: String, link: String?,
               threadId: String? = null, step: String? = null): Notification? {
        if (!settings().kinds.allows(type)) return null
        if (projectId != null) {
            when (runCatching { settings.effective(projectId).notify }.getOrDefault("all")) {
                "none" -> return null
                "needs_you" -> if (type != "review") return null
            }
        }
        val at = Time.now()
        val id = Db.insertId(
            jdbc,
            "INSERT INTO notifications(type, project_id, title, body, link, at, read, thread_id, step, done) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 0)",
            type, projectId, title, body, link, at, threadId?.takeIf { it.isNotBlank() }, step,
        )
        val n = Notification(id, type, projectId, title, body, link, at, false, threadId?.takeIf { it.isNotBlank() }, step, false)
        hub.publish(projectId, "notification", n)
        return n
    }

    fun list(limit: Int): List<Notification> =
        jdbc.query(
            "SELECT id, type, project_id, title, body, link, at, read, thread_id, step, done FROM notifications ORDER BY id DESC LIMIT ?",
            { rs, _ ->
                Notification(
                    rs.getLong(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5),
                    rs.getString(6), rs.getString(7), rs.getInt(8) == 1, rs.getString(9), rs.getString(10), rs.getInt(11) == 1,
                )
            },
            limit.coerceIn(1, 500),
        )

    fun readAll(): Int = jdbc.update("UPDATE notifications SET read = 1 WHERE read = 0")

    fun read(id: Long): Boolean = jdbc.update("UPDATE notifications SET read = 1 WHERE id = ?", id) > 0

    /** Deletes one notification; false when there is none with that id. */
    fun delete(id: Long): Boolean = jdbc.update("DELETE FROM notifications WHERE id = ?", id) > 0

    /** Clears the whole list. Returns how many were removed. */
    fun clear(): Int = jdbc.update("DELETE FROM notifications")

    /** The newest gate notification of [threadId] that is not done yet (null: none). */
    fun openGateId(threadId: String): Long? =
        jdbc.queryForObject("SELECT MAX(id) FROM notifications WHERE thread_id = ? AND done = 0", Long::class.java, threadId)

    /**
     * The gate [threadId] waited at was decided (from the Flow page, the Inbox or MCP, or by the engine itself): its
     * notifications are done and read. [upTo] keeps a notification that arrived after the decision (the next gate) open.
     * [read] false (the flow ended without a decision here): done, but still unread. Tells every browser tab
     * (`notification.done`), so the bell's list follows.
     */
    fun markDone(threadId: String, upTo: Long? = null, read: Boolean = true): List<Long> {
        if (threadId.isBlank()) return emptyList()
        val ids = if (upTo != null) {
            jdbc.queryForList("SELECT id FROM notifications WHERE thread_id = ? AND done = 0 AND id <= ?", Long::class.java, threadId, upTo)
        } else {
            jdbc.queryForList("SELECT id FROM notifications WHERE thread_id = ? AND done = 0", Long::class.java, threadId)
        }
        if (ids.isEmpty()) return ids
        jdbc.update("UPDATE notifications SET done = 1${if (read) ", read = 1" else ""} WHERE id IN (${ids.joinToString(",")})")
        val pid = jdbc.query("SELECT project_id FROM notifications WHERE id = ?", { rs, _ -> rs.getString(1) }, ids.first()).firstOrNull()
        hub.publish(pid, "notification.done", mapOf("thread_id" to threadId, "ids" to ids, "read" to read))
        return ids
    }

    companion object {
        const val KEY = "notification-settings"
    }
}
