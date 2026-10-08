// The web half of the parity e2e (e2e/parity/parity.py): is keel B's web app the same for people as keel A's?
//
//   npx -y -p playwright@1 node e2e/parity/pages.mjs <urlA> <urlB> <outdir> [--sets]
//
// For both keels (headless Chromium, the keel's first project chosen) it reads the menu (groups, labels, order,
// links) and the frame around the pages, searches in the launcher (⌘K) scope by scope, then opens every menu page,
// #/projects, #/code, #/keelbot and a few deep links, each with a full load. It records what a person sees in the main
// area: headings, tabs, views, regions, buttons, fields, alerts, badges, listed rows and looks (computed styles),
// plus the console errors and the failed requests. It clicks every tab and every Code view (they only show things)
// and records what each one shows. Then it compares A with B; on a difference it looks again once, then saves
// screenshots of both into <outdir>. It writes <outdir>/web.json (parity.py reads it) and seen.json (what each page
// showed), and prints one line per page. Exit 1 when something differs.

import fs from "fs";
import { createRequire } from "module";
import os from "os";
import path from "path";

const [urlA, urlB, outDir] = process.argv.slice(2).filter((x) => !x.startsWith("--"));
// --sets: the data may differ in how many times a thing is there (a flow's retries: one more agent call, one more
// checkpoint), so buttons, badges and rows are compared as sets: the same things, in any order and number
const SETS = process.argv.includes("--sets");
// with --sets (a flow ran): the pages that show each agent call step by step (what a step wrote, its diff) differ
// when a step ran twice on one keel; there only the page's structure counts, not its content
const STEP_BY_STEP = SETS ? new Set(["#/live", "#/jobs"]) : new Set();
if (!urlA || !urlB || !outDir) {
  console.error("usage: npx -y -p playwright@1 node e2e/parity/pages.mjs <urlA> <urlB> <outdir> [--sets]");
  process.exit(2);
}
fs.mkdirSync(outDir, { recursive: true });

// pages that are not in the menu but people open: the project list, the names the Code and KeelBot pages show, and
// deep links (a file at a line in Code, a page of the Wiki)
const EXTRA = ["#/projects", "#/code", "#/keelbot", "#/repo/web/cart.ts:9", "#/wiki/kb:architecture", "#/wiki/runbook"];
// a tablist whose tabs are the files someone opened, not a part of the page
const NOT_TABS = new Set(["Open files"]);
// at most this many tabs and views are clicked on one page
const MAX_CLICKS = 16;

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
  throw new Error("playwright not found: run me with npx -y -p playwright@1 node e2e/parity/pages.mjs …");
}

/** Chromium: playwright's own, else the newest headless shell in the browser cache (a newer playwright@1 may want a
 *  build that is not downloaded yet). */
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

/** Text as compared: one line, commit shas as <sha>, numbers as #, at most 80 characters. */
const norm = (s) => String(s ?? "").replace(/\s+/g, " ").replace(/[0-9a-f]*\d[0-9a-f]*/g, (m) => (m.length >= 7 ? "<sha>" : m))
  .replace(/\d+/g, "#").trim().slice(0, 80);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- one keel

async function session(browser, base, side) {
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 }, colorScheme: "light", reducedMotion: "reduce" });
  const page = await ctx.newPage();
  const projects = await (await ctx.request.get(`${base}/api/projects`)).json().catch(() => []);
  const pid = projects[0]?.id ?? "";
  const s = { side, base, ctx, page, pid, console: [], failed: [], inflight: new Map() };
  const rel = (url) => {
    const u = new URL(url);
    return norm(u.pathname.replaceAll(`/${pid}/`, "/{pid}/") + (u.search ? "?" + [...u.searchParams.keys()].join("&") : ""));
  };
  page.on("console", (m) => {
    if (m.type() === "error") s.console.push(norm(m.text().replaceAll(base, "")));
  });
  page.on("pageerror", (e) => s.console.push("page error: " + norm(e.message)));
  page.on("request", (r) => s.inflight.set(r, Date.now()));
  page.on("requestfinished", (r) => s.inflight.delete(r));
  page.on("requestfailed", (r) => {
    s.inflight.delete(r);
    const why = r.failure()?.errorText ?? "";
    // a request the page dropped when it moved on (the event stream, a superseded fetch) is not a failure
    if (!/ERR_ABORTED|NS_BINDING_ABORTED/.test(why)) s.failed.push(`${r.method()} ${rel(r.url())} ${why}`);
  });
  page.on("response", (res) => {
    if (res.status() >= 400) s.failed.push(`${res.request().method()} ${rel(res.url())} → ${res.status()}`);
  });
  await page.goto(`${base}/#/projects`);
  await page.evaluate((p) => localStorage.setItem("keel2.project", p), pid);
  return s;
}

