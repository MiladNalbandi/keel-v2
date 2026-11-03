// The GitHub Issues plugin's web part imports only react, @keel/web-sdk and its own files (keel's page shares the first
// two at run time), and every name it takes from @keel/web-sdk is in it. Like the Jira plugin's own check.

import { join } from "node:path";
import { checkPluginImports } from "../../../../web/src/test/pluginImports";

checkPluginImports(
  join(__dirname, ".."),
  "GitHub Issues",
  ["definePlugin", "Note", "agoText"],
);
