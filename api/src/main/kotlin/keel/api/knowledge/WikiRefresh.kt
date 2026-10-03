package keel.api.knowledge

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.BadRequest
import keel.api.flow.Ac
import keel.api.flow.FlowService
import keel.api.projects.ProjectService
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController

data class WikiRefreshBody(val sections: List<String>? = null)

/**
 * Starts the engine's `knowledge-refresh` template for stale knowledge sections, like any flow
 * start. Each section goes to the engine as one AC (`{id: section, layer: "API", title: section}`):
 * the template runs one librarian per AC.
 */
@Service
class WikiRefreshService(
    private val knowledge: KnowledgeService,
    private val flows: FlowService,
    private val projects: ProjectService,
) {
    fun refresh(pid: String, wanted: List<String>?): JsonNode {
        val root = projects.root(pid)
        val all = knowledge.knowledge(root)
        val sections = if (wanted.isNullOrEmpty()) {
            all.filter { it.status == "stale" }.map { it.id }
        } else {
            val known = all.map { it.id }.toSet()
            val unknown = wanted.filter { it !in known }
            if (unknown.isNotEmpty()) throw BadRequest("Unknown knowledge section: ${unknown.joinToString()}", "Sections: ${known.joinToString()}")
            wanted.distinct()
        }
        if (sections.isEmpty()) throw BadRequest("No knowledge section is stale", "Pick the sections to refresh with { sections: [...] }.")
        val acs = sections.map { Ac(id = it, layer = "API", title = it) }
        return flows.start(pid, TEMPLATE, "Refresh knowledge: ${sections.joinToString()}", acs)
    }

    companion object {
        const val TEMPLATE = "knowledge-refresh"
    }
}

@RestController
class WikiRefreshController(private val refresh: WikiRefreshService) {
    @PostMapping("/api/projects/{pid}/wiki/refresh")
    fun refresh(@PathVariable pid: String, @RequestBody(required = false) body: WikiRefreshBody?): JsonNode =
        refresh.refresh(pid, body?.sections)
}
