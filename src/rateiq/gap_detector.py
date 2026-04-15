"""
WHY:  The gap detector is the core decision engine. It decides whether
      historical rates are trustworthy (CRAG reflection), how to aggregate them,
      whether market data is plausible (MAST ratio gate), and what final rate
      to suggest (CONFIRMED / MINOR_GAP / MAJOR_GAP / NO_HIST).
WHAT: analyze()      — full pipeline: CRAG + SQL + search fallback + gap computation.
      suggest_rate() — final rate + confidence from GapAnalysis verdict.
      compute_gap()  — pure: gap % + verdict from two rate numbers.
FITS INTO: Called by agent._gap_node(). Depends on postgres_store, validators, config.
"""

import logging

from .config import settings
from .models import GapAnalysis, MarketRate, RateStats, SearchResult
from .postgres_store import rate_stats
from .validators import validate_rate

logger = logging.getLogger(__name__)

# ── Decision thresholds ────────────────────────────────────────────────────
# WHY: Named constants so any reader can trace "where did 20% come from" without
#      hunting through logic. Changes here propagate to every branch automatically.
_SQL_SPREAD_THRESHOLD: float = 20.0  # if std/avg > 20% → SQL results are too mixed
_MAST_HIGH_RATIO: float = 10.0       # market/hist > 10x → likely unit extraction error
_MAST_LOW_RATIO: float = 0.1         # market/hist < 0.1x → likely unit extraction error
_MIN_HIST_COUNT: int = 3             # below this, do not grant "high" confidence


# ── Pure helper: CRAG retrieval filter ────────────────────────────────────

def _run_crag_filter(
    search_results: list[SearchResult],
) -> tuple[list[SearchResult], str]:
    """
    WHY:  Implements Andrew Ng's Reflection pattern — if retrieved chunks are
          not relevant to this item, using them to compute hist_avg would inject
          noise (e.g., 9" brick rates contaminating a 4.5" brick query).
    READS:  reranker_score on each SearchResult; RETRIEVAL thresholds from settings
    WRITES: returns (filtered_results, crag_verdict_string)
    Pure function — no DB, no logging side-effects (caller logs).
    """
    if not search_results:
        return [], "unknown"

    top_score = max(r.reranker_score for r in search_results)

    if top_score < settings.RETRIEVAL_INCORRECT_THRESHOLD:
        return [], "incorrect"
    if top_score < settings.RETRIEVAL_CORRECT_THRESHOLD:
        return search_results, "ambiguous"
    return search_results, "correct"


# ── Pure helper: SQL keyword strategy ─────────────────────────────────────

def _query_sql_stats(
    keyword_3: str,
    keyword_2: str,
    unit: str | None,
) -> tuple[RateStats | None, str]:
    """
    WHY:  Tries 3-word keyword first (specific) to discriminate specs.
          "brick work 4.5" matches only 4.5-inch items; "brick work" alone would
          average together 4.5" (Rs.315) and 9" (Rs.520) giving a wrong Rs.388.
          Falls back to 2-word if 3-word returns nothing (handles variant spellings).
    READS:  postgres_store.rate_stats() — DB query, not pure
    WRITES: returns (stats_or_None, hist_source_label)
    """
    if keyword_3:
        stats = rate_stats(keyword_3, unit)
        if stats and stats.count > 0:
            logger.info("SQL 3-word match: '%s' → %d rows", keyword_3, stats.count)
            return stats, "sql_3word"

    stats = rate_stats(keyword_2, unit)
    if stats and stats.count > 0:
        logger.info("SQL 2-word fallback: '%s' → %d rows", keyword_2, stats.count)
        return stats, "sql_2word"

    return None, "none"


# ── Pure helper: aggregate hist stats from search results ─────────────────

