package keel.api.flow

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.agents.AgentService
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
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.events.EventHub
import keel.api.mcp.McpServerSpec
import keel.api.mcp.McpService
import keel.api.projects.ProjectService
import keel.api.settings.Model
import keel.api.settings.SettingsService
import keel.api.skills.SkillService
import keel.api.workflows.Workflow
import keel.api.workflows.WorkflowService
import org.springframework.http.HttpStatus
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service

data class Ac(val id: String = "", val layer: String = "API", val title: String = "")

data class ThreadSettings(val gatesMode: String, val capTokens: Int, val onCap: String, val cheaperModel: Model?)

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
    /** provider -> API key for models that run in "api" mode; the engine keeps them in memory only. */
    val keys: Map<String, String>? = null,
    /** What the user asked for, in their words. */
    val request: String? = null,
)

/** Per-flow cap from POST /flows; null fields fall back to the project's settings. */
data class FlowCap(val capTokens: Int? = null, val onCap: String? = null) {
    fun check() {
        if (capTokens != null && capTokens <= 0) throw BadRequest("cap_tokens must be above 0")
        if (onCap != null && onCap !in setOf("pause", "cheaper", "stop")) throw BadRequest("on_cap cannot be \"$onCap\"", "Pick one of: pause, cheaper, stop")
    }
}

