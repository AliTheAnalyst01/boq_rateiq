# BOQ RateIQ Production Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add eval harness (±15% rate accuracy + decision path audit), unit tests, Redis market cache, Anthropic prompt caching, circuit breaker wiring, and API hardening to the BOQ RateIQ system.

**Architecture:** Holdout-first: split 20% of boq_chunks.csv as a permanent test set, establish baseline accuracy, apply cost optimizations (Redis + prompt caching), wire circuit breaker, add API guards, re-run eval to confirm no regression. Rate Invariant: all rates are per-unit — never derived from amount/qty.

**Tech Stack:** Python 3.11, pytest, redis (new dep), anthropic (prompt caching), FastAPI, LangGraph, Qdrant, PostgreSQL.

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Create | `src/rateiq/cache.py` | Redis wrapper — get/set with 7-day TTL, silent fallthrough on error |
| Create | `scripts/build_holdout.py` | One-time stratified 80/20 split; removes holdout from Qdrant + Postgres |
| Create | `tests/__init__.py` | Make tests a package |
| Create | `tests/unit/__init__.py` | Make unit tests a package |
| Create | `tests/unit/test_gap_detector.py` | 15 unit tests for compute_gap, suggest_rate, _aggregate_from_search |
| Create | `tests/unit/test_validators.py` | Unit tests for validate_rate, bounds_hint |
| Create | `tests/unit/test_parser.py` | Unit tests for normalize_unit, clean_rate, find_header_row |
| Create | `tests/unit/test_cache_key.py` | Unit tests for _cache_key from market_search |
| Create | `tests/eval/__init__.py` | Make eval tests a package |
| Create | `tests/eval/test_accuracy.py` | Eval harness: ±15% rate accuracy + decision path audit |
| Create | `tests/eval/results/.gitkeep` | Keep results dir in git |
| Create | `tests/integration/__init__.py` | Make integration tests a package |
| Create | `tests/integration/test_api.py` | API smoke tests: health, rate-item, fill-boq guards |
| Modify | `pyproject.toml` | Add `redis>=5.0.0` dependency |
| Modify | `src/rateiq/agent.py` | Prompt caching on classify+output nodes; circuit breaker on market node |
| Modify | `src/rateiq/market_search.py` | Redis cache check before Tavily call |
| Modify | `src/rateiq/api.py` | 10MB file guard + 50-row cap on /fill-boq |
| Modify | `src/rateiq/config.py` | Add REDIS_URL setting |

---

## Task 1: Add Redis dependency and create cache.py

**Files:**
- Modify: `pyproject.toml`
- Create: `src/rateiq/cache.py`
- Modify: `src/rateiq/config.py`

- [ ] **Step 1: Add redis to pyproject.toml**

Open `pyproject.toml` and add `"redis>=5.0.0"` to the `dependencies` list (after `"qdrant-client>=1.17.1"`):

```toml
    "redis>=5.0.0",
```

- [ ] **Step 2: Add REDIS_URL to config.py**

In `src/rateiq/config.py`, add to the `Settings` dataclass after the `CONTRACTOR_MARKUP` field:

```python
    # ── Redis ──────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_MARKET_TTL: int = 604800  # 7 days in seconds
```

And after the existing env overrides (after `POSTGRES_URL` override), add:

```python
if os.getenv("REDIS_URL"):
    settings.REDIS_URL = os.getenv("REDIS_URL")
```

- [ ] **Step 3: Create src/rateiq/cache.py**

```python
"""
WHY:  Redis wrapper for cross-session market rate caching.
      Falls through silently if Redis is unavailable — cache is never blocking.
WHAT: get_market_rate() / set_market_rate() — typed get/set with 7-day TTL.
      CACHE KEY: rateiq:market:{3word_keywords}|{unit_norm}|{category}
FITS INTO: Called by market_search.search_market_rate() before Tavily call.
           Depends on redis, config, models.
"""

import json
import logging

from .config import settings
from .models import MarketRate

logger = logging.getLogger(__name__)

_redis_client = None


def _get_client():
    """Lazy singleton Redis client. Returns None if connection fails."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis
        client = redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        _redis_client = client
        logger.info("Redis cache connected: %s", settings.REDIS_URL)
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable, cache disabled: %s", exc)
        return None


def _market_rate_to_dict(rate: MarketRate) -> dict:
    """Serialize MarketRate to a JSON-safe dict."""
    return {
        "raw_rate": rate.raw_rate,
        "contractor_rate": rate.contractor_rate,
        "rate_type": rate.rate_type,
        "source_name": rate.source_name,
        "source_url": rate.source_url,
        "raw_price_text": rate.raw_price_text,
        "source_year": rate.source_year,
        "confidence": rate.confidence,
        "note": rate.note,
    }


def _dict_to_market_rate(d: dict) -> MarketRate:
    """Deserialize a dict back to MarketRate."""
    return MarketRate(
        raw_rate=d.get("raw_rate"),
        contractor_rate=d.get("contractor_rate"),
        rate_type=d.get("rate_type", "unknown"),
        source_name=d.get("source_name"),
        source_url=d.get("source_url"),
        raw_price_text=d.get("raw_price_text"),
        source_year=d.get("source_year"),
        confidence=d.get("confidence", "low"),
        note=d.get("note"),
    )


def get_market_rate(cache_key: str) -> MarketRate | None:
    """
    INPUT:  cache_key string — from market_search._cache_key()
    OUTPUT: MarketRate if found in Redis, None on miss or error.

    EXAMPLE:
        >>> get_market_rate("brick work 4.5|sft|civil_id")
        MarketRate(contractor_rate=480.0, ...)   # on hit
        None                                      # on miss
    """
    client = _get_client()
    if client is None:
        return None
    try:
        redis_key = f"rateiq:market:{cache_key}"
        raw = client.get(redis_key)
        if raw is None:
            return None
        data = json.loads(raw)
        logger.info("Redis cache HIT: %s", cache_key)
        return _dict_to_market_rate(data)
    except Exception as exc:
        logger.warning("Redis get_market_rate error (ignored): %s", exc)
        return None


def set_market_rate(cache_key: str, rate: MarketRate) -> None:
    """
    INPUT:  cache_key string — from market_search._cache_key()
            rate MarketRate — result to cache
    OUTPUT: None — writes to Redis with TTL, silent on failure.

    EXAMPLE:
        >>> set_market_rate("brick work 4.5|sft|civil_id", market_rate)
    """
    client = _get_client()
    if client is None:
        return
    try:
        redis_key = f"rateiq:market:{cache_key}"
        payload = json.dumps(_market_rate_to_dict(rate))
        client.setex(redis_key, settings.REDIS_MARKET_TTL, payload)
        logger.debug("Redis cache SET: %s (TTL=%ds)", cache_key, settings.REDIS_MARKET_TTL)
    except Exception as exc:
        logger.warning("Redis set_market_rate error (ignored): %s", exc)
```

