package dev.keel.android.ui.inbox

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.CallMerge
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.keel.android.data.isFailedStep
import dev.keel.android.data.InboxItem
import dev.keel.android.data.PrSummary
import dev.keel.android.data.ago
import dev.keel.android.data.usd
import dev.keel.android.ui.common.EmptyState
import dev.keel.android.ui.common.ErrorState
import dev.keel.android.ui.common.HeaderChip
import dev.keel.android.ui.common.HomeViewModel
import dev.keel.android.ui.common.InlineError
import dev.keel.android.ui.common.KCard
import dev.keel.android.ui.common.KeelMark
import dev.keel.android.ui.common.Loading
import dev.keel.android.ui.common.Phases
import dev.keel.android.ui.common.PillButton
import dev.keel.android.ui.common.SectionLabel
import dev.keel.android.ui.common.Tag
import dev.keel.android.ui.theme.K
import dev.keel.android.ui.theme.Mono

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun InboxScreen(
    vm: InboxViewModel,
    home: HomeViewModel,
    onQuestion: (String, String) -> Unit,
    onFlow: (String, String) -> Unit,
    onPr: (Int) -> Unit,
    onSettings: () -> Unit,
) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    val h by home.ui.collectAsStateWithLifecycle()
    Column(Modifier.fillMaxSize()) {
        Column(Modifier.padding(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 8.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                ProjectSwitcher(h.projects.map { it.id to it.name }, h.project, home::pickProject, Modifier.weight(1f))
                dev.keel.android.ui.common.DemoChip()
                IconButton(onClick = onSettings, modifier = Modifier.size(48.dp)) { Icon(Icons.Outlined.Settings, contentDescription = "Settings", tint = K.c.fg) }
            }
            Row(verticalAlignment = Alignment.Bottom) {
                Text("Waiting for you", style = MaterialTheme.typography.headlineMedium, color = K.c.fg, modifier = Modifier.weight(1f))
                if (!ui.loading) Text("${ui.count} thing${if (ui.count == 1) "" else "s"}", fontSize = 13.sp, color = K.c.dim)
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val q = ui.questions.size + ui.permissions.size
                HeaderChip("◆ $q question${if (q == 1) "" else "s"}", K.c.accentSoft, K.c.accentInk)
                if (ui.reviews.isNotEmpty()) HeaderChip("${ui.reviews.size} review${if (ui.reviews.size == 1) "" else "s"}", K.c.runSoft, K.c.runInk)
                ui.today?.let { HeaderChip("${usd(it)} today", androidx.compose.ui.graphics.Color.Transparent, K.c.dim, K.c.border) }
            }
        }
        val demo = dev.keel.android.ui.common.LocalDemo.current
        PullToRefreshBox(isRefreshing = ui.refreshing, onRefresh = vm::refresh, modifier = Modifier.fillMaxSize()) {
            Phases(ui, { s -> when { s.loading -> 0; s.error != null && s.items.isEmpty() -> 1; else -> 2 } }) { ui ->
            when {
                ui.loading -> Loading()
                ui.error != null && ui.items.isEmpty() -> ErrorState(ui.error!!, vm::refresh)
                else -> LazyColumn(
                    Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    if (demo) item(key = "demo") {
                        Text(
                            "Demo data — pair a keel in Settings to see yours",
                            fontSize = 13.sp, color = K.c.warnInk, maxLines = 2,
                            modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(K.c.warnSoft).clickable(role = Role.Button, onClick = onSettings).padding(horizontal = 12.dp, vertical = 8.dp),
                        )
                    }
                    item(key = "error") { InlineError(ui.actionError, vm::clearActionError) }
                    if (ui.items.isEmpty() && ui.reviews.isEmpty()) {
                        item(key = "empty") { Box(Modifier.animateItem()) { EmptyState("Nothing waits for you", "When a flow asks something, it shows here and the phone tells you.") } }
                    }
                    if (ui.items.isNotEmpty()) item(key = "questions") { SectionLabel("Questions", Modifier.animateItem()) }
                    // an answered card fades and the ones below slide up; a new one fades in (#113)
                    ui.items.forEach { it ->
                        item(key = (it.id ?: it.threadId) + it.kind) {
                            Box(Modifier.animateItem()) {
                                if (it.kind == "permission") PermissionCard(it, ui.busy == (it.permission?.id ?: it.id), vm::decide)
                                else QuestionCard(it, onClick = {
                                    if (it.kind == "fix" || it.kind == "crash") onFlow(it.projectId, it.threadId) else onQuestion(it.projectId, it.threadId)
                                })
                            }
                        }
                    }
                    if (ui.reviews.isNotEmpty()) {
                        item(key = "reviews") { SectionLabel("Reviews", Modifier.animateItem().padding(top = 6.dp)) }
                        ui.reviews.forEach { p -> item(key = "pr${p.number}") { Box(Modifier.animateItem()) { ReviewCard(p) { onPr(p.number) } } } }
                    }
                }
            }
            }
        }
    }
}

