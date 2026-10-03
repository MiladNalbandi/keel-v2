package keel.api.common

import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.io.ClassPathResource
import org.springframework.core.io.Resource
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer
import org.springframework.web.servlet.resource.PathResourceResolver
import javax.sql.DataSource

@Configuration
class DataSourceConfig {
    /** SQLite at $KEEL_DATA/keel.db, WAL mode so SSE readers do not block writers. */
    @Bean
    fun dataSource(props: KeelProperties): DataSource {
        val file = props.dataDir.resolve("keel.db")
        val cfg = HikariConfig().apply {
            jdbcUrl = "jdbc:sqlite:$file?journal_mode=WAL&busy_timeout=10000&foreign_keys=false&synchronous=NORMAL"
            driverClassName = "org.sqlite.JDBC"
            maximumPoolSize = 4
            minimumIdle = 1
            poolName = "keel-sqlite"
        }
        return HikariDataSource(cfg)
    }
}

/** Serves the built web app from classpath:/static with a single-page-app fallback. */
@Configuration
class WebConfig : WebMvcConfigurer {
    override fun addResourceHandlers(registry: ResourceHandlerRegistry) {
        registry.addResourceHandler("/**")
            .addResourceLocations("classpath:/static/")
            .resourceChain(true)
            .addResolver(SpaResolver())
    }

    class SpaResolver : PathResourceResolver() {
        override fun getResource(resourcePath: String, location: Resource): Resource? {
            if (resourcePath.startsWith("api/") || resourcePath == "api" || resourcePath.startsWith("internal/")) return null
            val index = ClassPathResource("static/index.html")
            if (resourcePath.isEmpty() || resourcePath == "index.html") return if (index.exists()) index else null
            val found = super.getResource(resourcePath, location)
            if (found != null && found.exists() && found.isReadable) return found
            // Paths with a file extension are real asset requests: do not answer with index.html.
            if (resourcePath.substringAfterLast('/').contains('.')) return null
            return if (index.exists()) index else null
        }
    }
}
