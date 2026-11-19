"""Tests for authenticated, per-account web data access."""

import base64
import datetime as dt
import io
import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from src.agent.state import SessionState
from src.models.schemas import ExpenseEntry
from src.storage.local_db import get_local_db
from src.storage.web_context import user_scope
from src.tools.categorizer import Categorizer
from src.tools.registry import execute_tool, get_tool_schemas
from src.interfaces import web


PASSWORD = "a-test-password-that-is-long-enough"


class MemoryUserDatabase:
    def __init__(self):
        self.expenses = {}
        self.budgets = {}
        self.rules = {}
        self.recurring = {}
        self.settings = {"default_currency": "INR", "default_timezone": "Asia/Kolkata"}
        self.receipts = {}
        self.session_state = {}

    def get_all_expenses(self, limit=500):
        values = sorted(self.expenses.values(), key=lambda item: (item.date, item.created_at), reverse=True)
        return values if limit is None else values[:limit]

    def update_expense(self, expense_id, updates):
        expense = self.expenses.get(expense_id)
        if not expense:
            return False
        self.expenses[expense_id] = expense.model_copy(update=updates)
        return True

    def get_expense_category_totals(self):
        totals = {}
        for expense in self.expenses.values():
            category = expense.category.strip()
            key = category.casefold()
            if not key:
                continue
            total = totals.setdefault(
                key,
                {"category": category, "expense_count": 0, "total_amount": 0.0},
            )
            total["expense_count"] += 1
            total["total_amount"] += expense.amount
        return sorted(
            totals.values(),
            key=lambda total: (-total["total_amount"], total["category"].casefold()),
        )

    def delete_expense(self, expense_id):
        return self.expenses.pop(expense_id, None) is not None

    def get_all_budgets(self):
        return dict(self.budgets)

    def set_budget(self, category, monthly_limit):
        self.budgets[category] = monthly_limit

    def delete_budget(self, category):
        return self.budgets.pop(category, None) is not None

    def get_user_settings(self):
        return dict(self.settings)

    def set_user_setting(self, key, value):
        self.settings[key] = value

    def get_all_merchant_rules(self):
        return dict(self.rules)

    def add_recurring_expense(self, title, amount, category, day_of_month=1, notes=None):
        recurring_id = f"rec-{len(self.recurring) + 1}"
        self.recurring[recurring_id] = {
            "id": recurring_id,
            "title": title,
            "amount": amount,
            "category": category,
            "day_of_month": day_of_month,
            "notes": notes,
            "is_active": True,
        }
        return recurring_id

    def get_active_recurring_expenses(self):
        return [item for item in self.recurring.values() if item["is_active"]]

    def get_all_recurring_expenses(self):
        return [dict(item) for item in self.recurring.values()]

    def delete_recurring_expense(self, recurring_id):
        item = self.recurring.get(recurring_id)
        if not item or not item["is_active"]:
            return False
        item["is_active"] = False
        return True

    def remove_recurring_expense(self, recurring_id):
        return self.recurring.pop(recurring_id, None) is not None

    def create_receipt(self, filename, content_type, data):
        receipt_id = f"receipt-{len(self.receipts) + 1}"
        self.receipts[receipt_id] = {
            "id": receipt_id,
            "filename": filename,
            "content_type": content_type,
            "data": data,
            "created_at": dt.datetime.now(dt.timezone.utc),
        }
        return receipt_id

    def get_receipt(self, receipt_id):
        return self.receipts.get(receipt_id)

    def get_all_receipts(self):
        return list(self.receipts.values())

    def get_receipt_storage_size(self):
        return sum(len(receipt["data"]) for receipt in self.receipts.values())

    def delete_receipt(self, receipt_id):
        return self.receipts.pop(receipt_id, None) is not None

    def get_session_state(self, key):
        return self.session_state.get(key)

    def set_session_state(self, key, value):
        self.session_state[key] = value

    def insert_expense(self, entry, synced=False):
        self.expenses[entry.id] = entry
        self.set_session_state("last_logged_expense_id", entry.id)
        return entry.id


