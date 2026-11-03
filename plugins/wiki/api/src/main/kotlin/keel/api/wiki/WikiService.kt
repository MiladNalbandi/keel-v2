package keel.api.wiki

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.common.Yaml
import keel.api.knowledge.KnowledgeService
import keel.api.knowledge.Layout
import keel.api.projects.ProjectService
import keel.api.workflows.Step
import keel.api.workflows.Workflow
import keel.api.workflows.WorkflowService
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Path

/**
 * One page of the wiki. id, title and status are what every keel client reads; the rest (wiki order, docs/plugins)
 * helps a client group and sort the menu, and an old client ignores it:
 *   kind     overview | knowledge | spec | decision | runbook | workflow
 *   path     the file the page is read from (knowledge, specs, decisions)
 *   number   the number in the file name ("101" for SPEC-101-x.md, "0001" for 0001-x.md), for a stable order
 *   label    the title without that number ("Wave fallback" for "SPEC-101: Wave fallback")
 *   updated  when the file last changed (ISO)
 *   also     the same file in another of keel's folders (a mixed project's docs/specs and .keel/specs): shown once
 *   source   a workflow's origin (keel | yours | marketplace), addon its add-on ("product": keel Product's) and
 *   basedOn  the workflow it is based on: the menu groups the workflows by purpose and tags the ones not keel's own
 */
data class WikiItem(
    val id: String, val title: String, val status: String? = null,
    val kind: String? = null, val path: String? = null, val number: String? = null, val label: String? = null,
    val updated: String? = null, val also: List<String>? = null,
    val source: String? = null, val addon: String? = null, val basedOn: String? = null,
)
data class WikiSection(val id: String, val title: String, val items: List<WikiItem>)
/** notice: a problem with where keel's files are (an external layout whose folder keel cannot see); layout: its name. */
/** notes: 0.18.57 the project notes' switch (KnowledgeService.notes): off, the menu says so with Build them. */
data class Wiki(val sections: List<WikiSection>, val notice: String? = null, val layout: String? = null,
                val notes: keel.api.knowledge.Notes? = null)
data class WikiPage(val id: String, val title: String, val markdown: String, val meta: Map<String, Any?>)

/**
 * The Wiki plugin's pages (plugins/wiki): the knowledge base, a page for every workflow, the runbook and the
 * decisions of a project, read only. It keeps keel's package (keel.api.wiki), so keel's component scan finds it when
 * its jar is on loader.path (docs/plugins/11-step3-contract.md). The same answers as keel 0.15.1's KnowledgeService:
 * which knowledge sections are written, missing or stale is still keel's (KnowledgeService.knowledge).
 */
