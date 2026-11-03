package keel.api.common

import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.core.Ordered
import org.springframework.core.annotation.Order
import org.springframework.core.io.ClassPathResource
import org.springframework.stereotype.Component
import org.springframework.web.filter.OncePerRequestFilter
import java.security.MessageDigest
import java.util.Base64

/**
 * 0.21.3 the dashboard's Content-Security-Policy (docs/plugins/03-security.md, point 8). docs said the page sends one,
 * but none was sent. Every response of the web app (index.html and its files, not /api, /internal or /plugins, whose
 * routes set their own where they serve a page) gets it:
 *  - scripts only from keel itself, plus the inline import map index.html carries (tools/sdkShims.ts): it is allowed
 *    by its SHA-256, read from the built index.html, so no other inline script runs. Plugins' web parts are modules
 *    under /plugins/… on keel's own origin, so `'self'` covers them; no eval, no remote import().
 *  - styles from keel, inline styles (React's style attributes, mermaid's SVG) and Google Fonts' stylesheet; fonts
 *    from keel, data: and Google Fonts; images from keel, data:, blob: (the ER diagram's export) and https (a
 *    README's badges in a rendered page: an image runs no code);
 *  - the welcome tour's video from GitHub's release downloads; network calls (fetch, the live stream) only to keel;
 *  - no plugins (object), no <base> elsewhere, forms only to keel, and no other site may frame keel.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
class PageSecurity : OncePerRequestFilter() {

    override fun doFilterInternal(request: HttpServletRequest, response: HttpServletResponse, chain: FilterChain) {
        if (webPath(ApiPath.of(request))) {
            response.setHeader("Content-Security-Policy", policy)
            response.setHeader("X-Content-Type-Options", "nosniff")
        }
        chain.doFilter(request, response)
    }

    private fun webPath(path: String): Boolean = path.trimStart('/').substringBefore('/') !in WebConfig.SpaResolver.NOT_WEB

    /** The policy, with the hashes of index.html's inline scripts (none when the jar has no web app: tests). */
    val policy: String by lazy { policyFor(inlineScriptHashes(indexHtml())) }

    private fun indexHtml(): String =
        runCatching { ClassPathResource("static/index.html").takeIf { it.exists() }?.inputStream?.use { String(it.readAllBytes(), Charsets.UTF_8) } }
            .getOrNull() ?: ""

    companion object {
        fun policyFor(scriptHashes: List<String>): String = listOf(
            "default-src 'self'",
            "script-src 'self'" + scriptHashes.joinToString("") { " '$it'" },
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' data: https://fonts.gstatic.com",
            "img-src 'self' data: blob: https:",
            "media-src 'self' https://github.com https://*.githubusercontent.com",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ).joinToString("; ")

        private val INLINE_SCRIPT = Regex("<script\\b([^>]*)>(.*?)</script>", setOf(RegexOption.DOT_MATCHES_ALL, RegexOption.IGNORE_CASE))

        /** sha256-… of each inline <script> (one without src) in [html], as CSP names it. */
        fun inlineScriptHashes(html: String): List<String> = INLINE_SCRIPT.findAll(html)
            .filter { !Regex("\\bsrc\\s*=", RegexOption.IGNORE_CASE).containsMatchIn(it.groupValues[1]) && it.groupValues[2].isNotEmpty() }
            .map { "sha256-" + Base64.getEncoder().encodeToString(MessageDigest.getInstance("SHA-256").digest(it.groupValues[2].toByteArray(Charsets.UTF_8))) }
            .distinct().toList()
    }
}
