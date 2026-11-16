"""Automated test validating benchmark targets."""

from pathlib import Path
from tests.evaluation.run_eval import run_text_benchmark


def test_benchmark_targets_satisfied():
    """Verify that 100 benchmark test cases meet or exceed PRD targets."""
    dataset_file = Path(__file__).resolve().parent / "evaluation" / "dataset_text_100.json"
    results = run_text_benchmark(dataset_file)

    # PRD targets:
    # Amount extraction accuracy >= 98%
    assert results["amount_accuracy_pct"] >= 98.0
    # Category match accuracy >= 90%
    assert results["category_accuracy_pct"] >= 90.0
    # Latency p95 < 5.0 seconds (5000 ms)
    assert results["latency_p95_ms"] < 5000.0

