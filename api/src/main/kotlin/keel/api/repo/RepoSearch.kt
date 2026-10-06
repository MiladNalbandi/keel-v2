package keel.api.repo

import keel.api.common.BadRequest
import keel.api.common.Forbidden
import keel.api.projects.ProjectService
import org.springframework.stereotype.Service
import java.io.File
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.regex.Pattern
import java.util.regex.PatternSyntaxException
import kotlin.io.path.isDirectory
import kotlin.io.path.isSymbolicLink

/** One matching line: `ranges` are [start, end) offsets of the matches in `text` (UTF-16, like JavaScript). */
data class SearchMatch(val line: Int, val column: Int, val length: Int, val text: String, val ranges: List<List<Int>>)
data class SearchFile(val path: String, val matches: List<SearchMatch>)
data class SearchResult(
    val results: List<SearchFile>,
    val matches: Int,
    val files: Int,
    val truncated: Boolean,
    val timedOut: Boolean,
    val tookMs: Long,
)
data class FileList(val files: List<String>, val truncated: Boolean)

/**
 * Search across files with `git grep` (tracked and untracked files, .gitignore respected, binary files skipped) and
 * the list of files for quick open (`git ls-files`). Read-only; every git call has a timeout.
 */
@Service
class RepoSearch(private val projects: ProjectService) {
    private val fileCache = ConcurrentHashMap<String, Pair<Long, FileList>>()

    @Volatile private var pcre: Boolean? = null

    fun search(
        pid: String, q: String, regex: Boolean, matchCase: Boolean, word: Boolean,
        include: String?, exclude: String?, max: Int,
    ): SearchResult {
        if (q.isEmpty()) throw BadRequest("Type something to search for")
        if (q.length > 500) throw BadRequest("The search text is too long", "Use at most 500 characters.")
        if (q.contains('\u0000') || q.contains('\n') || q.contains('\r')) throw BadRequest("Search one line at a time")
        val root = projects.root(pid)
        val cap = max.coerceIn(1, MAX_RESULTS)
        val pattern = javaPattern(q, regex, matchCase, word)
        val specs = pathspecs(include, false).ifEmpty { listOf(".") } + pathspecs(exclude, true) + DEFAULT_EXCLUDES
        val started = System.currentTimeMillis()
        val inRepo = Proc0.ok(root, "rev-parse", "--is-inside-work-tree")

        fun args(engine: String): List<String> {
            val a = mutableListOf("git", "-c", "core.quotePath=false", "grep", "-n", "--column", "-I", "--null", "--no-color")
            a += if (inRepo) "--untracked" else "--no-index"
            if (!regex) a += "-F" else a += engine
            if (!matchCase) a += "-i"
            if (word) a += "-w"
            a += listOf("-e", q, "--")
            a += specs
            return a
        }

        var run = grep(root, args(if (pcre != false) "-P" else "-E"), cap, pattern)
        if (regex && pcre != false && run.failed && run.err.contains(Regex("PCRE|Perl|perl", RegexOption.IGNORE_CASE))) {
            pcre = false
            run = grep(root, args("-E"), cap, pattern)
        } else if (regex && pcre == null && !run.failed) {
            pcre = true
        }
        if (run.failed) {
            val why = run.err.lineSequence().firstOrNull { it.isNotBlank() }?.removePrefix("fatal: ")?.take(300)
            throw BadRequest("git grep could not run this search", why)
        }
        val results = run.byFile.map { (path, list) -> SearchFile(path, list) }
        return SearchResult(results, run.count, results.size, run.truncated, run.timedOut, System.currentTimeMillis() - started)
    }

    private class Run(
        val byFile: LinkedHashMap<String, MutableList<SearchMatch>>, val count: Int, val truncated: Boolean,
        val timedOut: Boolean, val failed: Boolean, val err: String,
    )

