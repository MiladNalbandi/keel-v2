package keel.api

import com.sun.net.httpserver.HttpServer
import keel.api.events.EventHub
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.request
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.net.InetSocketAddress

/** v0.2: caps, skill import, models, per-flow cap, lanes, estimate of unsaved YAML, flow passthrough, SSE for all projects. */
class V02FlowApiTest : ApiTest() {

    @Autowired lateinit var hub: EventHub

    @Test
    fun `caps crud and the budget shows them with the settings cap`() {
        val (pid, _) = newProject("v2-caps")
        assertThat(get("/api/projects/$pid/caps").json().size()).isEqualTo(0)
        val cap = post("/api/projects/$pid/caps", mapOf("scope" to "day", "limit" to 200000, "unit" to "tokens", "action" to "pause"))
            .andExpect(status().isOk).json()
        val id = cap["id"].asText()
        assertThat(id).startsWith("cap-")
        assertThat(cap["limit"].asDouble()).isEqualTo(200000.0)

        val edited = put("/api/projects/$pid/caps/$id", mapOf("scope" to "api_month", "limit" to 25, "unit" to "usd", "action" to "stop")).andExpect(status().isOk).json()
        assertThat(edited["unit"].asText()).isEqualTo("usd")
        assertThat(edited["scope"].asText()).isEqualTo("api_month")

        post("/api/projects/$pid/caps", mapOf("scope" to "year", "limit" to 1, "unit" to "tokens", "action" to "pause")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/caps", mapOf("scope" to "day", "limit" to 0, "unit" to "tokens", "action" to "pause")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/caps", mapOf("scope" to "day", "limit" to 5, "unit" to "eur", "action" to "pause")).andExpect(status().isBadRequest)
        put("/api/projects/$pid/caps/cap-nope", mapOf("scope" to "day", "limit" to 5, "unit" to "tokens", "action" to "pause")).andExpect(status().isNotFound)

        val caps = get("/api/projects/$pid/budget").json()["caps"]
        assertThat(caps.map { it["id"].asText() }).containsExactly("settings", id)
        assertThat(caps[0]["limit"].asDouble()).isEqualTo(500000.0)
        assertThat(caps[0]["source"].asText()).isEqualTo("settings")
        assertThat(caps[1]["action"].asText()).isEqualTo("stop")

        delete("/api/projects/$pid/caps/$id").andExpect(status().isOk)
        delete("/api/projects/$pid/caps/$id").andExpect(status().isNotFound)
        assertThat(get("/api/projects/$pid/caps").json().size()).isEqualTo(0)
    }

