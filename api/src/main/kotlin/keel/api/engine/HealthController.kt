package keel.api.engine

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.KeelHome
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController

data class KeelInfo(val version: String?, val home: String)
data class Health(val ok: Boolean, val engine: Boolean, val keel: KeelInfo, val fake: Boolean)

@RestController
class HealthController(private val engine: EngineClient, private val home: KeelHome) {
    @GetMapping("/api/health")
    fun health(): Health {
        val h = engine.health()
        return Health(
            ok = true,
            engine = h?.get("ok")?.asBoolean() == true,
            keel = KeelInfo(home.version(), home.path.toString()),
            fake = h?.get("fake")?.asBoolean() ?: false,
        )
    }

    /** The engine's model list per provider (`{ [provider]: {id, label}[] }`), passed through. */
    @GetMapping("/api/providers/models")
    fun models(): JsonNode = engine.models()
}
