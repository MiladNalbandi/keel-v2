package keel.api.goals

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.flow.FlowCap
import keel.api.flow.FlowService
import keel.api.inbox.InboxItem
import keel.api.inbox.InboxSource
import keel.api.projects.ProjectService
import org.springframework.core.annotation.Order
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.util.UUID

data class Goal(
    val id: String, val projectId: String, val title: String, val why: String?, val doneWhen: String, val capTokens: Long?, val maxFlows: Int?,
    val status: String, val planThread: String?, val planReady: Boolean, val createdAt: String, val updatedAt: String,
)

/** A step, with what its flow says now. */
data class GoalStep(
    val id: String, val n: Int, val title: String, val request: String, val workflow: String, val skipped: Boolean, val threadId: String?,
    /** todo | skipped | the flow's status (running, waiting, done, failed, stopped) */
    val state: String, val tokens: Long, val costUsd: Double,
)

/** How far a goal is. */
data class GoalProgress(
    val done: Int, val total: Int, val tokens: Long, val costUsd: Double, val flows: Int,
    /** why no further step may start, in plain words, or null */
    val blocked: String?,
)

data class GoalView(val goal: Goal, val steps: List<GoalStep>, val progress: GoalProgress, val draft: List<PlannedStep>?, val limits: List<String>)
data class GoalRow(val goal: Goal, val progress: GoalProgress)
data class PlannedStep(val title: String = "", val request: String = "", val workflow: String = "")
data class NewGoal(val title: String = "", val why: String? = null, val doneWhen: String = "", val capTokens: Long? = null, val maxFlows: Int? = null)
data class LimitsBody(val capTokens: Long? = null, val maxFlows: Int? = null)
data class StartBody(val allowFake: Boolean = false, val allowDirty: Boolean = false)

/**
 * The Goals plugin's api part. A goal holds what "done" means and its limits. A planning flow (workflow goals-plan) proposes steps; they
 * become real only when a person approves its gate ([GoalEvents]). A person starts each step: nothing starts by itself. A step's flow gets
 * what is left of the goal's tokens as its cap, so a flow pauses at keel's own budget gate when the goal's limit is reached.
 *
 *     GET  /api/projects/{pid}/goals                        the goals, with how far each is
 *     POST /api/projects/{pid}/goals                        {title, why, done_when, cap_tokens, max_flows}
 *     GET  /api/projects/{pid}/goals/{id}                   one, with its steps, progress and the plan that waits
 *     POST /api/projects/{pid}/goals/{id}/plan              start the planning flow
 *     POST /api/projects/{pid}/goals/{id}/limits            {cap_tokens, max_flows}
 *     POST /api/projects/{pid}/goals/{id}/steps/{sid}/start start this step's flow
 *     POST /api/projects/{pid}/goals/{id}/steps/{sid}/skip  leave it out
 *     POST /api/projects/{pid}/goals/{id}/status/{status}   done or stopped (a person says so)
 */
@Service
class GoalService(private val jdbc: JdbcTemplate, private val projects: ProjectService, private val flows: FlowService, private val mapper: ObjectMapper) {
    private val goalColumns = "id, project_id, title, why, done_when, cap_tokens, max_flows, status, plan_thread, plan_draft, created_at, updated_at"

