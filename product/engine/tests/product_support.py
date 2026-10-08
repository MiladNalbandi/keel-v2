"""keel Product's engine test helpers (a module of its own: keel's engine tests have a conftest too)."""

import subprocess
import time

from keel_engine.workflows import templates

FAKE = {"provider": "fake", "mode": "api", "model": "fake"}
INITIATIVE = {"id": "INI-12", "title": "Prices in euro", "idea": "EU visitors see USD prices. Show euro.", "owner": "Product"}

def git_repo(path, files: dict[str, str]):
    path.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "first"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", "-c", "commit.gpgsign=false", *args], cwd=path,
                       check=True, capture_output=True)
    return path


def start(client, root, workflow: str, data: dict, request: str = "Initiative INI-12: Prices in euro."):
    wf = templates.get_template(workflow)
    assert wf is not None, workflow
    body = {"project_id": "product", "root": str(root), "workflow": wf.model_dump(), "title": f"INI-12 · {workflow}",
            "models": {"default": FAKE}, "settings": {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"},
            "mcp": [], "skills": {}, "request": request, "data": {"initiative": INITIATIVE, "note": "", **data}}
    r = client.post("/threads", json=body)
    assert r.status_code == 200, r.text
    return r.json()["thread_id"]


def wait(client, tid, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/threads/{tid}").json()
        if s["status"] != "running":
            return s
        time.sleep(0.02)
    raise AssertionError(f"thread {tid} still running")


def decide(client, tid, decision="approve", why=None, **kw):
    r = client.post(f"/threads/{tid}/resume", json={"decision": decision, "why": why, **kw})
    assert r.status_code == 200, r.text
    return wait(client, tid)
