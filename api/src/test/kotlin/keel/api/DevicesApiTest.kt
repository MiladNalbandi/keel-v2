package keel.api

import com.fasterxml.jackson.databind.JsonNode
import keel.api.auth.AuthLimiter
import keel.api.auth.WebKey
import keel.api.common.Hash
import keel.api.devices.PairingCodes
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.http.MediaType
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.ResultActions
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete as rdelete
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get as rget
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post as rpost
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put as rput
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.net.URLDecoder

/** Phone access: pairing, device tokens, Settings › Access (devices/DevicesController, auth/LocalAuth). */
class DevicesApiTest : ApiTest() {
    @Autowired lateinit var key: WebKey
    @Autowired lateinit var codes: PairingCodes
    @Autowired lateinit var limiter: AuthLimiter
    @Autowired lateinit var jdbc: JdbcTemplate

    @BeforeEach
    fun fresh() {
        limiter.clear()
        codes.clear()
        jdbc.update("DELETE FROM devices")
        put("/api/devices/addresses", mapOf("urls" to emptyList<Any>())).andExpect(status().isOk)
    }

    private fun MockHttpServletRequestBuilder.body(v: Any) = contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsString(v))
    private fun MockHttpServletRequestBuilder.person() = header("Authorization", "Bearer ${key.value}")
    private fun MockHttpServletRequestBuilder.device(t: String) = header("Authorization", "Bearer $t")
    private fun MockHttpServletRequestBuilder.from(ip: String) = with { it.remoteAddr = ip; it }
    private fun ResultActions.body(): JsonNode = mapper.readTree(andReturn().response.getContentAsString(Charsets.UTF_8))

    private fun newCode(): JsonNode = bare.perform(rpost("/api/devices/pairing").person()).andExpect(status().isOk).body()

    private fun pair(code: String, ip: String = "127.0.0.1"): ResultActions =
        bare.perform(rpost("/api/devices/pair").from(ip).body(mapOf("code" to code, "name" to "Pixel 8", "platform" to "android")))

    private fun paired(): Pair<String, JsonNode> {
        val res = pair(newCode()["code"].asText()).andExpect(status().isOk).body()
        return res["token"].asText() to res["device"]
    }

    @Test
    fun `a person makes a pairing code, a phone pairs once with it and gets a device token`() {
        put("/api/devices/addresses", mapOf("urls" to listOf(
            mapOf("kind" to "wifi", "url" to "http://192.168.1.20:8088/"),
            mapOf("kind" to "tailscale", "url" to "https://mac.tail1234.ts.net"),
        ))).andExpect(status().isOk)
        val p = bare.perform(rpost("http://localhost:8088/api/devices/pairing").person()).andExpect(status().isOk).body()
        val code = p["code"].asText()
        assertThat(code).matches("[A-HJ-NP-Z2-9]{10}")
        assertThat(p["expiresAt"].asText()).endsWith("Z")
        // Tailscale first, then the Wi-Fi, then this page's own address
        assertThat(p["urls"].map { it["kind"].asText() + " " + it["url"].asText() })
            .containsExactly("tailscale https://mac.tail1234.ts.net", "wifi http://192.168.1.20:8088", "local http://localhost:8088")
        assertThat(p["reachable"].asBoolean()).isTrue()
        val qr = p["qr"].asText()
        assertThat(qr).startsWith("keel://pair?u=https%3A%2F%2Fmac.tail1234.ts.net&c=$code&n=")
        val params = qr.substringAfter('?').split('&').associate { it.substringBefore('=') to URLDecoder.decode(it.substringAfter('='), Charsets.UTF_8) }
        assertThat(params).containsEntry("u", "https://mac.tail1234.ts.net").containsEntry("c", code).containsKey("n")

        val res = pair(code).andExpect(status().isOk).body()
        assertThat(res.fieldNames().asSequence().toList()).containsExactly("token", "device", "keel")
        assertThat(res["token"].asText()).matches("kd_[0-9a-f]{64}")
        assertThat(res["device"].fieldNames().asSequence().toList()).containsExactly("id", "name", "platform", "createdAt")
        assertThat(res["device"]["name"].asText()).isEqualTo("Pixel 8")
        assertThat(res["device"]["platform"].asText()).isEqualTo("android")
        assertThat(res["keel"].fieldNames().asSequence().toList()).containsExactly("name", "version")

        // used once: the same code again is refused
        pair(code).andExpect(status().isForbidden).body().let {
            assertThat(it["error"].asText()).contains("wrong, used or expired")
            assertThat(it["hint"].asText()).contains("Settings › Access")
        }
    }

    @Test
    fun `a wrong or an expired code is refused`() {
        pair("WRONGCODE2").andExpect(status().isForbidden)
        bare.perform(rpost("/api/devices/pair").body(mapOf("name" to "x"))).andExpect(status().isForbidden)
        val (old, _) = codes.make(System.currentTimeMillis() - PairingCodes.TTL.toMillis() - 1000)
        pair(old).andExpect(status().isForbidden)
        // typed by hand: lower case and a dash still match
        val c = newCode()["code"].asText()
        pair(c.lowercase().chunked(5).joinToString("-")).andExpect(status().isOk)
    }

    @Test
    fun `five wrong codes a minute from one address, then even a right one waits (429)`() {
        repeat(5) { pair("WRONGCODE$it", ip = "203.0.113.7").andExpect(status().isForbidden) }
        val good = newCode()["code"].asText()
        pair(good, ip = "203.0.113.7").andExpect(status().isTooManyRequests)
        // another address is not held back; this computer itself never is
        pair(good, ip = "203.0.113.8").andExpect(status().isOk)
        repeat(6) { pair("WRONGCODE$it").andExpect(status().isForbidden) }
    }

    @Test
    fun `wrong sign-in keys from another address are limited too`() {
        repeat(20) {
            bare.perform(rget("/api/projects").from("203.0.113.9").header("Authorization", "Bearer wrong$it")).andExpect(status().isForbidden)
        }
        bare.perform(rget("/api/projects").from("203.0.113.9").person()).andExpect(status().isTooManyRequests)
        bare.perform(rget("/api/projects").from("203.0.113.10").person()).andExpect(status().isOk)
    }

    @Test
    fun `the device token works on a normal endpoint, without X-Keel-Web, and me says who it is`() {
        val (token, device) = paired()
        bare.perform(rget("/api/projects").device(token)).andExpect(status().isOk)
        bare.perform(rpost("/api/notifications/read-all").device(token)).andExpect(status().isOk)
        bare.perform(rget("/api/web/me").device(token)).andExpect(status().isOk).body().let { assertThat(it["via"].asText()).isEqualTo("device") }
        val me = bare.perform(rget("/api/devices/me").device(token)).andExpect(status().isOk).body()
        assertThat(me["device"]["id"].asText()).isEqualTo(device["id"].asText())
        assertThat(me["device"].fieldNames().asSequence().toList()).containsExactly("id", "name", "platform", "createdAt")
        assertThat(me["keel"]["version"].asText()).isNotBlank()
        // a person is not a device
        bare.perform(rget("/api/devices/me").person()).andExpect(status().isForbidden)
    }

    @Test
    fun `the device token works wherever the web key does - the inbox, approvals, flows, KeelBot, and the live stream`() {
        val (token, _) = paired()
        val (pid, _) = newProject("device-routes")
        listOf("/api/projects", "/api/inbox", "/api/inbox/count", "/api/projects/$pid/flows", "/api/projects/$pid/runs",
            "/api/workflows", "/api/projects/$pid/budget/now").forEach { path ->
            val viaKey = bare.perform(rget(path).person()).andReturn().response.status
            val viaDevice = bare.perform(rget(path).device(token)).andReturn().response.status
            assertThat(viaDevice).describedAs(path).isEqualTo(viaKey).isNotIn(401, 403)
        }
        // the live stream (SSE) starts for a device as for the browser
        bare.perform(rget("/api/events?project=$pid").device(token).header("Accept", "text/event-stream"))
            .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.request().asyncStarted())
        // a change without X-Keel-Web: the device is not a browser
        bare.perform(rpost("/api/inbox/x-1/act").device(token).body(mapOf("action" to "nope"))).andReturn().response.let {
            assertThat(it.status).isNotIn(401, 403)
        }
    }

    @Test
    fun `a device is never keel itself, an MCP server or the person managing devices`() {
        val (token, device) = paired()
        bare.perform(rget("/api/devices").device(token)).andExpect(status().isForbidden)
        bare.perform(rpost("/api/devices/pairing").device(token)).andExpect(status().isForbidden)
        bare.perform(rdelete("/api/devices/${device["id"].asText()}").device(token)).andExpect(status().isForbidden)
        bare.perform(rput("/api/devices/addresses").device(token).body(mapOf("urls" to emptyList<Any>()))).andExpect(status().isForbidden)
        bare.perform(rpost("/api/web/code").device(token)).andExpect(status().isForbidden)
        bare.perform(rpost("/internal/events").device(token).body(mapOf<String, Any>())).andExpect(status().isForbidden)
        // keel's own token (the engine, keel2) cannot manage devices either: only a person
        get("/api/devices").andExpect(status().isForbidden)
        post("/api/devices/pairing").andExpect(status().isForbidden)
    }

    @Test
    fun `a person lists and removes devices, and a removed device is refused`() {
        val (token, device) = paired()
        val list = bare.perform(rget("/api/devices").person()).andExpect(status().isOk).body()
        assertThat(list).hasSize(1)
        assertThat(list[0].fieldNames().asSequence().toList()).contains("id", "name", "platform", "createdAt", "lastSeenAt", "lastIp")
        assertThat(list[0]["lastIp"].asText()).isEqualTo("127.0.0.1")
        bare.perform(rdelete("/api/devices/${device["id"].asText()}").person()).andExpect(status().isOk)
        bare.perform(rdelete("/api/devices/${device["id"].asText()}").person()).andExpect(status().isNotFound)
        val gone = bare.perform(rget("/api/projects").device(token)).andExpect(status().isForbidden).body()
        assertThat(gone["error"].asText()).isEqualTo("This device was removed")
        assertThat(gone["hint"].asText()).isEqualTo("Pair it again in keel: Settings › Access")
        // an unknown token says the same
        bare.perform(rget("/api/projects").device("kd_" + "0".repeat(64))).andExpect(status().isForbidden).body().let {
            assertThat(it["error"].asText()).isEqualTo("This device was removed")
        }
    }

    @Test
    fun `only the token's hash is stored`() {
        val (token, device) = paired()
        val row = jdbc.queryForMap("SELECT * FROM devices WHERE id = ?", device["id"].asText())
        assertThat(row["token_hash"]).isEqualTo(Hash.sha256Hex(token))
        assertThat(row.values.map { it.toString() }).noneMatch { it.contains(token) || it.contains(token.removePrefix("kd_")) }
    }

    @Test
    fun `keel2's addresses are set by keel2 only, checked, and their hosts are keel's own`() {
        bare.perform(rput("/api/devices/addresses").person().body(mapOf("urls" to emptyList<Any>()))).andExpect(status().isForbidden)
        put("/api/devices/addresses", mapOf("urls" to listOf(mapOf("kind" to "moon", "url" to "http://x")))).andExpect(status().isBadRequest)
        put("/api/devices/addresses", mapOf("urls" to listOf(mapOf("kind" to "tunnel", "url" to "https://abc.trycloudflare.com/x")))).andExpect(status().isBadRequest)
        bare.perform(rget("http://abc.trycloudflare.com/api/health")).andExpect(status().isForbidden)
        put("/api/devices/addresses", mapOf("urls" to listOf(mapOf("kind" to "tunnel", "url" to "https://abc.trycloudflare.com")))).andExpect(status().isOk)
        bare.perform(rget("http://abc.trycloudflare.com/api/health")).andExpect(status().isOk)
        get("/api/devices/addresses").andExpect(status().isOk).body().let { assertThat(it["urls"][0]["kind"].asText()).isEqualTo("tunnel") }
        // a phone on the Wi-Fi calls keel by its address
        bare.perform(rget("http://192.168.1.20:8088/api/health")).andExpect(status().isOk)
        bare.perform(rget("http://evil.test/api/health")).andExpect(status().isForbidden)
    }

    @Test
    fun `only this page's own address when keel2 registered none`() {
        val p = bare.perform(rpost("http://localhost:8088/api/devices/pairing").person()).andExpect(status().isOk).body()
        assertThat(p["urls"].map { it["kind"].asText() }).containsExactly("local")
        assertThat(p["reachable"].asBoolean()).isFalse()
    }
}