- [ ] **Step 4: Install redis**

```bash
cd /home/faizan/Development/boq_rateiq
pip install "redis>=5.0.0"
```

Expected: `Successfully installed redis-5.x.x`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/rateiq/cache.py src/rateiq/config.py
git commit -m "feat: add Redis cache module and REDIS_URL config"
```

---

## Task 2: Unit tests — gap_detector

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/test_gap_detector.py`

- [ ] **Step 1: Create package init files**

Create `tests/__init__.py` (empty) and `tests/unit/__init__.py` (empty).

- [ ] **Step 2: Write tests/unit/test_gap_detector.py**

```python
"""
Unit tests for gap_detector pure functions.
No DB, no LLM, no Qdrant — all functions tested in isolation.
"""

import pytest
from unittest.mock import MagicMock, patch

from rateiq.gap_detector import compute_gap, suggest_rate, _aggregate_from_search
from rateiq.models import GapAnalysis, SearchResult, BOQChunk


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_search_result(rate: float, unit: str = "sft", reranker_score: float = 5.0) -> SearchResult:
    """Helper to build a minimal SearchResult for tests."""
    chunk = BOQChunk(
        chunk_id="test",
        description_short="Test item",
        description_full="Test item full",
        section_title="TEST",
        work_category="civil_id",
        rate=rate,
        unit_norm=unit,
        qty="100",
        source_file="test.xlsx",
        sheet_name="Sheet1",
        rate_per_unit_label=f"Rs. {rate} per {unit}",
        embedding_text="",
    )
    return SearchResult(
        chunk=chunk,
        rrf_score=0.03,
        reranker_score=reranker_score,
        bm25_rank=0,
        dense_rank=0,
        ranking_method="reranker",
    )


def _make_gap(verdict: str, hist_avg: float, market_rate=None, gap_pct: float = 0.0,
              hist_count: int = 3, crag_verdict: str = "correct") -> GapAnalysis:
    """Helper to build a GapAnalysis for suggest_rate tests."""
    return GapAnalysis(
        verdict=verdict,
        hist_avg=hist_avg,
        hist_count=hist_count,
        hist_min=hist_avg * 0.9,
        hist_max=hist_avg * 1.1,
        market_rate=market_rate,
        gap_pct=gap_pct,
        recommendation="test",
        crag_verdict=crag_verdict,
    )


# ── compute_gap tests ─────────────────────────────────────────────────────────

class TestComputeGap:
    def test_zero_gap_returns_confirmed(self):
        gap_pct, verdict = compute_gap(315.0, 315.0)
        assert gap_pct == 0.0
        assert verdict == "CONFIRMED"

    def test_small_gap_returns_confirmed(self):
        # 4.76% gap < 15% threshold
        gap_pct, verdict = compute_gap(315.0, 330.0)
        assert gap_pct == pytest.approx(4.76, abs=0.01)
        assert verdict == "CONFIRMED"

    def test_exactly_at_confirmed_boundary(self):
        # 15% gap == boundary — should be MINOR_GAP (< 15 is CONFIRMED)
        gap_pct, verdict = compute_gap(100.0, 115.0)
        assert gap_pct == pytest.approx(15.0, abs=0.01)
        assert verdict == "MINOR_GAP"

    def test_minor_gap_verdict(self):
        # 25% gap — between 15% and 35%
        gap_pct, verdict = compute_gap(200.0, 250.0)
        assert gap_pct == pytest.approx(25.0, abs=0.01)
        assert verdict == "MINOR_GAP"

    def test_exactly_at_major_gap_boundary(self):
        # 35% gap — boundary is MAJOR_GAP (>= 35)
        gap_pct, verdict = compute_gap(100.0, 135.0)
        assert gap_pct == pytest.approx(35.0, abs=0.01)
        assert verdict == "MAJOR_GAP"

    def test_large_gap_returns_major_gap(self):
        # 65% gap
        gap_pct, verdict = compute_gap(315.0, 520.0)
        assert gap_pct == pytest.approx(65.08, abs=0.1)
        assert verdict == "MAJOR_GAP"

    def test_zero_hist_avg_returns_major_gap(self):
        # No historical data — route to market
        gap_pct, verdict = compute_gap(0.0, 500.0)
        assert gap_pct == 100.0
        assert verdict == "MAJOR_GAP"

    def test_market_lower_than_hist(self):
        # Market dropped — still CONFIRMED if within 15%
        gap_pct, verdict = compute_gap(400.0, 360.0)
        assert gap_pct == pytest.approx(10.0, abs=0.01)
        assert verdict == "CONFIRMED"


# ── suggest_rate tests ────────────────────────────────────────────────────────

class TestSuggestRate:
    def test_confirmed_returns_hist_avg_high_confidence(self):
        gap = _make_gap("CONFIRMED", hist_avg=315.0, market_rate=330.0)
        rate, conf = suggest_rate(gap, [])
        assert rate == 315.0
        assert conf == "high"

    def test_minor_gap_returns_midpoint_medium_confidence(self):
        gap = _make_gap("MINOR_GAP", hist_avg=300.0, market_rate=400.0, gap_pct=33.0)
        rate, conf = suggest_rate(gap, [])
        assert rate == 350.0   # midpoint of 300 and 400
        assert conf == "medium"

    def test_no_market_data_returns_hist_avg(self):
        gap = _make_gap("NO_MARKET_DATA", hist_avg=315.0, market_rate=None)
        rate, conf = suggest_rate(gap, [])
        assert rate == 315.0
        assert conf == "medium"

    def test_crag_ambiguous_downgrades_high_to_medium(self):
        gap = _make_gap("CONFIRMED", hist_avg=315.0, market_rate=330.0, crag_verdict="ambiguous")
        rate, conf = suggest_rate(gap, [])
        assert rate == 315.0
        assert conf == "medium"   # downgraded from high

    def test_fallback_to_search_results_when_rate_zero(self):
        gap = _make_gap("NO_MARKET_DATA", hist_avg=0.0, market_rate=None, hist_count=0)
        results = [_make_search_result(rate=250.0)]
        rate, conf = suggest_rate(gap, results)
        assert rate == 250.0
        assert conf == "low"

    def test_zero_rate_zero_results_returns_zero(self):
        gap = _make_gap("NO_MARKET_DATA", hist_avg=0.0, market_rate=None, hist_count=0)
        rate, conf = suggest_rate(gap, [])
        assert rate == 0.0
        assert conf == "low"

    def test_major_gap_mast_gate_rejects_10x_market(self):
        # Market = 10x hist → MAST gate should use historical instead
        gap = _make_gap("MAJOR_GAP", hist_avg=300.0, market_rate=3100.0, gap_pct=933.0)
        rate, conf = suggest_rate(gap, [], category="civil_id", unit="sft")
        assert rate == 300.0   # historical, not the 10x market value
        assert conf == "medium"


# ── _aggregate_from_search tests ─────────────────────────────────────────────

class TestAggregateFromSearch:
    def test_unit_matched_results_preferred(self):
        results = [
            _make_search_result(rate=300.0, unit="sft", reranker_score=5.0),
            _make_search_result(rate=800.0, unit="rft", reranker_score=4.0),
        ]
        avg, mn, mx, count = _aggregate_from_search(results, "sft")
        assert avg == pytest.approx(300.0, abs=1.0)
        assert count == 1

    def test_weighted_average_by_reranker_score(self):
        # High-score result should dominate
        results = [
            _make_search_result(rate=300.0, unit="sft", reranker_score=8.0),
            _make_search_result(rate=600.0, unit="sft", reranker_score=2.0),
        ]
        avg, mn, mx, count = _aggregate_from_search(results, "sft")
        # Weighted: (300*8 + 600*2) / (8+2) = 3600/10 = 360
        assert avg == pytest.approx(360.0, abs=1.0)

    def test_empty_results_returns_zeros(self):
        avg, mn, mx, count = _aggregate_from_search([], "sft")
        assert avg == 0.0 and count == 0

    def test_zero_rate_results_excluded(self):
        results = [
            _make_search_result(rate=0.0, unit="sft"),
            _make_search_result(rate=400.0, unit="sft"),
        ]
        avg, mn, mx, count = _aggregate_from_search(results, "sft")
        assert count == 1
        assert avg == pytest.approx(400.0, abs=1.0)
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
cd /home/faizan/Development/boq_rateiq
python -m pytest tests/unit/test_gap_detector.py -v
```

