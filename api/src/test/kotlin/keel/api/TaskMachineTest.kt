package keel.api

import keel.api.tasks.IllegalMove
import keel.api.tasks.Task
import keel.api.tasks.TaskMachine
import keel.api.tasks.TaskStatus
import keel.api.tasks.TaskTypes
import keel.api.tasks.Trigger
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test

/** v0.5.0: the task lifecycle, one trigger at a time (no database, no Jira). */
class TaskMachineTest {
    private fun task(status: String, key: String? = "ABC-1", pr: String? = null) = Task(
        "tk_1", "p", "Rank players", "", "story", status, if (key != null) "jira" else "local", key, null, null, null, null,
        null, null, pr, emptyList(), null, "2026-10-06T00:00:00Z", "2026-10-06T00:00:00Z",
    )

    private val pr = "https://github.com/acme/app/pull/7"

    @Test
    fun `start moves a task to In progress and moves and comments the ticket`() {
        for (from in listOf("todo", "in_progress", "blocked")) {
            val p = TaskMachine.plan(task(from), Trigger.Start("feature", "t-1", "Follow it: http://keel/#/tasks/tk_1"))
            assertThat(p.to).isEqualTo("in_progress")
            assertThat(p.moveJira).isTrue()
            assertThat(p.comment).isEqualTo("keel started the feature flow for this ticket. Follow it: http://keel/#/tasks/tk_1")
            assertThat(p.actor).isEqualTo("user")
        }
        for (from in listOf("in_review", "testing_pp", "ready_prod", "done", "cancelled")) {
            assertThatThrownBy { TaskMachine.plan(task(from), Trigger.Start("feature", "t", null)) }.isInstanceOf(IllegalMove::class.java)
        }
    }

    @Test
    fun `the PR moves it to review and asks for reviewers`() {
        val p = TaskMachine.plan(task("in_progress"), Trigger.PrOpened(pr, "keel"))
        assertThat(p.to).isEqualTo("in_review")
        assertThat(p.askReviewers).isTrue()
        assertThat(p.prUrl).isEqualTo(pr)
        assertThat(p.comment).isEqualTo("Pull request: $pr")
        assertThat(p.note).isEqualTo("Pull request opened: $pr")
        assertThat(TaskMachine.plan(task("in_progress"), Trigger.PrOpened(pr, "user")).note).isEqualTo("PR link added: $pr")
        // a new link while in review: no move, reviewers asked again only for another PR
        assertThat(TaskMachine.plan(task("in_review", pr = pr), Trigger.PrOpened(pr, "user")).let { it.to to it.askReviewers }).isEqualTo("in_review" to false)
        assertThat(TaskMachine.plan(task("in_review", pr = pr), Trigger.PrOpened("$pr-2", "user")).askReviewers).isTrue()
        assertThatThrownBy { TaskMachine.plan(task("done"), Trigger.PrOpened(pr, "user")) }.isInstanceOf(IllegalMove::class.java)
    }

    @Test
    fun `a flow that ends without a PR waits for its link, one that fails or stops blocks the task`() {
        val done = TaskMachine.plan(task("in_progress"), Trigger.FlowDone())
        assertThat(done.to).isEqualTo("in_progress")
        assertThat(done.note).contains("paste its link")
        assertThat(done.moveJira).isFalse()
        val failed = TaskMachine.plan(task("in_progress"), Trigger.FlowEnded(true, "verify_green failed"))
        assertThat(failed.to).isEqualTo("blocked")
        assertThat(failed.blockedReason).isEqualTo("verify_green failed")
        assertThat(failed.comment).isEqualTo("keel's flow failed: verify_green failed")
        assertThat(failed.moveJira).isTrue()
        assertThat(TaskMachine.plan(task("in_progress"), Trigger.FlowEnded(false, "stopped")).note).isEqualTo("The flow was stopped: stopped")
        // after the PR: an old flow's end is only history
        assertThat(TaskMachine.plan(task("in_review"), Trigger.FlowEnded(true, "x")).to).isEqualTo("in_review")
    }

    @Test
    fun `approval moves to Testing (PP) with an Inbox item, confirming PP to Ready with another, shipping to Done`() {
        val a = TaskMachine.plan(task("in_review"), Trigger.Approved(listOf("ana", "bo")))
        assertThat(a.to).isEqualTo("testing_pp")
        assertThat(a.openItem!!.stage).isEqualTo("pp")
        assertThat(a.openItem!!.title).isEqualTo("Confirm PP testing for ABC-1")
        assertThat(a.comment).contains("approved by ana, bo")
        assertThat(TaskMachine.plan(task("todo"), Trigger.Approved(listOf("ana"))).to).isEqualTo("todo")      // history only

        val pp = TaskMachine.plan(task("testing_pp"), Trigger.Confirm("pp", "all green on PP"))
        assertThat(pp.to).isEqualTo("ready_prod")
        assertThat(pp.openItem!!.title).isEqualTo("Ship ABC-1 to production")
        assertThat(pp.comment).isEqualTo("Testing in PP passed. — all green on PP")
        val prod = TaskMachine.plan(task("ready_prod"), Trigger.Confirm("prod", null))
        assertThat(prod.to).isEqualTo("done")
        assertThat(prod.openItem).isNull()
        assertThat(prod.comment).isEqualTo("Shipped to production.")
        assertThatThrownBy { TaskMachine.plan(task("in_review"), Trigger.Confirm("pp", null)) }.isInstanceOf(IllegalMove::class.java)
        assertThatThrownBy { TaskMachine.plan(task("testing_pp"), Trigger.Confirm("prod", null)) }.isInstanceOf(IllegalMove::class.java)
        assertThatThrownBy { TaskMachine.plan(task("testing_pp"), Trigger.Confirm("qa", null)) }.isInstanceOf(IllegalMove::class.java)
    }

