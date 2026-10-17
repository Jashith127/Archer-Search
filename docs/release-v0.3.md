Archer v0.3 brings a redesigned installer that no longer needs Docker, search history with personalization that stays on your computer, and video game panels.

### Install

Download **ArcherSetup.exe** below and run it on Windows 10 or 11. Setup downloads Python and dependencies automatically. You need an internet connection and at least 1 GB of free space. Docker is no longer needed.

**Updating from v0.2:** run the new setup. It replaces Archer's program files but keeps your settings (`.env`) and your `data/` folder.

### Added

- **Search history and personalization**, kept in `data/history.sqlite3` and never sent anywhere:
  - A **history page** (`/history`) with searches by day, the results you opened, topics grouped by meaning, and search by meaning, with matches listed closest first.
  - **Ranking** that blends the provider's order with how closely each result matches your query and which sites you open. Each result's menu can raise, lower or block its site.
  - **You found these before** and **Your earlier searches like this** on the results page, and your own searches first in suggestions.
  - Meaning is matched locally with a 30 MB embedding model (downloaded once), a local Ollama model, or plain word matching. Pause or clear history any time.
- **Video game panels:** Steam price in your currency, reviews, current players, screenshots and PC requirements, plus PC deals from CheapShark.
- **Docker control in the tray:** where Archer manages a SearXNG container, the tray has Start/Stop Docker, and Docker Desktop starts hidden.
- **Search setup detection:** the companion app reads your browser profile to tell whether Archer is your default engine with suggestions.

### Changed

- **New installer:** a frameless setup window with a hero, a single setup screen and one install view. Choose SearXNG on this PC, DuckDuckGo, or Google or Brave with an optional API key. A done screen offers a first search, Archer's own setup and the browser steps. If WebView2 is missing, it falls back to a Chromium app window.
- **No Docker for SearXNG:** the installer sets SearXNG up natively. The Docker check and system checks are gone.
- SearXNG run natively is Archer's own process: it starts when Archer starts, stops when Archer exits, and the tray can start or stop it. An install that still names a Docker container keeps using it, and the tray keeps Start/Stop Docker.
- **Uninstall:** Windows **Installed apps → Archer → Uninstall** opens the same window and removes Archer and SearXNG, keeping your settings and history unless you choose otherwise.
- **AI Mode** gets a larger input budget and longer conversations: older turns are summarised to fit, and Ollama is held to its context window.
- The AI model card and the rate-limit banner are restyled, and the home page is refreshed.

### Performance

- Archer pins OpenBLAS to one thread, which avoids committing about 350 MB of memory.

### Verification

- **Tests:** 378 Python tests pass.
- **Native SearXNG:** verified on Windows 11. It downloads the pinned release, builds its own Python 3.12, writes `settings.yml`, answers `/healthz` and serves real search results on port 8888; stopping it releases the port.
- **Installer:** `ArcherSetup.exe` carries the native SearXNG module and its Windows shims, and a dry run completes every step, SearXNG included.
- **Not yet verified:** a clean-machine install.
- This executable is unsigned.

`SHA256SUMS.txt` contains the SHA-256 checksum for the installer.
