package dev.keel.android.ui.flow

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.keel.android.data.crashDetail
import dev.keel.android.data.isFailedStep
import dev.keel.android.data.Ac
import dev.keel.android.data.ThreadState
import dev.keel.android.data.clock
import dev.keel.android.data.ktokens
import dev.keel.android.data.usd
import dev.keel.android.ui.common.CodeBlock
import dev.keel.android.ui.common.DetailBar
import dev.keel.android.ui.common.EmptyState
import dev.keel.android.ui.common.ErrorState
import dev.keel.android.ui.common.InlineError
import dev.keel.android.ui.common.Loading
import dev.keel.android.ui.common.Reveal
import dev.keel.android.ui.common.Phases
import dev.keel.android.ui.common.SectionLabel
import dev.keel.android.ui.common.Tag
import dev.keel.android.ui.pair.Banner
import dev.keel.android.ui.question.FlowViewModel
import dev.keel.android.ui.question.Footer
import dev.keel.android.ui.question.GateFormView
import dev.keel.android.ui.theme.K
import dev.keel.android.ui.theme.Mono

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun FlowScreen(vm: FlowViewModel, onBack: () -> Unit) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    val t = ui.thread
    var menu by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize()) {
        DetailBar(t?.title?.ifBlank { null } ?: "Flow", t?.let { "${it.workflowId} flow · ${it.phase}" }, onBack, "Back to the flows") {
            if (t != null) Box {
                IconButton(onClick = { menu = true }, modifier = Modifier.size(48.dp)) { Icon(Icons.Outlined.MoreVert, contentDescription = "More actions", tint = K.c.fg) }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }, containerColor = K.c.panel2) {
                    if (t.status == "running" || t.status == "waiting") {
                        DropdownMenuItem(text = { Text("Stop the flow", color = K.c.badInk) }, onClick = { menu = false; vm.stopFlow() })
                    }
                    if (t.status == "stopped" || t.status == "failed") {
                        DropdownMenuItem(text = { Text("Resume from the last saved step", color = K.c.fg) }, onClick = { menu = false; vm.resume() })
                    }
                    DropdownMenuItem(text = { Text("Reload", color = K.c.fg) }, onClick = { menu = false; vm.refresh() })
                }
            }
        }
        PullToRefreshBox(isRefreshing = ui.refreshing, onRefresh = vm::refresh, modifier = Modifier.weight(1f)) {
            Phases(ui, { x -> when { x.loading -> 0; x.error != null -> 1; x.thread == null -> 2; else -> 3 } }) { ui ->
            val t = ui.thread
            when {
                ui.loading -> Loading()
                ui.error != null -> ErrorState(ui.error!!, { vm.load() })
                t == null -> EmptyState("This flow is gone", "keel does not know it any more.")
                else -> Column(
                    Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, bottom = 16.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp), itemVerticalAlignment = Alignment.CenterVertically) {
                        val (label, soft, ink) = statusLook(t)
                        Tag(label, soft, ink)
                        val tokens = t.usage.tokensIn + t.usage.tokensOut + t.usage.tokensCached / 10
                        Text("${ktokens(tokens)} tokens · ${usd(t.usage.costUsd)}", fontFamily = Mono, fontSize = 12.sp, color = K.c.dim)
                        t.runMode?.let { Text("mode $it", fontFamily = Mono, fontSize = 12.sp, color = K.c.dim) }
                    }
                    Reveal(ui.sent) { Banner(it, null, K.c.okSoft, K.c.okInk) }
                    // #101 a step failed and keel could not repair it: what failed, what to do, and the error itself
                    t.waiting?.takeIf { t.status == "waiting" && isFailedStep(it.kind, it.title, it.crash) }?.let { w ->
                        w.crash?.let { c -> Banner(c.title, c.fix, K.c.badSoft, K.c.badInk) }
                        val what = crashDetail(w.detail, w.crash)
                        if (t.acs.none { it.status == "red" } && what.isNotBlank()) CodeBlock(what, K.c.badInk, K.c.badSoft)
                    }
                    if (t.acs.isNotEmpty()) Criteria(t)
                    else if (!t.error.isNullOrBlank()) CodeBlock(t.error, K.c.badInk, K.c.badSoft)
                    Timeline(ui)
                    ui.form?.let { form ->
                        val shape = RoundedCornerShape(16.dp)
                        Column(
                            Modifier.fillMaxWidth().background(K.c.panel, shape).border(1.dp, K.c.accentBorder, shape).padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(10.dp),
                        ) { GateFormView(form, vm, idleBg = K.c.bg) }
                    }
                    InlineError(ui.sendError, vm::clearError)
                }
            }
            }
        }
        ui.form?.let { Footer(it.sendLabel, ui.sending, vm::send, note = "") }
    }
}

