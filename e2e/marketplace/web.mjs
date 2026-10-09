// The web half of the marketplace e2e (e2e/marketplace/e2e.py): Control › Plugins and the Inbox in headless Chromium.
//
//   npx -y -p playwright@1 node e2e/marketplace/web.mjs <url> <outdir> <plugin> <source title>
//
// It opens #/plugins and reads the three tabs (Installed lists the plugin, Marketplace shows its card, Sources and
// rules lists the source), the menu's link to the page, the plugin's own page (#/<plugin>, from its web part), and the
// Inbox's plugin-install card. It prints one JSON object: {checks: [{what, ok, detail}], console: [errors]}. On a
// failed check it saves a screenshot into <outdir>.

import fs from "fs";
import { createRequire } from "module";
import os from "os";
import path from "path";

const [base, outDir, plugin, sourceTitle] = process.argv.slice(2);
if (!base || !outDir || !plugin || !sourceTitle) {
  console.error("usage: npx -y -p playwright@1 node e2e/marketplace/web.mjs <url> <outdir> <plugin> <source title>");
  process.exit(2);
}
fs.mkdirSync(outDir, { recursive: true });

/** playwright from `npx -p playwright@1`: npx puts its node_modules/.bin on PATH (an ES module ignores NODE_PATH). */
function loadPlaywright() {
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (!dir.endsWith(path.join("node_modules", ".bin"))) continue;
    try {
      return createRequire(path.join(dir, "..", "_.js"))("playwright");
    } catch {
      /* the next one */
    }
  }
  throw new Error("playwright not found: run me with npx -y -p playwright@1 node e2e/marketplace/web.mjs …");
}

/** Chromium: playwright's own, else the newest headless shell in the browser cache (as e2e/parity/pages.mjs). */
async function launch(chromium) {
  try {
    return await chromium.launch();
  } catch (e) {
    const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH, path.join(os.homedir(), "Library/Caches/ms-playwright"),
      path.join(os.homedir(), ".cache/ms-playwright")].filter(Boolean);
    for (const root of roots) {
      if (!fs.existsSync(root)) continue;
      const shells = fs.readdirSync(root).filter((d) => d.startsWith("chromium_headless_shell-"))
        .sort((x, y) => Number(y.split("-")[1]) - Number(x.split("-")[1]));
      for (const d of shells) {
        for (const sub of fs.readdirSync(path.join(root, d))) {
          const exe = path.join(root, d, sub, "chrome-headless-shell");
          if (fs.existsSync(exe)) return chromium.launch({ executablePath: exe });
        }
      }
    }
    throw e;
  }
}

const checks = [];
const errors = [];
let shot = 0;

async function check(page, what, fn) {
  try {
    const detail = await fn();
    checks.push({ what, ok: true, detail: detail ?? "" });
  } catch (e) {
    const file = path.join(outDir, `web-${++shot}.png`);
    await page.screenshot({ path: file, fullPage: true }).catch(() => {});
    checks.push({ what, ok: false, detail: `${String(e.message ?? e).split("\n")[0]} (screenshot ${file})` });
  }
}

const { chromium } = loadPlaywright();
const browser = await launch(chromium);
try {
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 }, colorScheme: "light" });
  const page = await ctx.newPage();
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text().replaceAll(base, ""));
  });
  page.on("pageerror", (e) => errors.push(`page error: ${e.message}`));
  const projects = await (await ctx.request.get(`${base}/api/projects`)).json().catch(() => []);
  await page.goto(`${base}/#/projects`);
  await page.evaluate((p) => localStorage.setItem("keel2.project", p), projects[0]?.id ?? "");

  await page.goto(`${base}/#/plugins`);
  await page.reload();
  const T = 30_000;
  await check(page, "the menu links to Control › Plugins", async () => {
    await page.locator('nav a[href="#/plugins"], a[href="#/plugins"]').first().waitFor({ timeout: T });
  });
  await check(page, "#/plugins shows its three tabs: Installed, Marketplace, Sources and rules", async () => {
    const tabs = page.getByRole("tablist", { name: "Plugins" }).getByRole("tab");
    await tabs.first().waitFor({ timeout: T });
    const names = (await tabs.allTextContents()).map((t) => t.trim());
    const want = [/^Installed/, /^Marketplace$/, /^Sources and rules$/];
    if (names.length !== 3 || !want.every((re, i) => re.test(names[i]))) throw new Error(`tabs: ${names.join(" | ")}`);
    return names.join(" | ");
  });
  await check(page, `Installed lists ${plugin}`, async () => {
    const row = page.getByTestId(`plugin-${plugin}`);
    await row.waitFor({ timeout: T });
    return (await row.innerText()).replace(/\s+/g, " ").slice(0, 120);
  });
  await check(page, `Marketplace shows ${plugin}'s card`, async () => {
    await page.getByRole("tab", { name: "Marketplace" }).click();
    const card = page.locator("article.mk-card").filter({ hasText: /hello/i }).first();
    await card.waitFor({ timeout: T });
    return (await card.innerText()).replace(/\s+/g, " ").slice(0, 120);
  });
  await check(page, `Sources and rules lists the source "${sourceTitle}"`, async () => {
    await page.getByRole("tab", { name: "Sources and rules" }).click();
    const item = page.locator("li.mk-source").filter({ hasText: sourceTitle }).first();
    await item.waitFor({ timeout: T });
    return (await item.innerText()).replace(/\s+/g, " ").slice(0, 160);
  });
  await check(page, `the plugin's own page #/${plugin} opens (its web part's setup registered it)`, async () => {
    await page.goto(`${base}/#/${plugin}`);
    await page.getByRole("heading", { name: "Hello" }).first().waitFor({ timeout: T });
  });
  await check(page, "the Inbox shows the plugin-install card", async () => {
    await page.goto(`${base}/#/inbox`);
    const card = page.locator("article.k-plugin-install").first();
    await card.waitFor({ timeout: T });
    const text = (await card.innerText()).replace(/\s+/g, " ");
    if (!/hello/i.test(text) || !/Approve and (install|update)/.test(text)) throw new Error(`the card says: ${text.slice(0, 200)}`);
    return text.slice(0, 160);
  });
  // a last moment for late errors (a lazy chunk, a poll)
  await page.waitForTimeout(1500);
} finally {
  await browser.close();
}
console.log(JSON.stringify({ checks, console: errors }));
