"""Unit tests for the tri-level categorization engine."""

from src.tools.categorizer import categorize, get_categorizer


def test_merchant_exact_rule():
    """Verify configured merchant rules take priority."""
    result = categorize(merchant="Swiggy")
    assert result["category"] == "Dining"
    assert result["strategy"] == "merchant_rule"

    result_uber = categorize(merchant="Uber")
    assert result_uber["category"] == "Transport"


def test_keyword_match():
    """Verify keyword mapping for unknown merchants."""
    result = categorize(merchant="Local Supermarket", description="vegetables and milk")
    assert result["category"] == "Groceries"
    assert result["strategy"] == "keyword_match"

    result_petrol = categorize(merchant="Indian Oil", description="petrol fill")
    assert result_petrol["category"] == "Fuel"


def test_learned_user_rule():
    """Verify user learned rules override default rules (PRD FR-9)."""
    categorizer = get_categorizer()
    categorizer.add_user_rule("my custom store", "Shopping")

    result = categorize(merchant="my custom store")
    assert result["category"] == "Shopping"
    assert result["strategy"] == "user_learned_rule"
    assert result["confidence"] == 1.0


def test_fallback_ambiguous():
    """Verify fallback when no rules match."""
    result = categorize(merchant="Ravi", notes="random payment")
    assert result["category"] == "Other"
    assert result["needs_clarification"] is True

