import * as vscode from "vscode";
import { say } from "./api/client";
import type { InboxItem, PrSummary, ReviewThread } from "./api/models";
import { Auth } from "./core/auth";
import { refDocs } from "./core/files";
import { startFlow } from "./core/startFlow";
import { KeelState } from "./core/state";
import { StatusItem } from "./core/statusBar";
import { askText, firstLine, requestFromSelection } from "./logic/format";
import { buildAnswer, quickApprove, waitingOf } from "./logic/gate";
import { relativeTo } from "./logic/paths";
import {
  ChangedFilesTree,
  ReviewTree,
  type ReviewNode,
} from "./review/reviewTree";
import { ReviewMode } from "./review/reviewMode";
import { FlowPanel } from "./views/flowPanel";
import { KeelBotView } from "./views/keelbotView";
import { FlowsTree, InboxTree } from "./views/trees";

/** What the extension hands out (the smoke test reads it). */
export interface KeelExtension {
  state: KeelState;
  review: ReviewMode;
  flowPanels: () => number;
}

/** The selection of the active editor as keel wants it: the folder-relative path, its lines and the code. */
function selection(
  state: KeelState,
): { path: string; start: number; end: number; code: string } | undefined {
  const ed = vscode.window.activeTextEditor;
  if (!ed) return undefined;
  const doc = ed.document;
  let range: vscode.Range = ed.selection;
  if (ed.selection.isEmpty) range = doc.lineAt(ed.selection.active.line).range;
  const code = doc.getText(range);
  const folder = state.folder;
  const path =
    folder && doc.uri.scheme === "file"
      ? relativeTo(folder, doc.uri.fsPath)
      : doc.uri.path.split("/").pop() || "this file";
  const endLine =
    range.end.character === 0 && range.end.line > range.start.line
      ? range.end.line
      : range.end.line + 1;
  return { path, start: range.start.line + 1, end: endLine, code };
}

