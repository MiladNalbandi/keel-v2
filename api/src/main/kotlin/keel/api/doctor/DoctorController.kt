package keel.api.doctor

import keel.api.events.EventHub
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api")
class DoctorController(private val doctor: WorkspaceDoctor, private val hub: EventHub) {

    /** Looks at the uncommitted files and proposes commit / stash / ignore / keep per group. Changes nothing. */
    @PostMapping("/projects/{pid}/doctor/workspace")
    fun diagnose(@PathVariable pid: String): Diagnosis = doctor.diagnose(pid)

    /** Runs the plan the user approved (plain git; nothing is deleted). */
    @PostMapping("/projects/{pid}/doctor/workspace/apply")
    fun apply(@PathVariable pid: String, @RequestBody body: ApplyBody): Applied =
        doctor.apply(pid, body).also { hub.publish(pid, "project.changed", mapOf("id" to pid)) }
}
