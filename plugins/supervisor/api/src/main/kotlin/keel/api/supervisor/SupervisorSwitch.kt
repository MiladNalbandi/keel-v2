package keel.api.supervisor

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.BadRequest
import keel.api.engine.EngineClient
import keel.api.plugins.PluginService
import keel.api.plugins.PluginSwitched
import keel.api.projects.ProjectService
import keel.api.settings.SettingKey
import keel.api.settings.SettingsChanged
import keel.api.settings.SettingsSection
import keel.api.settings.SettingsService
import keel.api.settings.SimpleSettingsSection
import org.slf4j.LoggerFactory
import org.springframework.boot.context.event.ApplicationReadyEvent
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.event.EventListener
import org.springframework.stereotype.Component
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/**
 * The Supervisor's on/off switches. keel's settings hold them (`plugins.supervisor.on`): General is the keel-wide
 * switch, off until the person turns it on; a project may set its own (else it follows General). The plugin's own
 * switch per project (Control › Plugins) comes on top: off there, it is off. A flow may also have its own switch
 * (POST /api/projects/{pid}/flows/{tid}/supervisor {on}).
 *
 * The engine part acts on them while flows run, so every change is sent to it at once (POST /supervisor/switch), and
 * all of them when the api starts. Off keel-wide, the engine part listens to nothing and calls no model.
 */
@Component
class SupervisorSwitch(
    private val settings: SettingsService,
    private val plugins: PluginService,
    private val projects: ProjectService,
    private val engine: EngineClient,
) {
    private val log = LoggerFactory.getLogger(SupervisorSwitch::class.java)

    fun keelOn(): Boolean = settings.plugin(NAME).general()[KEY] == true

    /** 0.21.0 keel-wide mode and stall time (Settings › General › Supervisor). A flow's own value wins. */
    fun mode(): String = settings.plugin(NAME).general()[MODE_KEY]?.toString()?.takeIf { it in MODES } ?: "shadow"

    fun stallMinutes(): Int = (settings.plugin(NAME).general()[STALL_KEY] as? Number)?.toInt()?.coerceIn(STALL_MIN, STALL_MAX) ?: STALL_DEFAULT

    fun setDefaults(on: Boolean?, mode: String?, stall: Int?) {
        val patch = mutableMapOf<String, Any?>()
        on?.let { patch[SETTING] = it }
        mode?.let { patch[MODE_SETTING] = it }
        stall?.let { patch[STALL_SETTING] = it }
        if (patch.isNotEmpty()) settings.updateGeneral(patch)
    }

    /** The project's own switch: true, false, or null (it follows General). */
    fun own(pid: String): Boolean? = settings.plugin(NAME).overrides(pid)[KEY] as? Boolean

    fun projectOn(pid: String): Boolean = own(pid) ?: keelOn()

    fun pluginOn(pid: String): Boolean = plugins.on(pid, NAME)

    fun view(pid: String): Map<String, Any?> = mapOf(
        "keel" to keelOn(), "own" to own(pid), "project" to projectOn(pid), "plugin" to pluginOn(pid),
        "on" to (keelOn() && pluginOn(pid) && projectOn(pid)),
        "text" to text(pid),
    )

    /** The switches in one plain sentence for the TUI and MCP: on here or not, why, and the command that changes it. */
    fun text(pid: String): String {
        val own = own(pid)
        val project = when (own) { true -> "on (this project's own choice)"; false -> "off (this project's own choice)"
            null -> "follows keel-wide" }
        val switches = "keel-wide ${if (keelOn()) "on" else "off"} · project $project · plugin ${if (pluginOn(pid)) "on" else "off"}"
        val why = when {
            !pluginOn(pid) -> "the Supervisor plugin is off for this project: turn it on in Control › Plugins"
            own == false -> "this project is switched off: /supervisor on (or follow)"
            !projectOn(pid) -> "the keel-wide switch is off: /supervisor keel-on, or /supervisor on for this project only"
            else -> null
        }
        return if (why == null) "Supervisor: on here, it watches this project's flows · $switches"
        else "Supervisor: off here, $why · $switches"
    }

    fun keelText(): String = if (keelOn()) "Supervisor keel-wide: on (each project with the plugin on follows, unless it says otherwise)"
        else "Supervisor keel-wide: off (nothing listens and no model is called)"

    fun setKeel(on: Boolean) {
        settings.updateGeneral(mapOf(SETTING to on))
    }

    /** null: the project follows General again. */
    fun setProject(pid: String, on: Boolean?) {
        settings.updateProject(pid, mapOf(SETTING to on))
    }

    /** Sends the switches to the engine: the keel-wide one and one project's, or every project's (pid null). */
    fun push(pid: String?) {
        val ids = if (pid == null) projects.rows().map { it.id } else listOf(pid)
        val body = mapOf("keel" to keelOn(), "defaults" to mapOf("mode" to mode(), "stall_minutes" to stallMinutes()),
            "projects" to ids.associateWith { mapOf("on" to projectOn(it), "plugin" to pluginOn(it)) })
        runCatching { engine.post("/supervisor/switch", body) }
            .onFailure { log.warn("could not send the Supervisor's switches to the engine: {}", it.message) }
    }

    @EventListener
    fun settingsChanged(e: SettingsChanged) {
        if (e.keys.any { it.startsWith("plugins.$NAME.") }) push(e.projectId)
    }

    @EventListener
    fun pluginSwitched(e: PluginSwitched) {
        if (e.plugin == NAME) push(e.projectId.takeIf { it != "*" })
    }

    @EventListener(ApplicationReadyEvent::class)
    fun ready() = push(null)

    companion object {
        const val NAME = "supervisor"
        const val KEY = "on"
        const val SETTING = "plugins.supervisor.on"
        const val MODE_KEY = "mode"
        const val MODE_SETTING = "plugins.supervisor.mode"
        const val STALL_KEY = "stall_minutes"
        const val STALL_SETTING = "plugins.supervisor.stall_minutes"
        const val STALL_DEFAULT = 30
        const val STALL_MIN = 5
        const val STALL_MAX = 1440
        val MODES = listOf("shadow", "retry", "act", "fix")
    }
}

