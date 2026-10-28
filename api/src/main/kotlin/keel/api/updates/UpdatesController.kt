package keel.api.updates

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController

/**
 * keel 0.18.66 Settings › Access › Updates and the update pop-up. GET answers what is kept (never the network); Check now
 * asks GitHub at once (a person's button, so it may wait up to 20 s). Both changes need the MCP admin level.
 */
@RestController
class UpdatesController(private val check: ReleaseCheck) {

    @GetMapping("/api/updates")
    fun view(): UpdatesView = check.view()

    @PutMapping("/api/updates/settings")
    fun save(@RequestBody body: UpdateSettings): UpdatesView {
        check.saveSettings(body)
        return check.view()
    }

    @PostMapping("/api/updates/check")
    fun checkNow(): UpdatesView {
        check.checkNow()
        return check.view()
    }
}
