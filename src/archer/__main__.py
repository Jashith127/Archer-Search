"""Launcher: runs the server with a tray icon, or opens the companion app if Archer is already running.

- Started from the Start menu or a shortcut: opens the companion app (setup on first run).
- Started by Windows at sign-in (`--startup`): waits the configured delay, optionally starts
  SearXNG (natively on this PC, or its Docker container), then does what Settings → Startup says
  (tray only by default).

When Archer manages SearXNG's container it starts Docker Desktop itself, in the background, and
takes over Docker's own sign-in entry (which opens the dashboard) while Archer starts with Windows.
The tray menu can start and stop Docker. SearXNG run natively is Archer's own process: it starts
with Archer, stops with it, and the tray menu can start or stop it.
"""

import argparse
import json
import logging
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

from archer.config import DATA_DIR, settings

if TYPE_CHECKING:
    import uvicorn  # imported in main(), after the port is bound: it takes most of a second


def _port_free() -> bool:
    # Binding is instant; an HTTP probe of a closed port on Windows waits out its whole timeout.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", settings.port))
            return True
        except OSError:
            return False


def _listen() -> socket.socket | None:
    """Archer's listening socket, bound before the app imports (about two seconds). The window can
    open at once: its request waits in the socket's queue until the server answers it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn does
        sock.bind((settings.host, settings.port))
        sock.listen(2048)
    except OSError:
        sock.close()
        return None
    sock.set_inheritable(True)
    return sock


def _already_running() -> bool:
    if _port_free():
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{settings.port}/health", timeout=1) as resp:
            return json.load(resp).get("app") == "archer"
    except (OSError, ValueError):
        return False


def _wait_started(server: "uvicorn.Server", thread: threading.Thread) -> bool:
    while thread.is_alive():
        if server.started:
            return True
        time.sleep(0.05)
    return False


def _relaunch_command() -> list[str]:
    scripts = Path(sys.executable).parent
    launcher = scripts / "archerw.exe"
    return [str(launcher)] if launcher.exists() else [sys.executable, "-m", "archer"]


def _companion_url(page: str = "") -> str:
    from archer import companion
    if not companion.onboarded():
        return f"{settings.base_url}/app/welcome"
    return f"{settings.base_url}/app{page}"


def _first_view(args) -> str | None:
    """What to open once the server is up, or None for tray only."""
    from archer import companion
    if args.startup:
        if not companion.onboarded():
            return _companion_url()
        return {"app": _companion_url(), "search": settings.base_url + "/"}.get(settings.startup_open)
    if args.open:
        return _companion_url("#browser")
    return None if args.no_window else _companion_url()


def _show(url: str) -> None:
    from archer.window import open_app
    if url.rstrip("/") == settings.base_url:
        webbrowser.open(url)  # the search page belongs in the real browser
    else:
        open_app(url)


def _manages_docker() -> bool:
    return (settings.search_provider == "searxng" and settings.searxng_runtime == "docker"
            and bool(settings.searxng_container))


def _manages_native_searxng() -> bool:
    """Archer runs SearXNG itself, on this PC, with no Docker."""
    return settings.search_provider == "searxng" and settings.searxng_runtime == "native"


def _run_tray(server: "uvicorn.Server", thread: threading.Thread, restart: threading.Event,
              notify: bool, docker_boot: threading.Thread | None = None) -> bool:
    """Show the tray icon at once, while the server is still importing; returns False if it never started."""
    import pystray

    from archer import autostart, companion
    from archer.icon import make_icon

    show_docker = _manages_docker() and companion.docker_available()
    # Native SearXNG is Archer's own process, so it has its own item; Docker's covers the container.
    show_native = _manages_native_searxng()

    failed = threading.Event()
    # "running", "stopped", "starting" or "stopping"; "" until the first check. Checks run off the
    # menu's thread, since `docker info` can take seconds while the engine boots.
    docker = {"state": ""}

    def docker_job(state: str, work) -> None:
        def run():
            docker["state"] = state
            tray.update_menu()
            work()
            docker["state"] = "running" if companion.docker_running() else "stopped"
            tray.update_menu()
        threading.Thread(target=run, daemon=True).start()

    def docker_label(item) -> str:
        return {"starting": "Docker is starting…", "stopping": "Docker is stopping…",
                "running": "Stop Docker"}.get(docker["state"], "Start Docker")

    def docker_click(icon, item):
        if docker["state"] == "running":
            docker_job("stopping", companion.stop_docker)
        else:
            docker_job("starting", lambda: companion.start_container(settings.searxng_container))

    def toggle_autostart(icon, item):
        autostart.set_enabled(not autostart.enabled())

    def toggle_searxng(icon, item):
        # In the background: starting SearXNG builds nothing but can take a few seconds.
        action = companion.searxng_stop if companion.searxng_running() else companion.searxng_start
        threading.Thread(target=action, daemon=True).start()

    def searxng_label(item) -> str:
        return "Stop SearXNG" if _searxng_running() else "Start SearXNG"

    def stop(icon):
        server.should_exit = True
        icon.stop()

    def restart_app(icon=None, item=None):
        restart.set()
        stop(tray)

    def boot():
        if not _wait_started(server, thread):
            failed.set()
            tray.stop()
            return
        from archer.app import app
        # The companion app can restart Archer too, to apply a new port or address.
        app.state.restart = restart_app
        if notify:
            try:
                tray.notify("Archer is running. Search from your address bar.", "Archer")
            except Exception:  # notifications are a nicety; some shells refuse them
                pass

    def ready(icon):
        icon.visible = True
        threading.Thread(target=boot, daemon=True).start()
        if show_docker:
            # Follow the start main() began, or just learn whether the engine is up.
            docker_job("starting" if docker_boot else "", docker_boot.join if docker_boot else lambda: None)

    menu = pystray.Menu(
        pystray.MenuItem("Open Archer", lambda: _show(_companion_url()), default=True),
        pystray.MenuItem("Search", lambda: webbrowser.open(settings.base_url + "/")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(docker_label, docker_click, visible=show_docker,
                         enabled=lambda item: docker["state"] in ("running", "stopped")),
        pystray.MenuItem(searxng_label, toggle_searxng, visible=show_native),
        pystray.MenuItem("Start with Windows", toggle_autostart, checked=lambda item: autostart.enabled()),
        pystray.MenuItem("Restart", restart_app),
        pystray.MenuItem("Quit", lambda icon, item: stop(icon)),
    )
    tray = pystray.Icon("archer", make_icon(64), f"Archer · {settings.base_url}", menu)
    tray.run(setup=ready)
    return not failed.is_set()


def main() -> None:
    parser = argparse.ArgumentParser(prog="archer")
    parser.add_argument("--open", action="store_true", help="open the companion app at browser setup")
    parser.add_argument("--startup", action="store_true", help="started by Windows at sign-in")
    parser.add_argument("--no-window", action="store_true", help="start in the tray without opening a window")
    parser.add_argument("--no-tray", action="store_true", help="run in the foreground without a tray icon")
    args = parser.parse_args()

    if _already_running():
        if not args.startup:
            _show(_companion_url("#browser" if args.open else ""))
        return

    DATA_DIR.mkdir(exist_ok=True)
    if sys.stdout is None:
        # pythonw has no console; send uvicorn's logging and any tracebacks to a file.
        sys.stdout = sys.stderr = open(DATA_DIR / "archer.log", "a", buffering=1, encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s:    %(name)s: %(message)s")

    if args.startup and settings.startup_delay:
        time.sleep(settings.startup_delay)
    sock = _listen()
    if sock is None:
        _fail()
    from archer import tls
    # Loads the CA bundle (~0.4 s) alongside the app's imports instead of after them.
    threading.Thread(target=tls.context, daemon=True).start()
    # Open the window now, while the server starts: the browser takes about as long to launch,
    # and its first request waits in the socket's queue until the app answers it.
    view = _first_view(args)
    if view:
        _show(view)
    if sys.platform == "win32":
        from archer import autostart
        try:
            autostart.refresh()  # older entries lack --startup; keep the registry pointing at this install
        except OSError:
            pass
    from archer import companion
    docker_boot = None
    if _manages_docker():
        from archer import autostart
        # Archer starts Docker hidden; Docker's own sign-in entry would open its dashboard too.
        companion.quiet_docker_desktop(take_over_autostart=sys.platform == "win32" and autostart.enabled())
        # In the background: Docker can take a while, and the fallback covers searches meanwhile.
        docker_boot = threading.Thread(target=companion.start_container, args=(settings.searxng_container,), daemon=True)
        docker_boot.start()
    elif _manages_native_searxng():
        # In the background for the same reason: the first search can fall back meanwhile.
        threading.Thread(target=companion.searxng_start, daemon=True).start()

    import uvicorn
    # Search URLs contain private queries; do not write them into the tray app's log.
    server = uvicorn.Server(uvicorn.Config("archer.app:app", host=settings.host, port=settings.port, access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()

    if args.no_tray:
        if not _wait_started(server, thread):
            _fail()
        try:
            while thread.is_alive():  # short joins so Ctrl+C reaches us on Windows
                thread.join(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
        companion.searxng_shutdown()
    else:
        restart = threading.Event()
        if not _run_tray(server, thread, restart, notify=args.startup and settings.startup_notify,
                         docker_boot=docker_boot):
            _fail()
        thread.join(timeout=5)
        # Before relaunching, so the new process can take SearXNG's port.
        companion.searxng_shutdown()
        if restart.is_set():
            # A fresh process re-reads .env, including a new port or address.
            subprocess.Popen([*_relaunch_command(), "--no-window"], close_fds=True,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            return
    thread.join(timeout=5)


def _searxng_running() -> bool:
    from archer import companion
    return companion.searxng_running()


def _fail() -> None:
    print(f"Archer could not start on port {settings.port}; is another program using it?", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    main()
