package keel.api.stacks

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.Hash
import keel.api.common.KeelProperties
import keel.api.common.NotFound
import keel.api.common.Proc
import keel.api.common.Yaml
import keel.api.engine.EngineClient
import keel.api.engine.EngineError
import keel.api.projects.ProjectService
import keel.api.skills.SkillService
import org.springframework.stereotype.Service
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
    /**
     * keel 0.18.9 where it comes from: keel (content/stacks), keel pack (not installed), pack (a keel pack installed
     * here), marketplace, project (the project's own file).
     */
    val origin: String = source,
    /** Turned off for this project (`stacks: {ignore: [...]}` in .keel/config.yml): not used, whatever detection says. */
    val off: Boolean = false,
    /** The project added it, so DELETE removes it; keel's own are turned off instead ([why] says so). */
    val removable: Boolean = false,
    val why: String? = null,
    /** A marketplace install: {catalog, version, edited}. */
    val market: Map<String, Any?>? = null,
    /** The project's file, relative to its root (null for keel's own). */
    val file: String? = null,
)

data class NewStack(val name: String = "", val from: String? = null)

/** keel 0.18.9 a stack written here (the guided form gives `form`, KeelBot and MCP the YAML). */
data class StackText(val yaml: String? = null, val form: Map<String, Any?>? = null, val replace: Boolean = false)
data class StackOff(val off: Boolean = true)

private val LANG = Regex("^[a-z][a-z0-9+#-]{0,31}$")

private data class StackDef(val doc: Map<String, Any?>, val source: String, val file: Path) {
    val name: String get() = doc["name"].toString()
}

