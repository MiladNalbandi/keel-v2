// The slots of step 2 (docs/plugins/09-step2-contract.md §5): their names, and what a piece in each one carries.
// A part puts a piece in with registerSlot(SLOTS.jobsTab, { … }); the page that owns the slot reads it with useSlot.
//
//   Connections ─ connections.kind     Jobs ─ jobs.tab          Tools ─ tools.card       Settings ─ settings.section
//   Workflows ── workflow.actions      Code ─ code.activity, code.tab, assistant        ⌘K ─ launcher.source
//   Inbox ────── inbox.card (step 3)   Map (the Map plugin's page, step 3) ─ map.er.query
//   KeelBot ──── keelbot.card (step 3: a plugin's button for its blocks in an answer, ```keel-query```, ```keel-git```)
//   the shell ── nav.badge, shell.watch (0.15.2: KeelBot's count of new answers on its menu entry, and its watcher)
//   ⌘/ ───────── keys.area (0.15.4: a part's keys in the key cheat sheet: Code, Code Review, KeelBot)
//   the bell ─── notes.setting       Settings › This browser ─ settings.browser (0.15.2: KeelBot's own sound)

import type { ComponentType, ReactNode } from "react";
import type { GraphHit, HelperSelection } from "../api";
import type { KeyArea } from "../keys";
import type { InboxItem } from "../inboxApi";
import type { Item, Kind, Query, Recent } from "../components/launcher/model";
import type { Ctx, LauncherNotes } from "../components/launcher/sources";
import type { SlotItem } from "./registry";

export const SLOTS = {
  /** Connections: a section per kind of account (Jira, GitHub, GitLab, Databases) */
  connectionsKind: "connections.kind",
  /** Jobs: a tab next to "Agent calls" (Pipelines) */
  jobsTab: "jobs.tab",
  /** Tools: a card under the servers (Jira's MCP catalog) */
  toolsCard: "tools.card",
  /** Settings: a section of settings rows */
  settingsSection: "settings.section",
  /** Workflows: the action prefixes a plugin's blocks use (db:, git:) */
  workflowActions: "workflow.actions",
  /** Code: a view in the activity bar (Review, Database), or a panel in Source control (Git) */
  codeActivity: "code.activity",
  /** Code: an editor tab kind (a table, a review file, a branch) */
  codeTab: "code.tab",
  /** the assistant that answers askAssistant (KeelBot); the Code page shows it in a column */
  assistant: "assistant",
  /** ⌘K: results and actions a part adds (files, code, pull requests, tasks) */
  launcherSource: "launcher.source",
  /** Inbox: the card for items of a part's own kinds (the Tasks plugin's task items) */
  inboxCard: "inbox.card",
  /** Map › Database (ER): a panel under the diagram (the Database part's Query) — a slot of the Map plugin */
  mapErQuery: "map.er.query",
  /** KeelBot: the card for an answer's blocks of a kind no core card handles (the Database plugin's keel-query, the Git
   *  plugin's keel-git buttons) */
  keelbotCard: "keelbot.card",
  /** the menu (and the folded menu's icons): a part's count on one page's link (KeelBot's new answers on KeelBot) */
  navBadge: "nav.badge",
  /** the shell: a part's component mounted once on every page, that shows nothing (KeelBot watches for new answers) */
  shellWatch: "shell.watch",
  /** the key cheat sheet (⌘/): a part's area of keys (Code, Code Review, KeelBot), placed by its order */
  keysArea: "keys.area",
  /** the bell's drawer › Settings: a part's field after the sound (KeelBot's own sound) */
  notesSetting: "notes.setting",
  /** Settings › This browser: a part's row (KeelBot's own sound) */
  settingsBrowser: "settings.browser",
  /** keel 0.18.57 Settings: a part's own tab in the settings menu (the Database plugin's Databases), #/settings/<id> */
  settingsTab: "settings.tab",
} as const;

/** A part's piece is shown only while this project has the plugin on (Control › Plugins), when it names one. */
type ForPlugin = { plugin?: string };

