// J28 (#233) Home tells what keel gives your agent at first sight: six things (project knowledge, code and commit
// style, rules, Wiki, Graph, shared context), the commit-style example, and "Connect your agent" (Help › Start here,
// the MCP step). Hide folds it to one line and it stays folded after a reload; the one line opens it again. Desktop
// and phone width, no console errors.
import { expect, test, type Page } from "@playwright/test";
import { projectNamed, signIn, watchConsole, expectNoConsoleErrors } from "../lib/keel";

const OUT = process.env.KEEL_E2E_OUT || "report";

async function shot(page: Page, name: string) {
  for (const [w, h, tag] of [[1280, 900, "desktop"], [390, 844, "phone"]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${name}-${tag}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

test.describe("J28 Home: what keel gives your agent", () => {
  test("J28 the message at first sight, Connect your agent, Hide and open again", async ({ page }) => {
    const errors = watchConsole(page);
    const shop = await projectNamed("shop");
    await signIn(page);
    await page.goto(`/#/home?project=${shop.id}`);
    await page.evaluate(() => localStorage.removeItem("keel2.home.intro"));
    await page.reload();

    const intro = page.locator("section", { has: page.getByRole("heading", { name: /Your agent connects to keel/ }) });
    await expect(intro).toBeVisible({ timeout: 30_000 });
    await expect(intro).toContainText("does not take control of your agents");
    const gives = intro.getByRole("list", { name: "What keel gives your agent" }).getByRole("listitem");
    await expect(gives).toHaveCount(6);
    await expect(gives.locator("b")).toHaveText(["Project knowledge", "Code and commit style", "Rules", "Wiki", "Graph", "Shared context"]);
    await expect(intro).toContainText("Your agent wants to commit.");
    // the box to ask or build is still on the page, under the message
    await expect(page.getByRole("heading", { name: `Ask keel about ${shop.name}` })).toBeVisible();
    await shot(page, "j28-home-intro");

    // phone width: the six things fit (no sideways scroll)
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    await page.setViewportSize({ width: 1280, height: 900 });

    await intro.getByRole("link", { name: "Connect your agent ›" }).click();
    await expect(page).toHaveURL(/#\/help/);
    await expect(page.getByText(/Connect your AI coding tool to keel \(MCP\)/).first()).toBeVisible({ timeout: 20_000 });

    await page.goto(`/#/home?project=${shop.id}`);
    await page.getByRole("button", { name: "Hide what keel gives your agent" }).click();
    await expect(page.getByRole("heading", { name: /Your agent connects to keel/ })).toHaveCount(0);
    await page.reload();
    const again = page.getByRole("button", { name: "What keel gives your agent ›" });
    await expect(again).toBeVisible({ timeout: 30_000 });
    await shot(page, "j28-home-folded");
    await again.click();
    await expect(page.getByRole("heading", { name: /Your agent connects to keel/ })).toBeVisible();
    await expectNoConsoleErrors(errors);
  });
});
