package keel.api.slack

import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import keel.api.connections.SecretService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** Two-way Slack against a stand-in: only people on the list act, a bot is never read, an answer takes the Inbox's own path, and some gates stay in keel. */
class SlackApiTest : ApiTest() {
    @Autowired lateinit var slack: SlackService
    @Autowired lateinit var secrets: SecretService

    private lateinit var fake: FakeSlack
    private val json = jacksonObjectMapper()
    private val ann = "UANN1"

    @BeforeEach
    fun link() {
        slack.forget()
        fake = FakeSlack().start()
        post("/api/plugins/slack", mapOf("bot_token" to "xoxb-test", "app_token" to "xapp-test", "api_url" to fake.url, "channel" to "C1",
            "allowed" to listOf(mapOf("id" to ann, "name" to "Ann")))).andExpect(status().isOk)
        eventually { fake.connected }
    }

    @AfterEach
    fun unlink() {
        slack.forget()
        fake.stop()
    }

    private fun eventually(check: () -> Boolean) {
        val until = System.currentTimeMillis() + 8000
        while (!check() && System.currentTimeMillis() < until) Thread.sleep(20)
        assertThat(check()).isTrue()
    }

    private fun quiet() = Thread.sleep(400)
    private fun replies() = fake.sent("chat.postMessage").map { it.body.path("text").asText() }
    private fun lastReply(): String {
        val n = fake.sent("chat.postMessage").size
        return replies().getOrElse(n - 1) { "" }
    }

