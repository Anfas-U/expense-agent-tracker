"""Unit tests for Phase 0 models and schemas."""

from datetime import date, datetime
from src.models.schemas import (
    ExpenseEntry,
    ExpenseSource,
    PaymentMethod,
    ParsedExpenseItem,
    ReceiptExtractedData,
    ReceiptLineItem,
    ExpenseQueryFilter,
    ExpenseQueryResult,
    UserPreferences,
)
from config.settings import settings


def test_expense_entry_to_sheet_row():
    """Verify that ExpenseEntry properly formats a row according to PRD Section 6.1."""
    entry = ExpenseEntry(
        id="exp_test_001",
        date=date(2026, 11, 11),
        amount=450.0,
        currency="INR",
        merchant="Groceries Store",
        category="Groceries",
        payment_method="UPI",
        notes="Weekly vegetables",
        source=ExpenseSource.TEXT,
        receipt_url="",
        confidence=0.98,
        created_at=datetime(2026, 11, 11, 11, 30, 0),
    )

    row = entry.to_sheet_row()
    assert len(row) == 12
    assert row[0] == "exp_test_001"
    assert row[1] == "2026-11-11"
    assert row[2] == 450.0
    assert row[3] == "INR"
    assert row[4] == "Groceries Store"
    assert row[5] == "Groceries"
    assert row[6] == "UPI"
    assert row[7] == "Weekly vegetables"
    assert row[8] == "text"
    assert row[9] == ""
    assert row[10] == 0.98
    assert row[11] == "2026-11-11 11:30:00"

    headers = ExpenseEntry.sheet_columns()
    assert len(headers) == 12
    assert headers[0] == "id"
    assert headers[2] == "amount"
    assert headers[5] == "category"


def test_categories_loading():
    """Verify that default categories and merchant rules load correctly."""
    cat_data = settings.load_category_definitions()
    categories = cat_data.get("categories", [])
    assert len(categories) >= 13

    cat_names = [c["name"] for c in categories]
    assert "Groceries" in cat_names
    assert "Dining" in cat_names
    assert "Transport" in cat_names
    assert "Other" in cat_names

    rules = cat_data.get("merchant_rules", {})
    assert rules.get("swiggy") == "Dining"
    assert rules.get("uber") == "Transport"


def test_receipt_extracted_data():
    """Verify ReceiptExtractedData schema validation."""
    data = ReceiptExtractedData(
        merchant="Big Bazaar",
        date=date(2026, 11, 10),
        total_amount=1284.0,
        currency="INR",
        line_items=[
            ReceiptLineItem(description="Rice 5kg", amount=350.0, quantity=1.0),
            ReceiptLineItem(description="Cooking Oil", amount=200.0, quantity=1.0),
        ],
        category_suggestion="Groceries",
        confidence=0.95,
    )
    assert data.total_amount == 1284.0
    assert len(data.line_items) == 2
    assert data.merchant == "Big Bazaar"


def test_query_filter_and_result():
    """Verify query filter and deterministic aggregation model."""
    q_filter = ExpenseQueryFilter(category="Dining")
    assert q_filter.category == "Dining"

    result = ExpenseQueryResult(
        total_amount=6420.0,
        entry_count=12,
        category_breakdown={"Groceries": 3900.0, "Dining": 2520.0},
        percentage_change=8.0,
    )
    assert result.total_amount == 6420.0
    assert result.entry_count == 12
    assert result.percentage_change == 8.0

