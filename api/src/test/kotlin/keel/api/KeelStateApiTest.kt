package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * 0.18.64 keel's flow state for a project (MCP keel_state): the keel v2 flows that run or wait there and keel v1's
 * leftover .keel/state.json (engine GET /keel-state), whose close archives the file (engine POST /keel-state/close).
 * Over MCP, close is a write.
 */
class KeelStateApiTest : ApiTest() {

    private val v1 = mapOf("path" to ".keel/state.json", "flow" to "feature", "phase" to "full-review", "active" to true,
        "name" to "feature 012-prices, phase full-review",
        "line" to "a keel v1 flow (feature 012-prices, phase full-review) left .keel/state.json: it blocks commits")

    @Test
    fun `the state lists the running v2 flows and the v1 leftover, and close archives it`() {
        val (pid, root) = newProject("state-shop")
        engine.extraRoutes["/keel-state"] = { _ -> 200 to mapOf("v1" to v1) }
        engine.extraRoutes["/keel-state/close"] = { body ->
            200 to mapOf("archived" to ".keel/archive/state-2026-10-28.json", "flow" to "feature 012-prices, phase full-review",
                "was_active" to true, "root" to body?.path("root")?.asText())
        }
        try {
            engine.nextThreadIds.add("t-state-a")
            post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Prices in euros", "allow_fake" to true))
                .andExpect(status().isOk)
            val got = get("/api/projects/$pid/keel-state").andExpect(status().isOk).json()
            assertThat(got["flows"].map { it["id"].asText() }).containsExactly("t-state-a")
            assertThat(got["flows"][0]["title"].asText()).isEqualTo("Prices in euros")
            assertThat(got["v1"]["line"].asText()).contains("it blocks commits")
            assertThat(engine.calls.last { it.path == "/keel-state" }.query).contains(root.fileName.toString())

            val closed = post("/api/projects/$pid/keel-state/close").andExpect(status().isOk).json()
            assertThat(closed["archived"].asText()).isEqualTo(".keel/archive/state-2026-10-28.json")
            assertThat(closed["root"].asText()).endsWith(root.fileName.toString())
            // over MCP, show is a read and close a write
            val viaMcp = mapOf("X-Keel-Via" to "mcp", "X-Keel-Client" to "test", "X-Keel-Tool" to "keel_state")
            post("/api/projects/$pid/keel-state/close", null, viaMcp).andExpect(status().isForbidden)
        } finally {
            engine.extraRoutes.remove("/keel-state")
            engine.extraRoutes.remove("/keel-state/close")
        }
    }

    @Test
    fun `no leftover state is null`() {
        val (pid, _) = newProject("state-none")
        engine.extraRoutes["/keel-state"] = { _ -> 200 to mapOf("v1" to null) }
        try {
            val got = get("/api/projects/$pid/keel-state").andExpect(status().isOk).json()
            assertThat(got["v1"].isNull).isTrue()
            assertThat(got["flows"].size()).isEqualTo(0)
        } finally {
            engine.extraRoutes.remove("/keel-state")
        }
    }
}
