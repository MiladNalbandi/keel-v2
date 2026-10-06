package keel.api.projects

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.common.Proc
import keel.api.common.Slug
import keel.api.common.Time
import keel.api.engine.EngineClient
import org.slf4j.LoggerFactory
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.util.concurrent.Executors

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
class ProjectService(private val jdbc: JdbcTemplate, private val mapper: ObjectMapper, private val engine: EngineClient) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val scans = Executors.newSingleThreadExecutor { r -> Thread(r, "project-scan").apply { isDaemon = true } }

    /**
     * Asks the engine to scan the project (stack, code graph index, map). Fire and forget: the engine answers at once
     * and reports progress as index.* events. At startup the engine may not be up yet, so this retries for a minute.
     */
    fun triggerScan(row: ProjectRow, rebuild: Boolean = false) {
        scans.execute {
            for (attempt in 1..30) {
                try {
                    engine.scan(row.id, row.root, rebuild)
                    return@execute
                } catch (e: keel.api.engine.EngineDown) {
                    Thread.sleep(2000)
                } catch (e: Exception) {
                    log.info("scan of {} not started: {}", row.id, e.message)
                    return@execute
                }
            }
            log.info("scan of {} not started: the engine did not answer", row.id)
        }
    }

    /**
     * Projects that are reachable now. A parked workspace project, or one whose folder is not mounted in this
     * container (keel was started for another project, or with/without --docker), is kept with its history but not
     * listed; it comes back when its folder is mounted again.
     */
    fun rows(): List<ProjectRow> =
        jdbc.query("SELECT id, name, root FROM projects WHERE root NOT LIKE '%$PARKED%' ORDER BY name") { rs, _ -> ProjectRow(rs.getString(1), rs.getString(2), rs.getString(3)) }
            .filter { java.nio.file.Files.isDirectory(Paths.get(it.root)) }

    /**
     * The mounted folder (/workspace) is the same path for every project the launcher starts, so it is known by
     * its name: another name there parks the old project (history kept, not listed) and the same name re-attaches it.
     */
    fun registerWorkspace(rootText: String, name: String, scan: Boolean = true): ProjectRow {
        val root = Paths.get(rootText).toAbsolutePath().normalize().toString()
        val id = keel.api.common.Slug.of(name)
        val here = jdbc.query("SELECT id, name, root FROM projects WHERE root = ?", { rs, _ -> ProjectRow(rs.getString(1), rs.getString(2), rs.getString(3)) }, root).firstOrNull()
        if (here != null && here.id == id) return here
        if (here != null) jdbc.update("UPDATE projects SET root = ? WHERE id = ?", "$root$PARKED${here.id}", here.id)
        val parked = find(id)
        if (parked != null) {
            jdbc.update("UPDATE projects SET root = ?, name = ? WHERE id = ?", root, name, id)
            return ProjectRow(id, name, root).also { if (scan) triggerScan(it) }
        }
        return register(root, name, scan)
    }

    fun find(pid: String): ProjectRow? =
        jdbc.query("SELECT id, name, root FROM projects WHERE id = ?", { rs, _ -> ProjectRow(rs.getString(1), rs.getString(2), rs.getString(3)) }, pid).firstOrNull()

    fun require(pid: String): ProjectRow =
        find(pid) ?: throw NotFound("No project called \"$pid\"", "GET /api/projects lists the projects keel knows.")

    fun root(pid: String): Path = Paths.get(require(pid).root)

    /** Registers a folder (a new one is scanned). Same root again returns the existing project. */
    fun register(rootText: String, name: String? = null, scan: Boolean = true): ProjectRow {
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
        return ProjectRow(id, display, root.toString()).also { if (scan) triggerScan(it) }
    }

    /** The phase of the project's running or waiting flow; "none" when no flow is active. */
    fun activePhase(pid: String): String = jdbc.query(
        "SELECT phase FROM threads WHERE project_id = ? AND status IN ('running','waiting') ORDER BY updated_at DESC LIMIT 1",
        { rs, _ -> rs.getString(1) }, pid,
    ).firstOrNull()?.takeIf { it.isNotBlank() } ?: "none"

    fun branch(root: Path): String? {
        val r = Proc.run(listOf("git", "rev-parse", "--abbrev-ref", "HEAD"), root, 5)
        if (r.ok) return r.out.trim().ifBlank { null }
        // A repo with no commits yet has no HEAD to parse, but it has a branch name.
        val s = Proc.run(listOf("git", "symbolic-ref", "--short", "HEAD"), root, 5)
        return if (s.ok) s.out.trim().ifBlank { null } else null
    }

    fun view(row: ProjectRow): Project {
        val root = Paths.get(row.root)
        val thread = jdbc.query(
            "SELECT workflow_id, status, phase, state_json FROM threads WHERE project_id = ? ORDER BY CASE WHEN status IN ('running','waiting') THEN 0 ELSE 1 END, updated_at DESC LIMIT 1",
            { rs, _ -> listOf(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4)) }, row.id,
        ).firstOrNull()
        val active = thread != null && thread[1] in setOf("running", "waiting")
        val threadState = thread?.get(3)?.let { runCatching { mapper.readTree(it) }.getOrNull() }

        // Only a running or waiting flow gives the project a flow, a phase and AC counts.
        val flow = if (active) thread!![0] else null
        val phase = (if (active) thread!![2] else null) ?: "none"
        val acs = acCounts(if (active) threadState else null)
        val waiting = jdbc.queryForObject("SELECT COUNT(*) FROM threads WHERE project_id = ? AND status = 'waiting'", Int::class.java, row.id) ?: 0
        val running = jdbc.queryForObject("SELECT COUNT(*) FROM agent_calls WHERE project_id = ? AND status = 'running'", Int::class.java, row.id) ?: 0
        return Project(row.id, row.name, row.root, branch(root), flow, phase, acs, waiting, running)
    }

    private fun acCounts(thread: JsonNode?): List<Int> {
        val list = thread?.get("acs")
        if (list != null && list.isArray && list.size() > 0) {
            return listOf(list.count { it.get("status")?.asText() == "done" }, list.size())
        }
        return listOf(0, 0)
    }
}

/** Marker appended to a parked workspace project's root. */
const val PARKED = "#parked:"
