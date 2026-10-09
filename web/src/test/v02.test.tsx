// v0.2 screens: caps, YAML editing with an unsaved estimate, blockers, gate labels, guard unlock, init ladder, keel v1,
// per-flow cap, cross-project notifications, lanes, stacks, skills. The Code page's (repo update / history / unlock):
// plugins/code/web/test.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { Notification, ThreadState } from "../api";
import * as fx from "./fixtures";
import { db, FakeEventSource } from "./setup";

const at = (hash: string) => {
  location.hash = hash;
};
const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

describe("Budget caps", () => {
  it("adds, edits and deletes caps", async () => {
    const user = userEvent.setup();
    at("#/budget");
    render(<App />);
    const table = await screen.findByRole("table", { name: "Caps" });
    expect(await within(table).findByText("600k tokens")).toBeInTheDocument();
    expect(within(table).getByText("$100.00")).toBeInTheDocument();

    // add
    await user.click(screen.getAllByRole("button", { name: "Add cap" })[0]);
    const add = await screen.findByRole("dialog", { name: "Add cap" });
    await user.selectOptions(within(add).getByLabelText("What it limits"), "day");
    await user.type(within(add).getByLabelText("Limit"), "500k");
    await user.selectOptions(within(add).getByLabelText("When it is hit"), "stop");
    await user.click(within(add).getByRole("button", { name: "Add cap" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/caps")[0]?.body).toEqual({ scope: "day", unit: "tokens", limit: 500000, action: "stop" }));
    expect(await within(table).findByText("All flows, per day")).toBeInTheDocument();

    // edit
    await user.click(within(table).getByRole("button", { name: "Edit cap Each flow" }));
    const edit = await screen.findByRole("dialog", { name: "Edit cap" });
    const lim = within(edit).getByLabelText("Limit");
    await user.clear(lim);
    await user.type(lim, "700k");
    await user.click(within(edit).getByRole("button", { name: "Save cap" }));
    await waitFor(() => expect(calls("PUT", "/api/projects/ludus-engine/caps/c1")[0]?.body).toEqual({ id: "c1", scope: "flow", unit: "tokens", limit: 700000, action: "pause" }));
    expect(await within(table).findByText("700k tokens")).toBeInTheDocument();

    // delete asks first
    await user.click(within(table).getByRole("button", { name: "Delete cap API keys, per month" }));
    expect(calls("DELETE", "/api/projects/ludus-engine/caps/c2")).toHaveLength(0);
    await user.click(within(table).getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(calls("DELETE", "/api/projects/ludus-engine/caps/c2")).toHaveLength(1));
    await waitFor(() => expect(within(table).queryByText("$100.00")).not.toBeInTheDocument());
  });

  it("refuses an empty limit", async () => {
    const user = userEvent.setup();
    at("#/budget");
    render(<App />);
    await screen.findByRole("table", { name: "Caps" });
    await user.click(screen.getAllByRole("button", { name: "Add cap" })[0]);
    const add = await screen.findByRole("dialog", { name: "Add cap" });
    await user.click(within(add).getByRole("button", { name: "Add cap" }));
    expect(await within(add).findByText("Write a limit above 0.")).toBeInTheDocument();
    expect(calls("POST", "/api/projects/ludus-engine/caps")).toHaveLength(0);
  });
});

describe("Workflow YAML", () => {
  const yaml = "name: feature (keel)\nkeel_rules: true\nsteps:\n  - { id: s1, kind: agent, name: spec, agent: explorer }\n  - { id: s2, kind: gate, name: ok, lock: true }\n";

  it("edits YAML, estimates the unsaved text, shows validation errors, then saves", async () => {
    const user = userEvent.setup();
    at("#/workflows/feature");
    render(<App />);
    await user.click(await screen.findByRole("tab", { name: "YAML" }));
    const box = await screen.findByRole("textbox", { name: /Edit the workflow as text/ });
    fireEvent.change(box, { target: { value: yaml } });
    expect(screen.getByText("unsaved changes")).toBeInTheDocument();

    // debounced POST estimate with the unsaved YAML
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/estimate").at(-1)?.body).toEqual({ yaml, acs: 3 }), { timeout: 3000 });
    expect(await screen.findByText("Estimate with your changes")).toBeInTheDocument();
    expect(await screen.findByText("102k")).toBeInTheDocument();

    // validation errors from Save show under the editor
    db.yamlErrors = ["line 4: unknown kind 'agnet'", "line 5: gate needs a name"];
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("The workflow YAML is not valid.")).toBeInTheDocument();
    expect(screen.getByText("line 4: unknown kind 'agnet'")).toBeInTheDocument();
    expect(screen.getByText("line 5: gate needs a name")).toBeInTheDocument();
    expect(box).toHaveAttribute("aria-invalid", "true");

    db.yamlErrors = null;
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT", "/api/workflows/feature").at(-1)?.body).toMatchObject({ id: "feature", yaml }));
    expect(await screen.findByRole("button", { name: "Saved" })).toBeInTheDocument();
    expect(screen.queryByText("The workflow YAML is not valid.")).not.toBeInTheDocument();
  });
});

describe("Flow v0.2", () => {
  it("Before ship lists the blockers with why and fix", async () => {
    render(<App />);
    const head = await screen.findByRole("heading", { name: "Before ship" });
    const panel = head.closest(".panel") as HTMLElement;
    expect(within(panel).getByText("coverage")).toBeInTheDocument();
    expect(within(panel).getByText("No coverage verdict for HEAD yet.")).toBeInTheDocument();
    expect(within(panel).getByText("Fix: Run the coverage step: keel verify coverage.")).toBeInTheDocument();
    expect(within(panel).getByText("dependencies")).toBeInTheDocument();
  });

  it("Before ship says ready when nothing blocks", async () => {
    db.flows["ludus-engine"].thread!.blockers = [];
    render(<App />);
    const head = await screen.findByRole("heading", { name: "Before ship" });
    expect(within(head.closest(".panel") as HTMLElement).getByText("Nothing blocks shipping.")).toBeInTheDocument();
  });

  it("a new dependency waits with clear buttons and needs a reason to refuse", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = { step: "s6", kind: "fix", title: "Approve new dependency", detail: "io.ktor:ktor-server-core 3.0.1", options: ["approve", "reject"] };
    render(<App />);
    expect(await screen.findByRole("button", { name: "Approve the new dependency" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Refuse it" }));
    expect(calls("POST", "/api/threads/th_7f3a/resume")).toHaveLength(0);
    await user.type(screen.getByLabelText("Why (needed to refuse)"), "use the JDK client");
    await user.click(screen.getByRole("button", { name: "Refuse it" }));
    await waitFor(() => expect(calls("POST", "/api/threads/th_7f3a/resume")[0]?.body).toEqual({ decision: "reject", why: "use the JDK client" }));
  });

  it("an escalation gate says yes / no in words", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = { step: "c3", kind: "gate", title: "Escalate to a feature flow?", detail: "touches db/migration/V7.sql", options: ["approve", "reject"] };
    render(<App />);
    expect(await screen.findByRole("button", { name: "Yes, switch to a feature flow" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "No, keep the change flow" }));
    await waitFor(() => expect(calls("POST", "/api/threads/th_7f3a/resume")[0]?.body).toEqual({ decision: "reject" }));
  });

  it("a guard refusal offers 'Allow this file in this phase' with a confirm", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "Events" });
    act(() => FakeEventSource.emit("guard.refused", {
      type: "guard.refused", thread_id: "th_7f3a", project_id: "ludus-engine", at: new Date().toISOString(),
      data: { path: "api/build.gradle.kts", phase: "green", text: "write to build.gradle.kts in green" },
    }));
    await user.click(await screen.findByRole("button", { name: "Allow api/build.gradle.kts in this phase" }));
    const confirm = screen.getByRole("group", { name: "Confirm" });
    expect(confirm).toHaveTextContent(/logged/);
    await user.click(within(confirm).getByRole("button", { name: "Yes, allow it" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/unlock")[0]?.body).toEqual({ path: "api/build.gradle.kts", phase: "green" }));
  });

  it("the init flow draws the ladder from thread.ladder", async () => {
    const t = db.flows["ludus-engine"].thread!;
    db.flows["ludus-engine"] = {
      workflow: fx.initWorkflow,
      thread: { ...t, workflow_id: "init", status: "running", waiting: undefined, current: "i3", ladder: [
        { n: 2, name: "tests run", cmd: "./gradlew test", status: "fail", detail: "Docker is not running" },
        { n: 1, name: "toolchain", cmd: "java -version", status: "pass" },
      ] } as ThreadState,
    };
    render(<App />);
    expect(await screen.findByText("1 / 2 rungs")).toBeInTheDocument();
    expect(screen.getByText("toolchain")).toBeInTheDocument();
    expect(screen.getByText("Docker is not running")).toBeInTheDocument();
  });

  it("keel v1 is not offered any more: no Open in keel v1 on the flow or the map, the sidebar shows keel v2's version", async () => {
    render(<App />);
    expect(await screen.findByRole("button", { name: "Stop flow" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /keel v1/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/state\.json/)).not.toBeInTheDocument();
    const side = document.querySelector(".side") as HTMLElement;
    expect(await within(side).findByTitle("keel v2 version")).toHaveTextContent("v0.4.1");
    expect(side).not.toHaveTextContent("0.67");
  });

  it("Start a flow sends the cap with the flow, not as a project setting", async () => {
    const user = userEvent.setup();
    localStorage.setItem("keel2.project", "yegi");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Start a flow" }));
    const dlg = await screen.findByRole("dialog", { name: "Start a flow" });
    await user.type(within(dlg).getByLabelText("What to build"), "Rank next to top 10");
    await waitFor(() => expect(within(dlg).getByLabelText("Cap for this flow")).toHaveValue("500k"));
    const cap = within(dlg).getByLabelText("Cap for this flow");
    await user.clear(cap);
    await user.type(cap, "250k");
    await user.selectOptions(within(dlg).getByLabelText("When the cap is hit"), "stop");
    await user.click(within(dlg).getByRole("button", { name: "Start flow" }));
    await waitFor(() => expect(calls("POST", "/api/projects/yegi/flows")[0]?.body).toEqual({ workflow_id: "feature", title: "Rank next to top 10", cap_tokens: 250000, on_cap: "stop" }));
    expect(calls("PUT", "/api/projects/yegi/settings")).toHaveLength(0);
  });
});

describe("cross-project notifications", () => {
  const note = (over: Partial<Notification> = {}): Notification => ({
    id: "x-platform-1", type: "failed", project_id: "platform", title: "Guard reverted an edit in platform",
    body: "investigator tried to edit Tally.kt", link: "#/jobs", at: new Date().toISOString(), read: false, ...over,
  });

  const STREAM = "/api/events?project=ludus-engine&notify=all";
  async function ready() {
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    await waitFor(() => expect(FakeEventSource.instances.some((s) => s.url === STREAM && !s.closed)).toBe(true));
  }

  it("opens exactly one stream per tab: the project plus every project's notifications", async () => {
    await ready();
    const open = FakeEventSource.instances.filter((s) => !s.closed).map((s) => s.url);
    expect(open).toEqual([STREAM]);
  });

  it("a notification from another project pops up once, even when it arrives twice", async () => {
    await ready();
    act(() => FakeEventSource.emit("notification", note()));
    act(() => FakeEventSource.emit("notification", note()));
    const pops = await screen.findByTestId("popups");
    expect(within(pops).getAllByText("Guard reverted an edit in platform")).toHaveLength(1);
    expect(pops.querySelectorAll(".pop")).toHaveLength(1);
    expect(within(pops).getByText(/platform · investigator/)).toBeInTheDocument();
    expect(screen.getByTestId("unread")).toHaveTextContent("2");
  });

  it("a hidden tab gives its stream back after 30 s and reconnects when shown", async () => {
    await ready();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      act(() => { document.dispatchEvent(new Event("visibilitychange")); });
      act(() => { vi.advanceTimersByTime(31000); });
      expect(FakeEventSource.instances.filter((s) => !s.closed)).toHaveLength(0);
      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      act(() => { document.dispatchEvent(new Event("visibilitychange")); });
      expect(FakeEventSource.instances.filter((s) => !s.closed).map((s) => s.url)).toEqual([STREAM]);
    } finally {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      vi.useRealTimers();
    }
  });
});

describe("Build v0.2", () => {
  it("agent lane is saved with PUT, and the model list comes from the providers", async () => {
    const user = userEvent.setup();
    at("#/agents");
    render(<App />);
    await user.click(await screen.findByText("Writes the minimum code to make the failing test pass."));
    const dlg = await screen.findByRole("dialog", { name: "implementer" });
    await waitFor(() => expect(within(dlg).getByTestId("am-model")).toHaveTextContent("GPT-5 (Copilot)"));
    await user.selectOptions(within(dlg).getByLabelText("Lane"), "api");
    await user.click(within(dlg).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT", "/api/projects/ludus-engine/agents/implementer")[0]?.body).toEqual({ lane: "api" }));
  });

  it("New stack copies a stack; Install pack installs an installable one", async () => {
    const user = userEvent.setup();
    at("#/stacks");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "New stack" }));
    const dlg = await screen.findByRole("dialog", { name: "New stack" });
    await user.type(within(dlg).getByLabelText("Name"), "ts-react-custom");
    await user.selectOptions(within(dlg).getByLabelText("Start from"), "ts-react");
    await user.click(within(dlg).getByRole("button", { name: "Create stack" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/stacks")[0]?.body).toEqual({ name: "ts-react-custom", from: "ts-react" }));
    expect(await screen.findByRole("heading", { name: "ts-react-custom" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Install pack kotlin-spring" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/stacks/kotlin-spring/install")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Install pack kotlin-spring" })).not.toBeInTheDocument());
  });

  it("Skill import sends a URL or pasted SKILL.md", async () => {
    const user = userEvent.setup();
    at("#/skills");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Import" }));
    const dlg = await screen.findByRole("dialog", { name: "Import a skill" });
    await user.type(within(dlg).getByLabelText("Link to a SKILL.md"), "ftp://nope");
    await user.click(within(dlg).getByRole("button", { name: "Import" }));
    expect(await within(dlg).findByText(/starts with http/)).toBeInTheDocument();
    await user.click(within(dlg).getByRole("tab", { name: "Paste SKILL.md" }));
    fireEvent.change(within(dlg).getByLabelText("SKILL.md"), { target: { value: "---\nname: x\n---\n# x" } });
    await user.click(within(dlg).getByRole("button", { name: "Import" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/skills/import")[0]?.body).toEqual({ body: "---\nname: x\n---\n# x" }));
  });
});
