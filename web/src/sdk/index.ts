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
export type { ClarifyQuestion, KeelMap, MapLevel, MapNode, MapResponse } from "../api";

// data loading (live: loads again on every server event tick), and the app's state (the chosen project, toast)
export { useApp, useLoad } from "../state";
export type { Loaded } from "../state";

// links to screens, keel's own and the add-ons' (#/initiatives/INI-3)
export { hashForScreen } from "../routes";

// keel's small ui pieces
export { Async, Confirm, Drawer, Empty, ErrorBox, PageHead, Panel, Pill, Tabs } from "../components/ui";
export type { PillTone } from "../components/ui";
export { EmptyState } from "../components/page";
export { Markdown } from "../components/Markdown";
export { ClarifyForm, answersOf } from "../components/ClarifyForm";
export type { ClarifyAnswers } from "../components/ClarifyForm";
export { clock } from "../format";

// keel's diagram canvas (components/er): the boxes of a map level and the database diagram (the Map plugin draws
// them; the Graph page uses the same canvas)
export { BoxDiagram, moduleBoxes, systemBoxes } from "../components/er/BoxDiagram";
export type { GBox } from "../components/er/BoxDiagram";
export { ErDiagram } from "../components/er/ErDiagram";
export { schemaOf } from "../components/er/model";

// extension points (step 2, docs/plugins/09-step2-contract.md §5): pages for the menu and the router, and slots — named
// places in keel's pages where a part puts a piece (live: a page renders again when a piece registers)
export { allPages, pageFor, pageOf, registerPage, registerSlot, slotItems, usePages, useSlot } from "./registry";
export type { PageProps, PageRegistration, SlotItem } from "./registry";
export { SLOTS } from "./slots";
export type {
  AssistantItem, AssistantProps, CodeActivityItem, CodeActivityProps, CodeOpen, CodeOpenSpec, CodeTabItem, CodeTabProps,
  CodeTabRef, CodeView, ConnectionKindItem, JobsTabItem, LauncherSourceItem, MapErQueryItem, ScmPanelProps, SettingKind,
  SettingRow, SettingsSectionItem, ToolsCardItem, WorkflowActionsItem,
} from "./slots";

// "ask KeelBot" without importing it: the assistant's part listens
export { ASK_ASSISTANT_EVENT, askAssistant } from "./assistant";
export type { AskAssistantDetail } from "./assistant";
