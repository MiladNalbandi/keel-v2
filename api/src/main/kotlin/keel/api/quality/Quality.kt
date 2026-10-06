package keel.api.quality

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.KeelProperties
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.engine.EngineClient
import keel.api.events.EventHub
import keel.api.flow.FlowCap
import keel.api.flow.FlowService
import keel.api.projects.ProjectService
import keel.api.settings.Model
import keel.api.settings.SettingsService
import keel.api.workflows.WorkflowService
import org.slf4j.LoggerFactory
import org.springframework.boot.context.event.ApplicationReadyEvent
import org.springframework.context.event.EventListener
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.nio.file.Files
import java.nio.file.Path
import java.time.Duration
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneOffset
import java.util.UUID
import java.util.concurrent.Executors

/** Start a quality run: these flows × these models over the cases of these eval sets (empty: all of them). */
data class StartQuality(val flows: List<String> = listOf("change"), val models: List<Model> = emptyList(), val sets: List<String> = emptyList())
data class QualitySchedule(val enabled: Boolean = false, val at: String = "02:00", val flows: List<String> = listOf("change"),
                           val models: List<Model> = emptyList(), val lastRunDate: String? = null)

data class QualityCase(
    val id: String, val n: Int, val evalSet: String, val caseId: String, val title: String, val workflowId: String, val model: Model,
    val status: String, val outcome: String?, val reached: String?, val projectId: String?, val threadId: String?, val score: Int?,
    val tokens: Long?, val costUsd: Double?, val ms: Long?, val sendbacks: Int?, val estimate: Long?, val progress: Double?,
    val startedAt: String?, val endedAt: String?,
)
/** One flow × model in one run: the mean score of its cases, how many reached the end, what they used. */
data class QualityScore(val workflowId: String, val model: Model, val score: Int, val cases: Int, val reachedEnd: Int,
                        val tokens: Long, val ms: Long)
data class QualityRun(val id: String, val status: String, val trigger: String, val flows: List<String>, val models: List<Model>,
                      val sets: List<String>, val createdAt: String, val startedAt: String?, val endedAt: String?, val error: String?,
                      val scores: List<QualityScore>, val cases: List<QualityCase>)
data class QualityPoint(val runId: String, val at: String, val score: Int)
/** A flow × model over the runs, oldest first; `drop`: its last score fell by DROP points or more since the one before. */
data class QualityLine(val workflowId: String, val model: Model, val points: List<QualityPoint>, val last: Int?, val previous: Int?,
                       val drop: Boolean)
data class QualityView(val sets: JsonNode, val active: QualityRun?, val runs: List<QualityRun>, val lines: List<QualityLine>,
                       val schedule: QualitySchedule)

/**
 * v0.8.0 quality runs: keel runs its flows on small eval projects (content/evals) with chosen models, one case at a
 * time, and scores each run, so a change to a prompt or a model shows up as a drop before it ships.
 *
 * A case: the engine copies the eval project into a fresh git repository under $KEEL_DATA/evals/<run>/, the api
 * registers it as a hidden project (no list, Inbox or notification shows it), and starts the case's flow there in run
 * mode auto (it approves every gate it can, never opens a PR) with the chosen model for every agent. The case ends at
 * the PR gate or done (it reached the end), at another gate (stuck: something only a person answers), failed, or after
 * [CASE_TIMEOUT]. Score 0-100: the result (60), no send-backs (20), tokens within the estimate (20); see [score].
 */
