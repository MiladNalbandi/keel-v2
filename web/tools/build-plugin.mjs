// Builds plugins' web parts with plugin.vite.config.ts, each on its own (KEEL_PLUGIN=<name>):
//
//   npm run build:plugin -- map [db …]   these plugins: plugins/<name>/web/dist
//   npm run build:plugins                every plugin in plugins/ that has web/index.tsx
//
// keel Product keeps its own build (npm run build:product).

import { existsSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { build } from "vite";

const config = fileURLToPath(new URL("../plugin.vite.config.ts", import.meta.url));
const plugins = fileURLToPath(new URL("../../plugins/", import.meta.url));

const args = process.argv.slice(2);
const all = args.includes("--all");
const names = all
  ? (existsSync(plugins) ? readdirSync(plugins) : []).filter((n) => existsSync(`${plugins}${n}/web/index.tsx`)).sort()
  : args;

if (names.length === 0) {
  if (all) {
    console.log("build-plugin: no plugin in plugins/ has a web part");
    process.exit(0);
  }
  console.error("usage: npm run build:plugin -- <name> [<name> …]   (or npm run build:plugins)");
  process.exit(1);
}

for (const name of names) {
  process.env.KEEL_PLUGIN = name;
  await build({ configFile: config, mode: "production" });
  console.log(`build-plugin: ${name} → plugins/${name}/web/dist`);
}
