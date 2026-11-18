"""One-time import of a legacy local SQLite database into a hosted account."""

import argparse
import datetime as dt
import io
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from PIL import Image, UnidentifiedImageError

from src.models.schemas import ExpenseEntry, ExpenseSource
from src.storage.postgres_db import PostgresDatabase


def _tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows}


def _dict_rows(connection: sqlite3.Connection, table: str) -> Iterable[Dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    return (dict(row) for row in connection.execute(f"SELECT * FROM {table}"))


def _import_receipt(scoped_db: Any, receipt_url: Optional[str]) -> Optional[str]:
    if not receipt_url:
        return None
    receipt_path = Path(receipt_url)
    if not receipt_path.is_file():
        return receipt_url
    data = receipt_path.read_bytes()
    if not data or len(data) > 5 * 1024 * 1024:
        return receipt_url
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        content_type = Image.MIME.get(image.format or "")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return receipt_url
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        return receipt_url
    receipt_id = scoped_db.create_receipt(receipt_path.name, content_type, data)
    return f"/api/receipts/{receipt_id}"


def migrate(sqlite_path: Path, username: str) -> Dict[str, int]:
    """Copy expenses, budgets, merchant rules, and recurring definitions once."""
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")
    database = PostgresDatabase()
    database.ensure_schema()
    account = database.get_user_by_username(username.strip().lower())
    if not account:
        raise ValueError(f"No hosted account exists for username {username!r}.")
    scoped_db = database.user_database(account["id"])
    migrated = {
        "expenses": 0,
        "budgets": 0,
        "merchant_rules": 0,
        "recurring_expenses": 0,
        "receipts": 0,
    }
    uri = sqlite_path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as source:
        tables = _tables(source)
        if "expenses" in tables:
            for row in _dict_rows(source, "expenses"):
                if scoped_db.get_expense(row["id"]):
                    continue
                created_at = row.get("created_at") or dt.datetime.now(dt.timezone.utc).isoformat()
                try:
                    created_at = dt.datetime.fromisoformat(created_at)
                except (TypeError, ValueError):
                    created_at = dt.datetime.now(dt.timezone.utc)
                receipt_url = row.get("receipt_url") or None
                imported_receipt_url = _import_receipt(scoped_db, receipt_url)
                if imported_receipt_url and imported_receipt_url != receipt_url:
                    migrated["receipts"] += 1
                entry = ExpenseEntry(
                    id=row["id"],
                    date=dt.date.fromisoformat(row["date"]),
                    amount=float(row["amount"]),
                    currency=row["currency"],
                    merchant=row["merchant"],
                    category=row["category"],
                    payment_method=row.get("payment_method") or None,
                    notes=row.get("notes") or None,
                    source=ExpenseSource(row["source"])
                    if row["source"] in {value.value for value in ExpenseSource}
                    else ExpenseSource.TEXT,
                    receipt_url=imported_receipt_url,
                    confidence=float(row["confidence"]),
                    created_at=created_at,
                )
                scoped_db.insert_expense(entry, synced=bool(row.get("synced_to_sheet", 0)))
                migrated["expenses"] += 1

        if "budgets" in tables:
            existing = scoped_db.get_all_budgets()
            for row in _dict_rows(source, "budgets"):
                if row["category"] not in existing:
                    scoped_db.set_budget(row["category"], float(row["monthly_limit"]))
                    migrated["budgets"] += 1

        if "merchant_rules" in tables:
            existing_rules = scoped_db.get_all_merchant_rules()
            for row in _dict_rows(source, "merchant_rules"):
                merchant = row["merchant"].lower().strip()
                if merchant not in existing_rules:
                    scoped_db.save_merchant_rule(merchant, row["category"])
                    migrated["merchant_rules"] += 1

        if "recurring_expenses" in tables:
            for row in _dict_rows(source, "recurring_expenses"):
                if scoped_db.import_recurring_expense(row):
                    migrated["recurring_expenses"] += 1
    return migrated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True, help="Existing Pocket Money account username")
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=Path("data/expense_tracker.db"),
        help="Path to the local SQLite database (read-only import)",
    )
    args = parser.parse_args()
    counts = migrate(args.sqlite_path, args.username)
    print("Imported records: " + ", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
