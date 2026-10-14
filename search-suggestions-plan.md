# Search suggestions for Archer

Implementation brief, researched 14 October 2026 and subsequently implemented in this workspace. The current behavior and validation are documented in README.md and concept-note.md; the sections below preserve the design rationale and acceptance criteria.

## Recommendation

Use SearXNG as the single autocomplete source, accessed through Archer's existing `/suggest` endpoint. Add one reusable, accessible dropdown controller to both on-page search forms. Keep the OpenSearch response compatible with Helium, including Chromium relevance metadata. Render related searches from the suggestions already returned with search results, with autocomplete as a fallback only when those suggestions are absent.

This fits Archer's FastAPI/Jinja/vanilla-JavaScript architecture. It adds no framework, model, paid calls, database, or browser-to-provider connection. Keep DuckDuckGo as the initial provider: it is already configured and working. There is no comparative evidence here that changing provider would improve this installation.

## Where suggestions belong

| Surface | Current behavior | Recommended behavior |
| --- | --- | --- |
| Helium address bar | `/suggest` proxies SearXNG; setup and OpenSearch discovery already advertise it | Harden the existing endpoint and preserve browser metadata; Helium owns its dropdown |
| Home search box | Plain GET form, browser autocomplete disabled | Shared suggestion dropdown while typing |
| Results search box | Same plain form, plus clear button and `/` focus shortcut | Same dropdown on All, Images, News, and Videos; retain the current `tab` when submitting |
| Related searches | All tab only; JavaScript calls `/suggest` after every page load | Prefer actual `SearchResponse.suggestions`; show up to eight unique links on all four tabs where available, preserving the tab and resetting pagination |
| Spelling corrections | SearXNG correction link plus a Wikipedia entity correction fallback | Retain as a separate “Did you mean” feature; preserve the tab for SearXNG correction links |
| Setup page | Shows suggestion URL, no search field | No dropdown needed; document provider privacy and prerequisites |

There are exactly two on-page global search forms in the templates. Images, News, and Videos share `search.html`'s header; they do not need separate controllers. Hero controls such as unit converters, place filters, and translation fields are specialized inputs and should not receive web autocomplete.

## What the repository does today

- `app.py:168` implements `/suggest`. It forwards only `q`, without `X-Requested-With`, and passes through a list with a list at index 1. Errors return `[q, []]`. The default HTTP client timeout is 1.5 seconds.
- That validation accepts non-string completions and arbitrary trailing metadata. It has no query-length bound, completion-count bound, TTL cache, or duplicate-request coalescing.
- `searxng.py:112` contains an unused `autocomplete()` method. The route bypasses it and talks directly to `app.state.searxng`. Consolidate this before adding another caller.
- `searxng.py` already parses both `suggestions` and `corrections` from `/search?format=json`. Corrections are used by the template; suggestions are unused.
- `archer.js:61` selects only the results-page input for the existing search-box behavior. A shared controller must explicitly select `input[name="q"]` within each search form, avoiding the hidden `tab` input.
- `archer.js:258` builds related-search chips from autocomplete. It drops the active tab from their links.
- `getJSON()` caches promises indefinitely for the page, including failures. Do not use it as the keystroke-request cache.
- `.searchbar` has `overflow: hidden`; a dropdown placed inside it would be clipped. The header is sticky and collapses on scroll, so the dropdown must also cooperate with header focus and layering.
- `tests/test_app.py` covers the proxy's happy path and some failures, but no dropdown interactions, response races, bang query echoes, or metadata consistency.
- The concept note anticipates navigation-type browser suggestions. The current proxy does not construct those. Ordinary URL-looking completions should remain search suggestions until navigation semantics are separately implemented and verified in Helium.

## Verified local SearXNG behavior

The running `searxng` container uses `searxng/searxng:latest`, exposed to Archer at `127.0.0.1:8888`. Its `/etc/searxng/settings.yml` inherits defaults and has no autocomplete, language, safe-search, or minimum-length overrides in `search`. The installed default settings specify:

```yaml
search:
  autocomplete: "duckduckgo"
  autocomplete_min: 4
```

The installed handler and adapter confirm:

1. `/autocompleter` parses SearXNG query syntax. Engine/category/bang completions can be generated locally; normal query completions use one configured provider, not a metasearch across all result engines.
2. DuckDuckGo's adapter calls `https://duckduckgo.com/ac/?type=list&q=...&kl=...`. The provider receives partial query text and sees the SearXNG server's connection. Archer's browser remains on its local origin.
3. With `X-Requested-With: XMLHttpRequest`, SearXNG returns a flat string list. Without it, it returns OpenSearch JSON:

   ```json
   ["query", ["completion"], [], [], {"google:suggestrelevance": [600]}]
   ```

