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
    /** keel v1 dashboard port (`keel dashboard`), reverse-proxied at /keel-v1/. */
    val dashboardPort: Int = 7391,
    /** Start keel v1's dashboard when the api is ready, and start it again if it stops (on in the container). */
    val dashboardAutostart: Boolean = false,
    /** Let flows on real projects run with the fake model without asking (tests). The demo project always may. */
    val fakeOnRealProjects: Boolean = false,
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
    /** keel v2's own skills (spec-clarify, spec-writing): KEEL_V2_SKILLS, else /opt/keel-v2/skills, else ../skills (dev). */
    val v2Skills: Path by lazy {
        val env = System.getenv("KEEL_V2_SKILLS")
        if (!env.isNullOrBlank()) return@lazy Paths.get(env).toAbsolutePath().normalize()
        listOf("/opt/keel-v2/skills", "../skills", "../../skills").map { Paths.get(it).toAbsolutePath().normalize() }
            .firstOrNull { Files.isDirectory(it.resolve("spec-clarify")) } ?: Paths.get("/opt/keel-v2/skills")
    }

    val keelHome: Path by lazy {
        if (home.isNotBlank()) return@lazy Paths.get(home).toAbsolutePath().normalize()
        val candidates = listOf("/opt/keel", "../keel", "../../keel").map { Paths.get(it).toAbsolutePath().normalize() }
        candidates.firstOrNull { Files.isDirectory(it.resolve("agents")) } ?: candidates.first()
    }
}
