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
import keel.api.flow.TaskSink
import keel.api.workflows.WorkflowService
import keel.api.mcp.McpService
import keel.api.projects.ProjectService
import keel.api.settings.Model
import keel.api.settings.SettingsService
import keel.api.skills.SkillService
import keel.api.workspace.Workspace
import org.springframework.beans.factory.ObjectProvider
import org.springframework.stereotype.Service
import java.nio.file.Paths

/** A new Helper session: mode "ask" (read only), "edit" (the project folder, no flow needed; 0.18.7), "fix" (while the
 *  project's flow waits at a gate) or "side" (its own
 *  worktree and branch); the model defaults to the helper agent's. */
/** 0.18.57 force: a side session starts although a flow or another side agent works on the same ticket or files. */
data class HelperCreate(val mode: String = "ask", val model: Model? = null, val title: String = "", val force: Boolean = false)
data class HelperUndo(val path: String? = null)
/** Done: the commit's subject as the person wrote it (blank: the chat's title). */
/** keel 0.18.3 `paths`: keep only these of KeelBot's files (keel TUI's /keep <path>); none = every file. */
data class HelperDoneBody(val message: String = "", val paths: List<String>? = null)
/** Hand a side session over: as a task (its description says what was done and on which branch) ... */
data class HelperTaskBody(val title: String = "", val type: String = "task")
/** ... or as a flow on its branch (a change flow by default: it writes the tests for what the branch does). */
data class HelperFlowBody(val title: String = "", val workflowId: String = "change")
/** The person's answer to a permission card: once | always (this command, for the rest of the chat) | deny (with a reason). */
data class HelperAnswer(val decision: String = "", val why: String = "")
/** v0.15.2 folder: a folder id of this project; "" takes the chat out of its folder. 0.18.19 mode: the same chat in
 *  another mode (Fix: the flow that waits now goes with it). */
data class HelperPatch(val title: String? = null, val model: Model? = null, val folder: String? = null, val mode: String? = null)
/** v0.15.2 a folder for KeelBot's chats (create, rename): its name. */
data class HelperFolderBody(val name: String = "")

/** What the person points at in the Repo page: a file, a code-graph symbol or a criterion. */
data class HelperMention(val kind: String = "", val value: String = "", val file: String? = null, val line: Int? = null)
data class HelperSelection(val path: String = "", val from: Int? = null, val to: Int? = null, val text: String = "")
/** 0.18.18 keel TUI's `!command`: the person's own shell command, run in the chat's folder through keel's guard ... */
data class HelperRun(val command: String = "", val timeout: Int? = null)
/** ... and its output, sent along with the person's next question. */
data class HelperShell(val command: String = "", val exit: Int? = null, val output: String = "")

