package keel.api.flow

import com.fasterxml.jackson.annotation.JsonAnyGetter
import com.fasterxml.jackson.annotation.JsonIgnore
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.agents.AgentService
import keel.api.agents.Knowledge
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.KeelProperties
import keel.api.common.Slug
import keel.api.workspace.Workspace
import java.nio.file.Path
import java.nio.file.Paths
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.connections.SecretService
import keel.api.doctor.WorkspaceDoctor
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.engine.EngineError
import keel.api.events.EventHub
import keel.api.mcp.McpServerSpec
import keel.api.mcp.McpService
import keel.api.projects.ProjectService
import keel.api.budget.CapPlanner
import keel.api.budget.CapText
import keel.api.budget.ProviderUsageService
import keel.api.settings.LocalOnly
import keel.api.settings.Model
import keel.api.settings.SettingsService
import keel.api.skills.SkillService
import keel.api.workflows.Workflow
import keel.api.workflows.WorkflowService
import keel.api.workflows.wantsOwnBranch
import org.springframework.beans.factory.ObjectProvider
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
    /** What the FlowContributor beans add, by the engine's names (the Plugins part: `plugins`, the ones on). */
    @get:JsonIgnore val contributed: Map<String, Any?> = emptyMap(),
    /** v0.10.0: git:push and git:pr follow pushPr; git:branch the pattern. */
    @get:JsonIgnore val pushPr: String? = null,
    @get:JsonIgnore val branchPattern: String? = null,
) {
    /** Sent after the fields above: the contributed settings, then push_pr and branch_pattern (the order the engine always got). */
    @JsonAnyGetter
    fun tail(): Map<String, Any?> = contributed + linkedMapOf("push_pr" to pushPr, "branch_pattern" to branchPattern)
}

/** The phases in which nothing may be edited (the engine's rules.read_only: every action denied). */
val READ_ONLY_PHASES = setOf("review")

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
    /** B14 the tokens the estimate gave at the start (threads.estimate: the Budget page's estimate column); never sent. */
    @get:com.fasterxml.jackson.annotation.JsonIgnore
    val estimate: Long? = null,
    /** keel 0.18.64 every other project registered in this keel: the flow's agents may read them, never change them. */
    val otherProjects: List<OtherProject> = emptyList(),
)

