package keel.api.githubissues

import keel.api.common.Time
import keel.api.events.EventHub
import keel.api.tasks.SyncInfo
import keel.api.tasks.SyncResult
import keel.api.tasks.Task
import keel.api.tasks.TaskAutomations
import keel.api.tasks.TaskMachine
import keel.api.tasks.TaskService
import keel.api.tasks.TaskStatus
import keel.api.tasks.TaskStore
import keel.api.tasks.TicketTracker
import keel.api.tasks.TrackerConnection
import keel.api.tasks.Trigger
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import java.time.Duration
import java.time.Instant
import java.util.concurrent.ConcurrentHashMap

/**
 * GitHub → keel: the repo's open issues the connection follows (assigned to you, with a label, or all) become tasks
 * (source github, keyed "#12"); a state or status label changed on GitHub is recorded (actor github) and moves the task
 * when the map says where. Issues keel follows that left the list (closed, unassigned) are read once more by number.
 * Runs on "Sync now" and on a poll per connection.
 *
 * It is the Tasks plugin's [TicketTracker] for the projects connected here: the Tasks plugin closes, reopens, labels
 * and comments the real issue through it, and its pull request says "Closes #12".
 */
@Service
class GitHubIssuesSync(
    private val gh: GitHubIssuesService,
    private val store: TaskStore,
    private val tasks: TaskService,
    private val hub: EventHub,
    private val automations: TaskAutomations,
) : TicketTracker {
    private val log = LoggerFactory.getLogger(javaClass)
    private val tried = ConcurrentHashMap<String, Instant>()

    // ---- the Tasks plugin's tracker ----

    override val source = GitHubIssuesService.SOURCE
    override val title = "GitHub"

    override fun key(raw: String): String? = raw.trim().takeIf { GitHubIssuesClient.KEY.matches(it) }

    override fun ticketUrl(pid: String, key: String): String? {
        val n = GitHubIssuesClient.KEY.matchEntire(key.trim())?.groupValues?.get(1) ?: return null
        return gh.repo(pid)?.let { "${it.web}/issues/$n" }
    }

    /** "Closes #12" (or "Closes owner/repo#12" when the issues live in another repo than the code). */
    override fun prLine(pid: String, key: String): String? {
        val s = gh.settings(pid) ?: return null
        if (!s.closesOnMerge || !GitHubIssuesClient.KEY.matches(key.trim())) return null
        val repo = gh.repo(pid, s) ?: return null
        val same = gh.origin(pid)?.let { it.path.equals(repo.path, true) } ?: false
        return "Closes ${if (same) "" else repo.path}${key.trim()}"
    }

    override fun connection(pid: String): TrackerConnection? = gh.settings(pid)?.let {
        TrackerConnection(gh.repo(pid, it)?.web.orEmpty(), gh.fullMap(it), null, emptyList(), it.githubReviewers)
    }

    override fun client(pid: String): GitHubIssuesClient? = gh.client(pid)

    override fun syncInfo(pid: String): SyncInfo {
        val s = gh.settings(pid)
        val last = gh.lastSync(pid)
        return SyncInfo(s != null, if (s != null) "github" else null, last?.first, last?.second, gh.me(pid), s?.pollMinutes, source,
            s?.let { gh.repo(pid, it)?.path })
    }

    override fun sync(pid: String): SyncResult? = if (gh.settings(pid) != null) syncIssues(pid) else null

    fun syncIssues(pid: String): SyncResult {
        tried[pid] = Instant.now()
        val s = gh.settings(pid) ?: return SyncResult(false, false, error = "This project has no GitHub Issues connection")
        val client = gh.client(pid, s) ?: return fail(pid, if (gh.repo(pid, s) == null) "No GitHub repository" else "keel has no GitHub token",
            if (gh.repo(pid, s) == null) "Name the repository in Settings › Connections › GitHub Issues." else "Sign in to GitHub in Settings › Connections › GitHub.")
        return try {
            val me = gh.me(pid) ?: client.me().login.also { gh.setMe(pid, it) }
            val issues = when (s.follow) {
                "label" -> client.openIssues(label = s.label)
                "all" -> client.openIssues()
                else -> client.openIssues(assignee = me)
            }
            val seen = issues.map { "#${it.number}" }.toSet()
            // an issue keel follows that left the list (closed, unassigned, label taken off) is still followed
            val missing = store.openKeys(pid).filter { GitHubIssuesClient.KEY.matches(it) && it !in seen }.take(MAX_EXTRA)
            val extra = missing.mapNotNull { k ->
                try {
                    client.issue(GitHubIssuesClient.number(k))
                } catch (e: GitHubIssuesException) {
                    if (e.status == 404 || e.status == 410) null else throw e
                }
            } + keelBotIssues(pid, client, seen)
            var created = 0
            var updated = 0
            var moved = 0
            for (i in (issues + extra.filter { "#${it.number}" !in seen }).distinctBy { it.number }) {
                when (upsert(pid, s, client, i)) {
                    Change.CREATED -> created++
                    Change.UPDATED -> updated++
                    Change.MOVED -> moved++
                    Change.NONE -> {}
                }
            }
            gh.markSynced(pid, null)
            hub.publish(pid, "project.changed", mapOf("id" to pid))
            SyncResult(true, true, issues.size, created, updated, moved, Time.now())
        } catch (e: GitHubIssuesException) {
            fail(pid, e.message, e.hint)
        }
    }

    private fun fail(pid: String, error: String, hint: String?): SyncResult {
        gh.markSynced(pid, error)
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        return SyncResult(false, true, error = error, hint = hint, at = Time.now())
    }

    private enum class Change { CREATED, UPDATED, MOVED, NONE }

    /** Open issues assigned to KeelBot's GitHub login or with its label (Tasks › Automations) that the list left out. */
    private fun keelBotIssues(pid: String, client: GitHubIssuesClient, seen: Set<String>): List<GhIssue> {
        val kb = automations.settings(pid).keelbot
        val out = mutableListOf<GhIssue>()
        kb.account?.takeIf { GitHubIssuesService.LOGIN.matches(it) }?.let { a -> runCatching { out += client.openIssues(assignee = a, max = 100) } }
        kb.label?.let { l -> runCatching { out += client.openIssues(label = l, max = 100) } }
        return out.filter { "#${it.number}" !in seen }
    }

    private fun upsert(pid: String, s: GhIssuesSettings, client: GitHubIssuesClient, i: GhIssue): Change {
        val change = upsertTask(pid, s, client, i)
        // KeelBot's issues: keel watches their PR's review comments, as for Jira tickets
        store.byKey(pid, "#${i.number}")?.let { t ->
            runCatching { automations.onTicketSeen(t.id, automations.owns(pid, i.assignees, i.labels)) }
                .onFailure { log.warn("keelbot owner of #{}: {}", i.number, it.message) }
        }
        return change
    }

    private fun upsertTask(pid: String, s: GhIssuesSettings, client: GitHubIssuesClient, i: GhIssue): Change {
        val key = "#${i.number}"
        val title = i.title.ifBlank { key }.take(300)
        val description = i.body.take(20_000)
        val type = typeOf(i.labels)
        val assignee = i.assignees.firstOrNull()
        val status = client.status(i)
        val category = if (i.state == "closed") "done" else "new"
        val map = gh.fullMap(s)
        val existing = store.byKey(pid, key)
        if (existing == null) {
            val keel = TaskMachine.keelStatus(status, category, map, null) ?: TaskStatus.TODO
            val now = Time.now()
            val t = store.insert(Task(TaskStore.newId(), pid, title, description, type, keel, source, key, i.url, status, assignee, null,
                null, null, null, emptyList(), null, now, now))
            store.event(t.id, "created", null, keel, "Imported from GitHub ($key, $status).", source)
            return Change.CREATED
        }
        var t = existing
        val what = mutableListOf<String>()
        if (title != t.title) { t = t.copy(title = title); what += "title" }
        if (description != t.description) { t = t.copy(description = description); what += "description" }
        if (type != t.type) { t = t.copy(type = type); what += "type" }
        if (assignee != t.assignee) { t = t.copy(assignee = assignee); what += "assignee" }
        if (t.externalUrl == null) t = t.copy(externalUrl = i.url)
        if (t.source != source) t = t.copy(source = source)
        if (t != existing) store.update(t)
        if (what.isNotEmpty()) store.event(t.id, "jira_update", null, null, "Updated from GitHub: ${what.joinToString()}.", source)
        if (!status.equals(t.externalStatus, ignoreCase = true)) {
            val to = TaskMachine.keelStatus(status, category, map, t.status)
            val before = store.require(t.id)
            val after = tasks.fire(t.id, Trigger.JiraMoved(t.externalStatus, status, to?.takeIf { it != t.status }, source))
            // a move made on GitHub (never keel's own: it set external_status when it moved the issue)
            runCatching { automations.onJiraMoved(before, after) }.onFailure { log.warn("automation for #{}: {}", i.number, it.message) }
            return Change.MOVED
        }
        return if (what.isNotEmpty()) Change.UPDATED else Change.NONE
    }

    /** The Tasks plugin's poll (every minute): each connection whose poll interval passed syncs. */
    override fun poll() {
        for (pid in gh.connectedProjects()) {
            val s = gh.settings(pid) ?: continue
            if (s.pollMinutes <= 0) continue
            val last = tried[pid] ?: gh.lastSync(pid)?.first?.let { runCatching { Instant.parse(it) }.getOrNull() }
            if (last != null && Duration.between(last, Instant.now()) < Duration.ofMinutes(s.pollMinutes.toLong())) continue
            runCatching { syncIssues(pid) }.onFailure { log.warn("github issues sync of {}: {}", pid, it.message) }
        }
    }

    companion object {
        /** issues keel follows that left the list, read one by one per sync */
        const val MAX_EXTRA = 50

        /** bug for a bug label, story for a feature or enhancement, else task (as Jira's issue types map). */
        fun typeOf(labels: List<String>): String {
            val l = labels.map { it.lowercase() }
            return when {
                l.any { Regex("\\bbug\\b|defect|incident|regression").containsMatchIn(it) } -> "bug"
                l.any { Regex("enhancement|feature|story|epic").containsMatchIn(it) } -> "story"
                else -> "task"
            }
        }
    }
}
