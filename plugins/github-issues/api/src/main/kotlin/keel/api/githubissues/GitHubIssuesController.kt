package keel.api.githubissues

import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** Settings › Connections › GitHub Issues: a project's connection (its tickets are the Tasks plugin's). */
@RestController
@RequestMapping("/api/projects/{pid}/github-issues")
class GitHubIssuesController(private val gh: GitHubIssuesService) {

    @GetMapping
    fun view(@PathVariable pid: String): GhIssuesView = gh.view(pid)

    @PutMapping
    fun save(@PathVariable pid: String, @RequestBody body: GhIssuesSave): GhIssuesView = gh.save(pid, body)

    @DeleteMapping
    fun delete(@PathVariable pid: String): Map<String, Boolean> {
        gh.delete(pid)
        return mapOf("ok" to true)
    }

    /** Who the GitHub token is, and the repo and its issues: the saved connection, or the unsaved repository in the body. */
    @PostMapping("/test")
    fun test(@PathVariable pid: String, @RequestBody(required = false) body: GhIssuesSave?): GhIssuesTest = gh.test(pid, body)

    /** The repo's labels, for the status map. */
    @GetMapping("/labels")
    fun labels(@PathVariable pid: String): List<String> = gh.labels(pid)
}
