package keel.api.repo

import com.fasterxml.jackson.annotation.JsonInclude
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Forbidden
import keel.api.common.NotFound
import keel.api.common.ProcResult
import keel.api.common.Yaml
import keel.api.projects.ProjectService
import keel.api.workspace.Workspace
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Service
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path

data class Worktree(val branch: String?, val path: String)
data class BranchInfo(val name: String, val note: String)
data class RepoInfo(
    val branch: String?,
    val base: String?,
    val ahead: Int,
    val behind: Int,
    val remote: String?,
    val worktrees: List<Worktree>,
    val branches: List<BranchInfo>,
)

@JsonInclude(JsonInclude.Include.NON_NULL)
data class TreeNode(
    val path: String,
    val name: String,
    val depth: Int,
    val kind: String,
    val mark: String? = null,
    val keel: Boolean,
    val frozen: Boolean,
    val ac: String? = null,
)

/** `keel`: written by keel's own commit step (author KeelBot or the configured keel author, or co-authored by KeelBot). */
data class Commit(val sha: String, val message: String, val author: String, val at: String, val keel: Boolean = false)

/**
 * One file. `ac` is the newest acceptance criterion named by a commit on this branch that touched it; `phase`,
 * `bucket` and `verdict` say which keel rule applies to it now (verdict `deny` = frozen).
 */
data class FileView(
    val path: String,
    val size: Long,
    val mark: String?,
    val frozen: Boolean,
    val keel: Boolean,
    val ac: String?,
    val head: String,
    val lastCommit: Commit?,
    val binary: Boolean = false,
    val modified: Long = 0,
    val phase: String = "none",
    val bucket: String = "other",
    val verdict: String = "allow",
)

/** One changed path from `git status`: what is staged (index), what is not (work tree), or untracked. */
@JsonInclude(JsonInclude.Include.NON_NULL)
data class Change(
    val path: String,
    val from: String? = null,
    val staged: String? = null,
    val unstaged: String? = null,
    val untracked: Boolean = false,
    val conflict: Boolean = false,
)

@JsonInclude(JsonInclude.Include.NON_NULL)
data class CommitFile(val path: String, val status: String, val from: String? = null)
data class CommitView(val sha: String, val message: String, val body: String, val author: String, val at: String, val keel: Boolean, val files: List<CommitFile>)

/**
 * One local branch against the base (Code › Source control › a branch, with the Git plugin): the commits it has that the
 * base does not, and the files it changed since it left the base (`base...branch`, so the base's own new commits do not
 * show). `current` = the project folder is on it.
 */
data class BranchView(
    val name: String,
    val base: String?,
    val current: Boolean,
    val ahead: Int,
    val behind: Int,
    val commits: List<Commit>,
    val files: List<CommitFile>,
    val truncated: Boolean = false,
)

/** A unified diff of one file. `ref` is what it is compared with, in words. */
data class FileDiff(val path: String, val against: String, val ref: String, val diff: String, val binary: Boolean, val truncated: Boolean)

data class MergeResult(val ok: Boolean, val merged: Boolean, val conflicts: List<String>, val output: String)

/**
 * Views of a project's git repo, plus "update from base". Every git call goes through the core [Workspace] (cwd = root,
 * with a timeout).
 */
@Service
class RepoService(private val projects: ProjectService, private val rules: KeelRules, private val workspace: Workspace) {

    fun git(root: Path, vararg args: String, timeout: Long = 10): ProcResult = workspace.git(root, *args, timeout = timeout)

    private fun gitOut(root: Path, vararg args: String): String? = workspace.gitOut(root, *args)

