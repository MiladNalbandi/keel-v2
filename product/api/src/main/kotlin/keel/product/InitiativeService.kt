package keel.product

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.engine.EngineClient
import keel.api.events.EngineEvent
import keel.api.flow.FlowService
import keel.api.notifications.NotificationService
import keel.api.projects.ProjectService
import keel.api.connections.SecretService
import keel.api.settings.SettingsService
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import java.time.Instant
import java.time.temporal.ChronoUnit

data class NewInitiative(
    val title: String = "",
    val idea: String = "",
    val whyNow: String? = null,
    val outcomeHope: String? = null,
    val owner: String? = null,
    val repos: List<String> = emptyList(),
    /** start discovery right away (the dialog's "Start discovery") */
    val start: Boolean = true,
)

data class InitiativePatch(val title: String? = null, val idea: String? = null, val whyNow: String? = null, val outcomeHope: String? = null,
                           val owner: String? = null, val repos: List<String>? = null)

data class GateBody(val why: String? = null, val answers: Map<String, String>? = null)
data class NoteBody(val note: String = "")
data class DecideBody(val choice: String = "", val option: String? = null, val why: String? = null, val revisit: String? = null)
data class RerunBody(val stage: String? = null, val note: String? = null)
data class ClarifyBody(val about: String? = null, val text: String = "", val to: String = "keel")
data class AnswerBody(val answer: String = "", val by: String? = null)
data class DisagreeBody(val stage: String? = null, val reason: String = "", val proposal: String? = null, val decider: String? = null, val author: String? = null)
data class SettleBody(val outcome: String = "", val note: String? = null, val by: String? = null)
data class FollowUpBody(val text: String = "", val dueAt: String = "", val owner: String? = null, val repeatDays: Int? = null)
data class ParkBody(val revisit: String? = null, val why: String? = null)
data class MetricBody(val metric: String = "")

data class DocView(val kind: String, val version: Int, val versions: List<Int>, val path: String, val text: String, val data: Any?,
                   val approvedAt: String?, val createdAt: String)

data class StageView(val stage: String, val status: String, val threadId: String?, val waiting: JsonNode?, val next: String)

data class BoardItem(val id: String, val title: String, val stage: String, val status: String, val waiting: Boolean, val teams: List<String>,
                     val option: String?, val progress: Progress?, val next: String, val updatedAt: String, val revisitAt: String?)

data class Progress(val done: Int, val total: Int)

data class InitiativeDetail(
    val initiative: Initiative,
    val stage: StageView,
    val docs: Map<String, DocView>,
    val questions: List<ProductQuestion>,
    val disagreements: List<Disagreement>,
    val runs: List<ProductRun>,
    val history: List<ProductEvent>,
    val followUps: List<FollowUp>,
    val plan: PlanView?,
    val repos: List<RepoOwner>,
    val teams: List<String>,
)

/**
 * keel Product's initiatives: an idea goes through its stages (brief, impact, decision, plan) as keel flows on the
 * product repo; people approve, send back, decide, ask, answer and disagree; the documents, the history and the
 * follow-ups are kept here. The flows' events come back through [onEngineEvent].
 */
