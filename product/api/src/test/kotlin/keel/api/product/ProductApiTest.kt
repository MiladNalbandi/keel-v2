package keel.api.product

import com.fasterxml.jackson.databind.JsonNode
import keel.api.support.ApiTest
import keel.api.support.StubJira
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.content
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.time.Instant
import java.time.temporal.ChronoUnit

/**
 * keel Product's api with keel's stub engine: an idea goes brief → impact → decision → plan → delivery → outcome, the
 * stage flows' events drive it (as the engine add-on sends them), people approve, send back, decide, ask, answer,
 * disagree, park; the stories go to keel Tasks or to Jira (the stub).
 */
class ProductApiTest : ApiTest() {

    companion object {
        val jira: StubJira = StubJira.start("cloud")
        val WORKFLOWS = listOf("product-discover", "product-impact", "product-decide", "product-plan", "product-outcome")

        @JvmStatic
        @DynamicPropertySource
        fun productProps(r: DynamicPropertyRegistry) {
            r.add("keel.product.inline-effects") { true }
            r.add("keel.product.scheduler") { false }
        }

        val PLAN = mapOf("epics" to listOf(
            mapOf("id" to "PAY", "team" to "payments", "title" to "Prices API v2", "stories" to listOf(
                mapOf("id" to "PAY-S1", "title" to "Prices in euro", "repo" to "payments-api", "criteria" to listOf("AC-1 [API] GET /prices has currency"),
                    "depends_on" to emptyList<String>(), "estimate_days" to listOf(2, 3), "tasks" to listOf("Add currency column", "Convert prices")),
                mapOf("id" to "PAY-S2", "title" to "Rates job", "repo" to "payments-api", "criteria" to listOf("AC-1 [API] rates refresh daily"),
                    "depends_on" to listOf("PAY-S1"), "estimate_days" to listOf(1, 2), "tasks" to emptyList<String>()),
            )),
            mapOf("id" to "WEB", "team" to "web", "title" to "Show euro", "stories" to listOf(
                mapOf("id" to "WEB-S1", "title" to "Euro on the product page", "repo" to "web-shop",
                    "criteria" to listOf("AC-1 [WEB] an EU visitor sees euro"), "depends_on" to listOf("PAY-S1"), "estimate_days" to 2,
                    "tasks" to listOf("Price component")),
            )),
        ))
    }

    private lateinit var productPid: String

    @BeforeEach
    fun setUp() {
        WORKFLOWS.forEach { id ->
            engine.extraTemplates += mapOf("id" to id, "name" to "$id (keel product)", "addon" to "product", "keel_rules" to false, "version" to 1,
                "steps" to listOf(mapOf("id" to "a", "kind" to "agent", "name" to "a", "agent" to "product-manager")), "yaml" to "")
        }
        engine.extraRoutes["/product/deck"] = { body ->
            val ini = body!!["initiative"]["id"].asText()
            200 to mapOf("kind" to "deck", "version" to 9, "path" to "initiatives/$ini/deck-v9.html", "sha" to "abc")
        }
        productPid = get("/api/product").andExpect(status().isOk).json()["project_id"].asText()
    }

    @AfterEach
    fun clean() {
        engine.extraTemplates.clear()
        engine.extraRoutes.clear()
        engine.overrides.clear()
        engine.nextThreadIds.clear()
        jira.reset()
        put("/api/settings/general", mapOf("keel_mode" to "auto"))
    }

    // ---- helpers

    private fun nextTid(stage: String): String = "t-$stage-${java.util.UUID.randomUUID().toString().take(8)}".also { engine.nextThreadIds += it }

    private fun ev(type: String, tid: String, data: Map<String, Any?> = emptyMap(), step: String? = "s") =
        mapOf("type" to type, "thread_id" to tid, "project_id" to productPid, "step" to step, "at" to Instant.now().toString(), "data" to data)

    private fun send(vararg events: Map<String, Any?>) =
        post("/internal/events", events.toList(), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)

