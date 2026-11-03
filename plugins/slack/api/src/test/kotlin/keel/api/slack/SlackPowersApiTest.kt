package keel.api.slack

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * What docs/plans/finish-slack-supervisor.md adds, against the stand-in: a name on a task, forms in a modal, starting a flow, the free chat, and every gate from Slack. Each power
 * is off until the person turns it on. Budget and secret gates never get a button. No real Slack, no real model.
 */
class SlackPowersApiTest : ApiTest() {
    @Autowired lateinit var slack: SlackService
    @Autowired lateinit var jdbc: JdbcTemplate

    private lateinit var fake: FakeSlack
    private val json = jacksonObjectMapper()
    private val ann = "UANN1"
    private val sara = "USARA2"
    private val bea = "UBEA3"

    @BeforeEach
    fun link() {
        slack.forget()
        chatsBefore = engine.calls.count { it.path == "/slack/chat" }
        fake = FakeSlack().start()
        settings(mapOf("bot_token" to "xoxb-test", "app_token" to "xapp-test", "api_url" to fake.url, "channel" to "C1",
            "allowed" to listOf(mapOf("id" to ann, "name" to "Ann"), mapOf("id" to sara, "name" to "Sara Lind"), mapOf("id" to bea, "name" to "Bea"))))
        eventually { fake.connected }
    }

    @AfterEach
    fun unlink() {
        slack.forget()
        fake.stop()
    }

    private fun settings(body: Map<String, Any?>) = post("/api/plugins/slack", body).andExpect(status().isOk).json()

    private fun eventually(check: () -> Boolean) {
        val until = System.currentTimeMillis() + 8000
        while (!check() && System.currentTimeMillis() < until) Thread.sleep(20)
        assertThat(check()).isTrue()
    }

    private fun quiet() = Thread.sleep(400)
    private fun messages() = fake.sent("chat.postMessage").map { it.body }
    private fun said(words: String) = messages().any { it.path("text").asText().contains(words) }
    private fun gateMessages() = messages().filter { it.has("blocks") }
    private fun answers(tid: String) = engine.calls.filter { it.path == "/threads/$tid/resume" && it.method == "POST" }
    private fun value(tid: String, c: String? = null, q: String? = "q123456") = json.writeValueAsString(mapOf("t" to tid, "q" to q, "c" to c))

