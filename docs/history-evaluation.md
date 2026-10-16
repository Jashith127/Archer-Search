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

| | As first shipped | After the changes |
|---|---|---|
| Precision of shown matches (labelled pairs) | 0.63 | **0.88** |
| Same-need pairs found | 0.92 | 0.86 |
| Related pairs found | 0.87 | 0.65 |
| New-topic searches falsely matched | 0.74 | **0.12** |
| Variant found: typo / two typos / spaces removed / half-typed | 0.90 / 0.73 / 0.84 / 0.88 | **0.95 / 0.84 / 0.97 / 0.91** |
| Searches showing related searches (replay) | 81 % (2.97 each) | 49 % (1.14 each) |
| Latency per search, median / p95 | 17 / 34 ms | 28 / 55 ms |

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
