package keel.api.review

import keel.api.connections.ConnectionField
import keel.api.connections.ConnectionKind
import org.springframework.stereotype.Component

/** Connections › GitLab: the server and token Code Review uses for merge requests (GitLabConnection). */
@Component
class GitLabConnectionKind : ConnectionKind {
    override val kind = "gitlab"
    override val title = "GitLab"
    override val scope = "keel"
    override val order = 30
    override val fields = listOf(
        ConnectionField("url", "Address", "url", required = true),
        ConnectionField("token", "Token", "secret", required = true),
    )
}
