package keel.api.pluginhost

import keel.api.common.NotFound
import org.springframework.core.io.FileSystemResource
import org.springframework.core.io.Resource
import org.springframework.http.HttpHeaders
import org.springframework.http.MediaType
import org.springframework.http.MediaTypeFactory
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RestController
import java.nio.file.Path

/** GET /api/plugin-host: the plugin SDK, the mode, the plugins that loaded and the ones left out. */
data class PluginHostInfo(val sdk: Int, val mode: String, val plugins: List<PluginSummary>, val problems: List<PluginProblem>)

data class PluginSummary(val name: String, val title: String, val version: String, val source: String, val parts: List<String>)

data class Restarting(val restarting: Boolean)

@RestController
class PluginHostController(private val host: PluginHost, private val restarts: PluginRestart) {
    @GetMapping("/api/plugin-host")
    fun get(): PluginHostInfo = PluginHostInfo(
        PluginHost.SDK, host.mode, host.plugins.map { PluginSummary(it.name, it.title, it.version, it.source, it.parts()) }, host.problems,
    )

    /** 409 unless keel-start runs keel; else 202, and the api ends with code 75 a moment later. */
    @PostMapping("/api/plugin-host/restart")
    fun restart(): ResponseEntity<Restarting> {
        restarts.restart()
        return ResponseEntity.accepted().body(Restarting(true))
    }
}

/**
 * A plugin's web files: /plugins/<name>/<version>/web/<path> is `<dir>/web/<path>` of that resolved plugin. A url
 * holds the version, so the files never change and the browser keeps them for a year. Anything else is a 404.
 */
@RestController
class PluginWebController(private val host: PluginHost) {
    @GetMapping("/plugins/{name}/{version}/web/{*path}")
    fun file(@PathVariable name: String, @PathVariable version: String, @PathVariable path: String): ResponseEntity<Resource> {
        val file = host.webFile(name, version, path) ?: throw NotFound("No such plugin file", "/plugins/$name/$version/web$path")
        return ResponseEntity.ok()
            .header(HttpHeaders.CACHE_CONTROL, CACHE)
            .contentType(contentType(file))
            .body(FileSystemResource(file))
    }

    private fun contentType(file: Path): MediaType {
        val name = file.fileName.toString()
        return when (name.substringAfterLast('.', "").lowercase()) {
            "js", "mjs" -> JAVASCRIPT
            "css" -> CSS
            else -> MediaTypeFactory.getMediaType(name).orElse(MediaType.APPLICATION_OCTET_STREAM)
        }
    }

    companion object {
        const val CACHE = "public, max-age=31536000, immutable"
        val JAVASCRIPT = MediaType("text", "javascript")
        val CSS = MediaType("text", "css")
    }
}
