package keel.api.flow

/**
 * Where keel hands work over as a task: KeelBot's "Make a task" for a side session
 * (POST /api/projects/{pid}/helper/sessions/{sid}/task). The Tasks plugin (plugins/tasks) gives the one bean; core
 * knows no task. Without the plugin there is no bean, and that endpoint answers 409 with a clear message.
 *
 * Part of keel's api SDK (keel-api-sdk): a plugin may implement it.
 */
interface TaskSink {
    /**
     * Makes a task in project [pid] and returns it as the api shows it (the Tasks plugin's TaskView): the endpoint
     * sends this object back as its JSON body. [type] is the task's type ("task", "bug", "story"); a bad title or type
     * is the sink's own 400.
     */
    fun create(pid: String, title: String, description: String, type: String): Any
}
