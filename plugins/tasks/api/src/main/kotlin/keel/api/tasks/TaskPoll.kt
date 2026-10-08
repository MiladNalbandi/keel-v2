package keel.api.tasks

import org.slf4j.LoggerFactory
import org.springframework.beans.factory.ObjectProvider
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component

/**
 * The background poll, every minute: the tracker syncs each connection whose poll interval passed (the Jira plugin),
 * and every second tick keel reads the PR reviews of the tasks in review.
 */
@Component
class TaskPoll(
    private val tasks: TaskService,
    private val trackers: ObjectProvider<TicketTracker>,
    private val props: TaskProperties,
) {
    private val log = LoggerFactory.getLogger(javaClass)
    @Volatile private var ticks = 0L

    @Scheduled(fixedDelayString = "\${keel.tasks.tick-ms:60000}", initialDelayString = "\${keel.tasks.tick-ms:60000}")
    fun tick() {
        if (!props.scheduler) return
        trackers.orderedStream().forEach { t -> runCatching { t.poll() }.onFailure { log.warn("ticket sync: {}", it.message) } }
        if (ticks++ % 2 == 0L) runCatching { tasks.checkReviews(null) }.onFailure { log.warn("PR review check: {}", it.message) }
    }
}
