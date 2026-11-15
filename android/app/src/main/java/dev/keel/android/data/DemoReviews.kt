package dev.keel.android.data

import java.time.Instant

/**
 * The demo's pull requests, each with its own files, diffs, threads and checks (so #409 shows the retry change, not
 * the VAT one), and the pending comments of your review.
 */
class DemoReviews {
    private fun ago(sec: Long) = Instant.now().minusSeconds(sec).toString()
    private val d = '$'

    private class DemoFile(val path: String, val status: String, val diff: String) {
        val added get() = diff.lines().count { it.startsWith("+") && !it.startsWith("+++") }
        val removed get() = diff.lines().count { it.startsWith("-") && !it.startsWith("---") }
    }

    private class DemoPr(val summary: PrSummary, val files: List<DemoFile>, val threads: MutableList<ReviewThread>, val checks: List<CheckRun>, val approved: List<String>) {
        val drafts = mutableListOf<Draft>()
    }

    val list = listOf(
        PrSummary(412, "Keep the stored VAT when Pricing Engine sends none", "alex", "feature/SHOPS-1769", "main", updatedAt = ago(7200), mine = true),
        PrSummary(409, "Supplier adapter: retry on 429 with backoff", "sara.k", "feature/SHOPS-1758", "main", updatedAt = ago(18_000), reviewRequested = true),
        PrSummary(405, "Checkout page: show the last known VAT", "j.berg", "feature/SHOPS-1744", "main", updatedAt = ago(86_400), reviewRequested = true),
        PrSummary(398, "Order search by reference", "alex", "feature/SHOPS-1650", "main", updatedAt = ago(172_800), mine = true),
    )

    private fun checks(n: Int, failed: String? = null) = (1..n).map { CheckRun(listOf("phpunit (8.3)", "phpstan", "php-cs-fixer", "deptrac", "composer audit", "eslint", "build", "e2e smoke", "migrations", "openapi diff", "coverage", "secrets", "docker", "sonar")[(it - 1) % 14], "success") } +
        listOfNotNull(failed?.let { CheckRun(it, "failure") })

