package keel.api.connections

import org.springframework.beans.factory.ObjectProvider
import org.springframework.stereotype.Component
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController

/** A connection kind as GET /api/connections/kinds shows it. */
data class ConnectionKindView(val kind: String, val title: String, val scope: String, val order: Int, val fields: List<ConnectionField>)

/** Core's own kind: the GitHub token of Connections › GitHub (GITHUB_REPO_TOKEN), for git and ship's pull request. */
@Component
class GitHubConnectionKind : ConnectionKind {
    override val kind = "github"
    override val title = "GitHub"
    override val scope = "keel"
    override val order = 20
    override val fields = listOf(ConnectionField("token", "Token", "secret", required = true))
}

@RestController
class ConnectionKindController(private val kinds: ObjectProvider<ConnectionKind>) {

    /** Every kind of connection keel knows, core's and the plugins', by order. */
    @GetMapping("/api/connections/kinds")
    fun kinds(): List<ConnectionKindView> = kinds.orderedStream().toList()
        .map { ConnectionKindView(it.kind, it.title, it.scope, it.order, it.fields) }
        .sortedWith(compareBy({ it.order }, { it.kind }))
}
