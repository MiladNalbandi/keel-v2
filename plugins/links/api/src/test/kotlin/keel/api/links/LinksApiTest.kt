package keel.api.links

import com.sun.net.httpserver.HttpServer
import keel.api.connections.SecretService
import keel.api.flow.FlowService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.net.InetSocketAddress

/** The Linear key: checked before it is kept, never shown, and handed on only for a project with the plugin on. */
class LinksApiTest : ApiTest() {
    @Autowired lateinit var secrets: SecretService
    @Autowired lateinit var links: LinksService
    @Autowired lateinit var keys: LinksKeys
    @Autowired lateinit var flows: FlowService

    private lateinit var linear: HttpServer
    private var answer = 200 to """{"data":{"viewer":{"name":"Ann"}}}"""
    private var sawKey: String? = null

    @BeforeEach
    fun start() {
        links.forget()
        linear = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        linear.createContext("/graphql") { x ->
            sawKey = x.requestHeaders.getFirst("Authorization")
            x.requestBody.readAllBytes()
            val b = answer.second.toByteArray()
            x.sendResponseHeaders(answer.first, b.size.toLong())
            x.responseBody.use { it.write(b) }
        }
        linear.start()
    }

    @AfterEach
    fun stop() {
        linear.stop(0)
        links.forget()
    }

    private val url get() = "http://127.0.0.1:${linear.address.port}/graphql"
    private fun save(token: String = "lin_api_SECRETKEY", u: String = url) = post("/api/plugins/links/linear", mapOf("token" to token, "url" to u))

    @Test
    fun `not linked at first, and the page says what can go to the model company`() {
        val v = get("/api/plugins/links/linear").andExpect(status().isOk).json()
        assertThat(v["linked"].asBoolean()).isFalse()
        assertThat(v["sends"].asText()).contains("model company").contains("after your OK")
    }

    @Test
    fun `a key Linear takes is kept and never comes back`() {
        val raw = save().andExpect(status().isOk).andReturn().response.contentAsString
        assertThat(raw).doesNotContain("SECRETKEY")
        assertThat(sawKey).isEqualTo("lin_api_SECRETKEY")
        val v = get("/api/plugins/links/linear").json()
        assertThat(v["linked"].asBoolean()).isTrue()
        assertThat(v["who"].asText()).isEqualTo("Ann")
        assertThat(v.toString()).doesNotContain("SECRETKEY")
        assertThat(secrets.has(LinksService.TOKEN)).isTrue()
    }

    @Test
    fun `a key Linear refuses is not kept`() {
        answer = 401 to "{}"
        val res = save().andExpect(status().isBadRequest).json()
        assertThat(res["error"].asText()).contains("refused")
        assertThat(secrets.has(LinksService.TOKEN)).isFalse()
        answer = 200 to """{"errors":[{"message":"no"}]}"""
        save().andExpect(status().isBadRequest)
        assertThat(secrets.has(LinksService.TOKEN)).isFalse()
    }

    @Test
    fun `the key goes only over https or to this computer, and an empty one is refused`() {
        save(u = "http://example.com/graphql").andExpect(status().isBadRequest)
        save(u = "not an address").andExpect(status().isBadRequest)
        save(token = " ").andExpect(status().isBadRequest)
        assertThat(sawKey).isNull()
    }

    @Test
    fun `forgetting removes the key, and the engine gets it only for a project with the plugin on`() {
        val (pid, _) = newProject("links-keys")
        save().andExpect(status().isOk)
        assertThat(keys.keys(pid)).isEmpty()         // the plugin is off here (it is not in the stub's catalog)
        assertThat(links.keys()).containsEntry("links:linear:token", "lin_api_SECRETKEY").containsEntry("links:linear:url", url)
        delete("/api/plugins/links/linear").andExpect(status().isOk)
        assertThat(links.keys()).isEmpty()
        assertThat(get("/api/plugins/links/linear").json()["linked"].asBoolean()).isFalse()
    }
}
