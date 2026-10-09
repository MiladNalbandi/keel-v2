package keel.api

import keel.api.common.KeelProperties
import keel.api.pluginhost.PluginHost
import keel.api.pluginhost.PluginWebUrls
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

/** How the plugin host reads run/resolved.json, and which web files it gives out (no Spring). */
class PluginHostTest {
    private val data: Path = Files.createTempDirectory("keel-plugin-host")

    private fun host(resolved: String? = null): PluginHost {
        if (resolved != null) {
            val run = Files.createDirectories(data.resolve("plugins").resolve("run"))
            Files.writeString(run.resolve("resolved.json"), resolved)
        }
        return PluginHost(KeelProperties(data = data.toString()))
    }

    private fun plugin(name: String, version: String = "1.0.0", dir: String = "/opt/keel-v2/plugins/$name/$version", more: String = "") =
        """{ "name": "$name", "title": "$name", "version": "$version", "source": "image", "dir": "$dir" $more }"""

    private fun file(vararg plugins: String, sdk: Int = 1) = """{ "sdk": $sdk, "mode": "image", "plugins": [${plugins.joinToString()}], "problems": [] }"""

    @Test
    fun `no file means no plugins and no problems`() {
        val h = host()
        assertThat(h.mode).isEqualTo("off")
        assertThat(h.plugins).isEmpty()
        assertThat(h.problems).isEmpty()
    }

    @Test
    fun `a file that is not valid json means no plugins and one problem`() {
        val h = host("{ \"sdk\": 1, \"plugins\": [")
        assertThat(h.plugins).isEmpty()
        assertThat(h.problems.map { it.name }).containsExactly("resolved.json")
        assertThat(h.problems[0].error).contains("could not read it")
    }

    @Test
    fun `a file for another plugin SDK loads nothing`() {
        val h = host(file(plugin("demo"), sdk = 2))
        assertThat(h.plugins).isEmpty()
        assertThat(h.problems.single().error).contains("plugin SDK 2", "this keel has 1")
    }

    @Test
    fun `unknown fields are ignored and null is the default`() {
        val h = host(file(plugin("demo", more = """, "title": null, "web": { "entry": "web/index.js", "css": null }, "later": { "x": 1 }""")))
        assertThat(h.mode).isEqualTo("image")
        val p = h.plugins.single()
        assertThat(p.title).isEmpty()
        assertThat(p.web!!.css).isEmpty()
        assertThat(p.parts()).containsExactly("web")
    }

    @Test
    fun `a plugin with a bad name, version or folder, or a second one with the same name, is left out`() {
        val h = host(file(
            plugin("demo"),
            plugin("Demo"),
            plugin("ok", version = "../1"),
            plugin("rel", dir = "plugins/rel/1.0.0"),
            plugin("demo", version = "2.0.0"),
        ))
        assertThat(h.plugins.map { "${it.name} ${it.version}" }).containsExactly("demo 1.0.0")
        assertThat(h.problems.map { "${it.name} ${it.version}: ${it.error}" }).containsExactly(
            "Demo 1.0.0: its name is not a valid plugin name",
            "ok ../1: its version is not valid",
            "rel 1.0.0: its folder is not an absolute path",
            "demo 2.0.0: another plugin with this name is already loaded",
        )
    }

    @Test
    fun `web urls are absolute and under the plugin's web folder`() {
        val h = host(file(
            plugin("a", more = """, "web": { "entry": "web/index.js", "css": ["web/style.css", "./web/more.css"] }"""),
            plugin("b", version = "0.1.0-beta.1", more = """, "web": { "entry": "index.js" }"""),
            plugin("c"),
        ))
        val (a, b, c) = h.plugins
        assertThat(h.webUrls(a)).isEqualTo(PluginWebUrls("/plugins/a/1.0.0/web/index.js", listOf("/plugins/a/1.0.0/web/style.css", "/plugins/a/1.0.0/web/more.css")))
        assertThat(h.webUrls(b)).isEqualTo(PluginWebUrls("/plugins/b/0.1.0-beta.1/web/index.js", emptyList()))
        assertThat(h.webUrls(c)).isNull()
    }

    @Test
    fun `only real files inside the web folder are given out, also after links`() {
        val dir = Files.createDirectories(data.resolve("store/demo/1.0.0"))
        val web = Files.createDirectories(dir.resolve("web/assets"))
        Files.writeString(web.resolve("chunk.js"), "export {}")
        Files.writeString(dir.resolve("secret.txt"), "not for the browser")
        Files.createSymbolicLink(dir.resolve("web/link.txt"), dir.resolve("secret.txt"))
        val h = host(file(plugin("demo", dir = dir.toString())))
        assertThat(h.webFile("demo", "1.0.0", "/assets/chunk.js")).isEqualTo(web.resolve("chunk.js"))
        assertThat(h.webFile("demo", "1.0.0", "/../secret.txt")).isNull()
        assertThat(h.webFile("demo", "1.0.0", "/link.txt")).isNull()
        assertThat(h.webFile("demo", "1.0.0", "/assets")).isNull()
        assertThat(h.webFile("demo", "2.0.0", "/assets/chunk.js")).isNull()
        assertThat(h.webFile("other", "1.0.0", "/assets/chunk.js")).isNull()
    }
}
