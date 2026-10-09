package keel.api.support

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import java.net.URLDecoder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The engine's marketplace routes (docs/plugins/13-step4-contract.md §5) for the api's tests: a small catalog (Code,
 * Database that needs Code, Hello: content only), what is installed, sources and rules. A test sets [refusals] to make
 * one call answer an error the way the engine does ({error, hint, …}).
 */
class StubMarket {
    private val mapper = jacksonObjectMapper()

    private fun version(v: String, permissions: Map<String, Any?>, needs: Map<String, String> = emptyMap()) = mapOf(
        "version" to v, "released" to "2026-10-09", "requires" to mapOf("sdk" to 1, "keel" to ">=0.15.4", "plugins" to needs),
        "url" to "https://example.test/x-$v.kplug", "sha256" to "0".repeat(64), "size" to 1000, "permissions" to permissions,
    )

    /** The catalog's plugins (newest version first). */
    val catalog: List<Map<String, Any?>> = listOf(
        mapOf("name" to "code", "title" to "Code", "publisher" to "keel", "verified" to true, "category" to "code", "trust" to "code",
            "summary" to "The Code page: files, search and an editor.", "tags" to listOf("editor"),
            "versions" to listOf(version("1.0.0", mapOf("workspace" to "write", "pages" to true)))),
        mapOf("name" to "db", "title" to "Database", "publisher" to "keel", "verified" to true, "category" to "code", "trust" to "code",
            "summary" to "Connect a database. Agents get read-only SQL tools.", "tags" to listOf("sql"),
            "versions" to listOf(
                version("1.4.0", mapOf("secrets" to listOf("database"), "network" to listOf("from-connections"), "workspace" to "read"),
                    mapOf("code" to ">=1.0.0")),
                version("1.3.0", mapOf("secrets" to listOf("database")), mapOf("code" to ">=1.0.0")),
            )),
        mapOf("name" to "hello", "title" to "Hello", "publisher" to "ana-k", "verified" to false, "category" to "other", "trust" to "content",
            "summary" to "A workflow that says hello.", "tags" to emptyList<String>(),
            "versions" to listOf(version("0.1.0", emptyMap()))),
    )

    /** What is installed: name → {name, title, version, from, on, parts, previous}. */
    val installed = ConcurrentHashMap<String, MutableMap<String, Any?>>()

    @Volatile var pendingRestart = false

    val rules: MutableMap<String, Any?> = ConcurrentHashMap(DEFAULT_RULES)

    val sources = CopyOnWriteArrayList<Map<String, Any?>>(listOf(OFFICIAL))

    val sets = listOf(
        mapOf("id" to "developer", "title" to "Developer", "summary" to "Read and change code with agents.",
            "plugins" to listOf("code", "git", "review", "db", "ci", "graph", "keelbot")),
        mapOf("id" to "knowledge", "title" to "Knowledge", "summary" to "Help agents know the project.", "plugins" to listOf("wiki", "map", "graph")),
    )

    /** "install:db", "update:db", "remove:db", … → the engine's refusal (status, body). */
    val refusals = ConcurrentHashMap<String, Pair<Int, Map<String, Any?>>>()

    /** What POST /marketplace/install answers on top of the installed plugin (e.g. status: started). */
    @Volatile var installExtra: Map<String, Any?> = emptyMap()

    fun reset() {
        installed.clear()
        pendingRestart = false
        rules.clear()
        rules.putAll(DEFAULT_RULES)
        sources.clear()
        sources += OFFICIAL
        refusals.clear()
        installExtra = emptyMap()
    }

    private fun entry(name: String) = catalog.firstOrNull { it["name"] == name }

    @Suppress("UNCHECKED_CAST")
    private fun versions(p: Map<String, Any?>) = p["versions"] as List<Map<String, Any?>>

    private fun hit(p: Map<String, Any?>): Map<String, Any?> {
        val name = p["name"] as String
        val have = installed[name]?.get("version")
        val newest = versions(p).first()["version"]
        return p - "versions" + mapOf("version" to newest, "installed" to (have ?: false),
            "update" to if (have != null && have != newest) newest else null, "fits" to true)
    }

    private fun query(q: String?): Map<String, String> = q.orEmpty().split('&').filter { '=' in it }
        .associate { it.substringBefore('=') to URLDecoder.decode(it.substringAfter('='), Charsets.UTF_8) }

    private fun refused(key: String) = refusals[key]

