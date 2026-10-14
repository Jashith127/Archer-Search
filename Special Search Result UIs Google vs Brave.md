# Special Search Result UIs: Google vs Brave

Oct 13, 2026 · @Jash

## Overview

Google has about 70 distinct query-triggered layouts; Brave has 25, built from its own index plus licensed data feeds. Each entry below gives the UI name, the queries that trigger it, an example query, and the data points it shows.

- **Entity panels** fire when the query names one known thing (a person, film, company). On desktop they sit in the right rail; on mobile they become the top of the page with tabs.
- **Live widgets** fire for data that changes by the minute (scores, weather, stock prices).
- **Utility widgets** answer the query in an interactive tool (calculator, converter, timer).
- **Vertical modules** pull in a specialist product (Maps, Flights, Shopping) as a block on the results page.

Data points vary by region, device and how well-known the entity is; a minor actor gets fewer fields than Zendaya. Layouts below reflect desktop and mobile in India and the US as documented through 2026.

## Google: entity panels (Knowledge Graph)

All of these share one skeleton: name, one-line type ("American actress and singer"), image strip, a short Wikipedia description, then type-specific facts and tabs. Google builds them from its Knowledge Graph, Wikipedia/Wikidata, licensed data and claimed profiles.

| UI | Triggers on | Example | Data points shown |
| --- | --- | --- | --- |
| Person: actor / entertainer | Name of a film, TV or music performer | Zendaya | Name, profession line, image grid, Wikipedia blurb, born (date, place), age, height, parents, siblings, partner, awards, social profiles (Instagram, X, TikTok, YouTube), Top stories, Movies & TV shows carousel with year and role, songs/albums, "People also search for", tabs (Overview, Movies, Songs, Images, News, Videos) |
| Person: athlete | Name of a professional player | Virat Kohli | All person basics + team, position/role, jersey number, career stats table (matches, runs, average, centuries), current season stats, recent matches, upcoming fixtures, trophies, salary/net worth (sometimes) |
| Person: politician / public official | Name of an office holder | Narendra Modi | Basics + current office, party, term start, predecessor, constituency, education, spouse, positions held timeline |
| Person: author / academic / business leader | Name of a writer, scientist or executive | Satya Nadella | Basics + organisation and title, books written (cover carousel), notable works, education, quotes, net worth (executives) |
| Movie | Film title | Dune: Part Two | Title, year, rating (U/A, PG-13), genre, runtime, poster, trailer, ratings (IMDb, Rotten Tomatoes %, audience %), Google user "liked this film" %, release date, director, box office, budget, cast carousel with character names, where to watch (streaming, rent/buy prices), showtimes near you if in cinemas, reviews (critic + user), tabs (Overview, Cast, Reviews, Showtimes, Trivia) |
| TV show | Series title | The Bear | Title, years aired, network, number of seasons and episodes, ratings, genres, where to watch, episode guide by season, cast, next episode date, reviews |
| Book | Book title | Atomic Habits | Cover, author, first published, pages, genre, ratings (Goodreads, Google users), description, buy/borrow options (Google Play, Amazon, libraries), other editions, "Readers also liked" |
| Music: song | Song title | (any hit single) | Artist, album, release date, genre, songwriters, play buttons for Spotify / YouTube Music / Apple Music, licensed lyrics (where available), music video, other recordings |
| Music: album / artist | Album or band name | Coldplay | Members, origin, genres, active years, albums carousel, top songs, upcoming tour dates with venues and ticket links |
| Company / organisation | Company or brand name | Infosys | Logo, type, founded, founders, CEO, headquarters, revenue, employees, parent/subsidiaries, stock price chip, customer service number, website, social profiles, products, Top stories |
| Landmark / attraction | Named place or monument | Mysore Palace | Photos, Google rating and review count, address, hours (open now), entry fee, typical time spent, popular-times chart, map, architect, built date, "Plan a visit", reviews, Q&A |
| City / country | Place name | Bengaluru | Map, weather chip, local time, population, area, elevation, leader (mayor/PM), points of interest, travel guide (things to do, hotels, flights), area code |
| Animal / plant / species | Species name | Snow leopard | Scientific classification (kingdom to species), conservation status, lifespan, mass, length, speed, diet, habitat map, 3D "View in your space" (selected animals), images |
| Concept / topic | Abstract noun or field | Photosynthesis | Wikipedia definition, diagram images, related concepts, "Things to know" accordion, related questions |
| Chemical / element | Element or compound name | Sodium chloride | Formula, molar mass, density, melting and boiling points, IUPAC name, 3D molecule viewer, hazards |
| Holiday / observance | Holiday name | Diwali | Date this year, next occurrence, observed by, significance, related holidays, countdown |
| Disease / symptom | Condition name | Dengue | Medically reviewed card: description, common symptoms, treatments, when to see a doctor, how common, how it spreads, sources (e.g. Mayo Clinic) |

