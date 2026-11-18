"""Multi-currency detection, normalization, and exchange rate conversion."""

from typing import Dict, Optional, Tuple
from config.settings import settings
from src.storage.local_db import get_local_db


def _default_currency() -> str:
    return get_local_db().get_user_settings()["default_currency"]


# Default reference rates against INR (can be updated dynamically)
DEFAULT_RATES_TO_INR: Dict[str, float] = {
    "INR": 1.0,
    "USD": 87.0,
    "EUR": 92.0,
    "GBP": 110.0,
    "AED": 23.7,
    "CAD": 62.0,
    "AUD": 56.0,
    "SGD": 65.0,
    "JPY": 0.58,
}

CURRENCY_SYMBOL_MAP: Dict[str, str] = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "rupees": "INR",
    "usd": "USD",
    "eur": "EUR",
    "gbp": "GBP",
    "aed": "AED",
    "cad": "CAD",
    "aud": "AUD",
    "sgd": "SGD",
}


def detect_currency_code(raw_symbol_or_code: str) -> str:
    """Normalize raw currency symbol or text into ISO code."""
    clean = raw_symbol_or_code.lower().strip()
    return CURRENCY_SYMBOL_MAP.get(clean, _default_currency())


def convert_to_base_currency(
    amount: float,
    currency_code: str,
    base_currency: Optional[str] = None,
) -> Tuple[float, str, Optional[str]]:
    """Convert amount to user's base currency if foreign, and return conversion note.

    Returns:
        (converted_amount, base_currency, note)
    """
    base = base_currency or _default_currency()
    curr = currency_code.upper().strip()

    if curr == base:
        return amount, base, None

    currency_rate = DEFAULT_RATES_TO_INR.get(curr)
    base_rate = DEFAULT_RATES_TO_INR.get(base)
    if currency_rate is not None and base_rate is not None:
        rate = currency_rate / base_rate
        converted = round(amount * rate, 2)
        conversion_note = f"Original: {curr} {amount:,.2f} (rate {rate:.4f})"
        return converted, base, conversion_note

    # Fallback if unknown currency
    return amount, curr, None

