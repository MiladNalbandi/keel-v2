package keel.api.cluster

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import keel.api.common.KvStore
import keel.api.connections.ModelServerService
import keel.api.connections.SecretService
import keel.api.pluginhost.PluginsReloaded
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.context.ApplicationEventPublisher
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** The Cluster plugin's rules per context: on and read only by default, checked before every call, and sent to agents only for a
 *  project with the plugin on. kubectl itself is the engine part's (keel_plugin_cluster); here the stub engine answers. */
class ClusterApiTest : ApiTest() {
    @Autowired lateinit var kv: KvStore
    @Autowired lateinit var keys: ClusterKeys
    @Autowired lateinit var events: ApplicationEventPublisher
    @Autowired lateinit var secrets: SecretService

    private val ops = listOf("test", "snapshot", "logs", "describe", "exec", "change", "runs", "history")

    @BeforeEach
    fun stub() {
        kv.delete(ClusterService.KEY)
        engine.extraRoutes["/plugins/cluster/contexts"] = { _ ->
            200 to mapOf("kubectl" to true, "kubectl_version" to "v1.32.3", "kubeconfig" to "/home/keel/.kube/config", "in_docker" to false,
                "contexts" to listOf(
                    mapOf("name" to "kind-dev", "cluster" to "kind-dev", "server" to "https://127.0.0.1:42133", "namespace" to "default",
                        "current" to true, "local" to true),
                    mapOf("name" to "prod-eu", "cluster" to "prod-eu", "server" to "https://k8s.prod.example.net", "namespace" to "default",
                        "current" to false, "local" to false)))
        }
        ops.forEach { op -> engine.extraRoutes["/plugins/cluster/$op"] = { body -> 200 to mapOf("op" to op, "got" to body) } }
    }

    @AfterEach
    fun clean() {
        engine.extraRoutes.remove("/plugins/cluster/contexts")
        ops.forEach { engine.extraRoutes.remove("/plugins/cluster/$it") }
        engine.extraRoutes.remove("/plugins")
        events.publishEvent(PluginsReloaded(emptyList()))
        kv.delete(ClusterService.KEY)
        listOf(ClusterService.GOOGLE_SECRET, ModelServerService.VERTEX_SECRET).forEach { if (secrets.has(it)) secrets.delete(it) }
    }

    private fun saKey(email: String) = jacksonObjectMapper().writeValueAsString(mapOf("type" to "service_account", "project_id" to "demo-project",
        "private_key" to "-----BEGIN PRIVATE KEY-----\nTEST\n-----END PRIVATE KEY-----\n", "client_email" to email))

    private fun contexts(): Map<String, JsonNode> =
        get("/api/plugins/cluster/contexts").andExpect(status().isOk).json()["contexts"].associateBy { it["name"].asText() }

    private fun rules(vararg r: Pair<String, Map<String, Any?>>) =
        put("/api/plugins/cluster/settings", mapOf("clusters" to r.toMap()))

    @Test
    fun `every context is on and read only until the person says more`() {
        val c = contexts()
        assertThat(c.keys).containsExactly("kind-dev", "prod-eu")
        assertThat(c.values.map { it["on"].asBoolean() }).containsOnly(true)
        assertThat(c.values.map { it["access"].asText() }).containsOnly("read")
        assertThat(c["kind-dev"]!!["local"].asBoolean()).isTrue()
        val whole = get("/api/plugins/cluster/contexts").json()
        assertThat(whole["kubectl"].asBoolean()).isTrue()
        assertThat(whole["kubectl_version"].asText()).isEqualTo("v1.32.3")
    }

    @Test
    fun `settings take read, ask and free, and free only for a cluster on this computer`() {
        rules("kind-dev" to mapOf("access" to "everything")).andExpect(status().isBadRequest)
        put("/api/plugins/cluster/settings", mapOf<String, Any>()).andExpect(status().isBadRequest)
        rules("" to mapOf("on" to false)).andExpect(status().isBadRequest)
        assertThat(contexts()["kind-dev"]!!["access"].asText()).isEqualTo("read")       // nothing was kept

        val saved = rules("kind-dev" to mapOf("access" to "free"), "prod-eu" to mapOf("access" to "free")).andExpect(status().isOk).json()
        val byName = saved["contexts"].associateBy { it["name"].asText() }
        assertThat(byName["kind-dev"]!!["access"].asText()).isEqualTo("free")
        assertThat(byName["prod-eu"]!!["access"].asText()).isEqualTo("ask")              // not on this computer: ask instead

        rules("prod-eu" to mapOf("on" to false)).andExpect(status().isOk)
        val after = contexts()
        assertThat(after["prod-eu"]!!["on"].asBoolean()).isFalse()
        assertThat(after["prod-eu"]!!["access"].asText()).isEqualTo("ask")              // only the fields given change
        assertThat(after["kind-dev"]!!["access"].asText()).isEqualTo("free")
    }