// ---------- core pages ----------

/** Connections: one section (it shows its own title and help). */
export type ConnectionKindItem = SlotItem & {
  component: ComponentType<{ pid: string }>;
};

/** Jobs: a tab; #/jobs/<id> opens it. `sub` is the page's line under the title while it shows. */
export type JobsTabItem = SlotItem &
  ForPlugin & {
    title: string;
    sub: string;
    component: ComponentType<{ pid: string }>;
  };

/** Tools: a card under the servers. `onAdded` reads the servers again (it added one). */
export type ToolsCardItem = SlotItem & {
  component: ComponentType<{ pid: string; onAdded: () => void }>;
};

/** Settings: how a row edits its value. */
export type SettingKind =
  | { t: "select"; opts: [string, string][] }
  | { t: "bool"; on?: string; off?: string }
  | { t: "model" }
  | { t: "tokens" }
  | { t: "text"; suggest?: string[]; placeholder?: string }
  | { t: "list" };

/** Settings: one row. */
export type SettingRow = {
  key: string;
  label: string;
  kind: SettingKind;
  /** One line: what the setting does (a function when it depends on the value). */
  help: string | ((v: unknown) => string);
  /** shown when the api has no value yet (an older api) */
  def?: unknown;
  /** The help reads as a warning for this value. */
  warn?: (v: unknown) => boolean;
};

/** Settings: a section after keel's own ones (title, one line, its rows). A section whose values do not fit rows (a
 *  list of objects) draws itself with `component`, for the scope the page shows (General, or the project's own),
 *  and saves through its plugin's own checked routes. */
export type SettingsSectionItem = SlotItem & {
  title: string;
  sub: string;
  rows: SettingRow[];
  component?: ComponentType<SettingsSectionProps>;
  /** 0.18.74 "review": a project's own values are on Settings › Review (General keeps the keel-wide ones) */
  page?: "review";
};

/** What a section that draws itself gets: the project (empty for none) and the scope shown. */
export type SettingsSectionProps = { pid: string; scope: "general" | "project" };

/** Workflows: a code step whose action starts with `prefix` is a plugin's block: it takes `with:` settings, and the
 *  editor says so when the plugin is off. */
export type WorkflowActionsItem = SlotItem & ForPlugin & { prefix: string };

/** Inbox: what a card gets. `cardKey` goes on the card's root as data-key (with data-testid="inbox-item" and
 *  tabIndex -1): after an answer the Inbox moves the focus to the next card. */
export type InboxCardProps = {
  item: InboxItem;
  cardKey: string;
  /** the label of the item's kind (the same as in the Kind filter) */
  kindLabel: string;
  /** after an answer: the toast's words; the Inbox reads its list (and the projects' counts) again */
  onDone: (msg: string) => Promise<void>;
};

/** Inbox: the card for items of these kinds, with each kind's label (`{ task: "task", "jira-manual": "move in Jira" }`).
 *  An item of a kind no card claims shows as a flow's pause. */
export type InboxCardItem = SlotItem & {
  kinds: Record<string, string>;
  component: ComponentType<InboxCardProps>;
};

// ---------- the Code page ----------

export type CodeView = "code" | "diff" | "preview";
/** What to open in the editor: a file (no kind) or a tab of a kind ("db", "review", "branch", "commit"). */
export type CodeOpenSpec = {
  kind?: string;
  path: string;
  sha?: string;
  view?: CodeView;
};
export type CodeOpen = (
  spec: CodeOpenSpec,
  o?: { pin?: boolean; line?: number; col?: number; len?: number },
) => void;
/** An editor tab. */
export type CodeTabRef = {
  id: string;
  kind: string;
  path: string;
  sha?: string;
  view: CodeView;
};

