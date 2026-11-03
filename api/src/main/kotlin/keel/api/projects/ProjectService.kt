package keel.api.projects

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.common.Proc
import keel.api.common.Slug
import keel.api.common.Time
import keel.api.engine.EngineClient
import keel.api.inbox.InboxService
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.util.concurrent.Executors

data class ProjectRow(val id: String, val name: String, val root: String)

/** v0.15.7 a folder the bulk add did not add, and why. */
data class Skipped(val root: String, val why: String)

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
    /** v0.7.x: flows that run or wait, in the project folder and in worktrees */
    val flows: Int = 0,
    /** keel 0.18.55 keel knows this project: it has a .keel/config.yml (the init flow writes it) */
    val configured: Boolean = false,
    /** 0.22.0 the folder on the person's computer ([root] is where keel sees it); null when keel does not know it */
    val host: String? = null,
)

/**
 * keel 0.18.57 where the main project folder is on the person's computer ([host], KEEL_HOST_WORKSPACE from keel2) and
 * where this keel sees it ([container], KEEL_WORKSPACE): the plain start mounts it at /workspace, --docker at its own
 * path, native runs on it. A project is one folder on the person's computer, whichever way keel sees it.
 */
data class HostMount(val container: String, val host: String) {
    private val c = container.trim().trimEnd('/')
    private val h = host.trim().trimEnd('/')

    /** Whether keel knows where its folder is on the person's computer; unknown, a path says nothing about the folder. */
    val known: Boolean get() = c.isNotEmpty() && h.isNotEmpty()

    /** The folder's path on the person's computer (a folder outside the main one is mounted at its own path). */
    fun hostOf(root: String): String = when {
        c.isEmpty() || h.isEmpty() -> root
        root == c -> h
        root.startsWith("$c/") -> h + root.removePrefix(c)
        else -> root
    }

    /**
     * A project saved before keel knew host paths (0.18.56 and older): a repo in /workspace/<repo> is taken to be the
     * same repo in the main folder now (as the engine's relocate does for flows). /workspace itself is not guessed:
     * the main project is known by its name there (ProjectService.registerWorkspace).
     */
    fun legacyHostOf(root: String): String = when {
        h.isEmpty() -> root
        root.startsWith("$LEGACY_MOUNT/") -> h + root.removePrefix(LEGACY_MOUNT)
        else -> root
    }

    companion object {
        const val LEGACY_MOUNT = "/workspace"
    }
}