    @Test
    fun `a context that is off is refused, except for a test`() {
        rules("prod-eu" to mapOf("on" to false)).andExpect(status().isOk)
        val from = engine.calls.size
        val off = get("/api/plugins/cluster/snapshot?context=prod-eu").andExpect(status().isForbidden).json()
        assertThat(off["error"].asText()).contains("prod-eu is turned off in Cluster › Connect")
        get("/api/plugins/cluster/logs?context=prod-eu&ns=shop&name=web-1").andExpect(status().isForbidden)
        get("/api/plugins/cluster/describe?context=prod-eu&kind=pod&ns=shop&name=web-1").andExpect(status().isForbidden)
        post("/api/plugins/cluster/exec", mapOf("context" to "prod-eu", "ns" to "shop", "name" to "web-1", "command" to "ls")).andExpect(status().isForbidden)
        post("/api/plugins/cluster/change", mapOf("context" to "prod-eu", "action" to "delete", "kind" to "pod", "ns" to "shop", "name" to "web-1"))
            .andExpect(status().isForbidden)
        assertThat(engine.calls.drop(from).none { it.path in setOf("/plugins/cluster/snapshot", "/plugins/cluster/exec", "/plugins/cluster/change") })
            .isTrue()
        post("/api/plugins/cluster/test", mapOf("context" to "prod-eu")).andExpect(status().isOk)
        get("/api/plugins/cluster/snapshot").andExpect(status().isBadRequest)            // no context at all
    }

    @Test
    fun `reading needs only on, an exec or a change needs ask or free`() {
        val from = engine.calls.size
        val snap = get("/api/plugins/cluster/snapshot?context=kind-dev&ns=shop").andExpect(status().isOk).json()
        assertThat(snap["got"]["context"].asText()).isEqualTo("kind-dev")
        assertThat(snap["got"]["ns"].asText()).isEqualTo("shop")
        val logs = get("/api/plugins/cluster/logs?context=kind-dev&ns=shop&name=worker-1&previous=true&tail=50").andExpect(status().isOk).json()
        assertThat(logs["got"]["previous"].asBoolean()).isTrue()
        assertThat(logs["got"]["tail"].asInt()).isEqualTo(50)
        assertThat(get("/api/plugins/cluster/logs?context=kind-dev&ns=shop&name=worker-1").json()["got"]["tail"].asInt()).isEqualTo(300)

        val ro = post("/api/plugins/cluster/exec", mapOf("context" to "kind-dev", "ns" to "shop", "name" to "web-1", "command" to "ls"))
            .andExpect(status().isForbidden).json()
        assertThat(ro["error"].asText()).isEqualTo("kind-dev is read only in keel")
        assertThat(ro["hint"].asText()).isEqualTo("Change it in Cluster › Connect.")
        post("/api/plugins/cluster/change", mapOf("context" to "kind-dev", "action" to "restart", "kind" to "deployment", "ns" to "shop", "name" to "web"))
            .andExpect(status().isForbidden)
        assertThat(engine.calls.drop(from).none { it.path == "/plugins/cluster/exec" || it.path == "/plugins/cluster/change" }).isTrue()

        rules("kind-dev" to mapOf("access" to "ask")).andExpect(status().isOk)
        post("/api/plugins/cluster/exec", mapOf("context" to "kind-dev", "ns" to "shop", "name" to "web-1", "command" to " "))
            .andExpect(status().isBadRequest)
        post("/api/plugins/cluster/exec", mapOf("context" to "kind-dev", "ns" to "shop", "name" to "web-1", "container" to "web", "command" to "ls /"))
            .andExpect(status().isOk)
        val exec = engine.lastBody("/plugins/cluster/exec")!!
        assertThat(exec["access"].asText()).isEqualTo("ask")
        assertThat(exec["who"].asText()).isEqualTo("person")
        assertThat(exec["command"].asText()).isEqualTo("ls /")
        assertThat(exec["container"].asText()).isEqualTo("web")

        rules("kind-dev" to mapOf("access" to "free")).andExpect(status().isOk)
        post("/api/plugins/cluster/change", mapOf("context" to "kind-dev", "action" to "scale", "kind" to "deployment", "ns" to "shop",
            "name" to "web", "replicas" to 3)).andExpect(status().isOk)
        val change = engine.lastBody("/plugins/cluster/change")!!
        assertThat(change["access"].asText()).isEqualTo("free")
        assertThat(change["who"].asText()).isEqualTo("person")
        assertThat(change["action"].asText()).isEqualTo("scale")
        assertThat(change["replicas"].asInt()).isEqualTo(3)
    }

