package keel.api.review

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.events.EventHub
import keel.api.plugins.PluginService
import keel.api.projects.ProjectService
import keel.api.repo.FileDiff
import keel.api.repo.RepoService
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.nio.file.Path
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/** keel.review.* (application.yml): the API roots (tests or an Enterprise / self-hosted server) and the fetch timeout. */
@ConfigurationProperties(prefix = "keel.review")
data class ReviewProperties(
    val githubApi: String = "",
    val gitlabApi: String = "",
    val fetchTimeoutSec: Long = 120,
    /** Follow keel's AI runs on the event thread (tests) instead of a background worker. */
    val inlineEffects: Boolean = false,
)

/** A place in the code, for go to declaration and find usages. */
data class CodePlace(val path: String, val line: Int, val text: String, val declaration: Boolean, val test: Boolean, val changed: Boolean)
data class Places(val symbol: String, val ref: String, val places: List<CodePlace>, val truncated: Boolean)
data class FileAt(val path: String, val ref: String, val sha: String, val text: String, val truncated: Boolean)

/** A review's two commits: base (where it left its base branch) and head, and the refs that hold them. */
data class Refs(val key: String, val base: String, val head: String, val baseLabel: String, val headLabel: String, val number: Int?, val branch: String?)

/**
 * The Code Review plugin: a pull request (`pr:<n>`) or a local branch (`branch:<name>`) as a review. The host
 * (GitHub, GitLab) gives the pull request, its threads and takes the person's review; git gives the files and the
 * diffs from refs fetched into refs/keel/review/. Pending comments stay in keel until the person submits them.
 */
