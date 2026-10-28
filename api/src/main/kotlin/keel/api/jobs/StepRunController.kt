package keel.api.jobs

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/projects/{pid}/flows/{tid}")
class StepRunController(private val runs: StepRunService) {

    /** Each step's calls added up: tries, working time, tokens, cost, the model and effort that ran, the result line. */
    @GetMapping("/step-runs")
    fun stepRuns(@PathVariable pid: String, @PathVariable tid: String): List<StepRun> = runs.of(pid, tid)

    /** 0.18.64 what a step's last run changed against the one before (its document, else its agent's answer); no step:
     *  the steps that ran more than once. */
    @GetMapping("/step-diff")
    fun stepDiff(@PathVariable pid: String, @PathVariable tid: String, @RequestParam(required = false) step: String?,
                 @RequestParam(required = false) ac: String?): StepDiff = runs.diff(pid, tid, step, ac)
}
