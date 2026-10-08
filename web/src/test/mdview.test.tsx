// v0.15.3 tool output that is Markdown shows rendered (Raw shows the text; copying whole blocks gives their Markdown),
// output that is a diff shows as a highlighted diff, and a read of a .md file renders.

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { JobStep } from "../api";
import { StepView, outputKind } from "../components/StepView";

const MD = ["# Plugin substrate", "", "Three things become **data**:", "", "| Today | Becomes |", "|---|---|", "| wave | an entry |", "", "- one", "- two"].join("\n");
const DIFF = ["diff --git a/src/A.kt b/src/A.kt", "--- a/src/A.kt", "+++ b/src/A.kt", "@@ -1,2 +1,2 @@", " class A {", "-  val x = 1", "+  val x = 2"].join("\n");
const step = (over: Partial<JobStep>): JobStep => ({ n: 1, at: "2026-10-09T10:00:00Z", kind: "tool", text: "", ...over }) as JobStep;

describe("tool output", () => {
  it("knows a diff, Markdown and plain text", () => {
    expect(outputKind(DIFF)).toBe("diff");
    expect(outputKind("anything", "cat docs/specs/v1.md")).toBe("md");
    expect(outputKind(MD)).toBe("md");
    expect(outputKind("BUILD SUCCESSFUL in 3s\n2 tests completed")).toBe("text");
  });

  it("renders Markdown, Raw shows the text, and a copy of whole blocks gives their Markdown", async () => {
    const user = userEvent.setup();
    const { container } = render(<StepView s={step({ tool: "bash", text: "cat docs/notes.md", output: MD, ok: true })} />);
    expect(screen.getByRole("heading", { name: "Plugin substrate" })).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    // select the heading and the paragraph, then copy
    const blocks = container.querySelectorAll<HTMLElement>(".md-src");
    const range = document.createRange();
    range.setStartBefore(blocks[0]);
    range.setEndAfter(blocks[1]);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    let copied = "";
    const ev = new Event("copy", { bubbles: true, cancelable: true }) as Event & { clipboardData: { setData: (t: string, v: string) => void } };
    ev.clipboardData = { setData: (_t, v) => { copied = v; } };
    fireEvent(blocks[0], ev);
    expect(copied).toBe("# Plugin substrate\n\nThree things become **data**:");
    await user.click(screen.getByRole("button", { name: "Raw" }));
    expect(screen.queryByRole("heading", { name: "Plugin substrate" })).toBeNull();
    expect(container.textContent).toContain("| Today | Becomes |");
  });

  it("shows a git diff as a diff, and a read .md file rendered", () => {
    const { container } = render(<StepView s={step({ tool: "bash", text: "git diff", output: DIFF, ok: true })} />);
    expect(container.querySelector(".diffview")).not.toBeNull();
    expect(container.textContent).toContain("val x = 2");
    render(<StepView s={step({ kind: "read", path: "docs/README.md", text: "## Start\n\nRun **keel2 start**." })} />);
    expect(screen.getByRole("heading", { name: "Start" })).toBeInTheDocument();
  });
});
