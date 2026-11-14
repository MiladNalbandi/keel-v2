package dev.keel.android.ui.chat

import android.app.Activity
import android.content.Intent
import android.speech.RecognizerIntent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.zIndex
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Forum
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material.icons.outlined.Stop
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.keel.android.data.ANSWERS_PREFIX
import dev.keel.android.data.reachWord
import dev.keel.android.data.ClarifyQuestion
import dev.keel.android.data.HelperMessage
import dev.keel.android.data.ago
import dev.keel.android.data.ktokens
import dev.keel.android.data.splitKeelQuestions
import dev.keel.android.data.toolLines
import dev.keel.android.data.toolSummary
import dev.keel.android.data.usd
import dev.keel.android.data.Model
import dev.keel.android.data.optionChangesFiles
import dev.keel.android.data.shortModel
import dev.keel.android.ui.common.CodeBlock
import dev.keel.android.ui.common.DetailBar
import dev.keel.android.ui.common.EmptyState
import dev.keel.android.ui.common.ErrorState
import dev.keel.android.ui.common.InlineError
import dev.keel.android.ui.common.KeelMark
import dev.keel.android.ui.common.Loading
import dev.keel.android.ui.common.PillButton
import dev.keel.android.ui.common.Segmented
import dev.keel.android.ui.pair.Banner
import dev.keel.android.ui.theme.K
import dev.keel.android.ui.theme.Mono

private val MODE_HINT = mapOf(
    "ask" to "Ask: reads the code, changes nothing.",
    "fix" to "Fix: changes files at the gate a flow waits at, inside its rules; you keep or undo.",
    "edit" to "Edit: changes the project folder; you keep or undo each file.",
    "side" to "Side: works in its own worktree and branch.",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(vm: ChatViewModel, onBack: () -> Unit, onStartFlow: () -> Unit) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    val live by vm.liveText.collectAsStateWithLifecycle()
    val s = ui.session
    val list = rememberLazyListState()
    val messages = s?.messages.orEmpty()
    val streaming = ui.runningCall?.let { live[it] }
    LaunchedEffect(messages.size, streaming?.length) { if (messages.isNotEmpty()) list.animateScrollToItem(maxOf(0, messages.size + 2)) }

    Column(Modifier.fillMaxSize()) {
        // the header is opaque and lifts (shadow + divider) once the messages scroll under it
        val scrolled = list.canScrollBackward
        Surface(color = K.c.bg, shadowElevation = if (scrolled) 4.dp else 0.dp, modifier = Modifier.zIndex(1f)) {
            Column(Modifier.fillMaxWidth()) {
                DetailBar(
                    s?.title?.ifBlank { null } ?: "KeelBot",
                    s?.let { "${ktokens(it.tokensIn + it.tokensOut)} tokens · ${usd(it.costUsd)}" } ?: "a new chat",
                    onBack, "Back",
                ) {
                    IconButton(onClick = { vm.showList(true) }, modifier = Modifier.size(48.dp)) { Icon(Icons.Outlined.Forum, contentDescription = "All chats", tint = K.c.fg) }
                    IconButton(onClick = vm::newChat, modifier = Modifier.size(48.dp)) { Icon(Icons.Outlined.Add, contentDescription = "New chat", tint = K.c.fg) }
                }
                Row(Modifier.padding(horizontal = 16.dp).heightIn(max = 44.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Segmented(listOf("ask" to "Ask", "fix" to "Fix", "edit" to "Edit"), ui.currentMode, vm::setMode, Modifier.width(168.dp), height = 36.dp)
                    val m = s?.model?.takeIf { it.model.isNotBlank() }
                    Row(
                        Modifier.weight(1f, fill = false).height(36.dp).clip(RoundedCornerShape(18.dp)).border(1.dp, K.c.border, RoundedCornerShape(18.dp))
                            .clickable(role = Role.Button, onClickLabel = "Pick the model") { vm.showModels(true) }.padding(start = 12.dp, end = 8.dp),
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp),
                    ) {
                        Text(m?.let { shortModel(it) } ?: "Model", fontFamily = Mono, fontSize = 12.sp, color = K.c.text2, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                        Icon(Icons.Outlined.ExpandMore, contentDescription = null, tint = K.c.dim, modifier = Modifier.size(16.dp))
                    }
                }
                val hintColor = when (ui.currentMode) { "fix" -> K.c.warnInk; "edit" -> K.c.badInk; else -> K.c.dim }
                Text(MODE_HINT[ui.currentMode] ?: "", fontSize = 12.sp, lineHeight = 16.sp, color = hintColor, modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 8.dp))
                HorizontalDivider(color = if (scrolled) K.c.border else K.c.border.copy(alpha = 0.6f))
            }
        }
        Box(Modifier.weight(1f)) {
            when {
                ui.loading -> Loading()
                ui.error != null && s == null -> ErrorState(ui.error!!, vm::start)
                else -> LazyColumn(state = list, modifier = Modifier.fillMaxSize(), contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 20.dp, bottom = 16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    if (messages.isEmpty() && streaming == null) item {
                        EmptyState("Ask KeelBot", "It reads this project and answers with file:line links. Ask mode changes nothing.")
                    }
                    items(messages, key = { it.n }) { m -> Message(m, ui, vm) }
                    if (streaming != null) item { HelperText(streaming) }
                    else if (ui.busy) item { Text("KeelBot is writing…", fontSize = 13.sp, color = K.c.dim) }
                    if (ui.changes.isNotEmpty()) item { ChangesCard(ui, vm) }
                    item { ui.note?.let { Banner(it, null, K.c.okSoft, K.c.okInk) }; InlineError(ui.actionError, vm::clearNote) }
                }
            }
        }
        Composer(ui, vm, onStartFlow)
    }
    if (ui.showList) ChatList(ui, vm)
    if (ui.showModels) ModelSheet(ui, vm)
}

