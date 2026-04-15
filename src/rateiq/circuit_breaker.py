"""
RateIQ Circuit Breaker Module
3-state circuit breaker to prevent runaway agent loops.

States:
- CLOSED: Normal operation
- OPEN: Failing fast (error threshold exceeded)
- HALF_OPEN: Testing recovery
"""

import logging
import time
from enum import Enum
from threading import Lock
from typing import Any, Callable

from .config import settings

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when circuit is open and call is rejected."""

    pass


class CircuitBreaker:
    """
    Thread-safe circuit breaker for agent operations.

    Prevents runaway loops from burning API credits.
    Three-state machine: Closed → Open → Half-open → Closed.
    """

    def __init__(
        self,
        name: str,
        error_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 3,
    ):
        """
        INPUT:  name — identifier for this circuit
                error_threshold — errors before opening (default 5)
                recovery_timeout — seconds before half-open (default 30)
                success_threshold — successes to close from half-open (default 3)
        """
        self._name = name
        self._error_threshold = error_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._errors = 0
        self._successes = 0
        self._last_error_time = 0.0
        self._lock = Lock()

        logger.info(
            "%s circuit breaker initialized: errors=%d, timeout=%.0fs, success=%d",
            name,
            error_threshold,
            recovery_timeout,
            success_threshold,
        )

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._state

    @property
    def name(self) -> str:
        """Circuit name."""
        return self._name

    def record_success(self) -> None:
        """Record a successful call."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._successes += 1
                if self._successes >= self._success_threshold:
                    self._transition_to(CircuitState.CLOSED)
                    logger.info(
                        "%s circuit CLOSED after %d successes",
                        self._name,
                        self._successes,
                    )
            else:
                # Reset error count on success in closed state
                self._errors = 0
            self._successes = 0

    def record_failure(self) -> None:
        """Record a failed call."""
        with self._lock:
            self._errors += 1
            self._last_error_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                # Failed during recovery test - go back to open
                self._transition_to(CircuitState.OPEN)
                logger.warning("%s circuit OPEN (recovery failed)", self._name)
            elif self._errors >= self._error_threshold:
                self._transition_to(CircuitState.OPEN)
                logger.warning(
                    "%s circuit OPEN after %d errors",
                    self._name,
                    self._errors,
                )

    def _transition_to(self, new_state: CircuitState) -> None:
        """Change state with side effects."""
        old_state = self._state
        self._state = new_state

        if new_state == CircuitState.CLOSED:
            self._errors = 0
            self._successes = 0
        elif new_state == CircuitState.OPEN:
            self._successes = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._successes = 0

        logger.info("%s circuit: %s → %s", self._name, old_state.value, new_state.value)

    def can_execute(self) -> bool:
        """Check if execution is allowed."""
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                # Check if recovery timeout has passed
                if time.time() - self._last_error_time >= self._recovery_timeout:
                    self._transition_to(CircuitState.HALF_OPEN)
                    return True
                return False

            # HALF_OPEN state allows execution
            return True

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        Execute func through circuit breaker.

        Raises CircuitOpenError if circuit is open.

        EXAMPLE:
            >>> cb = CircuitBreaker("market_search")
            >>> result = cb.call(do_market_search, query="brick work")
        """
        if not self.can_execute():
            raise CircuitOpenError(f"{self._name} circuit is OPEN, retry after timeout")

        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            self.record_failure()
            raise

    def reset(self) -> None:
        """Manually reset circuit to closed state."""
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            logger.info("%s circuit manually reset", self._name)


# ── Global Circuit Breakers ────────────────────────────────────────────────────


_circuits: dict[str, CircuitBreaker] = {}


def get_circuit(name: str) -> CircuitBreaker:
    """
    Get or create a circuit breaker by name.

    EXAMPLE:
        >>> cb = get_circuit("market_search")
        >>> cb = get_circuit("llm_generation")
    """
    if name not in _circuits:
        _circuits[name] = CircuitBreaker(
            name=name,
            error_threshold=settings.CIRCUIT_BREAKER_ERROR_THRESHOLD,
            recovery_timeout=settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
            success_threshold=settings.CIRCUIT_BREAKER_SUCCESS_THRESHOLD,
        )
    return _circuits[name]


def reset_all_circuits() -> None:
    """Reset all circuit breakers."""
    for cb in _circuits.values():
        cb.reset()
    _circuits.clear()
    logger.info("All circuit breakers reset")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    cb = CircuitBreaker(
        "test", error_threshold=3, recovery_timeout=5, success_threshold=2
    )

    def failing_func(x: int) -> str:
        if x < 2:
            raise ValueError("fail")
        return f"success: {x}"

    for i in range(10):
        try:
            result = cb.call(failing_func, i)
            print(f"Call {i}: {result} | state={cb.state.value}")
        except CircuitOpenError as e:
            print(f"Call {i}: BLOCKED - {e}")
        except ValueError as e:
            print(f"Call {i}: FAILED - {e}")

    time.sleep(6)  # Wait for recovery timeout

    for i in range(10):
        try:
            result = cb.call(failing_func, i)
            print(f"Recovery {i}: {result} | state={cb.state.value}")
        except CircuitOpenError as e:
            print(f"Recovery {i}: BLOCKED - {e}")
