"""#101 the harness does what a coding agent does (feature list: docs/harness-parity.md): the project's instructions and
git state at the start, a folder's notes with its first Read, a shell whose folder stays and that runs in the
background, images, PDFs and notebooks, Grep's context lines, skills loaded on demand, helpers of a given agent type,
notes about files that changed, the run's usage live."""

import asyncio
import base64
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from test_harness import Scripted, call, kinds, request, run, text, turn

from keel_engine.harness import context, transcript
from keel_engine.harness.providers.anthropic import to_messages as a_messages
from keel_engine.harness.providers.openai_compat import to_messages as o_messages
from keel_engine.harness.tools import ToolSet, edit_notebook, notebook_text
from keel_engine.tools.agent_tools import ToolBox

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src" / "deep").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def total(xs):\n    return sum(xs)\n\n\ndef mean(xs):\n    return total(xs) / len(xs)\n")
    (root / "CLAUDE.md").write_text("Use tabs, never spaces.\n@docs/style.md\n")
    (root / "docs").mkdir()
    (root / "docs" / "style.md").write_text("Names are short.\n")
    (root / "src" / "AGENTS.md").write_text("src/: every function has a test.\n")
    (root / "logo.png").write_bytes(PNG)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(git + ["add", "-A"], cwd=root, check=True)
    subprocess.run(git + ["commit", "-qm", "feat: the first version"], cwd=root, check=True)
    (root / "src" / "app.py").write_text((root / "src" / "app.py").read_text() + "\n")
    return root


def box(project, **kw):
    return ToolBox(str(project), "none", **kw)


def test_a_session_starts_with_the_projects_instructions_and_git_state(project):
    p = Scripted([turn(call("Read", 1, file_path="src/app.py")), turn(text("ok"))])
    run(p, request(project))
    first = p.requests[0].messages[0]["content"][0]["text"]
    assert "<project-instructions source=\"CLAUDE.md\">\nUse tabs, never spaces." in first
    assert "<imported from=\"docs/style.md\">\nNames are short." in first
    assert "Branch: main" in first and " M src/app.py" in first and "feat: the first version" in first
    # src/AGENTS.md comes with the first Read in src/, once
    read = p.requests[1].messages[-1]["content"][0]["content"]
    assert "<folder-instructions source=\"src/AGENTS.md\">\nsrc/: every function has a test." in read


def test_instructions_the_agent_may_not_read_stay_out(project):
    (project / ".env").write_text("TOKEN=x\n")
    (project / "CLAUDE.md").write_text("@.env\n")
    assert "TOKEN=x" not in context.project_notes(box(project))


def test_bash_keeps_its_folder_inside_the_project(project):
    ts = ToolSet(box(project))
    run_ = lambda c: asyncio.run(ts.tools["Bash"].run({"command": c}))  # noqa: E731
    run_("cd src")
    assert run_("pwd").rstrip().endswith("/proj/src")
    assert "outside the project" in run_("cd /tmp")
    assert run_("pwd").rstrip().endswith("/proj")
    assert "__KEEL_PWD__" not in run_("echo hi")


def test_a_background_command_runs_on_and_stops_with_the_call(project):
    ts = ToolSet(box(project))
    started = asyncio.run(ts.tools["Bash"].run({"command": "echo first; sleep 30; echo never", "run_in_background": True}))
    assert started.startswith("Started in the background as shell_1")
    deadline = time.time() + 5
    out = ""
    while "\nfirst\n" not in out and time.time() < deadline:
        out = asyncio.run(ts.tools["BashOutput"].run({"shell_id": "shell_1"}))
        time.sleep(0.05)
    assert "running" in out and "first" in out
    assert "(no new output)" in asyncio.run(ts.tools["BashOutput"].run({"shell_id": "shell_1"}))
    assert asyncio.run(ts.tools["KillShell"].run({"shell_id": "shell_1"})).startswith("Stopped shell_1")
    ts2 = ToolSet(box(project))
    asyncio.run(ts2.tools["Bash"].run({"command": "sleep 30", "run_in_background": True}))
    proc = ts2.shells.bg["shell_1"].proc
    ts2.close()
    proc.wait(timeout=5)
    assert proc.poll() is not None
    refused = ToolSet(box(project, readonly=True))
    assert asyncio.run(refused.tools["Bash"].run({"command": "rm -rf src", "run_in_background": True})).startswith("REFUSED")


