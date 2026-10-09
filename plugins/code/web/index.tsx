// The Code plugin's web part (plugins/code): `npm run build:plugin -- code` (in web/) builds this file on its own into
// plugins/code/web/dist/index.js. keel loads it at start from the url /api/features gives and calls setup() once: it
// registers what the Code part registered as a built-in, with the same ids, titles and places. It imports only react,
// react-dom and @keel/web-sdk: keel's page shares its own copies (the import map). Its look is keel's own repo.css (the
// Code Review and Database views in the IDE use it too).
//
//   the menu        the Code page (#/repo, also #/code; #/repo/<path>:<line> opens a file at a line)
//   ⌘K              the project's files, the code graph's hits, Focus mode                    slot launcher.source
//   Code's tabs     a branch's tab (Source control opens it while the Git plugin's panel is there)  slot code.tab
//
// The Code page is a host: its side views, tabs and the assistant's column come from the slots code.activity, code.tab
// and assistant (Git, Database, Code Review, KeelBot). A branch's tab reads the branch and switches with the Git
// plugin's api (/git/branch, /git/switch: codeApi.ts).

import {
  definePlugin,
  SLOTS,
  type CodeTabItem,
  type CodeTabProps,
  type KeelSdk,
} from "@keel/web-sdk";
import { BranchTab } from "./Branch";
import { codeLauncher } from "./launcher";
import { RepoPage } from "./Repo";

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

/** The Code page, its launcher results and the branch tab: the same ids, titles and places as keel 0.15.1. */
export function setup(sdk: KeelSdk) {
  sdk.registerPage({
    id: "repo",
    label: "Code",
    group: "know",
    order: 10,
    aliases: ["code"],
    icon: <path d="M8 7l-5 5 5 5M16 7l5 5-5 5M13.5 5l-3 14" />,
    component: RepoPage,
  });

  sdk.registerSlot(SLOTS.launcherSource, codeLauncher);

  sdk.registerSlot<CodeTabItem>(SLOTS.codeTab, {
    id: "branch",
    icon: "branch",
    tabTitle: (path) => path,
    component: BranchEditorTab,
  });
}

export default definePlugin({ name: "code", setup });
