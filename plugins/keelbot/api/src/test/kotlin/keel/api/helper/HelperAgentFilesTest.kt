package keel.api.helper

import keel.api.agents.AgentCatalog
import keel.api.agents.AgentFiles
import keel.api.common.KeelProperties
import keel.api.pluginhost.PluginHost
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.ObjectProvider
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.util.stream.Stream

/**
 * KeelBot's agent file comes from its plugin folder (as the plugin host resolved it) and sits on the Agents page where
 * keel 0.15.1 had it: in file-name order among keel's own agents. No Spring.
 */
class HelperAgentFilesTest {
    /** plugins/keelbot/content, as the image's resolver names it (the test runs from api/). */
    private val content: Path = Paths.get("../plugins/keelbot/content").toAbsolutePath().normalize()

    private fun host(vararg plugins: Pair<String, Path?>): PluginHost {
        val data = Files.createTempDirectory("keel-keelbot-agent")
        val run = Files.createDirectories(data.resolve("plugins").resolve("run"))
        val list = plugins.joinToString { (name, c) ->
            """{ "name": "$name", "title": "$name", "version": "1.0.0", "source": "image", "dir": "/opt/keel-v2/plugins/$name/1.0.0"""" +
                (c?.let { """, "content": "$it"""" } ?: "") + " }"
        }
        Files.writeString(run.resolve("resolved.json"), """{ "sdk": 1, "mode": "image", "plugins": [$list], "problems": [] }""")
        return PluginHost(KeelProperties(data = data.toString()))
    }

    private class Provider<T : Any>(private val beans: List<T>) : ObjectProvider<T> {
        override fun getObject(vararg args: Any?): T = beans.first()
        override fun getObject(): T = beans.first()
        override fun getIfAvailable(): T? = beans.firstOrNull()
        override fun getIfUnique(): T? = beans.singleOrNull()
        override fun stream(): Stream<T> = beans.stream()
        override fun orderedStream(): Stream<T> = beans.stream()
    }

    @Test
    fun `its agent file is in the KeelBot plugin's content, and nowhere without the plugin`() {
        assertThat(HelperAgentFiles(host("keelbot" to content)).files().map { it.fileName.toString() }).containsExactly("helper.md")
        assertThat(HelperAgentFiles(host("map" to content)).files()).isEmpty()
        assertThat(HelperAgentFiles(host("keelbot" to null)).files()).isEmpty()
    }

    @Test
    fun `the Agents page lists it among keel's own, in file-name order, and keel's own file wins`() {
        val core = Files.createTempDirectory("keel-content")
        val agents = Files.createDirectories(core.resolve("agents"))
        for (id in listOf("explorer", "hunter", "implementer")) Files.writeString(agents.resolve("$id.md"), "---\nname: $id\n---\n\nI am $id.\n")
        val files: AgentFiles = HelperAgentFiles(host("keelbot" to content))
        val catalog = AgentCatalog(KeelProperties(content = core.toString()), Provider(listOf(files)))
        assertThat(catalog.defaults().map { it.id }).containsExactly("explorer", "helper", "hunter", "implementer")
        val helper = catalog.find("helper")!!
        assertThat(helper.prompt).startsWith("You are KeelBot.")
        assertThat(helper.knowledge.sections).containsExactly("architecture", "domain", "conventions", "data", "integrations", "journeys")
        // keel's own content may name it too (an older content folder): keel's file wins, it is listed once
        Files.writeString(agents.resolve("helper.md"), "---\nname: helper\n---\n\nThe old one.\n")
        assertThat(catalog.defaults().map { it.id }).containsExactly("explorer", "helper", "hunter", "implementer")
        assertThat(catalog.find("helper")!!.prompt).isEqualTo("The old one.")
        // without the plugin: keel's own only
        Files.delete(agents.resolve("helper.md"))
        assertThat(AgentCatalog(KeelProperties(content = core.toString()), Provider(emptyList())).defaults().map { it.id })
            .containsExactly("explorer", "hunter", "implementer")
    }
}