    fun info(pid: String): RepoInfo {
        val root = projects.root(pid)
        if (!Files.exists(root.resolve(".git"))) return RepoInfo(null, null, 0, 0, null, emptyList(), emptyList())
        val branch = projects.branch(root)
        val base = base(root) ?: branch
        var ahead = 0
        var behind = 0
        if (base != null && branch != null && base != branch) {
            gitOut(root, "rev-list", "--left-right", "--count", "$base...HEAD")?.split(Regex("\\s+"))?.let {
                behind = it.getOrNull(0)?.toIntOrNull() ?: 0
                ahead = it.getOrNull(1)?.toIntOrNull() ?: 0
            }
        }
        val remote = gitOut(root, "remote", "get-url", "origin")?.ifBlank { null }
            ?: gitOut(root, "remote")?.lines()?.firstOrNull()?.takeIf { it.isNotBlank() }?.let { gitOut(root, "remote", "get-url", it) }
        return RepoInfo(branch, base, ahead, behind, remote?.let(::redactRemote), worktrees(root), branches(root, branch, base))
    }

    /** Strips credentials from https remotes (`https://user:token@host/...`). */
    private fun redactRemote(url: String) = url.replace(Regex("(https?://)[^@/]+@"), "$1")

    fun base(root: Path): String? = workspace.base(root)

    private fun worktrees(root: Path): List<Worktree> {
        val out = gitOut(root, "worktree", "list", "--porcelain") ?: return emptyList()
        val list = mutableListOf<Worktree>()
        var path: String? = null
        var branch: String? = null
        for (line in out.lines() + "") {
            when {
                line.startsWith("worktree ") -> path = line.removePrefix("worktree ")
                line.startsWith("branch ") -> branch = line.removePrefix("branch ").removePrefix("refs/heads/")
                line.isBlank() -> {
                    if (path != null) list += Worktree(branch, path)
                    path = null; branch = null
                }
            }
        }
        return list
    }

    private fun branches(root: Path, current: String?, base: String?): List<BranchInfo> {
        val out = gitOut(root, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)\u001f%(subject)", "refs/heads") ?: return emptyList()
        return out.lines().filter { it.isNotBlank() }.take(50).map { line ->
            val (name, subject) = line.split('\u001f').let { it[0] to it.getOrElse(1) { "" } }
            val note = when (name) {
                current -> "current · $subject"
                base -> "base · $subject"
                else -> subject
            }
            BranchInfo(name, note)
        }
    }

    /** `git status --porcelain` → path → A | M | D. Untracked counts as A. */
    fun marks(root: Path): Map<String, String> {
        val out = git(root, "status", "--porcelain", "--untracked-files=all").takeIf { it.ok }?.out ?: return emptyMap()
        val map = mutableMapOf<String, String>()
        for (line in out.lines()) {
            if (line.length < 4) continue
            val xy = line.substring(0, 2)
            var path = line.substring(3)
            if (path.contains(" -> ")) path = path.substringAfter(" -> ")
            path = path.trim('"')
            map[path] = when {
                xy == "??" || xy.contains('A') -> "A"
                xy.contains('D') -> "D"
                else -> "M"
            }
        }
        return map
    }

    fun isKeel(rel: String, cfg: ClassifyConfig): Boolean =
        rel == ".keel" || rel.startsWith(".keel/") ||
            KEEL_DIRS.any { rel == it || rel.startsWith("$it/") } ||
            (cfg.contractFile.isNotEmpty() && rel == cfg.contractFile) ||
            Regex("(^|/)openapi\\.(ya?ml|json)$").containsMatchIn(rel)

