package keel.api.help

import keel.api.common.KvStore
import keel.api.common.Time
import keel.api.connections.ConnectionService
import keel.api.mcp.McpClients
import keel.api.projects.ProjectService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.nio.file.Paths

/** A model keel can use now: a provider with a mode that is ready (never the fake model). */
data class StartModel(val provider: String, val label: String, val mode: String)

/** The project step 4 (init) looks at: the one the page shows, else the first one. */
data class StartProject(val id: String, val name: String, val configured: Boolean, val initRunning: Boolean)

/** The first flow that is not init (or knowledge-refresh), with its project. */
data class StartFlow(val threadId: String, val projectId: String, val workflow: String?, val title: String, val status: String)

/**
 * keel 0.18.66 Help › Start here and the first-run welcome: the five steps, each with what keel really sees.
 *   1 a project added · 2 a model connected · 3 an AI tool called keel over MCP · 4 init done · 5 a first flow started
 */
data class StartView(
    val projects: Int,
    val project: StartProject?,
    val models: List<StartModel>,
    val mcp: List<keel.api.mcp.McpClientSeen>,
    val flow: StartFlow?,
    val flows: Int,
    val welcome: WelcomeState,
)

/** The welcome was finished or skipped (once per keel, not per browser), and when. */
data class WelcomeState(val seen: Boolean = false, val at: String? = null, val how: String? = null)

@RestController
class StartHereController(
    private val projects: ProjectService,
    private val connections: ConnectionService,
    private val clients: McpClients,
    private val jdbc: JdbcTemplate,
    private val kv: KvStore,
) {

    @GetMapping("/api/help/start")
    fun start(@RequestParam(required = false) project: String?): StartView {
        val rows = projects.rows()
        val row = rows.firstOrNull { it.id == project } ?: rows.firstOrNull()
        val p = row?.let {
            val init = jdbc.queryForObject(
                "SELECT COUNT(*) FROM threads WHERE project_id = ? AND workflow_id = 'init' AND status IN ('running', 'waiting')",
                Int::class.java, it.id,
            ) ?: 0
            StartProject(it.id, it.name, projects.configured(Paths.get(it.root)), init > 0)
        }
        // the slowest part (each CLI's version, up to 5 s): a failure only leaves the step open
        val models = runCatching {
            connections.connections().providers.filter { it.id != "fake" }.flatMap { pv ->
                pv.modes.filter { it.ready }.map { StartModel(pv.id, it.label, it.id) }
            }
        }.getOrDefault(emptyList())
        val ids = rows.map { it.id }.toSet()
        val flows = jdbc.query(
            "SELECT id, project_id, workflow_id, title, status FROM threads WHERE COALESCE(workflow_id, '') NOT IN ($NOT_A_FIRST_FLOW) " +
                "ORDER BY created_at",
        ) { rs, _ -> StartFlow(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4) ?: "", rs.getString(5) ?: "") }
            .filter { it.projectId in ids }
        return StartView(rows.size, p, models, clients.list().filter { it.via == "mcp" }, flows.firstOrNull(), flows.size, welcome())
    }

    @GetMapping("/api/help/welcome")
    fun welcome(): WelcomeState = kv.get<WelcomeState>(KEY) ?: WelcomeState()

    /** Finished or skipped: no browser of this keel shows the welcome again by itself (Help › Start here can). */
    @PutMapping("/api/help/welcome")
    fun saveWelcome(@RequestBody body: WelcomeState): WelcomeState {
        val next = if (body.seen) WelcomeState(true, Time.now(), body.how?.take(20)) else WelcomeState()
        kv.put(KEY, next)
        return next
    }

    companion object {
        const val KEY = "help.welcome"
        /** keel's own set-up flows: they do not count as "your first flow" */
        private const val NOT_A_FIRST_FLOW = "'init', 'knowledge-refresh'"
    }
}
