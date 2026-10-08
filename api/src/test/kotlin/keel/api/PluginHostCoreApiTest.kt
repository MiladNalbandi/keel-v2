package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.hamcrest.Matchers.containsString
import org.hamcrest.Matchers.not
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.content
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** keel run without keel-start (dev, and the shared test context): no run/resolved.json, so no plugins and no restart. */
class PluginHostCoreApiTest : ApiTest() {
    @Test
    fun `without resolved json there are no plugins and features still work`() {
        val host = get("/api/plugin-host").andExpect(status().isOk).json()
        assertThat(host["sdk"].asInt()).isEqualTo(1)
        assertThat(host["mode"].asText()).isEqualTo("off")
        assertThat(host["plugins"].size()).isZero()
        assertThat(host["problems"].size()).isZero()

        val f = get("/api/features").andExpect(status().isOk).json()
        assertThat(f["mode"].asText()).isEqualTo("dev")
        assertThat(f["plugins"].isArray).isTrue()
        assertThat(f["plugins"].size()).isZero()
        listOf("/plugins/product/0.1.0-beta.1/web/index.js", "/plugins/x").forEach {
            get(it).andExpect(status().isNotFound).andExpect(content().string(not(containsString("web app is not built"))))
        }
    }

    @Test
    fun `without keel-start keel cannot restart itself`() {
        val res = post("/api/plugin-host/restart").andExpect(status().isConflict).json()
        assertThat(res["error"].asText()).contains("keel-start")
    }
}
