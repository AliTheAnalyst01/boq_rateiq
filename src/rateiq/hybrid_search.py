"""
RateIQ Hybrid Search Module
Combines BM25 + Dense Vector Search + RRF + Reranking.
Import this in the LangGraph agent.
"""

import re
import numpy as np
import pandas as pd
from pathlib import Path
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from openai import OpenAI


CHUNKS_PATH = Path(__file__).parent.parent.parent / "data/processed/boq_chunks.csv"
COLLECTION_NAME = "boq_rates"


def tokenize_boq(text: str) -> list:
    if pd.isna(text):
        return []
    text = str(text).lower()
    text = re.sub(r"(\d+\.?\d*)\s*\"", r"\1inch ", text)
    text = re.sub(r"(\d+\.?\d*)\s*mm", r"\1mm ", text)
    text = re.sub(r"(\d+\.?\d*)\s*tr\b", r"\1tr ", text)
    text = re.sub(r"(\d+)\s*swg\b", r"\1swg ", text)
    tokens = re.findall(r"[a-z0-9][a-z0-9\.\:\-\/]*", text)
    stopwords = {
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
    return [t for t in tokens if t not in stopwords and len(t) > 1]


def reciprocal_rank_fusion(ranked_lists: list, k: int = 60) -> list:
    scores = {}
    payloads = {}
    for ranked_list in ranked_lists:
        for rank, item in enumerate(ranked_list):
            cid = item["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (rank + k)
            payloads[cid] = item
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [{**payloads[cid], "rrf_score": scores[cid]} for cid in sorted_ids]


class BOQSearcher:
    """
    Main search class. Instantiate once, reuse for all queries.
    """

    def __init__(self):
        print("Loading BOQ chunks...")
        self.df = pd.read_csv(CHUNKS_PATH)

        print("Building BM25 index...")
        corpus = self.df["embedding_text"].fillna("").tolist()
        self.tokenized_corpus = [tokenize_boq(t) for t in corpus]
        self.bm25 = BM25Okapi(self.tokenized_corpus)

        print("Loading cross-encoder...")
        self.reranker = CrossEncoder(
            "cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=512
        )

        self.qdrant = QdrantClient(url="http://localhost:6333")
        self.openai = OpenAI()
        print("BOQSearcher ready.")

    def _embed(self, query: str) -> list:
        """Embed a query text."""
        response = self.openai.embeddings.create(
            input=[query], model="text-embedding-3-large", dimensions=3072
        )
        if response.data:
            return response.data[0].embedding
        return []

    def _hybrid(
        self,
        query: str,
        top_k_retrieve: int = 20,
        top_k_fused: int = 10,
        work_category: str = None,
    ) -> list:
        """BM25 + dense + RRF."""
        # Embed
        query_vec = self._embed(query)

        # BM25
        tokens = tokenize_boq(query)
        bm25_scores = self.bm25.get_scores(tokens)
        bm25_top = np.argsort(bm25_scores)[::-1][:top_k_retrieve]
        bm25_results = []
        for rank, idx in enumerate(bm25_top):
            row = self.df.iloc[idx]
            if work_category and row["work_category"] != work_category:
                continue
            bm25_results.append(
                {
                    "chunk_id": str(row["chunk_id"]),
                    "description_short": str(row["description_short"]),
                    "description_full": str(row["description_full"]),
                    "work_category": str(row["work_category"]),
                    "rate": float(row["rate"]),
                    "unit_norm": str(row["unit_norm"]),
                    "source_file": str(row["source_file"]),
                    "rate_per_unit_label": str(row["rate_per_unit_label"]),
                    "bm25_score": float(bm25_scores[idx]),
                    "bm25_rank": rank,
                }
            )

        # Dense
        qdrant_filter = None
        if work_category:
            qdrant_filter = Filter(
                must=[
                    FieldCondition(
                        key="work_category", match=MatchValue(value=work_category)
                    )
                ]
            )
        dense_hits = self.qdrant.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vec,
            limit=top_k_retrieve,
            with_payload=True,
            query_filter=qdrant_filter,
        )
        dense_results = [
            {
                "chunk_id": h.payload["chunk_id"],
                "description_short": h.payload["description_short"],
                "description_full": h.payload["description_full"],
                "work_category": h.payload["work_category"],
                "rate": float(h.payload["rate"]),
                "unit_norm": h.payload["unit_norm"],
                "source_file": h.payload["source_file"],
                "rate_per_unit_label": h.payload["rate_per_unit_label"],
                "dense_score": float(h.score),
                "dense_rank": rank,
            }
            for rank, h in enumerate(dense_hits)
        ]

        # RRF
        fused = reciprocal_rank_fusion([bm25_results, dense_results])
        return fused[:top_k_fused]

    def _rerank(self, query: str, candidates: list, top_k: int = 5) -> list:
        """Cross-encoder rerank."""
        if not candidates:
            return []
        pairs = [
            [query, str(c.get("description_full", c["description_short"]))[:512]]
            for c in candidates
        ]
        scores = self.reranker.predict(pairs)
        for i, c in enumerate(candidates):
            c["reranker_score"] = float(scores[i])
        return sorted(candidates, key=lambda x: x["reranker_score"], reverse=True)[
            :top_k
        ]

    def search(self, query: str, top_k: int = 5, work_category: str = None) -> list:
        """
        Full pipeline: hybrid retrieval + reranking.
        Returns list of dicts with rate, source, scores.
        """
        candidates = self._hybrid(query, 20, 10, work_category)
        return self._rerank(query, candidates, top_k)

    def smart_search(
        self,
        query: str,
        top_k: int = 5,
        work_category: str = None,
        reranker_threshold: float = 3.0,
    ) -> list[dict]:
        """
        Search with reranker threshold fallback.
        Uses reranker order if confident (score > threshold),
        else falls back to RRF order.
        This is the method the LangGraph agent calls.
        """
        candidates = self._hybrid(query, 20, 10, work_category)
        if not candidates:
            return []

        reranked = self._rerank(query, candidates, top_k)
        if not reranked:
            return []

        top_score = reranked[0]["reranker_score"]

        if top_score >= reranker_threshold:
            results = reranked
            order = "reranker"
        else:
            results = sorted(candidates, key=lambda x: x["rrf_score"], reverse=True)[
                :top_k
            ]
            for r in results:
                r.setdefault("reranker_score", 0.0)
            order = "rrf_fallback"

        for i, r in enumerate(results):
            r["final_rank"] = i + 1
            r["ranking_method"] = order

        return results