def test_an_image_is_shown_to_the_model_in_both_api_shapes(project):
    p = Scripted([turn(call("Read", 1, file_path="logo.png")), turn(text("a dot"))])
    run(p, request(project))
    res = p.requests[1].messages[-1]["content"][0]
    assert res["images"][0]["media_type"] == "image/png" and base64.b64decode(res["images"][0]["data"]) == PNG
    a = a_messages(p.requests[1].messages, "claude-opus-5-5")[-1]["content"][0]["content"]
    assert a[1]["type"] == "image" and a[1]["source"]["media_type"] == "image/png"
    o = o_messages("", p.requests[1].messages)
    assert o[-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_notebooks_are_read_as_cells_and_edited_cell_by_cell(project):
    nb = {"cells": [{"cell_type": "code", "id": "a1", "metadata": {}, "source": ["print(1)\n"], "execution_count": 1,
                     "outputs": [{"output_type": "stream", "text": ["1\n"]}]},
                    {"cell_type": "markdown", "id": "b2", "metadata": {}, "source": ["# Title"]}],
          "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    (project / "nb.ipynb").write_text(json.dumps(nb))
    shown = notebook_text(json.dumps(nb))
    assert "## cell 0 [code] id=a1\nprint(1)\n\n--- output ---\n1" in shown and "## cell 1 [markdown] id=b2" in shown
    p = Scripted([turn(call("Read", 1, file_path="nb.ipynb")),
                  turn(call("NotebookEdit", 2, notebook_path="nb.ipynb", cell_id="a1", new_source="print(2)\n")),
                  turn(call("NotebookEdit", 3, notebook_path="nb.ipynb", cell_number=1, edit_mode="insert", new_source="x = 3",
                            cell_type="code")),
                  turn(text("ok"))])
    run(p, request(project))
    after = json.loads((project / "nb.ipynb").read_text())
    assert after["cells"][0]["source"] == ["print(2)\n"] and after["cells"][0]["outputs"] == []
    assert after["cells"][2]["source"] == ["x = 3"] and len(after["cells"]) == 3
    with pytest.raises(ValueError):
        edit_notebook(json.dumps(nb), {"cell_number": 9})


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext is not installed")
def test_a_pdf_is_read_as_text(project, tmp_path):
    pdf = project / "doc.pdf"
    subprocess.run(["sh", "-c", f"printf 'hello pdf' | {shutil.which('python3')} -c \"\"" ], check=True)
    # a tiny valid PDF with the words hello pdf
    pdf.write_bytes(b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
                    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj "
                    b"4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 20 100 Td (hello pdf) Tj ET\nendstream endobj "
                    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")
    out = asyncio.run(ToolSet(box(project)).tools["Read"].run({"file_path": "doc.pdf"}))
    assert "hello pdf" in out


def test_grep_shows_lines_before_and_after(project):
    out = box(project).grep("return total", mode="content", before=2, glob="*.py")
    assert "src/app.py-5-def mean(xs):" in out and "src/app.py:6:" in out


def test_skills_are_listed_and_loaded_when_needed(project):
    (project / ".claude" / "skills" / "house").mkdir(parents=True)
    (project / ".claude" / "skills" / "house" / "SKILL.md").write_text(
        "---\nname: house-style\ndescription: How this project names things.\n---\n# House style\nShort names.\n")
    p = Scripted([turn(call("Skill", 1, skill="house-style"), call("Skill", 2, skill="debugging")), turn(text("ok"))])
    run(p, request(project))
    desc = next(t for t in p.requests[0].tools if t.name == "Skill").description
    assert "- house-style: How this project names things." in desc and "- debugging:" in desc
    own, lib = p.requests[1].messages[-1]["content"]
    assert own["content"].startswith("<skill name=\"house-style\">\n# House style\nShort names.")
    assert lib["content"].startswith("<skill name=\"debugging\">") and "references/" in lib["content"] or lib["content"]


def test_task_runs_a_helper_as_one_of_keels_agents(project):
    p = Scripted([turn(call("Task", 1, description="explore", prompt="What does app.py hold?", subagent_type="explorer")),
                  turn(text("done"))],
                 helper=[turn(text("app.py holds total and mean."))])
    res, steps = run(p, request(project))
    helper = p.requests[1]
    assert "keel's harness" in helper.system and helper.system != p.requests[0].system
    assert any(e.get("tool") == "Task·explorer" for k, _t, e in steps if k == "tool")
    bad = Scripted([turn(call("Task", 1, description="x", prompt="y", subagent_type="nobody")), turn(text("ok"))])
    run(bad, request(project))          # the schema's enum refuses an unknown type before it runs
    assert "does not fit its schema" in bad.requests[1].messages[-1]["content"][0]["content"]


def test_the_model_hears_when_a_file_it_read_changed_under_it(project):
    def change(req):
        (project / "src" / "app.py").write_text("changed by the person\n")
        return turn(call("Glob", 2, pattern="*.md"))

    p = Scripted([turn(call("Read", 1, file_path="src/app.py")), change, turn(text("ok"))])
    run(p, request(project))
    note = p.requests[2].messages[-1]["content"][-1]["content"]
    assert "these files changed since you read them" in note and "src/app.py" in note


def test_usage_arrives_while_the_agent_works(project):
    p = Scripted([turn(call("Glob", 1, pattern="*")), turn(text("ok"))])
    res, steps = run(p, request(project))
    live = [e for k, _t, e in steps if k == "usage"]
    assert [u["tokens_out"] for u in live] == [20, 40] and live[-1]["tokens_in"] == res.tokens_in
