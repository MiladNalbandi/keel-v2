import * as vscode from "vscode";
import type { ChangedFile, ReviewThread, ReviewView } from "../api/models";
import { localFile, refUri } from "../core/files";
import type { KeelState } from "../core/state";
import { ago, threadStateOf } from "../logic/format";
import {
  askWhatChanged,
  decorationRanges,
  nextRange,
  placeThreads,
  prChangeRanges,
  prFileStatusOf,
  rangeAt,
  rangeLabel,
  type ChangeRange,
} from "../logic/diff";
import { relativeTo } from "../logic/paths";

export const DIFF_SCHEME = "keel-pr";

/**
 * Review mode: one pull request reviewed in the normal editors. It starts when a PR is chosen in the Review view and
 * ends with Exit review mode or when another PR is chosen. It knows the PR's changed files and each file's changed
 * blocks (keel's review diff), marks them in the editors (added and changed lines in the gutter and background, a
 * marker where lines were removed), and shows the review threads with VS Code's comments (reply, resolve).
 */
export class ReviewMode
  implements vscode.Disposable, vscode.HoverProvider
{
  key: string | undefined;
  view: ReviewView | undefined;
  readonly ranges = new Map<string, ChangeRange[]>();
  private readonly diffs = new Map<string, string>();
  private readonly controller: vscode.CommentController;
  private readonly threads = new Map<vscode.CommentThread, string>();
  private readonly changedEmitter = new vscode.EventEmitter<void>();
  readonly onDidChange = this.changedEmitter.event;
  private readonly docEmitter = new vscode.EventEmitter<vscode.Uri>();
  private loader = 0;
  private readonly disposables: vscode.Disposable[] = [];

  private readonly addedDeco: vscode.TextEditorDecorationType;
  private readonly modifiedDeco: vscode.TextEditorDecorationType;
  private readonly deletedDeco: vscode.TextEditorDecorationType;

  constructor(
    private readonly state: KeelState,
    extensionUri: vscode.Uri,
  ) {
    const icon = (n: string) =>
      vscode.Uri.joinPath(extensionUri, "media", `gutter-${n}.svg`);
    this.addedDeco = vscode.window.createTextEditorDecorationType({
      isWholeLine: true,
      backgroundColor: new vscode.ThemeColor(
        "diffEditor.insertedLineBackground",
      ),
      gutterIconPath: icon("added"),
      gutterIconSize: "contain",
      overviewRulerColor: new vscode.ThemeColor(
        "editorOverviewRuler.addedForeground",
      ),
      overviewRulerLane: vscode.OverviewRulerLane.Left,
    });
    this.modifiedDeco = vscode.window.createTextEditorDecorationType({
      isWholeLine: true,
      backgroundColor: new vscode.ThemeColor(
        "diffEditor.insertedLineBackground",
      ),
      gutterIconPath: icon("modified"),
      gutterIconSize: "contain",
      overviewRulerColor: new vscode.ThemeColor(
        "editorOverviewRuler.modifiedForeground",
      ),
      overviewRulerLane: vscode.OverviewRulerLane.Left,
    });
    this.deletedDeco = vscode.window.createTextEditorDecorationType({
      isWholeLine: true,
      borderStyle: "dashed",
      borderWidth: "1px 0 0 0",
      borderColor: new vscode.ThemeColor("editorGutter.deletedBackground"),
      gutterIconPath: icon("deleted"),
      gutterIconSize: "contain",
      overviewRulerColor: new vscode.ThemeColor(
        "editorOverviewRuler.deletedForeground",
      ),
      overviewRulerLane: vscode.OverviewRulerLane.Left,
    });
    this.controller = vscode.comments.createCommentController(
      "keel-review",
      "keel review",
    );
    this.controller.options = {
      prompt: "Reply…",
      placeHolder: "Reply to this thread",
    };
    this.disposables.push(
      this.addedDeco,
      this.modifiedDeco,
      this.deletedDeco,
      this.controller,
      this.changedEmitter,
      this.docEmitter,
      vscode.window.onDidChangeVisibleTextEditors(() => this.decorateAll()),
      vscode.window.onDidChangeActiveTextEditor(
        (e) => void this.setFileContext(e),
      ),
      vscode.workspace.registerTextDocumentContentProvider(DIFF_SCHEME, {
        onDidChange: this.docEmitter.event,
        provideTextDocumentContent: (u) => this.provideTextDocumentContent(u),
      }),
      vscode.languages.registerHoverProvider({ scheme: "file" }, this),
    );
  }

  get active(): boolean {
    return !!this.key;
  }

  get number(): number | undefined {
    return (
      this.view?.number ??
      (this.key?.startsWith("pr:") ? Number(this.key.slice(3)) : undefined)
    );
  }

  get files(): ChangedFile[] {
    return this.view?.files ?? [];
  }

  /** Review mode for this PR (key "pr:7"). */
  async enter(key: string): Promise<boolean> {
    if (this.key !== key) this.clear();
    this.key = key;
    await vscode.commands.executeCommand("setContext", "keel.reviewMode", true);
    const ok = await this.reload();
    if (!ok && this.key === key && !this.view) {
      await this.exit();
      return false;
    }
    return true;
  }

  async exit(): Promise<void> {
    this.clear();
    await vscode.commands.executeCommand(
      "setContext",
      "keel.reviewMode",
      false,
    );
    await vscode.commands.executeCommand(
      "setContext",
      "keel.reviewFile",
      false,
    );
    this.changedEmitter.fire();
  }

  private clear(): void {
    this.loader++;
    this.key = undefined;
    this.view = undefined;
    this.ranges.clear();
    this.diffs.clear();
    this.disposeThreads();
    for (const e of vscode.window.visibleTextEditors) {
      e.setDecorations(this.addedDeco, []);
      e.setDecorations(this.modifiedDeco, []);
      e.setDecorations(this.deletedDeco, []);
    }
  }

  /** The PR again (files, threads), then each file's blocks in the background. */
  async reload(refresh = false): Promise<boolean> {
    const key = this.key;
    if (!key) return false;
    const v = await this.state.call((api, pid) =>
      api.review(pid, key, refresh),
    );
    if (!v || this.key !== key) return false;
    this.setView(v);
    void this.loadRanges();
    return true;
  }

  private setView(v: ReviewView): void {
    this.view = v;
    void this.renderThreads();
    void this.setFileContext(vscode.window.activeTextEditor);
    this.changedEmitter.fire();
  }

  /** Each changed file's blocks, one file after the other. */
  private async loadRanges(): Promise<void> {
    const token = ++this.loader;
    const key = this.key;
    if (!key) return;
    const todo = this.files
      .filter((f) => !f.binary && !this.ranges.has(f.path))
      .slice(0, 150);
    for (const f of todo) {
      if (token !== this.loader || this.key !== key) return;
      const d = await this.state.call(
        (api, pid) => api.diff(pid, key, f.path),
        true,
      );
      if (token !== this.loader) return;
      const text = d?.diff ?? "";
      this.diffs.set(f.path, text);
      this.ranges.set(f.path, prChangeRanges(text));
      this.decorateAll(f.path);
      this.docEmitter.fire(this.diffUri(f.path));
    }
    this.changedEmitter.fire();
  }

  // ---------------------------------------------------------------- files and editors
  /** The PR path of an editor's file: relative to the project folder, else the changed path it ends with. */
  prPath(uri: vscode.Uri): string | undefined {
    if (!this.active || uri.scheme !== "file") return undefined;
    const folder = this.state.folder;
    const p = uri.fsPath.replace(/\\/g, "/");
    if (folder) {
      const rel = relativeTo(folder, p);
      if (this.files.some((f) => f.path === rel)) return rel;
    }
    return this.files.find((f) => p.endsWith("/" + f.path))?.path;
  }

  diffUri(path: string): vscode.Uri {
    return vscode.Uri.from({
      scheme: DIFF_SCHEME,
      path: "/" + path + ".diff",
      query: this.key ?? "",
    });
  }

  provideTextDocumentContent(uri: vscode.Uri): string {
    const path = uri.path.replace(/^\//, "").replace(/\.diff$/, "");
    const d = this.diffs.get(path);
    if (d === undefined) return `Loading the diff of ${path}…`;
    return d || `keel sent no diff for ${path} (a binary or an empty change).`;
  }

  /** Opens a changed file: the file itself with the marks, or its diff when it is deleted or not on this computer. */
  async openFile(path: string, line?: number): Promise<void> {
    const f = this.files.find((x) => x.path === path);
    const local =
      f && prFileStatusOf(f.status) !== "deleted"
        ? localFile(this.state, path)
        : undefined;
    if (!local) {
      if (!this.diffs.has(path) && this.key) {
        const key = this.key;
        const d = await this.state.call(
          (api, pid) => api.diff(pid, key, path),
          true,
        );
        this.diffs.set(path, d?.diff ?? "");
      }
      const doc = await vscode.workspace.openTextDocument(this.diffUri(path));
      await vscode.languages.setTextDocumentLanguage(doc, "diff");
      await vscode.window.showTextDocument(doc, { preview: true });
      return;
    }
    const opts: vscode.TextDocumentShowOptions = { preview: true };
    const first = line ?? this.ranges.get(path)?.[0]?.start;
    if (first && first > 0)
      opts.selection = new vscode.Range(first - 1, 0, first - 1, 0);
    await vscode.window.showTextDocument(local, opts);
  }

  /**
   * 0.21.1 Old | New: the whole file side by side in VS Code's diff editor, the old file (the review's base) on the
   * left and the new one (its head) on the right. A new file has an empty left side, a deleted one an empty right one.
   */
  async compareFile(path: string): Promise<void> {
    const key = this.key;
    if (!key) return;
    const f = this.files.find((x) => x.path === path);
    const status = prFileStatusOf(f?.status);
    const read = (p: string, side: "head" | "base") =>
      this.state.call((api, pid) => api.reviewFile(pid, key, p, side), true);
    const [base, head] = await Promise.all([
      status === "new" ? undefined : read(f?.from || path, "base"),
      status === "deleted" ? undefined : read(path, "head"),
    ]);
    if (!base && !head) {
      vscode.window.showErrorMessage(`keel: could not read ${path} from the pull request.`);
      return;
    }
    const name = path.slice(path.lastIndexOf("/") + 1);
    const left = refUri(`${f?.from || path} @ ${base?.ref ?? "base"}`, `${key}|base`, base?.text ?? "");
    const right = refUri(`${path} @ ${head?.ref ?? "head"}`, `${key}|head`, head?.text ?? "");
    const pr = this.number ? `PR #${this.number}` : key;
    await vscode.commands.executeCommand("vscode.diff", left, right, `${name}: Old | New (${pr})`, { preview: true });
  }

  private async setFileContext(
    e: vscode.TextEditor | undefined,
  ): Promise<void> {
    await vscode.commands.executeCommand(
      "setContext",
      "keel.reviewFile",
      !!(e && this.prPath(e.document.uri)),
    );
  }

  private decorateAll(onlyPath?: string): void {
    for (const e of vscode.window.visibleTextEditors) {
      const p = this.prPath(e.document.uri);
      if (onlyPath && p !== onlyPath) continue;
      this.decorate(e, p);
    }
  }

  private decorate(e: vscode.TextEditor, path: string | undefined): void {
    const rs = path ? this.ranges.get(path) : undefined;
    if (!rs) {
      e.setDecorations(this.addedDeco, []);
      e.setDecorations(this.modifiedDeco, []);
      e.setDecorations(this.deletedDeco, []);
      return;
    }
    const d = decorationRanges(rs, e.document.lineCount);
    const span = (s: { start: number; end: number }) =>
      new vscode.Range(s.start, 0, s.end, 0);
    e.setDecorations(this.addedDeco, d.added.map(span));
    e.setDecorations(this.modifiedDeco, d.modified.map(span));
    e.setDecorations(
      this.deletedDeco,
      d.deleted.map((l) => new vscode.Range(l, 0, l, 0)),
    );
  }

  /** A hover on a changed block: what it was before, and Ask KeelBot what changed here. */
  provideHover(
    doc: vscode.TextDocument,
    pos: vscode.Position,
  ): vscode.Hover | undefined {
    const path = this.prPath(doc.uri);
    if (!path) return undefined;
    const r = rangeAt(this.ranges.get(path) ?? [], pos.line + 1);
    if (!r) return undefined;
    const md = new vscode.MarkdownString(undefined, true);
    md.isTrusted = {
      enabledCommands: ["keel.review.askAboutChange", "keel.review.nextChange", "keel.review.compareFile"],
    };
    md.appendMarkdown(
      `**Changed in PR #${this.number ?? "?"}** · ${rangeLabel(r)}\n\n`,
    );
    if (r.oldLines.length) {
      md.appendMarkdown("Before:\n");
      md.appendCodeblock(
        r.oldLines.slice(0, 30).join("\n") +
          (r.oldLines.length > 30 ? "\n…" : ""),
        doc.languageId,
      );
    } else {
      md.appendMarkdown("New lines.\n\n");
    }
    const args = encodeURIComponent(JSON.stringify([path, r.start]));
    const file = encodeURIComponent(JSON.stringify([path]));
    md.appendMarkdown(
      `[$(sparkle) Ask KeelBot what changed here](command:keel.review.askAboutChange?${args}) · [$(arrow-down) Next change](command:keel.review.nextChange) · [$(split-horizontal) Old | New](command:keel.review.compareFile?${file})`,
    );
    return new vscode.Hover(md);
  }

  /** The text for KeelBot about the block at a line of a changed file. */
  askText(path: string, line: number): string | undefined {
    const r = rangeAt(this.ranges.get(path) ?? [], line);
    return r ? askWhatChanged(path, r, this.number) : undefined;
  }

  /** The next changed block: after the cursor in this file, else the first block of the next changed file. */
  async nextChange(): Promise<void> {
    if (!this.active) return;
    const e = vscode.window.activeTextEditor;
    const path = e ? this.prPath(e.document.uri) : undefined;
    if (e && path) {
      const line = e.selection.active.line + 1;
      const rs = this.ranges.get(path) ?? [];
      const after = [...rs]
        .sort((a, b) => a.start - b.start)
        .find((r) => r.start > line);
      if (after) return this.reveal(e, after);
    }
    const order = this.files
      .filter((f) => !f.binary && prFileStatusOf(f.status) !== "deleted")
      .map((f) => f.path);
    if (!order.length) return;
    const i = path ? order.indexOf(path) : -1;
    for (let k = 1; k <= order.length; k++) {
      const next = order[(i + k) % order.length];
      const first = nextRange(this.ranges.get(next) ?? [], 0);
      if (first || !this.ranges.has(next)) {
        await this.openFile(next, first?.start);
        return;
      }
    }
  }

  private reveal(e: vscode.TextEditor, r: ChangeRange): void {
    const line = Math.max(0, Math.min(r.start - 1, e.document.lineCount - 1));
    e.selection = new vscode.Selection(line, 0, line, 0);
    e.revealRange(
      new vscode.Range(line, 0, line, 0),
      vscode.TextEditorRevealType.InCenterIfOutsideViewport,
    );
  }

  // ---------------------------------------------------------------- threads (the Comments API)
  private disposeThreads(): void {
    for (const t of this.threads.keys()) t.dispose();
    this.threads.clear();
  }

  /** Every thread of the PR as a VS Code comment thread on its file (outdated ones at the top, marked so). */
  private async renderThreads(): Promise<void> {
    this.disposeThreads();
    const v = this.view;
    if (!v) return;
    const byPath = new Map<string, ReviewThread[]>();
    for (const t of v.threads ?? [])
      if (t.path) byPath.set(t.path, [...(byPath.get(t.path) ?? []), t]);
    for (const [path, ts] of byPath) {
      const uri = localFile(this.state, path) ?? this.diffUri(path);
      let lineCount = Number.MAX_SAFE_INTEGER;
      if (uri.scheme === "file") {
        try {
          lineCount = (await vscode.workspace.openTextDocument(uri)).lineCount;
        } catch {
          lineCount = Number.MAX_SAFE_INTEGER;
        }
      }
      if (this.view !== v) return;
      const places =
        uri.scheme === "file"
          ? placeThreads(ts, path, lineCount)
          : { inline: [], top: ts };
      for (const { line, thread } of places.inline)
        this.addThread(uri, line - 1, thread, v);
      for (const t of places.top)
        this.addThread(
          uri,
          0,
          t,
          v,
          t.outdated
            ? `outdated · was line ${t.line ?? "?"}`
            : t.side === "LEFT"
              ? `on a removed line ${t.line ?? ""}`.trim()
              : `line ${t.line ?? "?"}`,
        );
    }
  }

  private addThread(
    uri: vscode.Uri,
    line: number,
    t: ReviewThread,
    v: ReviewView,
    where?: string,
  ): void {
    const th = this.controller.createCommentThread(
      uri,
      new vscode.Range(line, 0, line, 0),
      commentsOf(t),
    );
    th.canReply = true;
    th.contextValue = t.resolved ? "keel-resolved" : "keel-open";
    th.state = t.resolved
      ? vscode.CommentThreadState.Resolved
      : vscode.CommentThreadState.Unresolved;
    const place = threadStateOf(t, v.me);
    th.label = [
      place === "waiting"
        ? "Waiting for you"
        : place === "open"
          ? "Open"
          : "Resolved",
      where,
    ]
      .filter(Boolean)
      .join(" · ");
    th.collapsibleState =
      t.resolved || t.outdated
        ? vscode.CommentThreadCollapsibleState.Collapsed
        : vscode.CommentThreadCollapsibleState.Expanded;
    this.threads.set(th, t.id);
  }

  /** The keel thread id of a VS Code comment thread. */
  threadId(th: vscode.CommentThread): string | undefined {
    return this.threads.get(th);
  }

  async reply(r: vscode.CommentReply): Promise<void> {
    const id = this.threadId(r.thread);
    const key = this.key;
    if (!id || !key || !r.text.trim()) return;
    const v = await this.state.call((api, pid) =>
      api.reply(pid, key, id, r.text.trim()),
    );
    if (v && this.key === key) this.setView(v);
  }

  async resolve(th: vscode.CommentThread, resolved: boolean): Promise<void> {
    const id = this.threadId(th);
    const key = this.key;
    if (!id || !key) return;
    const v = await this.state.call((api, pid) =>
      api.resolve(pid, key, id, resolved),
    );
    if (v && this.key === key) this.setView(v);
  }

  /** Opens a thread's file at its line and shows the thread. */
  async openThread(t: ReviewThread): Promise<void> {
    if (!t.path) return;
    await this.openFile(
      t.path,
      !t.outdated && t.side !== "LEFT" && t.line ? t.line : 1,
    );
    for (const [th, id] of this.threads)
      if (id === t.id)
        th.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
  }

  dispose(): void {
    this.clear();
    for (const d of this.disposables) d.dispose();
  }
}

function commentsOf(t: ReviewThread): vscode.Comment[] {
  return (t.comments ?? []).map((c) => {
    const body = new vscode.MarkdownString(c.body ?? "");
    body.supportHtml = false;
    return {
      body,
      mode: vscode.CommentMode.Preview,
      author: { name: c.author || "someone" },
      label: c.at ? ago(c.at) || undefined : undefined,
      contextValue: "keel-comment",
    } satisfies vscode.Comment;
  });
}

