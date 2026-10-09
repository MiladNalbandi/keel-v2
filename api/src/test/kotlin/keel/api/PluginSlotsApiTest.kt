package keel.api

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.connections.ConnectionField
import keel.api.connections.ConnectionKind
import keel.api.connections.SecretService
import keel.api.flow.FlowContributor
import keel.api.settings.SettingKey
import keel.api.settings.SettingsSection
import keel.api.settings.SettingsService
import keel.api.settings.SimpleSettingsSection
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.context.annotation.Bean
import org.springframework.http.MediaType
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.ResultActions
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.util.concurrent.ConcurrentHashMap

/**
 * Step 2 (docs/plugins/09-step2-contract.md §4) with a plugin's api part as step 3 will have it: a FlowContributor, a
 * settings section and a connection kind, all beans core does not know by name. Its own context and data folder.
 */
@SpringBootTest
@AutoConfigureMockMvc
class PluginSlotsApiTest {
    @Autowired lateinit var mvc: MockMvc
    @Autowired lateinit var mapper: ObjectMapper
    @Autowired lateinit var settings: SettingsService
    @Autowired lateinit var demo: DemoContributor
    @Autowired lateinit var secrets: SecretService

    /** Adds a key and a setting to the flows of the projects it is on for, and claims the "demo" templates. */
    class DemoContributor : FlowContributor {
        val on: MutableSet<String> = ConcurrentHashMap.newKeySet()
        override fun keys(pid: String): Map<String, String> = if (pid in on) mapOf("demo" to "demo-secret") else emptyMap()
        override fun settings(pid: String): Map<String, Any?> = if (pid in on) mapOf("demo_level" to 2) else emptyMap()
        override fun templateOn(pid: String, plugin: String): Boolean? = if (plugin == "demo") pid in on else null
    }

    @TestConfiguration
    class DemoPlugin {
        @Bean
        fun demoContributor() = DemoContributor()

        @Bean
        fun demoSettings(): SettingsSection =
            SimpleSettingsSection("demo", "Demo", listOf(SettingKey("plugins.demo.level", "int", 1)), order = 70)

        @Bean
        fun demoConnection(): ConnectionKind = object : ConnectionKind {
            override val kind = "demo"
            override val title = "Demo service"
            override val scope = "keel"
            override val order = 35
            override val fields = listOf(ConnectionField("token", "Token", "secret", required = true))
            override val tokens = setOf("DEMO_TOKEN")
        }
    }

    companion object {
        val dataDir = Files.createTempDirectory("keel-slots")

        @JvmStatic
        @DynamicPropertySource
        fun props(r: DynamicPropertyRegistry) {
            ApiTest.props(r)
            r.add("keel.data") { dataDir.toString() }
        }
    }

    private val engine get() = ApiTest.engine

