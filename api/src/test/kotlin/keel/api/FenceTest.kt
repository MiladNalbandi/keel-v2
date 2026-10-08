package keel.api

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.fail
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.createDirectories
import kotlin.io.path.extension
import kotlin.io.path.isDirectory
import kotlin.io.path.nameWithoutExtension
import kotlin.io.path.readLines
import kotlin.io.path.readText
import kotlin.io.path.writeText

/**
 * The fence (docs/plugins/07-step1-contract.md §9): keel's core must not use a package that becomes a plugin.
 * Today's couplings sit in src/test/resources/fence-allowlist.txt. That list may only shrink: a new coupling fails,
 * and so does a line whose import is gone. keel.product is never allowed in core. No Spring: it only reads the sources.
 */
class FenceTest {
    private val sources = Path.of("src/main/kotlin/keel/api")
    private val allowlist = Path.of("src/test/resources/fence-allowlist.txt")
    private val allowlistName = "api/src/test/resources/fence-allowlist.txt"

    @Test
    fun `core never uses keel Product`() {
        val bad = Fence.scan(sources).forbidden
        if (bad.isNotEmpty()) {
            fail(
                "Core must never use keel Product (keel.product) or a part that moved to plugins/: they are plugins. Use an extension point, see ${Fence.GUIDE}.\n" +
                    bad.joinToString("\n") { "  $it" },
            )
        }
    }

    @Test
    fun `the CI-CD plugin's classes are not in core, not even in its package keel_api_plugins`() {
        // the scan skips keel.api.plugins (Database and Git still live there), so look for the names themselves
        val names = Regex("""\bCi(Service|Controller|FixBody)\b""")
        val found = Files.walk(sources).use { paths -> paths.filter { it.extension == "kt" }.sorted().toList() }
            .filter { names.containsMatchIn(Fence.withoutComments(it.readText())) }
        assertThat(found).describedAs("plugins/ci/api has them; core never uses them").isEmpty()
    }

    @Test
    fun `core uses no plugin beyond the allowlist`() {
        assertThat(sources.isDirectory()).describedAs("run the tests from api/: $sources is missing").isTrue()
        val problems = Fence.problems(Fence.scan(sources).couplings, Fence.readAllowlist(allowlist), allowlistName)
        if (problems.isNotEmpty()) fail(problems.joinToString("\n\n"))
    }

    @Test
    fun `the allowlist is sorted and names no forbidden package`() {
        val allowed = Fence.readAllowlist(allowlist)
        assertThat(allowed).describedAs("Keep $allowlistName sorted, one line per coupling.").isEqualTo(allowed.toSortedSet().toList())
        val never = allowed.filter { Fence.under(it.substringAfter(" -> "), Fence.FORBIDDEN) }
        assertThat(never).describedAs("keel.product can never be in the allowlist").isEmpty()
    }

    // ---------- the scanner itself, on a tiny tree ----------

    @Test
    fun `the scanner finds imports and full names in code, not in comments`(@TempDir root: Path) {
        write(
            root, "keel/api/flow/FlowService.kt",
            """
            package keel.api.flow

            import keel.api.tasks.TaskService
            import keel.api.review.ReviewAiService as Review
            import keel.api.jira.*
            import keel.api.projects.ProjectService
            import keel.api.pluginhost.PluginHost

            // keel.api.helper.InComment is only named in a comment
            /* keel.api.helper.InBlock /* nested */ keel.api.helper.StillInBlock */
            class FlowService(private val plugins: keel.api.plugins.PluginService) {
                val s = "http://x // not a comment §{keel.api.helper.Helper.NAME} and §{listOf("a").map { "§it" }}"
                val c = '"'
                fun f() = keel.product.ProductService.X
            }
            """.trimIndent().replace('§', '$'),
        )
        write(root, "keel/api/review/ReviewService.kt", "package keel.api.review\n\nimport keel.product.X\nimport keel.api.helper.Y\n")
        write(root, "keel/api/KeelApiApplication.kt", "package keel.api\n\nfun main() = Unit\n")

        val found = Fence.scan(root)

        assertThat(found.couplings).containsExactlyInAnyOrder(
            "keel.api.flow.FlowService -> keel.api.helper.Helper",
            "keel.api.flow.FlowService -> keel.api.plugins.PluginService",
            "keel.api.flow.FlowService -> keel.api.review.ReviewAiService",
        )
        // a plugin part may use anything; only core is checked. Tasks and Jira moved out: core may never use them again.
        assertThat(found.forbidden).containsExactlyInAnyOrder(
            "keel.api.flow.FlowService -> keel.api.jira.*",
            "keel.api.flow.FlowService -> keel.api.tasks.TaskService",
            "keel.api.flow.FlowService -> keel.product.ProductService",
        )
    }

