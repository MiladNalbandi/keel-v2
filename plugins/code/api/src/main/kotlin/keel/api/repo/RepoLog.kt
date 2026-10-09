package keel.api.repo

import com.fasterxml.jackson.annotation.JsonInclude
import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.projects.ProjectService
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.nio.file.Files
import java.nio.file.Path

/** v0.15.2 One branch, remote branch or tag in the Git log's branch list. `ahead` / `behind`: a local branch against the base. */
@JsonInclude(JsonInclude.Include.NON_NULL)
data class RefItem(
    val name: String,
    val sha: String,
    val date: String,
    val subject: String,
    val current: Boolean = false,
    val upstream: String? = null,
    val ahead: Int? = null,
    val behind: Int? = null,
)

/** v0.15.2 Every ref the Git log can show: local branches (the current one marked), remote branches and tags. `head` is null when detached. */
data class RefsView(val head: String?, val base: String?, val local: List<RefItem>, val remote: List<RefItem>, val tags: List<RefItem>)

/** A name that points at a commit. kind: head (a detached HEAD) | local | remote | tag; `current` = HEAD is on this local branch. */
data class LogRef(val name: String, val kind: String, val current: Boolean = false)

/**
 * v0.15.2 One commit of the Git log. `parents` draw the graph (rewritten to the shown commits when a path filters the log);
 * `in_base` = the base branch has it already (false = only on the shown branches).
 */
data class LogCommit(
    val sha: String,
    val parents: List<String>,
    val subject: String,
    val author: String,
    val email: String,
    val at: String,
    val refs: List<LogRef>,
    val inBase: Boolean,
    val keel: Boolean,
)

/**
 * v0.15.2 The Git log of one branch (or all of them): `branch` is what it shows (null = all branches), `head` the current branch,
 * `ahead` / `behind` the shown branch against `base` (0 for all branches and for the base itself).
 */
data class GitLog(
    val branch: String?,
    val head: String?,
    val base: String?,
    val ahead: Int,
    val behind: Int,
    val commits: List<LogCommit>,
    val hasMore: Boolean,
)

/**
 * v0.15.2 Code › Source control › Log, like JetBrains' Git log: read only, no plugin needed. It reads refs and commits with
 * git and never checks out, resets or commits. A branch name is a local branch, a remote branch or a tag (never an option or
 * a range); the text and the author are fixed strings, not patterns.
 */
@Service
class RepoLog(private val projects: ProjectService, private val repo: RepoService) {

    fun refs(pid: String): RefsView {
        val root = projects.root(pid)
        if (!Files.exists(root.resolve(".git"))) return RefsView(null, null, emptyList(), emptyList(), emptyList())
        val head = current(root)
        val base = repo.base(root)
        val local = forEachRef(root, "refs/heads/", "-committerdate", 200).mapIndexed { i, r ->
            val counts = if (base != null && r.name != base && i < COUNTED) aheadBehind(root, "refs/heads/$base", "refs/heads/${r.name}") else null
            r.copy(current = r.name == head, ahead = counts?.second, behind = counts?.first)
        }
        val remote = forEachRef(root, "refs/remotes/", "-committerdate", 300).filterNot { it.name.endsWith("/HEAD") }
        val tags = forEachRef(root, "refs/tags/", "-creatordate", 100)
        return RefsView(head, base, local, remote, tags)
    }

