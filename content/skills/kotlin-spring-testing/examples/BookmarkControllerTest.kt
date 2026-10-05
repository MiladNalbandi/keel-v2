package com.example.api.web

import com.example.api.domain.BookmarkService
import com.ninjasquad.springmockk.MockkBean
import io.mockk.every
import org.junit.jupiter.api.Tag
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest
import org.springframework.http.MediaType.APPLICATION_JSON
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.post

/**
 * Web-slice test: status codes and validation, no database, no whole application context.
 * This is the lowest layer that can express "the API rejects a bookmark without a URL".
 */
@WebMvcTest(BookmarkController::class)
class BookmarkControllerTest(@Autowired val mvc: MockMvc) {

    @MockkBean
    lateinit var service: BookmarkService

    @Tag("AC-001")
    @Test
    fun `AC-001 rejects a bookmark without a url`() {
        mvc.post("/bookmarks") {
            contentType = APPLICATION_JSON
            content = """{"title":"no url here"}"""
        }.andExpect {
            status { isUnprocessableEntity() }
            jsonPath("$.errors[0].field") { value("url") }
        }
    }

    @Tag("AC-001")
    @Test
    fun `AC-001 rejects a url that is not http or https`() {
        mvc.post("/bookmarks") {
            contentType = APPLICATION_JSON
            content = """{"url":"javascript:alert(1)"}"""
        }.andExpect {
            status { isUnprocessableEntity() }
        }
    }

    @Tag("AC-002")
    @Test
    fun `AC-002 accepts a valid url and returns its id`() {
        every { service.create(any()) } returns Bookmark(id = 7, url = "https://x.dev")

        mvc.post("/bookmarks") {
            contentType = APPLICATION_JSON
            content = """{"url":"https://x.dev"}"""
        }.andExpect {
            status { isCreated() }
            jsonPath("$.id") { value(7) }
            jsonPath("$.url") { value("https://x.dev") }
        }
    }
}
