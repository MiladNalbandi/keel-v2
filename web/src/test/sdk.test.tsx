// @keel/web-sdk: it exports everything keel Product's pages use from keel (Product's own test checks that they take
// nothing else), and main.tsx shares keel's own React and the SDK on window.__keel for the import map's shims.

import * as React from "react";
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  ApiError,
  Async,
  ClarifyForm,
  Confirm,
  definePlugin,
  del,
  Drawer,
  Empty,
  ErrorBox,
  errorParts,
  get,
  getText,
  hashForScreen,
  Markdown,
  Panel,
  patch,
  Pill,
  post,
  put,
  Tabs,
  answersOf,
  useLoad,
  type AddonPageProps,
  type AddonWeb,
  type ClarifyAnswers,
  type ClarifyQuestion,
  type Loaded,
  type PillTone,
} from "@keel/web-sdk";

describe("@keel/web-sdk", () => {
  it("exports what keel Product's pages use from keel", () => {
    const values = { ApiError, Async, ClarifyForm, Confirm, definePlugin, del, Drawer, Empty, ErrorBox, errorParts, get, getText,
      hashForScreen, Markdown, Panel, patch, Pill, post, put, Tabs, answersOf, useLoad };
    for (const [name, value] of Object.entries(values)) expect(typeof value, name).toBe("function");
    // the types: this file compiles only while the SDK exports them
    const page: AddonPageProps = { pid: "p", arg: "a" };
    const tone: PillTone = "ok";
    const answers: ClarifyAnswers = { q1: "yes" };
    const question: ClarifyQuestion = { id: "q1", question: "Why?", options: [] };
    const loaded: Pick<Loaded<number>, "data"> = { data: 1 };
    expect([page.pid, tone, answers.q1, question.id, loaded.data]).toEqual(["p", "ok", "yes", "q1", 1]);
  });

  it("definePlugin gives the plugin back as it is", () => {
    const plugin: AddonWeb = { name: "demo", pages: {} };
    expect(definePlugin(plugin)).toBe(plugin);
  });
});

describe("window.__keel", () => {
  it("main.tsx shares keel's React and the SDK before it renders", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    await import("../main");
    const shared = window.__keel!;
    expect(shared.React.useState).toBe(React.useState);
    expect(shared.sdk.definePlugin).toBe(definePlugin);
    expect(typeof shared.ReactDOM.createPortal).toBe("function");
    expect(typeof shared.ReactDOMClient.createRoot).toBe("function");
    expect(typeof shared.jsxRuntime.jsx).toBe("function");
    // and the app it rendered is there
    expect(await screen.findByRole("navigation", { name: "Screens" })).toBeInTheDocument();
  });
});
