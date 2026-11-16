"""Tests for the local web interface."""

import base64

import pytest
from fastapi.testclient import TestClient

from src.interfaces import web


USERNAME = "test-user"
PASSWORD = "a-test-password-that-is-long-enough"
AUTH_HEADER = "Basic " + base64.b64encode(
    f"{USERNAME}:{PASSWORD}".encode("utf-8")
).decode("ascii")
client = TestClient(web.app, headers={"Authorization": AUTH_HEADER})


@pytest.fixture(autouse=True)
def configured_web_auth(monkeypatch):
    monkeypatch.setattr(web.settings, "web_username", USERNAME)
    monkeypatch.setattr(web.settings, "web_password", PASSWORD)


def test_dashboard_is_served():
    response = client.get("/")

    assert response.status_code == 200
    assert "Pocket Money" in response.text
    assert 'id="expense-search"' in response.text
    assert 'id="category-filter"' in response.text
    assert "/api/expenses/export" in response.text


def test_expenses_endpoint_returns_json_entries(monkeypatch):
    class FakeExpense:
        def model_dump(self, mode):
            assert mode == "json"
            return {"id": "exp_test", "merchant": "Coffee Shop", "amount": 120}

    class FakeDatabase:
        def get_all_expenses(self, limit):
            assert limit == 500
            return [FakeExpense()]

    monkeypatch.setattr(web, "get_local_db", lambda: FakeDatabase())

    response = client.get("/api/expenses")

    assert response.status_code == 200
    assert response.json() == {
        "expenses": [{"id": "exp_test", "merchant": "Coffee Shop", "amount": 120}]
    }


def test_export_endpoint_returns_all_expenses_as_csv(monkeypatch):
    class FakeExpense:
        id = "exp_test"
        date = "2026-11-13"
        amount = 120
        currency = "INR"
        merchant = "=1+1"
        category = "Dining"
        payment_method = None
        notes = None
        source = "text"
        receipt_url = None
        confidence = 1.0
        created_at = "2026-11-13 12:00:00"

        def to_sheet_row(self):
            return [
                self.id, self.date, self.amount, self.currency, self.merchant,
                self.category, "", "", self.source, "", self.confidence,
                self.created_at,
            ]

    class FakeDatabase:
        def get_all_expenses(self, limit):
            assert limit is None
            return [FakeExpense()]

    monkeypatch.setattr(web, "get_local_db", lambda: FakeDatabase())

    response = client.get("/api/expenses/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in response.headers["content-disposition"]
    assert "'=1+1" in response.text


def test_summary_endpoint_returns_query_result(monkeypatch):
    def fake_query_expenses(time_period):
        assert time_period == "this_month"
        return {"total_amount": 300.0, "entry_count": 2}

    monkeypatch.setattr(web, "query_expenses", fake_query_expenses)

    response = client.get("/api/summary")

    assert response.status_code == 200
    assert response.json() == {"total_amount": 300.0, "entry_count": 2}


def test_chat_endpoint_uses_agent(monkeypatch):
    class FakeAgent:
        def process_message(self, message):
            assert message == "spent 250 on lunch"
            return "Logged ₹250, Dining, today."

    monkeypatch.setattr(web, "_agent", FakeAgent())

    response = client.post("/api/chat", json={"message": "  spent 250 on lunch  "})

    assert response.status_code == 200
    assert response.json() == {"response": "Logged ₹250, Dining, today."}


def test_chat_endpoint_rejects_empty_message():
    response = client.post("/api/chat", json={"message": "   "})

    assert response.status_code == 422


@pytest.mark.parametrize(
    "path", ["/", "/api/expenses", "/api/expenses/export", "/api/summary"]
)
def test_private_routes_reject_unauthenticated_requests(path):
    response = TestClient(web.app).get(path)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_chat_rejects_unauthenticated_requests():
    response = TestClient(web.app).post("/api/chat", json={"message": "spent 20 on tea"})

    assert response.status_code == 401


def test_routes_fail_closed_if_auth_is_not_configured(monkeypatch):
    monkeypatch.setattr(web.settings, "web_username", "")
    monkeypatch.setattr(web.settings, "web_password", "")

    response = client.get("/")

    assert response.status_code == 503


def test_routes_reject_short_configured_password(monkeypatch):
    monkeypatch.setattr(web.settings, "web_password", "too-short")

    invalid_client = TestClient(
        web.app,
        headers={
            "Authorization": "Basic "
            + base64.b64encode(f"{USERNAME}:too-short".encode("utf-8")).decode("ascii")
        },
    )
    response = invalid_client.get("/")

    assert response.status_code == 503


def test_routes_reject_invalid_credentials():
    invalid_client = TestClient(
        web.app,
        headers={
            "Authorization": "Basic "
            + base64.b64encode(f"{USERNAME}:wrong-password".encode("utf-8")).decode("ascii")
        },
    )
    response = invalid_client.get("/")

    assert response.status_code == 401
