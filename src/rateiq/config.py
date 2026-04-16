"""
WHY:  Single source of truth for every tunable value in the system.
      No hardcoded numbers anywhere else — if a threshold feels wrong,
      change it here and the whole pipeline picks it up immediately.
WHAT: Settings dataclass (paths, models, search tuning, circuit breaker,
      Langfuse toggle, pipeline concurrency) + module-level API key reads from .env.
FITS INTO: Imported by every rateiq module. Depends only on stdlib + dotenv.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    """
    Central configuration for the RateIQ system.
    All paths, model names, thresholds, and connection strings live here.
    """

    # ── Paths ──────────────────────────────────────────────────────────────
    RAW_DIR: Path = field(default_factory=lambda: Path("data/raw"))
    PROCESSED_DIR: Path = field(default_factory=lambda: Path("data/processed"))
    CHUNKS_CSV: Path = field(
        default_factory=lambda: Path("data/processed/boq_chunks.csv")
    )
    CHECKPOINT: Path = field(
        default_factory=lambda: Path("data/processed/ingestion_checkpoint.json")
    )

    # ── Qdrant ─────────────────────────────────────────────────────────────
    QDRANT_URL: str = "http://localhost:6333"
    COLLECTION_NAME: str = "boq_rates"
    EMBEDDING_MODEL: str = "text-embedding-3-large"
    EMBEDDING_DIMS: int = 3072

    # ── PostgreSQL ─────────────────────────────────────────────────────────
    POSTGRES_URL: str = "postgresql://rateiq:rateiq123@localhost:5432/rateiq"

    # ── LangGraph / LLM ────────────────────────────────────────────────────
    ANTHROPIC_MODEL: str = "claude-sonnet-4-5"                    # market extraction (complex reasoning)
    ANTHROPIC_MODEL_LIGHT: str = "claude-haiku-4-5-20251001"      # classify + output + QA probe (simple tasks)
    OPENAI_MODEL: str = "gpt-4o"
    LLM_MAX_TOKENS: int = 1000

    # ── Search tuning ──────────────────────────────────────────────────────
    BM25_TOP_K: int = 20
    DENSE_TOP_K: int = 20
    RRF_K: int = 60
    RERANK_TOP_K: int = 10
    FINAL_TOP_K: int = 5
    RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    RERANKER_THRESHOLD: float = 3.0

    # ── CRAG (Corrective RAG) thresholds ──────────────────────────────────
    # top reranker_score >= CORRECT  → "Correct"   branch: use historical confidently
    # top reranker_score  < INCORRECT → "Incorrect" branch: skip historical, web-only
    # between the two                → "Ambiguous" branch: blend historical + market
    RETRIEVAL_CORRECT_THRESHOLD: float = 3.0
    RETRIEVAL_INCORRECT_THRESHOLD: float = 0.5

    # ── Gap detection thresholds ───────────────────────────────────────────
    GAP_CONFIRMED_PCT: float = 15.0
    GAP_MINOR_PCT: float = 35.0

    # ── Circuit breaker thresholds ───────────────────────────────────────
    CIRCUIT_BREAKER_ERROR_THRESHOLD: int = 5
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT: float = 30.0
    CIRCUIT_BREAKER_SUCCESS_THRESHOLD: int = 3

    # ── Observability ────────────────────────────────────────────────────────────
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""

    # ── Pipeline concurrency ──────────────────────────────────────────────
    PIPELINE_MAX_WORKERS: int = 2       # threads for parallel row processing
    PIPELINE_SEMAPHORE_LIMIT: int = 2   # concurrent agent.run() calls (LLM rate limit)

    # ── Market search ──────────────────────────────────────────────────────
    CONTRACTOR_MARKUP: float = 1.5  # multiply material-only price

    # ── Redis ──────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_MARKET_TTL: int = 604800  # 7 days in seconds


# ── Singleton ──────────────────────────────────────────────────────────────
settings = Settings()

# Override from environment where available
if os.getenv("QDRANT_URL"):
    settings.QDRANT_URL = os.getenv("QDRANT_URL")
if os.getenv("POSTGRES_URL"):
    settings.POSTGRES_URL = os.getenv("POSTGRES_URL")
if os.getenv("REDIS_URL"):
    settings.REDIS_URL = os.getenv("REDIS_URL")

# ── API Keys (loaded from .env) ────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
USE_OPENAI_FALLBACK: bool = os.getenv("USE_OPENAI_FALLBACK", "true").lower() == "true"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Settings loaded: %s", settings)
    logger.info("OPENAI_API_KEY set: %s", bool(OPENAI_API_KEY))
    logger.info("ANTHROPIC_API_KEY set: %s", bool(ANTHROPIC_API_KEY))
    logger.info("TAVILY_API_KEY set: %s", bool(TAVILY_API_KEY))
    logger.info("CHUNKS_CSV exists: %s", settings.CHUNKS_CSV.exists())
