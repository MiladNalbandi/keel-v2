// The Code page's Focus (no header), KeelBot's width, KeelBot on a page of its own, and Markdown that keeps
// a PR body readable (a <details> fold-out with a code block of its own inside, then the tables).

import {
  fireEvent,
  render,
  screen,
  waitFor,
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

describe("Code page: Focus and KeelBot's room", () => {
  it("hides the header and brings it back from the status bar", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Code" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Focus" }));
    expect(screen.queryByRole("heading", { name: "Code" })).toBeNull();
    expect(localStorage.getItem("keel2.repo.focus")).toBe("1");
    await user.click(screen.getByRole("button", { name: "Show the header" }));
    expect(
      await screen.findByRole("heading", { name: "Code" }),
    ).toBeInTheDocument();
  });

  it("makes KeelBot wider by its edge, and opens it alone on its page", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const edge = await screen.findByRole("separator", {
      name: "Resize KeelBot",
    });
    fireEvent.keyDown(edge, { key: "ArrowLeft" });
    fireEvent.keyDown(edge, { key: "ArrowLeft" });
    await waitFor(() =>
      expect(localStorage.getItem("keel2.repo.helper.w")).toBe("444"),
    );
    expect(
      document
        .querySelector<HTMLElement>(".ide")!
        .style.getPropertyValue("--help-w"),
    ).toBe("444px");
    await user.click(
      screen.getByRole("button", { name: "Open KeelBot full screen" }),
    );
    await waitFor(() => expect(location.hash).toBe("#/helper"));
    const page = await screen.findByRole("complementary", { name: "KeelBot" });
    expect(page).toHaveClass("page");
    expect(screen.queryByRole("region", { name: "Editor" })).toBeNull(); // only KeelBot
    await user.click(
      within(page).getByRole("button", { name: "Back to the code" }),
    );
    await waitFor(() => expect(location.hash).toBe("#/repo"));
  });

  it("has a KeelBot only button on the Code page and a KeelBot entry in the menu", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "KeelBot only" }),
    );
    await waitFor(() => expect(location.hash).toBe("#/helper"));
    expect(
      within(screen.getByRole("navigation", { name: "Screens" })).getByRole(
        "link",
        { name: "KeelBot" },
      ),
    ).toHaveAttribute("aria-current", "page");
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
