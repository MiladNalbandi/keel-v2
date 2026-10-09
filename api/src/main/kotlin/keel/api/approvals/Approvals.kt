package keel.api.approvals

import com.fasterxml.jackson.core.type.TypeReference
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Json
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.engine.EngineClient
import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.events.EventHub
import keel.api.events.str
import org.springframework.beans.factory.ObjectProvider
import org.springframework.http.HttpStatus
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.sql.ResultSet
import java.time.Instant
import java.util.UUID

/** One question for a person (table approvals). [payload] is what the asker sent (for the engine: its question as is). */
data class Approval(
    val id: String,
    val projectId: String,
    val kind: String,
    val source: String,
    val title: String,
    val detail: String,
    val payload: JsonNode?,
    /** waiting | approved | denied | expired | closed */
    val status: String,
    val decision: String?,
    val why: String?,
    val requestedBy: String?,
    val createdAt: String,
    val decidedAt: String?,
    val decidedBy: String?,
)

/** The person's answer: for the engine's questions once | always | deny; for the api's own, its kind's decisions. */
data class ApprovalDecide(val decision: String = "", val why: String = "")

/**
 * A kind of question the api asks itself (step 4: the marketplace's `plugin-install`): what its decisions are and what
 * happens when the person decides. Every other kind is the engine's (keel_engine/approvals.py): [ApprovalService.decide]
 * sends the answer there.
 */
interface ApprovalHandler {
    val kind: String

    /** The decisions a person may give; "deny" says no. */
    val decisions: List<String> get() = listOf("approve", "deny")

    fun decided(approval: Approval, decision: String, why: String)

    /** Ends its own waiting questions whose time is up (before the waiting ones are listed). */
    fun tidy() {}
}

/**
 * Approvals: keel's one place for "ask a person and wait" (docs/plugins/09-step2-contract.md §1). The engine asks and
 * waits in memory (KeelBot's commands, keel2 mcp's acting tools); its approval.* events become rows here. The api can ask
 * too ([create]). The person answers with [decide], from the Inbox or KeelBot's panel.
 */