/** One entry of StartThread.other_projects. */
data class OtherProject(val id: String, val name: String, val root: String)

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
/** v0.9.0: one flow of the project's history (the Flow page's list, the Workflows page, KeelBot). */
data class RunRow(
    val threadId: String, val title: String, val workflowId: String?, val status: String, val phase: String?,
    val current: String?, val waiting: String?, val acsDone: Int, val acsTotal: Int, val tokens: Long,
    val where: String, val branch: String?, val error: String?, val createdAt: String, val updatedAt: String,
    /** v0.15.4 what the flow cost (its own usage, else the sum of its agent calls) */
    val costUsd: Double = 0.0,
    /** v0.15.4 the project's newest flow (by start): only this one, when it was stopped, can be resumed from the history */
    val latest: Boolean = false,
    /** 0.18.57 what it works on, when its start named it (review-pr: pr:20, branch:feature/x) */
    val source: String? = null,
    /** 0.18.57 the newer run of the same source that replaced this failed or stopped one */
    val replacedBy: String? = null,
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
    private val workspace: Workspace,
    private val props: KeelProperties,
    private val providerUsage: ProviderUsageService,
    private val notifications: keel.api.notifications.NotificationService,
    private val capPlanner: CapPlanner,
    private val contributors: ObjectProvider<FlowContributor>,
    private val refusals: ObjectProvider<StartRefusal>,
    private val docs: keel.api.knowledge.DocHistoryService,
) {
    private val log = org.slf4j.LoggerFactory.getLogger(javaClass)

    /** Every contributor's secrets for this project's agent calls and KeelBot's turns (a later one wins a clash). */
    fun contributedKeys(pid: String): Map<String, String> =
        contributors.orderedStream().toList().fold(linkedMapOf<String, String>()) { acc, c -> acc.putAll(c.keys(pid)); acc }

    /** Every contributor's settings for the engine, in order. */
    private fun contributedSettings(pid: String): Map<String, Any?> =
        contributors.orderedStream().toList().fold(linkedMapOf<String, Any?>()) { acc, c -> acc.putAll(c.settings(pid)); acc }

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
        // A start without a cap (the API, MCP keel_start_flow, keel2 /flow, a KeelBot button) gets what the Start-a-flow
        // drawer gives: the Settings cap, raised to the estimate's high end + 30% when that is above it. The caps still bind.
        // B15 only keel's default cap is raised: a cap someone saved in Settings (General or this project) is the cap.
        val capSaved = settings.isSet(pid, "cap_tokens")
        // B14 one estimate per start: the Budget page compares it with what the flow used, and a cap may follow it
        val est = estimateAtStart(pid, wf.yaml, acs?.size)
        val wanted = if (cap?.capTokens == null) capForEstimate(est, s.capTokens) else null
        val raised = wanted?.takeIf { !capSaved }
        val limits = capPlanner.limits(pid, cap?.capTokens ?: raised, cap?.onCap, cheaperOk)
        limits.refused?.let { throw Conflict(it.error, it.hint) }
        val raisedNote = raised?.takeIf { limits.tokensFrom == "flow" }?.let {
            "keel raised this flow's cap from ${CapText.tokens(s.capTokens.toDouble())} (Settings) to ${CapText.tokens(it.toDouble())}: " +
                "the estimate's high end plus 30%."
        }
        val keptNote = wanted?.takeIf { capSaved && limits.tokensFrom == "settings" }?.let {
            "This flow keeps the cap of ${CapText.tokens(s.capTokens.toDouble())} saved in Settings, below the estimate's high end plus 30% " +
                "(${CapText.tokens(it.toDouble())}): it may reach the cap before it is done."
        }
        val notes = listOfNotNull(raisedNote, keptNote) + limits.notes
        if (limits.cheaper) models.keys.toList().forEach { models[it] = s.cheaperModel }
        // 0.18.46 local models only: the models this workflow's agents run on (and the cheaper one, when a cap may switch to it)
        keel.api.settings.LocalOnly.check(s, used.map { models[it] ?: models.getValue("default") } + models.getValue("default") +
            listOfNotNull(s.cheaperModel.takeIf { s.onCap == "cheaper" }), "this flow's agents")

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
                commitAuthor = s.commitAuthor.trim().ifBlank { null }, commitCoauthor = s.commitCoauthor,
                contributed = contributedSettings(pid), pushPr = s.pushPr, branchPattern = s.branchPattern),
            mcp = mcp.specsFor(s.mcp), skills = skillText,
            agents = all.filter { it.enabled }.associate { it.id to AgentStart(it.knowledge) },
            capNote = notes.takeIf { it.isNotEmpty() }?.joinToString(" "),
            estimate = est?.path("tokens")?.asLong(0)?.takeIf { it > 0 },
            otherProjects = projects.rows().filter { it.id != pid }.map { OtherProject(it.id, it.name, it.root) },
        )
    }

    /** The estimate of a flow that starts now, with the criteria given (none yet: 3, as the drawer); null when it fails. */
    private fun estimateAtStart(pid: String, yaml: String, acs: Int?): JsonNode? {
        if (yaml.isBlank()) return null
        return runCatching { estimateYaml(pid, yaml, acs?.takeIf { it > 0 } ?: 3) }
            .onFailure { log.warn("no estimate for the flow in {}: {}", pid, it.message) }.getOrNull()
    }

    /**
     * The cap a flow started without one should get (what web/src/components/StartFlow.tsx capForEstimate does): the
     * estimate's high end plus 30%, rounded up to 10k, when that is above the Settings cap; null when the Settings cap is
     * enough, is off (0) or there is no estimate ([est]: estimateAtStart).
     */
    private fun capForEstimate(est: JsonNode?, settingsCap: Int): Int? {
        if (settingsCap <= 0 || est == null) return null
        val high = est.path("high").asLong(0)
        if (high <= 0) return null
        val want = Math.ceil(high * 1.3 / 10_000).toLong() * 10_000
        return if (want > settingsCap) want.coerceAtMost(Int.MAX_VALUE.toLong()).toInt() else null
    }

    /**
     * [force] 0.18.57 one owner per task: null (keel's own starts: a review child, a hand-over) checks nothing; false (the
     * dashboard, MCP keel_start_flow, Tasks › Start) refuses while another flow or side agent of the project works on
     * the same ticket, branch or files; true starts anyway.
     */
    fun start(pid: String, workflowId: String, title: String, acs: List<Ac>?, cap: FlowCap? = null,
              allowFake: Boolean = false, allowDirty: Boolean = false, request: String? = null,
              options: Map<String, Any?>? = null, where: String? = null, model: Model? = null,
              ownBranch: Boolean = true, branch: String? = null, force: Boolean? = null, base: String? = null): JsonNode {
        if (title.isBlank()) throw BadRequest("Give the flow a title", "One short line: what should this flow build or fix?")
        cap?.check()
        var start = buildStart(pid, workflowId, title, acs, cap, model)
        // B7 a start that named no branch mode: ship and the other flows that finish the folder's branch continue it
        val mode = if (branch == DEFAULT_BRANCH) defaultBranch(workflowId, start.workflow.basedOn) else branch
        val root = Paths.get(start.root)
        refuseFake(start, root, allowFake)
        if (force == false) refuseOwners(pid, root, title, request, options, if (mode == "continue") projects.branch(root) else null)
        // 0.18.57 a read-only flow (review, review-pr: every step in a phase that may edit nothing) reads refs and works
        // in throwaway worktrees the engine removes: it never gets a branch or a worktree of its own, never checks
        // anything out in the person's folder and does not mind their uncommitted files (keel 0.18.56 made and checked
        // out feat/review-<title> in the folder of a PR review)
        val readOnly = readOnly(start.workflow)
        // 0.18.64 (field report bug 4) the branch the flow's new branch starts from, when the person named one
        val from = base?.trim()?.takeIf { it.isNotEmpty() }
        if (from != null) {
            if (readOnly) throw BadRequest("A read-only flow gets no branch of its own: base does not apply to it")
            if (mode == "continue") throw BadRequest("base starts a new branch from it; branch continue stays on the folder's branch",
                "Pass one of them.")
            if (!ownBranch || !wantsOwnBranch(start.workflow))
                throw BadRequest("This flow works on the branch the folder is on: it takes no base")
        }
        // 0.18.64 every remote fetched before the flow's branch is made, so the branch can start from the newer code; the
        // engine's start note says how far the base is behind each remote and which fetch failed. Never fails the start.
        // A read-only flow fetches the refs it reads itself: none here, and [] tells the engine not to either.
        val fetched: Any? = when {
            isDemo(root) || workspace.gitOut(root, "remote").isNullOrBlank() -> null
            readOnly -> emptyList<Any>()
            else -> fetchForStart(pid, root)
        }
        if (from != null && !workspace.git(root, "rev-parse", "--verify", "--quiet", "$from^{commit}").ok)
            throw BadRequest("There is no branch $from to start from",
                "Name a local branch or <remote>/<branch>; keel_fetch_remotes fetches the remotes and lists them.")
        // 0.18.65 (field report R5) base_branch names no local branch while a remote has it: the person chooses (use the
        // remote's, a local branch that tracks it, or another branch); keel never guesses and no agent edits the config
        if (from == null && !readOnly && !isDemo(root) && mode != "continue" && ownBranch && wantsOwnBranch(start.workflow)) {
            val info = workspace.baseInfo(root)
            if (info.mustAsk) throw Conflict(
                "base_branch ${info.configured} in .keel/config.yml names no local branch here; ${info.remote} has it",
                "Choose once (Settings › General › Base branch, the start dialog, or keel_base_branch): use ${info.remote}, " +
                    "create a local ${info.name} that tracks ${info.remote}, or pick another branch. Or start this flow with base ${info.remote}.")
        }
        // the project folder, or (v0.7.x) a worktree of its own next to the flow that runs or waits there
        val wt = if (!readOnly && inWorktree(pid, where, root)) worktreeFor(pid, root, title, workflowId, from) else null
        var madeBranch: MadeBranch? = null
        if (readOnly) {
            // nothing to do: the folder keeps its branch, its HEAD and its files
        } else if (wt == null) {
            refuseDirty(root, allowDirty)
            // an add-on's flow that only writes its own documents (keel Product) stays on the branch it is on, and so
            // does a workflow that says `own_branch: false` (0.18.57 hunt: a report needs no branch; hunt-next hands over)
            if (ownBranch && wantsOwnBranch(start.workflow)) madeBranch = ownBranch(pid, root, title, workflowId, mode, from)
        } else {
            start = start.copy(root = wt.path)
        }
        // what the start fetched goes to the engine, which then does not fetch again (null: no remote, or no answer)
        val data = if (fetched == null) options?.takeIf { it.isNotEmpty() } else options.orEmpty() + ("remotes_fetched" to fetched)
        // API keys for "api" models and CLI logins for subscription models, from the encrypted secrets table; the
        // cheaper model's too, so a cap that switches to it mid-flow can run it.
        val body = try {
            val keys = keysFor(start.models.values + listOfNotNull(start.settings.cheaperModel)) + contributedKeys(pid)
            val withRequest = start.copy(request = request?.trim()?.takeIf { it.isNotEmpty() }?.take(8000),
                data = data)
            if (keys.isEmpty()) withRequest else withRequest.copy(keys = keys)
        } catch (e: Exception) {
            wt?.let { removeWorktreeAt(root, it.name) }
            madeBranch?.let { undoBranch(root, it) }
            throw e
        }
        val res = try {
            engine.startThread(body)
        } catch (e: Exception) {
            wt?.let { removeWorktreeAt(root, it.name) }
            madeBranch?.let { undoBranch(root, it) }
            // v0.16.0 a part may answer the refusal better (needs_plugins: install requests for the missing plugins)
            if (e is EngineError) {
                val name = start.workflow.name.ifBlank { workflowId }
                refusals.orderedStream().toList().firstNotNullOfOrNull { it.answer(e, pid, name) }?.let { throw it }
            }
            throw e
        }
        val tid = res.get("thread_id")?.asText() ?: throw ApiException(HttpStatus.BAD_GATEWAY, "The engine did not return a thread id")
        val now = Time.now()
        jdbc.update(
            "INSERT OR IGNORE INTO threads(id, project_id, workflow_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'running', ?, ?)",
            tid, pid, workflowId, title, now, now,
        )
        jdbc.update("UPDATE threads SET workflow_id = ?, title = ?, project_id = ? WHERE id = ?", workflowId, title, pid, tid)
        jdbc.update("UPDATE threads SET worktree = ?, branch = ?, estimate = COALESCE(?, estimate) WHERE id = ?", wt?.name, wt?.branch, start.estimate, tid)
        res.get("source")?.asText()?.takeIf { it.isNotBlank() }?.let { markReplaced(pid, workflowId, tid, it) }
        // v0.15.9 what this flow applied on top of the repo starts with where it works (DocHistoryService)
        runCatching {
            (wt?.branch ?: projects.branch(root))?.let { docs.add(pid, tid, "branch", it, "the flow's branch") }
            wt?.let { docs.add(pid, tid, "worktree", it.name, it.path) }
        }.onFailure { log.warn("could not record the flow's branch for {}: {}", tid, it.message) }
        val state = runCatching { engine.thread(tid) }.getOrElse { res }
        save(tid, state)
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        val note = start.capNote
        // 0.18.64 how far the base is behind each remote, a failed fetch said plainly (the engine's start note)
        val baseNote = res.get("base_note")?.asText()?.takeIf { it.isNotBlank() }
        if ((note == null && baseNote == null) || state !is com.fasterxml.jackson.databind.node.ObjectNode) return state
        return state.deepCopy().also { s -> note?.let { s.put("cap_note", it) }; baseNote?.let { s.put("base_note", it) } }
    }

    /** 0.18.64 every remote of the project fetched before a start (engine POST /remotes/fetch: all at once, at most 20 s
     *  each), with keel's GitHub login for a remote on its host: the result per remote, or null when the engine could
     *  not answer. Never fails the start. */
    private fun fetchForStart(pid: String, root: Path): JsonNode? =
        runCatching { engine.post("/remotes/fetch", mapOf("root" to root.toString(), "keys" to contributedKeys(pid).takeIf { it.isNotEmpty() }), long = true) }
            .onFailure { log.warn("no fetch before the start in {}: {}", pid, it.message) }
            .getOrNull()?.get("fetched")?.takeIf { it.isArray }

    /** 0.18.64 keel_fetch_remotes: every remote of the project fetched, one result per remote (ok, or failed and why), and
     *  how far the base branch is behind each remote's copy and default branch (the engine's report). */
    fun fetchRemotes(pid: String): JsonNode {
        val root = projects.root(pid)
        if (!workspace.git(root, "rev-parse", "--git-dir").ok) throw BadRequest("This project is not a git repository: it has no remotes")
        val res = engine.post("/remotes/fetch", mapOf("root" to root.toString(), "keys" to contributedKeys(pid).takeIf { it.isNotEmpty() }), long = true)
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        return res
    }

    /**
     * 0.18.64 (field report bug 6) keel merges ([mode] merge, the default) or rebases a flow's branch onto [onto] (default:
     * the base branch's remote copy, else the local base), in the folder or the worktree the flow works in (engine POST
     * /threads/{id}/sync). [action] start, continue (after the conflicts were resolved: keel commits the resolution) or
     * abort. A conflict answers ok false with the files; the engine refuses while a step runs.
     */
    fun syncBranch(tid: String, onto: String?, mode: String?, action: String?): JsonNode {
        val m = mode?.trim()?.ifBlank { null } ?: "merge"
        val a = action?.trim()?.ifBlank { null } ?: "start"
        if (m !in setOf("merge", "rebase")) throw BadRequest("mode cannot be \"$m\"", "Pick one of: merge, rebase")
        if (a !in setOf("start", "continue", "abort")) throw BadRequest("action cannot be \"$a\"", "Pick one of: start, continue, abort")
        val pid = threadProject(tid) ?: throw NotFound("No flow $tid")
        val res = engine.post("/threads/$tid/sync", mapOf("onto" to onto?.trim()?.ifBlank { null }, "mode" to m, "action" to a,
            "root" to rootNow(tid)).filterValues { it != null }, long = true)
        res.get("thread")?.takeIf { it.isObject }?.let { save(tid, it) }
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        return res
    }

    /**
     * 0.18.57 The new run [tid] works on [source] (the engine's start names it: review-pr's pr:20). Earlier failed or
     * stopped runs of the same workflow and source are marked replaced by it: they stay in the history, marked, instead
     * of piling up as look-alike failures.
     */
    fun markReplaced(pid: String, workflowId: String, tid: String, source: String) {
        jdbc.update("UPDATE threads SET source = ? WHERE id = ?", source.take(300), tid)
        jdbc.update("UPDATE threads SET replaced_by = ? WHERE project_id = ? AND workflow_id = ? AND source = ? AND id <> ? " +
            "AND status IN ('failed','stopped') AND replaced_by IS NULL", tid, pid, workflowId, source.take(300), tid)
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

    /** 0.18.57 one owner per task: the flows and side agents of the project that already work on the same ticket, branch
     *  or files as a new start, each as "flow <title> at <step>" / "side agent <id>" with why. */
    fun owners(pid: String, root: Path, title: String, request: String?, options: Map<String, Any?>?, branch: String? = null,
               exceptSession: String? = null): List<Map<String, String>> {
        val ticket = ticketOf(options?.get("ticket")?.toString(), title, request)
        val wanted = pathsIn("$title\n${request.orEmpty()}")
        val out = mutableListOf<Map<String, String>>()
        val base = workspace.baseInfo(root).ref ?: workspace.base(root)     // 0.18.65 the canonical remote's copy
        jdbc.query("SELECT id, title, current, worktree, branch, state_json FROM threads WHERE project_id = ? AND status IN ('running','waiting') ORDER BY created_at",
            { rs, _ -> listOf(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5), rs.getString(6)) }, pid,
        ).forEach { row ->
            val (id, t, current, wt, br) = row
            val json = row[5]
            val st = runCatching { mapper.readTree(json ?: "{}") }.getOrNull()
            val theirTicket = ticketOf(st?.path("data")?.path("ticket")?.asText(null), t.orEmpty(), null)
            val dir = if (wt.isNullOrBlank()) root else root.resolve(".keel/worktrees").resolve(wt)
            val why = when {
                ticket != null && ticket == theirTicket -> "the same ticket $ticket"
                branch != null && br != null && branch == br -> "the same branch $branch"
                else -> sharedFiles(wanted, changedFiles(dir, base) + pathsIn(t.orEmpty()))?.let { "the same files: ${it.take(5).joinToString()}" }
            } ?: return@forEach
            out += mapOf("kind" to "flow", "id" to id, "who" to "flow ${t ?: id.take(8)} at ${current ?: "its start"}", "why" to why)
        }
        val sessions = runCatching { engine.get("/helper/sessions?project={pid}", mapOf("pid" to pid)) }.getOrNull()
        sessions?.forEach { s ->
            val sid = s.path("id").asText()
            if (s.path("mode").asText() != "side" || sid == exceptSession) return@forEach
            val path = s.path("worktree").asText("")
            if (path.isBlank() || !java.nio.file.Files.isDirectory(Paths.get(path))) return@forEach
            val t = s.path("title").asText("")
            val why = when {
                ticket != null && ticket == ticketOf(null, t, null) -> "the same ticket $ticket"
                branch != null && branch == s.path("branch").asText("") -> "the same branch $branch"
                else -> sharedFiles(wanted, changedFiles(Paths.get(path), s.path("base_sha").asText(null)) + pathsIn(t))
                    ?.let { "the same files: ${it.take(5).joinToString()}" }
            } ?: return@forEach
            out += mapOf("kind" to "side", "id" to sid, "who" to "side agent $sid (${t.take(60)})", "why" to why)
        }
        return out
    }

    /** The owners as a refusal (409 with `owners`): who works on it, and how to start anyway. */
    fun refuseOwners(pid: String, root: Path, title: String, request: String?, options: Map<String, Any?>?, branch: String? = null,
                     exceptSession: String? = null) {
        val found = owners(pid, root, title, request, options, branch, exceptSession)
        if (found.isEmpty()) return
        val lines = found.joinToString("; ") { "${it["who"]} works on ${it["why"]}" }
        throw ApiException(HttpStatus.CONFLICT, "Someone already works on this: $lines",
            "Two agents on one task write over each other. Wait for it, stop it, or start anyway (force).",
            mapOf("owners" to found))
    }

    /** keel commits only what its agents change; uncommitted work in the tree is a reason to stop and ask first.
     *  0.18.57 tool folders that are nobody's work (.serena/, .idea/, .vscode/, .fleet/, .DS_Store,
     *  .claude/settings.local.json) never block; the refusal lists the untracked paths that can be ignored instead
     *  (`ignorable`: Ignore this path adds it to .git/info/exclude, POST /projects/{pid}/doctor/workspace/exclude). */
    private fun refuseDirty(root: Path, allowDirty: Boolean) {
        if (allowDirty) return
        val r = workspace.git(root, "status", "--porcelain", "--untracked-files=all")
        if (!r.ok) return
        // v0.15.8 layout keel / external: .keel/ holds keel's own files, outside git (the engine hides it at flow start)
        val layout = keel.api.knowledge.Layout.resolve(root)
        val hidden = layout.hidden
        // 0.18.64 nor keel's own output under .keel/ (specs, knowledge, docs …) in any layout: keel's files never block keel
        val keelFolders = layout.folders() + layout.runbooks
        val files = r.out.lines().filter { it.length > 3 }.map { it.substring(3).trim().removeSurrounding("\"") }
            .filterNot { WorkspaceDoctor.isEngineFile(it) || WorkspaceDoctor.isToolFile(it) || (hidden && (it == ".keel" || it.startsWith(".keel/"))) ||
                WorkspaceDoctor.isKeelOutput(it, keelFolders) }
        if (files.isEmpty()) return
        // the untracked entries as git shows them by default (a new folder once, not every file in it); a folder only
        // when a file that blocks is in it (.claude/ holding only settings.local.json is a tool's folder)
        val ignorable = workspace.git(root, "status", "--porcelain", "--untracked-files=normal").out.lines()
            .filter { it.startsWith("?? ") }.map { it.substring(3).trim().removeSurrounding("\"") }
            .filter { entry -> if (entry.endsWith("/")) files.any { it.startsWith(entry) } else entry in files }
        val ignoreHint = if (ignorable.isEmpty()) "" else
            " A path that is not your work (a tool's folder, notes): Ignore this path adds it to .git/info/exclude (this computer only) — ${ignorable.take(5).joinToString()}."
        throw ApiException(HttpStatus.CONFLICT,
            "This project has uncommitted changes (${files.size} file${if (files.size == 1) "" else "s"}): ${files.take(5).joinToString()}${if (files.size > 5) ", …" else ""}",
            "Commit or stash them first, so keel's commits hold only what its agents wrote. Or tick \"Start anyway\": your files then stay out of keel's commits.$ignoreHint",
            mapOf("dirty" to files.take(50), "ignorable" to ignorable.take(50)),
        )
    }

    /** 0.18.57 a KeelBot chat that answered this recently still reads the project folder (folderActivity). */
    private val ACTIVE_CHAT_MINUTES = 30L

    /**
     * 0.18.57 who else reads the project folder now: Start a flow warns before a flow on its own branch checks it out
     * there (a chat would silently read other code). KeelBot's Ask and Edit chats that answer now or answered in the
     * last [ACTIVE_CHAT_MINUTES] minutes (a side session has its own worktree), the flow that runs or waits there, and
     * whether a worktree flow can run the tests: the engine's start check when this keel has one (the container-only
     * detection), else null (not known).
     */
    fun folderActivity(pid: String): Map<String, Any?> {
        val project = projects.require(pid)
        val since = java.time.Instant.now().minus(java.time.Duration.ofMinutes(ACTIVE_CHAT_MINUTES))
        val sessions = runCatching { engine.get("/helper/sessions?project={pid}", mapOf("pid" to pid)) }.getOrNull()
        val chats = sessions?.filter { s ->
            val mode = s.path("mode").asText()
            val at = s.path("updated_at").asText().let { t ->
                runCatching { java.time.OffsetDateTime.parse(t).toInstant() }.getOrNull() ?: runCatching { java.time.Instant.parse(t) }.getOrNull()
            }
            s.path("project").asText(pid) == pid && mode in setOf("ask", "edit") && (s.path("worktree").isNull || s.path("worktree").isMissingNode || s.path("worktree").asText().isBlank()) &&
                (s.path("status").asText() == "running" || (at != null && at.isAfter(since)))
        }?.map { s ->
            mapOf("id" to s.path("id").asText(), "title" to s.path("title").asText(), "mode" to s.path("mode").asText(),
                "status" to s.path("status").asText(), "updated_at" to s.path("updated_at").asText(null))
        }.orEmpty()
        val worktreeOk = runCatching { engine.get("/start-check?root={root}", mapOf("root" to project.root)) }.getOrNull()
            ?.path("worktree")?.path("ok")?.takeIf { it.isBoolean }?.asBoolean()
        return mapOf("chats" to chats, "flow" to folderFlow(pid), "worktree_ok" to worktreeOk)
    }

    /** The flow that runs or waits in the project folder itself (not in a worktree), if one does. */
    fun folderFlow(pid: String): String? = jdbc.query(
        "SELECT id FROM threads WHERE project_id = ? AND worktree IS NULL AND status IN ('running','waiting') ORDER BY updated_at DESC LIMIT 1",
        { rs, _ -> rs.getString(1) }, pid,
    ).firstOrNull()

    /** 0.18.57 what a start should know (engine GET /start-check): can a worktree flow reach the tests, is the base
     *  branch behind its remote. Null when the engine cannot say (an older engine, the engine down). */
    fun startCheck(pid: String): JsonNode? {
        val root = projects.root(pid)
        return runCatching { engine.get("/start-check?root={root}", mapOf("root" to root.toString())) }
            .onFailure { log.debug("no start check for {}: {}", pid, it.message) }.getOrNull()
    }

    /** Where a new flow runs: auto = the project folder when it is free, else a worktree of its own next to that flow.
     *  0.18.57 a project whose tests run in a container that sees only the project folder: auto never picks a
     *  worktree (its tests would run on the folder's code), and a second flow waits until the folder is free. */
    private fun inWorktree(pid: String, where: String?, root: Path? = null): Boolean {
        val busy = folderFlow(pid) != null
        val choice = where?.trim()?.lowercase()
        if (busy && (choice == null || choice == "" || choice == "auto")) {
            val fit = startCheck(pid)?.path("worktree")
            if (fit != null && fit.path("ok").isBoolean && !fit.path("ok").asBoolean()) {
                val reason = fit.path("reason").asText("its tests cannot run in a worktree")
                throw Conflict("A flow already runs in the project folder, and this project's flows run there: $reason",
                    "Wait until that flow ends (or stop it), then start this one. Only one flow at a time can test this project.")
            }
        }
        return when (choice) {
            null, "", "auto" -> busy
            "worktree" -> true
            "folder" -> if (busy) throw Conflict("A flow already runs in the project folder",
                "Start this one in its own worktree (it runs next to the other one), or wait until that flow ends.") else false
            // v0.13.0 a flow that writes only its own files (an add-on's documents) shares the folder with the others
            "shared" -> false
            else -> throw BadRequest("where must be folder, worktree, shared or auto")
        }
    }

    private data class FlowWorktree(val name: String, val branch: String, val path: String)

    /** A worktree for a flow, on its own branch (the branch pattern) from the base branch, not from what the folder has;
     *  0.18.64 from [from] when the start named a base. */
    private fun worktreeFor(pid: String, root: Path, title: String, workflowId: String, from: String? = null): FlowWorktree {
        if (!workspace.git(root, "rev-parse", "--git-dir").ok)
            throw Conflict("This project is not a git repository", "A flow next to another one needs git: its own worktree and branch.")
        val first = branchName(settings.effective(pid).branchPattern, title, workflowId)
        var branch = first
        var n = 2
        while (workspace.git(root, "rev-parse", "--verify", "--quiet", "refs/heads/$branch").ok) branch = "$first-${n++}"
        val name = "flow-${Slug.of(title).take(30)}-${java.util.UUID.randomUUID().toString().take(6)}"
        // B7 from origin/<base> when the local base is behind it (0.18.65 the canonical remote's copy)
        val res = engine.post("/worktrees", mapOf("root" to root.toString(), "name" to name, "branch" to branch,
            "start" to (from ?: freshBase(root))))
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

    /** A flow the engine gave a worktree of its own (a fix of a hunt finding: its own fix/ branch from the base). */
    fun recordWorktree(child: String, name: String, branch: String) {
        jdbc.update("UPDATE threads SET worktree = ?, branch = ? WHERE id = ?", name, branch, child)
    }

    /** A flow a flow handed over to (change → feature) works where its parent worked. */
    fun inheritWorktree(child: String, parent: String) {
        jdbc.update("UPDATE threads SET worktree = (SELECT worktree FROM threads WHERE id = ?), branch = (SELECT branch FROM threads WHERE id = ?) " +
            "WHERE id = ? AND worktree IS NULL", parent, parent, child)
    }

    /** A flow whose every step is in a read-only phase (the engine's rules: review): it changes nothing in the repo. */
    internal fun readOnly(wf: keel.api.workflows.Workflow): Boolean =
        wf.steps.isNotEmpty() && wf.steps.all { it.phase in READ_ONLY_PHASES }

    /** Never work on main/master: a flow gets its own branch from the branch pattern in Settings (feat/{slug}). */
    /** The branch [ownBranch] made in the person's checkout, with where it came from (to undo it if the start fails). */
    private data class MadeBranch(val name: String, val from: String, val tip: String)

    /** Puts the checkout back on the branch it was on and drops the new branch, when nothing was committed on it. */
    private fun undoBranch(root: Path, made: MadeBranch) {
        runCatching {
            val back = workspace.git(root, "checkout", "-q", made.from)
            if (!back.ok) { log.warn("could not switch {} back to {}: {}", root, made.from, back.err.take(200)); return }
            val tipNow = workspace.git(root, "rev-parse", "--verify", "--quiet", "refs/heads/${made.name}").takeIf { it.ok }?.out?.trim()
            if (tipNow == made.tip) workspace.git(root, "branch", "-D", made.name)   // no new commits: nothing to lose
        }.onFailure { log.warn("could not undo the branch {} in {}: {}", made.name, root, it.message) }
    }

    /**
     * The flow's own branch in the project folder. On the base branch it always gets one (feat/{slug}). On another branch
     * [mode] decides: "new" (Tasks, and by default the start dialog, MCP and the TUI for a flow that builds something)
     * makes one from the base branch, so a flow never stacks its commits on what another flow (or init) left checked
     * out; "continue" (the person picked "Continue this branch", or B7 by default ship, cover, review, lint, address)
     * and null (a caller that put the branch there itself: CI fix, review address) stay. 0.18.64 [start]: the base the
     * start named (a local branch or <remote>/<branch>): always a new branch from it.
     */
    private fun ownBranch(pid: String, root: Path, title: String, workflowId: String, mode: String? = null, start: String? = null): MadeBranch? {
        if (isDemo(root)) return null
        val current = workspace.git(root, "rev-parse", "--abbrev-ref", "HEAD").takeIf { it.ok }?.out?.trim() ?: return null
        val info = workspace.baseInfo(root)
        val base = workspace.base(root) ?: info.remote
        if (base == null && start == null) return null
        val offBase = current != base && current != info.name
        if (offBase && mode != "new" && start == null) return null
        // B7 a new branch starts from the base, and never from a local base that is behind origin's (a pull not made
        // yet): on the base itself too, unless the flow continues the base's own commits ("continue"). 0.18.65 (R4) on
        // init's own keel/setup branch (not merged yet) it starts there: it has keel's config, and keel's reviews and PR
        // bodies leave the setup commit out of the flow's change
        val setup = current.takeIf { workflowId != "init" && it.startsWith(SETUP_BRANCH) && aheadOf(root, it, info.ref) }
        val from = start ?: setup ?: if (base != null && (offBase || mode == "new")) freshBase(root) else null
        val tip = workspace.git(root, "rev-parse", from?.let { "$it^{commit}" } ?: "HEAD").takeIf { it.ok }?.out?.trim() ?: return null
        val first = branchName(settings.effective(pid).branchPattern, title, workflowId)
        var name = first
        var n = 2
        while (workspace.git(root, "rev-parse", "--verify", "--quiet", "refs/heads/$name").ok) name = "$first-${n++}"
        // --no-track: a branch from origin/<base> must not push to (or count itself against) the base
        val r = if (from != null) workspace.git(root, "checkout", "-q", "--no-track", "-b", name, from)
            else workspace.git(root, "checkout", "-q", "-b", name)
        if (!r.ok) throw Conflict("Could not create the branch $name" + (from?.let { " from $it" } ?: ""), r.err.ifBlank { r.out }.take(300))
        return MadeBranch(name, current, tip)
    }

    /**
     * B7 where a new branch starts: the local base, or its remote copy when the local one is only behind it (its
     * commits are all in the remote's: a pull not made yet). A local base that went its own way (commits on both sides)
     * is refused: keel does not guess which one has the work. Nothing is fetched: the remote is as the last fetch left it.
     * 0.18.65 the remote copy is the canonical remote's (Workspace.baseInfo: git.remote, the base's upstream, ...), not
     * always origin's; with no local base branch (base_branch: github/main) the remote copy itself. Null: no base.
     */
    private fun freshBase(root: Path): String? {
        val info = workspace.baseInfo(root)
        // no local copy of the base branch (base_branch: github/main): its remote copy, never another local branch
        if (info.local == null && info.remote != null && (info.explicit || info.remote.substringAfter('/') == info.name)) return info.remote
        val base = info.local ?: workspace.base(root)
        val remoteName = info.remote ?: base?.let { b -> "origin/$b".takeIf { workspace.git(root, "rev-parse", "--verify", "--quiet", "refs/remotes/$it").ok } }
        if (base == null || remoteName == null) return base ?: remoteName
        val local = "refs/heads/$base"
        val remote = "refs/remotes/$remoteName"
        if (!workspace.git(root, "rev-parse", "--verify", "--quiet", remote).ok) return base
        val behind = workspace.gitOut(root, "rev-list", "--count", "$local..$remote")?.toIntOrNull() ?: return base
        if (behind == 0) return base
        val ahead = workspace.gitOut(root, "rev-list", "--count", "$remote..$local")?.toIntOrNull() ?: 0
        if (ahead > 0) throw Conflict("The local $base and $remoteName went their own ways ($ahead and $behind commits)",
            "Pull (or rebase) $base first, so the flow's new branch starts from the latest code; start the flow with base " +
                "$remoteName; or continue on the branch the folder is on.")
        return remoteName
    }

    /** [branch] has commits [base] has not (init's keel/setup branch, not merged yet). */
    private fun aheadOf(root: Path, branch: String, base: String?): Boolean =
        base != null && (workspace.gitOut(root, "rev-list", "--count", "$base..refs/heads/$branch")?.toIntOrNull() ?: 0) > 0

    /** 0.18.65 the project's base and its canonical remote (engine GET /base): Settings › General › Base branch. */
    fun baseView(pid: String): JsonNode = engine.get("/base?root={root}", mapOf("root" to projects.root(pid).toString()))

    /** 0.18.65 the person's answer about the base (engine POST /base): remote, track, pick, or canonical. keel writes
     *  .keel/config.yml (and git branch --track for track); an agent never does. */
    fun chooseBase(pid: String, body: BaseChoice): JsonNode {
        if (body.choice !in setOf("remote", "track", "pick", "canonical"))
            throw BadRequest("choice cannot be \"${body.choice}\"", "Pick one of: remote, track, pick, canonical")
        val root = projects.root(pid)
        val res = engine.post("/base", mapOf("root" to root.toString(), "choice" to body.choice, "branch" to body.branch, "remote" to body.remote))
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        return res
    }

    /**
     * B8: init that ends without a commit on the branch it got leaves the project folder where it found it: back on the
     * base branch, and the empty branch dropped (uncommitted files stay: both branches point at the same commit). Only
     * when the folder is still on that branch and it has no commit the base does not have.
     */
    fun releaseEmptyBranch(tid: String) {
        val row = jdbc.query("SELECT project_id, workflow_id, worktree FROM threads WHERE id = ?",
            { rs, _ -> Triple(rs.getString(1), rs.getString(2), rs.getString(3)) }, tid).firstOrNull() ?: return
        val (pid, workflow, wt) = row
        if (workflow != "init" || !wt.isNullOrBlank()) return
        runCatching {
            val root = projects.root(pid)
            if (isDemo(root)) return
            val made = docs.flow(tid).items.firstOrNull { it.kind == "branch" }?.ref ?: return
            val base = workspace.base(root) ?: return
            if (made == base) return
            val current = workspace.gitOut(root, "rev-parse", "--abbrev-ref", "HEAD") ?: return
            if (current != made) return
            val ahead = workspace.gitOut(root, "rev-list", "--count", "$base..$made")?.toIntOrNull() ?: return
            if (ahead != 0) return
            val back = workspace.git(root, "checkout", "-q", base)
            if (!back.ok) { log.warn("could not switch {} back to {}: {}", root, base, back.err.take(200)); return }
            workspace.git(root, "branch", "-d", made)
            log.info("init {} made no commit: {} is back on {} and {} is gone", tid, root, base, made)
        }.onFailure { log.warn("could not release the empty branch of {}: {}", tid, it.message) }
    }

    /** Stores the latest ThreadState we saw, so the UI has something when the engine is down. */
    fun save(tid: String, state: JsonNode) {
        if (!state.isObject || state.get("status") == null) return
        // v0.15.4 a finished flow whose status did not change (opening an earlier run) keeps its updated_at: looking at a
        // flow does not make it the project's newest one
        val status = state.get("status")?.asText()
        jdbc.update(
            "UPDATE threads SET status = ?, current = ?, phase = ?, ac = ?, state_json = ?, error = ?, " +
                "updated_at = CASE WHEN status IS ? AND status IN ('done','failed','stopped') THEN updated_at ELSE ? END WHERE id = ?",
            status, state.get("current")?.takeIf { !it.isNull }?.asText(), state.get("phase")?.asText(),
            state.get("ac")?.takeIf { !it.isNull }?.asText(), mapper.writeValueAsString(state),
            state.get("error")?.takeIf { !it.isNull }?.asText(), status, Time.now(), tid,
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
        return keysFor(models) + contributedKeys(pid) + mcpEnvKeys(s.mcp)
    }

    /**
     * 0.18.64 the env values of the project's MCP servers, as "mcp_env:<server>:<NAME>": the engine keeps them in memory
     * only (its stored flow has the names, never the values), so after an engine restart they come again with the logins.
     */
    private fun mcpEnvKeys(names: List<String>): Map<String, String> =
        mcp.specsFor(names).flatMap { spec -> spec.env.orEmpty().map { (k, v) -> "mcp_env:${spec.name}:$k" to v } }.toMap()

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

    fun threadModels(tid: String): JsonNode = engine.threadModels(tid)

    /**
     * 0.18.64 a model for one flow (an agent in it, one step, or an agent's steps in one phase) without changing the
     * project's Agents page or any other flow. The model is checked as the Agents page checks one (its effort, local
     * models only), and its login or API key goes to the engine with it (memory only, as at the start).
     */
    fun setThreadModels(tid: String, body: ThreadModelBody): JsonNode {
        val pid = threadProject(tid) ?: throw NotFound("No flow $tid")
        if (body.agent.isNullOrBlank() && body.step.isNullOrBlank()) throw BadRequest("Say which agent or which step", "Send { agent } or { step }, and model.")
        val model = body.model?.checked()
        if (model != null) LocalOnly.check(settings.effective(pid), listOf(model), "this flow's ${body.step ?: body.agent}")
        val keys = model?.let { keysForThread(tid) + keysFor(listOf(it)) }?.takeIf { it.isNotEmpty() }
        val state = engine.setThreadModels(tid, mapOf("agent" to body.agent, "step" to body.step, "phase" to body.phase,
            "model" to model, "keys" to keys).filterValues { it != null })
        threadProject(tid)?.let { hub.publish(it, "project.changed", mapOf("id" to it)) }
        return state
    }

    fun stop(tid: String): JsonNode {
        val state = engine.stop(tid)
        save(tid, state)
        jdbc.update("UPDATE agent_calls SET status = 'stopped', ended_at = COALESCE(ended_at, ?) WHERE thread_id = ? AND status = 'running'", Time.now(), tid)
        releaseEmptyBranch(tid)
        threadProject(tid)?.let { hub.publish(it, "project.changed", mapOf("id" to it)) }
        return state
    }

    fun history(tid: String): JsonNode = engine.history(tid)

    /** 0.18.64 the flow's document (its clarify document, else its spec) from its file: path, kind, text, whether the
     *  person edited it after keel wrote it (and when), and the gate the flow waits at. */
    fun document(tid: String): JsonNode = engine.document(tid)

    /**
     * 0.18.64 replaces the flow's document while it waits at a gate (keel_flow_document). The engine writes the file and
     * records it in keel's file history as the person's edit; the gate's text shows it from then on, and a clarify
     * hand-over uses it. The saved state follows, so the run history and keel TUI show the new gate text.
     */
    /** 0.18.64 the AC gate's code for the web: the criterion's RED and GREEN commits, each file with its status, +/-
     *  counts, bucket (test, prod, generated, lock) and diff, cut; lock, generated and binary files folded. With [sha] and
     *  [path] one file with its diff (the gate's Show). Only a flow of this project. */
    fun acCode(pid: String, tid: String, ac: String?, sha: String?, path: String?): JsonNode {
        projects.require(pid)
        if (threadProject(tid) != pid) throw NotFound("No flow $tid in this project")
        if (ac.isNullOrBlank()) throw BadRequest("ac is missing", "Say which criterion: ac=AC-1.")
        return engine.acCode(tid, ac.trim(), sha?.trim()?.takeIf { it.isNotEmpty() }, path?.takeIf { it.isNotBlank() })
    }

    fun writeDocument(tid: String, text: String?): JsonNode {
        if (text.isNullOrBlank()) throw BadRequest("text is empty", "Send the document's whole new text.")
        val doc = engine.writeDocument(tid, mapOf("text" to text, "root" to rootNow(tid)).filterValues { it != null })
        runCatching { save(tid, engine.thread(tid)) }
        threadProject(tid)?.let { hub.publish(it, "project.changed", mapOf("id" to it)) }
        return doc
    }

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
            "SELECT id, workflow_id, state_json FROM threads WHERE project_id = ? AND worktree IS NULL AND $SHOWN ORDER BY CASE WHEN status IN ('running','waiting') THEN 0 ELSE 1 END, updated_at DESC LIMIT 1",
            { rs, _ -> Triple(rs.getString(1), rs.getString(2), rs.getString(3)) }, pid,
        ).firstOrNull()
        return view(row)
    }

    /** v0.15.4 The flow the Flow page shows as the project's own (flow()), and the project's newest flow by start. */
    private fun currentAndLatest(pid: String): Pair<String?, String?> {
        val current = jdbc.query("SELECT id FROM threads WHERE project_id = ? AND worktree IS NULL AND $SHOWN " +
            "ORDER BY CASE WHEN status IN ('running','waiting') THEN 0 ELSE 1 END, updated_at DESC LIMIT 1", { rs, _ -> rs.getString(1) }, pid).firstOrNull()
        val latest = jdbc.query("SELECT id FROM threads WHERE project_id = ? AND $SHOWN ORDER BY created_at DESC, rowid DESC LIMIT 1",
            { rs, _ -> rs.getString(1) }, pid).firstOrNull()
        return current to latest
    }

    /**
     * v0.15.4 Deletes a flow from the project's run history. It is only a mark (hidden_at): the engine's checkpoints, the
     * agent calls (budgets count them) and the events stay, and a flow that runs again shows up again. Refused while the
     * flow runs or waits, and for a stopped or failed flow that is the project's current or newest one (it can still be
     * resumed).
     */
    fun hide(pid: String, tid: String): JsonNode {
        projects.require(pid)
        val known = jdbc.query("SELECT status FROM threads WHERE project_id = ? AND id = ? AND hidden_at IS NULL", { rs, _ -> rs.getString(1) }, pid, tid)
            .firstOrNull() ?: throw NotFound("No flow $tid in this project's history")
        // the engine knows best whether it still runs; the stored status when it is down
        val status = runCatching { engine.thread(tid).also { save(tid, it) }.path("status").asText(known) }.getOrDefault(known)
        if (status == "running") throw Conflict("This flow still runs", "Stop it first, or let it finish. Then you can delete it from the history.")
        if (status == "waiting") throw Conflict("This flow waits for you", "Answer it or stop it first. Then you can delete it from the history.")
        val (current, latest) = currentAndLatest(pid)
        if (status in setOf("stopped", "failed") && (tid == current || tid == latest)) {
            throw Conflict("This flow can still be resumed", "Only the last stopped flow can be resumed, so it stays in the history. Resume it, or start a new flow first.")
        }
        jdbc.update("UPDATE threads SET hidden_at = ? WHERE id = ?", Time.now(), tid)
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        return mapper.valueToTree(mapOf("ok" to true, "thread_id" to tid))
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

    /** v0.9.0: the project's flows, newest first (one workflow's with [workflow]); tokens as the budget counts them. */
    fun runs(pid: String, workflow: String? = null, limit: Int = 20): List<RunRow> {
        projects.require(pid)
        val wf = workflow?.takeIf { it.isNotBlank() }
        val sql = "SELECT t.id, t.title, t.workflow_id, t.status, t.phase, t.current, t.state_json, t.worktree, t.branch, t.error, " +
            "t.created_at, t.updated_at, (SELECT COALESCE(SUM(c.tokens_in + c.tokens_out + c.tokens_cached / 10), 0) " +
            "FROM agent_calls c WHERE c.thread_id = t.id), (SELECT COALESCE(SUM(c.cost_usd), 0) FROM agent_calls c WHERE c.thread_id = t.id), " +
            "t.source, t.replaced_by " +
            "FROM threads t WHERE t.project_id = ? AND ${SHOWN.replace("status", "t.status").replace("hidden_at", "t.hidden_at")}" +
            (if (wf != null) " AND t.workflow_id = ?" else "") + " ORDER BY t.updated_at DESC LIMIT ?"
        val args = listOfNotNull(pid, wf, limit.coerceIn(1, 100)).toTypedArray()
        val latest = currentAndLatest(pid).second
        return jdbc.query(sql, { rs, _ ->
            val state = rs.getString(7)?.let { runCatching { mapper.readTree(it) }.getOrNull() }
            val acs = state?.path("acs")?.takeIf { it.isArray }?.toList().orEmpty()
            val waiting = state?.path("waiting")?.path("title")?.asText("")?.ifBlank { null }
            RunRow(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5), rs.getString(6),
                waiting.takeIf { rs.getString(4) == "waiting" },
                acs.count { it.path("status").asText() in setOf("done", "already-met", "accepted") }, acs.size, rs.getLong(13),
                if (rs.getString(8) != null) "worktree" else "folder", rs.getString(9), rs.getString(10)?.take(300),
                rs.getString(11), rs.getString(12),
                state?.path("usage")?.path("cost_usd")?.takeIf { it.isNumber && it.asDouble() > 0 }?.asDouble() ?: rs.getDouble(14),
                rs.getString(1) == latest, rs.getString(15), rs.getString(16))
        }, *args)
    }

    // ---- v0.7.x: the board of the project's flows ----------------------------------------------

    private data class BoardRow(val id: String, val title: String, val workflowId: String?, val status: String, val phase: String?,
                                val current: String?, val worktree: String?, val branch: String?, val state: String?, val updatedAt: String?)

    /** The flows that run or wait, in the project folder or in worktrees, and the finished ones whose worktree is left. */
    fun board(pid: String): FlowBoard {
        val root = projects.root(pid)
        val base = workspace.baseInfo(root).ref ?: workspace.base(root)     // 0.18.65 the canonical remote's copy
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
            val branch = r.branch ?: workspace.git(dir, "rev-parse", "--abbrev-ref", "HEAD").takeIf { it.ok }?.out?.trim()?.ifBlank { null }
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
        val committed = base?.let { b -> workspace.git(dir, "diff", "--name-only", "$b...HEAD").takeIf { it.ok }?.out?.lines() }.orEmpty()
        val open = workspace.git(dir, "status", "--porcelain", "--untracked-files=all").out.lines().filter { it.length > 3 }.map { it.substring(3).trim() }
        return (committed + open).map { it.trim() }
            .filter { it.isNotBlank() && !WorkspaceDoctor.isEngineFile(it) && !it.startsWith(".keel/") }.distinct().sorted()
    }

    /** The files where two branches do not merge cleanly (git merge-tree, git 2.38 or newer); null when git cannot tell. */
    private fun mergeConflicts(root: Path, a: String, b: String): List<String>? {
        val r = workspace.git(root, "merge-tree", "--write-tree", "--name-only", "--no-messages", a, b)
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
        // 0.18.57 the project folder: building the project notes (knowledge-refresh) counts what the librarians read
        val root = runCatching { projects.root(pid).toString() }.getOrNull()
        return engine.estimate(mapOf("yaml" to yaml, "acs" to acs.coerceIn(0, 50), "history" to history, "models" to models,
            "knowledge_tokens" to knowledge, "root" to root))
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

/** v0.15.4 A flow that is not deleted from the history (a flow that runs or waits always shows). */
private const val SHOWN = "(hidden_at IS NULL OR status IN ('running','waiting'))"

/**
 * B7 flows that finish the work on the branch the folder is on (ship it, cover it, review it, lint it, address a comment
 * on it): started with no branch choice they continue that branch. A new branch from the base would leave the work
 * behind (a ship of an empty diff). Every other flow gets a new branch from the base by default (B8).
 */
val CONTINUE_FLOWS = setOf("ship", "cover", "review", "lint", "address")

/** 0.18.65 (R4) the branch init commits keel's setup on (the engine's init_actions.SETUP_BRANCH). */
const val SETUP_BRANCH = "keel/setup"

/** 0.18.65 PUT /api/projects/{pid}/base: the person's answer about the base (choice remote, track, pick, canonical). */
data class BaseChoice(val choice: String = "", val branch: String? = null, val remote: String? = null)

/** POST /flows with no branch mode (the dashboard, MCP keel_start_flow, the TUI): [defaultBranch] picks it. */
const val DEFAULT_BRANCH = "default"

/** The branch mode of a start that named none ([DEFAULT_BRANCH]): continue for [CONTINUE_FLOWS] and a flow based on
 *  one of them, else new. */
fun defaultBranch(workflowId: String, basedOn: String?): String =
    if (workflowId in CONTINUE_FLOWS || basedOn?.removePrefix("keel/") in CONTINUE_FLOWS) "continue" else "new"

data class UnlockResult(val unlocks: List<Any?>, val via: String, val threadId: String?)

/**
 * A flow's branch from the branch pattern in Settings (blank: feat/{slug}): {slug} is the title, {flow} the workflow id
 * (it was always the word "flow"), and a fix goes on fix/ where the pattern says feat/ (the engine's bug.branch_name).
 */
fun branchName(pattern: String?, title: String, workflowId: String): String {
    // 0.18.65 (R4) init commits keel's setup on a branch of its own, for a setup PR: never a feature's branch
    if (workflowId == "init") return SETUP_BRANCH
    var p = pattern?.trim().orEmpty().ifBlank { "feat/{slug}" }
    if (workflowId == "fix" && p.startsWith("feat/")) p = "fix/" + p.removePrefix("feat/")
    return p.replace("{slug}", Slug.of(title).take(40)).replace("{user}", "keel").replace("{flow}", workflowId.ifBlank { "flow" })
}


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
