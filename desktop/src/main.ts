// keel desktop: keel without Docker, in one download (desktop/README.md). The main process: one window, keel's own
// pages for the first start and for errors (ui/, served as keel-app://app/), and keel itself (supervisor.ts) behind
// keel's web page, signed in by the app.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { app, BrowserWindow, clipboard, dialog, Menu, nativeTheme, net, protocol, session, shell, type MenuItemConstructorOptions } from "electron";
import { checkGit } from "./checks";
import { log, openLog } from "./log";
import { bundleIn, pathsFor } from "./paths";
import { addFolder, readSettings, writeSettings, type Settings } from "./settings";
import { loginShellPath, mergePath } from "./shellenv";
import { Keel } from "./supervisor";

const SMOKE = process.env.KEEL_DESKTOP_SMOKE === "1";
const REPO = "https://github.com/MiladNalbandi/keel-v2";
const UI = "keel-app://app";

type View =
  | { page: "boot"; text: string }
  | { page: "welcome"; data: string }
  | { page: "git"; hint: string; canInstall: boolean }
  | { page: "starting"; text: string; since: number }
  | { page: "failed"; title?: string; why: string; log: string; logs: string }
  | { page: "stopping"; text: string };

protocol.registerSchemesAsPrivileged([{ scheme: "keel-app", privileges: { standard: true, secure: true, supportFetchAPI: true } }]);

if (!app.requestSingleInstanceLock()) {
  // one keel per person: two would open one keel.db
  app.quit();
} else {
  void main();
}

