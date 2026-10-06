package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.nio.file.Path

/** The workspace Doctor: explains uncommitted files and cleans the tree without losing work. */
class WorkspaceDoctorApiTest : ApiTest() {

    @AfterEach
    fun reset() {
        engine.askAnswer = null
        put("/api/settings/general", mapOf("default_model" to mapOf("provider" to "fake", "mode" to "api", "model" to "fake"))).andExpect(status().isOk)
    }

    private fun sh(root: Path, vararg a: String): String =
        ProcessBuilder("git", *a).directory(root.toFile()).redirectErrorStream(true).start().inputStream.bufferedReader().readText()

    /** A repo dirty like ludus-engine was: setup files, a doc, code in progress, a secret and build output. */
    private fun dirtyProject(name: String): Pair<String, Path> {
        val (pid, root) = newProject(name, mapOf("README.md" to "# x\n", ".gitignore" to "*.tmp\n", ".keel/config.yml" to "a: 1\n", "src/App.kt" to "fun main() {}\n"))
        Files.writeString(root.resolve(".gitignore"), "*.tmp\n.keel/tools.json\n")
        Files.writeString(root.resolve(".keel/config.yml"), "a: 2\n")
        Files.createDirectories(root.resolve(".devcontainer")); Files.writeString(root.resolve(".devcontainer/devcontainer.json"), "{}\n")
        Files.createDirectories(root.resolve(".serena")); Files.writeString(root.resolve(".serena/project.yml"), "name: x\n")
        Files.createDirectories(root.resolve("docs/knowledge")); Files.writeString(root.resolve("docs/knowledge/journeys.md"), "# Journeys\n")
        Files.writeString(root.resolve("src/App.kt"), "fun main() { println(1) }\n")
        Files.writeString(root.resolve(".env"), "API_KEY=super-secret-value\n")
        Files.createDirectories(root.resolve("build")); Files.writeString(root.resolve("build/out.txt"), "x\n")
        return pid to root
    }

    @Test
    fun `the rules group the files and propose a safe action for each group`() {
        val (pid, _) = dirtyProject("doc-rules")
        val d = post("/api/projects/$pid/doctor/workspace").andExpect(status().isOk).json()
        assertThat(d["by"].asText()).isEqualTo("rules")
        val plan = d["plan"].associate { it["id"].asText() to (it["action"].asText() to it["files"].map { f -> f.asText() }) }
        assertThat(plan["tooling"]!!.first).isEqualTo("commit")
        assertThat(plan["tooling"]!!.second).contains(".gitignore", ".keel/config.yml", ".devcontainer/devcontainer.json")
        assertThat(plan["tooling"]!!.second).doesNotContain(".serena/project.yml")        // a tool's own folder, not team setup
        assertThat(plan["docs"]).isEqualTo("commit" to listOf("docs/knowledge/journeys.md"))
        assertThat(plan["code"]).isEqualTo("stash" to listOf("src/App.kt"))
        assertThat(plan["secret"]).isEqualTo("ignore" to listOf(".env"))
        // Build output and tool folders are hidden on this computer only (.git/info/exclude), not in the project's .gitignore.
        assertThat(plan["local"]!!.first).isEqualTo("exclude")
        assertThat(d["plan"].first { it["id"].asText() == "local" }["patterns"].map { it.asText() }).containsExactlyInAnyOrder("build/", ".serena/")
    }

    @Test
    fun `applying the plan leaves a clean tree, keeps every change and never commits the secret`() {
        val (pid, root) = dirtyProject("doc-apply")
        val d = post("/api/projects/$pid/doctor/workspace").json()
        val plan = d["plan"].map { mapOf("action" to it["action"].asText(), "files" to it["files"].map { f -> f.asText() },
            "message" to it["message"]?.asText(), "patterns" to it["patterns"]?.map { p -> p.asText() }, "title" to it["title"].asText()) }
        val res = post("/api/projects/$pid/doctor/workspace/apply", mapOf("plan" to plan)).andExpect(status().isOk).json()
        assertThat(res["clean"].asBoolean()).isTrue()
        assertThat(res["results"].all { it["ok"].asBoolean() }).isTrue()
        val log = sh(root, "log", "--format=%s")
        assertThat(log).contains("chore: project tooling", "docs: journeys")
        assertThat(sh(root, "show", "HEAD:.gitignore")).contains(".env").doesNotContain("build/")   // only the secret goes to .gitignore
        assertThat(Files.readString(root.resolve(".git/info/exclude"))).contains("build/", ".serena/")   // tool folders: this computer only
        assertThat(sh(root, "log", "--all", "--name-only", "--format=")).doesNotContain(".env\n")
        assertThat(Files.readString(root.resolve(".gitignore"))).contains(".env")
        assertThat(Files.readString(root.resolve(".env"))).contains("super-secret-value")      // still on disk
        assertThat(sh(root, "stash", "list")).contains("keel doctor")
        sh(root, "stash", "pop")
        assertThat(Files.readString(root.resolve("src/App.kt"))).contains("println(1)")         // work is back
    }

    @Test
    fun `a secret in a commit is refused, and unknown files are refused`() {
        val (pid, _) = dirtyProject("doc-refuse")
        post("/api/projects/$pid/doctor/workspace/apply", mapOf("plan" to listOf(mapOf("action" to "commit", "files" to listOf(".env"), "message" to "x"))))
            .andExpect(status().isConflict)
        post("/api/projects/$pid/doctor/workspace/apply", mapOf("plan" to listOf(mapOf("action" to "stash", "files" to listOf("not/there.kt")))))
            .andExpect(status().isBadRequest)
    }

    @Test
    fun `with a real model the doctor asks it, never shows it the secret, and checks its answer`() {
        val (pid, _) = dirtyProject("doc-model")
        put("/api/settings/general", mapOf("default_model" to mapOf("provider" to "claude", "mode" to "subscription", "model" to "sonnet"))).andExpect(status().isOk)
        engine.askAnswer = mapOf("ok" to true, "fake" to false, "tokens_in" to 900, "tokens_out" to 120, "text" to """
            Here is the plan: {"summary": "You set up tooling and wrote a doc.", "plan": [
              {"title": "Tooling", "why": "Team setup.", "action": "commit", "files": [".gitignore", ".keel/config.yml", ".devcontainer/devcontainer.json", ".serena/project.yml", ".env"], "message": "chore: add dev tooling"},
              {"title": "Bad", "why": "x", "action": "delete", "files": ["src/App.kt"]}
            ]}""".trimIndent())
        val d = post("/api/projects/$pid/doctor/workspace").andExpect(status().isOk).json()
        assertThat(d["by"].asText()).isEqualTo("claude · sonnet")
        assertThat(d["summary"].asText()).isEqualTo("You set up tooling and wrote a doc.")
        val tooling = d["plan"].first { it["title"].asText() == "Tooling" }
        assertThat(tooling["files"].map { it.asText() }).doesNotContain(".env")                  // secret dropped from the commit
        assertThat(d["plan"].none { it["action"].asText() == "delete" }).isTrue()                  // unknown action dropped
        val all = d["plan"].flatMap { it["files"].map { f -> f.asText() } }
        assertThat(all).contains(".env", "src/App.kt", "docs/knowledge/journeys.md", "build/out.txt") // the rules cover the rest
        val prompt = engine.lastBody("/agents/ask")!!["prompt"].asText()
        assertThat(prompt).doesNotContain("super-secret-value").contains(".env").contains("println(1)")
        assertThat(engine.lastBody("/agents/ask")!!["model"]["provider"].asText()).isEqualTo("claude")
    }
}
