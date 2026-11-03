package keel.api.desk

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
import keel.api.tasks.NewTask
import keel.api.tasks.TaskService
import org.springframework.beans.factory.ObjectProvider
import org.springframework.core.annotation.Order
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.util.UUID

data class DeskRequest(
    val id: String, val projectId: String, val title: String, val body: String, val origin: String?, val status: String,
    val kind: String?, val urgency: String?, val area: String?, val suggest: String?, val sortedText: String?, val note: String?,
    val threadId: String?, val choice: String?, val taskId: String?, val createdAt: String, val updatedAt: String,
)

/** A past case: another request, a task or a flow of this project with words in common. Found here, with no model. */
data class PastCase(val type: String, val id: String, val title: String, val status: String?, val score: Double)

/** A request with what the page shows beside it. */
data class DeskView(val request: DeskRequest, val past: List<PastCase>, val warnings: List<String>)
data class DeskList(val requests: List<DeskRequest>, val sends: String)
data class NewRequest(val title: String = "", val body: String = "", val origin: String? = null)
data class CloseBody(val note: String? = null)
data class SortBody(val allowFake: Boolean = false, val allowDirty: Boolean = true)

/**
 * The Desk plugin's api part. A person pastes a support request; [DeskRequest]s live in its own table. Past cases are
 * found here by shared words, with no model. Pressing Sort starts the workflow desk-sort: only then the request's text,
 * and the titles of its past cases, go to the model company (the page says so). The answer at its gate comes back as an
 * event ([DeskEvents]): a fix or a change is a new flow, a task is made here, a close closes it.
 *
 *     GET    /api/projects/{pid}/desk/requests              the list, and what can go to the model company
 *     POST   /api/projects/{pid}/desk/requests              {title, body, origin}
 *     GET    /api/projects/{pid}/desk/requests/{id}         one, with its past cases and the warnings about its text
 *     POST   /api/projects/{pid}/desk/requests/{id}/sort    start the sorting flow
 *     POST   /api/projects/{pid}/desk/requests/{id}/close   {note}
 *     DELETE /api/projects/{pid}/desk/requests/{id}         forget it
 */
