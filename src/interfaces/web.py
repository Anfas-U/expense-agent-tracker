"""Hosted multi-user web interface for Pocket Money."""

import base64
import csv
import datetime as dt
import hashlib
import io
import logging
import re
import secrets
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from PIL import Image, UnidentifiedImageError

from config.settings import settings
from src.agent.core import ExpenseAgent
from src.agent.state import SessionState
from src.storage.postgres_db import PostgresDatabase, UserDatabase
from src.storage.web_context import user_scope
from src.tools.categorizer import Categorizer
from src.tools.query_engine import query_expenses


app = FastAPI(title="Pocket Money", docs_url=None, redoc_url=None)
logger = logging.getLogger(__name__)
_WEB_PAGE = Path(__file__).with_name("web.html")
_LOGIN_PAGE = Path(__file__).with_name("login.html")
_SESSION_COOKIE = "pocket_money_session"
_SESSION_MAX_AGE = 30 * 24 * 60 * 60
_database_instance: Optional[PostgresDatabase] = None
_PASSWORD_SCRYPT_N = 2**14


@app.middleware("http")
async def harden_responses(request: Request, call_next):
    cross_origin = False
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        cross_origin = bool(origin and origin.rstrip("/") != expected_origin)
        cross_origin = cross_origin or (
            not origin and request.headers.get("sec-fetch-site") == "cross-site"
        )
    if cross_origin:
        response = JSONResponse(
            {"detail": "Cross-origin requests are not allowed."}, status_code=403
        )
    else:
        response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def get_database() -> PostgresDatabase:
    """Return a connected-and-initialized database or fail closed."""
    global _database_instance
    if not settings.database_url:
        raise HTTPException(
            status_code=503,
            detail="Hosted storage is not configured. Set DATABASE_URL to a PostgreSQL connection URL.",
        )
    if _database_instance is None or _database_instance.database_url != settings.database_url:
        _database_instance = PostgresDatabase(settings.database_url)
    try:
        _database_instance.ensure_schema()
    except Exception as exc:
        logger.error("Could not initialize the hosted PostgreSQL schema (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Hosted storage is temporarily unavailable.") from exc
    return _database_instance


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_PASSWORD_SCRYPT_N, r=8, p=1, dklen=64
    )
    return "scrypt${}${}${}${}${}".format(
        _PASSWORD_SCRYPT_N,
        8,
        1,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = stored_hash.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.b64decode(salt_text, validate=True)
        expected = base64.b64decode(digest_text, validate=True)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError, OverflowError):
        return False
    return secrets.compare_digest(actual, expected)


def _set_session_cookie(response: Response, database: PostgresDatabase, user_id: str) -> None:
    token = secrets.token_urlsafe(32)
    database.create_session(
        token,
        user_id,
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=_SESSION_MAX_AGE),
    )
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=token,
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


class AccountCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=1024)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        username = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9._@+-]{3,254}", username):
            raise ValueError("Use 3–254 letters, numbers, dots, underscores, @, +, or hyphens.")
        return username


class BudgetRequest(BaseModel):
    category: str = Field(min_length=1, max_length=100)
    monthly_limit: float = Field(ge=0, le=1_000_000_000)

    @field_validator("category")
    @classmethod
    def clean_category(cls, value: str) -> str:
        category = value.strip()
        if not category:
            raise ValueError("Category cannot be blank.")
        return category


