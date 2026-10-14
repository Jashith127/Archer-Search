# Archer

Personal AI search engine served on `http://archer.localhost:4747`, used as the default search engine in Helium (a Chromium browser) on Windows. Design and build order are in `concept-note.md`. Keep that note current when a design decision changes.

## Layout
- `src/archer/app.py`: FastAPI routes (`/search` renders the results page, `/suggest`, the lazy `/api/strip/{images|news|videos}`, `/api/similar` (side-panel similar images), `/api/works`, `/api/weather`, `/settings`, `/health`; `/setup` redirects to `/settings#browser`) and template filters
- `src/archer/views.py`: builds the page view model (SearXNG results plus one hero per query type, discussions, lazy-module flags)
- `src/archer/query.py`: query typing (utility, weather, time and define patterns; entity candidate)
- `src/archer/utilities.py`: local instant answers (calculator, units, colour, timestamp, random, currency parsing)
- `src/archer/imagerank.py`: image relevance re-ranking (title/URL term overlap, model numbers weighted, dedupe, drops tiny images and svg icons) and the "similar images" query; used by the Images tab, the strip and `/api/similar`
- `src/archer/searxng.py`: SearXNG JSON client with a 10-minute cache
- `src/archer/sources/`: `wiki.py` (Wikipedia + Wikidata entity panels by kind and person subtype, spelling suggestions, filmography via SPARQL, Retry-After backoff), `weather.py` (Open-Meteo weather, sun, geocoding), `dictionary.py` (Wiktionary), `crypto.py` (CoinGecko), `translate.py` (MyMemory), `places.py` (Photon geocoding, Overpass with a Photon fallback; loaded lazily via `/api/places`), `aimodels.py` (AI model panels from Epoch AI's benchmark zip, cached in `data/` and refreshed daily; matched locally before the Wikipedia entity lookup)
- Adding a query type: a pattern in `query.py` (or a utility in `utilities.py`), a builder in `views.py` (`LOCAL_KINDS` or `ASYNC_HEROES`), a `templates/heroes/<kind>.html`, styles in `archer.css`, and a mocked test in `tests/test_heroes.py`
- `src/archer/templates/`: Jinja templates; `heroes/<kind>.html` holds one hero per query type
- `src/archer/static/`: `archer.css` (dark theme tokens at the top) and `archer.js` (lazy modules, converters, clock; DOM-only, no innerHTML)
- `src/archer/__main__.py`: launcher. Runs uvicorn in a thread plus a pystray tray icon; if Archer is already running, it only opens the page. It runs windowless as `.venv\Scripts\archerw.exe`.
- `src/archer/config.py`: settings, loaded from `ARCHER_*` env vars and `.env`
- `src/archer/autostart.py`: the tray's "Start with Windows" toggle (HKCU Run key)
- Settings app: `settings_service.py` (field schema, validate-then-write `.env`, live apply, masked secrets, free connection tests), `settings_routes.py` (`/api/settings*`, guarded like AI POSTs), `envfile.py` (comment-preserving `.env` edits), `window.py` (opens a page as a Chromium `--app` window), `templates/settings.html`, `static/settings.css`, `static/settings.js`. A new setting needs a field in `config.py` and a `Spec` in `settings_service.py`.
- AI UI: `templates/ai_panel.html` (one panel; Balanced card or AI Mode via `body.ai-fullview`, turns cloned from a `<template>`), AI section of `archer.js` and `archer.css`
- `scripts/install.ps1`, `install.cmd`: one-click install (uv sync, shortcuts, launch)

## Commands
- `uv sync`: install
- `uv run pytest`: tests
- `uv run archer --no-tray`: run in the foreground for development

## Rules
- Bind to 127.0.0.1 only, and keep `TrustedHostMiddleware` in place. The public host must stay a `*.localhost` name.
- Never return a full API key to the browser; Settings shows masked hints only. Tests must use a temporary `.env`, never the real one.
- A plain `GET /search` must never trigger a paid AI call. AI generation goes through a POST that checks `Sec-Fetch-Site: same-origin`.
- Keep the `/suggest` response in OpenSearch JSON format: `[query, [completions], ...]`.
- The results page sends a strict CSP (`script-src 'self'`) and `no-referrer`. Keep scripts in `static/archer.js`, not inline.
- Upstream engines and Wikimedia rate-limit quickly. Don't burst-test live queries; prefer the mocked tests in `tests/`.
- In Jinja, avoid dict keys that shadow dict methods (`pop`, `items`, `keys`, `values`, `get`, `update`).
