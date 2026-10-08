package keel.api.jira

import keel.api.connections.ConnectionField
import keel.api.connections.ConnectionKind
import org.springframework.stereotype.Component

/** Connections › Jira: per project, where keel reads the tickets (JiraService, PUT /api/projects/{pid}/jira). */
@Component
class JiraConnectionKind : ConnectionKind {
    override val kind = "jira"
    override val title = "Jira"
    override val scope = "project"
    override val order = 10
    override val fields = listOf(
        ConnectionField("kind", "Jira", "choice", required = true, choices = listOf("cloud", "server")),
        ConnectionField("base_url", "Address", "url", required = true),
        ConnectionField("email", "Account e-mail (Cloud)"),
        ConnectionField("token", "Token", "secret", required = true),
        ConnectionField("project_key", "Project key"),
        ConnectionField("board_id", "Board"),
        ConnectionField("jql", "JQL"),
        ConnectionField("poll_minutes", "Minutes between syncs", "number"),
    )
}
