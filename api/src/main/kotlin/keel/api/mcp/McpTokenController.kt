package keel.api.mcp

import keel.api.common.Forbidden
import keel.api.common.KeelProperties
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RestController

@RestController
class McpTokenController(private val tokens: McpTokens, private val props: KeelProperties) {

    /** The token of one MCP level, only for a caller with the internal token (keel2 mcp, the engine). */
    @PostMapping("/api/mcp/token")
    fun token(@RequestHeader("X-Keel-Token", required = false) internal: String?, @RequestBody body: McpTokenRequest): Map<String, String> {
        val expected = props.token
        if (expected.isEmpty() || internal == null || !McpTokens.same(internal, expected)) {
            throw Forbidden("Wrong or missing X-Keel-Token", "Only keel2 mcp and keel's engine can get an MCP level token.")
        }
        return mapOf("level" to body.level, "token" to tokens.tokenFor(body.level))
    }
}