@Composable
private fun Message(m: HelperMessage, ui: ChatUi, vm: ChatViewModel) {
    when (m.role) {
        "user" -> {
            val answered = m.text.startsWith(ANSWERS_PREFIX)
            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                Column(
                    Modifier.widthIn(max = 330.dp).clip(RoundedCornerShape(18.dp, 18.dp, 6.dp, 18.dp)).background(K.c.accentSoft).padding(horizontal = 14.dp, vertical = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    val lines = m.text.lines()
                    val mentions = lines.filter { it.trim().startsWith("@") && !it.contains(' ') }
                    SelectionContainer { Text(lines.filterNot { it in mentions }.joinToString("\n").trim().let { if (answered) it.removePrefix(ANSWERS_PREFIX).trim() else it }, fontSize = 15.sp, lineHeight = 22.sp, color = K.c.fg) }
                    mentions.forEach { Text(it.trim(), fontFamily = Mono, fontSize = 11.sp, color = K.c.accentInk, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(K.c.bg).padding(horizontal = 8.dp, vertical = 3.dp)) }
                }
            }
        }
        "note" -> Text(m.text, fontSize = 13.sp, color = K.c.dim)
        else -> {
            val (text, questions) = splitKeelQuestions(m.text)
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                m.callId?.let { ToolLog(it, ui, vm) }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    KeelMark(24.dp, 7.dp)
                    Text("KeelBot", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = K.c.accentInk)
                    Text(ago(m.at), fontSize = 12.sp, color = K.c.dim)
                }
                HelperText(text)
                if (questions.isNotEmpty()) QuestionsCard(m.n, questions, ui, vm)
            }
        }
    }
}

@Composable
private fun ToolLog(callId: String, ui: ChatUi, vm: ChatViewModel) {
    var open by remember { mutableStateOf(false) }
    val steps = ui.steps[callId]
    val summary = steps?.let { toolSummary(it) }
    if (steps != null && summary.isNullOrBlank()) return
    val shape = RoundedCornerShape(12.dp)
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 44.dp).clip(shape).background(K.c.panel).border(1.dp, K.c.border, shape)
                .clickable(role = Role.Button, onClickLabel = if (open) "Hide the tool log" else "Show the tool log") {
                    if (steps == null) vm.loadSteps(callId)
                    open = !open
                }.padding(horizontal = 12.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text("✓", color = K.c.okInk)
            Text(summary ?: "Tools it used", fontSize = 13.sp, color = K.c.text2, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(if (open) "▾" else "▸", color = K.c.dim)
        }
        if (open && steps != null) CodeBlock(toolLines(steps), bg = K.c.panel)
    }
}

/** KeelBot's answer, with the little Markdown it uses: **bold**, `code`, "- " lists and ``` blocks. */
@Composable
private fun HelperText(text: String) {
    val blocks = remember(text) { splitFences(text) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        blocks.forEach { (code, body) ->
            if (code) CodeBlock(body, bg = K.c.panel)
            else body.split(Regex("\n{2,}")).filter { it.isNotBlank() }.forEach { para ->
                val lines = para.lines()
                if (lines.all { it.trimStart().startsWith("- ") || it.trimStart().startsWith("* ") }) {
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        lines.forEach { l ->
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("•", color = K.c.dim, fontSize = 14.sp)
                                SelectionContainer { Text(inline(l.trimStart().drop(2), K.c.fg, K.c.codeChip, K.c.codeChipInk), fontSize = 14.sp, lineHeight = 21.sp, color = K.c.body) }
                            }
                        }
                    }
                } else {
                    SelectionContainer { Text(inline(para, K.c.fg, K.c.codeChip, K.c.codeChipInk), fontSize = 15.sp, lineHeight = 23.sp, color = K.c.body) }
                }
            }
        }
    }
}

