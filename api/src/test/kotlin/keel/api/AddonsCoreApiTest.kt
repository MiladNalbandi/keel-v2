package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.13.0 keel's core without an add-on: features say "dev" only, add-on workflows stay hidden, and a task's
 *  keel-criteria block goes to its flow. */
class AddonsCoreApiTest : ApiTest() {
    @AfterEach
    fun clean() {
        engine.extraTemplates.clear()
        put("/api/settings/general", mapOf("keel_mode" to "auto"))
    }

    @Test
    fun `without an add-on keel is dev only and the mode setting changes nothing`() {
        val f = get("/api/features").andExpect(status().isOk).json()
        assertThat(f["mode"].asText()).isEqualTo("dev")
        assertThat(f["modes"].map { it.asText() }).containsExactly("dev")
        assertThat(f["parts"]["dev"].asBoolean()).isTrue()
        assertThat(f["addons"].size()).isZero()
        assertThat(f["screens"].size()).isZero()
        put("/api/settings/general", mapOf("keel_mode" to "both")).andExpect(status().isOk)
        assertThat(get("/api/features").json()["mode"].asText()).isEqualTo("dev")
        put("/api/settings/general", mapOf("keel_mode" to "everything")).andExpect(status().isBadRequest)
        val (pid, _) = newProject("core-mode")
        put("/api/projects/$pid/settings", mapOf("keel_mode" to "dev")).andExpect(status().isBadRequest)
    }

    @Test
    fun `an add-on's workflow is not listed while its add-on is not installed`() {
        engine.extraTemplates += mapOf("id" to "product-impact", "name" to "impact (keel product)", "addon" to "product",
            "keel_rules" to false, "version" to 1, "steps" to listOf(mapOf("id" to "a", "kind" to "parallel", "name" to "a",
                "agent" to "impact-analyst", "from" to "repos", "root" to "item")), "yaml" to "")
        val (pid, _) = newProject("core-wf")
        val ids = get("/api/projects/$pid/workflows").json().map { it["id"].asText() }
        assertThat(ids).contains("feature").doesNotContain("product-impact")
    }

    @Test
    fun `a task's keel-criteria block goes to its flow as the criteria`() {
        val (pid, _) = newProject("core-criteria")
        val description = """
            Why: EU visitors want euro prices.

            ```keel-criteria
            AC-1 [API] GET /prices returns the currency
            - AC-2 [web] An EU visitor sees the price in euro
            not a criterion
            ```
        """.trimIndent()
        val task = post("/api/projects/$pid/tasks", mapOf("title" to "Euro prices", "description" to description, "type" to "story")).json()
        post("/api/tasks/${task["id"].asText()}/start", mapOf("allow_fake" to true)).andExpect(status().isOk)
        val sent = engine.lastBody("/threads")!!
        assertThat(sent["acs"].map { it["id"].asText() + " " + it["layer"].asText() + " " + it["title"].asText() })
            .containsExactly("AC-1 API GET /prices returns the currency", "AC-2 WEB An EU visitor sees the price in euro")
        assertThat(sent["request"].asText()).contains("Why: EU visitors want euro prices.")
    }
}
