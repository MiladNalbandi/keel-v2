// The web half of the Start here e2e (e2e/starthere/e2e.py), in headless Chromium.
//
//   KEEL_SIGNIN=<link> KEEL_MCP_TOKEN=<write token> npx -y -p playwright@1 node e2e/starthere/walk.mjs <url> <outdir>
//
// 1. the first open (1440 px): the welcome (the update notice waits for it); its animations (none with reduced motion),
//    the tour's <video>; its five steps to "keel is ready"; then the notice; Later hides it; a reload shows neither
// 2. Help › Start here: the five steps, step 3's tool tabs and levels with Copy, the dot turns green on the first MCP
//    call (this script calls keel as an MCP client would: GET /api/projects with the token and X-Keel-Client), the short
//    guides, one guide and the search; "See the welcome again"
// 3. Settings › Access: where keel answers (the command for Wi-Fi, Save disabled), Devices, AI tools, Updates; the old
//    #/settings/devices link
// 4. the notice once per version: a new browser (a fresh context) shows it again until Later
// 5. 390 px: the welcome, Start here, Access and the notice; nothing scrolls the page sideways
// It prints one JSON object {checks: [{what, ok, detail}]} and saves a screenshot of each stage into <outdir>.

import fs from "fs";
import { createRequire } from "module";
import os from "os";
import path from "path";

const [base, outDir] = process.argv.slice(2);
if (!outDir) {
  console.error("usage: npx -y -p playwright@1 node e2e/starthere/walk.mjs <url> <outdir>");
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
  throw new Error("playwright not found: run me with npx -y -p playwright@1 node e2e/starthere/walk.mjs …");
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
          for (const name of ["chrome-headless-shell", "headless_shell"]) {
            const exe = path.join(root, d, sub, name);
            if (fs.existsSync(exe)) return chromium.launch({ executablePath: exe });
          }
        }
      }
    }
    throw e;
  }
}

