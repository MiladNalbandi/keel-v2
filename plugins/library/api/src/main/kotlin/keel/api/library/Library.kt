package keel.api.library

import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.projects.ProjectService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.util.UUID

data class LibraryDoc(
    val id: String, val projectId: String?, val title: String, val body: String, val source: String?, val forAgents: Boolean,
    val version: Int, val createdAt: String, val updatedAt: String,
)

/** A document in the list: no body. */
data class LibraryRow(val id: String, val projectId: String?, val title: String, val source: String?, val forAgents: Boolean, val version: Int,
                      val updatedAt: String, val size: Int)
data class LibraryList(val docs: List<LibraryRow>, val sends: String)

/** One passage that fits a search. */
data class LibraryHit(val docId: String, val title: String, val section: String?, val version: Int, val quote: String, val score: Int, val forAgents: Boolean)

/** A new document, or what changes in one. `scope` is all (every project) or project (only this one). */
data class DocBody(val title: String? = null, val body: String? = null, val source: String? = null, val scope: String? = null, val forAgents: Boolean? = null)

/**
 * The Library plugin's api part: documents a person put here, for every project or one, and a search over them that needs
 * no model. Two doors:
 *
 *     /api/projects/{pid}/library/docs, /search           the page: everything of this project and of all projects
 *     /api/projects/{pid}/library/agent/search, /docs/id  the agents' tools (engine keel_plugin_library): only documents
 *                                                         marked for agents, and GET only
 *
 * A search runs here, on this computer; nothing goes to a model until an agent's tool returns it.
 */
@Service
class LibraryService(private val jdbc: JdbcTemplate, private val projects: ProjectService) {
    private val all = "id, project_id, title, body, source, for_agents, version, created_at, updated_at"

