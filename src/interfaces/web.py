"""Hosted multi-user web interface for Pocket Money."""

import base64
import csv
import datetime as dt
import hashlib
import html
import io
import json
import logging
import re
import secrets
from pathlib import Path
from typing import Any, Optional
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request
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
_PASSWORD_RESET_PAGE = Path(__file__).with_name("password_reset.html")
_EMAIL_VERIFICATION_PAGE = Path(__file__).with_name("verify_email.html")
_BRAND_IMAGE = Path(__file__).with_name("brand.svg")
_SESSION_COOKIE = "pocket_money_session"
_SESSION_MAX_AGE = 30 * 24 * 60 * 60
_MAX_BACKUP_RECEIPT_BYTES = 25 * 1024 * 1024
_PASSWORD_RESET_TTL = dt.timedelta(minutes=30)
_EMAIL_VERIFICATION_TTL = dt.timedelta(hours=24)
_database_instance: Optional[PostgresDatabase] = None
_PASSWORD_SCRYPT_N = 2**14
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def _origin_matches_request(request: Request, origin: str) -> bool:
    forwarded_scheme = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    scheme = forwarded_scheme if forwarded_scheme in {"http", "https"} else request.url.scheme
    try:
        origin_url = url_parse.urlsplit(origin)
        host_url = url_parse.urlsplit(f"{scheme}://{request.headers.get('host', request.url.netloc)}")
        origin_port = origin_url.port or (443 if origin_url.scheme == "https" else 80)
        host_port = host_url.port or (443 if scheme == "https" else 80)
    except ValueError:
        return False
    return (
        origin_url.scheme == scheme
        and origin_url.hostname is not None
        and origin_url.hostname.lower() == (host_url.hostname or "").lower()
        and origin_port == host_port
        and not origin_url.username
        and not origin_url.password
        and not host_url.username
        and not host_url.password
        and not origin_url.path
        and not origin_url.query
        and not origin_url.fragment
    )


@app.middleware("http")
async def harden_responses(request: Request, call_next):
    cross_origin = False
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        cross_origin = bool(origin and not _origin_matches_request(request, origin))
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


class EmailDeliveryError(RuntimeError):
    """Raised when the configured email service cannot accept a message."""


def _email_service_configured() -> bool:
    try:
        parsed_url = url_parse.urlsplit(settings.public_base_url.strip())
    except ValueError:
        return False
    return bool(
        settings.resend_api_key.strip()
        and settings.email_from.strip()
        and parsed_url.scheme == "https"
        and parsed_url.netloc
        and parsed_url.path in {"", "/"}
        and not parsed_url.query
        and not parsed_url.fragment
        and not parsed_url.username
        and not parsed_url.password
    )


def _require_email_service() -> None:
    if not _email_service_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Account email is not configured. The site owner must set "
                "RESEND_API_KEY, EMAIL_FROM, and PUBLIC_BASE_URL."
            ),
        )


def _send_resend_email(recipient: str, subject: str, text: str) -> None:
    payload = json.dumps(
        {
            "from": settings.email_from,
            "to": [recipient],
            "subject": subject,
            "text": text,
            "html": f"<p>{html.escape(text).replace(chr(10), '<br>')}</p>",
        }
    ).encode("utf-8")
    request = url_request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with url_request.urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise EmailDeliveryError("The email provider rejected the message.")
    except (url_error.URLError, TimeoutError, OSError) as exc:
        raise EmailDeliveryError("The email provider could not be reached.") from exc


def _account_link(path: str, token: str) -> str:
    base_url = settings.public_base_url.strip().rstrip("/")
    return f"{base_url}{path}#{url_parse.urlencode({'token': token})}"


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _send_verification_email(
    database: PostgresDatabase, user_id: str, email: str
) -> bool:
    token = secrets.token_urlsafe(32)
    if not database.create_email_verification_token(
        user_id,
        email,
        _token_digest(token),
        dt.datetime.now(dt.timezone.utc) + _EMAIL_VERIFICATION_TTL,
    ):
        return False
    link = _account_link("/verify-email", token)
    _send_resend_email(
        email,
        "Verify your Pocket Money recovery email",
        (
            "Use this link to verify your Pocket Money recovery email. "
            f"The link expires in 24 hours:\n{link}"
        ),
    )
    return True


def _send_password_reset_email(
    database: PostgresDatabase, user_id: str, email: str
) -> bool:
    token = secrets.token_urlsafe(32)
    if not database.create_password_reset_token(
        user_id,
        _token_digest(token),
        dt.datetime.now(dt.timezone.utc) + _PASSWORD_RESET_TTL,
    ):
        return False
    link = _account_link("/reset-password", token)
    _send_resend_email(
        email,
        "Reset your Pocket Money password",
        (
            "Use this link to reset your Pocket Money password. "
            f"The link expires in 30 minutes. If you did not request this, "
            f"you can ignore this email:\n{link}"
        ),
    )
    return True


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


