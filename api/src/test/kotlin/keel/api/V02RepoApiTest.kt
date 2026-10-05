package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions

/** v0.2: update from base, file history, unlocks, stacks, wiki refresh. */
class V02RepoApiTest : ApiTest() {

    @Test
    fun `update from base merges main into the branch`() {
        val (pid, root) = newProject("v2-merge", mapOf("README.md" to "# demo\n", "a.txt" to "a\n"))
        git(root, "checkout", "-q", "-b", "feat/x")
        Files.writeString(root.resolve("b.txt"), "b\n")
        git(root, "add", "-A"); git(root, "commit", "-q", "-m", "b on feat")
        git(root, "checkout", "-q", "main")
        Files.writeString(root.resolve("c.txt"), "c\n")
        git(root, "add", "-A"); git(root, "commit", "-q", "-m", "c on main")
        git(root, "checkout", "-q", "feat/x")

        val r = post("/api/projects/$pid/repo/update-from-base").andExpect(status().isOk).json()
        assertThat(r["ok"].asBoolean()).isTrue()
        assertThat(r["merged"].asBoolean()).isTrue()
        assertThat(r["conflicts"].size()).isEqualTo(0)
        assertThat(Files.exists(root.resolve("c.txt"))).isTrue()
        assertThat(get("/api/projects/$pid/repo").json()["behind"].asInt()).isEqualTo(0)

        val again = post("/api/projects/$pid/repo/update-from-base").json()
        assertThat(again["ok"].asBoolean()).isTrue()
        assertThat(again["merged"].asBoolean()).isFalse()

        git(root, "checkout", "-q", "main")
        post("/api/projects/$pid/repo/update-from-base").andExpect(status().isBadRequest)
    }

    @Test
    fun `a conflict aborts the merge and names the files`() {
        val (pid, root) = newProject("v2-conflict", mapOf("README.md" to "# demo\n"))
        git(root, "checkout", "-q", "-b", "feat/y")
        Files.writeString(root.resolve("README.md"), "# feature\n")
        git(root, "commit", "-q", "-am", "feature readme")
        git(root, "checkout", "-q", "main")
        Files.writeString(root.resolve("README.md"), "# main\n")
        git(root, "commit", "-q", "-am", "main readme")
        git(root, "checkout", "-q", "feat/y")

        val r = post("/api/projects/$pid/repo/update-from-base").andExpect(status().isOk).json()
        assertThat(r["ok"].asBoolean()).isFalse()
        assertThat(r["merged"].asBoolean()).isFalse()
        assertThat(r["conflicts"].map { it.asText() }).containsExactly("README.md")
        assertThat(r["output"].asText()).contains("CONFLICT")
        assertThat(Files.exists(root.resolve(".git/MERGE_HEAD"))).isFalse()
        assertThat(Files.readString(root.resolve("README.md"))).isEqualTo("# feature\n")
        assertThat(git(root, "status", "--porcelain")).isBlank()
    }

    @Test
    fun `file history follows renames and refuses outside paths`() {
        val (pid, root) = newProject("v2-history", mapOf("old.txt" to "one\n"))
        git(root, "mv", "old.txt", "new.txt")
        git(root, "commit", "-q", "-m", "rename")
        Files.writeString(root.resolve("new.txt"), "two\n")
        git(root, "commit", "-q", "-am", "edit")

        val h = get("/api/projects/$pid/repo/history?path=new.txt").andExpect(status().isOk).json()
        assertThat(h.map { it["message"].asText() }).containsExactly("edit", "rename", "first commit")
        assertThat(h[0]["author"].asText()).isEqualTo("Test")
        assertThat(h[0]["sha"].asText()).hasSize(40)
        get("/api/projects/$pid/repo/history?path=../x").andExpect(status().isForbidden)
        assertThat(get("/api/projects/$pid/repo/history?path=never.txt").json().size()).isEqualTo(0)
    }

