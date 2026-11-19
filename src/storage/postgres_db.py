"""PostgreSQL account and per-user storage for the hosted web application."""

import datetime as dt
import hashlib
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from src.models.schemas import ExpenseEntry, ExpenseSource


class PostgresDatabase:
    """Owns account/session queries and creates strictly user-scoped storage handles."""

    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url or settings.database_url
        if not self.database_url:
            raise RuntimeError("DATABASE_URL must be configured for the hosted web app.")
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.database_url, row_factory=dict_row)

    def ensure_schema(self) -> None:
        """Create the hosted schema without expiring user records."""
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            self._create_schema()

    def _create_schema(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_idx ON users (lower(username))",
            """
            CREATE TABLE IF NOT EXISTS web_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS expenses (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                id TEXT NOT NULL,
                date DATE NOT NULL,
                amount DOUBLE PRECISION NOT NULL,
                currency TEXT NOT NULL,
                merchant TEXT NOT NULL,
                category TEXT NOT NULL,
                payment_method TEXT,
                notes TEXT,
                source TEXT NOT NULL,
                receipt_url TEXT,
                confidence DOUBLE PRECISION NOT NULL,
                created_at TIMESTAMP NOT NULL,
                synced_to_sheet BOOLEAN NOT NULL DEFAULT FALSE,
                PRIMARY KEY (user_id, id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS merchant_rules (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                merchant TEXT NOT NULL,
                category TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (user_id, merchant)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS budgets (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                category TEXT NOT NULL,
                monthly_limit DOUBLE PRECISION NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (user_id, category)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS session_state (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (user_id, key)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS recurring_expenses (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                id TEXT NOT NULL,
                title TEXT NOT NULL,
                amount DOUBLE PRECISION NOT NULL,
                category TEXT NOT NULL,
                day_of_month INTEGER NOT NULL DEFAULT 1,
                last_logged_date TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                notes TEXT,
                PRIMARY KEY (user_id, id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (user_id, key)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS receipts (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                id TEXT NOT NULL,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                data BYTEA NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, id)
            )
            """,
        )
        with self._connect() as conn:
            for statement in statements:
                conn.execute(statement)
        self._schema_ready = True

    def create_user(self, username: str, password_hash: str) -> Dict[str, str]:
        self.ensure_schema()
        user_id = str(uuid.uuid4())
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "INSERT INTO users (id, username, password_hash) VALUES (%s, %s, %s) RETURNING id, username",
                    (user_id, username, password_hash),
                ).fetchone()
        except Exception as exc:
            from psycopg.errors import UniqueViolation

            if isinstance(exc, UniqueViolation):
                raise ValueError("An account with that username already exists.") from exc
            raise
        return {"id": row["id"], "username": row["username"]}

    def get_user_by_username(self, username: str) -> Optional[Dict[str, str]]:
        self.ensure_schema()
        with self._connect() as conn:
            return conn.execute(
                "SELECT id, username, password_hash FROM users WHERE lower(username) = lower(%s)",
                (username,),
            ).fetchone()

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, str]]:
        self.ensure_schema()
        with self._connect() as conn:
            return conn.execute(
                "SELECT id, username, password_hash FROM users WHERE id = %s",
                (user_id,),
            ).fetchone()

    def create_session(self, token: str, user_id: str, expires_at: dt.datetime) -> None:
        self.ensure_schema()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO web_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                (token_hash, user_id, expires_at),
            )

    def get_session(self, token: str) -> Optional[Dict[str, str]]:
        self.ensure_schema()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT users.id, users.username
                FROM web_sessions
                JOIN users ON users.id = web_sessions.user_id
                WHERE web_sessions.token_hash = %s AND web_sessions.expires_at > now()
                """,
                (token_hash,),
            ).fetchone()

    def delete_session(self, token: str) -> None:
        self.ensure_schema()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute("DELETE FROM web_sessions WHERE token_hash = %s", (token_hash,))

    def delete_account(self, user_id: str) -> None:
        self.ensure_schema()
        with self._connect() as conn:
            conn.execute("DELETE FROM users WHERE id = %s", (user_id,))

    def user_database(self, user_id: str) -> "UserDatabase":
        self.ensure_schema()
        return UserDatabase(self, user_id)


class UserDatabase:
    """Data access handle that always scopes reads and writes to one user id."""

    def __init__(self, database: PostgresDatabase, user_id: str):
        self.database = database
        self.user_id = user_id

    def _connect(self):
        return self.database._connect()

    def insert_expense(self, entry: ExpenseEntry, synced: bool = False) -> str:
        source = entry.source.value if isinstance(entry.source, ExpenseSource) else str(entry.source)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO expenses (
                    user_id, id, date, amount, currency, merchant, category,
                    payment_method, notes, source, receipt_url, confidence,
                    created_at, synced_to_sheet
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, id) DO UPDATE SET
                    date = EXCLUDED.date, amount = EXCLUDED.amount, currency = EXCLUDED.currency,
                    merchant = EXCLUDED.merchant, category = EXCLUDED.category,
                    payment_method = EXCLUDED.payment_method, notes = EXCLUDED.notes,
                    source = EXCLUDED.source, receipt_url = EXCLUDED.receipt_url,
                    confidence = EXCLUDED.confidence, created_at = EXCLUDED.created_at,
                    synced_to_sheet = EXCLUDED.synced_to_sheet
                """,
                (
                    self.user_id, entry.id, entry.date, entry.amount, entry.currency,
                    entry.merchant, entry.category, entry.payment_method or "", entry.notes or "",
                    source, entry.receipt_url or "", entry.confidence,
                    entry.created_at.replace(tzinfo=None), synced,
                ),
            )
            self.set_session_state("last_logged_expense_id", entry.id)
        return entry.id

    def get_expense(self, expense_id: str) -> Optional[ExpenseEntry]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM expenses WHERE user_id = %s AND id = %s",
                (self.user_id, expense_id),
            ).fetchone()
        return self._row_to_entry(row) if row else None

    def get_last_logged_expense(self) -> Optional[ExpenseEntry]:
        value = self.get_session_state("last_logged_expense_id")
        if value:
            entry = self.get_expense(value)
            if entry:
                return entry
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM expenses WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
                (self.user_id,),
            ).fetchone()
        return self._row_to_entry(row) if row else None

    def update_expense(self, expense_id: str, updates: Dict[str, Any]) -> bool:
        allowed = {"date", "amount", "merchant", "category", "payment_method", "notes"}
        valid = [(key, value) for key, value in updates.items() if key in allowed]
        if not valid:
            return False
        assignments = ", ".join(f"{key} = %s" for key, _ in valid)
        values = [value.isoformat() if key == "date" and isinstance(value, (dt.date, dt.datetime)) else value for key, value in valid]
        with self._connect() as conn:
            result = conn.execute(
                f"UPDATE expenses SET {assignments} WHERE user_id = %s AND id = %s",
                (*values, self.user_id, expense_id),
            )
        return result.rowcount > 0

    def delete_expense(self, expense_id: str) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM expenses WHERE user_id = %s AND id = %s",
                (self.user_id, expense_id),
            )
        return result.rowcount > 0

    def get_all_expenses(self, limit: Optional[int] = 500) -> List[ExpenseEntry]:
        query = "SELECT * FROM expenses WHERE user_id = %s ORDER BY date DESC, created_at DESC"
        params: tuple[Any, ...] = (self.user_id,)
        if limit is not None:
            query += " LIMIT %s"
            params += (limit,)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def get_expense_category_totals(self) -> List[Dict[str, Any]]:
        """Return all-time expense totals grouped by this user's category."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT MIN(BTRIM(category)) AS category,
                       COUNT(*) AS expense_count,
                       SUM(amount) AS total_amount
                FROM expenses
                WHERE user_id = %s AND BTRIM(category) <> ''
                GROUP BY LOWER(BTRIM(category))
                ORDER BY SUM(amount) DESC, LOWER(BTRIM(category)) ASC
                """,
                (self.user_id,),
            ).fetchall()
        return [
            {
                "category": row["category"],
                "expense_count": int(row["expense_count"]),
                "total_amount": float(row["total_amount"]),
            }
            for row in rows
        ]

    def save_merchant_rule(self, merchant: str, category: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO merchant_rules (user_id, merchant, category, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, merchant) DO UPDATE
                SET category = EXCLUDED.category, updated_at = EXCLUDED.updated_at
                """,
                (self.user_id, merchant.lower().strip(), category, dt.datetime.now(dt.timezone.utc)),
            )

    def get_all_merchant_rules(self) -> Dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT merchant, category FROM merchant_rules WHERE user_id = %s",
                (self.user_id,),
            ).fetchall()
        return {row["merchant"]: row["category"] for row in rows}

    def set_budget(self, category: str, monthly_limit: float) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO budgets (user_id, category, monthly_limit, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, category) DO UPDATE
                SET monthly_limit = EXCLUDED.monthly_limit, updated_at = EXCLUDED.updated_at
                """,
                (self.user_id, category, monthly_limit, dt.datetime.now(dt.timezone.utc)),
            )

    def delete_budget(self, category: str) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM budgets WHERE user_id = %s AND category = %s",
                (self.user_id, category),
            )
        return result.rowcount > 0

    def get_all_budgets(self) -> Dict[str, float]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT category, monthly_limit FROM budgets WHERE user_id = %s",
                (self.user_id,),
            ).fetchall()
        return {row["category"]: float(row["monthly_limit"]) for row in rows}

    def add_recurring_expense(
        self, title: str, amount: float, category: str, day_of_month: int = 1,
        notes: Optional[str] = None,
    ) -> str:
        recurring_id = f"rec_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO recurring_expenses
                    (user_id, id, title, amount, category, day_of_month, notes, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                """,
                (self.user_id, recurring_id, title, amount, category, day_of_month, notes or ""),
            )
        return recurring_id

    def import_recurring_expense(self, item: Dict[str, Any]) -> bool:
        """Insert a legacy recurring row once, retaining its original identifier."""
        with self._connect() as conn:
            result = conn.execute(
                """
                INSERT INTO recurring_expenses
                    (user_id, id, title, amount, category, day_of_month,
                     last_logged_date, is_active, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, id) DO NOTHING
                """,
                (
                    self.user_id, item["id"], item["title"], item["amount"],
                    item["category"], item["day_of_month"], item.get("last_logged_date"),
                    bool(item.get("is_active", 1)), item.get("notes") or "",
                ),
            )
        return result.rowcount > 0

    def get_active_recurring_expenses(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recurring_expenses WHERE user_id = %s AND is_active = TRUE",
                (self.user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_all_recurring_expenses(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recurring_expenses WHERE user_id = %s ORDER BY id",
                (self.user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_recurring_logged(self, recurring_id: str, date_str: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE recurring_expenses SET last_logged_date = %s WHERE user_id = %s AND id = %s",
                (date_str, self.user_id, recurring_id),
            )

    def delete_recurring_expense(self, recurring_id: str) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "UPDATE recurring_expenses SET is_active = FALSE WHERE user_id = %s AND id = %s",
                (self.user_id, recurring_id),
            )
        return result.rowcount > 0

    def remove_recurring_expense(self, recurring_id: str) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM recurring_expenses WHERE user_id = %s AND id = %s",
                (self.user_id, recurring_id),
            )
        return result.rowcount > 0

    def get_user_settings(self) -> Dict[str, str]:
        values = {
            "default_currency": settings.default_currency,
            "default_timezone": settings.default_timezone,
        }
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM user_settings WHERE user_id = %s",
                (self.user_id,),
            ).fetchall()
        values.update({row["key"]: row["value"] for row in rows})
        return values

    def set_user_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (user_id, key, value) VALUES (%s, %s, %s)
                ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value
                """,
                (self.user_id, key, value),
            )

    def get_session_state(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM session_state WHERE user_id = %s AND key = %s",
                (self.user_id, key),
            ).fetchone()
        return row["value"] if row else None

    def set_session_state(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO session_state (user_id, key, value, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                """,
                (self.user_id, key, value, dt.datetime.now(dt.timezone.utc)),
            )

    def create_receipt(self, filename: str, content_type: str, data: bytes) -> str:
        receipt_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO receipts (user_id, id, filename, content_type, data)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (self.user_id, receipt_id, Path(filename).name, content_type, data),
            )
        return receipt_id

    def get_receipt(self, receipt_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT id, filename, content_type, data, created_at
                FROM receipts WHERE user_id = %s AND id = %s
                """,
                (self.user_id, receipt_id),
            ).fetchone()

    def get_all_receipts(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, filename, content_type, data, created_at
                FROM receipts WHERE user_id = %s ORDER BY created_at, id
                """,
                (self.user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_receipt_storage_size(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(OCTET_LENGTH(data)), 0) AS total_bytes FROM receipts WHERE user_id = %s",
                (self.user_id,),
            ).fetchone()
        return int(row["total_bytes"])

    def delete_receipt(self, receipt_id: str) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM receipts WHERE user_id = %s AND id = %s",
                (self.user_id, receipt_id),
            )
        return result.rowcount > 0

    @staticmethod
    def _row_to_entry(row: Dict[str, Any]) -> ExpenseEntry:
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = dt.datetime.fromisoformat(created_at)
        source = row["source"]
        return ExpenseEntry(
            id=row["id"],
            date=row["date"] if isinstance(row["date"], dt.date) else dt.date.fromisoformat(row["date"]),
            amount=float(row["amount"]),
            currency=row["currency"],
            merchant=row["merchant"],
            category=row["category"],
            payment_method=row["payment_method"] or None,
            notes=row["notes"] or None,
            source=ExpenseSource(source) if source in [item.value for item in ExpenseSource] else ExpenseSource.TEXT,
            receipt_url=row["receipt_url"] or None,
            confidence=float(row["confidence"]),
            created_at=created_at,
        )
