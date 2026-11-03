package keel.api.githubissues

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.Json
import keel.api.common.Proc
import keel.api.common.Time
import keel.api.plugins.PluginService
import keel.api.projects.ProjectService
import keel.api.settings.SettingsService
import keel.api.tasks.TaskProperties
import keel.api.tasks.TaskStatus
import keel.api.tasks.TicketTracker
import org.springframework.beans.factory.ObjectProvider
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Service
import java.util.concurrent.ConcurrentHashMap

/** A project's GitHub Issues connection. The token is not here: it is the one of Connections › GitHub. */
data class GhIssuesSettings(
    /** owner/repo; null = the project's origin on GitHub */
    val repo: String? = null,
    /** which open issues become tasks: assigned (to the token's user) | label | all */
    val follow: String = "assigned",
    /** the label to follow when [follow] is label */
    val label: String? = null,
    /** keel status → "-" | open | closed | closed:not planned | label:<name>; what is not set here is [DEFAULT_MAP]'s */
    val statusMap: Map<String, String> = emptyMap(),
    /** keel comments on the issue as the task moves (the flow, the PR, the review) */
    val comment: Boolean = true,
    /** keel's pull request says "Closes #N", so merging it closes the issue */
    val closesOnMerge: Boolean = true,
    /** GitHub logins asked to review the PR when the task names none */
    val githubReviewers: List<String> = emptyList(),
    /** minutes between syncs; 0 = only "Sync now" */
    val pollMinutes: Int = 5,
)

/** PUT body: null keeps the saved value. */
data class GhIssuesSave(
    val repo: String? = null,
    val follow: String? = null,
    val label: String? = null,
    val statusMap: Map<String, String>? = null,
    val comment: Boolean? = null,
    val closesOnMerge: Boolean? = null,
    val githubReviewers: List<String>? = null,
    val pollMinutes: Int? = null,
)

data class GhIssuesView(
    val connected: Boolean,
    val settings: GhIssuesSettings,
    /** owner/repo a sync reads now (the setting, else the origin), or null when there is none */
    val repo: String?,
    /** settings | origin | null */
    val repoFrom: String?,
    /** the project's origin on GitHub (owner/repo), or null */
    val origin: String?,
    val repoUrl: String?,
    /** the host the GitHub token belongs to */
    val host: String,
    /** Connections › GitHub has a token */
    val tokenSet: Boolean,
    /** keel status → what keel does to the issue, every status (the saved map over [DEFAULT_MAP]) */
    val statusMap: Map<String, String>,
    val lastSyncAt: String?,
    val lastSyncError: String?,
    val me: String?,
    /** the other tracker this project follows (a project uses one), e.g. "Jira"; null when none */
    val otherTracker: String?,
)

data class GhIssuesTest(val ok: Boolean, val user: String? = null, val repo: String? = null, val error: String? = null, val hint: String? = null,
                        /** the token may only read: keel cannot close, reopen or label */
                        val readOnly: Boolean = false)

/** GitHub's answer as an api error: 502 with GitHub's words (never the token). */
class GhIssuesFailed(e: GitHubIssuesException) : ApiException(HttpStatus.BAD_GATEWAY, e.message, e.hint)

/**
 * The GitHub Issues connection of each project (its settings are the plugin's own: `plugins.github-issues.connection`),
 * the GitHub client for it, and what the last sync said. The GitHub token is core's (Connections › GitHub): it goes only
 * to the host it belongs to.
 */
