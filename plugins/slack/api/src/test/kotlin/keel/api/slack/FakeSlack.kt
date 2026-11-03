package keel.api.slack

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.sun.net.httpserver.HttpServer
import java.io.DataInputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.MessageDigest
import java.util.Base64
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.concurrent.thread

/**
 * A stand-in for Slack on this computer: its Web API (the methods keel calls) and a Socket Mode WebSocket (RFC 6455, text frames only). No real Slack was
 * ever reached; this holds what Slack's docs say, as far as keel uses it.
 */
class FakeSlack {
    data class Call(val method: String, val token: String, val body: JsonNode)

    private val mapper = jacksonObjectMapper()
    private val http = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
    private val ws = ServerSocket(0)
    val calls = CopyOnWriteArrayList<Call>()
    val acks = CopyOnWriteArrayList<String>()
    @Volatile private var client: Socket? = null
    @Volatile var refuse: String? = null

    val url get() = "http://127.0.0.1:${http.address.port}/api"

    fun start(): FakeSlack {
        http.createContext("/api") { x ->
            val method = x.requestURI.path.substringAfterLast('/')
            val token = x.requestHeaders.getFirst("Authorization").orEmpty().removePrefix("Bearer ")
            val raw = x.requestBody.readAllBytes().toString(Charsets.UTF_8)
            val body: JsonNode = mapper.readTree(raw.ifEmpty { "{}" })
            calls += Call(method, token, body)
            val answer: Map<String, Any?> = when {
                refuse != null -> mapOf("ok" to false, "error" to refuse)
                method == "auth.test" -> mapOf("ok" to true, "team_id" to "T1", "team" to "Northwind", "user_id" to "UBOT")
                method == "apps.connections.open" -> mapOf("ok" to true, "url" to "ws://127.0.0.1:${ws.localPort}/socket")
                else -> mapOf("ok" to true, "channel" to body.path("channel").asText("C1"), "ts" to "${System.nanoTime()}.1")
            }
            val out = mapper.writeValueAsBytes(answer)
            x.sendResponseHeaders(200, out.size.toLong())
            x.responseBody.use { it.write(out) }
        }
        http.start()
        thread(isDaemon = true) {
            while (!ws.isClosed) {
                val s = runCatching { ws.accept() }.getOrNull() ?: break
                thread(isDaemon = true) { talk(s) }
            }
        }
        return this
    }

    fun stop() {
        runCatching { client?.close() }
        ws.close()
        http.stop(0)
    }

    val connected get() = client?.isClosed == false

    private fun talk(s: Socket) {
        val input = s.getInputStream()
        val head = StringBuilder()
        while (!head.endsWith("\r\n\r\n")) head.append(input.read().toChar())
        val key = Regex("(?i)sec-websocket-key: *(.+)\r").find(head)!!.groupValues[1].trim()
        val accept = Base64.getEncoder().encodeToString(MessageDigest.getInstance("SHA-1").digest((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").toByteArray()))
        s.getOutputStream().write("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: $accept\r\n\r\n".toByteArray())
        client = s
        send("""{"type":"hello"}""")
        val data = DataInputStream(input)
        runCatching {
            while (true) {
                val first = data.readUnsignedByte()
                val second = data.readUnsignedByte()
                var len = (second and 0x7f).toLong()
                if (len == 126L) len = data.readUnsignedShort().toLong() else if (len == 127L) len = data.readLong()
                val mask = ByteArray(4).also { data.readFully(it) }
                val bytes = ByteArray(len.toInt()).also { data.readFully(it) }
                for (i in bytes.indices) bytes[i] = (bytes[i].toInt() xor mask[i % 4].toInt()).toByte()
                if (first and 0x0f == 8) break
                if (first and 0x0f == 1) mapper.readTree(bytes).path("envelope_id").asText(null)?.let { acks += it }
            }
        }
        s.close()
    }

    @Synchronized
    fun send(text: String) {
        val out = client!!.getOutputStream()
        val bytes = text.toByteArray()
        out.write(0x81)
        when {
            bytes.size < 126 -> out.write(bytes.size)
            else -> { out.write(126); out.write(bytes.size shr 8); out.write(bytes.size and 0xff) }
        }
        out.write(bytes)
        out.flush()
    }

    private var n = 0

    /** Sends one envelope as Socket Mode does; returns its id. */
    fun envelope(type: String, payload: Map<String, Any?>, id: String = "env${++n}-${System.nanoTime()}"): String {
        send(mapper.writeValueAsString(mapOf("envelope_id" to id, "type" to type, "payload" to payload)))
        return id
    }

    fun dm(user: String, text: String, more: Map<String, Any?> = emptyMap(), team: String = "T1", eventId: String = "ev${++n}-${System.nanoTime()}") =
        envelope("events_api", mapOf("team_id" to team, "event_id" to eventId, "event" to mapOf("type" to "message", "channel_type" to "im", "channel" to "D1", "user" to user, "text" to text, "ts" to "1.1") + more))

    fun press(user: String, value: String, team: String = "T1", action: String = SlackService.ACTION, trigger: String = "trigger-1") = envelope("interactive", mapOf("type" to "block_actions",
        "user" to mapOf("id" to user), "team" to mapOf("id" to team), "trigger_id" to trigger, "channel" to mapOf("id" to "C1"), "message" to mapOf("ts" to "5.5"),
        "actions" to listOf(mapOf("action_id" to action, "value" to value))))

    /** A mention in a channel. */
    fun channel(user: String, text: String, team: String = "T1") = envelope("events_api", mapOf("team_id" to team, "event_id" to "ev${++n}-${System.nanoTime()}", "event" to mapOf("type" to "app_mention",
        "channel" to "C1", "user" to user, "text" to text, "ts" to "${System.nanoTime()}.2")))

    /** A plain reply in a channel's thread: Slack sends these once the app has message.channels. */
    fun channelReply(user: String, channel: String, thread: String, text: String, team: String = "T1") = envelope("events_api", mapOf("team_id" to team, "event_id" to "ev${++n}-${System.nanoTime()}",
        "event" to mapOf("type" to "message", "channel_type" to "channel", "channel" to channel, "user" to user, "text" to text, "ts" to "${System.nanoTime()}.3", "thread_ts" to thread)))

    /** A modal sent back: the view as keel opened it, with the person's values. */
    fun submit(user: String, view: Any?, values: Map<String, Any?>, team: String = "T1"): String {
        val tree = mapper.valueToTree<JsonNode>(view)
        return envelope("interactive", mapOf("type" to "view_submission", "user" to mapOf("id" to user), "team" to mapOf("id" to team),
            "view" to mapOf("callback_id" to tree.path("callback_id").asText(), "private_metadata" to tree.path("private_metadata").asText(), "state" to mapOf("values" to values))))
    }

    fun sent(method: String) = calls.filter { it.method == method }
}
