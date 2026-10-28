// The Supervisor plugin's Jobs › Supervisor inside keel's web (web/src/test/setup.ts runs every plugin's setup()): the
// audit log with each row's result and reason, the waiting flow's switch and mode, and the empty state.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { slotItems, SLOTS } from "../../../../web/src/sdk";
import { server } from "../../../../web/src/test/setup";
import plugin from "../index";
import { outcomeText, type Decision } from "../Supervisor";

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
