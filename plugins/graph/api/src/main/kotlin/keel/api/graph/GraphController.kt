package keel.api.graph

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.projects.ProjectService
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

/**
 * The Graph plugin's api part (plugins/graph): the project's code graph index (its status and a rebuild) and the code
 * graph for people (the Graph page), read by the engine from the project's CodeGraph index (keel_plugin_graph). It
 * keeps keel's package (keel.api.graph), so keel's component scan finds it when its jar is on loader.path
 * (docs/plugins/11-step3-contract.md). The same urls and answers as keel 0.15.1's ProjectController (index) and
 * KnowledgeController (graph).
 */
@RestController
@RequestMapping("/api/projects/{pid}")
class GraphController(
    private val projects: ProjectService,
    private val engine: EngineClient,
    private val mapper: ObjectMapper,
) {

    /** The code graph index: idle | indexing | ready | failed, with file and symbol counts (engine runtime/scan.py). */
    @GetMapping("/index")
    fun index(@PathVariable pid: String): JsonNode {
        projects.require(pid)
        return engine.get("/projects/$pid/index")
    }

    /** Scans the project again with a full re-index; answers at once with status "indexing". */
    @PostMapping("/index/rebuild")
    fun rebuildIndex(@PathVariable pid: String): JsonNode {
        val row = projects.require(pid)
        return engine.scan(row.id, row.root, rebuild = true)
    }

    /** The code graph for people: groups (packages or folders), units and the uses between them. */
    @GetMapping("/graph")
    fun graph(@PathVariable pid: String): JsonNode = graphCall(pid) { engine.get("/projects/$pid/graph") }

    /** Symbols by name. The text goes to the engine as JSON, not in a url. */
    @GetMapping("/graph/search")
    fun graphSearch(@PathVariable pid: String, @RequestParam(defaultValue = "") q: String): JsonNode =
        graphCall(pid) { engine.post("/projects/$pid/graph/search", mapOf("q" to q.take(200))) }

    /** One symbol: who uses it, what it uses, its members and how much depends on it (one or two steps). */
    @GetMapping("/graph/node")
    fun graphNode(@PathVariable pid: String, @RequestParam(defaultValue = "") id: String, @RequestParam(defaultValue = "1") depth: Int): JsonNode {
        if (id.isBlank()) throw BadRequest("id is missing", "Pick a symbol from the search or the graph.")
        return graphCall(pid) { engine.post("/projects/$pid/graph/node", mapOf("id" to id, "depth" to depth.coerceIn(1, 2))) }
    }

    private fun graphCall(pid: String, call: () -> JsonNode): JsonNode {
        projects.require(pid)
        return try {
            call()
        } catch (e: EngineDown) {
            mapper.createObjectNode().put("available", false).put("status", "engine")
                .put("reason", "The engine is not running, so the code graph cannot be read.")
        }
    }
}
