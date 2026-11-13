package keel.api.connections

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.engine.EngineClient
import keel.api.settings.SettingsService
import org.springframework.stereotype.Service

/** [need]: what a mode that is not ready misses; null when ready. */
data class ModeView(val id: String, val label: String, val ready: Boolean, val detail: String, val need: String? = null)

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
    private val engine: EngineClient,
    private val settings: SettingsService,
    private val servers: ModelServerService,
) {
    /** Programs on this machine, checked in parallel and cached for a minute (MachineTools). */
    internal var tools = MachineTools()

    private fun keyView(provider: String): Pair<Boolean, String?> {
        if (provider in ModelServerService.PROVIDERS) return servers.summary(provider).let { (ready, line) -> ready to line.takeIf { ready } }
        if (provider == "copilot") return secrets.copilotKeyHint().let { (it != null) to it }
        val name = SecretService.KEY_NAMES[provider] ?: return false to null
        if (secrets.has(name)) return true to secrets.hintOf(name)
        val env = System.getenv(name)
        return if (!env.isNullOrBlank()) true to "from the environment" else false to null
    }

    /** #101 keel's harness runs every model on its provider's API: each provider has that one mode, always selected. */
    fun connections(): Connections {
        // Every program at once: the answer takes about the slowest single check (up to 5 s), not their sum.
        val machine = tools.check(MACHINE)
        val providers = PROVIDERS.map { (id, label) ->
            val (keySet, hint) = keyView(id)
            val modes = when (id) {
                "fake" -> listOf(ModeView("api", "Fake model (no network)", true, "Deterministic answers for demos and tests."))
                "claude" -> listOf(ModeView("api", "Anthropic API key", keySet, if (keySet) "Key saved." else "Add ANTHROPIC_API_KEY."))
                "codex" -> listOf(ModeView("api", "OpenAI API key", keySet, if (keySet) "Key saved." else "Add OPENAI_API_KEY."))
                "copilot" -> listOf(ModeView("api", "GitHub Models (token)", keySet,
                    if (!keySet) "Add GITHUB_TOKEN, or sign in with GitHub." else if (hint?.startsWith("from ") == true) "Token $hint." else "Token saved."))
                "vertex" -> servers.summary(id).let { (ready, line) -> listOf(ModeView("api", "Vertex AI (Google Cloud)", ready, line)) }
                "local" -> servers.summary(id).let { (ready, line) -> listOf(ModeView("api", "Local model server", ready, line)) }
                else -> emptyList()
            }
            ProviderView(id, label, modes, MODE, keySet, hint)
        }
        return Connections(providers, machine)
    }

    /** #101 only "api" is left: a mode saved before (kv `connections`) is ignored. */
    fun select(provider: String, mode: String): Connections {
        if (PROVIDERS.none { it.first == provider }) throw NotFound("No provider called \"$provider\"")
        if (mode != MODE) throw BadRequest("$provider has no mode \"$mode\"", "keel runs every model on its API: the only mode is $MODE.")
        return connections()
    }

    /** Asks the engine to run "Reply with exactly: OK" on the provider's API. */
    fun test(provider: String): JsonNode {
        if (PROVIDERS.none { it.first == provider }) throw NotFound("No provider called \"$provider\"")
        val general = settings.general()
        val chosen = listOf(general.defaultModel, general.implementerModel, general.reviewerModel, general.cheaperModel)
            .firstOrNull { it.provider == provider }?.model
        // local models: no agent uses one yet, so there is no model to ask: say whether keel reaches each server instead
        if (provider == "local" && chosen == null) return testServers()
        val model = chosen ?: DEFAULT_MODELS[provider] ?: provider
        // #101 keel's harness runs every model on its API key, whatever mode was saved: the test does the same
        val body = mutableMapOf<String, Any?>("provider" to provider, "mode" to MODE, "model" to model)
        val secret = secrets.apiKey(provider)
        secret?.let { body["key"] = it }
        val res = engine.providerTest(body)
        // When the test fails, say what is wrong with the saved value's shape (never the value itself).
        val problem = if (res.path("ok").asBoolean(false) || secret == null || provider in ModelServerService.PROVIDERS) null else shapeProblem(provider, secret)
        return if (problem == null) res else (res as com.fasterxml.jackson.databind.node.ObjectNode).put("error", problem)
    }

    /** Every local model server: does keel reach it, and how many models it has. {ok, text, ms} or {ok: false, error}. */
    private fun testServers(): JsonNode {
        val eps = servers.local().endpoints
        val out = com.fasterxml.jackson.databind.node.JsonNodeFactory.instance.objectNode()
        if (eps.isEmpty()) return out.put("ok", false).put("ms", 0).put("error", "No local model server yet. Add one under Local models.")
        val results = eps.map { it.name to servers.testEndpoint(it.name) }
        val bad = results.filter { !it.second.path("ok").asBoolean(false) }
        out.put("ms", results.sumOf { it.second.path("ms").asLong(0) })
        return if (bad.isEmpty()) out.put("ok", true).put("text", results.joinToString(" · ") { (n, r) -> "$n: ${r.path("models").size()} models" })
        else out.put("ok", false).put("error", bad.joinToString(" ") { (n, r) -> "$n: ${r.path("error").asText("no answer")}" })
    }

    /** Plain checks on a saved key; the message never contains the value. */
    fun shapeProblem(provider: String, v: String): String? {
        val bad = v.filter { !(it.isLetterOrDigit() && it.code < 128) && it !in "-_.=" }
        return when {
            bad.isNotEmpty() -> "The saved API key has characters a token never has (${bad.toSet().joinToString("") { if (it.isWhitespace()) "␣" else it.toString() }}): " +
                "probably a copy with spaces, line breaks or • dots. Paste it again in one piece."
            provider == "claude" && !v.startsWith("sk-ant-") -> "The saved Anthropic API key does not start with sk-ant-. Check that you pasted the right key."
            else -> null
        }
    }

    companion object {
        /** #101 the one mode every provider has: its API (a key, a token or a model server). */
        const val MODE = "api"
        val PROVIDERS = listOf("fake" to "Fake (demo)", "claude" to "Claude", "codex" to "GPT / Codex", "copilot" to "Copilot",
            "vertex" to "Vertex AI", "local" to "Local models")
        val DEFAULT_MODELS = mapOf("fake" to "fake", "claude" to "sonnet", "codex" to "gpt-5", "copilot" to "gpt-5", "vertex" to "claude-sonnet-5-5")
        /** What "This machine" lists, in this order. */
        val MACHINE = listOf("node", "git", "java", MachineTools.DOCKER)
    }
}
