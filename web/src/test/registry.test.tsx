// Step 2 (docs/plugins/09-step2-contract.md §5): the page registry and the slots in @keel/web-sdk, askAssistant, and
// that keel's menu and links stay exactly as they were now that every page comes from the registry.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  ASK_ASSISTANT_EVENT,
  askAssistant,
  pageFor,
  pageOf,
  registerPage,
  registerSlot,
  SLOTS,
  slotItems,
  useSlot,
  type AskAssistantDetail,
  type SettingsSectionItem,
  type SlotItem,
} from "@keel/web-sdk";
import { App } from "../App";
import { navGroups } from "../addons";
import "../corePages";
import { NavIcon } from "../components/NavIcons";
import { GROUP_HEADS, hashFor, menuGroups, parseHash } from "../routes";
import type { Features } from "../api";

const nav = () => screen.getByRole("navigation", { name: "Screens" });
// where KeelBot's own page finds the text another part handed over (its plugin: plugins/keelbot/web/model.ts)
const PREFILL_KEY = "keel2.keelbot.prefill";

describe("registerPage", () => {
  it("adds a page, finds it by id or alias, and takes it away again", () => {
    const Page = () => <p>demo</p>;
    const off = registerPage({
      id: "demo-page",
      label: "Demo",
      group: "know",
      order: 35,
      aliases: ["demo-alias"],
      component: Page,
    });
    try {
      expect(pageOf("demo-page")?.label).toBe("Demo");
      expect(pageFor("demo-alias")?.id).toBe("demo-page");
      expect(parseHash("#/demo-alias/x")).toEqual({
        page: "demo-page",
        arg: "x",
      });
      expect(
        menuGroups()
          .find((g) => g.id === "know")
          ?.pages.map(([id]) => id),
      ).toEqual(["repo", "helper", "map", "demo-page", "graph", "wiki"]);
    } finally {
      off();
    }
    expect(pageOf("demo-page")).toBeNull();
    // not a page any more: an add-on's screen, if one has it
    expect(parseHash("#/demo-page")).toEqual({
      page: "addon",
      screen: "demo-page",
      arg: undefined,
    });
  });

  it("a page that registers after the first render shows in the menu and opens", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "ludus-engine" });
    expect(
      within(nav()).queryByRole("link", { name: "Late page" }),
    ).not.toBeInTheDocument();
    let off = () => {};
    act(() => {
      off = registerPage({
        id: "late",
        label: "Late page",
        group: "build",
        order: 55,
        component: ({ pid }) => <h1>Late for {pid}</h1>,
      });
    });
    try {
      const link = await within(nav()).findByRole("link", {
        name: "Late page",
      });
      expect(link).toHaveAttribute("href", "#/late");
      await act(async () => {
        location.hash = "#/late";
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      });
      expect(
        await screen.findByRole("heading", { name: "Late for ludus-engine" }),
      ).toBeInTheDocument();
    } finally {
      act(() => off());
    }
  });
});

