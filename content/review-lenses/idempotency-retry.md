# Concurrency and idempotency

a message delivered twice, overlapping workers, check-then-act without a lock

<!-- One file per lens (keel 0.18.57; the title and the description line under it 0.18.65, the format is
engine runtime/lenses.py). runtime/ticket_actions.py gives a review-pr reviewer the Checklist below in its
job item (it stays short: a job item is kept under 5,200 characters). The Checklist wording is the one measured on
keel's review-eval benchmark PR; the Reference is the longer guide from the 2026-10-26 review research, kept for a
lens agent of its own (not wired yet, not measured). New wording, written for keel. -->

## Checklist

Concurrency and idempotency: what happens when a message is delivered twice, two workers or two cron runs overlap, or two requests race? Look for check-then-act without a lock or unique key, side effects (an external call, a booking, an e-mail) repeated on redelivery, a claim without SKIP LOCKED or a claim token, and status changes whose SQL WHERE clause does not enforce the allowed transitions and terminal states.

## Reference

You review one area of someone's change through **one lens: what happens when the same work arrives twice,
fails half way, or keeps failing**. Message brokers, schedulers, webhooks and HTTP clients all retry. Code that
is only correct when everything runs exactly once is wrong.

Use the `read` commands in your item only (`git diff <base>...<ref> -- <file>`, `git show <ref>:<file>`,
`git grep -n <word> <ref> -- .`). Read every consumer, job and handler in your area **whole**, then the
config that decides how it is retried (listener container factory, `@Retryable`, `RetryTemplate`, broker
or queue config in the repo, dead-letter settings, job scheduler settings), then the producer of what it
consumes (`git grep -n <topic or queue name> <ref>`). Load the `hunt` skill's `references/lenses.md` sections
`idempotency` and `messaging` for what a provable claim looks like there.

## First: what does the system promise?

Find the delivery guarantee: at-least-once (the default for Kafka, SQS, RabbitMQ with manual ack, most
webhooks and schedulers) or something stronger the code really enforces. Under at-least-once, a duplicate
delivery is normal and **applying it twice** is the defect. If nothing in the repo says, assume at-least-once
and write an `X` line that the guarantee is unstated.

## Questions

**Twice**

- If this message, job run or request is processed twice, what happens? A second row, a second email, a
  second charge, a counter incremented twice, a status moved backwards? What keys the work — an idempotency
  key, a unique constraint on a natural or message id, an upsert, a "processed messages" table — and is that
  check **in the same transaction** as the effect, or can two deliveries both pass it?
- A create endpoint: if the client times out and sends it again, is a second record made?

**Half way**

- Where does the handler commit, ack or update its offset relative to the work? Ack **before** the work →
  a crash loses the message. Ack **after** a database commit but with an external call (email, HTTP, another
  publish) in between → a retry repeats the external call. Is there an outbox, or is the publish inside the
  database transaction with no outbox (published even when the transaction rolls back)?
- Is a status set to `PROCESSING` before the work and only reset on success, so a crash leaves it stuck?

**Keeps failing**

- Which errors are retried and which are not? A validation error or a bad payload retried forever blocks the
  partition or queue behind it (a poison message). A transient error (timeout, 503, deadlock) **not** retried
  loses the work.
- What happens when retries run out: a dead-letter queue or table someone watches, an error log with the ids,
  a status the user can see — or nothing (the message is dropped, the job is marked done)?
- Is the retry bounded and spaced (backoff), or a tight loop that hammers a failing dependency?
- Does an exception thrown inside the handler reach the framework (so it retries), or is it caught and the
  message acknowledged anyway? (Overlaps `silent-failure`: report it once, here, when the cause is the
  retry/ack design.)

**Order and concurrency**

- Does correctness depend on order (an `updated` before its `created`, two updates to one entity on different
  partitions or consumers)? Are two consumers of the same entity able to run at once, with a read-modify-write
  and no version or lock?

## Follow it before you write it

Open the retry and ack config before claiming "it is not retried" or "it is acked first". If the config is
outside the repository, the finding is at most `conf:medium` and the `X` line names the setting to check.

## Not this lens

A naturally idempotent `PUT` or `DELETE`. A correct 409 or "already processed" answer on the second call —
the defect is a second **effect**, not a second response. Throughput or batch-size tuning.

## Severity

**blocking**: work lost (acked before done, dropped after retries, failure swallowed); a duplicate effect with
money, messages to people, inventory or any external call; a poison message that stops the queue; a stuck
in-between state with no way out. **minor**: a duplicate that only rewrites the same value; a missing backoff
on a path that fails rarely; logs on exhausted retries that lack the ids.

## Answer in lines, nothing else

```
F | blocking | src/order/OrderCreatedConsumer.kt:44 | the consumer inserts an invoice for every delivery; the key is a new UUID, so a redelivered orders.created makes a second invoice | key the insert on the order id (unique constraint + insert-if-absent) in the same transaction | lens:idempotency-retry | conf:high | evidence:src/order/OrderCreatedConsumer.kt:40-48; db/migration/V30__invoice.sql:5; src/config/KafkaConfig.kt:22
F | blocking | src/config/KafkaConfig.kt:31 | DefaultErrorHandler retries 3 times then logs at debug and commits the offset; a failed invoice is lost with no trace | add a DeadLetterPublishingRecoverer to orders.created.DLT and log at error with the order id | lens:idempotency-retry | conf:high | evidence:src/config/KafkaConfig.kt:28-35
R | src/order/OrderCreatedConsumer.kt | full
X | src/order/OrderCreatedConsumer.kt | not judged | whether orders.created is keyed by order id (ordering per order) is set by the producer service, not in this repo | ask the author which key the producer uses
S | One sentence: which handlers and which retry config you read.
```

Columns and rules as in `pr-reviewer`: `F | blocking|minor | where | trigger → effect | what to do | lens:idempotency-retry | conf:high|medium | evidence:<file:line; ...>`; an `R` line for every file in your list; an `X` line for each thing you set aside. At most 8 `F` lines. End with exactly one line: `TICKET-REVIEW: done`.
