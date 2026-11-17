// The web half of the marketplace e2e's rule pack round (e2e/marketplace/e2e.py, rule_packs): Settings › Plugins ›
// Marketplace › Rules and a pack's page in headless Chromium.
//
//   npx -y -p playwright@1 node e2e/marketplace/rules-web.mjs <url> <outdir> <pack> <pack title>
//
// It opens #/plugins?tab=market&kind=rules (the kind switch, the pack's card with its review in short and "Installed
// here") and #/plugins/rules/<pack> (every rule in a table, who enforces it before the action and after the step, Open
// in Rules). It saves rt_rules_view.png and rt_rule_pack_page.png into <outdir>, and prints one JSON object:
// {checks: [{what, ok, detail}], console: [errors]}.

import fs from "fs";
import { createRequire } from "module";
import os from "os";
import path from "path";

const [base, outDir, pack, title] = process.argv.slice(2);
if (!base || !outDir || !pack || !title) {
  console.error("usage: npx -y -p playwright@1 node e2e/marketplace/rules-web.mjs <url> <outdir> <pack> <pack title>");
  process.exit(2);
}
fs.mkdirSync(outDir, { recursive: true });

function loadPlaywright() {
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (!dir.endsWith(path.join("node_modules", ".bin"))) continue;
    try {
      return createRequire(path.join(dir, "..", "_.js"))("playwright");
    } catch {
      /* the next one */
    }
  }
  throw new Error("playwright not found: run me with npx -y -p playwright@1 node e2e/marketplace/rules-web.mjs …");
}

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
    const file = path.join(outDir, `rt_rules-web-${++shot}.png`);
    await page.screenshot({ path: file, fullPage: true }).catch(() => {});
    checks.push({ what, ok: false, detail: `${String(e.message ?? e).split("\n")[0]} (screenshot ${file})` });
  }
}

const text = async (loc) => (await loc.innerText()).replace(/\s+/g, " ").trim();

const { chromium } = loadPlaywright();
const browser = await launch(chromium);
try {
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, colorScheme: "light" });
  const page = await ctx.newPage();
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text().replaceAll(base, ""));
  });
  page.on("pageerror", (e) => errors.push(`page error: ${e.message}`));
  const projects = await (await ctx.request.get(`${base}/api/projects`)).json().catch(() => []);
  await page.goto(`${base}/#/projects`);
  await page.evaluate((p) => localStorage.setItem("keel2.project", p), projects[0]?.id ?? "");
  const T = 30_000;

  await page.goto(`${base}/#/plugins?tab=market&kind=rules`);
  await page.reload();
  await check(page, "Marketplace's kind switch: Plugins · Workflows · Rules, Rules pressed", async () => {
    const group = page.getByRole("group", { name: "What to find" });
    await group.waitFor({ timeout: T });
    const names = (await group.getByRole("button").allTextContents()).map((t) => t.trim());
    if (names.join("|") !== "Plugins|Workflows|Rules") throw new Error(`kinds: ${names.join(" | ")}`);
    if ((await group.getByRole("button", { name: "Rules" }).getAttribute("aria-pressed")) !== "true") throw new Error("Rules is not pressed");
    return names.join(" · ");
  });
  await check(page, `Marketplace › Rules shows ${title}'s card: its review in short, installed here`, async () => {
    const card = page.getByRole("article", { name: `${title} rule pack` });
    await card.waitFor({ timeout: T });
    await card.getByText("Installed here").waitFor({ timeout: T });
    const t = await text(card);
    if (!t.includes("2 rules · bash 1 · edit 1 · every phase")) throw new Error(`the card says: ${t.slice(0, 200)}`);
    return t.slice(0, 160);
  });
  await page.screenshot({ path: path.join(outDir, "rt_rules_view.png"), fullPage: true });

  await page.goto(`${base}/#/plugins/rules/${pack}`);
  await check(page, `the pack's page lists every rule in a table`, async () => {
    const table = page.getByRole("table", { name: `Rules of ${title}` });
    await table.waitFor({ timeout: T });
    const rows = await table.getByRole("row").count();
    if (rows !== 3) throw new Error(`${rows - 1} rules`);
    return `${rows - 1} rules`;
  });
  await check(page, "who enforces it: bash before the action; edit also after the step (the diff guard)", async () => {
    const bash = await text(page.getByTestId("who-no-install"));
    const edit = await text(page.getByTestId("who-no-lockfile"));
    if (!bash.startsWith("before the action") || bash.includes("after the step")) throw new Error(`bash: ${bash}`);
    if (!edit.includes("after the step the diff guard")) throw new Error(`edit: ${edit}`);
    return bash.slice(0, 140);
  });
  await check(page, "the page links to the core Rules page (Open in Rules)", async () => {
    const href = await page.getByRole("link", { name: "Open in Rules" }).getAttribute("href");
    if (href !== "#/rules") throw new Error(`href ${href}`);
  });
  await page.screenshot({ path: path.join(outDir, "rt_rule_pack_page.png"), fullPage: true });
  await page.waitForTimeout(1500);
} finally {
  await browser.close();
}
console.log(JSON.stringify({ checks, console: errors }));
