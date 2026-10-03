package keel.api.mcp

import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api")
class McpController(private val mcp: McpService) {

    @GetMapping("/mcp-servers")
    fun list(): List<McpServer> = mcp.list()

    @PostMapping("/mcp-servers")
    fun create(@RequestBody spec: McpServerSpec): McpServer = mcp.create(spec)

    @PutMapping("/mcp-servers/{name}")
    fun update(@PathVariable name: String, @RequestBody body: McpUpdate): McpServer = mcp.update(name, body)

    @DeleteMapping("/mcp-servers/{name}")
    fun delete(@PathVariable name: String): Map<String, Boolean> {
        mcp.delete(name)
        return mapOf("ok" to true)
    }

    @PostMapping("/mcp-servers/{name}/test")
    fun test(@PathVariable name: String): McpTestResult = mcp.test(name)

    @GetMapping("/projects/{pid}/mcp-allow")
    fun allow(@PathVariable pid: String): Map<String, List<String>> = mcp.allow(pid)

    @PutMapping("/projects/{pid}/mcp-allow")
    fun saveAllow(@PathVariable pid: String, @RequestBody body: Map<String, List<String>>): Map<String, List<String>> =
        mcp.saveAllow(pid, body)
}
