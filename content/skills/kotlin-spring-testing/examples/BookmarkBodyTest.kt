package com.example.api.contract

import com.atlassian.oai.validator.mockmvc.OpenApiValidationMatchers.openApi
import com.example.api.domain.BookmarkService
import com.example.api.web.Bookmark
import com.example.api.web.BookmarkController
import com.ninjasquad.springmockk.MockkBean
import io.mockk.every
import org.junit.jupiter.api.Tag
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest
import org.springframework.http.MediaType.APPLICATION_JSON
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.get
import org.springframework.test.web.servlet.post

/**
 * Body test: the request and response bodies conform to contracts/openapi.yaml *and* the
 * business content is right. Two assertions, both necessary — schema conformance alone
 * would pass on a response carrying the wrong values.
 *
 * Every endpoint an AC touches should have one of these; it is what makes a contract
 * mismatch fail on the backend as well as the frontend.
 */
@WebMvcTest(BookmarkController::class)
class BookmarkBodyTest(@Autowired val mvc: MockMvc) {

    @MockkBean
    lateinit var service: BookmarkService

    @Tag("AC-002")
    @Test
    fun `AC-002 created bookmark body matches the contract`() {
        every { service.create(any()) } returns
            Bookmark(id = 7, url = "https://x.dev", createdAt = FIXED_INSTANT)

        mvc.post("/bookmarks") {
            contentType = APPLICATION_JSON
            content = """{"url":"https://x.dev"}"""
        }.andExpect {
            status { isCreated() }
            // Fails if the body breaks openapi.yaml: missing required field, wrong type,
            // wrong format, undocumented status code.
            match(openApi().isValid("contracts/openapi.yaml"))
            jsonPath("$.id") { value(7) }
            jsonPath("$.url") { value("https://x.dev") }
            jsonPath("$.createdAt") { exists() }
        }
    }

    @Tag("AC-003")
    @Test
    fun `AC-003 the list response matches the contract`() {
        every { service.list() } returns listOf(Bookmark(id = 7, url = "https://x.dev"))

        mvc.get("/bookmarks").andExpect {
            status { isOk() }
            match(openApi().isValid("contracts/openapi.yaml"))
            jsonPath("$.items.length()") { value(1) }
            jsonPath("$.items[0].url") { value("https://x.dev") }
        }
    }

    private companion object {
        val FIXED_INSTANT = java.time.Instant.parse("2026-01-01T00:00:00Z")
    }
}
