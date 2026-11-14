package dev.keel.android.ui.thread

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.WrapText
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconToggleButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.keel.android.data.DiffRow
import dev.keel.android.data.ReviewThread
import dev.keel.android.data.threadOnLine
import dev.keel.android.ui.common.DetailBar
import dev.keel.android.ui.common.EmptyState
import dev.keel.android.ui.common.ErrorState
import dev.keel.android.ui.common.InlineError
import dev.keel.android.ui.common.KTextField
import dev.keel.android.ui.common.Loading
import dev.keel.android.ui.common.PrimaryButton
import dev.keel.android.ui.pair.Banner
import dev.keel.android.ui.theme.K
import dev.keel.android.ui.theme.Mono

/** What the diff list shows, one item each (so a long file is virtualised line by line). */
private sealed class Item(val key: String) {
    class Header(val text: String, i: Int) : Item("h$i")
    class Line(val row: DiffRow.Line, i: Int) : Item("l$i")
    class Thread(val t: ReviewThread) : Item("t${t.id}")
    class Pending(val id: String, val body: String) : Item("d$id")
}

private data class CommentTarget(val line: Int, val side: String, val text: String)

/**
 * One file of a pull request: its unified diff (the review plugin's GET .../review/diff, `key=pr:N`), with old and new line
 * numbers, the review threads on their lines, and a comment on any line (long-press it, or tap its number).
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun DiffScreen(vm: ThreadViewModel, path: String, onBack: () -> Unit) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    LaunchedEffect(path) { vm.loadFile(path) }
    var wrap by rememberSaveable { mutableStateOf(false) }
    var open by remember { mutableStateOf<String?>(null) }
    var target by remember { mutableStateOf<CommentTarget?>(null) }
    val state = ui.files[path]
    val file = ui.view?.files?.firstOrNull { it.path == path }
    val threads = ui.view?.threads.orEmpty().filter { it.path == path }
    val drafts = ui.drafts.filter { it.path == path }

    Column(Modifier.fillMaxSize()) {
        DetailBar(path.substringAfterLast('/'), file?.let { "+${it.added} −${it.removed} · ${shortPath(path)}" } ?: shortPath(path), onBack, "Back to the files") {
            IconToggleButton(checked = wrap, onCheckedChange = { wrap = it }) {
                Icon(Icons.AutoMirrored.Outlined.WrapText, contentDescription = if (wrap) "Long lines wrap: tap to scroll them" else "Long lines scroll: tap to wrap them", tint = if (wrap) K.c.accentInk else K.c.dim)
            }
        }
        ui.note?.let { Box(Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) { Banner(it, null, K.c.okSoft, K.c.okInk) } }
        ui.actionError?.let { Box(Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) { InlineError(it, vm::clearNote) } }
        Box(Modifier.weight(1f)) {
            when {
                state == null || state.loading -> Loading()
                state.error != null -> ErrorState(state.error, { vm.loadFile(path, force = true) })
                state.binary -> EmptyState("A binary file", "Its changes cannot be shown as text.")
                state.rows.isEmpty() -> EmptyState("No changes to show", "keel has no diff for this file.")
                else -> {
                    val items = remember(state.rows, threads, drafts) { buildItems(state.rows, threads, drafts.map { Triple(it.id, it.line, it.body) }) }
                    val maxChars = remember(state.rows) { state.rows.maxOf { if (it is DiffRow.Line) it.text.length else 0 }.coerceAtLeast(20) }
                    val measurer = rememberTextMeasurer()
                    val density = LocalDensity.current
                    val codeWidth = remember(maxChars) {
                        val w = measurer.measure("M", TextStyle(fontFamily = Mono, fontSize = 12.sp)).size.width
                        with(density) { (w * (maxChars + 2)).toDp() + 16.dp }
                    }
                    val hScroll = rememberScrollState()
                    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 24.dp)) {
                        if (state.truncated) item { Text("keel cut this diff: it is long. The rest is on the code host.", fontSize = 12.sp, color = K.c.warnInk, modifier = Modifier.padding(12.dp)) }
                        items(items, key = { it.key }) { item ->
                            when (item) {
                                is Item.Header -> Text(
                                    item.text, fontFamily = Mono, fontSize = 12.sp, color = K.c.runInk, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                    modifier = Modifier.fillMaxWidth().background(K.c.runSoft).padding(horizontal = 12.dp, vertical = 6.dp),
                                )
                                is Item.Line -> LineRow(item.row, wrap, hScroll, codeWidth) { n, side -> target = CommentTarget(n, side, item.row.text) }
                                is Item.Thread -> InlineThread(item.t, ui, vm, expanded = open == item.t.id) {
                                    open = if (open == item.t.id) null else item.t.id
                                    if (open != null) vm.select(item.t.id)
                                }
                                is Item.Pending -> PendingRow(item.body) { vm.deleteDraft(item.id) }
                            }
                        }
                    }
                }
            }
        }
        PendingBar(ui, vm)
    }
    target?.let { t -> CommentSheet(t, busy = ui.busy == "comment", onDismiss = { target = null }) { body -> vm.comment(path, t.line, t.side, body); target = null } }
}

private fun buildItems(rows: List<DiffRow>, threads: List<ReviewThread>, drafts: List<Triple<String, Int?, String>>): List<Item> {
    val out = mutableListOf<Item>()
    val placed = mutableSetOf<String>()
    rows.forEachIndexed { i, r ->
        when (r) {
            is DiffRow.Hunk -> out += Item.Header(r.header, i)
            is DiffRow.Line -> {
                out += Item.Line(r, i)
                threads.filter { it.id !in placed && threadOnLine(it, r) }.forEach { placed += it.id; out += Item.Thread(it) }
                drafts.filter { d -> d.second != null && d.second == (r.new ?: r.old) && r.mark != '-' }.forEach { out += Item.Pending(it.first, it.third) }
            }
        }
    }
    // threads whose line is not in the diff any more (outdated): at the end
    threads.filter { it.id !in placed }.forEach { out += Item.Thread(it) }
    return out
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun LineRow(l: DiffRow.Line, wrap: Boolean, hScroll: ScrollState, codeWidth: androidx.compose.ui.unit.Dp, onComment: (Int, String) -> Unit) {
    val (bg, fg) = when (l.mark) {
        '+' -> K.c.okSoft to K.c.okInk
        '-' -> K.c.badSoft to K.c.badInk
        else -> Color.Transparent to K.c.body
    }
    val number = l.new ?: l.old ?: 0
    val side = if (l.mark == '-') "LEFT" else "RIGHT"
    val comment = { onComment(number, side) }
    Row(
        Modifier.fillMaxWidth().background(bg).combinedClickable(onClick = {}, onLongClick = comment, onLongClickLabel = "Comment on line $number")
            .semantics { contentDescription = "${if (l.mark == '+') "added" else if (l.mark == '-') "removed" else "line"} $number: ${l.text}" },
    ) {
        // the line numbers: old | new; a tap there comments on the line
        Row(Modifier.clickable(role = Role.Button, onClickLabel = "Comment on line $number", onClick = comment).padding(start = 4.dp)) {
            Text((l.old?.toString() ?: "").padStart(4), fontFamily = Mono, fontSize = 11.sp, lineHeight = 18.sp, color = K.c.dim, modifier = Modifier.width(34.dp))
            Text((l.new?.toString() ?: "").padStart(4), fontFamily = Mono, fontSize = 11.sp, lineHeight = 18.sp, color = K.c.dim, modifier = Modifier.width(34.dp))
            Text(l.mark.toString(), fontFamily = Mono, fontSize = 12.sp, lineHeight = 18.sp, color = fg, modifier = Modifier.width(14.dp))
        }
        if (wrap) {
            Text(l.text, fontFamily = Mono, fontSize = 12.sp, lineHeight = 18.sp, color = fg, modifier = Modifier.weight(1f).padding(end = 8.dp))
        } else {
            Box(Modifier.weight(1f).horizontalScroll(hScroll)) {
                Text(l.text, fontFamily = Mono, fontSize = 12.sp, lineHeight = 18.sp, color = fg, softWrap = false, maxLines = 1, modifier = Modifier.width(codeWidth).padding(end = 8.dp))
            }
        }
    }
}

/** A thread on its line: folded to one row (author · first line · replies · state); open, the comments and its actions. */
@Composable
private fun InlineThread(t: ReviewThread, ui: ThreadUi, vm: ThreadViewModel, expanded: Boolean, onToggle: () -> Unit) {
    val shape = RoundedCornerShape(12.dp)
    val first = t.comments.firstOrNull()
    val state = when {
        t.resolved -> "Resolved" to K.c.okInk
        ui.waitsForMe(t) -> "Waits for you" to K.c.warnInk
        else -> "Open" to K.c.text2
    }
    Column(
        Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp).clip(shape).background(K.c.panel)
            .border(1.dp, if (expanded) K.c.accent else K.c.accentBorder, shape),
    ) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable(role = Role.Button, onClickLabel = if (expanded) "Fold the thread" else "Open the thread", onClick = onToggle)
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(if (expanded) "▾" else "▸", color = K.c.dim)
            Text(
                "${first?.author ?: ""} · ${first?.body?.lineSequence()?.firstOrNull().orEmpty()}",
                fontSize = 13.sp, color = K.c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f),
            )
            val replies = (t.comments.size - 1).coerceAtLeast(0)
            if (replies > 0) Text("$replies repl${if (replies == 1) "y" else "ies"}", fontSize = 12.sp, color = K.c.dim)
            Text(state.first, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = state.second)
        }
        if (expanded) Column(Modifier.padding(start = 12.dp, end = 12.dp, bottom = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            ThreadComments(t, ui)
            val run = ui.ai.judges[t.id]
            if (run != null && t.id !in ui.dismissed) CheckCard(t, run, ui, vm, busy = ui.busy == "judge")
            dev.keel.android.data.fixNote(ui.ai.fixed[t.id], t)?.let { FixLine(it, t, ui, vm) }
            if (!t.resolved) ThreadActions(t, ui, vm)
        }
    }
}

@Composable
private fun PendingRow(body: String, onDelete: () -> Unit) {
    val shape = RoundedCornerShape(12.dp)
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp).clip(shape).background(K.c.accentSoft).padding(start = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f).padding(vertical = 8.dp)) {
            Text("Pending · you", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = K.c.accentInk)
            Text(body, fontSize = 13.sp, color = K.c.fg)
        }
        androidx.compose.material3.TextButton(onClick = onDelete) { Text("Delete", color = K.c.accentInk) }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CommentSheet(t: CommentTarget, busy: Boolean, onDismiss: () -> Unit, onSend: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true), containerColor = K.c.panel) {
        Column(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Comment on line ${t.line}${if (t.side == "LEFT") " (old)" else ""}", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg)
            Text(t.text.trim().ifEmpty { " " }, fontFamily = Mono, fontSize = 12.sp, color = K.c.text2, maxLines = 3, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(K.c.bg).padding(10.dp))
            KTextField(text, { text = it }, null, "What should change here?", minLines = 3)
            Text("It stays a pending comment of your review until you press Send review.", fontSize = 12.sp, color = K.c.dim)
            PrimaryButton("Add to my review", { onSend(text) }, enabled = text.isNotBlank(), busy = busy)
        }
    }
}
