package keel.api.reviewsetup

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import keel.api.agents.AgentNames
import keel.api.agents.AgentService
import keel.api.common.BadRequest
import keel.api.engine.EngineClient
import keel.api.projects.ProjectService
import keel.api.settings.Model
import keel.api.settings.SettingsService
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** "New lens" on Settings › Review: [reviewer] A..D puts it on that reviewer, null leaves it off. */
data class NewLensBody(val title: String = "", val description: String = "", val reviewer: String? = null, val checklist: String = "")

/** A project lens's whole file (its Edit link). */
data class LensTextBody(val text: String = "")

/** One Review page reviewer as the run needs it: [model] null is KeelBot's own model; [lenses] are their checklists. */
data class FindReviewer(
    val letter: String,
    val name: String,
    val focus: String,
    val model: Model?,
    val agent: String?,
    val lenses: List<Pair<String, String>>,
)

/** What the Review page's "Find problems" runs with (Settings › Review, keel's defaults when nothing is written). */
data class FindSetup(
    val reviewers: List<FindReviewer>,
    val checker: Model?,
    val checkShouldFix: Boolean,
    val fixFlow: String,
    val fixModel: Model?,
    val fixRunMode: String?,
    val oneFlowPerFile: Boolean,
    val text: String,
)

/**
 * keel 0.18.65 Settings › Review: the `review:` block of the project's .keel/config.yml and its lens files. The engine
 * reads and writes them (runtime/review_config.py, runtime/lenses.py) as it does the rules; the api gives it the
 * project's folder and its models (to say which model each part will use), checks the agents (only the api knows a
 * project's agents) and adds them to the view. The Review plugin's "Find problems" reads [find].
 */
