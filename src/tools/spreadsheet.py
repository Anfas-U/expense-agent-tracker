"""Spreadsheet tools for adding, modifying, and deleting expense rows."""

import datetime as dt
from typing import Any, Dict, List, Optional
from src.agent.guardrails import Guardrails
from src.agent.state import get_session_state
from src.models.schemas import ExpenseEntry, ExpenseSource
from src.storage.local_db import get_local_db
from src.storage.sheets_client import get_spreadsheet_storage
from src.tools.categorizer import get_categorizer, categorize
from src.tools.registry import register_tool


@register_tool(
    name="append_row",
    description="Save an expense record to the spreadsheet and local database.",
)
def append_row(
    amount: float,
    merchant: str,
    category: Optional[str] = None,
    date_str: Optional[str] = None,
    payment_method: Optional[str] = None,
    notes: Optional[str] = None,
    source: str = "text",
    receipt_url: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Append a new expense entry with duplicate detection.

    Args:
        amount: Monetary amount spent.
        merchant: Name of merchant, vendor, or description.
        category: Optional category name.
        date_str: Date in YYYY-MM-DD format. Defaults to today.
        payment_method: Optional payment method (e.g. UPI, Cash, Card).
        notes: Optional extra notes.
        source: Source channel ('text', 'receipt', 'voice').
        receipt_url: Optional link or path to receipt photo.
        force: If True, bypass duplicate check warning.

    Returns:
        Dictionary confirming logged status or duplicate warning.
    """
    local_db = get_local_db()
    sheets = get_spreadsheet_storage()
    session = get_session_state()

    # Determine date
    if date_str:
        try:
            entry_date = dt.date.fromisoformat(date_str)
        except Exception:
            entry_date = dt.date.today()
    else:
        entry_date = dt.date.today()

    # FR-14: Duplicate Detection (unless forced)
    if not force:
        dup = Guardrails.check_duplicate(amount=amount, merchant=merchant, entry_date=entry_date)
        if dup:
            dup_date_str = dup.date.strftime("%d %b")
            amt_display = f"₹{int(amount) if amount == int(amount) else amount}"
            prompt = f"Possible duplicate found: {amt_display} at {merchant} on {dup_date_str} already logged. Do you still want to save it?"
            
            session.stage_pending_action(
                "duplicate_append",
                {
                    "amount": amount,
                    "merchant": merchant,
                    "category": category,
                    "date_str": entry_date.isoformat(),
                    "payment_method": payment_method,
                    "notes": notes,
                    "source": source,
                    "receipt_url": receipt_url,
                },
            )
            return {
                "status": "duplicate_warning",
                "is_duplicate": True,
                "existing_entry_id": dup.id,
                "confirmation_prompt": prompt,
            }

    # Auto-categorize if needed
    final_category = category
    confidence = 1.0
    if not final_category or final_category.lower() in ("other", "none", "unknown"):
        cat_result = categorize(merchant=merchant, notes=notes)
        final_category = cat_result["category"]
        confidence = cat_result["confidence"]

    entry_source = ExpenseSource.TEXT
    if source.lower() == "receipt":
        entry_source = ExpenseSource.RECEIPT
    elif source.lower() == "voice":
        entry_source = ExpenseSource.VOICE

    entry = ExpenseEntry(
        amount=amount,
        merchant=merchant,
        category=final_category,
        date=entry_date,
        payment_method=payment_method,
        notes=notes,
        source=entry_source,
        receipt_url=receipt_url,
        confidence=confidence,
    )

    # 1. Save to SQLite
    local_db.insert_expense(entry, synced=sheets.is_google_connected)

    # 2. Save to Google Sheets / Local CSV
    sheets.append_expense(entry)

    # Date display
    today = dt.date.today()
    if entry.date == today:
        date_display = "today"
    elif entry.date == today - dt.timedelta(days=1):
        date_display = "yesterday"
    else:
        date_display = entry.date.strftime("%d %b")

    amt_display = f"₹{int(entry.amount) if entry.amount == int(entry.amount) else entry.amount}"
    confirmation = f"Logged {amt_display}, {entry.category}, {date_display}."

    # Record in short-term session memory
    session.record_operation("logged", confirmation, entry.id)

    return {
        "status": "success",
        "entry_id": entry.id,
        "amount": entry.amount,
        "currency": entry.currency,
        "merchant": entry.merchant,
        "category": entry.category,
        "date": entry.date.isoformat(),
        "confirmation_message": confirmation,
    }


@register_tool(
    name="update_row",
    description="Update or correct a previously logged expense entry (e.g. 'change that to 540' or change category).",
)
def update_row(
    expense_id: Optional[str] = None,
    new_amount: Optional[float] = None,
    new_category: Optional[str] = None,
    new_merchant: Optional[str] = None,
    new_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Update fields of an expense row."""
    local_db = get_local_db()
    sheets = get_spreadsheet_storage()
    session = get_session_state()

    # Find target entry
    target_id = expense_id or session.get_last_expense_id()
    if target_id:
        entry = local_db.get_expense(target_id)
    else:
        entry = local_db.get_last_logged_expense()

    if not entry:
        return {"status": "error", "message": "No previous expense found to update."}

    updates: Dict[str, Any] = {}
    if new_amount is not None and new_amount > 0:
        updates["amount"] = new_amount
    if new_category:
        updates["category"] = new_category
        # Learn from correction (PRD FR-9)
        local_db.save_merchant_rule(entry.merchant, new_category)
        get_categorizer().add_user_rule(entry.merchant, new_category)
    if new_merchant:
        updates["merchant"] = new_merchant
    if new_date:
        updates["date"] = new_date

    if not updates:
        return {"status": "no_changes", "message": "No updates specified."}

    # Apply updates
    local_db.update_expense(entry.id, updates)
    sheets.update_expense_row(entry.id, updates)

    updated_items = []
    if "amount" in updates:
        updated_items.append(f"to ₹{int(updates['amount']) if updates['amount'] == int(updates['amount']) else updates['amount']}")
    if "category" in updates:
        updated_items.append(f"category to {updates['category']}")
    if "merchant" in updates:
        updated_items.append(f"merchant to {updates['merchant']}")

    change_desc = ", ".join(updated_items)
    confirmation = f"Updated {change_desc}."

    session.record_operation("updated", confirmation, entry.id)

    return {
        "status": "success",
        "entry_id": entry.id,
        "updates": updates,
        "confirmation_message": confirmation,
    }


@register_tool(
    name="delete_row",
    description="Delete a previously logged expense entry (e.g. 'delete the last one').",
)
def delete_row(expense_id: Optional[str] = None) -> Dict[str, Any]:
    """Delete an expense row."""
    local_db = get_local_db()
    sheets = get_spreadsheet_storage()
    session = get_session_state()

    target_id = expense_id or session.get_last_expense_id()
    if target_id:
        entry = local_db.get_expense(target_id)
    else:
        entry = local_db.get_last_logged_expense()

    if not entry:
        return {"status": "error", "message": "No previous expense found to delete."}

    local_db.delete_expense(entry.id)
    sheets.delete_expense_row(entry.id)

    amt_str = f"₹{int(entry.amount) if entry.amount == int(entry.amount) else entry.amount}"
    confirmation = f"Deleted entry: {amt_str} for {entry.merchant} ({entry.category})."

    session.record_operation("deleted", confirmation, entry.id)

    return {
        "status": "success",
        "entry_id": entry.id,
        "deleted_entry": {
            "amount": entry.amount,
            "merchant": entry.merchant,
            "category": entry.category,
        },
        "confirmation_message": confirmation,
    }