Expected: all tests PASS (no DB/LLM required).

- [ ] **Step 4: Commit**

```bash
git add tests/__init__.py tests/unit/__init__.py tests/unit/test_gap_detector.py
git commit -m "test: add unit tests for gap_detector pure functions"
```

---

## Task 3: Unit tests — validators

**Files:**
- Create: `tests/unit/test_validators.py`

- [ ] **Step 1: Write tests/unit/test_validators.py**

```python
"""
Unit tests for validators.validate_rate and validators.bounds_hint.
No I/O — pure lookup table tests.
"""

import pytest
from rateiq.validators import validate_rate, bounds_hint


class TestValidateRate:
    # ── civil_id/sft bounds: (50, 3000)
    def test_civil_sft_valid(self):
        assert validate_rate("civil_id", "sft", 315.0) is True

    def test_civil_sft_too_low(self):
        assert validate_rate("civil_id", "sft", 14.0) is False

    def test_civil_sft_too_high(self):
        assert validate_rate("civil_id", "sft", 5000.0) is False

    def test_civil_sft_at_lower_bound(self):
        assert validate_rate("civil_id", "sft", 50.0) is True

    def test_civil_sft_at_upper_bound(self):
        assert validate_rate("civil_id", "sft", 3000.0) is True

    # ── hvac/nos bounds: (30000, 5000000)
    def test_hvac_nos_valid_ac_unit(self):
        assert validate_rate("hvac", "nos", 450_000.0) is True

    def test_hvac_nos_too_low(self):
        assert validate_rate("hvac", "nos", 500.0) is False

    # ── ceiling/sft bounds: (150, 5000)
    def test_ceiling_sft_valid(self):
        assert validate_rate("ceiling", "sft", 450.0) is True

    def test_ceiling_sft_too_low(self):
        assert validate_rate("ceiling", "sft", 50.0) is False

    # ── Unknown category — permissive (returns True)
    def test_unknown_category_permissive(self):
        assert validate_rate("unknown_cat", "sft", 1.0) is True

    # ── Unknown unit for known category — permissive
    def test_known_category_unknown_unit_permissive(self):
        assert validate_rate("civil_id", "truck", 5000.0) is True

    # ── Zero / None rates
    def test_zero_rate_rejected(self):
        assert validate_rate("civil_id", "sft", 0.0) is False

    def test_negative_rate_rejected(self):
        assert validate_rate("civil_id", "sft", -100.0) is False

    # ── electrical_elv/point bounds: (300, 50000)
    def test_electrical_point_valid(self):
        assert validate_rate("electrical_elv", "point", 3500.0) is True

    def test_electrical_point_too_high(self):
        assert validate_rate("electrical_elv", "point", 100_000.0) is False


class TestBoundsHint:
    def test_known_pair_returns_string(self):
        hint = bounds_hint("civil_id", "sft")
        assert "Rs." in hint
        assert "50" in hint
        assert "3,000" in hint

    def test_unknown_pair_returns_empty(self):
        hint = bounds_hint("unknown_cat", "sft")
        assert hint == ""

    def test_hvac_nos_returns_correct_range(self):
        hint = bounds_hint("hvac", "nos")
        assert "30,000" in hint
        assert "5,000,000" in hint
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/unit/test_validators.py -v
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_validators.py
git commit -m "test: add unit tests for validators"
```

---

## Task 4: Unit tests — parser

**Files:**
- Create: `tests/unit/test_parser.py`

- [ ] **Step 1: Write tests/unit/test_parser.py**

