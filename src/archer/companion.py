"""The companion app's local state and Windows helpers: first-run setup, browsers, Docker, SearXNG.

Nothing here touches search history. `data/companion.json` records only whether setup finished.
"""
import json
import os
import shutil
import sqlite3
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
                          "chromium": key != "firefox", "suggest_field": key in SUGGEST_FIELD,
                          "archer": search_setup(key, search_prefixes())})
    return sorted(found, key=lambda b: (not b["default"], list(BROWSERS).index(b["id"])))


# Browsers whose Add and Edit search engine dialogs have a suggestions URL field. Stock Chromium's
# (Chrome, Edge, Brave, Opera) don't: there only an engine found through /opensearch.xml has one.
SUGGEST_FIELD = {"helium"}

# Chromium profiles under %LOCALAPPDATA%. Opera keeps its profile elsewhere and isn't checked.
PROFILES = {
    "helium": r"imput\Helium\User Data",
    "chrome": r"Google\Chrome\User Data",
    "edge": r"Microsoft\Edge\User Data",
    "brave": r"BraveSoftware\Brave-Browser\User Data",
    "vivaldi": r"Vivaldi\User Data",
}


def _profile(browser: str) -> Path | None:
    if browser not in PROFILES or not os.environ.get("LOCALAPPDATA"):
        return None
    root = Path(os.environ["LOCALAPPDATA"]) / PROFILES[browser]
    try:
        last = json.loads((root / "Local State").read_text(encoding="utf-8")).get("profile", {}).get("last_used")
    except (OSError, ValueError, AttributeError):
        last = None
    path = root / (last if isinstance(last, str) and last else "Default")
    return path if path.is_dir() else None


def search_prefixes() -> tuple[str, ...]:
    """Search URLs that are Archer, as Chromium stores them."""
    return (f"{settings.base_url}/search?q=", f"http://127.0.0.1:{settings.port}/search?q=")


def search_setup(browser: str, prefixes: tuple[str, ...]) -> dict | None:
    """How a Chromium browser has Archer as a search engine, read from its profile (never written).

    An engine without a suggestions URL never asks /suggest: one added by hand with that field left
    empty, or in a browser whose dialog lacks it (see SUGGEST_FIELD).
    state: ready (default, with suggestions), no_suggestions (default, added by hand),
    found (picked up from Archer's page, not the default), manual (added by hand, not the default), missing.
    """
    profile = _profile(browser)
    if profile is None:
        return None
    ours = lambda url: isinstance(url, str) and url.startswith(prefixes)
    try:
        prefs = json.loads((profile / "Preferences").read_text(encoding="utf-8"))
        dse = prefs.get("default_search_provider_data", {}).get("mirrored_template_url_data") or {}
    except (OSError, ValueError, AttributeError):
        dse = {}
    rows: list[tuple] = []
    try:
        # immutable: the running browser holds a lock; this reads the search engines table only.
        db = sqlite3.connect(f"{(profile / 'Web Data').as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            rows = db.execute("SELECT url, suggest_url, is_active FROM keywords").fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        pass
    detected = [row for row in rows if ours(row[0]) and row[1]]
    active = any(row[2] == 1 for row in detected)
    if ours(dse.get("url")):
        state = "ready" if dse.get("suggestions_url") else "no_suggestions"
    elif detected:
        state = "found"
    elif any(ours(row[0]) for row in rows):
        state = "manual"
    else:
        state = "missing"
    return {"state": state, "detected": bool(detected), "active": active}


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


# Docker Desktop keeps its preferences here and its own sign-in entry in the per-user Run key.
DOCKER_SETTINGS = Path(os.environ.get("APPDATA", "")) / "Docker" / "settings-store.json"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_DOCKER_RUN_NAME = "Docker Desktop"


def quiet_docker_desktop(take_over_autostart: bool = False) -> None:
    """Let Archer start Docker Desktop in the background: no dashboard window when it starts and,
    when Archer itself starts with Windows, no sign-in entry of Docker's own that would open it."""
    changes = {"OpenUIOnStartupDisabled": True}
    if take_over_autostart:
        changes["AutoStart"] = False
    try:
        data = json.loads(DOCKER_SETTINGS.read_text(encoding="utf-8"))
        if isinstance(data, dict) and any(data.get(k) != v for k, v in changes.items()):
            data.update(changes)
            tmp = DOCKER_SETTINGS.with_suffix(".archer-tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(DOCKER_SETTINGS)
    except (OSError, ValueError):
        pass  # not installed yet, or a format we don't know: leave Docker's settings alone
    if take_over_autostart and sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, _DOCKER_RUN_NAME)
        except OSError:
            pass


def docker_running() -> bool:
    return _docker("info", "--format", "{{.ServerVersion}}")


def _launch_docker_desktop() -> bool:
    # `docker desktop start` (Docker Desktop 4.37+) brings the engine up in the background; the
    # dashboard stays closed because quiet_docker_desktop turned off "open on startup".
    quiet_docker_desktop()
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
    if not docker_running():
        if not _launch_docker_desktop():
            return False
        deadline = time.monotonic() + wait
        while not docker_running():
            if time.monotonic() > deadline:
                return False
            time.sleep(3)
    # Just after the engine comes up, a container with a restart policy may be starting on its own
    # and `docker start` fails; once it is running, `docker start` succeeds.
    for attempt in range(3):
        if _docker("start", name, timeout=90):
            return True
        time.sleep(2)
    return False


def stop_docker() -> bool:
    """Shut Docker Desktop down, engine and VM included, to give its memory back."""
    return _docker("desktop", "stop", timeout=120)


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
