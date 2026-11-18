"""Deterministic query and financial insights engine.

Implements PRD Section 4.5 & Section 7.3:
"Compute answers from the spreadsheet data using actual calculation, not
estimation by the model. Numbers in replies always come from tool results."
"""

import calendar
import datetime as dt
from typing import Any, Dict, List, Optional
import pytz

from config.settings import settings
from src.models.schemas import ExpenseEntry, ExpenseQueryResult
from src.storage.local_db import get_local_db
from src.tools.registry import register_tool


MONTH_NAME_TO_INT = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def resolve_time_period(
    time_period: Optional[str],
    start_date_iso: Optional[str],
    end_date_iso: Optional[str],
    base_date: dt.date,
) -> tuple[dt.date, dt.date, Optional[tuple[dt.date, dt.date]]]:
    """Resolve time range and corresponding previous comparison period."""
    if start_date_iso and end_date_iso:
        start_d = dt.date.fromisoformat(start_date_iso)
        end_d = dt.date.fromisoformat(end_date_iso)
        days = (end_d - start_d).days + 1
        prev_start = start_d - dt.timedelta(days=days)
        prev_end = start_d - dt.timedelta(days=1)
        return start_d, end_d, (prev_start, prev_end)

    tp = (time_period or "this_month").lower().strip()

    if "last month" in tp or tp == "last_month":
        # First day of last month
        year = base_date.year
        month = base_date.month - 1
        if month == 0:
            month = 12
            year -= 1
        last_day = calendar.monthrange(year, month)[1]
        start_d = dt.date(year, month, 1)
        end_d = dt.date(year, month, last_day)

        prev_month = month - 1 if month > 1 else 12
        prev_year = year if month > 1 else year - 1
        prev_last_day = calendar.monthrange(prev_year, prev_month)[1]
        prev_period = (dt.date(prev_year, prev_month, 1), dt.date(prev_year, prev_month, prev_last_day))
        return start_d, end_d, prev_period

    elif "this week" in tp or tp == "this_week":
        start_d = base_date - dt.timedelta(days=base_date.weekday())
        end_d = base_date
        prev_start = start_d - dt.timedelta(days=7)
        prev_end = start_d - dt.timedelta(days=1)
        return start_d, end_d, (prev_start, prev_end)

    elif "today" in tp:
        return base_date, base_date, (base_date - dt.timedelta(days=1), base_date - dt.timedelta(days=1))

    # Check named months e.g. "october", "oct"
    for m_name, m_num in MONTH_NAME_TO_INT.items():
        if m_name in tp:
            year = base_date.year
            if m_num > base_date.month:
                year -= 1
            last_day = calendar.monthrange(year, m_num)[1]
            start_d = dt.date(year, m_num, 1)
            end_d = dt.date(year, m_num, last_day)

            prev_m = m_num - 1 if m_num > 1 else 12
            prev_y = year if m_num > 1 else year - 1
            prev_last = calendar.monthrange(prev_y, prev_m)[1]
            return start_d, end_d, (dt.date(prev_y, prev_m, 1), dt.date(prev_y, prev_m, prev_last))

    # Default: this_month
    start_d = dt.date(base_date.year, base_date.month, 1)
    end_d = base_date

    # Compare with same days of last month
    prev_month = base_date.month - 1 if base_date.month > 1 else 12
    prev_year = base_date.year if base_date.month > 1 else base_date.year - 1
    prev_last_day = min(base_date.day, calendar.monthrange(prev_year, prev_month)[1])
    prev_period = (dt.date(prev_year, prev_month, 1), dt.date(prev_year, prev_month, prev_last_day))

    return start_d, end_d, prev_period


