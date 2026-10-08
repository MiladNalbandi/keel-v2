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
    // Engine workflow features the api passes through unchanged (engine/keel_engine/workflows/model.py):
    // fan-out from a state list (from, cap, batch), for_each loops (for_each, per_item), result markers and a
    // collected list, a branch on a marker or a data path (when; on a gate: when it pauses), named gate exits
    // (choices: {name: step id | "end"}), and the start_flow hand-off (flow, seed, then).
    val from: String? = null,
    val cap: Int? = null,
    /** A number, or "$<state path>" the engine reads when the step runs (e.g. "$data.hunt.prove_concurrency"). */
    val batch: Any? = null,
    val forEach: String? = null,
    val perItem: Boolean? = null,
    val markers: List<String>? = null,
    val collect: String? = null,
    val `when`: Map<String, Any?>? = null,
    /** v0.10.0 a plugin step's settings (db:check's sql and expect, git:pr's title...): `with:` in the YAML. */
    val with: Map<String, Any?>? = null,
    val flow: String? = null,
    val seed: Map<String, Any?>? = null,
    val then: String? = null,
    // ship and cover (engine docs/CONTRACT.md "v0.4.0 additions: cover, ship and include"): soft checks, retry_only,
    // rounds, a review's redo, the skip menu (skippable, group, skip_menu), the final report and choice gates.
    val soft: Boolean? = null,
    val retryOnly: Boolean? = null,
    val rounds: Int? = null,
    val redo: String? = null,
    val skippable: String? = null,
    val group: String? = null,
    val skipMenu: Boolean? = null,
    val report: String? = null,
    /** A list (one choice per loop item, cover) or a map of named exits {name: step id | "end"} (hunt). */
    val choices: Any? = null,
    val onSkip: Map<String, Any?>? = null,
    // review, diagnose, fix and change: the agent's step instructions and where a code step goes once its rounds are used up.
    val instructions: String? = null,
    val afterRounds: String? = null,
    val recommend: String? = null,
    /** Set by the engine when it expands an include: where the step came from, outermost include first ("ship", "ship/cover"). */
    val includedFrom: String? = null,
    /** v0.13.0 add-on switches: "item" runs a parallel step's agents each in its item's folder, read only; `asks` lets an
     *  agent step end with questions that the next gate shows as buttons (keel's clarify loop). */
    val root: String? = null,
    val asks: Boolean? = null,
    /** v0.13.0 an agent step whose whole answer the next steps read: state.data["<step id>_text"]. */
    val keep: Boolean? = null,
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
        /** `include` is expanded by the engine into the steps of the workflow it names (flow: ship). */
        val KINDS = setOf("agent", "code", "gate", "branch", "parallel", "include")

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
    /** v0.9.0, in a project's list: the folder it sits in on the Workflows page, how often it ran, and its last run. */
    val folder: String? = null,
    val runs: Int? = null,
    val lastRun: LastRun? = null,
    /** v0.11.0: a plugin's own workflow (ci-fix: ci); listed only for the projects that turned that plugin on. */
    val plugin: String? = null,
    /** v0.13.0: an add-on's workflow (keel Product: "product"); listed only while that part of keel is on. */
    val addon: String? = null,
)

/** A workflow's newest flow in the project (the Workflows page and KeelBot show it). */
data class LastRun(val threadId: String, val title: String, val status: String, val at: String)

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
