package keel.api.common

import org.springframework.boot.context.properties.ConfigurationProperties
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

@ConfigurationProperties(prefix = "keel")
data class KeelProperties(
    val data: String = "./.data",
    val workspace: String = "",
    val home: String = "",
    val engineUrl: String = "http://127.0.0.1:8090",
    val internalToken: String = "",
    val secret: String = "",
    val projectsFile: String = "",
    val scanOnStart: Boolean = true,
) {
    /** Absolute data folder; created on first use. */
    val dataDir: Path by lazy {
        val p = Paths.get(data).toAbsolutePath().normalize()
        Files.createDirectories(p)
        p
    }

    /**
     * keel v1 home: KEEL_HOME, else /opt/keel, else a sibling `keel` checkout (dev).
     */
    val keelHome: Path by lazy {
        if (home.isNotBlank()) return@lazy Paths.get(home).toAbsolutePath().normalize()
        val candidates = listOf("/opt/keel", "../keel", "../../keel").map { Paths.get(it).toAbsolutePath().normalize() }
        candidates.firstOrNull { Files.isDirectory(it.resolve("agents")) } ?: candidates.first()
    }
}
