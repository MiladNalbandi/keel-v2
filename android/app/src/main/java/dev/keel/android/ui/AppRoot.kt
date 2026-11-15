package dev.keel.android.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.CallMerge
import androidx.compose.material.icons.outlined.AccountTree
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Inbox
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.NamedNavArgument
import androidx.navigation.NavBackStackEntry
import androidx.navigation.NavGraphBuilder
import androidx.compose.animation.AnimatedContentScope
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import dev.keel.android.KeelApp
import dev.keel.android.ui.chat.ChatScreen
import dev.keel.android.ui.chat.ChatViewModel
import dev.keel.android.ui.common.HomeViewModel
import dev.keel.android.ui.flow.FlowScreen
import dev.keel.android.ui.flows.FlowsScreen
import dev.keel.android.ui.flows.FlowsViewModel
import dev.keel.android.ui.inbox.InboxScreen
import dev.keel.android.ui.inbox.InboxViewModel
import dev.keel.android.ui.pair.PairScreen
import dev.keel.android.ui.pair.PairViewModel
import dev.keel.android.ui.prs.PrsScreen
import dev.keel.android.ui.prs.PrsViewModel
import dev.keel.android.ui.question.FlowViewModel
import dev.keel.android.ui.question.QuestionScreen
import dev.keel.android.ui.question.QuestionViewModel
import dev.keel.android.ui.settings.SettingsScreen
import dev.keel.android.ui.common.LocalDemo
import dev.keel.android.ui.thread.DiffScreen
import dev.keel.android.ui.thread.ThreadScreen
import dev.keel.android.ui.thread.ThreadViewModel
import dev.keel.android.ui.theme.K
import dev.keel.android.ui.theme.KeelMotion
import dev.keel.android.ui.theme.enterScreen
import dev.keel.android.ui.theme.exitScreen
import dev.keel.android.ui.theme.popEnterScreen
import dev.keel.android.ui.theme.popExitScreen
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith

private data class Tab(val route: String, val label: String, val icon: ImageVector)

private val TABS = listOf(
    Tab("inbox", "Inbox", Icons.Outlined.Inbox),
    Tab("flows", "Flows", Icons.Outlined.AccountTree),
    Tab("prs", "PRs", Icons.AutoMirrored.Outlined.CallMerge),
    Tab("chat", "KeelBot", Icons.Outlined.ChatBubbleOutline),
)

/** Routes: pair, the four tabs, and the detail screens. */
object Routes {
    fun question(pid: String, tid: String) = "question/${enc(pid)}/${enc(tid)}"
    fun flow(pid: String, tid: String) = "flow/${enc(pid)}/${enc(tid)}"
    fun pr(pid: String, n: Int) = "pr/${enc(pid)}/$n"
    fun file(pid: String, n: Int, path: String) = "pr/${enc(pid)}/$n/file?path=${enc(path)}"
    private fun enc(s: String) = android.net.Uri.encode(s)
}

