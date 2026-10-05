package keel.api.inbox

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import keel.api.engine.EngineClient
import keel.api.flow.FlowService
import keel.api.projects.ProjectService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.util.concurrent.ConcurrentHashMap

/** One thing that waits for a person, in any project (GET /api/inbox). */
data class InboxItem(
    val projectId: String,
    val projectName: String,
    val threadId: String,
    /** The flow's title (what the user asked for). */
    val flow: String,
    val workflowId: String?,
    val step: String?,
    /** gate | clarify | fix | budget | usage, or "dependency" for a new dependency's approval. */
    val kind: String,
    val title: String,
    /** The pause's text, cut to [DETAIL_MAX] characters; `more` says it was cut (the Flow page shows all of it). */
    val detail: String,
    val more: Boolean,
    val options: List<String>,
    val choices: List<String>? = null,
    val questions: JsonNode? = null,
    val labels: JsonNode? = null,
    /** The question's id: send it back with an answer so a stale inbox cannot answer the next question. */
    val id: String? = null,
    val phase: String? = null,
    val ac: String? = null,
    val runMode: String? = null,
    /** When it started to wait (the last gate.waiting of the thread). */
    val since: String?,
)

data class InboxView(val items: List<InboxItem>, val count: Int, val kinds: List<String>, val projects: List<InboxProject>)
data class InboxProject(val id: String, val name: String, val count: Int)
data class InboxCount(val count: Int, val projects: Map<String, Int>)
data class InboxAct(val decision: String = "", val why: String? = null, val payload: Map<String, Any?>? = null, val id: String? = null)

/**
 * The inbox: every thread that waits for a person, across all projects. The api knows which threads wait (engine
 * events keep `threads.status`); what each one asks comes from the engine (GET /threads/{id}, cached for a few seconds),
 * or from the last state the api saved when the engine is down. Acting goes through FlowService.resume, the same path as
 * the Flow page and MCP.
 */
@Service
class InboxService(
    private val jdbc: JdbcTemplate,
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val flows: FlowService,
    private val mapper: ObjectMapper,
) {
    private data class Cached(val at: Long, val state: JsonNode)

    private val cache = ConcurrentHashMap<String, Cached>()

    private data class Row(val tid: String, val pid: String, val title: String, val workflowId: String?, val stateJson: String?, val updatedAt: String?)

    private fun waitingRows(): List<Row> {
        val listed = projects.rows().associateBy { it.id }
        return jdbc.query(
            "SELECT id, project_id, title, workflow_id, state_json, updated_at FROM threads WHERE status = 'waiting' ORDER BY updated_at",
            { rs, _ -> Row(rs.getString(1), rs.getString(2), rs.getString(3) ?: "", rs.getString(4), rs.getString(5), rs.getString(6)) },
        ).filter { it.pid in listed }
    }

    /** Cheap: the database only (for a badge). */
    fun count(): InboxCount {
        val rows = waitingRows()
        return InboxCount(rows.size, rows.groupingBy { it.pid }.eachCount())
    }

    fun list(project: String? = null, kind: String? = null): InboxView {
        val names = projects.rows().associate { it.id to it.name }
        val all = waitingRows().mapNotNull { item(it, names[it.pid] ?: it.pid) }.sortedBy { it.since ?: "" }
        val items = all.filter { (project.isNullOrBlank() || it.projectId == project) && (kind.isNullOrBlank() || it.kind == kind) }
        val perProject = all.groupingBy { it.projectId }.eachCount()
        return InboxView(items, all.size, all.map { it.kind }.distinct().sorted(),
            perProject.map { (id, n) -> InboxProject(id, names[id] ?: id, n) }.sortedBy { it.name.lowercase() })
    }

    /** Answers one inbox item the way the Flow page does; refuses when the thread now waits for something else. */
    fun act(tid: String, body: InboxAct): JsonNode {
        if (body.decision !in setOf("approve", "reject")) throw BadRequest("decision must be approve or reject")
        jdbc.query("SELECT 1 FROM threads WHERE id = ?", { _, _ -> 1 }, tid).firstOrNull() ?: throw NotFound("No flow $tid")
        val now = runCatching { engine.thread(tid) }.getOrNull()
        if (now != null) {
            val waiting = now.get("waiting")
            if (now.get("status")?.asText() != "waiting" || waiting == null || waiting.isNull) {
                flows.save(tid, now)
                cache.remove(tid)
                throw Conflict("This flow is not waiting for you any more", "Reload the inbox: someone answered it, or it moved on.")
            }
            val id = waiting.get("id")?.asText()
            if (!body.id.isNullOrBlank() && id != null && id != body.id) {
                cache.remove(tid)
                throw Conflict("This flow now asks something else", "Reload the inbox and read the new question first.")
            }
        }
        cache.remove(tid)
        return flows.resume(tid, body.decision, body.why, body.payload).also { cache.remove(tid) }
    }

    private fun stateOf(row: Row): JsonNode? {
        val hit = cache[row.tid]
        if (hit != null && System.currentTimeMillis() - hit.at < TTL_MS) return hit.state
        val state = try {
            engine.thread(row.tid).also { flows.save(row.tid, it) }
        } catch (e: ApiException) {
            // The engine is down or forgot the thread: the last state the api saw still says what it asked.
            row.stateJson?.let { runCatching { mapper.readTree(it) }.getOrNull() }
        } ?: return null
        cache[row.tid] = Cached(System.currentTimeMillis(), state)
        return state
    }

    private fun item(row: Row, projectName: String): InboxItem? {
        val state = stateOf(row) ?: return null
        val w = state.get("waiting")
        if (state.get("status")?.asText() != "waiting" || w == null || w.isNull) return null
        val title = w.get("title")?.asText().orEmpty()
        val rawKind = w.get("kind")?.asText() ?: "gate"
        val kind = if (rawKind == "fix" && title.startsWith("Approve new dependency")) "dependency" else rawKind
        val detail = w.get("detail")?.asText().orEmpty()
        val since = jdbc.queryForObject("SELECT MAX(at) FROM events WHERE thread_id = ? AND type = 'gate.waiting'", String::class.java, row.tid)
            ?: row.updatedAt
        return InboxItem(
            projectId = row.pid, projectName = projectName, threadId = row.tid,
            flow = row.title.ifBlank { state.get("title")?.asText().orEmpty() },
            workflowId = row.workflowId ?: state.get("workflow_id")?.asText(),
            step = w.get("step")?.asText(), kind = kind, title = title,
            detail = if (detail.length > DETAIL_MAX) detail.take(DETAIL_MAX).trimEnd() + "…" else detail, more = detail.length > DETAIL_MAX,
            options = w.get("options")?.takeIf { it.isArray }?.map { it.asText() } ?: listOf("approve", "reject"),
            choices = w.get("choices")?.takeIf { it.isArray }?.map { it.asText() },
            questions = w.get("questions")?.takeIf { it.isArray && it.size() > 0 },
            labels = w.get("labels")?.takeIf { it.isObject },
            id = w.get("id")?.asText(),
            phase = state.get("phase")?.asText(), ac = state.get("ac")?.takeIf { !it.isNull }?.asText(),
            runMode = state.get("run_mode")?.asText(), since = since,
        )
    }

    companion object {
        const val DETAIL_MAX = 700
        const val TTL_MS = 3_000L
    }
}
