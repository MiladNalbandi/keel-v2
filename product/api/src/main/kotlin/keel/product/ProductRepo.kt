package keel.product

import keel.api.common.BadRequest
import keel.api.common.KeelProperties
import keel.api.common.Proc
import keel.api.projects.ProjectRow
import keel.api.projects.ProjectService
import keel.api.addons.FeatureService
import keel.api.common.Conflict
import org.springframework.stereotype.Component
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

/**
 * The product repo: a small git repo in keel's data folder that holds every initiative's documents and every team's
 * pages as Markdown (reviewed like code, with history). keel registers it as the project "keel Product", so its stage
 * flows run like any flow and their gates show in the Inbox.
 */
@Component
class ProductRepo(private val props: KeelProperties, private val projects: ProjectService) {
    val root: Path get() = Paths.get(props.data).toAbsolutePath().normalize().resolve("keel-product")

    @Synchronized
    fun ensure(): ProjectRow {
        val r = root
        if (!Files.isDirectory(r.resolve(".git"))) {
            Files.createDirectories(r)
            git("init", "-q", "-b", "main")
            Files.writeString(r.resolve("README.md"), "# keel Product\n\nInitiatives (initiatives/INI-n/) and team pages (teams/<team>/knowledge/), " +
                "written by keel Product. Every change is a commit.\n")
            git("add", "README.md")
            git("commit", "-q", "-m", "keel Product: the product repo")
        }
        return projects.register(r.toString(), "keel Product", scan = false)
    }

    fun projectId(): String = ensure().id

    fun git(vararg args: String): String {
        val r = Proc.run(listOf("git", "-c", "user.name=keel", "-c", "user.email=keel@localhost", "-c", "commit.gpgsign=false", *args), root, 30)
        return r.out
    }

    private fun safe(rel: String): Path {
        val p = root.resolve(rel).normalize()
        if (!p.startsWith(root) || rel.contains("..")) throw BadRequest("Not a path in the product repo: $rel")
        return p
    }

    fun read(rel: String): String? = safe(rel).takeIf { Files.isRegularFile(it) }?.let { Files.readString(it) }

    /** Writes a file and commits it; returns the commit, or null when nothing changed. */
    @Synchronized
    fun write(rel: String, text: String, message: String): String? {
        ensure()
        val p = safe(rel)
        Files.createDirectories(p.parent)
        Files.writeString(p, if (text.endsWith("\n")) text else "$text\n")
        // A stage flow may commit in the engine at the same moment: git's index.lock makes one of them wait.
        repeat(6) { attempt ->
            var c = Proc.run(listOf("git", "add", "--", rel), root, 30)
            if (c.ok) c = Proc.run(listOf("git", "-c", "user.name=keel", "-c", "user.email=keel@localhost", "-c", "commit.gpgsign=false",
                "commit", "-q", "-m", message, "--", rel), root, 30)
            if (c.ok) return git("rev-parse", "HEAD").trim()
            if (!c.err.contains("index.lock")) return null
            Thread.sleep(250L * (attempt + 1))
        }
        return null
    }

    fun list(dir: String): List<String> {
        val d = safe(dir)
        if (!Files.isDirectory(d)) return emptyList()
        return Files.list(d).use { s -> s.filter { Files.isRegularFile(it) }.map { it.fileName.toString() }.sorted().toList() }
    }
}

/** Every product endpoint first: keel Product must be on (Settings › What this keel does). */
@Component
class ProductGuard(private val features: FeatureService) {
    fun on() {
        if (!features.partOn("product")) throw Conflict("keel Product is off", "Turn it on in Settings › What this keel does.")
    }
}