    private fun waitingFlow(pid: String, tid: String, question: Map<String, Any?>, title: String = "Export invoices") {
        val full = mapOf("step" to "g", "kind" to "gate", "title" to "spec approval", "name" to "spec approval", "detail" to "SECRET CODE", "id" to "q123456", "options" to listOf("approve", "reject")) + question
        engine.extraRoutes["/threads/$tid"] = { _ -> 200 to mapOf("thread_id" to tid, "project_id" to pid, "status" to "waiting", "waiting" to full) }
        engine.extraRoutes["/threads/$tid/resume"] = { _ -> 200 to mapOf("thread_id" to tid, "project_id" to pid, "status" to "running") }
        post("/internal/events", listOf(
            mapOf("type" to "thread.started", "thread_id" to tid, "project_id" to pid, "at" to java.time.Instant.now().toString(), "data" to mapOf("title" to title)),
            mapOf("type" to "gate.waiting", "thread_id" to tid, "project_id" to pid, "step" to "g", "at" to java.time.Instant.now().toString(),
                "data" to mapOf("title" to "spec approval", "detail" to "SECRET CODE", "kind" to "gate", "summary" to listOf("Two criteria")))), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
    }

    private fun answers(tid: String) = engine.calls.filter { it.path == "/threads/$tid/resume" && it.method == "POST" }
    private fun button(tid: String, c: String?, q: String? = "q123456") = json.writeValueAsString(mapOf("t" to tid, "q" to q, "c" to c))

    // ---- the link

    @Test
    fun `a token of the wrong kind or one Slack refuses is not kept, and no token ever comes back`() {
        slack.forget()
        post("/api/plugins/slack", mapOf("bot_token" to "nope", "api_url" to fake.url)).andExpect(status().isBadRequest)
        post("/api/plugins/slack", mapOf("app_token" to "xoxb-wrong", "api_url" to fake.url)).andExpect(status().isBadRequest)
        fake.refuse = "invalid_auth"
        val res = post("/api/plugins/slack", mapOf("bot_token" to "xoxb-bad", "api_url" to fake.url)).andExpect(status().isBadRequest)
        assertThat(res.andReturn().response.contentAsString).contains("invalid_auth").doesNotContain("xoxb-bad")
        assertThat(secrets.has(SlackService.BOT)).isFalse()
        fake.refuse = null
        val ok = post("/api/plugins/slack", mapOf("bot_token" to "xoxb-good-one", "app_token" to "xapp-good-one", "api_url" to fake.url)).andExpect(status().isOk).andReturn().response.contentAsString
        assertThat(ok + get("/api/plugins/slack").andReturn().response.contentAsString).doesNotContain("xoxb-good-one").doesNotContain("xapp-good-one")
        assertThat(secrets.get(SlackService.BOT)).isEqualTo("xoxb-good-one")
    }

    @Test
    fun `the page says what goes to Slack and to the model company, and who may act must be member ids`() {
        val v = get("/api/plugins/slack").json()
        assertThat(v["state"].asText()).isEqualTo("connected")
        assertThat(v["config"]["team_name"].asText()).isEqualTo("Northwind")
        assertThat(v["sends_slack"].asText()).contains("To Slack go")
        assertThat(v["sends_model"].asText()).contains("matched on this computer")
        post("/api/plugins/slack", mapOf("allowed" to listOf(mapOf("id" to "ann@example.com")))).andExpect(status().isBadRequest)
        post("/api/plugins/slack", mapOf("api_url" to "http://example.com/api")).andExpect(status().isBadRequest)
    }

    @Test
    fun `off closes the connection, and with nothing linked nothing is sent`() {
        post("/api/plugins/slack", mapOf("on" to false)).andExpect(status().isOk)
        assertThat(get("/api/plugins/slack").json()["state"].asText()).isEqualTo("off")
        val (pid, _) = newProject("sl-off")
        val before = fake.sent("chat.postMessage").size
        waitingFlow(pid, "t_off", emptyMap())
        quiet()
        assertThat(fake.sent("chat.postMessage")).hasSize(before)
    }

    // ---- who may act

    @Test
    fun `keel acknowledges every envelope at once`() {
        val id = fake.dm(ann, "help")
        eventually { id in fake.acks }
    }

    @Test
    fun `someone not on the list is refused, a bot and a subtype are not even answered, another workspace is refused`() {
        val (pid, _) = newProject("sl-who")
        fake.dm("UOTHER", "task in sl-who: Steal the show")
        eventually { lastReply().contains("not on this keel's list") }
        var n = replies().size
        fake.dm(ann, "task in sl-who: From a bot", mapOf("bot_id" to "B1"))
        fake.dm(ann, "task in sl-who: An edit", mapOf("subtype" to "message_changed"))
        fake.dm("UBOT", "task in sl-who: Myself")
        quiet()
        assertThat(replies()).hasSize(n)
        fake.dm(ann, "task in sl-who: Elsewhere", team = "T2")
        eventually { replies().size == n + 1 && lastReply().contains("not on this keel's list") }
        assertThat(get("/api/projects/$pid/tasks").andReturn().response.contentAsString).doesNotContain("Steal the show").doesNotContain("From a bot").doesNotContain("Elsewhere")
        assertThat(get("/api/plugins/slack").json()["log"].map { it["what"].asText() }).anyMatch { it.contains("refused") }.anyMatch { it.contains("bot") }
        n = replies().size
    }

    @Test
    fun `a message in a channel is read only when it mentions the app`() {
        val before = replies().size
        fake.envelope("events_api", mapOf("team_id" to "T1", "event_id" to "e-chan", "event" to mapOf("type" to "message", "channel_type" to "channel", "channel" to "C1", "user" to ann, "text" to "status", "ts" to "1.1")))
        quiet()
        assertThat(replies()).hasSize(before)
        fake.envelope("events_api", mapOf("team_id" to "T1", "event_id" to "e-men", "event" to mapOf("type" to "app_mention", "channel" to "C1", "user" to ann, "text" to "<@UBOT> nothing here", "ts" to "1.1")))
        eventually { lastReply().contains("I am keel") }
    }

    @Test
    fun `the same event sent twice acts once`() {
        val (pid, _) = newProject("sl-twice")
        fake.dm(ann, "task in sl-twice: Only once", eventId = "same-event")
        fake.dm(ann, "task in sl-twice: Only once", eventId = "same-event")
        eventually { lastReply().contains("is on the board") }
        quiet()
        val tasks = get("/api/projects/$pid/tasks").json()["tasks"]
        assertThat(tasks.filter { it["title"].asText() == "Only once" }).hasSize(1)
    }

    // ---- ask

    @Test
    fun `status gives a real answer from keel's own data, and no model is asked`() {
        val (pid, _) = newProject("sl-status")
        waitingFlow(pid, "t_status_1", emptyMap(), title = "Zebra invoices now")
        post("/api/projects/$pid/tasks", mapOf("title" to "Zebra the rest")).andExpect(status().isOk)
        val before = engine.calls.count { it.path == "/threads" || it.path.contains("agents/ask") }
        fake.dm(ann, "status")
        eventually { lastReply().contains("sl-status") && lastReply().contains("Zebra invoices now") }
        fake.dm(ann, "status zebra")
        eventually { lastReply().contains("Task *Zebra the rest*") && lastReply().contains("Flow *Zebra invoices now*") }
        fake.dm(ann, "status zzzz-nothing")
        eventually { lastReply().contains("no flow or task") }
        fake.dm(ann, "waiting")
        eventually { lastReply().contains("code `") }      // other tests' flows wait in this database too
        assertThat(engine.calls.count { it.path == "/threads" || it.path.contains("agents/ask") }).isEqualTo(before)
    }

    // ---- task

    @Test
    fun `a person on the list makes a task, it says who wrote it, and no flow starts`() {
        val (pid, _) = newProject("sl-task")
        val starts = engine.calls.count { it.path == "/threads" && it.method == "POST" }
        fake.dm(ann, "task in sl-task: Fix the export | it is empty for big files")
        eventually { lastReply().contains("is on the board") }
        val t = get("/api/projects/$pid/tasks").json()["tasks"].first { it["title"].asText() == "Fix the export" }
        assertThat(t["description"].asText()).contains("From Slack, written by Ann (UANN1)").contains("it is empty for big files")
        assertThat(engine.calls.count { it.path == "/threads" && it.method == "POST" }).isEqualTo(starts)
        fake.dm(ann, "task in no-such-project: x")
        eventually { lastReply().contains("keel has no project no-such-project") }
        fake.dm(ann, "task")
        eventually { lastReply().contains("Say `task <title>`") }
    }

    // ---- gates

    @Test
    fun `a waiting gate goes to the channel with its short lines and buttons, not its text`() {
        val (pid, _) = newProject("sl-gate")
        waitingFlow(pid, "t_gate", emptyMap())
        eventually { fake.sent("chat.postMessage").any { it.body.path("channel").asText() == "C1" && it.body.has("blocks") } }
        val m = fake.sent("chat.postMessage").last { it.body.has("blocks") }.body
        val text = m["blocks"][0]["text"]["text"].asText()
        assertThat(text).contains("Export invoices").contains("spec approval").contains("Two criteria").doesNotContain("SECRET CODE")
        assertThat(m["blocks"][1]["elements"].map { it["text"]["text"].asText() }).containsExactly("Approve")
        assertThat(m["parse"].asText()).isEqualTo("none")
    }

    @Test
    fun `the gate's whole text goes only when the person turned that on`() {
        post("/api/plugins/slack", mapOf("send_detail" to true)).andExpect(status().isOk)
        eventually { fake.connected }
        val (pid, _) = newProject("sl-detail")
        waitingFlow(pid, "t_detail", emptyMap())
        eventually { fake.sent("chat.postMessage").any { it.body.has("blocks") && it.body["blocks"][0]["text"]["text"].asText().contains("SECRET CODE") } }
    }

    @Test
    fun `a button goes through the Inbox's path and says who answered, a title cannot ping`() {
        val (pid, _) = newProject("sl-press")
        waitingFlow(pid, "t_press", emptyMap(), title = "Hello <!channel> @here *big*")
        eventually { fake.sent("chat.postMessage").any { it.body.has("blocks") } }
        val shown = fake.sent("chat.postMessage").last { it.body.has("blocks") }.body["blocks"][0]["text"]["text"].asText()
        assertThat(shown).doesNotContain("<!channel>").doesNotContain("@here")
        fake.press(ann, button("t_press", null))
        eventually { answers("t_press").size == 1 }
        val sent = answers("t_press")[0].body!!
        assertThat(sent["decision"].asText()).isEqualTo("approve")
        assertThat(sent["by"].asText()).isEqualTo("slack:Ann (UANN1)")
        eventually { lastReply().contains("Thanks Ann") }
        val logged = get("/api/plugins/slack").json()["log"][0]
        assertThat(logged["who"].asText()).contains("Ann")
        assertThat(logged["what"].asText()).contains("answered")
    }

    @Test
    fun `a button pressed by someone else does nothing`() {
        val (pid, _) = newProject("sl-other")
        waitingFlow(pid, "t_other", emptyMap())
        fake.press("UOTHER", button("t_other", null))
        eventually { fake.sent("chat.postEphemeral").any { it.body.path("text").asText().contains("not on this keel's list") } }
        fake.press(ann, button("t_other", null), team = "T2")
        quiet()
        assertThat(answers("t_other")).isEmpty()
    }

    @Test
    fun `an old button cannot answer a newer question`() {
        val (pid, _) = newProject("sl-stale")
        waitingFlow(pid, "t_stale", emptyMap())
        fake.press(ann, button("t_stale", null, q = "older-question"))
        eventually { lastReply().contains("keel did not take it") }
        assertThat(answers("t_stale")).isEmpty()
    }

    @Test
    fun `a gate only a person answers, and the Supervisor's never list, stay in keel`() {
        val (pid, _) = newProject("sl-only")
        waitingFlow(pid, "t_only", mapOf("person" to true, "gate_kind" to "note"))
        eventually { fake.sent("chat.postMessage").any { it.body.has("blocks") && it.body["blocks"][0]["text"]["text"].asText().contains("Answer it in keel") } }
        val m = fake.sent("chat.postMessage").last { it.body.has("blocks") }.body
        assertThat(m["blocks"].size()).isEqualTo(1)     // no buttons
        fake.press(ann, button("t_only", null))         // a forged press
        eventually { lastReply().contains("Only a person signed in to keel") }
        fake.dm(ann, "answer t_only-q123 approve")
        quiet()
        for (kind in SlackService.NEVER) {
            val tid = "t_never_$kind".replace("-", "_")
            waitingFlow(pid, tid, mapOf("gate_kind" to kind))
            fake.press(ann, button(tid, null))
            eventually { lastReply().contains("Answer it") }
            assertThat(answers(tid)).describedAs(kind).isEmpty()
        }
        assertThat(answers("t_only")).isEmpty()
    }

    @Test
    fun `a typed answer checks the choice and carries the reason, with a code that holds the question`() {
        val (pid, _) = newProject("sl-typed")
        val tid = "t1typed"
        waitingFlow(pid, tid, mapOf("choices" to listOf("use", "drop"), "choice_info" to listOf(
            mapOf("name" to "use", "label" to "Use it", "why" to "none"), mapOf("name" to "drop", "label" to "Drop it", "why" to "required"))))
        eventually { fake.sent("chat.postMessage").any { it.body.has("blocks") } }
        val m = fake.sent("chat.postMessage").last { it.body.has("blocks") }.body
        assertThat(m["blocks"][1]["elements"].map { it["text"]["text"].asText() }).containsExactly("Use it")     // a reason cannot come from a button
        assertThat(m["blocks"][0]["text"]["text"].asText()).contains("answer t1type-q123 drop <reason>").contains("answer t1type-q123 reject <reason>")
        fake.dm(ann, "answer t1type-q123 nonsense")
        eventually { lastReply().contains("This gate takes: use, drop, reject") }
        fake.dm(ann, "answer t1type-zzzz use")
        eventually { lastReply().contains("asks something else") }
        assertThat(answers(tid)).isEmpty()
        fake.dm(ann, "answer t1type-q123 drop not our code")
        eventually { answers(tid).size == 1 }
        val sent = answers(tid)[0].body!!
        assertThat(sent["payload"]["choice"].asText()).isEqualTo("drop")
        assertThat(sent["why"].asText()).isEqualTo("not our code")
        assertThat(sent["by"].asText()).contains("slack:Ann")
    }

    @Test
    fun `the Supervisor's words go under the gate's message, and an answer anywhere closes it`() {
        val (pid, _) = newProject("sl-sup")
        waitingFlow(pid, "t_sup", emptyMap())
        eventually { fake.sent("chat.postMessage").any { it.body.has("blocks") } }
        post("/internal/events", listOf(mapOf("type" to "supervisor.escalated", "thread_id" to "t_sup", "project_id" to pid, "step" to "g", "at" to java.time.Instant.now().toString(),
            "data" to mapOf("code" to "low-confidence", "reason" to "it was not sure enough", "summary" to "The second criterion has no test"))), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        eventually { lastReply().contains("The Supervisor asks you") && lastReply().contains("no test") }
        assertThat(fake.sent("chat.postMessage").last().body.path("thread_ts").asText()).isNotBlank()
        post("/internal/events", listOf(mapOf("type" to "gate.decided", "thread_id" to "t_sup", "project_id" to pid, "step" to "g", "at" to java.time.Instant.now().toString(),
            "data" to mapOf("decision" to "approve", "by" to "slack:Ann (UANN1)"))), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        eventually { fake.sent("chat.update").isNotEmpty() }
        val u = fake.sent("chat.update").last().body
        assertThat(u["blocks"].size()).isEqualTo(1)
        assertThat(u["blocks"][0]["text"]["text"].asText()).contains("Answered").contains("Ann").contains("in Slack")
    }

    @Test
    fun `it connects again when the connection is lost`() {
        fake.send("""{"type":"disconnect","reason":"refresh_requested"}""")
        val opens = fake.sent("apps.connections.open").size
        eventually { fake.sent("apps.connections.open").size > opens && fake.connected }
        val id = fake.dm(ann, "help")
        eventually { id in fake.acks }
    }
}
