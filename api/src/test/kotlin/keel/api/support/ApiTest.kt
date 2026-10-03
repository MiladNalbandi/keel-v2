package keel.api.support

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.http.MediaType
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.ResultActions
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.util.concurrent.TimeUnit

/** Shared Spring context: temp data dir, stub engine, fixture KEEL_HOME. */
@SpringBootTest
@AutoConfigureMockMvc
abstract class ApiTest {
    @Autowired lateinit var mvc: MockMvc
    @Autowired lateinit var mapper: ObjectMapper

    fun get(url: String): ResultActions = mvc.perform(MockMvcRequestBuilders.get(url))
    fun post(url: String, body: Any? = null, headers: Map<String, String> = emptyMap()) = send(MockMvcRequestBuilders.post(url), body, headers)
    fun put(url: String, body: Any?) = send(MockMvcRequestBuilders.put(url), body)
    fun delete(url: String): ResultActions = mvc.perform(MockMvcRequestBuilders.delete(url))

    private fun send(b: MockHttpServletRequestBuilder, body: Any?, headers: Map<String, String> = emptyMap()): ResultActions {
        headers.forEach { (k, v) -> b.header(k, v) }
        if (body != null) b.contentType(MediaType.APPLICATION_JSON).content(if (body is String) body else mapper.writeValueAsString(body))
        return mvc.perform(b)
    }

    fun ResultActions.json(): JsonNode = mapper.readTree(andReturn().response.getContentAsString(Charsets.UTF_8))

    /** Makes a git repo with one commit on main and registers it. Returns the project id. */
    fun newProject(name: String, files: Map<String, String> = mapOf("README.md" to "# demo\n")): Pair<String, Path> {
        val root = Files.createTempDirectory("keel-proj").resolve(name)
        Files.createDirectories(root)
        git(root, "init", "-q", "-b", "main")
        files.forEach { (rel, text) ->
            val f = root.resolve(rel)
            Files.createDirectories(f.parent)
            Files.writeString(f, text)
        }
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "first commit")
        val res = post("/api/projects", mapOf("root" to root.toString())).json()
        return res.get("id").asText() to root
    }

    fun git(root: Path, vararg args: String): String = gitEnv(root, emptyMap(), *args)

    fun gitEnv(root: Path, env: Map<String, String>, vararg args: String): String {
        val pb = ProcessBuilder(listOf("git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", *args))
            .directory(root.toFile()).redirectErrorStream(true)
        pb.environment().putAll(env)
        val p = pb.start()
        val out = p.inputStream.readAllBytes().toString(Charsets.UTF_8)
        p.waitFor(20, TimeUnit.SECONDS)
        check(p.exitValue() == 0) { "git ${args.joinToString(" ")} failed: $out" }
        return out
    }

    companion object {
        val dataDir: Path = Files.createTempDirectory("keel-data")
        val engine: StubEngine = StubEngine.start()
        val keelHome: Path = Paths.get(ApiTest::class.java.getResource("/keel-home")!!.toURI())
        const val TOKEN = "test-token"
        val dashboardPort: Int = java.net.ServerSocket(0).use { it.localPort }

        @JvmStatic
        @DynamicPropertySource
        fun props(r: DynamicPropertyRegistry) {
            r.add("keel.data") { dataDir.toString() }
            r.add("keel.engine-url") { engine.url }
            r.add("keel.home") { keelHome.toString() }
            r.add("keel.projects-file") { "" }
            r.add("keel.workspace") { "" }
            r.add("keel.internal-token") { TOKEN }
            r.add("keel.secret") { "" }
            r.add("keel.dashboard-port") { dashboardPort }
            r.add("keel.fake-on-real-projects") { true }
        }
    }
}
