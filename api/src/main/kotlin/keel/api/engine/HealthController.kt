package keel.api.engine

import com.fasterxml.jackson.databind.JsonNode
import org.springframework.beans.factory.ObjectProvider
import org.springframework.boot.info.BuildProperties
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController

/** `version` is keel v2's own version (the api's build-info; "dev" when the build wrote none). */
data class Health(val ok: Boolean, val engine: Boolean, val version: String, val fake: Boolean)

@RestController
class HealthController(private val engine: EngineClient, build: ObjectProvider<BuildProperties>) {
    private val version: String = build.ifAvailable?.version ?: "dev"

    @GetMapping("/api/health")
    fun health(): Health {
        val h = engine.health()
        return Health(
            ok = true,
            engine = h?.get("ok")?.asBoolean() == true,
            version = version,
            fake = h?.get("fake")?.asBoolean() ?: false,
        )
    }

    /** The engine's model list per provider (`{ [provider]: {id, label}[] }`), passed through. */
    @GetMapping("/api/providers/models")
    fun models(): JsonNode = engine.models()
}