@Service
class QualityService(
    private val jdbc: JdbcTemplate,
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val flows: FlowService,
    private val settings: SettingsService,
    private val workflows: WorkflowService,
    private val props: KeelProperties,
    private val hub: EventHub,
    private val mapper: ObjectMapper,
) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "quality-runs").apply { isDaemon = true } }
    @Volatile private var stopping: String? = null

    companion object {
        val CASE_TIMEOUT: Duration = Duration.ofMinutes(30)
        const val POLL_MS = 3000L
        const val DROP = 15
        const val MAX_MODELS = 4

        /**
         * The result (60): the end reached (the PR gate, or done) 60; stuck at another gate, stopped by its token cap,
         * timed out or stopped: 40 × how far it got; failed: 20 × how far it got. Send-backs (20): 5 less for each. Tokens (20): all within
         * the estimate, nothing at twice the estimate or more, in between on a line; 10 when there is no estimate.
         */
        fun score(outcome: String, progress: Double, sendbacks: Int, tokens: Long, estimate: Long?): Int {
            val p = progress.coerceIn(0.0, 1.0)
            val result = when (outcome) {
                "end" -> 60.0
                "stuck", "timeout", "stopped", "cap" -> 40.0 * p
                "failed" -> 20.0 * p
                else -> 0.0
            }
            val calm = (20 - 5 * sendbacks).coerceAtLeast(0).toDouble()
            val thrift = when {
                estimate == null || estimate <= 0 -> 10.0
                tokens <= estimate -> 20.0
                tokens >= 2 * estimate -> 0.0
                else -> 20.0 * (2 * estimate - tokens).toDouble() / estimate
            }
            return if (outcome == "refused") 0 else Math.round(result + calm + thrift).toInt().coerceIn(0, 100)
        }

        fun modelKey(m: Model) = "${m.provider}/${m.mode}/${m.model}"
    }

    // ---- the page -----------------------------------------------------------------------------

    fun view(): QualityView {
        val runs = runs(20)
        return QualityView(sets(), runs.firstOrNull { it.status in setOf("queued", "running") }, runs, lines(), schedule())
    }

    fun sets(): JsonNode = runCatching { engine.get("/evals") }.getOrElse { mapper.createArrayNode() }

    fun run(id: String): QualityRun = runs(200).firstOrNull { it.id == id } ?: throw NotFound("No quality run $id")

    private fun runs(limit: Int): List<QualityRun> {
        val rows = jdbc.queryForList("SELECT * FROM quality_runs ORDER BY created_at DESC LIMIT ?", limit)
        return rows.map { r ->
            val id = r["id"].toString()
            val cases = cases(id)
            QualityRun(id, r["status"].toString(), r["trigger"].toString(), list(r["flows_json"]), models(r["models_json"]), list(r["sets_json"]),
                r["created_at"].toString(), r["started_at"]?.toString(), r["ended_at"]?.toString(), r["error"]?.toString(), scores(cases), cases)
        }
    }

    private fun cases(runId: String): List<QualityCase> =
        jdbc.queryForList("SELECT * FROM quality_cases WHERE run_id = ? ORDER BY n", runId).map { r ->
            QualityCase(r["id"].toString(), (r["n"] as Number).toInt(), r["eval_set"].toString(), r["case_id"].toString(), r["title"].toString(),
                r["workflow_id"].toString(), mapper.readValue(r["model_json"].toString(), Model::class.java), r["status"].toString(),
                r["outcome"]?.toString(), r["reached"]?.toString(), r["project_id"]?.toString(), r["thread_id"]?.toString(),
                (r["score"] as Number?)?.toInt(), (r["tokens"] as Number?)?.toLong(), (r["cost_usd"] as Number?)?.toDouble(),
                (r["ms"] as Number?)?.toLong(), (r["sendbacks"] as Number?)?.toInt(), (r["estimate"] as Number?)?.toLong(),
                (r["progress"] as Number?)?.toDouble(), r["started_at"]?.toString(), r["ended_at"]?.toString())
        }

    private fun scores(cases: List<QualityCase>): List<QualityScore> =
        cases.filter { it.score != null }.groupBy { it.workflowId to modelKey(it.model) }.map { (_, cs) ->
            QualityScore(cs[0].workflowId, cs[0].model, Math.round(cs.map { it.score!! }.average()).toInt(), cs.size,
                cs.count { it.outcome == "end" }, cs.sumOf { it.tokens ?: 0 }, cs.sumOf { it.ms ?: 0 })
        }

    /** Each flow × model over the finished runs (the last 12), with a drop flag. */
    private fun lines(): List<QualityLine> {
        val done = runs(60).filter { it.status in setOf("done", "stopped") }.reversed()
        val byKey = linkedMapOf<String, MutableList<Pair<QualityRun, QualityScore>>>()
        for (r in done) for (s in r.scores) byKey.getOrPut("${s.workflowId}|${modelKey(s.model)}") { mutableListOf() } += r to s
        return byKey.values.map { pts ->
            val points = pts.takeLast(12).map { (r, s) -> QualityPoint(r.id, r.endedAt ?: r.createdAt, s.score) }
            val last = points.lastOrNull()?.score
            val previous = points.dropLast(1).lastOrNull()?.score
            QualityLine(pts[0].second.workflowId, pts[0].second.model, points, last, previous, last != null && previous != null && previous - last >= DROP)
        }
    }

    private fun list(v: Any?): List<String> = v?.let { mapper.readValue(it.toString(), Array<String>::class.java).toList() }.orEmpty()
    private fun models(v: Any?): List<Model> = v?.let { mapper.readValue(it.toString(), Array<Model>::class.java).toList() }.orEmpty()

    // ---- start and stop -------------------------------------------------------------------------

    fun start(body: StartQuality, trigger: String = "manual"): QualityRun {
        val flowIds = body.flows.map { it.trim() }.filter { it.isNotEmpty() }.distinct()
        if (flowIds.isEmpty()) throw BadRequest("Pick at least one flow", "For example change.")
        flowIds.forEach { runCatching { workflows.get(it) }.getOrElse { throw BadRequest("No workflow called \"$it\"") } }
        val models = body.models.distinctBy { modelKey(it) }
        if (models.isEmpty()) throw BadRequest("Pick at least one model", "Compare two: the score shows which one does the flows better.")
        if (models.size > MAX_MODELS) throw BadRequest("Four models at most in one run")
        val all = sets().filter { it.path("problems").isEmpty }
        val chosen = if (body.sets.isEmpty()) all else all.filter { it.path("name").asText() in body.sets }
        if (chosen.isEmpty()) throw BadRequest("No eval set to run", "content/evals has the eval sets; see its README.")
        val busy = jdbc.queryForObject("SELECT COUNT(*) FROM quality_runs WHERE status IN ('queued','running')", Int::class.java) ?: 0
        if (busy > 0) throw Conflict("A quality run is on already", "Wait for it, or stop it on the Quality page.")
        val id = "q_" + UUID.randomUUID().toString().replace("-", "").take(12)
        jdbc.update("INSERT INTO quality_runs(id, status, trigger, flows_json, models_json, sets_json, created_at) VALUES (?, 'queued', ?, ?, ?, ?, ?)",
            id, trigger, mapper.writeValueAsString(flowIds), mapper.writeValueAsString(models),
            mapper.writeValueAsString(chosen.map { it.path("name").asText() }), Time.now())
        var n = 0
        for (set in chosen) for (c in set.path("cases")) for (flow in flowIds) for (m in models) {
            jdbc.update("INSERT INTO quality_cases(id, run_id, n, eval_set, case_id, title, workflow_id, model_json, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued')",
                "$id-${n}", id, n, set.path("name").asText(), c.path("id").asText(), c.path("title").asText(), flow, mapper.writeValueAsString(m))
            n++
        }
        worker.execute { runAll(id) }
        return run(id)
    }

    fun stop(id: String): QualityRun {
        val r = run(id)
        if (r.status !in setOf("queued", "running")) throw Conflict("This run is over already")
        stopping = id
        return r
    }

    // ---- the worker -------------------------------------------------------------------------------

    private fun runAll(id: String) {
        jdbc.update("UPDATE quality_runs SET status = 'running', started_at = ? WHERE id = ?", Time.now(), id)
        try {
            for (c in cases(id)) {
                if (stopping == id) {
                    jdbc.update("UPDATE quality_cases SET status = 'stopped', outcome = 'stopped', reached = 'the run was stopped' WHERE run_id = ? AND status = 'queued'", id)
                    break
                }
                runCase(id, c)
                changed()
            }
            jdbc.update("UPDATE quality_runs SET status = ?, ended_at = ? WHERE id = ?", if (stopping == id) "stopped" else "done", Time.now(), id)
        } catch (e: Exception) {
            log.warn("quality run {} failed: {}", id, e.message)
            jdbc.update("UPDATE quality_runs SET status = 'failed', ended_at = ?, error = ? WHERE id = ?", Time.now(), e.message?.take(500), id)
        } finally {
            if (stopping == id) stopping = null
            runCatching { props.dataDir.resolve("evals").resolve(id).toFile().deleteRecursively() }
            changed()
        }
    }

    private fun runCase(runId: String, c: QualityCase) {
        val started = Instant.now()
        jdbc.update("UPDATE quality_cases SET status = 'running', started_at = ? WHERE id = ?", Time.now(), c.id)
        val set = sets().firstOrNull { it.path("name").asText() == c.evalSet }
        val spec = set?.path("cases")?.firstOrNull { it.path("id").asText() == c.caseId }
        if (spec == null) return finish(c, started, "refused", "the eval case is gone", null, null)
        var tid: String? = null
        var pid: String? = null
        try {
            val dest = props.dataDir.resolve("evals").resolve(runId).resolve("${c.n}-${c.evalSet}-${c.caseId}")
            engine.post("/evals/prepare", mapOf("name" to c.evalSet, "dest" to dest.toString()))
            val project = projects.registerHidden(dest, "eval $runId ${c.n} ${c.caseId}")
            pid = project.id
            settings.updateProject(project.id, mapOf("notify" to "none"))
            val acs = spec.path("acs").map { keel.api.flow.Ac(it.path("id").asText(), it.path("layer").asText("API"), it.path("title").asText()) }
            val estimate = runCatching { flows.estimate(project.id, c.workflowId, acs.size.coerceAtLeast(1)).path("tokens").asLong() }.getOrNull()
            // the case's own cap, else twice the estimate (the score already rewards staying within the estimate)
            val cap = spec.path("cap_tokens").takeIf { it.isNumber }?.asInt() ?: ((estimate ?: 300_000L) * 2).coerceIn(100_000, 3_000_000).toInt()
            val state = flows.start(project.id, c.workflowId, c.title, acs.takeIf { it.isNotEmpty() },
                FlowCap(cap, "stop", "auto"), allowFake = c.model.provider == "fake",
                request = spec.path("request").asText(), model = c.model)
            tid = state.path("thread_id").asText()
            jdbc.update("UPDATE quality_cases SET project_id = ?, thread_id = ?, estimate = ? WHERE id = ?", pid, tid, estimate, c.id)
            var (outcome, reached, last) = watch(runId, tid, started)
            // a flow its token cap stopped (on_cap stop) was not stopped by a person; the cap that bound is in its state
            // (the project's caps can leave less than the case asked for)
            val bound = last?.path("usage")?.path("cap_tokens")?.asLong(0)?.takeIf { it > 0 } ?: cap.toLong()
            if (outcome == "stopped" && stopping != runId && usedTokens(pid) >= bound * 9 / 10) {
                outcome = "cap"
                reached = "its token cap (${bound / 1000}k)"
            }
            if (last?.path("status")?.asText() in setOf("running", "waiting")) runCatching { flows.stop(tid) }
            finish(c, started, outcome, reached, last, estimate, pid, tid)
        } catch (e: ApiException) {
            // a used-up cap, the fake model on a real project, an engine error: nothing ran (or it stops here)
            tid?.let { t -> runCatching { flows.stop(t) } }
            finish(c, started, if (tid == null) "refused" else "failed", (e.message ?: "refused") + (e.hint?.let { " $it" } ?: ""), null, null, pid, tid)
        } catch (e: Exception) {
            tid?.let { t -> runCatching { flows.stop(t) } }
            finish(c, started, "failed", e.message ?: e.javaClass.simpleName, null, null, pid, tid)
        }
    }

    /** Follows a case's flow until it ends: (outcome, where, its last state). */
    private fun watch(runId: String, tid: String, started: Instant): Triple<String, String, JsonNode?> {
        var last: JsonNode? = null
        while (true) {
            if (stopping == runId) return Triple("stopped", "the run was stopped", last)
            if (Duration.between(started, Instant.now()) > CASE_TIMEOUT) return Triple("timeout", "no end after ${CASE_TIMEOUT.toMinutes()} minutes", last)
            last = runCatching { engine.thread(tid) }.getOrNull() ?: last
            val status = last?.path("status")?.asText()
            val waiting = last?.path("waiting")
            when (status) {
                "done" -> return Triple("end", "done", last)
                "failed" -> return Triple("failed", last?.path("error")?.asText("")?.ifBlank { null } ?: "the flow failed", last)
                "stopped" -> return Triple("stopped", "the flow was stopped", last)
                "waiting" -> {
                    val step = waiting?.path("step")?.asText("") ?: ""
                    val title = waiting?.path("title")?.asText("")?.ifBlank { step } ?: step
                    // run mode auto never opens a PR: waiting there is the end of the work
                    return if (step.contains("pr_gate") || step.endsWith("_pr")) Triple("end", "the PR gate", last) else Triple("stuck", title, last)
                }
            }
            Thread.sleep(POLL_MS)
        }
    }

    private fun finish(c: QualityCase, started: Instant, outcome: String, reached: String, last: JsonNode?, estimate: Long?,
                       pid: String? = null, tid: String? = null) {
        val tokens = pid?.let { usedTokens(it) } ?: 0L
        val cost = pid?.let { jdbc.queryForObject("SELECT COALESCE(SUM(cost_usd), 0) FROM agent_calls WHERE project_id = ?", Double::class.java, it) } ?: 0.0
        val sendbacks = last?.path("gate_log")?.count { Regex("\\breject\\b|sent back").containsMatchIn(it.asText()) } ?: 0
        val progress = progressOf(c.workflowId, last, outcome)
        val score = score(outcome, progress, sendbacks, tokens, estimate)
        jdbc.update("UPDATE quality_cases SET status = 'done', outcome = ?, reached = ?, project_id = COALESCE(?, project_id), thread_id = COALESCE(?, thread_id), " +
            "score = ?, tokens = ?, cost_usd = ?, ms = ?, sendbacks = ?, progress = ?, ended_at = ? WHERE id = ?",
            outcome, reached.take(300), pid, tid, score, tokens, cost, Duration.between(started, Instant.now()).toMillis(), sendbacks, progress, Time.now(), c.id)
    }

    /** Tokens as keel counts them for the budget and the caps: in + out + a tenth of the cache reads. */
    private fun usedTokens(pid: String): Long =
        jdbc.queryForObject("SELECT COALESCE(SUM(tokens_in + tokens_out + tokens_cached / 10), 0) FROM agent_calls WHERE project_id = ?", Long::class.java, pid) ?: 0L

    /** How far the flow got: the place of its current step among the workflow's steps (1.0 at the end). */
    private fun progressOf(workflowId: String, last: JsonNode?, outcome: String): Double {
        if (outcome == "end") return 1.0
        val current = last?.path("current")?.asText("")?.ifBlank { null } ?: return 0.0
        val steps = runCatching { workflows.get(workflowId).steps.map { it.id } }.getOrDefault(emptyList())
        val i = steps.indexOf(current)
        return if (i < 0 || steps.isEmpty()) 0.0 else (i + 1).toDouble() / steps.size
    }

    /** Every open page hears of it (a live tick): the Quality page reads the run again. */
    private fun changed() = runCatching { hub.publish(null, "project.changed", mapOf("id" to "quality")) }

    /** keel restarted in the middle of a run: its cases do not come back, the run says so. */
    @EventListener(ApplicationReadyEvent::class)
    fun interrupted() {
        jdbc.update("UPDATE quality_cases SET status = 'stopped', outcome = 'stopped', reached = 'keel restarted' WHERE status IN ('queued','running')")
        jdbc.update("UPDATE quality_runs SET status = 'stopped', ended_at = ?, error = 'keel restarted during the run' WHERE status IN ('queued','running')", Time.now())
    }

    // ---- every night ----------------------------------------------------------------------------

    fun schedule(): QualitySchedule {
        val r = jdbc.queryForList("SELECT * FROM quality_schedule WHERE id = 1").firstOrNull() ?: return QualitySchedule()
        return QualitySchedule((r["enabled"] as Number).toInt() == 1, r["at"].toString(), list(r["flows_json"]), models(r["models_json"]),
            r["last_run_date"]?.toString())
    }

    fun saveSchedule(s: QualitySchedule): QualitySchedule {
        val at = runCatching { LocalTime.parse(s.at.trim(), java.time.format.DateTimeFormatter.ofPattern("H:mm")) }
            .getOrElse { throw BadRequest("at must be a time like 02:00 (UTC)") }
        if (s.enabled && s.models.isEmpty()) throw BadRequest("Pick at least one model for the nightly run")
        if (s.enabled && s.flows.isEmpty()) throw BadRequest("Pick at least one flow for the nightly run")
        jdbc.update("INSERT INTO quality_schedule(id, enabled, at, flows_json, models_json) VALUES (1, ?, ?, ?, ?) " +
            "ON CONFLICT(id) DO UPDATE SET enabled = excluded.enabled, at = excluded.at, flows_json = excluded.flows_json, models_json = excluded.models_json",
            if (s.enabled) 1 else 0, "%02d:%02d".format(at.hour, at.minute), mapper.writeValueAsString(s.flows), mapper.writeValueAsString(s.models.take(MAX_MODELS)))
        return schedule()
    }

    /** Once a day, at or after the scheduled time (UTC), when no run is on. */
    @Scheduled(fixedDelayString = "\${keel.quality.tick-ms:60000}", initialDelayString = "\${keel.quality.tick-ms:60000}")
    fun nightly() {
        val s = schedule()
        if (!s.enabled) return
        val now = Instant.now().atOffset(ZoneOffset.UTC)
        val today = now.toLocalDate().toString()
        if (s.lastRunDate == today || now.toLocalTime().isBefore(LocalTime.parse(s.at))) return
        jdbc.update("UPDATE quality_schedule SET last_run_date = ? WHERE id = 1", today)
        runCatching { start(StartQuality(s.flows, s.models), "nightly") }.onFailure { log.info("nightly quality run not started: {}", it.message) }
    }
}

@RestController
@RequestMapping("/api/quality")
class QualityController(private val quality: QualityService) {
    @GetMapping
    fun view(): QualityView = quality.view()

    @PostMapping("/runs")
    fun start(@RequestBody body: StartQuality): QualityRun = quality.start(body)

    @GetMapping("/runs/{id}")
    fun run(@PathVariable id: String): QualityRun = quality.run(id)

    @PostMapping("/runs/{id}/stop")
    fun stop(@PathVariable id: String): QualityRun = quality.stop(id)

    @PutMapping("/schedule")
    fun schedule(@RequestBody body: QualitySchedule): QualitySchedule = quality.saveSchedule(body)
}
