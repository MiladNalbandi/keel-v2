package keel.api.review

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

/** What the Code Review part's acting MCP tools send is at the write level; its settings and GitLab sign-in stay admin. */
class ReviewMcpRoutesTest {
    private val routes = ReviewMcpRoutes()
    private fun write(method: String, path: String) = routes.write.any { (m, re) -> m == method && re.matches(path) }

    @Test
    fun `every acting tool's route is write`() {
        val p = "/api/projects/shop/review"
        listOf(
            "POST $p/drafts", "PUT $p/drafts/dr_1", "DELETE $p/drafts/dr_1", "POST $p/drafts/move", "POST $p/viewed", "PUT $p/base",
            "POST $p/threads/th1/reply", "POST $p/threads/th1/resolve", "POST $p/threads/th1/judge", "POST $p/threads/judge",
            "POST $p/threads/th1/buttons/explain-it", "POST $p/threads/th1/address", "POST $p/ai/overview", "POST $p/ai/findings",
            "POST $p/ai/findings/f1", "POST $p/ai/findings/f1/fix", "POST $p/submit", "POST $p/merge", "POST $p/checkout",
        ).forEach { r ->
            val (m, path) = r.split(" ")
            assertThat(write(m, path)).describedAs(r).isTrue()
        }
    }

    @Test
    fun `settings and connections are not`() {
        listOf(
            "PUT /api/projects/shop/review/settings", "PUT /api/review/settings", "PUT /api/gitlab", "DELETE /api/gitlab",
            "POST /api/projects/shop/review/threads/th1/buttons/a/b", "DELETE /api/projects/shop/review/threads/th1",
        ).forEach { r ->
            val (m, path) = r.split(" ")
            assertThat(write(m, path)).describedAs(r).isFalse()
        }
    }
}
