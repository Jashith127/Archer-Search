"""Launching Archer brings SearXNG up: Docker Desktop is started first when its engine is down."""
from archer import companion


def _fake_docker(monkeypatch, engine_up_after: int):
    calls, launched = [], []
    state = {"info": 0}

    def docker(*args, timeout=20):
        calls.append(args)
        if args[0] == "info":
            state["info"] += 1
            return state["info"] > engine_up_after
        return True

    monkeypatch.setattr(companion, "docker_cli", lambda: "docker.exe")
    monkeypatch.setattr(companion, "_docker", docker)
    monkeypatch.setattr(companion, "_launch_docker_desktop", lambda: launched.append(True) or True)
    monkeypatch.setattr(companion.time, "sleep", lambda s: None)
    return calls, launched


def test_running_engine_starts_container_directly(monkeypatch):
    calls, launched = _fake_docker(monkeypatch, engine_up_after=0)
    assert companion.start_container("searxng")
    assert not launched
    assert calls[-1] == ("start", "searxng")


def test_stopped_engine_launches_docker_desktop_then_container(monkeypatch):
    calls, launched = _fake_docker(monkeypatch, engine_up_after=3)
    assert companion.start_container("searxng")
    assert launched == [True]
    assert calls[-1] == ("start", "searxng")


def test_engine_that_never_comes_up_gives_up(monkeypatch):
    calls, launched = _fake_docker(monkeypatch, engine_up_after=10**9)
    ticks = iter(range(0, 10**6, 100))
    monkeypatch.setattr(companion.time, "monotonic", lambda: next(ticks))
    assert not companion.start_container("searxng", wait=250)
    assert ("start", "searxng") not in calls


def test_no_container_configured_does_nothing(monkeypatch):
    calls, launched = _fake_docker(monkeypatch, engine_up_after=0)
    assert not companion.start_container("")
    assert not calls and not launched
