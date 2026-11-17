// Settings › Plugins (v0.16.0, docs/plugins/13-step4-contract.md §9; v0.16.1 the look of docs/plugins/mockup.html):
// what this keel has installed, the marketplace, and where plugins come from. Three tabs, and a page per plugin:
//
//   Installed           keel core first (always on: no switch), then each plugin with its icon; the plugins that live
//                       inside another one's page (Git, Code Review, CI/CD and Database in Code) under it; version (and
//                       the update that is ready), parts, from, a status pill, on/off, Update, Roll back, Remove; "Check
//                       for updates" reads the catalogs again; a banner when changes wait for a restart, and (v0.17.1)
//                       the row of each change that waits says "applies after restart" with Restart now; a web or
//                       content-only plugin's change applies at once ("applied", or "applied — reload the page" when
//                       a web part left or changed: docs/plugins/22-one-plugins-page.md §5). v0.17.1: a plugin each
//                       project turns on (db, git, ci, review) has one control [ Off | This project | All projects ]
//                       in place of the switch, with "on in 2 other projects" under it (PluginProjects.tsx); any
//                       other plugin a switch with "On"/"Off" beside it
//   Marketplace         the catalog, search, the category chips, the trust legend; "The keel set" and the community's
//                       plugins as cards (Install / Update to x / Installed). No catalog yet: keel's own plugins from
//                       the image, with a note, never an empty page
//   Sources and rules   the catalogs (add one with its URL and public key, refresh, problems), the four rules, and
//                       install from a file in /data
//   #/settings/plugins/<name>    the plugin's own page (PluginDetail.tsx): about, permissions, versions, checks, needs, and
//                       (v0.17.1) the projects it is on for
//
// 0.16.1 plugin track (docs/plugins/21-workflow-marketplace.md §4): the Marketplace has a kind switch, Plugins ·
// Workflows (#/settings/plugins?tab=market&kind=workflows); a workflow's own page is #/settings/plugins/workflow/<name>
// (MarketWorkflow.tsx).
// Rule packs (docs/plugins/23-rules-in-marketplace.md §3) are the third kind: Plugins · Workflows · Rules
// (?kind=rules); a pack's page is #/settings/plugins/rules/<name> (MarketRulePack.tsx). keel 0.18.6: Skills is the fourth
// (?kind=skills, MarketSkill.tsx): keel's own skills and the catalogs'; a skill's page is #/settings/plugins/skills/<name>.
// keel 0.18.9: Stacks is the fifth (?kind=stacks, MarketStack.tsx); a stack's page is #/settings/plugins/stacks/<name>.
//
// Install opens a dialog that lists everything that will be installed and what each one may do.
// #/settings/plugins?set=developer (the core-only welcome's set cards) opens the install question for what that set adds at
// once (the missing plugins; the image's plugins that are off are turned on); nothing installs before the person presses
// Install. #/settings/plugins?tab=market|installed|sources opens that tab.

export { PluginsPage } from "./plugins/PluginsPage";
