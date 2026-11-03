// The web half of the core-only e2e (e2e/core/e2e.py): a keel with only its core, in headless Chromium.
//
//   npx -y -p playwright@1 node e2e/core/walk.mjs <url> <outdir> [<hash> …]
//
// It opens every page of the menu, #/projects and the extra pages it is given (a project's flows: #/flow/<thread>),
// each with a full load, and clicks every tab of the main area. On each page it records the console errors, the failed
// requests (a status of 400 or more, or no answer) and every link in the main area and the menu to a page this keel
// does not have (the page registry: window.__keel.sdk.pageFor). It also reads the menu (the Project group is the
// "No pages yet" box, the logo's tag says core; the welcome's sets come from /api/plugins/sets), the launcher (⌘K: only keel's own scopes and actions; "?" asks nobody) and the key sheet (no
// KeelBot, no diagram keys). It prints one JSON object: {checks: [{what, ok, detail}], pages: {<hash>: {…}}}. A failed
// check saves a screenshot into <outdir>.

import fs from "fs";
import { createRequire } from "module";
import os from "os";
import path from "path";

const [base, outDir, ...extra] = process.argv.slice(2);
if (!base || !outDir) {
  console.error("usage: npx -y -p playwright@1 node e2e/core/walk.mjs <url> <outdir> [<hash> …]");
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
  throw new Error("playwright not found: run me with npx -y -p playwright@1 node e2e/core/walk.mjs …");
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

// A plugin's api route: what a keel with only its core must never ask for (the parts that are plugins since step 3).
const PLUGIN_ROUTE = new RegExp(
  "/api/projects/[^/]+/(repo|helper|graph|index|wiki|map|tasks|ci|db|git|review)(/|\\?|$)" +
    "|/api/(jira|gitlab|mcp-catalog|tasks|inbox/tasks)(/|\\?|$)|^/plugins/",
);
// at most this many tabs are clicked on one page
const MAX_TABS = 12;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const checks = [];
const pages = {};
let shot = 0;

const { chromium } = loadPlaywright();
const browser = await launch(chromium);
const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 }, colorScheme: "light", reducedMotion: "reduce" });
const page = await ctx.newPage();
// v0.18.1 keel's api answers a signed-in browser only (api auth/LocalAuth): e2e/keelauth.py hands a one-time link
if (process.env.KEEL_SIGNIN) await page.goto(process.env.KEEL_SIGNIN);
const seen = { console: [], failed: [], plugin: [], inflight: new Map() };
const rel = (url) => {
  const u = new URL(url);
  return u.pathname + u.search;
};
page.on("console", (m) => {
  if (m.type() === "error") seen.console.push(m.text().replaceAll(base, "").slice(0, 300));
});
page.on("pageerror", (e) => seen.console.push("page error: " + String(e.message).slice(0, 300)));
page.on("request", (r) => {
  seen.inflight.set(r, Date.now());
  // a plugin's route is not asked for at all, whatever the answer
  const p = new URL(r.url()).pathname;
  if (r.url().startsWith(base) && PLUGIN_ROUTE.test(p)) seen.plugin.push(`${r.method()} ${rel(r.url())}`);
});
page.on("requestfinished", (r) => seen.inflight.delete(r));
page.on("requestfailed", (r) => {
  seen.inflight.delete(r);
  const why = r.failure()?.errorText ?? "";
  // a request the page dropped when it moved on (the event stream, a superseded fetch) is not a failure
  if (!/ERR_ABORTED|NS_BINDING_ABORTED/.test(why)) seen.failed.push(`${r.method()} ${rel(r.url())} ${why}`);
});
page.on("response", (res) => {
  if (res.status() >= 400) seen.failed.push(`${res.request().method()} ${rel(res.url())} → ${res.status()}`);
});

async function check(what, ok, detail = "") {
  let file = "";
  if (!ok) {
    file = path.join(outDir, `failed-${++shot}.png`);
    await page.screenshot({ path: file }).catch(() => (file = ""));
  }
  checks.push({ what, ok: !!ok, detail: String(detail).slice(0, 1500), shot: file });
}

/** Until the main area has stopped changing: no spinner, the same content for 600 ms, no request in flight (but the
 *  event stream), and the plugins' web parts have started (there are none: at once). */
async function settle(max = 12000) {
  const t0 = Date.now();
  await page.waitForSelector("main", { timeout: max }).catch(() => {});
  let last = "";
  let still = Date.now();
  while (Date.now() - t0 < max) {
    const sig = await page.evaluate(() => {
      const m = document.querySelector("main");
      const loading = !!document.querySelector("main .pg-spin, main .pg-skel, .empty.loading");
      return `${loading ? "loading " : ""}${m ? `${m.innerHTML.length}:${m.getElementsByTagName("*").length}` : "-"}`;
    }).catch(() => "loading");
    const busy = [...seen.inflight].some(([r, at]) => Date.now() - at < 5000 && r.resourceType() !== "eventsource" && !r.url().includes("/api/events"));
    if (sig !== last || sig.startsWith("loading")) {
      last = sig;
      still = Date.now();
    } else if (Date.now() - still > 600 && !busy) return;
    await wait(100);
  }
}

