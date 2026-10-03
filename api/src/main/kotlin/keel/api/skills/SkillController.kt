package keel.api.skills

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

data class NewSkill(val name: String = "", val kind: String = "knowledge", val stack: String = "any", val body: String = "")
data class SkillPatch(val agents: List<String>? = null, val `when`: String? = null, val body: String? = null, val enabled: Boolean? = null)

@RestController
@RequestMapping("/api")
class SkillController(private val skills: SkillService) {

    @GetMapping("/projects/{pid}/skills")
    fun list(@PathVariable pid: String): List<Skill> = skills.list(pid)

    @GetMapping("/skills/{sid}")
    fun get(@PathVariable sid: String, @RequestParam(required = false) project: String?): SkillDetail = skills.detail(sid, project)

    @PostMapping("/projects/{pid}/skills")
    fun create(@PathVariable pid: String, @RequestBody body: NewSkill): Skill =
        skills.create(pid, body.name, body.kind, body.stack, body.body)

    @PutMapping("/projects/{pid}/skills/{sid}")
    fun update(@PathVariable pid: String, @PathVariable sid: String, @RequestBody body: SkillPatch): Skill =
        skills.update(pid, sid, body.agents, body.`when`, body.body, body.enabled)
}
