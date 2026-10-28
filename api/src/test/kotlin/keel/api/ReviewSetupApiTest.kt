package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * keel 0.18.65 Settings › Review: the engine reads and writes the review block of .keel/config.yml and the lens files;
 * the api gives it the project's folder and models, checks the agents and adds them to the view.
 */
class ReviewSetupApiTest : ApiTest() {

    @Test
    fun `the view goes to the engine with the project's folder and models, and lists the project's agents`() {
        val (pid, root) = newProject("review-setup")
        val v = get("/api/projects/$pid/review-setup").andExpect(status().isOk).json()
        val body = engine.lastBody("/review-config/view")!!
        assertThat(body["root"].asText()).isEqualTo(root.toString())
        assertThat(body["models"].has("default")).isTrue()
        assertThat(body["models"].has("reviewer")).isTrue()
        assertThat(v["config"]["reviewers"].size()).isEqualTo(2)
        assertThat(v["agents"].map { it["id"].asText() }).contains("lens-reviewer", "implementer")
    }

    @Test
    fun `a reviewer's agent must be the project's, and a lens name is never a path`() {
        val (pid, _) = newProject("review-setup-checks")
        engine.extraRoutes["/review-config"] = { b -> 200 to mapOf("config" to b?.path("patch")) }
        engine.extraRoutes["/review-config/lenses"] = { b -> 200 to mapOf("lens" to mapOf("name" to "money-rounding"), "view" to mapOf("ok" to true)) }
        try {
            val bad = put("/api/projects/$pid/review-setup", mapOf("reviewers" to listOf(mapOf("name" to "a", "focus" to "b", "agent" to "nobody"))))
                .andExpect(status().isBadRequest).json()
            assertThat(bad["error"].asText()).contains("Reviewer A").contains("nobody")
            put("/api/projects/$pid/review-setup", mapOf("reviewers" to listOf(mapOf("name" to "a", "focus" to "b", "agent" to "lens-reviewer"))))
                .andExpect(status().isOk)
            assertThat(engine.lastBody("/review-config")!!["patch"]["reviewers"][0]["agent"].asText()).isEqualTo("lens-reviewer")
            put("/api/projects/$pid/review-setup", mapOf("reset" to true)).andExpect(status().isOk)
            assertThat(engine.lastBody("/review-config")!!["patch"]["reset"].asBoolean()).isTrue()

            val made = post("/api/projects/$pid/review-setup/lenses", mapOf("title" to "Money rounding", "reviewer" to "C",
                "checklist" to "- cents as integers")).andExpect(status().isOk).json()
            assertThat(made["lens"]["name"].asText()).isEqualTo("money-rounding")
            assertThat(engine.lastBody("/review-config/lenses")!!["lens"]["reviewer"].asText()).isEqualTo("C")
            put("/api/projects/$pid/review-setup/lenses/..", mapOf("text" to "x")).andExpect(status().isBadRequest)
            get("/api/projects/$pid/review-setup/lenses/Money%20Rounding").andExpect(status().isBadRequest)
        } finally {
            engine.extraRoutes.remove("/review-config")
            engine.extraRoutes.remove("/review-config/lenses")
        }
    }
}
