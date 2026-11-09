package keel.api.events

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.KeelProperties
import org.slf4j.LoggerFactory
import org.springframework.http.MediaType
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.LinkedBlockingQueue

/**
 * SSE registry. A client subscribes to one project (or to all with no project) and gets
 * `event: <type>` / `data: <json>` frames. A comment heartbeat goes out every 20 seconds.
 *
 * 0.24.0 behind the Python api ([KeelProperties.hubUrl] set), the Python api answers /api/events: every event goes to its
 * hub instead ([HubForward]), and this hub has no subscribers.
 */
@Component
class EventHub(private val mapper: ObjectMapper, props: KeelProperties) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val forward = props.hubUrl.takeIf { it.isNotBlank() }?.let { HubForward(it, props.internalToken, mapper) }

    /**
     * [light]: `project=*` — every project, but only the events the bell needs.
     * [notifyAll]: `project=X&notify=all` — everything for X plus the bell's events from every project, so a
     * browser tab needs one stream (browsers allow only 6 connections per server; each open tab used two).
     */
    private data class Sub(val project: String?, val emitter: SseEmitter, val light: Boolean = false, val notifyAll: Boolean = false)

    private val subs = CopyOnWriteArrayList<Sub>()

    fun subscribe(project: String?, notify: String? = null): SseEmitter {
        val emitter = SseEmitter(0L) // no timeout; the heartbeat finds dead clients
        val all = project?.trim() == "*"
        val sub = Sub(project?.takeIf { it.isNotBlank() && !all }, emitter, light = all, notifyAll = notify == "all" && !all)
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
        if (forward != null) {
            forward.send(project, type, mapper.writeValueAsString(data))
            return
        }
        // no open tab or terminal: nothing to write (live text arrives every 80 ms per agent, up to 20k characters each)
        if (subs.isEmpty()) return
        val json = mapper.writeValueAsString(data)
        for (sub in subs) {
            val otherProject = sub.project != null && project != null && sub.project != project
            if (otherProject && !(sub.notifyAll && type in LIGHT)) continue
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
        val LIGHT = setOf("notification", "notification.done", "project.changed")

        /** The one generic channel: an engine event the web does not listen to by name goes out as `event: engine`, its
         *  type inside (approval.*, a plugin's own events), so a part's web code can follow it. */
        const val ENGINE = "engine"

        /** Engine events the web listens to by their own name (web api.ts ENGINE_EVENT_TYPES). */
        val NAMED = setOf(
            "thread.started", "step.started", "step.finished", "agent.started", "agent.step", "agent.finished", "code.started", "code.step", "code.finished",
            "gate.waiting", "gate.decided", "budget.warn", "budget.stop", "guard.refused", "thread.done", "thread.failed",
            "helper.started", "helper.step", "helper.finished", "helper.permission", "helper.permission.answered", "helper.commit",
            // keel 0.18.2 live text: the answer as it is written (never stored, EventService)
            "helper.delta", "agent.delta",
            // keel TUI's tool lines: KeelBot's tool calls, one record each (never stored; the web does not listen to it)
            "helper.tool",
        )

        /** The SSE event name an engine event goes out under. */
        fun channelOf(type: String): String = if (type in NAMED) type else ENGINE
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

/**
 * Sends this api's events to the Python api's hub (POST [url], the internal token), in the order they were published.
 * One thread posts them, as many as are waiting in one batch, so [send] never waits on the network. While the Python api
 * is down or restarting its streams are gone too, so a batch that cannot be sent is dropped, not retried.
 */
internal class HubForward(url: String, private val token: String, private val mapper: ObjectMapper) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val uri = URI.create(url)
    private val client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build()
    private val queue = LinkedBlockingQueue<Map<String, String?>>(MAX_WAITING)

    @Volatile
    private var full = false

    init {
        Thread(::run, "keel-hub-forward").apply { isDaemon = true }.start()
    }

    /** [json]: the event's data, already written as JSON; the hub sends it on as it is. */
    fun send(project: String?, type: String, json: String) {
        if (!queue.offer(mapOf("project" to project, "type" to type, "data" to json)) && !full) {
            full = true
            log.warn("the Python api's event hub does not keep up: dropping events")
        }
    }

    private fun run() {
        val batch = ArrayList<Map<String, String?>>()
        while (true) {
            try {
                batch += queue.take()
                queue.drainTo(batch, MAX_BATCH - 1)
                post(batch)
            } catch (e: InterruptedException) {
                return
            } catch (e: Exception) {
                log.debug("event hub: {}", e.toString())
            } finally {
                batch.clear()
            }
        }
    }

    private fun post(batch: List<Map<String, String?>>) {
        val request = HttpRequest.newBuilder(uri)
            .timeout(Duration.ofSeconds(10))
            .header("Content-Type", "application/json")
            .header("X-Keel-Token", token)
            .POST(HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(batch)))
            .build()
        val status = client.send(request, HttpResponse.BodyHandlers.discarding()).statusCode()
        if (status == 200) full = false else log.debug("event hub answered {}", status)
    }

    companion object {
        const val MAX_WAITING = 10_000
        const val MAX_BATCH = 500
    }
}
