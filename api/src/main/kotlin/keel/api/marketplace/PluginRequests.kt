package keel.api.marketplace

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.node.JsonNodeFactory
import keel.api.approvals.Approval
import keel.api.approvals.ApprovalHandler
import keel.api.approvals.ApprovalService
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.Json
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.engine.EngineError
import keel.api.flow.StartRefusal
import keel.api.notifications.NotificationService
import keel.api.pluginhost.PluginHost
import org.slf4j.LoggerFactory
import org.springframework.http.HttpStatus
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component
import java.time.Duration
import java.time.Instant

/** POST /api/plugins/requests: an agent (or a workflow) asks a person to install a plugin. */
data class PluginRequestBody(
    val name: String = "",
    val version: String? = null,
    /** Why, in the asker's words: the Inbox card shows it. */
    val reason: String = "",
    /** Who asks: keelbot, a flow's agent, mcp, workflow, … */
    val source: String = "",
    val project: String? = null,
)

/** [joined]: a request for this plugin waited already, and this one joined it (its reason was added). */
data class PluginRequestAnswer(
    val id: String,
    val name: String,
    val title: String,
    val version: String?,
    val status: String,
    val joined: Boolean,
    val project: String?,
)

/**
 * The `plugin-install` approvals (docs/plugins/13-step4-contract.md §6, 04-marketplace.md §4.4): agents can ask, a
 * person decides. One waiting request per plugin: a second asker joins it (its reason is added). A request waits 7
 * days, then it ends. Approve installs the plugin (the restart banner shows, or keel restarts by itself when the rule
 * restart_when_idle is on); deny does nothing. The Inbox shows it through [keel.api.approvals.ApprovalInboxSource]:
 * its payload carries what the card shows (title, the reasons, trust, permissions, the plugins it needs).
 * A workflow that needs a plugin which is not loaded (needs_plugins) opens a request for each missing one.
 */