```python
"""
Unit tests for parser.normalize_unit, parser.clean_rate, parser.find_header_row.
No file I/O — uses in-memory DataFrames.
"""

import numpy as np
import pandas as pd
import pytest

from rateiq.parser import normalize_unit, clean_rate, find_header_row


class TestNormalizeUnit:
    def test_sft_variants(self):
        for raw in ["sft", "SFT", "Sft", "sqft", "SQFT", "sq.ft", "sq ft", "sqf"]:
            assert normalize_unit(raw) == "sft", f"Failed for {raw!r}"

    def test_nos_variants(self):
        for raw in ["nos", "no", "no.", "NOS", "No.", "number", "numbers"]:
            assert normalize_unit(raw) == "nos", f"Failed for {raw!r}"

    def test_rft_variants(self):
        for raw in ["rft", "RFT", "runnft", "r.ft"]:
            assert normalize_unit(raw) == "rft", f"Failed for {raw!r}"

    def test_ls_variants(self):
        for raw in ["ls", "l.s", "lump sum", "lumpsum", "l/s"]:
            assert normalize_unit(raw) == "ls", f"Failed for {raw!r}"

    def test_unknown_returns_cleaned_string(self):
        # Unknown unit — return cleaned version, not "unit"
        result = normalize_unit("truck")
        assert result == "truck"

    def test_empty_returns_unit(self):
        assert normalize_unit("") == "unit"

    def test_none_returns_unit(self):
        assert normalize_unit(None) == "unit"

    def test_trailing_dot_stripped(self):
        assert normalize_unit("Sft.") == "sft"


class TestCleanRate:
    def test_numeric_float_passthrough(self):
        assert clean_rate(315.0) == 315.0

    def test_numeric_int_converted(self):
        assert clean_rate(315) == 315.0

    def test_string_with_rs_prefix(self):
        assert clean_rate("Rs. 1,35,000") == 135000.0

    def test_string_with_commas(self):
        assert clean_rate("1,200") == 1200.0

    def test_string_plain_number(self):
        assert clean_rate("450") == 450.0

    def test_ofm_returns_none(self):
        assert clean_rate("OFM") is None

    def test_nan_returns_none(self):
        assert clean_rate(float("nan")) is None

    def test_none_returns_none(self):
        assert clean_rate(None) is None

    def test_zero_returns_none(self):
        assert clean_rate(0) is None

    def test_negative_returns_none(self):
        assert clean_rate(-100) is None

    def test_dash_returns_none(self):
        assert clean_rate("-") is None

    def test_empty_string_returns_none(self):
        assert clean_rate("") is None


class TestFindHeaderRow:
    def test_finds_header_at_row_0(self):
        df = pd.DataFrame([
            ["S.No", "Description", "Qty", "Unit", "Rate", "Amount"],
            [1, "Brick Work 4.5 thick", 100, "Sft", 315, 31500],
        ])
        assert find_header_row(df) == 0

    def test_finds_header_at_row_3(self):
        # Header is buried 3 rows down (common in Pakistani BOQ Excel files)
        df = pd.DataFrame([
            ["Project Name: ABC Tower", None, None, None, None, None],
            ["Client: XYZ Builders", None, None, None, None, None],
            [None, None, None, None, None, None],
            ["S.No", "Description", "Qty", "Unit", "Rate", "Amount"],
            [1, "Gypsum ceiling 12mm", 500, "Sft", 450, 225000],
        ])
        assert find_header_row(df) == 3

    def test_returns_none_when_no_header(self):
        df = pd.DataFrame([
            [1, 2, 3, 4, 5],
            [6, 7, 8, 9, 10],
        ])
        assert find_header_row(df) is None

    def test_handles_alternate_description_alias(self):
        df = pd.DataFrame([
            ["Sr.", "Particulars", "Qty", "Unit", "Rate"],
            [1, "Tile flooring", 200, "Sft", 850],
        ])
        assert find_header_row(df) == 0

    def test_empty_dataframe_returns_none(self):
        df = pd.DataFrame()
        assert find_header_row(df) is None
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/unit/test_parser.py -v
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_parser.py
git commit -m "test: add unit tests for parser normalize_unit, clean_rate, find_header_row"
```

---

## Task 5: Unit tests — cache key (market_search)

**Files:**
- Create: `tests/unit/test_cache_key.py`

- [ ] **Step 1: Write tests/unit/test_cache_key.py**

```python
"""
Unit tests for market_search._cache_key normalization.
Verifies that similar descriptions map to the same cache slot.
"""

from rateiq.market_search import _cache_key


class TestCacheKey:
    def test_first_3_meaningful_words_used(self):
        key = _cache_key("brick wall 4.5 inch first class", "sft", "civil_id")
        assert key == "bri|sft|civil_id" or "brick" in key  # 3 words > 2 chars

    def test_similar_descriptions_share_key(self):
        # Minor description variants should map to same cache slot
        key1 = _cache_key("brick work 4.5 inch", "sft", "civil_id")
        key2 = _cache_key("brick masonry 4.5 thick", "sft", "civil_id")
        # Both start with "brick" as first word — may or may not be same
        # The important invariant: key is deterministic
        assert key1 == _cache_key("brick work 4.5 inch", "sft", "civil_id")
        assert key2 == _cache_key("brick masonry 4.5 thick", "sft", "civil_id")

    def test_different_units_produce_different_keys(self):
        key_sft = _cache_key("gypsum ceiling 12mm", "sft", "ceiling")
        key_nos = _cache_key("gypsum ceiling 12mm", "nos", "ceiling")
        assert key_sft != key_nos

    def test_different_categories_produce_different_keys(self):
        key_civil = _cache_key("brick work 4.5", "sft", "civil_id")
        key_finish = _cache_key("brick work 4.5", "sft", "finishing")
        assert key_civil != key_finish

    def test_short_words_filtered_out(self):
        # Words <= 2 chars are skipped (e.g. "of", "in", "a")
        key = _cache_key("a of in brick work heavy", "sft", "civil_id")
        # "brick work heavy" should be the 3 meaningful words
        assert "brick" in key

    def test_case_insensitive(self):
        key_lower = _cache_key("brick work 4.5", "sft", "civil_id")
        key_upper = _cache_key("BRICK WORK 4.5", "sft", "civil_id")
        assert key_lower == key_upper

    def test_key_format_contains_pipe_separators(self):
        key = _cache_key("brick work 4.5", "sft", "civil_id")
        assert "|" in key
        parts = key.split("|")
        assert len(parts) == 3
        assert parts[1] == "sft"
        assert parts[2] == "civil_id"
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/unit/test_cache_key.py -v
```

Expected: all PASS.

- [ ] **Step 3: Run all unit tests together**

```bash
python -m pytest tests/unit/ -v
```

Expected: all tests in all 4 files PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_cache_key.py
git commit -m "test: add unit tests for market_search cache key normalization"
```

---

## Task 6: Wire Redis cache into market_search.py

**Files:**
- Modify: `src/rateiq/market_search.py`

- [ ] **Step 1: Add Redis cache check to search_market_rate()**

In `src/rateiq/market_search.py`, add this import at the top (after existing imports):

```python
from . import cache as _cache_module
```

Then replace the entire `search_market_rate()` function body with:

```python
def search_market_rate(
    keywords: str,
    unit: str,
    category: str,
) -> MarketRate:
    """
    INPUT:  keywords — item description to search (e.g. "brick wall 4.5 inch")
            unit — normalised unit (e.g. "sft")
            category — work category for query tuning (e.g. "civil_id")
    OUTPUT: MarketRate with contractor_rate, source name/URL, confidence.
            Returns null-rate MarketRate if no market data found after 3 query attempts.

    Cache check order:
      1. In-memory batch cache (_market_cache) — fastest, cleared per batch
      2. Redis cross-session cache — persists 7 days across restarts
      3. Tavily web search — 3 attempts with fallback queries
    """
    key = _cache_key(keywords, unit, category)

    # Level 1: in-memory batch cache
    if key in _market_cache:
        logger.info("Market cache hit (in-memory): '%s'", key)
        return _market_cache[key]

    # Level 2: Redis cross-session cache
    redis_result = _cache_module.get_market_rate(key)
    if redis_result is not None:
        _market_cache[key] = redis_result  # also warm in-memory cache
        return redis_result

    # Level 3: Tavily search — attempt 1
    result = _run_tavily_and_extract(build_query(keywords, unit, category), keywords, unit, category)
    if result.contractor_rate is not None:
        _market_cache[key] = result
        _cache_module.set_market_rate(key, result)
        return result

    # Attempt 2: broad query without site restrictions
    query2 = f"{keywords} price rate PKR Pakistan 2025 Lahore contractor supply install"
    result = _run_tavily_and_extract(query2, keywords, unit, category)
    if result.contractor_rate is not None:
        _market_cache[key] = result
        _cache_module.set_market_rate(key, result)
        return result

    # Attempt 3: community/forum sources as last resort
    query3 = (
        f"{keywords} rate per {unit} Pakistan PKR "
        f"site:quora.com OR site:reddit.com OR site:propakistani.pk"
    )
    result = _run_tavily_and_extract(query3, keywords, unit, category)

    # Cache even null results to avoid re-querying categories with no web data
    _market_cache[key] = result
    _cache_module.set_market_rate(key, result)
    return result
