"""
WHY:  Historical BOQ data only covers items in past tenders. For new items
      or items with major price movements, we need current market prices.
      This module searches the web via Tavily, then uses Claude to extract
      a structured per-unit rate from unstructured supplier/forum text.
WHAT: search_market_rate() — public entry point with batch-level keyword cache.
      _run_tavily_and_extract() — one Tavily search + Claude extraction round.
      extract_rate_from_results() — Claude call to parse rate from web text.
      clear_market_cache() — reset deduplication cache between pipeline runs.
FITS INTO: Called by agent._market_node(). Depends on Tavily API + Anthropic API.
           Context pruned to ~400 tokens (answer + 2×300 chars) — rate info is
           in the first sentence of a result, not buried 600 chars in.
"""

import datetime
import json
import logging

from anthropic import Anthropic
from openai import OpenAI
from tavily import TavilyClient
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import (
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
    TAVILY_API_KEY,
    USE_OPENAI_FALLBACK,
    settings,
)
from .models import MarketRate
from .validators import bounds_hint, validate_rate

logger = logging.getLogger(__name__)

# ── Static system prompt (cache-eligible by Anthropic) ────────────────────
# WHY: Separated from the dynamic per-call parts (item, unit, web_context)
#      so Anthropic's prompt cache can hold the static text across calls.
#      Only the 3 dynamic lines at the end change per request.
MARKET_EXTRACT_SYSTEM = """You are a Pakistan construction cost analyst.

IMPORTANT: You are looking for the UNIT RATE for
a specific construction item (like Rs.315 per sft
for brick work), NOT the total construction cost
per square foot for an entire house or building.

If the results only show total house construction
costs (like Rs.2,800-3,400 per sft grey structure),
return market_rate=null because that is NOT the
item-level rate we need.

For brick work 4.5 inch specifically:
- We need: Rs.300-400 per sft (masonry rate)
- NOT: Rs.2,800-3,400 per sft (whole house rate)

For gypsum ceiling:
- We need: Rs.400-600 per sft (ceiling contractor rate)
- NOT: Rs.1,400-1,700 per sft (whole finishing rate)"""

# ── Static extraction prompt template ─────────────────────────────────────
# WHY: Static instructions separated so _build_extraction_prompt() only
#      injects the small dynamic portion (item + context) per call.
_EXTRACTION_PROMPT_TEMPLATE = """\
You are a Pakistan construction cost analyst.

Extract the current market rate for the following item from the web search results below.

Item: {keywords}
Unit: {unit}
Category: {category}
Today's year: {current_year}{bounds_instruction}

Web search results:
{web_context}

Return a JSON object with exactly these keys:
{{
  "market_rate": <float in PKR per {unit}, or null if not found>,
  "rate_type": <"material_only" | "contractor_rate" | "unknown">,
  "rate_source_name": <string name of the source website/supplier, or null>,
  "rate_source_url": <EXACT URL from the web results above where this rate was found>,
  "raw_price_text": <exact price text from source, include year if visible, e.g. "PKR 13,000-16,000 per 1000 bricks (2025)">,
  "source_year": <int year the rate was published/found, e.g. 2025, or null if unknown>,
  "confidence": <"high" | "medium" | "low">,
  "note": <brief caveat if any, or null>
}}

RECENCY RULE: Prefer rates from {current_year} or {current_year_minus1}.
If only pre-{current_year_minus1} rates are available, set confidence="low" and note the year.

UNIT RULE: Extract rate per {unit} for THIS SPECIFIC ITEM only.
Do NOT extract total building construction cost per sft (grey structure rate).
Do NOT extract material unit price (per bag, per litre, per 1000 bricks) as the final rate.
If source gives price per a different unit, convert mathematically and set rate_type="material_only".
If you cannot confidently convert to per {unit}, set market_rate=null.

Copy the URL EXACTLY as it appears in the web results — do not modify or guess.
Return valid JSON only, no markdown fences."""

# ── Empty result sentinel ─────────────────────────────────────────────────
_EMPTY_EXTRACTION: dict = {
    "market_rate": None,
    "rate_type": "unknown",
    "rate_source_name": None,
    "rate_source_url": None,
    "raw_price_text": None,
    "confidence": "low",
    "note": None,
}

# ── Lazy singletons ────────────────────────────────────────────────────────
_tavily_client: TavilyClient | None = None
_llm_client: Anthropic | None | object = None

# ── Batch-level market search cache ───────────────────────────────────────
# WHY: In a 20-item BOQ, several items are near-duplicates ("Brick Work 4.5 inch",
#      "Brick Wall 4.5 Thick"). Each currently triggers 1-3 Tavily calls.
#      Normalised keyword cache achieves ~15-25% hit rate per batch.
# NOTE: Cleared per batch run (not per request) — stale rates are acceptable
#       within a single session but not across sessions.
_market_cache: dict[str, MarketRate] = {}


