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


def test_quiet_docker_desktop_hides_dashboard_and_keeps_other_settings(tmp_path, monkeypatch):
    store = tmp_path / "settings-store.json"
    store.write_text('{"AutoStart": true, "OpenUIOnStartupDisabled": false, "SettingsVersion": 46}', encoding="utf-8")
    monkeypatch.setattr(companion, "DOCKER_SETTINGS", store)
    monkeypatch.setattr(companion.sys, "platform", "linux")  # leave the real Run key alone
    companion.quiet_docker_desktop()
    import json
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data == {"AutoStart": True, "OpenUIOnStartupDisabled": True, "SettingsVersion": 46}
    companion.quiet_docker_desktop(take_over_autostart=True)
    assert json.loads(store.read_text(encoding="utf-8"))["AutoStart"] is False


def test_quiet_docker_desktop_ignores_missing_or_odd_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(companion.sys, "platform", "linux")
    monkeypatch.setattr(companion, "DOCKER_SETTINGS", tmp_path / "missing.json")
    companion.quiet_docker_desktop(take_over_autostart=True)
    odd = tmp_path / "odd.json"
    odd.write_text("[1, 2]", encoding="utf-8")
    monkeypatch.setattr(companion, "DOCKER_SETTINGS", odd)
    companion.quiet_docker_desktop()
    assert odd.read_text(encoding="utf-8") == "[1, 2]"


def test_stop_docker_stops_docker_desktop(monkeypatch):
    calls, launched = _fake_docker(monkeypatch, engine_up_after=0)
    assert companion.stop_docker()
    assert calls == [("desktop", "stop")]
