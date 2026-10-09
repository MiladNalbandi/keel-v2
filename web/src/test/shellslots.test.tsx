// The slots the port of 0.15.2–0.15.4 added for the parts (docs/plugins/09-step2-contract.md §5): a count on a menu
// page (nav.badge), a watcher the shell mounts once (shell.watch), a field in the bell's settings (notes.setting) and a
// row in Settings › This browser (settings.browser). KeelBot uses all four (its new answers and its own sound: tested
// with the plugin, plugins/keelbot/web/test/keelbot-chats.test.tsx); here a part of the test's own does.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { describe, expect, it } from "vitest";
import {
  registerSlot,
  SLOTS,
  type NavBadgeItem,
  type NotesSettingItem,
  type SettingsBrowserItem,
  type ShellWatchItem,
} from "@keel/web-sdk";
import { App } from "../App";

describe("nav.badge", () => {
  it("shows a part's count on its page's link, in the menu and on the folded menu's icon", async () => {
    const badge: NavBadgeItem = {
      id: "pt-demo",
      page: "workflows",
      component: ({ kind }: { kind: "nav" | "rail" }) => <span data-testid={`pt-badge-${kind}`}>3</span>,
    };
    const off = registerSlot(SLOTS.navBadge, badge);
    try {
      location.hash = "#/flow";
      const { unmount } = render(<App />);
      const nav = await screen.findByRole("navigation", { name: "Screens" });
      const link = within(nav).getByRole("link", { name: /Workflows/ });
      expect(within(link).getByTestId("pt-badge-nav")).toHaveTextContent("3");
      // only on its own page's link
      expect(within(nav).getAllByTestId("pt-badge-nav")).toHaveLength(1);
      unmount();

      localStorage.setItem("keel2.nav.hidden", "1");
      render(<App />);
      const rail = await screen.findByRole("navigation", {
        name: "Screens (icons)",
      });
      expect(
        within(
          within(rail).getByRole("link", { name: "Workflows" }),
        ).getByTestId("pt-badge-rail"),
      ).toBeInTheDocument();
    } finally {
      act(() => off());
    }
  });
});

describe("shell.watch", () => {
  it("mounts a part's watcher once, on every page, and it shows nothing", async () => {
    let mounted = 0;
    let alive = 0;
    function Watch() {
      useEffect(() => {
        mounted++;
        alive++;
        return () => void alive--;
      }, []);
      return null;
    }
    const off = registerSlot<ShellWatchItem>(SLOTS.shellWatch, {
      id: "pt-watch",
      component: Watch,
    });
    try {
      location.hash = "#/flow";
      render(<App />);
      await screen.findByRole("navigation", { name: "Screens" });
      await waitFor(() => expect(alive).toBe(1));
      // another page keeps the same watcher
      await act(async () => {
        location.hash = "#/workflows";
        await new Promise((r) => setTimeout(r, 20));
      });
      expect(mounted).toBe(1);
      expect(alive).toBe(1);
      act(() => off());
      expect(alive).toBe(0);
    } finally {
      act(() => off());
    }
  });
});

describe("notes.setting and settings.browser", () => {
  it("put a part's field in the bell's settings, after the sound, and its row in Settings › This browser", async () => {
    const user = userEvent.setup();
    const offNote = registerSlot<NotesSettingItem>(SLOTS.notesSetting, {
      id: "pt-note",
      order: -1, // before KeelBot's own field (the full image has the KeelBot plugin)
      component: () => (
        <div className="field" data-testid="pt-note">
          A part's sound
        </div>
      ),
    });
    const offRow = registerSlot<SettingsBrowserItem>(SLOTS.settingsBrowser, {
      id: "pt-row",
      component: () => (
        <div className="field" data-testid="pt-row">
          A part's choice in this browser
        </div>
      ),
    });
    try {
      location.hash = "#/flow";
      const { unmount } = render(<App />);
      await user.click(
        await screen.findByRole("button", { name: /Notifications/ }),
      );
      await user.click(screen.getByRole("tab", { name: "Settings" }));
      const field = await screen.findByTestId("pt-note");
      // after the sound's field, before the pop-ups'
      const fields = [...document.querySelectorAll(".field")];
      const sound = fields.findIndex((f) =>
        f.contains(screen.getByLabelText("Sound")),
      );
      expect(fields.indexOf(field)).toBe(sound + 1);
      unmount();

      location.hash = "#/settings";
      render(<App />);
      const browser = await screen.findByRole("region", {
        name: "This browser",
      });
      expect(within(browser).getByTestId("pt-row")).toHaveTextContent(
        "A part's choice in this browser",
      );
    } finally {
      act(() => {
        offNote();
        offRow();
      });
    }
  });
});
