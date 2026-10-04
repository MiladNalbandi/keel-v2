import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, vi } from "vitest";
import { resetProviderModels } from "../components/ModelPicker";
import { resetAudio } from "../notify";
import { createDb, handlers, type Db } from "./handlers";

// ---- MSW: one server, a fresh db per test ----
export let db: Db = createDb();
export const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  db = createDb();
  server.resetHandlers(...handlers(db));
  (globalThis as unknown as { __db: Db }).__db = db;
  localStorage.clear();
  location.hash = "";
  FakeEventSource.instances = [];
  audioLog.length = 0;
  resetAudio();
  resetProviderModels();
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
