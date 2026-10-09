package keel.api.review

import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

/** The Code Review plugin: Code › Review (docs/CONTRACT.md "v0.14.0: Code Review"). Every call needs the plugin on. */
@RestController
@RequestMapping("/api/projects/{pid}/review")
class ReviewController(private val reviews: ReviewService, private val ai: ReviewAiService) {

    @GetMapping("/prs")
    fun prs(@PathVariable pid: String, @RequestParam(defaultValue = "review") filter: String) = reviews.prs(pid, filter)

    @GetMapping("/branch")
    fun branch(@PathVariable pid: String) = reviews.thisBranch(pid)

    @GetMapping("/view")
    fun view(@PathVariable pid: String, @RequestParam key: String, @RequestParam(defaultValue = "false") refresh: Boolean) = reviews.view(pid, key, refresh)

    @GetMapping("/diff")
    fun diff(@PathVariable pid: String, @RequestParam key: String, @RequestParam path: String) = reviews.diff(pid, key, path)

    @GetMapping("/file")
    fun file(@PathVariable pid: String, @RequestParam key: String, @RequestParam path: String, @RequestParam(defaultValue = "head") side: String) =
        reviews.fileAt(pid, key, path, side)

    @GetMapping("/definition")
    fun definition(@PathVariable pid: String, @RequestParam key: String, @RequestParam symbol: String) = reviews.definition(pid, key, symbol)

    @GetMapping("/usages")
    fun usages(@PathVariable pid: String, @RequestParam key: String, @RequestParam symbol: String) = reviews.usages(pid, key, symbol)

    @PostMapping("/drafts")
    fun addDraft(@PathVariable pid: String, @RequestBody b: DraftBody) = reviews.addDraft(pid, b)

    @PutMapping("/drafts/{id}")
    fun editDraft(@PathVariable pid: String, @PathVariable id: String, @RequestBody b: DraftEdit) = reviews.editDraft(pid, id, b.body)

    @DeleteMapping("/drafts/{id}")
    fun deleteDraft(@PathVariable pid: String, @PathVariable id: String): Map<String, Boolean> {
        reviews.deleteDraft(pid, id)
        return mapOf("ok" to true)
    }

    @PostMapping("/viewed")
    fun viewed(@PathVariable pid: String, @RequestBody b: ViewedBody) = mapOf("viewed" to reviews.setViewed(pid, b))

    @PostMapping("/threads/{threadId}/reply")
    fun reply(@PathVariable pid: String, @PathVariable threadId: String, @RequestBody b: ReplyBody) = reviews.reply(pid, threadId, b)

    @PostMapping("/threads/{threadId}/resolve")
    fun resolve(@PathVariable pid: String, @PathVariable threadId: String, @RequestBody b: ResolveBody) = reviews.resolve(pid, threadId, b)

    @PostMapping("/submit")
    fun submit(@PathVariable pid: String, @RequestBody b: SubmitBody) = reviews.submit(pid, b)

    @PostMapping("/merge")
    fun merge(@PathVariable pid: String, @RequestBody b: MergeBody) = reviews.merge(pid, b)

    @PostMapping("/checkout")
    fun checkout(@PathVariable pid: String, @RequestBody b: KeyBody) = reviews.checkout(pid, b.key)

    // ---- keel's AI on a review

    @GetMapping("/ai")
    fun aiState(@PathVariable pid: String, @RequestParam key: String) = ai.state(pid, key)

    @PostMapping("/ai/{kind}")
    fun aiStart(@PathVariable pid: String, @PathVariable kind: String, @RequestBody b: KeyBody) = ai.start(pid, b.key, kind)

    @PostMapping("/ai/findings/{findingId}")
    fun decide(@PathVariable pid: String, @PathVariable findingId: String, @RequestBody b: FindingDecision) = ai.decide(pid, b.key, findingId, b)
}
