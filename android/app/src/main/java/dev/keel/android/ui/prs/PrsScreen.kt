package dev.keel.android.ui.prs

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.keel.android.data.PrSummary
import dev.keel.android.data.ago
import dev.keel.android.data.initials
import dev.keel.android.ui.common.Avatar
import dev.keel.android.ui.common.EmptyState
import dev.keel.android.ui.common.ErrorState
import dev.keel.android.ui.common.KCard
import dev.keel.android.ui.common.Loading
import dev.keel.android.ui.common.Phases
import dev.keel.android.ui.common.Segmented
import dev.keel.android.ui.common.Tag
import dev.keel.android.ui.theme.K
import dev.keel.android.ui.theme.Mono

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PrsScreen(vm: PrsViewModel, onOpen: (Int) -> Unit) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    Column(Modifier.fillMaxSize()) {
        Column(Modifier.padding(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 4.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Pull requests", style = MaterialTheme.typography.headlineMedium, color = K.c.fg, modifier = Modifier.weight(1f))
                dev.keel.android.ui.common.DemoChip()
            }
            Segmented(listOf("review" to "To review", "mine" to "Mine", "all" to "All"), ui.filter, vm::setFilter, Modifier.fillMaxWidth(), height = 40.dp)
        }
        PullToRefreshBox(isRefreshing = ui.refreshing, onRefresh = vm::refresh, modifier = Modifier.fillMaxSize()) {
            Phases(ui, { s -> when { s.loading && s.list == null -> 0; s.error != null && s.list == null -> 1; else -> 2 } }) { ui ->
            val list = ui.list
            when {
                ui.loading && list == null -> Loading()
                ui.error != null && list == null -> ErrorState(ui.error!!, vm::refresh)
                else -> LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    list?.note?.let { note -> item { Text(note, fontSize = 13.sp, color = K.c.warnInk, lineHeight = 19.sp) } }
                    if (list != null && list.prs.isEmpty()) item {
                        EmptyState(
                            when (ui.filter) { "review" -> "No review waits for you"; "mine" -> "No open pull request of yours"; else -> "No open pull request" },
                            if (list.host == null) "keel finds pull requests on GitHub or GitLab once the project has a remote and keel has a token." else "Pull down to look again.",
                        )
                    }
                    items(list?.prs.orEmpty(), key = { it.number }) { p -> Box(Modifier.animateItem()) { PrCard(p, list?.me) { onOpen(p.number) } } }
                }
            }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PrCard(p: PrSummary, me: String?, onClick: () -> Unit) {
    val mine = p.mine || (me != null && p.author == me)
    val (bg, fg) = when ((p.author.hashCode() and 0x7fffffff) % 3) {
        0 -> K.c.runSoft to K.c.runInk
        1 -> K.c.okSoft to K.c.okInk
        else -> K.c.warnSoft to K.c.warnInk
    }
    KCard(onClick = onClick, clickLabel = "Open the pull request", padding = PaddingValues(horizontal = 16.dp, vertical = 14.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            if (mine) Avatar(initials(p.author), K.c.accentSoft, K.c.accentInk) else Avatar(initials(p.author), bg, fg)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(p.title, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, lineHeight = 20.sp, color = K.c.fg)
                Text("#${p.number} · ${if (mine) "you" else p.author} · ${ago(p.updatedAt)}", fontFamily = Mono, fontSize = 12.sp, color = K.c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp), itemVerticalAlignment = Alignment.CenterVertically) {
            if (p.branch.isNotBlank()) Tag(p.branch.substringAfterLast('/'), K.c.accentSoft, K.c.accentInk, mono = true)
            if (p.draft) Tag("draft", K.c.panel2, K.c.text2)
            if (p.reviewRequested) Tag("your review asked", K.c.warnSoft, K.c.warnInk)
            if (p.assigned) Tag("assigned to you", K.c.runSoft, K.c.runInk)
            Box(Modifier.weight(1f))
            Text("→ ${p.base}", fontSize = 12.sp, color = K.c.dim, fontFamily = Mono)
        }
    }
}
