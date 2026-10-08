// The Graph part (web/src/builtins.ts loads this file): its page in the menu (#/graph).

import { GraphPage } from "../../pages/Graph";
import { registerPage } from "../../sdk/registry";

registerPage({
  id: "graph",
  label: "Graph",
  group: "know",
  order: 40,
  icon: (
    <>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="18" cy="7" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      <path d="M8.3 7l7.3-.2M7.2 8.3l3.7 7.5M16.9 9.3l-3.7 6.5" />
    </>
  ),
  component: GraphPage,
});
