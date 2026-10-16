# History matching: evaluation and the rules it led to

October 2026. Archer's history features (related searches, "You found these before", history
search, topics, site affinity) were evaluated on a real three-month Helium history: 2,233 exported
entries, of which 1,436 were web searches (1,267 distinct) after removing site navigation that
Chromium records as keyword searches. The examples below are paraphrased; no searches from that
history are in this repository. Re-run everything with:

    python scripts/export_history.py --browser helium --out my-searches.json
    uv run python scripts/eval_embeddings.py my-searches.json --model potion-base-8M

The model was a stand-in: this environment could not download `potion-base-8M`, so WordLlama's
`l2_supercat` (a static token-embedding model like Model2Vec's potions, 256 dimensions) was
converted to a Model2Vec folder and run through Archer's own code. Numbers for potion will differ;
the script above measures them.

## What was run

| Run | Size | Ground truth |
|---|---|---|
| Hand-labelled search pairs (same need / related / unrelated) | 660 pairs | labelled blind to scores |
| Variants of every search: typos, two typos, spaces removed, word split, word dropped, word order, filler words, half-typed prefix | 8,113 | the original search |
| Searches on topics absent from the history | 121 | any match is false |
| Near-misses: same search, different model or version number | 242 | should not recall the other model's pages |
| Replay: every search in date order, with history as it was then | 1,436 × 4 configurations | pages actually reopened |
| Topic clustering over the whole history | 1,267 searches | inspection |

## Results

| | As first shipped (loose cutoff) | Embedding alone, strict cutoff (0.48) | After the changes |
|---|---|---|---|
| Precision of shown matches (labelled pairs) | 0.63 | 0.85 | **0.88** |
| Same-need pairs found | 0.92 | 0.78 | 0.86 |
| Related pairs found | 0.87 | 0.60 | 0.65 |
| New-topic searches falsely matched | 0.74 | 0.14 | **0.12** |
| Variant found: typo / two typos / spaces removed / half-typed | 0.97 / 0.90 / 0.97 / 0.97 | 0.90 / 0.73 / 0.84 / 0.88 | 0.95 / 0.84 / 0.97 / 0.91 |
| Searches showing related searches (replay) | 81 % (2.97 each) | 48 % (1.05 each) | 49 % (1.14 each) |
| Latency per search, median / p95 | 17 / 34 ms | 17 / 34 ms | 28 / 55 ms |

The shipped cutoff found almost everything because it matched almost anything: three in four
searches on topics absent from the history still "found" something. Tightening the cutoff alone
fixes that but loses typo and spacing variants; the blend and the spelling rules recover most of
them at the same precision.

Word matching alone with its refitted cutoff (0.45) reaches 0.94 precision but finds only 0.70 of
same-need pairs and 0.45 of related ones; the embedding is what recovers rewordings, abbreviated
and run-together spellings, and typos.

Ranking quality on the labelled pairs (area under the ROC curve, same need vs unrelated): word
matching 0.86, embedding 0.95, blend 0.95.

## Failure modes, causes and what was done

| Failure | Example (paraphrased) | Cause | Change |
|---|---|---|---|
| Cutoff far too loose | a third of shown matches unrelated | calibrated at mean + 3.5σ of unrelated sentences; the hard negatives are near-topic, not random | cutoff at mean + 7σ (0.39 for the stand-in), fitted to 85–88 % precision |
| One shared word decides a short search | a country name alone linked a car brand search and a festival search; a band name linked an energy drink | short queries are dominated by one token | one-word searches need +0.1 unless one contains the other |
| Calculator-style searches cluster | a subtraction matched percentage and unit conversions | numbers embed alike | sums, conversions and currency never match (`personalize.utility`) |
| Half-typed fragments | a single letter matched a celebrity's name | the browser recorded partial entries | searches under 3 characters never match |
| Run-together or split words | "light house" vs "lighthouse" | static models tokenize them differently; word matching sees no shared word (found 3 %) | spelling match after removing spaces, or ~1–2 typos apart |
| Abbreviations and niche names | a company's initials; a design system's product name vs its description | the small model lacks the knowledge | searches that opened a page now in the results are listed as related |
| Near-identical models | phone 16 vs 15, GPU 4050 vs 4060 | embeddings ignore which number | recall skips pages naming a different number of the same shape |
| Three pages of one site crowd the recall box | three pages of an API console hid the one reopened | top-3 by score | one page per site |
| Junk pages recalled | challenge screens, 404s, sign-in pages | they were "opened" | skipped by title |
| Topics chain across meanings | a single letter → a browser → an actress | greedy centroid clustering with a loose cutoff | stricter cutoff, utility and fragments excluded, members must stay close to the final centre |
| Site affinity barely predicts | top site matched the one opened 21 % of the time (popularity 46 %) | revisits often follow unrelated wording; linkage in the export is noisy | weight 0.3 → 0.15, needs two visits |

## Known limits

- Word senses: a search for a poem form still matched an AI model of the same name; a drink brand
  matched a song. A static model has one vector per word.
- Near-miss names without numbers (two car models from one maker) still look the same.
- Exported page links are inferred from timing for most visits (Archer and many engines send no
  referrer), so replay re-finding rates (22 % exact page, 39 % same site) are a lower bound.
  Archer records actual result clicks, which avoids this.
- One person's history, one stand-in model. Re-run on potion and on other histories before
  trusting the exact cutoffs.

## Second round: from failures to fixes

Each remaining failure was treated as a problem to solve, prototyped against the same data and kept
only if the numbers improved. Ablations switch each rule off (`--without context,prefix,acronym,site,names`).

| | First round | Second round | Word matching only |
|---|---|---|---|
| Precision of shown matches | 0.88 | **0.92** | 0.94 |
| Same-need pairs found | 0.86 | **0.90** | 0.73 |
| Related pairs found | 0.65 | **0.69** | 0.47 |
| New-topic searches falsely matched | 0.12 | **0.10** | 0.025 |
| Half-typed variant found | 0.91 | **0.97** | 0.97 |
| Replay: reopened page / site in recall | 0.22 / 0.39 | **0.27 / 0.43** | 0.23 / 0.36 |
| Latency median / p95 | 28 / 55 ms | 30 / 68 ms | |

| Problem | Idea | Evidence |
|---|---|---|
| Words with two senses; topic-level relations missed | A past search means what the searches around it meant: its vector is blended 50/50 with its 30-minute session's | At a fixed cutoff: precision +0.05, new-topic false matches -0.04; with the cutoff refitted (×0.96), precision and recall both rose |
| Half-typed searches | A search that is the start of a past one matches it | Half-typed found 0.88 → 0.97 |
| Initials | "dwc" matches the three words it abbreviates | Unit tests; rare in this history |
| Searching as navigation | A search naming a site you use recalls that site's most visited page first | Recall shown 0.30 → 0.34 of searches, reopened page 0.23 → 0.27 |
| Look-alike products without numbers | A word the embedding model's vocabulary doesn't know, beside the same brand, is a product name; recall skips the other product | Hand near-misses kept out 15 → 23 of 30; spelling variants exempt |
| Version numbers beside shared tokens | Compare only the tokens that differ ("f1 2021" / "f1 2024") | Included above |
| Errors no rule fixes | "Not related" on every earlier-search chip and recalled page, remembered both ways | Removes a wrong pair permanently; cleared with history |
| Slower matching | Cached text features, length checks before string rules, context rebuilt every 25 searches | ~150 → ~40 ms per search under load |

Still open: one-word sense collisions on topics with no history (a poem form vs an AI model of
the same name) — session context needs a session; the "Not related" button is the backstop. Word
matching is now as robust to typing variants as the embedding (the spelling rules apply to both);
the embedding's value is rewording: 0.90 vs 0.73 of same-need pairs, 0.69 vs 0.47 of related ones.
