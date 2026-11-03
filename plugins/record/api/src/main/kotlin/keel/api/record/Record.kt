package keel.api.record

import keel.api.common.Json
import keel.api.common.NotFound
import keel.api.projects.ProjectService
import org.springframework.http.HttpHeaders
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

/** One flow in the Record page's list. */
data class RecordRow(
    val threadId: String, val title: String, val workflowId: String?, val status: String, val startedAt: String,
    val updatedAt: String, val tokens: Long, val costUsd: Double, val gates: Int, val commits: Int,
)

/** Who asked for the flow, and how it reached keel. `by` stays one sentence: keel has one person and keeps no names. */
data class RecordAsked(
    val by: String, val via: String, val title: String, val workflowId: String?, val request: String?, val startedAt: String,
    val parent: String?, val source: String?, val task: String?,
)

/** One run of an agent or a code step, as keel's Jobs page has it. */
data class RecordRun(
    val id: String, val agent: String?, val step: String?, val phase: String?, val ac: String?, val provider: String?,
    val model: String?, val status: String, val startedAt: String, val endedAt: String?, val tokensIn: Long, val tokensOut: Long,
    val tokensCached: Long, val costUsd: Double, val result: String?,
)

/** One answer at a gate. `who` is who gave it: the person, keel's run mode, keel itself (not asked) or the Supervisor. */
data class RecordGate(
    val at: String, val step: String?, val gate: String?, val decision: String?, val choice: String?, val why: String?,
    val who: String, val ac: String?, val item: String?,
)

/** A commit, branch, worktree or pull request the flow made, from keel's own list. */
data class RecordApplied(val at: String, val kind: String, val ref: String, val subject: String?, val files: List<String>)

/** Something else that acted on the flow: an MCP client, keel TUI, a run mode change, a refused edit, a Supervisor note. */
data class RecordOther(val at: String, val type: String, val text: String)

data class RecordCost(val tokensIn: Long, val tokensOut: Long, val tokensCached: Long, val usd: Double, val runs: Int)

/** All of it for one flow. */
data class FlowRecord(
    val threadId: String, val projectId: String, val status: String, val updatedAt: String, val branch: String?, val error: String?,
    val waiting: String?, val asked: RecordAsked, val ran: List<RecordRun>, val gates: List<RecordGate>, val applied: List<RecordApplied>,
    val other: List<RecordOther>, val cost: RecordCost, val limits: List<String>,
)

/**
 * The Record plugin's api part. It joins what keel already keeps about one flow and writes nothing:
 *
 *     threads       the flow, its status and branch
 *     events        thread.started (who, how, the request), gate.decided, supervisor.*, *.call, thread.mode, guard.refused
 *     agent_calls   what ran, the model, tokens and cost
 *     flow_applied  commits, the branch, the worktree
 *
 *     GET /api/projects/{pid}/record/flows?limit=                 the project's flows, newest first
 *     GET /api/projects/{pid}/record/flows/{tid}                  one flow's record
 *     GET /api/projects/{pid}/record/flows/{tid}/export?format=   the same as a file: md (default) or json
 *
 * Reading is the whole job, so it has no permission beyond its pages. Reads pass keel's sign-in as any page's do.
 */
@Service
class RecordService(private val jdbc: JdbcTemplate, private val projects: ProjectService) {