/** Until the main area has stopped changing: no spinner, the same content for 500 ms and no request in flight
 *  (but the event stream). Pages that poll settle too: their content stays the same. */
async function settle(s, max = 10000) {
  const t0 = Date.now();
  await s.page.waitForSelector("main", { timeout: max }).catch(() => {});
  let last = "";
  let still = Date.now();
  while (Date.now() - t0 < max) {
    const sig = await s.page.evaluate(() => {
      const m = document.querySelector("main");
      const loading = !!document.querySelector("main .pg-spin, main .pg-skel, .empty.loading");
      return `${loading ? "loading " : ""}${m ? `${m.innerHTML.length}:${m.getElementsByTagName("*").length}` : "-"}`;
    }).catch(() => "loading");
    // a request of a page that is gone never finishes: only the last 5 s count
    const busy = [...s.inflight].some(([r, at]) => Date.now() - at < 5000 && r.resourceType() !== "eventsource" && !r.url().includes("/api/events"));
    if (sig !== last || sig.startsWith("loading")) {
      last = sig;
      still = Date.now();
    } else if (Date.now() - still > 500 && !busy) return;
    await wait(100);
  }
}

/** What a person sees in the main area (runs in the page). */
function seen() {
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && getComputedStyle(el).visibility !== "hidden";
  const name = (el) => (el.getAttribute("aria-label") || el.textContent || el.getAttribute("title") || "").replace(/\s+/g, " ").trim();
  const main = document.querySelector("main#main") || document.querySelector("main");
  if (!main) return { main: false };
  const all = (sel) => [...main.querySelectorAll(sel)].filter(visible);
  const labelOf = (el) => el.getAttribute("aria-label") || (el.getAttribute("aria-labelledby") && document.getElementById(el.getAttribute("aria-labelledby"))?.textContent) || "";
  return {
    main: true,
    empty: !main.textContent.trim(),
    headings: all("h1,h2,h3,h4").map((h) => `${h.tagName.toLowerCase()} ${name(h)}`),
    tabs: all("[role=tablist]").map((t) => ({ label: t.getAttribute("aria-label") || "", tabs: [...t.querySelectorAll("[role=tab]")].filter(visible).map(name) })),
    views: all("nav[aria-label]").map((n) => ({ label: n.getAttribute("aria-label"), items: [...n.querySelectorAll("a,button")].filter(visible).map(name) })),
    regions: all("section[aria-labelledby],section[aria-label],aside[aria-label],[role=region],[role=toolbar],[role=dialog],form[aria-label]")
      .map((r) => `${r.getAttribute("role") || r.tagName.toLowerCase()} ${labelOf(r)}`.trim()),
    buttons: all("button,a[href],[role=button]").filter((b) => !b.closest("[role=tablist],nav[aria-label]")).map(name).filter(Boolean),
    fields: all("input,select,textarea").map((f) => `${f.tagName.toLowerCase()}${f.type ? ":" + f.type : ""} ${f.getAttribute("aria-label") || f.getAttribute("placeholder") || f.name || ""}`.trim()),
    alerts: all("[role=alert],.errbox").map(name),
    // small states: pills, tags, badges, status lines ("claude CLI not installed", "Index: ready")
    badges: all(".pill,.tag,.badge,.count,[role=status]").map(name).filter(Boolean),
    // the data a page lists (files in a tree, rows of a table, list items): the first 60, each by its first line
    rows: all("[role=treeitem],[role=row],tr,li,[role=option]").slice(0, 60)
      .map((r) => (r.getAttribute("aria-label") || r.innerText || "").split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 2).join(" · ")),
    // how it looks: the computed style of the first 400 elements (a missing stylesheet changes most of them); no
    // sizes that follow the data, such as widths
    style: [...main.getElementsByTagName("*")].filter(visible).slice(0, 400).map((el) => {
      const cs = getComputedStyle(el);
      return [el.tagName.toLowerCase(), ...["display", "position", "font-family", "font-size", "font-weight", "color", "background-color",
        "border-top-width", "border-top-style", "border-radius", "padding-top", "padding-left", "margin-top", "gap", "text-align", "opacity"]
        .map((p) => (p === "font-family" ? cs.getPropertyValue(p).split(",")[0].replaceAll('"', "") : cs.getPropertyValue(p)))].join(" ");
    }),
  };
}

