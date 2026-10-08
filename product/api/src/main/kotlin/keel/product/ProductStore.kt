package keel.product

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.module.kotlin.readValue
import keel.api.common.Time
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import java.sql.ResultSet
import java.util.UUID

// ---------- what keel Product keeps (its own tables, db/product/P1__product.sql) ----------

data class Initiative(
    val id: String,
    val n: Int,
    val title: String,
    val idea: String,
    val whyNow: String?,
    val outcomeHope: String?,
    val owner: String?,
    /** idea, brief, impact, decision, plan, delivery, outcome, done */
    val stage: String,
    /** new, running, waiting, ready, parked, failed, done */
    val status: String,
    val option: String?,
    val repos: List<String>,
    val metric: String?,
    val revisitAt: String?,
    val createdAt: String,
    val updatedAt: String,
)

data class ProductDoc(
    val initiativeId: String,
    val kind: String,
    val version: Int,
    val path: String,
    val sha: String?,
    val text: String,
    val data: Any?,
    val threadId: String?,
    val createdAt: String,
    val approvedAt: String?,
)

data class ProductRun(
    val threadId: String,
    val initiativeId: String,
    val stage: String,
    val workflowId: String,
    val status: String,
    val reason: String?,
    val startedAt: String,
    val endedAt: String?,
)

data class ProductQuestion(
    val id: String,
    val initiativeId: String,
    val stage: String,
    val role: String,
    val askedTo: String?,
    val about: String?,
    val text: String,
    val answer: String?,
    val answeredBy: String?,
    val status: String,
    val createdAt: String,
    val answeredAt: String?,
)

data class Disagreement(
    val id: String,
    val initiativeId: String,
    val stage: String,
    val author: String,
    val reason: String,
    val proposal: String?,
    val decider: String,
    val status: String,
    val outcome: String?,
    val decidedBy: String?,
    val createdAt: String,
    val decidedAt: String?,
)

data class ProductEvent(val id: Long, val initiativeId: String, val at: String, val actor: String, val kind: String, val text: String, val data: Any?)

data class FollowUp(
    val id: String,
    val initiativeId: String,
    val kind: String,
    val text: String,
    val owner: String?,
    val dueAt: String,
    val repeatDays: Int?,
    val ref: String?,
    val doneAt: String?,
    val lastRemindedAt: String?,
    val createdAt: String,
)

data class PlanItem(
    val initiativeId: String,
    val version: Int,
    val id: String,
    val epic: String,
    val team: String?,
    val repo: String?,
    val title: String,
    val body: Map<String, Any?>,
    val jiraKey: String?,
    val projectId: String?,
    val taskId: String?,
)

data class TeamPath(val teamId: String, val projectId: String, val glob: String, val source: String)

data class Team(
    val id: String,
    val name: String,
    val lead: String?,
    val jiraProject: String?,
    val capacityDays: Double?,
    val members: List<String>,
    val source: String,
    val owns: List<TeamPath>,
    val createdAt: String,
    val updatedAt: String,
)

data class TeamSuggestion(
    val id: String,
    val teamId: String,
    val page: String,
    val text: String,
    val source: String?,
    val status: String,
    val createdAt: String,
    val decidedAt: String?,
)

/** JDBC for keel Product's tables. Every write goes through here. */
@Component
class ProductStore(private val jdbc: JdbcTemplate, private val mapper: ObjectMapper, @Suppress("unused") schema: ProductSchema) {
    private fun json(v: Any?): String? = v?.let { mapper.writeValueAsString(it) }
    private fun <T> parse(s: String?, fallback: T, read: (String) -> T): T = if (s.isNullOrBlank()) fallback else runCatching { read(s) }.getOrDefault(fallback)
    private fun any(s: String?): Any? = parse(s, null) { mapper.readValue<Any>(it) }
    fun newId(prefix: String) = "$prefix-${UUID.randomUUID().toString().replace("-", "").take(10)}"

