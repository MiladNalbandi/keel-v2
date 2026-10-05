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