    private fun waitingFlow(pid: String, tid: String, question: Map<String, Any?>) {
        val full = mapOf("step" to "g", "kind" to "gate", "title" to "spec approval", "name" to "spec approval", "detail" to "text", "options" to listOf("approve", "reject"), "id" to "q123456") + question
        engine.extraRoutes["/threads/$tid"] = { _ -> 200 to mapOf("thread_id" to tid, "project_id" to pid, "status" to "waiting", "waiting" to full) }
        engine.extraRoutes["/threads/$tid/resume"] = { _ -> 200 to mapOf("thread_id" to tid, "project_id" to pid, "status" to "running") }
        post("/internal/events", listOf(
            mapOf("type" to "thread.started", "thread_id" to tid, "project_id" to pid, "at" to java.time.Instant.now().toString(), "data" to mapOf("title" to "Export", "workflow" to "feature")),
            mapOf("type" to "gate.waiting", "thread_id" to tid, "project_id" to pid, "step" to "g", "at" to java.time.Instant.now().toString(), "data" to mapOf("title" to full["title"], "kind" to full["kind"])),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        eventually { gateMessages().any { it["blocks"][0]["text"]["text"].asText().contains("Export") } }
    }

    private fun lastGate() = gateMessages().last()
    private fun actions(m: JsonNode) = m["blocks"].firstOrNull { it["type"].asText() == "actions" }?.get("elements")?.toList().orEmpty()

    // ---- B3 a name on a task

    private fun task(pid: String, title: String) = get("/api/projects/$pid/tasks").json()["tasks"].first { it["title"].asText() == title }

    @Test
    fun `task with a name on the list makes it for them and sends a direct message`() {
        val (pid, _) = newProject("sl-b3-task")
        settings(mapOf("project" to pid))
        fake.dm(ann, "task Fix the export @sara")
        eventually { said("for Sara Lind") }
        assertThat(task(pid, "Fix the export")["assignee"].asText()).isEqualTo("Sara Lind")
        val dm = messages().first { it["channel"].asText() == sara }
        assertThat(dm["text"].asText()).contains("Ann gave you a task").contains("Fix the export")
    }

    @Test
    fun `a real Slack mention names the person, and only the app's own mention is taken off`() {
        val (pid, _) = newProject("sl-b3-mention")
        settings(mapOf("project" to pid))
        fake.channel(ann, "<@UBOT> task Mentioned task <@$sara>")
        eventually { said("for Sara Lind") }
        assertThat(task(pid, "Mentioned task")["assignee"].asText()).isEqualTo("Sara Lind")
    }

    @Test
    fun `a name that is not on the list makes no task, and your own task sends no message`() {
        val (pid, _) = newProject("sl-b3-refuse")
        settings(mapOf("project" to pid))
        fake.dm(ann, "task Nobody hears @stranger")
        eventually { said("stranger is not on this keel's list") }
        fake.dm(ann, "task A real mention of a stranger <@UNOBODY>")
        eventually { said("That person is not on this keel's list") }
        assertThat(get("/api/projects/$pid/tasks").json()["tasks"].map { it["title"].asText() }).doesNotContain("Nobody hears", "A real mention of a stranger")
        fake.dm(ann, "task For me @ann")
        eventually { said("for Ann") }
        assertThat(messages().none { it["channel"].asText() == ann && it["text"].asText().contains("gave you a task") }).isTrue()
    }

    @Test
    fun `assign puts a name on a local task by title or id, and refuses what it cannot`() {
        val (pid, _) = newProject("sl-b3-assign")
        settings(mapOf("project" to pid))
        fake.dm(ann, "task Late filter")
        eventually { said("Late filter") }
        fake.dm(ann, "assign late filter @bea")
        eventually { said("now says Bea") }
        val t = task(pid, "Late filter")
        assertThat(t["assignee"].asText()).isEqualTo("Bea")
        assertThat(messages().any { it["channel"].asText() == bea }).isTrue()
        fake.dm(ann, "assign ${t["id"].asText().take(8)} @sara")
        eventually { task(pid, "Late filter")["assignee"].asText() == "Sara Lind" }
        fake.dm(ann, "assign late filter @stranger")
        eventually { said("stranger is not on this keel's list") }
        fake.dm(ann, "assign no such thing @bea")
        eventually { said("keel has no task") }
        fake.dm(ann, "assign @bea")
        eventually { said("Say `assign <task> @name`") }
    }

    @Test
    fun `Jira keeps its own assignee, and a key typed by hand is keel's own`() {
        val (pid, _) = newProject("sl-b3-jira")
        settings(mapOf("project" to pid))
        post("/api/projects/$pid/tasks", mapOf("title" to "From Jira", "external_key" to "ORD-901")).andExpect(status().isOk)
        post("/api/projects/$pid/tasks", mapOf("title" to "By hand", "external_key" to "ORD-902")).andExpect(status().isOk)
        jdbc.update("UPDATE tasks SET external_status = 'In Progress', assignee = 'Jira Person' WHERE external_key = 'ORD-901'")
        fake.dm(ann, "assign ORD-901 @bea")
        eventually { said("Jira keeps its own assignee") }
        assertThat(task(pid, "From Jira")["assignee"].asText()).isEqualTo("Jira Person")
        fake.dm(ann, "assign ORD-902 @bea")
        eventually { task(pid, "By hand")["assignee"]?.asText() == "Bea" }
    }

    // ---- B4 a form in a modal

    private val form = mapOf(
        "kind" to "clarify", "title" to "Two questions", "options" to listOf("approve"),
        "questions" to listOf(
            mapOf("id" to "db", "question" to "Which database?", "options" to listOf(mapOf("label" to "Postgres", "recommended" to true), mapOf("label" to "SQLite"))),
            mapOf("id" to "areas", "question" to "Which areas?", "multiple" to true, "own_words" to false, "options" to listOf(mapOf("label" to "API", "recommended" to true), mapOf("label" to "Web"))),
        ),
    )

    private fun input(view: JsonNode, id: String) = view["blocks"].first { it.path("block_id").asText() == id }

    @Test
    fun `a gate with several questions opens a modal with the same options, and its answer takes the Inbox's path`() {
        val (pid, _) = newProject("sl-b4")
        waitingFlow(pid, "t_form", form)
        val m = lastGate()
        assertThat(actions(m).map { it["action_id"].asText() }).containsExactly(SlackService.FORM_ACTION)
        fake.press(ann, value("t_form"), action = SlackService.FORM_ACTION, trigger = "trig-1")
        eventually { fake.sent("views.open").isNotEmpty() }
        val open = fake.sent("views.open")[0].body
        assertThat(open["trigger_id"].asText()).isEqualTo("trig-1")
        val view = open["view"]
        assertThat(view["callback_id"].asText()).isEqualTo(SlackForms.FORM)
        assertThat(view["title"]["text"].asText().length).isLessThanOrEqualTo(24)
        val db = input(view, "q0")
        assertThat(db["label"]["text"].asText()).isEqualTo("Which database?")
        assertThat(db["element"]["type"].asText()).isEqualTo("static_select")
        assertThat(db["element"]["options"].map { it["text"]["text"].asText() }).containsExactly("Postgres", "SQLite")
        val areas = input(view, "q1")
        assertThat(areas["element"]["type"].asText()).isEqualTo("multi_static_select")
        assertThat(areas["element"]["initial_options"].map { it["text"]["text"].asText() }).containsExactly("API")
        assertThat(view["blocks"].none { it.path("block_id").asText() == "w1" }).isTrue()       // own_words: false, as in the Inbox

        fake.submit(ann, view, mapOf(
            "q0" to mapOf("pick" to mapOf("selected_option" to mapOf("value" to "1"))),
            "w0" to mapOf("text" to mapOf("value" to "")),
            "q1" to mapOf("pick" to mapOf("selected_options" to listOf(mapOf("value" to "0"), mapOf("value" to "1")))),
            "why" to mapOf("text" to mapOf("value" to "small enough")),
        ))
        eventually { answers("t_form").size == 1 }
        val sent = answers("t_form")[0].body!!
        assertThat(sent["decision"].asText()).isEqualTo("approve")
        assertThat(sent["why"].asText()).isEqualTo("small enough")
        assertThat(sent["payload"]["answers"]["db"].asText()).isEqualTo("SQLite")
        assertThat(sent["payload"]["answers"]["areas"].map { it.asText() }).containsExactly("API", "Web")
        assertThat(sent["by"].asText()).isEqualTo("slack:Ann (UANN1)")
    }

    @Test
    fun `typed words win on a single question and join a multiple one, as in the Inbox`() {
        val (pid, _) = newProject("sl-b4-typed")
        val q = mapOf("kind" to "clarify", "questions" to listOf(
            mapOf("id" to "a", "question" to "One?", "options" to listOf(mapOf("label" to "X"))),
            mapOf("id" to "b", "question" to "Many?", "multiple" to true, "options" to listOf(mapOf("label" to "Y"))),
            mapOf("id" to "c", "question" to "Left empty?", "options" to listOf(mapOf("label" to "Z", "recommended" to true))),
        ))
        waitingFlow(pid, "t_typed", q)
        fake.press(ann, value("t_typed"), action = SlackService.FORM_ACTION)
        eventually { fake.sent("views.open").isNotEmpty() }
        fake.submit(ann, fake.sent("views.open")[0].body["view"], mapOf(
            "q0" to mapOf("pick" to mapOf("selected_option" to mapOf("value" to "0"))), "w0" to mapOf("text" to mapOf("value" to "my words")),
            "q1" to mapOf("pick" to mapOf("selected_options" to listOf(mapOf("value" to "0")))), "w1" to mapOf("text" to mapOf("value" to "more")),
            "q2" to mapOf("pick" to mapOf("selected_option" to null)),
        ))
        eventually { answers("t_typed").size == 1 }
        val got = answers("t_typed")[0].body!!["payload"]["answers"]
        assertThat(got["a"].asText()).isEqualTo("my words")
        assertThat(got["b"].map { it.asText() }).containsExactly("Y", "more")
        assertThat(got.has("c")).isFalse()      // unanswered: the engine takes the recommended one and says so
    }

    @Test
    fun `a named choice next to the questions goes back with them`() {
        val (pid, _) = newProject("sl-b4-choice")
        waitingFlow(pid, "t_both", mapOf("kind" to "gate", "questions" to listOf(mapOf("id" to "a", "question" to "One?", "options" to listOf(mapOf("label" to "X")))),
            "choices" to listOf("go", "stop"), "choice_info" to listOf(mapOf("name" to "go", "label" to "Go on", "default" to true), mapOf("name" to "stop", "label" to "Stop"))))
        fake.press(ann, value("t_both"), action = SlackService.FORM_ACTION)
        eventually { fake.sent("views.open").isNotEmpty() }
        val view = fake.sent("views.open")[0].body["view"]
        assertThat(input(view, "choice")["element"]["initial_option"]["text"]["text"].asText()).isEqualTo("Go on")
        fake.submit(ann, view, mapOf("q0" to mapOf("pick" to mapOf("selected_option" to mapOf("value" to "0"))), "choice" to mapOf("pick" to mapOf("selected_option" to mapOf("value" to "1")))))
        eventually { answers("t_both").size == 1 }
        assertThat(answers("t_both")[0].body!!["payload"]["choice"].asText()).isEqualTo("stop")
    }

    @Test
    fun `a form Slack cannot show says answer it in keel, with no button`() {
        val (pid, _) = newProject("sl-b4-big")
        val many = (1..SlackForms.MAX_QUESTIONS + 1).map { mapOf("id" to "q$it", "question" to "Q$it?", "options" to listOf(mapOf("label" to "A"))) }
        waitingFlow(pid, "t_many", mapOf("kind" to "clarify", "questions" to many))
        assertThat(lastGate()["blocks"][0]["text"]["text"].asText()).contains("Answer it in keel")
        assertThat(actions(lastGate())).isEmpty()

        val long = "x".repeat(SlackForms.MAX_LABEL + 1)
        waitingFlow(pid, "t_long", mapOf("kind" to "clarify", "questions" to listOf(mapOf("id" to "l", "question" to "Long?", "options" to listOf(mapOf("label" to long))))))
        assertThat(actions(lastGate())).isEmpty()

        fake.press(ann, value("t_many"), action = SlackService.FORM_ACTION)     // a forged press
        eventually { said("Slack cannot show this form") }
        assertThat(fake.sent("views.open")).isEmpty()
    }

    @Test
    fun `a skip menu has one box for its reasons, and a ticked step that needs one and has none changes nothing`() {
        val (pid, _) = newProject("sl-b4-skip")
        val q = mapOf("kind" to "gate", "questions" to listOf(
            mapOf("id" to "skip", "question" to "Which steps should keel skip?", "multiple" to true, "own_words" to false, "options" to listOf(
                mapOf("label" to "lint", "why" to "required", "description" to "it costs little"), mapOf("label" to "smoke", "why" to "required"), mapOf("label" to "docs", "why" to "optional"))),
        ))
        waitingFlow(pid, "t_skip", q)
        assertThat(actions(lastGate()).map { it["action_id"].asText() }).containsExactly(SlackService.FORM_ACTION)
        fake.press(ann, value("t_skip"), action = SlackService.FORM_ACTION)
        eventually { fake.sent("views.open").isNotEmpty() }
        val view = fake.sent("views.open")[0].body["view"]
        assertThat(input(view, "q0")["element"]["options"][0]["description"]["text"].asText()).isEqualTo("it costs little")
        assertThat(input(view, "r0")["label"]["text"].asText()).contains("each one you tick")
        fun ticked(reasons: String) = mapOf("q0" to mapOf("pick" to mapOf("selected_options" to listOf(mapOf("value" to "0"), mapOf("value" to "1"), mapOf("value" to "2")))),
            "r0" to mapOf("text" to mapOf("value" to reasons)))
        fake.submit(ann, view, ticked("lint: no linter here"))
        eventually { said("Write why for \"smoke\" first") }
        assertThat(answers("t_skip")).isEmpty()
        fake.submit(ann, view, ticked("lint: no linter here\nsmoke: nothing to run"))
        eventually { answers("t_skip").size == 1 }
        val p = answers("t_skip")[0].body!!["payload"]
        assertThat(p["answers"]["skip"].map { it.asText() }).containsExactly("lint", "smoke", "docs")
        assertThat(p["reasons"]["lint"].asText()).isEqualTo("no linter here")
        assertThat(p["reasons"]["smoke"].asText()).isEqualTo("nothing to run")
        assertThat(p["reasons"].has("docs")).isFalse()         // optional: none given
    }

    @Test
    fun `a single option that takes a reason sends it as the Inbox does`() {
        val (pid, _) = newProject("sl-b4-one")
        waitingFlow(pid, "t_one", mapOf("kind" to "gate", "questions" to listOf(mapOf("id" to "f", "question" to "The finding?", "options" to listOf(mapOf("label" to "accept", "why" to "required"), mapOf("label" to "fix"))))))
        fake.press(ann, value("t_one"), action = SlackService.FORM_ACTION)
        eventually { fake.sent("views.open").isNotEmpty() }
        val view = fake.sent("views.open")[0].body["view"]
        val pick = mapOf("pick" to mapOf("selected_option" to mapOf("value" to "0")))
        fake.submit(ann, view, mapOf("q0" to pick))
        eventually { said("Write why for \"accept\" first") }
        fake.submit(ann, view, mapOf("q0" to pick, "w0" to mapOf("text" to mapOf("value" to "it is only a test file"))))
        eventually { answers("t_one").size == 1 }
        assertThat(answers("t_one")[0].body!!["payload"]["answers"]["f"].asText()).isEqualTo("accept: it is only a test file")
    }

    @Test
    fun `a form cannot be answered in words, an old form answers nothing, and a stranger's form is dropped`() {
        val (pid, _) = newProject("sl-b4-rules")
        waitingFlow(pid, "t_rules", form)
        slack.answer(slack.config(), SlackPerson(ann, "Ann"), "t_rules", "q123456", "approve", null).also { assertThat(it).contains("Answer the form") }
        assertThat(answers("t_rules")).isEmpty()
        fake.press(ann, value("t_rules"), action = SlackService.FORM_ACTION)
        eventually { fake.sent("views.open").isNotEmpty() }
        val view = fake.sent("views.open")[0].body["view"]
        fake.submit("USTRANGER", view, emptyMap())
        quiet()
        assertThat(answers("t_rules")).isEmpty()
        // the flow moved on to a new question: the old form is no answer to it
        engine.extraRoutes["/threads/t_rules"] = { _ -> 200 to mapOf("thread_id" to "t_rules", "project_id" to pid, "status" to "waiting", "waiting" to form + mapOf("id" to "q999")) }
        fake.submit(ann, view, emptyMap())
        eventually { said("now asks something else") }
        assertThat(answers("t_rules")).isEmpty()
        fake.press(ann, value("t_rules", q = "q123456"), action = SlackService.FORM_ACTION)
        eventually { said("now asks something else") }
    }

    // ---- B2 starting a flow

    private fun starts() = engine.calls.count { it.path == "/threads" && it.method == "POST" }
    private fun startMessage() = messages().last { it.has("blocks") && it["text"].asText().startsWith("Start ") }
    private fun startId(m: JsonNode) = actions(m).first { it["action_id"].asText() == SlackService.START }["value"].asText()

    @Test
    fun `starting a flow is off until the person turns it on`() {
        val before = starts()
        fake.dm(ann, "start feature add a late filter")
        eventually { said("Starting flows from Slack is off") }
        assertThat(starts()).isEqualTo(before)
    }

    @Test
    fun `start asks with one button, starts through the dashboard's start, and records who`() {
        val (pid, _) = newProject("sl-b2")
        settings(mapOf("allow_start" to true))
        val before = starts()
        fake.dm(ann, "start feature in sl-b2: add a late filter")
        eventually { messages().any { it["text"].asText().startsWith("Start ") } }
        assertThat(starts()).isEqualTo(before)         // nothing started yet
        val m = startMessage()
        assertThat(m["text"].asText()).contains("feature").contains("sl-b2").contains("add a late filter")
        assertThat(actions(m).map { it["text"]["text"].asText() }).containsExactly("Start it", "Cancel")
        fake.press(ann, startId(m), action = SlackService.START)
        eventually { starts() == before + 1 }
        val body = engine.lastBody("/threads")!!
        assertThat(body["project_id"].asText()).isEqualTo(pid)
        assertThat(body["title"].asText()).isEqualTo("add a late filter")
        assertThat(body["request"].asText()).isEqualTo("add a late filter")
        assertThat(body["data"]["started_via"].asText()).isEqualTo("slack")
        assertThat(body["data"]["started_by"].asText()).isEqualTo("slack:Ann (UANN1)")
        eventually { said("Its gates come to the channel") }
        fake.press(ann, startId(m), action = SlackService.START)      // one use
        eventually { said("no longer there") }
        assertThat(starts()).isEqualTo(before + 1)
    }

    @Test
    fun `only who asked can press it, Cancel starts nothing, and it is gone when the power goes off`() {
        newProject("sl-b2-who")
        settings(mapOf("allow_start" to true))
        val before = starts()
        fake.dm(ann, "start feature in sl-b2-who: first")
        eventually { messages().any { it["text"].asText().contains("first") } }
        val m = startMessage()
        fake.press(bea, startId(m), action = SlackService.START)
        eventually { fake.sent("chat.postEphemeral").any { it.body["text"].asText().contains("Only the person who asked") } }
        fake.press("USTRANGER", startId(m), action = SlackService.START)
        eventually { fake.sent("chat.postEphemeral").any { it.body["text"].asText().contains("not on this keel's list") } }
        fake.press(ann, startId(m), action = SlackService.START_CANCEL)
        eventually { said("Cancelled") }
        fake.press(ann, startId(m), action = SlackService.START)
        eventually { said("no longer there") }
        assertThat(starts()).isEqualTo(before)

        fake.dm(ann, "start feature in sl-b2-who: second")
        eventually { messages().any { it["text"].asText().contains("second") } }
        val again = startMessage()
        settings(mapOf("allow_start" to false))
        eventually { fake.connected }
        fake.press(ann, startId(again), action = SlackService.START)
        eventually { said("off now") }
        assertThat(starts()).isEqualTo(before)
    }

    @Test
    fun `keel's refusal reaches Slack, and a project must be clear`() {
        newProject("sl-b2-refuse")
        settings(mapOf("allow_start" to true))
        fake.dm(ann, "start nosuchflow in sl-b2-refuse: x")
        eventually { messages().any { it["text"].asText().contains("nosuchflow") } }
        fake.press(ann, startId(startMessage()), action = SlackService.START)
        eventually { said("keel did not start it") }
        fake.dm(ann, "start feature in nowhere-here: x")
        eventually { said("keel has no project nowhere-here") }
        fake.dm(ann, "start")
        eventually { said("Say `start <flow>") }
    }

    @Test
    fun `a client of the start route cannot claim a flow came from Slack`() {
        val (pid, _) = newProject("sl-b2-claim")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Claim", "options" to mapOf("started_via" to "slack", "started_by" to "slack:Someone (U1)", "x" to "y"),
            "acs" to listOf(mapOf("id" to "AC-001", "layer" to "API", "title" to "a")))).andExpect(status().isOk)
        val data = engine.lastBody("/threads")!!["data"]
        assertThat(data.has("started_by")).isFalse()
        assertThat(data.path("started_via").asText("")).isNotEqualTo("slack")
    }

