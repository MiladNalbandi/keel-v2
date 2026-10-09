package keel.api.support

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import java.net.URLDecoder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The engine's marketplace routes for the api's tests, in the shapes of docs/plugins/13-step4-contract.md §12: a small
 * catalog (Code, Database that needs Code, Hello: content only, Old: refused), what is installed, sources, rules and
 * sets. A test sets [refusals] to make one call answer an error the way the engine does ({error, hint, …}), and [more]
 * to make an update ask for more permissions.
 */
class StubMarket {
    private val mapper = jacksonObjectMapper()

    private fun version(v: String, permissions: Map<String, Any?>, needs: Map<String, String> = emptyMap()) = mapOf(
        "version" to v, "released" to "2026-10-09", "requires" to mapOf("sdk" to 1, "keel" to ">=0.15.4", "plugins" to needs),
        "url" to "https://example.test/x-$v.kplug", "sha256" to "0".repeat(64), "size" to 1000, "permissions" to permissions,
        "revoked" to null, "fits" to true, "why_not" to null,
    )

    private fun plugin(name: String, title: String, publisher: String, verified: Boolean, category: String, trust: String, summary: String,
                       vararg versions: Map<String, Any?>) = mapOf(
        "name" to name, "title" to title, "publisher" to publisher, "publisher_title" to publisher, "verified" to verified,
        "category" to category, "summary" to summary, "tags" to emptyList<String>(), "trust" to trust,
        "repo" to "https://github.com/keel-studio/keel-plugin-$name", "source" to "keel", "versions" to versions.toList(),
    )

    /** The catalog's plugins (newest version first). */
    val catalog: List<Map<String, Any?>> = listOf(
        plugin("code", "Code", "keel", true, "code", "code", "The Code page: files, search and an editor.",
            version("1.0.0", mapOf("workspace" to "write"))),
        plugin("db", "Database", "keel", true, "code", "code", "Connect a database. Agents get read-only SQL tools.",
            version("1.4.0", mapOf("secrets" to listOf("database"), "network" to listOf("from-connections"), "workspace" to "read"), mapOf("code" to ">=1.0.0")),
            version("1.3.0", mapOf("secrets" to listOf("database")), mapOf("code" to ">=1.0.0"))),
        plugin("hello", "Hello", "ana-k", false, "other", "content", "A workflow that says hello.", version("0.1.0-beta.1", emptyMap())),
        plugin("old", "Old", "keel", true, "other", "code", "A plugin whose only version was revoked.", version("0.1.0", emptyMap())),
    )

    /** What is installed: name → the installed shape. */
    val installed = ConcurrentHashMap<String, MutableMap<String, Any?>>()

    @Volatile var pendingRestart = false

    val rules: MutableMap<String, Any?> = ConcurrentHashMap(DEFAULT_RULES)

    val sources = CopyOnWriteArrayList<Map<String, Any?>>(listOf(OFFICIAL))

    /** "install:db", "update:db", "remove:db", … → the engine's refusal (status, body). */
    val refusals = ConcurrentHashMap<String, Pair<Int, Map<String, Any?>>>()

    /** name → the permissions an update to the newest version adds (refused without allow_more_permissions). */
    val more = ConcurrentHashMap<String, List<String>>()

    fun reset() {
        installed.clear()
        pendingRestart = false
        rules.clear()
        rules.putAll(DEFAULT_RULES)
        sources.clear()
        sources += OFFICIAL
        refusals.clear()
        more.clear()
    }

    private fun entry(name: String) = catalog.firstOrNull { it["name"] == name }

    @Suppress("UNCHECKED_CAST")
    private fun versions(p: Map<String, Any?>) = p["versions"] as List<Map<String, Any?>>

    @Suppress("UNCHECKED_CAST")
    private fun needs(v: Map<String, Any?>) = (v["requires"] as Map<String, Any?>)["plugins"] as Map<String, String>

    private fun pending() = mapOf("pending" to pendingRestart,
        "changes" to installed.values.map { mapOf("name" to it["name"], "now" to null, "next" to it["version"]) })

    private fun hit(p: Map<String, Any?>): Map<String, Any?> {
        val name = p["name"] as String
        val have = installed[name]?.get("version")
        val newest = versions(p).first()
        val revoked = if (name == "old") mapOf("version" to "0.1.0", "why" to "it sent errors to a wrong host", "fixed" to null) else null
        return p - "versions" + mapOf("latest" to newest["version"], "version" to newest["version"], "permissions" to newest["permissions"],
            "fits" to true, "why_not" to null, "installed" to have, "installed_from" to installed[name]?.get("from"),
            "update" to if (have != null && have != newest["version"]) newest["version"] else null, "revoked" to revoked, "old" to false)
    }

    private fun query(q: String?): Map<String, String> = q.orEmpty().split('&').filter { '=' in it }
        .associate { it.substringBefore('=') to URLDecoder.decode(it.substringAfter('='), Charsets.UTF_8) }

