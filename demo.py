"""Interactive walkthrough & live demonstration of Expense Tracker Agent."""

import time
from src.interfaces.cli import ExpenseCLI


def run_demo():
    cli = ExpenseCLI()

    print("\n" + "=" * 65)
    print("      EXPENSE TRACKER AGENT - LIVE WALKTHROUGH DEMO")
    print("=" * 65 + "\n")

    test_steps = [
        ("Single Text Entry", "spent 450 on groceries"),
        ("Multi-Item & Relative Date", "auto 80, coffee 120 and lunch 250 yesterday"),
        ("Deterministic Query (PRD 8.2)", "how much on food this month?"),
        ("Contextual Correction", "change that to 540"),
        ("Budget Management", "/budget Dining 5000"),
        ("Monthly Aggregation", "/summary"),
    ]

    for label, user_msg in test_steps:
        print(f"[{label}]")
        print(f"  User  > {user_msg}")
        response = cli.run_command(user_msg)
        print(f"  Agent > {response}\n")
        time.sleep(0.3)

    print("=" * 65)
    print("Demo complete! To chat interactively, run: python main.py --mode cli")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    run_demo()

