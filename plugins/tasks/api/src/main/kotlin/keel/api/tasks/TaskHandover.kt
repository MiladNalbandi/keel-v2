package keel.api.tasks

import keel.api.flow.TaskSink
import org.springframework.stereotype.Component

/** Core's [TaskSink]: KeelBot's "Make a task" for a side session becomes a task here (a [TaskView] as its answer). */
@Component
class TaskHandover(private val tasks: TaskService) : TaskSink {
    override fun create(pid: String, title: String, description: String, type: String): TaskView =
        tasks.create(pid, NewTask(title = title, description = description, type = type))
}
