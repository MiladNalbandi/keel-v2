package keel.product

import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.engine.EngineClient
import org.springframework.stereotype.Service

/** The presentation of an initiative: built by the decide flow (product:deck), rebuilt here when the documents changed. */
@Service
class DeckService(private val store: ProductStore, private val repo: ProductRepo, private val engine: EngineClient) {

    fun html(id: String, version: Int? = null): String {
        val d = (if (version != null) store.doc(id, "deck", version) else store.latest(id, "deck"))
            ?: throw NotFound("$id has no presentation yet", "keel builds it with the decision memo.")
        return repo.read(d.path) ?: throw NotFound("The presentation file ${d.path} is gone")
    }

    @Suppress("UNCHECKED_CAST")
    fun rebuild(i: Initiative): ProductDoc {
        val impact = store.latest(i.id, "impact")
        val docs = mapOf(
            "brief" to store.latest(i.id, "brief")?.text, "impact" to impact?.text,
            "impact_repos" to (impact?.data as? Map<String, Any?>)?.get("repos"),
            "decision" to store.latest(i.id, "decision")?.text, "plan" to store.latest(i.id, "plan")?.data,
        ).filterValues { it != null }
        val versions = listOf("brief", "impact", "decision", "plan").mapNotNull { k -> store.latest(i.id, k)?.let { k to it.version } }.toMap()
        val meta = engine.post("/product/deck", mapOf("root" to repo.ensure().root, "initiative" to mapOf("id" to i.id, "title" to i.title,
            "idea" to i.idea, "owner" to (i.owner ?: "Product")), "docs" to docs, "versions" to versions, "recommend" to (i.option ?: "")), long = true)
        val doc = ProductDoc(i.id, "deck", meta.path("version").asInt(), meta.path("path").asText(), meta.path("sha").asText().ifBlank { null }, "",
            null, null, Time.now(), null)
        store.saveDoc(doc)
        store.event(i.id, InitiativeService.YOU, "version", "Built the presentation v${doc.version}", mapOf("kind" to "deck", "version" to doc.version))
        return doc
    }
}