    @Test
    fun `the engine's refusal comes through as it is`() {
        engine.extraRoutes["/plugins/cluster/snapshot"] = { _ ->
            409 to mapOf("error" to "kind-dev was not reached: connection refused", "hint" to "Is the cluster running?")
        }
        val res = get("/api/plugins/cluster/snapshot?context=kind-dev").andExpect(status().isConflict).json()
        assertThat(res["error"].asText()).isEqualTo("kind-dev was not reached: connection refused")
        assertThat(res["hint"].asText()).isEqualTo("Is the cluster running?")
    }

    @Test
    fun `runs gets the project's folder and the contexts that are on, history goes to the engine`() {
        val (pid, root) = newProject("cluster-runs")
        rules("prod-eu" to mapOf("on" to false)).andExpect(status().isOk)
        get("/api/plugins/cluster/runs").andExpect(status().isBadRequest)
        get("/api/plugins/cluster/runs?pid=$pid").andExpect(status().isOk)
        val sent = engine.lastBody("/plugins/cluster/runs")!!
        assertThat(sent["root"].asText()).isEqualTo(root.toString())
        assertThat(sent["contexts"].map { it.asText() }).containsExactly("kind-dev")
        assertThat(get("/api/plugins/cluster/history").andExpect(status().isOk).json()["op"].asText()).isEqualTo("history")
    }

    @Test
    fun `agents get the rules only for a project with the plugin on`() {
        val (pid, _) = newProject("cluster-keys")
        rules("kind-dev" to mapOf("access" to "ask"), "prod-eu" to mapOf("on" to false)).andExpect(status().isOk)
        assertThat(keys.keys(pid)).isEmpty()                 // the plugin is not in the stub's catalog: off
        engine.extraRoutes["/plugins"] = { _ -> 200 to listOf(mapOf("name" to "cluster", "title" to "Cluster", "installable" to true,
            "per_project" to true, "actions" to emptyList<Any>())) }
        events.publishEvent(PluginsReloaded(listOf("cluster")))
        assertThat(keys.keys(pid)).isEmpty()                 // known now, but still off for this project
        put("/api/projects/$pid/plugins/cluster", mapOf("enabled" to true)).andExpect(status().isOk)
        val access = jacksonObjectMapper().readTree(keys.keys(pid).getValue("cluster:access"))
        assertThat(access.fieldNames().asSequence().toList()).containsExactly("kind-dev")   // prod-eu is off: not there at all
        assertThat(access["kind-dev"].asText()).isEqualTo("ask")
    }

    @Test
    fun `GKE signs in with the Vertex AI key by default, and the key only goes to the engine`() {
        val from = engine.calls.size
        assertThat(get("/api/plugins/cluster/google").json()["source"].asText()).isEqualTo("vertex")
        get("/api/plugins/cluster/snapshot?context=kind-dev").andExpect(status().isOk)
        assertThat(engine.lastBody("/plugins/cluster/snapshot")!!.path("google").let { it.isNull || it.isMissingNode }).isTrue()       // no Vertex key saved: none

        secrets.put(ModelServerService.VERTEX_SECRET, saKey("vertex@demo-project.iam.gserviceaccount.com"))
        val view = get("/api/plugins/cluster/contexts").andExpect(status().isOk).json()["google"]
        assertThat(view["email"].asText()).isEqualTo("vertex@demo-project.iam.gserviceaccount.com")
        assertThat(view["vertex_email"].asText()).isEqualTo("vertex@demo-project.iam.gserviceaccount.com")
        assertThat(view.toString()).doesNotContain("PRIVATE KEY")
        listOf("test" to { post("/api/plugins/cluster/test", mapOf("context" to "prod-eu")) },
            "snapshot" to { get("/api/plugins/cluster/snapshot?context=kind-dev") },
            "logs" to { get("/api/plugins/cluster/logs?context=kind-dev&ns=shop&name=web-1") },
            "describe" to { get("/api/plugins/cluster/describe?context=kind-dev&kind=pod&ns=shop&name=web-1") }).forEach { (op, call) ->
            call().andExpect(status().isOk)
            val sent = jacksonObjectMapper().readTree(engine.lastBody("/plugins/cluster/$op")!!["google"].asText())
            assertThat(sent["client_email"].asText()).isEqualTo("vertex@demo-project.iam.gserviceaccount.com")
        }
        assertThat(engine.calls.drop(from).filter { it.path == "/plugins/cluster/contexts" || it.path == "/plugins/cluster/history" }
            .none { it.body.toString().contains("PRIVATE KEY") }).isTrue()
    }

