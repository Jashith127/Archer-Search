# Archer

A personal search engine that runs on your machine and serves as Helium's default search engine. Links and cards come first, and AI is added only as much as you choose. See [concept-note.md](concept-note.md) for the design.

## Install (one time)

Double-click **`install.cmd`**. It:

1. installs dependencies with `uv`,
2. adds an **Archer** shortcut to the desktop and Start menu,
3. starts Archer (a tray icon appears) and opens **Archer Settings** at Browser setup, with copy buttons for each value.

Then add Archer in Helium (Settings → Search engine → Manage search engines and site search → Site search → **Add**):

| Field | Value |
|---|---|
| Name | `Archer` |
| Shortcut | `ar` |
| URL with %s in place of query | `http://archer.localhost:4747/search?q=%s` |
| Suggestions URL with %s in place of query | `http://archer.localhost:4747/suggest?q=%s` |

Click **Add as default**.

Archer also shows suggestions in its home and results search boxes. Type at least
two characters, then use the arrow keys and Enter, or click a suggestion. Escape
restores what you typed; Tab dismisses the list. Searches retain the selected result
type (Images, News, or Videos).

Suggestions use your SearXNG instance's configured autocomplete provider (DuckDuckGo
by default). Partial queries are sent to that provider through SearXNG, including
before you submit a search. Responses are cached briefly in memory, with no saved
search history. If autocomplete is disabled in SearXNG or unavailable, searches
still work. The upstream timeout can be set with `ARCHER_SUGGEST_TIMEOUT` (default
1.5 seconds).

## Daily use

- The **Archer** shortcut starts it in the tray. If it's already running, the shortcut opens Settings.
- The tray menu has **Open Archer**, **Settings**, **Start with Windows**, **Restart** and **Quit**. Searches in Helium won't work while Archer isn't running.
- `uninstall.cmd` stops Archer and removes its shortcuts and the autostart entry.

## Settings

Open **Settings** from the tray (it opens as its own window), the sliders icon on the results
page, or `http://archer.localhost:4747/settings`.

- **General:** home location, country, currency and the contact Wikipedia requires.
- **Search:** SafeSearch, results language, the SearXNG address (with a **Test** button) and suggestion timing.
- **AI:** cloud AI on or off, Classic or Balanced by default, whether Balanced answers start small,
  the API key and model, and spending limits with today's usage.
- **Connections:** the optional TMDB key for where-to-watch listings.
- **Browser:** the values to paste into Helium, with copy buttons.
- **System:** Start with Windows, port and address, restart, data folders and clearing AI conversations.

Changes are saved to `.env` in the Archer folder, keeping its comments, and apply immediately.
Only the port and address need a restart, which Settings can do for you. Keys are never sent
back to the page: it shows whether one is set and its last four characters. **Test** buttons
check a key or address through free endpoints (a model list, TMDB's configuration, SearXNG's
`/config`), never a paid AI call. A value set as a system environment variable overrides `.env`
and appears read-only.

## AI search

Paste a DeepSeek API key in **Settings → AI**; it works right away. The default model is
`deepseek-flash`. Keys stay on this computer; never paste one into a query or commit `.env`.

On the first page of **All** results, choose **Balanced** or **AI** in the mode switch, or use
the **Quick answer** / **AI Mode** prompt shown above question-like searches. Balanced answers
appear as a card above the results, with images from the same search; they start small
(change that in Settings → AI) and expand with **Show full answer**. **Go deeper in AI Mode**
continues in a full-page workspace with research steps, a source rail, comparisons and
follow-ups. **All results** returns to the results page without stopping the conversation;
**Classic** stops it. Open `/ai` to begin a new conversation. Visiting or reloading that page
does not start a paid generation; a completed session is restored from memory without another
AI call. The first use asks you to turn on cloud AI. The provider receives the question,
selected public evidence, relevant card data and follow-up context. Turn on **Answer questions
automatically** only if you want eligible questions answered after results load.
Classic remains the initial default; utilities and simple entity cards don't need AI.

