"""
RateIQ Query Transformer Module
HyDE (Hypothetical Document Embeddings) + Multi-Query Expansion + Query Decomposition.

Fixes query-document mismatch — the #1 silent failure in RAG.
"""

import logging
import re
from typing import Any

from anthropic import Anthropic
from openai import OpenAI

from .config import ANTHROPIC_API_KEY, OPENAI_API_KEY, settings

logger = logging.getLogger(__name__)


_WORK_CATEGORY_SYNONYMS = {
    "civil_id": [
        "brick work",
        "brickwork",
        "masonry",
        " RCC",
        "concrete",
        "shuttering",
        "steel reinforcement",
        "foundation",
    ],
    "hvac": [
        "air conditioning",
        "AC",
        "split unit",
        "VRF",
        "chiller",
        "fan coil",
        "ductwork",
        "ventilation",
    ],
    "electrical_elv": [
        "wiring",
        "conduit",
        "MCB",
        "panel",
        "switch",
        "socket",
        "cable",
        "lighting",
    ],
    "finishing": [
        "plaster",
        "paint",
        "putty",
        "texture",
        "wall finish",
        "polish",
    ],
    "plumbing": [
        "pipe",
        "fitting",
        "sanitary",
        "toilet",
        "basin",
        "water supply",
        "drainage",
    ],
    "ceiling": [
        "gypsum",
        "false ceiling",
        "acoustic ceiling",
        "t-grid",
        "ceiling board",
        "POP ceiling",
    ],
    "flooring": [
        "tile",
        "marble",
        "granite",
        "vinyl",
        "carpet",
        "terrazzo",
        "screed",
    ],
    "carpentry": [
        "door",
        "window",
        "cabinet",
        "joinery",
        "veneered",
        "MDF",
        "plywood",
    ],
}


_UNIT_SYNONYMS = {
    "sft": ["sq ft", "sqft", "square foot", "square feet"],
    "rft": ["rf", "running foot", "running ft", "linear foot", "linear ft"],
    "cft": ["cu ft", "cubic foot", "cubic ft"],
    "mtr": ["meter", "metre", "m"],
    "nos": ["nos", "no", "numbers", "num", "pieces", "pcs"],
    "kg": ["kilogram", "kgs", "kilog"],
    "ton": ["tons", "tonne", "tonnes"],
    "ls": ["lump sum", "job", "set"],
}


