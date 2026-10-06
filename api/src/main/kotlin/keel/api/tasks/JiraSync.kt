package keel.api.tasks

import keel.api.common.Time
import keel.api.events.EventHub
import keel.api.jira.JiraException
import keel.api.jira.JiraIssue
import keel.api.jira.JiraService
import keel.api.jira.JiraSettings
import keel.api.projects.ProjectService
import org.slf4j.LoggerFactory
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Service
import java.time.Duration
import java.time.Instant
import java.util.concurrent.ConcurrentHashMap

data class SyncResult(
    val ok: Boolean,
    /** false: the project has no Jira connection (only the PR reviews were read). */
    val jira: Boolean,
    val total: Int = 0,
    val created: Int = 0,
    val updated: Int = 0,
    val moved: Int = 0,
    val at: String? = null,
    val error: String? = null,
    val hint: String? = null,
    val reviewsChecked: Int = 0,
    val reviewsMoved: Int = 0,
)

/**
 * Jira → keel: the board's (or the JQL's) tickets become tasks (source jira, keyed by the ticket key); a status changed
 * in Jira is recorded as an event (actor jira) and moves the task when the mapping says where. Tickets keel follows that
 * left the query (moved to Done, reassigned) are read once more by key. Runs on "Sync now" and on a poll per connection.
 */
@Service
class JiraSync(
    private val jira: JiraService,
    private val store: TaskStore,
    private val tasks: TaskService,
    private val projects: ProjectService,
    private val hub: EventHub,
    private val props: TaskProperties,
) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val tried = ConcurrentHashMap<String, Instant>()
    @Volatile private var ticks = 0L

    /** "Sync now": Jira (when connected), then the PR reviews of the project's tasks in review. */
    fun syncNow(pid: String): SyncResult {
        projects.require(pid)
        val r = if (jira.settings(pid) != null) sync(pid) else SyncResult(ok = true, jira = false, at = Time.now())
        val (checked, moved) = tasks.checkReviews(pid)
        return r.copy(reviewsChecked = checked, reviewsMoved = moved)
    }

    fun sync(pid: String): SyncResult {
        tried[pid] = Instant.now()
        val s = jira.settings(pid) ?: return SyncResult(false, false, error = "This project has no Jira connection")
        val client = jira.client(pid) ?: return SyncResult(false, true, error = "The Jira token is missing", hint = "Save it again in Connections › Jira.")
        return try {
            val issues = if (s.boardId != null) client.boardIssues(s.boardId, jira.jql(s)) else client.search(jira.jql(s))
            val seen = issues.map { it.key }.toSet()
            val missing = store.openKeys(pid).filter { it !in seen }.take(100)
            // A ticket that left the query (done, reassigned) is still followed; one that no longer exists makes Jira refuse
            // the whole "key in (…)" query, so that part is best effort.
            val extra = if (missing.isEmpty()) emptyList() else runCatching { client.search("key in (${missing.joinToString(",")})", 100) }.getOrDefault(emptyList())
            if (jira.me(pid) == null) runCatching { jira.setMe(pid, client.myself()) }
            var created = 0
            var updated = 0
            var moved = 0
            for (i in issues + extra.filter { it.key !in seen }) {
                when (upsert(pid, s, i)) {
                    Change.CREATED -> created++
                    Change.UPDATED -> updated++
                    Change.MOVED -> moved++
                    Change.NONE -> {}
                }
            }
            jira.markSynced(pid, null)
            hub.publish(pid, "project.changed", mapOf("id" to pid))
            SyncResult(true, true, issues.size, created, updated, moved, Time.now())
        } catch (e: JiraException) {
            jira.markSynced(pid, e.message)
            hub.publish(pid, "project.changed", mapOf("id" to pid))
            SyncResult(false, true, error = e.message, hint = e.hint, at = Time.now())
        }
    }

    private enum class Change { CREATED, UPDATED, MOVED, NONE }

    private fun upsert(pid: String, s: JiraSettings, i: JiraIssue): Change {
        if (i.key.isBlank()) return Change.NONE
        val url = "${s.baseUrl}/browse/${i.key}"
        val title = i.summary.ifBlank { i.key }.take(300)
        val type = TaskTypes.fromJira(i.type)
        val existing = store.byKey(pid, i.key)
        if (existing == null) {
            val status = TaskMachine.keelStatus(i.status, i.category, s.statusMap, null) ?: TaskStatus.TODO
            val now = Time.now()
            val t = store.insert(Task(TaskStore.newId(), pid, title, i.description, type, status, "jira", i.key, url, i.status, i.assignee, i.priority,
                null, null, null, emptyList(), null, now, now))
            store.event(t.id, "created", null, status, "Imported from Jira (status ${i.status}).", "jira")
            return Change.CREATED
        }
        var t = existing
        val what = mutableListOf<String>()
        if (title != t.title) { t = t.copy(title = title); what += "title" }
        if (i.description != t.description) { t = t.copy(description = i.description); what += "description" }
        if (type != t.type) { t = t.copy(type = type); what += "type" }
        if (i.assignee != t.assignee) { t = t.copy(assignee = i.assignee); what += "assignee" }
        if (i.priority != t.priority) { t = t.copy(priority = i.priority); what += "priority" }
        if (t.externalUrl == null) t = t.copy(externalUrl = url)
        if (t.source != "jira") t = t.copy(source = "jira")
        if (t != existing) store.update(t)
        if (what.isNotEmpty()) store.event(t.id, "jira_update", null, null, "Updated from Jira: ${what.joinToString()}.", "jira")
        if (!i.status.equals(t.externalStatus, ignoreCase = true)) {
            val to = TaskMachine.keelStatus(i.status, i.category, s.statusMap, t.status)
            tasks.fire(t.id, Trigger.JiraMoved(t.externalStatus, i.status, to?.takeIf { it != t.status }))
            return Change.MOVED
        }
        return if (what.isNotEmpty()) Change.UPDATED else Change.NONE
    }

    /** Every minute: each connection whose poll interval passed syncs; every second tick, the PR reviews. */
    @Scheduled(fixedDelayString = "\${keel.tasks.tick-ms:60000}", initialDelayString = "\${keel.tasks.tick-ms:60000}")
    fun tick() {
        if (!props.scheduler) return
        val listed = projects.rows().map { it.id }.toSet()
        for (pid in jira.connectedProjects().filter { it in listed }) {
            val s = jira.settings(pid) ?: continue
            if (s.pollMinutes <= 0) continue
            val last = tried[pid] ?: jira.lastSync(pid)?.first?.let { runCatching { Instant.parse(it) }.getOrNull() }
            if (last != null && Duration.between(last, Instant.now()) < Duration.ofMinutes(s.pollMinutes.toLong())) continue
            runCatching { sync(pid) }.onFailure { log.warn("jira sync of {}: {}", pid, it.message) }
        }
        if (ticks++ % 2 == 0L) runCatching { tasks.checkReviews(null) }.onFailure { log.warn("PR review check: {}", it.message) }
    }
}