def _cache_key(keywords: str, unit: str, category: str) -> str:
    """
    WHY:  Normalise to first 3 meaningful words so minor description differences
          ("brick work 4.5 inch" vs "brick masonry 4.5 thick") share a cache slot.
    Pure function — no side effects.
    """
    words = [w for w in keywords.lower().split() if len(w) > 2][:3]
    return f"{' '.join(words)}|{unit}|{category}"


def clear_market_cache() -> None:
    """Call at the start of each pipeline batch run to prevent stale rate reuse."""
    global _market_cache
    _market_cache.clear()
    logger.debug("Market search cache cleared.")


# ── Lazy singleton getters ─────────────────────────────────────────────────

def _get_tavily() -> TavilyClient:
    """WHY: Singleton avoids re-initialising TavilyClient per search call."""
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
    return _tavily_client


def _get_llm() -> Anthropic | OpenAI:
    """
    WHY:  Lazy init with OpenAI fallback — if Anthropic balance is exhausted,
          the system degrades gracefully to GPT-4o rather than stopping.
    NOTE: Probe call on first init costs 1 token but confirms the key works.
    """
    global _llm_client
    if _llm_client is None:
        try:
            _llm_client = Anthropic(api_key=ANTHROPIC_API_KEY)
            _llm_client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            logger.info("Market search using Anthropic")
        except Exception as e:
            if "credit balance" in str(e).lower() or "400" in str(e):
                logger.warning("Anthropic credit low in market_search: %s", e)
                if USE_OPENAI_FALLBACK and OPENAI_API_KEY:
                    _llm_client = OpenAI(api_key=OPENAI_API_KEY)
                    logger.info("Market search using OpenAI fallback")
                else:
                    raise RuntimeError(
                        "Anthropic credit low. Set USE_OPENAI_FALLBACK=true and OPENAI_API_KEY."
                    )
            else:
                raise
    return _llm_client


# ── Pure helper: build Tavily query string ────────────────────────────────

def build_query(keywords: str, unit: str, category: str) -> str:
    """
    WHY:  Query tuning is critical — generic queries return house grey-structure
          rates (Rs.2,800/sft) instead of item rates (Rs.315/sft brick work).
          Site targets narrow results to Pakistan construction suppliers.
    Pure function — no I/O.
    """
    unit_label = {
        "sft": "per square foot sft",
        "nos": "per unit piece number",
        "rft": "per running foot rft",
        "cft": "per cubic foot cft",
        "mtr": "per meter",
        "bags": "per bag",
        "kgs": "per kg kilogram",
        "points": "per point",
        "ls": "lump sum total",
        "job": "per job",
    }.get(unit, "per unit")

    site_targets = {
        "civil_id": (
            "site:niazibricks.com.pk OR site:priceindex.pk OR "
            "site:civilconstructionguide.com OR site:acco.com.pk"
        ),
        "hvac": "site:mega.pk OR site:aysonline.pk OR site:pakfan.com.pk",
        "electrical_elv": "site:priceindex.pk OR site:hamariweb.com OR site:rozee.pk",
        "plumbing": "site:priceindex.pk OR site:civilconstructionguide.com",
        "fire_fighting": "fire fighting equipment price Pakistan PKR",
        "special_works": "site:priceindex.pk OR site:civilconstructionguide.com",
    }

    rate_context = {
        "civil_id": "contractor labor rate masonry finishing",
        "hvac": "AC unit price installation PKR",
        "electrical_elv": "electrical contractor wiring rate PKR",
        "plumbing": "plumber pipe fitting rate PKR",
        "fire_fighting": "fire safety equipment price PKR",
        "special_works": "woodwork interior contractor rate PKR",
    }.get(category, "contractor rate PKR")

    sites = site_targets.get(category, "")
    current_year = datetime.date.today().year

    return (
        f"{keywords} rate {unit_label} "
        f"Pakistan Lahore {current_year} {current_year - 1} PKR "
        f"{rate_context} {sites}"
    )


# ── Pure helper: assemble extraction prompt ────────────────────────────────

