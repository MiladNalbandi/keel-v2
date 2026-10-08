package keel.product

import keel.api.events.EngineEvent
import keel.api.events.EngineEventStored
import org.slf4j.LoggerFactory
import org.springframework.context.event.EventListener
import org.springframework.stereotype.Component
import java.util.concurrent.Executors

/**
 * Follows the stage flows: their documents (product.* events from the engine add-on), their gates and their end. The
 * event thread only checks (one indexed query) whether the thread is a product run; the work runs on one background
 * worker, in order, so ingesting engine events never waits for keel Product.
 */
@Component
class ProductEvents(
    private val initiatives: InitiativeService,
    private val store: ProductStore,
    private val props: ProductProperties,
) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "product-events").apply { isDaemon = true } }

    @EventListener
    fun on(stored: EngineEventStored) {
        val e = stored.event
        val follows = when {
            e.type.startsWith("product.") -> true
            e.type in setOf("gate.waiting", "gate.decided", "thread.done", "thread.failed") -> e.threadId.isNotBlank() && store.run(e.threadId) != null
            else -> false
        }
        // Runs that started keep their records even when the view is switched to dev meanwhile.
        if (!follows) return
        if (props.inlineEffects) run(e) else worker.execute { run(e) }
    }

    private fun run(e: EngineEvent) {
        try {
            initiatives.onEngineEvent(e)
        } catch (ex: Exception) {
            log.warn("keel Product for {} {}: {}", e.type, e.threadId, ex.message)
        }
    }
}