4. The relevance extension matters: the installed source says Chromium otherwise displays only three suggestions. Preserve aligned relevance values when normalizing completions.
5. The query echo is SearXNG's parsed query fragment, not necessarily the original request. A local request for `!go` returned `""` at index 0 and valid `!google`, `!google_images`, and other bang completions. That request took approximately 122 ms end-to-end. It is not a measurement of external-provider latency.
6. The handler does not enforce `autocomplete_min`; that is a UI threshold. Archer must choose and enforce its own threshold consistently.
7. Changing SearXNG preferences in a browser does not configure Archer's server-side HTTP client. Use instance defaults initially; if provider/language controls are added later, send validated explicit preferences and include them in cache keys.

A normal-query request returned HTTP 200. Its body was not retained in readable form by the initial PowerShell output, so this report does not claim a measured normal-query latency or completion quality benchmark. No full live searches or provider comparison bursts were performed.

Official references: [autocomplete endpoint source](https://github.com/searxng/searxng/blob/master/searx/webapp.py), [provider settings](https://docs.searxng.org/admin/settings/settings_search.html), [autocomplete adapters](https://docs.searxng.org/_modules/searx/autocomplete.html), and [result suggestions](https://docs.searxng.org/dev/result_types/suggestion.html).

## Proposed endpoint contract

Keep `GET /suggest?q=...` and `application/x-suggestions+json`. Both Helium and page JavaScript consume the same response. Do not forward the browser's XHR header to SearXNG.

- Echo the original incoming `q` at index 0, including for bangs. Use stripped text only for emptiness and minimum-length checks; forward the original syntax and spacing upstream.
- Set a maximum query length of 300 characters, matching Archer's other query APIs. For oversized input, return `[q, []]` without contacting upstream, so browsers still receive a valid suggestions response.
- Start normal provider suggestions at two non-whitespace characters. Use the same threshold in the page controller and endpoint. Allow syntax completion for shorter leading `!` or `:` queries, which SearXNG can handle locally. Verify the category syntax against the installed parser before implementing its exception.
- Keep the existing configurable 1.5-second timeout, passed explicitly to autocomplete so tests and future clients cannot accidentally lose it.
- Validate the top-level shape and every completion. Strip surrounding whitespace; remove empty strings, the unchanged query, overlong entries, and case-insensitive duplicates. Preserve provider order; return at most eight completions.
- Whitelist only understood metadata. Preserve relevance values corresponding to retained completions; if absent or invalid, generate a descending integer relevance list. Do not forward navigation-type metadata or arbitrary provider fields in this first release. Return empty description/URL arrays as appropriate for the OpenSearch shape.
- Keep cache keys case-sensitive and include any future provider/language settings. Add a bounded in-memory cache, initially 256 keys for 60 seconds. Cache successful empty results briefly (about five seconds); do not cache transport failures as successful empties. Nothing is persisted.
- Coalesce concurrent requests for the same key, so browser and page requests do not both call upstream. Ensure cancellation of one caller does not cancel work another caller is awaiting, and always remove completed or failed in-flight entries.
- Use `Cache-Control: no-store` on HTTP responses. Internal memory caching is independent of browser/disk caching.
- Timeout, connection failure, non-200 response, invalid JSON, and invalid response shape all degrade to a valid empty response. Suggestions must never block an ordinary form submission.

Put provider fetching, normalization, caching, and coalescing in `SearXNG.autocomplete()`, leaving the route responsible for the public query bound, headers, and JSON response. Test through `app.state.searx`, as search already does, rather than retaining two competing autocomplete paths.

## Dropdown interaction

Implement manual-selection list autocomplete following the [W3C combobox pattern](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/).

- Debounce input by about 180 ms. Request on typed input or focus with eligible existing text; never fetch for an empty field.
- Abort the previous fetch when text changes. Also use a monotonically increasing request generation: abort alone cannot prevent every stale response. Render only if the generation, current text, composition state, and focus still match.
- Invalidate pending work on clear, Escape, blur, submit, and composition start. Pause during IME composition; schedule once composition ends.
- Show at most eight rows, with no automatic first selection. Enter searches exactly what was typed unless a row was deliberately selected.
- Arrow Down/Up selects rows while focus stays in the input. Preserve the typed draft when previewing a selection; Escape restores it and closes the popup. Tab closes the popup and moves focus normally, without submitting or silently selecting a completion.
- Clicking or tapping a row fills the query and calls `form.requestSubmit()`, preserving hidden fields and existing loading feedback. Handle pointer/blur ordering so the row survives long enough to activate; do not disable touch scrolling in the list.
- Clear empties the input, cancels requests, closes the popup, and focuses the field. The existing `/` shortcut should work with the shared query-field selector, including on home, and ignore editable elements.
- Use `role="combobox"`, `aria-autocomplete="list"`, `aria-controls`, and synchronized `aria-expanded`/`aria-activedescendant`. The popup is a `listbox` and rows are `option`s with stable IDs and selection states. A polite status region can announce the available count.
- Build text with DOM APIs and `textContent`; never use provider HTML. Highlight only literal matched text, allowing suggestions that do not begin with the query.
- Place the popup in a positioned wrapper outside the clipped `.searchbar` surface. Match the search field's width, use existing dark-theme tokens, and layer it above result tabs and cards. Give it a viewport-limited scroll area and touch-friendly rows. Preserve the existing white Search button and home/results sizing.
- Keep the header expanded while the input is focused. Clear stale popup state on back/forward restoration. Coordinate Escape with the image-panel listener so closing suggestions does not also close an unrelated panel.
- With JavaScript unavailable or suggestions unavailable, both GET search forms continue working normally. Do not show an error row or spinner that blocks searching.

Two-character lookup is a proposed product choice, not SearXNG's current UI default. Keep it as a shared configurable value if upstream volume becomes a concern.

## Related searches and corrections

In `views.build()`, normalize `response.suggestions` into a bounded, ordered list. Render these links with Jinja on all result tabs where supplied; there is no additional request when search results already contain suggestions. If absent, retain a lazy `/suggest` fallback that uses the same normalized service and preserves `tab`. Do not treat absence as an error or fabricate related terms.

Autocomplete completions and related searches have different purposes: one helps finish partial text, the other helps reformulate a submitted query. The fallback is a pragmatic coverage choice, not equivalent data. Avoid another full search just to populate related chips on a specialized tab.

Retain spelling corrections as explicit links, never an automatic query rewrite. Preserve the result tab for SearXNG corrections; the Wikipedia correction is an entity lookup and may intentionally lead to All.

## Implementation order and files

1. Harden and consolidate the backend in `searxng.py` and `app.py`; add mocked tests for the public format and provider behavior.
2. Add a shared search-form macro or wrapper used by `home.html` and `search.html`, then the dropdown controller in `archer.js` and responsive styles in `archer.css`. A native `datalist` is unsuitable for the required control over keyboard behavior, styling, race handling, and accessibility announcements.
3. Add normalized related terms to the view model in `views.py`; render primary chips in `search.html` and update the existing JS fallback and correction URLs to retain the active tab.
4. Document partial-query forwarding in `README.md`/setup and update `concept-note.md` with the finalized behavior. Do not describe the feature as keeping keystrokes entirely local.

Exclude persisted history, trends, AI-generated completions, direct provider integrations, multiple-provider fan-out, rich entity suggestions, and navigation redirects from this implementation. Those require separate behavior and privacy decisions and are unnecessary to complete normal search suggestions.

## Acceptance checks

Use mocked upstream responses for automated tests; reserve a small final smoke check for the local instance.

- API: valid OpenSearch output and MIME; original query echo for bangs; relevance values stay aligned after filtering; blank/short/oversized queries avoid provider requests; Unicode and punctuation survive; duplicates and malformed completions are removed.
- Failure behavior: timeout, connection error, HTTP 429/500, invalid JSON, and invalid shape all produce valid empties; subsequent healthy requests recover.
- Cache: expiry, capacity, short-lived valid empty results, uncached failures, same-key concurrent coalescing, independent keys, and cancellation cleanup.
- Browser interactions with delayed mock responses: stale results after typing, clearing, Escape, blur, and submit never reopen the popup; IME sends no intermediate text; Enter with no selection retains the original draft; arrows, Escape, Tab, click, and touch selection behave consistently.
- Coverage: home and all four result tabs, hidden tab preservation, reset to page one, clear, `/` shortcut, back/forward restoration, image-panel keyboard interaction, and form submission when suggestions fail or JS is disabled.
- Layout/accessibility: desktop and narrow viewport screenshots, dropdown unclipped above tabs, long and Unicode suggestions, touch scrolling, combobox states, and a keyboard/screen-reader pass.
- Related searches: result suggestions render without an autocomplete request; absent suggestions use fallback; chips and SearXNG correction links retain the category.
- Helium: verify its actual visible suggestion count and selection behavior with the hardened OpenSearch response. Chromium source compatibility alone does not prove the current Helium UI behavior.
- Run the repository's `uv run pytest` suite after implementation, plus a browser interaction harness for the request-race and keyboard cases.
