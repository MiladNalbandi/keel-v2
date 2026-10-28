# Error handling and retry

one failure stops a batch, a retry with no end, a catch-all that only logs

<!-- keel 0.18.65: the Review page's reviewer A went through this lens as a fixed text in ReviewAiService (LENSES_A);
it is a file now, so a project can turn it off or give it to another reviewer (Settings › Review). Format: engine
runtime/lenses.py. silent-failure.md is the longer, measured lens of the review-pr flow. -->

## Checklist

Error handling and retry: one failure that stops a batch (a throw in a loop), a retry without an end or a terminal failed state, a catch-all that only logs.