    @Test
    fun `a skill can be imported from pasted text or a link`() {
        val (pid, _) = newProject("v2-skill-import")
        val pasted = post("/api/projects/$pid/skills/import", mapOf("body" to "---\nname: v2-scoring-rules\ndescription: How scores work.\n---\n\n# Scores\n"))
            .andExpect(status().isOk).json()
        assertThat(pasted["id"].asText()).isEqualTo("v2-scoring-rules")
        assertThat(pasted["source"].asText()).isEqualTo("yours")
        assertThat(pasted["about"].asText()).isEqualTo("How scores work.")

        post("/api/projects/$pid/skills/import", mapOf("body" to "# no front matter\n")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/skills/import", emptyMap<String, Any>()).andExpect(status().isBadRequest)
        post("/api/projects/$pid/skills/import", mapOf("url" to "ftp://example.com/SKILL.md")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/skills/import", mapOf("url" to "file:///etc/passwd")).andExpect(status().isBadRequest)

        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/skills/") { ex ->
            val body = when (ex.requestURI.path) {
                "/skills/payments/SKILL.md" -> "---\ndescription: Payment rules.\n---\n\n# Payments\n".toByteArray()
                "/skills/huge/SKILL.md" -> ("---\nname: huge\n---\n" + "x".repeat(300 * 1024)).toByteArray()
                else -> null
            }
            if (body == null) ex.sendResponseHeaders(404, -1) else {
                ex.sendResponseHeaders(200, body.size.toLong()); ex.responseBody.use { it.write(body) }
            }
            ex.close()
        }
        server.start()
        try {
            val base = "http://127.0.0.1:${server.address.port}"
            val linked = post("/api/projects/$pid/skills/import", mapOf("url" to "$base/skills/payments/SKILL.md")).andExpect(status().isOk).json()
            assertThat(linked["id"].asText()).isEqualTo("payments")
            assertThat(linked["about"].asText()).isEqualTo("Payment rules.")
            assertThat(get("/api/skills/payments?project=$pid").json()["body"].asText()).contains("# Payments")
            post("/api/projects/$pid/skills/import", mapOf("url" to "$base/skills/huge/SKILL.md")).andExpect(status().isBadRequest)
            post("/api/projects/$pid/skills/import", mapOf("url" to "$base/skills/missing/SKILL.md")).andExpect(status().isBadRequest)
        } finally {
            server.stop(0)
        }
    }

    @Test
    fun `provider models come from the engine`() {
        val m = get("/api/providers/models").andExpect(status().isOk).json()
        assertThat(m["fake"][0]["id"].asText()).isEqualTo("fake")
        assertThat(m["claude"][0]["label"].asText()).isEqualTo("Sonnet")
    }

    @Test
    fun `a flow can carry its own cap, agent lanes ride along as step metadata, and the flow view keeps blockers and ladder`() {
        val (pid, _) = newProject("v2-flow-cap")
        val agent = put("/api/projects/$pid/agents/implementer", mapOf("lane" to "web")).andExpect(status().isOk).json()
        assertThat(agent["lane"].asText()).isEqualTo("web")
        assertThat(agent["overridden"].map { it.asText() }).contains("lane")
        assertThat(get("/api/projects/$pid/agents").json().first { it["id"].asText() == "explorer" }["lane"].asText()).isEqualTo("follow")
        put("/api/projects/$pid/agents/implementer", mapOf("lane" to "mobile")).andExpect(status().isBadRequest)

        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Capped", "cap_tokens" to 90000, "on_cap" to "stop"))
            .andExpect(status().isOk)
        val body = engine.lastBody("/threads")!!
        assertThat(body["settings"]["cap_tokens"].asInt()).isEqualTo(90000)
        assertThat(body["settings"]["on_cap"].asText()).isEqualTo("stop")
        val steps = body["workflow"]["steps"].associateBy { it["id"].asText() }
        assertThat(steps["green"]!!["lane"].asText()).isEqualTo("web")
        assertThat(steps["spec"]!!.has("lane")).isFalse()
        // the project's settings did not change
        assertThat(get("/api/projects/$pid/settings").json()["effective"]["cap_tokens"].asInt()).isEqualTo(500000)

        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x", "on_cap" to "explode")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x", "cap_tokens" to 0)).andExpect(status().isBadRequest)

        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Plain")).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads")!!["settings"]["on_cap"].asText()).isEqualTo("pause")

        val flow = get("/api/projects/$pid/flow").andExpect(status().isOk).json()
        assertThat(flow["thread"]["blockers"][0]["gate"].asText()).isEqualTo("coverage")
        assertThat(flow["thread"]["blockers"][0]["fix"].asText()).isEqualTo("Add tests for Score.kt")
        assertThat(flow["thread"]["ladder"][0]["status"].asText()).isEqualTo("pass")
    }

    @Test
    fun `unsaved workflow yaml can be estimated`() {
        val (pid, _) = newProject("v2-estimate")
        val yaml = "name: Draft\nsteps:\n  - { id: a, kind: agent, name: a, agent: explorer }\n"
        val est = post("/api/projects/$pid/estimate", mapOf("yaml" to yaml, "acs" to listOf(mapOf("id" to "AC-1"), mapOf("id" to "AC-2"))))
            .andExpect(status().isOk).json()
        assertThat(est["tokens"].asInt()).isEqualTo(1000)
        val body = engine.lastBody("/workflows/estimate")!!
        assertThat(body["yaml"].asText()).isEqualTo(yaml)
        assertThat(body["acs"].asInt()).isEqualTo(2)
        assertThat(body["models"]["default"]["provider"].asText()).isEqualTo("fake")
        assertThat(body["history"].isArray).isTrue()

        post("/api/projects/$pid/estimate", mapOf("yaml" to yaml, "acs" to 5)).andExpect(status().isOk)
        assertThat(engine.lastBody("/workflows/estimate")!!["acs"].asInt()).isEqualTo(5)
        post("/api/projects/$pid/estimate", mapOf("yaml" to "")).andExpect(status().isBadRequest)
    }

    @Test
    fun `events for all projects carry only notifications and project changes`() {
        val all = mvc.perform(MockMvcRequestBuilders.get("/api/events?project=*")).andExpect(request().asyncStarted()).andReturn()
        val one = mvc.perform(MockMvcRequestBuilders.get("/api/events?project=sse-a")).andExpect(request().asyncStarted()).andReturn()
        hub.publish("sse-a", "agent.step", mapOf("text" to "step-a"))
        hub.publish("sse-a", "notification", mapOf("title" to "note-a"))
        hub.publish("sse-b", "project.changed", mapOf("id" to "sse-b"))
        hub.publish("sse-b", "notification", mapOf("title" to "note-b"))

        val text = all.response.contentAsString
        assertThat(text).contains("event:notification", "note-a", "note-b", "event:project.changed")
        assertThat(text).doesNotContain("step-a")
        val mine = one.response.contentAsString
        assertThat(mine).contains("step-a", "note-a").doesNotContain("note-b")
    }
}