    @Test
    fun `new and stale lines get a clear message`(@TempDir root: Path) {
        val list = root.resolve("list.txt")
        list.writeText("# header\n\n  b  ->  keel.api.tasks.T   # why\nc->keel.api.jira.J\n")
        val allowed = Fence.readAllowlist(list)
        assertThat(allowed).containsExactly("b -> keel.api.tasks.T", "c -> keel.api.jira.J")

        val problems = Fence.problems(setOf("a -> keel.api.helper.H", "b -> keel.api.tasks.T"), allowed, "list.txt")
        assertThat(problems).hasSize(2)
        assertThat(problems[0]).contains("Core must not use a plugin", Fence.GUIDE, "  a -> keel.api.helper.H")
        assertThat(problems[1]).contains("Remove these lines from list.txt: the coupling is gone", "  c -> keel.api.jira.J")
        assertThat(Fence.problems(allowed.toSet(), allowed, "list.txt")).isEmpty()
    }

    private fun write(root: Path, name: String, text: String) {
        val file = root.resolve(name)
        file.parent.createDirectories()
        file.writeText(text)
    }
}

/** Reads Kotlin sources and finds where core uses a plugin package. */
private object Fence {
    const val GUIDE = "docs/plugins/02-plugin-package.md"

    // The packages that become plugins (docs/plugins/01-today.md). keel.api.repo and keel.api.knowledge stay core.
    val PLUGINS = listOf("keel.api.helper", "keel.api.plugins", "keel.api.review")

    // Core never uses these, not even today. They can never be in the allowlist. The parts that moved out keep their
    // package, and core never uses it again (step 3): keel.api.map (plugins/map), keel.api.wiki (plugins/wiki),
    // keel.api.tasks (plugins/tasks), keel.api.jira (plugins/jira), and the Ci* classes of CI/CD (plugins/ci, in keel's
    // package keel.api.plugins).
    val FORBIDDEN = listOf(
        "keel.product", "keel.api.map", "keel.api.wiki", "keel.api.tasks", "keel.api.jira",
        "keel.api.plugins.CiController", "keel.api.plugins.CiFixBody", "keel.api.plugins.CiService",
    )

    data class Found(val couplings: Set<String>, val forbidden: Set<String>)

    private val packageLine = Regex("""^\s*package\s+([\w.]+)""", RegexOption.MULTILINE)
    private val importLine = Regex("""^\s*import\s+(\w+(?:\.\w+)*(?:\.\*)?)""", RegexOption.MULTILINE)
    private val headerLine = Regex("""^\s*(?:package|import)\s.*$""", RegexOption.MULTILINE)
    private val fullName = Regex("""(?<![\w.])keel(?:\.\w+)+""")

    fun under(name: String, prefixes: List<String>) = prefixes.any { name == it || name.startsWith("$it.") }

    /** keel.api.tasks.TaskStatus.DONE -> keel.api.tasks.TaskStatus: the name up to its first class. */
    private fun classOf(name: String): String {
        val parts = name.split(".")
        val i = parts.indexOfFirst { it.firstOrNull()?.isUpperCase() == true }
        return if (i < 0) name else parts.take(i + 1).joinToString(".")
    }

