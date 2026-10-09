package keel.api

import com.fasterxml.jackson.databind.JsonNode
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.time.Instant

/**
 * Step 4's api (docs/plugins/13-step4-contract.md §6, §8): /api/marketplace and /api/plugins against the stub engine's
 * marketplace, the plugin-install approvals (join, 7 days, deny, approve → install → notification), needs_plugins and
 * the restart routes. The restart's waiting itself is RestartPlanTest.
 */
class MarketplaceApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate

    @BeforeEach
    fun clean() {
        engine.market.reset()
        jdbc.update("UPDATE approvals SET status = 'closed' WHERE kind = 'plugin-install' AND status = 'waiting'")
    }

    @AfterEach
    fun after() {
        engine.threadRefusal = null
        engine.market.reset()
    }

    private fun events(type: String): List<JsonNode> =
        jdbc.queryForList("SELECT data_json FROM events WHERE type = ? ORDER BY id", String::class.java, type).map { mapper.readTree(it) }

    private fun notes(): List<JsonNode> = get("/api/notifications?limit=500").json().toList()

    private fun request(name: String, reason: String, source: String = "keelbot", project: String? = null, version: String? = null) =
        post("/api/plugins/requests", mapOf("name" to name, "reason" to reason, "source" to source, "project" to project, "version" to version))

    // ---------- the catalogs

    @Test
    fun `search, one plugin and refresh go to the engine, and its refusals keep their status, words and fields`() {
        val hits = get("/api/marketplace?q=database&category=code").andExpect(status().isOk).json()
        assertThat(hits["plugins"].map { it["name"].asText() }).containsExactly("db")
        assertThat(hits["sources"][0]["ok"].asBoolean()).isTrue()
        assertThat(hits["categories"].map { it.asText() }).contains("code", "review")
        assertThat(engine.calls.last { it.path == "/marketplace/search" }.query).isEqualTo("q=database&category=code")
        // a search word with any character reaches the engine as it is
        get("/api/marketplace?q=" + java.net.URLEncoder.encode("a&b{c}+d", Charsets.UTF_8)).andExpect(status().isOk)
        val q = engine.calls.last { it.path == "/marketplace/search" }.query!!
        assertThat(java.net.URLDecoder.decode(q.substringAfter("q=").substringBefore("&category"), Charsets.UTF_8)).isEqualTo("a&b{c}+d")

        val db = get("/api/marketplace/db").andExpect(status().isOk).json()
        assertThat(db["title"].asText()).isEqualTo("Database")
        assertThat(db["plan"]["install"].map { it["name"].asText() }).containsExactly("code", "db")
        assertThat(db["needs"]["code"].asText()).isEqualTo(">=1.0.0")
        val nope = get("/api/marketplace/nope").andExpect(status().isNotFound).json()
        assertThat(nope["error"].asText()).isEqualTo("No catalog lists a plugin called nope.")
        assertThat(nope["hint"].asText()).isEqualTo("Search for another word.")
        get("/api/marketplace/Not_A_Name").andExpect(status().isBadRequest)
        post("/api/marketplace/refresh").andExpect(status().isOk)
        assertThat(engine.calls.last().path).isEqualTo("/marketplace/refresh")
        assertThat(post("/api/marketplace/refresh?source=keel").andExpect(status().isOk).json()["sources"].single()["id"].asText()).isEqualTo("keel")
        assertThat(engine.calls.last().query).isEqualTo("source=keel")
        post("/api/marketplace/refresh?source=a/b").andExpect(status().isBadRequest)
    }

    // ---------- what a person does on the Plugins page

    @Test
    fun `install, update, roll back, turn off and remove go to the engine, each an event with who, and an install tells the person`() {
        val done = post("/api/plugins/install", mapOf("name" to "db", "version" to "1.3.0")).andExpect(status().isOk).json()
        assertThat(done["version"].asText()).isEqualTo("1.3.0")
        assertThat(engine.lastBody("/marketplace/install")!!["name"].asText()).isEqualTo("db")
        assertThat(engine.lastBody("/marketplace/install")!!["by"].asText()).isEqualTo("person")
        assertThat(events("plugin.installed").last()["by"].asText()).isEqualTo("person")
        assertThat(events("plugin.installed").last()["version"].asText()).isEqualTo("1.3.0")
        val note = notes().first { it["title"].asText() == "Database is installed: restart keel to use it" }
        assertThat(note["link"].asText()).isEqualTo("/plugins")
        assertThat(note["body"].asText()).contains("1.3.0")

        val list = get("/api/plugins").andExpect(status().isOk).json()
        assertThat(list["plugins"].map { it["name"].asText() }).containsExactly("db")
        assertThat(list["restart"]["pending"].asBoolean()).isTrue()
        assertThat(list["restart"]["scheduled"].asBoolean()).isFalse()
        assertThat(list["restart"]["supervised"].asBoolean()).isFalse()
        assertThat(list["problems"].isArray).isTrue()
        assertThat(get("/api/plugins/installed").json()["plugins"].size()).isEqualTo(1)

        engine.market.refusals["update:db"] = 502 to mapOf("error" to "the sha256 of the download is not the catalog's",
            "hint" to "Try again later.", "step" to "sha256")
        val bad = post("/api/plugins/db/update", mapOf<String, Any>()).andExpect(status().isBadGateway).json()
        assertThat(bad["error"].asText()).isEqualTo("the sha256 of the download is not the catalog's")
        assertThat(bad["hint"].asText()).isEqualTo("Try again later.")
        assertThat(bad["step"].asText()).isEqualTo("sha256")
        engine.market.refusals.clear()

        val updated = post("/api/plugins/db/update", null).andExpect(status().isOk).json()
        assertThat(updated["version"].asText()).isEqualTo("1.4.0")
        assertThat(engine.lastBody("/marketplace/installed/db/update")!!.has("allow_more_permissions")).isFalse()
        assertThat(events("plugin.updated").last()["version"].asText()).isEqualTo("1.4.0")
        assertThat(events("plugin.updated").last()["from"].asText()).isEqualTo("1.3.0")
        assertThat(notes().any { it["title"].asText() == "Database is updated: restart keel to use it" }).isTrue()
        assertThat(post("/api/plugins/db/rollback").andExpect(status().isOk).json()["version"].asText()).isEqualTo("1.3.0")
        assertThat(engine.calls.last { it.path == "/marketplace/installed/db/rollback" }.query).isEqualTo("by=person")
        assertThat(events("plugin.rolled_back").last()["name"].asText()).isEqualTo("db")
        assertThat(notes().any { it["title"].asText() == "Database is back on 1.3.0: restart keel to use it" }).isTrue()

        put("/api/plugins/db", mapOf("on" to false)).andExpect(status().isOk)
        assertThat(engine.lastBody("/marketplace/installed/db")!!["on"].asBoolean()).isFalse()
        assertThat(events("plugin.turned_off").last()["by"].asText()).isEqualTo("person")
        put("/api/plugins/db", mapOf<String, Any>()).andExpect(status().isBadRequest)

        delete("/api/plugins/db?data=maybe").andExpect(status().isBadRequest)
        delete("/api/plugins/db?data=delete").andExpect(status().isOk)
        assertThat(engine.calls.last().query).isEqualTo("data=delete&by=person")
        assertThat(events("plugin.removed").last()["data"].asText()).isEqualTo("delete")
        assertThat(events("plugin.removed").last()["version"].asText()).isEqualTo("1.3.0")
        delete("/api/plugins/db").andExpect(status().isNotFound)          // the engine's 404, passed on
        post("/api/plugins/install", mapOf("name" to "db", "version" to "../1")).andExpect(status().isBadRequest)
    }

    @Test
    fun `a refused install keeps the engine's words, and a file installs as unsigned`() {
        engine.market.refusals["install:hello"] = 422 to mapOf("error" to "the signature does not match the publisher's key",
            "hint" to "Do not install this file. Tell the publisher.", "step" to "signature")
        val bad = post("/api/plugins/install", mapOf("name" to "hello")).andExpect(status().isUnprocessableEntity).json()
        assertThat(bad["error"].asText()).isEqualTo("the signature does not match the publisher's key")
        assertThat(bad["hint"].asText()).isEqualTo("Do not install this file. Tell the publisher.")
        assertThat(bad["step"].asText()).isEqualTo("signature")
        assertThat(events("plugin.installed").none { it["name"].asText() == "hello" && it["source"]?.asText() != "file" }).isTrue()

        post("/api/plugins/install-file", mapOf("path" to " ")).andExpect(status().isBadRequest)
        val file = post("/api/plugins/install-file", mapOf("path" to "/data/hello-0.1.0-beta.1.kplug")).andExpect(status().isOk).json()
        assertThat(file["source"].asText()).isEqualTo("file")
        assertThat(engine.lastBody("/marketplace/install-file")!!["path"].asText()).isEqualTo("/data/hello-0.1.0-beta.1.kplug")
        assertThat(events("plugin.installed").last()["source"].asText()).isEqualTo("file")
        assertThat(events("plugin.installed").last()["name"].asText()).isEqualTo("hello")
        assertThat(events("plugin.installed").last()["version"].asText()).isEqualTo("0.1.0-beta.1")
    }

    @Test
    fun `an install from elsewhere tells the person once, and the api's own install is not told twice`() {
        fun told() = notes().count { it["title"].asText() == "Code is installed: restart keel to use it" }
        val before = told()
        // keel2 plugins install code: only the engine's event says so
        val done = mapOf("type" to "plugin.install.done", "thread_id" to "marketplace", "project_id" to "", "step" to "plugin",
            "at" to Instant.now().toString(), "data" to mapOf("name" to "code", "version" to "1.0.0", "update" to false,
                "installed" to listOf(mapOf("name" to "code", "version" to "1.0.0", "from" to null)), "turned_on" to emptyList<String>()))
        post("/internal/events", listOf(done, done), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        assertThat(notes().count { it["title"].asText() == "code is installed: restart keel to use it" }).isGreaterThanOrEqualTo(1)
        // the api's own install tells at once; the engine's event that follows is only an echo
        post("/api/plugins/install", mapOf("name" to "code")).andExpect(status().isOk)
        assertThat(told()).isEqualTo(before + 1)
        post("/internal/events", listOf(done), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        assertThat(told()).isEqualTo(before + 1)
    }

    @Test
    fun `an update that asks for more permissions waits in the Inbox, and approving it updates with them`() {
        post("/api/plugins/install", mapOf("name" to "db", "version" to "1.3.0")).andExpect(status().isOk)
        engine.market.more["db"] = listOf("+ network: from-connections", "+ workspace: read")
        val refused = post("/api/plugins/db/update", mapOf<String, Any>()).andExpect(status().isConflict).json()
        assertThat(refused["error"].asText()).isEqualTo("Database 1.4.0 asks for more permissions than 1.3.0.")
        assertThat(refused["hint"].asText()).isEqualTo("A request with the new permissions waits in the Inbox: approve it there to update.")
        assertThat(refused["more"].map { it.asText() }).containsExactly("+ network: from-connections", "+ workspace: read")
        assertThat(refused["installed"].asText()).isEqualTo("1.3.0")
        val id = refused["requests"].single().asText()

        val item = get("/api/inbox").json()["items"].first { it["id"].asText() == id }
        assertThat(item["title"].asText()).isEqualTo("Update Database to 1.4.0?")
        assertThat(item["payload"]["more"].map { it.asText() }).containsExactly("+ network: from-connections", "+ workspace: read")
        assertThat(item["payload"]["update"].asBoolean()).isTrue()
        assertThat(item["payload"]["source"].asText()).isEqualTo("keel")
        // a second click joins the same request
        val again = post("/api/plugins/db/update", mapOf<String, Any>()).andExpect(status().isConflict).json()
        assertThat(again["requests"].single().asText()).isEqualTo(id)

        post("/api/approvals/$id/decide", mapOf("decision" to "approve")).andExpect(status().isOk)
        val body = engine.lastBody("/marketplace/installed/db/update")!!
        assertThat(body["allow_more_permissions"].asBoolean()).isTrue()
        assertThat(get("/api/plugins").json()["plugins"].first { it["name"].asText() == "db" }["version"].asText()).isEqualTo("1.4.0")
        assertThat(events("plugin.updated").last()["request"].asText()).isEqualTo(id)
    }

    @Test
    fun `sources, rules and sets are the engine's`() {
        assertThat(get("/api/plugins/sources").json()["sources"][0]["id"].asText()).isEqualTo("keel")
        val bad = put("/api/plugins/sources", mapOf("sources" to listOf(mapOf("id" to "acme", "url" to "http://acme.test/index.json", "key" to "RWQx"))))
            .andExpect(status().isBadRequest).json()
        assertThat(bad["error"].asText()).isEqualTo("A catalog URL must start with https://")
        val two = listOf(engine.market.sources.first(), mapOf("id" to "acme", "title" to "Acme", "url" to "https://acme.test/index.json", "key" to "RWQx", "on" to true))
        assertThat(put("/api/plugins/sources", mapOf("sources" to two)).andExpect(status().isOk).json()["sources"].size()).isEqualTo(2)

        assertThat(get("/api/plugins/rules").json()["agents_may_ask"].asBoolean()).isTrue()
        val rules = put("/api/plugins/rules", mapOf("agents_may_ask" to false, "allow_unverified" to false, "check_daily" to true, "restart_when_idle" to true))
            .andExpect(status().isOk).json()
        assertThat(rules["agents_may_ask"].asBoolean()).isFalse()
        assertThat(rules["restart_when_idle"].asBoolean()).isTrue()
        val sets = get("/api/plugins/sets").json()["sets"]
        assertThat(sets.map { it["id"].asText() }).containsExactly("developer")
        assertThat(sets[0]["missing"].map { it.asText() }).contains("code", "db")
        // the per-project plugins' catalog moved next to them
        assertThat(get("/api/plugins/catalog").json().map { it["name"].asText() }).containsExactly("db", "git", "ci", "review")
    }

    // ---------- agents ask, a person decides

    @Test
    fun `an agent's request is an Inbox card, a second asker joins it, and deny installs nothing`() {
        val (pid, _) = newProject("mkt-ask")
        val first = request("db", "I need to read the sessions table's schema.", project = pid).andExpect(status().isOk).json()
        assertThat(first["joined"].asBoolean()).isFalse()
        assertThat(first["title"].asText()).isEqualTo("Database")
        assertThat(first["version"].asText()).isEqualTo("1.4.0")
        val id = first["id"].asText()

        val item = get("/api/inbox?project=$pid").json()["items"].single()
        assertThat(item["kind"].asText()).isEqualTo("plugin-install")
        assertThat(item["id"].asText()).isEqualTo(id)
        assertThat(item["title"].asText()).isEqualTo("Install Database 1.4.0?")
        assertThat(item["detail"].asText()).isEqualTo("I need to read the sessions table's schema.")
        assertThat(item["options"].map { it.asText() }).containsExactly("approve", "deny")
        val p = item["payload"]
        assertThat(p["trust"].asText()).isEqualTo("code")
        assertThat(p["publisher"].asText()).isEqualTo("keel")
        assertThat(p["verified"].asBoolean()).isTrue()
        assertThat(p["permissions"]["secrets"][0].asText()).isEqualTo("database")
        assertThat(p["needs"].map { it.asText() }).containsExactly("code")
        assertThat(p["installs"].map { it["name"].asText() }).containsExactly("code")
        assertThat(p["source"].asText()).isEqualTo("keelbot")
        assertThat(notes().first { it["title"].asText() == "KeelBot asks to install Database" }["link"].asText()).isEqualTo("/inbox")
        assertThat(get("/api/projects/$pid").json()["waiting"].asInt()).isEqualTo(1)
        // a flow's step that renders an inbox item never sees the payload field on other kinds
        assertThat(get("/api/inbox").json()["items"].filter { it["kind"].asText() != "plugin-install" }.none { it.has("payload") }).isTrue()

        val second = request("db", "The flow fix-login needs read-only SQL.", source = "agent", project = pid).andExpect(status().isOk).json()
        assertThat(second["id"].asText()).isEqualTo(id)
        assertThat(second["joined"].asBoolean()).isTrue()
        val joined = get("/api/inbox?project=$pid").json()["items"].single()
        assertThat(joined["detail"].asText()).contains("sessions table", "fix-login")
        assertThat(joined["payload"]["reasons"].map { it["source"].asText() }).containsExactly("keelbot", "agent")
        assertThat(events("plugin.request.joined").last()["id"].asText()).isEqualTo(id)

        val installs = engine.calls.count { it.path == "/marketplace/install" }
        val denied = post("/api/approvals/$id/decide", mapOf("decision" to "deny", "why" to "not now")).andExpect(status().isOk).json()
        assertThat(denied["status"].asText()).isEqualTo("denied")
        assertThat(engine.calls.count { it.path == "/marketplace/install" }).isEqualTo(installs)
        assertThat(events("plugin.request.denied").last()["why"].asText()).isEqualTo("not now")
        assertThat(get("/api/inbox?project=$pid").json()["items"].size()).isZero()
        assertThat(notes().first { it["title"].asText() == "KeelBot asks to install Database" }["done"].asBoolean()).isTrue()
    }

    @Test
    fun `a request needs a reason, a plugin name, a known project and the rule, and not an installed plugin`() {
        request("db", " ").andExpect(status().isBadRequest)
        request("Bad Name", "x").andExpect(status().isBadRequest)
        request("db", "x", project = "no-such-project").andExpect(status().isNotFound)
        request("db", "x", version = "1.x/../2").andExpect(status().isBadRequest)
        request("nope", "x").andExpect(status().isNotFound)
        // keel would refuse its install: the agent hears why, nobody is asked
        val old = request("old", "I need it.").andExpect(status().isConflict).json()
        assertThat(old["error"].asText()).isEqualTo("Old 0.1.0 was revoked: it sent errors to a wrong host.")
        assertThat(old["hint"].asText()).isEqualTo("Wait for a fixed version.")
        post("/api/plugins/install", mapOf("name" to "code")).andExpect(status().isOk)
        assertThat(request("code", "I need the editor.").andExpect(status().isConflict).json()["error"].asText()).isEqualTo("Code is installed already")

        engine.market.rules["agents_may_ask"] = false
        val off = request("db", "I need it.").andExpect(status().isConflict).json()
        assertThat(off["error"].asText()).isEqualTo("This keel does not let agents ask to install plugins")
        assertThat(off["hint"].asText()).isEqualTo("Ask the person to install it in Control › Plugins.")
    }

    @Test
    fun `approve installs the plugin, tells the person and ends the request, and a refused install keeps it waiting`() {
        engine.market.refusals["install:db"] = 422 to mapOf("error" to "the sha256 of the download is not the catalog's", "hint" to "Try again later.")
        val id = request("db", "I need SQL.").andExpect(status().isOk).json()["id"].asText()
        // keel-wide (no project): in the Inbox under keel's name, and in its total
        val item = get("/api/inbox").json()["items"].first { it["id"].asText() == id }
        assertThat(item["project_name"].asText()).isEqualTo("keel")
        assertThat(get("/api/inbox").json()["projects"].none { it["id"].asText() == "" }).isTrue()
        assertThat(get("/api/inbox/count").json()["count"].asInt()).isGreaterThanOrEqualTo(1)

        val refused = post("/api/approvals/$id/decide", mapOf("decision" to "approve")).andExpect(status().isUnprocessableEntity).json()
        assertThat(refused["error"].asText()).isEqualTo("the sha256 of the download is not the catalog's")
        assertThat(get("/api/approvals?status=waiting").json().map { it["id"].asText() }).contains(id)

        engine.market.refusals.clear()
        val ok = post("/api/approvals/$id/decide", mapOf("decision" to "approve")).andExpect(status().isOk).json()
        assertThat(ok["status"].asText()).isEqualTo("approved")
        assertThat(engine.lastBody("/marketplace/install")!!["name"].asText()).isEqualTo("db")
        assertThat(engine.lastBody("/marketplace/install")!!["version"].asText()).isEqualTo("1.4.0")
        assertThat(events("plugin.installed").last()["request"].asText()).isEqualTo(id)
        assertThat(events("plugin.request.approved").last()["id"].asText()).isEqualTo(id)
        assertThat(notes().any { it["title"].asText() == "Database is installed: restart keel to use it" }).isTrue()
        assertThat(get("/api/plugins").json()["restart"]["pending"].asBoolean()).isTrue()
        // the rule restart_when_idle cannot restart a keel that keel-start does not run: nothing waits
        assertThat(get("/api/plugins").json()["restart"]["scheduled"].asBoolean()).isFalse()
    }

    @Test
    fun `a request waits 7 days, then it ends, and the next asker opens a new one`() {
        val id = request("db", "I need SQL.").andExpect(status().isOk).json()["id"].asText()
        jdbc.update("UPDATE approvals SET created_at = ? WHERE id = ?", Instant.now().minusSeconds(8 * 86400).toString(), id)
        assertThat(get("/api/approvals?status=waiting").json().none { it["id"].asText() == id }).isTrue()
        val ended = get("/api/approvals?status=expired").json().first { it["id"].asText() == id }
        assertThat(ended["decided_by"].asText()).isEqualTo("keel")
        assertThat(ended["why"].asText()).isEqualTo("Nobody answered in 7 days, so the request ended.")
        assertThat(events("plugin.request.expired").last()["id"].asText()).isEqualTo(id)
        val next = request("db", "Still need SQL.").andExpect(status().isOk).json()
        assertThat(next["id"].asText()).isNotEqualTo(id)
        assertThat(next["joined"].asBoolean()).isFalse()

        // an answer to one whose time ran out (before keel noticed) ends it too
        jdbc.update("UPDATE approvals SET created_at = ? WHERE id = ?", Instant.now().minusSeconds(8 * 86400).toString(), next["id"].asText())
        val installs = engine.calls.count { it.path == "/marketplace/install" }
        val late = post("/api/approvals/${next["id"].asText()}/decide", mapOf("decision" to "approve")).andExpect(status().isConflict).json()
        assertThat(late["error"].asText()).isEqualTo("This request ended: nobody answered it in 7 days")
        assertThat(jdbc.queryForObject("SELECT status FROM approvals WHERE id = ?", String::class.java, next["id"].asText())).isEqualTo("expired")
        assertThat(engine.calls.count { it.path == "/marketplace/install" }).isEqualTo(installs)
    }

    // ---------- needs_plugins

    @Test
    fun `a workflow that needs a plugin which is not loaded opens a request for it and says so`() {
        val (pid, _) = newProject("mkt-needs")
        engine.threadRefusal = 409 to mapOf("error" to "This workflow needs Database.", "hint" to "Install it in Control › Plugins, then restart keel.",
            "missing" to listOf("db", "nope"), "workflow" to "feature")
        val res = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Scores"))
            .andExpect(status().isConflict).json()
        assertThat(res["error"].asText()).isEqualTo("This workflow needs Database.")
        assertThat(res["missing"].map { it.asText() }).containsExactly("db", "nope")
        assertThat(res["hint"].asText()).contains("Inbox")
        val ids = res["requests"].map { it.asText() }
        assertThat(ids).hasSize(1)                                  // nope is in no catalog: nothing to ask for
        val item = get("/api/inbox?project=$pid").json()["items"].single { it["kind"].asText() == "plugin-install" }
        assertThat(item["id"].asText()).isEqualTo(ids.single())
        assertThat(item["detail"].asText()).isEqualTo("the workflow feature (keel) needs it")
        assertThat(item["payload"]["source"].asText()).isEqualTo("workflow")
        // the rule is for agents: a person's own start still asks
        engine.market.rules["agents_may_ask"] = false
        val again = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Scores")).andExpect(status().isConflict).json()
        assertThat(again["requests"].map { it.asText() }).containsExactly(ids.single())
        // any other refusal of the engine stays as it was
        engine.threadRefusal = 409 to mapOf("error" to "Something else.")
        val other = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Scores")).andExpect(status().isConflict).json()
        assertThat(other.has("requests")).isFalse()
        assertThat(other.fieldNames().asSequence().toList()).containsExactly("error", "hint")
    }

    // ---------- restart

    @Test
    fun `restart now or when idle needs keel-start`() {
        val now = post("/api/plugins/restart", mapOf("now" to true)).andExpect(status().isConflict).json()
        assertThat(now["hint"].asText()).isEqualTo("Restart keel by hand: keel2 restart")
        post("/api/plugins/restart", mapOf<String, Any>()).andExpect(status().isConflict)
        post("/api/plugins/restart").andExpect(status().isConflict)
        assertThat(get("/api/plugins").json()["restart"]["scheduled"].asBoolean()).isFalse()
    }
}
