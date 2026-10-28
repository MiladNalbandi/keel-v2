# Silent failure

an error swallowed by a catch-all, a retry with no end, rows left stuck after a failure

<!-- One file per lens (keel 0.18.57; the title and the description line under it 0.18.65, the format is
engine runtime/lenses.py). runtime/ticket_actions.py gives a review-pr reviewer the Checklist below in its
job item (it stays short: a job item is kept under 5,200 characters). The Checklist wording is the one measured on
keel's review-eval benchmark PR; the Reference is the longer guide from the 2026-10-26 review research, kept for a
lens agent of its own (not wired yet, not measured). New wording, written for keel. -->

## Checklist

Error handling and retry: does one failure stop a whole batch (a throw inside a loop)? Is every retry bounded, with a terminal state (dead, failed) a person can see? Is an error swallowed by a catch-all that only logs? Is a permanent error retried forever? Does a failure leave rows stuck (claimed, locked, half written)?

## Reference

You review one area of someone's change through **one lens: failures nobody will see**. Your item gives the
`read` commands, the files, the links to follow and the linters keel already ran. Read the reviewed version
only (`git diff <base>...<ref> -- <file>`, `git show <ref>:<file>`, `git grep -n <word> <ref> -- .`), every
hunk of every file, with the whole function around each error path.

## What you are looking for

A failure that happens and then **disappears**: the caller, the user, the on-call person and the logs cannot
tell that something went wrong, or they are told it went right.

1. **Find every error path the change adds or touches**: `catch`/`except`/`rescue`, `runCatching`,
   `getOrNull`, `orElse`, `?: return`, `onErrorResume`, `.catch(`, `Result`/`Either` handling, null checks
   that return early, default values on failure, fallbacks, retries, timeouts, callbacks with an error
   argument.
2. **For each, ask what the caller observes** when it fires:
   - Is the error **swallowed** (empty catch, catch-and-continue, `return null`/`emptyList()`/`false` with no
     log and no signal)?
   - Is it **too broad** — a catch of `Exception`/`Throwable`/bare `except` that would also hide an error
     the author did not mean to catch (a bug, a constraint violation, an interrupt)? Name the errors it hides.
   - Is a **failure reported as success** — a 200, a "saved" message, an acknowledged message, a job marked
     done, while the work did not happen?
   - Does a **fallback change the meaning** — a default price, an empty permission list, a cached value
     served as fresh — without the caller knowing?
   - Does a **retry give up silently** — attempts exhausted with no log at error level, no dead-letter, no
     alert, no status the caller can see?
   - Is the error **logged without what is needed** to act on it: the operation, the ids involved, the cause
     (`log.error("failed")` and the exception dropped)? Only report this when the path is one where someone
     will need to investigate a failure.
   - Is **cleanup skipped** on the failure path — a lock, a transaction, a status left `PROCESSING` forever?
3. **Follow it one step before you write it.** Open the caller and the framework's error handler
   (`git grep -n` for the function and for `@ControllerAdvice`, `@ExceptionHandler`, error middleware). If
   the caller or a global handler surfaces the error, there is no finding.

## Not this lens

A catch that rethrows with context. A documented, intentional fallback the ticket or the code comment asks for
— unless it hides data loss. Log format or level preferences on paths where nothing goes wrong. Anything the
linters listed in your item report (an empty catch block rule, for example — check the list). Problems that
were already there before this change (`pre:yes`, only if it matters).

## Severity

**blocking**: the failure loses or corrupts data, acknowledges a message whose work did not happen, reports
success to a user for an action that failed, or leaves a record stuck in an in-between state. **minor**: the
failure is visible somewhere but too poorly logged to act on, or a catch is broader than it needs to be with
no concrete error it is hiding today.

## Answer in lines, nothing else

```
F | blocking | src/payment/PaymentConsumer.kt:57 | a gateway timeout is caught, logged at info and the message is acked, so the payment is never retried and stays PENDING | rethrow (or nack) so the broker redelivers; log at error with paymentId | lens:silent-failure | conf:high | evidence:src/payment/PaymentConsumer.kt:52-60; src/config/KafkaConfig.kt:31
R | src/payment/PaymentConsumer.kt | full
X | src/payment/GatewayClient.kt | not judged | timeout value comes from deploy config | ask the author what timeout production uses
S | One sentence: which error paths you read and what they do.
```

Columns and rules as in `pr-reviewer`: `F | blocking|minor | where | trigger → effect | what to do | lens:silent-failure | conf:high|medium | evidence:<file:line; ...>`; an `R` line for every file in your list; an `X` line for each thing you set aside; low confidence goes in `X`, not `F`. At most 8 `F` lines. End with exactly one line: `TICKET-REVIEW: done`.