/** The menu (the groups with their pages, in order) and the frame around the pages (the buttons and links outside
 *  the main area and the menu: search, notifications, hide the menu, …). */
async function menu(s) {
  await s.page.goto("about:blank");
  await s.page.goto(`${s.base}/#/flow`);
  await settle(s);
  // the parts' pages join the menu when their web part has loaded: read it until it stays the same for 1.5 s
  let last = null;
  for (let i = 0; i < 8; i++) {
    const now = await readMenu(s);
    if (last && JSON.stringify(now) === JSON.stringify(last)) return now;
    last = now;
    await wait(1500);
  }
  return last;
}

function readMenu(s) {
  return s.page.evaluate(() => {
    const text = (el) => (el?.textContent || "").replace(/\s+/g, " ").trim();
    const nav = document.querySelector("nav#nav") || document.querySelector("nav[aria-label=Screens]");
    const groups = !nav ? [] : [...nav.querySelectorAll(".nav-sec")].map((sec) => ({
      group: text(sec.querySelector(".nav-h span")),
      pages: [...sec.querySelectorAll("a")].map((a) => ({ label: text(a.querySelector(".nav-l")), href: a.getAttribute("href") })),
    }));
    const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const frame = [...document.querySelectorAll("button,a[href],[role=button]")]
      .filter((b) => visible(b) && !b.closest("main, nav#nav, nav[aria-label=Screens]"))
      .map((b) => b.getAttribute("aria-label") || b.getAttribute("title") || text(b)).filter(Boolean);
    return { groups, frame };
  });
}

/** The launcher (⌘K): its scopes, and what it lists for a few searches, scope by scope. It only searches. */
async function launcher(s) {
  const { page } = s;
  await page.goto("about:blank");
  await page.goto(`${s.base}/#/flow`);
  await settle(s);
  const out = {};
  const dialog = page.locator("[role=dialog][aria-label=Launcher]");
  const read = () => dialog.evaluate((d) => {
    const text = (el) => (el?.getAttribute("aria-label") || el?.textContent || "").replace(/\s+/g, " ").trim();
    return [...d.querySelectorAll("[role=listbox][aria-label=Results] [role=group]")]
      .flatMap((g) => [`group ${text(g)}`, ...[...g.querySelectorAll("[role=option]")].map((o) => `  ${text(o)}`)]);
  });
  const quiet = async () => {
    let last = "";
    let still = Date.now();
    const t0 = Date.now();
    while (Date.now() - t0 < 6000) {
      const sig = await dialog.evaluate((d) => (d.querySelector(".pg-spin") ? "loading" : d.innerHTML.length)).catch(() => "gone");
      if (String(sig) !== last || sig === "loading") {
        last = String(sig);
        still = Date.now();
      } else if (Date.now() - still > 600) return;
      await wait(100);
    }
  };
  try {
    await page.locator("button[aria-label^='Search and actions']").first().click({ timeout: 5000 });
    await dialog.waitFor({ timeout: 5000 });
    out.scopes = await dialog.locator("[role=tablist][aria-label=Scope] [role=tab]").allTextContents();
    const box = dialog.locator("[role=combobox]").first();
    for (const q of ["", "cart", "price"]) {
      await box.fill(q);
      await quiet();
      out[`"${q}"`] = await read();
    }
    for (const [i, scope] of out.scopes.entries()) {
      await dialog.locator("[role=tablist][aria-label=Scope] [role=tab]").nth(i).click();
      await quiet();
      out[`"price" in ${scope.trim()}`] = await read();
    }
  } catch (e) {
    out.error = [norm(e.message.split("\n")[0])];
  }
  await page.keyboard.press("Escape").catch(() => {});
  return out;
}

