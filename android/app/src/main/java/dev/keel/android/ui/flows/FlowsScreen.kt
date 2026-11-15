package dev.keel.android.ui.flows

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.keel.android.data.RunRow
import dev.keel.android.data.stepOf
import dev.keel.android.data.usd
import dev.keel.android.ui.common.ChoiceRow
import dev.keel.android.ui.common.Dot
import dev.keel.android.ui.common.EmptyState
import dev.keel.android.ui.common.ErrorState
import dev.keel.android.ui.common.FilterChipK
import dev.keel.android.ui.common.InlineError
import dev.keel.android.ui.common.KCard
import dev.keel.android.ui.common.KTextField
import dev.keel.android.ui.common.Loading
import dev.keel.android.ui.common.Phases
import dev.keel.android.ui.common.PrimaryButton
import dev.keel.android.ui.common.SectionLabel
import dev.keel.android.ui.common.StepBars
import dev.keel.android.ui.common.Tag
import dev.keel.android.ui.theme.K
import dev.keel.android.ui.theme.Mono

/** The colours of a flow's state: solid (dot, bar), soft (tag), ink (text). */
data class StateLook(val solid: Color, val soft: Color, val ink: Color)

@Composable
fun lookOf(status: String): StateLook = when (status) {
    "waiting" -> StateLook(K.c.accent, K.c.accentSoft, K.c.accentInk)
    "failed", "stuck" -> StateLook(K.c.bad, K.c.badSoft, K.c.badInk)
    "done" -> StateLook(K.c.ok, K.c.okSoft, K.c.okInk)
    "stopped" -> StateLook(K.c.dim, K.c.panel2, K.c.text2)
    else -> StateLook(K.c.run, K.c.runSoft, K.c.runInk)
}

/** "◆ Waits for you", "Running · green", "Stuck · AC-3 red ×3", "Done · 6 of 6". */
fun stateText(r: RunRow): Pair<String, String> {
    val stuck = r.status == "waiting" && (r.waiting.orEmpty().contains("still red", true) || r.waiting.orEmpty().contains("stuck", true))
    return when {
        r.status == "waiting" && r.waiting.orEmpty().endsWith(" failed") -> "stuck" to "Step failed · ${r.waiting.orEmpty().removeSuffix(" failed")}"
        stuck -> "stuck" to "Stuck · ${r.waiting}"
        r.status == "waiting" -> "waiting" to "◆ Waits for you"
        r.status == "running" -> "running" to "Running · ${r.current ?: r.phase ?: "…"}"
        r.status == "done" -> "done" to "Done · ${r.acsDone} of ${r.acsTotal}"
        r.status == "failed" -> "failed" to "Failed${r.error?.let { " · ${it.take(40)}" } ?: ""}"
        else -> r.status to r.status.replaceFirstChar { it.uppercase() }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FlowsScreen(vm: FlowsViewModel, onOpen: (RunRow) -> Unit) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    LaunchedEffect(ui.started) {
        val tid = ui.started ?: return@LaunchedEffect
        ui.runs.firstOrNull { it.threadId == tid }?.let(onOpen)
        vm.consumedStarted()
    }
    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            Column(Modifier.padding(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 0.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(verticalAlignment = Alignment.Bottom) {
                    Text("Flows", style = MaterialTheme.typography.headlineMedium, color = K.c.fg, modifier = Modifier.weight(1f))
                    val b = ui.budget
                    val day = b?.caps?.firstOrNull { it.scope == "day" && it.unit == "usd" && it.limit > 0 }
                    val text = when {
                        day != null -> "budget ${(day.used * 100 / day.limit).toInt()}% of ${usd(day.limit)}"
                        b != null -> "${usd(b.today.costUsd)} today"
                        else -> ""
                    }
                    Text(text, fontFamily = Mono, fontSize = 12.sp, color = K.c.dim)
                    dev.keel.android.ui.common.DemoChip(Modifier.padding(start = 8.dp))
                }
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChipK("All ${ui.count(FlowFilter.ALL)}", ui.filter == FlowFilter.ALL) { vm.setFilter(FlowFilter.ALL) }
                    FilterChipK("Waits for you ${ui.count(FlowFilter.WAITS)}", ui.filter == FlowFilter.WAITS) { vm.setFilter(FlowFilter.WAITS) }
                    FilterChipK("Running ${ui.count(FlowFilter.RUNNING)}", ui.filter == FlowFilter.RUNNING) { vm.setFilter(FlowFilter.RUNNING) }
                    FilterChipK("Done ${ui.count(FlowFilter.DONE)}", ui.filter == FlowFilter.DONE) { vm.setFilter(FlowFilter.DONE) }
                }
            }
            PullToRefreshBox(isRefreshing = ui.refreshing, onRefresh = vm::refresh, modifier = Modifier.fillMaxSize()) {
                Phases(ui, { s -> when { s.loading -> 0; s.error != null && s.runs.isEmpty() -> 1; s.shown.isEmpty() -> 2; else -> 3 } }) { ui ->
                when {
                    ui.loading -> Loading()
                    ui.error != null && ui.runs.isEmpty() -> ErrorState(ui.error!!, vm::refresh)
                    ui.shown.isEmpty() -> LazyColumn(Modifier.fillMaxSize()) {
                        item {
                            EmptyState(
                                if (ui.runs.isEmpty()) "No flow yet" else "No flow here",
                                if (ui.runs.isEmpty()) "Start one: keel writes the spec, the tests and the code, and asks you at the gates." else "Another filter shows the others.",
                            )
                        }
                    }
                    else -> LazyColumn(
                        Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 96.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        // a filter or a new state moves the cards instead of redrawing the list (#113)
                        items(ui.shown, key = { it.threadId }) { r -> Box(Modifier.animateItem()) { FlowCard(r) { onOpen(r) } } }
                    }
                }
                }
            }
        }
        ExtendedFloatingActionButton(
            onClick = vm::openStart,
            icon = { Icon(Icons.Filled.Add, contentDescription = null) },
            text = { Text("Start a flow", fontSize = 15.sp, fontWeight = FontWeight.SemiBold) },
            containerColor = K.c.accent, contentColor = K.c.onAccent, shape = RoundedCornerShape(16.dp),
            modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
        )
    }
    ui.start?.let { StartFlowSheet(it, vm) }
}

