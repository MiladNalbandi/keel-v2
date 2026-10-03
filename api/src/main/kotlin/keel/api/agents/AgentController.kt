package keel.api.agents

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.BadRequest
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

data class AgentTest(val pid: String? = null)

@RestController
@RequestMapping("/api")
class AgentController(private val agents: AgentService) {

    @GetMapping("/projects/{pid}/agents")
    fun list(@PathVariable pid: String): List<Agent> = agents.list(pid)

    @PutMapping("/projects/{pid}/agents/{aid}")
    fun override(@PathVariable pid: String, @PathVariable aid: String, @RequestBody patch: Map<String, Any?>): Agent =
        agents.override(pid, aid, patch)

    @PostMapping("/projects/{pid}/agents")
    fun create(@PathVariable pid: String, @RequestBody body: CustomAgent): Agent = agents.createCustom(pid, body)

    @DeleteMapping("/projects/{pid}/agents/{aid}")
    fun delete(@PathVariable pid: String, @PathVariable aid: String): Map<String, Boolean> {
        agents.deleteCustom(pid, aid)
        return mapOf("ok" to true)
    }

    @PostMapping("/agents/{aid}/test")
    fun test(@PathVariable aid: String, @RequestBody body: AgentTest): JsonNode =
        agents.test(aid, body.pid ?: throw BadRequest("pid is missing", "Send { pid } so keel knows which project's model to use."))
}