    private fun goal(rs: java.sql.ResultSet) = Goal(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5),
        rs.getObject(6)?.let { (it as Number).toLong() }, rs.getObject(7)?.let { (it as Number).toInt() }, rs.getString(8), rs.getString(9), rs.getString(10) != null,
        rs.getString(11), rs.getString(12))

    fun find(pid: String, id: String): Goal =
        jdbc.query("SELECT $goalColumns FROM goals_goal WHERE project_id = ? AND id = ?", { rs, _ -> goal(rs) }, pid, id).firstOrNull()
            ?: throw NotFound("No goal $id in this project")

    fun list(pid: String): List<GoalRow> {
        projects.require(pid)
        return jdbc.query("SELECT $goalColumns FROM goals_goal WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 100", { rs, _ -> goal(rs) }, pid)
            .map { GoalRow(it, progress(it, steps(it))) }
    }

    fun create(pid: String, b: NewGoal): Goal {
        projects.require(pid)
        val title = b.title.trim()
        val done = b.doneWhen.trim()
        if (title.isEmpty() || done.isEmpty()) throw BadRequest("A goal needs a title and what \"done\" means", "Say it so a person can check it: \"people can export invoices as a file\".")
        limits(b.capTokens, b.maxFlows)
        val id = "g" + UUID.randomUUID().toString().replace("-", "").take(10)
        val now = Time.now()
        jdbc.update("INSERT INTO goals_goal(id, project_id, title, why, done_when, cap_tokens, max_flows, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)",
            id, pid, title.take(200), b.why?.trim()?.ifBlank { null }?.take(2000), done.take(2000), b.capTokens, b.maxFlows, now, now)
        return find(pid, id)
    }

    private fun limits(cap: Long?, max: Int?) {
        if (cap != null && cap <= 0) throw BadRequest("The token limit must be above 0")
        if (max != null && max <= 0) throw BadRequest("The flow limit must be above 0")
    }

    fun view(pid: String, id: String): GoalView {
        projects.require(pid)
        val g = find(pid, id)
        val s = steps(g)
        return GoalView(g, s, progress(g, s), draft(id), LIMITS)
    }

    private fun draft(id: String): List<PlannedStep>? =
        jdbc.query("SELECT plan_draft FROM goals_goal WHERE id = ?", { rs, _ -> rs.getString(1) }, id).firstOrNull()?.let { readPlan(it) }

    private fun readPlan(json: String): List<PlannedStep>? = runCatching {
        mapper.readTree(json).map { PlannedStep(it.path("title").asText(""), it.path("request").asText(""), it.path("workflow").asText("")) }
    }.getOrNull()

    private fun steps(g: Goal): List<GoalStep> = jdbc.query(
        """SELECT s.id, s.n, s.title, s.request, s.workflow, s.skipped, s.thread_id, t.status,
             (SELECT COALESCE(SUM(c.tokens_in + c.tokens_out), 0) FROM agent_calls c WHERE c.thread_id = s.thread_id),
             (SELECT COALESCE(SUM(c.cost_usd), 0) FROM agent_calls c WHERE c.thread_id = s.thread_id)
           FROM goals_step s LEFT JOIN threads t ON t.id = s.thread_id WHERE s.goal_id = ? ORDER BY s.n""",
        { rs, _ ->
            val skipped = rs.getInt(6) == 1
            val tid = rs.getString(7)
            val state = when { skipped -> "skipped"; tid == null -> "todo"; else -> rs.getString(8) ?: "running" }
            GoalStep(rs.getString(1), rs.getInt(2), rs.getString(3), rs.getString(4), rs.getString(5), skipped, tid, state, rs.getLong(9), rs.getDouble(10))
        },
        g.id,
    )

    /** What the planning flow used counts too: a limit is on the whole goal. */
    private fun progress(g: Goal, s: List<GoalStep>): GoalProgress {
        val plan = g.planThread?.let { tid ->
            jdbc.queryForMap("SELECT COALESCE(SUM(tokens_in + tokens_out), 0) AS t, COALESCE(SUM(cost_usd), 0) AS c FROM agent_calls WHERE thread_id = ?", tid)
        }
        val tokens = s.sumOf { it.tokens } + ((plan?.get("t") as? Number)?.toLong() ?: 0L)
        val cost = s.sumOf { it.costUsd } + ((plan?.get("c") as? Number)?.toDouble() ?: 0.0)
        val started = s.count { it.threadId != null }
        val blocked = when {
            g.status == "done" || g.status == "stopped" -> "The goal is ${g.status}."
            s.any { it.state == "running" || it.state == "waiting" } -> "A step of this goal runs or waits. Finish it first."
            g.maxFlows != null && started >= g.maxFlows -> "The goal's limit of ${g.maxFlows} flows is reached."
            g.capTokens != null && tokens >= g.capTokens -> "The goal's limit of ${g.capTokens} tokens is reached ($tokens used)."
            else -> null
        }
        val live = s.filter { !it.skipped }
        return GoalProgress(live.count { it.state == "done" }, live.size, tokens, cost, started, blocked)
    }

    /** Starts the planning flow: the goal's words go to the model company. A person pressed the button. */
    fun plan(pid: String, id: String, b: StartBody): Goal {
        projects.require(pid)
        val g = find(pid, id)
        if (g.status != "new" && g.status != "active") throw Conflict("This goal is ${g.status}", "Open its planning flow to go on.")
        if (steps(g).any { it.threadId != null }) throw Conflict("A step of this goal already started", "Planning again would hide what it did.")
        val state = flows.start(pid, WORKFLOW, "Plan: ${g.title}".take(200), null, g.capTokens?.let { FlowCap(capTokens = it.coerceAtMost(Int.MAX_VALUE.toLong()).toInt()) } ?: FlowCap(),
            allowFake = b.allowFake, allowDirty = true, request = planRequest(g), ownBranch = false)
        val tid = state.get("thread_id")?.asText()?.ifBlank { null } ?: throw Conflict("The engine did not say which flow it started")
        jdbc.update("UPDATE goals_goal SET status = 'planning', plan_thread = ?, plan_draft = NULL, updated_at = ? WHERE id = ?", tid, Time.now(), id)
        return find(pid, id)
    }

    fun setLimits(pid: String, id: String, b: LimitsBody): Goal {
        find(pid, id)
        limits(b.capTokens, b.maxFlows)
        jdbc.update("UPDATE goals_goal SET cap_tokens = ?, max_flows = ?, updated_at = ? WHERE id = ?", b.capTokens, b.maxFlows, Time.now(), id)
        return find(pid, id)
    }

    fun setStatus(pid: String, id: String, status: String): Goal {
        find(pid, id)
        if (status !in setOf("done", "stopped")) throw BadRequest("A goal can be marked done or stopped")
        jdbc.update("UPDATE goals_goal SET status = ?, updated_at = ? WHERE id = ?", status, Time.now(), id)
        return find(pid, id)
    }

    /** Starts one step's flow, with what is left of the goal's tokens as its cap. Refused past a limit. */
    fun startStep(pid: String, id: String, sid: String, b: StartBody): GoalView {
        projects.require(pid)
        val g = find(pid, id)
        val all = steps(g)
        val step = all.firstOrNull { it.id == sid } ?: throw NotFound("No step $sid in this goal")
        if (step.threadId != null || step.skipped) throw Conflict("This step is ${step.state}")
        val p = progress(g, all)
        p.blocked?.let { throw Conflict(it, if ("limit" in it) "Raise the limit, or stop the goal." else null) }
        val left = g.capTokens?.let { (it - p.tokens).coerceIn(1, Int.MAX_VALUE.toLong()).toInt() }
        val state = flows.start(pid, step.workflow, step.title.take(200), null, left?.let { FlowCap(capTokens = it, onCap = "pause") } ?: FlowCap(),
            allowFake = b.allowFake, allowDirty = b.allowDirty, request = stepRequest(g, step))
        val tid = state.get("thread_id")?.asText()?.ifBlank { null } ?: throw Conflict("The engine did not say which flow it started")
        jdbc.update("UPDATE goals_step SET thread_id = ? WHERE id = ?", tid, sid)
        return view(pid, id)
    }

    fun skipStep(pid: String, id: String, sid: String): GoalView {
        projects.require(pid)
        val g = find(pid, id)
        val step = steps(g).firstOrNull { it.id == sid } ?: throw NotFound("No step $sid in this goal")
        if (step.threadId != null) throw Conflict("This step already started")
        jdbc.update("UPDATE goals_step SET skipped = 1 WHERE id = ?", sid)
        return view(pid, id)
    }

    /** The planner reached its gate: keep the steps it proposed, not real yet. */
    fun planned(threadId: String, text: String) {
        val g = byPlan(threadId) ?: return
        val steps = planFrom(text)
        jdbc.update("UPDATE goals_goal SET plan_draft = ?, updated_at = ? WHERE id = ?", if (steps.isEmpty()) null else mapper.writeValueAsString(steps), Time.now(), g.id)
    }

    /** A person approved the plan: now its steps are real. A send-back keeps the goal planning. */
    fun approved(threadId: String, decision: String?) {
        val g = byPlan(threadId) ?: return
        if (decision != "approve") return
        val steps = draft(g.id).orEmpty()
        val now = Time.now()
        jdbc.update("DELETE FROM goals_step WHERE goal_id = ? AND thread_id IS NULL", g.id)
        steps.forEachIndexed { i, s ->
            jdbc.update("INSERT INTO goals_step(id, goal_id, n, title, request, workflow, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                "s" + UUID.randomUUID().toString().replace("-", "").take(10), g.id, i + 1, s.title.take(200), s.request.take(REQUEST_MAX), s.workflow, now)
        }
        jdbc.update("UPDATE goals_goal SET status = 'active', plan_draft = NULL, updated_at = ? WHERE id = ?", now, g.id)
    }

    /** The planning flow failed or was stopped before a person approved: plan again. */
    fun planEnded(threadId: String) {
        val g = byPlan(threadId)?.takeIf { it.status == "planning" } ?: return
        jdbc.update("UPDATE goals_goal SET status = 'new', updated_at = ? WHERE id = ?", Time.now(), g.id)
    }

    private fun byPlan(tid: String): Goal? =
        if (tid.isBlank()) null else jdbc.query("SELECT $goalColumns FROM goals_goal WHERE plan_thread = ?", { rs, _ -> goal(rs) }, tid).firstOrNull()

    /** The steps of a planner's answer: its last ```json block; a flow it may not name becomes change. */
    fun planFrom(text: String): List<PlannedStep> {
        val block = Regex("```json\\s*(.*?)```", RegexOption.DOT_MATCHES_ALL).findAll(text).lastOrNull()?.groupValues?.get(1) ?: return emptyList()
        return runCatching {
            mapper.readTree(block).mapNotNull { n ->
                val title = n.path("title").asText("").trim()
                if (title.isEmpty()) null else PlannedStep(title, n.path("request").asText(title).trim().ifEmpty { title },
                    n.path("workflow").asText("").trim().takeIf { it in FLOWS } ?: "change")
            }.take(STEPS_MAX)
        }.getOrDefault(emptyList())
    }

    /** Goals with a next step to start and nothing running: they wait for the person, in the Inbox. */
    fun ready(pid: String?): List<Pair<Goal, GoalStep>> = jdbc.query(
        "SELECT $goalColumns FROM goals_goal WHERE status = 'active'" + (if (pid != null) " AND project_id = ?" else "") + " LIMIT 100",
        { rs, _ -> goal(rs) }, *listOfNotNull(pid).toTypedArray(),
    ).mapNotNull { g ->
        val s = steps(g)
        val next = s.firstOrNull { it.state == "todo" }
        if (next != null && progress(g, s).blocked == null) g to next else null
    }

    private fun planRequest(g: Goal) = buildString {
        appendLine("The goal: ${g.title}")
        g.why?.let { appendLine("Why: $it") }
        appendLine("Done when: ${g.doneWhen}")
        appendLine("Limits: " + listOfNotNull(g.capTokens?.let { "$it tokens for all flows together" }, g.maxFlows?.let { "$it flows at most" }).ifEmpty { listOf("none set") }.joinToString(", "))
    }

    private fun stepRequest(g: Goal, s: GoalStep) = buildString {
        appendLine(s.request)
        appendLine()
        appendLine("This is step ${s.n} of the goal \"${g.title}\", which is done when: ${g.doneWhen}")
        appendLine("Do only this step.")
    }

    companion object {
        const val WORKFLOW = "goals-plan"
        const val STEPS_MAX = 20
        const val REQUEST_MAX = 8000
        val FLOWS = setOf("quick", "small", "change", "fix", "feature")
        val LIMITS = listOf(
            "A goal has no named owner. keel has one person, so the person signed in owns every goal, and nobody else can be asked or told.",
            "keel checks a goal's limits when a step starts, and a step's flow pauses at its token cap. Nothing starts by itself.",
            "keel does not check that a goal is really done. It counts steps. You say when it is done.",
        )
    }
}

