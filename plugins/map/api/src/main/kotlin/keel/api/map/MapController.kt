package keel.api.map

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.projects.ProjectService
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/**
 * The Map plugin's api part (plugins/map): the map the engine built for a project (keel_plugin_map, stored in the
 * engine DB). It keeps keel's package (keel.api.map), so keel's component scan finds it when its jar is on
 * loader.path (docs/plugins/11-step3-contract.md). The same answers as keel 0.15.1's KnowledgeController.
 */
@RestController
@RequestMapping("/api/projects/{pid}/map")
class MapController(
    private val projects: ProjectService,
    private val engine: EngineClient,
    private val mapper: ObjectMapper,
) {

    /** The map the engine built, or `{missing}` until it built one. */
    @GetMapping
    fun map(@PathVariable pid: String): JsonNode {
        projects.require(pid)
        return try {
            engine.get("/projects/$pid/map")
        } catch (e: EngineDown) {
            mapper.createObjectNode().put("missing", "The engine is not running, so the map cannot be read.")
        }
    }

    /** Builds the map for HEAD in the engine: folders, tables from the SQL migrations, endpoints from the API contract. */
    @PostMapping("/rebuild")
    fun rebuild(@PathVariable pid: String): JsonNode {
        val root = projects.root(pid)
        return engine.post("/projects/$pid/map", mapOf("root" to root.toString()), long = true)
    }
}