    private val prs: Map<Int, DemoPr> = mapOf(
        412 to DemoPr(
            list[0],
            listOf(
                DemoFile(
                    "src/Vat/VatWebhook.php", "modified",
                    """
                    @@ -52,9 +52,14 @@ final class VatWebhook
                         public function handle(VatPayload ${d}payload): void
                         {
                             ${d}order = ${d}this->orders->byCheckout(${d}payload->checkout);
                    -        ${d}order->setVat(${d}payload->vat);
                    +        if (${d}payload->vat === null) {
                    +            ${d}this->log->info('vat absent, kept', ['supplier' => ${d}payload->supplier]);
                    +            return;
                    +        }
                    +        ${d}order->setVat(${d}payload->vat);
                             ${d}order->touchVatUpdatedAt(${d}this->clock->now());
                    +        ${d}this->orders->save(${d}order);
                             ${d}this->events->dispatch(new VatChanged(${d}order->id()));
                         }
                     }
                    """.trimIndent(),
                ),
                DemoFile(
                    "tests/Integration/Vat/VatAbsentKeepsStoredTest.php", "added",
                    """
                    @@ -0,0 +1,26 @@
                    +<?php
                    +
                    +namespace App\Tests\Integration\Vat;
                    +
                    +use App\Vat\VatWebhook;
                    +use App\Tests\Integration\IntegrationTestCase;
                    +
                    +final class VatAbsentKeepsStoredTest extends IntegrationTestCase
                    +{
                    +    protected function setUp(): void
                    +    {
                    +        parent::setUp();
                    +        ${d}conn = ${d}this->connection();
                    +        ${d}conn->insert('supplier', ['id' => 7, 'name' => 'Acme']);
                    +        ${d}conn->insert('resolver', ['supplier_id' => 7, 'kind' => 'order-perform']);
                    +        ${d}conn->insert('order', ['id' => 41, 'checkout' => 'JD0001', 'vat' => '2026-10-21']);
                    +    }
                    +
                    +    public function test_absent_vat_keeps_the_stored_one(): void
                    +    {
                    +        ${d}this->webhook()->handle(${d}this->payload(checkout: 'JD0001', vat: null));
                    +
                    +        self::assertSame('2026-10-21', ${d}this->order(41)->vat()?->format('Y-m-d'));
                    +    }
                    +}
                    +
                    """.trimIndent(),
                ),
                DemoFile(
                    "migrations/Version20261020.php", "added",
                    """
                    @@ -0,0 +1,14 @@
                    +<?php
                    +
                    +declare(strict_types=1);
                    +
                    +namespace DoctrineMigrations;
                    +
                    +final class Version20261020 extends AbstractMigration
                    +{
                    +    public function up(Schema ${d}schema): void
                    +    {
                    +        ${d}this->addSql('ALTER TABLE order RENAME COLUMN vat_at TO vat_updated_at');
                    +        ${d}this->addSql('CREATE INDEX order_vat_updated ON order (vat_updated_at)');
                    +    }
                    +}
                    """.trimIndent(),
                ),
            ),
            mutableListOf(
                ReviewThread(
                    "412-1", "tests/Integration/Vat/VatAbsentKeepsStoredTest.php", 15, startLine = 13,
                    comments = listOf(
                        ReviewComment("c1", "sara.k", "Maybe a factory would fit here?", ago(10_800)),
                        ReviewComment("c2", "alex", "Fair point, thanks. Will update it.", ago(3600)),
                    ),
                ),
                ReviewThread(
                    "412-2", "src/Vat/VatWebhook.php", 56,
                    comments = listOf(
                        ReviewComment("c3", "sara.k", "Should this log the order too, not only the supplier (AC-3)?", ago(7000)),
                        ReviewComment("c3b", "alex", "Fixed: the log line has the order id now.", ago(1800)),
                    ),
                ),
                ReviewThread(
                    "412-4", "tests/Integration/Vat/VatAbsentKeepsStoredTest.php", 17,
                    comments = listOf(ReviewComment("c3c", "j.berg", "These raw inserts could use the factories we already have.", ago(5400))),
                ),
                ReviewThread(
                    "412-3", "migrations/Version20261020.php", 11, resolved = true,
                    comments = listOf(ReviewComment("c4", "j.berg", "rename vat_at to vat_updated_at", ago(20_000)), ReviewComment("c5", "alex", "Renamed in 3c1f9a2. Thanks!", ago(18_000))),
                ),
            ),
            checks(14), listOf("j.berg"),
        ),
        409 to DemoPr(
            list[1],
            listOf(
                DemoFile(
                    "src/Supplier/HttpSupplierClient.php", "modified",
                    """
                    @@ -31,12 +31,20 @@ final class HttpSupplierClient implements SupplierClient
                         public function track(string ${d}checkout): CheckoutResponse
                         {
                    -        ${d}response = ${d}this->http->request('GET', ${d}this->url(${d}checkout));
                    -        if (${d}response->getStatusCode() === 429) {
                    -            throw new SupplierThrottled(${d}this->supplier);
                    +        for (${d}try = 1; ; ${d}try++) {
                    +            ${d}response = ${d}this->http->request('GET', ${d}this->url(${d}checkout));
                    +            if (${d}response->getStatusCode() !== 429) {
                    +                break;
                    +            }
                    +            if (${d}try >= ${d}this->backoff->maxTries()) {
                    +                throw new SupplierThrottled(${d}this->supplier, ${d}try);
                    +            }
                    +            ${d}this->sleeper->sleep(${d}this->backoff->delay(${d}try, ${d}response->getHeaders(false)['retry-after'][0] ?? null));
                             }

                             return CheckoutResponse::fromArray(${d}response->toArray());
                         }
                    """.trimIndent(),
                ),
                DemoFile(
                    "src/Supplier/Backoff.php", "added",
                    """
                    @@ -0,0 +1,24 @@
                    +<?php
                    +
                    +namespace App\Supplier;
                    +
                    +/** Exponential backoff for a supplier that answers 429: 1 s, 2 s, 4 s, ... capped, Retry-After wins. */
                    +final class Backoff
                    +{
                    +    public function __construct(private int ${d}maxTries = 4, private int ${d}capMs = 8000) {}
                    +
                    +    public function maxTries(): int { return ${d}this->maxTries; }
                    +
                    +    public function delay(int ${d}try, ?string ${d}retryAfter): int
                    +    {
                    +        ${d}base = min(${d}this->capMs, 1000 * 2 ** (${d}try - 1));
                    +        ${d}jitter = random_int(0, intdiv(${d}base, 4));
                    +        if (${d}retryAfter !== null && ctype_digit(${d}retryAfter)) {
                    +            return min(${d}this->capMs, (int) ${d}retryAfter * 1000);
                    +        }
                    +
                    +        return ${d}base + ${d}jitter;
                    +    }
                    +}
                    +
                    +
                    """.trimIndent(),
                ),
                DemoFile(
                    "tests/Unit/Supplier/BackoffTest.php", "added",
                    """
                    @@ -0,0 +1,18 @@
                    +<?php
                    +
                    +namespace App\Tests\Unit\Supplier;
                    +
                    +use App\Supplier\Backoff;
                    +use PHPUnit\Framework\TestCase;
                    +
                    +final class BackoffTest extends TestCase
                    +{
                    +    public function test_retry_after_wins(): void
                    +    {
                    +        self::assertSame(3000, (new Backoff())->delay(1, '3'));
                    +    }
                    +
                    +    public function test_capped(): void
                    +    {
                    +        self::assertLessThanOrEqual(10000, (new Backoff())->delay(9, null));
                    +    }
                    """.trimIndent(),
                ),
            ),
            mutableListOf(
                ReviewThread(
                    "409-1", "src/Supplier/Backoff.php", 15,
                    comments = listOf(
                        ReviewComment("c6", "alex", "Should the delay have some jitter? Two workers would retry in lockstep.", ago(14_000)),
                        ReviewComment("c7", "sara.k", "Good catch: random_int jitter added, up to a quarter of the delay.", ago(5000)),
                    ),
                ),
                ReviewThread("409-2", "src/Supplier/HttpSupplierClient.php", 41, comments = listOf(ReviewComment("c8", "j.berg", "Sleeping in a web request: is this only called from the worker?", ago(4000)))),
            ),
            checks(12), emptyList(),
        ),
        405 to DemoPr(
            list[2],
            listOf(
                DemoFile(
                    "templates/checkout/show.html.twig", "modified",
                    """
                    @@ -18,7 +18,12 @@
                       <section class="vat">
                    -    <p>{{ order.vat|date('d M Y') }}</p>
                    +    {% if order.vat %}
                    +      <p>{{ order.vat|date('d M Y') }}</p>
                    +    {% else %}
                    +      <p class="vat-last">Last known VAT: {{ order.lastKnownVat|date('d M Y') }}</p>
                    +      <small>updated {{ order.vatUpdatedAt|ago }}</small>
                    +    {% endif %}
                       </section>
                    """.trimIndent(),
                ),
                DemoFile(
                    "assets/checkout.css", "modified",
                    """
                    @@ -40,3 +40,7 @@
                     .vat p { font-weight: 600; }
                    +.vat-last { color: var(--dim); }
                    +.vat small { display: block; font-size: 12px; }
                    """.trimIndent(),
                ),
            ),
            mutableListOf(
                ReviewThread(
                    "405-1", "templates/checkout/show.html.twig", 22,
                    comments = listOf(
                        ReviewComment("c9", "alex", "Show the date in the order's timezone?", ago(60_000)),
                        ReviewComment("c10", "j.berg", "It uses the reader's timezone, like the rest of the page. OK?", ago(30_000)),
                    ),
                ),
            ),
            checks(11, failed = "e2e smoke"), emptyList(),
        ),
        398 to DemoPr(
            list[3],
            listOf(
                DemoFile(
                    "src/Search/OrderSearch.php", "modified",
                    """
                    @@ -12,6 +12,10 @@ final class OrderSearch
                         public function find(string ${d}q): array
                         {
                    +        if (preg_match('/^[A-Z]{2}\d{4,}${d}/', ${d}q)) {
                    +            return ${d}this->repo->byReference(${d}q);
                    +        }
                    +
                             return ${d}this->repo->fullText(${d}q);
                         }
                    """.trimIndent(),
                ),
            ),
            mutableListOf(
                ReviewThread(
                    "398-1", "src/Search/OrderSearch.php", 15, resolved = true,
                    comments = listOf(ReviewComment("c11", "sara.k", "Is the reference always two letters?", ago(200_000)), ReviewComment("c12", "alex", "Yes, supplier prefix. Added a test.", ago(190_000))),
                ),
            ),
            checks(14), listOf("sara.k", "j.berg"),
        ),
    )

