// The Record plugin inside keel's web (web/src/test/setup.ts runs every plugin's setup()): its page, one flow's record,
// who answered each gate, the export and the panel in the Flow page's slot. It imports only react and @keel/web-sdk.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { App } from "../../../../web/src/App";
import { slotItems, SLOTS } from "../../../../web/src/sdk";
import type { FlowPanelItem, FlowPanelProps } from "../../../../web/src/sdk";
import { server } from "../../../../web/src/test/setup";
import plugin from "../index";
import { RecordFlowPanel, RecordView, type FlowRecord, type RecordRow } from "../Record";

const FLOW: FlowRecord = {
  thread_id: "t1", project_id: "shop", status: "done", updated_at: "2026-10-01T10:07:00Z", branch: "feat/export", error: null, waiting: null,
  asked: { by: "the person signed in to this keel", via: "the keel dashboard", title: "Export invoices", workflow_id: "feature",
    request: "Let people export invoices", started_at: "2026-10-01T10:00:00Z", parent: null, source: null, task: null },
  ran: [{ id: "c1", agent: "explorer", step: "spec", phase: "spec", ac: null, provider: "fake", model: "fake-model", status: "done",
    started_at: "2026-10-01T10:00:01Z", ended_at: "2026-10-01T10:00:09Z", tokens_in: 100, tokens_out: 20, tokens_cached: 0, cost_usd: 0.5, result: null }],
  gates: [
    { at: "2026-10-01T10:01:00Z", step: "spec_gate", gate: "spec approval", decision: "approve", choice: null, why: "looks right", who: "the person", ac: null, item: null },
    { at: "2026-10-01T10:02:00Z", step: "ac_gate", gate: "AC gate", decision: "approve", choice: null, why: "auto-approved (mode auto)", who: "keel's run mode", ac: "AC-1", item: null },
  ],
  applied: [{ at: "2026-10-01T10:05:00Z", kind: "commit", ref: "abc123def456789", subject: "feat: export", files: ["src/a.kt"] },
    { at: "2026-10-01T10:00:00Z", kind: "branch", ref: "feat/export", subject: null, files: [] }],
  other: [{ at: "2026-10-01T10:06:00Z", type: "thread.mode", text: "the run mode became auto" }],
  cost: { tokens_in: 100, tokens_out: 20, tokens_cached: 0, usd: 0.5, runs: 1 },
  limits: ["It is not signed."],
};
const main = () => within(document.getElementById("main")!);
const open = (hash: string) => {
  location.hash = hash;
  render(<App />);
};
const ROW: RecordRow = { thread_id: "t1", title: "Export invoices", workflow_id: "feature", status: "done", started_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:07:00Z", tokens: 120, cost_usd: 0.5, gates: 2, commits: 1 };

describe("the Record plugin", () => {
  it("registers its page and its panel in the Flow page's slot", () => {
    expect(plugin.name).toBe("record");
    expect(slotItems<FlowPanelItem>(SLOTS.flowPanel).map((p) => p.id)).toContain("record");
  });

  it("lists the project's flows with their counts and a link to each record", async () => {
    server.use(http.get("/api/projects/:pid/record/flows", () => HttpResponse.json([ROW])));
    open("#/record");
    const table = await main().findByRole("table", { name: "Flows" });
    const link = within(table).getByRole("link", { name: "Export invoices" });
    expect(link.getAttribute("href")).toBe("#/record/t1");
    expect(within(table).getByText("$0.50")).toBeTruthy();
  });

  it("says so when the project has no flow", async () => {
    server.use(http.get("/api/projects/:pid/record/flows", () => HttpResponse.json([])));
    open("#/record");
    expect(await main().findByText("No flow yet")).toBeTruthy();
  });

  it("shows who asked, what ran, who answered each gate, the commits, the cost and what it is not", () => {
    render(<RecordView r={FLOW} />);
    expect(screen.getByText("Let people export invoices")).toBeTruthy();
    expect(screen.getByRole("table", { name: "What ran" }).textContent).toContain("fake-model");
    const gates = screen.getByRole("list", { name: "Gate answers" });
    expect(gates.textContent).toContain("the person");
    expect(gates.textContent).toContain("keel's run mode");
    expect(screen.getByRole("list", { name: "Commits" }).textContent).toContain("abc123def456");
    expect(screen.getByText(/branch feat\/export/)).toBeTruthy();
    expect(screen.getByText("It is not signed.")).toBeTruthy();
    expect(screen.getByRole("list", { name: "Also on this flow" }).textContent).toContain("run mode became auto");
  });

  it("says a flow kept no request and shows a waiting gate", () => {
    render(<RecordView r={{ ...FLOW, status: "waiting", waiting: "the whole document", asked: { ...FLOW.asked, request: null } }} />);
    expect(screen.getByText("keel kept no request text for this flow.")).toBeTruthy();
    expect(screen.getByText(/the whole document/)).toBeTruthy();
  });

  it("gives the export as a file, Markdown or JSON", async () => {
    const asked: string[] = [];
    server.use(
      http.get("/api/projects/:pid/record/flows/t1", () => HttpResponse.json(FLOW)),
      http.get("/api/projects/:pid/record/flows/t1/export", ({ request }) => {
        asked.push(new URL(request.url).searchParams.get("format") ?? "");
        return HttpResponse.text("# Record");
      }),
    );
    const made = vi.fn(() => "blob:x");
    const url = globalThis.URL as unknown as { createObjectURL: unknown; revokeObjectURL: unknown };
    url.createObjectURL = made;
    url.revokeObjectURL = vi.fn();
    open("#/record/t1");
    await main().findByRole("heading", { name: "Export invoices" });
    await userEvent.click(main().getByRole("button", { name: "Export Markdown" }));
    await waitFor(() => expect(asked).toEqual(["md"]));
    await userEvent.click(main().getByRole("button", { name: "Export JSON" }));
    await waitFor(() => expect(asked).toEqual(["md", "json"]));
    await waitFor(() => expect(made).toHaveBeenCalledTimes(2));
  });

  it("the Flow page's panel offers the record of the flow it shows", () => {
    const props = { pid: "shop", thread: { thread_id: "t1" }, readOnly: false } as unknown as FlowPanelProps;
    render(<RecordFlowPanel {...props} />);
    const panel = screen.getByRole("region", { name: "Record of this flow" });
    expect(within(panel).getByRole("button", { name: "Open the record" })).toBeTruthy();
    expect(within(panel).getByRole("button", { name: "Export Markdown" })).toBeTruthy();
  });

  it("imports only react, @keel/web-sdk and its own files", () => {
    const here = join(__dirname, "..");
    const outside = readdirSync(here)
      .filter((f) => /\.tsx?$/.test(f))
      .flatMap((f) => [...readFileSync(join(here, f), "utf8").matchAll(/from "([^"]+)"/g)].map((m) => m[1]))
      .filter((from) => !from.startsWith("./") && from !== "react" && from !== "@keel/web-sdk");
    expect(outside).toEqual([]);
  });
});