describe("registerSlot and useSlot", () => {
  it("keeps a slot's pieces by order, then by when they came; the same id replaces", () => {
    const offs = [
      registerSlot("test.slot", { id: "b", order: 20 }),
      registerSlot("test.slot", { id: "a", order: 10 }),
      registerSlot("test.slot", { id: "c", order: 20 }),
      registerSlot("test.slot", { id: "d" }),
    ];
    try {
      expect(slotItems("test.slot").map((i) => i.id)).toEqual([
        "d",
        "a",
        "b",
        "c",
      ]);
      // the same list until something changes
      expect(slotItems("test.slot")).toBe(slotItems("test.slot"));
      offs.push(
        registerSlot("test.slot", { id: "a", order: 30, title: "again" }),
      );
      expect(
        slotItems("test.slot").map(
          (i) => `${i.id}${i.title ? ":" + i.title : ""}`,
        ),
      ).toEqual(["d", "b", "c", "a:again"]);
    } finally {
      offs.forEach((off) => off());
    }
    expect(slotItems("test.slot")).toEqual([]);
  });

  it("useSlot is live: a piece that comes or goes renders again", () => {
    function Host() {
      const items = useSlot<SlotItem & { text: string }>("test.live");
      return (
        <ul aria-label="pieces">
          {items.map((i) => (
            <li key={i.id}>{i.text}</li>
          ))}
        </ul>
      );
    }
    render(<Host />);
    const list = screen.getByRole("list", { name: "pieces" });
    expect(list).toBeEmptyDOMElement();
    let off = () => {};
    act(() => {
      off = registerSlot("test.live", { id: "one", text: "first piece" });
    });
    expect(within(list).getByText("first piece")).toBeInTheDocument();
    act(() => off());
    expect(list).toBeEmptyDOMElement();
  });

  it("keel's built-in parts fill the step 2 slots", () => {
    const ids = (slot: string) => slotItems(slot).map((i) => i.id);
    expect(ids(SLOTS.connectionsKind)).toEqual([
      "jira",
      "github",
      "gitlab",
      "databases",
    ]);
    expect(ids(SLOTS.jobsTab)).toEqual(["pipelines"]);
    expect(ids(SLOTS.toolsCard)).toEqual(["jira-catalog"]);
    expect(ids(SLOTS.workflowActions).sort()).toEqual(["db", "git"]);
    expect(ids(SLOTS.codeActivity).sort()).toEqual(["db", "git", "review"]);
    expect(ids(SLOTS.codeTab).sort()).toEqual(["branch", "db", "review"]);
    expect(ids(SLOTS.assistant)).toEqual(["keelbot"]);
    expect(ids(SLOTS.launcherSource)).toEqual(["review", "code", "tasks"]);
    expect(ids(SLOTS.settingsSection)).toEqual([]);
    // step 3: the Tasks plugin's cards in the Inbox
    expect(ids(SLOTS.inboxCard)).toEqual(["tasks"]);
  });

  it("Settings shows a part's section after keel's own", async () => {
    const off = registerSlot<SettingsSectionItem>(SLOTS.settingsSection, {
      id: "demo",
      title: "Demo part",
      sub: "What the demo part does.",
      rows: [
        {
          key: "demo_mode",
          label: "Demo mode",
          kind: { t: "bool" },
          help: "Turns the demo on.",
          def: true,
        },
      ],
    });
    try {
      location.hash = "#/settings";
      render(<App />);
      const sec = await screen.findByRole("region", { name: "Demo part" });
      expect(
        within(sec).getByText("What the demo part does."),
      ).toBeInTheDocument();
      // the rows come once the settings have loaded (a moment after the section on a slow machine)
      expect(await within(sec).findByLabelText("Demo mode")).toHaveValue("1");
      const nav = screen.getByRole("navigation", { name: "Settings sections" });
      const names = within(nav)
        .getAllByRole("button")
        .map((b) => b.textContent);
      expect(names.indexOf("Demo part")).toBe(
        names.indexOf("This browser") - 1,
      );
    } finally {
      act(() => off());
    }
  });
});

describe("askAssistant", () => {
  it("hands the text to KeelBot with one window event; KeelBot keeps it for its own page", () => {
    const seen: AskAssistantDetail[] = [];
    const on = (e: Event) =>
      seen.push((e as CustomEvent<AskAssistantDetail>).detail);
    window.addEventListener(ASK_ASSISTANT_EVENT, on);
    try {
      askAssistant("/ci run #12", { run: 12 });
    } finally {
      window.removeEventListener(ASK_ASSISTANT_EVENT, on);
    }
    expect(ASK_ASSISTANT_EVENT).toBe("keel:ask-assistant");
    expect(seen).toEqual([{ text: "/ci run #12", context: { run: 12 } }]);
    expect(sessionStorage.getItem(PREFILL_KEY)).toBe("/ci run #12");
  });

  it("opens KeelBot's page with the text in its input", async () => {
    location.hash = "#/jobs";
    render(<App />);
    await screen.findByRole("heading", { name: "Jobs" });
    act(() => askAssistant("Why did the build fail?"));
    await act(async () => {
      location.hash = "#/keelbot";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "Ask KeelBot" })).toHaveValue(
        "Why did the build fail?",
      ),
    );
  });

  it("opens the Code page's KeelBot column with the text", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    await screen.findByRole("heading", { name: "Code" });
    expect(document.querySelector(".ide-help")).toBeNull();
    act(() => askAssistant("/pr"));
    await waitFor(() =>
      expect(document.querySelector(".ide-help")).not.toBeNull(),
    );
    expect(screen.getByRole("button", { name: "KeelBot" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "KeelBot" }));
    expect(document.querySelector(".ide-help")).toBeNull();
  });
});

