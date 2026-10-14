Archer's first Windows installer release: a personal search engine on your computer, with optional AI and a guided companion setup.

### Install

Download **ArcherSetup.exe** below and run it on Windows 10 or 11. Setup downloads Python and dependencies automatically. An internet connection and at least 1 GB of free space are required. Docker Desktop is optional: use it for a local SearXNG instance, or start with DuckDuckGo.

### Included

- Custom installer with a redesigned welcome screen, a full-background pixel field, morphing artwork in the clear margin, and a compact step indicator.
- Install location and startup choices, desktop and Start menu shortcuts, and Windows Installed apps integration.
- Guided search, AI, personal preferences and browser setup.
- DuckDuckGo, SearXNG, Google and Brave search; optional cloud AI or local Ollama.
- Companion settings, spending controls, Docker startup and scoped uninstall behavior.

![Archer installer](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.1/docs/screenshots/installer-welcome.png)

![Configure Archer](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.1/docs/screenshots/installer-configure.png)

The attached screenshots were captured using the installer's dry-run preview and a fresh companion configuration.

### Verification

254 Python tests pass. The installer compiles with the Windows .NET Framework compiler, and its dry-run browser flow reaches configuration without JavaScript errors. The compact layout was checked for horizontal overflow. A clean-machine installation has not been verified. This v0.1 executable is unsigned.

The separate AI browser regression reached its cancellation check but timed out waiting for the Stop control; that browser suite is not fully passing in this release.

`SHA256SUMS.txt` contains the SHA-256 checksum for the installer.