def _aggregate_from_search(
    search_results: list[SearchResult],
    unit: str,
) -> tuple[float, float, float, int]:
    """
    WHY:  SQL fallback when keyword is too generic or item is new to the DB.
          Uses reranker-score-weighted average of top-3 so a high-scoring
          near-match (exact spec) dominates over a low-scoring distant match.
          Unit filter prevents rft/cft contaminating sft averages.
    Returns (hist_avg, hist_min, hist_max, hist_count) — zeros if no valid data.
    Pure function — no DB access.
    """
    if not search_results:
        return 0.0, 0.0, 0.0, 0

    # Prefer unit-matched results; fall back to all if none match
    if unit and unit != "unit":
        unit_matched = [r for r in search_results if r.chunk.unit_norm == unit]
        candidates = unit_matched if unit_matched else search_results
        if not unit_matched:
            logger.info(
                "search_results fallback: no unit=%s matches, using all %d results",
                unit, len(search_results),
            )
    else:
        candidates = search_results

    top3 = sorted(candidates, key=lambda r: r.reranker_score, reverse=True)[:3]
    valid_pairs = [
        (r.chunk.rate, max(r.reranker_score, 0.01))
        for r in top3
        if r.chunk.rate and r.chunk.rate > 0
    ]
    if not valid_pairs:
        return 0.0, 0.0, 0.0, 0

    total_weight = sum(s for _, s in valid_pairs)
    weighted_avg = sum(rate * score for rate, score in valid_pairs) / total_weight
    logger.info(
        "CRAG weighted-avg fallback: count=%d, weighted_avg=%.0f (rates=%s, scores=%s)",
        len(valid_pairs),
        weighted_avg,
        [r for r, _ in valid_pairs],
        [round(s, 2) for _, s in valid_pairs],
    )
    return (
        round(weighted_avg, 2),
        min(r for r, _ in valid_pairs),
        max(r for r, _ in valid_pairs),
        len(valid_pairs),
    )


# ── Pure helper: build gap recommendation text ────────────────────────────

def _build_gap_recommendation(
    verdict: str,
    hist_avg: float,
    unit: str,
    hist_count: int,
    mkt_rate: float,
    gap_pct: float,
) -> str:
    """
    WHY:  Recommendation text is purely a formatting concern — isolated so
          the gap logic can be tested without asserting on prose strings.
    Pure function — no side effects.
    """
    if verdict == "CONFIRMED":
        return (
            f"Historical Rs.{hist_avg:,.0f}/{unit} ({hist_count} tenders) matches "
            f"market Rs.{mkt_rate:,.0f} ({gap_pct:.1f}% gap). Rate confirmed."
        )
    if verdict == "MINOR_GAP":
        midpoint = round((hist_avg + mkt_rate) / 2, 0)
        return (
            f"Historical Rs.{hist_avg:,.0f}/{unit} vs market Rs.{mkt_rate:,.0f} "
            f"({gap_pct:.1f}% gap). Minor inflation. Suggest midpoint Rs.{midpoint:,.0f}."
        )
    # MAJOR_GAP
    return (
        f"Historical Rs.{hist_avg:,.0f}/{unit} vs market Rs.{mkt_rate:,.0f} "
        f"({gap_pct:.1f}% gap). Significant price change — use current market rate."
    )


# ── Pure helper: MAST ratio gate ──────────────────────────────────────────

def _apply_mast_ratio_gate(
    market_rate: float,
    hist_avg: float,
    category: str,
    unit: str,
) -> tuple[float, str, bool]:
    """
    WHY:  MAST anti-cascade pattern — never let one agent's output become another
          agent's unverified premise. A 10x+ ratio almost always means the web
          extractor confused units (Rs.15,000/unit → Rs.300/sft using a made-up
          divisor). Use history instead of propagating the hallucination.
    READS:  _MAST_HIGH_RATIO, _MAST_LOW_RATIO constants
    WRITES: returns (chosen_rate, confidence, was_gated)
    Pure function — no DB, no logging (caller logs).
    """
    if hist_avg > 0:
        ratio = market_rate / hist_avg
        if ratio > _MAST_HIGH_RATIO or ratio < _MAST_LOW_RATIO:
            # Unit error detected — trust history
            return round(hist_avg, 0), "medium", True

    # Bounds check — reject if outside realistic range for this category/unit
    if category and not validate_rate(category, unit, market_rate):
        return round(hist_avg, 0) if hist_avg > 0 else market_rate, "low", False

    return round(market_rate, 0), "low", False


