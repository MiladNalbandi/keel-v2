package dev.keel.android.ui.theme

import androidx.compose.animation.AnimatedContentTransitionScope
import androidx.compose.animation.EnterTransition
import androidx.compose.animation.ExitTransition
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.FiniteAnimationSpec
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.VisibilityThreshold
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.ui.Alignment
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.navigation.NavBackStackEntry

/**
 * keel's motion (#113), Material 3's expressive motion scheme (its spring values): springs for what moves and grows
 * (spatial: it may bounce a little), springs that never bounce for colour and fades (effects). The system's animator
 * duration scale ("Remove animations") scales all of it, down to none.
 */
object KeelMotion {
    fun <T> fastSpatialSpec(): FiniteAnimationSpec<T> = spring(dampingRatio = 0.6f, stiffness = 800f)
    fun <T> defaultSpatialSpec(): FiniteAnimationSpec<T> = spring(dampingRatio = 0.8f, stiffness = 380f)
    fun <T> slowSpatialSpec(): FiniteAnimationSpec<T> = spring(dampingRatio = 0.8f, stiffness = 200f)
    fun <T> fastEffectsSpec(): FiniteAnimationSpec<T> = spring(dampingRatio = 1f, stiffness = 3800f)
    fun <T> defaultEffectsSpec(): FiniteAnimationSpec<T> = spring(dampingRatio = 1f, stiffness = 1600f)
    fun <T> slowEffectsSpec(): FiniteAnimationSpec<T> = spring(dampingRatio = 1f, stiffness = 800f)

    /** A card or a box that grows or shrinks with its content: no bounce, so text never wobbles. */
    val size: FiniteAnimationSpec<IntSize> = spring(dampingRatio = Spring.DampingRatioNoBouncy, stiffness = 600f, visibilityThreshold = IntSize.VisibilityThreshold)

    /** Something that appears in a screen (a banner, keel's check, an error): it fades in and opens its room. */
    val reveal: EnterTransition =
        fadeIn(spring(stiffness = 1600f)) + expandVertically(size, expandFrom = Alignment.Top)
    val hide: ExitTransition =
        fadeOut(spring(stiffness = 3800f)) + shrinkVertically(size, shrinkTowards = Alignment.Top)

    // ---- screens. Tweens, not springs: the predictive back gesture seeks through a screen change by the finger's
    // progress, and every part of it must keep pace with the finger. A screen is opaque (AppRoot's screen()): the one on
    // top covers the one below, so two screens never show through each other.

    private val emphasizedDecelerate = CubicBezierEasing(0.05f, 0.7f, 0.1f, 1f)
    private const val PUSH_MS = 380
    private const val POP_MS = 320

    private val pushSlide = tween<IntOffset>(PUSH_MS, easing = emphasizedDecelerate)
    // back follows the finger: an easing close to it, not one that runs ahead
    private val popSlide = tween<IntOffset>(POP_MS, easing = FastOutSlowInEasing)

    /** A screen opens on top: it slides in from the right edge, and the one below moves a little left and dims. */
    val pushIn: EnterTransition = slideInHorizontally(pushSlide) { it }
    val pushOut: ExitTransition = slideOutHorizontally(pushSlide) { -it / 4 } + fadeOut(tween(PUSH_MS, easing = LinearEasing), targetAlpha = 0.5f)

    /** Back (also the back gesture, step by step): the top screen leaves to the right, the one below comes back. */
    val popIn: EnterTransition = slideInHorizontally(popSlide) { -it / 4 } + fadeIn(tween(POP_MS, easing = LinearEasing), initialAlpha = 0.5f)
    val popOut: ExitTransition = slideOutHorizontally(popSlide) { it }

    /** Between the tabs (fade through): the old one fades out quickly, the new one fades in and grows a little. */
    val tabIn: EnterTransition = fadeIn(tween(210, delayMillis = 90, easing = LinearEasing)) +
        scaleIn(tween(210, delayMillis = 90, easing = emphasizedDecelerate), initialScale = 0.96f)
    val tabOut: ExitTransition = fadeOut(tween(90, easing = LinearEasing))
}

/** The screens a person moves between with the bottom bar (and Pair, which leads to them): a change between two of them
 *  is a fade through, any other one a push or a back. */
private val TOP_LEVEL = setOf("pair", "inbox", "flows", "prs", "chat")

private fun AnimatedContentTransitionScope<NavBackStackEntry>.betweenTabs() =
    initialState.destination.route in TOP_LEVEL && targetState.destination.route in TOP_LEVEL

fun AnimatedContentTransitionScope<NavBackStackEntry>.enterScreen(): EnterTransition = if (betweenTabs()) KeelMotion.tabIn else KeelMotion.pushIn
fun AnimatedContentTransitionScope<NavBackStackEntry>.exitScreen(): ExitTransition = if (betweenTabs()) KeelMotion.tabOut else KeelMotion.pushOut
fun AnimatedContentTransitionScope<NavBackStackEntry>.popEnterScreen(): EnterTransition = if (betweenTabs()) KeelMotion.tabIn else KeelMotion.popIn
fun AnimatedContentTransitionScope<NavBackStackEntry>.popExitScreen(): ExitTransition = if (betweenTabs()) KeelMotion.tabOut else KeelMotion.popOut
