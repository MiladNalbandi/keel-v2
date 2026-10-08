package keel.api.pluginhost

import org.flywaydb.core.Flyway
import org.slf4j.LoggerFactory
import org.springframework.boot.autoconfigure.flyway.FlywayMigrationStrategy
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import java.nio.file.Paths
import javax.sql.DataSource

/**
 * Database migrations: keel's own first (as Spring Boot would run them), then each resolved plugin's `migrations`
 * folder, in the plugins' order, each with its own history table `<name>_schema_history` (keel's history never sees
 * them). Spring Boot runs this strategy in its flyway initializer, and the beans that use the database wait for that
 * initializer, so a plugin's tables exist before anything reads them. A plugin migration that fails stops the start,
 * like a failing core one; keel-start then falls back to the last good set of plugins.
 */
@Configuration
class PluginMigrations {
    private val log = LoggerFactory.getLogger(PluginMigrations::class.java)

    @Bean
    fun pluginMigrationStrategy(host: PluginHost): FlywayMigrationStrategy = FlywayMigrationStrategy { core ->
        core.migrate()
        host.plugins.filter { it.migrations != null }.forEach { migrate(core.configuration.dataSource, it) }
    }

    private fun migrate(dataSource: DataSource, p: ResolvedPlugin) {
        val folder = Paths.get(p.dir).resolve(p.migrations!!).normalize()
        log.info("plugins: migrations of {} {} from {}", p.name, p.version, folder)
        try {
            Flyway.configure()
                .dataSource(dataSource)
                .locations("filesystem:$folder")
                .table("${p.name}_schema_history")
                .baselineOnMigrate(true)
                .baselineVersion("0")
                .load()
                .migrate()
        } catch (e: Exception) {
            throw IllegalStateException("The migrations of plugin ${p.name} ${p.version} failed: ${e.message}", e)
        }
    }
}
