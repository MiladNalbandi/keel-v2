package keel.api.common

import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.io.ClassPathResource
import org.springframework.core.io.Resource
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry
import org.springframework.http.CacheControl
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
            // index.html names the current JavaScript file, so it is always re-checked (a browser that kept an old one
            // kept running the old app after an update); the hashed files under /assets never change.
            .setCacheControl(CacheControl.noCache())
            .resourceChain(true)
            .addResolver(SpaResolver())
        registry.addResourceHandler("/assets/**")
            .addResourceLocations("classpath:/static/assets/")
            .setCacheControl(CacheControl.maxAge(java.time.Duration.ofDays(365)).cachePublic().immutable())
    }

    class SpaResolver : PathResourceResolver() {
        override fun getResource(resourcePath: String, location: Resource): Resource? {
            if (resourcePath.substringBefore('/') in NOT_WEB) return null
            val index = ClassPathResource("static/index.html")
            if (resourcePath.isEmpty() || resourcePath == "index.html") return if (index.exists()) index else null
            val found = super.getResource(resourcePath, location)
            if (found != null && found.exists() && found.isReadable) return found
            // Paths with a file extension are real asset requests: do not answer with index.html.
            if (resourcePath.substringAfterLast('/').contains('.')) return null
            return if (index.exists()) index else null
        }

        companion object {
            /**
             * First path parts that are never the web app, so they answer 404 instead of index.html: the api, the
             * engine's internal calls, "plugins" (plugin web files, keel.api.pluginhost: a missing one must fail, not
             * load the app as JavaScript), and "keel-v1", where 0.4.0 still proxied keel v1's dashboard (removed in
             * 0.4.1: an old bookmark fails clearly instead of opening an empty page).
             */
            val NOT_WEB = setOf("api", "internal", "plugins", "keel-v1")
        }
    }
}