/** One page, fresh: what it shows, then what each tab and Code view shows; the console errors and failed requests. */
async function visit(s, hash) {
  s.console = [];
  s.failed = [];
  s.inflight.clear();
  const { page } = s;
  await page.goto("about:blank");
  await page.goto(`${s.base}/${hash}`);
  await settle(s);
  const top = await page.evaluate(seen);
  const shot = await page.screenshot().catch(() => null);
  const inner = {};
  const shots = { "": shot };
  if (top.main) {
    const clicks = [];
    top.tabs.forEach((t, i) => {
      if (!NOT_TABS.has(t.label)) t.tabs.forEach((tab, j) => clicks.push({ key: `tab ${t.label || i} › ${tab}`, list: t.label, i, j }));
    });
    // the Code page's views (Explorer, Search, Source control, keel and the parts' views); not the assistant
    const views = await page.locator("main nav[aria-label] button[aria-pressed]:not(.act-help)").evaluateAll((bs) =>
      bs.map((b) => b.getAttribute("aria-label") || b.textContent.trim())).catch(() => []);
    views.forEach((v, j) => clicks.push({ key: `view ${v}`, view: j }));
    for (const c of clicks.slice(0, MAX_CLICKS)) {
      try {
        const target = c.view !== undefined
          ? page.locator("main nav[aria-label] button[aria-pressed]:not(.act-help)").nth(c.view)
          : (c.list ? page.locator(`main [role=tablist][aria-label="${c.list.replaceAll('"', '\\"')}"]`).first() : page.locator("main [role=tablist]").nth(c.i))
            .locator("[role=tab]").nth(c.j);
        await target.click({ timeout: 4000 });
        await settle(s, 6000);
        inner[c.key] = await page.evaluate(seen);
        shots[c.key] = await page.screenshot().catch(() => null);
      } catch (e) {
        inner[c.key] = { error: norm(e.message.split("\n")[0]) };
      }
    }
  }
  return { top, inner, shots, console: [...new Set(s.console)], failed: [...new Set(s.failed)] };
}

// ---------------------------------------------------------------- comparing

/** Lines only in A / only in B (as a multiset), or "the order differs". Styles are compared as they are. */
function listDiff(what, a = [], b = [], exact = false, sets = false) {
  const unique = (x) => (sets ? [...new Set(x)].sort() : x);
  const na = unique(exact ? [...a] : a.map(norm)), nb = unique(exact ? [...b] : b.map(norm));
  if (JSON.stringify(na) === JSON.stringify(nb)) return [];
  const left = [...na], right = [...nb];
  for (const x of na) {
    const i = right.indexOf(x);
    if (i >= 0) {
      right.splice(i, 1);
      left.splice(left.indexOf(x), 1);
    }
  }
  if (!left.length && !right.length) return [`${what}: the same items in another order`];
  return [...left.map((x) => `${what}: "${x}" only in A`), ...right.map((x) => `${what}: "${x}" only in B`)];
}

/** What differs between two views of the same place, by aspect. structure: only the page's structure (headings, tabs,
 *  views, regions, fields, alerts), not what it lists (buttons, badges, rows, looks). */
function compareSeen(a, b, structure = false) {
  const out = {};
  const add = (aspect, lines) => {
    if (lines.length) out[aspect] = [...(out[aspect] || []), ...lines];
  };
  if (a.error || b.error) {
    if (norm(a.error) !== norm(b.error)) add("render", [`could not open it: ${a.error ? "A: " + a.error : ""} ${b.error ? "B: " + b.error : ""}`.trim()]);
    return out;
  }
  if (a.main !== b.main) add("render", [`main area: ${a.main ? "shown" : "missing"} in A, ${b.main ? "shown" : "missing"} in B`]);
  if (!a.main || !b.main) return out;
  if (a.empty !== b.empty) add("render", [`main area: ${a.empty ? "empty" : "filled"} in A, ${b.empty ? "empty" : "filled"} in B`]);
  add("headings", listDiff("heading", a.headings, b.headings));
  const lists = (x) => x.map((t) => `${t.label}: ${t.tabs.map(norm).join(" | ")}`);
  add("tabs", listDiff("tabs", lists(a.tabs), lists(b.tabs)));
  const navs = (x) => x.map((n) => `${n.label}: ${n.items.map(norm).join(" | ")}`);
  add("views", listDiff("views", navs(a.views), navs(b.views)));
  add("regions", listDiff("region", a.regions, b.regions));
  add("fields", listDiff("field", a.fields, b.fields));
  add("alerts", listDiff("alert", a.alerts, b.alerts));
  if (structure) return out;
  add("buttons", listDiff("button", a.buttons, b.buttons, false, SETS));
  add("badges", listDiff("badge", a.badges, b.badges, false, SETS));
  add("rows", listDiff("row", a.rows, b.rows, false, SETS));
  // the looks used on the page, as a set: one more row of a kind already there is not a new look
  const sa = new Set(a.style), sb = new Set(b.style);
  const onlyA = [...sa].filter((x) => !sb.has(x)), onlyB = [...sb].filter((x) => !sa.has(x));
  if (onlyA.length || onlyB.length) {
    add("style", [`${onlyB.length} look(s) only in B, ${onlyA.length} only in A (of ${sa.size} in A)`,
      ...onlyB.slice(0, 4).map((x) => `style only in B: ${x}`), ...onlyA.slice(0, 4).map((x) => `style only in A: ${x}`)]);
  }
  return out;
}

