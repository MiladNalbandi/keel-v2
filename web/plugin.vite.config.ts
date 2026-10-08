// A plugin's web part as a bundle of its own (docs/plugins/11-step3-contract.md, wave 0). KEEL_PLUGIN=<name> builds
// ../plugins/<name>/web/index.tsx into ../plugins/<name>/web/dist (index.js, and style.css when it imports css).
// React and @keel/web-sdk stay bare imports, so the bundle holds only the plugin's own code: keel's page gives it
// keel's copies at run time (the import map in keel's index.html, tools/sdkShims.ts).
//
//   npm run build:plugin -- map      one plugin (tools/build-plugin.mjs sets KEEL_PLUGIN)
//   npm run build:plugins            every plugins/<name>/web
//
// A plugin's web folder has no node_modules: an import of anything but react, @keel/web-sdk and its own files fails
// the build, so a plugin never carries a second copy of keel's code.

import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { UserConfig } from "vite";
import { SHARED } from "./tools/sdkShims";

const name = process.env.KEEL_PLUGIN ?? "";
if (!/^[a-z][a-z0-9-]{0,31}$/.test(name)) {
  throw new Error(
    `KEEL_PLUGIN must name a plugin in plugins/ (got "${name}"): npm run build:plugin -- <name>`,
  );
}
const root = fileURLToPath(new URL(`../plugins/${name}/web/`, import.meta.url));
if (!existsSync(`${root}index.tsx`))
  throw new Error(`plugins/${name}/web/index.tsx is missing`);

export default {
  root,
  publicDir: false,
  esbuild: { jsx: "automatic" },
  logLevel: "warn",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    lib: { entry: "index.tsx", formats: ["es"], fileName: () => "index.js" },
    rollupOptions: { external: Object.keys(SHARED) },
  },
} satisfies UserConfig;
