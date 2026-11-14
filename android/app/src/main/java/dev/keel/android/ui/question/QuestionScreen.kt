package dev.keel.android.ui.question

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.keel.android.data.crashDetail
import dev.keel.android.data.isFailedStep
import dev.keel.android.data.Ac
import dev.keel.android.data.ClarifyQuestion
import dev.keel.android.data.GateForm
import dev.keel.android.data.stepOf
import dev.keel.android.ui.common.ChoiceRow
import dev.keel.android.ui.common.DetailBar
import dev.keel.android.ui.common.EmptyState
import dev.keel.android.ui.common.ErrorState
import dev.keel.android.ui.common.InlineError
import dev.keel.android.ui.common.KTextField
import dev.keel.android.ui.common.Loading
import dev.keel.android.ui.common.PrimaryButton
import dev.keel.android.ui.common.StepBars
import dev.keel.android.ui.common.Tag
import dev.keel.android.ui.pair.Banner
import dev.keel.android.ui.theme.K
import dev.keel.android.ui.theme.Mono

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun QuestionScreen(vm: FlowViewModel, onBack: () -> Unit, onFlow: () -> Unit) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    val t = ui.thread
    val w = t?.waiting
    val title = when {
        w == null -> "Question"
        t.phase.startsWith("spec") && w.kind == "gate" -> "Spec gate"
        w.kind == "clarify" -> "A question"
        isFailedStep(w.kind, w.title, w.crash) -> "Step failed"
        w.kind == "fix" -> "Stuck"
        w.kind == "usage" || w.kind == "budget" -> "Budget"
        else -> w.name ?: "Gate"
    }
    Column(Modifier.fillMaxSize()) {
        DetailBar(title, t?.let { listOfNotNull(it.title.takeIf { s -> s.isNotBlank() }, "${it.workflowId} flow").joinToString(" · ") }, onBack, "Back to the inbox") {
            TextButton(onClick = onFlow, modifier = Modifier.padding(end = 4.dp)) { Text("Flow", color = K.c.accentInk, fontSize = 14.sp, fontWeight = FontWeight.Medium) }
        }
        PullToRefreshBox(isRefreshing = ui.refreshing, onRefresh = vm::refresh, modifier = Modifier.weight(1f)) {
            when {
                ui.loading -> Loading()
                ui.error != null -> ErrorState(ui.error!!, { vm.load() })
                t == null -> EmptyState("This flow is gone", "keel does not know it any more.")
                else -> Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    StepBars(stepOf(t.phase, t.status), K.c.accent, K.c.accentInk, big = true)
                    ui.sent?.let {
                        Banner(it, null, K.c.okSoft, K.c.okInk)
                        dev.keel.android.ui.common.PillButton("Follow the flow", onFlow, Modifier.fillMaxWidth())
                    }
                    if (w == null || ui.form == null) {
                        if (ui.sent == null) EmptyState("Nothing to answer here now", "The flow is ${t.status}. Open the flow to see what it does.")
                    } else {
                        w.crash?.let { c -> Banner(c.title, c.fix, K.c.badSoft, K.c.badInk) }
                        QuestionContext(w.title, crashDetail(w.detail, w.crash), t.acs, showAcs = w.kind == "gate" && t.phase.startsWith("spec"))
                        GateFormView(ui.form!!, vm)
                    }
                    InlineError(ui.sendError, vm::clearError)
                }
            }
        }
        if (ui.form != null) Footer(ui.form!!.sendLabel, ui.sending, vm::send)
    }
}

