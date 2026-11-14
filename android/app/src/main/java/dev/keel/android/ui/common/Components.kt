package dev.keel.android.ui.common

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.keel.android.data.KeelException
import dev.keel.android.data.STEP_NAMES
import dev.keel.android.ui.theme.K
import dev.keel.android.ui.theme.KeelMotion
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.LocalIndication
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.composed
import dev.keel.android.ui.theme.Mono

/** True while the app shows the demo keel's fake data (AppRoot provides it). */
val LocalDemo = androidx.compose.runtime.staticCompositionLocalOf { false }

/** "DEMO" in warn colours, on every screen while the data is the demo's. */
@Composable
fun DemoChip(modifier: Modifier = Modifier) {
    if (!LocalDemo.current) return
    Text(
        "DEMO", color = K.c.warnInk, fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.8.sp, maxLines = 1,
        modifier = modifier.clip(RoundedCornerShape(6.dp)).background(K.c.warnSoft).border(1.dp, K.c.warn.copy(alpha = 0.5f), RoundedCornerShape(6.dp))
            .padding(horizontal = 7.dp, vertical = 3.dp).semantics { contentDescription = "Demo data" },
    )
}

/** keel's "k" mark: the accent square. */
@Composable
fun KeelMark(size: Dp = 22.dp, corner: Dp = 7.dp) {
    Box(
        Modifier.size(size).clip(RoundedCornerShape(corner)).background(K.c.accent),
        contentAlignment = Alignment.Center,
    ) { Text("k", color = K.c.onAccent, fontWeight = FontWeight.Bold, fontSize = (size.value * 0.55f).sp) }
}

/** A small tag: "SPEC GATE", "Stuck · code". */
@Composable
fun Tag(text: String, bg: Color, fg: Color, upper: Boolean = false, mono: Boolean = false) {
    Text(
        if (upper) text.uppercase() else text,
        color = fg,
        fontSize = if (upper) 11.sp else 12.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = if (upper) 0.6.sp else 0.sp,
        fontFamily = if (mono) Mono else null,
        modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(bg).padding(horizontal = 8.dp, vertical = 3.dp),
        maxLines = 1,
    )
}

/** A header chip (height 32): "◆ 3 questions". */
@Composable
fun HeaderChip(text: String, bg: Color, fg: Color, border: Color? = null) {
    Box(
        Modifier.height(32.dp).clip(RoundedCornerShape(8.dp)).background(bg)
            .then(if (border != null) Modifier.border(1.dp, border, RoundedCornerShape(8.dp)) else Modifier)
            .padding(horizontal = 12.dp),
        contentAlignment = Alignment.Center,
    ) { Text(text, color = fg, fontSize = 13.sp, fontWeight = FontWeight.Medium) }
}

@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text.uppercase(), color = K.c.dim, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 1.sp,
        modifier = modifier.padding(top = 6.dp, bottom = 2.dp).semantics { heading() },
    )
}

/** The card every list uses: panel background, 16dp corners, a 1dp border. */
@Composable
fun KCard(
    modifier: Modifier = Modifier,
    border: Color = K.c.border,
    bg: Color = K.c.panel,
    padding: PaddingValues = PaddingValues(16.dp),
    onClick: (() -> Unit)? = null,
    clickLabel: String? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val shape = RoundedCornerShape(16.dp)
    val press = remember { MutableInteractionSource() }
    val edge by animateColorAsState(border, KeelMotion.defaultEffectsSpec(), label = "card border")
    Column(
        modifier.pressScale(press, 0.98f).fillMaxWidth().animateContentSize(KeelMotion.size).clip(shape).background(bg).border(1.dp, edge, shape)
            .then(
                if (onClick != null) Modifier.clickable(interactionSource = press, indication = LocalIndication.current, onClickLabel = clickLabel, role = Role.Button, onClick = onClick)
                else Modifier,
            )
            .padding(padding),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        content = content,
    )
}

/** The four step bars (Spec, Test, Code, Review). `at` = the current step (4: all done). */
@Composable
fun StepBars(at: Int, solid: Color, ink: Color, modifier: Modifier = Modifier, big: Boolean = false) {
    Row(
        modifier.fillMaxWidth().semantics(mergeDescendants = true) {
            contentDescription = if (at >= 4) "All steps done" else "Step ${at + 1} of 4: ${STEP_NAMES[at]}"
        },
        horizontalArrangement = Arrangement.spacedBy(if (big) 6.dp else 4.dp),
    ) {
        STEP_NAMES.forEachIndexed { i, n ->
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(if (big) 6.dp else 5.dp)) {
                Box(
                    Modifier.fillMaxWidth().height(4.dp).clip(RoundedCornerShape(2.dp))
                        .background(if (i < at) K.c.ok else if (i == at) solid else K.c.border),
                )
                Text(
                    (if (i == at && big) "◆ " else "") + n,
                    fontSize = if (big) 12.sp else 11.sp,
                    fontWeight = if (i == at) FontWeight.SemiBold else FontWeight.Normal,
                    color = if (i == at) ink else K.c.dim,
                )
            }
        }
    }
}