    private fun prOf(key: String?) = prs[key.orEmpty().removePrefix("pr:").toIntOrNull() ?: 412] ?: prs.getValue(412)

    fun view(key: String?): ReviewView {
        val p = prOf(key)
        val s = p.summary
        return ReviewView(
            key = "pr:${s.number}", number = s.number, title = s.title, author = s.author, base = s.base, branch = s.branch,
            url = "https://github.com/acme/checkout/pull/${s.number}", state = "open", checks = p.checks, approved = p.approved,
            files = p.files.map { ChangedFile(it.path, it.status, it.added, it.removed) }, added = p.files.sumOf { it.added }, removed = p.files.sumOf { it.removed },
            threads = p.threads.toList(), drafts = p.drafts.toList(), canPost = true,
            // 0.26.1 two reviewers asked for the same thing on lines close together: keel checks them as one point
            threadGroups = SAME_POINT.filter { g -> g.all { id -> p.threads.any { it.id == id && !it.resolved } } },
            host = HostRef("github", "github.com", "acme/checkout", "https://github.com/acme/checkout"), me = "alex",
        )
    }

    fun diff(key: String?, path: String): FileDiff {
        val f = prOf(key).files.firstOrNull { it.path == path } ?: return FileDiff(path, "")
        return FileDiff(path, "diff --git a/$path b/$path\n--- a/$path\n+++ b/$path\n" + f.diff)
    }

