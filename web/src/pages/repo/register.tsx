// The Code part (web/src/builtins.ts loads this file): its page in the menu (#/repo, also #/code) and what it adds to
// the launcher (the project's files, the code graph's hits, Focus mode). Its own side views and tabs are in Ide.tsx;
// the other parts add theirs through the slots code.activity, code.tab and assistant.
// A branch's tab (Branch.tsx) is the Code page's own: Source control opens it while the Git plugin's panel is there,
// and it reads the branch and switches with the Git plugin's api (/git/branch, /git/switch).

import { registerPage, registerSlot } from "../../sdk/registry";
import { SLOTS, type CodeTabItem, type CodeTabProps } from "../../sdk/slots";
import { RepoPage } from "../Repo";
import { BranchTab } from "./Branch";
import { codeLauncher } from "./launcher";

/** A branch's tab: its commits and changed files against the base, and Switch. */
function BranchEditorTab({ pid, tab, mode, open }: CodeTabProps) {
  return (
    <BranchTab
      pid={pid}
      name={tab.path}
      mode={mode}
      onOpenCommitFile={(sha, p, pin) => open({ kind: "commit", path: p, sha, view: "diff" }, { pin })}
    />
  );
}

registerPage({
  id: "repo",
  label: "Code",
  group: "know",
  order: 10,
  aliases: ["code"],
  icon: <path d="M8 7l-5 5 5 5M16 7l5 5-5 5M13.5 5l-3 14" />,
  component: RepoPage,
});

registerSlot(SLOTS.launcherSource, codeLauncher);

registerSlot<CodeTabItem>(SLOTS.codeTab, {
  id: "branch",
  icon: "branch",
  tabTitle: (path) => path,
  component: BranchEditorTab,
});
