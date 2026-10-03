package keel.api.notifications

import keel.api.common.NotFound
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api")
class NotificationController(private val notifications: NotificationService) {

    @GetMapping("/notifications")
    fun list(@RequestParam(defaultValue = "50") limit: Int): List<Notification> = notifications.list(limit)

    @PostMapping("/notifications/read-all")
    fun readAll(): Map<String, Boolean> {
        notifications.readAll()
        return mapOf("ok" to true)
    }

    @PostMapping("/notifications/{id}/read")
    fun read(@PathVariable id: Long): Map<String, Boolean> {
        if (!notifications.read(id)) throw NotFound("No notification $id")
        return mapOf("ok" to true)
    }

    @GetMapping("/notification-settings")
    fun settings(): NotificationSettings = notifications.settings()

    @PutMapping("/notification-settings")
    fun save(@RequestBody body: NotificationSettings): NotificationSettings = notifications.saveSettings(body)
}