    private fun detail(id: String): JsonNode = get("/api/initiatives/$id").andExpect(status().isOk).json()

    private fun waitAt(tid: String, title: String, kind: String = "gate", extra: Map<String, Any?> = emptyMap()) {
        val waiting = mapOf("step" to "g", "kind" to kind, "title" to title, "detail" to "") + extra
        engine.overrides[tid] = mapOf("status" to "waiting", "waiting" to waiting)
        send(ev("gate.waiting", tid, mapOf("kind" to kind, "title" to title)))
    }

    private fun doc(tid: String, id: String, kind: String, version: Int, text: String, extra: Map<String, Any?> = emptyMap()) =
        send(ev("product.doc", tid, mapOf("initiative" to id, "kind" to kind, "version" to version, "path" to "initiatives/$id/$kind-v$version.md",
            "sha" to "s$version", "text" to text) + extra))

    private fun decided(tid: String, decision: String = "approve", why: String = "", extra: Map<String, Any?> = emptyMap()) =
        send(ev("gate.decided", tid, mapOf("gate" to "g", "decision" to decision, "why" to why) + extra))

    private fun done(tid: String) = send(ev("thread.done", tid, mapOf("status" to "done")))

    private fun runOf(id: String): String = detail(id)["stage"]["thread_id"].asText()

    private fun repos(): Pair<String, String> {
        val (web, _) = newProject("web-shop")
        val (pay, _) = newProject("payments-api")
        listOf("web", "payments").forEach { delete("/api/teams/$it") }
        post("/api/teams", mapOf("name" to "web", "lead" to "Ana", "owns" to listOf(mapOf("project_id" to web, "glob" to "**")))).andExpect(status().isOk)
        post("/api/teams", mapOf("name" to "payments", "lead" to "Bo", "jira_project" to "PAY", "capacity_days" to 12,
            "owns" to listOf(mapOf("project_id" to pay, "glob" to "**")))).andExpect(status().isOk)
        return web to pay
    }

    /** Through brief, impact, decision (go) and plan, with no questions: the initiative waits at delivery. */
    private fun toDelivery(id: String) {
        for ((stage, kind) in listOf("brief" to "brief", "impact" to "impact", "decision" to "decision", "plan" to "plan")) {
            val tid = runOf(id)
            assertThat(detail(id)["stage"]["stage"].asText()).isEqualTo(stage)
            if (kind == "plan") send(ev("product.plan", tid, mapOf("initiative" to id, "version" to 1, "path" to "initiatives/$id/plan-v1.json",
                "sha" to "p1", "plan" to PLAN, "problems" to emptyList<String>(), "ok" to true, "critical_path" to listOf("PAY-S1", "PAY-S2"),
                "critical_days" to 5, "teams" to mapOf("payments" to listOf(3, 5)), "total_days" to listOf(5, 7),
                "counts" to mapOf("epics" to 2, "stories" to 3, "tasks" to 3))))
            else doc(tid, id, kind, 1, "# The $kind\n\nText of the $kind.")
            waitAt(tid, "Approve the $stage")
            if (stage == "decision") {
                post("/api/initiatives/$id/decide", mapOf("choice" to "go", "option" to "B", "why" to "worth it")).andExpect(status().isOk)
                decided(tid, extra = mapOf("choice" to "go"))
                send(ev("product.decision", tid, mapOf("initiative" to id, "choice" to "go", "option" to "B", "why" to "worth it")))
            } else {
                post("/api/initiatives/$id/approve", emptyMap<String, Any>()).andExpect(status().isOk)
                decided(tid)
            }
            if (kind != "plan") nextTid(listOf("brief", "impact", "decision", "plan").let { it[it.indexOf(stage) + 1] })
            done(tid)
        }
    }

    // ---- tests