    // ---- B1 the chat

    private fun chatAnswers(answer: (JsonNode?) -> Map<String, Any?> = { mapOf("status" to "done", "text" to "It runs fine.", "tokens" to 100, "cost_usd" to 0.01) }) {
        engine.extraRoutes["/slack/chat"] = { b -> 200 to answer(b) }
    }

    private var chatsBefore = 0
    private fun chats() = engine.calls.filter { it.path == "/slack/chat" }.drop(chatsBefore)

    @AfterEach
    fun noChat() {
        engine.extraRoutes.remove("/slack/chat")
    }

    @Test
    fun `the chat is off until the person turns it on, and then no model is asked`() {
        chatAnswers()
        fake.dm(ann, "how does the export go?")
        eventually { said("I am keel") }
        assertThat(chats()).isEmpty()
    }

    @Test
    fun `a message that is no command gets a model's answer in the same thread, and a reply there goes on`() {
        val (pid, root) = newProject("sl-b1")
        settings(mapOf("allow_chat" to true, "project" to pid))
        chatAnswers()
        fake.dm(ann, "how does the export go?", mapOf("channel" to "D1", "ts" to "100.1"))
        eventually { said("It runs fine") }
        val sent = chats()[0].body!!
        assertThat(sent["text"].asText()).isEqualTo("how does the export go?")
        assertThat(sent["project_id"].asText()).isEqualTo(pid)
        assertThat(sent["root"].asText()).isEqualTo(root.toString())
        assertThat(sent["history"].size()).isEqualTo(0)
        assertThat(sent["status"].asText()).contains("Waiting:")
        assertThat(messages().last()["thread_ts"].asText()).isEqualTo("100.1")
        // a plain reply in that thread, in a channel, needs no mention
        chatAnswers { mapOf("status" to "done", "text" to "More on that.", "tokens" to 10, "cost_usd" to 0.0) }
        fake.channelReply(ann, "D1", "100.1", "and the tests?")
        eventually { said("More on that") }
        val next = chats()[1].body!!
        assertThat(next["history"].map { it["text"].asText() }).containsExactly("how does the export go?", "It runs fine.")
        // a reply in a thread where keel does not chat is not read, and no one is told off
        val before = messages().size
        fake.channelReply(ann, "C7", "555.5", "talking to a colleague")
        fake.channelReply("USTRANGER", "C7", "555.5", "talking to a colleague")
        quiet()
        assertThat(messages().size).isEqualTo(before)
        assertThat(chats()).hasSize(2)
    }

