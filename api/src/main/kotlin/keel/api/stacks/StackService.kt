package keel.api.stacks

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.KeelHome
import keel.api.common.Yaml
import keel.api.projects.ProjectService
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RestController
import java.nio.file.Files
import java.nio.file.Path

data class StackCommand(val name: String, val cmd: String)
data class StackTool(val name: String, val on: String?, val fail: String?)

data class Stack(
    val name: String,
    val lane: String?,
    val source: String,
    val detected: Boolean,
    val detect: Map<String, Any?>,
    val layers: List<Map<String, Any?>>,
    val commands: List<StackCommand>,
    val tools: List<StackTool>,
    val skills: Map<String, Any?>,
)

private data class StackDef(val doc: Map<String, Any?>, val source: String)

/** keel v1 stack packs (stacks/NAME.yml and packs/stacks/NAME.yml) and which ones a project matches. */
@Service
class StackService(private val home: KeelHome, private val projects: ProjectService, private val mapper: ObjectMapper) {

    private fun defs(): List<StackDef> {
        fun load(dir: Path, source: String): List<StackDef> {
            if (!Files.isDirectory(dir)) return emptyList()
            return Files.list(dir).use { s -> s.filter { it.toString().endsWith(".yml") }.sorted().toList() }
                .mapNotNull { f -> Yaml.readMap(Files.readString(f))?.takeIf { it["name"] != null }?.let { StackDef(it, source) } }
        }
        return load(home.path.resolve("stacks"), "keel") + load(home.path.resolve("packs/stacks"), "keel pack")
    }

    fun list(pid: String): List<Stack> {
        val root = projects.root(pid)
        val files = ProjectFiles(root)
        return defs().map { d ->
            val doc = d.doc
            @Suppress("UNCHECKED_CAST")
            val detect = doc["detect"] as? Map<String, Any?> ?: emptyMap()
            @Suppress("UNCHECKED_CAST")
            val commands = (doc["commands"] as? Map<String, Any?>).orEmpty().map { (k, v) -> StackCommand(k, v.toString()) }
            @Suppress("UNCHECKED_CAST")
            val tools = (doc["tools"] as? Map<String, Any?>).orEmpty().map { (k, v) ->
                val m = v as? Map<*, *> ?: emptyMap<String, Any>()
                StackTool(k, m["on"]?.toString(), m["fail"]?.toString())
            }
            @Suppress("UNCHECKED_CAST")
            Stack(
                name = doc["name"].toString(), lane = doc["lane"]?.toString(), source = d.source,
                detected = detects(detect, files), detect = detect,
                layers = (doc["layers"] as? List<Map<String, Any?>>).orEmpty(), commands = commands, tools = tools,
                skills = (doc["skills"] as? Map<String, Any?>).orEmpty(),
            )
        }
    }

    private fun strings(v: Any?): List<String> = (v as? List<*>)?.mapNotNull { it?.toString() } ?: emptyList()

    /** `exclude_files` first; then any of files / package_deps / extensions. */
    fun detects(detect: Map<String, Any?>, files: ProjectFiles): Boolean {
        val exclude = strings(detect["exclude_files"])
        val wanted = strings(detect["files"])
        val deps = strings(detect["package_deps"])
        val exts = strings(detect["extensions"])
        if (wanted.isEmpty() && deps.isEmpty() && exts.isEmpty()) return false
        return files.dirs.any { dir ->
            if (exclude.any { Files.exists(dir.resolve(it)) }) return@any false
            wanted.any { Files.exists(dir.resolve(it)) } ||
                (deps.isNotEmpty() && files.packageDeps(dir).any { it in deps }) ||
                (exts.isNotEmpty() && files.hasExtension(dir, exts))
        }
    }

    /** The root and its direct child folders: keel v1 probes a lane directory, which is usually one of them. */
    inner class ProjectFiles(val root: Path) {
        val dirs: List<Path> = listOf(root) + runCatching {
            Files.list(root).use { s ->
                s.filter { Files.isDirectory(it) && !it.fileName.toString().startsWith(".") && it.fileName.toString() !in SKIP }.sorted().toList()
            }
        }.getOrDefault(emptyList())

        fun packageDeps(dir: Path): Set<String> {
            val f = dir.resolve("package.json")
            if (!Files.isRegularFile(f)) return emptySet()
            return runCatching {
                val n = mapper.readTree(f.toFile())
                listOf("dependencies", "devDependencies").flatMap { k -> n.get(k)?.fieldNames()?.asSequence()?.toList() ?: emptyList() }.toSet()
            }.getOrDefault(emptySet())
        }

        fun hasExtension(dir: Path, exts: List<String>): Boolean = runCatching {
            Files.walk(dir, 6).use { w ->
                w.filter { p -> p.none { it.toString() in SKIP } }
                    .limit(20_000)
                    .anyMatch { p -> Files.isRegularFile(p) && exts.any { p.fileName.toString().endsWith(it) } }
            }
        }.getOrDefault(false)
    }

    companion object {
        val SKIP = setOf("node_modules", "build", ".git", ".venv", "dist", "target", ".gradle")
    }
}

@RestController
class StackController(private val stacks: StackService) {
    @GetMapping("/api/projects/{pid}/stacks")
    fun list(@PathVariable pid: String): List<Stack> = stacks.list(pid)
}