@Composable
private fun FlowCard(r: RunRow, onClick: () -> Unit) {
    val (kind, label) = stateText(r)
    val look = lookOf(kind)
    KCard(onClick = onClick, clickLabel = "Open the flow") {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Dot(look.solid)
            Tag(label, look.soft, look.ink)
            Box(Modifier.weight(1f))
            Text(usd(r.costUsd), fontFamily = Mono, fontSize = 12.sp, color = K.c.dim)
        }
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(r.title.ifBlank { r.threadId }, fontSize = 16.sp, fontWeight = FontWeight.SemiBold, lineHeight = 21.sp, color = K.c.fg)
            val meta = listOfNotNull(r.workflowId?.let { "$it flow" }, r.where, if (r.acsTotal > 0) "AC ${r.acsDone}/${r.acsTotal}" else null, r.branch).joinToString(" · ")
            Text(meta, fontFamily = Mono, fontSize = 12.sp, color = K.c.dim, maxLines = 1)
        }
        StepBars(stepOf(r.phase, r.status), look.solid, look.ink)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun StartFlowSheet(s: StartSheet, vm: FlowsViewModel) {
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(onDismissRequest = vm::closeStart, sheetState = state, containerColor = K.c.panel) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Start a flow", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg)
            KTextField(s.title, { v -> vm.editStart { it.copy(title = v, error = null) } }, "What should keel build or fix?", "e.g. Keep the stored ETA when it is absent", singleLine = true)
            KTextField(s.request, { v -> vm.editStart { it.copy(request = v) } }, "More detail (optional)", "What it is for, where it lives, what must not change", minLines = 3)
            SectionLabel("Workflow")
            if (s.loading) Text("Loading the workflows…", color = K.c.dim, fontSize = 14.sp)
            s.workflows.take(6).forEach { w ->
                ChoiceRow(s.workflowId == w.id, w.name, null, { vm.editStart { it.copy(workflowId = w.id) } })
            }
            Text("It starts in manual mode: keel asks you at every gate. Runs next to other flows in its own worktree when the folder is busy.", fontSize = 12.sp, color = K.c.dim, lineHeight = 17.sp)
            InlineError(s.error)
            PrimaryButton("Start", vm::start, enabled = s.workflowId != null, busy = s.busy)
        }
    }
}
