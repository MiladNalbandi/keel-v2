// The Supervisor plugin's Jobs › Supervisor inside keel's web (web/src/test/setup.ts runs every plugin's setup()): the
// audit log with each row's result and reason, the waiting flow's switch and mode, and the empty state.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { AppProvider } from "../../../../web/src/state";
import { slotItems, SLOTS } from "../../../../web/src/sdk";
import { server } from "../../../../web/src/test/setup";
import plugin from "../index";
import { outcomeText, type Decision, type Defaults, type FlowStatus, type Note } from "../Supervisor";
import { SupervisorChip, SupervisorNote, SupervisorSettingsRow } from "../SupervisorParts";

const row = (over: Partial<Decision>): Decision => ({
  id: "d1", thread_id: "t-sup", step: "g1", ac: "AC-1", kind: "ac", action: "answer", decision: "approve", choice: null,
  why: "supervisor: the test passes", confidence: "high", evidence: ["src/a.ts:3"], code: null,
  summary: "Test and review are clean.", tokens: 1234, outcome: "applied", at: new Date().toISOString(), ...over,
});

function api(decisions: Decision[], posted: unknown[] = []) {
  let keel = false;
  let own: boolean | null = null;
  let flowOn: boolean | null = null;
  let mode = "shadow";
  const project = () => ({ on: keel && (own ?? keel), keel, project: own ?? keel, own, plugin: true });
  const settings = () => {
    const on = keel && (flowOn ?? own ?? keel);
    return { on, mode, cap_tokens: 150000, switches: { keel, plugin: true, project: own ?? keel, flow: flowOn,
      off_by: on ? null : !keel ? "keel" : flowOn === false ? "flow" : "project" } };
  };
  server.use(
    http.get("/api/supervisor/switch", () => HttpResponse.json({ on: keel })),
    http.post("/api/supervisor/switch", async ({ request }) => {
      const body = (await request.json()) as { on: boolean };
      posted.push({ keel: body.on });
      keel = body.on;
      return HttpResponse.json({ on: keel });
    }),
    http.get("/api/projects/:pid/supervisor/switch", () => HttpResponse.json(project())),
    http.post("/api/projects/:pid/supervisor/switch", async ({ request }) => {
      const body = (await request.json()) as { on: boolean | null };
      posted.push({ project: body.on });
      own = body.on;
      return HttpResponse.json(project());
    }),
    // the plugin is on for the project (per project, Control › Plugins): Jobs shows its tab
    http.get("/api/projects/:pid/plugins", () =>
      HttpResponse.json([{ name: "supervisor", title: "Supervisor", description: "", needs: [], tools: {}, actions: [],
        shows_in: ["jobs"], commands: [], enabled: true, scope: "project" }])),
    http.get("/api/projects/:pid/supervisor/decisions", () => HttpResponse.json({ decisions })),
    http.get("/api/projects/:pid/flow", () =>
      HttpResponse.json({ thread: { thread_id: "t-sup", title: "Player ranks", status: "waiting" } })),
    http.get("/api/projects/:pid/flows/:tid/supervisor", () => HttpResponse.json({ settings: settings(), used: { tokens: 1234 } })),
    http.post("/api/projects/:pid/flows/:tid/supervisor", async ({ request }) => {
      const body = (await request.json()) as { on?: boolean | null; mode?: string };
      posted.push(body);
      if ("on" in body) flowOn = body.on ?? null;
      if (body.mode) mode = body.mode;
      return HttpResponse.json({ settings: settings(), used: { tokens: 1234 } });
    }),
  );
}

