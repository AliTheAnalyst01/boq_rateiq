"""
RateIQ Logging Configuration
Production-grade centralized logging with progress tracking.
"""

import logging
import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime
from typing import Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeRemainingColumn,
    TaskID,
)
from rich.table import Table
from rich.live import Live

_worker_id: int = 0
_operation_logger: Optional["OperationLogger"] = None


class Stage(str, Enum):
    """Processing stages with visual indicators."""

    LOADING = "🔵 Loading"
    PARSING = "🟡 Parsing"
    SEARCHING = "🔍 Searching"
    RATING = "⭐ Rating"
    MARKET = "🌐 Market"
    GAP = "📊 Gap Analysis"
    SAVING = "💾 Saving"
    COMPLETE = "✅ Complete"
    ERROR = "❌ Error"


@dataclass
class OperationStats:
    """Statistics for an ongoing operation."""

    total: int = 0
    completed: int = 0
    failed: int = 0
    start_time: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)
    stage: Stage = Stage.LOADING
    current_item: str = ""

    @property
    def success_rate(self) -> float:
        if self.completed == 0:
            return 0.0
        return (self.completed - self.failed) / self.completed * 100

    @property
    def eta_seconds(self) -> float:
        if self.completed == 0:
            return 0
        elapsed = time.time() - self.start_time
        rate = self.completed / elapsed
        remaining = self.total - self.completed
        return remaining / rate if rate > 0 else 0

    @property
    def rows_per_sec(self) -> float:
        elapsed = time.time() - self.start_time
        return self.completed / elapsed if elapsed > 0 else 0


