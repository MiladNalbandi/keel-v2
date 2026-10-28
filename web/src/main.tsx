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
import "./styles/plugins.css";
import "./styles/scm.css";
import "./styles/launcher.css";
import "./styles/rules.css";
// v0.15.2 the model picker and the providers' marks.
import "./styles/models.css";
// v0.15.4 the key cheat sheet and the menu by keyboard.
import "./styles/keys.css";
// v0.16.0 Control › Plugins, the Inbox's install requests and the core-only menu's sets.
import "./styles/marketplace.css";
// v0.16.0 the core-only welcome and menu box, and the Inbox's agent install request (docs/plugins/mockup.html).
import "./styles/corehome.css";
// v0.16.1 Control › Plugins in the look of docs/plugins/mockup.html.
import "./styles/plugins-page.css";
// keel 0.18.57 the Settings page (General · Models & logins · Connections · Budget · Plugins · Databases · Devices).
import "./styles/settings.css";
// keel 0.18.66 Help › Start here, the short guides, step 3's MCP set-up, Settings › Access and the update notice; the
// first-run welcome (its own dark look, from the approved mockup).
import "./styles/help.css";
import "./styles/welcome.css";

// Plugins' web parts load at run time and use this React and @keel/web-sdk: the import map in index.html points their
// bare imports to small shims that read window.__keel (src/sdk/shared.ts). So it is set before anything renders.
window.__keel = { React, ReactDOM, ReactDOMClient, jsxRuntime, sdk };

ReactDOMClient.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