class MemoryDatabase:
    database_url = "memory:"

    def __init__(self):
        self.users = {}
        self.stores = {}
        self.sessions = {}
        self.recovery_emails = {}
        self.email_verification_tokens = {}
        self.password_reset_tokens = {}
        self.sent_emails = []
        self.next_user = 1

    def ensure_schema(self):
        pass

    def create_user(self, username, password_hash, email):
        if self.get_user_by_username(username):
            raise ValueError("An account with that username or email already exists.")
        if any(
            (record.get("email") or "").lower() == email.lower()
            or record.get("pending_email", "").lower() == email.lower()
            for record in self.recovery_emails.values()
        ):
            raise ValueError("An account with that username or email already exists.")
        user = {"id": f"user-{self.next_user}", "username": username, "password_hash": password_hash}
        self.next_user += 1
        self.users[user["id"]] = user
        self.stores[user["id"]] = MemoryUserDatabase()
        self.recovery_emails[user["id"]] = {
            "email": None,
            "verified": False,
            "pending_email": email,
        }
        return {"id": user["id"], "username": username}

    def get_user_by_verified_email(self, email):
        record = next(
            (
                self.recovery_emails[user_id]
                for user_id in self.users
                if self.recovery_emails[user_id]["verified"]
                and self.recovery_emails[user_id]["email"].lower() == email.lower()
            ),
            None,
        )
        if not record:
            return None
        user = next(
            user for user_id, user in self.users.items()
            if self.recovery_emails[user_id] is record
        )
        return {"id": user["id"], "username": user["username"]}

    def set_pending_recovery_email(self, user_id, email):
        record = self.recovery_emails[user_id]
        if record["verified"] and record["email"].lower() == email.lower():
            raise ValueError("This recovery email is already verified.")
        if any(
            other_id != user_id
            and (
                (other.get("email") or "").lower() == email.lower()
                or other.get("pending_email", "").lower() == email.lower()
            )
            for other_id, other in self.recovery_emails.items()
        ):
            raise ValueError("That email address is already associated with an account.")
        record["pending_email"] = email
        for token in self.email_verification_tokens.values():
            if token["user_id"] == user_id:
                token["used"] = True

    def get_recovery_email_status(self, user_id):
        return dict(self.recovery_emails[user_id])

    def create_email_verification_token(self, user_id, email, token_hash, expires_at):
        now = dt.datetime.now(dt.timezone.utc)
        recent = [
            token for token in self.email_verification_tokens.values()
            if token["user_id"] == user_id
            and token["created_at"] > now - dt.timedelta(hours=1)
        ]
        if len(recent) >= 3:
            return False
        record = self.recovery_emails[user_id]
        if record["pending_email"] != email:
            return False
        for token in self.email_verification_tokens.values():
            if token["user_id"] == user_id:
                token["used"] = True
        self.email_verification_tokens[token_hash] = {
            "user_id": user_id,
            "email": email,
            "expires_at": expires_at,
            "created_at": now,
            "used": False,
        }
        return True

    def verify_email_token(self, token_hash):
        token = self.email_verification_tokens.get(token_hash)
        now = dt.datetime.now(dt.timezone.utc)
        if not token or token["used"] or token["expires_at"] <= now:
            return False
        record = self.recovery_emails[token["user_id"]]
        if record["pending_email"] != token["email"]:
            token["used"] = True
            return False
        record.update(email=token["email"], verified=True, pending_email=None)
        for other in self.email_verification_tokens.values():
            if other["user_id"] == token["user_id"]:
                other["used"] = True
        for reset_token in self.password_reset_tokens.values():
            if reset_token["user_id"] == token["user_id"]:
                reset_token["used"] = True
        return True

    def create_password_reset_token(self, user_id, token_hash, expires_at):
        now = dt.datetime.now(dt.timezone.utc)
        recent = [
            token for token in self.password_reset_tokens.values()
            if token["user_id"] == user_id
            and token["created_at"] > now - dt.timedelta(hours=1)
        ]
        if len(recent) >= 3:
            return False
        for token in self.password_reset_tokens.values():
            if token["user_id"] == user_id:
                token["used"] = True
        self.password_reset_tokens[token_hash] = {
            "user_id": user_id,
            "expires_at": expires_at,
            "created_at": now,
            "used": False,
        }
        return True

    def has_valid_password_reset_token(self, token_hash):
        token = self.password_reset_tokens.get(token_hash)
        return bool(
            token
            and not token["used"]
            and token["expires_at"] > dt.datetime.now(dt.timezone.utc)
        )

    def consume_password_reset_token(self, token_hash, password_hash):
        if not self.has_valid_password_reset_token(token_hash):
            return False
        user_id = self.password_reset_tokens[token_hash]["user_id"]
        self.users[user_id]["password_hash"] = password_hash
        for token in self.password_reset_tokens.values():
            if token["user_id"] == user_id:
                token["used"] = True
        self.sessions = {
            token: session for token, session in self.sessions.items()
            if session[0] != user_id
        }
        return True

    def get_user_by_username(self, username):
        return next(
            (user for user in self.users.values() if user["username"].lower() == username.lower()),
            None,
        )

    def get_user_by_id(self, user_id):
        return self.users.get(user_id)

    def create_session(self, token, user_id, expires_at):
        self.sessions[token] = (user_id, expires_at)

    def get_session(self, token):
        session = self.sessions.get(token)
        if not session or session[1] <= dt.datetime.now(dt.timezone.utc):
            return None
        user = self.users.get(session[0])
        return {"id": user["id"], "username": user["username"]} if user else None

    def delete_session(self, token):
        self.sessions.pop(token, None)

    def delete_account(self, user_id):
        self.users.pop(user_id, None)
        self.stores.pop(user_id, None)
        self.recovery_emails.pop(user_id, None)
        self.sessions = {
            token: session for token, session in self.sessions.items() if session[0] != user_id
        }

    def user_database(self, user_id):
        return self.stores[user_id]