private fun splitFences(text: String): List<Pair<Boolean, String>> {
    val out = mutableListOf<Pair<Boolean, String>>()
    val buf = StringBuilder()
    var inCode = false
    for (line in text.lines()) {
        if (line.trim().startsWith("```")) {
            out += inCode to buf.toString().trimEnd()
            buf.clear()
            inCode = !inCode
        } else buf.append(line).append('\n')
    }
    out += inCode to buf.toString().trimEnd()
    return out.filter { it.second.isNotBlank() }
}

private fun inline(s: String, strong: Color, chip: Color, chipInk: Color): AnnotatedString = buildAnnotatedString {
    var i = 0
    while (i < s.length) {
        when {
            s.startsWith("**", i) && s.indexOf("**", i + 2) > 0 -> {
                val end = s.indexOf("**", i + 2)
                withStyle(SpanStyle(fontWeight = FontWeight.SemiBold, color = strong)) { append(s.substring(i + 2, end)) }
                i = end + 2
            }
            s[i] == '`' && s.indexOf('`', i + 1) > 0 -> {
                val end = s.indexOf('`', i + 1)
                withStyle(SpanStyle(fontFamily = Mono, fontSize = 13.sp, background = chip, color = chipInk)) { append(" " + s.substring(i + 1, end) + " ") }
                i = end + 1
            }
            else -> { append(s[i]); i++ }
        }
    }
}

/** KeelBot asks (```keel-questions```): pick an answer; it goes back as the next message. */
@Composable
private fun QuestionsCard(n: Int, questions: List<ClarifyQuestion>, ui: ChatUi, vm: ChatViewModel) {
    val shape = RoundedCornerShape(16.dp)
    val done = n in ui.answered || ui.session?.messages.orEmpty().any { it.n > n && it.role == "user" }
    val oneTap = questions.size == 1 && !questions[0].multiple
    val ask = ui.currentMode == "ask"
    val switching = ask && questions.any { q -> q.options.any { optionChangesFiles(it) } }
    fun switchFor(picked: Map<String, List<String>>) =
        if (ask && questions.any { q -> q.options.any { o -> o.label in picked[q.id].orEmpty() && optionChangesFiles(o) } }) "edit" else null
    Column(Modifier.fillMaxWidth().clip(shape).background(K.c.panel).border(1.dp, K.c.accentBorder, shape).padding(14.dp).semantics { contentDescription = "KeelBot asks" },
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        questions.forEach { q ->
            Text("◆ ${q.question}", fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg)
            q.options.forEach { o ->
                val on = o.label in ui.qPicked[q.id].orEmpty()
                val oshape = RoundedCornerShape(12.dp)
                Column(
                    Modifier.fillMaxWidth().heightIn(min = 52.dp).clip(oshape).background(if (on) K.c.accentSoft else K.c.bg).border(1.dp, if (on) K.c.accent else K.c.border, oshape)
                        .clickable(enabled = !done && !ui.busy, role = Role.Button) {
                            vm.pickAnswer(q, o.label)
                            if (oneTap) mapOf(q.id to listOf(o.label)).let { p -> vm.answer(n, questions, p, switchFor(p)) }
                        }.padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalArrangement = Arrangement.spacedBy(3.dp),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(o.label, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg, modifier = Modifier.weight(1f, fill = false))
                        if (ask && !done && optionChangesFiles(o)) Text("switches to Edit", fontSize = 11.sp, color = K.c.warnInk,
                            modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(K.c.warnSoft).padding(horizontal = 6.dp, vertical = 2.dp))
                    }
                    o.description?.let { Text(it, fontSize = 12.sp, lineHeight = 17.sp, color = K.c.text2) }
                }
            }
        }
        if (!oneTap && !done) PillButton("Send the answers", { vm.answer(n, questions, ui.qPicked, switchFor(ui.qPicked)) }, filled = true, enabled = !ui.busy)
        if (switching && !done) Text("This chat is in Ask mode: picking a change switches it to Edit, so KeelBot may change files.", fontSize = 12.sp, lineHeight = 17.sp, color = K.c.dim)
        if (done) Text("Sent. KeelBot reads your answer.", fontSize = 12.sp, color = K.c.okInk)
    }
}