    // ---- initiatives
    private fun initiative(rs: ResultSet) = Initiative(
        rs.getString("id"), rs.getInt("n"), rs.getString("title"), rs.getString("idea"), rs.getString("why_now"),
        rs.getString("outcome_hope"), rs.getString("owner"), rs.getString("stage"), rs.getString("status"), rs.getString("option"),
        parse(rs.getString("repos_json"), emptyList()) { mapper.readValue<List<String>>(it) }, rs.getString("metric"),
        rs.getString("revisit_at"), rs.getString("created_at"), rs.getString("updated_at"),
    )

    fun initiatives(): List<Initiative> = jdbc.query("SELECT * FROM product_initiatives ORDER BY n DESC") { rs, _ -> initiative(rs) }
    fun initiative(id: String): Initiative? = jdbc.query("SELECT * FROM product_initiatives WHERE id = ?", { rs, _ -> initiative(rs) }, id).firstOrNull()

    fun nextNumber(): Int = (jdbc.queryForObject("SELECT COALESCE(MAX(n), 0) FROM product_initiatives", Int::class.java) ?: 0) + 1

    fun insert(i: Initiative) {
        jdbc.update(
            "INSERT INTO product_initiatives(id, n, title, idea, why_now, outcome_hope, owner, stage, status, option, repos_json, metric, revisit_at, created_at, updated_at) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            i.id, i.n, i.title, i.idea, i.whyNow, i.outcomeHope, i.owner, i.stage, i.status, i.option, json(i.repos), i.metric, i.revisitAt,
            i.createdAt, i.updatedAt,
        )
    }

    fun update(i: Initiative): Initiative {
        val next = i.copy(updatedAt = Time.now())
        jdbc.update(
            "UPDATE product_initiatives SET title = ?, idea = ?, why_now = ?, outcome_hope = ?, owner = ?, stage = ?, status = ?, option = ?, " +
                "repos_json = ?, metric = ?, revisit_at = ?, updated_at = ? WHERE id = ?",
            next.title, next.idea, next.whyNow, next.outcomeHope, next.owner, next.stage, next.status, next.option, json(next.repos), next.metric,
            next.revisitAt, next.updatedAt, next.id,
        )
        return next
    }

    // ---- documents
    private fun doc(rs: ResultSet) = ProductDoc(
        rs.getString("initiative_id"), rs.getString("kind"), rs.getInt("version"), rs.getString("path"), rs.getString("sha"),
        rs.getString("text") ?: "", any(rs.getString("data_json")), rs.getString("thread_id"), rs.getString("created_at"), rs.getString("approved_at"),
    )

    fun docs(id: String): List<ProductDoc> =
        jdbc.query("SELECT * FROM product_docs WHERE initiative_id = ? ORDER BY kind, version", { rs, _ -> doc(rs) }, id)

    fun doc(id: String, kind: String, version: Int): ProductDoc? =
        jdbc.query("SELECT * FROM product_docs WHERE initiative_id = ? AND kind = ? AND version = ?", { rs, _ -> doc(rs) }, id, kind, version).firstOrNull()

    fun latest(id: String, kind: String): ProductDoc? =
        jdbc.query("SELECT * FROM product_docs WHERE initiative_id = ? AND kind = ? ORDER BY version DESC LIMIT 1", { rs, _ -> doc(rs) }, id, kind).firstOrNull()

    fun saveDoc(d: ProductDoc) {
        jdbc.update(
            "INSERT INTO product_docs(initiative_id, kind, version, path, sha, text, data_json, thread_id, created_at, approved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
                "ON CONFLICT(initiative_id, kind, version) DO UPDATE SET path = excluded.path, sha = excluded.sha, text = excluded.text, " +
                "data_json = excluded.data_json, thread_id = excluded.thread_id",
            d.initiativeId, d.kind, d.version, d.path, d.sha, d.text, json(d.data), d.threadId, d.createdAt, d.approvedAt,
        )
    }