@pytest.fixture
def database(monkeypatch):
    database = MemoryDatabase()
    monkeypatch.setattr(web.settings, "cookie_secure", False)
    monkeypatch.setattr(web.settings, "resend_api_key", "test-api-key")
    monkeypatch.setattr(web.settings, "email_from", "Pocket Money <support@example.com>")
    monkeypatch.setattr(web.settings, "public_base_url", "https://pocket-money.example.com")
    monkeypatch.setattr(
        web,
        "_send_resend_email",
        lambda recipient, subject, text: database.sent_emails.append(
            {"to": recipient, "subject": subject, "text": text}
        ),
    )
    web.app.dependency_overrides[web.get_database] = lambda: database
    yield database
    web.app.dependency_overrides.clear()


@pytest.fixture
def browser():
    return TestClient(web.app)


def create_account(browser, username):
    response = browser.post(
        "/signup",
        json={"username": username, "password": PASSWORD, "email": f"{username}@example.com"},
    )
    assert response.status_code == 200
    return response


def token_from_email(email):
    link = next(line for line in email["text"].splitlines() if line.startswith("https://"))
    return parse_qs(urlparse(link).fragment)["token"][0]


def test_signup_login_and_logout_issue_and_revoke_account_session(database, browser, monkeypatch):
    response = create_account(browser, "alice")
    cookie = response.cookies.get(web._SESSION_COOKIE)

    stored_hash = database.get_user_by_username("alice")["password_hash"]
    assert stored_hash != PASSWORD
    assert web._verify_password(PASSWORD, stored_hash)
    assert cookie
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert response.headers["cache-control"] == "no-store"
    dashboard = browser.get("/")
    assert dashboard.status_code == 200
    assert 'id="delete-account"' in dashboard.text

    monkeypatch.setattr(web.settings, "database_url", "configured")
    monkeypatch.setattr(web, "get_database", lambda: database)
    logout = browser.post(
        "/logout",
        follow_redirects=False,
        headers={"Origin": "https://testserver", "X-Forwarded-Proto": "https"},
    )
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"
    assert database.get_session(cookie) is None
    assert browser.get("/", follow_redirects=False).status_code == 303

    login = browser.post("/login", json={"username": "ALICE", "password": PASSWORD})
    assert login.status_code == 200
    assert browser.get("/").status_code == 200


def test_duplicate_accounts_and_invalid_password_are_rejected(database, browser):
    create_account(browser, "alice")

    duplicate = TestClient(web.app).post(
        "/signup",
        json={
            "username": "ALICE",
            "password": PASSWORD,
            "email": "other@example.com",
        },
    )
    invalid = TestClient(web.app).post(
        "/login", json={"username": "alice", "password": "not-the-right-password"}
    )
    invalid_email = TestClient(web.app).post(
        "/signup",
        json={"username": "charlie", "password": PASSWORD, "email": "not-an-email"},
    )

    assert duplicate.status_code == 409
    assert invalid.status_code == 401
    assert invalid.json() == {"detail": "Incorrect username or password."}
    assert invalid_email.status_code == 422


