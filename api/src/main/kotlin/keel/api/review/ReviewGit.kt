package keel.api.review

import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.Proc
import keel.api.common.ProcResult
import keel.api.repo.FileDiff
import java.nio.file.Path

/**
 * The git side of a review, read only: where the code lives (origin), a pull request's head and base fetched into
 * refs/keel/review/, and what changed between two refs. Nothing here checks anything out, builds or runs code.
 */
object ReviewGit {
    const val MAX_DIFF = 800_000

    fun git(root: Path, vararg args: String, timeout: Long = 20, env: Map<String, String> = emptyMap()): ProcResult =
        Proc.run(listOf("git", "-c", "core.quotePath=false", *args), root, timeout, env)

    private fun out(root: Path, vararg args: String): String? = git(root, *args).takeIf { it.ok }?.out?.trim()

    /** origin as written in the config (not rewritten by insteadOf): https://host/a/b(.git), git@host:a/b.git, ssh://git@host/a/b. */
    fun identity(root: Path, gitlabHost: String? = null): HostRef? {
        val url = out(root, "config", "--get", "remote.origin.url") ?: return null
        val (host, path) = parseRemote(url) ?: return null
        val kind = when {
            host == "github.com" || host.startsWith("github.") -> "github"
            gitlabHost != null && host.equals(gitlabHost, true) -> "gitlab"
            host == "gitlab.com" || host.startsWith("gitlab.") -> "gitlab"
            else -> "github"            // GitHub Enterprise on its own domain; a GitLab server is named in Connections
        }
        return HostRef(kind, host, path, "https://$host/$path")
    }

    fun parseRemote(url: String): Pair<String, String>? {
        val u = url.trim().removeSuffix("/").removeSuffix(".git")
        val m = Regex("^(?:https?|ssh|git)://(?:[^@/]+@)?([^/:]+)(?::\\d+)?/(.+)$").matchEntire(u)
            ?: Regex("^(?:[^@/]+@)?([^/:]+):(.+)$").matchEntire(u)
            ?: return null
        val host = m.groupValues[1].lowercase()
        val path = m.groupValues[2].trim('/')
        if (!path.contains('/') || path.contains("..")) return null
        return host to path
    }

    fun branchName(name: String): String {
        if (name.isBlank() || name.startsWith("-") || name.contains("..") || name.any { it.isISOControl() || it == ' ' || it in "~^:?*[\\" }) {
            throw BadRequest("That is not a branch name: $name")
        }
        return name
    }

    fun headRef(number: Int) = "refs/keel/review/pr-$number"
    fun baseRef(number: Int) = "refs/keel/review/pr-$number-base"

    /** The pull request's head and its base branch, fetched over https with the token (only for that host). */
    fun fetchPr(root: Path, host: CodeHost, number: Int, base: String, timeoutSec: Long) {
        branchName(base)
        val url = host.fetchUrl()
        val scope = Regex("^(https?://[^/]+/)").find(url)?.groupValues?.get(1) ?: url
        val env = mapOf(
            "GIT_TERMINAL_PROMPT" to "0",
            "GIT_CONFIG_COUNT" to "1",
            "GIT_CONFIG_KEY_0" to "http.${scope}.extraheader",
            "GIT_CONFIG_VALUE_0" to host.authHeader(),
        )
        val r = git(root, "fetch", "--no-tags", "--quiet", url, "+${host.headRef(number)}:${headRef(number)}",
            "+refs/heads/$base:${baseRef(number)}", timeout = timeoutSec, env = env)
        if (!r.ok) {
            val why = (r.err.ifBlank { r.out }).lines().firstOrNull { it.isNotBlank() }.orEmpty().take(240)
            throw Conflict("keel could not fetch pull request #$number", why.ifBlank { "git fetch failed" })
        }
    }

    fun sha(root: Path, ref: String): String? = out(root, "rev-parse", "--verify", "--quiet", "$ref^{commit}")

