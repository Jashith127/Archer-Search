# AI search for Archer

Implementation design · 14 October 2026

Implementation update: the core compact-answer and tool-driven research flows now exist
in the workspace, including guarded POSTs, evidence snapshots, streaming, native tools,
validated custom cards, comparisons, follow-ups, cancellation and usage limits. See
[README](README.md#ai-search) for setup and current boundaries. The document below retains
the product rationale and phased design; optional classifier evaluation and opt-in
feedback calibration remain future work. The full AI mode now has its own reading workspace,
source sidebar, pinned composer and cached conversation restores. Ordinary explanations skip
paid planning; tool-dependent tasks default to one compact planning call plus a developed
answer. Native media retrieval, list/step blocks and bounded gzip/deflate reading address the
first implementation's coverage gaps. Current operating limits and pricing estimates in
README supersede the original planning limits below.

## The idea

Archer should combine **search for discovery, cards and tools for precise answers, and AI for explanation and synthesis**. A question should produce the most useful combination of those things, with a visible path back to the evidence.

Archer already knows how to do more than retrieve links: calculate, convert, identify entities, show weather, find nearby places, surface discussions, and connect films and songs to useful actions. AI should make that collection work together. For example, “is tomorrow morning a good time for a run in Bengaluru?” should use Archer’s weather data and explain the tradeoffs, while leaving the forecast available to inspect. It should not generate an unrelated weather essay from search snippets.

The central product decision is to connect ordinary search with a dedicated AI research view.
Users can stop at a link, use a card, read a compact explanation, or open the full conversation
workspace while retaining the same query, evidence and tools.

This document proposes behavior and an implementation sequence. The repository’s current implementation is the baseline; proposed capabilities below are not already available.

## 1. What Archer has today

I reviewed the current workspace, including its in-progress changes, rather than treating the concept note as a record of shipped features.

| Existing strength | Evidence in the code | How AI should use it |
| --- | --- | --- |
| Results from multiple engines, with engine attribution | `searxng.py`: `Result`, `SearchResponse`, `SearXNG.search()` | Start from the same retrieved results the user sees. Preserve the original ranking and attribution. |
| Deterministic utilities and interactive widgets | `query.py`, `utilities.py`, `views.py`: `LOCAL_KINDS` | Calculate with the existing implementation; let AI explain the result or interpret a longer request. |
| Typed, source-backed cards | `sources/`, `templates/heroes/` | Supply actual card facts to the answer, including their provenance and limitations. |
| Wikipedia/Wikidata entity matching | `sources/wiki.py`: `_matches()`, `lookup()` | Resolve an identity before summarizing it; reuse Wikidata IDs across follow-ups. |
| Discussions separated from ordinary results | `views.py`: `DISCUSSION_HOSTS`, `View.discussions` | Distinguish experience and opinion from specifications and official statements. |
| Images, news, videos, works and places loaded separately | `app.py`: lazy APIs; `archer.js` | Fetch only the evidence a task needs. AI must not wait for decorative extras. |
| Home location, country and currency | `config.py` | Use explicit defaults for local questions, streaming availability and monetary context. Show which defaults were used. |
| Server-rendered UI, local tray app and caching | `app.py`, `searxng.py`, `sources/__init__.py` | Keep ordinary search independent of model availability and avoid repeated upstream work. |

Important boundaries in the current implementation:

- **Classic works; Balanced and AI are disabled labels.** There is no AI provider integration, generation endpoint, page-reading pipeline, research session store or local model dependency yet.
- Query typing currently uses deterministic patterns and question/how-to heuristics. GLiNER2 is a planned experiment, not an installed router.
- The “Quick answer” hero comes from DuckDuckGo through SearXNG and is explicitly labeled as not AI-generated.
- Bang queries hand off to SearXNG. Broader website shortcuts and automatic navigation described in the concept note are still planned.
- Local hosting does not make retrieval private or offline: search engines and card APIs receive requests, and autocomplete sends partial queries upstream. Cloud AI would add another recipient.
- Initial search and an optional hero lookup run concurrently, but `views.build()` awaits both before rendering. The SearXNG request permits a 12-second timeout. A subsecond results page is therefore an aspiration, not a property to assume.

## 2. The experience: one search, adjustable depth

Keep the existing **Classic · Balanced · AI** control, with Classic as the initial default. These describe the amount of work authorized, not different search engines.

| Mode | Behavior | Model work |
| --- | --- | --- |
| Classic | Existing results and cards. Contextual actions such as **Explain this**, **Compare**, or **Ask AI** where useful. | None until an explicit action. |
| Balanced | The same results page, with one compact synthesis when the query needs explanation or comparison and evidence is sufficient. | One bounded answer generation; no autonomous follow-up searches. |
| AI | A larger research panel within the same page, with sources, comparisons, uncertainties and follow-up questions. Results remain accessible. | Bounded page reading, tool calls and additional searches. Always explicitly entered. |

A useful card alone should normally satisfy a utility, entity or simple lookup query in either Classic or Balanced. “100 USD to INR” gets the converter. “Zendaya” gets the entity panel. Neither needs a generated paragraph saying what the card already says.

Balanced should consider the *remaining task*, not just whether a card exists. “Who is Zendaya?” may be fully answered by the entity card; “how has Zendaya’s choice of roles changed?” needs synthesis. Card presence is not a blanket reason to suppress AI.

### Placement and interaction

- Preserve the existing hero. Put a compact **AI explanation** section immediately after it, or above the links when there is no hero. Avoid two competing full-height hero modules.
- Start with two to four sentences, or a small comparison table when that communicates more clearly. Include citations and an evidence label such as **From search excerpts** or **Read 3 pages**.
- Show **Investigate further** on the compact answer. This explicitly enters AI mode, reusing the evidence collected so far. Model computation still takes time; reuse does not make expansion instant.
- Offer relevant card actions: **Explain this forecast**, **Compare these films**, **Find evidence about these places**. Use existing IDs and card payloads rather than asking the model to rediscover the objects.
- **Compare selected** lets the user select two to four results or supported cards. Each comparison cell has evidence or “Not found.” Result cards remain clickable throughout.
- Follow-ups inherit the current research session’s subjects and explicit constraints. An ordinary search-box submission begins a new search. Show inherited assumptions as editable chips, such as **India**, **INR**, or **Bengaluru**.

Switching to Classic stops any generation and collapses the answer. It does not silently restart research on a later reload. Images, News and Videos retain their existing browsing behavior; the first release enables automatic Balanced answers only on page one of All.

## 3. What makes this specifically Archer

### A. Explain and reason over the cards

**Search:** “Should I run before work tomorrow in Bengaluru?”

Archer resolves the location and date, obtains the relevant hourly forecast from its weather adapter, and shows the forecast card. AI compares available temperature and precipitation windows, cites those readings and states the assumed time window. If “before work” has no saved meaning, it can offer an editable 6–9 a.m. assumption. It must not invent air quality, daylight or forecast coverage: call the relevant supported adapter or mark the information unavailable.

This requires extending today’s exact weather patterns and adapting the existing weather payload to the requested date. The current rule matcher does not already understand the whole question.

### B. Compare entities and keep the actions

**Search:** “Dune vs Dune Part Two — which should I watch first, and where in India?”

Resolve which “Dune” is intended, retain both film identities, and reuse entity facts and regional watch-provider data. A compact comparison can explain viewing order and link to each film’s card and viewing options. If TMDB is not configured or fails, Wikidata streaming links remain labeled as listings whose Indian availability is unconfirmed. AI cannot promote those fallback links into a verified availability claim.

This needs a new multi-entity comparison view: today’s `View` has one hero. Build it by composing existing card data and templates, rather than replacing the film UI with prose.

### C. Separate specifications from lived experience

**Search:** “Is a budget NAS good enough for Plex with two users?”

Use ordinary results to find candidate devices and official compatibility material. Use the existing Discussions group to find experiences involving transcoding, noise or setup. AI distinguishes verified specifications, reported experiences and its own recommendation. It asks about direct play versus transcoding when that changes the answer materially, or gives conditional guidance for both.

Engine agreement can help choose relevant pages, but three engines returning the same article still provide one source. Several pages copying the same benchmark do not provide independent confirmation.

### D. Improve local discovery without inventing reviews

**Search:** “Quiet cafes to work from in Indiranagar.”

Use Archer’s local place lookup, area correction, maps, addresses and website actions. In AI mode, read candidate websites or retrieve specific evidence for Wi-Fi, seating or work suitability. Explain which preferences are supported and which remain unknown. Existing OSM records do not supply reviews or reliably establish quietness; distance and metadata completeness are not quality ratings.

Keep a plain places query useful while richer evidence is unavailable. A Photon fallback contains less information than Overpass; that reduced coverage must survive into the AI context.

### E. Connect exact tools with natural-language intent

**Search:** “What’s my EMI for a ₹10 lakh loan over 5 years at 10%, and what if I repay faster?”

AI can extract proposed inputs, but a server-side deterministic calculator must produce the numbers. Show editable principal, rate and term next to the explanation. Archer’s existing EMI form runs in JavaScript; exposing the same validated calculation as a server-side tool is new work. The model must not substitute mental arithmetic for the calculator, and the UI must disclose any assumed compounding or repayment convention.

These examples are intended product behaviors, not claims about current forecasts, prices, availability or financial outcomes.

## 4. Routing: separate recognition, permission and evidence

Keep precise rules ahead of any learned router. They already handle a large share of everyday searches cheaply and predictably.

Extend query interpretation to return a structured plan:

```text
intent: navigation | utility | entity | lookup | explain | compare | research | unclear
subjects: text, resolved ID if available, match status
constraints: place, country, currency, date range, explicit user requirements
requested_tools: allowlisted adapter names and validated arguments
answer_need: none | compact | research
route_basis: rule | heuristic | local_model | explicit_user_action
```

Keep this separate from **user permission** (mode and action) and **evidence sufficiency** (what the retrieved material actually establishes). A confident intent classification does not make an answer trustworthy or authorize extra spending.

Initial routing policy:

1. Honor existing bangs and exact utility rules.
2. Recognize simple entity and lookup requests, then confirm identities through existing source adapters.
3. Treat explanation, comparison and constraint-heavy requests as candidates for synthesis.
4. In Balanced, skip automatic generation for unclear intent, unresolved important identities, missing critical evidence or a sufficient deterministic answer. Offer an explicit action instead.
5. In AI mode, ask a focused clarification only when ambiguity would materially change the investigation. Otherwise show an editable assumption and proceed within budget.

Start with explainable heuristics plus explicit actions. Evaluate GLiNER2 behind a flag against real Archer queries before requiring it. Its suitability, memory use, latency and score calibration need measurement on this machine. Do not copy the concept note’s estimated latency or confidence thresholds into launch guarantees.

## 5. Evidence before prose

Add a shared **evidence bundle**, reused by compact answers, expanded research and follow-ups. It is a server-owned snapshot, not whatever snippets happen to remain in the DOM.

Each evidence item records:

- Stable source ID; canonical URL; title; publisher; engine attribution where applicable.
- Evidence type: search excerpt, fetched page passage, structured API field, or deterministic calculation.
- Retrieved time and, separately, published/updated time when known. Record forecast validity or provider region where relevant.
- Text or typed values, associated entity IDs, and extraction location or passage IDs.
- Coverage and failure information: excerpt only, unavailable page, fallback place data, unconfirmed streaming region, missing field or partial engine coverage.

Structured card adapters must preserve field-level provenance where feasible. A derived value should identify its inputs and calculation; it should not acquire a made-up webpage citation.

### Balanced evidence policy

Reuse the results and successful card lookups already obtained. Read up to two relevant public pages only when excerpts cannot support the requested answer. Make no additional search queries automatically. If sufficient evidence still is not available, show what is known and offer **Investigate further**, or skip generation entirely.

A short answer based only on excerpts is acceptable for a narrow, well-supported explanation if its label makes that limitation visible. Excerpts are not enough to claim a comprehensive comparison, a current price, or consensus. Prefer official or primary sources for specifications and current factual claims; treat discussions as attributed experiences.

### AI mode evidence policy

Start with the same bundle. Plan only the missing evidence: resolve an ambiguity, obtain an official specification, check a conflicting claim, or investigate a user constraint. Additional searches use Archer’s SearXNG client and cache. Category searches, including News, count against the same budget.

Deduplicate URLs and repeated reporting, select relevant passages, and stop when the question is answered or a limit is reached. Do not activate every image, video and news module as part of research. Preserve upstream failure notices so a partial search cannot be presented as exhaustive.

### Citations and disagreement

The model returns structured blocks with source IDs. Archer validates that every referenced ID exists, builds links itself, and rejects unknown references. Every material external factual claim should cite supporting evidence; comparison cells may have different citations. Separate recommendations and inferences from observed facts.

ID validation proves that a source exists, not that it supports the sentence. Add support checks for critical quantities, dates and fields, and evaluate claim support manually on a fixed sample. An optional second model check is a later quality experiment with its own cost budget, not a guarantee of truth.

When sources disagree, show the conflicting values and dates instead of averaging them. When evidence is missing, say “Couldn’t verify.” Empty search results must never trigger an answer composed from model memory and presented as researched.

## 6. Implementation within the existing app

Keep FastAPI, Jinja and vanilla JavaScript. A vector database and a permanent web corpus are unnecessary for the initial workflow: Archer already has web retrieval, structured adapters and bounded query-specific evidence.

### The request lifecycle

1. `GET /search` retrieves ordinary results and cards and renders a page. It may create a short-lived search snapshot, but never invokes paid AI.
2. After the page is visible, its JavaScript may make a same-origin `POST /api/ai/runs` if the saved Balanced preference and server-side eligibility policy permit it. A Classic action or AI-mode selection uses the same endpoint with the explicitly requested depth.
3. The POST resolves a server-owned snapshot ID, checks permission and budget, assembles evidence and starts a run. The client cannot supply a forged evidence bundle or instruct the server to fetch arbitrary URLs.
4. Stream typed events over the POST response using `fetch()` and a readable stream: status, validated answer block, evidence update, completion, error. Scripts remain in `static/archer.js` under the existing CSP.
5. **Investigate further** creates a deeper run against the existing session and evidence version. Follow-ups add explicit constraints and retrieve missing evidence.
6. Cancel via `POST /api/ai/runs/{id}/cancel`; abort reading on navigation. Cancel downstream tasks where supported and record usage already incurred. A browser disconnect alone is not proof that provider billing stopped.

Generation is never triggered by a GET stream endpoint, a URL mode parameter alone, browser prefetch, or `/suggest`. The server must enforce saved defaults and explicit action rules; a client-supplied `mode` is not the policy.

Use `Sec-Fetch-Site: same-origin` as required by the repository, plus exact Origin validation and a short-lived session/CSRF token on paid POSTs. Reject absent or foreign required browser headers. Keep loopback binding, TrustedHostMiddleware, no-referrer, and restricted CSP.

Deduplicate active runs by session, query, depth and evidence version. Refreshing, double-clicking or opening the same answer twice must not spawn parallel paid runs. Scope automatic Balanced deduplication across an active browser session; intentional regeneration is a separate action. A missing or expired snapshot can refresh ordinary retrieval within the authorized run, with that work counted against its limits.

### Suggested code boundaries

| Area | Proposed change |
| --- | --- |
| `query.py` / new `routing.py` | Preserve existing rules; add task intent, constraints and eligibility without calling a paid model during GET. |
| `views.py` | Expose resolved card data and source context; attach a search snapshot ID; keep AI state separate from `hero`. |
| New `evidence.py` | Normalize cards/results/passages, manage evidence IDs, timestamps and snapshots. |
| New `fetcher.py` | Bounded public-page fetching, extraction, URL safety and passage caching. |
| New `ai/provider.py` | Small provider interface for structured responses, streaming, cancellation and usage. |
| New `ai/service.py` | Compact-answer workflow, bounded research loop, citation validation and budget enforcement. |
| `app.py` | Paid POST guards, run lifecycle and cancellation routes. |
| `config.py` / preferences | Provider key and model in environment settings; mode, privacy and spending preferences stored locally through guarded settings actions. |
| Templates / `archer.js` / `archer.css` | Compact answer, research panel, source inspection, selection, follow-ups and accessible status updates. |

DeepSeek is the existing concept note’s provider candidate. Keep it configurable; verify the exact model’s structured-output, streaming, usage reporting and cancellation behavior before selection. This proposal does not assume a current model ID or price. Reusing Archer’s evidence cache is reliable application behavior; provider-side prompt-cache savings should be measured separately.

## 7. Limits, freshness, privacy and failure

The following are initial engineering limits to tune after measurement, not performance promises:

| Limit | Balanced | AI mode |
| --- | --- | --- |
| Additional search queries | 0 | Up to 3 beyond the initial search |
| Public pages read per run | Up to 2 | Up to 6, including previously read pages reused from cache |
| Page-fetch concurrency | 2 | 2 |
| Model requests | 1 | Up to 4 total, including planning and final synthesis |
| Total model input/output allowance | 12k / 700 tokens | 40k / 4k tokens across all calls |
| Wall-clock deadline after POST | 12 seconds | 45 seconds |

Enforce the first reached limit, including a configurable money cap per run and day. Count provider reasoning tokens where billed and reported. Reserve estimated budget before each call; settle actual usage afterward. Block another call if the remaining allowance cannot cover it. If pricing is unavailable, use conservative token limits and show cost as unknown rather than guessing.

Initial resource limits for fetched pages: public HTTP(S) only, bounded redirects, five-second fetch timeout, a two-MB decompressed response ceiling, and a bounded extracted passage budget. Use a dedicated fetcher rather than the existing general external client, which follows redirects for trusted API calls.

The public-page fetcher must block loopback, private, link-local and other non-public destinations, validate every redirect, and protect against DNS rebinding at connection time. Search results are untrusted input. Never forward browser cookies or local credentials. Treat page instructions as source text, expose only allowlisted tools with validated arguments, and give the model no filesystem, shell or arbitrary network tool. Render generated content through DOM APIs and allowlisted links, following Archer’s existing no-`innerHTML` approach.

### Freshness and reuse

Keep the current ten-minute search cache and one-day entity cache as explicit baselines, but make AI freshness depend on the claim. A cached entity panel is useful background; it is not sufficient proof of a current executive, price or watch listing. Time-sensitive tasks need a targeted refresh or an explicit stale/unverified label.

Key answer reuse by query, user constraints, region/currency, evidence hashes, model and prompt version. Invalidate when relevant data expires. Add pending-request coalescing to search and page fetches: the existing SearXNG search cache does not coalesce simultaneous misses, although autocomplete already does. Respect existing source backoff and avoid multiplying research retries.

### Privacy and failures

Before enabling cloud AI, explain that the provider receives the query, selected public evidence, relevant constraints and follow-up context. Send home location only for tasks that need it. Default to in-memory research sessions and no raw query or page-text logging. Optional persisted history needs an explicit setting, retention limit and delete control. Persist only minimal usage totals needed for spending limits by default.

If the model is unavailable, a cap is reached, extraction fails or a stream breaks, retain the search page and show the specific limitation with an explicit retry action. Do not silently switch providers or upgrade to a deeper paid run. With incomplete evidence, return a limited answer only if its supported parts remain useful. Keep “From search excerpts,” “Source unavailable” and “Budget reached” distinct from a successful full investigation.

## 8. Build order and release checks

**Phase 1 — Explicit Ask AI on current results.** Add evidence snapshots, one provider adapter, guarded generation POST, usage limits, source-linked compact answers and cancellation. Begin with current excerpts and card facts; abstain when they are insufficient. No autonomous page fetching or search loop. This proves the core interaction and exposes citation-quality problems early.

**Phase 2 — Balanced and selective reading.** Add the safe public-page fetcher, extraction, evidence freshness, saved mode preference and conservative eligibility. Add one end-to-end card explanation, preferably weather, before generalizing adapters. Instrument search-render and AI timings separately. If initial page latency is poor, give optional hero lookups a render deadline and lazy fallback; never add a classifier or model wait to the existing critical path.

**Phase 3 — Comparisons and bounded AI research.** Add multi-entity resolution, selected-result comparisons, typed tool calls, up to three follow-up searches, conflict handling and session follow-ups. Start with films, where Archer already has entity facts and viewing actions; add local-place evidence and deterministic calculator tools after that pattern works.

**Phase 4 — Improve routing from evidence.** Evaluate the local classifier on a curated set and opt-in corrections. Distinguish **Wrong interpretation** from **Wanted more depth**. Switching modes or dismissing a card is ambiguous feedback; do not automatically turn it into a training label. Compare routing accuracy, RAM, latency and user benefit before making a model mandatory.

### What must pass

Use mocked HTTP/provider responses in the existing pytest suite, plus browser interaction checks. The repository documents upstream CAPTCHA and rate-limit incidents; live query bursts would distort the test and harm normal usage.

- **Permission:** Classic GETs, suggestions, reloads, tab changes, cross-origin requests and rejected tokens produce zero provider calls. Balanced starts only under saved opt-in and eligibility rules. Concurrent duplicate requests result in one paid run.
- **Regression:** Existing calculator, conversion, song, entity, watch, places and image behavior still works without an AI key or with AI disabled.
- **Evidence:** Unknown citation IDs are rejected; fabricated comparison cells become unavailable; a missing regional watch result never becomes a verified claim. A failed source and a fresh conflicting source remain visible.
- **Safety:** Test redirect-to-localhost, private IPs, DNS rebinding, oversized/decompression responses, source prompt injection and unsafe generated links.
- **Control:** Caps stop subsequent model/search calls, cancellation stops pending work, and partial output stays clearly labeled. Follow-ups cannot inherit another session’s evidence.
- **UX:** Links remain usable during generation; keyboard focus and source inspection work; navigating away cannot insert the previous answer into a new query. No-JavaScript search remains functional.

Create a fixed evaluation set covering ordinary navigation, exact utilities, identity ambiguities such as “python” and “tron,” a simple explanation, a mixed weather question, a film comparison, a local recommendation and a NAS investigation. For each, record the desired route, needed evidence, allowed assumptions and abstention conditions.

Release only when those cases pass, all material factual claims in the reviewed sample have supporting evidence, and a timed comparison with Classic shows that adding AI has not delayed ordinary results. Measure answer usefulness, unsupported claims, unnecessary generation rate, time to useful answer and cost per completed task. More generated answers is not a success metric.

## 9. Relationship to the existing concept note

Retain its Classic default, adjustable AI depth, visible sources, source-backed cards and local-first deployment. Refine four assumptions before implementation:

1. **Card facts belong in AI evidence.** Snippet-only summaries miss a major Archer advantage; use selective page reading when excerpts are insufficient.
2. **GLiNER2 is an experiment rather than a prerequisite.** Explicit actions and existing rules let the first useful release ship and produce an evaluation set.
3. **Expansion reuses evidence, not guaranteed model computation.** Make its latency and additional spending visible.
4. **Budget and speed claims require measurement.** The concept note’s token prices, monthly estimates and subsecond targets are planning assumptions, not verified current costs or observed performance.

The concept note now points to the implemented behavior. Its original classifier thresholds,
navigation plans and cost estimates remain marked as historical planning material.

The first implementation should make one interaction excellent: **search normally, ask Archer to explain what it found, inspect the evidence, and keep using the cards and links.** Everything deeper grows from that same workflow.

## Repository references

- [Product intent and earlier roadmap](concept-note.md)
- [Current installation and daily use](README.md)
- [Query rules and heuristics](src/archer/query.py)
- [Search client, result model and caching](src/archer/searxng.py)
- [Hero composition and discussion grouping](src/archer/views.py)
- [Routes and security headers](src/archer/app.py)
- [Entity matching and facts](src/archer/sources/wiki.py)
- [Places and fallback coverage](src/archer/sources/places.py)
- [Streaming-provider API adapter](src/archer/sources/watch.py)
- [Current mode placeholders and results layout](src/archer/templates/search.html)
- [Country-specific watch UI and fallback labels](src/archer/templates/watch_panel.html)
- [Browser interactions and lazy modules](src/archer/static/archer.js)
- [Existing query and endpoint checks](tests/test_query.py), [endpoint tests](tests/test_app.py)
