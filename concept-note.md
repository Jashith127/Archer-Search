# Concept note: Archer, a personal AI search engine

## Summary
Archer is a self-hosted search engine you set as Helium's default. It works like a normal search engine with useful cards. AI is added only as much as you've chosen in settings, and you can switch to bounded tool-driven research with one click. Conservative local rules decide when an automatic compact answer is useful; DeepSeek writes cited answers and calls Archer's tools. GLiNER2 remains an optional routing experiment.

Archer runs as a small Windows tray app that serves the engine on `http://archer.localhost:4747`. You fill in Helium's "Add search engine" dialog once, using `/search?q=%s` and `/suggest?q=%s`.

### Companion app and providers · 14 October 2026

Archer is being prepared for people other than its author, so setup no longer assumes Docker,
SearXNG or a DeepSeek key.

- **Companion app** (`/app`, opened as its own window from the Start menu or tray) replaces the
  Settings page: Home, Search, AI, Personal, Startup, Browser, Connections, Privacy & data.
  **First-run setup** (`/app/welcome`) walks through search, AI, personal defaults, startup and
  browser in seven steps and opens automatically until it is finished.
- **Companion layout (15 October 2026):** a plain preferences window, not a dashboard. A toolbar
  of tabs across the top, one centred column, settings in inset lists of label/control rows, and
  provider choices as radio lists in the same list as the fields they need. Neutral greys; white
  marks what is on or selected, green and amber are kept for status. Unsaved edits collect in a
  save bar docked to the bottom. Setup is a narrow centred column with a segmented step bar.
- **Search providers:** DuckDuckGo (no key; its HTML page, since it has no official results API),
  SearXNG, Google (Serper, because Google's own JSON API takes no new customers and ends on
  1 January 2027; legacy Programmable Search keys still work) and Brave's official API. A failing or
  rate-limited provider falls back through every provider that is set up (Google and Brave when
  keyed, keyless DuckDuckGo, a local SearXNG) with a visible notice; SearXNG with every engine
  suspended counts as rate-limited. A rate-limited provider is skipped for 10 minutes, so later
  searches, strips, product lookups and AI research go straight to a free one. While any engine or
  provider is rate-limited (or was within the hour) every results page and AI Mode carry a
  dismissible marquee across the top ("SearXNG rate limit reached", each engine as a pill in its brand
  colour, and which provider is answering); dismissing it holds until a different engine is limited.
  Address-bar suggestions fall back to DuckDuckGo's when SearXNG doesn't answer. Setup detects a running SearXNG and
  otherwise suggests DuckDuckGo.
- **AI providers:** DeepSeek stays recommended; OpenAI, Claude (native Messages API), OpenRouter,
  Ollama (native API, so the context window is set) and any OpenAI-compatible server also work.
  Spending caps price known models from list prices; local models cost nothing.
- **Startup:** Start with Windows passes `--startup`, which honours a sign-in delay, a ready
  notification, what to open (tray, companion or search) and an optional SearXNG container.

### SearXNG without Docker · 17 October 2026

Docker Desktop was the heaviest part of Archer. Measured on this PC with the same settings.yml
and one query:

- Docker Desktop: ~2.9 GB (2.2 GB VM and 0.7 GB of Windows helpers).
- An Alpine WSL distro with docker-ce: ~1.4 GB.
- The same distro running SearXNG from source: ~1.15 GB.
- SearXNG run natively on Windows: **~135–150 MB** with no VM.

Archer now runs SearXNG itself (`searxng_local.py`, `searxng_runtime=native`):

- It installs a pinned SearXNG commit, checked against a sha256, into its own uv venv under
  `%LOCALAPPDATA%\Archer\searxng`, with two small shims (a `pwd` stub and the selector event loop).
- It runs `python -m granian` on loopback inside a kill-on-close Job Object, and keeps the server
  log in memory only, because engine warnings contain query URLs.
- Upstream doesn't support Windows, so the pin moves only after a real install and start, and
  updates will smoke-test and roll back.
- Docker stays available (`searxng_runtime=docker`; an existing `searxng_container` migrates to
  it), and so does any other instance (`external`).
- The tray has a "SearXNG running" toggle.
- Still to come: the companion pane, installer step, and update/rollback (see the plan in the
  session notes).

### History and personalization · 16 October 2026

