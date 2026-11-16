"""Text expense parser tool for extracting structured expense records from natural language."""

import datetime as dt
import re
from typing import Any, Dict, List, Optional
import pytz
from dateutil.relativedelta import relativedelta, MO, TU, WE, TH, FR, SA, SU

from config.settings import settings
from src.models.schemas import ParsedExpenseItem
from src.tools.currency_converter import detect_currency_code, convert_to_base_currency
from src.tools.registry import register_tool


WEEKDAY_MAP = {
    "monday": MO(-1),
    "tuesday": TU(-1),
    "wednesday": WE(-1),
    "thursday": TH(-1),
    "friday": FR(-1),
    "saturday": SA(-1),
    "sunday": SU(-1),
}


def resolve_date_expression(text: str, base_date: dt.date) -> dt.date:
    """Parse relative expressions like 'yesterday', 'day before yesterday', 'last Friday'."""
    lower = text.lower()
    if "day before yesterday" in lower:
        return base_date - dt.timedelta(days=2)
    elif "yesterday" in lower:
        return base_date - dt.timedelta(days=1)
    elif "today" in lower:
        return base_date
    
    # Check 'last <weekday>'
    for day_name, weekday_const in WEEKDAY_MAP.items():
        if f"last {day_name}" in lower or f"past {day_name}" in lower:
            return base_date + relativedelta(weekday=weekday_const)
            
    # Check 'X days ago'
    days_ago_match = re.search(r"(\d+)\s+days?\s+ago", lower)
    if days_ago_match:
        days = int(days_ago_match.group(1))
        return base_date - dt.timedelta(days=days)

    return base_date


def clean_amount(val_str: str) -> float:
    """Clean string number into float (e.g. '1,284.50' -> 1284.50)."""
    clean = val_str.replace(",", "").strip()
    return float(clean)