    /** Streams git grep's output and stops (kills git) at `cap` matches or after the timeout. */
    private fun grep(root: Path, cmd: List<String>, cap: Int, pattern: Pattern): Run {
        val errFile = File.createTempFile("keel-grep", ".err")
        try {
            val pb = ProcessBuilder(cmd).directory(root.toFile())
            pb.environment()["GIT_TERMINAL_PROMPT"] = "0"
            pb.redirectError(errFile).redirectInput(ProcessBuilder.Redirect.from(File("/dev/null")))
            val p = pb.start()
            val timedOut = AtomicBoolean(false)
            Thread {
                if (!p.waitFor(TIMEOUT_MS, TimeUnit.MILLISECONDS)) {
                    timedOut.set(true)
                    p.destroyForcibly()
                }
            }.apply { isDaemon = true; start() }
            val byFile = LinkedHashMap<String, MutableList<SearchMatch>>()
            var count = 0
            var truncated = false
            p.inputStream.bufferedReader(Charsets.UTF_8).use { r ->
                while (true) {
                    val line = r.readLine() ?: break
                    val parts = line.split('\u0000', limit = 4)
                    if (parts.size < 4) continue
                    val path = parts[0].removePrefix("./")
                    val ln = parts[1].toIntOrNull() ?: continue
                    if (RepoService.isSecret(path.substringAfterLast('/'))) continue
                    if (count >= cap) {
                        truncated = true
                        break
                    }
                    byFile.getOrPut(path) { mutableListOf() } += match(ln, parts[3], pattern)
                    count++
                }
            }
            if (truncated) p.destroyForcibly()
            p.waitFor(5, TimeUnit.SECONDS)
            val code = if (p.isAlive) -1 else p.exitValue()
            // 0 = found, 1 = nothing found; anything else is an error unless we stopped git ourselves
            val failed = !truncated && !timedOut.get() && code != 0 && code != 1
            return Run(byFile, count, truncated, timedOut.get(), failed, runCatching { errFile.readText() }.getOrDefault(""))
        } finally {
            errFile.delete()
        }
    }

    /** The line, cut to a window around the first match when it is long, with the match ranges in it. */
    private fun match(line: Int, text: String, pattern: Pattern): SearchMatch {
        val all = mutableListOf<IntArray>()
        val m = pattern.matcher(text.take(MAX_SCAN))
        while (all.size < 50 && m.find()) {
            if (m.end() > m.start()) all += intArrayOf(m.start(), m.end())
            if (m.end() == m.start() && m.end() >= text.length) break
        }
        val first = all.firstOrNull()
        val from = if (first != null && first[0] > PREVIEW - 80) first[0] - 40 else 0
        val cut = text.substring(from, minOf(text.length, from + PREVIEW))
        val prefix = if (from > 0) "…" else ""
        val shift = prefix.length - from
        val ranges = all.filter { it[0] >= from && it[1] <= from + PREVIEW }.map { listOf(it[0] + shift, it[1] + shift) }
        return SearchMatch(line, (first?.get(0) ?: 0) + 1, first?.let { it[1] - it[0] } ?: 0, prefix + cut, ranges)
    }

    /** Every file quick open can open: tracked and untracked (not ignored), no deleted or secret files. Cached a few seconds. */
    fun files(pid: String): FileList {
        val root = projects.root(pid)
        val key = root.toString()
        fileCache[key]?.let { (at, list) -> if (System.currentTimeMillis() - at < CACHE_MS) return list }
        val listed = Proc0.run(root, 20, "-c", "core.quotePath=false", "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        val paths = if (listed != null) {
            val deleted = Proc0.run(root, 20, "-c", "core.quotePath=false", "ls-files", "-z", "--deleted")?.split('\u0000')?.toSet().orEmpty()
            listed.split('\u0000').asSequence().filter { it.isNotEmpty() && !it.endsWith("/") && it !in deleted }.distinct()
        } else {
            walk(root)
        }
        val kept = paths.filter { p -> !RepoService.isSecret(p.substringAfterLast('/')) && p.split('/').none { it in RepoService.SKIP_DIRS } }
            .take(MAX_FILES + 1).toList()
        val list = FileList(kept.take(MAX_FILES), kept.size > MAX_FILES)
        fileCache[key] = System.currentTimeMillis() to list
        return list
    }