# ── Pure helper: rate + confidence for each verdict branch ────────────────

def _pick_rate_for_verdict(
    gap: GapAnalysis,
    search_results: list[SearchResult],
    category: str,
    unit: str,
) -> tuple[float, str]:
    """
    WHY:  Verdict → rate+confidence mapping isolated so each branch can be
          unit-tested independently without constructing a full pipeline state.
    READS:  gap.verdict, gap.hist_avg, gap.market_rate, search_results
    Pure function — no logging (caller logs decision summary).
    """
    verdict = gap.verdict
    rate: float = 0.0
    confidence: str = "low"

    if verdict == "CONFIRMED":
        if gap.hist_avg > 0:
            return round(gap.hist_avg, 0), "high"
        if gap.market_rate and gap.market_rate > 0:
            return round(gap.market_rate, 0), "medium"

    elif verdict == "MINOR_GAP":
        if gap.market_rate is not None:
            rate = round((gap.hist_avg + gap.market_rate) / 2, 0)
        else:
            rate = round(gap.hist_avg, 0)
        return rate, "medium"

    elif verdict == "MAJOR_GAP":
        mkt = gap.market_rate
        if mkt and mkt > 0:
            rate, confidence, gated = _apply_mast_ratio_gate(mkt, gap.hist_avg, category, unit)
            if gated:
                logger.warning(
                    "MAST ratio gate: market Rs.%.0f / hist Rs.%.0f = %.1fx — "
                    "likely unit error in web extraction, using historical",
                    mkt, gap.hist_avg, mkt / max(gap.hist_avg, 0.01),
                )
            elif confidence == "low" and rate == gap.hist_avg:
                logger.warning(
                    "CRAG sanity gate: market Rs.%.0f failed bounds for %s/%s "
                    "— falling back to historical Rs.%.0f",
                    mkt, category, unit, gap.hist_avg,
                )
            return rate, confidence
        if gap.hist_avg > 0:
            return round(gap.hist_avg, 0), "low"

    else:  # NO_MARKET_DATA
        if gap.hist_avg > 0:
            return round(gap.hist_avg, 0), "medium"

    # Universal fallback: use first valid search result rather than returning Rs.0
    valid = [r for r in search_results if r.chunk.rate and r.chunk.rate > 0]
    if valid:
        logger.info("suggest_rate guard: rate was 0, using search_result Rs.%.0f", valid[0].chunk.rate)
        return round(valid[0].chunk.rate, 0), "low"

    return 0.0, "low"


# ── Public: compute gap percentage and verdict ─────────────────────────────

def compute_gap(hist_avg: float, market_rate: float) -> tuple[float, str]:
    """
    INPUT:  hist_avg float — historical average rate PKR
            market_rate float — current market rate PKR
    OUTPUT: (gap_pct, verdict) — gap_pct is always positive; verdict is
            CONFIRMED / MINOR_GAP / MAJOR_GAP based on config thresholds.

    EXAMPLE:
        >>> compute_gap(315.0, 330.0)  → (4.76, "CONFIRMED")
        >>> compute_gap(315.0, 520.0)  → (65.08, "MAJOR_GAP")
    """
    try:
        if hist_avg <= 0:
            # WHY: No historical basis — MAJOR_GAP routes suggest_rate() to market.
            logger.info("compute_gap: hist_avg=0, no history — returning MAJOR_GAP")
            return 100.0, "MAJOR_GAP"

        gap_pct = round(abs(market_rate - hist_avg) / hist_avg * 100.0, 2)

        if gap_pct < settings.GAP_CONFIRMED_PCT:
            verdict = "CONFIRMED"
        elif gap_pct < settings.GAP_MINOR_PCT:
            verdict = "MINOR_GAP"
        else:
            verdict = "MAJOR_GAP"

        return gap_pct, verdict

    except Exception as exc:
        logger.error("compute_gap failed: %s", exc)
        return 0.0, "CONFIRMED"


# ── Public: full historical + market gap analysis ─────────────────────────

