// J27 (0.40, #201) Rules: Normal | Safety only. Normal is the default everywhere (Settings and Start a flow); a flow
// started in Safety only shows the chip on the Flow page, and the Rules switch takes it back to Normal; the History
// marks the lines judged in Safety only.
import { expect, test, type Page } from "@playwright/test";
import { api, projectNamed, signIn, startFlow, until, useFakeModel, watchConsole, expectNoConsoleErrors } from "../lib/keel";

const OUT = process.env.KEEL_E2E_OUT || "report";

async function shot(page: Page, name: string) {
  for (const [w, h, tag] of [[1280, 900, "desktop"], [390, 844, "phone"]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${name}-${tag}.png`, fullPage: false });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

test.describe("J27 Rules: Safety only", () => {
  test("J27 Normal by default; a Safety only flow shows its chip and switches back", async ({ page }) => {
    test.setTimeout(5 * 60_000);
    const errors = watchConsole(page);
    await useFakeModel();
    const shop = await projectNamed("shop");
    expect((await api("GET", "/settings/general")).rules_mode).toBe("normal");
    expect((await api("GET", `/projects/${shop.id}/settings`)).effective.rules_mode).toBe("normal");

    await signIn(page);
    await page.goto("/#/settings/general");
    const row = page.getByRole("group", { name: /Rules \(new flows\)/ });
    await expect(row).toBeVisible({ timeout: 30_000 });
    await expect(row.getByRole("button", { name: "Normal" })).toHaveAttribute("aria-pressed", "true");
    await expect(row.getByRole("button", { name: "Safety only" })).toHaveAttribute("aria-pressed", "false");
    await row.scrollIntoViewIfNeeded();
    await shot(page, "j27-settings-rules");

    await page.goto("/#/projects");
    await page.getByRole("button", { name: "Start a flow in shop" }).click();
    const dlg = page.getByRole("dialog").first();
    const pick = dlg.getByTestId("rules-mode-pick");
    await expect(pick).toBeVisible();
    await expect(pick.getByRole("radio", { name: /^Normal/ })).toBeChecked();
    await pick.scrollIntoViewIfNeeded();
    await shot(page, "j27-start-rules");
    await page.keyboard.press("Escape");

    const tid = await startFlow(shop.id, "quick", "Safety only check", { rules_mode: "safety-only", request: "Say hello in the README." });
    await until("the flow waits or ends", async () => {
      const t = (await api("GET", `/projects/${shop.id}/flows/${tid}`)).thread;
      return t && t.status !== "running" ? t : null;
    }, 180_000, 2000);
    const t = (await api("GET", `/projects/${shop.id}/flows/${tid}`)).thread;
    expect(t.rules_mode).toBe("safety-only");

    await page.goto(`/#/flow/${tid}`);
    await expect(page.getByTestId("rules-chip")).toHaveText("Safety only", { timeout: 30_000 });
    expect(((await api("GET", `/threads/${tid}/rules-now`)) as any).rules_mode).toBe("safety-only");
    await expect(page.getByTestId("rn-safety-only")).toBeVisible({ timeout: 20_000 });
    await shot(page, "j27-flow-chip");
    if (t.status === "waiting") {
      const sw = page.getByTestId("rules-switch").getByLabel("Rules");
      await expect(sw).toHaveValue("safety-only");
      await sw.selectOption("normal");
      await expect(page.getByTestId("rules-chip")).toHaveCount(0, { timeout: 20_000 });
      expect((await api("GET", `/projects/${shop.id}/flows/${tid}`)).thread.rules_mode).toBe("normal");
      await api("POST", `/threads/${tid}/stop`, {}, [200, 204, 409]);
    }
    await expectNoConsoleErrors(errors.filter((e) => !/Failed to fetch|NetworkError|ERR_CONNECTION|502|503/.test(e)));
  });
});
