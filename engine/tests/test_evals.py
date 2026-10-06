"""v0.8.0 quality runs: the eval sets (content/evals) and a fresh copy of a set's project for one case."""

import subprocess
from pathlib import Path

from keel_engine.runtime import evals


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True).stdout


def test_the_bundled_eval_sets_load_with_their_cases(client):
    sets = {s["name"]: s for s in client.get("/evals").json()}
    assert {"shop-js", "scores-py"} <= set(sets)
    shop = sets["shop-js"]
    assert shop["problems"] == [] and [c["id"] for c in shop["cases"]] == ["euro-format", "quantity-below-one"]
    assert shop["cases"][0]["workflow"] == "change" and shop["cases"][0]["cap_tokens"] is None      # the api: 2 × estimate
    assert "formatEuro" in shop["cases"][0]["request"]
    assert sets["scores-py"]["project"] == "demo" and sets["scores-py"]["cases"][0]["id"] == "median"


def test_a_case_gets_a_fresh_repository_under_the_data_folder(client, env):
    dest = env / "data" / "evals" / "run-1" / "shop-js-euro-format"
    r = client.post("/evals/prepare", json={"name": "shop-js", "dest": str(dest)}).json()
    assert r["path"] == str(dest.resolve()) and (dest / "package.json").exists() and (dest / "src" / "domain" / "money.js").exists()
    assert _git(dest, "branch", "--show-current").strip() == "main"
    assert _git(dest, "log", "--format=%s").strip() == "chore: eval project shop-js"
    again = client.post("/evals/prepare", json={"name": "shop-js", "dest": str(dest)})
    assert again.status_code == 409
    demo = client.post("/evals/prepare", json={"name": "scores-py", "dest": str(env / "data" / "evals" / "run-1" / "median")}).json()
    assert (Path(demo["path"]) / "src" / "scores" / "__init__.py").exists()


def test_prepare_refuses_a_folder_outside_the_data_folder_and_an_unknown_set(client, env, tmp_path):
    r = client.post("/evals/prepare", json={"name": "shop-js", "dest": str(tmp_path / "elsewhere")})
    assert r.status_code == 400 and "data folder" in r.json()["error"]
    r = client.post("/evals/prepare", json={"name": "nope", "dest": str(env / "data" / "evals" / "x")})
    assert r.status_code == 404


def test_a_broken_eval_set_lists_its_problems(tmp_path, monkeypatch):
    content = tmp_path / "content"
    (content / "evals" / "bad").mkdir(parents=True)
    (content / "evals" / "bad" / "eval.yml").write_text("name: bad\ncases:\n  - id: Not OK\n    request: x\n  - id: ok\n")
    monkeypatch.setenv("KEEL_CONTENT", str(content))
    s = evals.load_all()[0]
    assert s["name"] == "bad" and s["cases"] == []
    assert any("Not OK" in p for p in s["problems"]) and any("no request" in p for p in s["problems"])
    assert any("no project folder" in p for p in s["problems"])
