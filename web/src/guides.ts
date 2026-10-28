// keel 0.18.66 Help's short guides: docs/guides/*.md, bundled into the web app (the Dockerfile's web stage copies the
// folder in), so they work without the network and match this keel's version. docs/GUIDE.md stays the long reference
// (on GitHub, at this keel's version). Each file: "# Title", a blank line, one summary sentence, then "## " sections.

const FILES = import.meta.glob<string>("../../docs/guides/*.md", { query: "?raw", import: "default", eager: true });

export type Guide = { id: string; title: string; summary: string; text: string };

/** The order of the list; a guide not named here goes after them, by title. */
const ORDER = [
  "answer-a-gate", "phone", "models-per-step", "review-a-pull-request", "ci-before-ship", "rules",
  "keel-tui", "budgets-and-caps", "connect-an-ai-tool", "settings-review", "supervisor", "pick-a-flow", "troubleshooting",
];

/** A guide from its file: the title from its first line, the summary from the first paragraph after it. */
export function parseGuide(id: string, raw: string): Guide {
  const text = raw.replace(/\r\n/g, "\n").trim();
  const lines = text.split("\n");
  const title = lines[0]?.startsWith("# ") ? lines[0].slice(2).trim() : id;
  const rest = lines[0]?.startsWith("# ") ? lines.slice(1) : lines;
  const summary = rest.join("\n").trim().split(/\n\s*\n/)[0]?.replace(/\s+/g, " ").trim() ?? "";
  return { id, title, summary: summary.startsWith("#") ? "" : summary, text };
}

const rank = (id: string) => {
  const i = ORDER.indexOf(id);
  return i < 0 ? ORDER.length : i;
};

export const GUIDES: Guide[] = Object.entries(FILES)
  .map(([path, raw]) => parseGuide(path.replace(/^.*\//, "").replace(/\.md$/, ""), raw))
  .sort((a, b) => rank(a.id) - rank(b.id) || a.title.localeCompare(b.title));

export const guideById = (id: string): Guide | undefined => GUIDES.find((g) => g.id === id);

/** One search hit: the guide and a short piece of text around the first match (empty when the title matched). */
export type GuideHit = { guide: Guide; snippet: string };

/** Search title and text: every word must be there (case does not matter); a title match comes first. */
export function searchGuides(query: string, guides: Guide[] = GUIDES): GuideHit[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const hits: (GuideHit & { score: number })[] = [];
  for (const g of guides) {
    const title = g.title.toLowerCase();
    const body = g.text.toLowerCase();
    if (!words.every((w) => title.includes(w) || body.includes(w))) continue;
    const inTitle = words.filter((w) => title.includes(w)).length;
    const at = body.indexOf(words.find((w) => !title.includes(w)) ?? words[0]);
    hits.push({ guide: g, snippet: snippet(g.text, at), score: inTitle * 100 - Math.min(at < 0 ? 99 : at, 99) / 100 });
  }
  return hits.sort((a, b) => b.score - a.score).map(({ guide, snippet: s }) => ({ guide, snippet: s }));
}

function snippet(text: string, at: number): string {
  if (at < 0) return "";
  const from = Math.max(0, at - 50);
  const piece = text.slice(from, at + 90).replace(/[#*`|>]/g, "").replace(/\s+/g, " ").trim();
  return `${from > 0 ? "…" : ""}${piece}…`;
}

/** The long guide on GitHub, at this keel's version (main for a build without one). */
export function longGuideUrl(version?: string | null): string {
  const v = (version ?? "").replace(/^v/, "");
  const ref = /^\d+\.\d+\.\d+/.test(v) ? `v${v}` : "main";
  return `https://github.com/MiladNalbandi/keel-v2/blob/${ref}/docs/GUIDE.md`;
}
