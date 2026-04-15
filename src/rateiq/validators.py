"""
WHY:  Extracted rates from web search are unreliable — unit mismatches
      ("Rs.15,000/unit" for a sft item) are the #1 source of cascading
      price errors in the pipeline. Centralising bounds here means both
      market_search and gap_detector validate against the same source of truth.
WHAT: validate_rate() — returns bool: is this rate plausible for category+unit?
      bounds_hint()   — returns human-readable bound string for LLM prompts.
FITS INTO: Called by gap_detector.suggest_rate() and market_search extraction.
           Depends on nothing else in rateiq — pure lookup table + logic.
"""

import logging

logger = logging.getLogger(__name__)

# NOTE: Bounds are intentionally wide (Rs.20–Rs.5,000,000) to avoid false rejects.
# The 10x ratio gate in gap_detector.py is the primary defence against unit errors.
# These bounds only catch extreme extractions (Rs.3 for brick, Rs.500,000 for paint).
# Based on Pakistan Lahore construction market as of 2025-2026.
# (category, unit) → (min_realistic_PKR, max_realistic_PKR)
RATE_BOUNDS: dict[tuple[str, str], tuple[float, float]] = {
    # Civil / masonry / concrete
    ("civil_id",        "sft"):  (50,       3_000),
    ("civil_id",        "cft"):  (200,      5_000),
    ("civil_id",        "rft"):  (50,       3_000),
    ("civil_id",        "nos"):  (500,    500_000),
    ("civil_id",        "ls"):   (5_000, 50_000_000),
    # Finishing (plaster, paint, putty)
    ("finishing",       "sft"):  (20,       2_000),
    ("finishing",       "rft"):  (50,       2_000),
    # Ceiling (gypsum, acoustic, POP)
    ("ceiling",         "sft"):  (150,      5_000),
    # Flooring (tile, marble, vinyl, carpet)
    ("flooring",        "sft"):  (80,      10_000),
    # Plumbing
    ("plumbing",        "rft"):  (80,       5_000),
    ("plumbing",        "nos"):  (2_000, 1_000_000),
    ("plumbing",        "ls"):   (5_000, 50_000_000),
    # HVAC
    ("hvac",            "nos"):  (30_000, 5_000_000),
    ("hvac",            "rft"):  (80,       5_000),
    ("hvac",            "ls"):   (50_000, 100_000_000),
    # Electrical / ELV
    ("electrical_elv",  "nos"):  (300,   5_000_000),
    ("electrical_elv",  "mtr"):  (80,       8_000),
    ("electrical_elv",  "rft"):  (80,       5_000),
    ("electrical_elv",  "point"): (300,    50_000),
    ("electrical_elv",  "ls"):   (5_000, 100_000_000),
    # Carpentry / joinery / MDF
    ("carpentry",       "sft"):  (150,     15_000),
    ("carpentry",       "nos"):  (1_000, 2_000_000),
    ("carpentry",       "rft"):  (200,     10_000),
    # Fire fighting
    ("fire_fighting",   "nos"):  (500,   5_000_000),
    ("fire_fighting",   "rft"):  (100,      5_000),
    ("fire_fighting",   "ls"):   (5_000, 50_000_000),
}


def validate_rate(category: str, unit: str, rate: float) -> bool:
    """
    INPUT:  category str — work category (civil_id, hvac, electrical_elv, etc.)
            unit str     — normalized unit (sft, nos, rft, ls, mtr, etc.)
            rate float   — PKR rate to validate
    OUTPUT: bool — True if rate is within realistic bounds, False if not.
            Returns True (permissive) when no bounds are defined for the pair,
            so new categories are never silently blocked.

    EXAMPLE:
        >>> validate_rate("finishing", "sft", 14.0)   → False  (Rs.14/sft too low)
        >>> validate_rate("finishing", "sft", 150.0)  → True
        >>> validate_rate("hvac", "nos", 500.0)       → False  (Rs.500 for AC unit too low)
        >>> validate_rate("hvac", "nos", 450_000.0)   → True
        >>> validate_rate("unknown_cat", "sft", 1.0)  → True   (permissive — no bounds defined)
    """
    if rate is None or rate <= 0:
        return False

    key = (category, unit)
    if key not in RATE_BOUNDS:
        return True   # no bounds defined → allow

    lo, hi = RATE_BOUNDS[key]
    valid = lo <= rate <= hi

    if not valid:
        logger.debug(
            "validate_rate FAIL: %s/%s Rs.%.0f not in [%.0f, %.0f]",
            category, unit, rate, lo, hi,
        )
    return valid


def bounds_hint(category: str, unit: str) -> str:
    """
    INPUT:  category, unit
    OUTPUT: human-readable string for LLM prompts, e.g.
            "Rs.50 to Rs.3,000" — or empty string if no bounds defined.
    """
    key = (category, unit)
    if key not in RATE_BOUNDS:
        return ""
    lo, hi = RATE_BOUNDS[key]
    return f"Rs.{lo:,.0f} to Rs.{hi:,.0f}"
