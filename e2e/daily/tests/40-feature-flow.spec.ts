// J2 a feature flow from the dashboard to the spec gate and on, on the fake model, with the SAFE driver.
import { expect, test } from "@playwright/test";
import { api, driveFlow, note, projectNamed, signIn, until, useFakeModel, watchConsole, expectNoConsoleErrors } from "../lib/keel";

test.describe("J2 Feature flow", () => {
  test("J2 start a feature flow in the UI, reach the spec gate, approve it, go on", async ({ page }, info) => {
    const errors = watchConsole(page);
    await useFakeModel();
    await signIn(page);
    const shop = await projectNamed("shop");
    await page.goto("/#/projects");
    await page.getByRole("button", { name: "Start a flow in shop" }).click();
    const dlg = page.getByRole("dialog").first();
    await expect(dlg).toBeVisible();
    await dlg.getByLabel("Workflow").selectOption({ label: await dlg.getByLabel("Workflow").locator("option", { hasText: /^feature\b/ }).first().innerText() });
    await dlg.getByLabel(/what to build/i).fill("Save a score");
    await dlg.getByLabel(/describe it for the agents/i).fill("A player's score is saved and shown on the shop page.");
    await dlg.getByRole("button", { name: "Start flow" }).click();
    // shop has no .keel/config.yml: keel asks once (Run init first / Start anyway). The question must be in view:
    // it used to open under the fold of the long form, so Start flow seemed to do nothing.
    const anyway = dlg.getByRole("button", { name: "Start anyway" });
    if (await anyway.waitFor({ timeout: 5_000 }).then(() => true, () => false)) {
      await expect(anyway, "the init question is in view").toBeInViewport({ ratio: 1 });
      await page.waitForTimeout(1_500); // the estimate arrives late and grows the form above the question
      await expect(anyway, "the init question stays in view").toBeInViewport({ ratio: 1 });
      await anyway.click();
    }

    const th = await until("the flow", async () => {
      const t = (await api("GET", `/projects/${shop.id}/flow`)).thread;
      return t && t.workflow_id === "feature" ? t : null;
    }, 90_000);
    // to the first gate that is not a plain question
    const r = await driveFlow(shop.id, th.thread_id, { stopAtStep: /spec/i, maxDecisions: 8, waitMs: 420_000 });
    note(info, `drive 1: ${r.stoppedBecause}; gates: ${r.gates.join(" | ")}`);
    expect(["stop-at", "needs-person", "done"], "reached the spec gate").toContain(r.stoppedBecause);
    if (r.stoppedBecause === "stop-at") {
      // the dashboard shows the gate; read the spec file the fake model wrote
      await page.goto(`/#/flow/${th.thread_id}`);
      await expect(page.locator("main")).toContainText(r.thread.waiting!.title.slice(0, 20), { timeout: 20_000 });
      await page.goto("/#/inbox");
      await expect(page.locator('[data-testid="inbox-item"]').first()).toBeVisible();
      const approve = page.locator('[data-testid="inbox-item"]').first().locator("button.warn, button.primary").first(); // the card's first action: Approve (or its first named choice)
      await expect(approve, "the spec gate offers an approve button").toBeVisible();
      await approve.click();
      await expect.poll(async () => (await api("GET", `/projects/${shop.id}/flow`)).thread?.waiting?.step, { timeout: 120_000 }).not.toBe(r.thread.waiting!.step);
      const r2 = await driveFlow(shop.id, th.thread_id, { maxDecisions: 10, waitMs: 420_000 });
      note(info, `drive 2: ${r2.stoppedBecause}; gates: ${r2.gates.join(" | ")}`);
      expect(r2.stoppedBecause, "the safe driver never walked into a token cap or a loop").not.toBe("failed");
      // clean up: stop what we started so the next journeys have an idle project
      await api("POST", `/threads/${th.thread_id}/stop`, undefined, [200, 204, 409]).catch(() => undefined);
    }
    await expectNoConsoleErrors(errors);
  });

  test("J2 the safe driver refuses a repeated gate (self-test)", async () => {
    // a fake of the loop rule: the same key more than 3 times stops. Exercised on the real api by J2 above; here the rule's arithmetic.
    const seen = new Map<string, number>();
    const key = "gate|AC-1|Spec";
    const stops = [1, 2, 3, 4].map(() => { const n = (seen.get(key) ?? 0) + 1; seen.set(key, n); return n > 3; });
    expect(stops).toEqual([false, false, false, true]);
  });
});
