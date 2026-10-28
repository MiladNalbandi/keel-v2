"""0.18.64 the AC gate's code for the web (flow_actions.ac_code, GET /threads/{tid}/ac-code): the criterion's RED and
GREEN commits, each file with its status, counts, bucket and diff; lock, generated and binary files folded; long diffs
cut."""

from conftest import start, wait
from test_flows_feature import to
from test_v02 import commit_all, git, write
from keel_engine.runtime import flow_actions
from keel_engine.runtime.flow_actions import ac_code


def shop(tmp_path):
    root = tmp_path / "shop"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "README.md", "a shop\n")
    write(root, "src/orders/old_name.py", "def total(lines):\n    return sum(lines)\n")
    commit_all(root, "init")
    base = git(root, "rev-parse", "HEAD").stdout.strip()
    write(root, "tests/test_orders.py", "def test_an_order_keeps_its_lines():\n    assert parse()['lines'] == 3\n")
    write(root, "tests/fixtures/order.json", '{"lines": 3}\n')
    commit_all(root, "test(AC-1): an order keeps its lines")
    write(root, "src/orders/parse.py", "def parse(text):\n    return {'lines': 3}\n")
    write(root, "src/orders/big.py", "".join(f"LINE_{i} = {i}\n" for i in range(900)))
    write(root, "package-lock.json", '{"lockfileVersion": 3}\n')
    write(root, "src/orders/generated/client.py", "# generated\n")
    (root / "src/orders/logo.png").write_bytes(bytes(range(256)) * 4)
    git(root, "mv", "src/orders/old_name.py", "src/orders/totals.py")
    commit_all(root, "feat(AC-1): parse an order with its lines")
    return root, base


def test_the_red_and_green_commits_come_with_each_files_diff(tmp_path):
    root, base = shop(tmp_path)
    d = ac_code(str(root), "AC-1", base)
    red, green = d["red"], d["green"]
    assert red["subject"] == "test(AC-1): an order keeps its lines" and len(red["sha"]) == 40 and red["sha"].startswith(red["short"])
    assert green["subject"] == "feat(AC-1): parse an order with its lines"
    assert d["commits"] == 2 and d["files"] == 8 and d["branch"] == "main"
    assert d["added"] == red["added"] + green["added"] and d["removed"] == 0

    t = {f["path"]: f for f in red["files"]}
    assert t["tests/test_orders.py"]["bucket"] == "test" and t["tests/test_orders.py"]["status"] == "added"
    assert t["tests/test_orders.py"]["added"] == 2 and t["tests/test_orders.py"]["removed"] == 0
    assert t["tests/test_orders.py"]["diff"].startswith("@@ -0,0 +1,2 @@\n+def test_an_order_keeps_its_lines():")
    assert "diff --git" not in t["tests/test_orders.py"]["diff"], "the header lines stay out"

    g = {f["path"]: f for f in green["files"]}
    assert g["src/orders/parse.py"]["bucket"] == "prod" and "+def parse(text):" in g["src/orders/parse.py"]["diff"]
    lock = g["package-lock.json"]
    assert lock["bucket"] == "lock" and lock["folded"] is True and lock["diff"] is None and lock["added"] == 1
    gen = g["src/orders/generated/client.py"]
    assert gen["bucket"] == "generated" and gen["folded"] is True and gen["diff"] is None
    png = g["src/orders/logo.png"]
    assert png["binary"] is True and png["diff"] is None and png["added"] == 0
    moved = g["src/orders/totals.py"]
    assert moved["status"] == "renamed" and moved["old_path"] == "src/orders/old_name.py" and moved["added"] == 0


def test_a_long_diff_is_cut_and_says_so(tmp_path):
    root, base = shop(tmp_path)
    d = ac_code(str(root), "AC-1", base)
    big = next(f for f in d["green"]["files"] if f["path"] == "src/orders/big.py")
    assert big["added"] == 900 and big["lines"] == 901, "the hunk line and 900 added lines"
    assert big["truncated"] is True and len(big["diff"].splitlines()) == flow_actions.FILE_LINES
    assert d["truncated"] is True


def test_the_total_budget_leaves_later_files_without_text(tmp_path, monkeypatch):
    root, base = shop(tmp_path)
    monkeypatch.setattr(flow_actions, "TOTAL_BYTES", flow_actions.MIN_LEFT + 60)
    d = ac_code(str(root), "AC-1", base)
    texts = [f for f in d["red"]["files"] + d["green"]["files"] if not f.get("folded")]
    assert texts[0]["diff"] and any(f["diff"] is None and f["truncated"] for f in texts[1:])


def test_one_folded_file_comes_with_its_diff_on_request(tmp_path):
    root, base = shop(tmp_path)
    green = ac_code(str(root), "AC-1", base)["green"]
    one = ac_code(str(root), "AC-1", base, green["short"], "package-lock.json")
    assert one["file"]["path"] == "package-lock.json" and '+{"lockfileVersion": 3}' in one["file"]["diff"]
    # only the criterion's own commits, and never a binary
    assert ac_code(str(root), "AC-1", base, "0000000", "package-lock.json")["file"] is None
    assert ac_code(str(root), "AC-1", base, green["sha"], "src/orders/logo.png")["file"] is None


def test_a_missing_commit_is_none_and_counts_nothing(tmp_path):
    root, base = shop(tmp_path)
    d = ac_code(str(root), "AC-2", base)
    assert d["red"] is None and d["green"] is None and d["commits"] == 0 and d["files"] == 0
    assert ac_code(str(tmp_path), "AC-1")["red"] is None, "not a git repository"


def test_the_route_answers_for_the_criterion_the_flow_waits_on(client, repo):
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "ac_gate")
    assert s["waiting"]["ac"] == "AC-1"
    d = client.get(f"/threads/{tid}/ac-code", params={"ac": "AC-1"}).json()
    assert d["thread_id"] == tid and d["commits"] == 2
    assert d["red"]["subject"].startswith("test(AC-1)") and d["green"]["subject"].startswith("feat(AC-1)")
    assert all(f["bucket"] in ("test", "prod", "generated", "lock") for f in d["red"]["files"] + d["green"]["files"])
    assert any(f["diff"] for f in d["green"]["files"])
    assert client.get(f"/threads/{tid}/ac-code", params={"ac": "nope!"}).status_code == 400