/** Links (in the main area, the menu and any dialog, or in `where`) to a page this keel does not have. */
function deadLinks(where = "main a[href^='#'], nav a[href^='#'], [role=dialog] a[href^='#']") {
  return page.evaluate((sel) => {
    const sdk = window.__keel?.sdk;
    // keel's own screen that is not a registered page (the router knows it)
    const own = new Set(["projects"]);
    const out = [];
    for (const a of document.querySelectorAll(sel)) {
      const href = a.getAttribute("href") ?? "";
      if (!href.startsWith("#/")) {
        // an anchor in the page (#main) is fine when the element is there
        if (href.length > 1 && !document.getElementById(href.slice(1))) out.push(`${href} (no such element)`);
        continue;
      }
      const first = decodeURIComponent(href.slice(2).split(/[/?]/)[0]);
      if (!first || own.has(first) || sdk?.pageFor(first)) continue;
      out.push(`${href} "${(a.getAttribute("aria-label") || a.textContent || "").replace(/\s+/g, " ").trim().slice(0, 50)}"`);
    }
    return [...new Set(out)];
  }, where);
}

/** What the page shows that says a part is missing (an add-on page that is not in this keel). */
function missingText() {
  return page.evaluate(() => {
    const t = document.querySelector("main")?.textContent ?? "";
    return /is not in this keel|could not be loaded/.test(t) ? t.replace(/\s+/g, " ").slice(0, 200) : "";
  });
}

/** One page, with a full load: its errors, failed requests, plugin requests and dead links, then each tab's. */
async function visit(hash) {
  seen.console = [];
  seen.failed = [];
  seen.plugin = [];
  seen.inflight.clear();
  await page.goto("about:blank");
  await page.goto(`${base}/${hash}`);
  await settle();
  const dead = new Set(await deadLinks());
  const missing = await missingText();
  const tabs = [];
  const lists = await page.locator("main [role=tablist]").count();
  let clicked = 0;
  for (let i = 0; i < lists && clicked < MAX_TABS; i++) {
    const list = page.locator("main [role=tablist]").nth(i);
    const n = await list.locator("[role=tab]").count().catch(() => 0);
    for (let j = 0; j < n && clicked < MAX_TABS; j++) {
      const tab = list.locator("[role=tab]").nth(j);
      const name = ((await tab.textContent().catch(() => "")) ?? "").replace(/\s+/g, " ").trim();
      try {
        await tab.click({ timeout: 4000 });
        clicked++;
        await settle(8000);
        tabs.push(name);
        for (const d of await deadLinks()) dead.add(`${d} (tab ${name})`);
      } catch {
        /* a tab that went away when another was clicked */
      }
    }
  }
  return { console: [...new Set(seen.console)], failed: [...new Set(seen.failed)], plugin: [...new Set(seen.plugin)],
    dead: [...dead], missing, tabs };
}

