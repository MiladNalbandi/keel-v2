"""keel 0.20.0 how much of the machine keel may use (runtime/resources.py, Settings › Access › Resources)."""

import asyncio
import os
import sys

import pytest

from keel_engine.models import cli
from keel_engine.runtime import resources


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path))
    return tmp_path


def cgroup(tmp_path, **files):
    root = tmp_path / "cg"
    root.mkdir()
    for name, text in files.items():
        (root / name.replace("_", ".")).write_text(text)
    return root


def test_the_container_limits_are_read_from_its_cgroup(tmp_path):
    root = cgroup(tmp_path, cpu_max="400000 100000\n", memory_current="1000\n", memory_max="7516192768\n",
                  memory_events="low 0\noom 2\noom_kill 3\n")
    assert resources.cpu_limit(root) == 4
    assert resources.memory(root) == {"used": 1000, "limit": 7516192768, "oom_kills": 3}
    assert resources.auto_agents(root) == 4


def test_no_limit_and_no_cgroup_are_not_a_limit(tmp_path):
    root = cgroup(tmp_path, cpu_max="max 100000\n", memory_max="max\n")
    assert resources.cpu_limit(root) is None and resources.memory(root)["limit"] is None
    assert resources.memory(tmp_path / "none") == {"used": None, "limit": None, "oom_kills": 0}


@pytest.mark.parametrize("cpu_max,want", [("100000 100000", 2), ("150000 100000", 2), ("300000 100000", 3), ("1600000 100000", 6)])
def test_auto_is_the_cpus_at_least_2_at_most_6(tmp_path, cpu_max, want):
    assert resources.auto_agents(cgroup(tmp_path, cpu_max=cpu_max)) == want


def test_the_settings_are_kept_and_checked(data):
    assert resources.settings() == {"max_agents": 0, "low_priority": True}
    resources.save({"max_agents": 3})
    assert resources.settings() == {"max_agents": 3, "low_priority": True} and resources.max_agents() == 3
    resources.save({"low_priority": False})
    assert resources.settings() == {"max_agents": 3, "low_priority": False}
    for bad in ({"max_agents": -1}, {"max_agents": 33}, {"max_agents": "2"}, {"max_agents": True}, {"low_priority": "no"}):
        with pytest.raises(ValueError):
            resources.save(bad)
    assert resources.settings()["max_agents"] == 3
    (data / "resources.json").write_text("not json")
    assert resources.settings() == resources.DEFAULTS


async def test_only_max_agents_run_at_once_and_the_others_wait(data):
    resources.save({"max_agents": 2})
    running = peak = 0

    async def agent():
        nonlocal running, peak
        async with resources.gate().slot():
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1
    await asyncio.gather(*[agent() for _ in range(6)])
    assert peak == 2 and resources.gate().running == 0 and resources.gate().waiting == 0


async def test_a_cancelled_agent_gives_its_place_back(data):
    resources.save({"max_agents": 1})
    started = asyncio.Event()

    async def holds():
        async with resources.gate().slot():
            started.set()
            await asyncio.sleep(60)
    task = asyncio.create_task(holds())
    await started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    async def next_one():
        async with resources.gate().slot():
            return "ran"
    assert await asyncio.wait_for(next_one(), 2) == "ran"


async def test_a_raised_limit_frees_a_waiting_agent_and_a_cancelled_one_leaves_the_queue(data, monkeypatch):
    monkeypatch.setattr(resources, "WAKE_S", 0.02)
    resources.save({"max_agents": 1})
    done = asyncio.Event()

    async def holds():
        async with resources.gate().slot():
            await done.wait()

    async def waits():
        async with resources.gate().slot():
            return "ran"
    first = asyncio.create_task(holds())
    await asyncio.sleep(0)
    second, third = asyncio.create_task(waits()), asyncio.create_task(waits())
    await asyncio.sleep(0.05)
    assert not second.done() and resources.gate().waiting == 2
    third.cancel()
    await asyncio.gather(third, return_exceptions=True)
    assert resources.gate().waiting == 1
    resources.save({"max_agents": 2})                       # the first still runs: only the new limit frees the second
    assert await asyncio.wait_for(second, 2) == "ran" and not first.done()
    done.set()
    await first