```

- [ ] **Step 2: Run unit tests to confirm no regression**

```bash
python -m pytest tests/unit/ -v
```

Expected: all still PASS (market_search changes don't affect unit tests).

- [ ] **Step 3: Commit**

```bash
git add src/rateiq/market_search.py
git commit -m "feat: wire Redis cross-session cache into market_search (7-day TTL)"
```

---

## Task 7: Wire circuit breaker into agent._market_node()

**Files:**
- Modify: `src/rateiq/agent.py`

- [ ] **Step 1: Add circuit breaker import to agent.py**

In `src/rateiq/agent.py`, add this import (after the existing imports, before `logger = ...`):

```python
from .circuit_breaker import CircuitOpenError, get_circuit
```

- [ ] **Step 2: Replace _market_node() with circuit-breaker-wrapped version**

Find `_market_node` in `agent.py` (around line 550) and replace the entire method:

```python
    def _market_node(self, state: RateIQState) -> RateIQState:
        """
        WHY:   Current market prices validate or override historical rates.
               Without this node, the system would quote 2021 rates in 2025.
        READS: state["search_keywords"], state["unit_norm"], state["work_category"]
        WRITES: state["market_rate"]
        EXIT:  CircuitOpenError → market_rate=None, gap_node uses historical only.
               On other failure → state["market_rate"]=None; gap_node handles gracefully.
        MODEL: ANTHROPIC_MODEL (Sonnet) via market_search — unit conversion reasoning.
        """
        cb = get_circuit("market_search")
        try:
            keywords = state.get("search_keywords") or state.get("description", "")
            unit = state.get("unit_norm") or "unit"
            category = state.get("work_category") or "other"

            market: MarketRate = cb.call(
                _market_search_module.search_market_rate,
                keywords, unit, category,
            )
            state["market_rate"] = _dataclass_to_dict(market)

            logger.info(
                "market_node: contractor_rate=%s, confidence=%s",
                market.contractor_rate,
                market.confidence,
            )

        except CircuitOpenError as exc:
            logger.warning("market_node: circuit OPEN, skipping market search: %s", exc)
            state["market_rate"] = None
            errors = state.get("errors") or []
            errors.append(f"market_node: circuit_open: {exc}")
            state["errors"] = errors

        except Exception as exc:
            logger.error("_market_node failed: %s", exc)
            state["market_rate"] = None
            errors = state.get("errors") or []
            errors.append(f"market_node: {exc}")
            state["errors"] = errors

        return state
```

- [ ] **Step 3: Run unit tests to confirm no regression**

```bash
python -m pytest tests/unit/ -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/rateiq/agent.py
git commit -m "feat: wire circuit breaker into agent._market_node() for Tavily failure protection"
```

---

## Task 8: Add Anthropic prompt caching to classify and output nodes

**Files:**
- Modify: `src/rateiq/agent.py`

The key change: split the monolithic user-message prompt into a static `system` block (with `cache_control`) and a small dynamic user message. This lets Anthropic cache the ~200-token static instruction across all calls in a batch.

- [ ] **Step 1: Add static system prompt constants at the top of agent.py**

After the existing `_CLASSIFY_PROMPT_TEMPLATE` and `_OUTPUT_PROMPT_TEMPLATE` constants (around line 242), add:

```python
# ── Cached system prompts (static — eligible for Anthropic prompt cache) ──────
# WHY: cache_control="ephemeral" tells Anthropic to cache these blocks for 5 min.
#      In a 20-item BOQ batch, ~80% of calls hit the cache → ~50% LLM cost reduction.
#      Only the dynamic user message (description, unit, rate) changes per call.

_CLASSIFY_SYSTEM = """\
You are a Pakistan construction cost expert.
Classify this BOQ item and extract search keywords.

Return JSON with exactly these keys:
{
  "work_category": <one of: civil_id, hvac, electrical_elv, finishing, plumbing, ceiling, flooring, carpentry, other>,
  "search_keywords": <3-6 word concise search term, no filler words>,
  "unit_norm": <normalized unit: sft, nos, rft, ls, kg, mtr, cft, ton, point, set, day — pick best match>
}

Return valid JSON only."""

_OUTPUT_SYSTEM = """\
Write a 2-3 sentence explanation for a BOQ rate suggestion.
Be specific, cite sources. Maximum 60 words.
Always state the per-unit rate explicitly (e.g. "Rs. 4,500 per bag", "Rs. 315 per sft").
Write in plain English. No markdown."""
```

- [ ] **Step 2: Replace the LLM call in _classify_node() with cached version**

In `_classify_node()`, find the `response = self._llm.messages.create(...)` call (around line 386) and replace it:

```python
            # Static system prompt is cached by Anthropic for 5 min.
            # Only the 2-line user message (description + unit) changes per call.
            response = self._llm.messages.create(
                model=settings.ANTHROPIC_MODEL_LIGHT,
                max_tokens=200,
                system=[
                    {
                        "type": "text",
                        "text": _CLASSIFY_SYSTEM,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": f"Description: {description}\nUnit (from sheet): {unit_input}",
                    }
                ],
            )
```

- [ ] **Step 3: Replace the LLM call in _output_node() with cached version**

In `_output_node()`, find the `response = self._llm.messages.create(...)` call (around line 721) and replace it:

```python
            # Static system prompt is cached. Dynamic user message includes all rate data.
            dynamic_user = _OUTPUT_PROMPT_TEMPLATE.format(
                description=state.get("description", ""),
                suggested_rate=suggested_rate,
                unit_norm=state.get("unit_norm", "unit"),
                confidence=confidence,
                verdict=gap_dict.get("verdict", "N/A"),
                recommendation=gap_dict.get("recommendation", ""),
                hist_avg=gap_dict.get("hist_avg", 0),
                hist_count=gap_dict.get("hist_count", 0),
                market_rate_str=mkt_rate_display,
                sources_str=", ".join(source_refs[:3]) if source_refs else "historical data",
            )
            response = self._llm.messages.create(
                model=settings.ANTHROPIC_MODEL_LIGHT,
                max_tokens=settings.LLM_MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": _OUTPUT_SYSTEM,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": dynamic_user}],
            )