    fun log(pid: String, branch: String?, all: Boolean, author: String?, q: String?, path: String?, limit: Int, skip: Int): GitLog {
        val root = projects.root(pid)
        if (!Files.exists(root.resolve(".git"))) throw BadRequest("This project is not a git repo")
        val head = current(root)
        val base = repo.base(root)
        val name = branch?.trim()?.takeIf { it.isNotEmpty() && !all }
        if (!repo.git(root, "rev-parse", "--verify", "--quiet", "HEAD").ok && name == null) {
            return GitLog(if (all) null else head, head, base, 0, 0, emptyList(), false)
        }
        val tips = if (all) ALL else listOf(name?.let { ref(root, it) } ?: "HEAD")
        val shown = if (all) null else name ?: head ?: "HEAD"
        val baseRef = base?.let { "refs/heads/$it" }
        val onBase = !all && baseRef != null && (tips[0] == baseRef || (tips[0] == "HEAD" && head == base))
        val (behind, ahead) = if (!all && baseRef != null && !onBase) aheadBehind(root, baseRef, tips[0]) ?: (0 to 0) else 0 to 0
        // the commits the base does not have; without a base nothing is marked as "on the base"
        val notInBase: Set<String>? = when {
            baseRef == null -> null
            onBase -> emptySet()
            else -> repo.git(root, "rev-list", "--max-count=$NOT_IN_BASE_MAX", *tips.toTypedArray(), "--not", baseRef, timeout = 30)
                .takeIf { it.ok }?.out?.lines()?.filter { it.isNotBlank() }?.toSet() ?: emptySet()
        }
        val n = limit.coerceIn(1, LIMIT_MAX)
        val args = mutableListOf(
            "--literal-pathspecs", "-c", "core.quotePath=false", "log", "--date-order", "--parents", "--no-color", "--decorate=full",
            "--decorate-refs=HEAD", "--decorate-refs=refs/heads", "--decorate-refs=refs/remotes", "--decorate-refs=refs/tags",
            "--decorate-refs-exclude=refs/remotes/*/HEAD", "--format=$FORMAT",
        )
        val hash = q?.trim()?.takeIf { HASH.matches(it) }?.let { commitOn(root, it, tips, all) }
        if (hash != null) {
            args += listOf("--no-walk", hash)
        } else {
            args += listOf("--max-count=${n + 1}", "--skip=${skip.coerceIn(0, 1_000_000)}")
            val who = author?.trim().orEmpty()
            val text = q?.trim().orEmpty()
            if (who.isNotEmpty() || text.isNotEmpty()) args += listOf("--fixed-strings", "--regexp-ignore-case")
            if (who.isNotEmpty()) args += "--author=$who"
            if (text.isNotEmpty()) args += "--grep=$text"
            args += tips
            pathOf(root, path)?.let { args += listOf("--", it) }
        }
        val r = repo.git(root, *args.toTypedArray(), timeout = 30)
        if (!r.ok) throw BadRequest("git could not read the log", r.err.trim().take(300).ifBlank { null })
        val authors = repo.keelAuthors(root)
        val commits = r.out.split('\u001e').map { it.trimStart('\n') }.filter { it.isNotBlank() }.map { rec ->
            val p = rec.split('\u001f')
            val sha = p[0]
            val who = p.getOrElse(2) { "" }
            val email = p.getOrElse(3) { "" }
            LogCommit(
                sha = sha, parents = p.getOrElse(1) { "" }.split(' ').filter { it.isNotBlank() }, subject = p.getOrElse(6) { "" },
                author = who, email = email, at = p.getOrElse(4) { "" }, refs = refsOf(p.getOrElse(5) { "" }),
                inBase = notInBase != null && sha !in notInBase,
                keel = repo.isKeelAuthor(who, email, authors) || RepoService.KEELBOT_EMAIL in p.getOrElse(7) { "" },
            )
        }
        return GitLog(shown, head, base, ahead, behind, commits.take(n), commits.size > n)
    }

    /** The current branch, or null when HEAD is detached (or the folder is no repo). */
    private fun current(root: Path): String? = projects.branch(root)?.takeIf { it != "HEAD" }

    /** `git rev-list --left-right --count a...b` → (only in a, only in b). */
    private fun aheadBehind(root: Path, a: String, b: String): Pair<Int, Int>? =
        repo.git(root, "rev-list", "--left-right", "--count", "$a...$b").takeIf { it.ok }?.out?.trim()?.split(Regex("\\s+"))?.let {
            (it.getOrNull(0)?.toIntOrNull() ?: 0) to (it.getOrNull(1)?.toIntOrNull() ?: 0)
        }

    private fun forEachRef(root: Path, prefix: String, sort: String, max: Int): List<RefItem> {
        val fmt = listOf("%(refname)", "%(objectname)", "%(*objectname)", "%(creatordate:iso-strict)", "%(subject)", "%(upstream:short)", "%(symref)")
            .joinToString("\u001f")
        val out = repo.git(root, "for-each-ref", "--sort=$sort", "--count=$max", "--format=$fmt", prefix).takeIf { it.ok }?.out ?: return emptyList()
        return out.lines().filter { it.isNotBlank() }.mapNotNull { line ->
            val p = line.split('\u001f')
            if (p.getOrElse(6) { "" }.isNotBlank()) return@mapNotNull null   // a symbolic ref (origin/HEAD)
            RefItem(
                name = p[0].removePrefix(prefix), sha = p.getOrElse(2) { "" }.ifBlank { p.getOrElse(1) { "" } }, date = p.getOrElse(3) { "" },
                subject = p.getOrElse(4) { "" }, upstream = p.getOrElse(5) { "" }.ifBlank { null },
            )
        }
    }

