package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.9.0: KeelBot knows the project's workflows and flows, checks a workflow it wrote, and the Workflows page has
 *  folders and each workflow's runs. */
class KeelBotApiTest : ApiTest() {

    private fun start(pid: String, title: String) =
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to title, "allow_fake" to true, "allow_dirty" to true,
            "where" to "worktree")).andExpect(status().isOk)

    @Test
    fun `every KeelBot message carries the workflows it can suggest and the project's flows`() {
        val (pid, _) = newProject("keelbot-context")
        engine.nextThreadIds.addAll(listOf("t-kb-1", "t-kb-2"))
        start(pid, "Euro prices")
        start(pid, "Score ranks")
        val sid = post("/api/projects/$pid/helper/sessions", emptyMap<String, Any>()).andExpect(status().isOk).json()["id"].asText()
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "Which workflow for a new report page?")).andExpect(status().isOk)
        val keel = engine.lastBody("/helper/sessions/$sid/turn")!!["keel"]
        val feature = keel["workflows"].first { it["id"].asText() == "feature" }
        assertThat(feature["source"].asText()).isEqualTo("keel")
        assertThat(feature["steps"].size()).isGreaterThan(0)
        assertThat(feature["last_run"]["title"].asText()).isEqualTo("Score ranks")
        assertThat(keel["flows"].map { it["title"].asText() }).containsExactly("Score ranks", "Euro prices")
        assertThat(keel["flows"][0]["workflow"].asText()).isEqualTo("feature")
        assertThat(keel["flows"][0]["thread_id"].asText()).isEqualTo("t-kb-2")
    }

    @Test
    fun `a workflow KeelBot wrote is checked without saving, then saved into a folder`() {
        val (pid, _) = newProject("keelbot-check")
        val yaml = """
            id: check-before-push
            name: check before push
            keel_rules: false
            version: 1
            steps:
              - { id: lint, kind: code, name: run the linters, action: "run: npm run lint", soft: true }
              - { id: tests, kind: code, name: run the tests, action: "run: npm test" }
              - { id: look, kind: gate, name: look at the results }
        """.trimIndent()
        val ok = post("/api/projects/$pid/workflows/check", mapOf("yaml" to yaml)).andExpect(status().isOk).json()
        assertThat(ok["valid"].asBoolean()).isTrue()
        assertThat(ok["steps"].asInt()).isEqualTo(3)
        assertThat(ok["gates"].asInt()).isEqualTo(1)
        assertThat(ok["agents"].size()).isEqualTo(0)                                  // code steps only: no model runs
        assertThat(ok["commands"].map { it.asText() }).containsExactly("npm run lint", "npm test")
        assertThat(get("/api/projects/$pid/workflows").json().none { it["name"].asText() == "check before push" }).isTrue()

        val bad = post("/api/projects/$pid/workflows/check", mapOf("yaml" to yaml.replace("look at", "INVALID"))).json()
        assertThat(bad["valid"].asBoolean()).isFalse()
        assertThat(bad["errors"][0].asText()).contains("INVALID")
        val broken = post("/api/projects/$pid/workflows/check", mapOf("yaml" to "steps: [")).andExpect(status().isOk).json()
        assertThat(broken["valid"].asBoolean()).isFalse()
        assertThat(broken["errors"][0].asText()).contains("not a workflow")

        val saved = post("/api/projects/$pid/workflows/import", mapOf("yaml" to yaml, "folder" to " Checks ")).andExpect(status().isOk).json()
        val wid = saved["workflow"]["id"].asText()
        assertThat(saved["workflow"]["folder"].asText()).isEqualTo("Checks")
        assertThat(get("/api/projects/$pid/workflows").json().first { it["id"].asText() == wid }["folder"].asText()).isEqualTo("Checks")
    }

    @Test
    fun `workflows sit in folders per project, and each one has its runs`() {
        val (pid, _) = newProject("keelbot-folders")
        val (other, _) = newProject("keelbot-folders-2")
        put("/api/projects/$pid/workflows/feature/folder", mapOf("folder" to "Daily  work")).andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/workflows").json().first { it["id"].asText() == "feature" }["folder"].asText()).isEqualTo("Daily work")
        assertThat(get("/api/projects/$other/workflows").json().first { it["id"].asText() == "feature" }["folder"].isNull).isTrue()
        put("/api/projects/$pid/workflows/feature/folder", mapOf("folder" to "x".repeat(41))).andExpect(status().isBadRequest)
        put("/api/projects/$pid/workflows/nope/folder", mapOf("folder" to "A")).andExpect(status().isNotFound)
        put("/api/projects/$pid/workflows/feature/folder", mapOf("folder" to "")).andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/workflows").json().first { it["id"].asText() == "feature" }["folder"].isNull).isTrue()

        engine.nextThreadIds.addAll(listOf("t-kbr-1", "t-kbr-2"))
        start(pid, "First")
        start(pid, "Second")
        val runs = get("/api/projects/$pid/runs").andExpect(status().isOk).json()
        assertThat(runs.map { it["title"].asText() }).containsExactly("Second", "First")
        assertThat(runs[0]["where"].asText()).isEqualTo("worktree")
        assertThat(get("/api/projects/$pid/runs?workflow=feature&limit=1").json().map { it["thread_id"].asText() }).containsExactly("t-kbr-2")
        assertThat(get("/api/projects/$pid/runs?workflow=knowledge-refresh").json().size()).isEqualTo(0)
        val feature = get("/api/projects/$pid/workflows").json().first { it["id"].asText() == "feature" }
        assertThat(feature["runs"].asInt()).isEqualTo(2)
        assertThat(feature["last_run"]["thread_id"].asText()).isEqualTo("t-kbr-2")
    }
}