    fun mergeBase(root: Path, a: String, b: String): String? = out(root, "merge-base", a, b)

    /** The files changed between base (its merge base with head) and head, with added and removed line counts. */
    fun files(root: Path, base: String, head: String, max: Int = 2000): List<ChangedFile> {
        val range = "$base...$head"
        val status = git(root, "diff", "--no-color", "--no-ext-diff", "-M", "--name-status", "-z", range)
        if (!status.ok) throw Conflict("git could not compare $base and $head", status.err.lines().firstOrNull().orEmpty())
        val stats = mutableMapOf<String, Pair<Int?, Int?>>()
        val num = git(root, "diff", "--no-color", "--no-ext-diff", "-M", "--numstat", "-z", range)
        if (num.ok) {
            val parts = num.out.split('\u0000')
            var i = 0
            while (i < parts.size) {
                val p = parts[i]
                if (p.isBlank()) { i++; continue }
                val cols = p.split('\t')
                val a = cols.getOrNull(0)?.toIntOrNull()
                val d = cols.getOrNull(1)?.toIntOrNull()
                if (cols.size >= 3 && cols[2].isNotEmpty()) {
                    stats[cols[2]] = a to d
                    i++
                } else {
                    // a rename: "a\td\t" then the old and the new path
                    stats[parts.getOrElse(i + 2) { "" }] = a to d
                    i += 3
                }
            }
        }
        val list = mutableListOf<ChangedFile>()
        val parts = status.out.split('\u0000')
        var i = 0
        while (i < parts.size && list.size < max) {
            val st = parts[i]
            if (st.isBlank()) { i++; continue }
            val code = st.take(1)
            val (path, from) = if (code == "R" || code == "C") {
                val p = parts.getOrElse(i + 2) { "" } to parts.getOrNull(i + 1)
                i += 3
                p
            } else {
                val p = parts.getOrElse(i + 1) { "" } to null
                i += 2
                p
            }
            val s = stats[path]
            list += ChangedFile(path, code, from, s?.first ?: 0, s?.second ?: 0, s != null && s.first == null)
        }
        return list
    }

    /** One file's unified diff between base (merge base) and head. */
    fun fileDiff(root: Path, base: String, head: String, file: ChangedFile, label: String): FileDiff {
        val paths = listOfNotNull(file.from, file.path).distinct().toTypedArray()
        val r = git(root, "--literal-pathspecs", "diff", "--no-color", "--no-ext-diff", "-M", "$base...$head", "--", *paths, timeout = 30)
        if (!r.ok) throw Conflict("git could not show the changes of ${file.path}", r.err.lines().firstOrNull().orEmpty())
        val text = r.out
        val binary = file.binary || text.lines().any { it.startsWith("Binary files ") }
        return FileDiff(file.path, "base", label, if (text.length > MAX_DIFF) text.take(MAX_DIFF) else text, binary, text.length > MAX_DIFF)
    }

    /** The lines a comment can sit on: new-side lines (RIGHT) and old-side lines (LEFT) inside the diff's hunks. */
    fun commentable(diff: String): Pair<Set<Int>, Set<Int>> {
        val right = mutableSetOf<Int>()
        val left = mutableSetOf<Int>()
        var old = 0
        var new = 0
        var inHunk = false
        for (line in diff.lines()) {
            val h = Regex("^@@ -(\\d+)(?:,\\d+)? \\+(\\d+)(?:,\\d+)? @@").find(line)
            when {
                h != null -> { old = h.groupValues[1].toInt(); new = h.groupValues[2].toInt(); inHunk = true }
                !inHunk -> {}
                line.startsWith("+") -> { right += new; new++ }
                line.startsWith("-") -> { left += old; old++ }
                line.startsWith(" ") -> { right += new; left += old; new++; old++ }
                line.startsWith("\\") -> {}
                else -> inHunk = false
            }
        }
        return right to left
    }
}
