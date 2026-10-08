package keel.api.review

import keel.api.common.ApiException
import org.springframework.http.HttpStatus

/** A line comment that goes out with a review: on the new side (RIGHT) or the old side (LEFT) of the diff. */
data class OutComment(val path: String, val line: Int, val side: String, val body: String)

class HostException(message: String, hint: String? = null, status: Int = 502) :
    ApiException(HttpStatus.valueOf(status), message, hint)

/**
 * What the review window needs from GitHub or GitLab. Every call is the person's own (their token); keel never posts
 * on its own. A host that cannot do one (GitLab has no "request changes" review state) says so in [HostException].
 */
interface CodeHost {
    val ref: HostRef

    /** The token's own user name. */
    fun me(): String

    /** The open pull requests, newest change first. */
    fun list(me: String): List<PrSummary>

    fun pr(number: Int): PrDetail

    /** The open pull request of a branch of this repo, if there is one. */
    fun forBranch(branch: String, me: String): PrSummary?

    fun checks(pr: PrDetail): List<CheckRun>

    fun decisions(number: Int): Decisions

    fun threads(number: Int): List<ReviewThread>

    /** The comments that are not on a line (the pull request's conversation). */
    fun conversation(number: Int): List<ReviewComment>

    fun reply(number: Int, thread: ReviewThread, body: String)

    fun resolve(number: Int, thread: ReviewThread, resolved: Boolean)

    /** One review: line comments plus COMMENT, APPROVE or REQUEST_CHANGES. Returns a link to it, when the host gives one. */
    fun submit(pr: PrDetail, event: String, body: String, comments: List<OutComment>): String?

    /** What `git fetch` needs: the repo's https URL, the auth header for it, and the refs of a pull request's head. */
    fun fetchUrl(): String
    fun authHeader(): String
    fun headRef(number: Int): String
}
