"""
WHY:  PostgreSQL is the fast-lookup store for aggregated historical rates.
      Qdrant holds full chunk text for semantic search; Postgres holds
      structured rows (description, rate, unit) so gap_detector can
      filter by keyword + unit in <10ms without re-scanning the vector store.
WHAT: rate_lookup()        — raw matching rows for a keyword + optional unit.
      rate_stats()         — aggregated stats (avg, min, max, count) for a keyword.
      cross_project_rates() — rates for the same item across all BOQ files.
      insert_boq_item()    — write a new BOQ chunk to the DB during ingestion.
FITS INTO: Called by gap_detector.analyze(). Wraps SQLAlchemy + psycopg2 pool.
           Depends on config (POSTGRES_URL) and models (BOQChunk, RateStats).
"""

import logging
import statistics
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import settings
from .models import BOQChunk, RateStats

logger = logging.getLogger(__name__)

_engine: Engine | None = None


def get_engine() -> Engine:
    """
    INPUT:  nothing — reads POSTGRES_URL from settings
    OUTPUT: SQLAlchemy engine — singleton, created once on first call

    EXAMPLE:
        >>> input:  (none — uses settings.POSTGRES_URL)
        >>> output: Engine connected to postgresql://rateiq:...@localhost:5432/rateiq
    """
    global _engine
    if _engine is None:
        try:
            _engine = create_engine(
                settings.POSTGRES_URL,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
            )
            logger.info("PostgreSQL engine created: %s", settings.POSTGRES_URL)
        except Exception as exc:
            logger.error("get_engine failed: %s", exc)
            raise
    return _engine


