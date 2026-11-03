// Runs inside VS Code (the extension host): activates the extension against the demo keel and checks the commands,
// the views' data, a flow panel, the live stream and review mode.
import * as assert from "node:assert";
import * as vscode from "vscode";

interface KeelExtension {
  state: { connected: boolean; pid?: string; runs: unknown[]; questions: unknown[]; live: boolean };
  review: { active: boolean; files: unknown[]; ranges: Map<string, unknown[]> };
  flowPanels: () => number;
}

async function until(what: string, ok: () => boolean, ms = 20_000): Promise<void> {
  const end = Date.now() + ms;
  while (!ok()) {
    if (Date.now() > end) throw new Error(`timed out waiting for: ${what}`);
    await new Promise((r) => setTimeout(r, 100));
  }
}

export async function run(): Promise<void> {
  const ext = vscode.extensions.getExtension<KeelExtension>("keel.keel-vscode");
  assert.ok(ext, "the extension is installed");
  const api = await ext.activate();
  const results: string[] = [];
  const check = async (name: string, fn: () => Promise<void> | void) => {
    await fn();
    results.push(`ok  ${name}`);
  };

  await check("every contributed command is registered", async () => {
    const all = new Set(await vscode.commands.getCommands(true));
    const declared = (ext.packageJSON.contributes.commands as { command: string }[]).map((c) => c.command);
    const missing = declared.filter((c) => !all.has(c));
    assert.deepStrictEqual(missing, []);
    assert.ok(declared.length >= 20);
  });
  await check("connects to keel and maps the folder to project shop", () => until("connected", () => api.state.connected && api.state.pid === "shop"));
  await check("lists the flows and the waiting question", () => {
    assert.strictEqual(api.state.runs.length, 3);
    assert.strictEqual(api.state.questions.length, 1);
  });
  await check("the live stream connects", () => until("live", () => api.state.live));
  await check("opens a flow panel", async () => {
    await vscode.commands.executeCommand("keel.openFlow", "t-refund");
    assert.strictEqual(api.flowPanels(), 1);
  });
  await check("review mode loads PR #7 with its changed blocks", async () => {
    await vscode.commands.executeCommand("keel.review.open", 7);
    assert.ok(api.review.active);
    assert.strictEqual(api.review.files.length, 3);
    await until("ranges of src/cart.ts", () => (api.review.ranges.get("src/cart.ts")?.length ?? 0) > 0);
    const ed = vscode.window.activeTextEditor;
    assert.ok(ed?.document.uri.fsPath.endsWith("cart.ts"), "the first changed file is open");
  });
  await check("next change moves the cursor to a changed block", async () => {
    const ed = vscode.window.activeTextEditor!;
    ed.selection = new vscode.Selection(0, 0, 0, 0);
    await vscode.commands.executeCommand("keel.review.nextChange");
    assert.strictEqual(vscode.window.activeTextEditor!.selection.active.line, 1);
  });
  await check("Old | New opens the whole file side by side: the base on the left, the head on the right", async () => {
    await vscode.commands.executeCommand("keel.review.compareFile", "src/cart.ts");
    const input = vscode.window.tabGroups.activeTabGroup.activeTab?.input;
    assert.ok(input instanceof vscode.TabInputTextDiff, "a diff editor is open");
    const left = await vscode.workspace.openTextDocument(input.original);
    const right = await vscode.workspace.openTextDocument(input.modified);
    assert.ok(left.getText().includes("TAX = 0.2;"), "the old file is on the left");
    assert.ok(right.getText().includes("TAX = 0.21;"), "the new file is on the right");
  });
  await check("exit review mode", async () => {
    await vscode.commands.executeCommand("keel.review.exit");
    assert.ok(!api.review.active);
  });
  console.log(results.join("\n"));
}
