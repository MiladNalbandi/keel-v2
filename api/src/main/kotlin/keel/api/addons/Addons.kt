package keel.api.addons

import keel.api.pluginhost.PluginHost
import keel.api.pluginhost.PluginWebUrls
import keel.api.settings.SettingsService
import org.springframework.beans.factory.ObjectProvider
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController

/**
 * An add-on's API side (keel Product is one): a Spring bean in the add-on's own jar, loaded only where the add-on is
 * installed. keel's core knows an add-on only through this interface; with none installed nothing here changes.
 */
interface KeelAddon {
    /** Its id, the same as its engine add-on's (keel_engine/addons.py): "product". */
    val name: String
    val version: String
    /** What people see: "keel Product". */
    val title: String
    /** The part of keel it is, turned on and off by Settings › What this keel does (keel_mode). */
    val part: String
    /** The menu items it adds (its web pages, product/web). */
    val screens: List<AddonScreen>
}

data class AddonScreen(val id: String, val label: String, val group: String, val needsProject: Boolean = false, val addon: String = "")

data class AddonInfo(val name: String, val version: String, val title: String, val part: String, val on: Boolean)

/** A plugin keel-start loaded (keel.api.pluginhost). The web loads its web part from these urls at run time. */
data class PluginInfo(val name: String, val title: String, val version: String, val web: PluginWebUrls?)

/**
 * GET /api/features: what this keel does now. `modes` lists what Settings may choose (only "dev" without an add-on).
 * `plugins` lists every resolved plugin, whatever the mode.
 */
data class Features(
    val mode: String,
    val modes: List<String>,
    val parts: Map<String, Boolean>,
    val addons: List<AddonInfo>,
    val screens: List<AddonScreen>,
    val plugins: List<PluginInfo>,
)

@Service
class FeatureService(found: ObjectProvider<KeelAddon>, private val settings: SettingsService, private val host: PluginHost) {
    /** The installed add-ons (none in keel's own image). */
    val addons: List<KeelAddon> = found.orderedStream().toList()

    private fun parts(): Set<String> = addons.map { it.part }.toSet()

    /** dev, product or both. "auto" (the default) is both when a Product add-on is installed, else dev. */
    fun mode(): String {
        val chosen = settings.general().keelMode
        val offered = parts()
        return when {
            offered.isEmpty() -> "dev"
            chosen == "auto" -> "both"
            chosen == "dev" || chosen == "both" -> chosen
            chosen in offered -> chosen
            else -> "dev"
        }
    }

    fun partOn(part: String): Boolean {
        val mode = mode()
        return if (part == "dev") mode == "dev" || mode == "both" else part in parts() && (mode == part || mode == "both")
    }

    /**
     * Whether an engine add-on's workflows show. An add-on that is a part of keel (keel Product: a [KeelAddon]) follows
     * its part's mode. v0.16.0 a plugin keel-start loaded whose engine part brings workflows but that is no part of keel
     * (a marketplace plugin: no KeelAddon) shows them while it is loaded. Any other engine add-on stays hidden.
     */
    fun addonOn(name: String): Boolean = addons.firstOrNull { it.name == name }?.let { partOn(it.part) }
        ?: host.plugins.any { it.name == name }

    fun features(): Features {
        val offered = parts()
        val modes = if (offered.isEmpty()) listOf("dev") else listOf("dev") + offered.sorted() + "both"
        val on = (setOf("dev") + offered).associateWith { partOn(it) }
        return Features(mode(), modes, on, addons.map { AddonInfo(it.name, it.version, it.title, it.part, partOn(it.part)) },
            addons.filter { partOn(it.part) }.flatMap { a -> a.screens.map { it.copy(addon = a.name) } },
            host.plugins.map { PluginInfo(it.name, it.title, it.version, host.webUrls(it)) })
    }
}

@RestController
class FeatureController(private val features: FeatureService) {
    @GetMapping("/api/features")
    fun get(): Features = features.features()
}
