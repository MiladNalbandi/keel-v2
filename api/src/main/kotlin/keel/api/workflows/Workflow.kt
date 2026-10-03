package keel.api.workflows

import com.fasterxml.jackson.annotation.JsonInclude
import com.fasterxml.jackson.annotation.JsonPropertyOrder
import keel.api.common.BadRequest
import keel.api.common.Yaml

@JsonInclude(JsonInclude.Include.NON_NULL)
data class Lane(val name: String = "", val sub: String? = null, val kind: String = "agent")

@JsonInclude(JsonInclude.Include.NON_NULL)
@JsonPropertyOrder("id", "kind", "name", "agent", "model", "phase", "action")
data class Step(
    val id: String = "",
    val kind: String = "agent",
    val name: String = "",
    val agent: String? = null,
    val model: String? = null,
    val phase: String? = null,
    val action: String? = null,
    val perAc: Boolean? = null,
    val parallel: Int? = null,
    val lanes: List<Lane>? = null,
    val back: String? = null,
    val no: String? = null,
    val lock: Boolean? = null,
    val maxTokens: Int? = null,
    val onLimit: String? = null,
    val tools: List<String>? = null,
    /** Agent lane metadata ("api" | "web"), set by the api from the agent's project override at flow start. */
    val lane: String? = null,
) {
    val locked: Boolean @com.fasterxml.jackson.annotation.JsonIgnore get() = lock == true
}

@JsonInclude(JsonInclude.Include.NON_NULL)
data class Budget(val maxTokens: Int? = null, val onLimit: String? = null)

/** What a workflow YAML file holds (also the library metadata keys, ignored by the engine). */
@JsonInclude(JsonInclude.Include.NON_NULL)
@JsonPropertyOrder("id", "name", "source", "version", "about", "est_tokens", "mcp", "based_on", "keel_rules", "budget", "steps")
data class WorkflowDoc(
    val id: String? = null,
    val name: String = "",
    val source: String? = null,
    val version: String? = null,
    val about: String? = null,
    val estTokens: Int? = null,
    val mcp: List<String>? = null,
    val basedOn: String? = null,
    val keelRules: Boolean = true,
    val budget: Budget? = null,
    val steps: List<Step> = emptyList(),
) {
    fun toYaml(): String = Yaml.mapper.writeValueAsString(this)

    companion object {
        val KINDS = setOf("agent", "code", "gate", "branch", "parallel")

        fun parse(yaml: String): WorkflowDoc {
            if (yaml.isBlank()) throw BadRequest("The workflow YAML is empty")
            val doc = try {
                Yaml.mapper.readValue(yaml, WorkflowDoc::class.java)
            } catch (e: Exception) {
                throw BadRequest("This is not a workflow YAML file", e.message?.lineSequence()?.firstOrNull()?.take(200))
            } ?: throw BadRequest("The workflow YAML is empty")
            if (doc.name.isBlank()) throw BadRequest("The workflow has no name", "Add `name:` at the top of the file.")
            val bad = doc.steps.filter { it.id.isBlank() || it.kind !in KINDS }
            if (bad.isNotEmpty()) {
                throw BadRequest("Some steps have no id or an unknown kind", "Kinds: ${KINDS.joinToString()}. Steps: ${bad.joinToString { it.name.ifBlank { it.id } }}")
            }
            val dup = doc.steps.groupBy { it.id }.filter { it.value.size > 1 }.keys
            if (dup.isNotEmpty()) throw BadRequest("Two steps share the id ${dup.joinToString()}")
            return doc
        }
    }
}

/** The Workflow type of the contract (+ source: keel | yours | library). */
data class Workflow(
    val id: String,
    val name: String,
    val basedOn: String?,
    val keelRules: Boolean,
    val version: Int,
    val steps: List<Step>,
    val yaml: String,
    val source: String = "yours",
)

data class LibraryItem(
    val id: String,
    val name: String,
    val source: String,
    val version: String,
    val about: String,
    val steps: Int,
    val gates: Int,
    val estTokens: Int,
    val agents: List<String>,
    val mcp: List<String>,
    val editsFiles: Boolean,
    val installed: Boolean,
)

/** What the user sees before trusting an imported workflow. */
data class InstallReview(
    val name: String,
    val source: String,
    val steps: Int,
    val gates: Int,
    val keelRules: Boolean,
    val locked: Int,
    val agents: List<String>,
    val mcp: List<String>,
    val tools: List<String>,
    val commands: List<String>,
    val editsFiles: Boolean,
    val valid: Boolean,
    val errors: List<String>,
    val warnings: List<String>,
)

/** PUT body: the Workflow type, every field optional (missing = keep). A changed `yaml` wins over the fields. */
data class WorkflowUpdate(
    val name: String? = null,
    val basedOn: String? = null,
    val keelRules: Boolean? = null,
    val steps: List<Step>? = null,
    val yaml: String? = null,
)