    @Test
    fun `keel Product is an add-on, its pages are listed, and every endpoint is refused while it is off`() {
        val f = get("/api/features").andExpect(status().isOk).json()
        assertThat(f["mode"].asText()).isEqualTo("both")
        assertThat(f["modes"].map { it.asText() }).contains("dev", "product", "both")
        assertThat(f["addons"].map { it["name"].asText() + " " + it["version"].asText() }).contains("product 0.1.0-beta.1")
        assertThat(f["screens"].map { it["id"].asText() }).contains("initiatives", "teams")
        val pids = get("/api/projects").json().map { it["id"].asText() }
        assertThat(pids).contains(productPid)
        assertThat(get("/api/projects/$productPid/workflows").json().map { it["id"].asText() }).contains("product-discover")

        put("/api/settings/general", mapOf("keel_mode" to "dev")).andExpect(status().isOk)
        get("/api/initiatives").andExpect(status().isConflict)
        post("/api/initiatives", mapOf("title" to "x", "idea" to "y")).andExpect(status().isConflict)
        get("/api/teams").andExpect(status().isConflict)
        assertThat(get("/api/projects/$productPid/workflows").json().map { it["id"].asText() }).doesNotContain("product-discover")

        put("/api/settings/general", mapOf("keel_mode" to "product")).andExpect(status().isOk)
        assertThat(get("/api/features").json()["parts"]["dev"].asBoolean()).isFalse()
        get("/api/initiatives").andExpect(status().isOk)
    }

