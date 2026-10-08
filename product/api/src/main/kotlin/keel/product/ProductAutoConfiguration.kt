package keel.product

import keel.api.addons.AddonScreen
import keel.api.addons.KeelAddon
import org.flywaydb.core.Flyway
import org.springframework.boot.autoconfigure.AutoConfiguration
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.ComponentScan
import org.springframework.stereotype.Component
import javax.sql.DataSource

/** keel Product's api, loaded only from its own jar (keel-api-product.jar): META-INF/spring/...AutoConfiguration.imports. */
@AutoConfiguration
@ComponentScan(basePackages = ["keel.product"])
@EnableConfigurationProperties(ProductProperties::class)
class ProductAutoConfiguration

/** keel.product.* (application.yml or KEEL_PRODUCT_* env). */
@ConfigurationProperties(prefix = "keel.product")
data class ProductProperties(
    /** React to engine events on the event thread (tests) instead of a background worker. */
    val inlineEffects: Boolean = false,
    /** Look for due follow-ups by itself (off in tests: they call the check endpoint). */
    val scheduler: Boolean = true,
    val tickMs: Long = 300_000,
)

const val PRODUCT_VERSION = "0.1.0-beta.1"

/** What keel's core knows of this add-on (keel.api.addons): its part and its pages. */
@Component
class ProductAddon : KeelAddon {
    override val name = "product"
    override val version = PRODUCT_VERSION
    override val title = "keel Product"
    override val part = "product"
    override val screens = listOf(
        AddonScreen("initiatives", "Initiatives", "Product"),
        AddonScreen("teams", "Teams", "Product"),
    )
}

/** The add-on's own tables, with their own migration history (keel's history never sees them). */
@Component
class ProductSchema(dataSource: DataSource) {
    init {
        Flyway.configure()
            .dataSource(dataSource)
            .locations("classpath:db/product")
            .sqlMigrationPrefix("P")
            .table("product_schema_history")
            .baselineOnMigrate(true)
            .baselineVersion("0")
            .load()
            .migrate()
    }
}
