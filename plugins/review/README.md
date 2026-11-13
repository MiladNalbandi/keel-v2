# Code Review

Review the branch you are on, or any pull request (GitHub) or merge request (GitLab), in keel: files, diff, threads,
line comments and one review you send in one go. keel explains the change, finds problems and checks review threads.
**keel never posts by itself**: a reply, a resolve or a review goes out only when you press the button.

This page is about the threads: **✦ Is the reviewer right?** (one thread) and **Check all open** (every open thread).

## Who is right

keel's check answers point by point. Each point names the role and the person, the same words in the classic Threads
panel, the new Comments tab and a follow-up question to KeelBot (`web/verdict.ts`):

| verdict | keel says |
| --- | --- |
| `reviewer_right` | Reviewer (ana) is right |
| `author_right` | Author (you) is right |
| `partly` | Reviewer (ana) is partly right |
| `unclear` | Unclear: keel could not decide |

The reviewer is the first one in the thread who did not write the change; "you" is the person signed in.

## The same point in two threads

Two reviewers often say the same thing on lines close together. keel treats open threads as one point when they are in
one file and on one side, at most 10 lines apart, and their first comments share two telling words or more (at least a
third of the shorter one's).

```
 bo · line 3   "The page size cache is never cleared"  ─┐
                                                        ├─▶ one check ─▶ the same answer on both
 alex · line 4 "This cache grows and is never cleared" ─┘      "Same point as the thread on line 4 (alex)"
```

- **Check all open (N)** counts points, not threads: one model call for all of them, one section per point.
- **✦ Is the reviewer right?** on one of them checks the point once, for all of its threads.
- Each answer links the other threads: **Same point as the thread on line N (bo)** shows that thread.
- **Use as my reply on both** puts keel's reply into each thread's reply box. You send each one.
- keel never merges threads on GitHub or GitLab: they stay as they are.

## What keel reads

A check is one cheap model call with no tools, so keel puts the code in front of it. Before, that was the thread's own
lines (±30) and its diff hunk only, and a thread that said "covered by test_refunds" came back unclear. Now it also
reads, from the head commit:

| what | example |
| --- | --- |
| the files the comments name | `shop/money.py`, `refunds.py:20` (the lines around 20) |
| the tests the author points to | "covered by test_refunds": `tests/test_refunds.py`, else the test that declares `test_refunds` |
| the test paired with the thread's file | `refunds.py` ↔ `test_refunds.py` / `refunds_test.py`, `Prefs.kt` ↔ `PrefsTest.kt` |

At most 4 files with the thread's own, 60 lines of each other file and 150 in all, and the check's token cap (Settings ›
Code Review threads) still holds: over the cap, keel reads fewer lines, then no other file. The answer says what it read
("read 3 files"; the files are in its tooltip). Check all reads them too, with fewer lines.

## Fixed → Resolve

When the author answers "fixed" (or "done", "addressed") and pushes, keel checks the point again against the new code:
one cheap call, in the background, when the review is opened or the reply is sent. keel **never resolves** by itself.

```
 bo: "Read the page size from config"   ana: "Fixed."   ana pushes abc1234
                                                              │
                                    keel reads the new code ◀─┘  (once per thread, reply and head commit)
                                       │                     │
                              fixed    ▼                     ▼  not fixed
              "Fixed in abc1234 · Resolve"         "Still open: the max is still hard-coded"
                     │ your click
                     ▼
              the thread is resolved on the host
```

- It runs only for an open thread whose last comment is the author's "fixed / done / addressed" and only when the head
  has a commit made after the point (the last comment before the reply). No new commit: nothing is checked or shown.
- Both views show it under the thread; the new view's pill says "Fixed in abc1234" too. Resolve is the same button as
  the thread's own Resolve.
- When the head moves on again, the old answer is hidden and the new code is checked.
- Only in quick mode (Settings › Code Review threads), under the same token cap.
