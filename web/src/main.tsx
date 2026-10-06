import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";
// The shell and Run pages refine styles.css, so they load after it (an import from a component would load first).
import "./styles/run.css";
// The Project, Build and Control pages (Repo … Connections) refine styles.css the same way.
import "./styles/pages.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