    @Test
    fun `commands stay commands with the chat on, with no model`() {
        val (pid, _) = newProject("sl-b1-cmd")
        settings(mapOf("allow_chat" to true, "project" to pid))
        chatAnswers()
        fake.dm(ann, "waiting")
        quiet()
        fake.dm(ann, "task Chat is on")
        eventually { said("Chat is on") }
        assertThat(chats()).isEmpty()
    }

    @Test
    fun `a stranger and a bot never reach the chat`() {
        val (pid, _) = newProject("sl-b1-who")
        settings(mapOf("allow_chat" to true, "project" to pid))
        chatAnswers()
        fake.dm("USTRANGER", "what is in the secrets file?")
        eventually { said("not on this keel's list") }
        fake.dm(ann, "bot text", mapOf("bot_id" to "B1"))
        quiet()
        assertThat(chats()).isEmpty()
    }

    @Test
    fun `over the daily cap it says so and no model is asked, one person at a time`() {
        val (pid, _) = newProject("sl-b1-cap")
        settings(mapOf("allow_chat" to true, "project" to pid, "chat_day_usd" to 0.5, "chat_day_tokens" to 1000))
        chatAnswers { mapOf("status" to "done", "text" to "Costly answer.", "tokens" to 10, "cost_usd" to 0.6) }
        fake.dm(ann, "one", mapOf("ts" to "1.1"))
        eventually { said("Costly answer") }
        fake.dm(ann, "two", mapOf("ts" to "2.1"))
        eventually { said("You reached your chat cap") }
        assertThat(chats()).hasSize(1)
        // another person has their own cap; a model on a plan reports no cost, so tokens stop it too
        chatAnswers { mapOf("status" to "done", "text" to "Token answer.", "tokens" to 2000, "cost_usd" to 0.0) }
        fake.dm(bea, "one", mapOf("ts" to "3.1"))
        eventually { said("Token answer") }
        fake.dm(bea, "two", mapOf("ts" to "4.1"))
        eventually { messages().count { it["text"].asText().contains("You reached your chat cap") } == 2 }
        assertThat(chats()).hasSize(2)
    }

