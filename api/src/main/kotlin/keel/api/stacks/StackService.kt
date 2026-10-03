package keel.api.stacks

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.KeelHome
import keel.api.common.NotFound
import keel.api.common.Proc
import keel.api.common.Yaml
import keel.api.projects.ProjectService
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
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
    /** A keel pack that is not installed in this project yet (POST .../stacks/{name}/install). */
    val installable: Boolean = false,
)

data class NewStack(val name: String = "", val from: String? = null)

private data class StackDef(val doc: Map<String, Any?>, val source: String, val file: Path) {
    val name: String get() = doc["name"].toString()
}

/** keel v1 stack packs (stacks/NAME.yml and packs/stacks/NAME.yml) and which ones a project matches. */
@Service
class StackService(private val home: KeelHome, private val projects: ProjectService, private val mapper: ObjectMapper) {

    private fun read(f: Path, source: String): StackDef? =
        runCatching { Yaml.readMap(Files.readString(f)) }.getOrNull()?.takeIf { it["name"] != null }?.let { StackDef(it, source, f) }

    private fun load(dir: Path, source: String): List<StackDef> {
        if (!Files.isDirectory(dir)) return emptyList()
        return Files.list(dir).use { s -> s.filter { it.toString().endsWith(".yml") }.sorted().toList() }.mapNotNull { read(it, source) }
    }

    private fun shipped(): List<StackDef> = load(home.path.resolve("stacks"), "keel") + load(home.path.resolve("packs/stacks"), "keel pack")

    /**
     * Packs in `<root>/.keel/stacks`, the way keel v1 finds them: a `<name>.yml`, a `<dir>/stack.yml`,
     * or yml files in `<dir>` or `<dir>/stacks` (what `keel packs add --project` leaves). Up to 3 levels deep.
     */
    private fun projectDefs(root: Path): List<StackDef> {
        val dir = root.resolve(".keel/stacks")
        if (!Files.isDirectory(dir)) return emptyList()
        return runCatching {
            Files.walk(dir, 3).use { w -> w.filter { Files.isRegularFile(it) && it.toString().endsWith(".yml") }.sorted().toList() }
        }.getOrDefault(emptyList()).mapNotNull { read(it, "project") }
    }

    /** keel v1 order: this project's packs beat the ones keel ships. */
    private fun defs(root: Path): List<StackDef> {
        val project = projectDefs(root).distinctBy { it.name }
        val names = project.map { it.name }.toSet()
        return shipped().filter { it.name !in names } + project
    }

    fun list(pid: String): List<Stack> {
        val root = projects.root(pid)
        val files = ProjectFiles(root)
        return defs(root).map { d ->
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
                // A stack the user put in the project is in use, whatever the files say.
                detected = d.source == "project" || detects(detect, files), detect = detect,
                layers = (doc["layers"] as? List<Map<String, Any?>>).orEmpty(), commands = commands, tools = tools,
                skills = (doc["skills"] as? Map<String, Any?>).orEmpty(),
                installable = d.source == "keel pack",
            )
        }
    }

    fun get(pid: String, name: String): Stack = list(pid).firstOrNull { it.name == name } ?: throw NotFound("No stack called \"$name\"")

    /**
     * Copies the closest keel stack YAML to `<root>/.keel/stacks/<name>.yml` and renames it.
     * Closest = `from` when given, else a stack with the same name, else the first detected one.
     */
    fun create(pid: String, name: String, from: String?): Stack {
        val root = projects.root(pid)
        if (!Regex("^[a-z0-9][a-z0-9-]{0,40}$").matches(name)) {
            throw BadRequest("The stack name is not valid", "Use lower case letters, digits and dashes, like my-api.")
        }
        val target = root.resolve(".keel/stacks/$name.yml")
        if (Files.exists(target) || projectDefs(root).any { it.name == name }) throw Conflict("This project already has a stack called \"$name\"")
        val all = shipped()
        val source = when {
            !from.isNullOrBlank() -> all.firstOrNull { it.name == from } ?: throw NotFound("No keel stack called \"$from\"", "GET /stacks lists them.")
            else -> all.firstOrNull { it.name == name }
                ?: run { val files = ProjectFiles(root); all.firstOrNull { d -> @Suppress("UNCHECKED_CAST") detects((d.doc["detect"] as? Map<String, Any?>).orEmpty(), files) } }
                ?: all.firstOrNull()
                ?: throw NotFound("keel has no stack to copy from", "Check KEEL_HOME: it needs stacks/*.yml.")
        }
        val text = Files.readString(source.file)
        val nameLine = Regex("(?m)^name:.*$")
        val renamed = if (nameLine.containsMatchIn(text)) text.replaceFirst(nameLine, "name: $name") else "name: $name\n$text"
        Files.createDirectories(target.parent)
        Files.writeString(target, renamed)
        return get(pid, name)
    }

    /** Runs `keel packs add $KEEL_HOME/packs --project` in the repo, then returns the stack. */
    fun install(pid: String, name: String): Stack {
        val root = projects.root(pid)
        val stack = get(pid, name)
        if (stack.source == "project") return stack
        if (!stack.installable) throw Conflict("\"$name\" ships with keel", "It is always there; nothing to install.")
        if (!home.installed()) throw ApiException(HttpStatus.SERVICE_UNAVAILABLE, "keel is not installed at ${home.path}", "Set KEEL_HOME.")
        val r = Proc.run(home.command("packs", "add", home.path.resolve("packs").toString(), "--project"), root, 120)
        val output = (r.out + r.err).trim()
        // keel refuses when the pack folder is already there: that is "installed" for us.
        if (!r.ok && !output.contains("already exists")) {
            val why = if (r.timedOut) "keel packs add took too long." else output.lines().filter { it.isNotBlank() }.takeLast(5).joinToString("\n")
            throw ApiException(HttpStatus.BAD_GATEWAY, "keel could not install the pack", why)
        }
        return get(pid, name)
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

    @PostMapping("/api/projects/{pid}/stacks")
    fun create(@PathVariable pid: String, @RequestBody body: NewStack): Stack = stacks.create(pid, body.name.trim(), body.from)

    @PostMapping("/api/projects/{pid}/stacks/{name}/install")
    fun install(@PathVariable pid: String, @PathVariable name: String): Stack = stacks.install(pid, name)
}