def rate_lookup(
    description_fragment: str,
    unit: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    INPUT:  description_fragment string — keyword to search e.g. "brick work"
            unit string or None — filter by unit e.g. "sft"
            limit int — max rows to return (default 5)
    OUTPUT: list of dicts, each with keys:
            description_short, rate, unit_norm,
            source_file, rate_per_unit_label, work_category
            Returns empty list on failure or no matches.

    EXAMPLE:
        >>> input:  "brick work", unit="sft", limit=3
        >>> output: [{"description_short": "Brick Work 4.5 Thick",
                      "rate": 315.0, "source_file": "BOQ-01.xlsx", ...}]
    """
    try:
        cols = "description_short, rate, unit_norm, source_file, rate_per_unit_label, work_category"
        sql, params = _build_keyword_query(description_fragment, unit, select_cols=cols)
        sql += " ORDER BY rate ASC LIMIT :limit"
        params["limit"] = limit
        with get_engine().connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        return [dict(row._mapping) for row in rows]
    except Exception as exc:
        logger.error("rate_lookup failed for '%s': %s", description_fragment, exc)
        return []


def _build_keyword_query(
    fragment: str,
    unit: str | None,
    select_cols: str,
) -> tuple[str, dict[str, Any]]:
    """
    WHY:  rate_stats, rate_lookup, and cross_project_rates all share the same
          WHERE ILIKE + optional unit filter pattern. Centralised here so a
          SQL change only needs to happen in one place.
    Returns (sql_string, params_dict) — pure function, no I/O.
    """
    # LIMITATION: SQL uses ILIKE '%keyword%' which matches substrings.
    # "brick" matches "red brick", "brick work", "concrete block+brick" equally.
    # The 3-word/2-word keyword strategy in gap_detector reduces false matches
    # but cannot fully eliminate them. For high-value items, verify hist_count > 3.
    sql = f"SELECT {select_cols} FROM boq_items WHERE LOWER(description_short) LIKE :pattern"
    params: dict[str, Any] = {"pattern": f"%{fragment.lower()}%"}
    if unit:
        sql += " AND unit_norm = :unit"
        params["unit"] = unit
    return sql, params


def _compute_stats_from_rows(keyword: str, rows: list) -> RateStats | None:
    """
    WHY:  Isolates the statistics computation from DB execution so it can be
          unit-tested without a real database connection.
    Pure function — no DB access, no side effects.
    Returns RateStats or None if rows contain no valid rates.
    """
    rates = [float(row.rate) for row in rows if row.rate is not None]
    sources = list({row.source_file for row in rows if row.source_file})
    if not rates:
        return None
    return RateStats(
        item_keyword=keyword,
        count=len(rates),
        min_rate=min(rates),
        max_rate=max(rates),
        avg_rate=round(sum(rates) / len(rates), 2),
        median_rate=round(statistics.median(rates), 2),
        std_rate=round(statistics.stdev(rates) if len(rates) > 1 else 0.0, 2),
        source_files=sources,
        sample_rates=sorted(rates),
    )


def rate_stats(
    description_fragment: str,
    unit: str | None = None,
) -> RateStats | None:
    """
    INPUT:  description_fragment string — e.g. "brick work 4.5"
            unit string or None — filter by unit
    OUTPUT: RateStats dataclass with count/min/max/avg/median/sources.
            None if no matching items found.

    EXAMPLE:
        >>> input:  "gypsum", unit="sft"
        >>> output: RateStats(count=4, min_rate=375.0, max_rate=455.0,
                    avg_rate=423.0, source_files=["3.CEILING.xlsx", ...])
    """
    try:
        sql, params = _build_keyword_query(
            description_fragment, unit, select_cols="rate, source_file"
        )
        with get_engine().connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        if not rows:
            return None
        return _compute_stats_from_rows(description_fragment, rows)
    except Exception as exc:
        logger.error("rate_stats failed for '%s': %s", description_fragment, exc)
        return None


def cross_project_rates(
    description_fragment: str,
    unit: str | None = None,
) -> list[dict]:
    """
    INPUT:  description_fragment string — keyword to search
            unit string or None — filter by unit
    OUTPUT: list of dicts sorted by rate ascending
            each with: description_short, rate, unit_norm,
                       source_file, work_category
            Shows same item across different past tenders.
            Returns empty list on failure.

    EXAMPLE:
        >>> input:  "brick work 4.5", unit="sft"
        >>> output: [{"rate": 310.0, "source_file": "1.CIVIL.xlsx"},
                     {"rate": 315.0, "source_file": "BOQ-01.xlsx"},
                     {"rate": 320.0, "source_file": "ID Works.xlsx"}]
    """
    try:
        cols = "description_short, rate, unit_norm, source_file, work_category"
        sql, params = _build_keyword_query(description_fragment, unit, select_cols=cols)
        sql += " ORDER BY rate ASC"
        with get_engine().connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        return [dict(row._mapping) for row in rows]
    except Exception as exc:
        logger.error("cross_project_rates failed for '%s': %s", description_fragment, exc)
        return []


def insert_boq_item(chunk: BOQChunk) -> bool:
    """
    INPUT:  BOQChunk — one line item to insert
    OUTPUT: bool — True if inserted, False if duplicate (chunk_id exists)
            Returns False on any error.
    Used when new BOQ files are ingested.

    EXAMPLE:
        >>> input:  BOQChunk(chunk_id="abc123", description_short="Brick Work", ...)
        >>> output: True  (first insert) or False (if chunk_id already exists)
    """
    try:
        check_query = "SELECT 1 FROM boq_items WHERE chunk_id = :chunk_id"
        insert_query = """
            INSERT INTO boq_items (
                chunk_id, description_short, description_full, section_title,
                work_category, rate, unit_norm, qty, source_file,
                sheet_name, rate_per_unit_label, embedding_text
            ) VALUES (
                :chunk_id, :description_short, :description_full, :section_title,
                :work_category, :rate, :unit_norm, :qty, :source_file,
                :sheet_name, :rate_per_unit_label, :embedding_text
            )
        """

        params = {
            "chunk_id": chunk.chunk_id,
            "description_short": chunk.description_short,
            "description_full": chunk.description_full,
            "section_title": chunk.section_title,
            "work_category": chunk.work_category,
            "rate": chunk.rate,
            "unit_norm": chunk.unit_norm,
            "qty": chunk.qty,
            "source_file": chunk.source_file,
            "sheet_name": chunk.sheet_name,
            "rate_per_unit_label": chunk.rate_per_unit_label,
            "embedding_text": chunk.embedding_text,
        }

        with get_engine().connect() as conn:
            exists = conn.execute(
                text(check_query), {"chunk_id": chunk.chunk_id}
            ).fetchone()
            if exists:
                logger.debug("Chunk %s already exists — skipping.", chunk.chunk_id)
                return False

            conn.execute(text(insert_query), params)
            conn.commit()

        logger.debug("Inserted chunk %s.", chunk.chunk_id)
        return True

    except Exception as exc:
        logger.error("insert_boq_item failed for chunk %s: %s", chunk.chunk_id, exc)
        return False


def ensure_schema_and_seed(chunks_csv_path) -> int:
    """
    INPUT:  Path to boq_chunks.csv
    OUTPUT: int — number of rows inserted (0 if table already had data)

    Creates boq_items table if missing, then seeds from CSV if empty.
    Called once at startup — safe to call on every cold start because
    PostgreSQL uses ephemeral storage and loses data on container restart.
    """
    create_sql = """
        CREATE TABLE IF NOT EXISTS boq_items (
            id               SERIAL PRIMARY KEY,
            chunk_id         TEXT UNIQUE NOT NULL,
            description_short TEXT NOT NULL,
            description_full  TEXT,
            section_title     TEXT,
            work_category     TEXT,
            rate              NUMERIC,
            unit_norm         TEXT,
            qty               TEXT,
            source_file       TEXT,
            sheet_name        TEXT,
            rate_per_unit_label TEXT,
            embedding_text    TEXT
        )
    """
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text(create_sql))
            conn.commit()
            count = conn.execute(text("SELECT COUNT(*) FROM boq_items")).scalar()

        if count and count > 0:
            logger.info("boq_items already has %d rows — skipping seed.", count)
            return 0

        logger.info("boq_items is empty — seeding from %s ...", chunks_csv_path)
        import pandas as pd
        df = pd.read_csv(chunks_csv_path)
        inserted = 0
        with engine.connect() as conn:
            for _, row in df.iterrows():
                try:
                    conn.execute(text("""
                        INSERT INTO boq_items (
                            chunk_id, description_short, description_full,
                            section_title, work_category, rate, unit_norm,
                            qty, source_file, sheet_name,
                            rate_per_unit_label, embedding_text
                        ) VALUES (
                            :chunk_id, :description_short, :description_full,
                            :section_title, :work_category, :rate, :unit_norm,
                            :qty, :source_file, :sheet_name,
                            :rate_per_unit_label, :embedding_text
                        ) ON CONFLICT (chunk_id) DO NOTHING
                    """), {
                        "chunk_id": str(row.get("chunk_id", "")),
                        "description_short": str(row.get("description_short", "")),
                        "description_full": str(row.get("description_full", "")),
                        "section_title": str(row.get("section_title", "")),
                        "work_category": str(row.get("work_category", "")),
                        "rate": float(row["rate"]) if pd.notna(row.get("rate")) else None,
                        "unit_norm": str(row.get("unit_norm", "")),
                        "qty": str(row.get("qty", "")),
                        "source_file": str(row.get("source_file", "")),
                        "sheet_name": str(row.get("sheet_name", "")),
                        "rate_per_unit_label": str(row.get("rate_per_unit_label", "")),
                        "embedding_text": str(row.get("embedding_text", "")),
                    })
                    inserted += 1
                except Exception as row_exc:
                    logger.warning("Skipping row %s: %s", row.get("chunk_id"), row_exc)
            conn.commit()

        logger.info("Seeded %d rows into boq_items.", inserted)
        return inserted

    except Exception as exc:
        logger.error("ensure_schema_and_seed failed: %s", exc)
        return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Smoke test: rate lookup
    results = rate_lookup("brick", unit="sft", limit=3)
    logger.info("rate_lookup smoke test: %d results", len(results))
    for r in results:
        logger.info(
            "  %s | Rs.%.0f/%s", r["description_short"], r["rate"], r["unit_norm"]
        )

    # Smoke test: rate stats
    stats = rate_stats("brick", unit="sft")
    if stats:
        logger.info(
            "rate_stats: count=%d, avg=%.0f, min=%.0f, max=%.0f",
            stats.count,
            stats.avg_rate,
            stats.min_rate,
            stats.max_rate,
        )
    else:
        logger.warning("rate_stats returned None — check DB connection or data.")
