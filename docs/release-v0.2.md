Archer v0.2 adds product, film, TV, actor and music panels, lets search fall back between engines when one is rate-limited, redesigns the home page and companion app, and does less work in the background.

### Install

Download **ArcherSetup.exe** below and run it on Windows 10 or 11. Setup downloads Python and dependencies automatically. You need an internet connection and at least 1 GB of free space. Docker Desktop is optional: use it for a local SearXNG instance, or start with DuckDuckGo.

**Updating from v0.1:** run the new setup. It replaces Archer's program files but keeps your settings (`.env`), your `data/` folder and your SearXNG folder.

### Added

- **Product panels** for searches like `sony wh-1000xm5` or `iphone 16 pro price`, in both search and AI Mode:
  - **Prices:** the price range across stores, with the size or capacity each price is for and the lowest store. Prices are taken only from stores' own product pages; trade-in, EMI, carousel and foreign-currency figures are skipped.
  - **Reviews:** an expert score ring and store star ratings.
  - **Photos:** credited photos from the maker, stores and review sites, in a gallery that always shows the whole photo.
  - **Overview, Reviews and Specs tabs**, with highlights, what reviewers say, colour and storage variants, and the model line.
  - **Second-hand:** for models three or more years old, listings from your country's used markets (OLX and Quikr in India, eBay and Swappa in the US, and so on).
  - **AI Mode** opens with the same product card, and a comparison ("iPhone 16 vs iPhone 17") gets a card for each product it names.
- **Movies and shows grid for actors:** posters with year, kind and role, filters for All, Movies and TV, sorting by best known or newest, and "Show all".
- **Cast & crew for films and series:** key crew as chips, then every cast member with photo and character. Credits come from Wikidata with Wikipedia photos, or from TMDB when `TMDB_API_KEY` is set.
- **Where to watch** shows each streaming service's mark even without a TMDB key.
- **Ambiguous names follow the web results.** A brand's own site ranked first means its company, otherwise the top-ranked matching Wikipedia article wins ("dune" → the 2021 film, "apple" → Apple Inc.). The other meanings appear as chips, and a trailing kind word ("archer show", "dune movie") picks the kind.
- **Spotify for every song:** songs without a Spotify ID on Wikidata now find their track through a web search and get the Spotify player.
- **Rate-limit fallback:** when an engine is rate-limited, Archer moves on to the next search provider you've set up (keyed Google and Brave, DuckDuckGo, SearXNG) and skips the limited one for ten minutes. A dismissible banner names each limited engine in its brand colour. SearXNG suggestions fall back to DuckDuckGo's.
- **News pictures:** a news story that arrives without a thumbnail shows the article's own preview image (its `og:image`). The page is read through the same public-addresses-only reader the AI answers use, and only Archer's own pages can request it.
- **Scroll arrows** on the image, news and video rows. The side that has more to show fades out instead of cutting a card in half, and cards snap into place. Images show up to 16 cards and news up to 10.
- `installer/build.sh` builds the same `ArcherSetup.exe` on Linux (Roslyn from the .NET 8 SDK against the .NET Framework 4.8 reference assemblies). This release was built with it.

### Changed

- **Home page:** a single search card with a Search / AI Mode switch and starter suggestions per mode. The card's border turns into pixel dots near the cursor. The corner keeps only the settings icon.
- **Companion app and setup:** redesigned as a plain preferences window, with tabs across the top, settings in label/control rows, providers as radio lists beside the fields they need, and a save bar for unsaved edits. Setup is a narrow column with a step bar.
- **Panel layouts are balanced:**
  - Fact tiles always fill complete rows, so no tile is left stretched across a row on its own.
  - Songs put their facts in one row under the video, and the lyrics card grows to end level with the left column.
  - An About section with a single fact gives it the full width.
- **Spotify and Apple Music players** load with the page and show on their own, with no card or background around them.
- **Tall pictures** are cropped to their box around the picture's content instead of stretching the panel.
- The quick answer label is centred, underlined text.

### Fixed

- Opening the companion app at a specific pane (`/app#ai`) no longer jumps down the page.
- Start-up failures now exit cleanly instead of leaving a stuck tray icon.

### Performance

- **Faster start-up:** the tray icon appears at once while the server loads, and the "already running?" check binds the port instead of waiting on an HTTP request. Docker Desktop starts in the background when SearXNG needs it.
- **AI answers:** streams now wait for the next update instead of checking 20 times a second for every open answer.
- **Home page animation:** the dot field stops animating while it's scrolled off screen.
- **Page loads:** Archer's stylesheet and script are cached by the browser for good, since each new version gets a new address. Pages no longer re-check them on every load.
- Images decode off the main thread.

### Screenshots

The screenshots were taken from Archer's offline test fixtures, so pictures are placeholder art and names and figures are sample data. The Spotify, Apple Music and YouTube players can't load offline, so their frames are labelled instead.

**Home page**

![Home page](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/home.png)

**Product panel**

![Product panel](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/product.png)

**Song with video, players, lyrics and a row of facts**

![Music panel](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/music.png)

**Film with where to watch and balanced tiles**

![Film panel](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/film.png)

**Cast & crew**

![Cast and crew](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/film-cast.png)

**An actor's movies and shows**

![Movies and shows](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/actor-filmography.png)

**Series with a single streaming service**

![Series panel](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/series-watch.png)

**Organisation panel: top story and a 2 × 2 tile grid**

![Organisation panel](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/entity-tiles.png)

**News row: arrows, edge fade, and article images for stories that had none**

![News row](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/strips.png)

**Image row, scrolled: both arrows and both fades**

![Image row](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/strip-images.png)

**Rate-limit banner**

![Rate-limit banner](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/rate-limit.png)

**Companion app**

![Companion app](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/companion.png)

**Setup**

![Setup](https://raw.githubusercontent.com/Jashith127/Archer-Search/v0.2/docs/screenshots/v0.2/setup.png)

### Verification

- **Tests:** 296 Python tests pass and 1 fails. The failing test (`test_companion_pages_render_every_provider_without_keys`) expects Microsoft Edge to be detected as an installed browser. It fails the same way on v0.1 in the Linux container where this release was built.
- **Installer:** the installer UI is unchanged since v0.1. `ArcherSetup.exe` was compiled on Linux, not with the Windows compiler used for v0.1. Its metadata checks out: it's a Windows GUI app for .NET Framework 4, and it embeds the app payload, installer UI, fonts, icon, version info and manifest.
- **Not yet verified:**
  - The exe hasn't been run on Windows, and a clean-machine install hasn't been tested.
  - The new screens were checked against offline fixtures only, not live search providers or real news sites.
- This v0.2 executable is unsigned.

`SHA256SUMS.txt` contains the SHA-256 checksum for the installer.
