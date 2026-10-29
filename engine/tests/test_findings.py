from keel_engine.runtime.findings import blocking

ASSERTIONS = """## Review (assertions / test-quality angle on `git diff main...HEAD`)

**Blocking**

- `test/scores.test.js:72-79` — "AC-6 ... ranks distinct best scores in order" is byte-for-byte identical
  to the AC-2 test at `test/scores.test.js:14-21`; it's a duplicate test under a new name.
- `test/scores.test.js:81-90` — the tie test is identical to the AC-3 test; same issue.

AC-6 asks for tests that *cover* three scenarios, which AC-2/AC-3/AC-4's own tests already satisfy.

**Non-blocking**

- `test/scores.test.js:92-95` — the null test is fine but could reset first.
"""

PERF = """## Review — performance lens
### Blocking
None. `rankOf` adds no new data-access path.

### Non-blocking
- `src/scores.js:26` — O(players × scores) per call.
"""

INLINE_NONE = """Reviewed the diff.

**Blocking:** none.

- `src/scores.js:23-28` — correct.

**Non-blocking:**
- `test/scores.test.js:72-79` — duplicates.
"""

NO_SECTION = "Implementation matches the contract. No findings for this lens.\n\n**Non-blocking observations:**\n- something"


def test_two_bullets_under_a_bold_blocking_label():
    found = blocking(ASSERTIONS)
    assert len(found) == 2
    assert found[0].startswith("`test/scores.test.js:72-79`") and "duplicate test under a new name" in found[0]
    assert "AC-6 asks" not in " ".join(found) or len(found) == 2


def test_none_under_a_heading_and_inline_none_and_no_section():
    assert blocking(PERF) == []
    assert blocking(INLINE_NONE) == []
    assert blocking(NO_SECTION) == []


def test_non_blocking_alone_is_not_blocking():
    assert blocking("### Non-blocking\n- a nit\n- another") == []


def test_e2e_fail_counts():
    assert blocking("Ran the specs.\nE2E-RESULT: fail — login spec times out") == ["E2E failed: login spec times out"]
    assert blocking("E2E-RESULT: pass") == []


def test_inline_finding_after_the_label():
    assert blocking("**Blocking:** `a.js:3` leaks the token in a log line.") == ["`a.js:3` leaks the token in a log line."]


def test_keel_verdict_line_decides_and_is_never_a_finding():
    from keel_engine.runtime.findings import unique
    yes = ASSERTIONS + "\nBLOCKING: yes\n"
    assert len(blocking(yes)) == 2 and all(x not in ("yes", "no") for x in blocking(yes))
    assert blocking("**Blocking**\n- `a.js:1` something odd\n\nBLOCKING: no") == []
    assert blocking("Looks risky overall.\n\nBLOCKING: yes")[0].startswith("The reviewer marked this blocking: Looks risky")
    two = [{"lens": "#2", "text": "`test/x.js:72-79` dup of AC-2"}, {"lens": "#4", "text": "`test/x.js:72-79` — same dup"},
           {"lens": "#4", "text": "`test/x.js:81-90` dup of AC-3"}]
    assert [f["lens"] for f in unique(two)] == ["#2", "#4"]


def test_an_own_verdict_line_with_findings_stops_the_flow_with_its_list_items():
    text = "Read the diff.\n\n- `a.py:3` duplicates `b.py:9`\n- `c.py:1` renames a public field\n\nCODE-REVIEW: findings"
    assert blocking(text) == ["`a.py:3` duplicates `b.py:9`", "`c.py:1` renames a public field"]
    assert blocking("All good.\nCODE-REVIEW: pass") == []
    assert blocking("Checked the routes and the queries.\nSECURITY: clean") == []
    assert blocking("lodash is reachable from upload.ts:4.\nDEPS: findings") == [
        "The reviewer reported findings: lodash is reachable from upload.ts:4."]


def test_non_blocking_items_are_read_and_none_is_nothing():
    from keel_engine.runtime.findings import key, nonblocking
    assert nonblocking(ASSERTIONS) == ["`test/scores.test.js:92-95` — the null test is fine but could reset first."]
    assert nonblocking(PERF) == ["`src/scores.js:26` — O(players × scores) per call."]
    assert nonblocking("### Non-blocking\n- a nit\n- another\nCODE-REVIEW: pass") == ["a nit", "another"]
    assert nonblocking("**Non-blocking:** none.") == [] and nonblocking("**Blocking**\n- x") == []
    assert key("`a.py:3` bad") == key("a.py:3 — same place, other words") == "a.py:3"


def test_split_answer_and_the_reason_check():
    from keel_engine.runtime import clarify, finding_decisions
    q = finding_decisions.questions([{"lens": "l", "text": "t"}], can_amend=True)[0]
    assert clarify.split_answer(q, "Accept") == ("accept", "")
    assert clarify.split_answer(q, "accept: ticket 4") == ("accept", "ticket 4")
    assert clarify.split_answer(q, "not a finding - checked in X") == ("not a finding", "checked in X")
    assert clarify.split_answer(q, "amend spec") == ("amend spec", "")
    assert clarify.split_answer(q, "accepted it") == (None, "accepted it")
    assert clarify.reason_problem([q], {"f1": "accept"}, None)
    assert clarify.reason_problem([q], {"f1": "accept"}, "shared reason") is None
    assert clarify.reason_problem([q], {"f1": "fix"}, None) is None
    assert "needs a reason" in finding_decisions.problem({"questions": [q]}, "approve", "", {"answers": {"f1": "not a finding"}})[0]


