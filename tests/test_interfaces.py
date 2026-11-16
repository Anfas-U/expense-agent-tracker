"""Unit tests for Phase 6: User Interfaces (CLI and Telegram)."""

from src.interfaces.cli import ExpenseCLI
from src.agent.core import ExpenseAgent, MockLLMClient


def test_cli_summary_command():
    """Verify CLI /summary command executes and prints formatted summary."""
    agent = ExpenseAgent(llm_client=MockLLMClient())
    cli = ExpenseCLI(agent=agent)

    output = cli.run_command("/summary")
    assert "This Month's Summary" in output
    assert "Total Spent:" in output


def test_cli_budget_command():
    """Verify CLI /budget command sets category budget."""
    agent = ExpenseAgent(llm_client=MockLLMClient())
    cli = ExpenseCLI(agent=agent)

    out = cli.run_command("/budget Transport 2500")
    assert "Budget for Transport set to ₹2,500/month." in out


def test_cli_text_routing():
    """Verify standard text queries route properly to agent."""
    agent = ExpenseAgent(llm_client=MockLLMClient())
    cli = ExpenseCLI(agent=agent)

    out = cli.run_command("spent 450 on groceries")
    assert "Logged ₹450" in out

