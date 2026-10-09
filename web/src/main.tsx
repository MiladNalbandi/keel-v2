import * as React from "react";
import * as ReactDOM from "react-dom";
import * as ReactDOMClient from "react-dom/client";
import * as jsxRuntime from "react/jsx-runtime";
import { App } from "./App";
// keel's built-in parts register their pages and pieces (once, before the first render)
import "./builtins";
import * as sdk from "./sdk";
import "./styles.css";
// The shell and Run pages refine styles.css, so they load after it (an import from a component would load first).
import "./styles/run.css";
// The Project, Build and Control pages (Repo … Connections) refine styles.css the same way.
import "./styles/pages.css";
// The Map's diagrams (components/er).
import "./styles/diagram.css";
// The Code page's small IDE (activity bar, side bar, editor tabs, status bar).
import "./styles/repo.css";
import "./styles/helper.css";
import "./styles/plugins.css";
import "./styles/launcher.css";

// Plugins' web parts load at run time and use this React and @keel/web-sdk: the import map in index.html points their
// bare imports to small shims that read window.__keel (src/sdk/shared.ts). So it is set before anything renders.
window.__keel = { React, ReactDOM, ReactDOMClient, jsxRuntime, sdk };

ReactDOMClient.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
