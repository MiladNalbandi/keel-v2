// The Code part (web/src/builtins.ts loads this file): its page in the menu (#/repo, also #/code) and what it adds to
// the launcher (the project's files, the code graph's hits, Focus mode). Its own side views and tabs are in Ide.tsx;
// the other parts add theirs through the slots code.activity, code.tab and assistant.

import { registerPage, registerSlot } from "../../sdk/registry";
import { SLOTS } from "../../sdk/slots";
import { RepoPage } from "../Repo";
import { codeLauncher } from "./launcher";

registerPage({
  id: "repo",
  label: "Code",
  group: "know",
  order: 10,
  aliases: ["code"],
  icon: <path d="M8 7l-5 5 5 5M16 7l5 5-5 5M13.5 5l-3 14" />,
  component: RepoPage,
});

registerSlot(SLOTS.launcherSource, codeLauncher);
