package keel.api.knowledge

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.common.Yaml
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.projects.ProjectService
import keel.api.repo.ClassifyConfig
import keel.api.repo.RepoService
import keel.api.workflows.Step
import keel.api.workflows.Workflow
import keel.api.workflows.WorkflowService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Path
import java.util.UUID

data class KeelDoc(val path: String, val what: String, val by: String, val updated: String?, val status: String)
data class Fact(val id: String, val title: String, val text: String, val kind: String, val source: String, val at: String)
data class KnowledgeSection(val id: String, val status: String, val words: Int, val cites: Int)
data class Memory(val facts: List<Fact>, val knowledge: List<KnowledgeSection>)
data class WikiItem(val id: String, val title: String, val status: String? = null)
data class WikiSection(val id: String, val title: String, val items: List<WikiItem>)
data class Wiki(val sections: List<WikiSection>)
data class WikiPage(val id: String, val title: String, val markdown: String, val meta: Map<String, Any?>)

/** keel docs, memory (facts + knowledge base), the wiki and the map of a project. */
@Service
class KnowledgeService(
    private val jdbc: JdbcTemplate,
    private val projects: ProjectService,
    private val repo: RepoService,
    private val workflows: WorkflowService,
    private val engine: EngineClient,
    private val mapper: ObjectMapper,
) {
    // ---- keel docs ------------------------------------------------------------------------

    fun keelDocs(pid: String): List<KeelDoc> {
        val root = projects.root(pid)
        val out = mutableListOf<KeelDoc>()
        fun add(rel: String, what: String, by: String, status: String = "ok") {
            val f = root.resolve(rel)
            if (Files.exists(f)) out += KeelDoc(rel, what, by, mtime(f), status)
        }
        listMd(root.resolve("docs/specs")).forEach { add("docs/specs/${it.fileName}", "Spec — ${countAcs(it)} ACs", "explorer + you") }
        val cfg = ClassifyConfig.load(root)
        contractFiles(root, cfg).forEach { add(it, "Contract", "explorer") }
        listMd(root.resolve("docs/adr")).forEach { add("docs/adr/${it.fileName}", "Decision — ${title(it)}", "you") }
        val kb = knowledge(root)
        if (kb.any { it.status != "missing" }) {
            val written = kb.count { it.status != "missing" }
            val stale = kb.filter { it.status == "stale" }.map { it.id }
            out += KeelDoc(
                "docs/knowledge/", "Knowledge base — $written sections", "librarian",
                mtime(root.resolve("docs/knowledge")), if (stale.isEmpty()) "ok" else "check",
            )
        }
        // keel v2's own files only: a flow's state and events live in the engine (/data), not in the project.
        add(".keel/config.yml", "Project config", "init")
        add(".keel/ladder.json", "Ladder results", "init", "live")
        add("docs/RUNNING.md", "Runbook", "init")
        return out
    }

    private fun contractFiles(root: Path, cfg: ClassifyConfig): List<String> {
        val list = mutableListOf<String>()
        if (Files.isRegularFile(root.resolve(cfg.contractFile))) list += cfg.contractFile
        for (c in listOf("docs/contract/openapi.yaml", "docs/contract/openapi.yml", "openapi.yaml", "contracts/openapi.yaml")) {
            if (c !in list && Files.isRegularFile(root.resolve(c))) list += c
        }
        return list
    }

    private fun countAcs(f: Path) = runCatching { Regex("\\bAC-\\d+").findAll(Files.readString(f)).map { it.value }.distinct().count() }.getOrDefault(0)

    private fun listMd(dir: Path): List<Path> =
        if (!Files.isDirectory(dir)) emptyList()
        else Files.list(dir).use { s -> s.filter { it.toString().endsWith(".md") && Files.isRegularFile(it) }.sorted().toList() }

    private fun mtime(p: Path): String? = runCatching { Time.iso(Files.getLastModifiedTime(p).toMillis()) }.getOrNull()

    private fun title(f: Path): String = runCatching {
        Files.readAllLines(f).firstOrNull { it.startsWith("# ") }?.removePrefix("# ")?.trim()
    }.getOrNull() ?: f.fileName.toString().removeSuffix(".md")

    // ---- memory ---------------------------------------------------------------------------

    fun memory(pid: String): Memory {
        val root = projects.root(pid)
        return Memory(facts(pid), knowledge(root))
    }

    fun facts(pid: String): List<Fact> =
        jdbc.query("SELECT id, title, text, kind, source, at FROM facts WHERE project_id = ? ORDER BY at DESC", { rs, _ ->
            Fact(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5), rs.getString(6))
        }, pid)

    fun addFact(pid: String, title: String, text: String, kind: String, source: String?): Fact {
        projects.require(pid)
        checkFact(title, text, kind)
        val id = "f-" + UUID.randomUUID().toString().take(8)
        jdbc.update(
            "INSERT INTO facts(id, project_id, title, text, kind, source, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            id, pid, title, text, kind, source ?: "you", Time.now(),
        )
        return facts(pid).first { it.id == id }
    }

    fun updateFact(pid: String, fid: String, title: String?, text: String?, kind: String?): Fact {
        val cur = facts(pid).firstOrNull { it.id == fid } ?: throw NotFound("No fact $fid")
        val next = cur.copy(title = title ?: cur.title, text = text ?: cur.text, kind = kind ?: cur.kind)
        checkFact(next.title, next.text, next.kind)
        jdbc.update("UPDATE facts SET title = ?, text = ?, kind = ?, at = ? WHERE id = ? AND project_id = ?", next.title, next.text, next.kind, Time.now(), fid, pid)
        return facts(pid).first { it.id == fid }
    }

    fun deleteFact(pid: String, fid: String) {
        if (jdbc.update("DELETE FROM facts WHERE id = ? AND project_id = ?", fid, pid) == 0) throw NotFound("No fact $fid")
    }

    private fun checkFact(title: String, text: String, kind: String) {
        if (title.isBlank() || text.isBlank()) throw BadRequest("A fact needs a title and a text")
        if (kind !in FACT_KINDS) throw BadRequest("kind must be one of: ${FACT_KINDS.joinToString()}")
    }

    /**
     * docs/knowledge/<section>.md: written | missing | stale. Stale = the section's last commit is
     * older than the last commit that touched code (a heuristic).
     */
    fun knowledge(root: Path): List<KnowledgeSection> {
        val dir = root.resolve("docs/knowledge")
        val extra = listMd(dir).map { it.fileName.toString().removeSuffix(".md") }.filter { it !in SECTIONS && it.lowercase() != "readme" }
        val lastCode = repo.lastCodeCommit(root)
        return (SECTIONS + extra).map { s ->
            val f = dir.resolve("$s.md")
            if (!Files.isRegularFile(f)) return@map KnowledgeSection(s, "missing", 0, 0)
            val text = Files.readString(f)
            val words = text.split(Regex("\\s+")).count { it.isNotBlank() }
            val cites = CITATION.findAll(text).count()
            val written = repo.lastCommitTime(root, "docs/knowledge/$s.md")
            val stale = written != null && lastCode != null && written < lastCode
            KnowledgeSection(s, if (stale) "stale" else "written", words, cites)
        }
    }

    // ---- wiki -----------------------------------------------------------------------------

    fun wiki(pid: String): Wiki {
        val root = projects.root(pid)
        val kb = knowledge(root).map { WikiItem("kb:${it.id}", it.id.replaceFirstChar { c -> c.uppercase() }, it.status) }
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
                val section = knowledge(root).firstOrNull { it.id == name }
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

    // ---- map ------------------------------------------------------------------------------

    /** The map the engine built (engine runtime/mapper.py, stored in its DB), or `{missing}` until it built one. */
    fun map(pid: String): JsonNode {
        projects.require(pid)
        return try {
            engine.map(pid)
        } catch (e: EngineDown) {
            missing("The engine is not running, so the map cannot be read.")
        }
    }

    /** Builds the map for HEAD in the engine: folders, tables from the SQL migrations, endpoints from the API contract. */
    fun rebuildMap(pid: String): JsonNode {
        val root = projects.root(pid)
        return engine.buildMap(pid, root.toString())
    }

    private fun missing(reason: String): JsonNode = mapper.createObjectNode().put("missing", reason)

    // ---- the code graph (the Graph page): the engine reads the project's CodeGraph index ---------

    fun graph(pid: String): JsonNode = graphCall(pid) { engine.graph(pid) }

    fun graphSearch(pid: String, q: String): JsonNode = graphCall(pid) { engine.graphSearch(pid, q.take(200)) }

    fun graphNode(pid: String, id: String, depth: Int): JsonNode {
        if (id.isBlank()) throw BadRequest("id is missing", "Pick a symbol from the search or the graph.")
        return graphCall(pid) { engine.graphNode(pid, id, depth.coerceIn(1, 2)) }
    }

    private fun graphCall(pid: String, call: () -> JsonNode): JsonNode {
        projects.require(pid)
        return try {
            call()
        } catch (e: EngineDown) {
            mapper.createObjectNode().put("available", false).put("status", "engine")
                .put("reason", "The engine is not running, so the code graph cannot be read.")
        }
    }

    companion object {
        val SECTIONS = listOf("architecture", "domain", "conventions", "data", "integrations", "journeys")
        val FACT_KINDS = setOf("fact", "rule", "flaky", "unlock")
        val CITATION = Regex("`([A-Za-z0-9_@./-]+\\.[A-Za-z0-9]+):(\\d+)(?:-(\\d+))?`")
    }
}