function comparePage(a, b, hash) {
  const out = {};
  const merge = (prefix, diffs) => {
    for (const [aspect, lines] of Object.entries(diffs)) out[aspect] = [...(out[aspect] || []), ...lines.map((l) => prefix + l)];
  };
  const structure = STEP_BY_STEP.has(hash);
  merge("", compareSeen(a.top, b.top, structure));
  const keys = [...new Set([...Object.keys(a.inner), ...Object.keys(b.inner)])];
  for (const k of keys) {
    if (!(k in b.inner)) out.clicks = [...(out.clicks || []), `${k}: only in A`];
    else if (!(k in a.inner)) out.clicks = [...(out.clicks || []), `${k}: only in B`];
    else merge(`${k}: `, compareSeen(a.inner[k], b.inner[k], structure));
  }
  // B must not add console errors or failed requests (the ones A has too are 0.15.1's own)
  const newer = (x, y) => x.filter((e) => !y.includes(e));
  if (newer(b.console, a.console).length) out.console = newer(b.console, a.console).map((e) => `console error only in B: ${e}`);
  if (newer(b.failed, a.failed).length) out.requests = newer(b.failed, a.failed).map((e) => `failed request only in B: ${e}`);
  return out;
}

/** Where the first difference shows: the top of the page, or the tab or view it is in. */
function shotKey(diffs, a, b) {
  for (const lines of Object.values(diffs)) {
    for (const l of lines) {
      const k = Object.keys(a.shots).find((key) => key && l.startsWith(key + ": "));
      if (k && b.shots[k]) return k;
    }
  }
  return "";
}

