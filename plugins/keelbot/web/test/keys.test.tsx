// v0.15.4 KeelBot's keys in keel's key cheat sheet (⌘/ or ?): its area comes from this plugin (slot keys.area), on its
// own page and on the Code page, after the Code page's and Code Review's areas. It runs inside keel's web, as the full
// image has it (web/src/test/setup.ts runs every plugin's setup()).

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
// keel's own app: the sheet is keel's, the area is KeelBot's
import { App } from "../../../../web/src/App";

describe("KeelBot's keys in the key cheat sheet", () => {
  it("shows on its own page and on the Code page, after Code and Code Review", async () => {
    location.hash = "#/helper";
    const { unmount } = render(<App />);
    await screen.findByRole("complementary", { name: "KeelBot" });
    fireEvent.keyDown(window, { key: "/", code: "Slash", ctrlKey: true });
    let dlg = await screen.findByRole("dialog", { name: "Keyboard keys" });
    expect(
      within(dlg).getByRole("heading", { name: "KeelBot" }),
    ).toBeInTheDocument();
    const row = within(dlg)
      .getByText("Name a file, a symbol or an AC")
      .closest(".ks-row") as HTMLElement;
    expect(row).toHaveTextContent("@");
    expect(within(dlg).queryByRole("heading", { name: "Code" })).toBeNull(); // the Code page's keys: not this page
    unmount();

    location.hash = "#/repo";
    render(<App />);
    await screen.findByRole("heading", { name: "Code" });
    fireEvent.keyDown(window, { key: "/", code: "Slash", ctrlKey: true });
    dlg = await screen.findByRole("dialog", { name: "Keyboard keys" });
    const heads = within(dlg)
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(heads.slice(0, 3)).toEqual([
      "Code",
      "Code Review (in a review's file tab)",
      "KeelBot",
    ]);
  });
});
