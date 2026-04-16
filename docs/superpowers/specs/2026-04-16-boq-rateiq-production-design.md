# BOQ RateIQ — Production Accuracy & Cost Optimization Design

**Date:** 2026-04-16
**Author:** AliTheAnalyst01
**Approach:** C — Eval Harness + Cost Fixes Together

---

## 1. Goals

| Priority | Goal | Success Criterion |
|---|---|---|
| Primary | Verify agent returns accurate per-unit rates | ≥75% of holdout items within ±15% of ground truth |
| Primary | Audit decision path correctness | CRAG + gap verdicts match expected logic |
| Secondary | Reduce Anthropic LLM cost | ~50% reduction via prompt caching |
| Secondary | Reduce Tavily search cost | ~60% reduction via Redis market cache |

---

## 2. Core Data Contract

> **Rate Invariant:** All rates in `boq_chunks.csv` and all agent outputs represent the **per-unit price for a single unit** of the item. The `qty` field describes project scope only — it is never a multiplier for the rate.

**Example:**
```
Description: OPC Cement Bag | Qty: 10 | Unit: bag | Rate: 4500 | Amount: 45000
```
Ground truth = **Rs. 4,500 per bag**. Not 45,000. Not 45,000 ÷ 10 (redundant).

**Implications:**
- Eval harness compares `predicted_rate` against the `rate` column directly.
- Rate is never derived from `amount / qty`.
- Agent `output_node` must always state the unit explicitly in its explanation: e.g., *"Rs. 4,500 per bag"*.
- The `qty` column may be passed to the agent as context (helps identify item type) but never influences rate derivation.

---

## 3. Evaluation Harness

### 3.1 Holdout Split

**Script:** `scripts/build_holdout.py`

- Reads `data/processed/boq_chunks.csv` (1,429 chunks)
- Performs stratified 80/20 split by `work_category` (ensures all 8 categories represented)
- Writes 286-item test set to `tests/eval/holdout_set.csv`
- Removes holdout items from live Qdrant collection and PostgreSQL table
- This script runs **once only** and must not be re-run (it would corrupt the holdout)

**Holdout CSV columns preserved:**
`chunk_id, description_short, unit_norm, work_category, rate` (ground truth), `source_file`

### 3.2 Accuracy Metrics

**Rate Accuracy:**
```
gap_pct = abs(predicted_rate - actual_rate) / actual_rate × 100
PASS if gap_pct ≤ 15%
```

**Decision Path Audit:**

| Metric | Pass Condition |
|---|---|
| CRAG verdict | `correct` when top reranker score ≥ 3.0; `incorrect` when < 0.5 |
| Gap verdict | `CONFIRMED` when gap < 15%; `MAJOR_GAP` when gap ≥ 35% |
| Source used | `historical` when CONFIRMED; `market` when MAJOR_GAP |
| Explanation | Contains unit label (e.g., "per bag", "per sft") |

**Report Output:**
- Console: per-category accuracy table
- File: `tests/eval/results/YYYY-MM-DD.csv` (one row per holdout item)
- Pytest exit code: FAIL if overall accuracy < 75%

### 3.3 Eval Run Command

```bash
pytest tests/eval/test_accuracy.py -v --tb=short
```

Requires: live Qdrant + PostgreSQL. Runtime: ~10-15 min (286 items, 2 concurrent).

---

## 4. Unit Test Suite

**Layer:** No API calls, no DB. Runtime < 10 seconds.

```bash
pytest tests/unit/
```

### Files

| File | What It Tests |
|---|---|
| `tests/unit/test_gap_detector.py` | `compute_gap()`, `suggest_rate()`, `_weighted_avg()` — 15 cases including zero gap, MINOR/MAJOR boundaries, MAST 10x ratio gate |
| `tests/unit/test_validators.py` | All 20+ category/unit bounds, out-of-range rejection, unknown category passthrough |
| `tests/unit/test_parser.py` | Header detection on 5 malformed Excel layouts, unit normalization (sft/sqft/sq.ft → sft), rate cleaning (Rs.1,200 → 1200.0) |
| `tests/unit/test_query_transformer.py` | Query rewriting, keyword normalization, 3-word key extraction |

---

## 5. Integration Smoke Tests

**Layer:** Requires all services (Qdrant, Postgres, Redis, FastAPI). Runtime < 2 min.

```bash
pytest tests/integration/test_api.py
```

| Test | Expected |
|---|---|
| `GET /health` | 200 |
| `POST /rate-item` with known description | Rate within ±15% of known value |
| `POST /fill-boq` with 5-row test Excel | Returns filled Excel file |
| `POST /fill-boq` with 60-row Excel | Returns 422 |
| `POST /fill-boq` with 11MB file | Returns 413 |

---

## 6. Cost Optimization

### 6.1 Anthropic Prompt Caching

**Target nodes:** `classify_node`, `output_node` in `src/rateiq/agent.py`

**Mechanism:** Add `cache_control: {"type": "ephemeral"}` to the static system prompt block on each Claude API call. Anthropic caches the prompt for 5 minutes. Within a 20-item BOQ batch (typical runtime 3-4 min), ~80% of calls hit the cache.

**Expected savings:** ~50% reduction in classify + output LLM token cost.

**No accuracy impact:** Only the static system prompt is cached. The dynamic user message (item description, unit, category) is never cached.

