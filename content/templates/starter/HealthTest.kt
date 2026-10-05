package app

import org.junit.jupiter.api.Tag
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.get

@WebMvcTest
class HealthTest(@Autowired val mvc: MockMvc) {

    @Tag("AC-000")
    @Test
    fun `AC-000 the app answers on the health endpoint`() {
        mvc.get("/api/ping").andExpect { status { isOk() } }
    }
}
