package keel.api.doctor

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.connections.SecretService
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.projects.ProjectService
import keel.api.repo.RepoService
import keel.api.settings.SettingsService
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Path

data class DirtyFile(val path: String, val status: String, val size: Long, val kind: String, val secret: Boolean, val tracked: Boolean)

/** One proposed step. action: commit | stash | exclude (.git/info/exclude, this computer only) | ignore (.gitignore) | keep. */
data class PlanItem(
    val id: String,
    val title: String,
    val why: String,
    val action: String,
    val files: List<String>,
    val message: String? = null,
    val patterns: List<String>? = null,
)

data class Diagnosis(val by: String, val summary: String, val files: List<DirtyFile>, val plan: List<PlanItem>, val note: String? = null,
                     val tokensIn: Int = 0, val tokensOut: Int = 0)

data class ApplyItem(val action: String = "", val files: List<String> = emptyList(), val message: String? = null,
                     val patterns: List<String>? = null, val title: String? = null)
data class ApplyBody(val plan: List<ApplyItem> = emptyList())
data class ApplyResult(val action: String, val files: List<String>, val ok: Boolean, val detail: String)
data class Applied(val results: List<ApplyResult>, val remaining: List<String>, val clean: Boolean)

/**
 * The workspace Doctor: when a flow cannot start because the tree has uncommitted work, it groups the files,
 * explains them and proposes commit / stash / .gitignore / keep. Rules always give a plan; when the project's
 * default model is a real one, that model refines it (secret files are never shown to it). Applying runs plain
 * git and never deletes anything.
 */
