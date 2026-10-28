package keel.api.knowledge

import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.Hash
import keel.api.common.Json
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.events.EngineEvent
import keel.api.projects.ProjectService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Path
import java.util.UUID

data class DocVersion(
    val id: String, val path: String, val kind: String, val version: Int, val sha256: String, val size: Int,
    val deleted: Boolean, val masked: Boolean, val byKind: String, val byName: String?, val via: String?, val threadId: String?,
    val step: String?, val phase: String?, val commitSha: String?, val at: String, val content: String? = null,
)
data class DocFile(val path: String, val kind: String, val versions: Int, val at: String, val state: String,
    /** state "conflict": [newest version id, the other thread's version id that was overwritten]. */
    val conflict: List<String>? = null)
data class Applied(val kind: String, val ref: String, val subject: String?, val files: List<String>, val at: String)
data class FlowApplied(val threadId: String, val items: List<Applied>)
data class DocDiff(val path: String, val from: String, val to: String, val diff: String)
data class RestoreBody(val versionId: String = "")

/**
 * v0.15.9 keel's file history (V15__doc_history.sql): every version of the specs, knowledge sections, ADRs and the
 * runbook keel's flows wrote (engine runtime/doc_history.py sends `doc.version`), and what each flow applied
 * (`keel.applied`, plus the branch and worktree the api makes). The file on disk is the current text; this is history.
 */
@Service
class DocHistoryService(private val jdbc: JdbcTemplate, private val projects: ProjectService) {

    // ---- written from engine events (EventService) ------------------------------------------

