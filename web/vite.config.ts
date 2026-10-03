/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` serves the studio on :5173 and forwards /api (including the /api/events SSE stream)
// to the Spring Boot api on :8080. `npm run build` writes web/dist, which the api serves at /.
// Override with KEEL_API=http://host:port npm run dev when the api runs elsewhere.
const API = process.env.KEEL_API ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
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
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