    /** A branch, remote branch or tag by its name, as a full ref; never an option, a range or a revision expression. */
    private fun ref(root: Path, name: String): String {
        if (name == "HEAD") return "HEAD"
        if (name.startsWith("-") || name.startsWith("/") || name.endsWith("/") || name.contains("..") ||
            name.any { it.isISOControl() || it.isWhitespace() || it in "~^:?*[\\" }
        ) {
            throw BadRequest("That is not a branch or tag name")
        }
        return listOf("refs/heads/", "refs/remotes/", "refs/tags/").map { it + name }
            .firstOrNull { repo.git(root, "show-ref", "--verify", "--quiet", it).ok } ?: throw NotFound("No branch or tag $name")
    }

    /** A path inside the repo (a file or a folder, deleted ones too); blank or the root = no path filter. */
    private fun pathOf(root: Path, path: String?): String? {
        if (path.isNullOrBlank()) return null
        val target = repo.safePath(root, path.trim())
        return root.relativize(target).toString().replace('\\', '/').ifEmpty { null }
    }

    /** A commit id the person typed, when it names a commit the shown branch has (any commit for all branches). */
    private fun commitOn(root: Path, id: String, tips: List<String>, all: Boolean): String? {
        val sha = repo.git(root, "rev-parse", "--verify", "--quiet", "$id^{commit}").takeIf { it.ok }?.out?.trim()?.ifBlank { null } ?: return null
        if (all) return sha
        return sha.takeIf { repo.git(root, "merge-base", "--is-ancestor", it, tips[0]).ok }
    }

    private fun refsOf(d: String): List<LogRef> = d.split(", ").filter { it.isNotBlank() }.mapNotNull { part ->
        when {
            part == "HEAD" -> LogRef("HEAD", "head")
            part.startsWith("HEAD -> ") -> LogRef(part.removePrefix("HEAD -> ").removePrefix("refs/heads/"), "local", current = true)
            part.startsWith("tag: ") -> LogRef(part.removePrefix("tag: ").removePrefix("refs/tags/"), "tag")
            part.startsWith("refs/heads/") -> LogRef(part.removePrefix("refs/heads/"), "local")
            part.startsWith("refs/remotes/") -> LogRef(part.removePrefix("refs/remotes/"), "remote")
            else -> null
        }
    }

    companion object {
        /** sha, parents, author, e-mail, date, refs, subject, and the Co-Authored-By names; \u001e ends a commit. */
        const val FORMAT = "%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%D%x1f%s%x1f%(trailers:key=Co-Authored-By,valueonly,separator=%x20)%x1e"
        val ALL = listOf("--branches", "--remotes", "--tags", "HEAD")
        val HASH = Regex("^[0-9a-fA-F]{7,40}$")
        const val LIMIT_MAX = 1000
        const val NOT_IN_BASE_MAX = 5000
        /** Local branches that get ahead / behind counts (one git call each). */
        const val COUNTED = 60
    }
}

@RestController
@RequestMapping("/api/projects/{pid}/repo")
class RepoLogController(private val log: RepoLog) {

    /** v0.15.2 Code › Source control › Log: the branch list. */
    @GetMapping("/refs")
    fun refs(@PathVariable pid: String): RefsView = log.refs(pid)

    /** v0.15.2 The Git log: `branch` (blank = the current one) or `all`, filtered by author, text (or a commit id) and path. */
    @GetMapping("/log")
    fun log(
        @PathVariable pid: String,
        @RequestParam(required = false) branch: String?,
        @RequestParam(defaultValue = "false") all: Boolean,
        @RequestParam(required = false) author: String?,
        @RequestParam(required = false) q: String?,
        @RequestParam(required = false) path: String?,
        @RequestParam(defaultValue = "100") limit: Int,
        @RequestParam(defaultValue = "0") skip: Int,
    ): GitLog = log.log(pid, branch, all, author, q, path, limit, skip)
}