@Service
class DeskService(
    private val jdbc: JdbcTemplate,
    private val projects: ProjectService,
    private val flows: FlowService,
    private val tasks: ObjectProvider<TaskService>,
) {
    private val columns = "id, project_id, title, body, origin, status, kind, urgency, area, suggest, sorted_text, note, thread_id, choice, task_id, created_at, updated_at"

    private fun row(rs: java.sql.ResultSet) = DeskRequest(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5), rs.getString(6),
        rs.getString(7), rs.getString(8), rs.getString(9), rs.getString(10), rs.getString(11), rs.getString(12), rs.getString(13), rs.getString(14),
        rs.getString(15), rs.getString(16), rs.getString(17))

    fun list(pid: String): DeskList {
        projects.require(pid)
        return DeskList(jdbc.query("SELECT $columns FROM desk_request WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 200", { rs, _ -> row(rs) }, pid), SENDS)
    }

    fun create(pid: String, b: NewRequest): DeskRequest {
        projects.require(pid)
        val title = b.title.trim().ifEmpty { b.body.trim().lineSequence().firstOrNull().orEmpty().take(120) }
        if (b.body.isBlank()) throw BadRequest("Paste the request's text", "What the person wrote, in their words.")
        if (b.body.length > BODY_MAX) throw BadRequest("The request is too long (${BODY_MAX / 1000} KB at most)")
        val id = "d" + UUID.randomUUID().toString().replace("-", "").take(10)
        val now = Time.now()
        jdbc.update("INSERT INTO desk_request(id, project_id, title, body, origin, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'new', ?, ?)",
            id, pid, title.take(200), b.body.trim(), b.origin?.trim()?.ifBlank { null }?.take(200), now, now)
        return find(pid, id)
    }

    private fun find(pid: String, id: String): DeskRequest =
        jdbc.query("SELECT $columns FROM desk_request WHERE project_id = ? AND id = ?", { rs, _ -> row(rs) }, pid, id).firstOrNull()
            ?: throw NotFound("No request $id in this project")

    fun view(pid: String, id: String): DeskView {
        projects.require(pid)
        val r = find(pid, id)
        return DeskView(r, past(r), warnings(r.body))
    }

    /** Requests, tasks and flows of this project that share words with this one, best first. */
    fun past(r: DeskRequest): List<PastCase> {
        val mine = words(r.title + " " + r.body)
        if (mine.isEmpty()) return emptyList()
        val rows = mutableListOf<Triple<String, String, Pair<String, String?>>>()
        jdbc.query("SELECT id, title, status FROM desk_request WHERE project_id = ? AND id <> ? ORDER BY created_at DESC LIMIT ?",
            { rs, _ -> rows += Triple("request", rs.getString(1), rs.getString(2) to rs.getString(3)) }, r.projectId, r.id, SCAN)
        jdbc.query("SELECT id, title, status FROM tasks WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?",
            { rs, _ -> rows += Triple("task", rs.getString(1), rs.getString(2) to rs.getString(3)) }, r.projectId, SCAN)
        jdbc.query("SELECT id, title, status FROM threads WHERE project_id = ? AND workflow_id <> ? AND id <> ? AND hidden_at IS NULL ORDER BY updated_at DESC LIMIT ?",
            { rs, _ -> rows += Triple("flow", rs.getString(1), rs.getString(2) to rs.getString(3)) }, r.projectId, WORKFLOW, r.threadId ?: "", SCAN)
        return rows.mapNotNull { (type, id, t) ->
            val theirs = words(t.first)
            val shared = mine.intersect(theirs).size
            // a title is short: what counts is how much of the title is in the request, and at least two words in common
            val score = if (theirs.isEmpty() || shared < 2) 0.0 else shared.toDouble() / theirs.size
            if (score < MIN_SCORE) null else PastCase(type, id, t.first, t.second, Math.round(score * 100) / 100.0)
        }.sortedByDescending { it.score }.take(PAST_MAX)
    }

    /** Starts the sorting flow: from here on the text goes to the model company. A person pressed the button. */
    fun sort(pid: String, id: String, b: SortBody): DeskRequest {
        projects.require(pid)
        val r = find(pid, id)
        if (r.status != "new") throw Conflict("This request is ${r.status}", "Open its flow to go on.")
        val state = flows.start(pid, WORKFLOW, r.title.take(200), null, FlowCap(), allowFake = b.allowFake, allowDirty = b.allowDirty,
            request = prompt(r, past(r)), ownBranch = false)
        val tid = state.get("thread_id")?.asText()?.ifBlank { null } ?: throw Conflict("The engine did not say which flow it started")
        jdbc.update("UPDATE desk_request SET status = 'sorting', thread_id = ?, updated_at = ? WHERE id = ?", tid, Time.now(), id)
        return find(pid, id)
    }

    fun close(pid: String, id: String, note: String?): DeskRequest {
        projects.require(pid)
        find(pid, id)
        jdbc.update("UPDATE desk_request SET status = 'closed', note = ?, updated_at = ? WHERE id = ?", note?.trim()?.ifBlank { null }?.take(2000), Time.now(), id)
        return find(pid, id)
    }

    fun delete(pid: String, id: String) {
        projects.require(pid)
        find(pid, id)
        jdbc.update("DELETE FROM desk_request WHERE id = ?", id)
    }

    /** The sorter's answer reached its gate: keep what it said for the list. */
    fun sorted(threadId: String, text: String) {
        val r = byThread(threadId) ?: return
        fun marker(name: String) = Regex("(?im)^\\s*$name\\s*:\\s*(.+?)\\s*$").findAll(text).lastOrNull()?.groupValues?.get(1)?.take(80)
        jdbc.update("UPDATE desk_request SET status = 'sorted', kind = ?, urgency = ?, area = ?, suggest = ?, sorted_text = ?, updated_at = ? WHERE id = ?",
            marker("KIND"), marker("URGENCY"), marker("AREA"), marker("NEXT"), text.take(TEXT_MAX), Time.now(), r.id)
    }

    /** A person answered the gate. Only this makes anything happen. */
    fun decided(threadId: String, choice: String?, why: String?) {
        val r = byThread(threadId) ?: return
        val now = Time.now()
        when (choice) {
            "fix_flow", "change_flow" -> jdbc.update("UPDATE desk_request SET status = 'handed', choice = ?, updated_at = ? WHERE id = ?", choice, now, r.id)
            "make_task" -> {
                val taskId = tasks.ifAvailable?.create(r.projectId, NewTask(title = r.title.take(300), description = taskText(r), type = if (r.kind == "bug") "bug" else "task"))?.id
                jdbc.update("UPDATE desk_request SET status = 'handed', choice = ?, task_id = ?, updated_at = ? WHERE id = ?", choice, taskId, now, r.id)
            }
            "close" -> jdbc.update("UPDATE desk_request SET status = 'closed', choice = ?, note = ?, updated_at = ? WHERE id = ?", choice, why?.ifBlank { null }?.take(2000), now, r.id)
        }
    }

    private fun taskText(r: DeskRequest) = "From the Desk.\n\n${r.body}" + (r.sortedText?.let { "\n\nHow keel sorted it:\n\n$it" } ?: "")

    /** The flow failed or was stopped before a person chose: the request can be sorted again. */
    fun ended(threadId: String) {
        val r = byThread(threadId)?.takeIf { it.status == "sorting" || it.status == "sorted" } ?: return
        jdbc.update("UPDATE desk_request SET status = 'new', updated_at = ? WHERE id = ?", Time.now(), r.id)
    }

    private fun byThread(tid: String): DeskRequest? =
        if (tid.isBlank()) null else jdbc.query("SELECT $columns FROM desk_request WHERE thread_id = ?", { rs, _ -> row(rs) }, tid).firstOrNull()

    fun waiting(pid: String?): List<DeskRequest> = jdbc.query(
        "SELECT $columns FROM desk_request WHERE status = 'new'" + (if (pid != null) " AND project_id = ?" else "") + " ORDER BY created_at LIMIT 100",
        { rs, _ -> row(rs) }, *listOfNotNull(pid).toTypedArray())

    companion object {
        const val WORKFLOW = "desk-sort"
        const val BODY_MAX = 50_000
        const val TEXT_MAX = 60_000
        const val SCAN = 300
        const val PAST_MAX = 5
        const val MIN_SCORE = 0.34
        const val SENDS = "When you press Sort, the request's text and the titles of its past cases go to your model company, and agents read this project's repo. " +
            "The \"from\" field stays in keel. Nothing goes anywhere before you press Sort. Take out personal or customer data you may not share with that company."
        private val STOP = setOf("the", "and", "for", "that", "this", "with", "you", "your", "are", "was", "were", "but", "not", "have", "has", "had", "from", "they",
            "them", "our", "can", "could", "would", "should", "will", "when", "what", "why", "how", "does", "did", "get", "got", "please", "there", "here", "its", "into", "out", "all", "any")

        fun words(text: String): Set<String> = Regex("[\\p{L}\\p{N}]{3,}").findAll(text.lowercase()).map { it.value }.filterNot { it in STOP }.toSet()

        /** What in a text may be personal data, in plain words: shown before it can go to a model. */
        fun warnings(text: String): List<String> = listOfNotNull(
            "an e-mail address".takeIf { Regex("[\\w.+-]+@[\\w-]+\\.[\\w.-]+").containsMatchIn(text) },
            "a phone number or another long number".takeIf { Regex("(?<![\\w.])\\+?\\d[\\d ()/-]{8,}\\d").containsMatchIn(text) },
            "something that looks like a password or key".takeIf { Regex("(?i)\\b(password|passwd|api[_ -]?key|secret|token)\\b\\s*[:=]").containsMatchIn(text) },
        )

        /** The flow's request: the text as data between its lines, then the titles of the past cases. Never `origin`. */
        fun prompt(r: DeskRequest, past: List<PastCase>): String = buildString {
            appendLine("--- the request ---")
            appendLine(r.body.replace("--- end of the request ---", "(line removed)"))
            appendLine("--- end of the request ---")
            if (past.isNotEmpty()) {
                appendLine()
                appendLine("Past cases in this project (their titles):")
                past.forEach { appendLine("- ${it.type} ${it.id}: ${it.title}" + (it.status?.let { s -> " ($s)" } ?: "")) }
            }
        }
    }
}

