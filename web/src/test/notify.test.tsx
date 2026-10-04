import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { Notification } from "../api";
import { playSound, shouldAlert, NOTIFY_DEFAULTS } from "../notify";
import { audioLog, db, FakeEventSource } from "./setup";

const note = (over: Partial<Notification> = {}): Notification => ({
  id: `x${Math.random()}`, type: "review", project_id: "ludus-engine", title: "Fix needs your approval",
  body: "setup-doctor wants to edit conftest.py", link: "#/jobs", at: new Date().toISOString(), read: false, ...over,
});

async function ready() {
  render(<App />);
  await screen.findByText(/AC gate — AC-002/);
  await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
}

describe("notifications", () => {
  it("a server notification pops up with an action and plays the chime", async () => {
    const user = userEvent.setup();
    await ready();
    act(() => FakeEventSource.emit("notification", note()));
    const pops = await screen.findByTestId("popups");
    expect(pops).toHaveTextContent("Fix needs your approval");
    expect(audioLog).toEqual([660, 880]);
    expect(screen.getByTestId("unread")).toHaveTextContent("2");
    await user.click(screen.getByRole("button", { name: "Review now" }));
    await waitFor(() => expect(location.hash).toBe("#/jobs"));
    expect(screen.queryByTestId("popups")).not.toBeInTheDocument();
  });

  it("a failure plays the lower tone", async () => {
    await ready();
    act(() => FakeEventSource.emit("notification", note({ type: "failed", title: "Guard reverted an edit" })));
    await within(await screen.findByTestId("popups")).findByText("Guard reverted an edit");
    expect(audioLog).toEqual([392, 262]);
  });

  it("do not disturb keeps it in the inbox without sound or pop-up", async () => {
    db.nset.quiet = true;
    const user = userEvent.setup();
    await ready();
    await waitFor(() => expect(document.querySelector(".bell-q")).not.toBeNull());
    act(() => FakeEventSource.emit("notification", note({ title: "Quiet one" })));
    await waitFor(() => expect(screen.getByTestId("unread")).toHaveTextContent("2"));
    expect(screen.queryByTestId("popups")).not.toBeInTheDocument();
    expect(audioLog).toEqual([]);
    await user.click(screen.getByRole("button", { name: /Notifications, 2 unread/ }));
    expect(await screen.findByText("Quiet one")).toBeInTheDocument();
  });

  it("settings in the bell drawer are saved to the api", async () => {
    const user = userEvent.setup();
    await ready();
    await user.click(screen.getByRole("button", { name: /Notifications/ }));
    await user.click(screen.getByRole("tab", { name: "Settings" }));
    await user.selectOptions(screen.getByLabelText("Sound"), "off");
    await waitFor(() => expect(db.calls.find((c) => c.path === "/api/notification-settings")?.body).toMatchObject({ tone: "off" }));
    await user.click(screen.getByRole("button", { name: "Send a test notification" }));
    expect(await screen.findByTestId("popups")).toHaveTextContent("Test notification");
    expect(audioLog).toEqual([]); // silent tone
  });

  it("mark all as read clears the badge", async () => {
    const user = userEvent.setup();
    await ready();
    await user.click(screen.getByRole("button", { name: /Notifications, 1 unread/ }));
    await user.click(screen.getByRole("button", { name: "Mark all as read" }));
    expect(screen.queryByTestId("unread")).not.toBeInTheDocument();
    expect(db.calls.some((c) => c.path === "/api/notifications/read-all")).toBe(true);
  });
});

describe("notify helpers", () => {
  it("soft tone is one note; off is silent", () => {
    expect(playSound("review", { sound: true, tone: "soft", volume: 0.5 })).toBe(true);
    expect(audioLog).toEqual([523]);
    expect(playSound("review", { sound: true, tone: "off", volume: 0.5 })).toBe(false);
    expect(playSound("review", { sound: false, tone: "chime", volume: 0.5 })).toBe(false);
  });
  it("respects kinds and scope", () => {
    expect(shouldAlert(note({ type: "started" }), NOTIFY_DEFAULTS, "ludus-engine")).toBe(false);
    expect(shouldAlert(note({ project_id: "platform" }), { ...NOTIFY_DEFAULTS, scope: "project" }, "ludus-engine")).toBe(false);
    expect(shouldAlert(note(), NOTIFY_DEFAULTS, "ludus-engine")).toBe(true);
  });
});