const checks = [];
const errors = [];
const failed = [];
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (what, ok, detail = "") => checks.push({ what, ok: !!ok, detail: ok ? "" : String(detail).slice(0, 600) });
const shot = (page, name, opts = {}) => page.screenshot({ path: path.join(outDir, name), ...opts }).catch(() => undefined);
const sideways = (page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

const { chromium } = loadPlaywright();
const browser = await launch(chromium);

async function newPage(width, height, opts = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: "dark", reducedMotion: "reduce", ...opts });
  await ctx.grantPermissions(["clipboard-read", "clipboard-write"], { origin: base }).catch(() => undefined);
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().replaceAll(base, "").slice(0, 300)); });
  page.on("pageerror", (e) => errors.push("page error: " + String(e.message).slice(0, 300)));
  page.on("response", (r) => { if (r.status() >= 400) failed.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  // keel's api answers a signed-in browser only: e2e/keelauth.py hands a one-time link (one per walk: each context
  // signs in with it while it is valid, or the first context's cookie is copied)
  return { ctx, page };
}

async function signIn(ctx, page, cookies) {
  if (cookies) await ctx.addCookies(cookies);
  else await page.goto(process.env.KEEL_SIGNIN);
}

const clip = (page) => page.evaluate(() => navigator.clipboard.readText()).catch((e) => `(no clipboard: ${e})`);

try {
  // ---- 1. the first open, 1440 px, motion allowed: the welcome and the update notice ----
  const first = await newPage(1440, 900, { reducedMotion: "no-preference" });
  await signIn(first.ctx, first.page);
  const cookies = await first.ctx.cookies();
  const page = first.page;
  await page.goto(base + "/#/projects");
  const welcome = page.getByRole("dialog", { name: "Welcome to keel" });
  await welcome.waitFor({ timeout: 30_000 });
  check("the first open shows the welcome", await welcome.isVisible());
  await wait(2500);
  check("the update notice waits while the welcome is open (it would cover Skip)",
    (await page.getByRole("dialog", { name: /is out/ }).count()) === 0);
  const brand = welcome.locator(".wl-brand");
  const brandColor = await brand.evaluate((el) => getComputedStyle(el).color);
  const markFill = await brand.locator("path").getAttribute("fill");
  check(`the welcome's header: keel's purple mark (${markFill}) and "keel" in white (${brandColor})`,
    markFill === "#8b6dff" && brandColor === "rgb(255, 255, 255)", `${markFill} ${brandColor}`);
  const video = welcome.locator("video");
  check(`the tour's <video> is there: ${await video.getAttribute("src")}`, /keel-v2-explainer\.mp4$/.test((await video.getAttribute("src")) ?? ""),
    await video.getAttribute("src"));
  const cover = await welcome.getByRole("img", { name: /4-minute tour/ }).evaluate((img) => img.complete && img.naturalWidth);
  check(`the cover image is in the bundle and loads (${cover} px wide)`, cover > 100, cover);
  const ring = await welcome.getByRole("button", { name: "Play the 4-minute tour" }).evaluate((el) => getComputedStyle(el).animationName);
  const drift = await welcome.locator(".wl-glow.a").evaluate((el) => getComputedStyle(el).animationName);
  check(`animations on: the play button pulses (${ring}), the glows drift (${drift})`, ring.includes("wl-ring") && drift.includes("wl-drift"),
    `${ring} ${drift}`);
  await shot(page, "01-first-open-1440.png");

  // the welcome's steps
  await welcome.getByRole("button", { name: "Set up keel · 5 steps, ~5 min" }).click();
  for (let n = 1; n <= 5; n++) {
    const h = welcome.getByRole("heading", { level: 1 });
    await welcome.getByText(new RegExp(`^Step ${n} of 5`)).waitFor({ timeout: 15_000 });
    await wait(700);
    await shot(page, `02-welcome-step${n}-1440.png`);
    check(`welcome step ${n}: ${await h.innerText()}`, true);
    if (n === 3) {
      const cmd = await welcome.getByLabel("Claude Code command", { exact: true }).innerText();
      check(`step 3 in the welcome has the command: ${cmd}`, cmd.trim() === "claude mcp add keel -- keel2 mcp --write", cmd);
    }
    const next = welcome.getByRole("button", { name: n === 3 ? "Next" : /^(Next|Later)$/ }).first();
    await next.click();
  }
  await welcome.getByRole("heading", { level: 1, name: "keel is ready" }).waitFor({ timeout: 15_000 });
  await wait(700);
  await shot(page, "03-welcome-ready-1440.png");
  check("the five steps lead to “keel is ready”", true);
  await welcome.getByRole("button", { name: "Open Start here" }).click();

  // the update notice, now that the welcome is closed
  const notice = page.getByRole("dialog", { name: "keel 0.18.99 is out" });
  await notice.waitFor({ timeout: 30_000 });
  check(`then the update notice shows: ${(await notice.innerText()).split("\n").slice(0, 2).join(" · ")}`, await notice.isVisible());
  const box = await notice.boundingBox();
  check(`the notice sits at the top right (${Math.round(box.x)}, ${Math.round(box.y)}, ${Math.round(box.width)} wide)`,
    box && box.y < 40 && box.x + box.width > 1440 - 40, JSON.stringify(box));
  const items = await notice.getByRole("listitem").allInnerTexts();
  check(`what is new: the first ${items.length} lines of the notes`, items.length === 4 && /Settings › Access/.test(items[0]), items.join(" | "));
  await wait(500);
  await shot(page, "03b-notice-1440.png");
  await notice.getByRole("button", { name: "Copy the update command" }).click();
  check(`Copy the update command copies: ${await clip(page)}`, /keel2 update/.test(await clip(page)), await clip(page));
  await notice.getByRole("button", { name: "Later" }).click();
  check("Later hides the notice", await notice.isHidden());
  await page.reload();
  await page.getByRole("heading", { level: 1 }).first().waitFor({ timeout: 30_000 });
  await wait(1500);
  check("after a reload: no welcome (finished), no notice (Later)",
    (await page.getByRole("dialog", { name: "Welcome to keel" }).count()) === 0 && (await page.getByRole("dialog", { name: /is out/ }).count()) === 0);

  // ---- 2. Help › Start here ----
  await page.goto(base + "/#/help");
  await page.getByRole("heading", { level: 1, name: "Start here" }).waitFor({ timeout: 30_000 });
  await page.getByText(/of 5 done/).waitFor({ timeout: 30_000 });
  const progress = await page.getByText(/of 5 done/).innerText();
  check(`Start here: ${progress}`, /^[12] of 5 done$/.test(progress), progress);
  check("the menu has Help › Start here", await page.getByRole("navigation", { name: "Screens" }).getByRole("link", { name: "Start here" }).isVisible());
  const step3 = page.getByTestId("step-mcp");
  const live = step3.getByTestId("mcp-live");
  check(`step 3 waits: ${await live.innerText()}`, /Waiting for the first call from Claude Code/.test(await live.innerText()), await live.innerText());
  for (const [tab, re] of [["opencode", /"type": "local"/], ["Codex", /codex mcp add keel -- keel2 mcp --write/], ["Cursor / VS Code", /"mcpServers"/], ["Claude Code", /claude mcp add keel/]]) {
    await step3.getByRole("tab", { name: tab }).click();
    const text = await step3.getByRole("tabpanel").innerText();
    check(`step 3 tab ${tab}: its own command or config`, re.test(text), text.slice(0, 300));
    if (tab === "opencode") await shot(page, "04-start-opencode-1440.png");
  }
  await step3.getByRole("radio", { name: /Read only/ }).check();
  check(`level Read only: ${await step3.getByLabel("Claude Code command", { exact: true }).innerText()}`,
    (await step3.getByLabel("Claude Code command", { exact: true }).innerText()).trim() === "claude mcp add keel -- keel2 mcp");
  await step3.getByRole("radio", { name: /Admin/ }).check();
  check("level Admin warns", /Pick it only when you need it/.test(await step3.innerText()));
  await step3.getByRole("radio", { name: /Write/ }).check();
  await step3.getByRole("button", { name: "Copy: Claude Code command" }).click();
  check(`Copy: ${await clip(page)}`, (await clip(page)) === "claude mcp add keel -- keel2 mcp --write", await clip(page));
  await step3.getByText("It does not connect").click();
  check("“It does not connect” names keel2 doctor and the Inbox", /keel2 doctor/.test(await step3.innerText()) && /Inbox/.test(await step3.innerText()));
  await step3.scrollIntoViewIfNeeded();
  await shot(page, "05-start-waiting-1440.png");
  // the first MCP call, as Claude Code with keel2 mcp --write would make it
  const r = await fetch(base + "/api/projects", { headers: { "X-Keel-Mcp-Token": process.env.KEEL_MCP_TOKEN || "", "X-Keel-Client": "claude-code" } });
  check(`an MCP call reaches keel (${r.status})`, r.ok, r.status);
  await live.getByText(/Connected\./).waitFor({ timeout: 15_000 }).catch(() => undefined);
  check(`the dot turns green by itself: ${await live.innerText()}`, /keel saw claude-code \(write\)/.test(await live.innerText()), await live.innerText());
  const progress2 = await page.getByText(/of 5 done/).innerText();
  await page.reload();
  await page.getByText(/of 5 done/).waitFor({ timeout: 30_000 });
  const progress3 = await page.getByText(/of 5 done/).innerText();
  check(`step 3 counts as done: ${progress2} → after a reload ${progress3}`, Number(progress3[0]) > Number(progress[0]), `${progress} ${progress3}`);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "06-start-1440.png", { fullPage: true });
  check(`1440 px: Start here does not scroll sideways (${await sideways(page)} px)`, (await sideways(page)) <= 1);
  // the guides
  const guides = page.getByRole("region", { name: "Short guides" });
  const links = await guides.getByRole("link").allInnerTexts();
  check(`${links.length - 1} short guides and the long one: ${links.slice(0, 4).join(", ")}…`, links.length >= 13 && links.includes("All of the guide (long) ›"), links.join(" | "));
  await guides.getByRole("searchbox").fill("tailscale");
  const hits = await guides.getByRole("list", { name: "Search results" }).getByRole("link").allInnerTexts();
  check(`search “tailscale”: ${hits.join(", ")}`, hits.includes("Use keel from your phone"), hits.join(" | "));
  await shot(page, "07-start-search-1440.png");
  await guides.getByRole("link", { name: "Use keel from your phone" }).click();
  await page.getByRole("heading", { level: 1, name: "Use keel from your phone" }).waitFor({ timeout: 15_000 });
  check("a guide opens in the app", /keel2 start --wifi/.test(await page.getByTestId("guide").innerText()));
  await shot(page, "08-guide-1440.png");

  // ---- 3. Settings › Access ----
  await page.goto(base + "/#/settings/devices");
  await page.getByRole("heading", { level: 1, name: "Access" }).waitFor({ timeout: 30_000 });
  await wait(300);
  check(`the old Devices link opens Access (${await page.evaluate(() => location.hash)})`, (await page.evaluate(() => location.hash)) === "#/settings/access");
  const where = page.getByRole("radiogroup", { name: "Where keel answers" });
  await where.getByRole("radio", { name: /This computer only/ }).waitFor();
  await page.waitForFunction(() => document.querySelector('[role="radio"][aria-checked="true"]')?.textContent?.includes("now"), null, { timeout: 15_000 });
  check("where keel answers: this computer only, now", (await where.getByRole("radio", { name: /This computer only/ }).getAttribute("aria-checked")) === "true");
  check("Save and restart keel is not there yet (disabled)", await page.getByRole("button", { name: "Save and restart keel" }).isDisabled());
  await where.getByRole("radio", { name: /My Wi-Fi/ }).click();
  const cmd = await page.getByLabel("keel2 command", { exact: true }).innerText();
  check(`Wi-Fi: the command to run, ${cmd.trim()}, and the plain-http warning`, /^keel2 start --wifi/.test(cmd.trim()) && /plain http/.test(await where.innerText()), cmd);
  for (const part of ["Devices", "AI tools (MCP) and the terminal", "Updates"])
    check(`Access has ${part}`, await page.getByRole("heading", { level: 2, name: part, exact: true }).isVisible());
  check("Devices: Pair a phone and Sign out every browser", await page.getByRole("button", { name: "Pair a phone (QR code)" }).isVisible()
    && await page.getByRole("button", { name: "Sign out every browser" }).isVisible());
  const tools = await page.getByRole("list", { name: "AI tools and terminal apps" }).innerText().catch(() => "");
  check(`AI tools: ${tools.split("\n")[0]}`, /claude-code · write/.test(tools) && /today/.test(tools), tools);
  const upd = page.getByRole("region", { name: "Updates" });
  check(`Updates: ${(await upd.innerText()).split("\n")[1]}`, /a newer one: 0\.18\.99/.test(await upd.innerText()), await upd.innerText());
  await shot(page, "09-access-1440.png", { fullPage: true });
  check(`1440 px: Access does not scroll sideways (${await sideways(page)} px)`, (await sideways(page)) <= 1);
  await first.ctx.close();

  // ---- 4 + 5. a new browser at 390 px: the notice shows again (this browser did not say Later); the welcome again ----
  const phone = await newPage(390, 844);
  await signIn(phone.ctx, phone.page, cookies);
  const p = phone.page;
  await p.goto(base + "/#/help");
  await p.getByRole("heading", { level: 1, name: "Start here" }).waitFor({ timeout: 30_000 });
  const n2 = p.getByRole("dialog", { name: "keel 0.18.99 is out" });
  await n2.waitFor({ timeout: 30_000 });
  check("a new browser: the notice shows (once per version, per browser), and no welcome (keel remembers it)",
    (await p.getByRole("dialog", { name: "Welcome to keel" }).count()) === 0);
  await wait(400);
  await shot(p, "10-notice-390.png");
  check(`390 px: the notice fits (${await sideways(p)} px over)`, (await sideways(p)) <= 1);
  const animNone = await n2.evaluate((el) => getComputedStyle(el).animationName);
  check(`reduced motion: the notice does not animate (${animNone})`, animNone === "none", animNone);
  await n2.getByRole("button", { name: "Later" }).click();
  await p.getByText(/of 5 done/).waitFor({ timeout: 30_000 });
  await shot(p, "11-start-390.png", { fullPage: true });
  check(`390 px: Start here does not scroll sideways (${await sideways(p)} px)`, (await sideways(p)) <= 1);
  await p.getByRole("button", { name: "See the welcome again" }).click();
  const w2 = p.getByRole("dialog", { name: "Welcome to keel" });
  await w2.waitFor({ timeout: 15_000 });
  const ringOff = await w2.getByRole("button", { name: "Play the 4-minute tour" }).evaluate((el) => getComputedStyle(el).animationName);
  check(`reduced motion: the welcome does not animate (${ringOff})`, ringOff === "none", ringOff);
  await shot(p, "12-welcome-390.png");
  const wOver = await w2.evaluate((el) => el.scrollWidth - el.clientWidth);
  check(`390 px: the welcome does not scroll sideways (${wOver} px)`, wOver <= 1 && (await sideways(p)) <= 1);
  await w2.getByRole("button", { name: "Set up keel · 5 steps, ~5 min" }).click();
  await w2.getByRole("button", { name: "Next" }).click();
  await w2.getByRole("button", { name: /^Next$/ }).first().click();
  await w2.getByText(/^Step 3 of 5/).waitFor({ timeout: 15_000 });
  await shot(p, "13-welcome-step3-390.png", { fullPage: true });
  const w3 = await w2.evaluate((el) => el.scrollWidth - el.clientWidth);
  check(`390 px: welcome step 3 does not scroll sideways (${w3} px)`, w3 <= 1);
  await w2.getByRole("button", { name: "Skip, I know keel" }).click();
  check("Skip closes it", (await p.getByRole("dialog", { name: "Welcome to keel" }).count()) === 0);
  await p.goto(base + "/#/settings/access");
  await p.getByRole("heading", { level: 1, name: "Access" }).waitFor({ timeout: 30_000 });
  await wait(600);
  await shot(p, "14-access-390.png", { fullPage: true });
  check(`390 px: Access does not scroll sideways (${await sideways(p)} px)`, (await sideways(p)) <= 1);
  await p.goto(base + "/#/help/connect-an-ai-tool");
  await p.getByRole("heading", { level: 1, name: "Connect an AI tool (MCP)" }).waitFor({ timeout: 15_000 });
  await shot(p, "15-guide-390.png", { fullPage: true });
  check(`390 px: a guide does not scroll sideways (${await sideways(p)} px)`, (await sideways(p)) <= 1);

  const bad = failed.filter((f) => !/\/api\/web\/code/.test(f));
  check("no console error and no failed request on the way", !errors.length && !bad.length, [...errors, ...bad].join(" | "));
} catch (e) {
  check("the walk ran to its end", false, e instanceof Error ? e.stack || e.message : String(e));
}
await browser.close();
console.log(JSON.stringify({ checks, errors, failed }));
