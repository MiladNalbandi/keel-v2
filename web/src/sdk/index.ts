// @keel/web-sdk: what a plugin's web pages may use from keel's web. Core bundles it once and shares it on
// window.__keel; a plugin's build keeps `import … from "@keel/web-sdk"` as a bare import, and the import map points it
// to a small shim that reads window.__keel.sdk (tools/sdkShims.ts).
//
// Name every export here (no `export *`): the build lists these names to write the shim. Removing or changing one
// breaks the plugins that use it.

// the plugin itself
export { definePlugin } from "./plugin";
export type { AddonPageProps, AddonWeb, KeelSdk } from "./plugin";

// keel's api: the same transport as keel's own pages (/api + path, errors as ApiError)
export {
  ApiError,
  del,
  errorParts,
  get,
  getText,
  patch,
  post,
  put,
} from "../api";
export type {
  ChoiceInfo,
  ClarifyQuestion,
  Estimate,
  KeelMap,
  MapLevel,
  MapNode,
  MapResponse,
  McpServer,
  Notes,
  ProjectSettings,
  RunMode,
  Step,
  SpecFlow,
  SpecState,
  SpecStatus,
  WikiItem,
  WikiPage,
  WikiTree,
  Workflow,
} from "../api";
// the Inbox's items (a plugin's inbox.card shows its own kinds)
export type { InboxItem, InboxTask } from "../inboxApi";

// data loading (live: loads again on every server event tick), and the app's state (the chosen project, toast)
export { useApp, useLoad } from "../state";
export type { Loaded } from "../state";
// 0.18.42 small values a page keeps in the browser (the open panel, a filter, an unsent draft, a scroll position), so
// it looks the same when you come back to it
export {
  capStored,
  dropStored,
  readStored,
  storedKeys,
  useDraft,
  useStored,
  useStoredScroll,
  writeStored,
} from "../storage";
export type { StoredOptions, StoreKind } from "../storage";

// links to screens, keel's own and the add-ons' (#/initiatives/INI-3); the link the page shows now (#/wiki/kb:domain
// is { page: "wiki", arg: "kb:domain" }), go(screen, arg) to open another, useRoute() to read the link
export { hashForScreen, settingsHash } from "../routes";
export { go, useRoute } from "../state";
// keel 0.18.6 the shared focus: the project and KeelBot chat every keel front end shows (api ui/UiFocus.kt)
export { shareFocus, UI_FOCUS_EVENT, FOCUS_CLIENT, type UiFocus } from "../state";
export type { Route } from "../routes";
// agent names that changed (0.18.38: the review agents): an old name a setting still holds gives the agent's new name
export { agentName } from "../agentNames";

// keel's small ui pieces
export {
  Async,
  Confirm,
  Drawer,
  Empty,
  ErrorBox,
  Loading,
  PageHead,
  Panel,
  Pill,
  Since,
  Tabs,
  Ticks,
} from "../components/ui";
export type { PillTone } from "../components/ui";
export {
  EmptyState,
  SearchBox,
  Section,
  Skeleton,
  useNarrow,
} from "../components/page";
export { CiteLinks, Markdown } from "../components/Markdown";
// keel 0.18.57 the Settings page's small pieces (a plugin's settings tab or connection uses the same look)
export { Fold, Knob, Note, RadioCards, Seg, Steps, Switch, SwitchRow, Tile, TonePill } from "../components/settingsUi";
export type { StepItem, StepState, Tone } from "../components/settingsUi";
// 0.18.57 the project notes are opt-in: "Project notes are off — Build them" (Wiki, Map)
export { NOTE_SECTIONS, ProjectNotesOff, notesCostLabel, setNotes } from "../components/ProjectNotes";
// a code block with keel's highlighting (the Database plugin's KeelBot card shows its SQL with it)
export { CodeBlock } from "../components/Code";
// keel 0.18.9 a failed button's "Ask KeelBot to fix it" (KeelBot's chat provides AskKeelBot)
export { AskFixButton, AskKeelBot, fixRequest as askFixRequest } from "../components/AskFix";
export type { AskOptions } from "../components/AskFix";
// keel 0.18.63 the bar over selected code: Search, Ask KeelBot, Review (the Code page and the Review page)
export { SelectionBar, searchTextOf, selectionIn, selectionReviewRequest } from "../components/SelectionBar";
export type { SelectionAction } from "../components/SelectionBar";
export { ClarifyForm, answersOf } from "../components/ClarifyForm";
export type { ClarifyAnswers } from "../components/ClarifyForm";
// keel 0.18.13 one way to ask: questions read, answered and written back the same way everywhere; a gate's named choices
export { ANSWERS_PREFIX, answersMessage, byRecommended, choiceProblem, choiceRows, defaultPick, parseQuestions, pickedMode, readAnswersMessage } from "../questions";
export { ChoiceForm } from "../components/ChoiceForm";
export { clock, kfmt } from "../format";
export { agoText } from "../components/UsageStrip";
// starting a flow: the run mode picker, and the Doctor that cleans the project folder when a start is refused
export { RunModePicker } from "../components/RunMode";
export { WorkspaceDoctor } from "../components/WorkspaceDoctor";
// keel 0.18.55 a project keel does not know yet (no .keel/config.yml): the ask before a flow starts on it (Run init
// first / Start anyway), the test, and starting init (the Tasks Start box asks it)
export { InitAsk, isInitFlow, needsInit, startInit, useStartInit } from "../components/InitAsk";
// 0.18.57 the flows a request can be sized into (one line each), and "only test code changes" (the address flow): the
// Tasks plugin's "keel suggests" card names the flow and its steps with them
export { SIZE_FLOWS, testOnlyFiles } from "../flowSize";

