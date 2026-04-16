"""
Unit tests for gap_detector pure functions.
No DB, no LLM, no Qdrant — all functions tested in isolation.
"""

import pytest

from rateiq.gap_detector import compute_gap, suggest_rate, _aggregate_from_search
from rateiq.models import GapAnalysis, SearchResult, BOQChunk


def _make_search_result(rate: float, unit: str = "sft", reranker_score: float = 5.0) -> SearchResult:
    chunk = BOQChunk(
        chunk_id="test", description_short="Test item", description_full="Test item full",
        section_title="TEST", work_category="civil_id", rate=rate, unit_norm=unit,
        qty="100", source_file="test.xlsx", sheet_name="Sheet1",
        rate_per_unit_label=f"Rs. {rate} per {unit}", embedding_text="",
    )
    return SearchResult(
        chunk=chunk, rrf_score=0.03, reranker_score=reranker_score,
        bm25_rank=0, dense_rank=0, ranking_method="reranker",
    )


def _make_gap(verdict: str, hist_avg: float, market_rate=None, gap_pct: float = 0.0,
              hist_count: int = 3, crag_verdict: str = "correct") -> GapAnalysis:
    return GapAnalysis(
        verdict=verdict, hist_avg=hist_avg, hist_count=hist_count,
        hist_min=hist_avg * 0.9, hist_max=hist_avg * 1.1,
        market_rate=market_rate, gap_pct=gap_pct, recommendation="test",
        crag_verdict=crag_verdict,
    )


class TestComputeGap:
    def test_zero_gap_returns_confirmed(self):
        gap_pct, verdict = compute_gap(315.0, 315.0)
        assert gap_pct == 0.0
        assert verdict == "CONFIRMED"

    def test_small_gap_returns_confirmed(self):
        gap_pct, verdict = compute_gap(315.0, 330.0)
        assert gap_pct == pytest.approx(4.76, abs=0.01)
        assert verdict == "CONFIRMED"

    def test_exactly_at_confirmed_boundary(self):
        gap_pct, verdict = compute_gap(100.0, 115.0)
        assert gap_pct == pytest.approx(15.0, abs=0.01)
        assert verdict == "MINOR_GAP"

    def test_minor_gap_verdict(self):
        gap_pct, verdict = compute_gap(200.0, 250.0)
        assert gap_pct == pytest.approx(25.0, abs=0.01)
        assert verdict == "MINOR_GAP"

    def test_exactly_at_major_gap_boundary(self):
        gap_pct, verdict = compute_gap(100.0, 135.0)
        assert gap_pct == pytest.approx(35.0, abs=0.01)
        assert verdict == "MAJOR_GAP"

    def test_large_gap_returns_major_gap(self):
        gap_pct, verdict = compute_gap(315.0, 520.0)
        assert gap_pct == pytest.approx(65.08, abs=0.1)
        assert verdict == "MAJOR_GAP"

    def test_zero_hist_avg_returns_major_gap(self):
        gap_pct, verdict = compute_gap(0.0, 500.0)
        assert gap_pct == 100.0
        assert verdict == "MAJOR_GAP"

    def test_market_lower_than_hist(self):
        gap_pct, verdict = compute_gap(400.0, 360.0)
        assert gap_pct == pytest.approx(10.0, abs=0.01)
        assert verdict == "CONFIRMED"


class TestSuggestRate:
    def test_confirmed_returns_hist_avg_high_confidence(self):
        gap = _make_gap("CONFIRMED", hist_avg=315.0, market_rate=330.0)
        rate, conf = suggest_rate(gap, [])
        assert rate == 315.0
        assert conf == "high"

    def test_minor_gap_returns_midpoint_medium_confidence(self):
        gap = _make_gap("MINOR_GAP", hist_avg=300.0, market_rate=400.0, gap_pct=33.0)
        rate, conf = suggest_rate(gap, [])
        assert rate == 350.0
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
        assert conf == "medium"

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
        gap = _make_gap("MAJOR_GAP", hist_avg=300.0, market_rate=3100.0, gap_pct=933.0)
        rate, conf = suggest_rate(gap, [], category="civil_id", unit="sft")
        assert rate == 300.0
        assert conf == "medium"


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
        results = [
            _make_search_result(rate=300.0, unit="sft", reranker_score=8.0),
            _make_search_result(rate=600.0, unit="sft", reranker_score=2.0),
        ]
        avg, mn, mx, count = _aggregate_from_search(results, "sft")
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
