"""Guardrails, safety validations, and duplicate detection engine."""

import datetime as dt
import re
from typing import Dict, List, Optional
from src.models.schemas import ExpenseEntry
from src.storage.local_db import get_local_db


class GuardrailViolation(Exception):
    """Exception raised when a guardrail rule is violated."""
    pass


class Guardrails:
    """Implements safety policies defined in PRD Section 7.3 & FR-14."""

    FORBIDDEN_INTENTS = [
        r"\b(transfer\s+money|send\s+money|pay\s+bill\s+now|make\s+payment|buy\s+crypto|wire\s+cash)\b",
        r"\b(invest|buy\s+shares|transfer\s+to\s+account)\b",
    ]

    @classmethod
    def validate_user_intent(cls, user_text: str):
        """Block any attempt to initiate money movements or financial transactions."""
        text_lower = user_text.lower()
        for pattern in cls.FORBIDDEN_INTENTS:
            if re.search(pattern, text_lower):
                raise GuardrailViolation(
                    "I am an expense tracking agent and cannot execute financial payments, transfers, or transactions."
                )

    @classmethod
    def check_duplicate(
        cls,
        amount: float,
        merchant: str,
        entry_date: dt.date,
        window_days: int = 2,
    ) -> Optional[ExpenseEntry]:
        """Detect probable duplicate entries (same amount, merchant, and date within window)."""
        local_db = get_local_db()
        recent_expenses = local_db.get_all_expenses(limit=50)

        merchant_clean = merchant.lower().strip()
        for exp in recent_expenses:
            # Check amount match
            if abs(exp.amount - amount) < 0.01:
                # Check merchant similarity
                exp_merchant = exp.merchant.lower().strip()
                if exp_merchant == merchant_clean or exp_merchant in merchant_clean or merchant_clean in exp_merchant:
                    # Check date proximity (within 48 hours)
                    days_diff = abs((exp.date - entry_date).days)
                    if days_diff <= window_days:
                        return exp
        return None

    @classmethod
    def requires_bulk_delete_confirmation(cls, delete_all: bool = False, count: int = 1) -> bool:
        """Destructive action policy: bulk deletion requires explicit confirmation."""
        return delete_all or count > 1

