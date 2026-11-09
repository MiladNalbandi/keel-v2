package keel.api

import com.fasterxml.jackson.databind.ObjectMapper
import com.sun.net.httpserver.HttpServer
import keel.api.common.KeelProperties
import keel.api.events.EventHub
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.net.InetSocketAddress
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit

/** 0.24.0 behind the Python api, every event goes to its hub (POST /internal/hub), in order, with the internal token. */
class EventHubForwardTest {
    private val mapper = ObjectMapper()

    @Test
    fun `behind the Python api every event goes to its hub in order`() {
        val got = CopyOnWriteArrayList<Map<*, *>>()
        val tokens = CopyOnWriteArrayList<String?>()
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/internal/hub") { ex ->
            tokens += ex.requestHeaders.getFirst("X-Keel-Token")
            mapper.readValue(ex.requestBody, List::class.java).forEach { got += it as Map<*, *> }
            val body = """{"ok":true,"subscribers":1}""".toByteArray()
            ex.sendResponseHeaders(200, body.size.toLong())
            ex.responseBody.use { it.write(body) }
        }
        server.start()
        try {
            val hub = EventHub(mapper, KeelProperties(internalToken = "tok", hubUrl = "http://127.0.0.1:${server.address.port}/internal/hub"))
            for (i in 1..50) hub.publish("p1", "agent.delta", mapOf("i" to i))
            hub.publish(null, "notification", mapOf("title" to "é", "x" to null))
            val end = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
            while (got.size < 51 && System.nanoTime() < end) Thread.sleep(10)
            assertThat(got).hasSize(51)
            assertThat(got.take(50).map { it["data"] }).isEqualTo((1..50).map { """{"i":$it}""" })
            assertThat(got.last()).isEqualTo(mapOf("project" to null, "type" to "notification", "data" to """{"title":"é","x":null}"""))
            assertThat(tokens).containsOnly("tok")
            assertThat(hub.count()).isZero()
        } finally {
            server.stop(0)
        }
    }

    @Test
    fun `with the Python api down publishing never fails or waits`() {
        val hub = EventHub(mapper, KeelProperties(internalToken = "tok", hubUrl = "http://127.0.0.1:1/internal/hub"))
        val start = System.nanoTime()
        repeat(20_000) { hub.publish("p1", "agent.delta", mapOf("i" to it)) }
        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(5_000)
    }
}
