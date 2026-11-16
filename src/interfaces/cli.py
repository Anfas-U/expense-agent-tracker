"""Interactive Developer CLI interface for Expense Tracker Agent."""

import sys
from pathlib import Path
from typing import Optional

from src.agent.core import ExpenseAgent
from src.storage.local_db import get_local_db
from src.tools.receipt_scanner import extract_receipt
from src.tools.query_engine import query_expenses


class ExpenseCLI:
    """Command-line REPL interface."""

    def __init__(self, agent: Optional[ExpenseAgent] = None):
        self.agent = agent or ExpenseAgent()
        self.local_db = get_local_db()

    def print_banner(self):
        print("=" * 60)
        print("      Expense Tracker Agent (CLI Mode)")
        print("=" * 60)
        print("Commands:")
        print("  <text>                Log expense, query, or make corrections")
        print("                        e.g. 'spent 450 on groceries'")
        print("                        e.g. 'how much on food this month?'")
        print("                        e.g. 'change that to 540'")
        print("  /receipt <file_path>  Process a receipt photo")
        print("  /summary              Show this month's financial summary")
        print("  /budget <cat> <amt>   Set a category budget")
        print("  /exit or /quit        Exit")
        print("=" * 60)
        print()

    def run_command(self, user_input: str) -> str:
        """Process a single command or message and return response."""
        line = user_input.strip()
        if not line:
            return ""

        # Special commands
        if line.startswith("/receipt "):
            img_path = line[9:].strip().strip('"').strip("'")
            res = extract_receipt(img_path)
            if res.get("status") == "success":
                return res.get("confirmation_prompt", "Receipt extracted.")
            else:
                return f"Error: {res.get('message', 'Failed to extract receipt.')}"

        elif line == "/summary":
            q_res = query_expenses(time_period="this_month")
            summary_lines = [
                f"--- This Month's Summary ({q_res.get('start_date')} to {q_res.get('end_date')}) ---",
                f"Total Spent: ₹{q_res.get('total_amount'):,.2f} across {q_res.get('entry_count')} entries.",
            ]
            breakdown = q_res.get("category_breakdown", {})
            if breakdown:
                summary_lines.append("Breakdown by Category:")
                for cat, amt in breakdown.items():
                    summary_lines.append(f"  • {cat}: ₹{amt:,.2f}")
            for alert in q_res.get("budget_alerts", []):
                summary_lines.append(f"  ⚠️  {alert}")
            return "\n".join(summary_lines)

        elif line.startswith("/budget "):
            parts = line.split(maxsplit=2)
            if len(parts) == 3:
                cat = parts[1]
                try:
                    limit = float(parts[2])
                    self.local_db.set_budget(cat, limit)
                    return f"Budget for {cat} set to ₹{limit:,.0f}/month."
                except ValueError:
                    return "Usage: /budget <Category> <Amount>"
            return "Usage: /budget <Category> <Amount>"

        # Natural language routed to Agent Core
        return self.agent.process_message(line)

    def start_loop(self):
        """Start interactive REPL."""
        self.print_banner()
        while True:
            try:
                user_input = input("You > ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
                    print("Exiting Expense Tracker Agent. Goodbye!")
                    break

                response = self.run_command(user_input)
                print(f"Agent > {response}\n")
            except (KeyboardInterrupt, EOFError):
                print("\nGoodbye!")
                break
            except Exception as e:
                print(f"Agent Error > {e}\n")


def main():
    cli = ExpenseCLI()
    cli.start_loop()


if __name__ == "__main__":
    main()

