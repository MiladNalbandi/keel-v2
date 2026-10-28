import { useCallback, useRef, useState } from "react";
import { questionChain } from "../../core/commands";
import { answersMessage, blockedByChanges, divergedBase, buttonWords, elsewhere, NOT_SENT_NEW_MESSAGE, prepare, pressButton, type Button } from "../../core/buttons";
import { fixRequest } from "../../../../web/src/askFix";
import { pickedMode } from "../../../../web/src/questions";
import { errorParts } from "../../core/sdk";
import { api } from "../../core/sdk";
import type { KeelCore } from "../../core/keel";
import type { Block, Pick } from "../../core/commands";

/** KeelBot's buttons (start a flow, decide a gate, stop flows, a run mode, its questions): each as a yes / no (or a chain
 *  of questions) waiting in `buttonAsks`, after its answer; `qTick` says there is a new one. */
export function useButtonAsks(core: KeelCore, push: (...bs: Block[]) => void) {
  // KeelBot's buttons (start a flow, decide a gate, stop flows, a run mode): a yes / no each, after its answer
  const buttonAsks = useRef<Pick[]>([]);
  const [qTick, setQTick] = useState(0);

  /** After a failed button: stash and switch (a git switch the folder's changes block), ask KeelBot to find the fix, or
   *  leave it. */
  const fixPick = (b: Button, err: { message: string; hint?: string }, pid: string, name: (tid: string) => string): Pick => ({
    title: "It did not work. Find a fix?",
    choices: [
      ...(blockedByChanges(b, err.message) ? [{ label: "Stash my changes and switch", value: "stash", hint: "git stash pop on your branch brings them back" }] : []),
      ...(divergedBase(b, err.message) ? [{ label: "Start on the current branch", value: "continue", hint: "the flow works on the branch the folder is on; main stays as it is" }] : []),
      { label: "Ask KeelBot to find a fix", value: "ask", hint: "it reads the error and the project, and proposes a fix you press" },
      { label: "No", value: "no" },
    ],
    then: async (v) => {
      if (v === "ask") return { blocks: [], chat: fixRequest(buttonWords(b), err) };
      if (v === "continue" && b.kind === "start") {
        try {
          const said = await pressButton({ ...b, branch: "continue" }, pid, name);
          await core.refresh().catch(() => undefined);
          return { blocks: [{ kind: "ok", text: said }] };
        } catch (e) {
          const again = errorParts(e);
          return { blocks: [{ kind: "error", text: again.message }], pick: fixPick({ ...b, branch: "continue" }, again, pid, name) };
        }
      }
      if (v === "stash" && b.kind === "git") {
        try {
          const said = await pressButton({ ...b, stash: true }, pid, name);
          await core.refresh().catch(() => undefined);
          return { blocks: [{ kind: "ok", text: said }] };
        } catch (e) {
          const again = errorParts(e);
          return { blocks: [{ kind: "error", text: again.message }], pick: fixPick({ ...b, stash: true }, again, pid, name) };
        }
      }
      return { blocks: [{ kind: "dim", text: "  left as it is" }] };
    },
  });

  /** Each of KeelBot's buttons as a yes / no; one only the dashboard has says where to press it. */
  const askButtons = useCallback(async (buttons: Button[]) => {
    const pid = core.snapshot.project?.id;
    if (!pid) return;
    const rows = await api.runs(pid, undefined, 30).catch(() => []);
    const name = (tid: string) => {
      const r = rows.find((x) => x.thread_id === tid);
      return r ? `"${r.title}"` : `flow ${tid.slice(0, 8)}`;
    };
    for (const b of buttons) {
      // keel 0.18.9 KeelBot asks the person to choose: the questions one after the other, the answers back to it
      if (b.kind === "questions") {
        // 0.18.63 an option with "mode": "edit" (KeelBot's "Switch to Edit"): the chat goes on in Edit, as /mode edit,
        // and the answer already runs in it
        const first = await questionChain(b.questions, async (a) => {
          const mode = pickedMode(b.questions, a);
          const same = mode ? await core.setChatMode(mode) : true;
          const note = mode ? [{ kind: "dim" as const, text: same ? `  switched to ${mode === "edit" ? "Edit" : "Ask"} mode` : `  ${mode === "edit" ? "Edit" : "Ask"} mode: a new chat` }] : [];
          return { blocks: note, chat: answersMessage(b.questions, a) };
        });
        if (first.blocks.length) push(...first.blocks);
        if (first.pick) buttonAsks.current.push(first.pick);
        continue;
      }
      // a new workflow or rule pack: keel's check first (it saves nothing), so the question says what it would be
      const prep = await prepare(b, pid, name).catch((e) => ({ error: e instanceof Error ? e.message : String(e) }));
      if (!prep) {
        push({ kind: "dim", text: "  " + elsewhere(b) });
        continue;
      }
      if ("error" in prep) {
        push({ kind: "error", text: prep.error });
        continue;
      }
      if (prep.detail) push({ kind: "dim", text: prep.detail.split("\n").map((l) => "  " + l).join("\n") });
      const title = prep.title;
      buttonAsks.current.push({ title, choices: [{ label: "Yes", value: "yes" }, { label: "No", value: "no" }], newMessage: NOT_SENT_NEW_MESSAGE,
        then: async (v) => {
          if (v !== "yes") return { blocks: [{ kind: "dim", text: "  Suggestion not sent: you chose No." }] };
          try {
            const said = await pressButton(b, pid, name);
            await core.refresh().catch(() => undefined);
            return { blocks: [{ kind: "ok", text: said }] };
          } catch (e) {
            // keel 0.18.9 a button that failed: a way out (stash and switch, when that is it) or KeelBot finds the fix
            const err = errorParts(e);
            return { blocks: [{ kind: "error", text: err.hint ? `${err.message} ${err.hint}` : err.message }], pick: fixPick(b, err, pid, name) };
          }
        } });
    }
    setQTick((n) => n + 1);
  }, [core, push]);
  return { buttonAsks, qTick, setQTick, askButtons };
}
