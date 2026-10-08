package keel.api.helper

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.agents.AgentService
import keel.api.approvals.ApprovalService
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import keel.api.connections.SecretService
import keel.api.doctor.WorkspaceDoctor
import keel.api.engine.EngineClient
import keel.api.flow.AgentStart
import keel.api.flow.FlowService
import keel.api.workflows.WorkflowService
import keel.api.mcp.McpService
import keel.api.projects.ProjectService
import keel.api.repo.RepoService
import keel.api.settings.Model
import keel.api.settings.SettingsService
import keel.api.skills.SkillService
import keel.api.tasks.NewTask
import keel.api.tasks.TaskService
import keel.api.tasks.TaskView
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PatchMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.nio.file.Paths

/** A new Helper session: mode "ask" (read only), "fix" (while the project's flow waits at a gate) or "side" (its own
 *  worktree and branch); the model defaults to the helper agent's. */
data class HelperCreate(val mode: String = "ask", val model: Model? = null, val title: String = "")
data class HelperUndo(val path: String? = null)
/** Done: the commit's subject as the person wrote it (blank: the chat's title). */
data class HelperDoneBody(val message: String = "")
/** Hand a side session over: as a task (its description says what was done and on which branch) ... */
data class HelperTaskBody(val title: String = "", val type: String = "task")
/** ... or as a flow on its branch (a change flow by default: it writes the tests for what the branch does). */
data class HelperFlowBody(val title: String = "", val workflowId: String = "change")
/** The person's answer to a permission card: once | always (this command, for the rest of the chat) | deny (with a reason). */
data class HelperAnswer(val decision: String = "", val why: String = "")
data class HelperPatch(val title: String? = null, val model: Model? = null)

/** What the person points at in the Repo page: a file, a code-graph symbol or a criterion. */
data class HelperMention(val kind: String = "", val value: String = "", val file: String? = null, val line: Int? = null)
data class HelperSelection(val path: String = "", val from: Int? = null, val to: Int? = null, val text: String = "")

/** One message to KeelBot; the api adds the logins, the MCP servers, the agent's settings and the project's flow. */
data class HelperTurn(
    val text: String = "",
    val model: Model? = null,
    val mentions: List<HelperMention> = emptyList(),
    val selection: HelperSelection? = null,
    val openFile: String? = null,
)

/**
 * KeelBot: chat sessions in the Repo page. The engine runs them (engine runtime/helper.py) with keel's own
 * harness; the api owns who may call, what the engine gets (logins, MCP, knowledge, the flow) and the records (the
 * helper.* events become agent calls in EventService, so the budget, Live agents and Jobs count them).
 */