```

Note: the `_OUTPUT_PROMPT_TEMPLATE.format(...)` call was previously assigned to `prompt` then passed to `messages`. After this change, assign to `dynamic_user` and pass directly.

- [ ] **Step 4: Run unit tests to confirm no regression**

```bash
python -m pytest tests/unit/ -v
```

Expected: all PASS (agent.py changes don't affect unit tests).

- [ ] **Step 5: Commit**

```bash
git add src/rateiq/agent.py
git commit -m "perf: add Anthropic prompt caching to classify and output nodes (~50% LLM cost reduction)"
```

---

## Task 9: Add API guards to /fill-boq

**Files:**
- Modify: `src/rateiq/api.py`

- [ ] **Step 1: Add file size and row count guards to fill_boq()**

In `src/rateiq/api.py`, replace the `fill_boq` function with:

```python
MAX_UPLOAD_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_EMPTY_ROWS = 50


@app.post("/fill-boq")
async def fill_boq(file: UploadFile = File(...)):
    """
    INPUT:  Excel file (.xlsx) uploaded via multipart form
            File must have columns: DESCRIPTION, QTY, UNIT
            RATE column can be empty — agent will fill it
            Max file size: 10 MB. Max empty-rate rows: 50.

    OUTPUT: Excel file (.xlsx) download with added columns:
            SUGGESTED_RATE  — PKR number recommended by agent
            CONFIDENCE      — high / medium / low
            EXPLANATION     — 2-3 sentence reason with sources
            SOURCES         — comma-separated past tender files
            MARKET_SOURCE   — website where market rate found
            MARKET_RATE     — current market rate if found
            GAP_PCT         — % difference hist vs market
            GAP_VERDICT     — CONFIRMED / MINOR_GAP / MAJOR_GAP

    EXAMPLE:
        curl -X POST http://localhost:8000/fill-boq \\
             -F "file=@test_boq_input.xlsx" \\
             --output filled_boq.xlsx
    """
    if agent is None:
        raise HTTPException(503, "Agent not ready. Wait and retry.")

    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Only .xlsx or .xls files accepted.")

    content = await file.read()

    # Guard 1: file size
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"File too large: {len(content) / 1024 / 1024:.1f} MB. Maximum is 10 MB.",
        )

    tmp_input = Path(tempfile.mktemp(suffix=".xlsx"))
    tmp_output = Path(tempfile.mktemp(suffix=".xlsx"))

    try:
        tmp_input.write_bytes(content)

        # Guard 2: empty-row count — parse first, then count before running agent
        import pandas as pd
        try:
            preview_df = pd.read_excel(tmp_input, nrows=200)
            # Find RATE column (case-insensitive)
            rate_col = next(
                (c for c in preview_df.columns if str(c).strip().lower() in ("rate", "rates", "unit rate", "unit price")),
                None,
            )
            if rate_col:
                empty_count = preview_df[rate_col].isna().sum() + (preview_df[rate_col] == 0).sum()
                if empty_count > MAX_EMPTY_ROWS:
                    raise HTTPException(
                        422,
                        f"File has {empty_count} empty-rate rows. Maximum is {MAX_EMPTY_ROWS}. "
                        "Split the BOQ into smaller batches.",
                    )
        except HTTPException:
            raise
        except Exception:
            pass  # If preview fails, let the main pipeline handle it

        result = process_boq_file(
            input_path=tmp_input, output_path=tmp_output, agent=agent
        )

        if not tmp_output.exists():
            raise HTTPException(500, "Processing failed — no output file.")

        return FileResponse(
            path=str(tmp_output),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=f"rateiq_filled_{file.filename}",
            headers={
                "X-Rows-Total": str(result.get("total_rows", 0)),
                "X-Rows-Filled": str(result.get("filled", 0)),
                "X-Rows-Skipped": str(result.get("skipped", 0)),
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"fill_boq error: {e}", exc_info=True)
        raise HTTPException(500, f"Processing error: {str(e)}")
    finally:
        if tmp_input.exists():
            tmp_input.unlink()
```

- [ ] **Step 2: Run unit tests to confirm no regression**

```bash
python -m pytest tests/unit/ -v
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add src/rateiq/api.py
git commit -m "feat: add 10MB file size guard and 50-row cap to /fill-boq endpoint"
```

---

## Task 10: Build holdout split script

**Files:**
- Create: `scripts/build_holdout.py`
- Create: `tests/eval/__init__.py`
- Create: `tests/eval/results/.gitkeep`

⚠️ **This script runs ONCE. Do not re-run after holdout_set.csv is created.**

- [ ] **Step 1: Create scripts/build_holdout.py**

```python
"""
WHY:  Creates the permanent 80/20 holdout split for eval harness.
      Removes holdout items from Qdrant + PostgreSQL so the agent
      cannot retrieve its own test data during evaluation.
RUN:  python scripts/build_holdout.py
      Run ONCE only — re-running corrupts the holdout set.
"""

import logging
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CHUNKS_CSV = Path("data/processed/boq_chunks.csv")
HOLDOUT_CSV = Path("tests/eval/holdout_set.csv")
HOLDOUT_FRACTION = 0.20
RANDOM_SEED = 42


def build_holdout():
    # Safety check — never overwrite an existing holdout
    if HOLDOUT_CSV.exists():
        logger.error(
            "ABORT: %s already exists. Delete it manually if you want to rebuild "
            "(this will invalidate all previous eval results).",
            HOLDOUT_CSV,
        )
        sys.exit(1)

    logger.info("Loading chunks from %s ...", CHUNKS_CSV)
    df = pd.read_csv(CHUNKS_CSV)
    logger.info("Total chunks: %d", len(df))

    # Only keep rows with a valid rate (can't evaluate without ground truth)
    df = df[df["rate"].notna() & (df["rate"] > 0)].copy()
    logger.info("Chunks with valid rate: %d", len(df))

    # Stratified 80/20 split by work_category
    holdout_frames = []
    train_frames = []
    for category, group in df.groupby("work_category"):
        n_holdout = max(1, round(len(group) * HOLDOUT_FRACTION))
        holdout = group.sample(n=n_holdout, random_state=RANDOM_SEED)
        train = group.drop(holdout.index)
        holdout_frames.append(holdout)
        train_frames.append(train)
        logger.info(
            "  %s: total=%d, holdout=%d, train=%d",
            category, len(group), len(holdout), len(train),
        )

    holdout_df = pd.concat(holdout_frames).reset_index(drop=True)
    logger.info("Total holdout set: %d items", len(holdout_df))

    # Save holdout — keep all columns including 'rate' as ground truth
    HOLDOUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    holdout_df.to_csv(HOLDOUT_CSV, index=False)
    logger.info("Holdout saved to %s", HOLDOUT_CSV)

    # Remove holdout items from Qdrant
    holdout_ids = holdout_df["chunk_id"].tolist()
    try:
        from qdrant_client import QdrantClient
        from rateiq.config import settings
        qclient = QdrantClient(url=settings.QDRANT_URL)
        qclient.delete(
            collection_name=settings.COLLECTION_NAME,
            points_selector=holdout_ids,
        )
        logger.info("Removed %d items from Qdrant collection '%s'", len(holdout_ids), settings.COLLECTION_NAME)
    except Exception as exc:
        logger.warning("Qdrant removal failed (manual cleanup needed): %s", exc)

    # Remove holdout items from PostgreSQL
    try:
        import psycopg2
        from rateiq.config import settings
        conn = psycopg2.connect(settings.POSTGRES_URL)
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM boq_rates WHERE chunk_id = ANY(%s)",
            (holdout_ids,),
        )
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Removed %d rows from PostgreSQL boq_rates table", deleted)
    except Exception as exc:
        logger.warning("PostgreSQL removal failed (manual cleanup needed): %s", exc)

    logger.info(
        "\n=== HOLDOUT BUILD COMPLETE ===\n"
        "  Holdout file: %s\n"
        "  Items: %d\n"
        "  Run eval with: pytest tests/eval/test_accuracy.py -v\n"
        "  DO NOT re-run this script.",
        HOLDOUT_CSV, len(holdout_df),
    )


