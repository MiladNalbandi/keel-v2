// v0.15.2 the model picker is one dropdown: a button that opens a list of every provider's models, grouped, each group
// with its company's mark. Keys (↑↓, Home/End, Enter, Esc, letters), "Other model…" for any id, and where the list goes.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { Model } from "../api";
import { ModelPicker } from "../components/ModelPicker";
import { brandOf, ProviderIcon } from "../components/ProviderIcon";
import { providerModels } from "./fixtures";
import { server } from "./setup";

const sonnet: Model = { provider: "claude", mode: "subscription", model: "sonnet" };

function setup(start: Model = sonnet, props: { provider?: boolean; mode?: boolean; effort?: boolean } = {}) {
  const changes: Model[] = [];
  function Harness() {
    const [m, setM] = useState<Model>(start);
    return <ModelPicker id="t" value={m} onChange={(x) => { changes.push(x); setM(x); }} {...props} />;
  }
  const user = userEvent.setup();
  render(<Harness />);
  return { user, changes, button: () => screen.getByTestId("t-model") };
}
const ready = () => waitFor(() => expect(screen.getByTestId("t-source")).toHaveTextContent("from the CLI"));
const active = () => document.getElementById(screen.getByRole("listbox").getAttribute("aria-activedescendant") ?? "")!;