    fun approveLatest(id: String, kind: String) {
        jdbc.update(
            "UPDATE product_docs SET approved_at = ? WHERE initiative_id = ? AND kind = ? AND version = (SELECT MAX(version) FROM product_docs WHERE initiative_id = ? AND kind = ?)",
            Time.now(), id, kind, id, kind,
        )
    }

    // ---- runs
    private fun run(rs: ResultSet) = ProductRun(
        rs.getString("thread_id"), rs.getString("initiative_id"), rs.getString("stage"), rs.getString("workflow_id"), rs.getString("status"),
        rs.getString("reason"), rs.getString("started_at"), rs.getString("ended_at"),
    )

    fun runs(id: String): List<ProductRun> =
        jdbc.query("SELECT * FROM product_runs WHERE initiative_id = ? ORDER BY started_at DESC", { rs, _ -> run(rs) }, id)

    fun run(tid: String): ProductRun? = jdbc.query("SELECT * FROM product_runs WHERE thread_id = ?", { rs, _ -> run(rs) }, tid).firstOrNull()

    fun activeRun(id: String): ProductRun? =
        jdbc.query("SELECT * FROM product_runs WHERE initiative_id = ? AND status IN ('running','waiting') ORDER BY started_at DESC LIMIT 1",
            { rs, _ -> run(rs) }, id).firstOrNull()

    fun saveRun(r: ProductRun) {
        jdbc.update(
            "INSERT INTO product_runs(thread_id, initiative_id, stage, workflow_id, status, reason, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) " +
                "ON CONFLICT(thread_id) DO UPDATE SET status = excluded.status, ended_at = excluded.ended_at",
            r.threadId, r.initiativeId, r.stage, r.workflowId, r.status, r.reason, r.startedAt, r.endedAt,
        )
    }

    // ---- questions
    private fun question(rs: ResultSet) = ProductQuestion(
        rs.getString("id"), rs.getString("initiative_id"), rs.getString("stage"), rs.getString("role"), rs.getString("asked_to"),
        rs.getString("about"), rs.getString("text"), rs.getString("answer"), rs.getString("answered_by"), rs.getString("status"),
        rs.getString("created_at"), rs.getString("answered_at"),
    )

    fun questions(id: String): List<ProductQuestion> =
        jdbc.query("SELECT * FROM product_questions WHERE initiative_id = ? ORDER BY created_at", { rs, _ -> question(rs) }, id)

    fun question(qid: String): ProductQuestion? = jdbc.query("SELECT * FROM product_questions WHERE id = ?", { rs, _ -> question(rs) }, qid).firstOrNull()

    fun saveQuestion(q: ProductQuestion) {
        jdbc.update(
            "INSERT INTO product_questions(id, initiative_id, stage, role, asked_to, about, text, answer, answered_by, status, created_at, answered_at) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET answer = excluded.answer, answered_by = excluded.answered_by, " +
                "status = excluded.status, answered_at = excluded.answered_at",
            q.id, q.initiativeId, q.stage, q.role, q.askedTo, q.about, q.text, q.answer, q.answeredBy, q.status, q.createdAt, q.answeredAt,
        )
    }

    // ---- disagreements
    private fun disagreement(rs: ResultSet) = Disagreement(
        rs.getString("id"), rs.getString("initiative_id"), rs.getString("stage"), rs.getString("author"), rs.getString("reason"),
        rs.getString("proposal"), rs.getString("decider"), rs.getString("status"), rs.getString("outcome"), rs.getString("decided_by"),
        rs.getString("created_at"), rs.getString("decided_at"),
    )

    fun disagreements(id: String): List<Disagreement> =
        jdbc.query("SELECT * FROM product_disagreements WHERE initiative_id = ? ORDER BY created_at", { rs, _ -> disagreement(rs) }, id)

    fun disagreement(did: String): Disagreement? =
        jdbc.query("SELECT * FROM product_disagreements WHERE id = ?", { rs, _ -> disagreement(rs) }, did).firstOrNull()

