package keel.api.projects

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.KeelProperties
import org.slf4j.LoggerFactory
import org.springframework.boot.ApplicationArguments
import org.springframework.boot.ApplicationRunner
import org.springframework.core.annotation.Order
import org.springframework.stereotype.Component
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

/**
 * Registers projects at start:
 *  1. KEEL_WORKSPACE — itself if it is a git repo, else each child git repo;
 *  2. ~/.keel/projects.json (keel v1's registry), roots that exist;
 *  3. nothing found and nothing registered → $KEEL_DATA/demo when it exists (the engine sets it up).
 * Each project found is then scanned by the engine (its code graph index catches up with the code).
 */
@Component
@Order(10)
class ProjectScanner(
    private val props: KeelProperties,
    private val projects: ProjectService,
    private val mapper: ObjectMapper,
) : ApplicationRunner {
    private val log = LoggerFactory.getLogger(javaClass)

    override fun run(args: ApplicationArguments?) {
        if (!props.scanOnStart) return
        scan()
    }

    fun scan(): List<String> {
        val found = mutableListOf<Path>()
        if (props.workspace.isNotBlank()) {
            val ws = Paths.get(props.workspace).toAbsolutePath().normalize()
            if (isRepo(ws)) {
                found.add(ws)
            } else if (Files.isDirectory(ws)) {
                Files.list(ws).use { s -> s.filter { Files.isDirectory(it) && isRepo(it) }.sorted().forEach { found.add(it) } }
            }
        }
        found.addAll(fromV1Registry())
        if (found.isEmpty() && projects.rows().isEmpty()) {
            val demo = props.dataDir.resolve("demo")
            if (Files.isDirectory(demo)) found.add(demo)
        }
        // The launcher passes the host folder's name, so a mounted /workspace is not called "workspace".
        val wsName = System.getenv("KEEL_PROJECT_NAME")?.takeIf { it.isNotBlank() }
        val wsPath = props.workspace.takeIf { it.isNotBlank() }?.let { Paths.get(it).toAbsolutePath().normalize() }
        val ids = found.distinct().mapNotNull { root ->
            runCatching { if (root == wsPath && wsName != null) projects.registerWorkspace(root.toString(), wsName, scan = false) else projects.register(root.toString(), scan = false) }
                .onFailure { log.warn("could not register {}: {}", root, it.message) }
                .getOrNull()
        }.map { row -> projects.triggerScan(row); row.id }
        if (ids.isNotEmpty()) log.info("projects: {}", ids.joinToString())
        return ids
    }

    private fun isRepo(p: Path) = Files.exists(p.resolve(".git"))

    private fun fromV1Registry(): List<Path> {
        if (props.projectsFile.isBlank()) return emptyList()
        val f = Paths.get(props.projectsFile)
        if (!Files.isRegularFile(f)) return emptyList()
        return try {
            val node = mapper.readTree(f.toFile())
            node.get("projects")?.mapNotNull { it.get("root")?.asText() }
                ?.map { Paths.get(it) }
                ?.filter { Files.isDirectory(it) }
                ?: emptyList()
        } catch (e: Exception) {
            log.warn("could not read {}: {}", f, e.message)
            emptyList()
        }
    }
}
