package keel.api.flow

import com.fasterxml.jackson.databind.JsonNode
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
)
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
        flows.start(pid, body.workflowId, body.title, body.acs, FlowCap(body.capTokens, body.onCap, body.runMode), body.allowFake, body.allowDirty, body.request, body.options)

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
