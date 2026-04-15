"""
RateIQ Embedder Module
All vector embedding logic.
Talks to OpenAI API and Qdrant.
"""

import json
import logging
from pathlib import Path

from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)

from .config import OPENAI_API_KEY, settings
from .models import BOQChunk
from .parser import load_all_chunks

logger = logging.getLogger(__name__)

_openai_client: OpenAI | None = None


def _get_openai() -> OpenAI:
    """Lazy singleton OpenAI client."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


def embed_texts(
    texts: list[str],
    model: str = settings.EMBEDDING_MODEL,
) -> list[list[float]]:
    """
    INPUT:  list of strings — texts to embed (max 100 per call recommended)
            model — OpenAI embedding model name (default text-embedding-3-large)
    OUTPUT: list of lists — each inner list is a 3072-dim float vector
            Returns empty list on failure.

    EXAMPLE:
        >>> input:  ["brick work 4.5 inch", "gypsum ceiling"]
        >>> output: [[0.023, -0.041, ...], [0.018, 0.034, ...]]
    """
    try:
        client = _get_openai()
        response = client.embeddings.create(
            input=texts,
            model=model,
            dimensions=settings.EMBEDDING_DIMS,
        )
        return [item.embedding for item in response.data]
    except Exception as exc:
        logger.error("embed_texts failed: %s", exc)
        return []


def embed_single(text: str) -> list[float]:
    """
    INPUT:  string — one query text to embed
    OUTPUT: list[float] — one 3072-dim vector
            Returns empty list on failure.

    EXAMPLE:
        >>> input:  "providing and laying brick wall"
        >>> output: [0.023, -0.041, 0.017, ...]  (3072 values)
    """
    try:
        results = embed_texts([text])
        return results[0] if results else []
    except Exception as exc:
        logger.error("embed_single failed: %s", exc)
        return []


def create_qdrant_collection(
    client: QdrantClient,
    collection_name: str,
    dims: int,
) -> bool:
    """
    INPUT:  QdrantClient — initialized Qdrant client
            collection_name string — name of the collection
            dims int — vector dimension (3072 for text-embedding-3-large)
    OUTPUT: bool — True if created fresh, False if already exists

    EXAMPLE:
        >>> input:  client, "boq_rates", 3072
        >>> output: True  (first time) or False (already exists)
    """
    try:
        existing = [c.name for c in client.get_collections().collections]
        if collection_name in existing:
            logger.info("Collection '%s' already exists — skipping creation.", collection_name)
            return False

        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=dims, distance=Distance.COSINE),
        )
        logger.info("Created collection '%s' with %d dims.", collection_name, dims)
        return True
    except Exception as exc:
        logger.error("create_qdrant_collection failed: %s", exc)
        return False


def upsert_chunks(
    client: QdrantClient,
    collection_name: str,
    chunks: list[BOQChunk],
    vectors: list[list[float]],
) -> int:
    """
    INPUT:  QdrantClient — initialized Qdrant client
            collection_name string — target collection
            list[BOQChunk] — chunks to store
            list of vectors — one per chunk, same order
    OUTPUT: int — number of points successfully upserted
            Returns 0 on failure.

    EXAMPLE:
        >>> input:  client, "boq_rates", [BOQChunk(...)], [[0.023, ...]]
        >>> output: 1
    """
    try:
        if len(chunks) != len(vectors):
            logger.error(
                "upsert_chunks: chunks (%d) and vectors (%d) length mismatch.",
                len(chunks),
                len(vectors),
            )
            return 0

        points: list[PointStruct] = []
        for chunk, vector in zip(chunks, vectors):
            # Use first 8 hex chars of chunk_id as int ID for Qdrant
            point_id = abs(hash(chunk.chunk_id)) % (2**63)
            payload = {
                "chunk_id": chunk.chunk_id,
                "description_short": chunk.description_short,
                "description_full": chunk.description_full[:1000],
                "section_title": chunk.section_title,
                "work_category": chunk.work_category,
                "rate": chunk.rate,
                "unit_norm": chunk.unit_norm,
                "qty": chunk.qty,
                "source_file": chunk.source_file,
                "sheet_name": chunk.sheet_name,
                "rate_per_unit_label": chunk.rate_per_unit_label,
            }
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))

        client.upsert(collection_name=collection_name, points=points)
        logger.info("Upserted %d points to '%s'.", len(points), collection_name)
        return len(points)

    except Exception as exc:
        logger.error("upsert_chunks failed: %s", exc)
        return 0


def ingest_all_files(
    chunks_csv: Path,
    checkpoint_path: Path,
) -> dict:
    """
    INPUT:  Path to boq_chunks.csv — source of all processed chunks
            Path to checkpoint JSON file — tracks which source files are done
    OUTPUT: dict with keys:
            "files_processed": int — new files ingested this run
            "total_upserted":  int — total points inserted
            "errors":          list[str] — any file-level errors
            "skipped":         int — files already in checkpoint

    Main entry point for full ingestion pipeline.
    Processes file by file with checkpoint saves.

    EXAMPLE:
        >>> input:  Path("data/processed/boq_chunks.csv"),
                    Path("data/processed/ingestion_checkpoint.json")
        >>> output: {"files_processed": 8, "total_upserted": 1429, "errors": [], "skipped": 0}
    """
    result = {
        "files_processed": 0,
        "total_upserted": 0,
        "errors": [],
        "skipped": 0,
    }

    try:
        # Load checkpoint
        checkpoint: dict = {}
        if checkpoint_path.exists():
            try:
                checkpoint = json.loads(checkpoint_path.read_text())
            except Exception as e:
                logger.warning("Could not read checkpoint: %s", e)

        # Load all chunks
        chunks = load_all_chunks(chunks_csv)
        if not chunks:
            result["errors"].append("No chunks loaded from CSV.")
            return result

        # Group by source_file
        from collections import defaultdict
        file_groups: dict[str, list[BOQChunk]] = defaultdict(list)
        for chunk in chunks:
            file_groups[chunk.source_file].append(chunk)

        # Initialize Qdrant
        qdrant_client = QdrantClient(url=settings.QDRANT_URL)
        create_qdrant_collection(qdrant_client, settings.COLLECTION_NAME, settings.EMBEDDING_DIMS)

        for source_file, file_chunks in file_groups.items():
            if source_file in checkpoint:
                logger.info("Skipping already-ingested: %s", source_file)
                result["skipped"] += 1
                continue

            logger.info("Ingesting %s (%d chunks)...", source_file, len(file_chunks))
            try:
                texts = [c.embedding_text for c in file_chunks]
                # Batch embed in groups of 50
                all_vectors: list[list[float]] = []
                batch_size = 50
                for i in range(0, len(texts), batch_size):
                    batch = texts[i: i + batch_size]
                    vecs = embed_texts(batch)
                    if not vecs:
                        raise RuntimeError(f"Embedding failed for batch {i}")
                    all_vectors.extend(vecs)

                upserted = upsert_chunks(
                    qdrant_client, settings.COLLECTION_NAME, file_chunks, all_vectors
                )
                result["files_processed"] += 1
                result["total_upserted"] += upserted

                # Save checkpoint
                checkpoint[source_file] = upserted
                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                checkpoint_path.write_text(json.dumps(checkpoint, indent=2))

            except Exception as file_exc:
                err = f"{source_file}: {file_exc}"
                logger.error("Ingestion failed for %s: %s", source_file, file_exc)
                result["errors"].append(err)

        return result

    except Exception as exc:
        logger.error("ingest_all_files fatal error: %s", exc)
        result["errors"].append(str(exc))
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from .config import settings

    # Smoke test: embed one text
    vec = embed_single("brick wall 4.5 inch cement mortar")
    logger.info("Smoke test embed_single: vector length = %d", len(vec))
    if vec:
        logger.info("First 5 dims: %s", vec[:5])
