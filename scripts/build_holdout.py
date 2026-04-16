"""
WHY:  Creates the permanent 80/20 holdout split for eval harness.
      Removes holdout items from Qdrant + PostgreSQL so the agent
      cannot retrieve its own test data during evaluation.
RUN:  python scripts/build_holdout.py
      Run ONCE only — re-running corrupts the holdout set.
"""

import logging
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