/** The planning flow's gate: the plan waits, or a person answered. */
@Component
class GoalEvents(private val goals: GoalService) : EngineEventHandler {
    override val prefix = "gate."
    override fun handle(event: EngineEvent) {
        if (event.step != "plan_gate") return
        when (event.type) {
            "gate.waiting" -> goals.planned(event.threadId, event.data["detail"]?.toString().orEmpty())
            "gate.decided" -> goals.approved(event.threadId, event.data["decision"]?.toString())
        }
    }
}

@Component
class GoalEndEvents(private val goals: GoalService) : EngineEventHandler {
    override val prefix = "thread."
    override fun handle(event: EngineEvent) {
        if (event.type == "thread.failed" || (event.type == "thread.done" && event.data["status"] == "stopped")) goals.planEnded(event.threadId)
    }
}

/** A goal whose next step can start waits for the person, with its own card (the web's slot inbox.card, kind goal-step). */
@Component
@Order(30)
class GoalInbox(private val goals: GoalService) : InboxSource {
    override val kind = "goals"

    override fun items(pid: String?): List<InboxItem> = goals.ready(pid).map { (g, s) ->
        InboxItem(projectId = g.projectId, projectName = "", threadId = "", flow = g.title, workflowId = s.workflow, step = s.id, kind = "goal-step",
            title = "Next step of \"${g.title}\": ${s.title}", detail = "Flow: ${s.workflow}. ${s.request.take(300)}", more = s.request.length > 300, options = emptyList(), id = g.id, since = g.updatedAt)
    }

