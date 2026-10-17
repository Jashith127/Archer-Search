"""The companion app's local state and Windows helpers: first-run setup, browsers, Docker, SearXNG.

Nothing here touches search history. `data/companion.json` records only whether setup finished.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

from archer.config import DATA_DIR, settings

STATE_FILE = DATA_DIR / "companion.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def onboarded() -> bool:
    return load().get("onboarded") is True


def set_onboarded(done: bool) -> None:
    data = {**load(), "onboarded": done}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(STATE_FILE)


# ------------------------------------------------------------------ browsers

# id: (name, path under a Windows base folder, settings page that lists search engines, ProgId prefix)
BROWSERS = {
    "helium": ("Helium", r"imput\Helium\Application\chrome.exe", "chrome://settings/searchEngines", "Helium"),
    "chrome": ("Google Chrome", r"Google\Chrome\Application\chrome.exe", "chrome://settings/searchEngines", "ChromeHTML"),
    "edge": ("Microsoft Edge", r"Microsoft\Edge\Application\msedge.exe", "edge://settings/searchEngines", "MSEdgeHTM"),
    "brave": ("Brave", r"BraveSoftware\Brave-Browser\Application\brave.exe", "brave://settings/searchEngines", "BraveHTML"),
    "vivaldi": ("Vivaldi", r"Vivaldi\Application\vivaldi.exe", "vivaldi://settings/search", "VivaldiHTM"),
    "opera": ("Opera", r"Programs\Opera\opera.exe", "opera://settings/searchEngines", "Opera"),
    "firefox": ("Firefox", r"Mozilla Firefox\firefox.exe", "about:preferences#search", "FirefoxURL"),
}


def _bases() -> list[Path]:
    names = ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")
    return [Path(os.environ[name]) for name in names if os.environ.get(name)]


def _default_progid() -> str:
    if sys.platform != "win32":
        return ""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
            return str(winreg.QueryValueEx(key, "ProgId")[0])
    except OSError:
        return ""


def browser_path(browser: str) -> Path | None:
    if browser not in BROWSERS:
        return None
    relative = BROWSERS[browser][1]
    for base in _bases():
        path = base / relative
        if path.is_file():
            return path
    return None


def browsers() -> list[dict]:
    """Installed browsers, the default first."""
    progid = _default_progid()
    found = []
    for key, (name, _, page, prefix) in BROWSERS.items():
        if browser_path(key):
            found.append({"id": key, "name": name, "settings": page, "default": progid.startswith(prefix),
                          "chromium": key != "firefox"})
    return sorted(found, key=lambda b: (not b["default"], list(BROWSERS).index(b["id"])))


def open_search_settings(browser: str) -> bool:
    """Open a browser at its search-engine settings. Only fixed pages of detected browsers run."""
    path = browser_path(browser)
    if not path:
        return False
    subprocess.Popen([str(path), BROWSERS[browser][2]], close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return True


# -------------------------------------------------------------------- docker

# Docker Desktop installs per machine or per user; its CLI isn't always on PATH for GUI apps.
_DOCKER_HOMES = [Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Docker" / "Docker",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DockerDesktop"]


def docker_cli() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    for home in _DOCKER_HOMES:
        cli = home / "resources" / "bin" / "docker.exe"
        if cli.is_file():
            return str(cli)
    return None


def docker_available() -> bool:
    return docker_cli() is not None


def _docker(*args: str, timeout: float = 20) -> bool:
    cli = docker_cli()
    if not cli:
        return False
    try:
        return subprocess.run([cli, *args], capture_output=True, timeout=timeout, creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _launch_docker_desktop() -> bool:
    # `docker desktop start` (Docker Desktop 4.37+) brings the engine up in the background; whether the
    # dashboard opens too is Docker's own "Open Docker Dashboard when Docker Desktop starts" setting.
    if _docker("desktop", "start", "--detach", timeout=30):
        return True
    for home in _DOCKER_HOMES:
        exe = home / "Docker Desktop.exe"
        if exe.is_file():
            try:
                subprocess.Popen([str(exe)], close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
                return True
            except OSError:
                return False
    return False


def start_container(name: str, wait: float = 240) -> bool:
    """Bring SearXNG up: start Docker Desktop if its engine isn't running, wait for it, then
    `docker start <name>`. Quiet, bounded, and harmless if everything is already running."""
    if not name or not docker_available():
        return False
    if not _docker("info", "--format", "{{.ServerVersion}}"):
        if not _launch_docker_desktop():
            return False
        deadline = time.monotonic() + wait
        while not _docker("info", "--format", "{{.ServerVersion}}"):
            if time.monotonic() > deadline:
                return False
            time.sleep(3)
    return _docker("start", name, timeout=90)


# ------------------------------------------------------------------- searxng
# One place that knows where SearXNG runs (settings.searxng_runtime), for startup and the tray.

def searxng_managed() -> bool:
    """Archer starts and stops SearXNG itself: the provider is SearXNG and it runs here or in Docker."""
    if settings.search_provider != "searxng":
        return False
    if settings.searxng_runtime == "native":
        return True
    return settings.searxng_runtime == "docker" and bool(settings.searxng_container)


def searxng_start() -> bool:
    if not searxng_managed():
        return False
    if settings.searxng_runtime == "docker":
        return start_container(settings.searxng_container)
    return _start_native()


def searxng_stop() -> None:
    if not searxng_managed():
        return
    if settings.searxng_runtime == "docker":
        _docker("stop", settings.searxng_container, timeout=60)
        return
    from archer import searxng_local
    searxng_local.stop()


def searxng_running() -> bool:
    if not searxng_managed():
        return False
    if settings.searxng_runtime == "native":
        from archer import searxng_local
        return searxng_local.running()
    # Asking SearXNG is quicker than asking Docker, whose CLI stalls while its engine starts.
    try:
        return httpx.get(settings.searxng_url.rstrip("/") + "/healthz", timeout=0.5).status_code == 200
    except httpx.HTTPError:
        return False


def searxng_shutdown() -> None:
    """When Archer exits: the native server goes with it (its job would end it anyway); a Docker
    container is left as it was, as before."""
    if searxng_managed() and settings.searxng_runtime == "native":
        searxng_stop()


def _start_native() -> bool:
    from archer import searxng_local
    if not searxng_local.installed():
        return False
    if not searxng_local.running() and not searxng_local.port_free(searxng_local.port()):
        # Another program took the port since the install (often a Docker SearXNG on 8888); move.
        try:
            searxng_local.set_port(searxng_local.free_port())
        except searxng_local.SetupError:
            return False
    if settings.searxng_url.rstrip("/") != searxng_local.url():
        _point_at(searxng_local.url())
    return searxng_local.start()


def _point_at(url: str) -> None:
    """Save the native server's address in .env and push it into the running app's clients."""
    from archer.app import app
    from archer.settings_service import SettingsService, apply_runtime
    service = getattr(app.state, "settings_service", None)
    if service is None:
        service = app.state.settings_service = SettingsService()
    result = service.apply({"searxng_url": url,
                            "results_url": url.replace("127.0.0.1", "localhost") + "/search?q={q}"})
    apply_runtime(app, result["changed"])