@Composable
fun AppRoot(app: KeelApp, link: String?, onLinkUsed: () -> Unit, open: String?, onOpenUsed: () -> Unit) {
    val session = app.session
    val nav = rememberNavController()
    val active by session.active.collectAsState()
    val pairVm: PairViewModel = viewModel(factory = viewModelFactory { initializer { PairViewModel(session) } })
    val home: HomeViewModel = viewModel(factory = viewModelFactory { initializer { HomeViewModel(session, app.live, app.notifier) } })
    val homeUi by home.ui.collectAsStateWithLifecycle()
    val pairUi by pairVm.ui.collectAsStateWithLifecycle()
    val start = remember { if (session.active.value == null) "pair" else "inbox" }

    // notifications: ask once, after pairing (Android 13+)
    val askNotify = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LaunchedEffect(active?.keel?.id) {
        if (active != null && Build.VERSION.SDK_INT >= 33 && !app.notifier.allowed() && active?.keel?.demo != true) askNotify.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    // a keel://pair link (camera app, browser): the Pair screen pairs with it
    LaunchedEffect(link) {
        if (link != null) {
            nav.navigate("pair") { launchSingleTop = true }
            pairVm.fromLink(link)
            onLinkUsed()
        }
    }
    LaunchedEffect(pairUi.paired) {
        if (pairUi.paired) {
            nav.navigate("inbox") { popUpTo(0) { inclusive = true } }
            pairVm.consumed()
        }
    }
    // keel removed this phone (403 "This device was removed"): back to the Pair screen
    LaunchedEffect(Unit) {
        session.removed.collect { nav.navigate("pair") { popUpTo(0) { inclusive = true } } }
    }
    LaunchedEffect(open, active?.keel?.id) {
        val o = open ?: return@LaunchedEffect
        if (active == null) return@LaunchedEffect
        openRoute(nav, o)
        onOpenUsed()
    }

    val entry by nav.currentBackStackEntryAsState()
    val route = entry?.destination?.route
    // KeelBot is a tab, but a chat takes the whole screen: no bottom bar there (its back arrow returns)
    val showBar = route != "chat" && TABS.any { it.route == route }

    CompositionLocalProvider(LocalDemo provides (active?.keel?.demo == true)) {
    Scaffold(
        containerColor = K.c.bg,
        contentWindowInsets = WindowInsets(0),
        bottomBar = {
            AnimatedVisibility(
                showBar,
                enter = expandVertically(KeelMotion.size, expandFrom = Alignment.Top) + fadeIn(KeelMotion.defaultEffectsSpec()),
                exit = shrinkVertically(KeelMotion.size, shrinkTowards = Alignment.Top) + fadeOut(KeelMotion.fastEffectsSpec()),
                label = "bottom bar",
            ) {
                BottomBar(route, homeUi.inboxCount, homeUi.reviewCount) { r ->
                    nav.navigate(r) {
                        popUpTo("inbox") { saveState = true }
                        launchSingleTop = true
                        restoreState = true
                    }
                }
            }
        },
    ) { pad ->
        Box(
            // the bar's height is consumed as insets: under the bar no extra room, without it the system bar's (and in between
            // what is left of it, so nothing jumps while the bar slides)
            Modifier.fillMaxSize().background(K.c.bg).padding(pad).consumeWindowInsets(pad).statusBarsPadding()
                .navigationBarsPadding().imePadding(),
        ) {
            NavHost(
                nav, startDestination = start,
                enterTransition = { enterScreen() }, exitTransition = { exitScreen() },
                popEnterTransition = { popEnterScreen() }, popExitTransition = { popExitScreen() },
            ) {
                screen("pair") {
                    PairScreen(pairVm, saved = session.store.keels.collectAsState().value, onBack = if (active != null) ({ nav.popBackStack() }) else null)
                }
                screen("inbox") {
                    val vm: InboxViewModel = viewModel(factory = viewModelFactory { initializer { InboxViewModel(session, app.live) } })
                    InboxScreen(vm, home, onQuestion = { pid, tid -> nav.navigate(Routes.question(pid, tid)) }, onFlow = { pid, tid -> nav.navigate(Routes.flow(pid, tid)) },
                        onPr = { n -> homeUi.project?.let { nav.navigate(Routes.pr(it, n)) } }, onSettings = { nav.navigate("settings") })
                }
                screen("flows") {
                    val vm: FlowsViewModel = viewModel(factory = viewModelFactory { initializer { FlowsViewModel(session, app.live) } })
                    FlowsScreen(vm, onOpen = { r -> homeUi.project?.let { pid -> nav.navigate(if (r.status == "waiting") Routes.question(pid, r.threadId) else Routes.flow(pid, r.threadId)) } })
                }
                screen("prs") {
                    val vm: PrsViewModel = viewModel(factory = viewModelFactory { initializer { PrsViewModel(session, app.live) } })
                    PrsScreen(vm, onOpen = { n -> homeUi.project?.let { nav.navigate(Routes.pr(it, n)) } })
                }
                screen("chat") {
                    val vm: ChatViewModel = viewModel(factory = viewModelFactory { initializer { ChatViewModel(session, app.live) } })
                    ChatScreen(vm, onBack = { if (!nav.popBackStack()) nav.navigate("inbox") }, onStartFlow = { nav.navigate("flows") })
                }
                screen(
                    "question/{pid}/{tid}",
                    arguments = listOf(navArgument("pid") { type = NavType.StringType }, navArgument("tid") { type = NavType.StringType }),
                ) { e ->
                    val pid = e.arguments?.getString("pid").orEmpty()
                    val tid = e.arguments?.getString("tid").orEmpty()
                    val vm: QuestionViewModel = viewModel(factory = viewModelFactory { initializer { QuestionViewModel(session, app.live, pid, tid) } })
                    QuestionScreen(vm, onBack = { nav.popBackStack() }, onFlow = { nav.navigate(Routes.flow(pid, tid)) })
                }
                screen(
                    "flow/{pid}/{tid}",
                    arguments = listOf(navArgument("pid") { type = NavType.StringType }, navArgument("tid") { type = NavType.StringType }),
                ) { e ->
                    val pid = e.arguments?.getString("pid").orEmpty()
                    val tid = e.arguments?.getString("tid").orEmpty()
                    val vm: FlowViewModel = viewModel(factory = viewModelFactory { initializer { FlowViewModel(session, app.live, pid, tid, withHistory = true) } })
                    FlowScreen(vm, onBack = { nav.popBackStack() })
                }
                screen(
                    "pr/{pid}/{n}",
                    arguments = listOf(navArgument("pid") { type = NavType.StringType }, navArgument("n") { type = NavType.IntType }),
                ) { e ->
                    val pid = e.arguments?.getString("pid").orEmpty()
                    val n = e.arguments?.getInt("n") ?: 0
                    val vm: ThreadViewModel = viewModel(factory = viewModelFactory { initializer { ThreadViewModel(session, app.live, pid, n) } })
                    ThreadScreen(vm, onBack = { nav.popBackStack() }, onFile = { path -> nav.navigate(Routes.file(pid, n, path)) })
                }
                screen(
                    "pr/{pid}/{n}/file?path={path}",
                    arguments = listOf(
                        navArgument("pid") { type = NavType.StringType }, navArgument("n") { type = NavType.IntType },
                        navArgument("path") { type = NavType.StringType; defaultValue = "" },
                    ),
                ) { e ->
                    val pid = e.arguments?.getString("pid").orEmpty()
                    val n = e.arguments?.getInt("n") ?: 0
                    val path = e.arguments?.getString("path").orEmpty()
                    // the pull request's own view model (threads, replies, keel's check), shared with the PR screen
                    val owner = remember(e) { runCatching { nav.getBackStackEntry("pr/{pid}/{n}") }.getOrNull() }
                    val vm: ThreadViewModel = if (owner != null) viewModel(owner, factory = viewModelFactory { initializer { ThreadViewModel(session, app.live, pid, n) } })
                    else viewModel(factory = viewModelFactory { initializer { ThreadViewModel(session, app.live, pid, n) } })
                    DiffScreen(vm, path, onBack = { nav.popBackStack() })
                }
                screen("settings") {
                    SettingsScreen(session, home, notifier = app.notifier, onBack = { nav.popBackStack() }, onPair = { nav.navigate("pair") })
                }
            }
        }
    }
    }
}

/** A destination on keel's own background: while two screens move (a push, a back gesture) one never shows through the
 *  other as text over text. */
private fun NavGraphBuilder.screen(
    route: String,
    arguments: List<NamedNavArgument> = emptyList(),
    content: @Composable AnimatedContentScope.(NavBackStackEntry) -> Unit,
) = composable(route, arguments) { e ->
    val scope = this
    Box(Modifier.fillMaxSize().background(K.c.bg)) { scope.content(e) }
}

/** "inbox", "flows", "prs", "chat", "question:<pid>:<tid>", "flow:<pid>:<tid>", "pr:<pid>:<n>" (notifications, adb). */
private fun openRoute(nav: NavHostController, o: String) {
    val p = o.split(':')
    val r = when (p[0]) {
        "question" -> if (p.size >= 3) Routes.question(p[1], p[2]) else null
        "flow" -> if (p.size >= 3) Routes.flow(p[1], p[2]) else null
        "pr" -> if (p.size >= 3) p[2].toIntOrNull()?.let { Routes.pr(p[1], it) } else null
        "inbox", "flows", "prs", "chat", "settings" -> p[0]
        else -> null
    } ?: return
    nav.navigate(r) { launchSingleTop = true }
}

/** The compact bottom bar: 64dp + the system navigation bar's inset (once), 24dp icons, 12sp labels, a 56×28 pill. */
@Composable
private fun BottomBar(route: String?, inbox: Int, reviews: Int, go: (String) -> Unit) {
    Column(Modifier.fillMaxWidth().background(K.c.panel)) {
        HorizontalDivider(color = K.c.border)
        Row(Modifier.fillMaxWidth().navigationBarsPadding().height(64.dp)) {
            TABS.forEach { t ->
                val on = route == t.route
                val badge = when (t.route) { "inbox" -> inbox; "prs" -> reviews; else -> 0 }
                Column(
                    Modifier.weight(1f).fillMaxHeight()
                        .selectable(on, role = Role.Tab, onClick = { go(t.route) })
                        .semantics { contentDescription = if (badge > 0) "${t.label}, $badge waiting" else t.label },
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    val pill by animateDpAsState(if (on) 56.dp else 24.dp, KeelMotion.fastSpatialSpec(), label = "tab pill")
                    val pillColor by animateColorAsState(if (on) K.c.accentSoft else K.c.accentSoft.copy(alpha = 0f), KeelMotion.defaultEffectsSpec(), label = "tab pill colour")
                    val ink by animateColorAsState(if (on) K.c.accentInk else K.c.dim, KeelMotion.defaultEffectsSpec(), label = "tab icon")
                    val word by animateColorAsState(if (on) K.c.fg else K.c.dim, KeelMotion.defaultEffectsSpec(), label = "tab label")
                    Box(Modifier.size(56.dp, 28.dp), contentAlignment = Alignment.Center) {
                        Box(Modifier.size(pill, 28.dp).clip(RoundedCornerShape(14.dp)).background(pillColor))
                        Icon(t.icon, contentDescription = null, tint = ink, modifier = Modifier.size(24.dp))
                        androidx.compose.animation.AnimatedVisibility(
                            badge > 0, Modifier.align(Alignment.TopEnd).padding(end = 8.dp),
                            enter = scaleIn(KeelMotion.fastSpatialSpec()) + fadeIn(KeelMotion.fastEffectsSpec()),
                            exit = scaleOut(KeelMotion.fastEffectsSpec()) + fadeOut(KeelMotion.fastEffectsSpec()),
                            label = "badge",
                        ) {
                            Box(
                                Modifier.heightIn(min = 14.dp).widthIn(min = 14.dp).clip(RoundedCornerShape(7.dp))
                                    .background(if (t.route == "prs") K.c.run else K.c.bad).padding(horizontal = 3.dp),
                                contentAlignment = Alignment.Center,
                            ) {
                                // a new count rolls in from below (or from above when it goes down)
                                AnimatedContent(
                                    badge,
                                    transitionSpec = {
                                        val up = targetState > initialState
                                        (slideInVertically(KeelMotion.fastSpatialSpec()) { h -> if (up) h else -h } + fadeIn(KeelMotion.fastEffectsSpec())) togetherWith
                                            (slideOutVertically(KeelMotion.fastSpatialSpec()) { h -> if (up) -h else h } + fadeOut(KeelMotion.fastEffectsSpec()))
                                    },
                                    label = "badge count",
                                ) { n -> Text(if (n > 99) "99+" else "$n", color = K.c.onAccent, fontSize = 9.sp, lineHeight = 10.sp, fontWeight = FontWeight.Bold) }
                            }
                        }
                    }
                    Text(t.label, fontSize = 12.sp, lineHeight = 14.sp, maxLines = 1, color = word,
                        fontWeight = if (on) FontWeight.SemiBold else FontWeight.Medium, modifier = Modifier.padding(top = 4.dp))
                }
            }
        }
    }
}