async def test_run_cli_runs_an_agent_at_a_lower_priority_when_asked(data):
    base = os.nice(0)
    # the priority is set just after the start, so the child reads it a moment later
    long_argv = [sys.executable, "-c", "import os, time; time.sleep(0.3); print(os.nice(0))"]
    out, _ = await cli.run_cli("probe", long_argv, timeout=30)
    assert int(out.strip()) == max(base, resources.NICE)
    resources.save({"low_priority": False})
    out, _ = await cli.run_cli("probe", long_argv, timeout=30)
    assert int(out.strip()) == base


def test_the_engine_answers_and_refuses_a_bad_value(client):
    got = client.get("/resources").json()
    assert got["max_agents"] == 0 and got["low_priority"] is True and got["agents_limit"] >= 2 and got["running"] == 0
    assert client.put("/resources", json={"max_agents": 4}).json()["agents_limit"] == 4
    r = client.put("/resources", json={"max_agents": 99})
    assert r.status_code == 400 and "max_agents" in r.json()["error"]


def test_settings_saves_the_limits_it_wants_for_keel2_to_take(client, data):
    from pathlib import Path

    got = client.put("/resources", json={"memory": "6G", "cpus": "2.5"}).json()
    assert got["wanted"] == {"memory": "6g", "cpus": "2.5"}
    assert (Path(data) / "limits-wanted").read_text() == "memory=6g\ncpus=2.5\n"      # keel2 reads it with sed
    assert client.put("/resources", json={"memory": "off", "cpus": ""}).json()["wanted"] == {"memory": "off"}
    assert client.put("/resources", json={"memory": "", "cpus": ""}).json()["wanted"] == {}
    assert not (Path(data) / "limits-wanted").exists()
    for bad, why in (({"memory": "500m"}, "at least 1g"), ({"memory": "lots"}, "Memory"), ({"memory": "6g\nx=1"}, "Memory"),
                     ({"cpus": "0"}, "CPUs"), ({"cpus": "many"}, "CPUs")):
        r = client.put("/resources", json=bad)
        assert r.status_code == 400 and why in r.json()["error"], bad
    assert client.put("/resources", json={"max_agents": 2}).json()["max_agents"] == 2


def test_settings_saves_docker_access_and_the_wifi_for_keel2_to_take_at_its_next_start(client, data, monkeypatch):
    """0.21.2 keel2 starts keel: Settings can only ask it, in <data>/start-wanted (keel2 confirms an "on" there)."""
    from pathlib import Path

    monkeypatch.setenv("KEEL_BIND", "127.0.0.1")
    monkeypatch.setattr(resources, "start_now", lambda: {"docker": False, "wifi": False})
    got = client.get("/resources").json()
    assert got["start"] == {"docker": False, "wifi": False} and got["start_wanted"] == {}
    got = client.put("/resources", json={"docker": True}).json()
    assert got["start_wanted"] == {"docker": True}
    assert (Path(data) / "start-wanted").read_text() == "docker=1\n"                    # keel2 reads it with sed
    assert client.put("/resources", json={"wifi": True}).json()["start_wanted"] == {"docker": True, "wifi": True}
    # back to how keel runs now: nothing waits
    assert client.put("/resources", json={"docker": False, "wifi": False}).json()["start_wanted"] == {}
    assert not (Path(data) / "start-wanted").exists()
    r = client.put("/resources", json={"docker": "yes"})
    assert r.status_code == 400 and "docker" in r.json()["error"]


def test_how_keel_runs_now_comes_from_the_socket_and_the_address(monkeypatch):
    monkeypatch.setenv("KEEL_BIND", "0.0.0.0")
    assert resources.start_now()["wifi"] is True
    monkeypatch.setenv("KEEL_BIND", "127.0.0.1")
    assert resources.start_now()["wifi"] is False
