// The Code Review part (web/src/builtins.ts loads this file): the GitLab connection, the Review view in the Code
// page's activity bar (while the plugin is on) with its keys and popups over the IDE, a review's file as an editor
// tab, the link #/repo/@review/pr:7, and the pull requests in the launcher.

import { useEffect } from "react";
import { nameOf } from "../../pages/repo/model";
import { registerSlot } from "../../sdk/registry";
import {
  SLOTS,
  type CodeActivityItem,
  type CodeActivityProps,
  type CodeTabItem,
  type CodeTabProps,
  type CodeTabRef,
  type ConnectionKindItem,
} from "../../sdk/slots";
import { GitLabSection } from "../plugins/GitLabConnection";
import { reviewLauncher } from "./launcher";
import { ReviewFileTab } from "./ReviewFileTab";
import { ReviewLayer } from "./ReviewLayer";
import { openReview, ReviewSide } from "./ReviewSide";
import { setOpener } from "./store";

/** A review file tab's path: "<review key>|<file>" (pr:7|src/a.kt, branch:feat/x|src/a.kt). */
const splitReview = (p: string): [string, string] => {
  const i = p.indexOf("|");
  return i < 0 ? [p, ""] : [p.slice(0, i), p.slice(i + 1)];
};

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

registerSlot<ConnectionKindItem>(SLOTS.connectionsKind, {
  id: "gitlab",
  title: "GitLab",
  order: 30,
  component: GitLabSection,
});

registerSlot<CodeActivityItem>(SLOTS.codeActivity, {
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

registerSlot<CodeTabItem>(SLOTS.codeTab, {
  id: "review",
  icon: "review",
  tabTitle: (path) => {
    const [key, file] = splitReview(path);
    return `${nameOf(file)} (${key.startsWith("pr:") ? key.slice(3) : key.slice(7)})`;
  },
  component: ReviewEditorTab,
});

registerSlot(SLOTS.launcherSource, reviewLauncher);