    fun flows(pid: String, limit: Int): List<RecordRow> {
        projects.require(pid)
        return jdbc.query(
            """SELECT t.id, t.title, t.workflow_id, t.status, t.created_at, t.updated_at,
                 (SELECT COALESCE(SUM(c.tokens_in + c.tokens_out), 0) FROM agent_calls c WHERE c.thread_id = t.id),
                 (SELECT COALESCE(SUM(c.cost_usd), 0) FROM agent_calls c WHERE c.thread_id = t.id),
                 (SELECT COUNT(*) FROM events e WHERE e.thread_id = t.id AND e.type = 'gate.decided'),
                 (SELECT COUNT(*) FROM flow_applied a WHERE a.thread_id = t.id AND a.kind = 'commit')
               FROM threads t WHERE t.project_id = ? AND t.hidden_at IS NULL ORDER BY t.created_at DESC, t.rowid DESC LIMIT ?""",
            { rs, _ ->
                RecordRow(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5), rs.getString(6),
                    rs.getLong(7), rs.getDouble(8), rs.getInt(9), rs.getInt(10))
            },
            pid, limit.coerceIn(1, 200),
        )
    }

    fun of(pid: String, tid: String): FlowRecord {
        projects.require(pid)
        val t = jdbc.query(
            "SELECT title, workflow_id, status, created_at, updated_at, branch, error, source, state_json FROM threads WHERE project_id = ? AND id = ?",
            { rs, _ ->
                mapOf("title" to rs.getString(1), "workflow" to rs.getString(2), "status" to rs.getString(3), "created" to rs.getString(4),
                    "updated" to rs.getString(5), "branch" to rs.getString(6), "error" to rs.getString(7), "source" to rs.getString(8),
                    "state" to rs.getString(9))
            },
            pid, tid,
        ).firstOrNull() ?: throw NotFound("No flow $tid in this project", "Open the flow from its own project.")
        val events = events(pid, tid)
        val started = events.firstOrNull { it.type == "thread.started" }?.data ?: emptyMap()
        val via = started["via"]?.toString()
        val task = runCatching {
            jdbc.query("SELECT id FROM tasks WHERE thread_id = ? LIMIT 1", { rs, _ -> rs.getString(1) }, tid).firstOrNull()
        }.getOrNull()
        val parent = (started["parent"] as? Map<*, *>)?.get("thread_id")?.toString() ?: started["parent"]?.takeIf { it !is Map<*, *> }?.toString()?.ifBlank { null }
        val asked = RecordAsked(
            by = started["by"]?.toString()?.takeIf { via == "slack" && it.startsWith("slack:") }?.let { "a person in Slack: ${it.removePrefix("slack:")}" } ?: BY_PERSON,
            via = howStarted(via, parent, task, t["source"]), title = t["title"].orEmpty(), workflowId = t["workflow"],
            request = started["request"]?.toString()?.ifBlank { null }, startedAt = t["created"].orEmpty(), parent = parent,
            source = t["source"], task = task,
        )
        val ran = runs(tid)
        val cost = RecordCost(ran.sumOf { it.tokensIn }, ran.sumOf { it.tokensOut }, ran.sumOf { it.tokensCached },
            ran.sumOf { it.costUsd }, ran.size)
        val waiting = t["state"]?.let { runCatching { Json.readMap(it)["waiting"] as? Map<*, *> }.getOrNull() }
            ?.takeIf { t["status"] == "waiting" }?.get("title")?.toString()
        return FlowRecord(tid, pid, t["status"].orEmpty(), t["updated"].orEmpty(), t["branch"], t["error"]?.take(500), waiting, asked, ran,
            events.filter { it.type == "gate.decided" }.map(::gateOf), applied(tid), events.mapNotNull(::otherOf), cost, LIMITS)
    }

    private fun runs(tid: String): List<RecordRun> = jdbc.query(
        """SELECT id, agent, step, phase, ac, provider, model, status, started_at, ended_at, tokens_in, tokens_out, tokens_cached, cost_usd, result
           FROM agent_calls WHERE thread_id = ? ORDER BY started_at, rowid""",
        { rs, _ ->
            RecordRun(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5), rs.getString(6), rs.getString(7),
                rs.getString(8), rs.getString(9), rs.getString(10), rs.getLong(11), rs.getLong(12), rs.getLong(13), rs.getDouble(14),
                rs.getString(15)?.take(RESULT_MAX))
        },
        tid,
    )

    private fun applied(tid: String): List<RecordApplied> = jdbc.query(
        "SELECT at, kind, ref, subject, files_json FROM flow_applied WHERE thread_id = ? ORDER BY at, id",
        { rs, _ ->
            RecordApplied(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4),
                rs.getString(5)?.let { runCatching { Json.readList(it) }.getOrNull() } ?: emptyList())
        },
        tid,
    )

    private data class Ev(val type: String, val step: String?, val at: String, val data: Map<String, Any?>)

    private fun events(pid: String, tid: String): List<Ev> = jdbc.query(
        "SELECT type, step, at, data_json FROM events WHERE project_id = ? AND thread_id = ? AND type IN (${TYPES.joinToString(", ") { "?" }}) ORDER BY id LIMIT ?",
        { rs, _ -> Ev(rs.getString(1), rs.getString(2), rs.getString(3), Json.readMap(rs.getString(4))) },
        pid, tid, *TYPES.toTypedArray(), EVENTS_MAX,
    )

    private fun gateOf(e: Ev): RecordGate {
        val d = e.data
        return RecordGate(e.at, e.step, d["gate"]?.toString(), d["decision"]?.toString(), d["choice"]?.toString(), d["why"]?.toString()?.ifBlank { null },
            whoAnswered(d["by"]?.toString(), d["why"]?.toString()), d["ac"]?.toString(), d["item"]?.toString())
    }

    private fun otherOf(e: Ev): RecordOther? {
        val d = e.data
        val text = when (e.type) {
            "slack.call" -> "Slack: ${d["who"]} ${d["what"]}" + if (d["ok"] == false) " (refused)" else ""
            "supervisor.diagnosis" -> "The Supervisor's diagnosis: ${d["summary"]}"
            "supervisor.fixed" -> "The Supervisor made a small fix in ${(d["files"] as? List<*>)?.joinToString(", ")}: ${d["why"]}"
            "mcp.call", "tui.call", "xal.call" -> "${d["client"] ?: e.type.substringBefore('.')}: ${d["tool"] ?: "a call"}" +
                (d["sql"]?.let { " ($it)" } ?: "") + if (d["ok"] == false) " (refused)" else ""
            "thread.mode" -> "the run mode became ${d["mode"]}" + (d["by"]?.let { " ($it)" } ?: "")
            "guard.refused" -> "keel refused: ${d["reason"] ?: d["path"] ?: d["tool"] ?: "a change"}"
            "supervisor.decided", "supervisor.escalated", "supervisor.paused" ->
                "the Supervisor (${e.type.substringAfter('.')}): ${d["summary"] ?: d["why"] ?: ""}".trimEnd(' ', ':')
            "thread.failed" -> "the flow failed: ${d["error"] ?: ""}".trimEnd(' ', ':')
            else -> return null
        }
        return RecordOther(e.at, e.type, text.take(OTHER_MAX))
    }

    companion object {
        const val BY_PERSON = "the person signed in to this keel (keel has one person and keeps no names)"
        val LIMITS = listOf(
            "keel has one person: it records how an answer came (a person, a run mode, the Supervisor), not a person's name.",
            "An answer through an MCP client counts as the person's here. The client's call is listed under \"Also on this flow\".",
            "This record is what keel stored in its own database. It is not signed, and whoever runs keel can change it. Treat it as working notes, not as proof.",
        )
        private val TYPES = listOf("thread.started", "gate.decided", "supervisor.decided", "supervisor.escalated", "supervisor.paused", "supervisor.diagnosis", "supervisor.fixed",
            "mcp.call", "tui.call", "xal.call", "slack.call", "thread.mode", "guard.refused", "thread.failed")
        private const val EVENTS_MAX = 5000
        private const val RESULT_MAX = 400
        private const val OTHER_MAX = 400

        internal fun howStarted(via: String?, parent: String?, task: String?, source: String?): String = when {
            parent != null -> "handed over by another flow"
            via == "mcp" -> "an MCP client (keel_start_flow)"
            via == "tui" -> "keel TUI"
            via == "xal" -> "the xal client"
            via == "slack" -> "Slack"
            task != null -> "a task on the Tasks page"
            !source.isNullOrBlank() -> "the keel dashboard, for $source"
            else -> "the keel dashboard"
        }

        /** Who gave an answer, from the event's `by` and its line (keel's run modes write "auto-approved (mode x)"). */
        internal fun whoAnswered(by: String?, why: String?): String = when {
            by == "supervisor" -> "the Supervisor"
            by?.startsWith("slack:") == true -> "a person in Slack: ${by.removePrefix("slack:")}"
            by == "engine" -> "keel (not asked: the gate's condition did not hold)"
            why?.contains("auto-approved (mode ") == true -> "keel's run mode"
            why?.startsWith("waived") == true -> "keel (the gate was waived)"
            else -> "the person"
        }

        /** The record as a file a person can read, keep or hand on. */
        fun markdown(r: FlowRecord): String = buildString {
            appendLine("# Record of a flow: ${oneLine(r.asked.title)}")
            appendLine()
            appendLine("- Flow: `${r.threadId}` in project `${r.projectId}`")
            appendLine("- Status: ${r.status}" + (r.waiting?.let { " (waiting: ${oneLine(it)})" } ?: ""))
            r.branch?.let { appendLine("- Branch: `$it`") }
            r.error?.let { appendLine("- Error: ${oneLine(it)}") }
            appendLine()
            appendLine("## Who asked")
            appendLine()
            appendLine("- By: ${r.asked.by}")
            appendLine("- Through: ${r.asked.via}")
            appendLine("- Workflow: ${r.asked.workflowId ?: "unknown"}")
            appendLine("- Started: ${r.asked.startedAt}")
            r.asked.task?.let { appendLine("- Task: $it") }
            r.asked.parent?.let { appendLine("- Parent flow: `$it`") }
            appendLine()
            appendLine(r.asked.request?.let { "The request:\n\n" + quote(it) } ?: "keel kept no request text for this flow.")
            appendLine()
            appendLine("## What ran (${r.ran.size})")
            appendLine()
            if (r.ran.isEmpty()) appendLine("Nothing ran.") else {
                appendLine("| started | step | agent | model | status | tokens | cost |")
                appendLine("|---|---|---|---|---|---|---|")
                r.ran.forEach {
                    appendLine("| ${it.startedAt} | ${cell(it.step)} | ${cell(it.agent)} | ${cell(it.model)} | ${it.status} | ${it.tokensIn + it.tokensOut} | ${usd(it.costUsd)} |")
                }
            }
            appendLine()
            appendLine("## Gate answers (${r.gates.size})")
            appendLine()
            if (r.gates.isEmpty()) appendLine("No gate was answered.") else r.gates.forEach { g ->
                val what = listOfNotNull(g.decision, g.choice?.let { "[$it]" }).joinToString(" ")
                val about = listOfNotNull(g.gate ?: g.step, g.ac, g.item).joinToString(" · ")
                appendLine("- ${g.at} **${oneLine(about)}**: $what, by ${g.who}" + (g.why?.let { ". ${oneLine(it)}" } ?: ""))
            }
            appendLine()
            val commits = r.applied.filter { it.kind == "commit" }
            appendLine("## Commits (${commits.size})")
            appendLine()
            if (commits.isEmpty()) appendLine("No commit.") else commits.forEach { c ->
                appendLine("- `${c.ref.take(12)}` ${oneLine(c.subject ?: "")}" + if (c.files.isEmpty()) "" else " (${c.files.joinToString(", ")})")
            }
            r.applied.filter { it.kind != "commit" }.takeIf { it.isNotEmpty() }?.let { more ->
                appendLine()
                appendLine("Also: " + more.joinToString("; ") { "${it.kind} `${it.ref}`" })
            }
            appendLine()
            appendLine("## Cost")
            appendLine()
            appendLine("${r.cost.tokensIn + r.cost.tokensOut} tokens (in ${r.cost.tokensIn}, out ${r.cost.tokensOut}, cached ${r.cost.tokensCached}), ${usd(r.cost.usd)}, ${r.cost.runs} runs. Plan logins show no cost per call, so the real bill may differ.")
            if (r.other.isNotEmpty()) {
                appendLine()
                appendLine("## Also on this flow (${r.other.size})")
                appendLine()
                r.other.forEach { appendLine("- ${it.at} ${oneLine(it.text)}") }
            }
            appendLine()
            appendLine("## What this record is not")
            appendLine()
            r.limits.forEach { appendLine("- $it") }
        }

        private fun oneLine(s: String) = s.replace(Regex("\\s+"), " ").trim()
        private fun cell(s: String?) = oneLine(s ?: "").replace("|", "\\|").ifEmpty { "-" }
        private fun quote(s: String) = s.trim().lines().joinToString("\n") { "> $it" }
        private fun usd(v: Double) = "$" + String.format(java.util.Locale.ROOT, "%.4f", v)
    }
}

@RestController
@RequestMapping("/api/projects/{pid}/record")
class RecordController(private val record: RecordService) {

    @GetMapping("/flows")
    fun flows(@PathVariable pid: String, @RequestParam(defaultValue = "50") limit: Int): List<RecordRow> = record.flows(pid, limit)

    @GetMapping("/flows/{tid}")
    fun flow(@PathVariable pid: String, @PathVariable tid: String): FlowRecord = record.of(pid, tid)

    /** The record as a file: md (default) or json. Anything else is md, so a wrong word still gives the person the file. */
    @GetMapping("/flows/{tid}/export")
    fun export(@PathVariable pid: String, @PathVariable tid: String, @RequestParam(defaultValue = "md") format: String): ResponseEntity<*> {
        val r = record.of(pid, tid)
        val name = "keel-record-${tid.take(12).replace(Regex("[^A-Za-z0-9_-]"), "_")}"
        val builder = ResponseEntity.ok()
        return if (format == "json") {
            builder.header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"$name.json\"").contentType(MediaType.APPLICATION_JSON).body(r)
        } else {
            builder.header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"$name.md\"")
                .contentType(MediaType("text", "markdown", Charsets.UTF_8)).body(RecordService.markdown(r))
        }
    }
}
