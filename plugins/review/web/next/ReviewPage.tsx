// keel 0.18.62 the new Review view as its own page in the menu (#/review, #/review/pr:42): the pull requests on the
// left (NextSide, without the Classic | New view switch) and the one you picked on the right (NextReview). The classic
// view stays in Code › Review as it was.
//
//   Review                                               [Refresh] [Review #42 ⇧N]
//   2 reviews are asked of you · 1 pull request of yours is open
//   ┌ This branch ────────┐ ┌ #42  open  checks pass        Open on GitHub ↗ [Submit] ┐
//   (All 4) (To review 2) …  │ Fix the cart total                                     │
//   │ #42 Fix the cart …  │ └────────────────────────────────────────────────────────┘
//   │ #41 Euro prices     │   Comments · Files · Problems · Commits · Checklist
//
// On a phone one at a time: the list, then the pull request with "‹ All pull requests" back to the list.
//
// #248 it reads the project's plugins first: while Code Review is off for this project it says so (with the button
// that turns it on here) and asks nothing of the review api, whose answer would be a 409 in the console.

import { useCallback, useEffect, useState } from "react";
import {
  ErrorBox,
  get,
  go,
  Loading,
  useLoad,
  useRoute,
  type CodeOpen,
  type PageProps,
} from "@keel/web-sdk";
import { NextReview } from "./NextReview";
import { NextSide } from "./NextSide";

const PHONE = "(max-width: 720px)";

/** True on a phone-wide screen; it follows the window. */
function usePhone(): boolean {
  const [phone, setPhone] = useState(() => !!window.matchMedia?.(PHONE).matches);
  useEffect(() => {
    const m = window.matchMedia?.(PHONE);
    if (!m) return;
    const on = () => setPhone(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return phone;
}

/** The review key of a link's rest: "pr:42" or "branch:feat/x"; null for anything else. */
export const reviewArg = (arg?: string): string | null =>
  arg && /^(pr:\d+|branch:.+)$/.test(arg) ? arg : null;

/** The message keel's api gives when Code Review is off (Plugins.require): ErrorBox offers to turn it on for it. */
export const REVIEW_OFF = {
  message: "The Code Review plugin is off for this project",
  hint: "Turn it on here, or in Settings › Plugins.",
};

/** Is Code Review on for this project? null while keel reads it. The answer names its project: in the one render
 *  after the project changes, the last project's answer must not draw the list (it would ask the api for this one). */
function useReviewOn(pid: string) {
  const plugins = useLoad(
    pid ? `review-plugins:${pid}` : null,
    async () => ({
      pid,
      list: await get<{ name: string; enabled: boolean }[]>(
        `/projects/${encodeURIComponent(pid)}/plugins`,
      ),
    }),
    { live: false },
  );
  const mine = plugins.data?.pid === pid ? plugins.data : null;
  const on = mine ? !!mine.list.find((p) => p.name === "review")?.enabled : null;
  return { on, error: plugins.error, reload: plugins.reload };
}

export function ReviewPage({ pid }: PageProps) {
  const { arg } = useRoute();
  const review = useReviewOn(pid);
  const [key, setKey] = useState<string | null>(() => reviewArg(arg));
  const phone = usePhone();
  // the link decides (back and forward, the launcher): #/review/pr:42 shows #42
  useEffect(() => setKey(reviewArg(arg)), [arg]);
  // NextSide opens a review the way the Code page opens an editor tab ("pr:42|"): here it is the page's link
  const open = useCallback<CodeOpen>((ref) => {
    const k = reviewArg(ref.path.split("|")[0]);
    if (!k) return;
    setKey(k);
    go("review", k);
  }, []);
  if (!pid)
    return (
      <p className="rvn-dim rvn-page-empty">
        Pick a project to see its pull requests.
      </p>
    );
  // an older keel without the plugins route: the page as before (the list says what is wrong)
  if (review.on === null && !review.error) return <Loading what="Reading the project's plugins" />;
  if (review.on === false)
    return (
      <div className="rvn-page-empty">
        <ErrorBox error={REVIEW_OFF} onRetry={() => void review.reload()} />
      </div>
    );
  const main = (
    <main className="rvn-page-main rise r2" aria-label="Pull request">
      {phone && key && (
        <button
          type="button"
          className="rvn-btn rvn-page-back"
          onClick={() => {
            setKey(null);
            go("review");
          }}
        >
          ‹ All pull requests
        </button>
      )}
      {key ? (
        <NextReview key={key} pid={pid} reviewKey={key} />
      ) : phone ? null : (
        <p className="rvn-dim rvn-page-empty">
          Pick a pull request on the left, or press “Review #…” for the next one that asks you.
        </p>
      )}
    </main>
  );
  // the page head, the list and this column (0.28: NextSide draws the page; on a phone the list or the pull request)
  return (
    <NextSide
      pid={pid}
      open={open}
      activeKey={key}
      page={{ main, showList: !(phone && key), phone }}
    />
  );
}
