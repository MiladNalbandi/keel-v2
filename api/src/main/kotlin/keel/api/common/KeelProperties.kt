package keel.api.common

import org.springframework.boot.context.properties.ConfigurationProperties
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

@ConfigurationProperties(prefix = "keel")
data class KeelProperties(
    val data: String = "./.data",
    val workspace: String = "",
    /** v0.15.7 the folders keel may look in for repos (KEEL_FOLDERS, ':' between them like PATH); blank = [workspace] alone. */
    val folders: String = "",
    /** keel v2's content folder (KEEL_CONTENT); blank = found, see [contentDir]. */
    val content: String = "",
    val engineUrl: String = "http://127.0.0.1:8090",
    val internalToken: String = "",
    val secret: String = "",
    val scanOnStart: Boolean = true,
    /** Let flows on real projects run with the fake model without asking (tests). The demo project always may. */
    val fakeOnRealProjects: Boolean = false,
    /** The Python that runs keel's own MCP server (`-m keel_engine.mcp`): blank = the image's engine venv, else python3 (dev). */
    val mcpPython: String = "",
    /** Where `keel2 start --with-keel-v1 <path>` mounts a keel v1 checkout; its MCP server is offered only when it is there. */
    val keelV1Optional: String = "/opt/keel-v1-optional",
) {
    /** The command for keel's builtin MCP server. */
    val mcpPythonCommand: String by lazy {
        mcpPython.ifBlank { ENGINE_PYTHON.takeIf { Files.isExecutable(Paths.get(it)) } ?: "python3" }
    }

    /**
     * v0.15.7 the folders to find repos in: KEEL_FOLDERS (the main project folder first, then the ones `keel2 add` mounted),
     * else KEEL_WORKSPACE alone; absolute, normalized, no doubles.
     */
    val folderRoots: List<Path> by lazy {
        val list = folders.split(':').map { it.trim() }.filter { it.isNotEmpty() }.ifEmpty { listOf(workspace.trim()).filter { it.isNotEmpty() } }
        list.map { Paths.get(it).toAbsolutePath().normalize() }.distinct()
    }

    /** Absolute data folder; created on first use. */
    val dataDir: Path by lazy {
        val p = Paths.get(data).toAbsolutePath().normalize()
        Files.createDirectories(p)
        p
    }

    /**
     * keel v2's own content (agents, skills, stacks, packs, templates): KEEL_CONTENT, else /opt/keel-v2/content,
     * else ../content or ../../content from the api folder (dev).
     */
    val contentDir: Path by lazy {
        if (content.isNotBlank()) return@lazy Paths.get(content).toAbsolutePath().normalize()
        listOf("/opt/keel-v2/content", "../content", "../../content").map { Paths.get(it).toAbsolutePath().normalize() }
            .firstOrNull { Files.isDirectory(it.resolve("agents")) } ?: Paths.get("/opt/keel-v2/content")
    }

    companion object {
        const val ENGINE_PYTHON = "/opt/engine/.venv/bin/python"
    }
}