describe("model picker: one dropdown", () => {
  it("opens a listbox with the right ARIA, grouped by provider, each group with its company's mark", async () => {
    const { user, button } = setup();
    await ready();
    expect(button()).toHaveAttribute("aria-haspopup", "listbox");
    expect(button()).toHaveAttribute("aria-expanded", "false");
    expect(button()).toHaveAccessibleName("Model: Claude Sonnet, Claude");
    expect(button().querySelector("svg.pi")).toHaveAttribute("data-brand", "anthropic");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();

    await user.click(button());
    const list = screen.getByRole("listbox", { name: "Models" });
    expect(button()).toHaveAttribute("aria-expanded", "true");
    expect(button()).toHaveAttribute("aria-controls", list.id);
    expect(list).toHaveFocus();
    // the chosen model is selected and active; the others are not selected
    const chosen = within(list).getByRole("option", { name: "Claude Sonnet" });
    expect(chosen).toHaveAttribute("aria-selected", "true");
    expect(list).toHaveAttribute("aria-activedescendant", chosen.id);
    expect(within(list).getAllByRole("option").filter((o) => o.getAttribute("aria-selected") === "true")).toEqual([chosen]);
    // the groups, in the catalog's order, each with the mark of its company and the mode its list is for
    const groups = within(list).getAllByRole("group");
    expect(groups.map((g) => [g.getAttribute("data-provider"), g.querySelector("svg.pi")?.getAttribute("data-brand")])).toEqual([
      ["claude", "anthropic"], ["codex", "openai"], ["copilot", "github"], ["fake", "fake"],
    ]);
    expect(within(list).getByRole("group", { name: "Claude" })).toHaveTextContent("Subscription");
    expect(within(groups[2]).getAllByRole("option").map((o) => o.getAttribute("aria-label"))).toEqual(["GPT-5 (Copilot)", "Claude Sonnet 4.5 (Copilot)"]);
    // the last item types any id
    expect(within(list).getAllByRole("option").at(-1)).toHaveAccessibleName("Other model…");
  });

  it("keys: ↑↓ Home End move, Enter chooses, Esc closes and gives the focus back to the button", async () => {
    const { user, button, changes } = setup();
    await ready();
    button().focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("listbox")).toHaveFocus();
    expect(active()).toHaveAccessibleName("Claude Sonnet");
    await user.keyboard("{ArrowDown}");
    expect(active()).toHaveAccessibleName("Claude Haiku");
    await user.keyboard("{End}");
    expect(active()).toHaveAccessibleName("Other model…");
    await user.keyboard("{ArrowDown}");
    expect(active()).toHaveAccessibleName("Other model…");
    await user.keyboard("{Home}");
    expect(active()).toHaveAccessibleName("Claude Opus");
    expect(active()).toHaveClass("is-active");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(button()).toHaveFocus();
    expect(changes).toEqual([]);

    await user.keyboard("{ArrowUp}{ArrowUp}{Enter}");
    expect(changes).toEqual([{ provider: "claude", mode: "subscription", model: "opus" }]);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(button()).toHaveFocus();
    expect(button()).toHaveTextContent("Claude Opus");
    // Enter on the button opens it, as a click does
    await user.keyboard("{Enter}");
    expect(screen.getByRole("listbox")).toBeInTheDocument();
  });

  it("typing a few letters jumps to a match, on the open list and on the closed button", async () => {
    const { user, button, changes } = setup();
    await ready();
    button().focus();
    // on the closed button: the list opens on the match (a word of the name)
    await user.keyboard("h");
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(active()).toHaveAccessibleName("Claude Haiku");
    await user.keyboard("{Escape}");
    // more letters, typed quickly, keep looking from the same place
    await user.keyboard("fak");
    expect(active()).toHaveAccessibleName("Fake model");
    await user.keyboard("{Enter}");
    expect(changes.at(-1)).toEqual({ provider: "fake", mode: "api", model: "fake" });
    expect(button().querySelector("svg.pi")).toHaveAttribute("data-brand", "fake");
  });

  it("another provider's model brings its provider and default mode; its default model brings its default effort", async () => {
    const { user, button, changes } = setup({ provider: "claude", mode: "api", model: "claude-sonnet-4-5" });
    await ready();
    await user.click(button());
    const list = screen.getByRole("listbox");
    expect(within(list).getByRole("group", { name: "Claude" })).toHaveTextContent("API key");
    expect(within(list).getByRole("group", { name: "GPT / Codex" })).toHaveTextContent("Subscription");
    await user.click(within(list).getByRole("option", { name: "GPT-5 (Copilot)" }));
    expect(changes.at(-1)).toEqual({ provider: "copilot", mode: "subscription", model: "gpt-5" });
    await user.click(button());
    await user.click(screen.getByRole("option", { name: "GPT-5.6 Sol" }));
    expect(changes.at(-1)).toEqual({ provider: "codex", mode: "subscription", model: "gpt-5.6-sol", effort: "medium" });
    await user.click(button());
    // the same provider keeps the effort when the new model has it
    await user.click(screen.getByRole("option", { name: "GPT-5 mini" }));
    expect(changes.at(-1)).toEqual({ provider: "codex", mode: "subscription", model: "gpt-5-mini", effort: "medium" });
    // another provider's model that is not its default starts on "effort: default"
    await user.click(button());
    await user.click(screen.getByRole("option", { name: "Claude Opus" }));
    expect(changes.at(-1)).toEqual({ provider: "claude", mode: "subscription", model: "opus" });
  });

  it("a click outside or on the button again closes the list without a change", async () => {
    const { user, button, changes } = setup();
    await ready();
    await user.click(button());
    await user.click(document.body);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    await user.click(button());
    await user.click(button());
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(changes).toEqual([]);
  });

  it("Other model… opens a box for any id: Enter keeps it, Esc drops it; the id then shows on the button and in the list", async () => {
    const { user, button, changes } = setup();
    await ready();
    await user.click(button());
    await user.click(screen.getByRole("option", { name: "Other model…" }));
    const box = screen.getByRole("textbox", { name: "Model id" });
    expect(box).toHaveFocus();
    expect(box).toHaveValue("");
    await user.type(box, "claude-opus-5{Enter}");
    expect(changes).toEqual([{ provider: "claude", mode: "subscription", model: "claude-opus-5" }]);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(button()).toHaveFocus();
    expect(button()).toHaveTextContent("claude-opus-5");

    // the typed id is in Claude's group, chosen; Other model… starts from it
    await user.click(button());
    const typed = screen.getByRole("option", { name: "claude-opus-5" });
    expect(typed).toHaveAttribute("aria-selected", "true");
    expect(typed).toHaveTextContent("other");
    expect(typed.closest('[role="group"]')).toHaveAttribute("data-provider", "claude");
    await user.click(screen.getByRole("option", { name: "Other model…" }));
    expect(screen.getByRole("textbox", { name: "Model id" })).toHaveValue("claude-opus-5");
    await user.type(screen.getByRole("textbox", { name: "Model id" }), "-x{Escape}");
    expect(changes).toHaveLength(1);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(button()).toHaveFocus();
  });

  it("where the place fixes the provider and the mode, only that provider's models show and no runs-on select", async () => {
    const { user, button } = setup(sonnet, { provider: false, mode: false });
    await ready();
    expect(screen.queryByLabelText("Runs on")).not.toBeInTheDocument();
    await user.click(button());
    const groups = within(screen.getByRole("listbox")).getAllByRole("group");
    expect(groups.map((g) => g.getAttribute("data-provider"))).toEqual(["claude"]);
    expect(groups[0]).not.toHaveTextContent("Subscription");
  });

  it("the list stays inside the screen: moved left at the right edge, over the button at the bottom, full width on a phone", async () => {
    const { user, button } = setup();
    await ready();
    const size = (w: number, h: number) => {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: w });
      Object.defineProperty(window, "innerHeight", { configurable: true, value: h });
    };
    const at = (left: number, top: number) => {
      button().getBoundingClientRect = () => ({ left, top, right: left + 180, bottom: top + 30, width: 180, height: 30, x: left, y: top, toJSON: () => ({}) });
    };
    const [w0, h0] = [window.innerWidth, window.innerHeight];
    try {
      size(1024, 768);
      at(900, 700);
      await user.click(button());
      let list = screen.getByRole("listbox");
      expect(list.style.left).toBe("766px"); // 1024 − 8 − 250
      expect(list.style.bottom).toBe("72px"); // no room below: it opens upward
      expect(list.style.top).toBe("");
      await user.keyboard("{Escape}");

      size(375, 700);
      at(150, 100);
      await user.click(button());
      list = screen.getByRole("listbox");
      expect(list.style.left).toBe("8px");
      expect(list.style.width).toBe("359px");
      expect(list.style.top).toBe("134px");
    } finally {
      size(w0, h0);
    }
  });
});

