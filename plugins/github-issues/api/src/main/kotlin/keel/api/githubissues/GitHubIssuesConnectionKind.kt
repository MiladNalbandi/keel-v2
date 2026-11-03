package keel.api.githubissues

import keel.api.connections.ConnectionField
import keel.api.connections.ConnectionKind
import org.springframework.stereotype.Component

/** Connections › GitHub Issues: per project, the repo whose issues become tasks (the token is Connections › GitHub's). */
@Component
class GitHubIssuesConnectionKind : ConnectionKind {
    override val kind = "github-issues"
    override val title = "GitHub Issues"
    override val scope = "project"
    override val order = 11
    override val fields = listOf(
        ConnectionField("repo", "Repository"),
        ConnectionField("follow", "Issues", "choice", choices = GitHubIssuesService.FOLLOW),
        ConnectionField("label", "Label"),
        ConnectionField("poll_minutes", "Minutes between syncs", "number"),
    )
}
