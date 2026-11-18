"""Recurring expenses management and automated monthly scheduler (PRD US-11)."""

import datetime as dt
from typing import Any, Dict, List, Optional
import pytz

from config.settings import settings
from src.storage.local_db import get_local_db
from src.tools.categorizer import categorize
from src.tools.registry import register_tool
from src.tools.spreadsheet import append_row


@register_tool(
    name="manage_recurring",
    description="Manage recurring expenses (rent, subscriptions, utilities) that recur monthly.",
)
def manage_recurring(
    action: str,  # 'add', 'list', 'check_due', 'delete'
    title: Optional[str] = None,
    amount: Optional[float] = None,
    category: Optional[str] = None,
    day_of_month: int = 1,
    notes: Optional[str] = None,
    recurring_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Manage recurring monthly expenses (PRD US-11).

    Args:
        action: 'add', 'list', 'check_due', or 'delete'.
        title: Title/merchant of the expense (e.g. 'House Rent', 'Netflix').
        amount: Recurring monetary amount.
        category: Expense category.
        day_of_month: Day of the month on which it is due (1-28).
        notes: Optional notes.
        recurring_id: ID for deletion.

    Returns:
        Status and results of operation.
    """
    local_db = get_local_db()

    if action == "add":
        if not title or not amount or amount <= 0:
            return {"status": "error", "message": "Title and positive amount are required."}

        final_category = category
        if not final_category:
            cat_res = categorize(merchant=title, notes=notes)
            final_category = cat_res["category"]

        rec_id = local_db.add_recurring_expense(
            title=title,
            amount=amount,
            category=final_category,
            day_of_month=min(max(day_of_month, 1), 28),
            notes=notes,
        )
        return {
            "status": "success",
            "recurring_id": rec_id,
            "message": f"Added recurring expense '{title}' of ₹{amount:,.0f} due on day {day_of_month} of every month.",
        }

    elif action == "list":
        items = local_db.get_active_recurring_expenses()
        return {
            "status": "success",
            "count": len(items),
            "recurring_expenses": items,
        }

    elif action == "delete":
        if not recurring_id:
            return {"status": "error", "message": "recurring_id is required to delete."}
        ok = local_db.delete_recurring_expense(recurring_id)
        return {
            "status": "success" if ok else "not_found",
            "message": f"Recurring expense {recurring_id} deactivated." if ok else "Not found.",
        }

    elif action == "check_due":
        logged_items = check_and_log_due_recurring()
        return {
            "status": "success",
            "logged_count": len(logged_items),
            "logged_items": logged_items,
        }

    return {"status": "error", "message": f"Unknown action: {action}"}


def check_and_log_due_recurring(reference_date: Optional[dt.date] = None) -> List[Dict[str, Any]]:
    """Check all active recurring expenses and auto-log any due for the current month."""
    tz = pytz.timezone(get_local_db().get_user_settings()["default_timezone"])
    today = reference_date or dt.datetime.now(tz).date()
    current_month_prefix = today.strftime("%Y-%m")

    local_db = get_local_db()
    active_items = local_db.get_active_recurring_expenses()

    logged_results = []
    for item in active_items:
        last_logged = item.get("last_logged_date") or ""
        # Check if already logged this month
        if last_logged.startswith(current_month_prefix):
            continue

        # Check if due day has arrived
        due_day = item.get("day_of_month", 1)
        if today.day >= due_day:
            due_date = dt.date(today.year, today.month, min(due_day, 28))
            append_res = append_row(
                amount=float(item["amount"]),
                merchant=item["title"],
                category=item["category"],
                date_str=due_date.isoformat(),
                notes=f"Recurring auto-entry. {item.get('notes') or ''}".strip(),
                source="recurring",
                force=True,  # Bypass duplicate check for verified recurring schedule
            )
            local_db.mark_recurring_logged(item["id"], due_date.isoformat())
            logged_results.append({
                "title": item["title"],
                "amount": item["amount"],
                "category": item["category"],
                "date": due_date.isoformat(),
                "entry_id": append_res.get("entry_id"),
            })

    return logged_results

