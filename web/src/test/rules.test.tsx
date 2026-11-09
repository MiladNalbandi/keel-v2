// v0.17.2 Build › Rules: the rules in plain words with when and by which engine they are checked, the library drawer,
// a new rule (form → YAML, check and dry-run), switches, removal, and "Rules now" on the Flow page.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { EngineEvent } from "../api";
import { packYaml } from "../rulesApi";
import { db, FakeEventSource, server } from "./setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

describe("Rules page", () => {
  it("is in Build and shows each rule with when, the engines and where it is saved", async () => {
    location.hash = "#/rules";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Safety & History", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Safety & History/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Safety rules" })).toHaveAttribute("aria-selected", "true");
    const always = (await screen.findByRole("heading", { name: /Always on/ })).closest("section") as HTMLElement;
    const env = within(always).getByText(/\.env files hold secrets/).closest("tr") as HTMLElement;
    expect(within(env).getByLabelText(/Claude checked before the action/)).toBeInTheDocument();
    expect(within(env).getByLabelText(/Copilot not checked by this engine/)).toBeInTheDocument();
    expect(within(env).getByText("keel (built in)")).toBeInTheDocument();
    expect(within(env).getByTitle("This rule cannot be switched off")).toBeInTheDocument();
    expect(screen.getByLabelText("Legend")).toHaveTextContent("checked after the step: keel puts the file back");
    expect(screen.getByText(/shell commands of git, gh, the package managers/)).toBeInTheDocument();
    const list = document.querySelector('[data-list="guards.read_guard_max_lines"]') as HTMLElement;
    expect(within(list).getByText("saved in .keel/config.yml")).toBeInTheDocument();
    expect(screen.getByText(/No rule packs yet/)).toBeInTheDocument();
  });

  it("installs a pack from the library and removes it again", async () => {
    const user = userEvent.setup();
    location.hash = "#/rules";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Library" }));
    const drawer = await screen.findByRole("dialog", { name: "Rule library" });
    const item = (await within(drawer).findByText("No new dependencies")).closest(".rl-libitem") as HTMLElement;
    await user.click(within(item).getByRole("button", { name: "Install" }));
    expect(await within(item).findByText("installed")).toBeInTheDocument();
    expect(calls("POST", "/api/projects/ludus-engine/rules/library/no-new-dependencies/install")).toHaveLength(1);
    await user.click(within(drawer).getAllByRole("button", { name: "Close" })[0]);
    const pack = document.querySelector('[data-pack="no-new-dependencies"]') as HTMLElement;
    expect(within(pack).getByText(/Adding a package is a person's decision/)).toBeInTheDocument();
    expect(within(pack).getByText(".keel/rules/no-new-dependencies.yaml")).toBeInTheDocument();
    expect(within(pack).getByText("v1.0.0")).toBeInTheDocument();
    expect(within(pack).getByText("from keel's library")).toBeInTheDocument();
    expect(within(pack).getByRole("link", { name: "Export" })).toHaveAttribute("href", "/api/projects/ludus-engine/rules/packs/no-new-dependencies/export");
    await user.click(within(pack).getByRole("button", { name: "Remove" }));
    await user.click(within(within(pack).getByRole("group", { name: "Confirm" })).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(calls("DELETE", "/api/projects/ludus-engine/rules/packs/no-new-dependencies")).toHaveLength(1));
    expect(await screen.findByText(/No rule packs yet/)).toBeInTheDocument();
  });

  it("writes a new rule with the form, checks it against recent calls and saves it", async () => {
    const user = userEvent.setup();
    location.hash = "#/rules";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "New rule" }));
    const drawer = await screen.findByRole("dialog", { name: "New rule" });
    await user.clear(within(drawer).getByLabelText("Pack name"));
    await user.type(within(drawer).getByLabelText("Pack name"), "No deploys");
    await user.clear(within(drawer).getByLabelText("File name (id)"));
    await user.type(within(drawer).getByLabelText("File name (id)"), "no-deploys");
    // the starter is a complete pack: a real rule with its commands and a reason
    expect(within(drawer).getByLabelText("Commands that contain")).toHaveValue("kubectl apply\nhelm upgrade");
    expect(within(drawer).getByLabelText("Why (the agent reads this)")).toHaveValue("Deploys are a person's job. Say what should be deployed and why in your answer.");
    await user.clear(within(drawer).getByLabelText("Commands that contain"));
    await user.type(within(drawer).getByLabelText("Commands that contain"), "kubectl apply");
    await user.clear(within(drawer).getByLabelText("Why (the agent reads this)"));
    await user.click(within(drawer).getByRole("button", { name: "Check and try it" }));
    expect(await within(drawer).findByText(/reason: say in plain words/)).toBeInTheDocument();
    await user.type(within(drawer).getByLabelText("Why (the agent reads this)"), "Deploys are a person's job.");
    await user.click(within(drawer).getByRole("button", { name: "Check and try it" }));
    expect(await within(drawer).findByText(/would have refused/)).toHaveTextContent("It would have refused 1 of the last 37 tool calls");
    await user.click(within(drawer).getByRole("tab", { name: "YAML" }));
    expect((within(drawer).getByLabelText("YAML") as HTMLTextAreaElement).value).toContain("match: [kubectl apply]");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/rules/packs")).toHaveLength(1));
    expect((calls("POST", "/api/projects/ludus-engine/rules/packs")[0].body as { yaml: string }).yaml).toContain("name: no-deploys");
  });

  it("switches a rule off in .keel/config.yml; never-off rules have no switch", async () => {
    const user = userEvent.setup();
    location.hash = "#/rules";
    render(<App />);
    await user.click(await screen.findByRole("checkbox", { name: "read-twice on" }));
    await waitFor(() => expect(calls("PUT", "/api/projects/ludus-engine/rules/config")[0]?.body).toEqual({ rules: { off: ["read-twice"] } }));
    expect(await screen.findByRole("checkbox", { name: "read-twice on" })).not.toBeChecked();
    expect(screen.queryByRole("checkbox", { name: "env-files on" })).not.toBeInTheDocument();
  });

  it("two quick switches both stay off (the second is built on the first's answer)", async () => {
    const user = userEvent.setup();
    location.hash = "#/rules";
    render(<App />);
    const a = await screen.findByRole("checkbox", { name: "read-twice on" });
    const b = screen.getByRole("checkbox", { name: "push-gate on" });
    await Promise.all([user.click(a), user.click(b)]);
    await waitFor(() => expect(calls("PUT", "/api/projects/ludus-engine/rules/config")).toHaveLength(2));
    const last = calls("PUT", "/api/projects/ludus-engine/rules/config").at(-1)?.body as { rules: { off: string[] } };
    expect([...last.rules.off].sort()).toEqual(["push-gate", "read-twice"]);
  });

  it("a failed list switch goes back to where it was", async () => {
    const user = userEvent.setup();
    server.use(http.put("/api/projects/:pid/rules/config", () => HttpResponse.json({ error: "no" }, { status: 500 })));
    location.hash = "#/rules";
    render(<App />);
    await screen.findByRole("heading", { name: /Always on/ });
    const box = document.getElementById("rl-rules-push_gate") as HTMLInputElement;
    expect(box).toBeChecked();
    await user.click(box);
    await waitFor(() => expect(box).toBeChecked());
  });
});

