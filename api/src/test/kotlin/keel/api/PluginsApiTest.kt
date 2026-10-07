package keel.api

import keel.api.connections.SecretService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.10.0: the Database and Git plugins. On per project (or for all), their secrets ride along with every flow and
 *  KeelBot turn, the person's own database and git calls go to the engine, and nothing works while a plugin is off. */
class PluginsApiTest : ApiTest() {
    @Autowired lateinit var secrets: SecretService
    @Autowired lateinit var jdbc: JdbcTemplate

    private fun del(url: String) = mvc.perform(MockMvcRequestBuilders.delete(url))

    @Test
    fun `a plugin is on for one project, or for all, and a project's own choice wins`() {
        val (a, _) = newProject("plug-a")
        val (b, _) = newProject("plug-b")
        assertThat(get("/api/plugins").json().map { it["name"].asText() }).containsExactly("db", "git")
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
    fun `a connection's password is a secret, the web sees the address without it, and the test result is kept`() {
        val (pid, _) = newProject("plug-db")
        get("/api/projects/$pid/db/suggest").andExpect(status().isOk)
        val made = post("/api/projects/$pid/db/connections", mapOf("name" to "local", "url" to "postgres://app:s3cret@localhost:5432/app", // keel:allow-secret
            "env" to "local", "source" to "docker-compose.yml (service db)")).andExpect(status().isOk).json()
        assertThat(made["connection"]["shown"].asText()).isEqualTo("postgres://app:•••@localhost:5432/app") // keel:allow-secret
        assertThat(made["connection"]["ok"].asBoolean()).isTrue()
        assertThat(made["connection"]["tables"].asInt()).isEqualTo(23)
        assertThat(made["connection"]["can_change"].asBoolean()).isTrue()
        assertThat(made.toString()).doesNotContain("s3cret")
        val id = jdbc.queryForObject("SELECT id FROM db_connections WHERE project_id = ?", String::class.java, pid)
        assertThat(secrets.get("db.$id")).isEqualTo("postgres://app:s3cret@localhost:5432/app") // keel:allow-secret
        post("/api/projects/$pid/db/connections", mapOf("name" to "local", "url" to "postgres://x@y/z")).andExpect(status().isConflict)
        post("/api/projects/$pid/db/connections", mapOf("name" to "o", "url" to "oracle://x@y/z")).andExpect(status().isBadRequest)
        val bad = post("/api/projects/$pid/db/connections", mapOf("name" to "prod", "url" to "postgres://app:wrong@db:5432/app", "env" to "prod")) // keel:allow-secret
            .andExpect(status().isOk).json()
        assertThat(bad["connection"]["ok"].asBoolean()).isFalse()
        assertThat(bad["connection"]["can_change"].asBoolean()).isFalse()           // prod: read only, always
        assertThat(bad["connection"]["error"].asText()).contains("password authentication failed", "Check the password.")

        // the plugin is off: no schema, no query
        post("/api/projects/$pid/db/query", mapOf("sql" to "select 1")).andExpect(status().isConflict)
        put("/api/projects/$pid/plugins/db", mapOf("enabled" to true)).andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/db/schema").json()["connection"].asText()).isEqualTo("local")   // local by default
        val q = post("/api/projects/$pid/db/query", mapOf("sql" to "update scores set value = 0", "change" to true)).json()
        assertThat(q["changed"].asInt()).isEqualTo(2)
        val sent = engine.lastBody("/plugins/db/query")!!
        assertThat(sent["connection"]["url"].asText()).contains("s3cret")              // the engine gets it, memory only
        assertThat(sent["confirm"].asBoolean()).isFalse()
        del("/api/projects/$pid/db/connections/prod").andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/db/connections").json().map { it["name"].asText() }).containsExactly("local")
    }

    @Test
    fun `flows and KeelBot turns carry the plugins and their secrets, and KeelBot gets their commands`() {
        val (pid, _) = newProject("plug-flow")
        put("/api/projects/$pid/plugins/db", mapOf("enabled" to true)).andExpect(status().isOk)
        put("/api/projects/$pid/plugins/git", mapOf("enabled" to true)).andExpect(status().isOk)
        post("/api/projects/$pid/db/connections", mapOf("name" to "local", "url" to "sqlite:app.db")).andExpect(status().isOk)
        put("/api/secrets/GITHUB_REPO_TOKEN", mapOf("value" to " ghp_example token ")).andExpect(status().isOk)
        assertThat(get("/api/github").json()["hint"].asText()).isEqualTo("…ken")

        engine.nextThreadIds.add("t-plug-1")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Data", "allow_fake" to true, "allow_dirty" to true))
            .andExpect(status().isOk)
        val body = engine.lastBody("/threads")!!
        assertThat(body["settings"]["plugins"].map { it.asText() }).containsExactly("db", "git")
        assertThat(body["settings"]["push_pr"].asText()).isEqualTo("ask")
        assertThat(body["keys"]["github"].asText()).isEqualTo("ghp_exampletoken")
        assertThat(body["keys"]["db:local"].asText()).contains("\"url\":\"sqlite:app.db\"", "\"env\":\"local\"")
        assertThat(body["settings"].toString()).doesNotContain("sqlite:app.db")       // secrets never in the saved settings

        val sid = post("/api/projects/$pid/helper/sessions", emptyMap<String, Any>()).json()["id"].asText()
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "How many scores?")).andExpect(status().isOk)
        val turn = engine.lastBody("/helper/sessions/$sid/turn")!!
        assertThat(turn["plugins"].map { it.asText() }).containsExactly("db", "git")
        assertThat(turn["keys"]["db:local"].asText()).contains("sqlite:app.db")
        get("/api/projects/$pid/helper/commands").andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/commands")!!["plugins"].map { it.asText() }).containsExactly("db", "git")
        del("/api/secrets/GITHUB_REPO_TOKEN").andExpect(status().isOk)
    }

    @Test
    fun `the person's git actions go to the engine with the token and the commit settings`() {
        val (pid, _) = newProject("plug-git")
        get("/api/projects/$pid/git/status").andExpect(status().isConflict)
        put("/api/projects/$pid/plugins/git", mapOf("enabled" to true)).andExpect(status().isOk)
        put("/api/projects/$pid/settings", mapOf("commit_author" to "Ada Lovelace <ada@example.com>")).andExpect(status().isOk)
        post("/api/projects/$pid/git/commit", mapOf("message" to "fix: x")).andExpect(status().isOk)
        val sent = engine.lastBody("/plugins/git/commit")!!
        assertThat(sent["message"].asText()).isEqualTo("fix: x")
        assertThat(sent["settings"]["commit_author"].asText()).isEqualTo("Ada Lovelace <ada@example.com>")
        assertThat(sent["settings"]["commit_coauthor"].asBoolean()).isTrue()
        post("/api/projects/$pid/git/switch", mapOf("branch" to "feat/x", "create" to true)).andExpect(status().isOk)
        assertThat(engine.lastBody("/plugins/git/switch")!!["create"].asBoolean()).isTrue()
        post("/api/projects/$pid/git/pr", mapOf("title" to "X", "body" to "why")).andExpect(status().isOk)
        assertThat(engine.lastBody("/plugins/git/pr")!!["title"].asText()).isEqualTo("X")
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
