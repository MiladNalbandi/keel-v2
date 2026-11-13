package keel.api.connections

import keel.api.settings.Model
import keel.api.settings.SettingsService
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Service

/**
 * B8a: the first real model key (an Anthropic or OpenAI API key) becomes the model of every agent when nobody picked one
 * yet. Before, Connections said "Ready, logged in" while every agent stayed on the fake model
 * until the person found "Use for all agents". A model someone saved in Settings (General: default_model,
 * implementer_model, reviewer_model) is never replaced; only the ones still unset follow the login. Not with "Local models
 * only" on for the whole keel, and off with keel.first-login-default=false (the API tests, where one test's login must not
 * change the next test's models).
 */
@Service
class FirstModelLogin(
    private val settings: SettingsService,
    @Value("\${keel.first-login-default:true}") var enabled: Boolean,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    /** A secret was saved under [name] from Connections: when it is a model key, [saved] it. */
    fun savedSecret(name: String) {
        val key = SecretService.KEY_NAMES.entries.firstOrNull { it.value == name }?.key
        // GITHUB_TOKEN is GitHub's token too (pull requests, Tasks): saving it does not pick GitHub Models
        if (key != null && key != "copilot") saved(key, ConnectionService.MODE)
    }

    /** The model login of [provider] in [mode] is saved: the agents' models still unset become its default model. */
    fun saved(provider: String, mode: String): Model? {
        if (!enabled) return null
        val model = ConnectionService.DEFAULT_MODELS[provider]?.takeIf { provider != "fake" } ?: return null
        if (settings.general().localOnly) return null
        val unset = MODEL_KEYS.filter { !settings.isGeneralSet(it) }
        if (unset.isEmpty()) return null
        val m = Model(provider, mode, model)
        runCatching { settings.updateGeneral(unset.associateWith { m }) }
            .onFailure { log.warn("could not make {} the agents' model: {}", provider, it.message); return null }
        log.info("first {} login: the agents use {} {} ({})", provider, provider, model, unset.joinToString())
        return m
    }

    companion object {
        val MODEL_KEYS = listOf("default_model", "implementer_model", "reviewer_model")
    }
}
