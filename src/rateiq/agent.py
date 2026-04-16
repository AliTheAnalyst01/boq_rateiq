"""
WHY:  Single orchestration point for all rate-suggestion logic.
      Using LangGraph forces each step into an isolated, named node — so any
      reader can follow classify → history → market → gap → output without
      tracing function calls across five files.
WHAT: RateIQState — TypedDict carrying all pipeline data between nodes.
      RateIQAgent  — compiles and runs the graph; one instance per server startup.
      Node functions — _classify_node, _history_node, _market_node,
                       _gap_node, _output_node.
FITS INTO: Called by pipeline.py (batch) and api.py (per-request).
           Depends on searcher, gap_detector, market_search, postgres_store.

NOTE: All heavy objects (BOQSearcher, LLM, Tavily) are passed in at construction
      — NOT created inside nodes. Every node is independently testable.
LIMITATION: LangGraph StateGraph compiles at class init time (~2s).
            Do not instantiate RateIQAgent inside a hot path (per-request).
            pipeline.py creates one instance at startup and reuses it.
"""

import dataclasses
import json
import logging
import re
from typing import Any

from anthropic import Anthropic
from openai import OpenAI
from langgraph.graph import END, START, StateGraph
from tavily import TavilyClient
from typing_extensions import TypedDict

from .config import settings
from .gap_detector import analyze as gap_analyze
from .gap_detector import suggest_rate
from . import market_search as _market_search_module
from .observability import get_observer
from .models import (
    BOQChunk,
    GapAnalysis,
    MarketRate,
    RateRecommendation,
    RateStats,
    SearchResult,
)
from .postgres_store import cross_project_rates, rate_stats
from .searcher import BOQSearcher
from .circuit_breaker import CircuitOpenError, get_circuit

logger = logging.getLogger(__name__)


# ── State ─────────────────────────────────────────────────────────────────────


class RateIQState(TypedDict):
    # Input (set once at start)
    description: str
    qty: str | None
    unit_input: str | None

    # Set by classify_node
    work_category: str | None
    search_keywords: str | None
    unit_norm: str | None

    # Set by history_node
    search_results: list | None  # list[SearchResult] serialized as dicts
    rate_stats: dict | None  # RateStats as dict
    cross_project: list | None

    # Set by market_node
    market_rate: dict | None  # MarketRate as dict

    # Set by gap_node
    gap_analysis: dict | None  # GapAnalysis as dict

    # Set by output_node
    suggested_rate: float | None
    confidence: str | None
    explanation: str | None
    source_refs: list | None

    # CRAG: Retrieval quality verdict
    retrieval_verdict: str | None  # "correct" | "incorrect" | "ambiguous"

    # Error accumulator — never crashes
    errors: list | None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _dataclass_to_dict(obj: object) -> dict:
    """Safely convert a dataclass to a dict, recursively."""
    try:
        return dataclasses.asdict(obj)
    except Exception:
        return {}


def _grade_retrieval_quality(search_results: list[dict]) -> str:
    """
    Grade retrieval quality using cross-encoder reranker scores.

    INPUT:  search_results — list of SearchResult dicts
    OUTPUT: "correct" | "incorrect" | "ambiguous"

    Uses thresholds from config.py:
    - >= RETRIEVAL_CORRECT_THRESHOLD (3.0) → correct
    - <= RETRIEVAL_INCORRECT_THRESHOLD (0.5) → incorrect
    - between → ambiguous
    """
    if not search_results:
        return "incorrect"

    scores = [d.get("reranker_score", 0.0) for d in search_results]
    top_score = max(scores) if scores else 0.0

    if top_score >= settings.RETRIEVAL_CORRECT_THRESHOLD:
        return "correct"
    elif top_score <= settings.RETRIEVAL_INCORRECT_THRESHOLD:
        return "incorrect"
    else:
        return "ambiguous"


