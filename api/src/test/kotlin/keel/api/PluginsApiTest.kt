package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.10.0: the Database and Git plugins. On per project (or for all), their secrets ride along with every flow (and
 *  KeelBot turn: plugins/keelbot/api), and nothing works while a plugin is off. The person's own database and git calls are the plugins' own
 *  tests (plugins/db/api: DatabaseApiTest, plugins/git/api: GitPluginApiTest). */
class PluginsApiTest : ApiTest() {
    private fun del(url: String) = mvc.perform(MockMvcRequestBuilders.delete(url))

    @Test
    fun `a plugin is on for one project, or for all, and a project's own choice wins`() {
        val (a, _) = newProject("plug-a")
        val (b, _) = newProject("plug-b")
        assertThat(get("/api/plugins").json().map { it["name"].asText() }).containsExactly("db", "git", "ci", "review")
        assertThat(get("/api/projects/$a/plugins").json().map { it["enabled"].asBoolean() }).containsOnly(false)
        val list = put("/api/projects/$a/plugins/db", mapOf("enabled" to true)).andExpect(status().isOk).json()
        assertThat(list.first { it["name"].asText() == "db" }["scope"].asText()).isEqualTo("project")
        assertThat(get("/api/projects/$b/plugins").json().first { it["name"].asText() == "db" }["enabled"].asBoolean()).isFalse()
        put("/api/projects/$b/plugins/git", mapOf("enabled" to true, "scope" to "all")).andExpect(status().isOk)
        assertThat(get("/api/projects/$a/plugins").json().first { it["name"].asText() == "git" }["scope"].asText()).isEqualTo("all")
        put("/api/projects/$a/plugins/git", mapOf("enabled" to false)).andExpect(status().isOk)       // a's own row wins
        assertThat(get("/api/projects/$a/plugins").json().first { it["name"].asText() == "git" }["enabled"].asBoolean()).isFalse()
        assertThat(get("/api/projects/$b/plugins").json().first { it["name"].asText() == "git" }["enabled"].asBoolean()).isTrue()
        put("/api/projects/$a/plugins/nope", mapOf("enabled" to true)).andExpect(status().isNotFound)
        put("/api/projects/$a/plugins/db", mapOf("enabled" to true, "scope" to "everywhere")).andExpect(status().isBadRequest)
        put("/api/projects/$b/plugins/git", mapOf("enabled" to false, "scope" to "all")).andExpect(status().isOk)
    }

    @Test
    fun `flows carry the plugins and their secrets`() {
        val (pid, _) = newProject("plug-flow")
        put("/api/projects/$pid/plugins/db", mapOf("enabled" to true)).andExpect(status().isOk)
        put("/api/projects/$pid/plugins/git", mapOf("enabled" to true)).andExpect(status().isOk)
        put("/api/secrets/GITHUB_REPO_TOKEN", mapOf("value" to " ghp_example token ")).andExpect(status().isOk)
        assertThat(get("/api/github").json()["hint"].asText()).isEqualTo("…ken")

        engine.nextThreadIds.add("t-plug-1")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Data", "allow_fake" to true, "allow_dirty" to true))
            .andExpect(status().isOk)
        val body = engine.lastBody("/threads")!!
        assertThat(body["settings"]["plugins"].map { it.asText() }).containsExactly("db", "git")
        assertThat(body["settings"]["push_pr"].asText()).isEqualTo("ask")
        assertThat(body["keys"]["github"].asText()).isEqualTo("ghp_exampletoken")
        assertThat(body["settings"].toString()).doesNotContain("ghp_exampletoken")    // secrets never in the saved settings
        // KeelBot's turns carry them too (plugins/keelbot/api tests that)
        del("/api/secrets/GITHUB_REPO_TOKEN").andExpect(status().isOk)
    }

    @Test
    fun `claude code's acting tool asks the person through the engine`() {
        val (pid, _) = newProject("plug-ask")
        val q = post("/api/projects/$pid/plugins/ask", mapOf("title" to "Claude Code: push?", "command" to "push the branch")).andExpect(status().isOk).json()
        assertThat(engine.lastBody("/plugins/ask")!!["project"].asText()).isEqualTo(pid)
        assertThat(get("/api/plugins/asks/${q["id"].asText()}").json()["decision"].asText()).isEqualTo("allow")
        get("/api/plugins/asks/../../threads").andExpect(status().isNotFound)
        get("/api/plugins/asks/bad").andExpect(status().isBadRequest)
    }
}