/** One answer of a single-choice question (a radio row, the whole row is the target). */
@Composable
fun ChoiceRow(
    selected: Boolean,
    label: String,
    description: String?,
    onClick: () -> Unit,
    idleBg: Color = K.c.panel,
    multiple: Boolean = false,
) {
    val shape = RoundedCornerShape(14.dp)
    val fill by animateColorAsState(if (selected) K.c.accentSoft else idleBg, KeelMotion.defaultEffectsSpec(), label = "choice fill")
    val edge by animateColorAsState(if (selected) K.c.accent else K.c.border, KeelMotion.defaultEffectsSpec(), label = "choice edge")
    val sel = if (multiple) Modifier.toggleable(selected, role = Role.Checkbox, onValueChange = { onClick() })
    else Modifier.selectable(selected, role = Role.RadioButton, onClick = onClick)
    Row(
        Modifier.fillMaxWidth().heightIn(min = 52.dp).clip(shape)
            .background(fill)
            .border(1.dp, edge, shape)
            .then(sel)
            .padding(horizontal = 14.dp, vertical = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.Top,
    ) {
        if (multiple) {
            Checkbox(checked = selected, onCheckedChange = null, colors = CheckboxDefaults.colors(checkedColor = K.c.accent, checkmarkColor = K.c.onAccent), modifier = Modifier.size(20.dp))
        } else {
            RadioButton(selected = selected, onClick = null, colors = RadioButtonDefaults.colors(selectedColor = K.c.accent), modifier = Modifier.size(20.dp))
        }
        Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(label, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg)
            if (!description.isNullOrBlank()) Text(description, fontSize = 13.sp, lineHeight = 19.sp, color = K.c.text2)
        }
    }
}

@Composable
fun PrimaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, busy: Boolean = false) {
    val shape = RoundedCornerShape(26.dp)
    val press = remember { MutableInteractionSource() }
    val fill by animateColorAsState(if (enabled) K.c.accent else K.c.accent.copy(alpha = 0.4f), KeelMotion.defaultEffectsSpec(), label = "button fill")
    Box(
        modifier.pressScale(press, 0.97f).fillMaxWidth().height(52.dp).clip(shape)
            .background(fill)
            .clickable(interactionSource = press, indication = LocalIndication.current, enabled = enabled && !busy, role = Role.Button, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        // the label, the spinner while it works, and a new label cross-fade
        AnimatedContent(if (busy) null else text, transitionSpec = { fadeIn(KeelMotion.defaultEffectsSpec()) togetherWith fadeOut(KeelMotion.fastEffectsSpec()) }, label = "button label") { t ->
            if (t == null) CircularProgressIndicator(Modifier.size(22.dp), color = K.c.onAccent, strokeWidth = 2.dp)
            else Text(t, color = K.c.onAccent, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}

/** A pill button (44dp) for secondary actions. */
@Composable
fun PillButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    filled: Boolean = false,
    tinted: Boolean = false,
    enabled: Boolean = true,
    height: Dp = 44.dp,
    corner: Dp = 22.dp,
) {
    val shape = RoundedCornerShape(corner)
    val bg = when { filled -> K.c.accent; tinted -> K.c.checkBg; else -> Color.Transparent }
    val fg = when { filled -> K.c.onAccent; tinted -> K.c.accentInk; else -> K.c.fg }
    val border = when { filled -> null; tinted -> K.c.accentBorder; else -> K.c.border }
    val press = remember { MutableInteractionSource() }
    Box(
        modifier.pressScale(press, 0.95f).heightIn(min = height).clip(shape).background(bg)
            .then(if (border != null) Modifier.border(1.dp, border, shape) else Modifier)
            .clickable(interactionSource = press, indication = LocalIndication.current, enabled = enabled, role = Role.Button, onClick = onClick)
            .padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = if (enabled) fg else K.c.dim, fontSize = 13.sp, fontWeight = if (filled || tinted) FontWeight.SemiBold else FontWeight.Medium, maxLines = 1)
    }
}

/** The 64dp top bar of a detail screen: back, title + mono subtitle, actions. */
@Composable
fun DetailBar(title: String, subtitle: String?, onBack: () -> Unit, backLabel: String, actions: @Composable RowScope.() -> Unit = {}) {
    Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onBack, modifier = Modifier.size(48.dp)) {
            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = backLabel, tint = K.c.fg)
        }
        Column(Modifier.weight(1f).padding(vertical = 4.dp)) {
            Text(title, fontSize = 18.sp, lineHeight = 22.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis, color = K.c.fg)
            if (!subtitle.isNullOrBlank()) Text(subtitle, fontFamily = Mono, fontSize = 12.sp, lineHeight = 15.sp, color = K.c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        DemoChip(Modifier.padding(horizontal = 4.dp))
        actions()
    }
}

@Composable
fun Loading(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(color = K.c.accent, modifier = Modifier.semantics { contentDescription = "Loading" })
    }
}

