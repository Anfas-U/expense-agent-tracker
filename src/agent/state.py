"""Conversational state management and recent operations memory."""

import datetime as dt
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.models.schemas import ExpenseEntry
from src.storage.local_db import get_local_db


class OperationRecord(BaseModel):
    """History of recent actions taken by the user/agent."""
    action_type: str  # 'logged', 'updated', 'deleted', 'staged_receipt'
    expense_id: Optional[str] = None
    summary: str
    timestamp: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class SessionState:
    """Manages short-term conversational context across multi-turn dialogs."""

    def __init__(self, max_history: int = 10):
        self.max_history = max_history
        self.recent_operations: List[OperationRecord] = []
        self.pending_confirmation: Optional[Dict[str, Any]] = None

    def record_operation(self, action_type: str, summary: str, expense_id: Optional[str] = None):
        """Record an operation to short-term memory."""
        op = OperationRecord(action_type=action_type, expense_id=expense_id, summary=summary)
        self.recent_operations.append(op)
        if len(self.recent_operations) > self.max_history:
            self.recent_operations.pop(0)

    def get_last_expense_id(self) -> Optional[str]:
        """Get ID of the most recently logged or modified expense."""
        for op in reversed(self.recent_operations):
            if op.expense_id:
                return op.expense_id
        # Fallback to local DB
        last_entry = get_local_db().get_last_logged_expense()
        return last_entry.id if last_entry else None

    def stage_pending_action(self, action_type: str, payload: Dict[str, Any]):
        """Stage an action that requires user confirmation."""
        self.pending_confirmation = {
            "type": action_type,
            "payload": payload,
            "staged_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    def pop_pending_action(self) -> Optional[Dict[str, Any]]:
        """Retrieve and clear pending confirmation action."""
        action = self.pending_confirmation
        self.pending_confirmation = None
        return action

    def clear(self):
        """Clear memory."""
        self.recent_operations.clear()
        self.pending_confirmation = None


_session_state_instance = SessionState()


def get_session_state() -> SessionState:
    """Get singleton SessionState instance."""
    return _session_state_instance