    private fun doc(rs: java.sql.ResultSet) = LibraryDoc(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5), rs.getInt(6) == 1,
        rs.getInt(7), rs.getString(8), rs.getString(9))

    private fun docs(pid: String, agents: Boolean): List<LibraryDoc> = jdbc.query(
        "SELECT $all FROM library_doc WHERE (project_id = ? OR project_id IS NULL)" + (if (agents) " AND for_agents = 1" else "") + " ORDER BY title COLLATE NOCASE LIMIT $DOCS_MAX",
        { rs, _ -> doc(rs) }, pid)

    fun list(pid: String): LibraryList {
        projects.require(pid)
        return LibraryList(docs(pid, false).map { LibraryRow(it.id, it.projectId, it.title, it.source, it.forAgents, it.version, it.updatedAt, it.body.length) }, SENDS)
    }

    fun one(pid: String, id: String, agents: Boolean = false): LibraryDoc {
        projects.require(pid)
        return docs(pid, agents).firstOrNull { it.id == id } ?: throw NotFound("No document $id for this project")
    }

    fun add(pid: String, b: DocBody): LibraryDoc {
        projects.require(pid)
        val title = b.title?.trim().orEmpty()
        val body = b.body?.trim().orEmpty()
        if (title.isEmpty() || body.isEmpty()) throw BadRequest("A document needs a title and its text")
        check(body)
        val id = "l" + UUID.randomUUID().toString().replace("-", "").take(10)
        val now = Time.now()
        jdbc.update("INSERT INTO library_doc(id, project_id, title, body, source, for_agents, version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            id, scoped(pid, b.scope), title.take(200), body, b.source?.trim()?.ifBlank { null }?.take(200), if (b.forAgents == false) 0 else 1, now, now)
        return one(pid, id)
    }

    fun change(pid: String, id: String, b: DocBody): LibraryDoc {
        val d = one(pid, id)
        b.body?.let { check(it.trim()) }
        // a new version only when the words change: an agent's quote names the version it read
        val text = b.title != null && b.title.trim() != d.title || b.body != null && b.body.trim() != d.body
        jdbc.update("UPDATE library_doc SET project_id = ?, title = ?, body = ?, source = ?, for_agents = ?, version = ?, updated_at = ? WHERE id = ?",
            if (b.scope != null) scoped(pid, b.scope) else d.projectId, b.title?.trim()?.ifBlank { null }?.take(200) ?: d.title, b.body?.trim()?.ifBlank { null } ?: d.body,
            if (b.source != null) b.source.trim().ifBlank { null }?.take(200) else d.source, if (b.forAgents ?: d.forAgents) 1 else 0,
            if (text) d.version + 1 else d.version, Time.now(), id)
        return one(pid, id)
    }

    fun delete(pid: String, id: String) {
        one(pid, id)
        jdbc.update("DELETE FROM library_doc WHERE id = ?", id)
    }

    private fun check(body: String) {
        if (body.length > BODY_MAX) throw BadRequest("The document is too long (${BODY_MAX / 1000} KB at most)", "Split it into several, one per subject.")
    }

    private fun scoped(pid: String, scope: String?): String? = when (scope ?: "all") {
        "all" -> null
        "project" -> pid
        else -> throw BadRequest("scope is all or project")
    }

    fun search(pid: String, q: String, limit: Int, agents: Boolean): List<LibraryHit> {
        projects.require(pid)
        val words = words(q)
        if (words.isEmpty()) return emptyList()
        return docs(pid, agents).flatMap { d -> passages(d.body).mapNotNull { p -> hit(d, p, words, q) } }
            .sortedWith(compareByDescending<LibraryHit> { it.score }.thenBy { it.title }).take(limit.coerceIn(1, 20))
    }

    private fun hit(d: LibraryDoc, p: Passage, words: Set<String>, q: String): LibraryHit? {
        val here = words(p.text)
        val title = words(d.title + " " + (p.section ?: ""))
        val inText = words.count { it in here }
        if (inText == 0) return null
        var score = inText * 3 + words.count { it in title }
        if (p.text.contains(q.trim(), ignoreCase = true)) score += 5
        return LibraryHit(d.id, d.title, p.section, d.version, p.text.take(QUOTE_MAX), score, d.forAgents)
    }

    companion object {
        const val BODY_MAX = 200_000
        const val DOCS_MAX = 1000
        const val QUOTE_MAX = 700
        const val SENDS = "What an agent finds here goes to your model company, as anything an agent reads does. That is only a document marked for agents, in a " +
            "project that turned the Library on. A search on this page stays on this computer. Do not put staff or customer data here unless that company may have it."

        data class Passage(val section: String?, val text: String)

        private val HEADING = Regex("^#{1,6}\\s+(.+)$")

        /** The text as passages: split at blank lines, each with the last heading over it. A heading alone is no passage. */
        fun passages(body: String): List<Passage> {
            val out = mutableListOf<Passage>()
            var section: String? = null
            val now = StringBuilder()
            fun flush() {
                val t = now.toString().trim()
                if (t.isNotEmpty()) out += Passage(section, t)
                now.clear()
            }
            for (line in body.lines()) {
                val h = HEADING.find(line.trim())
                when {
                    h != null -> { flush(); section = h.groupValues[1].trim() }
                    line.isBlank() -> flush()
                    else -> now.appendLine(line)
                }
            }
            flush()
            return out
        }

        private val STOP = setOf("the", "and", "for", "that", "this", "with", "are", "was", "our", "you", "your", "what", "how", "does", "any", "all", "has", "have", "who", "when", "where", "which")

        /** Words of three letters or more, plain and without a plural s, so "backups" finds "backup". */
        fun words(text: String): Set<String> = Regex("[\\p{L}\\p{N}]{3,}").findAll(text.lowercase()).map { it.value.removeSuffix("s") }
            .filter { it.length >= 3 && it !in STOP }.toSet()
    }
}

@RestController
@RequestMapping("/api/projects/{pid}/library")
class LibraryController(private val library: LibraryService) {
    @GetMapping("/docs")
    fun list(@PathVariable pid: String): LibraryList = library.list(pid)

    @PostMapping("/docs")
    fun add(@PathVariable pid: String, @RequestBody body: DocBody): LibraryDoc = library.add(pid, body)

    @GetMapping("/docs/{id}")
    fun one(@PathVariable pid: String, @PathVariable id: String): LibraryDoc = library.one(pid, id)

    @PostMapping("/docs/{id}")
    fun change(@PathVariable pid: String, @PathVariable id: String, @RequestBody body: DocBody): LibraryDoc = library.change(pid, id, body)

    @DeleteMapping("/docs/{id}")
    fun delete(@PathVariable pid: String, @PathVariable id: String): Map<String, Boolean> {
        library.delete(pid, id)
        return mapOf("deleted" to true)
    }

    @GetMapping("/search")
    fun search(@PathVariable pid: String, @RequestParam q: String, @RequestParam(defaultValue = "10") limit: Int): List<LibraryHit> =
        library.search(pid, q, limit, agents = false)

    /** The agents' door: what library_search gets. Never a document marked for people only. */
    @GetMapping("/agent/search")
    fun agentSearch(@PathVariable pid: String, @RequestParam q: String, @RequestParam(defaultValue = "5") limit: Int): List<LibraryHit> =
        library.search(pid, q, limit, agents = true)

    @GetMapping("/agent/docs/{id}")
    fun agentDoc(@PathVariable pid: String, @PathVariable id: String): LibraryDoc = library.one(pid, id, agents = true)
}
