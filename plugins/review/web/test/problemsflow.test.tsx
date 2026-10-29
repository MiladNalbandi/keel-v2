// keel 0.18.67 Find problems as the review flow: the Problems tab says the flow is reading the change, at which depth,
// and links to the flow (every step is on its Flow page); the checked result keeps the link. The buttons do not change.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProblemsTab } from "../next/Problems";
import type { AiState, ReviewView } from "../reviewApi";

const view = { host: { kind: "github" } } as unknown as ReviewView;

function ai(findings: Record<string, unknown>): AiState {
  return {
    overview: null,
    findings: {
      id: "rr_1", kind: "findings", head_sha: "abc", stale: false, sessions: [], error: null,
      created_at: "2026-10-29T10:00:00Z", updated_at: "2026-10-29T10:00:00Z", ...findings,
    },
    decisions: {},
  } as unknown as AiState;
}

describe("Problems: the review flow", () => {
  it("while it runs: the flow, its depth and a link to it", () => {
    render(
      <ProblemsTab pid="p" reviewKey="pr:7" view={view} running={[]} onShowInFile={() => {}}
        ai={ai({ status: "running", stage: "flow", result: { stage: "flow", flow_id: "t-rv-1", depth: "quick" } })} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("The review flow (quick) reads the change with your project's rules");
    expect(screen.getByRole("link", { name: "Open the review flow ▸" })).toHaveAttribute("href", "#/flow/t-rv-1");
  });

  it("when it is done: the checked findings, and the link to the flow stays", () => {
    render(
      <ProblemsTab pid="p" reviewKey="pr:7" view={view} running={[]} onShowInFile={() => {}}
        ai={ai({ status: "done", stage: "done", result: { stage: "done", flow_id: "t-rv-1", depth: "standard", findings: [],
          tests: "compile: ok", counts: { blocking: 0 } } })} />,
    );
    expect(screen.getByText(/No blocking problem/)).toHaveTextContent("tests: compile: ok · standard review");
    expect(screen.getByRole("link", { name: "Open the review flow ▸" })).toHaveAttribute("href", "#/flow/t-rv-1");
    expect(screen.queryByRole("button", { name: "Find problems" })).toBeNull();
  });
});