AI mode can search for missing evidence, retrieve source-linked image/video galleries,
read public pages, resolve and compare entities,
check forecasts and nearby places, verify regional viewing providers when a TMDB key is
configured, and calculate arithmetic, unit conversions and loan payments. It can request
forecast, place, entity, provider and editable calculation cards, alongside cited comparison
tables. **Compare sources** lets you choose two to four retrieved sources. Follow-ups
reuse the research evidence. **Stop** cancels pending work; already incurred provider usage
may still be billed. Unknown citation IDs stop the answer. A statement with an unsupported
literal quantity is omitted while other supported sections continue. These checks do not
replace checking that a source supports the full claim.

The full view includes a conversation column, source sidebar, retained follow-up turns and
a pinned multiline composer with source inspection, Research/Quick answer depth, Enter to send,
and Shift+Enter for a new line. Rounded pixel dots indicate gathering, reading and writing,
then settle on completion. Reduced-motion settings disable their animation.

Ordinary explanations use one answer call. Tool-dependent research defaults to one planning
round plus an answer; `ARCHER_AI_PLANNING_ROUNDS` allows 0–2 rounds. Only tools relevant to
the task are advertised. The final answer can use up to 5,000 output tokens (configurable
through `ARCHER_AI_ANSWER_TOKENS`); broad questions are prompted for developed sections,
lists or comparisons rather than a compact summary. Longer passages from up to ten selected
sources are provided to the answer, while the planner sees a small source inventory.

Research allows three additional web searches, two media searches,
six page reads, twelve tool calls and 75 seconds per run. Compact answers have a 12-second
deadline, one model call and up to two page reads; weather questions can also use the
existing weather adapter. Token budgets are enforced before provider calls. Aggregate
usage reservations persist in `data/ai-usage.json`; the default daily cap is 500,000 tokens,
resetting at 00:00 UTC. For the official `deepseek-flash` endpoint, built-in cache-aware
peak-price ceilings activate dollar caps and show a conservative cost estimate alongside
reported tokens and model-call count. Off-peak bills can be lower. Rates were reviewed on
14 October 2026 against [DeepSeek's pricing reference](https://api-docs.deepseek.com/quick_start/pricing/).
Set both `ARCHER_AI_INPUT_PRICE` and `ARCHER_AI_OUTPUT_PRICE` to override these rates.
Unpriced custom endpoints retain token caps and show cost as unavailable. Conservative reservations remain
for calls interrupted before final usage is reported.

Preferences live in `data/ai-preferences.json`. Query, source and answer contents stay in
bounded memory sessions and expire after 30 minutes (cleaned every 30 seconds); they are
not saved as search history. Choosing Classic disables a saved automatic Balanced default.
Turning cloud AI off stops active runs. Ordinary `GET /search` and suggestions never call
the paid provider. Automatic generation requires a saved opt-in and same-origin POST.

The public-page reader blocks private/local destinations and pins validated DNS addresses
through connection and TLS, checks redirects, and limits page size and time. It reads text
and HTML, including bounded gzip/deflate responses; PDFs, unsupported compression,
login-only and JavaScript-only pages fall back
to clearly labeled search excerpts. Source pages are untrusted data, and generated UI is
limited to Archer's validated components rather than arbitrary HTML or JavaScript.

See [the design](ai-search-design.md) for rationale. Optional GLiNER2 evaluation and
personal routing calibration remain future work; the shipped router uses conservative
rules and explicit actions.

## Development

```bash
uv sync
uv run archer --no-tray      # foreground server, Ctrl+C to stop
uv run pytest
```

Offline browser checks (set `NODE_PATH` to a Playwright installation if needed):

```powershell
# Terminal 1: isolated sources/provider; temporary settings; no paid or public calls
uv run python tests/ai_browser_server.py
# Terminal 2
node tests/ai.browser.cjs
```

Settings and API keys live in `.env` (see `.env.example`); the Settings app edits the same file. Logs from the tray app are written to `data/archer.log`.

**Set a contact** (Settings → General, or `ARCHER_CONTACT` in `.env`) to an email or URL. Wikipedia and Wikidata require contact details in the User-Agent. Without them, entity panels get rate-limited or refused.