    @Test
    fun `send back returns review, PP and ready to In progress`() {
        for (from in listOf("in_review", "testing_pp", "ready_prod")) {
            val p = TaskMachine.plan(task(from), Trigger.SendBack("the total is wrong"))
            assertThat(p.to).isEqualTo("in_progress")
            assertThat(p.comment).endsWith(": the total is wrong")
            assertThat(p.moveJira).isTrue()
        }
        assertThatThrownBy { TaskMachine.plan(task("todo"), Trigger.SendBack("x")) }.isInstanceOf(IllegalMove::class.java)
    }

    @Test
    fun `a move by hand opens the item of the new status and cancelling stops the flow`() {
        val m = TaskMachine.plan(task("in_review", pr = pr), Trigger.Move("testing_pp", null))
        assertThat(m.openItem!!.stage).isEqualTo("pp")
        assertThat(m.comment).isNull()
        val c = TaskMachine.plan(task("in_progress"), Trigger.Move("cancelled", "not needed"))
        assertThat(c.stopFlow).isTrue()
        assertThat(c.comment).isEqualTo("Moved to Cancelled: not needed")
        assertThat(TaskMachine.plan(task("in_progress", pr = pr), Trigger.Move("in_review", null)).askReviewers).isTrue()
        assertThat(TaskMachine.plan(task("todo"), Trigger.Move("blocked", null)).blockedReason).isEqualTo("blocked by hand")
        assertThatThrownBy { TaskMachine.plan(task("todo"), Trigger.Move("todo", null)) }.isInstanceOf(IllegalMove::class.java)
        assertThatThrownBy { TaskMachine.plan(task("todo"), Trigger.Move("shipped", null)) }.isInstanceOf(IllegalMove::class.java)
    }

    @Test
    fun `a change in Jira is recorded, moves the task only when it maps, and never moves Jira back`() {
        val moved = TaskMachine.plan(task("in_review"), Trigger.JiraMoved("In Review", "QA", "testing_pp"))
        assertThat(moved.to).isEqualTo("testing_pp")
        assertThat(moved.actor).isEqualTo("jira")
        assertThat(moved.moveJira).isFalse()
        assertThat(moved.comment).isNull()
        assertThat(moved.openItem!!.stage).isEqualTo("pp")
        assertThat(moved.note).isEqualTo("Jira: In Review → QA (keel: Testing (PP))")
        val kept = TaskMachine.plan(task("in_progress"), Trigger.JiraMoved("In Progress", "Code freeze", null))
        assertThat(kept.to).isEqualTo("in_progress")
        assertThat(kept.note).isEqualTo("Jira: In Progress → Code freeze")
    }

    @Test
    fun `Jira status names come from the mapping, else the usual names`() {
        val map = mapOf("in_review" to "Code Review", "testing_pp" to "-", "blocked" to "On Hold")
        assertThat(TaskMachine.jiraTarget("in_review", map)).isEqualTo("Code Review")
        assertThat(TaskMachine.jiraTarget("testing_pp", map)).isNull()                 // "do not move"
        assertThat(TaskMachine.jiraTarget("in_progress", map)).isEqualTo("In Progress")
        assertThat(TaskMachine.jiraTarget("blocked", map)).isEqualTo("On Hold")
        assertThat(TaskMachine.jiraTarget("blocked", emptyMap())).isNull()           // most workflows have no Blocked
        assertThat(TaskMachine.jiraTarget("cancelled", emptyMap())).isNull()

        assertThat(TaskMachine.keelStatus("Code Review", "indeterminate", map, "in_progress")).isEqualTo("in_review")
        assertThat(TaskMachine.keelStatus("in progress", "indeterminate", emptyMap(), "todo")).isEqualTo("in_progress")
        assertThat(TaskMachine.keelStatus("QA", "indeterminate", mapOf("testing_pp" to "QA", "ready_prod" to "QA"), "ready_prod")).isEqualTo("ready_prod")
        assertThat(TaskMachine.keelStatus("Selected", "new", emptyMap(), "in_progress")).isNull()     // unmapped: the task stays
        assertThat(TaskMachine.keelStatus("Selected", "new", emptyMap(), null)).isEqualTo("todo")      // a new task by category
        assertThat(TaskMachine.keelStatus("Closed", "done", emptyMap(), "in_review")).isEqualTo("done")
        assertThat(TaskMachine.keelStatus("Won't Do", "done", emptyMap(), "todo")).isEqualTo("cancelled")
    }

    @Test
    fun `a first mapping is suggested from the discovered statuses, and types pick the default flow`() {
        val s = TaskMachine.suggest(listOf("Backlog" to "new", "In Progress" to "indeterminate", "Code Review" to "indeterminate",
            "QA on PP" to "indeterminate", "Ready for Release" to "indeterminate", "Done" to "done", "Won't Do" to "done"))
        assertThat(s).containsEntry("todo", "Backlog").containsEntry("in_progress", "In Progress").containsEntry("in_review", "Code Review")
            .containsEntry("testing_pp", "QA on PP").containsEntry("ready_prod", "Ready for Release").containsEntry("done", "Done")
            .containsEntry("cancelled", "Won't Do").doesNotContainKey("blocked")
        assertThat(TaskTypes.defaultWorkflow("bug")).isEqualTo("fix")
        assertThat(TaskTypes.defaultWorkflow("story")).isEqualTo("feature")
        assertThat(TaskTypes.defaultWorkflow("task")).isEqualTo("change")
        assertThat(TaskTypes.fromJira("Bug")).isEqualTo("bug")
        assertThat(TaskTypes.fromJira("Sub-task")).isEqualTo("task")
        assertThat(TaskStatus.ALL).hasSize(8)
    }
}
