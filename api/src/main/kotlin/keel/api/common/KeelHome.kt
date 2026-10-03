package keel.api.common

import com.fasterxml.jackson.databind.ObjectMapper
import org.springframework.stereotype.Component
import java.nio.file.Files
import java.nio.file.Path

/** keel v1 install (KEEL_HOME): version and well-known paths. */
@Component
class KeelHome(private val props: KeelProperties, private val mapper: ObjectMapper) {
    val path: Path get() = props.keelHome

    fun version(): String? {
        val f = path.resolve(".claude-plugin/plugin.json")
        if (!Files.isRegularFile(f)) return null
        return runCatching { mapper.readTree(f.toFile()).get("version")?.asText() }.getOrNull()
    }

    fun bin(): Path = path.resolve("bin/keel")
    fun mcpServer(): Path = path.resolve("mcp/server.js")
}
