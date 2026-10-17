"""Launching Archer brings SearXNG up: Docker Desktop is started first when its engine is down,
or SearXNG is started natively on this PC when it isn't in Docker at all."""
import pytest

from archer import companion, config, searxng_local
from archer.config import Settings, settings


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


# ------------------------------------------------- where SearXNG runs (settings.searxng_runtime)


def test_a_container_from_before_searxng_runtime_keeps_using_docker(monkeypatch):
    monkeypatch.setattr(config, "native_searxng_installed", lambda: False)
    assert Settings(_env_file=None).searxng_runtime == "external"
    assert Settings(_env_file=None, searxng_container="searxng").searxng_runtime == "docker"
    # Once the runtime is chosen, the old container name doesn't override it.
    assert Settings(_env_file=None, searxng_container="searxng", searxng_runtime="native").searxng_runtime == "native"


def test_a_native_install_is_the_default(monkeypatch):
    monkeypatch.setattr(config, "native_searxng_installed", lambda: True)
    assert Settings(_env_file=None).searxng_runtime == "native"
    # A choice in .env, or an older Docker setup, still wins.
    assert Settings(_env_file=None, searxng_runtime="external").searxng_runtime == "external"
    assert Settings(_env_file=None, searxng_container="searxng").searxng_runtime == "docker"


@pytest.fixture
def native(monkeypatch):
    """A native install on port 8889 with the process faked; .env changes recorded, not written."""
    monkeypatch.setattr(settings, "searxng_runtime", "native")
    monkeypatch.setattr(settings, "searxng_url", "http://127.0.0.1:8889")
    state = {"port": 8889, "running": False, "free": True, "pointed": []}
    monkeypatch.setattr(searxng_local, "installed", lambda: True)
    monkeypatch.setattr(searxng_local, "port", lambda: state["port"])
    monkeypatch.setattr(searxng_local, "url", lambda: f"http://127.0.0.1:{state['port']}")
    monkeypatch.setattr(searxng_local, "set_port", lambda value: state.update(port=value))
    monkeypatch.setattr(searxng_local, "port_free", lambda port: state["free"])
    monkeypatch.setattr(searxng_local, "free_port", lambda preferred=None: 8890)
    monkeypatch.setattr(searxng_local, "running", lambda: state["running"])
    monkeypatch.setattr(searxng_local, "start", lambda: state.update(running=True) or True)
    monkeypatch.setattr(searxng_local, "stop", lambda: state.update(running=False))
    monkeypatch.setattr(companion, "_point_at", lambda url: state["pointed"].append(url))
    monkeypatch.setattr(companion, "start_container", lambda name: pytest.fail("Docker used"))
    return state


def test_native_start_stop_and_running(native):
    assert companion.searxng_managed()
    assert companion.searxng_start() and companion.searxng_running()
    assert native["pointed"] == []  # .env already points at it
    companion.searxng_stop()
    assert not companion.searxng_running()


def test_native_moves_off_a_port_another_program_took(native):
    native["free"] = False  # e.g. a Docker SearXNG now publishes 8889
    assert companion.searxng_start()
    assert native["port"] == 8890
    assert native["pointed"] == ["http://127.0.0.1:8890"]


def test_native_not_installed_starts_nothing(native, monkeypatch):
    monkeypatch.setattr(searxng_local, "installed", lambda: False)
    assert not companion.searxng_start()
    assert not native["running"]


def test_shutdown_stops_native_but_leaves_docker_alone(native, monkeypatch):
    companion.searxng_start()
    companion.searxng_shutdown()
    assert not native["running"]

    monkeypatch.setattr(settings, "searxng_runtime", "docker")
    monkeypatch.setattr(settings, "searxng_container", "searxng")
    monkeypatch.setattr(companion, "_docker", lambda *a, **k: pytest.fail("container stopped on exit"))
    companion.searxng_shutdown()


def test_docker_runtime_dispatches_to_the_container(monkeypatch):
    calls, launched = _fake_docker(monkeypatch, engine_up_after=0)
    monkeypatch.setattr(settings, "searxng_runtime", "docker")
    monkeypatch.setattr(settings, "searxng_container", "searxng")
    assert companion.searxng_start()
    companion.searxng_stop()
    assert calls[-2:] == [("start", "searxng"), ("stop", "searxng")]


@pytest.mark.parametrize("provider,runtime,container", [
    ("searxng", "external", ""), ("duckduckgo", "native", ""), ("searxng", "docker", "")])
def test_nothing_to_manage(monkeypatch, provider, runtime, container):
    monkeypatch.setattr(settings, "search_provider", provider)
    monkeypatch.setattr(settings, "searxng_runtime", runtime)
    monkeypatch.setattr(settings, "searxng_container", container)
    monkeypatch.setattr(searxng_local, "start", lambda: pytest.fail("started"))
    assert not companion.searxng_managed()
    assert not companion.searxng_start() and not companion.searxng_running()
    companion.searxng_stop()


def test_point_at_writes_both_addresses_to_env(monkeypatch, tmp_path):
    from archer.app import app
    from archer.settings_service import SettingsService
    path = tmp_path / ".env"
    path.write_text("ARCHER_SEARXNG_URL=http://127.0.0.1:8888\n", encoding="utf-8")
    monkeypatch.setattr(app.state, "settings_service", SettingsService(path, live=Settings(_env_file=path)), raising=False)
    companion._point_at("http://127.0.0.1:8890")
    text = path.read_text(encoding="utf-8")
    assert "ARCHER_SEARXNG_URL=http://127.0.0.1:8890" in text
    assert "ARCHER_RESULTS_URL=http://localhost:8890/search?q={q}" in text
