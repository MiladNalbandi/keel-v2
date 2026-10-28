package keel.api.jira

import keel.api.common.NotFound
import keel.api.mcp.McpServer
import keel.api.mcp.McpService
import keel.api.projects.ProjectService
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

/** An optional MCP server keel can add from a project's settings (Tools › Catalog). */
data class CatalogEntry(
    val id: String,
    val name: String,
    val about: String,
    val url: String,
    val license: String,
    val command: String,
    /** The project has what the entry needs (a Jira connection). */
    val ready: Boolean,
    val why: String?,
    /** The MCP server's name once added (it starts turned off). */
    val server: String?,
    val added: Boolean,
)

@RestController
@RequestMapping("/api/projects/{pid}")
class JiraController(private val jira: JiraService, private val mcp: McpService, private val projects: ProjectService) {

    @GetMapping("/jira")
    fun view(@PathVariable pid: String): JiraView = jira.view(pid)

    @PutMapping("/jira")
    fun save(@PathVariable pid: String, @RequestBody body: JiraSave): JiraView = jira.save(pid, body)

    @DeleteMapping("/jira")
    fun delete(@PathVariable pid: String): Map<String, Boolean> {
        jira.delete(pid)
        return mapOf("ok" to true)
    }

    /** GET /rest/api/2/myself with the saved connection, or with the unsaved form in the body. */
    @PostMapping("/jira/test")
    fun test(@PathVariable pid: String, @RequestBody(required = false) body: JiraSave?): JiraTestResult = jira.test(pid, body)

    @GetMapping("/jira/discover")
    fun discover(@PathVariable pid: String, @RequestParam(required = false) key: String?): JiraDiscovery = jira.discover(pid, key)

    /** 0.18.52 the KeelBot account picker (Tasks › Automations): Jira users matching [q]. */
    @GetMapping("/jira/users")
    fun users(@PathVariable pid: String, @RequestParam q: String): List<JiraUser> = jira.users(pid, q)

    @GetMapping("/mcp-catalog")
    fun catalog(@PathVariable pid: String): List<CatalogEntry> {
        projects.require(pid)
        val connected = jira.settings(pid) != null
        val name = JiraService.mcpName(pid)
        val added = mcp.list().any { it.name == name }
        return listOf(CatalogEntry(
            id = "jira", name = "Jira (mcp-atlassian)",
            about = "Lets agents read the ticket and its comments themselves. Filled from this project's Jira connection, read-only.",
            url = "https://github.com/sooperset/mcp-atlassian", license = "MIT", command = "uvx mcp-atlassian",
            ready = connected, why = if (connected) null else "Connect Jira for this project first (Control › Connections › Jira).",
            server = name.takeIf { added }, added = added,
        ))
    }

    /** Adds the entry as an MCP server, turned off: turn it on in Tools, add it to Settings › MCP servers, allow agents. */
    @PostMapping("/mcp-catalog/{id}")
    fun addFromCatalog(@PathVariable pid: String, @PathVariable id: String): McpServer {
        projects.require(pid)
        if (id != "jira") throw NotFound("No catalog entry \"$id\"")
        val name = jira.addMcp(pid)
        // 0.18.64 the server may use this project's Jira token, and only that one (McpService resolves attached secrets)
        mcp.attach(name, listOf(JiraService.secretName(pid)))
        return mcp.viewOf(name)
    }
}

/** The keel-wide Jira connection: one site and token for work that is not one project's (keel Product's tickets). */
@RestController
@RequestMapping("/api/jira")
class JiraKeelController(private val jira: JiraService) {

    @GetMapping
    fun view(): JiraView = jira.view(JiraService.KEEL_WIDE)

    @PutMapping
    fun save(@RequestBody body: JiraSave): JiraView = jira.save(JiraService.KEEL_WIDE, body)

    @DeleteMapping
    fun delete(): Map<String, Boolean> {
        jira.delete(JiraService.KEEL_WIDE)
        return mapOf("ok" to true)
    }

    @PostMapping("/test")
    fun test(@RequestBody(required = false) body: JiraSave?): JiraTestResult = jira.test(JiraService.KEEL_WIDE, body)
}