    /** Couplings and forbidden uses of the core files under [root], as "importer -> used" lines. */
    fun scan(root: Path, plugins: List<String> = PLUGINS, forbidden: List<String> = FORBIDDEN): Found {
        val couplings = sortedSetOf<String>()
        val bad = sortedSetOf<String>()
        val files = Files.walk(root).use { paths -> paths.filter { it.extension == "kt" }.sorted().toList() }
        for (file in files) {
            val code = withoutComments(file.readText())
            val pkg = packageLine.find(code)?.groupValues?.get(1).orEmpty()
            if (under(pkg, plugins)) continue // a plugin part may use core and other plugins
            val importer = listOf(pkg, file.nameWithoutExtension).filter { it.isNotEmpty() }.joinToString(".")
            // imports, plus full names written in the code (`keel.api.plugins.PluginService` as a type)
            val used = importLine.findAll(code).map { it.groupValues[1] } +
                fullName.findAll(headerLine.replace(code, "")).map { it.value }
            for (target in used.map(::classOf)) {
                when {
                    under(target, forbidden) -> bad += "$importer -> $target"
                    under(target, plugins) -> couplings += "$importer -> $target"
                }
            }
        }
        return Found(couplings, bad)
    }

    /** The lines of an allowlist, without comments ('#') and blank lines, with one space around "->". */
    fun readAllowlist(file: Path): List<String> = file.readLines()
        .map { it.substringBefore('#').trim() }
        .filter { it.isNotEmpty() }
        .map { line -> line.split("->").joinToString(" -> ") { it.trim() } }

    /** What is wrong, in plain words: new couplings, and allowlist lines whose import is gone. */
    fun problems(found: Set<String>, allowed: List<String>, allowlistName: String): List<String> {
        val new = (found - allowed.toSet()).sorted()
        val gone = (allowed.toSet() - found).sorted()
        return listOfNotNull(
            new.takeIf { it.isNotEmpty() }?.let { lines ->
                "Core must not use a plugin. Use an extension point instead, see $GUIDE.\n" +
                    "New uses from core of a plugin:\n" + lines.joinToString("\n") { "  $it" }
            },
            gone.takeIf { it.isNotEmpty() }?.let { lines ->
                "Remove these lines from $allowlistName: the coupling is gone (the list only shrinks).\n" +
                    lines.joinToString("\n") { "  $it" }
            },
        )
    }

    /** The source without its comments. Strings stay: a "${keel.api.x.Y}" template is code. */
    fun withoutComments(src: String): String {
        val out = StringBuilder(src.length)
        // where we are: 'q' in a "string", 'r' in a """raw string""", '{' in a ${ } template (or a { } inside one)
        val inside = ArrayDeque<Char>()
        var i = 0
        while (i < src.length) {
            val c = src[i]
            val top = inside.lastOrNull()
            val inString = top == 'q' || top == 'r'
            val step = when {
                inString && src.startsWith("\${", i) -> { inside.addLast('{'); 2 }
                top == 'q' && c == '\\' -> 2
                top == 'q' && c == '"' -> { inside.removeLast(); 1 }
                top == 'r' && src.startsWith("\"\"\"", i) -> { inside.removeLast(); 3 }
                inString -> 1
                src.startsWith("//", i) -> { i = src.indexOf('\n', i).let { if (it < 0) src.length else it }; 0 }
                src.startsWith("/*", i) -> { i = endOfBlock(src, i); out.append(' '); 0 }
                src.startsWith("\"\"\"", i) -> { inside.addLast('r'); 3 }
                c == '"' -> { inside.addLast('q'); 1 }
                c == '\'' -> charLiteralLength(src, i)
                c == '{' && top == '{' -> { inside.addLast('{'); 1 }
                c == '}' && top == '{' -> { inside.removeLast(); 1 }
                else -> 1
            }
            val end = minOf(i + step, src.length)
            out.append(src, i, end)
            i = end
        }
        return out.toString()
    }

    /** Where a block comment that starts at [start] ends. Kotlin block comments nest. */
    private fun endOfBlock(src: String, start: Int): Int {
        var depth = 0
        var i = start
        while (i < src.length) {
            when {
                src.startsWith("/*", i) -> { depth++; i += 2 }
                src.startsWith("*/", i) -> { depth--; i += 2; if (depth == 0) return i }
                else -> i++
            }
        }
        return src.length
    }

    /** The length of a char literal like 'a', '"' or '\n' that starts at [start]. */
    private fun charLiteralLength(src: String, start: Int): Int {
        var j = start + 1
        while (j < src.length && src[j] != '\'' && src[j] != '\n') j += if (src[j] == '\\') 2 else 1
        return j - start + 1
    }
}