/** keel's stacks (content/stacks/NAME.yml and content/packs/NAME/stack.yml) and which ones a project matches. */
@Service
class StackService(
    private val props: KeelProperties,
    private val projects: ProjectService,
    private val mapper: ObjectMapper,
    private val engine: EngineClient,
    private val skills: SkillService,
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

    /** `stacks: {ignore: [...]}` in the project's .keel/config.yml (the engine writes it: runtime/stack_edit.py). 0.22.0
     *  plus this clone's own switches, <git common dir>/keel/stacks.json {off, on} (engine runtime/stacks.py local_file):
     *  a committed config.yml is not changed by a switch, so the tree stays clean. */
    private fun turnedOff(root: Path): Set<String> {
        val f = root.resolve(".keel/config.yml")
        val block = if (Files.isRegularFile(f)) runCatching { Yaml.readMap(Files.readString(f)) }.getOrNull()?.get("stacks") as? Map<*, *> else null
        val shared = block?.let { strings(it["ignore"]).toSet() }.orEmpty()
        val common = runCatching { Proc.run(listOf("git", "rev-parse", "--git-common-dir"), root, 10) }.getOrNull()
            ?.takeIf { it.ok }?.out?.trim()?.ifEmpty { null } ?: return shared
        val local = root.resolve(common).normalize().resolve("keel/stacks.json")
        val mine = if (Files.isRegularFile(local)) runCatching { mapper.readTree(Files.readString(local)) }.getOrNull() else null
        val off = mine?.path("off")?.mapNotNull { it.asText(null) }.orEmpty()
        val on = mine?.path("on")?.mapNotNull { it.asText(null) }.orEmpty()
        return (shared + off) - on.toSet()
    }

    /** .keel/stacks/.installed.json: the stacks a marketplace install saved. */
    private fun installed(root: Path): Map<String, Map<String, Any?>> {
        val f = root.resolve(".keel/stacks/.installed.json")
        if (!Files.isRegularFile(f)) return emptyMap()
        @Suppress("UNCHECKED_CAST")
        return runCatching { mapper.readValue(f.toFile(), Map::class.java) as Map<String, Any?> }.getOrDefault(emptyMap())
            .mapNotNull { (k, v) -> (v as? Map<*, *>)?.let { m -> k to m.entries.associate { it.key.toString() to it.value } } }.toMap()
    }

    private fun sha256(text: String): String =
        Hash.sha256Hex(text)

    private data class Origin(val origin: String, val removable: Boolean, val why: String?, val market: Map<String, Any?>?)

    /** Twin of the engine's stack_edit.origin. */
    private fun originOf(root: Path, d: StackDef, have: Map<String, Map<String, Any?>>, packNames: Set<String>): Origin = when (d.source) {
        "keel" -> Origin("keel", false, "It ships with keel and other projects may use it. Turn it off for this project instead.", null)
        "keel pack" -> Origin("keel pack", false, "It is not installed in this project: nothing to remove.", null)
        else -> {
            val mine = have[d.name]?.takeIf { it["source"] == "marketplace" }
            val base = root.resolve(".keel/stacks")
            when {
                mine != null -> {
                    val sha = mine["sha256"]?.toString()
                    val edited = sha != null && runCatching { sha256(Files.readString(d.file)) }.getOrNull() != sha
                    Origin("marketplace", true, null, mapOf("catalog" to mine["catalog"], "version" to mine["version"], "edited" to edited))
                }
                d.file.fileName.toString() == "stack.yml" && d.file.parent.parent == base && d.file.parent.fileName.toString() in packNames ->
                    Origin("pack", true, null, null)
                else -> Origin("project", true, null, null)
            }
        }
    }

    fun list(pid: String): List<Stack> {
        val root = projects.root(pid)
        val files = ProjectFiles(root)
        val off = turnedOff(root)
        val have = installed(root)
        val packNames = packs().map { it.name }.toSet()
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
            val o = originOf(root, d, have, packNames)
            @Suppress("UNCHECKED_CAST")
            Stack(
                name = doc["name"].toString(), lane = doc["lane"]?.toString(), source = d.source,
                // A stack the user put in the project is in use, whatever the files say.
                detected = d.source == "project" || detects(detect, files), detect = detect,
                layers = (doc["layers"] as? List<Map<String, Any?>>).orEmpty(), commands = commands, tools = tools,
                skills = (doc["skills"] as? Map<String, Any?>).orEmpty(),
                installable = d.source == "keel pack",
                origin = o.origin, off = doc["name"].toString() in off, removable = o.removable, why = o.why, market = o.market,
                file = if (d.source == "project") root.relativize(d.file).toString() else null,
            )
        }
    }

    /**
     * keel 0.18.28 the highlight.js languages of the stacks this project uses (detected or its own, not turned off), merged
     * by name: each stack's `languages:` list, else one per detect.extensions ending keel knows
     * (content/highlight-languages.json). The dashboard loads exactly these when the project opens.
     */
    fun languages(pid: String): Map<String, Any?> {
        val root = projects.root(pid)
        val files = ProjectFiles(root)
        val off = turnedOff(root)
        val table = highlightTable()
        val out = linkedMapOf<String, MutableMap<String, Any?>>()
        for (d in defs(root)) {
            @Suppress("UNCHECKED_CAST")
            val detect = d.doc["detect"] as? Map<String, Any?> ?: emptyMap()
            if (d.name in off || !(d.source == "project" || detects(detect, files))) continue
            for ((name, exts) in languagesOf(d.doc, table)) {
                val cur = out.getOrPut(name) { mutableMapOf("name" to name, "ext" to mutableListOf<String>(), "stacks" to mutableListOf<String>()) }
                @Suppress("UNCHECKED_CAST")
                (cur["ext"] as MutableList<String>).let { l -> exts.filter { it !in l }.forEach { l.add(it) } }
                @Suppress("UNCHECKED_CAST")
                (cur["stacks"] as MutableList<String>).let { l -> if (d.name !in l) l.add(d.name) }
            }
        }
        return mapOf("languages" to out.values.toList())
    }

    private fun highlightTable(): Map<String, String> = runCatching {
        @Suppress("UNCHECKED_CAST")
        (mapper.readValue(Files.readString(props.contentDir.resolve("highlight-languages.json")), Map::class.java)["extensions"]
            as? Map<String, Any?>).orEmpty().mapValues { it.value.toString() }
    }.getOrDefault(emptyMap())

    private fun languagesOf(doc: Map<String, Any?>, table: Map<String, String>): List<Pair<String, List<String>>> {
        val own = (doc["languages"] as? List<*>)?.mapNotNull { x ->
            val m = x as? Map<*, *> ?: return@mapNotNull null
            val name = m["name"]?.toString()?.takeIf { LANG.matches(it) } ?: return@mapNotNull null
            val exts = strings(m["ext"]).map { it.removePrefix(".").lowercase() }.filter { it.isNotBlank() }
            if (exts.isEmpty()) null else name to exts
        }
        if (!own.isNullOrEmpty()) return own
        @Suppress("UNCHECKED_CAST")
        val exts = strings((doc["detect"] as? Map<String, Any?>)?.get("extensions")).map { it.removePrefix(".").lowercase() }
        return exts.mapNotNull { e -> table[e]?.let { it to e } }.groupBy({ it.first }, { it.second }).map { (k, v) -> k to v }
    }

    fun get(pid: String, name: String): Stack = list(pid).firstOrNull { it.name == name } ?: throw NotFound("No stack called \"$name\"")

    /** One stack with the text of its YAML file (keel's own: as keel ships it). */
    fun detail(pid: String, name: String): JsonNode {
        val st = get(pid, name)
        val root = projects.root(pid)
        val file = defs(root).first { it.name == name }.file
        val out = mapper.valueToTree<ObjectNode>(st)
        out.put("yaml", runCatching { Files.readString(file) }.getOrDefault(""))
        return out
    }

    // ---- keel 0.18.9 the engine owns the stack files (runtime/stack_edit.py); the api gives it the project's folder

    /** The engine's refusal as the api's answer, with its `errors` next to error and hint. */
    private fun pass(call: () -> JsonNode): JsonNode = try {
        call()
    } catch (e: EngineError) {
        val errors = e.body?.path("errors")?.takeIf { it.isArray }
        throw ApiException(e.status, e.message, e.hint, errors?.let { mapOf("errors" to mapper.convertValue(it, List::class.java)) })
    }

    private fun stackName(name: String): String {
        if (!Regex("^[a-z0-9][a-z0-9-]{0,63}$").matches(name)) throw BadRequest("$name is not a stack name", "Lower-case letters, digits and dashes.")
        return name
    }

    /** What keel reads in the project's files for a new stack (lane: api or web; empty: keel's guess). */
    fun suggest(pid: String, lane: String?): JsonNode {
        val l = lane?.trim()?.ifBlank { null }
        if (l != null && l !in setOf("api", "web")) throw BadRequest("lane is api or web")
        return pass { engine.get("/stacks/suggest?root={root}&lane={lane}", mapOf("root" to projects.root(pid).toString(), "lane" to l.orEmpty())) }
    }

    /** Check a stack (its YAML, or the guided form's answers): errors, warnings, the review, the YAML; saves nothing. The
     *  skills it names that this project does not have are `skills_missing`. */
    fun check(pid: String, body: StackText): JsonNode {
        if ((body.yaml?.length ?: 0) > MAX_BYTES) throw BadRequest("A stack is at most ${MAX_BYTES / 1024} KB.")
        val out = pass {
            engine.post("/stacks/check", mapOf("root" to projects.root(pid).toString(), "yaml" to body.yaml, "form" to body.form,
                "replace" to body.replace))
        } as ObjectNode
        val have = skills.list(pid).map { it.id }.toSet()
        val named = out.path("review").path("skills").mapNotNull { it.asText(null) }
        out.set<JsonNode>("skills_missing", mapper.valueToTree(named.filter { it !in have }))
        return out
    }

    /** keel's check of a stack without a project (the marketplace's preview of keel's own): {ok, errors, warnings, review}. */
    fun checkAlone(yaml: String): JsonNode = pass { engine.post("/stacks/check", mapOf("yaml" to yaml)) }

    /** Save a stack (after the engine's check) as .keel/stacks/<name>.yml: {stack, path, replaced, warnings}. */
    fun save(pid: String, body: StackText, source: String = "project", market: Map<String, Any?>? = null): JsonNode {
        val yaml = body.yaml ?: throw BadRequest("Send the stack's YAML", "POST .../stacks/check turns the form into YAML.")
        if (yaml.length > MAX_BYTES) throw BadRequest("A stack is at most ${MAX_BYTES / 1024} KB.")
        val r = pass {
            engine.post("/stacks/save", mapOf("root" to projects.root(pid).toString(), "yaml" to yaml, "replace" to body.replace,
                "source" to source, "market" to market))
        }
        val out = mapper.createObjectNode()
        out.set<JsonNode>("stack", mapper.valueToTree(get(pid, r.path("name").asText())))
        out.put("path", r.path("path").asText())
        out.put("replaced", r.path("replaced").asBoolean(false))
        out.set<JsonNode>("warnings", r.path("check").path("warnings"))
        return out
    }

    /** Remove the project's own stack (its file, or the folder an installed pack left); keel's own: 409, turn it off. */
    fun remove(pid: String, name: String): JsonNode =
        pass { engine.post("/stacks/remove", mapOf("root" to projects.root(pid).toString(), "name" to stackName(name))) }

    /** Turn a stack off (or on) for the project: {name, off, all (every stack turned off there)}. */
    fun setOff(pid: String, name: String, off: Boolean): JsonNode =
        pass { engine.post("/stacks/off", mapOf("root" to projects.root(pid).toString(), "name" to stackName(name), "off" to off)) }

    /** keel's own stacks as keel ships them (the marketplace's "From keel"): content/stacks and content/packs. */
    fun library(): List<Map<String, Any?>> = shipped().map { d ->
        @Suppress("UNCHECKED_CAST")
        val detect = d.doc["detect"] as? Map<String, Any?> ?: emptyMap()
        mapOf("name" to d.name, "lane" to d.doc["lane"]?.toString(), "source" to d.source, "detect" to detect,
            "installable" to (d.source == "keel pack"), "yaml" to runCatching { Files.readString(d.file) }.getOrDefault(""))
    }

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

    /**
     * Does the project test a web layer of its own (0.18.12, the engine's runtime/stacks.py web_layer)? Yes with a web test
     * command (commands.web_test_*), a frontend folder that exists (frontend.dir, default apps/web), or a stack in use
     * whose lane is web. Without one the Code page counts server-rendered pages as the backend's, like the guard.
     */
    fun hasWebLayer(root: Path): Boolean {
        val f = root.resolve(".keel/config.yml")
        val cfg = if (Files.isRegularFile(f)) runCatching { Yaml.readMap(Files.readString(f)) }.getOrNull().orEmpty() else emptyMap()
        val cmds = cfg["commands"] as? Map<*, *> ?: emptyMap<String, Any>()
        if (WEB_TEST_KEYS.any { cmds[it]?.toString()?.isNotBlank() == true }) return true
        val front = ((cfg["frontend"] as? Map<*, *>)?.get("dir")?.toString() ?: "apps/web").trim().trim('/').removePrefix("./")
        if (front !in setOf("", ".") && ".." !in front.split('/') && Files.isDirectory(root.resolve(front))) return true
        val off = turnedOff(root)
        val files = ProjectFiles(root)
        return defs(root).any { d ->
            val name = d.doc["name"].toString()
            @Suppress("UNCHECKED_CAST")
            val detect = d.doc["detect"] as? Map<String, Any?> ?: emptyMap()
            val web = d.doc["lane"]?.toString() == "web" || WEB_TEST_KEYS.any { (d.doc["commands"] as? Map<*, *>)?.containsKey(it) == true }
            web && name !in off && (d.source == "project" || detects(detect, files))
        }
    }

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
        val WEB_TEST_KEYS = listOf("web_test_ac", "web_test_module", "web_test_paths")
        /** A stack file is at most 64 KB (the marketplace's limit too). */
        const val MAX_BYTES = 64 * 1024
    }
}
