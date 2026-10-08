package keel.api.wiki

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.common.Yaml
import keel.api.knowledge.KnowledgeService
import keel.api.projects.ProjectService
import keel.api.workflows.Step
import keel.api.workflows.Workflow
import keel.api.workflows.WorkflowService
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.nio.file.Files
import java.nio.file.Path

data class WikiItem(val id: String, val title: String, val status: String? = null)
data class WikiSection(val id: String, val title: String, val items: List<WikiItem>)
data class Wiki(val sections: List<WikiSection>)
data class WikiPage(val id: String, val title: String, val markdown: String, val meta: Map<String, Any?>)

/**
 * The Wiki plugin's pages (plugins/wiki): the knowledge base, a page for every workflow, the runbook and the
 * decisions of a project, read only. It keeps keel's package (keel.api.wiki), so keel's component scan finds it when
 * its jar is on loader.path (docs/plugins/11-step3-contract.md). The same answers as keel 0.15.1's KnowledgeService:
 * which knowledge sections are written, missing or stale is still keel's (KnowledgeService.knowledge).
 */
@Service
class WikiService(
    private val projects: ProjectService,
    private val knowledge: KnowledgeService,
    private val workflows: WorkflowService,
    private val mapper: ObjectMapper,
) {
    fun wiki(pid: String): Wiki {
        val root = projects.root(pid)
        val kb = knowledge.knowledge(root).map { WikiItem("kb:${it.id}", it.id.replaceFirstChar { c -> c.uppercase() }, it.status) }
        val wfs = workflows.list(pid).map { WikiItem("wf:${it.id}", it.name) }
        val adrs = listMd(root.resolve("docs/adr")).map { WikiItem("adr:${it.fileName}", title(it)) }
        return Wiki(
            listOf(
                WikiSection("knowledge", "Knowledge", kb),
                WikiSection("workflows", "Workflows", wfs),
                WikiSection("runbook", "Runbook", listOf(WikiItem("runbook", "How to run this project"))),
                WikiSection("decisions", "Decisions", adrs),
            ),
        )
    }

    fun page(pid: String, id: String): WikiPage {
        val root = projects.root(pid)
        return when {
            id.startsWith("kb:") -> {
                val name = id.removePrefix("kb:")
                if (!Regex("^[A-Za-z0-9_-]+$").matches(name)) throw BadRequest("Bad page id")
                val f = root.resolve("docs/knowledge/$name.md")
                val section = knowledge.knowledge(root).firstOrNull { it.id == name }
                if (!Files.isRegularFile(f)) {
                    WikiPage(id, name, "This section is not written yet. Run the init flow, or ask the librarian to write it.", mapOf("status" to "missing"))
                } else {
                    WikiPage(id, title(f), Files.readString(f), mapOf(
                        "path" to "docs/knowledge/$name.md", "status" to section?.status, "words" to section?.words,
                        "cites" to section?.cites, "updated" to mtime(f),
                    ))
                }
            }
            id.startsWith("wf:") -> {
                val wf = workflows.get(id.removePrefix("wf:"))
                WikiPage(id, wf.name, workflowMarkdown(wf), mapOf("version" to wf.version, "keel_rules" to wf.keelRules, "steps" to wf.steps.size, "source" to wf.source))
            }
            id == "runbook" -> runbook(root)
            id.startsWith("adr:") -> {
                val name = id.removePrefix("adr:")
                if (name.contains('/') || name.contains("..")) throw BadRequest("Bad page id")
                val f = root.resolve("docs/adr/$name")
                if (!Files.isRegularFile(f)) throw NotFound("No decision called $name")
                WikiPage(id, title(f), Files.readString(f), mapOf("path" to "docs/adr/$name", "updated" to mtime(f)))
            }
            else -> throw NotFound("No wiki page \"$id\"", "Ids look like kb:architecture, wf:<id>, runbook or adr:<file>.")
        }
    }

    fun workflowMarkdown(wf: Workflow): String = buildString {
        appendLine("# ${wf.name}")
        appendLine()
        wf.basedOn?.let { appendLine("Based on **$it**.") }
        appendLine(if (wf.keelRules) "keel rules are **on**: locked steps stay in the flow." else "keel rules are **off**.")
        appendLine()
        appendLine("| # | Step | Kind | Who | Notes |")
        appendLine("|---|---|---|---|---|")
        wf.steps.forEachIndexed { i, s -> appendLine("| ${i + 1} | ${s.name.ifBlank { s.id }} | ${s.kind} | ${who(s)} | ${notes(s)} |") }
        val gates = wf.steps.filter { it.kind == "gate" }
        if (gates.isNotEmpty()) {
            appendLine()
            appendLine("## Where it asks you")
            gates.forEach { g -> appendLine("- **${g.name}**" + (g.back?.let { " — send back goes to `$it`" } ?: "")) }
        }
    }

    private fun who(s: Step) = when (s.kind) {
        "agent", "parallel" -> s.agent ?: "—"
        "code" -> s.action ?: "code"
        "gate" -> "you"
        else -> "—"
    }

    private fun notes(s: Step) = listOfNotNull(
        "per AC".takeIf { s.perAc == true }, "locked".takeIf { s.locked }, s.phase?.let { "phase $it" },
        s.parallel?.let { "× $it" }, s.no?.let { "no → $it" },
    ).joinToString(", ")

    private fun runbook(root: Path): WikiPage {
        val ladder = root.resolve(".keel/ladder.json")
        if (Files.isRegularFile(ladder)) {
            val node = runCatching { mapper.readTree(ladder.toFile()) }.getOrNull()
            val rungs = (node?.get("rungs") ?: node?.get("steps") ?: node)?.takeIf { it.isArray }
            if (rungs != null) {
                val md = buildString {
                    appendLine("# How to run this project")
                    appendLine()
                    appendLine("From the setup ladder (`.keel/ladder.json`).")
                    appendLine()
                    rungs.forEachIndexed { i, r ->
                        val name = r.get("name")?.asText() ?: r.get("id")?.asText() ?: "step ${i + 1}"
                        val cmd = r.get("cmd")?.asText() ?: r.get("command")?.asText()
                        val ok = r.get("ok")?.asBoolean()
                        appendLine("${i + 1}. **$name**" + (cmd?.let { " — `$it`" } ?: "") + (ok?.let { if (it) " ✓" else " ✗" } ?: ""))
                    }
                }
                return WikiPage("runbook", "How to run this project", md, mapOf("from" to ".keel/ladder.json"))
            }
        }
        val running = root.resolve("docs/RUNNING.md")
        if (Files.isRegularFile(running)) return WikiPage("runbook", "How to run this project", Files.readString(running), mapOf("from" to "docs/RUNNING.md"))
        val cfg = root.resolve(".keel/config.yml").takeIf { Files.isRegularFile(it) }?.let { Yaml.readMap(Files.readString(it)) }
        val commands = (cfg?.get("commands") as? Map<*, *>)?.filterValues { it != null && it.toString().isNotBlank() }
        val md = if (commands.isNullOrEmpty()) {
            "# How to run this project\n\nNo runbook yet. The init flow writes one when it proves the setup ladder."
        } else buildString {
            appendLine("# How to run this project")
            appendLine()
            appendLine("Commands from `.keel/config.yml`:")
            appendLine()
            commands.forEach { (k, v) -> appendLine("- **$k** — `$v`") }
        }
        return WikiPage("runbook", "How to run this project", md, mapOf("from" to if (commands.isNullOrEmpty()) null else ".keel/config.yml"))
    }

    // ---- small file helpers (the same as keel's KnowledgeService has for its own reads) ----

    private fun listMd(dir: Path): List<Path> =
        if (!Files.isDirectory(dir)) emptyList()
        else Files.list(dir).use { s -> s.filter { it.toString().endsWith(".md") && Files.isRegularFile(it) }.sorted().toList() }

    private fun mtime(p: Path): String? = runCatching { Time.iso(Files.getLastModifiedTime(p).toMillis()) }.getOrNull()

    private fun title(f: Path): String = runCatching {
        Files.readAllLines(f).firstOrNull { it.startsWith("# ") }?.removePrefix("# ")?.trim()
    }.getOrNull() ?: f.fileName.toString().removeSuffix(".md")
}

/** The Wiki page's reads: its table of contents and one page (kb:<section>, wf:<id>, runbook, adr:<file>). */
@RestController
@RequestMapping("/api/projects/{pid}/wiki")
class WikiController(private val wiki: WikiService) {

    @GetMapping
    fun wiki(@PathVariable pid: String): Wiki = wiki.wiki(pid)

    @GetMapping("/page")
    fun page(@PathVariable pid: String, @RequestParam id: String): WikiPage = wiki.page(pid, id)
}