    /** Not a git repo: the files on disk, without the folders the tree skips, links or nested repos. */
    private fun walk(root: Path): Sequence<String> {
        val out = mutableListOf<String>()
        fun go(dir: Path, depth: Int) {
            if (out.size > MAX_FILES || depth > 12) return
            val children = runCatching { Files.list(dir).use { it.toList() } }.getOrDefault(emptyList()).sortedBy { it.fileName.toString() }
            for (c in children) {
                val name = c.fileName.toString()
                if (c.isSymbolicLink()) continue
                if (c.isDirectory()) {
                    if (name !in RepoService.SKIP_DIRS && !Files.exists(c.resolve(".git"))) go(c, depth + 1)
                } else {
                    out += root.relativize(c).toString().replace('\\', '/')
                }
            }
        }
        go(root, 1)
        return out.asSequence()
    }

    companion object {
        const val MAX_RESULTS = 5000
        const val MAX_FILES = 50_000
        const val TIMEOUT_MS = 10_000L
        const val CACHE_MS = 5_000L
        const val PREVIEW = 300
        const val MAX_SCAN = 20_000
        private val DEFAULT_EXCLUDES = RepoService.SKIP_DIRS.filter { it != ".git" }.map { ":(exclude,glob)**/$it/**" }

        /** The same search as a Java pattern, to find the match ranges in each line (or a 400 for a bad regex). */
        fun javaPattern(q: String, regex: Boolean, matchCase: Boolean, word: Boolean): Pattern {
            var body = if (regex) q else Pattern.quote(q)
            if (word) body = "(?<![\\p{L}\\p{N}_])(?:$body)(?![\\p{L}\\p{N}_])"
            val flags = if (matchCase) 0 else Pattern.CASE_INSENSITIVE or Pattern.UNICODE_CASE
            return try {
                Pattern.compile(body, flags)
            } catch (e: PatternSyntaxException) {
                throw BadRequest("The regex is not valid", e.description)
            }
        }

        /**
         * Comma-separated globs as git pathspecs, like VS Code: `*.kt` matches in every folder, `src` is a file or
         * folder anywhere, `api/src/` a folder from the root. Paths outside the repo are refused.
         */
        fun pathspecs(text: String?, exclude: Boolean): List<String> {
            val globs = text.orEmpty().split(',').map { it.trim() }.filter { it.isNotEmpty() }
            if (globs.size > 30) throw BadRequest("Too many globs", "Use at most 30.")
            val magic = if (exclude) ":(exclude,glob)" else ":(glob)"
            return globs.flatMap { raw ->
                if (raw.contains('\u0000')) throw BadRequest("A glob is not valid")
                var g = raw.replace('\\', '/').removePrefix("./")
                if (g.startsWith("/") || g.startsWith("~") || g.startsWith(":") || Regex("^[A-Za-z]:").containsMatchIn(g) ||
                    g.split('/').any { it == ".." }
                ) {
                    throw Forbidden("That path is outside the project", "Use globs relative to the repo root, like src/ or *.kt.")
                }
                if (g.endsWith("/")) g += "**"
                val wild = g.any { it == '*' || it == '?' || it == '[' }
                val pats = when {
                    !g.contains('/') -> if (wild) listOf("**/$g") else listOf("**/$g", "**/$g/**")
                    !wild -> listOf(g, "$g/**")
                    else -> listOf(g)
                }
                pats.map { magic + it }
            }
        }
    }
}

/** Small git helpers for this file: output on success, else null. */
private object Proc0 {
    fun run(root: Path, timeout: Long, vararg args: String): String? =
        keel.api.common.Proc.run(listOf("git", *args), root, timeout).takeIf { it.ok }?.out

    fun ok(root: Path, vararg args: String): Boolean = keel.api.common.Proc.run(listOf("git", *args), root, 10).ok
}
