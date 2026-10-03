package keel.api.connections

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.BadRequest
import keel.api.common.KeelHome
import keel.api.common.KvStore
import keel.api.common.NotFound
import keel.api.common.Proc
import keel.api.engine.EngineClient
import keel.api.settings.SettingsService
import org.springframework.stereotype.Service
import java.util.concurrent.ConcurrentHashMap

data class ModeView(val id: String, val label: String, val ready: Boolean, val detail: String)

data class ProviderView(
    val id: String,
    val label: String,
    val modes: List<ModeView>,
    val selected: String,
    val keySet: Boolean,
    val keyHint: String? = null,
)

data class MachineTool(val name: String, val ok: Boolean, val version: String? = null)
data class Connections(val providers: List<ProviderView>, val machine: List<MachineTool>)

@Service
class ConnectionService(
    private val secrets: SecretService,
    private val kv: KvStore,
    private val home: KeelHome,
    private val engine: EngineClient,
    private val settings: SettingsService,
) {
    private data class Cached(val at: Long, val tool: MachineTool)
    private val cache = ConcurrentHashMap<String, Cached>()

    /** Is a program installed, and which version? Cached for a minute (some CLIs are slow to answer). */
    private fun tool(name: String): MachineTool {
        cache[name]?.takeIf { System.currentTimeMillis() - it.at < 60_000 }?.let { return it.tool }
        val path = Proc.which(name)
        val t = if (path == null) MachineTool(name, false) else {
            val r = Proc.run(listOf(path, "--version"), null, 5)
            MachineTool(name, true, r.out.ifBlank { r.err }.lineSequence().firstOrNull()?.trim()?.take(80)?.ifBlank { null })
        }
        cache[name] = Cached(System.currentTimeMillis(), t)
        return t
    }

    private fun selectedModes(): Map<String, String> = kv.get<Map<String, String>>(KEY) ?: emptyMap()

    private fun keyView(provider: String): Pair<Boolean, String?> {
        val name = SecretService.KEY_NAMES[provider] ?: return false to null
        if (secrets.has(name)) return true to secrets.hintOf(name)
        val env = System.getenv(name)
        return if (!env.isNullOrBlank()) true to "from the environment" else false to null
    }

    fun connections(): Connections {
        val sel = selectedModes()
        val providers = PROVIDERS.map { (id, label) ->
            val (keySet, hint) = keyView(id)
            val modes = when (id) {
                "fake" -> listOf(ModeView("api", "Fake model (no network)", true, "Deterministic answers for demos and tests."))
                "claude" -> listOf(
                    cliMode("subscription", "Claude subscription (Claude Code CLI)", "claude"),
                    ModeView("api", "Anthropic API key", keySet, if (keySet) "Key saved." else "Add ANTHROPIC_API_KEY."),
                )
                "codex" -> listOf(
                    cliMode("subscription", "ChatGPT subscription (Codex CLI)", "codex"),
                    ModeView("api", "OpenAI API key", keySet, if (keySet) "Key saved." else "Add OPENAI_API_KEY."),
                )
                "copilot" -> listOf(
                    cliMode("subscription", "Copilot CLI", "copilot"),
                    cliMode("opencode", "OpenCode", "opencode"),
                    ModeView("api", "GitHub Models (token)", keySet, if (keySet) "Token saved." else "Add GITHUB_TOKEN."),
                )
                else -> emptyList()
            }
            val selected = sel[id] ?: modes.firstOrNull { it.ready }?.id ?: modes.first().id
            ProviderView(id, label, modes, selected, keySet, hint)
        }
        val machine = listOf("node", "git").map { tool(it) } + keelTool() + listOf("claude", "codex", "copilot", "opencode").map { tool(it) }
        return Connections(providers, machine)
    }

    private fun keelTool(): MachineTool {
        val v = home.version()
        return MachineTool("keel", v != null, v)
    }

    private fun cliMode(id: String, label: String, bin: String): ModeView {
        val t = tool(bin)
        return ModeView(id, label, t.ok, if (t.ok) "${t.version ?: bin} found." else "$bin is not installed. Build the image with INSTALL_CLIS=1.")
    }

    fun select(provider: String, mode: String): Connections {
        val p = connections().providers.firstOrNull { it.id == provider } ?: throw NotFound("No provider called \"$provider\"")
        if (p.modes.none { it.id == mode }) throw BadRequest("$provider has no mode \"$mode\"", "Modes: ${p.modes.joinToString { it.id }}")
        kv.put(KEY, selectedModes() + (provider to mode))
        return connections()
    }

    /** Asks the engine to run "Reply with exactly: OK" with the selected mode. */
    fun test(provider: String): JsonNode {
        val p = connections().providers.firstOrNull { it.id == provider } ?: throw NotFound("No provider called \"$provider\"")
        val general = settings.general()
        val model = listOf(general.defaultModel, general.implementerModel, general.reviewerModel, general.cheaperModel)
            .firstOrNull { it.provider == provider }?.model ?: DEFAULT_MODELS[provider] ?: provider
        val body = mutableMapOf<String, Any?>("provider" to provider, "mode" to p.selected, "model" to model)
        if (p.selected == "api") secrets.keyForProvider(provider)?.let { body["key"] = it }
        return engine.providerTest(body)
    }

    companion object {
        const val KEY = "connections"
        val PROVIDERS = listOf("fake" to "Fake (demo)", "claude" to "Claude", "codex" to "GPT / Codex", "copilot" to "Copilot")
        val DEFAULT_MODELS = mapOf("fake" to "fake", "claude" to "sonnet", "codex" to "gpt-5", "copilot" to "gpt-5")
    }
}
