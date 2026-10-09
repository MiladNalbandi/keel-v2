// keel's built-in parts: the one core file that names them (docs/plugins/09-step2-contract.md §5). Each part's
// registration module puts its pages and its pieces in the registries (src/sdk/registry.ts); core reads those and
// imports no part. main.tsx loads this file once, before the first render. In step 3 each line here becomes a plugin
// (the Map, the Wiki, CI/CD, Tasks, Jira, Code Review, Database, Git, the Graph, the Code page and KeelBot are plugins
// now: plugins/map, plugins/wiki, plugins/ci, plugins/tasks, plugins/jira, plugins/review, plugins/db, plugins/git,
// plugins/graph, plugins/code, plugins/keelbot, loaded at start by addons.ts). No part is left here.
//
// It stays as the place a future built-in part would go; today it is empty.
export {};
