import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { DoctorReport } from "../api";
import { server } from "./setup";

const REPORT: DoctorReport = {
  days: 7, since: "2026-11-06T12:00:00+00:00", until: "2026-11-13T12:00:00+00:00", total: 3, flows: 3,
  repaired: 1, retried: 1, waiting: 1, stopped: 0, failed: 0,
  kinds: [
    { kind: "auth", title: "The provider refused the key", fix: "Check the key in Settings › Models & keys, then retry.",
      count: 2, repaired: 0, retried: 1, waiting: 1, stopped: 0, failed: 0, last_at: "2026-11-13T11:59:00+00:00",
      steps: ["feature › spec", "change › impl"], providers: ["claude"], where: [],
      advice: "A key was refused 2×. Check it in Settings › Models & keys (Test says why), or replace it.",
      example: { error: "ModelError: Error code: 401 - invalid x-api-key", step: "spec" } },
    { kind: "overloaded", title: "The provider was overloaded", fix: "keel waits and runs the step again.", count: 1,
      repaired: 1, retried: 0, waiting: 0, stopped: 0, failed: 0, last_at: "2026-11-13T10:00:00+00:00",
      steps: ["change › impl"], providers: ["claude"], where: [],
      advice: "The provider was overloaded 1×. keel waits and retries.", example: { error: "529 overloaded" } },
  ],
  recent: [
    { at: "2026-11-13T11:59:00+00:00", project: "shop", workflow: "change", step: "impl", kind: "auth",
      title: "The provider refused the key", end: "waiting", error: "401" },
    { at: "2026-11-13T10:00:00+00:00", project: "shop", workflow: "change", step: "impl", kind: "overloaded",
      title: "The provider was overloaded", end: "repaired", tries: 1, error: "529" },
  ],
  markdown: "# keel flow doctor: the last 7 days\n",
};

describe("Watch › Flow doctor (#101 part 3)", () => {
  it("shows the numbers, what to improve, each kind of crash and how each one ended", async () => {
    const asked: string[] = [];
    server.use(http.get("/api/flow-doctor/report", ({ request }) => {
      asked.push(new URL(request.url).searchParams.get("days") ?? "");
      return HttpResponse.json(REPORT);
    }));
    location.hash = "#/doctor";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Flow doctor" })).toBeInTheDocument();
    const nums = await screen.findByRole("region", { name: "In numbers" });
    expect(nums).toHaveTextContent("Crashes3");
    expect(nums).toHaveTextContent("Repaired by keel1");
    expect(nums).toHaveTextContent("Wait for you1");
    expect(screen.getByText(/A key was refused 2×/)).toBeInTheDocument();
    expect(screen.getByText("Steps: feature › spec, change › impl")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Recent crashes" });
    expect(within(table).getByText("waits for you")).toBeInTheDocument();
    expect(within(table).getByText("repaired by keel")).toBeInTheDocument();
    expect(within(table).getByText(/after 1 repair$/)).toBeInTheDocument();
    // an example error opens on demand
    await userEvent.click(screen.getAllByRole("button", { name: "Show an example" })[0]);
    expect(screen.getByText("ModelError: Error code: 401 - invalid x-api-key")).toBeInTheDocument();
    // another time span asks again
    await userEvent.click(screen.getByRole("tab", { name: "30 days" }));
    await screen.findByRole("region", { name: "In numbers" });
    expect(asked).toEqual(["7", "30"]);
  });

  it("copies the report as Markdown and says when there was no crash", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    server.use(http.get("/api/flow-doctor/report", () => HttpResponse.json(REPORT)));
    location.hash = "#/doctor";
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Copy the report" }));
    expect(write).toHaveBeenCalledWith("# keel flow doctor: the last 7 days\n");
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();

    server.use(http.get("/api/flow-doctor/report", () => HttpResponse.json({ ...REPORT, total: 0, flows: 0, kinds: [], recent: [] })));
    await userEvent.click(screen.getByRole("tab", { name: "Today" }));
    expect(await screen.findByText("No crash today")).toBeInTheDocument();
  });

  it("is in the Watch group of the menu", async () => {
    server.use(http.get("/api/flow-doctor/report", () => HttpResponse.json(REPORT)));
    location.hash = "#/projects";
    render(<App />);
    const link = await screen.findByRole("link", { name: /Flow doctor/ });
    expect(link).toHaveAttribute("href", "#/doctor");
  });
});