def parse_single_clause(clause: str, tx_date: dt.date) -> Optional[ParsedExpenseItem]:
    """Extract amount, merchant, and payment method from a single clause."""
    clause = clause.strip()
    if not clause:
        return None

    # Currency pattern: ₹, rs, rs., inr, rupees, $, €, £, usd, eur, gbp, aed (all optional)
    curr_prefix = r"(?:(?:₹|rs\.?|inr|rupees|[\$€£]|usd|eur|gbp|aed)\s*)?"
    curr_suffix = r"(?:\s*(?:rs\.?|rupees|inr|usd|eur|gbp|aed))?"

    amount = None
    currency = settings.default_currency
    merchant = ""
    payment_method = None
    notes = None
    confidence = 1.0

    lower_clause = clause.lower()
    if "via upi" in lower_clause or "through upi" in lower_clause or "by upi" in lower_clause:
        payment_method = "UPI"
    elif "by card" in lower_clause or "credit card" in lower_clause or "debit card" in lower_clause:
        payment_method = "Card"
    elif "in cash" in lower_clause or "by cash" in lower_clause:
        payment_method = "Cash"

    # Pattern A: "spent 450 on groceries" / "paid 2000 to Ravi" / "bought 500 petrol"
    pat_action_amt = re.search(
        r"^(?:spent|paid|spend|transferred|bought|give|gave)\s+"
        + curr_prefix + r"([\d,]+(?:\.\d+)?)" + curr_suffix + r"\s+(?:on|for|to|at)?\s*(.+)$",
        clause,
        re.IGNORECASE,
    )

    # Pattern B: "auto 80" / "coffee 120" / "lunch 250"
    pat_item_amt = re.search(
        r"^(.+?)\s+" + curr_prefix + r"([\d,]+(?:\.\d+)?)" + curr_suffix + r"$",
        clause,
        re.IGNORECASE,
    )

    # Pattern C: "₹450 on groceries" / "450 rupees for lunch"
    pat_amt_for = re.search(
        r"^" + curr_prefix + r"([\d,]+(?:\.\d+)?)" + curr_suffix + r"\s+(?:on|for|at|to)\s*(.+)$",
        clause,
        re.IGNORECASE,
    )

    if pat_action_amt:
        amount = clean_amount(pat_action_amt.group(1))
        merchant = pat_action_amt.group(2).strip()
    elif pat_amt_for:
        amount = clean_amount(pat_amt_for.group(1))
        merchant = pat_amt_for.group(2).strip()
    elif pat_item_amt:
        merchant = pat_item_amt.group(1).strip()
        amount = clean_amount(pat_item_amt.group(2))
    else:
        # Fallback regex looking for any numeric value
        num_match = re.search(r"(?:₹|rs\.?|inr|rupees|[\$€£]|usd|eur|gbp|aed)?\s*([\d,]+(?:\.\d+)?)(?:\s*(?:rs\.?|rupees|inr|usd|eur|gbp|aed))?", clause, re.IGNORECASE)
        if num_match:
            try:
                amt_str = num_match.group(1)
                if amt_str:
                    amount = clean_amount(amt_str)
                    merchant = clause[:num_match.start()] + clause[num_match.end():]
                    merchant = re.sub(r"\b(spent|paid|on|for|to|at|rupees|rs|inr|usd|eur|gbp|aed)\b", "", merchant, flags=re.IGNORECASE).strip()
                    confidence = 0.7
            except Exception:
                pass

    if amount is None or amount <= 0:
        return None

    # Multi-Currency Conversion (PRD Section 4.1 FR-3 & Phase 4 Expansion)
    detected_curr = "INR"
    if re.search(r"(\$|\busd\b)", clause, re.IGNORECASE):
        detected_curr = "USD"
    elif re.search(r"(€|\beur\b)", clause, re.IGNORECASE):
        detected_curr = "EUR"
    elif re.search(r"(£|\bgbp\b)", clause, re.IGNORECASE):
        detected_curr = "GBP"
    elif re.search(r"\baed\b", clause, re.IGNORECASE):
        detected_curr = "AED"

    if detected_curr != "INR":
        amount, currency, notes = convert_to_base_currency(amount, detected_curr, settings.default_currency)

    # Clean up merchant string
    merchant = re.sub(r"\b(yesterday|today|day before yesterday|last \w+)\b", "", merchant, flags=re.IGNORECASE)
    merchant = re.sub(r"\b(via upi|by card|in cash|by cash)\b", "", merchant, flags=re.IGNORECASE)
    merchant = re.sub(r"[\$€£]", "", merchant)
    merchant = merchant.strip(" .,-:")
    if not merchant:
        merchant = "Expense"
        confidence = 0.5

    # If ambiguous "paid to <person>" without description, flag lower confidence for clarification
    if re.search(r"^(to\s+)?([A-Z][a-z]+|someone|friend)$", merchant, re.IGNORECASE):
        confidence = 0.55

    return ParsedExpenseItem(
        amount=amount,
        currency=currency,
        merchant=merchant,
        date=tx_date,
        payment_method=payment_method,
        notes=notes,
        confidence=confidence,
    )


@register_tool(
    name="parse_expense",
    description="Parse free-form natural language text to extract structured expense items (amount, merchant, date, payment method).",
)
def parse_expense(text: str, reference_date_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    """Parse a user text message into one or more structured expense items."""
    tz = pytz.timezone(settings.default_timezone)
    if reference_date_iso:
        try:
            base_date = dt.date.fromisoformat(reference_date_iso)
        except Exception:
            base_date = dt.datetime.now(tz).date()
    else:
        base_date = dt.datetime.now(tz).date()

    # Determine date from the text if relative date is present
    tx_date = resolve_date_expression(text, base_date)

    # Remove relative date keywords before clause splitting to prevent sticking to merchants
    clean_text = re.sub(r"\b(day before yesterday|yesterday|today|last \w+|\d+\s+days?\s+ago)\b", "", text, flags=re.IGNORECASE).strip()

    # Split multi-item clauses (by ',', ' and ', newline)
    split_regex = r"(?:,\s*(?=[a-zA-Z₹])|\s+and\s+|\n+)"
    raw_clauses = re.split(split_regex, clean_text)

    results: List[ParsedExpenseItem] = []
    for clause in raw_clauses:
        item = parse_single_clause(clause, tx_date)
        if item:
            results.append(item)

    # Fallback: if splitting failed to extract items, try whole string
    if not results:
        single_item = parse_single_clause(clean_text, tx_date)
        if single_item:
            results.append(single_item)

    return [item.model_dump(mode="json") for item in results]
