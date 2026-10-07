package keel.api.plugins

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.Conflict
import keel.api.common.Time
import keel.api.engine.EngineClient
import keel.api.flow.FlowService
import keel.api.notifications.NotificationService
import keel.api.projects.ProjectService
import keel.api.settings.SettingsService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.time.Duration
import java.time.Instant

data class CiFixBody(val run: Long? = null)

/**
 * The CI/CD plugin (engine keel_engine/plugins/ci): the project's pipelines on GitHub Actions for Run › Jobs ›
 * Pipelines, a re-run of the failed jobs, and the ci-fix flow (the plugin's own workflow: read the failure, fix, commit,
 * push, wait for CI). A watcher looks every two minutes: a newly failed run is a notification, and with
 * Settings › When CI fails = fix the flow starts by itself, when the project folder is free and on that branch.
 */
@Service
class CiService(
    private val jdbc: JdbcTemplate,
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val plugins: PluginService,
    private val flows: FlowService,
    private val notifications: NotificationService,
    private val settings: SettingsService,
) {
    private val log = org.slf4j.LoggerFactory.getLogger(javaClass)

    private fun call(pid: String, op: String, extra: Map<String, Any?> = emptyMap(), long: Boolean = false): JsonNode {
        plugins.require(pid, "ci")
        val body = mapOf("root" to projects.root(pid).toString(),
            "keys" to (plugins.githubToken()?.let { mapOf("github" to it) } ?: emptyMap())) + extra
        return engine.post("/plugins/ci/$op", body, long = long)
    }

    fun runs(pid: String, branch: String?): JsonNode = call(pid, "runs", mapOf("branch" to (branch ?: ""), "limit" to 25), long = true)
    fun run(pid: String, id: Long): JsonNode = call(pid, "run", mapOf("run" to id), long = true)
    fun rerun(pid: String, id: Long): JsonNode = call(pid, "rerun", mapOf("run" to id), long = true)

    /** The ci-fix flow on the failed run's branch: in the project folder, which must be free and on that branch. */
    fun fix(pid: String, runId: Long?): JsonNode {
        plugins.require(pid, "ci")
        if (!plugins.on(pid, "git")) throw Conflict("The fix flow pushes its fix with the Git plugin", "Turn on Git in Tools › Plugins.")
        val root = projects.root(pid)
        val current = projects.branch(root)
        val r = runId?.let { run(pid, it) }
            ?: runs(pid, current).firstOrNull { it.path("failed").asBoolean() }
            ?: throw Conflict("No failed pipeline on ${current ?: "this branch"}", "Its pipelines pass, or none ran yet.")
        val branch = r.path("branch").asText()
        if (branch != current) {
            throw Conflict("CI failed on $branch, but the project folder is on ${current ?: "no branch"}",
                "Switch to $branch first (Code › Source control), then fix it.")
        }
        flows.folderFlow(pid)?.let { throw Conflict("A flow runs in the project folder", "Fix CI when that flow ends.") }
        val title = "Fix CI: ${r.path("workflow").asText("pipeline")} on $branch"
        return flows.start(pid, "ci-fix", title, null, request = "The ${r.path("workflow").asText()} pipeline failed on $branch " +
            "(run #${r.path("id").asText()}, ${r.path("url").asText()}). Find the cause and fix it.", where = "folder")
    }

    /** Every two minutes: the projects with the plugin on, their newly failed runs (once each). */
    @Scheduled(fixedDelayString = "\${keel.ci.tick-ms:120000}", initialDelayString = "\${keel.ci.tick-ms:120000}")
    fun watch() {
        if (plugins.githubToken() == null) return
        for (p in projects.rows()) {
            if (!runCatching { plugins.on(p.id, "ci") }.getOrDefault(false)) continue
            runCatching { check(p.id) }.onFailure { log.debug("ci watch of {} failed: {}", p.id, it.message) }
        }
    }

    /** One project's look: a failed run that finished in the last two hours and is not known yet is told once. */
    fun check(pid: String): List<Long> {
        val told = mutableListOf<Long>()
        val since = Instant.now().minus(Duration.ofHours(2))
        for (r in runs(pid, null)) {
            if (!r.path("failed").asBoolean() || r.path("status").asText() != "completed") continue
            val at = runCatching { Instant.parse(r.path("updated_at").asText()) }.getOrNull() ?: continue
            if (at.isBefore(since)) continue
            val id = r.path("id").asLong()
            val fresh = jdbc.update("INSERT OR IGNORE INTO ci_seen(project_id, run_id, conclusion, at) VALUES (?, ?, ?, ?)",
                pid, id, r.path("conclusion").asText(), Time.now()) == 1
            if (!fresh) continue
            told += id
            val title = "CI failed: ${r.path("workflow").asText()} on ${r.path("branch").asText()}"
            var body = "${r.path("title").asText()} · ${r.path("sha").asText().take(7)}"
            if (settings.effective(pid).ciOnFailure == "fix") {
                body += runCatching {
                    val t = fix(pid, id)
                    jdbc.update("UPDATE ci_seen SET fixed_by = ? WHERE project_id = ? AND run_id = ?", t.path("thread_id").asText(), pid, id)
                    " · keel started the fix flow."
                }.getOrElse { " · keel could not start the fix flow: ${it.message}" }
            }
            if (settings.effective(pid).ciOnFailure != "quiet") {
                notifications.create("failed", pid, title, body, "/jobs/pipelines")
            }
        }
        return told
    }
}

@RestController
@RequestMapping("/api/projects/{pid}/ci")
class CiController(private val ci: CiService) {
    @GetMapping("/runs")
    fun runs(@PathVariable pid: String, @RequestParam(required = false) branch: String?): JsonNode = ci.runs(pid, branch)

    @GetMapping("/runs/{id}")
    fun run(@PathVariable pid: String, @PathVariable id: Long): JsonNode = ci.run(pid, id)

    @PostMapping("/runs/{id}/rerun")
    fun rerun(@PathVariable pid: String, @PathVariable id: Long): JsonNode = ci.rerun(pid, id)

    /** Start the ci-fix flow for a failed run (default: the newest failed run of the project folder's branch). */
    @PostMapping("/fix")
    fun fix(@PathVariable pid: String, @RequestBody(required = false) body: CiFixBody?): JsonNode = ci.fix(pid, body?.run)

    /** Look now instead of waiting for the watcher (what it would tell). */
    @PostMapping("/check")
    fun check(@PathVariable pid: String): Map<String, List<Long>> = mapOf("told" to ci.check(pid))
}