describe("the YAML editor", () => {
  it("starts from a valid pack, marks an error at its line, and saves only after the check passes", async () => {
    const user = userEvent.setup();
    location.hash = "#/rules";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "New rule" }));
    const drawer = await screen.findByRole("dialog", { name: "New rule" });
    await user.click(within(drawer).getByRole("tab", { name: "YAML" }));
    const editor = within(drawer).getByLabelText("YAML") as HTMLTextAreaElement;
    expect(editor.value.split("\n").slice(0, 2)).toEqual(["# keel rule pack: No deploys (format: docs/rules/pack-format.md)", "schema: 1"]);
    expect(editor.value).toContain("requires:\n  keel: '>=0.17.2'");
    expect(editor.value).toContain("match: [kubectl apply, helm upgrade]");
    expect(drawer.querySelector(".ce-pre .hljs-attr")).not.toBeNull();            // highlighted like keel's code views
    expect(within(drawer).getByText("The fields of a rule pack")).toBeInTheDocument();
    // break it: no reason. Save checks first, saves nothing, and marks the line
    const broken = editor.value.replace(/\n\s+reason: .*\n?$/, "\n");
    await user.clear(editor);
    await user.click(editor);
    await user.paste(broken);
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    const list = await within(drawer).findByRole("alert", { name: "Errors" });
    const line = broken.split("\n").findIndex((l) => l.trim().startsWith("- id:")) + 1;
    expect(list).toHaveTextContent(`line ${line} rules[1].reason`);
    expect(drawer.querySelector(".ce-gutter .ce-bad")).toHaveTextContent(String(line));
    expect(calls("POST", "/api/projects/ludus-engine/rules/packs")).toHaveLength(0);
  });

  it("import checks the pasted pack before it adds it", async () => {
    const user = userEvent.setup();
    location.hash = "#/rules";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Import" }));
    const drawer = await screen.findByRole("dialog", { name: "Import a rule pack" });
    await user.click(within(drawer).getByLabelText("Or paste it"));
    await user.paste("schema: 1\nname: x\ntitle: X\nversion: 1.0.0\nrules:\n  - id: r\n    kind: bash\n    match: [x]\n");
    await user.click(within(drawer).getByRole("button", { name: "Import" }));
    expect(await within(drawer).findByRole("alert", { name: "Errors" })).toHaveTextContent("line 6");
    expect(calls("POST", "/api/projects/ludus-engine/rules/packs")).toHaveLength(0);
  });
});