    @Test
    fun `unlock goes to the project's flow through the engine and is refused without one`() {
        val (pid, root) = newProject("v2-unlock")
        post("/api/projects/$pid/unlock", mapOf("path" to "src/main/App.kt")).andExpect(status().isConflict)

        val tid = "t-unlock-engine"
        post("/internal/events", listOf(mapOf("type" to "thread.started", "thread_id" to tid, "project_id" to pid, "at" to "2026-10-03T10:00:00Z",
            "data" to mapOf("title" to "Ranks"))), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        engine.overrides[tid] = mapOf("phase" to "red")
        val r = post("/api/projects/$pid/unlock", mapOf("path" to "./src/main/App.kt", "reason" to "helper")).andExpect(status().isOk).json()
        assertThat(r["via"].asText()).isEqualTo("engine")
        assertThat(r["thread_id"].asText()).isEqualTo(tid)
        assertThat(r["unlocks"][0]["path"].asText()).isEqualTo("src/main/App.kt")
        assertThat(r["unlocks"][0]["phase"].asText()).isEqualTo("red")
        val body = engine.lastBody("/threads/$tid/unlocks")!!
        assertThat(body["path"].asText()).isEqualTo("src/main/App.kt")
        assertThat(body["reason"].asText()).isEqualTo("helper")
        assertThat(body.has("phase")).isFalse()                       // the engine picks the thread's phase

        // same path + phase again: no duplicate; another phase: a second entry
        post("/api/projects/$pid/unlock", mapOf("path" to "src/main/App.kt")).andExpect(status().isOk)
        val two = post("/api/projects/$pid/unlock", mapOf("path" to "src/main/App.kt", "phase" to "green")).json()
        assertThat(two["unlocks"].map { it["phase"].asText() }).containsExactly("red", "green")
        assertThat(engine.lastBody("/threads/$tid/unlocks")!!["phase"].asText()).isEqualTo("green")
        assertThat(Files.exists(root.resolve(".keel").resolve("state.json"))).isFalse()   // nothing written into the project

        post("/api/projects/$pid/unlock", mapOf("path" to "../etc/passwd")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/unlock", mapOf("path" to "")).andExpect(status().isBadRequest)
    }

    @Test
    fun `unlock goes to the waiting thread as a resume payload when it waits on a fix`() {
        val (pid, root) = newProject("v2-unlock-thread")
        val tid = "t-unlock-fix"
        post("/internal/events", listOf(mapOf("type" to "gate.waiting", "thread_id" to tid, "project_id" to pid, "at" to "2026-10-03T10:00:00Z",
            "data" to mapOf("title" to "Guard refused"))), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)

        // a gate wait is not approved by an unlock: it goes to the thread's unlocks in the engine instead
        engine.overrides[tid] = mapOf("status" to "waiting", "phase" to "green", "waiting" to mapOf("step" to "g", "kind" to "gate", "title" to "t", "detail" to "", "options" to listOf("approve", "reject")))
        assertThat(post("/api/projects/$pid/unlock", mapOf("path" to "a.kt")).json()["via"].asText()).isEqualTo("engine")
        assertThat(engine.calls.none { it.path == "/threads/$tid/resume" }).isTrue()

        engine.overrides[tid] = mapOf("status" to "waiting", "phase" to "green", "waiting" to mapOf("step" to "green", "kind" to "fix", "title" to "Guard refused", "detail" to "", "options" to listOf("approve", "reject")))
        val r = post("/api/projects/$pid/unlock", mapOf("path" to "src/main/Score.kt")).andExpect(status().isOk).json()
        assertThat(r["via"].asText()).isEqualTo("thread")
        assertThat(r["thread_id"].asText()).isEqualTo(tid)
        assertThat(r["unlocks"][0]["path"].asText()).isEqualTo("src/main/Score.kt")
        val body = engine.lastBody("/threads/$tid/resume")!!
        assertThat(body["decision"].asText()).isEqualTo("approve")
        assertThat(body["payload"]["unlock"]["path"].asText()).isEqualTo("src/main/Score.kt")
        assertThat(body["payload"]["unlock"]["phase"].asText()).isEqualTo("green")
        assertThat(engine.unlocks[tid]!!.map { it["path"] }).containsExactly("a.kt")
        assertThat(Files.exists(root.resolve(".keel").resolve("state.json"))).isFalse()
    }

    @Test
    fun `stacks can be copied into the project and keel packs installed`() {
        val (pid, root) = newProject("v2-stacks", mapOf("build.gradle.kts" to "", "src/main/kotlin/A.kt" to "class A"))
        val before = get("/api/projects/$pid/stacks").json()
        assertThat(before.first { it["name"].asText() == "django" }["installable"].asBoolean()).isTrue()
        assertThat(before.first { it["name"].asText() == "kotlin-spring" }["installable"].asBoolean()).isFalse()

        val made = post("/api/projects/$pid/stacks", mapOf("name" to "my-api", "from" to "kotlin-spring")).andExpect(status().isOk).json()
        assertThat(made["name"].asText()).isEqualTo("my-api")
        assertThat(made["source"].asText()).isEqualTo("project")
        assertThat(made["detected"].asBoolean()).isTrue()
        assertThat(made["commands"][0]["name"].asText()).isEqualTo("api_test_ac")
        assertThat(Files.readString(root.resolve(".keel/stacks/my-api.yml"))).startsWith("name: my-api")
        // no `from`: the closest is the detected kotlin-spring stack
        val closest = post("/api/projects/$pid/stacks", mapOf("name" to "other")).andExpect(status().isOk).json()
        assertThat(closest["lane"].asText()).isEqualTo("api")
        post("/api/projects/$pid/stacks", mapOf("name" to "my-api")).andExpect(status().isConflict)
        post("/api/projects/$pid/stacks", mapOf("name" to "Bad Name!")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/stacks", mapOf("name" to "x2", "from" to "nope")).andExpect(status().isNotFound)

        post("/api/projects/$pid/stacks/kotlin-spring/install").andExpect(status().isConflict)
        withFakeKeel {
            val installed = post("/api/projects/$pid/stacks/django/install").andExpect(status().isOk).json()
            assertThat(installed["source"].asText()).isEqualTo("project")
            assertThat(installed["installable"].asBoolean()).isFalse()
            assertThat(Files.readString(root.resolve(".keel-fake-args"))).contains("packs add ${keelHome.resolve("packs")} --project")
            // a second install of an installed pack is fine
            post("/api/projects/$pid/stacks/django/install").andExpect(status().isOk)
        }
        post("/api/projects/$pid/stacks/nope/install").andExpect(status().isNotFound)
    }

    @Test
    fun `wiki refresh starts the knowledge-refresh template with one AC per stale section`() {
        val (pid, root) = newProject("v2-wiki", mapOf("docs/knowledge/architecture.md" to "# A\n", "docs/knowledge/domain.md" to "# D\n", "src/A.kt" to "class A"))
        post("/api/projects/$pid/wiki/refresh", emptyMap<String, Any>()).andExpect(status().isBadRequest)

        Files.writeString(root.resolve("src/A.kt"), "class A2")
        gitEnv(root, mapOf("GIT_COMMITTER_DATE" to "2099-01-01T00:00:00Z", "GIT_AUTHOR_DATE" to "2099-01-01T00:00:00Z"), "commit", "-q", "-am", "code moved on")
        assertThat(get("/api/projects/$pid/memory").json()["knowledge"].first { it["id"].asText() == "architecture" }["status"].asText()).isEqualTo("stale")

        val state = post("/api/projects/$pid/wiki/refresh").andExpect(status().isOk).json()
        assertThat(state["thread_id"].asText()).isEqualTo("t-stub-1")
        val body = engine.lastBody("/threads")!!
        assertThat(body["workflow"]["id"].asText()).isEqualTo("knowledge-refresh")
        assertThat(body["acs"].map { it["id"].asText() }).containsExactly("architecture", "domain")
        assertThat(body["acs"][0]["layer"].asText()).isEqualTo("API")
        assertThat(body["title"].asText()).contains("architecture")

        post("/api/projects/$pid/wiki/refresh", mapOf("sections" to listOf("domain"))).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads")!!["acs"].map { it["title"].asText() }).containsExactly("domain")
        post("/api/projects/$pid/wiki/refresh", mapOf("sections" to listOf("gossip"))).andExpect(status().isBadRequest)
    }

    /** Puts a shell `bin/keel` into the fixture KEEL_HOME for the block, then removes it. */
    private fun withFakeKeel(block: () -> Unit) {
        val bin = keelHome.resolve("bin/keel")
        fakeKeel(keelHome, FAKE_KEEL)
        try {
            block()
        } finally {
            Files.deleteIfExists(bin)
            Files.deleteIfExists(bin.parent)
        }
    }

    companion object {
        val FAKE_KEEL = """
            #!/bin/sh
            echo "${'$'}@" >> "${'$'}PWD/.keel-fake-args"
            if [ "${'$'}1" = "packs" ]; then
              if [ -e .keel/stacks/packs ]; then echo ".keel/stacks/packs already exists. Remove it first" >&2; exit 1; fi
              mkdir -p .keel/stacks && cp -R "${'$'}3" .keel/stacks/packs
            fi
        """.trimIndent() + "\n"

        fun fakeKeel(home: Path, script: String) {
            val bin = home.resolve("bin/keel")
            Files.createDirectories(bin.parent)
            Files.writeString(bin, script)
            runCatching { Files.setPosixFilePermissions(bin, PosixFilePermissions.fromString("rwxr-xr-x")) }
        }
    }
}
