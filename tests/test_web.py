"""Tests for authenticated, per-account web data access."""

import datetime as dt
import io

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
        }
        return receipt_id

    def get_receipt(self, receipt_id):
        return self.receipts.get(receipt_id)

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
        self.next_user = 1

    def ensure_schema(self):
        pass

    def create_user(self, username, password_hash):
        if self.get_user_by_username(username):
            raise ValueError("An account with that username already exists.")
        user = {"id": f"user-{self.next_user}", "username": username, "password_hash": password_hash}
        self.next_user += 1
        self.users[user["id"]] = user
        self.stores[user["id"]] = MemoryUserDatabase()
        return {"id": user["id"], "username": username}

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
        self.sessions = {
            token: session for token, session in self.sessions.items() if session[0] != user_id
        }

    def user_database(self, user_id):
        return self.stores[user_id]


@pytest.fixture
def database(monkeypatch):
    database = MemoryDatabase()
    monkeypatch.setattr(web.settings, "cookie_secure", False)
    web.app.dependency_overrides[web.get_database] = lambda: database
    yield database
    web.app.dependency_overrides.clear()


@pytest.fixture
def browser():
    return TestClient(web.app)


def create_account(browser, username):
    response = browser.post("/signup", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return response


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
    logout = browser.post("/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert database.get_session(cookie) is None
    assert browser.get("/", follow_redirects=False).status_code == 303

    login = browser.post("/login", json={"username": "ALICE", "password": PASSWORD})
    assert login.status_code == 200
    assert browser.get("/").status_code == 200


def test_duplicate_accounts_and_invalid_password_are_rejected(database, browser):
    create_account(browser, "alice")

    duplicate = TestClient(web.app).post(
        "/signup", json={"username": "ALICE", "password": PASSWORD}
    )
    invalid = TestClient(web.app).post(
        "/login", json={"username": "alice", "password": "not-the-right-password"}
    )

    assert duplicate.status_code == 409
    assert invalid.status_code == 401
    assert invalid.json() == {"detail": "Incorrect username or password."}


def test_dashboard_and_private_routes_require_an_account(database, browser):
    assert browser.get("/login").status_code == 200
    assert 'id="login-form"' in browser.get("/login").text
    assert browser.get("/signup").status_code == 200
    assert browser.get("/", follow_redirects=False).headers["location"] == "/login"

    for path in ("/api/expenses", "/api/expenses/export", "/api/summary", "/api/settings"):
        assert browser.get(path).status_code == 401

    assert browser.post("/api/chat", json={"message": "spent 20 on tea"}).status_code == 401


def test_dashboard_currency_selector_includes_rupees_by_default(database, browser):
    create_account(browser, "alice")

    dashboard = browser.get("/").text
    settings_response = browser.get("/api/settings")

    assert 'id="currency-select"' in dashboard
    assert '<option value="INR">₹ INR</option>' in dashboard
    assert settings_response.json()["settings"]["default_currency"] == "INR"


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
