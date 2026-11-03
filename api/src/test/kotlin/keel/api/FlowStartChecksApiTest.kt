package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

/**
 * 0.18.57 what a flow start checks first (real use on a project whose PHP lives only in a docker compose service):
 * tool folders never block and a blocking path can be ignored, a container that sees only the project folder keeps
 * flows in the folder, and one task has one owner.
 */
class FlowStartChecksApiTest : ApiTest() {

    private fun start(pid: String, vararg extra: Pair<String, Any?>) =
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x", "allow_fake" to true) + extra.toMap())

    @Test
    fun `tool folders do not block a start, and a blocking untracked path can be ignored`() {
        val (pid, root) = newProject("tools")
        Files.createDirectories(root.resolve(".serena/cache"))
        Files.writeString(root.resolve(".serena/cache/x.json"), "{}")
        Files.createDirectories(root.resolve(".idea"))
        Files.writeString(root.resolve(".idea/workspace.xml"), "<x/>")
        Files.writeString(root.resolve(".DS_Store"), "x")
        Files.createDirectories(root.resolve(".claude"))
        Files.writeString(root.resolve(".claude/settings.local.json"), "{}")
        Files.createDirectories(root.resolve("scratch"))
        Files.writeString(root.resolve("scratch/notes.md"), "my notes\n")
        val res = start(pid).andExpect(status().isConflict).json()
        assertThat(res["error"].asText()).contains("scratch/notes.md").doesNotContain(".serena").doesNotContain(".idea")
        assertThat(res["ignorable"].map { it.asText() }).containsExactly("scratch/")
        assertThat(res["hint"].asText()).contains("Ignore this path adds it to .git/info/exclude")

        val done = post("/api/projects/$pid/doctor/workspace/exclude", mapOf("path" to "scratch/")).andExpect(status().isOk).json()
        assertThat(done["clean"].asBoolean()).isTrue()
        assertThat(Files.readString(root.resolve(".git/info/exclude"))).contains("scratch/")
        start(pid).andExpect(status().isOk)
    }

    @Test
    fun `a tracked file cannot be ignored`() {
        val (pid, root) = newProject("tracked")
        Files.writeString(root.resolve("README.md"), "changed\n")
        post("/api/projects/$pid/doctor/workspace/exclude", mapOf("path" to "README.md")).andExpect(status().isConflict)
    }

    @Test
    fun `a container that sees only the project folder keeps a second flow out, with the reason`() {
        val (pid, _) = newProject("shop")
        engine.extraRoutes["/start-check"] = { _ -> 200 to mapOf("worktree" to mapOf("ok" to false, "service" to "app",
            "reason" to "tests run in the container `app`, which sees only the project folder"), "base" to null) }
        try {
            assertThat(get("/api/projects/$pid/start-check").andExpect(status().isOk).json()["worktree"]["ok"].asBoolean()).isFalse()
            engine.nextThreadIds.add("t-shop-a")
            start(pid, "title" to "Euro prices").andExpect(status().isOk)
            assertThat(engine.lastBody("/threads")!!["root"].asText()).doesNotContain(".keel/worktrees")
            val made = engine.calls.count { it.path == "/worktrees" }
            val res = start(pid, "title" to "Dollar prices").andExpect(status().isConflict).json()
            assertThat(res["error"].asText()).contains("tests run in the container `app`, which sees only the project folder")
            assertThat(res["hint"].asText()).contains("Wait until that flow ends")
            assertThat(engine.calls.count { it.path == "/worktrees" }).isEqualTo(made)
        } finally {
            engine.extraRoutes.remove("/start-check")
        }
    }

    @Test
    fun `one owner per task - a second start on the same ticket is refused and names who, force starts it`() {
        val (pid, _) = newProject("owners")
        engine.nextThreadIds.add("t-own-a")
        start(pid, "title" to "SHOPS-1234: Euro prices").andExpect(status().isOk)
        val res = start(pid, "title" to "Prices in euros", "options" to mapOf("ticket" to "SHOPS-1234")).andExpect(status().isConflict).json()
        assertThat(res["error"].asText()).contains("flow SHOPS-1234: Euro prices at").contains("the same ticket SHOPS-1234")
        assertThat(res["owners"][0]["kind"].asText()).isEqualTo("flow")
        assertThat(res["owners"][0]["id"].asText()).isEqualTo("t-own-a")
        // another ticket starts (next to it, in a worktree); force starts the same ticket anyway
        engine.nextThreadIds.add("t-own-b")
        start(pid, "title" to "SHOPS-99: Cart badge").andExpect(status().isOk)
        engine.nextThreadIds.add("t-own-c")
        start(pid, "title" to "Prices in euros", "options" to mapOf("ticket" to "SHOPS-1234"), "force" to true).andExpect(status().isOk)
    }

    @Test
    fun `a file the request only names is a warning, not a refusal`() {
        val (pid, root) = newProject("named")
        engine.nextThreadIds.add("t-named-a")
        start(pid, "title" to "Euro prices").andExpect(status().isOk)
        // the running flow changed src/Price.kt on its branch; the next request names that file as an example
        git(root, "switch", "-q", "-c", "feat/euro")
        Files.createDirectories(root.resolve("src"))
        Files.writeString(root.resolve("src/Price.kt"), "class Price\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "feat: euro")
        engine.nextThreadIds.add("t-named-b")
        val res = start(pid, "title" to "Cart badge", "request" to "A badge on the cart, styled like src/Price.kt does it.")
            .andExpect(status().isOk).json()
        assertThat(res["overlap_note"].asText()).contains("Euro prices").contains("src/Price.kt")
    }

    @Test
    fun `the project's doctor lists stale commands from the engine's config check`() {
        val (pid, root) = newProject("stale")
        engine.extraRoutes["/config-check"] = { _ -> 200 to mapOf("stale" to listOf(mapOf("key" to "smoke", "line" to 3)),
            "lines" to listOf(".keel/config.yml:3 commands.smoke: the Makefile has no target 'smoke' (make smoke)")) }
        try {
            val res = get("/api/projects/$pid/doctor/config").andExpect(status().isOk).json()
            assertThat(res["lines"][0].asText()).startsWith(".keel/config.yml:3 commands.smoke")
            assertThat(engine.calls.last { it.path == "/config-check" }.query).contains("root=")
        } finally {
            engine.extraRoutes.remove("/config-check")
        }
    }
}
