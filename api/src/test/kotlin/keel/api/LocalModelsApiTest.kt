package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** 0.18.46 local model servers (provider local) and Google Vertex AI (provider vertex): settings, secrets, what the
 *  engine gets, the catalog, Connections, and the project setting "local models only". */
class LocalModelsApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate

    private val key = "sk-local-key-0123456789abcdef" // keel:allow-secret (a test value)
    private val saKey = """{"type": "service_account", "project_id": "my-proj", "client_email": "keel@my-proj.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEfake\n-----END PRIVATE KEY-----\n"}""" // keel:allow-secret (a fake key)

    @AfterEach
    fun clean() {
        jdbc.update("DELETE FROM kv WHERE key IN ('local_models', 'vertex')")
        jdbc.update("DELETE FROM secrets WHERE name LIKE 'local.%' OR name = 'VERTEX_CREDENTIALS'")
        engine.extraRoutes.remove("/providers/local/test")
        put("/api/settings/general", mapOf("default_model" to mapOf("provider" to "fake", "mode" to "api", "model" to "fake")))
    }

    @Test
    fun `0_18_64 a key goes over http only inside the network, and a new host drops the saved key`() {
        // plain http to a machine on the internet: refused; https, this machine and the local network: fine
        put("/api/providers/local/endpoints/far", mapOf("kind" to "ollama", "url" to "http://models.example.com:11434", "key" to key))
            .andExpect(status().isBadRequest)
        for (url in listOf("https://models.example.com", "http://127.0.0.1:11434", "http://host.docker.internal:11434",
                "http://gpu-box.local:8000/v1", "http://192.168.1.20:1234/v1", "http://172.20.0.3:8000/v1")) {
            put("/api/providers/local/endpoints/near", mapOf("kind" to "ollama", "url" to url)).andExpect(status().isOk)
        }
        put("/api/providers/local/endpoints/lab", mapOf("kind" to "ollama", "url" to "http://10.0.0.5:11434", "key" to key)).andExpect(status().isOk)
        // the same host (another path): the key stays; another host: the key is dropped, not sent there
        put("/api/providers/local/endpoints/lab", mapOf("kind" to "ollama", "url" to "http://10.0.0.5:11434/v1")).andExpect(status().isOk)
        assertThat(get("/api/providers/local").json()["endpoints"].first { it["name"].asText() == "lab" }["key_set"].asBoolean()).isTrue()
        put("/api/providers/local/endpoints/lab", mapOf("kind" to "ollama", "url" to "https://other.example.com")).andExpect(status().isOk)
        assertThat(get("/api/providers/local").json()["endpoints"].first { it["name"].asText() == "lab" }["key_set"].asBoolean()).isFalse()
        // with a new key for the new host, it is kept
        put("/api/providers/local/endpoints/lab", mapOf("kind" to "ollama", "url" to "https://third.example.com", "key" to key)).andExpect(status().isOk)
        assertThat(get("/api/providers/local").json()["endpoints"].first { it["name"].asText() == "lab" }["key_set"].asBoolean()).isTrue()
    }

    @Test
    fun `a local model server is saved, its key never comes back, and the engine gets it as the provider's key`() {
        val empty = get("/api/providers/local").andExpect(status().isOk).json()
        assertThat(empty["enabled"].asBoolean()).isFalse()
        assertThat(empty["defaults"]["ollama"].asText()).isEqualTo("http://host.docker.internal:11434")
        assertThat(empty["kinds"].map { it.asText() }).contains("ollama", "lmstudio", "vllm", "llamacpp", "openai-compatible")

        val saved = put("/api/providers/local/endpoints/lab", mapOf("kind" to "lmstudio", "url" to "http://10.0.0.5:1234/v1/", "key" to key,
            "context" to 16384, "price_in" to 0.5)).andExpect(status().isOk)
        assertThat(saved.andReturn().response.contentAsString).doesNotContain(key)
        val v = saved.json()
        assertThat(v["enabled"].asBoolean()).isTrue()            // the first server turns local models on
        val ep = v["endpoints"].single()
        assertThat(ep["url"].asText()).isEqualTo("http://10.0.0.5:1234/v1")
        assertThat(ep["key_set"].asBoolean()).isTrue()
        assertThat(ep["key_hint"].asText()).isEqualTo("…def")
        assertThat(ep["context"].asInt()).isEqualTo(16384)

        // saving again without a key keeps it; clear_key removes it
        put("/api/providers/local/endpoints/lab", mapOf("kind" to "lmstudio", "url" to "http://10.0.0.5:1234/v1", "context" to 16384,
            "price_in" to 0.5)).andExpect(status().isOk)
        assertThat(get("/api/providers/local").json()["endpoints"][0]["key_set"].asBoolean()).isTrue()

        // the engine: the catalog and a flow get {enabled, endpoints:[{…, key}]}
        get("/api/providers/models").andExpect(status().isOk)
        val sent = mapper.readTree(engine.lastBody("/providers/models")!!["keys"]["local"].asText())
        assertThat(sent["endpoints"][0]["key"].asText()).isEqualTo(key)
        assertThat(sent["endpoints"][0]["price_in"].asDouble()).isEqualTo(0.5)

        // Connections lists it as ready
        val conn = get("/api/connections").json()["providers"].first { it["id"].asText() == "local" }
        assertThat(conn["modes"].single()["ready"].asBoolean()).isTrue()
        assertThat(conn["key_set"].asBoolean()).isTrue()
        assertThat(get("/api/connections").andReturn().response.contentAsString).doesNotContain(key)

        put("/api/providers/local/endpoints/lab", mapOf("kind" to "lmstudio", "url" to "http://10.0.0.5:1234/v1", "clear_key" to true))
        assertThat(get("/api/providers/local").json()["endpoints"][0]["key_set"].asBoolean()).isFalse()

        // off: the catalog no longer asks for it
        put("/api/providers/local", mapOf("enabled" to false)).andExpect(status().isOk)
        engine.calls.clear()
        get("/api/providers/models").andExpect(status().isOk)
        assertThat(engine.calls.last { it.path == "/providers/models" }.method).isEqualTo("GET")

        delete("/api/providers/local/endpoints/lab").andExpect(status().isOk)
        delete("/api/providers/local/endpoints/lab").andExpect(status().isNotFound)
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM secrets WHERE name = 'local.lab'", Int::class.java)).isZero()
    }

    @Test
    fun `local servers are checked`() {
        put("/api/providers/local/endpoints/Bad Name", mapOf("kind" to "ollama", "url" to "http://x")).andExpect(status().isBadRequest)
        put("/api/providers/local/endpoints/ok", mapOf("kind" to "gpt4all", "url" to "http://x")).andExpect(status().isBadRequest)
        put("/api/providers/local/endpoints/ok", mapOf("kind" to "ollama", "url" to "file:///etc/passwd")).andExpect(status().isBadRequest)
        put("/api/providers/local/endpoints/ok", mapOf("kind" to "ollama", "url" to "http://x", "context" to 10)).andExpect(status().isBadRequest)
        put("/api/providers/local/endpoints/ok", mapOf("kind" to "ollama", "url" to "http://x", "price_out" to -1)).andExpect(status().isBadRequest)
    }

    @Test
    fun `test connection asks the engine with the key and Connections' Test sums it up`() {
        engine.extraRoutes["/providers/local/test"] = { body ->
            200 to mapOf("ok" to true, "ms" to 12, "models" to listOf(mapOf("id" to "llama3:8b"), mapOf("id" to "qwen3:4b")), "seen_key" to body?.path("key")?.asText())
        }
        put("/api/providers/local/endpoints/ollama", mapOf("kind" to "ollama", "url" to "http://host.docker.internal:11434", "key" to key))
        val r = post("/api/providers/local/endpoints/ollama/test").andExpect(status().isOk).json()
        assertThat(r["ok"].asBoolean()).isTrue()
        assertThat(engine.lastBody("/providers/local/test")!!["url"].asText()).isEqualTo("http://host.docker.internal:11434")
        post("/api/providers/local/endpoints/nope/test").andExpect(status().isNotFound)

        val t = post("/api/connections/local/test").andExpect(status().isOk).json()
        assertThat(t["ok"].asBoolean()).isTrue()
        assertThat(t["text"].asText()).isEqualTo("ollama: 2 models")
    }

    @Test
    fun `vertex keeps the project and region, the key stays secret, and the engine gets all three`() {
        put("/api/providers/vertex", mapOf("project" to "My Project", "region" to "us-east5")).andExpect(status().isBadRequest)
        put("/api/providers/vertex", mapOf("project" to "my-proj", "region" to "us east")).andExpect(status().isBadRequest)
        put("/api/providers/vertex", mapOf("project" to "my-proj", "region" to "us-east5", "credentials_json" to "{\"type\": \"user\"}"))
            .andExpect(status().isBadRequest)
        put("/api/providers/vertex", mapOf("project" to "my-proj", "region" to "us-east5", "credentials_json" to "not json"))
            .andExpect(status().isBadRequest)

        val adc = put("/api/providers/vertex", mapOf("project" to "my-proj", "region" to "global")).andExpect(status().isOk).json()
        assertThat(adc["credentials"].asText()).isEqualTo("adc")
        assertThat(adc["ready"].asBoolean()).isTrue()

        val v = put("/api/providers/vertex", mapOf("project" to "my-proj", "region" to "us-east5", "credentials_json" to saKey)).andExpect(status().isOk)
        assertThat(v.andReturn().response.contentAsString).doesNotContain("PRIVATE KEY")
        assertThat(v.json()["client_email"].asText()).isEqualTo("keel@my-proj.iam.gserviceaccount.com")
        assertThat(v.json()["credentials"].asText()).isEqualTo("service-account")
        assertThat(get("/api/providers/vertex").andReturn().response.contentAsString).doesNotContain("PRIVATE KEY")

        get("/api/providers/models").andExpect(status().isOk)
        val sent = mapper.readTree(engine.lastBody("/providers/models")!!["keys"]["vertex"].asText())
        assertThat(sent["project"].asText()).isEqualTo("my-proj")
        assertThat(sent["region"].asText()).isEqualTo("us-east5")
        assertThat(sent["credentials"]["private_key"].asText()).contains("PRIVATE KEY")

        // Connections' Test sends the connection as the key, and says nothing about its "shape"
        put("/api/settings/general", mapOf("default_model" to mapOf("provider" to "vertex", "mode" to "api", "model" to "gemini-2.5-pro")))
        post("/api/connections/vertex/test").andExpect(status().isOk)
        val test = engine.lastBody("/providers/test")!!
        assertThat(test["provider"].asText()).isEqualTo("vertex")
        assertThat(test["model"].asText()).isEqualTo("gemini-2.5-pro")
        assertThat(mapper.readTree(test["key"].asText())["project"].asText()).isEqualTo("my-proj")
        assertThat(get("/api/connections").json()["providers"].first { it["id"].asText() == "vertex" }["modes"][0]["ready"].asBoolean()).isTrue()

        val cleared = put("/api/providers/vertex", mapOf("project" to "my-proj", "region" to "us-east5", "clear_credentials" to true)).json()
        assertThat(cleared["credentials"].asText()).isEqualTo("adc")
    }

    @Test
    fun `a project with local models only refuses a flow on a cloud model and starts one on a local model`() {
        val (pid, _) = newProject("local-only")
        put("/api/providers/local/endpoints/ollama", mapOf("kind" to "ollama", "url" to "http://host.docker.internal:11434"))
        put("/api/projects/$pid/settings", mapOf("local_only" to true, "on_cap" to "pause",
            "default_model" to mapOf("provider" to "claude", "mode" to "api", "model" to "sonnet"))).andExpect(status().isOk)
        val refused = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Secret code"))
            .andExpect(status().isBadRequest).json()
        assertThat(refused["error"].asText()).contains("local models only").contains("claude sonnet")
        assertThat(refused["hint"].asText()).contains("Local models")

        val local = mapOf("provider" to "local", "mode" to "api", "model" to "ollama/qwen3-coder:30b")
        put("/api/projects/$pid/settings", mapOf("default_model" to local)).andExpect(status().isOk)
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Secret code")).andExpect(status().isOk)
        val keys = engine.lastBody("/threads")!!["keys"]
        assertThat(mapper.readTree(keys["local"].asText())["endpoints"][0]["name"].asText()).isEqualTo("ollama")
        assertThat(keys.has("claude")).isFalse()
    }

    @Test
    fun `local only is a setting of the Models section`() {
        val sections = get("/api/settings/sections").json()
        val models = sections.first { it["id"].asText() == "models" }
        assertThat(models["keys"].map { it["key"].asText() }).contains("local_only")
        assertThat(get("/api/settings/general").json()["local_only"].asBoolean()).isFalse()
    }
}
