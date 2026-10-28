// keel 0.18.65 a branch review's base (the stale-base field report, R3): the review compares against
// merge-base(<canonical remote>/<default branch>, HEAD); when the local base went its own way the panel says so, and the
// person can compare against the local base instead (or go back). keel init's setup commits are left out (R4).

import type { ReviewView } from "./reviewApi";
import * as R from "./store";

/** The warning and the "Compare with" pick of a branch review; nothing for a pull request or a base that agrees. */
export function BaseLine({ pid, view, className = "rv-warn" }: { pid: string; view: ReviewView; className?: string }) {
  const options = view.base_options ?? [];
  if (view.kind !== "branch" || (!view.base_warning && options.length < 2)) return null;
  const busy = (R.review(pid, view.key).running ?? []).includes("base");
  return (
    <div className={`rv-base ${className}`} role="note" data-testid="review-base">
      {view.base_warning && <span data-testid="review-base-warning">{view.base_warning}</span>}
      {options.length > 1 && (
        <label className="rv-base-pick">
          Compare with{" "}
          <select
            aria-label="Compare with"
            value={view.base_choice ?? options[0].id}
            disabled={busy}
            onChange={(e) => void R.pickBase(pid, view.key, e.target.value as "remote" | "local")}
          >
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label} {o.id === "remote" ? "(the remote's)" : "(your local base)"}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
}