def analyze(
    description_fragment: str,
    unit: str,
    market_rate: MarketRate,
    search_results: list[SearchResult],
) -> GapAnalysis:
    """
    INPUT:  description_fragment — keyword for SQL lookup (e.g. "brick work 4.5")
            unit — normalised unit string (e.g. "sft")
            market_rate — MarketRate from market_search (may have contractor_rate=None)
            search_results — ranked RAG results used as fallback if SQL returns nothing
    OUTPUT: GapAnalysis with verdict, rate stats, gap %, and recommendation text.

    Decision flow:
      1. CRAG filter — drop search_results if reranker score too low (Reflection)
      2. SQL 3-word → 2-word fallback for historical avg
      3. search_results weighted-avg if SQL returned nothing
      4. Gap verdict: NO_MARKET_DATA / CONFIRMED / MINOR_GAP / MAJOR_GAP
    """
    try:
        # Step 1: CRAG filter
        search_results, crag_verdict = _run_crag_filter(search_results)
        if crag_verdict == "incorrect":
            logger.info(
                "CRAG Incorrect branch: retrieved chunks irrelevant, skipping historical fallback."
            )
        else:
            logger.info("CRAG verdict: %s", crag_verdict)

        # Step 2: SQL historical stats (3-word first, 2-word fallback)
        words = description_fragment.split()
        keyword_3 = " ".join(words[:3]) if len(words) >= 3 else ""
        keyword_2 = " ".join(words[:2])
        sql_unit = unit if unit != "unit" else None

        stats, hist_source = _query_sql_stats(keyword_3, keyword_2, sql_unit)

        hist_avg, hist_count, hist_min, hist_max = 0.0, 0, 0.0, 0.0
        if stats and stats.count > 0:
            spread_pct = (stats.std_rate / stats.avg_rate * 100) if stats.avg_rate else 0
            if spread_pct > _SQL_SPREAD_THRESHOLD and search_results:
                # WHY: Wide spread means the keyword matched mixed specs (e.g. both
                # 4.5" and 9" brickwork). Prefer semantically-ranked search_results.
                logger.info(
                    "SQL stats too broad (spread=%.1f%% > %.0f%%) — preferring search_results.",
                    spread_pct, _SQL_SPREAD_THRESHOLD,
                )
            else:
                hist_avg = stats.avg_rate
                hist_count = stats.count
                hist_min = stats.min_rate
                hist_max = stats.max_rate
                logger.info(
                    "SQL stats: count=%d avg=%.0f min=%.0f max=%.0f spread=%.1f%%",
                    hist_count, hist_avg, hist_min, hist_max, spread_pct,
                )

        # Step 3: search_results fallback if SQL returned nothing usable
        if hist_count == 0 and search_results:
            hist_avg, hist_min, hist_max, hist_count = _aggregate_from_search(
                search_results, unit
            )
            if hist_count > 0:
                hist_source = "search_results"

        # Step 4: detect market_rejected flag (rate was extracted but failed bounds)
        mkt_rate_val = market_rate.contractor_rate if market_rate else None
        mkt_note = (market_rate.note or "") if market_rate else ""
        market_rejected = (
            mkt_rate_val is None
            and market_rate is not None
            and "outside realistic bounds" in mkt_note
        )

        # Step 5: build GapAnalysis
        if mkt_rate_val is None or mkt_rate_val <= 0:
            return _build_no_market_gap(
                hist_avg, hist_count, hist_min, hist_max,
                unit, hist_source, crag_verdict, market_rejected,
            )

        gap_pct, verdict = compute_gap(hist_avg, mkt_rate_val)
        recommendation = _build_gap_recommendation(
            verdict, hist_avg, unit, hist_count, mkt_rate_val, gap_pct
        )
        return GapAnalysis(
            verdict=verdict,
            hist_avg=hist_avg,
            hist_count=hist_count,
            hist_min=hist_min,
            hist_max=hist_max,
            market_rate=mkt_rate_val,
            gap_pct=gap_pct,
            recommendation=recommendation,
            hist_source=hist_source,
            crag_verdict=crag_verdict,
            market_rejected=market_rejected,
        )

    except Exception as exc:
        logger.error("analyze failed: %s", exc)
        return GapAnalysis(
            verdict="NO_MARKET_DATA",
            hist_avg=0.0, hist_count=0, hist_min=0.0, hist_max=0.0,
            market_rate=None, gap_pct=0.0,
            recommendation=f"Gap analysis failed: {exc}",
        )


