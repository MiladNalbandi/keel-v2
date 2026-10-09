package keel.api

import keel.api.flow.ThreadSettings
import keel.api.settings.Model
import keel.api.settings.Settings
import keel.api.settings.SettingsService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * Step 2 (docs/plugins/09-step2-contract.md §4) with keel's own parts only: the flow payload the engine gets is the same
 * as before FlowContributor, today's connection kinds and settings sections are listed, and a plugin's own settings
 * live under `plugins.<name>.<key>` while core's keys keep their checks.
 */
class CoreSlotsApiTest : ApiTest() {
    @Autowired lateinit var settings: SettingsService

    /** ThreadSettings as it was before step 2: the Plugins part's list was a field of its own. */
    data class ThreadSettingsBefore(
        val gatesMode: String, val runMode: String, val capTokens: Int, val onCap: String, val cheaperModel: Model?,
        val usageWarn: Double?, val usagePause: Double?, val providerWindows: List<Map<String, Any?>>?, val capUsd: Double?,
        val onCapUsd: String?, val stepCapTokens: Int?, val stepOnCap: String?, val commitAuthor: String?, val commitCoauthor: Boolean?,
        val plugins: List<String>?, val pushPr: String?, val branchPattern: String?,
    )

    private fun before(plugins: List<String>?) = ThreadSettingsBefore("every-ac", "manual", 500_000, "pause", Model("claude", "subscription", "haiku"),
        0.8, 0.95, null, 2.5, "stop", null, null, null, true, plugins, "ask", "feat/{slug}")

    private fun now(contributed: Map<String, Any?>) = ThreadSettings("every-ac", "manual", 500_000, "pause", Model("claude", "subscription", "haiku"),
        0.8, 0.95, null, 2.5, "stop", null, null, null, true, contributed = contributed, pushPr = "ask", branchPattern = "feat/{slug}")

    @Test
    fun `flow settings are written byte for byte as before, the contributed ones where plugins was`() {
        assertThat(mapper.writeValueAsString(now(mapOf("plugins" to listOf("db", "git")))))
            .isEqualTo(mapper.writeValueAsString(before(listOf("db", "git"))))
        assertThat(mapper.writeValueAsString(now(mapOf("plugins" to null)))).isEqualTo(mapper.writeValueAsString(before(null)))
    }

    @Test
    fun `a flow start sends the Plugins part's list, then push_pr and branch_pattern, as before`() {
        val (pid, _) = newProject("slots-payload")
        engine.nextThreadIds.add("t-slots-payload")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Payload", "allow_fake" to true, "allow_dirty" to true))
            .andExpect(status().isOk)
        val sent = engine.lastBody("/threads")!!["settings"]
        assertThat(sent.fieldNames().asSequence().toList().takeLast(5)).containsExactly("commit_author", "commit_coauthor", "plugins", "push_pr", "branch_pattern")
        assertThat(sent["plugins"].isNull).isTrue()                  // none on: null, as the engine always got it
        assertThat(sent["push_pr"].asText()).isEqualTo("ask")
        assertThat(sent["branch_pattern"].asText()).isEqualTo("feat/{slug}")
    }

    @Test
    fun `the connection kinds come in the page's order, GitHub from core`() {
        // Jira's kind comes with the Jira plugin (plugins/jira checks it there, first in this order), GitLab's with the
        // Code Review plugin (plugins/review checks it there, between GitHub and the databases)
        val kinds = get("/api/connections/kinds").andExpect(status().isOk).json()
        assertThat(kinds.map { it["kind"].asText() }).containsExactly("github", "database")
        assertThat(kinds.map { it["scope"].asText() }).containsExactly("keel", "project")
        val github = kinds.first { it["kind"].asText() == "github" }
        assertThat(github["title"].asText()).isEqualTo("GitHub")
        assertThat(github["fields"].map { it["key"].asText() + ":" + it["type"].asText() }).containsExactly("token:secret")
        val db = kinds.first { it["kind"].asText() == "database" }
        assertThat(db["fields"].filter { it["required"].asBoolean() }.map { it["key"].asText() }).containsExactly("name", "url")
        assertThat(db["fields"].first { it["key"].asText() == "env" }["choices"].map { it.asText() }).containsExactly("local", "test", "staging", "prod")
    }

