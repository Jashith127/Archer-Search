"""Launcher: runs the server with a tray icon, or opens the companion app if Archer is already running.

- Started from the Start menu or a shortcut: opens the companion app (setup on first run).
- Started by Windows at sign-in (`--startup`): waits the configured delay, optionally starts
  SearXNG (natively or in its Docker container), then does what Settings → Startup says (tray only by default).
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

import uvicorn

from archer.config import DATA_DIR, settings


def _port_free() -> bool:
    # Binding is instant; an HTTP probe of a closed port on Windows waits out its whole timeout.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", settings.port))
            return True
        except OSError:
            return False


def _already_running() -> bool:
    if _port_free():
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{settings.port}/health", timeout=1) as resp:
            return json.load(resp).get("app") == "archer"
    except (OSError, ValueError):
        return False


def _wait_started(server: uvicorn.Server, thread: threading.Thread) -> bool:
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


def _run_tray(server: uvicorn.Server, thread: threading.Thread, restart: threading.Event,
              view: str | None, notify: bool) -> bool:
    """Show the tray icon at once, while the server is still importing; returns False if it never started."""
    import pystray

    from archer import autostart
    from archer.icon import make_icon

    failed = threading.Event()

    def toggle_autostart(icon, item):
        autostart.set_enabled(not autostart.enabled())

    def stop(icon):
        server.should_exit = True
        icon.stop()

    def toggle_searxng(icon, item):
        from archer import companion
        # In the background: starting can take a while (Docker Desktop most of all).
        action = companion.searxng_stop if companion.searxng_running() else companion.searxng_start
        threading.Thread(target=action, daemon=True).start()

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
        if view:
            _show(view)
        if notify:
            try:
                tray.notify("Archer is running. Search from your address bar.", "Archer")
            except Exception:  # notifications are a nicety; some shells refuse them
                pass

    def ready(icon):
        icon.visible = True
        threading.Thread(target=boot, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("Open Archer", lambda: _show(_companion_url()), default=True),
        pystray.MenuItem("Search", lambda: webbrowser.open(settings.base_url + "/")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("SearXNG running", toggle_searxng, checked=lambda item: _searxng_running(),
                         visible=lambda item: _searxng_managed()),
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
    if sys.platform == "win32":
        from archer import autostart
        try:
            autostart.refresh()  # older entries lack --startup; keep the registry pointing at this install
        except OSError:
            pass
    from archer import companion
    if companion.searxng_managed():
        # In the background: Docker can take a while, and the fallback covers searches meanwhile.
        threading.Thread(target=companion.searxng_start, daemon=True).start()

    # Search URLs contain private queries; do not write them into the tray app's log.
    server = uvicorn.Server(uvicorn.Config("archer.app:app", host=settings.host, port=settings.port, access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    view = _first_view(args)

    if args.no_tray:
        if not _wait_started(server, thread):
            _fail()
        if view:
            _show(view)
        try:
            while thread.is_alive():  # short joins so Ctrl+C reaches us on Windows
                thread.join(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
        companion.searxng_shutdown()
    else:
        restart = threading.Event()
        if not _run_tray(server, thread, restart, view, notify=args.startup and settings.startup_notify):
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


def _searxng_managed() -> bool:
    from archer import companion
    return companion.searxng_managed()


def _searxng_running() -> bool:
    from archer import companion
    return companion.searxng_running()


def _fail() -> None:
    print(f"Archer could not start on port {settings.port}; is another program using it?", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    main()