/** The card above the answer: the criteria of a spec gate, or the question's text. */
@Composable
private fun QuestionContext(title: String, detail: String, acs: List<Ac>, showAcs: Boolean) {
    var whole by remember { mutableStateOf(false) }
    val shape = RoundedCornerShape(16.dp)
    Column(Modifier.fillMaxWidth().background(K.c.panel, shape).border(1.dp, K.c.border, shape).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        val head = if (showAcs) detail.lineSequence().firstOrNull { it.isNotBlank() } ?: title else title
        Text(head, fontSize = 18.sp, fontWeight = FontWeight.SemiBold, lineHeight = 23.sp, color = K.c.fg, modifier = Modifier.semantics { heading() })
        if (showAcs && acs.isNotEmpty()) {
            acs.forEach { ac ->
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.Top) {
                    val (soft, ink) = layerColors(ac.layer)
                    Tag("${ac.id} ${ac.layer}".trim(), soft, ink, mono = true)
                    Text(ac.title, fontSize = 14.sp, lineHeight = 20.sp, color = K.c.body)
                }
            }
        }
        val rest = if (showAcs) detail.lines().drop(1).joinToString("\n").trim() else detail.trim()
        if (rest.isNotEmpty()) {
            HorizontalDivider(color = K.c.border)
            Text(rest, fontSize = 14.sp, lineHeight = 20.sp, color = K.c.text2, maxLines = if (whole) Int.MAX_VALUE else 6)
            if (rest.lines().size > 6 || rest.length > 400) TextButton(onClick = { whole = !whole }) {
                Text(if (whole) "Show less" else "Read the whole text", color = K.c.accentInk)
            }
        }
    }
}

@Composable
fun layerColors(layer: String): Pair<Color, Color> = when (layer.lowercase()) {
    "api" -> K.c.runSoft to K.c.runInk
    "db" -> K.c.okSoft to K.c.okInk
    "ui", "web" -> K.c.warnSoft to K.c.warnInk
    else -> K.c.accentSoft to K.c.accentInk
}

/** The answer itself: the named choices (radio rows), the reason box, the questions of a clarify step. */
@Composable
fun GateFormView(form: GateForm, vm: FlowViewModel, idleBg: Color = K.c.panel, legend: String? = null) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        val head = legend ?: if (form.questions.isEmpty() || form.rows.isNotEmpty()) "◆ ${form.waiting.title}" else null
        if (head != null && form.rows.isNotEmpty()) Text(head, fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg, modifier = Modifier.padding(bottom = 2.dp).semantics { heading() })
        form.rows.forEach { r ->
            ChoiceRow(selected = form.picked == r.name, label = r.label, description = r.description.ifBlank { null }, onClick = { vm.pick(r.name) }, idleBg = idleBg)
        }
        form.reasonLabel?.let { label ->
            KTextField(form.why, vm::setWhy, label, placeholder = if (form.plain && form.waiting.kind == "fix") "e.g. The key is wrong; I will fix it later." else if (form.plain) "e.g. AC-3: log to the audit table, not the app log" else "e.g. returns are rows with status=returned, count them once")
        }
        form.questions.forEach { q -> QuestionBlock(q, form, vm, idleBg) }
    }
}

@Composable
private fun QuestionBlock(q: ClarifyQuestion, form: GateForm, vm: FlowViewModel, idleBg: Color) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.padding(top = 4.dp)) {
        Text("◆ ${q.question}", fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg, modifier = Modifier.semantics { heading() })
        if (!q.why.isNullOrBlank()) Text(q.why, fontSize = 13.sp, color = K.c.dim)
        if (q.multiple) Text("Pick any", fontSize = 12.sp, color = K.c.warnInk, fontFamily = Mono)
        q.options.forEach { o ->
            ChoiceRow(
                selected = form.isPicked(q, o.label),
                label = o.label + if (o.recommended) "  · recommended" else "",
                description = o.description,
                onClick = { vm.toggle(q, o.label) },
                idleBg = idleBg,
                multiple = q.multiple,
            )
        }
        KTextField(form.qTyped[q.id].orEmpty(), { vm.type(q, it) }, "Or in your own words", placeholder = "Your answer", minLines = 1)
    }
}

@Composable
fun Footer(label: String, busy: Boolean, onSend: () -> Unit, note: String = "Your answer goes to the flow only. keel never pushes.") {
    Column(
        Modifier.fillMaxWidth().background(K.c.panel).padding(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        PrimaryButton(label, onSend, busy = busy)
        if (note.isNotEmpty()) Text(note, fontSize = 12.sp, color = K.c.dim)
    }
}
