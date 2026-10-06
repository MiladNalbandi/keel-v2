package keel.api.stacks

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.KeelProperties
import keel.api.common.NotFound
import keel.api.common.Yaml
import keel.api.projects.ProjectService
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path

data class StackCommand(val name: String, val cmd: String)
/** One `tools:` entry of a stack (v0.4.1: the engine runs them; see docs/CONTRACT.md "lint and static checks"). */
data class StackTool(
    val name: String,
    val on: String?,
    val fail: String?,
    val description: String? = null,
    val kind: String? = null,
    val match: String? = null,
    /** `<name>: false` in a stack file: the tool is turned off. */
    val off: Boolean = false,
)

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

/** keel's stacks (content/stacks/NAME.yml and content/packs/NAME/stack.yml) and which ones a project matches. */
@Service
class StackService(
    private val props: KeelProperties,
    private val projects: ProjectService,
    private val mapper: ObjectMapper,
) {

    private fun read(f: Path, source: String): StackDef? =
        runCatching { Yaml.readMap(Files.readString(f)) }.getOrNull()?.takeIf { it["name"] != null }?.let { StackDef(it, source, f) }

    private fun load(dir: Path, source: String): List<StackDef> {
        if (!Files.isDirectory(dir)) return emptyList()
        return Files.list(dir).use { s -> s.filter { it.toString().endsWith(".yml") }.sorted().toList() }.mapNotNull { read(it, source) }
    }

    private fun shipped(): List<StackDef> = load(props.contentDir.resolve("stacks"), "keel") + packs()

    /** One folder per pack: `content/packs/<name>/stack.yml`, with its skills and templates beside it. */
    private fun packs(): List<StackDef> {
        val dir = props.contentDir.resolve("packs")
        if (!Files.isDirectory(dir)) return emptyList()
        return Files.list(dir).use { s -> s.map { it.resolve("stack.yml") }.filter { Files.isRegularFile(it) }.sorted().toList() }
            .mapNotNull { read(it, "keel pack") }
    }

    /**
     * Packs in `<root>/.keel/stacks`: a `<name>.yml`, a `<dir>/stack.yml` (what [install] leaves),
     * or yml files in `<dir>` or `<dir>/stacks`. Up to 3 levels deep.
     */
    private fun projectDefs(root: Path): List<StackDef> {
        val dir = root.resolve(".keel/stacks")
        if (!Files.isDirectory(dir)) return emptyList()
        return runCatching {
            Files.walk(dir, 3).use { w -> w.filter { Files.isRegularFile(it) && it.toString().endsWith(".yml") }.sorted().toList() }
        }.getOrDefault(emptyList()).mapNotNull { read(it, "project") }
    }

    /** This project's packs beat the ones keel ships. */
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
                // `on:` is the word; a YAML 1.1 reader may have turned the key into true
                val on = (m["on"] ?: m[true] ?: m["true"])?.toString() ?: if (v is Map<*, *>) "manual" else null
                StackTool(
                    k, on, m["fail"]?.toString() ?: if (v is Map<*, *>) "warn" else null, m["description"]?.toString(),
                    m["kind"]?.toString() ?: if (v is Map<*, *>) "check" else null, m["match"]?.toString(), off = v == false,
                )
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
                ?: throw NotFound("keel has no stack to copy from", "Check KEEL_CONTENT: it needs stacks/*.yml.")
        }
        val text = Files.readString(source.file)
        val nameLine = Regex("(?m)^name:.*$")
        val renamed = if (nameLine.containsMatchIn(text)) text.replaceFirst(nameLine, "name: $name") else "name: $name\n$text"
        Files.createDirectories(target.parent)
        Files.writeString(target, renamed)
        return get(pid, name)
    }

    /**
     * Copies keel's pack folder `<content>/packs/<pack>` (stack.yml, skills, templates) to `<root>/.keel/stacks/<pack>`,
     * then returns the stack. A folder that is already there counts as installed and is left as it is.
     */
    fun install(pid: String, name: String): Stack {
        val root = projects.root(pid)
        val stack = get(pid, name)
        if (stack.source == "project") return stack
        if (!stack.installable) throw Conflict("\"$name\" ships with keel", "It is always there; nothing to install.")
        val pack = packs().firstOrNull { it.name == name }?.file?.parent
            ?: throw NotFound("No keel pack called \"$name\"", "GET /stacks lists them.")
        val target = root.resolve(".keel/stacks").resolve(pack.fileName.toString())
        if (!Files.exists(target, LinkOption.NOFOLLOW_LINKS)) copyTree(pack, target)
        return get(pid, name)
    }

    /** Copies a folder with its files and sub-folders; links are not followed (a pack has none). */
    private fun copyTree(from: Path, to: Path) {
        // Copied next to .keel/stacks first (never read as a pack), then moved in with one rename, so a half-copied
        // pack is never read as installed.
        val tmp = to.parent.resolveSibling(".installing-${to.fileName}")
        tmp.toFile().deleteRecursively()
        Files.walk(from).use { w ->
            w.filter { !Files.isSymbolicLink(it) }.forEach { src ->
                val dest = tmp.resolve(from.relativize(src).toString())
                if (Files.isDirectory(src)) Files.createDirectories(dest) else Files.copy(src, dest)
            }
        }
        Files.createDirectories(to.parent)
        Files.move(tmp, to)
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
