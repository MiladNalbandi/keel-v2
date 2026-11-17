package keel.api.tasks

import keel.api.common.Db
import keel.api.common.Json
import keel.api.common.NotFound
import keel.api.common.Time
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import java.sql.ResultSet
import java.util.UUID

/** The tasks tables (V7): tasks, task_events, task_inbox. Plain SQL, no logic. */
@Component
class TaskStore(private val jdbc: JdbcTemplate) {

    private val cols = "id, project_id, title, description, type, status, source, external_key, external_url, external_status, assignee, " +
        "priority, thread_id, workflow_id, pr_url, reviewers, blocked_reason, created_at, updated_at, mission_id, mission_org, mission_status, parent_task_id"

    private fun task(rs: ResultSet) = Task(
        rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4) ?: "", rs.getString(5), rs.getString(6), rs.getString(7),
        rs.getString(8), rs.getString(9), rs.getString(10), rs.getString(11), rs.getString(12), rs.getString(13), rs.getString(14),
        rs.getString(15), reviewers(rs.getString(16)), rs.getString(17), rs.getString(18), rs.getString(19),
        rs.getString(20), rs.getString(21), rs.getString(22), rs.getString(23),
    )

    private fun reviewers(json: String?): List<Reviewer> =
        if (json.isNullOrBlank()) emptyList() else runCatching { Json.read<List<Reviewer>>(json) }.getOrDefault(emptyList())

    fun find(id: String): Task? = jdbc.query("SELECT $cols FROM tasks WHERE id = ?", { rs, _ -> task(rs) }, id).firstOrNull()

    fun require(id: String): Task = find(id) ?: throw NotFound("No task $id", "GET /api/projects/{pid}/tasks lists them.")

    fun list(pid: String): List<Task> =
        jdbc.query("SELECT $cols FROM tasks WHERE project_id = ? ORDER BY updated_at DESC", { rs, _ -> task(rs) }, pid)

    fun byKey(pid: String, key: String): Task? =
        jdbc.query("SELECT $cols FROM tasks WHERE project_id = ? AND external_key = ?", { rs, _ -> task(rs) }, pid, key).firstOrNull()

    /** 0.34 the task an Org mission works on (Hand to Org) or is a part of (a part task). */
    fun byMission(mid: String): Task? =
        jdbc.query("SELECT $cols FROM tasks WHERE mission_id = ? ORDER BY updated_at DESC LIMIT 1", { rs, _ -> task(rs) }, mid).firstOrNull()

    /** 0.34 a task's part tasks, oldest first. */
    fun parts(id: String): List<Task> =
        jdbc.query("SELECT $cols FROM tasks WHERE parent_task_id = ? ORDER BY created_at, id", { rs, _ -> task(rs) }, id)

    fun byThread(tid: String): Task? =
        jdbc.query("SELECT $cols FROM tasks WHERE thread_id = ? ORDER BY updated_at DESC LIMIT 1", { rs, _ -> task(rs) }, tid).firstOrNull()

    /** Tasks in review with a PR link: the review poll reads their PR. */
    fun inReview(): List<Task> =
        jdbc.query("SELECT $cols FROM tasks WHERE status = 'in_review' AND pr_url IS NOT NULL", { rs, _ -> task(rs) })

    /** Keys of tasks keel still follows (not done or cancelled). */
    fun openKeys(pid: String): List<String> = jdbc.queryForList(
        "SELECT external_key FROM tasks WHERE project_id = ? AND external_key IS NOT NULL AND status NOT IN ('done', 'cancelled')",
        String::class.java, pid,
    )

    fun insert(t: Task): Task {
        jdbc.update(
            "INSERT INTO tasks($cols) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            t.id, t.projectId, t.title, t.description, t.type, t.status, t.source, t.externalKey, t.externalUrl, t.externalStatus,
            t.assignee, t.priority, t.threadId, t.workflowId, t.prUrl, Json.write(t.reviewers), t.blockedReason, t.createdAt, t.updatedAt,
            t.missionId, t.missionOrg, t.missionStatus, t.parentTaskId,
        )
        return t
    }

    fun update(t: Task): Task {
        val now = Time.now()
        jdbc.update(
            """UPDATE tasks SET title = ?, description = ?, type = ?, status = ?, source = ?, external_key = ?, external_url = ?,
               external_status = ?, assignee = ?, priority = ?, thread_id = ?, workflow_id = ?, pr_url = ?, reviewers = ?,
               blocked_reason = ?, updated_at = ?, mission_id = ?, mission_org = ?, mission_status = ?, parent_task_id = ? WHERE id = ?""",
            t.title, t.description, t.type, t.status, t.source, t.externalKey, t.externalUrl, t.externalStatus, t.assignee, t.priority,
            t.threadId, t.workflowId, t.prUrl, Json.write(t.reviewers), t.blockedReason, now, t.missionId, t.missionOrg, t.missionStatus,
            t.parentTaskId, t.id,
        )
        return t.copy(updatedAt = now)
    }

    fun delete(id: String) {
        jdbc.update("DELETE FROM task_events WHERE task_id = ?", id)
        jdbc.update("DELETE FROM task_inbox WHERE task_id = ?", id)
        jdbc.update("DELETE FROM task_watch WHERE task_id = ?", id)
        jdbc.update("DELETE FROM task_runs WHERE task_id = ?", id)
        jdbc.update("DELETE FROM tasks WHERE id = ?", id)
    }

    fun event(taskId: String, kind: String, from: String?, to: String?, note: String?, actor: String): TaskEvent {
        val at = Time.now()
        val id = Db.insertId(jdbc, "INSERT INTO task_events(task_id, at, kind, from_status, to_status, note, actor) VALUES (?, ?, ?, ?, ?, ?, ?)",
            taskId, at, kind, from, to, note?.take(2000), actor)
        return TaskEvent(id, taskId, at, kind, from, to, note?.take(2000), actor)
    }

    fun events(taskId: String): List<TaskEvent> = jdbc.query(
        "SELECT id, task_id, at, kind, from_status, to_status, note, actor FROM task_events WHERE task_id = ? ORDER BY id",
        { rs, _ -> TaskEvent(rs.getLong(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5), rs.getString(6), rs.getString(7), rs.getString(8)) },
        taskId,
    )

    // ---- inbox items ----

    private val itemCols = "id, task_id, project_id, kind, stage, title, detail, created_at, done_at, data"
    private fun item(rs: ResultSet) = TaskItem(rs.getLong(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5),
        rs.getString(6), rs.getString(7) ?: "", rs.getString(8), rs.getString(9), data(rs.getString(10)))

    private fun data(json: String?): Map<String, Any?>? =
        if (json.isNullOrBlank()) null else runCatching { Json.read<Map<String, Any?>>(json) }.getOrNull()

    fun openItem(t: Task, kind: String, stage: String?, title: String, detail: String, data: Map<String, Any?>? = null): TaskItem {
        val at = Time.now()
        val id = Db.insertId(jdbc, "INSERT INTO task_inbox(task_id, project_id, kind, stage, title, detail, created_at, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            t.id, t.projectId, kind, stage, title.take(300), detail.take(2000), at, data?.let { Json.write(it) })
        return TaskItem(id, t.id, t.projectId, kind, stage, title.take(300), detail.take(2000), at, null, data)
    }

    fun item(id: Long): TaskItem? = jdbc.query("SELECT $itemCols FROM task_inbox WHERE id = ?", { rs, _ -> item(rs) }, id).firstOrNull()

    fun openItems(taskId: String): List<TaskItem> =
        jdbc.query("SELECT $itemCols FROM task_inbox WHERE task_id = ? AND done_at IS NULL ORDER BY id", { rs, _ -> item(rs) }, taskId)

    fun openItemsAll(): List<TaskItem> =
        jdbc.query("SELECT $itemCols FROM task_inbox WHERE done_at IS NULL ORDER BY created_at, id", { rs, _ -> item(rs) })

    fun openCount(pid: String): Int =
        jdbc.queryForObject("SELECT COUNT(*) FROM task_inbox WHERE project_id = ? AND done_at IS NULL", Int::class.java, pid) ?: 0

    /** Closes the open items of a task ([kind] null = every kind); returns how many. */
    fun closeItems(taskId: String, kind: String? = null, stage: String? = null): Int {
        val sql = StringBuilder("UPDATE task_inbox SET done_at = ? WHERE task_id = ? AND done_at IS NULL")
        val args = mutableListOf<Any>(Time.now(), taskId)
        if (kind != null) { sql.append(" AND kind = ?"); args += kind }
        if (stage != null) { sql.append(" AND stage = ?"); args += stage }
        return jdbc.update(sql.toString(), *args.toTypedArray())
    }

    /**
     * B11 why the flow [threadId] opened no pull request: the newest note of its open_pr step that says so ("not pushed,
     * and keel never pushes", "No GitHub token", "Run mode auto"), without the "Copy the PR body" tail; null when none.
     */
    fun noPrReason(threadId: String): String? =
        jdbc.query("SELECT data_json FROM events WHERE thread_id = ? AND type = 'step.finished' ORDER BY id DESC LIMIT 60",
            { rs, _ -> rs.getString(1) }, threadId)
            .asSequence()
            .mapNotNull { runCatching { Json.readMap(it)["note"]?.toString() }.getOrNull() }
            .firstOrNull { NO_PR.containsMatchIn(it) }
            ?.substringBefore(" Copy the PR body")?.trim()?.take(400)

    fun closeItem(id: Long): Boolean = jdbc.update("UPDATE task_inbox SET done_at = ? WHERE id = ? AND done_at IS NULL", Time.now(), id) > 0

    /** The thread row the api keeps (status, phase), for the task's flow line. */
    fun flow(tid: String): TaskFlow? = jdbc.query(
        "SELECT id, workflow_id, status, phase, current, title FROM threads WHERE id = ?",
        { rs, _ -> TaskFlow(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5), rs.getString(6)) }, tid,
    ).firstOrNull()

    companion object {
        private val NO_PR = Regex("no PR opened|opens no PR|not pushed, and keel never pushes|are not pushed")

        fun newId(): String = "tk_" + UUID.randomUUID().toString().replace("-", "").take(12)
    }
}
