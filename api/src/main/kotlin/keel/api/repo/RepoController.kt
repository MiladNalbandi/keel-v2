package keel.api.repo

import org.springframework.web.bind.annotation.GetMapping
import keel.api.events.EventHub
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/projects/{pid}/repo")
class RepoController(private val repo: RepoService, private val hub: EventHub) {

    @GetMapping
    fun info(@PathVariable pid: String): RepoInfo = repo.info(pid)

    @GetMapping("/tree")
    fun tree(@PathVariable pid: String, @RequestParam(defaultValue = "4") depth: Int): List<TreeNode> = repo.tree(pid, depth)

    @GetMapping("/file")
    fun file(@PathVariable pid: String, @RequestParam path: String): FileView = repo.file(pid, path)

    @GetMapping("/commits")
    fun commits(@PathVariable pid: String, @RequestParam(defaultValue = "30") limit: Int): List<Commit> = repo.commits(pid, limit)

    @GetMapping("/history")
    fun history(@PathVariable pid: String, @RequestParam path: String): List<Commit> = repo.history(pid, path)

    @PostMapping("/update-from-base")
    fun updateFromBase(@PathVariable pid: String): MergeResult {
        val r = repo.updateFromBase(pid)
        if (r.merged) hub.publish(pid, "project.changed", mapOf("id" to pid))
        return r
    }
}
