package keel.api.projects

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.common.Proc
import keel.api.common.Slug
import keel.api.common.Time
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

data class ProjectRow(val id: String, val name: String, val root: String)

data class Project(
    val id: String,
    val name: String,
    val root: String,
    val branch: String?,
    val flow: String?,
    val phase: String,
    val acs: List<Int>,
    val waiting: Int,
    val running: Int,
)

@Service
class ProjectService(private val jdbc: JdbcTemplate, private val mapper: ObjectMapper) {

    /** Projects that are reachable now (a parked workspace project is kept with its history but not listed). */
    fun rows(): List<ProjectRow> =
        jdbc.query("SELECT id, name, root FROM projects WHERE root NOT LIKE '%$PARKED%' ORDER BY name") { rs, _ -> ProjectRow(rs.getString(1), rs.getString(2), rs.getString(3)) }

    /**
     * The mounted folder (/workspace) is the same path for every project the launcher starts, so it is known by
     * its name: another name there parks the old project (history kept, not listed) and the same name re-attaches it.
     */
    fun registerWorkspace(rootText: String, name: String): ProjectRow {
        val root = Paths.get(rootText).toAbsolutePath().normalize().toString()
        val id = keel.api.common.Slug.of(name)
        val here = jdbc.query("SELECT id, name, root FROM projects WHERE root = ?", { rs, _ -> ProjectRow(rs.getString(1), rs.getString(2), rs.getString(3)) }, root).firstOrNull()
        if (here != null && here.id == id) return here
        if (here != null) jdbc.update("UPDATE projects SET root = ? WHERE id = ?", "$root$PARKED${here.id}", here.id)
        val parked = find(id)
        if (parked != null) {
            jdbc.update("UPDATE projects SET root = ?, name = ? WHERE id = ?", root, name, id)
            return ProjectRow(id, name, root)
        }
        return register(root, name)
    }

    fun find(pid: String): ProjectRow? =
        jdbc.query("SELECT id, name, root FROM projects WHERE id = ?", { rs, _ -> ProjectRow(rs.getString(1), rs.getString(2), rs.getString(3)) }, pid).firstOrNull()

    fun require(pid: String): ProjectRow =
        find(pid) ?: throw NotFound("No project called \"$pid\"", "GET /api/projects lists the projects keel knows.")

    fun root(pid: String): Path = Paths.get(require(pid).root)

    /** Registers a folder. Same root again returns the existing project. */
    fun register(rootText: String, name: String? = null): ProjectRow {
        if (rootText.isBlank()) throw BadRequest("root is empty", "Send the absolute path of the repo inside the container.")
        val root = Paths.get(rootText).toAbsolutePath().normalize()
        if (!Files.isDirectory(root)) throw BadRequest("There is no folder at $root", "Mount the project and use the path inside the container.")
        val existing = jdbc.query("SELECT id, name, root FROM projects WHERE root = ?", { rs, _ -> ProjectRow(rs.getString(1), rs.getString(2), rs.getString(3)) }, root.toString()).firstOrNull()
        if (existing != null) return existing
        val base = Slug.of(name?.takeIf { it.isNotBlank() } ?: root.fileName?.toString() ?: "project")
        var id = base
        var n = 2
        while (find(id) != null) id = "$base-${n++}"
        val display = name?.takeIf { it.isNotBlank() } ?: root.fileName?.toString() ?: id
        jdbc.update("INSERT INTO projects(id, name, root, created_at) VALUES (?, ?, ?, ?)", id, display, root.toString(), Time.now())
        return ProjectRow(id, display, root.toString())
    }

    fun keelState(root: Path): JsonNode? {
        val f = root.resolve(".keel/state.json")
        return if (Files.isRegularFile(f)) runCatching { mapper.readTree(f.toFile()) }.getOrNull() else null
    }

    fun branch(root: Path): String? {
        val r = Proc.run(listOf("git", "rev-parse", "--abbrev-ref", "HEAD"), root, 5)
        if (r.ok) return r.out.trim().ifBlank { null }
        // A repo with no commits yet has no HEAD to parse, but it has a branch name.
        val s = Proc.run(listOf("git", "symbolic-ref", "--short", "HEAD"), root, 5)
        return if (s.ok) s.out.trim().ifBlank { null } else null
    }

    fun view(row: ProjectRow): Project {
        val root = Paths.get(row.root)
        val state = keelState(root)
        val thread = jdbc.query(
            "SELECT workflow_id, status, phase, state_json FROM threads WHERE project_id = ? ORDER BY CASE WHEN status IN ('running','waiting') THEN 0 ELSE 1 END, updated_at DESC LIMIT 1",
            { rs, _ -> listOf(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4)) }, row.id,
        ).firstOrNull()
        val active = thread != null && thread[1] in setOf("running", "waiting")
        val threadState = thread?.get(3)?.let { runCatching { mapper.readTree(it) }.getOrNull() }

        val flow = if (active) thread!![0] else state?.get("flow")?.takeIf { !it.isNull }?.asText()
        val phase = (if (active) thread!![2] else null) ?: state?.get("phase")?.asText() ?: "none"
        val acs = acCounts(if (active) threadState else null, state)
        val waiting = jdbc.queryForObject("SELECT COUNT(*) FROM threads WHERE project_id = ? AND status = 'waiting'", Int::class.java, row.id) ?: 0
        val running = jdbc.queryForObject("SELECT COUNT(*) FROM agent_calls WHERE project_id = ? AND status = 'running'", Int::class.java, row.id) ?: 0
        return Project(row.id, row.name, row.root, branch(root), flow, phase, acs, waiting, running)
    }

    private fun acCounts(thread: JsonNode?, state: JsonNode?): List<Int> {
        val list = thread?.get("acs")
        if (list != null && list.isArray && list.size() > 0) {
            return listOf(list.count { it.get("status")?.asText() == "done" }, list.size())
        }
        val map = state?.get("acs")
        if (map != null && map.isObject && map.size() > 0) {
            val done = map.elements().asSequence().count { ac ->
                val st = ac.get("status")?.asText() ?: ac.get("state")?.asText()
                st == "done" || st == "green" || st == "approved"
            }
            return listOf(done, map.size())
        }
        return listOf(0, 0)
    }
}

/** Marker appended to a parked workspace project's root. */
const val PARKED = "#parked:"
