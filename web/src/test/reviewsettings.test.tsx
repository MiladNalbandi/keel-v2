// keel 0.18.65 Settings › Review: the reviewers (how many, focus, model, agent), the lenses (on, reviewer, a new one),
// the checker and the fix, the last review's model, the text the Review page shows, and Back to keel's default.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { findText, slug } from "../pages/settings/reviewSetup";
import { server } from "./setup";

const SONNET = { provider: "claude", mode: "subscription", model: "sonnet" };
const OPUS_HIGH = { ...SONNET, model: "opus", effort: "high" };

function viewOf(reviewers: { name: string; focus: string; lenses: string[] }[], extra: Record<string, unknown> = {}) {
  const revs = reviewers.map((r) => ({ model: "default", effort: null, agent: null, ...r }));
  const owner = (n: string) => {
    const i = revs.findIndex((r) => r.lenses.includes(n));
    return i < 0 ? null : "ABCD"[i];
  };
  const lens = (name: string, title: string, source = "keel") => ({
    name, title, source, description: `${title} in short`, checklist: "- a check",
    path: source === "keel" ? `content/review-lenses/${name}.md` : `.keel/lenses/${name}.md`, reviewer: owner(name),
  });
  return {
    configured: true,
    stored: {},
    config: {
      reviewers: revs,
      checker: { model: "stronger", effort: null, should_fix: true },
      fix: { flow: "small", model: "flow", run_mode: null, one_flow_per_file: false },
      final: { model: "stronger", effort: null },
      drop_correctness: false,
      depth: (extra.depth as string) ?? "standard",
      find_runs: (extra.runs as string) ?? "classic",
    },
    defaults: {},
    lenses: [lens("data-migration", "Data migration and backfill"), lens("config-secrets", "Secrets and config"),
      lens("silent-failure", "Silent failure"), ...((extra.own as string[]) ?? []).map((n) => lens(n, "Money rounding", "project"))],
    missing: [],
    uses: { reviewers: revs.map(() => SONNET), checker: OPUS_HIGH, final: OPUS_HIGH, fix: null },
    models: { default: SONNET, reviewer: SONNET },
    find_text: findText(revs),
    ship_lenses: ["correctness", "security", "performance", "consumers-config"],
    ship_lenses_from: "keel's default",
    limits: { reviewers: 4, efforts: ["low", "medium", "high"], fix_flows: ["quick", "small", "change", "ask"], run_modes: ["manual", "important", "auto"],
      depths: ["quick", "standard", "full"] },
    depths: {
      quick: { title: "Quick", tokens: "~20k tokens", time: "1–2 min", what: "One reviewer." },
      standard: { title: "Standard", tokens: "~80k tokens", time: "4–6 min", what: "One reviewer per area." },
      full: { title: "Full", tokens: "~250k tokens", time: "10–15 min", what: "Every lens that fits." },
    },
    standards: { rules: ["AGENTS.md", ".cursor/rules/naming.mdc"], lenses: ["money-rounding"], stack: ["python"] },
    agents: [{ id: "branch-reviewer", label: "Branch reviewer", custom: false, keel: true },
      { id: "implementer", label: "Implementer", custom: false, keel: true },
      { id: "money-reviewer", label: "Money reviewer", custom: true, keel: false }],
  };
}

const TWO = [
  { name: "correctness and security", focus: "logic, edge cases", lenses: ["data-migration"] },
  { name: "tests, the description and design", focus: "would the tests fail", lenses: ["config-secrets"] },
];

