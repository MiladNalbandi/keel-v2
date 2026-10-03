package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

class SettingsApiTest : ApiTest() {

    @Test
    fun `first boot uses the fake model so nothing needs a login`() {
        val g = get("/api/settings/general").andExpect(status().isOk).json()
        assertThat(g["default_model"]["provider"].asText()).isEqualTo("fake")
        assertThat(g["gates_mode"].asText()).isEqualTo("every-ac")
        assertThat(g["mcp"].map { it.asText() }).containsExactly("keel")
    }

    @Test
    fun `project overrides win, null goes back to general, general changes flow through`() {
        val (pid, _) = newProject("settings-demo")

        val first = put("/api/projects/$pid/settings", mapOf("gates_mode" to "end", "cap_tokens" to 300000)).andExpect(status().isOk).json()
        assertThat(first["overrides"].fieldNames().asSequence().toList()).containsExactlyInAnyOrder("gates_mode", "cap_tokens")
        assertThat(first["effective"]["gates_mode"].asText()).isEqualTo("end")
        assertThat(first["effective"]["cap_tokens"].asInt()).isEqualTo(300000)
        assertThat(first["general"]["gates_mode"].asText()).isEqualTo("every-ac")

        // General changes reach the project for keys it does not override.
        put("/api/settings/general", mapOf("on_cap" to "cheaper", "gates_mode" to "end-of-lane")).andExpect(status().isOk)
        val second = get("/api/projects/$pid/settings").json()
        assertThat(second["effective"]["on_cap"].asText()).isEqualTo("cheaper")
        assertThat(second["effective"]["gates_mode"].asText()).isEqualTo("end")

        // null = use general again
        val third = put("/api/projects/$pid/settings", mapOf("gates_mode" to null)).json()
        assertThat(third["overrides"].has("gates_mode")).isFalse()
        assertThat(third["effective"]["gates_mode"].asText()).isEqualTo("end-of-lane")

        put("/api/settings/general", mapOf("on_cap" to "pause", "gates_mode" to "every-ac")).andExpect(status().isOk)
    }

    @Test
    fun `unknown keys and bad choices are refused with a hint`() {
        val (pid, _) = newProject("settings-bad")
        val e1 = put("/api/projects/$pid/settings", mapOf("colour" to "red")).andExpect(status().isBadRequest).json()
        assertThat(e1["error"].asText()).contains("colour")
        assertThat(e1["hint"].asText()).contains("gates_mode")
        put("/api/settings/general", mapOf("gates_mode" to "sometimes")).andExpect(status().isBadRequest)
        put("/api/settings/general", mapOf("cap_tokens" to "lots")).andExpect(status().isBadRequest)
    }

    @Test
    fun `models can be overridden per project`() {
        val (pid, _) = newProject("settings-model")
        val model = mapOf("provider" to "claude", "mode" to "subscription", "model" to "opus")
        val r = put("/api/projects/$pid/settings", mapOf("implementer_model" to model)).json()
        assertThat(r["effective"]["implementer_model"]["model"].asText()).isEqualTo("opus")
        val agents = get("/api/projects/$pid/agents").json()
        val implementer = agents.first { it["id"].asText() == "implementer" }
        assertThat(implementer["model"]["provider"].asText()).isEqualTo("claude")
        val explorer = agents.first { it["id"].asText() == "explorer" }
        assertThat(explorer["model"]["provider"].asText()).isEqualTo("fake")
    }
}