    private fun prOfThread(id: String) = prs.values.firstOrNull { p -> p.threads.any { it.id == id } }

    fun reply(threadId: String, body: String): ReviewView {
        val p = prOfThread(threadId) ?: prs.getValue(412)
        val i = p.threads.indexOfFirst { it.id == threadId }
        if (i >= 0) p.threads[i] = p.threads[i].copy(comments = p.threads[i].comments + ReviewComment("r${System.nanoTime()}", "alex", body, Instant.now().toString()))
        return view("pr:${p.summary.number}")
    }

    fun resolve(threadId: String, resolved: Boolean): ReviewView {
        val p = prOfThread(threadId) ?: prs.getValue(412)
        val i = p.threads.indexOfFirst { it.id == threadId }
        if (i >= 0) p.threads[i] = p.threads[i].copy(resolved = resolved)
        return view("pr:${p.summary.number}")
    }

    fun addDraft(key: String?, path: String?, line: Int?, side: String, body: String): Draft {
        val draft = Draft("d${System.nanoTime()}", path, line, side, body, Instant.now().toString())
        prOf(key).drafts += draft
        return draft
    }

    fun deleteDraft(id: String) = prs.values.forEach { p -> p.drafts.removeAll { it.id == id } }

    /** The pending comments become threads of yours on the code host. */
    fun submit(key: String?): SubmitResult {
        val p = prOf(key)
        val n = p.drafts.size
        p.drafts.forEach { dr -> p.threads += ReviewThread("new-${dr.id}", dr.path, dr.line, dr.side, comments = listOf(ReviewComment("c-${dr.id}", "alex", dr.body, Instant.now().toString()))) }
        p.drafts.clear()
        return SubmitResult(n, "COMMENT", null, view(key))
    }
}

/** The demo's threads that make the same point (keel's ReviewJudgeService.samePoint finds them on a real keel). */
internal val SAME_POINT = listOf(listOf("412-1", "412-4"))
