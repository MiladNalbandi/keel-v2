// The Org plugin inside keel's web (web/src/test/setup.ts runs every plugin's setup()): the chart's lines, the ready-made
// orgs in Settings › Plugins › Marketplace › Orgs, and New org filled in from one of them.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { server } from "../../../../web/src/test/setup";
import { Chart, lineOf, pairs, says, type ChartLink, type ChartRole } from "../Chart";
import type { OrgTemplate } from "../Templates";

const role = (id: string, title: string, team: string[] = [], boss: string | null = null, human = false): ChartRole =>
  ({ id, title, human, workflows: human ? [] : ["small"], autonomy: "act", team, boss });

// the small team: you give work to the lead, the lead to fe and be; fe may ask be (a side link)
const ROLES = [role("me", "You", ["lead"], null, true), role("lead", "Tech lead", ["fe", "be"], "me"),
  role("fe", "Frontend engineer", [], "lead"), role("be", "Backend engineer", [], "lead")];
const LINKS: ChartLink[] = [
  { from: ["me"], to: ["lead"], can: ["delegate"] },
  { from: ["lead"], to: ["fe", "be"], can: ["delegate"] },
  { from: ["fe", "be"], to: ["lead"], can: ["report", "ask"] },
  { from: ["lead"], to: ["me"], can: ["report", "ask", "approve"] },
  { from: ["fe"], to: ["be"], can: ["ask"] },
];

const tpl = (id: string, title: string, summary: string, roles: ChartRole[], links: ChartLink[], levels: number): OrgTemplate => ({
  id, title, summary, people: roles.filter((r) => r.human).length, agents: roles.filter((r) => !r.human).length, levels,
  workflows: ["lead-intake", "lead-review", "small"], roles, links, yaml: `name: ${title}\nroles: {}\n`,
});
const TEMPLATES = [
  tpl("solo", "Solo developer", "You and two agents, no lead.", [role("me", "You", ["dev"], null, true), role("dev", "Developer", [], "me")],
    [{ from: ["me"], to: ["dev"], can: ["delegate"] }], 2),
  tpl("startup", "Startup", "A CTO splits the work between two leads.", ROLES, LINKS, 3),
];

describe("the org chart", () => {
  it("joins each boss and team member with a work line, and other links with a side line", () => {
    const ps = pairs(ROLES, LINKS);
    expect(ps.map((p) => [p.a, p.b, lineOf(p).kind])).toEqual([
      ["lead", "me", "work"], ["fe", "lead", "work"], ["be", "lead", "work"], ["be", "fe", "side"],
    ]);
    const title = (id: string) => ROLES.find((r) => r.id === id)?.title ?? id;
    expect(says(ps[0], title)).toBe("Tech lead reports to, asks, approves for You; You gives work to Tech lead");
    expect(says(ps[3], title)).toBe("Frontend engineer asks Backend engineer");
  });

  it("draws the lines with a dot at each end, the arrow where work goes, and lights a role's lines", () => {
    const { container } = render(<Chart roles={ROLES} links={LINKS} />);
    const svg = screen.getByTestId("org-wires");
    expect(svg.querySelectorAll('[data-wire="work"]')).toHaveLength(3);
    const side = svg.querySelectorAll('[data-wire="side"]');
    expect(side).toHaveLength(1);
    expect(side[0].querySelectorAll("circle")).toHaveLength(2);
    expect(side[0].querySelector("title")?.textContent).toBe("Frontend engineer asks Backend engineer");
    // work goes down: the arrow is at the team member's end only
    const work = svg.querySelector('[data-wire="work"] path') as SVGPathElement;
    expect(work.getAttribute("marker-end")).toMatch(/^url\(#/);
    expect(work.getAttribute("marker-start")).toBeNull();
    // the levels: you, the lead, then its team
    expect([...container.querySelectorAll(".org-level")].map((l) => [...l.querySelectorAll("[data-role]")].map((b) => b.getAttribute("data-role"))))
      .toEqual([["me"], ["lead"], ["fe", "be"]]);
    fireEvent.mouseEnter(screen.getByRole("generic", { name: "Frontend engineer" }));
    expect(svg.querySelectorAll(".org-wire.on")).toHaveLength(2);       // fe's work line from the lead, and its side line
    expect(screen.getByText("Frontend engineer asks Backend engineer", { selector: "li" })).toBeInTheDocument();
  });
});

describe("the ready-made orgs", () => {
  it("are a kind of their own in the Marketplace: search, preview and Use this org", async () => {
    server.use(http.get("/api/org/templates", () => HttpResponse.json(TEMPLATES)));
    const user = userEvent.setup();
    location.hash = "#/settings/plugins?tab=market&kind=orgs";
    render(<App />);
    const kinds = await screen.findByRole("group", { name: "What to find" });
    expect(within(kinds).getByRole("button", { name: "Orgs" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Ready-made orgs of agents/)).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Ready-made orgs" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText("1 person · 1 agent · 2 levels")).toBeInTheDocument();
    await user.type(screen.getByRole("searchbox"), "cto");
    await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(1));
    expect(within(list).getByRole("link", { name: "Use Startup" })).toHaveAttribute("href", "#/org/new~startup");
    await user.click(within(list).getByRole("button", { name: "Preview Startup" }));
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByTestId("org-wires").querySelectorAll('[data-wire="work"]')).toHaveLength(3);
    expect(within(drawer).getByRole("link", { name: "Use this org" })).toHaveAttribute("href", "#/org/new~startup");
  });

  it("fill in New org from the one the link names, and save as your own org", async () => {
    let saved: { id: string; yaml: string } | null = null;
    server.use(
      http.get("/api/org/orgs", () => HttpResponse.json([])),
      http.get("/api/org/templates", () => HttpResponse.json(TEMPLATES)),
      http.put("/api/org/orgs/:id", async ({ request, params }) => {
        saved = { id: String(params.id), yaml: ((await request.json()) as { yaml: string }).yaml };
        return HttpResponse.json({ id: params.id, name: "Startup", yaml: saved.yaml, updated_at: "", problems: [], roles: [], links: [] });
      }),
    );
    const user = userEvent.setup();
    location.hash = "#/org/new~startup";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "New org" })).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Ready-made orgs" });
    const startup = within(list).getAllByRole("listitem")[1];
    expect(within(startup).getByRole("button", { name: "Picked" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText(/Org id/)).toHaveValue("startup");
    expect(screen.getByLabelText("The org's YAML")).toHaveValue("name: Startup\nroles: {}\n");
    // #97 keel's code editor: the YAML is highlighted under the text, with line numbers
    const editor = screen.getByLabelText("The org's YAML").closest(".ce") as HTMLElement;
    expect(editor.querySelector("code.language-yaml")).not.toBeNull();
    expect(editor.querySelectorAll(".ce-gutter span")).toHaveLength(3);
    // another pick fills in its own
    await user.click(within(within(list).getAllByRole("listitem")[0]).getByRole("button", { name: "Start from this" }));
    expect(screen.getByLabelText(/Org id/)).toHaveValue("solo");
    expect(screen.getByLabelText("The org's YAML")).toHaveValue("name: Solo developer\nroles: {}\n");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saved).toEqual({ id: "solo", yaml: "name: Solo developer\nroles: {}\n" }));
  });
});
