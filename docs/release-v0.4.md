Archer v0.4 adds panels for Formula One, football, basketball and esports, makes startup and searching noticeably faster, redesigns the companion app, and makes AI answers stricter about their evidence.

![A Formula One driver panel: championship standing, season round by round, career totals and the next round's track](https://raw.githubusercontent.com/Jashith127/Archer-Search/main/docs/screenshots/v0.4/f1-driver.png)

![A Grand Prix panel: the circuit as a dot matrix, podium, race stats, weekend schedule and past winners](https://raw.githubusercontent.com/Jashith127/Archer-Search/main/docs/screenshots/v0.4/f1-grand-prix.png)

![A league panel: the title race, this round, places at stake and the full table](https://raw.githubusercontent.com/Jashith127/Archer-Search/main/docs/screenshots/v0.4/league.png)

![The VCT season page: champion, standings and results](https://raw.githubusercontent.com/Jashith127/Archer-Search/main/docs/screenshots/v0.4/vct.png)

### Install

Download **ArcherSetup.exe** below and run it on Windows 10 or 11. Setup downloads Python and dependencies automatically. You need an internet connection and at least 1 GB of free space.

**Updating from v0.3:** run the new setup. It replaces Archer's program files but keeps your settings (`.env`) and your `data/` folder.

### Added

- **Formula One:** driver and team panels (standing with the drivers around them, every result this season, form by round, career totals and titles), Grand Prix panels (podium, race stats, weekend rail, past winners) and circuit panels with the track drawn as a dot matrix and a car lapping it. Searching "Formula One" shows the championship: both tables, the next round, the last podium and the calendar. Car models get a panel with a matted picture. Data comes from Jolpica; no key needed.
- **Team sports:** club panels for football, basketball, American football, baseball and ice hockey (table, results, fixtures, live game, squad, coach) and league panels with tables and this round, from ESPN's public data.
- **Esports:** Valorant team and player panels from VLR.gg, **VCT season pages** (try "vct 2026" or "valorant champions 2024") with champion, standings, results and upcoming matches, and panels for teams and players in other games from Liquipedia.
- **Card system:** every fact in a panel now takes the form that suits it: dates with years ago, spans as a rail, numbers with units, people with monograms, places and chips. Weather, time, sunrise, crypto, colour, finance, random and timer panels use pixel-dot range bars, split bars and day rails.
- **GitHub links in AI questions:** a github.com repository link is read from the GitHub API (facts and README), not the page.
- **Result quality signals:** results several engines agree on get a nudge up, duplicates of the same page (www, AMP, tracking links) go down, and for "latest", "news", "today" and similar searches newer pages rise.
- **Start-up splash:** the companion window opens on a splash while the server starts.

### Changed

- **Companion app and setup redesigned** in Archer's own look: the results-page header, a hero per section with the current value large, flat rows, and a control that fits each kind of setting (provider cards, pill levels, tiles, sliders, live previews of the fallback chain, sign-in timeline and browser dialogs).
- **Results no longer wait for a slow knowledge panel.** If Wikipedia isn't ready 0.3 s after the results, they appear first and the panel, its AI card and related rows follow. The loading placeholder is a quiet version of AI Mode's pixel skeleton, and the loader runs slower and fainter.
- **Native SearXNG is the default** wherever a native install exists.
- **AI answers are stricter about evidence:**
  - A search snippet alone can no longer carry a summary, though it can still be cited.
  - Dates are checked against the sources like numbers are.
  - One unsupported section or table cell is dropped; it no longer ends the whole answer.
  - If no section is supported, the answer says so rather than reporting an "inconsistent" reply.
  - Comparisons such as "riot vs valve" now search for real queries, forum threads count as discussion, not proof, and navigation and link-farm lists are stripped from pages you read.
  - Saved answers carry a version, so a new release never replays an old answer.

### Performance

- **Faster start:** the port opens and the window appears before the app loads (about 0.5 s instead of 3.4 s), and one TLS context is shared across all connections.
- **Faster searches:** results stream as soon as they are ready, a SearXNG that isn't running is skipped instead of costing about 2 s, and Wikipedia lookups run side by side.
- **Faster native SearXNG:** setup compiles Python's standard library and SearXNG's helpers to bytecode (its import takes about 24 s without), and Archer turns off SearXNG's Wikidata engine per request, cutting the median first search from 2.2 s to 1.4 s.

### Fixed

- **Blank AI Mode:** the page-in animation left a transform on the page that clipped the full-screen AI panel. It no longer does.
- **Setup could not run SearXNG on its own:** the installer payload now carries the native SearXNG module and its Windows shims.
- **SearXNG on a moved port:** Archer reads its address per request, so a port change at startup no longer sends every search to the fallback.
- **DuckDuckGo bot checks** are recognised as rate limits, so the fallback chain takes over.

### Verification

- **Tests:** 511 Python tests pass.
- **Not yet verified:** a clean-machine install.
- This executable is unsigned.

`SHA256SUMS.txt` contains the SHA-256 checksum for the installer.
