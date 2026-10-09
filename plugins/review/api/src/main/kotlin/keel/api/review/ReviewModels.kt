package keel.api.review

import keel.api.repo.Commit

/** Where a project's code lives: github (github.com or an Enterprise host) or gitlab, and the repo's path there. */
data class HostRef(
    val kind: String,
    val host: String,
    /** owner/repo on GitHub, group/sub/project on GitLab */
    val path: String,
    /** https://<host>/<path>: links to pull requests */
    val web: String,
)

data class PrSummary(
    val number: Int,
    val title: String,
    val author: String,
    val branch: String,
    val base: String,
    val draft: Boolean,
    val updatedAt: String,
    val url: String,
    /** you are one of the reviewers asked */
    val reviewRequested: Boolean,
    /** you opened it */
    val mine: Boolean,
    val headSha: String?,
    /** you are one of its assignees */
    val assigned: Boolean = false,
)

data class PrList(
    val host: HostRef?,
    val me: String?,
    val prs: List<PrSummary>,
    val counts: Map<String, Int>,
    /** why the list is empty or short: no token, not a GitHub repo, ... */
    val note: String?,
)

data class PrDetail(
    val number: Int,
    val title: String,
    val author: String,
    val body: String,
    val branch: String,
    val base: String,
    val headSha: String,
    val state: String,
    val draft: Boolean,
    val url: String,
    /** the branch is in this repo (not a fork): it can be checked out under its own name */
    val sameRepo: Boolean,
    /** the host says it can be merged now (null: it does not know yet) */
    val mergeable: Boolean? = null,
    /** the host's word for it: clean, blocked, behind, dirty (GitHub), mergeable, ci_must_pass, conflict… (GitLab) */
    val mergeState: String? = null,
)

data class CheckRun(val name: String, val state: String, val url: String?)

data class ReviewComment(val id: String, val author: String, val body: String, val at: String, val url: String?)

/** A discussion on the code. `replyTo` is what a reply needs (GitHub: the first comment's id; GitLab: the discussion). */
data class ReviewThread(
    val id: String,
    val path: String?,
    val line: Int?,
    val side: String,
    val resolved: Boolean,
    val outdated: Boolean,
    val comments: List<ReviewComment>,
    val replyTo: String?,
)

data class Decisions(val approved: List<String>, val changes: List<String>)

data class ChangedFile(val path: String, val status: String, val from: String?, val added: Int, val removed: Int, val binary: Boolean)

data class Draft(
    val id: String,
    val path: String?,
    val line: Int?,
    val side: String,
    val body: String,
    val findingId: String?,
    val createdAt: String,
)

/** One review tab: a pull request (`pr:12`) or a local branch (`branch:feat/x`) against its base. */
data class ReviewView(
    val key: String,
    val kind: String,
    val number: Int?,
    val title: String,
    val author: String?,
    val body: String?,
    val base: String,
    val branch: String,
    val baseSha: String,
    val headSha: String,
    val url: String?,
    val state: String?,
    val draft: Boolean,
    val sameRepo: Boolean,
    val checks: List<CheckRun>,
    val approved: List<String>,
    val changesRequested: List<String>,
    val files: List<ChangedFile>,
    val added: Int,
    val removed: Int,
    val commits: List<Commit>,
    val threads: List<ReviewThread>,
    val conversation: List<ReviewComment>,
    val drafts: List<Draft>,
    val viewed: List<String>,
    /** comments, approve and threads need a pull request on a host keel has a token for */
    val canPost: Boolean,
    val host: HostRef?,
    val me: String?,
    val notes: List<String>,
    /** you opened it: only then keel offers Merge */
    val mine: Boolean = false,
    val mergeable: Boolean? = null,
    val mergeState: String? = null,
)

/** The branch the project folder is on, against its base: Review › This branch. */
data class BranchSummary(
    val branch: String?,
    val base: String?,
    val ahead: Int,
    val files: Int,
    val added: Int,
    val removed: Int,
    val pr: PrSummary?,
    val note: String?,
)

data class DraftBody(val key: String = "", val path: String? = null, val line: Int? = null, val side: String = "RIGHT",
                     val body: String = "", val findingId: String? = null)
data class DraftEdit(val body: String = "")
data class ViewedBody(val key: String = "", val path: String = "", val viewed: Boolean = true)
data class ReplyBody(val key: String = "", val body: String = "")
data class ResolveBody(val key: String = "", val resolved: Boolean = true)
data class SubmitBody(val key: String = "", val event: String = "COMMENT", val body: String = "")
data class KeyBody(val key: String = "")
data class SubmitResult(val posted: Int, val inBody: Int, val event: String, val url: String?, val view: ReviewView)
data class CheckoutResult(val branch: String, val note: String)
data class MergeBody(val key: String = "", val method: String = "merge", val deleteBranch: Boolean = false)
data class MergeResult(val merged: Boolean, val message: String, val view: ReviewView)
