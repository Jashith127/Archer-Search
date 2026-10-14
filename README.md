# Archer

A personal search engine that runs on your machine and serves as Helium's default search engine. Links and cards come first, and AI is added only as much as you choose. See [concept-note.md](concept-note.md) for the design.

## Install (one time)

Double-click **`install.cmd`**. It:

1. installs dependencies with `uv`,
2. adds an **Archer** shortcut to the desktop and Start menu,
3. starts Archer (a tray icon appears) and opens the setup page in your browser.

Then add Archer in Helium (Settings → Search engine → Manage search engines and site search → Site search → **Add**):

| Field | Value |
|---|---|
| Name | `Archer` |
| Shortcut | `ar` |
| URL with %s in place of query | `http://archer.localhost:4747/search?q=%s` |
| Suggestions URL with %s in place of query | `http://archer.localhost:4747/suggest?q=%s` |

Click **Add as default**.

## Daily use

- The **Archer** shortcut starts it. If it's already running, the shortcut opens the setup page instead.
- To have Archer start automatically when you log in, tick **Start with Windows** in the tray icon's menu.
- **Quit** in the tray menu stops it. Searches in Helium won't work until it's running again.
- `uninstall.cmd` stops Archer and removes its shortcuts and the autostart entry.

## Development

```bash
uv sync
uv run archer --no-tray      # foreground server, Ctrl+C to stop
uv run pytest
```

Settings and API keys go in `.env` (see `.env.example`). Logs from the tray app are written to `data/archer.log`.

**Set `ARCHER_CONTACT`** in `.env` to an email or URL. Wikipedia and Wikidata require contact details in the User-Agent. Without them, entity panels get rate-limited or refused.
