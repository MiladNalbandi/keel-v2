// J22 (0.35) features of 0.29–0.34 that had no journey: Home's Ask opens KeelBot with the question (no popup), and keel
// Product is a feature that is on or off for the whole keel, right on its card in Add features (0.31, one keel).
import { expect, test } from "@playwright/test";
import { projectNamed, signIn, useFakeModel, watchConsole, expectNoConsoleErrors } from "../lib/keel";

const OUT = process.env.KEEL_E2E_OUT || "report";

test.describe("J22 Home Ask and keel Product", () => {
  test("J22 Home's Ask opens KeelBot with the question", async ({ page }) => {
    const errors = watchConsole(page);
    await useFakeModel();
    await signIn(page);
    const shop = await projectNamed("shop");
    await page.goto(`/#/home?project=${shop.id}`);
    const box = page.locator("textarea").first();
    await expect(box).toBeVisible({ timeout: 30_000 });
    await box.fill("Where is the price computed?");
    await page.getByRole("button", { name: /^Ask/ }).last().click();
    await expect(page).toHaveURL(/#\/helper/, { timeout: 30_000 });
    await expect(page.getByText("Where is the price computed?").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.locator("[role=dialog]")).toHaveCount(0);
    await page.screenshot({ path: `${OUT}/j22-home-ask-keelbot.png` });
    expectNoConsoleErrors(errors);
  });

  test("J22 keel Product is off by default and turns on and off on its card", async ({ page }) => {
    await signIn(page);
    await page.goto("/#/features");
    const sw = page.getByRole("group", { name: /Product.* for this keel/i });
    await expect(sw).toBeVisible({ timeout: 30_000 });
    await expect(sw.getByRole("button", { name: "Off" })).toHaveAttribute("aria-pressed", "true");
    await sw.getByRole("button", { name: "On" }).click();
    await expect(sw.getByRole("button", { name: "On" })).toHaveAttribute("aria-pressed", "true", { timeout: 60_000 });
    await page.screenshot({ path: `${OUT}/j22-product-on.png` });
    await sw.getByRole("button", { name: "Off" }).click();
    await expect(sw.getByRole("button", { name: "Off" })).toHaveAttribute("aria-pressed", "true", { timeout: 60_000 });
  });
});
