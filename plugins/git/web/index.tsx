// The Git plugin's web part (plugins/git): `npm run build:plugin -- git` (in web/) builds this file on its own into
// plugins/git/web/dist/index.js. keel loads it at start from the url /api/features gives and calls setup() once: it puts
// what the Git part had in keel 0.15.1 in the same places. It imports only @keel/web-sdk and react: keel's page shares
// its own copies (the import map).
//
//   Code › Source control   its panel (commit, push, the pull request), while the plugin is on   slot code.activity
//   Workflows               its blocks git:branch … git:cleanup take `with:` settings              slot workflow.actions
//   KeelBot                 its buttons (```keel-git: commit, push, pr, switch, sync)               slot keelbot.card
//
// What stays in keel's core: Connections › GitHub (the token; ship opens the pull request with it), and the Code page's
// branch tab (the Code plugin's own: plugins/code/web/Branch.tsx), which Source control opens while this
// panel is there.

import {
  definePlugin,
  SLOTS,
  type CodeActivityItem,
  type KeelbotCardItem,
  type KeelSdk,
  type WorkflowActionsItem,
} from "@keel/web-sdk";
import { GitCard } from "./GitCard";
import { GitPanel } from "./GitPanel";

export function setup(sdk: KeelSdk) {
  sdk.registerSlot<CodeActivityItem>(SLOTS.codeActivity, {
    id: "git",
    place: "scm",
    plugin: "git",
    component: GitPanel,
  });
  sdk.registerSlot<WorkflowActionsItem>(SLOTS.workflowActions, {
    id: "git",
    plugin: "git",
    prefix: "git:",
  });
  sdk.registerSlot<KeelbotCardItem>(SLOTS.keelbotCard, {
    id: "git",
    kind: "keel-git",
    component: GitCard,
  });
}

export default definePlugin({ name: "git", setup });
