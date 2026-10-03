package keel.api.repo

import com.fasterxml.jackson.annotation.JsonInclude
import keel.api.common.BadRequest
import keel.api.common.Forbidden
import keel.api.common.NotFound
import keel.api.common.Proc
import keel.api.common.ProcResult
import keel.api.projects.ProjectService
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

data class Commit(val sha: String, val message: String, val author: String, val at: String)

data class FileView(
    val path: String,
    val size: Long,
    val mark: String?,
    val frozen: Boolean,
    val keel: Boolean,
    val ac: String?,
    val head: String,
    val lastCommit: Commit?,
)

/** Read-only views of a project's git repo. Every git call runs with cwd = root and a timeout. */
@Service
class RepoService(private val projects: ProjectService, private val rules: KeelRules) {

    fun git(root: Path, vararg args: String, timeout: Long = 10): ProcResult = Proc.run(listOf("git", *args), root, timeout)

    private fun gitOut(root: Path, vararg args: String): String? = git(root, *args).takeIf { it.ok }?.out?.trim()

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

    fun base(root: Path): String? = listOf("main", "master").firstOrNull {
        git(root, "show-ref", "--verify", "--quiet", "refs/heads/$it").ok
    }

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

    private fun phase(root: Path): String = projects.keelState(root)?.get("phase")?.asText() ?: "none"

    fun isKeel(rel: String, cfg: ClassifyConfig): Boolean =
        rel == ".keel" || rel.startsWith(".keel/") ||
            KEEL_DIRS.any { rel == it || rel.startsWith("$it/") } ||
            (cfg.contractFile.isNotEmpty() && rel == cfg.contractFile) ||
            Regex("(^|/)openapi\\.(ya?ml|json)$").containsMatchIn(rel)

    fun tree(pid: String, depth: Int): List<TreeNode> {
        val root = projects.root(pid)
        val maxDepth = depth.coerceIn(1, 8)
        val cfg = ClassifyConfig.load(root)
        val phase = phase(root)
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
        walk(root, 1)
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
        val head = if (bytes.take(8000).any { it == 0.toByte() }) {
            "(binary file)"
        } else {
            String(bytes, StandardCharsets.UTF_8).lineSequence().take(HEAD_LINES).joinToString("\n")
        }
        return FileView(
            path = relNorm, size = Files.size(target), mark = marks(root)[relNorm],
            frozen = rules.frozen(phase(root), cfg, relNorm), keel = isKeel(relNorm, cfg), ac = null,
            head = head, lastCommit = commits(root, 1, relNorm).firstOrNull(),
        )
    }

    fun commits(pid: String, limit: Int): List<Commit> = commits(projects.root(pid), limit, null)

    fun commits(root: Path, limit: Int, path: String?): List<Commit> {
        val args = mutableListOf("log", "-n", limit.coerceIn(1, 500).toString(), "--format=%H\u001f%s\u001f%an\u001f%aI")
        if (path != null) args += listOf("--", path)
        val out = git(root, *args.toTypedArray()).takeIf { it.ok }?.out ?: return emptyList()
        return out.lines().filter { it.isNotBlank() }.map {
            val p = it.split('\u001f')
            Commit(p[0], p.getOrElse(1) { "" }, p.getOrElse(2) { "" }, p.getOrElse(3) { "" })
        }
    }

    /** Unix time of the last commit that touched code (not docs/ or .keel/), or null. */
    fun lastCodeCommit(root: Path): Long? =
        gitOut(root, "log", "-1", "--format=%ct", "--", ".", ":(exclude)docs", ":(exclude).keel")?.toLongOrNull()

    fun lastCommitTime(root: Path, rel: String): Long? = gitOut(root, "log", "-1", "--format=%ct", "--", rel)?.toLongOrNull()

    companion object {
        const val HEAD_LINES = 120
        const val MAX_NODES = 5000
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
