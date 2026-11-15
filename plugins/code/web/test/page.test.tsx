// The Code page's head and keel view (Update from base, a file's history, Unlock, Refresh stale in Memory), a map
// link that opens a file at a line, and Focus mode and KeelBot's room. Moved from web/src/test (v02, er and focus tests)
// with the plugin: it runs inside keel's web, as the full image has it (web/src/test/setup.ts runs every plugin's
// setup()).

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
// keel's own app and test harness: this test runs the Code page inside keel's web, as keel loads it
import { App } from "../../../../web/src/App";
import { SLOTS } from "../../../../web/src/sdk/slots";
import { restorePlugins, withoutPages, withoutSlot } from "../../../../web/src/test/coreOnly";
import { db } from "../../../../web/src/test/setup";

const at = (hash: string) => {
  location.hash = hash;
};
const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

describe("Code", () => {
  it("Update from base shows a clean merge, then conflicts", async () => {
    const user = userEvent.setup();
    at("#/repo");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Update from main" }));
    expect(await screen.findByText("Updated: main is merged into this branch.")).toBeInTheDocument();
    expect(calls("POST", "/api/projects/ludus-engine/repo/update-from-base")).toHaveLength(1);

    db.update = { ok: true, merged: false, conflicts: ["api/ScoreController.kt", "web/App.tsx"], output: "CONFLICT (content)" };
    await user.click(screen.getByRole("button", { name: "Update from main" }));
    const box = await screen.findByText("Not updated: 2 files conflict with main.");
    const alert = box.closest(".errbox") as HTMLElement;
    expect(within(alert).getByText("api/ScoreController.kt")).toBeInTheDocument();
    expect(within(alert).getByText(/nothing changed/)).toBeInTheDocument();
  });

  it("file History (keel view) lists the commits of that file", async () => {
    const user = userEvent.setup();
    at("#/repo/api/ScoreController.kt");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "keel" }));
    await user.click(await screen.findByRole("button", { name: "History" }));
    const list = await screen.findByRole("list", { name: "File history" });
    expect(within(list).getByText(/77b1e02/)).toBeInTheDocument();
    expect(within(list).getByText(/save a score/)).toBeInTheDocument();
  });

  it("Unlock for this phase asks first, says it is logged, then calls the api", async () => {
    const user = userEvent.setup();
    at("#/repo/api/ScoreController.kt");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "keel" }));
    await user.click(await screen.findByRole("button", { name: "Unlock for this phase" }));
    const confirm = screen.getByRole("group", { name: "Confirm" });
    expect(confirm).toHaveTextContent(/logged/);
    expect(confirm).toHaveTextContent("ac-gate");
    expect(calls("POST", "/api/projects/ludus-engine/unlock")).toHaveLength(0);
    await user.click(within(confirm).getByRole("button", { name: "Yes, unlock it" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/unlock")[0]?.body).toEqual({ path: "api/ScoreController.kt", phase: "ac-gate" }));
    expect(await screen.findByText("unlocked in ac-gate")).toBeInTheDocument();
  });

  it("Refresh stale in Memory starts the knowledge refresh and links to Flow", async () => {
    const user = userEvent.setup();
    at("#/repo");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "keel" }));
    await user.click(await screen.findByRole("button", { name: /^Memory/ }));
    await user.click(await screen.findByRole("button", { name: "Refresh stale" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/wiki/refresh")[0]?.body).toEqual({ sections: ["domain"] }));
    expect(await screen.findByRole("link", { name: "Watch it in Flow" })).toHaveAttribute("href", "#/flow");
    expect(screen.getByText("th_kr1")).toBeInTheDocument();
  });
});

describe("Code page from a map link", () => {
  it("opens the file at the line a table or column cites", async () => {
    localStorage.setItem("keel2.project", "ludus-engine");
    location.hash = `#/repo/${encodeURIComponent("api/ScoreController.kt:3")}`;
    render(<App />);
    // The Repo IDE (v0.5.1) opens the encoded map link at the line and marks it.
    await screen.findByRole("region", { name: "Code of api/ScoreController.kt" });
    const row = () => document.querySelector<HTMLElement>('.cv-row[data-line="3"]');
    await waitFor(() => expect(row()).toHaveClass("tgt"));
    expect(row()!.textContent).toContain("import org.springframework.web.bind.annotation.PostMapping");
  });
});

