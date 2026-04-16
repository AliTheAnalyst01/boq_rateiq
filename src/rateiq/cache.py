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

__all__ = ["get_market_rate", "set_market_rate"]

# Sentinel: distinguishes "never tried" (None) from "tried and failed" (_UNAVAILABLE).
# Without this, every call retries the TCP connect when Redis is down — 100+ timeout
# waits per 50-row batch at 2s each = 200s overhead.
_UNAVAILABLE = object()
_redis_client = None  # None = not yet tried; _UNAVAILABLE = tried and failed


def _get_client():
    """
    Lazy singleton Redis client.
    Returns the connected client, or None if Redis is unavailable.
    On first connection failure, permanently disables Redis for this process
    (sets sentinel) so subsequent calls return immediately without retrying.
    """
    global _redis_client
    if _redis_client is _UNAVAILABLE:
        return None
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
        logger.warning("Redis unavailable, cache disabled for this session: %s", exc)
        _redis_client = _UNAVAILABLE
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
        logger.debug("Redis cache HIT: %s", cache_key)
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