class OperationLogger:
    """
    Track long-running operations with real-time progress.
    Zero-latency: writes directly to Rich display.
    """

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console(stderr=True, force_terminal=True)
        self._progress: Optional[Progress] = None
        self._task_id: Optional[TaskID] = None
        self._live: Optional[Live] = None
        self._stats = OperationStats()
        self._enabled = True
        self._last_log_time = 0
        self._log_interval = 0.5  # seconds between progress updates

    def start(self, total: int, description: str = "Processing") -> None:
        """Start tracking an operation."""
        if not self._enabled:
            return

        self._stats = OperationStats(total=total, start_time=time.time())
        self._last_log_time = time.time()

        # Create rich progress bar - use only built-in fields
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TextColumn("[cyan]{task.fields[eta]}"),
            TextColumn("•"),
            TextColumn("[green]{task.completed}/{task.total}"),
            TextColumn("•"),
            TextColumn("[yellow]{task.fields[rate]:.1f} rows/s"),
            TextColumn("•"),
            TextColumn("[magenta]{task.fields[stage]}"),
            console=self.console,
            transient=False,
        )

        self._progress.start()
        self._task_id = self._progress.add_task(
            description,
            total=total,
            eta="--:--",
            rate=0.0,
            stage="Starting...",
        )

    def update(
        self,
        completed: int,
        stage: Stage = Stage.RATING,
        current_item: str = "",
        failed: bool = False,
    ) -> None:
        """Update progress - call this after each item."""
        if not self._enabled or not self._progress or self._task_id is None:
            return

        now = time.time()
        if now - self._last_log_time < self._log_interval and not failed:
            return  # Throttle updates

        self._last_log_time = now
        self._stats.completed = completed
        self._stats.stage = stage
        self._stats.current_item = current_item

        if failed:
            self._stats.failed += 1

        # Calculate ETA
        eta_seconds = self._stats.eta_seconds
        if eta_seconds > 0 and eta_seconds < 3600:
            eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
        elif eta_seconds > 0:
            eta_str = f"{int(eta_seconds // 3600)}h {int((eta_seconds % 3600) // 60)}m"
        else:
            eta_str = "--:--"

        stage_str = stage.value.split()[1] if " " in stage.value else stage.value

        self._progress.update(
            self._task_id,
            eta=eta_str,
            rate=self._stats.rows_per_sec,
            stage=stage_str,
        )

    def set_stage(self, stage: Stage) -> None:
        """Update current stage."""
        self._stats.stage = stage

    def finish(self, success: bool = True) -> OperationStats:
        """Complete the operation and return stats."""
        if self._progress and self._task_id is not None:
            self._progress.update(
                self._task_id,
                completed=self._stats.total,
                stage="Done" if success else "Failed",
            )
            self._progress.stop()

        stats = self._stats
        self._stats = OperationStats()
        return stats

    def log_summary(self, stats: Optional[OperationStats] = None) -> None:
        """Print a formatted summary table."""
        if stats is None:
            stats = self._stats
        elapsed = time.time() - stats.start_time

        table = Table(title="📊 Processing Summary", show_header=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Total Rows", str(stats.total))
        table.add_row("Completed", str(stats.completed))
        table.add_row("Failed", str(stats.failed))
        table.add_row("Success Rate", f"{stats.success_rate:.1f}%")
        table.add_row("Duration", f"{elapsed:.1f}s")
        table.add_row("Throughput", f"{stats.rows_per_sec:.2f} rows/s")

        self.console.print(table)

    def disable(self) -> None:
        """Disable progress display (for tests/non-interactive)."""
        self._enabled = False

    def enable(self) -> None:
        """Enable progress display."""
        self._enabled = True


def set_worker_id(worker_id: int) -> None:
    """Set the current worker ID for logging context."""
    global _worker_id
    _worker_id = worker_id


def get_worker_id() -> int:
    """Get the current worker ID."""
    return _worker_id


def get_operation_logger() -> OperationLogger:
    """Get the global operation logger instance."""
    global _operation_logger
    if _operation_logger is None:
        _operation_logger = OperationLogger()
    return _operation_logger


class WorkerFilter(logging.Filter):
    """Add worker ID to log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.worker_id = _worker_id
        return True


class StructuredLogger:
    """Logger that supports both rich console and structured JSON output."""

    def __init__(
        self,
        name: str,
        console: Console,
        json_handler: Optional[logging.Handler] = None,
    ):
        self.logger = logging.getLogger(name)
        self.console = console
        self.json_handler = json_handler

    def info(self, msg: str, **kwargs: Any) -> None:
        self.logger.info(msg, **kwargs)
        if self.json_handler:
            self._write_json("info", msg, **kwargs)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self.logger.debug(msg, **kwargs)
        if self.json_handler:
            self._write_json("debug", msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self.logger.warning(msg, **kwargs)
        if self.json_handler:
            self._write_json("warning", msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self.logger.error(msg, **kwargs)
        if self.json_handler:
            self._write_json("error", msg, **kwargs)

    def stage(self, stage: Stage, msg: str = "") -> None:
        """Log a processing stage."""
        prefix = stage.value
        self.logger.info(f"{prefix} {msg}" if msg else prefix)

    def _write_json(self, level: str, msg: str, **kwargs: Any) -> None:
        """Write structured JSON log."""
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": level,
            "message": msg,
            "worker_id": _worker_id,
            **kwargs,
        }
        self.json_handler.emit(
            logging.LogRecord(
                name="json",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg=json.dumps(record),
                args=(),
                exc_info=None,
            )
        )


def setup_logging(
    level: int = logging.INFO, log_file: Optional[Path] = None, json_logs: bool = False
) -> None:
    """
    Configure production-grade logging.

    Args:
        level: Logging level (default INFO)
        log_file: Optional file path for file logging
        json_logs: Enable JSON structured logging
    """
    console = Console(stderr=True, force_terminal=True)

    # Rich handler for terminal
    rich_handler = RichHandler(
        console=console,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
        show_time=True,
        show_path=False,
    )
    rich_handler.addFilter(WorkerFilter())

    handlers = [rich_handler]

    # File handler if requested
    file_handler: Optional[logging.FileHandler] = None
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(worker_id)s | %(message)s"
            )
        )
        handlers.append(file_handler)

    # JSON handler for structured logs
    json_handler: Optional[logging.FileHandler] = None
    if json_logs:
        json_path = (
            log_file.parent / "rateiq.json.log"
            if log_file
            else Path("logs/rateiq.json.log")
        )
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_handler = logging.FileHandler(json_path)
        json_handler.setFormatter(logging.Formatter("%(message)s"))
        handlers.append(json_handler)

    logging.basicConfig(
        level=level,
        handlers=handlers,
        format="%(message)s",
    )

    # Configure all rateiq loggers
    for logger_name in (
        "rateiq",
        "agent",
        "pipeline",
        "market_search",
        "postgres_store",
        "searcher",
        "hybrid_search",
        "gap_detector",
    ):
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        logger.handlers.clear()
        for h in handlers:
            logger.addHandler(h)
        logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Get a logger for the given module name."""
    return logging.getLogger(name)


# Initialize global operation logger
_operation_logger = OperationLogger()