class SignupCredentials(AccountCredentials):
    email: str = Field(min_length=6, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not _EMAIL_RE.fullmatch(email):
            raise ValueError("Enter a valid email address.")
        return email


class EmailAddressRequest(BaseModel):
    email: str = Field(min_length=6, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not _EMAIL_RE.fullmatch(email):
            raise ValueError("Enter a valid email address.")
        return email


class RecoveryEmailRequest(EmailAddressRequest):
    password: str = Field(min_length=1, max_length=1024)


class TokenRequest(BaseModel):
    token: str = Field(min_length=32, max_length=128, pattern="^[A-Za-z0-9_-]+$")


class ResetPasswordRequest(TokenRequest):
    password: str = Field(min_length=12, max_length=1024)


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


class ExpenseUpdateRequest(BaseModel):
    merchant: Optional[str] = Field(default=None, min_length=1, max_length=200)
    category: Optional[str] = Field(default=None, min_length=1, max_length=100)
    amount: Optional[float] = Field(default=None, gt=0, le=1_000_000_000)
    date: Optional[dt.date] = None
    notes: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("merchant", "category")
    @classmethod
    def clean_expense_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        text = value.strip()
        if not text:
            raise ValueError("This value cannot be blank.")
        return text

    @model_validator(mode="after")
    def require_updates(self) -> "ExpenseUpdateRequest":
        updates = self.model_dump(exclude_unset=True)
        if not updates or any(value is None for value in updates.values()):
            raise ValueError("Provide at least one expense field to update.")
        return self


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


@app.get("/forgot-password", include_in_schema=False)
def forgot_password_page() -> FileResponse:
    return FileResponse(_LOGIN_PAGE, media_type="text/html")


@app.get("/reset-password", include_in_schema=False)
def reset_password_page() -> FileResponse:
    return FileResponse(_PASSWORD_RESET_PAGE, media_type="text/html")


@app.get("/verify-email", include_in_schema=False)
def verify_email_page() -> FileResponse:
    return FileResponse(_EMAIL_VERIFICATION_PAGE, media_type="text/html")


@app.get("/brand.svg", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
def brand_image() -> FileResponse:
    """Serve the public brand mark for the UI and browser tab."""
    return FileResponse(_BRAND_IMAGE, media_type="image/svg+xml")


@app.post("/signup", include_in_schema=False)
def signup(
    credentials: SignupCredentials,
    database: PostgresDatabase = Depends(get_database),
) -> Response:
    """Create an independent account and start its authenticated session."""
    try:
        user = database.create_user(
            credentials.username, _hash_password(credentials.password), credentials.email
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Could not create an account (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Account creation is temporarily unavailable.") from exc
    verification_email_sent = False
    if _email_service_configured():
        try:
            verification_email_sent = _send_verification_email(
                database, user["id"], credentials.email
            )
        except EmailDeliveryError as exc:
            logger.error("Could not send account verification email (%s)", type(exc).__name__)
    response = JSONResponse(
        {
            "ok": True,
            "username": user["username"],
            "email_verification_sent": verification_email_sent,
        }
    )
    _set_session_cookie(response, database, user["id"])
    return response


@app.post("/forgot-password", include_in_schema=False)
def request_password_reset(
    body: EmailAddressRequest,
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Send a generic, rate-limited reset response without revealing account existence."""
    _require_email_service()
    try:
        account = database.get_user_by_verified_email(body.email)
        if account:
            try:
                _send_password_reset_email(database, account["id"], body.email)
            except EmailDeliveryError as exc:
                logger.error("Could not send a password reset email (%s)", type(exc).__name__)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Could not process a password reset request (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Password reset is temporarily unavailable."
        ) from exc
    return {
        "ok": True,
        "message": "If a verified account uses that email, a reset link will be sent.",
    }


@app.post("/verify-email", include_in_schema=False)
def verify_email(
    body: TokenRequest,
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, bool]:
    try:
        verified = database.verify_email_token(_token_digest(body.token))
    except Exception as exc:
        logger.error("Could not verify an account email (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Email verification is temporarily unavailable."
        ) from exc
    if not verified:
        raise HTTPException(status_code=400, detail="This verification link is invalid or expired.")
    return {"ok": True}


@app.post("/reset-password", include_in_schema=False)
def reset_password(
    body: ResetPasswordRequest,
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, bool]:
    token_hash = _token_digest(body.token)
    try:
        if not database.has_valid_password_reset_token(token_hash):
            raise HTTPException(
                status_code=400,
                detail="This password reset link is invalid or expired.",
            )
        updated = database.consume_password_reset_token(
            token_hash, _hash_password(body.password)
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Could not reset an account password (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Password reset is temporarily unavailable."
        ) from exc
    if not updated:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or expired.")
    return {"ok": True}


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


@app.get("/api/expenses/category-totals")
def expense_category_totals(
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, list[dict[str, Any]]]:
    """Return all-time category totals for the authenticated user."""
    return {
        "category_totals": _scoped_database(database, user).get_expense_category_totals()
    }


@app.patch("/api/expenses/{expense_id}")
def update_expense(
    expense_id: str,
    body: ExpenseUpdateRequest,
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, bool]:
    updates = body.model_dump(exclude_unset=True)
    if not _scoped_database(database, user).update_expense(expense_id, updates):
        raise HTTPException(status_code=404, detail="Expense not found.")
    return {"ok": True}


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


@app.get("/api/account/backup")
def download_account_backup(
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> Response:
    """Download this account's private records and receipt images as JSON."""
    scoped_db = _scoped_database(database, user)
    if scoped_db.get_receipt_storage_size() > _MAX_BACKUP_RECEIPT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                "Receipt images are too large for a single download. "
                "Use the database provider's backup and restore tools."
            ),
        )
    receipts = scoped_db.get_all_receipts()
    backup = {
        "format": "pocket-money-account-backup",
        "version": 1,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "username": user["username"],
        "expenses": [
            expense.model_dump(mode="json")
            for expense in scoped_db.get_all_expenses(limit=None)
        ],
        "budgets": scoped_db.get_all_budgets(),
        "merchant_rules": scoped_db.get_all_merchant_rules(),
        "recurring_expenses": scoped_db.get_all_recurring_expenses(),
        "settings": scoped_db.get_user_settings(),
        "receipts": [
            {
                "id": receipt["id"],
                "filename": receipt["filename"],
                "content_type": receipt["content_type"],
                "created_at": (
                    receipt["created_at"].isoformat()
                    if hasattr(receipt["created_at"], "isoformat")
                    else str(receipt["created_at"])
                ),
                "data_base64": base64.b64encode(bytes(receipt["data"])).decode("ascii"),
            }
            for receipt in receipts
        ],
    }
    return JSONResponse(
        content=backup,
        headers={
            "Content-Disposition": (
                f'attachment; filename="pocket-money-backup-{dt.date.today().isoformat()}.json"'
            )
        },
    )


def _safe_csv_text(value: str) -> str:
    """Prevent spreadsheet formula execution for user-controlled text fields."""
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


@app.get("/api/account/recovery-email")
def read_recovery_email(
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        status = database.get_recovery_email_status(user["id"])
    except Exception as exc:
        logger.error("Could not read recovery email settings (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Recovery email settings are temporarily unavailable."
        ) from exc
    return {**status, "email_configured": _email_service_configured()}


@app.put("/api/account/recovery-email")
def update_recovery_email(
    body: RecoveryEmailRequest,
    user: dict[str, str] = Depends(get_current_user),
    database: PostgresDatabase = Depends(get_database),
) -> dict[str, bool]:
    _require_email_service()
    try:
        account = database.get_user_by_id(user["id"])
        if not account or not _verify_password(body.password, account["password_hash"]):
            raise HTTPException(status_code=401, detail="Incorrect password.")
        database.set_pending_recovery_email(user["id"], body.email)
        if not _send_verification_email(database, user["id"], body.email):
            raise HTTPException(
                status_code=429,
                detail="Too many verification emails were requested. Try again later.",
            )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EmailDeliveryError as exc:
        logger.error("Could not send a recovery email verification (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Could not send the verification email. Please try again."
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Could not update a recovery email (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Recovery email could not be updated."
        ) from exc
    return {"ok": True}


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
    scoped_db = _scoped_database(database, user)
    today = dt.datetime.now(
        ZoneInfo(scoped_db.get_user_settings()["default_timezone"])
    ).date()
    expenses = []
    for expense in scoped_db.get_active_recurring_expenses():
        due_day = min(max(int(expense.get("day_of_month", 1)), 1), 28)
        next_due = dt.date(today.year, today.month, due_day)
        if next_due < today:
            if today.month == 12:
                next_due = dt.date(today.year + 1, 1, due_day)
            else:
                next_due = dt.date(today.year, today.month + 1, due_day)
        expenses.append(
            {
                **expense,
                "next_due_date": next_due.isoformat(),
                "days_until_due": (next_due - today).days,
            }
        )
    return {"recurring_expenses": expenses}


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
