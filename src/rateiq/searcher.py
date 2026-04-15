"""
RateIQ Searcher Module
Hybrid search — BM25 + Dense Vectors + RRF + Reranking.
The core retrieval engine.
"""

import logging
import re

import numpy as np
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder

from .config import OPENAI_API_KEY, settings
from .models import BOQChunk, SearchResult

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "and",
    "or",
    "the",
    "of",
    "in",
    "to",
    "for",
    "with",
    "as",
    "per",
    "at",
    "on",
    "is",
    "are",
    "be",
    "by",
    "an",
    "a",
    "all",
    "etc",
    "complete",
}


class BOQSearcher:
    """
    Main search class. Initialize once at app startup, reuse for all queries.
    Combines BM25 sparse retrieval with Qdrant dense vector search,
    fuses results via RRF, then reranks with a cross-encoder.
    """

    def __init__(self, chunks: list[BOQChunk]) -> None:
        """
        INPUT:  list[BOQChunk] — all 1429 chunks loaded from CSV
        OUTPUT: initialized BOQSearcher with BM25 index
                and cross-encoder loaded in memory
        Builds BM25 index and loads reranker on init.

        EXAMPLE:
            >>> input:  load_all_chunks(settings.CHUNKS_CSV)
            >>> output: BOQSearcher ready, BM25 index built, reranker loaded
        """
        self.chunks = chunks
        self._chunk_map: dict[str, BOQChunk] = {c.chunk_id: c for c in chunks}

        logger.info("Building BM25 index over %d chunks...", len(chunks))
        corpus = [c.embedding_text for c in chunks]
        self._tokenized_corpus = [self._tokenize(t) for t in corpus]
        self._bm25 = BM25Okapi(self._tokenized_corpus)

        logger.info("Loading cross-encoder: %s", settings.RERANKER_MODEL)
        self._reranker = CrossEncoder(settings.RERANKER_MODEL, max_length=512)

        self._qdrant = QdrantClient(url=settings.QDRANT_URL)
        self._openai = OpenAI(api_key=OPENAI_API_KEY)
        logger.info("BOQSearcher ready.")

    def _tokenize(self, text: str) -> list[str]:
        """
        INPUT:  string — any BOQ text
        OUTPUT: list[str] — tokens with measurements preserved
                e.g. "4.5inch", "2.5mm", "4.0tr", "14swg"

        EXAMPLE:
            >>> input:  "Brick Wall 4.5\" thick in 1:4 mortar"
            >>> output: ["brick", "wall", "4.5inch", "thick", "1:4", "mortar"]
        """
        try:
            if not text or (isinstance(text, float) and np.isnan(text)):
                return []
            text = str(text).lower()
            # Preserve measurements
            text = re.sub(r'(\d+\.?\d*)\s*"', r"\1inch ", text)
            text = re.sub(r"(\d+\.?\d*)\s*mm\b", r"\1mm ", text)
            text = re.sub(r"(\d+\.?\d*)\s*tr\b", r"\1tr ", text)
            text = re.sub(r"(\d+)\s*swg\b", r"\1swg ", text)
            tokens = re.findall(r"[a-z0-9][a-z0-9.\:\-\/]*", text)
            return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]
        except Exception as exc:
            logger.error("_tokenize failed: %s", exc)
            return []

    def _bm25_search(
        self,
        query: str,
        top_k: int,
        work_category: str | None,
    ) -> list[tuple[BOQChunk, float, int]]:
        """
        INPUT:  query string — search query text
                top_k int — how many results to return
                work_category — optional filter string or None
        OUTPUT: list of tuples: (BOQChunk, bm25_score, rank_position)
                sorted by score descending

        EXAMPLE:
            >>> input:  "brick work 4.5 inch", top_k=20, work_category="civil_id"
            >>> output: [(BOQChunk(...), 4.21, 0), (BOQChunk(...), 3.87, 1), ...]
        """
        try:
            tokens = self._tokenize(query)
            scores = self._bm25.get_scores(tokens)
            top_indices = np.argsort(scores)[::-1]

            results: list[tuple[BOQChunk, float, int]] = []
            rank = 0
            for idx in top_indices:
                if len(results) >= top_k:
                    break
                chunk = self.chunks[idx]
                if work_category and chunk.work_category != work_category:
                    continue
                results.append((chunk, float(scores[idx]), rank))
                rank += 1

            return results
        except Exception as exc:
            logger.error("_bm25_search failed: %s", exc)
            return []

    def _dense_search(
        self,
        query: str,
        top_k: int,
        work_category: str | None,
    ) -> list[tuple[BOQChunk, float, int]]:
        """
        INPUT:  query string — will be embedded via OpenAI
                top_k int — how many results
                work_category — optional Qdrant filter or None
        OUTPUT: list of tuples: (BOQChunk, cosine_score, rank_position)
                sorted by score descending

        EXAMPLE:
            >>> input:  "gypsum ceiling board 12mm", top_k=20, work_category=None
            >>> output: [(BOQChunk(...), 0.891, 0), (BOQChunk(...), 0.867, 1), ...]
        """
        try:
            response = self._openai.embeddings.create(
                input=[query],
                model=settings.EMBEDDING_MODEL,
                dimensions=settings.EMBEDDING_DIMS,
            )
            if not response.data:
                return []
            query_vec = response.data[0].embedding

            qdrant_filter = None
            if work_category:
                qdrant_filter = Filter(
                    must=[
                        FieldCondition(
                            key="work_category",
                            match=MatchValue(value=work_category),
                        )
                    ]
                )

            hits = self._qdrant.query_points(
                collection_name=settings.COLLECTION_NAME,
                query=query_vec,
                limit=top_k,
                with_payload=True,
                query_filter=qdrant_filter,
            ).points

            results: list[tuple[BOQChunk, float, int]] = []
            for rank, hit in enumerate(hits):
                chunk_id = hit.payload.get("chunk_id", "")
                chunk = self._chunk_map.get(chunk_id)
                if chunk is None:
                    # Reconstruct from payload
                    chunk = BOQChunk(
                        chunk_id=chunk_id,
                        description_short=hit.payload.get("description_short", ""),
                        description_full=hit.payload.get("description_full", ""),
                        section_title=hit.payload.get("section_title", ""),
                        work_category=hit.payload.get("work_category", ""),
                        rate=float(hit.payload.get("rate", 0.0)),
                        unit_norm=hit.payload.get("unit_norm", "unit"),
                        qty=hit.payload.get("qty", ""),
                        source_file=hit.payload.get("source_file", ""),
                        sheet_name=hit.payload.get("sheet_name", ""),
                        rate_per_unit_label=hit.payload.get("rate_per_unit_label", ""),
                        embedding_text="",
                    )
                results.append((chunk, float(hit.score), rank))

            return results
        except Exception as exc:
            logger.error("_dense_search failed: %s", exc)
            return []

    def _rrf_fuse(
        self,
        bm25_results: list[tuple[BOQChunk, float, int]],
        dense_results: list[tuple[BOQChunk, float, int]],
        k: int = settings.RRF_K,
    ) -> list[tuple[BOQChunk, float]]:
        """
        INPUT:  two ranked lists from BM25 and dense search
                k int — RRF smoothing constant (default 60)
        OUTPUT: list of tuples: (BOQChunk, rrf_score)
                merged and sorted by combined RRF score descending

        EXAMPLE:
            >>> input:  bm25_results=[(chunk_a, 4.2, 0), ...],
                        dense_results=[(chunk_a, 0.89, 0), ...]
            >>> output: [(chunk_a, 0.0328, ...), ...]  # chunk_a fused to top
        """
        try:
            scores: dict[str, float] = {}
            chunk_map: dict[str, BOQChunk] = {}

            for rank_offset, result_list in enumerate([bm25_results, dense_results]):
                for chunk, _score, rank in result_list:
                    cid = chunk.chunk_id
                    scores[cid] = scores.get(cid, 0.0) + 1.0 / (rank + k)
                    chunk_map[cid] = chunk

            fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            return [(chunk_map[cid], score) for cid, score in fused]
        except Exception as exc:
            logger.error("_rrf_fuse failed: %s", exc)
            return []

    def _rerank(
        self,
        query: str,
        candidates: list[BOQChunk],
        top_k: int,
    ) -> list[tuple[BOQChunk, float]]:
        """
        INPUT:  query string — original search query
                candidates list[BOQChunk] — items to rerank (max ~20)
                top_k int — how many to return after reranking
        OUTPUT: list of tuples: (BOQChunk, reranker_score)
                sorted by cross-encoder score descending

        EXAMPLE:
            >>> input:  "brick wall 4.5 inch", [BOQChunk(...), ...], top_k=5
            >>> output: [(BOQChunk(description_short="BRICKWORK 4 1/2\""), 6.819), ...]
        """
        try:
            if not candidates:
                return []
            pairs = [
                [query, (c.description_full or c.description_short)[:512]]
                for c in candidates
            ]
            scores = self._reranker.predict(pairs)
            ranked = sorted(
                zip(candidates, scores.tolist()),
                key=lambda x: x[1],
                reverse=True,
            )
            return [(chunk, float(score)) for chunk, score in ranked[:top_k]]
        except Exception as exc:
            logger.error("_rerank failed: %s", exc)
            return [(c, 0.0) for c in candidates[:top_k]]

    def search(
        self,
        query: str,
        top_k: int = settings.FINAL_TOP_K,
        work_category: str | None = None,
    ) -> list[SearchResult]:
        """
        INPUT:  query string — description text to search for
                top_k int — number of final results (default 5)
                work_category string or None — filter by category
        OUTPUT: list[SearchResult] — top results with all scores
                ranked by smart_rank logic (reranker if confident,
                RRF fallback if reranker score < threshold)

        EXAMPLE:
            >>> input:  "brick wall 4.5 inch thick cement mortar"
            >>> output: [SearchResult(chunk=BOQChunk(description_short=
                        "Brick Work 4.5 Thick", rate=315.0, ...),
                        reranker_score=6.819, ranking_method="reranker")]
        """
        try:
            bm25_results = self._bm25_search(query, settings.BM25_TOP_K, work_category)
            dense_results = self._dense_search(
                query, settings.DENSE_TOP_K, work_category
            )

            fused = self._rrf_fuse(bm25_results, dense_results)

            # Build lookup maps for rank positions
            bm25_rank_map: dict[str, int] = {c.chunk_id: r for c, _, r in bm25_results}
            dense_rank_map: dict[str, int] = {
                c.chunk_id: r for c, _, r in dense_results
            }
            rrf_score_map: dict[str, float] = {c.chunk_id: s for c, s in fused}

            candidates = [chunk for chunk, _ in fused[: settings.RERANK_TOP_K]]

            reranked = self._rerank(query, candidates, top_k)
            top_reranker_score = reranked[0][1] if reranked else 0.0

            if top_reranker_score >= settings.RERANKER_THRESHOLD:
                ranking_method = "reranker"
                final_pairs = reranked
            else:
                ranking_method = "rrf_fallback"
                final_pairs = [(chunk, score) for chunk, score in reranked]
                # Re-sort by RRF score
                final_pairs = sorted(
                    [(c, rrf_score_map.get(c.chunk_id, 0.0)) for c, _ in reranked],
                    key=lambda x: x[1],
                    reverse=True,
                )[:top_k]

            results: list[SearchResult] = []
            for chunk, reranker_score in reranked[:top_k]:
                result = SearchResult(
                    chunk=chunk,
                    rrf_score=rrf_score_map.get(chunk.chunk_id, 0.0),
                    reranker_score=reranker_score,
                    bm25_rank=bm25_rank_map.get(chunk.chunk_id, 999),
                    dense_rank=dense_rank_map.get(chunk.chunk_id, 999),
                    ranking_method=ranking_method,
                )
                results.append(result)

            logger.info(
                "search('%s'): %d results, method=%s, top_reranker=%.2f",
                query[:50],
                len(results),
                ranking_method,
                top_reranker_score,
            )
            return results

        except Exception as exc:
            logger.error("search failed: %s", exc)
            return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from .config import settings
    from .parser import load_all_chunks

    chunks = load_all_chunks(settings.CHUNKS_CSV)
    s = BOQSearcher(chunks)
    results = s.search("brick wall 4.5 inch", top_k=3)
    for r in results:
        print(
            f"{r.chunk.description_short[:40]} | "
            f"{r.chunk.rate_per_unit_label} | "
            f"reranker={r.reranker_score:.3f}"
        )
