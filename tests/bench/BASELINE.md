# Baseline — unmodified implementation
# Captured 2026-10-18 against the running local Archer (DeepSeek / deepseek-flash,
# native SearXNG on :8888, app on :4747). All 16 cases forced fresh runs with
# `regenerate: true` so the saved-answer replay cache could not substitute a
# cached answer for a real run.
#
# Command:
#   .venv\Scripts\python.exe tests/bench/run_bench.py --fresh --out tests/bench/results-baseline

baseline:
  cases: 16
  passed: 6
  failed: 10
  terminal_errors: 2
  median_first_block_s: 1.336
  median_total_s: 2.463
  total_model_calls: 15
  total_cost_usd: 0.010308
  citation_failures: 0
  unsupported_number_failures: 0
  snippet_only_failures: 8
  human_review_completed: 0

findings_from_baseline:

  F-1. Snippet-only summaries (8/16 cases).
       The dominant failure. On the compact path `execute` only reads pages when the
       question matches a narrow regex, so most answers rest entirely on unverified
       search snippets. Target of D1-3 / U3.
       Affected: factual-01, quantity-01, date-01, current-02, ambiguous-01,
       ambiguous-02, insufficient-01, contradiction-01, stale-01.

  F-2. All-blocks-omitted produces a misleading terminal error.
       current-02: every block was dropped for unsupported quantities, yet the run
       ended with "The provider returned inconsistent answer blocks." That message is
       wrong — the blocks were consistent, they were all unsupported. Comes from the
       final `not blocks` branch at ai_service.py:646-651. A truthful "no supported
       sections remained" is needed.

  F-3. check_numbers over-fires on real answers.
       current-02 omitted 3 blocks; deterministic-01 omitted 3. On an EMI question the
       legitimate computed monthly payment failed the digit check. This is the exact
       false-omission risk U2 was written about. Deterministic-01 completed with 2
       blocks and no summary at all.

  F-4. An uncited comparison-table cell destroys the entire answer.
       compare-02 ended with "AI returned an unsupported comparison cell. The answer
       was stopped." after 4 blocks had already been streamed to the user. One bad
       cell out of a whole table should not discard the answer.

  F-5. Saved-answer replay served stale results during benchmarking.
       An earlier aborted benchmark run populated `AIService.saved`; the next run
       replayed 8/16 answers with `model_calls == 0` and ~0s latency. The cache is
       keyed on sha256(question + depth) with a 1-hour TTL and no version marker, so
       any code change is invisible to it. The harness now forces fresh runs, and the
       cache is versioned in D1.

limitations:
  - n = 16 is a small benchmark. These are descriptive counts, not statistically
    significant results.
  - All `human_review` fields are null. Citation-support quality (does the cited
    PASSAGE contain the fact?), whether the answer addresses the question, and
    uncited factual claims were NOT assessed automatically and remain unmeasured.
  - Latency figures are single-run, local, loopback-to-loopback SearXNG. No p95.
  - Cost uses DeepSeek flash list pricing assumed by ai_budget; `cost_is_ceiling`
    may apply for unpriced models.