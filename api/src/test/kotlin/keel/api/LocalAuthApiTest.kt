package keel.api

import jakarta.servlet.http.Cookie
import keel.api.auth.LocalAuth
import keel.api.auth.WebKey
import keel.api.auth.WebSignInController
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.beans.factory.annotation.Qualifier
import org.springframework.http.MediaType
import org.springframework.test.web.servlet.MvcResult
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get as rget
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.options
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post as rpost
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.request
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import org.springframework.web.bind.annotation.RequestMethod
import org.springframework.web.servlet.mvc.method.annotation.RequestMappingHandlerMapping
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermission

/** v0.18.1 keel's local api proves every caller (auth/LocalAuth, docs/plans/agent-clients.md section 0). */
class LocalAuthApiTest : ApiTest() {
    @Autowired lateinit var key: WebKey
    @Autowired @Qualifier("requestMappingHandlerMapping") lateinit var mappings: RequestMappingHandlerMapping

    private fun signInCookie(): Cookie {
        val code = post("/api/web/code").andExpect(status().isOk).json()["code"].asText()
        val res = bare.perform(rget("/web/login").param("code", code)).andExpect(status().isFound).andReturn()
        assertThat(res.response.getHeader("Location")).isEqualTo("/")
        return res.response.getCookie(key.cookieName)!!
    }

    private fun MockHttpServletRequestBuilder.json(body: String = "{}") = contentType(MediaType.APPLICATION_JSON).content(body)

    /** Every route of the api and of the plugins in this build, as (method, path with each {variable} filled in). */
    private fun routes(): List<Pair<RequestMethod, String>> = mappings.handlerMethods.keys.flatMap { info ->
        val paths = info.pathPatternsCondition?.patternValues ?: info.patternValues
        val methods = info.methodsCondition.methods.ifEmpty { setOf(RequestMethod.GET) }
        paths.flatMap { p -> methods.map { m -> m to p.replace(Regex("\\{\\*?[^}]+}"), "x") } }
    }.filter { (_, p) -> p.startsWith("/api/") || p.startsWith("/internal/") }.distinct()

    @Test
    fun `every route refuses a caller that never signed in, and says how to sign in`() {
        val all = routes()
        assertThat(all.size).isGreaterThan(150)
        // open: health, and a phone's pairing (its one-time code is the credential: DevicesApiTest)
        val open = all.filter { (m, p) -> (m == RequestMethod.GET && p == "/api/health") || (m == RequestMethod.POST && p == "/api/devices/pair") }
        assertThat(open).hasSize(2)
        val wrong = (all - open.toSet()).mapNotNull { (m, p) ->
            val res = bare.perform(request(org.springframework.http.HttpMethod.valueOf(m.name), p).json()).andReturn().response
            if (res.status == 401 && res.getHeader(LocalAuth.AUTH_HEADER) == "sign-in" && res.contentAsString.contains("keel2 open")) null
            else "$m $p -> ${res.status}"
        }
        assertThat(wrong).isEmpty()
    }

    @Test
    fun `a path written in another form (semicolon, percent, double slash) is still an api path and needs a credential`() {
        val sneaky = listOf("/api;x=1/projects", "/%61pi/projects", "/api/%70rojects", "//api/projects", "/api//projects",
            "/api/./projects", "/api%2fprojects", "/%2e/api/projects", "/api/projects;jsessionid=1", "/internal;x/anything", "//internal/anything")
        val open = sneaky.mapNotNull { p ->
            val res = bare.perform(rget(java.net.URI.create(p))).andReturn().response   // a URI keeps the raw path
            if (res.status == 401 || res.status == 400 || res.status == 403 || res.status == 404) null else "GET $p -> ${res.status}"
        }
        assertThat(open).isEmpty()
        // the plain path still answers a signed-in caller
        mvc.perform(rget("/api/projects")).andExpect(status().isOk)
    }

