# Journeys

What this project does for the people who use it, end to end. Everything else in this knowledge
base answers *what is where*; this answers *what happens, in what order, and where it stops*.

It is the one section keel cannot derive. A call graph shows which class calls which; it cannot
show that placing an order and charging for it are one piece of business, or that the charge is
allowed to happen later. Until this is written, the map falls back to reading the specs — one step
per acceptance criterion — which is honest but only as good as the last spec anyone wrote.

> Claims here carry a backticked `path:line`; every one must resolve.

## {{JOURNEY}}

<One line: who starts it, and what they have at the end.>

| # | Step | Who or what | Where it happens | Notes |
|---|---|---|---|---|
| 1 | <browses the catalogue> | <a person, no account needed> | `{{ENTRY_POINT}}` | <nothing is written> |
| 2 | <places the order> | <a signed-in customer> | `{{USE_CASE}}` | <the rule that actually decides> |
| 3 | <is told it worked> | <a worker, off the request> | `{{CONSUMER}}` | <nobody waits for this> |

<The three things worth writing under a journey, because none of them is visible in the code:>

- **Where it can stop.** Which steps are allowed to fail without the whole journey failing, and
  what the person sees when they do.
- **What happens later.** Any step that completes after the request has already answered — a queue
  message, a scheduled job, a webhook coming back.
- **The branch nobody draws.** The cancel, the refund, the retry. A journey with only its happy
  path written down is the one that surprises people.

## Journeys this project does not have

<Say what deliberately does not exist — no self-service signup, no bulk import — so nobody spends
an afternoon looking for it. An absence nobody recorded is an absence somebody will re-derive.>