class QueryTransformer:
    """
    Query transformation for better retrieval.
    HyDE + multi-query expansion + decomposition.
    """

    def __init__(self, llm_client: Any | None = None):
        """
        INPUT: llm_client — Anthropic or OpenAI client (optional, lazy init)
        """
        self._llm = llm_client
        self._is_openai = False

    def _get_llm(self) -> Any:
        """Lazy init LLM client."""
        if self._llm is None:
            if ANTHROPIC_API_KEY:
                try:
                    self._llm = Anthropic(api_key=ANTHROPIC_API_KEY)
                except Exception:
                    if OPENAI_API_KEY:
                        self._llm = OpenAI(api_key=OPENAI_API_KEY)
                        self._is_openai = True
            elif OPENAI_API_KEY:
                self._llm = OpenAI(api_key=OPENAI_API_KEY)
                self._is_openai = True
        return self._llm

    def hyde_generate(self, query: str, work_category: str = "") -> list[str]:
        """
        INPUT:  query string — user's description
                work_category — for context
        OUTPUT: list of hypothetical BOQ descriptions that would match

        HyDE generates "ideal" document descriptions that would be relevant
        to the query, then those get embedded for semantic search.

        EXAMPLE:
            >>> input:  "brick wall thick 4.5 inch", "civil_id"
            >>> output: ["BRICKWORK 4.5 INCH 1:4 CEMENT MORTAR",
                         "BRICK MASONRY 4.5 INCH THICK FIRST CLASS"]
        """
        try:
            prompt = f"""Generate 2-3 hypothetical BOQ descriptions that would match the user's query.
Each should be a realistic BOQ line item description with measurements.

Query: {query}
Category: {work_category or "unknown"}

Return a JSON array of strings, each a hypothetical BOQ description.
Keep descriptions concise (under 80 characters) and technical.
Return valid JSON only, no markdown."""

            client = self._get_llm()
            if self._is_openai:
                response = client.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = response.choices[0].message.content or ""
            else:
                response = client.messages.create(
                    model=settings.ANTHROPIC_MODEL,
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = ""
                for block in response.content:
                    if hasattr(block, "text") and block.text:
                        raw = block.text
                        break

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            results = eval(raw) if raw.startswith("[") else [query]
            return results[:3]

        except Exception as e:
            logger.warning(f"hyde_generate failed: {e}")
            return [query]

    def multi_query_expand(
        self, query: str, work_category: str = "", unit: str = ""
    ) -> list[str]:
        """
        INPUT:  query string — original search query
                work_category — for synonyms
                unit — for unit expansion
        OUTPUT: list of expanded query variants

        Creates multiple search queries:
        - Technical variant (with full measurements)
        - Short variant (key terms only)
        - Category-aware variant

        EXAMPLE:
            >>> input:  "brick wall", "civil_id", "sft"
            >>> output: ["brick wall 4.5 inch cement mortar",
                         "brick masonry",
                         "BRICKWORK 4.5 INCH"]
        """
        variants = [query]

        # Short variant: key meaningful words
        words = [
            w
            for w in re.split(r"\s+", query.lower())
            if len(w) > 2 and w not in {"and", "the", "for", "with", "per", "providing"}
        ][:5]
        if words:
            short_variant = " ".join(words)
            if short_variant != query.lower():
                variants.append(short_variant)

        # Technical variant: add category-specific terms
        if work_category in _WORK_CATEGORY_SYNONYMS:
            synonyms = _WORK_CATEGORY_SYNONYMS[work_category]
            for syn in synonyms[:2]:
                if syn.lower() not in query.lower():
                    tech_variant = f"{query} {syn}"
                    if len(tech_variant) < 60:
                        variants.append(tech_variant)

        # Unit-aware: include unit
        if unit and unit in _UNIT_SYNONYMS:
            unit_syns = _UNIT_SYNONYMS[unit]
            for us in unit_syns[:1]:
                if us.lower() not in query.lower():
                    variants.append(f"{query} {us}")

        # Dedupe while preserving order
        seen = set()
        unique = []
        for v in variants:
            v_norm = v.lower().strip()
            if v_norm not in seen:
                seen.add(v_norm)
                unique.append(v)
        return unique[:5]

    def decompose(self, query: str) -> list[str]:
        """
        INPUT:  query string — possibly compound query
        OUTPUT: list of sub-queries if compound, else [query]

        Decomposes compound queries into sub-queries.
        "Provide and install gypsum ceiling with painting" →
        ["gypsum ceiling false ceiling", "ceiling painting"]

        Splits on common conjunctions that likely indicate multiple items.
        """
        # Common separators indicating compound items
        separators = [
            r"\s+and\s+",
            r"\s+with\s+",
            r"\s+including\s+",
            r";\s*",
            r",\s*",
        ]

        parts = [query]
        for sep in separators:
            new_parts = []
            for part in parts:
                split_result = re.split(sep, part, maxsplit=1)
                new_parts.extend(split_result)
            if len(new_parts) > len(parts):
                parts = new_parts
                break

        # Clean each sub-query
        sub_queries = []
        for p in parts:
            p = p.strip()
            # Add back context words if they were lost
            p = re.sub(r"^provide\s+", "", p, flags=re.IGNORECASE)
            p = re.sub(r"^install\s+", "", p, flags=re.IGNORECASE)
            p = re.sub(r"^fixing\s+", "", p, flags=re.IGNORECASE)
            if len(p) > 5:
                sub_queries.append(p)

        return sub_queries if len(sub_queries) > 1 else [query]

    def transform(
        self,
        query: str,
        work_category: str = "",
        unit: str = "",
        use_hyde: bool = True,
    ) -> list[str]:
        """
        Full transformation pipeline.

        INPUT:  query — original user query
                work_category — for context
                unit — for unit expansion
                use_hyde — whether to generate hypothetical docs
        OUTPUT: All transformed queries (original + variants + hyde)

        EXAMPLE:
            >>> input:  "brick wall thick", "civil_id", "sft"
            >>> output: ["brick wall thick",
                         "brick wall thick 4.5 inch cement mortar",
                         "brick masonry",
                         "BRICKWORK 4.5 INCH"]
        """
        all_queries = [query]

        # Multi-query expansion
        expanded = self.multi_query_expand(query, work_category, unit)
        all_queries.extend(expanded[1:])  # Skip duplicate of original

        # Decomposition (if compound)
        decomp = self.decompose(query)
        for d in decomp:
            if d not in all_queries:
                all_queries.append(d)

        # HyDE (if enabled)
        if use_hyde:
            hyde_queries = self.hyde_generate(query, work_category)
            for hq in hyde_queries:
                if hq not in all_queries:
                    all_queries.append(hq)

        # Dedupe
        seen = set()
        unique = []
        for q in all_queries:
            if q.lower().strip() not in seen:
                seen.add(q.lower().strip())
                unique.append(q)

        return unique[:6]  # Max 6 queries


def transform_query(
    query: str,
    work_category: str = "",
    unit: str = "",
    use_hyde: bool = True,
) -> list[str]:
    """
    Convenience function for query transformation.

    EXAMPLE:
        >>> transform_query("gypsum ceiling 12mm", "ceiling", "sft")
        ["gypsum ceiling 12mm", "gypsum false ceiling", ...]
    """
    transformer = QueryTransformer()
    return transformer.transform(query, work_category, unit, use_hyde)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    qt = QueryTransformer()

    tests = [
        ("brick wall thick", "civil_id", "sft"),
        ("gypsum false ceiling 12mm", "ceiling", "sft"),
        ("split AC 4 ton installation", "hvac", "nos"),
        ("light circuit wiring 2.5mm", "electrical_elv", "rft"),
    ]

    for query, cat, unit in tests:
        print(f"\n--- {query} ({cat}) ---")
        transformed = qt.transform(query, cat, unit)
        for i, t in enumerate(transformed, 1):
            print(f"  {i}. {t}")
