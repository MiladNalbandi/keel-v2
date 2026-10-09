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

/** Shared Spring context: temp data dir, stub engine and fixture content (KEEL_CONTENT). */
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
        val github: StubGitHub = StubGitHub.start()
        val contentDir: Path = Paths.get(ApiTest::class.java.getResource("/content-fixture")!!.toURI())
        const val TOKEN = "test-token"

        @JvmStatic
        @DynamicPropertySource
        fun props(r: DynamicPropertyRegistry) {
            r.add("keel.data") { dataDir.toString() }
            r.add("keel.engine-url") { engine.url }
            r.add("keel.content") { contentDir.toString() }
            r.add("keel.workspace") { "" }
            r.add("keel.internal-token") { TOKEN }
            r.add("keel.secret") { "" }
            r.add("keel.fake-on-real-projects") { true }
            r.add("keel.keel-v1-optional") { dataDir.resolve("no-keel-v1").toString() }
            // v0.5.0 tasks (the Tasks plugin's keel.tasks.*, for the plugins' tests and Product's): effects on the event
            // thread, no background polls, GitHub = the stub, no token from the environment
            r.add("keel.tasks.inline-effects") { true }
            r.add("keel.tasks.scheduler") { false }
            r.add("keel.tasks.github-api") { github.url }
            r.add("keel.tasks.github-from-env") { false }
            r.add("keel.tasks.public-url") { "http://keel.test" }
            // Login helpers: no pseudo-terminal in tests, and stand-in CLIs that behave like the real ones.
            r.add("keel.login-pty") { false }
            r.add("keel.login-commands.codex") {
                "echo 'Open this link: https://auth.openai.com/codex/device'; echo 'Enter this one-time code: AB12-CD345'; sleep 1; " +
                    "mkdir -p \"\$CODEX_HOME\"; echo '{\"tokens\":{\"access_token\":\"codex-from-device\"}}' > \"\$CODEX_HOME/auth.json\""
            }
            r.add("keel.login-commands.copilot") {
                "echo 'To authenticate, visit https://github.com/login/device and enter code 32B2-75E8'; sleep 1; " +
                    // like the real CLI 1.0.94 in a container: no keychain, so it asks before it writes the token, then stays open
                    "printf 'System keychain unavailable. Store token in plaintext config file? (y/N) '; read answer; " +
                    "if [ \"\$answer\" != y ]; then echo 'Login succeeded, but the token was not saved.'; exit 1; fi; " +
                    "mkdir -p \"\$HOME/.copilot\"; echo '{\"token\":\"gho_abcdefghijklmnopqrstuvwxyz123456\"}' > \"\$HOME/.copilot/config.json\"; " +
                    "echo 'Signed in successfully'; sleep 600"
            }
            r.add("keel.login-commands.claude") {
                "echo 'Browse to https://claude.com/cai/oauth/authorize?code=true&client_id=x'; printf 'Paste code here if prompted> '; read code; " +
                    "if [ \"\$code\" = good-code ]; then echo 'Your OAuth token: sk-ant-oat01-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123'; else echo 'OAuth error: invalid code'; exit 1; fi"
            }
        }
    }
}
