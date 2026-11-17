package keel.api.tasks

import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

/** v0.5.0: tasks (local or from Jira) and their lifecycle; docs/CONTRACT.md "v0.5.0: tasks and Jira". */
@RestController
@RequestMapping("/api")
class TaskController(private val tasks: TaskService, private val automations: TaskAutomations, private val store: TaskStore) {

    @GetMapping("/projects/{pid}/tasks")
    fun list(@PathVariable pid: String, @RequestParam(required = false) source: String?): TaskList = tasks.list(pid, source)

    @PostMapping("/projects/{pid}/tasks")
    fun create(@PathVariable pid: String, @RequestBody body: NewTask): TaskView = tasks.create(pid, body)

    /** Jira → tasks now (when connected: the Jira plugin), and the PR reviews of the tasks in review. */
    @PostMapping("/projects/{pid}/tasks/sync")
    fun syncNow(@PathVariable pid: String): SyncResult {
        val r = tasks.syncNow(pid)
        return r.copy(commentsChecked = automations.watchReviews(pid))
    }

    @GetMapping("/tasks/{id}")
    fun get(@PathVariable id: String): TaskView = tasks.get(id)

    @PutMapping("/tasks/{id}")
    fun update(@PathVariable id: String, @RequestBody body: TaskPatch): TaskView = tasks.update(id, body)

    @DeleteMapping("/tasks/{id}")
    fun delete(@PathVariable id: String): Map<String, Boolean> {
        tasks.delete(id)
        return mapOf("ok" to true)
    }

    @PostMapping("/tasks/{id}/start")
    fun start(@PathVariable id: String, @RequestBody(required = false) body: StartTask?): TaskView = tasks.start(id, body ?: StartTask())

    /** 0.34 Hand to Org: the task goes to an org (the Org plugin) as a mission; its status follows the mission. */
    @PostMapping("/tasks/{id}/org")
    fun handToOrg(@PathVariable id: String, @RequestBody body: HandToOrg): TaskView = tasks.handToOrg(id, body)

    @PostMapping("/tasks/{id}/confirm")
    fun confirm(@PathVariable id: String, @RequestBody body: ConfirmTask): TaskView = tasks.confirm(id, body)

    @PostMapping("/tasks/{id}/status")
    fun status(@PathVariable id: String, @RequestBody body: MoveTask): TaskView = tasks.move(id, body)

    @PostMapping("/tasks/{id}/pr")
    fun pr(@PathVariable id: String, @RequestBody body: PrBody): TaskView = tasks.setPr(id, body.url)

    /**
     * An Inbox task item's button: confirm | send_back (kind task), done (kind jira-manual); 0.18.52 start | other | later
     * (kind task-proposal), reply | address | handle (kind task-review).
     */
    @PostMapping("/inbox/tasks/{itemId}/act")
    fun act(@PathVariable itemId: Long, @RequestBody body: ItemAct): TaskView =
        if (store.item(itemId)?.kind in TaskAutomations.KINDS) automations.act(itemId, body) else tasks.act(itemId, body)

    // ---- 0.18.52 task automations (docs/plans/task-automations.md) ----

    @GetMapping("/projects/{pid}/tasks/automations")
    fun automations(@PathVariable pid: String): AutomationView = automations.view(pid)

    @PutMapping("/projects/{pid}/tasks/automations")
    fun saveAutomations(@PathVariable pid: String, @RequestBody body: AutomationSave): AutomationView = automations.save(pid, body)

    /** "Pick a flow for me": keel picks the best flow and asks in the Inbox (it never starts it from here). */
    @PostMapping("/tasks/{id}/pick")
    fun pick(@PathVariable id: String, @RequestBody(required = false) body: PickBody?): TaskView = automations.pickFor(id)

    /** "Review it": keel proposes a review of the task's pull request. */
    @PostMapping("/tasks/{id}/review")
    fun review(@PathVariable id: String, @RequestBody(required = false) body: PickBody?): TaskView = automations.reviewFor(id)
}
