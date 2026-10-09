package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

/** v0.2: unlocks, stacks (the wiki refresh: plugins/wiki/api, WikiApiTest; update from base and file history: the Code
 *  plugin's RepoApiTest, plugins/code/api). */
class V02RepoApiTest : ApiTest() {

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
        // v0.4.1: every stack lists its tools with when they run, what a failure means and what they are for
        val ktlint = before.first { it["name"].asText() == "kotlin-spring" }["tools"].first { it["name"].asText() == "ktlint" }
        assertThat(ktlint["on"].asText()).isEqualTo("pre-commit")
        assertThat(ktlint["fail"].asText()).isEqualTo("block")
        assertThat(ktlint["description"].asText()).isEqualTo("Refuse a commit whose Kotlin breaks the ktlint rules")
        assertThat(ktlint["match"].asText()).isEqualTo("\\.kts?$")
        val sonar = before.first { it["name"].asText() == "kotlin-spring" }["tools"].first { it["name"].asText() == "sonar" }
        assertThat(sonar["off"].asBoolean()).isTrue()

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
        // keel copies the pack folder itself (no keel v1 binary since 0.4.1): stack.yml and its skills
        val installed = post("/api/projects/$pid/stacks/django/install").andExpect(status().isOk).json()
        assertThat(installed["source"].asText()).isEqualTo("project")
        assertThat(installed["installable"].asBoolean()).isFalse()
        val pack = root.resolve(".keel/stacks/django")
        assertThat(Files.readString(pack.resolve("stack.yml"))).isEqualTo(Files.readString(contentDir.resolve("packs/django/stack.yml")))
        assertThat(pack.resolve("skills/django-testing/SKILL.md")).exists()
        assertThat(root.resolve(".keel/.installing-django")).doesNotExist()
        // a second install of an installed pack is fine and leaves the project's copy alone
        Files.writeString(pack.resolve("stack.yml"), Files.readString(pack.resolve("stack.yml")) + "# edited\n")
        post("/api/projects/$pid/stacks/django/install").andExpect(status().isOk)
        assertThat(Files.readString(pack.resolve("stack.yml"))).endsWith("# edited\n")
        post("/api/projects/$pid/stacks/nope/install").andExpect(status().isNotFound)
    }
}
