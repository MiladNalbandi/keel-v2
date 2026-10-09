package keel.api

import keel.api.approvals.Approval
import keel.api.approvals.ApprovalHandler
import keel.api.approvals.ApprovalService
import keel.api.budget.ProviderUsageStore
import keel.api.engine.EngineClient
import keel.api.events.AgentCalls
import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.events.EventHub
import keel.api.events.EventService
import keel.api.notifications.NotificationService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.ObjectProvider
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.context.ApplicationEventPublisher
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.request
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.time.Instant
import java.util.stream.Stream

/** Step 2 approvals (docs/plugins/09-step2-contract.md §1, §2, §4): the table, its routes, the Inbox source, event handlers. */
class ApprovalsApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate
    @Autowired lateinit var approvals: ApprovalService
    @Autowired lateinit var events: EventService
    @Autowired lateinit var hub: EventHub

    private fun ev(type: String, pid: String, data: Map<String, Any?>, tid: String = "h_a") =
        mapOf("type" to type, "thread_id" to tid, "project_id" to pid, "step" to "approval", "at" to Instant.now().toString(), "data" to data)

    private fun send(vararg events: Map<String, Any?>) =
        post("/internal/events", events.toList(), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)

    private fun question(id: String, pid: String, command: String = "npm install left-pad", session: String = "h_a",
                         at: String = Instant.now().toString()) = mapOf(
        "id" to id, "kind" to "command", "project" to pid, "title" to "Fix the totals", "command" to command, "path" to "",
        "source" to "keelbot", "thread_id" to "t-1", "session" to session, "at" to at)

    @Test
    fun `an engine question becomes a row, an Inbox card and a waiting count, and its answer goes to the engine`() {
        val (pid, _) = newProject("appr-ask")
        val q = question("p_a00000000001", pid)
        engine.helperQuestions += q                          // the engine has it in memory, and says so in an event
        send(ev("approval.asked", pid, q))

        val row = get("/api/approvals?status=waiting&project=$pid").andExpect(status().isOk).json().single()
        assertThat(row["id"].asText()).isEqualTo("p_a00000000001")
        assertThat(row["kind"].asText()).isEqualTo("command")
        assertThat(row["source"].asText()).isEqualTo("keelbot")
        assertThat(row["title"].asText()).isEqualTo("Fix the totals")
        assertThat(row["detail"].asText()).isEqualTo("npm install left-pad")
        assertThat(row["requested_by"].asText()).isEqualTo("h_a")
        assertThat(row["payload"]["thread_id"].asText()).isEqualTo("t-1")

        val item = get("/api/inbox?project=$pid").json()["items"].single()
        assertThat(item["kind"].asText()).isEqualTo("permission")
        assertThat(item["title"].asText()).isEqualTo("KeelBot asks to run a command")
        assertThat(item["flow"].asText()).isEqualTo("Fix the totals")
        assertThat(item["project_name"].asText()).isEqualTo("appr-ask")
        assertThat(item["options"].map { it.asText() }).containsExactly("once", "always", "deny")
        assertThat(item["permission"]["session"].asText()).isEqualTo("h_a")
        assertThat(get("/api/inbox/count").json()["projects"][pid].asInt()).isEqualTo(1)
        assertThat(get("/api/projects/$pid").json()["waiting"].asInt()).isEqualTo(1)

        post("/api/approvals/p_a00000000001/decide", mapOf("decision" to "maybe")).andExpect(status().isBadRequest)
        post("/api/approvals/p_nope00000000/decide", mapOf("decision" to "once")).andExpect(status().isNotFound)
        val done = post("/api/approvals/p_a00000000001/decide", mapOf("decision" to "always", "why" to " fine "))
            .andExpect(status().isOk).json()
        assertThat(done["status"].asText()).isEqualTo("approved")
        assertThat(done["decision"].asText()).isEqualTo("always")
        assertThat(done["decided_by"].asText()).isEqualTo("person")
        assertThat(engine.lastBody("/approvals/p_a00000000001")!!["decision"].asText()).isEqualTo("always")
        assertThat(engine.lastBody("/approvals/p_a00000000001")!!["why"].asText()).isEqualTo("fine")
        post("/api/approvals/p_a00000000001/decide", mapOf("decision" to "once")).andExpect(status().isNotFound)   // answered

        assertThat(get("/api/inbox?project=$pid").json()["items"].size()).isZero()
        assertThat(get("/api/projects/$pid").json()["waiting"].asInt()).isZero()
        assertThat(get("/api/approvals?project=$pid").json().map { it["status"].asText() }).containsExactly("approved")
        get("/api/approvals?status=maybe").andExpect(status().isBadRequest)
    }

    @Test
    fun `the engine's answered event closes the row - a deny, a time out, an asker that ended`() {
        val (pid, _) = newProject("appr-answered")
        for (id in listOf("p_b00000000001", "p_b00000000002", "p_b00000000003")) send(ev("approval.asked", pid, question(id, pid)))
        engine.helperQuestions += question("p_b00000000001", pid)      // only the first one still waits in the engine
        engine.helperQuestions += question("p_b00000000002", pid)
        engine.helperQuestions += question("p_b00000000003", pid)
        assertThat(approvals.waitingCount(pid)).isEqualTo(3)
        fun answered(id: String, decision: String?, why: String, status: String, by: String) {
            engine.helperQuestions.removeIf { it["id"] == id }
            send(ev("approval.answered", pid, question(id, pid) + mapOf("decision" to decision, "why" to why, "status" to status, "by" to by)))
        }
        answered("p_b00000000001", "deny", "keel pushes, not you", "denied", "person")
        answered("p_b00000000002", null, "Nobody answered in 10 minutes, so the command did not run.", "expired", "keel")
        answered("p_b00000000003", null, "The person stopped KeelBot.", "closed", "keel")
        val rows = get("/api/approvals?project=$pid").json().associateBy { it["id"].asText() }
        assertThat(rows["p_b00000000001"]!!["status"].asText()).isEqualTo("denied")
        assertThat(rows["p_b00000000001"]!!["why"].asText()).isEqualTo("keel pushes, not you")
        assertThat(rows["p_b00000000002"]!!["status"].asText()).isEqualTo("expired")
        assertThat(rows["p_b00000000002"]!!["decided_by"].asText()).isEqualTo("keel")
        assertThat(rows["p_b00000000003"]!!["status"].asText()).isEqualTo("closed")
        assertThat(get("/api/inbox?project=$pid").json()["items"].size()).isZero()
        // keel2 mcp's poll reads the answer from the row once it ended
        assertThat(get("/api/plugins/asks/p_b00000000001").json()["decision"].asText()).isEqualTo("deny")
        assertThat(get("/api/plugins/asks/p_b00000000001").json()["why"].asText()).isEqualTo("keel pushes, not you")
    }

    @Test
    fun `the table follows the engine - a question it missed is added, one the engine lost is closed`() {
        val (pid, _) = newProject("appr-sync")
        // asked while the api was down: no event, but the engine has it
        engine.helperQuestions += question("p_c00000000001", pid)
        // the engine restarted: its old question is gone, and nothing said so
        send(ev("approval.asked", pid, question("p_c00000000002", pid, at = Instant.now().minusSeconds(120).toString())))
        // asked a moment ago: not in the engine's answer yet, so it is not closed
        send(ev("approval.asked", pid, question("p_c00000000003", pid)))
        val waiting = get("/api/approvals?status=waiting&project=$pid").json().map { it["id"].asText() }
        assertThat(waiting).containsExactlyInAnyOrder("p_c00000000001", "p_c00000000003")
        assertThat(approvals.find("p_c00000000002")!!.status).isEqualTo("closed")
        // answering a question the engine forgot: 404, and the row ends
        post("/api/approvals/p_c00000000003/decide", mapOf("decision" to "once")).andExpect(status().isNotFound)
        assertThat(approvals.find("p_c00000000003")!!.status).isEqualTo("closed")
        engine.helperQuestions.removeIf { it["id"] == "p_c00000000001" }
    }

    @Test
    fun `keel2 mcp's poll delegates to approvals`() {
        val (pid, _) = newProject("appr-old")
        val q = question("p_d00000000001", pid)
        engine.helperQuestions += q
        send(ev("approval.asked", pid, q))
        assertThat(get("/api/plugins/asks/p_d00000000001").json()["waiting"].asBoolean()).isTrue()
        post("/api/approvals/p_d00000000001/decide", mapOf("decision" to "once")).andExpect(status().isOk)
        assertThat(engine.lastBody("/approvals/p_d00000000001")!!["decision"].asText()).isEqualTo("once")
        assertThat(get("/api/plugins/asks/p_d00000000001").json()["decision"].asText()).isEqualTo("allow")
        // KeelBot's old answer route is its plugin's (plugins/keelbot/api tests it): core has none
        get("/api/projects/$pid/helper/permissions").andExpect(status().isNotFound)
    }

    @Test
    fun `keel2 mcp's question notifies the person, and its answer clears the notification`() {
        val (pid, _) = newProject("appr-mcp-note")
        val q = mapOf("id" to "p_g00000000001", "kind" to "plugin", "project" to pid, "title" to "Claude Code: push the branch?",
            "command" to "push the branch", "source" to "mcp", "session" to "mcp")
        fun hev(type: String, data: Map<String, Any?>) = mapOf("type" to type, "thread_id" to "mcp", "project_id" to pid,
            "step" to "helper", "at" to Instant.now().toString(), "data" to data)
        send(hev("helper.permission", q))
        val note = get("/api/notifications?limit=500").json().first { it["title"].asText() == "Claude Code: push the branch?" }
        assertThat(note["body"].asText()).isEqualTo("push the branch")
        assertThat(note["link"].asText()).isEqualTo("/inbox")
        send(hev("helper.permission.answered", mapOf("id" to "p_g00000000001", "decision" to "allow")))
        val done = get("/api/notifications?limit=500").json().first { it["title"].asText() == "Claude Code: push the branch?" }
        assertThat(done["done"].asBoolean()).isTrue()
    }

    @Test
    fun `keel2 mcp's acting tool is a permission card with its own title`() {
        val (pid, _) = newProject("appr-mcp")
        val q = mapOf("id" to "p_e00000000001", "kind" to "plugin", "project" to pid, "title" to "Claude Code: push the branch?",
            "command" to "push the branch", "path" to "", "source" to "mcp", "thread_id" to null, "session" to "mcp",
            "at" to Instant.now().toString())
        engine.helperQuestions += q
        send(ev("approval.asked", pid, q, tid = "mcp"))
        val item = get("/api/inbox?project=$pid").json()["items"].single()
        assertThat(item["title"].asText()).isEqualTo("Claude Code: push the branch?")
        assertThat(item["permission"]["session"].asText()).isEqualTo("mcp")
        post("/api/approvals/p_e00000000001/decide", mapOf("decision" to "deny", "why" to "not today")).andExpect(status().isOk)
        assertThat(get("/api/plugins/asks/p_e00000000001").json()["decision"].asText()).isEqualTo("deny")
    }

    @Test
    fun `approval events reach the web on the one generic channel, KeelBot's keep their own name`() {
        val (pid, _) = newProject("appr-sse")
        val stream = mvc.perform(MockMvcRequestBuilders.get("/api/events?project=$pid")).andExpect(request().asyncStarted()).andReturn()
        send(ev("approval.asked", pid, question("p_f00000000001", pid)), ev("helper.permission", pid, question("p_f00000000001", pid)))
        val text = stream.response.contentAsString
        assertThat(text).contains("event:engine", "\"type\":\"approval.asked\"", "event:helper.permission")
        assertThat(text).doesNotContain("event:approval.asked")
        assertThat(EventHub.channelOf("gate.waiting")).isEqualTo("gate.waiting")
        assertThat(EventHub.channelOf("plugin.db.query")).isEqualTo(EventHub.ENGINE)
    }

    // ---------- the api's own approvals (a kind with a handler) and event handlers by prefix, without Spring wiring

    private class Provider<T : Any>(private val beans: List<T>) : ObjectProvider<T> {
        override fun getObject(vararg args: Any?): T = beans.first()
        override fun getObject(): T = beans.first()
        override fun getIfAvailable(): T? = beans.firstOrNull()
        override fun getIfUnique(): T? = beans.singleOrNull()
        override fun stream(): Stream<T> = beans.stream()
        override fun orderedStream(): Stream<T> = beans.stream()
    }

    @Test
    fun `an approval the api asks itself is decided by its handler, never by the engine`(@Autowired client: EngineClient) {
        val (pid, _) = newProject("appr-own")
        val heard = mutableListOf<String>()
        val handler = object : ApprovalHandler {
            override val kind = "plugin-install"
            override fun decided(approval: Approval, decision: String, why: String) {
                heard += "${approval.id}:$decision:$why"
            }
        }
        val own = ApprovalService(jdbc, client, mapper, hub, Provider(listOf(handler)))
        val a = own.create("plugin-install", pid, "Install the Database plugin?", "keel-db 1.2.0", mapOf("plugin" to "db"), requestedBy = "marketplace")
        assertThat(a.id).startsWith("a_")
        assertThat(a.status).isEqualTo("waiting")
        assertThat(own.engineOwned(a)).isFalse()
        val calls = engine.calls.size
        org.junit.jupiter.api.assertThrows<keel.api.common.BadRequest> { own.decide(a.id, "once") }
        val done = own.decide(a.id, "approve", "yes please")
        assertThat(done.status).isEqualTo("approved")
        assertThat(heard).containsExactly("${a.id}:approve:yes please")
        assertThat(engine.calls.drop(calls).none { it.path.startsWith("/approvals/") }).isTrue()
        assertThat(own.asked(a.id)["decision"].asText()).isEqualTo("allow")
        org.junit.jupiter.api.assertThrows<IllegalArgumentException> { own.create("nobody-decides", pid, "?") }
    }

    @Test
    fun `engine events go to the handlers whose prefix matches, and a broken one stops nobody`(
        @Autowired notifications: NotificationService, @Autowired usage: ProviderUsageStore,
        @Autowired publisher: ApplicationEventPublisher, @Autowired calls: AgentCalls,
    ) {
        val seen = mutableListOf<String>()
        val broken = object : EngineEventHandler {
            override val prefix = "plugin."
            override fun handle(event: EngineEvent) = throw IllegalStateException("broken")
        }
        val mine = object : EngineEventHandler {
            override val prefix = "plugin.db."
            override fun handle(event: EngineEvent) {
                seen += "${event.type}@${event.at != null}"
            }
        }
        val service = EventService(jdbc, notifications, hub, usage, publisher, calls, Provider(listOf(broken, mine)))
        val stored = service.ingest(listOf(
            EngineEvent("plugin.db.query", "t-h", "p-h", data = mapOf("sql" to "select 1")),
            EngineEvent("plugin.git.push", "t-h", "p-h"),
            EngineEvent("thread.started", "t-h", "p-h", data = mapOf("workflow" to "fix", "title" to "Handlers")),
        ))
        assertThat(stored).isEqualTo(3)
        assertThat(seen).containsExactly("plugin.db.query@true")           // stored first, with its time
        assertThat(jdbc.queryForObject("SELECT status FROM threads WHERE id = 't-h'", String::class.java)).isEqualTo("running")
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM events WHERE thread_id = 't-h'", Int::class.java)).isEqualTo(3)
    }
}
