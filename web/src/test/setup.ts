import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, vi } from "vitest";
import { resetFeatures } from "../addons";
// keel's built-in parts, as main.tsx loads them: their pages, slots and launcher results
import "../builtins";
import * as sdk from "../sdk";
import type { AddonWeb } from "../sdk/plugin";
import { resetReviews } from "../components/review/store";
import { resetLauncherCache } from "../components/launcher/sources";
import { resetProviderModels } from "../components/ModelPicker";
import { resetAudio } from "../notify";
import { createDb, handlers, type Db } from "./handlers";

// keel as the full image has it: every plugin in plugins/ is there, and its setup() has run once (as addons.ts does at
// start with the web parts /api/features lists), so the menu and the slots are the same as for people
const plugins = import.meta.glob<{ default: AddonWeb }>("../../../plugins/*/web/index.tsx", { eager: true });
for (const m of Object.values(plugins)) m.default.setup?.(sdk);

// findBy* / waitFor wait up to 3 s (the default 1 s failed on CI's slower machines: the Repo IDE's first render took 1.1 s).
configure({ asyncUtilTimeout: 3000 });

// ---- MSW: one server, a fresh db per test ----
export let db: Db = createDb();
export const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  db = createDb();
  server.resetHandlers(...handlers(db));
  (globalThis as unknown as { __db: Db }).__db = db;
  localStorage.clear();
  sessionStorage.clear();
  location.hash = "";
  FakeEventSource.instances = [];
  audioLog.length = 0;
  resetAudio();
  resetProviderModels();
  resetFeatures();
  resetReviews();
  resetLauncherCache();
});
afterEach(() => cleanup());
afterAll(() => server.close());

// ---- EventSource: tests push server events with FakeEventSource.emit ----
export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onopen: ((e: Event) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
    queueMicrotask(() => this.onopen?.(new Event("open")));
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  removeEventListener() {}
  close() {
    this.closed = true;
  }
  /** Send an event on the streams whose url matches only (e.g. the all-projects stream). */
  static emitTo(match: (url: string) => boolean, type: string, data: unknown) {
    FakeEventSource.instances.filter((s) => !s.closed && match(s.url)).forEach((s) => {
      const e = new MessageEvent(type, { data: JSON.stringify(data) });
      (s.listeners[type] ?? []).forEach((fn) => fn(e));
    });
  }
  static emit(type: string, data: unknown) {
    FakeEventSource.instances.filter((s) => !s.closed).forEach((s) => {
      const e = new MessageEvent(type, { data: JSON.stringify(data) });
      (s.listeners[type] ?? []).forEach((fn) => fn(e));
    });
  }
}
vi.stubGlobal("EventSource", FakeEventSource);

// ---- WebAudio: record oscillator frequencies so tests can check which sound played ----
export const audioLog: number[] = [];
class FakeAudioContext {
  state = "running";
  currentTime = 0;
  destination = {};
  resume() { return Promise.resolve(); }
  createOscillator() {
    const o = {
      type: "sine", frequency: { value: 0 },
      connect: (n: unknown) => n, start: () => audioLog.push(o.frequency.value), stop: () => undefined,
    };
    return o;
  }
  createGain() {
    return { gain: { setValueAtTime: () => undefined, linearRampToValueAtTime: () => undefined, exponentialRampToValueAtTime: () => undefined }, connect: (n: unknown) => n };
  }
}
vi.stubGlobal("AudioContext", FakeAudioContext);

// ---- jsdom gaps ----
Element.prototype.scrollIntoView = function () {};
window.scrollTo = (() => undefined) as typeof window.scrollTo;
window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, onchange: null })) as unknown as typeof window.matchMedia;
