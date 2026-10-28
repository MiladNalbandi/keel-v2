package keel.api.supervisor

import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import keel.api.common.KeelProperties
import keel.api.pluginhost.PluginHost
import keel.api.pluginhost.PluginsReloaded
import org.springframework.context.ApplicationEventPublisher
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.nio.file.Path

/** The Supervisor plugin's api part: its routes pass through for the flow's own project only, its turns are agent calls,
 *  and only the escalations the person did not expect make a notification. */
class SupervisorApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate
    @Autowired lateinit var host: PluginHost
    @Autowired lateinit var props: KeelProperties
    @Autowired lateinit var events: ApplicationEventPublisher

    private fun ev(pid: String, type: String, data: Map<String, Any?>, call: String? = null) = mapOf("type" to type,
        "thread_id" to "t_sup", "project_id" to pid, "step" to "g1", "call_id" to call,
        "at" to java.time.Instant.now().toString(), "data" to data)

    private fun flowOf(pid: String) {
        engine.extraRoutes["/threads/t_sup"] = { _ -> 200 to mapOf("thread_id" to "t_sup", "project_id" to pid, "status" to "waiting") }
        engine.extraRoutes["/threads/t_sup/supervisor"] = { body -> 200 to mapOf("thread_id" to "t_sup", "got" to body) }
    }

    @Test
    fun `a flow's settings pass through, with only the keys the Supervisor takes`() {
        val (pid, _) = newProject("sup-settings")
        flowOf(pid)
        post("/api/projects/$pid/flows/t_sup/supervisor", mapOf("on" to true, "mode" to "act", "root" to "/etc"))
            .andExpect(status().isOk)
        val sent = engine.lastBody("/threads/t_sup/supervisor")!!
        assertThat(sent["on"].asBoolean()).isTrue()
        assertThat(sent["mode"].asText()).isEqualTo("act")
        assertThat(sent.has("root")).isFalse()
        get("/api/projects/$pid/flows/t_sup/supervisor").andExpect(status().isOk)
    }

    @Test
    fun `0_18_64 policy rung may needs the words that say commands run without you`() {
        val (pid, _) = newProject("sup-rung")
        flowOf(pid)
        val calls = engine.calls.count { it.path == "/threads/t_sup/supervisor" && it.method == "POST" }
        val res = post("/api/projects/$pid/flows/t_sup/supervisor", mapOf("mode" to "act", "policy" to mapOf("rung" to "may")))
            .andExpect(status().isBadRequest).json()
        assertThat(res["error"].asText()).contains("without you")
        assertThat(engine.calls.count { it.path == "/threads/t_sup/supervisor" && it.method == "POST" }).isEqualTo(calls)
        post("/api/projects/$pid/flows/t_sup/supervisor", mapOf("mode" to "act", "policy" to mapOf("rung" to "may"),
            "i_understand" to SupervisorController.RUNG_OK)).andExpect(status().isOk)
        val sent = engine.lastBody("/threads/t_sup/supervisor")!!
        assertThat(sent["policy"]["rung"].asText()).isEqualTo("may")
        assertThat(sent.has("i_understand")).isFalse()
        // another kind needs no words; an MCP client below admin never changes the policy
        post("/api/projects/$pid/flows/t_sup/supervisor", mapOf("policy" to mapOf("spec" to "may"))).andExpect(status().isOk)
        val write = post("/api/mcp/token", mapOf("level" to "write"), mapOf("X-Keel-Token" to TOKEN)).json()["token"].asText()
        val mcp = mapOf("X-Keel-Via" to "mcp", "X-Keel-Mcp-Token" to write)
        post("/api/projects/$pid/flows/t_sup/supervisor", mapOf("policy" to mapOf("spec" to "may")), mcp).andExpect(status().isForbidden)
        post("/api/projects/$pid/flows/t_sup/supervisor", mapOf("mode" to "act"), mcp).andExpect(status().isOk)
        // keel_supervisor_set's routes stay at write; any other non-GET route of the plugin needs admin
        post("/api/projects/$pid/supervisor/flow", mapOf("mode" to "act"), mcp).andExpect(status().isForbidden)
    }

    @Test
    fun `another project's flow is a 404`() {
        val (pid, _) = newProject("sup-mine")
        flowOf("someone-else")
        get("/api/projects/$pid/flows/t_sup/supervisor").andExpect(status().isNotFound)
        post("/api/projects/$pid/flows/t_sup/supervisor", mapOf("on" to true)).andExpect(status().isNotFound)
        assertThat(engine.lastBody("/threads/t_sup/supervisor")).isNull()
    }

    @Test
    fun `the audit log is asked for this project`() {
        val (pid, _) = newProject("sup-log")
        engine.extraRoutes["/supervisor/decisions"] = { _ -> 200 to mapOf("decisions" to emptyList<Any>()) }
        get("/api/projects/$pid/supervisor/decisions?threadId=t_sup&limit=9999").andExpect(status().isOk)
        val call = engine.calls.last { it.path == "/supervisor/decisions" }
        assertThat(call.query).contains("project_id=$pid").contains("thread_id=t_sup").contains("limit=500")
    }

    @Test
    fun `its turns are agent calls counted in the budget`() {
        val (pid, _) = newProject("sup-calls")
        post("/internal/events", listOf(
            ev(pid, "supervisor.started", mapOf("agent" to "supervisor", "provider" to "claude", "model" to "opus", "effort" to "high"), "c-sup-1"),
            ev(pid, "supervisor.finished", mapOf("agent" to "supervisor", "status" to "done", "tokens_in" to 900, "tokens_out" to 100), "c-sup-1"),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        val row = jdbc.queryForMap("SELECT agent, status, tokens_in, effort FROM agent_calls WHERE id = 'c-sup-1'")
        assertThat(row["agent"]).isEqualTo("supervisor")
        assertThat(row["status"]).isEqualTo("done")
        assertThat((row["tokens_in"] as Number).toInt()).isEqualTo(900)
        assertThat(row["effort"]).isEqualTo("high")
    }

    @Test
    fun `an escalation the person did not expect is a notification, the others stay quiet`() {
        val (pid, _) = newProject("sup-notify")
        post("/internal/events", listOf(
            ev(pid, "supervisor.escalated", mapOf("code" to "shadow", "reason" to "shadow mode: it only suggests", "summary" to "x")),
            ev(pid, "supervisor.escalated", mapOf("code" to "low-confidence", "reason" to "it was not sure enough",
                "summary" to "The test checks 404, the spec says 410.")),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        val notes = get("/api/notifications").json().filter { it["title"].asText().startsWith("Supervisor:") }
        assertThat(notes).hasSize(1)
        assertThat(notes[0]["title"].asText()).isEqualTo("Supervisor: it was not sure enough")
        assertThat(notes[0]["body"].asText()).contains("410")
        assertThat(notes[0]["thread_id"].asText()).isEqualTo("t_sup")
    }

    @Test
    fun `the project's flow now is the TUI's flow, and none says so`() {
        val (pid, _) = newProject("sup-now")
        get("/api/projects/$pid/supervisor/flow").andExpect(status().isOk).json().let {
            assertThat(it["thread_id"].isNull).isTrue()
            assertThat(it["note"].asText()).contains("No flow runs")
        }
        engine.nextThreadIds.add("t_now")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Ranks", "allow_fake" to true)).andExpect(status().isOk)
        engine.overrides["t_now"] = mapOf("status" to "waiting", "project_id" to pid)
        engine.extraRoutes["/threads/t_now/supervisor"] = { body -> 200 to mapOf("thread_id" to "t_now", "got" to body) }
        post("/api/projects/$pid/supervisor/flow", mapOf("on" to true, "mode" to "act")).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads/t_now/supervisor")!!["mode"].asText()).isEqualTo("act")
    }

    @Test
    fun `its keel TUI commands pass keel's checks`() {
        val manifest = Path.of(System.getProperty("user.dir")).parent.resolve("plugins/supervisor/keel-plugin.yml")
        val dir = Files.createTempDirectory("tui-plugin-supervisor")
        Files.writeString(dir.resolve("keel-plugin.yml"), Files.readString(manifest))
        val run = Files.createDirectories(props.dataDir.resolve("plugins").resolve("run"))
        val file = run.resolve("resolved.json")
        Files.writeString(file, jacksonObjectMapper().writeValueAsString(mapOf("sdk" to 1, "mode" to "on", "problems" to emptyList<Any>(),
            "plugins" to listOf(mapOf("name" to "supervisor", "title" to "Supervisor", "version" to "0.1.0", "source" to "image", "dir" to dir.toString())))))
        // the engine lists the plugin (its engine part is loaded), so a project can turn it on; PluginsReloaded drops the
        // api's cached catalog
        engine.extraRoutes["/plugins"] = { _ -> 200 to listOf(mapOf("name" to "supervisor", "title" to "Supervisor", "installable" to true,
            "per_project" to true, "actions" to emptyList<Any>())) }
        host.reload()
        events.publishEvent(PluginsReloaded(listOf("supervisor")))
        try {
            val (pid, _) = newProject("sup-tui")
            assertThat(get("/api/tui/plugins?project=$pid").json()["plugins"].none { it["name"].asText() == "supervisor" }).isTrue()   // off here
            put("/api/projects/$pid/plugins/supervisor", mapOf("enabled" to true)).andExpect(status().isOk)
            val body = get("/api/tui/plugins?project=$pid").andExpect(status().isOk).json()
            assertThat(body["problems"]).isEmpty()
            val cmd = body["plugins"].first { it["name"].asText() == "supervisor" }["commands"][0]
            assertThat(cmd["sub"].fieldNames().asSequence().toList()).containsExactly("status", "on", "off", "follow", "keel-on",
                "keel-off", "flow-on", "flow-off", "flow-follow", "shadow", "act", "log")
            assertThat(cmd["sub"]["keel-off"]["call"]["path"].asText()).isEqualTo("/supervisor/switch")
            assertThat(cmd["sub"]["act"]["confirm"].asText()).contains("answer gates")
        } finally {
            engine.extraRoutes.remove("/plugins")
            Files.deleteIfExists(file)
            host.reload()
            events.publishEvent(PluginsReloaded(emptyList()))
        }
    }
}
