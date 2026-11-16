"""Google Sheets API client and local spreadsheet fallback adapter."""

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional
from config.settings import settings
from src.models.schemas import ExpenseEntry


class SpreadsheetStorage:
    """Manages reading and writing to Google Sheets with automatic local CSV fallback."""

    def __init__(self):
        self.credentials_path = Path(settings.google_credentials_path)
        self.local_csv_path = settings.data_dir / "expenses_sheet.csv"
        self.local_csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.is_google_connected = False
        self.client = None
        self.spreadsheet = None
        self.expenses_worksheet = None
        self.summary_worksheet = None

        self._init_connection()

    def _init_connection(self):
        """Attempt to connect to Google Sheets using service account credentials."""
        if self.credentials_path.exists():
            try:
                import gspread
                self.client = gspread.service_account(filename=str(self.credentials_path))
                sheet_name = settings.google_sheet_name
                try:
                    if settings.google_sheet_id:
                        self.spreadsheet = self.client.open_by_key(settings.google_sheet_id)
                    else:
                        self.spreadsheet = self.client.open(sheet_name)
                except Exception:
                    # Create new spreadsheet if not found
                    self.spreadsheet = self.client.create(sheet_name)

                self._init_worksheets()
                self.is_google_connected = True
            except Exception as e:
                self.is_google_connected = False
        else:
            self._init_local_csv()

    def _init_worksheets(self):
        """Initialize 'Expenses' and 'Summary' worksheets in Google Sheet."""
        if not self.spreadsheet:
            return

        headers = ExpenseEntry.sheet_columns()

        # 1. Expenses worksheet
        try:
            self.expenses_worksheet = self.spreadsheet.worksheet("Expenses")
        except Exception:
            self.expenses_worksheet = self.spreadsheet.add_worksheet(title="Expenses", rows=1000, cols=len(headers))
            self.expenses_worksheet.append_row(headers)
            # Freeze header row
            self.expenses_worksheet.freeze(rows=1)

        # 2. Monthly Summary worksheet (PRD Section 6.2)
        try:
            self.summary_worksheet = self.spreadsheet.worksheet("Monthly Summary")
        except Exception:
            self.summary_worksheet = self.spreadsheet.add_worksheet(title="Monthly Summary", rows=50, cols=10)
            summary_headers = ["Category", "Budget", "Total Spent", "Remaining", "% Used"]
            self.summary_worksheet.append_row(summary_headers)
            self.summary_worksheet.freeze(rows=1)

            # Insert formulas for default categories
            cat_data = settings.load_category_definitions()
            for idx, cat in enumerate(cat_data.get("categories", []), start=2):
                cat_name = cat["name"]
                # Google Sheets dynamic formula references
                row_data = [
                    cat_name,
                    0,  # Default budget
                    f'=SUMIFS(Expenses!C:C, Expenses!F:F, "{cat_name}")',
                    f'=B{idx}-C{idx}',
                    f'=IF(B{idx}>0, C{idx}/B{idx}, 0)',
                ]
                self.summary_worksheet.append_row(row_data, value_input_option="USER_ENTERED")

    def _init_local_csv(self):
        """Initialize local CSV fallback."""
        if not self.local_csv_path.exists():
            with open(self.local_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(ExpenseEntry.sheet_columns())

    def append_expense(self, entry: ExpenseEntry) -> bool:
        """Append a single expense entry to the spreadsheet."""
        return self.append_expenses_batch([entry])

    def append_expenses_batch(self, entries: List[ExpenseEntry]) -> bool:
        """Append multiple expenses to the spreadsheet."""
        if not entries:
            return True

        rows = [entry.to_sheet_row() for entry in entries]

        if self.is_google_connected and self.expenses_worksheet:
            try:
                self.expenses_worksheet.append_rows(rows, value_input_option="USER_ENTERED")
                return True
            except Exception:
                pass

        # Write to local CSV fallback
        with open(self.local_csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(rows)
        return True

    def update_expense_row(self, entry_id: str, updates: Dict[str, Any]) -> bool:
        """Update an existing expense row matching entry_id without overwriting other rows."""
        # Update local CSV
        if self.local_csv_path.exists():
            rows = []
            updated = False
            headers = ExpenseEntry.sheet_columns()
            with open(self.local_csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header_row = next(reader, None)
                if header_row:
                    rows.append(header_row)
                    for r in reader:
                        if len(r) > 0 and r[0] == entry_id:
                            for key, val in updates.items():
                                if key in headers:
                                    idx = headers.index(key)
                                    r[idx] = str(val)
                            updated = True
                        rows.append(r)

            if updated:
                with open(self.local_csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerows(rows)

        # Update Google Sheets if connected
        if self.is_google_connected and self.expenses_worksheet:
            try:
                cell = self.expenses_worksheet.find(entry_id)
                if cell:
                    row_num = cell.row
                    headers = ExpenseEntry.sheet_columns()
                    for key, val in updates.items():
                        if key in headers:
                            col_num = headers.index(key) + 1
                            self.expenses_worksheet.update_cell(row_num, col_num, val)
                    return True
            except Exception:
                pass

        return True

    def delete_expense_row(self, entry_id: str) -> bool:
        """Delete an expense row by its entry_id."""
        deleted = False
        if self.local_csv_path.exists():
            rows = []
            with open(self.local_csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header_row = next(reader, None)
                if header_row:
                    rows.append(header_row)
                    for r in reader:
                        if len(r) > 0 and r[0] == entry_id:
                            deleted = True
                            continue
                        rows.append(r)
            if deleted:
                with open(self.local_csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerows(rows)

        if self.is_google_connected and self.expenses_worksheet:
            try:
                cell = self.expenses_worksheet.find(entry_id)
                if cell:
                    self.expenses_worksheet.delete_rows(cell.row)
                    return True
            except Exception:
                pass

        return deleted

    def read_all_expenses(self) -> List[Dict[str, Any]]:
        """Read all rows from the spreadsheet."""
        records = []
        if self.local_csv_path.exists():
            with open(self.local_csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    records.append(r)
        return records


# Singleton instance
_spreadsheet_storage = SpreadsheetStorage()


def get_spreadsheet_storage() -> SpreadsheetStorage:
    """Get the active spreadsheet storage instance."""
    return _spreadsheet_storage

