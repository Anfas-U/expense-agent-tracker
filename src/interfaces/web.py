"""Local web interface for the Expense Tracker Agent."""

import csv
import datetime as dt
import io
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field, field_validator

from src.agent.core import ExpenseAgent
from config.settings import settings
from src.storage.local_db import get_local_db
from src.tools.query_engine import query_expenses


app = FastAPI(title="Pocket Money", docs_url=None, redoc_url=None)
_agent = ExpenseAgent()
_WEB_PAGE = Path(__file__).with_name("web.html")
_basic_auth = HTTPBasic(auto_error=False)


def require_auth(
    credentials: HTTPBasicCredentials | None = Depends(_basic_auth),
) -> None:
    """Require configured single-user credentials for all web routes."""
    username = settings.web_username
    password = settings.web_password
    if not username or len(password) < 16:
        raise HTTPException(
            status_code=503,
            detail="Web authentication is not configured. Set WEB_USERNAME and a WEB_PASSWORD of at least 16 characters.",
        )

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    valid_username = secrets.compare_digest(
        credentials.username.encode("utf-8"), username.encode("utf-8")
    )
    valid_password = secrets.compare_digest(
        credentials.password.encode("utf-8"), password.encode("utf-8")
    )
    if not (valid_username and valid_password):
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Basic"},
        )


class ChatRequest(BaseModel):
    """Message submitted through the browser interface."""

    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("message must not be blank")
        return message


@app.get("/", include_in_schema=False, dependencies=[Depends(require_auth)])
def home() -> FileResponse:
    """Serve the dashboard."""
    return FileResponse(_WEB_PAGE, media_type="text/html")


@app.get("/api/expenses", dependencies=[Depends(require_auth)])
def list_expenses() -> dict[str, list[dict[str, Any]]]:
    """Return recent local expenses for the dashboard."""
    expenses = get_local_db().get_all_expenses(limit=500)
    return {"expenses": [expense.model_dump(mode="json") for expense in expenses]}


@app.get("/api/expenses/export", dependencies=[Depends(require_auth)])
def export_expenses() -> Response:
    """Download all local expenses as an Excel-friendly CSV file."""
    expenses = get_local_db().get_all_expenses(limit=None)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "id", "date", "amount", "currency", "merchant", "category",
            "payment_method", "notes", "source", "receipt_url", "confidence",
            "created_at",
        ]
    )
    for expense in expenses:
        row = expense.to_sheet_row()
        writer.writerow(
            [
                _safe_csv_text(value) if isinstance(value, str) else value
                for value in row
            ]
        )

    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="pocket-money-expenses-{dt.date.today().isoformat()}.csv"'
            )
        },
    )


def _safe_csv_text(value: str) -> str:
    """Prevent spreadsheet formula execution for user-controlled text fields."""
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


@app.get("/api/summary", dependencies=[Depends(require_auth)])
def monthly_summary() -> dict[str, Any]:
    """Return deterministic totals and budget alerts for this month."""
    return query_expenses(time_period="this_month")


@app.post("/api/chat", dependencies=[Depends(require_auth)])
def chat(request: ChatRequest) -> dict[str, str]:
    """Send a natural-language request to the existing expense agent."""
    return {"response": _agent.process_message(request.message)}


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