describe("the menu and the links stay as they were", () => {
  const TODAY = [
    {
      id: "run",
      label: "Run",
      hint: "what is happening now",
      pages: [
        ["flow", "Flow"],
        ["tasks", "Tasks"],
        ["inbox", "Inbox"],
        ["live", "Live agents"],
        ["jobs", "Jobs"],
      ],
    },
    {
      id: "know",
      label: "Project",
      hint: "what this project is",
      pages: [
        ["repo", "Code"],
        ["helper", "KeelBot"],
        ["map", "Map"],
        ["graph", "Graph"],
        ["wiki", "Wiki"],
      ],
    },
    {
      id: "build",
      label: "Build",
      hint: "how agents work",
      pages: [
        ["workflows", "Workflows"],
        ["agents", "Agents"],
        ["skills", "Skill hub"],
        ["stacks", "Stacks"],
        ["tools", "Tools (MCP)"],
        ["quality", "Quality"],
      ],
    },
    {
      id: "control",
      label: "Control",
      hint: "cost, limits, accounts",
      pages: [
        ["budget", "Budget"],
        ["settings", "Settings"],
        ["connections", "Connections"],
      ],
    },
  ];

  it("has the same groups, pages, labels and order as before the registry", () => {
    expect(GROUP_HEADS.map((g) => g.id)).toEqual([
      "run",
      "know",
      "build",
      "control",
    ]);
    expect(menuGroups()).toEqual(TODAY);
  });

  it("keeps a Product-only keel's menu: the Inbox, KeelBot and Control", () => {
    const product: Features = {
      mode: "product",
      modes: ["dev", "product", "both"],
      parts: { dev: false, product: true },
      addons: [],
      screens: [],
      plugins: [],
    };
    expect(
      navGroups(product, "all").map((g) => [g.id, g.pages.map((p) => p.id)]),
    ).toEqual([
      ["run", ["inbox"]],
      ["know", ["helper"]],
      ["control", ["budget", "settings", "connections"]],
    ]);
  });

  it("opens every page by its link, and the aliases #/code and #/keelbot", () => {
    for (const g of TODAY)
      for (const [id] of g.pages)
        expect(parseHash(hashFor(id, "a/b"))).toEqual({ page: id, arg: "a/b" });
    expect(parseHash("#/projects")).toEqual({
      page: "projects",
      arg: undefined,
    });
    expect(parseHash("#/code/src/a.kt:3")).toEqual({
      page: "repo",
      arg: "src/a.kt:3",
    });
    expect(parseHash("#/keelbot")).toEqual({ page: "helper", arg: undefined });
    expect(parseHash("#/initiatives/INI-2")).toEqual({
      page: "addon",
      screen: "initiatives",
      arg: "INI-2",
    });
    expect(parseHash("#/")).toEqual({ page: "flow", arg: undefined });
    expect(parseHash("#/Not_A_Page")).toEqual({ page: "flow", arg: undefined });
  });

  it("gives every page its own icon in the folded menu (a part's comes with its page)", () => {
    const { container } = render(<NavIcon id="addon" />);
    const compass = container.innerHTML;
    for (const g of TODAY)
      for (const [id] of g.pages) {
        const { container: c } = render(<NavIcon id={id} />);
        expect(c.innerHTML, id).not.toBe(compass);
      }
  });
});
