# Review a pull request

Read someone's pull request in Code › Review with keel's help, or let the review-pr flow check it against a ticket.

## Before you start

1. Turn on the **Code Review** plugin: **Settings › Plugins › Installed**, its **This project** switch.
2. Connect GitHub: **Settings › Connections › GitHub › Sign in with GitHub**. Open github.com/login/device and type
   the one-time code keel shows. GitLab works too.

A **pull request** (PR) is a request to merge one branch into another. On GitLab it is a merge request.

## Code › Review

Open **Code › Review**. You see the pull requests: to review, assigned to you, and your own. Open one to see:

- its files and a side-by-side diff,
- its CI checks,
- the threads and your line comments (also over several lines),
- **Approve** and **Request changes**.

Your comments are drafts first. You send them as one review, in one go. keel only reads the pull request's code:
it never builds or runs it. **Nothing is posted until you press Submit.**

## Let keel help

- **Explain**: keel says what the change does, so it is easy to review.
- **Find problems**: reviewers read the change, a checker checks each finding again, and you see only the
  confirmed findings. Each confirmed finding has **Fix it**. Set the reviewers in
  [Settings › Review](#/help/settings-review).
- **✦ Is the reviewer right?** on a comment: keel checks the comment against the code and answers point by point
  (they're right, you're right, partly, unclear), with a draft reply. Nothing is posted for you.
- **✦ Address it** on a thread: keel starts a small flow on the pull request's branch to make the change. keel never
  pushes.

```text
 comment on a thread ─► ✦ Is the reviewer right? ─► draft reply (you post it, or not)
                    └─► ✦ Address it ─► address flow ─◆ your one look ─► one commit (no push)
```

## The review-pr flow

Use **review-pr** for someone else's PR or branch, checked against a ticket and your definition of done.

```text
 PR + ticket ─ build and lint in a throw-away copy ─ each checklist item: met / not met, with proof
            ─ bugs checked twice ─◆ you pick what the author sees ─ posted only if you choose post
```

- The build and lint run in a **throwaway worktree**: a separate copy of the project that keel deletes after.
- Each item of the checklist is marked **met** or **not met**, with evidence.
- You pick what the author sees. Nothing is posted unless you choose **post**.

Start it in **Flow › Start a flow** and pick **review-pr**. From an AI tool, `keel_start_flow` lists every flow and
its inputs (for review-pr: a ticket or a pr).

## Review your own branch

The **review** flow reads your branch, with one reviewer per lens (correctness, security, performance,
architecture, assertions). It changes nothing. You get the report word for word.

## In the terminal

With the Code Review plugin on, keel TUI has a review mode:

```
/review          the branch you are on
/review 12       pull request #12
/review prs      the open pull requests
```

It takes the whole screen until **Esc**. Keys: **↑↓** and **Enter** open a file's diff, **]** **[** next file,
**}** **{** next change, **c** a line comment, **r** reply, **x** resolve, **v** viewed, **e** / **f** ask keel to
explain or find problems, **s** submit (keel asks first), **p** another pull request, **?** all keys.

## From an AI tool (MCP)

- Read: `keel_review_prs`, `keel_review_files`, `keel_review_diff`, `keel_review_notes`.
- With `--write`: `keel_review_comment`, `keel_review_explain`, `keel_review_findings`, `keel_review_reply`,
  `keel_review_resolve`, `keel_review_submit`. The last three post to GitHub or GitLab, so your tool asks you first.

See [Connect an AI tool (MCP)](#/help/connect-an-ai-tool).
