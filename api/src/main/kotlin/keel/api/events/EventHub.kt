package keel.api.events

import com.fasterxml.jackson.databind.ObjectMapper
import org.slf4j.LoggerFactory
import org.springframework.http.MediaType
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter
import java.util.concurrent.CopyOnWriteArrayList

/**
 * SSE registry. A client subscribes to one project (or to all with no project) and gets
 * `event: <type>` / `data: <json>` frames. A comment heartbeat goes out every 20 seconds.
 */
@Component
class EventHub(private val mapper: ObjectMapper) {
    private val log = LoggerFactory.getLogger(javaClass)

    /** [light]: `project=*` — every project, but only the events the bell needs. */
    private data class Sub(val project: String?, val emitter: SseEmitter, val light: Boolean = false)

    private val subs = CopyOnWriteArrayList<Sub>()

    fun subscribe(project: String?): SseEmitter {
        val emitter = SseEmitter(0L) // no timeout; the heartbeat finds dead clients
        val all = project?.trim() == "*"
        val sub = Sub(project?.takeIf { it.isNotBlank() && !all }, emitter, light = all)
        subs += sub
        emitter.onCompletion { subs.remove(sub) }
        emitter.onTimeout { subs.remove(sub) }
        emitter.onError { subs.remove(sub) }
        try {
            emitter.send(SseEmitter.event().comment("connected"))
        } catch (e: Exception) {
            subs.remove(sub)
        }
        return emitter
    }

    /** Sends to subscribers of [project] and to subscribers of every project. */
    fun publish(project: String?, type: String, data: Any?) {
        val json = mapper.writeValueAsString(data)
        for (sub in subs) {
            if (sub.project != null && project != null && sub.project != project) continue
            if (sub.light && type !in LIGHT) continue
            try {
                sub.emitter.send(SseEmitter.event().name(type).data(json, MediaType.APPLICATION_JSON))
            } catch (e: Exception) {
                subs.remove(sub)
                runCatching { sub.emitter.completeWithError(e) }
            }
        }
    }

    fun count(): Int = subs.size

    companion object {
        val LIGHT = setOf("notification", "project.changed")
    }

    @Scheduled(fixedRate = 20_000, initialDelay = 20_000)
    fun heartbeat() {
        for (sub in subs) {
            try {
                sub.emitter.send(SseEmitter.event().comment("ping"))
            } catch (e: Exception) {
                // The connection is already gone; completing it would only start an error dispatch.
                log.debug("dropping SSE client: {}", e.message)
                subs.remove(sub)
            }
        }
    }
}