@Service
class ProjectService(
    private val jdbc: JdbcTemplate,
    private val mapper: ObjectMapper,
    private val engine: EngineClient,
    /** The Inbox needs the project list, so it is looked up when a count is asked for. */
    private val inbox: ObjectProvider<InboxService>,
    props: keel.api.common.KeelProperties,
    private val kv: keel.api.common.KvStore,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    /** 0.18.57 how this keel sees the main project folder (tests set another one). */
    var mount = HostMount(
        props.workspace.takeIf { it.isNotBlank() }?.let { Paths.get(it).toAbsolutePath().normalize().toString() } ?: "",
        props.hostWorkspace,
    )

    private fun hostKey(id: String) = "project.host.$id"

    /** Saves where the project's folder is on the person's computer, so another mount finds it again. */
    private fun rememberHost(row: ProjectRow): ProjectRow {
        if (mount.known && !row.root.contains(PARKED)) runCatching { kv.put(hostKey(row.id), mount.hostOf(row.root)) }
        return row
    }

    /**
     * 0.18.57 the project whose folder on the person's computer is the one at [root], seen through another mount
     * before (/workspace/shop then, ~/Projects/shop now, or back): it moves to [root] with its id, history, Jira
     * connection and caps, instead of a new "<name>-2". Only a project keel cannot reach where it was saved.
     */
    private fun movedHere(root: String): ProjectRow? {
        if (!mount.known) return null
        val host = mount.hostOf(root)
        val rows = jdbc.query("SELECT id, name, root FROM projects WHERE root <> ?", { rs, _ -> ProjectRow(rs.getString(1), rs.getString(2), rs.getString(3)) }, root)
        fun gone(r: ProjectRow) = !Files.exists(Paths.get(r.root.substringBefore(PARKED)).resolve(".git"))
        val known = rows.associateWith { r -> runCatching { kv.get<String>(hostKey(r.id)) }.getOrNull() }
        val row = rows.firstOrNull { r -> known[r] == host && gone(r) }
            ?: rows.firstOrNull { r -> known[r] == null && !r.root.contains(PARKED) && mount.legacyHostOf(r.root) == host && gone(r) }
            ?: return null
        jdbc.update("UPDATE projects SET root = ?, hidden = 0 WHERE id = ?", root, row.id)
        log.info("project {} is at {} now (it was {})", row.id, root, row.root)
        return rememberHost(row.copy(root = root))
    }

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
        jdbc.query("SELECT id, name, root FROM projects WHERE root NOT LIKE '%$PARKED%' AND hidden = 0 ORDER BY name") { rs, _ -> ProjectRow(rs.getString(1), rs.getString(2), rs.getString(3)) }
            .filter { java.nio.file.Files.isDirectory(Paths.get(it.root)) }

    /**
     * The mounted folder (/workspace) is the same path for every project the launcher starts, so it is known by
     * its name: another name there parks the old project (history kept, not listed) and the same name re-attaches it.
     */
    fun registerWorkspace(rootText: String, name: String, scan: Boolean = true): ProjectRow {
        val root = Paths.get(rootText).toAbsolutePath().normalize().toString()
        val id = keel.api.common.Slug.of(name)
        val here = jdbc.query("SELECT id, name, root FROM projects WHERE root = ?", { rs, _ -> ProjectRow(rs.getString(1), rs.getString(2), rs.getString(3)) }, root).firstOrNull()
        if (here != null && (here.id == id || mount.known && mount.hostOf(root) == runCatching { kv.get<String>(hostKey(here.id)) }.getOrNull())) return rememberHost(here)
        if (here != null) jdbc.update("UPDATE projects SET root = ? WHERE id = ?", "$root$PARKED${here.id}", here.id)
        // 0.18.57 the same folder on the person's computer, seen through another mount before (plain <-> --docker)
        movedHere(root)?.let { moved -> return moved.also { if (scan) triggerScan(it) } }
        val parked = find(id)
        if (parked != null) {
            jdbc.update("UPDATE projects SET root = ?, name = ? WHERE id = ?", root, name, id)
            return rememberHost(ProjectRow(id, name, root)).also { if (scan) triggerScan(it) }
        }
        return register(root, name, scan)
    }

    fun find(pid: String): ProjectRow? =
        jdbc.query("SELECT id, name, root FROM projects WHERE id = ?", { rs, _ -> ProjectRow(rs.getString(1), rs.getString(2), rs.getString(3)) }, pid).firstOrNull()

    fun require(pid: String): ProjectRow =
        find(pid) ?: throw NotFound("No project called \"$pid\"", "GET /api/projects lists the projects keel knows.")

    fun root(pid: String): Path = Paths.get(require(pid).root)

    /** v0.15.7 every project's folder (not a parked one) → its id, to mark the repos that are projects already. */
    fun idsByRoot(): Map<String, String> =
        jdbc.query("SELECT id, root FROM projects WHERE root NOT LIKE '%$PARKED%'") { rs, _ -> rs.getString(2) to rs.getString(1) }.toMap()

    /**
     * v0.15.7 registers many folders at once, each like [register] (the same checks). A folder that is a project
     * already, or that [register] refuses, is skipped with the reason; the rest are added and scanned.
     */
    fun registerMany(roots: List<String>, names: Map<String, String> = emptyMap()): Pair<List<ProjectRow>, List<Skipped>> {
        val added = mutableListOf<ProjectRow>()
        val skipped = mutableListOf<Skipped>()
        val ids = idsByRoot()
        for (text in roots.map { it.trim() }.distinct()) {
            val norm = text.takeIf { it.isNotEmpty() }?.let { Paths.get(it).toAbsolutePath().normalize().toString() }
            val already = norm?.let { ids[it] } ?: added.firstOrNull { it.root == norm }?.id
            if (already != null) {
                skipped += Skipped(text, "already a project ($already)")
                continue
            }
            try {
                added += register(text, names[text])
            } catch (e: keel.api.common.ApiException) {
                skipped += Skipped(text, e.message)
            }
        }
        return added to skipped
    }

    /** Registers a folder (a new one is scanned). Same root again returns the existing project. */
    fun register(rootText: String, name: String? = null, scan: Boolean = true): ProjectRow {
        if (rootText.isBlank()) throw BadRequest("root is empty", "Send the absolute path of the repo inside the container.")
        val root = Paths.get(rootText).toAbsolutePath().normalize()
        if (!Files.isDirectory(root)) throw BadRequest(notMounted(root.toString()))
        val existing = jdbc.query("SELECT id, name, root FROM projects WHERE root = ?", { rs, _ -> ProjectRow(rs.getString(1), rs.getString(2), rs.getString(3)) }, root.toString()).firstOrNull()
        if (existing != null) {
            // v0.16.0 a removed project (hidden) comes back with its history when its folder is added again
            jdbc.update("UPDATE projects SET hidden = 0 WHERE id = ? AND hidden = 1", existing.id)
            return rememberHost(existing)
        }
        // 0.18.57 the same folder on the person's computer, seen through another mount before (plain <-> --docker)
        movedHere(root.toString())?.let { moved -> return moved.also { if (scan) triggerScan(it) } }
        val base = Slug.of(name?.takeIf { it.isNotBlank() } ?: root.fileName?.toString() ?: "project")
        var id = base
        var n = 2
        while (find(id) != null) id = "$base-${n++}"
        val display = name?.takeIf { it.isNotBlank() } ?: root.fileName?.toString() ?: id
        jdbc.update("INSERT INTO projects(id, name, root, created_at) VALUES (?, ?, ?, ?)", id, display, root.toString(), Time.now())
        return rememberHost(ProjectRow(id, display, root.toString())).also { if (scan) triggerScan(it) }
    }

    /** v0.8.0: a quality run's eval project: it runs flows like any project, but no list, Inbox or notification shows it. */
    fun registerHidden(root: Path, name: String): ProjectRow {
        val base = Slug.of(name)
        var id = base
        var n = 2
        while (find(id) != null) id = "$base-${n++}"
        jdbc.update("INSERT INTO projects(id, name, root, created_at, hidden) VALUES (?, ?, ?, ?, 1)", id, name, root.toAbsolutePath().normalize().toString(), Time.now())
        return ProjectRow(id, name, root.toAbsolutePath().normalize().toString())
    }

    /** v0.16.0 off the list, history kept (DELETE /api/projects/{pid}). Not while a flow runs or waits there. */
    fun remove(pid: String) {
        require(pid)
        val busy = jdbc.queryForObject("SELECT COUNT(*) FROM threads WHERE project_id = ? AND status IN ('running','waiting')", Int::class.java, pid) ?: 0
        if (busy > 0) throw keel.api.common.Conflict("A flow runs or waits on $pid", "Stop it first, or let it finish.")
        jdbc.update("UPDATE projects SET hidden = 1 WHERE id = ?", pid)
    }

    fun hidden(pid: String): Boolean =
        (jdbc.queryForObject("SELECT COALESCE(MAX(hidden), 0) FROM projects WHERE id = ?", Int::class.java, pid) ?: 0) == 1

    /** The phase of the project's running or waiting flow; "none" when no flow is active. */
    fun activePhase(pid: String): String = jdbc.query(
        "SELECT phase FROM threads WHERE project_id = ? AND worktree IS NULL AND status IN ('running','waiting') ORDER BY updated_at DESC LIMIT 1",
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
            "SELECT workflow_id, status, phase, state_json FROM threads WHERE project_id = ? AND worktree IS NULL ORDER BY CASE WHEN status IN ('running','waiting') THEN 0 ELSE 1 END, updated_at DESC LIMIT 1",
            { rs, _ -> listOf(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4)) }, row.id,
        ).firstOrNull()
        val active = thread != null && thread[1] in setOf("running", "waiting")
        val threadState = thread?.get(3)?.let { runCatching { mapper.readTree(it) }.getOrNull() }

        // Only a running or waiting flow gives the project a flow, a phase and AC counts.
        val flow = if (active) thread!![0] else null
        val phase = (if (active) thread!![2] else null) ?: "none"
        val acs = acCounts(if (active) threadState else null)
        // what waits for a person: the Inbox's count (flows at a pause, and every Inbox source: approvals, task items, ...)
        val waiting = inbox.getObject().waiting(row.id)
        val running = jdbc.queryForObject("SELECT COUNT(*) FROM agent_calls WHERE project_id = ? AND status = 'running'", Int::class.java, row.id) ?: 0
        val flows = jdbc.queryForObject("SELECT COUNT(*) FROM threads WHERE project_id = ? AND status IN ('running','waiting')", Int::class.java, row.id) ?: 0
        val host = if (mount.known) mount.hostOf(row.root) else runCatching { kv.get<String>(hostKey(row.id)) }.getOrNull()
        return Project(row.id, row.name, row.root, branch(root), flow, phase, acs, waiting, running, flows, configured(root), host)
    }

    /** keel 0.18.55 the same test as the engine's (rules/__init__.py `configured`): the project has a .keel/config.yml. */
    fun configured(root: Path): Boolean = Files.isRegularFile(root.resolve(".keel").resolve("config.yml"))

    private fun acCounts(thread: JsonNode?): List<Int> {
        val list = thread?.get("acs")
        if (list != null && list.isArray && list.size() > 0) {
            return listOf(list.count { it.get("status")?.asText() == "done" }, list.size())
        }
        return listOf(0, 0)
    }
}

/** v0.15.8 a folder keel cannot see (not mounted): what the person does about it, in one sentence. */
fun notMounted(path: String) = "keel can't see $path. Run `keel2 add $path` in a terminal first, then add it here."

/** Marker appended to a parked workspace project's root. */
const val PARKED = "#parked:"