describe("the Supervisor plugin's web part", () => {
  it("is the plugin supervisor, with a tab in Jobs", () => {
    expect(plugin.name).toBe("supervisor");
    const tabs = slotItems<{ id: string; plugin?: string }>(SLOTS.jobsTab);
    expect(tabs.find((t) => t.id === "supervisor")?.plugin).toBe("supervisor");
  });

  it("says each result in plain words", () => {
    expect(outcomeText(row({}))).toBe("answered approve");
    expect(outcomeText(row({ action: "send_back", decision: "reject" }))).toBe("sent back");
    expect(outcomeText(row({ outcome: "escalated" }))).toBe("asked you");
    expect(outcomeText(row({ outcome: "suggested" }))).toBe("suggested");
    expect(outcomeText(row({ outcome: "stale" }))).toBe("too late (you answered)");
  });

  it("lists its decisions", async () => {
    api([row({}), row({ id: "d2", outcome: "escalated", code: "low-confidence", summary: "Spec says 410, test 404." })]);
    location.hash = "#/jobs/supervisor";
    render(<App />);
    const table = await screen.findByRole("table", { name: "Supervisor decisions" });
    expect(within(table).getByText("answered approve")).toBeInTheDocument();
    expect(within(table).getByText("Spec says 410, test 404.")).toBeInTheDocument();
  });

  it("shows a suggested own_fix's commands and sends them as your answer", async () => {
    // 0.18.64 a setup rung whose fix failed: the Supervisor's better fix is one click away
    const resumed: unknown[] = [];
    api([row({ id: "d3", step: "rung_gate", ac: null, kind: "rung", outcome: "escalated", code: "policy-escalate",
      choice: "own_fix", commands: ["tools/lock.py --all"], summary: "The lock is older than the manifest." })]);
    server.use(http.post("/api/threads/:tid/resume", async ({ request, params }) => {
      resumed.push({ tid: params.tid, ...((await request.json()) as object) });
      return HttpResponse.json({ ok: true });
    }));
    const user = userEvent.setup();
    location.hash = "#/jobs/supervisor";
    render(<App />);
    const table = await screen.findByRole("table", { name: "Supervisor decisions" });
    expect(within(table).getByLabelText("Suggested commands")).toHaveTextContent("$ tools/lock.py --all");
    await user.click(within(table).getByRole("button", { name: "Run these commands" }));
    await waitFor(() => expect(resumed).toEqual([{ tid: "t-sup", decision: "approve", why: "the Supervisor's commands",
      payload: { choice: "own_fix", commands: ["tools/lock.py --all"] } }]));
    expect(await within(table).findByText(/Sent: keel runs them/)).toBeInTheDocument();
  });

  it("is off until you turn it on keel-wide; then a project and a flow may say otherwise", async () => {
    const posted: unknown[] = [];
    api([], posted);
    const user = userEvent.setup();
    location.hash = "#/jobs/supervisor";
    render(<App />);
    const sw = await screen.findByRole("region", { name: "Supervisor switches" });
    expect(await within(sw).findByText(/does not run here: it is off keel-wide/)).toBeInTheDocument();
    const box = await screen.findByRole("region", { name: "Supervisor for this flow" });
    expect(await within(box).findByText(/does not supervise this flow: it is off keel-wide/)).toBeInTheDocument();

    await user.click(within(sw).getByRole("checkbox", { name: "On for the whole keel" }));
    await waitFor(() => expect(posted).toEqual([{ keel: true }]));
    expect(await within(sw).findByText("The Supervisor runs in this project.")).toBeInTheDocument();
    expect(await within(box).findByText("It supervises this flow.")).toBeInTheDocument();

    await user.selectOptions(within(sw).getByRole("combobox", { name: "This project" }), "off");
    await waitFor(() => expect(posted).toContainEqual({ project: false }));
    expect(await within(box).findByText(/does not supervise this flow: it is off for this project/)).toBeInTheDocument();

    await user.selectOptions(within(box).getByRole("combobox", { name: "This flow" }), "on");
    await waitFor(() => expect(posted).toContainEqual({ on: true }));
    expect(await within(box).findByText("It supervises this flow.")).toBeInTheDocument();
    await user.selectOptions(within(box).getByRole("combobox", { name: "Mode" }), "act");
    await waitFor(() => expect(posted).toContainEqual({ mode: "act" }));

    await user.click(within(sw).getByRole("checkbox", { name: "On for the whole keel" }));
    await waitFor(() => expect(posted).toContainEqual({ keel: false }));
    expect(await within(box).findByText(/does not supervise this flow: it is off keel-wide/)).toBeInTheDocument();
  });

  it("says what it does when it has not looked at anything yet", async () => {
    api([]);
    location.hash = "#/jobs/supervisor";
    render(<App />);
    expect(await screen.findByText("The Supervisor has not looked at anything yet.")).toBeInTheDocument();
  });
});

