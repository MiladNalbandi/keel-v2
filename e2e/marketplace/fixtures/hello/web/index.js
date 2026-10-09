// The hello test plugin's web part (e2e/marketplace). Written by hand as plain JavaScript, in the shape that
// `npm run build:plugin` gives a plugin's web/index.tsx: an ES module that imports only react and @keel/web-sdk (keel's
// page hands it keel's own copies through the import map) and default-exports definePlugin({ name, setup }). setup()
// puts one page, "Hello", in the menu.

import { createElement as h } from "react";
import { definePlugin } from "@keel/web-sdk";

function HelloPage() {
  return h(
    "section",
    { className: "panel", "aria-label": "Hello" },
    h("h2", null, "Hello"),
    h("p", null, "The hello test plugin is loaded."),
  );
}

export function setup(sdk) {
  sdk.registerPage({
    id: "hello",
    label: "Hello",
    group: "control",
    order: 90,
    needsProject: false,
    icon: h("path", { d: "M5 12h14M12 5v14" }),
    component: HelloPage,
  });
}

export default definePlugin({ name: "hello", setup });