Archer now keeps a local search history and uses it to personalize results, without an LLM.
This replaces the earlier "Archer keeps no search history" stance: history is on by default,
lives only in `data/history.sqlite3`, can be paused, cleared by range or entry, and expires after
a chosen number of days (a year by default).

- **What is stored:** page-1 searches (a repeat within 30 minutes updates one entry; browser
  prefetches are not recorded), the results opened from each (a same-origin `keepalive` POST as
  you leave the page), site preferences (raise, lower, block) and meanings picked from "Other
  meanings" chips.
- **Embeddings:** meaning is compared with local vectors. The default is Model2Vec's
  `potion-base-8M`, a static model (token lookup and mean) that embeds a page of snippets in about
  a millisecond on a CPU, so no GPU, PyTorch or LLM is involved; it downloads once (30 MB).
  `potion-retrieval-32M`, an Ollama embedding model, or hashed word/trigram vectors (no download,
  and the fallback while a model loads or can't be fetched) are alternatives. Vectors are cached
  per model, so switching re-embeds lazily.
- **Ranking:** `0.6·rank + 0.25·semantic + 0.15·affinity` (affinity needs two visits), plus ±0.35 for a raised or lowered site and
  a small lift for a page you opened before. *Rank* is the provider's position on a fixed curve
  (1, 0.8, 0.67, …), *semantic* is query-to-snippet similarity normalised across the page (half
  weight with word matching), *affinity* counts visits to that site after searches that mean the
  same thing, fading with a 30-day half-life. The provider's order keeps the most weight so exact
  matches (model numbers, names) stay near the top. Blocked sites are removed, including from
  Discussions. Personalization reorders within a page; it never adds a request.
- **What counts as related (16 October 2026, after evaluating a real history; see
  `docs/history-evaluation.md`):** similarity is 0.7·embedding + 0.3·word similarity, stored as one
  concatenated vector. The cutoff is calibrated per model on twenty unrelated searches at mean + 7
  standard deviations (3.5 showed a third unrelated matches). Calculator-style searches and fragments
  never match; spelling variants always do; one-word searches need a stronger match; searches whose
  opened page is in the current results are related regardless of wording. Recall shows one page per
  site, skips junk titles, and never shows a different model or version number than the one searched.
  Precision of shown matches went from 0.63 to 0.88, new-topic false matches from 74 % to 12 %.
  A second round added: session context (a past search's vector blended with the searches made
  within 30 minutes of it, cutoff ×0.96), half-typed and initials matching, sites named in a search
  recalled first, other products of the same brand kept out of recall (words outside the model's
  vocabulary count as product names), and "Not related" corrections remembered both ways.
  Precision 0.92, same-need recall 0.90.
- **Recall and related:** "You found these before" lists up to three pages opened for similar
  searches; "Your earlier searches like this" lists past searches with the same meaning in other
  words. The `/history` page searches history by meaning and groups it into topics (greedy
  clustering on query vectors, labelled by shared words).
- **Suggestions:** an empty search box lists recent searches; typing (and the address bar's
  `/suggest`) puts matching past searches first, ranked by frequency and recency. OpenSearch's
  format is unchanged.
- **Meanings:** an "Other meanings" choice is reapplied the next time that name is searched, with
  a quiet line saying so; the other meanings stay one click away.
- **Not done:** query expansion (extra searches from related history) was left out because it
  costs provider requests and invites off-topic results; history is never sent to an AI provider.

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
visible; missing evidence does not become an invented result. The multiline composer carries
Archer's pixel mark (fast wave and a pixel scan along its top edge while answering), a context
row saying what the next question builds on with a "New question" toggle to drop that context,
a Quick / Research segmented control (remembered per browser), a labelled source count, key
hints while typing, a character count near the 600 limit, and a white send button that becomes
a labelled Stop. "/" focuses it in AI Mode.

AI mode now opens a full-page reading workspace with a source sidebar and pinned composer.
Follow-up turns remain visible, and cached session restores make no new model calls. Ordinary
explanations use one model call; research needing tools defaults to one lean planning round
plus an answer. Research has a 5,000-token answer ceiling and a 75-second run deadline;
it is prompted for developed, useful sections rather than a summary. Media is retrieved from
Archer's existing image/video search instead of guessed URLs. Gzip/deflate pages are read with
strict compressed/decompressed limits. Source failures appear in collapsed notes. Built-in
Flash pricing ceilings, reported token usage and model-call counts make spending visible.

AI answers are shaped answer-first. The model classifies the question (fact, explanation,
how-to, comparison, recommendation, overview, current event, media) and composes from typed
blocks: a highlighted bottom-line **summary** first, then key-fact tiles (a fact naming a
person, place or work shows its Wikipedia photo, exact titles only), a **verdict** card,
**pros/cons**, **product** cards (photo and link from the cited listing, never the model),
**bars** for comparable numbers, a **timeline**, a verbatim **quote** (downgraded to a paragraph
if it isn't word for word in the source), one tip/warning **callout**, tables, lists and steps.
`==phrase==` highlights at most one phrase per block. Every figure must appear in a cited
source; a malformed visual block drops only itself.

Pictures are decided before any model call (`visuals.py`): *required* when asked for, *helpful*
for visual subjects (species, places, products, food, art, entities), otherwise *none*, so
calculations, code, finance and abstract explanations get no image search. Follow-ups only get
pictures when they ask. Images must name the subject in their title or URL (`relevant_images`),
three or more or none; the answer model then keeps only refs that show the actual subject or
hides the gallery, and that verdict streams before the text.

Cost controls: a finished answer is replayed for the same question and depth for an hour, across
sessions, at no cost (regenerate still pays). The payload sends evidence in a fixed order before
the question so DeepSeek's prefix cache (about 2% of the input price) covers the system prompt
and most of a follow-up's evidence. Research pages are cut to 2,800 characters and answers aim
for 300-600 words. Automatic Balanced answers start only once the results tab is visible, after
0.7 s, so background tabs and immediate result clicks cost nothing. Snippets sharing no word
with the question, and repeated snippets, stay out of the prompt (at least three text sources
are kept). Fully specified loans, "A vs B" comparisons and forecasts run Archer's tools directly
instead of a paid planning call, and the image search runs alongside planning and reading.

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
- **Investigate further:** reuses the evidence bundle; additional research and generation are separately bounded and take time. The payload is ordered so provider prompt caching can apply, but budgets still reserve the uncached price.
- **AI mode:** native tool calls gather the evidence required by the task. Archer validates arguments, provenance and UI components; the model cannot execute arbitrary scripts or network requests.

---

## Results page

Archer renders SearXNG's JSON in its own dark UI. The layout follows Google's landscape orientation: a full-width **hero module** for the query type sits at the top, with the results column and a side panel below it.

**Page skeleton:** header (search box, `All · Images · News · Videos · Maps` tabs, `Classic · Balanced · AI` switcher) → hero → results column (with inline strips) + side panel → related searches → pager → engine status.

### Hero modules by query type

| Query type | Detected by | Example | Hero shows | Data source |
|---|---|---|---|---|
| Person | Wikipedia title match (ambiguous names follow the web results: "dune" → the 2021 film, "apple" → Apple Inc.; other meanings become chips; a trailing "show"/"movie"/"company" picks the kind), Wikidata P31 = human | `zendaya` | Name, description, link chips (site, socials, IMDb), image collage, top story, tiles (age, birthplace, spouse/parents, height); actors get a full-width **Movies and shows** poster grid (All/Movies/TV, best known/newest, two rows then Show all, role under each title); writers and musicians a **Books** or **Albums** row; About + facts panel | Wikipedia, Wikidata, SearXNG images/news; TMDB posters when keyed |
| Film | Wikidata film types | `dune part two` | Poster, where to watch (service logos), tiles (released, runtime, director, genre); budget, box office in panel; a full-width **Cast & crew** section below (key crew chips, every cast member with photo and character) | Wikipedia, Wikidata; TMDB credits when keyed |
| TV series | Wikidata TV types | `the bear tv series` | Poster, tiles (years aired, seasons, episodes, network); **Cast & crew** section with episode counts when TMDB is keyed | Wikipedia, Wikidata; TMDB credits when keyed |
| Book | Wikidata literary work | `atomic habits` | Cover, tiles (author, published, pages, genre) | Wikipedia, Wikidata |
| Music | Band, album or song | `coldplay` | Collage, tiles (formed, genre, label, members), Albums row. Songs and albums: video (or artwork) with its tiles in one row beneath it on the left; Spotify, Apple Music and lyrics on the right, the lyrics stretching to end level with the left side | Wikipedia, Wikidata |
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
| AI model | Name of a model in Epoch AI's capability index, optionally framed (`gpt 5 release date`, `openai o3`); very short names like `o3` need the framing word | `claude opus 4.5` | Release date, developer, family, open/closed weights, capability rank, previous and next model in the line; tabbed bar chart comparing it with its earlier versions and the strongest models from other labs released around the same time, on the Epoch Capabilities Index plus up to four well-known benchmarks (GPQA Diamond, SWE-bench Verified, FrontierMath, HLE…; SWE-bench always gets a tab when Epoch has results). Each graph plots score against API price (log scale, with a best-value line) when at least three of its models have a price, otherwise against release date; hiding a model removes it from the graph and the labels and best-value line close up. Layout is portrait: a facts strip (one row, or two even rows), a token-pricing section (input, output, cached and blended rates per million tokens, a workload cost estimator, blended price against the chart's models) and the chart with a compact sortable model table (price, score, cost per point; show all / only this model). Prices show in the home currency (₹ by default) at the day's USD rate, with a USD switch | Epoch AI (CC BY 4.0), zip cached in `data/` and refreshed daily; OpenRouter public model list for prices, cached daily; Frankfurter (ECB) or CoinGecko for the USD rate |
| Product | A model name that Wikidata calls a product (manufacturer/brand, smartphone/computer/product model), or a model name ("wh-1000xm5", "g3") that two or more stores list; shopping words are stripped and pick the opening tab (`price`, `review`, `specs`, `buy …`) | `sony wh-1000xm5`, `iphone 16 pro price` | Photo gallery (the maker's site first, then stores, then review sites; each photo credited and linked; the stage fits the window and keeps each photo's proportions), price box (range across stores, the size or capacity it is for, lowest store), expert score ring and store star rating, store listings (only stores' own product pages, never search or category pages; one row per size per store, trackers after stores; snippets quoting a carousel of other products give no price), a Second-hand block for models three or more years old (release year from Wikidata or the dates listings and reviews mention): listings from the home country's used markets (OLX and Quikr in India, eBay and Swappa in the US, Gumtree in the UK…) and refurbished store pages, priced on their own "Used from" line that never sets the new price, plus a labelled marketplace search link; trackers' prices are hidden for models six or more years old (launch-era figures), tabs: Overview (Wikipedia text, listing highlights, model line previous → next, what reviewers say, storage and colour variants, compare chips), Reviews (outlets with score, date and excerpt, video reviews of this model), Specs (Wikidata and listings); "Ask AI Mode" questions. AI Mode opens with the same product card refreshed by a price and a review search, adds those listings as citable sources; a comparison ("iPhone 16 vs iPhone 17", also as a follow-up) gets a card, listings and reviews for each product it names and answers with a side-by-side table; prices inside trade-in, financing or discount clauses, and prices in another currency or from another country's store page, are never used, and answers with verdict, `specs`, pros/cons, review-score bars and store cards linked to the named store's page | Search snippets (no keyless shopping API; store pages refuse direct fetches), Wikidata, the configured image search; `/api/product` adds a price search and a review search, `/api/product/photos` the credited photos |
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

**Not built (no free, keyless data source):** live sports scores, stocks and indices, flight status, election results, price history (product prices come only from current search snippets), hotels, flights, jobs, showtimes, nutrition. Each needs a paid or keyed API; add one when a key is available.

### Inline modules (no hero needed)

| Module | When | Placement |
|---|---|---|
| Discussions | 2+ results from Reddit, Stack Exchange, Quora, HN | Card group after result 4 |
| In the news | Entity heroes and news-y queries (`latest`, `today`, `news`) | Strip after result 2 |
| Videos | `how to …`, `trailer`, `tutorial` | Strip after result 3 |
| Images | Visual queries (`photos`, `wallpaper`) and people/places | Strip after result 1 |
| Related searches | Every query | Chips at the bottom |

Strips scroll sideways in a viewport that runs 32px past the results column (edge to edge on phones). The side with more to show fades out instead of cutting a card, and round arrows page through it (hidden on touch-only screens). Images show up to 16 cards, news up to 10; a news story the engine sent without a picture gets the article's own share image (`og:image`, read from the page head by the pinned public-only fetcher).

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
- **AI loading and reveal:** while an AI answer is being worked out, the turn shows its shape in brand dots (`pixelSkeleton` in `archer.js`, a canvas at about 18fps): lines of rounded squares at stepped ink levels whose tails dissolve like the mark's legs, a bright band sweeping diagonally across and stray flashes. Once the first part arrives, a two-line tail of the same dots stays under the answer until it is complete. Each arriving block and tool card decodes (`decodeReveal`): every character is first a pulsing dot, then a random letter that changes every 60ms, then locks onto the real character, in a wave spreading outward from the top-left corner. Characters keep their real widths throughout, so the layout never reflows, and the original text nodes are restored afterwards. Reduced motion skips the decode.
- **Home page:** only a settings icon top right (no wordmark, clock or date); in the middle the 56px pixel mark, a two-tone greeting ("Good morning." then "What are you looking for?" in grey) and one composer card: the input on top, and a footer row holding the Search / AI Mode switch (two uppercase mono labels with a dotted rule sliding under the active one, Alt+M, remembered per browser in `localStorage`), and the submit button (no shortcut labels; Alt+M and Ctrl+Enter still work). Below it sits a row of starter chips (instant-answer examples in Search, questions in AI Mode) that fill the box and submit it in the current mode. Search submits to `/search`; AI Mode submits to `/ai`, changes the copy, the starter chips and the button ("Ask"), tints the card and adds a soft glow behind it. Ctrl+Enter sends the query to the other mode. Bangs always go to search. The paid run starts on `/ai` only because the home submit leaves a one-shot `sessionStorage` flag for that exact query, so a reload, history entry or prefetch of `/ai` never starts generation. The settings icon links to `/app`. There is no caption under the box, except in AI Mode without an API key, where one line links to the AI settings (only whether a key exists reaches the page, never the key). The home dot field covers the whole viewport and the field itself fills the bottom 56%. The dot field ignores the cursor. The composer is one solid colour (#161616; violet-tinted in AI Mode) with no border, panel shadow or blur, and no magnifier inside the input; the submit button carries the magnifier to the left of its label. The fill is drawn in CSS under the form so its outer 2px band can be masked. Within about 96px of the edge outside (22px inside, so the effect never flips between top and bottom while the cursor crosses the box), that band fades out around the point nearest the cursor and the stretch is redrawn as rounded pixel dots in the composer's own colour, fully opaque and snapped to device pixels so they read as exactly the fill (a translucent dot over the darker page comes out a different grey): up to 4.5px bigger than the band right by the cursor, shrinking smoothly to the band's thickness about 104px away, with the page showing between them. Like the mark's legs, the edge dissolves outward: near the cursor up to two more rows of dots spill out one 7px step at a time, each smaller and needing a closer cursor to appear, the outermost one sparse (every other dot). The closer the cursor, the fuller the effect. One animation loop eases the edge point (about 30ms, so no visible lag) and the strength (fast in, slower out), drives both the CSS mask and the dots from the same values, and sleeps once settled. The dot canvas overhangs the box by 20px so the spill rows are never clipped and dot positions never reshuffle. Focus lifts the fill a shade and widens the band to 3px (so the dots grow with it), with no glow; the colour and band animate on focus and mode changes (registered custom properties). In AI Mode the field flashes more often and cross-fades (about 0.9s, a CSS opacity fade between a grey layer and a coloured one) to AI Mode's colours: periwinkle, violet and blue, with a little of the green accent, each dot keeping its own hue and shade.
- **Fonts:** DM Sans (primary) and DM Mono (secondary: badges, eyebrow labels, numbers), self-hosted.
- **Link chips:** no Facebook. X shows as its logo only.
- **Flags:**
  - Countries lead with the flag from Wikidata (P41, via Wikimedia Commons).
  - "Country" tiles and currency fields show small ISO flags (flagcdn).
- **Brand logos:** link chips use Simple Icons (CC0), stored locally.
- **Result favicons:** fetched server-side through `/api/favicon/<host>` and cached. The letter badge stays as the fallback.
- **Cache-busting:** CSS and JS URLs carry a content hash (`?v=`), so the browser never pairs new HTML with an old stylesheet.

**Failures are visible.** If a card's data source (Open-Meteo, Wiktionary, MyMemory, CoinGecko) fails, the page says which one instead of silently dropping the card. Entity panels stay quiet on failure, because many general queries aren't entities anyway. Wikimedia timeouts are retried once.

**Speed.** The SearXNG search and the hero lookup run concurrently. Slow extras load after the page is on screen: collage images, the top story, strips, the filmography grid, the cast section and the place weather tile. SearXNG responses are cached for 10 minutes and Wikipedia/Wikidata panels for a day, so reloads don't hit the upstream engines again.

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