@Composable
fun ProjectSwitcher(projects: List<Pair<String, String>>, current: String?, onPick: (String) -> Unit, modifier: Modifier = Modifier) {
    var open by remember { mutableStateOf(false) }
    Box(modifier) {
        Row(
            Modifier.fillMaxWidth().height(48.dp).clip(RoundedCornerShape(24.dp)).background(K.c.panel).border(1.dp, K.c.border, RoundedCornerShape(24.dp))
                .clickable(role = Role.Button, onClickLabel = "Switch project") { open = true }.padding(start = 10.dp, end = 12.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            KeelMark()
            Text(projects.firstOrNull { it.first == current }?.second ?: current ?: "No project yet", fontFamily = Mono, fontSize = 12.sp, color = K.c.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            Icon(Icons.Outlined.ExpandMore, contentDescription = null, tint = K.c.dim, modifier = Modifier.size(18.dp))
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }, containerColor = K.c.panel2) {
            projects.forEach { (id, name) ->
                DropdownMenuItem(text = { Text(name, fontFamily = Mono, fontSize = 13.sp, color = if (id == current) K.c.accentInk else K.c.fg) }, onClick = { open = false; onPick(id) })
            }
        }
    }
}

/** The tag of a question card: what kind of stop it is. */
private data class Look(val tag: String, val soft: androidx.compose.ui.graphics.Color, val ink: androidx.compose.ui.graphics.Color, val border: androidx.compose.ui.graphics.Color)

@Composable
private fun lookOf(it: InboxItem): Look {
    val c = K.c
    return when {
        isFailedStep(it.kind, it.title) -> Look("Step failed", c.badSoft, c.badInk, c.badBorder)
        it.kind == "fix" -> Look("Stuck", c.badSoft, c.badInk, c.badBorder)
        it.kind == "clarify" && it.questions.orEmpty().any { q -> q.multiple } -> Look("Pick any", c.warnSoft, c.warnInk, c.border)
        it.kind == "clarify" -> Look("Question", c.accentSoft, c.accentInk, c.accentBorder)
        it.kind == "usage" || it.kind == "budget" -> Look("Budget", c.warnSoft, c.warnInk, c.border)
        it.kind == "dependency" -> Look("New dependency", c.warnSoft, c.warnInk, c.border)
        it.phase.orEmpty().startsWith("spec") -> Look("Spec gate", c.accentSoft, c.accentInk, c.accentBorder)
        it.ac != null -> Look("${it.ac} gate", c.accentSoft, c.accentInk, c.accentBorder)
        else -> Look("Gate", c.accentSoft, c.accentInk, c.accentBorder)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun QuestionCard(it: InboxItem, onClick: () -> Unit) {
    val look = lookOf(it)
    KCard(border = look.border, onClick = onClick, clickLabel = "Answer") {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Tag(look.tag, look.soft, look.ink, upper = true)
            Box(Modifier.weight(1f))
            Text(ago(it.since), fontSize = 12.sp, color = K.c.dim)
        }
        Text(it.title.ifBlank { it.flow }, fontSize = 17.sp, fontWeight = FontWeight.SemiBold, lineHeight = 22.sp, color = K.c.fg)
        val opts = it.questions?.firstOrNull()?.options.orEmpty()
        if (it.kind == "clarify" && opts.isNotEmpty()) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                opts.take(3).forEach { o -> OptionChip(o.label) }
                if (opts.size > 3) OptionChip("+${opts.size - 3}")
            }
        } else if (it.detail.isNotBlank()) {
            Text(it.detail.trim().lines().filter { l -> l.isNotBlank() }.take(3).joinToString("\n"), fontSize = 14.sp, lineHeight = 20.sp, color = K.c.text2, maxLines = 3, overflow = TextOverflow.Ellipsis)
        }
        Text(listOfNotNull(it.flow.takeIf { f -> f.isNotBlank() && f != it.title }, it.workflowId?.let { w -> "$w flow" }, it.projectName.takeIf { p -> p.isNotBlank() }).joinToString(" · "),
            fontFamily = Mono, fontSize = 12.sp, color = K.c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun OptionChip(label: String) {
    Text(label, fontSize = 12.sp, color = K.c.text2, modifier = Modifier.clip(RoundedCornerShape(14.dp)).border(1.dp, K.c.border, RoundedCornerShape(14.dp)).padding(horizontal = 10.dp, vertical = 4.dp))
}

@Composable
private fun PermissionCard(it: InboxItem, busy: Boolean, decide: (InboxItem, String) -> Unit) {
    KCard(border = K.c.accentBorder) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Tag("Command", K.c.warnSoft, K.c.warnInk, upper = true)
            Box(Modifier.weight(1f))
            Text(ago(it.since), fontSize = 12.sp, color = K.c.dim)
        }
        Text(it.title.ifBlank { "KeelBot asks to run a command" }, fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg)
        Text(it.permission?.command ?: it.detail, fontFamily = Mono, fontSize = 13.sp, color = K.c.text2)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            PillButton("Once", { decide(it, "once") }, filled = true, enabled = !busy)
            PillButton("Always", { decide(it, "always") }, enabled = !busy)
            PillButton("Deny", { decide(it, "deny") }, enabled = !busy)
        }
    }
}

@Composable
private fun ReviewCard(p: PrSummary, onClick: () -> Unit) {
    KCard(onClick = onClick, clickLabel = "Open the pull request", padding = PaddingValues(horizontal = 16.dp, vertical = 14.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Box(Modifier.size(36.dp).clip(RoundedCornerShape(10.dp)).background(K.c.runSoft), contentAlignment = Alignment.Center) {
                Icon(Icons.AutoMirrored.Outlined.CallMerge, contentDescription = null, tint = K.c.runInk, modifier = Modifier.size(18.dp))
            }
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("#${p.number} ${p.title}", fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg, lineHeight = 20.sp)
                Text(if (p.reviewRequested) "Your review is asked" else "Waits for you", fontSize = 13.sp, color = K.c.warnInk)
                Text("${p.author} · ${ago(p.updatedAt)}${if (p.draft) " · draft" else ""}", fontSize = 12.sp, color = K.c.dim)
            }
        }
    }
}