@Service
class HelperService(
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val settings: SettingsService,
    private val agents: AgentService,
    private val secrets: SecretService,
    private val mcp: McpService,
    private val skills: SkillService,
    private val flows: FlowService,
    private val workflows: WorkflowService,
    private val plugins: keel.api.plugins.PluginService,
    private val tasks: TaskService,
    private val repo: RepoService,
    private val mapper: ObjectMapper,
    private val approvals: ApprovalService,
) {
    private fun helperAgent(pid: String) = agents.list(pid).firstOrNull { it.id == AGENT }

    /** KeelBot's model: the helper agent's (Agents page), else the project's default model. */
    fun defaultModel(pid: String): Model = helperAgent(pid)?.model ?: settings.effective(pid).defaultModel

    fun create(pid: String, body: HelperCreate): JsonNode {
        val project = projects.require(pid)
        if (body.mode !in MODES) throw BadRequest("Unknown KeelBot mode ${body.mode}", "Use ask, fix or side.")
        val thread = if (body.mode == "fix") waitingThread(pid) else null
        val threadId = thread?.path("thread_id")?.asText()
        return engine.post("/helper/sessions", mapOf(
            "project_id" to pid, "root" to project.root, "mode" to body.mode,
            "model" to (body.model ?: defaultModel(pid)), "title" to body.title.take(120),
            "thread_id" to threadId, "flow" to threadId?.let { fixContext(pid, it) },
        ).filterValues { it != null })
    }

    /** Fix mode works only while the project's flow waits at a gate (and never in a read-only run). */
    private fun waitingThread(pid: String, threadId: String? = null): JsonNode {
        val t = runCatching { flows.flow(pid).thread }.getOrNull()
        if (t == null || t.path("status").asText() != "waiting")
            throw Conflict("No flow waits at a gate in this project", "Fix mode works while a flow waits for you. Use Ask, or wait for the next gate.")
        if (threadId != null && t.path("thread_id").asText() != threadId)
            throw Conflict("This Fix chat belongs to another flow", "Start a new Fix chat for the flow that waits now.")
        if (t.path("run_mode").asText() == "readonly")
            throw Conflict("This flow runs read-only", "Change its run mode on the Flow page to let KeelBot edit.")
        return t
    }

    fun list(pid: String): JsonNode {
        projects.require(pid)
        return engine.get("/helper/sessions?project=$pid")
    }

    /** The session, if it belongs to this project (a session id from another project is a 404 here). */
    fun get(pid: String, sid: String): JsonNode {
        projects.require(pid)
        val s = engine.get("/helper/sessions/$sid")
        if (s.path("project").asText() != pid) throw NotFound("No KeelBot session $sid in project $pid")
        return s
    }

    fun patch(pid: String, sid: String, body: HelperPatch): JsonNode {
        get(pid, sid)
        return engine.patch("/helper/sessions/$sid", mapOf("title" to body.title, "model" to body.model).filterValues { it != null })
    }

    fun delete(pid: String, sid: String): JsonNode {
        get(pid, sid)
        return engine.delete("/helper/sessions/$sid")
    }

    fun stop(pid: String, sid: String): JsonNode {
        get(pid, sid)
        return engine.post("/helper/sessions/$sid/stop", emptyMap<String, Any>())
    }

    fun turn(pid: String, sid: String, body: HelperTurn): JsonNode {
        val text = body.text.trim()
        if (text.isEmpty()) throw BadRequest("The message is empty", "Write a question, or pick a command with /.")
        if (text.length > 20_000) throw BadRequest("The message is too long", "Keep it under 20,000 characters; point at files with @ instead.")
        val s = get(pid, sid)
        val fix = s.path("mode").asText() == "fix"
        val model = body.model ?: mapper.treeToValue(s.path("model"), Model::class.java)
        val eff = settings.effective(pid)
        val helper = helperAgent(pid)
        val payload = linkedMapOf<String, Any?>(
            "text" to text,
            "model" to body.model,
            "keys" to (secrets.engineKeys(model.provider, model.mode) + plugins.keysFor(pid)).takeIf { it.isNotEmpty() },
            "plugins" to plugins.enabled(pid),
            "mcp" to mcp.specsFor(eff.mcp),
            "tools_allow" to (mcp.allow(pid)[AGENT] ?: emptyList()),
            "agents" to (helper?.let { mapOf(AGENT to AgentStart(it.knowledge)) } ?: emptyMap()),
            "skills" to (helper?.skills?.takeIf { it.isNotEmpty() }?.let { mapOf(AGENT to skills.textFor(pid, it)) } ?: emptyMap()),
            "flow" to if (fix) fixContext(pid, s.path("thread_id").asText()) else flowContext(pid),
            "keel" to keelContext(pid),
            "mentions" to body.mentions.filter { it.value.isNotBlank() }.take(30),
            "selection" to body.selection?.takeIf { it.path.isNotBlank() && it.text.isNotBlank() }?.let { it.copy(text = it.text.take(8000)) },
            "open_file" to body.openFile?.takeIf { it.isNotBlank() },
        ).filterValues { it != null }
        return engine.post("/helper/sessions/$sid/turn", payload)
    }

    fun commands(pid: String): JsonNode =
        engine.post("/helper/commands", mapOf("root" to projects.require(pid).root, "plugins" to plugins.enabled(pid)))

    // ---- Fix mode: KeelBot's changes, Undo, Done, and the permission cards -----------------------------

    fun changes(pid: String, sid: String): JsonNode {
        get(pid, sid)
        return engine.get("/helper/sessions/$sid/changes")
    }

    fun undo(pid: String, sid: String, body: HelperUndo): JsonNode {
        get(pid, sid)
        return engine.post("/helper/sessions/$sid/undo", mapOf("path" to body.path?.takeIf { it.isNotBlank() }))
    }

    /** Run the checks and make keel's commit of KeelBot's files, while the flow still waits at its gate. */
    fun done(pid: String, sid: String, body: HelperDoneBody = HelperDoneBody()): JsonNode {
        val s = get(pid, sid)
        val flow = when (s.path("mode").asText()) {
            "fix" -> fixContext(pid, s.path("thread_id").asText())
            "side" -> emptyMap()            // Keep: the checks, then keel's commit on the side session's own branch
            else -> throw BadRequest("Only a Fix chat or a side session has changes to commit")
        }
        val eff = settings.effective(pid)
        val commit = mapOf("commit_author" to eff.commitAuthor.trim().ifBlank { null }, "commit_coauthor" to eff.commitCoauthor)
            .filterValues { it != null }
        return engine.post("/helper/sessions/$sid/done", mapOf("flow" to flow, "message" to body.message.trim().take(200),
            "commit" to commit), long = true)
    }

    /** The commands that wait for the person's OK in this project (core approvals), as the engine asked them. */
    fun permissions(pid: String): JsonNode {
        projects.require(pid)
        return mapper.valueToTree(approvals.list(ApprovalService.WAITING, pid).filter { approvals.engineOwned(it) }.mapNotNull { it.payload })
    }

    /** The old answer route: the same as POST /api/approvals/{id}/decide, for this project's questions. */
    fun answer(pid: String, qid: String, body: HelperAnswer): JsonNode {
        projects.require(pid)
        if (body.decision !in ApprovalService.ENGINE_DECISIONS) throw BadRequest("Answer once, always or deny")
        approvals.decide(qid, body.decision, body.why, project = pid)
        return mapper.valueToTree(mapOf("id" to qid, "decision" to body.decision))
    }

    // ---- side sessions: hand over as a task, or as a change flow on the branch ---------------------------

    fun handover(pid: String, sid: String): JsonNode {
        get(pid, sid)
        return engine.get("/helper/sessions/$sid/handover")
    }

    /** A task whose description says what the side session did and where the work is (its branch stays). */
    fun toTask(pid: String, sid: String, body: HelperTaskBody): TaskView {
        val h = handover(pid, sid)
        val title = body.title.trim().ifBlank { h.path("title").asText() }.take(300)
        return tasks.create(pid, NewTask(title = title, description = handoverText(h), type = body.type))
    }

    /**
     * A flow on the side session's branch: its worktree goes, the project folder checks the branch out, and the flow
     * (a change flow by default) writes the tests for what the branch does; a test that passes at once is "already met".
     * Only while no flow runs or waits in the project folder, which must be clean, and with everything kept.
     */
    fun toFlow(pid: String, sid: String, body: HelperFlowBody): JsonNode {
        val root = Paths.get(projects.require(pid).root)
        val h = handover(pid, sid)
        val status = runCatching { flows.flow(pid).thread }.getOrNull()?.path("status")?.asText()
        if (status == "running" || status == "waiting")
            throw Conflict("A flow already runs in this project's folder", "Finish or stop it first, or hand the side session over as a task.")
        val open = h.path("uncommitted").map { it.asText() }
        if (open.isNotEmpty())
            throw Conflict("${open.size} file(s) are not kept yet: ${open.take(5).joinToString()}", "Keep them (the checks run, keel commits on the branch) or undo them first.")
        if (h.path("commits").isEmpty) throw Conflict("Nothing is kept on the branch yet", "Keep KeelBot's change first.")
        val dirty = repo.git(root, "status", "--porcelain", "--untracked-files=all").out.lines().filter { it.length > 3 }
            .map { it.substring(3).trim() }.filterNot { WorkspaceDoctor.isEngineFile(it) }
        if (dirty.isNotEmpty())
            throw Conflict("The project folder has uncommitted changes: ${dirty.take(5).joinToString()}", "Commit or stash them first: the folder checks out the side branch.")
        engine.post("/helper/sessions/$sid/release", emptyMap<String, Any>())
        val branch = h.path("branch").asText()
        val r = repo.git(root, "checkout", "-q", branch)
        if (!r.ok) throw Conflict("Could not check out $branch", r.err.ifBlank { r.out }.take(300))
        val title = body.title.trim().ifBlank { h.path("title").asText() }.take(200)
        return flows.start(pid, body.workflowId.ifBlank { "change" }, title, null, request = handoverText(h))
    }

    private fun handoverText(h: JsonNode): String = buildString {
        appendLine("Made in a KeelBot side session on branch `${h.path("branch").asText()}` (from ${h.path("base").asText().take(7)}).")
        val commits = h.path("commits")
        if (!commits.isEmpty) {
            appendLine()
            appendLine("Commits on the branch:")
            commits.forEach { appendLine("- ${it.path("sha").asText().take(7)} ${it.path("subject").asText()}") }
        }
        val open = h.path("uncommitted")
        if (!open.isEmpty) {
            appendLine()
            appendLine("Not kept yet (in the worktree only): ${open.joinToString { it.asText() }}")
        }
        val asked = h.path("asked").map { it.asText().lines().first().take(200) }
        if (asked.isNotEmpty()) {
            appendLine()
            appendLine("What the person asked:")
            asked.forEach { appendLine("- $it") }
        }
        h.path("answer").asText("").takeIf { it.isNotBlank() }?.let {
            appendLine()
            appendLine("KeelBot's last answer:")
            appendLine(it.take(3000))
        }
    }.trim()

    /** What a Fix turn and Done need from the waiting flow: its phase, criterion, unlocks, workflow and run mode, and
     *  the phases of the steps before the waiting one (nearest first): at a gate whose own phase lets only notes change
     *  (the AC gate's "gate"), the engine takes the nearest phase that lets code change (runtime/helper.py fix_phase). */
    private fun fixContext(pid: String, threadId: String): Map<String, Any?> {
        val t = waitingThread(pid, threadId)
        val base = flowContext(pid) ?: emptyMap()
        val acId = t.path("ac").asText("").ifBlank { null }
        val ac = t.path("acs").firstOrNull { it.path("id").asText() == acId }
        val unlocks = runCatching { engine.unlocks(threadId) }.getOrNull()
        return base + mapOf(
            "thread_id" to threadId,
            "ac" to ac?.let { mapOf("id" to it.path("id").asText(), "layer" to it.path("layer").asText("API"), "title" to it.path("title").asText()) },
            "unlocks" to (unlocks?.takeIf { it.isArray } ?: unlocks?.path("unlocks")?.takeIf { it.isArray } ?: mapper.createArrayNode()),
            "workflow" to t.path("workflow_id").asText(""),
            "run_mode" to t.path("run_mode").asText("manual"),
            "phases_before" to phasesBefore(pid, t),
        )
    }

    private fun phasesBefore(pid: String, t: JsonNode): List<String> {
        val steps = runCatching { flows.flow(pid).workflow?.steps }.getOrNull() ?: return emptyList()
        val at = t.path("waiting").path("step").asText("").ifBlank { t.path("current").asText("") }
        val i = steps.indexOfFirst { it.id == at }
        if (i <= 0) return emptyList()
        return steps.subList(0, i).asReversed().mapNotNull { it.phase?.ifBlank { null } }.distinct()
    }

    /** The flow that runs or waits in the project, as KeelBot's prompt shows it; null when none does. */
    private fun flowContext(pid: String): Map<String, Any?>? {
        val t = runCatching { flows.flow(pid).thread }.getOrNull() ?: return null
        val status = t.path("status").asText()
        if (status != "running" && status != "waiting") return null
        val waiting = t.path("waiting").takeIf { it.isObject }
        return mapOf(
            "title" to t.path("title").asText(),
            "status" to (if (status == "waiting") "waits" else "runs"),
            "phase" to t.path("phase").asText(""),
            "spec" to t.path("spec").asText("").ifBlank { null },
            "acs" to t.path("acs").map { a -> mapOf("id" to a.path("id").asText(), "layer" to a.path("layer").asText(),
                "title" to a.path("title").asText(), "status" to a.path("status").asText()) },
            "waiting" to waiting?.let { mapOf("title" to it.path("title").asText(), "detail" to it.path("detail").asText("").take(3000)) },
        )
    }

    /** v0.9.0, KeelBot's view of keel (engine runtime/keelbot.py): the workflows it can suggest, and the project's flows. */
    private fun keelContext(pid: String): Map<String, Any?> {
        val wfs = runCatching { workflows.list(pid) }.getOrDefault(emptyList())
        val runs = runCatching { flows.runs(pid, null, 8) }.getOrDefault(emptyList())
        return mapOf(
            "workflows" to wfs.map { w ->
                mapOf("id" to w.id, "name" to w.name, "source" to w.source, "based_on" to w.basedOn, "folder" to w.folder,
                    "last_run" to w.lastRun?.let { mapOf("title" to it.title, "status" to it.status) },
                    "steps" to w.steps.map { st -> mapOf("id" to st.id, "kind" to st.kind, "name" to st.name, "agent" to st.agent) })
            },
            "flows" to runs.map { r ->
                mapOf("thread_id" to r.threadId, "title" to r.title, "workflow" to r.workflowId, "status" to r.status,
                    "phase" to r.phase, "step" to r.current, "waiting" to r.waiting?.let { mapOf("title" to it) },
                    "acs_done" to r.acsDone, "acs_total" to r.acsTotal, "tokens" to r.tokens, "where" to r.where,
                    "branch" to r.branch, "error" to r.error, "updated_at" to r.updatedAt)
            },
        )
    }

    companion object {
        const val AGENT = "helper"
        val MODES = setOf("ask", "fix", "side")
    }
}