def _build_extraction_prompt(
    keywords: str,
    unit: str,
    category: str,
    web_context: str,
) -> str:
    """
    WHY:  Separates static template from dynamic variables so the static
          portion benefits from Anthropic's prompt cache across calls.
    Pure function — returns filled prompt string.
    """
    current_year = datetime.date.today().year
    rate_bounds = bounds_hint(category, unit)
    bounds_instruction = (
        f"\nREALISTIC RANGE for {category}/{unit} in Pakistan: {rate_bounds}. "
        f"If the extracted rate is outside this range, set market_rate=null — "
        f"it likely means you extracted a wrong unit (per bag, per litre, per project)."
        if rate_bounds else ""
    )
    return _EXTRACTION_PROMPT_TEMPLATE.format(
        keywords=keywords,
        unit=unit,
        category=category,
        current_year=current_year,
        current_year_minus1=current_year - 1,
        bounds_instruction=bounds_instruction,
        web_context=web_context[:4000],
    )


# ── Helper: call LLM and return raw text response ─────────────────────────

def _call_llm_for_extraction(prompt: str) -> str:
    """
    WHY:  Isolated so the retry decorator (tenacity) and the parsing logic
          can each be changed without touching the other.
    Returns raw text from Claude or GPT-4o. Empty string on failure.
    """
    client = _get_llm()
    if isinstance(client, OpenAI):
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            max_tokens=500,
            messages=[
                {"role": "system", "content": MARKET_EXTRACT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    # Anthropic path
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=500,
        system=MARKET_EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    for block in response.content:
        if hasattr(block, "text") and block.text:
            return block.text
        if hasattr(block, "thinking") and block.thinking:
            return block.thinking
    return ""


# ── Helper: parse and validate JSON extraction response ───────────────────

def _parse_extraction_response(
    raw: str,
    category: str,
    unit: str,
) -> dict:
    """
    WHY:  JSON parsing is error-prone (markdown fences, partial responses).
          Isolated here so parse failures return a safe null-rate dict
          without crashing extract_rate_from_results().
    Returns dict with market_rate, rate_type, confidence, note, etc.
    Pure function — no LLM calls, no DB.
    """
    if not raw:
        return {**_EMPTY_EXTRACTION, "note": "Empty response from LLM"}

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("_parse_extraction_response: JSON decode failed: %s", exc)
        return {**_EMPTY_EXTRACTION, "note": f"JSON parse error: {exc}"}

    # Post-extraction sanity: reject rates outside realistic bounds
    extracted_rate = data.get("market_rate")
    if extracted_rate and not validate_rate(category, unit, float(extracted_rate)):
        logger.warning(
            "extract_rate: Rs.%.0f failed sanity bounds for %s/%s — setting null",
            extracted_rate, category, unit,
        )
        data["market_rate"] = None
        data["confidence"] = "low"
        data["note"] = (
            f"Extracted Rs.{extracted_rate:,.0f} is outside realistic bounds "
            f"for {category}/{unit}. Rate rejected."
        )

    return {
        "market_rate": data.get("market_rate"),
        "rate_type": data.get("rate_type", "unknown"),
        "rate_source_name": data.get("rate_source_name"),
        "rate_source_url": data.get("rate_source_url"),
        "raw_price_text": data.get("raw_price_text"),
        "source_year": data.get("source_year"),
        "confidence": data.get("confidence", "low"),
        "note": data.get("note"),
    }


# ── Public: extract rate from pre-built web context ───────────────────────

def extract_rate_from_results(
    web_context: str,
    keywords: str,
    unit: str,
    category: str,
) -> dict:
    """
    INPUT:  web_context — concatenated Tavily results text (max ~4000 chars)
            keywords, unit, category — for prompt context + bounds validation
    OUTPUT: dict with market_rate, rate_type, rate_source_name, rate_source_url,
            raw_price_text, confidence, note. All values may be None.

    EXAMPLE:
        >>> extract_rate_from_results("Niazi Bricks PKR 13000-16000 per 1000...",
                                      "brick wall", "sft", "civil_id")
        {"market_rate": 290.0, "rate_type": "material_only", "confidence": "medium", ...}
    """
    try:
        prompt = _build_extraction_prompt(keywords, unit, category, web_context)
        raw = _call_llm_for_extraction(prompt)
        return _parse_extraction_response(raw, category, unit)
    except Exception as exc:
        logger.error("extract_rate_from_results failed: %s", exc)
        return {**_EMPTY_EXTRACTION, "note": f"Extraction error: {exc}"}


# ── Tavily search with retry ───────────────────────────────────────────────

@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _call_tavily(query: str) -> dict:
    """
    WHY:  Tavily occasionally returns 429 or drops connection. 3 retries with
          exponential backoff recover transient failures silently.
    NOTE: reraise=True means after 3 failures the error propagates to
          _run_tavily_and_extract() which returns a null-rate MarketRate.
    """
    return _get_tavily().search(
        query=query, max_results=5, search_depth="advanced", include_answer=True
    )


# ── Helper: one Tavily round-trip + extraction ─────────────────────────────

def _run_tavily_and_extract(
    query: str,
    keywords: str,
    unit: str,
    category: str,
) -> MarketRate:
    """
    WHY:  Encapsulates one search + extraction attempt so search_market_rate()
          can try multiple query formulations without repeating boilerplate.
    READS:  Tavily API, LLM API
    WRITES: returns MarketRate (all None fields if nothing found)
    """
    try:
        resp = _call_tavily(query)
        results = resp.get("results", [])
        answer = resp.get("answer", "")

        if not results and not answer:
            return _make_null_market_rate("No search results found.")

        # Context pruning: Tavily's answer field is already the distilled summary.
        # Pass answer + top-2 results at 300 chars each (≈400 tokens total).
        # NOTE: Was 5×600 chars (~1,000 tokens). Rate info appears in first sentence,
        # not buried 600 chars in — accuracy unchanged with 60% fewer tokens.
        web_context = f"SEARCH SUMMARY:\n{answer}\n\n" if answer else ""
        for r in results[:2]:
            web_context += (
                f"Source: {r.get('title', '')}\n"
                f"URL: {r.get('url', '')}\n"
                f"Text: {r.get('content', '')[:300]}\n\n"
            )

        extracted = extract_rate_from_results(web_context, keywords, unit, category)

        raw_rate = extracted.get("market_rate")
        rate_type = extracted.get("rate_type", "unknown")
        source_url = extracted.get("rate_source_url") or (results[0].get("url") if results else None)

        contractor_rate: float | None = None
        if raw_rate is not None:
            contractor_rate = (
                round(raw_rate * settings.CONTRACTOR_MARKUP, 0)
                if rate_type == "material_only"
                else raw_rate
            )

        return MarketRate(
            raw_rate=raw_rate,
            contractor_rate=contractor_rate,
            rate_type=rate_type,
            source_name=extracted.get("rate_source_name"),
            source_url=source_url,
            raw_price_text=extracted.get("raw_price_text"),
            source_year=extracted.get("source_year"),
            confidence=extracted.get("confidence", "low"),
            note=extracted.get("note"),
        )

    except Exception as exc:
        logger.error("Tavily search failed: %s", exc)
        return _make_null_market_rate(f"Search error: {exc}")


def _make_null_market_rate(note: str) -> MarketRate:
    """
    WHY:  Repeated null-MarketRate construction across fallback paths.
          Centralised so the shape is always consistent.
    Pure function.
    """
    return MarketRate(
        raw_rate=None,
        contractor_rate=None,
        rate_type="unknown",
        source_name=None,
        source_url=None,
        raw_price_text=None,
        confidence="low",
        note=note,
    )


# ── Public: search market rate with cache ─────────────────────────────────

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

    EXAMPLE:
        >>> search_market_rate("brick wall 4.5 inch", "sft", "civil_id")
        MarketRate(contractor_rate=480.0, rate_type="material_only",
                   source_name="Niazi Bricks", confidence="medium")
    """
    key = _cache_key(keywords, unit, category)
    if key in _market_cache:
        logger.info("Market cache hit: '%s'", key)
        return _market_cache[key]

    # Attempt 1: targeted query with site restrictions
    result = _run_tavily_and_extract(build_query(keywords, unit, category), keywords, unit, category)
    if result.contractor_rate is not None:
        _market_cache[key] = result
        return result

    # Attempt 2: broad query without site restrictions
    query2 = f"{keywords} price rate PKR Pakistan 2025 Lahore contractor supply install"
    result = _run_tavily_and_extract(query2, keywords, unit, category)
    if result.contractor_rate is not None:
        _market_cache[key] = result
        return result

    # Attempt 3: community/forum sources as last resort
    query3 = (
        f"{keywords} rate per {unit} Pakistan PKR "
        f"site:quora.com OR site:reddit.com OR site:propakistani.pk"
    )
    result = _run_tavily_and_extract(query3, keywords, unit, category)

    # NOTE: Cache even null results to avoid re-querying categories with no web data
    _market_cache[key] = result
    return result


if __name__ == "__main__":
    tests = [
        ("brick wall 4.5 inch first class", "sft", "civil_id"),
        ("gypsum board false ceiling 12mm", "sft", "civil_id"),
        ("split AC 4 ton installation", "nos", "hvac"),
        ("light circuit wiring 2.5mm PVC", "nos", "electrical_elv"),
    ]
    for kw, u, cat in tests:
        r = search_market_rate(kw, u, cat)
        print(
            f"{kw[:35]:35s} | {str(r.contractor_rate):10s} | "
            f"{r.rate_type:15s} | {r.source_name} | {r.note}"
        )