describe("Code page: Focus and KeelBot's room", () => {
  it("Focus mode is the whole window: the header, keel's menu and the usage bar go; the status bar brings them back", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Code" }),
    ).toBeInTheDocument();
    // v0.17.1 Focus is only in the status bar (not in the head)
    const head = screen.getByRole("heading", { name: "Code" }).closest(".top") as HTMLElement;
    expect(within(head).queryByRole("button", { name: /Focus/ })).toBeNull();
    const bar = document.querySelector<HTMLElement>("footer.ide-sb")!;
    await user.click(within(bar).getByRole("button", { name: /^Focus/ }));
    expect(screen.queryByRole("heading", { name: "Code" })).toBeNull();
    expect(localStorage.getItem("keel2.repo.focus")).toBe("1");
    // the shell hides keel's menu and the usage bar while <html data-focus> is set (styles/repo.css)
    expect(document.documentElement.dataset.focus).toBe("code");
    await user.click(screen.getByRole("button", { name: /Exit focus/ }));
    expect(
      await screen.findByRole("heading", { name: "Code" }),
    ).toBeInTheDocument();
    expect(document.documentElement.dataset.focus).toBeUndefined();
  });

  it("⇧⌘\\ turns Focus mode on and off, Esc twice leaves it, and leaving Code shows keel's menu again", async () => {
    location.hash = "#/repo";
    render(<App />);
    await screen.findByRole("heading", { name: "Code" });
    const focusKey = () => fireEvent.keyDown(window, { key: "|", code: "Backslash", ctrlKey: true, shiftKey: true });
    focusKey();
    await waitFor(() => expect(document.documentElement.dataset.focus).toBe("code"));
    focusKey();
    await waitFor(() => expect(document.documentElement.dataset.focus).toBeUndefined());
    focusKey();
    await waitFor(() => expect(document.documentElement.dataset.focus).toBe("code"));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.documentElement.dataset.focus).toBe("code"); // one Esc is not enough
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(document.documentElement.dataset.focus).toBeUndefined());
    focusKey();
    await waitFor(() => expect(document.documentElement.dataset.focus).toBe("code"));
    location.hash = "#/flow";
    await waitFor(() => expect(document.documentElement.dataset.focus).toBeUndefined());
    expect(localStorage.getItem("keel2.repo.focus")).toBe("1"); // Code opens in Focus mode next time
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

  it("v0.17.1: the head has no KeelBot only or Focus button; KeelBot opens alone from its panel or the menu", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    const head = (await screen.findByRole("heading", { name: "Code" })).closest(".top") as HTMLElement;
    expect(within(head).getByRole("button", { name: "Update from main" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "KeelBot only" })).toBeNull();
    expect(within(head).queryByRole("button", { name: /Focus/ })).toBeNull();
    expect(within(head).queryByRole("button", { name: /Rebuild/ })).toBeNull();
    await user.click(
      within(screen.getByRole("navigation", { name: "Screens" })).getByRole("link", { name: "KeelBot" }),
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

// Step 6: the Code page with other plugins off (a set without them): its links to their pages go, and its buttons for
// KeelBot go without the assistant. With every plugin (the tests above) they are as before.
describe("Code with other plugins off", () => {
  afterEach(() => restorePlugins());

  // 0.28 the redesign put Go to file (⌘P) in the head, and Ask KeelBot (⌘I) while KeelBot's column is there; without
  // KeelBot the head keeps Update from main and Go to file only
  it("without KeelBot: no KeelBot in the menu, and the head has only Update from main and Go to file", async () => {
    withoutPages("helper");
    withoutSlot(SLOTS.assistant);
    at("#/repo");
    render(<App />);
    const update = await screen.findByRole("button", { name: "Update from main" });
    expect(within(update.closest(".top") as HTMLElement).getAllByRole("button").map((b) => b.textContent))
      .toEqual(["Update from main", expect.stringMatching(/^Go to file/)]);
    expect(within(screen.getByRole("navigation", { name: "Screens" })).queryByRole("link", { name: "KeelBot" })).toBeNull();
  });

  it("the keel view links to the Wiki only when this keel has it, and Refresh stale goes with it", async () => {
    const user = userEvent.setup();
    at("#/repo");
    const full = render(<App />);
    await user.click(await screen.findByRole("button", { name: "keel" }));
    const pages = await screen.findByRole("region", { name: "keel's pages" });
    expect(within(pages).getByRole("link", { name: /^Wiki/ })).toHaveAttribute("href", "#/wiki");
    full.unmount();
    withoutPages("wiki");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "keel" }));
    const own = await screen.findByRole("region", { name: "keel's pages" });
    expect(within(own).getByRole("button", { name: /^Memory/ })).toBeInTheDocument();
    expect(within(own).queryByRole("link", { name: /^Wiki/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /^Wiki/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: /^Memory/ }));
    expect(await screen.findByText(/stale/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Refresh stale" })).toBeNull();
  });

  it("a branch's Ask KeelBot to review it: with an assistant, and not without one", async () => {
    const user = userEvent.setup();
    db.plugins.git = true;
    const branch = async () => {
      at("#/repo");
      const r = render(<App />);
      await user.click(await screen.findByRole("button", { name: "Source control" }));
      await user.click(await screen.findByRole("button", { name: "Open the branch feat/scores" }));
      return { r, tab: await screen.findByRole("region", { name: "Branch feat/scores" }) };
    };
    const full = await branch();
    expect(within(full.tab).getByRole("button", { name: "Ask KeelBot to review it" })).toBeInTheDocument();
    full.r.unmount();
    withoutPages("helper");
    withoutSlot(SLOTS.assistant);
    const off = await branch();
    expect(within(off.tab).getByRole("region", { name: "Changed files" })).toBeInTheDocument();
    expect(within(off.tab).queryByRole("button", { name: "Ask KeelBot to review it" })).toBeNull();
  });
});