/** keel could not answer: say so plainly, with keel's hint (or the Wi-Fi advice when it was not reached at all). */
@Composable
fun ErrorState(error: Throwable, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    val k = error as? KeelException
    val title = when {
        k?.unreachable == true -> "keel is not reachable"
        else -> k?.message ?: (error.message ?: "Something went wrong")
    }
    val text = when {
        k?.unreachable == true -> (k.message + "\n" + (k.hint ?: ""))
        else -> k?.hint
    }
    Column(
        modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Spacer(Modifier.height(48.dp))
        Icon(Icons.Outlined.CloudOff, contentDescription = null, tint = K.c.dim, modifier = Modifier.size(40.dp))
        Text(title, fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg, textAlign = TextAlign.Center)
        if (!text.isNullOrBlank()) SelectionContainer {
            Text(text.trim(), fontSize = 14.sp, lineHeight = 20.sp, color = K.c.text2, textAlign = TextAlign.Center)
        }
        PillButton("Try again", onRetry, filled = true)
    }
}

/** An error inside a screen that still shows its data (a send that failed). */
@Composable
fun InlineError(error: Throwable?, onDismiss: (() -> Unit)? = null) {
    Reveal(error) { e -> ErrorBox(e, onDismiss) }
}

@Composable
private fun ErrorBox(error: Throwable, onDismiss: (() -> Unit)?) {
    val k = error as? KeelException
    val shape = RoundedCornerShape(12.dp)
    Row(
        Modifier.fillMaxWidth().clip(shape).background(K.c.badSoft).border(1.dp, K.c.badBorder, shape).padding(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(if (k?.unreachable == true) "keel is not reachable" else (error.message ?: "Failed"), color = K.c.badInk, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            val hint = k?.hint
            if (!hint.isNullOrBlank()) Text(hint, color = K.c.text2, fontSize = 13.sp)
        }
        if (onDismiss != null) TextButton(onClick = onDismiss) { Text("OK", color = K.c.badInk) }
    }
}

@Composable
fun EmptyState(title: String, text: String, modifier: Modifier = Modifier) {
    Column(
        modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        KeelMark(36.dp, 11.dp)
        Spacer(Modifier.height(4.dp))
        Text(title, fontSize = 17.sp, fontWeight = FontWeight.SemiBold, color = K.c.fg, textAlign = TextAlign.Center)
        Text(text, fontSize = 14.sp, lineHeight = 20.sp, color = K.c.text2, textAlign = TextAlign.Center)
    }
}

/** A text box in keel's look: the accent border, the dim placeholder. */
@Composable
fun KTextField(
    value: String,
    onChange: (String) -> Unit,
    label: String?,
    placeholder: String,
    modifier: Modifier = Modifier,
    minLines: Int = 2,
    singleLine: Boolean = false,
    mono: Boolean = false,
) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (label != null) Text(label, fontSize = 13.sp, color = K.c.text2)
        OutlinedTextField(
            value = value,
            onValueChange = onChange,
            modifier = Modifier.fillMaxWidth().semantics { if (label != null) contentDescription = label },
            placeholder = { Text(placeholder, color = K.c.dim) },
            minLines = if (singleLine) 1 else minLines,
            singleLine = singleLine,
            shape = RoundedCornerShape(12.dp),
            textStyle = MaterialTheme.typography.bodyLarge.copy(color = K.c.fg, fontFamily = if (mono) Mono else null),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = K.c.accent, unfocusedBorderColor = K.c.accentBorder, cursorColor = K.c.accent,
                focusedContainerColor = K.c.bg, unfocusedContainerColor = K.c.bg,
            ),
        )
    }
}

/** A round avatar with initials. */
@Composable
fun Avatar(text: String, bg: Color, fg: Color, size: Dp = 36.dp) {
    Box(Modifier.size(size).clip(CircleShape).background(bg), contentAlignment = Alignment.Center) {
        Text(text, color = fg, fontSize = (size.value * 0.36f).sp, fontWeight = FontWeight.SemiBold)
    }
}

