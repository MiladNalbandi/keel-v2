package keel.api.pluginhost

import com.fasterxml.jackson.annotation.JsonProperty
import com.fasterxml.jackson.databind.DeserializationFeature
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.PropertyNamingStrategies
import com.fasterxml.jackson.databind.json.JsonMapper
import com.fasterxml.jackson.module.kotlin.KotlinFeature
import com.fasterxml.jackson.module.kotlin.KotlinModule
import keel.api.common.KeelProperties
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Component
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

/** run/resolved.json, as `keel-engine plugins resolve` writes it (docs/plugins/07-step1-contract.md, section 4). */
data class Resolved(
    val sdk: Int = PluginHost.SDK,
    val keel: String = "",
    /** KEEL_PLUGINS: on, image (safe mode) or off. */
    val mode: String = "on",
    val resolvedAt: String = "",
    val plugins: List<ResolvedPlugin> = emptyList(),
    val problems: List<PluginProblem> = emptyList(),
)

/** A plugin that loads in this run. Its folder and part paths are absolute; its web files are relative to the folder. */
data class ResolvedPlugin(
    val name: String = "",
    val title: String = "",
    val version: String = "",
    /** image (in keel's image) or file (installed by a person). */
    val source: String = "",
    val dir: String = "",
    val engine: EnginePart? = null,
    val api: ApiPart? = null,
    val web: WebPart? = null,
    val content: String? = null,
    /** The folder of its own Flyway migrations (V1__x.sql …), run by [PluginMigrations]. */
    val migrations: String? = null,
    val requires: Requires = Requires(),
) {
    /** The parts it has, always in this order: engine, api, web, content, migrations. */
    fun parts(): List<String> = listOfNotNull(
        engine?.let { "engine" }, api?.let { "api" }, web?.let { "web" }, content?.let { "content" }, migrations?.let { "migrations" },
    )
}

data class EnginePart(val path: String = "", @JsonProperty("package") val pkg: String = "")
data class ApiPart(val jars: List<String> = emptyList(), val lib: String? = null)
data class WebPart(val entry: String = "", val css: List<String> = emptyList())
data class Requires(val sdk: Int? = null, val keel: String? = null, val plugins: Map<String, String> = emptyMap())

/** A plugin that was left out, and why. */
data class PluginProblem(val name: String = "", val version: String? = null, val dir: String? = null, val error: String = "")

/** Where the browser loads a plugin's web part from: absolute urls under /plugins/<name>/<version>/web/. */
data class PluginWebUrls(val entry: String, val css: List<String>)

/**
 * The plugins keel-start resolved for this run. It reads `<keel.data>/plugins/run/resolved.json` once, at start.
 * No file means no plugins (a dev run without keel-start). A file it cannot read also means no plugins, plus one
 * problem that says why. A plugin with a bad name, version or folder is left out with a problem too.
 */
@Component
class PluginHost(props: KeelProperties) {
    private val log = LoggerFactory.getLogger(javaClass)

    val file: Path = props.dataDir.resolve("plugins").resolve("run").resolve("resolved.json")

    private val state: State = load(file)

    /** on, image or off. off when nothing was resolved. */
    val mode: String get() = state.mode
    val plugins: List<ResolvedPlugin> get() = state.plugins
    val problems: List<PluginProblem> get() = state.problems

    /** The resolved plugin with this name and version, or null. */
    fun find(name: String, version: String): ResolvedPlugin? = plugins.firstOrNull { it.name == name && it.version == version }

    /** The urls of a plugin's web entry and css files, or null when it has no web part. */
    fun webUrls(p: ResolvedPlugin): PluginWebUrls? {
        val web = p.web?.takeIf { it.entry.isNotBlank() } ?: return null
        return PluginWebUrls(webUrl(p, web.entry), web.css.filter { it.isNotBlank() }.map { webUrl(p, it) })
    }