def test_dashboard_and_private_routes_require_an_account(database, browser):
    login_page = browser.get("/login")
    signup_page = browser.get("/signup")
    assert login_page.status_code == 200
    assert 'id="login-form"' in login_page.text
    assert 'href="/forgot-password"' in login_page.text
    assert signup_page.status_code == 200
    assert 'id="email" name="email" type="email"' in signup_page.text
    assert browser.get("/forgot-password").status_code == 200
    assert browser.get("/reset-password").status_code == 200
    assert browser.get("/verify-email").status_code == 200
    assert browser.get("/", follow_redirects=False).headers["location"] == "/login"

    for path in (
        "/api/expenses",
        "/api/expenses/export",
        "/api/expenses/category-totals",
        "/api/account/backup",
        "/api/account/recovery-email",
        "/api/summary",
        "/api/settings",
    ):
        assert browser.get(path).status_code == 401

    assert browser.post("/api/chat", json={"message": "spent 20 on tea"}).status_code == 401


def test_signup_and_password_reset_use_verified_email_and_revoke_sessions(database, browser):
    alice = TestClient(web.app)
    bob = TestClient(web.app)
    create_account(alice, "alice")
    create_account(bob, "bob")

    alice_email = database.sent_emails[0]["to"]
    assert alice_email == "alice@example.com"
    verification_token = token_from_email(database.sent_emails[0])
    assert alice.post("/verify-email", json={"token": verification_token}).status_code == 200
    assert alice.post("/verify-email", json={"token": verification_token}).status_code == 400
    assert alice.get("/api/account/recovery-email").json()["verified"] is True

    unknown_response = alice.post(
        "/forgot-password", json={"email": "unknown@example.com"}
    )
    unverified_response = alice.post(
        "/forgot-password", json={"email": "bob@example.com"}
    )
    assert unknown_response.json() == unverified_response.json()
    assert len(database.sent_emails) == 2

    requested = alice.post("/forgot-password", json={"email": alice_email})
    assert requested.status_code == 200
    reset_token = token_from_email(database.sent_emails[-1])
    reset = alice.post(
        "/reset-password",
        json={"token": reset_token, "password": "a-new-password-that-is-long-enough"},
    )

    assert reset.status_code == 200
    assert alice.get("/api/settings").status_code == 401
    assert alice.post("/reset-password", json={
        "token": reset_token,
        "password": "another-new-password-that-is-long-enough",
    }).status_code == 400
    assert alice.post("/login", json={"username": "alice", "password": PASSWORD}).status_code == 401
    assert alice.post(
        "/login",
        json={"username": "alice", "password": "a-new-password-that-is-long-enough"},
    ).status_code == 200
    assert bob.post("/login", json={"username": "bob", "password": PASSWORD}).status_code == 200


def test_password_reset_email_requests_are_rate_limited(database, browser):
    create_account(browser, "alice")
    verification_token = token_from_email(database.sent_emails[0])
    browser.post("/verify-email", json={"token": verification_token})
    sent_before = len(database.sent_emails)

    responses = [
        browser.post("/forgot-password", json={"email": "alice@example.com"})
        for _ in range(4)
    ]

    assert all(response.status_code == 200 for response in responses)
    assert len(database.sent_emails) == sent_before + 3


def test_resend_delivery_uses_configured_sender_and_email_payload(monkeypatch):
    sent = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        sent["url"] = request.full_url
        sent["authorization"] = request.get_header("Authorization")
        sent["payload"] = json.loads(request.data)
        sent["timeout"] = timeout
        return Response()

    monkeypatch.setattr(web.settings, "resend_api_key", "test-api-key")
    monkeypatch.setattr(web.settings, "email_from", "Pocket Money <support@example.com>")
    monkeypatch.setattr(web.url_request, "urlopen", fake_urlopen)

    web._send_resend_email("alice@example.com", "Reset password", "Use this link.")

    assert sent["url"] == "https://api.resend.com/emails"
    assert sent["authorization"] == "Bearer test-api-key"
    assert sent["payload"]["from"] == "Pocket Money <support@example.com>"
    assert sent["payload"]["to"] == ["alice@example.com"]
    assert sent["payload"]["text"] == "Use this link."
    assert sent["timeout"] == 10


