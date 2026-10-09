// keel Product's web part as a plugin bundle: `npm run build:product` (in web/) builds index.tsx into dist/index.js
// and dist/style.css. React and @keel/web-sdk stay bare imports, so the bundle holds only Product's own code: keel's
// page gives it keel's copies at run time (the import map in keel's index.html).
//
// It imports no package at run time: Vite loads this file from product/web, which has no node_modules (web/ has them).

import { fileURLToPath } from "node:url";
import type { UserConfig } from "vite";

/** What keel shares with its plugins (web/tools/sdkShims.ts): never bundled into a plugin. */
const SHARED = ["react", "react-dom", "react-dom/client", "react/jsx-runtime", "@keel/web-sdk"];

export default {
  root: fileURLToPath(new URL(".", import.meta.url)),
  publicDir: false,
  esbuild: { jsx: "automatic" },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    lib: { entry: "index.tsx", formats: ["es"], fileName: () => "index.js" },
    rollupOptions: { external: SHARED },
  },
} satisfies UserConfig;
