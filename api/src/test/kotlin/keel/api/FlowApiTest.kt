package keel.api

import keel.api.common.KeelProperties
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.content
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.net.ServerSocket

class FlowApiTest : ApiTest() {

    @Test
    fun `starting a flow sends the engine a full StartThread and remembers the thread`() {
        val (pid, root) = newProject("flow-start")
        put("/api/projects/$pid/settings", mapOf("cap_tokens" to 200000)).andExpect(status().isOk)
        put("/api/projects/$pid/mcp-allow", mapOf("implementer" to listOf("mcp:keel:keel_next"))).andExpect(status().isOk)
        put("/api/projects/$pid/skills/kotlin-spring-testing", mapOf("agents" to listOf("test-author"))).andExpect(status().isOk)

        val state = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Scores", "acs" to listOf(mapOf("id" to "AC-001", "layer" to "API", "title" to "Save a score"))))
            .andExpect(status().isOk).json()
        assertThat(state["thread_id"].asText()).isEqualTo("t-stub-1")

        val body = engine.lastBody("/threads")!!
        assertThat(body["project_id"].asText()).isEqualTo(pid)
        assertThat(body["root"].asText()).isEqualTo(root.toString())
        assertThat(body["title"].asText()).isEqualTo("Scores")
        assertThat(body["workflow"]["id"].asText()).isEqualTo("feature")
        assertThat(body["models"]["default"]["provider"].asText()).isEqualTo("fake")
        assertThat(body["models"]["implementer"]["model"].asText()).isEqualTo("fake")
        assertThat(body["settings"]["cap_tokens"].asInt()).isEqualTo(200000)
        assertThat(body["settings"]["gates_mode"].asText()).isEqualTo("every-ac")
        assertThat(body["mcp"].map { it["name"].asText() }).containsExactly("keel")
        assertThat(body["mcp"][0]["args"][0].asText()).endsWith("mcp/server.js")
        assertThat(body["skills"]["test-author"].asText()).contains("Kotlin + Spring Boot test patterns")
        assertThat(body["acs"][0]["id"].asText()).isEqualTo("AC-001")
        val green = body["workflow"]["steps"].first { it["id"].asText() == "green" }
        assertThat(green["tools"].map { it.asText() }).containsExactly("mcp:keel:keel_next")
        assertThat(green["per_ac"].asBoolean()).isTrue()

        val flow = get("/api/projects/$pid/flow").json()
        assertThat(flow["thread"]["thread_id"].asText()).isEqualTo("t-stub-1")
        assertThat(flow["workflow"]["id"].asText()).isEqualTo("feature")

        val resumed = post("/api/threads/t-stub-1/resume", mapOf("decision" to "approve")).andExpect(status().isOk).json()
        assertThat(resumed["status"].asText()).isEqualTo("running")
        post("/api/threads/t-stub-1/resume", mapOf("decision" to "maybe")).andExpect(status().isBadRequest)
        assertThat(post("/api/threads/t-stub-1/stop").json()["status"].asText()).isEqualTo("stopped")
        assertThat(get("/api/threads/t-stub-1/history").json()[0]["id"].asText()).isEqualTo("c1")

        val est = get("/api/projects/$pid/estimate?workflow_id=feature&acs=3").andExpect(status().isOk).json()
        assertThat(est["tokens"].asInt()).isEqualTo(1000)
        assertThat(engine.lastBody("/workflows/estimate")!!["acs"].asInt()).isEqualTo(3)
    }

    @Test
    fun `agents come from KEEL_HOME with phases, and projects can override and add their own`() {
        val (pid, _) = newProject("flow-agents")
        val agents = get("/api/projects/$pid/agents").json()
        val impl = agents.first { it["id"].asText() == "implementer" }
        assertThat(impl["phases"].map { it.asText() }).containsExactly("green")
        assertThat(impl["tools"].map { it.asText() }).contains("Write")
        assertThat(impl["prompt"].asText()).contains("failing test pass")
        assertThat(impl["custom"].asBoolean()).isFalse()
        assertThat(agents.first { it["id"].asText() == "explorer" }["phases"].map { it.asText() }).containsExactly("spec", "contract")

        val o = put("/api/projects/$pid/agents/explorer", mapOf("enabled" to false, "prompt" to "Short.")).andExpect(status().isOk).json()
        assertThat(o["overridden"].map { it.asText() }).containsExactlyInAnyOrder("prompt", "enabled")
        assertThat(o["enabled"].asBoolean()).isFalse()
        // a disabled agent in the workflow stops the start
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x")).andExpect(status().isBadRequest)
        put("/api/projects/$pid/agents/explorer", mapOf("enabled" to null, "prompt" to null)).andExpect(status().isOk)

        val custom = post("/api/projects/$pid/agents", mapOf("label" to "lit-check", "about" to "Checks the spec reads well", "prompt" to "Read the spec.")).andExpect(status().isOk).json()
        assertThat(custom["id"].asText()).isEqualTo("lit-check")
        assertThat(custom["custom"].asBoolean()).isTrue()
        post("/api/projects/$pid/agents", mapOf("label" to "implementer")).andExpect(status().isConflict)
        delete("/api/projects/$pid/agents/implementer").andExpect(status().isConflict)
        delete("/api/projects/$pid/agents/lit-check").andExpect(status().isOk)

        val test = post("/api/agents/implementer/test", mapOf("pid" to pid)).andExpect(status().isOk).json()
        assertThat(test["ok"].asBoolean()).isTrue()
        assertThat(engine.lastBody("/providers/test")!!["provider"].asText()).isEqualTo("fake")
    }

