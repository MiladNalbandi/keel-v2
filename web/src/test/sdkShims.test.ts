// The build's shims for plugins (tools/sdkShims.ts): from a list of export names to a module that re-exports keel's
// own copy (window.__keel), its content-hashed file name, and the import map.

import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { afterEach, describe, expect, it } from "vitest";
import { importMap, SHARED, shimFileName, shimSource } from "../../tools/sdkShims";
import * as sdk from "../sdk";
import type { KeelShared } from "../sdk/shared";

/** Loads a shim's source as the ES module a browser would. */
const load = (src: string) => import(/* @vite-ignore */ `data:text/javascript,${encodeURIComponent(src)}`);

afterEach(() => {
  delete window.__keel;
});

describe("sdk shims", () => {
  it("re-export a member of window.__keel as the default and under each name, sorted, once", () => {
    const src = shimSource("react", ["useState", "Children", "default", "useState", "__esModule", "version"]);
    expect(src).toContain("const k = window.__keel;");
    expect(src).toContain("const m = k.React;");
    expect(src).toContain("export default m;");
    expect(src).toContain("export const { Children, useState, version } = m;");
    expect(src.match(/^export /gm)).toHaveLength(2);
  });

  it("give a plugin keel's own React and SDK, the very same functions", async () => {
    window.__keel = { React, jsxRuntime, sdk } as unknown as KeelShared;
    const react = await load(shimSource("react", ["createElement", "useEffect", "useState"]));
    expect(react.useState).toBe(React.useState);
    expect(react.default).toBe(React);
    const runtime = await load(shimSource("react/jsx-runtime", ["jsx", "jsxs", "Fragment"]));
    expect(runtime.jsx).toBe(jsxRuntime.jsx);
    const keel = await load(shimSource("@keel/web-sdk", Object.keys(sdk)));
    expect(keel.definePlugin).toBe(sdk.definePlugin);
    expect(keel.useLoad).toBe(sdk.useLoad);
  });

  it("say clearly when keel's page did not share window.__keel", async () => {
    await expect(load(shimSource("@keel/web-sdk", ["get", "post"]))).rejects.toThrow(
      "keel's web is not loaded: window.__keel is missing",
    );
  });

  it("know the five shared names and the window.__keel member of each", () => {
    expect(SHARED).toEqual({
      react: "React",
      "react-dom": "ReactDOM",
      "react-dom/client": "ReactDOMClient",
      "react/jsx-runtime": "jsxRuntime",
      "@keel/web-sdk": "sdk",
    });
    expect(shimSource("react-dom/client", ["createRoot"])).toContain("const m = k.ReactDOMClient;");
    expect(shimSource("@keel/web-sdk", [])).not.toContain("export const");
  });

  it("refuse a name keel does not share, and an export name a module cannot have", () => {
    expect(() => shimSource("lodash", ["map"])).toThrow('keel shares no module named "lodash"');
    expect(() => shimSource("react", ["useState", "not-a-name"])).toThrow('a shim for "react" cannot export not-a-name');
  });

  it("get a file name from the shared name and a hash of the content", () => {
    const a = shimSource("react-dom/client", ["createRoot"]);
    const b = shimSource("react-dom/client", ["createRoot", "hydrateRoot"]);
    expect(shimFileName("react-dom/client", a)).toMatch(/^react-dom-client-[0-9a-f]{8}\.js$/);
    expect(shimFileName("@keel/web-sdk", a)).toMatch(/^keel-web-sdk-[0-9a-f]{8}\.js$/);
    expect(shimFileName("react-dom/client", a)).toBe(shimFileName("react-dom/client", a));
    expect(shimFileName("react-dom/client", a)).not.toBe(shimFileName("react-dom/client", b));
  });

  it("write the import map as json that is safe inside a <script>", () => {
    const json = importMap({ react: "/assets/sdk/react-1a2b3c4d.js", "@keel/web-sdk": "/assets/sdk/</script>.js" });
    expect(json).not.toContain("</script>");
    expect(JSON.parse(json)).toEqual({
      imports: { react: "/assets/sdk/react-1a2b3c4d.js", "@keel/web-sdk": "/assets/sdk/</script>.js" },
    });
  });
});