    private fun answer(name: String, v: String, from: String?, extra: Map<String, Any?> = emptyMap()) = mapOf(
        "name" to name, "version" to v, "title" to entry(name)?.get("title"), "installed" to listOf(mapOf("name" to name, "version" to v, "from" to from)),
        "turned_on" to emptyList<String>(), "pending_restart" to pending(),
    ) + extra

    private fun put(name: String, v: String, from: String, previous: Any?) {
        installed[name] = mutableMapOf("name" to name, "title" to entry(name)?.get("title"), "version" to v, "loaded" to null,
            "parts" to listOf("engine", "web"), "from" to from, "on" to true, "status" to "restart", "problems" to emptyList<String>(),
            "revoked" to null, "update" to null, "previous" to previous, "image_version" to null, "can_remove" to true,
            "needed_by" to emptyList<String>(), "needs" to emptyMap<String, String>(), "trust" to entry(name)?.get("trust"),
            "publisher" to entry(name)?.get("publisher"), "catalog" to "keel", "permissions" to emptyMap<String, Any>(), "per_project" to false,
            "installed_at" to "2026-10-09T08:00:00Z")
        pendingRestart = true
    }

    fun route(method: String, path: String, body: JsonNode?, q: String?): Pair<Int, Any?> {
        val parts = path.removePrefix("/marketplace/").split('/')
        val sourcesStatus = sources.map { it - "key" + mapOf("ok" to true, "problem" to null, "old" to false, "plugins" to catalog.size) }
        return when {
            path == "/marketplace/search" -> {
                val qs = query(q)
                val word = qs["q"].orEmpty().lowercase()
                val cat = qs["category"].orEmpty()
                val hits = catalog.filter { p ->
                    (cat.isEmpty() || p["category"] == cat) &&
                        (word.isEmpty() || listOf("name", "title", "summary").any { (p[it] as String).lowercase().contains(word) })
                }.map(::hit)
                200 to mapOf("plugins" to hits, "sources" to sourcesStatus, "categories" to CATEGORIES)
            }
            parts[0] == "plugins" && parts.size == 2 -> {
                val p = entry(parts[1]) ?: return 404 to mapOf("error" to "No catalog lists a plugin called ${parts[1]}.", "hint" to "Search for another word.")
                val newest = versions(p).first()
                val needs = needs(newest)
                val refused = if (p["name"] == "old") mapOf("error" to "Old 0.1.0 was revoked: it sent errors to a wrong host.",
                    "hint" to "Wait for a fixed version.") else null
                val steps = (needs.keys.filter { !installed.containsKey(it) }.mapNotNull { entry(it) } + p).map { e ->
                    val v = versions(e).first()
                    mapOf("name" to e["name"], "version" to v["version"], "title" to e["title"], "trust" to e["trust"], "publisher" to e["publisher"],
                        "publisher_title" to e["publisher_title"], "verified" to e["verified"], "permissions" to v["permissions"], "size" to 1000,
                        "needed_by" to if (e["name"] == p["name"]) null else p["name"], "source" to "keel")
                }
                val plan = if (refused != null) null else mapOf("name" to p["name"], "version" to newest["version"], "title" to p["title"],
                    "source" to "keel", "install" to steps, "turn_on" to emptyList<String>(), "checks" to listOf("sha256 matches the catalog"))
                200 to (hit(p) + mapOf("versions" to versions(p), "needs" to needs,
                    "checks" to listOf("sha256 matches the catalog", "signed by keel, a key this keel trusts"), "plan" to plan, "refused" to refused))
            }
            path == "/marketplace/refresh" -> 200 to mapOf("sources" to sourcesStatus.filter { query(q)["source"].isNullOrBlank() || it["id"] == query(q)["source"] })
            path == "/marketplace/installed" -> 200 to mapOf("plugins" to installed.values.sortedBy { it["name"] as String },
                "pending_restart" to pending(), "problems" to emptyList<Any>(), "mode" to "on")
            path == "/marketplace/install" -> {
                val name = body?.path("name")?.asText().orEmpty()
                refusals["install:$name"]?.let { return it }
                val p = entry(name) ?: return 404 to mapOf("error" to "No catalog lists a plugin called $name.", "hint" to "Search for another word.")
                if (installed.containsKey(name)) return 409 to mapOf("error" to "${p["title"]} is installed already.", "installed" to installed[name]!!["version"])
                val v = body?.path("version")?.asText(null) ?: versions(p).first()["version"] as String
                put(name, v, "marketplace", null)
                200 to answer(name, v, null)
            }
            path == "/marketplace/install-file" -> {
                refusals["install-file"]?.let { return it }
                put("hello", "0.1.0-beta.1", "file", null)
                200 to answer("hello", "0.1.0-beta.1", null, mapOf("source" to "file"))
            }
            parts[0] == "installed" && parts.size == 3 && parts[2] == "update" -> {
                val name = parts[1]
                refusals["update:$name"]?.let { return it }
                val have = installed[name] ?: return 404 to mapOf("error" to "$name is not installed.")
                val v = body?.path("version")?.asText(null) ?: versions(entry(name)!!).first()["version"] as String
                val adds = more[name]
                if (adds != null && body?.path("allow_more_permissions")?.asBoolean() != true) {
                    return 409 to mapOf("error" to "${have["title"]} $v asks for more permissions than ${have["version"]}.",
                        "hint" to "A person approves the new permissions first.", "more" to adds, "installed" to have["version"], "version" to v)
                }
                val from = have["version"]
                put(name, v, "marketplace", mapOf("version" to from, "source" to "marketplace"))
                200 to answer(name, v, from as String?)
            }
            parts[0] == "installed" && parts.size == 3 && parts[2] == "rollback" -> {
                val have = installed[parts[1]] ?: return 404 to mapOf("error" to "${parts[1]} is not installed.")
                @Suppress("UNCHECKED_CAST")
                val previous = (have["previous"] as? Map<String, Any?>)?.get("version") ?: return 409 to mapOf(
                    "error" to "There is no kept version to go back to.", "hint" to "keel keeps the version before the last update.")
                val now = have["version"]
                have["previous"] = mapOf("version" to now)
                have["version"] = previous
                pendingRestart = true
                200 to mapOf("name" to parts[1], "version" to previous, "from" to now, "pending_restart" to pending())
            }
            parts[0] == "installed" && parts.size == 2 && method == "PUT" -> {
                val have = installed[parts[1]] ?: return 404 to mapOf("error" to "${parts[1]} is not installed.")
                have["on"] = body?.path("on")?.asBoolean() ?: true
                pendingRestart = true
                200 to mapOf("name" to parts[1], "on" to have["on"], "also" to emptyList<String>(), "pending_restart" to pending())
            }
            parts[0] == "installed" && parts.size == 2 && method == "DELETE" -> {
                refusals["remove:${parts[1]}"]?.let { return it }
                val gone = installed.remove(parts[1]) ?: return 404 to mapOf("error" to "${parts[1]} is not installed.")
                pendingRestart = true
                200 to mapOf("name" to parts[1], "removed" to gone["version"], "data" to query(q)["data"], "back_to_image" to null,
                    "pending_restart" to pending())
            }
            path == "/marketplace/sources" && method == "PUT" -> {
                @Suppress("UNCHECKED_CAST")
                val list = body?.path("sources")?.map { mapper.convertValue(it, Map::class.java) as Map<String, Any?> }.orEmpty()
                if (list.any { it["id"] != "keel" && !(it["url"] as? String).orEmpty().startsWith("https://") }) {
                    return 400 to mapOf("error" to "A catalog URL must start with https://", "hint" to "http is only for tests on this computer.")
                }
                val official = OFFICIAL + mapOf("on" to (list.firstOrNull { it["id"] == "keel" }?.get("on") ?: true))
                sources.clear()
                sources += listOf(official) + list.filter { it["id"] != "keel" }
                200 to mapOf("sources" to sources)
            }
            path == "/marketplace/sources" -> 200 to mapOf("sources" to sources)
            path == "/marketplace/rules" && method == "PUT" -> {
                body?.fields()?.forEach { (k, v) -> if (DEFAULT_RULES.containsKey(k)) rules[k] = v.asBoolean() }
                200 to rules.toMap()
            }
            path == "/marketplace/rules" -> 200 to rules.toMap()
            path == "/marketplace/sets" -> 200 to mapOf("sets" to listOf(
                mapOf("id" to "developer", "title" to "Developer", "summary" to "Read and change code with agents.",
                    "plugins" to DEVELOPER, "missing" to DEVELOPER.filter { !installed.containsKey(it) },
                    "off" to DEVELOPER.filter { installed[it]?.get("on") == false }),
            ))
            else -> 404 to mapOf("error" to "no route $path")
        }
    }

    companion object {
        val CATEGORIES = listOf("code", "knowledge", "tickets", "review", "product", "other")
        val DEVELOPER = listOf("code", "git", "review", "db", "ci", "graph", "keelbot")
        val DEFAULT_RULES = mapOf("agents_may_ask" to true, "allow_unverified" to false, "check_daily" to true, "restart_when_idle" to false)
        val OFFICIAL = mapOf("id" to "keel", "title" to "keel marketplace", "url" to "https://keel-studio.github.io/keel-marketplace/v1/index.json",
            "key" to "RWQBAgMEBQYHCHm1Vi6P5lT5QHixEuipi6eQH4U65pW+1+DjkQutBJZk", "on" to true, "official" to true)
    }
}
