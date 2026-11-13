// keel 0.18.9 KeelBot's thinking effort: the effort choices of a model (keel's catalog), how a model reads on screen
// (claude/sonnet · high), and /model and /effort, which pick KeelBot's model and its effort.

import { api, type Catalog, type Mode, type Model, type SavedMode } from "./sdk";
import type { Choice, Ctx, Reply } from "./commands";

/** Effort choices: the model's own list, else the provider's. Empty = this model has no effort setting (keel ignores
 *  one). The rule of web/src/components/ModelPicker.tsx's effortsOf, without its React. */
export function effortsOf(c: Catalog | null, p: string, mode: SavedMode, model: string): string[] {
  const e = c?.[p];
  if (!e) return [];
  // #101 a model saved for a coding-agent app (subscription, opencode) runs on the API: its list is the API's
  const list = (mode && e.modes[mode as Mode]) || e.modes[e.default.mode] || Object.values(e.modes)[0] || [];
  return list.find((x) => x.id === model)?.efforts ?? e.efforts ?? [];
}

/** claude/sonnet, or claude/sonnet · high when an effort is set. */
export const modelText = (m: { provider?: string | null; model?: string | null; effort?: string | null }): string =>
  `${m.provider ?? "?"}/${m.model ?? "?"}${m.effort ? ` · ${m.effort}` : ""}`;

/** 0.18.23 a model's name as people say it ("Claude Sonnet 5.5"): its label in keel's catalog, when it was read, else
 *  provider/model. */
export function modelName(m: { provider?: string | null; model?: string | null }): string {
  for (const list of Object.values(known?.[m.provider ?? ""]?.modes ?? {})) {
    const label = (list ?? []).find((x) => x.id === m.model)?.label;
    if (label) return label;
  }
  return `${m.provider ?? "?"}/${m.model ?? "?"}`;
}

const bare = (m: Model): Model => ({ provider: m.provider, mode: m.mode, model: m.model });
const ok = (t: string): Reply => ({ blocks: [{ kind: "ok", text: t }] });
const said = (t: string): Reply => ({ blocks: [{ kind: "text", text: t }] });
const fail = (t: string): Reply => ({ blocks: [{ kind: "error", text: t }] });

// the last catalog read, for /effort's completions (they cannot wait for keel)
let known: Catalog | null = null;
let cached: { at: number; data: Promise<Catalog> } | null = null;
/** keel's model catalog, read once and kept for 5 minutes (it asks every provider: slow); keel TUI starts reading it
 *  at once, so /model opens fast (0.18.9). */
export function modelCatalog(): Promise<Catalog> {
  if (!cached || Date.now() - cached.at > 300_000) {
    const data = api.providerModels().then((c) => ((c ?? {}) as Catalog)).catch((e) => {
      cached = null;
      throw e;
    });
    cached = { at: Date.now(), data };
  }
  return cached.data;
}
async function catalog(): Promise<Catalog> {
  known = await modelCatalog();
  return known;
}

/** KeelBot's model now: the one picked here, else the open chat's; null = keel's default (none picked yet). */
const current = (ctx: Ctx): Model | null => ctx.core.snapshot.chat.model ?? ctx.core.snapshot.chat.session?.model ?? null;

async function use(ctx: Ctx, m: Model): Promise<Reply> {
  await ctx.core.setChatModel(m);
  return ok(`KeelBot answers with ${modelText(m)} (${m.mode}).`);
}

/** The pick of `m`'s efforts: "default" first (the engine's own), the cursor on the current one. */
function effortPick(ctx: Ctx, m: Model, efforts: string[]): Reply {
  const values = ["default", ...efforts];
  const index = Math.max(0, values.indexOf(m.effort ?? "default"));
  return { blocks: [], pick: { title: `Thinking effort for ${m.provider}/${m.model}`, index,
    choices: values.map((v) => ({ label: v, value: v, hint: v === "default" ? "the model's own" : undefined })),
    then: (v) => use(ctx, v === "default" ? bare(m) : { ...bare(m), effort: v }) } };
}

/** /model: KeelBot's model from keel's catalog, then its thinking effort when the model has one. */
export async function modelRun(ctx: Ctx): Promise<Reply> {
  const cat = await catalog();
  const now = current(ctx);
  const choices: Choice[] = [];
  for (const [prov, c] of Object.entries(cat)) {
    for (const [mode, models] of Object.entries(c?.modes ?? {})) {
      for (const m of models ?? []) choices.push({ label: `${prov}/${m.id}`, value: JSON.stringify({ provider: prov, mode, model: m.id }), hint: `${c.label ?? prov} · ${mode} · ${m.label}` });
    }
  }
  if (!choices.length) return fail("keel's model catalog is empty.");
  const index = now ? Math.max(0, choices.findIndex((c) => c.value === JSON.stringify(bare(now)))) : 0;
  return { blocks: [], pick: { title: `KeelBot's model${now ? ` (now ${modelText(now)})` : ""}`, choices, index, then: async (v) => {
    const m = JSON.parse(v) as Model;
    const efforts = effortsOf(cat, m.provider, m.mode, m.model);
    if (!efforts.length) return use(ctx, m);
    // the same model again keeps its effort as the cursor's start
    const was = now && now.provider === m.provider && now.mode === m.mode && now.model === m.model ? now.effort : undefined;
    return effortPick(ctx, { ...m, ...(was ? { effort: was } : {}) }, efforts);
  } } };
}

/** /effort [level]: KeelBot's thinking effort on its current model; "default" clears it. */
export async function effortRun(ctx: Ctx, args: string): Promise<Reply> {
  const now = current(ctx);
  if (!now) return said("KeelBot has no model picked yet: /model picks one, then /effort its thinking effort.");
  const efforts = effortsOf(await catalog(), now.provider, now.mode, now.model);
  const name = `${now.provider}/${now.model}`;
  if (!efforts.length) return said(`${name} has no effort setting: keel ignores it.`);
  const want = args.trim().toLowerCase();
  if (!want) return effortPick(ctx, now, efforts);
  if (want === "default") return use(ctx, bare(now));
  if (!efforts.includes(want)) return fail(`${name} has no effort "${want}". Its choices: default, ${efforts.join(", ")}.`);
  return use(ctx, { ...bare(now), effort: want });
}

export function effortComplete(ctx: Ctx, arg: string): string[] {
  const now = current(ctx);
  const efforts = now ? effortsOf(known, now.provider, now.mode, now.model) : [];
  return ["default", ...efforts].filter((x) => x.startsWith(arg.toLowerCase()));
}