    @Test
    fun `the settings sections hold every core key once, with its type and default`() {
        val sections = get("/api/settings/sections").andExpect(status().isOk).json()
        assertThat(sections.map { it["id"].asText() }).containsExactly("mode", "flow", "models", "budget", "git", "notify", "env")
        val keys = sections.flatMap { s -> s["keys"].toList() }
        assertThat(keys.map { it["key"].asText() }).containsExactlyInAnyOrderElementsOf(Settings.KEYS)
        val byKey = keys.associateBy { it["key"].asText() }
        fun shape(k: String) = byKey.getValue(k).let { "${it["type"].asText()} ${it["default"]}" }
        assertThat(shape("push_pr")).isEqualTo("choice \"ask\"")
        assertThat(byKey.getValue("push_pr")["choices"].map { it.asText() }).containsExactly("ask", "auto", "never")
        assertThat(shape("cap_tokens")).isEqualTo("int 500000")
        assertThat(shape("usage_warn")).isEqualTo("number 0.8")
        assertThat(shape("keel_rules")).isEqualTo("bool true")
        assertThat(shape("branch_pattern")).isEqualTo("text \"feat/{slug}\"")
        assertThat(shape("mcp")).isEqualTo("list [\"keel\"]")
        assertThat(byKey.getValue("default_model")["type"].asText()).isEqualTo("model")
        assertThat(byKey.getValue("default_model")["default"]["provider"].asText()).isEqualTo("fake")
        assertThat(keys.filter { it["general_only"].asBoolean() }.map { it["key"].asText() }).containsExactly("keel_mode")
        assertThat(sections.first { it["id"].asText() == "git" }["keys"].map { it["key"].asText() })
            .containsExactly("branch_pattern", "web_lane_worktree", "commit_coauthor", "commit_author", "ci_on_failure", "push_pr")
    }

    @Test
    fun `a plugin's own settings round trip under plugins dot name, General then the project`() {
        val (pid, _) = newProject("slots-plugin-settings")
        put("/api/projects/$pid/settings", mapOf("gates_mode" to "end")).andExpect(status().isOk)
        put("/api/settings/general", mapOf("plugins.notes.colour" to "blue", "plugins.notes.size" to 3)).andExpect(status().isOk)
        val saved = put("/api/projects/$pid/settings", mapOf("plugins.notes.colour" to "red")).andExpect(status().isOk).json()
        // core's view is unchanged: only core's keys, and the project's own core override stays
        assertThat(saved["overrides"].fieldNames().asSequence().toList()).containsExactly("gates_mode")
        assertThat(saved["effective"]["gates_mode"].asText()).isEqualTo("end")
        assertThat(saved["effective"].has("plugins.notes.colour")).isFalse()
        assertThat(get("/api/settings/general").json().has("plugins.notes.colour")).isFalse()

        val view = get("/api/projects/$pid/settings/plugins/notes").andExpect(status().isOk).json()
        assertThat(view["general"].toString()).isEqualTo("""{"colour":"blue","size":3}""")
        assertThat(view["overrides"].toString()).isEqualTo("""{"colour":"red"}""")
        assertThat(view["effective"].toString()).isEqualTo("""{"colour":"red","size":3}""")
        assertThat(get("/api/settings/plugins/notes").json()["effective"].toString()).isEqualTo("""{"colour":"blue","size":3}""")
        assertThat(get("/api/settings/plugins/other").json()["general"].size()).isZero()

        // in Kotlin, through SettingsService.plugin
        val notes = settings.plugin("notes")
        notes.updateProject(pid, mapOf("size" to 5))
        assertThat(notes.get("size", pid)).isEqualTo(5)
        assertThat(notes.get("size")).isEqualTo(3)
        assertThat(notes.get("colour", pid)).isEqualTo("red")

        // null removes: the project follows General again, and General can drop a key too
        put("/api/projects/$pid/settings", mapOf("plugins.notes.colour" to null)).andExpect(status().isOk)
        assertThat(notes.effective(pid)).isEqualTo(mapOf("colour" to "blue", "size" to 5))
        put("/api/settings/general", mapOf("plugins.notes.colour" to null, "plugins.notes.size" to null)).andExpect(status().isOk)
        assertThat(notes.general()).isEmpty()
        assertThat(notes.effective(pid)).isEqualTo(mapOf("size" to 5))
        assertThat(settings.effective(pid).gatesMode).isEqualTo("end")
    }

    @Test
    fun `a plugin key needs a good name, and core's keys keep their checks`() {
        val (pid, _) = newProject("slots-plugin-bad")
        for (bad in listOf("plugins.Bad_Name.x", "plugins.notes", "plugins.notes.a.b", "plugins.notes.Colour", "plugins..x")) {
            val e = put("/api/projects/$pid/settings", mapOf(bad to 1)).andExpect(status().isBadRequest).json()
            assertThat(e["error"].asText()).describedAs(bad).contains(bad)
            assertThat(e["hint"].asText()).contains("plugins.<plugin>.<key>")
        }
        get("/api/settings/plugins/Bad_Name").andExpect(status().isBadRequest)

        // core keys: as before
        val unknown = put("/api/projects/$pid/settings", mapOf("colour" to "red")).andExpect(status().isBadRequest).json()
        assertThat(unknown["error"].asText()).contains("colour")
        assertThat(unknown["hint"].asText()).contains("gates_mode")
        put("/api/settings/general", mapOf("gates_mode" to null)).andExpect(status().isBadRequest)
        put("/api/projects/$pid/settings", mapOf("keel_mode" to "dev")).andExpect(status().isBadRequest)
        put("/api/projects/$pid/settings", mapOf("cap_tokens" to "lots")).andExpect(status().isBadRequest)
        // a bad core value refuses the whole patch: the plugin's key is not saved either
        put("/api/projects/$pid/settings", mapOf("plugins.notes.colour" to "red", "push_pr" to "sometimes")).andExpect(status().isBadRequest)
        assertThat(settings.plugin("notes").overrides(pid)).isEmpty()
    }
}
