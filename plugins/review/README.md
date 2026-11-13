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
