"""Start-with-Windows toggle, stored in the per-user Run registry key (no admin needed).

The entry passes --startup so the launcher knows Windows started it: it then honours the
sign-in delay and the "when Archer starts" choice instead of opening the companion app.
"""

import sys
import winreg
from pathlib import Path

_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_NAME = "Archer"


def _command() -> str:
    scripts = Path(sys.executable).parent
    launcher = scripts / "archerw.exe"
    if launcher.exists():
        return f'"{launcher}" --startup'
    return f'"{scripts / "pythonw.exe"}" -m archer --startup'


def _current() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY) as key:
            return str(winreg.QueryValueEx(key, _NAME)[0])
    except FileNotFoundError:
        return None


def enabled() -> bool:
    return _current() is not None


def set_enabled(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, _NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, _NAME)
            except FileNotFoundError:
                pass


def refresh() -> None:
    """Rewrite an existing entry that points elsewhere or predates --startup. Never adds one."""
    value = _current()
    if value is not None and value != _command():
        set_enabled(True)