const slug = (s) => s.replace(/^#\//, "").replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "page";

// ---------------------------------------------------------------- main

const { chromium } = loadPlaywright();
const browser = await launch(chromium);
const items = [];
// what each page showed on A and B (seen.json): to read when a difference is not clear
const seenBy = {};
const notes = [];
const started = Date.now();
let differs = 0;
let complete = false;
let failure = null;
try {
  const A = await session(browser, urlA, "a");
  const B = await session(browser, urlB, "b");
  if (A.pid !== B.pid) notes.push(`the first project is ${A.pid} in A and ${B.pid} in B`);

  // the menu and the frame around the pages
  const [fa, fb] = await Promise.all([menu(A), menu(B)]);
  const [ma, mb] = [fa.groups, fb.groups];
  const flat = (m) => m.flatMap((g) => [`group ${g.group}`, ...g.pages.map((p) => `  ${p.label} → ${p.href}`)]);
  const menuDiff = listDiff("menu", flat(ma), flat(mb));
  items.push({ what: "menu", page: "menu", verdict: menuDiff.length ? "different" : "same", details: menuDiff });
  console.log(`${menuDiff.length ? "DIFF" : "same"}  menu (${flat(ma).length} lines in A, ${flat(mb).length} in B)`);
  if (!ma.length) notes.push("A has no menu (nav#nav): is it a keel?");
  const frameDiff = listDiff("frame", fa.frame, fb.frame);
  items.push({ what: "frame", page: "frame", verdict: frameDiff.length ? "different" : "same", details: frameDiff });
  console.log(`${frameDiff.length ? "DIFF" : "same"}  frame (${fa.frame.length} buttons and links around the pages)`);

  // the launcher (⌘K): the parts add their results to it
  const [la, lb] = await Promise.all([launcher(A), launcher(B)]);
  Object.assign(seenBy, { menu: { a: fa, b: fb }, launcher: { a: la, b: lb } });
  const launcherDiff = [...new Set([...Object.keys(la), ...Object.keys(lb)])]
    .flatMap((k) => listDiff(`launcher ${k}`, la[k] ?? [], lb[k] ?? []));
  items.push({ what: "launcher", page: "launcher", verdict: launcherDiff.length ? "different" : "same", details: launcherDiff });
  console.log(`${launcherDiff.length ? "DIFF" : "same"}  launcher (${Object.keys(la).length - 1} searches)`);

  // every page in either menu, A's order first
  const hashes = [...new Set([...ma, ...mb].flatMap((g) => g.pages.map((p) => p.href)).concat(EXTRA))];
  for (const hash of hashes) {
    let a, b, diffs;
    for (let attempt = 0; attempt < 2; attempt++) {
      [a, b] = await Promise.all([visit(A, hash), visit(B, hash)]);
      diffs = comparePage(a, b, hash);
      if (!Object.keys(diffs).length) break; // a second look rules out what was only still loading
    }
    // without the styles (they are many): what a person reads
    const slim = (x) => ({ ...x.top, style: undefined, inner: Object.fromEntries(Object.entries(x.inner).map(([k, v]) => [k, { ...v, style: undefined }])),
      console: x.console, failed: x.failed });
    seenBy[hash] = { a: slim(a), b: slim(b) };
    const aspects = Object.keys(diffs);
    if (!aspects.length) {
      items.push({ what: hash, page: hash, verdict: "same", details: [] });
    } else {
      differs++;
      const key = shotKey(diffs, a, b);
      const shots = [];
      for (const [side, x] of [["a", a], ["b", b]]) {
        const buf = x.shots[key] ?? x.shots[""];
        if (!buf) continue;
        const file = path.join(outDir, `${slug(hash)}${key ? "-" + slug(key) : ""}-${side}.png`);
        fs.writeFileSync(file, buf);
        shots.push(file);
      }
      for (const aspect of aspects) items.push({ what: `${hash} ${aspect}`, page: hash, verdict: "different", details: diffs[aspect], shots });
    }
    const shared = a.failed.filter((e) => b.failed.includes(e));
    if (shared.length) notes.push(`${hash}: A has these failed requests too (not a parity difference): ${shared.slice(0, 4).join(" | ")}`);
    const quiet = (e) => /^Failed to load resource/.test(e); // the browser's line for a failed request listed above
    const both = a.console.filter((e) => b.console.includes(e) && !(shared.length && quiet(e)));
    if (both.length) notes.push(`${hash}: A has these console errors too (not a parity difference): ${both.slice(0, 3).join(" | ")}`);
    if (!b.top.main || b.top.empty) notes.push(`${hash}: B shows no main area`);
    const clicked = Object.keys(a.inner).length;
    console.log(`${aspects.length ? "DIFF" : "same"}  ${hash}${clicked ? ` (+${clicked} tabs/views)` : ""}${aspects.length ? ": " + aspects.join(", ") : ""}` +
      ` ${((Date.now() - started) / 1000).toFixed(0)}s`);
  }
  await A.ctx.close();
  await B.ctx.close();
  complete = true;
} catch (e) {
  failure = String(e?.stack || e);
  throw e;
} finally {
  await browser.close();
  // complete: false when the run broke half way (parity.py then does not take the pages it has for all of them)
  fs.writeFileSync(path.join(outDir, "web.json"), JSON.stringify({ a: urlA, b: urlB, complete, failure, items, notes }, null, 2));
  fs.writeFileSync(path.join(outDir, "seen.json"), JSON.stringify(seenBy, null, 1));
}
const whole = items.filter((i) => !i.page.startsWith("#") && i.verdict === "different").map((i) => i.what);
const pages = new Set(items.filter((i) => i.page.startsWith("#")).map((i) => i.page));
console.log(`\nweb: ${pages.size} pages, ${differs} differ${whole.length ? `; ${whole.join(", ")} differ` : ""}`);
process.exit(differs || whole.length ? 1 : 0);
