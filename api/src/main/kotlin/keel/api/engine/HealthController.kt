package keel.api.engine

import com.fasterxml.jackson.databind.JsonNode
import org.springframework.beans.factory.ObjectProvider
import org.springframework.boot.info.BuildProperties
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController

/** `native`: keel runs without Docker (`keel2 start --native` sets KEEL_NATIVE=1), so its agents run as the person.
 *  `version` is keel v2's own version (the api's build-info; "dev" when the build wrote none).
 *  keel 0.18.66 `latest`: a newer release keel's daily check found (updates/ReleaseCheck), else null; keel_health says it. */
data class Health(val ok: Boolean, val engine: Boolean, val version: String, val fake: Boolean, val native: Boolean = false,
                  val latest: String? = null)

@RestController
class HealthController(
    private val engine: EngineClient,
    build: ObjectProvider<BuildProperties>,
    private val servers: ObjectProvider<keel.api.connections.ModelServerService>,
    private val releases: ObjectProvider<keel.api.updates.ReleaseCheck>,
) {
    private val version: String = build.ifAvailable?.version ?: "dev"
    private val native: Boolean = System.getenv("KEEL_NATIVE") == "1"

    @GetMapping("/api/health")
    fun health(): Health {
        val h = engine.health()
        return Health(
            ok = true,
            engine = h?.get("ok")?.asBoolean() == true,
            version = version,
            fake = h?.get("fake")?.asBoolean() ?: false,
            native = native,
            latest = runCatching { releases.ifAvailable?.newerVersion() }.getOrNull(),
        )
    }

    /**
     * keel 0.18.22 the stack setup (engine runtime/stack_setup.py), passed through: what keel installs for the projects'
     * stacks (setup.apt) and how far it is: {state: idle|running|ready|failed|missing, mode, line, busy, missing, failed}.
     * Not in /api/health, which answers without a login: the line names projects.
     */
    @GetMapping("/api/setup")
    fun setup(): JsonNode = engine.get("/setup/status")

    /** The engine's model list per provider (`{ [provider]: {id, label}[] }`), passed through. 0.18.46 with local model
     *  servers or Vertex AI set up, the engine gets their connections and lists those providers too. */
    @GetMapping("/api/providers/models")
    fun models(): JsonNode {
        val keys = servers.ifAvailable?.catalogKeys().orEmpty()
        return if (keys.isEmpty()) engine.models() else engine.post("/providers/models", mapOf("keys" to keys))
    }
}