@Composable
private fun ChangesCard(ui: ChatUi, vm: ChatViewModel) {
    val shape = RoundedCornerShape(14.dp)
    Column(Modifier.fillMaxWidth().clip(shape).background(K.c.panel).border(1.dp, K.c.warnSoft, shape).padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("KeelBot changed ${ui.changes.size} file${if (ui.changes.size == 1) "" else "s"}", fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = K.c.warnInk)
        ui.changes.take(8).forEach { c ->
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(c.path, fontFamily = Mono, fontSize = 12.sp, color = K.c.fg, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text("+${c.added} −${c.removed}", fontFamily = Mono, fontSize = 12.sp, color = K.c.dim)
                PillButton("Undo", { vm.undo(c.path) }, height = 44.dp)
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            PillButton("Keep (commit)", vm::keep, filled = true)
            PillButton("Undo all", { vm.undo(null) })
        }
    }
}

@Composable
private fun Composer(ui: ChatUi, vm: ChatViewModel, onStartFlow: () -> Unit) {
    val speak = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        if (r.resultCode == Activity.RESULT_OK) {
            val said = r.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
            if (!said.isNullOrBlank()) vm.setDraft((ui.draft + " " + said).trim())
        }
    }
    Column(Modifier.fillMaxWidth().background(K.c.panel).padding(start = 12.dp, end = 12.dp, top = 10.dp, bottom = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            PillButton("Explain the diff", { vm.send("Explain the diff of this branch: what changed and why.") }, height = 32.dp, corner = 16.dp, enabled = !ui.busy)
            PillButton("Start a fix flow", onStartFlow, height = 32.dp, corner = 16.dp)
            PillButton("/compact", { vm.send("/compact") }, height = 32.dp, corner = 16.dp, enabled = !ui.busy && ui.session != null)
        }
        Row(
            Modifier.fillMaxWidth().heightIn(min = 52.dp).clip(RoundedCornerShape(26.dp)).background(K.c.bg).border(1.dp, K.c.border, RoundedCornerShape(26.dp)).padding(6.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            IconButton(
                onClick = { vm.setDraft(ui.draft + if (ui.draft.isEmpty() || ui.draft.endsWith(" ")) "@" else " @") },
                colors = IconButtonDefaults.iconButtonColors(containerColor = K.c.panel2, contentColor = K.c.accentInk), modifier = Modifier.size(44.dp),
            ) { Text("@", fontFamily = Mono, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, color = K.c.accentInk, modifier = Modifier.semantics { contentDescription = "Add a file or symbol" }) }
            TextField(
                value = ui.draft, onValueChange = vm::setDraft, modifier = Modifier.weight(1f).semantics { contentDescription = "Message KeelBot" },
                placeholder = { Text("Ask about this project…", color = K.c.dim) }, maxLines = 6,
                colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent, focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent, cursorColor = K.c.accent, focusedTextColor = K.c.fg, unfocusedTextColor = K.c.fg),
            )
            IconButton(onClick = {
                speak.launch(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM))
            }, modifier = Modifier.size(44.dp)) { Icon(Icons.Outlined.Mic, contentDescription = "Speak", tint = K.c.text2) }
            if (ui.busy && !ui.sending) {
                IconButton(onClick = vm::stop, colors = IconButtonDefaults.iconButtonColors(containerColor = K.c.badSoft, contentColor = K.c.badInk), modifier = Modifier.size(44.dp)) {
                    Icon(Icons.Outlined.Stop, contentDescription = "Stop KeelBot")
                }
            } else {
                IconButton(
                    onClick = { vm.send() }, enabled = ui.draft.isNotBlank() && !ui.sending,
                    colors = IconButtonDefaults.iconButtonColors(containerColor = K.c.accent, contentColor = K.c.onAccent, disabledContainerColor = K.c.accent.copy(alpha = 0.4f), disabledContentColor = K.c.onAccent),
                    modifier = Modifier.size(44.dp),
                ) { Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send", modifier = Modifier.size(20.dp)) }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatList(ui: ChatUi, vm: ChatViewModel) {
    ModalBottomSheet(onDismissRequest = { vm.showList(false) }, containerColor = K.c.panel) {
        Column(Modifier.fillMaxWidth().padding(bottom = 24.dp)) {
            Text("Chats", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg, modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
            if (ui.chats.isEmpty()) Text("No chat yet.", color = K.c.dim, modifier = Modifier.padding(16.dp))
            ui.chats.take(40).forEach { c ->
                Row(
                    Modifier.fillMaxWidth().heightIn(min = 56.dp).clickable(role = Role.Button) { vm.open(c.id) }.padding(horizontal = 16.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(c.title.ifBlank { "Chat" }, fontSize = 15.sp, color = if (c.id == ui.session?.id) K.c.accentInk else K.c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text("${c.mode} · ${c.turns} message${if (c.turns == 1) "" else "s"} · ${ago(c.updatedAt)}", fontSize = 12.sp, color = K.c.dim)
                    }
                }
            }
        }
    }
}

/** The model and effort for this chat: the catalog keel's ModelPicker reads (GET /api/providers/models). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ModelSheet(ui: ChatUi, vm: ChatViewModel) {
    val now = ui.session?.model ?: Model()
    var provider by remember(ui.catalog) { mutableStateOf(now.provider.ifBlank { ui.catalog?.firstOrNull()?.id.orEmpty() }) }
    val p = ui.catalog?.firstOrNull { it.id == provider }
    var mode by remember(provider, ui.catalog) { mutableStateOf(if (p?.modes?.containsKey(now.mode) == true && provider == now.provider) now.mode else p?.modes?.keys?.firstOrNull().orEmpty()) }
    val models = p?.modes?.get(mode).orEmpty()
    var model by remember(provider, mode, ui.catalog) { mutableStateOf(if (provider == now.provider && models.any { it.id == now.model }) now.model else models.firstOrNull()?.id.orEmpty()) }
    val efforts = p?.effortsOf(model, mode).orEmpty()
    var effort by remember(provider, mode, model, ui.catalog) { mutableStateOf(if (now.effort in efforts) now.effort else efforts.firstOrNull { it == "high" } ?: efforts.firstOrNull()) }
    ModalBottomSheet(onDismissRequest = { vm.showModels(false) }, containerColor = K.c.panel) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Model for this chat", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg)
            when {
                ui.catalog == null -> Text("Loading the models…", color = K.c.dim, fontSize = 14.sp)
                ui.catalog.isEmpty() -> Text("keel lists no model.", color = K.c.dim, fontSize = 14.sp)
                else -> {
                    SheetLabel("Provider")
                    ChipRow(ui.catalog.map { it.id to it.label }, provider) { provider = it }
                    if ((p?.modes?.size ?: 0) > 1) {
                        SheetLabel("Runs on")
                        ChipRow(p!!.modes.keys.map { it to reachWord(provider) }.distinctBy { it.second }, mode) { mode = it }
                    }
                    SheetLabel("Model")
                    models.forEach { cm -> ChoiceRowSmall(cm.label, cm.id == model) { model = cm.id } }
                    SheetLabel("Effort")
                    if (efforts.isEmpty()) Text("This model has no effort setting.", fontSize = 13.sp, color = K.c.dim)
                    else ChipRow(efforts.map { it to it }, effort ?: "") { effort = it }
                }
            }
            InlineError(ui.catalogError)
            Text("Runs on ${reachWord(provider)}. keel keeps this chat: another model goes on with the same conversation.", fontSize = 12.sp, color = K.c.dim, lineHeight = 17.sp)
            dev.keel.android.ui.common.PrimaryButton("Use ${listOfNotNull(model.ifBlank { null }, effort).joinToString(" · ").ifBlank { "it" }}",
                { vm.setModel(Model(provider, mode, model, effort)) }, enabled = model.isNotBlank())
        }
    }
}

@Composable
private fun SheetLabel(t: String) = Text(t.uppercase(), fontSize = 12.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 1.sp, color = K.c.dim, modifier = Modifier.padding(top = 6.dp))

@Composable
private fun ChipRow(items: List<Pair<String, String>>, picked: String, onPick: (String) -> Unit) {
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        items.forEach { (id, label) -> dev.keel.android.ui.common.FilterChipK(label, id == picked) { onPick(id) } }
    }
}

@Composable
private fun ChoiceRowSmall(label: String, on: Boolean, onClick: () -> Unit) {
    val shape = RoundedCornerShape(12.dp)
    Row(
        Modifier.fillMaxWidth().heightIn(min = 48.dp).clip(shape).background(if (on) K.c.accentSoft else K.c.bg).border(1.dp, if (on) K.c.accent else K.c.border, shape)
            .clickable(role = Role.RadioButton, onClick = onClick).padding(horizontal = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) { Text(label, fontFamily = Mono, fontSize = 14.sp, color = if (on) K.c.accentInk else K.c.fg) }
}