@Service
class InitiativeService(
    private val store: ProductStore,
    private val repo: ProductRepo,
    private val teams: TeamService,
    private val flows: FlowService,
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val notifications: NotificationService,
    private val settings: SettingsService,
    private val secrets: SecretService,
    private val delivery: DeliveryService,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    companion object {
        val WORKFLOWS = linkedMapOf("brief" to "product-discover", "impact" to "product-impact", "decision" to "product-decide",
            "plan" to "product-plan", "outcome" to "product-outcome")
        val DOC_OF = mapOf("brief" to "brief", "impact" to "impact", "decision" to "decision", "plan" to "plan", "outcome" to "outcome")
        const val YOU = "you"
        const val KEEL = "keel"
    }

    // ---------------------------------------------------------------- reading

    fun require(id: String): Initiative = store.initiative(id) ?: throw NotFound("No initiative $id")

    fun board(): List<BoardItem> = store.initiatives().map { i ->
        val run = store.activeRun(i.id)
        val plan = store.latest(i.id, "plan")
        val items = plan?.let { store.items(i.id, it.version) }.orEmpty()
        val progress = if (items.any { it.taskId != null }) delivery.progress(items).let { Progress(it.first, it.second) } else null
        BoardItem(i.id, i.title, i.stage, i.status, run?.status == "waiting", teamsOf(i), i.option, progress, next(i, run?.status == "waiting", null),
            i.updatedAt, i.revisitAt)
    }

    fun detail(id: String): InitiativeDetail {
        val i = require(id)
        val run = store.activeRun(id)
        val waiting = run?.takeIf { it.status == "waiting" }?.let { r -> runCatching { engine.thread(r.threadId).get("waiting") }.getOrNull() }
            ?.takeIf { !it.isNull }
        val all = store.docs(id)
        val docs = all.groupBy { it.kind }.mapValues { (_, list) ->
            val d = list.maxBy { it.version }
            DocView(d.kind, d.version, list.map { it.version }.sorted(), d.path, d.text, d.data, d.approvedAt, d.createdAt)
        }
        val plan = store.latest(id, "plan")?.let { delivery.planView(i, it) }
        return InitiativeDetail(i, StageView(i.stage, i.status, run?.threadId, waiting, next(i, run?.status == "waiting", waiting)), docs,
            store.questions(id), store.disagreements(id), store.runs(id), store.events(id), store.followUps(id), plan,
            teams.repoOwners().filter { it.projectId in i.repos }, teamsOf(i))
    }

    fun doc(id: String, kind: String, version: Int): ProductDoc = store.doc(id, kind, version) ?: throw NotFound("No $kind v$version for $id")

    private fun teamsOf(i: Initiative): List<String> {
        val plan = store.latest(i.id, "plan")
        val fromPlan = plan?.let { store.items(i.id, it.version) }.orEmpty().mapNotNull { it.team }
        if (fromPlan.isNotEmpty()) return fromPlan.distinct()
        return i.repos.flatMap { pid -> teams.ownersOf(pid).map { it.id } }.distinct()
    }

    private fun next(i: Initiative, waiting: Boolean, gate: JsonNode?): String = when {
        i.status == "parked" -> "Comes back ${i.revisitAt?.take(10) ?: "later"}"
        i.status == "failed" -> "A run failed: open it and run the stage again"
        i.status == "done" -> "Done"
        waiting && gate?.path("kind")?.asText() == "clarify" -> "Answer keel's questions"
        waiting && i.stage == "brief" -> "Approve the brief"
        waiting && i.stage == "impact" -> "The leads confirm the impact"
        waiting && i.stage == "decision" -> "Go or not now"
        waiting && i.stage == "plan" -> "The leads agree the plan"
        waiting && i.stage == "outcome" -> "Close the initiative"
        i.status == "running" -> "keel works on the ${i.stage}"
        i.stage == "idea" -> "Start discovery"
        i.stage == "delivery" && i.status == "ready" && !handedOff(i) -> "Send the stories to Tasks or Jira"
        i.stage == "delivery" -> "The teams build it"
        i.stage == "outcome" -> "Enter the metric after the release"
        else -> "Start the ${i.stage}"
    }

    private fun handedOff(i: Initiative): Boolean =
        store.latest(i.id, "plan")?.let { p -> store.items(i.id, p.version).any { it.taskId != null || it.jiraKey != null } } ?: false

    // ---------------------------------------------------------------- writing

    fun create(b: NewInitiative): InitiativeDetail {
        val title = b.title.trim()
        val idea = b.idea.trim()
        if (title.isEmpty()) throw BadRequest("Give the initiative a short title")
        if (idea.isEmpty()) throw BadRequest("Write the idea", "One or two sentences: what is the problem, and what should change?")
        b.repos.forEach { projects.require(it) }
        val n = store.nextNumber()
        val now = Time.now()
        val i = Initiative("INI-$n", n, title.take(200), idea.take(4000), b.whyNow?.trim(), b.outcomeHope?.trim(), b.owner?.trim()?.ifEmpty { null },
            "idea", "new", null, b.repos.distinct(), null, null, now, now)
        store.insert(i)
        store.event(i.id, YOU, "created", "Created the idea", mapOf("repos" to i.repos))
        if (b.start) startStage(i.id, "brief", "first run")
        return detail(i.id)
    }

    fun patch(id: String, b: InitiativePatch): InitiativeDetail {
        val i = require(id)
        b.repos?.forEach { projects.require(it) }
        store.update(i.copy(title = b.title?.trim()?.ifEmpty { null } ?: i.title, idea = b.idea?.trim()?.ifEmpty { null } ?: i.idea,
            whyNow = b.whyNow ?: i.whyNow, outcomeHope = b.outcomeHope ?: i.outcomeHope, owner = b.owner ?: i.owner, repos = b.repos ?: i.repos))
        store.event(id, YOU, "edited", "Changed the idea")
        return detail(id)
    }

    /** Runs a stage as a keel flow on the product repo; its events come back through [onEngineEvent]. */
    fun startStage(id: String, stage: String, reason: String, note: String? = null, objection: String? = null, metric: String? = null): ProductRun {
        val wid = WORKFLOWS[stage] ?: throw BadRequest("Unknown stage $stage", "One of ${WORKFLOWS.keys.joinToString()}.")
        val i = require(id)
        store.activeRun(id)?.let { throw Conflict("The ${it.stage} of $id is ${if (it.status == "waiting") "waiting for you" else "running"}",
            "Answer it, or stop that run first.") }
        if (stage == "impact" && i.repos.isEmpty()) throw Conflict("$id has no repos for keel to read", "Add the repos of this initiative first.")
        val pid = repo.projectId()
        val data = stageData(i, stage, note, objection, metric)
        val started = flows.start(pid, wid, "${i.id} · $stage · ${i.title}".take(200), null, allowFake = true, allowDirty = true,
            request = context(i, stage), options = data, where = "shared", ownBranch = false)
        val tid = started.get("thread_id")?.asText()?.takeIf { it.isNotBlank() } ?: throw Conflict("The engine did not say which flow it started")
        val run = ProductRun(tid, id, stage, wid, "running", reason, Time.now(), null)
        store.saveRun(run)
        store.update(i.copy(stage = stage, status = "running", metric = metric ?: i.metric))
        store.event(id, KEEL, "run", "Started the $stage ($reason)", mapOf("thread_id" to tid, "stage" to stage))
        return run
    }

    fun start(id: String, stage: String?): InitiativeDetail {
        val i = require(id)
        val s = stage ?: when (i.stage) {
            "idea" -> "brief"
            else -> i.stage
        }
        startStage(id, s, if (i.stage == s && store.runs(id).any { it.stage == s }) "run again" else "first run")
        return detail(id)
    }

    private fun waitingRun(id: String): ProductRun =
        store.activeRun(id)?.takeIf { it.status == "waiting" } ?: throw Conflict("Nothing of $id waits for you right now")

    fun approve(id: String, b: GateBody): InitiativeDetail {
        val run = waitingRun(id)
        val gate = runCatching { engine.thread(run.threadId).get("waiting") }.getOrNull()
        val payload = b.answers?.takeIf { it.isNotEmpty() }?.let { mapOf("answers" to it) }
        flows.resume(run.threadId, "approve", b.why?.trim()?.ifEmpty { null }, payload)
        if (gate?.path("kind")?.asText() == "clarify") keepAnswers(id, run.stage, gate.path("questions"), b.answers.orEmpty(), b.why)
        return detail(id)
    }

    /** keel's questions at a clarify gate and the answers given, kept on the Questions tab. */
    private fun keepAnswers(id: String, stage: String, questions: JsonNode?, answers: Map<String, String>, why: String?) {
        val now = Time.now()
        questions?.forEach { q ->
            val qid = q.path("id").asText()
            val recommended = q.path("options").firstOrNull { it.path("recommended").asBoolean() }?.path("label")?.asText()
            val answer = answers[qid]?.takeIf { it.isNotBlank() } ?: why?.takeIf { it.isNotBlank() } ?: recommended ?: "keel's recommended option"
            store.saveQuestion(ProductQuestion(store.newId("q"), id, stage, "keel-asked", YOU, q.path("why").asText().ifBlank { null },
                q.path("question").asText(), answer, YOU, "answered", now, now))
        }
        store.event(id, YOU, "answer", "Answered keel's ${questions?.size() ?: 0} question(s)", mapOf("answers" to answers))
    }

    fun sendBack(id: String, b: NoteBody): InitiativeDetail {
        val note = b.note.trim().ifEmpty { throw BadRequest("Say what to change", "Your words go to keel and into the history.") }
        val run = waitingRun(id)
        if (run.stage == "decision") flows.resume(run.threadId, "approve", note, mapOf("choice" to "back"))
        else flows.resume(run.threadId, "reject", note, null)
        return detail(id)
    }

    fun decide(id: String, b: DecideBody): InitiativeDetail {
        if (b.choice !in setOf("go", "not_now")) throw BadRequest("choice is go or not_now")
        val run = waitingRun(id)
        if (run.stage != "decision") throw Conflict("$id does not wait for a decision", "It waits at the ${run.stage}.")
        flows.resume(run.threadId, "approve", b.why?.trim()?.ifEmpty { null },
            mapOf("choice" to b.choice, "option" to b.option, "revisit" to b.revisit).filterValues { it != null })
        return detail(id)
    }

    /** Runs a stage again: a waiting gate takes it back (same flow, the note as feedback); otherwise a new run. */
    fun rerun(id: String, b: RerunBody): InitiativeDetail {
        val i = require(id)
        val stage = b.stage ?: i.stage
        val run = store.activeRun(id)
        if (run != null && run.status == "waiting" && run.stage == stage) {
            sendBack(id, NoteBody(b.note?.trim()?.ifEmpty { null } ?: "Run this again: something changed since the last version."))
        } else {
            startStage(id, stage, "run again", note = b.note)
        }
        return detail(id)
    }

    fun clarify(id: String, b: ClarifyBody): ProductQuestion {
        val i = require(id)
        val text = b.text.trim().ifEmpty { throw BadRequest("Write the question") }
        val to = b.to.trim().lowercase().ifEmpty { "keel" }
        val now = Time.now()
        if (to == "keel") {
            val answer = askKeel(i, b.about, text)
            val q = ProductQuestion(store.newId("q"), id, i.stage, "keel", null, b.about, text, answer, KEEL, "answered", now, Time.now())
            store.saveQuestion(q)
            store.event(id, YOU, "question", "Asked keel: $text", mapOf("question" to q.id))
            return q
        }
        val role = if (to in setOf("po", "pm", "lead", "dev", "legal", "architect")) to else "person"
        val q = ProductQuestion(store.newId("q"), id, i.stage, role, if (role == "person") b.to.trim() else null, b.about, text, null, null, "open", now, null)
        store.saveQuestion(q)
        store.event(id, YOU, "question", "Asked ${who(q)}: $text", mapOf("question" to q.id))
        store.saveFollowUp(FollowUp(store.newId("fu"), id, "question", "${who(q).replaceFirstChar { it.uppercase() }} answers: ${text.take(80)}",
            q.askedTo ?: role, inDays(2), 2, q.id, null, null, now))
        notifications.create("review", repo.projectId(), "A question on $id for ${who(q)}", text.take(300), "/initiatives/$id")
        return q
    }

    private fun who(q: ProductQuestion) = when (q.role) {
        "po" -> "the PO"
        "pm" -> "the PM"
        "lead" -> "a lead"
        "dev" -> "a developer"
        "legal" -> "Legal"
        "architect" -> "the architect"
        "person" -> q.askedTo ?: "a person"
        else -> q.role
    }

    private fun askKeel(i: Initiative, about: String?, text: String): String {
        val model = settings.general().defaultModel
        if (model.provider == "fake") return "The fake model cannot answer questions. Pick a real model in Connections, or ask a person."
        val prompt = buildString {
            append(context(i, i.stage)).append("\n\n")
            if (!about.isNullOrBlank()) append("The question is about this text:\n").append(about.take(2000)).append("\n\n")
            append("Question: ").append(text).append("\nAnswer in a few short sentences, in plain words. Say so when the documents do not tell.")
        }
        val res = runCatching {
            engine.post("/agents/ask", mapOf("model" to model, "system" to "You answer questions about one product initiative, from its documents.",
                "prompt" to prompt, "keys" to secrets.engineKeys(model.provider, model.mode), "timeout" to 120), long = true)
        }.getOrElse { return "keel could not reach the engine: ${it.message?.take(200)}" }
        return res.path("text").asText().trim().ifEmpty { "keel had no answer (${res.path("error").asText("no reason").take(200)})." }
    }

    fun answer(id: String, qid: String, b: AnswerBody): ProductQuestion {
        val q = store.question(qid)?.takeIf { it.initiativeId == id } ?: throw NotFound("No question $qid on $id")
        val text = b.answer.trim().ifEmpty { throw BadRequest("Write the answer") }
        val done = q.copy(answer = text, answeredBy = b.by?.trim()?.ifEmpty { null } ?: YOU, status = "answered", answeredAt = Time.now())
        store.saveQuestion(done)
        store.closeFollowUps(id, qid)
        store.event(id, done.answeredBy!!, "answer", "Answered: ${q.text.take(80)} → ${text.take(120)}", mapOf("question" to qid))
        return done
    }

    fun disagree(id: String, b: DisagreeBody): Disagreement {
        val i = require(id)
        val reason = b.reason.trim().ifEmpty { throw BadRequest("Say why you disagree") }
        val stage = b.stage ?: i.stage
        val decider = b.decider?.trim()?.ifEmpty { null } ?: if (stage in setOf("brief", "decision")) "po" else "lead"
        val d = Disagreement(store.newId("dis"), id, stage, b.author?.trim()?.ifEmpty { null } ?: YOU, reason, b.proposal?.trim()?.ifEmpty { null },
            decider, "open", null, null, Time.now(), null)
        store.saveDisagreement(d)
        store.event(id, d.author, "disagreed", "Disagreed with the $stage: ${reason.take(160)}", mapOf("disagreement" to d.id))
        notifications.create("review", repo.projectId(), "A disagreement on $id", "${d.author}: ${reason.take(240)}", "/initiatives/$id")
        return d
    }

    /** Settle by running the stage again with the objection as input: both sides see the new version. */
    fun rerunWithObjection(id: String, did: String): InitiativeDetail {
        val d = store.disagreement(did)?.takeIf { it.initiativeId == id } ?: throw NotFound("No disagreement $did on $id")
        if (d.status != "open") throw Conflict("This disagreement is ${d.status} already")
        val objection = "${d.author} disagrees: ${d.reason}" + (d.proposal?.let { " Their proposal: $it" } ?: "")
        val run = store.activeRun(id)
        if (run != null && run.status == "waiting" && run.stage == d.stage) {
            if (run.stage == "decision") flows.resume(run.threadId, "approve", objection, mapOf("choice" to "back"))
            else flows.resume(run.threadId, "reject", objection, null)
        } else {
            startStage(id, d.stage, "objection", objection = objection, note = objection)
        }
        store.saveDisagreement(d.copy(status = "rerun", outcome = "ran the ${d.stage} again with the objection", decidedAt = Time.now()))
        store.event(id, YOU, "settled", "Ran the ${d.stage} again with the objection", mapOf("disagreement" to did))
        return detail(id)
    }

    fun settle(id: String, did: String, b: SettleBody): Disagreement {
        val d = store.disagreement(did)?.takeIf { it.initiativeId == id } ?: throw NotFound("No disagreement $did on $id")
        if (d.status != "open") throw Conflict("This disagreement is ${d.status} already")
        val outcome = b.outcome.trim().ifEmpty { throw BadRequest("Say what was decided") }
        val done = d.copy(status = "settled", outcome = outcome + (b.note?.takeIf { it.isNotBlank() }?.let { ": $it" } ?: ""),
            decidedBy = b.by?.trim()?.ifEmpty { null } ?: YOU, decidedAt = Time.now())
        store.saveDisagreement(done)
        store.event(id, done.decidedBy!!, "settled", "Decided: ${done.outcome}", mapOf("disagreement" to did))
        return done
    }

    fun addFollowUp(id: String, b: FollowUpBody): FollowUp {
        require(id)
        val text = b.text.trim().ifEmpty { throw BadRequest("Write what to follow up") }
        val due = runCatching { Instant.parse(if (b.dueAt.length == 10) "${b.dueAt}T09:00:00Z" else b.dueAt).toString() }
            .getOrElse { throw BadRequest("due_at is a date: 2026-11-08") }
        val f = FollowUp(store.newId("fu"), id, "manual", text, b.owner?.trim()?.ifEmpty { null }, due, b.repeatDays, null, null, null, Time.now())
        store.saveFollowUp(f)
        store.event(id, YOU, "follow_up", "Follow up on ${due.take(10)}: $text")
        return f
    }

    fun doneFollowUp(id: String, fid: String): FollowUp {
        val f = store.followUp(fid)?.takeIf { it.initiativeId == id } ?: throw NotFound("No follow-up $fid on $id")
        val done = f.copy(doneAt = Time.now())
        store.saveFollowUp(done)
        return done
    }

    fun park(id: String, b: ParkBody): InitiativeDetail {
        val i = require(id)
        store.activeRun(id)?.let { runCatching { flows.stop(it.threadId) } }
        parkNow(i, b.revisit, b.why, YOU)
        return detail(id)
    }

    private fun parkNow(i: Initiative, revisit: String?, why: String?, actor: String) {
        val at = revisit?.let { runCatching { Instant.parse(if (it.length == 10) "${it}T09:00:00Z" else it).toString() }.getOrNull() } ?: inDays(90)
        store.update(i.copy(status = "parked", revisitAt = at))
        store.saveFollowUp(FollowUp(store.newId("fu"), i.id, "revisit", "Look again at ${i.title}", i.owner, at, null, "revisit", null, null, Time.now()))
        store.event(i.id, actor, "decision", "Not now: comes back on ${at.take(10)}" + (why?.takeIf { it.isNotBlank() }?.let { ". $it" } ?: ""))
    }

    fun unpark(id: String): InitiativeDetail {
        val i = require(id)
        if (i.status != "parked") throw Conflict("$id is not parked")
        store.update(i.copy(status = "ready", revisitAt = null))
        store.closeFollowUps(id, "revisit")
        store.event(id, YOU, "decision", "Back from Not now")
        return detail(id)
    }

    fun released(id: String): InitiativeDetail {
        val i = require(id)
        store.update(i.copy(stage = "outcome", status = "ready"))
        listOf(28, 56).forEach { d ->
            store.saveFollowUp(FollowUp(store.newId("fu"), id, "outcome", "Outcome check, ${d / 7} weeks after the release", i.owner, inDays(d.toLong()),
                null, "outcome", null, null, Time.now()))
        }
        store.event(id, YOU, "released", "Released: the outcome checks are planned in 4 and 8 weeks")
        return detail(id)
    }

    fun outcome(id: String, b: MetricBody): InitiativeDetail {
        val metric = b.metric.trim().ifEmpty { throw BadRequest("Enter the metric now", "For example: 2.5%") }
        startStage(id, "outcome", "the metric after the release", metric = metric)
        store.event(id, YOU, "outcome", "Entered the metric: $metric")
        return detail(id)
    }

    fun stop(id: String): InitiativeDetail {
        val run = store.activeRun(id) ?: throw Conflict("Nothing of $id runs")
        flows.stop(run.threadId)
        store.saveRun(run.copy(status = "stopped", endedAt = Time.now()))
        store.update(require(id).copy(status = "ready"))
        store.event(id, YOU, "run", "Stopped the ${run.stage}")
        return detail(id)
    }

    private fun inDays(n: Long): String = Instant.now().plus(n, ChronoUnit.DAYS).truncatedTo(ChronoUnit.SECONDS).toString()

    // ---------------------------------------------------------------- the flows' events

    fun onEngineEvent(e: EngineEvent) {
        val run = store.run(e.threadId)
        val ini = (e.data["initiative"] as? String)?.let { store.initiative(it) } ?: run?.let { store.initiative(it.initiativeId) } ?: return
        when (e.type) {
            "product.doc" -> {
                val kind = e.data["kind"] as? String ?: return
                val version = (e.data["version"] as? Number)?.toInt() ?: return
                store.saveDoc(ProductDoc(ini.id, kind, version, e.data["path"] as? String ?: "", e.data["sha"] as? String, e.data["text"] as? String ?: "",
                    e.data["repos"]?.let { mapOf("repos" to it) }, e.threadId.ifBlank { null }, Time.now(), null))
                store.event(ini.id, KEEL, "version", "Wrote the ${if (kind == "deck") "presentation" else kind} v$version", mapOf("kind" to kind, "version" to version))
            }
            "product.plan" -> {
                val version = (e.data["version"] as? Number)?.toInt() ?: return
                store.saveDoc(ProductDoc(ini.id, "plan", version, e.data["path"] as? String ?: "", e.data["sha"] as? String, planText(e.data),
                    e.data, e.threadId, Time.now(), null))
                delivery.saveItems(ini, version, e.data["plan"])
                val ok = e.data["ok"] == true
                store.event(ini.id, KEEL, "version", "Wrote the plan v$version" + if (ok) "" else " (with problems)", mapOf("kind" to "plan", "version" to version))
            }
            "product.decision" -> {
                val choice = e.data["choice"] as? String ?: return
                if (choice == "go") {
                    val option = (e.data["option"] as? String)?.ifBlank { null }
                    store.update(ini.copy(option = option))
                    store.event(ini.id, YOU, "decision", "Go" + (option?.let { " with option $it" } ?: "") +
                        ((e.data["why"] as? String)?.takeIf { it.isNotBlank() }?.let { ": $it" } ?: ""))
                } else if (choice == "not_now") {
                    parkNow(ini, e.data["revisit"] as? String, e.data["why"] as? String, YOU)
                }
            }
            "gate.waiting" -> {
                if (run == null) return
                store.saveRun(run.copy(status = "waiting"))
                store.update(ini.copy(status = "waiting"))
                val title = e.data["title"]?.toString() ?: "a gate"
                store.event(ini.id, KEEL, "waiting", "Waits for you: $title", mapOf("thread_id" to e.threadId, "step" to e.step))
                store.saveFollowUp(FollowUp(store.newId("fu"), ini.id, "gate", "Answer: $title", ini.owner, inDays(2), 2, e.threadId, null, null, Time.now()))
            }
            "gate.decided" -> {
                if (run == null || e.data["by"] == "engine") return
                store.saveRun(run.copy(status = "running"))
                store.update(ini.copy(status = "running"))
                store.closeFollowUps(ini.id, e.threadId)
                val choice = e.data["choice"] as? String
                val decision = e.data["decision"] as? String
                val why = (e.data["why"] as? String)?.takeIf { it.isNotBlank() }
                val text = when {
                    e.data["clarify"] == true -> "Sent the answers to keel"
                    choice == "back" -> "Sent the ${run.stage} back" + (why?.let { ": $it" } ?: "")
                    choice != null -> null      // product.decision tells it
                    decision == "reject" -> "Sent the ${run.stage} back" + (why?.let { ": $it" } ?: "")
                    else -> "Approved the ${run.stage}" + (why?.let { ": $it" } ?: "")
                }
                if (text != null) store.event(ini.id, YOU, if (decision == "reject" || choice == "back") "sent_back" else "approved", text)
            }
            "thread.done" -> {
                if (run == null || run.status in setOf("done", "stopped", "failed")) return
                if (e.data["status"] == "stopped") {
                    store.saveRun(run.copy(status = "stopped", endedAt = Time.now()))
                    if (ini.status != "parked") store.update(ini.copy(status = "ready"))
                    return
                }
                store.saveRun(run.copy(status = "done", endedAt = Time.now()))
                DOC_OF[run.stage]?.let { store.approveLatest(ini.id, it) }
                advance(store.initiative(ini.id) ?: return, run.stage)
            }
            "thread.failed" -> {
                if (run == null || run.status in setOf("done", "stopped", "failed")) return
                store.saveRun(run.copy(status = "failed", endedAt = Time.now()))
                store.update(ini.copy(status = "failed"))
                store.event(ini.id, KEEL, "run", "The ${run.stage} failed: ${(e.data["error"] as? String)?.take(200) ?: "a step failed"}")
            }
        }
    }

    private fun planText(d: Map<String, Any?>): String {
        @Suppress("UNCHECKED_CAST")
        val counts = d["counts"] as? Map<String, Any?> ?: emptyMap()
        @Suppress("UNCHECKED_CAST")
        val problems = d["problems"] as? List<Any?> ?: emptyList()
        return "${counts["epics"]} epics · ${counts["stories"]} stories · ${counts["tasks"]} tasks" +
            if (problems.isNotEmpty()) "\n\nProblems:\n" + problems.joinToString("\n") { "- $it" } else ""
    }

    /** What comes after a stage ended. */
    private fun advance(i: Initiative, stage: String) {
        if (i.status == "parked") return
        try {
            when (stage) {
                "brief" -> if (i.repos.isNotEmpty()) startStage(i.id, "impact", "the brief is approved")
                    else startStage(i.id, "decision", "the brief is approved (no repos to read)")
                "impact" -> startStage(i.id, "decision", "the leads confirmed the impact")
                "decision" -> startStage(i.id, "plan", "go" + (i.option?.let { " with option $it" } ?: ""))
                "plan" -> {
                    store.update(i.copy(stage = "delivery", status = "ready"))
                    store.event(i.id, KEEL, "run", "The plan is agreed: send the stories to Tasks or Jira")
                }
                "outcome" -> {
                    store.update(i.copy(stage = "done", status = "done"))
                    store.event(i.id, KEEL, "outcome", "The initiative is closed")
                    delivery.learn(i)
                }
            }
        } catch (ex: Exception) {
            log.warn("keel Product could not start the stage after {} of {}: {}", stage, i.id, ex.message)
            store.update((store.initiative(i.id) ?: i).copy(status = "ready"))
            store.event(i.id, KEEL, "run", "Could not start the next stage: ${ex.message?.take(200)}")
        }
    }

    // ---------------------------------------------------------------- what the agents get

    private fun stageData(i: Initiative, stage: String, note: String?, objection: String?, metric: String?): Map<String, Any?> {
        val repos = i.repos.mapNotNull { projects.find(it) }.map { p ->
            val t = teams.ownersOf(p.id).firstOrNull()
            mapOf("id" to p.id, "title" to p.name, "root" to p.root, "team" to (t?.id ?: "unassigned"), "team_title" to (t?.name ?: "No team yet"))
        }
        val inPlay = repos.map { it["team"] as String }.distinct()
        val impact = store.latest(i.id, "impact")
        @Suppress("UNCHECKED_CAST")
        val impactRepos = (impact?.data as? Map<String, Any?>)?.get("repos")
        val docs = mapOf(
            "brief" to store.latest(i.id, "brief")?.text, "impact" to impact?.text, "impact_repos" to impactRepos,
            "decision" to store.latest(i.id, "decision")?.text, "plan" to store.latest(i.id, "plan")?.data,
        ).filterValues { it != null }
        val versions = listOf("brief", "impact", "decision", "plan").mapNotNull { k -> store.latest(i.id, k)?.let { k to it.version } }.toMap()
        return mapOf(
            "initiative" to mapOf("id" to i.id, "title" to i.title, "idea" to i.idea, "owner" to (i.owner ?: "Product")),
            "stage" to stage, "note" to (note ?: ""), "objection" to (objection ?: ""), "metric" to (metric ?: ""),
            "repos" to repos,
            "team_list" to inPlay.joinToString(", ") { id -> store.team(id)?.let { "${it.id} (${it.name})" } ?: id },
            "repo_list" to repos.joinToString(", ") { "${it["id"]} (${it["team"]})" },
            "docs" to docs, "versions" to versions,
        )
    }

    /** The initiative as one readable text: every agent of every stage gets it ("What the user asked for"). */
    fun context(i: Initiative, stage: String): String = buildString {
        append("# Initiative ${i.id}: ${i.title}\n\n")
        append("Idea: ${i.idea}\n")
        i.whyNow?.takeIf { it.isNotBlank() }?.let { append("Why now: $it\n") }
        i.outcomeHope?.takeIf { it.isNotBlank() }?.let { append("Outcome hoped for: $it\n") }
        append("Owner: ${i.owner ?: "Product"}\n")
        i.option?.let { append("Option chosen at the decision: $it\n") }
        val asked = store.questions(i.id).filter { it.status == "answered" }
        if (asked.isNotEmpty()) {
            append("\n## Answers given so far\n")
            asked.forEach { append("- ${it.text} → ${it.answer}\n") }
        }
        for ((kind, title) in listOf("brief" to "The brief", "impact" to "The impact", "decision" to "The decision memo")) {
            if (kind == stage && stage != "impact") continue
            store.latest(i.id, kind)?.let { d ->
                append("\n## $title (v${d.version}${if (d.approvedAt != null) ", approved" else ""})\n").append(d.text.take(20_000)).append("\n")
            }
        }
        val repos = i.repos.mapNotNull { projects.find(it) }
        if (repos.isNotEmpty()) {
            append("\n## Repos\n")
            repos.forEach { p -> append("- ${p.id}: owned by ${teams.ownersOf(p.id).joinToString { it.name }.ifEmpty { "no team yet" }}\n") }
        }
        val inPlay = repos.flatMap { teams.ownersOf(it.id) }.distinctBy { it.id }
        if (inPlay.isNotEmpty()) {
            append("\n## Teams\n")
            inPlay.forEach { t ->
                append("- ${t.name} (${t.id}): owns ${t.owns.joinToString { "${it.projectId}:${it.glob}" }.ifEmpty { "nothing yet" }}")
                t.capacityDays?.let { append(" · free in the next 2 sprints: $it developer days") }
                t.lead?.let { append(" · lead: $it") }
                append("\n")
                teams.pagesText(t.id).takeIf { it.isNotBlank() }?.let { append(it).append("\n") }
            }
        }
    }
}