    @Test
    fun `skills, stacks and mcp servers`() {
        val (pid, _) = newProject("flow-catalog", mapOf("build.gradle.kts" to "", "src/main/kotlin/A.kt" to "class A"))
        val skills = get("/api/projects/$pid/skills").json()
        val testing = skills.first { it["id"].asText() == "kotlin-spring-testing" }
        assertThat(testing["kind"].asText()).isEqualTo("testing")
        assertThat(testing["source"].asText()).isEqualTo("keel")
        assertThat(testing["stack"].asText()).isEqualTo("kotlin-spring")
        assertThat(testing["agents"].map { it.asText() }).containsExactly("test-author")
        val detail = get("/api/skills/kotlin-spring-testing").json()
        assertThat(detail["refs"][0]["path"].asText()).isEqualTo("references/slices.md")
        assertThat(detail["refs"][0]["tokens"].asInt()).isGreaterThan(100)

        val mine = post("/api/projects/$pid/skills", mapOf("name" to "Scoring rules", "kind" to "knowledge", "stack" to "any", "body" to "---\nname: scoring-rules\ndescription: How scores work.\n---\n\n# Scores\n"))
            .andExpect(status().isOk).json()
        assertThat(mine["source"].asText()).isEqualTo("yours")
        put("/api/projects/$pid/skills/${mine["id"].asText()}", mapOf("agents" to listOf("implementer"), "when" to "green")).andExpect(status().isOk)
        val impl = get("/api/projects/$pid/agents").json().first { it["id"].asText() == "implementer" }
        assertThat(impl["skills"].map { it.asText() }).contains(mine["id"].asText())
        put("/api/projects/$pid/skills/feature", mapOf("body" to "nope")).andExpect(status().isConflict)

        val stacks = get("/api/projects/$pid/stacks").json()
        val ks = stacks.first { it["name"].asText() == "kotlin-spring" }
        assertThat(ks["detected"].asBoolean()).isTrue()
        assertThat(ks["commands"][0]["name"].asText()).isEqualTo("api_test_ac")
        assertThat(ks["tools"][0]["fail"].asText()).isEqualTo("block")
        val dj = stacks.first { it["name"].asText() == "django" }
        assertThat(dj["detected"].asBoolean()).isFalse()
        assertThat(dj["source"].asText()).isEqualTo("keel pack")

        val servers = get("/api/mcp-servers").json()
        val keel = servers.first { it["name"].asText() == "keel" }
        assertThat(keel["builtin"].asBoolean()).isTrue()
        assertThat(keel["command"].asText()).isEqualTo("node")
        delete("/api/mcp-servers/keel").andExpect(status().isConflict)
        post("/api/mcp-servers", mapOf("name" to "github", "command" to "npx", "args" to listOf("-y", "github-mcp"))).andExpect(status().isOk)
        post("/api/mcp-servers", mapOf("name" to "github", "command" to "npx")).andExpect(status().isConflict)
        val tested = post("/api/mcp-servers/github/test").andExpect(status().isOk).json()
        assertThat(tested["ok"].asBoolean()).isTrue()
        val gh = get("/api/mcp-servers").json().first { it["name"].asText() == "github" }
        assertThat(gh["status"].asText()).isEqualTo("ok")
        assertThat(gh["tools"].map { it.asText() }).containsExactly("keel_next")
        put("/api/mcp-servers/github", mapOf("enabled" to false)).andExpect(status().isOk)
        delete("/api/mcp-servers/github").andExpect(status().isOk)
    }

    @Test
    fun `health, limits, spa fallback and json 404s`() {
        val h = get("/api/health").andExpect(status().isOk).json()
        assertThat(h["engine"].asBoolean()).isTrue()
        assertThat(h["fake"].asBoolean()).isTrue()
        assertThat(h["keel"]["version"].asText()).isEqualTo("0.0.1-test")

        val limits = get("/api/limits").json()
        assertThat(limits.map { it["id"].asText() }).contains("claude", "copilot", "api")
        val edited = mapper.convertValue(limits, List::class.java).map { @Suppress("UNCHECKED_CAST") (it as Map<String, Any?>).toMutableMap() }
        edited.first { it["id"] == "copilot" }["cap"] = 500
        assertThat(put("/api/limits", edited).json().first { it["id"].asText() == "copilot" }["cap"].asInt()).isEqualTo(500)

        // "/" is forwarded to index.html by Spring's welcome page; MockMvc does not follow forwards.
        get("/index.html").andExpect(status().isOk).andExpect(content().string(org.hamcrest.Matchers.containsString("web app is not built")))
            .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.header().string("Cache-Control", "no-cache"))
        get("/projects/x/flow").andExpect(status().isOk).andExpect(content().string(org.hamcrest.Matchers.containsString("web app is not built")))
        get("/assets/missing.js").andExpect(status().isNotFound)
        val nf = get("/api/does-not-exist").andExpect(status().isNotFound).json()
        assertThat(nf["error"].asText()).isEqualTo("Not found")
        post("/api/projects", mapOf("root" to "/no/such/folder")).andExpect(status().isBadRequest)
    }

    @Test
    fun `the engine being down is a 503 with a hint`() {
        val port = ServerSocket(0).use { it.localPort }
        val client = EngineClient(KeelProperties(engineUrl = "http://127.0.0.1:$port"), mapper)
        assertThatThrownBy { client.templates() }.isInstanceOf(EngineDown::class.java)
        assertThat(client.health()).isNull()
    }
}