@Service
class ReviewSetupService(
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val settings: SettingsService,
    private val agents: AgentService,
    private val mapper: ObjectMapper,
) {
    companion object {
        val LENS_NAME = Regex("^[a-z0-9][a-z0-9-]{0,47}$")
    }

    private fun root(pid: String) = projects.root(pid).toString()

    /** The project's models: [default] is what "project default" means on the Review page (KeelBot's), the reviewer
     *  model is what the flows' review steps get before a final review raises it. */
    private fun models(pid: String, default: Model? = null): Map<String, Model> = settings.effective(pid).let {
        mapOf("default" to (default ?: it.defaultModel), "reviewer" to it.reviewerModel)
    }

    fun view(pid: String, default: Model? = null): JsonNode =
        withAgents(pid, engine.post("/review-config/view", mapOf("root" to root(pid), "models" to models(pid, default))))

    fun save(pid: String, patch: Map<String, Any?>): JsonNode {
        checkAgents(pid, patch["reviewers"])
        return withAgents(pid, engine.put("/review-config", mapOf("root" to root(pid), "patch" to patch, "models" to models(pid))))
    }

    /** A reviewer's agent must be one of the project's (its role and skills go into the reviewer's prompt). */
    private fun checkAgents(pid: String, reviewers: Any?) {
        val list = reviewers as? List<*> ?: return
        val ids by lazy { agents.list(pid).filter { it.enabled }.map { it.id }.toSet() }
        list.forEachIndexed { i, r ->
            val a = (r as? Map<*, *>)?.get("agent")?.toString()?.trim()?.ifEmpty { null } ?: return@forEachIndexed
            if (AgentNames.canonical(a) !in ids) throw BadRequest("Reviewer ${"ABCD"[i.coerceAtMost(3)]}: there is no agent called \"$a\" in this project",
                "Pick one of keel's agents or the project's own (Agents page).")
        }
    }

    private fun withAgents(pid: String, view: JsonNode): JsonNode {
        (view as? ObjectNode)?.set<JsonNode>("agents", mapper.valueToTree(agents.list(pid).filter { it.enabled }
            .map { mapOf("id" to it.id, "label" to it.label, "custom" to it.custom, "keel" to !it.custom) }))
        return view
    }

    fun newLens(pid: String, b: NewLensBody): JsonNode {
        val out = engine.post("/review-config/lenses", mapOf("root" to root(pid), "models" to models(pid),
            "lens" to mapOf("title" to b.title, "description" to b.description, "reviewer" to b.reviewer, "checklist" to b.checklist))) as ObjectNode
        out.get("view")?.let { withAgents(pid, it) }
        return out
    }

    /** A lens's name is a file name (money-rounding), never a path: checked here too, before it is part of a url. */
    private fun lensName(name: String): String {
        if (!LENS_NAME.matches(name)) throw BadRequest("\"${name.take(60)}\" is not a lens name", "Lower-case letters, digits and dashes (money-rounding).")
        return name
    }

    fun lens(pid: String, name: String): JsonNode =
        engine.get("/review-config/lenses/{name}?root={root}", mapOf("name" to lensName(name), "root" to root(pid)))

    fun saveLens(pid: String, name: String, text: String): JsonNode =
        engine.put("/review-config/lenses/${lensName(name)}", mapOf("root" to root(pid), "text" to text))

    // ---------------------------------------------------------------- what Find problems runs with

    private fun model(n: JsonNode?): Model? =
        n?.takeIf { it.isObject && it.path("provider").asText().isNotEmpty() }?.let { runCatching { mapper.treeToValue(it, Model::class.java) }.getOrNull() }

    /**
     * The reviewers, the checker and the fix as Find problems runs them. [default] is KeelBot's model: a reviewer on
     * "project default" with no effort of its own passes no model (KeelBot's own, as before 0.18.65).
     */
    fun find(pid: String, default: Model?): FindSetup {
        val v = view(pid, default)
        val cfg = v.path("config")
        val uses = v.path("uses")
        val checklists = v.path("lenses").associate { it.path("name").asText() to (it.path("title").asText() to it.path("checklist").asText()) }
        val reviewers = cfg.path("reviewers").mapIndexed { i, r ->
            val plain = r.path("model").asText() == "default" && r.path("effort").isNull
            FindReviewer(
                letter = "ABCD"[i].toString(), name = r.path("name").asText(), focus = r.path("focus").asText(),
                model = if (plain) null else model(uses.path("reviewers").path(i)),
                agent = r.path("agent").takeIf { it.isTextual }?.asText(),
                lenses = r.path("lenses").mapNotNull { n -> checklists[n.asText()]?.takeIf { it.second.isNotBlank() } },
            )
        }
        val fix = cfg.path("fix")
        return FindSetup(
            reviewers = reviewers, checker = model(uses.path("checker")), checkShouldFix = cfg.path("checker").path("should_fix").asBoolean(true),
            fixFlow = fix.path("flow").asText("small"), fixModel = model(fix.path("model")),
            fixRunMode = fix.path("run_mode").takeIf { it.isTextual }?.asText(), oneFlowPerFile = fix.path("one_flow_per_file").asBoolean(false),
            text = v.path("find_text").asText(),
        )
    }
}

/** GET|PUT /api/projects/{pid}/review-setup (Settings › Review), its lenses (new, view, edit). */
@RestController
@RequestMapping("/api/projects/{pid}/review-setup")
class ReviewSetupController(private val setup: ReviewSetupService) {
    @GetMapping
    fun view(@PathVariable pid: String): JsonNode = setup.view(pid)

    /** A patch of reviewers, checker, fix, final, drop_correctness; {"reset": true} is keel's default again. */
    @PutMapping
    fun save(@PathVariable pid: String, @RequestBody patch: Map<String, Any?>): JsonNode = setup.save(pid, patch)

    @PostMapping("/lenses")
    fun newLens(@PathVariable pid: String, @RequestBody b: NewLensBody): JsonNode = setup.newLens(pid, b)

    @GetMapping("/lenses/{name}")
    fun lens(@PathVariable pid: String, @PathVariable name: String): JsonNode = setup.lens(pid, name)

    @PutMapping("/lenses/{name}")
    fun saveLens(@PathVariable pid: String, @PathVariable name: String, @RequestBody b: LensTextBody): JsonNode = setup.saveLens(pid, name, b.text)
}
