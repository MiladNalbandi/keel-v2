// keel's built-in parts: the one core file that names them (docs/plugins/09-step2-contract.md §5). Each part's
// registration module puts its pages and its pieces in the registries (src/sdk/registry.ts); core reads those and
// imports no part. main.tsx loads this file once, before the first render. In step 3 each line here becomes a plugin
// (the Map, the Wiki, CI/CD, Tasks, Jira, Code Review, Database, Git and the Code page are plugins already:
// plugins/map, plugins/wiki, plugins/ci, plugins/tasks, plugins/jira, plugins/review, plugins/db, plugins/git,
// plugins/code, loaded at start by addons.ts).
//
// The order of the lines does not matter: pages and pieces carry their own order.

import "./components/helper/register"; // KeelBot: its page (#/keelbot) and the assistant (askAssistant, ⌘I)
import "./components/graph/register"; // Graph
