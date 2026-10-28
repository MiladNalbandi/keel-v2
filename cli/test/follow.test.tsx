// keel TUI 0.18.63: a window can keep its own project and KeelBot chat. Follow other keel windows (/follow, /settings,
// `follow` in keel TUI's settings file) is on at first: keel TUI goes where the dashboard goes, and the dashboard where
// keel TUI goes (sync.test.tsx). Off, it reads no shared focus, follows no other window and shares nothing.
import React from "react";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { render } from "ink-testing-library";
import { App, type Probe } from "../src/ui/App";
import { KeelCore } from "../src/core/keel";
import { run } from "../src/core/commands";
import { fileSettings, memorySettings, type TuiSettings } from "../src/core/settings";
import { makeTheme } from "../src/core/theme";
import { stripAnsi } from "../src/script";
import { stubKeel, tick, waitFor } from "./stub";

const none = makeTheme({ NO_COLOR: "1" }, 0);
const AT = "2026-10-16T10:00:00Z";

function setup(init: TuiSettings = {}, prep?: (s: ReturnType<typeof stubKeel>) => void) {
  const stub = stubKeel();
  prep?.(stub);
  const settings = memorySettings(init);
  const core = new KeelCore({ url: "http://keel.test", fetch: stub.fetch, noLive: true, cwd: "/nowhere", settings });
  const probe: Probe = { started: false, busy: false, pick: false, ask: false, chatBusy: false, answers: 0 };
  const r = render(<App core={core} probe={probe} width={120} theme={none} />);
  const frame = () => stripAnsi(r.lastFrame() ?? "");
  const event = (name: string, data: unknown) => core.onMessage({ event: name, data: JSON.stringify(data) });
  const shared = () => stub.calls.filter((c) => c.method === "PUT" && c.path === "/api/ui/focus");
  const type = async (line: string) => {
    t.r.stdin.write(line);
    await tick(60);
    t.r.stdin.write("\r");
  };
  const t = { stub, core, settings, probe, r, frame, event, shared, type };
  return t;
}

const withChat = (s: ReturnType<typeof stubKeel>) => {
  s.state.session = { id: "s1", project: "demo", root: "/work/demo", mode: "ask", title: "close all flows", model: { provider: "fake", mode: "api", model: "fake" },
    status: "idle", tokens_in: 0, tokens_out: 0, tokens_cached: 0, cost_usd: 0, turns: 1, created_at: AT, updated_at: AT };
  s.state.messages.push({ n: 1, role: "user", text: "close all flows", data: {}, at: AT }, { n: 2, role: "helper", text: "Two flows wait.", data: {}, at: AT });
};

