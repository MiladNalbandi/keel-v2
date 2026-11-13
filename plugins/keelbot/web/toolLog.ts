// 0.18.38 The tool calls an agent printed at the top of its answer, as plain text (an older chat's Copilot CLI:
// "● cd src; cat -n …", "│ …", "└ 220 lines…", "/ Search (grep) …", and "I'll start with …" between them; or a ```bash
// block of "$ command" and what it printed). The answer shows them folded to one line ("✓ Ran 3 shell commands — … · 151 + 45 lines"), so
// the answer itself comes first. Only a log at the very top counts: the same lines later in an answer stay as they are.

export type ToolCall = { title: string; shell: boolean; lines: number | null };
export type ToolLog = { calls: ToolCall[]; log: string; rest: string };

const START = /^\s*[●⏺✓✔✗✘]\s+(.+?)\s*$/;
const SEARCH = /^\s*\/ ((?:Search|Read|Glob|Grep|Bash|List)\b.*?)\s*$/;
const MORE = /^\s*(?:[│├└⎿↪┌╭╰]|\$ )/;
const COUNT = /^\s*[└⎿↪]\s*(\d+)\s+lines?\b/;
const NARRATION =
  /^\s*(?:I'll|I will|I'm going to|Let me|Now (?:the|let me|I'll|I will|I)\b)/i;
const NOT_SHELL =
  /^(?:Read|View|Edit|Write|Create|Search|Glob|Grep|List|Fetch|Find)\b/;
const SHELL_FENCE = /^\s*(`{3,}|~{3,})\s*(?:bash|sh|shell|zsh|console)?\s*$/i;

const closes = (line: string, open: string) => {
  const t = line.trim();
  return t.length >= open.length && t === open[0].repeat(t.length);
};

/** A ```bash block that starts at line `i` with "$ command": the call and the line after the block; else null. */
function shellFence(
  lines: string[],
  i: number,
): { call: ToolCall; end: number } | null {
  const f = lines[i].match(SHELL_FENCE);
  if (!f) return null;
  const body: string[] = [];
  let j = i + 1;
  while (j < lines.length && !closes(lines[j], f[1])) body.push(lines[j++]);
  if (j >= lines.length) return null;
  const cmd = body.find((l) => l.trim());
  if (!cmd || !/^\s*\$ /.test(cmd)) return null;
  const out = body.slice(body.indexOf(cmd) + 1).filter((l) => l.trim()).length;
  // no output: a command for the person to run, not one that ran
  if (!out) return null;
  return {
    call: {
      title: cmd.replace(/^\s*\$\s+/, "").trim(),
      shell: true,
      lines: out || null,
    },
    end: j + 1,
  };
}

/** The tool-call log at the top of an answer and the answer after it; null when the answer does not start with one. */
export function splitToolLog(text: string): ToolLog | null {
  const lines = (text ?? "").replace(/\r\n?/g, "\n").split("\n");
  const calls: ToolCall[] = [];
  let i = 0;
  let end = 0; // the line after the last call's lines
  const nextIsCall = (j: number) => {
    while (j < lines.length && (!lines[j].trim() || NARRATION.test(lines[j])))
      j++;
    return (
      j < lines.length &&
      (START.test(lines[j]) || SEARCH.test(lines[j]) || !!shellFence(lines, j))
    );
  };
  while (i < lines.length) {
    const l = lines[i];
    const start = l.match(START) ?? l.match(SEARCH);
    const fence = start ? null : shellFence(lines, i);
    if (start) {
      const call: ToolCall = {
        title: start[1].replace(/\s*\(shell\)$/i, ""),
        shell: !SEARCH.test(l) && !NOT_SHELL.test(start[1]),
        lines: null,
      };
      if (/\(shell\)$/i.test(start[1])) call.shell = true;
      i++;
      while (i < lines.length && MORE.test(lines[i])) {
        const n = lines[i].match(COUNT);
        if (n) call.lines = Number(n[1]);
        if (/^\s*\$ /.test(lines[i])) call.shell = true;
        i++;
      }
      calls.push(call);
      end = i;
    } else if (fence) {
      calls.push(fence.call);
      i = end = fence.end;
    } else if (
      (!l.trim() || (calls.length && NARRATION.test(l))) &&
      nextIsCall(i)
    ) {
      i++;
    } else break;
  }
  if (!calls.length) return null;
  const rest = lines
    .slice(end)
    .join("\n")
    .replace(/^\s*\n/, "");
  if (!rest.trim()) return null; // only a log, no answer after it: show it as it is
  return { calls, log: lines.slice(0, end).join("\n").trim(), rest };
}

const cut = (t: string, n: number) =>
  t.length > n ? t.slice(0, n - 1).trimEnd() + "…" : t;

/** The folded line: "Ran 3 shell commands — read migrations, the repository, the test" and "151 + 45 + 4 lines". */
export function toolSummary(calls: ToolCall[]): {
  head: string;
  lines: string;
} {
  const n = calls.length;
  const what = calls.every((c) => c.shell)
    ? `shell command${n === 1 ? "" : "s"}`
    : `tool call${n === 1 ? "" : "s"}`;
  const titles =
    calls
      .slice(0, 3)
      .map((c) => cut(c.title, 48))
      .join(", ") + (n > 3 ? ", …" : "");
  const counts = calls.filter((c) => c.lines !== null).map((c) => c.lines);
  return {
    head: `Ran ${n} ${what} — ${titles}`,
    lines: counts.length ? `${counts.join(" + ")} lines` : "",
  };
}
