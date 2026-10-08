package keel.api.knowledge

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.projects.ProjectService
import keel.api.repo.ClassifyConfig
import keel.api.workspace.Workspace
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Path
import java.util.UUID

data class KeelDoc(val path: String, val what: String, val by: String, val updated: String?, val status: String)
data class Fact(val id: String, val title: String, val text: String, val kind: String, val source: String, val at: String)
data class KnowledgeSection(val id: String, val status: String, val words: Int, val cites: Int)
data class Memory(val facts: List<Fact>, val knowledge: List<KnowledgeSection>)

/** keel docs, memory (facts + knowledge base) and the code graph of a project. The Wiki page reads the knowledge base
 *  from here (the Wiki plugin, plugins/wiki). */
@Service
class KnowledgeService(
    private val jdbc: JdbcTemplate,
    private val projects: ProjectService,
    private val workspace: Workspace,
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
        val lastCode = workspace.lastCodeCommit(root)
        return (SECTIONS + extra).map { s ->
            val f = dir.resolve("$s.md")
            if (!Files.isRegularFile(f)) return@map KnowledgeSection(s, "missing", 0, 0)
            val text = Files.readString(f)
            val words = text.split(Regex("\\s+")).count { it.isNotBlank() }
            val cites = CITATION.findAll(text).count()
            val written = workspace.lastCommitTime(root, "docs/knowledge/$s.md")
            val stale = written != null && lastCode != null && written < lastCode
            KnowledgeSection(s, if (stale) "stale" else "written", words, cites)
        }
    }

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
