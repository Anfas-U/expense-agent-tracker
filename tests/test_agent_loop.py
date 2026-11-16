"""Unit tests for the hand-crafted pure Python agent loop."""

from src.agent.core import ExpenseAgent, MockLLMClient
from src.tools.registry import get_tool, get_tool_schemas


def test_tool_registry_schemas():
    """Verify tool registration decorator builds valid JSON schemas."""
    schemas = get_tool_schemas()
    tool_names = [s["name"] for s in schemas]
    assert "parse_expense" in tool_names
    assert "categorize" in tool_names

    parse_tool = get_tool("parse_expense")
    assert parse_tool is not None
    assert "text" in parse_tool.parameters["properties"]


def test_agent_single_expense_loop():
    """Verify agent processes 'spent 450 on groceries' end-to-end via loop."""
    mock_client = MockLLMClient()
    agent = ExpenseAgent(llm_client=mock_client)

    response = agent.process_message("spent 450 on groceries")
    assert "Logged ₹450" in response
    assert len(mock_client.call_history) >= 2


def test_agent_multiple_expenses_loop():
    """Verify agent processes multiple expenses in one turn."""
    mock_client = MockLLMClient()
    agent = ExpenseAgent(llm_client=mock_client)

    response = agent.process_message("auto 80, coffee 120 and lunch 250 yesterday")
    assert "Logged 3 expenses" in response
    assert "450" in response


def test_food_total_question_uses_deterministic_query(monkeypatch):
    from src.agent import core

    calls = []

    def fake_execute_tool(name, arguments):
        calls.append((name, arguments))
        return {
            "formatted_summary": (
                "₹5,000 across Groceries (₹3,000), Dining (₹2,000)."
            )
        }

    monkeypatch.setattr(core, "execute_tool", fake_execute_tool)
    agent = ExpenseAgent(llm_client=MockLLMClient())

    response = agent.process_message("What is the total amount spent on food?")

    assert response == "₹5,000 across Groceries (₹3,000), Dining (₹2,000)."
    assert calls == [
        ("query_expenses", {"category": "food", "time_period": "this_month"})
    ]
