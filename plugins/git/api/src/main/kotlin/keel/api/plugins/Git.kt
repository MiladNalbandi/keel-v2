package keel.api.plugins

import com.fasterxml.jackson.databind.JsonNode
import keel.api.engine.EngineClient
import keel.api.events.EventHub
import keel.api.projects.ProjectService
import keel.api.repo.BranchView
import keel.api.repo.RepoService
import keel.api.settings.SettingsService
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

data class GitSwitchBody(val branch: String = "", val create: Boolean = false)
data class GitCommitBody(val message: String = "")
data class GitPrBody(val title: String = "", val body: String = "", val draft: Boolean = false)

/**
 * The Git plugin's actions for the person (Code › Git, KeelBot's buttons). The engine (keel_plugin_git) does them
 * under its rules: never a force push, never a push to main, master or the base branch, a commit only after the
 * secret check and as the author the settings name.
 *
 * The Git plugin's api part (plugins/git, keel-plugin-git.jar), in keel's package keel.api.plugins so keel's component
 * scan finds it on loader.path. It uses keel's core: PluginService (on or off, and the GitHub token of Connections ›
 * GitHub, which stays core because ship opens the pull request with it) and RepoService (the branch view).
 */
@Service
class GitPluginService(
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val plugins: PluginService,
    private val settings: SettingsService,
    private val repo: RepoService,
) {
    private fun call(pid: String, op: String, extra: Map<String, Any?> = emptyMap(), long: Boolean = false): JsonNode {
        plugins.require(pid, "git")
        val s = settings.effective(pid)
        val body = mapOf(
            "root" to projects.root(pid).toString(),
            "keys" to (plugins.githubToken()?.let { mapOf("github" to it) } ?: emptyMap()),
            "settings" to mapOf("commit_author" to s.commitAuthor.trim().ifBlank { null }, "commit_coauthor" to s.commitCoauthor)
                .filterValues { it != null },
        ) + extra
        return engine.post("/plugins/git/$op", body, long = long)
    }

    fun status(pid: String) = call(pid, "status")
    fun branches(pid: String) = call(pid, "branches")
    fun log(pid: String) = call(pid, "log")
    fun pr(pid: String) = call(pid, "pr_status", long = true)
    fun switch(pid: String, b: GitSwitchBody) = call(pid, "switch", mapOf("branch" to b.branch, "create" to b.create))
    fun commit(pid: String, b: GitCommitBody) = call(pid, "commit", mapOf("message" to b.message))
    fun sync(pid: String) = call(pid, "sync", long = true)
    fun push(pid: String) = call(pid, "push", long = true)
    fun openPr(pid: String, b: GitPrBody) = call(pid, "pr", mapOf("title" to b.title, "body" to b.body, "draft" to b.draft), long = true)
    fun cleanup(pid: String) = call(pid, "cleanup")

    /** One branch against the base, read here (no engine call): Code › Source control › a branch. */
    fun branch(pid: String, name: String): BranchView {
        plugins.require(pid, "git")
        return repo.branch(pid, name)
    }
}

@RestController
@RequestMapping("/api/projects/{pid}/git")
class GitPluginController(private val git: GitPluginService, private val hub: EventHub) {
    /** The branch or the files changed: the Code page (tree, changes, branch) reads them again. */
    private fun <T> changed(pid: String, result: T): T {
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        return result
    }

    @GetMapping("/status") fun status(@PathVariable pid: String) = git.status(pid)
    @GetMapping("/branches") fun branches(@PathVariable pid: String) = git.branches(pid)
    @GetMapping("/branch") fun branch(@PathVariable pid: String, @RequestParam name: String) = git.branch(pid, name)
    @GetMapping("/log") fun log(@PathVariable pid: String) = git.log(pid)
    @GetMapping("/pr") fun pr(@PathVariable pid: String) = git.pr(pid)
    @PostMapping("/switch") fun switch(@PathVariable pid: String, @RequestBody body: GitSwitchBody) = changed(pid, git.switch(pid, body))
    @PostMapping("/commit") fun commit(@PathVariable pid: String, @RequestBody body: GitCommitBody) = changed(pid, git.commit(pid, body))
    @PostMapping("/sync") fun sync(@PathVariable pid: String) = changed(pid, git.sync(pid))
    @PostMapping("/push") fun push(@PathVariable pid: String) = git.push(pid)
    @PostMapping("/pr") fun openPr(@PathVariable pid: String, @RequestBody body: GitPrBody) = git.openPr(pid, body)
    @PostMapping("/cleanup") fun cleanup(@PathVariable pid: String) = changed(pid, git.cleanup(pid))
}
