// J24 (0.36, #188) Review › Files: select lines → Comment opens the comment box on those lines; Ask KeelBot inside the
// box answers there (the fake model), "Use in my comment" puts the answer in the comment, and the comment is kept as
// pending on the lines. The shop project gets a branch with a new file to review; main is checked out again at the end.
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { note, signIn, useFakeModel, watchConsole, expectNoConsoleErrors } from "../lib/keel";

const OUT = process.env.KEEL_E2E_OUT || "report";
const SHOP = process.env.KEEL_E2E_SHOP || "";
const git = (...a: string[]) =>
  execFileSync("git", ["-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false", ...a], { cwd: SHOP, encoding: "utf8" });

async function shot(page: Page, name: string) {
  for (const [w, h, tag] of [[1280, 900, "desktop"], [390, 844, "phone"]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${name}-${tag}.png`, fullPage: false });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

test.describe("J24 Review comment box asks KeelBot", () => {
  test.beforeAll(() => {
    test.skip(!SHOP, "no KEEL_E2E_SHOP");
    git("checkout", "-q", "-B", "review/discount", "main");
    writeFileSync(join(SHOP, "shop/discount.py"), [
      '"""Discounts of the shop."""',
      "",
      "def discount(price: int, percent: int) -> int:",
      "    # a percent over 100 makes the price negative",
      "    return price - price * percent // 100",
      "",
    ].join("\n"));
    git("add", "-A");
    git("commit", "-q", "-m", "feat: discounts");
  });
  test.afterAll(() => {
    if (SHOP) git("checkout", "-q", "main");
  });

  test("J24 Comment on selected lines, Ask KeelBot in the box, Use in my comment", async ({ page }, info) => {
    test.setTimeout(6 * 60_000);
    const errors = watchConsole(page);
    await useFakeModel();
    await signIn(page);
    await page.goto("/#/review");
    await page.getByRole("button", { name: "Review this branch" }).click({ timeout: 30_000 });
    await page.getByRole("tab", { name: /^Files/ }).click({ timeout: 30_000 });
    await page.getByRole("button", { name: /discount\.py/ }).first().click();
    const diff = page.getByRole("region", { name: "Changes of shop/discount.py" });
    const row = (re: RegExp) => diff.locator(".rvn-dl-w", { hasText: re }).first();
    await expect(row(/def discount/)).toBeVisible({ timeout: 30_000 });

    // select two lines with the mouse, as a person does
    const a = await row(/def discount/).locator(".rvn-dl-t").boundingBox();
    const b = await row(/return price/).locator(".rvn-dl-t").boundingBox();
    await page.mouse.move(a!.x + 2, a!.y + a!.height / 2);
    await page.mouse.down();
    await page.mouse.move(b!.x + b!.width - 2, b!.y + b!.height / 2, { steps: 8 });
    await page.mouse.up();
    const bar = page.getByRole("toolbar", { name: "Selected code" });
    await expect(bar.getByRole("button", { name: "Comment" })).toBeVisible();
    await shot(page, "j24-selection-bar");
    await bar.getByRole("button", { name: "Comment" }).click();

    const box = diff.getByRole("form", { name: "Comment on lines 3–5" });
    await expect(box).toBeVisible();
    await expect(box.getByLabel("The code the comment is on")).toContainText("return price - price * percent // 100");
    await box.getByRole("textbox", { name: "Your comment on lines 3–5" }).fill("Is this safe?");
    await box.getByRole("button", { name: "Ask KeelBot" }).click();
    await box.getByRole("textbox", { name: "Your question to KeelBot" }).fill("What happens when percent is over 100?");
    await box.getByRole("textbox", { name: "Your question to KeelBot" }).press("Enter");
    await expect(box.getByRole("status")).toBeVisible();
    const answer = box.getByLabel("KeelBot's answer");
    await expect(answer).toBeVisible({ timeout: 180_000 });
    const said = (await answer.innerText()).trim();
    note(info, `KeelBot (fake model) answered: ${said.slice(0, 160)}`);
    expect(said.length).toBeGreaterThan(0);
    await shot(page, "j24-answer-in-box");
    await box.getByRole("button", { name: "Use in my comment" }).click();
    const comment = box.getByRole("textbox", { name: "Your comment on lines 3–5" });
    await expect(comment).toHaveValue(/^Is this safe\?\n\n\S/);
    await box.getByRole("button", { name: "Add to my review" }).click();
    await expect(diff.locator(".rvn-mark.pending")).toHaveCount(1, { timeout: 15_000 });
    await shot(page, "j24-pending");

    await expectNoConsoleErrors(errors);
  });
});