## Google: live-data widgets

These replace the top of the page with a live card that refreshes in place.

| UI | Triggers on | Example | Data points shown |
| --- | --- | --- | --- |
| Sports: match / scores | Team names, "X vs Y", league names, "score" | India vs Australia | Live score, status (live, innings break, full time), overs/minutes elapsed, team logos, scorecard (batting, bowling or goals, cards, subs), commentary, win probability, lineups, key moments video clips, match info (venue, toss, umpires), head-to-head, tabs (Matches, News, Standings, Players) |
| Sports: league / tournament | League or event name | IPL 2026 | Standings table (P, W, L, NRR or points), fixtures and results list, top scorers/run-getters, playoff bracket, news |
| Sports: team | Club or national team | Royal Challengers Bengaluru | Last and next match, recent form, roster with positions, standings, team stats, news, highlights |
| Weather | "weather", "weather in X", "will it rain" | weather Bengaluru | Current temperature, condition icon, precipitation %, humidity %, wind speed, hourly graph (temperature, precipitation, wind tabs), 8-day forecast with highs/lows, severe-weather alerts, source (weather.com) |
| Stock / finance | Ticker or "X share price" | RELIANCE share price | Current price, change and % today, after-hours price, interactive chart (1D to Max), open, high, low, market cap, P/E ratio, dividend yield, 52-week high/low, previous close, exchange, follow button, compare tool, news |
| Market index | Index name | Sensex | Index value, change, chart, constituent movers |
| Currency converter | "X to Y", "usd inr" | 100 usd to inr | Two editable amount fields with currency pickers, live rate, timestamp, rate chart (1D to 5Y), disclaimer |
| Crypto price | Coin name + price or pair | bitcoin price | Price in local currency, change %, chart, converter fields |
| Flight status | Airline code + number | 6E 2131 | Airline, departure and arrival airports, scheduled vs estimated times, terminal, gate, status (on time, delayed, landed), date picker |
| Local time / time zones | "time in X", "time difference" | time in London | Current time, date, time zone name and UTC offset, difference from your time |
| Sunrise / sunset | "sunrise", "sunset X" | sunset Mumbai | Time, date, location |
| Elections | Election names, "election results" | Karnataka election results | Seat or vote tallies by party, majority line, results map by constituency, candidate cards, live updates, polling-place lookup (US) |
| Package tracking | Courier tracking number | (a tracking number) | Detected courier, status, link to the courier's tracking page |
| Trending / live events | Breaking events (disasters, launches) | earthquake today | SOS alert banner, map, magnitude, time, safety tips, emergency numbers, Top stories |

## Google: utility widgets

The answer is an interactive tool rather than a page of facts.

| UI | Triggers on | Example | Data points shown |
| --- | --- | --- | --- |
| Calculator | Arithmetic expressions, "calculator" | 15% of 2400 | Expression, result, full scientific keypad (sin, log, √, π, Rad/Deg), history |
| Unit converter | "X in Y" for measurable units | 5 feet in cm | Category dropdown (length, mass, speed, data, etc.), two editable value fields, formula hint ("multiply by 30.48") |
| Dictionary | "define X", "X meaning" | define serendipity | Word, phonetic spelling, audio pronunciation, part of speech, numbered definitions, example sentences, synonyms/antonyms, etymology tree, usage-over-time chart, translations, source (Oxford Languages) |
| Translate | "translate X to Y", "X in Hindi" | thank you in kannada | Source and target language pickers, editable text box, translation, romanised pronunciation, audio, "Open in Google Translate" |
| Timer / stopwatch | "timer", "set timer 5 minutes" | timer 10 min | Countdown with start/reset, stopwatch tab, alarm sound |
| Math solver / graphing | Equations or functions | solve 2x+3=11, y=x^2 | Step-by-step solution, graph with zoom and pan, related practice problems |
| Nutrition facts | "calories in X", food names | calories in banana | Serving-size picker, calories, total fat, cholesterol, sodium, potassium, carbs, fibre, sugar, protein, vitamins as % daily value, source (USDA) |
| Random tools | "flip a coin", "roll a die", "random number", "spinner" | flip a coin | Animated result with "flip again", number range inputs, fidget spinner/number wheel |
| Colour picker | "color picker", hex code | #1a73e8 | Colour swatch, sliders, HEX, RGB, CMYK, HSV, HSL values |
| Metronome / tuner | "metronome", "tuner" | metronome | BPM slider and play; tuner uses microphone and shows note and pitch offset |
| Speed test | "internet speed test" | speed test | Run button, download Mbps, upload Mbps, latency, provider (M-Lab) |
| Loan / mortgage calculator | "mortgage calculator", "EMI calculator" (US/varies) | mortgage calculator | Loan amount, interest rate, term inputs; monthly payment output |
| Tip / percentage / BMI calculators | "tip calculator", "bmi calculator" | tip calculator | Bill, % and split inputs; tip and per-person total (BMI: height, weight inputs and category) |
| Games and easter eggs | "snake game", "tic tac toe", "solitaire", "pac-man" | tic tac toe | Playable game embedded in the results page |