def test_signed_in_user_can_verify_recovery_email_and_email_is_unique(database, browser):
    alice = TestClient(web.app)
    bob = TestClient(web.app)
    create_account(alice, "alice")
    create_account(bob, "bob")

    updated = alice.put(
        "/api/account/recovery-email",
        json={"email": "recovery@example.com", "password": PASSWORD},
    )
    assert updated.status_code == 200
    token = token_from_email(database.sent_emails[-1])
    assert alice.post("/verify-email", json={"token": token}).status_code == 200
    assert alice.get("/api/account/recovery-email").json() == {
        "email": "recovery@example.com",
        "verified": True,
        "pending_email": None,
        "email_configured": True,
    }

    duplicate = bob.put(
        "/api/account/recovery-email",
        json={"email": "recovery@example.com", "password": PASSWORD},
    )
    assert duplicate.status_code == 409
    wrong_password = bob.put(
        "/api/account/recovery-email",
        json={"email": "other@example.com", "password": "not-the-right-password"},
    )
    assert wrong_password.status_code == 401


def test_password_reset_rejects_expired_tokens_and_unconfigured_email(database, browser, monkeypatch):
    create_account(browser, "alice")
    verification_token = token_from_email(database.sent_emails[0])
    browser.post("/verify-email", json={"token": verification_token})
    browser.post("/forgot-password", json={"email": "alice@example.com"})
    reset_token = token_from_email(database.sent_emails[-1])
    digest = web._token_digest(reset_token)
    database.password_reset_tokens[digest]["expires_at"] = dt.datetime.now(
        dt.timezone.utc
    ) - dt.timedelta(seconds=1)

    expired = browser.post(
        "/reset-password",
        json={"token": reset_token, "password": "a-new-password-that-is-long-enough"},
    )
    assert expired.status_code == 400

    monkeypatch.setattr(web.settings, "resend_api_key", "")
    unconfigured = browser.post(
        "/forgot-password", json={"email": "alice@example.com"}
    )
    assert unconfigured.status_code == 503


def test_brand_logo_is_available_to_login_dashboard_and_browser_tab(database, browser):
    create_account(browser, "alice")

    login_page = browser.get("/login").text
    dashboard = browser.get("/").text
    logo = browser.get("/brand.svg")
    favicon = browser.get("/favicon.svg")

    assert 'href="/favicon.svg"' in login_page
    assert 'src="/brand.svg"' in login_page
    assert 'href="/favicon.svg"' in dashboard
    assert 'src="/brand.svg"' in dashboard
    assert logo.status_code == favicon.status_code == 200
    assert logo.headers["content-type"] == "image/svg+xml"
    assert logo.content == favicon.content
    assert b'<svg xmlns="http://www.w3.org/2000/svg"' in logo.content


def test_dashboard_currency_selector_includes_rupees_by_default(database, browser):
    create_account(browser, "alice")

    dashboard = browser.get("/").text
    settings_response = browser.get("/api/settings")

    assert 'id="currency-select"' in dashboard
    assert '<option value="INR">₹ INR</option>' in dashboard
    assert settings_response.json()["settings"]["default_currency"] == "INR"


def test_dashboard_has_budget_bill_edit_and_backup_controls(database, browser):
    create_account(browser, "alice")
    dashboard = browser.get("/").text

    assert 'id="budget-form"' in dashboard
    assert 'id="recurring-form"' in dashboard
    assert 'id="edit-expense-dialog"' in dashboard
    assert 'id="backup-button"' in dashboard
    assert 'id="recovery-email-form"' in dashboard
    assert dashboard.index('id="assistant"') < dashboard.index('aria-label="Monthly summary"')


def test_all_time_category_totals_group_expenses_and_stay_account_scoped(database):
    alice = TestClient(web.app)
    bob = TestClient(web.app)
    create_account(alice, "alice")
    create_account(bob, "bob")
    alice_store = database.user_database("user-1")
    bob_store = database.user_database("user-2")

    alice_store.insert_expense(
        ExpenseEntry(id="fuel-one", amount=500, merchant="Petrol station", category="Fuel")
    )
    alice_store.insert_expense(
        ExpenseEntry(id="fuel-two", amount=250, merchant="Fuel pump", category="fuel")
    )
    alice_store.insert_expense(
        ExpenseEntry(id="food-one", amount=120, merchant="Cafe", category="Dining")
    )
    bob_store.insert_expense(
        ExpenseEntry(id="bob-fuel", amount=2000, merchant="Other station", category="Fuel")
    )

    alice_totals = alice.get("/api/expenses/category-totals").json()["category_totals"]
    bob_totals = bob.get("/api/expenses/category-totals").json()["category_totals"]

    assert alice_totals == [
        {"category": "Fuel", "expense_count": 2, "total_amount": 750.0},
        {"category": "Dining", "expense_count": 1, "total_amount": 120.0},
    ]
    assert bob_totals == [
        {"category": "Fuel", "expense_count": 1, "total_amount": 2000.0}
    ]