describe("model picker in a drawer", () => {
  it("Esc closes only the list, not the Agents drawer around it; the list lives inside the drawer", async () => {
    const user = userEvent.setup();
    location.hash = "#/agents";
    render(<App />);
    await user.click(await screen.findByText("Writes the minimum code to make the failing test pass."));
    const dlg = await screen.findByRole("dialog", { name: "implementer" });
    await user.click(within(dlg).getByTestId("am-model"));
    expect(within(dlg).getByRole("listbox")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "implementer" })).toBeInTheDocument();
    expect(within(dlg).getByTestId("am-model")).toHaveFocus();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "implementer" })).not.toBeInTheDocument());
  });
});

describe("provider marks", () => {
  it("knows the companies by the provider's id or label; anything else gets its first letter", () => {
    expect(["claude", "codex", "copilot", "gemini", "openrouter", "ollama", "fake"].map((p) => brandOf(p))).toEqual(
      ["anthropic", "openai", "github", "google", "openrouter", "local", "fake"],
    );
    expect(brandOf("vertex-ai")).toBe("google");
    expect(brandOf("acme", "Google Gemini")).toBe("google");
    expect(brandOf("mistral", "Mistral")).toBe("other");
    const { container } = render(<><ProviderIcon provider="mistral" label="Mistral" /><ProviderIcon provider="claude" title="Anthropic" /></>);
    const [other, claude] = container.querySelectorAll("svg");
    expect(other).toHaveAttribute("aria-hidden", "true");
    expect(other.querySelector("text")).toHaveTextContent("M");
    expect(claude).toHaveAttribute("role", "img");
    expect(screen.getByRole("img", { name: "Anthropic" })).toBe(claude);
  });

  it("an unknown provider from the catalog still gets a group with its letter mark", async () => {
    server.use(http.get("/api/providers/models", () => HttpResponse.json({
      ...providerModels,
      gemini: { label: "Google Gemini", source: "cli", efforts: [], default: { mode: "api", model: "gemini-2.5-pro" }, modes: { api: [{ id: "gemini-2.5-pro", label: "Gemini 2.5 Pro" }] } },
      mistral: { label: "Mistral", source: "builtin", efforts: [], default: { mode: "api", model: "mistral-large" }, modes: { api: [{ id: "mistral-large", label: "Mistral Large" }] } },
    })));
    const { user, button } = setup();
    await ready();
    await user.click(button());
    const groups = within(screen.getByRole("listbox")).getAllByRole("group");
    expect(groups.map((g) => [g.getAttribute("data-provider"), g.querySelector("svg.pi")?.getAttribute("data-brand")]).slice(-2)).toEqual([
      ["gemini", "google"], ["mistral", "other"],
    ]);
    await user.click(screen.getByRole("option", { name: "Gemini 2.5 Pro" }));
    expect(button()).toHaveAccessibleName("Model: Gemini 2.5 Pro, Google Gemini");
    expect(button().querySelector("svg.pi")).toHaveAttribute("data-brand", "google");
  });
});