data class FlowView(val thread: JsonNode?, val workflow: Workflow?, val keelState: JsonNode?)

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
) {
    /** Builds StartThread from the workflow + effective settings + agent models + MCP + skills. */
    fun buildStart(pid: String, workflowId: String, title: String, acs: List<Ac>?, cap: FlowCap? = null): StartThread {
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
            settings = ThreadSettings(s.gatesMode, cap?.capTokens ?: s.capTokens, cap?.onCap ?: s.onCap, s.cheaperModel),
            mcp = mcp.specsFor(s.mcp), skills = skillText,
        )
    }

    fun start(pid: String, workflowId: String, title: String, acs: List<Ac>?, cap: FlowCap? = null,
              allowFake: Boolean = false, allowDirty: Boolean = false, request: String? = null): JsonNode {
        if (title.isBlank()) throw BadRequest("Give the flow a title", "One short line: what should this flow build or fix?")
        cap?.check()
        var start = buildStart(pid, workflowId, title, acs, cap)
        val root = Paths.get(start.root)
        refuseFake(start, root, allowFake)
        refuseDirty(root, allowDirty)
        ownBranch(pid, root, title)
        // API keys for "api" models and CLI logins for subscription models, from the encrypted secrets table.
        val keys = start.models.values.distinctBy { it.provider to it.mode }
            .fold(mutableMapOf<String, String>()) { acc, m -> secrets.engineKeys(m.provider, m.mode).forEach { (k, v) -> acc.putIfAbsent(k, v) }; acc }
        val withRequest = start.copy(request = request?.trim()?.takeIf { it.isNotEmpty() }?.take(8000))
        val body = if (keys.isEmpty()) withRequest else withRequest.copy(keys = keys)
        val res = engine.startThread(body)
        val tid = res.get("thread_id")?.asText() ?: throw ApiException(HttpStatus.BAD_GATEWAY, "The engine did not return a thread id")
        val now = Time.now()
        jdbc.update(
            "INSERT OR IGNORE INTO threads(id, project_id, workflow_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'running', ?, ?)",
            tid, pid, workflowId, title, now, now,
        )
        jdbc.update("UPDATE threads SET workflow_id = ?, title = ?, project_id = ? WHERE id = ?", workflowId, title, pid, tid)
        val state = runCatching { engine.thread(tid) }.getOrElse { res }
        save(tid, state)
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        return state
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
            .filterNot { it.startsWith(".keel/logs/") || it == ".keel/state.json" || it.startsWith(".keel/.state.json") }
        if (files.isEmpty()) return
        throw Conflict(
            "This project has uncommitted changes (${files.size} file${if (files.size == 1) "" else "s"}): ${files.take(5).joinToString()}${if (files.size > 5) ", …" else ""}",
            "Commit or stash them first, so keel's commits hold only what its agents wrote. Or tick \"Start anyway\": your files then stay out of keel's commits.",
        )
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

    fun resume(tid: String, decision: String, why: String?, payload: Map<String, Any?>?): JsonNode {
        if (decision !in setOf("approve", "reject")) throw BadRequest("decision must be approve or reject")
        val state = engine.resume(tid, mapOf("decision" to decision, "why" to why, "payload" to payload).filterValues { it != null })
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
        val state = engine.rewind(tid, checkpointId)
        save(tid, state)
        return state
    }

    fun flow(pid: String): FlowView {
        val root = projects.root(pid)
        val row = jdbc.query(
            "SELECT id, workflow_id, state_json FROM threads WHERE project_id = ? ORDER BY CASE WHEN status IN ('running','waiting') THEN 0 ELSE 1 END, updated_at DESC LIMIT 1",
            { rs, _ -> Triple(rs.getString(1), rs.getString(2), rs.getString(3)) }, pid,
        ).firstOrNull()
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
        return FlowView(thread, workflow, projects.keelState(root))
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
        agents.list(pid).filter { it.enabled }.forEach { models[it.id] = it.model }
        return engine.estimate(mapOf("yaml" to yaml, "acs" to acs.coerceIn(0, 50), "history" to history, "models" to models))
    }

    // ---- unlocks ----------------------------------------------------------------------------

    /**
     * Opens one path for one phase (keel v1 `keel unlock`). When the project's flow waits on a
     * "fix" (a guard refused an edit), the unlock goes to the engine as a resume payload, so the
     * thread continues with it. Otherwise it is written to `<root>/.keel/state.json` `unlocks`.
     */
    fun unlock(pid: String, path: String, phase: String?, reason: String?): UnlockResult {
        val root = projects.root(pid)
        val rel = path.trim().removePrefix("./")
        if (rel.isBlank()) throw BadRequest("path is empty", "Send the file path relative to the repo root.")
        if (rel.startsWith("/") || rel.split('/').any { it == ".." } || rel.contains('\u0000')) {
            throw BadRequest("That path is outside the project", "Use a path relative to the repo root.")
        }
        val why = reason?.takeIf { it.isNotBlank() } ?: "unlocked from keel v2"
        val waiting = jdbc.query(
            "SELECT id FROM threads WHERE project_id = ? AND status = 'waiting' ORDER BY updated_at DESC LIMIT 1",
            { rs, _ -> rs.getString(1) }, pid,
        ).firstOrNull()
        if (waiting != null) {
            val state = runCatching { engine.thread(waiting) }.getOrNull()
            val kind = state?.get("waiting")?.get("kind")?.asText()
            if (state != null && state.get("status")?.asText() == "waiting" && kind == "fix") {
                val ph = phase?.takeIf { it.isNotBlank() } ?: state.get("phase")?.asText() ?: "none"
                val unlock = mapOf("path" to rel, "phase" to ph)
                val next = resume(waiting, "approve", "unlock $rel in $ph: $why", mapOf("unlock" to unlock))
                val list = next.get("unlocks")?.takeIf { it.isArray }?.let { mapper.convertValue(it, List::class.java) }
                    ?: listOf(unlock)
                return UnlockResult(list, "thread", waiting)
            }
        }
        val list = writeUnlock(root, rel, phase, why)
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        return UnlockResult(list, "state", null)
    }

    /** Appends `{path, phase, reason, at}` to `.keel/state.json` unlocks (keel v1 format). */
    @Synchronized
    private fun writeUnlock(root: java.nio.file.Path, rel: String, phase: String?, why: String): List<Any?> {
        val file = root.resolve(".keel/state.json")
        val node = (projects.keelState(root) as? com.fasterxml.jackson.databind.node.ObjectNode) ?: mapper.createObjectNode()
        val ph = phase?.takeIf { it.isNotBlank() } ?: node.get("phase")?.asText()?.takeIf { it.isNotBlank() } ?: "none"
        val arr = (node.get("unlocks") as? com.fasterxml.jackson.databind.node.ArrayNode) ?: node.putArray("unlocks")
        val exists = arr.any { it.get("path")?.asText() == rel && it.get("phase")?.asText() == ph }
        if (!exists) {
            arr.addObject().put("path", rel).put("phase", ph).put("reason", why).put("at", Time.now())
        }
        java.nio.file.Files.createDirectories(file.parent)
        val tmp = file.resolveSibling("state.json.tmp")
        java.nio.file.Files.writeString(tmp, mapper.writerWithDefaultPrettyPrinter().writeValueAsString(node))
        java.nio.file.Files.move(tmp, file, java.nio.file.StandardCopyOption.REPLACE_EXISTING, java.nio.file.StandardCopyOption.ATOMIC_MOVE)
        return mapper.convertValue(arr, List::class.java)
    }
}

data class UnlockResult(val unlocks: List<Any?>, val via: String, val threadId: String?)
