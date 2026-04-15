"""
RateIQ Observability Module
Langfuse integration for traces + metrics.

Tracks:
- Retrieval latency (P50/P95/P99)
- LLM generation tokens + cost
- Error rates
- Node-level timing
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from collections import deque
import os

from .config import settings

logger = logging.getLogger(__name__)

_langfuse = None


def _get_langfuse():
    """Lazy init Langfuse."""
    global _langfuse
    if _langfuse is None:
        if not settings.LANGFUSE_ENABLED:
            logger.info("Langfuse disabled in config")
            return None

        if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
            logger.warning("Langfuse keys not configured")
            return None

        try:
            from langfuse import Langfuse

            _langfuse = Langfuse(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
            )
            logger.info("Langfuse initialized")
        except ImportError:
            logger.warning("langfuse package not installed")
            _langfuse = False
        except Exception as e:
            logger.warning(f"Langfuse init failed: {e}")
            _langfuse = False

    return _langfuse if _langfuse else None


@dataclass
class NodeTiming:
    """Timing for a single node execution."""

    node_name: str
    start_ms: float
    end_ms: float = 0.0
    error: str | None = None

    @property
    def duration_ms(self) -> float:
        """Duration in milliseconds."""
        if self.end_ms > 0:
            return self.end_ms - self.start_ms
        return 0.0


@dataclass
class RetrievalSpan:
    """A retrieval operation span."""

    query: str
    top_k: int
    results_count: int
    top_score: float
    latency_ms: float
    method: str = "hybrid"


@dataclass
class GenerationSpan:
    """An LLM generation span."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    error: str | None = None