    private fun get(url: String): ResultActions = mvc.perform(MockMvcRequestBuilders.get(url))
    private fun send(b: org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder, body: Any) =
        mvc.perform(b.contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsString(body)))
    private fun post(url: String, body: Any) = send(MockMvcRequestBuilders.post(url), body)
    private fun put(url: String, body: Any) = send(MockMvcRequestBuilders.put(url), body)
    private fun ResultActions.json(): JsonNode = mapper.readTree(andReturn().response.getContentAsString(Charsets.UTF_8))

    private fun git(root: java.nio.file.Path, vararg args: String) {
        val p = ProcessBuilder(listOf("git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", *args))
            .directory(root.toFile()).redirectErrorStream(true).start()
        p.inputStream.readAllBytes()
        check(p.waitFor() == 0) { "git ${args.joinToString(" ")} failed" }
    }

    private fun newProject(name: String): String {
        val root = Files.createDirectories(Files.createTempDirectory("keel-proj").resolve(name))
        git(root, "init", "-q", "-b", "main")
        Files.writeString(root.resolve("README.md"), "# $name\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "first commit")
        return post("/api/projects", mapOf("root" to root.toString())).andExpect(status().isOk).json()["id"].asText()
    }

    private fun start(pid: String, tid: String): JsonNode {
        engine.nextThreadIds.add(tid)
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Demo $tid", "allow_fake" to true, "allow_dirty" to true))
            .andExpect(status().isOk)
        return engine.lastBody("/threads")!!
    }

    @Test
    fun `a contributor's keys and settings reach the engine with a start and a resume, only where it is on`() {
        val on = newProject("slots-demo-on")
        val off = newProject("slots-demo-off")
        demo.on += on

        val body = start(on, "t-slots-on")
        assertThat(body["keys"]["demo"].asText()).isEqualTo("demo-secret")
        assertThat(body["settings"]["demo_level"].asInt()).isEqualTo(2)
        // core's Plugins part still sends its list; contributed settings come before push_pr and branch_pattern
        assertThat(body["settings"].has("plugins")).isTrue()
        assertThat(body["settings"].fieldNames().asSequence().toList().takeLast(2)).containsExactly("push_pr", "branch_pattern")
        assertThat(body["settings"].toString()).doesNotContain("demo-secret")

        post("/api/threads/t-slots-on/resume", mapOf("decision" to "approve")).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads/t-slots-on/resume")!!["keys"]["demo"].asText()).isEqualTo("demo-secret")

        val other = start(off, "t-slots-off")
        assertThat(other["keys"]?.has("demo") ?: false).isFalse()
        assertThat(other["settings"].has("demo_level")).isFalse()
    }

    @Test
    fun `a plugin's templates show only where its contributor says so, and a template nobody claims stays hidden`() {
        val on = newProject("slots-templates-on")
        val off = newProject("slots-templates-off")
        demo.on += on
        val added = listOf(
            mapOf("id" to "demo-flow", "name" to "demo flow", "plugin" to "demo", "keel_rules" to false, "version" to 1, "steps" to emptyList<Any>()),
            mapOf("id" to "orphan-flow", "name" to "orphan flow", "plugin" to "nobody", "keel_rules" to false, "version" to 1, "steps" to emptyList<Any>()),
        )
        engine.extraTemplates += added
        try {
            val shown = get("/api/projects/$on/workflows").andExpect(status().isOk).json().map { it["id"].asText() }
            assertThat(shown).contains("feature", "demo-flow").doesNotContain("orphan-flow", "ci-fix")
            val hidden = get("/api/projects/$off/workflows").json().map { it["id"].asText() }
            assertThat(hidden).contains("feature").doesNotContain("demo-flow", "orphan-flow")
        } finally {
            engine.extraTemplates -= added.toSet()
        }
    }

    @Test
    fun `the plugin's settings section and connection kind are listed, and its section gives the defaults`() {
        val sections = get("/api/settings/sections").andExpect(status().isOk).json()
        assertThat(sections.map { it["id"].asText() }.last()).isEqualTo("demo")
        assertThat(sections.last()["keys"][0]["key"].asText()).isEqualTo("plugins.demo.level")
        val kinds = get("/api/connections/kinds").andExpect(status().isOk).json().map { it["kind"].asText() }
        assertThat(kinds).containsExactly("github", "demo")          // GitLab and Databases come with their plugins

        val pid = newProject("slots-demo-settings")
        val level = settings.plugin("demo")
        assertThat(level.get("level", pid)).isEqualTo(1)                       // nobody set it: the section's default
        put("/api/settings/general", mapOf("plugins.demo.level" to 3)).andExpect(status().isOk)
        assertThat(level.get("level", pid)).isEqualTo(3)
        put("/api/projects/$pid/settings", mapOf("plugins.demo.level" to 4)).andExpect(status().isOk)
        val view = get("/api/projects/$pid/settings/plugins/demo").json()
        assertThat(view["effective"]["level"].asInt()).isEqualTo(4)
        assertThat(view["general"]["level"].asInt()).isEqualTo(3)
        assertThat(get("/api/settings/plugins/demo").json()["effective"]["level"].asInt()).isEqualTo(3)
        put("/api/settings/general", mapOf("plugins.demo.level" to null)).andExpect(status().isOk)
        assertThat(level.get("level")).isEqualTo(1)
    }

    @Test
    fun `a token the plugin's connection kind names is saved without whitespace, like keel's own`() {
        put("/api/secrets/DEMO_TOKEN", mapOf("value" to "demo-first\n   second-half")).andExpect(status().isOk)
        assertThat(secrets.get("DEMO_TOKEN")).isEqualTo("demo-firstsecond-half")
        // any other secret keeps its value as it is
        put("/api/secrets/DEMO_NOTE", mapOf("value" to "two words")).andExpect(status().isOk)
        assertThat(secrets.get("DEMO_NOTE")).isEqualTo("two words")
    }
}
