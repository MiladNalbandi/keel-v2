// What keel's page shares with the plugins' web parts. main.tsx sets window.__keel before anything renders; the shim
// modules the build writes (dist/assets/sdk/, tools/sdkShims.ts) read it. So a plugin uses keel's one React and this
// SDK, never a copy of its own (two Reacts break hooks: "Invalid hook call").

import type * as React from "react";
import type * as ReactDOM from "react-dom";
import type * as ReactDOMClient from "react-dom/client";
import type * as jsxRuntime from "react/jsx-runtime";
import type * as sdk from "./index";

export type KeelShared = {
  React: typeof React;
  ReactDOM: typeof ReactDOM;
  ReactDOMClient: typeof ReactDOMClient;
  jsxRuntime: typeof jsxRuntime;
  sdk: typeof sdk;
};

declare global {
  interface Window {
    __keel?: KeelShared;
  }
}