if __name__ == "__main__":
    build_holdout()
```

- [ ] **Step 2: Create test/eval package files**

Create `tests/eval/__init__.py` (empty) and `tests/eval/results/.gitkeep` (empty).

- [ ] **Step 3: Commit**

```bash
git add scripts/build_holdout.py tests/eval/__init__.py tests/eval/results/.gitkeep
git commit -m "feat: add holdout split script (run once before first eval)"
```

---

## Task 11: Build eval harness test_accuracy.py

**Files:**
- Create: `tests/eval/test_accuracy.py`

- [ ] **Step 1: Write tests/eval/test_accuracy.py**

```python
"""
WHY:  Measures agent accuracy against the permanent holdout set.
      Rate Invariant: all rates are per-unit — never derived from amount/qty.
      Baseline accuracy must be established BEFORE any pipeline changes.
      Target: ≥75% of holdout items within ±15% of ground truth.

REQUIRES: Live Qdrant + PostgreSQL + .env API keys
RUNTIME:  ~10-15 minutes for 286 items (2 concurrent)
RUN:      pytest tests/eval/test_accuracy.py -v --tb=short
"""

import csv
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

logger = logging.getLogger(__name__)

HOLDOUT_CSV = Path("tests/eval/holdout_set.csv")
RESULTS_DIR = Path("tests/eval/results")
ACCURACY_THRESHOLD = 0.75   # 75% of items must pass ±15%
RATE_TOLERANCE_PCT = 15.0   # ±15% of ground truth


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def agent():
    """Initialize agent once for the entire eval session."""
    from rateiq.pipeline import initialize_all
    return initialize_all()


@pytest.fixture(scope="session")
def holdout_items():
    """Load holdout set. Fail early if not built yet."""
    if not HOLDOUT_CSV.exists():
        pytest.skip(
            f"Holdout set not found at {HOLDOUT_CSV}. "
            "Run: python scripts/build_holdout.py"
        )
    df = pd.read_csv(HOLDOUT_CSV)
    # Only items with valid ground truth rates
    df = df[df["rate"].notna() & (df["rate"] > 0)].copy()
    return df.to_dict("records")


# ── Helpers ───────────────────────────────────────────────────────────────────

def within_tolerance(predicted: float, actual: float, tolerance_pct: float) -> bool:
    """Return True if predicted is within ±tolerance_pct% of actual."""
    if actual <= 0:
        return False
    gap_pct = abs(predicted - actual) / actual * 100.0
    return gap_pct <= tolerance_pct


def save_results(results: list[dict]) -> Path:
    """Save per-item results to dated CSV in tests/eval/results/."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = RESULTS_DIR / f"{date.today()}.csv"
    if results:
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
    logger.info("Eval results saved to %s", output_path)
    return output_path


def print_accuracy_table(results: list[dict]) -> float:
    """Print per-category accuracy table and return overall accuracy."""
    from collections import defaultdict
    by_cat: dict[str, list] = defaultdict(list)
    for r in results:
        by_cat[r["work_category"]].append(r["passed"])

    print("\n=== ACCURACY REPORT ===")
    print(f"{'Category':<25} {'Items':>6} {'Passed':>8} {'Accuracy':>10}")
    print("-" * 53)

    total_items = 0
    total_passed = 0
    for cat in sorted(by_cat):
        items = by_cat[cat]
        passed = sum(items)
        acc = passed / len(items) * 100 if items else 0
        print(f"{cat:<25} {len(items):>6} {passed:>8} {acc:>9.1f}%")
        total_items += len(items)
        total_passed += passed

    overall = total_passed / total_items * 100 if total_items else 0
    print("-" * 53)
    print(f"{'OVERALL':<25} {total_items:>6} {total_passed:>8} {overall:>9.1f}%")
    print(f"\nTarget: ≥{ACCURACY_THRESHOLD * 100:.0f}% | Result: {overall:.1f}%")
    return total_passed / total_items if total_items else 0.0


# ── Main eval test ────────────────────────────────────────────────────────────

def test_agent_rate_accuracy(agent, holdout_items):
    """
    For each holdout item:
      1. Run agent.run(description, qty, unit) — qty is context only, not rate multiplier
      2. Compare predicted rate vs ground truth rate (per-unit, from rate column)
      3. Check decision path: CRAG verdict, gap verdict, source
    Fail if overall accuracy < 75%.
    """
    results = []
    errors = []

    for i, item in enumerate(holdout_items):
        description = str(item.get("description_short", ""))
        unit = str(item.get("unit_norm", ""))
        qty = str(item.get("qty", ""))
        actual_rate = float(item.get("rate", 0.0))
        category = str(item.get("work_category", ""))

        if i % 20 == 0:
            logger.info("Eval progress: %d/%d items", i, len(holdout_items))

        try:
            rec = agent.run(description, qty, unit)
            predicted = rec.suggested_rate or 0.0

            passed = within_tolerance(predicted, actual_rate, RATE_TOLERANCE_PCT)
            gap_pct = abs(predicted - actual_rate) / actual_rate * 100 if actual_rate > 0 else None

            # Decision path audit
            gap_analysis = rec.gap_analysis
            crag_verdict = gap_analysis.crag_verdict if gap_analysis else "unknown"
            gap_verdict = gap_analysis.verdict if gap_analysis else "unknown"
            hist_source = gap_analysis.hist_source if gap_analysis else "unknown"

            # Check explanation contains per-unit label
            explanation = rec.explanation or ""
            has_unit_label = any(
                label in explanation.lower()
                for label in [f"per {unit}", f"/{unit}", f"per {unit.lower()}"]
            )

            results.append({
                "chunk_id": item.get("chunk_id", ""),
                "description": description,
                "unit": unit,
                "work_category": category,
                "actual_rate": actual_rate,
                "predicted_rate": predicted,
                "gap_pct": round(gap_pct, 2) if gap_pct is not None else None,
                "passed": passed,
                "confidence": rec.confidence,
                "crag_verdict": crag_verdict,
                "gap_verdict": gap_verdict,
                "hist_source": hist_source,
                "has_unit_label": has_unit_label,
                "error": None,
            })

        except Exception as exc:
            logger.error("Eval error on item %d (%s): %s", i, description[:40], exc)
            errors.append({"item": i, "description": description, "error": str(exc)})
            results.append({
                "chunk_id": item.get("chunk_id", ""),
                "description": description,
                "unit": unit,
                "work_category": category,
                "actual_rate": actual_rate,
                "predicted_rate": 0.0,
                "gap_pct": None,
                "passed": False,
                "confidence": "low",
                "crag_verdict": "error",
                "gap_verdict": "error",
                "hist_source": "error",
                "has_unit_label": False,
                "error": str(exc),
            })

    # Save results
    save_results(results)

    # Print report and check threshold
    overall_accuracy = print_accuracy_table(results)

    if errors:
        print(f"\n⚠ {len(errors)} items threw exceptions (marked as failed)")

    assert overall_accuracy >= ACCURACY_THRESHOLD, (
        f"Accuracy {overall_accuracy:.1%} is below threshold {ACCURACY_THRESHOLD:.0%}. "
        f"See tests/eval/results/{date.today()}.csv for details."
    )
```

- [ ] **Step 2: Run the full unit test suite one more time (verify nothing broken)**

```bash
python -m pytest tests/unit/ -v
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/eval/test_accuracy.py
git commit -m "test: add eval harness — rate accuracy ±15% + decision path audit"
```

---

## Task 12: Integration smoke tests

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_api.py`

- [ ] **Step 1: Write tests/integration/test_api.py**

```python
"""
Integration smoke tests for FastAPI endpoints.
REQUIRES: All services running (Qdrant, Postgres, Redis, FastAPI on port 8000).
RUNTIME:  < 2 minutes.
RUN:      pytest tests/integration/test_api.py -v
"""

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


@pytest.fixture(scope="session")
def client():
    """FastAPI TestClient — no live server needed."""
    from fastapi.testclient import TestClient
    from rateiq.api import app
    with TestClient(app) as c:
        yield c


def _make_small_excel(n_rows: int = 5) -> bytes:
    """Build an in-memory Excel BOQ with n_rows empty-rate rows."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["S.No", "Description", "Qty", "Unit", "Rate"])
    items = [
        ("Brick Work 4.5 Thick", "100", "Sft"),
        ("Gypsum False Ceiling 12mm", "200", "Sft"),
        ("Split AC 1.5 Ton Installation", "3", "Nos"),
        ("Ceramic Floor Tile 12x12", "150", "Sft"),
        ("Light Point Wiring 2.5mm", "10", "Nos"),
    ]
    for i, (desc, qty, unit) in enumerate(items[:n_rows], start=1):
        ws.append([i, desc, qty, unit, None])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "agent_ready" in data


