// v0.15.3 a notification opens where it came from: the api's "/projects/<id>/flow" link is that project's flow (the
// notification's own thread), not the All projects page; "/jobs/<id>" is that job.

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { Notification } from "../api";
import { routeFromLink } from "../routes";
import { FakeEventSource } from "./setup";

const note = (over: Partial<Notification>): Notification => ({
  id: `n${Math.random()}`, type: "review", project_id: "platform", title: "Spec approval waits for you", body: "", link: null,
  at: new Date().toISOString(), read: false, ...over,
}) as Notification;

describe("notification links", () => {
  it("reads the api's /projects/<id>/<page> form and keel's own forms", () => {
    expect(routeFromLink("/projects/ludus-engine/flow")).toEqual({ page: "flow", arg: undefined, project: "ludus-engine" });
    expect(routeFromLink("/projects/demo/flow/th_9")).toEqual({ page: "flow", arg: "th_9", project: "demo" });
    expect(routeFromLink("/jobs/j-1")).toEqual({ page: "jobs", arg: "j-1" });
    expect(routeFromLink("#/inbox")).toEqual({ page: "inbox", arg: undefined });
  });

  it("a click on a flow's notification opens that project's flow, at the notification's thread", async () => {
    const user = userEvent.setup();
    localStorage.setItem("keel2.mascot", "1");
    render(<App />);
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
    act(() => FakeEventSource.emit("notification", note({ link: "/projects/platform/flow", thread_id: "th_77" } as Partial<Notification>)));
    await user.click(await screen.findByRole("button", { name: "Open notification: Spec approval waits for you" }));
    await waitFor(() => expect(location.hash).toBe("#/flow/th_77"));
    expect(localStorage.getItem("keel2.project")).toBe("platform");
  });
});
