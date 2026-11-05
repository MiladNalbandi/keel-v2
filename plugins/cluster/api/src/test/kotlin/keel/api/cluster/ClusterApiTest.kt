package keel.api.cluster

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import keel.api.common.KvStore
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
    }

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
}