def _search_result_to_dict(sr: SearchResult) -> dict:
    """Convert SearchResult (with nested BOQChunk) to a plain dict."""
    return {
        "chunk_id": sr.chunk.chunk_id,
        "description_short": sr.chunk.description_short,
        "description_full": sr.chunk.description_full,
        "work_category": sr.chunk.work_category,
        "rate": sr.chunk.rate,
        "unit_norm": sr.chunk.unit_norm,
        "source_file": sr.chunk.source_file,
        "rate_per_unit_label": sr.chunk.rate_per_unit_label,
        "rrf_score": sr.rrf_score,
        "reranker_score": sr.reranker_score,
        "bm25_rank": sr.bm25_rank,
        "dense_rank": sr.dense_rank,
        "ranking_method": sr.ranking_method,
    }


def _dict_to_search_result(d: dict) -> SearchResult:
    """Reconstruct a SearchResult from a plain dict."""
    chunk = BOQChunk(
        chunk_id=d.get("chunk_id", ""),
        description_short=d.get("description_short", ""),
        description_full=d.get("description_full", ""),
        section_title="",
        work_category=d.get("work_category", ""),
        rate=float(d.get("rate", 0.0)),
        unit_norm=d.get("unit_norm", "unit"),
        qty="",
        source_file=d.get("source_file", ""),
        sheet_name="",
        rate_per_unit_label=d.get("rate_per_unit_label", ""),
        embedding_text="",
    )
    return SearchResult(
        chunk=chunk,
        rrf_score=float(d.get("rrf_score", 0.0)),
        reranker_score=float(d.get("reranker_score", 0.0)),
        bm25_rank=int(d.get("bm25_rank", 999)),
        dense_rank=int(d.get("dense_rank", 999)),
        ranking_method=d.get("ranking_method", "rrf_fallback"),
    )


# ── Helper: reconstruct typed RateRecommendation from final graph state ───────

def _build_rate_recommendation(
    final_state: dict,
    description: str,
    qty: str,
    unit: str,
) -> RateRecommendation:
    """
    WHY:  run() state dict → typed RateRecommendation. Isolated because this
          reconstruction is brittle (KeyError on bad state shape) and benefits
          from a single testable function separate from graph invocation.
    READS: final_state keys: search_results, rate_stats, market_rate, gap_analysis,
           suggested_rate, confidence, explanation, source_refs, unit_norm,
           work_category, search_keywords
    Returns fully-populated RateRecommendation dataclass.
    """
    sr_dicts = final_state.get("search_results") or []
    search_results = [_dict_to_search_result(d) for d in sr_dicts]

    rate_stats_obj: RateStats | None = None
    stats_dict = final_state.get("rate_stats")
    if stats_dict:
        try:
            rate_stats_obj = RateStats(
                **{k: v for k, v in stats_dict.items() if k in RateStats.__dataclass_fields__}
            )
        except Exception:
            pass

    market_rate_obj: MarketRate | None = None
    mkt_dict = final_state.get("market_rate") or {}
    if mkt_dict:
        try:
            market_rate_obj = MarketRate(
                **{k: v for k, v in mkt_dict.items() if k in MarketRate.__dataclass_fields__}
            )
        except Exception:
            pass

    gap_obj: GapAnalysis | None = None
    gap_dict = final_state.get("gap_analysis") or {}
    if gap_dict:
        try:
            gap_obj = GapAnalysis(
                **{k: v for k, v in gap_dict.items() if k in GapAnalysis.__dataclass_fields__}
            )
        except Exception:
            pass

    return RateRecommendation(
        description=description,
        unit=final_state.get("unit_norm") or unit or "unit",
        suggested_rate=float(final_state.get("suggested_rate") or 0.0),
        confidence=final_state.get("confidence") or "low",
        explanation=final_state.get("explanation") or "No explanation generated.",
        source_refs=final_state.get("source_refs") or [],
        search_results=search_results,
        rate_stats=rate_stats_obj,
        market_rate=market_rate_obj,
        gap_analysis=gap_obj,
        work_category=final_state.get("work_category") or "",
        search_keywords=final_state.get("search_keywords") or "",
    )


# ── Prompt templates (static portions — cache-eligible) ───────────────────────
# WHY: Static text separated from dynamic variables so the static portion
#      can be cached by Anthropic's prompt cache across calls.
#      Only the {description}/{unit} lines change per request.