    @Test
    fun `an idea goes from brief to delivery and outcome, with questions, a send-back and the decision`() {
        val (web, pay) = repos()
        post("/api/initiatives", mapOf("title" to " ", "idea" to "y")).andExpect(status().isBadRequest)
        post("/api/initiatives", mapOf("title" to "x", "idea" to "")).andExpect(status().isBadRequest)
        post("/api/initiatives", mapOf("title" to "x", "idea" to "y", "repos" to listOf("nope"))).andExpect(status().isNotFound)

        val t1 = nextTid("brief")
        val created = post("/api/initiatives", mapOf("title" to "Euro prices", "idea" to "Show prices in euro to EU visitors.",
            "why_now" to "EU launch in Q1", "owner" to "Mia", "repos" to listOf(web, pay))).andExpect(status().isOk).json()
        val id = created["initiative"]["id"].asText()
        assertThat(id).matches("INI-\\d+")
        assertThat(created["initiative"]["stage"].asText()).isEqualTo("brief")
        assertThat(created["initiative"]["status"].asText()).isEqualTo("running")
        assertThat(created["teams"].map { it.asText() }).containsExactlyInAnyOrder("web", "payments")
        val start = engine.lastBody("/threads")!!
        assertThat(start["workflow"]["id"].asText()).isEqualTo("product-discover")
        assertThat(start["root"].asText()).endsWith("keel-product")
        assertThat(start["request"].asText()).contains("# Initiative $id: Euro prices").contains("Why now: EU launch in Q1").contains("payments-api")
        assertThat(start["data"]["initiative"]["id"].asText()).isEqualTo(id)
        assertThat(start["data"]["repos"].map { it["team"].asText() }).containsExactlyInAnyOrder("web", "payments")
        assertThat(start["data"]["team_list"].asText()).contains("payments (payments)").contains("web (web)")
        post("/api/initiatives/$id/start", emptyMap<String, Any>()).andExpect(status().isConflict)      // a run is on
        post("/api/initiatives/$id/approve", emptyMap<String, Any>()).andExpect(status().isConflict)    // nothing waits yet

        // keel asks first (clarify), the PO answers: the answers go to the flow and stay on the Questions tab
        waitAt(t1, "product manager has 2 questions", "clarify", mapOf("questions" to listOf(
            mapOf("id" to "who", "question" to "Who sees euro?", "why" to "scope", "options" to listOf(mapOf("label" to "EU only", "recommended" to true))),
            mapOf("id" to "metric", "question" to "Which metric?", "options" to listOf(mapOf("label" to "Conversion", "recommended" to true))))))
        val asking = detail(id)
        assertThat(asking["initiative"]["status"].asText()).isEqualTo("waiting")
        assertThat(asking["stage"]["next"].asText()).isEqualTo("Answer keel's questions")
        assertThat(asking["stage"]["waiting"]["kind"].asText()).isEqualTo("clarify")
        post("/api/initiatives/$id/approve", mapOf("answers" to mapOf("who" to "EU and UK"))).andExpect(status().isOk)
        val resumed = engine.lastBody("/threads/$t1/resume")!!
        assertThat(resumed["decision"].asText()).isEqualTo("approve")
        assertThat(resumed["payload"]["answers"]["who"].asText()).isEqualTo("EU and UK")
        decided(t1, why = "answers", extra = mapOf("clarify" to true))
        val qs = detail(id)["questions"]
        assertThat(qs.map { it["text"].asText() + " → " + it["answer"].asText() })
            .containsExactlyInAnyOrder("Who sees euro? → EU and UK", "Which metric? → Conversion")
        assertThat(detail(id)["initiative"]["status"].asText()).isEqualTo("running")

        // brief v1, sent back with a note; v2 approved
        doc(t1, id, "brief", 1, "# Brief\n\nEuro for EU.")
        waitAt(t1, "Approve the brief")
        assertThat(detail(id)["stage"]["next"].asText()).isEqualTo("Approve the brief")
        post("/api/initiatives/$id/send-back", mapOf("note" to " ")).andExpect(status().isBadRequest)
        post("/api/initiatives/$id/send-back", mapOf("note" to "Add the UK")).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads/$t1/resume")!!["decision"].asText()).isEqualTo("reject")
        assertThat(engine.lastBody("/threads/$t1/resume")!!["why"].asText()).isEqualTo("Add the UK")
        decided(t1, "reject", "Add the UK")
        doc(t1, id, "brief", 2, "# Brief\n\nEuro for EU and UK.")
        waitAt(t1, "Approve the brief")
        post("/api/initiatives/$id/approve", mapOf("why" to "good")).andExpect(status().isOk)
        decided(t1, why = "good")
        val t2 = nextTid("impact")
        done(t1)

        // the impact starts by itself, with the approved brief
        val impact = detail(id)
        assertThat(impact["stage"]["stage"].asText()).isEqualTo("impact")
        assertThat(impact["docs"]["brief"]["version"].asInt()).isEqualTo(2)
        assertThat(impact["docs"]["brief"]["versions"].map { it.asInt() }).containsExactly(1, 2)
        assertThat(impact["docs"]["brief"]["approved_at"].isNull).isFalse()
        val impactStart = engine.lastBody("/threads")!!
        assertThat(impactStart["workflow"]["id"].asText()).isEqualTo("product-impact")
        assertThat(impactStart["data"]["docs"]["brief"].asText()).contains("EU and UK")
        assertThat(impactStart["request"].asText()).contains("Who sees euro? → EU and UK")
        assertThat(get("/api/initiatives/$id/docs/brief/1").json()["text"].asText()).contains("Euro for EU.")

        doc(t2, id, "impact", 1, "# Impact", mapOf("repos" to listOf(mapOf("repo" to pay, "risk" to "high"), mapOf("repo" to web, "risk" to "low"))))
        waitAt(t2, "Confirm the impact")
        post("/api/initiatives/$id/approve", emptyMap<String, Any>()).andExpect(status().isOk)
        decided(t2)
        val t3 = nextTid("decision")
        done(t2)

        // the decision: memo, presentation; a disagreement is written down and settled; then go with option B
        assertThat(engine.lastBody("/threads")!!["workflow"]["id"].asText()).isEqualTo("product-decide")
        assertThat(engine.lastBody("/threads")!!["data"]["docs"]["impact_repos"].size()).isEqualTo(2)
        doc(t3, id, "decision", 1, "# Decision memo\n\nOption B.")
        val root = get("/api/product").json()["root"].asText()
        Files.createDirectories(java.nio.file.Paths.get(root, "initiatives", id))
        Files.writeString(java.nio.file.Paths.get(root, "initiatives", id, "deck-v1.html"), "<h1>Euro prices</h1>")
        send(ev("product.doc", t3, mapOf("initiative" to id, "kind" to "deck", "version" to 1, "path" to "initiatives/$id/deck-v1.html", "text" to "")))
        get("/api/initiatives/$id/deck").andExpect(status().isOk).andExpect(content().string("<h1>Euro prices</h1>"))
        waitAt(t3, "Go or not now")
        assertThat(detail(id)["stage"]["next"].asText()).isEqualTo("Go or not now")
        val dis = post("/api/initiatives/$id/disagreements", mapOf("reason" to "Option A is cheaper", "proposal" to "A first", "author" to "Bo"))
            .andExpect(status().isOk).json()
        assertThat(dis["decider"].asText()).isEqualTo("po")
        assertThat(dis["status"].asText()).isEqualTo("open")
        post("/api/initiatives/$id/disagreements/${dis["id"].asText()}/settle", mapOf("outcome" to "B stays", "note" to "A can not do the UK"))
            .andExpect(status().isOk)
        post("/api/initiatives/$id/disagreements/${dis["id"].asText()}/settle", mapOf("outcome" to "again")).andExpect(status().isConflict)
        post("/api/initiatives/$id/decide", mapOf("choice" to "maybe")).andExpect(status().isBadRequest)
        post("/api/initiatives/$id/decide", mapOf("choice" to "go", "option" to "B", "why" to "UK too")).andExpect(status().isOk)
        val decide = engine.lastBody("/threads/$t3/resume")!!
        assertThat(decide["payload"]["choice"].asText()).isEqualTo("go")
        assertThat(decide["payload"]["option"].asText()).isEqualTo("B")
        decided(t3, extra = mapOf("choice" to "go"))
        send(ev("product.decision", t3, mapOf("initiative" to id, "choice" to "go", "option" to "B", "why" to "UK too")))
        val t4 = nextTid("plan")
        done(t3)
        assertThat(detail(id)["initiative"]["option"].asText()).isEqualTo("B")
        assertThat(engine.lastBody("/threads")!!["request"].asText()).contains("Option chosen at the decision: B")

        // the plan; a lead disagrees and the plan runs again with the objection; then agreed
        send(ev("product.plan", t4, mapOf("initiative" to id, "version" to 1, "path" to "initiatives/$id/plan-v1.json", "sha" to "p1",
            "plan" to PLAN, "problems" to emptyList<String>(), "ok" to true, "critical_path" to listOf("PAY-S1", "PAY-S2"), "critical_days" to 5,
            "teams" to mapOf("payments" to listOf(3, 5)), "total_days" to listOf(5, 7), "counts" to mapOf("epics" to 2, "stories" to 3, "tasks" to 3))))
        waitAt(t4, "Agree the plan")
        val objection = post("/api/initiatives/$id/disagreements", mapOf("reason" to "PAY-S2 is too small to be a story", "author" to "Bo"))
            .json()
        assertThat(objection["decider"].asText()).isEqualTo("lead")
        post("/api/initiatives/$id/disagreements/${objection["id"].asText()}/rerun").andExpect(status().isOk)
        assertThat(engine.lastBody("/threads/$t4/resume")!!["why"].asText()).contains("Bo disagrees: PAY-S2 is too small")
        decided(t4, "reject", "objection")
        waitAt(t4, "Agree the plan")
        post("/api/initiatives/$id/approve", emptyMap<String, Any>()).andExpect(status().isOk)
        decided(t4)
        done(t4)

        val delivery = detail(id)
        assertThat(delivery["initiative"]["stage"].asText()).isEqualTo("delivery")
        assertThat(delivery["stage"]["next"].asText()).isEqualTo("Send the stories to Tasks or Jira")
        assertThat(delivery["plan"]["epics"].map { it["id"].asText() }).containsExactlyInAnyOrder("PAY", "WEB")
        assertThat(delivery["plan"]["critical_path"].map { it.asText() }).containsExactly("PAY-S1", "PAY-S2")
        assertThat(delivery["disagreements"].map { it["status"].asText() }).containsExactlyInAnyOrder("settled", "rerun")

        // the stories go to keel Tasks, one per story in its repo's project, with their criteria
        post("/api/initiatives/$id/handoff", mapOf("target" to "fax")).andExpect(status().isBadRequest)
        val sent = post("/api/initiatives/$id/handoff", mapOf("target" to "tasks")).andExpect(status().isOk).json()
        assertThat(sent["tasks"].size()).isEqualTo(3)
        val payTasks = get("/api/projects/$pay/tasks").json()["tasks"]
        assertThat(payTasks.map { it["title"].asText() }).containsExactlyInAnyOrder("PAY-S1 · Prices in euro", "PAY-S2 · Rates job")
        val s1 = payTasks.first { it["title"].asText().startsWith("PAY-S1") }
        assertThat(s1["description"].asText()).contains("```keel-criteria\nAC-1 [API] GET /prices has currency\n```").contains("Part of $id")
            .contains("- Convert prices")
        assertThat(get("/api/projects/$web/tasks").json()["tasks"].single()["title"].asText()).isEqualTo("WEB-S1 · Euro on the product page")
        assertThat(post("/api/initiatives/$id/handoff", mapOf("target" to "tasks")).json()["tasks"].size()).isZero()      // not twice
        assertThat(detail(id)["stage"]["next"].asText()).isEqualTo("The teams build it")
        val board = get("/api/initiatives").json().first { it["id"].asText() == id }
        assertThat(board["progress"]["done"].asInt()).isZero()
        assertThat(board["progress"]["total"].asInt()).isEqualTo(3)
        assertThat(board["option"].asText()).isEqualTo("B")

        // released → outcome checks planned; the metric starts the outcome stage; closing it suggests a lesson to the teams
        post("/api/initiatives/$id/released").andExpect(status().isOk)
        assertThat(detail(id)["follow_ups"].filter { it["kind"].asText() == "outcome" }).hasSize(2)
        post("/api/initiatives/$id/outcome", mapOf("metric" to "")).andExpect(status().isBadRequest)
        val t5 = nextTid("outcome")
        post("/api/initiatives/$id/outcome", mapOf("metric" to "conversion 2.5% (was 2.1%)")).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads")!!["data"]["metric"].asText()).isEqualTo("conversion 2.5% (was 2.1%)")
        doc(t5, id, "outcome", 1, "Conversion went up by 0.4 points.")
        waitAt(t5, "Close the initiative")
        post("/api/initiatives/$id/approve", emptyMap<String, Any>()).andExpect(status().isOk)
        decided(t5)
        done(t5)
        val closed = detail(id)
        assertThat(closed["initiative"]["stage"].asText()).isEqualTo("done")
        assertThat(closed["stage"]["next"].asText()).isEqualTo("Done")
        val payTeam = get("/api/teams/payments").andExpect(status().isOk).json()
        val lesson = payTeam["suggestions"].single()
        assertThat(lesson["page"].asText()).isEqualTo("lessons")
        assertThat(lesson["text"].asText()).contains("Conversion went up")
        val accepted = post("/api/teams/payments/suggestions/${lesson["id"].asText()}", mapOf("accept" to true)).andExpect(status().isOk).json()
        assertThat(accepted["pages"].first { it["name"].asText() == "lessons" }["text"].asText()).contains("Conversion went up")

        // the history tells the whole story, newest first
        val history = closed["history"].map { it["kind"].asText() + ": " + it["text"].asText() }
        assertThat(history.last()).isEqualTo("created: Created the idea")
        assertThat(history).anyMatch { it.startsWith("sent_back: Sent the brief back: Add the UK") }
            .anyMatch { it == "approved: Approved the brief: good" }
            .anyMatch { it.startsWith("decision: Go with option B") }
            .anyMatch { it.startsWith("handoff: Sent 3 stories to keel Tasks") }
            .anyMatch { it == "version: Wrote the presentation v1" }
            .anyMatch { it.startsWith("disagreed: Disagreed with the decision") }
        assertThat(closed["runs"].map { it["stage"].asText() + " " + it["status"].asText() })
            .containsExactlyInAnyOrder("brief done", "impact done", "decision done", "plan done", "outcome done")
    }

