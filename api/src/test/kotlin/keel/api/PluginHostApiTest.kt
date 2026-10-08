package keel.api

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import keel.api.pluginhost.ApiExit
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.hamcrest.Matchers.not
import org.hamcrest.Matchers.containsString
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Primary
import org.springframework.core.io.support.PathMatchingResourcePatternResolver
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.ResultActions
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.content
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.header
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.net.URI
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Step 1's plugin host (docs/plugins/07-step1-contract.md, section 6) with the run/resolved.json keel-start would
 * write: "demo" (web files and migrations), "demo-notes" (migrations only, a name with a dash), one plugin with a bad
 * name and one problem from the resolver. keel-start runs this api (KEEL_SUPERVISED=1); exiting is faked.
 */
@SpringBootTest
@AutoConfigureMockMvc
class PluginHostApiTest {
    @Autowired lateinit var mvc: MockMvc
    @Autowired lateinit var mapper: ObjectMapper
    @Autowired lateinit var jdbc: JdbcTemplate
    @Autowired lateinit var exits: RecordingExit

    /** Records the exit instead of ending the test JVM. */
    class RecordingExit : ApiExit {
        data class Call(val code: Int, val at: Long, val daemon: Boolean)
        val calls = CopyOnWriteArrayList<Call>()
        override fun exit(code: Int) {
            calls += Call(code, System.nanoTime(), Thread.currentThread().isDaemon)
        }
    }

    @TestConfiguration
    class FakeExit {
        @Bean
        @Primary
        fun recordingExit() = RecordingExit()
    }

    companion object {
        val dataDir: Path = Files.createTempDirectory("keel-plugins")
        val demo: Path = fixture("demo")
        val notes: Path = fixture("demo-notes")

        private fun fixture(name: String): Path = Paths.get(PluginHostApiTest::class.java.getResource("/pluginhost/$name")!!.toURI())

        init {
            val resolved = mapOf(
                "sdk" to 1, "keel" to "0.15.0", "mode" to "on", "resolved_at" to "2026-10-08T18:00:00Z", "from_a_newer_resolver" to true,
                "plugins" to listOf(
                    mapOf(
                        "name" to "demo", "title" to "Demo", "version" to "1.0.0", "source" to "file", "dir" to demo.toString(),
                        "engine" to null, "api" to null, "web" to mapOf("entry" to "web/index.js", "css" to listOf("web/style.css")),
                        "content" to null, "migrations" to demo.resolve("migrations").toString(),
                        "requires" to mapOf("sdk" to 1, "keel" to ">=0.15.0,<1.0.0"), "signed" to false,
                    ),
                    mapOf(
                        "name" to "demo-notes", "title" to "Demo notes", "version" to "0.2.0-beta.1", "source" to "image", "dir" to notes.toString(),
                        "engine" to mapOf("path" to notes.resolve("engine").toString(), "package" to "demo_notes"), "api" to null, "web" to null,
                        "content" to null, "migrations" to "migrations", "requires" to mapOf("sdk" to 1, "plugins" to mapOf("demo" to ">=1.0.0")),
                    ),
                    mapOf("name" to "Bad_Name", "title" to "Bad", "version" to "1.0.0", "source" to "file", "dir" to "/data/plugins/store/bad/1.0.0"),
                ),
                "problems" to listOf(mapOf("name" to "x", "version" to "1.0.0", "dir" to "/data/plugins/store/x/1.0.0", "error" to "needs plugin SDK 2, this keel has 1")),
            )
            val run = Files.createDirectories(dataDir.resolve("plugins").resolve("run"))
            Files.writeString(run.resolve("resolved.json"), jacksonObjectMapper().writeValueAsString(resolved))
        }

        @JvmStatic
        @DynamicPropertySource
        fun props(r: DynamicPropertyRegistry) {
            ApiTest.props(r)
            r.add("keel.data") { dataDir.toString() }
            r.add("keel.supervised") { "1" }
        }
    }

    private fun get(url: String): ResultActions = mvc.perform(MockMvcRequestBuilders.get(url))
    private fun ResultActions.json(): JsonNode = mapper.readTree(andReturn().response.getContentAsString(Charsets.UTF_8))

    private fun tables(): List<String> = jdbc.queryForList("SELECT name FROM sqlite_master WHERE type = 'table'", String::class.java)

    @Test
    fun `the plugin host lists the plugins that loaded, their parts, and the ones left out`() {
        val host = get("/api/plugin-host").andExpect(status().isOk).json()
        assertThat(host["sdk"].asInt()).isEqualTo(1)
        assertThat(host["mode"].asText()).isEqualTo("on")
        assertThat(host["plugins"].map { it["name"].asText() + " " + it["version"].asText() }).containsExactly("demo 1.0.0", "demo-notes 0.2.0-beta.1")
        val first = host["plugins"][0]
        assertThat(first["title"].asText()).isEqualTo("Demo")
        assertThat(first["source"].asText()).isEqualTo("file")
        assertThat(first["parts"].map { it.asText() }).containsExactly("web", "migrations")
        assertThat(host["plugins"][1]["parts"].map { it.asText() }).containsExactly("engine", "migrations")
        assertThat(first.fieldNames().asSequence().toList()).containsExactlyInAnyOrder("name", "title", "version", "source", "parts")
        assertThat(host["problems"].map { it["name"].asText() + ": " + it["error"].asText() })
            .containsExactly("x: needs plugin SDK 2, this keel has 1", "Bad_Name: its name is not a valid plugin name")
        assertThat(host["problems"][0]["dir"].asText()).isEqualTo("/data/plugins/store/x/1.0.0")
    }

