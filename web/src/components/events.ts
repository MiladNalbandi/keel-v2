// Turn engine events into one short line for the Events panels.

import type { EngineEvent } from "../api";

const str = (v: unknown) => (v === undefined || v === null ? "" : String(v));

export function eventLine(ev: EngineEvent): { k: string; text: string } {
  const d = ev.data ?? {};
  switch (ev.type) {
    case "thread.started": return { k: "flow", text: "flow started" };
    case "step.started": return { k: "phase", text: `${ev.step ?? "step"} started` };
    case "step.finished": return { k: "phase", text: `${ev.step ?? "step"} finished${d.status ? " · " + str(d.status) : ""}` };
    case "helper.started":
    case "agent.started": return { k: "agent", text: `${str(d.agent)} start${d.model ? " · " + str(d.provider) + " " + str(d.model) : ""}${d.ac ? " · " + str(d.ac) : ""}` };
    case "helper.finished":
    case "agent.finished": return { k: "agent", text: `${str(d.agent) || "agent"} stop · ${str(d.status)}${d.result && typeof d.result === "string" ? " · " + d.result : ""}` };
    case "agent.step": return { k: str(d.kind) || "step", text: str(d.text).slice(0, 140) };
    case "gate.waiting": return { k: "gate", text: `${str(d.title) || "gate"} waits for you` };
    case "gate.decided": return { k: "gate", text: `${ev.step ?? "gate"} ${str(d.decision) === "reject" ? "sent back" : "approved"}${d.why ? ` — "${str(d.why)}"` : ""}` };
    case "budget.warn": return { k: "budget", text: str(d.text) || "flow is near its cap" };
    case "budget.stop": return { k: "budget", text: str(d.text) || "cap reached — flow paused" };
    case "guard.refused": return { k: "guard", text: `refused: ${str(d.text) || str(d.path) || "a write the phase does not allow"}` };
    case "helper.permission": return { k: "gate", text: `KeelBot asks to run: ${str(d.command).slice(0, 120)}` };
    case "helper.permission.answered": return { k: "gate", text: `KeelBot's command ${str(d.decision) === "deny" ? "refused" : "allowed"}${d.why ? ` — "${str(d.why)}"` : ""}` };
    case "helper.commit": return { k: "commit", text: `KeelBot's change committed: ${str(d.message)}` };
    case "thread.done": return { k: "flow", text: "flow done" };
    case "thread.failed": return { k: "flow", text: `flow failed${d.error ? ": " + str(d.error) : ""}` };
    default: return { k: "event", text: ev.type };
  }
}
