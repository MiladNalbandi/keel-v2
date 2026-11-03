import { existsSync } from "node:fs";
import * as vscode from "vscode";
import { localCandidates } from "../logic/paths";
import type { KeelState } from "./state";

/** The file on this computer a path keel names is, if any (see localCandidates). */
export function localFile(state: KeelState, path: string): vscode.Uri | undefined {
  const folder = state.folder;
  if (!folder) return undefined;
  const hit = localCandidates(path, state.project?.root, folder).find((p) => existsSync(p));
  return hit ? vscode.Uri.file(hit) : undefined;
}

/** Opens a path keel names at a 1-based line; false when it is not in this folder. */
export async function openPath(state: KeelState, path: string, line?: number): Promise<boolean> {
  const uri = localFile(state, path);
  if (!uri) {
    vscode.window.showInformationMessage(`keel: ${path} is not in this folder.`);
    return false;
  }
  const opts: vscode.TextDocumentShowOptions = { preview: true };
  if (line && line > 0) opts.selection = new vscode.Range(line - 1, 0, line - 1, 0);
  try {
    await vscode.window.showTextDocument(uri, opts);
    return true;
  } catch {
    // not a text file (an image, a binary): let VS Code pick the editor
    await vscode.commands.executeCommand("vscode.open", uri);
    return true;
  }
}

/** 0.18.57 the read-only documents of files at another ref (keel-ref:/<path>?<ref>): their text, read from keel's api */
export const REF_SCHEME = "keel-ref";
const refTexts = new Map<string, string>();

export function refDocs(): vscode.Disposable {
  return vscode.workspace.registerTextDocumentContentProvider(REF_SCHEME, {
    provideTextDocumentContent: (u) => refTexts.get(u.toString()) ?? "",
  });
}

/** 0.21.1 a read-only document (keel-ref:) holding `text`, titled by `label` */
export function refUri(label: string, query: string, text: string): vscode.Uri {
  const uri = vscode.Uri.from({ scheme: REF_SCHEME, path: `/${label}`, query });
  refTexts.set(uri.toString(), text);
  return uri;
}

/**
 * Opens a path keel names as `ref` has it (a KeelBot answer made on another branch, a PR head it read with git show):
 * the local file when the ref is the folder's checked-out branch (or keel cannot read it), else the file's text from
 * keel's api (GET /repo/file-at), read-only, in a document titled "<path> @ <ref>".
 */
export async function openPathAt(state: KeelState, path: string, line: number | undefined, ref: string): Promise<boolean> {
  const f = await state.call((api, pid) => api.fileAt(pid, path, ref), true);
  if (!f || f.current || f.binary || f.text == null) return openPath(state, path, line);
  const uri = vscode.Uri.from({ scheme: REF_SCHEME, path: `/${f.path} @ ${ref}`, query: ref });
  refTexts.set(uri.toString(), f.text);
  const doc = await vscode.workspace.openTextDocument(uri);
  const opts: vscode.TextDocumentShowOptions = { preview: true };
  if (line && line > 0) opts.selection = new vscode.Range(line - 1, 0, line - 1, 0);
  await vscode.window.showTextDocument(doc, opts);
  vscode.window.setStatusBarMessage(`keel: ${f.path} as ${ref} has it (not checked out here)`, 8000);
  return true;
}
