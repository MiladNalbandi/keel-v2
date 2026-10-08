// The Tasks plugin's web part (plugins/tasks): `npm run build:plugin -- tasks` (in web/) builds this file on its own
// into plugins/tasks/web/dist/index.js. keel loads it at start from the url /api/features gives and calls setup() once:
// it puts the Tasks page in the menu where it always was (Run, after Flow), its results in the launcher (⌘K) and its
// cards in the Inbox. It imports only @keel/web-sdk and react: keel's page shares its own copies (the import map).

import {
  definePlugin,
  SLOTS,
  type InboxCardItem,
  type KeelSdk,
} from "@keel/web-sdk";
import { TaskInboxCard, TASK_ITEM_KINDS } from "./TaskInboxCard";
import { TasksPage, tasksLauncher } from "./Tasks";

/** The Tasks page, its launcher results and its Inbox cards: the same id, label, group, place and icon as keel 0.15.1. */
export function setup(sdk: KeelSdk) {
  sdk.registerPage({
    id: "tasks",
    label: "Tasks",
    group: "run",
    order: 20,
    icon: (
      <path d="M10 6h10M10 12h10M10 18h10M4 6l1.5 1.5L8 5M4 12l1.5 1.5L8 11M4 18l1.5 1.5L8 17" />
    ),
    component: TasksPage,
  });
  sdk.registerSlot(SLOTS.launcherSource, tasksLauncher);
  sdk.registerSlot<InboxCardItem>(SLOTS.inboxCard, {
    id: "tasks",
    kinds: TASK_ITEM_KINDS,
    component: TaskInboxCard,
  });
}

export default definePlugin({ name: "tasks", setup });
