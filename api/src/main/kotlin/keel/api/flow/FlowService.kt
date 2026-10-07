package keel.api.flow

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.agents.AgentService
import keel.api.agents.Knowledge
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.KeelProperties
import keel.api.common.Slug
import keel.api.repo.RepoService
import java.nio.file.Path
import java.nio.file.Paths
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.connections.SecretService
import keel.api.doctor.WorkspaceDoctor
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.events.EventHub
import keel.api.mcp.McpServerSpec
import keel.api.mcp.McpService
import keel.api.projects.ProjectService
import keel.api.budget.CapPlanner
import keel.api.budget.ProviderUsageService
import keel.api.settings.Model
import keel.api.settings.SettingsService
import keel.api.skills.SkillService
import keel.api.workflows.Workflow
import keel.api.workflows.WorkflowService
import org.springframework.http.HttpStatus
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service

data class Ac(val id: String = "", val layer: String = "API", val title: String = "")

data class ThreadSettings(
    val gatesMode: String,
    /** v0.4.1: manual | important | auto | readonly (the engine's runtime/run_mode.py). */
    val runMode: String = "manual",
    val capTokens: Int,
    val onCap: String,
    val cheaperModel: Model?,
    val usageWarn: Double? = null,
    val usagePause: Double? = null,
    /** The latest plan windows keel knows (provider_usage), for the engine's pause rule before each agent. */
    val providerWindows: List<Map<String, Any?>>? = null,
    /** v0.4.2 project caps (budget/CapPlanner.kt): the flow's dollar cap against its reported cost, and what it does. */
    val capUsd: Double? = null,
    val onCapUsd: String? = null,
    /** Every step's token limit (a step's own smaller max_tokens still wins), and what it does. */
    val stepCapTokens: Int? = null,
    val stepOnCap: String? = null,
    /** Who keel's commits are by ("Name <email>", null: the project's git name) and whether KeelBot co-authors them. */
    val commitAuthor: String? = null,
    val commitCoauthor: Boolean? = null,
)

/** The engine's StartThread (CONTRACT "Shared types"). */
data class StartThread(
    val projectId: String,
    val root: String,
    val workflow: Workflow,
    val title: String,
    val acs: List<Ac>?,
    val models: Map<String, Model>,
    val settings: ThreadSettings,
    val mcp: List<McpServerSpec>,
    val skills: Map<String, String>,
    /** Per agent: what it uses besides its prompt (its knowledge setting). */
    val agents: Map<String, AgentStart> = emptyMap(),
    /** provider -> API key for models that run in "api" mode; the engine keeps them in memory only. */
    val keys: Map<String, String>? = null,
    /** What the user asked for, in their words. */
    val request: String? = null,
    /** Flow inputs (state.data): review lens and base, fix no_gates, ... */
    val data: Map<String, Any?>? = null,
    /** What the project's caps changed for this flow, in words (returned as cap_note; never sent to the engine). */
    @get:com.fasterxml.jackson.annotation.JsonIgnore
    val capNote: String? = null,
)

/** One agent's entry in StartThread.agents. */
data class AgentStart(val knowledge: Knowledge)

/** Per-flow cap (and v0.4.1 run mode) from POST /flows; null fields fall back to the project's settings. */
data class FlowCap(val capTokens: Int? = null, val onCap: String? = null, val runMode: String? = null) {
    fun check() {
        if (capTokens != null && capTokens <= 0) throw BadRequest("cap_tokens must be above 0")
        if (onCap != null && onCap !in setOf("pause", "cheaper", "stop")) throw BadRequest("on_cap cannot be \"$onCap\"", "Pick one of: pause, cheaper, stop")
        checkRunMode(runMode)
    }
}

/** A run mode the engine knows, or a 400 that lists them. */
fun checkRunMode(mode: String?) {
    if (mode != null && mode !in keel.api.settings.Settings.RUN_MODES) {
        throw BadRequest("run_mode cannot be \"$mode\"", "Pick one of: ${keel.api.settings.Settings.RUN_MODES.joinToString()}")
    }
}

data class FlowView(val thread: JsonNode?, val workflow: Workflow?)