@Service
class ApprovalService(
    private val jdbc: JdbcTemplate,
    private val engine: EngineClient,
    private val mapper: ObjectMapper,
    private val hub: EventHub,
    handlers: ObjectProvider<ApprovalHandler>,
) {
    private val own by lazy { handlers.orderedStream().toList().associateBy { it.kind } }

    /** The engine's question (KeelBot's command, keel2 mcp's acting tool); the api's own kinds have a handler. */
    fun engineOwned(a: Approval): Boolean = a.kind !in own

    fun decisionsOf(a: Approval): List<String> = own[a.kind]?.decisions ?: ENGINE_DECISIONS

    // ---- the engine's questions -------------------------------------------------------------------------------

    /** approval.asked adds the row; approval.answered says how it ended (the person, or keel: time up, asker gone). */
    fun stored(e: EngineEvent) {
        val d = e.data
        val id = d.str("id") ?: return
        val pid = d.str("project") ?: e.projectId
        insert(id, pid, d - ANSWER_FIELDS, e.at)
        if (e.type == "approval.answered") {
            val decision = d.str("decision")
            val status = d.str("status") ?: if (decision == "deny") DENIED else APPROVED
            jdbc.update(
                "UPDATE approvals SET status = ?, decision = ?, why = ?, decided_at = ?, decided_by = ? WHERE id = ?",
                status, decision, d.str("why"), e.at ?: Time.now(), d.str("by") ?: "person", id,
            )
        }
    }

    /**
     * The engine keeps its questions in memory; the table follows it. A question the engine has and the table missed (the
     * api was down when it was asked) is added. A waiting row the engine no longer has (it restarted) is closed, once it
     * is older than [SYNC_MARGIN_S] (a question asked a moment ago may not be in the engine's answer yet).
     * False when the engine cannot be asked: its rows stay as they are.
     */
    fun sync(): Boolean {
        val started = Instant.now()
        val live = try {
            engine.get("/approvals")
        } catch (e: ApiException) {
            return false
        }
        if (!live.isArray) return false
        val ids = HashSet<String>()
        for (q in live) {
            val d: Map<String, Any?> = mapper.convertValue(q, MAP)
            val id = d.str("id") ?: continue
            ids += id
            insert(id, d.str("project") ?: "", d, null)
        }
        val before = started.minusSeconds(SYNC_MARGIN_S)
        for (a in rows("status = ?", listOf(WAITING))) {
            if (!engineOwned(a) || a.id in ids) continue
            val at = runCatching { Instant.parse(a.createdAt) }.getOrNull() ?: continue
            if (at.isBefore(before)) close(a.id, "keel no longer has this question (it restarted), so it ended unanswered.")
        }
        return true
    }

    private fun insert(id: String, pid: String, d: Map<String, Any?>, at: String?) {
        val source = d.str("source") ?: "engine"
        jdbc.update(
            """INSERT OR IGNORE INTO approvals(id, project_id, kind, source, title, detail, payload_json, status, requested_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?)""",
            id, pid, d.str("kind") ?: "command", source, d.str("title") ?: "", d.str("command") ?: d.str("detail") ?: "",
            Json.write(d), d.str("session") ?: source, d.str("at") ?: at ?: Time.now(),
        )
    }

    private fun close(id: String, why: String) {
        jdbc.update("UPDATE approvals SET status = ?, why = ?, decided_at = ?, decided_by = 'keel' WHERE id = ? AND status = ?",
            CLOSED, why, Time.now(), id, WAITING)
    }

    // ---- the api's own questions --------------------------------------------------------------------------------

    /** A question the api asks itself; its kind needs an [ApprovalHandler] (the api decides it, not the engine). */
    fun create(kind: String, projectId: String, title: String, detail: String = "", payload: Map<String, Any?> = emptyMap(),
               requestedBy: String? = null, source: String = "api"): Approval {
        require(kind in own) { "No ApprovalHandler for the kind $kind: the api could not decide it" }
        val id = "a_" + UUID.randomUUID().toString().replace("-", "").take(12)
        jdbc.update(
            """INSERT INTO approvals(id, project_id, kind, source, title, detail, payload_json, status, requested_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?)""",
            id, projectId, kind, source, title.take(300), detail.take(4000), Json.write(payload), requestedBy, Time.now(),
        )
        changed(projectId)
        return find(id)!!
    }

    /** The asker adds to its own waiting question (a second agent asks for the same plugin: its reason joins). */
    fun amend(id: String, detail: String, payload: Map<String, Any?>): Approval? {
        val n = jdbc.update("UPDATE approvals SET detail = ?, payload_json = ? WHERE id = ? AND status = ?",
            detail.take(4000), Json.write(payload), id, WAITING)
        val a = find(id) ?: return null
        if (n > 0) changed(a.projectId)
        return a
    }

    /** The api ends one of its own waiting questions (its time is up): status expired or closed, decided by keel. */
    fun end(id: String, status: String, why: String): Boolean {
        val n = jdbc.update("UPDATE approvals SET status = ?, why = ?, decided_at = ?, decided_by = 'keel' WHERE id = ? AND status = ?",
            status, why, Time.now(), id, WAITING)
        if (n > 0) find(id)?.let { changed(it.projectId) }
        return n > 0
    }

    // ---- reading ------------------------------------------------------------------------------------------------

    fun find(id: String): Approval? = rows("id = ?", listOf(id)).firstOrNull()

    /** Waiting ones oldest first (the engine's checked first); any other status newest first. */
    fun list(status: String? = WAITING, project: String? = null): List<Approval> {
        val st = status?.trim()?.ifBlank { null }
        if (st != null && st !in STATUSES) throw BadRequest("Unknown status $st", "Use one of: ${STATUSES.joinToString(", ")}.")
        if (st == null || st == WAITING) {
            sync()
            own.values.forEach { runCatching { it.tidy() } }
        }
        val where = mutableListOf<String>()
        val args = mutableListOf<Any>()
        st?.let { where += "status = ?"; args += it }
        project?.trim()?.ifBlank { null }?.let { where += "project_id = ?"; args += it }
        return rows(where.joinToString(" AND ").ifBlank { "1 = 1" }, args, if (st == WAITING) "created_at, id" else "created_at DESC, id DESC LIMIT 200")
    }

    /** How many wait (the database only: for badges and a project's waiting count). */
    fun waitingCount(project: String?): Int =
        if (project.isNullOrBlank()) jdbc.queryForObject("SELECT COUNT(*) FROM approvals WHERE status = ?", Int::class.java, WAITING) ?: 0
        else jdbc.queryForObject("SELECT COUNT(*) FROM approvals WHERE status = ? AND project_id = ?", Int::class.java, WAITING, project) ?: 0

    /** How many of the api's own questions wait that belong to no project (keel-wide: a plugin install request). */
    fun waitingKeelWide(): Int {
        if (own.isEmpty()) return 0
        val kinds = own.keys.toList()
        return jdbc.queryForObject(
            "SELECT COUNT(*) FROM approvals WHERE status = ? AND project_id = '' AND kind IN (${kinds.joinToString(",") { "?" }})",
            Int::class.java, WAITING, *kinds.toTypedArray(),
        ) ?: 0
    }

    /** keel2 mcp polls its question: {waiting: true} until the person answers, then {decision: allow | deny, why}. */
    fun asked(id: String): JsonNode {
        val a = find(id)
        if (a != null && a.status != WAITING) {
            return mapper.valueToTree(mapOf("id" to id, "decision" to if (a.status == APPROVED) "allow" else "deny", "why" to (a.why ?: "")))
        }
        if (a != null && !engineOwned(a)) return mapper.valueToTree(mapOf("id" to id, "waiting" to true))
        return engine.get("/approvals/$id")       // the engine's, still waiting there (or answered a moment ago)
    }

    // ---- deciding -----------------------------------------------------------------------------------------------

    /**
     * The person's answer. The engine's questions go to the engine (POST /approvals/{id}: the one who asked goes on); the
     * api's own go to their [ApprovalHandler]. Then the row is marked. [project]: the answer must come from that project.
     */
    fun decide(id: String, decision: String, why: String = "", project: String? = null): Approval {
        val a = (find(id) ?: if (sync()) find(id) else null)?.takeIf { project == null || it.projectId == project }
            ?: throw NotFound(GONE)
        val choices = decisionsOf(a)
        if (decision !in choices) throw BadRequest("Answer ${choices.dropLast(1).joinToString(", ")} or ${choices.last()}")
        if (a.status != WAITING) throw NotFound(GONE)
        val note = why.trim().take(500)
        val handler = own[a.kind]
        if (handler == null) {
            try {
                engine.post("/approvals/$id", mapOf("decision" to decision, "why" to note))
            } catch (e: ApiException) {
                if (e.status != HttpStatus.NOT_FOUND) throw e
                close(id, "keel no longer has this question, so it ended unanswered.")
                throw NotFound(GONE)
            }
        } else {
            handler.decided(a, decision, note)
        }
        jdbc.update(
            "UPDATE approvals SET status = ?, decision = ?, why = ?, decided_at = ?, decided_by = 'person' WHERE id = ?",
            if (decision == "deny") DENIED else APPROVED, decision, note, Time.now(), id,
        )
        changed(a.projectId)
        return find(id)!!
    }

    /** What waits for a person changed: every tab's sidebar and Inbox badge reloads. */
    private fun changed(pid: String) {
        if (pid.isNotBlank()) hub.publish(pid, "project.changed", mapOf("id" to pid))
    }

    private fun rows(where: String, args: List<Any>, order: String = "created_at, id"): List<Approval> = jdbc.query(
        "SELECT $COLS FROM approvals WHERE $where ORDER BY $order", { rs, _ -> approval(rs) }, *args.toTypedArray(),
    )

    private fun approval(rs: ResultSet) = Approval(
        id = rs.getString(1), projectId = rs.getString(2), kind = rs.getString(3), source = rs.getString(4),
        title = rs.getString(5) ?: "", detail = rs.getString(6) ?: "",
        payload = rs.getString(7)?.let { runCatching { mapper.readTree(it) }.getOrNull() },
        status = rs.getString(8), decision = rs.getString(9), why = rs.getString(10), requestedBy = rs.getString(11),
        createdAt = rs.getString(12), decidedAt = rs.getString(13), decidedBy = rs.getString(14),
    )

    companion object {
        const val WAITING = "waiting"
        const val APPROVED = "approved"
        const val DENIED = "denied"
        const val CLOSED = "closed"
        val STATUSES = listOf(WAITING, APPROVED, DENIED, "expired", CLOSED)
        val ENGINE_DECISIONS = listOf("once", "always", "deny")
        const val GONE = "That question was answered already, or its command ended"
        const val SYNC_MARGIN_S = 30L
        private const val COLS =
            "id, project_id, kind, source, title, detail, payload_json, status, decision, why, requested_by, created_at, decided_at, decided_by"
        private val ANSWER_FIELDS = setOf("decision", "why", "status", "by")
        private val MAP = object : TypeReference<Map<String, Any?>>() {}
    }
}

/** approval.asked / approval.answered from the engine become rows. */
@Component
class ApprovalEvents(private val approvals: ApprovalService) : EngineEventHandler {
    override val prefix = "approval."

    override fun handle(event: EngineEvent) = approvals.stored(event)
}

@RestController
@RequestMapping("/api/approvals")
class ApprovalController(private val approvals: ApprovalService) {
    /** status: waiting (oldest first) | approved | denied | expired | closed; none: every status, newest first. */
    @GetMapping
    fun list(@RequestParam(required = false) status: String?, @RequestParam(required = false) project: String?): List<Approval> =
        approvals.list(status, project)

    @PostMapping("/{id}/decide")
    fun decide(@PathVariable id: String, @RequestBody body: ApprovalDecide): Approval = approvals.decide(id, body.decision, body.why)
}
