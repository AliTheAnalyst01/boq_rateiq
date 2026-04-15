"""
RateIQ — BOQ Rate Suggestion System
Pakistani interior design company rate intelligence engine.

Quick start:
    from rateiq import initialize_all, process_boq_file
    agent = initialize_all()
    rec = agent.run("Providing and laying Brick Work 4.5\" Thick")
"""

from .config import settings
from .models import (
    BOQChunk,
    GapAnalysis,
    MarketRate,
    RateRecommendation,
    RateStats,
    SearchResult,
)
from .pipeline import initialize_all, process_boq_file

__all__ = [
    "initialize_all",
    "process_boq_file",
    "RateRecommendation",
    "BOQChunk",
    "SearchResult",
    "MarketRate",
    "GapAnalysis",
    "RateStats",
    "settings",
]
