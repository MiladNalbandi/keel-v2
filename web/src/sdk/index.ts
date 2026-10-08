// @keel/web-sdk: what a plugin's web pages may use from keel's web. Core bundles it once and shares it on
// window.__keel; a plugin's build keeps `import … from "@keel/web-sdk"` as a bare import, and the import map points it
// to a small shim that reads window.__keel.sdk (tools/sdkShims.ts).
//
// Name every export here (no `export *`): the build lists these names to write the shim. Removing or changing one
// breaks the plugins that use it.

// the plugin itself
export { definePlugin } from "./plugin";
export type { AddonPageProps, AddonWeb } from "./plugin";

// keel's api: the same transport as keel's own pages (/api + path, errors as ApiError)
export { ApiError, del, errorParts, get, getText, patch, post, put } from "../api";
export type { ClarifyQuestion } from "../api";

// data loading (live: loads again on every server event tick)
export { useLoad } from "../state";
export type { Loaded } from "../state";

// links to screens, keel's own and the add-ons' (#/initiatives/INI-3)
export { hashForScreen } from "../routes";

// keel's small ui pieces
export { Async, Confirm, Drawer, Empty, ErrorBox, Panel, Pill, Tabs } from "../components/ui";
export type { PillTone } from "../components/ui";
export { Markdown } from "../components/Markdown";
export { ClarifyForm, answersOf } from "../components/ClarifyForm";
export type { ClarifyAnswers } from "../components/ClarifyForm";
