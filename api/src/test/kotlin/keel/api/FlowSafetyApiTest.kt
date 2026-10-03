package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

/** A flow must never mix the user's own work into keel's commits, and never work on main. */
class FlowSafetyApiTest : ApiTest() {

    private fun branchOf(root: java.nio.file.Path) =
        ProcessBuilder("git", "rev-parse", "--abbrev-ref", "HEAD").directory(root.toFile()).start().inputStream.bufferedReader().readText().trim()

    @Test
    fun `a dirty working tree is refused with the files named, and can be overridden`() {
        val (pid, root) = newProject("dirty")
        Files.writeString(root.resolve("README.md"), "# my work in progress\n")
        Files.writeString(root.resolve("notes.md"), "untracked\n")
        val before = engine.calls.count { it.path == "/threads" }
        val res = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x")).andExpect(status().isConflict).json()
        assertThat(res["error"].asText()).contains("uncommitted changes").contains("README.md").contains("notes.md")
        assertThat(res["hint"].asText()).contains("Commit or stash")
        assertThat(engine.calls.count { it.path == "/threads" }).isEqualTo(before)   // the engine was never asked

        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x", "allow_dirty" to true)).andExpect(status().isOk)
    }

    @Test
    fun `keel's own state files do not count as uncommitted work`() {
        val (pid, root) = newProject("own-files")
        Files.createDirectories(root.resolve(".keel/logs"))
        Files.writeString(root.resolve(".keel/state.json"), "{}")
        Files.writeString(root.resolve(".keel/logs/events.jsonl"), "{}\n")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x")).andExpect(status().isOk)
    }

    @Test
    fun `a flow started on main gets its own branch from the branch pattern`() {
        val (pid, root) = newProject("branching")
        assertThat(branchOf(root)).isEqualTo("main")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Top 10 scores")).andExpect(status().isOk)
        assertThat(branchOf(root)).isEqualTo("feat/top-10-scores")

        // On a feature branch already: stays there.
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Another one")).andExpect(status().isOk)
        assertThat(branchOf(root)).isEqualTo("feat/top-10-scores")
    }

    @Autowired lateinit var projectService: keel.api.projects.ProjectService

    @Test
    fun `a different project mounted at the same path is a different project, and comes back with its history`() {
        val (_, root) = newProject("mount-a")
        val a = projectService.registerWorkspace(root.toString(), "ludus-x")
        val b = projectService.registerWorkspace(root.toString(), "scores-x")
        assertThat(b.id).isEqualTo("scores-x").isNotEqualTo(a.id)
        val listed = get("/api/projects").json().map { it["id"].asText() }
        assertThat(listed).contains("scores-x").doesNotContain("ludus-x")
        val back = projectService.registerWorkspace(root.toString(), "ludus-x")
        assertThat(back.id).isEqualTo("ludus-x")
        assertThat(get("/api/projects").json().map { it["id"].asText() }).contains("ludus-x").doesNotContain("scores-x")
    }
}
