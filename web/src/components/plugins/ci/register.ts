// The CI/CD part (web/src/builtins.ts loads this file): the project's pipelines as a tab of Jobs (#/jobs/pipelines),
// while the plugin is on.

import { registerSlot } from "../../../sdk/registry";
import { SLOTS, type JobsTabItem } from "../../../sdk/slots";
import { PipelinesView } from "../Pipelines";

registerSlot<JobsTabItem>(SLOTS.jobsTab, {
  id: "pipelines",
  title: "Pipelines",
  sub: "Agent calls, and the project's CI pipelines (CI/CD plugin).",
  plugin: "ci",
  order: 10,
  component: PipelinesView,
});