try {
  const projects = await (await ctx.request.get(`${base}/api/projects`)).json().catch(() => []);
  const pid = projects[0]?.id ?? "";
  await page.goto(`${base}/#/projects`);
  await page.evaluate((p) => localStorage.setItem("keel2.project", p), pid);
  await check("a project to show", !!pid, JSON.stringify(projects.map((p) => p.id)));

  // ---- the menu
  await page.goto("about:blank");
  await page.goto(`${base}/#/flow`);
  await settle();
  await wait(1500); // the plugins' web parts would join the menu now: there are none
  const menu = await page.evaluate(() => {
    const text = (el) => (el?.textContent || "").replace(/\s+/g, " ").trim();
    const nav = document.querySelector("nav#nav");
    return {
      groups: !nav ? [] : [...nav.querySelectorAll(".nav-sec")].map((sec) => ({
        group: text(sec.querySelector(".nav-h span")),
        pages: [...sec.querySelectorAll("a")].map((a) => ({ label: text(a.querySelector(".nav-l")), href: a.getAttribute("href") })),
      })),
      box: text(nav?.querySelector("[role=group][aria-label='No pages yet']")),
      brand: text(document.querySelector(".side .brand")),
    };
  });
  const sets = await (await ctx.request.get(`${base}/api/plugins/sets`)).json().then((s) => (s.sets ?? s).map((x) => x.title ?? x.id), () => []);
  const groups = menu.groups.map((g) => g.group);
  await check("the menu has Run, Build, Control and Help, and its Project group is the 'No pages yet' box (+ Add pages)",
    /No pages yet/.test(menu.box) && /\+ Add pages/.test(menu.box) && groups.join(",") === "Run,Project,Build,Control,Help" && menu.groups[1].pages.length === 0,
    `groups ${groups.join(", ")}; box: ${menu.box}`);
  await check("the tag next to the logo says core", /core/.test(menu.brand), menu.brand);
  const pluginPages = menu.groups.flatMap((g) => g.pages).filter((p) => /^#\/(tasks|repo|code|helper|keelbot|map|graph|wiki)\b/.test(p.href));
  await check("no plugin page in the menu", pluginPages.length === 0, JSON.stringify(pluginPages));
  await check("the sets are offered (Developer, Review, Knowledge, Tickets)", sets.length >= 4, sets.join(", "));

  // ---- every page
  const hashes = [...new Set(["#/projects", ...menu.groups.flatMap((g) => g.pages.map((p) => p.href)), ...extra])];
  for (const hash of hashes) {
    const r = await visit(hash);
    pages[hash] = r;
    const bad = [
      ...r.console.map((x) => `console: ${x}`),
      ...r.failed.map((x) => `failed: ${x}`),
      ...r.plugin.map((x) => `plugin route: ${x}`),
      ...r.dead.map((x) => `link to a missing page: ${x}`),
      ...(r.missing ? [`shows: ${r.missing}`] : []),
    ];
    await check(`${hash}${r.tabs.length ? ` (+${r.tabs.length} tabs)` : ""}: no console error, failed request, plugin route or dead link`,
      bad.length === 0, bad.join(" | "));
  }

  // ---- the launcher (⌘K): keel's own scopes and actions; a search asks no plugin (the code graph); "?" asks nobody
  seen.console = [];
  seen.failed = [];
  seen.plugin = [];
  await page.goto("about:blank");
  await page.goto(`${base}/#/flow`);
  await settle();
  const dialog = page.locator("[role=dialog][aria-label=Launcher]");
  const open = async () => {
    await page.locator("button[aria-label^='Search and actions']").first().click({ timeout: 5000 });
    await dialog.waitFor({ timeout: 5000 });
    await wait(800);
  };
  const close = async () => {
    for (let i = 0; i < 4 && (await dialog.count()); i++) await page.keyboard.press("Escape");
  };
  await open();
  const scopes = (await dialog.locator("[role=tablist][aria-label=Scope] [role=tab]").allTextContents()).map((s) => s.trim());
  await check("launcher: only keel's own scopes (All, Flows, Actions)", scopes.join(",") === "All,Flows,Actions", scopes.join(", "));
  const box = dialog.locator("[role=combobox]").first();
  const placeholder = await box.getAttribute("placeholder");
  await check("launcher: the field searches what keel has", !/files|pull requests|tasks/.test(placeholder ?? ""), placeholder);
  const options = async () => (await dialog.locator("[role=option]").allTextContents()).map((s) => s.replace(/\s+/g, " ").trim());
  const first = await options();
  // a word: keel's own results only (the code graph's search is the Graph plugin's)
  await box.fill("price");
  await wait(1200);
  const found = await options();
  await close();
  await open();
  await box.fill(">");
  await wait(600);
  const actions = await options();
  const parts = [...first, ...found, ...actions].filter((o) => /KeelBot|Add a task|Go to file|Focus mode in Code/.test(o));
  await check("launcher: no plugin action (Ask KeelBot, Add a task, Go to file)", parts.length === 0, parts.join(" | ") || actions.join(" | "));
  await close();
  await open();
  await box.fill("?why is it slow");
  await wait(600);
  const label = await dialog.locator("[role=combobox]").first().getAttribute("aria-label");
  await check("launcher: '?' asks nobody (KeelBot is a plugin)", label === "Search keel", `field: ${label}`);
  await box.press("Enter").catch(() => {});
  await wait(1000);
  const foot = (await dialog.locator(".lx-foot").textContent().catch(() => "")) ?? "";
  await check("launcher: its footer offers no KeelBot", !/KeelBot/.test(foot), foot.replace(/\s+/g, " "));
  const deadHere = await deadLinks("[role=dialog][aria-label=Launcher] a[href^='#']");
  await close();
  await wait(500);
  const launcherBad = [...seen.console, ...seen.failed, ...seen.plugin.map((x) => `plugin route: ${x}`), ...deadHere.map((d) => `link: ${d}`)];
  await check("launcher: no console error, failed request, plugin route or dead link", !launcherBad.length, launcherBad.join(" | "));

  // ---- the key sheet (⌘/), on every page
  await page.goto("about:blank");
  await page.goto(`${base}/#/flow`);
  await settle();
  await page.locator("main").click({ position: { x: 5, y: 5 } }).catch(() => {});
  await page.keyboard.press(process.platform === "darwin" ? "Meta+Slash" : "Control+Slash");
  const sheet = page.locator("#key-sheet");
  let sheetText = "";
  try {
    await sheet.waitFor({ timeout: 4000 });
    await sheet.locator("label.ks-every input").check().catch(() => {});
    await wait(300);
    sheetText = ((await sheet.textContent()) ?? "").replace(/\s+/g, " ");
  } catch (e) {
    sheetText = `not opened: ${String(e.message).split("\n")[0]}`;
  }
  await check("key sheet: no KeelBot keys and no diagram keys (Map, Graph)",
    sheetText.includes("Everywhere") && !/KeelBot|Map and Graph/.test(sheetText), sheetText.slice(0, 300));
} catch (e) {
  checks.push({ what: "the walk ran to its end", ok: false, detail: String(e?.stack || e).slice(0, 1500), shot: "" });
} finally {
  await browser.close();
}
console.log(JSON.stringify({ checks, pages }));
