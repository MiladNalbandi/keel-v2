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
