"""Unit tests for Phase 2: Local Database and Spreadsheet Storage."""

import datetime as dt
from pathlib import Path
from src.storage.local_db import LocalDatabase
from src.storage.sheets_client import SpreadsheetStorage
from src.tools.spreadsheet import append_row, update_row, delete_row
from src.tools.categorizer import get_categorizer


def test_local_db_insert_and_get(tmp_path: Path):
    """Verify local SQLite database insert, retrieval, and session tracking."""
    test_db = LocalDatabase(db_path=tmp_path / "test.db")
    from src.models.schemas import ExpenseEntry, ExpenseSource

    entry = ExpenseEntry(
        id="exp_test_db_1",
        date=dt.date(2026, 11, 11),
        amount=500.0,
        currency="INR",
        merchant="Shell Petrol",
        category="Fuel",
        payment_method="UPI",
        notes="Bike petrol",
        source=ExpenseSource.TEXT,
    )

    test_db.insert_expense(entry)

    # Fetch by ID
    fetched = test_db.get_expense("exp_test_db_1")
    assert fetched is not None
    assert fetched.amount == 500.0
    assert fetched.merchant == "Shell Petrol"
    assert fetched.category == "Fuel"

    # Fetch last logged
    last = test_db.get_last_logged_expense()
    assert last is not None
    assert last.id == "exp_test_db_1"


def test_append_row_tool():
    """Verify append_row tool inserts and returns formatted confirmation."""
    res = append_row(
        amount=450.0,
        merchant="Groceries Fresh Mart",
        category="Groceries",
        notes="Fresh vegetables",
        force=True,
    )
    assert res["status"] == "success"
    assert res["amount"] == 450.0
    assert "Logged ₹450, Groceries" in res["confirmation_message"]


def test_update_row_and_learn_rule():
    """Verify update_row updates the last entry and registers learned merchant rule."""
    # First append an expense
    append_row(
        amount=1200.0,
        merchant="Specialty Roasters",
        category="Other",
        force=True,
    )

    # Correct category to Dining and amount to 1250 (PRD FR-9 & FR-12)
    update_res = update_row(new_amount=1250.0, new_category="Dining")
    assert update_res["status"] == "success"
    assert "to ₹1250" in update_res["confirmation_message"]
    assert "category to Dining" in update_res["confirmation_message"]

    # Verify rule was learned: future query for this merchant should be 'Dining'
    cat_engine = get_categorizer()
    assert cat_engine.user_learned_rules.get("specialty roasters") == "Dining"


def test_delete_row_tool():
    """Verify delete_row deletes the last logged entry."""
    append_row(
        amount=99.0,
        merchant="Test Shopping Store",
        category="Shopping",
        force=True,
    )

    del_res = delete_row()
    assert del_res["status"] == "success"
    assert "Deleted entry: ₹99 for Test Shopping Store" in del_res["confirmation_message"]