def test_expense_editing_is_validated_and_account_scoped(database):
    alice = TestClient(web.app)
    bob = TestClient(web.app)
    create_account(alice, "alice")
    create_account(bob, "bob")
    expense = ExpenseEntry(
        id="edit-me", amount=500, merchant="Petrol station", category="Fuel"
    )
    database.user_database("user-1").insert_expense(expense)

    cross_account = bob.patch(
        "/api/expenses/edit-me",
        json={"amount": 1, "category": "Dining"},
    )
    invalid = alice.patch("/api/expenses/edit-me", json={"amount": 0})
    updated = alice.patch(
        "/api/expenses/edit-me",
        json={
            "amount": 550,
            "merchant": "City Petrol",
            "category": "Fuel",
            "date": "2026-11-18",
            "notes": "Corrected receipt",
        },
    )

    assert cross_account.status_code == 404
    assert invalid.status_code == 422
    assert updated.status_code == 200
    saved = alice.get("/api/expenses").json()["expenses"][0]
    assert saved["amount"] == 550
    assert saved["merchant"] == "City Petrol"
    assert saved["notes"] == "Corrected receipt"
    assert database.user_database("user-2").expenses == {}


def test_private_backup_contains_all_account_data_and_receipts(database):
    alice = TestClient(web.app)
    bob = TestClient(web.app)
    create_account(alice, "alice")
    create_account(bob, "bob")
    alice_store = database.user_database("user-1")
    bob_store = database.user_database("user-2")
    alice_store.insert_expense(
        ExpenseEntry(id="alice-expense", amount=450, merchant="Fuel pump", category="Fuel")
    )
    bob_store.insert_expense(
        ExpenseEntry(id="bob-expense", amount=900, merchant="Other", category="Dining")
    )
    alice_store.set_budget("Fuel", 2000)
    alice_store.rules["fuel pump"] = "Fuel"
    alice_store.add_recurring_expense("Rent", 15000, "Housing", day_of_month=5)
    alice_store.create_receipt("fuel.png", "image/png", b"private receipt bytes")
    alice_store.set_user_setting("default_currency", "INR")

    response = alice.get("/api/account/backup")
    backup = response.json()

    assert response.status_code == 200
    assert "attachment;" in response.headers["content-disposition"]
    assert backup["format"] == "pocket-money-account-backup"
    assert [expense["id"] for expense in backup["expenses"]] == ["alice-expense"]
    assert backup["budgets"] == {"Fuel": 2000}
    assert backup["merchant_rules"] == {"fuel pump": "Fuel"}
    assert backup["recurring_expenses"][0]["title"] == "Rent"
    assert backup["settings"]["default_currency"] == "INR"
    assert base64.b64decode(backup["receipts"][0]["data_base64"]) == b"private receipt bytes"
    assert "bob-expense" not in response.text
    assert "password_hash" not in backup
    assert "session" not in backup


