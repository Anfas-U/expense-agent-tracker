"""Unit tests for Phase 4: State management, corrections, guardrails, and duplicate detection."""

import datetime as dt
import pytest
from src.agent.guardrails import Guardrails, GuardrailViolation
from src.agent.state import get_session_state
from src.tools.spreadsheet import append_row, update_row


def test_guardrails_blocks_money_transfer():
    """Verify guardrails block money movement or transfer attempts (PRD Section 7.3)."""
    with pytest.raises(GuardrailViolation):
        Guardrails.validate_user_intent("Please transfer money 500 to my account")

    with pytest.raises(GuardrailViolation):
        Guardrails.validate_user_intent("buy crypto 1000")

    # Regular expense queries and logs should NOT be blocked
    Guardrails.validate_user_intent("spent 450 on groceries")
    Guardrails.validate_user_intent("how much on food this month?")


def test_duplicate_detection_flow():
    """Verify duplicate detection warns user for identical amount, merchant, and date within 48h."""
    # Log original
    first_res = append_row(
        amount=350.0,
        merchant="Subway Sandwich",
        category="Dining",
        force=True,
    )
    assert first_res["status"] == "success"

    # Log identical again
    dup_res = append_row(
        amount=350.0,
        merchant="Subway Sandwich",
        category="Dining",
        force=False,
    )
    assert dup_res["status"] == "duplicate_warning"
    assert dup_res["is_duplicate"] is True
    assert "Possible duplicate found" in dup_res["confirmation_prompt"]

    # Verify pending state was staged
    session = get_session_state()
    assert session.pending_confirmation is not None
    assert session.pending_confirmation["type"] == "duplicate_append"

    # Forcing append should succeed
    forced_res = append_row(
        amount=350.0,
        merchant="Subway Sandwich",
        category="Dining",
        force=True,
    )
    assert forced_res["status"] == "success"


def test_relative_correction_state():
    """Verify 'change that to 1248' resolves last logged entry via session state (PRD 8.2)."""
    # 1. Log entry
    append_row(
        amount=1284.0,
        merchant="Big Bazaar",
        category="Groceries",
        force=True,
    )

    # 2. Relative correction
    up_res = update_row(new_amount=1248.0)
    assert up_res["status"] == "success"
    assert "to ₹1248" in up_res["confirmation_message"]

