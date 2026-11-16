"""Specialized parser for Indian UPI payment screenshots (GPay, PhonePe, Paytm, BHIM).

Implements Phase 4 Expansion from PRD Section 11:
"UPI screenshot parsing"
"""

import re
from typing import Any, Dict, Optional


UPI_INDICATORS = [
    r"\bupi\b",
    r"\bgpay\b",
    r"\bgoogle\s*pay\b",
    r"\bphonepe\b",
    r"\bpaytm\b",
    r"\bbhim\b",
    r"\bupi\s*transaction\s*id\b",
    r"\bupi\s*ref(?:erence)?\s*(?:no|id)?\b",
    r"\bdebited\s+from\b",
    r"\bpaid\s+to\b",
    r"\bpayment\s+to\b",
]


def detect_upi_screenshot(text: str) -> bool:
    """Detect whether text/OCR content originates from a UPI payment screenshot."""
    text_lower = text.lower()
    matches = sum(1 for pattern in UPI_INDICATORS if re.search(pattern, text_lower))
    return matches >= 1


def clean_upi_merchant(raw_merchant: str) -> str:
    """Clean recipient string from UPI screen (e.g. 'Paid to Swiggy Bangalore' -> 'Swiggy Bangalore')."""
    merchant = re.sub(
        r"^(?:paid\s+to|payment\s+to|paid\s+successfully\s+to|transfer\s+to)\s+",
        "",
        raw_merchant,
        flags=re.IGNORECASE,
    ).strip()
    return merchant or "UPI Merchant"


def extract_upi_details(text: str, raw_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract specialized UPI fields from OCR text and parsed data.

    Returns:
        Dict with 'is_upi', 'payment_method', 'cleaned_merchant', 'upi_ref_id', and 'notes'.
    """
    raw_data = raw_data or {}
    combined_text = f"{text} {raw_data.get('merchant', '')} {raw_data.get('notes', '')}"

    is_upi = detect_upi_screenshot(combined_text)

    # Extract UPI Transaction Reference Number (usually 12 digits e.g. 432109876543 or alphanumeric)
    upi_ref = None
    ref_match = re.search(
        r"(?:upi\s*(?:ref|transaction)\s*(?:no|id)?[:\s]+)([0-9A-Za-z]{10,22})",
        combined_text,
        re.IGNORECASE,
    )
    if ref_match:
        upi_ref = ref_match.group(1).strip()

    # Clean merchant name
    raw_merchant = raw_data.get("merchant", "") or text
    cleaned_merchant = clean_upi_merchant(raw_merchant)

    # Build formatted notes
    notes_parts = []
    if upi_ref:
        notes_parts.append(f"UPI Ref: {upi_ref}")
    if raw_data.get("notes"):
        notes_parts.append(raw_data["notes"])

    return {
        "is_upi": is_upi,
        "payment_method": "UPI" if is_upi else None,
        "merchant": cleaned_merchant,
        "upi_ref": upi_ref,
        "notes": " | ".join(notes_parts) if notes_parts else None,
    }