    fun route(method: String, path: String, body: JsonNode?, q: String?): Pair<Int, Any?> {
        val parts = path.removePrefix("/marketplace/").split('/')
        return when {
            path == "/marketplace/search" -> {
                val qs = query(q)
                val word = qs["q"].orEmpty().lowercase()
                val cat = qs["category"].orEmpty()
                val hits = catalog.filter { p ->
                    (cat.isEmpty() || p["category"] == cat) &&
                        (word.isEmpty() || listOf("name", "title", "summary").any { (p[it] as String).lowercase().contains(word) })
                }.map(::hit)
                200 to mapOf("hits" to hits, "sources" to listOf(mapOf("id" to "keel", "ok" to true, "problem" to null, "old" to false)))
            }
            parts[0] == "plugins" && parts.size == 2 -> {
                val p = entry(parts[1]) ?: return 404 to mapOf("error" to "No plugin ${parts[1]} in the catalogs.", "hint" to "Search for another word.")
                val newest = versions(p).first()
                @Suppress("UNCHECKED_CAST")
                val needs = ((newest["requires"] as Map<String, Any?>)["plugins"] as Map<String, String>).keys
                val plan = needs.filter { !installed.containsKey(it) }.mapNotNull { entry(it) }.map { mapOf("name" to it["name"], "title" to it["title"],
                    "version" to versions(it).first()["version"]) } + mapOf("name" to p["name"], "title" to p["title"], "version" to newest["version"])
                200 to (hit(p) + mapOf("versions" to versions(p), "needs" to needs.toList(),
                    "checks" to listOf("sha256 matches the catalog", "signed by keel, a key this keel trusts"),
                    "plan" to mapOf("version" to newest["version"], "plugins" to plan)))
            }
            path == "/marketplace/refresh" -> 200 to mapOf("sources" to listOf(mapOf("id" to "keel", "ok" to true, "problem" to null, "old" to false)))
            path == "/marketplace/installed" -> 200 to mapOf("plugins" to installed.values.sortedBy { it["name"] as String }, "pending_restart" to pendingRestart)
            path == "/marketplace/install" -> {
                val name = body?.path("name")?.asText().orEmpty()
                refused("install:$name")?.let { return it }
                val p = entry(name) ?: return 404 to mapOf("error" to "No plugin $name in the catalogs.", "hint" to "Search for another word.")
                val v = body?.path("version")?.asText(null) ?: versions(p).first()["version"] as String
                installed[name] = mutableMapOf("name" to name, "title" to p["title"], "version" to v, "from" to "marketplace", "on" to true,
                    "parts" to listOf("engine", "web"), "status" to "installed", "previous" to null, "problems" to emptyList<Any>())
                pendingRestart = true
                200 to mapOf("name" to name, "title" to p["title"], "version" to v, "installed" to listOf(name)) + installExtra
            }
            path == "/marketplace/install-file" -> {
                refused("install-file")?.let { return it }
                installed["hello"] = mutableMapOf("name" to "hello", "title" to "Hello", "version" to "0.1.0", "from" to "file", "on" to true,
                    "parts" to listOf("content"), "status" to "installed")
                pendingRestart = true
                200 to mapOf("name" to "hello", "title" to "Hello", "version" to "0.1.0", "source" to "file", "path" to body?.path("path")?.asText())
            }
            parts[0] == "installed" && parts.size == 3 && parts[2] == "update" -> {
                val name = parts[1]
                refused("update:$name")?.let { return it }
                val have = installed[name] ?: return 404 to mapOf("error" to "$name is not installed.")
                val v = body?.path("version")?.asText(null) ?: versions(entry(name)!!).first()["version"]
                have["previous"] = have["version"]
                have["version"] = v
                pendingRestart = true
                200 to have + mapOf("title" to have["title"])
            }
            parts[0] == "installed" && parts.size == 3 && parts[2] == "rollback" -> {
                val have = installed[parts[1]] ?: return 404 to mapOf("error" to "${parts[1]} is not installed.")
                val previous = have["previous"] ?: return 409 to mapOf("error" to "There is no kept version to go back to.",
                    "hint" to "keel keeps the version before the last update.")
                have["previous"] = have["version"]
                have["version"] = previous
                pendingRestart = true
                200 to have.toMap()
            }
            parts[0] == "installed" && parts.size == 2 && method == "PUT" -> {
                val have = installed[parts[1]] ?: return 404 to mapOf("error" to "${parts[1]} is not installed.")
                have["on"] = body?.path("on")?.asBoolean() ?: true
                pendingRestart = true
                200 to mapOf("name" to parts[1], "on" to have["on"], "dependents" to emptyList<String>())
            }
            parts[0] == "installed" && parts.size == 2 && method == "DELETE" -> {
                refused("remove:${parts[1]}")?.let { return it }
                installed.remove(parts[1]) ?: return 404 to mapOf("error" to "${parts[1]} is not installed.")
                pendingRestart = true
                200 to mapOf("name" to parts[1], "removed" to true, "data" to query(q)["data"])
            }
            path == "/marketplace/sources" && method == "PUT" -> {
                val list = body?.path("sources")?.map { mapper.convertValue(it, Map::class.java) as Map<String, Any?> }.orEmpty()
                if (list.any { !(it["url"] as? String).orEmpty().startsWith("https://") }) {
                    return 400 to mapOf("error" to "A catalog URL must start with https://", "hint" to "http is only for tests on this computer.")
                }
                sources.clear()
                sources += list
                200 to mapOf("sources" to sources)
            }
            path == "/marketplace/sources" -> 200 to mapOf("sources" to sources)
            path == "/marketplace/rules" && method == "PUT" -> {
                body?.fields()?.forEach { (k, v) -> if (k in DEFAULT_RULES) rules[k] = v.asBoolean() }
                200 to rules.toMap()
            }
            path == "/marketplace/rules" -> 200 to rules.toMap()
            path == "/marketplace/sets" -> 200 to sets
            else -> 404 to mapOf("error" to "no route $path")
        }
    }

    companion object {
        val DEFAULT_RULES = mapOf("agents_may_ask" to true, "allow_unverified" to false, "check_daily" to true, "restart_when_idle" to false)
        val OFFICIAL = mapOf("id" to "keel", "title" to "keel marketplace", "url" to "https://keel-studio.github.io/keel-marketplace/v1/index.json",
            "key" to "RWQBAgMEBQYHCHm1Vi6P5lT5QHixEuipi6eQH4U65pW+1+DjkQutBJZk", "on" to true, "official" to true)
    }
}