    @Test
    fun `the stories go to Jira as epics, stories and sub-tasks with what waits for what`() {
        val (web, pay) = repos()
        put("/api/projects/$pay/jira", mapOf("kind" to "cloud", "base_url" to jira.url, "email" to jira.email, "token" to jira.token))
            .andExpect(status().isOk)
        nextTid("brief")
        val id = post("/api/initiatives", mapOf("title" to "Jira path", "idea" to "Send the plan to Jira.", "repos" to listOf(web, pay)))
            .json()["initiative"]["id"].asText()
        post("/api/initiatives/$id/handoff", mapOf("target" to "jira")).andExpect(status().isConflict)        // no agreed plan yet
        toDelivery(id)
        jira.missingTypes += "Epic"

        val res = post("/api/initiatives/$id/handoff", mapOf("target" to "both")).andExpect(status().isOk).json()
        val notes = res["notes"].map { it.asText() }
        assertThat(notes).anyMatch { it.startsWith("epic PAY: Jira refused an Epic") }
            .anyMatch { it == "epic WEB: the repo's keel project has no Jira connection" }
        val stories = jira.issues.filterValues { it.type == "Story" }
        assertThat(stories.values.map { it.summary }).containsExactlyInAnyOrder("PAY-S1 · Prices in euro", "PAY-S2 · Rates job")
        val s1 = stories.entries.first { it.value.summary.startsWith("PAY-S1") }.key
        val s2 = stories.entries.first { it.value.summary.startsWith("PAY-S2") }.key
        assertThat(s1).startsWith("PAY-")
        assertThat(jira.issues.filterValues { it.type == "Sub-task" }.values.map { it.summary to it.fields["parent"] })
            .containsExactlyInAnyOrder("Add currency column" to s1, "Convert prices" to s1)
        assertThat(jira.links).containsExactly(Triple("Blocks", s2, s1))
        assertThat(stories.getValue(s1).description).contains("```keel-criteria")

        // "both": the keel tasks carry the ticket keys; the web story has no ticket but still becomes a task
        val payTasks = get("/api/projects/$pay/tasks").json()["tasks"]
        assertThat(payTasks.map { it["external_key"]?.asText() }).containsExactlyInAnyOrder(s1, s2)
        assertThat(res["tasks"].size()).isEqualTo(3)
        val plan = detail(id)["plan"]["epics"].flatMap { e -> e["stories"].map { it["id"].asText() to it["jira_key"]?.takeIf { k -> !k.isNull }?.asText() } }
        assertThat(plan).contains("PAY-S1" to s1, "WEB-S1" to null)
    }

