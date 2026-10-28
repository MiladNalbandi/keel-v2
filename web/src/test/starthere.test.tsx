// keel 0.18.66 Help › Start here (the five steps with what keel sees, step 3's MCP set-up, the short guides and their
// search), Settings › Access, the update notice and the first-run welcome.

import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { mcpSnippets, stepsOf } from "../components/StartSteps";
import { whatsNew } from "../components/UpdateNotice";
import { closeWelcome } from "../components/welcomeState";
import { GUIDES, longGuideUrl, parseGuide, searchGuides } from "../guides";
import { accessCommand } from "../pages/settings/Access";
import { parseHash } from "../routes";
import { db } from "./setup";

const now = new Date().toISOString();
const claudeCode = { client: "claude-code", via: "mcp", level: "write", first_at: now, last_at: now, last_tool: "keel_status", today: 3, total: 3 };

// userEvent.setup() gives the page a clipboard of its own: what a Copy button wrote is read back from it
const clip = () => navigator.clipboard.readText();
beforeEach(() => {
  closeWelcome();
  // neutral projects of this file's own
  db.projects = [
    { id: "my-app", name: "my-app", root: "/workspace/my-app", branch: "main", flow: null, phase: "none", acs: [0, 0], waiting: 0, running: 0 },
    { id: "docs-site", name: "docs-site", root: "/workspace/docs-site", branch: "main", flow: null, phase: "none", acs: [0, 0], waiting: 0, running: 0 },
    { id: "api-server", name: "api-server", root: "/workspace/api-server", branch: "main", flow: null, phase: "none", acs: [0, 0], waiting: 0, running: 0 },
  ];
});

const openStart = async () => {
  location.hash = "#/help";
  render(<App />);
  return screen.findByRole("heading", { level: 1, name: "Start here" });
};

