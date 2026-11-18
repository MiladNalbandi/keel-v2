// keel TUI's version: keel v2's (cli/package.json, engine/pyproject.toml and keel2 move together). The build puts
// it in from package.json; tests and tsx see the fallback.
declare const __KEEL_TUI_VERSION__: string | undefined;
export const VERSION: string = typeof __KEEL_TUI_VERSION__ === "string" ? __KEEL_TUI_VERSION__ : "0.17.3";

/** 0.37.1 (#194) keel TUI is parked: it keeps working and gets its fixes; new features land in the web app and the IDE plugins. */
export const PARKED = "keel TUI is parked: it keeps working, new features land in the web app (keel2 open) and the IDE plugins.";