_CLASSIFY_PROMPT_TEMPLATE = """\
You are a Pakistan construction cost expert.
Classify this BOQ item and extract search keywords.

Description: {description}
Unit (from sheet): {unit_input}

Return JSON with exactly these keys:
{{
  "work_category": <one of: civil_id, hvac, electrical_elv, finishing, plumbing, ceiling, flooring, carpentry, other>,
  "search_keywords": <3-6 word concise search term, no filler words>,
  "unit_norm": <normalized unit: sft, nos, rft, ls, kg, mtr, cft, ton, point, set, day — pick best match>
}}

Return valid JSON only."""

_OUTPUT_PROMPT_TEMPLATE = """\
Write a 2-3 sentence explanation for this BOQ rate suggestion.
Be specific, cite sources, max 60 words.

Item: {description}
Suggested Rate: Rs.{suggested_rate:,.0f} per {unit_norm}
Confidence: {confidence}
Gap Analysis: {verdict} — {recommendation}
Historical Avg: Rs.{hist_avg:,.0f} from {hist_count} tender(s)
Market Rate: {market_rate_str}
Sources: {sources_str}

Write the explanation in plain English. No markdown."""

# ── Helper: extract text from Anthropic SDK response content block ─────────────

def _extract_llm_text(response_content) -> str:
    """
    WHY:  Anthropic SDK returns a Messages object with typed content blocks
          (TextBlock, ThinkingBlock, etc.). This helper handles all variants
          so node functions don't duplicate the same 10-line pattern.
    Raises ValueError if no text content found in any block.
    """
    blocks = list(response_content)
    if not blocks:
        raise ValueError("Empty response from LLM")
    first = blocks[0]
    if isinstance(first, str):
        return first.strip()
    if hasattr(first, "text") and first.text:
        return first.text.strip()
    if hasattr(first, "thinking") and first.thinking:
        return first.thinking.strip()
    raise ValueError(f"Unexpected block type: {type(first)}")


# ── Agent ─────────────────────────────────────────────────────────────────────