/** One message to KeelBot; the api adds the logins, the MCP servers, the agent's settings and the project's flow. */
data class HelperTurn(
    val text: String = "",
    val model: Model? = null,
    val mentions: List<HelperMention> = emptyList(),
    val selection: HelperSelection? = null,
    val openFile: String? = null,
    val shell: HelperShell? = null,
    /** 0.18.18 image files of the project the person named (keel TUI); the engine sends those the model can see. */
    val images: List<String> = emptyList(),
    /** 0.18.28 stop the running answer and send this one now (Esc / Ctrl+C); else it waits until the answer ends. */
    val interrupt: Boolean? = null,
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
    private val jobs: keel.api.jobs.JobService,
    private val workflows: WorkflowService,
    private val plugins: keel.api.plugins.PluginService,
    private val tasks: ObjectProvider<TaskSink>,
    private val workspace: Workspace,
    private val mapper: ObjectMapper,
    private val approvals: ApprovalService,
) {
    private fun helperAgent(pid: String) = agents.list(pid).firstOrNull { it.id == AGENT }

    /** KeelBot's model: the helper agent's (Agents page), else the project's default model. */
    fun defaultModel(pid: String): Model = helperAgent(pid)?.model ?: settings.effective(pid).defaultModel

    fun create(pid: String, body: HelperCreate): JsonNode {
        val project = projects.require(pid)
        if (body.mode !in MODES) throw BadRequest("Unknown KeelBot mode ${body.mode}", "Use ask, edit, fix or side.")
        // 0.18.57 one owner per task: a side agent on what a flow or another side agent already works on is refused
        if (body.mode == "side" && !body.force) flows.refuseOwners(pid, java.nio.file.Paths.get(project.root), body.title, null, null)
        // 0.22.0 a side or ask agent decides from git ("is this merged?"): fresh remote refs first (a field report: one
        // said a merged branch was not, from a clone that had not fetched in days). At most a few seconds; never fails.
        if (body.mode != "fix") flows.fetchForStart(pid, java.nio.file.Paths.get(project.root))
        val thread = if (body.mode == "fix") waitingThread(pid) else null
        val threadId = thread?.path("thread_id")?.asText()
        return engine.post("/helper/sessions", mapOf(
            "project_id" to pid, "root" to project.root, "mode" to body.mode,
            "model" to (body.model?.checked() ?: defaultModel(pid)), "title" to body.title.take(120),
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
        val folder = body.folder?.trim()
        if (!folder.isNullOrEmpty()) requireFolder(pid, folder)
        val mode = body.mode?.trim()?.takeIf { it.isNotEmpty() }
        if (mode != null && mode !in MODES) throw BadRequest("Unknown KeelBot mode $mode", "Use ask, edit, fix or side.")
        val threadId = if (mode == "fix") waitingThread(pid).path("thread_id").asText() else null
        return engine.patch("/helper/sessions/$sid", mapOf("title" to body.title, "model" to body.model?.checked(), "folder" to folder,
            "mode" to mode, "thread_id" to threadId, "flow" to threadId?.let { fixContext(pid, it) })
            .filterValues { it != null })
    }

    // v0.15.2 folders for the chats: the engine keeps them next to the sessions, one list per project, so every browser
    // sees the same folders. Deleting a folder keeps its chats (they move out of it).
    fun folders(pid: String): JsonNode {
        projects.require(pid)
        return engine.get("/helper/folders?project=$pid")
    }

    fun createFolder(pid: String, body: HelperFolderBody): JsonNode {
        projects.require(pid)
        return engine.post("/helper/folders", mapOf("project_id" to pid, "name" to folderName(body.name)))
    }

    fun renameFolder(pid: String, fid: String, body: HelperFolderBody): JsonNode {
        requireFolder(pid, fid)
        return engine.patch("/helper/folders/$fid", mapOf("name" to folderName(body.name)))
    }

    fun deleteFolder(pid: String, fid: String): JsonNode {
        requireFolder(pid, fid)
        return engine.delete("/helper/folders/$fid")
    }

    private fun folderName(name: String): String {
        if (name.isBlank()) throw BadRequest("A folder needs a name", "Write a short name, for example Payments.")
        return name.trim().take(200)
    }

    /** The folder, if it belongs to this project (a folder id from another project is a 404 here). */
    private fun requireFolder(pid: String, fid: String) {
        if (folders(pid).none { it.path("id").asText() == fid }) throw NotFound("No KeelBot folder $fid in project $pid")
    }

    fun delete(pid: String, sid: String): JsonNode {
        get(pid, sid)
        return engine.delete("/helper/sessions/$sid")
    }

    fun stop(pid: String, sid: String): JsonNode {
        get(pid, sid)
        return engine.post("/helper/sessions/$sid/stop", emptyMap<String, Any>())
    }

    /** P1-2 the engine summarises the chat and goes on from the summary: {before, after} tokens. */
    fun compact(pid: String, sid: String): JsonNode {
        val s = get(pid, sid)
        // 0.18.54 the summary runs the chat's model, so it needs the same login a turn sends (without it a Claude
        // login saved in Connections was missing: "`claude` is not logged in inside the container")
        val model = mapper.treeToValue(s.path("model"), Model::class.java)
        val keys = secrets.engineKeys(model.provider, model.mode) + flows.contributedKeys(pid)
        return engine.post("/helper/sessions/$sid/compact", if (keys.isEmpty()) emptyMap<String, Any>() else mapOf("keys" to keys))
    }

    /** P1-3 the chat as Markdown in the project's .keel/chats/: {path, messages}. */
    fun export(pid: String, sid: String): JsonNode {
        get(pid, sid)
        return engine.post("/helper/sessions/$sid/export", emptyMap<String, Any>())
    }

    /** 0.18.19 Undo KeelBot's last answer (its files back, its messages marked reverted): {n, text, files, messages}. */
    fun undoTurn(pid: String, sid: String): JsonNode {
        get(pid, sid)
        return engine.post("/helper/sessions/$sid/undo-turn", emptyMap<String, Any>())
    }

    /** 0.18.19 Redo the answer undoTurn took back, while no new message was sent: {n, text, files, messages}. */
    fun redoTurn(pid: String, sid: String): JsonNode {
        get(pid, sid)
        return engine.post("/helper/sessions/$sid/redo-turn", emptyMap<String, Any>())
    }

    /** 0.18.18 The person's earlier prompts in this project's chats, newest first, each once: keel TUI's ↑ / ↓. */
    fun prompts(pid: String, limit: Int = 50): JsonNode {
        projects.require(pid)
        return engine.get("/helper/prompts?project=$pid&limit=${limit.coerceIn(1, 500)}")
    }

    fun turn(pid: String, sid: String, body: HelperTurn): JsonNode {
        val text = body.text.trim()
        if (text.isEmpty()) throw BadRequest("The message is empty", "Write a question, or pick a command with /.")
        if (text.length > 20_000) throw BadRequest("The message is too long", "Keep it under 20,000 characters; point at files with @ instead.")
        val s = get(pid, sid)
        val fix = s.path("mode").asText() == "fix"
        val model = body.model?.checked() ?: mapper.treeToValue(s.path("model"), Model::class.java)
        val eff = settings.effective(pid)
        keel.api.settings.LocalOnly.check(eff, listOf(model), "KeelBot")
        val helper = helperAgent(pid)
        val payload = linkedMapOf<String, Any?>(
            "text" to text,
            "model" to body.model?.checked(),
            "keys" to (secrets.engineKeys(model.provider, model.mode) + flows.contributedKeys(pid)).takeIf { it.isNotEmpty() },
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
            "images" to body.images.map { it.trim() }.filter { it.isNotEmpty() && it.length <= 1000 }.distinct().take(16).takeIf { it.isNotEmpty() },
            "shell" to body.shell?.takeIf { it.command.isNotBlank() }?.let { it.copy(command = it.command.take(2000), output = it.output.takeLast(8000)) },
            "interrupt" to body.interrupt?.takeIf { it },
        ).filterValues { it != null }
        return engine.post("/helper/sessions/$sid/turn", payload)
    }

    /** 0.18.18 keel TUI's `!command`: the engine runs it in the chat's folder through keel's guard (a command that
     *  changes something waits for the person's OK, as KeelBot's own do): {command, exit, output, refused}. It may wait
     *  for that OK (up to 10 minutes), then for the command. */
    fun run(pid: String, sid: String, body: HelperRun): JsonNode {
        val cmd = body.command.trim()
        if (cmd.isEmpty()) throw BadRequest("The command is empty", "Write it after the !, for example !git status.")
        if (cmd.length > 8000) throw BadRequest("The command is too long", "Keep it under 8,000 characters.")
        get(pid, sid)
        return engine.postWaiting("/helper/sessions/$sid/run", mapOf("command" to cmd, "timeout" to body.timeout?.coerceIn(1, 3600))
            .filterValues { it != null })
    }

    fun commands(pid: String): JsonNode =
        engine.post("/helper/commands", mapOf("root" to projects.require(pid).root, "plugins" to plugins.enabled(pid)))

    // ---- Fix mode: KeelBot's changes, Undo, Done, and the permission cards -----------------------------

    /** Every file KeelBot changed in this chat (not kept or undone); `turn` (0.18.20): only the files the answer to that
     *  user message changed (keel TUI's block after an answer). */
    fun changes(pid: String, sid: String, turn: Int? = null): JsonNode {
        get(pid, sid)
        return engine.get("/helper/sessions/$sid/changes" + (turn?.let { "?turn=$it" } ?: ""))
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
            "edit" -> emptyMap()            // keel 0.18.7 Keep: the checks, then keel's commit in the project folder
            else -> throw BadRequest("Only a Fix, Edit or side chat has changes to commit")
        }
        val eff = settings.effective(pid)
        val commit = mapOf("commit_author" to eff.commitAuthor.trim().ifBlank { null }, "commit_coauthor" to eff.commitCoauthor)
            .filterValues { it != null }
        return engine.post("/helper/sessions/$sid/done", mapOf("flow" to flow, "message" to body.message.trim().take(200),
            "paths" to body.paths?.map { it.trim() }?.filter { it.isNotEmpty() }?.takeIf { it.isNotEmpty() },
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

    /**
     * A task whose description says what the side session did and where the work is (its branch stays). The Tasks
     * plugin makes it (a [TaskSink]); without that plugin: 409.
     */
    fun toTask(pid: String, sid: String, body: HelperTaskBody): Any {
        val h = handover(pid, sid)
        val sink = tasks.orderedStream().findFirst().orElse(null)
            ?: throw Conflict("Tasks are not installed in this keel", "Add the Tasks plugin, or hand the side session over as a flow.")
        val title = body.title.trim().ifBlank { h.path("title").asText() }.take(300)
        return sink.create(pid, title, handoverText(h), body.type)
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
        val dirty = workspace.git(root, "status", "--porcelain", "--untracked-files=all").out.lines().filter { it.length > 3 }
            .map { it.substring(3).trim() }.filterNot { WorkspaceDoctor.isEngineFile(it) }
        if (dirty.isNotEmpty())
            throw Conflict("The project folder has uncommitted changes: ${dirty.take(5).joinToString()}", "Commit or stash them first: the folder checks out the side branch.")
        val branch = h.path("branch").asText()
        // the checkout comes first: when it fails the session is still a side session with its worktree (the release
        // cannot be taken back). The worktree still holds the branch, so git must be told that is fine.
        val was = workspace.git(root, "rev-parse", "--abbrev-ref", "HEAD").out.trim()
        val r = workspace.git(root, "checkout", "-q", "--ignore-other-worktrees", branch)
        if (!r.ok) throw Conflict("Could not check out $branch", r.err.ifBlank { r.out }.take(300))
        try {
            engine.post("/helper/sessions/$sid/release", emptyMap<String, Any>())
        } catch (e: Exception) {
            if (was.isNotBlank() && was != "HEAD") workspace.git(root, "checkout", "-q", was)      // nothing changed after all
            throw e
        }
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
            "waiting" to waiting?.let { mapOf("title" to it.path("title").asText(), "name" to it.path("name").asText("").ifBlank { null },
                "detail" to it.path("detail").asText("").take(6000)) + askedOf(it) },
            // 0.18.15 what runs now and what the latest agents and code steps ended with (a doctor's diagnosis, a
            // ladder's failing rung): KeelBot could only see the step's name before
            "current" to t.path("current").asText("").ifBlank { null },
            "jobs" to t.path("thread_id").asText("").ifBlank { null }?.let { tid -> runCatching { jobs.recentOfThread(tid) }.getOrNull() },
        )
    }

    /** keel 0.19.0: the waiting question's structure for KeelBot's answer button (keel-flow {"do": "answer"}): the
     *  questions' ids and option labels, and the named choices with whether each needs a reason and the plain approve's. */
    private fun askedOf(w: JsonNode): Map<String, Any?> {
        val out = mutableMapOf<String, Any?>()
        val qs = w.path("questions").takeIf { it.isArray && it.size() > 0 }
        if (qs != null) out["questions"] = qs.map { q ->
            mapOf("id" to q.path("id").asText(), "question" to q.path("question").asText(), "multiple" to q.path("multiple").asBoolean(false),
                "options" to q.path("options").map { it.path("label").asText() },
                "recommended" to q.path("options").filter { it.path("recommended").asBoolean(false) }.map { it.path("label").asText() })
        }
        val info = w.path("choice_info").takeIf { it.isArray && it.size() > 0 }
        if (info != null) out["choices"] = info.map { c ->
            mapOf("name" to c.path("name").asText(), "label" to c.path("label").asText(), "why" to c.path("why").asText("optional"),
                // 0.18.28 what each choice really does: KeelBot checks its advice against it
                "description" to c.path("description").asText(""), "default" to c.path("default").asBoolean(false))
        }
        else w.path("choices").takeIf { it.isArray && it.size() > 0 }?.let { names ->
            out["choices"] = names.mapIndexed { i, n -> mapOf("name" to n.asText(), "label" to n.asText(), "why" to "optional", "default" to (i == 0)) }
        }
        // 0.18.28 what a plain approve and a plain reject do at this gate, and whether the reason is used
        w.path("plain").takeIf { it.isObject }?.let { pl ->
            out["plain"] = listOf("approve", "reject").mapNotNull { k ->
                pl.path(k).takeIf { it.isObject }?.let { e -> k to mapOf("does" to e.path("does").asText(), "reason" to e.path("reason").asText()) }
            }.toMap()
        }
        return out
    }

    /** v0.9.0, KeelBot's view of keel (engine runtime/keelbot.py): the workflows it can suggest, and the project's flows. */
    private fun keelContext(pid: String): Map<String, Any?> {
        val wfs = runCatching { workflows.list(pid) }.getOrDefault(emptyList())
        val runs = runCatching { flows.runs(pid, null, 8) }.getOrDefault(emptyList())
        // the waiting flow's question structure (ids, choice names) for KeelBot's answer button; one engine read
        val now = runCatching { flows.flow(pid).thread }.getOrNull()
        val asked = now?.path("waiting")?.takeIf { it.isObject }?.let { askedOf(it) }.orEmpty()
        val nowId = now?.path("thread_id")?.asText()
        return mapOf(
            "workflows" to wfs.map { w ->
                mapOf("id" to w.id, "name" to w.name, "source" to w.source, "based_on" to w.basedOn, "folder" to w.folder,
                    "last_run" to w.lastRun?.let { mapOf("title" to it.title, "status" to it.status) },
                    "steps" to w.steps.map { st -> mapOf("id" to st.id, "kind" to st.kind, "name" to st.name, "agent" to st.agent) })
            },
            "flows" to runs.map { r ->
                mapOf("thread_id" to r.threadId, "title" to r.title, "workflow" to r.workflowId, "status" to r.status,
                    "phase" to r.phase, "step" to r.current,
                    "waiting" to r.waiting?.let { mapOf("title" to it) + (if (r.threadId == nowId) asked else emptyMap()) },
                    "acs_done" to r.acsDone, "acs_total" to r.acsTotal, "tokens" to r.tokens, "where" to r.where,
                    "branch" to r.branch, "error" to r.error, "updated_at" to r.updatedAt)
            },
        )
    }

    companion object {
        const val AGENT = "helper"
        val MODES = setOf("ask", "edit", "fix", "side")
    }
}