### 6.2 Redis Market Rate Cache

**New module:** `src/rateiq/cache.py`

**Cache key:** `rateiq:market:{normalized_3word_keywords}:{unit_norm}:{work_category}`

**TTL:** 7 days (market rates in Pakistan's construction sector change slowly)

**Flow:**
```
Before Tavily call:
  1. Check Redis key
  2. HIT → return cached MarketRate, log cache_hit=True
  3. MISS → call Tavily, write result to Redis, return MarketRate

Failure mode:
  - If Redis is unreachable → skip cache entirely, call Tavily directly
  - Cache is never blocking — all failures fall through silently
```

**Expected savings:** After warmup (2-3 batches), ~60% Tavily call reduction.

### 6.3 Combined Cost Estimate

| Expense | Before | After |
|---|---|---|
| Anthropic classify+output (20-item BOQ) | ~$0.04 | ~$0.02 |
| Tavily market search (fresh batch) | ~$0.20 | ~$0.08 |
| **Total per BOQ** | **~$0.24** | **~$0.10** |

---

## 7. Circuit Breaker Wiring

**Current state:** `src/rateiq/circuit_breaker.py` exists but is never called by `agent.py`.

**Fix:** Wrap `_market_node()` in `agent.py` with the existing `CircuitBreaker` instance.

**Behavior:**
- After 5 consecutive Tavily failures → breaker OPENS
- All subsequent market calls in that batch are skipped instantly (no timeout wait)
- Falls back to historical rate only (gap verdict becomes `NO_MARKET_DATA`)
- After 30s recovery timeout → breaker moves to HALF_OPEN, allows one test call
- 3 consecutive successes → breaker CLOSES and resumes normal operation

**No new code required** — `circuit_breaker.py` already implements all three states.

---

## 8. API Hardening

**File:** `src/rateiq/api.py`

**Changes to `/fill-boq` endpoint:**

| Guard | Limit | HTTP Response |
|---|---|---|
| File size | 10 MB | `413 Request Entity Too Large` |
| Empty-rate rows | 50 rows max | `422 Unprocessable Entity` with count in message |
| Non-Excel file | Any non-.xlsx | `400 Bad Request` (already handled by parser, just wrap cleanly) |

---

## 9. New File Structure

```
tests/
├── eval/
│   ├── holdout_set.csv              ← permanent, never regenerated
│   ├── test_accuracy.py             ← pytest eval harness
│   └── results/                     ← dated CSV accuracy reports
├── unit/
│   ├── test_gap_detector.py
│   ├── test_validators.py
│   ├── test_parser.py
│   └── test_query_transformer.py
└── integration/
    └── test_api.py

scripts/
└── build_holdout.py                 ← one-time split script

src/rateiq/
└── cache.py                         ← Redis wrapper (new module)

docs/superpowers/specs/
└── 2026-04-16-boq-rateiq-production-design.md   ← this file
```

**Existing files modified (surgical additions only):**
- `src/rateiq/agent.py` — prompt caching headers + circuit breaker wiring
- `src/rateiq/market_search.py` — Redis cache check before Tavily call
- `src/rateiq/api.py` — file size + row count guards

---

## 10. Implementation Phases

| Phase | Action | Validates |
|---|---|---|
| 1 | Run `build_holdout.py` once | Holdout set created, live index unaffected |
| 2 | Write unit tests | Pure function correctness confirmed |
| 3 | Run eval harness — establish baseline | Baseline accuracy % recorded |
| 4 | Add `cache.py` + Redis market cache | No accuracy change (same Tavily data, cached) |
| 5 | Add prompt caching to `agent.py` | No accuracy change (same prompts, cached) |
| 6 | Wire circuit breaker in `agent.py` | No accuracy change (breaker only activates on failure) |
| 7 | Add API guards in `api.py` | No accuracy change (guards reject bad input before pipeline) |
| 8 | Re-run eval harness | Confirm accuracy ≥ baseline, all metrics green |

---

## 11. Error Handling Summary

| Scenario | Handling |
|---|---|
| Agent exception on holdout item | Mark `ERROR`, continue, report separately |
| Holdout item rate = 0 or NaN | Skip — no ground truth |
| Redis unreachable | Fall through to Tavily silently |
| Tavily circuit breaker OPEN | Skip market node, use historical, verdict = NO_MARKET_DATA |
| File > 10MB | 413 before parsing |
| BOQ > 50 empty rows | 422 with row count |
| Predicted rate contains no unit label | Explanation flagged as incomplete in eval report |

---

## 12. Definition of Done

- [ ] `pytest tests/unit/` passes (all 4 test files, 0 failures)
- [ ] `python scripts/build_holdout.py` runs successfully (286-item holdout created)
- [ ] `pytest tests/eval/test_accuracy.py` baseline run completes, accuracy ≥ 75%
- [ ] Redis cache integrated, confirmed via cache hit log on second run of same batch
- [ ] Prompt caching verified via Anthropic usage dashboard (cache_read_input_tokens > 0)
- [ ] Circuit breaker wired and tested by simulating Tavily failure
- [ ] API guards tested via `pytest tests/integration/test_api.py`
- [ ] Final eval re-run shows accuracy ≥ baseline (no regression)
- [ ] All results saved in `tests/eval/results/`
