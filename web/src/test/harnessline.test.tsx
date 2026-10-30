// keel 0.19.0 harness mode: the flow page names the coding harness that does the work and the command that opens its
// session in a person's own terminal.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HarnessLine } from "../pages/flow/FlowParts";

describe("HarnessLine", () => {
  it("names the harness and copies the command that opens its session", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    render(<HarnessLine harness={{ provider: "claude", model: "sonnet", session: "s-1",
      open: "CLAUDE_CONFIG_DIR=/data/agent-home/claude claude --resume s-1" }} />);
    const line = screen.getByLabelText("Harness session");
    expect(line).toHaveTextContent("Claude Code · sonnet does this work in one session");
    expect(line).toHaveTextContent("claude --resume s-1");
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(write).toHaveBeenCalledWith("CLAUDE_CONFIG_DIR=/data/agent-home/claude claude --resume s-1");
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("says OpenCode for an opencode session", () => {
    render(<HarnessLine harness={{ provider: "copilot", session: "ses_9", open: "opencode -s ses_9" }} />);
    expect(screen.getByLabelText("Harness session")).toHaveTextContent("OpenCode does this work");
  });
});
