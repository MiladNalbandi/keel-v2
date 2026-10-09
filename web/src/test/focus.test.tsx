// Markdown that keeps a PR body readable (a <details> fold-out with a code block of its own inside, then the tables),
// and the menu on a big screen. The Code page's Focus mode and KeelBot's room in it: plugins/code/web/test.

import {
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { Markdown } from "../components/Markdown";

describe("Markdown of a PR body", () => {
  it("folds the spec extract in <details>, keeps its own code block inside, and renders what follows", () => {
    const body = [
      "# Euro prices",
      "",
      "<details><summary>Spec extract</summary>",
      "",
      "````markdown",
      "## Request path",
      "```",
      "test -> formatEuro(cents)",
      "```",
      "````",
      "</details>",
      "",
      "## Acceptance criteria",
      "",
      "| AC | status |",
      "|---|---|",
      "| CHG-1.1 | done |",
      "",
      "## KeelBot changes",
      "",
      "- `94baef0` feat(helper): say that formatEuro takes whole cents",
    ].join("\n");
    const { container } = render(<Markdown text={body} />);
    const details = container.querySelector("details")!;
    expect(within(details).getByText("Spec extract")).toBeInTheDocument();
    expect(details.querySelector(".md-code")).toHaveTextContent(
      "test -> formatEuro(cents)",
    );
    expect(
      screen.getByRole("heading", { name: "Acceptance criteria" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("table")).toHaveTextContent("CHG-1.1");
    expect(
      screen.getByRole("heading", { name: "KeelBot changes" }),
    ).toBeInTheDocument();
    expect(container.querySelectorAll(".md-code")).toHaveLength(1); // nothing after </details> is code
  });

  it("closes a fence only on a plain fence at least as long", () => {
    const { container } = render(
      <Markdown
        text={["```", "a", "```markdown", "b", "```", "after"].join("\n")}
      />,
    );
    expect(container.querySelector(".md-code")).toHaveTextContent(
      "a ```markdown b",
    );
    expect(screen.getByText("after").tagName).toBe("P");
  });
});

describe("the menu on a big screen", () => {
  it("folds into a thin strip, comes back with ☰, and ⌘\\ toggles it", async () => {
    const user = userEvent.setup();
    location.hash = "#/flow";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Hide the menu" }));
    expect(document.querySelector(".app")).toHaveClass("nav-hidden");
    expect(localStorage.getItem("keel2.nav.hidden")).toBe("1");
    await user.click(screen.getByRole("button", { name: "Show the menu" }));
    expect(document.querySelector(".app")).not.toHaveClass("nav-hidden");
    fireEvent.keyDown(window, { key: "\\", metaKey: true });
    expect(document.querySelector(".app")).toHaveClass("nav-hidden");
    fireEvent.keyDown(window, { key: "\\", metaKey: true });
    expect(document.querySelector(".app")).not.toHaveClass("nav-hidden");
  });
});
