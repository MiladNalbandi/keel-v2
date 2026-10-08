/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { sdkShims } from "./tools/sdkShims";

// `npm run dev` serves the studio on :5173 and forwards /api (including the /api/events SSE stream) and /plugins (the
// plugins' web parts) to the Spring Boot api on :8080. `npm run build` writes web/dist, which the api serves at /.
// Override with KEEL_API=http://host:port npm run dev when the api runs elsewhere.
const API = process.env.KEEL_API ?? "http://127.0.0.1:8080";

// @keel/web-sdk: what the plugins' web pages use from keel's web (tools/sdkShims.ts shares it with them at run time)
const SDK = fileURLToPath(new URL("./src/sdk/index.ts", import.meta.url));

export default defineConfig({
  plugins: [react(), sdkShims({ sdk: SDK })],
  resolve: {
    alias: { "@keel/web-sdk": SDK },
    // keel Product's tests (../product/web/test) and the plugins' (../plugins/*/web/test) run here: they use this
    // folder's packages, not a copy of their own
    dedupe: ["react", "react-dom", "@testing-library/react", "@testing-library/user-event", "msw"],
  },
  server: {
    port: 5173,
    // vitest loads keel Product's tests and pages from ../product/web, and the plugins' from ../plugins/<name>/web
    fs: { allow: [".", "../product/web", "../plugins"] },
    proxy: {
      // SSE: no buffering, keep the connection open.
      "/api/events": {
        target: API,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyRes", (res) => {
            res.headers["cache-control"] = "no-cache";
            res.headers["x-accel-buffering"] = "no";
          });
        },
      },
      "/api": { target: API, changeOrigin: true },
      "/plugins": { target: API, changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "../product/web/**/*.test.{ts,tsx}", "../plugins/*/web/**/*.test.{ts,tsx}"],
    css: false,
  },
});