    @Test
    fun `a key for clusters only, checked, kept as a secret, and off sends none`() {
        put("/api/plugins/cluster/google", mapOf("source" to "everything")).andExpect(status().isBadRequest)
        put("/api/plugins/cluster/google", mapOf("source" to "own")).andExpect(status().isBadRequest)          // no key yet
        val bad = put("/api/plugins/cluster/google", mapOf("source" to "own", "credentials_json" to "{\"type\": \"authorized_user\"}"))
            .andExpect(status().isBadRequest).json()
        assertThat(bad["error"].asText()).isEqualTo("This JSON is not a service-account key")
        assertThat(get("/api/plugins/cluster/google").json()["source"].asText()).isEqualTo("vertex")        // nothing was kept

        secrets.put(ModelServerService.VERTEX_SECRET, saKey("vertex@demo-project.iam.gserviceaccount.com"))
        val own = put("/api/plugins/cluster/google", mapOf("source" to "own", "credentials_json" to saKey("gke@demo-project.iam.gserviceaccount.com")))
            .andExpect(status().isOk).json()
        assertThat(own["source"].asText()).isEqualTo("own")
        assertThat(own["email"].asText()).isEqualTo("gke@demo-project.iam.gserviceaccount.com")
        assertThat(own["own_key_set"].asBoolean()).isTrue()
        assertThat(own.toString()).doesNotContain("PRIVATE KEY")
        get("/api/plugins/cluster/snapshot?context=kind-dev").andExpect(status().isOk)
        assertThat(engine.lastBody("/plugins/cluster/snapshot")!!["google"].asText()).contains("gke@demo-project.iam.gserviceaccount.com")

        rules("kind-dev" to mapOf("access" to "ask")).andExpect(status().isOk)
        assertThat(get("/api/plugins/cluster/google").json()["source"].asText()).isEqualTo("own")          // rules keep the sign-in

        val off = put("/api/plugins/cluster/google", mapOf("source" to "off")).andExpect(status().isOk).json()
        assertThat(off.path("email").let { it.isNull || it.isMissingNode }).isTrue()
        assertThat(off["own_key_set"].asBoolean()).isTrue()                                                   // kept for later
        get("/api/plugins/cluster/snapshot?context=kind-dev").andExpect(status().isOk)
        assertThat(engine.lastBody("/plugins/cluster/snapshot")!!.path("google").let { it.isNull || it.isMissingNode }).isTrue()
        put("/api/plugins/cluster/google", mapOf("source" to "off", "clear_credentials" to true)).andExpect(status().isOk)
        assertThat(secrets.has(ClusterService.GOOGLE_SECRET)).isFalse()
    }

    @Test
    fun `agents get the Google key with the rules, and none when GKE sign-in is off`() {
        val (pid, _) = newProject("cluster-google")
        engine.extraRoutes["/plugins"] = { _ -> 200 to listOf(mapOf("name" to "cluster", "title" to "Cluster", "installable" to true,
            "per_project" to true, "actions" to emptyList<Any>())) }
        events.publishEvent(PluginsReloaded(listOf("cluster")))
        put("/api/projects/$pid/plugins/cluster", mapOf("enabled" to true)).andExpect(status().isOk)
        assertThat(keys.keys(pid)).doesNotContainKey("cluster:google")
        secrets.put(ModelServerService.VERTEX_SECRET, saKey("vertex@demo-project.iam.gserviceaccount.com"))
        assertThat(keys.keys(pid).getValue("cluster:google")).contains("vertex@demo-project.iam.gserviceaccount.com")
        put("/api/plugins/cluster/google", mapOf("source" to "off")).andExpect(status().isOk)
        assertThat(keys.keys(pid)).doesNotContainKey("cluster:google")
    }
}
