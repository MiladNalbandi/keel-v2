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
class ReviewController(private val reviews: ReviewService, private val ai: ReviewAiService, private val addressing: ReviewAddressService,
                       private val judging: ReviewJudgeService, private val threadSettings: ReviewThreadSettings,
                       private val diagrams: ReviewDiagramsService) {

    @GetMapping("/prs")
    fun prs(@PathVariable pid: String, @RequestParam(defaultValue = "review") filter: String) = reviews.prs(pid, filter)

    @GetMapping("/branch")
    fun branch(@PathVariable pid: String) = reviews.thisBranch(pid)

    @GetMapping("/view")
    fun view(@PathVariable pid: String, @RequestParam key: String, @RequestParam(defaultValue = "false") refresh: Boolean) = reviews.view(pid, key, refresh)

    /** keel TUI's review mode and keel2 mcp (keel-plugin.yml contributes.tui.modes): a review from a person's words. */
    @GetMapping("/open")
    fun open(@PathVariable pid: String, @RequestParam(defaultValue = "") ref: String) = reviews.view(pid, reviews.keyOf(pid, ref))

    /** 0.18.65 which base a branch review compares against (body: key, base remote | local | ""); answers the review. */
    @PutMapping("/base")
    fun pickBase(@PathVariable pid: String, @RequestBody b: BaseBody) = reviews.pickBase(pid, b.key, b.base)

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

    /** A branch review's pending comments move to its pull request (body: from, to). */
    @PostMapping("/drafts/move")
    fun moveDrafts(@PathVariable pid: String, @RequestBody b: MoveDrafts) = reviews.moveDrafts(pid, b)

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

    /** The overview's Database and System diagrams (Mermaid, ASCII and their data), read from git, kept per head commit. */
    @GetMapping("/diagrams")
    fun diagrams(@PathVariable pid: String, @RequestParam key: String, @RequestParam(defaultValue = "false") refresh: Boolean) =
        diagrams.diagrams(pid, key, refresh)

    // ---- keel's AI on a review

    @GetMapping("/ai")
    fun aiState(@PathVariable pid: String, @RequestParam key: String) = ai.state(pid, key)

    @PostMapping("/ai/{kind}")
    fun aiStart(@PathVariable pid: String, @PathVariable kind: String, @RequestBody b: KeyBody) = ai.start(pid, b.key, kind)

    /** "Is the reviewer right?": one cheap model call checks the thread against its lines (kept per last comment). Posts nothing. */
    @PostMapping("/threads/{threadId}/judge")
    fun judge(@PathVariable pid: String, @PathVariable threadId: String, @RequestBody b: JudgeBody) = judging.judge(pid, b.key, threadId, b)

    /** An extra thread button (Settings › Code Review threads): ask (one cheap call) or workflow (a flow). */
    @PostMapping("/threads/{threadId}/buttons/{buttonId}")
    fun button(@PathVariable pid: String, @PathVariable threadId: String, @PathVariable buttonId: String, @RequestBody b: JudgeBody) =
        judging.button(pid, b.key, threadId, buttonId, b)

    /** The thread settings that apply to this project (and what the pickers offer); PUT overrides them here (null: follow General). */
    @GetMapping("/settings")
    fun settings(@PathVariable pid: String) = threadSettings.view(pid)

    @PutMapping("/settings")
    fun saveSettings(@PathVariable pid: String, @RequestBody patch: Map<String, Any?>) = threadSettings.update(pid, patch)

    /** Check all open threads: one call for every open thread without an answer for its last comment. */
    @PostMapping("/threads/judge")
    fun judgeAll(@PathVariable pid: String, @RequestBody b: JudgeBody) = judging.judgeAll(pid, b.key, b)

    /** "Address it": a small flow makes the change the thread asks for, on the pull request's branch. Never pushes. */
    @PostMapping("/threads/{threadId}/address")
    fun address(@PathVariable pid: String, @PathVariable threadId: String, @RequestBody b: AddressBody) = addressing.address(pid, b.key, threadId, b)

    @PostMapping("/ai/findings/{findingId}")
    fun decide(@PathVariable pid: String, @PathVariable findingId: String, @RequestBody b: FindingDecision) = ai.decide(pid, b.key, findingId, b)
}

/** The Code Review thread settings keel-wide (General): GET what applies, PUT a checked patch (null removes a key). */
@RestController
@RequestMapping("/api/review/settings")
class ReviewSettingsController(private val threadSettings: ReviewThreadSettings) {
    @GetMapping
    fun view() = threadSettings.view(null)

    @PutMapping
    fun save(@RequestBody patch: Map<String, Any?>) = threadSettings.update(null, patch)
}
