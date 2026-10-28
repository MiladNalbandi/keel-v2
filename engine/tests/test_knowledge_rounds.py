"""The knowledge check after a real run (spring-petclinic): shorthand citations resolve, problems go back to the
librarian for its rounds, then the person chooses go on with a warning or stop, and neither fails a finished flow.
The fake librarian cites a file of the project it runs on, never keel's own demo."""

import subprocess
from pathlib import Path

from conftest import decide, start, wait
from keel_engine.models import fake as fake_mod
from keel_engine.runtime import knowledge

SETTINGS = {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "sections": ["architecture"], "fix_attempts": 2}


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def commit_all(root):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "files"], cwd=root, check=True)


def test_a_shorthand_or_bare_file_name_citation_resolves_to_its_one_tracked_file(repo):
    lines = "".join(f"line {n}\n" for n in range(1, 61))
    write(repo, "src/main/java/org/x/owner/OwnerRepository.java", lines)
    write(repo, "src/test/java/org/x/MySqlIntegrationTests.java", lines)
    write(repo, "a/Dup.java", "x\n")
    write(repo, "b/Dup.java", "x\n")
    commit_all(repo)
    write(repo, "docs/knowledge/architecture.md", "# Architecture\n\n"
          "Owners (`.../owner/OwnerRepository.java:36`), the database test (`MySqlIntegrationTests.java:50`).\n")
    assert knowledge.check(str(repo))["pass"], knowledge.check(str(repo))["problems"]

    write(repo, "docs/knowledge/architecture.md", "# Architecture\n\n"
          "Too far (`.../owner/OwnerRepository.java:99`), two of them (`Dup.java:1`), gone (`Gone.java:1`).\n")
    problems = "\n".join(knowledge.check(str(repo))["problems"])
    # 0.18.64 the real count: the last newline ends line 60, it does not start a 61st (runtime/citations.py lines)
    assert "`.../owner/OwnerRepository.java:99` but that file has 60 line(s) (src/main/java/org/x/owner/OwnerRepository.java)" in problems
    assert "`Dup.java:1`, which matches 2 files (a/Dup.java, b/Dup.java); give the full path" in problems
    assert "`Gone.java:1`, which does not exist" in problems


