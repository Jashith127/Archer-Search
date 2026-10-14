"""Companion app on a fresh, throwaway configuration, for checking setup and settings in a browser.

Run: uv run python tests/companion_browser_server.py  (then open http://localhost:4790/app/welcome)
Settings are read from and saved to a temporary .env, and setup progress, AI preferences and usage to
a temporary folder, so the real .env, keys and data folder are never touched. "Start with Windows" is
simulated in memory instead of writing the registry. Archer's defaults apply, as on a new install.
"""
import sys
import tempfile
from pathlib import Path

import uvicorn

import archer.app
from archer import companion
from archer.app import app
from archer.config import Settings, settings
from archer.settings_service import SettingsService

folder = Path(tempfile.mkdtemp(prefix="archer-companion-"))
env = folder / ".env"
env.write_text("# Throwaway settings for the companion fixture\n", encoding="utf-8")
companion.STATE_FILE = folder / "companion.json"
archer.app.DATA_DIR = folder  # the AI service keeps preferences and spending here
if sys.platform == "win32":
    from archer import autostart
    startup = {"on": False}
    autostart.enabled = lambda: startup["on"]
    autostart.set_enabled = lambda on: startup.update(on=on)
    autostart.refresh = lambda: None
fresh = Settings(_env_file=env)
for name in type(fresh).model_fields:
    setattr(settings, name, getattr(fresh, name))
app.state.settings_service = SettingsService(env, live=settings)

if __name__ == "__main__":
    print(f"Throwaway settings in {folder}")
    uvicorn.run(app, host="127.0.0.1", port=4790, access_log=False)