describe("a keel TUI window that keeps its own project and chat", () => {
  it("follows the other windows at first: a project picked here is shared", async () => {
    const t = setup();
    await waitFor(() => t.probe.started);
    expect(t.core.follows).toBe(true);
    await t.core.useProject("alpha");
    await waitFor(() => t.shared().some((c) => (c.body as { project?: string }).project === "alpha"));
    t.r.unmount();
  });

  it("switched off: starts on its own project, not the dashboard's, and does not open the dashboard's chat", async () => {
    const t = setup({ follow: false }, (s) => {
      withChat(s);
      s.state.focus = { project: "zeta", chats: { demo: "s1", zeta: "s1" }, by: "web", client: "tab-1" };
    });
    await waitFor(() => t.probe.started);
    expect(t.core.snapshot.project?.id).toBe(t.core.snapshot.projects[0].id);
    expect(t.core.snapshot.project?.id).not.toBe("zeta");
    await tick(60);
    expect(t.core.snapshot.chat.session).toBeNull();
    expect(t.stub.calls.some((c) => c.path === "/api/ui/focus")).toBe(false);     // not read, not written
    t.r.unmount();
  });

  it("switched off: does not follow the dashboard to another project or chat", async () => {
    const t = setup({ follow: false }, withChat);
    await waitFor(() => t.probe.started);
    const own = t.core.snapshot.project?.id;
    t.event("ui.focus", { project: "alpha", chats: {}, by: "web", client: "tab-1" });
    await tick(80);
    expect(t.core.snapshot.project?.id).toBe(own);
    expect(t.frame()).not.toContain("following the dashboard");
    t.event("ui.focus", { project: own, chats: { [own!]: "s1" }, by: "web", client: "tab-1" });
    await tick(80);
    expect(t.core.snapshot.chat.session).toBeNull();
    t.r.unmount();
  });

  it("switched off: its own project and new chat move no other window", async () => {
    const t = setup({ follow: false });
    await waitFor(() => t.probe.started);
    expect(await t.core.useProject("alpha")).toBe(true);
    await t.core.useProject("demo");
    t.r.stdin.write("hi");
    await tick(40);
    t.r.stdin.write("\r");
    await waitFor(() => t.core.snapshot.chat.session?.id === "s1");
    await tick(60);
    expect(t.shared()).toEqual([]);
    expect(t.stub.state.focus.project).toBeNull();
    t.r.unmount();
  });

  it("/follow says which it is, /follow off keeps the choice and stops following, /follow on follows again", async () => {
    const t = setup();
    await waitFor(() => t.probe.started);
    await t.type("/follow");
    await waitFor(() => t.frame().includes("follow on: following the other keel windows"));
    await t.type("/follow off");
    await waitFor(() => t.frame().includes("follow off: this window keeps its own project and KeelBot chat"));
    expect(t.settings.get().follow).toBe(false);
    expect(t.core.follows).toBe(false);
    const own = t.core.snapshot.project?.id;
    t.event("ui.focus", { project: "alpha", chats: {}, by: "web", client: "tab-1" });
    await tick(80);
    expect(t.core.snapshot.project?.id).toBe(own);
    await t.type("/follow on");
    await waitFor(() => t.settings.get().follow === true);
    t.event("ui.focus", { project: "alpha", chats: {}, by: "web", client: "tab-1" });
    await waitFor(() => t.frame().includes("following the dashboard: project alpha"));
    await t.type("/follow maybe");
    await waitFor(() => t.frame().includes("/follow on or /follow off"));
    t.r.unmount();
  });

  it("/settings shows each setting with its value now, and Enter on Follow other keel windows switches it", async () => {
    const t = setup();
    await waitFor(() => t.probe.started);
    await t.type("/settings");
    await waitFor(() => t.probe.pick);
    await tick(60);
    expect(t.frame()).toContain("keel TUI settings");
    expect(t.frame()).toMatch(/Follow other keel windows\s+on/);
    expect(t.frame()).toMatch(/Sidebar\s+on/);
    expect(t.frame()).toMatch(/Mouse\s+off/);
    expect(t.frame()).toMatch(/Theme\s+keel/);
    t.r.stdin.write("\r");                         // the cursor starts on the first: follow
    await waitFor(() => t.settings.get().follow === false);
    await waitFor(() => t.frame().includes("follow off: this window keeps its own"));
    expect(t.core.follows).toBe(false);
    // again: the picker says off now
    await t.type("/settings");
    await waitFor(() => t.probe.pick);
    await tick(60);
    expect(t.frame()).toMatch(/Follow other keel windows\s+off/);
    t.r.unmount();
  });

  it("-c has no screen: /settings shows what keel TUI keeps (follow and the sidebar)", async () => {
    const stub = stubKeel();
    const core = new KeelCore({ url: "http://keel.test", fetch: stub.fetch, noLive: true, settings: memorySettings({ sidebar: false }) });
    await core.start();
    const r = await run({ core, width: 100 }, "/settings");
    expect(r.pick?.choices.map((c) => c.label)).toEqual(["Follow other keel windows  on", "Sidebar                    off"]);
    const after = await r.pick!.then("follow");
    expect(after.blocks[0].text).toMatch(/^follow off/);
    expect(core.settings.get().follow).toBe(false);
    core.stop();
  });
});

describe("keel TUI's settings file", () => {
  it("keeps follow for the next start, and a change from another window's store does not drop it", () => {
    const dir = mkdtempSync(join(tmpdir(), "keel-tui-settings-"));
    const env = { KEEL_TUI_SETTINGS: join(dir, "tui.json") };
    const a = fileSettings(env);          // this keel TUI
    const b = fileSettings(env);          // another keel TUI, started before the change
    a.set({ follow: false });
    b.set({ sidebar: false });
    expect(JSON.parse(readFileSync(env.KEEL_TUI_SETTINGS, "utf8"))).toEqual({ follow: false, sidebar: false });
    expect(fileSettings(env).get().follow).toBe(false);
    writeFileSync(env.KEEL_TUI_SETTINGS, "not json");
    expect(fileSettings(env).get().follow).toBeUndefined();      // the default: following
  });
});
