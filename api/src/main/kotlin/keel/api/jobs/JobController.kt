package keel.api.jobs

import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/jobs")
class JobController(private val jobs: JobService, private val engine: EngineClient) {

    @GetMapping
    fun list(
        @RequestParam(required = false) project: String?,
        @RequestParam(required = false) status: String?,
        @RequestParam(required = false) agent: String?,
        @RequestParam(required = false) provider: String?,
        @RequestParam(required = false) q: String?,
        @RequestParam(defaultValue = "50") limit: Int,
    ): List<Job> = jobs.list(project, status, agent, provider, limit, q)

    /** v0.15.2 how many calls match the same filters as the list, without its limit: `{"count": 312}`. */
    @GetMapping("/count")
    fun count(
        @RequestParam(required = false) project: String?,
        @RequestParam(required = false) status: String?,
        @RequestParam(required = false) agent: String?,
        @RequestParam(required = false) provider: String?,
        @RequestParam(required = false) q: String?,
    ): Map<String, Int> = mapOf("count" to jobs.count(project, status, agent, provider, q))

    @GetMapping("/{id}")
    fun get(@PathVariable id: String): JobDetail = JobDetail(jobs.get(id), jobs.steps(id, 0))

    /** 0.18.64 the exact prompt the call got: `{id, agent, step, ac, at, system, prompt, system_cut, prompt_cut}`. */
    @GetMapping("/{id}/prompt")
    fun prompt(@PathVariable id: String): JobPrompt = jobs.prompt(id)

    @GetMapping("/{id}/steps")
    fun steps(@PathVariable id: String, @RequestParam(defaultValue = "0") after: Int): JobSteps {
        val job = jobs.get(id)
        return JobSteps(jobs.steps(id, after), job.status == "running")
    }

    /**
     * The engine stops whole threads, not single agent calls, so this stops the job's thread
     * and marks the job stopped.
     */
    @PostMapping("/{id}/stop")
    fun stop(@PathVariable id: String): Job {
        val job = jobs.get(id)
        if (job.status == "running" && job.threadId != null) {
            try {
                engine.stop(job.threadId)
            } catch (e: EngineDown) {
                // Engine gone: the job cannot be running anymore. Mark it stopped below.
            }
        }
        jobs.markStopped(id)
        return jobs.get(id)
    }
}