## Google: commerce, travel and local modules

These embed a Google vertical (Maps, Flights, Hotels, Shopping, Jobs) as a block on the results page.

| UI | Triggers on | Example | Data points shown |
| --- | --- | --- | --- |
| Local pack (3-pack + map) | "X near me", category + area | cafes in Indiranagar | Map with pins, 3 listings each with name, rating, review count, price level (₹–₹₹₹₹), category, address, open/closes time, review snippet, photo, buttons (Website, Directions, Call), filters (Rating, Open now, Price) |
| Business profile | A specific business name | Toit Brewpub | Photos, rating, reviews, category, address, hours, phone, website, popular times, menu link, reserve/order buttons, Q&A, "From the owner" posts, amenities (outdoor seating, Wi-Fi), price per person |
| Hotels | "hotels in X", hotel name | hotels in Goa | Map, date and guest pickers, hotel cards with price per night, rating, class (stars), amenities icons, deal badges, price comparison across OTAs, filters |
| Flights | "flights X to Y" | flights Bengaluru to Delhi | From/to and date fields, cheapest and fastest options, airline, duration, stops, price, price-trend graph, "Show flights" into Google Flights |
| Travel guide / things to do | "things to do in X", "X travel" | things to do in Madikeri | Top sights carousel with rating and photo, itineraries, best time to visit, typical costs |
| Shopping / product grid | Product or category with buying intent | noise cancelling headphones | Product cards (image, name, price, store, rating, review count, delivery and returns), filters (price, brand, features), deals/price drop tags, popular products carousel, "Compare prices" |
| Product panel | Specific product model | Sony WH-1000XM5 | Image gallery, price range across stores, specs, user reviews summary with pros/cons, "price insights" history chart, buying guide |
| Recipes | Dish name or "X recipe" | paneer butter masala recipe | Recipe cards with photo, site, rating, review count, total time, key ingredients, filters (ingredients, cook time), videos |
| Jobs | "X jobs", "jobs near me" | UX designer jobs Bangalore | Job listings with title, company, location, posting date, salary (if given), job type, apply links across boards, filters (date, type, company) |
| Events | "events in X", "concerts this weekend" | events in Bangalore this weekend | Event cards with name, date and time, venue, image, ticket links, filters (today, this week, online) |
| Showtimes | Movie in theatres or "movies near me" | movies near me | Theatre list, show times by format (2D, IMAX), booking links, date tabs |
| Vacation rentals | "villas in X", "vacation rentals X" | villas in Coorg | Rental cards with nightly price, bedrooms, guest count, amenities, rating, map, booking-site links |

## Google: general results-page modules

These can appear on almost any query and wrap around the panels above.

