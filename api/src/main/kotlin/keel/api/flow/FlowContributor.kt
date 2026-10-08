package keel.api.flow

/**
 * What a part adds to a project's flows. FlowService and WorkflowService ask every FlowContributor bean; core knows no
 * part by name. Every method has a default, so a contributor implements only what it needs.
 *
 * Part of keel's api SDK (keel-api-sdk): a plugin may implement it.
 */
interface FlowContributor {
    /**
     * Secrets an agent call of this project may use, by the engine's key name (`github`, `db:<name>`, …). They go with
     * every start, resume and rewind; the engine keeps them in memory only. A later contributor wins a clash.
     */
    fun keys(pid: String): Map<String, String> = emptyMap()

    /**
     * Settings sent to the engine with a flow, by the engine's names (snake_case, as in its ThreadSettings). They come
     * after core's own settings; a null value is sent as null.
     */
    fun settings(pid: String): Map<String, Any?> = emptyMap()

    /**
     * Whether a workflow template that belongs to [plugin] shows for this project. null = not mine. The first
     * contributor that answers decides; a template no contributor claims stays hidden.
     */
    fun templateOn(pid: String, plugin: String): Boolean? = null
}
