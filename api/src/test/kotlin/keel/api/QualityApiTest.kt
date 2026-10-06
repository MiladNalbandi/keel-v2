package keel.api

import com.fasterxml.jackson.databind.JsonNode
import keel.api.quality.QualityService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.8.0: quality runs. keel runs flows on eval projects with chosen models, one case at a time, and scores them. */
class QualityApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate

    private val fake = mapOf("provider" to "fake", "mode" to "api", "model" to "fake")
    private val haiku = mapOf("provider" to "claude", "mode" to "subscription", "model" to "haiku")

    private fun waitDone(id: String): JsonNode {
        val deadline = System.currentTimeMillis() + 30_000
        while (System.currentTimeMillis() < deadline) {
            val r = get("/api/quality/runs/$id").json()
            if (r["status"].asText() !in setOf("queued", "running")) return r
            Thread.sleep(100)
        }
        error("quality run $id did not end")
    }

    @Test
    fun `the score adds the result, the send-backs and the tokens against the estimate`() {
        assertThat(QualityService.score("end", 1.0, 0, 800, 1000)).isEqualTo(100)
        assertThat(QualityService.score("end", 1.0, 2, 1500, 1000)).isEqualTo(60 + 10 + 10)
        assertThat(QualityService.score("stuck", 0.5, 0, 0, null)).isEqualTo(20 + 20 + 10)
        assertThat(QualityService.score("failed", 0.5, 5, 3000, 1000)).isEqualTo(10)
        assertThat(QualityService.score("refused", 0.0, 0, 0, 1000)).isEqualTo(0)
        assertThat(QualityService.score("cap", 0.85, 0, 304_300, 581_850)).isEqualTo(34 + 20 + 20)
    }

    @Test
    fun `a run scores each flow and model on the eval cases, in hidden projects, and a fall shows as a drop`() {
        // the fake model reaches the PR gate; haiku gets stuck at the spec gate
        engine.nextThreadIds.addAll(listOf("t-q-1", "t-q-2"))
        engine.overrides["t-q-1"] = mapOf("status" to "waiting", "current" to "ship_verify",
            "waiting" to mapOf("step" to "ship_pr_gate", "kind" to "gate", "title" to "open the PR", "options" to listOf("approve")))
        engine.overrides["t-q-2"] = mapOf("status" to "waiting", "current" to "g-spec",
            "waiting" to mapOf("step" to "g-spec", "kind" to "gate", "title" to "spec approval", "options" to listOf("approve", "reject")))
        post("/api/quality/runs", mapOf("flows" to emptyList<String>(), "models" to listOf(fake))).andExpect(status().isBadRequest)
        post("/api/quality/runs", mapOf("flows" to listOf("feature"), "models" to emptyList<Any>())).andExpect(status().isBadRequest)
        val run = post("/api/quality/runs", mapOf("flows" to listOf("feature"), "models" to listOf(fake, haiku))).andExpect(status().isOk).json()
        assertThat(run["cases"].size()).isEqualTo(2)
        val done = waitDone(run["id"].asText())
        assertThat(done["status"].asText()).isEqualTo("done")
        val cases = done["cases"].associateBy { it["model"]["provider"].asText() }
        assertThat(cases["fake"]!!["outcome"].asText()).isEqualTo("end")
        assertThat(cases["fake"]!!["reached"].asText()).isEqualTo("the PR gate")
        assertThat(cases["fake"]!!["score"].asInt()).isEqualTo(100)
        assertThat(cases["claude"]!!["outcome"].asText()).isEqualTo("stuck")
        assertThat(cases["claude"]!!["reached"].asText()).isEqualTo("spec approval")
        assertThat(cases["claude"]!!["score"].asInt()).isLessThan(60)
        // the flows ran in run mode auto with the chosen model for every agent, in hidden projects
        val body = engine.calls.last { it.path == "/threads" }.body!!
        assertThat(body["settings"]["run_mode"].asText()).isEqualTo("auto")
        assertThat(body["models"].map { it["model"].asText() }.distinct()).containsExactly("haiku")
        val pid = cases["claude"]!!["project_id"].asText()
        assertThat(get("/api/projects").json().map { it["id"].asText() }).doesNotContain(pid)
        assertThat(jdbc.queryForObject("SELECT hidden FROM projects WHERE id = ?", Int::class.java, pid)).isEqualTo(1)
        assertThat(engine.calls.any { it.path == "/threads/t-q-2/stop" }).isTrue()          // stuck: keel stopped it

        // the same run again, and the fake model now gets stuck: its line drops
        engine.nextThreadIds.addAll(listOf("t-q-3", "t-q-4"))
        engine.overrides["t-q-3"] = engine.overrides["t-q-2"]!!
        engine.overrides["t-q-4"] = engine.overrides["t-q-2"]!!
        val again = post("/api/quality/runs", mapOf("flows" to listOf("feature"), "models" to listOf(fake, haiku))).andExpect(status().isOk).json()
        waitDone(again["id"].asText())
        val view = get("/api/quality").json()
        val lines = view["lines"].associateBy { it["model"]["provider"].asText() }
        assertThat(lines["fake"]!!["points"].map { it["score"].asInt() }).hasSize(2)
        assertThat(lines["fake"]!!["previous"].asInt()).isEqualTo(100)
        assertThat(lines["fake"]!!["drop"].asBoolean()).isTrue()
        assertThat(lines["claude"]!!["drop"].asBoolean()).isFalse()
        assertThat(view["sets"][0]["name"].asText()).isEqualTo("tiny")
        assertThat(view["active"].isNull).isTrue()
    }

    @Test
    fun `the nightly schedule is saved with its flows and models`() {
        put("/api/quality/schedule", mapOf("enabled" to true, "at" to "25:00", "flows" to listOf("feature"), "models" to listOf(fake)))
            .andExpect(status().isBadRequest)
        put("/api/quality/schedule", mapOf("enabled" to true, "at" to "03:30", "flows" to listOf("feature"), "models" to emptyList<Any>()))
            .andExpect(status().isBadRequest)
        val s = put("/api/quality/schedule", mapOf("enabled" to true, "at" to "3:30", "flows" to listOf("feature"), "models" to listOf(fake)))
            .andExpect(status().isOk).json()
        assertThat(s["at"].asText()).isEqualTo("03:30")
        assertThat(get("/api/quality").json()["schedule"]["enabled"].asBoolean()).isTrue()
        put("/api/quality/schedule", mapOf("enabled" to false, "at" to "03:30", "flows" to listOf("feature"), "models" to listOf(fake))).andExpect(status().isOk)
    }
}
