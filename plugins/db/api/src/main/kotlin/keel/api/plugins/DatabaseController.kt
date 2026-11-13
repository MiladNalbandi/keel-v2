package keel.api.plugins

import com.fasterxml.jackson.databind.JsonNode
import jakarta.servlet.http.HttpServletRequest
import keel.api.common.Forbidden
import keel.api.mcp.McpLevelGuard
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

/** A call from keel2 mcp (an MCP client, an agent of the person's): the connection's Access applies to it. */
internal fun HttpServletRequest.fromAgent(): Boolean = getAttribute(McpLevelGuard.LEVEL_ATTR) != null

/** #96 what only the person may do (read a private result, change what MCP clients may): never an MCP call, at any level. */
internal fun HttpServletRequest.personOnly(what: String) {
    if (fromAgent()) throw Forbidden("Only the person may $what", "An MCP client never reads a private result or changes what MCP clients may do.")
}

/** keel 0.15's routes, by connection name: the project's connections as it uses them (its own and the globals it sees). */
@RestController
@RequestMapping("/api/projects/{pid}/db")
class DatabaseController(private val db: DatabaseService) {
    @GetMapping("/connections")
    fun list(@PathVariable pid: String, request: HttpServletRequest): List<DbConnection> = db.list(pid, request.fromAgent())

    @PostMapping("/connections")
    fun create(@PathVariable pid: String, @RequestBody body: DbConnectionBody): Map<String, Any?> = db.create(pid, body)

    @PutMapping("/connections/{name}")
    fun update(@PathVariable pid: String, @PathVariable name: String, @RequestBody body: DbConnectionBody): DbConnection = db.update(pid, name, body)

    @DeleteMapping("/connections/{name}")
    fun delete(@PathVariable pid: String, @PathVariable name: String): Map<String, Boolean> {
        db.delete(pid, name)
        return mapOf("ok" to true)
    }

    @PostMapping("/connections/{name}/test")
    fun test(@PathVariable pid: String, @PathVariable name: String): Map<String, Any?> = db.test(pid, name)

    @GetMapping("/suggest")
    fun suggest(@PathVariable pid: String): JsonNode = db.suggest(pid)

    @GetMapping("/schema")
    fun schema(@PathVariable pid: String, @RequestParam(required = false) connection: String?, request: HttpServletRequest): JsonNode =
        db.schema(pid, connection, request.fromAgent())

    /** v0.16.1 an MCP call at the read level may read (keel_db_query), never change (McpLevelGuard lets the route through at read). */
    @PostMapping("/query")
    fun query(@PathVariable pid: String, @RequestBody body: DbQueryBody, request: HttpServletRequest): JsonNode {
        if (body.change && request.getAttribute(McpLevelGuard.LEVEL_ATTR) == "read") {
            throw Forbidden("This needs the MCP write level: run keel2 mcp --write", "A read-level MCP server can only read the database.")
        }
        return db.query(pid, body, request.fromAgent(), request.getHeader("X-Keel-Client"))
    }

    /** #96 the results of agents' queries on a private connection, for the person only (newest first, without rows). */
    @GetMapping("/results")
    fun results(@PathVariable pid: String, request: HttpServletRequest): JsonNode {
        request.personOnly("read private results")
        return db.results(pid)
    }

    @GetMapping("/results/{id}")
    fun result(@PathVariable pid: String, @PathVariable id: String, request: HttpServletRequest): JsonNode {
        request.personOnly("read private results")
        return db.result(pid, id)
    }

    @DeleteMapping("/results/{id}")
    fun forget(@PathVariable pid: String, @PathVariable id: String, request: HttpServletRequest): JsonNode {
        request.personOnly("forget private results")
        return db.forget(pid, id)
    }
}

/**
 * Database connections global or per project (Control › Databases and a project's Databases), by id. Everything under
 * /api/databases changes what every project sees, so an MCP call needs the admin level there (McpLevelGuard); a
 * project's own connections stay at the write level.
 */