// keel's workflow pictures (the Flow page and the builder draw with them; the Wiki plugin shows a workflow read only):
// the blocks, the step table, the graph with zoom, the Blocks / Table / Graph choice, the tokens per step, and the
// "what this step does" drawer
export {
  Blocks,
  BlocksLegend,
  StepTable,
  useMapView,
  ViewToggle,
} from "../components/Blocks";
export { Graph, GraphLegend } from "../components/Graph";
export { Zoom } from "../components/Zoom";
export { tokensByStep } from "../components/workflow";
export { StepInfoDrawer } from "../components/StepInfo";

// the knowledge base: "Refresh stale" starts the knowledge-refresh flow for the stale sections (the Wiki plugin and
// the Code page's keel view show it)
export { RefreshStaleButton } from "../components/RefreshStale";

// keel's diagram canvas (components/er): the boxes of a map level and the database diagram (the Map plugin draws
// them; the Graph plugin uses the same canvas)
export { BoxDiagram, moduleBoxes, systemBoxes } from "../components/er/BoxDiagram";
export type { GBox, GEdge } from "../components/er/BoxDiagram";
export { ErDiagram } from "../components/er/ErDiagram";
export { attr, schemaOf } from "../components/er/model";
// the canvas itself (pan, zoom, fit), its icons, a file:line link to the Code page, and the elbow lines between boxes
// (the Graph plugin draws one symbol with who uses it and what it uses with them)
export { Canvas } from "../components/er/Canvas";
export type { CanvasHandle } from "../components/er/Canvas";
export { Ic, IconDefs } from "../components/er/icons";
export { fileHref } from "../components/er/Structure";
export type { Pt } from "../components/er/layout";
export { elbow, endMark, pathD } from "../components/er/route";
export type { Rect } from "../components/er/route";
// keel 0.18.57 Mermaid text drawn as SVG (its own chunk, loaded on first use; the Code Review overview's Database and
// System tabs), the page's light or dark, and keel's one way to copy text
export { pageIsDark, renderMermaid } from "../components/mermaid";
export { copyText } from "../clipboard";

// extension points (step 2, docs/plugins/09-step2-contract.md §5): pages for the menu and the router, and slots — named
// places in keel's pages where a part puts a piece (live: a page renders again when a piece registers)
export {
  allPages,
  hasPage,
  pageFor,
  pageOf,
  registerPage,
  registerSlot,
  slotItems,
  useHasPage,
  usePages,
  useSlot,
} from "./registry";
export type { PageProps, PageRegistration, SlotItem } from "./registry";
export { SLOTS } from "./slots";
export type {
  AssistantItem,
  AssistantProps,
  CodeActivityItem,
  CodeActivityProps,
  CodeOpen,
  CodeOpenSpec,
  CodeTabItem,
  CodeTabProps,
  CodeTabRef,
  CodeView,
  ConnectionKindItem,
  InboxCardItem,
  InboxCardProps,
  JobsTabItem,
  KeelbotBlock,
  KeelbotCardItem,
  KeelbotCardProps,
  KeysAreaItem,
  LauncherSourceItem,
  MapErQueryItem,
  NavBadgeItem,
  NotesSettingItem,
  ScmPanelParts,
  ScmPanelProps,
  SettingKind,
  SettingRow,
  SettingsBrowserItem,
  SettingsSectionItem,
  SettingsSectionProps,
  SettingsTabItem,
  ShellWatchItem,
  ToolsCardItem,
  WorkflowActionsItem,
} from "./slots";

