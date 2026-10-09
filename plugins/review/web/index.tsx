// The Code Review plugin's web part (plugins/review): `npm run build:plugin -- review` (in web/) builds this file on its
// own into plugins/review/web/dist/index.js and style.css (review.css, the look of keel 0.15.1). keel loads both at
// start from the urls /api/features gives and calls setup() once: it registers what the part registered as a built-in,
// with the same ids, titles and places. The GitLab connection, the Review view in the Code page's activity bar (while
// the plugin is on) with its keys and popups over the IDE, a review's file as an editor tab, the link #/repo/@review/pr:7,
// and the pull requests in the launcher. It imports only @keel/web-sdk and react: keel's page shares its own copies
// (the import map). The key chords and the keymap are keel's own (src/keys.ts), through @keel/web-sdk.

import { useEffect } from "react";
import {
  definePlugin,
  SLOTS,
  type CodeActivityItem,
  type CodeActivityProps,
  type CodeTabItem,
  type CodeTabProps,
  type CodeTabRef,
  type ConnectionKindItem,
  type KeelSdk,
} from "@keel/web-sdk";
import { GitLabSection } from "./GitLabConnection";
import { reviewLauncher } from "./launcher";
import { ReviewFileTab } from "./ReviewFileTab";
import { ReviewLayer } from "./ReviewLayer";
import { openReview, ReviewSide } from "./ReviewSide";
import { setOpener } from "./store";
import "./review.css";

/** A review file tab's path: "<review key>|<file>" (pr:7|src/a.kt, branch:feat/x|src/a.kt). */
const splitReview = (p: string): [string, string] => {
  const i = p.indexOf("|");
  return i < 0 ? [p, ""] : [p.slice(0, i), p.slice(i + 1)];
};

/** A path's last name (src/a.kt → a.kt), as the Code page names its tabs. */
const nameOf = (p: string) => p.slice(p.lastIndexOf("/") + 1);

/** The review file the editor shows now, or null. */
function reviewOf(
  active: CodeTabRef | null,
): { key: string; path: string; view: "code" | "diff" } | null {
  if (active?.kind !== "review") return null;
  const [key, path] = splitReview(active.path);
  return { key, path, view: active.view === "code" ? "code" : "diff" };
}

/** Code › Review: the pull requests and one review; its files open as editor tabs. */
function ReviewActivity({ pid, active, open }: CodeActivityProps) {
  // v0.14.0 a review opens its files as editor tabs (kind "review", path "<key>|<file>", view diff or code)
  useEffect(() => {
    setOpener((key, path, view, pin) =>
      open({ kind: "review", path: `${key}|${path}`, view }, { pin }),
    );
    return () => setOpener(null);
  }, [open]);
  const f = reviewOf(active);
  return (
    <ReviewSide
      pid={pid}
      activeFile={f ? { key: f.key, path: f.path } : null}
    />
  );
}

/** The review's keys (F7, ⌘B, ⇧⌘A …) and popups, over the whole IDE. */
function ReviewKeys({ pid, active }: CodeActivityProps) {
  return <ReviewLayer pid={pid} active={reviewOf(active)} />;
}

/** One file of a review: its diff, or the whole file. */
function ReviewEditorTab({ pid, tab, mode }: CodeTabProps) {
  const [key, file] = splitReview(tab.path);
  return (
    <ReviewFileTab
      pid={pid}
      reviewKey={key}
      path={file}
      view={tab.view === "code" ? "code" : "diff"}
      mode={mode}
    />
  );
}

/** Connections › GitLab, Code › Review, the review tabs and the pull requests in ⌘K: the same ids, titles and places
 *  as keel 0.15.1. */
export function setup(sdk: KeelSdk) {
  sdk.registerSlot<ConnectionKindItem>(SLOTS.connectionsKind, {
    id: "gitlab",
    title: "GitLab",
    order: 30,
    component: GitLabSection,
  });

  sdk.registerSlot<CodeActivityItem>(SLOTS.codeActivity, {
    id: "review",
    title: "Review",
    icon: "review",
    short: "Review",
    order: 40,
    plugin: "review",
    component: ReviewActivity,
    layer: ReviewKeys,
    onLink: openReview,
  });

  sdk.registerSlot<CodeTabItem>(SLOTS.codeTab, {
    id: "review",
    icon: "review",
    tabTitle: (path) => {
      const [key, file] = splitReview(path);
      return `${nameOf(file)} (${key.startsWith("pr:") ? key.slice(3) : key.slice(7)})`;
    },
    component: ReviewEditorTab,
  });

  sdk.registerSlot(SLOTS.launcherSource, reviewLauncher);
}

export default definePlugin({ name: "review", setup });