@RestController
class DatabasesController(private val db: DatabaseService) {
    // ---- every project (Control › Databases)
    @GetMapping("/api/databases")
    fun globals(request: HttpServletRequest): List<DbEntry> = db.globals(request.fromAgent())

    @PostMapping("/api/databases")
    fun createGlobal(@RequestBody body: DbConfigBody): DbEntry = db.createGlobal(body)

    @PostMapping("/api/databases/test")
    fun testDraft(@RequestBody body: DbConfigBody, @RequestParam(required = false) project: String?): DbTestResult = db.testDraft(body, project)

    @PostMapping("/api/databases/server-databases")
    fun serverDatabases(@RequestBody body: DbConfigBody, @RequestParam(required = false) project: String?): JsonNode =
        db.serverDatabases(body, project)

    /** #96 what keel's MCP server (Claude Code, OpenCode, Codex … through keel2 mcp) may do with databases, at most. */
    @GetMapping("/api/databases/mcp")
    fun mcpAccess(): DbMcpAccess = db.mcpAccess()

    @PutMapping("/api/databases/mcp")
    fun setMcpAccess(@RequestBody body: DbMcpAccess, request: HttpServletRequest): DbMcpAccess {
        request.personOnly("change what MCP clients may do with databases")
        return db.setMcpAccess(body)
    }

    @GetMapping("/api/databases/{id}")
    fun one(@PathVariable id: String, request: HttpServletRequest): DbEntry = db.get(id, request.fromAgent())

    @PutMapping("/api/databases/{id}")
    fun update(@PathVariable id: String, @RequestBody body: DbConfigBody, @RequestParam(required = false) project: String?): DbEntry =
        db.updateById(id, body, project)

    @DeleteMapping("/api/databases/{id}")
    fun delete(@PathVariable id: String): Map<String, Boolean> {
        db.deleteById(id)
        return mapOf("ok" to true)
    }

    @PostMapping("/api/databases/{id}/test")
    fun test(@PathVariable id: String, @RequestParam(required = false) project: String?): Map<String, Any?> = db.testById(id, project)

    // ---- one project (its own, and the globals it sees)
    @GetMapping("/api/projects/{pid}/databases")
    fun effective(@PathVariable pid: String, request: HttpServletRequest): List<DbEntry> = db.effective(pid, request.fromAgent())

    @PostMapping("/api/projects/{pid}/databases")
    fun create(@PathVariable pid: String, @RequestBody body: DbConfigBody): DbEntry = db.createIn(pid, body)

    @GetMapping("/api/projects/{pid}/databases/suggest")
    fun suggest(@PathVariable pid: String): JsonNode = db.suggest(pid)

    @PostMapping("/api/projects/{pid}/databases/test")
    fun testDraftIn(@PathVariable pid: String, @RequestBody body: DbConfigBody): DbTestResult = db.testDraft(body, pid)

    @PostMapping("/api/projects/{pid}/databases/server-databases")
    fun serverDatabasesIn(@PathVariable pid: String, @RequestBody body: DbConfigBody): JsonNode = db.serverDatabases(body, pid)

    @PutMapping("/api/projects/{pid}/databases/{id}")
    fun updateIn(@PathVariable pid: String, @PathVariable id: String, @RequestBody body: DbConfigBody): DbEntry = db.updateIn(pid, id, body)

    @DeleteMapping("/api/projects/{pid}/databases/{id}")
    fun deleteIn(@PathVariable pid: String, @PathVariable id: String): Map<String, Boolean> {
        db.deleteIn(pid, id)
        return mapOf("ok" to true)
    }

    @PostMapping("/api/projects/{pid}/databases/{id}/test")
    fun testIn(@PathVariable pid: String, @PathVariable id: String): Map<String, Any?> = db.testIn(pid, id)

    /** A project turns a global connection off for itself, or on again: {"hidden": true}. */
    @PutMapping("/api/projects/{pid}/databases/{id}/hidden")
    fun hide(@PathVariable pid: String, @PathVariable id: String, @RequestBody body: Map<String, Boolean>): List<DbEntry> =
        db.hide(pid, id, body["hidden"] ?: true)
}