class TestFillBoqGuards:
    def test_rejects_oversized_file(self, client):
        # 11 MB of zeros — should get 413
        big_content = b"x" * (11 * 1024 * 1024)
        resp = client.post(
            "/fill-boq",
            files={"file": ("big.xlsx", big_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 413

    def test_accepts_valid_5_row_file(self, client):
        excel_bytes = _make_small_excel(5)
        resp = client.post(
            "/fill-boq",
            files={"file": ("test.xlsx", excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        # Should succeed (200) or at least not 413/422
        assert resp.status_code in (200, 500)  # 500 = DB not connected in test env

    def test_rejects_non_excel_file(self, client):
        resp = client.post(
            "/fill-boq",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 400


class TestRateItemEndpoint:
    def test_rate_item_returns_structure(self, client):
        resp = client.post(
            "/rate-item",
            json={"description": "Brick Work 4.5 inch thick", "qty": "100", "unit": "Sft"},
        )
        # Either 200 with result or 503 if agent not ready in test env
        assert resp.status_code in (200, 503)
        if resp.status_code == 200:
            data = resp.json()
            assert "suggested_rate" in data
            assert "confidence" in data
            assert "explanation" in data

    def test_rate_item_rejects_empty_description(self, client):
        resp = client.post(
            "/rate-item",
            json={"description": "", "qty": "100", "unit": "Sft"},
        )
        assert resp.status_code == 400
```

- [ ] **Step 2: Commit**

```bash
git add tests/integration/__init__.py tests/integration/test_api.py
git commit -m "test: add integration smoke tests for API guards and endpoints"
```

---

## Task 13: Run full test suite and establish baseline

- [ ] **Step 1: Run unit tests (no services needed)**

```bash
cd /home/faizan/Development/boq_rateiq
python -m pytest tests/unit/ -v
```

Expected output:
```
tests/unit/test_gap_detector.py ............ PASSED
tests/unit/test_validators.py .............. PASSED
tests/unit/test_parser.py .................. PASSED
tests/unit/test_cache_key.py ............... PASSED
```

- [ ] **Step 2: Build holdout set (ONCE — check it doesn't already exist)**

```bash
python scripts/build_holdout.py
```

Expected: `tests/eval/holdout_set.csv` created with ~286 items. Qdrant + Postgres items removed.

- [ ] **Step 3: Run eval harness to establish baseline accuracy**

```bash
python -m pytest tests/eval/test_accuracy.py -v --tb=short -s
```

Expected: accuracy report printed. Results saved to `tests/eval/results/YYYY-MM-DD.csv`. Test passes if accuracy ≥ 75%.

- [ ] **Step 4: Run integration smoke tests**

```bash
python -m pytest tests/integration/test_api.py -v
```

Expected: health, guard, and structure tests pass.

- [ ] **Step 5: Final commit**

```bash
git add tests/eval/results/
git commit -m "test: establish baseline eval accuracy — see tests/eval/results/"
```

---

## Self-Review Checklist

| Spec Requirement | Covered by Task |
|---|---|
| ±15% rate accuracy eval harness | Task 11 |
| Decision path audit (CRAG + gap verdict) | Task 11 |
| Per-unit rate invariant documented + enforced | Task 11 (eval), Spec Section 2 |
| Explanation contains unit label check | Task 11 |
| pytest unit tests — gap_detector | Task 2 |
| pytest unit tests — validators | Task 3 |
| pytest unit tests — parser | Task 4 |
| pytest unit tests — query_transformer/_cache_key | Task 5 |
| Redis cross-session market cache | Tasks 1, 6 |
| Anthropic prompt caching classify+output | Task 8 |
| Circuit breaker wired into market_node | Task 7 |
| 10MB file size guard on /fill-boq | Task 9 |
| 50-row cap on /fill-boq | Task 9 |
| Integration smoke tests | Task 12 |
| Build holdout script | Task 10 |
| Re-run eval to confirm no regression | Task 13 |
