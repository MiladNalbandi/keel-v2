package keel.api.jobs

import com.fasterxml.jackson.annotation.JsonInclude
import com.fasterxml.jackson.annotation.JsonUnwrapped
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.core.RowMapper
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

data class Job(
    val id: String,
    val projectId: String?,
    val threadId: String?,
    val agent: String?,
    val provider: String?,
    val model: String?,
    val step: String?,
    val phase: String?,
    val ac: String?,
    val status: String,
    val startedAt: String,
    val endedAt: String?,
    val tokensIn: Long,
    val tokensOut: Long,
    val tokensCached: Long,
    val costUsd: Double,
    val premiumRequests: Long,
    val stepsCount: Int,
    val mcpCalls: Int,
)

@JsonInclude(JsonInclude.Include.NON_NULL)
data class JobStep(
    val n: Int,
    val at: String,
    val kind: String,
    val text: String?,
    val tool: String? = null,
    val server: String? = null,
    val path: String? = null,
    val diff: String? = null,
    val ms: Long? = null,
    val ok: Boolean? = null,
    val output: String? = null,
)

data class JobDetail(@get:JsonUnwrapped val job: Job, val steps: List<JobStep>)
data class JobSteps(val steps: List<JobStep>, val running: Boolean)

@Service
class JobService(private val jdbc: JdbcTemplate) {
    private val cols = "id, project_id, thread_id, agent, provider, model, step, phase, ac, status, started_at, ended_at, " +
        "tokens_in, tokens_out, tokens_cached, cost_usd, premium_requests, steps_count, mcp_calls"

    private val jobMapper = RowMapper { rs, _ ->
        Job(
            rs.getString("id"), rs.getString("project_id"), rs.getString("thread_id"), rs.getString("agent"),
            rs.getString("provider"), rs.getString("model"), rs.getString("step"), rs.getString("phase"), rs.getString("ac"),
            rs.getString("status"), rs.getString("started_at"), rs.getString("ended_at"), rs.getLong("tokens_in"),
            rs.getLong("tokens_out"), rs.getLong("tokens_cached"), rs.getDouble("cost_usd"), rs.getLong("premium_requests"), rs.getInt("steps_count"),
            rs.getInt("mcp_calls"),
        )
    }

    private val stepMapper = RowMapper { rs, _ ->
        JobStep(
            rs.getInt("n"), rs.getString("at"), rs.getString("kind"), rs.getString("text"), rs.getString("tool"),
            rs.getString("server"), rs.getString("path"), rs.getString("diff"),
            rs.getLong("ms").takeIf { !rs.wasNull() }, rs.getInt("ok").takeIf { !rs.wasNull() }?.let { it == 1 }, rs.getString("output"),
        )
    }

    fun list(project: String?, status: String?, agent: String?, provider: String?, limit: Int): List<Job> {
        val where = mutableListOf<String>()
        val args = mutableListOf<Any>()
        if (!project.isNullOrBlank()) { where += "project_id = ?"; args += project }
        if (!status.isNullOrBlank()) {
            if (status == "done") where += "status IN ('done', 'stopped')" else { where += "status = ?"; args += status }
        }
        if (!agent.isNullOrBlank()) { where += "agent = ?"; args += agent }
        if (!provider.isNullOrBlank()) { where += "provider = ?"; args += provider }
        val sql = "SELECT $cols FROM agent_calls" + (if (where.isEmpty()) "" else " WHERE " + where.joinToString(" AND ")) +
            " ORDER BY started_at DESC LIMIT ?"
        args += limit.coerceIn(1, 500)
        return jdbc.query(sql, jobMapper, *args.toTypedArray())
    }

    fun get(id: String): Job =
        jdbc.query("SELECT $cols FROM agent_calls WHERE id = ?", jobMapper, id).firstOrNull()
            ?: throw NotFound("No job $id")

    fun steps(id: String, after: Int): List<JobStep> =
        jdbc.query("SELECT * FROM agent_steps WHERE call_id = ? AND n > ? ORDER BY n", stepMapper, id, after)

    fun markStopped(id: String) {
        jdbc.update("UPDATE agent_calls SET status = 'stopped', ended_at = COALESCE(ended_at, ?) WHERE id = ? AND status = 'running'", Time.now(), id)
    }
}

@RestController
@RequestMapping("/api/jobs")
class JobController(private val jobs: JobService, private val engine: EngineClient) {

    @GetMapping
    fun list(
        @RequestParam(required = false) project: String?,
        @RequestParam(required = false) status: String?,
        @RequestParam(required = false) agent: String?,
        @RequestParam(required = false) provider: String?,
        @RequestParam(defaultValue = "50") limit: Int,
    ): List<Job> = jobs.list(project, status, agent, provider, limit)

    @GetMapping("/{id}")
    fun get(@PathVariable id: String): JobDetail = JobDetail(jobs.get(id), jobs.steps(id, 0))

    @GetMapping("/{id}/steps")
    fun steps(@PathVariable id: String, @RequestParam(defaultValue = "0") after: Int): JobSteps {
        val job = jobs.get(id)
        return JobSteps(jobs.steps(id, after), job.status == "running")
    }

    /**
     * The engine stops whole threads, not single agent calls, so this stops the job's thread
     * and marks the job stopped.
     */
    @PostMapping("/{id}/stop")
    fun stop(@PathVariable id: String): Job {
        val job = jobs.get(id)
        if (job.status == "running" && job.threadId != null) {
            try {
                engine.stop(job.threadId)
            } catch (e: EngineDown) {
                // Engine gone: the job cannot be running anymore. Mark it stopped below.
            }
        }
        jobs.markStopped(id)
        return jobs.get(id)
    }
}
