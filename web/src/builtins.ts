// keel's built-in parts: the one core file that names them (docs/plugins/09-step2-contract.md §5). Each part's
// registration module puts its pages and its pieces in the registries (src/sdk/registry.ts); core reads those and
// imports no part. main.tsx loads this file once, before the first render. In step 3 each line here becomes a plugin
// (the Map, Tasks and Jira are plugins already: plugins/map, plugins/tasks, plugins/jira, loaded at start by addons.ts).
//
// The order of the lines does not matter: pages and pieces carry their own order.

import "./pages/repo/register"; // Code: the page (#/repo, #/code) and its launcher results
import "./components/helper/register"; // KeelBot: its page (#/keelbot) and the assistant (askAssistant, ⌘I)
import "./components/graph/register"; // Graph
import "./pages/Wiki"; // Wiki
import "./components/plugins/git/register"; // Git: GitHub token, Code › Source control, branch tabs, git: blocks
import "./components/plugins/db/register"; // Database: Connections, Code › Database, table tabs, db: blocks
import "./components/plugins/ci/register"; // CI/CD: Jobs › Pipelines
import "./components/review/register"; // Code Review: GitLab, Code › Review, review tabs, pull requests in ⌘K