export function activate(ctx: vscode.ExtensionContext): KeelExtension {
  const auth = new Auth(ctx);
  const state = new KeelState(auth, ctx);
  const review = new ReviewMode(state, ctx.extensionUri);
  const keelbot = new KeelBotView(state, ctx.extensionUri);
  const reviewTree = new ReviewTree(state, review);
  const reviewView = vscode.window.createTreeView<ReviewNode>("keel.review", {
    treeDataProvider: reviewTree,
    showCollapseAll: true,
  });
  reviewTree.treeView = reviewView;
  reviewView.description = reviewTree.filter();

  ctx.subscriptions.push(
    refDocs(),
    state,
    review,
    keelbot,
    reviewView,
    new StatusItem(state),
    vscode.window.createTreeView("keel.flows", {
      treeDataProvider: new FlowsTree(state),
    }),
    vscode.window.createTreeView("keel.inbox", {
      treeDataProvider: new InboxTree(state),
    }),
    vscode.window.createTreeView("keel.changedFiles", {
      treeDataProvider: new ChangedFilesTree(review),
    }),
    vscode.window.registerWebviewViewProvider(KeelBotView.id, keelbot, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("keel.review.filter")) {
        reviewView.description = reviewTree.filter();
        reviewTree.refresh();
      }
    }),
    state.onNewQuestion((q) => void notify(q)),
  );

  const cmd = (id: string, fn: (...args: any[]) => unknown) =>
    ctx.subscriptions.push(vscode.commands.registerCommand(id, fn));

  // ---------------------------------------------------------------- connection and project
  cmd("keel.connect", () => auth.connect());
  cmd("keel.signOut", () => auth.signOut());
  cmd("keel.refresh", async () => {
    reviewTree.refresh();
    await state.refresh();
  });
  cmd("keel.pickProject", async () => {
    if (!state.connected) await state.refresh();
    if (!state.projects.length) {
      vscode.window.showWarningMessage(
        `keel: no projects to pick. ${state.error ? say(state.error) : ""}`.trim(),
      );
      return;
    }
    const items = state.projects.map((p) => ({
      label: p.name,
      description: p.root,
      detail: p.id === state.pid ? "this folder now" : undefined,
      id: p.id,
    }));
    const auto = {
      label: "$(search) Find it by folder",
      description: "match the workspace folder to keel's project paths",
      id: "",
    };
    const pick = await vscode.window.showQuickPick([auto, ...items], {
      placeHolder: "Which keel project is this folder?",
    });
    if (pick) await state.pick(pick.id || undefined);
  });

  // ---------------------------------------------------------------- flows and questions
  cmd("keel.startFlow", async (prefill?: string) => {
    const tid = await startFlow(
      state,
      typeof prefill === "string" ? prefill : undefined,
    );
    if (tid) FlowPanel.show(state, ctx.extensionUri, tid, true);
  });
  cmd("keel.startFlowFromSelection", async () => {
    const sel = selection(state);
    const tid = await startFlow(
      state,
      sel
        ? requestFromSelection(sel.path, sel.start, sel.end, sel.code)
        : undefined,
    );
    if (tid) FlowPanel.show(state, ctx.extensionUri, tid, true);
  });
  cmd("keel.openFlow", (threadId: string, focusGate?: boolean) => {
    if (typeof threadId === "string" && threadId)
      FlowPanel.show(state, ctx.extensionUri, threadId, !!focusGate);
  });
  cmd("keel.stopFlow", async (row?: { thread_id?: string; title?: string }) => {
    const tid = row?.thread_id;
    if (!tid) return;
    const ok = await vscode.window.showWarningMessage(
      `Stop the flow "${row?.title ?? tid}"? Its work so far stays.`,
      { modal: true },
      "Stop flow",
    );
    if (ok !== "Stop flow") return;
    if (await state.call((api) => api.stop(tid)))
      vscode.window.showInformationMessage("keel: the flow stopped.");
    await state.refresh();
  });
  cmd("keel.openInbox", async () => {
    await vscode.commands.executeCommand("keel.inbox.focus");
  });
  cmd("keel.answerItem", (item?: InboxItem) => item && answerItem(item));

  /** A question of the Inbox: a flow's gate opens the flow at its question; KeelBot's command asks here. */
  async function answerItem(item: InboxItem): Promise<void> {
    if (item.kind === "permission" && item.permission) {
      const p = item.permission;
      const choice = await vscode.window.showWarningMessage(
        `KeelBot wants to run: ${p.command ?? ""}`,
        { modal: true, detail: p.path ? `in ${p.path}` : undefined },
        "Allow once",
        "Always",
        "Deny",
      );
      const decision =
        choice === "Allow once"
          ? "once"
          : choice === "Always"
            ? "always"
            : choice === "Deny"
              ? "deny"
              : undefined;
      if (!decision) return;
      await state.call((api) => api.decideApproval(p.id, decision));
      await state.refresh();
      return;
    }
    if (item.thread_id)
      FlowPanel.show(state, ctx.extensionUri, item.thread_id, true);
  }

  /** A new question of this project: "Approve / Open" when a single approve is offered, else only "Open". */
  async function notify(q: InboxItem): Promise<void> {
    if (
      !vscode.workspace
        .getConfiguration("keel")
        .get<boolean>("notifications", true)
    )
      return;
    if (q.kind === "permission" && q.permission) {
      const pick = await vscode.window.showWarningMessage(
        `KeelBot wants to run: ${firstLine(q.permission.command, 120)}`,
        "Open",
      );
      if (pick) await answerItem(q);
      return;
    }
    const flow = q.flow || "A flow";
    const approve = quickApprove(q);
    const actions = approve ? [approve.label, "Open"] : ["Open"];
    const pick = await vscode.window.showInformationMessage(
      `keel: "${flow}" waits for you: ${q.title || "a question"}`,
      ...actions,
    );
    if (!pick) return;
    if (approve && pick === approve.label && q.thread_id) {
      const tid = q.thread_id;
      const r = await state.call((api) =>
        api.answer(tid, buildAnswer(waitingOf(q), { picked: approve.name })),
      );
      if (r)
        vscode.window.showInformationMessage(
          `keel: sent "${approve.label}" to "${flow}".`,
        );
      await state.refresh();
    } else if (q.thread_id) {
      FlowPanel.show(state, ctx.extensionUri, q.thread_id, true);
    }
  }

  // ---------------------------------------------------------------- KeelBot
  cmd("keel.openKeelBot", () =>
    vscode.commands.executeCommand(`${KeelBotView.id}.focus`),
  );
  cmd("keel.askKeelBot", async () => {
    const sel = selection(state);
    await keelbot.prefill(
      sel && sel.code.trim()
        ? askText(sel.path, sel.start, sel.end, sel.code)
        : "",
    );
  });

  // ---------------------------------------------------------------- review mode
  cmd("keel.review.open", async (arg?: number | PrSummary | ReviewNode) => {
    let n: number | undefined;
    if (typeof arg === "number") n = arg;
    else if (arg && "kind" in arg && arg.kind === "pr") n = arg.pr.number;
    else if (arg && "number" in arg) n = arg.number;
    if (n === undefined) {
      const raw = await vscode.window.showInputBox({
        prompt: "Pull request number",
        validateInput: (v) =>
          /^\d+$/.test(v.trim()) ? undefined : "A number, like 7",
      });
      if (!raw) return;
      n = Number(raw.trim());
    }
    const ok = await review.enter(`pr:${n}`);
    if (ok) {
      vscode.window.setStatusBarMessage(
        `keel: reviewing #${n} · ${review.files.length} files`,
        5000,
      );
      const first = review.files[0];
      if (first) await review.openFile(first.path);
    }
  });
  cmd("keel.review.exit", () => review.exit());
  cmd("keel.review.filter", async () => {
    const cur = reviewTree.filter();
    const items = [
      { label: "review", description: "asks for your review" },
      { label: "assigned", description: "assigned to you" },
      { label: "mine", description: "your own" },
      { label: "all", description: "all open pull requests" },
    ].map((i) => ({ ...i, picked: i.label === cur }));
    const pick = await vscode.window.showQuickPick(items, {
      placeHolder: "Which pull requests?",
    });
    if (pick)
      await vscode.workspace
        .getConfiguration("keel")
        .update(
          "review.filter",
          pick.label,
          vscode.ConfigurationTarget.Workspace,
        );
  });
  cmd("keel.review.openFile", async (key: string, path: string) => {
    if (review.key !== key && !(await review.enter(key))) return;
    await review.openFile(path);
  });
  cmd("keel.review.openThread", async (key: string, t: ReviewThread) => {
    if (review.key !== key && !(await review.enter(key))) return;
    await review.openThread(t);
  });
  cmd("keel.review.nextChange", () => review.nextChange());
  // 0.21.1 the whole file, old next to new
  cmd("keel.review.compareFile", async (arg?: string | ReviewNode) => {
    const ed = vscode.window.activeTextEditor;
    // a path (the hover's link), a changed file in the Review view (its menu), or the file in the editor
    if (arg && typeof arg !== "string" && arg.kind === "file" && review.key !== arg.key && !(await review.enter(arg.key))) return;
    const p =
      typeof arg === "string"
        ? arg
        : arg?.kind === "file"
          ? arg.file.path
          : ed
            ? review.prPath(ed.document.uri)
            : undefined;
    if (!p) {
      vscode.window.showInformationMessage("keel: open a changed file of the pull request first.");
      return;
    }
    await review.compareFile(p);
  });
  cmd("keel.review.askAboutChange", async (path?: string, line?: number) => {
    let p = path;
    let l = line;
    if (!p) {
      const ed = vscode.window.activeTextEditor;
      p = ed ? review.prPath(ed.document.uri) : undefined;
      l = ed ? ed.selection.active.line + 1 : undefined;
    }
    const text = p && l ? review.askText(p, l) : undefined;
    if (!text) {
      vscode.window.showInformationMessage(
        "keel: put the cursor on a changed block of the pull request.",
      );
      return;
    }
    await keelbot.prefill(text);
  });
  cmd("keel.review.reply", (r: vscode.CommentReply) => review.reply(r));
  cmd("keel.review.resolve", (t: vscode.CommentThread) =>
    review.resolve(t, true),
  );
  cmd("keel.review.unresolve", (t: vscode.CommentThread) =>
    review.resolve(t, false),
  );

  void vscode.commands.executeCommand("setContext", "keel.reviewMode", false);
  void state.refresh();
  return { state, review, flowPanels: () => FlowPanel.count() };
}

export function deactivate(): void {
  // everything is in ctx.subscriptions
}
