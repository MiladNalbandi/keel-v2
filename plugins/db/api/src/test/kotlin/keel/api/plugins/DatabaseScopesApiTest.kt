package keel.api.plugins

import com.fasterxml.jackson.databind.JsonNode
import keel.api.connections.SecretService
import keel.api.flow.FlowService
import keel.api.mcp.McpTokens
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * Database connections global or per project (V22): rows from before move into their fields and keep their secrets, a
 * project sees its own and the globals for it (its own wins on the same name), an allow-list and a hide narrow a global
 * one, the password never comes back (not in a list, a test or an error), and an MCP call needs admin for globals and
 * respects each connection's Access. The context is shared, so every global here names its projects (or is removed).
 */
class DatabaseScopesApiTest : ApiTest() {
    @Autowired lateinit var secrets: SecretService
    @Autowired lateinit var jdbc: JdbcTemplate
    @Autowired lateinit var flows: FlowService
    @Autowired lateinit var tokens: McpTokens

    private fun JsonNode.named(name: String): List<JsonNode> = filter { it["name"].asText() == name }

    /** A row as keel 0.18.37 wrote it: no driver or fields, the whole address in the secret store. */
    private fun oldRow(pid: String, id: String, name: String, kind: String, env: String, url: String) {
        jdbc.update("INSERT INTO db_connections(id, project_id, name, kind, env, shown, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            id, pid, name, kind, env, DatabaseService.shownOf(url), "2026-10-01T00:00:00Z")
        secrets.put("db.$id", url)
    }

    @Test
    fun `rows from before move into their fields, keep their secrets and keep working`() {
        val (pid, _) = newProject("dbs-old")
        oldRow(pid, "old00000000pg001", "local", "postgres", "local", "postgres://app:s3cret@localhost:5432/app") // keel:allow-secret
        oldRow(pid, "old00000000my001", "shop", "mysql", "prod", "mysql://root:pw9@db.local:3307/shop") // keel:allow-secret
        oldRow(pid, "old00000000sq001", "file", "sqlite", "test", "sqlite:data/app.db")
        oldRow(pid, "old00000000jd001", "jdbc", "postgres", "staging", "jdbc:postgresql://h:6543/d?user=a&password=q1w2e3&sslmode=require")

        val all = get("/api/projects/$pid/databases").andExpect(status().isOk).json()
        val pg = all.named("local").single()
        assertThat(listOf(pg["driver"], pg["connect_mode"], pg["host"], pg["port"], pg["username"], pg["default_db"]).map { it.asText() })
            .containsExactly("postgres", "host", "localhost", "5432", "app", "app")
        assertThat(pg["source"].asText()).isEqualTo("project")
        assertThat(pg["has_password"].asBoolean()).isTrue()
        assertThat(pg["url"].asText()).isEqualTo("postgres://app@localhost:5432/app")
        assertThat(pg["agent_access"].asText()).isEqualTo("write_approve")       // what keel_db_change did on a local database
        val my = all.named("shop").single()
        assertThat(listOf(my["driver"], my["host"], my["port"], my["username"], my["default_db"]).map { it.asText() })
            .containsExactly("mysql", "db.local", "3307", "root", "shop")
        assertThat(my["agent_access"].asText()).isEqualTo("read")                // prod: read only
        val file = all.named("file").single()
        assertThat(file["driver"].asText()).isEqualTo("sqlite")
        assertThat(file["default_db"].asText()).isEqualTo("data/app.db")
        val jd = all.named("jdbc").single()
        assertThat(jd["connect_mode"].asText()).isEqualTo("url")
        assertThat(jd["url_override"].asText()).isEqualTo("postgres://a@h:6543/d?sslmode=require")
        assertThat(all.toString()).doesNotContain("s3cret", "pw9", "q1w2e3")

        // the secrets are as they were, and a flow still gets them
        assertThat(secrets.get("db.old00000000pg001")).isEqualTo("postgres://app:s3cret@localhost:5432/app") // keel:allow-secret
        put("/api/projects/$pid/plugins/db", mapOf("enabled" to true)).andExpect(status().isOk)
        val keys = flows.contributedKeys(pid)
        assertThat(keys["db:local"]).contains("postgres://app:s3cret@localhost:5432/app") // keel:allow-secret
        assertThat(keys["db:jdbc"]).contains("password=q1w2e3")
        assertThat(keys["db:shop"]).contains("\"agent_access\":\"read\"", "\"scope\":\"project\"")

        // an edit of the note only keeps the address and its password
        val id = pg["id"].asText()
        put("/api/projects/$pid/databases/$id", mapOf("note" to "the app's own")).andExpect(status().isOk)
        assertThat(secrets.get("db.$id")).isEqualTo("postgres://app:s3cret@localhost:5432/app") // keel:allow-secret
        // a new port rebuilds the address with the kept password
        put("/api/projects/$pid/databases/$id", mapOf("port" to 5433)).andExpect(status().isOk)
        assertThat(secrets.get("db.$id")).isEqualTo("postgres://app:s3cret@localhost:5433/app") // keel:allow-secret
    }

    @Test
    fun `a project sees its own and the globals for it, its own wins, and an allow-list and a hide narrow a global`() {
        val (a, _) = newProject("dbs-a")
        val (b, _) = newProject("dbs-b")
        val (c, _) = newProject("dbs-c")
        listOf(a, b, c).forEach { put("/api/projects/$it/plugins/db", mapOf("enabled" to true)).andExpect(status().isOk) }
        val g = post("/api/databases", mapOf("name" to "shared", "env" to "test", "driver" to "postgres", "host" to "pg.team", "port" to 5432,
            "username" to "team", "password" to "teamPW77", "default_db" to "app", "projects" to listOf(a, b))).andExpect(status().isOk).json()
        val gid = g["id"].asText()
        try {
            assertThat(g["source"].asText()).isEqualTo("global")
            assertThat(g["projects"].map { it.asText() }).containsExactlyInAnyOrder(a, b)
            assertThat(g.toString()).doesNotContain("teamPW77")
            assertThat(secrets.get("db.$gid")).isEqualTo("postgres://team:teamPW77@pg.team:5432/app") // keel:allow-secret
            assertThat(get("/api/databases").json().map { it["id"].asText() }).contains(gid)

            // a sees it through keel 0.15's routes too; c is not on its list
            assertThat(get("/api/projects/$a/db/connections").json().named("shared").single()["scope"].asText()).isEqualTo("global")
            assertThat(get("/api/projects/$c/databases").json().named("shared")).isEmpty()
            assertThat(get("/api/projects/$c/db/connections").json().named("shared")).isEmpty()

            // a's own "shared" wins inside a: the global one shows as overridden
            post("/api/projects/$a/databases", mapOf("name" to "shared", "driver" to "sqlite", "default_db" to "a.db")).andExpect(status().isOk)
            val seen = get("/api/projects/$a/databases").json().named("shared")
            assertThat(seen.map { it["source"].asText() to it["overridden"].asBoolean() })
                .containsExactlyInAnyOrder("project" to false, "global" to true)
            assertThat(get("/api/projects/$a/db/connections").json().named("shared").map { it["scope"].asText() }).containsExactly("project")
            put("/api/projects/$a/plugins/db", mapOf("enabled" to true)).andExpect(status().isOk)
            assertThat(flows.contributedKeys(a)["db:shared"]).contains("sqlite:a.db").doesNotContain("pg.team")

            // b hides it for itself, and turns it on again
            put("/api/projects/$b/databases/$gid/hidden", mapOf("hidden" to true)).andExpect(status().isOk)
            assertThat(get("/api/projects/$b/db/connections").json().named("shared")).isEmpty()
            assertThat(get("/api/projects/$b/databases").json().named("shared").single()["hidden"].asBoolean()).isTrue()
            put("/api/projects/$b/databases/$gid/hidden", mapOf("hidden" to false)).andExpect(status().isOk)
            assertThat(get("/api/projects/$b/db/connections").json().named("shared")).hasSize(1)

            // a project cannot change or remove a global one by its own routes
            put("/api/projects/$b/databases/$gid", mapOf("note" to "x")).andExpect(status().isConflict)
            delete("/api/projects/$b/db/connections/shared").andExpect(status().isConflict)
            post("/api/projects/$b/databases", mapOf("name" to "x", "scope" to "global", "driver" to "sqlite", "default_db" to "x.db"))
                .andExpect(status().isForbidden)

            // moved to project c only: a and b lose it, c has it as its own
            put("/api/databases/$gid", mapOf("scope" to "project", "project" to c)).andExpect(status().isOk)
            assertThat(get("/api/projects/$b/databases").json().named("shared")).isEmpty()
            assertThat(get("/api/projects/$c/databases").json().named("shared").single()["source"].asText()).isEqualTo("project")
            assertThat(secrets.get("db.$gid")).contains("teamPW77")
        } finally {
            delete("/api/databases/$gid")
        }
        assertThat(secrets.get("db.$gid")).isNull()
    }

    @Test
    fun `the password never comes back, not in a test, a list of databases or an error`() {
        val (pid, _) = newProject("dbs-pw")
        val made = post("/api/projects/$pid/databases", mapOf("name" to "LOC 8.4", "env" to "local", "driver" to "mysql", "host" to "localhost",
            "port" to 10109, "username" to "root", "password" to "Sup3rSecret", "default_db" to "carrier_router")).andExpect(status().isOk).json()
        val id = made["id"].asText()
        assertThat(made["url"].asText()).isEqualTo("mysql://root@localhost:10109/carrier_router")
        assertThat(made["shown"].asText()).isEqualTo("mysql://root:•••@localhost:10109/carrier_router")

        val t = post("/api/projects/$pid/databases/$id/test").andExpect(status().isOk).json()
        assertThat(t["test"]["ok"].asBoolean()).isTrue()
        assertThat(t["test"]["server"].asText()).isEqualTo("PostgreSQL 16.4")
        assertThat(t["test"]["tables"].asInt()).isEqualTo(23)
        assertThat(t["test"]["ms"].asLong()).isEqualTo(12)
        assertThat(engine.lastBody("/plugins/db/test")!!["connection"]["url"].asText()).contains("Sup3rSecret")   // the engine, memory only

        // the editor's draft: the kept password is used, not returned
        val draft = post("/api/projects/$pid/databases/test", mapOf("id" to id, "port" to 10110)).andExpect(status().isOk).json()
        assertThat(draft["ok"].asBoolean()).isTrue()
        assertThat(engine.lastBody("/plugins/db/test")!!["connection"]["url"].asText()).isEqualTo("mysql://root:Sup3rSecret@localhost:10110/carrier_router") // keel:allow-secret
        val dbs = post("/api/projects/$pid/databases/server-databases", mapOf("id" to id)).andExpect(status().isOk).json()
        assertThat(dbs["databases"].map { it["name"].asText() }).containsExactly("app", "scores")

        // an engine refusal that quotes the address comes back without the password
        val bad = post("/api/projects/$pid/databases/server-databases", mapOf("driver" to "mysql", "host" to "h", "username" to "u",
            "password" to "wrongPass1", "name" to "x")).andExpect(status().isBadGateway).json()
        assertThat(bad.toString()).doesNotContain("wrongPass1").contains("mysql://u:•••@h/")
        val badPg = post("/api/projects/$pid/databases/test", mapOf("driver" to "postgres", "host" to "h", "username" to "u",
            "password" to "wrong-one", "name" to "y")).andExpect(status().isOk).json()
        assertThat(badPg["ok"].asBoolean()).isFalse()
        assertThat(badPg.toString()).doesNotContain("wrong-one")

        val everything = listOf(get("/api/projects/$pid/databases").json(), get("/api/databases/$id").json(),
            get("/api/projects/$pid/db/connections").json(), t, draft).joinToString()
        assertThat(everything).doesNotContain("Sup3rSecret")
    }

    @Test
    fun `keep the password for this session or never, or read the address from the project's dot env`() {
        val (pid, root) = newProject("dbs-keep", mapOf("README.md" to "# x\n", ".env" to "# local\nexport DATABASE_URL=\"postgres://e:envPW1@envhost:5432/e\"\n")) // keel:allow-secret
        put("/api/projects/$pid/plugins/db", mapOf("enabled" to true)).andExpect(status().isOk)
        val s = post("/api/projects/$pid/databases", mapOf("name" to "sess", "driver" to "postgres", "host" to "h", "username" to "u",
            "password" to "sessPW1", "keep_password" to "session")).andExpect(status().isOk).json()
        assertThat(secrets.get("db.${s["id"].asText()}")).isNull()
        assertThat(s["has_password"].asBoolean()).isTrue()
        val n = post("/api/projects/$pid/databases", mapOf("name" to "never", "driver" to "postgres", "host" to "h", "username" to "u",
            "password" to "neverPW1", "keep_password" to "never")).andExpect(status().isOk).json()
        assertThat(secrets.get("db.${n["id"].asText()}")).isNull()
        val e = post("/api/projects/$pid/databases", mapOf("name" to "fromenv", "driver" to "postgres", "connect_mode" to "env",
            "env_var" to "DATABASE_URL")).andExpect(status().isOk).json()
        assertThat(e["shown"].asText()).isEqualTo("DATABASE_URL from .env")
        assertThat(secrets.get("db.${e["id"].asText()}")).isNull()

        val keys = flows.contributedKeys(pid)
        assertThat(keys["db:sess"]).contains("sessPW1")
        assertThat(keys["db:fromenv"]).contains("postgres://e:envPW1@envhost:5432/e") // keel:allow-secret
        assertThat(keys["db:never"]).contains("\"url\":\"\"", "keeps no password").doesNotContain("neverPW1")
        val refused = post("/api/projects/$pid/db/query", mapOf("sql" to "select 1", "connection" to "never")).andExpect(status().isConflict).json()
        assertThat(refused["error"].asText()).contains("keeps no password")
        assertThat(root.resolve(".env")).exists()
    }

    @Test
    fun `an MCP call needs admin for globals and respects each connection's Access`() {
        val (pid, _) = newProject("dbs-mcp")
        val via = mapOf("X-Keel-Via" to "mcp", "X-Keel-Client" to "test", "X-Keel-Tool" to "t")
        val write = via + mapOf("X-Keel-Mcp-Token" to tokens.tokenFor("write"))
        val admin = via + mapOf("X-Keel-Mcp-Token" to tokens.tokenFor("admin"))
        val g = mapOf("name" to "mcp-global", "driver" to "sqlite", "default_db" to "g.db", "projects" to listOf(pid))
        post("/api/databases", g, write).andExpect(status().isForbidden)
        val made = post("/api/databases", g, admin).andExpect(status().isOk).json()
        try {
            assertThat(get("/api/databases").andExpect(status().isOk).json().map { it["name"].asText() }).contains("mcp-global")
            post("/api/databases/test", g, write).andExpect(status().isForbidden)

            put("/api/projects/$pid/plugins/db", mapOf("enabled" to true)).andExpect(status().isOk)
            // 0.18.64 a project's connection too is admin for MCP (no MCP tool adds one; a route no list names needs admin)
            val hidden = mapOf("name" to "hidden", "driver" to "sqlite", "default_db" to "h.db", "agent_access" to "none")
            post("/api/projects/$pid/databases", hidden, write).andExpect(status().isForbidden)
            post("/api/projects/$pid/databases", hidden, admin).andExpect(status().isOk)
            post("/api/projects/$pid/databases", mapOf("name" to "ro", "env" to "local", "driver" to "sqlite", "default_db" to "r.db"))
                .andExpect(status().isOk)
            val prod = post("/api/projects/$pid/databases", mapOf("name" to "live", "env" to "prod", "driver" to "sqlite", "default_db" to "p.db",
                "agent_access" to "write_approve")).andExpect(status().isOk).json()
            assertThat(prod["agent_access_effective"].asText()).isEqualTo("read")       // prod: read only for agents, always

            // the person sees all; an MCP client only what agents may use
            assertThat(get("/api/projects/$pid/databases").json().map { it["name"].asText() }).contains("hidden", "ro", "live")
            val listed = mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get("/api/projects/$pid/databases")
                .also { b -> via.forEach { (k, v) -> b.header(k, v) } }).json()
            assertThat(listed.map { it["name"].asText() }).contains("ro", "live", "mcp-global").doesNotContain("hidden")

            post("/api/projects/$pid/db/query", mapOf("sql" to "select 1", "connection" to "hidden", "mask" to true), via).andExpect(status().isForbidden)
            post("/api/projects/$pid/db/query", mapOf("sql" to "select 1", "connection" to "ro", "mask" to true), via).andExpect(status().isOk)
            post("/api/projects/$pid/db/query", mapOf("sql" to "update t set a = 1", "connection" to "ro", "change" to true), write)
                .andExpect(status().isForbidden)                                              // read: no change for agents
            post("/api/projects/$pid/db/query", mapOf("sql" to "update t set a = 1", "connection" to "live", "change" to true), write)
                .andExpect(status().isForbidden)
            // the person's own query is not an agent's
            post("/api/projects/$pid/db/query", mapOf("sql" to "select 1", "connection" to "hidden")).andExpect(status().isOk)
            assertThat(flows.contributedKeys(pid)["db:hidden"]).contains("\"agent_access\":\"none\"")
        } finally {
            delete("/api/databases/${made["id"].asText()}")
        }
    }
}
