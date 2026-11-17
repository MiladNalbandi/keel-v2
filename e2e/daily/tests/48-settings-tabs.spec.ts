// J22 (0.35) Settings: changing the tab draws only the panel. The page, its head place and the menu stay the same
// elements (nothing above the panel flashes), the scroll stays, and a warning on Settings › Review reads as one sentence.
import { expect, test, type Page } from "@playwright/test";
import { signIn, watchConsole, expectNoConsoleErrors } from "../lib/keel";

const OUT = process.env.KEEL_E2E_OUT || "report";

async function shot(page: Page, name: string) {
  for (const [w, h, tag] of [[1280, 900, "desktop"], [390, 844, "phone"]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${name}-${tag}.png`, fullPage: false });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

test.describe("J22 Settings tabs", () => {
  test("J22 the menu stays while the tab changes; only the panel is new", async ({ page }) => {
    test.setTimeout(4 * 60_000);
    const errors = watchConsole(page);
    await signIn(page);
    await page.goto("/#/settings/general");
    await expect(page.getByRole("heading", { level: 1, name: "General" })).toBeVisible({ timeout: 30_000 });

    // mark the page and the menu: a page drawn again would lose the marks
    await page.evaluate(() => {
      document.querySelector('[data-testid="settings-page"]')?.setAttribute("data-mark", "page");
      document.querySelector('nav[aria-label="Settings sections"]')?.setAttribute("data-mark", "menu");
    });
    const menu = page.getByRole("navigation", { name: "Settings sections" });
    for (const [label, title] of [
      [/Models & keys/, "Models & keys"],
      [/^Review/, "Review"],
      [/Connections/, "Connections"],
      [/Budget/, "Budget"],
      [/General/, "General"],
    ] as const) {
      await menu.getByRole("link", { name: label }).click();
      await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible({ timeout: 15_000 });
      await expect(page.locator('[data-testid="settings-page"][data-mark="page"]')).toHaveCount(1);
      await expect(page.locator('nav[aria-label="Settings sections"][data-mark="menu"]')).toHaveCount(1);
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    }

    // Settings › Review: a warning is one sentence (no line per word group)
    await menu.getByRole("link", { name: /^Review/ }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Review" })).toBeVisible();
    const warn = page.locator(".ss-cardp.ss-warn-text").first();
    if (await warn.count()) {
      const display = await warn.evaluate((el) => getComputedStyle(el).display);
      expect(display).toBe("block");
    }
    await shot(page, "j22-settings-review");
    await menu.getByRole("link", { name: /Models & keys/ }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Models & keys" })).toBeVisible();
    await shot(page, "j22-settings-models");
    await expectNoConsoleErrors(errors);
  });
});