/** A side view's props: the active editor tab, and how to open another. */
export type CodeActivityProps = {
  pid: string;
  active: CodeTabRef | null;
  open: CodeOpen;
};
/** A panel in Source control: how many files are uncommitted, and "something changed, read the commits again". */
export type ScmPanelProps = {
  pid: string;
  dirty: number;
  onChanged: () => void;
  /** 0.18.38 Source control lays the panel's parts out itself: a panel given `layout` returns layout(parts) instead of
   *  its own stack, so its push button sits under the branch, its commit box under the changes. */
  layout?: (parts: ScmPanelParts) => ReactNode;
  /** 0.18.38 the link that opens a pull request in keel's review (the Code Review plugin, when it is on) */
  reviewHref?: (pr: number) => string;
  /** 0.18.38 goes up when the person presses Refresh: read everything again (the pull request too) */
  refresh?: number;
};
/** The pieces of a Source control panel, each in its place (all optional: a part it has nothing for is left out). */
export type ScmPanelParts = {
  /** pills after the branch's own (pushed, n to push) */
  pills?: ReactNode;
  /** the buttons under the branch (Update from the base, Push) */
  actions?: ReactNode;
  /** the pull request section's body */
  pr?: ReactNode;
  /** the commit box, under the list of changes */
  commit?: ReactNode;
  /** a button in the Branches section's head (New branch) */
  branchActions?: ReactNode;
  /** the bottom of the panel (Ask KeelBot about the pull request) */
  footer?: ReactNode;
  /** lines added and deleted per uncommitted path, when the panel knows them */
  stats?: Record<string, { added: number; deleted: number }>;
};

export type CodeActivityItem = SlotItem &
  ForPlugin &
  (
    | {
        /** a view of its own in the activity bar (the default) */
        place?: "side";
        title: string;
        /** a Code page icon's name */
        icon: string;
        /** its name under the icon on a phone */
        short: string;
        /** its key after ⌘ in the title ("⇧G") */
        keys?: string;
        component: ComponentType<CodeActivityProps>;
        /** drawn over the whole IDE while the view is there (the review's popups and keys) */
        layer?: ComponentType<CodeActivityProps>;
        /** #/repo/@<id>/<value> opens this view with `value` (a review: #/repo/@review/pr:7) */
        onLink?: (pid: string, value: string) => void;
        /** its own side bar width, remembered in this browser under `key` (like Source control's); without it the view
         *  shares the other views' width. The Code page's splitter resizes it (drag, keys, double-click: `default`). */
        sideWidth?: { key: string; default: number; min?: number };
      }
    | {
        /** a panel in Source control (the Git plugin: commit, push, the pull request) */
        place: "scm";
        component: ComponentType<ScmPanelProps>;
      }
  );

export type CodeTabProps = {
  pid: string;
  tab: CodeTabRef;
  mode: "inline" | "split";
  open: CodeOpen;
  /** the tab now shows something else (a console moved to another database) */
  retarget: (spec: CodeOpenSpec) => void;
};
/** An editor tab kind: `id` is the kind (its tabs are "<kind>:<path>"). */
export type CodeTabItem = SlotItem & {
  /** the tab's name for its path */
  tabTitle: (path: string) => string;
  /** a Code page icon's name ("keel" when none) */
  icon?: string;
  component: ComponentType<CodeTabProps>;
};

/** What the assistant's panel gets from the page that shows it (the Code page's column). */
export type AssistantProps = {
  pid: string;
  /** the file open in the editor */
  openFile?: string | null;
  /** lines the person chose to ask about */
  selection?: HelperSelection | null;
  onClearSelection?: () => void;
  /** 0.18.57 `ref`: the branch, review ref or commit the answer was made from (the file as that ref has it) */
  onOpenFile: (path: string, line?: number, ref?: string | null) => void;
  onOpenDiff?: (path: string) => void;
  onClose: () => void;
  /** grows each time the page wants the input focused (⌘I) */
  focusKey?: number;
  /** text askAssistant handed over; n changes each time */
  prefill?: { text: string; n: number } | null;
};
export type AssistantItem = SlotItem & {
  title: string;
  component: ComponentType<AssistantProps>;
  /** a count on the assistant's button in the Code page (KeelBot's new answers), or nothing */
  count?: ComponentType;
};