    /**
     * The file a url under /plugins/<name>/<version>/web/ asks for: only inside that resolved plugin's `<dir>/web/`
     * folder (also after following links), and only a real file. Null otherwise.
     */
    fun webFile(name: String, version: String, path: String): Path? {
        val p = find(name, version) ?: return null
        val root = Paths.get(p.dir).resolve("web").normalize()
        val file = runCatching { root.resolve(path.trimStart('/')).normalize() }.getOrNull() ?: return null
        if (!file.startsWith(root) || !Files.isRegularFile(file)) return null
        val inside = runCatching { file.toRealPath().startsWith(root.toRealPath()) }.getOrDefault(false)
        return file.takeIf { inside }
    }

    /** A web file's path is relative to the plugin folder ("web/index.js"); one without "web/" is taken as inside web/. */
    private fun webUrl(p: ResolvedPlugin, file: String): String {
        val rel = file.trim().removePrefix("./").trimStart('/')
        return "/plugins/${p.name}/${p.version}/" + if (rel.startsWith("web/")) rel else "web/$rel"
    }

    private data class State(val mode: String, val plugins: List<ResolvedPlugin>, val problems: List<PluginProblem>)

    private fun load(file: Path): State {
        if (!Files.exists(file)) {
            log.info("plugins: no {}, so no plugins (keel-start did not resolve any)", file)
            return State("off", emptyList(), emptyList())
        }
        val resolved = try {
            READER.readValue(file.toFile(), Resolved::class.java)
        } catch (e: Exception) {
            log.error("plugins: cannot read {}, so no plugin loads", file, e)
            return State("off", emptyList(), listOf(fileProblem(file, "keel could not read it, so no plugin loads: ${e.message?.lineSequence()?.first()}")))
        }
        if (resolved.sdk != SDK) {
            log.error("plugins: {} is for plugin SDK {}, this keel has {}; no plugin loads", file, resolved.sdk, SDK)
            return State(resolved.mode, emptyList(), resolved.problems + fileProblem(file, "it is for plugin SDK ${resolved.sdk}, this keel has $SDK, so no plugin loads"))
        }
        val kept = mutableListOf<ResolvedPlugin>()
        val problems = resolved.problems.toMutableList()
        for (p in resolved.plugins) {
            val error = check(p, kept)
            if (error == null) kept += p else problems += PluginProblem(p.name, p.version, p.dir, error)
        }
        log.info("plugins: {} loaded ({}), {} left out (mode {})", kept.size, kept.joinToString { "${it.name} ${it.version}" }, problems.size, resolved.mode)
        problems.forEach { log.warn("plugins: {} {} left out: {}", it.name, it.version.orEmpty(), it.error) }
        return State(resolved.mode, kept, problems)
    }

    private fun check(p: ResolvedPlugin, kept: List<ResolvedPlugin>): String? = when {
        !NAME.matches(p.name) -> "its name is not a valid plugin name"
        !VERSION.matches(p.version) -> "its version is not valid"
        !runCatching { Paths.get(p.dir).isAbsolute }.getOrDefault(false) -> "its folder is not an absolute path"
        kept.any { it.name == p.name } -> "another plugin with this name is already loaded"
        else -> null
    }

    private fun fileProblem(file: Path, error: String) = PluginProblem("resolved.json", dir = file.parent.toString(), error = error)

    companion object {
        /** The plugin SDK major this keel offers (the engine's keel_engine.pluginhost.SDK). */
        const val SDK = 1
        val NAME = Regex("^[a-z][a-z0-9-]{0,31}$")
        /** A version is also a url part, so: letters, digits, dots, plus, minus and underscore only. */
        val VERSION = Regex("^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")

        /** Its own reader: snake_case, unknown fields ignored, null = the default. */
        private val READER: ObjectMapper = JsonMapper.builder()
            .addModule(KotlinModule.Builder().enable(KotlinFeature.NullIsSameAsDefault).build())
            .propertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .build()
    }
}