    fun saveDisagreement(d: Disagreement) {
        jdbc.update(
            "INSERT INTO product_disagreements(id, initiative_id, stage, author, reason, proposal, decider, status, outcome, decided_by, created_at, decided_at) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET status = excluded.status, outcome = excluded.outcome, " +
                "decided_by = excluded.decided_by, decided_at = excluded.decided_at",
            d.id, d.initiativeId, d.stage, d.author, d.reason, d.proposal, d.decider, d.status, d.outcome, d.decidedBy, d.createdAt, d.decidedAt,
        )
    }

    // ---- history
    fun event(id: String, actor: String, kind: String, text: String, data: Any? = null) {
        jdbc.update("INSERT INTO product_events(initiative_id, at, actor, kind, text, data_json) VALUES (?, ?, ?, ?, ?, ?)",
            id, Time.now(), actor, kind, text.take(2000), json(data))
    }

    fun events(id: String): List<ProductEvent> = jdbc.query("SELECT * FROM product_events WHERE initiative_id = ? ORDER BY id DESC", { rs, _ ->
        ProductEvent(rs.getLong("id"), rs.getString("initiative_id"), rs.getString("at"), rs.getString("actor"), rs.getString("kind"),
            rs.getString("text"), any(rs.getString("data_json")))
    }, id)

    // ---- follow-ups
    private fun followUp(rs: ResultSet) = FollowUp(
        rs.getString("id"), rs.getString("initiative_id"), rs.getString("kind"), rs.getString("text"), rs.getString("owner"),
        rs.getString("due_at"), rs.getObject("repeat_days")?.let { (it as Number).toInt() }, rs.getString("ref"), rs.getString("done_at"),
        rs.getString("last_reminded_at"), rs.getString("created_at"),
    )

    fun followUps(id: String): List<FollowUp> =
        jdbc.query("SELECT * FROM product_follow_ups WHERE initiative_id = ? ORDER BY done_at IS NOT NULL, due_at", { rs, _ -> followUp(rs) }, id)

    fun openFollowUps(): List<FollowUp> = jdbc.query("SELECT * FROM product_follow_ups WHERE done_at IS NULL ORDER BY due_at") { rs, _ -> followUp(rs) }

    fun followUp(fid: String): FollowUp? = jdbc.query("SELECT * FROM product_follow_ups WHERE id = ?", { rs, _ -> followUp(rs) }, fid).firstOrNull()

    fun saveFollowUp(f: FollowUp) {
        jdbc.update(
            "INSERT INTO product_follow_ups(id, initiative_id, kind, text, owner, due_at, repeat_days, ref, done_at, last_reminded_at, created_at) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET text = excluded.text, owner = excluded.owner, " +
                "due_at = excluded.due_at, repeat_days = excluded.repeat_days, done_at = excluded.done_at, last_reminded_at = excluded.last_reminded_at",
            f.id, f.initiativeId, f.kind, f.text, f.owner, f.dueAt, f.repeatDays, f.ref, f.doneAt, f.lastRemindedAt, f.createdAt,
        )
    }

    fun closeFollowUps(id: String, ref: String) {
        jdbc.update("UPDATE product_follow_ups SET done_at = ? WHERE initiative_id = ? AND ref = ? AND done_at IS NULL", Time.now(), id, ref)
    }

    // ---- plan items
    private fun item(rs: ResultSet) = PlanItem(
        rs.getString("initiative_id"), rs.getInt("version"), rs.getString("id"), rs.getString("epic"), rs.getString("team"), rs.getString("repo"),
        rs.getString("title"), parse(rs.getString("body_json"), emptyMap()) { mapper.readValue<Map<String, Any?>>(it) },
        rs.getString("jira_key"), rs.getString("project_id"), rs.getString("task_id"),
    )

    fun items(id: String, version: Int): List<PlanItem> =
        jdbc.query("SELECT * FROM product_plan_items WHERE initiative_id = ? AND version = ? ORDER BY rowid", { rs, _ -> item(rs) }, id, version)

