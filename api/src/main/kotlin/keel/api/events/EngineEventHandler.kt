package keel.api.events

/**
 * A part's own side effects for engine events (docs/plugins/09-step2-contract.md §4). [EventService] stores every event
 * and keeps core's side effects (threads, agent calls, gates, budget, notifications); then it hands the event to each
 * handler whose [prefix] the event's type starts with: "helper." (KeelBot), "index.done" (projects), "approval." (approvals).
 */
interface EngineEventHandler {
    val prefix: String

    fun handle(event: EngineEvent)
}
