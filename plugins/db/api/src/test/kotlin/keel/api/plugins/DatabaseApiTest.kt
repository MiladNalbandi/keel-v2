package keel.api.plugins

import keel.api.connections.SecretService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.10.0 the Database plugin (plugins/db, moved from keel's api tests): a connection's password is a secret, the
 *  person's own queries go to the engine only while the plugin is on, flows and KeelBot turns carry the connections
 *  (its FlowContributor, DatabaseKeys), and Connections › Databases is its connection kind. */
class DatabaseApiTest : ApiTest() {
    @Autowired lateinit var secrets: SecretService
    @Autowired lateinit var jdbc: JdbcTemplate

    private fun del(url: String) = mvc.perform(MockMvcRequestBuilders.delete(url))

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
    fun `flows and KeelBot turns carry its connections while it is on, next to the GitHub token`() {
        put("/api/secrets/GITHUB_REPO_TOKEN", mapOf("value" to "ghp_exampletoken")).andExpect(status().isOk)
        // off for a project: its connections stay home
        val (off, _) = newProject("plug-db-off")
        post("/api/projects/$off/db/connections", mapOf("name" to "local", "url" to "sqlite:app.db")).andExpect(status().isOk)
        engine.nextThreadIds.add("t-plug-db-0")
        post("/api/projects/$off/flows", mapOf("workflow_id" to "feature", "title" to "Off", "allow_fake" to true, "allow_dirty" to true))
            .andExpect(status().isOk)
        assertThat(engine.lastBody("/threads")!!["keys"].fieldNames().asSequence().toList()).contains("github").doesNotContain("db:local")

        val (pid, _) = newProject("plug-db-keys")
        post("/api/projects/$pid/db/connections", mapOf("name" to "local", "url" to "sqlite:app.db")).andExpect(status().isOk)
        post("/api/projects/$pid/db/connections", mapOf("name" to "ci", "url" to "sqlite:ci.db", "env" to "test")).andExpect(status().isOk)
        put("/api/projects/$pid/plugins/db", mapOf("enabled" to true)).andExpect(status().isOk)
        engine.nextThreadIds.add("t-plug-db-1")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Data", "allow_fake" to true, "allow_dirty" to true))
            .andExpect(status().isOk)
        val body = engine.lastBody("/threads")!!
        assertThat(body["settings"]["plugins"].map { it.asText() }).containsExactly("db")
        assertThat(body["keys"]["github"].asText()).isEqualTo("ghp_exampletoken")
        assertThat(body["keys"]["db:local"].asText()).contains("\"url\":\"sqlite:app.db\"", "\"env\":\"local\"")
        assertThat(body["keys"]["db:ci"].asText()).isEqualTo("""{"name":"ci","kind":"sqlite","url":"sqlite:ci.db","env":"test"}""")
        assertThat(body["settings"].toString()).doesNotContain("sqlite:app.db")       // secrets never in the saved settings

        val sid = post("/api/projects/$pid/helper/sessions", emptyMap<String, Any>()).json()["id"].asText()
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "How many scores?")).andExpect(status().isOk)
        val turn = engine.lastBody("/helper/sessions/$sid/turn")!!
        assertThat(turn["plugins"].map { it.asText() }).containsExactly("db")
        assertThat(turn["keys"]["db:local"].asText()).contains("sqlite:app.db")
        assertThat(turn["keys"]["github"].asText()).isEqualTo("ghp_exampletoken")
        del("/api/secrets/GITHUB_REPO_TOKEN").andExpect(status().isOk)
    }

    @Test
    fun `Connections lists Databases as its kind, after core's, with its fields`() {
        val kinds = get("/api/connections/kinds").andExpect(status().isOk).json()
        assertThat(kinds.map { it["kind"].asText() }).containsExactly("github", "gitlab", "database")
        val db = kinds.first { it["kind"].asText() == "database" }
        assertThat(db["title"].asText()).isEqualTo("Databases")
        assertThat(db["scope"].asText()).isEqualTo("project")
        assertThat(db["fields"].filter { it["required"].asBoolean() }.map { it["key"].asText() }).containsExactly("name", "url")
        assertThat(db["fields"].first { it["key"].asText() == "url" }["type"].asText()).isEqualTo("secret")
        assertThat(db["fields"].first { it["key"].asText() == "env" }["choices"].map { it.asText() }).containsExactly("local", "test", "staging", "prod")
    }
}