describe("packYaml", () => {
  it("quotes what YAML would misread", () => {
    const y = packYaml({ name: "p", title: "No: deploys", rules: [{ id: "r", kind: "bash", match: ["a: b", "npm install"], how: "contains", phases: "in-flow", reason: "yes" }] });
    expect(y).toContain('title: "No: deploys"');
    expect(y.split("\n").slice(1, 7)).toEqual(["schema: 1", "kind: rules", "name: p", 'title: "No: deploys"', "version: 1.0.0", "publisher: local"]);
    const long = packYaml({ name: "p", title: "P", rules: [{ id: "r", kind: "edit", match: ["**/package-lock.json", "**/pnpm-lock.yaml", "**/yarn.lock", "**/uv.lock"], how: "glob", phases: "all", reason: "x" }] });
    expect(long).toContain("    match:\n      - **/package-lock.json\n");
    expect(y).toContain('match: ["a: b", npm install]');
    expect(y).toContain("phases: in-flow");
    expect(y).toContain('reason: "yes"');
  });
});

describe("Rules now on the Flow page", () => {
  it("says what agents may change, what fired, the engine's protection, and links to the Rules page", async () => {
    const user = userEvent.setup();
    location.hash = "#/flow";
    render(<App />);
    const now = await screen.findByTestId("rules-now");
    expect(now).toHaveTextContent("Phase green.");
    expect(within(now).getByText(/✓ backend production code/)).toBeInTheDocument();
    expect(within(now).getByText(/✗ backend tests/)).toBeInTheDocument();
    expect(within(now).getByText(/🔒 \.env files/)).toBeInTheDocument();
    expect(within(now).getByText("src/test/kotlin/FlakyTest.kt")).toBeInTheDocument();
    expect(within(now).getByText(/2×/).closest("li")).toHaveTextContent("npm install left-pad");
    expect(now).toHaveTextContent("copilot: shell commands checked before; file edits only after each step.");
    expect(now).toHaveTextContent("./gradlew test");
    const n = db.calls.length;
    const e: EngineEvent = { type: "guard.refused", thread_id: "th_7f3a", project_id: "ludus-engine", step: "green", at: new Date().toISOString(),
      data: { tool: "Bash", reason: "x" } };
    act(() => FakeEventSource.emit(e.type, e));
    await waitFor(() => expect(db.calls.length).toBeGreaterThanOrEqual(n));
    await user.click(screen.getByRole("button", { name: "All rules ›" }));
    expect(await screen.findByRole("heading", { name: "Safety & History", level: 1 })).toBeInTheDocument();
  });
});
