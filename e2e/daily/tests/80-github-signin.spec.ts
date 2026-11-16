// J8 Connections -> GitHub sign-in shows the code and the link (a fake `gh` on keel's PATH: e2e/daily/bin/gh-fake).
import { expect, test } from "@playwright/test";
import { api, bug, signIn, until, watchConsole, expectNoConsoleErrors } from "../lib/keel";

test.describe("J8 GitHub sign-in", () => {
  test("J8 the api shows code AB12-CD34 and the device link", async () => {
    const v = await api("POST", "/github/login", { host: "github.com" });
    const done = await until("the login code", async () => {
      const s = await api("GET", `/github/login/${v.id}`);
      return s.code ? s : null;
    }, 40_000, 800);
    expect(done.code).toBe("AB12-CD34");
    expect(JSON.stringify(done)).toContain("github.com/login/device");
    await api("DELETE", `/github/login/${v.id}`, undefined, [200, 204]);
  });

  test("J8 the Connections page: Sign in with GitHub shows the code and the link", async ({ page }, info) => {
    const errors = watchConsole(page);
    await signIn(page);
    await page.goto("/#/connections");
    // the fake gh starts signed in (keel shows "connected as e2e-user"): sign out first, then the sign-in is offered
    const signOut = page.getByRole("button", { name: "Sign out" }).first();
    const signInBtn = page.getByRole("button", { name: "Sign in with GitHub" });
    await expect(signOut.or(signInBtn).first()).toBeVisible({ timeout: 20_000 });
    if (await signOut.isVisible().catch(() => false)) {
      await signOut.click();
      const sure = page.getByRole("dialog").getByRole("button", { name: /sign out/i }).first();
      if (await sure.isVisible().catch(() => false)) await sure.click();
    }
    await signInBtn.click();
    const drawer = page.getByRole("dialog", { name: /GitHub login/i });
    await drawer.getByRole("button", { name: "Start login" }).click();
    const shown = page.getByText("AB12-CD34");
    if (!(await shown.waitFor({ timeout: 40_000 }).then(() => true, () => false))) {
      bug(info, { title: "GitHub sign-in does not show the one-time code", steps: ["Connections", "GitHub: Sign in"], expected: "code AB12-CD34 and a link to github.com/login/device", actual: "no code on the page after 40 s" });
    } else {
      await expect(page.locator('a[href*="github.com/login/device"]').first()).toBeVisible();
    }
    const close = page.getByRole("dialog", { name: /GitHub login/i }).getByRole("button", { name: /cancel|close/i }).first();
    if (await close.isVisible().catch(() => false)) await close.click();
    await expectNoConsoleErrors(errors);
  });
});