async function main(): Promise<void> {
  const paths = pathsFor(process.platform, os.homedir(), process.env);
  openLog(paths.logs);
  const resources = app.isPackaged ? process.resourcesPath : process.env.KEEL_DESKTOP_RESOURCES || path.join(__dirname, "..", "..", "build", "stage");
  const bundle = bundleIn(resources);
  const keelVersion = (() => {
    try {
      return fs.readFileSync(path.join(bundle.keel, "VERSION"), "utf8").trim();
    } catch {
      return app.getVersion();
    }
  })();
  const keel = new Keel(paths, bundle, keelVersion);
  log(`keel desktop ${app.getVersion()} (keel ${keelVersion}) on ${process.platform}-${process.arch}; data ${paths.data}`);

  let settings: Settings = readSettings(paths.settings);
  if (SMOKE) settings = { accepted: true, folders: process.env.KEEL_DESKTOP_SMOKE_FOLDER ? [process.env.KEEL_DESKTOP_SMOKE_FOLDER] : [] };
  let view: View = { page: "boot", text: "Opening keel…" };
  let win: BrowserWindow | null = null;
  let pathValue = process.env.PATH || "/usr/bin:/bin";
  let quitting = false;
  let busy = false; // a start, stop or restart is on its way

  const save = () => {
    if (!SMOKE) writeSettings(paths.settings, settings);
  };
  const parent = () => (win && !win.isDestroyed() ? win : null);
  const box = (o: Electron.MessageBoxOptions) => {
    const w = parent();
    return w ? dialog.showMessageBox(w, o) : dialog.showMessageBox(o);
  };
  const pick = (o: Electron.OpenDialogOptions) => {
    const w = parent();
    return w ? dialog.showOpenDialog(w, o) : dialog.showOpenDialog(o);
  };
  const keelOrigin = () => (keel.ports ? [`http://localhost:${keel.ports.api}`, `http://127.0.0.1:${keel.ports.api}`] : []);
  const isKeel = (u: string) => keelOrigin().some((o) => u === o || u.startsWith(o + "/"));
  const isUi = (u: string) => u.startsWith(UI + "/");

  function show(v: View): void {
    view = v;
    if (SMOKE && (v.page === "failed" || v.page === "git")) {
      process.stdout.write(`SMOKE FAILED: ${JSON.stringify(v)}\n`);
      app.exit(1);
      return;
    }
    if (win && !win.isDestroyed() && !isUi(win.webContents.getURL())) void win.loadURL(`${UI}/ui/index.html`);
  }

  async function openKeel(): Promise<void> {
    if (!win || win.isDestroyed()) return;
    const url = await keel.signInUrl();
    await win.loadURL(url);
  }

  // ---------------------------------------------------------------- keel

  async function startKeel(safe = false): Promise<void> {
    if (busy) return;
    busy = true;
    try {
      const git = checkGit(pathValue);
      if (!git.ok) {
        show({ page: "git", hint: git.hint, canInstall: git.canInstall });
        return;
      }
      show({ page: "starting", text: safe ? "Starting keel in safe mode (only the plugins that came with it)…" : "Starting keel…", since: Date.now() });
      if (!(await keel.adopt())) {
        await keel.start({ folders: settings.folders.filter((f) => fs.existsSync(f)), port: settings.port, safe, pathValue, env: process.env });
        log(`keel started: pid ${keel.pid}, port ${keel.ports?.api}`);
      } else log(`keel found running: pid ${keel.pid}, port ${keel.ports?.api}`);
      if (keel.ports && settings.port !== keel.ports.api) {
        settings.port = keel.ports.api;
        save();
      }
      if (!(await keel.waitUp())) {
        const why = keel.running() ? "keel did not answer in 3 minutes." : "keel stopped while it started.";
        log(why);
        if (keel.running()) await keel.stop();
        show({ page: "failed", why, log: keel.logTail(), logs: paths.logs });
        return;
      }
      void keel.register(settings.folders.filter((f) => fs.existsSync(f))).then((added) => added.length && log(`projects added: ${added.join(", ")}`));
      await openKeel();
    } catch (e) {
      log("start failed:", e);
      show({ page: "failed", why: (e as Error).message, log: keel.logTail(), logs: paths.logs });
    } finally {
      busy = false;
    }
  }

  keel.on("crashed", (code) => {
    log(`keel stopped by itself (${code})`);
    if (quitting) return;
    show({ page: "failed", title: "keel stopped", why: `keel stopped by itself (exit ${code}). Your projects and their flows are safe: start it again.`, log: keel.logTail(), logs: paths.logs });
  });

  /** Agents at work? Then ask first: their step starts again later and its tokens are spent again (keel2 guard_busy). */
  async function okToStop(what: string): Promise<boolean> {
    if (!keel.running()) return true;
    const n = await keel.busyAgents();
    if (n === 0) return true;
    const text =
      n === null
        ? `keel did not say whether agents are working. To ${what}, keel stops them: their step starts again later and its tokens are spent again.`
        : `${n} agent${n === 1 ? " is" : "s are"} working right now. To ${what}, keel stops ${n === 1 ? "it" : "them"}: the step starts again later and its tokens are spent again.`;
    const r = await box({
      type: "warning",
      buttons: [`${what[0].toUpperCase()}${what.slice(1)} anyway`, "Cancel"],
      defaultId: 1,
      cancelId: 1,
      message: n === null ? "Are agents working?" : "Agents are working",
      detail: text,
    });
    return r.response === 0;
  }

  async function restart(safe = false): Promise<void> {
    if (busy || !(await okToStop("restart keel"))) return;
    show({ page: "stopping", text: "Restarting keel…" });
    await keel.stop();
    await startKeel(safe);
  }

  async function quitApp(): Promise<void> {
    if (quitting) return;
    if (!SMOKE && !(await okToStop("quit"))) return;
    quitting = true;
    show({ page: "stopping", text: "Stopping keel…" });
    await keel.stop();
    log("keel stopped; bye");
    app.exit(0);
  }

  async function addProject(): Promise<void> {
    const r = await pick({
      title: "Add a project folder",
      buttonLabel: "Add",
      message: "A git repository, or a folder with git repositories in it. keel's agents may work in it.",
      properties: ["openDirectory", "createDirectory"],
    });
    const f = r.filePaths[0];
    if (r.canceled || !f) return;
    const next = addFolder(settings.folders, f);
    if (next === settings.folders) {
      await box({ message: "keel has this folder already.", detail: f });
      return;
    }
    if (!(await okToStop("add the folder"))) return;
    settings.folders = next;
    save();
    show({ page: "stopping", text: "keel restarts with the new folder…" });
    await keel.stop();
    await startKeel();
  }

  // ---------------------------------------------------------------- the app's own pages

  async function act(name: string): Promise<unknown> {
    switch (name) {
      case "choose-folder": {
        const r = await pick({
          title: "Choose a project folder",
          buttonLabel: "Use this folder",
          message: "A git repository, or a folder with git repositories in it.",
          properties: ["openDirectory", "createDirectory"],
        });
        if (r.canceled || !r.filePaths[0]) return { ok: false };
        settings = { ...settings, accepted: true, folders: addFolder(settings.folders, r.filePaths[0]) };
        save();
        void startKeel();
        return { ok: true };
      }
      case "demo":
        settings = { ...settings, accepted: true };
        save();
        void startKeel();
        return { ok: true };
      case "retry":
        void startKeel();
        return { ok: true };
      case "safe":
        void startKeel(true);
        return { ok: true };
      case "install-clt":
        spawn("xcode-select", ["--install"], { stdio: "ignore", detached: true }).unref();
        return { ok: true };
      case "logs":
        void shell.openPath(paths.logs);
        return { ok: true };
      case "copy-report":
        clipboard.writeText(`keel desktop ${app.getVersion()} · ${process.platform}-${process.arch} · ${os.release()}\n\n${keel.logTail(80)}`);
        return { ok: true };
      case "quit":
        void quitApp();
        return { ok: true };
      default:
        return { ok: false, error: `no action ${name}` };
    }
  }

  function serveUi(): void {
    const uiDir = path.join(__dirname, "..", "..", "ui");
    protocol.handle("keel-app", async (req) => {
      const u = new URL(req.url);
      if (u.host !== "app") return new Response("not found", { status: 404 });
      if (u.pathname === "/api/state") {
        return Response.json({ ...view, version: app.getVersion(), platform: process.platform });
      }
      if (u.pathname.startsWith("/api/do/") && req.method === "POST") {
        return Response.json(await act(u.pathname.slice("/api/do/".length)));
      }
      if (u.pathname.startsWith("/ui/")) {
        const f = path.normalize(path.join(uiDir, u.pathname.slice("/ui/".length)));
        if (!f.startsWith(uiDir + path.sep)) return new Response("not found", { status: 404 });
        return net.fetch(pathToFileURL(f).toString());
      }
      return new Response("not found", { status: 404 });
    });
  }

  // ---------------------------------------------------------------- the window

  function createWindow(): BrowserWindow {
    const b = settings.bounds;
    const w = new BrowserWindow({
      width: b?.width ?? 1320,
      height: b?.height ?? 860,
      x: b?.x,
      y: b?.y,
      minWidth: 760,
      minHeight: 520,
      title: "keel",
      show: false,
      backgroundColor: nativeTheme.shouldUseDarkColors ? "#121117" : "#ffffff",
      icon: process.platform === "linux" ? path.join(__dirname, "..", "..", "ui", "icon.png") : undefined,
      webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, spellcheck: true },
    });
    w.once("ready-to-show", () => {
      if (!SMOKE) w.show();
    });
    const wc = w.webContents;
    // only keel's own pages in this window; every other link opens in the person's browser
    wc.on("will-navigate", (e, url) => {
      if (isKeel(url) || isUi(url)) return;
      e.preventDefault();
      if (/^(https?|mailto):/.test(url)) void shell.openExternal(url);
    });
    wc.setWindowOpenHandler(({ url }) => {
      if (isKeel(url)) {
        return { action: "allow", overrideBrowserWindowOptions: { webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false } } };
      }
      if (/^(https?|mailto):/.test(url)) void shell.openExternal(url);
      return { action: "deny" };
    });
    wc.on("render-process-gone", (_e, d) => {
      log(`the page stopped (${d.reason}); loading it again`);
      if (keel.running() && !quitting) void openKeel().catch(() => show(view));
    });
    wc.on("did-finish-load", () => {
      const url = wc.getURL();
      if (SMOKE && isKeel(url) && !url.includes("/web/login")) {
        void wc.executeJavaScript("document.title").then((t: string) => {
          log(`SMOKE page ${url} title ${t}`);
          process.stdout.write(`SMOKE OK keel ${keelVersion} at ${url} (${t})\n`);
          void quitApp();
        });
      }
    });
    w.on("close", (e) => {
      if (!w.isDestroyed()) {
        settings.bounds = w.getBounds();
        save();
      }
      // Linux and Windows: the last window closes the app, and with it keel: ask first when agents work
      if (process.platform !== "darwin" && !quitting && keel.running()) {
        e.preventDefault();
        void quitApp();
      }
    });
    w.on("closed", () => {
      if (win === w) win = null;
    });
    return w;
  }

  // keel 0.28 the menus reach keel's own places: a page link, or an event keel's web listens for (New flow, All
  // pages). Only while keel's page is in the window; on the app's own pages they do nothing.
  function inKeel(js: string): void {
    const w = parent();
    if (!w || !isKeel(w.webContents.getURL())) return;
    void w.webContents.executeJavaScript(js).catch((e: unknown) => log("menu:", e));
  }
  const goTo = (hash: string) => inKeel(`location.hash = ${JSON.stringify(hash)}`);
  const sendEvent = (name: string) => inKeel(`window.dispatchEvent(new CustomEvent(${JSON.stringify(name)}, { detail: {} }))`);

  function menu(): void {
    const mac = process.platform === "darwin";
    const t: MenuItemConstructorOptions[] = [];
    if (mac) t.push({ role: "appMenu" });
    t.push({
      label: "File",
      submenu: [
        // keel's web opens Start a flow on this event (its menu's New flow and the N key do the same)
        { label: "New Flow…", accelerator: "CmdOrCtrl+N", click: () => sendEvent("keel:start-flow") },
        { label: "Add Project Folder…", accelerator: "CmdOrCtrl+O", click: () => void addProject() },
        {
          label: "Open in Browser",
          accelerator: "CmdOrCtrl+Shift+B",
          click: () => void keel.signInUrl().then((u) => shell.openExternal(u), (e) => log("open in browser:", e)),
        },
        { type: "separator" },
        mac ? { role: "close" } : { label: "Quit", accelerator: "Ctrl+Q", click: () => void quitApp() },
      ],
    });
    t.push({ role: "editMenu" });
    t.push({
      label: "View",
      submenu: [
        { role: "reload" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
        ...(app.isPackaged && !process.env.KEEL_DESKTOP_DEBUG ? [] : [{ role: "toggleDevTools" } as MenuItemConstructorOptions]),
      ],
    });
    // the redesigned menu's places. No key on them: keel's page has its own (⌘K, ⌃⌥1–9), and a menu key would take them
    t.push({
      label: "Go",
      submenu: [
        { label: "Home", click: () => goTo("#/home") },
        { label: "Inbox", click: () => goTo("#/inbox") },
        { label: "Flow", click: () => goTo("#/flow") },
        { label: "Code", click: () => goTo("#/code") },
        { label: "KeelBot", click: () => goTo("#/keelbot") },
        { label: "Live Agents", click: () => goTo("#/live") },
        { type: "separator" },
        { label: "All Pages…", click: () => sendEvent("keel:all-pages") },
        { label: "Add Features", click: () => goTo("#/features") },
        { label: "Settings", click: () => goTo("#/settings") },
      ],
    });
    t.push({ role: "windowMenu" });
    t.push({
      role: "help",
      submenu: [
        { label: "Restart keel", click: () => void restart() },
        { label: "Restart keel in Safe Mode", click: () => void restart(true) },
        { label: "Show keel's Logs", click: () => void shell.openPath(paths.logs) },
        {
          label: "Copy Sign-in Key for Terminal Apps",
          click: () => {
            try {
              clipboard.writeText(fs.readFileSync(path.join(paths.data, "run", "web-key"), "utf8").trim());
              void box({
                message: "The sign-in key is copied.",
                detail: "Your own terminal apps on this computer use it (keel TUI, xal). Never give it to an agent or put it in a project.",
              });
            } catch {
              void box({ type: "warning", message: "keel has no sign-in key yet: start it first." });
            }
          },
        },
        { type: "separator" },
        { label: "keel on GitHub", click: () => void shell.openExternal(REPO) },
        { label: "Report a Problem", click: () => void shell.openExternal(`${REPO}/issues/new`) },
      ],
    });
    Menu.setApplicationMenu(Menu.buildFromTemplate(t));
  }

  // ---------------------------------------------------------------- the app's life

  app.on("second-instance", () => {
    if (!win || win.isDestroyed()) win = createWindow();
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  });
  app.on("before-quit", (e) => {
    if (quitting || !keel.running()) return;
    e.preventDefault();
    void quitApp();
  });
  app.on("window-all-closed", () => {
    // macOS keeps the app (and keel, with its agents) running: the Dock icon opens the window again
    if (process.platform !== "darwin") void quitApp();
  });
  app.on("activate", () => {
    if (win && !win.isDestroyed()) return;
    win = createWindow();
    if (keel.running()) void openKeel().catch(() => show(view));
    else void win.loadURL(`${UI}/ui/index.html`);
  });

  await app.whenReady();
  app.setAboutPanelOptions({ applicationName: "keel", applicationVersion: app.getVersion(), copyright: "MIT licence", website: REPO });
  // keel's page may copy to the clipboard and show notifications; nothing else (no camera, microphone or place)
  const allowed = new Set(["clipboard-sanitized-write", "clipboard-read", "notifications", "fullscreen"]);
  session.defaultSession.setPermissionRequestHandler((_wc, perm, cb, d) => cb(allowed.has(perm) && isKeel(d.requestingUrl)));
  session.defaultSession.setPermissionCheckHandler((_wc, perm, origin) => allowed.has(perm) && isKeel(origin));
  serveUi();
  menu();
  win = createWindow();
  await win.loadURL(`${UI}/ui/index.html`);

  // the person's PATH from their login shell (the Dock gives an app almost none)
  const login = await loginShellPath(process.env.SHELL, process.env);
  if (!login) log("the login shell gave no PATH: keel uses the one the app got");
  pathValue = mergePath(login, process.env.PATH, bundle.bin, process.env.APPDIR);
  log(`PATH ${pathValue}`);

  if (SMOKE) {
    setTimeout(() => {
      process.stdout.write("SMOKE FAILED: no keel page in 4 minutes\n");
      void keel.stop().then(() => app.exit(2));
    }, 240_000);
    keel.on("crashed", () => {
      process.stdout.write(`SMOKE FAILED: keel stopped\n${keel.logTail(30)}\n`);
      app.exit(1);
    });
  }
  if (!settings.accepted) show({ page: "welcome", data: paths.data });
  else void startKeel();
}
