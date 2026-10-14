"""Launcher: runs the server with a tray icon, or opens the page if Archer is already running."""

import argparse
import json
import logging
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from archer.config import DATA_DIR, settings


def _already_running() -> bool:
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


def _run_tray(server: uvicorn.Server, setup_url: str) -> None:
    import pystray

    from archer import autostart
    from archer.icon import make_icon

    def toggle_autostart(icon, item):
        autostart.set_enabled(not autostart.enabled())

    def quit_app(icon, item):
        server.should_exit = True
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open Archer", lambda: webbrowser.open(settings.base_url + "/"), default=True),
        pystray.MenuItem("Helium setup", lambda: webbrowser.open(setup_url)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start with Windows", toggle_autostart, checked=lambda item: autostart.enabled()),
        pystray.MenuItem("Quit", quit_app),
    )
    pystray.Icon("archer", make_icon(64), f"Archer · {settings.base_url}", menu).run()


def main() -> None:
    parser = argparse.ArgumentParser(prog="archer")
    parser.add_argument("--open", action="store_true", help="open the setup page in the default browser")
    parser.add_argument("--no-tray", action="store_true", help="run in the foreground without a tray icon")
    args = parser.parse_args()

    setup_url = f"{settings.base_url}/setup"
    if _already_running():
        if args.open:
            webbrowser.open(setup_url)
        return

    DATA_DIR.mkdir(exist_ok=True)
    if sys.stdout is None:
        # pythonw has no console; send uvicorn's logging and any tracebacks to a file.
        sys.stdout = sys.stderr = open(DATA_DIR / "archer.log", "a", buffering=1, encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s:    %(name)s: %(message)s")

    server = uvicorn.Server(uvicorn.Config("archer.app:app", host=settings.host, port=settings.port))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if not _wait_started(server, thread):
        print(f"Archer could not start on port {settings.port}; is another program using it?", file=sys.stderr)
        sys.exit(1)
    if args.open:
        webbrowser.open(setup_url)

    if args.no_tray:
        try:
            while thread.is_alive():  # short joins so Ctrl+C reaches us on Windows
                thread.join(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
    else:
        _run_tray(server, setup_url)
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
