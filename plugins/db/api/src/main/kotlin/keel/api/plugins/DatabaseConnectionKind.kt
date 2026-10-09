package keel.api.plugins

import keel.api.connections.ConnectionField
import keel.api.connections.ConnectionKind
import org.springframework.stereotype.Component

/** Connections › Databases: the Database plugin's connections, per project (DatabaseService). */
@Component
class DatabaseConnectionKind : ConnectionKind {
    override val kind = "database"
    override val title = "Databases"
    override val scope = "project"
    override val order = 40
    override val fields = listOf(
        ConnectionField("name", "Name", required = true),
        // the address holds the password, so all of it is a secret (the web sees it without the password)
        ConnectionField("url", "Address", "secret", required = true),
        ConnectionField("env", "Environment", "choice", choices = DatabaseService.ENVS.toList()),
    )
}