@Service
class WorkspaceDoctor(
    private val projects: ProjectService,
    private val repo: RepoService,
    private val settings: SettingsService,
    private val secrets: SecretService,
    private val engine: EngineClient,
    private val mapper: ObjectMapper,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    fun dirty(root: Path): List<DirtyFile> {
        val r = repo.git(root, "status", "--porcelain", "--untracked-files=all")
        if (!r.ok) throw Conflict("This folder is not a git repository", r.err.take(200))
        return r.out.lines().filter { it.length > 3 }.mapNotNull { line ->
            val xy = line.substring(0, 2)
            var path = line.substring(3).trim().removeSurrounding("\"")
            if (" -> " in path) path = path.substringAfter(" -> ")
            if (isEngineFile(path)) return@mapNotNull null
            val f = root.resolve(path)
            val size = runCatching { if (Files.isRegularFile(f)) Files.size(f) else 0L }.getOrDefault(0L)
            val tracked = xy[0] != '?'
            val status = when {
                xy == "??" -> "new"
                'D' in xy -> "deleted"
                'R' in xy -> "renamed"
                else -> "changed"
            }
            DirtyFile(path, status, size, kindOf(path), isSecret(path), tracked)
        }
    }

    fun diagnose(pid: String): Diagnosis {
        val root = projects.root(pid)
        val files = dirty(root)
        if (files.isEmpty()) return Diagnosis("rules", "Nothing to clean: the working tree is clean.", files, emptyList())
        val rules = rulesPlan(files)
        val model = settings.effective(pid).defaultModel
        if (model.provider == "fake") {
            return Diagnosis("rules", summaryOf(files), files, rules,
                note = "Built-in rules. Pick a real model in Connections and the Doctor explains the files with it.")
        }
        val prompt = promptFor(root, files)
        val answer = try {
            engine.post("/agents/ask", mapOf(
                "model" to model, "system" to SYSTEM, "prompt" to prompt,
                "keys" to secrets.engineKeys(model.provider, model.mode), "timeout" to 240,
            ), long = true)
        } catch (e: EngineDown) {
            return Diagnosis("rules", summaryOf(files), files, rules, note = "The engine is not running, so the built-in rules made this plan.")
        }
        if (!answer.path("ok").asBoolean(false)) {
            return Diagnosis("rules", summaryOf(files), files, rules,
                note = "The model did not answer (${answer.path("error").asText("no reason").take(200)}), so the built-in rules made this plan.")
        }
        val parsed = runCatching { parseModelPlan(answer.path("text").asText(), files) }.getOrNull()
        val by = "${model.provider} · ${model.model}"
        return if (parsed == null) {
            Diagnosis("rules", summaryOf(files), files, rules, note = "The model's answer could not be read, so the built-in rules made this plan.")
        } else {
            Diagnosis(by, parsed.first, files, parsed.second, tokensIn = answer.path("tokens_in").asInt(), tokensOut = answer.path("tokens_out").asInt())
        }
    }

    fun apply(pid: String, body: ApplyBody): Applied {
        val root = projects.root(pid)
        if (body.plan.isEmpty()) throw BadRequest("The plan is empty")
        val dirtyNow = dirty(root).associateBy { it.path }
        // Doing it in this order keeps every step independent: exclude/ignore first (they hide files), stash last.
        val order = listOf("exclude", "ignore", "commit", "stash", "keep")
        val items = body.plan.sortedBy { order.indexOf(it.action).takeIf { i -> i >= 0 } ?: 9 }
        items.forEach { item ->
            if (item.action !in order) throw BadRequest("Unknown action \"${item.action}\"", "Use commit, stash, exclude, ignore or keep.")
            val unknown = item.files.filter { it !in dirtyNow }
            if (unknown.isNotEmpty()) throw BadRequest("These files have no uncommitted changes: ${unknown.joinToString()}", "Ask the Doctor again.")
            if (item.action == "commit") {
                val secret = item.files.filter { dirtyNow.getValue(it).secret }
                if (secret.isNotEmpty()) throw Conflict("These look like secrets and are never committed: ${secret.joinToString()}", "Ignore or stash them instead.")
                if (item.message.isNullOrBlank()) throw BadRequest("A commit needs a message")
            }
            if (item.action == "ignore" || item.action == "exclude") {
                val tracked = item.files.filter { dirtyNow.getValue(it).tracked }
                if (tracked.isNotEmpty()) throw Conflict("Already tracked by git, so an ignore rule cannot hide them: ${tracked.joinToString()}", "Stash or commit them instead.")
            }
        }
        // Start from an empty index so a commit holds exactly its files (the work stays in the tree).
        repo.git(root, "reset", "-q")
        val results = mutableListOf<ApplyResult>()
        var gitignoreChanged = false
        for (item in items) {
            val r = when (item.action) {
                "exclude" -> {
                    // Hidden on this computer only: .git/info/exclude is never committed, the project's .gitignore stays.
                    val patterns = (item.patterns?.filter { it.isNotBlank() }?.takeIf { it.isNotEmpty() } ?: item.files).map { it.trim() }
                    val where = repo.git(root, "rev-parse", "--git-path", "info/exclude").out.trim().ifBlank { ".git/info/exclude" }
                    val f = root.resolve(where)
                    Files.createDirectories(f.parent)
                    val have = if (Files.exists(f)) Files.readAllLines(f).map { it.trim() }.toSet() else emptySet()
                    val add = patterns.filter { it !in have }
                    if (add.isNotEmpty()) {
                        val prefix = if (Files.exists(f) && Files.readString(f).let { it.isNotEmpty() && !it.endsWith("\n") }) "\n" else ""
                        Files.writeString(f, prefix + "# hidden on this computer by the keel Doctor\n" + add.joinToString("\n") + "\n",
                            java.nio.file.StandardOpenOption.CREATE, java.nio.file.StandardOpenOption.APPEND)
                    }
                    ApplyResult("exclude", item.files, true, if (add.isEmpty()) "already hidden on this computer"
                        else "hidden on this computer (.git/info/exclude): ${add.joinToString()}")
                }
                "ignore" -> {
                    val patterns = (item.patterns?.filter { it.isNotBlank() }?.takeIf { it.isNotEmpty() } ?: item.files).map { it.trim() }
                    val f = root.resolve(".gitignore")
                    val have = if (Files.exists(f)) Files.readAllLines(f).map { it.trim() }.toSet() else emptySet()
                    val add = patterns.filter { it !in have }
                    if (add.isNotEmpty()) {
                        val prefix = if (Files.exists(f) && Files.readString(f).let { it.isNotEmpty() && !it.endsWith("\n") }) "\n" else ""
                        Files.writeString(f, prefix + "# added by the keel Doctor\n" + add.joinToString("\n") + "\n",
                            java.nio.file.StandardOpenOption.CREATE, java.nio.file.StandardOpenOption.APPEND)
                        gitignoreChanged = true
                    }
                    ApplyResult("ignore", item.files, true, if (add.isEmpty()) "already in .gitignore" else "added to .gitignore: ${add.joinToString()}")
                }
                "commit" -> {
                    val files = item.files
                    val add = repo.git(root, "add", "-A", "--", *files.toTypedArray())
                    val c = if (add.ok) repo.git(root, "commit", "-q", "-m", item.message!!.trim()) else add
                    if (c.ok && files.contains(".gitignore")) gitignoreChanged = false
                    ApplyResult("commit", files, c.ok, if (c.ok) "committed: ${item.message!!.trim()}" else (c.err.ifBlank { c.out }).take(300))
                }
                "stash" -> {
                    val s = repo.git(root, "stash", "push", "-q", "-u", "-m", "keel doctor: ${item.title ?: "saved work"}", "--", *item.files.toTypedArray())
                    ApplyResult("stash", item.files, s.ok, if (s.ok) "stashed — get it back with: git stash pop" else (s.err.ifBlank { s.out }).take(300))
                }
                else -> ApplyResult("keep", item.files, true, "left as it is")
            }
            results += r
        }
        if (gitignoreChanged) {
            val add = repo.git(root, "add", "--", ".gitignore")
            val c = if (add.ok) repo.git(root, "commit", "-q", "-m", "chore: ignore local and secret files") else add
            results += ApplyResult("commit", listOf(".gitignore"), c.ok, if (c.ok) "committed: chore: ignore local and secret files" else c.err.take(300))
        }
        val remaining = dirty(root).map { it.path }
        return Applied(results, remaining, remaining.isEmpty())
    }

    // ---- rules ----

    fun rulesPlan(files: List<DirtyFile>): List<PlanItem> {
        val by = files.groupBy { it.kind }
        val plan = mutableListOf<PlanItem>()
        by["secret"]?.let { fs ->
            val untracked = fs.filter { !it.tracked }
            val tracked = fs.filter { it.tracked }
            if (untracked.isNotEmpty()) plan += PlanItem("secret", "Secret-looking files", "They may hold passwords or keys. Keep them out of git for good.",
                "ignore", untracked.map { it.path }, patterns = untracked.map { it.path })
            if (tracked.isNotEmpty()) plan += PlanItem("secret-tracked", "Changed secret-looking files", "Already in git, so .gitignore cannot hide them. Stash keeps the change out of keel's commits.",
                "stash", tracked.map { it.path })
        }
        by["local"]?.let { fs ->
            val untracked = fs.filter { !it.tracked }
            if (untracked.isNotEmpty()) plan += PlanItem("local", "Build output, editor and tool folders",
                "Made by tools on this computer; nobody else needs them. Hidden here only (.git/info/exclude): the project's .gitignore stays as it is.",
                "exclude", untracked.map { it.path }, patterns = untracked.map { localPattern(it.path) }.distinct())
            val tracked = fs.filter { it.tracked }
            if (tracked.isNotEmpty()) plan += PlanItem("local-tracked", "Changed tool files", "Already in git; stash them for now.", "stash", tracked.map { it.path })
        }
        by["tooling"]?.let { fs ->
            plan += PlanItem("tooling", "Project setup (keel, dev container, git, editor)", "Settings the whole team can share; a separate commit keeps them apart from feature work.",
                "commit", fs.map { it.path }, message = "chore: project tooling (" + fs.map { area(it.path) }.distinct().joinToString(", ") + ")")
        }
        by["docs"]?.let { fs ->
            plan += PlanItem("docs", "Documentation", "Text only; safe to commit on its own.", "commit", fs.map { it.path },
                message = "docs: " + fs.map { it.path.substringAfterLast('/').substringBeforeLast('.') }.distinct().take(4).joinToString(", "))
        }
        by["code"]?.let { fs ->
            plan += PlanItem("code", "Code and tests in progress", "Unfinished work. A stash keeps it safe; `git stash pop` brings it back after the flow.",
                "stash", fs.map { it.path })
        }
        return plan
    }

    private fun summaryOf(files: List<DirtyFile>): String {
        val counts = files.groupingBy { it.kind }.eachCount()
        val parts = listOf("tooling" to "setup", "docs" to "docs", "code" to "code", "local" to "local", "secret" to "secret-looking")
            .mapNotNull { (k, l) -> counts[k]?.let { "$it $l" } }
        return "${files.size} uncommitted file${if (files.size == 1) "" else "s"}: ${parts.joinToString(", ")}."
    }

    private fun parseModelPlan(text: String, files: List<DirtyFile>): Pair<String, List<PlanItem>>? {
        val json = text.substring(text.indexOf('{').takeIf { it >= 0 } ?: return null, text.lastIndexOf('}') + 1)
        val node: JsonNode = mapper.readTree(json)
        val byPath = files.associateBy { it.path }
        val used = mutableSetOf<String>()
        val items = node.path("plan").mapIndexedNotNull { i, n ->
            val action = n.path("action").asText()
            if (action !in setOf("commit", "stash", "exclude", "ignore", "keep")) return@mapIndexedNotNull null
            var fs = n.path("files").mapNotNull { it.asText().takeIf { p -> p in byPath && p !in used } }
            if (action == "commit") fs = fs.filter { !byPath.getValue(it).secret }
            if (action == "ignore" || action == "exclude") fs = fs.filter { !byPath.getValue(it).tracked }
            if (fs.isEmpty()) return@mapIndexedNotNull null
            used += fs
            PlanItem("m$i", n.path("title").asText("Group ${i + 1}").take(80), n.path("why").asText("").take(300), action, fs,
                message = n.path("message").asText("").takeIf { action == "commit" && it.isNotBlank() }?.take(120)
                    ?: if (action == "commit") "chore: ${fs.first()}" else null,
                patterns = n.path("patterns").mapNotNull { it.asText().takeIf { p -> p.isNotBlank() } }.takeIf { (action == "ignore" || action == "exclude") && it.isNotEmpty() })
        }.toMutableList()
        // Anything the model left out (and every secret it put in a commit) goes through the rules.
        val left = files.filter { it.path !in used }
        if (left.isNotEmpty()) items += rulesPlan(left).map { it.copy(id = "r-" + it.id, why = it.why + " (rules)") }
        if (items.isEmpty()) return null
        return node.path("summary").asText(summaryOf(files)).take(400) to items
    }

    private fun promptFor(root: Path, files: List<DirtyFile>): String {
        val sb = StringBuilder()
        sb.append("Uncommitted files in this repository (path · status · size · guess):\n")
        files.forEach { sb.append("- ${it.path} · ${it.status} · ${it.size} bytes · ${it.kind}${if (it.secret) " (secret-looking: content not shown)" else ""}\n") }
        sb.append("\nWhat changed (shortened):\n")
        var budget = 12_000
        for (f in files) {
            if (f.secret || budget <= 0) continue
            val text = if (f.tracked) repo.git(root, "diff", "--", f.path).out
            else runCatching { val p = root.resolve(f.path); if (Files.size(p) < 50_000 && !isBinary(p)) Files.readString(p) else "(binary or large file)" }.getOrDefault("")
            val cut = text.lines().take(60).joinToString("\n").take(budget.coerceAtMost(2_000))
            budget -= cut.length
            sb.append("\n### ${f.path}\n").append(cut).append("\n")
        }
        sb.append("\nAnswer with JSON only: {\"summary\": \"one or two plain sentences\", \"plan\": [{\"title\": \"short\", \"why\": \"one plain sentence\", " +
            "\"action\": \"commit|stash|ignore|keep\", \"files\": [\"exact paths from the list\"], \"message\": \"conventional commit message (commit only)\", " +
            "\"patterns\": [\".gitignore lines (ignore only)\"]}]}")
        return sb.toString()
    }

    companion object {
        val SYSTEM = """
            You are the keel Doctor. A developer wants to start an automated coding flow, but their git working tree has
            uncommitted changes. Group the files and propose what to do with each group so the tree becomes clean without
            losing any work: commit (shareable setup, docs, finished changes; give a conventional commit message), stash
            (unfinished code), exclude (this computer's tool, editor and build folders such as .serena/ or .idea/: hidden only here via
            .git/info/exclude; only untracked files), ignore (generated files nobody should ever commit: added to the project's .gitignore;
            only untracked files), or keep.
            Never propose committing secrets. Write short, plain sentences for someone whose English is basic.
        """.trimIndent()

        /** keel's own files in a project (the engine's ENGINE_FILES, engine/keel_engine/tools/git.py): never the user's work. */
        private val ENGINE_FILES = listOf(".keel/ladder.json", ".keel/agents/")
        fun isEngineFile(p: String) = ENGINE_FILES.any { p == it || p.startsWith(it) }

        private val SECRET = Regex("""(^|/)(\.env(\..*)?|.*\.(pem|key|p12|pfx|jks|keystore)|id_(rsa|ed25519|ecdsa)(\.pub)?|.*credentials.*|.*secret.*|\.npmrc|\.pypirc|\.netrc)$""", RegexOption.IGNORE_CASE)
        private val LOCAL = Regex("""(^|/)(build|dist|out|target|node_modules|\.gradle|\.kotlin|__pycache__|\.pytest_cache|\.venv|\.idea|coverage|\.next|\.cache|\.serena)(/|$)|(\.log|\.iml|\.DS_Store|\.swp|\.class|\.pyc)$""")
        private val TOOLING = Regex("""^(\.gitignore|\.gitattributes|\.editorconfig|\.keel/[^/]+\.ya?ml|\.keel/stacks/.*|\.devcontainer/.*|\.vscode/(settings|extensions)\.json|\.github/.*|\.claude/settings\.json|\.mcp\.json|\.tool-versions|\.nvmrc|\.python-version|compose\.ya?ml|docker-compose\.ya?ml|Dockerfile.*)$""")
        private val DOCS = Regex("""(^docs/.*|\.(md|mdx|rst|adoc|txt)$)""", RegexOption.IGNORE_CASE)

        fun isSecret(p: String) = SECRET.containsMatchIn(p) && !p.endsWith(".example") && !p.endsWith(".sample")
        fun kindOf(p: String): String = when {
            isSecret(p) -> "secret"
            LOCAL.containsMatchIn(p) -> "local"
            TOOLING.matches(p) -> "tooling"
            DOCS.containsMatchIn(p) -> "docs"
            else -> "code"
        }

        private fun localPattern(p: String): String {
            val m = Regex("""(^|/)(build|dist|out|target|node_modules|\.gradle|\.kotlin|__pycache__|\.pytest_cache|\.venv|\.idea|coverage|\.next|\.cache|\.serena)(/|$)""").find(p)
            if (m != null) return p.substring(0, m.range.last + 1).trimEnd('/') + "/"
            val ext = Regex("""(\.log|\.iml|\.DS_Store|\.swp|\.class|\.pyc)$""").find(p)?.value
            return if (ext != null) "*$ext" else p
        }

        private fun area(p: String) = when {
            p.startsWith(".keel") -> "keel"
            p.startsWith(".devcontainer") -> "dev container"
            p.startsWith(".serena") -> "serena"
            p.startsWith(".github") -> "CI"
            p.startsWith(".vscode") || p == ".editorconfig" -> "editor"
            p.startsWith(".git") -> "git"
            else -> p.substringAfterLast('/')
        }

        private fun isBinary(p: Path): Boolean = Files.newInputStream(p).use { s -> s.readNBytes(800).any { it == 0.toByte() } }
    }
}