@Service
class GitHubIssuesService(
    private val settingsStore: SettingsService,
    private val plugins: PluginService,
    private val projects: ProjectService,
    private val trackers: ObjectProvider<TicketTracker>,
    private val props: TaskProperties,
    private val mapper: ObjectMapper,
) {
    private val synced = ConcurrentHashMap<String, Pair<String, String?>>()
    private val mes = ConcurrentHashMap<String, String>()

    private fun store() = settingsStore.plugin(PLUGIN)

    fun settings(pid: String): GhIssuesSettings? =
        (store().overrides(pid)[KEY] as? Map<*, *>)?.let { runCatching { Json.mapper.convertValue(it, GhIssuesSettings::class.java) }.getOrNull() }

    fun connectedProjects(): List<String> {
        val listed = projects.rows().map { it.id }
        return listed.filter { settings(it) != null }
    }

    /** keel's status map for the connection: [DEFAULT_MAP], then what the person set. */
    fun fullMap(s: GhIssuesSettings): Map<String, String> = DEFAULT_MAP + s.statusMap.filterKeys { it in TaskStatus.ALL }

    fun host(): String = plugins.githubHost()

    /** The project's origin when it is on the token's GitHub host. */
    fun origin(pid: String): RepoRef? {
        val r = Proc.run(listOf("git", "-c", "core.quotePath=false", "config", "--get", "remote.origin.url"), projects.root(pid), 10)
        if (!r.ok) return null
        return originRepo(r.out.trim(), host())
    }

    /** The repo a sync reads: the setting, else the origin. */
    fun repo(pid: String, s: GhIssuesSettings? = settings(pid)): RepoRef? =
        s?.repo?.let { RepoRef.parse(it, host()) } ?: origin(pid)

    fun client(pid: String, s: GhIssuesSettings? = settings(pid)): GitHubIssuesClient? {
        val set = s ?: return null
        val token = plugins.githubToken() ?: return null
        val repo = repo(pid, set) ?: return null
        return GitHubIssuesClient(token, repo, mapper, props.githubApi, fullMap(set), set.comment)
    }

    fun view(pid: String): GhIssuesView {
        projects.require(pid)
        val s = settings(pid)
        val origin = origin(pid)
        val repo = s?.repo?.let { RepoRef.parse(it, host()) } ?: origin
        val last = synced[pid]
        val other = trackers.orderedStream().toList().firstOrNull { it.source != SOURCE && it.connection(pid) != null }?.title
        return GhIssuesView(s != null, s ?: GhIssuesSettings(), repo?.path, if (s?.repo != null && repo != null) "settings" else if (repo != null) "origin" else null,
            origin?.path, repo?.web, host(), plugins.githubToken() != null, fullMap(s ?: GhIssuesSettings()), last?.first, last?.second, mes[pid], other)
    }

    fun save(pid: String, b: GhIssuesSave): GhIssuesView {
        projects.require(pid)
        trackers.orderedStream().toList().firstOrNull { it.source != SOURCE && it.connection(pid) != null }?.let {
            throw Conflict("This project follows ${it.title} already", "A project follows one tracker: remove its ${it.title} connection first (Settings › Connections).")
        }
        val cur = settings(pid) ?: GhIssuesSettings()
        val repo = b.repo?.trim()?.let { r ->
            if (r.isEmpty()) null
            else (RepoRef.parse(r, host()) ?: throw BadRequest("\"$r\" is not a GitHub repository", "Like owner/repo (on ${host()}). Leave it empty for the project's origin.")).path
        } ?: if (b.repo != null) null else cur.repo
        val follow = (b.follow?.trim()?.lowercase() ?: cur.follow)
        if (follow !in FOLLOW) throw BadRequest("Follow \"$follow\"?", "Pick one of: ${FOLLOW.joinToString()}.")
        val label = b.label?.trim()?.ifEmpty { null } ?: if (b.label != null) null else cur.label
        if (follow == "label" && label == null) throw BadRequest("Which label?", "Name the label whose issues become tasks.")
        label?.let { checkLabel(it) }
        val map = b.statusMap?.let { m ->
            m.forEach { (k, v) ->
                if (k !in TaskStatus.ALL) throw BadRequest("\"$k\" is not a keel status", "One of: ${TaskStatus.ALL.joinToString()}.")
                checkTarget(v)
            }
            m.mapValues { it.value.trim() }.filterValues { it.isNotEmpty() }
        } ?: cur.statusMap
        val poll = b.pollMinutes ?: cur.pollMinutes
        if (poll !in 0..1440) throw BadRequest("Sync every $poll minutes?", "0 (only Sync now) to 1440.")
        val reviewers = b.githubReviewers?.map { it.trim().removePrefix("@") }?.filter { it.isNotEmpty() }?.distinct() ?: cur.githubReviewers
        reviewers.firstOrNull { !LOGIN.matches(it) }?.let { throw BadRequest("\"$it\" is not a GitHub login") }
        val next = GhIssuesSettings(repo, follow, label, map, b.comment ?: cur.comment, b.closesOnMerge ?: cur.closesOnMerge, reviewers, poll)
        if (repo(pid, next) == null) {
            throw BadRequest("Which repository?", "This project's origin is not on ${host()}: name the repository (owner/repo).")
        }
        store().updateProject(pid, mapOf(KEY to Json.mapper.convertValue(next, Map::class.java)))
        return view(pid)
    }

    fun delete(pid: String) {
        projects.require(pid)
        store().updateProject(pid, mapOf(KEY to null))
        synced.remove(pid)
        mes.remove(pid)
    }

    /** The token, the repo and its issues, with the saved connection or with the unsaved form in [b]. */
    fun test(pid: String, b: GhIssuesSave?): GhIssuesTest {
        projects.require(pid)
        val cur = settings(pid) ?: GhIssuesSettings()
        val s = if (b?.repo != null) cur.copy(repo = b.repo.trim().ifEmpty { null }) else cur
        if (plugins.githubToken() == null) {
            return GhIssuesTest(false, error = "keel has no GitHub token", hint = "Sign in to GitHub in Settings › Connections › GitHub.")
        }
        if (s.repo != null && RepoRef.parse(s.repo, host()) == null) {
            return GhIssuesTest(false, error = "\"${s.repo}\" is not a GitHub repository", hint = "Like owner/repo (on ${host()}).")
        }
        val c = client(pid, s) ?: return GhIssuesTest(false, error = "No repository",
            hint = "This project's origin is not on ${host()}: name the repository (owner/repo).")
        return try {
            val me = c.me()
            val r = c.repository()
            mes[pid] = me.login
            if (!r.hasIssues) GhIssuesTest(false, me.login, r.fullName, "Issues are turned off for ${r.fullName}", "Turn them on in the repository's settings on GitHub.")
            else GhIssuesTest(true, me.login, r.fullName, readOnly = !r.canPush)
        } catch (e: GitHubIssuesException) {
            GhIssuesTest(false, error = e.message, hint = e.hint)
        }
    }

    /** The repo's labels, for the status map's picker. */
    fun labels(pid: String): List<String> {
        projects.require(pid)
        val c = client(pid, settings(pid) ?: GhIssuesSettings()) ?: throw Conflict("No GitHub token or repository", "Sign in to GitHub in Settings › Connections, and name the repository.")
        return try { c.labels() } catch (e: GitHubIssuesException) { throw GhIssuesFailed(e) }
    }

    fun lastSync(pid: String): Pair<String, String?>? = synced[pid]

    fun markSynced(pid: String, error: String?) {
        synced[pid] = Time.now() to error
    }

    fun me(pid: String): String? = mes[pid]

    fun setMe(pid: String, login: String) {
        mes[pid] = login
    }

    private fun checkLabel(l: String) {
        if (l.length > 50 || l.any { it.isISOControl() }) throw BadRequest("\"${l.take(60)}\" is not a GitHub label", "At most 50 characters.")
    }

    private fun checkTarget(v: String) {
        val t = v.trim()
        if (t.isEmpty() || t == TaskStatus.DO_NOT_MOVE || t.lowercase() in setOf(GitHubIssuesClient.OPEN, GitHubIssuesClient.CLOSED, GitHubIssuesClient.NOT_PLANNED)) return
        GitHubIssuesClient.labelOf(t)?.let { checkLabel(it); return }
        throw BadRequest("\"$v\" is not something keel can do to an issue", "Use -, open, closed, closed:not planned or label:<name>.")
    }

    companion object {
        const val PLUGIN = "github-issues"
        const val KEY = "connection"
        const val SOURCE = "github"
        val FOLLOW = listOf("assigned", "label", "all")
        val LOGIN = Regex("^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(/[A-Za-z0-9._-]+)?$")

        /** What keel does to an issue by default: close it when the task is done or cancelled, nothing else. */
        val DEFAULT_MAP: Map<String, String> = TaskStatus.ALL.associateWith { TaskStatus.DO_NOT_MOVE } +
            mapOf(TaskStatus.DONE to GitHubIssuesClient.CLOSED, TaskStatus.CANCELLED to GitHubIssuesClient.NOT_PLANNED)

        /** A remote URL's GitHub repo when it is on [host] (https, ssh or scp form); null otherwise. */
        fun originRepo(url: String, host: String): RepoRef? {
            val u = url.trim().removeSuffix("/").removeSuffix(".git")
            val m = Regex("^(?:https?|ssh|git)://(?:[^@/]+@)?([^/:]+)(?::\\d+)?/(.+)$").matchEntire(u)
                ?: Regex("^(?:[^@/]+@)?([^/:]+):(.+)$").matchEntire(u) ?: return null
            if (!m.groupValues[1].equals(host, true)) return null
            return RepoRef.parse(m.groupValues[2].trim('/'), host)
        }
    }
}