class UserSettingsRequest(BaseModel):
    default_currency: Optional[str] = Field(
        default=None, min_length=3, max_length=3, pattern="^[A-Za-z]{3}$"
    )
    default_timezone: Optional[str] = Field(default=None, min_length=1, max_length=100)

    @field_validator("default_currency")
    @classmethod
    def normalize_currency(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value

    @model_validator(mode="after")
    def require_setting(self) -> "UserSettingsRequest":
        if self.default_currency is None and self.default_timezone is None:
            raise ValueError("At least one setting is required.")
        if self.default_timezone is not None:
            try:
                ZoneInfo(self.default_timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("Unknown timezone.") from exc
        return self


class RecurringExpenseRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    amount: float = Field(gt=0, le=1_000_000_000)
    category: str = Field(min_length=1, max_length=100)
    day_of_month: int = Field(default=1, ge=1, le=28)
    notes: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("title", "category")
    @classmethod
    def clean_recurring_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("This value cannot be blank.")
        return text


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def get_current_user(
    request: Request,
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, str]:
    token = request.cookies.get(_SESSION_COOKIE, "")
    if not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    try:
        user = database.get_session(token)
    except Exception as exc:
        logger.error("Could not validate a web session (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Hosted storage is temporarily unavailable.") from exc
    if not user:
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    return user


def _scoped_database(database: PostgresDatabase, user: dict[str, str]) -> UserDatabase:
    return database.user_database(user["id"])


@app.get("/login", include_in_schema=False)
@app.get("/signup", include_in_schema=False)
def login_page() -> FileResponse:
    """Serve the sign-in and account creation form."""
    return FileResponse(_LOGIN_PAGE, media_type="text/html")


@app.post("/signup", include_in_schema=False)
def signup(
    credentials: AccountCredentials,
    database: PostgresDatabase = Depends(get_database),
) -> Response:
    """Create an independent account and start its authenticated session."""
    try:
        user = database.create_user(credentials.username, _hash_password(credentials.password))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Could not create an account (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Account creation is temporarily unavailable.") from exc
    response = JSONResponse({"ok": True, "username": user["username"]})
    _set_session_cookie(response, database, user["id"])
    return response


@app.post("/login", include_in_schema=False)
def login(
    credentials: AccountCredentials,
    database: PostgresDatabase = Depends(get_database),
) -> Response:
    """Authenticate an account and issue a random, database-backed session."""
    try:
        user = database.get_user_by_username(credentials.username)
        if not user or not _verify_password(credentials.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Incorrect username or password.")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Could not authenticate an account (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Sign-in is temporarily unavailable.") from exc
    response = JSONResponse({"ok": True, "username": user["username"]})
    _set_session_cookie(response, database, user["id"])
    return response


@app.post("/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    """Revoke the current database session and clear its browser cookie."""
    token = request.cookies.get(_SESSION_COOKIE, "")
    if token and settings.database_url:
        try:
            database = get_database()
            database.delete_session(token)
        except HTTPException:
            logger.warning("Session cookie cleared while hosted storage was unavailable")
        except Exception as exc:
            logger.error("Could not revoke the web session (%s)", type(exc).__name__)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(
        key=_SESSION_COOKIE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/", include_in_schema=False)
def home(
    request: Request,
    database: PostgresDatabase = Depends(get_database),
) -> Response:
    """Serve the dashboard only to an authenticated account."""
    token = request.cookies.get(_SESSION_COOKIE, "")
    try:
        if token and len(token) <= 128 and database.get_session(token):
            return FileResponse(_WEB_PAGE, media_type="text/html")
    except Exception as exc:
        logger.error("Could not load the dashboard session (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Hosted storage is temporarily unavailable.") from exc
    return RedirectResponse(url="/login", status_code=303)


@app.get("/api/expenses")
def list_expenses(
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, list[dict[str, Any]]]:
    """Return only the authenticated user's recent expenses."""
    expenses = _scoped_database(database, user).get_all_expenses(limit=500)
    return {"expenses": [expense.model_dump(mode="json") for expense in expenses]}


@app.delete("/api/expenses/{expense_id}", status_code=204)
def delete_expense(
    expense_id: str,
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> Response:
    if not _scoped_database(database, user).delete_expense(expense_id):
        raise HTTPException(status_code=404, detail="Expense not found.")
    return Response(status_code=204)


@app.get("/api/expenses/export")
def export_expenses(
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> Response:
    """Download only the authenticated user's expenses as CSV."""
    expenses = _scoped_database(database, user).get_all_expenses(limit=None)
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
        writer.writerow(
            [
                _safe_csv_text(value) if isinstance(value, str) else value
                for value in expense.to_sheet_row()
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


@app.get("/api/summary")
def monthly_summary(
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Return totals and alerts calculated from this account's private records."""
    scoped_db = _scoped_database(database, user)
    with user_scope(scoped_db, SessionState(), _user_categorizer(scoped_db)):
        return query_expenses(time_period="this_month")


def _user_categorizer(database: UserDatabase) -> Categorizer:
    categorizer = Categorizer()
    for merchant, category in database.get_all_merchant_rules().items():
        categorizer.add_user_rule(merchant, category)
    return categorizer


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("message must not be blank")
        return message


@app.post("/api/chat")
def chat(
    body: ChatRequest,
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, str]:
    """Run agent tools with request-local storage and conversational state."""
    scoped_db = _scoped_database(database, user)
    with user_scope(scoped_db, SessionState(), _user_categorizer(scoped_db)):
        return {"response": ExpenseAgent().process_message(body.message)}


@app.get("/api/budgets")
def list_budgets(
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, dict[str, float]]:
    return {"budgets": _scoped_database(database, user).get_all_budgets()}


@app.put("/api/budgets")
def set_budget(
    body: BudgetRequest,
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, Any]:
    _scoped_database(database, user).set_budget(body.category.strip(), body.monthly_limit)
    return {"ok": True}


@app.delete("/api/budgets/{category}", status_code=204)
def delete_budget(
    category: str,
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> Response:
    if not _scoped_database(database, user).delete_budget(category):
        raise HTTPException(status_code=404, detail="Budget not found.")
    return Response(status_code=204)


@app.get("/api/settings")
def read_settings(
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, dict[str, str]]:
    return {"settings": _scoped_database(database, user).get_user_settings()}


@app.patch("/api/settings")
def update_settings(
    body: UserSettingsRequest,
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, Any]:
    scoped_db = _scoped_database(database, user)
    for key, value in body.model_dump(exclude_none=True).items():
        scoped_db.set_user_setting(key, value)
    return {"settings": scoped_db.get_user_settings()}


@app.get("/api/recurring-expenses")
def list_recurring_expenses(
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, list[dict[str, Any]]]:
    return {
        "recurring_expenses": _scoped_database(database, user).get_active_recurring_expenses()
    }


@app.post("/api/recurring-expenses", status_code=201)
def create_recurring_expense(
    body: RecurringExpenseRequest,
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, str]:
    recurring_id = _scoped_database(database, user).add_recurring_expense(
        title=body.title.strip(),
        amount=body.amount,
        category=body.category.strip(),
        day_of_month=body.day_of_month,
        notes=body.notes,
    )
    return {"id": recurring_id}


@app.delete("/api/recurring-expenses/{recurring_id}", status_code=204)
def delete_recurring_expense(
    recurring_id: str,
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> Response:
    if not _scoped_database(database, user).remove_recurring_expense(recurring_id):
        raise HTTPException(status_code=404, detail="Recurring expense not found.")
    return Response(status_code=204)


@app.post("/api/receipts", status_code=201)
async def upload_receipt(
    file: UploadFile = File(...),
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, str]:
    content = await file.read(5 * 1024 * 1024 + 1)
    if not content or len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Receipt images must be between 1 byte and 5 MB.")
    try:
        image = Image.open(io.BytesIO(content))
        image.verify()
        content_type = Image.MIME.get(image.format or "")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=415, detail="Upload a valid JPEG, PNG, or WEBP receipt image.") from exc
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=415, detail="Upload a valid JPEG, PNG, or WEBP receipt image.")
    receipt_id = _scoped_database(database, user).create_receipt(
        file.filename or "receipt", content_type, content
    )
    return {"id": receipt_id, "url": f"/api/receipts/{receipt_id}"}


@app.get("/api/receipts/{receipt_id}")
def read_receipt(
    receipt_id: str,
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> Response:
    receipt = _scoped_database(database, user).get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    return Response(
        content=receipt["data"],
        media_type=receipt["content_type"],
        headers={"Content-Disposition": 'inline; filename="receipt"'},
    )


@app.delete("/api/receipts/{receipt_id}", status_code=204)
def delete_receipt(
    receipt_id: str,
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> Response:
    if not _scoped_database(database, user).delete_receipt(receipt_id):
        raise HTTPException(status_code=404, detail="Receipt not found.")
    return Response(status_code=204)


@app.delete("/api/account", status_code=204)
def delete_account(
    body: DeleteAccountRequest,
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> Response:
    account = database.get_user_by_id(user["id"])
    if not account or not _verify_password(body.password, account["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect password.")
    database.delete_account(user["id"])
    response = Response(status_code=204)
    response.delete_cookie(
        key=_SESSION_COOKIE, httponly=True, secure=settings.cookie_secure,
        samesite="lax", path="/"
    )
    return response


if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