/** Its keys in keel's settings: off until the person turns it on, and then it only suggests. */
@Configuration
class SupervisorSettings {
    @Bean
    fun supervisorSettingsSection(): SettingsSection = SimpleSettingsSection("supervisor", "Supervisor",
        listOf(SettingKey(SupervisorSwitch.SETTING, "bool", false),
            SettingKey(SupervisorSwitch.MODE_SETTING, "choice", "shadow", SupervisorSwitch.MODES, generalOnly = true),
            SettingKey(SupervisorSwitch.STALL_SETTING, "int", SupervisorSwitch.STALL_DEFAULT, generalOnly = true)), 90)
}

/**
 *     GET|POST /api/supervisor/switch                    {on}: the keel-wide switch
 *     GET|POST /api/projects/{pid}/supervisor/switch     {on: true | false | null}: the project's (null = follow General)
 *     GET|POST /api/supervisor/defaults                  {on, mode, stall_minutes}: the row on Settings › General. It is not
 *                                                        on the MCP write list ([SupervisorMcpRoutes]): a mode that answers
 *                                                        gates for every flow is the person's call, or an admin client's
 */
@RestController
@RequestMapping("/api")
class SupervisorSwitchController(private val switch: SupervisorSwitch, private val projects: ProjectService,
                                 private val engine: EngineClient) {
    @GetMapping("/supervisor/switch")
    fun keel(): Map<String, Any?> = mapOf("on" to switch.keelOn(), "text" to switch.keelText(),
        "engine" to runCatching<JsonNode> { engine.get("/supervisor/switch") }.getOrNull())

    @PostMapping("/supervisor/switch")
    fun setKeel(@RequestBody body: Map<String, Any?>): Map<String, Any?> {
        switch.setKeel(body["on"] == true)
        return keel()
    }

    @GetMapping("/supervisor/defaults")
    fun defaults(): Map<String, Any?> = mapOf("on" to switch.keelOn(), "mode" to switch.mode(), "stall_minutes" to switch.stallMinutes(),
        "modes" to SupervisorSwitch.MODES, "stall_min" to SupervisorSwitch.STALL_MIN, "stall_max" to SupervisorSwitch.STALL_MAX)

    @PostMapping("/supervisor/defaults")
    fun setDefaults(@RequestBody body: Map<String, Any?>): Map<String, Any?> {
        val mode = body["mode"]?.toString()
        if (mode != null && mode !in SupervisorSwitch.MODES) throw BadRequest("\"$mode\" is not a Supervisor mode", "Pick one of: ${SupervisorSwitch.MODES.joinToString()}.")
        val stall = body["stall_minutes"]?.let { (it as? Number)?.toInt() ?: it.toString().toIntOrNull()
            ?: throw BadRequest("The stall time must be a number of minutes") }
        if (stall != null && stall !in SupervisorSwitch.STALL_MIN..SupervisorSwitch.STALL_MAX)
            throw BadRequest("The stall time must be ${SupervisorSwitch.STALL_MIN} to ${SupervisorSwitch.STALL_MAX} minutes")
        switch.setDefaults(body["on"] as? Boolean, mode, stall)
        return defaults()
    }

    @GetMapping("/projects/{pid}/supervisor/switch")
    fun project(@PathVariable pid: String): Map<String, Any?> {
        projects.require(pid)
        return switch.view(pid)
    }

    @PostMapping("/projects/{pid}/supervisor/switch")
    fun setProject(@PathVariable pid: String, @RequestBody body: Map<String, Any?>): Map<String, Any?> {
        projects.require(pid)
        switch.setProject(pid, body["on"] as? Boolean)
        return switch.view(pid)
    }
}
