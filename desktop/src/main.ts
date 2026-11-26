// keel desktop: keel without Docker, in one download (desktop/README.md). The main process: one window, keel's own
// pages for the first start and for errors (ui/, served as keel-app://app/), and keel itself (supervisor.ts) behind
// keel's web page, signed in by the app.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { app, BrowserWindow, clipboard, dialog, Menu, nativeImage, nativeTheme, net, Notification, protocol, session, shell, Tray, type MenuItemConstructorOptions } from "electron";
import { connectText, mcpLauncher, mcpLauncherPath } from "./mcp";
import { autostartEntry, autostartFile, newWaits, nowLine, nowOf, startsHidden, tooltipOf, type Now } from "./background";
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
  if (SMOKE) settings = { ...settings, accepted: true, folders: process.env.KEEL_DESKTOP_SMOKE_FOLDER ? [process.env.KEEL_DESKTOP_SMOKE_FOLDER] : [], keepRunning: false };
  let view: View = { page: "boot", text: "Opening keel…" };
  let win: BrowserWindow | null = null;
  let pathValue = process.env.PATH || "/usr/bin:/bin";
  let quitting = false;
  // 0.29.2 opened at login (or with --background): no window, only the icon, until the person opens keel
  const hiddenStart = !SMOKE && startsHidden(process.argv, process.platform === "darwin" && app.getLoginItemSettings().wasOpenedAtLogin);
  let tray: Tray | null = null;
  let opened = false; // the first window of a hidden start stays hidden
  let now: Now | null = null;
  let seen: Now | null = null;
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
        await keel.start({
          folders: settings.folders.filter((f) => fs.existsSync(f)), port: settings.port, safe, pathValue, env: process.env,
          // the app's browser profile holds the window's sign-in cookie: as strong as keel's own keys
          hide: [app.getPath("userData")],
        });
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
      writeMcpLauncher();
      await openKeel();
      void look();
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
      if (!SMOKE && !(hiddenStart && !opened)) w.show();
      opened = true;
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
      if (quitting) return;
      // 0.29.2 keel keeps running in the background: the window goes, keel, its flows and its MCP stay; the icon's menu
      // opens it again or quits keel
      if (settings.keepRunning && keel.running()) {
        e.preventDefault();
        w.hide();
        hintOnce();
        return;
      }
      // keep running is off (Linux and Windows): the last window closes the app, and with it keel: ask first
      if (process.platform !== "darwin" && keel.running()) {
        e.preventDefault();
        void quitApp();
      }
    });
    w.on("closed", () => {
      if (win === w) win = null;
    });
    return w;
  }

  // ---------------------------------------------------------------- 0.29.2 keel's MCP for Claude Code and OpenCode

  /** <keel folder>/keel-mcp, for this keel's port and the app's Python (the AppImage's place changes every run). */
  function writeMcpLauncher(): void {
    if (!keel.ports) return;
    try {
      const f = mcpLauncherPath(paths);
      fs.writeFileSync(`${f}.tmp`, mcpLauncher({ bundle, paths, port: keel.ports.api }), { mode: 0o700 });
      fs.renameSync(`${f}.tmp`, f);
    } catch (e) {
      log("keel-mcp:", e);
    }
  }

  async function connectClients(): Promise<void> {
    const c = connectText(mcpLauncherPath(paths));
    const r = await box({
      type: "info",
      buttons: ["Copy for Claude Code", "Copy for OpenCode", "Copy for Claude Desktop", "Close"],
      defaultId: 0,
      cancelId: 3,
      message: "Connect Claude Code or OpenCode to keel",
      detail:
        "Your own Claude Code or OpenCode (signed in with your plan) can work for keel: keel sends the work, checks it and makes the commits.\n\n" +
        `Claude Code, in a terminal:\n${c.claude}\n\n` +
        `OpenCode: add this to opencode.json:\n${c.opencode}\n\n` +
        "Then tell it: \"Work for keel: call keel_session_open, then keel_session_next, and follow its work orders.\" " +
        "In keel: Start a flow › Run with › Connected session. keel must be running (this app).",
    });
    const pickText = [c.claude, c.opencode, c.desktop][r.response];
    if (pickText) clipboard.writeText(pickText);
  }

  // ---------------------------------------------------------------- 0.29.2 in the background

  /** The window again (the icon, the Dock, a second start of the app): the one there is, or a new one on keel. */
  function openWindow(hash?: string): void {
    if (!win || win.isDestroyed()) {
      win = createWindow();
      if (keel.running()) void openKeel().then(() => hash && goTo(hash)).catch(() => show(view));
      else void win.loadURL(`${UI}/ui/index.html`);
    } else if (hash) goTo(hash);
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    if (process.platform === "darwin") void app.dock?.show();
  }

  let hinted = false;
  /** The first close in this run says once where keel went. */
  function hintOnce(): void {
    if (hinted || settings.quiet || !Notification.isSupported()) return;
    hinted = true;
    const where = process.platform === "darwin" ? "the menu bar" : "the tray";
    new Notification({ title: "keel keeps running", body: `Your flows and keel's MCP go on. Open keel or quit it from its icon in ${where}.`, silent: true }).show();
  }

  function trayIcon(): Electron.NativeImage {
    const img = nativeImage.createFromPath(path.join(__dirname, "..", "..", "ui", "icon.png"));
    return img.isEmpty() ? img : img.resize({ width: process.platform === "darwin" ? 18 : 22 });
  }

  function trayMenu(): void {
    if (!tray) return;
    const up = keel.running();
    const t: MenuItemConstructorOptions[] = [
      { label: "Open keel", click: () => openWindow() },
      { label: nowLine(now, up), enabled: false },
      ...(now && now.waiting ? [{ label: "Open the Inbox", click: () => openWindow("#/inbox") } as MenuItemConstructorOptions] : []),
      { type: "separator" },
      { label: "Notifications", type: "checkbox", checked: !settings.quiet, click: (i) => { settings.quiet = !i.checked; save(); } },
      { label: "Keep keel running when the window is closed", type: "checkbox", checked: settings.keepRunning, click: (i) => { settings.keepRunning = i.checked; save(); } },
      { label: "Start keel when I log in", type: "checkbox", checked: settings.startAtLogin, click: (i) => setStartAtLogin(i.checked) },
      { type: "separator" },
      { label: "Quit keel", click: () => void quitApp() },
    ];
    tray.setContextMenu(Menu.buildFromTemplate(t));
    tray.setToolTip(tooltipOf(now, up));
  }

  function makeTray(): void {
    if (SMOKE || tray) return;
    try {
      tray = new Tray(trayIcon());
      tray.on("click", () => openWindow());
      trayMenu();
    } catch (e) {
      // a Linux desktop without a tray (no StatusNotifier): the window stays the way in, and close quits as before
      log("no tray icon:", e);
      tray = null;
    }
  }

  /** What keel does now, every 15 s: the icon's line, and a notification for a new "waits for you" while no window shows. */
  async function look(): Promise<void> {
    if (!keel.running()) {
      now = null;
      trayMenu();
      return;
    }
    try {
      const r = await keel.call("GET", "/projects");
      const n = r.ok ? nowOf(await r.json()) : null;
      if (n) {
        const visible = !!win && !win.isDestroyed() && win.isVisible() && !win.isMinimized();
        // keel's own page notifies while it shows; the app does when it does not
        if (!visible && !settings.quiet && Notification.isSupported()) {
          for (const m of newWaits(seen, n)) {
            const note = new Notification({ title: m.title, body: m.body });
            note.on("click", () => openWindow("#/inbox"));
            note.show();
          }
        }
        seen = n;
      }
      now = n;
    } catch (e) {
      log("look:", e);
    }
    trayMenu();
  }

  function setStartAtLogin(on: boolean): void {
    settings.startAtLogin = on;
    save();
    try {
      if (process.platform === "darwin") {
        app.setLoginItemSettings({ openAtLogin: on }); // opened at login: wasOpenedAtLogin, no window
      } else if (process.platform === "linux") {
        const f = autostartFile(os.homedir(), process.env);
        // the AppImage's own path (APPIMAGE); a dev run (npm start) has none: nothing to start at login
        const exe = process.env.APPIMAGE || (app.isPackaged ? process.execPath : "");
        if (on && exe) {
          fs.mkdirSync(path.dirname(f), { recursive: true });
          fs.writeFileSync(f, autostartEntry(exe), { mode: 0o644 });
        } else fs.rmSync(f, { force: true });
      } else {
        app.setLoginItemSettings({ openAtLogin: on, args: ["--background"] });
      }
    } catch (e) {
      log("start at login:", e);
    }
    trayMenu();
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
        { label: "Keep keel Running When the Window Is Closed", type: "checkbox", checked: settings.keepRunning, click: (i) => { settings.keepRunning = i.checked; save(); trayMenu(); } },
        { label: "Start keel When I Log In", type: "checkbox", checked: settings.startAtLogin, click: (i) => setStartAtLogin(i.checked) },
        { type: "separator" },
        mac ? { role: "close" } : { label: "Close Window", accelerator: "Ctrl+W", click: () => parent()?.close() },
        ...(mac ? [] : [{ label: "Quit keel", accelerator: "Ctrl+Q", click: () => void quitApp() } as MenuItemConstructorOptions]),
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
        { label: "Connect Claude Code or OpenCode…", click: () => void connectClients() },
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

  app.on("second-instance", () => openWindow());
  app.on("before-quit", (e) => {
    if (quitting || !keel.running()) return;
    e.preventDefault();
    void quitApp();
  });
  app.on("window-all-closed", () => {
    // macOS keeps the app (and keel, with its agents) running: the Dock icon opens the window again; elsewhere keel
    // keeps running when the person chose it (the icon stays)
    if (process.platform !== "darwin" && !(settings.keepRunning && keel.running())) void quitApp();
  });
  app.on("activate", () => openWindow());

  await app.whenReady();
  app.setAboutPanelOptions({ applicationName: "keel", applicationVersion: app.getVersion(), copyright: "MIT licence", website: REPO });
  // keel's page may copy to the clipboard and show notifications; nothing else (no camera, microphone or place)
  const allowed = new Set(["clipboard-sanitized-write", "clipboard-read", "notifications", "fullscreen"]);
  session.defaultSession.setPermissionRequestHandler((_wc, perm, cb, d) => cb(allowed.has(perm) && isKeel(d.requestingUrl)));
  session.defaultSession.setPermissionCheckHandler((_wc, perm, origin) => allowed.has(perm) && isKeel(origin));
  serveUi();
  menu();
  makeTray();
  if (process.platform === "darwin" && hiddenStart) app.dock?.hide();
  win = createWindow();
  await win.loadURL(`${UI}/ui/index.html`);
  if (!SMOKE) setInterval(() => void look(), 15_000);

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
  if (!SMOKE && settings.startAtLogin) setStartAtLogin(true); // the AppImage may have moved: write its path again
  if (!settings.accepted) show({ page: "welcome", data: paths.data });
  else void startKeel();
}
