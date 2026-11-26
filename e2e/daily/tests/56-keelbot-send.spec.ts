// J29 (#235) KeelBot's chat sends at once: the message is on screen right after Enter and the input is empty, "Sending…"
// ends when the api took it, a failed send keeps the message with the reason and Retry sends it; the chat follows
// KeelBot's text, and scrolled up it stays and offers "New messages ↓". Desktop and phone width, the fake model.
import { expect, test, type Page } from "@playwright/test";
import { note, signIn, useFakeModel, watchConsole, expectNoConsoleErrors } from "../lib/keel";

const OUT = process.env.KEEL_E2E_OUT || "report";
const TURN = "**/api/projects/*/helper/sessions/*/turn";

async function open(page: Page) {
  await page.goto("/#/keelbot");
  const box = page.getByRole("textbox", { name: "Ask KeelBot" });
  await expect(box).toBeVisible({ timeout: 30_000 });
  return box;
}

for (const [w, h, tag] of [[1360, 860, "desktop"], [390, 844, "phone"]] as const) {
  test(`J29 KeelBot sends at once, Retry, New messages (${tag})`, async ({ page }, info) => {
    test.setTimeout(5 * 60_000);
    await page.setViewportSize({ width: w, height: h });
    const errors = watchConsole(page);
    await useFakeModel();
    await signIn(page);
    const box = await open(page);
    await page.getByRole("button", { name: "New chat" }).first().click().catch(() => undefined);  // a list column has its own
    const log = page.getByRole("log", { name: "Conversation" });

    // 1. at once: the message and an empty input, before the api answered (its answer is held back 1.5 s)
    await page.route(TURN, async (r) => { await new Promise((ok) => setTimeout(ok, 1500)); await r.continue(); });
    await box.click();
    await box.pressSequentially("What does this project do?", { delay: 5 });
    const t0 = Date.now();
    await box.press("Enter");
    const out = page.getByTestId("hp-outgoing").filter({ hasText: "What does this project do?" });
    await expect(out).toBeVisible({ timeout: 1000 });
    const shown = Date.now() - t0;
    await expect(box).toHaveValue("");
    await expect(out.getByRole("status")).toHaveText("Sending…");
    await expect(page.getByText("Sending…")).toHaveCount(0, { timeout: 15_000 });
    note(info, `Enter → message on screen: ${shown} ms (the api held back 1.5 s)`);
    expect(shown).toBeLessThan(1000);
    await page.unroute(TURN);
    await expect(log.locator(".hp-help").last()).toBeVisible({ timeout: 60_000 });
    await expect(page.locator(".hp-live")).toHaveCount(0, { timeout: 60_000 });
    await page.screenshot({ path: `${OUT}/j29-sent-${tag}.png` });

    // 2. a failed send: the reason and Retry; Retry sends it
    await page.route(TURN, (r) => r.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ error: "The engine is not running", hint: "Start keel again." }) }));
    await box.pressSequentially("Where are the tests?", { delay: 5 });
    await box.press("Enter");
    const alert = page.getByRole("alert").filter({ hasText: "Not sent" });
    await expect(alert).toContainText("The engine is not running");
    await page.screenshot({ path: `${OUT}/j29-failed-${tag}.png` });
    await page.unroute(TURN);
    await alert.getByRole("button", { name: "Retry" }).click();
    await expect(alert).toHaveCount(0, { timeout: 15_000 });
    await expect(log.locator(".hp-text", { hasText: "Where are the tests?" })).toHaveCount(1, { timeout: 15_000 });
    await expect(page.locator(".hp-live")).toHaveCount(0, { timeout: 60_000 });

    // 3. scrolled up while KeelBot writes: the place stays, New messages ↓ shows, a click goes down
    await box.pressSequentially("Explain each file in detail, one paragraph each.", { delay: 5 });
    await box.press("Enter");
    await page.waitForTimeout(150);
    await log.evaluate((el) => { el.scrollTop = 0; el.dispatchEvent(new Event("scroll")); });
    const more = page.getByRole("button", { name: "New messages: scroll down" });
    await expect(more).toBeVisible({ timeout: 30_000 });
    expect(await log.evaluate((el) => el.scrollTop)).toBeLessThan(50);
    await page.screenshot({ path: `${OUT}/j29-new-messages-${tag}.png` });
    await more.click();
    await expect.poll(() => log.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(40);
    await expect(more).toHaveCount(0);

    // the 503 above is the test's own
    await expectNoConsoleErrors(errors.filter((e) => !/status of 503/.test(e)));
  });
}
