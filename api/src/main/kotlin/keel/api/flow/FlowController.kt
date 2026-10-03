package keel.api.flow

import com.fasterxml.jackson.databind.JsonNode
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

data class StartFlow(val workflowId: String = "", val title: String = "", val acs: List<Ac>? = null)
data class Resume(val decision: String = "", val why: String? = null, val payload: Map<String, Any?>? = null)
data class Rewind(val checkpointId: String = "")

@RestController
@RequestMapping("/api")
class FlowController(private val flows: FlowService) {

    @GetMapping("/projects/{pid}/flow")
    fun flow(@PathVariable pid: String): FlowView = flows.flow(pid)

    @PostMapping("/projects/{pid}/flows")
    fun start(@PathVariable pid: String, @RequestBody body: StartFlow): JsonNode =
        flows.start(pid, body.workflowId, body.title, body.acs)

    @PostMapping("/threads/{tid}/resume")
    fun resume(@PathVariable tid: String, @RequestBody body: Resume): JsonNode = flows.resume(tid, body.decision, body.why, body.payload)

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
}
