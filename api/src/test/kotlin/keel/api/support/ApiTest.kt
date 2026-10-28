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
    /** Calls keel as its engine does: with the internal token (support/SignedIn), unless a test sends its own. */
    @Autowired lateinit var mvc: MockMvc
    @Autowired lateinit var mapper: ObjectMapper
    @Autowired lateinit var wac: org.springframework.web.context.WebApplicationContext
    @Autowired lateinit var filters: List<jakarta.servlet.Filter>

    /** v0.18.1 the same app with no credential at all: a caller that never signed in (auth/LocalAuth). */
    val bare: MockMvc by lazy { SignedIn.bare(wac, filters) }

    fun get(url: String): ResultActions = mvc.perform(MockMvcRequestBuilders.get(url))
    fun post(url: String, body: Any? = null, headers: Map<String, String> = emptyMap()) = send(MockMvcRequestBuilders.post(url), body, headers)
    fun put(url: String, body: Any?, headers: Map<String, String> = emptyMap()) = send(MockMvcRequestBuilders.put(url), body, headers)
    fun delete(url: String, headers: Map<String, String> = emptyMap()): ResultActions = send(MockMvcRequestBuilders.delete(url), null, headers)

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
        /** v0.15.7 KEEL_FOLDERS in tests: two folders under this one ("main" and "extra"), and "missing", which is not there. */
        val foldersDir: Path = Files.createTempDirectory("keel-folders")
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
            r.add("keel.folders") { listOf("main", "extra", "missing").joinToString(":") { foldersDir.resolve(it).toString() } }
            r.add("keel.internal-token") { TOKEN }
            r.add("keel.secret") { "" }
            r.add("keel.fake-on-real-projects") { true }
            // B8a off: one test's model login must not change the next test's models (FirstModelLoginApiTest turns it on)
            r.add("keel.first-login-default") { false }
            // the stub engine answers one fixed state: a pause must not overwrite what a test saved (RunHistoryApiTest turns it on)
            r.add("keel.refresh-state-on-events") { false }
            r.add("keel.keel-v1-optional") { dataDir.resolve("no-keel-v1").toString() }
            // 0.18.66 the daily release check never runs by itself in tests, and never reaches GitHub (UpdatesApiTest
            // puts a stand-in source in)
            r.add("keel.release-check.scheduler") { false }
            r.add("keel.release-check-url") { "file:///nonexistent/keel-release.json" }
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
                    "echo 'Signed in successfully'; exec sleep 600"
            }
            // GitHub sign-in: a stand-in gh (it writes its arguments to gh.log) and no real GitHub
            val fakeGh = dataDir.resolve("fake-gh")
            Files.writeString(fakeGh, """#!/bin/sh
echo "${'$'}*" >> "${'$'}(dirname "${'$'}0")/gh.log"
case "${'$'}1 ${'$'}2" in
  "auth login")
    # like gh 2.102 in a container: the plain prompter needs GH_ACCESSIBLE_PROMPTER, else gh waits for terminal answers forever
    if [ "${'$'}GH_ACCESSIBLE_PROMPTER" != true ]; then exec sleep 600; fi
    case "${'$'}*" in
      *cpr.example.com*) printf '\033[6n'; reply=${'$'}(dd bs=1 count=6 2>/dev/null); case "${'$'}reply" in *"[1;1R"*) ;; *) exec sleep 600;; esac;;
      *hang.example.com*) exec sleep 600;;
    esac
    # exactly what gh 2.102's accessible prompter prints (square brackets, no newline)
    printf 'Authenticate Git with your GitHub credentials? (default: yes) [Y/n] '
    IFS= read -r answer
    if [ "${'$'}answer" != Y ]; then echo "did not authenticate git"; exit 1; fi
    echo "! First copy your one-time code: AB12-CD34"
    echo "Press Enter to open https://github.com/login/device in your browser..."
    IFS= read -r enter
    sleep 1;;
  "auth token") echo "gho_fakefakefakefakefakefakefakefake1234";;
  "auth status") echo "github.com"; echo "  ✓ Logged in to github.com account mili-dev (hosts.yml)"; echo "  - Token scopes: 'read:org', 'repo', 'workflow'";;
  "auth logout")
    # a gh that never answers, with a child of its own (like gh waiting on a browser helper); the service kills the whole tree
    case "${'$'}*" in *hang.example.com*) sleep 600 & wait;; esac
    echo "Logged out";;
esac
exit 0
""")
            fakeGh.toFile().setExecutable(true)
            r.add("keel.github-login.gh") { fakeGh.toString() }
            r.add("keel.login-commands.claude") {
                "echo 'Browse to https://claude.com/cai/oauth/authorize?code=true&client_id=x'; printf 'Paste code here if prompted> '; read code; " +
                    "if [ \"\$code\" = good-code ]; then echo 'Your OAuth token: sk-ant-oat01-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123'; else echo 'OAuth error: invalid code'; exit 1; fi"
            }
        }
    }
}
