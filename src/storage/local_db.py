"""Local persistence layer using SQLite for settings, cache, rules, and offline resilience."""

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
from config.settings import settings
from src.models.schemas import ExpenseEntry, ExpenseSource
from src.storage.web_context import current_database


class LocalDatabase:
    """Manages SQLite storage for expenses, user settings, merchant rules, and session state."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.database_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Create tables if they do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Expenses table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS expenses (
                    id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL,
                    merchant TEXT NOT NULL,
                    category TEXT NOT NULL,
                    payment_method TEXT,
                    notes TEXT,
                    source TEXT NOT NULL,
                    receipt_url TEXT,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    synced_to_sheet INTEGER DEFAULT 0
                )
            """)

            # Merchant to category rules (learned from corrections)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS merchant_rules (
                    merchant TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # User category budgets
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS budgets (
                    category TEXT PRIMARY KEY,
                    monthly_limit REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Session state (e.g. last inserted expense id for 'change that')
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS session_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Recurring expenses (PRD US-11)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recurring_expenses (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    amount REAL NOT NULL,
                    category TEXT NOT NULL,
                    day_of_month INTEGER NOT NULL DEFAULT 1,
                    last_logged_date TEXT,
                    is_active INTEGER DEFAULT 1,
                    notes TEXT
                )
            """)
            conn.commit()

    def insert_expense(self, entry: ExpenseEntry, synced: bool = False) -> str:
        """Insert a new expense into local database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO expenses (
                    id, date, amount, currency, merchant, category,
                    payment_method, notes, source, receipt_url, confidence,
                    created_at, synced_to_sheet
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.date.isoformat(),
                    entry.amount,
                    entry.currency,
                    entry.merchant,
                    entry.category,
                    entry.payment_method or "",
                    entry.notes or "",
                    entry.source.value if isinstance(entry.source, ExpenseSource) else str(entry.source),
                    entry.receipt_url or "",
                    entry.confidence,
                    entry.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    1 if synced else 0,
                ),
            )
            # Record last logged expense in session state
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            cursor.execute(
                "INSERT OR REPLACE INTO session_state (key, value, updated_at) VALUES (?, ?, ?)",
                ("last_logged_expense_id", entry.id, now_iso),
            )
            conn.commit()
        return entry.id

    def get_expense(self, expense_id: str) -> Optional[ExpenseEntry]:
        """Fetch expense by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_entry(row)

    def get_last_logged_expense(self) -> Optional[ExpenseEntry]:
        """Get the most recently logged expense for contextual corrections."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM session_state WHERE key = 'last_logged_expense_id'")
            state_row = cursor.fetchone()
            if state_row and state_row["value"]:
                return self.get_expense(state_row["value"])
            # Fallback to order by created_at DESC
            cursor.execute("SELECT * FROM expenses ORDER BY created_at DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                return self._row_to_entry(row)
        return None

    def update_expense(self, expense_id: str, updates: Dict[str, Any]) -> bool:
        """Update fields of an existing expense."""
        allowed_fields = ["date", "amount", "merchant", "category", "payment_method", "notes"]
        set_clauses = []
        values = []
        for field, val in updates.items():
            if field in allowed_fields:
                if field == "date" and isinstance(val, (dt.date, dt.datetime)):
                    val = val.isoformat()
                set_clauses.append(f"{field} = ?")
                values.append(val)

        if not set_clauses:
            return False

        values.append(expense_id)
        query = f"UPDATE expenses SET {', '.join(set_clauses)} WHERE id = ?"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(values))
            conn.commit()
            return cursor.rowcount > 0

    def delete_expense(self, expense_id: str) -> bool:
        """Delete an expense by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
            conn.commit()
            return cursor.rowcount > 0

    def get_all_expenses(self, limit: Optional[int] = 500) -> List[ExpenseEntry]:
        """Fetch recent expenses."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if limit is None:
                cursor.execute("SELECT * FROM expenses ORDER BY date DESC, created_at DESC")
            else:
                cursor.execute(
                    "SELECT * FROM expenses ORDER BY date DESC, created_at DESC LIMIT ?",
                    (limit,),
                )
            rows = cursor.fetchall()
            return [self._row_to_entry(r) for r in rows]

    def save_merchant_rule(self, merchant: str, category: str):
        """Store or update a learned merchant-to-category rule (PRD FR-9)."""
        clean_merchant = merchant.lower().strip()
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO merchant_rules (merchant, category, updated_at) VALUES (?, ?, ?)",
                (clean_merchant, category, now_iso),
            )
            conn.commit()

    def get_all_merchant_rules(self) -> Dict[str, str]:
        """Retrieve all learned merchant rules."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT merchant, category FROM merchant_rules")
            rows = cursor.fetchall()
            return {r["merchant"]: r["category"] for r in rows}

    def set_budget(self, category: str, monthly_limit: float):
        """Set a monthly budget for a category."""
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO budgets (category, monthly_limit, updated_at) VALUES (?, ?, ?)",
                (category, monthly_limit, now_iso),
            )
            conn.commit()

    def get_all_budgets(self) -> Dict[str, float]:
        """Retrieve all monthly budgets."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT category, monthly_limit FROM budgets")
            rows = cursor.fetchall()
            return {r["category"]: float(r["monthly_limit"]) for r in rows}

    def delete_budget(self, category: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM budgets WHERE category = ?", (category,))
            conn.commit()
            return cursor.rowcount > 0

    def get_user_settings(self) -> Dict[str, str]:
        return {
            "default_currency": settings.default_currency,
            "default_timezone": settings.default_timezone,
        }

    def set_user_setting(self, key: str, value: str) -> None:
        if key not in {"default_currency", "default_timezone"}:
            raise ValueError(f"Unsupported user setting: {key}")

    def add_recurring_expense(
        self, title: str, amount: float, category: str, day_of_month: int = 1, notes: Optional[str] = None
    ) -> str:
        """Add a recurring expense definition (PRD US-11)."""
        import uuid
        rec_id = f"rec_{uuid.uuid4().hex[:8]}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO recurring_expenses (id, title, amount, category, day_of_month, notes, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (rec_id, title, amount, category, day_of_month, notes or ""),
            )
            conn.commit()
        return rec_id

    def get_active_recurring_expenses(self) -> List[Dict[str, Any]]:
        """Retrieve all active recurring expense definitions."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM recurring_expenses WHERE is_active = 1")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def mark_recurring_logged(self, rec_id: str, date_str: str):
        """Update last logged date for a recurring expense."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE recurring_expenses SET last_logged_date = ? WHERE id = ?",
                (date_str, rec_id),
            )
            conn.commit()

    def delete_recurring_expense(self, rec_id: str) -> bool:
        """Deactivate or remove a recurring expense."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE recurring_expenses SET is_active = 0 WHERE id = ?", (rec_id,))
            conn.commit()
            return cursor.rowcount > 0

    def _row_to_entry(self, row: sqlite3.Row) -> ExpenseEntry:
        return ExpenseEntry(
            id=row["id"],
            date=dt.date.fromisoformat(row["date"]),
            amount=float(row["amount"]),
            currency=row["currency"],
            merchant=row["merchant"],
            category=row["category"],
            payment_method=row["payment_method"] or None,
            notes=row["notes"] or None,
            source=ExpenseSource(row["source"]) if row["source"] in [s.value for s in ExpenseSource] else ExpenseSource.TEXT,
            receipt_url=row["receipt_url"] or None,
            confidence=float(row["confidence"]),
            created_at=dt.datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S"),
        )


# Global database instance
_local_db_instance = LocalDatabase()


def get_local_db() -> LocalDatabase:
    """Get singleton LocalDatabase instance."""
    return current_database() or _local_db_instance