describe("0.21.0 the Supervisor's chip, note and settings row", () => {
  const thread = (status: string) => ({ thread_id: "t-sup", project_id: "p1", status, waiting: { id: "q1" } }) as never;
  const flowStatus = (over: Partial<FlowStatus> = {}): FlowStatus => ({ state: "watching", on: true, off_by: null, mode: "shadow", note: null, ...over });
  const route = "/api/projects/p1/flows/t-sup/supervisor/status";

  it.each([
    ["watching", "Supervisor: watching"],
    ["looking", "Supervisor: looking…"],
    ["acted", "Supervisor: acted"],
    ["escalated", "Supervisor: escalated"],
    ["paused-budget", "Supervisor: paused (budget)"],
    ["paused-repeats", "Supervisor: paused (repeats)"],
    ["off", "Supervisor: off"],
  ] as [FlowStatus["state"], string][])("the chip says %s", async (state, text) => {
    server.use(http.get(route, () => HttpResponse.json(flowStatus({ state, off_by: state === "off" ? "project" : null }))));
    render(<AppProvider><SupervisorChip pid="p1" thread={thread("running")} readOnly={false} /></AppProvider>);
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.getByTestId("supervisor-chip")).toHaveAttribute("href", "#/jobs/supervisor");
  });

  it("no chip while it is off keel-wide, for an earlier run, or when the call fails", async () => {
    let asked = 0;
    server.use(http.get(route, () => { asked += 1; return HttpResponse.json(flowStatus({ state: "off", off_by: "keel" })); }));
    const chip = (status: string, readOnly: boolean) => <AppProvider><SupervisorChip pid="p1" thread={thread(status)} readOnly={readOnly} /></AppProvider>;
    const { rerender } = render(chip("running", false));
    await waitFor(() => expect(asked).toBeGreaterThan(0));
    expect(screen.queryByTestId("supervisor-chip")).toBeNull();
    rerender(chip("running", true));
    expect(screen.queryByTestId("supervisor-chip")).toBeNull();
    server.use(http.get(route, () => HttpResponse.json({ error: "gone" }, { status: 503 })));
    rerender(chip("waiting", false));
    await waitFor(() => expect(screen.queryByTestId("supervisor-chip")).toBeNull());
  });

  const item = { project_id: "p1", thread_id: "t-sup", id: "q1" } as never;
  const note = (over: Partial<Note> = {}): Note => ({ summary: "Test and review are clean.", code: "shadow", reason: "shadow mode: it only suggests", confidence: "high",
    suggestion: { decision: "approve", choice: "retry", answers: {}, why: "the test passes" }, patch: null, ...over });

  it("Use suggestion only fills in: it sends nothing", async () => {
    const posted: unknown[] = [];
    server.use(http.get(route, () => HttpResponse.json(flowStatus({ note: note() }))),
      http.post("/api/threads/:tid/resume", async ({ request }) => { posted.push(await request.json()); return HttpResponse.json({}); }));
    const filled: unknown[] = [];
    render(<AppProvider><SupervisorNote item={item} fill={(f) => filled.push(f)} /></AppProvider>);
    expect(await screen.findByText(/Test and review are clean/)).toBeInTheDocument();
    expect(screen.getByText(/It suggests: retry/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Use suggestion" }));
    expect(filled).toEqual([{ why: "the test passes", choice: "retry", answers: undefined }]);
    expect(screen.getByRole("status")).toHaveTextContent("Nothing is sent until you send it");
    expect(posted).toEqual([]);
  });

  it("a note with no answer has no button, and a fix it put back shows its patch", async () => {
    server.use(http.get(route, () => HttpResponse.json(flowStatus({ note: note({ suggestion: null, code: "fix-too-big", reason: "its fix is over the size limit, so it put it back", patch: "--- a/src/x.py" }) }))));
    render(<AppProvider><SupervisorNote item={item} fill={() => undefined} /></AppProvider>);
    expect(await screen.findByText("--- a/src/x.py")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use suggestion" })).toBeNull();
  });

  it("no note when it said nothing about the waiting question", async () => {
    let asked = 0;
    server.use(http.get(route, () => { asked += 1; return HttpResponse.json(flowStatus()); }));
    render(<AppProvider><SupervisorNote item={item} fill={() => undefined} /></AppProvider>);
    await waitFor(() => expect(asked).toBeGreaterThan(0));
    expect(screen.queryByTestId("supervisor-note")).toBeNull();
  });

  it("Settings' row changes the keel-wide mode and stall time, and warns about fix mode", async () => {
    const sent: Record<string, unknown>[] = [];
    let d: Defaults = { on: false, mode: "shadow", stall_minutes: 30, modes: ["shadow", "retry", "act", "fix"], stall_min: 5, stall_max: 1440 };
    server.use(http.get("/api/supervisor/defaults", () => HttpResponse.json(d)),
      http.post("/api/supervisor/defaults", async ({ request }) => {
        const b = (await request.json()) as Record<string, unknown>;
        sent.push(b);
        d = { ...d, ...b };
        return HttpResponse.json(d);
      }));
    render(<AppProvider><SupervisorSettingsRow pid="" scope="general" /></AppProvider>);
    expect(await screen.findByLabelText("Mode")).toHaveValue("shadow");
    expect(screen.getByLabelText("On for the whole keel")).not.toBeChecked();
    expect(screen.queryByRole("note")).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Mode"), "fix");
    expect(await screen.findByRole("note")).toHaveTextContent("at most 3 files and 40 lines");
    const stall = screen.getByLabelText(/quiet for/);
    await userEvent.clear(stall);
    await userEvent.type(stall, "45");
    await userEvent.tab();
    await userEvent.click(screen.getByLabelText("On for the whole keel"));
    await waitFor(() => expect(sent).toEqual([{ mode: "fix" }, { stall_minutes: 45 }, { on: true }]));
  });

  it("a project's Settings points at General", () => {
    render(<AppProvider><SupervisorSettingsRow pid="p1" scope="project" /></AppProvider>);
    expect(screen.getByText(/under General/)).toBeInTheDocument();
  });
});
