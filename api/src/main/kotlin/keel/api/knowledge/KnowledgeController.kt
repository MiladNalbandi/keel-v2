package keel.api.knowledge

import com.fasterxml.jackson.databind.JsonNode
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

data class NewFact(val title: String = "", val text: String = "", val kind: String = "fact", val source: String? = null)
data class FactPatch(val title: String? = null, val text: String? = null, val kind: String? = null)

@RestController
@RequestMapping("/api/projects/{pid}")
class KnowledgeController(private val knowledge: KnowledgeService) {

    @GetMapping("/keel-docs")
    fun keelDocs(@PathVariable pid: String): List<KeelDoc> = knowledge.keelDocs(pid)

    @GetMapping("/memory")
    fun memory(@PathVariable pid: String): Memory = knowledge.memory(pid)

    @PostMapping("/memory")
    fun addFact(@PathVariable pid: String, @RequestBody body: NewFact): Fact =
        knowledge.addFact(pid, body.title, body.text, body.kind, body.source)

    @PutMapping("/memory/{fid}")
    fun updateFact(@PathVariable pid: String, @PathVariable fid: String, @RequestBody body: FactPatch): Fact =
        knowledge.updateFact(pid, fid, body.title, body.text, body.kind)

    @DeleteMapping("/memory/{fid}")
    fun deleteFact(@PathVariable pid: String, @PathVariable fid: String): Map<String, Boolean> {
        knowledge.deleteFact(pid, fid)
        return mapOf("ok" to true)
    }

    @GetMapping("/graph")
    fun graph(@PathVariable pid: String): JsonNode = knowledge.graph(pid)

    @GetMapping("/graph/search")
    fun graphSearch(@PathVariable pid: String, @RequestParam(defaultValue = "") q: String): JsonNode = knowledge.graphSearch(pid, q)

    @GetMapping("/graph/node")
    fun graphNode(@PathVariable pid: String, @RequestParam(defaultValue = "") id: String, @RequestParam(defaultValue = "1") depth: Int): JsonNode =
        knowledge.graphNode(pid, id, depth)

    @GetMapping("/wiki")
    fun wiki(@PathVariable pid: String): Wiki = knowledge.wiki(pid)

    @GetMapping("/wiki/page")
    fun page(@PathVariable pid: String, @RequestParam id: String): WikiPage = knowledge.page(pid, id)
}