    /** The tree from the root, or (lazy loading) from `dir`, `depth` levels down. Depth 1 = the root's children. */
    fun tree(pid: String, depth: Int, dir: String? = null): List<TreeNode> {
        val root = projects.root(pid)
        val start = if (dir.isNullOrBlank()) root else safePath(root, dir)
        if (!Files.isDirectory(start)) throw NotFound("No folder at $dir")
        val startDepth = if (start == root) 0 else root.relativize(start).nameCount
        val maxDepth = startDepth + depth.coerceIn(1, 8)
        val cfg = ClassifyConfig.load(root)
        val phase = projects.activePhase(pid)
        val marks = marks(root)
        val out = mutableListOf<TreeNode>()

        fun walk(dir: Path, d: Int) {
            if (out.size >= MAX_NODES) return
            val children = runCatching { Files.list(dir).use { s -> s.toList() } }.getOrDefault(emptyList())
                .filter { it.fileName.toString() !in SKIP_DIRS || !Files.isDirectory(it) }
                .sortedWith(compareBy<Path>({ !Files.isDirectory(it) }, { it.fileName.toString().lowercase() }))
            for (child in children) {
                if (out.size >= MAX_NODES) return
                val rel = root.relativize(child).toString().replace('\\', '/')
                val isDir = Files.isDirectory(child)
                val mark = if (isDir) null else marks[rel]
                out += TreeNode(
                    path = rel, name = child.fileName.toString(), depth = d, kind = if (isDir) "dir" else "file",
                    mark = mark, keel = isKeel(rel, cfg), frozen = !isDir && rules.frozen(phase, cfg, rel),
                )
                // Do not walk into links, nested repos or worktrees: they are other projects.
                val nested = isDir && Files.exists(child.resolve(".git"))
                if (isDir && d < maxDepth && !Files.isSymbolicLink(child) && !nested) walk(child, d + 1)
            }
        }
        walk(start, startDepth + 1)
        return out
    }

    /** Resolves a user path inside the root. Refuses anything outside it, `.git/`, and secret files. */
    fun safePath(root: Path, rel: String): Path {
        if (rel.isBlank()) throw BadRequest("path is empty")
        if (rel.contains('\u0000')) throw BadRequest("path is not valid")
        val target = root.resolve(rel).normalize()
        val realRoot = root.toRealPath()
        if (!target.startsWith(root) || Path.of(rel).isAbsolute) {
            throw Forbidden("That path is outside the project", "Use a path relative to the repo root.")
        }
        if (Files.exists(target) && !target.toRealPath().startsWith(realRoot)) {
            throw Forbidden("That path is outside the project", "It is a link to a file outside the repo.")
        }
        val relNorm = root.relativize(target).toString().replace('\\', '/')
        if (relNorm == ".git" || relNorm.startsWith(".git/") || relNorm.contains("/.git/")) {
            throw Forbidden("keel does not show files inside .git")
        }
        if (isSecret(target.fileName?.toString() ?: "")) {
            throw Forbidden("keel does not show secret files", "Files like .env, keys and certificates stay private.")
        }
        return target
    }

    fun file(pid: String, rel: String): FileView {
        val root = projects.root(pid)
        val target = safePath(root, rel)
        if (!Files.isRegularFile(target)) throw NotFound("No file at $rel")
        val relNorm = root.relativize(target).toString().replace('\\', '/')
        val cfg = ClassifyConfig.load(root)
        val bytes = Files.newInputStream(target).use { it.readNBytes(256 * 1024) }
        val binary = isBinary(bytes)
        val head = if (binary) {
            "(binary file)"
        } else {
            String(bytes, StandardCharsets.UTF_8).lineSequence().take(HEAD_LINES).joinToString("\n")
        }
        val phase = projects.activePhase(pid)
        val bucket = KeelRules.classify(cfg, relNorm)
        val verdict = rules.verdict(phase, bucket)
        return FileView(
            path = relNorm, size = Files.size(target), mark = marks(root)[relNorm],
            frozen = verdict == "deny", keel = isKeel(relNorm, cfg), ac = acFor(root, relNorm),
            head = head, lastCommit = commits(root, 1, relNorm).firstOrNull(),
            binary = binary, modified = Files.getLastModifiedTime(target).toMillis(),
            phase = phase, bucket = bucket, verdict = verdict,
        )
    }

    /** The newest AC id (AC-001) in the subjects of this branch's commits that touched the file, or null. */
    private fun acFor(root: Path, rel: String): String? {
        val branch = projects.branch(root)
        val base = base(root)
        val range = if (base != null && branch != null && base != branch) "$base..HEAD" else "HEAD"
        val out = gitOut(root, "log", "-n", "50", "--format=%s", range, "--", rel) ?: return null
        return out.lineSequence().mapNotNull { AC_ID.find(it)?.value }.firstOrNull()
    }

