package keel.api.events

import keel.api.common.Forbidden
import keel.api.common.KeelProperties
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter
import java.security.MessageDigest

@RestController
class EventController(
    private val events: EventService,
    private val hub: EventHub,
    private val props: KeelProperties,
) {
    @GetMapping("/api/events", produces = ["text/event-stream"])
    fun stream(@RequestParam(required = false) project: String?, @RequestParam(required = false) notify: String?): SseEmitter =
        hub.subscribe(project, notify)

    /** Engine → api. Checks X-Keel-Token when KEEL_INTERNAL_TOKEN is set (dev without it accepts any). */
    @PostMapping("/internal/events")
    fun ingest(
        @RequestHeader("X-Keel-Token", required = false) token: String?,
        @RequestBody body: List<EngineEvent>,
    ): Map<String, Any> {
        val expected = props.internalToken
        if (expected.isNotEmpty()) {
            val ok = token != null && MessageDigest.isEqual(token.toByteArray(), expected.toByteArray())
            if (!ok) throw Forbidden("Wrong or missing X-Keel-Token")
        }
        val stored = events.ingest(body)
        return mapOf("ok" to true, "stored" to stored)
    }
}
