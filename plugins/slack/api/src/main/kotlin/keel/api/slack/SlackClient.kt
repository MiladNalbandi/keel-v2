package keel.api.slack

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import org.slf4j.LoggerFactory
import java.net.InetAddress
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.net.http.WebSocket
import java.time.Duration
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** What Slack refused, in words without a token. */
class SlackError(message: String) : RuntimeException(message)

const val SLACK_API = "https://slack.com/api"

/** The tokens go only over https, or to a server on this computer (a stand-in, in tests). */
fun checkedAddress(url: String, secure: String, plain: String): URI {
    val uri = runCatching { URI(url.trim()) }.getOrNull()
    val host = uri?.host ?: throw BadRequest("That is not a web address")
    val local = runCatching { InetAddress.getAllByName(host).all { it.isLoopbackAddress } }.getOrDefault(false)
    if (uri.scheme != secure && !(uri.scheme == plain && local)) throw BadRequest("The address must start with $secure://", "keel never sends a token unencrypted.")
    return uri
}

/** Slack's Web API, the few methods keel uses. */
class SlackApi(private val base: String, private val mapper: ObjectMapper) {
    private val http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(8)).followRedirects(HttpClient.Redirect.NEVER).build()

    fun call(method: String, token: String, body: Map<String, Any?> = emptyMap()): JsonNode {
        val request = HttpRequest.newBuilder(checkedAddress("${base.trimEnd('/')}/$method", "https", "http")).timeout(Duration.ofSeconds(15))
            .header("Content-Type", "application/json; charset=utf-8").header("Authorization", "Bearer $token")
            .POST(HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(body))).build()
        val res = try {
            http.send(request, HttpResponse.BodyHandlers.ofString())
        } catch (e: java.io.IOException) {
            throw SlackError("Slack cannot be reached (${e.javaClass.simpleName}).")
        }
        val json = runCatching { mapper.readTree(res.body()) }.getOrNull()
        if (res.statusCode() != 200 || json == null) throw SlackError("Slack answered ${res.statusCode()}.")
        if (!json.path("ok").asBoolean(false)) throw SlackError("Slack said: ${json.path("error").asText("no reason").take(80)}.")
        return json
    }
}

/**
 * Socket Mode: keel connects out to Slack over a WebSocket, so it needs no public address. Slack sends each event as an envelope; keel
 * acknowledges it at once (Slack sends it again otherwise) and hands it on. A lost connection is opened again, a little later each time.
 * The JDK's own client: a plugin's jar carries no library.
 */
class SlackSocket(
    private val open: () -> String,
    private val mapper: ObjectMapper,
    private val onEnvelope: (JsonNode) -> Unit,
    private val onState: (String, String?) -> Unit,
) {
    private val log = LoggerFactory.getLogger(SlackSocket::class.java)
    @Volatile private var stopped = false
    @Volatile private var socket: WebSocket? = null
    private var thread: Thread? = null
    private val http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build()

    fun start() {
        thread = Thread({ loop() }, "keel-slack-socket").apply { isDaemon = true; start() }
    }

    fun stop() {
        stopped = true
        runCatching { socket?.abort() }
        thread?.interrupt()
    }

    private fun loop() {
        var wait = 1L
        while (!stopped) {
            val closed = CountDownLatch(1)
            try {
                val uri = checkedAddress(open(), "wss", "ws")
                socket = http.newWebSocketBuilder().connectTimeout(Duration.ofSeconds(10)).buildAsync(uri, Listener(closed)).get(15, TimeUnit.SECONDS)
                onState("connected", null)
                wait = 1
                closed.await()
                if (!stopped) onState("connecting", "The connection closed. keel connects again.")
            } catch (e: InterruptedException) {
                return
            } catch (e: SlackError) {
                onState("failed", e.message)
            } catch (e: BadRequest) {
                onState("failed", e.message)
            } catch (e: Exception) {
                log.warn("slack: the connection failed: {}", e.javaClass.simpleName)
                onState("failed", "The connection failed (${e.javaClass.simpleName}).")
            }
            if (stopped) return
            try {
                Thread.sleep(wait * 1000)
            } catch (e: InterruptedException) {
                return
            }
            wait = (wait * 2).coerceAtMost(MAX_WAIT)
        }
    }

    private inner class Listener(private val closed: CountDownLatch) : WebSocket.Listener {
        private val text = StringBuilder()

        override fun onOpen(webSocket: WebSocket) {
            webSocket.request(1)
        }

        override fun onText(webSocket: WebSocket, data: CharSequence, last: Boolean): CompletionStage<*> {
            text.append(data)
            if (last) {
                val whole = text.toString()
                text.clear()
                runCatching { handle(webSocket, whole) }.onFailure { log.warn("slack: an event could not be read: {}", it.javaClass.simpleName) }
            }
            webSocket.request(1)
            return CompletableFuture.completedFuture(null)
        }

        private fun handle(webSocket: WebSocket, raw: String) {
            val msg = mapper.readTree(raw)
            if (msg.path("type").asText() == "disconnect") {
                webSocket.sendClose(WebSocket.NORMAL_CLOSURE, "")
                closed.countDown()
                return
            }
            val id = msg.path("envelope_id").asText("")
            if (id.isEmpty()) return            // hello
            webSocket.sendText(mapper.writeValueAsString(mapOf("envelope_id" to id)), true)
            onEnvelope(msg)
        }

        override fun onClose(webSocket: WebSocket, statusCode: Int, reason: String): CompletionStage<*> {
            closed.countDown()
            return CompletableFuture.completedFuture(null)
        }

        override fun onError(webSocket: WebSocket, error: Throwable) {
            closed.countDown()
        }
    }

    companion object {
        const val MAX_WAIT = 60L
    }
}
