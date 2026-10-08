// Code Review in the launcher (⌘K): the project's pull requests (when the review plugin is on), and the ones that
// ask for your review on the empty launcher. Registered by ./register.tsx (slot launcher.source).

import { api, errorParts } from "../../api";
import {
  askAction,
  copyAction,
  goHash,
  linkAction,
  type Ctx,
} from "../launcher/sources";
import type { Item } from "../launcher/model";
import { reviewHash } from "../../pages/repo/model";
import { reviewApi, type PrSummary } from "../../reviewApi";
import type { LauncherSourceItem } from "../../sdk/slots";

type Prs = { prs: PrSummary[]; host: { kind: string; web: string } | null };

function prItem(ctx: Ctx, p: PrSummary, host: string | null): Item {
  const key = `pr:${p.number}`;
  const role = p.mine
    ? "yours"
    : p.review_requested
      ? "you review"
      : p.assigned
        ? "assigned to you"
        : p.author;
  const where = host === "gitlab" ? "GitLab" : "GitHub";
  return {
    id: key,
    kind: "pr",
    title: `#${p.number} ${p.title}`,
    sub: `${p.mine ? "" : p.author + " · "}${role === p.author ? p.branch : role}${p.draft ? " · draft" : ""}`,
    actions: [
      {
        id: "open",
        label: "Open the review",
        keys: "enter",
        run: goHash(ctx, reviewHash(key)),
      },
      askAction(
        ctx,
        `What does pull request #${p.number} "${p.title}" (branch ${p.branch}) change, and what should I check first?`,
        "Ask KeelBot what it changes",
      ),
      {
        id: "checkout",
        label: "Check out the branch",
        keys: "meta+shift+o",
        confirm: `Check out the branch of #${p.number} (${p.branch})? The files in your project folder change to it.`,
        run: async () => {
          try {
            const r = await reviewApi.checkout(ctx.pid!, key);
            ctx.toast(r.note || `On ${r.branch} now.`);
            ctx.close();
          } catch (e) {
            ctx.toast(`Not checked out: ${errorParts(e).message}`);
          }
        },
      },
      ...(p.url
        ? [
            copyAction(ctx, p.url, "Copy the link"),
            linkAction(`Open on ${where}`, p.url),
          ]
        : []),
    ],
    ask: `What does pull request #${p.number} "${p.title}" (branch ${p.branch}) change, and what should I check first?`,
    copy: p.url || `#${p.number}`,
    preview: {
      kind: "text",
      title: `#${p.number} ${p.title}`,
      lines: [
        ["Author", p.mine ? `${p.author} (you)` : p.author],
        ["Branch", `${p.branch} → ${p.base}`],
        [
          "You",
          p.mine
            ? "wrote it: you can merge it"
            : p.review_requested
              ? "are asked to review"
              : p.assigned
                ? "are assigned"
                : "are not asked",
        ],
        ...(p.draft ? [["State", "draft"] as [string, string]] : []),
      ],
    },
  };
}


export const reviewLauncher: LauncherSourceItem = {
  id: "review",
  title: "Pull requests",
  order: 10,
  load: async (pid, notes) => {
    let out: Prs | null = null;
    await api
      .plugins(pid)
      .then(async (plugins) => {
        if (!plugins.find((p) => p.name === "review")?.enabled) return;
        const list = await reviewApi.prs(pid, "all");
        out = {
          prs: list.prs,
          host: list.host ? { kind: list.host.kind, web: list.host.web } : null,
        };
        if (list.note) notes.add(`Pull requests: ${list.note}`);
      }, notes.failed("Plugins"))
      .catch(notes.failed("Pull requests"));
    return out;
  },
  items: (ctx, data, q) => {
    const d = data as Prs | null;
    return q.kinds.includes("pr")
      ? (d?.prs ?? []).map((p) => prItem(ctx, p, d?.host?.kind ?? null))
      : [];
  },
  waiting: (ctx, data) => {
    const d = data as Prs | null;
    return (d?.prs ?? [])
      .filter((p) => p.review_requested && !p.mine)
      .slice(0, 3)
      .map((p) => prItem(ctx, p, d?.host?.kind ?? null));
  },
};