def test_the_fake_librarian_cites_a_file_of_the_project_it_runs_on(client, tmp_path):
    root = tmp_path / "petclinic"
    write(root, "README.md", "# petclinic\n")
    write(root, "build.gradle", "plugins { id 'java' }\n")
    write(root, "src/main/java/org/x/App.java", "class App {}\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    commit_all(root)
    assert fake_mod.first_source(str(root)) == "src/main/java/org/x/App.java"
    assert fake_mod.first_source(None) == "src/scores/__init__.py"

    tid = start(client, root, workflow="knowledge-refresh", title="Refresh knowledge", settings=SETTINGS)
    s = wait(client, tid)
    assert s["status"] == "done" and "warnings" not in s, s
    page = next(root.glob("**/knowledge/architecture.md")).read_text()
    assert "`src/main/java/org/x/App.java:1`" in page and "scores" not in page
    assert knowledge.check(str(root))["pass"]


def librarian(monkeypatch, fixes: bool):
    """The librarian writes a page with a citation of a missing file; its fix rounds repair it (fixes) or do not."""
    real = fake_mod._plan
    seen = []

    def plan(req):
        if req.agent != "librarian":
            return real(req)
        seen.append(req)
        fixing = req.step_name.endswith("fix the pages")
        cite = "src/scores/__init__.py:1" if fixing and fixes else "src/gone.py:1"
        return "docs/knowledge/architecture.md", f"# Architecture\n\nScores (`{cite}`).\n", "written", {}
    monkeypatch.setattr(fake_mod, "_plan", plan)
    return seen


def test_knowledge_problems_go_to_the_librarian_and_the_check_runs_again(client, repo, monkeypatch):
    seen = librarian(monkeypatch, fixes=True)
    tid = start(client, repo, workflow="knowledge-refresh", title="Refresh knowledge", settings=SETTINGS)
    s = wait(client, tid)
    assert s["status"] == "done" and "warnings" not in s, s
    fixers = [r for r in seen if r.step_name.endswith("fix the pages")]
    assert len(fixers) == 1
    assert "src/gone.py:1" in fixers[0].feedback and "full path from the repo root" in fixers[0].feedback
    assert any(line.startswith("knowledge memory_check round 1 of 2") for line in s["gate_log"])


def test_after_its_rounds_keel_asks_and_go_on_keeps_a_warning(client, repo, monkeypatch):
    seen = librarian(monkeypatch, fixes=False)
    tid = start(client, repo, workflow="knowledge-refresh", title="Refresh knowledge", settings=SETTINGS)
    s = wait(client, tid)
    w = s["waiting"]
    assert w["kind"] == "fix" and w["title"] == "knowledge check: problems remain after 2 round(s)", w
    assert w["labels"] == {"approve": "Go on with a warning", "reject": "Stop"}
    assert "src/gone.py:1" in w["detail"]
    assert len([r for r in seen if r.step_name.endswith("fix the pages")]) == 2, "one librarian per round, no blind re-check"
    s = decide(client, tid, "approve")
    assert s["status"] == "done", s
    assert s["warnings"] == ["knowledge check: The knowledge base has 1 problem(s) (not fixed after 2 round(s))"]
    done = client.bus.of(tid, "thread.done")[-1]["data"]
    assert done["warnings"] == s["warnings"] and done["title"].startswith("done with 1 warning(s): knowledge check")
    assert any("go on with a warning" in line for line in s["gate_log"])


def test_stop_at_the_knowledge_question_ends_the_flow_done_not_failed(client, repo, monkeypatch):
    librarian(monkeypatch, fixes=False)
    tid = start(client, repo, workflow="knowledge-refresh", title="Refresh knowledge", settings=SETTINGS)
    wait(client, tid)
    s = decide(client, tid, "reject", why="I fix the pages myself")
    assert s["status"] == "done" and "error" not in s, s
    assert s["warnings"] and not client.bus.of(tid, "thread.failed")
    assert client.bus.of(tid, "thread.done")[-1]["data"]["title"].startswith("stopped with a warning")


def test_a_section_is_stale_only_when_a_file_it_cites_changed(tmp_path):
    from test_v02 import commit_all, git, write
    from keel_engine.runtime import knowledge
    root = tmp_path / "k"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "src/a.py", "A = 1\n")
    write(root, "src/deep/b.py", "B = 1\n")
    write(root, "docs/knowledge/architecture.md", "# A\n\nStarts in `src/a.py:1`.\n")
    write(root, "docs/knowledge/data.md", "# D\n\nShorthand `b.py:1`.\n")
    commit_all(root, "init")
    since = git(root, "rev-parse", "HEAD").stdout.strip()
    write(root, "src/c.py", "C = 1\n")
    write(root, "docs/knowledge/architecture.md", "# A\n\nStarts in `src/a.py:1`. Edited.\n")
    commit_all(root, "a commit that touches no cited file")
    assert knowledge.stale(str(root), since) == []
    write(root, "src/deep/b.py", "B = 2\n")                    # uncommitted counts too; the shorthand resolves
    assert knowledge.stale(str(root), since) == [{"name": "data", "files": ["src/deep/b.py"]}]
    commit_all(root, "b")
    write(root, "src/a.py", "A = 2\n")
    assert [s["name"] for s in knowledge.stale(str(root), since)] == ["architecture", "data"]
    assert knowledge.cited_files(str(root), "data") == ["src/deep/b.py"]


def test_a_refresh_without_a_list_updates_only_the_stale_sections(client, tmp_path, monkeypatch):
    root = tmp_path / "kr"
    write(root, "src/a.py", "A = 1\n")
    write(root, "src/b.py", "B = 1\n")
    write(root, "docs/knowledge/architecture.md", "# A\n\nStarts in `src/a.py:1`.\n")
    write(root, "docs/knowledge/domain.md", "# D\n\nNamed in `src/b.py:1`.\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    commit_all(root)
    subprocess.run(["git", "checkout", "-q", "-b", "feat/x"], cwd=root, check=True)
    write(root, "src/a.py", "A = 2\n")
    commit_all(root)
    seen = []
    real = fake_mod._plan
    monkeypatch.setattr(fake_mod, "_plan", lambda req: (seen.append(req), real(req))[1])
    settings = {k: v for k, v in SETTINGS.items() if k != "sections"}
    tid = start(client, root, workflow="knowledge-refresh", title="Refresh knowledge", settings=settings)
    s = wait(client, tid)
    assert s["status"] == "done", s
    lib = [r for r in seen if r.agent == "librarian"]
    assert [r.section for r in lib] == ["architecture"]
    assert "`src/a.py`" in lib[0].prompt and "keep every claim that is still true" in lib[0].prompt


def test_init_again_keeps_the_sections_that_exist(tmp_path):
    from keel_engine.runtime import init_gates
    root = tmp_path / "again"
    write(root, "docs/knowledge/architecture.md", "# A\n")
    write(root, "docs/knowledge/data.md", "# D\n")
    d = init_gates.defaults(str(root))
    assert d["write_sections"] == [s for s in init_gates.SECTIONS if s not in ("architecture", "data")]
    plan = init_gates.plan(str(root), d)
    assert "Kept as they are (they exist): architecture, data" in plan
    q = next(x for x in init_gates.clarify_questions(str(root)) if x["id"] == "knowledge")
    arch = next(o for o in q["options"] if o["label"] == "architecture")
    assert arch["recommended"] is False and "kept" in arch["description"]
    assert "3. Write no knowledge section." in init_gates.plan(str(root), {**d, "write_sections": []})
