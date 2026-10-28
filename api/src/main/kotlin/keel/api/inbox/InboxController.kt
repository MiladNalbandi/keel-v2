package keel.api.inbox

import com.fasterxml.jackson.databind.JsonNode
import jakarta.servlet.http.HttpServletRequest
import keel.api.auth.LocalAuth
import keel.api.mcp.McpLevelGuard
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

/** v0.4.1: one place for everything that waits for a person, across all projects. */
@RestController
@RequestMapping("/api/inbox")
class InboxController(private val inbox: InboxService) {

    @GetMapping
    fun list(@RequestParam(required = false) project: String?, @RequestParam(required = false) kind: String?): InboxView =
        inbox.list(project, kind)

    @GetMapping("/count")
    fun count(): InboxCount = inbox.count()

    /**
     * Approve, send back (why), pick a choice or answer questions (payload) — the same resume as the Flow page.
     * 0.18.64 an approval the api asked itself (a plugin install, a send to Jira) is answered here only by a person or
     * the MCP admin level, as on POST /api/approvals/{id}/decide: an MCP write token answers flows only.
     */
    @PostMapping("/{tid}/act")
    fun act(@PathVariable tid: String, @RequestBody body: InboxAct, request: HttpServletRequest): JsonNode =
        inbox.act(tid, body, mayDecide = request.getAttribute(LocalAuth.WHO_ATTR) != "mcp" ||
            request.getAttribute(McpLevelGuard.LEVEL_ATTR) == "admin")
}
