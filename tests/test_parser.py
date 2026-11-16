"""Unit tests for the natural language expense parser."""

import datetime as dt
from src.tools.parser import parse_expense, resolve_date_expression


def test_parse_single_expense_standard():
    """Test 'spent 450 on groceries'."""
    results = parse_expense("spent 450 on groceries", reference_date_iso="2026-11-11")
    assert len(results) == 1
    assert results[0]["amount"] == 450.0
    assert "groceries" in results[0]["merchant"].lower()
    assert results[0]["currency"] == "INR"
    assert results[0]["date"] == "2026-11-11"


def test_parse_multiple_expenses_in_one_message():
    """Test 'auto 80, coffee 120 and lunch 250 yesterday' (PRD Section 8.2 example)."""
    results = parse_expense("auto 80, coffee 120 and lunch 250 yesterday", reference_date_iso="2026-11-11")
    assert len(results) == 3

    amounts = [r["amount"] for r in results]
    assert amounts == [80.0, 120.0, 250.0]

    # Date should be yesterday: 2026-11-10
    for r in results:
        assert r["date"] == "2026-11-10"


def test_parse_with_currency_symbols():
    """Test amounts with ₹, Rs., and comma formatting."""
    results1 = parse_expense("Paid ₹1,284 for supermarket groceries", reference_date_iso="2026-11-11")
    assert len(results1) >= 1
    assert results1[0]["amount"] == 1284.0

    results2 = parse_expense("Rs 500 petrol via UPI", reference_date_iso="2026-11-11")
    assert len(results2) >= 1
    assert results2[0]["amount"] == 500.0
    assert results2[0]["payment_method"] == "UPI"


def test_relative_date_resolution():
    """Test resolution of relative dates."""
    base = dt.date(2026, 11, 11)  # Wednesday
    assert resolve_date_expression("spent yesterday", base) == dt.date(2026, 11, 10)
    assert resolve_date_expression("spent today", base) == dt.date(2026, 11, 11)
    assert resolve_date_expression("day before yesterday", base) == dt.date(2026, 11, 9)
    assert resolve_date_expression("3 days ago", base) == dt.date(2026, 11, 8)

