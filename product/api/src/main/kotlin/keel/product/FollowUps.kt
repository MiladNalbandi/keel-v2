package keel.product

import keel.api.addons.FeatureService
import keel.api.common.Time
import keel.api.notifications.NotificationService
import org.slf4j.LoggerFactory
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component
import java.time.Instant
import java.time.temporal.ChronoUnit

data class FollowUpCheck(val due: Int, val reminded: List<String>, val revisited: List<String>)

/**
 * The follow-ups of every initiative: a gate that waits too long, a question nobody answered, a parked idea that comes
 * back, the outcome checks after a release. A due one becomes a notification (and, if it repeats, is due again later).
 */
@Component
class FollowUps(
    private val store: ProductStore,
    private val repo: ProductRepo,
    private val notifications: NotificationService,
    private val features: FeatureService,
    private val props: ProductProperties,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    @Scheduled(fixedDelayString = "\${keel.product.tick-ms:300000}", initialDelayString = "\${keel.product.tick-ms:300000}")
    fun tick() {
        if (!props.scheduler || !features.partOn("product")) return
        runCatching { check(Instant.now()) }.onFailure { log.warn("keel Product follow-ups: {}", it.message) }
    }

    @Synchronized
    fun check(now: Instant): FollowUpCheck {
        val reminded = mutableListOf<String>()
        val revisited = mutableListOf<String>()
        val due = store.openFollowUps().filter { Instant.parse(it.dueAt) <= now }
        for (f in due) {
            val ini = store.initiative(f.initiativeId) ?: continue
            if (f.kind == "revisit" && ini.status == "parked") {
                store.update(ini.copy(status = "ready", revisitAt = null))
                store.event(ini.id, InitiativeService.KEEL, "decision", "Back from Not now: look at it again")
                revisited += ini.id
            }
            notifications.create("review", repo.projectId(), "${ini.id}: ${f.text}".take(200),
                (f.owner?.let { "For $it. " } ?: "") + "Follow-up of ${ini.title}.", "/initiatives/${ini.id}")
            val at = Time.now()
            store.saveFollowUp(if (f.repeatDays != null && f.repeatDays > 0 && f.kind != "revisit")
                f.copy(dueAt = now.plus(f.repeatDays.toLong(), ChronoUnit.DAYS).truncatedTo(ChronoUnit.SECONDS).toString(), lastRemindedAt = at)
            else f.copy(doneAt = if (f.kind in setOf("revisit", "outcome")) at else null, lastRemindedAt = at,
                dueAt = if (f.kind in setOf("revisit", "outcome")) f.dueAt else now.plus(7, ChronoUnit.DAYS).truncatedTo(ChronoUnit.SECONDS).toString()))
            store.event(ini.id, InitiativeService.KEEL, "follow_up", "Reminded: ${f.text}")
            reminded += f.id
        }
        return FollowUpCheck(due.size, reminded, revisited)
    }
}
