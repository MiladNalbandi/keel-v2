package keel.api.agents

import keel.api.common.KeelProperties
import keel.api.common.Yaml
import org.springframework.stereotype.Component
import java.nio.file.Files
import java.nio.file.Path

data class AgentDef(
    val id: String,
    val label: String,
    val about: String,
    val tools: List<String>,
    val model: String?,
    val prompt: String,
    val phases: List<String>,
)

/** Splits a markdown file with YAML front matter into (front matter, body). */
object FrontMatter {
    fun split(text: String): Pair<Map<String, Any?>, String> {
        val t = text.replace("\r\n", "\n")
        if (!t.startsWith("---\n")) return emptyMap<String, Any?>() to t
        val end = t.indexOf("\n---", 4)
        if (end < 0) return emptyMap<String, Any?>() to t
        val fm = Yaml.readMap(t.substring(4, end)) ?: emptyMap()
        val body = t.substring(end + 4).removePrefix("\n").trimStart('\n')
        return fm to body
    }

    fun list(v: Any?): List<String> = when (v) {
        null -> emptyList()
        is List<*> -> v.mapNotNull { it?.toString()?.trim() }.filter { it.isNotEmpty() }
        else -> v.toString().split(',').map { it.trim() }.filter { it.isNotEmpty() }
    }
}

/** keel v1's agents (one markdown file per agent in $KEEL_HOME/agents) and which keel phases each one works in. */
@Component
class AgentCatalog(private val props: KeelProperties) {

    fun defaults(): List<AgentDef> {
        val dir = props.keelHome.resolve("agents")
        if (!Files.isDirectory(dir)) return emptyList()
        return Files.list(dir).use { s -> s.filter { it.toString().endsWith(".md") }.sorted().toList() }
            .mapNotNull { read(it) }
    }

    fun find(id: String): AgentDef? = defaults().firstOrNull { it.id == id }

    private fun read(file: Path): AgentDef? = runCatching {
        val (fm, body) = FrontMatter.split(Files.readString(file))
        val id = fm["name"]?.toString() ?: file.fileName.toString().removeSuffix(".md")
        AgentDef(
            id = id,
            label = id,
            about = fm["description"]?.toString() ?: "",
            tools = FrontMatter.list(fm["tools"]),
            model = fm["model"]?.toString(),
            prompt = body.trim(),
            phases = PHASES[id] ?: emptyList(),
        )
    }.getOrNull()

    /** True when an agent can change files (it has Write or Edit). */
    fun writes(id: String, customTools: List<String>? = null): Boolean {
        val tools = customTools ?: find(id)?.tools ?: return id in WRITERS
        return tools.any { it.equals("Write", true) || it.equals("Edit", true) || it.contains("write", true) }
    }

    companion object {
        val PHASES: Map<String, List<String>> = mapOf(
            "explorer" to listOf("spec", "contract"),
            "test-author" to listOf("red"),
            "implementer" to listOf("green"),
            "ac-reviewer" to listOf("gate"),
            "code-reviewer" to listOf("integration"),
            "security-auditor" to listOf("security"),
            "dependency-triager" to listOf("security"),
            "e2e-author" to listOf("e2e"),
            "reviewer" to listOf("ship"),
            "librarian" to listOf("memory"),
            "reproducer" to listOf("bug-repro"),
            "investigator" to listOf("bug-investigate"),
            "setup-doctor" to listOf("preflight"),
            "arch-surveyor" to listOf("preflight"),
            "hunter" to listOf("hunt-sweep"),
            "prover" to listOf("hunt-prove"),
            "bulk-reader" to listOf("any"),
            "lane-runner" to listOf("any"),
        )
        val REVIEWERS = setOf("ac-reviewer", "code-reviewer", "reviewer", "security-auditor")
        val WRITERS = setOf("implementer", "test-author", "e2e-author", "lane-runner", "reproducer", "librarian", "prover")
    }
}