class RateIQObserver:
    """
    Observability wrapper for RateIQ.

    Usage:
        observer = RateIQObserver()
        observer.trace_retrieval("brick work", 5, 3, 6.8, 45.0)
        observer.trace_generation("claude-sonnet-4-5", 100, 50, 500.0)
        observer.metrics()  # returns P50/P95/P99 latency
    """

    def __init__(self):
        self._retrieval_timings: deque = deque(maxlen=1000)
        self._generation_timings: deque = deque(maxlen=1000)
        self._node_timings: dict[str, list[float]] = {}
        self._lf = None

    def _ensure_langfuse(self):
        """Ensure Langfuse is initialized."""
        if self._lf is None:
            self._lf = _get_langfuse()

    def trace_retrieval(
        self,
        query: str,
        top_k: int,
        results_count: int,
        top_score: float,
        latency_ms: float,
        method: str = "hybrid",
    ) -> None:
        """Log a retrieval operation."""
        span = RetrievalSpan(
            query=query[:100],  # Truncate for storage
            top_k=top_k,
            results_count=results_count,
            top_score=top_score,
            latency_ms=latency_ms,
            method=method,
        )
        self._retrieval_timings.append(span)

        self._ensure_langfuse()
        if self._lf:
            try:
                self._lf.trace(
                    name="retrieval",
                    input={"query": query, "top_k": top_k},
                    output={"count": results_count, "top_score": top_score},
                    metadata={"method": method, "latency_ms": latency_ms},
                )
            except Exception as e:
                logger.debug(f"Langfuse trace failed: {e}")

    def trace_generation(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        error: str | None = None,
    ) -> None:
        """Log an LLM generation."""
        span = GenerationSpan(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            error=error,
        )
        self._generation_timings.append(span)

        self._ensure_langfuse()
        if self._lf:
            try:
                total_tokens = input_tokens + output_tokens
                cost = self._estimate_cost(model, input_tokens, output_tokens)
                self._lf.trace(
                    name="generation",
                    input={"model": model, "tokens_in": input_tokens},
                    output={"tokens_out": output_tokens, "total": total_tokens},
                    metadata={
                        "latency_ms": latency_ms,
                        "cost_usd": cost,
                        "error": error,
                    },
                )
            except Exception as e:
                logger.debug(f"Langfuse generation trace failed: {e}")

    def node_start(self, node_name: str) -> NodeTiming:
        """Start timing a node. Returns timing object to complete later."""
        timing = NodeTiming(node_name=node_name, start_ms=time.time() * 1000)
        return timing

    def node_end(self, timing: NodeTiming, error: str | None = None) -> float:
        """Complete a node timing. Returns duration in ms."""
        timing.end_ms = time.time() * 1000
        timing.error = error

        if timing.node_name not in self._node_timings:
            self._node_timings[timing.node_name] = []
        self._node_timings[timing.node_name].append(timing.duration_ms)

        return timing.duration_ms

    def _estimate_cost(self, model: str, in_tokens: int, out_tokens: int) -> float:
        """Estimate cost in USD for the model."""
        # Approximate pricing (USD per 1M tokens)
        pricing = {
            "claude-sonnet-4-5": (3.0, 15.0),  # in, out
            "claude-3-5-sonnet": (3.0, 15.0),
            "claude-3-opus": (15.0, 75.0),
            "gpt-4o": (5.0, 15.0),
            "gpt-4o-mini": (0.15, 0.6),
        }
        rates = pricing.get(model, (5.0, 15.0))
        return (in_tokens / 1_000_000 * rates[0]) + (out_tokens / 1_000_000 * rates[1])

    def metrics(self) -> dict[str, Any]:
        """Get observability metrics."""
        metrics = {
            "retrieval": self._compute_latency_stats(self._retrieval_timings),
            "generation": self._compute_latency_stats(self._generation_timings),
            "nodes": {},
        }

        for node_name, durations in self._node_timings.items():
            if durations:
                sorted_durations = sorted(durations)
                n = len(sorted_durations)
                metrics["nodes"][node_name] = {
                    "count": n,
                    "p50": sorted_durations[int(n * 0.5)],
                    "p95": sorted_durations[int(n * 0.95)]
                    if n > 1
                    else sorted_durations[0],
                    "p99": sorted_durations[int(n * 0.99)]
                    if n > 1
                    else sorted_durations[0],
                    "avg": sum(durations) / n,
                }

        # Error rates
        total_retrieval = len(self._retrieval_timings)
        total_gen = len(self._generation_timings)
        errors = sum(1 for s in self._generation_timings if s.error)

        metrics["error_rate"] = errors / total_gen if total_gen > 1 else 0.0
        metrics["total_traces"] = total_retrieval + total_gen

        return metrics

    def _compute_latency_stats(self, timings: Any) -> dict[str, Any]:
        """Compute P50/P95/P99 latency stats."""
        if not timings:
            return {"count": 0, "p50": 0, "p95": 0, "p99": 0}

        latencies = [t.latency_ms for t in timings if t.latency_ms > 0]
        if not latencies:
            return {"count": 0, "p50": 0, "p95": 0, "p99": 0}

        sorted_lat = sorted(latencies)
        n = len(sorted_lat)

        return {
            "count": n,
            "p50": sorted_lat[int(n * 0.5)],
            "p95": sorted_lat[int(n * 0.95)] if n > 1 else sorted_lat[0],
            "p99": sorted_lat[int(n * 0.99)] if n > 1 else sorted_lat[0],
            "avg": sum(latencies) / n,
        }


# ── Global Observer ────────────────────────────────────────────────────────────────

_observer: RateIQObserver | None = None


def get_observer() -> RateIQObserver:
    """Get the global observer instance."""
    global _observer
    if _observer is None:
        _observer = RateIQObserver()
    return _observer


def trace_node(node_name: str):
    """
    Context manager for timing a node.

    Usage:
        with trace_node("classify"):
            # ... node logic
            pass  # duration is logged automatically
    """
    return NodeTimingContext(node_name)


class NodeTimingContext:
    """Context manager for timing a node."""

    def __init__(self, node_name: str):
        self._node_name = node_name
        self._timing: NodeTiming | None = None

    def __enter__(self):
        self._timing = NodeTiming(
            node_name=self._node_name,
            start_ms=time.time() * 1000,
        )
        return self._timing

    def __exit__(self, exc_type, exc_val, exc_tb):
        error = str(exc_val) if exc_val else None
        if self._timing:
            self._timing.end_ms = time.time() * 1000
            observer = get_observer()
            observer.node_end(self._timing, error)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    obs = RateIQObserver()

    obs.trace_retrieval("brick work", 5, 3, 6.8, 45.0)
    obs.trace_retrieval("gypsum ceiling", 5, 4, 5.2, 62.0)
    obs.trace_generation("claude-sonnet-4-5", 150, 80, 520.0)

    timing = obs.node_start("classify")
    time.sleep(0.1)
    obs.node_end(timing)

    print("\nMetrics:")
    import json

    print(json.dumps(obs.metrics(), indent=2, default=str))