// "ask KeelBot" without importing it: the assistant's part listens
export { ASK_ASSISTANT_EVENT, askAssistant } from "./assistant";
export type { AskAssistantDetail } from "./assistant";

// the launcher (⌘K): a launcher.source's results and their actions (open a link, ask KeelBot, copy)
export {
  askAction,
  copyAction,
  goHash,
  linkAction,
} from "../components/launcher/sources";
export type { Ctx as LauncherCtx } from "../components/launcher/sources";
export type { Item as LauncherItem } from "../components/launcher/model";

// code and diffs, and keys (the Code Review plugin's diff, file tree and keys use them): a unified diff's rows (and side
// by side), the syntax colours, the Code page's file and view icons, keel's key chords and the keymap a person picked
// (IntelliJ or VS Code; src/keys.ts), and the api's commit and diff types
export { parseDiff, splitRows } from "../components/Code";
export type { DiffRow, SplitRow } from "../components/Code";
export { highlight, languageOf, loadLanguage, useLanguage } from "../components/highlight";
export { FileIcon, Icon } from "../components/icons";
export { keyLabel, matches, readKeymap, saveKeymap } from "../keys";
export type { Keymap } from "../keys";
export type { Commit, FileDiff } from "../api";

// the Code page (a plugin since step 3, plugins/code): what it shares with keel and the other parts. Fuzzy file
// matching (quick open; KeelBot's @ mentions rank files with it too), the rest of keel's file icons, the code renderer's
// lines, keel's keys (⇧⇧ opens the launcher), a link read like the router reads it, plural words, and the core api's
// types it shows (the code graph index, keel's files and Memory, the lines a person asks the assistant about)
export { fuzzy, rankFiles } from "../components/fuzzy";
export type { Fuzzy } from "../components/fuzzy";
export { Chevron, extOf, FolderIcon, isImage, languageName } from "../components/icons";
export { linesOf } from "../components/Code";
export { openLauncher } from "../components/launcher/open";
export { doubleShift, isMac } from "../keys";
export { parseHash } from "../routes";
export { plural } from "../format";
export type { Fact, FactKind, GraphHit, HelperSelection, IndexStatus, KeelDoc, Memory } from "../api";
// KeelBot's chat (plugins/keelbot): the model picker and its labels, an answer's steps as they stream in, a change's
// diff with its fold, the @file ranking (above), file:line links in answers, and the api's types of its sessions, of the
// flow it follows and of the buttons it gives
export { ModelPicker } from "../components/ModelPicker";
export { codeHref, fileLink, modelLabel, provLabel } from "../format";
export { mergeSteps } from "../components/StepFeed";
export { StepView } from "../components/StepView";
export { DiffView, FoldedText } from "../components/Code";
export { sourceRef } from "../api";
export type {
  AnswerSource, CiRun, FlowView, HelperChange, HelperCommand, HelperDone, HelperHandover, HelperMention, HelperMessage, HelperMode,
  HelperQuestion, HelperSession, HelperTurnStarted, JobStep, Model, ThreadState, WorkflowCheck,
} from "../api";
// 0.15.2–0.15.4 (main's releases, ported to the plugin track): keel's keys for the parts (Focus mode's key and event, the
// cheat sheet's rows, "is the person typing", "a dialog holds the keys"), Markdown that copies as Markdown (Code's .md
// preview), the providers' marks (KeelBot's model button), wide screens (KeelBot's chat list as a column), and KeelBot's
// own sound (core's audio, so it shares the notification volume and "do not disturb")
export { FOCUS_EVENT, FOCUS_KEYS, isTyping, modalOpen } from "../keys";
export type { KeyArea, KeyRow } from "../keys";
// 0.18.33 scrolling: ⌘/Ctrl+A selects one view's own text, and scrollToCenter moves one scroller (never the window)
export { scrollToCenter, selectedAll, useSelectAll } from "../selectAll";
export { MarkdownView } from "../components/Markdown";
export { ProviderIcon } from "../components/ProviderIcon";
export { useWide } from "../components/useWide";
export { playKeelBot } from "../notify";
// 0.15.7–0.16.1 (main's releases, ported to the plugin track): keel's file history (0.15.9), the panels the Wiki plugin
// (a page's versions, restore) and the Code page's keel view (files keel could not read or write) show
export { DocHistoryPanel, DocProblems } from "../components/DocHistory";
// 0.17.3 a spec's build status in the Wiki plugin: the Flow page's Resume (a rewind to the last saved step) and keel's
// Start a flow drawer, started from a spec (its path and its number of criteria)
export { ResumeRow } from "../components/ResumeRow";
export { StartFlowDrawer } from "../components/StartFlow";
export type { StartFromSpec } from "../components/StartFlow";
