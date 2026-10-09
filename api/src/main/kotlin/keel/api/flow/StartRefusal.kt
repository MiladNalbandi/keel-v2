package keel.api.flow

import keel.api.common.ApiException
import keel.api.engine.EngineError

/**
 * v0.16.0 the engine refused a flow's start, and a part can answer better than the engine's error alone. The
 * marketplace: a workflow that needs plugins which are not loaded (needs_plugins, docs/plugins/13-step4-contract.md
 * §8) opens an install request for each one and lists them. Null: not this part's refusal.
 */
fun interface StartRefusal {
    fun answer(e: EngineError, pid: String, workflow: String): ApiException?
}
