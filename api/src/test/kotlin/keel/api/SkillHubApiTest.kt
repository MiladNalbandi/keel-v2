package keel.api

import keel.api.support.ApiTest
import keel.api.skills.SkillService
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** keel's spec skills (spec-clarify, spec-writing, from content/skills) are in the Skill hub and assigned to the explorer's spec step. */
class SkillHubApiTest : ApiTest() {
    @Test
    fun `spec-clarify and spec-writing are keel skills for the explorer`() {
        val (pid, _) = newProject("skill-hub")
        val skills = get("/api/projects/$pid/skills").andExpect(status().isOk).json()
        val byId = skills.associateBy { it["id"].asText() }
        for (id in listOf("spec-clarify", "spec-writing")) {
            val s = byId[id] ?: error("missing skill $id")
            assertThat(s["source"].asText()).isEqualTo("keel")
            assertThat(s["kind"].asText()).isEqualTo("authoring")
            assertThat(s["agents"].map { it.asText() }).containsExactly("explorer")
            assertThat(s["when"].asText()).isEqualTo("spec")
            assertThat(s["enabled"].asBoolean()).isTrue()
        }
        val detail = get("/api/skills/spec-clarify?project=$pid").andExpect(status().isOk).json()
        assertThat(detail["body"].asText()).contains("keel-questions", "recommended")
        assertThat(detail["refs"].map { it["path"].asText() }).contains("references/clarify-probes.md")
        assertThat(get("/api/skills/spec-writing?project=$pid").json()["refs"].map { it["path"].asText() })
            .contains("references/acceptance-criteria.md", "references/ui-mockup.md", "references/request-path.md")
        // only content/skills and the packs are keel's: nothing else is scanned
        assertThat(byId.keys).containsExactlyInAnyOrder("kotlin-spring-testing", "spec-clarify", "spec-writing", "django-testing")
    }

    @Test
    fun `0_20_0 the EGM skills sort as review and authoring, and only egm-resolve goes on an agent by default`() {
        assertThat(SkillService.kindOf("egm-review")).isEqualTo("review")
        assertThat(SkillService.kindOf("egm-resolve")).isEqualTo("review")
        assertThat(SkillService.kindOf("egm-draft")).isEqualTo("authoring")
        assertThat(SkillService.defaultAssignment("egm-resolve").first).containsExactly("egm-resolver")
        for (id in listOf("egm-review", "egm-review-loop", "egm-brainstorm", "egm-draft", "egm-bootstrap", "egm-drift-report")) {
            assertThat(SkillService.defaultAssignment(id).first).isEmpty()
        }
    }

    @Test
    fun `0_20_0 Resources asks the engine, and a tool below the admin level cannot change it`() {
        engine.extraRoutes["/resources"] = { _ -> 200 to mapOf("max_agents" to 3, "low_priority" to true) }
        assertThat(get("/api/resources").andExpect(status().isOk).json()["max_agents"].asInt()).isEqualTo(3)
        put("/api/resources", mapOf("max_agents" to 3)).andExpect(status().isOk)
        assertThat(engine.lastBody("/resources")!!["max_agents"].asInt()).isEqualTo(3)
        val mcp = mapOf("X-Keel-Via" to "mcp", "X-Keel-Client" to "test", "X-Keel-Tool" to "t")
        put("/api/resources", mapOf("max_agents" to 1), mcp).andExpect(status().isForbidden)
        engine.extraRoutes.remove("/resources")
    }

    @Test
    fun `the explorer's skill text carries the question format`() {
        val (pid, _) = newProject("skill-text")
        val start = flows.buildStart(pid, "feature", "x", null)
        val text = start.skills["explorer"] ?: error("explorer has no skills")
        assertThat(text).contains("keel-questions", "Acceptance criteria")
    }

    @org.springframework.beans.factory.annotation.Autowired lateinit var flows: keel.api.flow.FlowService
}