@RestController
@RequestMapping("/api/projects/{pid}/helper")
class HelperController(private val helper: HelperService) {
    @GetMapping("/sessions")
    fun list(@PathVariable pid: String): JsonNode = helper.list(pid)

    @PostMapping("/sessions")
    fun create(@PathVariable pid: String, @RequestBody(required = false) body: HelperCreate?): JsonNode = helper.create(pid, body ?: HelperCreate())

    @GetMapping("/sessions/{sid}")
    fun get(@PathVariable pid: String, @PathVariable sid: String): JsonNode = helper.get(pid, sid)

    @PatchMapping("/sessions/{sid}")
    fun patch(@PathVariable pid: String, @PathVariable sid: String, @RequestBody body: HelperPatch): JsonNode = helper.patch(pid, sid, body)

    @DeleteMapping("/sessions/{sid}")
    fun delete(@PathVariable pid: String, @PathVariable sid: String): JsonNode = helper.delete(pid, sid)

    @PostMapping("/sessions/{sid}/turn")
    fun turn(@PathVariable pid: String, @PathVariable sid: String, @RequestBody body: HelperTurn): JsonNode = helper.turn(pid, sid, body)

    @PostMapping("/sessions/{sid}/stop")
    fun stop(@PathVariable pid: String, @PathVariable sid: String): JsonNode = helper.stop(pid, sid)

