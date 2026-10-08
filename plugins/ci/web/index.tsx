// The CI/CD plugin's web part (plugins/ci): `npm run build:plugin -- ci` (in web/) builds this file on its own into
// plugins/ci/web/dist/index.js. keel loads it at start from the url /api/features gives and calls setup() once: it puts
// the project's pipelines in Jobs as the tab Pipelines (#/jobs/pipelines), shown while the plugin is on, as in keel
// 0.15.1. It imports only @keel/web-sdk and react: keel's page shares its own copies (the import map).
// KeelBot's CI button (keel-ci blocks) and Settings › When CI fails stay in keel's core.

import {
  definePlugin,
  SLOTS,
  type JobsTabItem,
  type KeelSdk,
} from "@keel/web-sdk";
import { PipelinesView } from "./Pipelines";

/** Jobs › Pipelines: the same id, title, line and place as keel 0.15.1. */
export function setup(sdk: KeelSdk) {
  sdk.registerSlot<JobsTabItem>(SLOTS.jobsTab, {
    id: "pipelines",
    title: "Pipelines",
    sub: "Agent calls, and the project's CI pipelines (CI/CD plugin).",
    plugin: "ci",
    order: 10,
    component: PipelinesView,
  });
}

export default definePlugin({ name: "ci", setup });
