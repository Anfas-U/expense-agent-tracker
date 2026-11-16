"""Entry point for Expense Tracker Agent.

Supports interactive CLI, Telegram bot daemon, and summary generation.
"""

import argparse
import sys
from config.settings import settings
from src.interfaces.cli import ExpenseCLI
from src.interfaces.telegram_bot import ExpenseTelegramBot
from src.tools.query_engine import query_expenses


def parse_args():
    parser = argparse.ArgumentParser(
        description="Expense Tracker Agent - Conversational Spending Tracker"
    )
    parser.add_argument(
        "--mode",
        choices=["cli", "telegram", "summary"],
        default="cli",
        help="Interface mode: 'cli' (interactive shell), 'telegram' (bot daemon), or 'summary' (print report)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.mode == "telegram":
        print("Starting Expense Tracker Agent in Telegram Bot mode...")
        bot = ExpenseTelegramBot()
        bot.run()
    elif args.mode == "summary":
        res = query_expenses(time_period="this_month")
        print("\n" + "=" * 50)
        print("      CURRENT MONTH EXPENSE SUMMARY")
        print("=" * 50)
        print(f"Total Spent: ₹{res['total_amount']:,.2f} ({res['entry_count']} transactions)")
        print("\nCategory Breakdown:")
        for cat, amt in res.get("category_breakdown", {}).items():
            print(f"  • {cat:<15}: ₹{amt:,.2f}")
        if res.get("budget_alerts"):
            print("\nAlerts:")
            for a in res["budget_alerts"]:
                print(f"  ⚠️  {a}")
        print("=" * 50 + "\n")
    else:
        # Default CLI
        cli = ExpenseCLI()
        cli.start_loop()


if __name__ == "__main__":
    main()

