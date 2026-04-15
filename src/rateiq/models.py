"""
WHY:  Every data structure in the pipeline is defined here so any reader
      can understand what flows between nodes without opening agent.py.
      No raw dicts pass between functions — every shape is named and typed.
WHAT: Typed dataclasses for BOQ chunks, search results, rate recommendations,
      gap analysis, and market rates.
FITS INTO: Imported by agent.py, gap_detector.py, pipeline.py, api.py.
           Nothing in this file imports from other rateiq modules.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class BOQChunk:
    """
    One line item from a past BOQ tender.

    INPUT:  raw CSV row data from data/processed/boq_chunks.csv
    OUTPUT: structured dataclass representing one historical rate entry

    EXAMPLE:
        >>> input:  CSV row with chunk_id="10b1cc5b...", rate=315.0
        >>> output: BOQChunk(description_short="BRICKWORK 4 1/2\"",
                             rate=315.0, unit_norm="sft", ...)
    """

    chunk_id: str
    description_short: str
    description_full: str
    section_title: str
    work_category: str          # civil_id / hvac / electrical_elv / etc.
    rate: float                 # PKR
    unit_norm: str              # sft / nos / rft / ls / etc.
    qty: str
    source_file: str
    sheet_name: str
    rate_per_unit_label: str    # "Rs. 315 per sft"
    embedding_text: str


@dataclass
class SearchResult:
    """
    One result returned from hybrid search.

    INPUT:  a matched BOQChunk plus scores from BM25, dense, and reranker
    OUTPUT: ranked result with all scoring metadata attached

    EXAMPLE:
        >>> input:  BOQChunk(rate=315.0, ...), rrf_score=0.031, reranker_score=6.8
        >>> output: SearchResult(chunk=BOQChunk(...), rrf_score=0.031,
                                 reranker_score=6.8, ranking_method="reranker")
    """

    chunk: BOQChunk
    rrf_score: float            # Reciprocal Rank Fusion score
    reranker_score: float       # Cross-encoder score
    bm25_rank: int              # BM25 position (0-indexed)
    dense_rank: int             # Vector search position (0-indexed)
    ranking_method: str         # "reranker" or "rrf_fallback"


@dataclass
class RateStats:
    """
    Statistical summary of a rate across past tenders.

    INPUT:  multiple BOQChunk records for the same item type
    OUTPUT: aggregated statistics showing rate range, average, spread

    EXAMPLE:
        >>> input:  4 rows of "gypsum ceiling" at 375, 405, 430, 455
        >>> output: RateStats(count=4, min_rate=375.0, max_rate=455.0,
                              avg_rate=416.25, median_rate=417.5, std_rate=33.0,
                              source_files=["3.CEILING.xlsx", ...])
    """

    item_keyword: str
    count: int                  # how many times quoted in past tenders
    min_rate: float
    max_rate: float
    avg_rate: float
    median_rate: float
    std_rate: float
    source_files: list[str] = field(default_factory=list)   # past tender files
    sample_rates: list[float] = field(default_factory=list)


@dataclass
class MarketRate:
    """
    Current Pakistan market rate from web search.

    INPUT:  Tavily search results + Claude extraction for a specific item
    OUTPUT: structured market rate with source citation and confidence

    EXAMPLE:
        >>> input:  Tavily results about "brick wall 4.5 inch Pakistan 2025"
        >>> output: MarketRate(contractor_rate=480.0, rate_type="material_only",
                               source_name="Niazi Bricks",
                               source_url="https://niazibricks.pk/...",
                               confidence="medium")
    """

    raw_rate: float | None              # as found online
    contractor_rate: float | None       # after markup if material_only
    rate_type: str                      # "material_only" / "contractor_rate" / "unknown"
    source_name: str | None             # "Niazi Bricks", "PriceIndex.pk"
    source_url: str | None
    raw_price_text: str | None          # "PKR 13,000-16,000 per 1000 bricks"
    source_year: int | None = None      # year the rate was published/found (e.g. 2025)
    confidence: str = "low"            # "high" / "medium" / "low"
    note: str | None = None            # caveat or extra context


@dataclass
class GapAnalysis:
    """
    Comparison between historical rate and current market rate.

    INPUT:  RateStats from PostgreSQL + MarketRate from web search
    OUTPUT: verdict on whether historical rate is still valid

    EXAMPLE:
        >>> input:  hist_avg=315.0, market_rate=330.0
        >>> output: GapAnalysis(verdict="CONFIRMED", gap_pct=4.76,
                                recommendation="Historical Rs.315/sft matches
                                market Rs.330 (4.8% gap). Rate confirmed.")
    """

    verdict: str            # CONFIRMED / MINOR_GAP / MAJOR_GAP / NO_MARKET_DATA
    hist_avg: float
    hist_count: int
    hist_min: float
    hist_max: float
    market_rate: float | None
    gap_pct: float
    recommendation: str     # plain English explanation
    # Decision provenance — carried for audit trail and Excel DECISION_PATH column
    hist_source: str = "unknown"    # "sql_3word" | "sql_2word" | "search_results" | "none"
    crag_verdict: str = "unknown"   # "correct" | "ambiguous" | "incorrect" | "unknown"
    market_rejected: bool = False   # True if market rate extracted but failed sanity bounds
    # LIMITATION: hist_avg is a mean across all matched rows — outliers can skew it.
    # If hist_count < 3, treat confidence as "low" regardless of CRAG verdict.


@dataclass
class RateRecommendation:
    """
    Final output for one BOQ row.

    INPUT:  all pipeline outputs — search results, stats, market rate, gap analysis
    OUTPUT: single clean recommendation with the rate to fill into the BOQ

    EXAMPLE:
        >>> input:  description="Providing and laying Brick Work 4.5 Thick"
        >>> output: RateRecommendation(suggested_rate=315.0,
                                       confidence="high",
                                       explanation="Based on BOQ-01.xlsx...",
                                       source_refs=["BOQ - 01 .xlsx", "Niazi Bricks"])
    """

    description: str                            # original description from new BOQ
    unit: str
    suggested_rate: float                       # PKR — the number to fill in BOQ
    confidence: str                             # high / medium / low
    explanation: str                            # plain English, max 60 words
    source_refs: list[str] = field(default_factory=list)        # past tender files + market source
    search_results: list[SearchResult] = field(default_factory=list)
    rate_stats: RateStats | None = None
    market_rate: MarketRate | None = None
    gap_analysis: GapAnalysis | None = None
    work_category: str = ""
    search_keywords: str = ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    chunk = BOQChunk(
        chunk_id="abc123",
        description_short="Brick Work 4.5 Thick",
        description_full="Providing and laying brick masonry...",
        section_title="BRICKWORK",
        work_category="civil_id",
        rate=315.0,
        unit_norm="sft",
        qty="1000",
        source_file="BOQ-01.xlsx",
        sheet_name="Civil",
        rate_per_unit_label="Rs. 315 per sft",
        embedding_text="WORK: civil_id\nITEM: Brick Work 4.5 Thick",
    )

    result = SearchResult(
        chunk=chunk,
        rrf_score=0.031,
        reranker_score=6.819,
        bm25_rank=0,
        dense_rank=1,
        ranking_method="reranker",
    )

    stats = RateStats(
        item_keyword="brick work 4.5",
        count=3,
        min_rate=310.0,
        max_rate=320.0,
        avg_rate=315.0,
        median_rate=315.0,
        std_rate=5.0,
        source_files=["BOQ-01.xlsx", "ID Works.xlsx"],
        sample_rates=[310.0, 315.0, 320.0],
    )

    logger.info("BOQChunk: %s", chunk.description_short)
    logger.info("SearchResult reranker_score: %s", result.reranker_score)
    logger.info("RateStats count: %s, avg: %s", stats.count, stats.avg_rate)