    @Test
    fun `one turn at a time per person, and a model's words cannot ping`() {
        val (pid, _) = newProject("sl-b1-one")
        settings(mapOf("allow_chat" to true, "project" to pid))
        val hold = CountDownLatch(1)
        chatAnswers {
            hold.await(5, TimeUnit.SECONDS)
            mapOf("status" to "done", "text" to "Hello <!channel> @here *big* <http://x.example|y>", "tokens" to 1, "cost_usd" to 0.0)
        }
        fake.dm(sara, "first question", mapOf("ts" to "6.1"))
        eventually { chats().size == 1 }
        fake.dm(sara, "second question", mapOf("ts" to "7.1"))
        eventually { said("still answering your last question") }
        hold.countDown()
        eventually { said("Hello") }
        assertThat(chats()).hasSize(1)
        val shown = messages().last { it["text"].asText().contains("Hello") }["text"].asText()
        assertThat(shown).doesNotContain("<!channel>").doesNotContain("@here").doesNotContain("<http")
    }

    @Test
    fun `a failed turn says so and is not remembered`() {
        val (pid, _) = newProject("sl-b1-fail")
        settings(mapOf("allow_chat" to true, "project" to pid))
        chatAnswers { mapOf("status" to "failed", "text" to "", "tokens" to 0, "cost_usd" to 0.0, "error" to "the model is down") }
        fake.dm(ann, "anything", mapOf("channel" to "D2", "ts" to "8.1"))
        eventually { said("The chat failed: the model is down") }
        engine.extraRoutes["/slack/chat"] = { _ -> 500 to mapOf("error" to "boom") }
        fake.dm(ann, "again", mapOf("channel" to "D3", "ts" to "9.1"))
        eventually { said("The chat failed. The commands still work") }
    }