    @Test
    fun `questions to keel and to people, follow-ups that remind, and a parked idea that comes back`() {
        nextTid("brief")
        val id = post("/api/initiatives", mapOf("title" to "Gift cards", "idea" to "Sell gift cards.", "start" to false)).json()["initiative"]["id"].asText()
        assertThat(detail(id)["stage"]["next"].asText()).isEqualTo("Start discovery")
        post("/api/initiatives/$id/start", mapOf("stage" to "impact")).andExpect(status().isConflict)        // no repos to read
        post("/api/initiatives/$id/start", mapOf("stage" to "launch")).andExpect(status().isBadRequest)

        // keel answers from the documents (the fake model can not: it says so)
        val k = post("/api/initiatives/$id/questions", mapOf("text" to "What does it cost?")).andExpect(status().isOk).json()
        assertThat(k["status"].asText()).isEqualTo("answered")
        assertThat(k["answer"].asText()).contains("fake model")

        // a person: an open question with a follow-up in 2 days
        val q = post("/api/initiatives/$id/questions", mapOf("text" to "Can finance book gift cards?", "to" to "legal")).json()
        assertThat(q["status"].asText()).isEqualTo("open")
        val fu = detail(id)["follow_ups"].single { it["kind"].asText() == "question" }
        assertThat(fu["ref"].asText()).isEqualTo(q["id"].asText())
        val before = post("/api/product/follow-ups/check?at=${Instant.now()}").andExpect(status().isOk).json()
        assertThat(before["reminded"].map { it.asText() }).doesNotContain(fu["id"].asText())
        val later = Instant.now().plus(3, ChronoUnit.DAYS)
        val check = post("/api/product/follow-ups/check?at=$later").json()
        assertThat(check["reminded"].map { it.asText() }).contains(fu["id"].asText())
        val inbox = get("/api/notifications").json().toString()
        assertThat(inbox).contains("$id: Legal answers: Can finance book gift cards?")
        post("/api/initiatives/$id/questions/${q["id"].asText()}/answer", mapOf("answer" to "Yes, as a liability", "by" to "Lea")).andExpect(status().isOk)
        assertThat(detail(id)["follow_ups"].single { it["kind"].asText() == "question" }["done_at"].isNull).isFalse()

        // a manual follow-up, done by hand
        post("/api/initiatives/$id/follow-ups", mapOf("text" to "Ask the shop team", "due_at" to "tomorrow")).andExpect(status().isBadRequest)
        val manual = post("/api/initiatives/$id/follow-ups", mapOf("text" to "Ask the shop team", "due_at" to "2027-01-10")).json()
        post("/api/initiatives/$id/follow-ups/${manual["id"].asText()}/done").andExpect(status().isOk)

        // Not now: parked until a date; the follow-up check brings it back
        post("/api/initiatives/$id/park", mapOf("revisit" to "2026-01-01", "why" to "after the launch")).andExpect(status().isOk)
        assertThat(detail(id)["initiative"]["status"].asText()).isEqualTo("parked")
        assertThat(detail(id)["stage"]["next"].asText()).isEqualTo("Comes back 2026-01-01")
        val back = post("/api/product/follow-ups/check").json()
        assertThat(back["revisited"].map { it.asText() }).contains(id)
        assertThat(detail(id)["initiative"]["status"].asText()).isEqualTo("ready")
        post("/api/initiatives/$id/unpark").andExpect(status().isConflict)
    }