| UI | Triggers on | Example | Data points shown |
| --- | --- | --- | --- |
| AI Overview | Informational or multi-part questions | how does a heat pump work | Generated summary paragraphs and bullets, cited source cards with favicon and title, expandable "Show more", follow-up into AI Mode |
| AI Mode | Tab or button for open-ended questions | plan a 3-day trip to Coorg | Chat-style answer, source links panel, follow-up box, inline maps, product or place cards |
| Featured snippet | Direct questions with one clear answer | how long to boil an egg | Extracted paragraph, list or table from one page, bolded answer, page title and URL, image |
| Top stories | Newsworthy names and topics | Zendaya | News cards with headline, publisher, logo, time posted, thumbnail; "Live" badges |
| People also ask | Most queries | Zendaya | Expandable related questions, each opening a snippet with source link |
| Videos carousel | How-to and visual topics | how to tie a tie | Video thumbnail, title, channel, platform, duration, upload date, key moments (timestamped chapters) |
| Short videos | Trending and lifestyle topics | Bangalore street food | Vertical clips from YouTube Shorts, Instagram, TikTok with creator name |
| Images pack | Visual queries | mid century living room | Image grid, source site, filter chips (colour, type, style) |
| Discussions and forums | Opinion and experience queries | is ux design a good career | Reddit, Quora and forum threads with title, community, answer count, date, top snippet |
| Perspectives / social posts | Personalities and trending topics | Zendaya | X posts carousel (handle, text, time), creator posts, Instagram tile |
| Things to know / Refine chips | Broad topics | learn guitar | Sub-topic accordion and filter chips (beginners, chords, online) |
| Fact check | Claims fact-checked by publishers | (a viral claim) | Claim, claimant, rating (False, Misleading), fact-checker name and link |
| Scholarly articles | Academic terms | transformer neural network | Google Scholar papers with author, citations count |
| Sitelinks and site search | Brand or website name | Swiggy | Main result plus 4–6 deep links and a search box for that site |
| Related searches | Bottom of most pages | Zendaya | Grid of follow-up queries, sometimes with entity thumbnails |

## Brave Search: special UIs

Brave has 25 distinct layouts. Live widgets come from licensed third-party feeds (sports, stocks, weather; crypto via CoinGecko); entity panels come from its own index plus Wikipedia/Wikidata. Field lists for the live widgets are taken from Brave's published API schema, which mirrors what search.brave.com renders.

**Entity and page-enhancement UIs**

| UI | Triggers on | Example | Data points shown |
| --- | --- | --- | --- |
| Infobox (entity panel) | People, companies, places, films, concepts | Zendaya | Title, category line, image(s), long description (Wikipedia), attribute pairs (born, parents, occupation, years active), social profiles, official website, ratings for films/shows (e.g. IMDb, Rotten Tomatoes), providers/sources, related entities |
| Movie / TV infobox | Film or show title | Dune: Part Two | Poster, release date, runtime, genre, director, cast, ratings, plot summary |
| Local results / places | "X near me", category + city | cafes in Indiranagar | Map with pins, place cards: name, category, address, distance, rating and review count, price range, cuisine, opening hours (open now), phone, photos, reviews, website, reserve/order actions |
| Discussions | Opinion, advice, troubleshooting | is ux design a good career | Forum threads from Reddit, Stack Exchange etc.: forum name, thread title, question text, number of answers, score/upvotes, top comment, age |
| FAQ | Pages with FAQ markup | (product or service names) | Question, answer, source page |
| News cluster | Newsworthy topics | Zendaya | Article cards: headline, publisher, favicon, thumbnail, time ago, "Live" flag, topical sub-clusters |
| Videos | Visual or how-to queries | how to tie a tie | Thumbnail, title, creator/channel, publisher, duration, views, age |
| Rich snippets from page markup | Pages with schema.org data | paneer butter masala recipe | Recipe (rating, cook time), product (price, rating), review stars, article byline, music/song, creative-work metadata |
| Images | Visual queries | snow leopard | Image strip at top of All results; full grid in Images tab with source and size |

**Live-data widgets**