@Composable
private fun statusLook(t: ThreadState): Triple<String, Color, Color> {
    val c = K.c
    val w = t.waiting
    return when {
        t.status == "waiting" && w != null && isFailedStep(w.kind, w.title, w.crash) ->
            Triple("Step failed · ${w.crash?.title ?: w.title.removeSuffix(" failed")}", c.badSoft, c.badInk)
        t.status == "waiting" && t.waiting?.kind == "fix" -> Triple("Stuck · ${t.phase}", c.badSoft, c.badInk)
        t.status == "waiting" -> Triple("◆ Waits for you · ${t.phase}", c.accentSoft, c.accentInk)
        t.status == "running" -> Triple("Running · ${t.current ?: t.phase}", c.runSoft, c.runInk)
        t.status == "done" -> Triple("Done", c.okSoft, c.okInk)
        t.status == "failed" -> Triple("Failed", c.badSoft, c.badInk)
        else -> Triple(t.status.replaceFirstChar { it.uppercase() }, c.panel2, c.text2)
    }
}

@Composable
private fun Criteria(t: ThreadState) {
    val shape = RoundedCornerShape(16.dp)
    Column(Modifier.fillMaxWidth().clip(shape).background(K.c.panel).border(1.dp, K.c.border, shape).semantics { contentDescription = "Acceptance criteria" }) {
        t.acs.forEachIndexed { i, ac ->
            if (i > 0) HorizontalDivider(color = K.c.border)
            val red = ac.status == "red"
            val failure = if (red && (ac.id == t.ac || t.acs.count { it.status == "red" } == 1)) failureText(t) else null
            Column(
                Modifier.fillMaxWidth().background(if (red) K.c.badTint else Color.Transparent).padding(horizontal = 14.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    AcMark(ac)
                    Text(
                        buildAnnotatedString {
                            withStyle(SpanStyle(fontFamily = Mono, fontWeight = FontWeight.Medium)) { append(ac.id) }
                            append(" ")
                            append(ac.title)
                        },
                        fontSize = 14.sp, lineHeight = 20.sp, color = K.c.fg,
                    )
                }
                if (!failure.isNullOrBlank()) CodeBlock(failure, K.c.badInk, K.c.badSoft)
            }
        }
    }
}

private fun failureText(t: ThreadState): String? = t.waiting?.takeIf { it.kind == "fix" || it.kind == "crash" }?.let { crashDetail(it.detail, it.crash) }?.takeIf { it.isNotEmpty() } ?: t.error

@Composable
private fun AcMark(ac: Ac) {
    val c = K.c
    val (mark, soft, ink, word) = when (ac.status) {
        "done", "green", "already-met" -> Quad("✓", c.okSoft, c.okInk, "done")
        "red" -> Quad("✕", c.badSoft, c.badInk, "red")
        else -> Quad("·", c.panel2, c.dim, "to do")
    }
    Box(Modifier.size(24.dp).clip(CircleShape).background(soft).semantics { contentDescription = "${ac.id} $word" }, contentAlignment = Alignment.Center) {
        Text(mark, color = ink, fontSize = 13.sp)
    }
}

private data class Quad(val a: String, val b: Color, val c: Color, val d: String)

@Composable
private fun Timeline(ui: dev.keel.android.ui.question.FlowUi) {
    val t = ui.thread ?: return
    val rows: List<Triple<String, String, Color>> = if (ui.history.isNotEmpty()) {
        ui.history.sortedBy { it.n }.takeLast(12).map { cp ->
            val text = cp.note.ifBlank { "${cp.step} finished" }
            Triple(clock(cp.at), text, colorFor(text))
        }
    } else {
        t.gateLog.takeLast(8).map { Triple("", it, colorFor(it)) }
    }
    if (rows.isEmpty()) return
    Column(Modifier.semantics { contentDescription = "What happened" }) {
        SectionLabel("What happened", Modifier.padding(bottom = 8.dp))
        rows.forEachIndexed { i, (at, text, color) ->
            Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min).heightIn(min = 40.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(at, fontFamily = Mono, fontSize = 12.sp, color = K.c.dim, modifier = Modifier.width(44.dp).padding(top = 1.dp))
                Column(Modifier.width(16.dp).fillMaxHeight(), horizontalAlignment = Alignment.CenterHorizontally) {
                    Box(Modifier.padding(top = 4.dp).size(10.dp).clip(CircleShape).background(color))
                    if (i < rows.size - 1) Box(Modifier.width(2.dp).weight(1f).background(K.c.border))
                }
                Text(text, fontSize = 14.sp, lineHeight = 20.sp, color = K.c.body, modifier = Modifier.weight(1f).padding(bottom = 12.dp))
            }
        }
    }
}

@Composable
private fun colorFor(text: String): Color {
    val s = text.lowercase()
    return when {
        "red" in s || "fail" in s || "stuck" in s -> K.c.bad
        "approved" in s || "you " in s -> K.c.accent
        "green" in s || "ok" in s || "done" in s -> K.c.ok
        else -> K.c.run
    }
}
