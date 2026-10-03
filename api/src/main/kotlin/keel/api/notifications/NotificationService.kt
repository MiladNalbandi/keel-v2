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
    fun create(type: String, projectId: String?, title: String, body: String, link: String?): Notification? {
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
            "INSERT INTO notifications(type, project_id, title, body, link, at, read) VALUES (?, ?, ?, ?, ?, ?, 0)",
            type, projectId, title, body, link, at,
        )
        val n = Notification(id, type, projectId, title, body, link, at, false)
        hub.publish(projectId, "notification", n)
        return n
    }

    fun list(limit: Int): List<Notification> =
        jdbc.query(
            "SELECT id, type, project_id, title, body, link, at, read FROM notifications ORDER BY id DESC LIMIT ?",
            { rs, _ ->
                Notification(
                    rs.getLong(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5),
                    rs.getString(6), rs.getString(7), rs.getInt(8) == 1,
                )
            },
            limit.coerceIn(1, 500),
        )

    fun readAll() {
        jdbc.update("UPDATE notifications SET read = 1 WHERE read = 0")
    }

    fun read(id: Long): Boolean = jdbc.update("UPDATE notifications SET read = 1 WHERE id = ?", id) > 0

    companion object {
        const val KEY = "notification-settings"
    }
}