| UI | Triggers on | Example | Data points shown |
| --- | --- | --- | --- |
| Weather | "weather X" | weather Bengaluru | Location, current temperature, feels like, condition, humidity, pressure, dew point, UV index, cloud cover, visibility, wind speed and direction, rain/snow, sunrise and sunset, 3-hourly forecast, daily forecast (morning/day/evening/night temperatures, min/max, precipitation probability, moonrise/moonset), weather alerts |
| Stocks | Ticker or company + stock | RELIANCE stock | Symbol, company name, exchange, latest price, change and %, open, high, low, previous close, volume, market cap, P/E ratio, 52-week high/low, price chart by time range, market open/close times |
| Currency converter | "X to Y" currencies | 100 usd to inr | Amount, from/to currency, rate, converted result, timestamp, exchange-rate chart |
| Cryptocurrency | Coin name, ticker, coin pair | bitcoin price | Price, market cap and rank, 24h volume, 24h high/low, 24h change, circulating/total/max supply, all-time high/low with dates, charts (price, market cap, volume), converter |
| Crypto wallet lookup | ENS name or wallet address | vitalik.eth | Asset holdings and total valuation |
| Cricket | Teams, tournaments, "score" | India vs Australia | League and season, teams with logos, runs and overs per side, match status, start time, highlights video |
| Football (soccer) | Clubs, leagues, fixtures | Real Madrid | League, round, teams, score, penalty score, aggregate score, minute played, match events (goals, cards, subs with player names), match statistics (possession, shots), lineups with formation, starting XI, subs and coach |
| Formula 1 | "F1", Grand Prix names | F1 standings | Next/previous/live race (circuit, city, laps, fastest lap), weekend schedule, race results, qualifying, sprint results, driver standings (points, wins, gap), constructor standings |
| Basketball / American football / baseball / ice hockey | Teams, leagues (NBA, NFL, MLB, NHL) | Lakers score | League, teams, score, status and time elapsed, per-quarter/inning/period score table (baseball adds hits and errors; hockey adds shootout), highlights video, standings |
| Package tracking | Courier tracking number | (a tracking number) | Detected courier and service, tracking number, link to courier's tracking page |

**Utility widgets**

| UI | Triggers on | Example | Data points shown |
| --- | --- | --- | --- |
| Calculator | Math expressions | 15% of 2400 | Expression, answer, keypad |
| Unit converter | "X in Y" units | 5 feet in cm | Amount, from/to units, measurement type, result |
| Dictionary | "define X", "X meaning" | define serendipity | Word, pronunciation and audio, part of speech, definitions, example uses, related words, labels (formal, slang), source dictionary |
| Unix timestamp | A Unix epoch number | 1760000000 | Human-readable date and time conversion |

**Brave-only features**

| UI | Triggers on | Example | Data points shown |
| --- | --- | --- | --- |
| Answer with AI / Ask Brave | Questions; "Ask" button or automatic summary at top | how does a heat pump work | Generated answer, inline citations, source list, entity info, follow-up chat |
| Goggles | User-applied filter on any query | (any query + a Goggle) | Same results re-ranked by a community rule set; the active Goggle name shown |

## Google vs Brave, by query type

Google's lead is in commerce, travel and deep entity tabs; Brave matches it on live data (weather, stocks, sports, crypto) and adds Goggles and richer crypto data.

| Query type | Google | Brave |
| --- | --- | --- |
| Person (e.g. Zendaya) | Full panel with tabs, filmography carousel, family, socials, news | Infobox with description, attributes, socials, news cluster |
| Movie / TV | Ratings, cast, where to watch with prices, showtimes, reviews | Infobox with ratings, cast, basics |
| Sports | Live scorecards, commentary, win probability, standings, players | Live scores, events, lineups, stats for soccer, cricket, F1, US leagues |
| Weather, stocks, currency | Full widgets | Full widgets, comparable depth |
| Crypto | Price and converter | Price, supply, all-time highs, charts, wallet lookups |
| Local places | Local pack, business profiles, popular times, booking | Map and place cards with hours, ratings, price |
| Shopping | Product grid, price history, store comparison | Rich snippets only, no shopping vertical |
| Flights, hotels, rentals | Dedicated booking modules | None |
| Jobs, events, showtimes | Dedicated modules | None |
| Utilities | 14 tools incl. timer, translate, games, speed test | Calculator, unit, dictionary, timestamp |
| AI answers | AI Overview, AI Mode | Answer with AI / Ask Brave |
| Custom ranking | None | Goggles |

## Sources

- [Google Search Central: Visual Elements gallery](https://developers.google.com/search/docs/appearance/visual-elements-gallery)
- [Google Search Central: structured data feature list](https://developers.google.com/search/docs/appearance/structured-data/search-gallery)
- [Brave Search API: Rich search reference (widget fields)](https://api-dashboard.search.brave.com/api-reference/web/rich_search/index.html.md)
- [Brave Search API: Web search service (rich and local enrichments)](https://api-dashboard.search.brave.com/documentation/services/web-search)
- [Brave Search Help: features overview](https://safe.search.brave.com/help)
- [Brave Search: Goggles and Answer with AI](https://brave.com/search/)
- [CoinGecko: Brave crypto search case study](https://www.coingecko.com/learn/brave-browser-case-study)

Google panel field lists come from observed results pages and change often by region; check a live query before building on any single field.
