package keel.api.updates

import com.fasterxml.jackson.databind.JsonNode
import keel.api.engine.EngineClient
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController

/**
 * keel 0.20.0 Settings › Resources: what keel uses and may use, and how many agents run at once (the engine's
 * runtime/resources.py keeps it). The change needs the MCP admin level. The container's memory and CPU limits are Docker's:
 * a container cannot change its own, so what is saved here waits for keel2 (its next start, or keel2 limits).
 */
@RestController
class ResourcesController(private val engine: EngineClient) {

    @GetMapping("/api/resources")
    fun view(): JsonNode = engine.get("/resources")

    @PutMapping("/api/resources")
    fun save(@RequestBody body: Map<String, Any?>): JsonNode = engine.put("/resources", body)
}
