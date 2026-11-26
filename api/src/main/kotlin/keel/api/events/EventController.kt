package keel.api.events

import keel.api.common.Forbidden
import keel.api.common.Hash
import keel.api.common.KeelProperties
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter

@RestController
class EventController(
    private val events: EventService,
    private val hub: EventHub,
    private val props: KeelProperties,
) {
    @GetMapping("/api/events", produces = ["text/event-stream"])
    fun stream(@RequestParam(required = false) project: String?, @RequestParam(required = false) notify: String?): SseEmitter =
        hub.subscribe(project, notify)

    /** Engine → api, with X-Keel-Token. v0.18.1: without KEEL_INTERNAL_TOKEN nobody may send events (it was "accept any"). */
    @PostMapping("/internal/events")
    fun ingest(
        @RequestHeader("X-Keel-Token", required = false) token: String?,
        @RequestBody body: List<EngineEvent>,
    ): Map<String, Any> {
        val expected = props.token
        val ok = expected.isNotEmpty() && token != null && Hash.constantTimeEquals(token, expected)
        if (!ok) throw Forbidden("Wrong or missing X-Keel-Token", "Set KEEL_INTERNAL_TOKEN for the api and the engine (keel2 does).")
        val stored = events.ingest(body)
        return mapOf("ok" to true, "stored" to stored)
    }
}
