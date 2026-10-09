package keel.api.connections

/**
 * One field a connection asks for. type: text | url | secret | choice | number | list. A secret is kept encrypted
 * and never sent back.
 *
 * Part of keel's api SDK (keel-api-sdk).
 */
data class ConnectionField(
    val key: String,
    val label: String,
    val type: String = "text",
    val required: Boolean = false,
    /** For a choice: the values it may take. */
    val choices: List<String>? = null,
)

/**
 * A kind of connection on the Connections page (GitHub, GitLab, Jira, databases, …). Metadata only: the kind keeps its
 * own endpoints and secrets. keel lists every ConnectionKind bean at GET /api/connections/kinds, by [order].
 *
 * Part of keel's api SDK (keel-api-sdk): a plugin may add one.
 */
interface ConnectionKind {
    val kind: String
    val title: String
    /** keel: one for every project | project: set per project */
    val scope: String
    val order: Int get() = 100
    val fields: List<ConnectionField>
    /** The names of its secrets whose value is one token (GitLab's GITLAB_TOKEN): SecretService removes whitespace from
     *  them when they are saved, as from keel's own tokens. Not shown at GET /api/connections/kinds. */
    val tokens: Set<String> get() = emptySet()
}