class RateIQAgent:
    """
    LangGraph-powered agent that orchestrates all RateIQ components.
    Initialize once, call run() for each BOQ row.
    """

    def __init__(
        self,
        searcher: BOQSearcher,
        llm: Anthropic,
        tavily: TavilyClient,
    ) -> None:
        """
        INPUT:  BOQSearcher — initialized search engine with BM25 + Qdrant
                Anthropic — initialized Anthropic client
                TavilyClient — initialized web search client
        OUTPUT: RateIQAgent ready to process BOQ rows
        Builds and compiles the LangGraph graph on init.

        EXAMPLE:
            >>> input:  BOQSearcher(chunks), Anthropic(), TavilyClient(api_key=...)
            >>> output: RateIQAgent with compiled graph, ready for .run()
        """
        self._searcher = searcher
        self._llm = llm
        self._tavily = tavily
        self._graph = self._build_graph()
        self._is_openai = isinstance(llm, OpenAI)
        logger.info(
            "RateIQAgent initialized and graph compiled. Using OpenAI: %s",
            self._is_openai,
        )

    def _build_graph(self):
        """Build and compile the LangGraph StateGraph."""
        graph = StateGraph(RateIQState)

        graph.add_node("classify", self._classify_node)
        graph.add_node("history", self._history_node)
        graph.add_node("market", self._market_node)
        graph.add_node("gap", self._gap_node)
        graph.add_node("output", self._output_node)

        graph.add_edge(START, "classify")
        graph.add_edge("classify", "history")

        def _route_after_history(state: RateIQState) -> str:
            """CRAG: Route based on retrieval quality."""
            verdict = state.get("retrieval_verdict", "ambiguous")
            if verdict == "incorrect":
                # No good historical match - skip to market (web search only)
                logger.info("CRAG: verdict=incorrect, skipping historical")
                return "gap"  # Go directly to gap with no historical data
            return "market"

        graph.add_conditional_edges(
            "history",
            _route_after_history,
            {"market": "market", "gap": "gap"},
        )

        graph.add_edge("market", "gap")
        graph.add_edge("gap", "output")
        graph.add_edge("output", END)

        return graph.compile()

    # ── Nodes ──────────────────────────────────────────────────────────────

    def _classify_node(self, state: RateIQState) -> RateIQState:
        """
        WHY:   Routing decision — category + keywords determine which SQL index
               and search strategy run next. Wrong category = wrong site targets
               in market_search, wrong SQL bounds in validators.
        READS: state["description"], state["unit_input"]
        WRITES: state["work_category"], state["search_keywords"], state["unit_norm"]
        EXIT:  If LLM classify fails → _classify_fallback() fills safe defaults
               so the graph never stalls on this node.
        MODEL: ANTHROPIC_MODEL_LIGHT (Haiku) — JSON extraction only, no reasoning.
        """
        try:
            description = state.get("description", "")
            unit_input = state.get("unit_input") or ""

            prompt = _CLASSIFY_PROMPT_TEMPLATE.format(
                description=description, unit_input=unit_input
            )
            response = self._llm.messages.create(
                model=settings.ANTHROPIC_MODEL_LIGHT,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = _extract_llm_text(response.content)
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            data = json.loads(raw)
            state["work_category"] = data.get("work_category", "other")
            state["search_keywords"] = data.get("search_keywords", description[:60])
            state["unit_norm"] = data.get("unit_norm", unit_input or "unit")

        except Exception as exc:
            logger.warning(
                "_classify_node Claude failed, using regex fallback: %s", exc
            )
            state = self._classify_fallback(state)
            errors = state.get("errors") or []
            errors.append(f"classify_node: {exc}")
            state["errors"] = errors

        return state

    def _classify_fallback(self, state: RateIQState) -> RateIQState:
        """Regex-based fallback classifier when Claude fails."""
        desc = (state.get("description") or "").lower()
        unit_input = (state.get("unit_input") or "").lower()

        # Category rules
        category_patterns: list[tuple[str, str]] = [
            (r"hvac|air.?cond|duct|chiller|fan.?coil|vrf|split.?unit", "hvac"),
            (
                r"electric|wiring|conduit|mcb|panel|switch|socket|cable|lighting",
                "electrical_elv",
            ),
            (r"gypsum|false.?ceil|acoustic.?ceil|t.?grid|ceiling board", "ceiling"),
            (r"tile|marble|granite|floor|screed|terrazzo|vinyl|carpet", "flooring"),
            (r"plumb|pipe|sanit|toilet|basin|fitting|water.?supply", "plumbing"),
            (r"door|window|cabinet|wood.?work|carpentry|joinery|veneered", "carpentry"),
            (r"paint|plaster|skim|puttytex|wall.?finish|texture", "finishing"),
            (r"brick|concrete|rcc|shuttering|steel.?rebar|masonry|excavat", "civil_id"),
        ]
        category = "other"
        for pattern, cat in category_patterns:
            if re.search(pattern, desc):
                category = cat
                break

        # Unit rules
        unit_map = {
            "sft": "sft",
            "sqft": "sft",
            "rft": "rft",
            "nos": "nos",
            "no": "nos",
            "ls": "ls",
            "kg": "kg",
            "ton": "ton",
            "mtr": "mtr",
            "cft": "cft",
            "point": "point",
        }
        unit_norm = unit_map.get(
            unit_input.strip().lower().rstrip("."), unit_input or "unit"
        )

        # Keywords: take first 6 meaningful words
        words = [w for w in re.split(r"\s+", desc) if len(w) > 2][:6]
        keywords = " ".join(words)

        state["work_category"] = category
        state["search_keywords"] = keywords
        state["unit_norm"] = unit_norm
        return state

    def _history_node(self, state: RateIQState) -> RateIQState:
        """
        WHY:   Retrieves historical evidence before market search runs.
               CRAG verdict here determines whether market node runs or is skipped.
        READS: state["search_keywords"], state["unit_norm"], state["work_category"]
        WRITES: state["search_results"], state["rate_stats"], state["cross_project"],
                state["retrieval_verdict"]
        EXIT:  On failure → empty lists + error appended; pipeline continues to market.
        MODEL: None — pure DB + vector search, no LLM.
        """
        try:
            keywords = state.get("search_keywords") or state.get("description", "")
            category = state.get("work_category")
            unit = state.get("unit_norm")

            results: list[SearchResult] = self._searcher.search(
                query=keywords,
                top_k=settings.FINAL_TOP_K,
                work_category=category,
            )

            # Observability: trace retrieval
            try:
                top_score = results[0].reranker_score if results else 0.0
                method = results[0].ranking_method if results else "none"
                get_observer().trace_retrieval(
                    query=keywords,
                    top_k=settings.FINAL_TOP_K,
                    results_count=len(results),
                    top_score=top_score,
                    latency_ms=0.0,  # TODO: add timing
                    method=method,
                )
            except Exception:
                pass  # Observability failures never crash the agent

            # Fire fighting items must not bleed into generic electrical results.
            # Strictly keep only fire_fighting or electrical_elv chunks so that
            # a FACP panel doesn't get matched against light circuit wiring rates.
            if category == "fire_fighting" and results:
                filtered = [
                    r
                    for r in results
                    if r.chunk.work_category in ("fire_fighting", "electrical_elv")
                ]
                if filtered:
                    results = filtered
                    logger.info(
                        "history_node: fire_fighting filter applied, kept %d/%d results",
                        len(results),
                        settings.FINAL_TOP_K,
                    )

            state["search_results"] = [_search_result_to_dict(r) for r in results]

            # CRAG: Grade retrieval quality and set verdict
            verdict = _grade_retrieval_quality(state["search_results"])
            state["retrieval_verdict"] = verdict

            # SQL stats
            stats = rate_stats(keywords, unit if unit != "unit" else None)
            state["rate_stats"] = _dataclass_to_dict(stats) if stats else None

            logger.info(
                "history_node: verdict=%s, %d results, SQL stats=%s",
                verdict,
                len(results),
                "found" if stats else "none",
            )

            # Cross-project
            state["cross_project"] = cross_project_rates(
                keywords, unit if unit != "unit" else None
            )

        except Exception as exc:
            logger.error("_history_node failed: %s", exc)
            state["search_results"] = []
            state["rate_stats"] = None
            state["cross_project"] = []
            errors = state.get("errors") or []
            errors.append(f"history_node: {exc}")
            state["errors"] = errors

        return state

    def _market_node(self, state: RateIQState) -> RateIQState:
        """
        WHY:   Current market prices validate or override historical rates.
               Without this node, the system would quote 2021 rates in 2025.
        READS: state["search_keywords"], state["unit_norm"], state["work_category"]
        WRITES: state["market_rate"]
        EXIT:  CircuitOpenError → market_rate=None, gap_node uses historical only.
               On other failure → state["market_rate"]=None; gap_node handles gracefully.
        MODEL: ANTHROPIC_MODEL (Sonnet) via market_search — unit conversion reasoning.
        """
        cb = get_circuit("market_search")
        try:
            keywords = state.get("search_keywords") or state.get("description", "")
            unit = state.get("unit_norm") or "unit"
            category = state.get("work_category") or "other"

            market: MarketRate = cb.call(
                _market_search_module.search_market_rate,
                keywords, unit, category,
            )
            state["market_rate"] = _dataclass_to_dict(market)

            logger.info(
                "market_node: contractor_rate=%s, confidence=%s",
                market.contractor_rate,
                market.confidence,
            )

        except CircuitOpenError as exc:
            logger.warning("market_node: circuit OPEN, skipping market search: %s", exc)
            state["market_rate"] = None
            errors = state.get("errors") or []
            errors.append(f"market_node: circuit_open: {exc}")
            state["errors"] = errors

        except Exception as exc:
            logger.error("_market_node failed: %s", exc)
            state["market_rate"] = None
            errors = state.get("errors") or []
            errors.append(f"market_node: {exc}")
            state["errors"] = errors

        return state

    def _gap_node(self, state: RateIQState) -> RateIQState:
        """
        WHY:   Decides the final rate: use history (CONFIRMED), blend (MINOR_GAP),
               use market (MAJOR_GAP), or flag for review (NO_MARKET_DATA).
               MAST ratio gate here prevents cascading hallucinations from market node.
        READS: state["search_results"], state["market_rate"], state["search_keywords"],
               state["unit_norm"], state["work_category"]
        WRITES: state["gap_analysis"] (includes _suggested_rate, _confidence keys)
        EXIT:  On failure → gap_analysis=None; output_node falls back to recommendation text.
        MODEL: None — pure logic from gap_detector.py.
        """
        try:
            keywords = state.get("search_keywords") or state.get("description", "")
            unit = state.get("unit_norm") or "unit"

            # Reconstruct MarketRate from dict
            mkt_dict = state.get("market_rate") or {}
            market_rate = MarketRate(
                raw_rate=mkt_dict.get("raw_rate"),
                contractor_rate=mkt_dict.get("contractor_rate"),
                rate_type=mkt_dict.get("rate_type", "unknown"),
                source_name=mkt_dict.get("source_name"),
                source_url=mkt_dict.get("source_url"),
                raw_price_text=mkt_dict.get("raw_price_text"),
                confidence=mkt_dict.get("confidence", "low"),
                note=mkt_dict.get("note"),
            )

            # Reconstruct SearchResults
            sr_dicts = state.get("search_results") or []
            search_results = [_dict_to_search_result(d) for d in sr_dicts]

            gap: GapAnalysis = gap_analyze(keywords, unit, market_rate, search_results)
            suggested, confidence = suggest_rate(
                gap,
                search_results,
                category=state.get("work_category") or "",
                unit=unit,
            )

            gap_dict = _dataclass_to_dict(gap)
            gap_dict["_suggested_rate"] = suggested
            gap_dict["_confidence"] = confidence
            state["gap_analysis"] = gap_dict

            logger.info(
                "gap_node: verdict=%s, gap=%.1f%%, suggested=%.0f, confidence=%s",
                gap.verdict,
                gap.gap_pct,
                suggested,
                confidence,
            )

        except Exception as exc:
            logger.error("_gap_node failed: %s", exc)
            state["gap_analysis"] = None
            errors = state.get("errors") or []
            errors.append(f"gap_node: {exc}")
            state["errors"] = errors

        return state

    def _output_node(self, state: RateIQState) -> RateIQState:
        """
        WHY:   The final rate is only useful if the estimator understands WHY.
               Claude writes a 2-3 sentence explanation citing specific source files
               and rates so the human can verify before submitting the BOQ.
        READS: state["gap_analysis"], state["search_results"], state["market_rate"],
               state["description"], state["unit_norm"]
        WRITES: state["suggested_rate"], state["confidence"], state["explanation"],
                state["source_refs"]
        EXIT:  Unknown-item guard (reranker < -1.0) returns early with "not found" message.
               On LLM failure → falls back to gap_analysis.recommendation as explanation.
        MODEL: ANTHROPIC_MODEL_LIGHT (Haiku) — prose writing from structured data.
        """
        try:
            gap_dict = state.get("gap_analysis") or {}
            suggested_rate = gap_dict.get("_suggested_rate", 0.0)
            confidence = gap_dict.get("_confidence", "low")

            # Unknown-item detection: if we have search results but every
            # candidate scores deeply negative on the cross-encoder, the item
            # is not in our knowledge base at all (e.g. quantum dot OLED TV
            # scored -10 vs CCTV cameras). All valid items score ≥ 3.0.
            # Guard: only fire when sr_dicts is non-empty — empty results
            # mean dense search failed (Qdrant down), NOT a genuinely unknown
            # item, and we should not block the pipeline in that case.
            sr_dicts = state.get("search_results") or []
            top_reranker = max(
                (d.get("reranker_score", 0.0) for d in sr_dicts), default=0.0
            )
            if sr_dicts and top_reranker < -1.0:
                logger.info(
                    "output_node: top_reranker=%.3f < 1.0 — item not in knowledge base.",
                    top_reranker,
                )
                state["suggested_rate"] = None
                state["confidence"] = "low"
                state["explanation"] = (
                    "This item was not found in past BOQ records. "
                    "No reliable historical rate is available. "
                    "Manual rate entry required."
                )
                state["source_refs"] = []
                return state

            state["suggested_rate"] = float(suggested_rate)
            state["confidence"] = confidence

            # Build source refs
            source_refs: list[str] = []
            for sr in sr_dicts[:3]:
                sf = sr.get("source_file", "")
                if sf and sf not in source_refs:
                    source_refs.append(sf)

            mkt_dict = state.get("market_rate") or {}
            mkt_source = mkt_dict.get("source_name")
            if mkt_source:
                source_refs.append(mkt_source)

            state["source_refs"] = source_refs

            # Ask Claude for a concise explanation (Haiku — prose writing, no reasoning)
            mkt_rate_display = f"Rs.{gap_dict.get('market_rate'):,.0f}" if gap_dict.get("market_rate") else "not found"
            prompt = _OUTPUT_PROMPT_TEMPLATE.format(
                description=state.get("description", ""),
                suggested_rate=suggested_rate,
                unit_norm=state.get("unit_norm", "unit"),
                confidence=confidence,
                verdict=gap_dict.get("verdict", "N/A"),
                recommendation=gap_dict.get("recommendation", ""),
                hist_avg=gap_dict.get("hist_avg", 0),
                hist_count=gap_dict.get("hist_count", 0),
                market_rate_str=mkt_rate_display,
                sources_str=", ".join(source_refs[:3]) if source_refs else "historical data",
            )
            response = self._llm.messages.create(
                model=settings.ANTHROPIC_MODEL_LIGHT,
                max_tokens=settings.LLM_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            state["explanation"] = _extract_llm_text(response.content)

        except Exception as exc:
            logger.error("_output_node failed: %s", exc)
            # Fallback explanation without Claude
            gap_dict = state.get("gap_analysis") or {}
            state["suggested_rate"] = float(gap_dict.get("_suggested_rate", 0.0))
            state["confidence"] = gap_dict.get("_confidence", "low")
            state["explanation"] = gap_dict.get(
                "recommendation", "Rate based on historical data."
            )
            state["source_refs"] = []
            errors = state.get("errors") or []
            errors.append(f"output_node: {exc}")
            state["errors"] = errors

        return state

    # ── Public API ────────────────────────────────────────────────────────

    def run(
        self,
        description: str,
        qty: str = "",
        unit: str = "",
    ) -> RateRecommendation:
        """
        INPUT:  description string — BOQ item description text
                qty string — quantity (optional)
                unit string — unit from BOQ sheet (optional)
        OUTPUT: RateRecommendation dataclass — complete result
                with suggested_rate, explanation, sources

        EXAMPLE:
            >>> input:  "Providing and laying Brick Work 4.5 Thick"
            >>> output: RateRecommendation(
                        suggested_rate=315.0,
                        confidence="high",
                        explanation="Based on BOQ-01.xlsx (Rs.315/sft)...",
                        source_refs=["BOQ - 01 .xlsx", "Niazi Bricks"])
        """
        try:
            initial_state: RateIQState = {
                "description": description,
                "qty": qty or None,
                "unit_input": unit or None,
                "work_category": None,
                "search_keywords": None,
                "unit_norm": None,
                "search_results": None,
                "rate_stats": None,
                "cross_project": None,
                "market_rate": None,
                "gap_analysis": None,
                "suggested_rate": None,
                "confidence": None,
                "explanation": None,
                "source_refs": None,
                "retrieval_verdict": None,
                "errors": [],
            }

            final_state = self._graph.invoke(initial_state)

            if final_state.get("errors"):
                logger.warning("Agent completed with errors: %s", final_state["errors"])

            return _build_rate_recommendation(final_state, description, qty, unit)

        except Exception as exc:
            logger.error("RateIQAgent.run failed: %s", exc)
            return RateRecommendation(
                description=description,
                unit=unit or "unit",
                suggested_rate=0.0,
                confidence="low",
                explanation=f"Agent failed: {exc}",
                source_refs=[],
                work_category="",
                search_keywords="",
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info(
        "agent.py: import OK. Use pipeline.initialize_all() to test end-to-end."
    )