    /** Stores a `doc.version` and returns the event without its content (the events table and SSE never carry it).
     *  The same text as the newest version is not a new version, unless `always` (a restore is always one). */
    @Synchronized   // one lock for the engine's events and a restore: the next version number is read, then written
    fun record(e: EngineEvent, always: Boolean = false): EngineEvent {
        val d = e.data
        val path = d["path"]?.toString()?.takeIf { it.isNotBlank() } ?: return e.copy(data = d - "content")
        val sha = d["sha256"]?.toString() ?: ""
        val newest = newest(e.projectId, path)
        val deleted = d["deleted"] == true
        if (!always && newest != null && newest.sha256 == sha && newest.deleted == deleted) return e.copy(data = d - "content")
        val content = d["content"]?.toString()
        jdbc.update(
            """INSERT INTO doc_versions(id, project_id, path, kind, version, sha256, content, size, deleted, masked, by_kind, by_name, via,
               thread_id, step, phase, commit_sha, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            "dv-" + UUID.randomUUID().toString().take(8), e.projectId, path, d["kind"]?.toString() ?: kindOf(path),
            (newest?.version ?: 0) + 1, sha, content, (d["size"] as? Number)?.toInt() ?: (content?.length ?: 0), if (deleted) 1 else 0, if (d["masked"] == true) 1 else 0,
            d["by_kind"]?.toString() ?: "keel", d["by_name"]?.toString(), d["via"]?.toString(), e.threadId.ifBlank { null },
            e.step ?: d["step"]?.toString(), d["phase"]?.toString(), null, e.at ?: Time.now(),
        )
        return e.copy(data = d - "content")
    }

    /** `keel.applied`: a commit (its files get the sha on their newest version), or files recorded instead of committed. */
    fun applied(e: EngineEvent) {
        val d = e.data
        @Suppress("UNCHECKED_CAST")
        val files = (d["files"] as? List<Any?>)?.mapNotNull { it?.toString() } ?: emptyList()
        val kind = d["kind"]?.toString() ?: "commit"
        val ref = d["sha"]?.toString() ?: d["ref"]?.toString() ?: files.firstOrNull() ?: return
        add(e.projectId, e.threadId, kind, ref, d["subject"]?.toString(), files, e.at)
        if (kind == "commit") {
            for (f in files) {
                jdbc.update(
                    "UPDATE doc_versions SET commit_sha = ? WHERE project_id = ? AND path = ? AND commit_sha IS NULL AND " +
                        "version = (SELECT MAX(version) FROM doc_versions WHERE project_id = ? AND path = ?)",
                    ref, e.projectId, f, e.projectId, f,
                )
            }
        }
    }

    /** A row of what a flow applied (the api's own: the branch and the worktree it made at the start). */
    fun add(pid: String, tid: String, kind: String, ref: String, subject: String?, files: List<String> = emptyList(), at: String? = null) {
        jdbc.update(
            // an engine retry sends the same keel.applied again: one row per (thread, kind, ref)
            "INSERT OR IGNORE INTO flow_applied(project_id, thread_id, kind, ref, subject, files_json, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            pid, tid, kind, ref, subject, if (files.isEmpty()) null else Json.write(files), at ?: Time.now(),
        )
    }

    // ---- read ------------------------------------------------------------------------------

    fun files(pid: String): List<DocFile> {
        val root = projects.root(pid)
        val rows = jdbc.query(
            // one row per path, with the kind of its newest version (a layout change can give a path another kind)
            "SELECT d.path, (SELECT kind FROM doc_versions n WHERE n.project_id = d.project_id AND n.path = d.path ORDER BY version DESC LIMIT 1), " +
                "COUNT(*), MAX(d.at), MAX(d.version) FROM doc_versions d WHERE d.project_id = ? GROUP BY d.path ORDER BY d.path",
            { rs, _ -> listOf(rs.getString(1), rs.getString(2), rs.getInt(3).toString(), rs.getString(4)) }, pid,
        )
        return rows.map { (path, kind, n, at) ->
            val st = state(pid, root, path)
            val two = if (st == "ok") conflictOf(pid, path) else null
            DocFile(path, kind, n.toInt(), at, if (two != null) "conflict" else st, two)
        }
    }

    /** ok | missing (versions, no file: Restore) | changed (the file differs from the newest version) | deleted |
     *  unreadable (the file is there but cannot be read). A file can vanish between the check and the read (on Docker
     *  Desktop's file sharing a deleted file still looks present for a moment): then it is missing. */
    fun state(pid: String, root: Path, path: String): String {
        val last = newest(pid, path) ?: return "ok"
        val f = root.resolve(path)
        if (!Files.isRegularFile(f)) return if (last.deleted) "deleted" else "missing"
        val bytes = try {
            Files.readAllBytes(f)
        } catch (e: java.io.IOException) {
            return if (Files.exists(f)) "unreadable" else if (last.deleted) "deleted" else "missing"
        }
        return if (sha256(bytes) == last.sha256) "ok" else "changed"
    }

    /** The two newest versions written by different flows (both known) while the file is the newest text: the other
     *  flow's text was overwritten. [newest id, other id], else null. */
    private fun conflictOf(pid: String, path: String): List<String>? {
        val two = jdbc.query("$SELECT WHERE project_id = ? AND path = ? ORDER BY version DESC LIMIT 2", { rs, _ -> row(rs, false) }, pid, path)
        if (two.size < 2) return null
        val (a, b) = two
        val differ = a.threadId != null && b.threadId != null && a.threadId != b.threadId
        return if (differ && !a.deleted && !b.deleted && a.sha256 != b.sha256) listOf(a.id, b.id) else null
    }

    fun versions(pid: String, path: String): List<DocVersion> {
        projects.require(pid)
        return jdbc.query("$SELECT WHERE project_id = ? AND path = ? ORDER BY version DESC", { rs, _ -> row(rs, false) }, pid, path)
    }

    fun version(pid: String, id: String): DocVersion =
        jdbc.query("$SELECT WHERE project_id = ? AND id = ?", { rs, _ -> row(rs, true) }, pid, id).firstOrNull()
            ?: throw NotFound("No version $id")

    fun diff(pid: String, path: String, from: String, to: String): DocDiff {
        val root = projects.root(pid)
        // only keel's own files, and only one with a history here: never any other file of the computer
        if (!known(root, path) || newest(pid, path) == null) throw BadRequest("$path is not one of keel's files with a history here")
        fun text(ref: String): String = when (ref) {
            "file" -> inside(root, path)?.takeIf { Files.isRegularFile(it) }?.let { Files.readString(it) } ?: ""
            else -> version(pid, ref).also { if (it.path != path) throw BadRequest("$ref is a version of ${it.path}") }.content ?: ""
        }
        return DocDiff(path, from, to, unified(text(from), text(to), path))
    }

    fun flow(tid: String): FlowApplied = FlowApplied(tid, jdbc.query(
        "SELECT kind, ref, subject, files_json, at FROM flow_applied WHERE thread_id = ? ORDER BY at, id", { rs, _ ->
            Applied(rs.getString(1), rs.getString(2), rs.getString(3),
                rs.getString(4)?.let { runCatching { Json.read<List<String>>(it) }.getOrNull() } ?: emptyList(), rs.getString(5))
        }, tid,
    ))

    // ---- restore ---------------------------------------------------------------------------

    /** Writes a version back to its file (only a path the layout knows), as a new version by the person. */
    @Synchronized
    fun restore(pid: String, versionId: String): DocVersion {
        val v = version(pid, versionId)
        val root = projects.root(pid)
        if (!known(root, v.path)) throw BadRequest("${v.path} is not one of keel's files here", "Only specs, knowledge, ADRs and the runbook can be restored.")
        val phase = jdbc.query(
            "SELECT phase FROM threads WHERE project_id = ? AND worktree IS NULL AND status IN ('running','waiting') ORDER BY updated_at DESC LIMIT 1",
            { rs, _ -> rs.getString(1) }, pid,
        ).firstOrNull()
        if (phase != null && (phase in setOf("red", "green", "gate") || phase.endsWith("gate"))) {
            throw Conflict("A flow is in $phase on this folder, so its files are frozen", "Wait until it is past the criteria loop, or stop it.")
        }
        val target = inside(root, v.path) ?: throw BadRequest("${v.path} leads outside the project", "A symlink there is not followed.")
        val content = v.content ?: throw Conflict("Version ${v.version} of ${v.path} has no stored text (it was too big or a deletion)")
        if (v.masked) throw Conflict("Version ${v.version} of ${v.path} had lines that look like secrets, and keel stored them masked",
            "Restoring it would write the masks into the file. Copy the parts you need from the History by hand.")
        Files.createDirectories(target.parent)
        Files.writeString(target, content)
        record(EngineEvent("doc.version", "", pid, null, Time.now(), null, mapOf(
            "path" to v.path, "kind" to v.kind, "sha256" to sha256(content.toByteArray()), "content" to content,
            "size" to content.toByteArray().size, "by_kind" to "user", "by_name" to "restore of version ${v.version}",
        )), always = true)
        return versions(pid, v.path).first()
    }

    /** The file for a keel path, unless it is a symlink or its folder leads outside the project (or, in the external
     *  layout, outside the folder .keel links to). Null: refuse. */
    private fun inside(root: Path, path: String): Path? {
        val f = root.resolve(path).normalize()
        if (!f.startsWith(root.normalize()) || Files.isSymbolicLink(f)) return null
        val parent = generateSequence(f.parent) { it.parent }.firstOrNull { Files.exists(it) } ?: return null
        val real = runCatching { parent.toRealPath() }.getOrNull() ?: return null
        val bases = listOfNotNull(runCatching { root.toRealPath() }.getOrNull(), runCatching { root.resolve(".keel").toRealPath() }.getOrNull())
        return f.takeIf { bases.any { real.startsWith(it) } }
    }

    private fun known(root: Path, path: String): Boolean {
        if (path.split('/').any { it == ".." } || path.startsWith("/")) return false
        val lay = Layout.resolve(root)
        val dir = path.substringBeforeLast('/', "")
        // 0.18.64 the clarify flow's documents (<specs>/clarify): the engine records them, so they can be restored too
        val clarify = dir.endsWith("/clarify") && dir.removeSuffix("/clarify") in lay.specDirs
        return path.endsWith(".md") && (dir in lay.folders() || clarify || path in (lay.runbooks + lay.runbook))
    }

    // ---- helpers ---------------------------------------------------------------------------

    private fun newest(pid: String, path: String): DocVersion? =
        jdbc.query("$SELECT WHERE project_id = ? AND path = ? ORDER BY version DESC LIMIT 1", { rs, _ -> row(rs, false) }, pid, path).firstOrNull()

    private fun row(rs: java.sql.ResultSet, withContent: Boolean) = DocVersion(
        rs.getString("id"), rs.getString("path"), rs.getString("kind"), rs.getInt("version"), rs.getString("sha256"),
        rs.getInt("size"), rs.getInt("deleted") == 1, rs.getInt("masked") == 1, rs.getString("by_kind"), rs.getString("by_name"), rs.getString("via"),
        rs.getString("thread_id"), rs.getString("step"), rs.getString("phase"), rs.getString("commit_sha"), rs.getString("at"),
        if (withContent) rs.getString("content") else null,
    )

    companion object {
        const val DIFF_CELLS = 4_000_000L
        private const val SELECT = "SELECT id, path, kind, version, sha256, size, deleted, masked, by_kind, by_name, via, thread_id, step, phase, " +
            "commit_sha, at, content FROM doc_versions"

        fun sha256(bytes: ByteArray): String = Hash.sha256Hex(bytes)

        fun kindOf(path: String): String = when {
            path.contains("/specs/") || path.startsWith("specs/") -> "spec"
            path.contains("knowledge/") -> "knowledge"
            path.contains("/adr/") -> "adr"
            else -> "runbook"
        }

        /** A small unified diff (whole files as one hunk): enough for a spec's few hundred lines. */
        fun unified(a: String, b: String, path: String): String {
            val x = a.lines()
            val y = b.lines()
            // the table is n × m: two big files would take hundreds of MB, so past this a diff is not drawn
            if (x.size.toLong() * y.size > DIFF_CELLS) return "--- a/$path\n+++ b/$path\n(too big to compare here: ${x.size} and ${y.size} lines)\n"
            val n = x.size
            val m = y.size
            val lcs = Array(n + 1) { IntArray(m + 1) }
            for (i in n - 1 downTo 0) for (j in m - 1 downTo 0) {
                lcs[i][j] = if (x[i] == y[j]) lcs[i + 1][j + 1] + 1 else maxOf(lcs[i + 1][j], lcs[i][j + 1])
            }
            val out = StringBuilder("--- a/$path\n+++ b/$path\n@@ -1,$n +1,$m @@\n")
            var i = 0
            var j = 0
            while (i < n && j < m) {
                when {
                    x[i] == y[j] -> { out.append(' ').append(x[i]).append('\n'); i++; j++ }
                    lcs[i + 1][j] >= lcs[i][j + 1] -> { out.append('-').append(x[i]).append('\n'); i++ }
                    else -> { out.append('+').append(y[j]).append('\n'); j++ }
                }
            }
            while (i < n) out.append('-').append(x[i++]).append('\n')
            while (j < m) out.append('+').append(y[j++]).append('\n')
            return out.toString()
        }
    }
}
