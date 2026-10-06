package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

/** v0.7.x: several flows at once, each next to the others in its own worktree; the board, overlaps and conflicts. */
class FlowBoardApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate

    @Test
    fun `a second flow runs in its own worktree from the base branch, and the board shows both with what they share`() {
        val (pid, root) = newProject("board", mapOf("README.md" to "# shop\n", "src/price.js" to "export const p = 1;\n"))
        engine.nextThreadIds.add("t-board-a")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Euro prices", "allow_fake" to true)).andExpect(status().isOk)
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("feat/euro-prices")
        assertThat(engine.lastBody("/threads")!!["root"].asText()).isEqualTo(root.toString())
        // the folder is busy: "folder" is refused, auto goes to a worktree
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Dollar prices", "allow_fake" to true, "where" to "folder"))
            .andExpect(status().isConflict)
        engine.nextThreadIds.add("t-board-b")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Dollar prices", "allow_fake" to true)).andExpect(status().isOk)
        val wtRoot = engine.lastBody("/threads")!!["root"].asText()
        assertThat(wtRoot).startsWith(root.resolve(".keel/worktrees").toString())
        assertThat(engine.lastBody("/worktrees")!!["start"].asText()).isEqualTo("main")
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("feat/euro-prices")       // the folder did not move
        val row = jdbc.queryForMap("SELECT worktree, branch FROM threads WHERE id = 't-board-b'")
        assertThat(row["branch"]).isEqualTo("feat/dollar-prices")
        assertThat(row["worktree"].toString()).startsWith("flow-dollar-prices-")

        // both change src/price.js, differently: an overlap and a conflict between their branches
        Files.writeString(root.resolve("src/price.js"), "export const p = 2; // euro\n")
        git(root, "commit", "-qam", "euro")
        val wt = java.nio.file.Paths.get(wtRoot)
        Files.writeString(wt.resolve("src/price.js"), "export const p = 3; // dollar\n")
        Files.writeString(wt.resolve("src/usd.js"), "export const usd = 1;\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-qm", "dollar")
        val board = get("/api/projects/$pid/flows").andExpect(status().isOk).json()
        val flows = board["flows"].associateBy { it["thread_id"].asText() }
        assertThat(flows.keys).containsExactlyInAnyOrder("t-board-a", "t-board-b")
        assertThat(flows["t-board-a"]!!["where"].asText()).isEqualTo("folder")
        assertThat(flows["t-board-b"]!!["where"].asText()).isEqualTo("worktree")
        assertThat(flows["t-board-b"]!!["files"].map { it.asText() }).containsExactly("src/price.js", "src/usd.js")
        assertThat(board["overlaps"].map { it["file"].asText() }).containsExactly("src/price.js")
        assertThat(board["conflicts"][0]["files"].map { it.asText() }).containsExactly("src/price.js")
        assertThat(board["order"].size()).isEqualTo(2)
        // the folder's flow stays "the" flow of the project; the other one is reached by id
        assertThat(get("/api/projects/$pid/flow").json()["thread"]["thread_id"].asText()).isEqualTo("t-board-a")
        assertThat(get("/api/projects/$pid/flows/t-board-b").andExpect(status().isOk).json()["thread"]["thread_id"].asText()).isEqualTo("t-board-b")
        assertThat(get("/api/projects").json().first { it["id"].asText() == pid }["flows"].asInt()).isEqualTo(2)
        // its resume goes to its worktree
        post("/api/threads/t-board-b/resume", mapOf("decision" to "approve")).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads/t-board-b/resume")!!["root"].asText()).isEqualTo(wtRoot)

        // a flow it hands over to works in the same worktree
        post("/internal/events", listOf(mapOf("type" to "thread.started", "thread_id" to "t-board-c", "project_id" to pid,
            "at" to java.time.Instant.now().toString(), "data" to mapOf("title" to "Dollar prices (feature)", "workflow_id" to "feature",
                "parent" to mapOf("thread_id" to "t-board-b")))), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        assertThat(jdbc.queryForObject("SELECT worktree FROM threads WHERE id = 't-board-c'", String::class.java)).isEqualTo(row["worktree"])

        // a finished flow's worktree goes on request; its branch stays
        post("/api/threads/t-board-b/worktree/remove").andExpect(status().isConflict)          // it still runs
        engine.overrides["t-board-b"] = mapOf("status" to "done")
        engine.overrides["t-board-c"] = mapOf("status" to "done")
        jdbc.update("UPDATE threads SET status = 'done' WHERE id IN ('t-board-b', 't-board-c')")
        assertThat(get("/api/projects/$pid/flows").json()["flows"].first { it["thread_id"].asText() == "t-board-b" }["worktree_left"].asBoolean()).isTrue()
        post("/api/threads/t-board-b/worktree/remove").andExpect(status().isOk)
        assertThat(Files.exists(wt)).isFalse()
        assertThat(git(root, "branch", "--list", "feat/dollar-prices").trim()).isNotEmpty()
        assertThat(get("/api/projects/$pid/flows").json()["flows"].map { it["thread_id"].asText() }).containsExactly("t-board-a")
    }
}