describe("Help › Start here", () => {
  it("shows each step as keel sees it, with the count done", async () => {
    db.projects = db.projects.map((p) => ({ ...p, configured: p.id !== "my-app" }));
    await openStart();
    expect(await screen.findByText("2 of 5 done")).toBeInTheDocument();
    const project = screen.getByTestId("step-project");
    expect(within(project).getByRole("heading")).toHaveTextContent("(done)");
    expect(project).toHaveTextContent("Done: my-app and 2 more.");
    expect(screen.getByTestId("step-model")).toHaveTextContent("Done: Claude subscription (Claude Code CLI).");
    expect(screen.getByTestId("step-mcp")).toHaveTextContent("Waiting for the first call from Claude Code…");
    expect(screen.getByTestId("step-init")).toHaveTextContent("keel finds the stack and the test commands");
    expect(within(screen.getByTestId("step-init")).getByRole("button", { name: "Start init on my-app" })).toBeInTheDocument();
    expect(within(screen.getByTestId("step-flow")).getByRole("group", { name: "Your first flow" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start here" })).toHaveAttribute("aria-current", "page");
  });

  it("step 3: a tab per tool, a level, the exact command with Copy, and the dot turns green on the first call", async () => {
    const user = userEvent.setup();
    await openStart();
    const step = await screen.findByTestId("step-mcp");
    expect(within(step).getByLabelText("Claude Code command")).toHaveTextContent("claude mcp add keel -- keel2 mcp --write");
    await user.click(within(step).getByRole("radio", { name: /Read only/ }));
    expect(within(step).getByLabelText("Claude Code command")).toHaveTextContent(/^claude mcp add keel -- keel2 mcp$/);
    await user.click(within(step).getByRole("radio", { name: /Admin/ }));
    expect(within(step).getByRole("note")).toHaveTextContent("Pick it only when you need it.");
    await user.click(within(step).getByRole("tab", { name: "opencode" }));
    expect(within(step).getByLabelText("opencode config").textContent).toMatch(/"keel2",\s*"mcp",\s*"--admin"/);
    await user.click(within(step).getByRole("tab", { name: "Cursor / VS Code" }));
    expect(within(step).getByLabelText("Cursor / VS Code config 1")).toHaveTextContent(/"mcpServers"/);
    expect(within(step).getByLabelText("Cursor / VS Code config 2")).toHaveTextContent(/"servers"/);
    await user.click(within(step).getByRole("tab", { name: "Claude Code" }));
    await user.click(within(step).getByRole("button", { name: "Copy: Claude Code command" }));
    expect(await clip()).toBe("claude mcp add keel -- keel2 mcp --admin");
    expect(within(step).getByRole("button", { name: "Copy: Claude Code command" })).toHaveTextContent("Copied");
    expect(within(step).getByText("It does not connect")).toBeInTheDocument();
    expect(step).toHaveTextContent("keel2 doctor");
    expect(step).toHaveTextContent("Inbox");
    // the first call: keel's list of AI tools shows one (asked every 3 s)
    db.mcpClients = [claudeCode];
    expect(await within(step).findByText(/Connected\./, {}, { timeout: 5000 })).toBeInTheDocument();
    expect(within(step).getByTestId("mcp-live")).toHaveTextContent("keel saw claude-code (write)");
  }, 10_000);

  it("the short guides: the list, a search on title and text, and one guide", async () => {
    const user = userEvent.setup();
    await openStart();
    const guides = screen.getByRole("region", { name: "Short guides" });
    for (const t of ["Answer a gate", "Use keel from your phone", "Choose models per step", "Review a pull request",
      "Run your CI jobs before ship", "Rules: what agents may do", "The keel TUI", "Budgets and caps",
      "Connect an AI tool (MCP)", "Settings › Review", "The Supervisor", "Troubleshooting"]) {
      expect(within(guides).getByRole("link", { name: t })).toHaveAttribute("href", expect.stringMatching(/^#\/help\//));
    }
    expect(within(guides).getByRole("link", { name: "All of the guide (long) ›" })).toHaveAttribute("href", expect.stringContaining("docs/GUIDE.md"));
    await user.type(within(guides).getByRole("searchbox"), "tailscale");
    const hits = within(guides).getByRole("list", { name: "Search results" });
    expect(within(hits).getByRole("link", { name: "Use keel from your phone" })).toBeInTheDocument();
    await user.click(within(hits).getByRole("link", { name: "Use keel from your phone" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Use keel from your phone" })).toBeInTheDocument();
    expect(screen.getByTestId("guide")).toHaveTextContent("keel2 start --wifi");
  });

  it("each guide is short, starts with its title and a summary, and names nobody's own work", () => {
    expect(GUIDES.length).toBeGreaterThanOrEqual(12);
    for (const g of GUIDES) {
      expect(g.title, g.id).not.toBe(g.id);
      expect(g.summary.length, g.id).toBeGreaterThan(20);
      expect(g.text.split("\n").length, g.id).toBeLessThan(150);
      expect(g.text, g.id).not.toMatch(/192\.168\.178\.|\bshop\b/);
    }
    expect(parseGuide("x", "# Title\n\nOne line.\n\n## Part\ntext").summary).toBe("One line.");
    expect(searchGuides("")).toEqual([]);
    expect(searchGuides("gate")[0].guide.id).toBe("answer-a-gate");
    expect(longGuideUrl("0.18.66")).toBe("https://github.com/MiladNalbandi/keel-v2/blob/v0.18.66/docs/GUIDE.md");
    expect(longGuideUrl("dev")).toContain("/blob/main/");
  });

  it("the steps' lines", () => {
    const s = stepsOf(null, []);
    expect(s.map((x) => x.done)).toEqual([false, false, false, false, false]);
    expect(mcpSnippets("codex", "read")[0].text).toBe("codex mcp add keel -- keel2 mcp");
    expect(mcpSnippets("codex", "write")[1].text).toBe('[mcp_servers.keel]\ncommand = "keel2"\nargs = ["mcp", "--write"]');
    expect(JSON.parse(mcpSnippets("opencode", "write")[0].text).mcp.keel).toEqual({ type: "local", command: ["keel2", "mcp", "--write"], enabled: true });
    expect(JSON.parse(mcpSnippets("cursor", "read")[1].text).servers.keel).toEqual({ type: "stdio", command: "keel2", args: ["mcp"] });
  });
});

describe("Settings › Access", () => {
  const openAccess = async (hash = "#/settings/access") => {
    location.hash = hash;
    render(<App />);
    return screen.findByRole("heading", { level: 1, name: "Access" });
  };

  it("where keel answers: the current mode, and the exact keel2 command for another one", async () => {
    const user = userEvent.setup();
    await openAccess();
    const group = await screen.findByRole("radiogroup", { name: "Where keel answers" });
    await waitFor(() => expect(within(group).getByRole("radio", { name: /This computer only/ })).toHaveAttribute("aria-checked", "true"));
    expect(within(group).getByRole("radio", { name: /This computer only/ })).toHaveTextContent("now");
    expect(screen.getByRole("button", { name: "Save and restart keel" })).toBeDisabled();
    await user.click(within(group).getByRole("radio", { name: /My Wi-Fi/ }));
    expect(screen.getByLabelText("keel2 command")).toHaveTextContent("keel2 start --wifi /home/ana/code/my-app");
    expect(within(group).getByRole("radio", { name: /My Wi-Fi/ })).toHaveTextContent("plain http inside your network");
    await user.click(screen.getByRole("button", { name: "Copy: keel2 command" }));
    expect(await clip()).toBe("keel2 start --wifi /home/ana/code/my-app");
    await user.click(within(group).getByRole("radio", { name: /Tailscale/ }));
    expect(screen.getByLabelText("keel2 command")).toHaveTextContent("keel2 share tailscale");
  });

  it("shows the Wi-Fi addresses when keel answers there", async () => {
    db.access = { ...db.access, mode: "wifi", bind: "0.0.0.0", wifi: ["http://192.168.1.20:8080"], names: ["http://my-computer.local:8080"] };
    await openAccess();
    const wifi = await screen.findByRole("radio", { name: /My Wi-Fi/ });
    await waitFor(() => expect(wifi).toHaveTextContent("http://my-computer.local:8080 and http://192.168.1.20:8080"));
    expect(wifi).toHaveAttribute("aria-checked", "true");
  });

  it("the AI tools keel saw, and the updates part: check every day, the channel, the command", async () => {
    const user = userEvent.setup();
    db.mcpClients = [claudeCode, { ...claudeCode, client: "keel TUI 0.18.66", via: "tui", level: "you", today: 0 }];
    db.updates = { ...db.updates, latest: "0.18.67", newer: true, image: "ghcr.io/miladnalbandi/keel-v2:0.18.66",
      command: "KEEL_IMAGE=ghcr.io/miladnalbandi/keel-v2:0.18.67 keel2 update", command_why: "keel runs a pinned tag" };
    localStorage.setItem("keel2.update-dismissed", "0.18.67");
    await openAccess("#/settings/access?at=updates");
    const tools = await screen.findByRole("list", { name: "AI tools and terminal apps" });
    expect(tools).toHaveTextContent("claude-code · write");
    expect(tools).toHaveTextContent("3 calls today");
    expect(tools).toHaveTextContent("keel TUI 0.18.66 · as you");
    expect(screen.getByRole("link", { name: "Connect another tool (Help › Start here, step 3) ›" })).toHaveAttribute("href", "#/help");
    const updates = screen.getByRole("region", { name: "Updates" });
    expect(updates).toHaveTextContent("keel 0.18.66 · a newer one: 0.18.67");
    expect(within(updates).getByLabelText("update command")).toHaveTextContent("KEEL_IMAGE=ghcr.io/miladnalbandi/keel-v2:0.18.67 keel2 update");
    await user.click(within(updates).getByRole("checkbox", { name: "Check every day" }));
    await waitFor(() => expect(db.calls.some((c) => c.path === "/api/updates/settings" && (c.body as { check_daily: boolean }).check_daily === false)).toBe(true));
    await user.selectOptions(within(updates).getByRole("combobox"), "product");
    await waitFor(() => expect(db.updates.channel).toBe("product"));
    await user.click(within(updates).getByRole("button", { name: "Check now" }));
    await waitFor(() => expect(db.calls.some((c) => c.path === "/api/updates/check")).toBe(true));
  });

  it("the keel2 commands per mode", () => {
    const base = { mode: "local" as const, folder: "/home/ana/my app", native: false, tailscale: null, tunnel: null, wifi: [], bind: "127.0.0.1" };
    expect(accessCommand("wifi", base)).toBe("keel2 start --wifi '/home/ana/my app'");
    expect(accessCommand("tailscale", base)).toBe("keel2 share tailscale");
    expect(accessCommand("tailscale", { ...base, mode: "wifi", bind: "0.0.0.0" })).toBe("keel2 start '/home/ana/my app'\nkeel2 share tailscale");
    expect(accessCommand("local", { ...base, mode: "tailscale", tailscale: "https://x" })).toBe("keel2 start '/home/ana/my app'\nkeel2 share off");
    expect(accessCommand("wifi", { ...base, native: true, folder: null })).toBe("keel2 start --native --wifi");
    expect(parseHash("#/settings/devices")).toMatchObject({ page: "settings", arg: "access", redirect: "#/settings/access" });
    expect(parseHash("#/devices")).toMatchObject({ page: "settings", arg: "access" });
  });
});

describe("the update notice", () => {
  const newer = () => {
    db.updates = { ...db.updates, current: "0.18.65", latest: "0.18.66", newer: true,
      notes: "## What's new\n- **Settings › Access**: where keel answers\n- Help › Start here\n- a [notice](https://x) for updates\n- one\n- two" };
  };

  it("shows once per version: what is new, the running flows, Copy the update command, Later", async () => {
    const user = userEvent.setup();
    newer();
    db.projects = db.projects.map((p, i) => ({ ...p, flows: i === 0 ? 2 : 0 }));
    location.hash = "#/help";
    const { unmount } = render(<App />);
    const pop = await screen.findByRole("dialog", { name: "keel 0.18.66 is out" });
    expect(pop).toHaveTextContent("You run 0.18.65. What is new:");
    expect(within(pop).getAllByRole("listitem").map((li) => li.textContent)).toEqual(
      ["Settings › Access: where keel answers", "Help › Start here", "a notice for updates", "one"]);
    expect(within(pop).getByRole("link", { name: "All changes ›" })).toHaveAttribute("href", expect.stringContaining("github.com"));
    await waitFor(() => expect(pop).toHaveTextContent("2 flows are running. keel restarts for the update (about 30 s); they go on after it."));
    expect(within(pop).getByRole("link", { name: "Settings › Updates" })).toHaveAttribute("href", "#/settings/access?at=updates");
    await user.click(within(pop).getByRole("button", { name: "Copy the update command" }));
    expect(await clip()).toBe("keel2 update");
    await user.click(within(pop).getByRole("button", { name: "Later" }));
    expect(screen.queryByRole("dialog", { name: "keel 0.18.66 is out" })).toBeNull();
    expect(localStorage.getItem("keel2.update-dismissed")).toBe("0.18.66");
    unmount();
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Start here" });
    await act(async () => new Promise((r) => setTimeout(r, 50)));
    expect(screen.queryByRole("dialog", { name: "keel 0.18.66 is out" })).toBeNull();
    // the next version shows again
    db.updates = { ...db.updates, latest: "0.18.67" };
    cleanupAndRender();
    expect(await screen.findByRole("dialog", { name: "keel 0.18.67 is out" })).toBeInTheDocument();
  });

  it("nothing when keel runs the newest", async () => {
    await openStart();
    await act(async () => new Promise((r) => setTimeout(r, 50)));
    expect(screen.queryByRole("dialog", { name: /is out/ })).toBeNull();
  });

  it("the first points of the notes", () => {
    expect(whatsNew("## x\nFirst line.\n\nSecond `one`.\n| a |")).toEqual(["First line.", "Second one."]);
    expect(whatsNew("* a\n* b\n1. c", 2)).toEqual(["a", "b"]);
  });
});

function cleanupAndRender() {
  cleanup();
  render(<App />);
}

describe("the first-run welcome", () => {
  it("opens the first time, walks the five steps, and is remembered by keel when finished", async () => {
    const user = userEvent.setup();
    db.welcome = { seen: false };
    db.mcpClients = [];
    render(<App />);
    const w = await screen.findByRole("dialog", { name: "Welcome to keel" });
    expect(within(w).getByRole("heading", { level: 1 })).toHaveTextContent("Your AI agents,with a process you trust.");
    expect(within(w).getByTestId("tour-video")).toHaveAttribute("src", expect.stringContaining("keel-v2-explainer.mp4"));
    expect(within(w).getByRole("button", { name: "Play the 4-minute tour" })).toBeInTheDocument();
    expect(within(w).getByRole("button", { name: "Skip, I know keel" })).toBeInTheDocument();
    await user.click(within(w).getByRole("button", { name: "Set up keel · 5 steps, ~5 min" }));
    expect(await within(w).findByText("Step 1 of 5 · done")).toBeInTheDocument();
    expect(within(w).getByLabelText("Setup progress: step 1 of 5")).toBeInTheDocument();
    await user.click(within(w).getByRole("button", { name: "Next" }));
    expect(within(w).getByRole("heading", { level: 1, name: "Connect a model" })).toBeInTheDocument();
    await user.click(within(w).getByRole("button", { name: "Next" }));
    expect(within(w).getByRole("heading", { level: 1, name: "Connect your AI tool (optional)" })).toBeInTheDocument();
    expect(within(w).getByLabelText("Claude Code command")).toHaveTextContent("claude mcp add keel -- keel2 mcp --write");
    await user.click(within(w).getByRole("button", { name: "Next" }));
    expect(within(w).getByRole("heading", { level: 1, name: "Let keel learn the project (init)" })).toBeInTheDocument();
    await user.click(within(w).getByRole("button", { name: "Next" }));
    expect(within(w).getByRole("heading", { level: 1, name: "Your first flow" })).toBeInTheDocument();
    expect(within(w).getByRole("radio", { name: /clarify/ })).toBeChecked();
    expect(within(w).getByRole("button", { name: "Start the flow" })).toBeInTheDocument();
    await user.click(within(w).getByRole("button", { name: "Next" }));
    expect(await within(w).findByRole("heading", { level: 1, name: "keel is ready" })).toBeInTheDocument();
    await waitFor(() => expect(db.welcome).toMatchObject({ seen: true, how: "finished" }));
    await user.click(within(w).getByRole("button", { name: "Open Start here" }));
    expect(screen.queryByRole("dialog", { name: "Welcome to keel" })).toBeNull();
    expect(location.hash).toBe("#/help");
  });

  it("Skip is always there and is remembered; Help › Start here opens it again", async () => {
    const user = userEvent.setup();
    db.welcome = { seen: false };
    location.hash = "#/help";
    render(<App />);
    const w = await screen.findByRole("dialog", { name: "Welcome to keel" });
    await user.click(within(w).getByRole("button", { name: "Skip, I know keel" }));
    expect(screen.queryByRole("dialog", { name: "Welcome to keel" })).toBeNull();
    await waitFor(() => expect(db.welcome).toMatchObject({ seen: true, how: "skipped" }));
    await user.click(screen.getByRole("button", { name: "See the welcome again" }));
    expect(await screen.findByRole("dialog", { name: "Welcome to keel" })).toBeInTheDocument();
  });

  it("the update notice waits while the welcome is open", async () => {
    const user = userEvent.setup();
    db.welcome = { seen: false };
    db.updates = { ...db.updates, current: "0.18.65", latest: "0.18.66", newer: true, notes: "- one" };
    render(<App />);
    const w = await screen.findByRole("dialog", { name: "Welcome to keel" });
    await act(async () => new Promise((r) => setTimeout(r, 50)));
    expect(screen.queryByRole("dialog", { name: "keel 0.18.66 is out" })).toBeNull();
    await user.click(within(w).getByRole("button", { name: "Skip, I know keel" }));
    expect(await screen.findByRole("dialog", { name: "keel 0.18.66 is out" })).toBeInTheDocument();
  });

  it("not when keel saw it already, nor for a keel that has flows", async () => {
    render(<App />);
    await act(async () => new Promise((r) => setTimeout(r, 50)));
    expect(screen.queryByRole("dialog", { name: "Welcome to keel" })).toBeNull();
    cleanupAndRender();
    db.welcome = { seen: false };
    db.startFlow = { thread_id: "t-1", project_id: "my-app", workflow: "feature", title: "Prices with tax", status: "done" };
    cleanupAndRender();
    await act(async () => new Promise((r) => setTimeout(r, 80)));
    expect(screen.queryByRole("dialog", { name: "Welcome to keel" })).toBeNull();
  });

  it("the tour: offline, the cover stays and a link to GitHub shows", async () => {
    const user = userEvent.setup();
    db.welcome = { seen: false };
    const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    render(<App />);
    const w = await screen.findByRole("dialog", { name: "Welcome to keel" });
    await user.click(within(w).getByRole("button", { name: "Play the 4-minute tour" }));
    expect(within(w).getByRole("status")).toHaveTextContent("The tour cannot play here.");
    expect(within(w).getByRole("link", { name: "Watch it on GitHub ›" })).toHaveAttribute("href", expect.stringContaining("keel-v2-explainer.mp4"));
    expect(within(w).getByRole("img", { name: /The 4-minute tour of keel/ })).toBeInTheDocument();
    online.mockRestore();
  });
});
