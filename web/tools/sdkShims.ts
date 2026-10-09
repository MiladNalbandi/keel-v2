// The build half of sharing keel's React and @keel/web-sdk with plugins (src/sdk/shared.ts is the page half).
//
//   index.html                       <script type="importmap"> { "react": "/assets/sdk/react-1a2b3c4d.js", … }
//   /assets/sdk/react-1a2b3c4d.js    const m = window.__keel.React; export default m; export const { useState, … } = m;
//   a plugin's web/index.js          import { useState } from "react"   → that shim → keel's own React
//
// A shim must list the names it exports, so the build lists them: React's from the installed packages, the SDK's
// from src/sdk/index.ts as the build parsed it. `vite` (dev) serves shims at the same place, and the same import map.

import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import path from "node:path";
import type { Plugin } from "vite"; // types only: the tests import this file in jsdom, where Vite's esbuild cannot run
import type { KeelShared } from "../src/sdk/shared";

/** The bare names a plugin's web part imports from keel, and the member of window.__keel each one is. */
export const SHARED: Record<string, keyof KeelShared> = {
  react: "React",
  "react-dom": "ReactDOM",
  "react-dom/client": "ReactDOMClient",
  "react/jsx-runtime": "jsxRuntime",
  "@keel/web-sdk": "sdk",
};

const SDK = "@keel/web-sdk";
const IDENT = /^[A-Za-z_$][\w$]*$/;

/** The shim module for `name`: keel's copy (a member of window.__keel) as the default export and under each name. */
export function shimSource(name: string, names: Iterable<string>): string {
  const member = SHARED[name];
  if (!member) throw new Error(`keel shares no module named "${name}"`);
  const list = [...new Set(names)].filter((n) => n !== "default" && n !== "__esModule").sort();
  const bad = list.filter((n) => !IDENT.test(n));
  if (bad.length) throw new Error(`a shim for "${name}" cannot export ${bad.join(", ")}`);
  return [
    `// "${name}" for keel's plugins: keel's own copy (window.__keel.${member}), never a second one.`,
    "const k = window.__keel;",
    `if (!k) throw new Error("keel's web is not loaded: window.__keel is missing");`,
    `const m = k.${member};`,
    "export default m;",
    ...(list.length ? [`export const { ${list.join(", ")} } = m;`] : []),
    "",
  ].join("\n");
}

/** A path with / (Rollup's module ids and urls use it on every system). */
const posix = (p: string) => p.split(path.sep).join("/");

/** A file-name-safe form of a shared name: react-dom/client → react-dom-client, @keel/web-sdk → keel-web-sdk. */
const slug = (name: string) => name.replace(/^@/, "").replace(/[^A-Za-z0-9]+/g, "-");

/** The shim's file name, with a hash of its content: react-dom/client → react-dom-client-1a2b3c4d.js. */
export function shimFileName(name: string, source: string): string {
  return `${slug(name)}-${createHash("sha256").update(source).digest("hex").slice(0, 8)}.js`;
}

/** The import map's json (safe inside a <script>). */
export function importMap(imports: Record<string, string>): string {
  return JSON.stringify({ imports }, null, 2).replace(/</g, "\\u003c");
}

/**
 * The Vite plugin. `vite build`: writes the shims to <assetsDir>/sdk/ and puts the import map in index.html (Vite
 * moves it before the first module script). `vite` (dev): serves shims at the same place, and the import map.
 * `sdk` is the absolute path of src/sdk/index.ts.
 */
export function sdkShims({ sdk }: { sdk: string }): Plugin {
  const sdkId = posix(sdk);
  let base = "/";
  let root = "";
  let assetsDir = "assets";
  let packageNames: (name: string) => string[] = () => [];
  /** shared name → its shim: the path under base, and the source */
  const shims = new Map<string, { file: string; source: string }>();

  return {
    name: "keel:sdk-shims",

    configResolved(config) {
      base = config.base;
      root = config.root;
      assetsDir = config.build.assetsDir;
      const need = createRequire(path.join(root, "package.json"));
      packageNames = (name) => Object.keys(need(name) as object);
    },

    configureServer(server) {
      for (const name of Object.keys(SHARED)) {
        // dev: the SDK's shim re-exports the module the page already loaded (the same instance), so it needs no names
        const source = name === SDK
          ? `export * from "${base}${posix(path.relative(root, sdkId))}";\n`
          : shimSource(name, packageNames(name));
        shims.set(name, { file: `${assetsDir}/sdk/${slug(name)}.js`, source });
      }
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? "").split("?")[0];
        const shim = [...shims.values()].find((s) => base + s.file === url);
        if (!shim) return next();
        res.setHeader("Content-Type", "text/javascript");
        res.end(shim.source);
      });
    },

    // build: the module graph is complete here, so the SDK's export names are known
    renderStart() {
      const info = this.getModuleInfo(sdkId);
      if (!info?.exports) return this.error(`${sdkId} is not in the build: main.tsx must import it`);
      if (info.exports.includes("*")) this.error(`${sdkId} must name each export (no export *): its shim lists them`);
      for (const name of Object.keys(SHARED)) {
        const source = shimSource(name, name === SDK ? info.exports : packageNames(name));
        this.parse(source); // a shim that does not parse fails the build, not the page
        shims.set(name, { file: `${assetsDir}/sdk/${shimFileName(name, source)}`, source });
      }
    },

    generateBundle() {
      for (const { file, source } of shims.values()) this.emitFile({ type: "asset", fileName: file, source });
    },

    transformIndexHtml: {
      order: "post",
      handler() {
        if (shims.size === 0) throw new Error("keel:sdk-shims: index.html came before the shims were made");
        const imports = Object.fromEntries([...shims].map(([name, s]) => [name, base + s.file]));
        return [{ tag: "script", attrs: { type: "importmap" }, children: importMap(imports), injectTo: "head-prepend" }];
      },
    },
  };
}