@Component
class PluginRequests(
    private val approvals: ApprovalService,
    private val marketplace: Marketplace,
    private val changes: PluginChanges,
    private val restarts: RestartPlan,
    private val notifications: NotificationService,
    private val log: PluginEventLog,
    private val jdbc: JdbcTemplate,
) : ApprovalHandler, StartRefusal {
    private val logger = LoggerFactory.getLogger(javaClass)

    override val kind = KIND

    // ---- asking ---------------------------------------------------------------------------------------------------

    /** A request: a new one, or the waiting one for this plugin with this reason added. [checkRule]: agents_may_ask. */
    @Synchronized
    fun ask(body: PluginRequestBody, checkRule: Boolean = true): PluginRequestAnswer {
        val name = Marketplace.checkName(body.name)
        val reason = body.reason.trim().take(REASON_MAX)
        if (reason.isEmpty()) throw BadRequest("Say why: a request needs a reason", "The person reads it in the Inbox before they decide.")
        val source = body.source.trim().ifBlank { "agent" }.take(60)
        val project = body.project?.trim()?.ifBlank { null }
        if (project != null && (jdbc.queryForObject("SELECT COUNT(*) FROM projects WHERE id = ?", Int::class.java, project) ?: 0) == 0) {
            throw NotFound("No project $project")
        }
        if (checkRule && !marketplace.ruleValues().agentsMayAsk) {
            throw Conflict("This keel does not let agents ask to install plugins", "Ask the person to install it in Control › Plugins.")
        }
        val asked = mapOf("reason" to reason, "source" to source, "project" to project, "at" to Time.now())
        waitingFor(name)?.let { return join(it, asked) }

        val wanted = body.version?.trim()?.ifBlank { null }
        if (wanted != null && !PluginHost.VERSION.matches(wanted)) throw BadRequest("$wanted is not a version", "A version looks like 1.4.0.")
        val card = card(name, wanted, marketplace.plugin(name))
        val have = installedVersion(card.installed)
        if (have != null && (wanted == null || wanted == have)) {
            throw Conflict("${card.title} is installed already",
                "A new install loads after the next restart; one that is off can be turned on in Control › Plugins.")
        }
        // keel would refuse the install (revoked, the catalog is old, it does not fit): say so now, ask nobody
        if (have == null) card.refused?.let { throw ApiException(HttpStatus.CONFLICT, it.first, it.second) }
        // installed in another version: approving updates it
        val a = open(card, card.heading, reason, source, project, asked, mapOf("update" to (have != null)))
        return answer(a, joined = false)
    }

    /**
     * An update the engine refused because the new version asks for more permissions (409 {more, installed, version}):
     * a request in the Inbox with the difference; approving it updates with those permissions. Null: not that refusal.
     */
    @Synchronized
    fun forMorePermissions(name: String, e: ApiException): ApiException? {
        val extra = e.extra ?: return null
        val more = (extra["more"] as? List<*>)?.map { it.toString() }
        if (e.status != HttpStatus.CONFLICT || more.isNullOrEmpty()) return null
        val version = extra["version"]?.toString()?.ifBlank { null }
        val reason = "The new version${version?.let { " $it" } ?: ""} asks for more permissions: ${more.joinToString("; ")}."
        val asked = mapOf("reason" to reason, "source" to KEEL, "project" to null, "at" to Time.now())
        val id = waitingFor(name)?.let { join(it, asked).id } ?: run {
            val info = runCatching { marketplace.plugin(name) }.getOrNull() ?: JsonNodeFactory.instance.objectNode()
            val card = card(name, version, info)
            open(card, "Update ${card.title}${version?.let { " to $it" } ?: ""}?", reason, KEEL, null, asked,
                mapOf("update" to true, "more" to more, "installed" to extra["installed"])).id
        }
        return ApiException(HttpStatus.CONFLICT, e.message, MORE_HINT, extra + mapOf("requests" to listOf(id)))
    }

    /** A new request: the row, its event and the person's notification. */
    private fun open(card: Card, heading: String, reason: String, source: String, project: String?, asked: Map<String, Any?>,
                     more: Map<String, Any?>): Approval {
        val payload = card.payload + mapOf("reason" to reason, "source" to source, "reasons" to listOf(asked)) + more
        val a = approvals.create(KIND, project ?: "", heading, reason, payload, requestedBy = source, source = source)
        log.record("plugin.request.asked", mapOf("id" to a.id, "name" to card.payload["name"], "version" to card.version, "by" to source,
            "reason" to reason, "project" to project))
        val what = if (more["update"] == true) "update" else "install"
        notifications.create("review", project, "${who(source)} asks to $what ${card.title}", reason.take(300), "/inbox", threadId = a.id)
        return a
    }

    private fun join(a: Approval, asked: Map<String, Any?>): PluginRequestAnswer {
        val payload = Json.readMap(a.payload?.toString())
        val reasons = (payload["reasons"] as? List<*>).orEmpty().filterIsInstance<Map<*, *>>() + listOf(asked)
        val detail = reasons.mapNotNull { it["reason"]?.toString() }.joinToString("\n\n")
        val joined = approvals.amend(a.id, detail, payload + mapOf("reasons" to reasons)) ?: a
        log.record("plugin.request.joined", mapOf("id" to a.id, "name" to payload["name"], "by" to asked["source"], "reason" to asked["reason"]))
        return answer(joined, joined = true)
    }

    /** The waiting request for this plugin, if one waits (and its 7 days are not over). */
    private fun waitingFor(name: String): Approval? {
        tidy()
        return waiting().firstOrNull { Json.readMap(it.payload?.toString())["name"] == name }
    }

    private fun waiting(): List<Approval> = jdbc.queryForList(
        "SELECT id FROM approvals WHERE kind = ? AND status = ? ORDER BY created_at, id", String::class.java, KIND, ApprovalService.WAITING,
    ).mapNotNull { approvals.find(it) }

    private fun answer(a: Approval, joined: Boolean): PluginRequestAnswer {
        val p = a.payload
        return PluginRequestAnswer(a.id, p?.path("name")?.asText("").orEmpty(), p?.path("title")?.asText("").orEmpty(),
            p?.path("version")?.asText(null), a.status, joined, a.projectId.ifBlank { null })
    }

    // ---- the person decides ---------------------------------------------------------------------------------------

    override fun decided(approval: Approval, decision: String, why: String) {
        if (expired(approval)) {
            end(approval)
            throw Conflict("This request ended: nobody answered it in 7 days", "Ask again, or install the plugin in Control › Plugins.")
        }
        val p = approval.payload
        val name = p?.path("name")?.asText("").orEmpty()
        val version = p?.path("version")?.asText(null)
        if (decision == "approve") {
            // the person saw the permissions on the card: an update may add them
            if (p?.path("update")?.asBoolean() == true) changes.update(name, version, allowMore = true, request = approval.id)
            else changes.install(name, version, by = PluginChanges.PERSON, request = approval.id)
            restarts.afterApprovedInstall(marketplace.ruleValues())
        }
        notifications.markDone(approval.id)
        log.record(if (decision == "approve") "plugin.request.approved" else "plugin.request.denied",
            mapOf("id" to approval.id, "name" to name, "version" to version, "by" to PluginChanges.PERSON, "why" to why.ifBlank { null }))
    }

    // ---- 7 days ---------------------------------------------------------------------------------------------------

    /** Ends the requests that waited 7 days (when the waiting ones are listed, and every few minutes). */
    override fun tidy() {
        waiting().filter { expired(it) }.forEach { end(it) }
    }

    @Scheduled(fixedDelay = 600_000L, initialDelay = 60_000L)
    fun expireOld() = tidy()

    private fun expired(a: Approval): Boolean {
        val at = runCatching { Instant.parse(a.createdAt) }.getOrNull() ?: return false
        return at.isBefore(Instant.now().minus(WAITS))
    }

    private fun end(a: Approval) {
        if (!approvals.end(a.id, "expired", "Nobody answered in 7 days, so the request ended.")) return
        notifications.markDone(a.id)
        log.record("plugin.request.expired", mapOf("id" to a.id, "name" to a.payload?.path("name")?.asText(null), "by" to "keel"))
    }

    // ---- needs_plugins --------------------------------------------------------------------------------------------

    /**
     * The engine refused a flow's start because its workflow needs plugins that are not loaded (409 {error, missing}):
     * open a request for each one (or join the waiting one) and answer 409 {error, missing, requests}.
     */
    override fun answer(e: EngineError, pid: String, workflow: String): ApiException? {
        if (e.status != HttpStatus.CONFLICT) return null
        val missing = e.body?.path("missing")?.takeIf { it.isArray }?.mapNotNull { it.asText("").trim().ifBlank { null } }
        if (missing.isNullOrEmpty()) return null
        val ids = missing.mapNotNull { name ->
            try {
                ask(PluginRequestBody(name, reason = "the workflow $workflow needs it", source = "workflow", project = pid), checkRule = false).id
            } catch (x: ApiException) {
                logger.info("plugins: no install request for {} ({}): {}", name, workflow, x.message)
                null
            }
        }
        // the requests wait in the Inbox: say so (the engine's hint says to install it by hand)
        val hint = if (ids.isNotEmpty()) NEEDS_HINT else e.hint ?: NEEDS_HINT
        return ApiException(HttpStatus.CONFLICT, e.message, hint, mapOf("missing" to missing, "requests" to ids))
    }

    // ---- the card -------------------------------------------------------------------------------------------------

    /**
     * What the Inbox card shows, read from the engine's answer for one plugin (versions, permissions, needs, plan).
     * [refused]: the engine would refuse its install (error, hint).
     */
    internal data class Card(val title: String, val version: String?, val installed: JsonNode?, val payload: Map<String, Any?>,
                             val refused: Pair<String, String?>? = null) {
        val heading get() = "Install $title${version?.let { " $it" } ?: ""}?"
    }

    internal fun card(name: String, asked: String?, info: JsonNode): Card {
        val title = info.path("title").asText("").ifBlank { name }
        val versions = info.path("versions").takeIf { it.isArray }?.toList().orEmpty()
        val plan = info.path("plan")
        val version = asked?.trim()?.ifBlank { null }
            ?: plan.path("version").asText("").ifBlank { null }
            ?: planItems(plan).firstOrNull { it.path("name").asText() == name }?.path("version")?.asText("")?.ifBlank { null }
            ?: info.path("version").asText("").ifBlank { null }
            ?: info.path("latest").asText("").ifBlank { null }
            ?: newest(versions.mapNotNull { it.path("version").asText("").ifBlank { null } })
        val entry = versions.firstOrNull { it.path("version").asText() == version }
        val permissions = entry?.path("permissions")?.takeIf { !it.isMissingNode && !it.isNull }
            ?: info.path("permissions").takeIf { !it.isMissingNode && !it.isNull }
        // needs: the version's requires.plugins, or the answer's {name: range} (or a list of names)
        val needsNode = info.path("needs")
        val needs = entry?.path("requires")?.path("plugins")?.takeIf { it.isObject }?.fieldNames()?.asSequence()?.toList()
            ?: needsNode.takeIf { it.isObject }?.fieldNames()?.asSequence()?.toList()
            ?: needsNode.takeIf { it.isArray }?.mapNotNull { n -> (if (n.isObject) n.path("name").asText("") else n.asText("")).ifBlank { null } }
            ?: emptyList()
        val installs = planItems(plan).filter { it.path("name").asText() != name }.map { item ->
            mapOf("name" to item.path("name").asText(), "title" to item.path("title").asText("").ifBlank { null },
                "version" to item.path("version").asText("").ifBlank { null })
        }
        val publisher = info.path("publisher_title").asText("").ifBlank { null }
            ?: info.path("publisher").let { if (it.isObject) it.path("name").asText("").ifBlank { it.path("title").asText("") } else it.asText("") }
        val refused = info.path("refused").takeIf { it.isObject }?.let { r ->
            r.path("error").asText("").ifBlank { null }?.let { it to r.path("hint").asText("").ifBlank { null } }
        }
        val verified = info.path("verified").takeIf { it.isBoolean }?.asBoolean()
            ?: info.path("publisher").path("verified").takeIf { it.isBoolean }?.asBoolean()
        val payload = linkedMapOf<String, Any?>(
            "name" to name, "title" to title, "version" to version,
            "summary" to info.path("summary").asText("").ifBlank { null },
            "trust" to info.path("trust").asText("").ifBlank { null },
            "publisher" to publisher.ifBlank { null }, "verified" to verified,
            "permissions" to permissions, "needs" to needs, "installs" to installs,
        )
        return Card(title, version, info.path("installed").takeIf { !it.isMissingNode && !it.isNull }, payload, refused)
    }

    /** The plugins a plan installs: a list, or {plugins: [...]} / {install: [...]}. */
    private fun planItems(plan: JsonNode): List<JsonNode> = when {
        plan.isArray -> plan.toList()
        plan.path("plugins").isArray -> plan.path("plugins").toList()
        plan.path("install").isArray -> plan.path("install").toList()
        else -> emptyList()
    }.filter { it.isObject }

    /** The installed version ("" when the engine only says true), or null when it is not installed. */
    private fun installedVersion(installed: JsonNode?): String? = when {
        installed == null -> null
        installed.isBoolean -> if (installed.asBoolean()) "" else null
        installed.isTextual -> installed.asText().ifBlank { null }
        installed.isObject -> installed.path("version").asText("")
        else -> null
    }

    companion object {
        const val KIND = "plugin-install"
        val WAITS: Duration = Duration.ofDays(7)
        const val REASON_MAX = 2000
        const val NEEDS_HINT = "keel asked you in the Inbox to install it. Approve the request, restart keel, then start the flow again."
        const val MORE_HINT = "A request with the new permissions waits in the Inbox: approve it there to update."
        /** keel itself asks (an update that needs more permissions). */
        const val KEEL = "keel"

        fun who(source: String) = when (source.lowercase()) {
            "keelbot" -> "KeelBot"
            "workflow" -> "A workflow"
            "mcp" -> "Claude Code"
            KEEL -> "keel"
            "agent", "" -> "An agent"
            else -> source
        }

        /** The highest x.y.z of [versions] (a pre-release counts below its release). */
        fun newest(versions: List<String>): String? = versions.maxWithOrNull { a, b -> compare(a, b) }

        private fun compare(a: String, b: String): Int {
            fun parts(v: String) = v.substringBefore('-').substringBefore('+').split('.').map { it.toIntOrNull() ?: 0 }
            val pa = parts(a)
            val pb = parts(b)
            for (i in 0 until maxOf(pa.size, pb.size)) {
                val c = (pa.getOrElse(i) { 0 }).compareTo(pb.getOrElse(i) { 0 })
                if (c != 0) return c
            }
            return when {
                '-' in a && '-' !in b -> -1
                '-' !in a && '-' in b -> 1
                else -> a.compareTo(b)
            }
        }
    }
}