/** A coloured dot. */
@Composable
fun Dot(color: Color, size: Dp = 10.dp) {
    Box(Modifier.size(size).clip(CircleShape).background(color))
}

/** A monospace block (a failure, a tool log). */
@Composable
fun CodeBlock(text: String, fg: Color = K.c.text2, border: Color = K.c.border, bg: Color = K.c.bg) {
    val shape = RoundedCornerShape(10.dp)
    SelectionContainer {
        Text(
            text, fontFamily = Mono, fontSize = 12.sp, lineHeight = 18.sp, color = fg,
            modifier = Modifier.fillMaxWidth().clip(shape).background(bg).border(BorderStroke(1.dp, border), shape).padding(horizontal = 12.dp, vertical = 10.dp),
        )
    }
}

/** The segmented control of the PRs and KeelBot screens (40dp, rounded, the picked one tinted). */
@Composable
fun Segmented(options: List<Pair<String, String>>, picked: String, onPick: (String) -> Unit, modifier: Modifier = Modifier, height: Dp = 40.dp) {
    val shape = RoundedCornerShape(height / 2)
    Row(modifier.height(height).clip(shape).border(1.dp, K.c.border, shape)) {
        options.forEachIndexed { i, (id, label) ->
            val on = id == picked
            val fill by animateColorAsState(if (on) K.c.accentSoft else K.c.accentSoft.copy(alpha = 0f), KeelMotion.defaultEffectsSpec(), label = "segment")
            if (i > 0) Box(Modifier.width(1.dp).fillMaxHeight().background(K.c.border))
            Box(
                Modifier.weight(1f).fillMaxHeight().background(fill)
                    .selectable(on, role = Role.Tab, onClick = { onPick(id) }),
                contentAlignment = Alignment.Center,
            ) {
                Text(label, color = if (on) K.c.accentInk else K.c.text2, fontSize = 13.sp, fontWeight = if (on) FontWeight.SemiBold else FontWeight.Medium)
            }
        }
    }
}


/** A filter chip of the Flows screen (36dp). */
@Composable
fun FilterChipK(label: String, on: Boolean, onClick: () -> Unit) {
    val shape = RoundedCornerShape(8.dp)
    val fill by animateColorAsState(if (on) K.c.accentSoft else K.c.accentSoft.copy(alpha = 0f), KeelMotion.defaultEffectsSpec(), label = "chip fill")
    val edge by animateColorAsState(if (on) K.c.accent else K.c.border, KeelMotion.defaultEffectsSpec(), label = "chip edge")
    Box(
        Modifier.height(48.dp).padding(vertical = 6.dp).clip(shape)
            .background(fill)
            .border(1.dp, edge, shape)
            .selectable(on, role = Role.Tab, onClick = onClick)
            .padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) { Text(label, color = if (on) K.c.accentInk else K.c.text2, fontSize = 13.sp, fontWeight = FontWeight.Medium) }
}

/** Sinks a little while pressed and springs back (Material 3 expressive): the finger feels the press land. */
fun Modifier.pressScale(source: MutableInteractionSource, pressed: Float = 0.97f): Modifier = composed {
    val down by source.collectIsPressedAsState()
    val scale by animateFloatAsState(if (down) pressed else 1f, KeelMotion.fastSpatialSpec(), label = "press")
    graphicsLayer { scaleX = scale; scaleY = scale }
}

/** Shows [value] while it is there: it fades in and opens its room, and when it goes it fades and closes, still showing
 *  what it showed last (a banner, keel's check, an error). */
@Composable
fun <T : Any> Reveal(value: T?, modifier: Modifier = Modifier, content: @Composable (T) -> Unit) {
    val last = remember { arrayOfNulls<Any>(1) }
    if (value != null) last[0] = value
    AnimatedVisibility(value != null, modifier, enter = KeelMotion.reveal, exit = KeelMotion.hide, label = "reveal") {
        @Suppress("UNCHECKED_CAST")
        (value ?: last[0] as T?)?.let { content(it) }
    }
}

/** A screen's states (loading, an error, nothing, its content) cross-fade instead of jumping. [phase] names the state;
 *  [content] gets the screen's state as it was for that phase, so a leaving error still has its error. */
@Composable
fun <T> Phases(state: T, phase: (T) -> Any, modifier: Modifier = Modifier, content: @Composable (T) -> Unit) {
    AnimatedContent(
        state, modifier,
        transitionSpec = { fadeIn(KeelMotion.slowEffectsSpec()) togetherWith fadeOut(KeelMotion.fastEffectsSpec()) },
        contentKey = phase, label = "phases",
    ) { content(it) }
}
