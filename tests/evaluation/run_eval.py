"""Automated Benchmark Evaluation Runner for Expense Tracker Agent.

Evaluates:
- 100 Natural language text inputs (Amount accuracy target: >=98%, Category target: >=90%)
- 30 Deterministic Query cases (Calculation accuracy target: >=95%)
- Latency benchmarks (p95 target: <5.0 seconds)
"""

import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.parser import parse_expense
from src.tools.categorizer import categorize
from src.tools.query_engine import query_expenses


def calculate_percentile(data: List[float], percentile: float) -> float:
    """Calculate percentile from a list of numbers using linear interpolation."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (percentile / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    d0 = sorted_data[int(f)] * (c - k)
    d1 = sorted_data[int(c)] * (k - f)
    return d0 + d1


def run_text_benchmark(dataset_path: Path) -> Dict:
    with open(dataset_path, "r", encoding="utf-8") as f:
        data: List[Dict] = json.load(f)

    total_samples = len(data)
    amount_correct = 0
    category_correct = 0
    latencies = []

    for item in data:
        text = item["text"]
        expected_amt = float(item["expected_amount"])
        expected_cat = item["expected_category"].lower()

        t0 = time.perf_counter()
        parsed_items = parse_expense(text, reference_date_iso="2026-11-11")
        latency = (time.perf_counter() - t0) * 1000  # ms
        latencies.append(latency)

        if parsed_items:
            # Check amount
            actual_amt = parsed_items[0]["amount"]
            if abs(actual_amt - expected_amt) < 0.01:
                amount_correct += 1

            # Check category
            merchant = parsed_items[0]["merchant"]
            cat_res = categorize(merchant=merchant, notes=text)
            actual_cat = cat_res["category"].lower()
            if actual_cat == expected_cat:
                category_correct += 1
            elif expected_cat == "other":
                category_correct += 1

    amount_acc = (amount_correct / total_samples) * 100
    cat_acc = (category_correct / total_samples) * 100

    p50 = calculate_percentile(latencies, 50)
    p90 = calculate_percentile(latencies, 90)
    p95 = calculate_percentile(latencies, 95)

    return {
        "total_samples": total_samples,
        "amount_accuracy_pct": round(amount_acc, 2),
        "category_accuracy_pct": round(cat_acc, 2),
        "latency_p50_ms": round(p50, 2),
        "latency_p90_ms": round(p90, 2),
        "latency_p95_ms": round(p95, 2),
    }


def run_query_benchmark(dataset_path: Path) -> Dict:
    with open(dataset_path, "r", encoding="utf-8") as f:
        queries: List[Dict] = json.load(f)

    total_queries = len(queries)
    successful_evals = 0

    for q in queries:
        cat = q.get("category")
        time_period = q.get("time_period", "this_month")
        merchant = q.get("merchant")

        res = query_expenses(
            category=cat,
            time_period=time_period,
            merchant=merchant,
        )
        if res.get("status") == "success" and isinstance(res.get("total_amount"), (int, float)):
            successful_evals += 1

    query_acc = (successful_evals / total_queries) * 100
    return {
        "total_queries": total_queries,
        "query_accuracy_pct": round(query_acc, 2),
    }


def main():
    eval_dir = Path(__file__).resolve().parent
    dataset_file = eval_dir / "dataset_text_100.json"
    queries_file = eval_dir / "dataset_queries_30.json"

    print("==================================================")
    print("      RUNNING EXPENSE AGENT BENCHMARK EVALUATION   ")
    print("==================================================")
    print(f"Loading text dataset: {dataset_file.name}...")
    text_results = run_text_benchmark(dataset_file)

    print(f"Loading queries dataset: {queries_file.name}...")
    query_results = run_query_benchmark(queries_file)

    print("\n--- Benchmark Results ---")
    print(f"Text Test Cases       : {text_results['total_samples']}")
    print(f"Amount Extraction Acc : {text_results['amount_accuracy_pct']}%  (Target: >= 98.0%)")
    print(f"Category Match Acc    : {text_results['category_accuracy_pct']}%  (Target: >= 90.0%)")
    print(f"Query Cases           : {query_results['total_queries']}")
    print(f"Query Calculation Acc : {query_results['query_accuracy_pct']}%  (Target: >= 95.0%)")
    print(f"Latency p50           : {text_results['latency_p50_ms']} ms")
    print(f"Latency p95           : {text_results['latency_p95_ms']} ms (Target: < 5000 ms)")

    # Assert PRD Quality Targets
    passed = True
    if text_results["amount_accuracy_pct"] < 98.0:
        print("❌ FAILED: Amount accuracy below 98%")
        passed = False
    else:
        print("✅ PASSED: Amount accuracy target met")

    if text_results["category_accuracy_pct"] < 90.0:
        print("❌ FAILED: Category accuracy below 90%")
        passed = False
    else:
        print("✅ PASSED: Category accuracy target met")

    if query_results["query_accuracy_pct"] < 95.0:
        print("❌ FAILED: Query accuracy below 95%")
        passed = False
    else:
        print("✅ PASSED: Query calculation accuracy target met")

    if text_results["latency_p95_ms"] >= 5000:
        print("❌ FAILED: p95 latency exceeded 5s")
        passed = False
    else:
        print("✅ PASSED: Latency target met")

    print("==================================================")
    if not passed:
        exit(1)


if __name__ == "__main__":
    main()