    @Test
    fun `health, the web app's files and the sign-in link need nothing`() {
        bare.perform(rget("/api/health")).andExpect(status().isOk)
        bare.perform(rget("/")).andExpect(status().isOk)
        bare.perform(rget("/web/login")).andExpect(status().isFound).andReturn().response.getHeader("Location").let {
            assertThat(it).isEqualTo("/?signin=expired")
        }
    }

    @Test
    fun `keel only answers on its own host names`() {
        bare.perform(rget("http://evil.test/api/health")).andExpect(status().isForbidden)
        bare.perform(rget("http://evil.test/")).andExpect(status().isForbidden)
        mvc.perform(rget("http://evil.test/api/projects")).andExpect(status().isForbidden)   // a token does not help either
        listOf("http://localhost:8088", "http://127.0.0.1:8080", "http://[::1]:8080", "http://keel.localhost").forEach {
            bare.perform(rget("$it/api/health")).andExpect(status().isOk)
        }
    }

    @Test
    fun `a one-time code signs a browser in once, with a strict cookie named after this keel`() {
        val code = post("/api/web/code").andExpect(status().isOk).json()
        assertThat(code["path"].asText()).startsWith("/web/login?code=")
        assertThat(code["expires_in"].asLong()).isEqualTo(60)
        val res = bare.perform(rget(code["path"].asText())).andExpect(status().isFound).andReturn()
        val set = res.response.getHeader("Set-Cookie")!!
        assertThat(set).startsWith(key.cookieName + "=").contains("HttpOnly").contains("SameSite=Strict").contains("Path=/").contains("Max-Age=")
        assertThat(key.cookieName).matches("keel_[0-9a-f]{8}")
        // the same code again: no cookie, and the page says the link is used up
        val again = bare.perform(rget(code["path"].asText())).andExpect(status().isFound).andReturn()
        assertThat(again.response.getHeader("Set-Cookie")).isNull()
        assertThat(again.response.getHeader("Location")).isEqualTo("/?signin=expired")
    }

    @Test
    fun `only keel itself can make a code, a signed-in browser cannot`() {
        bare.perform(rpost("/api/web/code")).andExpect(status().isUnauthorized)
        val c = signInCookie()
        bare.perform(rpost("/api/web/code").cookie(c).header("X-Keel-Web", "1")).andExpect(status().isForbidden)
    }

    @Test
    fun `the browser reads with its cookie and changes things only from keel's own page`() {
        val c = signInCookie()
        bare.perform(rget("/api/projects").cookie(c)).andExpect(status().isOk)
        bare.perform(rget("/api/web/me").cookie(c)).andExpect(status().isOk).andReturn().let {
            assertThat(it.response.contentAsString).contains("\"via\":\"browser\"")
        }
        // a form or a fetch from another page cannot add X-Keel-Web
        bare.perform(rpost("/api/notifications/read-all").cookie(c)).andExpect(status().isForbidden)
        bare.perform(rpost("/api/notifications/read-all").cookie(c).header("X-Keel-Web", "1")).andExpect(status().isOk)
        bare.perform(rpost("/api/notifications/read-all").cookie(c).header("X-Keel-Web", "1").header("Origin", "http://localhost"))
            .andExpect(status().isOk)
        // another local site (same "site" for SameSite, another origin)
        bare.perform(rpost("/api/notifications/read-all").cookie(c).header("X-Keel-Web", "1").header("Origin", "http://localhost:3000"))
            .andExpect(status().isForbidden)
        // a cookie from another keel (another key) is not this keel's
        bare.perform(rget("/api/projects").cookie(Cookie(key.cookieName, "0".repeat(64)))).andExpect(status().isUnauthorized)
    }

    @Test
    fun `keel never answers a CORS preflight`() {
        val res: MvcResult = bare.perform(options("/api/notifications/read-all").header("Origin", "http://localhost:3000")
            .header("Access-Control-Request-Method", "POST").header("Access-Control-Request-Headers", "x-keel-web")).andReturn()
        assertThat(res.response.status).isNotEqualTo(200)
        assertThat(res.response.headerNames.filter { it.startsWith("Access-Control-") }).isEmpty()
    }

