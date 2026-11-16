"""Unit tests for Phase 5: Deterministic Query and Insights Engine."""

import datetime as dt
from src.models.schemas import ExpenseEntry, ExpenseSource
from src.storage.local_db import get_local_db
from src.tools.query_engine import query_expenses, resolve_time_period


def test_query_expenses_food_composite():
    """Verify 'food' query combines Groceries and Dining with exact totals (PRD Section 8.2)."""
    db = get_local_db()

    today = dt.date.today()
    # Insert Groceries 3900 and Dining 2520
    db.insert_expense(
        ExpenseEntry(
            id="exp_food_1",
            date=today,
            amount=3900.0,
            merchant="Big Bazaar",
            category="Groceries",
            source=ExpenseSource.TEXT,
        )
    )
    db.insert_expense(
        ExpenseEntry(
            id="exp_food_2",
            date=today,
            amount=2520.0,
            merchant="Main Street Cafe",
            category="Dining",
            source=ExpenseSource.TEXT,
        )
    )

    result = query_expenses(category="food", time_period="this_month")
    assert result["status"] == "success"
    assert result["total_amount"] >= 6420.0
    assert "Groceries" in result["category_breakdown"]
    assert "Dining" in result["category_breakdown"]


def test_budget_threshold_alerts():
    """Verify 80% notice and 100% budget alerts (PRD Section 4.5 FR-22)."""
    db = get_local_db()
    # Set Dining budget to 2000
    db.set_budget(category="Dining", monthly_limit=2000.0)

    # We already inserted Dining of 2520.0 in the previous test
    result = query_expenses(category="Dining", time_period="this_month")
    assert len(result["budget_alerts"]) > 0
    assert any("exceeded 100%" in alert for alert in result["budget_alerts"])


def test_time_period_resolution():
    """Verify time period resolution for named months and relative ranges."""
    base = dt.date(2026, 11, 11)

    start_oct, end_oct, _ = resolve_time_period("october", None, None, base)
    assert start_oct == dt.date(2026, 10, 1)
    assert end_oct == dt.date(2026, 10, 31)

    start_week, end_week, _ = resolve_time_period("this_week", None, None, base)
    assert start_week <= end_week