/** What happens to a sorting flow: its gate waits (the sorter is done), a person answers it, or it ends without an answer. */
@Component
class DeskGateEvents(private val desk: DeskService) : EngineEventHandler {
    override val prefix = "gate."
    override fun handle(event: EngineEvent) {
        if (event.step != "decide") return
        when (event.type) {
            "gate.waiting" -> desk.sorted(event.threadId, event.data["detail"]?.toString().orEmpty())
            "gate.decided" -> desk.decided(event.threadId, event.data["choice"]?.toString(), event.data["why"]?.toString())
        }
    }
}

@Component
class DeskEndEvents(private val desk: DeskService) : EngineEventHandler {
    override val prefix = "thread."
    override fun handle(event: EngineEvent) {
        if (event.type == "thread.failed" || (event.type == "thread.done" && event.data["status"] == "stopped")) desk.ended(event.threadId)
    }
}

/** A request nobody sorted yet waits in the Inbox, with its own card (the web's slot inbox.card, kind desk-request). */
@Component
@Order(20)
class DeskInbox(private val desk: DeskService) : InboxSource {
    override val kind = "desk"

    override fun items(pid: String?): List<InboxItem> = desk.waiting(pid).map { r ->
        InboxItem(projectId = r.projectId, projectName = "", threadId = "", flow = r.title, workflowId = DeskService.WORKFLOW, step = null, kind = "desk-request",
            title = r.title, detail = r.body.take(400), more = r.body.length > 400, options = emptyList(), id = r.id, since = r.createdAt)
    }

    override fun waiting(pid: String?): Int = desk.waiting(pid).size
}

@RestController
@RequestMapping("/api/projects/{pid}/desk/requests")
class DeskController(private val desk: DeskService) {
    @GetMapping
    fun list(@PathVariable pid: String): DeskList = desk.list(pid)

    @PostMapping
    fun create(@PathVariable pid: String, @RequestBody body: NewRequest): DeskView = desk.create(pid, body).let { desk.view(pid, it.id) }

    @GetMapping("/{id}")
    fun one(@PathVariable pid: String, @PathVariable id: String): DeskView = desk.view(pid, id)

    @PostMapping("/{id}/sort")
    fun sort(@PathVariable pid: String, @PathVariable id: String, @RequestBody(required = false) body: SortBody?): DeskRequest =
        desk.sort(pid, id, body ?: SortBody())

    @PostMapping("/{id}/close")
    fun close(@PathVariable pid: String, @PathVariable id: String, @RequestBody(required = false) body: CloseBody?): DeskRequest =
        desk.close(pid, id, body?.note)

    @DeleteMapping("/{id}")
    fun delete(@PathVariable pid: String, @PathVariable id: String): Map<String, Boolean> {
        desk.delete(pid, id)
        return mapOf("deleted" to true)
    }
}