def test_unanswered_findings_follow_the_plain_decision():
    """An old client: approve fixes all, reject accepts all with its why; own words accept with the words."""
    from keel_engine.runtime import finding_decisions as fd
    found = [{"lens": "l", "text": "a.py:1 x"}, {"lens": "l", "text": "b.py:2 y"}]
    asked = fd.questions(found, can_amend=False)
    assert [d["decision"] for d in fd.read(asked, found, {"decision": "approve"})] == ["fix", "fix"]
    got = fd.read(asked, found, {"decision": "reject", "why": "known"})
    assert [(d["decision"], d["reason"]) for d in got] == [("accept", "known"), ("accept", "known")]
    got = fd.read(asked, found, {"decision": "approve", "payload": {"answers": {"f2": "we chose this on purpose"}}})
    assert [(d["decision"], d["reason"]) for d in got] == [("fix", ""), ("accept", "we chose this on purpose")]
    got = fd.read(asked, found, {"decision": "approve", "payload": {"answers": {"f1": "amend spec"}}})
    assert got[0]["decision"] == "fix", "no amendment where the flow has none"
    auto = fd.read(asked, found, {"decision": "approve", "auto": True, "payload": {"answers": {"f1": "accept: x"}}})
    assert [d["decision"] for d in auto] == ["fix", "fix"]


# keel 0.18.68 the 0.18.65 regression: a branch review's checklist answers were read as blocking findings
CHECKLIST_ANSWER = """Read the spec, then git diff main...HEAD.

1. **Correctness across ACs**: nothing to report.
2. **Shared config keys, parameters and routes**
- EXPORT_LIMIT: new, read only by the export service.
- **Each public type against its contract**: none.
- Consumer upgrade: n/a, nothing is published.
- **Runtime and dependency versions**
- `src/Export/Writer.php:41` calls `utf8_encode()`, removed in PHP 8.4 (the CI matrix tests 8.4).

## What I did not check
- the deploy config (not in the repo)
- the staging database

CODE-REVIEW: findings"""


def test_checklist_answers_and_what_was_not_checked_are_not_findings():
    assert blocking(CHECKLIST_ANSWER) == [
        "EXPORT_LIMIT: new, read only by the export service.",
        "`src/Export/Writer.php:41` calls `utf8_encode()`, removed in PHP 8.4 (the CI matrix tests 8.4).",
    ]


def test_a_findings_heading_is_the_only_place_findings_are_read_from():
    text = ("## Checklist\n- Shared config keys: one new key, fine\n- Consumer upgrade: nothing to report\n\n"
            "## Findings\n- `src/Order.kt:30` the retry never ends\n\n## What I did not check\n- the CI file\n\n"
            "CODE-REVIEW: findings")
    assert blocking(text) == ["`src/Order.kt:30` the retry never ends"]


def test_only_what_was_not_checked_is_a_pass_with_notes():
    text = ("## Checklist\n- Correctness across ACs: nothing to report\n\n## Findings\nNone.\n\n"
            "## What I did not check\n- the CI file (not readable)\n\nCODE-REVIEW: findings")
    assert blocking(text) == []
    assert blocking("I read it all and found a problem in the export.\nCODE-REVIEW: findings") == [
        "The reviewer reported findings: I read it all and found a problem in the export."]


# keel 0.18.71 a dismissed finding said again in other words is the same finding
def test_the_same_problem_in_other_words_is_the_same_finding():
    from keel_engine.runtime.findings import same
    a = "`src/Payload.php:44` the nullable default `?string $x = null` is not the project's style"
    assert same(a, "`src/Payload.php:44` uses ?string $x = null, against the style rule")             # same file:line
    assert same("the webhook handler never logs a failed delivery when logging is enabled in config",
                "when logging is enabled, a failed delivery is never logged by the webhook handler")   # same words
    assert not same(a, "`src/Payload.php:80` the payload is decoded twice")
    assert not same("retry never ends", "the retry has no end")                                         # too short to tell


def test_a_finding_about_a_spec_keel_put_back_recommends_amend_spec():
    from keel_engine.runtime import finding_decisions as fd
    found = [{"text": "specs/002-webhook.md:44 says 201, the code answers 200", "lens": "spec"},
             {"text": "src/Handler.php:12 swallows the exception", "lens": "bugs"}]
    q = fd.questions(found, True, ["specs/002-webhook.md"])
    first = [o["label"] for o in q[0]["options"]]
    assert first[0] == fd.AMEND and q[0]["options"][0]["recommended"]
    assert [o["label"] for o in q[1]["options"]][0] == fd.FIX                        # not about the spec: fix first
    assert [o["label"] for o in fd.questions(found, True)[0]["options"]][0] == fd.FIX   # nothing put back: as before
