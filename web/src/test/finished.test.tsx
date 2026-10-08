// v0.15.2 Jobs and Live agents: running calls in the first tab, finished ones in a Finished tab, counts on both
// tabs, one search box (agent, model or provider, project, step or AC, status), and the tab kept in this browser.

import { cleanup, render, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { Job } from "../api";
import { jobMatches, searchWords } from "../components/RunSearch";
import { PROV } from "../format";
import * as fx from "./fixtures";
import { server } from "./setup";

const main = () => within(document.getElementById("main")!);
const ago = (s: number) => new Date(Date.now() - s * 1000).toISOString();

/** 59 older finished calls after fx.jobs, and the oldest one (an archaeologist) past the list's limit of 50. */
const older: Job[] = Array.from({ length: 59 }, (_, i) => ({
  ...fx.jobs[1], id: `old-${i}`, agent: "test-author", provider: "claude", model: "sonnet", step: "s3", phase: "red", ac: `AC-${100 + i}`,
  status: i % 3 ? "done" : "failed", started_at: ago(2000 + i * 60), ended_at: ago(1990 + i * 60),
}));
const oldest: Job = { ...fx.jobs[1], id: "old-x", agent: "archaeologist", provider: "fake", model: "fake", step: "s1", phase: "spec", ac: null,
  status: "done", started_at: ago(90000), ended_at: ago(89000) };
const all: Job[] = [...fx.jobs, ...older, oldest];

/** A small api: status (with the "finished" and "done" groups), project, provider, q and limit; and /jobs/count. */
function jobsApi(list: Job[] = all) {
  const seen: URL[] = [];
  const pick = (u: URL) => {
    const st = u.searchParams.get("status");
    const pid = u.searchParams.get("project");
    const prov = u.searchParams.get("provider");
    const words = searchWords(u.searchParams.get("q") ?? "");
    const pname = (id: string) => fx.projects.find((p) => p.id === id)?.name;
    return list
      .filter((j) => !st || (st === "finished" ? j.status !== "running" : st === "done" ? ["done", "stopped"].includes(j.status) : j.status === st))
      .filter((j) => (!pid || j.project_id === pid) && (!prov || j.provider === prov) && jobMatches(j, words, pname(j.project_id)))
      .sort((a, b) => b.started_at.localeCompare(a.started_at));
  };
  server.use(
    http.get("/api/jobs/count", ({ request }) => {
      const u = new URL(request.url);
      seen.push(u);
      return HttpResponse.json({ count: pick(u).length });
    }),
    http.get("/api/jobs", ({ request }) => {
      const u = new URL(request.url);
      seen.push(u);
      return HttpResponse.json(pick(u).slice(0, Number(u.searchParams.get("limit") ?? 50)));
    }),
  );
  return seen;
}

async function at(hash: string) {
  location.hash = hash;
  render(<App />);
}

describe("search words", () => {
  it("match what a row shows: agent, provider name, model, step, AC, status, project name; every word must match", () => {
    const j = fx.jobs[0]; // ac-reviewer · codex gpt-5.6-sol · s7 · ac-gate · AC-002 · running · ludus-engine
    const m = (q: string, name?: string) => jobMatches(j, searchWords(q), name);
    expect(PROV.codex).toBe("GPT / Codex");
    expect(m("")).toBe(true);
    expect(m("REVIEWER")).toBe(true);
    expect(m("gpt")).toBe(true);
    expect(m("codex")).toBe(true);
    expect(m("5.6-sol")).toBe(true);
    expect(m("ac-gate ac-002")).toBe(true);
    expect(m("s7 running")).toBe(true);
    expect(m("zebra", "Zebra Shop")).toBe(true);
    expect(m("ac-reviewer done")).toBe(false);
    expect(m("implementer")).toBe(false);
  });
});

describe("Jobs: Running and Finished tabs", () => {
  it("running calls stay in the first tab, finished ones are in Finished; both tabs show their count", async () => {
    const user = userEvent.setup();
    jobsApi();
    await at("#/jobs");
    const running = await main().findByRole("tab", { name: "Running 1" });
    expect(running).toHaveAttribute("aria-selected", "true");
    const now = main().getByRole("region", { name: "Running now" });
    expect(within(now).getByText("ac-reviewer")).toBeInTheDocument();
    expect(within(now).queryByText("implementer")).toBeNull();
    // the count comes from the api: all 61 finished calls, past the list's limit of 50
    const fin = await main().findByRole("tab", { name: "Finished 61" });
    await user.click(fin);
    expect(fin).toHaveAttribute("aria-selected", "true");
    const table = await main().findByRole("table", { name: "Agent calls" });
    expect(within(table).getAllByRole("row")).toHaveLength(51);
    expect(within(table).queryByText("ac-reviewer")).toBeNull();
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("implementer");
    expect(main().getByText("The newest 50 of 61 finished calls. Search to find an older one.")).toBeInTheDocument();
    expect(main().getByLabelText("Filter by status")).toBeInTheDocument();
  });

  it("the search filters both tabs and asks the api for older finished calls; Clear the search brings all back", async () => {
    const user = userEvent.setup();
    const seen = jobsApi();
    await at("#/jobs");
    await main().findByRole("tab", { name: "Finished 61" });
    const box = main().getByRole("searchbox", { name: "Search calls" });
    await user.type(box, "archaeologist");
    // nothing running matches
    expect(await main().findByText("No running call matches this search")).toBeInTheDocument();
    expect(main().getByRole("tab", { name: "Running 0" })).toBeInTheDocument();
    // the oldest call is past the first 50: the api finds it
    const fin = await main().findByRole("tab", { name: "Finished 1" });
    await user.click(fin);
    const table = await main().findByRole("table", { name: "Agent calls" });
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("archaeologist");
    expect(seen.some((u) => u.pathname === "/api/jobs" && u.searchParams.get("q") === "archaeologist" && u.searchParams.get("status") === "finished")).toBe(true);
    expect(seen.some((u) => u.pathname === "/api/jobs/count" && u.searchParams.get("q") === "archaeologist")).toBe(true);

    // a model and an AC, together
    await user.clear(box);
    await user.type(box, "sonnet ac-101");
    await waitFor(() => expect(within(main().getByRole("table", { name: "Agent calls" })).getAllByRole("row")).toHaveLength(2));
    expect(main().getByRole("tab", { name: "Finished 1" })).toBeInTheDocument();

    await user.clear(box);
    await user.type(box, "nothing-like-this");
    expect(await main().findByText("No finished call matches this search")).toBeInTheDocument();
    await user.click(main().getByRole("button", { name: "Clear the search" }));
    expect(box).toHaveValue("");
    await waitFor(() => expect(within(main().getByRole("table", { name: "Agent calls" })).getAllByRole("row")).toHaveLength(51));
    expect(main().getByRole("tab", { name: "Finished 61" })).toBeInTheDocument();
  });

  it("the chosen tab is kept in this browser", async () => {
    const user = userEvent.setup();
    jobsApi();
    await at("#/jobs");
    await user.click(await main().findByRole("tab", { name: /^Finished/ }));
    expect(localStorage.getItem("keel2.jobs.tab")).toBe("finished");
    cleanup();
    await at("#/jobs");
    // something runs, but this browser chose Finished
    expect(await main().findByRole("table", { name: "Agent calls" })).toBeInTheDocument();
    expect(main().getByRole("tab", { name: /^Finished/ })).toHaveAttribute("aria-selected", "true");
    expect(main().getByRole("tab", { name: "Running 1" })).toHaveAttribute("aria-selected", "false");
  });

  it("Running with nothing running says so and offers the finished calls", async () => {
    const user = userEvent.setup();
    localStorage.setItem("keel2.jobs.tab", "running");
    jobsApi(all.filter((j) => j.status !== "running"));
    await at("#/jobs");
    expect(await main().findByText("Nothing runs now")).toBeInTheDocument();
    expect(main().getByRole("tab", { name: "Running 0" })).toHaveAttribute("aria-selected", "true");
    await user.click(main().getByRole("button", { name: "Show the finished calls" }));
    expect(await main().findByRole("table", { name: "Agent calls" })).toBeInTheDocument();
    expect(localStorage.getItem("keel2.jobs.tab")).toBe("finished");
  });
});

describe("Live agents: Working now and Finished tabs", () => {
  it("agents that ended are in Finished, with counts; the search filters both and the feed stays on the working agent", async () => {
    const user = userEvent.setup();
    const seen = jobsApi();
    await at("#/live");
    expect(await main().findByRole("heading", { name: "ac-reviewer · AC-002" })).toBeInTheDocument();
    const working = await main().findByRole("tab", { name: "Working now 1" });
    expect(working).toHaveAttribute("aria-selected", "true");
    expect(within(main().getByRole("region", { name: "Working now" })).getByRole("button", { name: /ac-reviewer/ })).toBeInTheDocument();

    const fin = await main().findByRole("tab", { name: "Finished 61" });
    await user.click(fin);
    const list = main().getByRole("region", { name: "Finished" });
    expect(await within(list).findAllByRole("button", { name: /test-author|implementer/ })).toHaveLength(30);
    expect(within(list).getByText("The newest 30 of 61. Search to find an older one.")).toBeInTheDocument();
    expect(localStorage.getItem("keel2.live.tab")).toBe("finished");

    await user.type(main().getByRole("searchbox", { name: "Search agents" }), "implementer");
    await waitFor(() => expect(within(main().getByRole("region", { name: "Finished" })).getAllByRole("button", { name: /implementer/ })).toHaveLength(1));
    expect(within(main().getByRole("region", { name: "Finished" })).queryByRole("button", { name: /test-author/ })).toBeNull();
    expect(await main().findByRole("tab", { name: "Finished 1" })).toBeInTheDocument();
    expect(main().getByRole("tab", { name: "Working now 0" })).toBeInTheDocument();
    await waitFor(() => expect(seen.some((u) => u.searchParams.get("q") === "implementer" && u.searchParams.get("status") === "finished" && u.searchParams.get("limit") === "30")).toBe(true));
    // the feed does not jump to the first match
    expect(main().getByRole("heading", { name: "ac-reviewer · AC-002" })).toBeInTheDocument();

    // an agent past the list's limit: the api finds it, and a click opens its feed
    await user.clear(main().getByRole("searchbox", { name: "Search agents" }));
    await user.type(main().getByRole("searchbox", { name: "Search agents" }), "archaeologist");
    await user.click(await within(main().getByRole("region", { name: "Finished" })).findByRole("button", { name: /archaeologist/ }));
    await waitFor(() => expect(location.hash).toBe("#/live/old-x"));
    expect(await main().findByRole("heading", { name: "archaeologist" })).toBeInTheDocument();
  });

  it("with nobody working and nothing chosen it opens on Finished, and a search does not move the feed; a kept choice wins", async () => {
    const user = userEvent.setup();
    jobsApi(all.filter((j) => j.status !== "running"));
    await at("#/live");
    // the tab is chosen once the running list is in (the count can come a moment earlier)
    await waitFor(() => expect(main().getByRole("tab", { name: "Finished 61" })).toHaveAttribute("aria-selected", "true"));
    expect(localStorage.getItem("keel2.live.tab")).toBeNull();
    expect(await main().findByRole("heading", { name: "implementer · AC-002" })).toBeInTheDocument();
    await user.type(main().getByRole("searchbox", { name: "Search agents" }), "archaeologist");
    expect(await within(main().getByRole("region", { name: "Finished" })).findByRole("button", { name: /archaeologist/ })).toHaveAttribute("aria-pressed", "false");
    expect(main().getByRole("heading", { name: "implementer · AC-002" })).toBeInTheDocument();
    expect(main().getByText("No agent is working right now. This is the last one that ran.")).toBeInTheDocument();
    cleanup();

    localStorage.setItem("keel2.live.tab", "finished");
    jobsApi();
    await at("#/live");
    expect(await main().findByRole("heading", { name: "ac-reviewer · AC-002" })).toBeInTheDocument();
    await waitFor(() => expect(main().getByRole("tab", { name: "Working now 1" })).toHaveAttribute("aria-selected", "false"));
    expect(main().getByRole("tab", { name: /^Finished/ })).toHaveAttribute("aria-selected", "true");
  });
});