    override fun waiting(pid: String?): Int = goals.ready(pid).size
}

@RestController
@RequestMapping("/api/projects/{pid}/goals")
class GoalController(private val goals: GoalService) {
    @GetMapping
    fun list(@PathVariable pid: String): List<GoalRow> = goals.list(pid)

    @PostMapping
    fun create(@PathVariable pid: String, @RequestBody body: NewGoal): Goal = goals.create(pid, body)

    @GetMapping("/{id}")
    fun one(@PathVariable pid: String, @PathVariable id: String): GoalView = goals.view(pid, id)

    @PostMapping("/{id}/plan")
    fun plan(@PathVariable pid: String, @PathVariable id: String, @RequestBody(required = false) body: StartBody?): Goal = goals.plan(pid, id, body ?: StartBody())

    @PostMapping("/{id}/limits")
    fun limits(@PathVariable pid: String, @PathVariable id: String, @RequestBody body: LimitsBody): Goal = goals.setLimits(pid, id, body)

    @PostMapping("/{id}/steps/{sid}/start")
    fun start(@PathVariable pid: String, @PathVariable id: String, @PathVariable sid: String, @RequestBody(required = false) body: StartBody?): GoalView =
        goals.startStep(pid, id, sid, body ?: StartBody())

    @PostMapping("/{id}/steps/{sid}/skip")
    fun skip(@PathVariable pid: String, @PathVariable id: String, @PathVariable sid: String): GoalView = goals.skipStep(pid, id, sid)

    @PostMapping("/{id}/status/{status}")
    fun status(@PathVariable pid: String, @PathVariable id: String, @PathVariable status: String): Goal = goals.setStatus(pid, id, status)
}
