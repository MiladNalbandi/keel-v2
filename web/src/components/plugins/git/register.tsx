// The Git part (web/src/builtins.ts loads this file): the GitHub token in Connections, its panel in Code › Source
// control (commit, push, the pull request, while the plugin is on), a branch as an editor tab, and its workflow
// blocks (git:push, …).

import { BranchTab } from "../../../pages/repo/Branch";
import { registerSlot } from "../../../sdk/registry";
import {
  SLOTS,
  type CodeActivityItem,
  type CodeTabItem,
  type CodeTabProps,
  type ConnectionKindItem,
  type WorkflowActionsItem,
} from "../../../sdk/slots";
import { GitHubSection } from "../GitHubToken";
import { GitPanel } from "../GitPanel";

/** A branch's tab: its commits and changed files against the base, and Switch. */
function BranchEditorTab({ pid, tab, mode, open }: CodeTabProps) {
  return (
    <BranchTab
      pid={pid}
      name={tab.path}
      mode={mode}
      onOpenCommitFile={(sha, p, pin) =>
        open({ kind: "commit", path: p, sha, view: "diff" }, { pin })
      }
    />
  );
}

registerSlot<ConnectionKindItem>(SLOTS.connectionsKind, {
  id: "github",
  title: "GitHub",
  order: 20,
  component: GitHubSection,
});

registerSlot<CodeActivityItem>(SLOTS.codeActivity, {
  id: "git",
  place: "scm",
  plugin: "git",
  component: GitPanel,
});

registerSlot<CodeTabItem>(SLOTS.codeTab, {
  id: "branch",
  icon: "branch",
  tabTitle: (path) => path,
  component: BranchEditorTab,
});

registerSlot<WorkflowActionsItem>(SLOTS.workflowActions, {
  id: "git",
  plugin: "git",
  prefix: "git:",
});
