"""
WHY:  Measures agent accuracy against the permanent holdout set.
      Rate Invariant: all rates are per-unit — never derived from amount/qty.
      Baseline accuracy must be established BEFORE any pipeline changes.
      Target: ≥75% of holdout items within ±15% of ground truth.

REQUIRES: Live Qdrant + PostgreSQL + .env API keys
RUNTIME:  ~10-15 minutes for ~286 items
RUN:      pytest tests/eval/test_accuracy.py -v --tb=short -s
"""

import csv
import logging
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

# Add src to path so rateiq is importable when running from project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

logger = logging.getLogger(__name__)

HOLDOUT_CSV = Path("tests/eval/holdout_set.csv")
RESULTS_DIR = Path("tests/eval/results")
ACCURACY_THRESHOLD = 0.75   # 75% of items must pass ±15%
RATE_TOLERANCE_PCT = 15.0   # ±15% of ground truth


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def agent():
    """Initialize agent once for the entire eval session."""
    from rateiq.pipeline import initialize_all
    return initialize_all()


@pytest.fixture(scope="session")
def holdout_items():
    """Load holdout set. Skip if not built yet."""
    if not HOLDOUT_CSV.exists():
        pytest.skip(
            f"Holdout set not found at {HOLDOUT_CSV}. "
            "Run: python scripts/build_holdout.py"
        )
    df = pd.read_csv(HOLDOUT_CSV)
    # Only items with valid ground truth rates
    df = df[df["rate"].notna() & (df["rate"] > 0)].copy()
    return df.to_dict("records")


# ── Helpers ───────────────────────────────────────────────────────────────────

def within_tolerance(predicted: float, actual: float, tolerance_pct: float) -> bool:
    """Return True if predicted is within ±tolerance_pct% of actual."""
    if actual <= 0:
        return False
    gap_pct = abs(predicted - actual) / actual * 100.0
    return gap_pct <= tolerance_pct


def save_results(results: list) -> Path:
    """Save per-item results to dated CSV in tests/eval/results/."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = RESULTS_DIR / f"{date.today()}.csv"
    if results:
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
    logger.info("Eval results saved to %s", output_path)
    return output_path


def print_accuracy_table(results: list) -> float:
    """Print per-category accuracy table and return overall accuracy (0.0–1.0)."""
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["work_category"]].append(r["passed"])

    print("\n=== ACCURACY REPORT ===")
    print(f"{'Category':<25} {'Items':>6} {'Passed':>8} {'Accuracy':>10}")
    print("-" * 53)

    total_items = 0
    total_passed = 0
    for cat in sorted(by_cat):
        items = by_cat[cat]
        passed = sum(items)
        acc = passed / len(items) * 100 if items else 0
        print(f"{cat:<25} {len(items):>6} {passed:>8} {acc:>9.1f}%")
        total_items += len(items)
        total_passed += passed

    overall = total_passed / total_items * 100 if total_items else 0
    print("-" * 53)
    print(f"{'OVERALL':<25} {total_items:>6} {total_passed:>8} {overall:>9.1f}%")
    print(f"\nTarget: ≥{ACCURACY_THRESHOLD * 100:.0f}% | Result: {overall:.1f}%")
    return total_passed / total_items if total_items else 0.0


# ── Main eval test ─────────────────────────────────────────────────────────────

def test_agent_rate_accuracy(agent, holdout_items):
    """
    For each holdout item:
      1. Run agent.run(description, qty, unit) — qty is context only, rate is per-unit
      2. Compare predicted rate vs ground truth rate (per-unit, from rate column)
      3. Audit decision path: CRAG verdict, gap verdict, hist source
    Fail if overall accuracy < 75%.
    """
    results = []
    errors = []

    for i, item in enumerate(holdout_items):
        description = str(item.get("description_short", ""))
        unit = str(item.get("unit_norm", ""))
        qty = str(item.get("qty", ""))
        actual_rate = float(item.get("rate", 0.0))
        category = str(item.get("work_category", ""))

        if i % 20 == 0:
            logger.info("Eval progress: %d/%d items", i, len(holdout_items))

        try:
            rec = agent.run(description, qty, unit)
            predicted = rec.suggested_rate or 0.0

            passed = within_tolerance(predicted, actual_rate, RATE_TOLERANCE_PCT)
            gap_pct = (
                round(abs(predicted - actual_rate) / actual_rate * 100, 2)
                if actual_rate > 0 else None
            )

            # Decision path audit
            gap_analysis = rec.gap_analysis
            crag_verdict = gap_analysis.crag_verdict if gap_analysis else "unknown"
            gap_verdict = gap_analysis.verdict if gap_analysis else "unknown"
            hist_source = gap_analysis.hist_source if gap_analysis else "unknown"

            # Rate Invariant: explanation must name the per-unit rate explicitly
            explanation = rec.explanation or ""
            has_unit_label = any(
                label in explanation.lower()
                for label in [f"per {unit.lower()}", f"/{unit.lower()}"]
            )

            results.append({
                "chunk_id": item.get("chunk_id", ""),
                "description": description,
                "unit": unit,
                "work_category": category,
                "actual_rate": actual_rate,
                "predicted_rate": predicted,
                "gap_pct": gap_pct,
                "passed": passed,
                "confidence": rec.confidence,
                "crag_verdict": crag_verdict,
                "gap_verdict": gap_verdict,
                "hist_source": hist_source,
                "has_unit_label": has_unit_label,
                "error": None,
            })

        except Exception as exc:
            logger.error("Eval error on item %d (%s): %s", i, description[:40], exc)
            errors.append({"item": i, "description": description, "error": str(exc)})
            results.append({
                "chunk_id": item.get("chunk_id", ""),
                "description": description,
                "unit": unit,
                "work_category": category,
                "actual_rate": actual_rate,
                "predicted_rate": 0.0,
                "gap_pct": None,
                "passed": False,
                "confidence": "low",
                "crag_verdict": "error",
                "gap_verdict": "error",
                "hist_source": "error",
                "has_unit_label": False,
                "error": str(exc),
            })

    # Save results to CSV
    save_results(results)

    # Print per-category report and get overall accuracy
    overall_accuracy = print_accuracy_table(results)

    if errors:
        print(f"\n{len(errors)} items threw exceptions (counted as failed)")

    assert overall_accuracy >= ACCURACY_THRESHOLD, (
        f"Accuracy {overall_accuracy:.1%} is below threshold {ACCURACY_THRESHOLD:.0%}. "
        f"See tests/eval/results/{date.today()}.csv for details."
    )
