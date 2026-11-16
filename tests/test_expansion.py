"""Unit tests for Phase 4 Expansion Features: UPI Screenshots, Voice Notes, and Multi-Currency."""

from pathlib import Path
from src.tools.upi_parser import detect_upi_screenshot, clean_upi_merchant, extract_upi_details
from src.tools.currency_converter import convert_to_base_currency, detect_currency_code
from src.tools.voice_transcriber import process_voice_expense
from src.tools.parser import parse_expense


def test_upi_screenshot_detection_and_cleaning():
    """Verify UPI screenshot detection and recipient cleaning."""
    gpay_text = "Google Pay: Paid to Swiggy UPI Ref: 432109876543"
    assert detect_upi_screenshot(gpay_text) is True

    details = extract_upi_details(gpay_text, {"merchant": "Paid to Swiggy"})
    assert details["is_upi"] is True
    assert details["payment_method"] == "UPI"
    assert details["merchant"] == "Swiggy"
    assert details["upi_ref"] == "432109876543"
    assert "UPI Ref: 432109876543" in details["notes"]


def test_multi_currency_conversion():
    """Verify conversion of foreign currencies (USD, EUR) to base INR."""
    # Direct currency conversion
    conv_usd, base_curr, note_usd = convert_to_base_currency(25.0, "USD", "INR")
    assert conv_usd == 2175.0  # 25 * 87
    assert base_curr == "INR"
    assert "Original: USD 25.00" in note_usd

    conv_eur, _, note_eur = convert_to_base_currency(50.0, "EUR", "INR")
    assert conv_eur == 4600.0  # 50 * 92
    assert "Original: EUR 50.00" in note_eur


def test_parse_expense_with_foreign_currency():
    """Verify parser extracts and converts '$25 for dinner'."""
    results = parse_expense("spent $25 for dinner")
    assert len(results) == 1
    assert results[0]["amount"] == 2175.0
    assert results[0]["currency"] == "INR"
    assert "dinner" in results[0]["merchant"].lower()
    assert "Original: USD 25.00" in results[0]["notes"]


def test_voice_note_transcription_tool(tmp_path: Path):
    """Verify voice note processing returns parsed expense items."""
    dummy_voice_path = tmp_path / "voice_sample.ogg"
    dummy_voice_path.write_bytes(b"dummy_audio_bytes")

    result = process_voice_expense(str(dummy_voice_path))
    assert result["status"] == "success"
    assert len(result["parsed_items"]) >= 1
    assert result["parsed_items"][0]["amount"] == 350.0
    assert result["parsed_items"][0]["payment_method"] == "UPI"