def _build_no_market_gap(
    hist_avg: float,
    hist_count: int,
    hist_min: float,
    hist_max: float,
    unit: str,
    hist_source: str,
    crag_verdict: str,
    market_rejected: bool,
) -> GapAnalysis:
    """
    WHY:  Extracted so analyze() returns at a single point for the no-market case,
          keeping the main function under 40 lines.
    Pure function — builds the GapAnalysis dataclass with appropriate recommendation.
    """
    if hist_count == 0:
        rec = "No historical data or market rate found. Manual rate research required."
    else:
        rec = (
            f"Historical avg Rs.{hist_avg:,.0f}/{unit} from {hist_count} tender(s). "
            "No current market data found — using historical rate."
        )
    return GapAnalysis(
        verdict="NO_MARKET_DATA",
        hist_avg=hist_avg,
        hist_count=hist_count,
        hist_min=hist_min,
        hist_max=hist_max,
        market_rate=None,
        gap_pct=0.0,
        recommendation=rec,
        hist_source=hist_source,
        crag_verdict=crag_verdict,
        market_rejected=market_rejected,
    )


# ── Public: suggest final rate + confidence ───────────────────────────────

def suggest_rate(
    gap: GapAnalysis,
    search_results: list[SearchResult],
    category: str = "",
    unit: str = "",
) -> tuple[float, str]:
    """
    INPUT:  gap — GapAnalysis from analyze()
            search_results — fallback if all rate sources are empty
            category — work category for bounds validation
            unit — normalised unit string
    OUTPUT: (suggested_rate_PKR, confidence_string)
            confidence: "high" / "medium" / "low"

    Decision rules:
      CONFIRMED      → hist_avg,               confidence=high
      MINOR_GAP      → midpoint(hist, market), confidence=medium
      MAJOR_GAP      → market (MAST gate),     confidence=low (or medium if gated)
      NO_MARKET_DATA → hist_avg,               confidence=medium
      fallback       → top search_result rate, confidence=low

    EXAMPLE:
        >>> suggest_rate(GapAnalysis(verdict="CONFIRMED", hist_avg=315.0, ...), [])
        (315.0, "high")
    """
    try:
        rate, confidence = _pick_rate_for_verdict(gap, search_results, category, unit)

        # Reflection pattern: CRAG ambiguous means retrieved history is uncertain —
        # "high" confidence on uncertain data is misleading to the estimator.
        if gap.crag_verdict == "ambiguous" and confidence == "high":
            confidence = "medium"
            logger.info("suggest_rate: CRAG ambiguous — downgrading confidence to medium")

        return rate, confidence

    except Exception as exc:
        logger.error("suggest_rate failed: %s", exc)
        valid = [r for r in search_results if r.chunk.rate and r.chunk.rate > 0]
        return (round(valid[0].chunk.rate, 0), "low") if valid else (0.0, "low")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    gap_pct, verdict = compute_gap(315.0, 330.0)
    logger.info("compute_gap(315, 330): gap=%.2f%%, verdict=%s", gap_pct, verdict)

    gap_pct2, verdict2 = compute_gap(315.0, 520.0)
    logger.info("compute_gap(315, 520): gap=%.2f%%, verdict=%s", gap_pct2, verdict2)

    fake_gap = GapAnalysis(
        verdict="CONFIRMED",
        hist_avg=315.0, hist_count=3, hist_min=310.0, hist_max=320.0,
        market_rate=330.0, gap_pct=4.76,
        recommendation="Rate confirmed.",
    )
    rate, conf = suggest_rate(fake_gap, [])
    logger.info("suggest_rate(CONFIRMED, 315): rate=%.0f, confidence=%s", rate, conf)
