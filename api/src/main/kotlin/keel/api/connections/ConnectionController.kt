package keel.api.connections

import com.fasterxml.jackson.databind.JsonNode
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

data class SelectMode(val mode: String = "")
data class SecretValue(val value: String = "")

@RestController
@RequestMapping("/api")
class ConnectionController(private val connections: ConnectionService, private val secrets: SecretService) {

    @GetMapping("/connections")
    fun list(): Connections = connections.connections()

    @PutMapping("/connections/{provider}")
    fun select(@PathVariable provider: String, @RequestBody body: SelectMode): Connections = connections.select(provider, body.mode)

    @PostMapping("/connections/{provider}/test")
    fun test(@PathVariable provider: String): JsonNode = connections.test(provider)

    @PutMapping("/secrets/{name}")
    fun putSecret(@PathVariable name: String, @RequestBody body: SecretValue): Map<String, String> =
        mapOf("hint" to secrets.put(name, body.value))

    @DeleteMapping("/secrets/{name}")
    fun deleteSecret(@PathVariable name: String): Map<String, Boolean> {
        secrets.delete(name)
        return mapOf("ok" to true)
    }
}
