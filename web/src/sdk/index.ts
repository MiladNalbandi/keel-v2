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
export { ApiError, del, errorParts, get, getText, patch, post, put } from "../api";
export type {
  ClarifyQuestion, Estimate, KeelMap, MapLevel, MapNode, MapResponse, McpServer, ProjectSettings, RunMode, Step, WikiPage,
  WikiTree, Workflow,
} from "../api";
// the Inbox's items (a plugin's inbox.card shows its own kinds)
export type { InboxItem, InboxTask } from "../inboxApi";

// data loading (live: loads again on every server event tick), and the app's state (the chosen project, toast)
export { useApp, useLoad } from "../state";
export type { Loaded } from "../state";

// links to screens, keel's own and the add-ons' (#/initiatives/INI-3); the link the page shows now (#/wiki/kb:domain
// is { page: "wiki", arg: "kb:domain" }), go(screen, arg) to open another, useRoute() to read the link
export { hashForScreen } from "../routes";
export { go, useRoute } from "../state";
export type { Route } from "../routes";

// keel's small ui pieces
export { Async, Confirm, Drawer, Empty, ErrorBox, Loading, PageHead, Panel, Pill, Since, Tabs } from "../components/ui";
export type { PillTone } from "../components/ui";
export { EmptyState, SearchBox, Section, Skeleton, useNarrow } from "../components/page";
export { Markdown } from "../components/Markdown";
// a code block with keel's highlighting (the Database plugin's KeelBot card shows its SQL with it)
export { CodeBlock } from "../components/Code";
export { ClarifyForm, answersOf } from "../components/ClarifyForm";
export type { ClarifyAnswers } from "../components/ClarifyForm";
export { clock, kfmt } from "../format";
export { agoText } from "../components/UsageStrip";
// starting a flow: the run mode picker, and the Doctor that cleans the project folder when a start is refused
export { RunModePicker } from "../components/RunMode";
export { WorkspaceDoctor } from "../components/WorkspaceDoctor";

// keel's workflow pictures (the Flow page and the builder draw with them; the Wiki plugin shows a workflow read only):
// the blocks, the step table, the graph with zoom, the Blocks / Table / Graph choice, the tokens per step, and the
// "what this step does" drawer
export { Blocks, BlocksLegend, StepTable, useMapView, ViewToggle } from "../components/Blocks";
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

// extension points (step 2, docs/plugins/09-step2-contract.md §5): pages for the menu and the router, and slots — named
// places in keel's pages where a part puts a piece (live: a page renders again when a piece registers)
export { allPages, pageFor, pageOf, registerPage, registerSlot, slotItems, usePages, useSlot } from "./registry";
export type { PageProps, PageRegistration, SlotItem } from "./registry";
export { SLOTS } from "./slots";
export type {
  AssistantItem, AssistantProps, CodeActivityItem, CodeActivityProps, CodeOpen, CodeOpenSpec, CodeTabItem, CodeTabProps,
  CodeTabRef, CodeView, ConnectionKindItem, InboxCardItem, InboxCardProps, JobsTabItem, KeelbotBlock, KeelbotCardItem,
  KeelbotCardProps, LauncherSourceItem, MapErQueryItem, ScmPanelProps, SettingKind, SettingRow, SettingsSectionItem,
  ToolsCardItem, WorkflowActionsItem,
} from "./slots";

// "ask KeelBot" without importing it: the assistant's part listens
export { ASK_ASSISTANT_EVENT, askAssistant } from "./assistant";
export type { AskAssistantDetail } from "./assistant";

// the launcher (⌘K): a launcher.source's results and their actions (open a link, ask KeelBot, copy)
export { askAction, copyAction, goHash, linkAction } from "../components/launcher/sources";
export type { Ctx as LauncherCtx } from "../components/launcher/sources";
export type { Item as LauncherItem } from "../components/launcher/model";

// code and diffs, and keys (the Code Review plugin's diff, file tree and keys use them): a unified diff's rows (and side
// by side), the syntax colours, the Code page's file and view icons, keel's key chords and the keymap a person picked
// (IntelliJ or VS Code; src/keys.ts), and the api's commit and diff types
export { parseDiff, splitRows } from "../components/Code";
export type { DiffRow, SplitRow } from "../components/Code";
export { highlight, languageOf } from "../components/highlight";
export { FileIcon, Icon } from "../components/icons";
export { keyLabel, matches, readKeymap, saveKeymap } from "../keys";
export type { Keymap } from "../keys";
export type { Commit, FileDiff } from "../api";