@Service
class ReviewService(
    private val projects: ProjectService,
    private val plugins: PluginService,
    private val repo: RepoService,
    private val jdbc: JdbcTemplate,
    private val mapper: ObjectMapper,
    private val props: ReviewProperties,
    private val hub: EventHub,
    private val gitlab: GitLabConnection,
) {
    private val logins = ConcurrentHashMap<String, String>()

    companion object {
        private val KEY = Regex("^(pr):(\\d{1,9})$|^(branch):(.+)$")
        private val SYMBOL = Regex("^[A-Za-z_$][A-Za-z0-9_$]{0,80}$")
        private const val DECL_WORDS = "fun|val|var|class|interface|object|enum|record|struct|trait|typealias|type|def|function|const|let|fn|func|impl|module"
        const val PLUGIN = "review"
        const val MAX_FILE = 1_000_000
    }

    private fun root(pid: String): Path {
        plugins.require(pid, PLUGIN)
        return projects.root(pid)
    }

    // ---------------------------------------------------------------- the host

    fun hostRef(root: Path): HostRef? = ReviewGit.identity(root, gitlab.host())

    /** The host to talk to, or null with the reason (no remote, no token). */
    private fun host(root: Path): Pair<CodeHost?, String?> {
        val ref = hostRef(root) ?: return null to "This project has no origin remote on GitHub or GitLab, so keel cannot list its pull requests."
        if (ref.kind == "gitlab") {
            val token = gitlab.token() ?: return null to "Add GitLab (the server and a token) in Connections to see the merge requests and post reviews."
            return GitLabHost(ref, token, mapper, props.gitlabApi) to null
        }
        val token = plugins.githubToken() ?: return null to "Add a GitHub token in Connections to see the pull requests and post reviews."
        return GitHubHost(ref, token, mapper, props.githubApi) to null
    }

    private fun needHost(root: Path): CodeHost {
        val (h, why) = host(root)
        return h ?: throw Conflict(why ?: "No host")
    }

    private fun me(h: CodeHost): String {
        val k = h.ref.host + ":" + MessageDigest.getInstance("SHA-256").digest(h.authHeader().toByteArray()).joinToString("") { "%02x".format(it) }.take(16)
        return logins.getOrPut(k) { h.me() }
    }

    fun prs(pid: String, filter: String): PrList {
        val root = root(pid)
        val (h, why) = host(root)
        if (h == null) return PrList(hostRef(root), null, emptyList(), emptyMap(), why)
        val me = me(h)
        val all = h.list(me)
        val counts = mapOf("review" to all.count { it.reviewRequested }, "assigned" to all.count { it.assigned }, "mine" to all.count { it.mine }, "all" to all.size)
        val shown = when (filter) {
            "review" -> all.filter { it.reviewRequested }
            "assigned" -> all.filter { it.assigned }
            "mine" -> all.filter { it.mine }
            "all", "" -> all
            else -> throw BadRequest("filter is review, assigned, mine or all")
        }
        return PrList(h.ref, me, shown, counts, null)
    }

    fun thisBranch(pid: String): BranchSummary {
        val root = root(pid)
        val branch = projects.branch(root)?.takeIf { it != "HEAD" }
        val base = repo.base(root)
        if (branch == null) return BranchSummary(null, base, 0, 0, 0, 0, null, "The project folder is not on a branch.")
        if (base == null || base == branch) return BranchSummary(branch, base, 0, 0, 0, 0, null,
            if (base == null) "keel found no main or master branch to compare with." else "You are on the base branch: switch to a branch to review it.")
        val ahead = ReviewGit.git(root, "rev-list", "--count", "refs/heads/$base..refs/heads/$branch").out.trim().toIntOrNull() ?: 0
        val files = ReviewGit.files(root, "refs/heads/$base", "refs/heads/$branch")
        val (h, _) = host(root)
        val (pr, note) = if (h == null) null to null else try {
            h.forBranch(branch, me(h)) to null
        } catch (e: HostException) {
            null to e.message
        }
        return BranchSummary(branch, base, ahead, files.size, files.sumOf { it.added }, files.sumOf { it.removed }, pr, note)
    }

    // ---------------------------------------------------------------- one review

    private fun parse(key: String): Pair<String, String> {
        val m = KEY.matchEntire(key.trim()) ?: throw BadRequest("A review is pr:<number> or branch:<name>")
        return if (m.groupValues[1] == "pr") "pr" to m.groupValues[2] else "branch" to ReviewGit.branchName(m.groupValues[4])
    }

    /** The two commits of a review (a pull request must have been fetched: open it first). */
    fun refs(pid: String, key: String): Refs {
        val root = root(pid)
        val (kind, v) = parse(key)
        if (kind == "pr") {
            val n = v.toInt()
            val head = ReviewGit.sha(root, ReviewGit.headRef(n)) ?: throw Conflict("Pull request #$n is not fetched yet", "Open it in the Review window first.")
            val baseTip = ReviewGit.sha(root, ReviewGit.baseRef(n)) ?: throw Conflict("The base of #$n is not fetched yet", "Open it in the Review window first.")
            val base = ReviewGit.mergeBase(root, baseTip, head) ?: baseTip
            return Refs(key, base, head, "base", "#$n", n, null)
        }
        val base = repo.base(root) ?: throw Conflict("No base branch found", "keel looks for main or master.")
        val head = ReviewGit.sha(root, "refs/heads/$v") ?: throw NotFound("No branch $v")
        val baseTip = ReviewGit.sha(root, "refs/heads/$base") ?: throw NotFound("No branch $base")
        return Refs(key, ReviewGit.mergeBase(root, baseTip, head) ?: baseTip, head, base, v, null, v)
    }

    fun view(pid: String, key: String, refresh: Boolean = false): ReviewView {
        val root = root(pid)
        val (kind, v) = parse(key)
        val notes = mutableListOf<String>()
        if (kind == "branch") {
            val r = refs(pid, key)
            val files = ReviewGit.files(root, r.base, r.head)
            val (h, why) = host(root)
            val pr = h?.let { runCatching { it.forBranch(v, me(it)) }.getOrNull() }
            if (pr != null) notes += "This branch has pull request #${pr.number}: open it to see its threads and to post a review."
            else if (why != null) notes += why
            if (projects.branch(root) == v && repo.marks(root).isNotEmpty()) notes += "Uncommitted changes are not part of the review: it shows the commits."
            return ReviewView(key, "branch", null, v, null, null, r.baseLabel, v, r.base, r.head, pr?.url, null, false, true,
                emptyList(), emptyList(), emptyList(), files, files.sumOf { it.added }, files.sumOf { it.removed },
                repo.commits(root, 100, null, "${r.base}..${r.head}"), emptyList(), emptyList(), drafts(pid, key), viewed(pid, key, r.head),
                false, h?.ref, h?.let { me(it) }, notes)
        }
        val n = v.toInt()
        val h = needHost(root)
        val pr = h.pr(n)
        val fetched = ReviewGit.sha(root, ReviewGit.headRef(n))
        if (refresh || fetched != pr.headSha || ReviewGit.sha(root, ReviewGit.baseRef(n)) == null) ReviewGit.fetchPr(root, h, n, pr.base, props.fetchTimeoutSec)
        val r = refs(pid, key)
        val files = ReviewGit.files(root, r.base, r.head)
        fun <T> part(what: String, fallback: T, block: () -> T): T = try {
            block()
        } catch (e: HostException) {
            notes += "keel could not read $what: ${e.message}"
            fallback
        }
        val threads = part("the threads", emptyList()) { h.threads(n) }
        val conversation = part("the conversation", emptyList()) { h.conversation(n) }
        val checks = part("the checks", emptyList()) { h.checks(pr) }
        val decisions = part("the reviews", Decisions(emptyList(), emptyList())) { h.decisions(n) }
        if (files.size > 60 || files.sumOf { it.added + it.removed } > 1000) {
            notes += "This pull request is big (${files.size} files, +${files.sumOf { it.added }} −${files.sumOf { it.removed }}). Reviews find more in small ones: ask for a split if it mixes changes."
        }
        return ReviewView(key, "pr", n, pr.title, pr.author, pr.body, pr.base, pr.branch, r.base, r.head, pr.url, pr.state, pr.draft, pr.sameRepo,
            checks, decisions.approved, decisions.changes, files, files.sumOf { it.added }, files.sumOf { it.removed },
            repo.commits(root, 100, null, "${r.base}..${r.head}"), threads, conversation, drafts(pid, key), viewed(pid, key, r.head),
            pr.state == "open", h.ref, me(h), notes, pr.author.equals(me(h), true), pr.mergeable, pr.mergeState)
    }

    private fun fileOf(root: Path, r: Refs, path: String): ChangedFile =
        ReviewGit.files(root, r.base, r.head).firstOrNull { it.path == path } ?: throw NotFound("$path is not changed in this review")

    fun diff(pid: String, key: String, path: String): FileDiff {
        val root = root(pid)
        val r = refs(pid, key)
        return ReviewGit.fileDiff(root, r.base, r.head, fileOf(root, r, path), "${r.baseLabel}…${r.headLabel}")
    }

    // ---------------------------------------------------------------- jumping through the code

    private fun cleanPath(path: String): String {
        val p = path.trim().trimStart('/')
        if (p.isEmpty() || p.split('/').any { it == ".." || it.isEmpty() } || p.any { it.isISOControl() }) throw BadRequest("That is not a path in the repo")
        return p
    }

    /** A whole file as it is in the review's head (or base) commit: Jump to source, Go to declaration. */
    fun fileAt(pid: String, key: String, path: String, side: String = "head"): FileAt {
        val root = root(pid)
        val r = refs(pid, key)
        val sha = if (side == "base") r.base else r.head
        val p = cleanPath(path)
        val res = ReviewGit.git(root, "show", "$sha:$p", timeout = 20)
        if (!res.ok) throw NotFound("$p is not in ${if (side == "base") r.baseLabel else r.headLabel}")
        val text = res.out
        return FileAt(p, if (side == "base") r.baseLabel else r.headLabel, sha, if (text.length > MAX_FILE) text.take(MAX_FILE) else text, text.length > MAX_FILE)
    }

    private fun grep(root: Path, sha: String, args: List<String>, limit: Int): Pair<List<Triple<String, Int, String>>, Boolean> {
        val res = ReviewGit.git(root, "grep", "-n", "-I", "--no-color", *args.toTypedArray(), sha, "--", ".", timeout = 20)
        // exit 1 = nothing found
        if (!res.ok && res.code != 1) throw Conflict("git grep failed", res.err.lines().firstOrNull().orEmpty())
        val out = mutableListOf<Triple<String, Int, String>>()
        for (line in res.out.lines()) {
            if (line.isBlank()) continue
            val rest = line.removePrefix("$sha:")
            val m = Regex("^(.+?):(\\d+):(.*)$").matchEntire(rest) ?: continue
            out += Triple(m.groupValues[1], m.groupValues[2].toInt(), m.groupValues[3].trim().take(240))
            if (out.size >= limit) return out to true
        }
        return out to false
    }

    private fun declRegex(symbol: String): Regex {
        val s = Regex.escape(symbol)
        return Regex("(\\b($DECL_WORDS)\\s+(<[^>]*>\\s*)?([A-Za-z0-9_.]*\\.)?$s\\b)|" +
            "(^\\s*(public|private|protected|internal|static|final|abstract|override|async|export|default|\\s)*[A-Za-z0-9_<>\\[\\]?,]+\\s+$s\\s*\\()|" +
            "(\\b$s\\s*[:=]\\s*(async\\s*)?(\\(|function\\b))")
    }

    private fun isTest(path: String) = Regex("(^|/)(test|tests|spec|__tests__)/|[._-](test|spec)\\.[a-z]+$|Test\\.[a-z]+$", RegexOption.IGNORE_CASE).containsMatchIn(path)

    private fun places(pid: String, key: String, symbol: String, declarationsOnly: Boolean): Places {
        if (!SYMBOL.matches(symbol)) throw BadRequest("That is not a name keel can look for")
        val root = root(pid)
        val r = refs(pid, key)
        val changed = ReviewGit.files(root, r.base, r.head).map { it.path }.toSet()
        val decl = declRegex(symbol)
        val (hits, more) = grep(root, r.head, listOf("-w", "-F", "-e", symbol), if (declarationsOnly) 2000 else 300)
        val all = hits.map { (p, l, t) ->
            CodePlace(p, l, t, decl.containsMatchIn(t) && !Regex("\\b(return|new|throw|await|yield)\\s+${Regex.escape(symbol)}\\b").containsMatchIn(t), isTest(p), p in changed)
        }
        val list = if (declarationsOnly) all.filter { it.declaration } else all
        val sorted = list.sortedWith(compareByDescending<CodePlace> { it.declaration }.thenByDescending { it.changed }.thenBy { it.test }.thenBy { it.path }.thenBy { it.line })
        return Places(symbol, r.headLabel, sorted, more)
    }

    fun definition(pid: String, key: String, symbol: String) = places(pid, key, symbol, true)
    fun usages(pid: String, key: String, symbol: String) = places(pid, key, symbol, false)

    // ---------------------------------------------------------------- your pending comments and viewed files

    fun drafts(pid: String, key: String): List<Draft> = jdbc.query(
        "SELECT * FROM review_drafts WHERE project_id = ? AND review_key = ? ORDER BY created_at, id", { rs, _ ->
            Draft(rs.getString("id"), rs.getString("path"), rs.getObject("line")?.let { (it as Number).toInt() }, rs.getString("side"), rs.getString("body"),
                rs.getString("finding_id"), rs.getString("created_at"))
        }, pid, key)

    fun addDraft(pid: String, b: DraftBody): Draft {
        root(pid)
        parse(b.key)
        val body = b.body.trim().ifEmpty { throw BadRequest("Write the comment") }.take(20_000)
        if (b.side !in setOf("RIGHT", "LEFT")) throw BadRequest("side is RIGHT or LEFT")
        val path = b.path?.let { cleanPath(it) }
        if ((path == null) != (b.line == null)) throw BadRequest("A line comment needs both the file and the line")
        if (b.line != null && b.line < 1) throw BadRequest("line starts at 1")
        val d = Draft("dr_" + UUID.randomUUID().toString().replace("-", "").take(12), path, b.line, b.side, body, b.findingId, Time.now())
        jdbc.update("INSERT INTO review_drafts(id, project_id, review_key, path, line, side, body, finding_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            d.id, pid, b.key, d.path, d.line, d.side, d.body, d.findingId, d.createdAt)
        return d
    }

    fun editDraft(pid: String, id: String, body: String): Draft {
        root(pid)
        val text = body.trim().ifEmpty { throw BadRequest("Write the comment") }.take(20_000)
        if (jdbc.update("UPDATE review_drafts SET body = ? WHERE project_id = ? AND id = ?", text, pid, id) == 0) throw NotFound("No pending comment $id")
        return jdbc.query("SELECT * FROM review_drafts WHERE id = ?", { rs, _ ->
            Draft(rs.getString("id"), rs.getString("path"), rs.getObject("line")?.let { (it as Number).toInt() }, rs.getString("side"), rs.getString("body"),
                rs.getString("finding_id"), rs.getString("created_at"))
        }, id).first()
    }

    fun deleteDraft(pid: String, id: String) {
        root(pid)
        if (jdbc.update("DELETE FROM review_drafts WHERE project_id = ? AND id = ?", pid, id) == 0) throw NotFound("No pending comment $id")
    }

    fun viewed(pid: String, key: String, head: String): List<String> = jdbc.queryForList(
        "SELECT path FROM review_viewed WHERE project_id = ? AND review_key = ? AND head_sha = ? ORDER BY path", String::class.java, pid, key, head)

    fun setViewed(pid: String, b: ViewedBody): List<String> {
        val r = refs(pid, b.key)
        val p = cleanPath(b.path)
        if (b.viewed) {
            jdbc.update("INSERT OR REPLACE INTO review_viewed(project_id, review_key, path, head_sha, at) VALUES (?,?,?,?,?)", pid, b.key, p, r.head, Time.now())
        } else {
            jdbc.update("DELETE FROM review_viewed WHERE project_id = ? AND review_key = ? AND path = ?", pid, b.key, p)
        }
        return viewed(pid, b.key, r.head)
    }

    // ---------------------------------------------------------------- talking back to the host

    private fun prNumber(key: String): Int {
        val (kind, v) = parse(key)
        if (kind != "pr") throw Conflict("A branch has no threads", "Open its pull request to comment and approve.")
        return v.toInt()
    }

    private fun thread(h: CodeHost, n: Int, id: String): ReviewThread =
        h.threads(n).firstOrNull { it.id == id } ?: throw NotFound("No thread $id on #$n")

    fun reply(pid: String, threadId: String, b: ReplyBody): ReviewView {
        val root = root(pid)
        val n = prNumber(b.key)
        val body = b.body.trim().ifEmpty { throw BadRequest("Write the reply") }
        val h = needHost(root)
        h.reply(n, thread(h, n, threadId), body)
        return view(pid, b.key)
    }

    fun resolve(pid: String, threadId: String, b: ResolveBody): ReviewView {
        val root = root(pid)
        val n = prNumber(b.key)
        val h = needHost(root)
        h.resolve(n, thread(h, n, threadId), b.resolved)
        return view(pid, b.key)
    }

    /** One review with every pending comment. A comment whose line is not in the diff goes into the review's text. */
    fun submit(pid: String, b: SubmitBody): SubmitResult {
        val root = root(pid)
        val n = prNumber(b.key)
        if (b.event !in setOf("COMMENT", "APPROVE", "REQUEST_CHANGES")) throw BadRequest("event is COMMENT, APPROVE or REQUEST_CHANGES")
        val h = needHost(root)
        val pr = h.pr(n)
        if (pr.state != "open") throw Conflict("#$n is ${pr.state}", "Only an open pull request takes a review.")
        val r = refs(pid, b.key)
        if (r.head != pr.headSha) throw Conflict("#$n has new commits", "Open it again (Refresh) so your comments sit on the code that is there now.")
        val pending = drafts(pid, b.key)
        if (b.event == "REQUEST_CHANGES" && b.body.isBlank() && pending.isEmpty()) throw BadRequest("Say what must change", "Write a comment, or a line comment.")
        if (b.event == "COMMENT" && b.body.isBlank() && pending.isEmpty()) throw BadRequest("Nothing to send", "Write a comment, or add a line comment first.")
        val files = ReviewGit.files(root, r.base, r.head).associateBy { it.path }
        val lines = mutableMapOf<String, Pair<Set<Int>, Set<Int>>>()
        val out = mutableListOf<OutComment>()
        val extra = mutableListOf<String>()
        for (d in pending) {
            val f = d.path?.let { files[it] }
            val ok = f != null && d.line != null && lines.getOrPut(f.path) {
                ReviewGit.commentable(ReviewGit.fileDiff(root, r.base, r.head, f, "").diff)
            }.let { (right, left) -> if (d.side == "LEFT") d.line in left else d.line in right }
            if (ok) out += OutComment(d.path!!, d.line!!, d.side, d.body)
            else extra += (if (d.path != null) "**`${d.path}${d.line?.let { ":$it" } ?: ""}`** " else "") + d.body
        }
        val body = listOf(b.body.trim(), extra.joinToString("\n\n")).filter { it.isNotBlank() }.joinToString("\n\n")
        val url = h.submit(pr, b.event, body, out)
        jdbc.update("DELETE FROM review_drafts WHERE project_id = ? AND review_key = ?", pid, b.key)
        hub.publish(pid, "review.submitted", mapOf("key" to b.key, "event" to b.event, "comments" to out.size))
        return SubmitResult(out.size, extra.size, b.event, url, view(pid, b.key))
    }

    /** Merges your own pull request (keel offers Merge only on yours). The host checks the rest: checks, reviews, conflicts. */
    fun merge(pid: String, b: MergeBody): MergeResult {
        val root = root(pid)
        val n = prNumber(b.key)
        if (b.method !in setOf("merge", "squash", "rebase")) throw BadRequest("method is merge, squash or rebase")
        val h = needHost(root)
        val pr = h.pr(n)
        if (!pr.author.equals(me(h), true)) throw Conflict("Only your own ${if (h.ref.kind == "gitlab") "merge" else "pull"} request can be merged here", "Its author merges it, or merge it on ${h.ref.host}.")
        if (pr.state != "open") throw Conflict("${if (h.ref.kind == "gitlab") "!" else "#"}$n is ${pr.state}")
        if (pr.draft) throw Conflict("${if (h.ref.kind == "gitlab") "!" else "#"}$n is a draft", "Mark it ready for review first.")
        val message = h.merge(pr, b.method, b.deleteBranch)
        hub.publish(pid, "review.merged", mapOf("key" to b.key, "method" to b.method))
        return MergeResult(true, message, view(pid, b.key))
    }

    /** The pull request's code in the project folder, to run and test it: its own branch name, or review/pr-<n> for a fork. */
    fun checkout(pid: String, key: String): CheckoutResult {
        val root = root(pid)
        val n = prNumber(key)
        val h = needHost(root)
        val pr = h.pr(n)
        ReviewGit.fetchPr(root, h, n, pr.base, props.fetchTimeoutSec)
        val name = if (pr.sameRepo) ReviewGit.branchName(pr.branch) else "review/pr-$n"
        val exists = ReviewGit.git(root, "show-ref", "--verify", "--quiet", "refs/heads/$name").ok
        val sw = if (exists) ReviewGit.git(root, "switch", name) else ReviewGit.git(root, "switch", "-c", name, ReviewGit.headRef(n))
        if (!sw.ok) throw Conflict("git did not switch to $name", (sw.err.ifBlank { sw.out }).lines().firstOrNull { it.isNotBlank() }.orEmpty().take(240))
        var note = "On $name now: the code of #$n is in the project folder."
        if (exists) {
            val ff = ReviewGit.git(root, "merge", "--ff-only", "--quiet", ReviewGit.headRef(n))
            if (!ff.ok) note = "On $name now. It has commits #$n does not have, so keel left it as it was."
        }
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        return CheckoutResult(name, note)
    }
}
