"""Start-with-Windows toggle, stored in the per-user Run registry key (no admin needed)."""

import sys
import winreg
from pathlib import Path

_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_NAME = "Archer"


def _command() -> str:
    scripts = Path(sys.executable).parent
    launcher = scripts / "archerw.exe"
    if launcher.exists():
        return f'"{launcher}"'
    return f'"{scripts / "pythonw.exe"}" -m archer'


def enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY) as key:
            winreg.QueryValueEx(key, _NAME)
        return True
    except FileNotFoundError:
        return False


def set_enabled(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, _NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, _NAME)
            except FileNotFoundError:
                pass
