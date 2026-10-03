package keel.api

import com.sun.net.httpserver.HttpServer
import keel.api.dashboard.KeelDashboardController
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.http.MediaType
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.header
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.net.InetSocketAddress
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList

/** v0.2: GET /api/keel-dashboard and the /keel-v1/ reverse proxy. */
class V02DashboardApiTest : ApiTest() {

    @Test
    fun `starting the dashboard runs keel dashboard in a project with the port, and reports a failed start`() {
        val (pid, root) = newProject("v2-dash-start")
        V02RepoApiTest.fakeKeel(keelHome, """
            #!/bin/sh
            echo "${'$'}@ port=${'$'}KEEL_DASHBOARD_PORT" >> "${'$'}PWD/.keel-fake-args"
            echo "could not start: no node here" >&2
            exit 1
        """.trimIndent() + "\n")
        try {
            val r = get("/api/keel-dashboard?project=$pid").andExpect(status().isBadGateway).json()
            assertThat(r["error"].asText()).isEqualTo("keel dashboard did not start")
            assertThat(r["hint"].asText()).contains("no node here")
            assertThat(Files.readString(root.resolve(".keel-fake-args")).trim())
                .isEqualTo("dashboard --port $dashboardPort port=$dashboardPort")
        } finally {
            Files.deleteIfExists(keelHome.resolve("bin/keel"))
            Files.deleteIfExists(keelHome.resolve("bin"))
        }
        // without keel installed it says so
        assertThat(get("/api/keel-dashboard").andExpect(status().isServiceUnavailable).json()["hint"].asText()).contains("KEEL_HOME")
    }

    @Test
    fun `the proxy forwards to keel with keel's Host, rewrites absolute paths in the page and streams events`() {
        newProject("v2-dash-proxy")
        // Not running yet: the proxy says how to start it.
        assertThat(get("/keel-v1/").andExpect(status().isBadGateway).json()["hint"].asText()).contains("/api/keel-dashboard")

        val hosts = CopyOnWriteArrayList<String>()
        val origins = CopyOnWriteArrayList<String>()
        val bodies = CopyOnWriteArrayList<String>()
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", dashboardPort), 0)
        server.createContext("/") { ex ->
            hosts += ex.requestHeaders.getFirst("Host") ?: ""
            ex.requestHeaders.getFirst("Origin")?.let { origins += it }
            val text = ex.requestBody.readAllBytes().toString(Charsets.UTF_8)
            if (text.isNotEmpty()) bodies += text
            val path = ex.requestURI.path
            val query = ex.requestURI.rawQuery
            val (type, body) = when (path) {
                "/", "/index.html" -> "text/html; charset=utf-8" to
                    "<html><script>fetch('/api/map?project=a'); new EventSource(selected ? '/events?project=' + p : '/events?overview=1'); fetch(\"/api/console/\" + t)</script></html>"
                "/api/hello" -> "application/json" to """{"keel":true}"""
                "/api/view" -> "application/json" to """{"project":"$query"}"""
                "/api/console/run" -> "application/json" to """{"ok":true}"""
                "/events" -> "text/event-stream" to "retry: 2000\n\nevent: projects\ndata: {\"n\":1}\n\n"
                else -> "text/plain" to "not found"
            }
            val bytes = body.toByteArray()
            ex.responseHeaders.add("Content-Type", type)
            ex.sendResponseHeaders(if (body == "not found") 404 else 200, bytes.size.toLong())
            ex.responseBody.use { it.write(bytes) }
        }
        server.start()
        try {
            val info = get("/api/keel-dashboard").andExpect(status().isOk).json()
            assertThat(info["url"].asText()).isEqualTo("/keel-v1/")
            assertThat(info["started"].asBoolean()).isFalse()

            get("/keel-v1").andExpect(status().isFound).andExpect(header().string("Location", "/keel-v1/"))

            val html = get("/keel-v1/").andExpect(status().isOk).andReturn().response.getContentAsString(Charsets.UTF_8)
            assertThat(html).contains("fetch('/keel-v1/api/map?project=a')", "'/keel-v1/events?project='", "'/keel-v1/events?overview=1'", "\"/keel-v1/api/console/\"")
            assertThat(html).doesNotContain("'/api/", "'/events")

            assertThat(get("/keel-v1/api/view?project=demo").andExpect(status().isOk).json()["project"].asText()).isEqualTo("project=demo")
            val sse = get("/keel-v1/events?project=demo").andExpect(status().isOk).andReturn().response
            assertThat(sse.contentType).startsWith("text/event-stream")
            assertThat(sse.contentAsString).contains("event: projects", "data: {\"n\":1}")

            val posted = mvc.perform(
                MockMvcRequestBuilders.post("/keel-v1/api/console/run?project=demo")
                    .header("Origin", "http://localhost:8080")
                    .contentType(MediaType.APPLICATION_JSON).content("""{"cmd":"status"}"""),
            ).andExpect(status().isOk).json()
            assertThat(posted["ok"].asBoolean()).isTrue()
            assertThat(bodies).contains("""{"cmd":"status"}""")
            assertThat(origins).containsOnly("http://127.0.0.1:$dashboardPort")

            get("/keel-v1/nope").andExpect(status().isNotFound)
            assertThat(hosts).isNotEmpty.allMatch { it == "127.0.0.1:$dashboardPort" }
        } finally {
            server.stop(0)
        }
    }

    @Test
    fun `html rewrite leaves other paths alone`() {
        val out = KeelDashboardController.rewriteHtml("<a href=\"#x/map\">m</a><script>fetch('/api/view'); x('/apiary')</script>")
        assertThat(out).isEqualTo("<a href=\"#x/map\">m</a><script>fetch('/keel-v1/api/view'); x('/apiary')</script>")
    }
}
