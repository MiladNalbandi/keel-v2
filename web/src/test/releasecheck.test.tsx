// "Whole test suite before push" (Settings › General › Flow and gates, one project's own): required, or off because CI
// runs the whole suite. keel writes release.required in .keel/config.yml (PUT /projects/{pid}/release-check), and
// Before ship says the gate is off instead of listing it.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { ReleaseCheck } from "../api";
import { BeforeShipPanel } from "../pages/flow/Gate";
import { server } from "./setup";

function answers(start: Partial<ReleaseCheck> = {}) {
  const sent: unknown[] = [];
  let now: ReleaseCheck = { configured: true, required: true, setting: null, why: null, ...start };
  server.use(
    http.get("/api/projects/:pid/release-check", () => HttpResponse.json(now)),
    http.put("/api/projects/:pid/release-check", async ({ request }) => {
      const b = (await request.json()) as { required: boolean };
      sent.push(b);
      now = { configured: true, required: b.required, setting: b.required,
        why: b.required ? null : "off in this project's settings: CI runs the whole suite" };
      return HttpResponse.json(now);
    }),
  );
  return sent;
}

async function row() {
  const sec = await screen.findByTestId("sec-flow");
  return within(sec).findByTestId("row-release_check");
}

describe("Whole test suite before push", () => {
  it("a project turns it off and on again, with a line that says what it means", async () => {
    const sent = answers();
    location.hash = "#/settings/general/yegi";
    render(<App />);
    const r = await row();
    expect(r).toHaveTextContent("Whole test suite before push");
    expect(await within(r).findByText("keel runs the whole test suite before a push, and the push waits until it passes.")).toBeInTheDocument();
    const seg = within(r).getByRole("group", { name: "Whole test suite before push" });
    expect(within(seg).getByRole("button", { name: "required" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(seg).getByRole("button", { name: "off (CI runs it)" }));
    await waitFor(() => expect(sent).toEqual([{ required: false }]));
    expect(await within(r).findByText(/Off: your CI runs the whole suite\. Ship skips verify release/)).toBeInTheDocument();
    expect(within(seg).getByRole("button", { name: "off (CI runs it)" })).toHaveAttribute("aria-pressed", "true");
    expect(r).toHaveTextContent("saved in .keel/config.yml (release.required)");
    await userEvent.click(within(seg).getByRole("button", { name: "required" }));
    await waitFor(() => expect(sent).toEqual([{ required: false }, { required: true }]));
  });

  it("Every project says it is set per project, and a project without a config has no switch", async () => {
    answers({ configured: false });
    location.hash = "#/settings/general";
    const { unmount } = render(<App />);
    const all = await row();
    expect(all).toHaveTextContent("Set per project, in its .keel/config.yml.");
    expect(within(all).queryByRole("group")).toBeNull();
    unmount();
    location.hash = "#/settings/general/yegi";
    render(<App />);
    const r = await row();
    expect(await within(r).findByText(/has no .keel\/config.yml yet/)).toBeInTheDocument();
    expect(within(r).queryByRole("group", { name: "Whole test suite before push" })).toBeNull();
  });

  it("Before ship says the release gate is off instead of blocking", () => {
    render(<BeforeShipPanel blockers={[]} pid="yegi"
      off={[{ gate: "release", why: "off in this project's settings: CI runs the whole suite", fix: "" }]} />);
    const off = screen.getByLabelText("Off for pushes in this project");
    expect(off).toHaveTextContent("release: off");
    expect(off).toHaveTextContent("off in this project's settings: CI runs the whole suite");
    expect(within(off).getByRole("link", { name: "Settings › Flow and gates" })).toHaveAttribute("href", "#/settings/general/yegi");
    expect(screen.getByText("Nothing blocks shipping.")).toBeInTheDocument();
  });
});
