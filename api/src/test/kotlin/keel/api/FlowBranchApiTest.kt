package keel.api

import keel.api.flow.branchName
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** A flow's branch: {flow} is its workflow id, a fix goes on fix/, and a fix of a hunt finding gets its own worktree. */
class FlowBranchApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate

    @Test
    fun `the branch pattern names the workflow and a fix goes on fix`() {
        assertThat(branchName(null, "Top 10 scores", "feature")).isEqualTo("feat/top-10-scores")
        assertThat(branchName("", "Average is wrong", "fix")).isEqualTo("fix/average-is-wrong")
        assertThat(branchName("feat/{slug}", "Average is wrong", "fix")).isEqualTo("fix/average-is-wrong")
        assertThat(branchName("{user}/{flow}/{slug}", "Ranks", "hunt")).isEqualTo("keel/hunt/ranks")
        assertThat(branchName("{flow}/{slug}", "Ranks", "fix")).isEqualTo("fix/ranks")
        assertThat(branchName("work/{slug}", "Ranks", "fix")).isEqualTo("work/ranks")
    }

    @Test
    fun `a read-only review never makes or checks out a branch and leaves the folder as it was`() {
        val review = mapOf("id" to "review-pr", "name" to "review a PR (keel)", "based_on" to null, "keel_rules" to true, "version" to 1,
            "steps" to listOf(
                mapOf("id" to "prep", "kind" to "code", "name" to "prepare the review", "action" to "ticket_prep", "phase" to "review"),
                mapOf("id" to "reviewers", "kind" to "parallel", "name" to "one reviewer per job", "agent" to "pr-reviewer", "phase" to "review"),
                mapOf("id" to "share", "kind" to "gate", "name" to "the review", "phase" to "review")),
            "yaml" to "")
        engine.extraTemplates += review
        try {
            val (pid, root) = newProject("review-folder")
            git(root, "checkout", "-q", "-b", "feat/my-work")
            java.nio.file.Files.writeString(root.resolve("wip.txt"), "not committed\n")      // the person's own work
            fun snapshot() = listOf(git(root, "rev-parse", "--abbrev-ref", "HEAD"), git(root, "rev-parse", "HEAD"),
                git(root, "status", "--porcelain"), git(root, "branch", "--list"), git(root, "worktree", "list", "--porcelain"))
            val before = snapshot()
            post("/api/projects/$pid/flows", mapOf("workflow_id" to "review-pr", "title" to "Review the dispatch PR",
                "request" to "branch: feat/x")).andExpect(status().isOk)   // no allow_dirty: a review does not mind wip.txt
            assertThat(snapshot()).isEqualTo(before)
            val row = jdbc.queryForMap("SELECT worktree, branch FROM threads WHERE project_id = ?", pid)
            assertThat(row["worktree"]).isNull()
            // a second review while the first one waits in the folder: still no worktree and no branch of its own
            post("/api/projects/$pid/flows", mapOf("workflow_id" to "review-pr", "title" to "Review it again")).andExpect(status().isOk)
            assertThat(snapshot()).isEqualTo(before)
        } finally {
            engine.extraTemplates -= review
        }
    }

    @Test
    fun `a fix started on main gets a fix branch`() {
        val fix = engine.featureTemplate + mapOf("id" to "fix", "name" to "fix (keel)")
        engine.extraTemplates += fix
        try {
            val (pid, root) = newProject("fix-branch")
            post("/api/projects/$pid/flows", mapOf("workflow_id" to "fix", "title" to "Average is wrong")).andExpect(status().isOk)
            assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("fix/average-is-wrong")
        } finally {
            engine.extraTemplates -= fix
        }
    }

    @Test
    fun `B11 a workflow with own_branch false starts on the branch the folder is on`() {
        val hunt = engine.featureTemplate + mapOf("id" to "hunt", "name" to "hunt (keel)",
            "yaml" to "id: hunt\nname: hunt (keel)\nown_branch: false\nsteps: []\n")
        engine.extraTemplates += hunt
        try {
            val (pid, root) = newProject("hunt-no-branch")
            post("/api/projects/$pid/flows", mapOf("workflow_id" to "hunt", "title" to "Bug hunt on scores", "allow_fake" to true))
                .andExpect(status().isOk)
            assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("main")
            assertThat(git(root, "branch", "--list", "feat/*").trim()).isEmpty()
        } finally {
            engine.extraTemplates -= hunt
        }
    }

    @Test
    fun `a start the engine refuses leaves the checkout on its own branch and drops the new one`() {
        val fix = engine.featureTemplate + mapOf("id" to "fix", "name" to "fix (keel)")
        engine.extraTemplates += fix
        engine.threadRefusal = 409 to mapOf("error" to "This workflow needs Database.")
        try {
            val (pid, root) = newProject("branch-undo")
            post("/api/projects/$pid/flows", mapOf("workflow_id" to "fix", "title" to "Average is wrong")).andExpect(status().is4xxClientError)
            assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("main")
            assertThat(git(root, "branch", "--list", "fix/*").trim()).isEmpty()
        } finally {
            engine.threadRefusal = null
            engine.extraTemplates -= fix
        }
    }

    @Test
    fun `a flow the engine gave its own worktree records it instead of the parent's`() {
        val (pid, _) = newProject("fix-own-worktree")
        engine.nextThreadIds.add("t-hunt-next")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Hunt next", "allow_fake" to true)).andExpect(status().isOk)
        post("/internal/events", listOf(mapOf("type" to "thread.started", "thread_id" to "t-fix-own", "project_id" to pid,
            "at" to java.time.Instant.now().toString(), "data" to mapOf("title" to "F-001: ranks", "workflow_id" to "fix",
                "parent" to mapOf("thread_id" to "t-hunt-next"),
                "worktree" to mapOf("name" to "flow-f-001-ranks-abc123", "branch" to "fix/f-001-ranks", "base" to "main")))),
            mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        val row = jdbc.queryForMap("SELECT worktree, branch FROM threads WHERE id = 't-fix-own'")
        assertThat(row["worktree"]).isEqualTo("flow-f-001-ranks-abc123")
        assertThat(row["branch"]).isEqualTo("fix/f-001-ranks")
    }

    @Test
    fun `B8 a flow started on another branch gets its own branch from the base branch`() {
        val (pid, root) = newProject("branch-from-base")
        git(root, "checkout", "-q", "-b", "feat/init-2026-10-25")           // what init left checked out, no commit
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Players see their rank", "allow_fake" to true))
            .andExpect(status().isOk)
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("feat/players-see-their-rank")
        assertThat(git(root, "rev-parse", "HEAD").trim()).isEqualTo(git(root, "rev-parse", "main").trim())
    }

    @Test
    fun `B8 a branch with commits of its own is not the base of the next flow`() {
        val (pid, root) = newProject("branch-not-stacked")
        git(root, "checkout", "-q", "-b", "fix/old-work")
        java.nio.file.Files.writeString(root.resolve("old.txt"), "old\n")
        git(root, "add", "old.txt"); git(root, "commit", "-q", "-m", "old work")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "New thing", "allow_fake" to true)).andExpect(status().isOk)
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("feat/new-thing")
        assertThat(java.nio.file.Files.exists(root.resolve("old.txt"))).isFalse()
    }

    @Test
    fun `B8 continue this branch stays on the branch the folder is on`() {
        val (pid, root) = newProject("branch-continue")
        git(root, "checkout", "-q", "-b", "feat/my-work")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "More of it", "allow_fake" to true, "branch" to "continue"))
            .andExpect(status().isOk)
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("feat/my-work")
    }

    private fun <T> withFlow(id: String, body: () -> T): T {
        val wf = engine.featureTemplate + mapOf("id" to id, "name" to "$id (keel)")
        engine.extraTemplates += wf
        try {
            return body()
        } finally {
            engine.extraTemplates -= wf
        }
    }

    /** A feature branch with a commit of its own: the work a ship (cover, review, lint, address) flow finishes. */
    private fun onFeatureBranch(name: String): Pair<String, java.nio.file.Path> {
        val (pid, root) = newProject(name)
        git(root, "checkout", "-q", "-b", "feat/coupons")
        java.nio.file.Files.writeString(root.resolve("coupons.txt"), "coupons\n")
        git(root, "add", "coupons.txt"); git(root, "commit", "-q", "-m", "feat: coupons")
        return pid to root
    }

    /** origin/main one commit ahead of the local main (a pull not made yet). */
    private fun originAhead(root: java.nio.file.Path) {
        git(root, "checkout", "-q", "-b", "tmp-origin", "main")
        java.nio.file.Files.writeString(root.resolve("team.txt"), "the team's latest\n")
        git(root, "add", "team.txt"); git(root, "commit", "-q", "-m", "feat: the team's latest")
        git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(root, "checkout", "-q", "-")
        git(root, "branch", "-q", "-D", "tmp-origin")
    }

    @Test
    fun `B7 ship started with no branch choice continues the branch the folder is on`() = withFlow("ship") {
        for (wf in listOf("ship", "feature")) {
            val (pid, root) = onFeatureBranch("ship-continues-$wf")
            val body = mapOf("workflow_id" to wf, "title" to "Ship the coupons", "allow_fake" to true)
            post("/api/projects/$pid/flows", body).andExpect(status().isOk)
            val head = git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()
            if (wf == "ship") {
                assertThat(head).isEqualTo("feat/coupons")
                assertThat(java.nio.file.Files.exists(root.resolve("coupons.txt"))).isTrue()
            } else {
                assertThat(head).isEqualTo("feat/ship-the-coupons")       // a flow that builds something: new, as B8
            }
        }
    }

    @Test
    fun `B7 ship asked for a new branch still gets one`() = withFlow("ship") {
        val (pid, root) = onFeatureBranch("ship-new")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "ship", "title" to "Ship it", "allow_fake" to true, "branch" to "new"))
            .andExpect(status().isOk)
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("feat/ship-it")
    }

    @Test
    fun `B7 a new branch starts from origin's base when the local base is behind it`() {
        val (pid, root) = onFeatureBranch("branch-from-origin")
        originAhead(root)
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Basket totals", "allow_fake" to true))
            .andExpect(status().isOk)
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("feat/basket-totals")
        assertThat(git(root, "rev-parse", "HEAD").trim()).isEqualTo(git(root, "rev-parse", "origin/main").trim())
        // the flow's branch does not track origin/main: a push goes to its own name
        assertThat(git(root, "branch", "-vv", "--list", "feat/basket-totals")).doesNotContain("[origin/main")
    }

    @Test
    fun `B7 on a stale base the new branch starts from origin's base too`() {
        val (pid, root) = newProject("base-behind")
        originAhead(root)
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Basket totals", "allow_fake" to true))
            .andExpect(status().isOk)
        assertThat(git(root, "rev-parse", "HEAD").trim()).isEqualTo(git(root, "rev-parse", "origin/main").trim())
    }

    @Test
    fun `B7 a local base that went its own way from origin is refused, not branched from`() {
        val (pid, root) = onFeatureBranch("base-diverged")
        originAhead(root)
        git(root, "checkout", "-q", "main")
        java.nio.file.Files.writeString(root.resolve("local.txt"), "local only\n")
        git(root, "add", "local.txt"); git(root, "commit", "-q", "-m", "chore: local only")
        git(root, "checkout", "-q", "feat/coupons")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Basket totals", "allow_fake" to true))
            .andExpect(status().isConflict)
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("feat/coupons")
    }

    @Test
    fun `B8 init that made no commit gives the folder back on the base branch`() {
        val init = engine.featureTemplate + mapOf("id" to "init", "name" to "init (keel)")
        engine.extraTemplates += init
        try {
            val (pid, root) = newProject("init-empty-branch")
            engine.nextThreadIds.add("t-init-empty")
            post("/api/projects/$pid/flows", mapOf("workflow_id" to "init", "title" to "init 2026-10-25", "allow_fake" to true)).andExpect(status().isOk)
            assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("keel/setup")
            java.nio.file.Files.writeString(root.resolve("README.md"), "# demo, changed\n")      // uncommitted: it stays
            post("/internal/events", listOf(mapOf("type" to "thread.done", "thread_id" to "t-init-empty", "project_id" to pid,
                "at" to java.time.Instant.now().toString(), "data" to mapOf("title" to "init"))), mapOf("X-Keel-Token" to TOKEN))
                .andExpect(status().isOk)
            assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("main")
            assertThat(git(root, "branch", "--list", "keel/setup*").trim()).isEmpty()
            assertThat(java.nio.file.Files.readString(root.resolve("README.md"))).contains("changed")
        } finally {
            engine.extraTemplates -= init
        }
    }

    @Test
    fun `B8 init that committed keeps its branch`() {
        val init = engine.featureTemplate + mapOf("id" to "init", "name" to "init (keel)")
        engine.extraTemplates += init
        try {
            val (pid, root) = newProject("init-kept-branch")
            engine.nextThreadIds.add("t-init-kept")
            post("/api/projects/$pid/flows", mapOf("workflow_id" to "init", "title" to "init 2026-10-25", "allow_fake" to true)).andExpect(status().isOk)
            java.nio.file.Files.writeString(root.resolve("setup.txt"), "x\n")
            git(root, "add", "setup.txt"); git(root, "commit", "-q", "-m", "chore: keel setup")
            post("/internal/events", listOf(mapOf("type" to "thread.done", "thread_id" to "t-init-kept", "project_id" to pid,
                "at" to java.time.Instant.now().toString(), "data" to mapOf("title" to "init"))), mapOf("X-Keel-Token" to TOKEN))
                .andExpect(status().isOk)
            assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("keel/setup")
        } finally {
            engine.extraTemplates -= init
        }
    }
}
