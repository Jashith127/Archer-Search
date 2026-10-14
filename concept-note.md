# Concept note: Archer, a personal AI search engine

## Summary
Archer is a self-hosted search engine you set as Helium's default. It works like a normal search engine with useful cards. AI is added only as much as you've chosen in settings, and you can switch to bounded tool-driven research with one click. Conservative local rules decide when an automatic compact answer is useful; DeepSeek writes cited answers and calls Archer's tools. GLiNER2 remains an optional routing experiment.

Archer runs as a small Windows tray app that serves the engine on `http://archer.localhost:4747`. You fill in Helium's "Add search engine" dialog once, using `/search?q=%s` and `/suggest?q=%s`.

### Implementation status · 14 October 2026

AI search is implemented in the workspace. See [AI search design](ai-search-design.md) for
the refined architecture and [README](README.md#ai-search) for setup and limits. Classic
remains the initial default. Balanced is opt-in; research is always explicit. Generation
starts only through same-origin, CSRF-protected POSTs, after the ordinary results render.

AI mode has native tools for web search, public-page reading, Wikipedia/Wikidata entities,
image/video retrieval, weather, OSM places, regional watch providers, deterministic calculations and loan payments.
It can request structured forecast/place/entity/provider/calculation UI, compare sources
in cited tables, and continue with follow-ups. Research is bounded by time, searches,
pages, tool calls, tokens and optional configured dollar caps. Source limitations remain
visible; missing evidence does not become an invented result. An editable multiline
composer uses the app's dark surfaces and a compact source/model/depth toolbar.

AI mode now opens a full-page reading workspace with a source sidebar and pinned composer.
Follow-up turns remain visible, and cached session restores make no new model calls. Ordinary
explanations use one model call; research needing tools defaults to one lean planning round
plus an answer. Research has a 5,000-token answer ceiling and a 75-second run deadline;
it is prompted for developed, useful sections rather than a summary. Media is retrieved from
Archer's existing image/video search instead of guessed URLs. Gzip/deflate pages are read with
strict compressed/decompressed limits. Source failures appear in collapsed notes. Built-in
Flash pricing ceilings, reported token usage and model-call counts make spending visible.

Archer's existing rounded-square matrix is also the research activity language: cells
gather while finding evidence, scan while reading, soften while writing and settle when
done. Text status accompanies the animation, and reduced motion disables it.

The detailed GLiNER2 thresholds, automatic navigation, inferred correction learning and
historical price/latency estimates below are original planning material, not implemented
behavior or measured guarantees. The refined design uses explicit feedback before any
future classifier training. Page reading reuses evidence; model computation still takes time.

## Problem
- Google is cluttered with ads and SEO spam. Its useful features (knowledge panels, where-to-watch, instant answers) are hard to get anywhere else.
- AI-first search tools are slow and use the LLM even when you only wanted a link.
- No tool lets you set how much AI you want by default and still lets you go fully agentic when you need it.

---

## The three modes

| Mode | What you see | AI cost per query |
|---|---|---|
| **Classic** *(no AI)* | Links, cards, instant answers. No LLM call at all. | $0 |
| **Balanced** | Links and cards, plus a compact cited explanation for eligible questions. **Investigate further** reuses evidence in AI mode. | Depends on configured model and actual usage |
| **AI mode** *(tool-driven research)* | Plans searches, reads public pages, calls Archer tools, and returns cited answers, tables and useful structured UI. | Depends on tools/model usage; bounded per run |

### How the default works
- **Out of the box, the default is Classic.** It behaves like a clean, ordinary search engine.
- **One setting switches the default to Balanced.** Question-type queries then get a light AI summary on top of the links.
- **AI mode is never a default.** It's always one click away from a switcher at the top of the page (`Classic · Balanced · AI`), or from the Expand button in Balanced.

### What the classifier does
The classifier doesn't choose the mode; your setting does. It decides what happens within that mode:

| Query intent | In Classic mode | In Balanced mode |
|---|---|---|
| Navigational (`github`) | Redirect | Redirect |
| Utility (`100 usd to inr`) | Widget | Widget, no AI summary |
| Entity (`zendaya`) | Knowledge card | Knowledge card, no AI summary |
| Media (`dune part two`) | Where-to-watch card | Where-to-watch card, no AI summary |
| Question (`why do cats knead`) | Links, plus an **"Ask AI"** chip (nothing generated until you click it) | Links plus a compact AI summary |
| Deep task (`compare budget NAS for Plex`) | Links, plus a **"Try AI mode"** chip | Compact AI summary, with AI mode offered |
| *Unclear (low confidence)* | Links only | Links, plus an **"Ask AI"** chip and no summary |

AI text is only generated when your setting calls for it on a confidently routed Question or Deep task, or when you click for it. The classifier never adds an AI call on its own, and an unclear query costs $0 in every mode.

---

## Routing
Queries pass through three layers, and each layer only handles what the previous one couldn't.

**Layer A: the browser (Helium)**
- Typed URLs (`github.com`) go straight to the site.
- Current `/suggest` completions submit searches. Navigation-type website suggestions are planned separately; verify Helium's custom-engine support before adding them.

**Layer B: fixed rules on the server** (under 1 ms)
- Text that looks like a domain, checked against the public suffix list.
- Bangs: `!g`, `!yt`, `!w`.
- Your own shortcuts (`gh`, `mail`).
- A single word that matches a known site, which redirects like "I'm Feeling Lucky".

**Layer C: GLiNER2 intent model** (future experiment; current implementation uses conservative heuristics)
- **Local, small (~205M parameters), runs on CPU.** It works offline and keeps queries on your machine. I estimate ~10–50 ms per short query; benchmark it on your hardware.
- **One pass gives both the intent and the entities.**
  ```
  "where can i watch dune part two"
   → intent: media (0.94) · movie: "dune part two" → TMDB card
  ```
- **What happens at each confidence level.** The thresholds below are starting values kept in config. They're deliberately conservative until real calibration data exists (step 6).

  | Level | Intent score | What happens |
  |---|---|---|
  | Confident | ≥ 0.80 | Route by intent: redirect, card, AI summary or chip. |
  | Unsure | 0.50 – 0.80 | Route by intent, but never redirect (show the top link first instead). Show the mode switcher more prominently. |
  | Low | < 0.50 | Plain search: links in your default mode, no redirect and no AI summary, with an "Ask AI" chip in Balanced. No second model is called. |

- **Entity rescue.** Even with low intent confidence, a card is shown if GLiNER2 scores an entity at ≥ 0.85 *and* the card source returns an exact match (a Wikipedia title or a TMDB title). Otherwise the plain page would hide a card you probably wanted. It never redirects.
- **Learns from you, using two separate signals.** Only real routing errors count as corrections. Asking for more AI on a correctly routed query does not.

  | Signal | Logged as | Example |
  |---|---|---|
  | You leave the route: re-run the same query with `!g` or another bang, back out of a redirect within a few seconds, or dismiss a card | **correction** | `dune` redirected to a site when you wanted the film |
  | You switch modes on an intent that shouldn't need AI (navigational, utility, entity, media) | **correction** | Switching `zendaya` to AI mode suggests it was really a question |
  | Expand, "Ask AI", "Try AI mode", or switching to AI mode on a Question, Deep task or unclear query | **escalation** | You wanted more depth; routing was right |

  Corrections calibrate GLiNER2's thresholds and, later, fine-tune it on your own searches. Escalations tune *defaults* instead, for example whether Balanced should summarise Deep tasks more fully. Logging starts in step 3, so data builds up from day one.

---

## Features

**Cards (no LLM)**
- **Knowledge panel:** Wikipedia summary plus Wikidata facts.
- **Where to watch:** the TMDB watch-providers endpoint, region-aware.
- **Instant answers:** calculator, currency, units, weather, time zones, definitions.

**AI answers (DeepSeek)**
- **Compact explanation (Balanced):** uses results and card evidence, with selective page reads for comparison/current-information queries and a deterministic weather adapter where useful. Validated blocks stream with source references.
- **Investigate further:** reuses the evidence bundle; additional research and generation are separately bounded and take time. Provider prompt-cache savings are not assumed.
- **AI mode:** native tool calls gather the evidence required by the task. Archer validates arguments, provenance and UI components; the model cannot execute arbitrary scripts or network requests.

---

## Results page

Archer renders SearXNG's JSON in its own dark UI. The layout follows Google's landscape orientation: a full-width **hero module** for the query type sits at the top, with the results column and a side panel below it.

**Page skeleton:** header (search box, `All · Images · News · Videos · Maps` tabs, `Classic · Balanced · AI` switcher) → hero → results column (with inline strips) + side panel → related searches → pager → engine status.

### Hero modules by query type

| Query type | Detected by | Example | Hero shows | Data source |
|---|---|---|---|---|
| Person | Wikipedia title match, Wikidata P31 = human | `zendaya` | Name, description, link chips (site, socials, IMDb), image collage, top story, tiles (age, birthplace, spouse/parents, height); **Movies and shows**, **Books** or **Albums** row; About + facts panel | Wikipedia, Wikidata, SearXNG images/news |
| Film | Wikidata film types | `dune part two` | Poster, top stories, tiles (released, runtime, director, genre); budget, box office, cast in panel | Wikipedia, Wikidata |
| TV series | Wikidata TV types | `the bear tv series` | Poster, tiles (years aired, seasons, episodes, network) | Wikipedia, Wikidata |
| Book | Wikidata literary work | `atomic habits` | Cover, tiles (author, published, pages, genre) | Wikipedia, Wikidata |
| Music | Band, album or song | `coldplay` | Collage, tiles (formed, genre, label, members), Albums row | Wikipedia, Wikidata |
| Company / organisation | Business/organisation types | `infosys` | Tiles (founded, CEO, HQ, employees, revenue), founders | Wikipedia, Wikidata |
| Place | Place types or coordinates | `bengaluru` | Collage, top story, tiles (country, population, area) + live weather tile | Wikipedia, Wikidata, Open-Meteo |
| Species | Wikidata taxon | `snow leopard` | Tiles (scientific name, conservation status, parent taxon) | Wikipedia, Wikidata |
| Other entity | Any other title match | `fitgirl` (from `who is fitgirl`) | Generic tiles (since, country, type) | Wikipedia, Wikidata |
| Weather | `weather X`, `X forecast`, `will it rain` | `weather bengaluru` | Now (temp, condition, feels like, humidity, wind, sun), 24-hour bars (temperature + rain chance), 7-day row | Open-Meteo |
| Local time | `time in X`, `X time` | `time in london` | Live ticking clock, date, zone, offset, difference from you | Open-Meteo geocoding + tz database |
| Dictionary | `define X`, `X meaning` | `define serendipity` | Word, pronunciation (when available), meanings by part of speech with examples | Wiktionary |
| Currency | SearXNG currency answer | `100 usd to inr` | Big result, rate, live two-way converter | SearXNG currency engine |
| Unit conversion | `N unit in unit` | `5 feet in cm` | Result, formula, live two-way converter (length, mass, volume, speed, area, data, time, temperature) | Local |
| Calculator | Arithmetic, `% of`, sqrt/sin/log | `15% of 2400` | Expression and result, copy | Local (safe AST eval) |
| Colour | `#hex`, `rgb()`, `color X` | `#1a73e8` | Swatch, HEX/RGB/HSL with copy | Local |
| Unix timestamp | 10/13-digit epoch, `unix time` | `1760000000` | UTC and your local time | Local |
| Random | `flip a coin`, `roll a die`, `random number between A and B` | `flip a coin` | Result with "again" button (crypto RNG) | Local |
| Person subtypes | Occupation or team/office claims | `virat kohli`, `narendra modi`, `satya nadella` | Athlete: team, position, number. Politician: current office, party. Executive: organisation, net worth. Writer: notable work | Wikidata |
| Country | Wikidata country types | `india` | Capital, population, currency, head of government; languages, area, calling code | Wikidata |
| Chemical / element | Element or compound | `sodium chloride`, `oxygen` | Symbol and atomic number or formula (subscripted), molar mass, melting/boiling point, density | Wikidata |
| Disease | Disease types | `dengue` | Symptoms, specialty, how it spreads, treatment; "not medical advice" note | Wikidata |
| Landmark | Attractions, monuments, natural features | `mysore palace` | Location, built, architect, height, heritage status; live weather tile | Wikidata, Open-Meteo |
| Software | Software, languages, OSes, browsers | `python` | Developer, first release, latest version, licence; source-code link | Wikidata |
| Holiday | Holidays and festivals | `diwali` | Date rule, religion, where observed | Wikidata |
| Crypto | Coin name/ticker + price, or `btc to usd` | `bitcoin price` | Price (₹ uses lakh/crore grouping), 24 h and 7 d change, 7-day chart, market cap, volume, highs, all-time high, supply, converter | CoinGecko |
| AI model | Name of a model in Epoch AI's capability index, optionally framed (`gpt 5 release date`, `openai o3`); very short names like `o3` need the framing word | `claude opus 4.5` | Release date, developer, family, open/closed weights, capability rank, previous and next model in the line; tabbed bar chart comparing it with its earlier versions and the strongest models from other labs released around the same time, on the Epoch Capabilities Index plus up to four well-known benchmarks (GPQA Diamond, SWE-bench Verified, FrontierMath, HLE…) | Epoch AI (CC BY 4.0), zip cached in `data/` and refreshed daily |
| Sunrise / sunset | `sunset X`, `X sunrise` | `sunset mumbai` | Today's time, plus sunrise, sunset and daylight for today and tomorrow | Open-Meteo |
| Translate | `X in <language>`, `translate X to Y`, `how do you say X in Y` (not `movies in hindi`) | `thank you in kannada` | Detected source → translation, alternatives, copy | MyMemory |
| Local places | `<category> near me`, `<category> in <area>` (cafés, ATMs, pharmacies, hospitals… 40+ types) | `cafes in indiranagar` | Up to 8 places: distance, cuisine/brand, address, opening hours, website/call/map; "Open map". Loads after the page. A misspelled area is corrected from the results' own wording ("indranagar" → Indiranagar). Same-named areas resolve to the one nearest home. If Overpass is overloaded, a quicker list without hours comes from Photon, and a clear message shows if both fail | OpenStreetMap (Photon + Overpass, two servers raced) |
| Timer / stopwatch | `timer 10 min`, `5 minute timer`, `stopwatch` | `timer 10 min` | Big countdown or stopwatch, presets, alarm beep | Local (JS) |
| Metronome | `metronome`, `120 bpm` | `metronome 90` | BPM, slider, accented 4-beat pulse (Web Audio) | Local (JS) |
| EMI / BMI / tip | `emi calculator`, `bmi`, `tip calculator` | `emi calculator` | Live calculator form with results | Local (JS) |
| Equation solver | `solve …`, any linear or quadratic equation in x | `x^2-5x+6=0` | Roots (real or complex) with steps | Local |
| Graph | `y = …`, `graph …`, `plot …` | `y=sin(x)*x` | SVG plot over −10…10 with axes and grid | Local (server-rendered SVG) |
| Package tracking | Tracking-number formats (UPS, USPS, UPU/India Post, DHL/FedEx with "track") | `track EE123456789IN` | Guessed carrier, track on carrier site or 17TRACK | Local |
| Quick answer | Question or general query with a DuckDuckGo instant answer | `why do cats knead` | Featured-snippet card with source | DuckDuckGo via SearXNG |
| Misspelled entity | Wikipedia's spelling suggestion | `zendeya` | "Did you mean Zendaya?" plus the full panel | Wikipedia |

**Not built (no free, keyless data source):** live sports scores, stocks and indices, flight status, election results, shopping and price history, hotels, flights, jobs, showtimes, nutrition. Each needs a paid or keyed API; add one when a key is available.

### Inline modules (no hero needed)

| Module | When | Placement |
|---|---|---|
| Discussions | 2+ results from Reddit, Stack Exchange, Quora, HN | Card group after result 4 |
| In the news | Entity heroes and news-y queries (`latest`, `today`, `news`) | Strip after result 2 |
| Videos | `how to …`, `trailer`, `tutorial` | Strip after result 3 |
| Images | Visual queries (`photos`, `wallpaper`) and people/places | Strip after result 1 |
| Related searches | Every query | Chips at the bottom |

**Images tab.** Results are re-ranked by `imagerank.py` (query terms in title/URL, model numbers weighted 3x, duplicates, tiny images and svg icons dropped). Clicking a tile opens a sticky right-hand panel (large image, source, Visit page / View image, arrow-key navigation) with a Similar images grid below it, fetched from `/api/similar` by searching the picked image's own title. On narrow screens the panel is a full-screen overlay. SearXNG engines `devicons`, `lucide`, `unsplash`, `pexels` and `artic` are removed in its `settings.yml` (icons, stock photos and a host that 403s hotlinks).

Results show a site badge, site name, breadcrumb, title, date, snippet, thumbnail, and **which engines returned the result**, so SearXNG's agreement-based ranking stays visible.

**Visual system.**
- **Type badge:** every card opens with one (icon + kind + context, for example "Person · Athlete" or "Weather · Bengaluru").
- **Entity bento:** fixed-height, laid out as media · top story · facts. The key fact sits in a wide lead tile, the rest in compact tiles, each with an icon. Tiles that don't fit move into About.
- **About:** inside the card, open by default.
  - **Rich panels** (4+ facts) show About as a full-width reading panel under the bento.
  - **Thin panels** (3 facts or fewer, such as a fictional character, a book or a generic topic) drop the tall facts bento. Media sits on the left; on the right, the facts become stat chips on top of a scrollable About panel.
- **Type tags:** an entity's Wikidata types ("fictional human", "television character") show as dashed tag chips beside the link chips, not as a fact tile.
- **Archer's mark:** a 7x7 dot matrix of slightly rounded squares forming the chevron "A", with the bottom row fading out (`templates/pixel_mark.html`). It uses one monochrome ink at stepped opacities. In the results header it sits alone at 32px with no wordmark. A CSS-only wave rises through the cells every 4.8s, and runs faster while the next search loads. The favicon and tray icon use the same bitmap on a dark tile. Dot fields (`dotField` in `archer.js`, a canvas drawn at about 15fps and only while on screen) sit along the bottom of the home page and at the foot of the results page. Each is densest at the bottom and thins and fades toward a curved horizon, which sits lower in the middle than at the edges. Each dot steps between ink levels at random, with an occasional brief bright flash. While the next results page loads, a row of 3px dots on the header's bottom edge runs a stepped scanning band. The wider pixel-grid exploration is in `mockups/brand.html`.
- **Fonts:** DM Sans (primary) and DM Mono (secondary: badges, eyebrow labels, numbers), self-hosted.
- **Link chips:** no Facebook. X shows as its logo only.
- **Flags:**
  - Countries lead with the flag from Wikidata (P41, via Wikimedia Commons).
  - "Country" tiles and currency fields show small ISO flags (flagcdn).
- **Brand logos:** link chips use Simple Icons (CC0), stored locally.
- **Result favicons:** fetched server-side through `/api/favicon/<host>` and cached. The letter badge stays as the fallback.
- **Cache-busting:** CSS and JS URLs carry a content hash (`?v=`), so the browser never pairs new HTML with an old stylesheet.

**Failures are visible.** If a card's data source (Open-Meteo, Wiktionary, MyMemory, CoinGecko) fails, the page says which one instead of silently dropping the card. Entity panels stay quiet on failure, because many general queries aren't entities anyway. Wikimedia timeouts are retried once.

**Speed.** The SearXNG search and the hero lookup run concurrently. Slow extras load after the page is on screen: collage images, the top story, strips, the filmography row and the place weather tile. SearXNG responses are cached for 10 minutes and Wikipedia/Wikidata panels for a day, so reloads don't hit the upstream engines again.

**Typing today vs. later.** Utility, weather, time and definition queries are matched by deterministic patterns (rules layer). Entities are decided by data: a Wikipedia title must match the query. When GLiNER2 arrives (step 2) it replaces the question/how-to/general guesses and feeds entity candidates. The hero modules, keyed by type, stay as they are.

---

## Running it

**The app.** Archer is a Python app (FastAPI + uvicorn) with a tray icon. It listens on `127.0.0.1:4747` only, so nothing else on your network can reach it. Helium uses the name `archer.localhost`, which Chromium-based browsers resolve to your own machine without any hosts-file change.

**One-click start.**
1. Double-click `install.cmd` once. It installs dependencies with `uv`, adds **Archer** shortcuts to the desktop and Start menu, starts Archer and opens Archer Settings at Browser setup.
2. After that, the Archer shortcut is the one click. If Archer is already running, the shortcut opens Settings.
3. To start Archer automatically when you log in, tick **Start with Windows** in the tray menu.

**Adding it to Helium** (Settings → Search engine → Manage search engines and site search → Site search → **Add**):

| Field | Value |
|---|---|
| Name | `Archer` |
| Shortcut | `ar` |
| URL with %s in place of query | `http://archer.localhost:4747/search?q=%s` |
| Suggestions URL with %s in place of query | `http://archer.localhost:4747/suggest?q=%s` |

Then click **Add as default**. Settings → Browser (`/settings#browser`; the old `/setup` redirects there) shows the same values with copy buttons. If Helium won't load `archer.localhost`, use `127.0.0.1` in its place.

**Settings app.** `/settings` is the one place to configure Archer: preferences, API keys, search options, AI limits, startup and restart. The tray opens it as a chromeless window (Helium, then Edge or Chrome in `--app` mode), so it feels like a desktop app without bundling a webview. It edits `.env` in place after validating the candidate file with the same `Settings` loader used at startup, then updates the live settings object, so changes apply without a restart except for the port and address. Keys are shown masked, connection tests use free metadata endpoints, and every change is a same-origin POST with the session's CSRF token, like AI actions. The address name must stay `localhost`, `127.0.0.1` or `*.localhost`.

---

## Architecture
```
Archer tray app (archerw.exe) ── runs ──► uvicorn on 127.0.0.1:4747
                                            │
Helium (default engine → /search?q=%s, suggestions → /suggest?q=%s)
   │
   ▼
Rules layer ──match──► redirect
   │
   ▼
GLiNER2 (local) ──low confidence──► plain search in your default mode (+ entity rescue)
   │  intent + entities
   ▼
Run in parallel:
 ├─ Search backend (SearXNG or Brave API)
 ├─ Cards (Wikipedia, Wikidata, TMDB, widgets)
 └─ AI layer (only if the mode or a click asks for it)
      ├─ Balanced: compact summary
      └─ AI mode: agentic browse
   │
   ▼
Streamed results page with the mode switcher at the top
   │
   ▼
Event log (corrections + escalations) → calibration / fine-tuning
```

## Speed targets
| Stage | Target |
|---|---|
| Rules and GLiNER2 routing | < 50 ms |
| Links and cards on screen | < 400 ms |
| First token of the compact summary | < 1.2 s |
| First result in AI mode | < 3 s, streaming |

## Estimated DeepSeek cost per month
These use the rate from your current usage, about $0.16 per million tokens, and assume ~60 searches a day. Classification is local, so it adds $0.

| Default setting | Usage pattern | **Cost/month** |
|---|---|---|
| Classic | AI only when you click: ~10% "Ask AI", ~2 AI-mode runs a day | **~$0.40** |
| Balanced | Compact summary on about half of queries, some Expands, ~2 AI-mode runs a day | **~$1.00** |
| Balanced, heavy use | 150 searches a day | **~$2.50** |

## Main risks
- **Song cards and lyrics.** A matching lyrics search result can identify a song even when Wikipedia has no article. Existing Wikidata player IDs drive video and Spotify embeds; songs without a Spotify ID get a Spotify search card. Lyrics load on demand from LRCLIB and show their source; unmatched songs retain a link to the original lyrics result.
- **Search backend.** Self-hosted SearXNG is free but gets rate-limited by the engines it scrapes. A paid API is more reliable and will probably cost more than DeepSeek.
  - Seen on 13 Oct 2026: Brave was suspended ("too many requests") and Startpage hit a CAPTCHA. A 20-query burst also got DuckDuckGo a CAPTCHA, leaving Google CSE as the only reliable web engine.
  - Archer caches responses and loads the image, news and video strips only when the query calls for them. Adding a keyed engine (Brave Search API, Mojeek) to SearXNG would stabilise results.
- **Wikimedia limits.** Wikipedia and Wikidata return 403 to clients without contact details in the User-Agent, and 429 under bursts. Set `ARCHER_CONTACT` (an email or URL) in `.env`. Archer backs off for `Retry-After` and doesn't cache failures as "no entity".
- **Other web pages calling Archer.** Any page open in Helium can make requests to `localhost`. Two defences:
  - Archer only accepts its own host names, which blocks DNS rebinding.
  - AI generation will run only through a POST endpoint that checks `Sec-Fetch-Site: same-origin`. A plain `GET /search` never spends money, so a malicious page can't burn your DeepSeek credit.
- **One machine only.** Archer is local-only by design. Using it on other devices would need real hosting with authentication, which is out of scope for now.
- **Routing mistakes.** When GLiNER2 is wrong or unsure, the worst outcome is a plain results page. The switcher, the AI chips and `!g` let you correct it in one click, and the correction log improves the model over time.

## Build order
0. ~~Workspace, tray app, `install.cmd`, Helium setup page, and placeholder `/search` and `/suggest` endpoints~~ (done)
1. Real `/search` and `/suggest` endpoints, the rules layer, and SearXNG results
   - Done: `/suggest` uses SearXNG's `/autocompleter` (DuckDuckGo by default), normalizes its OpenSearch response, and retains Chromium relevance scores. Lookups use a bounded 60-second memory cache (five seconds for successful empty results), coalesce identical in-flight queries, and time out after 1.5 seconds. No history is persisted. Partial query text reaches the configured provider through SearXNG.
   - Done: home and every results tab share an accessible autocomplete dropdown: two-character minimum (short bang/category syntax allowed), 180 ms debounce, eight results, cancellation and stale-response protection, IME handling, arrow/Enter selection, Escape restoration, and normal Tab dismissal. Suggestions submit the existing form and preserve the active results tab. Related searches prefer SearXNG's search-response suggestions, with autocomplete as a fallback; SearXNG correction links also retain the tab.
   - Done: `/search` renders SearXNG's JSON in Archer's own UI, with the hero modules and inline modules above. SearXNG's `settings.yml` now allows `json` (a backup is at `settings.yml.bak-archer`). Bangs (`!g`, `!!w`) still hand off to SearXNG's page.
   - Next: the rest of the rules layer (domain detection, own shortcuts, "I'm Feeling Lucky" redirects).
2. GLiNER2 router with the confidence levels and entity rescue, plus the knowledge and where-to-watch cards
3. Classic mode with the AI chips, the mode switcher, the default-mode setting, and the **event log** (corrections and escalations)
4. Balanced mode's compact summary with Expand
5. AI mode (agentic browse)
6. GLiNER2 calibration from the correction log, then fine-tuning
