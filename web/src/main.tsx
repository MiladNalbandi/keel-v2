import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";
// The shell and Run pages refine styles.css, so they load after it (an import from a component would load first).
import "./styles/run.css";
// The Project, Build and Control pages (Repo … Connections) refine styles.css the same way.
import "./styles/pages.css";
// The Map's diagrams (components/er).
import "./styles/diagram.css";
// The Code page's small IDE (activity bar, side bar, editor tabs, status bar).
import "./styles/repo.css";
import "./styles/graph.css";
import "./styles/helper.css";
import "./styles/plugins.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
