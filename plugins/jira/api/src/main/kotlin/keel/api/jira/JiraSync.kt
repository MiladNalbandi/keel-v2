package keel.api.jira

import keel.api.common.Time
import keel.api.events.EventHub
import keel.api.projects.ProjectService
import keel.api.tasks.SyncInfo
import keel.api.tasks.SyncResult
import keel.api.tasks.Task
import keel.api.tasks.TaskAutomations
import keel.api.tasks.TaskMachine
import keel.api.tasks.TaskService
import keel.api.tasks.TaskStatus
import keel.api.tasks.TaskStore
import keel.api.tasks.TaskTypes
import keel.api.tasks.TicketTracker
import keel.api.tasks.TrackerConnection
import keel.api.tasks.Trigger
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import java.time.Duration
import java.time.Instant
import java.util.concurrent.ConcurrentHashMap

/**
 * Jira → keel: the board's (or the JQL's) tickets become tasks (source jira, keyed by the ticket key); a status changed
 * in Jira is recorded as an event (actor jira) and moves the task when the mapping says where. Tickets keel follows that
 * left the query (moved to Done, reassigned) are read once more by key. Runs on "Sync now" and on a poll per connection.
 *
 * It is the Tasks plugin's [TicketTracker]: the Tasks plugin moves and comments the real tickets through it, and its
 * page shows the sync line from it.
 */
@Service
class JiraSync(
    private val jira: JiraService,
    private val store: TaskStore,
    private val tasks: TaskService,
    private val projects: ProjectService,
    private val hub: EventHub,
    private val automations: TaskAutomations,
) : TicketTracker {
    private val log = LoggerFactory.getLogger(javaClass)
    private val tried = ConcurrentHashMap<String, Instant>()

    // ---- the Tasks plugin's tracker ----

    override fun connection(pid: String): TrackerConnection? = jira.settings(pid)?.let {
        TrackerConnection(it.baseUrl, it.statusMap, it.reviewerField, it.jiraReviewers, it.githubReviewers)
    }

    override fun client(pid: String): JiraClient? = jira.client(pid)

    override fun syncInfo(pid: String): SyncInfo {
        val s = jira.settings(pid)
        val last = jira.lastSync(pid)
        return SyncInfo(s != null, s?.kind, last?.first, last?.second, jira.me(pid)?.name, s?.pollMinutes, "jira", s?.baseUrl)
    }

    /** "Sync now" of the Tasks page: Jira, when the project is connected (the Tasks plugin reads the PR reviews after). */
    override fun sync(pid: String): SyncResult? = if (jira.settings(pid) != null) syncJira(pid) else null

    fun syncJira(pid: String): SyncResult {
        tried[pid] = Instant.now()
        val s = jira.settings(pid) ?: return SyncResult(false, false, error = "This project has no Jira connection")
        val client = jira.client(pid) ?: return SyncResult(false, true, error = "The Jira token is missing", hint = "Save it again in Connections › Jira.")
        return try {
            val issues = if (s.boardId != null) client.boardIssues(s.boardId, jira.jql(s)) else client.search(jira.jql(s))
            val seen = issues.map { it.key }.toSet()
            val missing = store.openKeys(pid).filter { it !in seen }.take(100)
            // A ticket that left the query (done, reassigned) is still followed; one that no longer exists makes Jira refuse
            // the whole "key in (…)" query, so that part is best effort.
            val extra = (if (missing.isEmpty()) emptyList() else runCatching { client.search("key in (${missing.joinToString(",")})", 100) }.getOrDefault(emptyList())) +
                keelBotIssues(pid, client, seen)
            if (jira.me(pid) == null) runCatching { jira.setMe(pid, client.myself()) }
            var created = 0
            var updated = 0
            var moved = 0
            for (i in (issues + extra.filter { it.key !in seen }).distinctBy { it.key }) {
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

    /**
     * 0.18.52 tickets assigned to KeelBot (or with its label) that the connection's own query leaves out (it reads
     * "assignee = currentUser()" by default). Best effort: a query Jira refuses only means no extra tickets.
     */
    private fun keelBotIssues(pid: String, client: JiraClient, seen: Set<String>): List<JiraIssue> {
        val kb = automations.settings(pid).keelbot
        val jql = listOfNotNull(kb.account?.let { "assignee = \"$it\"" }, kb.label?.let { "labels = \"$it\"" })
        if (jql.isEmpty()) return emptyList()
        return runCatching { client.search("(${jql.joinToString(" OR ")}) AND statusCategory != Done", 100) }.getOrDefault(emptyList())
            .filter { it.key !in seen }
    }

    private fun upsert(pid: String, s: JiraSettings, i: JiraIssue): Change {
        val change = upsertTask(pid, s, i)
        // 0.18.52 KeelBot's tickets: keel watches their PR's review comments
        store.byKey(pid, i.key)?.let { t ->
            runCatching { automations.onTicketSeen(t.id, automations.owns(pid, listOf(i.assigneeId, i.assignee), i.labels)) }
                .onFailure { log.warn("keelbot owner of {}: {}", i.key, it.message) }
        }
        return change
    }

    private fun upsertTask(pid: String, s: JiraSettings, i: JiraIssue): Change {
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
            val before = store.require(t.id)
            val after = tasks.fire(t.id, Trigger.JiraMoved(t.externalStatus, i.status, to?.takeIf { it != t.status }))
            // 0.18.52 a move made in Jira (never keel's own: it set external_status when it moved the ticket)
            runCatching { automations.onJiraMoved(before, after) }.onFailure { log.warn("automation for {}: {}", i.key, it.message) }
            return Change.MOVED
        }
        return if (what.isNotEmpty()) Change.UPDATED else Change.NONE
    }

    /** The Tasks plugin's poll (every minute): each connection whose poll interval passed syncs. */
    override fun poll() {
        val listed = projects.rows().map { it.id }.toSet()
        for (pid in jira.connectedProjects().filter { it in listed }) {
            val s = jira.settings(pid) ?: continue
            if (s.pollMinutes <= 0) continue
            val last = tried[pid] ?: jira.lastSync(pid)?.first?.let { runCatching { Instant.parse(it) }.getOrNull() }
            if (last != null && Duration.between(last, Instant.now()) < Duration.ofMinutes(s.pollMinutes.toLong())) continue
            runCatching { syncJira(pid) }.onFailure { log.warn("jira sync of {}: {}", pid, it.message) }
        }
    }
}