    fun saveItem(p: PlanItem) {
        jdbc.update(
            "INSERT INTO product_plan_items(initiative_id, version, id, epic, team, repo, title, body_json, jira_key, project_id, task_id) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(initiative_id, version, id) DO UPDATE SET jira_key = excluded.jira_key, " +
                "project_id = excluded.project_id, task_id = excluded.task_id",
            p.initiativeId, p.version, p.id, p.epic, p.team, p.repo, p.title, json(p.body), p.jiraKey, p.projectId, p.taskId,
        )
    }

    // ---- teams
    fun paths(teamId: String? = null): List<TeamPath> = (if (teamId == null)
        jdbc.query("SELECT * FROM product_team_paths ORDER BY team_id, project_id, glob") { rs, _ -> path(rs) }
    else jdbc.query("SELECT * FROM product_team_paths WHERE team_id = ? ORDER BY project_id, glob", { rs, _ -> path(rs) }, teamId))

    private fun path(rs: ResultSet) = TeamPath(rs.getString("team_id"), rs.getString("project_id"), rs.getString("glob"), rs.getString("source"))

    private fun team(rs: ResultSet, owns: List<TeamPath>) = Team(
        rs.getString("id"), rs.getString("name"), rs.getString("lead"), rs.getString("jira_project"),
        rs.getObject("capacity_days")?.let { (it as Number).toDouble() },
        parse(rs.getString("members_json"), emptyList()) { mapper.readValue<List<String>>(it) }, rs.getString("source"),
        owns.filter { it.teamId == rs.getString("id") }, rs.getString("created_at"), rs.getString("updated_at"),
    )

    fun teams(): List<Team> {
        val owns = paths()
        return jdbc.query("SELECT * FROM product_teams ORDER BY name") { rs, _ -> team(rs, owns) }
    }

    fun team(id: String): Team? = jdbc.query("SELECT * FROM product_teams WHERE id = ?", { rs, _ -> team(rs, paths(id)) }, id).firstOrNull()

    fun saveTeam(t: Team) {
        jdbc.update(
            "INSERT INTO product_teams(id, name, lead, jira_project, capacity_days, members_json, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) " +
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, lead = excluded.lead, jira_project = excluded.jira_project, " +
                "capacity_days = excluded.capacity_days, members_json = excluded.members_json, updated_at = excluded.updated_at",
            t.id, t.name, t.lead, t.jiraProject, t.capacityDays, json(t.members), t.source, t.createdAt, t.updatedAt,
        )
    }

    fun setPaths(teamId: String, owns: List<TeamPath>, source: String? = null) {
        if (source == null) jdbc.update("DELETE FROM product_team_paths WHERE team_id = ?", teamId)
        else jdbc.update("DELETE FROM product_team_paths WHERE team_id = ? AND source = ?", teamId, source)
        owns.forEach { jdbc.update("INSERT OR IGNORE INTO product_team_paths(team_id, project_id, glob, source) VALUES (?, ?, ?, ?)", teamId, it.projectId, it.glob, it.source) }
    }

    fun deleteTeam(id: String) {
        jdbc.update("DELETE FROM product_team_paths WHERE team_id = ?", id)
        jdbc.update("DELETE FROM product_team_suggestions WHERE team_id = ?", id)
        jdbc.update("DELETE FROM product_teams WHERE id = ?", id)
    }

    fun suggestions(teamId: String): List<TeamSuggestion> = jdbc.query("SELECT * FROM product_team_suggestions WHERE team_id = ? ORDER BY created_at DESC", { rs, _ ->
        TeamSuggestion(rs.getString("id"), rs.getString("team_id"), rs.getString("page"), rs.getString("text"), rs.getString("source"),
            rs.getString("status"), rs.getString("created_at"), rs.getString("decided_at"))
    }, teamId)

    fun saveSuggestion(s: TeamSuggestion) {
        jdbc.update(
            "INSERT INTO product_team_suggestions(id, team_id, page, text, source, status, created_at, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) " +
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, decided_at = excluded.decided_at",
            s.id, s.teamId, s.page, s.text, s.source, s.status, s.createdAt, s.decidedAt,
        )
    }
}
