package keel.api.support

import keel.api.mcp.McpLevelGuard
import org.springframework.web.context.WebApplicationContext
import org.springframework.web.servlet.mvc.method.annotation.RequestMappingHandlerMapping

/**
 * 0.18.64 every non-GET /api route the app has mapped, with the levels McpLevelGuard's lists give it. A plugin's
 * tests can use it too: their app has the plugin's routes.
 */
object RouteLevels {
    data class Route(val method: String, val pattern: String, val handler: String, val levels: List<String>)

    private val VAR = Regex("\\{[^}]*}")
    private val SAFE = setOf("GET", "HEAD", "OPTIONS")

    /** A path for a pattern: each {name} becomes a plain segment. */
    fun sample(pattern: String): String = VAR.replace(pattern, "x1")

    fun all(wac: WebApplicationContext): List<Route> {
        val guard = wac.getBean(McpLevelGuard::class.java)
        val mapping = wac.getBean("requestMappingHandlerMapping", RequestMappingHandlerMapping::class.java)
        val out = mutableListOf<Route>()
        for ((info, handler) in mapping.handlerMethods) {
            val methods = info.methodsCondition.methods.map { it.name }.ifEmpty { listOf("ANY") }
            for (pattern in info.patternValues) {
                if (!pattern.startsWith("/api/")) continue
                for (m in methods) {
                    if (m in SAFE) continue
                    val name = "${handler.beanType.simpleName}.${handler.method.name}"
                    out += Route(m, pattern, name, if (m == "ANY") emptyList() else guard.levels(m, sample(pattern)))
                }
            }
        }
        return out.sortedBy { "${it.pattern} ${it.method}" }
    }
}