    @Test
    fun `a stopped or failed run does not move the stage on, and the presentation can be rebuilt`() {
        val t1 = nextTid("brief")
        val id = post("/api/initiatives", mapOf("title" to "Dark mode", "idea" to "A dark theme.")).json()["initiative"]["id"].asText()
        send(ev("thread.failed", t1, mapOf("error" to "the model said no")))
        val failed = detail(id)
        assertThat(failed["initiative"]["status"].asText()).isEqualTo("failed")
        assertThat(failed["stage"]["next"].asText()).contains("failed")
        assertThat(failed["runs"].single()["status"].asText()).isEqualTo("failed")

        val t2 = nextTid("brief")
        post("/api/initiatives/$id/rerun", mapOf("note" to "Try again")).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads")!!["data"]["note"].asText()).isEqualTo("Try again")
        post("/api/initiatives/$id/stop").andExpect(status().isOk)
        send(ev("thread.done", t2, mapOf("status" to "stopped")))
        val stopped = detail(id)
        assertThat(stopped["initiative"]["stage"].asText()).isEqualTo("brief")
        assertThat(stopped["initiative"]["status"].asText()).isEqualTo("ready")
        assertThat(stopped["runs"].map { it["status"].asText() }).containsExactlyInAnyOrder("failed", "stopped")

        get("/api/initiatives/$id/deck").andExpect(status().isNotFound)
        val deck = post("/api/initiatives/$id/deck").andExpect(status().isOk).json()
        assertThat(deck["version"].asInt()).isEqualTo(9)
        assertThat(engine.lastBody("/product/deck")!!["initiative"]["title"].asText()).isEqualTo("Dark mode")
    }
}