    @Test
    fun `a person's terminal app signs in with the web key`() {
        bare.perform(rpost("/api/notifications/read-all").header("Authorization", "Bearer ${key.value}")).andExpect(status().isOk)
        bare.perform(rpost("/api/notifications/read-all").header("Authorization", "Bearer wrong")).andExpect(status().isForbidden)
    }

    @Test
    fun `a cookie or the web key never lifts an MCP call above its level`() {
        val read = post("/api/mcp/token", mapOf("level" to "read")).json()["token"].asText()
        val c = signInCookie()
        bare.perform(rpost("/api/threads/t-stub-1/stop").header("X-Keel-Mcp-Token", read)).andExpect(status().isForbidden)
        bare.perform(rpost("/api/threads/t-stub-1/stop").header("X-Keel-Mcp-Token", read).cookie(c).header("X-Keel-Web", "1"))
            .andExpect(status().isForbidden)
        bare.perform(rpost("/api/threads/t-stub-1/stop").header("X-Keel-Mcp-Token", read).header("Authorization", "Bearer ${key.value}"))
            .andExpect(status().isForbidden)
        bare.perform(rget("/api/projects").header("X-Keel-Mcp-Token", read)).andExpect(status().isOk)
        bare.perform(rget("/api/projects").header("X-Keel-Mcp-Token", "made-up")).andExpect(status().isForbidden)
    }

    @Test
    fun `MCP tokens stay the same while the internal token does (an api restart keeps clients working)`() {
        val a = post("/api/mcp/token", mapOf("level" to "write")).json()["token"].asText()
        val b = keel.api.mcp.McpTokens(keel.api.common.KeelProperties(internalToken = TOKEN)).tokenFor("write")
        assertThat(b).isEqualTo(a)
        assertThat(keel.api.mcp.McpTokens(keel.api.common.KeelProperties(internalToken = "another")).tokenFor("write")).isNotEqualTo(a)
    }

    @Test
    fun `signing out clears this browser's cookie`() {
        val c = signInCookie()
        val res = bare.perform(rpost("/api/web/sign-out").cookie(c).header("X-Keel-Web", "1")).andExpect(status().isOk).andReturn()
        assertThat(res.response.getHeader("Set-Cookie")).startsWith(key.cookieName + "=;").contains("Max-Age=0")
    }

    @Test
    fun `0_18_64 the cookie is this browser's own session, never the web key, and sign-out ends it`() {
        val c = signInCookie()
        assertThat(c.value).startsWith("ks_").isNotEqualTo(key.value)
        val other = signInCookie()
        bare.perform(rpost("/api/web/sign-out").cookie(c).header("X-Keel-Web", "1")).andExpect(status().isOk)
        // the signed-out cookie no longer works, even if a copy of it was kept; the other browser stays signed in
        bare.perform(rget("/api/projects").cookie(c)).andExpect(status().isUnauthorized)
        bare.perform(rget("/api/projects").cookie(other)).andExpect(status().isOk)
        // sign out everywhere: every browser's session ends; the terminal app's web key still works
        val third = signInCookie()
        val res = bare.perform(rpost("/api/web/sign-out-everywhere").cookie(third).header("X-Keel-Web", "1")).andExpect(status().isOk).json()
        assertThat(res["ended"].asInt()).isGreaterThanOrEqualTo(2)
        bare.perform(rget("/api/projects").cookie(other)).andExpect(status().isUnauthorized)
        bare.perform(rget("/api/projects").cookie(third)).andExpect(status().isUnauthorized)
        bare.perform(rget("/api/projects").header("Authorization", "Bearer ${key.value}")).andExpect(status().isOk)
    }