def test_expenses_budgets_settings_recurring_and_receipts_are_account_scoped(database):
    alice = TestClient(web.app)
    bob = TestClient(web.app)
    create_account(alice, "alice")
    create_account(bob, "bob")
    alice_store = database.user_database("user-1")
    bob_store = database.user_database("user-2")

    alice_store.insert_expense(
        ExpenseEntry(id="alice-expense", amount=120, merchant="Coffee", category="Dining")
    )
    bob_store.insert_expense(
        ExpenseEntry(id="bob-expense", amount=900, merchant="Fuel", category="Transport")
    )

    assert [row["id"] for row in alice.get("/api/expenses").json()["expenses"]] == ["alice-expense"]
    assert [row["id"] for row in bob.get("/api/expenses").json()["expenses"]] == ["bob-expense"]
    assert "alice-expense" in alice.get("/api/expenses/export").text
    assert "alice-expense" not in bob.get("/api/expenses/export").text
    assert alice.get("/api/summary").json()["total_amount"] == 120
    assert bob.get("/api/summary").json()["total_amount"] == 900
    assert alice.delete("/api/expenses/bob-expense").status_code == 404
    assert bob.delete("/api/expenses/alice-expense").status_code == 404

    assert alice.put("/api/budgets", json={"category": "Dining", "monthly_limit": 500}).status_code == 200
    assert alice.get("/api/budgets").json()["budgets"] == {"Dining": 500}
    assert bob.get("/api/budgets").json()["budgets"] == {}
    assert alice.patch(
        "/api/settings",
        json={"default_currency": "usd", "default_timezone": "America/Los_Angeles"},
    ).json()["settings"]["default_currency"] == "USD"
    assert bob.get("/api/settings").json()["settings"] == {
        "default_currency": "INR",
        "default_timezone": "Asia/Kolkata",
    }

    recurring = alice.post(
        "/api/recurring-expenses",
        json={"title": "Rent", "amount": 1000, "category": "Housing"},
    )
    recurring_id = recurring.json()["id"]
    assert recurring.status_code == 201
    alice_recurring = alice.get("/api/recurring-expenses").json()["recurring_expenses"]
    assert alice_recurring[0]["title"] == "Rent"
    assert isinstance(alice_recurring[0]["next_due_date"], str)
    assert alice_recurring[0]["days_until_due"] >= 0
    assert bob.get("/api/recurring-expenses").json()["recurring_expenses"] == []
    assert bob.delete(f"/api/recurring-expenses/{recurring_id}").status_code == 404

    image = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(image, format="PNG")
    uploaded = alice.post(
        "/api/receipts",
        files={"file": ("receipt.png", image.getvalue(), "image/png")},
    )
    receipt_id = uploaded.json()["id"]
    assert uploaded.status_code == 201
    assert alice.get(f"/api/receipts/{receipt_id}").content == image.getvalue()
    assert bob.get(f"/api/receipts/{receipt_id}").status_code == 404


def test_web_agent_uses_scoped_storage_and_does_not_share_local_files(database, monkeypatch):
    browser = TestClient(web.app)
    create_account(browser, "alice")
    store = database.user_database("user-1")

    class FakeAgent:
        def process_message(self, message):
            assert message == "record test expense"
            get_local_db().insert_expense(
                ExpenseEntry(id="web-chat-expense", amount=25, merchant="Tea", category="Dining")
            )
            return "Saved"

    monkeypatch.setattr(web, "ExpenseAgent", FakeAgent)
    response = browser.post("/api/chat", json={"message": "record test expense"})

    assert response.json() == {"response": "Saved"}
    assert list(store.expenses) == ["web-chat-expense"]
    assert get_local_db() is not store


def test_web_tools_do_not_expose_server_file_receipt_scanner(database):
    browser = TestClient(web.app)
    create_account(browser, "alice")
    scoped_db = database.user_database("user-1")
    with user_scope(scoped_db, SessionState(), Categorizer()):
        assert "extract_receipt" not in {schema["name"] for schema in get_tool_schemas()}
        with pytest.raises(ValueError, match="disabled in the web interface"):
            execute_tool("extract_receipt", {"image_path": "server-file.png"})


def test_account_deletion_removes_the_account_data_and_session(database):
    browser = TestClient(web.app)
    create_account(browser, "alice")
    account_id = "user-1"
    database.user_database(account_id).set_budget("Dining", 100)

    response = browser.request("DELETE", "/api/account", json={"password": PASSWORD})

    assert response.status_code == 204
    assert account_id not in database.users
    assert account_id not in database.stores
    assert browser.get("/", follow_redirects=False).status_code == 303


def test_receipt_upload_rejects_non_images(database):
    browser = TestClient(web.app)
    create_account(browser, "alice")

    response = browser.post(
        "/api/receipts",
        files={"file": ("not-image.txt", b"private text", "text/plain")},
    )

    assert response.status_code == 415


def test_cross_origin_mutations_are_rejected(database, browser):
    create_account(browser, "alice")

    response = browser.put(
        "/api/budgets",
        headers={"Origin": "https://attacker.example"},
        json={"category": "Dining", "monthly_limit": 50},
    )

    assert response.status_code == 403
    assert browser.get("/api/budgets").json()["budgets"] == {}


def test_hosted_web_fails_closed_without_a_database(monkeypatch):
    monkeypatch.setattr(web.settings, "database_url", "")

    with pytest.raises(HTTPException) as error:
        web.get_database()

    assert error.value.status_code == 503
