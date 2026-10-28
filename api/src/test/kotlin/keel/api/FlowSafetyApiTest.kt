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
        Files.createDirectories(root.resolve(".keel/agents"))
        Files.writeString(root.resolve(".keel/ladder.json"), "{}")
        Files.writeString(root.resolve(".keel/agents/explorer.md"), "# explorer\n")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x")).andExpect(status().isOk)
    }

    /** 0.18.64 a field report: layout repo with keel's notes under .keel/ (memory.dir .keel/docs/knowledge); the start's
     *  409 listed keel's own .keel/docs/ and .keel/specs/ files. keel's output never blocks keel; the person's settings
     *  under .keel/ still do. */
    @Test
    fun `keel's own output under keel's folder does not block a start, the person's settings there do`() {
        val (pid, root) = newProject("own-output", mapOf("README.md" to "# demo\n",
            ".keel/config.yml" to "layout: repo\nmemory:\n  dir: .keel/docs/knowledge\nspecs:\n  dir: .keel/specs\n"))
        for (rel in listOf(".keel/docs/knowledge/architecture.md", ".keel/specs/clarify/2026-10-01-webhook.md", ".keel/hunts/r1/report.md",
                ".keel/chats/c1.md", ".keel/RUNNING.md")) {
            Files.createDirectories(root.resolve(rel).parent)
            Files.writeString(root.resolve(rel), "# keel wrote this\n")
        }
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x")).andExpect(status().isOk)

        val (pid2, root2) = newProject("own-settings", mapOf("README.md" to "# demo\n", ".keel/config.yml" to "layout: repo\n"))
        Files.writeString(root2.resolve(".keel/config.yml"), "layout: repo\nrules:\n  off: [read-twice]\n")
        val res = post("/api/projects/$pid2/flows", mapOf("workflow_id" to "feature", "title" to "x")).andExpect(status().isConflict).json()
        assertThat(res["error"].asText()).contains(".keel/config.yml")
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
