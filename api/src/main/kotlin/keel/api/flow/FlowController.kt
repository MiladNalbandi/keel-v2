package keel.api.flow

import com.fasterxml.jackson.databind.JsonNode
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

data class StartFlow(
    val workflowId: String = "",
    val title: String = "",
    val acs: List<Ac>? = null,
    val capTokens: Int? = null,
    val onCap: String? = null,
    /** Run on a real project even though some agents use the fake model (it writes example files). */
    val allowFake: Boolean = false,
    /** Start although the working tree has uncommitted changes (they stay out of keel's commits). */
    val allowDirty: Boolean = false,
    /** What to build, in the user's words; every agent gets it. */
    val request: String? = null,
    /** Flow inputs the workflow reads from its state data: review {lens, base}, fix {no_gates}, ... */
    val options: Map<String, Any?>? = null,
    /** v0.4.1: manual | important | auto | readonly for this flow (default: the project's run_mode setting). */
    val runMode: String? = null,
    /** v0.7.x: folder | worktree | auto (default: the project folder when it is free, else a worktree of its own). */
    val where: String? = null,
    /** B8: new (its own branch from the base branch, also when the folder is on another branch) | continue (stay on the
     *  branch the project folder is on; on the base branch it still gets its own). B7 none: continue for the flows that
     *  finish the folder's branch (ship, cover, review, lint, address), new for every other one. */
    val branch: String? = null,
    /** 0.18.57 start although another flow or side agent works on the same ticket, branch or files. */
    val force: Boolean = false,
    /** 0.18.64 the branch the flow's new branch starts from: a local branch or <remote>/<branch> (default: the base). */
    val base: String? = null,
)
/** 0.18.64 keel brings a flow's branch up to a base: merge (default) or rebase; start (default), continue or abort. */
data class SyncBody(val onto: String? = null, val mode: String? = null, val action: String? = null)
data class EstimateYaml(val yaml: String = "", val acs: JsonNode? = null)
data class UnlockBody(val path: String = "", val phase: String? = null, val reason: String? = null)
data class Resume(val decision: String = "", val why: String? = null, val payload: Map<String, Any?>? = null)
data class Rewind(val checkpointId: String = "")
data class ModeBody(val mode: String = "")

@RestController
@RequestMapping("/api")
class FlowController(private val flows: FlowService) {

    @GetMapping("/projects/{pid}/flow")
    fun flow(@PathVariable pid: String): FlowView = flows.flow(pid)

    @PostMapping("/projects/{pid}/flows")
    fun start(@PathVariable pid: String, @RequestBody body: StartFlow): JsonNode =
        flows.start(pid, body.workflowId, body.title, body.acs, FlowCap(body.capTokens, body.onCap, body.runMode), body.allowFake, body.allowDirty,
            body.request, body.options, body.where, branch = when (body.branch) { "continue", "new" -> body.branch; else -> DEFAULT_BRANCH },
            force = body.force, base = body.base)

    /** 0.18.64 every remote of the project fetched (20 s each at most): one result per remote, how far the base is behind. */
    @PostMapping("/projects/{pid}/remotes/fetch")
    fun fetchRemotes(@PathVariable pid: String): JsonNode = flows.fetchRemotes(pid)

    /** 0.18.64 keel merges or rebases the flow's branch onto a base; a conflict answers ok false with the files. */
    @PostMapping("/threads/{tid}/sync")
    fun sync(@PathVariable tid: String, @RequestBody(required = false) body: SyncBody?): JsonNode =
        flows.syncBranch(tid, body?.onto, body?.mode, body?.action)

    /** 0.18.57 what Start a flow shows first: whether a worktree flow can run the tests, and the base branch's drift. */
    @GetMapping("/projects/{pid}/start-check")
    fun startCheck(@PathVariable pid: String): JsonNode =
        flows.startCheck(pid) ?: com.fasterxml.jackson.databind.node.JsonNodeFactory.instance.objectNode()

    /** v0.7.x: the project's flows side by side (folder and worktrees), the files they share, conflicts, a merge order. */
    @GetMapping("/projects/{pid}/flows")
    fun board(@PathVariable pid: String): FlowBoard = flows.board(pid)

    /** v0.9.0: the project's flows, newest first, or one workflow's (its run history). */
    @GetMapping("/projects/{pid}/runs")
    fun runs(@PathVariable pid: String, @RequestParam(required = false) workflow: String?,
             @RequestParam(required = false, defaultValue = "20") limit: Int): List<RunRow> = flows.runs(pid, workflow, limit)

    @GetMapping("/projects/{pid}/flows/{tid}")
    fun flowOf(@PathVariable pid: String, @PathVariable tid: String): FlowView = flows.flowOf(pid, tid)

    /** v0.15.4 Deletes a flow from the project's run history (a mark: its checkpoints, calls and events stay). 409 while it
     *  runs or waits, and for the last flow that can still be resumed. */
    @DeleteMapping("/projects/{pid}/flows/{tid}")
    fun hide(@PathVariable pid: String, @PathVariable tid: String): JsonNode = flows.hide(pid, tid)

    /** A finished flow's worktree goes; its branch stays. */
    @PostMapping("/threads/{tid}/worktree/remove")
    fun removeWorktree(@PathVariable tid: String): JsonNode = flows.removeWorktree(tid)

    @PostMapping("/threads/{tid}/resume")
    fun resume(@PathVariable tid: String, @RequestBody body: Resume): JsonNode = flows.resume(tid, body.decision, body.why, body.payload)

    /** v0.4.1: change the run mode during a run (from the next gate on). */
    @PostMapping("/threads/{tid}/mode")
    fun mode(@PathVariable tid: String, @RequestBody body: ModeBody): JsonNode = flows.setMode(tid, body.mode)

    @PostMapping("/threads/{tid}/stop")
    fun stop(@PathVariable tid: String): JsonNode = flows.stop(tid)

    @GetMapping("/threads/{tid}/history")
    fun history(@PathVariable tid: String): JsonNode = flows.history(tid)

    @PostMapping("/threads/{tid}/rewind")
    fun rewind(@PathVariable tid: String, @RequestBody body: Rewind): JsonNode = flows.rewind(tid, body.checkpointId)

    /** 0.18.57 who else reads the project folder (KeelBot's Ask and Edit chats, the folder's flow): Start a flow warns. */
    @GetMapping("/projects/{pid}/folder-activity")
    fun folderActivity(@PathVariable pid: String): Map<String, Any?> = flows.folderActivity(pid)

    @GetMapping("/projects/{pid}/estimate")
    fun estimate(
        @PathVariable pid: String,
        @RequestParam("workflow_id") workflowId: String,
        @RequestParam(defaultValue = "3") acs: Int,
    ): JsonNode = flows.estimate(pid, workflowId, acs)

    /** Estimate unsaved workflow YAML. `acs` is a count, or a list of ACs (its size is used). */
    @PostMapping("/projects/{pid}/estimate")
    fun estimateYaml(@PathVariable pid: String, @RequestBody body: EstimateYaml): JsonNode {
        val n = when {
            body.acs == null || body.acs.isNull -> 3
            body.acs.isArray -> body.acs.size()
            body.acs.isNumber || body.acs.isTextual -> body.acs.asInt(3)
            else -> 3
        }
        return flows.estimateYaml(pid, body.yaml, n)
    }

    @PostMapping("/projects/{pid}/unlock")
    fun unlock(@PathVariable pid: String, @RequestBody body: UnlockBody): UnlockResult =
        flows.unlock(pid, body.path, body.phase, body.reason)
}