    @GetMapping("/commands")
    fun commands(@PathVariable pid: String): JsonNode = helper.commands(pid)

    @GetMapping("/sessions/{sid}/changes")
    fun changes(@PathVariable pid: String, @PathVariable sid: String): JsonNode = helper.changes(pid, sid)

    @PostMapping("/sessions/{sid}/undo")
    fun undo(@PathVariable pid: String, @PathVariable sid: String, @RequestBody(required = false) body: HelperUndo?): JsonNode =
        helper.undo(pid, sid, body ?: HelperUndo())

    @PostMapping("/sessions/{sid}/done")
    fun done(@PathVariable pid: String, @PathVariable sid: String, @RequestBody(required = false) body: HelperDoneBody?): JsonNode =
        helper.done(pid, sid, body ?: HelperDoneBody())

    @GetMapping("/sessions/{sid}/handover")
    fun handover(@PathVariable pid: String, @PathVariable sid: String): JsonNode = helper.handover(pid, sid)

    @PostMapping("/sessions/{sid}/task")
    fun toTask(@PathVariable pid: String, @PathVariable sid: String, @RequestBody(required = false) body: HelperTaskBody?): TaskView =
        helper.toTask(pid, sid, body ?: HelperTaskBody())

    @PostMapping("/sessions/{sid}/flow")
    fun toFlow(@PathVariable pid: String, @PathVariable sid: String, @RequestBody(required = false) body: HelperFlowBody?): JsonNode =
        helper.toFlow(pid, sid, body ?: HelperFlowBody())

    @GetMapping("/permissions")
    fun permissions(@PathVariable pid: String): JsonNode = helper.permissions(pid)

    @PostMapping("/permissions/{qid}")
    fun answer(@PathVariable pid: String, @PathVariable qid: String, @RequestBody body: HelperAnswer): JsonNode = helper.answer(pid, qid, body)
}