    @Test
    fun `a project that uses only local models refuses a cloud model in the chat`() {
        val (pid, _) = newProject("sl-b1-local")
        settings(mapOf("allow_chat" to true, "project" to pid))
        put("/api/projects/$pid/settings", mapOf("local_only" to true, "default_model" to mapOf("provider" to "claude", "mode" to "api", "model" to "some-cloud-model")))
        chatAnswers()
        fake.dm(ann, "anything")
        eventually { said("local models only") }
        assertThat(chats()).isEmpty()
    }

    @Test
    fun `its turns are agent calls the budget counts`() {
        val (pid, _) = newProject("sl-b1-call")
        post("/internal/events", listOf(
            mapOf("type" to "slack.chat.started", "thread_id" to "slack", "project_id" to pid, "call_id" to "c-sl-1", "at" to java.time.Instant.now().toString(),
                "data" to mapOf("agent" to "slack-chat", "provider" to "fake", "model" to "fake")),
            mapOf("type" to "slack.chat.finished", "thread_id" to "slack", "project_id" to pid, "call_id" to "c-sl-1", "at" to java.time.Instant.now().toString(),
                "data" to mapOf("agent" to "slack-chat", "status" to "done", "tokens_in" to 90, "tokens_out" to 10)),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        val row = jdbc.queryForMap("SELECT agent, status, tokens_in FROM agent_calls WHERE id = 'c-sl-1'")
        assertThat(row["agent"]).isEqualTo("slack-chat")
        assertThat(row["status"]).isEqualTo("done")
        assertThat((row["tokens_in"] as Number).toInt()).isEqualTo(90)
    }

    // ---- B5 every gate from Slack

    private fun allowAll() = settings(mapOf("allow_all_gates" to true, "strict_allowed" to listOf(mapOf("id" to ann, "name" to "Ann"))))

    @Test
    fun `off, a kept gate has no buttons, and the second list must be part of the first`() {
        val (pid, _) = newProject("sl-b5-off")
        waitingFlow(pid, "t_b5off", mapOf("person" to true))
        assertThat(actions(lastGate())).isEmpty()
        settings(mapOf("strict_allowed" to listOf(mapOf("id" to ann, "name" to "Ann"))))     // the list alone opens nothing
        fake.press(ann, value("t_b5off"), action = SlackService.KEPT_ACTION)
        eventually { fake.sent("chat.postEphemeral").any { it.body["text"].asText().contains("Only a person signed in") } }
        assertThat(fake.sent("views.open")).isEmpty()
        post("/api/plugins/slack", mapOf("strict_allowed" to listOf(mapOf("id" to "UNOTLISTED", "name" to "Nobody")))).andExpect(status().isBadRequest)
    }

    @Test
    fun `on, a person on the short list answers a kept gate with a reason and one confirm`() {
        val (pid, _) = newProject("sl-b5")
        allowAll()
        eventually { fake.connected }
        waitingFlow(pid, "t_b5", mapOf("person" to true))
        val m = lastGate()
        assertThat(m["blocks"][0]["text"]["text"].asText()).contains("Ann may answer it here")
        assertThat(actions(m).map { it["action_id"].asText() }.toSet()).containsExactly(SlackService.KEPT_ACTION)
        assertThat(actions(m).map { it["text"]["text"].asText() }).containsExactly("Approve", "Send back")
        fake.press(ann, value("t_b5"), action = SlackService.KEPT_ACTION, trigger = "trig-5")
        eventually { fake.sent("views.open").isNotEmpty() }
        val view = fake.sent("views.open")[0].body["view"]
        assertThat(view["callback_id"].asText()).isEqualTo(SlackForms.KEPT)
        assertThat(view["submit"]["text"].asText()).isEqualTo("Yes, answer it")
        assertThat(input(view, "why")["optional"].asBoolean()).isFalse()
        assertThat(answers("t_b5")).isEmpty()          // opening it answers nothing
        fake.submit(ann, view, mapOf("why" to mapOf("text" to mapOf("value" to " "))))
        eventually { said("It needs a reason") }
        assertThat(answers("t_b5")).isEmpty()
        fake.submit(ann, view, mapOf("why" to mapOf("text" to mapOf("value" to "I read it and it is right"))))
        eventually { answers("t_b5").size == 1 }
        val sent = answers("t_b5")[0].body!!
        assertThat(sent["decision"].asText()).isEqualTo("approve")
        assertThat(sent["why"].asText()).isEqualTo("I read it and it is right")
        assertThat(sent["by"].asText()).isEqualTo("slack:Ann (UANN1)")
        val logged = get("/api/plugins/slack").json()["log"][0]
        assertThat(logged["what"].asText()).contains("normally keeps")
    }

    @Test
    fun `the never list gets the same, and someone not on the short list is refused`() {
        val (pid, _) = newProject("sl-b5-never")
        allowAll()
        eventually { fake.connected }
        for (kind in SlackService.NEVER - SlackService.ALWAYS) {
            val tid = "t_b5_$kind".replace("-", "_")
            waitingFlow(pid, tid, mapOf("gate_kind" to kind))
            assertThat(actions(lastGate()).map { it["action_id"].asText() }).describedAs(kind).contains(SlackService.KEPT_ACTION)
            fake.press(bea, value(tid), action = SlackService.KEPT_ACTION)
            eventually { fake.sent("chat.postEphemeral").any { it.body["text"].asText().contains("not on the short list") } }
            assertThat(fake.sent("views.open")).isEmpty()
            // a forged confirm from Bea, and a typed answer from anyone, change nothing
            fake.submit(bea, mapOf("callback_id" to SlackForms.KEPT, "private_metadata" to value2(tid)), mapOf("why" to mapOf("text" to mapOf("value" to "because"))))
            eventually { said("not on the short list") }
            assertThat(slack.answer(slack.config(), SlackPerson(ann, "Ann"), tid, "q123456", "approve", "typed")).contains("Answer it")
            assertThat(answers(tid)).describedAs(kind).isEmpty()
        }
    }

    private fun value2(tid: String) = json.writeValueAsString(mapOf("t" to tid, "q" to "q123456", "c" to null, "ch" to "C1", "ts" to "5.5"))

    @Test
    fun `budget and secret gates stay in keel, even with every gate allowed`() {
        val (pid, _) = newProject("sl-b5-money")
        allowAll()
        eventually { fake.connected }
        val cases = listOf(
            mapOf("gate_kind" to "budget"), mapOf("gate_kind" to "secrets"), mapOf("kind" to "budget"), mapOf("kind" to "usage"),
            mapOf("title" to "Token cap reached"), mapOf("person" to true, "gate_kind" to "secrets"),
        )
        cases.forEachIndexed { i, q ->
            val tid = "t_b5_money_$i"
            waitingFlow(pid, tid, q)
            assertThat(actions(lastGate())).describedAs("$q").isEmpty()
            val before = fake.sent("views.open").size
            fake.press(ann, value(tid), action = SlackService.KEPT_ACTION)
            quiet()
            assertThat(fake.sent("views.open")).describedAs("$q").hasSize(before)
            fake.press(ann, value(tid))
            quiet()
            fake.submit(ann, mapOf("callback_id" to SlackForms.KEPT, "private_metadata" to value2(tid)), mapOf("why" to mapOf("text" to mapOf("value" to "because"))))
            quiet()
            assertThat(slack.answer(slack.config(), SlackPerson(ann, "Ann"), tid, "q123456", "approve", "typed")).contains("Answer it there")
            assertThat(answers(tid)).describedAs("$q").isEmpty()
        }
    }

    @Test
    fun `the page says what each power opens, and all are off on a new link`() {
        val v = get("/api/plugins/slack").json()
        assertThat(v["config"]["allow_chat"].asBoolean()).isFalse()
        assertThat(v["config"]["allow_start"].asBoolean()).isFalse()
        assertThat(v["config"]["allow_all_gates"].asBoolean()).isFalse()
        assertThat(v["config"]["strict_allowed"].size()).isEqualTo(0)
        assertThat(v["sends_model"].asText()).contains("With the chat on").contains("files the model chooses to read")
        post("/api/plugins/slack", mapOf("chat_day_usd" to 0)).andExpect(status().isBadRequest)
    }
}