describe("Settings › Review", () => {
  it("0.18.67 runs one review flow at the depth you pick, and shows the standards every reviewer reads first", async () => {
    let current = viewOf(TWO, { runs: "flow" });
    const saved: Record<string, unknown>[] = [];
    server.use(
      http.get("/api/projects/:pid/review-setup", () => HttpResponse.json(current)),
      http.put("/api/projects/:pid/review-setup", async ({ request }) => {
        const patch = (await request.json()) as Record<string, unknown>;
        saved.push(patch);
        current = viewOf(TWO, { runs: (patch.find_runs as string) ?? current.config.find_runs, depth: (patch.depth as string) ?? current.config.depth });
        return HttpResponse.json(current);
      }),
    );
    location.hash = "#/settings/review";
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "Review" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /The review flow/ })).toBeChecked();
    // the flow has no reviewer count: the classic reviewers show only when Find problems runs them
    expect(screen.queryByRole("radiogroup", { name: "Number of reviewers" })).toBeNull();
    const depth = screen.getByRole("radiogroup", { name: "Review depth" });
    expect(within(depth).getByRole("radio", { name: /Standard/ })).toHaveAttribute("aria-checked", "true");
    expect(within(depth).getByRole("radio", { name: /Quick/ })).toHaveTextContent("~20k tokens · 1–2 min");
    const std = screen.getByRole("group", { name: "Your standards" });
    expect(std).toHaveTextContent("AGENTS.md · .cursor/rules/naming.mdc");
    expect(std).toHaveTextContent("money-rounding");
    await userEvent.click(within(depth).getByRole("radio", { name: /Full/ }));
    await waitFor(() => expect(saved.at(-1)).toEqual({ depth: "full" }));
    await waitFor(() => expect(within(depth).getByRole("radio", { name: /Full/ })).toHaveAttribute("aria-checked", "true"));
    await userEvent.click(screen.getByRole("radio", { name: /Classic/ }));
    await waitFor(() => expect(saved.at(-1)).toEqual({ find_runs: "classic" }));
    expect(await screen.findByRole("radiogroup", { name: "Number of reviewers" })).toBeInTheDocument();
  });

  it("changes the number of reviewers, puts a lens on a reviewer, adds a lens and goes back to keel's default", async () => {
    let current = viewOf(TWO);
    const saved: Record<string, unknown>[] = [];
    const lensBodies: Record<string, unknown>[] = [];
    server.use(
      http.get("/api/projects/:pid/review-setup", () => HttpResponse.json(current)),
      http.put("/api/projects/:pid/review-setup", async ({ request }) => {
        const patch = (await request.json()) as Record<string, unknown>;
        saved.push(patch);
        if (patch.reset) current = viewOf(TWO);
        else if (patch.reviewers) current = viewOf(patch.reviewers as typeof TWO);
        return HttpResponse.json(current);
      }),
      http.post("/api/projects/:pid/review-setup/lenses", async ({ request }) => {
        const b = (await request.json()) as Record<string, unknown>;
        lensBodies.push(b);
        const revs = current.config.reviewers.map((r, i) => ({ ...r, lenses: i === 2 ? [...r.lenses, "money-rounding"] : r.lenses }));
        current = viewOf(revs, { own: ["money-rounding"] });
        return HttpResponse.json({ lens: { name: "money-rounding", path: ".keel/lenses/money-rounding.md" }, view: current });
      }),
    );
    location.hash = "#/settings/review";
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "Review" })).toBeInTheDocument();
    const count = screen.getByRole("radiogroup", { name: "Number of reviewers" });
    expect(within(count).getByRole("radio", { name: "2" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("rv-find-text")).toHaveTextContent(
      "Two keel reviewers read the change (A: correctness and security, B: tests, the description and design).",
    );
    // the models it really uses: the reviewers keep the project's, the checker and the last review get a stronger one
    expect(screen.getByTestId("rv-model-A-uses")).toHaveTextContent("uses claude · sonnet");
    expect(screen.getByTestId("rv-checker-uses")).toHaveTextContent("project default (sonnet) → uses claude · opus · high");
    expect(screen.getByTestId("rv-final-line")).toHaveTextContent("project default (sonnet) → the final review uses claude · opus · high");

    await userEvent.click(within(count).getByRole("radio", { name: "3" }));
    await waitFor(() => expect((saved.at(-1)!.reviewers as unknown[]).length).toBe(3));
    expect(await screen.findByRole("group", { name: "Reviewer C" })).toBeInTheDocument();
    expect(screen.getByTestId("rv-find-text")).toHaveTextContent("Three keel reviewers read the change (A: correctness and security, B: tests, the description and design, C: your own reviewer).");

    // a lens on, on reviewer B
    await userEvent.click(screen.getByRole("checkbox", { name: "Silent failure on" }));
    await waitFor(() => expect((saved.at(-1)!.reviewers as { lenses: string[] }[])[0].lenses).toContain("silent-failure"));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Reviewer for Silent failure" }), "B");
    await waitFor(() => expect((saved.at(-1)!.reviewers as { lenses: string[] }[])[1].lenses).toContain("silent-failure"));

    // a new lens: a file of the project, on reviewer C
    await userEvent.click(screen.getByRole("button", { name: "+ New lens" }));
    const form = screen.getByRole("region", { name: "New lens" });
    await userEvent.type(within(form).getByRole("textbox", { name: "Name" }), "Money rounding");
    expect(form).toHaveTextContent("Saved as .keel/lenses/money-rounding.md in the project");
    await userEvent.type(within(form).getByRole("textbox", { name: /Checklist/ }), "- cents as integers");
    await userEvent.click(within(form).getByRole("button", { name: "Save lens" }));
    await waitFor(() => expect(lensBodies).toHaveLength(1));
    expect(lensBodies[0]).toMatchObject({ title: "Money rounding", reviewer: "C", checklist: "- cents as integers" });
    expect(await screen.findByText(".keel/lenses/money-rounding.md")).toBeInTheDocument();

    // the fix: ask each time
    await userEvent.click(screen.getByRole("radio", { name: /ask me each time/ }));
    await waitFor(() => expect(saved.at(-1)).toMatchObject({ fix: { flow: "ask" } }));

    await userEvent.click(screen.getByRole("button", { name: "Back to keel's default" }));
    await waitFor(() => expect(saved.at(-1)).toEqual({ reset: true }));
    await waitFor(() => expect(within(count).getByRole("radio", { name: "2" })).toHaveAttribute("aria-checked", "true"));
  });

  it("names a lens file from its title and words the Review page's text by the count", () => {
    expect(slug("Money rounding!")).toBe("money-rounding");
    expect(slug("../../etc")).toBe("etc");
    expect(findText([{ name: "everything" }])).toMatch(/^One keel reviewer reads the change \(A: everything\)\./);
  });
});
