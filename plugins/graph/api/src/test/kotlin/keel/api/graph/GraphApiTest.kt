package keel.api.graph

import keel.api.events.EngineEvent
import keel.api.events.EventService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * The Graph plugin's api part: the index status and rebuild, index.done as a notification, and the code graph routes
 * proxy the engine (moved from keel's IndexApiTest and ApprovalsApiTest with the code).
 */
class GraphApiTest : ApiTest() {
    @Autowired lateinit var events: EventService

    @Test
    fun `the index routes proxy the engine, and a rebuild scans again with a full index`() {
        val (pid, _) = newProject("index-routes")
        engine.index[pid] = mapOf("project" to pid, "status" to "ready", "files" to 42, "symbols" to 310, "indexed_at" to "2026-10-05T10:00:00Z")
        val idx = get("/api/projects/$pid/index").andExpect(status().isOk).json()
        assertThat(idx["status"].asText()).isEqualTo("ready")
        assertThat(idx["files"].asInt()).isEqualTo(42)

        val again = post("/api/projects/$pid/index/rebuild").andExpect(status().isOk).json()
        assertThat(again["status"].asText()).isEqualTo("indexing")
        assertThat(engine.lastBody("/projects/$pid/scan")!!["rebuild"].asBoolean()).isTrue()
        get("/api/projects/nope/index").andExpect(status().isNotFound)
        post("/api/projects/nope/index/rebuild").andExpect(status().isNotFound)
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
    fun `the code graph routes proxy the engine with the search text and the symbol`() {
        val (pid, _) = newProject("graph-routes")
        val o = get("/api/projects/$pid/graph").andExpect(status().isOk).json()
        assertThat(o["groups"][0]["label"].asText()).isEqualTo("app")
        val r = get("/api/projects/$pid/graph/search?q=Sv c").andExpect(status().isOk).json()
        assertThat(r["results"][0]["q"].asText()).isEqualTo("Sv c")                    // sent on as JSON, not in a URL
        val n = get("/api/projects/$pid/graph/node?id=class:svc&depth=5").andExpect(status().isOk).json()
        assertThat(n["focus"]["id"].asText()).isEqualTo("class:svc")
        assertThat(n["depth"].asInt()).isEqualTo(2)                                       // one or two steps only
        get("/api/projects/$pid/graph/node").andExpect(status().isBadRequest)
        get("/api/projects/nope/graph").andExpect(status().isNotFound)
    }
}
