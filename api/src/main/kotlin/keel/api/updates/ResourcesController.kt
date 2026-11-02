package keel.api.updates

import com.fasterxml.jackson.databind.JsonNode
import keel.api.engine.EngineClient
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController

/**
 * keel 0.20.0 Settings › Access › Resources: what keel uses and may use, and how many agents run at once (the engine's
 * runtime/resources.py keeps it). The change needs the MCP admin level. The container's memory and CPU limits are Docker's:
 * keel2 limits sets them from the host, because a container cannot change its own.
 */
@RestController
class ResourcesController(private val engine: EngineClient) {

    @GetMapping("/api/resources")
    fun view(): JsonNode = engine.get("/resources")

    @PutMapping("/api/resources")
    fun save(@RequestBody body: Map<String, Any?>): JsonNode = engine.put("/resources", body)
}