    /**
     * The whole file as bytes, for the editor and for images. Refused above [RAW_MAX]. The caller serves it with
     * a sandbox CSP so an SVG or HTML file can never run as a page of keel.
     */
    fun raw(pid: String, rel: String): Pair<String, ByteArray> {
        val root = projects.root(pid)
        val target = safePath(root, rel)
        if (!Files.isRegularFile(target)) throw NotFound("No file at $rel")
        val size = Files.size(target)
        if (size > RAW_MAX) {
            throw ApiException(HttpStatus.valueOf(413), "The file is too big to show (${size / (1024 * 1024)} MB)", "keel shows files up to ${RAW_MAX / (1024 * 1024)} MB.")
        }
        return root.relativize(target).toString().replace('\\', '/') to Files.readAllBytes(target)
    }

    /** `git status --porcelain -z`: staged, unstaged and untracked paths (a path can be staged and changed again). */
    fun changes(pid: String): List<Change> {
        val root = projects.root(pid)
        val r = git(root, "-c", "core.quotePath=false", "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if (!r.ok) return emptyList()
        val parts = r.out.split('\u0000')
        val list = mutableListOf<Change>()
        var i = 0
        while (i < parts.size) {
            val e = parts[i]
            i++
            if (e.length < 4) continue
            val x = e[0]
            val y = e[1]
            val path = e.substring(3)
            var from: String? = null
            if (x == 'R' || x == 'C') { from = parts.getOrNull(i); i++ }
            val xy = "$x$y"
            list += when {
                xy == "??" -> Change(path, untracked = true)
                xy == "!!" -> continue
                x == 'U' || y == 'U' || xy == "AA" || xy == "DD" -> Change(path, from, conflict = true)
                else -> Change(path, from, staged = x.takeIf { it != ' ' }?.toString(), unstaged = y.takeIf { it != ' ' }?.toString())
            }
        }
        return list.sortedBy { it.path }
    }

    /**
     * The diff of one file: `against` head (work tree vs HEAD, staged and not), base (work tree vs the merge-base
     * with main/master: everything this branch changed), the change one commit (`sha`) made, or what another local
     * `branch` changed since it left the base (`base...branch`; the work tree plays no part).
     */
    fun diff(pid: String, rel: String, against: String, sha: String?, branch: String? = null): FileDiff {
        val root = projects.root(pid)
        val target = safePath(root, rel)
        val relNorm = root.relativize(target).toString().replace('\\', '/')
        // one file only: a folder (or ".") would diff every file in it, secret ones too
        if (Files.isDirectory(target) || relNorm.isEmpty()) throw BadRequest("That is a folder", "Pick a file to see its changes.")
        // the path is a name, never a pathspec (":(glob)**" must not match every file)
        val common = arrayOf("--literal-pathspecs", "-c", "core.quotePath=false")
        val opts = arrayOf("--no-color", "--no-ext-diff", "-M")
        val untracked = git(root, "--literal-pathspecs", "ls-files", "--error-unmatch", "--", relNorm).ok.not() && Files.isRegularFile(target)
        val (ref, r) = when {
            sha != null -> {
                val s = checkSha(sha)
                val first = firstParentOfMerge(root, s)
                "commit ${s.take(7)}" to if (first != null) git(root, *common, "diff", *opts, first, s, "--", relNorm, timeout = 20)
                else git(root, *common, "show", "--format=", *opts, s, "--", relNorm, timeout = 20)
            }
            branch != null -> {
                val b = localBranch(root, branch)
                val base = base(root) ?: throw BadRequest("No base branch found", "keel looks for main or master.")
                "$base…$b" to git(root, *common, "diff", *opts, "refs/heads/$base...refs/heads/$b", "--", relNorm, timeout = 20)
            }
            untracked -> "nothing (a new file)" to git(root, *common, "diff", "--no-index", *opts, "--", "/dev/null", relNorm, timeout = 20)
            against == "base" -> {
                val branch = projects.branch(root)
                val base = base(root) ?: throw BadRequest("No base branch found", "keel looks for main or master.")
                val mb = gitOut(root, "merge-base", base, "HEAD") ?: throw BadRequest("This branch has no common commit with $base")
                val name = if (branch == base) "HEAD" else base
                "$name (${mb.take(7)})" to git(root, *common, "diff", *opts, mb, "--", relNorm, timeout = 20)
            }
            against == "head" -> {
                if (!git(root, "rev-parse", "--verify", "--quiet", "HEAD").ok) throw BadRequest("The repo has no commit yet")
                "HEAD" to git(root, *common, "diff", *opts, "HEAD", "--", relNorm, timeout = 20)
            }
            else -> throw BadRequest("against is head or base")
        }
        // git diff --no-index exits 1 when the files differ
        if (!r.ok && !(untracked && r.code == 1 && !r.timedOut)) {
            throw BadRequest("git could not diff $relNorm", r.err.trim().take(300).ifBlank { null })
        }
        val truncated = r.out.length > DIFF_MAX
        val text = if (truncated) r.out.take(DIFF_MAX).substringBeforeLast('\n') else r.out
        val binary = Regex("^Binary files .* differ$", RegexOption.MULTILINE).containsMatchIn(text.take(4000))
        return FileDiff(relNorm, if (sha != null) "commit" else if (branch != null) "branch" else against, ref, text, binary, truncated)
    }

    /** One commit: subject, body, author and the files it changed (`git diff-tree`, renames detected). */
    fun commit(pid: String, sha: String): CommitView {
        val root = projects.root(pid)
        val s = checkSha(sha)
        val head = git(root, "show", "-s", "--format=%H%x1f%s%x1f%an%x1f%aI%x1f%ae%x1f%b", s)
        if (!head.ok) throw NotFound("No commit $sha")
        val p = head.out.trimEnd().split('\u001f')
        val authors = keelAuthors(root)
        val first = firstParentOfMerge(root, p[0])
        val list = if (first != null) nameStatus(git(root, "-c", "core.quotePath=false", "diff", "--name-status", "-M", "-z", first, p[0]), 1000)
        else nameStatus(git(root, "-c", "core.quotePath=false", "diff-tree", "--no-commit-id", "-r", "-M", "--root", "--name-status", "-z", s), 1000)
        return CommitView(
            sha = p[0], message = p.getOrElse(1) { "" }, body = p.getOrElse(5) { "" }.trim(), author = p.getOrElse(2) { "" },
            at = p.getOrElse(3) { "" }, keel = isKeelAuthor(p.getOrElse(2) { "" }, p.getOrElse(4) { "" }, authors) ||
                KEELBOT_EMAIL in p.getOrElse(5) { "" }, files = list,
        )
    }

    /** v0.15.2 A merge's first parent (its files and diffs are what it brought into that branch, like IDEs show them); null for any other commit. */
    private fun firstParentOfMerge(root: Path, sha: String): String? =
        gitOut(root, "show", "-s", "--format=%P", sha)?.split(' ')?.filter { it.isNotBlank() }?.takeIf { it.size > 1 }?.first()

    /** `--name-status -z` output (diff-tree or diff): status letter, path, and the old path of a rename or copy. */
    private fun nameStatus(r: ProcResult, max: Int): List<CommitFile> {
        val files = r.takeIf { it.ok }?.out?.split('\u0000').orEmpty()
        val list = mutableListOf<CommitFile>()
        var i = 0
        while (i < files.size && list.size < max) {
            val st = files[i]
            if (st.isBlank()) { i++; continue }
            val code = st.take(1)
            if (code == "R" || code == "C") {
                list += CommitFile(files.getOrElse(i + 2) { "" }, code, files.getOrNull(i + 1))
                i += 3
            } else {
                list += CommitFile(files.getOrElse(i + 1) { "" }, code)
                i += 2
            }
        }
        return list
    }

    /** A local branch by its name, or why not: never an option, a range or another ref (`refs/heads/<name>` must exist). */
    private fun localBranch(root: Path, name: String): String {
        if (name.isBlank() || name.startsWith("-") || name.contains("..") || name.any { it.isISOControl() || it == ' ' }) {
            throw BadRequest("That is not a branch name")
        }
        if (!git(root, "show-ref", "--verify", "--quiet", "refs/heads/$name").ok) throw NotFound("No branch $name")
        return name
    }

    /** One local branch against the base: its own commits (newest first, at most 100) and the files it changed. */
    fun branch(pid: String, name: String): BranchView {
        val root = projects.root(pid)
        val b = localBranch(root, name)
        val current = projects.branch(root) == b
        val base = base(root)
        if (base == null || base == b) return BranchView(b, base, current, 0, 0, commits(root, 30, null, "refs/heads/$b"), emptyList())
        var ahead = 0
        var behind = 0
        gitOut(root, "rev-list", "--left-right", "--count", "refs/heads/$base...refs/heads/$b")?.split(Regex("\\s+"))?.let {
            behind = it.getOrNull(0)?.toIntOrNull() ?: 0
            ahead = it.getOrNull(1)?.toIntOrNull() ?: 0
        }
        val files = nameStatus(git(root, "-c", "core.quotePath=false", "diff", "--name-status", "-M", "-z", "refs/heads/$base...refs/heads/$b"), 1001)
        return BranchView(b, base, current, ahead, behind, commits(root, 100, null, "refs/heads/$base..refs/heads/$b"),
            files.take(1000), files.size > 1000)
    }

    private fun checkSha(sha: String): String {
        if (!Regex("^[0-9a-fA-F]{4,64}$").matches(sha)) throw BadRequest("That is not a commit id")
        return sha
    }

    /** The names and e-mails keel commits as by itself: KeelBot, or `.keel/config.yml` commit.author_name / author_email.
     *  A commit by the person is keel's when it ends with the KeelBot co-author line. */
    fun keelAuthors(root: Path): Set<String> {
        val f = root.resolve(".keel/config.yml")
        val cfg = if (Files.isRegularFile(f)) Yaml.readMap(runCatching { Files.readString(f) }.getOrDefault("")) else null
        val c = cfg?.get("commit") as? Map<*, *>
        return setOfNotNull("keelbot", "KeelBot", KEELBOT_EMAIL, c?.get("author_name")?.toString(), c?.get("author_email")?.toString())
            .filter { it.isNotBlank() }.toSet()
    }

    fun isKeelAuthor(name: String, email: String, authors: Set<String>) = name in authors || email in authors

    /** `range` branch = only the commits this branch has that the base does not (`base..HEAD`). */
    fun commits(pid: String, limit: Int, range: String? = null): List<Commit> {
        val root = projects.root(pid)
        if (range == "branch") {
            val branch = projects.branch(root)
            val base = base(root)
            if (base != null && branch != null && base != branch) return commits(root, limit, null, "$base..HEAD")
        }
        return commits(root, limit, null)
    }

    fun commits(root: Path, limit: Int, path: String?, rev: String? = null): List<Commit> {
        val args = mutableListOf("log", "-n", limit.coerceIn(1, 500).toString(), "--format=$LOG_FORMAT")
        if (rev != null) args += rev
        if (path != null) args += listOf("--", path)
        val out = git(root, *args.toTypedArray()).takeIf { it.ok }?.out ?: return emptyList()
        return parseLog(root, out)
    }

    private fun parseLog(root: Path, out: String): List<Commit> {
        val authors = keelAuthors(root)
        return out.lines().filter { it.isNotBlank() }.map {
            val p = it.split('\u001f')
            Commit(p[0], p.getOrElse(1) { "" }, p.getOrElse(2) { "" }, p.getOrElse(3) { "" },
                isKeelAuthor(p.getOrElse(2) { "" }, p.getOrElse(4) { "" }, authors) || KEELBOT_EMAIL in p.getOrElse(5) { "" })
        }
    }

    /** `git log --follow` for one path (the file can be deleted; it still has a history). */
    fun history(pid: String, rel: String): List<Commit> {
        val root = projects.root(pid)
        val target = safePath(root, rel)
        val relNorm = root.relativize(target).toString().replace('\\', '/')
        val out = git(root, "--literal-pathspecs", "log", "--follow", "-n", "30", "--format=$LOG_FORMAT", "--", relNorm).takeIf { it.ok }?.out
            ?: return emptyList()
        return parseLog(root, out)
    }

    /**
     * `git merge <base>` into the current branch. On a conflict the merge is aborted, so the
     * work tree is left as it was, and the conflicting files are returned.
     */
    @Synchronized
    fun updateFromBase(pid: String): MergeResult {
        val root = projects.root(pid)
        if (!Files.exists(root.resolve(".git"))) throw BadRequest("This project is not a git repo")
        val branch = projects.branch(root) ?: throw BadRequest("The repo has no current branch", "Check out a branch first.")
        val base = base(root) ?: throw BadRequest("No base branch found", "keel looks for main or master.")
        if (branch == base) throw BadRequest("You are on the base branch ($base)", "Switch to a feature branch to bring $base into it.")
        // A merge commit needs a name; containers often have none configured.
        val ident = if (gitOut(root, "config", "user.email").isNullOrBlank()) {
            listOf("-c", "user.name=keel", "-c", "user.email=keel@localhost")
        } else emptyList()
        val r = git(root, *(ident + listOf("merge", "--no-edit", base)).toTypedArray(), timeout = 60)
        val output = (r.out + r.err).trim().take(4000)
        if (r.ok) {
            val merged = !output.contains("Already up to date", ignoreCase = true)
            return MergeResult(true, merged, emptyList(), output)
        }
        val conflicts = gitOut(root, "diff", "--name-only", "--diff-filter=U")?.lines()?.filter { it.isNotBlank() }.orEmpty()
        if (conflicts.isNotEmpty() || Files.exists(root.resolve(".git/MERGE_HEAD"))) {
            git(root, "merge", "--abort", timeout = 30)
        }
        return MergeResult(false, false, conflicts, output)
    }

    companion object {
        /** KeelBot's e-mail: keel's own commits are by it, or name it in their Co-Authored-By line. */
        const val KEELBOT_EMAIL = "keel.dev.bot@gmail.com"
        /** sha, subject, author, date, e-mail, and the Co-Authored-By names (parseLog). */
        const val LOG_FORMAT = "%H\u001f%s\u001f%an\u001f%aI\u001f%ae\u001f%(trailers:key=Co-Authored-By,valueonly,separator=%x20)"
        const val HEAD_LINES = 120
        const val MAX_NODES = 5000
        /** The editor's limit: bigger files say "too big to show". */
        const val RAW_MAX = 10L * 1024 * 1024
        const val DIFF_MAX = 2 * 1024 * 1024
        val AC_ID = Regex("\\bAC-\\d+\\b")

        /** A NUL byte in the first 8000 bytes: git's own test for binary. */
        fun isBinary(bytes: ByteArray): Boolean = bytes.take(8000).any { it == 0.toByte() }
        val SKIP_DIRS = setOf(".git", "node_modules", "build", ".venv", ".gradle", "dist", "target", "__pycache__", ".idea", ".pytest_cache")
        val KEEL_DIRS = listOf("docs/specs", "docs/knowledge", "docs/adr")

        fun isSecret(name: String): Boolean {
            val n = name.lowercase()
            if (n == ".env.example" || n == ".env.sample" || n == ".env.template") return false
            return n == ".env" || n.startsWith(".env.") || n.endsWith(".pem") || n.endsWith(".key") ||
                n.endsWith(".p12") || n.endsWith(".pfx") || n.endsWith(".keystore") || n.endsWith(".jks") ||
                n.startsWith("id_rsa") || n.startsWith("id_ed25519") || n.startsWith("id_ecdsa") ||
                n == ".npmrc" || n == ".pypirc" || n == ".netrc" || n == "credentials" || n == "credentials.json" ||
                n == "master.key" || n == "secrets.yml" || n == "secrets.yaml"
        }
    }
}
