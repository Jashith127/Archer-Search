"""Open an Archer page as its own app window, using a Chromium browser's --app mode.

Helium is preferred (it is Archer's browser), then Edge, which every Windows 11 machine has,
then Chrome. Without any of them the page opens in the default browser instead.
"""
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path


def _candidates() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    programs = [Path(os.environ.get(name, "")) for name in ("PROGRAMFILES", "PROGRAMFILES(X86)")]
    paths = [base / "imput" / "Helium" / "Application" / "chrome.exe" for base in [*programs, local]]
    paths += [base / "Microsoft" / "Edge" / "Application" / "msedge.exe" for base in programs]
    paths += [base / "Google" / "Chrome" / "Application" / "chrome.exe" for base in [*programs, local]]
    for name in ("helium", "msedge", "chrome", "chromium"):
        found = shutil.which(name)
        if found:
            paths.append(Path(found))
    return paths


def open_app(url: str, size: tuple[int, int] = (1120, 820)) -> None:
    if sys.platform == "win32":
        for path in _candidates():
            if path.is_file():
                try:
                    subprocess.Popen([str(path), f"--app={url}", f"--window-size={size[0]},{size[1]}"],
                                     close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
                    return
                except OSError:
                    continue
    webbrowser.open(url)