// ---------- the Map plugin's page ----------

/** Map › Database (ER): a panel under the diagram, for the chosen project. */
export type MapErQueryItem = SlotItem & {
  component: ComponentType<{ pid: string }>;
};

// ---------- KeelBot ----------

/** One block of a KeelBot answer: ```keel-query {json}``` is { kind: "keel-query", body: "{json}" }. */
export type KeelbotBlock = { kind: string; body: string };
/** What a KeelBot card gets: the block, and the project the answer is about. */
export type KeelbotCardProps = { block: KeelbotBlock; pid: string };
/** KeelBot: the card (a button the person presses) for the blocks of one kind, the fence's word ("keel-query",
 *  "keel-git"). A block no card claims shows as code. */
export type KeelbotCardItem = SlotItem & {
  kind: string;
  component: ComponentType<KeelbotCardProps>;
};

// ---------- the shell (0.15.2) and the key cheat sheet (0.15.4) ----------

/** The menu: a count on the link of page `page` (its id: "helper"), in the menu (nav) or on the folded menu's icon
 *  (rail). It renders nothing when there is nothing to count. */
export type NavBadgeItem = SlotItem & {
  page: string;
  component: ComponentType<{ kind: "nav" | "rail" }>;
};

/** The shell mounts it once, on every page; it shows nothing (it watches the live events, plays a sound…). */
export type ShellWatchItem = SlotItem & { component: ComponentType };

/** The key cheat sheet: an area of keys. `order` places it among keel's own areas (Everywhere 10, the menu 20, the
 *  launcher 30, Flow 70, Workflows 80, the diagrams 90); `pages` are the pages it belongs to (none: every page). */
export type KeysAreaItem = SlotItem & KeyArea;

/** A field in the notification settings (the bell's drawer), or a row in Settings › This browser. */
export type NotesSettingItem = SlotItem & { component: ComponentType };
export type SettingsBrowserItem = SlotItem & { component: ComponentType };

/** keel 0.18.57 Settings: a part's tab. `title` is its name in the menu, `order` its place among keel's own (General 10,
 *  Models & logins 20, Connections 30, Budget 40, Plugins 50, Devices 70); `line` is the one line under its title. The
 *  component draws the tab's content (keel draws the head and the menu); `actions` go on the right of the head. */
export type SettingsTabItem = SlotItem & {
  title: string;
  line: string;
  component: ComponentType<{ pid: string }>;
  actions?: ComponentType<{ pid: string }>;
};

// ---------- the launcher (⌘K) ----------

/** What a part adds to the launcher. Every function is optional; the launcher keeps what `load` read for 30 s. */
export type LauncherSourceItem = SlotItem & {
  /** the kinds of results it gives ("file", "symbol", "pr", "task"): the launcher shows a scope (Files, Code, Pull
   *  requests, Tasks) only when a source gives its kinds. Not said: it may give any kind. */
  kinds?: Kind[];
  /** what it reads when the launcher opens; `notes.failed("Files")` turns a failure into one line under the results */
  load?: (pid: string, notes: LauncherNotes) => Promise<unknown>;
  /** its results for the query (`data` is what load read, null before; `code` is the code graph's hits) */
  items?: (
    ctx: Ctx,
    data: unknown,
    q: Query,
    more: {
      code: GraphHit[];
      filesPer: number;
    },
  ) => Item[];
  /** results that wait for the person (the empty launcher's first group) */
  waiting?: (ctx: Ctx, data: unknown) => Item[];
  /** a result the person used before, made again (Recent), or null when it is not one of its kinds */
  recent?: (ctx: Ctx, r: Recent) => Item | null;
  /** actions it adds (after keel's own) */
  actions?: (ctx: Ctx) => Item[];
  /** the link that opens a file at a line (KeelBot's answers in the launcher's Ask); 0.18.57 `ref`: as that branch,
   *  review ref or commit has it */
  fileHash?: (path: string, line?: number, ref?: string | null) => string;
};