    @Test
    fun `0_18_64 a session ends 30 days after its last use, and each use moves the end`(@Autowired sessions: keel.api.auth.WebSessions) {
        val day = 24L * 3600 * 1000
        val t0 = 1_800_000_000_000L
        val id = sessions.open(t0)
        assertThat(sessions.check(id, t0 + 20 * day)).isEqualTo(keel.api.auth.WebSessions.Check.MOVED)
        assertThat(sessions.check(id, t0 + 20 * day + 1000)).isEqualTo(keel.api.auth.WebSessions.Check.YES)
        // 45 days after it was opened, but 25 after its last use: still signed in
        assertThat(sessions.check(id, t0 + 45 * day)).isNotEqualTo(keel.api.auth.WebSessions.Check.NO)
        // 31 days without a use: gone
        assertThat(sessions.check(id, t0 + 76 * day + 1000)).isEqualTo(keel.api.auth.WebSessions.Check.NO)
        assertThat(sessions.check("ks_" + "0".repeat(64), t0)).isEqualTo(keel.api.auth.WebSessions.Check.NO)
    }

    @Test
    fun `0_18_64 over https the cookie is a Secure __Host- cookie`() {
        val code = post("/api/web/code").andExpect(status().isOk).json()["code"].asText()
        val res = bare.perform(rget("/web/login").param("code", code).secure(true)).andExpect(status().isFound).andReturn()
        val set = res.response.getHeader("Set-Cookie")!!
        assertThat(set).startsWith("__Host-${key.cookieName}=ks_").contains("Secure").contains("HttpOnly").contains("Path=/")
            .contains("Max-Age=${30L * 24 * 3600}").doesNotContain("Domain=")
        val c = res.response.getCookie("__Host-${key.cookieName}")!!
        bare.perform(rget("/api/projects").secure(true).cookie(c)).andExpect(status().isOk)
    }

    @Test
    fun `0_18_64 a cookie from before (the web key itself) is swapped for a session once`() {
        val old = Cookie(key.cookieName, key.value)
        val res = bare.perform(rget("/api/projects").cookie(old)).andExpect(status().isOk).andReturn()
        assertThat(res.response.getHeader("Set-Cookie")).startsWith(key.cookieName + "=ks_")
    }

    @Test
    fun `the web key is kept in a file only its owner reads`() {
        assertThat(key.value).hasSize(64)
        assertThat(Files.readString(key.file).trim()).isEqualTo(key.value)
        runCatching { Files.getPosixFilePermissions(key.file) }.getOrNull()?.let {
            assertThat(it).containsExactlyInAnyOrder(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE)
        }
    }

    @Test
    fun `after signing in, keel only goes to one of its own pages`() {
        assertThat(WebSignInController.safeNext("/flow?x=1")).isEqualTo("/flow?x=1")
        listOf(null, "", "//evil.test", "https://evil.test", "/\\evil", "flow",
            "/%09/evil.test", "/%0a/evil.test", "/%2F/evil.test", "/%252F/evil.test", "/%5Cevil.test", "/\t/evil.test").forEach {
            assertThat(WebSignInController.safeNext(it)).describedAs(it).isEqualTo("/")
        }
        assertThat(WebSignInController.safeNext("/flow?q=a%20b")).isEqualTo("/flow?q=a%20b")
    }

    @Test
    fun `a key file is created for its owner only`() {
        val dir = java.nio.file.Files.createTempDirectory("keel-own")
        val f = dir.resolve("k")
        keel.api.common.OwnerOnly.create(f, "secret".toByteArray())
        assertThat(java.nio.file.Files.readString(f)).isEqualTo("secret")
        if (f.fileSystem.supportedFileAttributeViews().contains("posix")) {
            assertThat(java.nio.file.attribute.PosixFilePermissions.toString(java.nio.file.Files.getPosixFilePermissions(f))).isEqualTo("rw-------")
        }
        org.assertj.core.api.Assertions.assertThatThrownBy { keel.api.common.OwnerOnly.create(f, "x".toByteArray()) }
            .isInstanceOf(java.nio.file.FileAlreadyExistsException::class.java)
    }
}