    @Test
    fun `features list every plugin with absolute web urls and keep the add-on fields`() {
        val f = get("/api/features").andExpect(status().isOk).json()
        assertThat(f["mode"].asText()).isEqualTo("dev")
        assertThat(f["modes"].map { it.asText() }).containsExactly("dev")
        assertThat(f["parts"]["dev"].asBoolean()).isTrue()
        assertThat(f["addons"].size()).isZero()
        assertThat(f["screens"].size()).isZero()
        assertThat(f["plugins"].map { it["name"].asText() }).containsExactly("demo", "demo-notes")
        val demo = f["plugins"][0]
        assertThat(demo["title"].asText()).isEqualTo("Demo")
        assertThat(demo["version"].asText()).isEqualTo("1.0.0")
        assertThat(demo["web"]["entry"].asText()).isEqualTo("/plugins/demo/1.0.0/web/index.js")
        assertThat(demo["web"]["css"].map { it.asText() }).containsExactly("/plugins/demo/1.0.0/web/style.css")
        assertThat(f["plugins"][1].has("web")).isTrue()
        assertThat(f["plugins"][1]["web"].isNull).isTrue()
    }

    @Test
    fun `a plugin's web files are served for a year with their content type`() {
        get("/plugins/demo/1.0.0/web/index.js").andExpect(status().isOk)
            .andExpect(header().string("Cache-Control", "public, max-age=31536000, immutable"))
            .andExpect(content().contentType("text/javascript"))
            .andExpect(content().string(Files.readString(demo.resolve("web/index.js"))))
        get("/plugins/demo/1.0.0/web/style.css").andExpect(status().isOk)
            .andExpect(header().string("Cache-Control", "public, max-age=31536000, immutable"))
            .andExpect(content().contentType("text/css"))
            .andExpect(content().string(containsString(".demo")))
    }

    @Test
    fun `unknown plugins, other versions, missing files and paths out of the web folder are 404s, never the web app`() {
        val missing = listOf(
            "/plugins/nope/1.0.0/web/index.js",
            "/plugins/demo/2.0.0/web/index.js",
            "/plugins/demo/1.0.0/web/missing.js",
            "/plugins/demo/1.0.0/web/",
            "/plugins/demo/1.0.0/web/../keel-plugin.yml",
            "/plugins/demo/1.0.0/web/../../demo/keel-plugin.yml",
            "/plugins/demo/1.0.0/keel-plugin.yml",
            "/plugins/demo-notes/0.2.0-beta.1/web/index.js",
            "/plugins/Bad_Name/1.0.0/web/index.js",
            "/plugins/demo",
            "/plugins/x",
            "/plugins",
        )
        missing.forEach { url ->
            get(url).andExpect(status().isNotFound).andExpect(content().string(not(containsString("web app is not built"))))
        }
        mvc.perform(MockMvcRequestBuilders.get(URI.create("/plugins/demo/1.0.0/web/%2e%2e/keel-plugin.yml"))).andExpect(status().isNotFound)
        // the web app still answers its own pages
        get("/projects/x/flow").andExpect(status().isOk).andExpect(content().string(containsString("web app is not built")))
    }

    @Test
    fun `plugin migrations run after keel's, each in its own history, and keel's history does not change`() {
        assertThat(tables()).contains("demo_items", "demo_schema_history", "demo_notes", "demo-notes_schema_history", "flyway_schema_history")
        assertThat(jdbc.queryForObject("SELECT title FROM demo_items WHERE id = 'first'", String::class.java)).isEqualTo("The first demo item")
        val demoHistory = jdbc.queryForList("SELECT version, script, success FROM demo_schema_history ORDER BY installed_rank")
        assertThat(demoHistory.map { "${it["version"]} ${it["script"]} ${it["success"]}" }).containsExactly("0 << Flyway Baseline >> 1", "1 V1__init.sql 1")
        val core = PathMatchingResourcePatternResolver().getResources("classpath:db/migration/*.sql").map { it.filename }
        val coreHistory = jdbc.queryForList("SELECT script FROM flyway_schema_history", String::class.java)
        assertThat(coreHistory).containsExactlyInAnyOrderElementsOf(core)
        assertThat(jdbc.queryForList("SELECT script FROM \"demo-notes_schema_history\" WHERE success = 1", String::class.java)).contains("V1__notes.sql")
    }

    @Test
    fun `under keel-start a restart answers 202 and then exits with 75`() {
        val asked = System.nanoTime()
        val res = mvc.perform(MockMvcRequestBuilders.post("/api/plugin-host/restart")).andExpect(status().isAccepted).json()
        assertThat(res["restarting"].asBoolean()).isTrue()
        val deadline = System.nanoTime() + 5_000_000_000
        while (exits.calls.isEmpty() && System.nanoTime() < deadline) Thread.sleep(20)
        assertThat(exits.calls.map { it.code }).containsExactly(75)
        assertThat((exits.calls[0].at - asked) / 1_000_000).isGreaterThanOrEqualTo(450)
        // a daemon thread lets the JVM end with code 0 once Spring has stopped, before the exit with 75 (a real race)
        assertThat(exits.calls[0].daemon).isFalse()
    }
}
