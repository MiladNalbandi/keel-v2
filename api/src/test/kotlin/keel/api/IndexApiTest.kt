package keel.api

import keel.api.events.EngineEvent
import keel.api.events.EventService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

/** Plan 5b: a project is scanned when it is added; its code graph index status and rebuild go through the engine. */
class IndexApiTest : ApiTest() {
    @Autowired lateinit var events: EventService

    private fun waitFor(what: String, check: () -> Boolean) {
        val until = System.currentTimeMillis() + 5000
        while (System.currentTimeMillis() < until) {
            if (check()) return
            Thread.sleep(20)
        }
        throw AssertionError("timed out waiting for $what")
    }

    @Test
    fun `adding a project starts a scan, and the index routes proxy the engine`() {
        val (pid, root) = newProject("index-scan")
        waitFor("the scan call") { engine.calls.any { it.path == "/projects/$pid/scan" } }
        val scan = engine.lastBody("/projects/$pid/scan")!!
        assertThat(scan["root"].asText()).isEqualTo(root.toString())
        assertThat(scan["rebuild"].asBoolean()).isFalse()

        engine.index[pid] = mapOf("project" to pid, "status" to "ready", "files" to 42, "symbols" to 310, "indexed_at" to "2026-10-05T10:00:00Z")
        val idx = get("/api/projects/$pid/index").andExpect(status().isOk).json()
        assertThat(idx["status"].asText()).isEqualTo("ready")
        assertThat(idx["files"].asInt()).isEqualTo(42)

        val again = post("/api/projects/$pid/index/rebuild").andExpect(status().isOk).json()
        assertThat(again["status"].asText()).isEqualTo("indexing")
        assertThat(engine.lastBody("/projects/$pid/scan")!!["rebuild"].asBoolean()).isTrue()
        get("/api/projects/nope/index").andExpect(status().isNotFound)
    }

    @Test
    fun `index done becomes a notification`() {
        val (pid, _) = newProject("index-notify")
        events.ingest(listOf(EngineEvent("index.done", "", pid, data = mapOf("status" to "ready", "files" to 12, "symbols" to 99))))
        events.ingest(listOf(EngineEvent("index.done", "", pid, data = mapOf("status" to "failed", "error" to "CodeGraph is not installed"))))
        val titles = get("/api/notifications").json().map { it["title"].asText() }
        assertThat(titles).contains("Index ready: 12 files, 99 symbols", "Index failed: CodeGraph is not installed")
    }

    @Test
    fun `the map is the engine's, a map file keel v1 left in the project is not read`() {
        val (pid, root) = newProject("index-oldmap")
        Files.createDirectories(root.resolve(".keel"))
        Files.writeString(root.resolve(".keel/map.json"), """{"sha":"old1234","at":"2026-01-01T00:00:00Z","levels":{}}""")
        assertThat(get("/api/projects/$pid/map").json()["missing"].asText()).contains("No map yet")
        post("/api/projects/$pid/map/rebuild").andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/map").json()["sha"].asText()).isEqualTo("abc1234")
    }
}
