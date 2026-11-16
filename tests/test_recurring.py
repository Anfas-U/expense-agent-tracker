"""Unit tests for Recurring Expenses Management and Scheduler (PRD US-11)."""

import datetime as dt
from src.tools.recurring import manage_recurring, check_and_log_due_recurring
from src.storage.local_db import get_local_db


def test_add_and_list_recurring():
    """Verify adding recurring expense creates definition in database."""
    res = manage_recurring(
        action="add",
        title="House Rent",
        amount=15000.0,
        category="Rent",
        day_of_month=1,
    )
    assert res["status"] == "success"
    assert "rec_" in res["recurring_id"]

    list_res = manage_recurring(action="list")
    assert list_res["status"] == "success"
    titles = [i["title"] for i in list_res["recurring_expenses"]]
    assert "House Rent" in titles


def test_check_and_log_due_recurring():
    """Verify scheduler logs due recurring items and prevents duplicate monthly runs."""
    local_db = get_local_db()

    # Add a recurring expense due on 1st of month
    rec_id = local_db.add_recurring_expense(
        title="Netflix Premium",
        amount=649.0,
        category="Subscriptions",
        day_of_month=1,
    )

    test_date = dt.date(2026, 11, 11)  # Day 11 is past day 1

    # First check: should log
    logged = check_and_log_due_recurring(reference_date=test_date)
    matching = [x for x in logged if x["title"] == "Netflix Premium"]
    assert len(matching) == 1
    assert matching[0]["amount"] == 649.0

    # Second check in same month: should skip already logged
    logged_again = check_and_log_due_recurring(reference_date=test_date)
    matching_again = [x for x in logged_again if x["title"] == "Netflix Premium"]
    assert len(matching_again) == 0  # Idempotent: no duplicate logs in same month


def test_delete_recurring():
    """Verify deactivation of recurring expense."""
    add_res = manage_recurring(
        action="add",
        title="Temporary Gym",
        amount=1000.0,
        category="Health",
    )
    rec_id = add_res["recurring_id"]

    del_res = manage_recurring(action="delete", recurring_id=rec_id)
    assert del_res["status"] == "success"

    list_res = manage_recurring(action="list")
    ids = [i["id"] for i in list_res["recurring_expenses"]]
    assert rec_id not in ids

