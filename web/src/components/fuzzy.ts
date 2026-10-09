// Fuzzy file matching (keel's core, shared through @keel/web-sdk): the Code page's quick open (⌘P) and its files in the
// launcher (⌘K), and KeelBot's @ mentions rank a project's files with it. Kept in core so a part can use it without the
// Code page. No React, so it is easy to test.

export type Fuzzy = { score: number; hits: number[] };

const isBoundary = (s: string, i: number) =>
  i === 0 || "/._- ".includes(s[i - 1]) || (s[i] >= "A" && s[i] <= "Z" && s[i - 1] >= "a" && s[i - 1] <= "z");

function subseq(path: string, q: string, from: number): Fuzzy | null {
  const low = path.toLowerCase();
  const hits: number[] = [];
  let score = 0;
  let at = from;
  for (const ch of q) {
    const i = low.indexOf(ch, at);
    if (i < 0) return null;
    const prev = hits[hits.length - 1];
    score += 1;
    if (prev !== undefined && i === prev + 1) score += 5;
    if (isBoundary(path, i)) score += 4;
    hits.push(i);
    at = i + 1;
  }
  return { score, hits };
}

/**
 * Match `query` against a path: the letters in order, anywhere. Matches in the file name, in a row and at word
 * starts score higher; shorter paths win ties. Null = no match.
 */
export function fuzzy(query: string, path: string): Fuzzy | null {
  const q = query.replace(/\s+/g, "").toLowerCase();
  if (!q) return { score: 0, hits: [] };
  const nameAt = path.lastIndexOf("/") + 1;
  const inName = q.includes("/") ? null : subseq(path, q, nameAt);
  const whole = subseq(path, q, 0);
  let best = inName ? { score: inName.score + 10 + (path.toLowerCase().startsWith(q, nameAt) ? 20 : 0), hits: inName.hits } : whole;
  if (inName && whole && whole.score > best!.score) best = whole;
  if (!best) return null;
  return { score: best.score - path.length / 100, hits: best.hits };
}

export function rankFiles(files: string[], query: string, limit = 50): { path: string; hits: number[] }[] {
  if (!query.trim()) return files.slice(0, limit).map((path) => ({ path, hits: [] }));
  const out: { path: string; hits: number[]; score: number }[] = [];
  for (const path of files) {
    const f = fuzzy(query, path);
    if (f) out.push({ path, hits: f.hits, score: f.score });
  }
  out.sort((a, b) => b.score - a.score || a.path.length - b.path.length);
  return out.slice(0, limit);
}