/** v0.7.x: one flow on the project's board. where = folder (the project folder) | worktree (its own, next to others). */
data class BoardFlow(
    val threadId: String, val title: String, val workflowId: String?, val status: String, val phase: String?,
    val current: String?, val waiting: JsonNode?, val where: String, val worktree: String?, val branch: String?,
    /** the files it changed against the base branch (committed on its branch or not yet) */
    val files: List<String>, val updatedAt: String?,
    /** a finished flow whose worktree is still there (Remove the worktree; its branch stays) */
    val worktreeLeft: Boolean = false,
)
/** A file two or more flows change: their merges will meet there. */
data class FlowOverlap(val file: String, val flows: List<String>)
/** Two flows' branches that do not merge cleanly (git merge-tree), and where. */
data class FlowConflict(val a: String, val b: String, val files: List<String>)
/** The project's flows side by side, what they share, and an order to merge them in (fewest conflicts first). */
data class FlowBoard(val flows: List<BoardFlow>, val overlaps: List<FlowOverlap>, val conflicts: List<FlowConflict>, val order: List<String>)

@Service
class FlowService(
    private val jdbc: JdbcTemplate,
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val workflows: WorkflowService,
    private val settings: SettingsService,
    private val agents: AgentService,
    private val skills: SkillService,
    private val mcp: McpService,
    private val hub: EventHub,
    private val mapper: ObjectMapper,
    private val secrets: SecretService,
    private val repo: RepoService,
    private val props: KeelProperties,
    private val providerUsage: ProviderUsageService,
    private val notifications: keel.api.notifications.NotificationService,
    private val capPlanner: CapPlanner,
) {
    private val log = org.slf4j.LoggerFactory.getLogger(javaClass)

    /** Builds StartThread from the workflow + effective settings + agent models + MCP + skills. */
    fun buildStart(pid: String, workflowId: String, title: String, acs: List<Ac>?, cap: FlowCap? = null, model: Model? = null): StartThread {
        val project = projects.require(pid)
        val wf = workflows.get(workflowId)
        val s = settings.effective(pid)
        val all = agents.list(pid)
        val byId = all.associateBy { it.id }
        val used = wf.steps.flatMap { st -> listOfNotNull(st.agent) + st.lanes.orEmpty().filter { it.kind == "agent" }.mapNotNull { it.sub } }.distinct()
        val off = used.filter { byId[it]?.enabled == false }
        if (off.isNotEmpty()) throw BadRequest("These agents are turned off for this project: ${off.joinToString()}", "Turn them on in Agents, or change the workflow.")

        val models = linkedMapOf("default" to s.defaultModel)
        all.filter { it.enabled }.forEach { models[it.id] = it.model }
        // v0.8.0: a quality run measures one model: every agent of this flow uses it
        if (model != null) models.keys.toList().forEach { models[it] = model }

        // Every cap of the project: the smallest one left binds. A used-up cap refuses the start (pause, stop) or starts
        // every agent on the cheaper model (cheaper) — never the fake model for a flow that runs real models.
        val cheaperOk = s.cheaperModel.provider != "fake" || models.values.all { it.provider == "fake" }
        val limits = capPlanner.limits(pid, cap?.capTokens, cap?.onCap, cheaperOk)
        limits.refused?.let { throw Conflict(it.error, it.hint) }
        if (limits.cheaper) models.keys.toList().forEach { models[it] = s.cheaperModel }

        val allow = mcp.allow(pid)
        val steps = wf.steps.map { st ->
            val a = st.agent
            var next = if (st.tools == null && a != null && allow[a] != null) st.copy(tools = allow[a]) else st
            // An agent's lane ("api" | "web") rides along as step metadata; "follow" leaves the step as it is.
            val lane = a?.let { byId[it]?.lane }?.takeIf { it != "follow" }
            if (lane != null) next = next.copy(lane = lane)
            next
        }
        val skillText = used.mapNotNull { id ->
            val ids = byId[id]?.skills.orEmpty()
            if (ids.isEmpty()) null else id to skills.textFor(pid, ids)
        }.filter { it.second.isNotBlank() }.toMap()

        return StartThread(
            projectId = pid, root = project.root, workflow = wf.copy(steps = steps), title = title, acs = acs?.takeIf { it.isNotEmpty() },
            models = models,
            settings = ThreadSettings(s.gatesMode, cap?.runMode ?: s.runMode, limits.capTokens, limits.onCap, s.cheaperModel,
                s.usageWarn, s.usagePause, providerUsage.windowsForEngine().takeIf { it.isNotEmpty() },
                limits.capUsd, limits.onCapUsd, limits.stepCapTokens, limits.stepOnCap,
                commitAuthor = s.commitAuthor.trim().ifBlank { null }, commitCoauthor = s.commitCoauthor),
            mcp = mcp.specsFor(s.mcp), skills = skillText,
            agents = all.filter { it.enabled }.associate { it.id to AgentStart(it.knowledge) },
            capNote = limits.notes.takeIf { it.isNotEmpty() }?.joinToString(" "),
        )
    }

    fun start(pid: String, workflowId: String, title: String, acs: List<Ac>?, cap: FlowCap? = null,
              allowFake: Boolean = false, allowDirty: Boolean = false, request: String? = null,
              options: Map<String, Any?>? = null, where: String? = null, model: Model? = null): JsonNode {
        if (title.isBlank()) throw BadRequest("Give the flow a title", "One short line: what should this flow build or fix?")
        cap?.check()
        var start = buildStart(pid, workflowId, title, acs, cap, model)
        val root = Paths.get(start.root)
        refuseFake(start, root, allowFake)
        // the project folder, or (v0.7.x) a worktree of its own next to the flow that runs or waits there
        val wt = if (inWorktree(pid, where)) worktreeFor(pid, root, title) else null
        if (wt == null) {
            refuseDirty(root, allowDirty)
            ownBranch(pid, root, title)
        } else {
            start = start.copy(root = wt.path)
        }
        // API keys for "api" models and CLI logins for subscription models, from the encrypted secrets table; the
        // cheaper model's too, so a cap that switches to it mid-flow can run it.
        val keys = keysFor(start.models.values + listOfNotNull(start.settings.cheaperModel))
        val withRequest = start.copy(request = request?.trim()?.takeIf { it.isNotEmpty() }?.take(8000),
            data = options?.takeIf { it.isNotEmpty() })
        val body = if (keys.isEmpty()) withRequest else withRequest.copy(keys = keys)
        val res = try {
            engine.startThread(body)
        } catch (e: Exception) {
            wt?.let { removeWorktreeAt(root, it.name) }
            throw e
        }
        val tid = res.get("thread_id")?.asText() ?: throw ApiException(HttpStatus.BAD_GATEWAY, "The engine did not return a thread id")
        val now = Time.now()
        jdbc.update(
            "INSERT OR IGNORE INTO threads(id, project_id, workflow_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'running', ?, ?)",
            tid, pid, workflowId, title, now, now,
        )
        jdbc.update("UPDATE threads SET workflow_id = ?, title = ?, project_id = ? WHERE id = ?", workflowId, title, pid, tid)
        jdbc.update("UPDATE threads SET worktree = ?, branch = ? WHERE id = ?", wt?.name, wt?.branch, tid)
        val state = runCatching { engine.thread(tid) }.getOrElse { res }
        save(tid, state)
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        val note = start.capNote
        return if (note != null && state is com.fasterxml.jackson.databind.node.ObjectNode) state.deepCopy().put("cap_note", note) else state
    }

    private fun isDemo(root: Path) = root.toAbsolutePath().normalize() == props.dataDir.resolve("demo").toAbsolutePath().normalize()

    /** The fake model writes example files and commits them; on a real project that must be a conscious choice. */
    private fun refuseFake(start: StartThread, root: Path, allowFake: Boolean) {
        if (allowFake || props.fakeOnRealProjects || isDemo(root)) return
        val used = start.workflow.steps.flatMap { st -> listOfNotNull(st.agent) + st.lanes.orEmpty().filter { it.kind == "agent" }.mapNotNull { it.sub } }.distinct()
        val fake = used.filter { (start.models[it] ?: start.models["default"])?.provider == "fake" }
        if (fake.isEmpty()) return
        throw Conflict(
            "These agents would use the fake model: ${fake.joinToString()}. It writes example files, not real code.",
            "Pick a real model in Control › Connections (Use for all agents) or Settings › Models. To try the flow anyway, tick \"Run with the fake model\".",
        )
    }

    /** keel commits only what its agents change; uncommitted work in the tree is a reason to stop and ask first. */
    private fun refuseDirty(root: Path, allowDirty: Boolean) {
        if (allowDirty) return
        val r = repo.git(root, "status", "--porcelain", "--untracked-files=all")
        if (!r.ok) return
        val files = r.out.lines().filter { it.length > 3 }.map { it.substring(3).trim() }
            .filterNot { WorkspaceDoctor.isEngineFile(it) }
        if (files.isEmpty()) return
        throw Conflict(
            "This project has uncommitted changes (${files.size} file${if (files.size == 1) "" else "s"}): ${files.take(5).joinToString()}${if (files.size > 5) ", …" else ""}",
            "Commit or stash them first, so keel's commits hold only what its agents wrote. Or tick \"Start anyway\": your files then stay out of keel's commits.",
        )
    }

    /** The flow that runs or waits in the project folder itself (not in a worktree), if one does. */
    fun folderFlow(pid: String): String? = jdbc.query(
        "SELECT id FROM threads WHERE project_id = ? AND worktree IS NULL AND status IN ('running','waiting') ORDER BY updated_at DESC LIMIT 1",
        { rs, _ -> rs.getString(1) }, pid,
    ).firstOrNull()

    /** Where a new flow runs: auto = the project folder when it is free, else a worktree of its own next to that flow. */
    private fun inWorktree(pid: String, where: String?): Boolean {
        val busy = folderFlow(pid) != null
        return when (where?.trim()?.lowercase()) {
            null, "", "auto" -> busy
            "worktree" -> true
            "folder" -> if (busy) throw Conflict("A flow already runs in the project folder",
                "Start this one in its own worktree (it runs next to the other one), or wait until that flow ends.") else false
            else -> throw BadRequest("where must be folder, worktree or auto")
        }
    }

    private data class FlowWorktree(val name: String, val branch: String, val path: String)

    /** A worktree for a flow, on its own branch (the branch pattern) from the base branch, not from what the folder has. */
    private fun worktreeFor(pid: String, root: Path, title: String): FlowWorktree {
        if (!repo.git(root, "rev-parse", "--git-dir").ok)
            throw Conflict("This project is not a git repository", "A flow next to another one needs git: its own worktree and branch.")
        val pattern = settings.effective(pid).branchPattern.ifBlank { "feat/{slug}" }
        val first = pattern.replace("{slug}", Slug.of(title).take(40)).replace("{user}", "keel").replace("{flow}", "flow")
        var branch = first
        var n = 2
        while (repo.git(root, "rev-parse", "--verify", "--quiet", "refs/heads/$branch").ok) branch = "$first-${n++}"
        val name = "flow-${Slug.of(title).take(30)}-${java.util.UUID.randomUUID().toString().take(6)}"
        val res = engine.post("/worktrees", mapOf("root" to root.toString(), "name" to name, "branch" to branch, "start" to repo.base(root)))
        return FlowWorktree(name, res.path("branch").asText(branch), res.path("path").asText())
    }

    private fun removeWorktreeAt(root: Path, name: String) {
        runCatching { engine.post("/worktrees/remove", mapOf("root" to root.toString(), "name" to name)) }
            .onFailure { log.warn("could not remove the worktree {}: {}", name, it.message) }
    }

    /** A finished flow's worktree goes; its branch (and so its commits and PR) stays. */
    fun removeWorktree(tid: String): JsonNode {
        val row = jdbc.query("SELECT project_id, worktree, status FROM threads WHERE id = ?",
            { rs, _ -> Triple(rs.getString(1), rs.getString(2), rs.getString(3)) }, tid).firstOrNull() ?: throw NotFound("No flow $tid")
        val (pid, name, status) = row
        if (name.isNullOrBlank()) throw BadRequest("This flow runs in the project folder; it has no worktree")
        if (status in setOf("running", "waiting")) throw Conflict("This flow still runs", "Stop it first, or let it finish.")
        val users = jdbc.queryForObject("SELECT COUNT(*) FROM threads WHERE project_id = ? AND worktree = ? AND status IN ('running','waiting')",
            Int::class.java, pid, name) ?: 0
        if (users > 0) throw Conflict("Another flow works in this worktree", "It took over this flow's work; let it finish first.")
        engine.post("/worktrees/remove", mapOf("root" to projects.root(pid).toString(), "name" to name))
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        return mapper.valueToTree(mapOf("ok" to true, "worktree" to name))
    }

    /** A flow a flow handed over to (change → feature) works where its parent worked. */
    fun inheritWorktree(child: String, parent: String) {
        jdbc.update("UPDATE threads SET worktree = (SELECT worktree FROM threads WHERE id = ?), branch = (SELECT branch FROM threads WHERE id = ?) " +
            "WHERE id = ? AND worktree IS NULL", parent, parent, child)
    }

    /** Never work on main/master: a flow gets its own branch from the branch pattern in Settings (feat/{slug}). */
    private fun ownBranch(pid: String, root: Path, title: String) {
        if (isDemo(root)) return
        val current = repo.git(root, "rev-parse", "--abbrev-ref", "HEAD").takeIf { it.ok }?.out?.trim() ?: return
        val base = repo.base(root) ?: return
        if (current != base) return
        val pattern = settings.effective(pid).branchPattern.ifBlank { "feat/{slug}" }
        val first = pattern.replace("{slug}", Slug.of(title).take(40)).replace("{user}", "keel").replace("{flow}", "flow")
        var name = first
        var n = 2
        while (repo.git(root, "rev-parse", "--verify", "--quiet", "refs/heads/$name").ok) name = "$first-${n++}"
        val r = repo.git(root, "checkout", "-q", "-b", name)
        if (!r.ok) throw Conflict("Could not create the branch $name", r.err.ifBlank { r.out }.take(300))
    }

    /** Stores the latest ThreadState we saw, so the UI has something when the engine is down. */
    fun save(tid: String, state: JsonNode) {
        if (!state.isObject || state.get("status") == null) return
        jdbc.update(
            "UPDATE threads SET status = ?, current = ?, phase = ?, ac = ?, state_json = ?, error = ?, updated_at = ? WHERE id = ?",
            state.get("status")?.asText(), state.get("current")?.takeIf { !it.isNull }?.asText(), state.get("phase")?.asText(),
            state.get("ac")?.takeIf { !it.isNull }?.asText(), mapper.writeValueAsString(state),
            state.get("error")?.takeIf { !it.isNull }?.asText(), Time.now(), tid,
        )
    }

    private fun threadProject(tid: String): String? =
        jdbc.query("SELECT project_id FROM threads WHERE id = ?", { rs, _ -> rs.getString(1) }, tid).firstOrNull()

    /** API keys and CLI logins for these models, under the engine's names (never stored by the engine). */
    private fun keysFor(models: Collection<Model>): Map<String, String> =
        models.distinctBy { it.provider to it.mode }
            .fold(mutableMapOf()) { acc, m -> secrets.engineKeys(m.provider, m.mode).forEach { (k, v) -> acc.putIfAbsent(k, v) }; acc }

    /** The logins a thread's agents need right now; sent with every resume and rewind because an engine restart forgets them. */
    private fun keysForThread(tid: String): Map<String, String> {
        val pid = threadProject(tid) ?: return emptyMap()
        val s = settings.effective(pid)
        val models = listOf(s.defaultModel, s.cheaperModel) + agents.list(pid).filter { it.enabled }.map { it.model }
        return keysFor(models)
    }

    /**
     * After a keel restart: the engine waits with the threads that were running until it has their logins (it keeps
     * them in memory only). Send each one its logins and folder, so its agents continue where they stopped.
     */
    fun continueAfterRestart(): Int {
        // Agent runs that were cut off by the restart: their agent continues in a new run (with its own session).
        jdbc.update("UPDATE agent_calls SET status = 'stopped', ended_at = COALESCE(ended_at, ?) WHERE status = 'running'", Time.now())
        val running = jdbc.queryForList("SELECT id FROM threads WHERE status = 'running'", String::class.java)
        var n = 0
        for (tid in running) {
            runCatching { engine.continueThread(tid, keysForThread(tid).takeIf { it.isNotEmpty() }, rootNow(tid)) }
                .onSuccess { save(tid, it); n++ }
                .onFailure { log.warn("could not continue thread {} after a restart: {}", tid, it.message) }
        }
        return n
    }

    /** The thread's folder now: the project folder (it moves when keel is started another way, keel2 start --docker), or
     *  its worktree in it. */
    private fun rootNow(tid: String): String? {
        val (pid, wt) = jdbc.query("SELECT project_id, worktree FROM threads WHERE id = ?", { rs, _ -> rs.getString(1) to rs.getString(2) }, tid)
            .firstOrNull() ?: return null
        val root = runCatching { projects.root(pid) }.getOrNull() ?: return null
        return (if (wt.isNullOrBlank()) root else root.resolve(".keel/worktrees").resolve(wt)).toString()
    }

    fun resume(tid: String, decision: String, why: String?, payload: Map<String, Any?>?): JsonNode {
        if (decision !in setOf("approve", "reject")) throw BadRequest("decision must be approve or reject")
        val keys = keysForThread(tid).takeIf { it.isNotEmpty() }
        // The gate's notification is done once the engine took the answer; one that arrives meanwhile (the next gate) stays.
        val open = notifications.openGateId(tid)
        val state = engine.resume(tid, mapOf("decision" to decision, "why" to why, "payload" to payload, "keys" to keys,
            "root" to rootNow(tid)).filterValues { it != null })
        save(tid, state)
        if (open != null) notifications.markDone(tid, open)
        threadProject(tid)?.let { hub.publish(it, "project.changed", mapOf("id" to it)) }
        return state
    }

    /** v0.4.1: the thread's run mode from its next pause on (the engine keeps it with the thread). */
    fun setMode(tid: String, mode: String): JsonNode {
        if (mode.isBlank()) throw BadRequest("mode is missing", "Pick one of: ${keel.api.settings.Settings.RUN_MODES.joinToString()}")
        checkRunMode(mode)
        val state = engine.setMode(tid, mode)
        save(tid, state)
        threadProject(tid)?.let { hub.publish(it, "project.changed", mapOf("id" to it)) }
        return state
    }

    fun stop(tid: String): JsonNode {
        val state = engine.stop(tid)
        save(tid, state)
        jdbc.update("UPDATE agent_calls SET status = 'stopped', ended_at = COALESCE(ended_at, ?) WHERE thread_id = ? AND status = 'running'", Time.now(), tid)
        threadProject(tid)?.let { hub.publish(it, "project.changed", mapOf("id" to it)) }
        return state
    }

    fun history(tid: String): JsonNode = engine.history(tid)

    fun rewind(tid: String, checkpointId: String): JsonNode {
        if (checkpointId.isBlank()) throw BadRequest("checkpoint_id is missing")
        val state = engine.rewind(tid, checkpointId, keysForThread(tid).takeIf { it.isNotEmpty() }, rootNow(tid))
        save(tid, state)
        return state
    }

    /** The project folder's flow: the one that runs or waits there, else the last one (flows in worktrees: flowOf, board). */
    fun flow(pid: String): FlowView {
        projects.require(pid)
        val row = jdbc.query(
            "SELECT id, workflow_id, state_json FROM threads WHERE project_id = ? AND worktree IS NULL ORDER BY CASE WHEN status IN ('running','waiting') THEN 0 ELSE 1 END, updated_at DESC LIMIT 1",
            { rs, _ -> Triple(rs.getString(1), rs.getString(2), rs.getString(3)) }, pid,
        ).firstOrNull()
        return view(row)
    }

    /** One flow of the project, wherever it runs. */
    fun flowOf(pid: String, tid: String): FlowView {
        projects.require(pid)
        val row = jdbc.query("SELECT id, workflow_id, state_json FROM threads WHERE project_id = ? AND id = ?",
            { rs, _ -> Triple(rs.getString(1), rs.getString(2), rs.getString(3)) }, pid, tid).firstOrNull()
            ?: throw NotFound("No flow $tid in this project")
        return view(row)
    }

    private fun view(row: Triple<String, String?, String?>?): FlowView {
        var thread: JsonNode? = null
        var workflow: Workflow? = null
        if (row != null) {
            thread = try {
                engine.thread(row.first).also { save(row.first, it) }
            } catch (e: EngineDown) {
                row.third?.let { mapper.readTree(it) }
            } catch (e: ApiException) {
                row.third?.let { mapper.readTree(it) }
            }
            workflow = row.second?.let { wid -> try { workflows.get(wid) } catch (e: NotFound) { null } }
        }
        return FlowView(thread, workflow)
    }

    // ---- v0.7.x: the board of the project's flows ----------------------------------------------

    private data class BoardRow(val id: String, val title: String, val workflowId: String?, val status: String, val phase: String?,
                                val current: String?, val worktree: String?, val branch: String?, val state: String?, val updatedAt: String?)

    /** The flows that run or wait, in the project folder or in worktrees, and the finished ones whose worktree is left. */
    fun board(pid: String): FlowBoard {
        val root = projects.root(pid)
        val base = repo.base(root)
        val rows = jdbc.query(
            "SELECT id, title, workflow_id, status, phase, current, worktree, branch, state_json, updated_at FROM threads " +
                "WHERE project_id = ? AND (status IN ('running','waiting') OR worktree IS NOT NULL) ORDER BY created_at",
            { rs, _ -> BoardRow(rs.getString(1), rs.getString(2) ?: "", rs.getString(3), rs.getString(4) ?: "", rs.getString(5), rs.getString(6),
                rs.getString(7), rs.getString(8), rs.getString(9), rs.getString(10)) }, pid,
        )
        val flows = rows.mapNotNull { r ->
            val dir = if (r.worktree.isNullOrBlank()) root else root.resolve(".keel/worktrees").resolve(r.worktree)
            val active = r.status in setOf("running", "waiting")
            val left = !active && !r.worktree.isNullOrBlank() && java.nio.file.Files.isDirectory(dir)
            if (!active && !left) return@mapNotNull null
            val state = r.state?.let { runCatching { mapper.readTree(it) }.getOrNull() }
            val branch = r.branch ?: repo.git(dir, "rev-parse", "--abbrev-ref", "HEAD").takeIf { it.ok }?.out?.trim()?.ifBlank { null }
            BoardFlow(r.id, r.title, r.workflowId, r.status, r.phase, r.current, state?.get("waiting")?.takeIf { it.isObject },
                if (r.worktree.isNullOrBlank()) "folder" else "worktree", r.worktree, branch, changedFiles(dir, base), r.updatedAt, left)
        }
        val active = flows.filter { !it.worktreeLeft }
        val overlaps = active.flatMap { f -> f.files.map { it to f.threadId } }.groupBy({ it.first }, { it.second })
            .filter { it.value.distinct().size > 1 }.map { FlowOverlap(it.key, it.value.distinct()) }.sortedBy { it.file }
        val conflicts = mutableListOf<FlowConflict>()
        for (i in active.indices) for (j in i + 1 until active.size) {
            val a = active[i]
            val b = active[j]
            if (a.branch == null || b.branch == null || a.branch == b.branch) continue
            val files = mergeConflicts(root, a.branch, b.branch) ?: continue
            if (files.isNotEmpty()) conflicts += FlowConflict(a.threadId, b.threadId, files)
        }
        // merge the flows with the fewest conflicts first; among those, the smaller change first
        val clashes = active.associate { f -> f.threadId to conflicts.count { it.a == f.threadId || it.b == f.threadId } }
        val order = active.sortedWith(compareBy({ clashes[it.threadId] ?: 0 }, { it.files.size })).map { it.threadId }
        return FlowBoard(flows, overlaps, conflicts, order)
    }

    /** What a flow changed against the base branch: committed on its branch, or not yet (a running agent's files). */
    private fun changedFiles(dir: Path, base: String?): List<String> {
        if (!java.nio.file.Files.isDirectory(dir)) return emptyList()
        val committed = base?.let { b -> repo.git(dir, "diff", "--name-only", "$b...HEAD").takeIf { it.ok }?.out?.lines() }.orEmpty()
        val open = repo.git(dir, "status", "--porcelain", "--untracked-files=all").out.lines().filter { it.length > 3 }.map { it.substring(3).trim() }
        return (committed + open).map { it.trim() }
            .filter { it.isNotBlank() && !WorkspaceDoctor.isEngineFile(it) && !it.startsWith(".keel/") }.distinct().sorted()
    }

    /** The files where two branches do not merge cleanly (git merge-tree, git 2.38 or newer); null when git cannot tell. */
    private fun mergeConflicts(root: Path, a: String, b: String): List<String>? {
        val r = repo.git(root, "merge-tree", "--write-tree", "--name-only", "--no-messages", a, b)
        return when (r.code) {
            0 -> emptyList()
            1 -> r.out.lines().drop(1).map { it.trim() }.filter { it.isNotBlank() }.distinct()
            else -> null
        }
    }

    /** Engine estimate, fed with this project's job history. */
    fun estimate(pid: String, workflowId: String, acs: Int): JsonNode {
        projects.require(pid)
        return estimateYaml(pid, workflows.get(workflowId).yaml, acs)
    }

    /** Estimate for workflow YAML that is not saved yet (POST /estimate). */
    fun estimateYaml(pid: String, yaml: String, acs: Int): JsonNode {
        projects.require(pid)
        if (yaml.isBlank()) throw BadRequest("yaml is empty", "Send the workflow YAML to estimate.")
        val history = jdbc.query(
            "SELECT agent, tokens_in, tokens_out FROM agent_calls WHERE project_id = ? AND status = 'done' AND agent IS NOT NULL ORDER BY started_at DESC LIMIT 300",
            { rs, _ -> mapOf("agent" to rs.getString(1), "tokens_in" to rs.getLong(2), "tokens_out" to rs.getLong(3), "retries" to 0) }, pid,
        )
        val models = linkedMapOf<String, Model>("default" to settings.effective(pid).defaultModel)
        val enabled = agents.list(pid).filter { it.enabled }
        enabled.forEach { models[it.id] = it.model }
        // What each agent reads of the knowledge base it is given (counted for agents with no history yet).
        val knowledge = enabled.filter { it.knowledgeTokens > 0 }.associate { it.id to it.knowledgeTokens }
        return engine.estimate(mapOf("yaml" to yaml, "acs" to acs.coerceIn(0, 50), "history" to history, "models" to models,
            "knowledge_tokens" to knowledge))
    }

    // ---- unlocks ----------------------------------------------------------------------------

    /**
     * Opens one path for one phase. When the project's flow waits on a "fix" (a guard refused an edit), the unlock
     * goes to the engine as a resume payload, so the thread continues with it. Otherwise it goes to the project's
     * running or waiting thread through the engine (`POST /threads/{id}/unlocks`): the engine keeps it in the flow
     * state and hands it to the guards of the agents running now.
     */
    fun unlock(pid: String, path: String, phase: String?, reason: String?): UnlockResult {
        projects.require(pid)
        val rel = path.trim().removePrefix("./")
        if (rel.isBlank()) throw BadRequest("path is empty", "Send the file path relative to the repo root.")
        if (rel.startsWith("/") || rel.split('/').any { it == ".." } || rel.contains('\u0000')) {
            throw BadRequest("That path is outside the project", "Use a path relative to the repo root.")
        }
        val why = reason?.takeIf { it.isNotBlank() } ?: "unlocked from keel v2"
        val active = jdbc.query(
            "SELECT id, status FROM threads WHERE project_id = ? AND worktree IS NULL AND status IN ('running', 'waiting') ORDER BY updated_at DESC LIMIT 1",
            { rs, _ -> rs.getString(1) to rs.getString(2) }, pid,
        ).firstOrNull() ?: throw Conflict("No flow is running in this project", "Unlocks belong to a flow: start one, then unlock the file in it.")
        val (tid, status) = active
        if (status == "waiting") {
            val state = runCatching { engine.thread(tid) }.getOrNull()
            val kind = state?.get("waiting")?.get("kind")?.asText()
            if (state != null && state.get("status")?.asText() == "waiting" && kind == "fix") {
                val ph = phase?.takeIf { it.isNotBlank() } ?: state.get("phase")?.asText() ?: "none"
                val unlock = mapOf("path" to rel, "phase" to ph)
                val next = resume(tid, "approve", "unlock $rel in $ph: $why", mapOf("unlock" to unlock))
                val list = next.get("unlocks")?.takeIf { it.isArray }?.let { mapper.convertValue(it, List::class.java) }
                    ?: listOf(unlock)
                return UnlockResult(list, "thread", tid)
            }
        }
        val body = mapOf("path" to rel, "phase" to phase?.takeIf { it.isNotBlank() }, "reason" to why).filterValues { it != null }
        val list = mapper.convertValue(engine.addUnlock(tid, body), List::class.java)
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        return UnlockResult(list, "engine", tid)
    }
}

data class UnlockResult(val unlocks: List<Any?>, val via: String, val threadId: String?)


/** When the api is ready (the engine is already up: keel-start starts it first), hand running threads back to it. */
@org.springframework.stereotype.Component
class ContinueAfterRestart(private val flows: FlowService) {
    private val log = org.slf4j.LoggerFactory.getLogger(javaClass)

    @org.springframework.context.event.EventListener(org.springframework.boot.context.event.ApplicationReadyEvent::class)
    fun onReady() {
        Thread({
            val n = runCatching { flows.continueAfterRestart() }.getOrElse { log.warn("continue after restart: {}", it.message); 0 }
            if (n > 0) log.info("continued {} flow(s) that were running when keel stopped", n)
        }, "continue-after-restart").apply { isDaemon = true }.start()
    }
}