@Service
class WikiService(
    private val projects: ProjectService,
    private val knowledge: KnowledgeService,
    private val workflows: WorkflowService,
    private val mapper: ObjectMapper,
) {
    fun wiki(pid: String): Wiki {
        val root = projects.root(pid)
        val lay = Layout.resolve(root)
        val sections = knowledge.knowledge(root)
        val kb = sections.map { k ->
            val rel = lay.knowledgeFile(root, k.id)
            val written = k.status != "missing"
            val heading = if (written) heading(root.resolve(rel)) else null
            WikiItem("kb:${k.id}", k.id.replaceFirstChar { c -> c.uppercase() }, k.status, kind = "knowledge",
                path = rel.takeIf { written }, label = heading ?: humanize(k.id), updated = if (written) mtime(root.resolve(rel)) else null)
        }
        // the knowledge folder's index.md ("What an agent needs to know about …") is the wiki's overview
        val overview = lay.knowledgeDirs.map { "$it/index.md" }.firstOrNull { regular(root.resolve(it)) }?.let { rel ->
            val f = root.resolve(rel)
            listOf(WikiItem("kb:index", title(f), "written", kind = "overview", path = rel, label = title(f), updated = mtime(f)))
        }.orEmpty()
        val wfs = workflows.list(pid).map {
            WikiItem("wf:${it.id}", it.name, kind = "workflow", label = it.name, source = it.source, addon = it.addon, basedOn = it.basedOn)
        }
            .sortedWith(compareBy(String.CASE_INSENSITIVE_ORDER) { it.title })
        val adrs = lay.adrDirs.flatMap { d -> listMd(root.resolve(d)).filter { inside(root, it) && !isIndex(it) }.map { "$d/${it.fileName}" } }
            .distinctBy { it.substringAfterLast('/') }
            .map { rel -> doc("adr:${rel.substringAfterLast('/')}", "decision", root, rel) }
            .sortedWith(ORDER)
        val specs = specFiles(root, lay).groupBy { it.substringAfterLast('/') }.values
            .map { same -> doc("spec:${same[0]}", "spec", root, same[0]).copy(also = same.drop(1).ifEmpty { null }) }
            .sortedWith(ORDER)
        return Wiki(
            listOf(
                WikiSection("specs", "Specs", specs),
                WikiSection("knowledge", "Knowledge", overview + kb),
                WikiSection("workflows", "Workflows", wfs),
                WikiSection("runbook", "Runbook", listOf(WikiItem("runbook", "How to run this project", kind = "runbook", label = "How to run this project"))),
                WikiSection("decisions", "Decisions", adrs),
            ),
            lay.missing,
            lay.name,
            knowledge.notes(root, sections),
        )
    }

    /** The project's spec files without a folder's README.md or index.md (a list of the specs, not a spec). */
    internal fun specFiles(root: Path, lay: Layout = Layout.resolve(root)): List<String> =
        knowledge.specFiles(root, lay).filterNot { isIndex(Path.of(it)) }

    private fun doc(id: String, kind: String, root: Path, rel: String): WikiItem {
        val f = root.resolve(rel)
        val name = f.fileName.toString().removeSuffix(".md")
        val title = title(f)
        val number = NUMBER.find(name)?.groupValues?.get(1)
        return WikiItem(id, title, kind = kind, path = rel, number = number, label = label(title, number), updated = mtime(f))
    }

    fun page(pid: String, id: String): WikiPage {
        val root = projects.root(pid)
        return when {
            id.startsWith("kb:") -> {
                val name = id.removePrefix("kb:")
                if (!Regex("^[A-Za-z0-9_-]+$").matches(name)) throw BadRequest("Bad page id")
                val rel = Layout.resolve(root).knowledgeFile(root, name)
                val f = root.resolve(rel)
                val section = knowledge.knowledge(root).firstOrNull { it.id == name }
                if (!regular(f)) {
                    // 0.18.57 the project notes are opt-in: off, the page says so instead of "not written yet"
                    if (!knowledge.notes(root).on) {
                        WikiPage(id, humanize(name), "Project notes are off. keel's agents read the code itself. Build them " +
                            "with **Build them** in the Wiki's menu (the knowledge-refresh flow); that turns them on.", mapOf("status" to "off"))
                    } else {
                        WikiPage(id, humanize(name), "This section is not written yet. Run the init flow, or ask the librarian to write it.", mapOf("status" to "missing"))
                    }
                } else {
                    WikiPage(id, title(f), Files.readString(f), mapOf(
                        "path" to rel, "status" to section?.status, "words" to section?.words,
                        "cites" to section?.cites, "updated" to mtime(f),
                    ))
                }
            }
            id.startsWith("wf:") -> {
                val wf = workflows.get(id.removePrefix("wf:"))
                WikiPage(id, wf.name, workflowMarkdown(wf), mapOf("version" to wf.version, "keel_rules" to wf.keelRules, "steps" to wf.steps.size, "source" to wf.source))
            }
            id == "runbook" -> runbook(root)
            id.startsWith("adr:") -> {
                val name = id.removePrefix("adr:")
                if (name.contains('/') || name.contains("..")) throw BadRequest("Bad page id")
                // 0.21.3 a symlinked decision, or one in a folder that links outside the project, is not read (as the list skips it)
                val rel = Layout.resolve(root).adrDirs.map { "$it/$name" }.firstOrNull { inside(root, root.resolve(it)) }
                    ?: throw NotFound("No decision called $name")
                val f = root.resolve(rel)
                WikiPage(id, title(f), Files.readString(f), mapOf("path" to rel, "updated" to mtime(f)))
            }
            id.startsWith("spec:") -> {
                val rel = id.removePrefix("spec:")
                // every spec file, a folder's README too: an old link still opens
                if (rel.split('/').any { it == ".." } || rel !in knowledge.specFiles(root)) throw NotFound("No spec called $rel", "Ids look like spec:.keel/specs/<file>.md.")
                val f = root.resolve(rel)
                WikiPage(id, title(f), Files.readString(f), mapOf("path" to rel, "acs" to countAcs(f), "updated" to mtime(f)))
            }
            else -> throw NotFound("No wiki page \"$id\"", "Ids look like kb:architecture, spec:<path>, wf:<id>, runbook or adr:<file>.")
        }
    }

    fun workflowMarkdown(wf: Workflow): String = buildString {
        appendLine("# ${wf.name}")
        appendLine()
        wf.basedOn?.let { appendLine("Based on **$it**.") }
        appendLine(if (wf.keelRules) "keel rules are **on**: locked steps stay in the flow." else "keel rules are **off**.")
        appendLine()
        appendLine("| # | Step | Kind | Who | Notes |")
        appendLine("|---|---|---|---|---|")
        wf.steps.forEachIndexed { i, s -> appendLine("| ${i + 1} | ${s.name.ifBlank { s.id }} | ${s.kind} | ${who(s)} | ${notes(s)} |") }
        val gates = wf.steps.filter { it.kind == "gate" }
        if (gates.isNotEmpty()) {
            appendLine()
            appendLine("## Where it asks you")
            gates.forEach { g -> appendLine("- **${g.name}**" + (g.back?.let { " — send back goes to `$it`" } ?: "")) }
        }
    }

    private fun who(s: Step) = when (s.kind) {
        "agent", "parallel" -> s.agent ?: "—"
        "code" -> s.action ?: "code"
        "gate" -> "you"
        else -> "—"
    }

    private fun notes(s: Step) = listOfNotNull(
        "per AC".takeIf { s.perAc == true }, "locked".takeIf { s.locked }, s.phase?.let { "phase $it" },
        s.parallel?.let { "× $it" }, s.no?.let { "no → $it" },
    ).joinToString(", ")

    private fun runbook(root: Path): WikiPage {
        val ladder = root.resolve(".keel/ladder.json")
        if (Files.isRegularFile(ladder)) {
            val node = runCatching { mapper.readTree(ladder.toFile()) }.getOrNull()
            val rungs = (node?.get("rungs") ?: node?.get("steps") ?: node)?.takeIf { it.isArray }
            if (rungs != null) {
                val md = buildString {
                    appendLine("# How to run this project")
                    appendLine()
                    appendLine("From the setup ladder (`.keel/ladder.json`).")
                    appendLine()
                    rungs.forEachIndexed { i, r ->
                        val name = r.get("name")?.asText() ?: r.get("id")?.asText() ?: "step ${i + 1}"
                        val cmd = r.get("cmd")?.asText() ?: r.get("command")?.asText()
                        val ok = r.get("ok")?.asBoolean()
                        appendLine("${i + 1}. **$name**" + (cmd?.let { " — `$it`" } ?: "") + (ok?.let { if (it) " ✓" else " ✗" } ?: ""))
                    }
                }
                return WikiPage("runbook", "How to run this project", md, mapOf("from" to ".keel/ladder.json"))
            }
        }
        Layout.resolve(root).runbooks.firstOrNull()?.let { rel ->
            return WikiPage("runbook", "How to run this project", Files.readString(root.resolve(rel)), mapOf("from" to rel))
        }
        val cfg = root.resolve(".keel/config.yml").takeIf { Files.isRegularFile(it) }?.let { Yaml.readMap(Files.readString(it)) }
        val commands = (cfg?.get("commands") as? Map<*, *>)?.filterValues { it != null && it.toString().isNotBlank() }
        val md = if (commands.isNullOrEmpty()) {
            "# How to run this project\n\nNo runbook yet. The init flow writes one when it proves the setup ladder."
        } else buildString {
            appendLine("# How to run this project")
            appendLine()
            appendLine("Commands from `.keel/config.yml`:")
            appendLine()
            commands.forEach { (k, v) -> appendLine("- **$k** — `$v`") }
        }
        return WikiPage("runbook", "How to run this project", md, mapOf("from" to if (commands.isNullOrEmpty()) null else ".keel/config.yml"))
    }

    // ---- small file helpers (the same as keel's KnowledgeService has for its own reads) ----

    private fun listMd(dir: Path): List<Path> =
        if (!Files.isDirectory(dir)) emptyList()
        // a symlinked .md is skipped: a cloned repo could link docs/specs/x.md to a file outside it
        else Files.list(dir).use { s -> s.filter { it.toString().endsWith(".md") && Files.isRegularFile(it) && !Files.isSymbolicLink(it) }.sorted().toList() }

    internal fun countAcs(f: Path) = runCatching { Regex("\\bAC-\\d+").findAll(Files.readString(f)).map { it.value }.distinct().count() }.getOrDefault(0)

    private fun mtime(p: Path): String? = runCatching { Time.iso(Files.getLastModifiedTime(p).toMillis()) }.getOrNull()

    /** The file's first "# " heading, else its name in words ("v1.1.0-plugin-substrate" → "V1.1.0 plugin substrate"). */
    internal fun title(f: Path): String = heading(f) ?: humanize(f.fileName.toString().removeSuffix(".md"))

    private fun heading(f: Path): String? = runCatching {
        Files.readAllLines(f).firstOrNull { it.startsWith("# ") }?.removePrefix("# ")?.trim()?.ifBlank { null }
    }.getOrNull()

    private fun regular(f: Path) = Files.isRegularFile(f) && !Files.isSymbolicLink(f)

    /**
     * 0.21.3 a regular file, no symlink, whose real folder is in the project (or, in keel v1's external layout, in the
     * folder .keel links to). A cloned repo could link docs/adr (or a file in it) to a folder outside it.
     */
    internal fun inside(root: Path, f: Path): Boolean {
        if (!regular(f)) return false
        val real = runCatching { f.parent.toRealPath() }.getOrNull() ?: return false
        val bases = listOfNotNull(runCatching { root.toRealPath() }.getOrNull(), runCatching { root.resolve(".keel").toRealPath() }.getOrNull())
        return bases.any { real.startsWith(it) }
    }

    companion object {
        /** The number a spec or decision file starts with: SPEC-101-x, ADR_0002-x, 001-x, 0004 (not v1.1.0-x). */
        val NUMBER = Regex("^(?:(?:spec|adr)[\\s_-]*)?(\\d+)(?![.\\d])", RegexOption.IGNORE_CASE)

        /** Numbered first, by number; then the rest by title. */
        val ORDER: Comparator<WikiItem> = compareBy<WikiItem>({ it.number == null }, { it.number?.toBigInteger() })
            .thenComparing({ it.label ?: it.title }, String.CASE_INSENSITIVE_ORDER)

        fun isIndex(f: Path): Boolean = f.fileName.toString().lowercase().let { it == "readme.md" || it == "index.md" }

        /** "kebab_case-name" → "Kebab case name". */
        fun humanize(name: String): String =
            name.replace(Regex("[-_]+"), " ").trim().replaceFirstChar { it.uppercase() }.ifBlank { name }

        /** The title without its leading number: "SPEC-101: Wave fallback", "ADR 0001 — Use X", "001 — Quotas" → the words. */
        fun label(title: String, number: String?): String {
            if (number == null) return title
            val m = Regex("^(?:(?:spec|adr)[\\s_-]*)?(\\d+)\\s*[:.\u2014\u2013-]\\s*(.+)$", RegexOption.IGNORE_CASE).find(title.trim()) ?: return title
            return if (m.groupValues[1].toBigInteger() == number.toBigInteger()) m.groupValues[2].trim() else title
        }
    }
}