@register_tool(
    name="query_expenses",
    description="Calculate exact expense totals, category breakdowns, and monthly trends using deterministic code math.",
)
def query_expenses(
    category: Optional[str] = None,
    time_period: Optional[str] = "this_month",
    merchant: Optional[str] = None,
    start_date_iso: Optional[str] = None,
    end_date_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Query expenses and perform exact calculations without model estimation.

    Args:
        category: Category name or alias (e.g. 'Dining', 'Groceries', or 'food' for both).
        time_period: Natural time period ('this_month', 'last_month', 'this_week', 'october', etc.).
        merchant: Optional filter by merchant name.
        start_date_iso: Explicit start date (YYYY-MM-DD).
        end_date_iso: Explicit end date (YYYY-MM-DD).

    Returns:
        Exact calculated metrics including total amount, breakdown, and percentage changes.
    """
    local_db = get_local_db()
    user_settings = local_db.get_user_settings()
    tz = pytz.timezone(user_settings["default_timezone"])
    base_date = dt.datetime.now(tz).date()

    start_date, end_date, prev_period = resolve_time_period(
        time_period, start_date_iso, end_date_iso, base_date
    )

    all_expenses = local_db.get_all_expenses(limit=1000)

    # Category normalization: "food" -> ["Groceries", "Dining"]
    target_categories = None
    if category:
        cat_lower = category.lower().strip()
        if cat_lower in ("food", "food & dining", "food and dining"):
            target_categories = ["Groceries", "Dining"]
        else:
            target_categories = [cat_lower]

    # Filter current period entries
    current_entries: List[ExpenseEntry] = []
    for exp in all_expenses:
        if start_date <= exp.date <= end_date:
            if target_categories:
                if exp.category.lower() not in [c.lower() for c in target_categories]:
                    continue
            if merchant and merchant.lower() not in exp.merchant.lower():
                continue
            current_entries.append(exp)

    # Filter previous period entries for comparison
    prev_total = 0.0
    if prev_period:
        p_start, p_end = prev_period
        for exp in all_expenses:
            if p_start <= exp.date <= p_end:
                if target_categories:
                    if exp.category.lower() not in [c.lower() for c in target_categories]:
                        continue
                if merchant and merchant.lower() not in exp.merchant.lower():
                    continue
                prev_total += exp.amount

    # Compute metrics deterministically
    total_amount = sum(e.amount for e in current_entries)
    entry_count = len(current_entries)

    # Category breakdown
    category_breakdown: Dict[str, float] = {}
    for e in current_entries:
        category_breakdown[e.category] = round(
            category_breakdown.get(e.category, 0.0) + e.amount, 2
        )

    # Percentage change
    pct_change = None
    if prev_total > 0:
        pct_change = round(((total_amount - prev_total) / prev_total) * 100, 1)

    # Format human-friendly summary text (PRD Section 8.2 example)
    amt_fmt = f"₹{int(total_amount) if total_amount == int(total_amount) else total_amount:,.2f}".replace(".00", "")
    
    parts = []
    if len(category_breakdown) > 1:
        breakdown_str = ", ".join(
            [f"{cat} (₹{int(amt) if amt == int(amt) else amt:,.0f})" for cat, amt in category_breakdown.items()]
        )
        parts.append(f"{amt_fmt} across {breakdown_str}")
    else:
        parts.append(f"{amt_fmt}")

    if pct_change is not None:
        direction = "up" if pct_change >= 0 else "down"
        parts.append(f"{direction} {abs(pct_change)}% from last period")

    formatted_summary = ", ".join(parts) + "."

    # Budget alerts check
    budgets = local_db.get_all_budgets()
    budget_alerts = []
    for cat, spent in category_breakdown.items():
        if cat in budgets:
            limit = budgets[cat]
            if limit > 0:
                pct_used = (spent / limit) * 100
                if pct_used >= 100:
                    budget_alerts.append(f"Alert: {cat} has exceeded 100% of its ₹{limit:.0f} budget (₹{spent:.0f} spent).")
                elif pct_used >= 80:
                    budget_alerts.append(f"Notice: {cat} is at {pct_used:.0f}% of its ₹{limit:.0f} budget.")

    return {
        "status": "success",
        "total_amount": round(total_amount, 2),
        "entry_count": entry_count,
        "currency": user_settings["default_currency"],
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "category_breakdown": category_breakdown,
        "previous_period_total": round(prev_total, 2) if prev_period else None,
        "percentage_change": pct_change,
        "budget_alerts": budget_alerts,
        "formatted_summary": formatted_summary,
    }

